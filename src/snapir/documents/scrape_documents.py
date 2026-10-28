"""
Browser-based document scraper for SERP documents.

Storage layout:
    {output}/
        index.jsonl                      ← url → doc_id + first_seen
        {snapshot_date}/
            {doc_id}/
                meta.json                ← url, fetched_at, scrape_status, title, …
                page.mhtml               ← self-contained MHTML archive (CDP snapshot)
                page.pdf                 ← raw bytes, instead of page.mhtml for PDFs
                screenshot.png           ← full-page screenshot for human review

One output directory holds one collection; use separate --output directories to
keep collections apart.

A persistent Chromium profile (BROWSER_PROFILE_DIR, default
data/.browser_profile) is shared across all runs. Once Cloudflare clearance or
cookie consent is obtained in a headed --interactive session, the full browser
state is reused in subsequent headless runs — no repeated challenges.
EXTENSION_DIR (default data/extensions/idcac) is loaded into the browser if it
exists.

doc_id is a 16-char SHA-1 hex digest of the URL (snapir.ids.doc_id_from_url).

No text is extracted here; `documents extract-text` fills meta.json text from
the saved captures afterwards.
"""

import asyncio
import base64
import json
import os
import random
import select
import shutil
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from snapir.serps.links import get_urls_from_serp_dir

BROWSER_PROFILE_DIR = Path(os.environ.get("BROWSER_PROFILE_DIR", "data/.browser_profile"))
EXTENSION_DIR = Path(os.environ.get("EXTENSION_DIR", "data/extensions/idcac"))
RATE_LIMIT_DELAY = 3  # seconds between page loads
DOCUMENT_TIMEOUT = 120  # seconds; guards against a wedged page stalling the whole batch
PROFILE_RELAUNCH_ATTEMPTS = 8
PROFILE_RELAUNCH_DELAY = 2  # seconds; Chromium needs a moment to release a persistent profile

_CLOUDFLARE_MARKERS = [
    "just a moment",
    "cf-browser-verification",
    "checking your browser",
    "enable javascript and cookies",
]
_TERMS_MARKERS = [
    "accept our terms",
    "agree to our terms",
    "accept the terms",
    "i accept the terms",
    "i have read and agree",
    "agree to the terms",
    "by clicking accept you agree",
    "must accept our terms",
    "terms and conditions apply",
    "accept terms and conditions",
    "agree to terms of service",
    "accept terms of service",
    "agree to terms of use",
    "accept terms of use",
    "by continuing you agree to our",
]
_PAYWALL_MARKERS = [
    "subscribe to read",
    "subscribe to continue",
    "sign in to read",
    "create a free account to",
    "this article is for subscribers",
    "to continue reading",
    "premium content",
]
_BOT_CHECK_MARKERS = [
    "px-captcha",
    "press & hold",
    "press and hold",
    "captcha-delivery.com",
    "are you a robot",
]
_LOGIN_REDIRECT_PATTERNS = [
    "facebook.com/login",
    "instagram.com/accounts/login",
    "linkedin.com/authwall",
    "twitter.com/i/flow/login",
    "x.com/i/flow/login",
]


def _persistent_profile_in_use(error: Exception) -> bool:
    """Whether Chromium has not yet released a persistent profile directory."""
    return "Opening in existing browser session" in str(error)


class PersistentProfileInUseError(RuntimeError):
    """Raised when Chromium keeps a persistent profile locked after retries."""


def _main_frame_login_redirect(response, page) -> str | None:
    """Return a login redirect only for the page's top-level navigation.

    Facebook redirects background resources such as its PWA manifest to
    /login. Those responses do not mean the visible post navigated away.
    """
    if response.status not in (301, 302, 303, 307, 308):
        return None
    location = response.headers.get("location", "")
    if not any(pattern in location for pattern in _LOGIN_REDIRECT_PATTERNS):
        return None
    try:
        request = response.request
        if not request.is_navigation_request() or request.frame != page.main_frame:
            return None
    except Exception:
        return None
    return location


async def _preserve_facebook_post(route) -> None:
    """Cancel a late Facebook login navigation without replacing the post."""
    request = route.request
    if (
        request.resource_type == "document"
        and "facebook.com/login" in request.url
        and request.is_navigation_request()
        and request.frame == request.frame.page.main_frame
    ):
        await route.fulfill(status=204)
    else:
        await route.continue_()


def _browser_args() -> list[str]:
    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-remote-fonts",  # prevents screenshot hanging on font-face resolution
    ]
    ext = EXTENSION_DIR.resolve()
    if ext.exists():
        args += [
            f"--disable-extensions-except={ext}",
            f"--load-extension={ext}",
        ]
    return args


def _domain(url: str) -> str:
    return url.split("//")[-1].split("/")[0]


