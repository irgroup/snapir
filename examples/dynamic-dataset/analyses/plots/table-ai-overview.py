import pandas as pd

def main():
    df_static = pd.read_csv("examples/dynamic-dataset/analyses/results/ai-overview-observations-static.csv")
    df_dynamic = pd.read_csv("examples/dynamic-dataset/analyses/results/ai-overview-observations-dynamic.csv")
    df_static["topic_type"] = "static"
    df_dynamic["topic_type"] = "dynamic"
    df = pd.concat([df_static, df_dynamic], ignore_index=True)
    
    df = df[df["snapshot"].between(0, 21)]
    print("Analysis window: snapshots 0–21 (first three weeks)")
    print("SERP retrieval coverage:")
    print(df.groupby("topic_type")["serp_loaded"].agg(["count", "sum", "mean"]))
    print()
    df = df[df["ai_overview_detected"].eq(True)]

    print("AI overview extraction coverage (among detected overviews):")
    coverage = df.groupby("topic_type").agg(
        detected_overviews=("query_id", "size"),
        with_text=("text_extracted", "sum"),
        with_sources=("sources_extracted", "sum"),
    )
    coverage["missing_text_fraction"] = 1 - coverage["with_text"] / coverage["detected_overviews"]
    coverage["missing_sources_fraction"] = 1 - coverage["with_sources"] / coverage["detected_overviews"]
    print(coverage.to_string())
    print()

    print("Have an ai overview at any snapshot")
    num_overviews = df.groupby(["topic_type"])["query_id"].nunique()
    print(num_overviews)
    print()
    
    print("Absolute num ai overviews")
    num_overviews_total = df.groupby(["topic_type"])["query_id"].count()
    print(num_overviews_total)
    print()
    
    print("First appearance of each query in each topic type:")
    apearence_dates = df.groupby(["topic_type", "query_id"])["snapshot"].min().reset_index()
    apearence_dates["snapshot"] = apearence_dates["snapshot"].astype(int)
    avg_overviews = apearence_dates.groupby("topic_type")["snapshot"].describe()
    print(avg_overviews)
    print()
    
    print("Number of distinct sources (among overviews with extracted sources):")
    avg_sources = df[df["sources_extracted"].eq(True)].groupby("topic_type")["num_sources"].describe()
    print(avg_sources)
    print()

    print("Ai overview sorces Jaccard to ranking")
    df_jaccard = pd.read_csv("examples/dynamic-dataset/analyses/results/ai-overview-sources-ranking-jaccard-static.csv")
    df_jaccard = pd.concat([df_jaccard, pd.read_csv("examples/dynamic-dataset/analyses/results/ai-overview-sources-ranking-jaccard-dynamic.csv")], ignore_index=True)
    df_jaccard = df_jaccard[df_jaccard["snapshot"].between(0, 21)]
    jaccard_stats = df_jaccard.groupby("topic_type")["jaccard_similarity"].mean()
    print(jaccard_stats)
    print()


if __name__ == "__main__":
    main()