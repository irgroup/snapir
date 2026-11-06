from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, PercentFormatter
import pandas as pd
import seaborn as sns

EXAMPLES_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = EXAMPLES_DIR / "results"
PLOTS_DIR = EXAMPLES_DIR / "images"

REQUIRED_COLUMNS = {"component", "query_id", "snapshot"}
TOPIC_TYPES = ("dynamic", "static")
TOPIC_LABELS = {"dynamic": "Dynamic", "static": "Static"}
COMPONENT_LABELS = {
	"ai_overview": "AI Overview",
	"organic_results": "Organic Results",
	"related_questions": "Related Questions",
	"related_searches": "Related Searches",
	"top_stories": "Top Stories",
	"inline_videos": "Inline Videos",
	"dmca_messages": "DMCA Messages",
	"discussions_and_forums": "Discussions and Forums",
}


def load_plot_data() -> pd.DataFrame:
    """Component prevalence among successfully loaded SERPs at each snapshot."""
    tables = []
    for topic_type in TOPIC_TYPES:
        components = pd.read_csv(RESULTS_DIR / f"serp-components-{topic_type}.csv")
        observations = pd.read_csv(RESULTS_DIR / f"serp-observations-{topic_type}.csv")
        observations = observations[observations.snapshot.between(0, 21)]
        loaded = observations[observations.serp_loaded.eq(True)][["query_id", "snapshot"]].drop_duplicates()
        denominators = loaded.groupby("snapshot").size()
        snapshots = sorted(observations.snapshot.unique())
        present = components.merge(loaded, on=["query_id", "snapshot"], how="inner")
        counts = present.groupby(["component", "snapshot"])["query_id"].nunique().reindex(
            pd.MultiIndex.from_product([list(COMPONENT_LABELS), snapshots], names=["component", "snapshot"]),
            fill_value=0,
        ).rename("count").reset_index()
        counts["observed_queries"] = counts.snapshot.map(denominators).fillna(0).astype(int)
        counts.loc[counts.observed_queries.eq(0), "count"] = float("nan")
        counts["proportion"] = counts["count"] / counts.observed_queries.replace(0, float("nan"))
        tables.append(counts.assign(topic_type=topic_type))
    return pd.concat(tables, ignore_index=True)


def main() -> None:
    data = load_plot_data()
    sample_sizes = data[["topic_type", "snapshot", "observed_queries"]].drop_duplicates()
    sample_sizes.to_csv(RESULTS_DIR / "serp-component-sample-sizes.csv", index=False)
    plot_components(data, absolute=False)
    plot_components(data, absolute=True)


def plot_components(plot_data: pd.DataFrame, *, absolute: bool) -> None:
	"""Plot observed SERP prevalence or absolute query counts."""
	sns.set_theme(style="whitegrid", context="paper", rc={
        "font.size": 7, "axes.titlesize": 8, "axes.labelsize": 7,
        "xtick.labelsize": 6, "ytick.labelsize": 6,
        "lines.linewidth": 2.0, "lines.markersize": 2.5,
    })
	fig = plt.figure(figsize=(12.2 / 2.54, 7.5 / 2.54), layout="constrained")
	fig.get_layout_engine().set(hspace=0, h_pad=0.02)
	grid_spec = fig.add_gridspec(2, len(TOPIC_TYPES), height_ratios=(1, 0.17))
	axes = [fig.add_subplot(grid_spec[0, 0])]
	axes.append(fig.add_subplot(grid_spec[0, 1], sharex=axes[0], sharey=axes[0]))
	axes[1].tick_params(labelleft=False)
	legend_axis = fig.add_subplot(grid_spec[1, :])
	legend_axis.set_axis_off()
	for axis, topic_type in zip(axes, TOPIC_TYPES):
		topic_data = plot_data[plot_data["topic_type"] == topic_type]
		sns.lineplot(
			data=topic_data,
			x="snapshot",
			y="count" if absolute else "proportion",
			hue="component",
			hue_order=list(COMPONENT_LABELS),
			markers=True,
			estimator=None,
			errorbar=None,
			legend=topic_type == TOPIC_TYPES[0],
			ax=axis,
		)
		axis.set(
			title=TOPIC_LABELS[topic_type],
			xlabel="Snapshot",
			ylabel="",
			xlim=(0, 21),
		)
		axis.set_xticks(range(0, 22, 7))
		if absolute:
			axis.yaxis.set_major_locator(MaxNLocator(integer=True))
		else:
			axis.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))

	handles, labels = axes[0].get_legend_handles_labels()
	if axes[0].legend_ is not None:
		axes[0].legend_.remove()
	legend_axis.legend(
		handles,
		[COMPONENT_LABELS.get(label, label) for label in labels],
		loc="center",
		ncol=4,
		frameon=False,
		fontsize=7,
		handlelength=1.0,
		handletextpad=0.3,
		labelspacing=0.15,
		borderaxespad=0,
		columnspacing=1.2,
		mode="expand",
		bbox_to_anchor=(0, 0, 1, 1),
	)
	fig.supylabel("Queries with Component" if absolute else "% Present", fontsize=7)

	filename = (
		"serp-components-absolute-by-snapshot.pdf"
		if absolute else "serp-components-by-snapshot.pdf"
	)
	output = PLOTS_DIR / filename
	fig.savefig(output, dpi=300)
	plt.close(fig)
	print(f"wrote {output}")


if __name__ == "__main__":
	main()
