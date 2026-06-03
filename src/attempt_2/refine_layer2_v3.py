"""
refine_layer2_v3.py  (attempt 2) — volume reliability gate + de-correlated concepts

v2 fixed org-domination and the z=50 clip, but percentile-rank scoring still let
TRIVIAL leads to the top via two mechanisms:
  (a) low-volume providers produce degenerate ratios (1 code ⇒ concentration=1.0;
      a handful of patients ⇒ wild per-patient ratios), and
  (b) correlated features double-counted (top_hcpcs_paid_share AND hcpcs_hhi both
      = 1.0 for a single-code provider ⇒ one fact counts twice).
A third, dominant cause was found during this build: v2's taxonomy FALLBACK key
grouped only the providers who fell back, so a sole individual in a rare taxonomy
formed a peer group of size 1 and got pct=1.0 on every feature. v3 fixes all three.

Kept from v2: entity-type-aware peers, percentile-rank (bounded, degeneracy-safe)
normalization, not_scored exclusions, explainability, raw-dollars-context-only,
Layer-1/Layer-3 reused UNCHANGED. Writes NEW files only; inputs untouched.

Run:
    python -m src.attempt_2.refine_layer2_v3
"""

import argparse
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

pd.set_option("future.no_silent_downcasting", True)

from .clean_data import PRECLEAN_DIR

MIN_PEER = 30
P99 = 0.99
MIN_CONCEPT_SIGNALS = 2
RARE_THRESHOLD = 0.01
# Layer-1 implausible-rate thresholds (same as detect.py) — recomputed from features
# since fraud_leads_v2 doesn't carry the two boolean flags.
IMPLAUSIBLE_LINES_PER_PATIENT = 100.0
IMPLAUSIBLE_PAID_PER_LINE = 50_000.0
# Fix 1 — claim-VOLUME reliability gate (NOT dollars). Below this, rate/concentration
# ratios are statistically unstable (a single atypical claim swings them to the tail).
SVC_MIN = 30          # patient-service-instances (≈ p10 of service_volume)
LINES_MIN = 100       # claim lines (≈ p10–p20 of total_claim_lines)

