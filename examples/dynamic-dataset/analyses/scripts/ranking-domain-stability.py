"""Compare organic domain sets with snapshot 0 and the preceding snapshot."""
from urllib.parse import urlparse

import pandas as pd
from ir_datasets_longeval import load
from examples.analyses.scripts.utils import RESULTS_DIR, load_serp, normalize_url


def jaccard(reference: set, current: set) -> float:
    union = reference | current
    return len(reference & current) / len(union) if union else 0.0


def compare_domains(domains: dict, query_id: str, snapshot: int, reference: int) -> float:
    """A missing SERP at either exact snapshot is unavailable, not dissimilar."""
    current = domains.get((query_id, snapshot))
    previous = domains.get((query_id, reference))
    if current is None or previous is None:
        return float("nan")
    return jaccard(previous, current)


def calculate_domain_stability(topic_type: str):
    domains = {}
    observations = []
    dataset = load(f"dynamic-dataset/{topic_type}/snapshot/IN")
    for snapshot in dataset.get_datasets():
        day = snapshot.get_snapshot()
        for query in snapshot.queries_iter():
            serp = load_serp(query.serp_file)
            if serp is None:
                continue
            domains[(query.query_id, day)] = {
                normalize_url(urlparse(result["link"]).netloc)
                for result in serp.get("organic_results", [])
            }
            if day > 0:
                observations.append((day, query.query_id, query.topic_id))

    rows = []
    for day, query_id, topic_id in observations:
        first = compare_domains(domains, query_id, day, 0)
        rows.append({
            "snapshot": day,
            "query_id": query_id,
            "topic_id": topic_id,
            "topic_type": topic_type,
            "jaccard": first,  # Preserve the existing snapshot-0 column.
            "jaccard_similarity_first": first,
            "jaccard_similarity_previous": compare_domains(domains, query_id, day, day - 1),
        })
    columns = ["snapshot", "query_id", "topic_id", "topic_type", "jaccard",
               "jaccard_similarity_first", "jaccard_similarity_previous"]
    pd.DataFrame(rows, columns=columns).to_csv(
        RESULTS_DIR / f"ranking-domain-stability-{topic_type}.csv", index=False
    )


def main():
    for topic_type in ("static", "dynamic"):
        calculate_domain_stability(topic_type)


if __name__ == "__main__":
    main()
