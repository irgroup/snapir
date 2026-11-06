# Minimal example: one topic, one snapshot

This walkthrough runs the whole SnapIR pipeline on the smallest input that still
makes sense: **one news debate → one topic → five queries → one snapshot** of
SERPs, captured pages and LLM relevance judgments, packaged as a collection.

Every step is one CLI command. [`run.sh`](run.sh) runs the same commands in
order, and [`expected-output/`](expected-output) holds the files from a real run
(made on 2026-11-06), so you can follow along without API keys.

For background on IDs, snapshots and the on-disk layout, see
[Concepts](../../docs/concepts.md).


## 0. Setup

```bash
pip install snapir "topic-gen @ git+https://github.com/irgroup/topic-gen.git"
playwright install chromium

cd examples/minimal-example
cp .env.example .env      # then fill in the keys
```

| Variable | What for |
|---|---|
| `SNAPIR_LLM_MODEL`, `SNAPIR_LLM_BASE_URL`, `SNAPIR_LLM_API_KEY` | Topic, query and judgment generation. Any OpenAI-compatible endpoint; the example uses OpenRouter |
| `SNAPIR_LLM_CONCURRENCY` | Parallel LLM calls (judging is the step that makes many) |
| `SERPAPI_API_KEY` | [SerpApi](https://serpapi.com) for Google results |

**Cost:** one SerpApi credit per query per snapshot (5 here). LLM calls: 1 topic,
1 query batch, plus one judgment per captured page with text (51 in our run).

`run.sh` loads `.env` and runs every step. All output goes to `data/` and
`collection/` in this directory:

```bash
./run.sh                 # all steps
STOP=4 ./run.sh          # only define the information need (steps 1–4)
STEP=5 ./run.sh          # resume at step 5
DEBATE_ID=366775 ./run.sh
```

The steps below explain each command. Run them from this directory.


## Part 1: Define the information need

### 1. Fetch source debates

```bash
snapir topics fetch-debates --output data/debates
```

SnapIR draws topics from [euro|topics](https://www.eurotopics.net/en/), which
summarizes how European newspapers cover a current news event. This command
stores the debates currently on the listing page (twelve in our run) in
`data/debates/debates.json`, with title, summary and press quotes. Each run
adds only new debates and logs itself in `manifest.json`.

Pick one debate. `run.sh` takes the newest one; ours was
*"US midterms: what will change for Europe?"* (`366775`).

### 2. Generate a topic

```bash
snapir topics generate --debates data/debates/debates.json \
  --prompt prompts/topic-emerging-v5.yaml \
  --output data/topics-generated.jsonl --debate-id 366775
```

An LLM turns the debate into a TREC-style topic using the prompt in [`prompts/topic-emerging-v5.yaml`](prompts/topic-emerging-v5.yaml). Add `--dry-run` to print the filled-in prompt without calling the LLM.


[`data/topics-generated.jsonl`](expected-output/data/topics-generated.jsonl):

```json
{
  "title": "US Midterms EU Policy",
  "description": "What specific European policy shifts regarding Ukraine and AI are expected following the US midterm election results?",
  "narrative": "Relevant documents must explicitly connect the results of the recent US midterm elections to tangible consequences for European Union policy, ...",
  "topic_id": "59d9670c0c727669ad58fcaf73ea241c",
  "debate_id": "366775",
  "debate_date": "2026-11-05",
  "llm_model": "qwen/qwen3.5-122b-a10b",
  "prompt": "topic-emerging-v5.yaml",
  ...
}
```


### 3. Generate queries

```bash
snapir queries generate --debates data/debates/debates.json \
  --topics data/topics-generated.jsonl --prompt prompts/queries-v5.yaml \
  --output data/queries-generated.jsonl --topic-id 59d9670c0c727669ad58fcaf73ea241c
```

The query prompt ([`prompts/queries-v5.yaml`](prompts/queries-v5.yaml)) sees the topic and the debate and writes five queries a user might type.

```json
{"topic_id": "59d9670c...", "query_id": "9c3b3412f4d18f57232677a276f02e3e",
 "query": "US midterm election impact on EU Ukraine policy", "llm_model": "qwen/qwen3.5-122b-a10b", ...}
```


### 4. Select the topic

```bash
snapir topics select --topic-id 59d9670c0c727669ad58fcaf73ea241c \
  --topics data/topics-generated.jsonl --queries data/queries-generated.jsonl \
  --output-topics data/topics-selected.jsonl --output-queries data/queries-selected.jsonl
```

The `*-generated.jsonl` files are a **pool** of candidates. Usually you generate topics every day and pick the ones worth tracking. `topics select` copies a topic and all its queries into the **curated set** (`*-selected.jsonl`). You may edit the curated files by hand, for example to reword a query. All later steps use only the curated set.

```
Topic: 'US Midterms EU Policy' [59d9670c0c727669ad58fcaf73ea241c]
  + 'US midterm election impact on EU Ukraine policy'
  + 'US AI regulation changes after midterms'
  + 'US Europe cooperation opportunities after midterms'
  + 'Trump veto power Ukraine military aid outlook'
  + 'US tech industry election funding AI policy'
```

> Have your own topics already? Skip steps 1–4 and write the two JSONL files yourself. See [your own topics](../../docs/guides/own-topics.md).



## Part 2: Capture a snapshot

A **snapshot** is everything captured on one day. Steps 5–8 make one. For a longitudinal collection, repeat them every day; see
[scheduled collection](../../docs/guides/scheduled-collection.md).

### 5. Fetch SERPs

```bash
snapir serps fetch --queries data/queries-selected.jsonl --output data/serps
```

Each query is sent to Google through SerpApi (UK locale), and the **full** response is stored, so you keep the organic results, the AI overview, top stories, related questions and so on:

```
data/serps/2026-11-06_queries-selected/      <date>_<queries file stem>
└── 59d9670c.../                             topic_id
    ├── 9c3b3412....json                     query_id: raw SerpApi response
    └── ...
```

In our run the five SERPs contained organic results (5 of 5), top stories (4), inline videos (4), an AI overview (1), related questions (1) and ads (1).


### 6. Capture the pages

```bash
snapir documents scrape --serps data/serps/2026-11-06_queries-selected --output data/docs
```

Every link in every SERP component is opened in a Chromium browser and stored as a self-contained MHTML archive plus a full-page screenshot (PDFs are kept as PDFs). URLs that `robots.txt` disallows are recorded but not loaded.

```
data/docs/
├── index.jsonl                  URL → doc_id, first_seen (every URL ever seen)
└── 2026-11-06/                  this snapshot
    └── <doc_id>/                first 16 hex chars of the URL's SHA-1
        ├── meta.json            url, fetched_at, scrape_status, title, ...
        ├── page.mhtml
        └── screenshot.png
```


`meta.json` records why pages can not be captured or are flagged:
`robots` (7: Facebook and Reuters disallow crawlers), `cloudflare` (4) and
`bot_check` (4).

Pages with problems (cookie banners, CAPTCHAs, paywalls) are **flagged**, not dropped. To check and fix them:

- `snapir documents review --documents data/docs/2026-11-06` shows the
  screenshots one by one.
- `snapir documents scrape ... --interactive` opens a visible browser and pauses
  on flagged pages so you can click through consent dialogs. The browser profile
  is kept, so later headless runs reuse those cookies.
- `snapir documents clear --documents data/docs/2026-11-06` removes captures
  flagged as blocked (Cloudflare, bot check, paywall, login, ...) so the next
  scrape retries them.

Failed pages are retried automatically when you run the command again.
[Capture quality](../../docs/guides/capture-quality.md) covers this in detail.


### 7. Extract the text

```bash
snapir documents extract-text --documents data/docs/2026-11-06
```

Adds the readable text of each page (HTML or PDF) to its `meta.json` as `text`. The relevance judgments and the exported collection use this text.


### 8. Verify the snapshot

```bash
snapir export verify --queries data/queries-selected.jsonl \
  --serps data/serps/2026-11-06_queries-selected --documents data/docs
```

Follows the chain queries → SERPs → captures → text and reports every gap: a query without a SERP, a link without a capture, an `ok` capture without text. Run it
before judging, and rerun the earlier steps until the gaps are acceptable.

```json
{
  "queries_total": 5,
  "serps_missing": [],
  "serp_urls_total": 60,
  "docs_missing": [],
  "docs_by_status": {"ok": 45, "robots": 7, "cloudflare": 4, "bot_check": 4},
  ...
  "ok": true
}
```

`"ok": true` means nothing is *missing*: every query has a SERP, every link has a `meta.json` and every `ok` capture has text. `docs_by_status` shows how many of those are usable captures.



## Part 3: Assess and package

### 9. Judge relevance

```bash
snapir judgments generate --serps data/serps/2026-11-06_queries-selected \
  --documents data/docs/2026-11-06 --topics data/topics-selected.jsonl \
  --output data/judgments/2026-11-06
```

An LLM judges every captured page against the topic, using the -DNA- prompt (zero-shot by default; see [LLM assessments](../../docs/guides/llm-assessments.md)). The prompt gets the topic's title as the query and its description and narrative as the information need. The five queries are treated as variants of the same need, so each page is judged once per topic. The command writes:

- `assessments.jsonl`: one record per topic and page with the scores and the
  IDs of the queries whose SERP links the page,
- `qrels.txt`: TREC qrels with the overall score `O`, one line for each query
  whose SERP links the page, so they match the per-query rankings exported in
  step 10.

Our run judged the 51 pages that had text: 10 highly relevant (2), 12 relevant (1) and 29 not relevant (0). Two pages appear in the SERPs of two queries, so the qrels have 53 lines. The 9 pages without text were skipped: 7 were not loaded because of `robots.txt`, and 2 are bot-check pages (step 6). Flagged captures that have text, such as a Cloudflare challenge page, are judged too, so check step 6 before trusting their labels. [`assessments.jsonl`](expected-output/data/judgments/2026-11-06/assessments.jsonl) keeps the separate scores `M` (match to the intent) and `T` (trustworthiness) next to the overall score `O`, plus the model that made the judgment.

For judgments by humans instead, SnapIR builds SoSci Survey questionnaires from the same captures; see [human assessments](../../docs/guides/human-assessments.md).


### 10. Export

```bash
snapir export rankings --serps data/serps --output data/rankings.csv

snapir export snapshot --serps data/serps --documents data/docs \
  --topics data/topics-selected.jsonl --queries data/queries-selected.jsonl \
  --qrels data/judgments/2026-11-06/qrels.txt --output collection
```

`export rankings` flattens the organic results of all SERPs into one CSV (topic, query, rank, url, timestamp) for analysis. `export snapshot` packages the collection with one directory per day:

```
collection/2026-11-06/
├── topics.jsonl.gz       topics with a SERP on that day (+ valid_from/valid_to)
├── queries.jsonl.gz
├── serps/                raw SERPs (<topic_id>/<query_id>.json.gz)
├── documents.jsonl.gz    meta.json and text of every captured page
├── run.tsv.gz            Google's organic ranking as a TREC run
└── qrels.txt             the judgments for that day
```

`run.tsv.gz` and `qrels.txt` are both keyed by `query_id` and use the same docnos. Add `--captures` to include the screenshots and MHTML files.

Our run exported 1 topic, 5 queries, 5 SERPs, 60 documents, 40 run lines and 53 qrels:



## Next: more snapshots

Run steps 5–10 again on another day (`STEP=5 ./run.sh`). The topic and query IDs stay the same, new SERPs land in a new dated directory, and pages are captured again under the new date. `export snapshot` then writes one directory per day, so you can compare how rankings, AI overviews and page content changed. The [dynamic dataset](../dynamic-dataset) shows what that looks like at scale: 29 topics followed over several weeks, with the analyses from the paper.

