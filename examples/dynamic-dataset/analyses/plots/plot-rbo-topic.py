import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def plot_topic(topic_type, topic_id, output=None):
    df = pd.read_csv(ROOT / "analyses/results" / f"rbo-{topic_type}.csv",
                     dtype={"topic_id": str, "query_id": str})
    matches = sorted(topic for topic in df.topic_id.unique() if topic.startswith(topic_id))
    if len(matches) != 1:
        raise ValueError(f"Expected one topic matching {topic_id!r}, found {len(matches)}")
    topic_id = matches[0]
    group = df[(df.topic_id == topic_id) & df.snapshot.between(1, 21)]
    if group.duplicated(["query_id", "snapshot"]).any():
        raise ValueError("Duplicate query–snapshot RBO values")
    query_ids = sorted(group.query_id.unique())
    if len(query_ids) != 5:
        raise ValueError(f"Expected five queries, got {len(query_ids)}")

    with plt.style.context("default"), plt.rc_context({
        "font.size": 7, "axes.titlesize": 8, "axes.labelsize": 7,
        "xtick.labelsize": 6, "ytick.labelsize": 6,
    }):
        fig, ax = plt.subplots(figsize=(8 / 2.54, 6.1 / 2.54))
        for query_id in query_ids:
            values = group[group.query_id == query_id].set_index("snapshot").rbo.reindex(range(22))
            # The CSV omits self-comparisons; the baseline ranking has RBO 1.
            values.loc[0] = 1.0
            ax.plot(values.index, values, linewidth=2)
        ax.set(xlim=(0, 21), ylim=(0, 1.025), xlabel="Snapshot", ylabel="RBO (p = 0.8)")
        ax.set_title("Ranking Similarity Relative to Snapshot 0")
        ax.set_xticks(range(0, 22, 7))
        fig.tight_layout()
        output = output or ROOT / "analyses/images" / f"rbo-topic-{topic_type}-{topic_id[:8]}.pdf"
        output.parent.mkdir(parents=True, exist_ok=True)
        for suffix in (".pdf", ".png"):
            path = output.with_suffix(suffix)
            fig.savefig(path, dpi=600, transparent=True)
            print(f"Wrote {path}")
        plt.close(fig)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic-type", choices=["dynamic", "static"], default="dynamic")
    parser.add_argument("--topic-id", default="b3d15ec8", help="Full topic ID or unique prefix")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    plot_topic(args.topic_type, args.topic_id, args.output)


if __name__ == "__main__":
    main()
