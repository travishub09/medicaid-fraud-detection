"""
person_resolver.py — probabilistic person ↔ employer resolution (Model B bridge).

The hardest, highest-value linkage in the system: resolving a workforce record's
employer STRING ("Acme Health Partners LLC") to the same canonical Organization
the claims world knows as NPIs/TIN. Nail it and a fraud signal connects to a
reachable audience; miss it and the two halves never touch.

GUARDRAILS (legal frame, docs/platform/01 + 05) — these are WHY this stays gated
operationally even though the code is built:
  * the output is person→employer LINKS that feed Model B's AUDIENCE builder
    (role × org × channel) — never a named-individual call list; ``person_id`` is
    an opaque token, no PII flows through here;
  * the "likely whistleblower at employer X" inference is sensitive from creation
    — minimize retention, never export it (the Model B chain's
    ``assert_no_identifiers`` tripwire enforces this downstream);
  * running this on REAL people-data requires the FCRA / privacy review and a
    licensed roster source first. The code is label-free and testable on
    synthetic rosters now; the gate is operational, not engineering.

Method: a transparent Fellegi–Sunter-style scored linkage (no extra deps) —
exact normalized-name match is high-confidence; otherwise a token-Jaccard ×
sequence-ratio similarity over normalized names, blocked by state when present;
auto_accept / review / auto_reject bands with confidence + provenance on every
link. (Swap in the Splink backend for scale; same interface.)
"""

from __future__ import annotations

from difflib import SequenceMatcher

import pandas as pd

from .resolve_entities import norm_org_name


def _similarity(a_key: str, a_tokens: set[str], b_key: str, b_tokens: set[str]) -> float:
    if not a_key or not b_key:
        return 0.0
    if a_key == b_key:
        return 1.0
    jacc = len(a_tokens & b_tokens) / len(a_tokens | b_tokens) if (a_tokens | b_tokens) else 0.0
    ratio = SequenceMatcher(None, a_key, b_key).ratio()
    return round(0.5 * jacc + 0.5 * ratio, 4)


def resolve_people_to_orgs(workforce: pd.DataFrame, org_nodes: pd.DataFrame, *,
                           review_band: tuple[float, float] = (0.3, 0.8)
                           ) -> pd.DataFrame:
    """Resolve workforce employer strings → canonical Organization node ids.

    ``workforce``: person_id, employer_name (+ optional state). ``org_nodes``:
    org_node_id, org_name (+ optional aliases, addr_state). Returns person_id,
    org_node_id, match_confidence, match_provenance, decision
    ∈ {auto_accept, review, auto_reject}. Blocks on state when both sides carry
    it (a faster, cleaner candidate set); exact name-key matches score 1.0.
    """
    lo, hi = review_band
    orgs = org_nodes.copy()
    orgs["nkey"] = orgs["org_name"].fillna("").map(norm_org_name)
    orgs["ntoks"] = orgs["nkey"].map(lambda k: set(k.split()))
    orgs["nstate"] = (orgs["addr_state"].fillna("").astype(str).str.upper()
                      if "addr_state" in orgs.columns else "")
    # alias expansion: each alias is another matchable key for the same org
    alias_rows = []
    if "aliases" in orgs.columns:
        for r in orgs.itertuples():
            for a in str(getattr(r, "aliases", "") or "").split(";"):
                k = norm_org_name(a)
                if k and k != r.nkey:
                    alias_rows.append({"org_node_id": r.org_node_id, "nkey": k,
                                       "ntoks": set(k.split()), "nstate": r.nstate})
    base = orgs[["org_node_id", "nkey", "ntoks", "nstate"]]
    cand = pd.concat([base, pd.DataFrame(alias_rows)], ignore_index=True) \
        if alias_rows else base

    rows = []
    for w in workforce.itertuples():
        ekey = norm_org_name(str(getattr(w, "employer_name", "") or ""))
        etoks = set(ekey.split())
        wstate = str(getattr(w, "state", "") or "").upper()
        pool = cand[cand["nstate"] == wstate] if (wstate and (cand["nstate"] == wstate).any()) else cand

        best_org, best_sim = "", 0.0
        for c in pool.itertuples():
            sim = _similarity(ekey, etoks, c.nkey, c.ntoks)
            if sim > best_sim:
                best_org, best_sim = str(c.org_node_id), sim
        decision = ("auto_accept" if best_sim >= hi else
                    "auto_reject" if best_sim < lo else "review")
        rows.append({
            "person_id": getattr(w, "person_id", None),
            "org_node_id": best_org if decision != "auto_reject" else "",
            "match_confidence": best_sim,
            "match_provenance": f"name_sim={best_sim}"
                                + (f"; state_block={wstate}" if wstate else ""),
            "decision": decision,
        })
    return pd.DataFrame(rows, columns=["person_id", "org_node_id",
                                       "match_confidence", "match_provenance",
                                       "decision"])


def build_employed_by_edges(person_org_matches: pd.DataFrame,
                            include_review: bool = False) -> pd.DataFrame:
    """Temporal Person→Org ``employed_by`` edges from accepted matches.

    Uses auto_accept matches (optionally review-band too); carries role/start/end
    when the match frame has them. ``person_id`` stays an opaque token — these
    edges feed the audience builder, never a contact list.
    """
    cols = ["src_id", "dst_id", "edge_type", "role", "start_date", "end_date",
            "match_confidence"]
    m = person_org_matches.copy()
    keep = {"auto_accept"} | ({"review"} if include_review else set())
    m = m[m["decision"].isin(keep) & (m["org_node_id"] != "")]
    if not len(m):
        return pd.DataFrame(columns=cols)
    return pd.DataFrame({
        "src_id": "person:" + m["person_id"].astype(str),
        "dst_id": m["org_node_id"].astype(str),
        "edge_type": "employed_by",
        "role": m["role"].astype(str) if "role" in m.columns else "",
        "start_date": pd.to_datetime(m.get("start_date"), errors="coerce"),
        "end_date": pd.to_datetime(m.get("end_date"), errors="coerce"),
        "match_confidence": m["match_confidence"],
    }).reset_index(drop=True)


def tenure_overlaps(employed_by_edges: pd.DataFrame, period_start: str,
                    period_end: str) -> pd.Series:
    """Point-in-time tenure overlap: did the employment interval intersect the
    scheme period? (The temporal correctness Model B's knowledge gate needs.)"""
    e = employed_by_edges
    start = pd.to_datetime(e["start_date"], errors="coerce")
    end = pd.to_datetime(e["end_date"], errors="coerce").fillna(pd.Timestamp("2100-01-01"))
    ps, pe = pd.Timestamp(period_start), pd.Timestamp(period_end)
    return (start.fillna(pd.Timestamp("1900-01-01")) <= pe) & (end >= ps)
