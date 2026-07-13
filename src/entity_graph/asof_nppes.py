"""
asof_nppes.py — freeze the co-location graph substrate to a historical NPPES edition.

The as-of graph froze exclusion dates and owner-edge association dates, but the
co-location edges (who shares an address) were built from the CURRENT NPPES
address file. That is a leak: a fraud ring that re-formed at a shell address
AFTER the cutoff hands a future-banned provider a fresh address edge into the
pre-cutoff graph, visible only because the future happened. And it lands exactly
on shell_score, which is the one feature carrying the network edge and which
sits in only the with-network arm of the A/B, so the "both arms share the
current-state columns" defense does not cover it.

NBER (and the NPPES monthly dissemination archive) keep historical monthly
editions, so the fix is real: rebuild the address key from a ~cutoff-month NPPES
edition and overlay it onto provider_dim before the graph builds co-location
edges. Providers not present (or not yet enumerated) in that edition get a blank
address key and drop out of the co-location layer, which is correct: they were
not at that address, at that time, in a world that could not see the future.

  build_asof_addresses(nppes_path, cutoff)  historical NPPES edition ->
      npi, addr_key, addr_state, using the SHARED normalizer, filtered to NPIs
      enumerated on/before the cutoff and not deactivated on/before it.
  apply_asof_addresses(provider_dim, asof_addr)  overlay the frozen address
      columns onto provider_dim by NPI (blank where absent) -> new provider_dim.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import _standardize_address, read_csv_text

# NPPES dissemination column names (same aliases clean_data uses for provider_dim)
_NPPES_COLS = {
    "npi": ["NPI", "npi"],
    "line1": ["Provider First Line Business Practice Location Address",
              "practice_address", "addr_line1"],
    "city": ["Provider Business Practice Location Address City Name",
             "practice_city", "addr_city"],
    "state": ["Provider Business Practice Location Address State Name",
              "practice_state", "addr_state"],
    "zip": ["Provider Business Practice Location Address Postal Code",
            "practice_zip", "addr_zip"],
    "enumeration": ["Provider Enumeration Date", "enumeration_date"],
    "deactivation": ["NPI Deactivation Date", "deactivation"],
}


def _ym(s) -> str:
    return str(s)[:7]


def _pick(cols, header):
    for c in cols:
        if c in header:
            return c
    return None


def _to_month(series: pd.Series) -> pd.Series:
    """NPPES dates are MM/DD/YYYY; normalize to YYYY-MM for comparison."""
    dt = pd.to_datetime(series, errors="coerce")
    return dt.dt.strftime("%Y-%m")


def build_asof_addresses(nppes_path: str | Path, cutoff: str) -> pd.DataFrame:
    """Historical NPPES edition -> npi, addr_key, addr_state as of ``cutoff``.

    ``cutoff`` is YYYY-MM. Rows are kept only when the provider was enumerated
    on/before the cutoff and not deactivated on/before it (when those columns
    are present). Uses the shared address normalizer so the key matches the one
    the live graph builds."""
    p = Path(nppes_path)
    header = list(read_csv_text(p, nrows=0).columns) if p.suffix.lower() != ".parquet" \
        else list(pd.read_parquet(p, columns=None).columns[:0]) or \
        list(pd.read_parquet(p).columns)
    resolved = {k: _pick(v, header) for k, v in _NPPES_COLS.items()}
    if not resolved.get("npi"):
        raise ValueError(f"NPPES edition missing an NPI column; saw {header[:8]}")
    use = [c for c in resolved.values() if c]
    if p.suffix.lower() == ".parquet":
        raw = pd.read_parquet(p, columns=use)
    else:
        raw = read_csv_text(p, usecols=use)
    df = raw.rename(columns={v: k for k, v in resolved.items() if v})
    df["npi"] = df["npi"].astype(str).str.strip()
    cut = _ym(cutoff)
    # enumeration on/before cutoff; deactivation strictly after (or none)
    if "enumeration" in df.columns:
        enum_m = _to_month(df["enumeration"])
        df = df[enum_m.isna() | (enum_m <= cut)]
    if "deactivation" in df.columns:
        deact_m = _to_month(df["deactivation"])
        df = df[deact_m.isna() | (deact_m > cut)]
    df["addr_key"] = _standardize_address(df, prefix="")
    df["addr_state"] = df.get("state", pd.Series("", index=df.index)).fillna("").astype(str).str.upper()
    out = (df[["npi", "addr_key", "addr_state"]]
           .drop_duplicates("npi").reset_index(drop=True))
    return out


def apply_asof_addresses(provider_dim: pd.DataFrame,
                         asof_addr: pd.DataFrame) -> pd.DataFrame:
    """Overlay frozen address columns onto provider_dim by NPI. Providers absent
    from the historical edition get a blank addr_key (they drop out of the
    co-location layer, which is the correct frozen behavior)."""
    pd_ = provider_dim.copy()
    pd_["npi"] = pd_["npi"].astype(str)
    a = asof_addr[["npi", "addr_key", "addr_state"]].copy()
    a["npi"] = a["npi"].astype(str)
    amap = a.drop_duplicates("npi").set_index("npi")
    pd_["addr_key"] = pd_["npi"].map(amap["addr_key"]).fillna("")
    if "addr_state" in pd_.columns:
        pd_["addr_state"] = pd_["npi"].map(amap["addr_state"]).fillna(pd_["addr_state"])
    else:
        pd_["addr_state"] = pd_["npi"].map(amap["addr_state"]).fillna("")
    return pd_
