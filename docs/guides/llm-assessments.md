# LLM assessments

SnapIR can judge every (topic, document) pair of a snapshot with an LLM. It uses the extracted page text and the full topic. This is cheap and fast, and you can run it for every snapshot. Whether LLM judgments can stand in for human ones is still debated (paper §2), so validate them against a human sample before relying on them.


## Run

Requires `topic-gen` and the `SNAPIR_LLM_*` variables (see the [README](../../README.md#configuration)).

```bash
D=2026-10-26
snapir judgments generate \
  --serps data/serps/${D}_queries-selected --documents data/docs/$D \
  --topics data/topics-selected.jsonl --output data/judgments/llm --dry-run   # count pending pairs

snapir judgments generate \
  --serps data/serps/${D}_queries-selected --documents data/docs/$D \
  --topics data/topics-selected.jsonl --output data/judgments/llm
```

`--query-id` limits the run to one query. `--max-chars` (default 4000) limits how much page text is sent to the model. Use `0` to send all of it. `SNAPIR_LLM_CONCURRENCY` sets the number of parallel requests.

Every document linked from the SERP is judged, not only the organic results. Pairs already in the output are skipped, so you can point every snapshot at the same output directory.


## Output

- `assessments.jsonl`: one record per pair with `topic_id`, `query_id`,
  `docno`, `snapshot_date`, the scores `M`, `T` and `O`, `llm_model` and
  `assessed_at`.
- `qrels.txt`: TREC qrels using the overall score `O`, rewritten after every
  run.



## Prompt

The default is the built-in `-DNA-zero-shot` prompt (Thomas et al.), which asks for intent match (`M`), trustworthiness (`T`) and an overall score (`O`), using the topic's description and narrative. Pass `--prompt` with another built-in `topic-gen` prompt name, or with your own prompt YAML that uses the input variables `query`, `description`, `narrative` and `document` and returns the same M/T/O structure.
