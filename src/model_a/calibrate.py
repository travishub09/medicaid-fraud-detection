"""
calibrate.py — graduate Model A from heuristic to enforcement-calibrated.

The label-free v1 ranks by ERV with `adjusted_prob` saturating near 1.0 — useful
triage, but rank order is driven by dollars, not by a calibrated probability of a
case. This stage breaks that ceiling using a healthcare-FCA CASE DATABASE
(DOJ/OIG settlements: defendant, sector, amount, intervened) matched to the
entity graph. It writes two artifacts Model A can consume:

  sector_priors.json   enforcement-weighted sector multipliers
                       (src.enforcement.derive_priors.derive_sector_priors) — feed
                       to `python -m src.model_a --priors`.
  pu_model.pkl         an Elkan–Noto PU classifier (src.model_a.supervised) trained
                       on org features with matched defendants as positives →
                       calibrated P(fraud|x). Feed to `--pu-model`; it replaces the
                       saturated heuristic probability and re-ranks ERV.

Where the case DB comes from (run on a networked box): the DOJ fetcher
(`src.enforcement.fetch`) for press-release settlements, plus the OIG enforcement
/ CIA feeds (docs/platform/16 §6). Schema = src.enforcement.case_db.CASE_COLUMNS.
Without a case DB, Model A stays on the documented placeholder priors.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
from pathlib import Path

import pandas as pd

# the org feature vector the PU model trains on (numeric, graph + billing concepts)
CALIB_FEATURE_COLS = [
    "concentration", "payment_intensity", "service_intensity", "specialty_mismatch",
    "temporal", "shell_score", "within_2_hops_of_exclusion",
    "related_party_density_norm", "co_location_cluster_size",
]


def build_org_feature_matrix(org_nodes: pd.DataFrame,
                             org_graph_features: pd.DataFrame,
                             company_features: pd.DataFrame) -> pd.DataFrame:
    """One numeric row per org over CALIB_FEATURE_COLS (missing → 0), keyed by
    org_node_id — the same features Model A scores on, for train and predict."""
    df = org_nodes[["org_node_id"]].merge(org_graph_features, on="org_node_id", how="left")
    df = df.merge(company_features, on="org_node_id", how="left")
    if "related_party_density" in df.columns and "related_party_density_norm" not in df.columns:
        df["related_party_density_norm"] = (
            pd.to_numeric(df["related_party_density"], errors="coerce").clip(lower=0)
            / 25.0).clip(upper=1.0)
    for c in CALIB_FEATURE_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0) if c in df.columns else 0.0
    return df[["org_node_id"] + CALIB_FEATURE_COLS]


def calibrate(case_db: pd.DataFrame, org_nodes: pd.DataFrame,
              org_graph_features: pd.DataFrame, company_features: pd.DataFrame):
    """Return (sector_priors dict, PUModel | None, report dict)."""
    from src.enforcement.derive_priors import derive_sector_priors
    from src.model_a.validation import outcomes_from_case_db
    from src.model_a.supervised import train_pu_classifier

    priors = derive_sector_priors(case_db)

    outcomes = outcomes_from_case_db(case_db, org_nodes)
    positives_ids = set(outcomes["org_node_id"].astype(str))
    feats = build_org_feature_matrix(org_nodes, org_graph_features, company_features)
    y = feats["org_node_id"].astype(str).isin(positives_ids).astype(int)

    model = None
    if y.sum() >= 5:                                   # need a few positives to learn
        model = train_pu_classifier(feats[CALIB_FEATURE_COLS], y,
                                    feature_cols=CALIB_FEATURE_COLS)
    report = {
        "n_cases_total": int(outcomes.attrs.get("n_cases_total", len(case_db))),
        "n_cases_matched_to_orgs": int(outcomes.attrs.get("n_cases_matched", len(outcomes))),
        "n_positive_orgs": int(y.sum()),
        "sector_priors": priors,
        "pu_model_trained": model is not None,
        "top_features": list(model.feature_importance)[:5] if model else [],
    }
    return priors, model, report


def _data_root(cli: str | None) -> Path:
    if cli:
        return Path(cli)
    return Path(os.environ.get("MEDICAID_DATA_ROOT",
                               str(Path.home() / "Desktop" / "data")))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case-db", default=None,
                    help="case DB CSV/parquet (CASE_COLUMNS). Default: the DOJ "
                         "fetcher's output <root>/feeds/enforcement/doj_cases.csv")
    ap.add_argument("--graph-dir", default=None, help="entity-graph output dir")
    ap.add_argument("--features", default=None, help="company_features.parquet")
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--out", default=None, help="output dir (default <root>/model_a)")
    args = ap.parse_args()
    root = _data_root(args.data_root)
    g = Path(args.graph_dir) if args.graph_dir else root / "graph"
    feats_p = Path(args.features) if args.features else root / "features" / "company_features.parquet"
    out = Path(args.out) if args.out else root / "model_a"
    out.mkdir(parents=True, exist_ok=True)

    case_db_path = (Path(args.case_db) if args.case_db
                    else root / "feeds" / "enforcement" / "doj_cases.csv")
    if not case_db_path.exists():
        raise SystemExit(
            f"No case DB at {case_db_path}. Build one first (needs network):\n"
            f"  python -m src.enforcement.fetch --backfill-years 10")
    case_db = (pd.read_csv(case_db_path, dtype=str) if str(case_db_path).endswith(".csv")
               else pd.read_parquet(case_db_path))
    org_nodes = pd.read_parquet(g / "nodes" / "org_nodes.parquet")
    gf = pd.read_parquet(g / "org_graph_features.parquet")
    company_features = pd.read_parquet(feats_p)

    priors, model, report = calibrate(case_db, org_nodes, gf, company_features)
    (out / "sector_priors.json").write_text(json.dumps(priors, indent=2), encoding="utf-8")
    if model is not None:
        with open(out / "pu_model.pkl", "wb") as fh:
            pickle.dump(model, fh)
    (out / "CALIBRATION_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Wrote sector_priors.json ({len(priors)} sectors)")
    print(f"  cases matched to orgs: {report['n_cases_matched_to_orgs']} / {report['n_cases_total']}"
          f"  → {report['n_positive_orgs']} positive orgs")
    if model is not None:
        print(f"  trained PU model (pu_model.pkl); top features: {report['top_features']}")
        print("  re-run: python -m src.model_a … --priors model_a/sector_priors.json "
              "--pu-model model_a/pu_model.pkl")
    else:
        print("  too few matched positives to train a PU model — sector priors only. "
              "Re-run: python -m src.model_a … --priors model_a/sector_priors.json")


if __name__ == "__main__":
    main()
