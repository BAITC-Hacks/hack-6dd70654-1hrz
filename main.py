import pandas as pd

nodes = pd.read_parquet("data/nodes.parquet")
edges = pd.read_parquet("data/edges.parquet")
transactions = pd.read_parquet("data/transactions.parquet")

print(nodes.head())
print(edges.head())
print(transactions.head())