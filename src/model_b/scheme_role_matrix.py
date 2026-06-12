"""
scheme_role_matrix.py — the scheme → role line-of-sight matrix (real data).

Encodes, for each scheme hypothesis Model A can emit, which roles have HIGH vs.
MEDIUM line-of-sight to the scheme, and which roles additionally carry strong
documentary access (relator value scales with documentation). Transcribed from
``docs/platform/05-model-b.md`` §1.1; used by ``knowledge.py``.

This is the only populated piece of the Model B scaffold — it is reference data,
not a model, so it ships complete.
"""

from __future__ import annotations

# scheme -> {"high": [...roles...], "medium": [...roles...]}
SCHEME_ROLE_LINE_OF_SIGHT: dict[str, dict[str, list[str]]] = {
    "upcoding_ma_risk_adjustment": {
        "high": ["coder", "cdi_specialist", "revenue_integrity", "billing_manager",
                 "compliance", "internal_audit", "medical_director"],
        "medium": ["physician", "finance"],
    },
    "kickbacks_aks_stark": {
        "high": ["sales_rep", "contracting", "business_development", "compliance"],
        "medium": ["marketing", "paid_medical_director", "finance"],
    },
    "medical_necessity": {
        "high": ["um_nurse", "um_physician", "clinical_staff", "medical_director"],
        "medium": ["prior_auth_staff"],
    },
    "home_health_hospice_eligibility": {
        "high": ["field_rn", "home_health_aide", "case_manager",
                 "marketing_liaison", "medical_director"],
        "medium": ["intake"],
    },
    "lab_genetic_testing": {
        "high": ["sales_rep", "order_coordination"],
        "medium": ["lab_director", "client_services"],
    },
    "pharmacy_340b": {
        "high": ["pharmacist", "340b_program_manager", "pbm_analyst"],
        "medium": ["pharmacy_tech"],
    },
    "evv_personal_care": {
        "high": ["scheduler", "coordinator", "billing"],
        "medium": ["aide"],
    },
    "cost_report_fraud": {
        "high": ["controller", "reimbursement_analyst", "cfo"],
        "medium": ["finance_staff"],
    },
}

# Roles whose work product evidences the scheme (documentation-access modifier > 1).
HIGH_DOCUMENTATION_ROLES: set[str] = {
    "compliance", "internal_audit", "revenue_integrity", "coder", "cdi_specialist",
    "controller", "reimbursement_analyst", "cfo", "finance", "pbm_analyst",
    "340b_program_manager", "case_manager",
}


def line_of_sight_weight(scheme: str, role: str) -> float:
    """0.0 / 0.4 / 1.0 base weight for (scheme, role); the matrix lookup B1 uses."""
    tier = SCHEME_ROLE_LINE_OF_SIGHT.get(scheme, {})
    if role in tier.get("high", []):
        return 1.0
    if role in tier.get("medium", []):
        return 0.4
    return 0.0