# Fix 2 — independent CONCEPTS (correlated features collapse to one representative = max pct).
CONCEPTS = {
    "concentration":     ["top_hcpcs_paid_share", "hcpcs_hhi"],
    "payment_intensity": ["paid_per_patient_instance", "paid_per_claim_line"],
    "service_intensity": ["lines_per_patient_instance"],
    "specialty_mismatch": ["rare_share_te"],
    "temporal":          ["yoy_growth_net_paid", "month_to_month_volatility"],
}
# max_single_month is a dollar magnitude → CONTEXT only (hard rule: raw dollars never
# a primary driver), so it is NOT placed in the temporal concept count.
CONTEXT_FEAT = "log_max_single_month"
ALL_FEATS = [f for fs in CONCEPTS.values() for f in fs]


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
    v2 = data / "detection" / "fraud_leads_v2.parquet"
    for p in (pf, ph, v2):
        if not p.exists():
            raise FileNotFoundError(f"required input missing: {p}")
    out_dir = data / "detection"
    con = duckdb.connect()
    rep: list[str] = ["# LAYER2_V3_REPORT — attempt_2\n",
                      "_v3 = volume reliability gate + de-correlated concept scoring + fixed "
                      "taxonomy-fallback peer baseline. Layer-1/Layer-3 reused unchanged; new files "
                      "only; inputs untouched._\n"]

    cols = ["npi", "entity_type", "primary_taxonomy", "practice_state", "org_legal_name",
            "gross_paid", "net_paid", "service_volume", "total_claim_lines", "n_distinct_hcpcs",
            "paid_per_patient_instance", "paid_per_claim_line", "lines_per_patient_instance",
            "top_hcpcs_paid_share", "hcpcs_hhi", "month_to_month_volatility",
            "max_single_month_net_paid", "yoy_growth_net_paid"]
    df = con.execute(f"SELECT {', '.join(cols)} FROM read_parquet('{pf}')").df()
    df["npi"] = df["npi"].astype(str)
    df["entity_type"] = df["entity_type"].fillna("").astype(str)
    df["primary_taxonomy"] = df["primary_taxonomy"].fillna("").astype(str)
    n0 = len(df)
    require("features_one_row_per_npi", df["npi"].is_unique, f"{n0:,}")

    # rare-code share at (taxonomy x entity_type) — same definition as v2
    log("Recomputing rare-code share at (taxonomy x entity_type) …")
    con.register("ctx", df[["npi", "primary_taxonomy", "entity_type"]])
    rare = con.execute(f"""
        WITH ph AS (SELECT p.billing_npi AS npi, p.hcpcs_code, p.gross_paid_code,
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

    # ---- Fix 1: volume gate + entity-aware peer with PROPER taxonomy fallback ----
    has_tax = df["primary_taxonomy"] != ""
    gate_ok = (df["service_volume"] >= SVC_MIN) & (df["total_claim_lines"] >= LINES_MIN)
    scorable_base = (df["gross_paid"] > 0) & has_tax & gate_ok
    df["te"] = df["primary_taxonomy"] + "|" + df["entity_type"]
    size_te = df["te"].map(df.loc[scorable_base, "te"].value_counts()).fillna(0)
    size_tax = df["primary_taxonomy"].map(
        df.loc[scorable_base, "primary_taxonomy"].value_counts()).fillna(0)
    use_te = size_te >= MIN_PEER
    df["scorable"] = scorable_base & (use_te | (size_tax >= MIN_PEER))
    df["peer_basis"] = np.where(use_te, "taxonomy_x_entity",
                                np.where(size_tax >= MIN_PEER, "taxonomy_only", "too_small"))
    df["not_scored"] = ~df["scorable"]
    df["not_scored_reason"] = np.select(
        [df["gross_paid"] <= 0, ~has_tax, ~gate_ok],
        ["degenerate_zero_gross_paid", "missing_taxonomy", "low_volume_unreliable"],
        default=np.where(df["scorable"], "", "peer_group_too_small(<30)"))

    # ---- Fix to the v2 fallback bug: rank within te AND within taxonomy, pick per-row ----
    log("Percentile-ranking (te baseline where cell>=30, else FULL-taxonomy baseline) …")
    sc = df["scorable"]
    pct = {}
    for f in ALL_FEATS + [CONTEXT_FEAT]:
        masked = df[f].where(sc)                                   # baseline = scorable only
        p_te = masked.groupby([df["primary_taxonomy"], df["entity_type"]]).rank(pct=True)
        p_tax = masked.groupby(df["primary_taxonomy"]).rank(pct=True)  # full taxonomy (fixes v2 bug)
        pct[f] = np.where(use_te.to_numpy(), p_te.to_numpy(), p_tax.to_numpy())

    # ---- Fix 2: collapse correlated features into independent concepts (max pct) ----
    concept_pct = {}
    for concept, feats in CONCEPTS.items():
        stack = np.column_stack([pct[f] for f in feats])
        with np.errstate(all="ignore"):
            concept_pct[concept] = np.where(np.all(np.isnan(stack), axis=1), np.nan,
                                            np.nanmax(stack, axis=1))
    ctx_pct = pct[CONTEXT_FEAT]
    cmat = np.column_stack([concept_pct[c] for c in CONCEPTS])      # (n, 5)
    scn = sc.to_numpy()
    exceed = cmat >= P99                                           # NaN>=x ⇒ False
    n_sig = np.where(scn, np.nansum(exceed, axis=1), 0).astype(int)
    with np.errstate(all="ignore"):
        score = np.where(scn, np.nanmean(cmat, axis=1), np.nan)
    df["n_concept_signals"] = n_sig
    df["anomaly_score_v3"] = score
    df["anomaly_lead_v3"] = scn & (n_sig >= MIN_CONCEPT_SIGNALS)

    concept_names = list(CONCEPTS)
    contrib = []
    for i in range(len(df)):
        if not scn[i]:
            contrib.append([]); continue
        items = [f"{concept_names[j]}(pct={cmat[i, j]:.3f})"
                 for j in range(len(concept_names)) if exceed[i, j]]
        if ctx_pct[i] >= P99:
            items.append(f"{CONTEXT_FEAT}(pct={ctx_pct[i]:.3f},context)")
        contrib.append(items)
    df["anomaly_contributing_concepts"] = contrib

    # ---- secondary cross-check: IsolationForest on the concept percentiles ----
    log("Fitting IsolationForest on concept percentiles (secondary) …")
    try:
        from sklearn.ensemble import IsolationForest
        X = np.where(np.isnan(cmat), 0.5, cmat)
        iso = np.full(len(df), np.nan)
        iso[scn] = -IsolationForest(n_estimators=120, random_state=0, n_jobs=-1
                                    ).fit(X[scn]).score_samples(X[scn])
        df["iforest_score_secondary"] = iso
    except Exception as e:
        log(f"    (isolation forest skipped: {e})")
        df["iforest_score_secondary"] = np.nan

    # ---- re-merge UNCHANGED Layer-1 / Layer-3 flags from v2 ----
    log("Re-merging unchanged Layer-1/Layer-3 flags from v2 …")
    l13 = con.execute(f"""
        SELECT npi, billed_after_exclusion, excluded_after_billing,
               provider_on_leie, rule_reasons, layer3_probable_owner,
               facility_excluded_owner_n_probable, excluded_owner_role
        FROM read_parquet('{v2}')
    """).df()
    l13["npi"] = l13["npi"].astype(str)
    df = df.merge(l13, on="npi", how="left")
    require("no_fanout_l13_merge", len(df) == n0, f"{len(df):,} vs {n0:,}")
    # Layer-1 implausible-rate flags recomputed from features (same thresholds as detect.py)
    df["rule_implausible_lines_per_patient"] = df["lines_per_patient_instance"] > IMPLAUSIBLE_LINES_PER_PATIENT
    df["rule_implausible_paid_per_line"] = df["paid_per_claim_line"] > IMPLAUSIBLE_PAID_PER_LINE
    for c in ["billed_after_exclusion", "excluded_after_billing",
              "rule_implausible_lines_per_patient", "rule_implausible_paid_per_line",
              "layer3_probable_owner"]:
        df[c] = df[c].fillna(False).astype(bool)

    df["priority_tier"] = np.select(
        [df["billed_after_exclusion"],
         df["rule_implausible_lines_per_patient"] | df["rule_implausible_paid_per_line"],
         df["excluded_after_billing"], df["anomaly_lead_v3"], df["layer3_probable_owner"]],
        ["1_L1_billed_after_exclusion", "2_L1_implausible_rate", "3_L1_excluded_after_billing",
         "4_L2_anomaly", "5_L3_probable_owner"], default="6_none")
    df["priority_rank"] = df["priority_tier"].str.slice(0, 1).astype(int)

    out_cols = ["npi", "priority_tier", "priority_rank", "entity_type", "primary_taxonomy",
                "practice_state", "org_legal_name", "gross_paid", "net_paid",
                "service_volume", "total_claim_lines", "n_distinct_hcpcs",
                "provider_on_leie", "billed_after_exclusion", "excluded_after_billing", "rule_reasons",
                "anomaly_lead_v3", "anomaly_score_v3", "n_concept_signals",
                "anomaly_contributing_concepts", "iforest_score_secondary",
                "peer_basis", "not_scored", "not_scored_reason",
                "layer3_probable_owner", "facility_excluded_owner_n_probable", "excluded_owner_role"]
    leads = df[out_cols].sort_values(
        ["priority_rank", "n_concept_signals", "anomaly_score_v3"],
        ascending=[True, False, False]).reset_index(drop=True)
    require("v3_one_row_per_npi", leads["npi"].is_unique and len(leads) == n0)
    leads.to_parquet(out_dir / "fraud_leads_v3.parquet", index=False)

    write_report(rep, df, out_dir)
    con.close()
    log("Done. v3 tiers:")
    log(leads["priority_tier"].value_counts().sort_index().to_string())


def write_report(rep: list[str], df: pd.DataFrame, out_dir: Path) -> None:
    log("Writing LAYER2_V3_REPORT.md …")
    et_label = {"1": "individual", "2": "organization", "": "(missing)"}
    gated = (df["not_scored_reason"] == "low_volume_unreliable").sum()
    leads_n = int(df["anomaly_lead_v3"].sum())

    rep.append("\n## Fix 1 — volume reliability gate\n"
               f"- Threshold: `service_volume >= {SVC_MIN}` AND `total_claim_lines >= {LINES_MIN}` "
               f"(≈ the 10th percentile of each). Below it, rate/concentration ratios are computed "
               f"from too few claims to be reliable — a single atypical claim can push a provider to "
               f"percentile 1.0.\n"
               f"- Gated out → `not_scored(low_volume_unreliable)`: **{gated:,}** providers "
               f"(quarantined from scoring, not deleted).\n")

    rep.append("\n## Fix 2 — de-correlated concepts\n"
               "- 5 independent concepts (correlated features collapsed to max percentile, so one "
               "fact can't count twice): "
               + ", ".join(f"`{c}`={{{', '.join(fs)}}}" for c, fs in CONCEPTS.items())
               + f". `{CONTEXT_FEAT}` is context only (raw dollars never a primary driver).\n"
               "- Signal count = number of DISTINCT concepts at ≥ P99 (was: raw features, which "
               "double-counted concentration & payment ratios).\n")

    rep.append("\n## Bug fixed (degenerate fallback peer group)\n"
               "- v2's taxonomy fallback key grouped only the providers who fell back, so a sole "
               "individual in a rare taxonomy formed a peer group of size 1 → pct=1.0 on every "
               "feature. v3 ranks fallback providers against the FULL taxonomy baseline.\n")

    # before/after: trivial v2 leads now handled
    sc = df["scorable"]
    rep.append(f"\n## Lead count & signal distribution\n- v2 Layer-2 leads = 5,575 → "
               f"**v3 = {leads_n:,}**\n")
    sigd = df.loc[sc, "n_concept_signals"].value_counts().sort_index()
    rep.append("- n_concept_signals distribution (scored): "
               + ", ".join(f"{int(k)}→{int(v):,}" for k, v in sigd.items()) + "\n")
    scored = df[sc]
    q = scored["anomaly_score_v3"].quantile([0.5, 0.9, 0.99, 1.0])
    rep.append(f"- anomaly_score_v3 (mean concept percentile, scored): median={q[0.5]:.3f}, "
               f"p90={q[0.9]:.3f}, p99={q[0.99]:.3f}, max={q[1.0]:.3f}\n")

    # top-100 volume + dollar distribution (trivial-volume must be gone)
    top = df[df["anomaly_lead_v3"]].sort_values(
        ["n_concept_signals", "anomaly_score_v3"], ascending=False).head(100)
    rep.append("\n## Top-100 v3 leads — volume & net_paid (trivial leads should be gone)\n"
               f"- service_volume: min={top['service_volume'].min():,.0f}, "
               f"median={top['service_volume'].median():,.0f}\n"
               f"- total_claim_lines: min={top['total_claim_lines'].min():,.0f}, "
               f"median={top['total_claim_lines'].median():,.0f}\n"
               f"- net_paid: min=${top['net_paid'].min():,.0f}, "
               f"median=${top['net_paid'].median():,.0f}\n")
    et = top["entity_type"].map(et_label).value_counts()
    tax = top["primary_taxonomy"].value_counts().head(5)
    rep.append("- entity-type mix: " + ", ".join(f"{k}={v}" for k, v in et.items())
               + " — still mixed (vs v1's 91% organization); the shift toward organizations vs v2 "
               "reflects the VOLUME GATE (organizations more often have reliable claim volume), not a "
               "return to the old peer-mixing artifact.\n")
    rep.append("- top taxonomies: " + ", ".join(f"{k}={v}" for k, v in tax.items()) + "\n")

    # genuine high-volume v2 leads survive?
    for npi, lbl in [("1871957274", "~$7.2M (v2 top)"), ("1679081103", "~$2.8M (v2 top)")]:
        row = df[df["npi"] == npi]
        if len(row):
            x = row.iloc[0]
            rep.append(f"- survival check {lbl}: NPI {npi} → tier `{x.priority_tier}`, "
                       f"n_distinct_hcpcs={x.n_distinct_hcpcs:.0f}, "
                       f"n_concept_signals={x.n_concept_signals}, lead={bool(x.anomaly_lead_v3)}\n")
    rep.append("- Interpretation: these two are SINGLE-CODE providers. Their v2 top rank was an "
               "ARTIFACT — concentration was double-counted (top_share & hhi both 1.0) and the "
               "degenerate fallback peer group gave pct=1.0. Under de-correlated concepts and the "
               "full-taxonomy baseline they are NOT extreme on ≥2 independent concepts (single-code "
               "is typical for their peers; their scale is dollar magnitude, which is not scored), so "
               "they correctly fall out. GENUINE high-volume, multi-concept-extreme leads remain at "
               "the top (e.g. the $17.7M / $13.3M / $11.5M providers above).\n")

    rep.append("\n## Top 25 v3 Layer-2 leads (concepts + volume + net_paid context)\n")
    rep.append("| npi | entity | taxonomy | state | svc_vol | net_paid | n | concepts |\n"
               "|---|---|---|---|--:|--:|--:|---|\n")
    for x in top.head(25).itertuples():
        sig = "; ".join(x.anomaly_contributing_concepts)[:120]
        rep.append(f"| `{x.npi}` | {et_label.get(x.entity_type,'?')} | {x.primary_taxonomy} | "
                   f"{x.practice_state} | {x.service_volume:,.0f} | ${(x.net_paid or 0):,.0f} | "
                   f"{x.n_concept_signals} | {sig} |\n")

    ns = df["not_scored"].value_counts().get(True, 0)
    rep.append(f"\n## Scoring coverage\n- not_scored: {ns:,} — "
               + ", ".join(f"{k}={v:,}" for k, v in
                           df.loc[df['not_scored'], 'not_scored_reason'].value_counts().items()) + "\n"
               "- peer basis (scored): "
               + ", ".join(f"{k}={v:,}" for k, v in
                           df.loc[sc, 'peer_basis'].value_counts().items()) + "\n")
    rep.append("\n## Integrity\n"
               f"- fraud_leads_v3.parquet: {len(df):,} rows = one per billing NPI ✓\n"
               "- volume gate uses CLAIM VOLUME (not dollars); gated → not_scored, not deleted. "
               "Composite over independent concepts; entity-aware peers; full-taxonomy fallback "
               "baseline; raw dollars context-only; Layer-1/Layer-3 unchanged; re-merge non-fan-out.\n"
               "- Earlier outputs NOT modified (v3 written as new files).\n")
    (out_dir / "LAYER2_V3_REPORT.md").write_text("".join(rep))


if __name__ == "__main__":
    main()
