"""
fetch.py — DOJ / OIG announcement fetcher (STUB).

Pulls listing pages from justice.gov (civil/false-claims-act press releases) and
oig.hhs.gov (enforcement actions, CIAs), feeding ``case_db.parse_press_release``.

Stub on purpose: it needs (a) network access in the runtime where it runs, and
(b) a quick scraping-terms review (justice.gov content is public-domain; still
rate-limit and identify the client). The parse/build/derive layers are real and
tested — backfill can proceed today from manually collected rows
(``preclean/enforcement/doj_cases.csv``) while this fetcher is pending.
"""

from __future__ import annotations

import pandas as pd


def fetch_doj_press_releases(since: str, until: str | None = None) -> pd.DataFrame:
    """Fetch FCA press-release texts from justice.gov between dates."""
    raise NotImplementedError(
        "needs network + scraping review; backfill via the manual CSV path "
        "(docs/platform/09-data-procurement.md #6)")


def fetch_oig_actions(since: str) -> pd.DataFrame:
    """Fetch OIG enforcement actions / CIA list."""
    raise NotImplementedError("same constraints as fetch_doj_press_releases")
