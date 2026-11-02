import argparse
import json
import math
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

ROOT = Path(__file__).resolve().parents[2]
COLORS = sns.color_palette("colorblind", 5)


def load_metadata(topic_type):
    def records(kind):
        path = ROOT / "data" / kind / f"{kind}-{topic_type}.jsonl"
        with path.open() as stream:
            return [json.loads(line) for line in stream if line.strip()]

    titles = {str(row["topic_id"]): row["title"] for row in records("topics")}
    queries = {str(row["query_id"]): row["query"] for row in records("queries")}
    return titles, queries


def plot_overview(topic_types, output):
    panels = []
    for topic_type in topic_types:
        df = pd.read_csv(
            ROOT / "examples" / "results" / f"rbo-{topic_type}.csv",
            dtype={"topic_id": str, "query_id": str},
        )
        df = df[df.snapshot.between(1, 21)]
        if df.duplicated(["topic_id", "query_id", "snapshot"]).any():
            raise ValueError(f"Duplicate query–snapshot RBO values in {topic_type}")
        titles, queries = load_metadata(topic_type)
        for topic_id, group in df.groupby("topic_id"):
            panels.append((topic_type, topic_id, titles.get(topic_id, topic_id), queries, group))
    panels.sort(key=lambda panel: (panel[0], panel[2].casefold()))
    if not panels:
        raise ValueError("No RBO observations in snapshots 1–21")

    columns = 3
    rows = math.ceil(len(panels) / columns)
    with sns.axes_style("whitegrid"), plt.rc_context({"font.family": "DejaVu Sans"}):
        fig, axes = plt.subplots(rows, columns, figsize=(18, rows * 4.8), squeeze=False)
        fig.subplots_adjust(left=0.045, right=0.99, top=0.97, bottom=0.025,
                            hspace=1.25, wspace=0.18)
        fig.suptitle("Ranking stability · five queries per topic", fontsize=21, y=0.995, weight="bold")
        fig.text(0.5, 0.982, "RBO (p = 0.8) relative to snapshot 0 · gaps indicate missing observations",
                 ha="center", fontsize=11, color="#555555")

        for ax, (topic_type, topic_id, title, queries, group) in zip(axes.flat, panels):
            query_ids = sorted(group.query_id.unique())
            if len(query_ids) != 5:
                raise ValueError(f"Expected five queries for {topic_type}/{topic_id}, got {len(query_ids)}")
            for index, query_id in enumerate(query_ids):
                values = group[group.query_id == query_id].set_index("snapshot").rbo.reindex(range(22))
                # The CSV omits self-comparisons; identical baseline rankings have RBO 1.
                values.loc[0] = 1.0
                ax.plot(values.index, values, color=COLORS[index], linewidth=1.5,
                        marker="o", markersize=2.5, alpha=0.9)
                text = textwrap.fill(f"Q{index + 1}  {queries.get(query_id, query_id)}", width=68,
                                     subsequent_indent="     ")
                ax.text(0, -0.29 - index * 0.145, text, transform=ax.transAxes,
                        color=COLORS[index], fontsize=7.5, va="top", linespacing=1.15)
            short_id = topic_id if len(topic_id) < 12 else topic_id[:8]
            ax.set_title(textwrap.fill(title, 48), loc="left", fontsize=11, weight="bold", pad=19)
            ax.text(0, 1.025, f"{topic_type.title()} · {short_id}", transform=ax.transAxes,
                    fontsize=8, color="#666666")
            ax.set(xlim=(0, 21), ylim=(0, 1.025), xlabel="Snapshot", ylabel="RBO")
            ax.set_xticks(range(0, 22, 7))
            ax.set_yticks([0, 0.25, 0.5, 0.75, 1])
            ax.tick_params(labelsize=8)
            ax.grid(alpha=0.35)
            sns.despine(ax=ax)
        for ax in list(axes.flat)[len(panels):]:
            ax.set_visible(False)
        output.parent.mkdir(parents=True, exist_ok=True)
        for suffix in (".pdf", ".png"):
            path = output.with_suffix(suffix)
            fig.savefig(path, dpi=140, bbox_inches="tight", facecolor="white")
            print(f"Wrote {path}")
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic-type", choices=["all", "dynamic", "static"], default="all")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "examples/analyses/images/rbo-topic-overview.pdf")
    args = parser.parse_args()
    plot_overview(("dynamic", "static") if args.topic_type == "all" else (args.topic_type,), args.output)


if __name__ == "__main__":
    main()
