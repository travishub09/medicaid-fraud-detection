"""
docket_monitor.py — retaliation/wrongful-termination docket alerts (STUB).

The warmest grievance leads in the system: employment-litigation plaintiffs who
already blew the whistle internally and got burned. Per the strategy, run as
continuous monitoring against PACER / CourtListener (RECAP) — see
``docs/platform/09-data-procurement.md`` #10 for the feed.

Planned: poll the CourtListener API for new employment-retaliation filings where
the defendant resolves to a canonical org (same ``norm_org_name`` matching as
warn_monitor); emit org-level grievance events for Model B2 and, where the
defendant is Model-A-flagged, escalate as priority sourcing alerts.

Stub: blocked on the docket feed; the matching machinery already exists in
``warn_monitor.match_warn_to_orgs`` and will be reused.
"""

from __future__ import annotations

import pandas as pd


def fetch_retaliation_dockets(since: str) -> pd.DataFrame:
    """Pull new retaliation/wrongful-termination filings from CourtListener."""
    raise NotImplementedError(
        "needs the CourtListener/PACER feed — see docs/platform/09-data-procurement.md #10")


def docket_grievance_events(dockets: pd.DataFrame, org_nodes: pd.DataFrame) -> pd.DataFrame:
    """Resolve defendants to canonical orgs; emit grievance events for Model B2."""
    raise NotImplementedError("blocked on fetch_retaliation_dockets")
