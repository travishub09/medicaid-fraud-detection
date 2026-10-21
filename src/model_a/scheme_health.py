"""
scheme_health.py — per-scheme calc-integrity + a run-to-run regression alarm.

The recurring failure mode on this platform is SILENT scheme degradation: a
source file gets renamed or swapped (the year-suffixed Part-B skip, the DMEPOS
summary-vs-detail swap), an input column stops being produced, and the affected
scheme quietly runs at partial strength or vanishes — with no error, just a
weaker matrix. EXPECTATIONS_REPORT catches degenerate COLUMNS; this catches
degenerate SCHEMES, and it remembers the last run so a drop is flagged before
the next one ships.

For every scheme in DEFAULT_SCHEME_WEIGHTS it checks, from the built matrix:

  wiring   each weighted input: is it actually reaching the subscore? (a v3/
           graph/analytics feature by its own name, or an adapter feature by its
           __peerpct). weight_present = share of the scheme's total weight whose
           inputs made it in.
  health   the subscore column: coverage, is it constant/dead, tie-flooded.
  status   HEALTHY / DEGRADED (partial inputs) / DORMANT (no inputs — usually
           un-procured data) / BROKEN (subscore present but constant → a
           miscalculation or a degenerate input).

``compare_to_baseline`` diffs against the previous run's scheme_health.json and
returns REGRESSIONS: a scheme that lost weight, lost coverage, or dropped status
since last time — the "a source silently disappeared" alarm.

  # standalone on a delivered matrix
  python -m src.model_a.scheme_health --matrix m.parquet --manifest fm.json \
      --baseline prev_scheme_health.json --out SCHEME_HEALTH.md
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .scheme_subscores import DEFAULT_SCHEME_WEIGHTS
from .provider_features_export import (V3_CONCEPTS, GRAPH_FEATURES,
                                       ANALYTICS_FEATURES)

_RAW_FED = set(V3_CONCEPTS) | set(GRAPH_FEATURES) | set(ANALYTICS_FEATURES)
COVERAGE_DROP_ALARM = 0.20        # >20% relative coverage loss vs baseline = regression
NEAR_CONSTANT = 0.98              # one value shared by >= this share of covered rows


def _input_reaches(col: str, matrix_cols: set) -> tuple[bool, str]:
    """Would input ``col`` reach the subscore, and under what column name?
    Raw-fed features (v3/graph/analytics + the graph-normalized *_norm) enter by
    their own name; everything else is an adapter feature fed as __peerpct."""
    if col in _RAW_FED or col.endswith("_norm"):
        return (col in matrix_cols, col)
    pk = f"{col}__peerpct"
    return (pk in matrix_cols, pk)


def _label_auc(y: np.ndarray, s: np.ndarray) -> tuple[float, int]:
    """Rank AUC of subscore ``s`` vs binary label ``y`` on covered (non-null s)
    rows; returns (auc, n_covered_positives). Mid-rank ties."""
    m = ~np.isnan(s)
    y, s = y[m], s[m]
    n1, n0 = int(y.sum()), int((y == 0).sum())
    if n1 == 0 or n0 == 0:
        return float("nan"), n1
    _, inv, cnt = np.unique(s, return_inverse=True, return_counts=True)
    csum = np.cumsum(cnt)
    avg = {i: (csum[i] - (cnt[i] - 1) / 2) for i in range(len(cnt))}
    ranks = np.array([avg[i] for i in inv])
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)), n1


FCA_SIGNAL_AUC = 0.58          # the band the operator's question table called "real signal"
FCA_MIN_POS = 20


def forward_label_vector(matrix: pd.DataFrame, forward: pd.DataFrame | None
                         ) -> tuple[np.ndarray | None, np.ndarray | None]:
    """(y, keep) for a prospective_label / forward-ruler CSV against the matrix:
    y = 1 for is_prospective_positive rows, keep = False for providers already
    known at the freeze (was_excluded_pre_cutoff), which the ruler never scores.
    Both None when no usable forward file is given."""
    if forward is None or not len(forward) or "npi" not in forward.columns \
            or "is_prospective_positive" not in forward.columns:
        return None, None
    fl = forward.copy()
    fl["npi"] = fl["npi"].astype(str)
    pos = set(fl.loc[pd.to_numeric(fl["is_prospective_positive"], errors="coerce").fillna(0) == 1, "npi"])
    drop = set()
    if "was_excluded_pre_cutoff" in fl.columns:
        drop = set(fl.loc[pd.to_numeric(fl["was_excluded_pre_cutoff"], errors="coerce").fillna(0) == 1, "npi"])
    npi = matrix["npi"].astype(str)
    return npi.isin(pos).to_numpy().astype(int), (~npi.isin(drop)).to_numpy()


# docs/FREEZE_DESIGN_NOTE_2026-10-21.md rule 3: a feature admitted from a later
# edition needs a leak audit = "the forward-ruler AUC implausibly above the in-time
# AUC". A scheme that separates the FUTURE cases far better than the cases already
# known at the freeze is reading something it should not be able to see.
LEAK_GAP = 0.15


def leak_suspect(label_auc, fca_auc, n_label_pos, n_fca_pos) -> bool:
    if label_auc is None or fca_auc is None or not n_label_pos or not n_fca_pos:
        return False
    if n_label_pos < FCA_MIN_POS or n_fca_pos < FCA_MIN_POS:
        return False
    return (float(fca_auc) - float(label_auc)) >= LEAK_GAP and float(fca_auc) >= 0.70


def fca_verdict(auc, n_pos) -> str:
    if auc is None or n_pos is None or n_pos < FCA_MIN_POS:
        return "thin"
    if auc >= FCA_SIGNAL_AUC:
        return "signal"
    if auc < 0.42:
        return "inverted"
    return "coin-flip"


def audit_scheme_health(matrix: pd.DataFrame, manifest: dict | None = None,
                        weights: dict | None = None,
                        forward: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per-scheme calc integrity + separation. ``forward`` (a prospective_label /
    FCA-ruler CSV) adds ``fca_auc`` / ``n_fca_pos`` / ``fca_verdict``: the per-scheme
    AUC on the FORWARD FCA ruler, the label the business is graded on (2026-10-14,
    docs/MODEL_QUESTIONS §1: the exclusion-label AUC alone cannot say whether a scheme
    is noise or whether the exclusion label is blind to it)."""
    weights = weights or DEFAULT_SCHEME_WEIGHTS
    cols = set(matrix.columns)
    n = len(matrix)
    y_fca, keep_fca = forward_label_vector(matrix, forward)
    # optional empirical direction check: a scheme's subscore should point the
    # SAME way as fraud. If it fires on the clean (AUC well below 0.5 on enough
    # known positives), that is either a direction bug OR the label being blind
    # to that scheme (billing schemes vs the exclusion label). Either way, flag
    # it for a look rather than trust it silently.
    label = (manifest or {}).get("label") or "provider_on_exclusion"
    yv = None
    if label in matrix.columns:
        yv = pd.to_numeric(matrix[label], errors="coerce").fillna(0).to_numpy().astype(int)
    rows = []
    for scheme, wmap in weights.items():
        total_w = sum(wmap.values()) or 1.0
        present_w, present_inputs, missing_inputs = 0.0, [], []
        for c, w in wmap.items():
            ok, name = _input_reaches(c, cols)
            if ok:
                present_w += w
                present_inputs.append(c)
            else:
                missing_inputs.append(c)
        weight_present = present_w / total_w
        sub = f"subscore_{scheme}"
        cov, constant, tie_flood = None, None, None
        label_auc, n_pos, direction = None, None, ""
        fca_auc, n_fca_pos = None, None
        if sub in cols:
            s = pd.to_numeric(matrix[sub], errors="coerce")
            covered = s.dropna()
            cov = round(len(covered) / n, 4) if n else 0.0
            if len(covered):
                top = covered.value_counts(normalize=True).iloc[0]
                constant = bool(covered.nunique() <= 1)
                tie_flood = round(float(top), 3)
            if yv is not None and not constant:
                auc, np_ = _label_auc(yv, s.to_numpy(float))
                n_pos = np_
                if not np.isnan(auc):
                    label_auc = round(auc, 3)
                    # only judge direction with enough known positives in coverage
                    if np_ >= 20:
                        if auc < 0.42:
                            direction = "INVERTED?"
                        elif auc < 0.48:
                            direction = "weak/inverse"
            if y_fca is not None and not constant:
                sv = s.to_numpy(float)[keep_fca]
                fauc, fpos = _label_auc(y_fca[keep_fca], sv)
                n_fca_pos = int(fpos)
                fca_auc = None if np.isnan(fauc) else round(fauc, 3)
        # status
        if sub not in cols or not present_inputs:
            status = "DORMANT"
        elif constant:
            status = "BROKEN"
        elif direction == "INVERTED?":
            status = "CHECK_DIRECTION"
        elif weight_present < 0.999:
            status = "DEGRADED"
        else:
            status = "HEALTHY"
        rows.append({
            "scheme": scheme, "status": status,
            "weight_present": round(weight_present, 3),
            "n_inputs_present": len(present_inputs),
            "n_inputs_total": len(wmap),
            "missing_inputs": ";".join(sorted(missing_inputs)),
            "subscore_coverage": cov,
            "subscore_constant": constant,
            "subscore_top_value_share": tie_flood,
            "label_auc": label_auc,
            "n_label_pos": n_pos,
            "direction": direction,
            "fca_auc": fca_auc,
            "n_fca_pos": n_fca_pos,
            "fca_verdict": (fca_verdict(fca_auc, n_fca_pos) if y_fca is not None
                            and sub in cols and not constant else ""),
            "leak_suspect": leak_suspect(label_auc, fca_auc, n_pos, n_fca_pos),
        })
    order = {"BROKEN": 0, "CHECK_DIRECTION": 1, "DEGRADED": 2, "DORMANT": 3,
             "HEALTHY": 4}
    df = pd.DataFrame(rows)
    return df.sort_values(["status", "scheme"],
                          key=lambda s: s.map(order) if s.name == "status" else s
                          ).reset_index(drop=True)


