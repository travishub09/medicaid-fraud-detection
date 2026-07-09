"""
entity_graph orchestrator — build the canonical graph end to end.

Consumes the integration outputs and writes node tables, edge tables, per-org
graph features, and ring-detection results, with the repo's assertion-driven
"no silent fan-out / no dropped rows" discipline.

Run against the integrate.py outputs (defaults to the processed drop):
    python -m src.entity_graph --input ~/Desktop/data/processed --out ~/Desktop/data/graph

Run against the synthetic fixture (no real data needed; what CI/tests use):
    python -m src.entity_graph --fixture --out /tmp/graph_out
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .build_nodes import build_provider_nodes, build_owner_nodes, build_exclusion_nodes
from .resolve_entities import resolve_organizations
from .build_edges import (
    build_member_edges, build_owned_by_edges, build_excluded_in_edges, build_co_located_edges,
    build_reassignment_edges,
)
from .graph_features import compute_graph_features
from .ring_detection import (
    shared_address_shell_clusters, common_owner_clusters,
    excluded_party_proximity, referral_rings,
)

REQUIRED = ["provider_dim"]
OPTIONAL = ["npi_xwalk", "owner_edges", "exclusions"]


def log(m: str) -> None:
    print(m, flush=True)


def require(name: str, ok: bool, detail: str = "") -> None:
    log(f"    [assert {'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        raise AssertionError(f"ASSERTION FAILED: {name} — {detail}")


def _load(input_dir: Path) -> dict[str, pd.DataFrame]:
    tables: dict[str, pd.DataFrame] = {}
    for name in REQUIRED + OPTIONAL:
        path = input_dir / f"{name}.parquet"
        if path.exists():
            tables[name] = pd.read_parquet(path)
        elif name in REQUIRED:
            raise FileNotFoundError(f"required input missing: {path}")
        else:
            tables[name] = None
            log(f"    (optional input absent) {name}")
    # Supplementary exclusion sources (OpenSanctions, SAM, state licensing) drop
    # ``exclusions_<source>.parquet`` next to the LEIE ``exclusions.parquet``; all
    # are concatenated so every excluded-party feed becomes graph exclusion nodes
    # uniformly (widening within_2_hops_of_exclusion and the PU positive set).
    extra = sorted(input_dir.glob("exclusions_*.parquet"))
    if extra:
        frames = ([tables["exclusions"]] if tables.get("exclusions") is not None else [])
        for p in extra:
            frames.append(pd.read_parquet(p))
            log(f"    + merged exclusion source {p.name}")
        tables["exclusions"] = pd.concat(frames, ignore_index=True)
    return tables


def _discover_docgraph(path: Path | None, input_dir: Path | None) -> Path | None:
    """Resolve the DocGraph/CareSet shared-patient file path (the file itself is
    NEVER loaded into pandas — the 2022 CareSet release is ~210M rows / 8 GB, so
    the edge build streams it through DuckDB inside run()). Auto-discovers
    ``<input>/../preclean/docgraph/*.csv*`` when no explicit path is given."""
    if path is None and input_dir is not None:
        cand = sorted((input_dir.parent / "preclean" / "docgraph").glob("*.csv*"))
        path = cand[0] if cand else None
    if path is None:
        log("    (optional input absent) docgraph — no referral edges this build")
        return None
    log(f"    docgraph file found: {path.name} "
        f"({path.stat().st_size/1e6:,.0f} MB) — streamed via DuckDB during the build")
    return path


def run(tables: dict[str, pd.DataFrame], out_dir: Path,
        embeddings: bool = True,
        max_component_size: int = 150_000,
        max_colocation_cluster: int = 100,
        docgraph_path: Path | None = None,
        docgraph_min_patients: float = 20,
        docgraph_max_edges: int = 2_000_000,
        ring_top_edges: int = 200_000) -> dict[str, pd.DataFrame]:
    """Build the graph from in-memory tables; write parquet; return the outputs."""
    provider_dim = tables["provider_dim"]
    npi_xwalk = tables.get("npi_xwalk")
    owner_edges = tables.get("owner_edges")
    exclusions = tables.get("exclusions")
    n_npi = len(provider_dim)

    log("Building nodes …")
    provider_nodes = build_provider_nodes(provider_dim, npi_xwalk)
    owner_nodes = build_owner_nodes(owner_edges)
    exclusion_nodes = build_exclusion_nodes(exclusions)
    require("provider_nodes_one_per_npi", len(provider_nodes) == n_npi,
            f"{len(provider_nodes)} vs {n_npi}")

    log("Resolving canonical organizations …")
    org_nodes, npi_to_org = resolve_organizations(provider_dim, npi_xwalk, owner_edges)
    require("npi_partitioned_into_orgs", int(org_nodes["n_constituent_npis"].sum()) == n_npi,
            f"{int(org_nodes['n_constituent_npis'].sum())} vs {n_npi}")
    require("every_npi_resolved_once", npi_to_org["npi"].nunique() == n_npi and len(npi_to_org) == n_npi)

    log("Building edges …")
    member_edges = build_member_edges(npi_to_org)
    owned_by_edges = build_owned_by_edges(owner_edges, npi_to_org)
    excluded_in_edges = build_excluded_in_edges(provider_dim, owner_nodes, exclusions)
    co_located_edges = build_co_located_edges(org_nodes)
    require("member_edges_match_npis", len(member_edges) == n_npi,
            f"{len(member_edges)} vs {n_npi}")
    # optional affiliation layer: provider→group reassignment edges (sweep 2.7),
    # only when the Revalidation Reassignment file is supplied
    reassignment = tables.get("reassignment")
    reassigns_to_edges = build_reassignment_edges(reassignment, npi_to_org, org_nodes) \
        if reassignment is not None else None

    # optional referral layer: DocGraph/CareSet shared-patient pairs → org→org
    # refers_to edges (previously the adapter existed but was never invoked here,
    # so a dropped-in file was silently unused). Small in-memory frames (tests)
    # take the pandas path; a real file streams through DuckDB — the 2022
    # CareSet release is ~210M rows and must never enter pandas whole.
    docgraph = tables.get("docgraph")
    refers_to_edges = None
    if docgraph is not None and len(docgraph):
        from src.ingest_cms.docgraph import build_referral_edges
        refers_to_edges = build_referral_edges(docgraph, npi_to_org)
        log(f"    referral edges: {len(refers_to_edges):,} org→org refers_to "
            f"(from {len(docgraph):,} shared-patient pairs)")
    elif docgraph_path is not None:
        from src.ingest_cms.docgraph import build_referral_edges_duckdb
        log(f"    building referral edges from {Path(docgraph_path).name} "
            f"(min_patients={docgraph_min_patients}, streamed)…")
        refers_to_edges = build_referral_edges_duckdb(
            docgraph_path, npi_to_org, min_patients=docgraph_min_patients,
            max_edges=docgraph_max_edges)
        if refers_to_edges is None:
            log("    docgraph SKIPPED: file lacks from/to NPI columns")
        else:
            n_tot = refers_to_edges.attrs.get("n_org_pairs_total", len(refers_to_edges))
            log(f"    referral edges: kept {len(refers_to_edges):,} of {n_tot:,} "
                f"org→org pairs (top by shared-patient volume; threshold "
                f">={docgraph_min_patients} patients per NPI pair)")

    log("Computing graph features …")
    org_features = compute_graph_features(
        org_nodes, owner_nodes, exclusion_nodes, member_edges,
        owned_by_edges, excluded_in_edges, co_located_edges,
        max_colocation_cluster=max_colocation_cluster)
    require("features_one_per_org", len(org_features) == len(org_nodes),
            f"{len(org_features)} vs {len(org_nodes)}")

    log("Computing graph node embeddings …")
    from .graph_embeddings import compute_node_embeddings
    excl_ids = (set(exclusion_nodes["node_id"].astype(str))
                if exclusion_nodes is not None and len(exclusion_nodes) else set())
    if not embeddings:
        import networkx as nx
        node_embeddings = compute_node_embeddings(nx.Graph())   # schema-only, empty
        log("    embeddings SKIPPED (--no-embeddings); core graph features unaffected")
    else:
        # SciPy-sparse backend: holds the full national graph (~14M nodes) in a few GB
        # instead of 30-50, so embeddings run on a 16 GB machine. Mega-address
        # co-location edges are pruned (artifact blobs shatter into real clusters) and
        # giant components are size-capped — both keep memory bounded AND signal clean.
        from .graph_features import build_sparse_adjacency
        from .graph_embeddings_sparse import compute_node_embeddings_sparse
        nodes, A = build_sparse_adjacency(
            org_nodes, owner_nodes, exclusion_nodes, member_edges, owned_by_edges,
            excluded_in_edges, co_located_edges, max_colocation_cluster=max_colocation_cluster)
        node_embeddings = compute_node_embeddings_sparse(
            nodes, A, excl_ids, max_component_size=max_component_size)
        log(f"    embedded {len(node_embeddings):,} graph nodes (SciPy-sparse: DeepWalk "
            f"+ fraud-proximity + motifs; mega-address edges pruned, components "
            f"<= {max_component_size:,})")

    log("Running ring detection …")
    shells = shared_address_shell_clusters(org_nodes)
    common_owners = common_owner_clusters(owned_by_edges, owner_nodes, excluded_in_edges)
    proximity = excluded_party_proximity(
        org_nodes, owner_nodes, exclusion_nodes, member_edges,
        owned_by_edges, excluded_in_edges, co_located_edges)
    ring_input = refers_to_edges
    if ring_input is not None and len(ring_input) > ring_top_edges:
        log(f"    referral rings: cycle search capped to the top {ring_top_edges:,} "
            f"edges by volume (of {len(ring_input):,}; the FULL edge set is still "
            f"written to edges/refers_to_edges.parquet)")
        ring_input = ring_input.nlargest(ring_top_edges, "shared_patient_volume")
    rings = referral_rings(ring_input)

    outputs = {
        "nodes/provider_nodes": provider_nodes,
        "nodes/owner_nodes": owner_nodes,
        "nodes/exclusion_nodes": exclusion_nodes,
        "nodes/org_nodes": org_nodes,
        "edges/member_edges": member_edges,
        "edges/owned_by_edges": owned_by_edges,
        "edges/excluded_in_edges": excluded_in_edges,
        "edges/co_located_edges": co_located_edges,
        "npi_to_org": npi_to_org,
        "org_graph_features": org_features,
        "node_embeddings": node_embeddings,
        "rings/shared_address_shells": shells,
        "rings/common_owner_clusters": common_owners,
        "rings/excluded_party_proximity": proximity,
        "rings/referral_rings": rings,
    }
    if reassigns_to_edges is not None:
        outputs["edges/reassigns_to_edges"] = reassigns_to_edges
    if refers_to_edges is not None:
        outputs["edges/refers_to_edges"] = refers_to_edges
    out_dir.mkdir(parents=True, exist_ok=True)
    for rel, df in outputs.items():
        path = out_dir / f"{rel}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
    _write_report(outputs, out_dir)
    log(f"Done — wrote {len(outputs)} tables to {out_dir}")
    return outputs


def _write_report(outputs: dict[str, pd.DataFrame], out_dir: Path) -> None:
    feats = outputs["org_graph_features"]
    lines = ["# GRAPH_REPORT — entity-resolution graph\n",
             "_Canonical nodes/edges + graph features from the integration outputs. "
             "Read-only on inputs; assertion-driven build._\n\n## Table sizes\n"]
    for rel, df in outputs.items():
        lines.append(f"- `{rel}`: {len(df):,} rows\n")
    if len(feats):
        lines.append("\n## Graph-feature highlights\n")
        lines.append(f"- orgs within 2 hops of an exclusion: "
                     f"{int(feats['within_2_hops_of_exclusion'].sum()):,}\n")
        lines.append(f"- max related-party density: {int(feats['related_party_density'].max()):,}\n")
        lines.append(f"- max co-location cluster size: {int(feats['co_location_cluster_size'].max()):,}\n")
        lines.append(f"- orgs with shell_score >= 0.5: {int((feats['shell_score'] >= 0.5).sum()):,}\n")
    (out_dir / "GRAPH_REPORT.md").write_text("".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=None,
                    help="dir with provider_dim/npi_xwalk/owner_edges/exclusions parquet")
    ap.add_argument("--out", default="/tmp/graph_out", help="output dir")
    ap.add_argument("--fixture", action="store_true",
                    help="use the in-repo synthetic fixture instead of --input")
    ap.add_argument("--neo4j-bulk", default=None,
                    help="also write neo4j-admin bulk-import CSVs + import.sh "
                         "to this dir (offline Neo4j load; no server needed)")
    ap.add_argument("--asof", default=None,
                    help="build a POINT-IN-TIME graph: keep only exclusions/owner "
                         "relationships known before this date (YYYY-MM-DD) so the "
                         "embeddings/proximity/label are leakage-correct as-of then")
    ap.add_argument("--no-embeddings", action="store_true",
                    help="skip the DeepWalk node embeddings entirely; core graph "
                         "features are still computed")
    ap.add_argument("--max-component-size", type=int, default=150_000,
                    help="drop connected components larger than this before embedding "
                         "(giant shared-address artifact hairballs); keeps memory "
                         "bounded + signal clean. Lower it (e.g. 50000) on tight RAM.")
    ap.add_argument("--max-colocation-cluster", type=int, default=100,
                    help="treat an address shared by more than this many orgs as a "
                         "mail-drop (not a real co-location) and drop its edges from "
                         "the embedding graph, so genuine rings fused to the blob via "
                         "a shared mail drop are rescued. Raise to keep larger clusters.")
    ap.add_argument("--docgraph", default=None,
                    help="DocGraph/CareSet shared-patient csv (or .csv.gz) → refers_to "
                         "edges + referral rings. Default: auto-discover "
                         "<input>/../preclean/docgraph/*.csv*")
    ap.add_argument("--docgraph-min-patients", type=float, default=20,
                    help="drop NPI pairs sharing fewer patients than this before "
                         "aggregation (the 210M-row CareSet tail is mostly weak "
                         "1-5-patient pairs; the threshold is logged, never silent)")
    args = ap.parse_args()

    docgraph_path = None
    if args.fixture:
        from tests.fixtures.synthetic import build_synthetic_inputs
        tables = build_synthetic_inputs()
    else:
        if not args.input:
            ap.error("either --input <dir> or --fixture is required")
        tables = _load(Path(args.input))
        docgraph_path = _discover_docgraph(
            Path(args.docgraph) if args.docgraph else None, Path(args.input))
    if args.asof:
        from src.model_a.temporal_sources import point_in_time_tables
        _ex = tables.get("exclusions")
        n0 = len(_ex) if _ex is not None else 0
        tables = point_in_time_tables(tables, args.asof)
        _ex2 = tables.get("exclusions")
        log(f"    point-in-time as-of {args.asof}: "
            f"{len(_ex2) if _ex2 is not None else 0} of {n0} exclusions retained")
    run(tables, Path(args.out), embeddings=not args.no_embeddings,
        max_component_size=args.max_component_size,
        max_colocation_cluster=args.max_colocation_cluster,
        docgraph_path=docgraph_path,
        docgraph_min_patients=args.docgraph_min_patients)

    if args.neo4j_bulk:
        from .neo4j_export import write_bulk_import
        manifest = write_bulk_import(Path(args.out), Path(args.neo4j_bulk))
        log(f"Neo4j bulk import written to {args.neo4j_bulk}: {manifest}")


if __name__ == "__main__":
    main()
