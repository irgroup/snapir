# SnapIR
`snapir` is a python toolkit to (repeatedly) create snapshots from web search settings. This includes topic and query creation, acquisition of rankings, web pages and (synthetic) relevance judgments, ultimately building the foundation for dynamic test collections with in time relevance judgments.  

## Getting Started
```bash
pip install git+github.com/irgroup/snapir
```


## Commands

```
SnapIR
│
├── topics
│   ├── fetch-debates       Collect source debates from Eurotopics
│   ├── generate            Generate topics from debates
│   └── select              Add a topic and its queries to a curated set
│
├── queries
│   └── generate            Generate queries for topics
│
├── serps
│   └── fetch               Fetch search results for queries
│
├── documents
│   ├── scrape              Capture documents referenced by SERPs
│   ├── extract-text        Extract missing text from saved captures (incl. PDFs)
│   ├── review              Interactively inspect screenshots
│   └── clear               Remove flagged captures so the next scrape retries them
│
├── judgments
│   ├── generate            Generate LLM relevance judgments
│   ├── create-questions    Generate the SoSci question pool
│   └── questionnaire       Build a questionnaire, screenshots, and manifest
│
└── export
    ├── rankings            Export rankings as a table
    ├── answers             Export human annotations
    ├── qrels               Aggregate human assessments into TREC qrels
    ├── snapshot            Assemble a distributable temporal collection
    └── verify              Check queries → SERPs → captures → text
```

## Configuration

Set via environment variables:

| Variable | Used by |
|---|---|
| `SNAPIR_LLM_MODEL`, `SNAPIR_LLM_BASE_URL`, `SNAPIR_LLM_API_KEY` | `topics generate`, `queries generate`, `judgments generate` (OpenAI-compatible endpoint; `gemini*` models use Google GenAI, install with `uv sync --extra gemini`) |
| `SNAPIR_LLM_TEMPERATURE` (default 1), `SNAPIR_LLM_CONCURRENCY` (default 1) | as above |
| `SERPAPI_API_KEY` | `serps fetch` |
| `BROWSER_PROFILE_DIR` (default `data/.browser_profile`), `EXTENSION_DIR` (default `data/extensions/idcac`) | `documents scrape` |
| `SOSCI_EXPORT_URL` | default for `export answers --sosci` |

## Scheduled runs

`workflows/` has example Prefect flows that grow the topic pool and take a
daily snapshot, together with a `prefect.yaml` and a Docker setup with a
server and worker. See [workflows/README.md](workflows/README.md).


## Example
```bash
uv run snapir topics fetch-debates --output data/debates

uv run snapir topics generate --debates data/debates/debates.json --output data/topics.jsonl --prompt data/topic-emerging-v5.yaml 

uv run snapir queries generate --debates data/debates/debates.json --topics data/topics.jsonl --output data/queries.jsonl --prompt data/queries-v5.yaml

uv run snapir topics select --queries data/queries.jsonl --topics data/topics.jsonl --output-topics data/topics-selected.jsonl --output-queries data/queries-selected.jsonl --topic-id 8719997c0e34fc72dd343b0ac7ea04dc

uv run snapir serps fetch --queries data/queries-selected.jsonl --query-id 4f17e83eaac64e08cce86d6db356c5a5 --output data/serps

uv run snapir documents scrape --serps data/serps/2026-10-26_queries-selected --output data/docs

uv run snapir documents extract-text --documents data/docs/2026-10-26

uv run snapir documents review --documents data/docs/2026-10-26 --serps data/serps/2026-10-26_queries-selected 

uv run snapir documents clear --documents data/docs/2026-10-26 --dry-run

uv run snapir export rankings --serps data/serps --output data/exports/rankings.csv

uv run snapir judgments generate --serps data/serps/2026-10-26_queries-selected --documents data/docs/2026-10-26 --topics data/topics-selected.jsonl --output data/judgments/llm

uv run snapir judgments create-questions --output data/sosci

uv run snapir judgments questionnaire --serps data/serps/2026-10-26_queries-selected --documents data/docs/2026-10-26 --topics data/topics-selected.jsonl --query-id 4f17e83eaac64e08cce86d6db356c5a5 --template data/sosci/questions-relevance.xml --template data/sosci/questions-rationale.xml --template data/sosci/questions-attention.xml --questionnaire data/sosci/questionaire.xml --train data/sosci/train.xml --output data/questionnaires

uv run snapir export answers --manifest data/questionnaires/T1-IN1 --output data/judgments/answers.csv

uv run snapir export qrels --answers data/judgments/answers-clean.csv --output data/judgments/qrels.txt

uv run snapir export snapshot --serps data/serps --documents data/docs --topics data/topics-selected.jsonl --queries data/queries-selected.jsonl --qrels data/judgments/qrels.txt --output data/exports/snapshot

uv run snapir export verify --queries data/queries-selected.jsonl --serps data/serps/2026-10-26_queries-selected --documents data/docs
```



## Roadmap for new features:

| Done | Task | Existing source | Notes |
|---|---|---|---|
| [ ] | Standardize configuration, paths, logging, and secrets | `src/config.py` |
| [ ] | Unify respondent eligibility rules for judgment exports | `scripts/export_results.py`, `src/results.py` |
| [ ] | Update tests, documentation, and container commands | `tests/`, `README.md`, `prefect/Dockerfile`, `prefect/manual-run.sh` | Container: `workflows/docker/`, `manual-run` replaced by `docker compose exec worker snapir ...`. Tests open. |
| [ ] | `export demographics` | `analysis/summarize-demographics.py` → `src/prolific.py` | Separate demographic retrieval/export from summary analysis. |
| [ ] | `export costs` | `analysis/summarize-spendings.py` → `src/prolific.py` | Expose project selection and actual/projected costs. |
