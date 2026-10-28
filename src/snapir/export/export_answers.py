"""Flatten SoSci responses into one row per assessor x judged item x query.

No respondent is filtered out here: every case of a mapped questionnaire is
exported together with its case-level signals (finished, consent, attention
checks, timing), so the user decides which assessors to keep before feeding
the cleaned table into `export qrels`.
"""

import csv
import io
from pathlib import Path

import httpx
import pandas as pd

from snapir.judgments.questionnaire import split_manifest_query_ids

JUDGED_KINDS = {"document", "ai_overview"}

# SoSci stores a select answer as the item's 1-based position, not the grade:
# item 1 = "Highly relevant (2)", 2 = "Relevant (1)", 3 = "Not relevant (0)"
# (judgments/sosci.py:RELEVANCE_ITEMS).
GRADE_FROM_ITEM = {1: 2, 2: 1, 3: 0}

# Item 4 ("Ultra elephant") is the passing answer of every attention-check
# question (data/sosci/questions-attention.xml, questionnaire.py redirect).
ATTENTION_PASS_ITEM = 4

# SoSci system variables carried into every row, renamed to snake_case.
CASE_FIELDS = {
    "STARTED": "started",
    "LASTDATA": "last_data",
    "FINISHED": "finished",
    "LASTPAGE": "last_page",
    "MAXPAGE": "max_page",
    "TIME_SUM": "time_sum",
    "MISSING": "missing",
    "DEG_TIME": "deg_time",
}

COLUMNS = [
    "questnnr",
    "case",
    "assessor_id",
    "topic_id",
    "query_id",
    "question_id",
    "kind",
    "document_source",
    "serp_position",
    "document_id",
    "docno",
    "url",
    "answer",
    "grade",
    "consent",
    *CASE_FIELDS.values(),
    "attention_checks",
    "attention_passed",
    "rationale",
]


def load_sosci(source: str) -> pd.DataFrame:
    """One row per CASE from SoSci's JSON export endpoint (URL), a saved copy
    of its JSON, or a downloaded CSV/TSV data file."""
    if source.startswith(("http://", "https://")):
        r = httpx.get(source, timeout=60)
        r.raise_for_status()
        df = _from_json(r.json())
    else:
        path = Path(source)
        if path.suffix.lower() == ".json":
            df = _from_json(pd.read_json(path, typ="series").to_dict())
        else:
            df = _from_csv(path)
    df["CASE"] = pd.to_numeric(df["CASE"], errors="coerce")
    return df.dropna(subset=["CASE"]).reset_index(drop=True)


def _from_json(payload: dict) -> pd.DataFrame:
    data = payload.get("data", payload)
    return pd.DataFrame.from_dict(data, orient="index")


def _from_csv(path: Path) -> pd.DataFrame:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"{path}: neither UTF-8 nor UTF-16 encoded")
    dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    return pd.read_csv(io.StringIO(text), sep=dialect.delimiter, dtype=str)


def resolve_manifest(path: Path) -> tuple[str, Path]:
    """Questionnaire export directory (or its manifest.csv) -> (QUESTNNR,
    manifest.csv path). The directory must be named like the questionnaire
    on SoSci, since that name is how responses are matched to the manifest."""
    manifest = path / "manifest.csv" if path.is_dir() else path
    if not manifest.is_file():
        raise ValueError(f"manifest not found at {manifest}")
    return manifest.parent.name, manifest


def _number(value) -> int | None:
    num = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(num) else int(num)


def _text(case: pd.Series, question_id: str) -> str:
    # Open text inputs are stored as <question>_01 in SoSci.
    for column in (f"{question_id}_01", question_id):
        value = case.get(column)
        if value is not None and not pd.isna(value):
            return str(value).strip()
    return ""


def _case_rows(case: pd.Series, questnnr: str, manifest: list[dict]) -> list[dict]:
    attention_ids = [r["question_id"] for r in manifest if r["kind"] == "attention_check"]
    rationale_ids = {r["topic_id"]: r["question_id"] for r in manifest if r["kind"] == "rationale"}
    base = {
        "questnnr": questnnr,
        "case": int(case["CASE"]),
        "assessor_id": case.get("PR01_RV1", ""),
        "consent": case.get("SC01", ""),
        **{name: case.get(field, "") for field, name in CASE_FIELDS.items()},
        "attention_checks": len(attention_ids),
        "attention_passed": sum(
            _number(case.get(qid)) == ATTENTION_PASS_ITEM for qid in attention_ids
        ),
    }

    rows = []
    for item in manifest:
        if item["kind"] not in JUDGED_KINDS:
            continue
        answer = _number(case.get(item["question_id"]))
        rationale_id = rationale_ids.get(item["topic_id"])
        for query_id in split_manifest_query_ids(item["query_ids"]) or [""]:
            rows.append(
                {
                    **base,
                    **{
                        k: item[k]
                        for k in (
                            "topic_id",
                            "question_id",
                            "kind",
                            "document_source",
                            "serp_position",
                            "document_id",
                            "docno",
                            "url",
                        )
                    },
                    "query_id": query_id,
                    "answer": answer,
                    "grade": GRADE_FROM_ITEM.get(answer) if answer is not None else None,
                    "rationale": _text(case, rationale_id) if rationale_id else "",
                }
            )
    return rows


def export_answers(sosci: str, manifests: dict[str, Path], output: Path) -> int:
    cases = load_sosci(sosci)
    unmapped = sorted(set(cases["QUESTNNR"].dropna()) - set(manifests))
    if unmapped:
        print(
            f"note: skipping cases of questionnaire(s) without a --manifest: {', '.join(unmapped)}"
        )

    rows = []
    for questnnr, manifest_path in manifests.items():
        manifest = pd.read_csv(manifest_path, dtype=str, keep_default_na=False)
        manifest = manifest.to_dict("records")
        subset = cases[cases["QUESTNNR"] == questnnr]
        if subset.empty:
            print(f"warning: no SoSci cases for questionnaire {questnnr}")
        for _, case in subset.iterrows():
            rows.extend(_case_rows(case, questnnr, manifest))

    df = pd.DataFrame(rows, columns=COLUMNS)
    df["answer"] = df["answer"].astype("Int64")
    df["grade"] = df["grade"].astype("Int64")
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False)
    return len(df)
