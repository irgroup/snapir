---
name: snapir
description: Operate the SnapIR CLI (`snapir` / `uv run snapir`) to build and grow dynamic IR test collections — fetching Eurotopics debates, generating topics and queries with an LLM, curating topics, fetching Google SERPs via SerpAPI, capturing web pages with Playwright, extracting text, LLM relevance judgments, SoSci questionnaires, human-answer export, TREC qrels, and distributable snapshots. Use this whenever the user wants to run, resume, extend, debug or check any stage of a SnapIR pipeline, take a new daily snapshot, add a topic to a collection, retry flagged/Cloudflare/paywalled captures, quality-check screenshots, prepare relevance judgments, build or hand off a SoSci questionnaire, export assessor answers, qrels or rankings, or asks "what command do I run next" in a project with `data/serps`, `data/docs`, `topics.jsonl` or `queries.jsonl` — even if they don't say "snapir" by name.
---

# SnapIR

SnapIR builds *dynamic* test collections: once added, the same queries are searched and their result pages captured repeatedly over time, so each crawl date becomes a snapshot with its own rankings, documents and judgments. Your job is to move the user's collection forward stage by stage: work out where it stands, run the next command(s) with correct paths, check what each one produced, and stop before anything that costs money, needs a human, or can't be undone.

Invoke as `uv run snapir ...` inside a uv project that depends on snapir, or plain `snapir ...` if it's pip-installed. When unsure of an option, `snapir <group> <command> --help` is authoritative and cheap — prefer it over guessing.

## The pipeline at a glance

```
topics fetch-debates ─► topics generate ─► queries generate ─► topics select   (topic pool → curated set)
                                                                     │
                                         ┌───────────────────────────┘   repeat per crawl date ▼
                                         ▼
                                   serps fetch ─► documents scrape ─► documents extract-text
                                                        │  ▲                    │
                                     documents review / clear (retry flagged)   │
                                                                                ▼
             judgments generate (LLM)   and/or   judgments questionnaire ─► (SoSci study) ─► export answers ─► export qrels
                                                                                ▼
                                       export verify · export rankings · export snapshot
```

| Stage | Command | Reads | Writes |
|---|---|---|---|
| Debates | `topics fetch-debates --output D` | Eurotopics (web) | `D/debates.json`, `D/manifest.json` |
| Topics | `topics generate --debates D/debates.json --prompt P.yaml --output topics.jsonl` | LLM | appends to topics JSONL |
| Queries | `queries generate --debates … --topics topics.jsonl --prompt P.yaml --output queries.jsonl` | LLM | appends to queries JSONL |
| Curate | `topics select --topic-id T --topics … --queries … --output-topics topics-selected.jsonl --output-queries queries-selected.jsonl` | — | appends one topic + all its queries |
| SERPs | `serps fetch --queries Q.jsonl --output data/serps` | SerpAPI ($) | `data/serps/<today>_<Q-stem>/<topic_id>/<query_id>.json` |
| Capture | `documents scrape --serps data/serps/<date>_<stem> --output data/docs` | Playwright browser | `data/docs/index.jsonl`, `data/docs/<today>/<doc_id>/{meta.json,page.mhtml,screenshot.png}` |
| Text | `documents extract-text --documents data/docs/<date>` | captures | adds `text` to each `meta.json` |
| Inspect | `documents review` (interactive), `documents clear [--dry-run]` | captures | deletes captures |
| LLM qrels | `judgments generate --serps <dated> --documents data/docs/<date> --topics … --output data/judgments/llm` | LLM | `assessments.jsonl`, `qrels.txt` |
| Human study | `judgments create-questions`, `judgments questionnaire …` | SoSci XML templates | `<output>/<date>_<name>_<query_id>/{questionnaire.xml,manifest.csv,screenshots/}` |
| Answers → qrels | `export answers --sosci URL\|file --manifest DIR --output answers.csv`, then `export qrels --answers … --output qrels.txt` | SoSci | CSV, TREC qrels |
| Check / ship | `export verify`, `export rankings`, `export snapshot` | everything | report / CSV / collection dir |

Paths above follow the project convention (`data/…`); use whatever the user's layout actually is.

## How to work

### 1. Orient before running anything

