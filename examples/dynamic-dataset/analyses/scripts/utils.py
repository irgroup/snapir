import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Optional

RESULTS_DIR = Path("examples/dynamic-dataset/analyses/results")

def load_serp(serp_file) -> Optional[dict]:
    try:
        with gzip.open(serp_file, 'rt', encoding='utf-8') as f:
            serp = json.load(f)
    except Exception as e:
        print(f"Error loading SERP for {serp_file}: {e}")
        return None
    return serp

_LITERAL_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")

def normalize_url(url: str) -> str:
    """Match the URL normalization used while packaging the dataset."""
    return _LITERAL_UNICODE_ESCAPE.sub(
        lambda match: chr(int(match.group(1), 16)), url
    )
    
def doc_id_from_url(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def docs_from_serp(serp: dict) -> set[str]:
    """Get the set of document IDs from a SERP."""
    docs = set()
    for result in serp.get("organic_results", []):
        docs.add(result.get("link"))
    return docs