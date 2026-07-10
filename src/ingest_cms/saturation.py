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

# Some vintages carry full state names; org addresses carry 2-letter codes.
# Without this map every state lookup silently NaNs on those vintages.
_STATE_NAME_TO_CODE = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI",
    "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX",
    "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY", "PUERTO RICO": "PR",
}


def compute_saturation_metrics(raw: pd.DataFrame,
                               max_period: str | None = None) -> pd.DataFrame:
    """County-grain saturation: providers_per_1k_benes + market_saturation_index.

    One row per (fips/state/county, service_type); the index is the one-sided
    percentile of providers-per-1k within the service type. Counties with no
    beneficiaries are NaN (degenerate, never force-ranked).

    ``max_period`` (frozen runs): the file STACKS reference periods (the
    cutoff-census found 2020..2025 in one file), so a freeze must use the
    latest period AT OR BEFORE the cutoff, not the latest overall — otherwise
    a 2025 market snapshot leaks into a 2023-12 matrix. YYYY-MM or longer;
    compared on the first 7 characters.
    """
    resolved = _resolve_columns(list(raw.columns), SATURATION_COLS)
    missing = [c for c in ["service", "state", "n_providers", "n_benes"]
               if c not in resolved]
    if missing:
        raise ValueError(f"Market Saturation file missing required columns "
                         f"{missing}; saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()

    # The state-county file STACKS reference periods; summing/ranking across them
    # double-counts providers and mixes vintages. Keep only the latest period
    # (at or before max_period when a freeze cutoff is in force).
    rp_col = next((c for c in raw.columns
                   if c.strip().lower().replace(" ", "_") == "reference_period"), None)
    if rp_col is not None:
        rp = df[rp_col].fillna("").astype(str)
        pool = rp[rp != ""]
        if max_period:
            cut = str(max_period)[:7]
            pool = pool[pool.str.slice(0, 7) <= cut]
            if not len(pool):
                raise ValueError(
                    f"Market Saturation file has no reference period at or "
                    f"before {max_period} — the frozen run cannot use it")
        latest = pool.max() if len(pool) else ""
        if latest:
            df = df[rp == latest].copy()
            df.attrs["reference_period"] = str(latest)

    for c in ("fips", "county"):
        if c not in df.columns:
            df[c] = ""
    df["fips"] = df["fips"].fillna("").astype(str).str.strip()      # string: zeros
    df["service_type"] = df["service"].fillna("").astype(str).str.strip()
    df["state"] = df["state"].fillna("").astype(str).str.strip().str.upper()
    df["state"] = df["state"].map(lambda s: _STATE_NAME_TO_CODE.get(s, s))
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
    out.attrs["reference_period"] = df.attrs.get("reference_period", "")
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
                             sector_to_service: dict[str, str] | None = None,
                             zip_to_county: pd.DataFrame | None = None
                             ) -> pd.DataFrame:
    """Attach ``market_saturation_index`` to org-grain features (state-grain).

    Only sectors with a mapped service type get a value (saturation is a
    HH/hospice/SNF/lab prior, not a universal feature); everything else stays
    NaN — absent, never neutral-scored. No fan-out, asserted.
    """
    smap = sector_to_service or SECTOR_TO_SERVICE
    states = state_saturation_index(county_metrics)

    orgs = org_nodes.set_index("org_node_id")

    # COUNTY grain when the plumbing exists: org ZIP (org_nodes.addr_zip) +
    # a ZIP→county-FIPS crosswalk (HUD/Census, preclean/hud/zip_county.csv).
    # This is the audit's "local area" upgrade — the state fallback below has
    # ~50 distinct values per sector, so its top slice is substantially a
    # sector×state fixed effect, not local oversupply. County stays optional
    # (skip-missing): without the crosswalk, behavior is unchanged.
    zip2fips: dict[str, str] = {}
    if zip_to_county is not None and len(zip_to_county):
        z = zip_to_county.rename(columns={c: c.lower() for c in zip_to_county.columns})
        zc = [c for c in z.columns if c in ("zip", "zip_code", "zcta")]
        fc = [c for c in z.columns if "fips" in c or c in ("county", "geoid")]
        if zc and fc:
            zips = z[zc[0]].astype(str).str.strip().str.zfill(5)
            fips = z[fc[0]].astype(str).str.strip().str.zfill(5)
            zip2fips = dict(zip(zips, fips))
    by_county: dict[str, list[tuple[str, float]]] = {}
    if zip2fips:
        for r in county_metrics.itertuples():
            f = str(r.fips).strip()
            if f:
                by_county.setdefault(f.zfill(5), []).append(
                    (str(r.service_type).lower(), r.market_saturation_index))
        for f in by_county:
            by_county[f].sort(key=lambda t: (len(t[0]), t[0]))

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
        if not frag:
            return float("nan")
        if zip2fips:                                  # county first, when wired
            z = str(row.get("addr_zip", "") or "").strip()[:5]
            fips = zip2fips.get(z.zfill(5)) if z else None
            if fips:
                for svc, idx in by_county.get(fips, ()):
                    if frag in svc:
                        return idx
        state = str(row.get("addr_state", "") or "").strip().upper()
        if not state:
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
