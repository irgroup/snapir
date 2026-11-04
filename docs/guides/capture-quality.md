# Capture quality

Assessors and analyses only see what was captured. Cookie banners, Cloudflare challenges and paywalls are common. SnapIR detects many of them, flags them, and gives you tools to retry. This guide covers getting clean captures and deciding when to stop.


## How a page is captured

`documents scrape` opens each URL in a real Chromium browser (Playwright) and saves `page.mhtml` (or `page.pdf`), a full-page `screenshot.png`, and `meta.json`. Before each page it checks `robots.txt` and waits at least 3 s per domain, longer if the site sets a `Crawl-delay`. `documents extract-text` later extracts the body text from the archive. PDFs are supported.

The browser uses a **persistent profile** (`BROWSER_PROFILE_DIR`). Cookie consent, Cloudflare clearance and logins obtained once are reused by every later run, including headless ones.


## Status values

| `scrape_status` | Meaning | What to do |
|---|---|---|
| `ok` | No problems detected | Spot-check screenshots |
| `cloudflare`, `bot_check` | Challenge page captured | Retry once interactively, then headless runs reuse the clearance |
| `terms` | Consent/terms wall captured | Retry interactively, or load a cookie-banner extension |
| `paywall` | Paywall detected | Often a real observation. Keep it or retry with a subscription in the profile |
| `login` | Redirected to a login page | Log in interactively if you have access |
| `robots` | Disallowed by `robots.txt`, not loaded | Leave it (see [responsible use](responsible-use.md)) |
| `error` | Load failed | Retried automatically by the next scrape |

Count the statuses of a snapshot:

```bash
jq -r .scrape_status data/docs/2026-10-26/*/meta.json | sort | uniq -c
```


## The quality loop

Do this **on the snapshot's day**. A capture made tomorrow belongs to tomorrow's snapshot.

```bash
D=2026-10-26; S=data/serps/${D}_queries-selected

# 1. overview: queries → SERPs → captures → text
snapir export verify --queries data/queries-selected.jsonl --serps $S --documents data/docs

# 2. look at screenshots (arrow keys to browse, d deletes a bad capture)
snapir documents review --documents data/docs/$D --serps $S --query-id <query_id>

# 3. drop flagged captures (default: cloudflare, bot_check, terms, paywall, login)
snapir documents clear --documents data/docs/$D --dry-run
snapir documents clear --documents data/docs/$D

# 4. capture again in a visible browser that pauses on every detected problem
snapir documents scrape --serps $S --output data/docs --interactive

# 5. extract text for the new captures
snapir documents extract-text --documents data/docs/$D
```

In `--interactive` mode the scraper stops when it detects a problem. Solve it in the browser window (accept cookies, pass the challenge, log in), then press Enter to retry, `s` + Enter to skip the page, or `c` + Enter to capture it as it is. `p` + Enter pauses after every page, so you can also fix problems that were not detected. `r` + Enter resumes continuous processing. `--headed` shows the browser without pausing.

Repeat until the remaining flags are acceptable. Use `--reason` with `clear` to retry only specific statuses.


## Options that change what sites see

- **Extensions.** If `EXTENSION_DIR` (default
  `data/extensions/idcac`) can contain an unpacked Chrome extension that the browser loads. 
- **`--headed`** passes more bot checks than headless. On a server, provide a virtual display (the [Docker setup](../../workflows/README.md#docker) has one).
- **`--stealth`** hides the browser automation from sites. It is off by default, and captures made with it are marked `"stealth": true` in `meta.json`. Read [responsible use](responsible-use.md) first.
- **`--ignore-robots`** loads pages that `robots.txt` disallows. Read [responsible use](responsible-use.md) first.


## Tracking page changes

`documents scrape --rescrape --output data/docs` captures **every URL in the collection's index** again for today, including pages that have since left the rankings. Run it as part of each snapshot when you want to study how page content evolves (the paper's text-change analysis). It increases scraping volume with every snapshot.
