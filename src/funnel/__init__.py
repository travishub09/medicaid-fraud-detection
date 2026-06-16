"""
funnel — the acquisition-funnel instrumentation + lead-scoring layer.

Implements the manifesto's ListenLayer/pipeline strategy as CODE (event taxonomy,
behavioral lead score, intake triage). The PUBLIC LAUNCH of the funnel stays
gated on Phase-0 counsel; this is the measurable machinery behind it.

Hard guardrails, enforced in code (legal frame, docs/platform/01):
  * audiences and role-based personas, never named-individual call lists;
  * NO PHI in any analytics/ad-platform payload (suppressed + flagged);
  * the lead score is a prioritization aid with named drivers — never a fraud
    boolean, never an accusation;
  * sensitive free text routes to secure, counsel-reviewed handling only.
"""

from .events import (
    DATA_LAYER_FIELDS, CANONICAL_EVENTS, ad_safe_payload, validate_event)
from .lead_score import composite_lead_score, LEAD_SCORE_WEIGHTS
from .intake import triage_intake

__all__ = [
    "DATA_LAYER_FIELDS", "CANONICAL_EVENTS", "ad_safe_payload", "validate_event",
    "composite_lead_score", "LEAD_SCORE_WEIGHTS", "triage_intake",
]
