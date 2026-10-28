"""Aggregate a (cleaned) per-assessor answers table into TREC qrels.

Reads the CSV written by `export answers`, after the user has dropped the
assessors/rows they do not want. Only `query_id`, `docno` and `grade` are
required; rows without a grade (unanswered) are ignored.
"""

import math
from pathlib import Path

import pandas as pd

AGGREGATIONS = ("majority", "median", "max", "min")


def _median(grades: pd.Series) -> int:
    # Rounded down, so an even split between two grades resolves to the lower.
    return math.floor(grades.median())


def aggregate(grades: pd.Series, method: str = "majority") -> int:
    if method == "max":
        return int(grades.max())
    if method == "min":
        return int(grades.min())
    if method == "median":
        return _median(grades)
    modes = grades.mode()
    if len(modes) == 1:
        return int(modes.iloc[0])
    # No single majority (e.g. 2/1/0 from three assessors): fall back to the
    # median, which reproduces the legacy "all disagree -> 1" rule.
    return _median(grades)


def export_qrels(
    answers: Path, output: Path, method: str = "majority", min_assessors: int = 1
) -> int:
    if method not in AGGREGATIONS:
        raise ValueError(f"unknown aggregation {method!r}, use one of {AGGREGATIONS}")
    df = pd.read_csv(answers, dtype={"query_id": str, "docno": str})
    missing = {"query_id", "docno", "grade"} - set(df.columns)
    if missing:
        raise ValueError(f"{answers}: missing column(s) {sorted(missing)}")

    df = df.dropna(subset=["query_id", "docno", "grade"])
    df["grade"] = df["grade"].astype(int)

    qrels = []
    skipped = 0
    for (query_id, docno), group in df.groupby(["query_id", "docno"], sort=True):
        if len(group) < min_assessors:
            skipped += 1
            continue
        qrels.append(f"{query_id} 0 {docno} {aggregate(group['grade'], method)}\n")
    if skipped:
        print(f"note: skipped {skipped} pair(s) with fewer than {min_assessors} assessors")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(qrels))
    return len(qrels)