def compare_to_baseline(current: pd.DataFrame, baseline: pd.DataFrame | None) -> list[dict]:
    """Regressions vs the previous run: a scheme that lost weight, lost coverage,
    or dropped status. Returns [] when there is no baseline or nothing regressed."""
    if baseline is None or not len(baseline):
        return []
    rank = {"HEALTHY": 4, "DEGRADED": 3, "DORMANT": 2, "CHECK_DIRECTION": 1,
            "BROKEN": 0}
    b = baseline.set_index("scheme")
    out = []
    for r in current.itertuples():
        if r.scheme not in b.index:
            continue
        prev = b.loc[r.scheme]
        # status drop (HEALTHY -> anything worse, or into BROKEN)
        if rank.get(r.status, 3) < rank.get(prev["status"], 3):
            out.append({"scheme": r.scheme, "kind": "status_drop",
                        "was": prev["status"], "now": r.status})
        # weight loss (an input silently stopped being produced)
        if r.weight_present < float(prev.get("weight_present", 0)) - 1e-6:
            out.append({"scheme": r.scheme, "kind": "weight_loss",
                        "was": float(prev["weight_present"]), "now": r.weight_present})
        # coverage collapse (a source shrank)
        pc, cc = prev.get("subscore_coverage"), r.subscore_coverage
        if pc and cc is not None and float(pc) > 0 and cc < float(pc) * (1 - COVERAGE_DROP_ALARM):
            out.append({"scheme": r.scheme, "kind": "coverage_drop",
                        "was": float(pc), "now": cc})
    return out


