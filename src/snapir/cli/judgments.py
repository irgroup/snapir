from pathlib import Path
from typing import Annotated

import typer

from snapir.judgments.generation import DEFAULT_MAX_CHARS, DEFAULT_PROMPT, generate_judgments
from snapir.judgments.questionnaire import create_questionnaire
from snapir.judgments.sosci import create_questions

app = typer.Typer(
    help="Create relevance judgments.",
    no_args_is_help=True,
)


@app.command("generate")
def generate_judgments_command(
    serps: Annotated[
        Path,
        typer.Option(
            "--serps",
            exists=True,
            file_okay=False,
            readable=True,
            help="Dated SERP directory, e.g. data/serps/2026-10-26_queries-selected.",
        ),
    ],
    documents: Annotated[
        Path,
        typer.Option(
            "--documents",
            exists=True,
            file_okay=False,
            readable=True,
            help="Dated documents snapshot directory, e.g. data/docs/2026-10-26.",
        ),
    ],
    topics: Annotated[
        Path,
        typer.Option(
            "--topics",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Topics JSONL file.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            file_okay=False,
            help="Output directory for assessments.jsonl and qrels.txt.",
        ),
    ],
    max_chars: Annotated[
        int,
        typer.Option(
            "--max-chars", min=0, help="Max document characters passed to the LLM (0 = all)."
        ),
    ] = DEFAULT_MAX_CHARS,
    query_id: Annotated[
        str | None,
        typer.Option("--query-id", help="Judge one query's documents only."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Count pending judgments without calling the LLM."),
    ] = False,
    prompt: Annotated[
        str,
        typer.Option("--prompt", help="Prompt YAML file or built-in topic-gen prompt name."),
    ] = DEFAULT_PROMPT,
):
    """Generate LLM relevance judgments."""
    count = generate_judgments(
        serps=serps,
        documents=documents,
        topics=topics,
        output=output,
        query_id=query_id,
        max_chars=max_chars,
        dry_run=dry_run,
        prompt=prompt,
    )
    if dry_run:
        typer.echo(f"{count} documents would be judged.")
    else:
        typer.echo(f"Generated {count} judgments → {output}")


@app.command("create-questions")
def create_questions_command(
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            file_okay=False,
            help="Output directory for questions-relevance.xml and questions-rationale.xml.",
        ),
    ],
    relevance: Annotated[
        int,
        typer.Option("--relevance", min=0, help="Number of document relevance questions."),
    ] = 85,
    ai_overviews: Annotated[
        int,
        typer.Option("--ai-overviews", min=0, help="Number of AI overview relevance questions."),
    ] = 10,
    rationale: Annotated[
        int,
        typer.Option("--rationale", min=0, help="Number of rationale questions."),
    ] = 10,
):
    """Create the SoSci question pool."""
    out = create_questions(relevance, ai_overviews, rationale, output)
    typer.echo(f"Created question pool → {out}")


@app.command("questionnaire")
def create_questionnaire_command(
    serps: Annotated[
        Path,
        typer.Option(
            "--serps",
            exists=True,
            file_okay=False,
            readable=True,
            help="Dated SERP directory (e.g. data/serps/2026-10-26_queries-selected).",
        ),
    ],
    documents: Annotated[
        Path,
        typer.Option(
            "--documents",
            exists=True,
            file_okay=False,
            readable=True,
            help="Dated documents snapshot directory (e.g. data/docs/2026-10-26); "
            "its name is the snapshot date.",
        ),
    ],
    topics: Annotated[
        Path,
        typer.Option(
            "--topics",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Topics JSONL file.",
        ),
    ],
    query_ids: Annotated[
        list[str],
        typer.Option(
            "--query-id",
            help="Query ID to export — expands to all documents it retrieved (repeatable).",
        ),
    ],
    templates: Annotated[
        list[Path],
        typer.Option(
            "--template",
            exists=True,
            dir_okay=False,
            readable=True,
            help="SoSci export XML(s) containing the question pool (repeatable; "
            "sections of all files are merged).",
        ),
    ],
    examples: Annotated[
        Path,
        typer.Option(
            "--questionnaire",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Working questionnaire XML; its pages before the first task page "
            "are copied verbatim as the intro.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, help="Base output directory."),
    ],
    train: Annotated[
        Path | None,
        typer.Option(
            "--train",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Training block XML, spliced in verbatim after the intro pages; its "
            "images are copied into screenshots/. Omit to build without training.",
        ),
    ] = None,
    exclude_manifests: Annotated[
        list[Path] | None,
        typer.Option(
            "--exclude-manifest",
            exists=True,
            dir_okay=False,
            readable=True,
            help="manifest.csv of a prior export (e.g. an in-flight RE study covering "
            "an earlier IN's frozen document set) — if the same URL is still ranked "
            "for the same query and its text is unchanged, it's skipped here since "
            "it's already being judged there; if the text has drifted it's included "
            "fresh instead. page.mhtml is separately diffed as a warning-only check. "
            "Repeatable.",
        ),
    ] = None,
    name: Annotated[
        str,
        typer.Option("--name", help="Export name for the output dir."),
    ] = "annotation",
    topic_type: Annotated[
        str,
        typer.Option("--topic-type", help="topic_type label written to manifest.csv."),
    ] = "Dynamic",
    seed: Annotated[
        int,
        typer.Option(
            "--seed",
            help="Seed for the within-topic page shuffle; the same seed and inputs "
            "reproduce the same questionnaire.",
        ),
    ] = 0,
):
    """Build a SoSci questionnaire, screenshots, and manifest."""
    out = create_questionnaire(
        serps,
        documents,
        topics,
        query_ids,
        templates,
        examples,
        train,
        exclude_manifests or [],
        output,
        name=name,
        topic_type=topic_type,
        seed=seed,
    )
    typer.echo(f"Created questionnaire → {out}")
