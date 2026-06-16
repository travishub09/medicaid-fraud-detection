"""
events.py — the ListenLayer event taxonomy + ad-platform safety (manifesto §20).

The canonical events and data-layer fields the site/instrumentation emit, plus
the suppression rule that keeps PHI and sensitive allegations out of any
ad-platform / general-analytics payload. The taxonomy mirrors the manifesto's
ListenLayer field set (fraud_typology, persona_segment, trust_stage,
target_cluster_id, content_depth, lead_event_quality, sensitive_free_text_present).
"""

from __future__ import annotations

# data-layer field → whether it is SAFE to send to ad platforms / general
# analytics. Sensitive fields stay first-party only (manifesto §20.9/§20.10).
DATA_LAYER_FIELDS: dict[str, bool] = {
    "fraud_typology": True,        # MA_Risk_Adjustment, DME, Hospice, … (theory, not allegation)
    "persona_segment": True,       # HCC_Coder, Billing_Manager, … (role-based metadata)
    "trust_stage": False,          # awareness→fear→evidence_eval→ready_for_counsel (sensitive behavior)
    "target_cluster_id": True,     # pseudonymous cohort id, NOT an employer name
    "content_depth": True,         # intro / workflow_specific / evidence_specific / consult
    "lead_event_quality": True,    # micro / high_intent / cql / counsel_accept (minimal outward)
    "employer_name": False,        # never to ad platforms
    "allegation_text": False,      # never — privileged/sensitive
    "phi_present": False,          # never
    "sensitive_free_text_present": False,
}

# canonical micro/macro conversions (manifesto §20.4/§20.6)
CANONICAL_EVENTS = [
    "page_view", "resource_download", "self_assessment_complete",
    "anonymous_form_start", "form_step_abandon", "anonymous_submit",
    "return_visit", "consult_request", "secure_intake_start",
    "cql_created", "counsel_accept",
]

# anything matching these never leaves first-party storage
_PHI_LIKE = ("patient", "dob", "date_of_birth", "ssn", "mrn", "member_id",
             "claim_id", "medical_record", "diagnosis")


def ad_safe_payload(event: dict) -> dict:
    """Strip a data-layer event down to only the ad-platform-safe fields.

    Drops every field flagged unsafe in DATA_LAYER_FIELDS, every unknown field
    (default-deny), and anything whose key looks PHI-like. The safety rule is
    code, not policy — there is no path to send a sensitive field outward.
    """
    out = {}
    for k, v in event.items():
        kl = str(k).lower()
        if any(p in kl for p in _PHI_LIKE):
            continue
        if DATA_LAYER_FIELDS.get(k, False):     # default-deny unknown fields
            out[k] = v
    return out


def validate_event(event: dict) -> dict:
    """Annotate an event for routing: is it ad-safe, and must it route to secure
    (counsel-reviewed) handling? Returns the event + ``_secure_only`` /
    ``_ad_safe_fields`` without mutating sensitive content."""
    kl = {str(k).lower(): k for k in event}
    secure = (bool(event.get("sensitive_free_text_present"))
              or bool(event.get("phi_present"))
              or any(p in key for key in kl for p in _PHI_LIKE)
              or "allegation_text" in event)
    return {**event, "_secure_only": secure,
            "_ad_safe_fields": sorted(ad_safe_payload(event))}
