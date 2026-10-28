"""Package SERPs, documents, topics, queries, runs and qrels into a
distributable collection with one self-contained directory per crawl date.

    <output>/<YYYY-MM-DD>/
        topics.jsonl.gz      topics with a SERP on that date (+ valid_from/valid_to)
        queries.jsonl.gz     queries with a SERP on that date
        serps/<topic_id>/<query_id>.json.gz
        documents.jsonl.gz   meta.json (incl. text) of every captured SERP link
        run.tsv.gz           organic ranking as a TREC run (docno = doc_id.date)
        qrels.txt            --qrels lines whose docno belongs to that date
        screenshots.tar.gz   with --captures
        pages.tar.gz         with --captures (MHTML)

Collection dates are taken from the SERP directories
(<serps>/<YYYY-MM-DD>_<queries-stem>/); several directories of one date are
merged, and only SERPs of queries in --queries are exported, so the queries
file defines the collection. A topic/query is part of a date's snapshot iff it
has a SERP on that date, so no separate validity metadata is needed.
"""

import gzip
import json
import re
import tarfile
from collections import defaultdict
from datetime import date
from pathlib import Path

from snapir.ids import doc_id_from_url, make_docno
from snapir.jsonl import read_jsonl
from snapir.serps.links import iter_serp_links

SERP_DIR = re.compile(r"^(\d{4}-\d{2}-\d{2})_")


def _write_jsonl_gz(path: Path, records: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def find_serp_dirs(
    serps: Path,
    date_from: str | None = None,
    date_to: str | None = None,
    skip_dates: list[str] | None = None,
) -> dict[str, list[Path]]:
    """{date: [dated SERP dirs]} within [date_from, date_to], both inclusive.
    `serps` is either the SERP root or a single dated directory."""
    candidates = [serps] if SERP_DIR.match(serps.name) else sorted(serps.iterdir())
    dirs: dict[str, list[Path]] = defaultdict(list)
    for path in candidates:
        m = SERP_DIR.match(path.name)
        if not path.is_dir() or not m:
            continue
        day = m.group(1)
        if date_from and day < date_from or date_to and day > date_to:
            continue
        if day in (skip_dates or []):
            print(f"note: skipping {day}")
            continue
        dirs[day].append(path)
    return dict(sorted(dirs.items()))


def _serp_files(dirs: list[Path], query_ids: set[str]) -> list[Path]:
    """<topic_id>/<query_id>.json files of the collection's queries."""
    return [path for d in dirs for path in sorted(d.glob("*/*.json")) if path.stem in query_ids]


def _tar(archive: Path, files: list[tuple[Path, str]]) -> None:
    with tarfile.open(archive, "w:gz") as tar:
        for path, arcname in files:
            tar.add(path, arcname=arcname)


def export_day(
    day: str,
    serp_files: list[Path],
    documents: Path,
    topics: dict[str, dict],
    queries: dict[str, dict],
    validity: dict[str, tuple[str, str]],
    qrels: list[str],
    output: Path,
    captures: bool,
) -> dict[str, int]:
    out = output / day
    out.mkdir(parents=True, exist_ok=True)
    serps = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in serp_files]

    topic_ids = {s["topic_id"] for _, s in serps}
    query_ids = {s["query_id"] for _, s in serps}
    missing = topic_ids - topics.keys()
    if missing:
        print(f"warning: {day}: {len(missing)} topic id(s) with SERPs are not in --topics")

    _write_jsonl_gz(
        out / "topics.jsonl.gz",
        [
            {**topics[t], "valid_from": validity[t][0], "valid_to": validity[t][1]}
            for t in sorted(topic_ids & topics.keys())
        ],
    )
    _write_jsonl_gz(
        out / "queries.jsonl.gz", [queries[q] for q in sorted(query_ids & queries.keys())]
    )

    run = []
    doc_ids = set()
    for path, serp in serps:
        target = out / "serps" / serp["topic_id"] / f"{path.stem}.json.gz"
        target.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(target, "wt", encoding="utf-8") as f:
            f.write(path.read_text(encoding="utf-8"))
        for url, result_type, position in iter_serp_links(serp):
            doc_id = doc_id_from_url(url)
            doc_ids.add(doc_id)
            if result_type == "organic" and position:
                run.append(
                    f"{serp['query_id']}\tQ0\t{make_docno(doc_id, day)}\t{position}"
                    f"\t{1.0 / position}\t{serp['search_parameters']['engine']}\n"
                )
    with gzip.open(out / "run.tsv.gz", "wt", encoding="utf-8") as f:
        f.writelines(run)

    records, screenshots, pages = [], [], []
    for doc_id in sorted(doc_ids):
        doc_dir = documents / day / doc_id
        meta = doc_dir / "meta.json"
        if not meta.is_file():
            continue
        records.append(
            {
                "doc_id": doc_id,
                "docno": make_docno(doc_id, day),
                **json.loads(meta.read_text(encoding="utf-8")),
            }
        )
        if (doc_dir / "screenshot.png").is_file():
            screenshots.append((doc_dir / "screenshot.png", f"{doc_id}.png"))
        if (doc_dir / "page.mhtml").is_file():
            pages.append((doc_dir / "page.mhtml", f"{doc_id}.mhtml"))
    _write_jsonl_gz(out / "documents.jsonl.gz", records)
    if captures:
        _tar(out / "screenshots.tar.gz", screenshots)
        _tar(out / "pages.tar.gz", pages)

    day_qrels = [line for line in qrels if line.split()[2].endswith(f".{day}")]
    if qrels:
        (out / "qrels.txt").write_text("".join(day_qrels))

    if len(records) < len(doc_ids):
        print(
            f"note: {day}: {len(doc_ids) - len(records)} of {len(doc_ids)} SERP documents not captured"
        )
    return {
        "topics": len(topic_ids & topics.keys()),
        "queries": len(query_ids & queries.keys()),
        "serps": len(serps),
        "documents": len(records),
        "run": len(run),
        "qrels": len(day_qrels),
    }


def export_snapshot(
    serps: Path,
    documents: Path,
    topics: Path,
    queries: Path,
    output: Path,
    qrels: Path | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    skip_dates: list[str] | None = None,
    captures: bool = False,
) -> int:
    for value in (date_from, date_to, *(skip_dates or [])):
        if value:
            date.fromisoformat(value)  # raises ValueError on malformed dates
    days = find_serp_dirs(serps, date_from, date_to, skip_dates)
    if not days:
        raise ValueError(f"no dated SERP directories (YYYY-MM-DD_*) found in {serps}")

    topic_records = {t["topic_id"]: t for t in read_jsonl(topics)}
    query_records = {q["query_id"]: q for q in read_jsonl(queries)}
    qrel_lines = (
        [line + "\n" for line in qrels.read_text().splitlines() if line.strip()] if qrels else []
    )

    # A topic is valid from its first to its last SERP date in this export.
    files = {day: _serp_files(dirs, query_records.keys()) for day, dirs in days.items()}
    seen: dict[str, list[str]] = defaultdict(list)
    for day, paths in files.items():
        for topic_id in {path.parent.name for path in paths}:
            seen[topic_id].append(day)
    validity = {t: (min(ds), max(ds)) for t, ds in seen.items()}

    for day, paths in files.items():
        counts = export_day(
            day,
            paths,
            documents,
            topic_records,
            query_records,
            validity,
            qrel_lines,
            output,
            captures,
        )
        print(f"{day}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    return len(days)
