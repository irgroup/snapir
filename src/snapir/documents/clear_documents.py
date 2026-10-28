import json
import shutil
from pathlib import Path

DEFAULT_REASONS = ["cloudflare", "bot_check", "terms", "paywall", "login"]


def clear_documents(
    documents: Path, reasons: list[str] | None = None, dry_run: bool = False
) -> int:
    """Delete captures in a snapshot directory flagged with any of `reasons`.

    A combined scrape_status such as "cloudflare,paywall" matches if any of its
    parts does. Deleted documents are re-captured by the next `documents
    scrape` run. Returns the number of matching captures.
    """
    reasons = set(reasons or DEFAULT_REASONS)
    if not documents.is_dir():
        raise FileNotFoundError(f"Documents directory not found: {documents}")

    doc_dirs: list[Path] = []
    for doc_dir in sorted(documents.iterdir()):
        if not doc_dir.is_dir():
            continue
        meta = doc_dir / "meta.json"
        if not meta.exists():
            print(f"Skipping {doc_dir} — no meta.json found.")
            continue
        try:
            status = json.loads(meta.read_text()).get("scrape_status")
        except json.JSONDecodeError:
            print(f"Skipping {doc_dir} — meta.json is not valid JSON.")
            continue
        if not status:
            print(f"Skipping {doc_dir} — scrape_status not found in meta.json.")
            continue
        if reasons & set(status.split(",")):
            print(
                f"{'Would delete' if dry_run else 'Deleting'} {doc_dir} — scrape_status: {status}"
            )
            doc_dirs.append(doc_dir)

    if not dry_run:
        for doc_dir in doc_dirs:
            shutil.rmtree(doc_dir)
    return len(doc_dirs)
