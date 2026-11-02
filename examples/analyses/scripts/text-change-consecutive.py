"""Compare successful persistent documents at adjacent snapshot ages."""
import pandas as pd
from ir_datasets_longeval import load
from examples.analyses.scripts.utils import RESULTS_DIR, doc_id_from_url, load_serp, normalize_url


def observations(topic_type, snapshot):
    result = {}
    for query in load(f"dynamic-dataset/{topic_type}/snapshot/{snapshot}/IN").queries_iter():
        serp = load_serp(query.serp_file)
        if serp is not None:
            result[query.query_id] = (
                query.serp_file.parent.parent.parent.name,
                {normalize_url(r["link"]) for r in serp.get("organic_results", [])},
            )
    return result


def calculate(topic_type):
    store = load(f"dynamic-dataset/{topic_type}/snapshot/0/IN").docs_store()
    previous = observations(topic_type, 0)
    rows = []
    for snapshot in range(1, 22):
        current = observations(topic_type, snapshot)
        for query_id in sorted(previous.keys() & current.keys()):
            old_date, old_urls = previous[query_id]
            new_date, new_urls = current[query_id]
            persistent = old_urls & new_urls
            eligible = changed = missing = unavailable = 0
            for url in persistent:
                doc_id = doc_id_from_url(url)
                try:
                    old = store.get(f"{doc_id}.{old_date}")
                    new = store.get(f"{doc_id}.{new_date}")
                except KeyError:
                    missing += 1
                    continue
                if any(d.scrape_status != "ok" or not (d.text or "").strip() for d in (old, new)):
                    unavailable += 1
                    continue
                eligible += 1
                changed += old.default_text() != new.default_text()
            rows.append(dict(
                topic_type=topic_type, query_id=query_id, snapshot=snapshot,
                old_date=old_date, new_date=new_date,
                persistent_pairs=len(persistent), eligible_pairs=eligible,
                missing_pairs=missing, unavailable_pairs=unavailable,
                changed_pairs=changed,
                text_change_rate=changed / eligible if eligible else None,
            ))
        # Never bridge a missing snapshot with the preceding observed day.
        previous = current
    frame = pd.DataFrame(rows)
    output = RESULTS_DIR / f"text-change-consecutive-{topic_type}.csv"
    frame.to_csv(output, index=False)
    assert (frame.eligible_pairs + frame.missing_pairs + frame.unavailable_pairs == frame.persistent_pairs).all()
    assert (frame.changed_pairs <= frame.eligible_pairs).all()
    assert frame.loc[frame.eligible_pairs == 0, "text_change_rate"].isna().all()
    print(f"wrote {output} ({len(frame)} query pairs)")


if __name__ == "__main__":
    for topic_type in ("static", "dynamic"):
        calculate(topic_type)
