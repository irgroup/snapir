import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib.ticker import PercentFormatter


def segments(days):
    result = []
    for day in sorted(days):
        if not result or day != result[-1][-1] + 1:
            result.append([day])
        else:
            result[-1].append(day)
    return result


def main():
    sns.set_theme(style="whitegrid", context="paper", rc={
        "font.size": 7, "axes.titlesize": 8, "axes.labelsize": 7,
        "xtick.labelsize": 6, "ytick.labelsize": 6,
        "lines.linewidth": 2,
    })
    fig, axes = plt.subplots(
        2, 1, sharex=True, figsize=(6.1 / 2.54, 6.1 / 2.54),
        gridspec_kw={"height_ratios": [3, 1]}, layout="constrained",
    )
    for topic_type, style, color in zip(
        ("static", "dynamic"), ("-", "-"), sns.color_palette(n_colors=2)
    ):
        df = pd.read_csv(f"examples/dynamic-dataset/analyses/results/text-change-consecutive-{topic_type}.csv")
        print(f"Text change consecutive {topic_type} snapshots 0–21")
        print(df.groupby("snapshot")[["text_change_rate", "eligible_pairs"]].mean().to_markdown())
        print(df[["text_change_rate", "eligible_pairs"]].mean().to_markdown())
        print()
        for ax, column in zip(axes, ("text_change_rate", "eligible_pairs")):
            summary = df.groupby("snapshot")[column].agg(
                mean="mean", lower=lambda x: x.quantile(.25),
                upper=lambda x: x.quantile(.75),
            ).dropna(subset=["mean"])
            for index, days in enumerate(segments(summary.index)):
                part = summary.loc[days]
                ax.plot(days, part["mean"], color=color, linestyle=style,
                        label=topic_type.capitalize() if index == 0 else None)
                ax.fill_between(days, part.lower, part.upper, color=color,
                                alpha=.15, linewidth=0)
    axes[0].set_ylabel("Text change")
    axes[0].set_ylim(0, .65)
    axes[0].yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    axes[0].legend(fontsize=7, frameon=False, handlelength=1.5)
    axes[1].set_ylabel("Pairs/query")
    axes[1].set_ylim(bottom=0)
    axes[1].set_xlim(0, 21)
    axes[1].set_xticks(range(0, 22, 7))
    axes[1].set_xlabel("Snapshot (vs. previous)")
    fig.align_ylabels(axes)

    fig.savefig(f"examples/dynamic-dataset/analyses/images/text-change-consecutive.pdf", dpi=200)
    plt.close(fig)
    print ("wrote examples/dynamic-dataset/analyses/images/text-change-consecutive.pdf")

if __name__ == "__main__":
    main()
