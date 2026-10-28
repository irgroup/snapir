from pathlib import Path
from typing import Annotated

import typer

from snapir.queries.generation import generate_queries

app = typer.Typer(
    help="Generate queries.",
    no_args_is_help=True,
)


@app.command("generate")
def generate_queries_command(
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
    topics: Annotated[
        Path,
        typer.Option(
            "--topics",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Input topics JSONL file.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", dir_okay=False, help="Destination queries JSONL file."),
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
    topic_id: Annotated[
        str | None,
        typer.Option("--topic-id", help="Generate for one topic only."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview prompts without calling the LLM."),
    ] = False,
):
    """Generate retrieval queries from topics."""
    count = generate_queries(
        debates=debates,
        topics=topics,
        output=output,
        prompt=prompt,
        topic_id=topic_id,
        dry_run=dry_run,
    )
    if dry_run:
        typer.echo(f"Previewed {count} query-generation prompts.")
    else:
        typer.echo(f"Generated {count} queries → {output}")
