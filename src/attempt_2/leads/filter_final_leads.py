#!/usr/bin/env python3
"""
filter_final_leads.py  (attempt_2)

Final filtration of FinalLeads.csv: removes the FP classes that build_final_leads does
NOT cover — individuals, fiscal intermediaries (FMS conduits), routine-testing lab giants —
then dedupes and adds a lead_strength band. Quarantine, never silently delete. Output to the
"linked in ads" hand-off folder, keeping the FinalLeads schema (+ lead_strength).
"""
import csv
import re
from collections import Counter
from pathlib import Path

from ..clean_data import PRECLEAN_DIR

DATA = PRECLEAN_DIR.parent
def _find(name):
    for root in [Path.home() / "Desktop/linked in ads", DATA / "detection", DATA]:
        if (root / name).exists():
            return root / name
    hits = sorted(DATA.rglob(name))
    if not hits:
        raise FileNotFoundError(name)
    return hits[0]

INPUT = _find("FinalLeads.csv")
OUT_DIR = Path.home() / "Desktop/linked in ads"
OUT_FINAL = OUT_DIR / "FinalLeads_filtered.csv"
OUT_EXCL = OUT_DIR / "FinalLeads_filtered_excluded.csv"

FISCAL = ["PUBLIC PARTNERSHIPS","CONSUMER DIRECT CARE","ACUMEN FISCAL","GT INDEPENDENCE",
    "TEMPUS UNLIMITED","PALCO","MORNING SUN FINANCIAL","ANNKISSAM","FISCAL"]
LABS = ["LABORATORY CORPORATION OF AMERICA","LABCORP","QUEST DIAGNOSTICS"]
CORP_TOKENS = {"LLC","INC","INCORPORATED","CORP","CORPORATION","CO","COMPANY","LTD","LLP","PLLC",
    "PC","PA","CENTER","CENTRE","SERVICES","SERVICE","CARE","HEALTH","HEALTHCARE","CLINIC","AGENCY",
    "GROUP","ASSOCIATES","ASSOCIATION","FOUNDATION","HOSPITAL","HOME","SYSTEM","SYSTEMS","PARTNERS",
    "NETWORK","SOLUTIONS","INSTITUTE","ENTERPRISES","WELLNESS","RECOVERY","BEHAVIORAL","FAMILY",
    "COMMUNITY","THERAPY","MEDICAL"}
PERSON_RE = re.compile(r"^[A-Z][A-Za-z.'\-]+,\s+[A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]*\.?){0,2}$")


def normalize(s):
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", str(s or "").upper())).strip()

def _wb(kw, n):
    return re.search(r"\b" + re.escape(kw) + r"\b", n) is not None

def _has_corp_token(name):
    toks = normalize(name).split()
    if set(toks) & CORP_TOKENS:
        return True
    merged, buf = [], ""
    for t in toks:
        if len(t) == 1:
            buf += t
        else:
            if buf:
                merged.append(buf); buf = ""
            merged.append(t)
    if buf:
        merged.append(buf)
    return bool(set(merged) & CORP_TOKENS)

def fp_category(name):
    if PERSON_RE.match(name.strip()) and not _has_corp_token(name):
        return ("individual", "person-name")
    n = normalize(name)
    for kw in LABS:
        if _wb(kw, n):
            return ("labs", kw)
    for kw in FISCAL:
        if _wb(kw, n):
            return ("fiscal_intermediary", kw)
    return None

def strength(row):
    leie = str(row.get("any_provider_on_leie", "")).lower() == "true"
    if leie or str(row.get("tier_label", "")).startswith("Direct"):
        return "high"
    try:
        s = float(row.get("company_anomaly_score") or 0)
    except ValueError:
        s = 0.0
    return "high" if s >= 0.9 else "medium" if s >= 0.8 else "low"


def main():
    rows = list(csv.DictReader(open(INPUT, encoding="utf-8", errors="replace")))
    cols = list(rows[0].keys()) + ["lead_strength"]
    kept, excl, fp_counts, seen, n_dup = [], [], Counter(), {}, 0
    for r in rows:
        name = r["company_name"].strip()
        if not name:
            excl.append({"company_name": "", "states": r.get("states", ""), "reason": "missing_name", "matched": ""})
            fp_counts["missing_name"] += 1; continue
        cat = fp_category(name)
        if cat:
            excl.append({"company_name": name, "states": r.get("states", ""), "reason": cat[0], "matched": cat[1]})
            fp_counts[cat[0]] += 1; continue
        key = (normalize(name), normalize(r.get("states", "").split(";")[0]))
        if key in seen:
            excl.append({"company_name": name, "states": r.get("states", ""), "reason": "duplicate", "matched": seen[key]})
            n_dup += 1; continue
        seen[key] = name
        r = dict(r); r["lead_strength"] = strength(r)
        kept.append(r)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_FINAL, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader(); w.writerows(kept)
    with open(OUT_EXCL, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["company_name", "states", "reason", "matched"])
        w.writeheader(); w.writerows(excl)

    assert len(kept) + len(excl) == len(rows), "reconcile fail"
    print("=" * 56)
    print(f"filter_final_leads.py — input {INPUT.name} ({len(rows)} rows)")
    print("-" * 56)
    for k, v in fp_counts.most_common():
        print(f"  removed {k:<20} {v}")
    print(f"  removed {'duplicate':<20} {n_dup}")
    print(f"  => FINAL kept: {len(kept)}")
    print(f"  tier_label: {dict(Counter(r['tier_label'] for r in kept))}")
    print(f"  lead_strength: {dict(Counter(r['lead_strength'] for r in kept))}")
    print(f"  -> {OUT_FINAL}")
    print(f"  -> {OUT_EXCL}")


if __name__ == "__main__":
    main()
