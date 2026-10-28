import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://www.eurotopics.net"
DEBATES_URL = f"{BASE_URL}/en/3/debates"
CRAWL_DELAY = 2  # seconds, per robots.txt

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def get(url: str) -> BeautifulSoup:
    resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=20)
    resp.raise_for_status()
    time.sleep(CRAWL_DELAY)
    return BeautifulSoup(resp.text, "html.parser")


def load_debates(debates_file: Path, debate_id: str | None = None) -> list[dict]:
    """Debates from a debates.json written by fetch_debates, optionally one."""
    with open(debates_file) as f:
        debates = json.load(f)
    if debate_id:
        debates = [d for d in debates if d["id"] == debate_id]
        if not debates:
            raise ValueError(f"Debate '{debate_id}' not found in {debates_file}")
    return debates


def format_quotes(debate: dict) -> str:
    """A debate's press quotes as prompt text, one "[source] title" block each."""
    lines = []
    for q in debate.get("quotes", []):
        header = f"[{q['source_name']}]"
        if q.get("quote_title"):
            header += f" {q['quote_title']}"
        text = (q.get("text") or "").strip()
        lines.append(f"{header}\n{text}")
    return "\n\n".join(lines)


def load_existing(output: Path) -> dict[str, dict]:
    if (output / "debates.json").exists():
        with open(output / "debates.json") as f:
            records = json.load(f)
        return {r["id"]: r for r in records}
    return {}


def save_debates(output: Path, debates: dict[str, dict]) -> None:
    records = sorted(debates.values(), key=lambda r: r["date"], reverse=True)
    with open(output / "debates.json", "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def load_manifest(output: Path) -> list[dict]:
    manifest_file = output / "manifest.json"
    if manifest_file.exists():
        with open(manifest_file) as f:
            return json.load(f)
    return []


def save_manifest(output: Path, manifest: list[dict]) -> None:
    with open(output / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def fetch_listing() -> list[dict]:
    """Scrape the debates listing page and return debate stubs."""
    soup = get(DEBATES_URL)
    debates = []
    for article in soup.find_all("article"):
        link_tag = article.find("a", href=True)
        if not link_tag:
            continue
        href = link_tag["href"]
        if href.startswith("/kurz/"):
            continue
        url = BASE_URL + href if href.startswith("/") else href
        parts = href.strip("/").split("/")
        debate_id = parts[1] if len(parts) >= 3 else href

        title_tag = article.find("h2")
        date_tag = article.find("time")
        body_div = article.find("div", class_="field-item line-clamp even") or article.find(
            "div", class_="field-item even"
        )
        debates.append(
            {
                "id": debate_id,
                "title": title_tag.get_text(strip=True) if title_tag else None,
                "date": date_tag["datetime"] if date_tag else None,
                "url": url,
                "summary": body_div.get_text(strip=True) if body_div else None,
            }
        )
    return debates


def parse_quote_panel(panel) -> dict:
    """Extract all fields from a single quote panel."""
    source_a = panel.find("a", class_="media-source-inner")
    svg = panel.find("svg", class_="flag-icon")
    pub_time = panel.find("time", attrs={"pubdate": True})
    title_tag = panel.find("h2", class_="panel-title")
    text_div = panel.find("div", class_="text-preview")
    author_div = panel.find("div", class_="author")
    author_a = panel.find("a", itemprop="url", rel="author")
    orig_link = panel.find("a", class_="article-link")

    # Country code from SVG classes: ['flag-icon', 'IT'] → 'IT'
    country_code = None
    if svg:
        classes = svg.get("class", [])
        country_code = next((c for c in classes if c != "flag-icon"), None)

    # Source name: strip the date span text from the link text
    source_name = None
    if source_a:
        date_span = source_a.find("span", class_="publication-date")
        if date_span:
            date_span.extract()
        source_name = source_a.get_text(strip=True)

    # Quote text: full text of the text-preview div (intro sentence + blockquote)
    quote_text = text_div.get_text(strip=True) if text_div else None

    # Author page URL on eurotopics
    author_page = (BASE_URL + author_a["href"]) if author_a and author_a.get("href") else None

    return {
        "source_name": source_name,
        "source_country": country_code,
        "source_url": (BASE_URL + source_a["href"]) if source_a and source_a.get("href") else None,
        "pub_date": pub_time["datetime"] if pub_time else None,
        "quote_title": title_tag.get_text(strip=True) if title_tag else None,
        "author": author_div.get_text(strip=True) if author_div else None,
        "author_url": author_page,
        "original_article_url": orig_link["href"] if orig_link else None,
        "text": quote_text,
    }


def fetch_detail(debate: dict) -> dict:
    """Fetch a debate detail page and enrich the debate dict with full content."""
    soup = get(debate["url"])

    # Structured metadata from JSON-LD
    ld_tag = soup.find("script", type="application/ld+json")
    published_at = modified_at = keywords = None
    if ld_tag:
        try:
            ld = json.loads(ld_tag.string)
            published_at = ld.get("datePublished")
            modified_at = ld.get("dateModified")
            kw = ld.get("keywords", "")
            keywords = [k.strip() for k in kw.split(",") if k.strip()] if kw else []
        except json.JSONDecodeError:
            pass

    # Quote panels
    panels = soup.find_all("div", class_="panel")
    quotes = [parse_quote_panel(p) for p in panels if p.find("h2", class_="panel-title")]

    return {
        **debate,
        "published_at": published_at,
        "modified_at": modified_at,
        "keywords": keywords,
        "quotes": quotes,
    }


def fetch_debates(output: Path) -> int:
    """Scrape the Eurotopics debates listing into output/debates.json.

    Only debates not stored yet (or stored without detail) are fetched; each
    run is logged in output/manifest.json. Returns the number of new debates.
    """
    output.mkdir(parents=True, exist_ok=True)
    scraped_at = datetime.now(timezone.utc).isoformat()

    print(f"Fetching listing: {DEBATES_URL} ...")
    listing = fetch_listing()
    print(f"Found {len(listing)} debates on listing page.")

    existing = load_existing(output)

    # Debates whose detail page hasn't been fetched yet
    new_stubs = [d for d in listing if d["id"] not in existing]
    # Debates already stored but missing full detail (e.g. from a previous listing-only run)
    needs_detail = [
        existing[d["id"]]
        for d in listing
        if d["id"] in existing and "quotes" not in existing[d["id"]]
    ]

    to_fetch = new_stubs + needs_detail
    print(
        f"Fetching detail pages for {len(to_fetch)} debate(s) ({len(new_stubs)} new, {len(needs_detail)} missing detail)..."
    )

    for i, debate in enumerate(to_fetch, 1):
        print(f"  [{i}/{len(to_fetch)}] {debate['title']} ({debate['id']})")
        try:
            enriched = fetch_detail(debate)
            enriched["scraped_at"] = scraped_at
            existing[debate["id"]] = enriched
        except httpx.HTTPError as e:
            print(f"    WARNING: failed to fetch detail — {e}")

    save_debates(output, existing)
    print(f"Done. Total stored: {len(existing)} debate(s).")

    manifest = load_manifest(output)
    manifest.append(
        {
            "scraped_at": scraped_at,
            "debates_on_page": len(listing),
            "new_debates": len(new_stubs),
            "detail_pages_fetched": len(to_fetch),
            "total_stored": len(existing),
            "new_ids": [d["id"] for d in new_stubs],
        }
    )
    save_manifest(output, manifest)
    return len(new_stubs)
