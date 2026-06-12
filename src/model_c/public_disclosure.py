"""
public_disclosure.py — the public-disclosure screen (Model C's first real function).

The fourth legal gate, automated (expansion plan A3; 06-model-c.md relator
features). Under 31 U.S.C. §3730(e)(4) allegations already in public litigation,
news, audits, or government reports can bar a relator's claim — so before any
outreach decision, every top-ranked org is checked against the sources we
already hold: the DOJ/OIG case DB and the CourtListener qui tam docket pulls.

Two framing rules, hard-coded:

  * A flag means COUNSEL MUST ASSESS the public-disclosure bar before relator
    outreach — it is a routing signal, never a legal conclusion.
  * The absence of a flag is NOT clearance. The screen is only as good as the
    sources it checked, so every row records what was checked
    (``disclosure_sources_checked``); "no match against nothing" must never
    read as a clean bill.

Matching is the shared ``norm_org_name`` key over the org's name + aliases —
the same key that joins these sources everywhere else. Ambiguity is resolved
in the conservative direction: if one name key matches several orgs, all of
them flag (over-flagging disclosure risk is cheap; missing it is fatal).
"""

from __future__ import annotations

import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name

MAX_CITATIONS = 3       # per org, on the dossier; the rest summarized as a count


def _org_name_keys(row) -> set[str]:
    names = {str(row.get("org_name", "") or "")}
    names.update(a.strip() for a in str(row.get("aliases", "") or "").split(";"))
    return {k for k in (norm_org_name(n) for n in names) if k}


def _case_citations(case_db: pd.DataFrame) -> dict[str, list[str]]:
    """name_key → ['DOJ/OIG case <id>: <defendant> (<date>)', ...]"""
    out: dict[str, list[str]] = {}
    for r in case_db.itertuples():
        key = str(getattr(r, "defendant_name_key", "") or "")
        if not key:
            continue
        cite = (f"DOJ/OIG case {getattr(r, 'case_id', '?')}: "
                f"{getattr(r, 'defendant_name', '?')} "
                f"({getattr(r, 'announced_date', '?') or 'date unknown'})")
        out.setdefault(key, []).append(cite)
    return out


def _docket_citations(dockets: pd.DataFrame) -> dict[str, list[str]]:
    """name_key → ['docket <id>: <case name> (filed <date>)', ...]

    Accepts the docket-monitor output (already carries ``defendant_key``) or a
    raw docket pull (derives the key from ``case_name``)."""
    d = dockets.copy()
    if "defendant_key" not in d.columns:
        from src.sourcing.docket_monitor import _defendant_key_from_case_name
        d["defendant_key"] = d["case_name"].map(_defendant_key_from_case_name)
    out: dict[str, list[str]] = {}
    for r in d.itertuples():
        key = str(getattr(r, "defendant_key", "") or "")
        if not key:
            continue
        cite = (f"docket {getattr(r, 'docket_id', '?')}: "
                f"{getattr(r, 'case_name', '?')} "
                f"(filed {getattr(r, 'date_filed', '?') or '?'})")
        out.setdefault(key, []).append(cite)
    return out


def public_disclosure_screen(orgs: pd.DataFrame,
                             case_db: pd.DataFrame | None = None,
                             dockets: pd.DataFrame | None = None
                             ) -> pd.DataFrame:
    """One row per org: flag + named citations + what was actually checked.

    ``orgs`` needs ``org_node_id`` + ``org_name`` (+ optional ``aliases``).
    Returns ``org_node_id``, ``public_disclosure_flag`` (0/1),
    ``public_disclosure_citations`` (named, capped at MAX_CITATIONS + count),
    ``disclosure_sources_checked`` (e.g. ``"case_db:120; dockets:43"``).
    """
    by_key: dict[str, list[str]] = {}
    checked: list[str] = []
    if case_db is not None:
        checked.append(f"case_db:{len(case_db)}")
        for k, cites in _case_citations(case_db).items():
            by_key.setdefault(k, []).extend(cites)
    if dockets is not None:
        checked.append(f"dockets:{len(dockets)}")
        for k, cites in _docket_citations(dockets).items():
            by_key.setdefault(k, []).extend(cites)
    sources = "; ".join(checked) if checked else "none"

    rows = []
    for _, org in orgs.iterrows():
        cites: list[str] = []
        for key in sorted(_org_name_keys(org)):
            cites.extend(by_key.get(key, []))
        cites = list(dict.fromkeys(cites))                    # de-dup, keep order
        shown = "; ".join(cites[:MAX_CITATIONS])
        if len(cites) > MAX_CITATIONS:
            shown += f" (+{len(cites) - MAX_CITATIONS} more)"
        rows.append({
            "org_node_id": str(org["org_node_id"]),
            "public_disclosure_flag": int(bool(cites)),
            "public_disclosure_citations": shown,
            "disclosure_sources_checked": sources,
        })
    out = pd.DataFrame(rows, columns=["org_node_id", "public_disclosure_flag",
                                      "public_disclosure_citations",
                                      "disclosure_sources_checked"])
    assert len(out) == len(orgs), "screen must return one row per org"
    return out
