"""
audiences.py — the audience assembly, with the guardrail enforced in code.

Per ``docs/platform/05-model-b.md`` §1.4/§1.6: combine B1 × B2 × reachability
into an internal per-person priority, then ROLL UP into audiences keyed by
role × org × channel, each with a message angle mapped to the scheme.

THE GUARDRAIL, ENFORCED: ``build_audiences`` strips every identifier column and
asserts none survive — the exported artifact is aggregated audience definitions
only. ``person_priority`` exists only inside the pipeline run and is never
written to an export. Named-individual outreach is gated by counsel; this module
makes the non-compliant path structurally unavailable.
"""

from __future__ import annotations

import pandas as pd

# columns that must NEVER appear in an audience export
FORBIDDEN_EXPORT_COLUMNS = {
    "person_id", "name", "first_name", "last_name", "full_name", "email",
    "phone", "linkedin_url", "address", "person_priority",
}

# scheme → the education-first message angle (no accusations; concerns framing)
MESSAGE_ANGLES: dict[str, str] = {
    "upcoding_ma_risk_adjustment":
        "What coders should know about unsupported diagnosis capture and RADV risk",
    "kickbacks_aks_stark":
        "When sales incentives and referral arrangements cross the legal line",
    "medical_necessity":
        "When utilization pressure becomes a False Claims Act problem",
    "home_health_hospice_eligibility":
        "When admissions pressure creates False Claims Act risk",
    "lab_genetic_testing":
        "When test-ordering arrangements become Medicare fraud",
    "pharmacy_340b":
        "340B compliance questions your employer may not want asked",
    "evv_personal_care":
        "When visit documentation doesn't match what happened",
    "cost_report_fraud":
        "Cost-report questions reimbursement analysts are right to raise",
}
_DEFAULT_ANGLE = "Understanding your rights when billing concerns go unheard"


def assert_no_identifiers(df: pd.DataFrame) -> None:
    """The export tripwire: raise if any identifier column survived. Called on
    every audience export; kept as a public helper so the guardrail itself is
    directly testable."""
    leaked = set(df.columns) & FORBIDDEN_EXPORT_COLUMNS
    assert not leaked, f"audience export contains forbidden columns: {leaked}"


def person_priority(knowledge: pd.Series, propensity: pd.Series,
                    reachability: pd.Series, org_erv: pd.Series) -> pd.Series:
    """knowledge × propensity × reachability, scaled by the org's ERV share.

    INTERNAL ONLY — never exported (see FORBIDDEN_EXPORT_COLUMNS).
    """
    erv_norm = org_erv.fillna(0).clip(lower=0)
    max_erv = float(erv_norm.max())
    erv_scale = erv_norm / max_erv if max_erv > 0 else 0.0
    return (knowledge.fillna(0) * propensity.fillna(0)
            * reachability.fillna(0) * erv_scale).clip(0, 1)


def build_audiences(people_scored: pd.DataFrame, scheme: str,
                    min_audience_size: int = 2) -> pd.DataFrame:
    """Aggregate scored people into role × org × channel audiences.

    Expected columns: org_node_id, role, recommended_channel, person_priority,
    financial_distress_review. Output: ONE ROW PER AUDIENCE — size, average
    priority, message angle, and how many members need human review. Audiences
    below ``min_audience_size`` are suppressed (a size-1 "audience" is a person).
    """
    grp = (people_scored
           .groupby(["org_node_id", "role", "recommended_channel"], as_index=False)
           .agg(audience_size=("person_priority", "size"),
                avg_priority=("person_priority", "mean"),
                n_review_flagged=("financial_distress_review", "sum")))
    grp = grp[grp["audience_size"] >= min_audience_size].copy()
    grp["avg_priority"] = grp["avg_priority"].round(4)
    grp["scheme"] = scheme
    grp["message_angle"] = MESSAGE_ANGLES.get(scheme, _DEFAULT_ANGLE)
    grp = grp.sort_values("avg_priority", ascending=False).reset_index(drop=True)

    assert_no_identifiers(grp)   # the guardrail, enforced on every export
    return grp
