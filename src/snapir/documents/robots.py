"""
robots.txt checks for the document scraper.

One robots.txt is fetched per origin (scheme + host) and cached for the run.
Fetch outcomes follow RFC 9309:

- 2xx: the file is parsed (Protego: wildcards, longest-match precedence).
- 4xx: robots.txt is "unavailable" — everything is allowed.
- 5xx or a network error: robots.txt is "unreachable" — nothing is allowed.
  The scraper records these URLs as errors so the next run retries them,
  instead of permanently skipping them on a transient failure.

Rules are matched for ROBOTS_USER_AGENT (default "SnapIR"), which falls back to
the "*" group when a site has no group for it.
"""

import asyncio
import os
from urllib.parse import urlsplit

import httpx
from protego import Protego

ROBOTS_USER_AGENT = os.environ.get("SNAPIR_ROBOTS_USER_AGENT", "SnapIR")
ROBOTS_TIMEOUT = 15  # seconds
# RFC 9309 asks crawlers to parse at least 500 KiB; more than that is ignored.
ROBOTS_MAX_BYTES = 500 * 1024


class RobotsUnreachableError(RuntimeError):
    """robots.txt could not be fetched (5xx or network error)."""


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class RobotsCache:
    """Per-run robots.txt cache, safe to share between concurrent workers."""

    def __init__(self, user_agent: str = ROBOTS_USER_AGENT):
        self.user_agent = user_agent
        self._parsers: dict[str, Protego | Exception] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=ROBOTS_TIMEOUT,
            headers={"User-Agent": user_agent},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch(self, origin: str) -> Protego:
        try:
            response = await self._client.get(f"{origin}/robots.txt")
        except httpx.HTTPError as e:
            raise RobotsUnreachableError(f"robots.txt unreachable for {origin}: {e}") from e
        if response.status_code >= 500:
            raise RobotsUnreachableError(
                f"robots.txt unreachable for {origin}: HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            return Protego.parse("")
        body = response.content[:ROBOTS_MAX_BYTES].decode("utf-8", errors="replace")
        return Protego.parse(body)

    async def _parser(self, url: str) -> Protego:
        origin = _origin(url)
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._parsers:
                try:
                    self._parsers[origin] = await self._fetch(origin)
                except RobotsUnreachableError as e:
                    # Cached too: the rest of the run fails fast for this origin.
                    self._parsers[origin] = e
        parser = self._parsers[origin]
        if isinstance(parser, Exception):
            raise parser
        return parser

    async def allowed(self, url: str) -> bool:
        """Whether url may be fetched. Raises RobotsUnreachableError."""
        return (await self._parser(url)).can_fetch(url, self.user_agent)

    async def crawl_delay(self, url: str) -> float | None:
        """The site's Crawl-delay in seconds, if it sets one."""
        try:
            return (await self._parser(url)).crawl_delay(self.user_agent)
        except RobotsUnreachableError:
            return None
