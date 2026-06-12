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
]

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
        "sector": _classify(text, _SECTOR_KEYWORDS),
        "scheme": _classify(text, _SCHEME_KEYWORDS),
        "amount_usd": _extract_amount(text),
        "qui_tam": qui_tam,
        "intervened": intervened,
        "jurisdiction": juris,
        "source_url": source_url,
        "summary": text[:500],
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
