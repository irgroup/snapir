import json
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

import serpapi

from snapir.jsonl import read_jsonl
from snapir.serps.links import decode_serp_url_fields
from snapir.serps.redirects import resolve_serp_redirects

RATE_LIMIT_DELAY = 2  # seconds between successful requests
MAX_RETRIES = 3  # retries on 429 before giving up
BACKOFF_BASE = 60  # seconds; doubled each retry (60 → 120 → 240)

AI_OVERVIEW_MAX_RETRIES = 2  # page_token expires within 1-4 min; keep retries short
AI_OVERVIEW_RETRY_DELAY = 3  # seconds


class Searcher:
    def __init__(
        self,
        engine: str = "google",
        location: str = "London,England,United Kingdom",
        google_domain: str = "google.com",
        hl: str = "en",
        gl: str = "uk",
    ):
        self.engine = engine
        self.location = location
        self.google_domain = google_domain
        self.hl = hl
        self.gl = gl

        api_key = os.environ.get("SERPAPI_API_KEY")
        if not api_key:
            raise ValueError("SERPAPI_API_KEY must be set in environment variables.")
        self._api_key = api_key
        self.client = serpapi.Client(api_key=api_key, timeout=10)

    def _redact(self, error: Exception) -> str:
        """Error text with the API key removed (SerpAPI errors include the request URL)."""
        return str(error).replace(self._api_key, "<SERPAPI_API_KEY>")

    def search(self, query: str, num: int = 10) -> dict | None:
        """
        Run one search and return the full SerpAPI result dict, or None on failure.
        Retries up to MAX_RETRIES times on 429 or timeout with exponential backoff.
        """
        params = {
            "engine": self.engine,
            "q": query,
            "num": num,
            "location": self.location,
            "google_domain": self.google_domain,
            "hl": self.hl,
            "gl": self.gl,
        }
        delay = BACKOFF_BASE
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return self.client.search(params).as_dict()
            except serpapi.HTTPError as e:
                if e.status_code == 429:
                    print(f"429 rate-limited (attempt {attempt}/{MAX_RETRIES}), waiting {delay}s")
                    time.sleep(delay)
                    delay *= 2
                elif e.status_code == 401:
                    print("401: SerpAPI rejected the API key (SERPAPI_API_KEY).")
                    return None
                else:
                    print(f"HTTP {e.status_code} for query '{query}': {self._redact(e)}")
                    return None
            except serpapi.TimeoutError as e:
                print(
                    f"Timeout for query '{query}' (attempt {attempt}/{MAX_RETRIES}): {self._redact(e)}"
                )
                if attempt == MAX_RETRIES:
                    return None
                time.sleep(delay)
                delay *= 2
        print(f"Giving up on '{query}' after {MAX_RETRIES} retries.")
        return None

    def resolve_ai_overview(self, page_token: str) -> dict | None:
        """
        Resolve a pending ai_overview page_token into text_blocks/references.

        The token expires within 1-4 minutes of the original search, so this
        must be called immediately after search() returns a pointer-only
        ai_overview — never on tokens loaded from previously saved files.
        Retries are short (no long backoff) since a slow retry just burns
        the token's remaining lifetime.
        """
        params = {"engine": "google_ai_overview", "page_token": page_token}
        for attempt in range(1, AI_OVERVIEW_MAX_RETRIES + 1):
            try:
                result = self.client.search(params).as_dict()
                return result.get("ai_overview")
            except serpapi.HTTPError as e:
                print(
                    f"HTTP {e.status_code} resolving ai_overview (attempt {attempt}/{AI_OVERVIEW_MAX_RETRIES}): {self._redact(e)}"
                )
                if e.status_code != 429 or attempt == AI_OVERVIEW_MAX_RETRIES:
                    return None
                time.sleep(AI_OVERVIEW_RETRY_DELAY)
            except serpapi.TimeoutError as e:
                print(
                    f"Timeout resolving ai_overview (attempt {attempt}/{AI_OVERVIEW_MAX_RETRIES}): {self._redact(e)}"
                )
                if attempt == AI_OVERVIEW_MAX_RETRIES:
                    return None
                time.sleep(AI_OVERVIEW_RETRY_DELAY)
        return None


