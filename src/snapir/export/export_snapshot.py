"""Package SERPs, documents, topics, queries, runs and qrels into a
distributable collection in the LongEval Snapshot Format v2, one
self-contained directory per crawl date:

    <output>/<YYYY-MM-DD>/
        manifest.json                     format, snapshot period, field types
        documents/documents_000000.jsonl.gz
                                          meta.json (incl. text) of every captured SERP link
        queries.jsonl.gz                  queries with a SERP on that date
        topics.jsonl.gz                   topics of those queries (+ valid_from/valid_to)
        run.trec.gz                       organic ranking as a TREC run (base doc_id)
        qrels[-<set>].trec.gz             --qrels judgments of that date (base doc_id)
        serps/<topic_id>/<query_id>.json.gz
        screenshots.tar.gz, pages.tar.gz  with --captures

The output directory is the collection; its name is the manifest's
`collection`. Collection dates are taken from the SERP directories
(<serps>/<YYYY-MM-DD>_<queries-stem>/); several directories of one date are
merged, and only SERPs of queries in --queries are exported, so the queries
file defines the collection. A topic/query is part of a date's snapshot iff it
has a SERP on that date. Dates without SERPs get no directory.

Input qrels use date-bearing docnos (`<doc_id>.<YYYY-MM-DD>`, as written by
`judgments generate` and `export qrels`): the date assigns a judgment to its
snapshot, and the exported line uses the base doc_id, since the snapshot
directory already identifies the version of the page.
"""

import gzip
import json
import re
import shutil
import tarfile
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from snapir.ids import doc_id_from_url, split_docno
from snapir.jsonl import read_jsonl
from snapir.serps.links import iter_serp_links

SERP_DIR = re.compile(r"^(\d{4}-\d{2}-\d{2})_")
QRELS_SET = re.compile(r"^[a-z0-9-]+$")

FORMAT = "longeval-snapshot"
FORMAT_VERSION = 2
DOCUMENTS_PER_FILE = 50_000

# Fields the loader parses as dates/datetimes; listed in the manifest only
# when they occur in that kind's records.
FIELD_TYPES = {
    "documents": {"fetched_at": "datetime", "publish_date": "date"},
    "queries": {"generated_at": "datetime"},
    "topics": {
        "generated_at": "datetime",
        "debate_date": "date",
        "valid_from": "date",
        "valid_to": "date",
    },
}

# Document fields the loader reserves or that the format drops.
DROPPED_DOCUMENT_FIELDS = ("docno", "snapshot")


def _write_jsonl_gz(path: Path, records: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _identifier(name: str) -> str:
    """Field name as a Python identifier, as the loader would rename it."""
    name = re.sub(r"\W", "_", name)
    return f"_{name}" if name[:1].isdigit() else name


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


def _qrels_file(name: str) -> str:
    return f"qrels-{name}.trec.gz" if name else "qrels.trec.gz"


def _read_qrels(qrels: dict[str, Path]) -> dict[str, dict[str, list[tuple[str, str, str]]]]:
    """{set: {date: [(query_id, doc_id, relevance)]}} from docno-keyed qrels."""
    by_set: dict[str, dict[str, list[tuple[str, str, str]]]] = {}
    for name, path in qrels.items():
        if name and not QRELS_SET.match(name):
            raise ValueError(f"qrels set name {name!r}: use lowercase letters, digits and '-'")
        by_day: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        undated = 0
        for n, line in enumerate(path.read_text().splitlines(), start=1):
            parts = line.split()
            if not parts:
                continue
            if len(parts) != 4:
                raise ValueError(f"{path}:{n}: expected 'query_id 0 docno relevance'")
            query_id, _, docno, relevance = parts
            int(relevance)  # raises ValueError on non-integer grades
            doc_id, day = split_docno(docno)
            if day is None:
                undated += 1
                continue
            by_day[day].append((query_id, doc_id, relevance))
        if undated:
            print(f"warning: {path}: skipped {undated} line(s) without a dated docno")
        by_set[name] = by_day
    return by_set


def _manifest(
    collection: str,
    day: str,
    kinds: dict[str, list[dict]],
    qrels_descriptions: dict[str, str],
    run_description: str | None,
) -> dict:
    manifest: dict = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "collection": collection,
        "snapshot": day,
        "from": day,
        "to": (date.fromisoformat(day) + timedelta(days=1)).isoformat(),
        "period": "P1D",
    }
    for kind, records in kinds.items():
        present = {key for record in records for key in record}
        types = {k: v for k, v in FIELD_TYPES[kind].items() if k in present}
        if types:
            manifest[kind] = {"types": types}
    if qrels_descriptions:
        manifest["qrels"] = qrels_descriptions
    if run_description is not None:
        manifest["runs"] = {"": run_description}
    return manifest


