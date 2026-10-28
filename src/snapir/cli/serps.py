from pathlib import Path
from typing import Annotated

import typer

from snapir.serps.fetch import fetch_serps

app = typer.Typer(
    help="Collect search results.",
    no_args_is_help=True,
)


@app.command("fetch")
def fetch_serps_command(
    queries: Annotated[
        Path,
        typer.Option(
            "--queries",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Input queries JSONL file.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            file_okay=False,
            help="SERP root directory; results go to <output>/<YYYY-MM-DD>_<queries-stem>/.",
        ),
    ],
    query_id: Annotated[
        str | None,
        typer.Option("--query-id", help="Fetch for one query only."),
    ] = None,
):
    """Fetch search results for queries."""
    count = fetch_serps(queries=queries, output=output, query_id=query_id)
    typer.echo(f"Fetched {count} SERPs → {output}")
