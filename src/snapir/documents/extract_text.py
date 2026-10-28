import email
import io
import json
import logging
from collections import Counter
from pathlib import Path

import pypdf
import trafilatura
from tqdm import tqdm

# pypdf logs a warning for every spec violation it repairs on the fly (e.g.
# duplicate /Info keys) — common in scraped PDFs and not actionable here.
# Real read failures still surface via the exception handler in _extract_pdf.
logging.getLogger("pypdf").setLevel(logging.ERROR)

# Content fields filled from the capture. "title" is also set by the scraper
# (from the live page) and is only overwritten when extraction finds one.
_CONTENT_FIELDS = ("author", "publish_date", "language")


def html_from_mhtml(mhtml_file: Path) -> str | None:
    """Return the first text/html part of an mhtml snapshot, decoded."""
    try:
        msg = email.message_from_bytes(mhtml_file.read_bytes())
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="ignore")
    except Exception as e:
        tqdm.write(f"failed to parse {mhtml_file}: {e}")
    return None


def _extract_pdf(pdf_file: Path) -> dict:
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_file.read_bytes()))
        pages = [p.extract_text() for p in reader.pages]
        return {"text": "\n\n".join(t for t in pages if t) or None}
    except Exception as e:
        tqdm.write(f"failed to read {pdf_file}: {e}")
        return {}


def _extract_html(html: str) -> dict:
    raw = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        with_metadata=True,
        output_format="json",
    )
    if raw:
        data = json.loads(raw)
        return {
            "title": data.get("title"),
            "text": data.get("text"),
            "author": data.get("author"),
            "publish_date": data.get("date"),
            "language": data.get("language"),
        }
    return {}


def _extract_capture(doc_dir: Path) -> tuple[dict, str] | None:
    """Extract content from the doc's saved capture. None if there is no capture."""
    pdf_file = doc_dir / "page.pdf"
    mhtml_file = doc_dir / "page.mhtml"
    html_file = doc_dir / "page.html"
    if pdf_file.exists():
        return _extract_pdf(pdf_file), "pdf"
    if mhtml_file.exists():
        html = html_from_mhtml(mhtml_file)
        source = "mhtml"
    elif html_file.exists():
        html = html_file.read_text(errors="ignore")
        source = "html"
    else:
        return None
    if not html:
        return None
    return _extract_html(html), source


def extract_text_for_doc(doc_dir: Path, dry_run: bool = False, overwrite: bool = False) -> str:
    """Fill meta.json text from the doc's saved capture. Returns an outcome key."""
    meta_file = doc_dir / "meta.json"
    try:
        meta = json.loads(meta_file.read_text())
    except (json.JSONDecodeError, OSError):
        return "bad_meta"

    if not overwrite and (meta.get("text") or "").strip():
        return "has_text"

    extracted = _extract_capture(doc_dir)
    if extracted is None:
        return "no_capture"
    content, source = extracted

    text = (content.get("text") or "").strip()
    if not text:
        return "still_empty"

    if not dry_run:
        meta["text"] = text
        meta["text_source"] = source
        meta["title"] = content.get("title") or meta.get("title") or None
        for key in _CONTENT_FIELDS:
            if (overwrite or not meta.get(key)) and content.get(key):
                meta[key] = content[key]
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    return "extracted"


def extract_text(documents: Path, dry_run: bool = False, overwrite: bool = False) -> int:
    """Fill missing doc texts for every meta.json under `documents`.

    `documents` can be a collection root (all snapshots) or a single dated
    snapshot directory. With dry_run=True nothing is written; with
    overwrite=True docs that already have text are re-extracted too.
    Returns the number of docs extracted (or extractable, with dry_run).
    """
    if not documents.is_dir():
        raise FileNotFoundError(f"Documents directory not found: {documents}")

    totals: Counter[str] = Counter()
    per_snapshot: dict[str, Counter] = {}
    meta_files = sorted(documents.rglob("meta.json"))
    for meta_file in tqdm(meta_files, desc="Extracting text", unit="doc"):
        doc_dir = meta_file.parent
        snapshot = str(doc_dir.parent.relative_to(documents)) or "."
        outcome = extract_text_for_doc(doc_dir, dry_run=dry_run, overwrite=overwrite)
        totals[outcome] += 1
        per_snapshot.setdefault(snapshot, Counter())[outcome] += 1

    mode = "DRY RUN — would extract" if dry_run else "Extracted"
    print(f"{mode}: {totals['extracted']} docs")
    print(f"Already had text: {totals['has_text']}")
    print(f"Empty, no capture on disk: {totals['no_capture']}")
    print(f"Empty and extraction still empty: {totals['still_empty']}")
    if totals["bad_meta"]:
        print(f"Unreadable meta.json: {totals['bad_meta']}")

    if len(per_snapshot) > 1:
        print("\nPer snapshot (extracted / still empty / no capture):")
        for snap, c in sorted(per_snapshot.items()):
            if c["extracted"] or c["still_empty"] or c["no_capture"]:
                print(f"  {snap}: {c['extracted']} / {c['still_empty']} / {c['no_capture']}")

    return totals["extracted"]
