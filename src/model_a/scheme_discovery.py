"""
scheme_discovery.py — mine the files we already have for schemes we don't run yet.

The runbook's "wrong-but-interesting" protocol and the vintage inspector's
orphan list both point at the same standing question: what detectable conduct
is sitting in procured files that no scheme consumes? This module answers it
mechanically, every run, instead of by occasional hand inspection:

  1. CANDIDATE SCHEMES — a curated menu of known-detectable schemes (each with
     the fraud rationale and the exact fields it needs). For each, the tool
     checks the CURRENT preclean tree: file present? fields present? → status
     READY TO WIRE / BLOCKED (names what's missing).
  2. UNUSED SIGNAL FIELDS — every data file's header is scanned for columns
     that look signal-bearing (rates, scores, counts, penalties, hours,
     ownership stakes...) but are not consumed by any wired adapter. These are
     the raw material for schemes nobody has designed yet.
  3. ORPHAN FOLDERS — folders no adapter reads (shared verdict with
     vintage_inspect): if the data is useful it enters through this report.

Read-only on inputs; writes preclean/SCHEME_DISCOVERY.md. Run after the
vintage inspector or any procurement session:

  python -m src.model_a.scheme_discovery --data-root C:/Users/treyr/OneDrive/Desktop/data
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from src.vintage_inspect import REQUIRED_COLS, _read_header

# column-name heuristic for "this column plausibly carries fraud signal"
SIGNAL_RE = re.compile(
    r"score|rate|pct|percent|ratio|count|num_|_num|amt|amount|charge|cost|"
    r"spend|payment|fine|penalt|deficien|citation|hours|hrs_|days|visit|"
    r"bed|stake|owner|percentage|measure|denial|revisit|complaint|staff|"
    r"turnover|minutes", re.IGNORECASE)

# fields the wired adapters already consume, beyond the REQUIRED_COLS minimum.
# Approximate by design: a fragment listed here marks matching columns "used".
CONSUMED_EXTRA = {
    "partb": ["Avg_Sbmtd_Chrg", "Avg_Mdcr_Pymt", "Tot_Benes", "Place_Of_Srvc"],
    "partd": ["Tot_Benes", "Tot_30day", "Tot_Day_Suply", "GE65"],
    "dmepos": ["Tot_Suplr", "Avg_Suplr"],
    "opioid": ["Opioid_Tot_Clms", "Opioid_Prscrbng_Rate", "LA_Tot_Clms"],
    "open_payments": ["Total_Amount_of_Payment", "Nature_of_Payment",
                      "Applicable_Manufacturer"],
    "pos": ["BED_CNT", "GNRL_FAC_TYPE", "STATE_CD", "CRTFD_BED_CNT"],
    "hcris": ["TOTAL_COSTS", "TOTAL_CHARGES"],
    "nadac": ["NADAC Per Unit", "Classification for Rate Setting"],
    "facility": ["Score", "Survey Date", "Hrs_RN", "MDScensus", "WorkDate",
                 "Measure Code", "Measure Date Range"],
    "saturation": ["Type of Service"],
    "docgraph": ["patient_count", "transaction_count"],
    "mue": ["MUE"],
}

# THE MENU — schemes we know how to detect, with the fields each needs.
# (source_folder, file name hint regex, [header fragments needed])
CANDIDATE_SCHEMES = [
    ("hospice_nf_care_share",
     "Hospices delivering most care inside nursing facilities (the Rhode "
     "Island Continuum-Care pattern generalized): a captive-referral setup "
     "where the hospice's census lives in partner SNFs, not homes.",
     "facility", r"hospice", ["Care_Provided_Nursing_Facility",
                              "Care_Provided_Skilled_Nursing"]),
    ("hospice_gip_share",
     "General-inpatient and continuous-home-care share outliers: the two "
     "highest-paid hospice levels, the OIG's standing intensity-upcoding "
     "target.",
     "facility", r"hospice", ["Care_Provided_Inpatient"]),
    ("hospice_visits_near_death",
     "No skilled visits in the last days of life: paid for crisis-level care "
     "that nobody delivered (worthless services at end of life).",
     "facility", r"hospice", ["H_009", "last_days_of_life"]),
    ("hospice_nurse_minutes",
     "Nurse minutes per routine-home-care day (incl. weekends): staffing "
     "far below what the billed level of care requires.",
     "facility", r"hospice", ["nurse_minutes", "H_010"]),
    ("hospice_spend_per_bene",
     "Per-beneficiary hospice spending outliers vs. peers: long-stay "
     "recruitment of non-terminal patients shows up as spend per head.",
     "facility", r"hospice", ["Spending_per_Beneficiary", "per_beneficiary"]),
    ("hospice_dementia_mix",
     "Share of beneficiaries with dementia: the classic long-stay "
     "recruitment profile (ineligibility risk concentrates here).",
     "facility", r"hospice", ["dementia"]),
    ("nh_ownership_stakes",
     "Nursing-home ownership percentages, roles, and association dates: "
     "refines related-party density and ownership-churn with exact stakes "
     "instead of edge counts.",
     "facility", r"ownership", ["Ownership Percentage", "Role"]),
    ("nh_penalties",
     "CMS fines and payment denials per facility: enforcement-adjacent "
     "corroboration (a scored lead that also carries penalties is warmer).",
     "facility", r"penalt", ["Fine Amount"]),
    ("pbj_weekend_staffing",
     "Weekend vs. weekday nurse staffing collapse in PBJ daily data: care "
     "billed daily but staffed only when inspectors work.",
     "facility", r"pbj", ["Hrs_RN", "WorkDate"]),
]


def _tree_headers(pre: Path) -> dict[str, list[str]]:
    """rel-path → header columns for every readable data file under preclean."""
    out: dict[str, list[str]] = {}
    for p in sorted(pre.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".csv", ".txt",
                                                       ".xlsx", ".xls",
                                                       ".parquet"):
            continue
        rel = str(p.relative_to(pre)).replace("\\", "/")
        if "_hold" in rel or rel.endswith(".bak") or p.name == "EDITIONS.csv":
            continue
        cols = _read_header(p)
        if cols:
            out[rel] = cols
    return out


# candidates that ARE wired (the export computes them): the report says so instead
# of "READY TO WIRE" forever. 2026-10-09: all three facility candidates had been
# wired since 2026-09 (facility.compute_nh_ownership / compute_nh_penalties /
# compute_pbj_weekend_gap) and the report kept listing them as waiting.
WIRED_CANDIDATES = {
    "nh_ownership_stakes": "facility.compute_nh_ownership -> nh_owner_count, nh_owner_concentration",
    "nh_penalties": "facility.compute_nh_penalties -> nh_penalty_count, nh_fine_total",
    "pbj_weekend_staffing": "facility.compute_pbj_weekend_gap -> pbj_weekend_understaffing",
}


def scheme_status(headers: dict[str, list[str]]) -> list[dict]:
    """Check every candidate scheme against the current tree."""
    out = []
    for name, why, folder, hint, need in CANDIDATE_SCHEMES:
        hint_re = re.compile(hint, re.IGNORECASE)
        matches = {rel: cols for rel, cols in headers.items()
                   if rel.split("/")[0] == folder and hint_re.search(rel)}
        best_file, missing = "", list(need)
        for rel, cols in matches.items():
            joined = " | ".join(str(c) for c in cols)
            # measure-style files carry values in rows, not headers: accept a
            # fragment found in either the header or the measure-code column's
            # domain by name convention (header-only check here; row-level
            # confirmation happens when the scheme is wired)
            miss = [f for f in need if f.lower() not in joined.lower()]
            if len(miss) < len(missing):
                best_file, missing = rel, miss
        if name in WIRED_CANDIDATES and matches and not missing:
            status = f"WIRED ({WIRED_CANDIDATES[name]})"
        elif not matches:
            status = "BLOCKED: no candidate file"
        elif not missing:
            status = "READY TO WIRE"
        else:
            status = f"CHECK ROWS: header lacks {', '.join(missing)}"
            # long-format measure files (Measure Code/Score) hide fields in
            # rows; flag as row-check rather than blocked
            probe = matches.get(best_file) or next(iter(matches.values()))
            if any("measure" in str(c).lower() for c in probe):
                status += " (long format — fragments may live in Measure Code rows)"
        out.append({"scheme": name, "why": why, "status": status,
                    "file": best_file or "/".join(sorted(matches)[:1]),
                    "needs": need})
    return out


def unused_signal_fields(headers: dict[str, list[str]]) -> list[dict]:
    """Per file: signal-looking columns no adapter consumes."""
    out = []
    for rel, cols in headers.items():
        folder = rel.split("/")[0]
        used = [str(x).lower() for x in
                REQUIRED_COLS.get(folder, []) + CONSUMED_EXTRA.get(folder, [])]
        cand = []
        for c in cols:
            cl = str(c).lower()
            if SIGNAL_RE.search(cl) and not any(u in cl or cl in u
                                                for u in used):
                cand.append(str(c))
        if cand:
            out.append({"file": rel, "unused": cand[:12],
                        "n_unused": len(cand)})
    return sorted(out, key=lambda r: -r["n_unused"])


def write_report(pre: Path, statuses: list[dict], unused: list[dict]) -> Path:
    L = ["# SCHEME DISCOVERY — what the files we already have could detect",
         "",
         "_Leads for design review: every candidate is a detection idea, "
         "never an accusation engine. A scheme graduates only with named "
         "drivers and the alternative-explanations frame._", ""]
    ready = [s for s in statuses if s["status"] == "READY TO WIRE"]
    L.append(f"_{len(ready)} of {len(statuses)} curated candidates are READY "
             f"TO WIRE against the current tree; "
             f"{len(unused)} files carry unused signal fields._")
    L.append("")
    L.append("## 1. Curated candidate schemes vs. the current tree")
    L.append("")
    L.append("| scheme | status | best file | needs |")
    L.append("|---|---|---|---|")
    for s in statuses:
        L.append(f"| {s['scheme']} | {s['status']} | {s['file']} "
                 f"| {', '.join(s['needs'])} |")
    L.append("")
    for s in statuses:
        L.append(f"**{s['scheme']}** — {s['why']}")
        L.append("")
    L.append("## 2. Unused signal fields (raw material for new schemes)")
    L.append("")
    L.append("Columns that look signal-bearing but no wired adapter reads. "
             "Paste any header row that looks interesting and it becomes a "
             "design conversation.")
    L.append("")
    L.append("| file | unused signal columns (first 12) | total |")
    L.append("|---|---|--:|")
    for r in unused:
        L.append(f"| {r['file']} | {', '.join(r['unused'])} | {r['n_unused']} |")
    out = pre / "SCHEME_DISCOVERY.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"[scheme_discovery] {len(ready)}/{len(statuses)} candidates ready; "
          f"{len(unused)} files with unused signal fields -> {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--data-root", required=True)
    args = ap.parse_args()
    pre = Path(args.data_root) / "preclean"
    headers = _tree_headers(pre)
    write_report(pre, scheme_status(headers), unused_signal_fields(headers))


if __name__ == "__main__":
    main()