def fetch_results(
    query_records: list[dict],
    searcher: Searcher,
    output_dir: Path,
    num: int = 10,
) -> list[dict]:
    """
    Fetch SerpAPI results for all query records into output_dir.
    Skips queries whose output file already exists.
    Sleeps RATE_LIMIT_DELAY seconds between successful fetches.
    Returns metadata for each newly fetched result.
    """
    to_fetch = [
        q
        for q in query_records
        if not (output_dir / q["topic_id"] / f"{q['query_id']}.json").exists()
    ]
    total = len(to_fetch)
    fetched = []
    for i, query in enumerate(to_fetch, 1):
        topic_id = query["topic_id"]
        topic_dir = output_dir / topic_id
        topic_dir.mkdir(parents=True, exist_ok=True)

        out_file = topic_dir / f"{query['query_id']}.json"
        print(f"[{i}/{total}] {query['query']}")
        raw = searcher.search(query["query"], num=num)
        if raw is None:
            print("no results, skipping")
            continue

        ao = raw.get("ai_overview")
        if isinstance(ao, dict) and "page_token" in ao and "text_blocks" not in ao:
            resolved = searcher.resolve_ai_overview(ao["page_token"])
            if resolved and "text_blocks" in resolved:
                raw["ai_overview"] = resolved
            else:
                print(f"could not resolve ai_overview for '{query['query']}'")

        # SerpAPI occasionally returns URL delimiters double-escaped (for
        # example ``?id\\u003d7\\u0026lang\\u003den``).  Its Python mapping
        # therefore still contains literal escape text; clean URL fields
        # before persisting so downstream browsers receive navigable URLs.
        raw = decode_serp_url_fields(raw)

        # Many links arrive as opaque Google redirects (/goto?url=...). Store
        # the publisher URL instead so doc_ids are stable and robots.txt is
        # checked against the publisher (see snapir.serps.redirects).
        resolved, unresolved = resolve_serp_redirects(raw)
        if resolved or unresolved:
            print(f"resolved {resolved} redirect link(s), {unresolved} unresolved")

        payload = {
            "topic_id": topic_id,
            "query": query["query"],
            "query_id": query["query_id"],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            **raw,
        }
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        fetched.append({"topic_id": topic_id, "query_id": query["query_id"], "query": query})
        time.sleep(RATE_LIMIT_DELAY)

    return fetched


def dated_output_dir(
    base: Path, run_date: date | None = None, query_file_name: str | None = None
) -> Path:
    """Return base/{date}[_{query_file_name}], e.g. data/serps/2026-05-19_queries."""
    date_part = (run_date or date.today()).isoformat()
    if query_file_name:
        date_part += f"_{query_file_name}"
    return base / date_part


def load_query_records(queries_file: Path, query_id: str | None = None) -> list[dict]:
    records = read_jsonl(queries_file)
    if query_id:
        records = [r for r in records if r["query_id"] == query_id]
    return records


def fetch_serps(
    queries: Path,
    output: Path,
    query_id: str | None = None,
    num: int = 10,
    run_date: date | None = None,
) -> int:
    """Fetch search results for queries into output/<run_date>_<queries-stem>/.

    run_date defaults to today. Queries with an existing result file for that
    date are skipped. Returns the number of newly fetched SERPs.
    """
    if not queries.exists():
        raise FileNotFoundError(f"{queries} not found.")

    day_dir = dated_output_dir(base=output, run_date=run_date, query_file_name=queries.stem)
    print(f"Output directory: {day_dir}")

    records = load_query_records(queries, query_id=query_id)
    if not records:
        print("No query records found.")
        return 0

    already_done = sum(
        1 for r in records if (day_dir / r["topic_id"] / f"{r['query_id']}.json").exists()
    )
    print(
        f"Topics: {len({r['topic_id'] for r in records})} | Queries: {len(records)} | "
        f"Already fetched: {already_done} | To fetch: {len(records) - already_done}"
    )
    if already_done == len(records):
        print("Nothing to do.")
        return 0

    searcher = Searcher()
    fetched = fetch_results(records, searcher, output_dir=day_dir, num=num)

    print(f"Done. Fetched {len(fetched)} new result(s).")
    return len(fetched)
