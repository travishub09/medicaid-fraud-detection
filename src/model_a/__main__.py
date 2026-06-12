"""
Model A v1 orchestrator — the heuristic ERV composite (Week-1/2 build item).

Joins company-grain anomaly concepts with the entity-graph features and ring
membership, computes scheme subscores → noisy-OR → sector prior × graph boost →
exposure → ERV, and writes the ranked table plus top-k target dossiers.

Run against real outputs (graph dir from src.entity_graph; features parquet with
one row per org carrying the v3 concept percentiles + payments):
    python -m src.model_a --graph-dir ~/Desktop/data/graph \
        --features ~/Desktop/data/detection/company_features.parquet \
        --out ~/Desktop/data/model_a

Run on the synthetic fixture (no real data; what tests use):
    python -m src.model_a --fixture --out /tmp/model_a_out
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .scheme_subscores import compute_subscores
from .scoring import expected_recoverable_value, graph_risk_boost
from .sector_priors import sector_prior_series
from .dossier import render_dossier


def log(m: str) -> None:
    print(m, flush=True)


def require(name: str, ok: bool, detail: str = "") -> None:
    log(f"    [assert {'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        raise AssertionError(f"ASSERTION FAILED: {name} — {detail}")


def run(org_nodes: pd.DataFrame, org_graph_features: pd.DataFrame,
        company_features: pd.DataFrame, shell_clusters: pd.DataFrame | None,
        common_owner_clusters: pd.DataFrame | None, out_dir: Path,
        top_k_dossiers: int = 10) -> pd.DataFrame:
    """Score every org; write erv_ranked.parquet, MODEL_A_REPORT.md, dossiers/."""
    n0 = len(org_nodes)
    df = org_nodes.merge(org_graph_features, on="org_node_id", how="left")
    require("graph_feature_join_no_fanout", len(df) == n0, f"{len(df)} vs {n0}")
    df = df.merge(company_features, on="org_node_id", how="left")
    require("company_feature_join_no_fanout", len(df) == n0, f"{len(df)} vs {n0}")
    df = df.set_index("org_node_id", drop=False)

    subscores, coverage = compute_subscores(df)
    boost = graph_risk_boost(df["org_node_id"], shell_clusters, common_owner_clusters)
    prior = sector_prior_series(df.get("primary_taxonomy", pd.Series("", index=df.index)))
    payments = df.get("payments", pd.Series(0.0, index=df.index)).fillna(0.0)

    scored = expected_recoverable_value(subscores, payments, prior,
                                        boost["graph_risk_boost"])
    out = pd.concat([df, subscores, boost.drop(columns=["graph_risk_boost"]),
                     scored, payments.rename("payments_joined")], axis=1)
    out = out.loc[:, ~out.columns.duplicated()].sort_values("erv", ascending=False)
    out["erv_rank"] = range(1, len(out) + 1)
    out = out.reset_index(drop=True)
    require("scored_one_row_per_org", len(out) == n0, f"{len(out)} vs {n0}")

    out_dir.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_dir / "erv_ranked.parquet", index=False)

    subscore_cols = [c for c in out.columns if c.startswith("subscore_")]
    dossier_dir = out_dir / "dossiers"
    dossier_dir.mkdir(exist_ok=True)
    for _, row in out.head(top_k_dossiers).iterrows():
        safe = str(row["org_node_id"]).replace(":", "_").replace("/", "_")
        (dossier_dir / f"{row['erv_rank']:03d}_{safe}.md").write_text(
            render_dossier(row, subscore_cols, coverage))

    _write_report(out, coverage, out_dir)
    log(f"Done — scored {n0} orgs; wrote {min(top_k_dossiers, n0)} dossiers to {out_dir}")
    return out


def _write_report(out: pd.DataFrame, coverage: dict, out_dir: Path) -> None:
    lines = ["# MODEL_A_REPORT — v1 heuristic ERV composite\n",
             "_Cold-start, label-free, explainable. Sector priors and recovery "
             "multipliers are documented placeholders pending the DOJ case DB "
             "(docs/platform/09 §6, GAPS #13)._\n",
             "\n## Feature coverage by scheme\n"]
    for scheme, feats in coverage.items():
        lines.append(f"- {scheme}: {', '.join(feats)}\n")
    lines.append("\n## Top 15 by ERV\n| rank | org | scheme | adj_prob | ERV |\n|--:|---|---|--:|--:|\n")
    for _, r in out.head(15).iterrows():
        lines.append(f"| {r['erv_rank']} | {(r.get('org_name') or r['org_node_id'])[:40]} "
                     f"| {r['scheme_hypothesis']} | {r['adjusted_prob']} "
                     f"| ${r['erv']:,.0f} |\n")
    lines.append(f"\n## Distribution\n- orgs scored: {len(out):,}\n"
                 f"- adjusted_prob ≥ 0.5: {int((out['adjusted_prob'] >= 0.5).sum()):,}\n"
                 f"- ERV > 0: {int((out['erv'] > 0).sum()):,}\n")
    (out_dir / "MODEL_A_REPORT.md").write_text("".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph-dir", default=None, help="output dir of src.entity_graph")
    ap.add_argument("--features", default=None,
                    help="parquet: org_node_id + concept percentiles + payments")
    ap.add_argument("--out", default="/tmp/model_a_out")
    ap.add_argument("--top-k", type=int, default=10, help="dossiers to render")
    ap.add_argument("--fixture", action="store_true",
                    help="build everything from the synthetic fixture")
    args = ap.parse_args()

    if args.fixture:
        from src.entity_graph.__main__ import run as run_graph
        from tests.fixtures.synthetic import build_synthetic_inputs, build_company_features
        graph_out = Path(args.out) / "_graph"
        outputs = run_graph(build_synthetic_inputs(), graph_out)
        org_nodes = outputs["nodes/org_nodes"]
        gf = outputs["org_graph_features"]
        feats = build_company_features(org_nodes)
        shells = outputs["rings/shared_address_shells"]
        owners = outputs["rings/common_owner_clusters"]
    else:
        if not args.graph_dir or not args.features:
            ap.error("--graph-dir and --features are required (or use --fixture)")
        g = Path(args.graph_dir)
        org_nodes = pd.read_parquet(g / "nodes" / "org_nodes.parquet")
        gf = pd.read_parquet(g / "org_graph_features.parquet")
        feats = pd.read_parquet(args.features)
        shells = pd.read_parquet(g / "rings" / "shared_address_shells.parquet")
        owners = pd.read_parquet(g / "rings" / "common_owner_clusters.parquet")

    run(org_nodes, gf, feats, shells, owners, Path(args.out), args.top_k)


if __name__ == "__main__":
    main()