def load_index(documents_dir: Path) -> dict[str, dict]:
    """Load the global index from documents_dir/index.jsonl.

    Merges duplicate URL entries (can arise from append-only writes) by
    taking the earliest first_seen.
    """
    index_file = documents_dir / "index.jsonl"
    index: dict[str, dict] = {}
    if not index_file.exists():
        return index
    with open(index_file) as f:
        for line in f:
            try:
                rec = json.loads(line)
                url = rec["url"]
                if url in index:
                    existing = index[url]
                    if rec.get("first_seen", "") < existing.get("first_seen", ""):
                        existing["first_seen"] = rec["first_seen"]
                else:
                    index[url] = rec
            except (json.JSONDecodeError, KeyError):
                pass
    return index


def _save_index(index: dict[str, dict], documents_dir: Path) -> None:
    """Rewrite the global index file from an in-memory dict."""
    with open(documents_dir / "index.jsonl", "w") as f:
        for rec in index.values():
            json.dump(rec, f, ensure_ascii=False)
            f.write("\n")


def get_docs_from_index(documents_dir: Path) -> list[dict]:
    """Return all index records."""
    return list(load_index(documents_dir).values())


def already_scraped(doc_id: str, snapshot_date: date | str, documents_dir: Path) -> bool:
    date_str = snapshot_date.isoformat() if isinstance(snapshot_date, date) else snapshot_date
    meta_file = documents_dir / date_str / doc_id / "meta.json"
    if not meta_file.exists():
        return False
    try:
        return json.loads(meta_file.read_text()).get("scrape_status") != "error"
    except Exception:
        return False


def _detect_issues(html: str, title: str) -> list[str]:
    probe = (html[:20_000] + title).lower()
    issues = []
    if any(m in probe for m in _CLOUDFLARE_MARKERS):
        issues.append("cloudflare")
    if any(m in probe for m in _BOT_CHECK_MARKERS):
        issues.append("bot_check")
    if any(m in probe for m in _TERMS_MARKERS):
        issues.append("terms")
    if any(m in probe for m in _PAYWALL_MARKERS):
        issues.append("paywall")
    return issues


def _as_pdf(raw: bytes | None) -> bytes | None:
    """Return raw trimmed to its %PDF- header, or None if it isn't a PDF.

    Some generators emit leading whitespace or a BOM before %PDF-.
    """
    if not raw:
        return None
    offset = raw.find(b"%PDF-")
    if offset < 0:
        return None
    return raw[offset:] if offset else raw


async def _fetch_pdf_bytes(url: str, page, response) -> tuple[bytes | None, str | None]:
    """Try every available source for the raw PDF bytes.

    A single source is not enough: bot-check interstitials let the browser
    through (persistent profile carries the clearance) while blocking the
    sidecar APIRequestContext, which does not present the same TLS/header
    fingerprint — that fetch then comes back as challenge HTML. The in-page
    fetch runs on the browser's own network stack, so it inherits whatever
    clearance got the navigation through.
    """
    last_error = None

    async def _api_context() -> bytes:
        resp = await page.context.request.get(url)
        return await resp.body()

    async def _navigation_response() -> bytes | None:
        return await response.body() if response else None

    async def _in_page_fetch() -> bytes | None:
        data_url = await asyncio.wait_for(
            page.evaluate(
                """async (u) => {
                    const r = await fetch(u, {credentials: 'include'});
                    if (!r.ok) return null;
                    const blob = await r.blob();
                    return await new Promise((resolve) => {
                        const fr = new FileReader();
                        fr.onload = () => resolve(fr.result);
                        fr.onerror = () => resolve(null);
                        fr.readAsDataURL(blob);
                    });
                }""",
                url,
            ),
            timeout=60.0,
        )
        if not data_url or "," not in data_url:
            return None
        return base64.b64decode(data_url.split(",", 1)[1])

    for source in (_api_context, _navigation_response, _in_page_fetch):
        try:
            pdf_bytes = _as_pdf(await source())
        except Exception as e:
            last_error = str(e)
            continue
        if pdf_bytes:
            return pdf_bytes, None

    return None, last_error


def _meta(url: str, status: str, title: str | None = None, **extra) -> dict:
    """meta.json record of one capture attempt; None-valued extras are dropped."""
    return {
        "url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "scrape_status": status,
        "title": title,
        **{k: v for k, v in extra.items() if v is not None},
    }


def _write_meta(out_dir: Path, meta: dict) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def _save_pdf(out_dir: Path, url: str, pdf_bytes: bytes, nav_error: str | None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "page.pdf").write_bytes(pdf_bytes)
    return _write_meta(out_dir, _meta(url, "ok", file_type="pdf", nav_error=nav_error))


# ---------------------------------------------------------------------------
# Login banner dismissal
# ---------------------------------------------------------------------------

