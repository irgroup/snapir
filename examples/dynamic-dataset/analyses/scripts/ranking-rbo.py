import pandas as pd
from ir_datasets_longeval import load
from repro_eval.measure.document_order import RBO
from examples.analyses.scripts.utils import load_serp, normalize_url, RESULTS_DIR


def calculate_rbo(topic_type: str):
    reference_snapshot = 0
    
    # reference rankings
    references = {}
    reference_dataset = load(f"dynamic-dataset/{topic_type}/snapshot/{reference_snapshot}/IN")
    for query in reference_dataset.queries_iter():
        serp = load_serp(query.serp_file)
        if serp is None:
            continue
        ranking = serp.get("organic_results", [])
        ranking = {normalize_url(r["link"]): rank for rank, r in enumerate(ranking)}
        references[query.query_id] = ranking

    # rbo
    
    res = []
    dataset = load(f"dynamic-dataset/{topic_type}/snapshot/IN")
    for snapshot in dataset.get_datasets():
        if snapshot.snapshot == reference_snapshot:
            continue
        
        for query in snapshot.queries_iter():
            serp = load_serp(query.serp_file)
            if serp is None or query.query_id not in references:
                continue

            ranking_reference = references[query.query_id]
            ranking_new = serp.get("organic_results", [])
            # new_ranking_ids = [normalize_url(r["link"]) for r in ranking_new]
            new_ranking_ids = {normalize_url(r["link"]): rank for rank, r in enumerate(ranking_new)}

            depth = max(len(ranking_reference), len(ranking_new))
            # calculate rbo
            rbo = RBO(
                    {query.query_id: ranking_reference},
                    {query.query_id: new_ranking_ids},
                    p=0.8,
                    depth=depth,
                    pbar=False,
                    per_topic=False,
                )
            
            res.append({
                "snapshot": snapshot.get_snapshot(),
                "query_id": query.query_id,
                "topic_id": query.topic_id,
                "topic_type": topic_type,
                "rbo": rbo,
            })
    
    pd.DataFrame(res).to_csv(RESULTS_DIR / f"rbo-{topic_type}.csv", index=False)
            
            
            
def main():
    calculate_rbo("static")
    calculate_rbo("dynamic")
    
if __name__ == "__main__":
    main()
