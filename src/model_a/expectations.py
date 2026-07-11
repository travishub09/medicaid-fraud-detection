"""
expectations.py — the standing calculation-integrity feedback loop.

Hard asserts crash a run when a join fans out or dollars don't conserve. This
module covers the OTHER failure class: the calculation that runs to completion
and quietly produces something wrong — a subscore flooded to one constant, a
share above 1.0, a percentile family whose covered rows aren't uniform, a
broadcast that reached 2% of the rows it should have. Every export run emits
an ``EXPECTATIONS_REPORT.md`` listing each check as PASS / WARN / FAIL with
the observed value, the expectation it violated, and what to go look at —
the go-back-and-fix-later queue.

It is a REPORTER, never a gate: findings are filed, the run is never killed
(a broken feature documented beats a run that dies at 2am). Each of these
checks is generalized from a real bug this platform actually shipped:

  constant/floor columns        the fillna(0) subscore floor
  tie-flooded top decile        subscore_saturation_fraud's 1.0x lift
  all-NaN produced features     the DMEPOS wrong-layout silent skip
  out-of-range shares           ratio bugs (inf/negative dollars)
  non-uniform peer percentiles  fake cohorts ("None" cells, mixed scales)
  label-count drift             widened-label wiring regressions

CLI (also runs standalone on any delivered matrix):
    python -m src.model_a.expectations --matrix provider_features_for_model.parquet \
        --manifest feature_manifest.json [--out EXPECTATIONS_REPORT.md]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

SEV_ORDER = {"FAIL": 0, "WARN": 1, "PASS": 2}
# derived CHANGES of bounded quantities are signed — [-1, 1], not [0, 1]
_SIGNED_SUFFIXES = ("_yoy", "_slope", "_delta")
# families whose values are shares/scores bounded to [0, 1]
_BOUNDED_HINTS = ("_share", "subscore_", "__peerpct", "_pct", "_index",
                  "_residual", "concentration", "_corr")
_TIE_FLOOD_SHARE = 0.5        # >50% of the top decile at ONE identical value
_UNIFORM_MEAN_BAND = (0.35, 0.65)   # peerpct mean far outside → fake cohort
_MIN_COVERED = 200            # below this, distribution checks are noise
# columns that are percentiles by construction and must be ~uniform on covered
# rows — the v3 concept columns join the __peerpct/_residual families here
# (the max-of-percentiles miscalibration class of bug)
_PERCENTILE_COLS = {"concentration", "payment_intensity", "service_intensity",
                    "specialty_mismatch", "temporal"}


def _finding(findings: list, severity: str, scope: str, check: str,
             observed: str, expected: str, action: str) -> None:
    findings.append({"severity": severity, "scope": scope, "check": check,
                     "observed": observed, "expected": expected,
                     "action": action})


def run_expectations(matrix: pd.DataFrame, manifest: dict,
                     min_covered: int = _MIN_COVERED) -> pd.DataFrame:
    """All checks over a delivered matrix + manifest → findings frame
    (severity / scope / check / observed / expected / action)."""
    f: list[dict] = []
    feature_cols = list(dict.fromkeys(
        manifest.get("raw_feature_cols", []) + manifest.get("peerpct_cols", [])
        + manifest.get("subscore_cols", [])))
    present = [c for c in feature_cols if c in matrix.columns]

    # 0. manifest ↔ matrix agreement: promised columns that never materialized
    missing = sorted(set(feature_cols) - set(present))
    if missing:
        _finding(f, "FAIL", "manifest", "promised_columns_missing",
                 f"{len(missing)} absent (e.g. {', '.join(missing[:4])})",
                 "every manifest feature exists in the matrix",
                 "the producing stage was skipped or renamed its output — check "
                 "SOURCES_REPORT for the silent skip")

    for c in present:
        vals = pd.to_numeric(matrix[c], errors="coerce")
        covered = vals.dropna()
        n_cov = len(covered)

        # 1. produced but empty: the column exists and carries nothing
        if n_cov == 0:
            _finding(f, "FAIL", c, "all_nan_feature", "0 non-null values",
                     "a produced feature carries data for someone",
                     "its source loaded zero usable rows (wrong layout / bad "
                     "join key) — the DMEPOS class of failure")
            continue

        # 2. range: bounded families must live in [0, 1]; nothing may be ±inf
        if np.isinf(covered).any():
            _finding(f, "FAIL", c, "infinite_values",
                     f"{int(np.isinf(covered).sum())} ±inf",
                     "no infinities (guard the zero denominators)",
                     "a ratio divided by an unguarded zero — find the division")
        if any(h in c for h in _BOUNDED_HINTS) and not c.endswith(_SIGNED_SUFFIXES):
            bad = int(((covered < -1e-9) | (covered > 1 + 1e-9)).sum())
            if bad:
                _finding(f, "FAIL", c, "share_out_of_bounds",
                         f"{bad} values outside [0,1] "
                         f"(min {covered.min():.3g}, max {covered.max():.3g})",
                         "shares/scores/percentiles live in [0,1]",
                         "the calculation's numerator/denominator pairing is "
                         "wrong for some rows")
        elif c.endswith(_SIGNED_SUFFIXES) and any(h in c for h in _BOUNDED_HINTS):
            # a CHANGE of a share is signed: yoy lives in [-1, 1], a slope in
            # [-1, 1] per year — the run-4 false-alarm class (12 spurious FAILs)
            bad = int(((covered < -1 - 1e-9) | (covered > 1 + 1e-9)).sum())
            if bad:
                _finding(f, "FAIL", c, "delta_out_of_bounds",
                         f"{bad} values outside [-1,1] "
                         f"(min {covered.min():.3g}, max {covered.max():.3g})",
                         "a share's change lives in [-1,1]",
                         "the delta was computed on something that is not a share")

        if n_cov < min_covered:
            continue                      # distribution checks below need mass

        # 3. constant / floor: one value dominating the covered rows
        top_share = float(covered.value_counts(normalize=True).iloc[0])
        if covered.nunique() == 1:
            _finding(f, "FAIL", c, "constant_feature",
                     f"single value {covered.iloc[0]:.4g} across {n_cov:,} rows",
                     "a feature varies", "dead input — the subscore-floor class "
                     "of bug; check for imputation or a degenerate transform")
        elif top_share > 0.95 and not c.startswith("subscore_"):
            _finding(f, "WARN", c, "near_constant_feature",
                     f"{top_share:.0%} of covered rows share one value",
                     "meaningful variation", "verify this is a genuine rare flag, "
                     "not a flooded default")

        # 4. tie-flooded top decile: the slice that becomes leads is one value
        k = max(1, n_cov // 10)
        top = covered.nlargest(k)
        flood = float(top.value_counts(normalize=True).iloc[0])
        if flood > _TIE_FLOOD_SHARE and top.nunique() > 1:
            _finding(f, "WARN", c, "tie_flooded_top_decile",
                     f"{flood:.0%} of the top decile is one identical value",
                     "the top decile discriminates",
                     "ranking on this column draws arbitrarily among ties — "
                     "the saturation-subscore class of bug")

        # 5. peer percentiles: covered rows should be ~uniform (mean ≈ 0.5)
        if c.endswith("__peerpct") or c.endswith("_residual") or c in _PERCENTILE_COLS:
            mu = float(covered.mean())
            if not (_UNIFORM_MEAN_BAND[0] <= mu <= _UNIFORM_MEAN_BAND[1]):
                _finding(f, "WARN", c, "percentile_not_uniform",
                         f"mean {mu:.2f} over {n_cov:,} covered rows",
                         "a within-cohort percentile averages ≈ 0.5",
                         "cohorts are broken for this metric (fake cells, "
                         "mixed ranking pools, or a one-sided clip upstream)")

    # 6. label sanity
    label = manifest.get("label")
    if label and label in matrix.columns:
        pos = int(pd.to_numeric(matrix[label], errors="coerce").fillna(0).sum())
        n_pos_manifest = manifest.get("n_positives")
        base = pos / max(len(matrix), 1)
        if pos == 0:
            _finding(f, "FAIL", label, "no_positives", "0 positives",
                     "the PU label carries positives",
                     "label join failed — nothing can train")
        elif n_pos_manifest and abs(pos - int(n_pos_manifest)) > 0.02 * int(n_pos_manifest):
            _finding(f, "WARN", label, "label_count_drift",
                     f"{pos:,} vs manifest {int(n_pos_manifest):,}",
                     "matrix and manifest agree on positives",
                     "a label source was added/dropped between stages")
        if base > 0.05:
            _finding(f, "WARN", label, "base_rate_implausible",
                     f"{base:.1%} positive", "rare-event base rate (≈0.3%)",
                     "the label column may be mis-joined or fanned out")

    # 7. join coverage: org broadcast reach
    if "org_node_id" in matrix.columns:
        share_org = float(matrix["org_node_id"].notna().mean())
        if share_org < 0.5:
            _finding(f, "WARN", "org_node_id", "org_join_coverage",
                     f"{share_org:.0%} of NPIs joined to an org",
                     "most NPIs resolve to a canonical org",
                     "the npi_to_org crosswalk is stale vs this matrix")

    out = pd.DataFrame(f, columns=["severity", "scope", "check",
                                   "observed", "expected", "action"])
    if len(out):
        out = out.sort_values("severity", key=lambda s: s.map(SEV_ORDER),
                              kind="stable").reset_index(drop=True)
    return out


def write_report(findings: pd.DataFrame, out_path: str | Path,
                 n_checked: int | None = None) -> str:
    fails = int((findings["severity"] == "FAIL").sum()) if len(findings) else 0
    warns = int((findings["severity"] == "WARN").sum()) if len(findings) else 0
    lines = ["# EXPECTATIONS_REPORT — where the data or a calculation "
             "didn't behave as designed\n\n",
             f"_{fails} FAIL / {warns} WARN. A FAIL is a calculation or join "
             "that ran but produced something structurally wrong; a WARN is a "
             "distribution that deserves a look. This is the go-back-and-fix "
             "queue — the run itself was not stopped._\n\n"]
    if n_checked is not None:
        lines.append(f"_Checked {n_checked} feature columns._\n\n")
    if not len(findings):
        lines.append("Every check passed. (Checks: promised-columns, all-NaN, "
                     "range, constants/floors, tie-flooded top deciles, "
                     "percentile uniformity, label sanity, join coverage.)\n")
    else:
        lines.append("| severity | column/scope | check | observed | expected "
                     "| go look at |\n|---|---|---|---|---|---|\n")
        for r in findings.itertuples():
            lines.append(f"| {r.severity} | `{r.scope}` | {r.check} | "
                         f"{r.observed} | {r.expected} | {r.action} |\n")
    text = "".join(lines)
    Path(out_path).write_text(text, encoding="utf-8")
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", default="EXPECTATIONS_REPORT.md")
    args = ap.parse_args()
    matrix = pd.read_parquet(args.matrix)
    manifest = json.loads(Path(args.manifest).read_text())
    findings = run_expectations(matrix, manifest)
    write_report(findings, args.out)
    print(findings.to_string(index=False) if len(findings)
          else "all expectations passed")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
