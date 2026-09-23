from pathlib import Path
import json
import shutil

import pandas as pd


ROOT = Path(__file__).resolve().parent

BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

BACKEND_DATA = BACKEND / "data"
BACKEND_OUTPUT = BACKEND / "output"

FRONTEND_DATA = FRONTEND / "public" / "data"

FRONTEND_DATA.mkdir(parents=True, exist_ok=True)


# =========================
# COPY BACKEND OUTPUT
# =========================

graph_source = BACKEND_OUTPUT / "graph.json"
report_source = BACKEND_OUTPUT / "report.json"

graph_target = FRONTEND_DATA / "graph.json"
report_target = FRONTEND_DATA / "report.json"

if not graph_source.exists():
    raise FileNotFoundError(
        f"Не найден {graph_source}. Сначала запусти backend/main.py"
    )

if not report_source.exists():
    raise FileNotFoundError(
        f"Не найден {report_source}. Сначала запусти backend/main.py"
    )

shutil.copy2(graph_source, graph_target)
shutil.copy2(report_source, report_target)


# =========================
# TRANSACTIONS
# =========================

transactions_path = BACKEND_DATA / "transactions.parquet"

if not transactions_path.exists():
    raise FileNotFoundError(
        f"Не найден {transactions_path}"
    )

transactions = pd.read_parquet(transactions_path)

transactions["src"] = transactions["src"].astype(str)
transactions["dst"] = transactions["dst"].astype(str)

transactions["date"] = (
    pd.to_datetime(transactions["date"])
    .dt.strftime("%Y-%m-%d")
)

transactions["sum_kzt"] = (
    transactions["sum_kzt"]
    .astype(float)
)

transactions_records = transactions.to_dict(
    orient="records"
)

with open(
    FRONTEND_DATA / "transactions.json",
    "w",
    encoding="utf-8",
) as f:
    json.dump(
        transactions_records,
        f,
        ensure_ascii=False,
        indent=2,
    )


# =========================
# INFO
# =========================

with open(
    graph_target,
    "r",
    encoding="utf-8",
) as f:
    graph = json.load(f)

print("Frontend data prepared successfully.")
print()
print("Graph:")
print(" Nodes:", len(graph.get("nodes", [])))
print(" Edges:", len(graph.get("edges", [])))
print(" Clusters:", len(graph.get("clusters", [])))
print(" Top nodes:", len(graph.get("top_nodes", [])))
print(" Transactions:", len(transactions))
print()
print("Created:")
print(graph_target)
print(report_target)
print(FRONTEND_DATA / "transactions.json")