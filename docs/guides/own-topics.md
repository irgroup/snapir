# Bring your own topics

The debate-based steps (1–3) are one way to get information needs. Any topic source works: TREC topics, a user study, a list of trending queries. Write the two JSONL files yourself and start at step 4 (`serps fetch`). The paper did this for its static topics from TREC Core 2018.


## Topics

One JSON object per line. `topic_id`, `title` and `description` are what SnapIR reads. `narrative` is used by both relevance assessment paths, so include it if you plan to judge.

```json
{"topic_id": "321", "title": "Women in Parliaments", "description": "Pertinent documents will reflect the fact that women continue to be poorly represented in parliaments across the world, and the gap in political power between the sexes is very wide, particularly in the Third World.", "narrative": "Pertinent documents relating to this issue will discuss the lack of representation by women, ..."}
```


## Queries

One query per line, linked to its topic:

```json
{"topic_id": "321", "query_id": "321-1", "query": "women representation in parliaments worldwide"}
```

IDs can be any strings that are unique and stay stable: they become directory and file names and appear in runs and qrels. Choose them once and never change them.

You can use the topic title as the only query, write queries by hand, or generate several per topic. 