"""
label_expansion.py — Tier 0 and L1.1 of docs/LABEL_EXPANSION_PLAN_2026-10.md:
more FCA-shaped positives from records the pipeline already holds, under the
integrity gate (an official resolved outcome or fraud-shaped statute, a HARD
identifier, a date, a tier and a provenance string on every row).

  1. AFFILIATED CONVICTIONS (the owner-run gap, audit finding A1).  83 percent
     of forward positives never join the billing universe because they are
     INDIVIDUALS (aides, nurses, billers, owners convicted personally) who never
     billed under their own NPI.  The claims say who they worked for: the
     billing NPI whose claims carried the individual as the RENDERING provider
     in the months before the exclusion.  That organization becomes a positive
     under the basis ``affiliated_conviction``, keyed by the billing NPI, dated
     by the exclusion, carrying the individual's NPI, the dollars rendered and
     the share of the biller's claims the individual rendered.  Budget: one
     excluded individual labels at most ``max_orgs`` billers, and only billers
     where the individual rendered at least ``min_share`` of the claims or
     ``min_dollars``; the rest are listed, never labelled.

  2. CIA PARTIES THROUGH OWNERSHIP (audit finding A4: case-DB recall against
     the OIG CIA roster 32 percent, only 27 percent of parties resolve to a
     graph org by name, because the settling party is the PARENT and the
     enrolled provider is a differently named subsidiary).  A CIA party resolves
     (a) by exact name key to an org node, (b) by exact name key to an
     ORGANIZATION OWNER in PECOS (owner nodes of type O) and from there to every
     org that owner owns, with a per-party budget (a chain owner that owns more
     than ``max_owned`` orgs is listed, not broadcast).  Names that resolve
     neither way go to the research residue.  Output in the defendant-resolver
     shape (case_id, defendant_name, npi, status, match_level) plus CIA case rows
     in the case-DB schema, so ``case_labels`` consumes them unchanged.

Nothing here fuzzy-matches a name (the fuzzy tier was retired 2026-10-11).
Diagnostics and label files only; nothing changes a model.  Leads for counsel
review, never accusations: a positive is a public record, not a finding.

    python -m src.model_a.label_expansion affiliated --exclusion-nodes <graph>/nodes/exclusion_nodes.parquet \\
        --spending processed/spending_fact.parquet --cutoff 2021-12 --out processed/affiliated_convictions.parquet
    python -m src.model_a.label_expansion cia --cia preclean/cia/cia_list.csv --graph-dir <graph> \\
        --out-cases enforcement/cia_cases.csv --out-npis processed/cia_defendant_npis.parquet
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

from src.attempt_2.duck import connect as _duck_connect
from src.entity_graph.resolve_entities import norm_org_name


def _sql_str(p) -> str:
    return str(p).replace("'", "''")


# ------------------------------------------------------- affiliated convictions

AFF_COLS = ["npi", "excluded_npi", "excl_date", "excl_type", "fca_tier", "fca_basis",
            "entity_name", "dollars_rendered", "biller_dollars_window", "share_of_biller",
            "months_rendered", "rank_for_individual", "basis", "kept", "drop_reason"]


def affiliated_convictions(exclusion_nodes: pd.DataFrame, spending: str | Path,
                           window_months: int = 24, min_share: float = 0.05,
                           min_dollars: float = 25_000.0, max_orgs: int = 1,
                           tier: str = "hard", con=None) -> pd.DataFrame:
    """For every exclusion record in the kept tier with a valid NPI and a date,
    the billing NPIs whose claims carried that NPI as the RENDERING provider in
    the ``window_months`` before the exclusion date.  One row per (biller,
    excluded individual); ``kept`` marks the rows that pass the budget (top
    ``max_orgs`` billers by dollars, each with share >= ``min_share`` OR dollars
    >= ``min_dollars``); the rest stay in the table with a ``drop_reason``
    (quarantine, never delete).  A biller that is the individual's own NPI is
    never a row (that is the ordinary positive, already labelled)."""
    from .fca_relevance import classify_series, tier_mask
    con = con or _duck_connect()
    e = exclusion_nodes.copy()
    e["npi"] = e.get("npi").astype("string").str.strip()
    e = e[e["npi"].notna() & e["npi"].str.fullmatch(r"\d{10}").fillna(False)].copy()
    e["_dt"] = pd.to_datetime(e.get("excl_date"), errors="coerce")
    e = e[e["_dt"].notna()].copy()
    e["excl_type"] = e.get("excl_type", "").fillna("").astype(str)
    tiers = classify_series(e["excl_type"])
    e["fca_tier"] = tiers["fca_tier"].values
    e["fca_basis"] = tiers["fca_basis"].values
    e = e[tier_mask(e["fca_tier"], tier).values].copy()
    if not len(e):
        return pd.DataFrame(columns=AFF_COLS)
    # earliest kept record per individual
    e = e.sort_values(["npi", "_dt"]).groupby("npi", as_index=False).first()
    e["_start"] = (e["_dt"] - pd.DateOffset(months=int(window_months))).dt.strftime("%Y-%m")
    e["_end"] = e["_dt"].dt.strftime("%Y-%m")
    x = e[["npi", "_start", "_end"]].rename(columns={"npi": "xnpi"})
    con.register("x_excl", x)
    p = _sql_str(spending)
    cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()]
    if "servicing_npi" not in cols:
        raise ValueError(f"{spending}: no servicing_npi column; the rendering grain is needed")
    rendered = con.execute(f"""
        WITH s AS (
            SELECT CAST(billing_npi AS VARCHAR) AS b, CAST(servicing_npi AS VARCHAR) AS r,
                   substr(CAST(service_month AS VARCHAR), 1, 7) AS ym, CAST(total_paid AS DOUBLE) AS paid
            FROM read_parquet('{p}')
            WHERE servicing_npi IS NOT NULL AND CAST(servicing_npi AS VARCHAR) IN (SELECT xnpi FROM x_excl)
        )
        SELECT s.b AS npi, x.xnpi AS excluded_npi, SUM(s.paid) AS dollars_rendered,
               COUNT(DISTINCT s.ym) AS months_rendered
        FROM s JOIN x_excl x ON s.r = x.xnpi AND s.ym >= x._start AND s.ym <= x._end
        WHERE s.b <> x.xnpi
        GROUP BY 1, 2""").df()
    if not len(rendered):
        con.unregister("x_excl")
        return pd.DataFrame(columns=AFF_COLS)
    con.register("cand", rendered[["npi", "excluded_npi"]])
    totals = con.execute(f"""
        WITH s AS (
            SELECT CAST(billing_npi AS VARCHAR) AS b, substr(CAST(service_month AS VARCHAR), 1, 7) AS ym,
                   CAST(total_paid AS DOUBLE) AS paid
            FROM read_parquet('{p}')
            WHERE CAST(billing_npi AS VARCHAR) IN (SELECT DISTINCT npi FROM cand)
        )
        SELECT c.npi, c.excluded_npi, SUM(s.paid) AS biller_dollars_window
        FROM cand c JOIN x_excl x ON x.xnpi = c.excluded_npi
        JOIN s ON s.b = c.npi AND s.ym >= x._start AND s.ym <= x._end
        GROUP BY 1, 2""").df()
    con.unregister("x_excl")
    con.unregister("cand")
    out = rendered.merge(totals, on=["npi", "excluded_npi"], how="left")
    out["biller_dollars_window"] = out["biller_dollars_window"].fillna(0.0)
    out["share_of_biller"] = (out["dollars_rendered"] / out["biller_dollars_window"]).where(
        out["biller_dollars_window"] > 0, 0.0).clip(0, 1)
    meta = e.set_index("npi")
    out["excl_date"] = out["excluded_npi"].map(meta["_dt"]).dt.strftime("%Y-%m-%d")
    out["excl_type"] = out["excluded_npi"].map(meta["excl_type"])
    out["fca_tier"] = out["excluded_npi"].map(meta["fca_tier"])
    out["fca_basis"] = out["excluded_npi"].map(meta["fca_basis"])
    out["entity_name"] = out["excluded_npi"].map(meta.get("entity_name", pd.Series(dtype=str))).fillna("")
    out = out.sort_values(["excluded_npi", "dollars_rendered"], ascending=[True, False])
    out["rank_for_individual"] = out.groupby("excluded_npi").cumcount() + 1
    strong = (out["share_of_biller"] >= float(min_share)) | (out["dollars_rendered"] >= float(min_dollars))
    out["kept"] = (out["rank_for_individual"] <= int(max_orgs)) & strong
    out["drop_reason"] = ""
    out.loc[~strong, "drop_reason"] = "below_share_and_dollar_floor"
    out.loc[strong & (out["rank_for_individual"] > int(max_orgs)), "drop_reason"] = "over_per_individual_budget"
    out["basis"] = "affiliated_conviction"
    return out[AFF_COLS].reset_index(drop=True)


def merge_affiliated(forward_label: pd.DataFrame, aff: pd.DataFrame, cutoff: str,
                     horizon_months: int | None = None) -> pd.DataFrame:
    """Add the kept affiliated rows to a forward label (``build_prospective_label``
    shape).  A biller already carrying a positive keeps its own earlier record;
    a biller known at the freeze (pre-cutoff exclusion of its own) is never
    re-labelled; a new biller row is a positive when the exclusion falls after
    the cutoff (and within the horizon)."""
    from .prospective_label import _cutoff_ts
    if aff is None or not len(aff):
        return forward_label
    k = aff[aff["kept"].astype(bool)].copy()
    if not len(k):
        return forward_label
    k["dt"] = pd.to_datetime(k["excl_date"], errors="coerce")
    k = k.sort_values(["npi", "dt"]).groupby("npi", as_index=False).first()
    cutoff_ts = _cutoff_ts(cutoff)
    end = (cutoff_ts + pd.DateOffset(months=int(horizon_months))) if horizon_months else None
    fl = forward_label.copy()
    have = fl.set_index(fl["npi"].astype(str))
    rows = []
    for r in k.itertuples():
        npi = str(r.npi)
        in_window = (r.dt > cutoff_ts) and (end is None or r.dt <= end)
        if npi in have.index:
            prev = have.loc[npi]
            if int(prev.get("was_excluded_pre_cutoff", 0) or 0) == 1 or int(prev.get("is_prospective_positive", 0) or 0) == 1:
                continue                                   # already known / already positive
            fl = fl[fl["npi"].astype(str) != npi]           # replace the negative row
        rows.append({"npi": npi, "first_excl_date": r.dt.strftime("%Y-%m-%d"),
                     "excl_type": f"affiliated:{r.excl_type}", "entity_name": r.entity_name,
                     "is_prospective_positive": int(in_window), "was_excluded_pre_cutoff": 0,
                     "undated_exclusion": 0, "fca_tier": r.fca_tier,
                     "fca_basis": f"affiliated conviction: {r.fca_basis} (rendering NPI {r.excluded_npi}, "
                                  f"{float(r.share_of_biller):.0%} of the biller's claims)",
                     "label_policy": str(fl["label_policy"].iloc[0]) if len(fl) and "label_policy" in fl.columns else "hard"})
    if rows:
        fl = pd.concat([fl, pd.DataFrame(rows)[fl.columns.intersection(pd.DataFrame(rows).columns).tolist()
                                              + [c for c in pd.DataFrame(rows).columns if c not in fl.columns]]],
                       ignore_index=True)
    return fl.reset_index(drop=True)


# --------------------------------------------------------------- CIA parties

CIA_NPI_COLS = ["case_id", "defendant_name", "npi", "org_node_id", "status", "match_level",
                "n_orgs_for_party", "label_weight"]


def _owner_name_index(owner_nodes: pd.DataFrame) -> dict[str, list[str]]:
    """Normalized ORGANIZATION-owner display name -> owner node ids."""
    idx: dict[str, list[str]] = {}
    if owner_nodes is None or not len(owner_nodes):
        return idx
    o = owner_nodes[owner_nodes.get("owner_type", "").fillna("").astype(str).str.upper() == "O"]
    for r in o.itertuples():
        k = norm_org_name(str(getattr(r, "owner_display_name", "") or ""))
        if k:
            idx.setdefault(k.upper(), []).append(str(r.node_id))
    return idx


_PARTY_SPLIT_RE = re.compile(r"\s*;\s*|\s+and\s+|\s*&\s*|\s+d\.?b\.?a\.?\s+|\s+d/b/a\s+|\s+doing\s+business\s+as\s+|\s*/\s*",
                             re.I)
_PERSON_TAIL_RE = re.compile(r"\b(?:M\.?D\.?|D\.?O\.?|D\.?C\.?|D\.?P\.?M\.?|R\.?P\.?H\.?|D\.?D\.?S\.?|P\.?A\.?-?C|N\.?P\.?|"
                             r"Ph\.?D\.?|Dr\.?)\b\.?", re.I)
_MANUFACTURER_TOKENS = ("PHARMACEUTICAL", "PHARMACEUTICALS", "PHARMA ", "PHARMA PLC", "THERAPEUTICS", "BIOSCIENCES",
                        "BIOPHARMA", "BIOTECH", "IMPLANT", "DEVICES", "MEDICAL SYSTEMS", "LABORATORIES INC", "GENETICS INC")
_MANUFACTURER_NAMES = ("NOVARTIS", "SANOFI", "AVENTIS", "INDIVIOR", "INSYS", "EXACTECH", "AESCULAP", "EYEPOINT",
                       "VELOXIS", "DUSA", "MERIT MEDICAL", "SUN PHARMACEUTICAL", "PFIZER", "ABBVIE", "ABBOTT",
                       "MEDTRONIC", "BOSTON SCIENTIFIC", "STRYKER", "ZIMMER", "BIOMET", "SMITH NEPHEW", "DEPUY",
                       "JOHNSON JOHNSON", "AMGEN", "GILEAD", "BIOGEN", "TEVA", "MALLINCKRODT", "PURDUE", "ASTRAZENECA",
                       "GLAXO", "MERCK", "LILLY", "BRISTOL", "GENENTECH", "ROCHE", "BAYER", "TAKEDA", "OLYMPUS",
                       "LUNDBECK", "ASTELLAS", "RESMED", "INNOVASIS", "JAZZ PHARMACEUTICALS", "AVANIR", "NUVASIVE",
                       "ORTHOFIX", "ARTHREX", "GLOBUS", "ALERE", "ABIOMED", "PHILIPS", "SIEMENS", "GE HEALTHCARE")


def party_components(name: str) -> list[str]:
    """The separately enrolled names a compound CIA party line carries: 'Acme Clinic,
    P.A. and John Doe, M.D.; d.b.a. Acme Pain' -> ['Acme Clinic, P.A.', 'John Doe',
    'Acme Pain']. Splits on ';', 'and', '&', '/', d.b.a. phrases; strips credential
    tails (M.D., D.C., RPH, Dr.) so a person's own billing entity can still match by
    exact name. Components under two characters are dropped; order is kept; the
    whole line is NOT a component (the caller tries it first)."""
    parts = [p.strip(" ,.") for p in _PARTY_SPLIT_RE.split(str(name or "")) if p and p.strip(" ,.")]
    out: list[str] = []
    for p in parts:
        p = _PERSON_TAIL_RE.sub("", p).strip(" ,.")
        p = re.sub(r"\s{2,}", " ", p)
        if len(p) >= 2 and p not in out:
            out.append(p)
    return out


def is_manufacturer_name(name_key: str) -> bool:
    """A drug or device manufacturer: a CIA party that is never an enrolled provider
    (its settlement is a kickback / off-label matter; the residue says so instead of
    sending it to research)."""
    k = str(name_key or "").upper()
    if not k:
        return False
    return any(t in k for t in _MANUFACTURER_TOKENS) or any(k.startswith(n) or f" {n}" in f" {k}" for n in _MANUFACTURER_NAMES)


def cia_resolve(cia_norm: pd.DataFrame, org_nodes: pd.DataFrame, owner_nodes: pd.DataFrame | None,
                owned_by_edges: pd.DataFrame | None, npi_to_org: pd.DataFrame,
                max_owned: int = 150, budget: int = 100,
                case_ids: dict[str, str] | None = None,
                max_npis: int = 150) -> tuple[pd.DataFrame, pd.DataFrame]:
    """CIA parties -> NPIs.  Returns (resolved rows in the defendant-resolver
    shape, the residue).  Two exact paths, no fuzzy matching:

      cia_exact_org    the party's name key equals an org node's name key
      cia_owner        the party's name key equals a PECOS organization owner's
                       name; every org that owner owns resolves (an owner of more
                       than ``max_owned`` orgs is a chain: listed in the residue
                       as ``chain_owner``, never broadcast)
      cia_component    (2026-10-13) the whole line matched nothing, but one of its
                       COMPONENTS did (``party_components``: 'X and Y; d.b.a. Z'):
                       each matching component resolves by either exact path

    CHAIN GUARD on every path (2026-10-13, the first real run: Walgreen Co matched
    one org node with 11,217 member NPIs): a party reaching more than ``max_npis``
    NPIs is listed as ``chain_org`` and never labelled (the same 150-NPI rule
    case_labels applies to a settled org). A drug / device manufacturer in the
    residue is ``manufacturer_not_enrolled`` (never a provider; not for research).

    ``label_weight`` = min(1, budget / NPIs for the party), the same per-case
    budget case_labels applies to broadcast rows (case_labels honours it on the
    resolved rows)."""
    id_col = next((k for k in ("org_node_id", "node_id") if k in org_nodes.columns), None)
    if id_col is None:
        raise ValueError("org_nodes: no org_node_id / node_id column")
    ok_col = next((k for k in ("name_key", "org_name_key") if k in org_nodes.columns), None)
    if ok_col:
        org_key = org_nodes[ok_col].fillna("").astype(str).str.upper()
    else:
        nm = next((k for k in ("org_legal_name", "org_name", "name", "entity_name", "org_display_name")
                   if k in org_nodes.columns), None)
        org_key = org_nodes[nm].fillna("").astype(str).map(norm_org_name).str.upper() if nm else pd.Series("", index=org_nodes.index)
    org_by_key: dict[str, list[str]] = {}
    for k, nid in zip(org_key, org_nodes[id_col].astype(str)):
        if k:
            org_by_key.setdefault(k, []).append(nid)
    owner_idx = _owner_name_index(owner_nodes) if owner_nodes is not None else {}
    owned: dict[str, list[str]] = {}
    if owned_by_edges is not None and len(owned_by_edges):
        for r in owned_by_edges.itertuples():
            owned.setdefault(str(r.dst_id), []).append(str(r.src_id))
    members: dict[str, list[str]] = {}
    for r in npi_to_org.itertuples():
        members.setdefault(str(r.org_node_id), []).append(str(r.npi))

    def _lookup(k: str) -> tuple[list[str], str]:
        """(org ids, match level) for one exact key, or ([], 'chain_owner') when the
        owner path reaches a chain, or ([], '') when nothing matches."""
        if k and k in org_by_key:
            return list(org_by_key[k]), "cia_exact_org"
        if k and k in owner_idx:
            o: list[str] = []
            for oid in owner_idx[k]:
                o += owned.get(oid, [])
            o = sorted(set(o))
            if len(o) > int(max_owned):
                return o, "chain_owner"       # the orgs come back for the count; never labelled
            return o, "cia_owner"
        return [], ""

    rows, residue = [], []
    for r in cia_norm.itertuples():
        key = str(getattr(r, "name_key", "") or "").upper()
        name = str(getattr(r, "entity_name", "") or "")
        case_id = (case_ids or {}).get(key) or f"cia:{key}"
        orgs, level = _lookup(key)
        if level == "chain_owner":
            residue.append({"case_id": case_id, "defendant_name": name, "name_key": key,
                            "reason": "chain_owner", "n_orgs": len(orgs)})
            continue
        if not orgs:
            # the compound line: try each component on its own (exact paths only)
            comps = party_components(name)
            hit_orgs: list[str] = []
            chain_n = 0
            for comp in comps:
                ck = norm_org_name(comp).upper()
                if not ck or ck == key:
                    continue
                o, lvl = _lookup(ck)
                if lvl == "chain_owner":
                    chain_n = max(chain_n, len(o))
                    continue
                hit_orgs += o
            orgs = sorted(set(hit_orgs))
            if orgs:
                level = "cia_component"
            elif chain_n:
                residue.append({"case_id": case_id, "defendant_name": name, "name_key": key,
                                "reason": "chain_owner", "n_orgs": chain_n})
                continue
        if not orgs:
            residue.append({"case_id": case_id, "defendant_name": name, "name_key": key,
                            "reason": ("manufacturer_not_enrolled" if is_manufacturer_name(key)
                                       else "no_exact_org_or_owner_match"), "n_orgs": 0})
            continue
        npis = sorted({n for o in orgs for n in members.get(o, [])})
        if not npis:
            residue.append({"case_id": case_id, "defendant_name": name, "name_key": key,
                            "reason": "org_without_member_npis", "n_orgs": len(orgs)})
            continue
        if len(npis) > int(max_npis):
            residue.append({"case_id": case_id, "defendant_name": name, "name_key": key,
                            "reason": "chain_org", "n_orgs": len(orgs), "n_npis": len(npis)})
            continue
        w = min(1.0, float(budget) / len(npis))
        org_of = {n: o for o in orgs for n in members.get(o, [])}
        for n in npis:
            rows.append({"case_id": case_id, "defendant_name": name, "npi": n, "org_node_id": org_of[n],
                         "status": "resolved", "match_level": level, "n_orgs_for_party": len(orgs),
                         "label_weight": w})
    res = pd.DataFrame(rows, columns=CIA_NPI_COLS)
    rsd = pd.DataFrame(residue, columns=["case_id", "defendant_name", "name_key", "reason", "n_orgs", "n_npis"])
    return res, rsd


def cia_report(cia_norm: pd.DataFrame, resolved: pd.DataFrame, residue: pd.DataFrame) -> str:
    n = len(cia_norm)
    parties_resolved = resolved["case_id"].nunique() if len(resolved) else 0
    L = ["# CIA PARTIES -> ENROLLED PROVIDERS", "",
         "_Every OIG Corporate Integrity Agreement is a resolved FCA matter. A party resolves by EXACT name "
         "to an enrolled organization or to a PECOS organization owner (then to every org it owns, chains "
         "excepted). No fuzzy matching. Leads for counsel review, never accusations._", "",
         f"- parties: {n:,}; resolved: {parties_resolved:,} ({(parties_resolved / n if n else 0):.0%}); "
         f"NPIs labelled: {len(resolved):,}; residue: {len(residue):,}", ""]
    if len(resolved):
        by = resolved.groupby("match_level")["case_id"].nunique()
        L.append("| match level | parties | NPIs |"); L.append("|---|--:|--:|")
        for lvl, cnt in by.items():
            L.append(f"| {lvl} | {cnt:,} | {int((resolved['match_level'] == lvl).sum()):,} |")
        L.append("")
        big = resolved.groupby(["case_id", "defendant_name"])["npi"].nunique().sort_values(ascending=False).head(15)
        L.append("Largest parties by NPIs (label_weight caps each at the per-case budget):"); L.append("")
        L.append("| party | NPIs |"); L.append("|---|--:|")
        for (cid, nm), c in big.items():
            L.append(f"| {nm[:70]} | {c:,} |")
        L.append("")
    if len(residue):
        L.append("Residue by reason:"); L.append("")
        for reason, c in residue["reason"].value_counts().items():
            L.append(f"- {reason}: {c:,}")
        L.append("")
        L.append("| party | state-less name key | reason |"); L.append("|---|---|---|")
        for r in residue.head(60).itertuples():
            L.append(f"| {str(r.defendant_name)[:70]} | {str(r.name_key)[:40]} | {r.reason} |")
    return "\n".join(L) + "\n"


# -------------------------------------------------------------------- CLI

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("affiliated", help="convicted individuals -> the billers whose claims carried them")
    a1.add_argument("--exclusion-nodes", required=True)
    a1.add_argument("--spending", required=True)
    a1.add_argument("--window-months", type=int, default=24)
    a1.add_argument("--min-share", type=float, default=0.05)
    a1.add_argument("--min-dollars", type=float, default=25_000.0)
    a1.add_argument("--max-orgs", type=int, default=1)
    a1.add_argument("--tier", default="hard", choices=["all", "hard", "hard+charged", "hard+weak"])
    a1.add_argument("--out", required=True, help="processed/affiliated_convictions.parquet")
    a2 = sub.add_parser("cia", help="CIA parties -> enrolled providers through exact name and ownership")
    a2.add_argument("--cia", required=True, help="preclean/cia/cia_list.csv")
    a2.add_argument("--graph-dir", required=True)
    a2.add_argument("--max-owned", type=int, default=150)
    a2.add_argument("--budget", type=int, default=100)
    a2.add_argument("--out-cases", required=True, help="enforcement/cia_cases.csv (case-DB schema)")
    a2.add_argument("--out-npis", required=True, help="processed/cia_defendant_npis.parquet")
    a3 = sub.add_parser("names", help="name-only exclusion rows -> NPIs, offline first (unique NPPES match in state)")
    a3.add_argument("--exclusion-nodes", required=True)
    a3.add_argument("--provider-dim", required=True, help="processed/provider_dim.parquet")
    a3.add_argument("--tier", default="hard", choices=["all", "hard", "hard+charged", "hard+weak"])
    a3.add_argument("--year-min", type=int, default=2015)
    a3.add_argument("--other-ids", default=None,
                    help="processed/nppes_other_ids.parquet (ccn_npi_crosswalk --other-ids-out): UPIN joins first")
    a3.add_argument("--out", required=True, help="processed/exclusion_name_resolution.parquet (every row + status)")
    a3.add_argument("--out-exclusions", default=None,
                    help="processed/exclusions_name_resolved.parquet (resolved rows in the exclusions schema; "
                         "the next graph build merges it)")
    a = ap.parse_args()

    if a.cmd == "names":
        nodes = pd.read_parquet(a.exclusion_nodes)
        dim = pd.read_parquet(a.provider_dim)
        oth = pd.read_parquet(a.other_ids) if a.other_ids else None
        fun = name_resolve_funnel(nodes, tier=a.tier, year_min=a.year_min)
        print("[label_expansion] names funnel: " + ", ".join(f"{k} {v}" for k, v in fun.items()))
        if fun.get("missing_columns"):
            print(f"[label_expansion] WARNING: the exclusion nodes lack {fun['missing_columns']}: the graph predates "
                  "the columns the resolver needs (integrate + a graph rebuild carry state / upin / zip since bundle 309)")
        res = exclusion_name_resolve(nodes, dim, tier=a.tier, year_min=a.year_min, other_ids=oth)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        res.to_parquet(a.out, index=False)
        amb = res[res["status"] == "ambiguous"] if len(res) else res
        amb.to_csv(Path(a.out).with_name("exclusion_name_residue.csv"), index=False)
        by = res["status"].value_counts().to_dict() if len(res) else {}
        print(f"[label_expansion] name-only {a.tier}-tier exclusion rows since {a.year_min}: {len(res):,} -> "
              + ", ".join(f"{k} {v:,}" for k, v in by.items()) + f" -> {a.out}; residue (ambiguous) -> "
              f"{Path(a.out).with_name('exclusion_name_residue.csv')}")
        if a.out_exclusions:
            ex = name_resolved_exclusions(res)
            ex.to_parquet(a.out_exclusions, index=False)
            print(f"[label_expansion] {len(ex):,} resolved rows in the exclusions schema -> {a.out_exclusions} "
                  "(rebuild the graph to use them)")
        return

    if a.cmd == "affiliated":
        nodes = pd.read_parquet(a.exclusion_nodes)
        aff = affiliated_convictions(nodes, a.spending, window_months=a.window_months, min_share=a.min_share,
                                     min_dollars=a.min_dollars, max_orgs=a.max_orgs, tier=a.tier)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        aff.to_parquet(a.out, index=False)
        kept = aff[aff["kept"].astype(bool)] if len(aff) else aff
        print(f"[label_expansion] affiliated convictions: {aff['excluded_npi'].nunique() if len(aff) else 0:,} "
              f"excluded individuals rendered under another biller; {len(kept):,} biller rows kept "
              f"({kept['npi'].nunique() if len(kept) else 0:,} billers), {len(aff) - len(kept):,} listed only -> {a.out}")
        md = Path(a.out).with_suffix(".md")
        L = ["# AFFILIATED CONVICTIONS", "",
             "_A convicted or excluded individual rarely bills under their own NPI; the biller whose claims carried "
             "them as the rendering provider in the months before the exclusion is the organization the case "
             "points at. Kept rows pass the per-individual budget; the rest are listed. Leads for counsel review, "
             "never accusations._", "",
             f"- individuals with a rendering history under another biller: {aff['excluded_npi'].nunique() if len(aff) else 0:,}",
             f"- kept biller rows: {len(kept):,} ({kept['npi'].nunique() if len(kept) else 0:,} distinct billers)",
             f"- listed only: {len(aff) - len(kept):,}" + (" (" + ", ".join(f"{k} {v:,}" for k, v in aff.loc[~aff['kept'].astype(bool), 'drop_reason'].value_counts().items()) + ")" if len(aff) else ""),
             ""]
        if len(kept):
            L += ["| share of biller | kept rows |", "|---|--:|"]
            bins = pd.cut(kept["share_of_biller"], [0, 0.05, 0.25, 0.5, 1.0], include_lowest=True)
            for b, c in bins.value_counts().sort_index().items():
                L.append(f"| {b} | {c:,} |")
        md.write_text("\n".join(L) + "\n", encoding="utf-8")
        print(f"[label_expansion] report -> {md}")
        return

    from src.enforcement.cia import normalize_cia, cia_case_rows
    from src.attempt_2.clean_data import read_csv_text
    raw = read_csv_text(a.cia)
    cia = normalize_cia(raw)
    g = Path(a.graph_dir)
    org_nodes = pd.read_parquet(g / "nodes" / "org_nodes.parquet")
    npi_to_org = pd.read_parquet(g / "npi_to_org.parquet")
    on_p, ob_p = g / "nodes" / "owner_nodes.parquet", g / "edges" / "owned_by_edges.parquet"
    owner_nodes = pd.read_parquet(on_p) if on_p.exists() else None
    owned_by = pd.read_parquet(ob_p) if ob_p.exists() else None
    cases = cia_case_rows(cia)
    cases["case_id"] = cases["case_id"].astype(str)
    case_ids = dict(zip(cases["defendant_name_key"].astype(str).str.upper(), cases["case_id"]))
    resolved, residue = cia_resolve(cia, org_nodes, owner_nodes, owned_by, npi_to_org,
                                    max_owned=a.max_owned, budget=a.budget, case_ids=case_ids)
    Path(a.out_cases).parent.mkdir(parents=True, exist_ok=True)
    cases.to_csv(a.out_cases, index=False)
    Path(a.out_npis).parent.mkdir(parents=True, exist_ok=True)
    resolved.to_parquet(a.out_npis, index=False)
    residue.to_csv(Path(a.out_npis).with_name("cia_residue.csv"), index=False)
    rep = Path(a.out_npis).with_name("CIA_RESOLUTION.md")
    rep.write_text(cia_report(cia, resolved, residue), encoding="utf-8")
    print(f"[label_expansion] CIA: {len(cia):,} parties -> {resolved['case_id'].nunique() if len(resolved) else 0:,} "
          f"resolved ({len(resolved):,} NPIs), {len(residue):,} residue -> {a.out_npis}; cases -> {a.out_cases}; {rep}")




# ------------------------------------------------- name-only exclusion rows

NAME_RES_COLS = ["source_node_id", "entity_name", "state", "excl_type", "excl_date", "fca_tier", "fca_basis",
                 "kind", "person_key", "npi", "match_level", "n_candidates", "candidate_npis", "status"]


def name_resolve_funnel(exclusion_nodes: pd.DataFrame, tier: str = "hard", year_min: int = 2015) -> dict:
    """Why the name resolver saw N rows: the count surviving each filter in order
    (rows, no NPI, with a name, with a state, dated, since year_min, kept tier) and
    the columns the nodes lack. The first real run read 0 rows on a graph built
    before the exclusion nodes carried `state`; this names the stage that emptied."""
    from .fca_relevance import classify_series, tier_mask
    e = exclusion_nodes
    need = ["npi", "entity_name", "state", "excl_date", "excl_type", "upin"]
    missing = [c for c in need if c not in e.columns]
    f: dict = {"rows": int(len(e)), "missing_columns": missing}
    npi = e.get("npi", pd.Series("", index=e.index)).astype("string").fillna("").str.strip()
    m = (npi == "") | (npi.str.lower() == "none")
    f["no_npi"] = int(m.sum())
    name = e.get("entity_name", pd.Series("", index=e.index)).fillna("").astype(str).str.strip()
    m &= name != ""
    f["with_name"] = int(m.sum())
    st = e.get("state", pd.Series("", index=e.index)).fillna("").astype(str).str.strip()
    m &= st != ""
    f["with_state"] = int(m.sum())
    dt = pd.to_datetime(e.get("excl_date", pd.Series(None, index=e.index)), errors="coerce")
    m &= dt.notna()
    f["dated"] = int(m.sum())
    m &= dt.dt.year >= int(year_min)
    f[f"since_{year_min}"] = int(m.sum())
    if m.any():
        tiers = classify_series(e.loc[m, "excl_type"].fillna("").astype(str)) if "excl_type" in e.columns \
            else pd.DataFrame({"fca_tier": pd.Series("weak", index=e.index[m])})
        f[f"tier_{tier}"] = int(tier_mask(tiers["fca_tier"], tier).sum())
    else:
        f[f"tier_{tier}"] = 0
    return f


def exclusion_name_resolve(exclusion_nodes: pd.DataFrame, provider_dim: pd.DataFrame,
                           tier: str = "hard", year_min: int = 2015,
                           other_ids: pd.DataFrame | None = None) -> pd.DataFrame:
    """The audit's biggest identifier gap (LEIE 66,081 name-only rows, SAM 102,232,
    state lists 3,795): a kept-tier exclusion record with NO NPI, a name and a
    state, dated ``year_min`` or later, is resolved OFFLINE first against NPPES
    (provider_dim) by the same key the defendant resolver uses: FIRST LAST for a
    person, the normalized legal name for a business, within the record's state.
    Exactly one NPPES record = resolved (``excl_name_state_unique`` /
    ``excl_org_state_unique``); several = ``ambiguous`` (the Manus residue, with the
    candidate NPIs listed so the agent settles it on specialty and address); none
    = ``no_match``.  No fuzzy matching; a name alone never becomes a label here."""
    from .fca_relevance import classify_series, tier_mask
    from src.enforcement.defendant_resolver import _index_nppes, _nppes_person_key
    e = exclusion_nodes.copy()
    npi = e.get("npi").astype("string").fillna("").str.strip()
    e = e[(npi == "") | (npi.str.lower() == "none")].copy()
    e["entity_name"] = e.get("entity_name", "").fillna("").astype(str).str.strip()
    e["state"] = e.get("state", pd.Series("", index=e.index)).fillna("").astype(str).str.upper().str.strip()
    e["_dt"] = pd.to_datetime(e.get("excl_date"), errors="coerce")
    e = e[(e["entity_name"] != "") & (e["state"] != "") & e["_dt"].notna() & (e["_dt"].dt.year >= int(year_min))].copy()
    if not len(e):
        return pd.DataFrame(columns=NAME_RES_COLS)
    e["excl_type"] = e.get("excl_type", "").fillna("").astype(str)
    tiers = classify_series(e["excl_type"])
    e["fca_tier"] = tiers["fca_tier"].values
    e["fca_basis"] = tiers["fca_basis"].values
    e = e[tier_mask(e["fca_tier"], tier).values].copy()
    if not len(e):
        return pd.DataFrame(columns=NAME_RES_COLS)
    is_person = e["entity_name"].str.contains(",")
    e["kind"] = is_person.map({True: "person", False: "org"})
    e["person_key"] = ""
    e.loc[is_person, "person_key"] = _nppes_person_key(e.loc[is_person, "entity_name"]).values
    e.loc[~is_person, "person_key"] = e.loc[~is_person, "entity_name"].map(norm_org_name).str.upper().values
    pkeys = set(e.loc[is_person, "person_key"]) - {""}
    okeys = set(e.loc[~is_person, "person_key"]) - {""}
    states = set(e["state"]) - {""}
    people, orgs, _ = _index_nppes(provider_dim, pkeys, okeys, states)
    # UPIN first (a HARD identifier): the LEIE's pre-NPI rows carry the Medicare UPIN
    # and NPPES records it as Other Provider Identifier type 02 (ccn_npi_crosswalk
    # --other-ids-out); one NPI per UPIN resolves without any name logic
    upin_to_npi: dict[str, list[str]] = {}
    if other_ids is not None and len(other_ids):
        u = other_ids[other_ids["id_type"].astype(str) == "upin"]
        for ident, n in zip(u["identifier"].astype(str).str.upper(), u["npi"].astype(str)):
            upin_to_npi.setdefault(ident, []).append(n)
    upins = e.get("upin", pd.Series("", index=e.index)).fillna("").astype(str).str.upper().str.strip()
    rows = []
    for r, upin in zip(e.itertuples(), upins):
        hits = sorted(set(upin_to_npi.get(upin, []))) if upin else []
        if len(hits) == 1:
            rows.append({"npi": hits[0], "match_level": "excl_upin", "n_candidates": 1,
                         "candidate_npis": hits[0], "status": "resolved"})
            continue
        key = r.person_key
        if not key:
            rows.append({"npi": "", "match_level": "", "n_candidates": 0, "candidate_npis": "", "status": "no_key"})
            continue
        cands = (people if r.kind == "person" else orgs).get((key, r.state), [])
        npis = sorted({c[0] for c in cands})
        if len(npis) == 1:
            rows.append({"npi": npis[0], "match_level": "excl_name_state_unique" if r.kind == "person" else "excl_org_state_unique",
                         "n_candidates": 1, "candidate_npis": npis[0], "status": "resolved"})
        elif len(npis) > 1:
            rows.append({"npi": "", "match_level": "", "n_candidates": len(npis), "candidate_npis": ";".join(npis[:12]),
                         "status": "ambiguous"})
        else:
            rows.append({"npi": "", "match_level": "", "n_candidates": 0, "candidate_npis": "", "status": "no_match"})
    res = pd.DataFrame(rows, index=e.index)
    out = pd.DataFrame({"source_node_id": e.get("node_id", pd.Series("", index=e.index)).astype(str),
                        "entity_name": e["entity_name"], "state": e["state"], "excl_type": e["excl_type"],
                        "excl_date": e["_dt"].dt.strftime("%Y-%m-%d"), "fca_tier": e["fca_tier"],
                        "fca_basis": e["fca_basis"], "kind": e["kind"], "person_key": e["person_key"]})
    out = pd.concat([out, res], axis=1)
    return out[NAME_RES_COLS].reset_index(drop=True)


def name_resolved_exclusions(res: pd.DataFrame) -> pd.DataFrame:
    """The resolved rows in the exclusions schema (processed/exclusions_name_resolved.parquet):
    the next graph build merges every processed/exclusions_*.parquet, so these rows become
    exclusion nodes WITH an NPI, carrying their original statute (the tier survives)."""
    from src.enforcement.opensanctions import EXCLUSION_COLS
    k = res[res["status"] == "resolved"].copy() if len(res) else res
    if not len(k):
        return pd.DataFrame(columns=EXCLUSION_COLS + ["match_level", "source_node_id"])
    out = pd.DataFrame({"npi": k["npi"].astype(str), "entity_name": k["entity_name"],
                        "name_key": k["entity_name"].map(norm_org_name),
                        "excl_type": k["excl_type"], "excl_date": pd.to_datetime(k["excl_date"], errors="coerce"),
                        "reinstate_date": pd.NaT, "currently_active": 1,
                        "match_level": k["match_level"], "source_node_id": k["source_node_id"]})
    return out.drop_duplicates(["npi", "excl_type", "excl_date"]).reset_index(drop=True)


if __name__ == "__main__":
    main()
