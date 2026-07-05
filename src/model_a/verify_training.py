"""
verify_training.py — audit a TRAINED model's artifacts against the contract.

Repo-ization of the scratchpad ``verify_travis.py`` (adversarial-review item),
plus the audit the handoff PROMISES: "no subscore_<scheme> should outrank its
own raw feature / __peerpct in gain importance." Run it on whatever the modeler
sends back (feature importance, train/test assignment) and get findings instead
of a debate.

Checks (each skip-missing — run with whatever artifacts you have):

  leakage_in_importance     any leakage_hard / leakage_adjacent column carrying
                            nonzero importance → FAIL (the 0.991-AUC
                            ownership-integrity class of inflation)
  forbidden_convenience     anomaly_score / signals_tripped / priority columns
                            as model inputs → FAIL (circular by construction)
  subscore_outranks_raw     a scheme's subscore beats every raw input that
                            feeds it → WARN (the transform is doing the work,
                            not the signal — section K)
  group_split_integrity     the same group_id on both sides of the train/test
                            split → FAIL (related-entity leakage)
  negative_composition      negatives all sitting at zero anomaly signals →
                            WARN (the circular-negatives scheme; pair with
                            retrospective.py's ablation to size the damage)

CLI:
    python -m src.model_a.verify_training --importance feature_importance.csv \
        --manifest feature_manifest.json [--assignments train_test.csv \
        --matrix provider_features_for_model.parquet] --out VERIFY_REPORT.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .scheme_subscores import DEFAULT_SCHEME_WEIGHTS

FORBIDDEN = ("anomaly_score", "anomaly_pct", "signals_tripped", "priority_tier",
             "priority_rank", "anomaly_score_v3", "anomaly_lead_v3",
             "n_concept_signals", "iforest_score_secondary")


def _finding(rows, severity, check, detail):
    rows.append({"severity": severity, "check": check, "detail": detail})


def verify_importance(importance: pd.DataFrame, manifest: dict) -> list[dict]:
    rows: list[dict] = []
    fcol = "feature" if "feature" in importance.columns else importance.columns[0]
    icol = "importance" if "importance" in importance.columns else importance.columns[1]
    imp = dict(zip(importance[fcol].astype(str),
                   pd.to_numeric(importance[icol], errors="coerce").fillna(0.0)))

    leak = set(manifest.get("leakage_hard", []) + manifest.get("leakage_adjacent", []))
    hot = sorted((c for c in leak if imp.get(c, 0) > 0), key=lambda c: -imp[c])
    if hot:
        _finding(rows, "FAIL", "leakage_in_importance",
                 f"{len(hot)} leakage column(s) carry importance "
                 f"(worst: {', '.join(hot[:3])}) — the score is inflated; retrain "
                 "with both leakage tiers dropped")
    bad = [c for c in FORBIDDEN if imp.get(c, 0) > 0]
    if bad:
        _finding(rows, "FAIL", "forbidden_convenience",
                 f"detector-derived columns trained on: {', '.join(bad)} — "
                 "circular by construction")

    # section K, delivered: a subscore must not beat every raw input feeding it
    for scheme, wmap in DEFAULT_SCHEME_WEIGHTS.items():
        sub = f"subscore_{scheme}"
        if imp.get(sub, 0) <= 0:
            continue
        raws = [w for c in wmap
                for w in (imp.get(c, 0), imp.get(f"{c}__peerpct", 0))]
        if raws and imp[sub] > max(raws):
            _finding(rows, "WARN", "subscore_outranks_raw",
                     f"{sub} outranks every raw input that feeds it — the 0-1 "
                     "transform is doing the work, not the signal (section K); "
                     "prefer the raw + __peerpct columns")
    if not rows:
        _finding(rows, "PASS", "importance_contract",
                 "no leakage, no convenience columns, no subscore inversions")
    return rows


def verify_split(assignments: pd.DataFrame, matrix: pd.DataFrame,
                 manifest: dict) -> list[dict]:
    rows: list[dict] = []
    idc = next((c for c in ("provider_id", "npi") if c in assignments.columns), None)
    pile = next((c for c in ("pile", "split", "fold") if c in assignments.columns), None)
    gcols = manifest.get("group_cols") or []
    if not idc or not pile or not gcols or gcols[0] not in matrix.columns:
        return rows
    a = assignments[[idc, pile]].copy()
    a[idc] = a[idc].astype(str)
    m = matrix[["npi", gcols[0]]].copy()
    m["npi"] = m["npi"].astype(str)
    j = a.merge(m, left_on=idc, right_on="npi", how="inner").dropna(subset=[gcols[0]])
    sides = j.groupby(gcols[0])[pile].nunique()
    straddle = int((sides > 1).sum())
    if straddle:
        _finding(rows, "FAIL", "group_split_integrity",
                 f"{straddle:,} group_id(s) straddle train/test — related entities "
                 "leak across the split; use group-aware CV")
    else:
        _finding(rows, "PASS", "group_split_integrity",
                 f"no group straddles the split ({len(sides):,} groups checked)")

    lab = next((c for c in ("label", manifest.get("label", "")) if c in assignments.columns), None)
    if lab and "anomaly_score" in matrix.columns:
        neg = assignments[pd.to_numeric(assignments[lab], errors="coerce") == 0]
        negm = matrix[matrix["npi"].astype(str).isin(neg[idc].astype(str))]
        anom = pd.to_numeric(negm["anomaly_score"], errors="coerce").fillna(0)
        if len(anom) and (anom == 0).mean() > 0.99:
            _finding(rows, "WARN", "negative_composition",
                     "negatives are ~all zero-anomaly providers — the circular "
                     "selection scheme; run retrospective.py's fair-negatives "
                     "ablation to size the inflation")
    return rows


def write_report(rows: list[dict], out_path: str | Path) -> str:
    fails = sum(r["severity"] == "FAIL" for r in rows)
    warns = sum(r["severity"] == "WARN" for r in rows)
    lines = [f"# VERIFY_TRAINING — {fails} FAIL / {warns} WARN\n\n",
             "| severity | check | detail |\n|---|---|---|\n"]
    for r in rows:
        lines.append(f"| {r['severity']} | {r['check']} | {r['detail']} |\n")
    text = "".join(lines)
    Path(out_path).write_text(text, encoding="utf-8")
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--importance", required=True, help="feature,importance csv")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--assignments", default=None,
                    help="train/test csv (provider_id, pile, label)")
    ap.add_argument("--matrix", default=None,
                    help="the training matrix (for group/negative checks)")
    ap.add_argument("--out", default="VERIFY_REPORT.md")
    args = ap.parse_args()
    manifest = json.loads(Path(args.manifest).read_text())
    rows = verify_importance(pd.read_csv(args.importance), manifest)
    if args.assignments and args.matrix:
        rows += verify_split(pd.read_csv(args.assignments, dtype=str),
                             pd.read_parquet(args.matrix), manifest)
    print(write_report(rows, args.out))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
