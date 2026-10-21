"""
questions_readout.py — the operator's standing questions, answered from the run's
own artifacts every run (2026-10-14).

docs/MODEL_QUESTIONS_2026-10-14.md was answered by a person reading six reports.
This writes QUESTIONS_READOUT.md so the next run answers them itself:

  1. Which schemes are noise?          scheme_health.json (exclusion-label AUC + the FCA-ruler
                                       verdict) + SOURCE_ABLATION's leave-one-out deltas
  2. Are the schemes wired, with the   sources_audit (skipped + why), DEGRADED missing inputs,
     right files?                      DATA_REFRESH (post-cutoff / undated editions)
  3. What do the cases light up?       LABEL_ENRICHMENT headline rows + the learning loop's
                                       near-miss feature requests
  4. Is the data good?                 expectations FAIL / WARN, vintage FAILs, label_audit's
                                       top findings
  5. Is the label complete?            training_label_policy.json, label_audit recall numbers
  6. Is Manus used?                    manus_lanes.json

Every answer names the artifact it came from; a missing artifact is said to be
missing, never guessed. Read-only.

  python -m src.model_a.questions_readout --run-dir <run> [--data-root D]
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd


def _json(p: Path):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _csv(p: Path) -> pd.DataFrame | None:
    try:
        return pd.read_csv(p, dtype=str) if Path(p).exists() else None
    except Exception:  # noqa: BLE001
        return None


def _f(v, nd=3):
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "-"


def q1_schemes(run_dir: Path) -> list[str]:
    sh = _json(run_dir / "scheme_health.json")
    L = ["## 1. Which schemes are noise?", ""]
    if not sh:
        return L + ["_scheme_health.json missing: the export did not write it_"]
    df = pd.DataFrame(sh.get("schemes") or [])
    if not len(df):
        return L + ["_no schemes recorded_"]
    has_fca = "fca_verdict" in df.columns and df["fca_verdict"].fillna("").astype(str).str.len().gt(0).any()
    L.append("| scheme | status | exclusion-label AUC (pos) | FCA-ruler AUC (pos) | verdict |")
    L.append("|---|---|--:|--:|:-:|")
    prune, signal = [], []
    for r in df.itertuples():
        la, lp = getattr(r, "label_auc", None), getattr(r, "n_label_pos", None)
        fa, fp, fv = getattr(r, "fca_auc", None), getattr(r, "n_fca_pos", None), getattr(r, "fca_verdict", "") or ""
        L.append(f"| {r.scheme} | {r.status} | {_f(la)} ({int(lp) if lp == lp and lp is not None else '-'}) | "
                 f"{_f(fa)} ({int(fp) if fp == fp and fp is not None else '-'}) | {fv or '-'} |")
        if fv == "signal":
            signal.append(r.scheme)
        try:
            if fv in ("coin-flip", "inverted") and la is not None and float(la) < 0.58 and lp and float(lp) >= 20:
                prune.append(r.scheme)
        except (TypeError, ValueError):
            pass
    L.append("")
    if has_fca:
        L.append(f"**Signal on the FCA ruler:** {', '.join(signal) or 'none'}. **Prune candidates** (coin-flip on BOTH "
                 f"labels with 20+ positives on each): {', '.join(prune) or 'none'}. A prune is a new pre-registration; "
                 "cross-check SOURCE_ABLATION's leave-one-out delta for the family before dropping it.")
    else:
        L.append("_The FCA-ruler verdict column is empty: scheme_health was not run with --future-label "
                 "(measure_run.sh step 1)._")
    sa = run_dir / "SOURCE_ABLATION.md"
    if sa.exists():
        txt = sa.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"\|\s*group\s*\|.*?\n\|[-| ]+\n((?:\|.*\n){1,30})", txt, re.I)
        if m:
            L += ["", "Source-family leave-one-out (SOURCE_ABLATION.md, first rows):", "", "```",
                  *m.group(1).strip().splitlines()[:12], "```"]
    else:
        L.append("_SOURCE_ABLATION.md not in this run folder (MEASURE_ABLATION=0 or the step failed)._")
    return L


def q2_wiring(run_dir: Path) -> list[str]:
    man = _json(run_dir / "feature_manifest.json") or {}
    L = ["## 2. Are the schemes wired, with the right files?", ""]
    audit = man.get("sources_audit") or []
    used = [r for r in audit if r.get("status") == "used"]
    skipped = [r for r in audit if r.get("status") != "used"]
    L.append(f"{len(used)} sources used, {len(skipped)} skipped (SOURCES_REPORT.md).")
    for r in skipped:
        L.append(f"- skipped `{r.get('source')}`: {str(r.get('detail', ''))[:140]}")
    sh = _json(run_dir / "scheme_health.json")
    if sh:
        deg = [s for s in sh.get("schemes") or [] if s.get("status") in ("DEGRADED", "DORMANT", "BROKEN")]
        if deg:
            L.append("")
            L.append("Schemes short of inputs (SCHEME_HEALTH.md):")
            for s in deg:
                L.append(f"- {s.get('scheme')} ({s.get('status')}): missing {s.get('missing_inputs') or 'n/a'}")
    dr = _json(run_dir / "data_refresh.json")
    if dr:
        L.append("")
        L.append(f"Editions (DATA_REFRESH.md): {dr.get('n_post_cutoff', 0)} post-cutoff, {dr.get('n_undated', 0)} undated, "
                 f"{dr.get('n_skipped', 0)} skipped; downloads due now: {', '.join(dr.get('due_now') or []) or 'none'}.")
    else:
        L.append("_DATA_REFRESH.md not written (measure_run.sh step)._")
    fp = man.get("feature_partition") or {}
    unm = fp.get("unmapped") if isinstance(fp, dict) else None
    if unm:
        L.append(f"- trainable columns mapped to no source: {len(unm)} (the gate should have raised)")
    dc = man.get("dropped_constant") or []
    if dc:
        L.append(f"- constant columns dropped from training: {len(dc)} ({', '.join(map(str, dc[:8]))}{'...' if len(dc) > 8 else ''})")
    return L


def q3_enrichment(run_dir: Path) -> list[str]:
    L = ["## 3. What do the forward cases light up?", ""]
    le = _csv(run_dir / "label_enrichment.csv")
    if le is None or not len(le):
        L.append("_LABEL_ENRICHMENT not written (measure_run.sh step 3)._")
    else:
        d = le.copy()
        for c in ("lift", "p_tail", "n_pos", "supported"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
        top = d[(d["supported"] == 1) & (d["p_tail"] < 0.01)
                & ~d["value"].isin(["(blank)", "(unknown)", "(not scored)", "rest"])]
        top = top.sort_values(["lift", "n_pos"], ascending=[False, False]).head(15)
        if len(top):
            L.append("| dimension | value | positives | lift | p |")
            L.append("|---|---|--:|--:|--:|")
            for r in top.itertuples():
                L.append(f"| {r.dimension} | {r.value} | {int(r.n_pos)} | {r.lift:.2f} | {float(r.p_tail):.1e} |")
        else:
            L.append("_no enrichment row passed support + p < 0.01_")
    ll = _json(run_dir / "learning_loop.json")
    nm = (ll or {}).get("near_misses") or {}
    reqs = nm.get("feature_requests") or nm.get("recurring") or []
    if reqs:
        L.append("")
        L.append("Near-miss feature requests (LEARNING_LOOP.md): " + ", ".join(
            (f"{r.get('feature')} ({r.get('n')})" if isinstance(r, dict) else str(r)) for r in reqs[:8]))
    return L


def q4_data(run_dir: Path) -> list[str]:
    man = _json(run_dir / "feature_manifest.json") or {}
    L = ["## 4. Is the data good?", ""]
    ex = man.get("expectations") or {}
    if isinstance(ex, dict) and ex:
        L.append(f"Calc integrity (EXPECTATIONS_REPORT.md): {ex.get('n_fail', ex.get('fail', '-'))} FAIL, "
                 f"{ex.get('n_warn', ex.get('warn', '-'))} WARN.")
    ri = _json(run_dir / "run_issues.json")
    if ri:
        items = ri if isinstance(ri, list) else (ri.get("issues") or [])
        fails = [i for i in items if str(i.get("severity", "")).upper() == "FAIL"]
        L.append(f"Ledger (RUN_ISSUES.md): {len(fails)} FAIL, {len(items) - len(fails)} other.")
        for i in fails[:8]:
            L.append(f"- FAIL {i.get('area')}:{i.get('subject')}:{i.get('check')}: {str(i.get('finding', ''))[:120]}")
    la = _json(run_dir / "label_audit.json")
    if la:
        f = la.get("findings") or []
        L.append("")
        L.append(f"Label audit (LABEL_AUDIT.md): {len(f)} findings; the top:")
        for x in f[:6]:
            L.append(f"- [{x.get('severity')}] {str(x.get('finding', ''))[:160]}")
    else:
        L.append("_LABEL_AUDIT.md not in this run folder (measure_run.sh step)._")
    return L


def q5_label(run_dir: Path) -> list[str]:
    L = ["## 5. Is the label complete?", ""]
    tp = _json(run_dir / "training_label_policy.json")
    if tp:
        L.append(f"Training label: policy {tp.get('label_tier')}, {tp.get('n_positives')} positives "
                 f"({tp.get('n_doj_positives')} DOJ-case, {tp.get('n_charged_positives', 0)} charged, "
                 f"{tp.get('n_commercial_positives', 0)} commercial).")
    for tag, name in (("", "exclusion ruler"), ("__fca", "FCA ruler"), ("__quitam", "qui tam ruler"), ("__aff", "affiliated ruler")):
        for p in run_dir.glob(f"future_bans_after_*{tag}*.csv" if tag else "future_bans_after_*.csv"):
            if "TIERS" in p.name:
                continue
            if tag == "" and "__" in p.name:
                continue
            df = _csv(p)
            if df is not None and "is_prospective_positive" in df.columns:
                n = int(pd.to_numeric(df["is_prospective_positive"], errors="coerce").fillna(0).sum())
                k = int(pd.to_numeric(df.get("was_excluded_pre_cutoff", 0), errors="coerce").fillna(0).sum())
                L.append(f"- {name} ({p.name}): {n} forward positives on paper, {k} known at the freeze")
            break
    la = _json(run_dir / "label_audit.json")
    if la:
        for k in ("recall", "case_db", "cia"):
            v = la.get(k)
            if isinstance(v, dict):
                L.append(f"- label_audit {k}: " + ", ".join(f"{kk} {vv}" for kk, vv in list(v.items())[:6]))
    ml = _json(run_dir.parent.parent / "processed" / "manus_lanes" / "manus_lanes.json")
    if ml:
        loaded = sum(int(r.get("loaded", 0) or 0) for r in ml)
        L.append(f"- research lanes have loaded {loaded} rows into the label inputs so far (MANUS_LANES.md)")
    return L


def q6_manus(run_dir: Path, data_root: Path | None) -> list[str]:
    L = ["## 6. Is the research service used?", ""]
    p = (Path(data_root) if data_root else run_dir.parent.parent) / "processed" / "manus_lanes" / "manus_lanes.json"
    ml = _json(p)
    if not ml:
        return L + ["_no MANUS_LANES report: `bash ops/vm/manus_lanes.sh <run tag>` has not run_"]
    L.append("| lane | prompts | dispatched | completed | loaded | quarantined |")
    L.append("|---|--:|--:|--:|--:|--:|")
    for r in ml:
        L.append(f"| {r.get('lane')} | {r.get('prompts')} | {r.get('dispatched')} | {r.get('completed')} | "
                 f"{r.get('loaded')} | {r.get('quarantined')} |")
    idle = [r.get("lane") for r in ml if int(r.get("prompts", 0) or 0) and not int(r.get("dispatched", 0) or 0)]
    if idle:
        L.append("")
        L.append(f"Lanes with prompts never dispatched: {', '.join(idle)}.")
    return L


def run(run_dir: Path, data_root: Path | None = None, out: Path | None = None) -> Path:
    run_dir = Path(run_dir)
    L = [f"# QUESTIONS_READOUT — the standing questions answered from {run_dir.name}", "",
         "_Each answer names its artifact; a missing artifact is reported, never guessed. "
         "Leads for counsel review, never accusations._", ""]
    for block in (q1_schemes(run_dir), q2_wiring(run_dir), q3_enrichment(run_dir), q4_data(run_dir),
                  q5_label(run_dir), q6_manus(run_dir, data_root)):
        L += block + [""]
    out = Path(out) if out else run_dir / "QUESTIONS_READOUT.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"[questions_readout] -> {out}")
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    run(Path(a.run_dir), Path(a.data_root) if a.data_root else None, Path(a.out) if a.out else None)


if __name__ == "__main__":
    main()
