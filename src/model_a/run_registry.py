"""
run_registry.py — the audit log of model runs: KPIs, versions, trends, rollback.

Every major run already writes its evidence into its output folder (manifest,
expectations, network A/B, ablation, digests). This module turns those folders
into an APPEND-ONLY registry so releases are comparable over time and a bad
release can be rolled back by pointing consumers at the previous champion.

  register  python -m src.model_a.run_registry --run-dir <folder> --tag v2
            parses the run folder's reports into one KPI row, appends it to
            <data-root>/model_a/run_registry.jsonl, and regenerates
            MODEL_RUNS.md (the human report: policy, table, trends,
            recommendation).
  report    python -m src.model_a.run_registry --report-only

KPIs captured per run: matrix shape, git commit, PU/forward-label counts,
expectation FAIL count, the network A/B verdict + matched structural ROC
delta, the OOF ablation deltas (lift / PR / ROC with CIs), complementarity
counts, and the FILE PATHS everything lives at — so "where is that run on my
machine" is always one lookup.

PROMOTION POLICY (encoded in recommend()):
  PROMOTE   0 expectation FAILs, network verdict KEEP, ablation lift CI clear
            of zero, and lift delta not more than 0.15 below the reigning
            champion's.
  HOLD      any FAILs, a CI that includes zero, or a >0.15 lift-delta drop —
            investigate before shipping; the champion stays the champion.
  Rollback = the champion pointer: consumers (Travis handoffs, dossiers,
  company lists) read from the champion run's folder, which is never deleted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_NUM = r"([+-]?\d+\.?\d*)"


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _search(pattern: str, text: str, cast=float, group: int = 1):
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return cast(m.group(group))
    except (TypeError, ValueError):
        return None


def parse_run_dir(run_dir: str | Path) -> dict:
    """One KPI row from a run folder's standard reports. Missing files leave
    nulls — a partial run is still registrable, its gaps visible."""
    d = Path(run_dir)
    row: dict = {"run_dir": str(d.resolve()), "tag": d.name}

    # matrix shape without loading the data
    mtx = d / "provider_features_for_model.parquet"
    if mtx.exists():
        try:
            import pyarrow.parquet as pq
            pf = pq.ParquetFile(mtx)
            row["n_providers"] = int(pf.metadata.num_rows)
            row["n_columns"] = int(len(pf.schema_arrow.names))
        except Exception:
            pass
        row["matrix_path"] = str(mtx.resolve())
        row["matrix_mtime"] = datetime.fromtimestamp(
            mtx.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")

    man = d / "feature_manifest.json"
    if man.exists():
        try:
            mj = json.loads(_read(man))
            row["asof_cutoff"] = mj.get("asof_cutoff")
            row["label"] = mj.get("label")
            subs = mj.get("graph_substrate") or {}
            row["substrate_frozen"] = bool(subs.get("address_layer_frozen"))
        except Exception:
            pass
        row["manifest_path"] = str(man.resolve())

    exp = _read(d / "EXPECTATIONS_REPORT.md")
    if exp:
        row["expectation_fails"] = _search(r"(\d+)\s+FAIL", exp, int)
        row["expectation_warns"] = _search(r"(\d+)\s+WARN", exp, int)

    ab = _read(d / "NETWORK_AB_REPORT.md")
    if ab:
        v = re.search(r"\*\*VERDICT:\s*([A-Z /-]+)", ab)
        row["network_verdict"] = v.group(1).strip() if v else None
        row["network_matched_roc_delta"] = _search(
            r"delta ([+-]?\d+\.?\d*), CI", ab)

    fb = d / "future_bans_after_2023-12.csv"
    if fb.exists():
        try:
            import pandas as pd
            f = pd.read_csv(fb, dtype=str)
            row["forward_positives"] = int(
                (pd.to_numeric(f.get("is_prospective_positive"),
                               errors="coerce").fillna(0) == 1).sum())
        except Exception:
            pass
        row["future_bans_path"] = str(fb.resolve())

    oof = _read(d / "SOURCE_ABLATION_OOF_FULL.md") or _read(
        d / "SOURCE_ABLATION_OOF.md")
    if oof:
        row["ablation_positives"] = _search(r"positives=(\d[\d,]*)", oof,
                                            lambda s: int(s.replace(",", "")))
        for key, name in (("lift", "top-decile lift"), ("pr", "PR-AUC"),
                          ("roc", "ROC-AUC")):
            m = re.search(
                rf"\| {re.escape(name)} \| {_NUM} \| {_NUM} \| {_NUM} "
                rf"\[{_NUM}, {_NUM}\]", oof)
            if m:
                row[f"ablation_{key}_core"] = float(m.group(1))
                row[f"ablation_{key}_full"] = float(m.group(2))
                row[f"ablation_{key}_delta"] = float(m.group(3))
                row[f"ablation_{key}_lo"] = float(m.group(4))
                row[f"ablation_{key}_hi"] = float(m.group(5))
        row["catches_full_only"] = _search(
            r"full only[^|]*\|\s*(\d+)", oof, int)
        row["catches_core_only"] = _search(
            r"core only[^|]*\|\s*(\d+)", oof, int)

    try:
        row["git_commit"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True,
            text=True, timeout=10).stdout.strip() or None
    except Exception:
        row["git_commit"] = None
    row["registered_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    return row


def recommend(rows: list[dict]) -> dict:
    """PROMOTE / HOLD for the newest run + the current champion pointer."""
    if not rows:
        return {"decision": "NONE", "champion": None, "reasons": []}
    latest = rows[-1]
    champion = None
    for r in rows[:-1]:
        if r.get("decision") == "PROMOTE":
            champion = r
    reasons = []
    fails = latest.get("expectation_fails")
    if fails is None:
        reasons.append("no expectations report found (treating as HOLD)")
    elif fails > 0:
        reasons.append(f"{fails} expectation FAIL(s)")
    verdict = latest.get("network_verdict") or ""
    if not verdict.startswith("KEEP"):
        reasons.append(f"network verdict is '{verdict or 'missing'}', not KEEP")
    lo = latest.get("ablation_lift_lo")
    if lo is None:
        reasons.append("no ablation lift CI found")
    elif lo <= 0:
        reasons.append(f"ablation lift CI includes zero (lo={lo:+.3f})")
    if champion is not None:
        prev = champion.get("ablation_lift_delta")
        cur = latest.get("ablation_lift_delta")
        if prev is not None and cur is not None and cur < prev - 0.15:
            reasons.append(f"lift delta dropped {prev - cur:.2f} vs champion "
                           f"'{champion.get('tag')}' (>0.15 threshold)")
    decision = "HOLD" if reasons else "PROMOTE"
    new_champion = latest if decision == "PROMOTE" else champion
    return {"decision": decision, "reasons": reasons,
            "champion": (new_champion or {}).get("tag"),
            "champion_dir": (new_champion or {}).get("run_dir")}


def _fmt(v, spec=""):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return format(v, spec) if spec else str(v)


def to_markdown(rows: list[dict], rec: dict) -> str:
    L = ["# MODEL RUNS — the release audit log", ""]
    L.append("**Policy.** Every major run is registered here with its KPIs and "
             "file locations. PROMOTE requires: zero expectation FAILs, a KEEP "
             "network verdict, an ablation lift CI clear of zero, and no more "
             "than a 0.15 lift-delta drop vs the reigning champion. Anything "
             "else is HOLD: investigate before shipping, and keep pointing "
             "consumers (Travis handoffs, company lists, dossiers) at the "
             "champion run's folder below. Run folders are immutable — "
             "rollback is a pointer change, never a rebuild.")
    L.append("")
    L.append(f"**Current champion:** `{rec.get('champion') or 'none yet'}`"
             + (f" — `{rec.get('champion_dir')}`" if rec.get("champion_dir")
                else ""))
    L.append(f"**Latest run decision:** **{rec.get('decision')}**"
             + ("" if not rec.get("reasons")
                else " — " + "; ".join(rec["reasons"])))
    L.append("")
    L.append("| run | registered | providers×cols | fwd positives | expect "
             "FAILs | network | ablation lift Δ [CI] | full/core-only catches "
             "| commit |")
    L.append("|---|---|---|--:|--:|---|---|---|---|")
    for r in reversed(rows):
        shape = (f"{_fmt(r.get('n_providers'), ',')}×"
                 f"{_fmt(r.get('n_columns'))}")
        lift = (f"{_fmt(r.get('ablation_lift_delta'), '+.3f')} "
                f"[{_fmt(r.get('ablation_lift_lo'), '+.3f')}, "
                f"{_fmt(r.get('ablation_lift_hi'), '+.3f')}]")
        catches = (f"{_fmt(r.get('catches_full_only'))}/"
                   f"{_fmt(r.get('catches_core_only'))}")
        L.append(f"| {r.get('tag')} | {str(r.get('registered_at', ''))[:10]} "
                 f"| {shape} | {_fmt(r.get('forward_positives'), ',')} "
                 f"| {_fmt(r.get('expectation_fails'))} "
                 f"| {(r.get('network_verdict') or '—')[:14]} | {lift} "
                 f"| {catches} | {r.get('git_commit') or '—'} |")
    if len(rows) >= 2:
        a, b = rows[-2], rows[-1]
        L.append("")
        L.append("## Trend: latest vs previous")
        L.append("")
        for key, name in (("ablation_lift_delta", "ablation lift delta"),
                          ("forward_positives", "forward positives"),
                          ("n_columns", "matrix columns"),
                          ("expectation_fails", "expectation FAILs")):
            va, vb = a.get(key), b.get(key)
            if va is not None and vb is not None:
                L.append(f"- {name}: {va} → {vb} ({vb - va:+})"
                         if isinstance(va, int) else
                         f"- {name}: {va:.3f} → {vb:.3f} ({vb - va:+.3f})")
    L.append("")
    L.append("## Where the files live")
    L.append("")
    for r in reversed(rows):
        L.append(f"- **{r.get('tag')}**: `{r.get('run_dir')}` — matrix "
                 f"`provider_features_for_model.parquet`, manifest, "
                 f"`future_bans_after_2023-12.csv`, and the report .md files "
                 f"all inside this folder.")
    L.append("")
    L.append("_Registered by run_registry; append-only jsonl beside this "
             "report is the machine-readable source of truth._")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default=None,
                    help="run folder to register (omit with --report-only)")
    ap.add_argument("--tag", default=None,
                    help="version tag (default: the folder name)")
    ap.add_argument("--data-root",
                    default=os.environ.get("MEDICAID_DATA_ROOT",
                                           str(Path.home() / "Desktop/data")))
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()

    reg_dir = Path(args.data_root) / "model_a"
    reg_dir.mkdir(parents=True, exist_ok=True)
    reg_path = reg_dir / "run_registry.jsonl"
    rows = []
    if reg_path.exists():
        rows = [json.loads(ln) for ln in
                reg_path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    if not args.report_only:
        if not args.run_dir:
            raise SystemExit("--run-dir required (or pass --report-only)")
        row = parse_run_dir(args.run_dir)
        if args.tag:
            row["tag"] = args.tag
        # provisional decision so the champion chain is reconstructible
        rec_now = recommend(rows + [row])
        row["decision"] = rec_now["decision"]
        rows.append(row)
        with open(reg_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    rec = recommend(rows)
    report = reg_dir / "MODEL_RUNS.md"
    report.write_text(to_markdown(rows, rec), encoding="utf-8")
    print(f"[run_registry] {len(rows)} run(s) | latest decision: "
          f"{rec['decision']} | champion: {rec.get('champion')} | "
          f"report -> {report}")


if __name__ == "__main__":
    main()
