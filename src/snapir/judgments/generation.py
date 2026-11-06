"""LLM relevance judgments (M/T/O scores) for SERP documents of one snapshot.

Each (topic, document) pair is judged once, with the topic title as the
query plus its description and narrative: a topic's queries are treated as
variants of one information need. Records are appended to
<output>/assessments.jsonl and <output>/qrels.txt is rewritten from it after
every run, with one line per query whose SERP links the document, so the
qrels match the per-query runs of `export snapshot`.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from topic_gen.generate import Generator

from snapir.ids import doc_id_from_url, make_docno
from snapir.jsonl import read_jsonl
from snapir.llm import make_generator, model_name, require_topic_gen
from snapir.serps.links import iter_serp_links

DEFAULT_PROMPT = "-DNA-zero-shot"  # built-in topic-gen prompt
DEFAULT_MAX_CHARS = 4_000


def _load_doc_text(documents_dir: Path, doc_id: str) -> str | None:
    """Document text from meta.json in the doc's directory."""
    meta_file = documents_dir / doc_id / "meta.json"
    if not meta_file.exists():
        return None
    try:
        return json.loads(meta_file.read_text()).get("text") or None
    except (json.JSONDecodeError, OSError):
        return None


def _collect_pairs(serp_date_dir: Path, query_id: str | None = None) -> list[dict]:
    """Unique (topic_id, doc_id) pairs, each with the query_ids whose SERP links it.

    With query_id, only pairs linked from that query's SERP are returned, but
    query_ids still lists every query of the snapshot that links the document.
    """
    query_ids: dict[tuple[str, str], list[str]] = {}
    for serp_file in sorted(serp_date_dir.glob("*/*.json")):
        try:
            serp = json.loads(serp_file.read_text())
        except json.JSONDecodeError:
            continue
        tid = serp.get("topic_id", serp_file.parent.name)
        qid = serp.get("query_id", serp_file.stem)
        for url, _result_type, _position in iter_serp_links(serp):
            qids = query_ids.setdefault((tid, doc_id_from_url(url)), [])
            if qid not in qids:
                qids.append(qid)
    return [
        {"topic_id": tid, "doc_id": did, "query_ids": qids}
        for (tid, did), qids in query_ids.items()
        if query_id is None or query_id in qids
    ]


def assess_documents(
    serp_date_dir: Path,
    documents_dir: Path,
    topics_file: Path,
    output_file: Path,
    generator: Generator | None,
    max_chars: int,
    snapshot_date: str,
    query_id: str | None = None,
) -> tuple[int, list[dict]]:
    """Judge all (topic, document) pairs in serp_date_dir not yet in output_file.

    Returns (number of pairs to judge, newly written records); with
    generator=None nothing is judged (dry run). snapshot_date forms the docno
    ({doc_id}.{date}) stored on every record, so judgments stay bound to the
    crawl they were made against.
    """
    topics = {t["topic_id"]: t for t in read_jsonl(topics_file)}
    existing = {(r["topic_id"], r["doc_id"]) for r in read_jsonl(output_file)}

    to_assess: list[dict] = []
    documents: list[str] = []
    skipped_existing = skipped_no_text = 0
    for pair in _collect_pairs(serp_date_dir, query_id=query_id):
        tid, did = pair["topic_id"], pair["doc_id"]
        if (tid, did) in existing:
            skipped_existing += 1
            continue
        if tid not in topics:
            continue
        text = _load_doc_text(documents_dir, did)
        if not text:
            skipped_no_text += 1
            continue
        to_assess.append(pair)
        documents.append(text[:max_chars] if max_chars else text)

    print(
        f"  To assess: {len(to_assess)} | Already done: {skipped_existing} "
        f"| No text: {skipped_no_text}"
    )
    if not to_assess or generator is None:
        return len(to_assess), []

    require_topic_gen()
    from topic_gen.models import MTO_responds

    results = generator.generate(
        query=[topics[p["topic_id"]].get("title", "") for p in to_assess],
        description=[topics[p["topic_id"]].get("description", "") for p in to_assess],
        narrative=[topics[p["topic_id"]].get("narrative", "") for p in to_assess],
        document=documents,
    )

    output_file.parent.mkdir(parents=True, exist_ok=True)
    llm = model_name()
    now = datetime.now(timezone.utc).isoformat()
    assessed = []
    with open(output_file, "a", encoding="utf-8") as f:
        for pair, result in zip(to_assess, results):
            if not isinstance(result, MTO_responds):
                print(f"  failed: [{pair['topic_id']}] {pair['doc_id']}")
                continue
            record = {
                "topic_id": pair["topic_id"],
                "doc_id": pair["doc_id"],
                "snapshot_date": snapshot_date,
                "docno": make_docno(pair["doc_id"], snapshot_date),
                "query_ids": pair["query_ids"],
                "M": result.M,
                "T": result.T,
                "O": result.O,
                "assessed_at": now,
                "llm_model": llm,
            }
            json.dump(record, f, ensure_ascii=False)
            f.write("\n")
            assessed.append(record)
    return len(to_assess), assessed


def _write_qrels(assessments_file: Path, qrels_file: Path) -> None:
    """Rewrite qrels.txt (query_id 0 docno O) from the full assessments file.

    A judgment is made per topic and written once for each of the topic's
    queries. Records from before the date-embedded docno scheme fall back to
    doc_id, and records from before per-query qrels to their single query_id.
    """
    seen: set[tuple[str, str]] = set()
    with open(qrels_file, "w") as fout:
        for rec in read_jsonl(assessments_file):
            if "topic_id" not in rec or "O" not in rec:
                continue
            docno = rec.get("docno") or rec["doc_id"]
            if (rec["topic_id"], docno) in seen:
                continue
            seen.add((rec["topic_id"], docno))
            query_ids = rec.get("query_ids")
            if query_ids is None:
                query_ids = [rec["query_id"]] if "query_id" in rec else []
            for qid in query_ids:
                fout.write(f"{qid} 0 {docno} {rec['O']}\n")


def generate_judgments(
    serps: Path,
    documents: Path,
    topics: Path,
    output: Path,
    query_id: str | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    dry_run: bool = False,
    prompt: Path | str = DEFAULT_PROMPT,
) -> int:
    """Judge one snapshot's SERP documents; returns the number of judgments
    written (or, with dry_run, the number of pairs that would be judged)."""
    require_topic_gen()
    from topic_gen.models import MTO_responds

    if not serps.is_dir():
        raise FileNotFoundError(f"SERP directory not found: {serps}")
    if not topics.exists():
        raise FileNotFoundError(f"Topics file not found: {topics}")
    if not documents.is_dir():
        raise FileNotFoundError(f"Documents directory not found: {documents}")

    snapshot_date = documents.name
    try:
        date.fromisoformat(snapshot_date)
    except ValueError:
        raise ValueError(
            f"Cannot derive a snapshot date from {documents} — expected a dated "
            "snapshot directory like data/docs/2026-10-26."
        ) from None

    output_file = output / "assessments.jsonl"
    qrels_file = output / "qrels.txt"

    pending, assessed = assess_documents(
        serp_date_dir=serps,
        documents_dir=documents,
        topics_file=topics,
        output_file=output_file,
        generator=None if dry_run else make_generator(prompt, MTO_responds),
        max_chars=max_chars,
        snapshot_date=snapshot_date,
        query_id=query_id,
    )
    if dry_run:
        return pending

    if output_file.exists():
        _write_qrels(output_file, qrels_file)
        print(f"TREC qrels: {qrels_file}")
    return len(assessed)
