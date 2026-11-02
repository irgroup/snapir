import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import MaxNLocator, PercentFormatter

ROOT = Path(__file__).resolve().parents[2]
TOPIC_TYPE = "dynamic"
TOPIC_ID = "7b05847afd5ea83ee809cfefe593733e"
LAST_SNAPSHOT = 21
OUTPUT = ROOT / f"examples/analyses/images/text-change-topic-{TOPIC_TYPE}-{TOPIC_ID[:8]}"


def main():
    with (ROOT / f"data/queries/queries-{TOPIC_TYPE}.jsonl").open() as stream:
        queries = [json.loads(line) for line in stream if line.strip()]
    queries = sorted((q for q in queries if q["topic_id"] == TOPIC_ID),
                     key=lambda q: q["query_id"])
    data = pd.read_csv(ROOT / f"examples/analyses/results/text-change-consecutive-{TOPIC_TYPE}.csv")
    data = data[data.query_id.isin([q["query_id"] for q in queries])]
    if len(queries) != 5 or data.duplicated(["query_id", "snapshot"]).any():
        raise ValueError("Expected five queries with unique query/snapshot observations")

    with plt.style.context("default"), plt.rc_context({
        "font.size": 7, "axes.labelsize": 7,
        "xtick.labelsize": 6, "ytick.labelsize": 6,
    }):
        fig, axes = plt.subplots(2, 1, sharex=True, figsize=(8 / 2.54, 8 / 2.54),
                                 gridspec_kw={"height_ratios": [2, 1]}, layout="constrained")
        for index, query in enumerate(queries):
            values = data[data.query_id == query["query_id"]].set_index("snapshot").reindex(
                range(1, LAST_SNAPSHOT + 1))
            for ax, column in zip(axes, ["text_change_rate", "eligible_pairs"]):
                ax.plot(values.index, values[column], color=f"C{index}", linewidth=2)
            print(f"Q{index + 1}: {query['query']}")
        axes[0].set(ylabel="Text change", ylim=(0, 1.025))
        axes[0].yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
        axes[1].set(ylabel="Eligible pairs", xlabel="Snapshot (vs. previous)",
                    xlim=(0, LAST_SNAPSHOT), ylim=(0, None))
        axes[1].yaxis.set_major_locator(MaxNLocator(integer=True, nbins=4))
        axes[1].set_xticks(range(0, LAST_SNAPSHOT + 1, 7))
        fig.align_ylabels(axes)
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        for suffix in (".pdf", ".png"):
            path = OUTPUT.with_suffix(suffix)
            fig.savefig(path, dpi=600, transparent=True)
            print(f"Wrote {path}")
        plt.close(fig)


if __name__ == "__main__":
    main()
