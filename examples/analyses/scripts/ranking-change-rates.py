import gzip
import json

import pandas as pd
from ir_datasets_longeval import load
from examples.analyses.scripts.utils import RESULTS_DIR, doc_id_from_url, load_serp, normalize_url


def get_reference_ranking(topic_type: str, snapshot: int) -> dict[str, dict[str, float]]:
    reference = {}
    dataset = load(f"dynamic-dataset/{topic_type}/snapshot/{snapshot}/IN")
    for query in dataset.queries_iter():
        serp = load_serp(query.serp_file)
        if serp is None:
            continue
        reference[query.query_id] = serp["organic_results"]
    return reference


def get_query_date(query) -> str:
    """Get the query emergence date from the SERP file path."""
    # TODO: This is a temporary hack to get the query emergence date from the SERP file path. 
    # We should instead store the query emergence date in the dataset itself.
    return query.serp_file.parent.parent.parent.name

def get_query_emergence_dates(topic_type: str, snapshot: int) -> dict[str, str]:
    emergence_dates = {}
    dataset = load(f"dynamic-dataset/{topic_type}/snapshot/{snapshot}/IN")
    for query in dataset.queries_iter():
        emergence_dates[query.query_id] = get_query_date(query)
    return emergence_dates


def get_capture_hashes() -> dict[str, str]:
    """Return the packaged raw-capture hash keyed by date-bearing docno."""
    hashes = {}
    with gzip.open(CAPTURES_FILE, "rt", encoding="utf-8") as f:
        for line in f:
            capture = json.loads(line)
            if capture.get("raw_hash"):
                hashes[capture["capture_docno"]] = capture["raw_hash"]
    return hashes


def novelty_rate(reference_docs: set[str], new_docs: set[str]) -> float:
    if not reference_docs:
        return 0.0
    return len(new_docs - reference_docs) / len(reference_docs)


def churn_rate(reference_docs: set[str], new_docs: set[str]) -> float:
    if not reference_docs:
        return 0.0
    return len(reference_docs - new_docs) / len(reference_docs)


class ChangeRate:
    def __init__(
        self,
        new_snapshot: int,
        old_snapshot: int,
        capture_hashes: dict[str, str],
        topic_type: str,
    ):
        self.new_snapshot = new_snapshot
        self.old_snapshot = old_snapshot
        self.capture_hashes = capture_hashes
        self.topic_type = topic_type
        self.new_docstore = self._get_docstore(new_snapshot)
        self.old_docstore = self._get_docstore(old_snapshot)
        
    def _get_docstore(self, snapshot: int):
        dataset = load(f"dynamic-dataset/{self.topic_type}/snapshot/{snapshot}/IN")
        return dataset.docs_store()

    def change_rate_text(self, reference_docs: set[str], new_docs: set[str], query_date_new: str, query_date_old: str) -> float | None:
        """The proportion of persistent result URLs whose extracted content changed or whose content could not be observed successfully at either comparison point."""
        changed_count = 0
        eligible_count = 0

        for url in reference_docs.intersection(new_docs):
            doc_id = doc_id_from_url(normalize_url(url))
            try:
                doc_new = self.new_docstore.get(f"{doc_id}.{query_date_new}")
                doc_old = self.old_docstore.get(f"{doc_id}.{query_date_old}")
            except KeyError:
                continue

            if any(
                doc.scrape_status != "ok" or not (doc.text or "").strip()
                for doc in (doc_old, doc_new)
            ):
                continue

            eligible_count += 1
            changed_count += doc_new.default_text() != doc_old.default_text()

        return changed_count / eligible_count if eligible_count else None

    def change_rate_mhtml(
        self,
        reference_docs: set[str],
        new_docs: set[str],
        query_date_new: str,
        query_date_old: str,
    ) -> float | None:
        """Compare raw mHTML hashes; treat a missing capture as changed."""
        if self.new_snapshot == self.old_snapshot:
            return 0.0

        candidate_doc_ids = reference_docs.intersection(new_docs)
        if not candidate_doc_ids:
            return None

        changed_count = 0
        for url in candidate_doc_ids:
            doc_id = doc_id_from_url(normalize_url(url))
            hash_new = self.capture_hashes.get(f"{doc_id}.{query_date_new}")
            hash_old = self.capture_hashes.get(f"{doc_id}.{query_date_old}")
            if hash_new is None or hash_old is None or hash_new != hash_old:
                changed_count += 1

        return changed_count / len(candidate_doc_ids)
    
    
def calculate_change_rates(topic_type: str):
    reference_snapshot = 0
    references = get_reference_ranking(topic_type, reference_snapshot)
    
    emergence_dates = get_query_emergence_dates(topic_type, reference_snapshot)
    # capture_hashes = get_capture_hashes()
    dataset = load(f"dynamic-dataset/{topic_type}/snapshot/IN")
    
    res = []

    for snapshot in dataset.get_datasets():
        num_queries = len(list(snapshot.queries_iter()))
        print(f"Processing snapshot {snapshot.get_snapshot()}, {num_queries} queries...")
        change_rate = ChangeRate(
            new_snapshot=snapshot.get_snapshot(),
            old_snapshot=0,
            capture_hashes={},
            topic_type=topic_type,
        )
        for query in snapshot.queries_iter():
            query_date_old = emergence_dates[query.query_id]
            
            # Get sets of IDs
            ranking_reference = references[query.query_id]
            ranking_reference_ids = set([normalize_url(r["link"]) for r in ranking_reference])
            serp = load_serp(query.serp_file)
            if serp is None or query.query_id not in references:
                continue
            ranking_new = serp.get("organic_results", [])
            new_ranking_ids = set([normalize_url(r["link"]) for r in ranking_new])
            
            # Calculate cahnge rates
            novelty_rate_value = novelty_rate(ranking_reference_ids, new_ranking_ids)
            churn_rate_value = churn_rate(ranking_reference_ids, new_ranking_ids)
            change_rate_text_value = change_rate.change_rate_text(ranking_reference_ids, new_ranking_ids, get_query_date(query), query_date_old)
            change_rate_mhtml_value = change_rate.change_rate_mhtml(ranking_reference_ids, new_ranking_ids, get_query_date(query), query_date_old)
            
            res.append({
                "snapshot": snapshot.get_snapshot(),
                "query_id": query.query_id,
                "topic_id": query.topic_id,
                "topic_type": topic_type,
                "novelty_rate": novelty_rate_value,
                "churn_rate": churn_rate_value,
                "change_rate_text": change_rate_text_value,
                "change_rate_mhtml": change_rate_mhtml_value,
            })
            
    pd.DataFrame(res).to_csv(RESULTS_DIR / f"change-rates-{topic_type}.csv", index=False)

def main():
    calculate_change_rates("static")
    calculate_change_rates("dynamic")
    
if __name__ == "__main__":
    main()
