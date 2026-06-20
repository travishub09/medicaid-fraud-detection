"""
cia.py — OIG Corporate Integrity Agreements (data-expansion sprint,
docs/platform/16 §6).

Source: HHS-OIG "Browse Corporate Integrity Agreements" + the data.gov CIA
documents dataset (free; ~335 active, scrapeable HTML/PDF). A CIA is the
compliance instrument OIG attaches to a civil FCA settlement, so the CIA list is
a near-perfect, structured proxy for "this org settled a material healthcare
fraud case." It serves TWO models:

  * Model A — an org-level PRIOR-ADJUDICATED-FRAUD flag (an active CIA / a place
    on the Heightened-Scrutiny list is a strong risk-multiplier and dossier
    corroborator), and
  * Model C — a settlement LABEL (CIA effective date ~= settlement date; the
    signed PDF usually names the scheme), feeding the case DB and sector priors.

  normalize_cia              CIA rows -> name_key (shared ``norm_org_name``),
                             cia_status ('active'|'closed'|'heightened'),
                             effective_date, state, source_url. The org-level
                             risk join keys on name_key against org nodes.
  cia_case_rows              the same rows shaped to the case-DB schema
                             (case_id, defendant_name, announced_date,
                             amount_usd?, intervened=True, scheme?) for Model C.

Surface as RISK CONTEXT, never an accusation (guardrail #5). docs/platform/16 §6.
"""

from __future__ import annotations

import pandas as pd

CIA_COLS = {
    "party": ["Party", "Company", "Entity", "party_name", "name"],
    "state": ["State", "state"],
    "effective_date": ["Effective Date", "effective_date", "Date"],
    "status": ["Status", "status"],
    "url": ["URL", "url", "Link", "Document"],
}


def normalize_cia(raw: pd.DataFrame) -> pd.DataFrame:
    """INPUT CONTRACT: scraped CIA rows (columns in CIA_COLS). OUTPUT: name_key
    (via src.entity_graph.resolve_entities.norm_org_name), cia_status,
    effective_date, state, source_url. docs/platform/16 §6."""
    raise NotImplementedError(
        "data-expansion sprint stub — implement per docs/platform/16 §6 "
        "(use the shared norm_org_name; do NOT write a second normalizer)")


def cia_case_rows(normalized_cia: pd.DataFrame) -> pd.DataFrame:
    """INPUT: normalize_cia output. OUTPUT: rows in the case-DB schema
    (src.enforcement.case_db.CASE_COLUMNS) with intervened=True and the CIA
    effective date as announced_date. docs/platform/16 §6."""
    raise NotImplementedError(
        "data-expansion sprint stub — implement per docs/platform/16 §6")
