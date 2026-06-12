"""
app.py — the public billing-risk lookup tool, v1 (INTERNAL PREVIEW).

The SEO front door and inbound funnel (07-sourcing-and-marketing.md), serving
peer-relative *percentiles* with named drivers and benign explanations — never a
fraud label, never an accusation (01-legal-compliance.md, defamation safety).

GATING — read before deploying: this build is an internal preview. PUBLIC launch
is gated on Phase-0 counsel sign-off (GAPS #18) and on real Part B data behind
it. Nothing here changes that; it exists so the product is testable end-to-end
the day both gates clear.

Data contract: a parquet with one row per NPI —
    npi · display_name · peer_group · <metric columns as 0–1 percentiles>
(the shape ``ingest_cms.to_peer_percentiles`` produces, plus identity columns).

Run:  python -m src.lookup_tool --features <percentiles.parquet> --port 8000
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# plain-language names for the public surface — jargon stays internal
METRIC_LABELS: dict[str, str] = {
    "em_high_level_share": "share of office visits billed at the highest levels",
    "services_per_bene": "services per patient",
    "allowed_per_bene": "Medicare-allowed dollars per patient",
    "code_concentration_hhi": "billing concentration in few service codes",
    "brand_generic_cost_ratio": "brand-name vs generic prescribing cost",
    "high_cost_drug_share": "share of drug cost in highest-cost drugs",
    "controlled_substance_share": "share of prescriptions that are controlled substances",
    "dme_high_cost_item_share": "share of equipment billing in highest-cost items",
    "dme_code_concentration": "equipment billing concentration",
}

BENIGN_EXPLANATIONS = [
    "Specialists and referral centers naturally treat more complex patients.",
    "Small peer groups can make ordinary practices look unusual.",
    "Billing-policy and code-definition changes can shift patterns year to year.",
    "Public data lags about two years and may not reflect current practice.",
]

DISCLAIMER = ("These figures describe how this provider's public billing data "
              "compares with peers. High percentiles are not evidence of fraud "
              "or wrongdoing, and many have ordinary explanations.")

IDENTITY_COLUMNS = {"npi", "display_name", "peer_group"}


def _risk_card(row: pd.Series, metric_cols: list[str]) -> dict:
    percentiles = {METRIC_LABELS.get(c, c): round(float(row[c]), 3)
                   for c in metric_cols if pd.notna(row[c])}
    top = sorted(percentiles.items(), key=lambda kv: -kv[1])[:3]
    card = {
        "npi": str(row["npi"]),
        "display_name": str(row.get("display_name", "")),
        "peer_group": str(row.get("peer_group", "national")),
        "percentile_by_metric": percentiles,
        "top_drivers": [{"metric": m, "percentile": p} for m, p in top],
        "benign_explanations": BENIGN_EXPLANATIONS,
        "disclaimer": DISCLAIMER,
    }
    # the hard rule, enforced where the response is built
    assert "fraud" not in {k.lower() for k in card}, "no fraud field, ever"
    return card


def build_app(features_path: str | Path):
    """Construct the FastAPI app over a percentile-features parquet."""
    from fastapi import FastAPI, HTTPException

    df = pd.read_parquet(features_path)
    assert "npi" in df.columns, "features parquet must carry an npi column"
    df["npi"] = df["npi"].astype(str)
    df = df.drop_duplicates("npi").set_index("npi", drop=False)
    metric_cols = [c for c in df.columns if c not in IDENTITY_COLUMNS]

    app = FastAPI(title="Billing-Risk Lookup (internal preview)",
                  description=DISCLAIMER)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "providers": int(len(df))}

    @app.get("/lookup/{npi}")
    def lookup(npi: str):
        npi = npi.strip()
        if npi not in df.index:
            raise HTTPException(status_code=404, detail="NPI not found")
        return _risk_card(df.loc[npi], metric_cols)

    return app
