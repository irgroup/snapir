# Workflows

Example [Prefect](https://docs.prefect.io) flows that run the snapir pipeline
on a schedule. They call the same functions as the CLI, so every step resumes
where it left off and retries are safe.

| Flow | Steps | Typical schedule |
|---|---|---|
| `flows/topic_pool.py` (`topic-pool`) | fetch new debates → generate topics → generate queries | weekdays |
| `flows/daily_snapshot.py` (`daily-snapshot`) | fetch SERPs → scrape SERP documents → rescrape indexed documents → extract text → verify | daily, one deployment per query set |

Curating the pool (`snapir topics select`), reviewing captures, and
judgments stay manual.

The daily snapshot fails if verification finds gaps (queries without SERPs,
URLs never attempted, `ok` documents without text). A missed day cannot be
refetched, so check failed runs on the same day.

The flow files are not part of the `snapir` package, so run them from a clone
of this repository. `topic-gen` is needed for the `topic-pool` flow:

```bash
git clone https://github.com/irgroup/snapir.git && cd snapir
pip install "snapir[prefect]" "topic-gen @ git+https://github.com/irgroup/topic-gen.git"
playwright install chromium
```

## Test locally

```bash
python workflows/flows/topic_pool.py \
    --topic-prompt data/topic-emerging-v5.yaml --query-prompt data/queries-v5.yaml

python workflows/flows/daily_snapshot.py --queries data/queries-selected.jsonl --headed
```

Without `PREFECT_API_URL`, Prefect starts a temporary local server for the run.
Configuration comes from the environment, as for the CLI (see the main README).

## Deploy

`prefect.yaml` defines one `topic-pool` and one `daily-snapshot-selected`
deployment on a `snapir` process work pool. Copy the snapshot block for each
further query set, with its own `queries` and `documents` paths.
`{{ $SNAPIR_WORKFLOWS_DIR }}` and `{{ $SNAPIR_DATA_DIR }}` are filled in from
the environment when you deploy.

To deploy against any Prefect server, using a worker on this machine:

```bash
cd workflows
export SNAPIR_WORKFLOWS_DIR=$PWD SNAPIR_DATA_DIR=$PWD/../data
prefect work-pool create snapir --type process
prefect --no-prompt deploy --all
prefect worker start --pool snapir   # in the environment with the credentials
```

## Docker

`docker/` contains a self-hosted setup: a Prefect server with Postgres, plus
one worker image. The image includes snapir, Chromium, and an Xvfb virtual
display. Headed scraping (`headed: true`) passes more bot checks than
headless. VNC on port 5900 lets you watch the display or take it over.

```bash
# credentials for the worker
cat > workflows/docker/.env <<EOF
SERPAPI_API_KEY=...
SNAPIR_LLM_MODEL=...
SNAPIR_LLM_BASE_URL=...
SNAPIR_LLM_API_KEY=...
EOF

docker compose -f workflows/docker/docker-compose.yml up -d --build
docker compose -f workflows/docker/docker-compose.yml exec worker prefect --no-prompt deploy --all
```

- **Data:** goes to `SNAPIR_HOST_DATA`, by default `workflows/docker/data`. Put the prompt files and curated query sets there.
- **Extensions:** the scraper loads them from `$EXTENSION_DIR` (`/data/extensions`) if they exists.
- **Other commands:** the CLI is installed in the worker, e.g. for an interactive scrape over VNC:

  ```bash
  docker compose -f workflows/docker/docker-compose.yml exec worker \
      snapir documents scrape --serps /data/serps/<date>_queries-selected --output /data/docs --interactive
  ```

- **Parallel query sets:** add a worker per work queue (see the comment in `docker-compose.yml`). Each query set then scrapes with its own browser profile.
- **Private git dependencies:** pass `GITHUB_PAT=<token>` to `build`.
