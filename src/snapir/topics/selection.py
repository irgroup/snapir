from pathlib import Path

from snapir.jsonl import append_jsonl, read_jsonl


def select_topic(
    topic_id: str, topics: Path, queries: Path, output_topics: Path, output_queries: Path
) -> int:
    """Append a generated topic and its queries to a curated set.

    Records already in the curated files are not re-added. Returns the number
    of newly added queries.
    """
    topic = next((t for t in read_jsonl(topics) if t["topic_id"] == topic_id), None)
    if topic is None:
        raise ValueError(f"Topic '{topic_id}' not found in {topics}.")
    target_topic_ids = {t["topic_id"] for t in read_jsonl(output_topics)}

    matching_queries = [q for q in read_jsonl(queries) if q["topic_id"] == topic_id]
    target_query_ids = {q["query_id"] for q in read_jsonl(output_queries)}
    new_queries = [q for q in matching_queries if q["query_id"] not in target_query_ids]
    skipped_queries = len(matching_queries) - len(new_queries)

    print(f"Topic: {topic['title']!r} [{topic_id}]")
    if topic_id in target_topic_ids:
        print(f"  Already in {output_topics} — topic not re-added.")
    else:
        print(f"  -> {output_topics}")

    print(f"Queries: {len(matching_queries)} found.")
    for query in new_queries:
        print(f"  + {query['query']!r}")
    if skipped_queries:
        print(f"  ({skipped_queries} already in {output_queries}, skipped)")
    if not matching_queries:
        print(f"  Warning: no queries found for topic '{topic_id}'.")

    for path in (output_topics, output_queries):
        path.parent.mkdir(parents=True, exist_ok=True)
    if topic_id not in target_topic_ids:
        append_jsonl(output_topics, [topic])
    if new_queries:
        append_jsonl(output_queries, new_queries)
    return len(new_queries)
