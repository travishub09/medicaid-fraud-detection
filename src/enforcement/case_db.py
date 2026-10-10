"""
case_db.py — structure DOJ/OIG announcements into the canonical case table.

One row per enforcement case/action. Two entry paths:
  * ``parse_press_release(text)`` — regex/keyword extraction from announcement
    text (amount, qui tam, intervention status, sector, scheme). Conservative by
    design: anything not confidently extracted is left empty for human review,
    never guessed.
  * manually curated rows (a CSV with these columns) — the backfill path while
    the fetcher (fetch.py) is pending.

``build_case_db`` validates either source into CASE_COLUMNS, adds the
``defendant_name_key`` (the same ``norm_org_name`` the entity resolver uses, so
cases join the graph), and de-duplicates.
"""

from __future__ import annotations

import re

import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name

CASE_COLUMNS = [
    "case_id",            # stable id (source url or hand-assigned)
    "announced_date",     # date of the release/settlement
    "defendant_name",
    "defendant_name_key",  # norm_org_name(defendant) — the graph join key
    "sector",             # home_health/hospice/dme/lab/behavioral/snf/pharmacy/...
    "scheme",             # kickback/upcoding/medical_necessity/billing_fraud/...
    "amount_usd",
    "qui_tam",            # 1/0/<NA>
    "intervened",         # 1/0/<NA>  (NA = not stated — most releases)
    "jurisdiction",       # district, e.g. "M.D. Fla."
    "source_url",
    "summary",
    "case_type",          # civil_fca / criminal_hcf / docket_fca / docket_qui_tam / "" (unclassified)
    "duplicate_of",       # case_id of the EARLIER announcement of the same settlement ("" = primary)
]

# One settlement, two releases: DOJ national + the local USAO, or a sentencing
# re-announcement. Same dollar amount, same announcement year, and a defendant
# token that is NOT one of these generic healthcare / corporate words. The
# LABEL_AUDIT of 2026-10-09 counted 2,929 such pairs, $593,532M double-counted
# in every dollar total. The later row is MARKED (duplicate_of = the earlier
# case_id), never dropped: resolver rows keyed to either id keep working, and
# the dollar aggregations skip the marked rows via drop_duplicate_settlements.
# IDENTITY stoplist (narrower than clean_data.GENERIC_NAME_TOKENS, which also drops
# SUNRISE / PEDIATRIC / MERCY so two local clinics never fuse into one canonical
# org). Here and in the resolver's fuzzy tier the question is "is this the same
# named party", and those words identify it; only pure filler and legal suffixes
# drop. Shared with defendant_resolver.FUZZY_STOP_TOKENS.
NAME_STOP_TOKENS = frozenset({
    "home", "care", "homecare", "health", "healthcare", "medical", "medicine", "services", "service",
    "clinic", "clinics", "center", "centers", "centre", "hospital", "hospitals", "group", "associates",
    "partners", "management", "holdings", "holding", "enterprise", "enterprises", "solutions",
    "professional", "professionals", "provider", "providers", "systems", "system", "network", "practice",
    "practices", "association", "the", "and", "for", "inc", "llc", "llp", "ltd", "corp", "corporation",
    "company", "companies", "co", "dba", "opco", "pllc", "plc", "america", "american", "national",
    "regional", "usa", "international", "global", "united", "states", "general", "et", "al",
    "jr", "sr", "iii", "mr", "mrs", "ms", "dr", "md",
})
GENERIC_NAME_TOKENS = NAME_STOP_TOKENS          # back-compat name
_NAME_TOKEN = re.compile(r"[a-z]{3,}")


def _name_tokens(key: str) -> set:
    return {t for t in _NAME_TOKEN.findall(str(key or "").lower()) if t not in GENERIC_NAME_TOKENS}


