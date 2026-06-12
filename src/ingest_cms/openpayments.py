"""
openpayments.py — Open Payments (manufacturer → physician payments).

Source: openpaymentsdata.cms.gov General Payments annual CSV
(09-data-procurement.md #4). Grain: one payment record. Headers below are the
published names (the NPI column exists from PY2021 onward; older vintages keyed
on Physician_Profile_ID are out of scope for v1).

Outputs:
  * per-NPI metrics — op_total_dollars, op_payment_concentration (top-
    manufacturer share of the provider's OP dollars), n_manufacturers;
  * ``pays`` edges (manufacturer → provider, with dollars and product) for the
    entity graph;
  * the KICKBACK CO-OCCURRENCE: ``op_payment_utilization_corr`` — the share of a
    prescriber's Part D drug cost that sits on products of manufacturers who
    paid that prescriber. Alone, payments are just payments; crossed with
    utilization they are the anti-kickback (AKS) signal behind many cases.
    (Named after the spec's correlation feature; v1 computes the cost-weighted
    co-occurrence, which is robust with one year of data — a true longitudinal
    correlation graduates in later when multiple vintages are loaded.)
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns, canonicalize_series
from src.ingest_cms.partd import PARTD_COLS

OP_COLS = {
    "npi": ["Covered_Recipient_NPI", "COVERED_RECIPIENT_NPI", "Physician_NPI", "NPI"],
    "manufacturer": ["Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name",
                     "APPLICABLE_MANUFACTURER_OR_APPLICABLE_GPO_MAKING_PAYMENT_NAME"],
    "amount": ["Total_Amount_of_Payment_USDollars",
               "TOTAL_AMOUNT_OF_PAYMENT_USDOLLARS"],
    "product": ["Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1",
                "Name_of_Associated_Covered_Drug_or_Biological1",
                "Product_Category_or_Therapeutic_Area_1"],
}


def compute_openpayments_metrics(raw: pd.DataFrame
                                 ) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Per-NPI OP metrics + ``pays`` edges. Returns (metrics, pays_edges, n_quarantined)."""
    resolved = _resolve_columns(list(raw.columns), OP_COLS)
    missing = [c for c in ["npi", "manufacturer", "amount"] if c not in resolved]
    if missing:
        raise ValueError(f"Open Payments file missing required columns {missing}; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()})

    npi = canonicalize_series(df["npi"])
    quarantined = int((npi.isna() & df["npi"].fillna("").astype(str).str.strip().ne("")).sum())
    df = df.assign(npi=npi)[npi.notna()].copy()

    df["manufacturer"] = df["manufacturer"].fillna("").astype(str).str.strip().str.upper()
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df["product"] = (df["product"].fillna("").astype(str).str.strip().str.upper()
                     if "product" in df.columns else "")
    df = df[df["manufacturer"] != ""]

    by_mfr = (df.groupby(["npi", "manufacturer"], as_index=False)
                .agg(amount=("amount", "sum"),
                     products=("product", lambda s: "; ".join(sorted({x for x in s if x}))[:200])))

    g = by_mfr.groupby("npi")
    metrics = pd.DataFrame({
        "op_total_dollars": g["amount"].sum(),
        "n_manufacturers": g["manufacturer"].nunique(),
        # concentration: the top single manufacturer's share of this NPI's OP dollars
        "op_payment_concentration": g["amount"].max() / g["amount"].sum(),
    }).reset_index()

    pays_edges = pd.DataFrame({
        "src_id": "manufacturer:" + by_mfr["manufacturer"],
        "dst_id": "provider:" + by_mfr["npi"],
        "edge_type": "pays",
        "amount": by_mfr["amount"],
        "products": by_mfr["products"],
    })
    return metrics, pays_edges, quarantined


def kickback_co_occurrence(op_raw: pd.DataFrame, partd_raw: pd.DataFrame) -> pd.DataFrame:
    """Cross OP payments with Part D utilization → per-NPI co-occurrence (0–1).

    For each prescriber: (drug cost on products whose names appear among the
    products of manufacturers who paid that prescriber) / (total drug cost).
    Product↔drug match is by normalized name against Brnd_Name OR Gnrc_Name.
    Prescribers with no OP payments score 0 (no kickback exposure observed).
    """
    # OP side: per NPI, the set of paid product names
    op_resolved = _resolve_columns(list(op_raw.columns), OP_COLS)
    op = op_raw.rename(columns={v: k for k, v in op_resolved.items()})
    op = op.assign(npi=canonicalize_series(op["npi"]))
    op = op[op["npi"].notna()]
    op["product"] = (op["product"].fillna("").astype(str).str.strip().str.upper()
                     if "product" in op.columns else "")
    paid_products: dict[str, set] = {
        n: {p for p in grp["product"] if p}
        for n, grp in op.groupby("npi")
    }

    # Part D side: per NPI × drug cost
    pd_resolved = _resolve_columns(list(partd_raw.columns), PARTD_COLS)
    d = partd_raw.rename(columns={v: k for k, v in pd_resolved.items()})
    d = d.assign(npi=canonicalize_series(d["npi"]))
    d = d[d["npi"].notna()].copy()
    d["cost"] = pd.to_numeric(d["cost"], errors="coerce").fillna(0.0)
    brand = d.get("brand_name", pd.Series("", index=d.index)).fillna("").str.strip().str.upper()
    generic = d.get("generic_name", pd.Series("", index=d.index)).fillna("").str.strip().str.upper()

    def paid_share(npi: str, grp_idx) -> float:
        prods = paid_products.get(npi, set())
        if not prods:
            return 0.0
        hit = brand.loc[grp_idx].isin(prods) | generic.loc[grp_idx].isin(prods)
        total = d.loc[grp_idx, "cost"].sum()
        return float(d.loc[grp_idx, "cost"][hit].sum() / total) if total > 0 else 0.0

    rows = [{"npi": n, "op_payment_utilization_corr": paid_share(n, grp.index)}
            for n, grp in d.groupby("npi")]
    return pd.DataFrame(rows, columns=["npi", "op_payment_utilization_corr"])
