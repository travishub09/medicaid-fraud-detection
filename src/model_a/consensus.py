"""
consensus.py — the merged, outlier-robust view of every model run's targets.

Thirteen runs taught us that each build has its own personality: v1's thin
feature set, v5's diluted breadth, v3's validated balance. A target that
runs HOT across several differently-built models is not an artifact of any
one build — cross-run agreement is the strongest conviction signal a single
family of models can produce. This module:

  1. scores every run's matrix out-of-fold (the same leak-screened scorer
     the priority list uses) and rolls up to organizations,
  2. joins organizations across runs (org ids are stable across runs that
     share a graph build; name-matching catches the rest),
  3. computes the CONSENSUS score = the MEDIAN dollar-weighted score across
     runs (the median is the outlier removal: one weird run cannot drag a
     target up or down), plus the agreement count (# runs hot at >= 0.5)
     and the per-run score columns so nothing is hidden,
  4. applies the standing filters from the ANCHOR run (the champion):
     scheme-aware case size >= the bar, government screen — filters, never
     sort keys,
  5. writes consensus_priority_list.csv + CONSENSUS.md.

Honest caveat, printed in the report: the runs share data and labels, so
consensus measures ROBUSTNESS, not independence. An independent check needs
a different model family (the supervised LEIE scorer).

  python -m src.model_a.consensus --runs <dirA> <dirB> ... --anchor <dirC>
      [--min-damages 10000000] [--min-runs 2]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def load_run_orgs(run_dir: str | Path) -> pd.DataFrame:
    """Score one run's matrix OOF and roll up to orgs (drop the excluded)."""
    from .company_list import oof_scores, rollup
    d = Path(run_dir)
    matrix = pd.read_parquet(d / "provider_features_for_model.parquet")
    manifest = json.loads((d / "feature_manifest.json").read_text(
        encoding="utf-8"))
    scores = oof_scores(matrix, manifest)
    orgs = rollup(matrix, scores, drop_excluded=True)
    keep = [c for c in ("org_id", "org_name", "score_dollar_weighted",
                        "score_max", "total_billed", "top_schemes",
                        "n_providers") if c in orgs.columns]
    out = orgs[keep].copy()
    out["org_id"] = out["org_id"].astype(str)
    return out


def consensus_from_frames(frames: dict[str, pd.DataFrame],
                          anchor_tag: str,
                          min_runs: int = 2,
                          hot: float = 0.5) -> pd.DataFrame:
    """The pure consensus math over per-run org frames.

    frames: run tag -> org frame (org_id, org_name, score_dollar_weighted,
    score_max, total_billed, top_schemes, n_providers). Joined on org_id.
    Returns one row per org present in >= min_runs runs, with per-run score
    columns dw_<tag>, the median consensus score, agreement count, spread,
    and the anchor run's descriptive columns.
    """
    if anchor_tag not in frames:
        raise SystemExit(f"anchor tag '{anchor_tag}' not among runs "
                         f"{sorted(frames)}")
    merged: pd.DataFrame | None = None
    for tag, f in frames.items():
        cols = f[["org_id", "score_dollar_weighted"]].rename(
            columns={"score_dollar_weighted": f"dw_{tag}"})
        merged = cols if merged is None else merged.merge(
            cols, on="org_id", how="outer")
    dw_cols = [c for c in merged.columns if c.startswith("dw_")]
    for c in dw_cols:
        merged[c] = pd.to_numeric(merged[c], errors="coerce")

    dw = merged[dw_cols]
    merged["runs_present"] = dw.notna().sum(axis=1)
    merged["consensus_score"] = dw.median(axis=1, skipna=True)
    merged["runs_hot"] = (dw >= hot).sum(axis=1)
    merged["score_spread"] = dw.max(axis=1) - dw.min(axis=1)

    anchor = frames[anchor_tag].rename(columns={
        "score_dollar_weighted": "anchor_dw"})
    out = merged.merge(anchor, on="org_id", how="left")
    # names for orgs absent from the anchor run: first run that has one
    missing = out["org_name"].isna() | (out["org_name"].astype(str) == "")
    if missing.any():
        for tag, f in frames.items():
            if tag == anchor_tag:
                continue
            fill = f.set_index("org_id")["org_name"]
            out.loc[missing, "org_name"] = out.loc[missing, "org_id"].map(fill)
            missing = out["org_name"].isna() | (out["org_name"].astype(str) == "")
            if not missing.any():
                break
    out = out[out["runs_present"] >= int(min_runs)]
    return out.sort_values(
        ["consensus_score", "runs_hot", "score_max"],
        ascending=False).reset_index(drop=True)