def to_markdown(df: pd.DataFrame, regressions: list[dict]) -> str:
    n_broken = int((df["status"] == "BROKEN").sum())
    n_degraded = int((df["status"] == "DEGRADED").sum())
    n_dir = int((df["status"] == "CHECK_DIRECTION").sum())
    L = ["# SCHEME_HEALTH — per-scheme calc integrity + regression alarm", ""]
    L.append(f"_{n_broken} BROKEN, {n_dir} CHECK_DIRECTION, {n_degraded} DEGRADED, "
             f"{int((df['status']=='DORMANT').sum())} DORMANT, "
             f"{int((df['status']=='HEALTHY').sum())} HEALTHY. BROKEN = the "
             "subscore is constant (a miscalculation or a dead input); "
             "CHECK_DIRECTION = the subscore fires on the CLEAN not the fraud "
             "(AUC < 0.42 on 20+ known positives) — a possible inversion, OR the "
             "label being blind to that scheme; DEGRADED = running on only part "
             "of its intended inputs; DORMANT = no inputs present (usually "
             "un-procured data)._")
    if regressions:
        L.append("")
        L.append("## REGRESSIONS SINCE LAST RUN — verify before shipping")
        for r in regressions:
            L.append(f"- **{r['scheme']}** {r['kind']}: was {r['was']} -> now {r['now']}")
        L.append("\n_A regression usually means a source file was renamed, "
                 "swapped, or dropped between runs. Check the SOURCES_REPORT for "
                 "that scheme's inputs before trusting this matrix._")
    has_fca = "fca_auc" in df.columns and df["fca_auc"].notna().any()
    L.append("")
    if has_fca:
        L.append(f"_FCA AUC = the subscore's separation on the FORWARD FCA ruler (cases announced "
                 f"after the freeze; providers known at the freeze dropped), the label the "
                 f"business is graded on. verdict: signal >= {FCA_SIGNAL_AUC}, coin-flip, inverted "
                 f"< 0.42, thin < {FCA_MIN_POS} covered positives. A scheme that is coin-flip on "
                 "BOTH labels with 20+ positives on each is a prune candidate for the next "
                 "pre-registration; a scheme thin on the FCA ruler is unjudged, not noise._")
        L.append("")
        L.append("| scheme | status | weight present | inputs | coverage | label AUC | dir | FCA AUC | FCA pos | FCA verdict | missing inputs |")
        L.append("|---|---|--:|--:|--:|--:|:-:|--:|--:|:-:|---|")
    else:
        L.append("| scheme | status | weight present | inputs | coverage | label AUC | dir | missing inputs |")
        L.append("|---|---|--:|--:|--:|--:|:-:|---|")
    for r in df.itertuples():
        la = getattr(r, "label_auc", None)
        line = (f"| {r.scheme} | {r.status} | {r.weight_present:.0%} | "
                f"{r.n_inputs_present}/{r.n_inputs_total} | "
                f"{('%.1f%%' % (100*r.subscore_coverage)) if r.subscore_coverage is not None else '-'} | "
                f"{la if la is not None else '-'} | "
                f"{getattr(r, 'direction', '') or ''} | ")
        if has_fca:
            fa = getattr(r, "fca_auc", None)
            fp = getattr(r, "n_fca_pos", None)
            line += (f"{fa if fa is not None and fa == fa else '-'} | "
                     f"{int(fp) if fp is not None and fp == fp else '-'} | "
                     f"{getattr(r, 'fca_verdict', '') or ''} | ")
        L.append(line + f"{r.missing_inputs} |")
    return "\n".join(L)


