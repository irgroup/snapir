import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import PercentFormatter

REQUIRED_COLUMNS = {
    "query_id",
    "snapshot",
    "topic_type",
    "novelty_rate",
    "churn_rate",
}
MEASURE_LABELS = {
    "novelty_rate": "Creation",
    "churn_rate": "Deletion",
}
TOPIC_LABELS = {"dynamic": "Dynamic Topics", "static": "Static Topics"}


def contiguous_day_segments(days: list[int]) -> list[list[int]]:
    """Split observed snapshot days so lines do not bridge missing days."""
    segments: list[list[int]] = []
    for day in sorted(days):
        if not segments or day != segments[-1][-1] + 1:
            segments.append([day])
        else:
            segments[-1].append(day)
    return segments

def main() -> None:
    """Write mean query change rates with 25th–75th percentile bands."""

    df = pd.read_csv("examples/dynamic-dataset/analyses/results/change-rates-dynamic.csv")
    df["topic_type"] = "dynamic"
    
    df_static = pd.read_csv("examples/dynamic-dataset/analyses/results/change-rates-static.csv")
    df_static["topic_type"] = "static"
    df = pd.concat([df, df_static], ignore_index=True)
    
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            f"Missing required columns: {', '.join(sorted(missing))}"
        )

    df = df[df["snapshot"].between(0, 21)].copy()

    plot_data = df.melt(
        id_vars=["snapshot", "topic_type"],
        value_vars=list(MEASURE_LABELS),
        var_name="measure",
        value_name="change_rate",
    )
    plot_data["measure"] = plot_data["measure"].map(MEASURE_LABELS)
    plot_data = plot_data[plot_data["topic_type"].isin(TOPIC_LABELS)]
    if plot_data.empty:
        raise ValueError("No static or dynamic topic rows in snapshots 0–21")
    print(plot_data.groupby(["snapshot", "topic_type", "measure"]).mean().unstack().to_markdown())
    
    sns.set_theme(style="whitegrid", context="paper", rc={
        "font.size": 7, "axes.titlesize": 8, "axes.labelsize": 7,
        "xtick.labelsize": 6, "ytick.labelsize": 6,
        "lines.linewidth": 2, "lines.markersize": 2.5,
    })
    fig, ax = plt.subplots(
        figsize=(6.1 / 2.54, 6.1 / 2.54), layout="constrained"
    )
    colors = dict(zip(("static", "dynamic"), sns.color_palette(n_colors=2)))
    markers = {"Creation": "+", "Deletion": "_"}
    for topic_type in ("static", "dynamic"):
        topic_data = plot_data[plot_data["topic_type"] == topic_type]
        for measure in MEASURE_LABELS.values():
            values = topic_data[topic_data["measure"] == measure]
            summary = values.groupby("snapshot")["change_rate"].agg(
                mean="mean", lower=lambda x: x.quantile(0.25),
                upper=lambda x: x.quantile(0.75),
            )
            segments = contiguous_day_segments(summary.index.tolist())
            for segment_index, segment in enumerate(segments):
                observed = summary.loc[segment]
                ax.plot(
                    observed.index, observed["mean"], color=colors[topic_type],
                    linestyle="None", marker=markers[measure], markersize=4,
                    label=f"{measure} {topic_type.capitalize()}"
                    if segment_index == 0 else None,
                )
                ax.fill_between(
                    observed.index, observed["lower"], observed["upper"],
                    color=colors[topic_type], alpha=0.10, linewidth=0,
                )
    ax.set_xlabel("Snapshot")
    ax.set_ylabel("Proportion")
    ax.set_ylim(0, 1.05)
    ax.set_xlim(0, 21)
    ax.set_xticks(range(0, 22, 7))
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
    handles = [
        Patch(facecolor=colors[topic], label=TOPIC_LABELS[topic])
        for topic in ("static", "dynamic")
    ] + [
        Line2D([], [], color="0.5", linestyle="None", marker=markers[measure],
               markersize=4, label=measure)
        for measure in ("Creation", "Deletion")
    ]
    ax.legend(
        handles=handles,
        loc="upper center", bbox_to_anchor=(0.5, -0.19), ncols=2,
        frameon=False, fontsize=7,
        handlelength=1, handletextpad=0.3, columnspacing=0.8,
        labelspacing=0.15, borderaxespad=0,
    )

    output =  "examples/dynamic-dataset/analyses/images/document-change-rates-by-snapshot.pdf"
    fig.savefig(output, dpi=300)
    fig.savefig("examples/dynamic-dataset/analyses/images/document-change-rates-by-snapshot.png", dpi=200)
    plt.close(fig)
    print(f"wrote {output}")
    

if __name__ == "__main__":
    main()
