"""
nadac.py — NADAC drug pricing reference + spread anomaly (doc 14 B4).

Source: data.medicaid.gov "NADAC (National Average Drug Acquisition Cost)"
(weekly, free). The authoritative per-unit acquisition cost per NDC. Two uses:

  compute_nadac_reference   parse NADAC → per-NDC nadac_per_unit, classification
                            (brand/generic), is_high_cost (top-decile per-unit) —
                            the reference that sharpens which drugs count as
                            "high cost" in the Part D signals.
  drug_spread_anomaly       given NDC-keyed claim costs (Medicaid SDUD or another
                            NDC-level source the caller supplies), per org the
                            dollar-weighted share billed ABOVE NADAC — the
                            markup/spread shape (and 340B duplicate-discount
                            groundwork). One-sided; feeds drug_outlier.

NADAC itself is drug-level (no provider); the per-provider join needs an
NDC-level claims source (the Part B/D PUFs are name-level, not NDC) — documented
in 15 §B4. NDCs are strings (leading zeros kept — hard rule #1).
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns

NADAC_COLS = {
    "ndc": ["NDC", "ndc"],
    "description": ["NDC Description", "ndc_description", "Description"],
    "per_unit": ["NADAC Per Unit", "nadac_per_unit", "NADAC_Per_Unit"],
    "classification": ["Classification for Rate Setting", "classification",
                       "Pharmacy Type Indicator"],
}


def compute_nadac_reference(raw: pd.DataFrame,
                            high_cost_quantile: float = 0.9) -> pd.DataFrame:
    """NADAC rows → per-NDC reference: ndc, nadac_per_unit, classification,
    is_high_cost (per-unit at/above the top-decile cut)."""
    resolved = _resolve_columns(list(raw.columns), NADAC_COLS)
    if "ndc" not in resolved or "per_unit" not in resolved:
        raise ValueError(f"NADAC file missing ndc/per-unit columns; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()
    df["ndc"] = df["ndc"].fillna("").astype(str).str.strip()
    df["per_unit"] = pd.to_numeric(df["per_unit"], errors="coerce")
    df["classification"] = (df["classification"] if "classification" in df.columns
                            else pd.Series("", index=df.index)
                            ).fillna("").astype(str).str.upper().str[:1]  # B/G
    df = df[(df["ndc"] != "") & df["per_unit"].notna()]
    g = df.groupby("ndc", as_index=False).agg(
        nadac_per_unit=("per_unit", "median"),
        classification=("classification", lambda s: s.mode().iat[0] if len(s) else ""))
    if len(g):
        cut = g["nadac_per_unit"].quantile(high_cost_quantile)
        g["is_high_cost"] = (g["nadac_per_unit"] >= cut).astype(int)
    else:
        g["is_high_cost"] = 0
    return g.rename(columns={"nadac_per_unit": "nadac_per_unit"})


def drug_spread_anomaly(ndc_claims: pd.DataFrame, nadac_reference: pd.DataFrame,
                        npi_to_org: pd.DataFrame) -> pd.DataFrame:
    """Per-org share of drug dollars billed ABOVE the NADAC benchmark.

    ``ndc_claims``: billing_npi, ndc, units, billed_cost (an NDC-level claims
    source). ``nadac_reference``: from compute_nadac_reference. Compares
    billed-per-unit to NADAC; the excess dollars over benchmark, as a share of
    the org's drug billing → ``drug_spread_anomaly`` (0–1), feeding drug_outlier.
    """
    cols = ["org_node_id", "above_nadac_paid", "drug_paid", "drug_spread_anomaly"]
    c = ndc_claims.copy()
    c["billing_npi"] = c["billing_npi"].astype(str)
    c["ndc"] = c["ndc"].astype(str).str.strip()
    for col in ("units", "billed_cost"):
        c[col] = pd.to_numeric(c.get(col), errors="coerce").fillna(0.0)

    xw = npi_to_org["npi"].astype(str)
    assert xw.is_unique, "npi_to_org has duplicate NPIs (ambiguous attribution)"
    c["org_node_id"] = c["billing_npi"].map(
        dict(zip(xw, npi_to_org["org_node_id"].astype(str))))
    c = c[c["org_node_id"].notna()]
    if not len(c):
        return pd.DataFrame(columns=cols)

    nadac = dict(zip(nadac_reference["ndc"].astype(str),
                     pd.to_numeric(nadac_reference["nadac_per_unit"], errors="coerce")))
    c["benchmark"] = c["ndc"].map(nadac) * c["units"]
    c["excess"] = (c["billed_cost"] - c["benchmark"]).clip(lower=0).where(
        c["benchmark"].notna(), 0.0)
    g = c.groupby("org_node_id").agg(above_nadac_paid=("excess", "sum"),
                                     drug_paid=("billed_cost", "sum"))
    g["drug_spread_anomaly"] = (g["above_nadac_paid"] / g["drug_paid"]).where(
        g["drug_paid"] > 0, 0.0).clip(0, 1)
    return g.reset_index()[cols]
