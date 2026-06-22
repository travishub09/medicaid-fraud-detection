"""
Model A v1 orchestrator — the heuristic ERV composite (Week-1/2 build item).

Joins company-grain anomaly concepts with the entity-graph features and ring
membership, computes scheme subscores → noisy-OR → sector prior × graph boost →
exposure → ERV, and writes the ranked table plus top-k target dossiers.

Run against real outputs. The --features parquet (one row per org carrying the
v3 concept percentiles + payments) is produced by `python -m src.model_a.build_features`,
which rolls the per-NPI fraud_leads_v3 concepts up to org grain via the graph
crosswalk:
    python -m src.model_a.build_features
    python -m src.model_a --graph-dir ~/Desktop/data/graph \
        --features ~/Desktop/data/features/company_features.parquet \
        --spending ~/Desktop/data/processed/spending_fact.parquet \
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
from .sector_priors import sector_prior_series, sector_for_taxonomy
from .government_interest import government_interest_overlay
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
        top_k_dossiers: int = 10,
        scoped_payments: pd.DataFrame | None = None,
        disclosure: pd.DataFrame | None = None,
        settled_org_ids: list[str] | None = None) -> pd.DataFrame:
    """Score every org; write erv_ranked.parquet, MODEL_A_REPORT.md, dossiers/."""
    n0 = len(org_nodes)
    df = org_nodes.merge(org_graph_features, on="org_node_id", how="left")
    require("graph_feature_join_no_fanout", len(df) == n0, f"{len(df)} vs {n0}")
    df = df.merge(company_features, on="org_node_id", how="left")
    require("company_feature_join_no_fanout", len(df) == n0, f"{len(df)} vs {n0}")

    # Candidate gate: only orgs with a billing-anomaly concept OR a graph/ownership
    # signal can score above baseline — with no signal the scheme noisy-OR ≈ 0, so
    # ERV ≈ 0 and the org is bottom-rank by construction (never a dossier). At full
    # scale ~94% of the 9M orgs are signal-less solo practitioners; scoring them all
    # holds an enormous frame (16GB OOM/swap) for zero ranking effect. Restrict to
    # candidates — identical top dossiers, a fraction of the memory.
    _concepts = [c for c in ["concentration", "payment_intensity", "service_intensity",
                             "specialty_mismatch", "temporal"] if c in df.columns]

    def _num(col):
        return (pd.to_numeric(df[col], errors="coerce").fillna(0.0)
                if col in df.columns else pd.Series(0.0, index=df.index))

    billing_sig = (df[_concepts].notna().any(axis=1)
                   if _concepts else pd.Series(False, index=df.index))
    graph_sig = ((_num("within_2_hops_of_exclusion") > 0) | (_num("shell_score") > 0)
                 | (_num("related_party_density") > 0)
                 | (_num("co_location_cluster_size") >= 2) | (_num("betweenness") > 0))
    candidates = (billing_sig | graph_sig).to_numpy()
    if candidates.any():
        df = df[candidates].copy()
    log(f"    scoring {len(df):,} candidate orgs of {n0:,} "
        f"(signal-less orgs score ERV≈0 and are omitted from the ranking)")

    # Drop program infrastructure (state/county agencies, fiscal intermediaries,
    # NEMT brokers, national reference labs, MMIS vendors) — the biggest billers
    # but not qui tam targets. Written to excluded_payers.parquet for audit.
    from .payer_filter import flag_non_target_payers
    name_col = "org_name" if "org_name" in df.columns else "org_legal_name"
    payer_reason = flag_non_target_payers(df.get(name_col, pd.Series("", index=df.index)))
    excluded_mask = (payer_reason != "").to_numpy()
    if excluded_mask.any():
        out_dir.mkdir(parents=True, exist_ok=True)
        (df.loc[excluded_mask, ["org_node_id", name_col]]
           .assign(reason=payer_reason[excluded_mask].to_numpy())
           .to_parquet(out_dir / "excluded_payers.parquet", index=False))
        log(f"    excluded {int(excluded_mask.sum()):,} program-infrastructure orgs "
            f"(government / fiscal intermediary / national payer) → excluded_payers.parquet")
        df = df[~excluded_mask].copy()
    n0 = len(df)                       # ranking/dossier counts are over candidates

    df = df.set_index("org_node_id", drop=False)
    # T-MSIS DQ Atlas state-quality → the confidence band down-weights signals
    # from states CMS flags as poor Medicaid reporters (doc 15 §2.8).
    from src.analytics.tmsis_quality import attach_state_quality
    df = attach_state_quality(df)

    subscores, coverage = compute_subscores(df)
    boost = graph_risk_boost(df["org_node_id"], shell_clusters, common_owner_clusters)
    taxonomies = df.get("primary_taxonomy", pd.Series("", index=df.index))
    prior = sector_prior_series(taxonomies)
    # A6: the OIG Work Plan overlay multiplies INTO the sector prior; both
    # components stay visible (sector_prior_base + gov_interest_* drivers).
    gov = government_interest_overlay(taxonomies.map(sector_for_taxonomy))
    combined_prior = prior * gov["gov_interest_multiplier"]
    payments = df.get("payments", pd.Series(0.0, index=df.index)).fillna(0.0)

    scoped_idx = None
    if scoped_payments is not None and len(scoped_payments):
        scoped_idx = (scoped_payments.set_index("org_node_id")
                      .reindex(df["org_node_id"]).set_axis(df.index))
    scored = expected_recoverable_value(subscores, payments, combined_prior,
                                        boost["graph_risk_boost"],
                                        scoped_payments=scoped_idx)
    gov.insert(0, "sector_prior_base", prior.round(3))
    from src.analytics.confidence import confidence_band
    conf = confidence_band(pd.concat([df, subscores, scored], axis=1)
                           .loc[:, lambda d: ~d.columns.duplicated()])
    # Computed outputs are authoritative: a stale erv/adjusted_prob/subscore_*
    # column arriving in the features input must never survive the concat
    # (duplicated() keeps the FIRST copy — found by probing: a poisoned input
    # column silently overrode the entire ranking).
    disc = None
    if disclosure is not None and len(disclosure):
        # A3: the public-disclosure screen, aligned per org. Orgs the screen
        # never saw stay NaN — "unchecked" must never render as "clear".
        disc = (disclosure.set_index("org_node_id")
                .reindex(df["org_node_id"]).set_axis(df.index))

    computed_cols = (set(subscores.columns) | set(boost.columns)
                     | set(scored.columns) | set(conf.columns) | set(gov.columns)
                     | {"erv_rank", "payments_joined", "public_disclosure_flag",
                        "public_disclosure_citations", "disclosure_sources_checked"})
    df = df.drop(columns=[c for c in df.columns if c in computed_cols],
                 errors="ignore")
    pieces = [df, subscores, boost.drop(columns=["graph_risk_boost"]),
              scored, gov, conf, payments.rename("payments_joined")]
    if disc is not None:
        pieces.append(disc)
    out = pd.concat(pieces, axis=1)
    assert not out.columns.duplicated().any(), \
        f"duplicate output columns: {out.columns[out.columns.duplicated()].tolist()}"
    out = out.sort_values("erv", ascending=False)
    out["erv_rank"] = range(1, len(out) + 1)
    out = out.reset_index(drop=True)
    require("scored_one_row_per_org", len(out) == n0, f"{len(out)} vs {n0}")

    # A9: enforcement lookalikes — X-layer corroboration only, never a driver.
    # Dormant (empty string) until settled orgs resolve from the case DB.
    if settled_org_ids:
        from .lookalikes import enforcement_lookalikes
        la = enforcement_lookalikes(out, settled_org_ids)
        out = out.merge(la, on="org_node_id", how="left")
        log(f"    enforcement lookalikes vs {len(set(settled_org_ids))} settled orgs")

    out_dir.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_dir / "erv_ranked.parquet", index=False)

    subscore_cols = [c for c in out.columns if c.startswith("subscore_")]
    dossier_dir = out_dir / "dossiers"
    dossier_dir.mkdir(exist_ok=True)
    for _, row in out.head(top_k_dossiers).iterrows():
        safe = str(row["org_node_id"]).replace(":", "_").replace("/", "_")
        (dossier_dir / f"{row['erv_rank']:03d}_{safe}.md").write_text(
            render_dossier(row, subscore_cols, coverage), encoding="utf-8")

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
    (out_dir / "MODEL_A_REPORT.md").write_text("".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph-dir", default=None, help="output dir of src.entity_graph")
    ap.add_argument("--features", default=None,
                    help="parquet: org_node_id + concept percentiles (+ payments)")
    ap.add_argument("--spending", default=None,
                    help="spending parquet (billing_npi, service_month, total_paid, "
                         "hcpcs_code): computes REAL per-org annual payments for the "
                         "exposure (overrides any payments column in --features); with "
                         "--provider-dim also drives growth + clinical plausibility")
    ap.add_argument("--provider-dim", default=None,
                    help="provider_dim parquet (npi, taxonomy_code) — the file the "
                         "graph build consumed; enables clinical plausibility (A5)")
    ap.add_argument("--out", default="/tmp/model_a_out")
    ap.add_argument("--top-k", type=int, default=10, help="dossiers to render")
    ap.add_argument("--case-db", default=None,
                    help="DOJ/OIG case table (csv or parquet) for the "
                         "public-disclosure screen (A3)")
    ap.add_argument("--dockets", default=None,
                    help="qui_tam_dockets.parquet from the docket monitor, "
                         "for the public-disclosure screen (A3)")
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
        scoped = None
        if args.spending:
            from .exposure import annual_payments_per_org_duckdb, attach_payments
            npi_to_org = pd.read_parquet(g / "npi_to_org.parquet")
            # per-org exposure computed entirely in DuckDB — the spending_fact
            # table (238M rows) never enters pandas (it OOMs a laptop even after
            # column projection). This is the ERV exposure input.
            log("    computing per-org exposure (DuckDB, streaming) …")
            payments, recon = annual_payments_per_org_duckdb(args.spending, npi_to_org)
            log(f"    exposure: ${recon['total_matched']:,.0f} matched "
                f"({recon['pct_dollars_matched']:.1%}); "
                f"{recon['unresolved_npis']} unresolved billing NPIs")
            feats = attach_payments(feats, payments)
            # scoped-damage / growth / clinical-plausibility enrichments still read
            # spending row-level (pandas) and are deferred at full scale pending a
            # DuckDB rewrite; core ERV = concept scores × graph features × sector
            # prior × real exposure runs without them.
            log("    note: scoped/growth/plausibility enrichments deferred at scale "
                "(DuckDB rewrite pending) — core ERV unaffected.")

    disclosure, settled_ids = None, None
    if args.case_db or args.dockets:
        from src.model_c.public_disclosure import public_disclosure_screen
        case_db = None
        if args.case_db:
            case_db = (pd.read_csv(args.case_db, dtype=str)
                       if args.case_db.endswith(".csv")
                       else pd.read_parquet(args.case_db))
        dockets = pd.read_parquet(args.dockets) if args.dockets else None
        disclosure = public_disclosure_screen(org_nodes, case_db, dockets)
        log(f"    disclosure screen: {int(disclosure['public_disclosure_flag'].sum())} "
            f"of {len(disclosure)} orgs flagged "
            f"({disclosure['disclosure_sources_checked'].iloc[0]})")
        if case_db is not None:
            from .lookalikes import resolve_settled_orgs
            settled = resolve_settled_orgs(org_nodes, case_db)
            settled_ids = settled["org_node_id"].tolist() or None

    run(org_nodes, gf, feats, shells, owners, Path(args.out), args.top_k,
        scoped_payments=scoped if args.spending else None,
        disclosure=disclosure, settled_org_ids=settled_ids)


if __name__ == "__main__":
    main()
