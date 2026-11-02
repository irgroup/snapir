"""Jaccard similarity between ranked documents and documents cited in AI overview sources."""

import pandas as pd
from ir_datasets_longeval import load
from examples.analyses.scripts.utils import RESULTS_DIR, docs_from_serp, load_serp


def calculate_jaccard_similarity(topic_type: str):
    res = []
    ai_overview_sources = pd.read_csv(RESULTS_DIR / f"ai-overview-sources-{topic_type}.csv")
    
    dataset = load(f"dynamic-dataset/{topic_type}/snapshot/IN")
    for snapshot in dataset.get_datasets():
        for query in snapshot.queries_iter():
            serp = load_serp(query.serp_file)
            if serp is None:
                continue
            ranked_docs = docs_from_serp(serp)
            
            ai_overview_docs = ai_overview_sources[(ai_overview_sources["query_id"] == query.query_id) & (ai_overview_sources["snapshot"] == snapshot.snapshot)]
            if ai_overview_docs.empty:
                # no ai overview
                continue
            ai_overview_docs = set(ai_overview_docs["source"].unique())
            jaccard_similarity = len(ranked_docs & ai_overview_docs) / len(ranked_docs | ai_overview_docs)
        
            res.append({
                "snapshot": snapshot.get_snapshot(),
                "query_id": query.query_id,
                "topic_id": query.topic_id,
                "topic_type": topic_type,
                "jaccard_similarity": jaccard_similarity,
            })

    pd.DataFrame(res).to_csv(RESULTS_DIR / f"ai-overview-sources-ranking-jaccard-{topic_type}.csv", index=False)

def main():
    calculate_jaccard_similarity("static")
    calculate_jaccard_similarity("dynamic")


if __name__ == "__main__":
    main()