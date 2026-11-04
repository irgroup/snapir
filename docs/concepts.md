# Concepts

The ideas the rest of the documentation relies on. The paper's §3 explains why the pipeline looks this way. This page explains how SnapIR represents it.


## Collection, snapshot, query set

- A **snapshot** is everything captured on one calendar day: the SERPs of every query and the pages they link to. The date always comes from the day the command runs ("today").
- A **collection** is a set of multiple snapshots.
- A **query set** is one queries JSONL file. Its file name (stem) names the SERP directories, so all snapshots of a query set line up.
- The **topic pool** (`topics-generated.jsonl`, `queries-generated.jsonl`) holds generated candidates. `topics select` copies the ones you choose into a **curated  set** (`topics-selected.jsonl`, `queries-selected.jsonl`).

A topic or query belongs to a snapshot exactly when it has a SERP on that day. Topics can join a collection on different days. The exported collection records each topic's `valid_from`/`valid_to`.


## Identifiers

| ID | Derived from | Example |
|---|---|---|
| `topic_id`, `query_id` | MD5 of LLM model + prompt + source text (`snapir.ids.record_id`) | `22d1456f725181d9df3fd91b6fe9bff6` |
| `doc_id` | first 16 hex chars of the URL's SHA-1 | `39e97bd58b5c54bc` |
| `docno` | `<doc_id>.<snapshot date>` | `39e97bd58b5c54bc.2026-10-26` |

IDs are deterministic. The same model and prompt applied to the same debate produce the same ID. To get new generations, change the model or the prompt, or delete the generated topic/query. Running again does not produce new ones. IDs you write yourself can be any unique strings (see [your own topics](guides/own-topics.md)).

A `docno` identifies **a URL as captured on one day**. Runs and qrels use it, so a relevance judgment always refers to the version of the page that the assessor saw.


## On-disk layout

```
data/
├── debates/debates.json                 topics fetch-debates
├── topics-generated.jsonl               topic pool (generated)
├── queries-generated.jsonl              query pool (generated)
├── topics-selected.jsonl                curated set
├── queries-selected.jsonl
├── serps/
│   └── 2026-10-26_queries-selected/     <date>_<queries stem>
│       └── <topic_id>/<query_id>.json   raw SerpApi response + topic_id, query_id, query
└── docs/                                one collection
    ├── index.jsonl                      every URL ever seen: doc_id, url, first_seen
    └── 2026-10-26/                      one snapshot
        └── <doc_id>/
            ├── meta.json                url, fetched_at, scrape_status, title, text, …
            ├── page.mhtml               self-contained page archive (page.pdf for PDFs)
            └── screenshot.png           full-page screenshot
```

The names under `data/` are a convention, not a requirement: every command takes explicit paths. Commands that work on one snapshot take the dated directory (`data/serps/2026-10-26_queries-selected`, `data/docs/2026-10-26`). `export rankings` and `export snapshot` take the roots.

SERP files keep the **full** SerpApi response, so every component is preserved: organic results, AI overview (resolved to its text and sources), top stories, related questions, and more. Scraping follows the links from all of these components, not only the organic results.


## Records

**Topic:** `topic_id`, `title`, `description`, `narrative`, plus provenance (`debate_id`, `debate_date`, `llm_model`, `prompt`, `generated_at`). The narrative is used only for relevance assessment, as in TREC.

**Query:** `topic_id`, `query_id`, `query`, plus provenance.

**Document (`meta.json`):** `url`, `fetched_at`, `scrape_status`, `title`, and, after `documents extract-text`, also `text`, `author`, `publish_date`, `language` and `text_source`.


## Resuming

Every step is append-only and skips work that is already done:

| Step | Skips |
|---|---|
| `topics generate` / `queries generate` | IDs already in the output file |
| `serps fetch` | queries that already have a SERP file in today's directory |
| `documents scrape` | URLs already captured today (captures with `error` are retried) |
| `documents extract-text` | captures that already have text (`--overwrite` to redo) |
| `judgments generate` | (topic, docno) pairs already assessed |

To recover from an interrupted run, run the same command again. Nothing is overwritten, so to redo a step, delete its output first. For captures, `documents clear` does this.


## Capture status

`scrape_status` is `ok`, `error`, `robots`, `login`, or a comma-separated list of detected problems (`cloudflare`, `bot_check`, `terms`, `paywall`). A **flagged** capture is still saved, because a paywall page can be a legitimate observation. You decide whether to keep it or retry it. See [capture quality](guides/capture-quality.md).


## Two kinds of revisit

- **New snapshot of the same queries:** `serps fetch` + `documents scrape` on another day. Rankings and pages may both have changed.
- **Rescraping known pages:** `documents scrape --rescrape` captures every URL in `index.jsonl` again, including pages that have since dropped out of the rankings. This tracks how the content of each page evolves.


## The exported collection

`export snapshot` writes one self-contained directory per day:

```
collection/2026-10-26/
├── topics.jsonl.gz       topics with a SERP that day (+ valid_from, valid_to)
├── queries.jsonl.gz
├── serps/<topic_id>/<query_id>.json.gz
├── documents.jsonl.gz    meta.json incl. text, with doc_id and docno
├── run.tsv.gz            organic ranking as a TREC run
├── qrels.txt             with --qrels: the judgments for that day's docnos
├── screenshots.tar.gz    with --captures
└── pages.tar.gz          with --captures (mHTML)
```


## Using SnapIR from Python

Each CLI command calls one plain function that you can import, for example `snapir.serps.fetch.fetch_serps`, `snapir.documents.scrape_documents.scrape_documents` or `snapir.export.export_snapshot.export_snapshot`. These functions take the same arguments as the CLI options and return a count. Some accept more than the CLI exposes, such as `run_date` on `fetch_serps` and `scrape_documents`. The [Prefect flows](../workflows/flows) use them this way.