Look at what already exists so you continue the collection rather than restart it: `ls data data/serps data/docs`, `wc -l` on the JSONL files, the newest dated SERP dir and docs snapshot. Read a line or two of a JSONL file if you need IDs. Many questions ("which queries haven't been fetched today?", "how many captures were flagged?") are answered by inspecting files, no command needed — e.g. count `scrape_status` values across `data/docs/<date>/*/meta.json` with `jq` or a short Python one-liner.

Check the environment variables the next stage needs (see the table below) and say clearly which are missing rather than letting the command fail halfway.

### 2. Gate the costly and the irreversible

Run free, local, read-only, or dry-run steps directly. For the following, state what will happen (how many queries / documents / LLM calls, which directory) and get the user's go-ahead first, unless they already explicitly asked for exactly that run:

- **`serps fetch`** — each query is one paid SerpAPI call. Report the query count (or use `--query-id` for a single query) before fetching.
- **`topics generate`, `queries generate`, `judgments generate`** — LLM calls. Run with `--dry-run` first to show how many prompts / pending judgments there are, then ask.
- **`documents scrape`** — opens many pages; fine to run headless on request, but `--interactive`/`--headed` need the user at a screen, so hand those to them (suggest `! uv run snapir documents scrape … --interactive`).
- **`documents review`** — interactive keyboard UI; the user must run it themselves.
- **`documents clear`** — deletes captures. Always run `--dry-run` first and show the list.
- **`export answers`** against a live SoSci URL, and anything that overwrites a previous export directory.

Long runs (scraping hundreds of URLs, big judgment batches) belong in the background; report progress from their output rather than blocking.

### 3. Check every stage's output before moving on

Each command prints a summary — read it, and look at the files. A run that "succeeded" but produced 0 new records usually means everything was already done (resume) or the input path was wrong; tell the user which. After `documents scrape`, summarise `ok` vs. flagged vs. `error` counts. `export verify` is the end-to-end check (queries → SERPs → captures → text) and exits 1 when something is missing; run it after a capture day and before any judgments or export.

## Things that commonly go wrong

- **Dates come from "today".** `serps fetch` names its directory `<today>_<queries-stem>` and `documents scrape` writes captures to `data/docs/<today>/`. Fetch SERPs and scrape on the same day; if they straddle midnight the capture snapshot and the SERP dir have different dates, and later commands need the matching pair (`export verify --date`, and pass the dated docs dir that actually contains the captures). Commands that take "a dated directory" want `data/serps/2026-10-26_queries-selected` or `data/docs/2026-10-26`, not the root — while `export rankings` and `export snapshot` want the roots.
- **Everything resumes; nothing is overwritten.** Topic/query IDs are deterministic MD5s of model + prompt + source, and existing IDs are skipped; `serps fetch` skips query files already present in today's dir; `scrape` skips docs already captured for that date (errors are retried); `judgments generate` skips assessed pairs. So re-running is the fix for interrupted runs. To get *new* generations you need a different model or prompt, not a re-run. Changing `SNAPIR_LLM_MODEL` changes the IDs.
- **`topics select` appends** — selecting the same topic twice duplicates it; check `topics-selected.jsonl` for the `topic_id` first.
- **The queries file stem names the SERP dir.** Fetch from the curated `queries-selected.jsonl` (→ `…_queries-selected`) consistently, so all crawl dates of one collection line up.
- **Flagged captures aren't errors.** `scrape_status` is `ok`, `error`, or a comma-list of issues (`cloudflare`, `bot_check`, `terms`, `paywall`, `login`, …). Retry flow: `documents clear --documents data/docs/<date> --dry-run` → `documents clear …` → have the user run `documents scrape … --interactive` once (headed; solves consent/Cloudflare in the persistent profile at `BROWSER_PROFILE_DIR`) → later headless runs reuse that clearance. `--reason` narrows which statuses are cleared.
- **No text, no judgment.** `judgments generate` skips documents without extracted text, so run `documents extract-text` (handles PDFs too) first; `--dry-run` reports "No text" counts. The reverse also holds: flagged captures (paywall, Cloudflare, error) that *do* have text are judged too, so their labels may describe a block page. Point this out, or run the quality pass first.
- **`--rescrape`** re-captures every URL in `data/docs/index.jsonl` for today without needing SERPs — use it to track how known documents change, not to retry failures.
- **Playwright missing**: `uv run playwright install chromium`. **`topic-gen` missing** (generation/judgment commands): `uv add "topic-gen @ git+https://github.com/irgroup/topic-gen.git"`.
- Errors print as a single `Error: …` line with exit code 1 — read it literally; it's usually a wrong path.

