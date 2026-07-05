"""
saturation.py — CMS Market Saturation & Utilization (expansion plan B2).

Source: data.cms.gov "Market Saturation & Utilization State-County" CSV
(runbook §1.4). Grain: county × type of service. CMS publishes this file FOR
program-integrity reasons — geographic over-supply of home health, hospice,
SNF and lab services is their own moratorium/fraud prior, which is exactly how
Model A uses it.

Produces ``market_saturation_index``: providers per 1,000 FFS beneficiaries,
one-sided percentile-ranked WITHIN service type across counties (a county is
only over-supplied relative to other counties offering the same service).
FIPS codes are strings (leading zeros — hard rule #1).

Org attachment is state-grain for now (orgs carry ``addr_state``; the
county-FIPS join is the upgrade once a ZIP→county mapping lands with the
Census file, B6). State rates are beneficiary-weighted aggregates, NOT the max
county — every org in a state must not inherit its worst county.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns
from src.model_a.sector_priors import sector_for_taxonomy

SATURATION_COLS = {
    "service": ["Type of Service", "type_of_service", "TYPE_OF_SERVICE"],
    "fips": ["State and County FIPS Code", "State County FIPS Code",
             "state_county_fips_code", "FIPS", "fips"],
    "state": ["State Name", "state_name", "State", "STATE"],
    "county": ["County Name", "county_name", "County"],
    "n_providers": ["Number of Providers", "number_of_providers"],
    "n_benes": ["Number of Fee-for-Service Beneficiaries",
                "number_of_fee_for_service_beneficiaries"],
}

# Model A sector (sector_priors) → substring of the PUF's "Type of Service".
# DME and ambulance are IN the PUF (CMS tracks them precisely because they're
# the highest-risk sectors) — leaving them unmapped silently NaN'd the two
# sectors this saturation prior matters most for.
SECTOR_TO_SERVICE: dict[str, str] = {
    "home_health": "home health",
    "hospice": "hospice",
    "snf": "skilled nursing",
    "lab": "clinical laboratory",
    "dme": "durable medical",
    "ambulance": "ambulance",
}


def compute_saturation_metrics(raw: pd.DataFrame) -> pd.DataFrame:
    """County-grain saturation: providers_per_1k_benes + market_saturation_index.

    One row per (fips/state/county, service_type); the index is the one-sided
    percentile of providers-per-1k within the service type. Counties with no
    beneficiaries are NaN (degenerate, never force-ranked).
    """
    resolved = _resolve_columns(list(raw.columns), SATURATION_COLS)
    missing = [c for c in ["service", "state", "n_providers", "n_benes"]
               if c not in resolved]
    if missing:
        raise ValueError(f"Market Saturation file missing required columns "
                         f"{missing}; saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()

    for c in ("fips", "county"):
        if c not in df.columns:
            df[c] = ""
    df["fips"] = df["fips"].fillna("").astype(str).str.strip()      # string: zeros
    df["service_type"] = df["service"].fillna("").astype(str).str.strip()
    df["state"] = df["state"].fillna("").astype(str).str.strip().str.upper()
    df["county"] = df["county"].fillna("").astype(str).str.strip()
    for c in ("n_providers", "n_benes"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    out = df[["fips", "state", "county", "service_type",
              "n_providers", "n_benes"]].copy()
    out["providers_per_1k_benes"] = (
        out["n_providers"] * 1_000.0 / out["n_benes"]).where(out["n_benes"] > 0)
    out["market_saturation_index"] = (
        out.groupby("service_type")["providers_per_1k_benes"]
           .rank(method="average", pct=True))
    return out


def state_saturation_index(county_metrics: pd.DataFrame) -> pd.DataFrame:
    """State-grain fallback: bene-weighted rate per (state, service), then the
    one-sided percentile within service type across states."""
    g = (county_metrics.groupby(["state", "service_type"], as_index=False)
         [["n_providers", "n_benes"]].sum())
    g["providers_per_1k_benes"] = (
        g["n_providers"] * 1_000.0 / g["n_benes"]).where(g["n_benes"] > 0)
    g["market_saturation_index"] = (
        g.groupby("service_type")["providers_per_1k_benes"]
         .rank(method="average", pct=True))
    return g


def attach_market_saturation(features: pd.DataFrame, org_nodes: pd.DataFrame,
                             county_metrics: pd.DataFrame,
                             sector_to_service: dict[str, str] | None = None
                             ) -> pd.DataFrame:
    """Attach ``market_saturation_index`` to org-grain features (state-grain).

    Only sectors with a mapped service type get a value (saturation is a
    HH/hospice/SNF/lab prior, not a universal feature); everything else stays
    NaN — absent, never neutral-scored. No fan-out, asserted.
    """
    smap = sector_to_service or SECTOR_TO_SERVICE
    states = state_saturation_index(county_metrics)

    orgs = org_nodes.set_index("org_node_id")
    # Deterministic (state, sector-fragment) → index resolution: candidate
    # service types are matched in sorted order and the shortest matching name
    # wins (the most specific label containing the fragment). The old dict-
    # iteration loop returned whichever matching entry insertion order served
    # first — file-ordering-dependent.
    by_state: dict[str, list[tuple[str, float]]] = {}
    for r in states.itertuples():
        by_state.setdefault(r.state, []).append(
            (str(r.service_type).lower(), r.market_saturation_index))
    for st in by_state:
        by_state[st].sort(key=lambda t: (len(t[0]), t[0]))

    def _index_for(org_id: str) -> float:
        if org_id not in orgs.index:
            return float("nan")
        row = orgs.loc[org_id]
        sector = sector_for_taxonomy(row.get("primary_taxonomy"))
        frag = smap.get(sector)
        state = str(row.get("addr_state", "") or "").strip().upper()
        if not frag or not state:
            return float("nan")
        for svc, idx in by_state.get(state, ()):
            if frag in svc:
                return idx
        return float("nan")

    out = features.copy()
    n0 = len(out)
    out["market_saturation_index"] = out["org_node_id"].astype(str).map(_index_for)
    assert len(out) == n0, "saturation attach fan-out"
    return out
