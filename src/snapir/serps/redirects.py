"""Resolve Google redirect links in SERPs to the publisher URL.

Since early November 2026 SerpApi returns many result links as opaque Google
redirects (``https://www.google.com/goto?url=CAES...``) instead of the target
URL. Google's robots.txt disallows ``/goto``, so the scraper skipped these
pages, and because the token differs per search the same page got a new
doc_id every day.

``resolve_serp_redirects`` replaces every such ``link`` with the URL the
redirect points to (one GET to Google per link, redirects not followed; only
the ``Location`` header is read) and keeps the original in
``google_redirect_link``. It runs when a SERP is stored, so every consumer of
the SERP (scraping, judging, export) derives the same doc_id from the
publisher URL, and robots.txt is checked against the publisher, not Google.
"""

from urllib.parse import parse_qs, urlparse

import httpx

ORIGINAL_KEY = "google_redirect_link"
MAX_HOPS = 3  # a redirect may point at another Google redirect
TIMEOUT = 10  # seconds per request


def is_google_redirect(url: str) -> bool:
    """True for Google redirect URLs such as /goto?url=... and /url?q=...."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    is_google = host == "google.com" or (
        host.startswith(("www.google.", "google.")) and "." in host.removeprefix("www.")
    )
    return is_google and parsed.path in ("/goto", "/url")


def _target_from_query(url: str) -> str | None:
    """Decode the old plain-text form /url?q=<target> without a request."""
    for key in ("q", "url"):
        value = parse_qs(urlparse(url).query).get(key, [None])[0]
        if value and value.startswith(("http://", "https://")):
            return value
    return None


def resolve_redirect(url: str, client: httpx.Client) -> str | None:
    """Return the URL a Google redirect points to, or None if it can't be resolved."""
    for _ in range(MAX_HOPS):
        target = _target_from_query(url)
        if target is None:
            try:
                response = client.get(url)
            except httpx.HTTPError as e:
                print(f"could not resolve redirect ({type(e).__name__}): {url[:80]}")
                return None
            target = response.headers.get("location") if response.is_redirect else None
            if not target:
                print(f"redirect not resolved (HTTP {response.status_code}): {url[:80]}")
                return None
            target = str(response.url.join(target))
        if urlparse(target).path.startswith("/sorry/"):
            print(f"Google rate-limited redirect resolution: {url[:80]}")
            return None
        if not is_google_redirect(target):
            return target
        url = target
    return None


def _iter_link_holders(value):
    """Yield every dict in a SERP payload that has a string "link"."""
    if isinstance(value, list):
        for item in value:
            yield from _iter_link_holders(item)
    elif isinstance(value, dict):
        if isinstance(value.get("link"), str):
            yield value
        for item in value.values():
            yield from _iter_link_holders(item)


def resolve_serp_redirects(serp: dict, client: httpx.Client | None = None) -> tuple[int, int]:
    """Replace Google redirect links in a SERP payload in place.

    Returns (resolved, unresolved). Unresolved links are left unchanged.
    """
    holders = [h for h in _iter_link_holders(serp) if is_google_redirect(h["link"])]
    if not holders:
        return 0, 0

    http = client or httpx.Client(follow_redirects=False, timeout=TIMEOUT)
    cache: dict[str, str | None] = {}
    resolved = unresolved = 0
    try:
        for holder in holders:
            url = holder["link"]
            if url not in cache:
                cache[url] = resolve_redirect(url, http)
            if cache[url]:
                holder.setdefault(ORIGINAL_KEY, url)
                holder["link"] = cache[url]
                resolved += 1
            else:
                unresolved += 1
    finally:
        if client is None:
            http.close()
    return resolved, unresolved
