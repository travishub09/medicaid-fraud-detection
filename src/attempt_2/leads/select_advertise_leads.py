#!/usr/bin/env python3
"""
select_advertise_leads.py  (attempt_2)

Final SELECTION step that turns the full scored company set (company_rollup.parquet)
into the advertising hand-off list. Runs AFTER anomaly detection (anomaly scores are
already computed on the full population by refine_layer2_v3 -> company_rollup).

Selection (the anomaly bar is the explicit constant below — set to 0.70):
  * ANOMALY tier : max_anomaly_score_v3 >= ANOMALY_SCORE_MIN  AND  company_net_paid >= DOLLAR_MIN
  * LEIE tier    : any_provider_on_leie                        AND  company_net_paid >= DOLLAR_MIN
                   (documented exclusion leads — NOT gated by the anomaly score)
Then: institutional / fiscal-intermediary / lab / individual FP removal (quarantined),
dedupe, lead_strength bands. Output goes to ~/Desktop/linked in ads/ (root).

NOTE: the repo's detector gates anomaly LEADS by concept-signal count, not by a score
cut; this file introduces the score-based selection threshold as an explicit constant so
it can be tuned in one place. Lowered to 0.70 here.
"""
import csv
import re
from pathlib import Path

import duckdb

# ----- tunable thresholds (the anomaly bar lives here) -----
ANOMALY_SCORE_MIN = 0.70          # was effectively ~0.80 in earlier hand-selection; lowered to 0.70
DOLLAR_MIN = 10_000_000

# ----- paths -----
def _find_rollup() -> Path:
    for p in [Path.home() / "Desktop/data/detection/tables/company_rollup.parquet",
              Path.home() / "Desktop/data/detection/company_rollup.parquet"]:
        if p.exists():
            return p
    raise FileNotFoundError("company_rollup.parquet not found")

ROLLUP = _find_rollup()
OUT_DIR = Path.home() / "Desktop/linked in ads"
OUT_FULL = OUT_DIR / "leads_0.70anomaly_leie_10m_full.csv"        # file 1: selection, ALL features
OUT_CLEAN = OUT_DIR / "leads_0.70anomaly_leie_10m_cleaned.csv"    # file 2: after secondary FP cleaning
OUT_EXCL = OUT_DIR / "leads_0.70anomaly_leie_10m_excluded.csv"    # quarantine (what cleaning removed)

# ----- state maps -----
STATE_FULL = {"AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California",
 "CO":"Colorado","CT":"Connecticut","DE":"Delaware","DC":"District of Columbia","FL":"Florida",
 "GA":"Georgia","HI":"Hawaii","ID":"Idaho","IL":"Illinois","IN":"Indiana","IA":"Iowa","KS":"Kansas",
 "KY":"Kentucky","LA":"Louisiana","ME":"Maine","MD":"Maryland","MA":"Massachusetts","MI":"Michigan",
 "MN":"Minnesota","MS":"Mississippi","MO":"Missouri","MT":"Montana","NE":"Nebraska","NV":"Nevada",
 "NH":"New Hampshire","NJ":"New Jersey","NM":"New Mexico","NY":"New York","NC":"North Carolina",
 "ND":"North Dakota","OH":"Ohio","OK":"Oklahoma","OR":"Oregon","PA":"Pennsylvania","RI":"Rhode Island",
 "SC":"South Carolina","SD":"South Dakota","TN":"Tennessee","TX":"Texas","UT":"Utah","VT":"Vermont",
 "VA":"Virginia","WA":"Washington","WV":"West Virginia","WI":"Wisconsin","WY":"Wyoming",
 "PR":"Puerto Rico","VI":"U.S. Virgin Islands","GU":"Guam","AS":"American Samoa"}
FULL_NAMES = {v.upper(): v for v in STATE_FULL.values()}

# ----- FP rule sets (anchored / whole-word) -----
GOVERNMENT = ["COUNTY OF","CITY OF","CITY & COUNTY OF","CITY AND COUNTY OF","STATE OF",
 "COMMONWEALTH OF","TOWN OF","VILLAGE OF","BOROUGH OF","PARISH OF","DEPARTMENT OF","DEPT OF",
 "BOARD OF","HOSPITAL DISTRICT","HEALTH DISTRICT","HEALTHCARE DISTRICT","HEALTH AUTHORITY",
 "HOSPITAL AUTHORITY","PUBLIC HEALTH","MUNICIPAL","HEALTH AND HOSPITALS"]
TRIBAL = ["TRIBE","TRIBAL","RANCHERIA","PUEBLO","NATION","BAND OF","NATIVE AMERICAN","INDIAN HEALTH",
 "INDIAN NATION","INDIAN TRIBE"]
