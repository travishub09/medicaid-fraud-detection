"""
openfda.py — openFDA enforcement/recall feed (sweep, honest scope).

Source: open.fda.gov (free, real API). HONEST SCOPE: the openFDA API serves
enforcement (recall) reports and adverse-event data — it does NOT cleanly serve
the warning-letter / Form-483 inspection signals (those live in the FDA
dashboard's FOIA reading room). So this client pulls what the API actually
provides — drug/device ENFORCEMENT (recall) reports — and turns matched ones
into integrity events for labs/pharma/device orgs.

  fetch_enforcement(endpoint, search)  → recall/enforcement reports (paged).
  enforcement_events(org_nodes, ...)    → match recalling firms to our org nodes
        by norm_org_name → events (org_node_id, event_type, event_date, detail)
        for the A8 exit-after-event timeline and dossier integrity context.

Transport injected (``fetch_json``); raw responses cached. Recalls are an
integrity signal, never an accusation — corroboration context only.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name
from src.feeds.client import default_fetch_json, cache_raw

# drug + device enforcement (recall) endpoints
ENFORCEMENT_URLS = {
    "drug": "https://api.fda.gov/drug/enforcement.json",
    "device": "https://api.fda.gov/device/enforcement.json",
}


def fetch_enforcement(endpoint: str, search: str = "", limit: int = 100,
                      fetch_json=default_fetch_json,
                      raw_root: Path | None = None) -> pd.DataFrame:
    """Pull recall/enforcement reports → recalling_firm, name_key, reason,
    status, report_date, classification."""
    url = ENFORCEMENT_URLS.get(endpoint)
    if url is None:
        raise ValueError(f"endpoint must be one of {list(ENFORCEMENT_URLS)}")
    params = {"limit": limit}
    if search:
        params["search"] = search
    payload = fetch_json(url, params=params)
    cache_raw("openfda", f"{endpoint}_enforcement", payload, root=raw_root)
    rows = []
    for r in payload.get("results") or []:
        firm = str(r.get("recalling_firm") or "")
        rows.append({"recalling_firm": firm, "name_key": norm_org_name(firm),
                     "reason": str(r.get("reason_for_recall") or "")[:300],
                     "status": str(r.get("status") or ""),
                     "report_date": str(r.get("report_date") or ""),
                     "classification": str(r.get("classification") or ""),
                     "endpoint": endpoint})
    return pd.DataFrame(rows, columns=["recalling_firm", "name_key", "reason",
                                       "status", "report_date", "classification",
                                       "endpoint"])


def enforcement_events(org_nodes: pd.DataFrame, recalls: pd.DataFrame) -> pd.DataFrame:
    """Match recalling firms to our orgs by name key → A8-timeline events.

    Returns org_node_id, event_type ('fda_recall'), event_date, detail. Matching
    is the shared exact name key (conservative; corroboration, not proof)."""
    cols = ["org_node_id", "event_type", "event_date", "detail"]
    if recalls is None or not len(recalls) or not len(org_nodes):
        return pd.DataFrame(columns=cols)
    key_to_org: dict[str, str] = {}
    for o in org_nodes.itertuples():
        names = {str(getattr(o, "org_name", "") or "")}
        names.update(a.strip() for a in str(getattr(o, "aliases", "") or "").split(";"))
        for n in names:
            k = norm_org_name(n)
            if k:
                key_to_org.setdefault(k, str(o.org_node_id))
    rows = []
    for r in recalls.itertuples():
        org = key_to_org.get(str(getattr(r, "name_key", "") or ""))
        if org:
            rows.append({"org_node_id": org, "event_type": "fda_recall",
                         "event_date": str(getattr(r, "report_date", "") or ""),
                         "detail": f"{r.classification}: {r.reason}"[:200]})
    return pd.DataFrame(rows, columns=cols)