## Configuration

| Variable | Needed by |
|---|---|
| `SNAPIR_LLM_MODEL`, `SNAPIR_LLM_BASE_URL`, `SNAPIR_LLM_API_KEY` | `topics generate`, `queries generate`, `judgments generate` (any OpenAI-compatible endpoint; models named `gemini*` use Google GenAI and need the `gemini` extra) |
| `SNAPIR_LLM_TEMPERATURE` (default 1), `SNAPIR_LLM_CONCURRENCY` (default 1) | same |
| `SERPAPI_API_KEY` | `serps fetch` |
| `BROWSER_PROFILE_DIR` (default `data/.browser_profile`), `EXTENSION_DIR` (default `data/extensions/idcac`) | `documents scrape` |
| `SOSCI_EXPORT_URL` | default for `export answers --sosci` |

Check presence with `[ -n "$VAR" ] && echo set` — never print API key values.

## Judging over time

A questionnaire is always built from one dated snapshot, but *when* it is judged is up to the study design. It can be judged right after the crawl, or the same questionnaire can be re-used later to judge the frozen documents of an earlier snapshot again. SnapIR doesn't prescribe a design. Follow the user's: which snapshots get judged, by whom, how often, and how rounds are named. Don't impose a scheme of your own.

## Recipes

Adapt paths to the user's layout; the shapes below assume one curated query file and one documents directory per query set.

### Take a snapshot

```bash
D=$(date +%F)
uv run snapir serps fetch --queries data/queries-selected.jsonl --output data/serps      # paid: confirm the query count first
uv run snapir documents scrape --serps data/serps/${D}_queries-selected --output data/docs
uv run snapir documents scrape --rescrape --output data/docs                              # optional: also re-capture previously seen documents
uv run snapir documents extract-text --documents data/docs/$D --workers 4
uv run snapir export verify --queries data/queries-selected.jsonl --serps data/serps/${D}_queries-selected --documents data/docs
```

Before fetching, check which queries earlier crawl dates actually cover (`find data/serps/<last dir> -name '*.json'` vs. the query file). If earlier days used `--query-id`, "same queries as last time" is ambiguous; ask whether to fetch that subset or the whole file, because a missed day can't be fetched later. For daily collection, the Prefect flow in `workflows/flows/daily_snapshot.py` runs these steps with the date pinned once per run.

### Curate new topics

Topic and query *generation* can run unattended (it's what `workflows/flows/topic_pool.py` schedules), but *selecting* topics is a human decision made on the user's own criteria. Generate candidates (`topics fetch-debates` → `topics generate --dry-run` → `topics generate` → `queries generate`), then present each candidate's title, description and queries in a compact list for the user and collaborators to discuss. Don't rank or filter the candidates yourself unless the user gives you criteria.

Users may edit a candidate's title, description, narrative or queries before selecting it. Then run `topics select --topic-id …` once per chosen topic (check it isn't already in the curated file). A newly selected topic has no captures yet: if the user needs its documents today, run a snapshot for its queries now (`serps fetch --query-id` per query, then scrape) instead of waiting for the next scheduled run.

### Quality pass before judging

Assessors judge what the captures show, so check them before building a questionnaire. Repeat until the user is satisfied:

1. `export verify` and a `scrape_status` count for the snapshot, as an overview.
2. The user inspects screenshots per query: `! uv run snapir documents review --documents data/docs/<date> --serps data/serps/<date>_<stem> --query-id Q` (`d` deletes a bad capture).
3. `documents clear --documents data/docs/<date> --dry-run`, then the real clear, to drop automatically flagged captures.
4. The user re-captures interactively: `! uv run snapir documents scrape --serps data/serps/<date>_<stem> --query-id Q --output data/docs --interactive`.
5. `documents extract-text` for the new captures.

