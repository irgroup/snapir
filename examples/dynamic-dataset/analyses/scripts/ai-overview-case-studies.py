"""Summarize delayed, persistent AI overviews without treating missing SERPs as absent."""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "analyses/results/ai-overview-case-studies"


def records(kind):
    with (ROOT / f"data/{kind}-dynamic.jsonl").open() as stream:
        return [json.loads(line) for line in stream if line.strip()]


def main():
    data = pd.read_csv(ROOT / "analyses/results/ai-overview-observations-dynamic.csv")
    if data.duplicated(["query_id", "snapshot"]).any():
        raise ValueError("Duplicate query/snapshot observations")
    titles = {row["topic_id"]: row["title"] for row in records("topics")}
    queries = {row["query_id"]: row["query"] for row in records("queries")}
    rows = []
    for (topic, query), group in data.groupby(["topic_id", "query_id"]):
        end = int(data.loc[data.topic_id == topic, "snapshot"].max())
        full = group.set_index("snapshot").reindex(range(end + 1))
        loaded = full.serp_loaded.eq(True)
        detected = loaded & full.ai_overview_detected.eq(True)
        present = full.index[detected]
        first = int(present.min()) if len(present) else None
        post = loaded & (full.index >= first) if first is not None else loaded & False
        run = longest = 0
        for yes in detected:
            run = run + 1 if yes else 0
            longest = max(longest, run)
        rows.append({
            "topic_id": topic, "title": titles.get(topic, topic),
            "query_id": query, "query": queries.get(query, query),
            "first_detection": first, "last_snapshot": end,
            "loaded_snapshots": int(loaded.sum()),
            "missing_snapshots": int((~loaded).sum()),
            "detections": int(detected.sum()),
            "loaded_since_first": int(post.sum()),
            "rate_since_first": detected.sum() / post.sum() if post.sum() else np.nan,
            "longest_consecutive_run": longest,
            "text_extractions": int((detected & full.text_extracted.eq(True)).sum()),
            "source_extractions": int((detected & full.sources_extracted.eq(True)).sum()),
            "detections_0_21": int(detected.loc[:21].sum()),
            "timeline": "".join("?" if not ok else "+" if yes else "."
                                for ok, yes in zip(loaded, detected)),
        })
    OUT.mkdir(parents=True, exist_ok=True)
    result = pd.DataFrame(rows).sort_values(
        ["longest_consecutive_run", "rate_since_first"], ascending=False)
    result.to_csv(OUT / "query-summary.csv", index=False)

    shortlist = ["81717916", "d1e54085", "9afbbc09"]
    fig, axes = plt.subplots(3, 1, figsize=(12, 7), layout="constrained")
    cmap = ListedColormap(["#d9d9d9", "#ffffff", "#f4a261", "#287d8e"])
    for ax, prefix in zip(axes, shortlist):
        subset = result[result.topic_id.str.startswith(prefix)].sort_values("query_id")
        matrix = []
        labels = []
        for row in subset.itertuples():
            group = data[data.query_id == row.query_id].set_index("snapshot").reindex(range(row.last_snapshot + 1))
            state = np.where(~group.serp_loaded.eq(True), 0,
                     np.where(~group.ai_overview_detected.eq(True), 1,
                     np.where(group.text_extracted.eq(True) & group.sources_extracted.eq(True), 3, 2)))
            matrix.append(state)
            labels.append(row.query)
        ax.imshow(matrix, aspect="auto", cmap=cmap, vmin=0, vmax=3,
                  interpolation="nearest")
        ax.set_yticks(range(len(labels)), labels, fontsize=7)
        ax.set_xticks(range(0, len(matrix[0]), 3))
        ax.set_title(subset.title.iloc[0], loc="left", fontsize=10)
        ax.set_xlabel("Snapshot relative to topic emergence", fontsize=8)
    fig.legend(handles=[Patch(facecolor=cmap(i), edgecolor="#999999", label=label)
                        for i, label in enumerate(["Missing SERP", "No overview", "Detected; text or sources missing", "Text and sources extracted"])],
               loc="outside lower center", ncol=2, fontsize=8)
    for extension in ("png", "pdf"):
        fig.savefig(OUT / f"shortlist-timelines.{extension}", dpi=200)
    plt.close(fig)
    print(f"Wrote summaries for {len(result)} queries to {OUT}")


if __name__ == "__main__":
    main()
