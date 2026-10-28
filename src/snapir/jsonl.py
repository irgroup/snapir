"""JSONL helpers. Readers skip blank and malformed lines, so a record
truncated by an interrupted append does not block a resumed run."""

import json
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    """All records in path, or [] if it does not exist."""
    if not path.exists():
        return []
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def existing_values(path: Path, key: str) -> set[str]:
    """Values of `key` already present in path, for resumable generation."""
    return {r[key] for r in read_jsonl(path) if key in r}


def append_jsonl(path: Path, records: list[dict]) -> None:
    # Hand-edited files may not end in a newline — appending directly would
    # concatenate onto the last line and corrupt both records.
    needs_leading_newline = path.exists() and path.stat().st_size > 0
    if needs_leading_newline:
        with open(path, "rb") as f:
            f.seek(-1, 2)
            needs_leading_newline = f.read(1) != b"\n"
    with open(path, "a", encoding="utf-8") as f:
        if needs_leading_newline:
            f.write("\n")
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_record(f, record: dict) -> None:
    """Write one record to an open JSONL file and flush, so progress survives
    an interrupted run."""
    json.dump(record, f, ensure_ascii=False)
    f.write("\n")
    f.flush()
