"""
build_features.py — roll per-NPI v3 concepts up to per-ORG company features.

Model A scores organizations, but the detection pipeline computes its anomaly
concepts at the PROVIDER (NPI) grain (``fraud_leads_v3.parquet``). This stage is
the missing bridge: it maps each scored NPI to its canonical organization (via
the entity graph's ``npi_to_org`` crosswalk) and aggregates the five v3 concept
percentiles to org grain, producing the ``company_features.parquet`` that
``python -m src.model_a --features`` consumes.

Aggregation: each concept is taken as the MAX across the org's scored members —
the same "as strong as its strongest signal" rule v3 uses to collapse correlated
features into a concept, and the honest org-level reading ("this organization
employs a provider in the Nth percentile for X"). NaN (not-scored) members are
ignored by the max. ``payments`` = summed net_paid of scored members (a fallback;
Model A overrides it with real exposure from ``--spending`` when supplied).

CLI: ``python -m src.model_a.build_features`` (honors MEDICAID_DATA_ROOT).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

CONCEPT_COLS = ["concentration", "payment_intensity", "service_intensity",
                "specialty_mismatch", "temporal"]


def build_company_features(leads_v3: pd.DataFrame,
                           npi_to_org: pd.DataFrame) -> pd.DataFrame:
    """Per-NPI v3 concepts + npi_to_org crosswalk → one row per org_node_id with
    the five concept percentiles (max over scored members) and ``payments``
    (summed net_paid). No fan-out: grouped to one row per org."""
    leads = leads_v3.copy()
    leads["npi"] = leads["npi"].astype(str)
    xw = npi_to_org[["npi", "org_node_id"]].copy()
    xw["npi"] = xw["npi"].astype(str)

    m = leads.merge(xw, on="npi", how="inner")
    for c in CONCEPT_COLS:
        m[c] = pd.to_numeric(m[c], errors="coerce") if c in m.columns else pd.NA
    m["net_paid"] = pd.to_numeric(m.get("net_paid"), errors="coerce").fillna(0.0)

    agg = {c: "max" for c in CONCEPT_COLS}          # max skips NaN (not-scored)
    agg["net_paid"] = "sum"
    g = m.groupby("org_node_id", as_index=False).agg(agg)
    g = g.rename(columns={"net_paid": "payments"})
    assert g["org_node_id"].is_unique, "company_features fan-out"
    return g


def _data_root(cli: str | None) -> Path:
    if cli:
        return Path(cli)
    return Path(os.environ.get("MEDICAID_DATA_ROOT",
                               str(Path.home() / "Desktop" / "data")))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", default=None, help="override MEDICAID_DATA_ROOT")
    ap.add_argument("--leads", default=None,
                    help="fraud_leads_v3.parquet (default <root>/detection/)")
    ap.add_argument("--graph-dir", default=None,
                    help="entity-graph output dir holding npi_to_org.parquet")
    ap.add_argument("--out", default=None,
                    help="output parquet (default <root>/features/company_features.parquet)")
    args = ap.parse_args()
    root = _data_root(args.data_root)
    leads_p = Path(args.leads) if args.leads else root / "detection" / "fraud_leads_v3.parquet"
    graph = Path(args.graph_dir) if args.graph_dir else root / "graph"
    out = Path(args.out) if args.out else root / "features" / "company_features.parquet"

    for p in (leads_p, graph / "npi_to_org.parquet"):
        if not p.exists():
            raise FileNotFoundError(f"required input missing: {p}")

    leads = pd.read_parquet(leads_p)
    if "concentration" not in leads.columns:
        raise ValueError(
            f"{leads_p} has no concept columns — re-run "
            "`python -m src.attempt_2.leads.refine_layer2_v3` on this branch "
            "(it now persists the per-concept percentiles).")
    npi_to_org = pd.read_parquet(graph / "npi_to_org.parquet")
    feats = build_company_features(leads, npi_to_org)
    out.parent.mkdir(parents=True, exist_ok=True)
    feats.to_parquet(out, index=False)
    flagged = int((feats[CONCEPT_COLS].fillna(0) >= 0.99).any(axis=1).sum())
    print(f"Wrote {out}")
    print(f"  orgs with >=1 scored member: {len(feats):,}")
    print(f"  orgs with a top-percentile (>=0.99) concept: {flagged:,}")
    print(f"  total payments rolled up: ${feats['payments'].sum():,.0f}")


if __name__ == "__main__":
    main()
