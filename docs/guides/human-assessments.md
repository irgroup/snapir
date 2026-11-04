# Human assessments

SnapIR builds relevance questionnaires for [SoSci Survey](https://www.soscisurvey.de) from a snapshot. The questionnaires show assessors the **screenshots** of the documents, so judgments reflect each page as it looked on the day of the search. You recruit assessors elsewhere, for example on [Prolific](https://www.prolific.com). SnapIR then turns the answers back into TREC qrels.

```
1. judgments create-questions
2. judgments questionnaire
3. SoSci (you)
4. recruit (you)
5. export answers
6. filter
7. export qrels
```


## Study design

A questionnaire is built from one dated snapshot. When it is answered is up to you. The paper's setting combines two kinds of judgments:

- **In-time (IN):** a snapshot is judged right after it is captured.
- **Retrospective (RE):** the *same* questionnaire is answered again later, so the frozen documents of an earlier snapshot are judged again, for example one and two weeks later.


## 1. Build the questionnaire

A questionnaire is assembled from SoSci XML parts:

| Option | Content |
|---|---|
| `--template` | Question pools. `judgments create-questions` generates the relevance and rationale pools. Add your attention-check pool here |
| `--questionnaire` | Your working questionnaire. Its pages before the first task page (welcome, consent, instructions) are copied unchanged |
| `--train` | Optional training block that is spliced in after the intro pages |

<!-- TODO: ship the paper's templates (questionnaire.xml with consent/instructions, train.xml, questions-attention.xml) in examples/sosci/ and reference them here. -->

```bash
snapir judgments create-questions --output data/sosci        # once

snapir judgments questionnaire \
  --serps data/serps/2026-10-26_queries-selected --documents data/docs/2026-10-26 \
  --topics data/topics-selected.jsonl \
  --query-id <query_id> --query-id <query_id> \
  --template data/sosci/questions-relevance.xml \
  --template data/sosci/questions-rationale.xml \
  --template <attention-checks>.xml \
  --questionnaire <questionnaire>.xml --train <training>.xml \
  --name T1-IN1 --output data/questionnaires
```

The output is `data/questionnaires/<date>_<name>_<query_id>/` with `questionnaire.xml`, `screenshots/`, and `manifest.csv`. By default, assessors grade each page as *highly relevant*, *relevant* or *not relevant*, judge the AI overview, and can leave a free-text rationale. Documents are shuffled within each topic. `--seed` makes the order reproducible.

> **Keep `manifest.csv`.** Question IDs are reused across questionnaires,
> and the manifest is the only mapping from answers to documents.

**Avoid double judging.** If documents of this snapshot are already being judged in another questionnaire (for example, an RE round of an earlier snapshot), pass that questionnaire's manifest with `--exclude-manifest`. A document that is still ranked for the same query and whose text is unchanged is skipped. A document whose text changed is included again.


## 2. Set up SoSci

1. Create a new questionnaire and give it **exactly the name of the output directory**. `export answers` uses this name to find the manifest.
2. Paste `questionnaire.xml` into the *Internal code* tab.
3. Upload everything in `screenshots/` under *Images and Media Files*, and state the legal basis for using them (see [responsible use](responsible-use.md)).
4. In the questionnaire settings, **turn off "Number questions in output consecutively"**, so that answer variables keep the IDs in the manifest, and turn on the back button.
5. Click through the whole questionnaire once as a participant.

For an RE round, reuse the same SoSci questionnaire. Check that it still loads completely, then recruit new assessors.


## 3. Recruit

Link the SoSci questionnaire from your recruiting platform. Practical notes
from the paper's study:

- Three assessors per questionnaire. Exclude participants of earlier rounds, so that RE judgments come from new assessors.
- About 20 minutes per questionnaire, paid at a fair hourly rate.
- **Check attention checks on the same day** that submissions arrive. On Prolific, ask failing participants to return their submission instead of rejecting it.
- Get ethics approval and informed consent according to your institution's rules.


## 4. Answers to qrels

```bash
snapir export answers --sosci "$SOSCI_EXPORT_URL" \
  --manifest data/questionnaires/2026-10-26_T1-IN1_<query_id> \
  --output data/judgments/answers.csv
```

`--sosci` accepts SoSci's JSON export URL or a downloaded data file. You can repeat `--manifest`. The CSV has one row per (assessor, document) with `query_id`, `docno`, `grade` (0–2), `rationale`, and case fields such as `finished`, `attention_checks` and `attention_passed`.

Filter the rows you want to keep (for example, finished cases that passed all attention checks), then aggregate:

```bash
snapir export qrels --answers data/judgments/answers-clean.csv \
  --output data/judgments/qrels.txt --agg majority --min-assessors 2
```

`--agg` is `majority` (ties fall back to the median), `median`, `max` or `min`. Pass the qrels to `export snapshot --qrels`, which assigns each judgment to the snapshot of its docno.
