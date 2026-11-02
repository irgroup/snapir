from ir_datasets_longeval import load
from examples.analyses.scripts.utils import load_serp, normalize_url

import pandas as pd

skip = {"topic_id", "query_id", "query_idx", "query", "fetched_at",
        "search_metadata", "search_parameters", "search_information",
        "pagination", "serpapi_pagination", "top_stories_serpapi_link"}

def main():
    topic_type = "dynamic"
    topic_id = "7b05847afd5ea83ee809cfefe593733e"
 
    
    dataset = load(f"dynamic-dataset/{topic_type}/snapshot/IN")
    
    
    res = []
    for snapshot in dataset.get_datasets():
        # if snapshot.get_snapshot() not in [0,7,14, 21]:
        #     continue
        
        for query in snapshot.queries_iter():
            line = {}
            if query.topic_id != topic_id:
                continue
            serp = load_serp(query.serp_file)
            if not serp:
                continue
            overview = serp.get("ai_overview") or {}
            references = overview.get("references") or []
            for key in serp:
                if key not in skip:
                    line[key] = True
            organic_results = serp.get("organic_results") or []
            total_results = serp["search_information"].get("total_results") 
            line.update({
                "snapshot": snapshot.get_snapshot(),
                "date": serp.get("fetched_at")[:10],
                "query_id": query.query_id,
                "topic_id": query.topic_id,
                "ai_overview_detected": "ai_overview" in serp,
                "num_references": len(references),
                "len_organic_results": len(organic_results),
                "query": query.query,
                "total_results": total_results
                        }),
            res.append(line)
            
            
        print(snapshot.get_snapshot())

    df = pd.DataFrame(res)
    
    text_change = pd.read_csv(f"examples/analyses/results/text-change-consecutive-{topic_type}.csv")
    df = df.merge(text_change, how="left", on=["snapshot", "query_id"])
    
    df.to_csv(f"serp-components-{topic_type}-{topic_id}-full.csv", index=False)
    print(pd.DataFrame(res))
    
    print()

if __name__ == "__main__":
    main()