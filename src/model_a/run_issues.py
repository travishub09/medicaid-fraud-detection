"""
run_issues.py — the run's issue ledger: every error, warning and open question the
run's own artifacts raised, in ONE ranked list with the fix for each, tracked across
runs so a recurring defect escalates and a fixed one is recorded as fixed.

  python -m src.model_a.run_issues --run-dir <run folder> [--data-root <root>]
                                   [--prev-run-dir <earlier run folder>]

What it reads (nothing new is computed; every input is an artifact the run writes):
  feature_manifest.json      expectations {fails, warns}, sources_audit (skips + why),
                             feature_partition.unmapped (the trainability gate),
                             training_policy, n_trainable_after_fence
  EXPECTATIONS_REPORT.md     every FAIL / WARN row (column, check, observed, go look at)
  scheme_health.json         schemes not HEALTHY (missing inputs, inverted direction,
                             weak label AUC) + the regressions block
  SCHEME_TRIAGE.md           the vintage audit (UNKNOWN / POST-CUTOFF editions)
  NETWORK_AB_REPORT.md       the verdict (KEEP or not)
  accuracy_diagnostics*.json the business-outcome grade (hits at 50 vs random, the
                             institutional share, top-50 stability)
  label_audit.json           the label audit's findings, when the run folder has one
  preregistrations.jsonl     the locked --check expressions (run_registry.check_prereg)
  the previous run           what got worse: new skips, more FAILs, a universe that
                             moved by more than a tenth

What it writes:
  <run>/RUN_ISSUES.md + run_issues.json      the ranked list (FAIL first, recurring
                                             before new) with an action per row
  <data_root>/model_a/issues_ledger.jsonl    one record per distinct issue id across
                                             runs: first_seen, last_seen, runs it
                                             appeared in, status open / resolved
                                             (resolved = present in an earlier run,
                                             absent now)

SELF-CORRECTION, HONESTLY SCOPED: nothing here edits code or data. The ledger turns
each finding into (a) a named fix, (b) a recurrence count so the same defect cannot
be waved through twice, and (c) a suggested --check for the next pre-registration,
so the next run is gated on the fix. Model behaviour changes only through a new
pre-registered run; an automatic retrain on unverified signals would be the leakage
machine the whole registry exists to prevent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SEV_RANK = {"FAIL": 0, "WARN": 1, "INFO": 2}

_EXP_ROW = re.compile(r"^\|\s*(FAIL|WARN)\s*\|\s*`?([^|`]+?)`?\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$")
_VINTAGE_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([^|]*?)\s*\|\s*\*\*([^*]+)\*\*\s*\|")
_VERDICT = re.compile(r"\*\*VERDICT:\s*([A-Z][A-Z \-]+?)[:*]", re.I)


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="latin-1")


def _issue(area: str, subject: str, check: str, severity: str, finding: str,
           action: str, evidence: str) -> dict:
    return {"id": f"{area}:{subject}:{check}", "area": area, "subject": subject,
            "check": check, "severity": severity, "finding": finding,
            "action": action, "evidence": evidence}


def _json(p: Path) -> dict | None:
    if not p.exists():
        return None
    try:
        return json.loads(_read(p))
    except Exception:
        return None


def collect_issues(run_dir: str | Path, prereg: dict | None = None,
                   prev_run_dir: str | Path | None = None) -> tuple[list[dict], dict]:
    """Every issue the run folder's artifacts raise, plus a summary dict.
    Pure read; safe on a partial folder (each missing artifact is reported once
    as INFO so a silently skipped stage is visible)."""
    from .run_registry import check_prereg, parse_run_dir
    d = Path(run_dir)
    issues: list[dict] = []
    row = parse_run_dir(d)
    man = _json(d / "feature_manifest.json") or {}

    # --- calc integrity (EXPECTATIONS) -------------------------------------------
    exp_p = d / "EXPECTATIONS_REPORT.md"
    if exp_p.exists():
        for line in _read(exp_p).splitlines():
            m = _EXP_ROW.match(line)
            if not m:
                continue
            sev, col, check, observed, expected, look = (x.strip() for x in m.groups())
            issues.append(_issue("feature", col, check, sev,
                                 f"{observed} (expected: {expected})", look or "inspect the source",
                                 "EXPECTATIONS_REPORT.md"))
    else:
        issues.append(_issue("stage", "expectations", "missing_artifact", "INFO",
                             "no EXPECTATIONS_REPORT.md in the run folder",
                             "the export did not finish, or the report was not copied", "run folder"))

    # --- sources: skips + the trainability gate ------------------------------------
    for s in man.get("sources_audit") or []:
        if str(s.get("status", "")).lower().startswith("skip"):
            detail = str(s.get("detail") or s.get("reason") or "")
            issues.append(_issue("data", str(s.get("source")), "source_skipped", "WARN", detail,
                                 "procure or build the file the detail names (docs/platform/12), "
                                 "or record the skip as intended", "SOURCES_REPORT.md"))
    unmapped = ((man.get("feature_partition") or {}).get("unmapped") or [])
    if unmapped:
        issues.append(_issue("feature", "feature_partition", "unmapped_trainable", "FAIL",
                             f"{len(unmapped)} trainable column(s) map to no source: {', '.join(map(str, unmapped[:8]))}",
                             "record the source in sources_used (the trainability gate should have raised)",
                             "feature_manifest.json"))

    # --- schemes ---------------------------------------------------------------------
    sh = _json(d / "scheme_health.json")
    if sh:
        for s in sh.get("schemes") or []:
            st = str(s.get("status", ""))
            name = str(s.get("scheme", "?"))
            auc, npos = s.get("label_auc"), s.get("n_label_pos")
            if st == "CHECK_DIRECTION":
                issues.append(_issue("scheme", name, "inverted_direction", "WARN",
                                     f"label AUC {auc} on {npos} positives: the subscore ranks positives below negatives",
                                     "check the sign / one-sided direction of its inputs; a scheme that points the "
                                     "wrong way is subtracting from the composite", "SCHEME_HEALTH.md"))
            elif st == "DEGRADED":
                issues.append(_issue("scheme", name, "missing_inputs", "WARN",
                                     f"inputs present {s.get('n_inputs_present')}/{s.get('n_inputs_total')}; "
                                     f"missing: {s.get('missing_inputs') or 'n/a'}; coverage {s.get('subscore_coverage')}",
                                     "wire or procure the missing input (SCHEME_TRIAGE names the file)", "SCHEME_HEALTH.md"))
            elif st not in ("HEALTHY", ""):
                issues.append(_issue("scheme", name, st.lower(), "WARN", json.dumps(s, default=str)[:200],
                                     "see SCHEME_TRIAGE.md", "SCHEME_HEALTH.md"))
            if auc is not None and npos and float(npos) >= 20 and float(auc) < 0.5 and st == "HEALTHY":
                issues.append(_issue("scheme", name, "weak_label_auc", "INFO",
                                     f"label AUC {auc} on {npos} positives while inputs are healthy",
                                     "the scheme's inputs do not separate this label; check the label's scheme mix "
                                     "before redesigning", "SCHEME_HEALTH.md"))
        for r in sh.get("regressions") or []:
            issues.append(_issue("scheme", str(r.get("scheme", r)), "regression", "WARN",
                                 json.dumps(r, default=str)[:200], "compare with the previous run's inputs",
                                 "scheme_health.json"))
    else:
        issues.append(_issue("stage", "scheme_health", "missing_artifact", "INFO",
                             "no scheme_health.json", "the scheme-health step did not run", "run folder"))

    # --- vintage audit (SCHEME_TRIAGE) -----------------------------------------------
    tri = d / "SCHEME_TRIAGE.md"
    if tri.exists():
        for line in _read(tri).splitlines():
            m = _VINTAGE_ROW.match(line)
            if not m:
                continue
            src, vint, verdict = (x.strip() for x in m.groups())
            v = verdict.upper()
            if "POST" in v or "LEAK" in v:
                issues.append(_issue("vintage", src, "post_cutoff_edition", "FAIL",
                                     f"edition {vint} is dated after the freeze",
                                     "procure the pre-cutoff edition or exclude the source from frozen builds",
                                     "SCHEME_TRIAGE.md"))
            elif v == "UNKNOWN":
                issues.append(_issue("vintage", src, "undated_edition", "INFO",
                                     "no readable edition date; point-in-time correctness unverified",
                                     "name the file with its edition year or record it in preclean/EDITIONS.csv",
                                     "SCHEME_TRIAGE.md"))

    # --- network A/B --------------------------------------------------------------------
    nv = row.get("network_verdict")
    if nv and str(nv).upper() != "KEEP":
        issues.append(_issue("evaluation", "network_family", "verdict_not_keep", "WARN",
                             f"verdict {nv}", "the graph family does not move the matched metric on this label; "
                             "keep the features but do not cite them as the win", "NETWORK_AB_REPORT.md"))

    # --- business-outcome grade -------------------------------------------------------------
    hits, base = row.get("acc_org_hits50"), row.get("acc_base50")
    if hits is not None and base is not None:
        if float(hits) <= float(base):
            issues.append(_issue("evaluation", str(row.get("acc_list") or "list"), "hits50_not_above_random",
                                 "FAIL", f"hits at 50 = {hits}, random would give {base}",
                                 "the list is not better than chance at the depth a person reads; "
                                 "do not promote; read the error taxonomy", "ACCURACY_DIAGNOSTICS"))
        inst = row.get("acc_inst_share50")
        if inst is not None and float(inst) > 0.4:
            issues.append(_issue("evaluation", str(row.get("acc_list") or "list"), "institutional_share_high",
                                 "WARN", f"{float(inst):.0%} of the top 50 is the institutional book",
                                 "grade the pursuit book, not the main list", "ACCURACY_DIAGNOSTICS"))
        jac = row.get("acc_top50_jaccard")
        if jac is not None and float(jac) < 0.6:
            issues.append(_issue("evaluation", str(row.get("acc_list") or "list"), "top50_unstable", "WARN",
                                 f"top-50 Jaccard across re-shuffled folds {float(jac):.2f}",
                                 "seed bagging / more positives; a list that changes with the fold draw is noise",
                                 "ACCURACY_DIAGNOSTICS"))
    else:
        issues.append(_issue("stage", "accuracy_diagnostics", "missing_artifact", "INFO",
                             "no accuracy_diagnostics json: the run was never graded on the business outcome",
                             "run accuracy_diagnostics --list-name provider_priority_list_pursuit.csv", "run folder"))

    # --- the locked pre-registration -----------------------------------------------------------
    if prereg:
        for c in check_prereg(row, prereg):
            if not c.get("ok"):
                issues.append(_issue("policy", "preregistration", str(c.get("expr")), "FAIL",
                                     str(c.get("why") or "check failed"),
                                     "the declared bar was not met: HOLD; the fix is whatever moves this metric",
                                     "preregistrations.jsonl"))

    # --- label audit, when present ------------------------------------------------------------
    la = _json(d / "label_audit.json")
    if la:
        for f in (la.get("findings") or [])[:40]:
            sev = str(f.get("severity", "WARN")).upper()
            sev = sev if sev in SEV_RANK else "WARN"
            issues.append(_issue("label", str(f.get("source") or f.get("area") or "label"),
                                 str(f.get("check") or f.get("title") or "finding")[:60], sev,
                                 str(f.get("finding") or f.get("detail") or "")[:240],
                                 str(f.get("fix") or f.get("action") or "see LABEL_AUDIT.md"), "label_audit.json"))

    # --- the learning loop, when it ran -------------------------------------------------------------
    ll = _json(d / "learning_loop.json")
    if ll:
        dr = ll.get("drift") or {}
        for w in (dr.get("worst") or []):
            if w.get("flag") in ("FAIL", "WARN"):
                issues.append(_issue("drift", str(w.get("feature")), "feature_drift", str(w.get("flag")),
                                     f"PSI {w.get('psi')} vs {Path(str(dr.get('prev_run') or '')).name or 'previous run'}; "
                                     f"null rate {w.get('null_rate_prev')} -> {w.get('null_rate_cur')}",
                                     "a feature moved without a code change: check the file edition and the join "
                                     "(feature_drift.csv)", "LEARNING_LOOP.md"))
        ln = ll.get("label_noise") or {}
        if ln.get("n_suspect"):
            issues.append(_issue("label", "training_label", "suspect_positives", "INFO",
                                 f"{ln['n_suspect']:,} labelled positives score below the mean negative "
                                 f"({ln.get('suspect_share_of_positives', 0):.0%} of positives)",
                                 "review label_review_queue.csv; an identity collision or administrative ban "
                                 "in the label is noise the model is trained on", "LEARNING_LOOP.md"))
        nm = ll.get("near_misses") or {}
        if nm.get("n_missed") and nm.get("feature_requests"):
            top = ", ".join(str(f.get("feature")) for f in nm["feature_requests"][:5])
            issues.append(_issue("evaluation", "near_misses", "feature_requests", "INFO",
                                 f"{nm['n_missed']} of {nm.get('n_forward_positives')} forward positives outside the "
                                 f"top {nm.get('top_k')}; they most often fall short on: {top}",
                                 "the features the model would have needed; candidates for the next scheme design",
                                 "LEARNING_LOOP.md"))
        cm = ll.get("calibration") or {}
        ece = cm.get("ece") if isinstance(cm, dict) else None
        if ece is not None and float(ece) > 0.05:
            issues.append(_issue("evaluation", "calibration", "ece_high", "WARN",
                                 f"expected calibration error {ece}",
                                 "the quoted probabilities are off by more than five points; recalibrate per "
                                 "subgroup before Model C reads them", "LEARNING_LOOP.md"))

    # --- what got worse since the previous run ---------------------------------------------------
    if prev_run_dir and Path(prev_run_dir).exists():
        prev = parse_run_dir(Path(prev_run_dir))
        pman = _json(Path(prev_run_dir) / "feature_manifest.json") or {}
        ef, pf = row.get("expectation_fails"), prev.get("expectation_fails")
        if ef is not None and pf is not None and int(ef) > int(pf):
            issues.append(_issue("regression", "expectations", "more_fails_than_previous", "WARN",
                                 f"{ef} FAILs now, {pf} in {Path(prev_run_dir).name}",
                                 "a calculation regressed between runs; diff the FAIL rows", "EXPECTATIONS_REPORT.md"))
        skipped_now = {s.get("source") for s in man.get("sources_audit") or [] if str(s.get("status", "")).lower().startswith("skip")}
        skipped_prev = {s.get("source") for s in pman.get("sources_audit") or [] if str(s.get("status", "")).lower().startswith("skip")}
        for src in sorted(skipped_now - skipped_prev):
            issues.append(_issue("regression", str(src), "newly_skipped", "WARN",
                                 f"used in {Path(prev_run_dir).name}, skipped now",
                                 "the file moved, was renamed, or failed to parse; SOURCES_REPORT has the reason",
                                 "SOURCES_REPORT.md"))
        n0, n1 = prev.get("n_providers"), row.get("n_providers")
        if n0 and n1 and abs(int(n1) - int(n0)) > 0.1 * int(n0):
            issues.append(_issue("regression", "universe", "provider_count_moved", "WARN",
                                 f"{int(n1):,} providers now, {int(n0):,} before",
                                 "the scored universe changed by more than a tenth; check the leads file and the freeze",
                                 "feature_manifest.json"))

    issues.sort(key=lambda i: (SEV_RANK.get(i["severity"], 9), i["area"], i["subject"]))
    summary = {"n_fail": sum(1 for i in issues if i["severity"] == "FAIL"),
               "n_warn": sum(1 for i in issues if i["severity"] == "WARN"),
               "n_info": sum(1 for i in issues if i["severity"] == "INFO"),
               "expectation_fails": row.get("expectation_fails"),
               "expectation_warns": row.get("expectation_warns"),
               "sources_skipped": row.get("sources_skipped"),
               "network_verdict": nv, "hits50": hits, "base50": base}
    return issues, summary


def suggested_checks(issues: list[dict], summary: dict) -> list[str]:
    """The --check expressions the NEXT pre-registration should carry so the fixes
    are gated, not narrated."""
    out = []
    if summary.get("expectation_fails"):
        out.append("fails = 0")
    if any(i["check"] == "verdict_not_keep" for i in issues):
        out.append("network = KEEP")
    hits, base = summary.get("hits50"), summary.get("base50")
    if hits is not None:
        out.append(f"hits50 >= {max(int(hits), 1) + 1 if base is not None and float(hits) <= float(base) else int(hits)}")
    if any(i["check"] == "institutional_share_high" for i in issues):
        out.append("inst_share50 <= 0.4")
    if any(i["check"] == "top50_unstable" for i in issues):
        out.append("top50_jaccard >= 0.6")
    return out


def update_ledger(ledger_path: Path, tag: str, issues: list[dict]) -> tuple[list[dict], list[dict]]:
    """Fold this run's issues into the cross-run ledger. Returns (issues annotated
    with first_seen / n_runs, newly resolved records)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    recs: dict[str, dict] = {}
    if ledger_path.exists():
        for line in _read(ledger_path).splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    recs[r["id"]] = r
                except Exception:
                    continue
    seen = {i["id"] for i in issues}
    resolved = []
    for rid, r in recs.items():
        if r.get("status") == "open" and rid not in seen and tag not in (r.get("runs") or []):
            r["status"] = "resolved"
            r["resolved_in"] = tag
            r["resolved_at"] = now
            resolved.append(r)
    for i in issues:
        r = recs.get(i["id"])
        if r is None:
            r = {"id": i["id"], "first_seen": tag, "first_seen_at": now, "runs": [], "status": "open",
                 "severity": i["severity"], "area": i["area"], "subject": i["subject"], "check": i["check"]}
            recs[i["id"]] = r
        if tag not in r["runs"]:
            r["runs"].append(tag)
        r["last_seen"] = tag
        r["last_seen_at"] = now
        r["status"] = "open"
        r["severity"] = i["severity"]
        r["action"] = i["action"]
        i["first_seen"] = r["first_seen"]
        i["n_runs"] = len(r["runs"])
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("".join(json.dumps(r, default=str) + "\n" for r in recs.values()), encoding="utf-8")
    # recurring issues outrank new ones of the same severity
    issues.sort(key=lambda i: (SEV_RANK.get(i["severity"], 9), -int(i.get("n_runs", 1)), i["area"], i["subject"]))
    return issues, resolved


