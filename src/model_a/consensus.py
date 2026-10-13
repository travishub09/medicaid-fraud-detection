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
  3. SCREENS DEGENERATE RUNS: a run whose dollar-weighted score barely fires
     (its hot count far below the other runs') is an old, thin build, not a
     dissenting opinion — the first consensus carried two such runs
     (v2_clean, frozen_2023-12: hundreds of exact zeros) and they dragged
     every median down; such runs are dropped and named in the report,
  4. computes the CONSENSUS score = the MEDIAN dollar-weighted score across
     the kept runs (the median is the outlier removal: one weird run cannot
     drag a target up or down), plus the agreement count (# runs hot at
     >= 0.5) and the per-run score columns so nothing is hidden,
  5. applies the standing filters from the ANCHOR run (the champion):
     scheme-aware case size >= the bar, government screen — filters, never
     sort keys,
  6. writes consensus_priority_list.csv + CONSENSUS.md.

Honest caveat, printed in the report: the runs share data and labels, so
consensus measures ROBUSTNESS, not independence. An independent check needs
a different model family (the supervised LEIE scorer).

  python -m src.model_a.consensus --runs <dirA> <dirB> ... --anchor <dirC>
      [--min-damages 10000000] [--min-runs 2] [--exclude-runs v2_clean ...]
      [--resolve-names]
  python -m src.model_a.consensus --recompute <consensus_priority_list.csv>
      [--exclude-runs ...]   # re-derive from the per-run dw_* columns, no rescoring
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

UNNAMED_PREFIX = "UNNAMED ORGANIZATION"


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


def run_health(dw: pd.DataFrame, hot: float = 0.5) -> pd.DataFrame:
    """Per-run health over the merged dw_* frame: how often the score fires.

    Columns: run, present, hot, hot_share, zero_share, mean."""
    rows = []
    for c in dw.columns:
        s = pd.to_numeric(dw[c], errors="coerce")
        n = int(s.notna().sum())
        rows.append({"run": c[3:] if c.startswith("dw_") else c,
                     "present": n,
                     "hot": int((s >= hot).sum()),
                     "hot_share": float((s >= hot).sum() / n) if n else 0.0,
                     "zero_share": float((s == 0).sum() / n) if n else 0.0,
                     "mean": float(s.mean()) if n else 0.0})
    return pd.DataFrame(rows)


def screen_runs(dw: pd.DataFrame, hot: float = 0.5,
                min_frac: float = 0.34,
                exclude: list[str] | None = None) -> tuple[list[str], dict[str, str]]:
    """Which dw_* columns to keep. A run is DEGENERATE when its hot count is
    below ``min_frac`` x the median hot count of all runs (its score barely
    fires: an old thin build, not a dissenting model). Explicit ``exclude``
    tags are dropped too. Returns (kept columns, {dropped column: reason})."""
    exclude = {e.strip().lower() for e in (exclude or [])}
    health = run_health(dw, hot).set_index("run")
    med_hot = float(health["hot"].median()) if len(health) else 0.0
    # below ~10 hot orgs per run the counts are too small to call a run
    # degenerate (a 2-vs-6 split is noise, not a dead build) — screen only
    # when the typical run has enough hits to judge by
    if med_hot < 10:
        med_hot = 0.0
    med_mean = float(health["mean"].median()) if len(health) else 0.0
    kept, dropped = [], {}
    for c in dw.columns:
        tag = c[3:] if c.startswith("dw_") else c
        h = health.loc[tag]
        hot_ratio = (h["hot"] / med_hot) if med_hot > 0 else 1.0
        mean_ratio = (h["mean"] / med_mean) if med_mean > 0 else 1.0
        if tag.lower() in exclude:
            dropped[c] = "excluded by --exclude-runs"
        elif hot_ratio < min_frac or mean_ratio < min_frac:
            # either the score rarely runs hot, or it sits near zero for
            # nearly everyone (the first consensus's frozen_2023-12: 9 hot vs
            # 19 but a mean a third of the others', 10% exact zeros)
            dropped[c] = (f"degenerate: {int(h['hot'])} hot vs median "
                          f"{med_hot:.0f}, mean score {h['mean']:.3f} vs median "
                          f"{med_mean:.3f}, {h['zero_share']:.0%} exact zeros — "
                          f"the score barely fires in this build")
        else:
            kept.append(c)
    if len(kept) < 2 and len(dw.columns) >= 2:
        # never let the screen leave fewer than two runs — fall back to all
        kept, dropped = list(dw.columns), {}
    return kept, dropped


def _consensus_math(merged: pd.DataFrame, dw_cols: list[str],
                    hot: float) -> pd.DataFrame:
    dw = merged[dw_cols].apply(pd.to_numeric, errors="coerce")
    merged["runs_present"] = dw.notna().sum(axis=1)
    merged["consensus_score"] = dw.median(axis=1, skipna=True)
    merged["runs_hot"] = (dw >= hot).sum(axis=1)
    merged["score_spread"] = dw.max(axis=1) - dw.min(axis=1)
    return merged


def consensus_from_frames(frames: dict[str, pd.DataFrame],
                          anchor_tag: str,
                          min_runs: int = 2,
                          hot: float = 0.5,
                          min_frac: float = 0.34,
                          exclude: list[str] | None = None,
                          ) -> tuple[pd.DataFrame, dict[str, str]]:
    """The pure consensus math over per-run org frames.

    frames: run tag -> org frame (org_id, org_name, score_dollar_weighted,
    score_max, total_billed, top_schemes, n_providers). Joined on org_id.
    Returns (one row per org present in >= min_runs KEPT runs, with per-run
    score columns dw_<tag> for EVERY run (dropped runs' columns stay visible
    but do not enter the median / agreement count), the anchor run's
    descriptive columns; {dropped dw column: reason}).
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

    kept, dropped = screen_runs(merged[dw_cols], hot=hot, min_frac=min_frac,
                                exclude=exclude)
    merged = _consensus_math(merged, kept, hot)

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
    out = out.sort_values(["consensus_score", "runs_hot", "score_max"],
                          ascending=False).reset_index(drop=True)
    return out, dropped


def flag_absent_from_newest(cons: pd.DataFrame, dw_cols: list[str]) -> pd.DataFrame:
    """Mark orgs the NEWEST run no longer produces.

    dw_* columns are in discovery order (newest run first). An org present in
    older runs but absent from the newest one is usually a resolver artifact
    the newest build corrected — the CHILDREN'S HOSPITAL cross-state name
    collision existed in v3/v4/v5 and was split by v6's state guard. The row
    stays (transparency) but carries `absent_in_newest` = the run tag and a
    visible name suffix so nobody pursues a merged phantom."""
    if not dw_cols:
        return cons
    newest = dw_cols[0]
    out = cons.copy()
    absent = pd.to_numeric(out[newest], errors="coerce").isna()
    out["absent_in_newest"] = np.where(absent, newest[3:], "")
    suffix = f" [absent in {newest[3:]}: likely resolver split — verify]"
    names = out["org_name"].fillna("").astype(str)
    out["org_name"] = np.where(absent & ~names.str.endswith(suffix),
                               names + suffix, names)
    return out


def resolve_unnamed(cons: pd.DataFrame, lookup=None, log=print) -> pd.DataFrame:
    """Fill blank / UNNAMED single-NPI org names from the live NPI Registry.

    An org whose id is org:npi:<npi> and whose name is blank in every matrix
    (the NPPES join missed) is still a real billing entity — the NPI IS the
    identifier. Names ship by operator decision; individuals come back as
    'LAST, FIRST' + a (NPI-1) tag so the reader knows it is a person."""
    if lookup is None:
        from ..ingest_cms.nppes_api import lookup_npi as lookup
    out = cons.copy()
    name = out["org_name"].fillna("").astype(str)
    target = (name.str.startswith(UNNAMED_PREFIX) | (name.str.strip() == "")) & \
        out["org_id"].astype(str).str.startswith("org:npi:")
    n_ok = 0
    n_missing = 0
    if "nppes_status" not in out.columns:
        out["nppes_status"] = ""
    for i in out.index[target]:
        npi = str(out.at[i, "org_id"]).split(":")[-1]
        try:
            rec = lookup(npi)
        except Exception as e:  # noqa: BLE001 — network; keep going
            log(f"[consensus] NPPES lookup failed for {npi}: {e}")
            out.at[i, "nppes_status"] = "lookup_failed"
            continue
        if not rec:
            # the registry answered and has no record: a deactivated /
            # retired NPI that is still in the billing fact. That is the
            # invalid_identity pattern, not a data gap — name it so the row
            # reads as the finding it is (Sept 2026: 10 such orgs over $10M/yr)
            out.at[i, "org_name"] = (f"NPI NOT IN REGISTRY (deactivated?) "
                                     f"{npi}")
            out.at[i, "nppes_status"] = "not_in_registry"
            n_missing += 1
            continue
        out.at[i, "nppes_status"] = f"active:{rec.get('status', '')}"
        if rec.get("org_name"):
            nm = rec["org_name"]
        elif rec.get("last_name"):
            nm = f"{rec['last_name']}, {rec.get('first_name', '')}".strip(", ") + \
                " (NPI-1)"
        else:
            continue
        loc = ", ".join(x for x in (rec.get("city", ""), rec.get("state", "")) if x)
        out.at[i, "org_name"] = nm + (f" [{loc}]" if loc else "")
        n_ok += 1
    log(f"[consensus] resolved {n_ok}/{int(target.sum())} unnamed org(s) via NPPES"
        + (f"; {n_missing} NOT IN REGISTRY (deactivated NPIs still billing — "
           f"invalid_identity leads)" if n_missing else ""))
    return out


def write_report(cons: pd.DataFrame, run_tags: list[str], anchor_tag: str,
                 out_dir: Path, min_runs: int, min_damages: float,
                 n_gov: int | None, n_small: int | None,
                 dropped: dict[str, str]) -> tuple[Path, Path]:
    csv_path = out_dir / "consensus_priority_list.csv"
    cons.to_csv(csv_path, index=False)
    md = out_dir / "CONSENSUS.md"
    dw_cols = [c for c in cons.columns if c.startswith("dw_")]
    kept = [c for c in dw_cols if c not in dropped]
    filt = (f" ({n_gov:,} government removed; {n_small:,} under the bar removed)"
            if n_gov is not None else " (filters inherited from the source list)")
    lines = [
        "# CONSENSUS PRIORITY LIST — the merged view of every model run", "",
        f"- runs scored: {len(run_tags)} ({', '.join(sorted(run_tags))}); "
        f"runs in the consensus: {len(kept)} ({', '.join(c[3:] for c in kept)}); "
        f"anchor (filters + case sizes): {anchor_tag}",
        f"- {len(cons):,} organizations present in >= {min_runs} kept runs, over "
        f"the ${min_damages:,.0f}/yr case-size bar, non-government{filt}.",
        (f"- **Label tier: `{cons['label_tier'].iloc[0]}`** — every run's scorer was "
         f"trained on this FCA tier (fraud-shaped ranking), not the in-matrix PU label."
         if "label_tier" in cons.columns and len(cons)
         and str(cons["label_tier"].iloc[0]) != "all" else
         "- Label: each run's in-matrix PU label (program-integrity ruler)."),
        "- **consensus_score** = MEDIAN dollar-weighted score across the KEPT runs "
        "(the median is the outlier removal: one aberrant run cannot drag a "
        "target up or down). **runs_hot** = in how many kept runs the org's "
        "money ran hot (>= 0.5) — the cross-model agreement count. Per-run "
        "scores ship in the dw_* columns so nothing is hidden, dropped runs included.",
    ]
    if dropped:
        lines.append("- **Runs dropped from the median** (their column still "
                     "shows, greyed by the reason): " + "; ".join(
                         f"`{c[3:]}` — {why}" for c, why in dropped.items()))
    lines += [
        "- Honest caveat: the runs share data and labels, so consensus "
        "measures ROBUSTNESS, not independence. An independent check needs "
        "a different model family (the supervised LEIE scorer).",
        "- Leads for counsel review, never accusations: a high consensus score "
        "means several builds agree the billing pattern is unusual, not that "
        "anything improper occurred.", "",
        "| rank | org | consensus | runs hot / present | "
        + " | ".join(c[3:] + ("*" if c in dropped else "") for c in dw_cols)
        + " | case size/yr |",
        "|--:|---|--:|--:|" + "--:|" * len(dw_cols) + "--:|",
    ]
    for _, r in cons.head(30).iterrows():
        per = " | ".join(
            f"{r[c]:.2f}" if pd.notna(r[c]) else "—" for c in dw_cols)
        lines.append(
            f"| {int(r['consensus_rank'])} | {str(r['org_name'])[:42]} "
            f"| {r['consensus_score']:.3f} "
            f"| {int(r['runs_hot'])}/{int(r['runs_present'])} | {per} "
            f"| ${float(r['case_size_yr']):,.0f} |")
    if dropped:
        lines += ["", "_* = dropped from the median (shown for transparency)._"]
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, md


def _run_tag(rd: str | Path) -> str:
    """frozen_2023-12_v6 -> v6; frozen_2021-12_v7 -> 2021-12_v7 (the freeze
    stays in the tag when it is not the original 2023-12 series)."""
    name = Path(rd).name
    if name.startswith("frozen_2023-12"):
        return name.replace("frozen_2023-12", "").strip("_") or name
    return name.replace("frozen_", "") or name


def _run_cache_path(out_dir: Path, tag: str, label_tier: str) -> Path:
    return out_dir / "_run_cache" / f"{tag}__{label_tier}.parquet"


def build(run_dirs: list[str], anchor: str, out_dir: str | None = None,
          min_damages: float = 10_000_000.0, min_runs: int = 2,
          exclude: list[str] | None = None, min_frac: float = 0.34,
          resolve_names: bool = False, label_tier: str = "all",
          use_cache: bool = True) -> Path:
    from .case_size import org_case_size
    from .pursuit_dossier import government_mask
    from .company_list import default_exclusion_nodes, set_label_policy

    # the output folder exists BEFORE hours of scoring, and every run's org
    # frame is cached the moment it is scored, so a crash at the write step
    # (or a killed window) restarts from the last finished run, not from zero
    d = Path(out_dir or anchor)
    d.mkdir(parents=True, exist_ok=True)
    frames: dict[str, pd.DataFrame] = {}
    for rd in run_dirs:
        tag = _run_tag(rd)
        cache = _run_cache_path(d, tag, label_tier)
        if use_cache and cache.exists():
            frames[tag] = pd.read_parquet(cache)
            frames[tag]["org_id"] = frames[tag]["org_id"].astype(str)
            print(f"[consensus] {tag}: {len(frames[tag]):,} orgs from cache {cache} "
                  f"(delete the file to rescore)")
            continue
        if label_tier != "all":
            # each run trains its scorer on the FCA tier rebuilt from ITS OWN
            # as-of exclusion nodes (dated <= its freeze), so a fraud-shaped
            # consensus never quietly falls back to the revocation label
            ex = default_exclusion_nodes(rd)
            if not ex.exists():
                raise SystemExit(f"--label-tier {label_tier}: exclusion nodes not "
                                 f"found for {rd} at {ex}")
            mj = json.loads((Path(rd) / "feature_manifest.json").read_text(
                encoding="utf-8"))
            cutoff = mj.get("asof_cutoff") or (mj.get("graph_substrate") or {}).get("asof")
            set_label_policy(label_tier, ex, cutoff)
            print(f"[consensus] scoring {tag} ({rd}) on label tier '{label_tier}' "
                  f"(nodes {ex.name}, records <= {cutoff}) ...")
        else:
            print(f"[consensus] scoring {tag} ({rd}) ...")
        frames[tag] = load_run_orgs(rd)
        print(f"[consensus]   {len(frames[tag]):,} orgs")
        if use_cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            frames[tag].to_parquet(cache, index=False)
            print(f"[consensus]   cached -> {cache}")
    set_label_policy("all")
    anchor_tag = _run_tag(anchor)
    cons, dropped = consensus_from_frames(frames, anchor_tag, min_runs=min_runs,
                                         min_frac=min_frac, exclude=exclude)
    cons["label_tier"] = label_tier
    for c, why in dropped.items():
        print(f"[consensus] DROPPED {c[3:]}: {why}")

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
    cons = flag_absent_from_newest(
        cons, [c for c in cons.columns if c.startswith("dw_")])
    cons = cons.reset_index(drop=True)
    cons.insert(0, "consensus_rank", cons.index + 1)
    if resolve_names:
        cons = resolve_unnamed(cons)

    csv_path, md = write_report(cons, list(frames), anchor_tag, d, min_runs,
                                min_damages, n_gov, n_small, dropped)
    print(f"[consensus] {len(cons):,} orgs -> {csv_path}")
    print(f"[consensus] report -> {md}")
    if use_cache:
        print(f"[consensus] per-run caches kept under {d / '_run_cache'} (reused by the "
              f"next build with the same label tier; delete to rescore)")
    return csv_path


def recompute(csv_path: str | Path, out_dir: str | Path | None = None,
              exclude: list[str] | None = None, min_frac: float = 0.34,
              hot: float = 0.5, min_runs: int = 2,
              resolve_names: bool = False, lookup=None) -> Path:
    """Re-derive the consensus from an existing consensus_priority_list.csv.

    The per-run dw_* columns are already there, so a run screen / exclusion /
    name resolution does not need the hour of rescoring. Filters (case size,
    government) were applied when the CSV was written and are inherited."""
    src = Path(csv_path)
    cons = pd.read_csv(src, dtype=str)
    dw_cols = [c for c in cons.columns if c.startswith("dw_")]
    for c in dw_cols:
        cons[c] = pd.to_numeric(cons[c], errors="coerce")
    kept, dropped = screen_runs(cons[dw_cols], hot=hot, min_frac=min_frac,
                                exclude=exclude)
    for c, why in dropped.items():
        print(f"[consensus] DROPPED {c[3:]}: {why}")
    cons = _consensus_math(cons, kept, hot)
    cons = cons[cons["runs_present"] >= int(min_runs)]
    # the government screen is name-based and cheap: re-apply it so a
    # pattern added after the CSV was written (MAYOR AND CITY COUNCIL OF ...)
    # still takes effect on a recompute
    from .pursuit_dossier import government_mask
    gov = government_mask(cons["org_name"].fillna("").astype(str))
    if gov.any():
        print(f"[consensus] government screen removed {int(gov.sum())} row(s) "
              f"on recompute")
        cons = cons[~gov.values]
    cons = flag_absent_from_newest(cons, dw_cols)
    cons["case_size_yr"] = pd.to_numeric(cons.get("case_size_yr"),
                                         errors="coerce").fillna(0.0)
    if "score_max" in cons.columns:
        cons["score_max"] = pd.to_numeric(cons["score_max"], errors="coerce")
    cons = cons.sort_values(["consensus_score", "runs_hot", "score_max"],
                            ascending=False).reset_index(drop=True)
    cons = cons.drop(columns=["consensus_rank"], errors="ignore")
    cons.insert(0, "consensus_rank", cons.index + 1)
    if resolve_names:
        cons = resolve_unnamed(cons, lookup=lookup)
    d = Path(out_dir) if out_dir else src.parent
    d.mkdir(parents=True, exist_ok=True)
    anchor_tag = "inherited from the source list"
    min_damages = float(cons["case_size_yr"].min()) if len(cons) else 0.0
    csv_out, md = write_report(cons, [c[3:] for c in dw_cols], anchor_tag, d,
                               min_runs, min_damages, None, None, dropped)
    print(f"[consensus] {len(cons):,} orgs -> {csv_out}")
    print(f"[consensus] report -> {md}")
    return csv_out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", nargs="+", default=None,
                    help="run folders to merge (2+), or the single word "
                         "'auto' to merge every frozen run in the data "
                         "root's run catalog (src.run_catalog; honors "
                         "MEDICAID_DATA_ROOT)")
    ap.add_argument("--anchor", default=None,
                    help="run folder whose case sizes/filters apply "
                         "(the champion; '@champion' works)")
    ap.add_argument("--recompute", default=None, metavar="CSV",
                    help="re-derive from an existing consensus_priority_list.csv "
                         "(no rescoring): applies the run screen / --exclude-runs "
                         "/ --resolve-names to its dw_* columns")
    ap.add_argument("--out", default=None,
                    help="output folder (default: the anchor folder, or the "
                         "CSV's folder with --recompute)")
    ap.add_argument("--min-damages", type=float, default=10_000_000.0)
    ap.add_argument("--min-runs", type=int, default=2,
                    help="require presence in >= N kept runs (default 2)")
    ap.add_argument("--exclude-runs", nargs="*", default=None,
                    help="run tags to leave out of the median (e.g. v2_clean)")
    ap.add_argument("--min-run-health", type=float, default=0.34,
                    help="drop a run whose hot count is below this fraction of "
                         "the median run's hot count (default 0.34; 0 disables)")
    ap.add_argument("--resolve-names", action="store_true",
                    help="fill blank / UNNAMED single-NPI org names from the live "
                         "NPI Registry (network)")
    ap.add_argument("--no-cache", action="store_true",
                    help="rescore every run even when <out>/_run_cache/<run>__<tier>.parquet exists")
    ap.add_argument("--label-tier", default="all", choices=["all", "hard", "hard+charged", "hard+weak"],
                    help="train every run's scorer on this FCA tier (rebuilt from "
                         "each run's own as-of exclusion nodes) before merging: "
                         "'hard' = a fraud-shaped consensus; 'all' = the in-matrix "
                         "PU label (the pre-v7 behaviour)")
    args = ap.parse_args()

    if args.recompute:
        recompute(args.recompute, out_dir=args.out, exclude=args.exclude_runs,
                  min_frac=args.min_run_health, min_runs=args.min_runs,
                  resolve_names=args.resolve_names)
        return
    if not args.runs or not args.anchor:
        ap.error("--runs and --anchor are required (or use --recompute CSV)")
    from .run_registry import resolve_run_dir
    anchor = resolve_run_dir(args.anchor)
    if len(args.runs) == 1 and args.runs[0].strip().lower() == "auto":
        import os as _os
        from ..run_catalog import cataloged_run_dirs
        root = _os.environ.get("MEDICAID_DATA_ROOT",
                               str(Path.home() / "Desktop/data"))
        runs = cataloged_run_dirs(root, kind="frozen")
        print(f"[consensus] --runs auto: {len(runs)} frozen runs from the "
              "catalog")
        if len(runs) < 2:
            raise SystemExit("catalog found fewer than 2 frozen runs — "
                             "pass folders explicitly")
    else:
        runs = [resolve_run_dir(r) for r in args.runs]
    if anchor not in runs:
        runs.append(anchor)
    build(runs, anchor, out_dir=args.out,
          min_damages=args.min_damages, min_runs=args.min_runs,
          exclude=args.exclude_runs, min_frac=args.min_run_health,
          resolve_names=args.resolve_names, label_tier=args.label_tier,
          use_cache=not args.no_cache)


if __name__ == "__main__":
    main()
