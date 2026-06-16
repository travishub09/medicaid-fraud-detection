"""
feeds — shared plumbing for the API event feeds.

The bulk files stay the source for full-universe statistics; these clients pull
*events* (enforcement actions, dockets, exclusions) and targeted lookups. See
``docs/platform/13-api-feeds.md``.

Design rules (every feed client):
  * transport is injectable (``fetch_json``) so tests run on canned JSON with
    zero network — the repo's no-live-network-in-CI rule;
  * every raw response is cached under ``MEDICAID_DATA_ROOT/feeds/raw/<source>/``
    before parsing (provenance for counsel; replayable without re-fetching);
  * incremental runs use per-feed cursors in ``feeds/state.json`` — cron fetches
    deltas, backfill is an explicit flag;
  * API keys come from the environment (.env): COURTLISTENER_TOKEN, SAM_API_KEY.
"""

from .client import default_fetch_json, default_post_json, cache_raw
from .state import load_state, save_state
from .propublica_nonprofits import (
    search_nonprofits, fetch_organization, nonprofit_owner_edges)
from .usaspending import fetch_recipient_awards, org_federal_funding
from .openfda import fetch_enforcement, enforcement_events

__all__ = [
    "default_fetch_json", "default_post_json", "cache_raw",
    "load_state", "save_state",
    "search_nonprofits", "fetch_organization", "nonprofit_owner_edges",
    "fetch_recipient_awards", "org_federal_funding",
    "fetch_enforcement", "enforcement_events",
]
