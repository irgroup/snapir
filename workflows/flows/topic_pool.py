"""Grow the candidate pool: new debates → generated topics → generated queries.

  1. fetch new debates from Eurotopics   (snapir topics fetch-debates)
  2. generate topics for new debates     (snapir topics generate)
  3. generate queries for new topics     (snapir queries generate)

Generation is append-only and skips existing ids, so each run only processes
what is new since the last one. The pool is not scraped directly: curate it
with `snapir topics select` into the query set the daily-snapshot flow uses.

Run once locally:

    uv run --extra prefect python workflows/flows/topic_pool.py \\
        --topic-prompt data/topic-emerging-v5.yaml --query-prompt data/queries-v5.yaml

or deploy on a schedule with workflows/prefect.yaml. The LLM is configured
through the environment (SNAPIR_LLM_MODEL, SNAPIR_LLM_BASE_URL, SNAPIR_LLM_API_KEY).
"""

import argparse
from pathlib import Path

from prefect import flow, task

from snapir.queries.generation import generate_queries
from snapir.topics.debates import fetch_debates
from snapir.topics.generation import generate_topics


@task(name="fetch-debates", retries=2, retry_delay_seconds=300)
def fetch_debates_task(debates_dir: Path) -> int:
    return fetch_debates(output=debates_dir)


@task(name="generate-topics", retries=1, retry_delay_seconds=60)
def generate_topics_task(debates_file: Path, topics: Path, prompt: Path) -> int:
    return generate_topics(debates=debates_file, output=topics, prompt=prompt)


@task(name="generate-queries", retries=1, retry_delay_seconds=60)
def generate_queries_task(debates_file: Path, topics: Path, queries: Path, prompt: Path) -> int:
    return generate_queries(debates=debates_file, topics=topics, output=queries, prompt=prompt)


@flow(name="topic-pool", log_prints=True)
def topic_pool(
    topic_prompt: Path,
    query_prompt: Path,
    debates_dir: Path = Path("data/debates"),
    topics: Path = Path("data/topics.jsonl"),
    queries: Path = Path("data/queries.jsonl"),
) -> dict:
    """Fetch new debates and generate topics and queries for them."""
    debates_file = debates_dir / "debates.json"
    new_debates = fetch_debates_task(debates_dir)
    new_topics = generate_topics_task(debates_file, topics, topic_prompt)
    new_queries = generate_queries_task(debates_file, topics, queries, query_prompt)
    return {"debates": new_debates, "topics": new_topics, "queries": new_queries}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the topic-pool flow once locally.")
    parser.add_argument("--topic-prompt", type=Path, required=True)
    parser.add_argument("--query-prompt", type=Path, required=True)
    parser.add_argument("--debates-dir", type=Path, default=Path("data/debates"))
    parser.add_argument("--topics", type=Path, default=Path("data/topics.jsonl"))
    parser.add_argument("--queries", type=Path, default=Path("data/queries.jsonl"))
    args = parser.parse_args()
    topic_pool(**vars(args))
