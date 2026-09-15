"""
cia.py — OIG Corporate Integrity Agreements (data-expansion sprint,
docs/platform/16 §6).

Source: HHS-OIG "Browse Corporate Integrity Agreements" + the data.gov CIA
documents dataset (free; ~335 active, scrapeable HTML/PDF). A CIA is the
compliance instrument OIG attaches to a civil FCA settlement, so the CIA list is
a near-perfect, structured proxy for "this org settled a material healthcare
fraud case." It serves THREE purposes:

  * Model A — an org-level PRIOR-ADJUDICATED-FRAUD flag (an active CIA / a place
    on the Heightened-Scrutiny list is a strong risk-multiplier and dossier
    corroborator),
  * Model C — a settlement LABEL (CIA effective date ~= settlement date; the
    signed PDF usually names the scheme), feeding the case DB and sector priors,
  * the LABEL AUDIT — an independent, official list of "orgs that settled an
    FCA matter" against which the DOJ case DB's RECALL can be measured
    (``model_a.label_audit --cia``).

  normalize_cia              CIA rows -> name_key (shared ``norm_org_name``),
                             cia_status ('active'|'closed'|'heightened'),
                             effective_date, state, source_url. The org-level
                             risk join keys on name_key against org nodes.
  cia_case_rows              the same rows shaped to the case-DB schema
                             (case_id, defendant_name, announced_date,
                             amount_usd?, intervened=True, scheme?) for Model C.

Input: the OIG page saved as CSV (party / state / effective date / status /
link columns under any of the header spellings in CIA_COLS). The operator
saves it to ``<data-root>/preclean/cia/cia_list.csv`` (procurement steps in
docs/platform/12 and the ``oig_cia`` reminder).

Surface as RISK CONTEXT, never an accusation (guardrail #5). docs/platform/16 §6.
"""

from __future__ import annotations

import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name

CIA_COLS = {
    "party": ["Party", "Company", "Entity", "party_name", "name", "Provider",
              "Entity Name", "Party Name"],
    "state": ["State", "state"],
    "effective_date": ["Effective Date", "effective_date", "Date", "Effective"],
    "status": ["Status", "status", "Agreement Status"],
    "url": ["URL", "url", "Link", "Document", "source_url", "Agreement"],
}

CIA_OUT_COLS = ["entity_name", "name_key", "cia_status", "effective_date",
                "state", "source_url"]


def _norm_header(c: str) -> str:
    return str(c).strip().strip('"').lower().replace(" ", "").replace("_", "")


def _pick(raw: pd.DataFrame, candidates: list[str]) -> pd.Series | None:
    by_norm = {_norm_header(c): c for c in raw.columns}
    for cand in candidates:
        col = by_norm.get(_norm_header(cand))
        if col is not None:
            return raw[col]
    return None


def _status(s: str) -> str:
    t = str(s or "").strip().lower()
    if "heighten" in t:
        return "heightened"
    if any(k in t for k in ("closed", "expired", "terminated", "complete",
                            "inactive", "ended", "released")):
        return "closed"
    return "active"


def normalize_cia(raw: pd.DataFrame) -> pd.DataFrame:
    """INPUT CONTRACT: scraped CIA rows (columns in CIA_COLS). OUTPUT: entity_name,
    name_key (via src.entity_graph.resolve_entities.norm_org_name), cia_status,
    effective_date, state, source_url. Rows whose party name normalizes to an
    empty key are dropped (nothing to join on). docs/platform/16 §6."""
    if raw is None or not len(raw):
        return pd.DataFrame(columns=CIA_OUT_COLS)
    party = _pick(raw, CIA_COLS["party"])
    if party is None:
        raise ValueError(f"CIA file has no party column; saw {list(raw.columns)[:12]}")
    party = party.fillna("").astype(str).str.strip()
    n = len(raw)

    def _col(key: str) -> pd.Series:
        s = _pick(raw, CIA_COLS[key])
        return (s.fillna("").astype(str).str.strip() if s is not None
                else pd.Series([""] * n, index=raw.index))

    out = pd.DataFrame({
        "entity_name": party,
        "name_key": party.map(norm_org_name),
        "cia_status": _col("status").map(_status),
        "effective_date": pd.to_datetime(_col("effective_date"), errors="coerce"),
        "state": _col("state").str.upper().str[:2],
        "source_url": _col("url"),
    }, index=raw.index)
    out = out[out["name_key"].fillna("") != ""]
    return out.reset_index(drop=True)[CIA_OUT_COLS]


def cia_case_rows(normalized_cia: pd.DataFrame) -> pd.DataFrame:
    """INPUT: normalize_cia output. OUTPUT: rows in the case-DB schema
    (src.enforcement.case_db.CASE_COLUMNS) with intervened=True and the CIA
    effective date as announced_date. Amount and scheme are unknown from the
    list alone (the signed PDF carries them) and ship as <NA>/''.
    docs/platform/16 §6."""
    from .case_db import CASE_COLUMNS
    if normalized_cia is None or not len(normalized_cia):
        return pd.DataFrame(columns=CASE_COLUMNS)
    c = normalized_cia
    date = pd.to_datetime(c["effective_date"], errors="coerce")
    rows = pd.DataFrame({
        "case_id": "cia:" + c["name_key"].astype(str) + ":"
                   + date.dt.strftime("%Y-%m-%d").fillna("undated"),
        "announced_date": date.dt.strftime("%Y-%m-%d"),
        "defendant_name": c["entity_name"].astype(str),
        "defendant_name_key": c["name_key"].astype(str),
        "sector": "",
        "scheme": "",
        "amount_usd": pd.Series([pd.NA] * len(c), index=c.index),
        "qui_tam": pd.Series([pd.NA] * len(c), index=c.index),
        "intervened": 1,
        "jurisdiction": "",
        "source_url": c["source_url"].astype(str),
        "summary": ("OIG Corporate Integrity Agreement ("
                    + c["cia_status"].astype(str) + ")"),
    }, index=c.index)
    return rows.drop_duplicates("case_id").reset_index(drop=True)[CASE_COLUMNS]
