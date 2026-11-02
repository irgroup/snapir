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

robots.txt is checked before every navigation (see snapir.documents.robots):
disallowed URLs are recorded with scrape_status "robots" and not loaded. Redirect
targets are checked after the navigation (Playwright cannot intercept the hops);
a disallowed one discards the capture with the same status. A
site's Crawl-delay raises the per-domain wait when it exceeds RATE_LIMIT_DELAY.
ignore_robots=True (--ignore-robots) skips both.

stealth=True (--stealth) hides that the browser is automated: playwright-stealth
patches plus --disable-blink-features=AutomationControlled. Off by default, so
sites can tell the scraper is a bot; captures made with it get "stealth": true
in meta.json.

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

from snapir.documents.robots import RobotsCache, RobotsUnreachableError
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

    Sites may redirect background resources (e.g. a PWA manifest) to a login
    page. Those responses do not mean the visible page navigated away.
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


def _browser_args(stealth: bool = False) -> list[str]:
    args = ["--disable-remote-fonts"]  # prevents screenshot hanging on font-face resolution
    if stealth:
        args.append("--disable-blink-features=AutomationControlled")
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
    url: str,
    out_dir: Path,
    page,
    interactive: bool,
    manual_advance: dict[str, bool],
    robots: RobotsCache | None = None,
) -> dict:
    # Files from an earlier attempt on the same snapshot date (a retry or
    # --rescrape) must not outlive it: a failed attempt writes only meta.json,
    # and extract-text would otherwise read the stale page.mhtml beside it.
    shutil.rmtree(out_dir, ignore_errors=True)
    try:
        return await _scrape_one_inner(url, out_dir, page, interactive, manual_advance, robots)
    except Exception as e:
        print(f"error: {e}")
        return _write_meta(out_dir, _meta(url, "error", error=str(e)))


async def _robots_blocked_hop(robots: RobotsCache, hops: list[str]) -> str | None:
    """First redirect hop robots.txt disallows, if any.

    RobotsUnreachableError propagates, so _scrape_one records an error and
    the URL is retried on the next run.
    """
    for hop in dict.fromkeys(hops):
        if hop.startswith(("http://", "https://")) and not await robots.allowed(hop):
            return hop
    return None


async def _scrape_one_inner(
    url: str,
    out_dir: Path,
    page,
    interactive: bool,
    manual_advance: dict[str, bool],
    robots: RobotsCache | None = None,
) -> dict:
    error = None
    response = None
    login_redirect: list[str] = []
    # Every main-frame URL after the requested one: HTTP redirect targets and
    # JS navigations. The worker checked only `url` against robots.txt.
    nav_hops: list[str] = []

    def _on_response(resp):
        if location := _main_frame_login_redirect(resp, page):
            login_redirect.append(location)
        try:
            request = resp.request
            if request.is_navigation_request() and request.frame == page.main_frame:
                nav_hops.append(resp.url)
        except Exception:
            pass

    # Each worker reuses its page for every URL. Blank it first so a failed
    # goto() leaves an empty tab instead of the previous document, which
    # would otherwise be captured and saved under this URL.
    await page.goto("about:blank")

    page.on("response", _on_response)
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(1_500)
    except Exception as e:
        error = str(e)
    finally:
        page.remove_listener("response", _on_response)

    # Also catch JS-driven redirects that fire after domcontentloaded
    if not login_redirect and any(p in page.url for p in _LOGIN_REDIRECT_PATTERNS):
        login_redirect.append(page.url)

    if login_redirect:
        print(f"login redirect → {login_redirect[0]}")
        return _write_meta(out_dir, _meta(url, "login"))

    # Playwright cannot intercept individual redirect hops, so a disallowed
    # target has already been requested here; it is just never stored.
    if robots is not None:
        hops = [hop for hop in [*nav_hops, page.url] if hop != url]
        if blocked := await _robots_blocked_hop(robots, hops):
            print(f"redirect target disallowed by robots.txt: {blocked}")
            return _write_meta(out_dir, _meta(url, "robots", final_url=blocked))

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
    # File links without a .pdf extension (/download/…, /files/…) abort the
    # navigation with "Download is starting"; the fetch may still yield a PDF.
    is_download = error is not None and "Download is starting" in error
    pdf_viewer = await _pdf_viewer_showing(page)
    if "application/pdf" in content_type or is_pdf_url or is_download or pdf_viewer:
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

    # Navigation never reached the document (invalid URL, download, timeout
    # before commit, DNS failure): there is nothing of this URL to capture.
    if page.url == "about:blank" or page.url.startswith("chrome-error://"):
        raise RuntimeError(error or f"navigation did not load the page ({page.url})")

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
    # Where the captured page actually is, after redirects — lets downstream
    # checks spot a capture of a different site than the one requested.
    final_url = page.url if page.url != url else None
    return _write_meta(
        out_dir, _meta(url, scrape_status, title or None, error=error, final_url=final_url)
    )


