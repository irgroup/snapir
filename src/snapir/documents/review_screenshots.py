import json
import shutil
import sys
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from PIL import Image

from snapir.ids import doc_id_from_url
from snapir.serps.links import iter_serp_links

# macosx backend only exists on macOS; TkAgg works locally and over the
# worker containers' Xvfb/VNC display (see prefect/Dockerfile for python3-tk).
matplotlib.use("macosx" if sys.platform == "darwin" else "TkAgg")

# Full-page screenshots can be many thousands of px tall; the figure only
# ever shows them shrunk to fit its aspect box anyway, so downsizing before
# imshow (rather than decoding + redrawing full-res on every keypress, then
# shipping that over VNC) is a large, lossless-in-practice speedup.
MAX_DISPLAY_SIZE = (1600, 1600)

# Default view is just the top of the page (roughly one viewport height) at
# full width, so it reads clearly instead of the whole page squished thin —
# most keep/delete calls don't need to see below the fold anyway.
TOP_CROP_HEIGHT = 1600


def _load_meta(doc_dir: Path) -> dict:
    try:
        return json.loads((doc_dir / "meta.json").read_text())
    except Exception:
        return {}


def _find_pairs(documents_dir: Path) -> list[tuple[Path, Path]]:
    return [(s, s.parent) for s in sorted(documents_dir.glob("*/screenshot.png"))]


def _doc_ids_for_queries(serp_dir: Path, query_ids: list[str]) -> set[str]:
    """doc_ids of every URL the given queries' SERPs link to (organic + AI sources).

    Query files are {serps}/{topic_id}/{query_id}.json (older dirs may prefix
    the filename with "{topic_id}-").
    """
    doc_ids: set[str] = set()
    for query_id in query_ids:
        serp_files = sorted(serp_dir.glob(f"*/*{query_id}.json"))
        if not serp_files:
            raise FileNotFoundError(f"No SERP file for query {query_id} in {serp_dir}")
        for serp_file in serp_files:
            try:
                serp = json.loads(serp_file.read_text())
            except json.JSONDecodeError:
                continue
            for url, _result_type, _position in iter_serp_links(serp):
                doc_ids.add(doc_id_from_url(url))
    return doc_ids


def review_documents(
    documents: Path, serps: Path | None = None, query_ids: list[str] | None = None
) -> int:
    """Step through a snapshot's screenshots, deleting captures on `d`.

    With query_ids (requires serps), only documents those queries' SERPs link
    to are shown. Returns the number of deleted captures.
    """
    if not documents.is_dir():
        raise FileNotFoundError(f"Documents directory not found: {documents}")

    pairs = _find_pairs(documents)

    if query_ids:
        if serps is None or not serps.is_dir():
            raise ValueError("Filtering by query ID requires an existing SERP directory.")
        wanted = _doc_ids_for_queries(serps, query_ids)
        pairs = [(s, d) for s, d in pairs if d.name in wanted]
        print(
            f"Filtered to {len(pairs)} screenshots for "
            f"{len(query_ids)} quer{'y' if len(query_ids) == 1 else 'ies'} "
            f"({len(wanted)} URLs in {serps.name})."
        )

    if not pairs:
        print("No screenshots found.")
        return 0

    print(f"Found {len(pairs)} screenshots — use ←/→ to navigate, d to delete, q to quit.")

    deleted: list[str] = []
    state = {"idx": 0}

    fig, ax = plt.subplots(figsize=(14, 9))
    fig.patch.set_facecolor("black")
    ax.set_facecolor("black")
    ax.axis("off")
    plt.subplots_adjust(top=0.92, bottom=0, left=0, right=1)

    def _show():
        idx = state["idx"]
        if idx >= len(pairs):
            plt.close(fig)
            return

        screenshot, doc_dir = pairs[idx]
        meta = _load_meta(doc_dir)
        url = meta.get("url", doc_dir.name)
        status = meta.get("scrape_status", "?")

        ax.clear()
        ax.axis("off")
        try:
            img = Image.open(screenshot)
            img = img.crop((0, 0, img.width, min(img.height, TOP_CROP_HEIGHT)))
            img.thumbnail(MAX_DISPLAY_SIZE)
            ax.imshow(img)
        except Exception as e:
            ax.text(
                0.5,
                0.5,
                f"Could not load image:\n{e}",
                color="red",
                ha="center",
                va="center",
                transform=ax.transAxes,
            )

        fig.suptitle(
            f"[{idx + 1}/{len(pairs)}]  {url}  ({status})\n"
            "← / →: navigate    d: delete    q / Esc: quit",
            color="white",
            fontsize=9,
            ha="left",
            x=0.01,
        )
        fig.canvas.draw()

    def _on_key(event):
        if event.key in ("right", " "):
            state["idx"] = min(state["idx"] + 1, len(pairs) - 1)
            _show()
        elif event.key == "left":
            state["idx"] = max(state["idx"] - 1, 0)
            _show()
        elif event.key == "d":
            idx = state["idx"]
            if idx < len(pairs):
                _, doc_dir = pairs[idx]
                shutil.rmtree(doc_dir, ignore_errors=True)
                deleted.append(doc_dir.name)
                print(f"Deleted: {doc_dir.name}")
            state["idx"] = min(state["idx"] + 1, len(pairs) - 1)
            _show()
        elif event.key in ("q", "escape"):
            plt.close(fig)

    fig.canvas.mpl_connect("key_press_event", _on_key)
    _show()
    plt.show()

    kept = len(pairs) - len(deleted)
    print(f"\nReviewed {len(pairs)} | Kept {kept} | Deleted {len(deleted)}")
    return len(deleted)
