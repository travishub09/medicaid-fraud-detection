"""
detect.py  (attempt 2) — three-layer, EXPLAINABLE fraud lead prioritization

Produces a ranked lead list from provider_features — NOT one blended black-box
score. Three independent layers, each lead keeps its contributing signals
visible:

  Layer 1  deterministic high-precision rule flags (highest priority)
           - billed-while-excluded (LEIE excl_date vs actual claim months)
           - physically-implausible rates (stated, justified thresholds)
  Layer 2  anomaly scoring on SIZE-NORMALIZED rate features vs taxonomy peers,
           via a transparent composite (count of robust-z exceedances + summed
           magnitude); raw-dollar magnitude is NEVER a primary driver.
  Layer 3  low-confidence ownership track (probable excluded owner) kept SEPARATE.

priority_tier ranks Layer-1 (esp. billed_after_exclusion) > Layer-2 > Layer-3.
Degenerate (gross<=0) and too-small-peer-group providers are flagged not_scored,
never scored. Read-only on all inputs; nothing is recomputed from raw data.

Run:
    python -m src.attempt_2.detect
"""

import argparse
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

pd.set_option("future.no_silent_downcasting", True)

from ..clean_data import PRECLEAN_DIR

# Parameters (all stated in the report)
ROBUST_Z_CUTOFF = 3.5            # a normalized feature is a "signal" at/above this robust z
ROBUST_Z_CLIP = 50.0             # clip robust z; near-zero-MAD peer groups else yield pathological z
ANOMALY_LEAD_MIN_SIGNALS = 2     # >= this many signals ⇒ a Layer-2 lead (tiered)
IMPLAUSIBLE_LINES_PER_PATIENT = 100.0    # max observed 497.8, p99.99 62.4 → physically implausible
IMPLAUSIBLE_PAID_PER_LINE = 50_000.0     # ~50x p99 ($926), ~5x p99.99 ($10.7k) → implausible avg/line

# Layer-2 PRIMARY normalized features (size-normalized; count toward signals/score).
PRIMARY_FEATS = ["paid_per_patient_instance", "lines_per_patient_instance", "paid_per_claim_line",
                 "top_hcpcs_paid_share", "hcpcs_hhi", "rare_for_taxonomy_paid_share",
                 "month_to_month_volatility", "yoy_growth_net_paid"]
# Dollar magnitude is CONTEXT only (log-transformed); recorded, never counted/scored.
CONTEXT_FEAT = "max_single_month_net_paid"


def log(m: str) -> None:
    print(m, flush=True)


