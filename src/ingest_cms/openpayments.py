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

import re

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
    # Fields 2-5 exist here ONLY so column-projecting readers (the export's
    # usecols path) keep them: kickback_co_occurrence scans the RAW header names
    # via _PRODUCT_COL_RE, so projecting them away at read time silently rebuilt
    # the "only field 1" bug this module documents fixing.
    "product_2": ["Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_2",
                  "Name_of_Associated_Covered_Drug_or_Biological2"],
    "product_3": ["Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_3",
                  "Name_of_Associated_Covered_Drug_or_Biological3"],
    "product_4": ["Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_4",
                  "Name_of_Associated_Covered_Drug_or_Biological4"],
    "product_5": ["Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_5",
                  "Name_of_Associated_Covered_Drug_or_Biological5"],
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


# All five OP associated-product name fields (the old path read only field 1,
# silently discarding up to 80% of the payment→product links).
_PRODUCT_COL_RE = re.compile(
    r"(name_of_(associated_covered_)?drug|product_category)", re.IGNORECASE)

# Dosage/form noise stripped before matching — OP writes "XARELTO 20MG TABLET"
# where Part D writes "XARELTO"; exact-string equality matched almost nothing
# (the shipped signal ranked at chance because of it).
_FORM_TOKENS = frozenset({
    "MG", "MCG", "ML", "GM", "HCL", "ER", "XR", "XL", "DR", "SR", "CR", "LA",
    "TABLET", "TABLETS", "TAB", "TABS", "CAPSULE", "CAPSULES", "CAP", "CAPS",
    "ORAL", "SOLUTION", "SUSPENSION", "INJECTION", "INJ", "CREAM", "GEL",
    "PATCH", "SPRAY", "KIT", "PEN", "DEVICE", "AND", "WITH", "FOR",
})


def _name_tokens(name: str) -> list[str]:
    toks = re.split(r"[^A-Z0-9]+", str(name).upper())
    return [t for t in toks
            if t and not t.isdigit() and not t[0].isdigit() and t not in _FORM_TOKENS]


def _match_keys(name: str) -> list[str]:
    """Keys a product/drug name matches under: the full form-stripped name, plus
    the first distinctive token (len ≥ 4) so "XARELTO 20MG TABLET" ↔ "XARELTO".
    The token fallback trades a little precision (same-family drugs share a first
    token) for the recall that makes the signal exist at all — acceptable for a
    one-sided exposure share, and documented."""
    toks = _name_tokens(name)
    if not toks:
        return []
    keys = [" ".join(toks)]
    if len(toks[0]) >= 4 and toks[0] != keys[0]:
        keys.append(toks[0])
    return keys


def kickback_co_occurrence(op_raw: pd.DataFrame, partd_raw: pd.DataFrame) -> pd.DataFrame:
    """Cross OP payments with Part D utilization → per-NPI co-occurrence (0–1).

    For each prescriber: (drug cost on products whose names appear among the
    products of manufacturers who paid that prescriber) / (total drug cost).
    Matching is form-stripped-name OR first-distinctive-token equality against
    Brnd_Name OR Gnrc_Name, across ALL five OP product fields (see _match_keys
    for the precision/recall tradeoff). Prescribers with no OP payments score 0
    (no kickback exposure observed).
    """
    # OP side: per NPI, the set of paid product match-keys, from every product column
    op_resolved = _resolve_columns(list(op_raw.columns), OP_COLS)
    op = op_raw.rename(columns={v: k for k, v in op_resolved.items()})
    op = op.assign(npi=canonicalize_series(op["npi"]))
    op = op[op["npi"].notna()]
    # include the canonicalized product_2..5 names too — OP_COLS now resolves
    # them (so the export's usecols read keeps them on disk), which renames those
    # columns to "product_N", out of _PRODUCT_COL_RE's range. Match both.
    product_cols = [c for c in op.columns
                    if str(c).startswith("product") or _PRODUCT_COL_RE.search(str(c))]
    if not product_cols:
        # no product columns at all → the whole feature is silently zero; say so
        import logging
        logging.getLogger(__name__).warning(
            "kickback_co_occurrence: no OP product columns found — "
            "op_payment_utilization_corr will be 0 for everyone")

    # Set of (npi, match-key) — a Part D row matches if any key of its drug name
    # was paid for THAT prescriber. Vectorized set-membership (see history: the
    # per-group loop swapped a 16 GB box).
    SEP = "\x1f"
    paid_keys: set[str] = set()
    for c in product_cols:
        vals = op[c].fillna("").astype(str)
        for npi_val, name in zip(op["npi"].to_numpy(), vals.to_numpy()):
            for k in _match_keys(name):
                paid_keys.add(f"{npi_val}{SEP}{k}")

    # Part D side: per NPI × drug cost
    pd_resolved = _resolve_columns(list(partd_raw.columns), PARTD_COLS)
    d = partd_raw.rename(columns={v: k for k, v in pd_resolved.items()})
    d = d.assign(npi=canonicalize_series(d["npi"]))
    d = d[d["npi"].notna()].copy()
    d["cost"] = pd.to_numeric(d["cost"], errors="coerce").fillna(0.0)
    npi_arr = d["npi"].astype(str).to_numpy()
    brand = d.get("brand_name", pd.Series("", index=d.index)).fillna("").astype(str)
    generic = d.get("generic_name", pd.Series("", index=d.index)).fillna("").astype(str)

    # memoize name → keys (Part D files repeat the same drug names constantly)
    key_cache: dict[str, list[str]] = {}

    def _keys_cached(name: str) -> list[str]:
        got = key_cache.get(name)
        if got is None:
            got = key_cache[name] = _match_keys(name)
        return got

    hit = [any(f"{n}{SEP}{k}" in paid_keys for k in _keys_cached(b))
           or any(f"{n}{SEP}{k}" in paid_keys for k in _keys_cached(ge))
           for n, b, ge in zip(npi_arr, brand.to_numpy(), generic.to_numpy())]
    hit = pd.Series(hit, index=d.index)
    agg = pd.DataFrame({"npi": d["npi"].to_numpy(),
                        "cost": d["cost"].to_numpy(),
                        "hit_cost": d["cost"].to_numpy() * hit.to_numpy()}
                       ).groupby("npi", as_index=False).agg(
        total=("cost", "sum"), hit=("hit_cost", "sum"))
    agg["op_payment_utilization_corr"] = (agg["hit"] / agg["total"]).where(agg["total"] > 0, 0.0)
    return agg[["npi", "op_payment_utilization_corr"]]
