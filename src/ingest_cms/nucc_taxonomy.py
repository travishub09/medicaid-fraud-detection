"""
nucc_taxonomy.py — NUCC Health Care Provider Taxonomy + CMS specialty crosswalk
(data-expansion sprint, docs/platform/16 §3).

Sources: NUCC "Provider Taxonomy" code set (nucc.org, free, twice/yr) and the
CMS "Medicare Provider and Supplier Taxonomy Crosswalk" (data.cms.gov). Together
they give an authoritative taxonomy hierarchy (~870 codes, 3 levels) and a map
between Medicare specialty codes (used in the claims files) and NUCC taxonomy
codes (used in NPPES). This FIXES PEER GROUPING: every one-sided robust-z signal
(hard rule #8) is only as good as its peer cohort, and a consistent
specialty<->taxonomy key cuts false positives from mis-grouped specialties. It
also makes ``model_a.sector_priors`` authoritative instead of prefix-coded.

  load_taxonomy_hierarchy    NUCC csv → taxonomy_code, grouping, classification,
                             specialization (the canonical 3-level tree).
  load_specialty_crosswalk   CMS csv → medicare_specialty_code <-> taxonomy_code.
  canonical_peer_group       given a provider dim (NPI, taxonomy and/or medicare
                             specialty), return one stable peer_group_key per
                             NPI — the grouping key the peer engine should use.

Pure reference data, no provider PII. Dormant until the two files land; see
docs/platform/16 §3 (this one is the highest-leverage, lowest-effort add).
"""

from __future__ import annotations

import pandas as pd

NUCC_COLS = {
    "taxonomy_code": ["Code", "code", "taxonomy_code"],
    "grouping": ["Grouping", "grouping"],
    "classification": ["Classification", "classification"],
    "specialization": ["Specialization", "specialization"],
}
CROSSWALK_COLS = {
    "medicare_specialty_code": ["MEDICARE SPECIALTY CODE",
                                "Medicare Specialty Code", "specialty_code"],
    "taxonomy_code": ["PROVIDER TAXONOMY CODE", "Provider Taxonomy Code",
                      "taxonomy_code"],
}


def load_taxonomy_hierarchy(raw: pd.DataFrame) -> pd.DataFrame:
    """INPUT: NUCC taxonomy csv. OUTPUT: taxonomy_code, grouping, classification,
    specialization (strings). docs/platform/16 §3."""
    raise NotImplementedError(
        "data-expansion sprint stub — implement per docs/platform/16 §3")


def load_specialty_crosswalk(raw: pd.DataFrame) -> pd.DataFrame:
    """INPUT: CMS specialty<->taxonomy crosswalk csv. OUTPUT: one row per
    (medicare_specialty_code, taxonomy_code) pair. docs/platform/16 §3."""
    raise NotImplementedError(
        "data-expansion sprint stub — implement per docs/platform/16 §3")


def canonical_peer_group(provider_dim: pd.DataFrame,
                         hierarchy: pd.DataFrame,
                         crosswalk: pd.DataFrame | None = None) -> pd.DataFrame:
    """INPUT: provider_dim (npi + taxonomy and/or medicare_specialty_code) plus
    the loaded hierarchy/crosswalk. OUTPUT: npi, peer_group_key (the canonical
    grouping the peer engine and sector_priors should consume). docs/platform/16 §3."""
    raise NotImplementedError(
        "data-expansion sprint stub — implement per docs/platform/16 §3")