def build(run_dirs: list[str], anchor: str, out_dir: str | None = None,
          min_damages: float = 10_000_000.0, min_runs: int = 2) -> Path:
    from .case_size import org_case_size
    from .pursuit_dossier import government_mask

    frames: dict[str, pd.DataFrame] = {}
    for rd in run_dirs:
        tag = Path(rd).name.replace("frozen_2023-12", "").strip("_") or \
            Path(rd).name
        print(f"[consensus] scoring {tag} ({rd}) ...")
        frames[tag] = load_run_orgs(rd)
        print(f"[consensus]   {len(frames[tag]):,} orgs")
    anchor_tag = Path(anchor).name.replace("frozen_2023-12", "").strip("_") \
        or Path(anchor).name
    cons = consensus_from_frames(frames, anchor_tag, min_runs=min_runs)

    # anchor-run case sizes + the standing filters (filters, never sorts)
    anchor_matrix = pd.read_parquet(
        Path(anchor) / "provider_features_for_model.parquet")
    sizes = org_case_size(anchor_matrix)
    cons = cons.merge(sizes, on="org_id", how="left",
                      suffixes=("", "_sz"))
    cons["case_size_yr"] = pd.to_numeric(
        cons.get("case_size_yr"), errors="coerce").fillna(0.0)
    gov = government_mask(cons["org_name"])
    n_gov = int(gov.sum())
    n_small = int((cons["case_size_yr"] < float(min_damages)).sum())
    cons = cons[~gov & (cons["case_size_yr"] >= float(min_damages))]
    cons = cons.reset_index(drop=True)
    cons.insert(0, "consensus_rank", cons.index + 1)

    d = Path(out_dir or anchor)
    csv_path = d / "consensus_priority_list.csv"
    cons.to_csv(csv_path, index=False)
    md = d / "CONSENSUS.md"
    dw_cols = [c for c in cons.columns if c.startswith("dw_")]
    lines = [
        "# CONSENSUS PRIORITY LIST — the merged view of every model run", "",
        f"- runs merged: {len(frames)} ({', '.join(sorted(frames))}); "
        f"anchor (filters + case sizes): {anchor_tag}",
        f"- {len(cons):,} organizations present in >= {min_runs} runs, over "
        f"the ${min_damages:,.0f}/yr case-size bar, non-government "
        f"({n_gov:,} government removed; {n_small:,} under the bar removed).",
        "- **consensus_score** = MEDIAN dollar-weighted score across runs "
        "(the median is the outlier removal: one aberrant run cannot drag a "
        "target up or down). **runs_hot** = in how many runs the org's "
        "money ran hot (>= 0.5) — the cross-model agreement count. Per-run "
        "scores ship in the dw_* columns so nothing is hidden.",
        "- Honest caveat: the runs share data and labels, so consensus "
        "measures ROBUSTNESS, not independence. An independent check needs "
        "a different model family (the supervised LEIE scorer).", "",
        "| rank | org | consensus | runs hot / present | "
        + " | ".join(c[3:] for c in dw_cols) + " | case size/yr |",
        "|--:|---|--:|--:|" + "--:|" * len(dw_cols) + "--:|",
    ]
    for _, r in cons.head(30).iterrows():
        per = " | ".join(
            f"{r[c]:.2f}" if pd.notna(r[c]) else "—" for c in dw_cols)
        lines.append(
            f"| {int(r['consensus_rank'])} | {str(r['org_name'])[:42]} "
            f"| {r['consensus_score']:.3f} "
            f"| {int(r['runs_hot'])}/{int(r['runs_present'])} | {per} "
            f"| ${r['case_size_yr']:,.0f} |")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[consensus] {len(cons):,} orgs -> {csv_path}")
    print(f"[consensus] report -> {md}")
    return csv_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", nargs="+", required=True,
                    help="run folders to merge (2+)")
    ap.add_argument("--anchor", required=True,
                    help="run folder whose case sizes/filters apply "
                         "(the champion; '@champion' works)")
    ap.add_argument("--out", default=None,
                    help="output folder (default: the anchor folder)")
    ap.add_argument("--min-damages", type=float, default=10_000_000.0)
    ap.add_argument("--min-runs", type=int, default=2,
                    help="require presence in >= N runs (default 2)")
    args = ap.parse_args()
    from .run_registry import resolve_run_dir
    anchor = resolve_run_dir(args.anchor)
    runs = [resolve_run_dir(r) for r in args.runs]
    if anchor not in runs:
        runs.append(anchor)
    build(runs, anchor, out_dir=args.out,
          min_damages=args.min_damages, min_runs=args.min_runs)


if __name__ == "__main__":
    main()
