import typer

from snapir.cli.documents import app as documents_app
from snapir.cli.export import app as export_app
from snapir.cli.judgments import app as judgments_app
from snapir.cli.queries import app as queries_app
from snapir.cli.serps import app as serps_app
from snapir.cli.topics import app as topics_app

app = typer.Typer(
    name="snapir",
    help="SnapIR: create dynamic test collections.",
    no_args_is_help=True,
)

app.add_typer(topics_app, name="topics")
app.add_typer(queries_app, name="queries")
app.add_typer(serps_app, name="serps")
app.add_typer(documents_app, name="documents")
app.add_typer(judgments_app, name="judgments")
app.add_typer(export_app, name="export")


def main() -> None:
    """Console entry point: report invalid input as a one-line error, not a traceback."""
    try:
        app()
    except (ValueError, FileNotFoundError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise SystemExit(1) from None
