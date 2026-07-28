"""
run_registry.py — the audit log of model runs: KPIs, versions, trends, rollback.

Every major run already writes its evidence into its output folder (manifest,
expectations, network A/B, ablation, digests). This module turns those folders
into an APPEND-ONLY registry so releases are comparable over time and a bad
release can be rolled back by pointing consumers at the previous champion.

  preregister  python -m src.model_a.run_registry --preregister --tag v3 \
                   --goal "..." --expected "lift delta >= +0.5, CI clear"
               LOCKS the hypothesis and acceptance bar with a timestamp
               BEFORE the run exists — measured-vs-declared is then shown
               side by side, so impact can never be narrated after the fact.
  register     python -m src.model_a.run_registry --run-dir <folder> --tag v2
               parses the run folder's reports into one KPI row (plus a
               REPRODUCIBILITY CAPSULE: git commit, library versions, input
               hashes) and appends it to run_registry.jsonl.
  signoff      python -m src.model_a.run_registry --signoff --tag v2 \
                   --by travis --status reproduced|objection --note "..."
               the independent-reviewer gate: a release without a
               'reproduced' sign-off wears an UNREPRODUCED chip.
  report       python -m src.model_a.run_registry --report-only
               regenerates MODEL_RUNS.md (and MODEL_RUNS.html with --html):
               policy, table, declared-vs-measured, trends, recommendation,
               cohort-usage warnings.

TEST-SET HYGIENE (cohort tracking): every registered run records its eval
cohort (the freeze cutoff). Each decision against the same cohort is a peek
that slowly overfits the release process to those specific positives; the
report warns when one cohort has supported more than 5 registered runs —
time to mint a fresh freeze (prospective_label --cutoff <newer month>).

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
    row["eval_cohort"] = row.get("asof_cutoff")
    row["capsule"] = _capsule(d)
    row["registered_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    return row


def _capsule(run_dir: Path) -> dict:
    """Reproducibility capsule: enough to rebuild bit-for-bit or prove drift.

    Small inputs get full sha256; the multi-GB matrix gets size+mtime (hashing
    it on every registration would take minutes for no added assurance — the
    manifest hash pins its schema and provenance)."""
    import hashlib
    cap: dict = {"python": None, "libs": {}, "inputs": {}}
    try:
        import sys
        cap["python"] = sys.version.split()[0]
        from importlib.metadata import version
        for lib in ("lightgbm", "scikit-learn", "pandas", "numpy", "duckdb"):
            try:
                cap["libs"][lib] = version(lib)
            except Exception:
                pass
    except Exception:
        pass
    for name in ("feature_manifest.json", "future_bans_after_2023-12.csv"):
        p = run_dir / name
        if p.exists():
            try:
                cap["inputs"][name] = {
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest()[:16],
                    "bytes": p.stat().st_size}
            except Exception:
                pass
    m = run_dir / "provider_features_for_model.parquet"
    if m.exists():
        cap["inputs"][m.name] = {
            "bytes": m.stat().st_size,
            "mtime": datetime.fromtimestamp(
                m.stat().st_mtime, tz=timezone.utc).isoformat(
                timespec="seconds")}
    return cap


def _load_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(ln) for ln in
            p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _append_jsonl(p: Path, row: dict) -> None:
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def cohort_usage(rows: list[dict], warn_at: int = 5) -> list[str]:
    """Test-set hygiene: warn when one freeze cohort has supported too many
    registered runs — repeated peeks overfit the process to those positives."""
    counts: dict = {}
    for r in rows:
        c = r.get("eval_cohort") or "unknown"
        counts[c] = counts.get(c, 0) + 1
    return [f"cohort {c}: {n} registered runs — mint a fresh freeze "
            f"(prospective_label --cutoff <newer month>) before more decisions"
            for c, n in counts.items() if n > warn_at]


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


def to_markdown(rows: list[dict], rec: dict,
                prereg: list[dict] | None = None,
                signoffs: list[dict] | None = None) -> str:
    prereg = prereg or []
    signoffs = signoffs or []
    so_by_tag = {}
    for s in signoffs:                        # latest sign-off per tag wins
        so_by_tag[s.get("tag")] = s
    pr_by_tag = {p.get("tag"): p for p in prereg}
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
             "| reviewer | commit |")
    L.append("|---|---|---|--:|--:|---|---|---|---|---|")
    for r in reversed(rows):
        shape = (f"{_fmt(r.get('n_providers'), ',')}×"
                 f"{_fmt(r.get('n_columns'))}")
        lift = (f"{_fmt(r.get('ablation_lift_delta'), '+.3f')} "
                f"[{_fmt(r.get('ablation_lift_lo'), '+.3f')}, "
                f"{_fmt(r.get('ablation_lift_hi'), '+.3f')}]")
        catches = (f"{_fmt(r.get('catches_full_only'))}/"
                   f"{_fmt(r.get('catches_core_only'))}")
        so = so_by_tag.get(r.get("tag"))
        rev = ("UNREPRODUCED" if so is None else
               f"{so.get('status', '?').upper()} ({so.get('by', '?')})")
        L.append(f"| {r.get('tag')} | {str(r.get('registered_at', ''))[:10]} "
                 f"| {shape} | {_fmt(r.get('forward_positives'), ',')} "
                 f"| {_fmt(r.get('expectation_fails'))} "
                 f"| {(r.get('network_verdict') or '—')[:14]} | {lift} "
                 f"| {catches} | {rev} | {r.get('git_commit') or '—'} |")

    # declared vs measured — the pre-registration receipt
    declared = [(t, p) for t, p in pr_by_tag.items()]
    if declared:
        L.append("")
        L.append("## Declared vs measured (pre-registered goals)")
        L.append("")
        L.append("_Goals and acceptance bars locked with a timestamp BEFORE "
                 "the run existed — impact is judged against these, never "
                 "narrated afterward._")
        L.append("")
        reg_tags = {r.get("tag"): r for r in rows}
        for tag, p in declared:
            L.append(f"- **{tag}** (declared {str(p.get('declared_at', ''))[:16]}): "
                     f"goal — {p.get('goal', '—')}; acceptance — "
                     f"{p.get('expected', '—')}")
            r = reg_tags.get(tag)
            if r:
                L.append(f"  measured — lift Δ "
                         f"{_fmt(r.get('ablation_lift_delta'), '+.3f')} "
                         f"[{_fmt(r.get('ablation_lift_lo'), '+.3f')}, "
                         f"{_fmt(r.get('ablation_lift_hi'), '+.3f')}], "
                         f"FAILs {_fmt(r.get('expectation_fails'))}, "
                         f"verdict {(r.get('network_verdict') or '—')[:20]}")
            else:
                L.append("  measured — run not registered yet")
    warns = cohort_usage(rows)
    if warns:
        L.append("")
        L.append("## Test-set hygiene warnings")
        L.append("")
        for w in warns:
            L.append(f"- ⚠ {w}")
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


def to_html(rows: list[dict], rec: dict, prereg: list[dict],
            signoffs: list[dict]) -> str:
    """Self-contained data-driven dashboard — regenerated on every
    registration, so KPIs never travel by clipboard."""
    md = to_markdown(rows, rec, prereg, signoffs)
    body = (md.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))
    # lift-delta strip chart from the data itself
    pts = []
    usable = [r for r in rows if r.get("ablation_lift_delta") is not None]
    for i, r in enumerate(usable):
        x = 90 + i * 130

        def y(v):
            return 190 - (v + 0.2) / 1.4 * 160

        d = r["ablation_lift_delta"]
        lo = r.get("ablation_lift_lo", d)
        hi = r.get("ablation_lift_hi", d)
        pts.append(
            f'<line x1="{x}" y1="{y(hi):.1f}" x2="{x}" y2="{y(lo):.1f}" '
            f'stroke="#2a78d6" stroke-width="2"/>'
            f'<circle cx="{x}" cy="{y(d):.1f}" r="5" fill="#2a78d6"/>'
            f'<text x="{x}" y="212" text-anchor="middle" font-size="10">'
            f'{r.get("tag", "?")}</text>'
            f'<text x="{x + 10}" y="{y(d) - 6:.1f}" font-size="10" '
            f'font-weight="bold">{d:+.3f}</text>')
    zero_y = 190 - 0.2 / 1.4 * 160
    svg = (f'<svg viewBox="0 0 {max(460, 90 + len(usable) * 130)} 230" '
           f'style="max-width:560px;font-family:monospace">'
           f'<line x1="40" y1="{zero_y:.1f}" x2="98%" y2="{zero_y:.1f}" '
           f'stroke="#888" stroke-width="1.5"/>'
           f'<text x="34" y="{zero_y + 4:.1f}" text-anchor="end" '
           f'font-size="10">0</text>' + "".join(pts) + "</svg>")
    return ("<title>Model runs</title>"
            "<style>body{font:14px/1.5 system-ui;max-width:900px;"
            "margin:24px auto;padding:0 16px;color:#141410}"
            "pre{white-space:pre-wrap;background:#f4f4f1;border:1px solid "
            "#e2e2dc;border-radius:8px;padding:14px;font-size:12.5px}"
            "@media(prefers-color-scheme:dark){body{background:#1a1a19;"
            "color:#f2f1ea}pre{background:#232322;border-color:#38382f}}"
            "</style>"
            "<h1 style='font-size:20px'>Model runs — auto-generated</h1>"
            "<p>Lift delta (full − core) by release, whisker = 95% CI:</p>"
            + svg + "<pre>" + body + "</pre>")


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
    ap.add_argument("--html", action="store_true",
                    help="also regenerate MODEL_RUNS.html (data-driven "
                         "dashboard, no clipboard KPIs)")
    # pre-registration (lock the goal BEFORE the run)
    ap.add_argument("--preregister", action="store_true")
    ap.add_argument("--goal", default=None,
                    help="with --preregister: the hypothesis in one sentence")
    ap.add_argument("--expected", default=None,
                    help="with --preregister: the acceptance bar, e.g. "
                         "'lift delta CI clear of zero and >= champion-0.15'")
    # independent-review sign-off
    ap.add_argument("--signoff", action="store_true")
    ap.add_argument("--by", default=None, help="with --signoff: reviewer name")
    ap.add_argument("--status", default=None,
                    choices=[None, "reproduced", "objection"],
                    help="with --signoff: reproduced or objection")
    ap.add_argument("--note", default="", help="with --signoff: free text")
    args = ap.parse_args()

    reg_dir = Path(args.data_root) / "model_a"
    reg_dir.mkdir(parents=True, exist_ok=True)
    reg_path = reg_dir / "run_registry.jsonl"
    pre_path = reg_dir / "preregistrations.jsonl"
    so_path = reg_dir / "signoffs.jsonl"
    rows = _load_jsonl(reg_path)
    prereg = _load_jsonl(pre_path)
    signoffs = _load_jsonl(so_path)

    if args.preregister:
        if not (args.tag and args.goal and args.expected):
            raise SystemExit("--preregister needs --tag, --goal, --expected")
        if any(p.get("tag") == args.tag for p in prereg):
            raise SystemExit(f"tag '{args.tag}' already pre-registered — "
                             "pre-registrations are locked, pick a new tag")
        entry = {"tag": args.tag, "goal": args.goal, "expected": args.expected,
                 "declared_at": datetime.now(timezone.utc).isoformat(
                     timespec="seconds")}
        _append_jsonl(pre_path, entry)
        prereg.append(entry)
        print(f"[run_registry] pre-registered '{args.tag}' — goal locked at "
              f"{entry['declared_at']}")
    elif args.signoff:
        if not (args.tag and args.by and args.status):
            raise SystemExit("--signoff needs --tag, --by, --status")
        entry = {"tag": args.tag, "by": args.by, "status": args.status,
                 "note": args.note,
                 "signed_at": datetime.now(timezone.utc).isoformat(
                     timespec="seconds")}
        _append_jsonl(so_path, entry)
        signoffs.append(entry)
        print(f"[run_registry] sign-off recorded: {args.tag} — "
              f"{args.status} by {args.by}")
    elif not args.report_only:
        if not args.run_dir:
            raise SystemExit("--run-dir required (or pass --report-only / "
                             "--preregister / --signoff)")
        row = parse_run_dir(args.run_dir)
        if args.tag:
            row["tag"] = args.tag
        rec_now = recommend(rows + [row])
        row["decision"] = rec_now["decision"]
        rows.append(row)
        _append_jsonl(reg_path, row)

    rec = recommend(rows)
    report = reg_dir / "MODEL_RUNS.md"
    report.write_text(to_markdown(rows, rec, prereg, signoffs),
                      encoding="utf-8")
    if args.html:
        (reg_dir / "MODEL_RUNS.html").write_text(
            to_html(rows, rec, prereg, signoffs), encoding="utf-8")
    print(f"[run_registry] {len(rows)} run(s) | latest decision: "
          f"{rec['decision']} | champion: {rec.get('champion')} | "
          f"report -> {report}")


if __name__ == "__main__":
    main()
