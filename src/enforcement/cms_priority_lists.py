"""
cms_priority_lists.py — the CMS enforcement-wave overlays (Run 2 §I2).

Three tiny curated files turn the April-2026 CMS revalidation directive into
per-provider flags for the government-interest overlay and Model C:

  preclean/cms_priority/moratoria.csv          sector,scope,effective_date
      (e.g. "home_health,nationwide,2026-05-01" — CMS's own highest-risk call)
  preclean/cms_priority/revalidation_due.csv   npi,due_date
      (the Revalidation Due Date List — overdue = operating unrevalidated)
  preclean/cms_priority/sff.csv                ccn,status
      (Special Focus Facility list — persistent quality failures)

  priority_flags(...) → per-NPI: under_enrollment_moratorium (sector matched via
  the taxonomy→sector map), revalidation_overdue (due < asof), is_sff (via the
  ccn→npi crosswalk). Overlay/Model-C inputs — context multipliers, not fraud
  evidence; they never enter the anomaly ranking.

Curated tables are refreshed by hand (like the OIG Work Plan table) — cite the
CMS source in the file when updating.
"""

from __future__ import annotations

import pandas as pd

from src.model_a.sector_priors import sector_for_taxonomy


def load_moratoria(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(columns={c: c.strip().lower() for c in raw.columns}).copy()
    for c in ("sector", "scope", "effective_date"):
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str).str.strip()
    df["sector"] = df["sector"].str.lower()
    return df[["sector", "scope", "effective_date"]]


def load_revalidation_due(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(columns={c: c.strip().lower() for c in raw.columns}).copy()
    npi_col = next((c for c in df.columns if "npi" in c), None)
    due_col = next((c for c in df.columns if "due" in c or "date" in c), None)
    if not npi_col or not due_col:
        return pd.DataFrame(columns=["npi", "due_date"])
    out = pd.DataFrame({"npi": df[npi_col].astype(str).str.strip(),
                        "due_date": df[due_col].astype(str).str.slice(0, 10)})
    return out[out["npi"].str.len() >= 10].drop_duplicates("npi")


def load_sff(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(columns={c: c.strip().lower() for c in raw.columns}).copy()
    ccn_col = next((c for c in df.columns if "ccn" in c or "provnum" in c), None)
    if not ccn_col:
        return pd.DataFrame(columns=["ccn"])
    ccn = df[ccn_col].fillna("").astype(str).str.strip().str.upper()
    ccn = ccn.where(~ccn.str.fullmatch(r"\d{1,6}").fillna(False), ccn.str.zfill(6))
    return pd.DataFrame({"ccn": ccn[ccn != ""]}).drop_duplicates()


def priority_flags(providers: pd.DataFrame, asof: str,
                   moratoria: pd.DataFrame | None = None,
                   revalidation: pd.DataFrame | None = None,
                   sff: pd.DataFrame | None = None,
                   ccn_to_npi: pd.DataFrame | None = None) -> pd.DataFrame:
    """providers: npi + primary_taxonomy. Returns per-NPI overlay flags (only
    the flags whose input tables were supplied — skip-missing)."""
    out = pd.DataFrame({"npi": providers["npi"].astype(str)})
    if moratoria is not None and len(moratoria):
        hot = set(moratoria["sector"])
        sectors = providers["primary_taxonomy"].map(sector_for_taxonomy)
        out["under_enrollment_moratorium"] = sectors.isin(hot).astype(int).to_numpy()
    if revalidation is not None and len(revalidation):
        due = dict(zip(revalidation["npi"], revalidation["due_date"]))
        d = out["npi"].map(due)
        out["revalidation_overdue"] = ((d.notna()) & (d < str(asof)[:10])).astype(int)
    if sff is not None and len(sff) and ccn_to_npi is not None and len(ccn_to_npi):
        xw = ccn_to_npi.copy()
        xw["ccn"] = xw["ccn"].astype(str).str.strip().str.upper()
        xw["npi"] = xw["npi"].astype(str)
        sff_npis = set(xw[xw["ccn"].isin(set(sff["ccn"]))]["npi"])
        out["is_sff"] = out["npi"].isin(sff_npis).astype(int)
    assert len(out) == len(providers), "priority flags fanned out"
    return out