# Maps a domain substring to login-mask indicator selectors ("mask") and
# close-button selectors ("close"). The first visible close match is clicked,
# but only when a mask indicator is visible: some Facebook posts render
# without any login mask, and the post dialog's own Close button matches the
# same selectors — clicking it closes the post and lands on the timeline.
# An absent/empty "mask" list means click unconditionally.
_LOGIN_BANNER_SELECTORS: dict[str, dict[str, list[str]]] = {
    "facebook.com": {
        # Strong, locale-independent login-wall evidence. Do not use login
        # links here: a logged-out Facebook post is itself a dialog and its
        # header contains a login link even when no popup is present.
        "mask": [
            "form[action*='login']",
            "input[name='email']",
            "input[name='pass']",
        ],
        # Some popup variants contain only links. These are considered only
        # when a second, smaller dialog is visible; a post's own header login
        # link must never be enough on its own.
        "weak_mask": [
            "a[href*='facebook.com/login']",
            "a[href*='/login']",
            "a[href*='/recover/']",
        ],
        "close": [
            "div[aria-label='Close'][role='button']",
            "div[aria-label='Schließen'][role='button']",  # German
            "[data-testid='close-button']",
        ],
    },
    "instagram.com": {
        "close": [
            "div.xdg88n9 svg",  # X close button; class may change with Instagram deploys
            "div[role='dialog'] svg",
        ],
    },
}


def _page_location(url: str) -> str:
    """URL without fragment or trailing slash, for before/after comparison."""
    return url.split("#", 1)[0].rstrip("/")


async def _login_dialog(
    page,
    selectors: list[str],
    weak_selectors: list[str] | None = None,
):
    """Return the nearest visible dialog around a strong login marker.

    The nearest-dialog scoping is essential on Facebook: the post and the
    login popup can both have role=dialog, and closing the post navigates away
    from the content we intended to capture.
    """
    for selector in selectors:
        try:
            markers = page.locator(selector)
            for index in range(min(await markers.count(), 10)):
                marker = markers.nth(index)
                if not await marker.is_visible(timeout=1_000):
                    continue
                dialog = marker.locator("xpath=ancestor::div[@role='dialog'][1]")
                if await dialog.is_visible(timeout=1_000):
                    return dialog
        except Exception:
            continue

    # Link-only evidence is ambiguous because the post dialog itself contains
    # a login link. Only use it when Facebook is visibly presenting more than
    # one dialog, then choose the smallest matching dialog (the overlay).
    if weak_selectors:
        try:
            dialogs = page.locator("div[role='dialog']")
            visible_dialogs = []
            for index in range(min(await dialogs.count(), 10)):
                dialog = dialogs.nth(index)
                if await dialog.is_visible(timeout=500):
                    visible_dialogs.append(dialog)
            if len(visible_dialogs) < 2:
                return None

            candidates = []
            for dialog in visible_dialogs:
                has_marker = False
                for selector in weak_selectors:
                    try:
                        if await dialog.locator(selector).first.is_visible(timeout=500):
                            has_marker = True
                            break
                    except Exception:
                        continue
                if has_marker:
                    box = await dialog.bounding_box()
                    area = box["width"] * box["height"] if box else float("inf")
                    candidates.append((area, dialog))
            if candidates:
                return min(candidates, key=lambda candidate: candidate[0])[1]
        except Exception:
            pass
    return None


async def _hide_facebook_signup_banner(page) -> bool:
    """Hide Facebook's fixed login/signup banner when it has no close button.

    This variant is not a role=dialog. Its stable structural signature is a
    data-nosnippet wrapper containing both a /login link and a /reg link.
    Scoping to that wrapper avoids touching the post or the header login form.
    """
    try:
        wrappers = page.locator("div[data-nosnippet]")
        for index in range(min(await wrappers.count(), 20)):
            wrapper = wrappers.nth(index)
            if not await wrapper.is_visible(timeout=500):
                continue
            login = wrapper.locator("a[href*='facebook.com/login']").first
            signup = wrapper.locator("a[href*='facebook.com/reg'], a[href*='/reg']").first
            if not await login.is_visible(timeout=500):
                continue
            if not await signup.is_visible(timeout=500):
                continue
            await wrapper.evaluate(
                "element => element.style.setProperty('display', 'none', 'important')"
            )
            print("hid Facebook login/signup banner")
            return True
    except Exception:
        pass
    return False


