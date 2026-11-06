from urllib.parse import urlparse

import pandas as pd
from examples.analyses.scripts.utils import RESULTS_DIR


def jaccard(previous_sources: set, snapshot_sources: set) -> float:
    intersection_prev = len(previous_sources.intersection(snapshot_sources))    
    union_prev = len(previous_sources.union(snapshot_sources))

    return intersection_prev / union_prev if union_prev > 0 else 0.0

def calculate_jaccard(sources: pd.DataFrame, observations: pd.DataFrame) -> pd.DataFrame:
    """Compare with the first detected overview and exact preceding snapshot.

    Self-comparisons and comparisons without extracted sources are missing.
    """
    first_observed = observations.loc[
        observations["serp_loaded"].eq(True)
        & observations["ai_overview_detected"].eq(True)
    ].groupby("query_id")["snapshot"].min().to_dict()
    source_sets = {
        (query_id, snapshot): set(group["source"].unique())
        for (query_id, snapshot), group in sources.groupby(["query_id", "snapshot"])
    }
    available = set(
        observations.loc[
            observations["serp_loaded"].eq(True)
            & observations["ai_overview_detected"].eq(True)
            & observations["sources_extracted"].eq(True),
            ["query_id", "snapshot"],
        ].itertuples(index=False, name=None)
    )

    def similarity(query_id, snapshot, reference):
        current_key = (query_id, snapshot)
        reference_key = (query_id, reference)
        if current_key not in available or reference_key not in available:
            return float("nan")
        current_sources = source_sets.get(current_key)
        reference_sources = source_sets.get(reference_key)
        if not current_sources or not reference_sources:
            return float("nan")
        return jaccard(reference_sources, current_sources)

    rows = []
    for observation in observations.itertuples(index=False):
        if observation.snapshot == 0:
            continue
        first_snapshot = first_observed.get(observation.query_id)
        first_similarity = float("nan")
        if first_snapshot is not None and observation.snapshot > first_snapshot:
            first_similarity = similarity(
                observation.query_id, observation.snapshot, first_snapshot
            )
        rows.append({
            "query_id": observation.query_id,
            "snapshot": observation.snapshot,
            "first_observed_snapshot": first_snapshot,
            "jaccard_similarity_first": first_similarity,
            "jaccard_similarity_previous": similarity(
                observation.query_id, observation.snapshot, observation.snapshot - 1
            ),
        })
    return pd.DataFrame(rows, columns=[
        "query_id", "snapshot", "first_observed_snapshot",
        "jaccard_similarity_first", "jaccard_similarity_previous",
    ])



def main():
    for topic_type in ["static", "dynamic"]:
        sources = pd.read_csv(RESULTS_DIR / f"ai-overview-sources-{topic_type}.csv")
        observations = pd.read_csv(RESULTS_DIR / f"ai-overview-observations-{topic_type}.csv")
        
        res = calculate_jaccard(sources, observations)
        output = RESULTS_DIR / f"ai-overview-sources-jaccard-{topic_type}.csv"
        res.to_csv(output, index=False)


        sources["source"] = sources["source"].apply(lambda x: urlparse(x).netloc)
        res = calculate_jaccard(sources, observations)
        output = RESULTS_DIR / f"ai-overview-sources-jaccard-domain-{topic_type}.csv"
        res.to_csv(output, index=False)



if __name__ == "__main__":
    main()
