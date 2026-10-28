from datetime import datetime, timezone
from pathlib import Path

from topic_gen.generate import Generator
from topic_gen.models import TRECTopic

from snapir.ids import record_id
from snapir.jsonl import existing_values, write_record
from snapir.llm import make_generator, model_name, prompt_name
from snapir.topics.debates import format_quotes, load_debates


def generate_topic(generator: Generator, debate: dict, dry_run: bool = False) -> TRECTopic | None:
    """Generate a single TRECTopic from a debate dict. Returns None on dry run or failure."""
    return generator.generate_one(
        item_id=debate["id"],
        dry_run=dry_run,
        title=debate["title"],
        summary=debate.get("summary", ""),
        quotes=format_quotes(debate),
    )


def generate_topics(
    debates: Path,
    output: Path,
    prompt: Path,
    debate_id: str | None = None,
    dry_run: bool = False,
) -> int:
    """Generate TRECTopics for debates (with quotes) not already in output.

    Appends results incrementally. Returns the number of debates processed
    (prompts previewed with dry_run, topics written otherwise).
    """
    records = load_debates(debates, debate_id)
    existing_ids = existing_values(output, "debate_id")
    to_process = [d for d in records if d["id"] not in existing_ids and d.get("quotes")]

    generator = make_generator(prompt, TRECTopic)
    llm = model_name()
    prompt_id = prompt_name(generator)

    if dry_run:
        for debate in to_process:
            generate_topic(generator, debate, dry_run=True)
        return len(to_process)

    output.parent.mkdir(parents=True, exist_ok=True)
    generated = 0
    with open(output, "a", encoding="utf-8") as f:
        for debate in to_process:
            topic = generate_topic(generator, debate)
            if topic is None:
                continue
            write_record(
                f,
                {
                    **topic.model_dump(),
                    "debate_id": debate["id"],
                    "debate_date": debate.get("date"),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "llm_model": llm,
                    "automatic": True,
                    "prompt": prompt_id,
                    "topic_id": record_id(llm=llm, prompt=prompt_id, debate=debate["id"]),
                },
            )
            generated += 1
    return generated
