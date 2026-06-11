"""
score.py (model) — score the FULL provider universe with the trained LightGBM
model, then roll predictions up to the company grain.

Scores all 617,062 NPIs from Model/provider_features_scored.parquet — including
the high-anomaly and not_scored providers that were held out of PU training
(that extrapolation is the point: the model ranks the ambiguous population).
Categorical encodings are reapplied from feature_list.json so inference codes
match training exactly; unseen categories become missing.

Company rollup uses detection/tables/npi_to_company_map.parquet (the validated
tracker linkage — NOT re-derived here): per company, max constituent score,
dollar-weighted mean score, constituent counts. Asserts the map covers every
NPI 1:1 and the company join does not fan out.

Writes to ~/Desktop/Data/Model/scores/:
    provider_model_scores.parquet — one row per NPI
    company_model_scores.parquet  — one row per company
    company_model_scores_top500.csv
    MODEL_SCORING_REPORT.md

Run (after train.py):
    python -m src.model.score
"""

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from . import config
from .data import build_feature_matrix, log, require

PROVIDER_CONTEXT_COLS = ["npi", "org_legal_name", "entity_type", "practice_state",
                         "primary_taxonomy", "net_paid", "provider_on_leie",
                         "anomaly_score", "not_scored"]


def load_model(artifacts_dir: Path):
    booster = lgb.Booster(model_file=str(artifacts_dir / "lgbm_leie.txt"))
    spec = json.loads((artifacts_dir / "feature_list.json").read_text())
    return booster, spec


def build_universe_matrix(df: pd.DataFrame, spec: dict) -> pd.DataFrame:
    X = build_feature_matrix(df)
    require("inference features match training feature list",
            list(X.columns) == spec["features"],
            f"{len(X.columns)} vs {len(spec['features'])}")
    for c, cats in spec["categories"].items():
        X[c] = pd.Categorical(df[c], categories=cats)  # unseen -> NaN (missing)
    return X


def rollup_to_company(prov: pd.DataFrame) -> pd.DataFrame:
    nmap = pd.read_parquet(config.NPI_TO_COMPANY_MAP, columns=["npi", "company_id"])
    require("npi_to_company_map covers the universe 1:1",
            len(nmap) == len(prov) and nmap["npi"].is_unique)
    j = prov.merge(nmap, on="npi", how="inner", validate="1:1")
    require("no NPI lost joining the company map", len(j) == len(prov))

    g_all = j.groupby("company_id", sort=False)
    comp = pd.DataFrame({
        "n_npis": g_all["npi"].size(),
        "n_leie_npis": g_all["provider_on_leie"].sum().astype(int),
    })

    # Company scores aggregate RELIABLE constituents only — a company must not
    # be surfaced by one unscoreable shell NPI's extrapolated score.
    r = j[j["score_reliable"]]
    w = r["net_paid"].clip(lower=0)
    r = r.assign(_w=w, _ws=r["model_score"] * w)
    g = r.groupby("company_id", sort=False)
    comp["n_npis_reliable"] = g["npi"].size().reindex(comp.index).fillna(0).astype(int)
    comp["company_model_score_max"] = g["model_score"].max().reindex(comp.index)
    comp["company_model_score_mean"] = g["model_score"].mean().reindex(comp.index)
    wsum = g["_w"].sum().reindex(comp.index)
    # dollar-weighted mean; zero-dollar companies fall back to the simple mean
    comp["company_model_score_wmean"] = (g["_ws"].sum().reindex(comp.index) / wsum).where(
        wsum > 0, comp["company_model_score_mean"])

    roll = pd.read_parquet(config.COMPANY_ROLLUP,
                           columns=["company_id", "company_name", "company_net_paid",
                                    "npi_count", "merge_confidence"])
    require("company_rollup unique per company_id", roll["company_id"].is_unique)
    out = comp.reset_index().merge(roll, on="company_id", how="left", validate="1:1")
    require("company rollup join did not fan out", len(out) == len(comp))
    require("constituent counts match the rollup",
            bool((out["n_npis"] == out["npi_count"]).all()))
    return out.drop(columns=["npi_count"]).sort_values(
        "company_model_score_max", ascending=False).reset_index(drop=True)


