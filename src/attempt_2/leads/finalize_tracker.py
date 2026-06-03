"""
finalize_tracker.py  (attempt 2) — clean & finalize the company lead tracker for triage

CLEANUP ONLY — no detection re-run, no re-rollup, no score changes. Takes the ranked
company leads and produces polished, triage-ready files:
  1. resolve company names (no raw `npi:` ids; fall back to "UNKNOWN NAME (NPI …)")
  2. add a human-readable `specialty` for the dominant taxonomy
  3. fix `fragmentation_signal` — keep TRUE only when billing is genuinely distributed
     (max single constituent NPI < frag_max_share * company_net_paid)
  4. flag likely under-merges (`possible_same_operator` + `related_entities`)
  5. consolidate caveats into one `review_flags` column
  6. rewrite `reasons` as a complete, human-readable sentence
  7. add `rank`, `tier_label`, reorder columns triage-first

Outputs: company_leads_clean.csv (all), triage_priority.csv (Direct + Company anomaly),
probable_owner_backlog.csv (probable-owner tier alone). Read-only inputs; new files only.

Run:
    python -m src.attempt_2.leads.finalize_tracker
"""

import argparse
import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

pd.set_option("future.no_silent_downcasting", True)

from ..clean_data import PRECLEAN_DIR, _TAXONOMY_SEED

TIER_LABEL = {
    "1_billed_after_exclusion": "Direct — billed while excluded",
    "2_on_leie": "Direct — on LEIE",
    "3_company_anomaly": "Company anomaly",
    "4_probable_owner": "Probable excluded owner",
}
_SUFFIX = re.compile(r"\b(INC|INCORPORATED|LLC|CORP|CORPORATION|CO|COMPANY|PC|PA|LTD|LP|LLP)\b")


def log(m: str) -> None:
    print(m, flush=True)


