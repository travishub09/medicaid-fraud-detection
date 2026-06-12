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
    return tables


def run(tables: dict[str, pd.DataFrame], out_dir: Path) -> dict[str, pd.DataFrame]:
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

    log("Computing graph features …")
    org_features = compute_graph_features(
        org_nodes, owner_nodes, exclusion_nodes, member_edges,
        owned_by_edges, excluded_in_edges, co_located_edges)
    require("features_one_per_org", len(org_features) == len(org_nodes),
            f"{len(org_features)} vs {len(org_nodes)}")

    log("Running ring detection …")
    shells = shared_address_shell_clusters(org_nodes)
    common_owners = common_owner_clusters(owned_by_edges, owner_nodes, excluded_in_edges)
    proximity = excluded_party_proximity(
        org_nodes, owner_nodes, exclusion_nodes, member_edges,
        owned_by_edges, excluded_in_edges, co_located_edges)
    rings = referral_rings()

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
        "rings/shared_address_shells": shells,
        "rings/common_owner_clusters": common_owners,
        "rings/excluded_party_proximity": proximity,
        "rings/referral_rings": rings,
    }
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
    (out_dir / "GRAPH_REPORT.md").write_text("".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=None,
                    help="dir with provider_dim/npi_xwalk/owner_edges/exclusions parquet")
    ap.add_argument("--out", default="/tmp/graph_out", help="output dir")
    ap.add_argument("--fixture", action="store_true",
                    help="use the in-repo synthetic fixture instead of --input")
    args = ap.parse_args()

    if args.fixture:
        from tests.fixtures.synthetic import build_synthetic_inputs
        tables = build_synthetic_inputs()
    else:
        if not args.input:
            ap.error("either --input <dir> or --fixture is required")
        tables = _load(Path(args.input))
    run(tables, Path(args.out))


if __name__ == "__main__":
    main()