async def _scrape_all_async(
    records: list[dict],
    documents_dir: Path,
    snapshot_date: str,
    headless: bool,
    interactive: bool,
    concurrency: int = 1,
    ignore_robots: bool = False,
    stealth: bool = False,
) -> list[dict]:
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    for lock in ("SingletonLock", "SingletonSocket", "lockfile"):
        (BROWSER_PROFILE_DIR / lock).unlink(missing_ok=True)
    results: list[dict] = []

    async def _handle_route(route):
        req = route.request
        if req.resource_type in ("font", "media"):
            await route.abort()
        elif req.resource_type == "document" and any(
            p in req.url for p in _LOGIN_REDIRECT_PATTERNS
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
                    args=_browser_args(stealth),
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
    # A robots.txt Crawl-delay longer than RATE_LIMIT_DELAY replaces it for
    # that domain.
    domain_lock = asyncio.Lock()
    domain_next_ok: dict[str, float] = {}
    robots = None if ignore_robots else RobotsCache()

    async def _wait_domain_turn(url: str) -> None:
        domain = _domain(url)
        delay = RATE_LIMIT_DELAY
        if robots is not None:
            delay = max(delay, await robots.crawl_delay(url) or 0)
        while True:
            async with domain_lock:
                now = time.monotonic()
                next_ok = domain_next_ok.get(domain, 0.0)
                if now >= next_ok:
                    domain_next_ok[domain] = now + delay
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

            if robots is not None:
                try:
                    allowed = await robots.allowed(url)
                    robots_error = None
                except RobotsUnreachableError as e:
                    allowed, robots_error = False, str(e)
                if not allowed:
                    # Same reset as _scrape_one: no stale capture may survive.
                    shutil.rmtree(out_dir, ignore_errors=True)
                    if robots_error:
                        print(f"{robots_error} — skipping, retried next run: {url}")
                        meta = _meta(url, "error", error=robots_error)
                    else:
                        print(f"disallowed by robots.txt: {url}")
                        meta = _meta(url, "robots")
                    meta = _write_meta(out_dir, meta)
                    meta["doc_id"] = did
                    results.append(meta)
                    continue

            await _wait_domain_turn(url)

            progress["started"] += 1
            print(f"[{progress['started']}/{total}] {url}")

            try:
                meta = await asyncio.wait_for(
                    _scrape_one(url, out_dir, page, interactive, manual_advance, robots),
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

            if stealth:
                meta = _write_meta(out_dir, {**meta, "stealth": True})
            meta["doc_id"] = did
            results.append(meta)

        try:
            await page.close()
        except Exception:
            pass

    playwright = Stealth().use_async(async_playwright()) if stealth else async_playwright()
    async with playwright as p:
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
            if robots is not None:
                await robots.aclose()

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
    ignore_robots: bool = False,
    stealth: bool = False,
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

    ignore_robots=True loads URLs that robots.txt disallows and ignores its
    Crawl-delay.

    stealth=True hides browser automation from the visited sites.
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
        _scrape_all_async(
            to_scrape,
            documents_dir,
            date_str,
            headless,
            interactive,
            concurrency,
            ignore_robots,
            stealth,
        )
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
    ignore_robots: bool = False,
    stealth: bool = False,
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
        ignore_robots=ignore_robots,
        stealth=stealth,
    )

    ok = sum(1 for r in results if r["scrape_status"] == "ok")
    disallowed = sum(1 for r in results if r["scrape_status"] == "robots")
    flagged = [r for r in results if r["scrape_status"] not in ("ok", "error", "robots")]
    errors = [r for r in results if r["scrape_status"] == "error"]
    print(f"\nDone. {ok}/{len(results)} pages ok.")
    if disallowed:
        print(f"{disallowed} disallowed by robots.txt (not loaded).")
    if flagged:
        print(f"{len(flagged)} flagged (re-run with --interactive to handle):")
        for r in flagged:
            print(f"  [{r['scrape_status']}] {r['url']}")
    if errors:
        print(f"{len(errors)} error(s) (will be retried on next run):")
        for r in errors:
            print(f"  {r['url']}: {r.get('error', '')[:80]}")

    return ok