async def _dismiss_login_banner(page, url: str) -> bool:
    """Dismiss login overlays if present. Returns True if an action was taken.

    Two guards keep this from closing the content itself: the close button is
    only clicked when a login-mask indicator is visible on the page, and if
    the click still causes a navigation (wrong dialog closed), we go back and
    leave the page as-is — a capture with the login mask still beats
    capturing the landing page.
    """
    domain = _domain(url)
    action_taken = False
    for pattern, cfg in _LOGIN_BANNER_SELECTORS.items():
        if pattern not in domain:
            continue
        # Instagram's modal can take a moment to appear after domcontentloaded
        if pattern == "instagram.com":
            await page.wait_for_timeout(1_500)
        if pattern == "facebook.com":
            # Facebook can show this fixed banner and a central login dialog
            # simultaneously. Hiding the banner must not skip dialog handling.
            action_taken = await _hide_facebook_signup_banner(page)
        scope = page
        mask_selectors = cfg.get("mask", [])
        if mask_selectors:
            scope = None
            # Facebook can render its overlay after domcontentloaded. A short
            # bounded poll handles that without delaying other domains.
            attempts = 5 if pattern == "facebook.com" else 1
            for attempt in range(attempts):
                scope = await _login_dialog(page, mask_selectors, cfg.get("weak_mask"))
                if scope is not None:
                    break
                if attempt + 1 < attempts:
                    await page.wait_for_timeout(300)
            if scope is None:
                print("no login mask detected — leaving page as-is")
                return action_taken
        for selector in cfg["close"]:
            try:
                # Close only inside the dialog proven to be the login popup.
                # A page-global selector can match the post's own Close button.
                el = scope.locator(selector).first
                if await el.is_visible(timeout=2_000):
                    url_before = page.url
                    await el.click()
                    await page.wait_for_timeout(500)
                    if _page_location(page.url) == _page_location(url_before):
                        return True
                    print(f"dismiss click navigated to {page.url} — going back")
                    try:
                        await page.go_back(wait_until="domcontentloaded", timeout=15_000)
                        await page.wait_for_timeout(1_000)
                    except Exception as e:
                        print(f"go_back failed: {e}")
                    return False
            except Exception:
                continue
    return action_taken


async def _expand_youtube_description(page, url: str) -> None:
    """Click 'Show more' on YouTube watch pages to expand the full description."""
    if "youtube.com/watch" not in url:
        return
    try:
        # Scroll down to trigger lazy rendering of the description component
        await page.evaluate("window.scrollBy(0, 500)")
        await page.wait_for_timeout(800)
        for selector in [
            "ytd-text-inline-expander #expand",
            "#description-inline-expander #expand",
            "tp-yt-paper-button#expand",
        ]:
            try:
                el = page.locator(selector).first
                if await el.is_visible():
                    # A DOM click, not a mouse click: YouTube's cookie-consent
                    # lightbox overlays the page and intercepts pointer events,
                    # so Playwright's actionability-checked click() would retry
                    # until its 30s default timeout for every selector.
                    await el.evaluate("el => el.click()", timeout=2_000)
                    await page.wait_for_timeout(500)
                    return
            except Exception:
                continue
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core async scraper
# ---------------------------------------------------------------------------


async def _capture(page) -> tuple[str, str]:
    # The 500k slice bounds the CDP payload so huge DOMs (YouTube, large
    # Wikipedia pages) can't hang the transfer. Text is never extracted from
    # this capture (`documents extract-text` reads the saved page.mhtml), so
    # the truncation only has to preserve enough for the title and
    # _detect_issues (first 20k chars).
    try:
        title = await asyncio.wait_for(page.title(), timeout=10.0)
        html = await asyncio.wait_for(
            page.evaluate("() => document.documentElement.outerHTML.slice(0, 500_000)"),
            timeout=10.0,
        )
        return title, html
    except Exception:
        return "", ""


async def _pdf_viewer_showing(page) -> bool:
    """True when Chromium's built-in PDF viewer is rendering the settled page.

    Checked instead of only the navigation response because sites behind a
    bot-check interstitial answer the navigation with an HTML challenge and
    serve the PDF only after it is solved — goto() then reports text/html
    while the page ends up in the viewer. The viewer's <embed> lives in a
    closed shadow root and is not reachable from here; document.contentType
    and the extension stylesheet in <head> are.
    """
    try:
        return await asyncio.wait_for(
            page.evaluate(
                """() => document.contentType === 'application/pdf'
                       || !!document.querySelector('link[href*="pdf_embedder.css"]')"""
            ),
            timeout=5.0,
        )
    except Exception:
        return False


async def _scrape_one(
    url: str, out_dir: Path, page, interactive: bool, manual_advance: dict[str, bool]
) -> dict:
    try:
        return await _scrape_one_inner(url, out_dir, page, interactive, manual_advance)
    except Exception as e:
        print(f"error: {e}")
        return _write_meta(out_dir, _meta(url, "error", error=str(e)))
    finally:
        # The Facebook navigation guard is installed only after the original
        # page load. Remove it before this worker page is reused for another URL.
        try:
            await page.unroute("**/*", _preserve_facebook_post)
        except Exception:
            pass


