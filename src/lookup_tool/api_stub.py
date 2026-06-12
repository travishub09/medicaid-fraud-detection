"""
api_stub.py — public billing-risk lookup API (SCAFFOLD).

Route shapes for the public lookup tool. The tool must present peer-relative
*percentiles* and named drivers, with benign-explanation context — never a bare
"fraud" label (defamation safety; see 01-legal-compliance.md). It is also the top
of the marketing funnel: every lookup is an opportunity to educate and capture an
inbound lead under the trust architecture.

This module intentionally avoids importing FastAPI at module load so the package
imports cleanly even before the web extra is installed; wiring is a later increment.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProviderRiskCard:
    """What the public tool returns for one provider/organization."""
    npi: str
    org_name: str
    peer_group: str
    percentile_by_metric: dict       # e.g. {"em_high_level_share": 0.97, ...}
    top_drivers: list                # human-readable named drivers
    benign_explanations: list        # required context, never omitted
    # NB: no "fraud" boolean — the tool reports outlier-ness, not a legal conclusion.


def build_app():
    """Construct the FastAPI app (routes: /lookup/{npi}, /search, /healthz)."""
    raise NotImplementedError(
        "lookup tool API — wire Model A percentile features behind FastAPI; "
        "see 07-sourcing-and-marketing.md and the defamation-safety notes in "
        "01-legal-compliance.md")