def write_report(path, prov, comp, n_features):
    rel = prov[prov["score_reliable"]].sort_values("model_score", ascending=False)
    n_pos_rel = int(rel["provider_on_leie"].sum())
    leie_recall = {k: int(rel.head(k)["provider_on_leie"].sum())
                   for k in (100, 500, 1000, 5000)}
    rec = "\n".join(f"| {k:,} | {h} | {h / n_pos_rel:.1%} |" for k, h in leie_recall.items())
    seg = prov.groupby("segment")["model_score"].describe()[["count", "mean", "50%", "max"]]
    seg_md = "\n".join(f"| {i} | {int(r['count']):,} | {r['mean']:.4f} | "
                       f"{r['50%']:.4f} | {r['max']:.4f} |" for i, r in seg.iterrows())
    top_seg = rel.head(1000)["segment"].value_counts()
    top_md = "\n".join(f"| {i} | {v} |" for i, v in top_seg.items())
    path.write_text(f"""# MODEL SCORING REPORT — full universe, NPI + company grain

Scored {len(prov):,} NPIs with the trained LightGBM ({n_features} features),
rolled up to {len(comp):,} companies via the validated tracker linkage.

## Reliability gate

`score_reliable = NOT not_scored` ({len(rel):,} NPIs pass). The detector's
volume/peer gate doubles as the model's: not_scored providers have null peer
features, so their scores are missing-value extrapolation (median ~1.0 at
median $0 net_paid) and are flagged, never ranked. Company scores aggregate
reliable constituents only.

## Score by population segment

| segment | n | mean | median | max |
|---|---|---|---|---|
{seg_md}

Separation is the design working: LEIE positives score high, confident-clean
negatives score ~0, and the held-out `ambiguous_high_anomaly` population
spreads between — those are the not-yet-caught candidates to triage.

## What's in the reliable top 1,000

| segment | n |
|---|---|
{top_md}

## LEIE positives in the reliable ranking ({n_pos_rel} reliable LEIE NPIs, incl. trained-on)

| top K | LEIE hits | recall |
|---|---|---|
{rec}

Read this with care: most of the top ranks are UNLABELED high-anomaly providers
outscoring known-LEIE ones. Under PU assumptions unlabeled ≠ negative, so low
universe recall@K is not a defect — held-out validation (MODEL_REPORT.md) is
the honest performance measure.

Scores are leads for human review, never determinations of fraud.
""")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--artifacts-dir", type=str, default=str(config.ARTIFACTS_DIR))
    p.add_argument("--out-dir", type=str, default=str(config.SCORES_DIR))
    args = p.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log("[1/4] Loading model + full universe")
    booster, spec = load_model(Path(args.artifacts_dir))
    df = pd.read_parquet(config.SCORED_UNIVERSE_PARQUET)
    require("universe rows match expected", len(df) == config.EXPECTED_UNIVERSE_ROWS,
            f"{len(df):,} vs {config.EXPECTED_UNIVERSE_ROWS:,}")
    require("npi unique in universe", df["npi"].is_unique)

    log("[2/4] Scoring all NPIs")
    X = build_universe_matrix(df, spec)
    score = booster.predict(X)
    pu_npis = set(pd.read_parquet(config.PU_TRAINING_PARQUET, columns=["npi"])["npi"])
    prov = df[PROVIDER_CONTEXT_COLS].copy()
    prov.insert(1, "model_score", score)
    prov["in_pu_training"] = prov["npi"].isin(pu_npis)
    prov["segment"] = np.select(
        [prov["provider_on_leie"], prov["in_pu_training"],
         prov["not_scored"].fillna(True)],
        ["leie_positive", "clean_negative_trained", "ambiguous_not_scored"],
        default="ambiguous_high_anomaly")
    # The detector's volume/peer gate doubles as the model's reliability gate:
    # not_scored providers have null peer features, so their model scores are
    # missing-value extrapolation (median 0.9996 at median $0 net_paid) — never
    # rank them against real predictions.
    prov["score_reliable"] = ~prov["not_scored"].fillna(True)
    prov = prov.sort_values("model_score", ascending=False).reset_index(drop=True)
    prov["rank_reliable"] = np.where(
        prov["score_reliable"], prov["score_reliable"].cumsum(), np.nan)

    log("[3/4] Rolling up to company grain")
    comp = rollup_to_company(prov)

    log("[4/4] Writing outputs")
    prov.to_parquet(out_dir / "provider_model_scores.parquet", index=False)
    comp.to_parquet(out_dir / "company_model_scores.parquet", index=False)
    comp.head(500).to_csv(out_dir / "company_model_scores_top500.csv", index=False)
    write_report(out_dir / "MODEL_SCORING_REPORT.md", prov, comp, len(spec["features"]))
    log(f"    {len(prov):,} provider scores, {len(comp):,} company scores → {out_dir}")


if __name__ == "__main__":
    main()