async def _scrape_one_inner(
    url: str, out_dir: Path, page, interactive: bool, manual_advance: dict[str, bool]
) -> dict:
    error = None
    response = None
    login_redirect: list[str] = []
    loaded_url = url
    loaded_is_facebook = False

    def _on_response(resp):
        if location := _main_frame_login_redirect(resp, page):
            login_redirect.append(location)

    page.on("response", _on_response)
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(1_500)
        # SERP links can be Google redirect wrappers. Domain-specific handling
        # must use the URL Chromium actually loaded, not the original wrapper.
        loaded_url = page.url
        loaded_is_facebook = "facebook.com" in _domain(loaded_url)
        if loaded_is_facebook and not any(p in page.url for p in _LOGIN_REDIRECT_PATTERNS):
            # Do not intercept during the initial load: some posts need those
            # requests to render. Protect only the already-loaded post from a
            # later JavaScript navigation to /login.
            await page.route("**/*", _preserve_facebook_post)
        await _dismiss_login_banner(page, loaded_url)
        await _expand_youtube_description(page, loaded_url)
    except Exception as e:
        error = str(e)
    finally:
        page.remove_listener("response", _on_response)

    # Also catch JS-driven redirects that fire after domcontentloaded
    if not login_redirect and any(p in page.url for p in _LOGIN_REDIRECT_PATTERNS):
        login_redirect.append(page.url)

    # The route handler answers Facebook's attempted /login navigation with
    # HTTP 204, which keeps the already-rendered post in the main frame. The
    # preceding 30x response is still observable, but it is not a failed
    # capture when the browser ultimately remains on the requested post.
    if (
        login_redirect
        and loaded_is_facebook
        and not any(p in page.url for p in _LOGIN_REDIRECT_PATTERNS)
    ):
        print("prevented Facebook login navigation — capturing post")
        login_redirect.clear()

    if login_redirect:
        print(f"login redirect → {login_redirect[0]}")
        return _write_meta(out_dir, _meta(url, "login"))

    if interactive and manual_advance["enabled"]:
        print(
            "Website loaded — inspect or adjust it in the browser, then press Enter "
            "to capture it and continue. Type r + Enter to resume continuous processing."
        )
        command = (await asyncio.to_thread(input, "  > ")).strip().lower()
        if command in {"r", "run"}:
            manual_advance["enabled"] = False
            print("Continuous processing resumed.")

    # PDF: save the raw bytes; skip HTML/screenshot flow entirely.
    # Detect by content-type OR .pdf URL extension OR the settled page being the
    # PDF viewer (bot-check interstitials make the navigation response HTML);
    # guard with magic bytes because online viewers sometimes claim
    # application/pdf but serve HTML.
    content_type = response.headers.get("content-type", "") if response else ""
    is_pdf_url = url.lower().split("?")[0].endswith(".pdf")
    pdf_viewer = await _pdf_viewer_showing(page)
    if "application/pdf" in content_type or is_pdf_url or pdf_viewer:
        pdf_bytes, fetch_error = await _fetch_pdf_bytes(url, page, response)
        if fetch_error:
            error = fetch_error

        if pdf_bytes:
            # The bytes came from a fetch independent of the navigation, so a
            # goto() failure says nothing about their completeness. Chromium
            # aborts the navigation with "Download is starting" whenever it
            # decides to download a PDF instead of rendering it — treating that
            # as an error dropped fully captured PDFs from the corpus and made
            # every run re-scrape them. Keep the detail, drop the error status.
            return _save_pdf(out_dir, url, pdf_bytes, error)

        if "application/pdf" in content_type or pdf_viewer:
            # Response (or the settled page) is a PDF but no source returned PDF
            # bytes — don't fall through to screenshot which would hang on the
            # PDF viewer and capture no text.
            blocked = response is not None and response.headers.get("cf-mitigated") == "challenge"
            reason = error or (
                "blocked by Cloudflare challenge" if blocked else "no %PDF- magic bytes"
            )
            raise RuntimeError(f"PDF response body unreadable: {reason}")
        # is_pdf_url but body is HTML (online viewer) — fall through to HTML flow.

    title, html = await _capture(page)
    issues = _detect_issues(html, title)

    # Cloudflare sets cf-mitigated: challenge in the response headers — more
    # reliable than HTML markers when the JS challenge hasn't rendered yet.
    if response and response.headers.get("cf-mitigated") == "challenge":
        if "cloudflare" not in issues:
            issues.append("cloudflare")

    if interactive:
        domain = _domain(url)
        skipped = False
        while issues:
            print(f"! {', '.join(i.upper() for i in issues)} on {domain}")
            print("Enter to retry  |  s + Enter to skip  |  c + Enter to force continue")
            cmd = (await asyncio.to_thread(input, "  > ")).strip().lower()
            if cmd == "s":
                skipped = True
                break
            if cmd == "c":
                break
            title, html = await _capture(page)
            issues = _detect_issues(html, title)
        if skipped:
            return _meta(url, "skipped")

    out_dir.mkdir(parents=True, exist_ok=True)

    print("taking screenshot...")
    try:
        await asyncio.wait_for(
            page.screenshot(path=out_dir / "screenshot.png", full_page=True),
            timeout=30.0,
        )
    except Exception as e:
        print(f"full-page screenshot failed ({e}), falling back to viewport")
        try:
            await asyncio.wait_for(
                page.screenshot(
                    path=out_dir / "screenshot.png",
                    clip={"x": 0, "y": 0, "width": 1280, "height": 1500},
                ),
                timeout=10.0,
            )
        except Exception as e2:
            print(f"screenshot skipped: {e2}")
    print("screenshot done")

    # Re-capture after screenshot so title and issue detection match what the
    # screenshot shows — JS may have dismissed cookie banners in the interim.
    # Fall back to pre-screenshot HTML if the page is in a broken state.
    print("re-capturing content...")
    new_title, new_html = await _capture(page)
    if new_html:
        title, html = new_title, new_html
    print(f"content captured ({len(html)} chars)")

    # Some repository/government sites serve a PDF from a URL with no .pdf
    # extension and a content-type that doesn't say application/pdf either;
    # Chromium's built-in viewer then only attaches its shadow-root <embed>
    # (and the pdf_embedder.css link the earlier check looks for) after the
    # screenshot delay above has already passed. Re-check now that more time
    # has settled, before committing to an HTML/mhtml save that would only
    # capture the empty viewer shell.
    if not pdf_viewer and await _pdf_viewer_showing(page):
        print("PDF viewer detected on re-check — switching to PDF flow")
        pdf_bytes, fetch_error = await _fetch_pdf_bytes(url, page, response)
        if pdf_bytes:
            return _save_pdf(out_dir, url, pdf_bytes, fetch_error)
        print(f"late PDF viewer detected but bytes unavailable: {fetch_error}")

    print("capturing mhtml...")
    try:
        cdp = await page.context.new_cdp_session(page)
        snapshot = await asyncio.wait_for(
            cdp.send("Page.captureSnapshot", {"format": "mhtml"}),
            timeout=30.0,
        )
        await cdp.detach()
        print(f"writing mhtml ({len(snapshot['data'])} chars)...")
        with open(out_dir / "page.mhtml", "w", encoding="utf-8") as f:
            f.write(snapshot["data"])
    except Exception as e:
        print(f"mhtml capture failed, falling back to html: {e}")
        # `html` here is truncated to 500k chars for the title/issue probe —
        # too short to reach <body> on markup-heavy pages (that truncated
        # capture was never meant to double as the extraction source). Fetch
        # the full, untruncated document for this on-disk fallback instead.
        try:
            full_html = await asyncio.wait_for(page.content(), timeout=30.0)
        except Exception as e2:
            print(f"full content() also failed, using truncated capture: {e2}")
            full_html = html
        print(f"writing html ({len(full_html)} chars)...")
        with open(out_dir / "page.html", "w", encoding="utf-8") as f:
            f.write(full_html)
    print("save done")

    scrape_status = "error" if error else (",".join(issues) if issues else "ok")
    return _write_meta(out_dir, _meta(url, scrape_status, title or None, error=error))


