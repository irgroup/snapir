import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


def main():
    df_static = pd.read_csv("examples/analyses/results/ai-overview-sources-jaccard-static.csv")
    df_dynamic = pd.read_csv("examples/analyses/results/ai-overview-sources-jaccard-dynamic.csv")
    df_static["topic_type"] = "static"
    df_dynamic["topic_type"] = "dynamic"
    df = pd.concat([df_static, df_dynamic], ignore_index=True)
    plot_jaccard_similarity(df, suffix="")
    
    df_static = pd.read_csv("examples/analyses/results/ai-overview-sources-jaccard-domain-static.csv")
    df_dynamic = pd.read_csv("examples/analyses/results/ai-overview-sources-jaccard-domain-dynamic.csv")
    df_static["topic_type"] = "static"
    df_dynamic["topic_type"] = "dynamic"
    df = pd.concat([df_static, df_dynamic], ignore_index=True)
    plot_jaccard_similarity(df, suffix="domain")


def plot_jaccard_similarity(df, suffix=""):
    df = df[df["snapshot"].between(0, 21)]
    plot_data = df.melt(
        id_vars=["snapshot", "query_id", "topic_type"],
        value_vars=["jaccard_similarity_first", "jaccard_similarity_previous"],
        var_name="jaccard_type",
        value_name="jaccard",
    ).replace(
        {
            "jaccard_similarity_first": "first",
            "jaccard_similarity_previous": "previous",
        }
    )
    sns.set_theme(style="whitegrid")
    palette = {"static": "#2a6fbb", "dynamic": "#d95f02"}
    _, ax = plt.subplots(figsize=(9, 3))
    for (topic_type, reference), group in plot_data.groupby(["topic_type", "jaccard_type"]):
        valid = group.dropna(subset=["jaccard"])
        if valid.empty:
            continue
        summary = valid.groupby("snapshot")["jaccard"].agg(
            mean="mean",
            lower=lambda values: values.quantile(0.25),
            upper=lambda values: values.quantile(0.75),
        )
        # Explicit missing days keep both lines and percentile bands disconnected.
        days = range(int(summary.index.min()), int(summary.index.max()) + 1)
        summary = summary.reindex(days)
        ax.plot(
            summary.index, summary["mean"], color=palette[topic_type],
            linestyle="-" if reference == "first" else "--",
            marker="o" if reference == "first" else "X",
            label=f"{topic_type.capitalize()} / {reference}",
        )
        ax.fill_between(
            summary.index, summary["lower"], summary["upper"],
            color=palette[topic_type], alpha=0.15,
        )
    ax.set(
        xlabel="Snapshot",
        ylabel="Jaccard similarity",
        ylim=(0, 0.8),
    )
    ax.set_xlim(0, 21)
    ax.set_xticks(range(0, 22, 7))
    ax.legend(
        ncols=4,
        loc="upper center", bbox_to_anchor=(0.5, -0.25),
        frameon=False,
    )
    plt.tight_layout()
    plt.savefig(f"examples/analyses/images/ai-overview-source-stability{suffix}.pdf", dpi=300)
    plt.close(ax.figure)


if __name__ == "__main__":
    main()
