import csv
import hashlib
import html
import json
import random
import shutil
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import date
from itertools import groupby
from pathlib import Path

from snapir.ids import doc_id_from_url, make_docno, split_docno
from snapir.jsonl import read_jsonl
from snapir.serps.links import decode_url_unicode_escapes, iter_serp_links

# Only these SERP result types are exported; every other section (related
# questions, discussions, videos, ...) is excluded.
SOURCE_BY_RESULT_TYPE = {
    "organic": "Web",
    "top_story": "News",
    "ai_overview_source": "AI",
}
# Pool descriptions. Every document page draws from one flat pool whatever
# its query set and SERP section — the distinctions live in manifest.csv.
# AI overviews have their own, since their question wording differs.
DOCUMENT_DESC = "relevance"
AI_OVERVIEW_DESC = "relevance-ai"
ATTENTION_DESC = "attention-check"
# Pre-shuffle order within a topic (kept deterministic for --seed).
RESULT_TYPE_ORDER = {"organic": 0, "top_story": 1, "ai_overview_source": 2}
RATIONALE_DESC = "Rationale"


def split_manifest_query_ids(value: object) -> list[str]:
    """Parse the query-id list stored in a questionnaire manifest cell.

    Current manifests use semicolons. Accept commas as well for older or
    hand-edited files, while keeping one canonical parser for exclusion,
    qrel creation, and dataset packaging.
    """
    if not isinstance(value, str):
        return []
    return [part.strip() for part in value.replace(",", ";").split(";") if part.strip()]


# ---------------------------------------------------------------------------
# Item collection
# ---------------------------------------------------------------------------


@dataclass
class StudyItem:
    topic_id: str
    query_ids: list[str]
    doc_id: str
    docno: str
    url: str
    title: str | None = None
    result_type: str | None = None
    position: int | None = None
    screenshot_path: Path | None = None
    pdf_path: Path | None = None
    status: str = "included"  # included | skipped_no_meta | skipped_scrape_error | skipped_no_screenshot | skipped_re_covered


def render_pdf_screenshot(
    pdf_path: Path, out_path: Path, max_pages: int = 3, dpi: int = 150
) -> None:
    """Render the first max_pages of a PDF stacked vertically into one PNG,
    as a screenshot substitute for documents scraped as page.pdf."""
    import pymupdf
    from PIL import Image

    matrix = pymupdf.Matrix(dpi / 72, dpi / 72)
    with pymupdf.open(pdf_path) as doc:
        pages = [
            page.get_pixmap(matrix=matrix, colorspace=pymupdf.csRGB)
            for page in doc.pages(stop=min(max_pages, doc.page_count))
        ]
    images = [Image.frombytes("RGB", (p.width, p.height), p.samples) for p in pages]
    combined = Image.new(
        "RGB",
        (max(i.width for i in images), sum(i.height for i in images)),
        "white",
    )
    y = 0
    for image in images:
        combined.paste(image, (0, y))
        y += image.height
    combined.save(out_path)


# Screenshots are almost entirely text on a plain background, so a small
# palette compresses far better than truecolor with no readable loss.
SCREENSHOT_COLORS = 32


def add_screenshot_border(
    path: Path, width: int = 2, color: str = "#6e7781", padding: int = 6
) -> None:
    """Draw the document frame into the PNG itself.

    The screenshot is rendered by SoSci's own <image> element, which cannot
    be wrapped or reliably styled from the questionnaire HTML, so the border
    is burned into the file: a white margin (padding) inside a solid frame,
    which reads as a page edge even against a white survey background."""
    from PIL import Image, ImageOps

    image = Image.open(path)
    if image.mode != "RGB":
        image = image.convert("RGB")
    if padding:
        image = ImageOps.expand(image, border=padding, fill="#ffffff")
    image = ImageOps.expand(image, border=width, fill=color)
    image.quantize(colors=SCREENSHOT_COLORS).save(path, optimize=True)


def shrink_screenshot(
    path: Path,
    max_bytes: int = 1_000_000,
    max_height: int = 15000,
    min_height: int = 4000,
    min_width: int = 800,
) -> None:
    """Reduce a PNG below max_bytes (SoSci media upload limit: 1 MB).

    Palette quantization first; if that is not enough, very tall pages are
    progressively cropped (keeping the top) before any downscaling, so the
    retained content stays readable. Warns if the limit is still exceeded
    at min_width/min_height."""
    if path.stat().st_size <= max_bytes:
        return
    from PIL import Image

    image = Image.open(path).convert("RGB")
    if image.height > max_height:
        image = image.crop((0, 0, image.width, max_height))
    while True:
        image.quantize(colors=SCREENSHOT_COLORS).save(path, optimize=True)
        if path.stat().st_size <= max_bytes:
            return
        if image.height > min_height:
            image = image.crop((0, 0, image.width, int(image.height * 0.75)))
        elif image.width > min_width:
            image = image.resize((int(image.width * 0.8), int(image.height * 0.8)), Image.LANCZOS)
        else:
            print(
                f"{path} still {path.stat().st_size} bytes after shrinking "
                f"({image.width}x{image.height})"
            )
            return


def load_topics(path: Path) -> dict[str, dict]:
    """Topics JSONL keyed by topic_id; last record wins on duplicate ids."""
    return {record["topic_id"]: record for record in read_jsonl(path)}


def find_query_files(
    serp_dir: Path, query_ids: tuple[str, ...], topic_ids: tuple[str, ...]
) -> list[tuple[str, str, Path]]:
    """Resolve requested query/topic ids to (topic_id, query_id, serp_file).

    Query files are {serps}/{topic_id}/{query_id}.json (older dirs may prefix
    the filename with "{topic_id}-"). Raises ValueError for requested ids
    with no SERP file.
    """
    resolved: dict[tuple[str, str], Path] = {}

    def add(topic_dir: Path, query_file: Path):
        stem = query_file.stem
        prefix = f"{topic_dir.name}-"
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
        resolved[(topic_dir.name, stem)] = query_file

    for topic_id in topic_ids:
        topic_dir = serp_dir / topic_id
        files = sorted(topic_dir.glob("*.json")) if topic_dir.is_dir() else []
        if not files:
            raise ValueError(f"No SERP files for topic {topic_id} in {serp_dir}")
        for query_file in files:
            add(topic_dir, query_file)

    for query_id in query_ids:
        matches = sorted(serp_dir.glob(f"*/*{query_id}.json"))
        if not matches:
            raise ValueError(f"No SERP file for query {query_id} in {serp_dir}")
        for query_file in matches:
            add(query_file.parent, query_file)

    return [(t, q, p) for (t, q), p in sorted(resolved.items())]