def to_markdown(tag: str, issues: list[dict], summary: dict, resolved: list[dict],
                checks: list[str]) -> str:
    L = [f"# RUN ISSUES: {tag}", "",
         "_Every error, warning and open question this run's own reports raised, in one list, "
         "worst first and recurring before new. Each row names the fix. Nothing here was fixed "
         "automatically: the ledger makes a defect impossible to wave through twice, and the "
         "suggested checks gate the next pre-registration on the fix._", "",
         f"- FAIL {summary['n_fail']}, WARN {summary['n_warn']}, INFO {summary['n_info']}; "
         f"calc-integrity FAILs {summary.get('expectation_fails')}, sources skipped "
         f"{summary.get('sources_skipped')}, network verdict {summary.get('network_verdict')}, "
         f"hits at 50 {summary.get('hits50')} (random {summary.get('base50')})", ""]
    if checks:
        L += ["## Checks the next pre-registration should carry", "", "```"] + checks + ["```", ""]
    L += ["## The list", "",
          "| # | severity | seen in | area | subject | check | finding | fix |",
          "|--:|---|--:|---|---|---|---|---|"]
    for n, i in enumerate(issues, 1):
        L.append(f"| {n} | {i['severity']} | {i.get('n_runs', 1)} run(s) | {i['area']} | `{i['subject']}` | "
                 f"{i['check']} | {str(i['finding']).replace('|', '/')[:160]} | {str(i['action']).replace('|', '/')[:200]} |")
    if resolved:
        L += ["", "## Resolved since the previous run", ""]
        L += [f"- `{r['id']}` (first seen {r.get('first_seen')}, open across {len(r.get('runs') or [])} run(s))"
              for r in resolved]
    L += ["", "_Leads for counsel review, never accusations. An issue here is about the pipeline, "
          "never about a provider._"]
    # operator-facing: no em or en dashes (the source reports use them)
    return ("\n".join(L) + "\n").replace(" — ", ": ").replace("—", "-").replace("–", "-")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--data-root", default=os.environ.get("MEDICAID_DATA_ROOT", str(Path.home() / "Desktop/data")))
    ap.add_argument("--prev-run-dir", default=None, help="an earlier run folder for the what-got-worse section")
    ap.add_argument("--tag", default=None, help="run tag (default: the folder name)")
    ap.add_argument("--no-ledger", action="store_true", help="do not touch <data-root>/model_a/issues_ledger.jsonl")
    ap.add_argument("--out", default=None, help="default <run-dir>/RUN_ISSUES.md")
    a = ap.parse_args()
    from .run_registry import resolve_run_dir
    d = Path(resolve_run_dir(a.run_dir))
    tag = a.tag or d.name
    prereg = None
    pre_p = Path(a.data_root) / "model_a" / "preregistrations.jsonl"
    if pre_p.exists():
        for line in _read(pre_p).splitlines():
            try:
                p = json.loads(line)
            except Exception:
                continue
            if p.get("tag") == tag and not p.get("superseded"):
                prereg = p
    issues, summary = collect_issues(d, prereg, a.prev_run_dir)
    resolved: list[dict] = []
    if not a.no_ledger:
        issues, resolved = update_ledger(Path(a.data_root) / "model_a" / "issues_ledger.jsonl", tag, issues)
    checks = suggested_checks(issues, summary)
    out = Path(a.out) if a.out else d / "RUN_ISSUES.md"
    out.write_text(to_markdown(tag, issues, summary, resolved, checks), encoding="utf-8")
    (out.parent / "run_issues.json").write_text(
        json.dumps({"tag": tag, "summary": summary, "suggested_checks": checks, "issues": issues,
                    "resolved": resolved}, indent=2, default=str), encoding="utf-8")
    print(f"[run_issues] {tag}: {summary['n_fail']} FAIL, {summary['n_warn']} WARN, {summary['n_info']} INFO; "
          f"{len(resolved)} resolved since the previous run -> {out}")


if __name__ == "__main__":
    main()
