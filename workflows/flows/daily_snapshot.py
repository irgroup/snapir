"""Daily snapshot of one collection: SERPs → documents → text → verification.

One run captures the live state of the web for one query set:

  1. fetch today's SERPs for every query            (snapir serps fetch)
  2. capture the documents those SERPs link to      (snapir documents scrape --serps)
  3. re-capture every document already indexed      (snapir documents scrape --rescrape)
  4. extract text from the new captures             (snapir documents extract-text)
  5. verify the snapshot is complete                (snapir export verify)

Every step skips work already done for the date, so a failed or retried run
resumes where it stopped. The date is pinned once per run, so a run that
crosses midnight stays in one snapshot. A missed day cannot be refetched
later, so an incomplete snapshot fails the flow.

Run once locally:

    uv run --extra prefect python workflows/flows/daily_snapshot.py \\
        --queries data/queries-selected.jsonl

or deploy on a schedule with workflows/prefect.yaml. Credentials come from the
environment (SERPAPI_API_KEY, BROWSER_PROFILE_DIR, EXTENSION_DIR).
"""

import argparse
from datetime import date
from pathlib import Path

from prefect import flow, task

from snapir.documents.extract_text import extract_text
from snapir.documents.scrape_documents import scrape_documents
from snapir.export.verify_snapshot import verify_snapshot
from snapir.serps.fetch import dated_output_dir, fetch_serps


@task(name="fetch-serps", retries=2, retry_delay_seconds=60)
def fetch_serps_task(queries: Path, serps: Path, run_date: date, num: int) -> int:
    return fetch_serps(queries=queries, output=serps, num=num, run_date=run_date)


@task(name="scrape-documents", retries=1, retry_delay_seconds=300)
def scrape_task(
    documents: Path,
    run_date: date,
    serps_day: Path | None,
    concurrency: int,
    headed: bool,
) -> int:
    return scrape_documents(
        serps=serps_day,
        rescrape=serps_day is None,
        query_id=None,
        headed=headed,
        interactive=False,
        output=documents,
        concurrency=concurrency,
        run_date=run_date,
    )


@task(name="extract-text")
def extract_text_task(snapshot_dir: Path) -> int:
    return extract_text(documents=snapshot_dir)


@task(name="verify-snapshot")
def verify_task(queries: Path, serps_day: Path, snapshot_dir: Path) -> dict:
    report = verify_snapshot(queries, serps_day, snapshot_dir)
    print(
        f"Verification: queries={report['queries_total']} "
        f"serp_urls={report['serp_urls_total']} "
        f"docs={report['docs_by_status']} issues={report['docs_by_issue']}"
    )
    if not report["ok"]:
        raise RuntimeError(f"Snapshot {snapshot_dir} incomplete: " + "; ".join(report["problems"]))
    return report


@flow(name="daily-snapshot", log_prints=True)
def daily_snapshot(
    queries: Path,
    serps: Path = Path("data/serps"),
    documents: Path = Path("data/docs"),
    num_results: int = 10,
    rescrape: bool = True,
    concurrency: int = 8,
    headed: bool = False,
    run_date: date | None = None,
) -> dict:
    """Capture one dated snapshot of the collection for `queries`.

    serps is the SERP root and documents the collection directory (one per
    query set). rescrape re-captures every indexed document, not only those
    in today's SERPs. headed runs a visible browser, which passes more bot
    checks; on a server, give it a virtual display (see workflows/docker).
    run_date overrides the snapshot date, e.g. to finish yesterday's run
    after midnight.
    """
    run_date = run_date or date.today()
    serps_day = dated_output_dir(base=serps, run_date=run_date, query_file_name=queries.stem)
    snapshot_dir = documents / run_date.isoformat()

    fetch_serps_task(queries, serps, run_date, num_results)
    scrape_task(documents, run_date, serps_day, concurrency, headed)
    if rescrape:
        scrape_task(documents, run_date, None, concurrency, headed)
    extract_text_task(snapshot_dir)
    return verify_task(queries, serps_day, snapshot_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run one daily snapshot locally.")
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--serps", type=Path, default=Path("data/serps"))
    parser.add_argument("--documents", type=Path, default=Path("data/docs"))
    parser.add_argument("--no-rescrape", dest="rescrape", action="store_false")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    daily_snapshot(**vars(args))
