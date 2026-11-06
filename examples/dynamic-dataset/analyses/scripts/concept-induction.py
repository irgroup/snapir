import asyncio
import json
from pathlib import Path

import pandas as pd
from ir_datasets_longeval import load

from examples.analyses.scripts.utils import doc_id_from_url, load_serp, normalize_url

TOPIC_TYPE = "dynamic"
TOPIC_ID = "7b05847afd5ea83ee809cfefe593733e"
SNAPSHOTS = [0, 7, 14, 21]  # Add 6, 13, 20 for preceding-day comparisons.
N_CONCEPTS = 20
MATCH_THRESHOLD = 1.0
OUTPUT = Path(f"examples/dynamic-dataset/analyses/results/concepts-{TOPIC_TYPE}-{TOPIC_ID}")


def repair_unicode(value):
    """Repair escaped surrogate pairs returned in scoring quotes."""
    if isinstance(value, str):
        return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    if isinstance(value, list):
        return [repair_unicode(item) for item in value]
    if isinstance(value, dict):
        return {repair_unicode(key): repair_unicode(item) for key, item in value.items()}
    return value


def collect_articles():
    rows = []
    skipped = 0
    for snapshot in SNAPSHOTS:
        dataset = load(f"dynamic-dataset/{TOPIC_TYPE}/snapshot/{snapshot}/IN")
        store = dataset.docs_store()
        for query in dataset.queries_iter():
            if query.topic_id != TOPIC_ID:
                continue
            serp = load_serp(query.serp_file)
            if serp is None:
                continue
            date = query.serp_file.parent.parent.parent.name
            for result in serp.get("organic_results", []):
                url = normalize_url(result["link"])
                if url in {"https://www.dailymotion.com/video/xat646q", "https://www.dailymotion.com/video/xat6qvm"}:
                    continue  # These two pages contain only player/interface text.
                base_id = doc_id_from_url(url)
                try:
                    doc = store.get(f"{base_id}.{date}")
                except KeyError:
                    skipped += 1
                    continue
                if doc.scrape_status != "ok" or not (doc.text or "").strip():
                    skipped += 1
                    continue
                rows.append(dict(snapshot=snapshot, date=date, query_id=query.query_id,
                                 query=query.query, doc_id=doc.doc_id, url=url,
                                 text=doc.default_text()))
    if not rows:
        raise ValueError("No usable articles found; check TOPIC_ID and SNAPSHOTS")
    print(f"Collected {len(rows)} article occurrences; skipped {skipped} unavailable articles")
    return pd.DataFrame(rows)


def summarize(frame, unique_texts, scores):
    concept_scores = scores.pivot(index="doc_id", columns="concept_name", values="score").add_prefix("concept__")
    text_scores = unique_texts[["doc_id", "text"]].merge(
        concept_scores, left_on="doc_id", right_index=True,
        how="left", validate="one_to_one").drop(columns="doc_id")
    scored = frame.merge(text_scores, on="text", how="left", validate="many_to_one")
    articles = scored.drop_duplicates(["snapshot", "doc_id"])
    columns = list(concept_scores.columns)
    # Missing scores stay missing, rather than becoming negative matches.
    matches = articles[columns].ge(MATCH_THRESHOLD).astype(float).where(articles[columns].notna())
    prevalence = matches.assign(snapshot=articles.snapshot).groupby("snapshot").mean() * 100
    return scored, articles, prevalence


async def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from text_lloom.workbench import lloom
    
    OUTPUT.mkdir(parents=True, exist_ok=True)
    frame = collect_articles()
    frame.to_csv(OUTPUT / "articles.csv", index=False)
    unique_texts = frame.drop_duplicates("text")
    print(f"Scoring {len(unique_texts)} unique texts")

    # Lloom reads OPENAI_API_KEY from the environment and uses its default models.
    session = lloom(df=unique_texts, text_col="text", id_col="doc_id")
    params = session.auto_suggest_parameters()
    params["synth_n_concepts"] = N_CONCEPTS
    await session.gen(params=params, n_synth=1, auto_review=False, debug=False)
    (OUTPUT / "concepts.json").write_text(json.dumps(
        repair_unicode([c.to_dict() for c in session.concepts.values()]), indent=2))

    # Same parser repair as the notebook, without the interactive selection widget.
    score_fn = session.score.__func__.__globals__["score_concepts"]
    namespace = score_fn.__globals__["get_score_df"].__globals__
    original_parser = namespace["json_load"]
    namespace["json_load"] = lambda *a, **kw: repair_unicode(original_parser(*a, **kw))
    try:
        scores = await session.score(score_all=True, debug=False)
    finally:
        namespace["json_load"] = original_parser
    scores.to_csv(OUTPUT / "scores.csv", index=False)

    scored, articles, prevalence = summarize(frame, unique_texts, scores)
    scored.to_csv(OUTPUT / "scored-articles.csv", index=False)
    prevalence.to_csv(OUTPUT / "concept-prevalence.csv")
    columns = list(prevalence.columns)
    counts = articles.groupby("snapshot")[columns].count()
    counts.insert(0, "n_articles", articles.groupby("snapshot").size())
    counts.to_csv(OUTPUT / "article-counts.csv")
    print(prevalence)
    print(counts)

    ax = prevalence.rename(columns=lambda c: c.removeprefix("concept__")).plot(marker="o", figsize=(10, 5))
    ax.set(xlabel="Snapshot", ylabel="Articles matching concept (%)", ylim=(0, 100), xticks=prevalence.index)
    ax.legend(title="Concept", bbox_to_anchor=(1.02, 1), loc="upper left")
    ax.figure.savefig(OUTPUT / "concept-prevalence.png", dpi=200, bbox_inches="tight")
    plt.close(ax.figure)
    print(f"Saved results to {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
