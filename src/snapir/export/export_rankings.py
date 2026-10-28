import json
from pathlib import Path

import pandas as pd

COLUMNS = ["topic", "query", "rank", "url", "timestamp"]


def _rankings(serp_dir: Path) -> list[dict]:
    """Organic rankings of one dated SERP directory, one row per result."""
    rows = []
    for serp_file in sorted(serp_dir.glob("*/*.json")):
        serp = json.loads(serp_file.read_text())
        for rank, result in enumerate(serp.get("organic_results", []), start=1):
            rows.append(
                {
                    "topic": serp_file.parent.name,
                    "query": serp_file.stem,
                    "rank": rank,
                    "url": result.get("link"),
                    "timestamp": serp["search_metadata"]["created_at"][:10],
                }
            )
    return rows


def export_rankings(serps: Path, output: Path) -> int:
    """Write the organic rankings of every dated SERP directory under `serps`
    to a CSV. Returns the number of rows written."""
    rows = [row for d in sorted(serps.iterdir()) if d.is_dir() for row in _rankings(d)]
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=COLUMNS).to_csv(output, index=False)
    return len(rows)