PUBLIC_ACADEMIC = ["UNIVERSITY","REGENTS OF","BOARD OF REGENTS","STATE COLLEGE","COLLEGE OF MEDICINE"]
NATIONAL_NONPROFIT = ["VOLUNTEERS OF AMERICA","SALVATION ARMY","GOODWILL","CATHOLIC CHARITIES",
 "EASTERSEALS","EASTER SEALS","SHRINERS","BANCROFT","MELMARK","THE ARC","ARC OF","UNITED WAY",
 "YMCA","YWCA","RED CROSS","LUTHERAN SOCIAL SERVICES","GOOD SAMARITAN SOCIETY","EVANGELICAL LUTHERAN",
 "JEWISH FAMILY","ST JUDE","BOYS AND GIRLS","BOYS GIRLS CLUB"]
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
    for grp, label in [(LABS, "labs"), (FISCAL, "fiscal_intermediary"), (GOVERNMENT, "government"),
                       (TRIBAL, "tribal"), (PUBLIC_ACADEMIC, "public_academic"),
                       (NATIONAL_NONPROFIT, "national_nonprofit")]:
        for kw in grp:
            if _wb(kw, n):
                return (label, kw)
    if re.search(r"\bCOUNTY$", n):
        return ("government", "ENDS WITH COUNTY")
    return None

def to_full(tok):
    u = tok.strip().upper()
    return STATE_FULL.get(u, FULL_NAMES.get(u, tok.strip()))

def split_states(cell):
    parts = [p.strip() for p in re.split(r"[;,/]", str(cell or "")) if p.strip()]
    seen, out = set(), []
    for p in parts:
        f = to_full(p)
        if f.upper() not in seen:
            seen.add(f.upper()); out.append(f)
    return out

def strength(score, is_leie):
    if is_leie:
        return "high"          # documented exclusion -> not anomaly-defined -> high
    if score >= 0.9:
        return "high"
    if score >= 0.8:
        return "medium"
    return "low"               # 0.70-0.80


def main():
    con = duckdb.connect()
    # ---- STAGE 1: selection (0.70+ anomaly OR LEIE, all >=$10M) with ALL rollup features ----
    df = con.execute(f"""
        SELECT * FROM read_parquet('{ROLLUP}')
        WHERE company_net_paid >= {DOLLAR_MIN}
          AND ( max_anomaly_score_v3 >= {ANOMALY_SCORE_MIN} OR any_provider_on_leie )
        ORDER BY any_provider_on_leie DESC, max_anomaly_score_v3 DESC, company_net_paid DESC
    """).fetchdf()
    n_candidates = len(df)
    base_cols = list(df.columns)

    # derived columns added to BOTH files
    df["states_all"] = df["states"].map(lambda s: "; ".join(split_states(s)))
    df["lead_strength"] = [
        strength(float(s) if s is not None else 0.0, bool(l))
        for s, l in zip(df["max_anomaly_score_v3"], df["any_provider_on_leie"])
    ]
    out_cols = base_cols + ["states_all", "lead_strength"]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # FILE 1 — full selection, every feature column, BEFORE cleaning
    df[out_cols].to_csv(OUT_FULL, index=False)

    # ---- STAGE 2: secondary cleaning (institutional/fiscal/lab/individual FP + dedupe) ----
    from collections import Counter
    keep_mask, excl_rows, fp_counts = [], [], Counter()
    seen, n_dup = {}, 0
    for _, r in df.iterrows():
        name = str(r["company_name"]).strip()
        if not name:
            keep_mask.append(False); fp_counts["missing_name"] += 1
            excl_rows.append({"company_name": "", "states": str(r["states"]),
                              "reason": "missing_name", "matched": ""}); continue
        cat = fp_category(name)
        if cat:
            keep_mask.append(False); fp_counts[cat[0]] += 1
            excl_rows.append({"company_name": name, "states": str(r["states"]),
                              "reason": cat[0], "matched": cat[1]}); continue
        key = (normalize(name), (r["states_all"].split(";")[0].strip().upper()))
        if key in seen:
            keep_mask.append(False); n_dup += 1
            excl_rows.append({"company_name": name, "states": r["states_all"],
                              "reason": "duplicate", "matched": seen[key]}); continue
        seen[key] = name
        keep_mask.append(True)

    clean = df[keep_mask]
    # FILE 2 — same feature columns, AFTER secondary cleaning
    clean[out_cols].to_csv(OUT_CLEAN, index=False)
    with open(OUT_EXCL, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["company_name", "states", "reason", "matched"])
        w.writeheader(); w.writerows(excl_rows)

    assert len(clean) + len(excl_rows) == n_candidates, "reconcile fail"
    full = con.execute(f"SELECT count(*) FROM read_parquet('{ROLLUP}')").fetchone()[0]
    print("=" * 60)
    print(f"select_advertise_leads.py  (ANOMALY_SCORE_MIN={ANOMALY_SCORE_MIN}, ${DOLLAR_MIN:,} floor)")
    print("=" * 60)
    print(f"full scored population:                       {full:,}")
    print(f"FILE 1 selection (0.70+ anomaly OR LEIE, >=$10M, all features): {n_candidates:,}")
    print(f"  secondary cleaning removed:")
    for r, c in fp_counts.most_common():
        print(f"      {r:<20} {c}")
    print(f"      {'duplicate':<20} {n_dup}")
    print(f"FILE 2 cleaned:                               {len(clean):,}")
    print(f"lead_strength (cleaned): {dict(Counter(clean['lead_strength']))}")
    print(f"file 1 (full)    -> {OUT_FULL}")
    print(f"file 2 (cleaned) -> {OUT_CLEAN}")
    print(f"excluded         -> {OUT_EXCL}")


if __name__ == "__main__":
    main()