def write_health(matrix: pd.DataFrame, manifest: dict, out_dir: Path,
                 baseline_path: Path | None = None,
                 forward: pd.DataFrame | None = None) -> dict:
    """Write SCHEME_HEALTH.md + scheme_health.json; return a summary dict with
    any regressions. The json becomes the next run's baseline."""
    df = audit_scheme_health(matrix, manifest, forward=forward)
    baseline = None
    bp = baseline_path or (out_dir / "scheme_health.json")
    if bp.exists():
        try:
            baseline = pd.DataFrame(json.loads(bp.read_text())["schemes"])
        except Exception:
            baseline = None
    regressions = compare_to_baseline(df, baseline)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "SCHEME_HEALTH.md").write_text(to_markdown(df, regressions),
                                              encoding="utf-8")
    (out_dir / "scheme_health.json").write_text(json.dumps({
        "schemes": df.to_dict("records"), "regressions": regressions,
    }, indent=2, default=str), encoding="utf-8")
    return {"n_broken": int((df["status"] == "BROKEN").sum()),
            "n_degraded": int((df["status"] == "DEGRADED").sum()),
            "n_check_direction": int((df["status"] == "CHECK_DIRECTION").sum()),
            "regressions": regressions}


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--baseline", default=None,
                    help="prior scheme_health.json for the regression alarm")
    ap.add_argument("--out", default="SCHEME_HEALTH.md")
    ap.add_argument("--future-label", default=None,
                    help="the forward ruler CSV (future_bans_after_<cut>__fca36.csv): adds the "
                         "per-scheme AUC on the FCA ruler + a verdict column")
    ap.add_argument("--json-out", default=None,
                    help="also write the table as json (the runner's scheme_health.json refresh)")
    args = ap.parse_args()
    m = pd.read_parquet(args.matrix)
    manifest = json.loads(Path(args.manifest).read_text())
    fl = pd.read_csv(args.future_label, dtype=str) if args.future_label else None
    df = audit_scheme_health(m, manifest, forward=fl)
    baseline = None
    if args.baseline and Path(args.baseline).exists():
        baseline = pd.DataFrame(json.loads(Path(args.baseline).read_text())["schemes"])
    reg = compare_to_baseline(df, baseline)
    report = to_markdown(df, reg)
    Path(args.out).write_text(report, encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "schemes": df.to_dict("records"), "regressions": reg,
        }, indent=2, default=str), encoding="utf-8")
    print(report)
    if fl is not None and "fca_verdict" in df.columns:
        vc = df["fca_verdict"].value_counts()
        print("\nFCA-ruler verdicts: " + ", ".join(f"{k} {v}" for k, v in vc.items()))
    if reg:
        print(f"\n*** {len(reg)} REGRESSION(S) vs baseline — verify before the next run. ***")


if __name__ == "__main__":
    main()
