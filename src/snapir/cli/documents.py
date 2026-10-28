from pathlib import Path
from typing import Annotated

import typer

from snapir.documents.clear_documents import DEFAULT_REASONS, clear_documents
from snapir.documents.extract_text import extract_text
from snapir.documents.review_screenshots import review_documents
from snapir.documents.scrape_documents import scrape_documents

app = typer.Typer(
    help="Capture and manage document snapshots.",
    no_args_is_help=True,
)


@app.command("scrape")
def scrape_documents_command(
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, help="Base output directory."),
    ],
    serps: Annotated[
        Path | None,
        typer.Option(
            "--serps",
            exists=True,
            file_okay=False,
            readable=True,
            help="Path to a dated SERP results directory.",
        ),
    ] = None,
    rescrape: Annotated[
        bool,
        typer.Option(
            "--rescrape",
            help="Re-capture every document in the output index (no SERPs needed).",
        ),
    ] = False,
    query_id: Annotated[
        str | None,
        typer.Option("--query-id", help="Restrict SERP mode to a single query ID."),
    ] = None,
    headed: Annotated[
        bool,
        typer.Option("--headed", help="Run browser headed without pausing on issues."),
    ] = False,
    interactive: Annotated[
        bool,
        typer.Option(
            "--interactive",
            help="Run headed and pause on detected issues; p/r toggle per-document pauses.",
        ),
    ] = False,
    concurrency: Annotated[
        int,
        typer.Option(
            "--concurrency",
            min=1,
            help="Number of pages to scrape in parallel (ignored with --interactive).",
        ),
    ] = 8,
):
    """Capture documents referenced by SERPs."""
    count = scrape_documents(
        serps=serps,
        rescrape=rescrape,
        query_id=query_id,
        headed=headed,
        interactive=interactive,
        output=output,
        concurrency=concurrency,
    )
    typer.echo(f"Captured {count} documents → {output}")


@app.command("extract-text")
def extract_text_command(
    documents: Annotated[
        Path,
        typer.Option(
            "--documents",
            exists=True,
            file_okay=False,
            readable=True,
            help="Collection directory (all snapshots) or a dated snapshot "
            "directory, e.g. data/docs/2026-10-26",
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Count extractable documents without writing."),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Re-extract documents that already have text and replace it.",
        ),
    ] = False,
    workers: Annotated[
        int,
        typer.Option("--workers", min=1, help="Number of parallel extraction processes."),
    ] = 1,
):
    """Extract missing text from saved captures (PDF, MHTML, HTML)."""
    count = extract_text(documents=documents, dry_run=dry_run, overwrite=overwrite, workers=workers)
    verb = "Would extract" if dry_run else "Extracted"
    typer.echo(f"{verb} text for {count} documents → {documents}")


@app.command("review")
def review_documents_command(
    documents: Annotated[
        Path,
        typer.Option(
            "--documents",
            exists=True,
            file_okay=False,
            help="Dated snapshot directory, e.g. data/docs/2026-10-26",
        ),
    ],
    serps: Annotated[
        Path | None,
        typer.Option(
            "--serps",
            exists=True,
            file_okay=False,
            help="Dated SERP directory, e.g. data/serps/2026-10-26_queries-selected "
            "(required with --query-id).",
        ),
    ] = None,
    query_ids: Annotated[
        list[str] | None,
        typer.Option("--query-id", help="Only review documents of this query (repeatable)."),
    ] = None,
):
    """Interactively inspect screenshots (d deletes a capture)."""
    count = review_documents(documents=documents, serps=serps, query_ids=query_ids)
    typer.echo(f"Deleted {count} captures in {documents}")


@app.command("clear")
def clear_documents_command(
    documents: Annotated[
        Path,
        typer.Option(
            "--documents",
            exists=True,
            file_okay=False,
            help="Dated snapshot directory, e.g. data/docs/2026-10-26",
        ),
    ],
    reasons: Annotated[
        list[str] | None,
        typer.Option(
            "--reason",
            help=f"scrape_status to clear (repeatable). Default: {', '.join(DEFAULT_REASONS)}.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be deleted without deleting."),
    ] = False,
):
    """Remove captures flagged with an issue, so the next scrape retries them."""
    count = clear_documents(documents=documents, reasons=reasons, dry_run=dry_run)
    verb = "Would delete" if dry_run else "Deleted"
    typer.echo(f"{verb} {count} captures in {documents}")