def collect_items(
    query_files: list[tuple[str, str, Path]],
    documents_dir: Path,
    snapshot_date: str,
    include_types: set[str] | None = None,
) -> list[StudyItem]:
    """Gather one StudyItem per (topic_id, doc_id) across the selected queries.

    include_types restricts which SERP result types are considered (None =
    all); filtering happens per link, so a document excluded in one query's
    SERP section still counts when another query retrieves it in an included
    section. A document retrieved by several of a topic's queries is judged
    once; query_ids accumulates. Documents without a usable screenshot are
    kept with a skipped_* status so coverage gaps stay auditable in the
    manifest.
    """
    items: dict[tuple[str, str], StudyItem] = {}
    for topic_id, query_id, serp_file in query_files:
        with open(serp_file) as f:
            serp = json.load(f)
        for url, result_type, position in iter_serp_links(serp):
            if include_types is not None and result_type not in include_types:
                continue
            doc_id = doc_id_from_url(url)
            key = (topic_id, doc_id)
            if key in items:
                if query_id not in items[key].query_ids:
                    items[key].query_ids.append(query_id)
                continue

            item = StudyItem(
                topic_id=topic_id,
                query_ids=[query_id],
                doc_id=doc_id,
                docno=make_docno(doc_id, snapshot_date),
                url=url,
                result_type=result_type,
                position=position,
            )
            meta_file = documents_dir / doc_id / "meta.json"
            screenshot = documents_dir / doc_id / "screenshot.png"
            pdf_path = documents_dir / doc_id / "page.pdf"
            if not meta_file.exists():
                item.status = "skipped_no_meta"
                print(f"No meta.json for {doc_id} ({url}) — skipping")
            else:
                meta = json.loads(meta_file.read_text())
                item.title = meta.get("title")
                if meta.get("scrape_status") != "ok":
                    item.status = "skipped_scrape_error"
                    print(
                        f"scrape_status={meta.get('scrape_status')} for {doc_id} ({url}) — skipping"
                    )
                elif screenshot.exists() and screenshot.stat().st_size:
                    item.screenshot_path = screenshot
                elif pdf_path.exists() and pdf_path.stat().st_size:
                    # PDFs never get a browser screenshot.png; the caller
                    # renders one from page.pdf instead (render_pdf_screenshot).
                    item.pdf_path = pdf_path
                else:
                    item.status = "skipped_no_screenshot"
                    print(f"No screenshot for {doc_id} ({url}) — skipping")
            items[key] = item
    return list(items.values())


