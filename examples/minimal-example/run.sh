#!/usr/bin/env bash
# Minimal SnapIR example: one debate -> one topic -> its queries -> one snapshot.
# Walkthrough with explanations: README.md in this directory.
#
#   cp .env.example .env    # fill in your keys
#   ./run.sh                # run every step
#   STEP=5 ./run.sh         # resume from step 5
#   STOP=4 ./run.sh         # stop after step 4
#   DEBATE_ID=... ./run.sh  # pick a debate instead of the newest one
set -euo pipefail

cd "$(dirname "$0")"
if [[ -f .env ]]; then set -a; source .env; set +a; fi

DATA=data
TODAY=$(date +%F)
SERPS_DIR="$DATA/serps/${TODAY}_queries-selected"
DOCS_DIR="$DATA/docs/$TODAY"
START=${STEP:-1}
END=${STOP:-10}

step() { [[ $1 -ge $START && $1 -le $END ]] && echo -e "\n=== Step $1: $2"; }

# The topic generated for the chosen debate (one topic per debate).
topic_id() {
  python3 -c '
import json, sys
debate = sys.argv[1]
for line in open(sys.argv[2]):
    t = json.loads(line)
    if t["debate_id"] == debate:
        print(t["topic_id"]); break
' "$DEBATE_ID" "$DATA/topics-generated.jsonl"
}

if step 1 "Fetch source debates"; then
  snapir topics fetch-debates --output "$DATA/debates"
fi

# Default to the newest debate on the listing.
[[ $END -lt 2 ]] && exit 0
DEBATE_ID=${DEBATE_ID:-$(python3 -c '
import json, sys
debates = json.load(open(sys.argv[1]))
print(max(debates, key=lambda d: d["date"])["id"])
' "$DATA/debates/debates.json")}
echo "Debate: $DEBATE_ID"

if step 2 "Generate a topic for one debate"; then
  snapir topics generate --debates "$DATA/debates/debates.json" \
    --prompt prompts/topic-emerging-v5.yaml \
    --output "$DATA/topics-generated.jsonl" --debate-id "$DEBATE_ID"
fi

[[ $END -lt 3 ]] && exit 0
TOPIC_ID=$(topic_id)
[[ -n $TOPIC_ID ]] || { echo "No topic for debate $DEBATE_ID yet"; exit 1; }
echo "Topic: $TOPIC_ID"

if step 3 "Generate queries for the topic"; then
  snapir queries generate --debates "$DATA/debates/debates.json" \
    --topics "$DATA/topics-generated.jsonl" --prompt prompts/queries-v5.yaml \
    --output "$DATA/queries-generated.jsonl" --topic-id "$TOPIC_ID"
fi

if step 4 "Add the topic to the curated set"; then
  snapir topics select --topic-id "$TOPIC_ID" \
    --topics "$DATA/topics-generated.jsonl" --queries "$DATA/queries-generated.jsonl" \
    --output-topics "$DATA/topics-selected.jsonl" --output-queries "$DATA/queries-selected.jsonl"
fi

if step 5 "Fetch today's SERPs"; then
  snapir serps fetch --queries "$DATA/queries-selected.jsonl" --output "$DATA/serps"
fi

if step 6 "Capture the linked pages"; then
  snapir documents scrape --serps "$SERPS_DIR" --output "$DATA/docs"
fi

if step 7 "Extract page text"; then
  snapir documents extract-text --documents "$DOCS_DIR"
fi

if step 8 "Check the snapshot for gaps"; then
  snapir export verify --queries "$DATA/queries-selected.jsonl" \
    --serps "$SERPS_DIR" --documents "$DATA/docs"
fi

if step 9 "Judge relevance with an LLM"; then
  snapir judgments generate --serps "$SERPS_DIR" --documents "$DOCS_DIR" \
    --topics "$DATA/topics-selected.jsonl" --output "$DATA/judgments/$TODAY"
fi

if step 10 "Export rankings and the collection"; then
  snapir export rankings --serps "$DATA/serps" --output "$DATA/rankings.csv"
  snapir export snapshot --serps "$DATA/serps" --documents "$DATA/docs" \
    --topics "$DATA/topics-selected.jsonl" --queries "$DATA/queries-selected.jsonl" \
    --qrels "$DATA/judgments/$TODAY/qrels.txt" --output collection
fi
