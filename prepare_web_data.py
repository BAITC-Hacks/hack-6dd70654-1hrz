import pandas as pd
import json
from pathlib import Path

DATA = Path("data")
OUT = Path("web_data")

OUT.mkdir(exist_ok=True)

nodes = pd.read_parquet(DATA / "nodes.parquet")
edges = pd.read_parquet(DATA / "edges.parquet")
transactions = pd.read_parquet(DATA / "transactions.parquet")

# Большие ID обязательно превращаем в строки для JavaScript
if "gid" in nodes.columns:
    nodes["gid"] = nodes["gid"].astype(str)

for col in ["src", "dst"]:
    if col in edges.columns:
        edges[col] = edges[col].astype(str)

    if col in transactions.columns:
        transactions[col] = transactions[col].astype(str)

# Даты тоже делаем строками
if "date" in transactions.columns:
    transactions["date"] = transactions["date"].astype(str)

nodes.to_json(
    OUT / "nodes.json",
    orient="records",
    force_ascii=False,
    indent=2
)

edges.to_json(
    OUT / "edges.json",
    orient="records",
    force_ascii=False,
    indent=2
)

transactions.to_json(
    OUT / "transactions.json",
    orient="records",
    force_ascii=False,
    indent=2
)

print("Готово!")
print("Nodes:", len(nodes))
print("Edges:", len(edges))
print("Transactions:", len(transactions))
print()
print("Созданы:")
print("web_data/nodes.json")
print("web_data/edges.json")
print("web_data/transactions.json")