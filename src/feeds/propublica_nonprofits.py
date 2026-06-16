"""
propublica_nonprofits.py — ProPublica Nonprofit Explorer feed (sweep 2.11).

Source: projects.propublica.org/nonprofits/api (free, no key) — IRS Form 990
data for 1.8M+ tax-exempt orgs, including nonprofit hospitals/health systems.
Two uses for the platform:

  * owner / related-party enrichment beyond CMS All-Owners: a 990's officers and
    its Schedule R related organizations are exactly the affiliations the
    ownership graph wants (doc 14 B8), available NOW without parsing raw EDGAR.
  * a finance-persona hint for Model B (exec-comp signals where the money sits).

  search_nonprofits(name)   → candidate EINs/orgs by name (the /search endpoint)
  fetch_organization(ein)   → one org's filings + officers (the /organizations endpoint)
  nonprofit_owner_edges(...)→ normalize matched orgs into owner: edges keyed by
                              the shared norm_org_name, ready for the entity graph.

Transport is injected (``fetch_json``); raw responses cached for provenance;
matching uses the shared normalizer so nonprofits join the graph like everything
else. Public 990 data only — no PHI, no person-level scoring.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name
from src.feeds.client import default_fetch_json, cache_raw

SEARCH_URL = "https://projects.propublica.org/nonprofits/api/v2/search.json"
ORG_URL = "https://projects.propublica.org/nonprofits/api/v2/organizations/{ein}.json"


def search_nonprofits(name: str, fetch_json=default_fetch_json,
                      raw_root: Path | None = None) -> pd.DataFrame:
    """Search the Nonprofit Explorer by name → candidate orgs.

    Returns ein, name, name_key (shared normalizer), state, ntee_code, city.
    """
    payload = fetch_json(SEARCH_URL, params={"q": name})
    cache_raw("propublica", f"search_{norm_org_name(name)[:40] or 'q'}",
              payload, root=raw_root)
    rows = []
    for o in payload.get("organizations") or []:
        nm = str(o.get("name") or "")
        rows.append({"ein": str(o.get("ein") or ""),
                     "name": nm, "name_key": norm_org_name(nm),
                     "state": str(o.get("state") or ""),
                     "ntee_code": str(o.get("ntee_code") or ""),
                     "city": str(o.get("city") or "")})
    return pd.DataFrame(rows, columns=["ein", "name", "name_key", "state",
                                       "ntee_code", "city"])


def fetch_organization(ein: str, fetch_json=default_fetch_json,
                       raw_root: Path | None = None) -> dict:
    """Fetch one org's 990 detail (filings + officers) by EIN."""
    ein_clean = str(ein).replace("-", "").strip()
    payload = fetch_json(ORG_URL.format(ein=ein_clean))
    cache_raw("propublica", f"org_{ein_clean}", payload, root=raw_root)
    return payload


def nonprofit_owner_edges(org_nodes: pd.DataFrame,
                          fetch_json=default_fetch_json,
                          raw_root: Path | None = None) -> pd.DataFrame:
    """Resolve our org nodes to nonprofits and emit owner-side enrichment rows.

    For each org node, search by name; if a nonprofit's name_key matches the
    org's (org_name or an alias), record it. Returns org_node_id, ein,
    nonprofit_name, name_key, state, ntee_code — the join the entity graph turns
    into `owner:`/nonprofit-affiliation context. Conservative exact-name-key
    match (no fuzzy), so it is corroboration, not proof.
    """
    rows = []
    for o in org_nodes.itertuples():
        keys = {norm_org_name(str(getattr(o, "org_name", "") or ""))}
        keys.update(norm_org_name(a) for a in
                    str(getattr(o, "aliases", "") or "").split(";"))
        keys.discard("")
        if not keys:
            continue
        seen = False
        for cand in search_nonprofits(
                str(getattr(o, "org_name", "") or ""),
                fetch_json=fetch_json, raw_root=raw_root).itertuples():
            if cand.name_key in keys and not seen:
                rows.append({"org_node_id": str(o.org_node_id), "ein": cand.ein,
                             "nonprofit_name": cand.name, "name_key": cand.name_key,
                             "state": cand.state, "ntee_code": cand.ntee_code})
                seen = True
    return pd.DataFrame(rows, columns=["org_node_id", "ein", "nonprofit_name",
                                       "name_key", "state", "ntee_code"])