def require(name: str, ok: bool, detail: str = "") -> None:
    log(f"    [assert {'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        raise AssertionError(f"ASSERTION FAILED: {name} — {detail}")


def resolve(fname: str, dirs: list[Path]) -> Path:
    for d in dirs:
        if (d / fname).exists():
            return d / fname
    raise FileNotFoundError(
        f"Required input '{fname}' not found in any of: {[str(d) for d in dirs]}. "
        f"Place it in ~/Desktop/data/features/ (or ~/Desktop/data/) and re-run — "
        f"do not recompute features.")


def robust_z_within(df: pd.DataFrame, col: str, grp: str, scorable: pd.Series) -> pd.Series:
    """Robust z = 1.4826*(x-median)/MAD computed within peer group over the
    scorable baseline only. MAD==0 or absent ⇒ NaN (no signal)."""
    base = df.loc[scorable, [grp, col]].dropna(subset=[col])
    med = base.groupby(grp)[col].median()
    ad = (base[col] - base[grp].map(med)).abs()
    mad = ad.groupby(base[grp]).median()
    m = df[grp].map(med)
    a = df[grp].map(mad).replace(0, np.nan)
    # Clip: a near-zero MAD would otherwise turn a tiny deviation into an absurd
    # z that swamps the composite. Capping keeps signals firing but bounded.
    return (1.4826 * (df[col] - m) / a).clip(-ROBUST_Z_CLIP, ROBUST_Z_CLIP)


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    data = PRECLEAN_DIR.parent                         # ~/Desktop/data
    search = [data / "features", data, data / "integrated"]
    pf_path = resolve("provider_features.parquet", search)
    excl_path = resolve("exclusions.parquet", search)
    base_path = resolve("spending_provider_base.parquet", search)
    fof_path = resolve("facility_owner_exclusion_flags.parquet", search)
    out_dir = data / "detection"
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"Inputs: features={pf_path.parent.name}/  exclusions={excl_path.parent.name}/  "
        f"base={base_path.parent.name}/  facility_flags={fof_path.parent.name}/")

    con = duckdb.connect()

    # ---- Layer 1a: billed-while-excluded (uses spending_provider_base for claim months) ----
    log("Layer 1a: billed-while-excluded (excl_date vs actual claim months) …")
    leie_check = con.execute(f"""
        WITH excl AS (SELECT npi, MIN(excl_date) AS excl_date
                      FROM read_parquet('{excl_path}') WHERE npi IS NOT NULL GROUP BY npi),
             leie AS (SELECT DISTINCT npi
                      FROM read_parquet('{pf_path}') WHERE provider_on_leie)
        SELECT b.billing_npi AS npi,
               ANY_VALUE(e.excl_date)                                   AS excl_date,
               MAX(b.service_month)                                     AS max_claim_month,
               COALESCE(SUM(b.total_paid) FILTER (
                   WHERE strftime(e.excl_date,'%Y-%m') IS NOT NULL
                     AND b.service_month >= strftime(e.excl_date,'%Y-%m')), 0) AS paid_after_exclusion,
               COUNT(DISTINCT b.service_month) FILTER (
                   WHERE strftime(e.excl_date,'%Y-%m') IS NOT NULL
                     AND b.service_month >= strftime(e.excl_date,'%Y-%m'))      AS n_months_after
        FROM read_parquet('{base_path}') b
        JOIN excl e ON b.billing_npi = e.npi
        WHERE b.billing_npi IN (SELECT npi FROM leie)
        GROUP BY b.billing_npi
    """).df()
    leie_check["npi"] = leie_check["npi"].astype(str)
    require("leie_check_one_row_per_npi", leie_check["npi"].is_unique)
    leie_check["billed_after_exclusion"] = leie_check["n_months_after"] > 0
    leie_check["excluded_after_billing"] = (~leie_check["billed_after_exclusion"]) \
        & leie_check["excl_date"].notna()

    # ---- load feature table ----
    feat_cols = ["npi", "entity_type", "primary_taxonomy", "practice_state", "org_legal_name",
                 "gross_paid", "net_paid", "paid_per_claim_line", "paid_per_patient_instance",
                 "lines_per_patient_instance", "top_hcpcs_paid_share", "hcpcs_hhi",
                 "rare_for_taxonomy_paid_share", "month_to_month_volatility",
                 "max_single_month_net_paid", "yoy_growth_net_paid", "peer_group_too_small_tax",
                 "provider_on_leie", "facility_has_excluded_owner_probable",
                 "facility_has_excluded_owner_high", "facility_excluded_owner_n_probable",
                 "excluded_owner_role"]
    df = con.execute(f"SELECT {', '.join(feat_cols)} FROM read_parquet('{pf_path}')").df()
    df["npi"] = df["npi"].astype(str)
    n0 = len(df)
    require("features_one_row_per_npi", df["npi"].is_unique, f"{n0:,} rows")

    df = df.merge(leie_check[["npi", "excl_date", "max_claim_month", "paid_after_exclusion",
                              "n_months_after", "billed_after_exclusion", "excluded_after_billing"]],
                  on="npi", how="left")
    require("no_fanout_after_leie_merge", len(df) == n0, f"{len(df):,} vs {n0:,}")
    df["billed_after_exclusion"] = df["billed_after_exclusion"].fillna(False).astype(bool)
    df["excluded_after_billing"] = df["excluded_after_billing"].fillna(False).astype(bool)

    # ---- Layer 1b: physically-implausible rates ----
    df["rule_implausible_lines_per_patient"] = df["lines_per_patient_instance"] > IMPLAUSIBLE_LINES_PER_PATIENT
    df["rule_implausible_paid_per_line"] = df["paid_per_claim_line"] > IMPLAUSIBLE_PAID_PER_LINE

    def l1_reasons(r) -> list[str]:
        out = []
        if r.billed_after_exclusion:
            out.append(f"billed_after_exclusion(paid_after=${r.paid_after_exclusion:,.0f},"
                       f"months={int(r.n_months_after)})")
        if r.excluded_after_billing:
            out.append("excluded_after_billing(on_LEIE,billing_predates_exclusion)")
        if r.rule_implausible_lines_per_patient:
            out.append(f"implausible_lines_per_patient(={r.lines_per_patient_instance:.1f})")
        if r.rule_implausible_paid_per_line:
            out.append(f"implausible_paid_per_claim_line(=${r.paid_per_claim_line:,.0f})")
        return out
    df["rule_reasons"] = [l1_reasons(r) for r in df.itertuples()]
    df["layer1_hit"] = df["rule_reasons"].str.len() > 0

    # ---- Layer 2: anomaly scoring on size-normalized features (taxonomy peers) ----
    log("Layer 2: robust-z anomaly scoring on normalized features (taxonomy peers) …")
    has_tax = df["primary_taxonomy"].notna() & (df["primary_taxonomy"].astype(str) != "")
    too_small = df["peer_group_too_small_tax"].fillna(False).astype(bool)
    scorable = (df["gross_paid"] > 0) & has_tax & (~too_small)
    df["not_scored"] = ~scorable
    # precedence: degenerate dollars → missing taxonomy → undersized peer group
    df["not_scored_reason"] = np.where(
        df["gross_paid"] <= 0, "degenerate_zero_gross_paid",
        np.where(~has_tax, "missing_taxonomy",
                 np.where(too_small, "peer_group_too_small(<30)", "")))

    df["_logmax"] = np.log1p(df[CONTEXT_FEAT].where(df[CONTEXT_FEAT] > 0))
    zmap = {}
    for f in PRIMARY_FEATS:
        zmap[f] = robust_z_within(df, f, "primary_taxonomy", scorable)
    z_context = robust_z_within(df.assign(**{CONTEXT_FEAT: df["_logmax"]}),
                                CONTEXT_FEAT, "primary_taxonomy", scorable)

    feat_arr = np.array(PRIMARY_FEATS)
    zmat = np.column_stack([zmap[f].to_numpy() for f in PRIMARY_FEATS])
    exceed = zmat >= ROBUST_Z_CUTOFF                    # NaN >= x ⇒ False (NaN not-applicable)
    sc = scorable.to_numpy()
    zc = z_context.to_numpy()
    n_signals, scores, contrib = np.zeros(len(df), int), np.full(len(df), np.nan), []
    for i in range(len(df)):
        if not sc[i]:
            contrib.append([])
            continue
        mask = exceed[i]
        n_signals[i] = int(mask.sum())
        scores[i] = float(np.nansum(np.where(mask, zmat[i], 0.0)))
        items = [f"{feat_arr[j]}(z={zmat[i, j]:.1f})" for j in range(len(feat_arr)) if mask[j]]
        if zc[i] >= ROBUST_Z_CUTOFF:                    # context: shown, not counted/scored
            items.append(f"{CONTEXT_FEAT}_log(z={zc[i]:.1f},context)")
        contrib.append(items)
    df["n_anomaly_signals"] = n_signals
    df["anomaly_score"] = scores
    df["anomaly_contributing_features"] = contrib
    df.loc[df["not_scored"], "n_anomaly_signals"] = 0
    df["anomaly_lead"] = scorable.to_numpy() & (df["n_anomaly_signals"] >= ANOMALY_LEAD_MIN_SIGNALS)

    # ---- Layer 3: probable excluded owner (separate, never blended) ----
    df["layer3_probable_owner"] = df["facility_has_excluded_owner_probable"].fillna(False)

    # ---- priority tiering: L1(billed_after > implausible > excluded_after) > L2 > L3 ----
    df["priority_tier"] = np.select(
        [df["billed_after_exclusion"],
         df["rule_implausible_lines_per_patient"] | df["rule_implausible_paid_per_line"],
         df["excluded_after_billing"],
         df["anomaly_lead"],
         df["layer3_probable_owner"]],
        ["1_L1_billed_after_exclusion", "2_L1_implausible_rate", "3_L1_excluded_after_billing",
         "4_L2_anomaly", "5_L3_probable_owner"],
        default="6_none")
    df["priority_rank"] = df["priority_tier"].str.slice(0, 1).astype(int)

    # ---- assemble + write ----
    out_cols = ["npi", "priority_tier", "priority_rank", "entity_type", "primary_taxonomy",
                "practice_state", "org_legal_name",
                # Layer 1
                "layer1_hit", "rule_reasons", "provider_on_leie", "billed_after_exclusion",
                "excluded_after_billing", "paid_after_exclusion", "n_months_after",
                "excl_date", "max_claim_month",
                "rule_implausible_lines_per_patient", "rule_implausible_paid_per_line",
                "lines_per_patient_instance", "paid_per_claim_line",
                # Layer 2
                "anomaly_lead", "anomaly_score", "n_anomaly_signals",
                "anomaly_contributing_features", "not_scored", "not_scored_reason",
                # Layer 3 (own column)
                "layer3_probable_owner", "facility_excluded_owner_n_probable", "excluded_owner_role",
                # context
                "gross_paid", "net_paid"]
    leads = df[out_cols].copy()
    require("leads_one_row_per_npi", leads["npi"].is_unique and len(leads) == n0,
            f"{len(leads):,} vs {n0:,}")

    leads.to_parquet(out_dir / "fraud_leads.parquet", index=False)
    leads[leads["layer1_hit"]].to_parquet(out_dir / "layer1_rule_hits.parquet", index=False)
    leads[leads["layer3_probable_owner"]].to_parquet(out_dir / "layer3_ownership_leads.parquet", index=False)

    write_report(leads, df, out_dir)
    con.close()
    log(f"Done. Outputs in {out_dir}")
    log(leads["priority_tier"].value_counts().sort_index().to_string())


