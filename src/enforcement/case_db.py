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
]

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


def _extract_amount(text: str) -> float | None:
    hits = _AMOUNT.findall(text)
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


def merge_case_files(primary: pd.DataFrame, secondary: pd.DataFrame) -> pd.DataFrame:
    """Append ``secondary`` rows whose announcement is not already in ``primary``
    (same case_id, or same source URL once the #fragment is stripped). Primary
    rows are never changed."""
    p = build_case_db(primary)
    s = build_case_db(secondary)
    have_ids = set(p["case_id"].astype(str))
    have_urls = {_url_base(u) for u in p["case_id"].astype(str)} | {
        _url_base(u) for u in p["source_url"].fillna("").astype(str) if u}
    keep = ~s["case_id"].astype(str).isin(have_ids) & ~s["case_id"].astype(str).map(_url_base).isin(have_urls) \
        & ~s["source_url"].fillna("").astype(str).map(_url_base).isin(have_urls - {""})
    return build_case_db(pd.concat([p, s[keep]], ignore_index=True))


def reclassify(df: pd.DataFrame) -> pd.DataFrame:
    """Re-stamp case_type from the stored text with the CURRENT rules (docket rows keep
    theirs). Run after any change to classify_case_type so old harvests agree."""
    out = df.copy()
    ct = out["case_type"].fillna("").astype(str) if "case_type" in out.columns else pd.Series("", index=out.index)
    text = out["summary"].fillna("").astype(str) + " " + out.get("scheme", pd.Series("", index=out.index)).fillna("").astype(str)
    fresh = text.map(classify_case_type)
    out["case_type"] = ct.where(ct.str.startswith("docket"), fresh)
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
        print(f"  + {path}: {len(sec):,} rows -> {len(primary) - n0:,} new so far")
    if a.reclassify:                       # AFTER the adds, so appended rows get the current rules too
        before = primary["case_type"].fillna("").value_counts().to_dict() if "case_type" in primary.columns else {}
        primary = reclassify(primary)
        after = primary["case_type"].fillna("").value_counts().to_dict()
        print(f"  reclassified: {before} -> {after}")
    primary.to_csv(a.into, index=False)
    print(f"case db: {n0:,} -> {len(primary):,} cases in {a.into}")


if __name__ == "__main__":
    main()