def file_sha256(path: Path) -> str:
    """Stream-hash a file — used to compare mHTML snapshots (can run a few MB
    each) without loading both fully into memory just to check equality."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_excluded_documents(
    manifest_paths: tuple[Path, ...], documents_base: Path
) -> dict[tuple[str, str], dict]:
    """(query_id, doc_id) -> {"docno", "manifest", "text", "doc_dir"} for
    every document page in prior manifest.csv export(s) (e.g. an in-flight RE
    study), so a new IN questionnaire can skip documents already covered
    there instead of judging the same URL twice in the same period.

    A manifest document can belong to multiple queries; ``query_ids`` is the
    semicolon-separated list written by create_questionnaire. Indexing
    every query/document pair separately is important: a document covered by
    a prior export for query A must not be excluded merely because query B,
    under the same topic, also ranks it.

    A doc_id/URL match alone isn't proof the content is still the same — the
    caller decides that by comparing `text` (that document's extracted text
    as scraped for the manifest's own snapshot date) against what's freshly
    scraped now: skip only if it still matches, otherwise treat it as a new
    item to judge. `doc_dir` (the manifest's own dated document directory,
    for locating page.mhtml) is exposed for an additional, independent raw
    mHTML comparison the caller uses to warn on any markup drift regardless
    of that decision. Both are None if the manifest's snapshot dir is no
    longer on disk.
    """
    excluded: dict[tuple[str, str], dict] = {}
    for manifest_path in manifest_paths:
        with open(manifest_path, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("kind") != "document":
                    continue
                query_ids = split_manifest_query_ids(row.get("query_ids"))
                if not query_ids:
                    print(
                        f"{manifest_path}: document {row.get('docno', '<unknown>')} "
                        "has no query_ids; cannot safely use it as a query-scoped "
                        "exclusion"
                    )
                    continue
                doc_id, date = split_docno(row["docno"])
                text = None
                doc_dir = None
                if date:
                    candidate_dir = documents_base / date / doc_id
                    meta_file = candidate_dir / "meta.json"
                    if meta_file.exists():
                        doc_dir = candidate_dir
                        text = json.loads(meta_file.read_text()).get("text")
                record = {
                    "docno": row["docno"],
                    "manifest": manifest_path,
                    "text": text,
                    "doc_dir": doc_dir,
                }
                for query_id in query_ids:
                    excluded[(query_id, doc_id)] = record
    return excluded


@dataclass
class ItemDecision:
    """One document's disposition for a single query pass of
    resolve_query_items: whether it's judged here, already judged elsewhere,
    or unusable.

    `status` is one of:
      - "included": judged in this questionnaire (fresh, or re_drifted=True
        if a prior RE export's copy exists but its text has changed enough
        that it's no longer really "the same document" RE is covering)
      - "repeat": already collected for an earlier query of the same topic
        (StudyItem.docno seen before in `seen_docs`)
      - "re_covered": same URL already covered by a prior RE export
        (excluded_docs) with matching or unverifiable text — not judged
        here; `re_manifest`/`re_docno` identify the export/row to backfill
        this judgment from
      - "no_screenshot": collect_items already marked the item unusable
        (skipped_no_meta / skipped_scrape_error / skipped_no_screenshot —
        see item.status for which)

    `note` is a ready-to-print, indented one-line detail for "re_covered"
    and drifted "included" decisions ("=" unchanged, "~" text unchanged but
    mHTML changed, "+" text drifted and included, "!" not comparable and
    skipped); None otherwise.
    """

    item: StudyItem
    status: str
    re_drifted: bool = False
    note: str | None = None
    re_manifest: Path | None = None
    re_docno: str | None = None


def resolve_query_items(
    topic_id: str,
    query_id: str,
    topic_items: list[StudyItem],
    excluded_docs: dict[tuple[str, str], dict],
    documents_dir: Path,
    seen_docs: set[tuple[str, str]],
) -> list[ItemDecision]:
    """Decide which of one query's documents need judging here.

    `topic_items` is the already-deduplicated (one per doc_id, see
    collect_items) list of this topic's items that this query retrieved,
    in the order decisions should be made — order only matters for the
    repeat check below, via `seen_docs`.

    For each item, first check `excluded_docs` (from
    load_excluded_documents) — if a prior RE
    export covers the same (query_id, doc_id) and its extracted text still
    matches (or neither side's text is available to compare, as a
    conservative fallback), it's "re_covered" and not judged here; if the
    text has drifted, it remains eligible here (re_drifted=True). Then skip
    it as a repeat if its (topic_id, docno) is already in `seen_docs` from an
    earlier eligible query of this topic; otherwise fall through to
    collect_items' own verdict (item.status), included only if it has a
    usable screenshot.

    Mutates `seen_docs`, adding every item's (topic_id, docno) that ends up
    "included" here (fresh or re_drifted). Exclusion does not mutate the
    shared StudyItem: collect_items reuses one item for a document ranked by
    multiple queries, and excluding it for one query must not make it
    unusable for another query.
    """
    decisions: list[ItemDecision] = []
    for item in topic_items:
        excluded = excluded_docs.get((query_id, item.doc_id))
        re_drifted = False
        re_note = None
        if excluded is not None:
            current_meta = documents_dir / item.doc_id / "meta.json"
            current_text = (
                json.loads(current_meta.read_text()).get("text") if current_meta.exists() else None
            )
            # Raw markup drift is checked independently of the skip/include
            # decision below — purely informational, since page.mhtml
            # routinely differs in ads/timestamps even when the article
            # text is unchanged.
            old_mhtml = excluded["doc_dir"] / "page.mhtml" if excluded["doc_dir"] else None
            current_mhtml = documents_dir / item.doc_id / "page.mhtml"
            mhtml_changed = bool(
                old_mhtml
                and old_mhtml.exists()
                and current_mhtml.exists()
                and file_sha256(old_mhtml) != file_sha256(current_mhtml)
            )
            mhtml_suffix = "; mHTML changed" if mhtml_changed else ""
            # Short label identifying the earlier export, without repeating
            # its full path on every line.
            re_label = excluded["manifest"].parent.name or excluded["manifest"]

            if excluded["text"] is None or current_text is None:
                # Can't verify sameness — fall back to the conservative
                # default (skip) rather than risk a duplicate judgment.
                decisions.append(
                    ItemDecision(
                        item=item,
                        status="re_covered",
                        note=(
                            f"  ! {item.doc_id}: no text to compare against "
                            f"{re_label} — skipped as a precaution{mhtml_suffix}"
                        ),
                        re_manifest=excluded["manifest"],
                        re_docno=excluded["docno"],
                    )
                )
                continue
            if excluded["text"] == current_text:
                note = (
                    f"  ~ {item.doc_id}: excluded – text unchanged; mHTML changed"
                    if mhtml_changed
                    else f"  = {item.doc_id}: excluded – unchanged"
                )
                decisions.append(
                    ItemDecision(
                        item=item,
                        status="re_covered",
                        note=note,
                        re_manifest=excluded["manifest"],
                        re_docno=excluded["docno"],
                    )
                )
                continue
            # Text has drifted — no longer really "the same document" the RE
            # study is covering, so judge it here instead of skipping it.
            re_drifted = True
            re_note = f"  + {item.doc_id}: included – text changed{mhtml_suffix} "

        # Exclusions are checked first because they apply to a query/document
        # pair. The same document may already have a questionnaire page for a
        # different query without making this query eligible for that page.
        if (topic_id, item.docno) in seen_docs:
            decisions.append(
                ItemDecision(
                    item=item,
                    status="repeat",
                    re_drifted=re_drifted,
                    note=re_note,
                )
            )
            continue

        if item.status != "included":
            decisions.append(ItemDecision(item=item, status="no_screenshot"))
            continue

        seen_docs.add((topic_id, item.docno))
        decisions.append(
            ItemDecision(
                item=item,
                status="included",
                re_drifted=re_drifted,
                note=re_note,
            )
        )
    return decisions


# ---------------------------------------------------------------------------
# Linked-pool questionnaire
#
# Questionnaire pages link questionnaire pages to a pool of questions the user
# pre-created in SoSci and imported once — every questionnaire references the
# same questions. Pool files are read, never modified. Questions are
# identified by their <description> field (e.g. "relevance",
# "relevance-ai"). When a description runs out of questions,
# pool exhaustion is a hard error — the pool files ship with a fixed number
# of duplicates per description (one per expected item slot) and must be
# regenerated with a higher count if an export needs more.
# ---------------------------------------------------------------------------


def _sosci_attr(elem: ET.Element, key: str) -> str | None:
    attr = elem.find(f"attributes.specific/attr[@id='{key}']")
    return attr.text if attr is not None else None


def _normalize_desc(desc: str) -> str:
    """Tolerate the 'dynmic' typo present in some pool descriptions."""
    return desc.replace("dynmic", "dynamic").strip()


class QuestionPool:
    """Pre-created SoSci questions keyed by <description>, allocated in pos
    order. The pool is fixed size — each description must have one question
    per expected item slot already present in the pool files; alloc() raises
    once a description is exhausted rather than cloning, so a shortfall is
    caught immediately with an actionable message. Question reference ids
    are {section_id}{question_id:02d}, e.g. R001 — also the response
    variable."""

    def __init__(self, roots: ET.Element | list[ET.Element]):
        if isinstance(roots, ET.Element):
            roots = [roots]
        self._by_desc: dict[str, list[tuple[ET.Element, ET.Element]]] = {}
        for root in roots:
            for section in root.iter("section"):
                for question in section.findall("question"):
                    desc = _normalize_desc(question.findtext("description") or "")
                    if desc:
                        self._by_desc.setdefault(desc, []).append((section, question))
        self._cursor: dict[str, int] = {d: 0 for d in self._by_desc}

    def descriptions(self) -> list[str]:
        return sorted(self._by_desc)

    def alloc(self, desc: str) -> str:
        desc = _normalize_desc(desc)
        pool = self._by_desc.get(desc)
        if not pool:
            raise ValueError(
                f"No question with description '{desc}' in the template "
                f"(available: {self.descriptions()})"
            )
        index = self._cursor[desc]
        if index >= len(pool):
            raise ValueError(
                f"Question pool exhausted for '{desc}': needed at least "
                f"{index + 1} but only {len(pool)} available. Add more "
                f"'{desc}' question duplicates to its pool file and re-run."
            )
        section, question = pool[index]
        self._cursor[desc] = index + 1
        section_id = _sosci_attr(section, "id")
        question_id = int(_sosci_attr(question, "id") or 0)
        if question_id > 99:
            raise ValueError(
                f"Section {section_id} holds more than 99 questions — SoSci "
                f"ids are 2-digit, so question {question_id} has no valid "
                "reference. Split the pool over more sections."
            )
        return f"{section_id}{question_id:02d}"


# Display styling. Inline styles are used throughout (SoSci's HTML sanitizer
# is more predictable with them than with stylesheets); the one <style> rule
# is for the screenshot, which SoSci renders from its own <image> element —
# if it were ever stripped the page just loses the frame, nothing breaks.
_CARD_BG = "#f6f8fa"
_CARD = (
    "border:1px solid #d0d7de;border-radius:6px;padding:14px 18px;"
    f"background:{_CARD_BG};margin:0 0 22px 0;"
)
_HEADING = (
    "margin:0 0 8px 0;font-size:1.05em;font-weight:bold;color:#24292f;"
    "border-bottom:2px solid #d0d7de;padding-bottom:4px;"
)
_FIELD_LABEL = (
    "font-size:0.8em;text-transform:uppercase;letter-spacing:0.06em;"
    "color:#57606a;margin-bottom:2px;"
)


def section_heading_html(label: str) -> str:
    """Section divider, e.g. Topic / Website / AI Overview / Questions."""
    return f'<p style="{_HEADING}">{html.escape(label)}</p>'


def topic_html(topic: dict) -> str:
    """Topic block: a labeled card with title, description and narrative."""
    rows = []
    for label, value, size in (
        ("Title", topic.get("title"), "1.2em"),
        ("Description", topic.get("description"), "1.05em"),
        ("Narrative", topic.get("narrative"), "1em"),
    ):
        if not value:
            continue
        weight = "bold" if label == "Title" else "normal"
        rows.append(
            f'<div style="margin-bottom:12px;">'
            f'<div style="{_FIELD_LABEL}">{label}</div>'
            f'<div style="font-size:{size};font-weight:{weight};line-height:1.45;">'
            f"{html.escape(value)}</div></div>"
        )
    return section_heading_html("Topic") + f'<div style="{_CARD}">' + "".join(rows) + "</div>"


# TREC topic 354 (TREC-8 ad hoc), kept verbatim apart from unwrapping the
# fixed-width lines. Used only as the worked example in the intro, so it can
# never collide with a study topic.
EXAMPLE_TOPIC = {
    "title": "journalist risks",
    "description": (
        "Identify instances where a journalist has been put at risk (e.g., "
        "killed, arrested or taken hostage) in the performance of his work."
    ),
    "narrative": (
        "Any document identifying an instance where a journalist or "
        "correspondent has been killed, arrested or taken hostage in the "
        "performance of his work is relevant."
    ),
}


# Placeholder written into the intro page of the working questionnaire, marking
# where the worked example belongs. Plain text rather than an HTML comment so it
# survives a round-trip through SoSci's WYSIWYG editor.
EXAMPLE_TOPIC_MARKER = "[EXAMPLE-TOPIC]"


def example_topic_html() -> str:
    """The intro's worked example, rendered by the same builder as the real
    topics so the layout the annotators learn here is the one they will see."""
    return topic_html(EXAMPLE_TOPIC)


def insert_example_topic(pages: list[list[tuple[str, str]]]) -> bool:
    """Replace EXAMPLE_TOPIC_MARKER in the intro pages with the example card,
    in place. Returns whether the marker was found."""
    found = False
    for page in pages:
        for i, (tag, content) in enumerate(page):
            if tag == "html" and EXAMPLE_TOPIC_MARKER in content:
                page[i] = (
                    tag,
                    content.replace(EXAMPLE_TOPIC_MARKER, example_topic_html()),
                )
                found = True
    return found


def train_intro_html() -> str:
    """Page shown right before the worked examples, so the annotators know the
    ratings there are practice and come with feedback."""
    return (
        section_heading_html("Examples") + f'<div style="{_CARD}">'
        '<p style="margin:0 0 10px 0;line-height:1.45;">Before the real task '
        "starts, here are <b>three worked examples</b>.</p>"
        '<p style="margin:0;line-height:1.45;">Each one shows an information '
        "need and a website. Please rate it as you would in the real task, "
        "on the next page you will see which grade we would have given and "
        "why. These practice ratings are not part of the study results.</p>"
        "</div>"
    )


def train_outro_html() -> str:
    """Page shown right after the worked examples, marking the start of the
    real task."""
    return (
        section_heading_html("Let's start") + f'<div style="{_CARD}">'
        '<p style="margin:0 0 10px 0;line-height:1.45;">That was it for the '
        "examples, the real task starts on the next page.</p>"
        '<p style="margin:0;line-height:1.45;">From here on there is no '
        "feedback, and the information need changes from time to time, so "
        "please read it carefully whenever it does. Thank you for your careful "
        "attention!</p>"
        "</div>"
    )


def attention_check_html() -> str:
    """Banner shown above the topic card on the attention-check page,
    flagging it as such. Uses the same border/radius/padding/background as
    the topic card (_CARD) so the banner reads as part of the same block
    rather than a separate alert style — only the bold text signals it."""
    return f'<div style="{_CARD}font-weight:bold;">This is an attention check.</div>'


def website_html() -> str:
    """Heading for the screenshot that follows on the page. The frame around
    the screenshot is burned into the PNG (see add_screenshot_border) — SoSci
    renders the image from its own <image> element, out of reach of the
    questionnaire HTML."""
    return section_heading_html("Website")


def _citation_markers(reference_indexes) -> str:
    """Render a block's (or list item's) reference_indexes as inline [n,m]
    markers, 1-based to match the numbered source list in _sources_html."""
    if not isinstance(reference_indexes, list):
        return ""
    nums = sorted({i + 1 for i in reference_indexes if isinstance(i, int)})
    if not nums:
        return ""
    return (
        ' <sup style="font-size:0.85em;color:#57606a;">['
        + ",".join(str(n) for n in nums)
        + "]</sup>"
    )


def _ai_overview_sources_html(references: list) -> str:
    """Numbered source list for the bottom of the AI Overview card, matching
    the [n] markers inline in the text (n = reference index + 1)."""
    items = [ref for ref in references if isinstance(ref, dict) and ref.get("link")]
    if not items:
        return ""
    rows = []
    for ref in sorted(items, key=lambda r: r.get("index", 0)):
        n = ref.get("index", 0) + 1
        link = decode_url_unicode_escapes(ref.get("link"))
        rows.append(
            f'<li style="margin-bottom:4px;word-break:break-all;">'
            f'[{n}] <a href="{html.escape(link)}">{html.escape(link)}</a></li>'
        )
    return (
        '<div style="margin-top:14px;padding-top:10px;'
        'border-top:1px solid #d0d7de;">'
        '<p style="margin:0 0 6px 0;font-size:0.9em;color:#57606a;">'
        "<b>Sources</b></p>"
        '<ol style="margin:0;padding-left:22px;font-size:0.85em;">' + "".join(rows) + "</ol></div>"
    )


def ai_overview_html(ai_overview: dict) -> str:
    """Render SerpAPI ai_overview text_blocks into a display card.

    Each block's reference_indexes become inline [n] citation markers, and
    the referenced sources are listed with their URLs at the bottom, so
    annotators can trace a claim back to the site it came from.
    """
    parts = []
    for block in ai_overview.get("text_blocks", []):
        block_type = block.get("type")
        snippet = block.get("snippet")
        refs = _citation_markers(block.get("reference_indexes"))
        if block_type == "heading" and snippet:
            parts.append(f'<p style="margin:14px 0 6px 0;"><b>{html.escape(snippet)}</b>{refs}</p>')
        elif block_type == "list":
            items = []
            for li in block.get("list", []):
                if not isinstance(li, dict):
                    continue
                text = ": ".join(t for t in (li.get("title"), li.get("snippet")) if t)
                if text:
                    item_refs = _citation_markers(li.get("reference_indexes"))
                    items.append(
                        f'<li style="margin-bottom:6px;">{html.escape(text)}{item_refs}</li>'
                    )
            if items:
                parts.append(
                    '<ul style="margin:6px 0;padding-left:22px;">' + "".join(items) + "</ul>"
                )
            if refs:
                parts.append(f'<p style="margin:-4px 0 6px 0;">{refs}</p>')
        elif snippet:
            parts.append(
                f'<p style="margin:0 0 10px 0;line-height:1.5;">{html.escape(snippet)}{refs}</p>'
            )
    parts.append(_ai_overview_sources_html(ai_overview.get("references", [])))
    return (
        section_heading_html("AI Overview") + f'<div style="{_CARD}">' + "".join(parts) + "</div>"
    )


def _parse_page(page: ET.Element, path: Path) -> list[tuple[str, str]]:
    """One questionnaire <page> in builder form; unsupported tags are dropped
    with a warning rather than failing a whole import."""
    elements: list[tuple[str, str]] = []
    for child in page:
        if child.tag in ("html", "php"):
            elements.append((child.tag, child.text or ""))
        elif child.tag == "image":
            src = child.get("src") or ""
            elements.append(("image", src.removeprefix("pro://")))
        elif child.tag == "question":
            elements.append(("question", child.get("id") or ""))
        else:
            print(f"Page element <{child.tag}> in {path} not supported — dropped")
    return elements


def extract_intro_pages(path: Path) -> list[list[tuple[str, str]]]:
    """Pages of the working questionnaire example preceding the first task
    page (= first page showing a screenshot), in builder form.

    The boundary is the image, not the question: intro pages may carry
    questions of their own — the consent page (SC01) and any demographics —
    and those are copied along with the instruction pages."""
    root = ET.parse(path).getroot()
    if root.tag != "questionnaire":
        raise ValueError(f"{path}: root element is {root.tag}, expected questionnaire")
    intro: list[list[tuple[str, str]]] = []
    for page in root.findall("page"):
        if page.find("image") is not None:
            break
        intro.append(_parse_page(page, path))
    return intro


def extract_pages(path: Path) -> list[list[tuple[str, str]]]:
    """Every page of a questionnaire XML in builder form.

    Used for the hand-written training block (data/sosci/train.xml), which —
    unlike the generated pages — already contains its images, question refs
    and the PHP feedback pages, and is spliced in verbatim."""
    root = ET.parse(path).getroot()
    if root.tag != "questionnaire":
        raise ValueError(f"{path}: root element is {root.tag}, expected questionnaire")
    return [_parse_page(page, path) for page in root.findall("page")]


def page_images(pages: list[list[tuple[str, str]]]) -> list[str]:
    """Media filenames referenced by the given pages, in order, deduplicated."""
    names: list[str] = []
    for page in pages:
        for kind, value in page:
            if kind == "image" and value not in names:
                names.append(value)
    return names


def build_linked_questionnaire(pages: list[list[tuple[str, str]]]) -> str:
    """Serialize questionnaire pages into SoSci's questionnaire XML (the
    escaped payload of the questionnaire 'content' attr).

    Each page is a list of (kind, value) with kind 'html' (CDATA-wrapped
    display HTML), 'php' (CDATA-wrapped PHP, as used by the training block's
    feedback pages), 'image' (media filename), or 'question' (question
    ref)."""
    counter = 1

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return str(counter - 1)

    lines = ['<?xml version="1.0"?>', "<questionnaire>", ""]
    for n, page in enumerate(pages, start=1):
        lines.append(f"<!-- Page {n} -->")
        lines.append(f'<page intID="{next_id()}">')
        for kind, value in page:
            if kind in ("html", "php"):
                if "]]>" in value:
                    raise ValueError(f"{kind} content contains ']]>' — cannot CDATA-wrap")
                lines.append(f'\t<{kind} intID="{next_id()}"><![CDATA[')
                lines.append(value.strip("\n"))
                lines.append(f"]]></{kind}>")
            elif kind == "image":
                lines.append(f'\t<image src="pro://{html.escape(value)}" intID="{next_id()}" />')
            elif kind == "question":
                lines.append(f'\t<question id="{value}" intID="{next_id()}" />')
            else:
                raise ValueError(f"Unknown page element kind: {kind}")
        lines.append("</page>")
        lines.append("")
    lines.append("</questionnaire>")
    return "\n".join(lines) + "\n"


def create_questionnaire(
    serps: Path,
    documents: Path,
    topics_file: Path,
    query_ids: list[str],
    templates: list[Path],
    examples: Path,
    train: Path | None,
    exclude_manifests: list[Path],
    output: Path,
    name: str = "annotation",
    topic_type: str = "Dynamic",
    seed: int = 0,
) -> Path:
    """Build a SoSci questionnaire linked to a pre-imported question pool.

    `serps` is one dated SERP directory, `documents` the matching dated
    snapshot directory (its name is the snapshot date, as for `judgments
    generate`). Writes questionnaire.xml, screenshots/ and manifest.csv under
    {output}/{date}_{name}_{query_ids}/ and returns that directory.

    Page order: intro pages copied from `examples` (with the example topic),
    the optional training block, then per topic its documents in random order
    (seeded), its AI overview(s) and a rationale page. Two attention checks
    are inserted (see below) and a final page redirects to Prolific.
    `exclude_manifests` are manifests of earlier exports whose unchanged
    documents are not judged again (see resolve_query_items).
    """
    snapshot_date = documents.name
    try:
        date.fromisoformat(snapshot_date)
    except ValueError:
        raise ValueError(
            f"Cannot derive a snapshot date from {documents} — expected a dated "
            "snapshot directory like data/docs/2026-10-26."
        ) from None
    if not serps.is_dir():
        raise FileNotFoundError(f"SERP directory not found: {serps}")
    if not documents.is_dir():
        raise FileNotFoundError(f"Documents snapshot not found: {documents}")
    if not topics_file.exists():
        raise FileNotFoundError(f"Topics file not found: {topics_file}")
    for template in templates:
        if not template.exists():
            raise FileNotFoundError(f"Template XML not found: {template}")
    if not examples.exists():
        raise FileNotFoundError(f"Questionnaire example not found: {examples}")
    if train is not None and not train.exists():
        raise FileNotFoundError(f"Training XML not found: {train}")
    for manifest_path in exclude_manifests:
        if not manifest_path.exists():
            raise FileNotFoundError(f"Exclude manifest not found: {manifest_path}")

    # Prior exports were built from snapshots of the same collection, i.e.
    # sibling dated directories of `documents`.
    excluded_docs = load_excluded_documents(tuple(exclude_manifests), documents.parent)
    topics = load_topics(topics_file)
    requested_query_ids = list(dict.fromkeys(query_ids))

    pool_roots: list[ET.Element] = []
    section_sources: dict[str, Path] = {}
    for template in templates:
        tree_root = ET.parse(template).getroot()
        if tree_root.tag not in ("surveyContent", "surveyProject"):
            raise ValueError(
                f"{template}: root element is {tree_root.tag}, expected a "
                "SoSci surveyProject/surveyContent export"
            )
        for sec in tree_root.iter("section"):
            attr = sec.find("attributes.specific/attr[@id='id']")
            sec_id = attr.text if attr is not None else None
            if sec_id in section_sources:
                raise ValueError(
                    f"Section id {sec_id} appears in both {section_sources[sec_id]} and {template}"
                )
            if sec_id:
                section_sources[sec_id] = template
        pool_roots.append(tree_root)
    pool = QuestionPool(pool_roots)
    pages = extract_intro_pages(examples)
    if not pages:
        raise ValueError(f"No intro pages found in {examples}")
    # The intro explains Title/Description/Narrative and marks where the worked
    # example goes; without the marker it still gets a page of its own.
    if not insert_example_topic(pages):
        print(
            f"Warning: no {EXAMPLE_TOPIC_MARKER} marker in "
            f"{examples} — appending the example topic as its own "
            "page after the intro"
        )
        pages.append([("html", example_topic_html())])

    # The hand-written training block (worked examples with their own
    # questions and PHP feedback pages) follows the intro verbatim, framed by
    # one text page announcing the examples and one announcing the start of
    # the real task; its images live next to it and are copied into
    # screenshots/ like any other media. It sits inside the intro block, so
    # the attention check can never land among the training pages.
    train_pages: list[list[tuple[str, str]]] = []
    train_images: list[str] = []
    train_start = 0
    if train:
        train_pages = extract_pages(train)
        if not train_pages:
            raise ValueError(f"No pages in {train}")
        train_images = page_images(train_pages)
        missing = [i for i in train_images if not (train.parent / i).exists()]
        if missing:
            raise FileNotFoundError(
                f"Training images referenced by {train} not found in {train.parent}: {missing}"
            )
        pages.append([("html", train_intro_html())])
        train_start = len(pages)
        pages.extend(train_pages)
        pages.append([("html", train_outro_html())])
    n_intro_pages = len(pages)

    # Collect documents and AI overviews per set, keeping SERP order.
    manifest: list[dict] = []
    for offset, page in enumerate(train_pages, start=1):
        for kind, value in page:
            if kind == "question":
                manifest.append(
                    {
                        "page": train_start + offset,
                        "question_id": value,
                        "kind": "training",
                        "topic_id": "trec-354",
                        "topic_type": "Training",
                        "screenshot": next((v for k, v in page if k == "image"), None),
                    }
                )
    skipped: list[tuple[str, StudyItem, str]] = []
    seen_docs: set[tuple[str, str]] = set()
    document_rows: dict[tuple[str, str], dict] = {}
    rng = random.Random(seed)
    n_pdf = 0

    query_files = find_query_files(serps, tuple(requested_query_ids), ())
    items = collect_items(
        query_files,
        documents,
        snapshot_date,
        include_types=set(SOURCE_BY_RESULT_TYPE),
    )

    items_by_topic: dict[str, list] = {}
    for item in items:
        items_by_topic.setdefault(item.topic_id, []).append(item)

    # find_query_files returns (topic_id, query_id, file) sorted by topic,
    # so a topic's queries are contiguous and can be closed with one
    # rationale page each.
    for topic_id, topic_queries in groupby(query_files, key=lambda t: t[0]):
        topic = topics.get(topic_id)
        if topic is None:
            raise ValueError(f"Topic {topic_id} not in {topics_file}")
        topic_query_ids: list[str] = []
        # (pool description, page blocks without the question, manifest row)
        # — collected first so the topic's items can be shuffled before
        # question ids are handed out, keeping ids in page order.
        entries: list[tuple[str, list, dict]] = []

        for _, query_id, serp_file in topic_queries:
            topic_query_ids.append(query_id)
            serp = json.loads(serp_file.read_text())
            # Raw link counts per included SERP section, so the run log
            # can account for every document the SERP offered — the page
            # count is smaller than this whenever a URL sits in two
            # sections, was already judged for this topic, or has no
            # usable screenshot.
            section_counts = Counter(
                result_type
                for _, result_type, _ in iter_serp_links(serp)
                if result_type in SOURCE_BY_RESULT_TYPE
            )
            n_links = sum(section_counts.values())
            topic_items = [i for i in items_by_topic.get(topic_id, []) if query_id in i.query_ids]
            # collect_items keeps one item per (topic, doc_id), so a URL
            # listed in several sections of this SERP is already collapsed.
            n_duplicate = n_links - len(topic_items)
            # Deterministic pre-shuffle order, so --seed reproduces a run.
            topic_items.sort(
                key=lambda i: (
                    RESULT_TYPE_ORDER.get(i.result_type, 9),
                    i.position or 999,
                )
            )
            decisions = resolve_query_items(
                topic_id,
                query_id,
                topic_items,
                excluded_docs,
                documents,
                seen_docs,
            )
            n_repeat = sum(1 for d in decisions if d.status == "repeat")
            n_re_covered = sum(1 for d in decisions if d.status == "re_covered")
            n_re_drifted = sum(1 for d in decisions if d.status == "included" and d.re_drifted)
            n_no_screenshot = sum(1 for d in decisions if d.status == "no_screenshot")
            n_added = sum(1 for d in decisions if d.status == "included")
            # Per-item RE/mHTML notes, printed indented under this
            # query's summary line once it's known (rather than
            # scattered above it in item-processing order). Report all
            # other skips here too: the aggregate deduction alone is not
            # enough to identify which document needs attention.
            decision_notes = [
                d.note
                or (
                    f"  - {d.item.doc_id}: skipped "
                    f"({'repeat' if d.status == 'repeat' else d.item.status})"
                )
                for d in decisions
                if d.status != "included"
            ]

            for d in decisions:
                item = d.item
                if d.status == "repeat":
                    existing_row = document_rows.get((topic_id, item.docno))
                    if existing_row is None:
                        raise RuntimeError(
                            f"Internal attribution error: no manifest row for "
                            f"repeated document {topic_id}/{item.docno}"
                        )
                    if query_id not in existing_row["query_ids"]:
                        existing_row["query_ids"].append(query_id)
                if d.status != "included":
                    skip_status = (
                        "skipped_re_covered"
                        if d.status == "re_covered"
                        else "repeat"
                        if d.status == "repeat"
                        else d.item.status
                    )
                    skipped.append((query_id, d.item, skip_status))
                    continue
                pdf_path = item.pdf_path
                n_pdf += 1 if pdf_path else 0
                screenshot = f"{snapshot_date}_{item.doc_id}.png"
                row = {
                    "kind": "document",
                    "topic_id": topic_id,
                    "topic_type": topic_type,
                    "document_source": SOURCE_BY_RESULT_TYPE[item.result_type],
                    "document_id": item.doc_id,
                    "docno": item.docno,
                    "url": item.url,
                    "screenshot": screenshot,
                    # Only queries for which this document survived
                    # pair-scoped exclusion are attached. Later eligible
                    # repeats append their query id to this same row.
                    "query_ids": [query_id],
                    "serp_position": item.position,
                    "_screenshot_path": item.screenshot_path,
                    "_pdf_path": pdf_path,
                }
                document_rows[(topic_id, item.docno)] = row
                entries.append(
                    (
                        DOCUMENT_DESC,
                        [
                            ("html", topic_html(topic)),
                            ("html", website_html()),
                            ("image", screenshot),
                            ("html", section_heading_html("Questions")),
                        ],
                        row,
                    )
                )

            ai_overview = serp.get("ai_overview")
            has_ai_overview = isinstance(ai_overview, dict) and ai_overview.get("text_blocks")
            if has_ai_overview:
                entries.append(
                    (
                        AI_OVERVIEW_DESC,
                        [
                            ("html", topic_html(topic)),
                            ("html", ai_overview_html(ai_overview)),
                            ("html", section_heading_html("Questions")),
                        ],
                        {
                            "kind": "ai_overview",
                            "topic_id": topic_id,
                            "topic_type": topic_type,
                            "query_ids": [query_id],
                        },
                    )
                )

            # Account for every link the SERP offered, so a page count
            # that looks short can be traced back to the SERP instead of
            # guessed at.
            sections = (
                ", ".join(
                    f"{section_counts[t]} {t.replace('_', ' ')}"
                    for t in sorted(section_counts, key=lambda t: RESULT_TYPE_ORDER.get(t, 9))
                )
                or "no results"
            )
            deductions = "".join(
                f", -{count} {label}"
                for count, label in (
                    # Same URL twice in one section (an AI overview
                    # citing a source per text block) or across two.
                    (n_duplicate, "duplicate URL(s)"),
                    (n_repeat, "already judged for this topic"),
                    (n_re_covered, "covered by RE"),
                    (n_no_screenshot, "without screenshot"),
                )
                if count
            )
            # Not a deduction — these documents are still added, just
            # noted since they'd otherwise have been skipped as RE-covered.
            notes = f", {n_re_drifted} drifted since RE (included)" if n_re_drifted else ""
            print(
                f"{query_id} ({topic_id}): {n_links} link(s) [{sections}]"
                f"{deductions}{notes} → {n_added} document page(s)"
                f"{' + AI overview' if has_ai_overview else ''}"
            )
            for note in decision_notes:
                print(note)

        # Present the topic's documents in random order — otherwise
        # annotators see them in SERP rank order, which biases the
        # judgments. The AI overview(s) follow after all of them, so the
        # documents are judged before the overview summarising them.
        document_entries = [e for e in entries if e[2]["kind"] == "document"]
        overviews = [e for e in entries if e[2]["kind"] == "ai_overview"]
        rng.shuffle(document_entries)
        rng.shuffle(overviews)
        entries = document_entries + overviews
        for description, blocks, row in entries:
            ref = pool.alloc(description)
            pages.append(blocks + [("question", ref)])
            row["page"] = len(pages)
            row["question_id"] = ref
            manifest.append(row)

        # Close the topic with one free-text rationale, unless every one
        # of its documents was skipped.
        if not entries:
            continue
        ref = pool.alloc(RATIONALE_DESC)
        pages.append(
            [
                ("html", topic_html(topic)),
                ("html", section_heading_html("Your overall rationale")),
                ("question", ref),
            ]
        )
        manifest.append(
            {
                "page": len(pages),
                "question_id": ref,
                "kind": "rationale",
                "topic_id": topic_id,
                "topic_type": topic_type,
                "query_ids": topic_query_ids,
            }
        )

    if not any(row["kind"] != "training" for row in manifest):
        raise ValueError("No usable items — nothing to export.")

    # Two attention checks when at least two task screenshots are available:
    # one after the halfway point of the task pages, one right after the last
    # document/AI-overview page — the end of the documents, but before the
    # final topic's rationale page rather than after it — never the first
    # page, so neither can double as a participant's first real judgment.
    # With only one screenshot, retain the halfway check and omit the end
    # check. Each is otherwise an ordinary
    # document page for the topic currently being judged (the topic
    # bordering its insertion point), flagged above the topic card by
    # attention_check_html() and additionally by the question itself (own
    # attention-check pool, title states the instruction). Their screenshots
    # reuse the first and, when available, second documents shown in the
    # study (no separate files needed) — computed from the pre-insertion page
    # list so the halfway point isn't skewed by the other check's page.
    def screenshot_of(page_index: int) -> str | None:
        return next((v for k, v in pages[page_index] if k == "image"), None)

    first_screenshot = screenshot_of(n_intro_pages)
    if first_screenshot is None:
        raise ValueError(
            "The first task page has no screenshot (likely an AI overview) "
            "— cannot build an attention check from it."
        )
    second_screenshot = next(
        (s for i in range(n_intro_pages + 1, len(pages)) if (s := screenshot_of(i)) is not None),
        None,
    )
    # 1-based number of the last document/AI-overview page — i.e. the end of
    # the documents, deliberately before the final topic's rationale page
    # rather than after it.
    end_at = max(row["page"] for row in manifest if row["kind"] in ("document", "ai_overview"))
    mid_at = n_intro_pages + max(1, (len(pages) - n_intro_pages) // 2)

    def insert_attention_check(at: int, screenshot: str) -> str:
        """Insert an attention check page right after 1-based page number
        `at` (which always belongs to a real topic), reusing `screenshot`.
        Returns the allocated question ref."""
        ref = pool.alloc(ATTENTION_DESC)
        current_topic_id = next(row["topic_id"] for row in manifest if row["page"] == at)
        current_topic = topics[current_topic_id]
        pages.insert(
            at,
            [
                ("html", attention_check_html()),
                ("html", topic_html(current_topic)),
                ("html", website_html()),
                ("image", screenshot),
                ("html", section_heading_html("Questions")),
                ("question", ref),
            ],
        )
        for row in manifest:
            if row["page"] > at:
                row["page"] += 1
        manifest.append(
            {
                "page": at + 1,
                "question_id": ref,
                "kind": "attention_check",
                "topic_id": current_topic_id,
                "topic_type": "Attention",
                "screenshot": screenshot,
            }
        )
        return ref

    # Insert the end check first (at the pre-computed page-count boundary)
    # so it doesn't shift mid_at before the halfway check is inserted. When
    # the study has only one screenshot, a single halfway check is sufficient.
    ref_end = None
    if second_screenshot is not None:
        ref_end = insert_attention_check(end_at, second_screenshot)
    ref_mid = insert_attention_check(mid_at, first_screenshot)
    manifest.sort(key=lambda r: r["page"])

    # Final page: PHP-only redirect based on every attention-check answer
    # (item 4 = "Ultra elephant", the correct/passing answer), to the
    # matching Prolific completion code.
    # Must be its own page — redirect() fires as soon as the page loads,
    # before anything else on it would render.
    pages.append(
        [
            (
                "php",
                "if (\n"
                + " ||\n".join(
                    f"  (value('{ref}') != 4)" for ref in (ref_mid, ref_end) if ref is not None
                )
                + "\n) {\n"
                "  redirect('https://app.prolific.com/submissions/complete?cc=C14O5904', false);\n"
                "} else {\n"
                "  redirect('https://app.prolific.com/submissions/complete?cc=C1BGGPWU', false);\n"
                "}",
            )
        ]
    )
    manifest.append(
        {
            "page": len(pages),
            "question_id": ";".join(ref for ref in (ref_mid, ref_end) if ref),
            "kind": "redirect",
        }
    )

    # Query ids in the directory name, so an export says on sight which
    # queries it covers. Ids are shortened if enough of them are requested
    # that the full name would approach the 255-byte filename limit.
    dir_name = f"{snapshot_date}_{name}_" + "_".join(requested_query_ids)
    if len(dir_name) > 200:
        dir_name = f"{snapshot_date}_{name}_" + "_".join(q[:8] for q in requested_query_ids)
    export_dir = output / dir_name
    if export_dir.exists():
        print(f"Warning: overwriting existing export dir {export_dir}")
        shutil.rmtree(export_dir)
    screenshots_dir = export_dir / "screenshots"
    screenshots_dir.mkdir(parents=True)

    (export_dir / "questionnaire.xml").write_text(
        build_linked_questionnaire(pages), encoding="utf-8"
    )
    for row in manifest:
        if row.get("kind") != "document":
            continue
        target = screenshots_dir / row["screenshot"]
        if row["_pdf_path"]:
            render_pdf_screenshot(row["_pdf_path"], target)
        else:
            shutil.copy2(row["_screenshot_path"], target)
        # Shrink first so cropping can never eat the frame, with a little
        # headroom for the bytes the border itself adds.
        shrink_screenshot(target, max_bytes=980_000)
        add_screenshot_border(target)

    # Training images are hand-made (and may be JPEGs, which the PNG shrinker
    # cannot rewrite) — copied verbatim, with a warning if one would be
    # rejected by SoSci's 1 MB media limit.
    for image in train_images:
        target = screenshots_dir / image
        shutil.copy2(train.parent / image, target)
        if target.stat().st_size > 1_000_000:
            print(
                f"Warning: training image {image} is "
                f"{target.stat().st_size / 1e6:.1f} MB — over SoSci's 1 MB "
                "media limit; shrink it next to the training XML"
            )

    public_rows = [{k: v for k, v in row.items() if not k.startswith("_")} for row in manifest]
    fieldnames = [
        "page",
        "question_id",
        "kind",
        "topic_id",
        "topic_type",
        "document_source",
        "serp_position",
        "document_id",
        "docno",
        "url",
        "screenshot",
        "query_ids",
    ]
    with open(export_dir / "manifest.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(
            {**row, "query_ids": ";".join(row.get("query_ids", []))} for row in public_rows
        )

    n_docs = sum(1 for r in manifest if r["kind"] == "document")
    n_ao = sum(1 for r in manifest if r["kind"] == "ai_overview")
    attention_check_details = [f"page {mid_at + 1} {ref_mid}"]
    if ref_end is not None:
        attention_check_details.append(f"page {end_at + 2} {ref_end}")
    print(
        f"{len(pages)} page(s): {n_docs} document(s) ({n_pdf} rendered from PDF), "
        f"{n_ao} AI overview(s), {len(train_pages)} training page(s), "
        f"{len(attention_check_details)} attention check(s) ({', '.join(attention_check_details)}), "
        f"{len(skipped)} skipped → {export_dir}"
    )
    for query_id, item, status in skipped:
        print(f"  skipped ({status}): {item.topic_id}/{query_id}/{item.doc_id} {item.url}")
    print(
        "Next: paste questionnaire.xml into the SoSci questionnaire XML editor "
        "and upload the contents of screenshots/ to the project media area "
        "(the question pool is imported once and shared by all questionnaires)."
    )
    return export_dir
