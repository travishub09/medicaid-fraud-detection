#!/usr/bin/env python3
"""
build_final_leads.py  (attempt_2)

Remove the OBVIOUS institutional false positives from company_leads_clean.csv,
producing FinalLeads.csv in the SAME columns/order (quarantine, never silently delete).

PASS 1 — institutional NAME filter (government, tribal, public/academic, large national
         nonprofits).
PASS 2 — HOSPITAL / FQHC removal by registered taxonomy code AND by name.

Inputs (read-only): company_leads_clean.csv (from finalize_tracker), found under detection/.
Outputs: FinalLeads.csv + FinalLeads_removed_audit.csv, written to detection/ AND to the
"linked in ads" hand-off folder.
"""
import csv
import re
import sys
from pathlib import Path

import pandas as pd

from ..clean_data import PRECLEAN_DIR

DATA = PRECLEAN_DIR.parent
def _find(name):
    for root in [DATA / "detection", DATA]:
        if (root / name).exists():
            return root / name
    hits = sorted(DATA.rglob(name))
    if not hits:
        raise FileNotFoundError(name)
    return hits[0]

INPUT_CSV = _find("company_leads_clean.csv")
OUT_DIRS = [DATA / "detection", Path.home() / "Desktop" / "linked in ads"]

NAME_COL = "company_name"
TAX_COL = "specialty"

GOVERNMENT = ["COUNTY OF","CITY OF","STATE OF","COMMONWEALTH OF","TOWN OF","VILLAGE OF",
    "BOROUGH OF","PARISH OF","DEPARTMENT OF","DEPT OF","BOARD OF","HOSPITAL DISTRICT",
    "HEALTH DISTRICT","HEALTHCARE DISTRICT","HEALTH AUTHORITY","HOSPITAL AUTHORITY","PUBLIC HEALTH",
    "MUNICIPAL","HEALTH AND HOSPITALS","HOSPITAL SERVICE DISTRICT","HEALTH CARE DISTRICT",
    "HEALTHCARE AUTHORITY","MEDICAL CENTER AUTHORITY","SERVICE DISTRICT NO"]
TRIBAL = ["TRIBE","TRIBAL","RANCHERIA","PUEBLO","NATION","BAND OF","NATIVE AMERICAN","INDIAN HEALTH",
    "INDIAN NATION","INDIAN TRIBE","REGIONAL HEALTH CONSORTIUM","NATIVE ASSOCIATION","ALASKA NATIVE"]
PUBLIC_ACADEMIC = ["UNIVERSITY","REGENTS OF","BOARD OF REGENTS","STATE COLLEGE","COLLEGE OF MEDICINE"]
NATIONAL_NONPROFITS = ["VOLUNTEERS OF AMERICA","SALVATION ARMY","GOODWILL","CATHOLIC CHARITIES",
    "EASTERSEALS","EASTER SEALS","SHRINERS","BANCROFT","MELMARK","THE ARC","ARC OF","UNITED WAY",
    "YMCA","YWCA","RED CROSS","LUTHERAN SOCIAL SERVICES","JEWISH FAMILY","ST JUDE","BOYS AND GIRLS",
    "BOYS GIRLS CLUB"]
HOSP_TAX_PREFIXES = ("282", "283", "273")
FQHC_TAX = {"261QF0400X"}
HOSPITAL_NAMES = ["HOSPITAL","MEDICAL CENTER","MED CENTER","MED CTR","INFIRMARY","HEALTH SYSTEM",
    "HEALTHCARE SYSTEM","HEALTH NETWORK","CLINIC FOUNDATION","ASCENSION","PROVIDENCE HEALTH",
    "TRINITY HEALTH","ADVENTIST","CATHOLIC HEALTH","DIGNITY HEALTH","COMMONSPIRIT","KAISER",
    "MAYO CLINIC","CLEVELAND CLINIC","INTERMOUNTAIN","SUTTER","GEISINGER","OCHSNER","BANNER HEALTH"]
NAME_RULES = [("government", GOVERNMENT), ("tribal", TRIBAL), ("public_academic", PUBLIC_ACADEMIC),
              ("national_nonprofit", NATIONAL_NONPROFITS), ("hospital_name", HOSPITAL_NAMES)]
TAX_CODE_RE = re.compile(r"([0-9]{3}[A-Z0-9]{6}X)")


def normalize(name):
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", str(name or "").upper())).strip()

def taxonomy_code(specialty):
    m = TAX_CODE_RE.search(str(specialty or "").upper())
    return m.group(1) if m else ""

def classify(norm_name, tax):
    if tax in FQHC_TAX:
        return "fqhc_taxonomy", tax
    if tax.startswith(HOSP_TAX_PREFIXES):
        return "hospital_taxonomy", tax
    for category, keywords in NAME_RULES:
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", norm_name):
                return category, kw
        if category == "government" and re.search(r"\bCOUNTY$", norm_name):
            return category, "ENDS WITH COUNTY"
    return None, None


def main():
    df = pd.read_csv(INPUT_CSV, dtype=str, keep_default_na=False)
    total_in = len(df)
    for col in (NAME_COL, TAX_COL):
        if col not in df.columns:
            print(f"ERROR: expected column '{col}' not in input.", file=sys.stderr); return 1

    cats, toks = [], []
    for nm, sp in zip(df[NAME_COL], df[TAX_COL]):
        c, t = classify(normalize(nm), taxonomy_code(sp))
        cats.append(c); toks.append(t)
    removed = pd.Series([c is not None for c in cats], index=df.index)

    final_df = df.loc[~removed].copy()
    audit_df = df.loc[removed].copy()
    audit_df["removed_category"] = [c for c, k in zip(cats, removed) if k]
    audit_df["matched"] = [t for t, k in zip(toks, removed) if k]

    for d in OUT_DIRS:
        d.mkdir(parents=True, exist_ok=True)
        final_df.to_csv(d / "FinalLeads.csv", index=False, quoting=csv.QUOTE_MINIMAL)
        audit_df.to_csv(d / "FinalLeads_removed_audit.csv", index=False, quoting=csv.QUOTE_MINIMAL)

    from collections import Counter
    per_cat = Counter(c for c in cats if c)
    print("=" * 60)
    print(f"build_final_leads.py — input {INPUT_CSV.name} ({total_in} rows)")
    print("-" * 60)
    for label in ["government","tribal","public_academic","national_nonprofit",
                  "hospital_taxonomy","fqhc_taxonomy","hospital_name"]:
        print(f"  removed {label:<20} {per_cat.get(label,0):>6}")
    print("-" * 60)
    print(f"  total removed:   {int(removed.sum())}")
    print(f"  FinalLeads.csv:  {len(final_df)} rows")
    print("  tier_label:", dict(Counter(final_df['tier_label'])))
    for d in OUT_DIRS:
        print(f"  -> {d / 'FinalLeads.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