async def _scrape_all_async(
    records: list[dict],
    documents_dir: Path,
    snapshot_date: str,
    headless: bool,
    interactive: bool,
    concurrency: int = 1,
) -> list[dict]:
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    for lock in ("SingletonLock", "SingletonSocket", "lockfile"):
        (BROWSER_PROFILE_DIR / lock).unlink(missing_ok=True)
    results: list[dict] = []

    async def _handle_route(route):
        req = route.request
        if req.resource_type in ("font", "media"):
            await route.abort()
        elif (
            req.resource_type == "document"
            and "facebook.com/login" not in req.url
            and any(p in req.url for p in _LOGIN_REDIRECT_PATTERNS)
        ):
            await route.abort()
        else:
            await route.continue_()

    async def _launch_context(p, profile_dir: Path = BROWSER_PROFILE_DIR):
        # Persistent context reuses the full browser state across runs —
        # Cloudflare clearance, consent cookies, fingerprint history. Shared
        # by every worker below, so concurrent pages inherit the same
        # cookie jar instead of needing their own profile.
        # After a wedged CDP operation, context.close() can return before its
        # Chromium process has released SingletonLock. Retrying is safe for
        # that short hand-off; immediately launching a second persistent
        # context is not, and previously brought down the entire flow.
        for attempt in range(1, PROFILE_RELAUNCH_ATTEMPTS + 1):
            try:
                context = await p.chromium.launch_persistent_context(
                    str(profile_dir),
                    headless=headless,
                    args=_browser_args(),
                )
                await context.route("**/*", _handle_route)
                return context
            except PlaywrightError as error:
                if not _persistent_profile_in_use(error):
                    raise
                if attempt == PROFILE_RELAUNCH_ATTEMPTS:
                    raise PersistentProfileInUseError(
                        "Chromium did not release the persistent browser profile after "
                        f"{PROFILE_RELAUNCH_ATTEMPTS * PROFILE_RELAUNCH_DELAY}s. "
                        "Another scraper or an orphaned Chromium process may still own it."
                    ) from error
                print(
                    "persistent browser profile is still in use; retrying context launch "
                    f"({attempt}/{PROFILE_RELAUNCH_ATTEMPTS})"
                )
                await asyncio.sleep(PROFILE_RELAUNCH_DELAY)

    # RATE_LIMIT_DELAY is a minimum gap between two requests to the *same*
    # domain, not a global one — that's what makes cross-domain concurrency
    # safe: no single site is ever hit harder than the old sequential loop
    # hit it, but unrelated domains no longer wait on each other.
    domain_lock = asyncio.Lock()
    domain_next_ok: dict[str, float] = {}

    async def _wait_domain_turn(url: str) -> None:
        domain = _domain(url)
        while True:
            async with domain_lock:
                now = time.monotonic()
                next_ok = domain_next_ok.get(domain, 0.0)
                if now >= next_ok:
                    domain_next_ok[domain] = now + RATE_LIMIT_DELAY
                    return
                wait = next_ok - now
            await asyncio.sleep(wait)

    # Shared context reference, swapped under a lock when a worker hits a
    # wedged page it can't close. Other workers notice the swap on their
    # next item and grab a fresh page instead of operating on a closed
    # context (see _worker below).
    ctx_state: dict = {}
    ctx_lock = asyncio.Lock()
    recovery_profiles: list[Path] = []

    async def _recycle_context(p, stale_context):
        async with ctx_lock:
            if ctx_state["context"] is stale_context:
                try:
                    await asyncio.wait_for(stale_context.close(), timeout=10.0)
                except Exception:
                    print(
                        "context close did not finish; waiting for Chromium to release its profile"
                    )
                try:
                    ctx_state["context"] = await _launch_context(p)
                except PersistentProfileInUseError:
                    # Do not remove a live profile lock: it may belong to the
                    # wedged browser, whose state is still useful to preserve
                    # for the next scheduled run. A fresh profile keeps this
                    # snapshot moving instead of failing its verification gate.
                    recovery_profile = Path(tempfile.mkdtemp(prefix="temporal-topics-browser-"))
                    recovery_profiles.append(recovery_profile)
                    print(
                        "persistent profile remains locked; continuing this run with "
                        "a temporary browser profile (saved cookies will be unavailable)"
                    )
                    ctx_state["context"] = await _launch_context(p, recovery_profile)
            return ctx_state["context"]

    # A single background watcher reads control commands so concurrent workers
    # do not all try to read stdin at once.
    pause_event = asyncio.Event()
    pause_event.set()
    manual_advance = {"enabled": False}

    async def _pause_watcher():
        try:
            while True:
                if select.select([sys.stdin], [], [], 0)[0]:
                    line = sys.stdin.readline().strip().lower()
                    if interactive and line == "p":
                        manual_advance["enabled"] = True
                        print(
                            "Manual document pauses enabled. Press r + Enter at a "
                            "document pause to resume continuous processing."
                        )
                    elif interactive and line in {"r", "run"}:
                        manual_advance["enabled"] = False
                        print("Continuous processing resumed.")
                    elif line == "p":
                        pause_event.clear()
                        print("Paused — fix anything in the browser, then press Enter to continue.")
                        await asyncio.to_thread(input, "  > ")
                        pause_event.set()
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    queue: asyncio.Queue = asyncio.Queue()
    for rec in records:
        queue.put_nowait(rec)
    total = len(records)
    progress = {"started": 0}

    async def _worker(p):
        context = ctx_state["context"]
        page = await context.new_page()
        while True:
            try:
                rec = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            if context is not ctx_state["context"]:
                context = ctx_state["context"]
                page = await context.new_page()

            url = rec["url"]
            did = rec["doc_id"]
            out_dir = documents_dir / snapshot_date / did

            await pause_event.wait()
            await _wait_domain_turn(url)

            progress["started"] += 1
            print(f"[{progress['started']}/{total}] {url}")

            try:
                meta = await asyncio.wait_for(
                    _scrape_one(url, out_dir, page, interactive, manual_advance),
                    timeout=DOCUMENT_TIMEOUT,
                )
            except asyncio.TimeoutError:
                print(f"timed out after {DOCUMENT_TIMEOUT}s, recycling page: {url}")
                meta = _write_meta(
                    out_dir, _meta(url, "error", error=f"timed out after {DOCUMENT_TIMEOUT}s")
                )

                # A timeout here means some earlier CDP call (screenshot/mhtml)
                # is still wedged on this page's renderer — asyncio cancellation
                # doesn't stop the underlying browser call, so a graceful
                # page.close() can hang too. Fall back to relaunching the whole
                # persistent context if the page itself won't close.
                try:
                    await asyncio.wait_for(page.close(), timeout=10.0)
                    page = await context.new_page()
                except Exception:
                    print("page close failed, relaunching browser context")
                    context = await _recycle_context(p, context)
                    page = await context.new_page()

            meta["doc_id"] = did
            results.append(meta)

        try:
            await page.close()
        except Exception:
            pass

    async with Stealth().use_async(async_playwright()) as p:
        ctx_state["context"] = await _launch_context(p)
        watcher = asyncio.create_task(_pause_watcher())
        try:
            await asyncio.gather(*(_worker(p) for _ in range(max(1, concurrency))))
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            try:
                await ctx_state["context"].close()
            except Exception:
                print("browser context did not close cleanly")
            for recovery_profile in recovery_profiles:
                shutil.rmtree(recovery_profile, ignore_errors=True)

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scrape(
    url_records: list[dict],
    documents_dir: Path,
    snapshot_date: date,
    headless: bool = True,
    interactive: bool = False,
    concurrency: int = 1,
) -> list[dict]:
    """
    Scrape documents for all url_records not already fetched for this
    snapshot_date.

    In SERP mode, pass url_records from get_urls_from_serp_dir — new URLs are
    added to the global index.  In rescrape mode, pass records from
    get_docs_from_index — the index is not modified.

    interactive=True implies headless=False and pauses on detected issues. In
    an interactive session, p + Enter enables a pause after every navigation;
    r + Enter resumes continuous processing. It also forces concurrency=1
    because it owns the terminal.

    concurrency>1 runs multiple pages in parallel against the same shared
    browser context/profile, pacing requests per-domain rather than
    globally (see _scrape_all_async).
    """
    if interactive:
        headless = False
        if concurrency > 1:
            print(
                f"--interactive forces concurrency=1 (requested {concurrency}); "
                "the pause-on-issue prompt assumes one page owns the terminal at a time."
            )
        concurrency = 1
    concurrency = max(1, concurrency)

    date_str = snapshot_date.isoformat()
    documents_dir.mkdir(parents=True, exist_ok=True)

    # Add URLs not yet recorded to the index.
    index = load_index(documents_dir)
    index_dirty = False
    for rec in url_records:
        url = rec["url"]
        if url not in index:
            index[url] = {
                "doc_id": rec["doc_id"],
                "url": url,
                "first_seen": date_str,
            }
            index_dirty = True
    if index_dirty:
        _save_index(index, documents_dir)

    to_scrape = [
        r for r in url_records if not already_scraped(r["doc_id"], date_str, documents_dir)
    ]
    if not to_scrape:
        return []

    random.shuffle(to_scrape)
    results = asyncio.run(
        _scrape_all_async(to_scrape, documents_dir, date_str, headless, interactive, concurrency)
    )

    return results


