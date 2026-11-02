import pandas as pd
from examples.analyses.scripts.utils import RESULTS_DIR, load_serp
from ir_datasets_longeval import load
dataset = load(f"dynamic-dataset/static/snapshot/IN")

res = []
observations = []
nque = 0
nser= 0
for snapshot in dataset.get_datasets():
    if snapshot.get_snapshot() == 22:
        break
    q = 0 
    q += len([query for query in snapshot.queries_iter()])
    for query in snapshot.queries_iter():
        serp = load_serp(query.serp_file)
        if serp:
            nser += 1
    print(snapshot.get_snapshot(), q)
    nque += q
    
print("Total queries:", nque, "of", 22*50)
print("Total serp:", nser, "of", 22*50)
    