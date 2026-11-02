from datetime import datetime, timezone
from pathlib import Path

from snapir.ids import record_id
from snapir.jsonl import existing_values, read_jsonl, write_record
from snapir.llm import make_generator, model_name, prompt_name, require_topic_gen
from snapir.topics.debates import format_quotes, load_debates


def load_topics(topics_path: Path, topic_id: str | None = None) -> list[dict]:
    if not topics_path.exists():
        raise FileNotFoundError(f"{topics_path} not found — run `snapir topics generate` first.")
    topics = read_jsonl(topics_path)
    if topic_id:
        topics = [t for t in topics if t["topic_id"] == topic_id]
        if not topics:
            raise ValueError(f"Topic '{topic_id}' not found in {topics_path}")
    return topics


def debate_context(debate: dict) -> str:
    """Debate text passed to the query prompt alongside the topic.

    The leading quote mark is carried over from the legacy pipeline; keep it
    so prompts stay identical to those the existing query sets were made with.
    """
    return f""""[Title]:
{debate["title"]}

[Summary]:
{debate["summary"]}

[Press Quotes]:
{format_quotes(debate)}"""


def generate_queries(
    debates: Path,
    topics: Path,
    output: Path,
    prompt: Path,
    topic_id: str | None = None,
    dry_run: bool = False,
) -> int:
    """Generate query sets for topics not already in output.

    Appends results incrementally. Returns the number of topics previewed
    (dry_run) or the number of queries written.
    """
    require_topic_gen()
    from pydantic import Field
    from topic_gen.models import BaseTopic

    class QuerySet(BaseTopic):
        """Five search queries generated from a TREC topic."""

        queries: list[str] = Field(description="List of exactly 5 search queries.")

    topic_records = load_topics(topics, topic_id)
    existing_ids = existing_values(output, "topic_id")
    to_process = [t for t in topic_records if t["topic_id"] not in existing_ids]

    generator = make_generator(prompt, QuerySet)
    llm = model_name()
    prompt_id = prompt_name(generator)
    contexts = {d["id"]: debate_context(d) for d in load_debates(debates)}

    def generate(topic: dict, dry_run: bool = False) -> QuerySet | None:
        return generator.generate_one(
            item_id=topic["topic_id"],
            dry_run=dry_run,
            title=topic["title"],
            description=topic["description"],
            debate=contexts[topic["debate_id"]],
        )

    if dry_run:
        for topic in to_process:
            generate(topic, dry_run=True)
        return len(to_process)

    output.parent.mkdir(parents=True, exist_ok=True)
    generated = 0
    with open(output, "a", encoding="utf-8") as f:
        for topic in to_process:
            result = generate(topic)
            if result is None:
                continue
            for query in result.queries:
                write_record(
                    f,
                    {
                        "topic_id": topic["topic_id"],
                        "query_id": record_id(
                            llm=llm, prompt=prompt_id, topic=topic["topic_id"], query=query
                        ),
                        "query": query,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "llm_model": llm,
                        "automatic": True,
                        "prompt": prompt_id,
                    },
                )
                generated += 1
    return generated
