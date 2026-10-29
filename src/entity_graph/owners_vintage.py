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

Temporal note: the churn window is the span BETWEEN the two editions. On a
CURRENT-DAY run any chain is fair. On a FROZEN run, ``asof_editions`` filters
each dataset's chain to editions dated on/before the cutoff, so the diff
window ends at (or before) the freeze and the feature is leakage-correct —
a dataset with fewer than two pre-cutoff editions is skipped by name.
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


_DATASET_RE = re.compile(r"([A-Za-z]+)_All_Owners", re.I)
# a short family token at the front of the name (the archived 2023 editions arrived as
# snf_2023-12.csv / hha_2023-10.csv ...; 2026-10-29) maps to the SAME token the CMS download
# names yield under _DATASET_RE, so both namings chain in one per-dataset ladder
_SHORT_FAMILY = {"hospital": "HOSPITAL", "snf": "FACILITY", "hha": "AGENCY", "hospice": "HOSPICE",
                 "fqhc": "CENTER", "rhc": "CLINIC", "lab": "LABORATORY", "laboratory": "LABORATORY"}


def _dataset_of(name: str) -> str:
    m = _DATASET_RE.search(name)
    if m:
        return m.group(1).upper()
    head = re.split(r"[._\-\s]", Path(name).stem, maxsplit=1)[0].lower()
    return _SHORT_FAMILY.get(head, "OWNERS")


def _read_pairs(p: Path) -> pd.DataFrame | None:
    raw = read_csv_text(p)
    resolved = _resolve_columns(list(raw.columns), OWNER_VINTAGE_COLS)
    if len(resolved) < 2:
        return None
    f = raw.rename(columns={v: k for k, v in resolved.items()})
    f = f[["facility_enrollment_id", "owner_pac_id"]].astype(str)
    f = f[(f["facility_enrollment_id"].str.strip() != "")
          & (f["owner_pac_id"].str.strip() != "")].copy()
    f["owner_key"] = "pac" + f["owner_pac_id"].str.strip()
    return (f[["facility_enrollment_id", "owner_key"]]
            .drop_duplicates().reset_index(drop=True))


def load_editions(folder: str | Path) -> dict[str, list[tuple[str, pd.DataFrame]]]:
    """All-Owners CSVs grouped PER DATASET, then by edition date -> a dict
    {dataset: sorted [(edition_date, pairs), ...]}.

    Per-dataset matters: the operator's editions have MIXED cadences (FQHC is
    December-dated, SNF/HHA/etc are June-dated). A single global date chain
    would insert an FQHC-only edition between two SNF editions, and the
    both-endpoints guard would silently drop every SNF facility's churn at
    that step. Each dataset chains through its own editions only."""
    d = Path(folder)
    by_ds: dict[str, dict[str, list[pd.DataFrame]]] = {}
    for p in sorted(d.glob("*.csv")) if d.is_dir() else []:
        ed = _edition_date(p.name)
        if not ed:
            continue
        pairs = _read_pairs(p)
        if pairs is None:
            continue
        by_ds.setdefault(_dataset_of(p.name), {}).setdefault(ed, []).append(pairs)
    out: dict[str, list[tuple[str, pd.DataFrame]]] = {}
    for ds, by_ed in by_ds.items():
        out[ds] = [(ed, pd.concat(frames, ignore_index=True).drop_duplicates()
                    .reset_index(drop=True))
                   for ed, frames in sorted(by_ed.items())]
    return out


def asof_editions(editions: dict[str, list[tuple[str, pd.DataFrame]]],
                  cutoff: str,
                  ) -> tuple[dict, dict, list[str]]:
    """Filter per-dataset edition chains to editions dated ON/BEFORE
    ``cutoff`` (YYYY-MM), split into (prior, current) for
    ``multi_edition_turnover``. A dataset needs >=2 pre-cutoff editions to
    diff; the rest are returned in ``skipped`` by name so the run log can
    say exactly which archived edition would unlock them."""
    prior: dict[str, list[tuple[str, pd.DataFrame]]] = {}
    cur: dict[str, list[tuple[str, pd.DataFrame]]] = {}
    skipped: list[str] = []
    for ds, eds in editions.items():
        pre = sorted([(d, p) for d, p in eds if d[:7] <= str(cutoff)[:7]])
        if len(pre) >= 2:
            prior[ds] = pre[:-1]
            cur[ds] = [pre[-1]]
        else:
            skipped.append(f"{ds}: {len(pre)} edition(s) on/before "
                           f"{str(cutoff)[:7]}")
    return prior, cur, skipped


def merge_editions(*edition_dicts: dict[str, list[tuple[str, pd.DataFrame]]],
                   ) -> dict[str, list[tuple[str, pd.DataFrame]]]:
    """Union several load_editions() results (e.g. owners_prior + owners),
    deduped per dataset by edition date, sorted."""
    out: dict[str, dict[str, pd.DataFrame]] = {}
    for d in edition_dicts:
        for ds, eds in d.items():
            for ed, pairs in eds:
                out.setdefault(ds, {}).setdefault(ed, pairs)
    return {ds: sorted(by_ed.items()) for ds, by_ed in out.items()}


def multi_edition_turnover(prior_by_dataset: dict[str, list[tuple[str, pd.DataFrame]]],
                           current_by_dataset: dict[str, list[tuple[str, pd.DataFrame]]],
                           ) -> tuple[pd.DataFrame, list[str]]:
    """Per-DATASET consecutive edition chains -> summed entries/exits per
    facility. Each dataset chains its own prior editions (dropping any not
    strictly older than its current edition — a newer 'prior' would diff
    backwards in time) and ends at its current edition. Facilities are
    disjoint across datasets, so results concatenate. Returns (per-facility
    frame, human-readable chain descriptions)."""
    cols = ["facility_enrollment_id", "n_owner_entries", "n_owner_exits",
            "ownership_turnover"]
    totals: dict[str, list[int]] = {}
    chains: list[str] = []
    for ds, cur_eds in current_by_dataset.items():
        if ds not in prior_by_dataset or not cur_eds:
            continue
        cur_date, cur_pairs = sorted(cur_eds)[-1]        # the dataset's current
        eds = [(d, p) for d, p in prior_by_dataset[ds]
               if not cur_date or d[:7] < cur_date[:7]]
        if not eds:
            continue
        chain = [p for _, p in sorted(eds, key=lambda kv: kv[0])] + [cur_pairs]
        chains.append(f"{ds}: {' → '.join(d for d, _ in sorted(eds))}"
                      f" → {cur_date or 'current'}")
        for older, newer in zip(chain, chain[1:]):
            step = vintage_ownership_turnover(older, newer)
            for r in step.itertuples(index=False):
                t = totals.setdefault(r.facility_enrollment_id, [0, 0])
                t[0] += int(r.n_owner_entries)
                t[1] += int(r.n_owner_exits)
    if not totals:
        return pd.DataFrame(columns=cols), chains
    out = pd.DataFrame([(fid, e, x) for fid, (e, x) in sorted(totals.items())],
                       columns=["facility_enrollment_id", "n_owner_entries",
                                "n_owner_exits"])
    out["ownership_turnover"] = ((out["n_owner_entries"] + out["n_owner_exits"])
                                 / TURNOVER_SATURATION).clip(0, 1.0)
    return out[cols], chains


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