Only do this on the snapshot's own date. A re-scrape on a later day writes into a *new* dated directory (`documents scrape` has no date option), which no longer matches the SERPs. To patch an earlier snapshot anyway, the logic function takes the date: `uv run python -c "from datetime import date; from pathlib import Path; from snapir.documents.scrape_documents import scrape_documents; scrape_documents(serps=Path('…'), rescrape=False, query_id=None, headed=True, interactive=True, output=Path('data/docs'), concurrency=1, run_date=date(YYYY, M, D))"`. Tell the user that those captures are from today even though they sit in the old folder.

### Build a questionnaire

One questionnaire per topic is a convenient unit. Pass the topic's query ID(s).

```bash
uv run snapir judgments create-questions --output data/sosci            # once: shared question pool
uv run snapir judgments questionnaire \
  --serps data/serps/<date>_<stem> --documents data/docs/<date> --topics data/topics-selected.jsonl \
  --query-id Q \
  --template data/sosci/questions-relevance.xml --template data/sosci/questions-rationale.xml [--template <attention-check pool>.xml] \
  --questionnaire <working questionnaire.xml> [--train <training block>.xml] \
  --output data/questionnaires
```

It writes `data/questionnaires/<date>_<name>_<query_id>/` with `questionnaire.xml`, `manifest.csv` and `screenshots/` (screenshot resizing makes it take a minute or two). If documents are already judged through another questionnaire, `--exclude-manifest <that export>/manifest.csv` (repeatable) skips each one that is still ranked for the same query with unchanged text. Drifted documents are included fresh. Use it only when the user wants to avoid double judging; whether that applies depends on their design. Keep `--seed` fixed so the build is reproducible.

`manifest.csv` maps each question ID (the answer variable) to its document. Question IDs come from the shared pool and repeat across questionnaires, so the manifest is the only key to the answers: keep it with the study results.

### Hand the questionnaire to SoSci (user's steps)

These happen in the SoSci web UI; give the user this checklist:

1. Create a new questionnaire **named exactly like the export directory**. `export answers` matches SoSci's questionnaire ID (QUESTNNR) to the manifest directory by name.
2. Paste `questionnaire.xml` into the questionnaire's *Internal code* tab.
3. Upload everything in `screenshots/` under *Images and Media Files*, with a licence or legal basis valid for the user's setting.
4. In the questionnaire settings, **turn off consecutive question numbering** (so the answer variables keep the IDs in `manifest.csv`) and turn on the back button.
5. Click through the questionnaire once as a participant before recruiting assessors.

Recruiting assessors is outside SnapIR. To judge an existing questionnaire again later, nothing has to be built: the user re-uses it in SoSci (after checking it still loads completely), and its answers are exported with the same manifest.

### Collect answers and build qrels

```bash
uv run snapir export answers --sosci "$SOSCI_EXPORT_URL" --manifest data/questionnaires/<export dir> [--manifest …] --output data/judgments/answers.csv
# filter rows (e.g. attention_passed, finished), then:
uv run snapir export qrels --answers data/judgments/answers-clean.csv --output data/judgments/qrels.txt [--agg majority --min-assessors 2]
```

Check attention-check results soon after submissions arrive (same day if possible), while failed submissions can still be returned on the recruiting platform. The `attention_checks`/`attention_passed` columns of `export answers` show them. Run the filtering step with the user; which rows to keep is their call.

### Distributable collection

`export snapshot --serps data/serps --documents data/docs --topics data/topics-selected.jsonl --queries data/queries-selected.jsonl [--qrels qrels.txt] --output data/exports/snapshot`. Add `--from/--to/--skip-date` to choose crawl dates and `--captures` to include screenshots/MHTML (much larger).

### When collection runs on a server

With scheduled runs on a remote worker (see `workflows/`), the dated SERP and document directories live there. Either run the interactive steps inside the worker (it needs a display, e.g. VNC, for `review` and `--interactive`), or sync the relevant dated directories locally (`rsync` of `serps/<date>_<stem>` and `docs/<date>/` plus `docs/index.jsonl`) before building questionnaires. Don't mix the two for one snapshot without re-syncing.

## Reporting back

End with a short status: what ran, the key counts (new records, captures ok/flagged/error, judgments made), where outputs landed, anything skipped or left for the user (and the exact command for them to run), and the natural next stage.
