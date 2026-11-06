import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


def main():
    df_static = pd.read_csv("examples/dynamic-dataset/analyses/results/ranking-domain-stability-static.csv")
    df_dynamic = pd.read_csv("examples/dynamic-dataset/analyses/results/ranking-domain-stability-dynamic.csv")
    df_static["topic_type"] = "static"
    df_dynamic["topic_type"] = "dynamic"
    df = pd.concat([df_static, df_dynamic], ignore_index=True)
    plot_jaccard_similarity(df)

def plot_jaccard_similarity(df):
    required_columns = {"snapshot", "topic_type", "jaccard_similarity_first", "jaccard_similarity_previous"}
    missing_columns = required_columns - set(df.columns)
    if missing_columns:
        raise ValueError(
            f"Missing required columns: {', '.join(sorted(missing_columns))}"
        )

    plot_data = df.loc[df["snapshot"].between(1, 21), [
        "snapshot", "topic_type", "jaccard_similarity_first", "jaccard_similarity_previous"
    ]].melt(
        id_vars=["snapshot", "topic_type"],
        value_vars=["jaccard_similarity_first", "jaccard_similarity_previous"],
        var_name="reference", value_name="jaccard",
    )
    plot_data["reference"] = plot_data["reference"].map({
        "jaccard_similarity_first": "Snapshot 0",
        "jaccard_similarity_previous": "Previous snapshot",
    })
    sns.set_theme(style="whitegrid")
    palette = {"static": "#2a6fbb", "dynamic": "#d95f02"}
    _, ax = plt.subplots(figsize=(9, 3))
    ax = sns.lineplot(
        data=plot_data,
        ax=ax,
        x="snapshot",
        y="jaccard",
        hue="topic_type",
        palette=palette,
        style="reference",
        style_order=["Snapshot 0", "Previous snapshot"],
        markers=True,
        estimator="mean",
        errorbar=("pi", 50),
        err_style="band",
        err_kws={"alpha": 0.15},
    )
    ax.set(
        xlabel="Snapshot",
        ylabel="Jaccard similarity",
        ylim=(0, 1),
    )
    ax.set_xlim(0, 21)
    ax.set_xticks(range(0, 22, 7))
    handles, labels = ax.get_legend_handles_labels()
    entries = [(handle, label) for handle, label in zip(handles, labels)
               if label not in {"topic_type", "reference"}]
    legend = ax.legend(
        [handle for handle, _ in entries], [label for _, label in entries],
        ncols=4, title=None, loc="upper center",
        bbox_to_anchor=(0.5, -0.25), frameon=False,
    )
    for text in legend.get_texts():
        text.set_text(
            {
                "static": "Static Topics",
                "dynamic": "Dynamic Topics",
            }.get(text.get_text(), text.get_text())
        )
    plt.tight_layout()
    plt.savefig("examples/dynamic-dataset/analyses/images/ranking-domain-stability.pdf", dpi=300, bbox_inches="tight")
    plt.close(ax.figure)


if __name__ == "__main__":
    main()
