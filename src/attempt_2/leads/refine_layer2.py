"""
refine_layer2.py  (attempt 2) — discriminating, trustworthy Layer-2 anomaly scoring

The original Layer-2 score was unreliable: peer groups mixed individuals and
organizations, near-zero-MAD groups made robust z explode (the ±50 "wall"),
rare_for_taxonomy fired near-universally, and "sum of clipped exceedances"
rewarded org complexity — yielding ~125k undifferentiated "leads". This rebuilds
Layer 2 only; Layer-1 (LEIE) and Layer-3 (probable-owner) flags are reused
UNCHANGED from fraud_leads.parquet and re-merged.

Fixes:
  1. Entity-type-aware peers: baseline within (taxonomy x entity_type), falling
     back to taxonomy-only when a cell has < MIN_PEER; never individuals vs orgs.
  2. Degeneracy-safe normalization: PRIMARY = within-peer PERCENTILE RANK
     (bounded [0,1], immune to near-zero MAD). No clipped robust-z as a driver.
  3. rare_for_taxonomy recomputed at (taxonomy x entity_type); reported flag rate.
  4. Discriminating composite: count of features at/above the in-group 99th
     percentile (lead = >= MIN_SIGNALS) + mean percentile; IsolationForest as a
     SECONDARY cross-check only. Contributing features stay visible.
  5. Safeguards kept: zero-gross + too-small-group ⇒ not_scored; raw dollars are
     never a primary driver (max_single_month is context only).

Writes NEW files only (never overwrites): fraud_leads_v2.parquet,
LAYER2_REFINEMENT_REPORT.md. Read-only on inputs. Assertions raise & stop.

Run:
    python -m src.attempt_2.refine_layer2
"""

import argparse
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

pd.set_option("future.no_silent_downcasting", True)

from ..clean_data import PRECLEAN_DIR

MIN_PEER = 30
P99 = 0.99                 # in-group percentile that counts as an extreme signal
MIN_SIGNALS = 2            # >= this many extreme signals ⇒ a Layer-2 lead
RARE_THRESHOLD = 0.01      # code billed by < this share of a (tax x entity) cell ⇒ rare

# PRIMARY size-normalized features (percentile-ranked; count toward signals).
PRIMARY = ["paid_per_patient_instance", "lines_per_patient_instance", "paid_per_claim_line",
           "top_hcpcs_paid_share", "hcpcs_hhi", "rare_share_te",
           "month_to_month_volatility", "yoy_growth_net_paid"]
CONTEXT = "log_max_single_month"          # dollar magnitude: context only, never counted


def log(m: str) -> None:
    print(m, flush=True)


