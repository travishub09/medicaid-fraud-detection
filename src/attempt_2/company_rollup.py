"""
company_rollup.py  (attempt 2) — consolidate per-NPI leads to the owning company

Leads are per-NPI, but one company is split across many NPIs (subparts / facilities).
This rolls every billing NPI in the FULL provider base up to a company and aggregates
dollars + signals, so a company that only crosses $10M when consolidated — and was
therefore invisible in the per-NPI leads file — finally surfaces.

Company linkage is tiered, most-reliable first; each NPI lands in exactly one company:
  1. pac_id       — NPIs sharing a PECOS_ASCT_CNTL_ID are the same enrolled entity (subparts).
  2. shared_owner — non-PAC NPIs whose facilities share a common owner (owner_edges).
  3. name         — EXACT normalized org-name match (fallback). Multi-state name merges
                    are kept but flagged low_confidence for manual review.
  else            — the NPI is its own single-NPI company.

net_paid is TOTAL billing — a SIZE proxy, NOT an adjudicated case value. Read-only on
inputs; writes new files only. Assertions hard-fail (dollar conservation, one-company-per-NPI).

Run:
    python -m src.attempt_2.company_rollup --min-net-paid 10000000
"""

import argparse
import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from .clean_data import PRECLEAN_DIR

LEGAL_SUFFIX = re.compile(
    r"\b(INC|INCORPORATED|LLC|CORP|CORPORATION|CO|COMPANY|PC|PA|LTD|LP|LLP)\b")


def log(m: str) -> None:
    print(m, flush=True)


