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
OPTIONAL = ["npi_xwalk", "owner_edges", "exclusions", "reassignment"]


def log(m: str) -> None:
    print(m, flush=True)


def require(name: str, ok: bool, detail: str = "") -> None:
    log(f"    [assert {'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        raise AssertionError(f"ASSERTION FAILED: {name} — {detail}")


EXCLUSION_DEDUP_KEY = ["npi", "name_key", "excl_type", "excl_date"]


def dedup_exclusion_sources(ex: pd.DataFrame, log=log) -> pd.DataFrame:
    """One exclusion record = one node source row (audit C.F13, 2026-10-21).

    The integrate union (processed/exclusions.parquet) and a standalone adapter run
    (processed/exclusions_<source>.parquet) can both carry the same record; the graph
    used to concatenate every file with no de-duplication, so a record counted twice
    in fraud proximity, the per-se checks and the label-source tallies. Exact
    duplicates on (npi, name_key, excl_type, excl_date) are dropped here with the
    count logged; the files on disk are untouched (integrate already quarantines its
    own duplicates to exclusions_duplicates.parquet)."""
    if ex is None or not len(ex):
        return ex
    key = [c for c in EXCLUSION_DEDUP_KEY if c in ex.columns]
    if not key:
        return ex
    k = ex[key].astype(str)
    dup = k.duplicated(keep="first")
    n = int(dup.sum())
    if n:
        log(f"    [exclusions] {n:,} duplicate records across the exclusion files dropped "
            f"(key {'+'.join(key)}); {int((~dup).sum()):,} distinct records remain")
    return ex.loc[~dup].reset_index(drop=True)


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
            if p.name == "exclusions_duplicates.parquet":   # integrate's quarantine, never a node source
                continue
            frames.append(pd.read_parquet(p))
            log(f"    + merged exclusion source {p.name}")
        tables["exclusions"] = dedup_exclusion_sources(pd.concat(frames, ignore_index=True), log=log)
    # Reassignment (the CMS Revalidation Clinic Group Practice Reassignment file →
    # provider→group affiliation edges). Accept the raw CSV straight from
    # preclean/reassignment/ when no prebuilt parquet exists — the edge builder
    # resolves the raw CMS headers itself. This table was consumed by run() but
    # never listed in OPTIONAL, so it was silently None on every real build.
    if tables.get("reassignment") is None:
        raw = sorted((input_dir.parent / "preclean" / "reassignment").glob("*.csv"))
        if raw:
            from src.attempt_2.clean_data import read_csv_text, _resolve_columns
            from .build_edges import _REASSIGN_COLS
            # usecols: the raw file is millions of rows × ~20 columns; loading
            # only the 4 columns the edge builder resolves keeps a 16 GB box safe.
            header = list(read_csv_text(raw[0], nrows=0).columns)
            resolved = _resolve_columns(header, _REASSIGN_COLS)
            use = list(dict.fromkeys(resolved.values())) or None
            tables["reassignment"] = read_csv_text(raw[0], usecols=use)
            log(f"    + reassignment raw csv {raw[0].name} "
                f"({len(tables['reassignment']):,} rows, "
                f"{len(tables['reassignment'].columns)} cols projected)")
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


def docgraph_edition(path: Path | None) -> str | None:
    """YYYY-MM the file name implies (DocGraph_Hop_Teaming_2022 -> 2022-12); None when
    undated. The as-of build refuses an edition after the cutoff (PLATFORM_AUDIT D3:
    the 2022 file sat inside every 2021-12 frozen graph as refers_to edges)."""
    if path is None:
        return None
    from src.model_a.provider_features_export import _file_period
    return _file_period(Path(path).name)


def run(tables: dict[str, pd.DataFrame], out_dir: Path,
        embeddings: bool = True,
        max_component_size: int = 150_000,
        max_embed_nodes: int = 1_500_000,
        max_colocation_cluster: int = 100,
        light_walks_above: int = 1_000_000,
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
        # referral edges are an OPTIONAL enrichment: a parse failure on the 8 GB
        # scan must never kill a multi-hour graph build — log and continue.
        try:
            from src.ingest_cms.docgraph import build_referral_edges_duckdb
            log(f"    building referral edges from {Path(docgraph_path).name} "
                f"(min_patients={docgraph_min_patients}, streamed)…")
            refers_to_edges = build_referral_edges_duckdb(
                docgraph_path, npi_to_org, min_patients=docgraph_min_patients,
                max_edges=docgraph_max_edges)
            if refers_to_edges is None:
                log("    docgraph SKIPPED: file lacks from/to NPI columns")
            else:
                n_tot = refers_to_edges.attrs.get("n_org_pairs_total",
                                                  len(refers_to_edges))
                log(f"    referral edges: kept {len(refers_to_edges):,} of {n_tot:,} "
                    f"org→org pairs (top by shared-patient volume; threshold "
                    f">={docgraph_min_patients} patients per NPI pair)")
        except Exception as e:
            refers_to_edges = None
            log(f"    docgraph SKIPPED (build error, graph continues): {e}")

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
            nodes, A, excl_ids, max_component_size=max_component_size, max_embed_nodes=max_embed_nodes,
            light_walks_above=light_walks_above)
        log(f"    embedded {len(node_embeddings):,} graph nodes (SciPy-sparse: DeepWalk "
            f"+ fraud-proximity + motifs; mega-address edges pruned, components "
            f"<= {max_component_size:,})")

    log("Running ring detection …")
    shells = shared_address_shell_clusters(org_nodes)
    common_owners = common_owner_clusters(owned_by_edges, owner_nodes,
                                          excluded_in_edges, npi_to_org=npi_to_org)
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
    ap.add_argument("--asof-nppes", default=None,
                    help="historical NPPES edition (~cutoff month) to FREEZE the "
                         "co-location address layer, closing the shell_score leak. "
                         "Without it the address edges use today's NPPES. NBER "
                         "archives monthly editions.")
    ap.add_argument("--no-embeddings", action="store_true",
                    help="skip the DeepWalk node embeddings entirely; core graph "
                         "features are still computed")
    ap.add_argument("--max-embed-nodes", type=int, default=1_500_000,
                    help="embed at most this many connected nodes (fraud-proximate first, then by "
                         "degree); the rest keep degree + proximity with a zero vector. Lower it "
                         "(e.g. 500000) on tight RAM; 0 = no cap.")
    ap.add_argument("--light-walks-above", type=int, default=1_000_000,
                    help="above this many connected nodes the walks are lightened (4 walks of 12, "
                         "window 3, instead of 10 of 20, window 5) to fit a 16 GB box; 0 = never "
                         "lighten (full walks on every node: the VM setting).")
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
    if args.asof and docgraph_path is not None:
        ed = docgraph_edition(docgraph_path)
        cut = str(args.asof)[:7]
        if ed is None or ed > cut:
            log(f"    docgraph SKIPPED for the as-of build: edition {ed or 'undated'} is not on/before "
                f"{cut} (the 2022 file inside a 2021-12 freeze was the D3 leak); refers_to edges empty")
            docgraph_path = None
    if args.asof:
        from src.model_a.temporal_sources import point_in_time_tables
        _ex = tables.get("exclusions")
        n0 = len(_ex) if _ex is not None else 0
        asof_addr = None
        if args.asof_nppes:
            from src.entity_graph.asof_nppes import build_asof_addresses
            asof_addr = build_asof_addresses(args.asof_nppes, args.asof)
            log(f"    co-location FROZEN: {len(asof_addr):,} addresses from the "
                f"{args.asof} NPPES edition ({Path(args.asof_nppes).name})")
        else:
            log("    [WARN] co-location layer NOT frozen: address edges use "
                "TODAY's NPPES, which leaks post-cutoff structure into "
                "shell_score. Pass --asof-nppes <historical NPPES edition> to "
                "close it (NBER archives monthly editions).")
        tables = point_in_time_tables(tables, args.asof, asof_addresses=asof_addr)
        _ex2 = tables.get("exclusions")
        log(f"    point-in-time as-of {args.asof}: "
            f"{len(_ex2) if _ex2 is not None else 0} of {n0} exclusions retained")
    # Substrate provenance: the graph SAYS how it was built, so downstream (the
    # export's vintage classes, Travis's GATE-0) reads facts, not assumptions.
    addr_frozen = bool(tables.pop("_addr_frozen", False))
    import json as _json
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "graph_build_info.json").write_text(_json.dumps({
        "asof": args.asof,
        "address_layer_frozen": addr_frozen,
        "asof_nppes_edition": (Path(args.asof_nppes).name
                               if getattr(args, "asof_nppes", None) else None),
    }, indent=2), encoding="utf-8")
    run(tables, Path(args.out), embeddings=not args.no_embeddings,
        max_component_size=args.max_component_size,
        max_embed_nodes=args.max_embed_nodes,
        max_colocation_cluster=args.max_colocation_cluster,
        light_walks_above=args.light_walks_above,
        docgraph_path=docgraph_path,
        docgraph_min_patients=args.docgraph_min_patients)

    if args.neo4j_bulk:
        from .neo4j_export import write_bulk_import
        manifest = write_bulk_import(Path(args.out), Path(args.neo4j_bulk))
        log(f"Neo4j bulk import written to {args.neo4j_bulk}: {manifest}")


if __name__ == "__main__":
    main()