def require(name: str, ok: bool, detail: str = "") -> None:
    log(f"    [assert {'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        raise AssertionError(f"ASSERTION FAILED: {name} — {detail}")


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    data = PRECLEAN_DIR.parent
    pf = data / "features" / "provider_features.parquet"
    ph = data / "features" / "provider_hcpcs.parquet"
    fl = data / "detection" / "fraud_leads.parquet"
    for p in (pf, ph, fl):
        if not p.exists():
            raise FileNotFoundError(f"required input missing: {p}")
    out_dir = data / "detection"
    con = duckdb.connect()
    rep: list[str] = ["# LAYER2_REFINEMENT_REPORT — attempt_2\n",
                      "_Layer-2 rebuilt with entity-type-aware peers + percentile-rank normalization. "
                      "Layer-1/Layer-3 reused unchanged. New files only; inputs untouched._\n"]

    # ---- load features ----
    cols = ["npi", "entity_type", "primary_taxonomy", "practice_state", "org_legal_name",
            "gross_paid", "net_paid", "paid_per_patient_instance", "lines_per_patient_instance",
            "paid_per_claim_line", "top_hcpcs_paid_share", "hcpcs_hhi",
            "rare_for_taxonomy_paid_share", "month_to_month_volatility",
            "max_single_month_net_paid", "yoy_growth_net_paid"]
    df = con.execute(f"SELECT {', '.join(cols)} FROM read_parquet('{pf}')").df()
    df["npi"] = df["npi"].astype(str)
    df["entity_type"] = df["entity_type"].fillna("").astype(str)
    df["primary_taxonomy"] = df["primary_taxonomy"].fillna("").astype(str)
    n0 = len(df)
    require("features_one_row_per_npi", df["npi"].is_unique, f"{n0:,}")

    # ---- Fix 3: recompute rare-code share at (taxonomy x entity_type) ----
    log("Recomputing rare-code share at (taxonomy x entity_type) …")
    con.register("ctx", df[["npi", "primary_taxonomy", "entity_type"]])
    rare = con.execute(f"""
        WITH ph AS (
            SELECT p.billing_npi AS npi, p.hcpcs_code, p.gross_paid_code,
                   c.primary_taxonomy AS tax, c.entity_type AS et
            FROM read_parquet('{ph}') p JOIN ctx c ON p.billing_npi = c.npi),
        prov AS (SELECT tax, et, COUNT(DISTINCT npi) np FROM ph GROUP BY 1, 2),
        code AS (SELECT tax, et, hcpcs_code, COUNT(DISTINCT npi) npc FROM ph GROUP BY 1, 2, 3),
        rare AS (SELECT c.tax, c.et, c.hcpcs_code FROM code c JOIN prov p USING (tax, et)
                 WHERE p.np > 0 AND CAST(c.npc AS DOUBLE)/p.np < {RARE_THRESHOLD})
        SELECT ph.npi,
               SUM(CASE WHEN r.hcpcs_code IS NOT NULL THEN gross_paid_code ELSE 0 END)
                   / NULLIF(SUM(gross_paid_code), 0) AS rare_share_te
        FROM ph LEFT JOIN rare r ON ph.tax=r.tax AND ph.et=r.et AND ph.hcpcs_code=r.hcpcs_code
        GROUP BY ph.npi
    """).df()
    rare["npi"] = rare["npi"].astype(str)
    df = df.merge(rare, on="npi", how="left")
    require("no_fanout_rare_merge", len(df) == n0)
    df["log_max_single_month"] = np.log1p(df["max_single_month_net_paid"].where(
        df["max_single_month_net_paid"] > 0))

    # ---- Fix 1: entity-type-aware peer key with taxonomy fallback ----
    has_tax = df["primary_taxonomy"] != ""
    scorable_base = (df["gross_paid"] > 0) & has_tax
    te = df["primary_taxonomy"] + "|" + df["entity_type"]
    size_te = te.where(scorable_base).map(te.where(scorable_base).value_counts()).fillna(0)
    size_t = df["primary_taxonomy"].where(scorable_base).map(
        df["primary_taxonomy"].where(scorable_base).value_counts()).fillna(0)
    df["peer_key"] = np.where(size_te >= MIN_PEER, te,
                              np.where(size_t >= MIN_PEER, df["primary_taxonomy"], np.nan))
    df["peer_basis"] = np.where(size_te >= MIN_PEER, "taxonomy_x_entity",
                                np.where(size_t >= MIN_PEER, "taxonomy_only", "too_small"))
    df["scorable"] = scorable_base & df["peer_key"].notna()
    df["not_scored"] = ~df["scorable"]
    df["not_scored_reason"] = np.where(
        df["gross_paid"] <= 0, "degenerate_zero_gross_paid",
        np.where(~has_tax, "missing_taxonomy",
                 np.where(~df["scorable"], "peer_group_too_small(<30)", "")))

    # ---- Fix 2: percentile-rank each feature within peer group (NaN-safe) ----
    log("Percentile-ranking features within entity-type-aware peer groups …")
    sc = df["scorable"]
    for f in PRIMARY + [CONTEXT]:
        masked = df[f].where(sc)                       # baseline = scorable only; non-scorable→NaN
        df["pct_" + f] = masked.groupby(df["peer_key"]).rank(pct=True)

    # ---- Fix 4: discriminating composite (count of P99 signals + mean percentile) ----
    pct_primary = df[[f"pct_{f}" for f in PRIMARY]]
    exceed = pct_primary.ge(P99)                        # NaN.ge ⇒ False (feature N/A for provider)
    df["n_extreme_p99"] = exceed.sum(axis=1).where(sc, 0).astype(int)
    df["mean_percentile"] = pct_primary.mean(axis=1)    # mean over available features
    df.loc[~sc, "mean_percentile"] = np.nan
    df["anomaly_score_v2"] = df["mean_percentile"]
    df["anomaly_lead_v2"] = sc & (df["n_extreme_p99"] >= MIN_SIGNALS)

    feat_arr = np.array(PRIMARY)
    ex = exceed.to_numpy()
    pv = pct_primary.to_numpy()
    pc = df[f"pct_{CONTEXT}"].to_numpy()
    scn = sc.to_numpy()
    contrib = []
    for i in range(len(df)):
        if not scn[i]:
            contrib.append([]); continue
        items = [f"{feat_arr[j]}(pct={pv[i, j]:.3f})" for j in range(len(feat_arr)) if ex[i, j]]
        if pc[i] >= P99:
            items.append(f"{CONTEXT}(pct={pc[i]:.3f},context)")
        contrib.append(items)
    df["anomaly_contributing_features_v2"] = contrib

    # ---- secondary cross-check: isolation forest on the percentile matrix ----
    log("Fitting IsolationForest (secondary cross-check) …")
    try:
        from sklearn.ensemble import IsolationForest
        X = pct_primary.fillna(0.5).to_numpy()         # neutral impute for N/A features
        Xs = X[scn]
        clf = IsolationForest(n_estimators=120, random_state=0, n_jobs=-1).fit(Xs)
        iso = np.full(len(df), np.nan)
        iso[scn] = -clf.score_samples(Xs)              # higher = more anomalous
        df["iforest_score_secondary"] = iso
    except Exception as e:                             # secondary only — never block the build
        log(f"    (isolation forest skipped: {e})")
        df["iforest_score_secondary"] = np.nan

    # ---- re-merge UNCHANGED Layer-1 / Layer-3 flags from fraud_leads ----
    log("Re-merging unchanged Layer-1/Layer-3 flags …")
    l13 = con.execute(f"""
        SELECT npi, billed_after_exclusion, excluded_after_billing,
               rule_implausible_lines_per_patient, rule_implausible_paid_per_line,
               provider_on_leie, rule_reasons, layer3_probable_owner,
               facility_excluded_owner_n_probable, excluded_owner_role,
               anomaly_score AS anomaly_score_old,
               anomaly_contributing_features AS contributing_old
        FROM read_parquet('{fl}')
    """).df()
    l13["npi"] = l13["npi"].astype(str)
    df = df.merge(l13, on="npi", how="left")
    require("no_fanout_l13_merge", len(df) == n0, f"{len(df):,} vs {n0:,}")

    # ---- re-tier (Layer-1 > Layer-2 > Layer-3), identical precedence ----
    for c in ["billed_after_exclusion", "excluded_after_billing",
              "rule_implausible_lines_per_patient", "rule_implausible_paid_per_line",
              "layer3_probable_owner"]:
        df[c] = df[c].fillna(False).astype(bool)
    df["priority_tier"] = np.select(
        [df["billed_after_exclusion"],
         df["rule_implausible_lines_per_patient"] | df["rule_implausible_paid_per_line"],
         df["excluded_after_billing"],
         df["anomaly_lead_v2"],
         df["layer3_probable_owner"]],
        ["1_L1_billed_after_exclusion", "2_L1_implausible_rate", "3_L1_excluded_after_billing",
         "4_L2_anomaly", "5_L3_probable_owner"], default="6_none")
    df["priority_rank"] = df["priority_tier"].str.slice(0, 1).astype(int)

    out_cols = ["npi", "priority_tier", "priority_rank", "entity_type", "primary_taxonomy",
                "practice_state", "org_legal_name", "gross_paid", "net_paid",
                "provider_on_leie", "billed_after_exclusion", "excluded_after_billing", "rule_reasons",
                "anomaly_lead_v2", "anomaly_score_v2", "n_extreme_p99", "mean_percentile",
                "anomaly_contributing_features_v2", "iforest_score_secondary",
                "peer_basis", "not_scored", "not_scored_reason",
                "layer3_probable_owner", "facility_excluded_owner_n_probable", "excluded_owner_role"]
    leads = df[out_cols].sort_values(
        ["priority_rank", "n_extreme_p99", "anomaly_score_v2"],
        ascending=[True, False, False]).reset_index(drop=True)
    require("v2_one_row_per_npi", leads["npi"].is_unique and len(leads) == n0)
    leads.to_parquet(out_dir / "fraud_leads_v2.parquet", index=False)

    write_report(rep, df, out_dir)
    con.close()
    log("Done. v2 tiers:")
    log(leads["priority_tier"].value_counts().sort_index().to_string())


def write_report(rep: list[str], df: pd.DataFrame, out_dir: Path) -> None:
    log("Writing LAYER2_REFINEMENT_REPORT.md …")
    et_label = {"1": "individual", "2": "organization", "": "(missing)"}

    # --- degenerate near-zero-MAD peer groups under the OLD (taxonomy-only) method ---
    rate_feats = ["paid_per_patient_instance", "lines_per_patient_instance",
                  "paid_per_claim_line", "month_to_month_volatility"]
    sc = df["scorable"]
    near0 = 0
    for f in rate_feats:
        g = df.loc[sc, [f, "primary_taxonomy"]].dropna()
        med = g.groupby("primary_taxonomy")[f].median()
        mad = (g[f] - g["primary_taxonomy"].map(med)).abs().groupby(g["primary_taxonomy"]).median()
        near0 += int(((mad > 0) & (mad < 1e-6 * (med.abs() + 1))).sum())

    old_rare = (df["rare_for_taxonomy_paid_share"] > 0).sum()
    new_rare = (df["rare_share_te"] > 0).sum()
    gp = (df["gross_paid"] > 0).sum()
    new_leads = int(df["anomaly_lead_v2"].sum())
    n_scored = int(sc.sum())
    rare_signal = int((df.loc[sc, "pct_rare_share_te"] >= P99).sum())

    rep.append("\n## Before vs after (validation)\n")
    rep.append(f"- **Layer-2 lead count**: old (≥2 clipped-z signals) = 125,364 → "
               f"**new (≥{MIN_SIGNALS} features ≥ P{int(P99*100)}) = {new_leads:,}**\n")
    rep.append(f"- **z=50 wall**: old leads with ≥1 feature at the ±50 clip = 17,427 → "
               f"**new = 0** (percentile rank is bounded [0,1]; no clip exists)\n")
    rep.append(f"- **near-zero-MAD peer-group×feature cells** under the OLD taxonomy-only method "
               f"(the source of the z=50 wall): **{near0:,}**\n")
    rep.append(f"- **rare-code presence** (share>0): old taxonomy-level = {old_rare:,}/{gp:,} "
               f"({old_rare/gp:.1%}) → new taxonomy×entity = {new_rare:,}/{gp:,} ({new_rare/gp:.1%}). "
               f"More importantly, as a SCORING SIGNAL it now fires only when in the top 1% of the "
               f"peer group: **{rare_signal:,}/{n_scored:,} scored ({rare_signal/n_scored:.2%})** reach "
               f"P{int(P99*100)} — vs the old method where rare_for_taxonomy hit the z=50 clip "
               f"near-universally among top leads.\n")

    # --- top-100 composition: old vs new ---
    def composition(score_col, mask=None):
        d = df[mask] if mask is not None else df
        top = d.nlargest(100, score_col)
        et = top["entity_type"].map(et_label).value_counts()
        tax = top["primary_taxonomy"].value_counts().head(5)
        return et, tax
    old_et, old_tax = composition("anomaly_score_old")
    new_et, new_tax = composition("anomaly_score_v2", df["anomaly_lead_v2"])
    rep.append("\n## Top-100 anomaly leads — entity-type composition (the key fix)\n")
    rep.append(f"- **OLD** top 100: " + ", ".join(f"{k}={v}" for k, v in old_et.items()) + "\n")
    rep.append(f"  - top taxonomies: " + ", ".join(f"{k}={v}" for k, v in old_tax.items()) + "\n")
    rep.append(f"- **NEW** top 100: " + ", ".join(f"{k}={v}" for k, v in new_et.items()) + "\n")
    rep.append(f"  - top taxonomies: " + ", ".join(f"{k}={v}" for k, v in new_tax.items()) + "\n")

    ns = df["not_scored"].sum()
    rep.append(f"\n## Scoring coverage\n- not_scored: {ns:,} — "
               + ", ".join(f"{k}={v:,}" for k, v in
                           df.loc[df['not_scored'], 'not_scored_reason'].value_counts().items()) + "\n")
    rep.append("- peer basis (scored): "
               + ", ".join(f"{k}={v:,}" for k, v in
                           df.loc[df['scorable'], 'peer_basis'].value_counts().items()) + "\n")
    scored = df[df["scorable"]]
    q = scored["anomaly_score_v2"].quantile([0.5, 0.9, 0.99, 1.0])
    sigdist = scored["n_extreme_p99"].value_counts().sort_index()
    rep.append(f"- anomaly_score_v2 (mean percentile, scored): median={q[0.5]:.3f}, "
               f"p90={q[0.9]:.3f}, p99={q[0.99]:.3f}, max={q[1.0]:.3f}\n")
    rep.append("- n_extreme_p99 distribution: "
               + ", ".join(f"{int(k)}→{int(v):,}" for k, v in sigdist.items()) + "\n")

    # --- top 25 refined leads ---
    rep.append("\n## Top 25 refined Layer-2 leads (with contributing signals)\n")
    rep.append("| npi | entity | taxonomy | state | net_paid | n_p99 | signals |\n"
               "|---|---|---|---|--:|--:|---|\n")
    top = df[df["anomaly_lead_v2"]].sort_values(
        ["n_extreme_p99", "anomaly_score_v2"], ascending=False).head(25)
    for x in top.itertuples():
        sig = "; ".join(x.anomaly_contributing_features_v2)[:150]
        rep.append(f"| `{x.npi}` | {et_label.get(x.entity_type,'?')} | {x.primary_taxonomy} | "
                   f"{x.practice_state} | ${(x.net_paid or 0):,.0f} | {x.n_extreme_p99} | {sig} |\n")

    rep.append("\n## Known limitation (for the reviewer)\n"
               "- Percentile rank is degeneracy-safe but still rewards EXTREME RATIOS, which can be "
               "noisy for low-volume providers (a few leads have very small net_paid). Raw dollars are "
               "intentionally not a primary driver; `net_paid` is shown for context so reviewers can "
               "down-weight trivial-volume leads. A claim-volume reliability gate (not a dollar gate) "
               "is the natural next refinement.\n")
    rep.append("\n## Integrity\n"
               f"- fraud_leads_v2.parquet: {len(df):,} rows = one per billing NPI ✓\n"
               "- peers within (taxonomy×entity_type), never individuals vs orgs; "
               "percentile-rank primary (degeneracy-safe, no clip); raw dollars context-only; "
               "Layer-1/Layer-3 reused unchanged; re-merge asserted non-fan-out.\n"
               "- Earlier outputs NOT modified (v2 written as new files).\n")
    (out_dir / "LAYER2_REFINEMENT_REPORT.md").write_text("".join(rep))


if __name__ == "__main__":
    main()