def mark_duplicate_settlements(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Set duplicate_of on the later of two rows that share amount, year and a
    distinctive defendant token. Chains collapse to the earliest announcement.
    Returns (df, stats) with pairs / dollars marked."""
    df = df.copy()
    if "duplicate_of" not in df.columns:
        df["duplicate_of"] = ""
    df["duplicate_of"] = df["duplicate_of"].fillna("").astype(str)
    amt = pd.to_numeric(df["amount_usd"], errors="coerce")
    ad = pd.to_datetime(df["announced_date"], errors="coerce")
    key = df["defendant_name_key"].fillna("").astype(str)
    key = key.where(key != "", df["defendant_name"].fillna("").astype(str).map(norm_org_name))
    cand = pd.DataFrame({"amt": amt.round(0), "yr": ad.dt.year, "ad": ad, "key": key}, index=df.index)
    cand = cand[(cand["amt"] > 0) & cand["yr"].notna()]
    marked, dollars = 0, 0.0
    for (_, _), g in cand.groupby(["amt", "yr"]):
        if len(g) < 2:
            continue
        idx = list(g.sort_values("ad", kind="mergesort").index)     # earliest first
        toks = {i: _name_tokens(g.at[i, "key"]) for i in idx}
        primary_of: dict = {}
        for a_i in range(len(idx)):
            i = idx[a_i]
            if i in primary_of:
                continue
            for j in idx[a_i + 1:]:
                if j in primary_of:
                    continue
                if toks[i] & toks[j]:
                    primary_of[j] = primary_of.get(i, i)
        for j, i in primary_of.items():
            if df.at[j, "duplicate_of"] == "":
                df.at[j, "duplicate_of"] = str(df.at[i, "case_id"])
                marked += 1
                dollars += float(cand.at[j, "amt"])
    return df, {"pairs_marked": marked, "dollars_marked": dollars,
                "total_marked": int((df["duplicate_of"] != "").sum())}


def drop_duplicate_settlements(df: pd.DataFrame) -> pd.DataFrame:
    """The primary announcements only (for every DOLLAR aggregation: dollar
    recall, unresolved-settlement totals, sector priors). Label building keeps
    all rows: a duplicate resolves to the same providers and adds no positive."""
    if "duplicate_of" not in df.columns:
        df, _ = mark_duplicate_settlements(df)
    d = df["duplicate_of"].fillna("").astype(str)
    return df[d == ""].reset_index(drop=True)

_CIVIL_RE = re.compile(r"false\s+claims\s+act|qui\s+tam|civil\s+settlement|agree[sd]?\s+to\s+pay", re.I)
_CRIMINAL_RE = re.compile(r"sentenced|pleaded\s+guilty|pled\s+guilty|indict|convicted|jury\s+found|"
                          r"health\s*care\s+fraud\s+(?:conspiracy|scheme|charges)|18\s*u\.?s\.?c\.?\s*(?:§\s*)?1347|"
                          r"anti-?kickback\s+statute|conspiracy\s+to\s+commit", re.I)


_CMP_RE = re.compile(r"civil\s+monetary\s+penalt(?:y|ies)\s+law|\bCMP\b|self-?disclos", re.I)
_NOT_FRAUD_RE = re.compile(r"patient\s+dumping|EMTALA|emergency\s+medical\s+treatment\s+and\s+labor|"
                           r"employ(?:ing|ed)\s+(?:an?\s+|two\s+|\d+\s+)?(?:individuals?|persons?)\s+(?:who\s+(?:was|were)\s+)?excluded|"
                           r"excluded\s+(?:individual|person|employee)", re.I)


def classify_case_type(text: str) -> str:
    """civil_fca (an FCA / qui tam resolution), criminal_hcf (a health-care-fraud
    prosecution), oig_cmp (an OIG civil-monetary-penalty settlement: fraud-shaped
    conduct resolved administratively, never a strict FCA outcome), not_fraud
    (EMTALA / employing an excluded person: an OIG action that is not billing
    fraud), or '' when the text says none. The type is kept so evaluations and the
    strict label policy can stratify."""
    t = str(text or "")
    if _NOT_FRAUD_RE.search(t) and not re.search(r"false\s+claims\s+act", t, re.I):
        return "not_fraud"
    if _CMP_RE.search(t) and not re.search(r"false\s+claims\s+act", t, re.I) and not _CRIMINAL_RE.search(t):
        return "oig_cmp"
    civ, crim = bool(_CIVIL_RE.search(t)), bool(_CRIMINAL_RE.search(t))
    if civ and not crim:
        return "civil_fca"
    if crim and not civ:
        return "criminal_hcf"
    if civ and crim:
        return "civil_fca" if re.search(r"false\s+claims\s+act", t, re.I) else "criminal_hcf"
    return ""

# Units: spelled out OR the abbreviations DOJ/press copy actually uses ("$3M",
# "$1.2B") — without [MBK], "$3M" parsed as three dollars (found by probing).
_AMOUNT = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(billion|million|thousand|[MBK])?\b",
    re.IGNORECASE)

_SECTOR_KEYWORDS: dict[str, list[str]] = {
    "hospice": ["hospice"],
    "home_health": ["home health", "home-health", "home healthcare"],
    "personal_care": ["personal care", "home care aide", "attendant care"],
    "dme": ["durable medical equipment", "dme", "orthotic", "brace", "braces"],
    "lab": ["laboratory", "lab test", "genetic testing", "toxicology"],
    "behavioral": ["behavioral health", "substance abuse", "substance use",
                   "addiction treatment", "sober home", "mental health"],
    "snf": ["skilled nursing", "nursing home", "nursing facility"],
    "pharmacy": ["pharmacy", "pharmacies", "pharmaceutical", "compounding", "340b"],
    "telehealth": ["telehealth", "telemedicine"],
    "managed_care": ["medicare advantage", "risk adjustment", "risk-adjustment"],
}

_SCHEME_KEYWORDS: dict[str, list[str]] = {
    "kickback": ["kickback", "anti-kickback", "stark law", "referral fee", "bribe"],
    "upcoding": ["upcod", "risk adjustment", "unsupported diagnos", "higher-paying code"],
    "medical_necessity": ["medically unnecessary", "not medically necessary",
                          "medical necessity"],
    "phantom_billing": ["services not rendered", "never provided",
                        "services that were not provided", "phantom"],
    "eligibility": ["not eligible", "ineligible", "eligibility"],
    "worthless_services": ["worthless services", "grossly substandard"],
    "pill_mill": ["opioid", "controlled substance", "oxycodone", "pill mill",
                  "overprescri", "unlawful prescri"],
}


# DOJ boilerplate that names a NATIONAL cumulative figure, never the case's own:
# "... has charged more than 4,200 defendants who collectively have billed the
# Medicare program for approximately $19 billion", "health care fraud costs ...
# $60 billion each year". Sentences that match are ignored by the amount parser.
_BOILERPLATE = re.compile(r"since (?:its|the) inception|strike force|collectively (?:have )?billed|"
                          r"nationwide|across the country|each year|annually|per year|a year\b|"
                          r"defendants who|the program has|task force", re.I)


def _case_sentences(text: str) -> str:
    return " ".join(sent for sent in re.split(r"(?<=[.!?])\s+", str(text or "")) if not _BOILERPLATE.search(sent))


def _extract_amount(text: str) -> float | None:
    hits = _AMOUNT.findall(_case_sentences(text))
    if not hits:
        return None
    best = 0.0
    for num, unit in hits:
        v = float(num.replace(",", ""))
        v *= {"billion": 1e9, "b": 1e9, "million": 1e6, "m": 1e6,
              "thousand": 1e3, "k": 1e3}.get((unit or "").lower(), 1.0)
        best = max(best, v)
    return best or None


def _classify(text: str, keyword_map: dict[str, list[str]]) -> str:
    low = text.lower()
    scores = {label: sum(low.count(k) for k in kws)
              for label, kws in keyword_map.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else ""


def _classify_all(text: str, keyword_map: dict[str, list[str]]) -> str:
    """EVERY matching label, ';'-joined, strongest first.

    A settlement usually names several conduct types at once ("kickbacks for
    medically unnecessary braces"); single-best classification silently drops
    all but one, which starves the per-scheme case validation. Downstream
    (case_labels -> case_validation) already splits scheme tags on ';'."""
    low = text.lower()
    scores = {label: sum(low.count(k) for k in kws)
              for label, kws in keyword_map.items()}
    hits = sorted(((n, label) for label, n in scores.items() if n > 0),
                  key=lambda t: (-t[0], t[1]))
    return ";".join(label for _, label in hits)


def parse_press_release(text: str, source_url: str = "",
                        announced_date: str = "",
                        defendant_name: str = "") -> dict:
    """One announcement text → one case dict (empty fields = needs human review)."""
    low = text.lower()
    qui_tam = 1 if ("qui tam" in low or "whistleblower" in low) else pd.NA
    if "declined to intervene" in low or "government declined" in low:
        intervened = 0
    elif "intervened" in low or "joined the lawsuit" in low:
        intervened = 1
    else:
        intervened = pd.NA
    juris = ""
    m = re.search(r"(Northern|Southern|Eastern|Western|Middle)\s+District\s+of\s+([A-Z][a-zA-Z ]+)", text)
    if m:
        juris = f"{m.group(1)} District of {m.group(2).strip()}"

    return {
        "case_id": source_url or f"manual:{hash(text) & 0xFFFFFFFF:x}",
        "announced_date": announced_date,
        "defendant_name": defendant_name,
        "defendant_name_key": norm_org_name(defendant_name),
        "sector": _classify(text, _SECTOR_KEYWORDS),      # single: priors groupby
        "scheme": _classify_all(text, _SCHEME_KEYWORDS),  # multi: split on ';'
        "amount_usd": _extract_amount(text),
        "qui_tam": qui_tam,
        "intervened": intervened,
        "jurisdiction": juris,
        "source_url": source_url,
        "summary": text[:1500],
        "case_type": classify_case_type(text),
    }


def build_case_db(rows: list[dict] | pd.DataFrame) -> pd.DataFrame:
    """Validate rows into CASE_COLUMNS; add name keys; de-duplicate on case_id."""
    df = pd.DataFrame(rows) if not isinstance(rows, pd.DataFrame) else rows.copy()
    for c in CASE_COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA
    df["defendant_name"] = df["defendant_name"].fillna("").astype(str)
    needs_key = df["defendant_name_key"].isna() | (df["defendant_name_key"].astype(str) == "")
    df.loc[needs_key, "defendant_name_key"] = df.loc[needs_key, "defendant_name"].map(norm_org_name)
    df["amount_usd"] = pd.to_numeric(df["amount_usd"], errors="coerce")
    df = df[CASE_COLUMNS].drop_duplicates(subset=["case_id"]).reset_index(drop=True)
    assert df["case_id"].notna().all(), "every case needs a case_id"
    return df


def _url_base(case_id: str) -> str:
    """'https://.../pr/x#DEFENDANT_A' and 'https://.../pr/x' are the same announcement."""
    return re.sub(r"#.*$", "", str(case_id or "")).rstrip("/").lower()


# a stored summary that is the page chrome, not the release (the first OIG harvest read the
# site navigation as the body); a re-harvest with the article text replaces it
_NAV_GARBAGE = re.compile(r"skip to main content|official website of the united states government|here.s how you know", re.I)
_REFRESH_COLS = ("announced_date", "defendant_name", "jurisdiction", "amount_usd", "scheme", "sector")
_last_refresh: dict = {"filled": 0, "summaries": 0}


def _blank(v) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):                      # float NaN, pd.NA, NaT
            return True
    except (TypeError, ValueError):
        pass
    return str(v).strip().lower() in ("", "nan", "none", "<na>")


def _str(v) -> str:
    return "" if _blank(v) else str(v)


def merge_case_files(primary: pd.DataFrame, secondary: pd.DataFrame, refresh: bool = True) -> pd.DataFrame:
    """Append ``secondary`` rows whose announcement is not already in ``primary``
    (same case_id, or same source URL once the #fragment is stripped). A primary
    row is never dropped or re-keyed; with ``refresh`` (the default) an incoming
    row for an announcement already stored FILLS its blanks (date, defendant,
    jurisdiction, amount, scheme, sector) and replaces a summary that is page
    chrome, so a better re-harvest of the same URL improves the row instead of
    being thrown away. The counts land in ``_last_refresh`` for the CLI line."""
    p = build_case_db(primary)
    s = build_case_db(secondary)
    have_ids = set(p["case_id"].astype(str))
    have_urls = {_url_base(u) for u in p["case_id"].astype(str)} | {
        _url_base(u) for u in p["source_url"].fillna("").astype(str) if u}
    keep = ~s["case_id"].astype(str).isin(have_ids) & ~s["case_id"].astype(str).map(_url_base).isin(have_urls) \
        & ~s["source_url"].fillna("").astype(str).map(_url_base).isin(have_urls - {""})
    _last_refresh.update(filled=0, summaries=0)
    if refresh and len(s):
        incoming: dict = {}
        for r in s.itertuples(index=False):
            d = r._asdict()
            for k in (_url_base(_str(d.get("case_id"))), _url_base(_str(d.get("source_url")))):
                if k:
                    incoming.setdefault(k, d)
        for i in p.index:
            new = incoming.get(_url_base(_str(p.at[i, "case_id"]))) or incoming.get(_url_base(_str(p.at[i, "source_url"])))
            if new is None:
                continue
            for c in _REFRESH_COLS:
                if c in p.columns and _blank(p.at[i, c]) and not _blank(new.get(c)):
                    p.at[i, c] = new[c]
                    _last_refresh["filled"] += 1
            old_sum, new_sum = _str(p.at[i, "summary"]), _str(new.get("summary"))
            if new_sum.strip() and not _NAV_GARBAGE.search(new_sum) and (_NAV_GARBAGE.search(old_sum) or len(old_sum.strip()) < 40):
                p.at[i, "summary"] = new_sum
                _last_refresh["summaries"] += 1
    merged = build_case_db(pd.concat([p, s[keep]], ignore_index=True))
    merged, stats = mark_duplicate_settlements(merged)
    _last_refresh["duplicates"] = stats
    return merged


def reclassify(df: pd.DataFrame) -> pd.DataFrame:
    """Re-stamp case_type from the stored text with the CURRENT rules (docket rows keep
    theirs). Run after any change to classify_case_type so old harvests agree."""
    out = df.copy()
    ct = out["case_type"].fillna("").astype(str) if "case_type" in out.columns else pd.Series("", index=out.index)
    text = out["summary"].fillna("").astype(str) + " " + out.get("scheme", pd.Series("", index=out.index)).fillna("").astype(str)
    fresh = text.map(classify_case_type)
    out["case_type"] = ct.where(ct.str.startswith("docket"), fresh)
    # amounts: recomputed from the stored text with the boilerplate filter; a row whose
    # text yields nothing keeps what it had
    if "summary" in out.columns:
        summ = out["summary"].fillna("").astype(str)
        fresh_amt = summ.map(_extract_amount)
        old_amt = pd.to_numeric(out.get("amount_usd"), errors="coerce") if "amount_usd" in out.columns else pd.Series(pd.NA, index=out.index)
        # an OLD amount at/above $1B whose stored text never says 'billion' is the national
        # boilerplate figure that survived a short summary; anything above $20B is not a case
        says_billion = summ.str.contains(r"\bbillion\b", case=False, regex=True)
        old_ok = old_amt.where(~((old_amt >= 1e9) & ~says_billion))
        amt = fresh_amt.where(fresh_amt.notna(), old_ok)
        out["amount_usd"] = amt.where(~(amt > 2e10))
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="merge case files: rows of --into win; --add rows join when their announcement is new")
    ap.add_argument("--into", required=True, help="the case CSV to extend (rewritten in place)")
    ap.add_argument("--add", nargs="*", default=[], help="case CSVs to fold in")
    ap.add_argument("--reclassify", action="store_true", help="re-stamp case_type on every row with the current rules")
    a = ap.parse_args()
    primary = pd.read_csv(a.into, dtype=str)
    n0 = len(primary)
    for path in a.add:
        sec = pd.read_csv(path, dtype=str)
        primary = merge_case_files(primary, sec)
        print(f"  + {path}: {len(sec):,} rows -> {len(primary) - n0:,} new so far; refreshed existing rows: "
              f"{_last_refresh['filled']:,} blank fields filled, {_last_refresh['summaries']:,} page-chrome summaries replaced")
    if not a.add:                          # a bare --into pass still marks the duplicate settlements
        primary, stats = mark_duplicate_settlements(build_case_db(primary))
        _last_refresh["duplicates"] = stats
    d = _last_refresh.get("duplicates") or {}
    print(f"  duplicate settlements marked (same amount + year + a distinctive defendant token): "
          f"{d.get('total_marked', 0):,} rows carry duplicate_of; dollar totals skip them")
    if a.reclassify:                       # AFTER the adds, so appended rows get the current rules too
        before = primary["case_type"].fillna("").value_counts().to_dict() if "case_type" in primary.columns else {}
        primary = reclassify(primary)
        after = primary["case_type"].fillna("").value_counts().to_dict()
        print(f"  reclassified: {before} -> {after}")
    primary.to_csv(a.into, index=False)
    print(f"case db: {n0:,} -> {len(primary):,} cases in {a.into}")


if __name__ == "__main__":
    main()
