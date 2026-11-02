from pathlib import Path

import pandas as pd
from ir_datasets_longeval import load
from examples.analyses.scripts.utils import RESULTS_DIR, load_serp


def get_components(topic_type: str, output: Path) -> None:
    dataset = load(f"dynamic-dataset/{topic_type}/snapshot/IN")
    sources = []
    observations = []
    for snapshot in dataset.get_datasets():
        for query in snapshot.queries_iter():
            identity = {
                "query_id": query.query_id,
                "topic_id": query.topic.topic_id,
                "snapshot": snapshot.snapshot,
            }
            serp = load_serp(query.serp_file)
            observation = {
                **identity,
                "serp_loaded": serp is not None,
                "ai_overview_detected": None,
                "text_extracted": None,
                "sources_extracted": None,
                "num_sources": None,
            }
            if serp is not None:
                overview = serp.get("ai_overview") or {}
                references = overview.get("references") or []
                observation.update({
                    "ai_overview_detected": "ai_overview" in serp,
                    "text_extracted": bool(overview.get("text_blocks")),
                    "sources_extracted": bool(references),
                    "num_sources": len({source["link"] for source in references}),
                })
                # Source analysis does not require successfully extracted text.
                for source in references:
                    sources.append({**identity, "source": source["link"]})
            observations.append(observation)

    pd.DataFrame(sources, columns=["source", "query_id", "topic_id", "snapshot"]).to_csv(output, index=False)
    overview_df = pd.DataFrame(observations)
    overview_df.to_csv(RESULTS_DIR / f"ai-overview-observations-{topic_type}.csv", index=False)
    detected = overview_df[overview_df["ai_overview_detected"].eq(True)]
    missing_sources = detected["sources_extracted"].eq(False).sum()
    missing_fraction = missing_sources / len(detected) if len(detected) else float("nan")
    print(f"{topic_type}: detected AI overviews: {len(detected)}, "
          f"missing sources: {missing_sources}/{len(detected)} ({missing_fraction:.2%}), "
          f"failed SERPs: {overview_df['serp_loaded'].eq(False).sum()}")


def main():
    for topic_type in ["static", "dynamic"]:
        output = RESULTS_DIR / f"ai-overview-sources-{topic_type}.csv"
        get_components(topic_type, output)


if __name__ == "__main__":
    main()
