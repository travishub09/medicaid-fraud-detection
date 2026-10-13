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


def cia_resolve(cia_norm: pd.DataFrame, org_nodes: pd.DataFrame, owner_nodes: pd.DataFrame | None,
                owned_by_edges: pd.DataFrame | None, npi_to_org: pd.DataFrame,
                max_owned: int = 150, budget: int = 100,
                case_ids: dict[str, str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """CIA parties -> NPIs.  Returns (resolved rows in the defendant-resolver
    shape, the residue).  Two exact paths, no fuzzy matching:

      cia_exact_org    the party's name key equals an org node's name key
      cia_owner        the party's name key equals a PECOS organization owner's
                       name; every org that owner owns resolves (an owner of more
                       than ``max_owned`` orgs is a chain: listed in the residue
                       as ``chain_owner``, never broadcast)

    ``label_weight`` = min(1, budget / NPIs for the party), the same per-case
    budget case_labels applies to broadcast rows."""
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

    rows, residue = [], []
    for r in cia_norm.itertuples():
        key = str(getattr(r, "name_key", "") or "").upper()
        name = str(getattr(r, "entity_name", "") or "")
        case_id = (case_ids or {}).get(key) or f"cia:{key}"
        orgs: list[str] = []
        level = ""
        if key and key in org_by_key:
            orgs, level = org_by_key[key], "cia_exact_org"
        elif key and key in owner_idx:
            for oid in owner_idx[key]:
                orgs += owned.get(oid, [])
            orgs = sorted(set(orgs))
            level = "cia_owner"
            if len(orgs) > int(max_owned):
                residue.append({"case_id": case_id, "defendant_name": name, "name_key": key,
                                "reason": "chain_owner", "n_orgs": len(orgs)})
                continue
        if not orgs:
            residue.append({"case_id": case_id, "defendant_name": name, "name_key": key,
                            "reason": "no_exact_org_or_owner_match", "n_orgs": 0})
            continue
        npis = sorted({n for o in orgs for n in members.get(o, [])})
        if not npis:
            residue.append({"case_id": case_id, "defendant_name": name, "name_key": key,
                            "reason": "org_without_member_npis", "n_orgs": len(orgs)})
            continue
        w = min(1.0, float(budget) / len(npis))
        org_of = {n: o for o in orgs for n in members.get(o, [])}
        for n in npis:
            rows.append({"case_id": case_id, "defendant_name": name, "npi": n, "org_node_id": org_of[n],
                         "status": "resolved", "match_level": level, "n_orgs_for_party": len(orgs),
                         "label_weight": w})
    res = pd.DataFrame(rows, columns=CIA_NPI_COLS)
    rsd = pd.DataFrame(residue, columns=["case_id", "defendant_name", "name_key", "reason", "n_orgs"])
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
    a1.add_argument("--tier", default="hard", choices=["all", "hard", "hard+weak"])
    a1.add_argument("--out", required=True, help="processed/affiliated_convictions.parquet")
    a2 = sub.add_parser("cia", help="CIA parties -> enrolled providers through exact name and ownership")
    a2.add_argument("--cia", required=True, help="preclean/cia/cia_list.csv")
    a2.add_argument("--graph-dir", required=True)
    a2.add_argument("--max-owned", type=int, default=150)
    a2.add_argument("--budget", type=int, default=100)
    a2.add_argument("--out-cases", required=True, help="enforcement/cia_cases.csv (case-DB schema)")
    a2.add_argument("--out-npis", required=True, help="processed/cia_defendant_npis.parquet")
    a = ap.parse_args()

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


if __name__ == "__main__":
    main()
