import json
from pathlib import Path
from typing import Annotated

import typer

from snapir.export.export_answers import export_answers, resolve_manifest
from snapir.export.export_qrels import AGGREGATIONS, export_qrels
from snapir.export.export_rankings import export_rankings
from snapir.export.export_snapshot import SERP_DIR, export_snapshot
from snapir.export.verify_snapshot import verify_snapshot

app = typer.Typer(
    help="Export the test collection.",
    no_args_is_help=True,
)


@app.command("rankings")
def export_rankings_command(
    serps: Annotated[
        Path,
        typer.Option(
            "--serps",
            exists=True,
            file_okay=False,
            readable=True,
            help="SERP root directory (with <YYYY-MM-DD>_<queries>/ dirs).",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", help="Destination rankings CSV file."),
    ],
):
    """Export organic rankings of all dated SERP directories as a CSV."""
    count = export_rankings(serps=serps, output=output)
    typer.echo(f"Exported {count} ranking rows → {output}")


@app.command("answers")
def export_answers_command(
    sosci: Annotated[
        str,
        typer.Option(
            "--sosci",
            envvar="SOSCI_EXPORT_URL",
            show_envvar=False,
            help="SoSci JSON export URL, or a downloaded data file (.json/.csv). "
            "Defaults to $SOSCI_EXPORT_URL.",
        ),
    ],
    manifests: Annotated[
        list[Path],
        typer.Option(
            "--manifest",
            exists=True,
            help="Questionnaire export directory (or its manifest.csv), named "
            "like the questionnaire on SoSci (QUESTNNR). Repeatable.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", help="Destination answers CSV file."),
    ],
):
    """Export SoSci answers as one row per assessor, judged item and query."""
    try:
        mapping = dict(resolve_manifest(m) for m in manifests)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--manifest") from e
    count = export_answers(sosci=sosci, manifests=mapping, output=output)
    typer.echo(f"Exported {count} answer rows → {output}")


@app.command("qrels")
def export_qrels_command(
    answers: Annotated[
        Path,
        typer.Option(
            "--answers",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Answers CSV from `export answers`, filtered to the rows to keep.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", help="Destination TREC qrels file."),
    ],
    agg: Annotated[
        str,
        typer.Option(
            "--agg",
            help=f"Aggregation over assessors: {', '.join(AGGREGATIONS)}. "
            "Majority ties fall back to the median (rounded down).",
        ),
    ] = "majority",
    min_assessors: Annotated[
        int,
        typer.Option("--min-assessors", min=1, help="Drop pairs judged by fewer assessors."),
    ] = 1,
):
    """Aggregate per-assessor grades into TREC qrels."""
    if agg not in AGGREGATIONS:
        raise typer.BadParameter(f"use one of {', '.join(AGGREGATIONS)}", param_hint="--agg")
    count = export_qrels(answers=answers, output=output, method=agg, min_assessors=min_assessors)
    typer.echo(f"Exported {count} qrels → {output}")


@app.command("snapshot")
def export_snapshot_command(
    serps: Annotated[
        Path,
        typer.Option(
            "--serps",
            exists=True,
            file_okay=False,
            help="SERP root (with <YYYY-MM-DD>_<queries>/ dirs) or one dated SERP dir.",
        ),
    ],
    documents: Annotated[
        Path,
        typer.Option(
            "--documents",
            exists=True,
            file_okay=False,
            help="Documents collection directory (with <YYYY-MM-DD>/<doc_id>/ captures).",
        ),
    ],
    topics: Annotated[
        Path,
        typer.Option("--topics", exists=True, dir_okay=False, help="Topics JSONL file."),
    ],
    queries: Annotated[
        Path,
        typer.Option("--queries", exists=True, dir_okay=False, help="Queries JSONL file."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", help="Destination collection directory."),
    ],
    qrels: Annotated[
        Path | None,
        typer.Option(
            "--qrels",
            exists=True,
            dir_okay=False,
            help="TREC qrels (e.g. from `export qrels`), split by docno date.",
        ),
    ] = None,
    date_from: Annotated[
        str | None,
        typer.Option("--from", help="First collection date (YYYY-MM-DD), inclusive."),
    ] = None,
    date_to: Annotated[
        str | None,
        typer.Option("--to", help="Last collection date (YYYY-MM-DD), inclusive."),
    ] = None,
    skip_dates: Annotated[
        list[str] | None,
        typer.Option("--skip-date", help="Collection date to leave out. Repeatable."),
    ] = None,
    captures: Annotated[
        bool,
        typer.Option("--captures", help="Also package screenshots and MHTML pages."),
    ] = False,
):
    """Assemble a distributable collection, one directory per crawl date."""
    count = export_snapshot(
        serps=serps,
        documents=documents,
        topics=topics,
        queries=queries,
        output=output,
        qrels=qrels,
        date_from=date_from,
        date_to=date_to,
        skip_dates=skip_dates,
        captures=captures,
    )
    typer.echo(f"Exported {count} snapshot date(s) → {output}")


@app.command("verify")
def verify_snapshot_command(
    queries: Annotated[
        Path,
        typer.Option("--queries", exists=True, dir_okay=False, help="Queries JSONL file."),
    ],
    serps: Annotated[
        Path,
        typer.Option(
            "--serps",
            exists=True,
            file_okay=False,
            help="Dated SERP directory, e.g. data/serps/2026-10-26_queries-selected.",
        ),
    ],
    documents: Annotated[
        Path,
        typer.Option(
            "--documents",
            exists=True,
            file_okay=False,
            help="Documents collection directory (with <YYYY-MM-DD>/<doc_id>/ captures).",
        ),
    ],
    snapshot_date: Annotated[
        str | None,
        typer.Option(
            "--date",
            help="Capture date to check (YYYY-MM-DD). Defaults to the SERP directory's date.",
        ),
    ] = None,
):
    """Check queries → SERPs → captures → text; exit 1 if incomplete."""
    if snapshot_date is None:
        m = SERP_DIR.match(serps.name)
        if not m:
            raise typer.BadParameter(
                "not a dated SERP directory (YYYY-MM-DD_*); pass --date", param_hint="--serps"
            )
        snapshot_date = m.group(1)
    snapshot_dir = documents / snapshot_date
    if not snapshot_dir.is_dir():
        raise FileNotFoundError(f"snapshot directory not found: {snapshot_dir}")

    report = verify_snapshot(queries=queries, serps=serps, snapshot_dir=snapshot_dir)
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    verdict = "complete" if report["ok"] else "INCOMPLETE: " + "; ".join(report["problems"])
    typer.echo(f"{snapshot_dir}: {verdict}", err=True)
    raise typer.Exit(0 if report["ok"] else 1)
