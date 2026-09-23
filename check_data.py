import pandas as pd

nodes = pd.read_parquet("data/nodes.parquet")
edges = pd.read_parquet("data/edges.parquet")
transactions = pd.read_parquet("data/transactions.parquet")

print("\n=== NODES ===")
print(nodes.columns.tolist())
print(nodes.head())
print("Rows:", len(nodes))

print("\n=== EDGES ===")
print(edges.columns.tolist())
print(edges.head())
print("Rows:", len(edges))

print("\n=== TRANSACTIONS ===")
print(transactions.columns.tolist())
print(transactions.head())
print("Rows:", len(transactions))