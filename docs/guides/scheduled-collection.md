# Scheduled collection

A longitudinal collection needs a snapshot **every day, at about the same time**, because a missed day cannot be fetched later. SnapIR includes two [Prefect](https://docs.prefect.io) flows and a Docker stack that run the pipeline unattended. The setup commands are in [`workflows/README.md`](../../workflows/README.md). This page covers what runs and how to operate it.


## What is automated

| Flow | Steps | Schedule (example) |
|---|---|---|
| `topic-pool` | fetch new debates → generate topics → generate queries | weekdays, 14:00 |
| `daily-snapshot` | fetch SERPs → scrape SERP pages → rescrape all indexed pages → extract text → verify | daily, 08:00, one deployment per query set |

What stays manual is the part that needs judgment: selecting topics, reviewing captures, and running assessments. The usual rhythm:

1. `topic-pool` adds candidates. You review them and run `snapir topics select` for the ones you want. The selected topics go into the query set of a `daily-snapshot` deployment.
2. `daily-snapshot` captures every query of the set each day.
3. When a new topic should be judged in time, run its first snapshot right away (`serps fetch --query-id …`, then `documents scrape`) instead of waiting for the next scheduled run, do the [quality pass](capture-quality.md), and build its [questionnaire](human-assessments.md).


## Choosing a setup

- **Run once locally:** `python workflows/flows/daily_snapshot.py --queries …`. Good for testing. A plain `cron` job running this works for small projects.
- **Prefect with a local worker:** gives you a schedule, retries, logs and a UI on any machine.
- **Docker stack:** Prefect server, Postgres and a worker with Chromium and a virtual display (Xvfb). Headed scraping passes more bot checks, and VNC on port 5900 lets you watch the browser or take it over for `--interactive` fixes. This is what the paper's study used.