def require(name: str, ok: bool, detail: str = "") -> None:
    log(f"    [assert {'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        raise AssertionError(f"ASSERTION FAILED: {name} — {detail}")


def name_root(s: str) -> str:
    """Normalized name root for under-merge clustering: upper, strip punctuation,
    drop leading THE + legal suffixes, then the first 3 significant tokens (so
    'NEW LIFE WELLNESS CENTER' and 'NEW LIFE WELLNESS FLAGSTAFF' share a root)."""
    s = re.sub(r"[^A-Z0-9 ]", " ", str(s or "").upper())
    s = re.sub(r"^THE ", "", s)
    s = _SUFFIX.sub(" ", s)
    toks = re.sub(r"\s+", " ", s).strip().split()
    return " ".join(toks[:3]) if len(toks) >= 3 else " ".join(toks)


def specialty_of(code: str) -> str:
    code = (code or "").strip()
    if not code:
        return ""
    return _TAXONOMY_SEED.get(code, f"{code} (taxonomy code)")


def clean_reason(r) -> str:
    """Complete, human-readable sentence of why the lead surfaced."""
    concepts = [m.split("(")[0] for m in str(r.contributing_concepts or "").split(";")
                if m.strip() and "context" not in m]
    concepts = [c.strip() for c in concepts if c.strip()]
    parts = []
    if r.priority_tier == "1_billed_after_exclusion":
        parts.append("Billed while excluded — a constituent NPI billed on or after its OIG LEIE "
                     f"exclusion date (${float(r.paid_after or 0):,.0f} billed after exclusion)")
    elif r.priority_tier == "2_on_leie":
        parts.append("A constituent NPI is on the OIG LEIE exclusion list")
    elif r.priority_tier == "3_company_anomaly":
        parts.append(f"Company-level billing anomaly vs. company peers (score "
                     f"{float(r.company_anomaly_score or 0):.2f})")
    elif r.priority_tier == "4_probable_owner":
        parts.append("A facility has a probable excluded owner (low-confidence name match)")
    if concepts and r.priority_tier != "1_billed_after_exclusion":
        parts.append("company anomaly: " + " + ".join(concepts) + " at ≥99th percentile vs peers")
    if bool(r.genuine_fragmentation):
        parts.append("billing is genuinely fragmented across NPIs (possible split-billing — "
                     "no single NPI was flagged, but the company is extreme consolidated)")
    if bool(r.any_provider_on_leie) and not r.priority_tier.startswith(("1", "2")):
        parts.append("also has an NPI on the OIG LEIE list")
    if bool(r.any_probable_excluded_owner) and r.priority_tier != "4_probable_owner":
        parts.append("also has a probable excluded owner")
    return ". ".join(parts) + "."


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frag-max-share", type=float, default=0.50)
    args = ap.parse_args()
    data = PRECLEAN_DIR.parent
    out_dir = data / "detection"

    # input: prefer the "Final leads" copy (what the user pointed at), else detection/
    tracker = next((p for p in [data / "Final leads" / "company_lead_tracker.csv",
                                data / "Final leads" / "company_lead_tracker.parquet",
                                out_dir / "company_lead_tracker.parquet",
                                out_dir / "company_lead_tracker.csv"]
                    if p.exists()), None)
    if tracker is None:
        raise FileNotFoundError("company_lead_tracker.{csv,parquet} not found (Final leads/ or detection/)")
    npimap = next(p for p in [out_dir / "npi_to_company_map.parquet",
                              *data.rglob("npi_to_company_map.parquet")] if p.exists())
    pf = data / "features" / "provider_features.parquet"
    pdim = next((p for p in [data / "integrated" / "provider_dim.parquet",
                             *data.rglob("provider_dim.parquet")] if p.exists()), None)
    log(f"tracker={tracker}\nnpimap={npimap}\nprovider_dim={pdim}")

    con = duckdb.connect()
    rdr = "read_csv_auto" if str(tracker).endswith(".csv") else "read_parquet"
    df = con.execute(f"SELECT * FROM {rdr}('{tracker}')").df()
    df["company_id"] = df["company_id"].astype(str)
    n0 = len(df)
    require("tracker_one_row_per_company", df["company_id"].is_unique, f"{n0:,}")
    df = df.rename(columns={"company_total_billing_size_proxy_not_case_value": "company_net_paid"})
    for b in ["any_billed_after_exclusion", "any_provider_on_leie", "any_probable_excluded_owner",
              "fragmentation_signal"]:
        df[b] = df[b].astype(str).str.lower().isin(["true", "1"])

    # ---- per-company lookups (read-only): top NPI, max constituent $, dominant taxonomy, name ----
    name_src = (f"LEFT JOIN read_parquet('{pdim}') pd ON mx.top_npi = pd.npi" if pdim else "")
    name_col = "pd.provider_name" if pdim else "NULL"
    look = con.execute(f"""
        WITH m AS (SELECT npi, company_id, net_paid FROM read_parquet('{npimap}')),
        mx AS (SELECT company_id, MAX(net_paid) max_constituent_net_paid,
                      arg_max(npi, net_paid) top_npi FROM m GROUP BY 1),
        tax AS (SELECT company_id, arg_max(tax, p) dominant_taxonomy FROM
                  (SELECT m.company_id, pf.primary_taxonomy tax, SUM(m.net_paid) p
                   FROM m JOIN read_parquet('{pf}') pf ON m.npi = pf.npi
                   WHERE COALESCE(pf.primary_taxonomy,'') <> '' GROUP BY 1,2) GROUP BY 1)
        SELECT mx.company_id, mx.max_constituent_net_paid, mx.top_npi, tax.dominant_taxonomy,
               COALESCE(NULLIF({name_col}, ''), NULLIF(pf2.org_legal_name, '')) AS resolved_name
        FROM mx LEFT JOIN tax USING (company_id)
        {name_src}
        LEFT JOIN read_parquet('{pf}') pf2 ON mx.top_npi = pf2.npi
    """).df()
    look["company_id"] = look["company_id"].astype(str)
    look["top_npi"] = look["top_npi"].astype(str)
    df = df.merge(look, on="company_id", how="left")
    require("no_fanout_lookup", len(df) == n0, f"{len(df):,} vs {n0:,}")

    # ---- 1. resolve company names (never leave a raw npi: id) ----
    cur = df["company_name"].fillna("").astype(str).str.strip()
    bad = (cur == "") | cur.str.startswith("npi:")
    resolved = df["resolved_name"].fillna("").astype(str).str.strip()
    df["name_unresolved"] = bad & (resolved == "")
    df["company_name"] = np.where(~bad, cur,
                          np.where(resolved != "", resolved,
                                   "UNKNOWN NAME (NPI " + df["top_npi"].astype(str) + ")"))
    n_resolved = int((bad & (resolved != "")).sum())

    # ---- 2. specialty ----
    df["specialty"] = df["dominant_taxonomy"].map(specialty_of)

    # ---- 3. fix fragmentation: keep TRUE only when billing is genuinely distributed ----
    share = df["max_constituent_net_paid"] / df["company_net_paid"].replace(0, np.nan)
    df["genuine_fragmentation"] = df["fragmentation_signal"] & (share < args.frag_max_share)
    n_frag_in = int(df["fragmentation_signal"].sum())
    n_frag_kept = int(df["genuine_fragmentation"].sum())

    # ---- 4. likely under-merges: same name root + same state across distinct company_ids ----
    df["_root"] = df["company_name"].map(name_root)
    df["_key"] = df["_root"] + "||" + df["states"].fillna("").astype(str)
    valid = (df["_root"].str.len() > 0) & (~df["name_unresolved"])
    grp = df[valid].groupby("_key")["company_id"].transform("nunique")
    df["possible_same_operator"] = False
    df.loc[valid, "possible_same_operator"] = grp >= 2
    rel = {}
    for key, g in df[df["possible_same_operator"]].groupby("_key"):
        names = list(zip(g["company_id"], g["company_name"]))
        for cid, _ in names:
            rel[cid] = "; ".join(f"{nm} ({c})" for c, nm in names if c != cid)
    df["related_entities"] = df["company_id"].map(rel).fillna("")
    n_clusters = df[df["possible_same_operator"]]["_key"].nunique()

    # ---- 5. consolidated review flags ----
    def flags(r):
        f = []
        if str(r.merge_confidence) == "low": f.append("low_merge_confidence")
        if r.possible_same_operator: f.append("possible_same_operator")
        if r.genuine_fragmentation: f.append("genuine_fragmentation")
        if r.name_unresolved: f.append("name_unresolved")
        return "; ".join(f)
    df["review_flags"] = df.apply(flags, axis=1)

    # ---- 6. clean reasons ----
    df["reasons"] = df.apply(clean_reason, axis=1)

    # ---- 7. rank, tier_label, reorder ----
    df["priority_rank"] = df["priority_tier"].str.slice(0, 1).astype(int)
    df["tier_label"] = df["priority_tier"].map(TIER_LABEL).fillna(df["priority_tier"])
    df["_strength"] = np.where(df["priority_rank"] == 3, df["company_anomaly_score"].fillna(0),
                               np.where(df["priority_rank"] == 1, df["paid_after"].fillna(0),
                                        df["company_net_paid"]))
    df = df.sort_values(["priority_rank", "_strength"], ascending=[True, False]).reset_index(drop=True)
    df["rank"] = np.arange(1, len(df) + 1)
    df = df.rename(columns={"company_net_paid": "company_total_billing_size_proxy_not_case_value"})

    cols = ["rank", "company_name", "tier_label", "specialty", "states",
            "company_total_billing_size_proxy_not_case_value", "reasons", "review_flags",
            "npi_count", "company_anomaly_score", "n_concept_signals", "fragmentation_signal",
            "any_billed_after_exclusion", "any_provider_on_leie", "any_probable_excluded_owner",
            "paid_after", "related_entities", "merge_basis", "merge_confidence", "npi_list"]
    # carry corrected fragmentation into the emitted column
    df["fragmentation_signal"] = df["genuine_fragmentation"]
    clean = df[cols].copy()

    def write(sub, name):
        con.register("t", sub)
        con.execute(f"""COPY (SELECT * FROM t) TO '{out_dir / name}'
            (FORMAT CSV, HEADER, QUOTE '"', FORCE_QUOTE (company_name, npi_list, related_entities))""")
        con.unregister("t")
        log(f"  wrote {name}: {len(sub):,} rows")

    write(clean, "company_leads_clean.csv")
    write(clean[df["priority_rank"].isin([1, 2, 3]).values], "triage_priority.csv")
    write(clean[df["priority_rank"].eq(4).values], "probable_owner_backlog.csv")

    # ---- summary ----
    log("\nLeads per tier_label:")
    log(clean["tier_label"].value_counts().reindex(
        ["Direct — billed while excluded", "Direct — on LEIE", "Company anomaly",
         "Probable excluded owner"]).dropna().to_string())
    log(f"\nnames resolved from NPPES: {n_resolved:,}; still unresolved: {int(df['name_unresolved'].sum()):,}")
    log(f"fragmentation flags: {n_frag_in:,} in → {n_frag_kept:,} kept (genuine), "
        f"{n_frag_in - n_frag_kept:,} dropped (one NPI ~= whole company)")
    log(f"possible_same_operator clusters: {n_clusters:,} "
        f"({int(df['possible_same_operator'].sum()):,} leads)")
    log(f"low_merge_confidence leads: {int((df['merge_confidence']=='low').sum()):,}")
    con.close()


if __name__ == "__main__":
    main()
