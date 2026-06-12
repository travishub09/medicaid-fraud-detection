"""
audiences.py — audience assembly (SCAFFOLD).

Per ``docs/platform/05-model-b.md`` §1.4. Combine B1×B2×reachability into a
per-person priority, prioritized across orgs by the org's expected recoverable
value (Model A), then ROLL UP into audiences keyed by ``role × org × channel``,
each with a recommended message angle mapped to the scheme and persona.

THE GUARDRAIL THAT SHAPES THE WHOLE DESIGN: the output is a set of marketing
audiences for compliant targeting, NOT a solicitation list of named individuals.
``person_priority`` stays internal; only aggregated, de-identified audience
definitions leave this module. See the guardrails in §1.6.
"""

from __future__ import annotations

import pandas as pd


def person_priority(knowledge: pd.Series, propensity: pd.Series,
                    reachability: pd.Series, org_erv: pd.Series) -> pd.Series:
    """knowledge × propensity × reachability, scaled by the org's ERV (internal only)."""
    raise NotImplementedError("person priority composite — see 05-model-b.md §1.4")


def build_audiences(people_scored: pd.DataFrame) -> pd.DataFrame:
    """Roll individuals up into role×org×channel audiences with a message angle.

    Output is aggregated audience definitions ONLY — never named-individual rows.
    """
    raise NotImplementedError("audience roll-up (de-identified) — see 05-model-b.md §1.4/§1.6")