def require(name: str, ok: bool, detail: str = "") -> None:
    log(f"    [assert {'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        raise AssertionError(f"ASSERTION FAILED: {name} — {detail}")


def norm_company(s: str) -> str:
    """Exact-match company-name key: upper, strip punctuation, drop leading THE,
    strip legal suffixes (INC/LLC/CORP/CO/PC/PA/LTD/LP/LLP), collapse whitespace."""
    s = re.sub(r"[^A-Z0-9 ]", " ", str(s or "").upper())
    s = re.sub(r"^THE ", "", s)
    s = LEGAL_SUFFIX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _find(name: str, data: Path) -> Path | None:
    for root in [data / "integrated", data / "features", data / "detection", data]:
        if (root / name).exists():
            return root / name
    hits = sorted(data.rglob(name))
    return hits[0] if hits else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-net-paid", type=float, default=10_000_000.0)
    args = ap.parse_args()
    thr = args.min_net_paid
    data = PRECLEAN_DIR.parent
    out_dir = data / "detection"
    out_dir.mkdir(parents=True, exist_ok=True)

    pf = _find("provider_features.parquet", data)
    xw = _find("npi_xwalk.parquet", data)
    oe = _find("owner_edges.parquet", data)
    v3 = _find("fraud_leads_v3.parquet", data)
    if pf is None or v3 is None:
        raise FileNotFoundError("provider_features.parquet and fraud_leads_v3.parquet are required")
    log(f"Base : {pf}\nxwalk: {xw}\nowner: {oe}\nleads: {v3}")
    con = duckdb.connect()

    # ---- FULL provider base (every NPI) ----
    df = con.execute(f"""
        SELECT npi, CAST(net_paid AS DOUBLE) net_paid, CAST(gross_paid AS DOUBLE) gross_paid,
               entity_type, primary_taxonomy, COALESCE(org_legal_name,'') org_legal_name,
               COALESCE(practice_state,'') practice_state
        FROM read_parquet('{pf}')
    """).df()
    df["npi"] = df["npi"].astype(str)
    n0 = len(df)
    require("base_one_row_per_npi", df["npi"].is_unique, f"{n0:,}")
    total_paid_base = float(df["net_paid"].fillna(0).sum())

    # ---- linkage maps ----
    pac = {}
    if xw is not None:
        p = con.execute(f"""SELECT npi, MIN(pac_id) pac FROM read_parquet('{xw}')
                            WHERE COALESCE(pac_id,'')<>'' GROUP BY npi""").df()
        pac = dict(zip(p["npi"].astype(str), p["pac"].astype(str)))
    owner = {}
    if oe is not None:
        o = con.execute(f"""
            SELECT facility_npi AS npi,
                   MIN(COALESCE(NULLIF(owner_npi,''), owner_name_key)) AS okey
            FROM read_parquet('{oe}')
            WHERE COALESCE(facility_npi,'')<>''
              AND COALESCE(NULLIF(owner_npi,''), owner_name_key) IS NOT NULL
              AND COALESCE(NULLIF(owner_npi,''), owner_name_key)<>''
            GROUP BY facility_npi""").df()
        owner = dict(zip(o["npi"].astype(str), o["okey"].astype(str)))

    # owner tier only counts where >=2 NON-PAC NPIs share an owner (true sharing)
    non_pac = ~df["npi"].isin(pac)
    df["_owner"] = df["npi"].map(owner).where(non_pac)
    owner_grp = df.loc[df["_owner"].notna(), "_owner"].value_counts()
    shared_owners = set(owner_grp[owner_grp >= 2].index)
    df["name_key"] = df["org_legal_name"].map(norm_company)

    # ---- assign each NPI to exactly one company key (precedence) ----
    pac_key = df["npi"].map(pac)
    owner_key = df["_owner"].where(df["_owner"].isin(shared_owners))
    name_ok = (~df["npi"].isin(pac)) & owner_key.isna() & (df["name_key"] != "")
    df["company_id"] = np.where(
        pac_key.notna(), "pac:" + pac_key.astype(str),
        np.where(owner_key.notna(), "owner:" + owner_key.astype(str),
                 np.where(name_ok, "name:" + df["name_key"],
                          "npi:" + df["npi"])))
    df["_basis_raw"] = np.where(
        pac_key.notna(), "pac_id",
        np.where(owner_key.notna(), "shared_owner",
                 np.where(name_ok, "name", "single")))

    # ---- per-NPI signals from v3 ----
    sig = con.execute(f"""
        SELECT npi, provider_on_leie, billed_after_exclusion,
               CAST(anomaly_score_v3 AS DOUBLE) anomaly_score_v3,
               CAST(n_concept_signals AS INTEGER) n_concept_signals,
               layer3_probable_owner, priority_rank, priority_tier
        FROM read_parquet('{v3}')""").df()
    sig["npi"] = sig["npi"].astype(str)
    df = df.merge(sig, on="npi", how="left")
    require("no_fanout_signal_merge", len(df) == n0, f"{len(df):,} vs {n0:,}")
    for b in ["provider_on_leie", "billed_after_exclusion", "layer3_probable_owner"]:
        df[b] = df[b].fillna(False).astype(bool)
    df["priority_rank"] = df["priority_rank"].fillna(6).astype(int)

    # ---- npi -> company map (audit trail) ----
    npimap = df[["npi", "company_id", "net_paid", "_basis_raw"]].rename(
        columns={"_basis_raw": "merge_basis_raw"})
    npimap.to_parquet(out_dir / "npi_to_company_map.parquet", index=False)

    # ---- aggregate to company ----
    rank_to_tier = dict(df[["priority_rank", "priority_tier"]].dropna().drop_duplicates().values)
    rank_to_tier.setdefault(6, "6_none")

    def agg(g: pd.DataFrame) -> pd.Series:
        nplist = "; ".join(f"{r.npi}:{r.net_paid:,.0f}" for r in
                           g.sort_values("net_paid", ascending=False).itertuples())
        best_rank = int(g["priority_rank"].min())
        states = sorted(s for s in g["practice_state"].unique() if s)
        basis_raw = g["_basis_raw"].iloc[0]
        n = len(g)
        basis = basis_raw if n >= 2 else "single"
        conf = ("high" if basis in ("pac_id", "shared_owner")
                else ("single" if basis == "single"
                      else ("medium" if len(states) <= 1 else "low")))
        return pd.Series({
            "company_net_paid": g["net_paid"].fillna(0).sum(),
            "company_gross_paid": g["gross_paid"].fillna(0).sum(),
            "npi_count": n,
            "max_constituent_net_paid": g["net_paid"].max(),
            "states": "; ".join(states),
            "n_states": len(states),
            "primary_taxonomies": "; ".join(sorted({t for t in g["primary_taxonomy"].dropna() if t}))[:200],
            "entity_types": "; ".join(sorted({e for e in g["entity_type"].dropna() if e})),
            "company_name": (g.loc[g["org_legal_name"] != "", "org_legal_name"].iloc[0]
                             if (g["org_legal_name"] != "").any() else ""),
            "merge_basis": basis,
            "merge_confidence": conf,
            "any_provider_on_leie": bool(g["provider_on_leie"].any()),
            "any_billed_after_exclusion": bool(g["billed_after_exclusion"].any()),
            "max_anomaly_score_v3": g["anomaly_score_v3"].max(),
            "max_n_concept_signals": int(g["n_concept_signals"].fillna(0).max()),
            "any_probable_excluded_owner": bool(g["layer3_probable_owner"].any()),
            "best_priority_rank": best_rank,
            "best_priority_tier": rank_to_tier.get(best_rank, "6_none"),
            "flagged": best_rank <= 5,
            "npi_list": nplist,
        })

    comp = df.groupby("company_id", sort=False).apply(agg, include_groups=False).reset_index()

    # ---- integrity ----
    require("every_npi_one_company", int(comp["npi_count"].sum()) == n0,
            f"{int(comp['npi_count'].sum()):,} vs {n0:,}")
    require("npimap_complete", npimap["npi"].nunique() == n0 and len(npimap) == n0)
    total_paid_comp = float(comp["company_net_paid"].sum())
    require("dollar_conservation",
            abs(total_paid_comp - total_paid_base) <= max(1.0, 1e-9 * abs(total_paid_base)),
            f"companies=${total_paid_comp:,.2f} base=${total_paid_base:,.2f}")

    comp.to_parquet(out_dir / "company_rollup.parquet", index=False)

    # ---- company leads CSV (>= threshold AND flagged) ----
    leads = comp[(comp["company_net_paid"] >= thr) & comp["flagged"]].copy()
    leads = leads.sort_values(["best_priority_rank", "company_net_paid"], ascending=[True, False])
    leads_csv = leads.assign(
        company_total_billing_size_proxy_not_case_value=leads["company_net_paid"].round(0)
    )[["company_id", "company_name", "best_priority_tier",
       "company_total_billing_size_proxy_not_case_value", "npi_count", "states",
       "merge_basis", "merge_confidence", "any_provider_on_leie", "any_billed_after_exclusion",
       "max_anomaly_score_v3", "max_n_concept_signals", "any_probable_excluded_owner",
       "primary_taxonomies", "npi_list"]]
    con.register("leads_csv", leads_csv)
    con.execute(f"""COPY (SELECT * FROM leads_csv)
                    TO '{out_dir / 'company_leads_over_10m.csv'}'
                    (FORMAT CSV, HEADER, QUOTE '"', FORCE_QUOTE (company_id, npi_list))""")

    write_report(comp, leads, thr, total_paid_base, total_paid_comp, n0, out_dir)
    con.close()
    log("Done.")


def write_report(comp, leads, thr, base_sum, comp_sum, n0, out_dir):
    log("Writing COMPANY_ROLLUP_REPORT.md …")
    multi = comp[comp["npi_count"] >= 2]
    low_conf = comp[(comp["merge_confidence"] == "low")]
    newly = leads[leads["max_constituent_net_paid"] < thr]   # company>=10M, no single NPI >=10M

    r = ["# COMPANY_ROLLUP_REPORT — attempt_2\n",
         "_Per-NPI leads consolidated to the owning company. net_paid = total billing (size proxy, "
         "NOT case value). Read-only; new files only._\n"]
    r.append("\n## Dollar conservation & coverage\n"
             f"- base SUM(net_paid) = ${base_sum:,.2f}; rolled-up SUM(company_net_paid) = "
             f"${comp_sum:,.2f} → {'RECONCILED ✓' if abs(base_sum-comp_sum)<=max(1.0,1e-9*base_sum) else 'MISMATCH ✗'}\n"
             f"- NPIs: {n0:,} → companies: {len(comp):,} ({len(multi):,} multi-NPI)\n")
    r.append("\n## Merge basis (companies)\n| basis | companies | multi-NPI |\n|---|--:|--:|\n")
    for b in ["pac_id", "shared_owner", "name", "single"]:
        sub = comp[comp["merge_basis"] == b]
        r.append(f"| {b} | {len(sub):,} | {int((sub['npi_count']>=2).sum()):,} |\n")
    r.append(f"\n- low-confidence companies (multi-state exact-name merges — manual review): "
             f"**{len(low_conf):,}**\n")

    r.append(f"\n## Newly surfaced companies (cross $10M only when consolidated)\n"
             f"- Companies with company_net_paid ≥ ${thr:,.0f} AND a flagged NPI where NO single "
             f"constituent NPI reached ${thr:,.0f}: **{len(newly):,}** — these are leads the per-NPI "
             f"filter missed.\n")
    r.append("\n### Top 25 newly-surfaced companies\n"
             "| company | tier | company_net_paid | npis | max single NPI | basis/conf |\n"
             "|---|---|--:|--:|--:|---|\n")
    for x in newly.sort_values("company_net_paid", ascending=False).head(25).itertuples():
        r.append(f"| {(x.company_name or x.company_id)[:34]} | {x.best_priority_tier} | "
                 f"${x.company_net_paid:,.0f} | {x.npi_count} | ${x.max_constituent_net_paid:,.0f} | "
                 f"{x.merge_basis}/{x.merge_confidence} |\n")

    r.append("\n## Top 25 company leads overall\n"
             "| company | tier | company_net_paid | npis | signals | basis/conf |\n"
             "|---|---|--:|--:|---|---|\n")
    for x in leads.head(25).itertuples():
        sigs = []
        if x.any_billed_after_exclusion: sigs.append("billed_after_exclusion")
        if x.any_provider_on_leie: sigs.append("on_LEIE")
        if x.max_n_concept_signals: sigs.append(f"L2_concepts={x.max_n_concept_signals}")
        if x.any_probable_excluded_owner: sigs.append("probable_owner")
        r.append(f"| {(x.company_name or x.company_id)[:34]} | {x.best_priority_tier} | "
                 f"${x.company_net_paid:,.0f} | {x.npi_count} | {'; '.join(sigs)[:60]} | "
                 f"{x.merge_basis}/{x.merge_confidence} |\n")

    r.append("\n## Integrity\n"
             f"- every NPI in exactly one company ({n0:,}); dollar conservation asserted; "
             "constituent NPIs + per-NPI net_paid preserved (npi_list + npi_to_company_map.parquet).\n"
             "- linkage: PAC > shared-owner > exact normalized name (multi-state name = low conf); "
             "identifiers as strings; no inputs modified.\n")
    (out_dir / "COMPANY_ROLLUP_REPORT.md").write_text("".join(r))


if __name__ == "__main__":
    main()
