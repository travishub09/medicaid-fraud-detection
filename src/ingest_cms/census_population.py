"""
census_population.py — Census county population + ZIP→county crosswalk (sweep 2.x).

The free Census/HUD files that supply the "local denominator" half of clinical
plausibility (A5) and add context to market saturation. Two small parsers:

  county_population   Census county population estimates → fips (5-digit string,
                      leading zeros kept), population, county_name, state.
  zip_to_county       HUD/Census ZIP→county crosswalk → zip, fips, res_ratio
                      (the residential share, for assigning a ZIP to its dominant
                      county). The bridge that lets an org's address ZIP attach
                      to a county FIPS, which is what the denominator needs.

Identifiers are strings throughout (FIPS and ZIP keep leading zeros — hard rule
#1). Dormant until the files are loaded; the plausibility denominator
(``analytics/plausibility.local_denominator_plausibility``) consumes the output.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns

POP_COLS = {
    "state_fips": ["STATE", "state", "STATEFP"],
    "county_fips": ["COUNTY", "county", "COUNTYFP"],
    "population": ["POPESTIMATE2023", "POPESTIMATE2022", "population",
                   "POPULATION", "POPESTIMATE"],
    "county_name": ["CTYNAME", "county_name", "NAME"],
    "state_name": ["STNAME", "state_name"],
    "fips": ["FIPS", "fips", "GEOID", "STCOU"],
}
ZIP_COLS = {
    "zip": ["ZIP", "zip", "ZIPCODE", "zip_code"],
    "fips": ["COUNTY", "county", "FIPS", "fips", "GEOID"],
    "res_ratio": ["RES_RATIO", "res_ratio", "TOT_RATIO", "tot_ratio"],
}


def _pad(s: pd.Series, width: int) -> pd.Series:
    return s.fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True).str.zfill(width)


def county_population(raw: pd.DataFrame) -> pd.DataFrame:
    """Census county estimates → fips, population, county_name, state."""
    resolved = _resolve_columns(list(raw.columns), POP_COLS)
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()
    if "fips" in df.columns:
        df["fips"] = _pad(df["fips"], 5)
    elif "state_fips" in df.columns and "county_fips" in df.columns:
        df["fips"] = _pad(df["state_fips"], 2) + _pad(df["county_fips"], 3)
    else:
        raise ValueError(f"county population file missing FIPS columns; "
                         f"saw {list(raw.columns)[:12]}")
    df["population"] = pd.to_numeric(df.get("population"), errors="coerce")
    for c in ("county_name", "state_name"):
        df[c] = (df[c] if c in df.columns else "").fillna("").astype(str).str.strip()
    # county rows only (drop state totals where county FIPS == 000)
    out = df[df["fips"].str.slice(2) != "000"][
        ["fips", "population", "county_name", "state_name"]]
    return out[out["population"].notna()].reset_index(drop=True)


def zip_to_county(raw: pd.DataFrame) -> pd.DataFrame:
    """HUD/Census ZIP→county crosswalk → zip, fips, res_ratio (dominant county
    per ZIP kept when a ZIP spans counties)."""
    resolved = _resolve_columns(list(raw.columns), ZIP_COLS)
    if "zip" not in resolved or "fips" not in resolved:
        raise ValueError(f"ZIP→county file missing zip/county columns; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()
    df["zip"] = _pad(df["zip"], 5)
    df["fips"] = _pad(df["fips"], 5)
    df["res_ratio"] = pd.to_numeric(df.get("res_ratio", 1.0), errors="coerce").fillna(1.0)
    # keep the dominant county per ZIP
    df = df.sort_values("res_ratio", ascending=False).drop_duplicates("zip")
    return df[["zip", "fips", "res_ratio"]].reset_index(drop=True)
