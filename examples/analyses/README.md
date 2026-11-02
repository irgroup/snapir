# Example
## Analysis

All plots and the AI-overview summary table use snapshots 0–21 inclusive (baseline plus three weeks). Extraction and metric CSVs retain the full observation period for exploratory work. The RBO figure uses one-based snapshot labels, so original snapshots 1–21 appear as 2–22.

### SERP components
- [serp-components.py](scripts/serp-components.py) 

Component percentages use successfully loaded SERPs at each snapshot as the denominator. Sample sizes are shown in the heatmaps and saved in `results/analyses/serp-component-sample-sizes.csv`; retrieval status is recorded in `results/analyses/serp-observations-{static,dynamic}.csv`. The observed query set declines and changes over time, so later estimates have fewer observations and can reflect changes in query composition. The line plots show the first three weeks; heatmaps use the same window and mask snapshots with no observed SERPs.

| Question | Script | Results | Plot | Figure |
|---|---|---|---|---|
| How do SERP components change over time? | [serp-components.py](scripts/serp-components.py) | [serp-components-dynamic.csv](results/serp-components-dynamic.csv) [serp-components-static.csv](results/serp-components-static.csv) | [plot-serp-components.py](plots/plot-serp-components.py), [plot-serp-components-heatmap.py](plots/plot-serp-components-heatmap.py) | [serp-components-absolute-by-snapshot.pdf](images/serp-components-absolute-by-snapshot.pdf) [serp-components-by-snapshot.pdf](images/serp-components-by-snapshot.pdf), [images/serp-components-absolute-heatmap.pdf](examples/images/serp-components-absolute-heatmap.pdf) | 


### AI Overviews

- Gather sources from rankings [ai-overview-sources-overview.py](scripts/ai-overview-sources-overview.py) 

This also writes `results/analyses/ai-overview-observations-{static,dynamic}.csv`, with one row per query–snapshot, SERP retrieval status, overview detection, independent text/source extraction flags, and distinct source counts. Failed SERPs have unknown overview/extraction fields. The overview table uses these observations for counts, first appearance, and extraction coverage; source statistics use only overviews with extracted sources. Detection follows SERP component-key presence.

| Question | Script | Results | Plot | Figure |
|---|---|---|---|---|
| How stable are the cited sources over time? | [ai-overview-sources-stability.py](scripts/ai-overview-sources-stability.py) | [ai-overview-sources-jaccard-domain-dynamic.csv](results/ai-overview-sources-jaccard-domain-dynamic.csv), [ai-overview-sources-jaccard-domain-static.csv](results/ai-overview-sources-jaccard-domain-static.csv), [ai-overview-sources-jaccard-dynamic.csv](results/ai-overview-sources-jaccard-dynamic.csv), [ai-overview-sources-jaccard-static.csv](results/ai-overview-sources-jaccard-static.csv) | [plot-ai-overview-source-stability.py](plots/plot-ai-overview-source-stability.py) | [ai-overview-source-stability.pdf](images/ai-overview-source-stability.pdf), [ai-overview-source-stabilitydomain.pdf](images/ai-overview-source-stabilitydomain.pdf) |
| What is the overlap between the raked docs and the ai overview sources? | [ai-overview-sources-ranking-jaccatd.py](scripts/ai-overview-sources-ranking-jaccatd.py) | [ai-overview-sources-ranking-jaccard-dynamic.csv](results/ai-overview-sources-ranking-jaccard-dynamic.csv), [ai-overview-sources-ranking-jaccard-static.csv](results/ai-overview-sources-ranking-jaccard-static.csv) |  | | 
| How do AI overviews differ between topic types? |[table-ai-overview.py](plots/table-ai-overview.py) | | | | |



### Organic Results

#### Concept induction

Edit the settings at the top of `scripts/concept-induction.py` (topic, snapshots,
number of concepts, match threshold, and output folder), then run:

```bash
uv run examples/analyses/scripts/concept-induction.py
```

Set `OPENAI_API_KEY` in the environment. The script collects successful articles,
generates one shared concept set, scores each unique text once, and saves scores,
article counts, concept prevalence, and a plot. All generated concepts are scored.
Each article counts once per snapshot; missing scores are excluded from prevalence.
Outputs default to `examples/results/concepts-dynamic-TOPIC_ID/`.

For the per-topic RBO overview, run:

```bash
uv run examples/analyses/plots/plot-rbo-overview.py
```

This writes `images/rbo-topic-overview.pdf` and `.png`: one panel per topic,
with five query lines and their query texts. Axes show snapshots 0–21 and RBO
relative to snapshot 0 (p=0.8). The baseline self-comparison is shown at 1;
missing subsequent observations leave gaps. Use `--topic-type dynamic` or
`--topic-type static` with `--output PATH` for a smaller overview.

For an 8 × 6.1 cm single-topic plot with default styling and no legend, run:

```bash
uv run examples/analyses/plots/plot-rbo-topic.py --topic-type dynamic --topic-id b3d15ec8
```

Topic IDs accept unique prefixes. This writes PDF and PNG files under
`images/rbo-topic-dynamic-b3d15ec8`, with transparent backgrounds and a 600 dpi PNG.

| Question | Script | Results | Plot | Figure |
|---|---|---|---|---|
|How does the set of ranked docs evolve?|  [ranking-change-rates.py](scripts/ranking-change-rates.py)  |  [change-rates-dynamic.csv](results/change-rates-dynamic.csv) |  [plot-ranking-change-rates.py](examples/plots/plot-ranking-change-rates.py) | [document-change-rates-by-snapshot.pdf](images/document-change-rates-by-snapshot.pdf) |
| How does the ranking change? | [ranking-rbo.py](scripts/ranking-rbo.py) | [rbo-static.csv](results/rbo-static.csv), [rbo-dynamic.csv](results/rbo-dynamic.csv) | [plot-rbo.py](plots/plot-rbo.py) | [rank-correlation-rbo.pdf](images/rank-correlation-rbo.pdf) |
| How stable are the ranked domains? | [ranking-domain-stability.py](scripts/ranking-domain-stability.py) |   |   |  |

