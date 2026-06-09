"""
company_lead_tracker.py  (attempt 2) — REAL company-level v3 anomaly + ranked tracker

The company rollup aggregated per-NPI signals with "max anomaly across NPIs", which
is biased toward big multi-NPI institutions. This computes a genuine company-level
anomaly score (company vs. company peers) by REUSING the exact pipeline logic:
  * rate features  — features.rate_features()  applied at company grain
  * v3 scoring     — refine_layer2_v3.score_concepts()  (entity-aware peers,
                     percentile ranks, volume gate, de-correlated concepts)
…then combines it with the ALREADY-AGGREGATED direct (Layer-1) and ownership
(Layer-3) signals from company_rollup.parquet (NOT recomputed) and emits one ranked,
explainable tracker.

net_paid is TOTAL billing — a SIZE proxy, NOT a case value. Read-only inputs; new
files only. Assertions hard-fail (dollar conservation, one row per company).

Run:
    python -m src.attempt_2.leads.company_lead_tracker --min-net-paid 10000000
"""

import argparse
import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

pd.set_option("future.no_silent_downcasting", True)

from ..clean_data import PRECLEAN_DIR
from ..ingest.features import rate_features              # reuse: rate features at any grain
from .refine_layer2_v3 import score_concepts, RARE_THRESHOLD   # reuse: exact v3 scoring

AUDIT_BASE_PAID = 1_100_631_960_143.0
# Anomaly tier = company_anomaly_score >= this (lowered bar to surface MORE anomalies),
# unioned with the original concept-signal lead (n_concept_signals >= 2) so no prior lead is lost.
ANOMALY_SCORE_MIN = 0.70
GOV_RE = re.compile(r"\b(COUNTY|STATE|CITY|DEPARTMENT|DEPT|UNIVERSITY|HOSPITALS?|DISTRICT|"
                    r"BOROUGH|PARISH|TRIBAL|TRIBE|NATION|PUBLIC|MUNICIPAL|GOVERNMENT|"
                    r"COMMONWEALTH|REGIONAL MEDICAL|HEALTH SYSTEM|MEDICAL CENTER)\b", re.I)


def log(m: str) -> None:
    print(m, flush=True)