def export_day(
    day: str,
    serp_files: list[Path],
    documents: Path,
    topics: dict[str, dict],
    queries: dict[str, dict],
    validity: dict[str, tuple[str, str]],
    qrels: dict[str, list[tuple[str, str, str]]],
    qrels_descriptions: dict[str, str],
    output: Path,
    captures: bool,
    collection: str,
) -> dict[str, int]:
    out = output / day
    # The day directory is fully derived from the inputs; start clean so no
    # file of an earlier export (other qrels sets, more shards) survives.
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    serps = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in serp_files]

    query_ids = sorted({s["query_id"] for _, s in serps} & queries.keys())
    query_records = [queries[q] for q in query_ids]
    topic_ids = {s["topic_id"] for _, s in serps} | {
        q["topic_id"] for q in query_records if q.get("topic_id")
    }
    topic_records = [
        {**topics[t], "valid_from": validity[t][0], "valid_to": validity[t][1]}
        for t in sorted(topic_ids)
    ]
    _write_jsonl_gz(out / "topics.jsonl.gz", topic_records)
    _write_jsonl_gz(out / "queries.jsonl.gz", query_records)

    # Organic ranking; a doc_id ranked twice for one query keeps its best rank.
    best: dict[tuple[str, str], tuple[int, str]] = {}
    engines = set()
    doc_ids = set()
    for path, serp in serps:
        target = out / "serps" / serp["topic_id"] / f"{path.stem}.json.gz"
        target.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(target, "wt", encoding="utf-8") as f:
            f.write(path.read_text(encoding="utf-8"))
        engine = serp.get("search_parameters", {}).get("engine", "serpapi")
        for url, result_type, position in iter_serp_links(serp):
            doc_id = doc_id_from_url(url)
            doc_ids.add(doc_id)
            if result_type == "organic" and position:
                engines.add(engine)
                key = (serp["query_id"], doc_id)
                if key not in best or position < best[key][0]:
                    best[key] = (position, engine)
    run = [
        f"{query_id}\tQ0\t{doc_id}\t{position}\t{1.0 / position}\t{engine}\n"
        for (query_id, doc_id), (position, engine) in sorted(
            best.items(), key=lambda item: (item[0][0], item[1][0])
        )
    ]
    if run:
        with gzip.open(out / "run.trec.gz", "wt", encoding="utf-8") as f:
            f.writelines(run)

    records, screenshots, pages = [], [], []
    for doc_id in sorted(doc_ids):
        doc_dir = documents / day / doc_id
        meta = doc_dir / "meta.json"
        if not meta.is_file():
            continue
        fields = json.loads(meta.read_text(encoding="utf-8"))
        records.append(
            {
                "doc_id": doc_id,
                **{
                    _identifier(k): v
                    for k, v in fields.items()
                    if k not in DROPPED_DOCUMENT_FIELDS and k != "doc_id"
                },
            }
        )
        if (doc_dir / "screenshot.png").is_file():
            screenshots.append((doc_dir / "screenshot.png", f"{doc_id}.png"))
        if (doc_dir / "page.mhtml").is_file():
            pages.append((doc_dir / "page.mhtml", f"{doc_id}.mhtml"))
    (out / "documents").mkdir()
    for i, start in enumerate(range(0, max(len(records), 1), DOCUMENTS_PER_FILE)):
        _write_jsonl_gz(
            out / "documents" / f"documents_{i:06d}.jsonl.gz",
            records[start : start + DOCUMENTS_PER_FILE],
        )
    if captures:
        _tar(out / "screenshots.tar.gz", screenshots)
        _tar(out / "pages.tar.gz", pages)

    # Qrels: only queries of this snapshot (judgments are made per topic and
    # copied to all its queries, including ones without a SERP that day).
    exported_sets = {}
    n_qrels = 0
    query_set = set(query_ids)
    captured = {r["doc_id"] for r in records}
    for name, lines in qrels.items():
        kept = sorted({line for line in lines if line[0] in query_set})
        if len(kept) < len(lines):
            print(
                f"note: {day}: {_qrels_file(name)}: dropped {len(lines) - len(kept)} "
                "judgment(s) of queries without a SERP that day"
            )
        uncaptured = sum(1 for _, doc_id, _ in kept if doc_id not in captured)
        if uncaptured:
            print(f"warning: {day}: {_qrels_file(name)}: {uncaptured} judged doc(s) not captured")
        if not kept:
            continue
        with gzip.open(out / _qrels_file(name), "wt", encoding="utf-8") as f:
            f.writelines(f"{q} 0 {d} {r}\n" for q, d, r in kept)
        exported_sets[name] = qrels_descriptions.get(name, "")
        n_qrels += len(kept)

    run_description = (
        f"{', '.join(sorted(e.title() for e in engines))} organic results "
        "fetched via SerpApi on the snapshot day"
        if run
        else None
    )
    manifest = _manifest(
        collection,
        day,
        {"documents": records, "queries": query_records, "topics": topic_records},
        exported_sets,
        run_description,
    )
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    if len(records) < len(doc_ids):
        print(
            f"note: {day}: {len(doc_ids) - len(records)} of {len(doc_ids)} SERP documents not captured"
        )
    return {
        "topics": len(topic_records),
        "queries": len(query_records),
        "serps": len(serps),
        "documents": len(records),
        "run": len(run),
        "qrels": n_qrels,
    }


