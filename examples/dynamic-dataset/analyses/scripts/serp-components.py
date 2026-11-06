from pathlib import Path

import pandas as pd
from ir_datasets_longeval import load
from examples.analyses.scripts.utils import RESULTS_DIR, load_serp

skip = {"topic_id", "query_id", "query_idx", "query", "fetched_at",
        "search_metadata", "search_parameters", "search_information",
        "pagination", "serpapi_pagination", "top_stories_serpapi_link"}


def get_components(topic_type: str, output: Path) -> dict[str, str]:
    dataset = load(f"dynamic-dataset/{topic_type}/snapshot/IN")
    
    res = []
    observations = []
    for snapshot in dataset.get_datasets():
        for query in snapshot.queries_iter():
            serp = load_serp(query.serp_file)
            observations.append({
                "query_id": query.query_id,
                "snapshot": snapshot.snapshot,
                "serp_loaded": serp is not None,
            })
            if serp is None:
                continue
            
            for key in serp:
                if key not in skip:
                    res.append({
                        "component": key,
                        "query_id": query.query_id,
                        "topic_id": query.topic.topic_id,
                        "snapshot": snapshot.snapshot,
                    })    

    df = pd.DataFrame(res)
    df.to_csv(output, index=False)
    pd.DataFrame(observations).to_csv(
        RESULTS_DIR / f"serp-observations-{topic_type}.csv", index=False
    )

def main():
    for topic_type in ["static", "dynamic"]:
        output = RESULTS_DIR / f"serp-components-{topic_type}.csv"
        get_components(topic_type, output)

if __name__ == "__main__":
    main()