def scrape_documents(
    serps: Path | None,
    rescrape: bool,
    query_id: str | None,
    headed: bool,
    interactive: bool,
    output: Path,
    concurrency: int,
    run_date: date | None = None,
) -> int:
    """Capture documents from SERPs (or the index, with rescrape).

    Captures go to output/<run_date>/ (default today). Returns the number of
    documents captured successfully in this run. Raises ValueError on invalid
    argument combinations.
    """
    run_date = run_date or date.today()

    if rescrape:
        if serps is not None or query_id is not None:
            raise ValueError("--rescrape cannot be combined with --serps or --query-id.")
        url_records = get_docs_from_index(output)
        if not url_records:
            print(f"No documents found in index at {output}.")
            return 0
        mode_label = "rescrape"
    else:
        if serps is None:
            raise ValueError("Provide --serps, or --rescrape to re-capture indexed documents.")
        url_records = get_urls_from_serp_dir(serps, query_id=query_id)
        if not url_records:
            print("No URLs found in SERP files.")
            return 0
        mode_label = f"serp ({serps.name})"

    already_done = sum(1 for r in url_records if already_scraped(r["doc_id"], run_date, output))
    to_fetch = len(url_records) - already_done
    print(
        f"Mode: {mode_label} | Unique URLs: {len(url_records)} | "
        f"Already scraped: {already_done} | To fetch: {to_fetch}"
    )

    if to_fetch == 0:
        print("Nothing to do.")
        return 0

    results = scrape(
        url_records,
        documents_dir=output,
        snapshot_date=run_date,
        headless=not headed,
        interactive=interactive,
        concurrency=concurrency,
    )

    ok = sum(1 for r in results if r["scrape_status"] == "ok")
    flagged = [r for r in results if r["scrape_status"] not in ("ok", "error")]
    errors = [r for r in results if r["scrape_status"] == "error"]
    print(f"\nDone. {ok}/{len(results)} pages ok.")
    if flagged:
        print(f"{len(flagged)} flagged (re-run with --interactive to handle):")
        for r in flagged:
            print(f"  [{r['scrape_status']}] {r['url']}")
    if errors:
        print(f"{len(errors)} error(s) (will be retried on next run):")
        for r in errors:
            print(f"  {r['url']}: {r.get('error', '')[:80]}")

    return ok