def export_snapshot(
    serps: Path,
    documents: Path,
    topics: Path,
    queries: Path,
    output: Path,
    qrels: dict[str, Path] | None = None,
    qrels_descriptions: dict[str, str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    skip_dates: list[str] | None = None,
    captures: bool = False,
) -> int:
    """Export the collection and validate it; raises ValueError if the result
    would be rejected by the snapshot loader. `qrels` maps a set name ("" for
    the unnamed default set) to a docno-keyed TREC qrels file. The output directory's
    name is the dataset name in every manifest."""
    collection = output.resolve().name
    for value in (date_from, date_to, *(skip_dates or [])):
        if value:
            date.fromisoformat(value)  # raises ValueError on malformed dates
    qrels = qrels or {}
    qrels_descriptions = qrels_descriptions or {}
    unknown = qrels_descriptions.keys() - qrels.keys()
    if unknown:
        raise ValueError(f"description for unknown qrels set(s): {sorted(unknown)}")
    days = find_serp_dirs(serps, date_from, date_to, skip_dates)
    if not days:
        raise ValueError(f"no dated SERP directories (YYYY-MM-DD_*) found in {serps}")

    topic_records = {t["topic_id"]: t for t in read_jsonl(topics)}
    query_records = {q["query_id"]: q for q in read_jsonl(queries)}
    qrels_by_set = _read_qrels(qrels)

    files = {day: _serp_files(dirs, set(query_records)) for day, dirs in days.items()}
    for day in [d for d, paths in files.items() if not paths]:
        print(f"note: {day}: no SERPs of --queries, no snapshot written")
        del files[day]
    if not files:
        raise ValueError(f"no SERPs of the queries in {queries} found in {serps}")

    def day_topics(paths: list[Path]) -> set[str]:
        """Topics of a day's SERPs: their directories and their queries' topic_id."""
        from_queries = (query_records[path.stem].get("topic_id") for path in paths)
        return {path.parent.name for path in paths} | {t for t in from_queries if t}

    # Every exported query must have its topic (the loader rejects the day).
    topics_by_day = {day: day_topics(paths) for day, paths in files.items()}
    missing = sorted(set().union(*topics_by_day.values()) - topic_records.keys())
    if missing:
        raise ValueError(
            f"{len(missing)} topic id(s) of exported queries are not in {topics}: "
            + ", ".join(missing[:5])
            + (" …" if len(missing) > 5 else "")
        )

    # A topic is valid from its first to its last SERP date in this export.
    seen: dict[str, list[str]] = defaultdict(list)
    for day, day_topic_ids in topics_by_day.items():
        for topic_id in day_topic_ids:
            seen[topic_id].append(day)
    validity = {t: (min(ds), max(ds)) for t, ds in seen.items()}

    for name, by_day in qrels_by_set.items():
        outside = sum(len(lines) for d, lines in by_day.items() if d not in files)
        if outside:
            print(f"note: {_qrels_file(name)}: {outside} judgment(s) of dates outside this export")

    for day, paths in files.items():
        counts = export_day(
            day,
            paths,
            documents,
            topic_records,
            query_records,
            validity,
            {name: by_day.get(day, []) for name, by_day in qrels_by_set.items()},
            qrels_descriptions,
            output,
            captures,
            collection,
        )
        print(f"{day}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))

    return len(files)