def require(name: str, ok: bool, detail: str = "") -> None:
    log(f"    [assert {'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        raise AssertionError(f"ASSERTION FAILED: {name} — {detail}")


def _find(name: str, data: Path) -> Path:
    for root in [data / "detection", data / "features", data / "integrated", data]:
        if (root / name).exists():
            return root / name
    hits = sorted(data.rglob(name))
    if not hits:
        raise FileNotFoundError(f"required input not found: {name}")
    return hits[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-net-paid", type=float, default=10_000_000.0)
    args = ap.parse_args()
    thr = args.min_net_paid
    data = PRECLEAN_DIR.parent
    out_dir = data / "detection"

    rollup = _find("company_rollup.parquet", data)
    npimap = _find("npi_to_company_map.parquet", data)
    base = _find("spending_provider_base.parquet", data)
    pf = _find("provider_features.parquet", data)
    v3 = _find("fraud_leads_v3.parquet", data)
    l1cases = _find("layer1_candidate_cases.parquet", data)
    log(f"rollup={rollup}\nnpimap={npimap}\nbase={base}\nv3={v3}")

    con = duckdb.connect(str(out_dir / "_tracker.duckdb"))

    # ---- company base: every claim row tagged with its company ----
    con.execute(f"""CREATE OR REPLACE TABLE company_base AS
        SELECT m.company_id, b.hcpcs_code, b.service_month, b.total_paid,
               b.total_claim_lines, b.total_patients
        FROM read_parquet('{base}') b
        JOIN read_parquet('{npimap}') m ON b.billing_npi = m.npi""")
    base_total = con.execute("SELECT SUM(total_paid) FROM company_base").fetchone()[0]
    require("company_base_dollar_conservation",
            abs(float(base_total) - AUDIT_BASE_PAID) <= max(1.0, 1e-6 * AUDIT_BASE_PAID),
            f"company_base=${float(base_total):,.2f} audit=${AUDIT_BASE_PAID:,.2f}")

    # ---- Step 1: company rate features (REUSE features.rate_features at company grain) ----
    log("Step 1: company rate features (reusing features.rate_features) …")
    rf = rate_features(con, "company_base", "company_id").rename(columns={"key": "company_id"})
    rf["company_id"] = rf["company_id"].astype(str)
    require("rate_features_dollar_conservation",
            abs(float(rf["net_paid"].sum()) - AUDIT_BASE_PAID) <= max(1.0, 1e-6 * AUDIT_BASE_PAID),
            f"sum=${float(rf['net_paid'].sum()):,.2f}")

    # dominant taxonomy (by net_paid) + entity_type (org if any constituent NPI is type-2)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE comp_dim AS
        WITH per AS (
            SELECT m.company_id, pf.primary_taxonomy AS tax, pf.entity_type AS et, m.net_paid
            FROM read_parquet('{npimap}') m JOIN read_parquet('{pf}') pf ON m.npi = pf.npi),
        tax AS (SELECT company_id, arg_max(tax, p) AS dominant_taxonomy FROM
                  (SELECT company_id, tax, SUM(net_paid) p FROM per
                   WHERE COALESCE(tax,'')<>'' GROUP BY 1,2) GROUP BY 1)
        SELECT e.company_id,
               COALESCE(t.dominant_taxonomy, '') AS primary_taxonomy,
               CASE WHEN BOOL_OR(e.et='2') THEN '2' WHEN BOOL_OR(e.et='1') THEN '1' ELSE '' END AS entity_type
        FROM per e LEFT JOIN tax t USING (company_id) GROUP BY e.company_id, t.dominant_taxonomy""")
    dim = con.execute("SELECT * FROM comp_dim").df()
    dim["company_id"] = dim["company_id"].astype(str)

    # ---- company rare_for_taxonomy share at (dominant_taxonomy x entity), companies as the unit ----
    con.execute(f"""CREATE OR REPLACE TEMP TABLE rare_te AS
        WITH chd AS (
            SELECT cb.company_id, cb.hcpcs_code,
                   SUM(CASE WHEN cb.total_paid>0 THEN cb.total_paid ELSE 0 END) gross_code,
                   d.primary_taxonomy tax, d.entity_type et
            FROM company_base cb JOIN comp_dim d USING (company_id)
            GROUP BY 1,2, d.primary_taxonomy, d.entity_type),
        prov AS (SELECT tax, et, COUNT(DISTINCT company_id) np FROM chd GROUP BY 1,2),
        code AS (SELECT tax, et, hcpcs_code, COUNT(DISTINCT company_id) npc FROM chd GROUP BY 1,2,3),
        rare AS (SELECT c.tax, c.et, c.hcpcs_code FROM code c JOIN prov p USING (tax, et)
                 WHERE p.np>0 AND CAST(c.npc AS DOUBLE)/p.np < {RARE_THRESHOLD})
        SELECT chd.company_id,
               SUM(CASE WHEN r.hcpcs_code IS NOT NULL THEN gross_code ELSE 0 END)
                   / NULLIF(SUM(gross_code),0) AS rare_share_te
        FROM chd LEFT JOIN rare r ON chd.tax=r.tax AND chd.et=r.et AND chd.hcpcs_code=r.hcpcs_code
        GROUP BY chd.company_id""")
    rare = con.execute("SELECT * FROM rare_te").df()
    rare["company_id"] = rare["company_id"].astype(str)

    # ---- assemble scoring input + run the EXACT v3 methodology ----
    df = rf.merge(dim, on="company_id", how="left").merge(rare, on="company_id", how="left")
    df["entity_type"] = df["entity_type"].fillna("").astype(str)
    df["primary_taxonomy"] = df["primary_taxonomy"].fillna("").astype(str)
    df["log_max_single_month"] = np.log1p(df["max_single_month_net_paid"].where(
        df["max_single_month_net_paid"] > 0))
    log("Step 2: scoring companies with the exact v3 methodology (score_concepts) …")
    df = score_concepts(df)
    df = df.rename(columns={"anomaly_score_v3": "company_anomaly_score",
                            "anomaly_lead_v3": "company_anomaly_lead"})

    # ---- merge UNCHANGED rollup direct/ownership signals (not recomputed) ----
    roll = con.execute(f"""SELECT company_id, company_name, company_net_paid, npi_count, states,
        merge_basis, merge_confidence, primary_taxonomies, npi_list, max_constituent_net_paid,
        any_provider_on_leie, any_billed_after_exclusion, any_probable_excluded_owner
        FROM read_parquet('{rollup}')""").df()
    roll["company_id"] = roll["company_id"].astype(str)
    n_roll = len(roll)
    df = roll.merge(df, on="company_id", how="left")
    require("one_row_per_company", df["company_id"].is_unique and len(df) == n_roll,
            f"{len(df):,} vs {n_roll:,}")
    for b in ["any_provider_on_leie", "any_billed_after_exclusion", "any_probable_excluded_owner",
              "company_anomaly_lead"]:
        df[b] = df[b].fillna(False).astype(bool)
    df["n_concept_signals"] = df["n_concept_signals"].fillna(0).astype(int)

    # ---- company paid_after = sum of constituent NPIs' clean-after billing ----
    pa = con.execute(f"""SELECT m.company_id, SUM(COALESCE(c.paid_after,0)) paid_after
        FROM read_parquet('{npimap}') m JOIN read_parquet('{l1cases}') c ON m.npi=c.npi
        GROUP BY 1""").df()
    pa["company_id"] = pa["company_id"].astype(str)
    df = df.merge(pa, on="company_id", how="left")
    df["paid_after"] = df["paid_after"].fillna(0.0)

    # ---- Step 4: fragmentation flag — company L2 lead but NO constituent NPI was an L2 lead ----
    npi_l2 = con.execute(f"""SELECT m.company_id, BOOL_OR(COALESCE(v.anomaly_lead_v3,FALSE)) any_npi_l2
        FROM read_parquet('{npimap}') m LEFT JOIN read_parquet('{v3}') v ON m.npi=v.npi
        GROUP BY 1""").df()
    npi_l2["company_id"] = npi_l2["company_id"].astype(str)
    df = df.merge(npi_l2, on="company_id", how="left")
    df["any_npi_l2"] = df["any_npi_l2"].fillna(False).astype(bool)
    # fragmentation = extreme CONSOLIDATED but no single NPI flagged → needs >=2 NPIs to be
    # "split-billing" (a 1-NPI company flipping is just a peer-definition difference, not splitting)
    df["fragmentation_signal"] = (df["company_anomaly_lead"] & (~df["any_npi_l2"])
                                  & (df["npi_count"] >= 2))

    # ---- Step 5: tier (direct > anomaly > ownership), gate, rank ----
    # anomaly tier now = score >= ANOMALY_SCORE_MIN (more anomalies) UNION the original
    # concept-signal lead, so the validated leads are kept and lower-scoring ones are added.
    df["anomaly_tier_member"] = (
        (df["company_anomaly_score"].fillna(0) >= ANOMALY_SCORE_MIN) | df["company_anomaly_lead"])
    df["priority_tier"] = np.select(
        [df["any_billed_after_exclusion"], df["any_provider_on_leie"],
         df["anomaly_tier_member"], df["any_probable_excluded_owner"]],
        ["1_billed_after_exclusion", "2_on_leie", "3_company_anomaly", "4_probable_owner"],
        default="5_none")
    df["priority_rank"] = df["priority_tier"].str.slice(0, 1).astype(int)

    has_signal = df["priority_rank"].isin([1, 2, 3, 4])
    df["contributing_concepts"] = df["anomaly_contributing_concepts"].apply(
        lambda x: "; ".join(x) if isinstance(x, list) else "")
    df["reasons"] = df.apply(_reason, axis=1)
    # within-tier strength: anomaly by score; billed-after by paid_after; else by $
    df["_strength"] = np.where(df["priority_rank"] == 3, df["company_anomaly_score"].fillna(0),
                               np.where(df["priority_rank"] == 1, df["paid_after"],
                                        df["company_net_paid"]))

    out_cols = ["company_id", "company_name", "npi_count", "states",
                "company_net_paid", "priority_tier", "any_provider_on_leie",
                "any_billed_after_exclusion", "paid_after", "company_anomaly_score",
                "n_concept_signals", "contributing_concepts", "fragmentation_signal",
                "any_probable_excluded_owner", "merge_basis", "merge_confidence", "reasons", "npi_list"]

    def write(sub: pd.DataFrame, name: str) -> None:
        o = sub.sort_values(["priority_rank", "_strength"], ascending=[True, False])[out_cols].rename(
            columns={"company_net_paid": "company_total_billing_size_proxy_not_case_value"})
        o["company_total_billing_size_proxy_not_case_value"] = \
            o["company_total_billing_size_proxy_not_case_value"].round(0)
        o.to_parquet(out_dir / (name + ".parquet"), index=False)
        con.register("o_csv", o)
        con.execute(f"""COPY (SELECT * FROM o_csv) TO '{out_dir / (name + '.csv')}'
            (FORMAT CSV, HEADER, QUOTE '"', FORCE_QUOTE (company_id, npi_list))""")
        con.unregister("o_csv")
        log(f"  wrote {name}: {len(o):,} rows")

    # main tracker: every signal tier, gated to companies >= $10M
    over = df[has_signal & (df["company_net_paid"] >= thr)]
    write(over, "company_lead_tracker")
    # companion: sub-$10M billed-while-excluded / on-LEIE direct leads (highest precision, preserved)
    direct_under = df[df["priority_rank"].isin([1, 2]) & (df["company_net_paid"] < thr)]
    write(direct_under, "company_tracker_direct_under_10m")

    write_report(df, over, direct_under, thr, float(base_total), out_dir)
    con.close()
    log(f"Done. Main tracker (>= ${thr:,.0f}) tiers:")
    log(over["priority_tier"].value_counts().sort_index().to_string())
    log(f"Preserved sub-${thr:,.0f} direct leads: {len(direct_under):,}")


def _reason(r) -> str:
    t = r.priority_tier
    if t == "1_billed_after_exclusion":
        base = "Billed while excluded: a constituent NPI billed on/after its LEIE exclusion date"
    elif t == "2_on_leie":
        base = "A constituent NPI is on the OIG LEIE exclusion list"
    elif t == "3_company_anomaly":
        base = (f"Company-level anomaly vs. company peers: {int(r.n_concept_signals)} concept signal(s), "
                f"score {float(r.company_anomaly_score or 0):.2f}")
    elif t == "4_probable_owner":
        base = "Probable excluded owner — low-confidence ownership link (manual review)"
    else:
        base = "—"
    extra = []
    if r.fragmentation_signal:
        extra.append("FRAGMENTATION: extreme consolidated but NO single NPI was a Layer-2 lead "
                     "(possible split-billing)")
    if r.contributing_concepts and not t.startswith("3"):
        extra.append(f"company concepts: {r.contributing_concepts}")
    if r.any_provider_on_leie and not (t.startswith("1") or t.startswith("2")):
        extra.append("also on LEIE")
    if r.any_probable_excluded_owner and not t.startswith("4"):
        extra.append("also probable excluded owner")
    return base + ("; " + "; ".join(extra) if extra else "")


def write_report(df, leads, direct_under, thr, base_total, out_dir):
    log("Writing COMPANY_TRACKER_REPORT.md …")
    not_scored = df["not_scored"].fillna(True)
    r = ["# COMPANY_TRACKER_REPORT — attempt_2\n",
         "_Real company-level v3 anomaly (company vs. company peers) reusing "
         "features.rate_features + refine_layer2_v3.score_concepts, combined with the "
         "unchanged Layer-1/Layer-3 rollup signals. net_paid = size proxy, not case value._\n"
         f"\n_Main tracker `company_lead_tracker.csv` = companies with company_net_paid ≥ "
         f"${thr:,.0f} (all signal tiers). Sub-threshold billed-while-excluded / on-LEIE direct "
         f"leads ({len(direct_under):,}) are preserved in `company_tracker_direct_under_10m.csv`._\n"]

    r.append("\n## Dollar conservation & coverage\n"
             f"- company_base SUM(total_paid) = ${base_total:,.2f} vs audit ${AUDIT_BASE_PAID:,.2f} → "
             f"{'RECONCILED ✓' if abs(base_total-AUDIT_BASE_PAID)<=max(1.0,1e-6*AUDIT_BASE_PAID) else 'MISMATCH ✗'}\n"
             f"- companies scored: {int((~not_scored).sum()):,}; not_scored: {int(not_scored.sum()):,}\n")
    r.append("- not_scored reasons: "
             + ", ".join(f"{k}={v:,}" for k, v in
                         df.loc[not_scored, 'not_scored_reason'].fillna('n/a').value_counts().items()) + "\n")

    r.append(f"\n## Main tracker lead counts per priority_tier (≥ ${thr:,.0f})\n| tier | leads |\n|---|--:|\n")
    for t, c in leads["priority_tier"].value_counts().sort_index().items():
        r.append(f"| `{t}` | {c:,} |\n")
    r.append(f"\n- sub-${thr:,.0f} direct leads preserved in companion file: **{len(direct_under):,}**\n")
    low = leads[leads["merge_confidence"] == "low"]
    r.append(f"- ranked leads on LOW merge_confidence (over/under-merge caution): {len(low):,}\n")

    # Step 3 check: top-25 company-anomaly composition — giants must NOT dominate
    anom = leads[leads["priority_tier"] == "3_company_anomaly"].copy()
    top = anom.head(25)
    gov_top = top["company_name"].fillna("").map(lambda s: bool(GOV_RE.search(s)))
    et = top["entity_type"].map({"1": "individual", "2": "organization"}).fillna("?").value_counts()
    r.append("\n## Top-25 company-anomaly leads — composition (giants should NOT dominate)\n"
             f"- government/public-style names in top 25: **{int(gov_top.sum())}/25**\n"
             f"- entity mix: " + ", ".join(f"{k}={v}" for k, v in et.items()) + "\n"
             f"- top taxonomies: " + ", ".join(f"{k}={v}" for k, v in
                                                top["primary_taxonomy"].value_counts().head(5).items()) + "\n")
    r.append("\n| company | tax | state(s) | company_net_paid | concepts | gov? |\n|---|---|---|--:|--:|:-:|\n")
    for x in top.itertuples():
        r.append(f"| {(x.company_name or x.company_id)[:30]} | {x.primary_taxonomy} | {x.states[:18]} | "
                 f"${x.company_net_paid:,.0f} | {x.n_concept_signals} | "
                 f"{'Y' if GOV_RE.search(x.company_name or '') else ''} |\n")

    # Step 4: fragmentation finds
    frag = leads[leads["fragmentation_signal"]].sort_values("company_net_paid", ascending=False)
    r.append(f"\n## Fragmentation signals (company L2 lead, NO constituent NPI was an L2 lead)\n"
             f"- count among ranked leads: **{len(frag):,}** (possible deliberate split-billing)\n")
    r.append("\n| company | npis | company_net_paid | max single NPI | concepts |\n|---|--:|--:|--:|--:|\n")
    for x in frag.head(25).itertuples():
        r.append(f"| {(x.company_name or x.company_id)[:30]} | {x.npi_count} | ${x.company_net_paid:,.0f} | "
                 f"${(x.max_constituent_net_paid or 0):,.0f} | {x.n_concept_signals} |\n")

    r.append("\n## Top 25 of the tracker overall\n"
             "| company | tier | company_net_paid | npis | reasons |\n|---|---|--:|--:|---|\n")
    for x in leads.head(25).itertuples():
        r.append(f"| {(x.company_name or x.company_id)[:28]} | {x.priority_tier} | "
                 f"${x.company_net_paid:,.0f} | {x.npi_count} | {x.reasons[:80]} |\n")

    r.append("\n## Integrity\n"
             "- company anomaly REUSES features.rate_features + refine_layer2_v3.score_concepts "
             "(imported, not reimplemented); dollar conservation asserted; one row per company; "
             "Layer-1/Layer-3 reused from the rollup (not recomputed); identifiers as strings.\n")
    (out_dir / "COMPANY_TRACKER_REPORT.md").write_text("".join(r))


if __name__ == "__main__":
    main()
