"""Snapshot completeness check for one (query set, date).

A day's snapshot cannot be refetched later, so incompleteness must fail
loudly on the day itself. verify_snapshot() checks the whole chain:

  1. every query record has a SERP result file,
  2. every SERP URL has a capture attempt (meta.json) in the snapshot dir,
  3. every doc scraped "ok" has non-empty extracted text.

Flagged-but-attempted docs (paywalls, Cloudflare challenges, timeouts) are
counted but are not failures — an attempted capture whose target refused is
a valid observation; a URL never attempted is a hole. docs_by_issue splits
combined statuses (e.g. "cloudflare,paywall") into one count per cause, and
docs_by_issue_ids lists the doc_ids behind each count for targeted retries.
"""

import json
from collections import Counter
from pathlib import Path

from snapir.jsonl import read_jsonl
from snapir.serps.links import get_urls_from_serp_dir


def verify_snapshot(queries: Path, serps: Path, snapshot_dir: Path) -> dict:
    """Return a verification report; report["ok"] is the verdict.

    The snapshot may contain more docs than this SERP dir links to (other
    query sets or rescrapes of indexed URLs) — those are included in the text
    checks, but only this SERP dir's URLs are required to be present.
    """
    problems: list[str] = []

    # 1. queries -> SERP files
    query_records = read_jsonl(queries)
    serps_missing = [
        f"{r['topic_id']}/{r['query_id']}"
        for r in query_records
        if not (serps / r["topic_id"] / f"{r['query_id']}.json").exists()
    ]
    if serps_missing:
        problems.append(f"{len(serps_missing)} queries without SERP result")

    # 2. SERP URLs -> capture attempts (same URL set the scraper works from)
    url_records = get_urls_from_serp_dir(serps)
    docs_missing = [
        r["url"] for r in url_records if not (snapshot_dir / r["doc_id"] / "meta.json").exists()
    ]
    if docs_missing:
        problems.append(f"{len(docs_missing)} SERP URLs never attempted")

    # 3. scrape statuses and text for every doc in the snapshot
    status_counts: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    issue_doc_ids: dict[str, list[str]] = {}
    docs_empty_text: list[str] = []
    for meta_file in sorted(snapshot_dir.glob("*/meta.json")):
        doc_id = meta_file.parent.name
        try:
            meta = json.loads(meta_file.read_text())
        except (json.JSONDecodeError, OSError):
            status_counts["bad_meta"] += 1
            problems.append(f"unreadable meta.json: {doc_id}")
            continue
        status = meta.get("scrape_status", "unknown")
        status_counts[status] += 1
        for part in status.split(","):
            issue_counts[part] += 1
            issue_doc_ids.setdefault(part, []).append(doc_id)
        if status == "ok" and not (meta.get("text") or "").strip():
            docs_empty_text.append(doc_id)
    if docs_empty_text:
        problems.append(f"{len(docs_empty_text)} ok-docs with empty text")

    return {
        "queries_total": len(query_records),
        "serps_missing": serps_missing,
        "serp_urls_total": len(url_records),
        "docs_missing": docs_missing,
        "docs_by_status": dict(status_counts),
        "docs_by_issue": dict(issue_counts),
        "docs_by_issue_ids": issue_doc_ids,
        "docs_empty_text": docs_empty_text,
        "problems": problems,
        "ok": not problems,
    }
