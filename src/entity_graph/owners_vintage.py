"""
owners_vintage.py — ownership churn from two dated editions of the All-Owners files.

The designed churn path diffs MONTHLY graph snapshots, which needs months to
accumulate. But CMS keeps prior versions of every All-Owners dataset, so the
operator can download an older edition today and get a real before/after diff
immediately:

    preclean/owners/        the current edition (already feeding the graph)
    preclean/owners_prior/  an older edition (6-12 months back) of the same files

The diff runs on the RAW files at the stable PECOS keys — facility ENROLLMENT ID
x owner ASSOCIATE ID — so no graph resolution is needed to detect the change;
the result is then mapped enrollment -> NPI -> org for the registry feature.

  load_owner_pairs           folder of All-Owners CSVs -> distinct
                             (facility_enrollment_id, owner_key) + the edition
                             date parsed from the filenames.
  vintage_ownership_turnover prior + current pairs -> per-enrollment entries,
                             exits, ownership_turnover (0-1, saturating like
                             the snapshot path). Facilities absent from either
                             edition are excluded — a facility newly listed
                             would otherwise fake 100% owner turnover.
  org_grain_turnover         enrollment-grain churn -> org_node_id grain via
                             the PECOS npi_xwalk and npi_to_org (max per org).

Temporal note: the churn window is the span BETWEEN the two editions (e.g.
2025-10 -> 2026-05), which is typically post-cutoff — so this feature belongs
to the CURRENT-DAY matrix (current_state vintage class), never the frozen one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns, read_csv_text
from .ownership_churn import TURNOVER_SATURATION

OWNER_VINTAGE_COLS = {
    "facility_enrollment_id": ["ENROLLMENT ID"],
    "owner_pac_id": ["ASSOCIATE ID - OWNER"],
}

_DATE_RE = re.compile(r"(20\d{2})[._-](\d{2})(?:[._-](\d{2}))?")


def _edition_date(name: str) -> str:
    """'..._2025.10.01' -> 2025-10-01; '..._2023-06' -> 2023-06 (day optional)."""
    m = _DATE_RE.search(name)
    if not m:
        return ""
    return (f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m.group(3)
            else f"{m.group(1)}-{m.group(2)}")


def load_owner_pairs(folder: str | Path) -> tuple[pd.DataFrame, str]:
    """All-Owners CSVs in ``folder`` -> distinct (facility_enrollment_id,
    owner_key) pairs + the edition date (max date found in the filenames)."""
    d = Path(folder)
    frames, dates = [], []
    for p in sorted(d.glob("*.csv")) if d.is_dir() else []:
        raw = read_csv_text(p)
        resolved = _resolve_columns(list(raw.columns), OWNER_VINTAGE_COLS)
        if len(resolved) < 2:
            continue                       # not an All-Owners layout — skip
        f = raw.rename(columns={v: k for k, v in resolved.items()})
        f = f[["facility_enrollment_id", "owner_pac_id"]].astype(str)
        f = f[(f["facility_enrollment_id"].str.strip() != "")
              & (f["owner_pac_id"].str.strip() != "")]
        frames.append(f)
        if _edition_date(p.name):
            dates.append(_edition_date(p.name))
    if not frames:
        return pd.DataFrame(columns=["facility_enrollment_id", "owner_key"]), ""
    pairs = pd.concat(frames, ignore_index=True)
    pairs["owner_key"] = "pac" + pairs["owner_pac_id"].str.strip()
    return (pairs[["facility_enrollment_id", "owner_key"]].drop_duplicates()
            .reset_index(drop=True), max(dates) if dates else "")


def load_editions(folder: str | Path) -> list[tuple[str, pd.DataFrame]]:
    """All-Owners CSVs grouped BY EDITION DATE (parsed per filename) -> a sorted
    list of (edition_date, pairs). Lets the operator stage several historical
    editions (2023, 2024, 2025) and get churn from CONSECUTIVE diffs — an owner
    who arrived and left between editions is invisible to a single two-point
    diff but visible here."""
    d = Path(folder)
    by_ed: dict[str, list[pd.DataFrame]] = {}
    for p in sorted(d.glob("*.csv")) if d.is_dir() else []:
        ed = _edition_date(p.name)
        if not ed:
            continue
        raw = read_csv_text(p)
        resolved = _resolve_columns(list(raw.columns), OWNER_VINTAGE_COLS)
        if len(resolved) < 2:
            continue
        f = raw.rename(columns={v: k for k, v in resolved.items()})
        f = f[["facility_enrollment_id", "owner_pac_id"]].astype(str)
        f = f[(f["facility_enrollment_id"].str.strip() != "")
              & (f["owner_pac_id"].str.strip() != "")]
        by_ed.setdefault(ed, []).append(f)
    out = []
    for ed in sorted(by_ed):
        pairs = pd.concat(by_ed[ed], ignore_index=True)
        pairs["owner_key"] = "pac" + pairs["owner_pac_id"].str.strip()
        out.append((ed, pairs[["facility_enrollment_id", "owner_key"]]
                    .drop_duplicates().reset_index(drop=True)))
    return out


def multi_edition_turnover(editions: list[tuple[str, pd.DataFrame]],
                           current: pd.DataFrame) -> pd.DataFrame:
    """Sum entries/exits across CONSECUTIVE edition diffs (oldest -> ... ->
    current), each diff restricted to facilities present in both of its
    endpoints. Returns the same shape as ``vintage_ownership_turnover``."""
    cols = ["facility_enrollment_id", "n_owner_entries", "n_owner_exits",
            "ownership_turnover"]
    chain = [p for _, p in sorted(editions, key=lambda kv: kv[0])] + [current]
    if len(chain) < 2:
        return pd.DataFrame(columns=cols)
    totals: dict[str, list[int]] = {}
    for older, newer in zip(chain, chain[1:]):
        step = vintage_ownership_turnover(older, newer)
        for r in step.itertuples(index=False):
            t = totals.setdefault(r.facility_enrollment_id, [0, 0])
            t[0] += int(r.n_owner_entries)
            t[1] += int(r.n_owner_exits)
    if not totals:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame([(fid, e, x) for fid, (e, x) in sorted(totals.items())],
                       columns=["facility_enrollment_id", "n_owner_entries",
                                "n_owner_exits"])
    out["ownership_turnover"] = ((out["n_owner_entries"] + out["n_owner_exits"])
                                 / TURNOVER_SATURATION).clip(0, 1.0)
    return out[cols]


def vintage_ownership_turnover(prior: pd.DataFrame,
                               current: pd.DataFrame) -> pd.DataFrame:
    """Per-facility owner entries/exits between two editions -> churn 0-1."""
    cols = ["facility_enrollment_id", "n_owner_entries", "n_owner_exits",
            "ownership_turnover"]
    both = (set(prior["facility_enrollment_id"])
            & set(current["facility_enrollment_id"]))
    if not both:
        return pd.DataFrame(columns=cols)
    p = prior[prior["facility_enrollment_id"].isin(both)]
    c = current[current["facility_enrollment_id"].isin(both)]
    p_pairs = set(map(tuple, p.itertuples(index=False)))
    c_pairs = set(map(tuple, c.itertuples(index=False)))
    entries = pd.DataFrame(list(c_pairs - p_pairs), columns=["fid", "owner_key"])
    exits = pd.DataFrame(list(p_pairs - c_pairs), columns=["fid", "owner_key"])
    ent = entries.groupby("fid").size() if len(entries) else pd.Series(dtype=int)
    exi = exits.groupby("fid").size() if len(exits) else pd.Series(dtype=int)
    out = pd.DataFrame({"facility_enrollment_id": sorted(both)})
    out["n_owner_entries"] = out["facility_enrollment_id"].map(ent).fillna(0).astype(int)
    out["n_owner_exits"] = out["facility_enrollment_id"].map(exi).fillna(0).astype(int)
    total = out["n_owner_entries"] + out["n_owner_exits"]
    out["ownership_turnover"] = (total / TURNOVER_SATURATION).clip(0, 1.0)
    return out[cols]


def org_grain_turnover(turnover: pd.DataFrame, npi_xwalk: pd.DataFrame,
                       npi_to_org: pd.DataFrame) -> pd.DataFrame:
    """Enrollment-grain churn -> org_node_id grain (max churn per org)."""
    cols = ["org_node_id", "ownership_turnover", "n_owner_entries", "n_owner_exits"]
    xw = npi_xwalk.copy()
    if "enrollment_id" not in xw.columns or "npi" not in xw.columns:
        return pd.DataFrame(columns=cols)
    xw["enrollment_id"] = xw["enrollment_id"].astype(str)
    xw["npi"] = xw["npi"].astype(str)
    enr2npi = (xw[xw["enrollment_id"] != ""]
               .drop_duplicates("enrollment_id").set_index("enrollment_id")["npi"])
    t = turnover.copy()
    t["npi"] = t["facility_enrollment_id"].astype(str).map(enr2npi)
    t = t[t["npi"].notna()]
    n2o = npi_to_org[["npi", "org_node_id"]].astype(str).drop_duplicates("npi")
    t = t.merge(n2o, on="npi", how="inner")
    if not len(t):
        return pd.DataFrame(columns=cols)
    g = t.groupby("org_node_id", as_index=False).agg(
        ownership_turnover=("ownership_turnover", "max"),
        n_owner_entries=("n_owner_entries", "sum"),
        n_owner_exits=("n_owner_exits", "sum"))
    return g[cols]
