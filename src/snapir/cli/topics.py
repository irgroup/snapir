from pathlib import Path
from typing import Annotated

import typer

from snapir.topics.debates import fetch_debates
from snapir.topics.generation import generate_topics
from snapir.topics.selection import select_topic

app = typer.Typer(
    help="Collect, generate, and select topics.",
    no_args_is_help=True,
)


@app.command("fetch-debates")
def fetch_debates_command(
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            file_okay=False,
            help="Output directory for debates.json and manifest.json.",
        ),
    ],
):
    """Collect source debates from Eurotopics."""
    count = fetch_debates(output=output)
    typer.echo(f"Fetched {count} new debates → {output / 'debates.json'}")


@app.command("generate")
def generate_topics_command(
    debates: Annotated[
        Path,
        typer.Option(
            "--debates",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Input debates.json from `topics fetch-debates`.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", dir_okay=False, help="Destination topics JSONL file."),
    ],
    prompt: Annotated[
        Path,
        typer.Option(
            "--prompt",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Prompt YAML file.",
        ),
    ],
    debate_id: Annotated[
        str | None,
        typer.Option("--debate-id", help="Generate for one debate only."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview prompts without calling the LLM."),
    ] = False,
):
    """Generate retrieval topics from debates."""
    count = generate_topics(
        debates=debates,
        output=output,
        prompt=prompt,
        debate_id=debate_id,
        dry_run=dry_run,
    )
    if dry_run:
        typer.echo(f"Previewed {count} topic-generation prompts.")
    else:
        typer.echo(f"Generated {count} topics → {output}")


@app.command("select")
def select_topic_command(
    topic_id: Annotated[
        str,
        typer.Option("--topic-id", help="Topic to add to the curated set."),
    ],
    topics: Annotated[
        Path,
        typer.Option(
            "--topics",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Generated topics JSONL file.",
        ),
    ],
    queries: Annotated[
        Path,
        typer.Option(
            "--queries",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Generated queries JSONL file.",
        ),
    ],
    output_topics: Annotated[
        Path,
        typer.Option(
            "--output-topics",
            dir_okay=False,
            help="Curated topics JSONL file (created or appended to).",
        ),
    ],
    output_queries: Annotated[
        Path,
        typer.Option(
            "--output-queries",
            dir_okay=False,
            help="Curated queries JSONL file (created or appended to).",
        ),
    ],
):
    """Add a topic and its queries to a curated set."""
    count = select_topic(
        topic_id=topic_id,
        topics=topics,
        queries=queries,
        output_topics=output_topics,
        output_queries=output_queries,
    )
    typer.echo(
        f"Topic {topic_id} selected ({count} new queries) → {output_topics}, {output_queries}"
    )
