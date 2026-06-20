"""
neo4j_export.py — load the relational entity graph into Neo4j.

The feature pipeline (``graph_features.py``) runs on the relational node/edge
tables with NetworkX and needs no graph database. Neo4j is the INTERACTIVE layer:
analysts walk ownership networks, hunt rings ad hoc in Cypher, and use Neo4j
Bloom / Browser for visualization — the queries illustrated in
``docs/platform/03-entity-resolution.md`` run directly against it.

Two load paths, both implemented and testable without a running server:

  write_bulk_import(graph_dir, out)   offline `neo4j-admin database import`:
        writes one nodes CSV per label and one rels CSV per type in Neo4j's
        bulk-import header format (``:ID`` / ``:LABEL`` / ``:START_ID`` /
        ``:END_ID`` / ``:TYPE``) plus a runnable import.sh. Fastest for a fresh
        DB; no driver dependency.
  export_to_neo4j(graph_dir, session) online load via the official ``neo4j``
        driver: idempotent batched ``UNWIND … MERGE`` (safe to re-run; updates
        in place). ``session`` is injected so tests run against a fake — no live
        server in CI (repo rule).

Node ids are the namespaced keys the graph already uses (``provider:<npi>``,
``org:<id>``, ``owner:<key>``, ``exclusion:<row>``), so MERGE is deterministic
and re-import never duplicates.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

# parquet table → (Neo4j label, id column). Properties = all other columns
# except node_type. Node ids are already globally unique + namespaced.
NODE_SPECS: dict[str, tuple[str, str]] = {
    "nodes/provider_nodes": ("Provider", "node_id"),
    "nodes/owner_nodes": ("Owner", "node_id"),
    "nodes/exclusion_nodes": ("Exclusion", "node_id"),
    "nodes/org_nodes": ("Org", "org_node_id"),
}
# edge table → the column holding the relationship type (uppercased for Neo4j).
EDGE_SPECS: dict[str, str] = {
    "edges/member_edges": "edge_type",
    "edges/owned_by_edges": "edge_type",
    "edges/excluded_in_edges": "edge_type",
    "edges/co_located_edges": "edge_type",
}
BATCH = 5_000

# Constraints make MERGE fast and enforce id uniqueness per label.
CONSTRAINTS: list[str] = [
    f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
    for label, _ in NODE_SPECS.values()
]

# Canned analyst queries — the ring patterns ring_detection.py computes, but
# clickable. Written to <out>/analyst_queries.cypher by write_bulk_import.
ANALYST_QUERIES: dict[str, str] = {
    "within_2_hops_of_exclusion":
        "MATCH (o:Org)-[:owned_by|member_of*1..2]-(x:Exclusion) "
        "RETURN o.id, o.org_name, collect(DISTINCT x.entity_name) AS exclusions "
        "ORDER BY size(exclusions) DESC LIMIT 50",
    "common_owner_clusters":
        "MATCH (o1:Org)-[:owned_by]->(w:Owner)<-[:owned_by]-(o2:Org) "
        "WHERE o1.id < o2.id "
        "RETURN w.owner_display_name, collect(DISTINCT o1.org_name) + "
        "collect(DISTINCT o2.org_name) AS orgs ORDER BY size(orgs) DESC LIMIT 50",
    "shared_address_shells":
        "MATCH (o1:Org)-[c:co_located]-(o2:Org) WHERE o1.id < o2.id "
        "RETURN c.addr_key, collect(DISTINCT o1.org_name) AS orgs, "
        "max(c.cluster_size) AS cluster_size ORDER BY cluster_size DESC LIMIT 50",
}


def _clean(v):
    """parquet cell → a Neo4j-safe scalar (NaN/NaT → None)."""
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
        return None
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _read(graph_dir: Path, rel: str) -> pd.DataFrame | None:
    p = Path(graph_dir) / f"{rel}.parquet"
    return pd.read_parquet(p) if p.exists() else None


def iter_node_batches(graph_dir: Path, batch: int = BATCH):
    """Yield (label, [ {id, props{}} ]) batches across all node tables."""
    for rel, (label, id_col) in NODE_SPECS.items():
        df = _read(graph_dir, rel)
        if df is None or not len(df):
            continue
        prop_cols = [c for c in df.columns if c not in (id_col, "node_type")]
        rows = []
        for r in df.itertuples(index=False):
            d = dict(zip(df.columns, r))
            rows.append({"id": str(d[id_col]),
                         "props": {c: _clean(d[c]) for c in prop_cols}})
            if len(rows) >= batch:
                yield label, rows
                rows = []
        if rows:
            yield label, rows


def iter_rel_batches(graph_dir: Path, batch: int = BATCH):
    """Yield (rel_type, [ {start, end, props{}} ]) batches across edge tables."""
    for rel, type_col in EDGE_SPECS.items():
        df = _read(graph_dir, rel)
        if df is None or not len(df):
            continue
        prop_cols = [c for c in df.columns
                     if c not in ("src_id", "dst_id", type_col)]
        for rtype, sub in df.groupby(type_col):
            rows = []
            for r in sub.itertuples(index=False):
                d = dict(zip(sub.columns, r))
                rows.append({"start": str(d["src_id"]), "end": str(d["dst_id"]),
                             "props": {c: _clean(d[c]) for c in prop_cols}})
                if len(rows) >= batch:
                    yield str(rtype), rows
                    rows = []
            if rows:
                yield str(rtype), rows


def _node_merge_cypher(label: str) -> str:
    return (f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) "
            f"SET n += row.props")


def _rel_merge_cypher(rtype: str) -> str:
    # rel type is validated to a safe identifier before interpolation
    safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in rtype)
    return (f"UNWIND $rows AS row MATCH (a {{id: row.start}}), (b {{id: row.end}}) "
            f"MERGE (a)-[r:{safe}]->(b) SET r += row.props")


def export_to_neo4j(graph_dir: Path, session=None, batch: int = BATCH) -> dict:
    """Idempotent online load via an injected Neo4j ``session``.

    ``session`` must expose ``run(cypher, **params)`` (the official driver's
    Session does). Creates constraints, then MERGEs nodes and relationships in
    batches. Returns counts. Re-running updates in place (MERGE), never
    duplicates. Pass a real session from::

        from neo4j import GraphDatabase
        drv = GraphDatabase.driver(uri, auth=(user, password))
        with drv.session() as s:
            export_to_neo4j(graph_dir, session=s)
    """
    if session is None:
        raise ValueError(
            "export_to_neo4j needs a Neo4j session; for a serverless load use "
            "write_bulk_import(graph_dir, out) and `neo4j-admin database import`.")
    for c in CONSTRAINTS:
        session.run(c)
    n_nodes = n_rels = 0
    for label, rows in iter_node_batches(graph_dir, batch):
        session.run(_node_merge_cypher(label), rows=rows)
        n_nodes += len(rows)
    for rtype, rows in iter_rel_batches(graph_dir, batch):
        session.run(_rel_merge_cypher(rtype), rows=rows)
        n_rels += len(rows)
    return {"nodes_loaded": n_nodes, "relationships_loaded": n_rels}


def write_bulk_import(graph_dir: Path, out_dir: Path) -> dict:
    """Offline path: write neo4j-admin bulk-import CSVs + import.sh.

    One nodes CSV per label (header ``id:ID,<props>,:LABEL``) and one rels CSV
    per type (header ``:START_ID,<props>,:END_ID,:TYPE``). Returns a manifest of
    files + row counts. Load with the generated import.sh on a STOPPED database.
    """
    out = Path(out_dir)
    (out / "nodes").mkdir(parents=True, exist_ok=True)
    (out / "rels").mkdir(parents=True, exist_ok=True)
    manifest: dict[str, int] = {}
    node_files, rel_files = [], []

    for rel, (label, id_col) in NODE_SPECS.items():
        df = _read(graph_dir, rel)
        if df is None or not len(df):
            continue
        prop_cols = [c for c in df.columns if c not in (id_col, "node_type")]
        fpath = out / "nodes" / f"{label.lower()}.csv"
        with open(fpath, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["id:ID"] + prop_cols + [":LABEL"])
            for r in df.itertuples(index=False):
                d = dict(zip(df.columns, r))
                w.writerow([str(d[id_col])]
                           + ["" if _clean(d[c]) is None else _clean(d[c])
                              for c in prop_cols] + [label])
        manifest[f"nodes/{label}"] = len(df)
        node_files.append(fpath)

    for rel, type_col in EDGE_SPECS.items():
        df = _read(graph_dir, rel)
        if df is None or not len(df):
            continue
        prop_cols = [c for c in df.columns
                     if c not in ("src_id", "dst_id", type_col)]
        fpath = out / "rels" / f"{rel.split('/')[-1]}.csv"
        with open(fpath, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow([":START_ID"] + prop_cols + [":END_ID", ":TYPE"])
            for r in df.itertuples(index=False):
                d = dict(zip(df.columns, r))
                w.writerow([str(d["src_id"])]
                           + ["" if _clean(d[c]) is None else _clean(d[c])
                              for c in prop_cols]
                           + [str(d["dst_id"]), str(d[type_col])])
        manifest[f"rels/{rel.split('/')[-1]}"] = len(df)
        rel_files.append(fpath)

    # import.sh runs on the (Linux) Neo4j host, so force POSIX forward-slash
    # paths regardless of the OS that generated the bundle (Windows would
    # otherwise bake in backslashes that the bash script can't use).
    nodes_arg = " ".join(f"--nodes={f.relative_to(out).as_posix()}"
                         for f in node_files)
    rels_arg = " ".join(f"--relationships={f.relative_to(out).as_posix()}"
                        for f in rel_files)
    (out / "import.sh").write_text(
        "#!/usr/bin/env bash\n"
        "# Run from this directory against a STOPPED Neo4j database.\n"
        "set -euo pipefail\n"
        f"neo4j-admin database import full {nodes_arg} {rels_arg} "
        "--overwrite-destination neo4j\n", encoding="utf-8")
    (out / "analyst_queries.cypher").write_text(
        "\n\n".join(f"// {name}\n{q};" for name, q in ANALYST_QUERIES.items()),
        encoding="utf-8")
    return manifest