def write_report(leads: pd.DataFrame, df: pd.DataFrame, out_dir: Path) -> None:
    log("Writing DETECTION_REPORT.md …")
    n = len(leads)
    r = ["# DETECTION_REPORT — attempt_2 (three-layer explainable leads)\n",
         "_One row per billing NPI. Layers kept separate; every lead exposes its signals. "
         "Read-only on all inputs._\n"]

    r.append("\n## Priority tiers (Layer-1 > Layer-2 > Layer-3)\n| tier | providers |\n|---|--:|\n")
    for t, c in leads["priority_tier"].value_counts().sort_index().items():
        r.append(f"| `{t}` | {c:,} |\n")

    n_leie = int(df["provider_on_leie"].sum())
    ba = int(df["billed_after_exclusion"].sum())
    ea = int(df["excluded_after_billing"].sum())
    r.append(f"\n## Layer 1 — billed-while-excluded (of {n_leie} LEIE providers)\n"
             f"- `billed_after_exclusion` (≥1 claim month on/after excl_date — VERY high precision): **{ba}**\n"
             f"- `excluded_after_billing` (all billing predates exclusion — lower priority): **{ea}**\n"
             f"- LEIE providers with no usable excl_date / undetermined: {n_leie - ba - ea}\n")
    r.append(f"\n### Layer 1 — implausible-rate thresholds (stated + justified)\n"
             f"- `lines_per_patient_instance > {IMPLAUSIBLE_LINES_PER_PATIENT:.0f}`: "
             f"**{int(df['rule_implausible_lines_per_patient'].sum())}** providers. "
             f"Justification: feature p99.99 ≈ 62, max ≈ 498; >100 billed lines per "
             f"patient-service-instance is physically implausible.\n"
             f"- `paid_per_claim_line > ${IMPLAUSIBLE_PAID_PER_LINE:,.0f}`: "
             f"**{int(df['rule_implausible_paid_per_line'].sum())}** providers. "
             f"Justification: ~50× the p99 ($926) and ~5× p99.99 ($10.7k) — an implausible average "
             f"payment per claim line for routine billing (flag for review).\n")

    ns = int(df["not_scored"].sum())
    r.append(f"\n## Layer 2 — anomaly scoring (taxonomy peers, robust z ≥ {ROBUST_Z_CUTOFF})\n"
             f"- Scored on size-normalized rate features only (raw-dollar magnitude excluded; "
             f"`{CONTEXT_FEAT}` log-transformed and used as CONTEXT, never a primary driver).\n"
             f"- robust z clipped to ±{ROBUST_Z_CLIP:.0f} so near-zero-MAD peer groups can't produce "
             f"pathological scores; `anomaly_score` = sum of clipped exceedances over signals.\n"
             f"- not_scored: **{ns:,}** providers — "
             + ", ".join(f"{k}={v:,}" for k, v in df.loc[df['not_scored'], 'not_scored_reason']
                         .value_counts().items()) + "\n"
             f"- Layer-2 leads (≥ {ANOMALY_LEAD_MIN_SIGNALS} signals): "
             f"**{int(df['anomaly_lead'].sum()):,}**\n")
    scored = df[~df["not_scored"]]
    if len(scored):
        q = scored["anomaly_score"].quantile([0.5, 0.9, 0.99, 1.0])
        sig = scored["n_anomaly_signals"].value_counts().sort_index()
        r.append(f"- anomaly_score (scored only): median={q[0.5]:.2f}, p90={q[0.9]:.2f}, "
                 f"p99={q[0.99]:.2f}, max={q[1.0]:.2f}\n")
        r.append("- signal-count distribution: "
                 + ", ".join(f"{int(k)}→{int(v):,}" for k, v in sig.items()) + "\n")

    r.append(f"\n## Layer 3 — probable excluded-owner track (SEPARATE, low confidence)\n"
             f"- providers flagged `layer3_probable_owner`: **{int(df['layer3_probable_owner'].sum()):,}** "
             f"(manual triage; never blended into Layer-2 score; high-tier owner matches = 0).\n")

    # top leads per tier
    def top(tier, sort_col, k=25):
        sub = leads[leads["priority_tier"] == tier].copy()
        if not len(sub):
            return f"\n### {tier}: (none)\n"
        sub = sub.sort_values(sort_col, ascending=False).head(k)
        lines = [f"\n### Top {min(k,len(sub))} — `{tier}` (by {sort_col})\n",
                 "| npi | taxonomy | state | net_paid | why |\n|---|---|---|--:|---|\n"]
        for x in sub.itertuples():
            why = "; ".join(x.rule_reasons) if x.rule_reasons else \
                  ("; ".join(x.anomaly_contributing_features) if x.anomaly_contributing_features
                   else ("probable_excluded_owner" if x.layer3_probable_owner else ""))
            why = (why[:160] + "…") if len(why) > 160 else why
            lines.append(f"| `{x.npi}` | {x.primary_taxonomy} | {x.practice_state} | "
                         f"${(x.net_paid or 0):,.0f} | {why} |\n")
        return "".join(lines)

    r.append("\n## Top leads per tier (with contributing signals)\n")
    r.append(top("1_L1_billed_after_exclusion", "paid_after_exclusion"))
    r.append(top("2_L1_implausible_rate", "net_paid"))
    r.append(top("3_L1_excluded_after_billing", "net_paid"))
    r.append(top("4_L2_anomaly", "anomaly_score"))
    r.append(top("5_L3_probable_owner", "facility_excluded_owner_n_probable"))

    r.append("\n## Integrity\n"
             f"- fraud_leads.parquet: {n:,} rows = one per billing NPI ✓\n"
             "- LEIE merge asserted non-fan-out; tiers kept separate; raw dollars not a primary "
             "anomaly driver; degenerate/small-group providers flagged not_scored.\n"
             "- No input files modified.\n")
    (out_dir / "DETECTION_REPORT.md").write_text("".join(r))


if __name__ == "__main__":
    main()
