"""SERP link enumeration, shared by scraping, judging and export.

Kept free of heavy dependencies (playwright, trafilatura) so that modules
which only need to enumerate links stay cheap to import.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path

from snapir.ids import doc_id_from_url

_LITERAL_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def decode_url_unicode_escapes(url: str) -> str:
    """Decode JSON Unicode escapes that SerpAPI sometimes double-escapes.

    A normal JSON ``"\\u003d"`` is decoded by ``json.load`` into ``"="``.
    Some SERP links instead contain ``"\\\\u003d"`` in the JSON file, which
    leaves the literal six-character sequence ``\\u003d`` in the parsed URL.
    Decode only those explicit four-hex-digit sequences rather than applying
    Python's broad ``unicode_escape`` codec to the whole URL.
    """
    return _LITERAL_UNICODE_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), url)


def decode_serp_url_fields(value):
    """Return a SERP payload with literal Unicode escapes decoded in URLs."""
    if isinstance(value, list):
        return [decode_serp_url_fields(item) for item in value]
    if not isinstance(value, dict):
        return value

    decoded = {}
    for key, item in value.items():
        is_url_field = key in {"link", "url", "website"} or key.endswith(("_link", "_url"))
        if is_url_field and isinstance(item, str):
            decoded[key] = decode_url_unicode_escapes(item)
        else:
            decoded[key] = decode_serp_url_fields(item)
    return decoded


# SERP list sections whose items carry a "link": (key, result_type, ranked).
_LINK_SECTIONS = (
    ("inline_videos", "inline_video", True),
    ("discussions_and_forums", "discussion", False),
    ("top_stories", "top_story", False),
    ("perspectives", "perspective", False),
    ("latest_posts", "latest_post", False),
    ("local_news", "local_news", False),
)


def _iter_raw_serp_links(serp: dict) -> Iterator[tuple[str, str, int | None]]:
    for hit in serp.get("organic_results", []):
        if hit.get("link"):
            yield hit["link"], "organic", hit.get("position")

    for item in serp.get("related_questions", []):
        url = item.get("link")
        # Skip Google-internal URLs (ai_overview items have no link or link to google.com)
        if url and not url.startswith("https://www.google."):
            yield url, "related_question", None

    sa = serp.get("scholarly_articles")
    if isinstance(sa, dict):
        for article in sa.get("articles", []):
            if article.get("link"):
                yield article["link"], "scholarly_article", None

    for key, result_type, ranked in _LINK_SECTIONS:
        for item in serp.get(key, []):
            if item.get("link"):
                yield item["link"], result_type, item.get("position") if ranked else None

    lf = serp.get("latest_from")
    if isinstance(lf, dict):
        for article in lf.get("articles", []):
            if article.get("link"):
                yield article["link"], "latest_from", None

    ab = serp.get("answer_box")
    if isinstance(ab, dict) and ab.get("link"):
        yield ab["link"], "answer_box", None

    lr = serp.get("local_results")
    if isinstance(lr, dict):
        for place in lr.get("places", []):
            url = place.get("links", {}).get("website")
            if url:
                yield url, "local_result", place.get("position")

    ao = serp.get("ai_overview")
    if isinstance(ao, dict):
        for ref in ao.get("references", []):
            if ref.get("link"):
                yield ref["link"], "ai_overview_source", None
        for block in ao.get("text_blocks", []):
            for sl in block.get("snippet_links", []):
                if sl.get("link"):
                    yield sl["link"], "ai_overview_source", None
            for li in block.get("list", []):
                if isinstance(li, dict):
                    for sl in li.get("snippet_links", []):
                        if sl.get("link"):
                            yield sl["link"], "ai_overview_source", None


def iter_serp_links(serp: dict) -> Iterator[tuple[str, str, int | None]]:
    """Yield (url, result_type, position) for every result link in a SERP dict.

    Covers all SERP sections considered by the pipeline; position is None for
    sections without a ranked position. Does not deduplicate. URLs are
    repaired of double-escaped Unicode chars.
    """
    for url, result_type, position in _iter_raw_serp_links(serp):
        yield decode_url_unicode_escapes(url), result_type, position


def get_urls_from_serp_dir(serp_date_dir: Path, query_id: str | None = None) -> list[dict]:
    """One record per unique URL linked from a dated SERP directory."""
    seen: set[str] = set()
    records: list[dict] = []

    pattern = f"*/{query_id}.json" if query_id else "*/*.json"
    for serp_file in sorted(serp_date_dir.glob(pattern)):
        try:
            serp = json.loads(serp_file.read_text())
        except json.JSONDecodeError:
            continue

        base = {
            "topic_id": serp.get("topic_id", serp_file.parent.name),
            "query_id": serp.get("query_id", serp_file.stem),
            "query": serp.get("query"),
        }

        for url, result_type, position in iter_serp_links(serp):
            if url in seen:
                continue
            seen.add(url)
            rec = {**base, "url": url, "doc_id": doc_id_from_url(url), "result_type": result_type}
            if position is not None:
                rec["position"] = position
            records.append(rec)

    return records
