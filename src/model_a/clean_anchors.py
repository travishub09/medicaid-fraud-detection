"""
clean_anchors.py — manufacture high-confidence NEGATIVES (known non-offenders).

PU learning treats "not on a list" as unlabeled, not clean — correctly, because an
unflagged provider may simply be uncaught. But a tree model that compares fraud
actors to *non-offenders* needs an actual negative anchor set, and we can construct
one conservatively from institutional priors + benign behavior + graph distance:

  confirmed_clean = 1  when ALL hold —
     * an INSTITUTIONAL anchor (FQHC/RHC taxonomy, or a name like VA / university /
       government / community health center — types with institutional audit and a
       near-zero fraud base rate) OR LONG continuous tenure;
     * BENIGN billing — below the peer median on every anomaly concept;
     * NO fraud signal — not on any exclusion/case list, not within two hops of an
       exclusion, low graph fraud-proximity.

These are the "reliable negatives" of PU learning, enriched so the contrast is a
real known-clean cohort rather than the unlabeled mass. Conservative by design:
ambiguous providers stay unlabeled (confirmed_clean = 0), never forced negative.
``clean_basis`` records which anchors fired (the explainability record).
"""

from __future__ import annotations

import re

import pandas as pd

# v3 anomaly concepts (kept local to avoid importing the export — which imports us)
_CONCEPTS = ["concentration", "payment_intensity", "service_intensity",
             "specialty_mismatch", "temporal"]

# Institutional types with institutional oversight and ~zero fraud base rate.
INSTITUTIONAL_TAXONOMIES = {
    "261QF0400X",   # Federally Qualified Health Center
    "261QR1300X",   # Rural Health Clinic
    "261QR0400X",   # Health Service / public health clinic
    "282N00000X",   # General Acute Care Hospital
    "282NC2000X",   # Critical Access Hospital
    "313M00000X",   # Nursing facility (county/state-run subset, name-gated below)
}
INSTITUTIONAL_NAME = re.compile(
    r"\bVETERANS\b|\bV\.?A\.? MEDICAL|\bUNIVERSITY\b|\bUNIV\b|\bCOLLEGE\b|"
    r"DEPARTMENT OF|COUNTY OF|STATE OF|CITY OF|\bREGENTS\b|FEDERALLY QUALIFIED|"
    r"COMMUNITY HEALTH CENTER|INDIAN HEALTH|PUBLIC HEALTH|\bGOVERNMENT\b",
    re.IGNORECASE)

BENIGN_THRESHOLD = 0.5          # below the peer median on every concept
LONG_TENURE_MONTHS = 120        # 10+ years continuously enrolled
PROXIMITY_CLEAN_MAX = 0.5       # graph fraud-field below the median


def _num(df: pd.DataFrame, col: str, default=0.0) -> pd.Series:
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce")
    return pd.Series(default, index=df.index)


def manufacture_negatives(matrix: pd.DataFrame) -> pd.DataFrame:
    """Per-NPI confirmed_clean (1/0) + clean_basis. Returns npi + the two columns."""
    df = matrix
    idx = df.index
    name = df.get("org_legal_name", pd.Series("", index=idx)).fillna("").astype(str)
    tax = df.get("primary_taxonomy", pd.Series("", index=idx)).fillna("").astype(str).str.upper()

    institutional = tax.isin(INSTITUTIONAL_TAXONOMIES) | name.str.contains(INSTITUTIONAL_NAME)
    long_tenure = _num(df, "tenure_months", 0) >= LONG_TENURE_MONTHS

    present = [c for c in _CONCEPTS if c in df.columns]
    benign = (pd.concat([_num(df, c) for c in present], axis=1) < BENIGN_THRESHOLD).all(axis=1) \
        if present else pd.Series(False, index=idx)

    on_list = _num(df, "provider_on_exclusion",
                   default=None).fillna(_num(df, "provider_on_leie", 0)) == 1
    near_fraud = (_num(df, "within_2_hops_of_exclusion") > 0) \
        | (_num(df, "graph_fraud_proximity") >= PROXIMITY_CLEAN_MAX)
    no_fraud_signal = ~on_list.fillna(False) & ~near_fraud.fillna(False)

    anchor = institutional | long_tenure
    confirmed = (anchor & benign & no_fraud_signal).astype(int)

    basis = pd.Series("", index=idx, dtype=object)
    for mask, tag in [(institutional, "institutional"), (long_tenure, "long_tenure"),
                      (benign, "benign_billing")]:
        basis = basis.where(~(mask & (confirmed == 1)),
                            (basis + ";" + tag).str.strip(";"))
    return pd.DataFrame({"npi": df["npi"].astype(str).values,
                         "confirmed_clean": confirmed.values,
                         "clean_basis": basis.values})
