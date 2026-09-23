"""Money Graph: explainable local graph analysis, without an LLM or a web server.
Run: python main.py
Input: data/nodes.parquet, data/edges.parquet, data/transactions.parquet
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from collections import deque
from decimal import Decimal, InvalidOperation
from pathlib import Path

try:
    import networkx as nx
    import numpy as np
    import pandas as pd
except ImportError as exc:
    raise SystemExit(
        f"Missing dependency: {exc}. Run: "
        'python -m pip install "pandas>=2.2,<4" "numpy>=1.26,<3" '
        '"networkx>=3.3,<4" "pyarrow>=17,<30"'
    ) from exc

BASE = Path(__file__).resolve().parent
ROLES = ["coordinator", "consolidator", "distributor", "transit", "terminal"]
CONFIG = {
    "max_depth": 4,
    "random_seed": 42,
    "louvain_resolution": 1.0,
    "consolidator_min_senders": 3,
    "distributor_min_receivers": 5,
    "transit_ratio_min": 0.8,
    "transit_ratio_max": 1.2,
    "terminal_ratio_max": 0.1,
    "fast_window_days": 2,
    "coordinator_betweenness_percentile": 0.90,
    "boundary_role_cap": 0.70,
    "boundary_priority_factor": 0.90,
}
NODE_COLUMNS = ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"]
CLUSTER_COLUMNS = ["cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis"]
TOP_COLUMNS = ["rank", "gid", "role", "priority_score", "why"]
LIMITATIONS = [
    "Роли — гипотезы для проверки, не утверждения о виновности.",
    "Скоры — значения эвристик, не вероятности; размеченных ролей нет.",
    "Видны только отобранные внутрибанковские переводы за период, не полный баланс.",
    "Исходящие depth=4 обрезаны; такие узлы не классифицируются как terminal.",
    "У seed неполные входящие; соотношение сумм не используется для их transit/terminal.",
    "Достижимость от seed не доказывает прохождение конкретных денег по маршруту.",
    "Сопоставление за 0–2 дня — временная совместимость, не трассировка денег; порядок внутри дня неизвестен.",
    "Операции ниже 5000 KZT и внешние переводы могут отсутствовать в исходной выборке.",
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def integers(series: pd.Series, label: str) -> pd.Series:
    """Never convert a large integer identifier through float64."""
    result = []
    for value in series:
        require(not pd.isna(value), f"{label}: обнаружено пустое значение.")
        require(not isinstance(value, (bool, np.bool_)), f"{label}: ожидается целое число.")
        if isinstance(value, (float, np.floating)):
            require(math.isfinite(value) and value.is_integer() and abs(value) < 2**53,
                    f"{label}: дробный или небезопасный float; gid должен храниться как int64 или строка.")
            number = int(value)
        else:
            text = str(value).strip()
            require(re.fullmatch(r"[+-]?\d+", text) is not None, f"{label}: неверное целое {text!r}.")
            number = int(text)
        require(-(2**63) <= number < 2**63, f"{label}: значение выходит за пределы int64.")
        result.append(number)
    return pd.Series(result, index=series.index, dtype="int64")


def cents(series: pd.Series, label: str) -> pd.Series:
    result = []
    for value in series:
        try:
            amount = Decimal(str(value)) * 100
            require(amount.is_finite() and amount > 0, f"{label}: сумма должна быть положительной.")
            rounded = amount.to_integral_value()
            require(abs(amount - rounded) <= Decimal("0.0001"),
                    f"{label}: больше двух значащих знаков после запятой: {value}.")
            require(rounded < 2**63, f"{label}: слишком большая сумма.")
            result.append(int(rounded))
        except InvalidOperation as exc:
            raise ValueError(f"{label}: неверная сумма {value!r}.") from exc
    require(sum(result) < 2**63, f"{label}: суммарный оборот превышает диапазон int64.")
    return pd.Series(result, index=series.index, dtype="int64")


def validate(nodes: pd.DataFrame, edges: pd.DataFrame, tx: pd.DataFrame):
    n, e, t = nodes.copy(), edges.copy(), tx.copy()
    schemas = [(n, "nodes", ["gid", "depth", "is_seed"]),
               (e, "edges", ["src", "dst", "sum_kzt", "n_tx", "depth"]),
               (t, "transactions", ["src", "dst", "date", "sum_kzt"])]
    for frame, name, columns in schemas:
        missing = sorted(set(columns) - set(frame.columns))
        require(not missing, f"{name}: отсутствуют колонки {missing}; есть {list(frame.columns)}.")
        require(not frame.columns.duplicated().any(), f"{name}: повторяющиеся названия колонок.")
        require(not frame[columns].isna().any().any(), f"{name}: обязательные колонки содержат пропуски.")
    require(len(n) > 0, "nodes.parquet пуст.")
    for frame, name, columns in [(n, "nodes", ["gid", "depth"]),
                                 (e, "edges", ["src", "dst", "n_tx", "depth"]),
                                 (t, "transactions", ["src", "dst"])]:
        for column in columns:
            frame[column] = integers(frame[column], f"{name}.{column}")
    flags = n["is_seed"].astype(str).str.strip().str.lower()
    flag_map = {"true": True, "false": False, "1": True, "0": False}
    require(flags.isin(flag_map).all(), "nodes.is_seed: разрешены true/false или 1/0.")
    n["is_seed"] = flags.map(flag_map).astype(bool)
    require(n["gid"].is_unique, "nodes.gid: повторяющиеся идентификаторы.")
    require(n["depth"].between(0, CONFIG["max_depth"]).all(), "nodes.depth: ожидается диапазон 0–4.")
    require((n["is_seed"] == n["depth"].eq(0)).all(), "nodes: is_seed не согласован с depth=0.")
    require(e["depth"].between(0, CONFIG["max_depth"]).all(), "edges.depth: ожидается диапазон 0–4.")
    require(e["n_tx"].gt(0).all(), "edges.n_tx: число транзакций должно быть положительным.")
    require(not e.duplicated(["src", "dst"]).any(), "edges: должна быть одна строка на пару src→dst.")
    gids = set(n["gid"])
    for frame, name in [(e, "edges"), (t, "transactions")]:
        unknown = (set(frame["src"]) | set(frame["dst"])) - gids
        require(not unknown, f"{name}: идентификаторы отсутствуют в nodes: {sorted(unknown)[:3]}.")
        frame["_cents"] = cents(frame["sum_kzt"], name + ".sum_kzt")
        frame["sum_kzt"] = frame["_cents"] / 100.0
    require(not pd.api.types.is_numeric_dtype(t["date"]), "transactions.date: требуется дата, не числовой код.")
    try:
        t["date"] = pd.to_datetime(t["date"], errors="raise", format="mixed")
        if t["date"].dt.tz is not None:
            t["date"] = t["date"].dt.tz_localize(None)
        t["date"] = t["date"].dt.normalize()
        require(not t["date"].isna().any(), "transactions.date: пустая дата NaT.")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("transactions.date: неверный или неоднородный формат дат.") from exc
    aggregate = t.groupby(["src", "dst"], sort=True).agg(
        tx_cents=("_cents", "sum"), tx_count=("_cents", "size"))
    check = e.set_index(["src", "dst"])[["_cents", "n_tx"]].join(aggregate, how="outer")
    mismatch = check.isna().any(axis=1) | check["_cents"].ne(check["tx_cents"]) | check["n_tx"].ne(check["tx_count"])
    require(not mismatch.any(),
            f"edges и transactions не совпадают по суммам/числу операций: {list(check.index[mismatch][:3])}.")
    return (n.sort_values("gid").reset_index(drop=True),
            e.sort_values(["src", "dst"]).reset_index(drop=True),
            t.sort_values(["date", "src", "dst", "_cents"]).reset_index(drop=True))


def percentile(series: pd.Series) -> pd.Series:
    result = pd.Series(0.0, index=series.index)
    positive = series.gt(0) & np.isfinite(series)
    result.loc[positive] = series.loc[positive].rank(method="average", pct=True)
    return result


def pagerank_numpy(graph: nx.DiGraph) -> dict:
    """Weighted PageRank without an extra SciPy dependency."""
    gids = list(graph)
    index = {gid: i for i, gid in enumerate(gids)}
    size = len(gids)
    src, dst, weights = [], [], []
    for u, v, attrs in graph.edges(data=True):
        src.append(index[u]); dst.append(index[v]); weights.append(attrs["weight"])
    src, dst, weights = np.array(src, dtype=int), np.array(dst, dtype=int), np.array(weights, dtype=float)
    totals = np.bincount(src, weights=weights, minlength=size)
    transition = weights / totals[src] if len(src) else weights
    dangling = totals == 0
    score = np.full(size, 1.0 / size)
    for _ in range(500):
        nxt = np.full(size, (0.15 + 0.85 * score[dangling].sum()) / size)
        nxt += 0.85 * np.bincount(dst, weights=score[src] * transition, minlength=size)
        if np.abs(nxt - score).sum() < 1e-12:
            return dict(zip(gids, nxt / nxt.sum()))
        score = nxt
    raise RuntimeError("PageRank не сошёлся за 500 итераций.")


def temporal_features(tx: pd.DataFrame, gids: pd.Index) -> pd.DataFrame:
    result = pd.DataFrame(0.0, index=gids, columns=["matched_2d_kzt", "fast_flow_ratio", "max_senders_same_day"])
    incoming = tx.groupby(["dst", "date"])["_cents"].sum()
    outgoing = tx.groupby(["src", "date"])["_cents"].sum()
    incoming.index.names = outgoing.index.names = ["gid", "date"]
    daily = pd.concat([incoming.rename("inc"), outgoing.rename("out")], axis=1).fillna(0)
    daily = daily.astype("int64").reset_index().sort_values(["gid", "date"])
    for gid, group in daily.groupby("gid", sort=True):
        queue, matched = deque(), 0
        total_in = int(group["inc"].sum())
        for row in group.itertuples(index=False):
            while queue and (row.date - queue[0][0]).days > CONFIG["fast_window_days"]:
                queue.popleft()
            if row.inc:
                queue.append([row.date, int(row.inc)])
            remaining = int(row.out)
            while remaining > 0 and queue:
                used = min(remaining, queue[0][1])
                remaining -= used; queue[0][1] -= used; matched += used
                if queue[0][1] == 0:
                    queue.popleft()
        result.loc[gid, "matched_2d_kzt"] = matched / 100.0
        result.loc[gid, "fast_flow_ratio"] = matched / total_in if total_in else 0.0
    sync = tx.groupby(["dst", "date"])["src"].nunique().groupby(level=0).max()
    result["max_senders_same_day"] = sync.reindex(gids, fill_value=0).astype("int64")
    return result


def compute_features(n: pd.DataFrame, e: pd.DataFrame, t: pd.DataFrame):
    graph, undirected = nx.DiGraph(), nx.Graph()
    graph.add_nodes_from(n["gid"].tolist())
    undirected.add_nodes_from(n["gid"].tolist())
    external = e.loc[e["src"].ne(e["dst"])].copy()
    for row in external.itertuples(index=False):
        u, v = int(row.src), int(row.dst)
        graph.add_edge(u, v, weight=float(row.sum_kzt), n_tx=int(row.n_tx))
        old = undirected.get_edge_data(u, v, {}).get("weight", 0.0)
        undirected.add_edge(u, v, weight=old + float(row.sum_kzt))
    if undirected.number_of_edges():
        communities = nx.community.louvain_communities(
            undirected, weight="weight", resolution=CONFIG["louvain_resolution"], seed=CONFIG["random_seed"])
        communities = [part for community in communities
                       for part in nx.connected_components(undirected.subgraph(community))]
    else:
        communities = [{gid} for gid in graph]
    communities.sort(key=lambda part: (-len(part), min(part)))
    cluster = {gid: cid for cid, part in enumerate(communities) for gid in part}
    components = sorted(nx.connected_components(undirected), key=lambda part: (-len(part), min(part)))
    component = {gid: cid for cid, part in enumerate(components) for gid in part}
    f = n.set_index("gid").copy()
    f["cluster_id"] = pd.Series(cluster).reindex(f.index).astype("int64")
    f["component_id"] = pd.Series(component).reindex(f.index).astype("int64")
    for name, values in [("in_degree", dict(graph.in_degree())), ("out_degree", dict(graph.out_degree()))]:
        f[name] = pd.Series(values).reindex(f.index).astype("int64")
    for side, endpoint in [("incoming", "dst"), ("outgoing", "src")]:
        grouped = external.groupby(endpoint)
        f[side + "_sum"] = grouped["_cents"].sum().reindex(f.index, fill_value=0) / 100.0
        f[side + "_tx_count"] = grouped["n_tx"].sum().reindex(f.index, fill_value=0).astype("int64")
    f["pass_ratio"] = f["outgoing_sum"].div(f["incoming_sum"].replace(0, np.nan))
    f["is_boundary"] = f["depth"].ge(CONFIG["max_depth"])
    f["is_isolated"] = (f["in_degree"] + f["out_degree"]).eq(0)
    f["pagerank"] = pd.Series(pagerank_numpy(graph)).reindex(f.index)
    approximate = len(graph) > 5000
    between = nx.betweenness_centrality(graph, weight=None, normalized=True,
                                       k=min(256, len(graph)) if approximate else None,
                                       seed=CONFIG["random_seed"])
    f["betweenness"] = pd.Series(between).reindex(f.index)
    f["external_clusters"] = [len({cluster[v] for v in undirected[gid]} - {cluster[gid]}) for gid in f.index]
    f["n_seed_reachable"] = 0
    f["min_seed_distance"] = -1
    for seed in n.loc[n["is_seed"], "gid"]:
        reach = nx.single_source_shortest_path_length(graph, int(seed), cutoff=CONFIG["max_depth"])
        for gid, distance in reach.items():
            if gid != seed:
                f.at[gid, "n_seed_reachable"] += 1
            old = f.at[gid, "min_seed_distance"]
            if old < 0 or distance < old:
                f.at[gid, "min_seed_distance"] = distance
    f = f.join(temporal_features(t.loc[t["src"].ne(t["dst"])], f.index))
    f["same_day_senders"] = f.pop("max_senders_same_day")
    f["data_warning"] = ""
    for gid, row in f.iterrows():
        notes = []
        if row.is_boundary:
            notes.append("Граница depth=4; исходящие не наблюдаются полностью")
        if row.is_seed:
            notes.append("Seed: входящие неполны")
        if row.is_isolated:
            notes.append("Нет межклиентских переводов в выборке")
        if row.outgoing_sum > row.incoming_sum:
            notes.append("Исходящие больше наблюдаемых входящих; полный баланс неизвестен")
        f.at[gid, "data_warning"] = "; ".join(notes)
    return f, graph, undirected, approximate


def assign_roles(f: pd.DataFrame) -> pd.DataFrame:
    f = f.copy()
    di, do = percentile(f["in_degree"]), percentile(f["out_degree"])
    mi, mo = percentile(f["incoming_sum"]), percentile(f["outgoing_sum"])
    bt = percentile(f["betweenness"])
    pr = percentile(f["pagerank"].where(f["in_degree"].gt(0), 0))
    sc = percentile(f["n_seed_reachable"])
    cc = percentile(f["external_clusters"])
    degree = (f["in_degree"] + f["out_degree"]).replace(0, 1)
    ratio = f["pass_ratio"]
    scores = pd.DataFrame(0.0, index=f.index, columns=ROLES)
    mask = f["in_degree"].ge(CONFIG["consolidator_min_senders"])
    scores.loc[mask, "consolidator"] = (0.50 + 0.50 * (0.55 * di + 0.30 * mi + 0.15 * f["in_degree"] / degree))[mask]
    mask = f["out_degree"].ge(CONFIG["distributor_min_receivers"])
    scores.loc[mask, "distributor"] = (0.50 + 0.50 * (0.55 * do + 0.30 * mo + 0.15 * f["out_degree"] / degree))[mask]
    ratio_usable = ~f["is_seed"] & ~f["is_boundary"] & f["incoming_sum"].gt(0)
    mask = ratio_usable & f["out_degree"].gt(0) & ratio.between(CONFIG["transit_ratio_min"], CONFIG["transit_ratio_max"])
    fit = (1 - (ratio - 1).abs() / 0.20).clip(0, 1)
    volume = ((f["incoming_tx_count"] + f["outgoing_tx_count"]) / 6).clip(0, 1)
    scores.loc[mask, "transit"] = (0.45 + 0.55 * (0.50 * fit + 0.35 * f["fast_flow_ratio"] + 0.15 * volume))[mask]
    mask = ratio_usable & ratio.le(CONFIG["terminal_ratio_max"])
    scores.loc[mask, "terminal"] = (0.40 + 0.40 * (0.65 * (1 - ratio).clip(0, 1) + 0.35 * mi))[mask]
    mask = (~f["is_boundary"] & f["in_degree"].gt(0) & f["out_degree"].gt(0)
            & degree.ge(4) & bt.ge(CONFIG["coordinator_betweenness_percentile"])
            & f["betweenness"].gt(0)
            & (f["external_clusters"].ge(2) | f["n_seed_reachable"].ge(3)))
    scores.loc[mask, "coordinator"] = (0.50 + 0.50 * (0.45 * bt + 0.20 * pr + 0.20 * cc + 0.15 * sc))[mask]
    scores.loc[f["is_boundary"]] = scores.loc[f["is_boundary"]].clip(upper=CONFIG["boundary_role_cap"])
    scores = scores.fillna(0).clip(0, 1)
    f["role"] = scores.idxmax(axis=1)
    f["role_score"] = scores.max(axis=1)
    fallback = f["role_score"].eq(0)
    f.loc[fallback, "role"] = "peripheral"
    f.loc[fallback, "role_score"] = 0.35
    f.loc[fallback & (f["is_boundary"] | f["is_isolated"]), "role_score"] = 0.15
    for role in ROLES:
        f[role + "_score"] = scores[role]
    f["priority_money"] = percentile(f["incoming_sum"] + f["outgoing_sum"])
    f["priority_structure"] = 0.65 * bt + 0.35 * pr
    proximity = 1 / (1 + f["min_seed_distance"].clip(lower=0))
    proximity = proximity.where(f["min_seed_distance"].ge(0), 0.0)
    f["priority_seed"] = 0.70 * (f["n_seed_reachable"] / 5).clip(0, 1) + 0.30 * proximity
    f["priority_role"] = f["role_score"].where(f["role"].ne("peripheral"), 0.0)
    f["priority_score"] = (0.30 * f["priority_money"] + 0.25 * f["priority_structure"]
                           + 0.20 * f["priority_seed"] + 0.25 * f["priority_role"])
    f.loc[f["is_boundary"], "priority_score"] *= CONFIG["boundary_priority_factor"]
    f.loc[f["is_isolated"], "priority_score"] = 0.0
    f["priority_score"] = f["priority_score"].clip(0, 1).round(6)
    f["role_score"] = f["role_score"].round(6)
    f["evidence"] = [evidence(row) for row in f.itertuples()]
    f["why"] = [
        f"{row.evidence} Приоритет: поток={row.priority_money:.2f}; "
        f"структура={row.priority_structure:.2f}; seed-связи={row.priority_seed:.2f}; "
        f"сила роли={row.priority_role:.2f}." for row in f.itertuples()
    ]
    return f


def evidence(row) -> str:
    if row.role == "consolidator":
        text = f"Признаки сбора: {row.in_degree} отправителей; вход {row.incoming_sum:,.2f} KZT; получателей {row.out_degree}."
    elif row.role == "distributor":
        text = f"Признаки распределения: {row.out_degree} получателей; выход {row.outgoing_sum:,.2f} KZT; отправителей {row.in_degree}."
    elif row.role == "transit":
        text = f"Признаки транзита: выход/вход={row.pass_ratio:.2f}; совместимо за 0–2 дня {row.fast_flow_ratio:.0%} входа."
    elif row.role == "terminal":
        text = f"Наблюдаемый сток: вход {row.incoming_sum:,.2f} KZT; выход/вход={row.pass_ratio:.2f}; depth={row.depth}. Не полный баланс."
    elif row.role == "coordinator":
        text = f"Структурный посредник: betweenness={row.betweenness:.4g}; внешних кластеров {row.external_clusters}; достижим от {row.n_seed_reachable} seed."
    elif row.is_isolated:
        text = "Нет межклиентских переводов в выборке; данных для содержательной роли недостаточно."
    else:
        text = f"Выраженных критериев ролей нет: отправителей {row.in_degree}, получателей {row.out_degree}; требуется проверка."
    if row.is_boundary:
        text += " Граница depth=4: исходящие обрезаны."
    elif row.is_seed:
        text += " Seed: входящие неполны."
    return text[:200]


def analyze(nodes: pd.DataFrame, edges: pd.DataFrame, transactions: pd.DataFrame, top_n: int = 50) -> dict:
    started = time.perf_counter()
    n, e, t = validate(nodes, edges, transactions)
    f, graph, undirected, approximate = compute_features(n, e, t)
    f = assign_roles(f)
    ranked = f.reset_index().sort_values(["priority_score", "gid"], ascending=[False, True]).reset_index(drop=True)
    ranked.insert(0, "rank", np.arange(1, len(ranked) + 1, dtype="int64"))
    top = ranked.head(min(len(ranked), max(20, top_n)))[TOP_COLUMNS].copy()
    cluster_map = f["cluster_id"].to_dict()
    internal = e.loc[e["src"].map(cluster_map).eq(e["dst"].map(cluster_map))].copy()
    internal["cluster_id"] = internal["src"].map(cluster_map)
    sums = internal.groupby("cluster_id")["_cents"].sum().to_dict()
    clusters = []
    for cid, group in f.groupby("cluster_id", sort=True):
        counts = group["role"].value_counts().to_dict()
        selected = ranked.loc[ranked["cluster_id"].eq(cid), "gid"].head(5)
        hypothesis = ("Изолированный узел: нет межклиентских переводов." if group["is_isolated"].all() else
                      f"Гипотеза о структуре: сборщиков {counts.get('consolidator', 0)}, "
                      f"распределителей {counts.get('distributor', 0)}, транзитных {counts.get('transit', 0)}. "
                      "Назначение сообщества требует проверки.")
        clusters.append({"cluster_id": int(cid), "n_nodes": len(group), "n_seed": int(group["is_seed"].sum()),
                         "sum_kzt_internal": int(sums.get(cid, 0)) / 100.0,
                         "top_gids": json.dumps([str(gid) for gid in selected]), "hypothesis": hypothesis})
    cluster_df = pd.DataFrame(clusters, columns=CLUSTER_COLUMNS)
    notes = []
    duplicate_tx = int(t.duplicated(["src", "dst", "date", "_cents"]).sum())
    if duplicate_tx:
        notes.append(f"{duplicate_tx} совпадающих строк транзакций сохранены: без transaction_id нельзя считать их ошибочными дублями.")
    if not n["is_seed"].any():
        notes.append("В nodes нет seed; признаки связи с исходными клиентами равны нулю.")
    if len(n) < 20:
        notes.append("Вход содержит меньше 20 узлов; top_nodes содержит все узлы без искусственного дублирования.")
    distance_mismatch = int(f["min_seed_distance"].ne(f["depth"]).sum())
    if distance_mismatch:
        notes.append(f"У {distance_mismatch} узлов заданный depth отличается от вычисленной достижимости. Исходный depth сохранён.")
    if len(t) and t["sum_kzt"].lt(5000).any():
        notes.append("Обнаружены операции ниже заявленного порога 5000 KZT; они не удалены.")
    report = {
        "schema_version": "1.0", "nodes": len(n), "edges": len(e), "transactions": len(t),
        "seed_count": int(n["is_seed"].sum()), "boundary_count": int(f["is_boundary"].sum()),
        "isolated_count": int(f["is_isolated"].sum()),
        "components_including_isolates": nx.number_connected_components(undirected),
        "components_with_edges": sum(len(c) > 1 for c in nx.connected_components(undirected)),
        "clusters": len(cluster_df), "total_kzt": int(e["_cents"].sum()) / 100.0,
        "self_loop_edges": int(e["src"].eq(e["dst"]).sum()),
        "period_start": t["date"].min().strftime("%Y-%m-%d") if len(t) else None,
        "period_end": t["date"].max().strftime("%Y-%m-%d") if len(t) else None,
        "role_counts": {key: int(value) for key, value in f["role"].value_counts().items()},
        "betweenness_mode": "sampled_256" if approximate else "exact_unweighted_directed",
        "config": CONFIG, "limitations": LIMITATIONS, "warnings": notes,
        "versions": {"python": sys.version.split()[0], "pandas": pd.__version__,
                     "numpy": np.__version__, "networkx": nx.__version__},
        "analysis_seconds": round(time.perf_counter() - started, 3),
    }
    result = {"features": f.reset_index(), "nodes_roles": f.reset_index()[NODE_COLUMNS],
              "clusters": cluster_df, "top_nodes": top, "edges": e, "transactions": t, "report": report}
    check_outputs(result)
    return result


def check_outputs(result: dict) -> None:
    nr, clusters, top = result["nodes_roles"], result["clusters"], result["top_nodes"]
    require(nr["gid"].is_unique and len(nr) == result["report"]["nodes"], "Потеряны/продублированы узлы.")
    require(not nr.isna().any().any(), "Пропуски в nodes_roles.")
    require(nr["role"].isin(ROLES + ["peripheral"]).all(), "Роль вне словаря.")
    for column in ["role_score", "priority_score"]:
        require(nr[column].between(0, 1).all(), f"{column} вне диапазона 0–1.")
    require(nr["evidence"].str.len().between(1, 200).all(), "Неверная длина evidence.")
    require(nr["cluster_id"].isin(clusters["cluster_id"]).all(), "Неизвестный кластер.")
    require(int(clusters["n_nodes"].sum()) == len(nr), "Размеры кластеров не сходятся.")
    require(top["gid"].is_unique and len(top) >= min(20, len(nr)), "Неверный top_nodes.")
    require(top["priority_score"].is_monotonic_decreasing, "Top nodes не отсортированы.")
    f = result["features"]
    require(not (f["is_boundary"] & f["role"].eq("terminal")).any(), "На границе ошибочно назначена роль terminal.")


def records(frame: pd.DataFrame, id_columns=()) -> list:
    """Convert identifiers to strings BEFORE JSON encoding, never through float."""
    frame = frame.copy()
    for column in id_columns:
        frame[column] = frame[column].map(str)
    return json.loads(frame.to_json(orient="records", force_ascii=False, date_format="iso", double_precision=12))


def export(result: dict, output_dir: Path) -> None:
    """Write CSV rows and JSON objects incrementally, without full text copies."""
    import csv
    import gc
    import tempfile
    from contextlib import contextmanager

    gc.collect()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def scalar(value):
        if value is None or value is pd.NA or value is pd.NaT:
            return None
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        return value

    @contextmanager
    def target(name):
        # Close the file before replacing it: required on Windows.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8-sig", newline="",
                dir=output_dir, prefix=f".{name}.", suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                yield stream
            temporary.replace(output_dir / name)
            print(f"[SAVE] {name}")
        except PermissionError as exc:
            raise PermissionError(
                f"Нет доступа к {name}. Закрой файл в Excel/другой "
                "программе и проверь права на папку output."
            ) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    # itertuples preserves integer IDs; do not use iterrows or float casts.
    for name in ("nodes_roles", "clusters", "top_nodes", "features"):
        frame = result[name]
        with target(name + ".csv") as stream:
            writer = csv.writer(stream)
            writer.writerow(frame.columns)
            for row in frame.itertuples(index=False, name=None):
                writer.writerow(scalar(value) for value in row)

    def dump(value, stream):
        json.dump(
            value, stream, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        )

    def write_array(stream, frame, kind, columns=None):
        # Select values by tuple position, avoiding a full DataFrame copy.
        names = list(frame.columns) if columns is None else list(columns)
        positions = [frame.columns.get_loc(name) for name in names]
        stream.write("[")
        first = True
        for values in frame.itertuples(index=False, name=None):
            row = {
                name: scalar(values[position])
                for name, position in zip(names, positions)
            }
            if kind in ("nodes", "top_nodes"):
                row["gid"] = str(row["gid"])
                if kind == "nodes":
                    row["id"] = row["gid"]
            elif kind == "edges":
                row["src"] = str(row["src"])
                row["dst"] = str(row["dst"])
                row["source"] = row["src"]
                row["target"] = row["dst"]
                row["id"] = row["src"] + "->" + row["dst"]
            elif kind == "clusters":
                row["top_gids"] = json.loads(row["top_gids"])
            if not first:
                stream.write(",")
            dump(row, stream)
            first = False
        stream.write("]")

    with target("graph.json") as stream:
        stream.write('{"schema_version":"1.0","meta":')
        dump(result["report"], stream)
        stream.write(',"nodes":')
        write_array(stream, result["features"], "nodes")
        stream.write(',"edges":')
        write_array(
            stream, result["edges"], "edges",
            ("src", "dst", "sum_kzt", "n_tx", "depth"),
        )
        stream.write(',"clusters":')
        write_array(stream, result["clusters"], "clusters")
        stream.write(',"top_nodes":')
        write_array(stream, result["top_nodes"], "top_nodes")
        stream.write("}")

    with target("report.json") as stream:
        json.dump(
            result["report"], stream,
            ensure_ascii=False, allow_nan=False, indent=2,
        )


def run(data_dir: Path = BASE / "data", output_dir: Path = BASE / "output", top_n: int = 50) -> dict:
    started = time.perf_counter()
    data_dir, output_dir = Path(data_dir), Path(output_dir)
    tables, hashes = {}, {}
    for name in ["nodes", "edges", "transactions"]:
        path = data_dir / (name + ".parquet")
        require(path.is_file(), f"Не найден файл: {path.resolve()}")
        try:
            tables[name] = pd.read_parquet(path, engine="pyarrow")
        except ImportError as exc:
            raise ValueError('Установи Parquet-движок: python -m pip install "pyarrow>=17,<30"') from exc
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"[LOAD] {path.name}: {len(tables[name]):,} rows")
    print("[ANALYZE] Validation, metrics, clusters, roles and ranking...")
    result = analyze(tables["nodes"], tables["edges"], tables["transactions"], top_n)
    result["report"]["input_sha256"] = hashes
    export(result, output_dir)
    seconds = time.perf_counter() - started
    print(f"[OK] {len(result['nodes_roles'])} nodes; {len(result['clusters'])} clusters; {seconds:.2f} sec")
    print(f"[OUTPUT] {output_dir.resolve()}")
    print(result["top_nodes"][["rank", "gid", "role", "priority_score"]].head(10).to_string(index=False))
    if seconds > 300:
        print("[WARNING] The full run exceeded the 5-minute requirement.")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Explainable Money Graph analysis")
    parser.add_argument("--data", type=Path, default=BASE / "data")
    parser.add_argument("--output", type=Path, default=BASE / "output")
    parser.add_argument("--top", type=int, default=50)
    args = parser.parse_args()
    try:
        run(args.data, args.output, args.top)
    except MemoryError as exc:
        print(f"[ERROR] Недостаточно памяти: {exc}", file=sys.stderr)
        print(
            "Закрой лишние программы и повтори запуск. Если ошибка сохраняется, "
            "проверь разрядность Python и выделенную память (Committed) "
            "в Диспетчере задач Windows.",
            file=sys.stderr,
        )
        sys.exit(1)
    except (ValueError, OSError, RuntimeError, nx.NetworkXException) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
