"""Deterministic identifiers shared across the pipeline."""

import hashlib


def record_id(llm: str, prompt: str, debate: str = "", topic: str = "", query: str = "") -> str:
    """MD5 id of a generated record (topic_id, query_id) from its provenance."""
    combined = f"{llm}_{prompt}_{debate}_{topic}_{query}"
    return hashlib.md5(combined.encode()).hexdigest()


def doc_id_from_url(url: str) -> str:
    """16-char SHA-1 of the URL; the on-disk directory name and index key."""
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def make_docno(doc_id: str, snapshot_date: str) -> str:
    """Docno for qrels and WARC-TREC-ID: {doc_id}.{YYYY-MM-DD}.

    A judgment or WARC record always refers to one URL *as crawled on one
    day* — embedding the snapshot date makes cross-date joins impossible
    rather than merely discouraged.
    """
    return f"{doc_id}.{snapshot_date}"


def split_docno(docno: str) -> tuple[str, str | None]:
    """Inverse of make_docno. Returns (doc_id, snapshot_date).

    Pre-study identifiers were plain doc_ids without a date part; for those
    snapshot_date is None.
    """
    doc_id, _, snapshot_date = docno.partition(".")
    return doc_id, snapshot_date or None
