"""
scheme_triage.py — the underperformance feedback loop, six questions per scheme.

SCHEME_HEALTH tells us WHICH schemes are underperforming every run; nothing
answered WHY. This module is the standing diagnosis: for every scheme that is
not healthy it assembles the run's own evidence into the six questions the
operator asks, each answered from data and each ending in a concrete action:

  Q1  Why do we think it is underperforming?     (status, AUC, direction,
      coverage, and whether the label can even see this scheme type)
  Q2  Were the analytics built correctly?        (the design: inputs + weights
      vs the scheme's spec, one-sidedness, what a reviewer should re-derive)
  Q3  Are data fields missing from the source?   (missing inputs, mapped to
      the source and file that should produce each)
  Q4  Were the analytics coded correctly?        (calc-integrity findings on
      this scheme's columns: out-of-range, constant, all-empty, inversion)
  Q5  Were the data wired in correctly?          (the feeding sources' audit
      rows — used/skipped and the file read — vs the subscore's coverage)
  Q6  Did we grab the right data files?          (file actually read vs the
      registry's expected file, vintage caps, skip reasons)

Inputs are the artifacts every run already writes: scheme_health.json,
feature_manifest.json (sources_audit + sources_used), EXPECTATIONS_REPORT.md,
and CASE_VALIDATION.md when present. Output: SCHEME_TRIAGE.md — one section
per underperforming scheme. Wired into the standard run flow next to the
pursuit dossier; conclusions feed fixes and the next pre-registration, never
gate the current run.

  python -m src.model_a.scheme_triage --run-dir <run_folder>
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

from .scheme_subscores import DEFAULT_SCHEME_WEIGHTS

# Scheme types an exclusion-based label largely cannot see (kickback, quality,
# cost-report conduct rarely ends in an OIG exclusion). For these, a low label
# AUC is expected BY CONSTRUCTION and the verdict defers to the DOJ case label.
EXCLUSION_BLIND = {
    "pharma_kickback", "cost_report_fraud", "worthless_services",
    "saturation_fraud", "hospice_ineligibility", "contract_pharmacy",
    "pill_mill", "upcoding",
}


# --- evidence loaders --------------------------------------------------------

def load_run(run_dir: str | Path) -> dict:
    d = Path(run_dir)
    ev: dict = {"run_dir": str(d)}
    hp = d / "scheme_health.json"
    if hp.exists():
        # write_health's real shape is {"schemes": [...], "regressions": [...]};
        # tolerate a bare records list too (older/synthetic files)
        payload = json.loads(hp.read_text(encoding="utf-8"))
        rows = (payload.get("schemes", []) if isinstance(payload, dict)
                else payload)
        ev["health"] = pd.DataFrame(rows)
    else:
        ev["health"] = pd.DataFrame()
    mp = d / "feature_manifest.json"
    man = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
    ev["sources_used"] = man.get("sources_used", {}) or {}
    ev["sources_audit"] = {r.get("source"): r
                           for r in (man.get("sources_audit") or [])}
    ev["expectations"] = _parse_expectations(d / "EXPECTATIONS_REPORT.md")
    ev["case_validation"] = _parse_case_validation(d / "CASE_VALIDATION.md")
    return ev


def _parse_expectations(p: Path) -> list[dict]:
    """The findings table → [{severity, column, check, observed}]."""
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\|\s*(FAIL|WARN)\s*\|\s*`?([^|`]+)`?\s*\|\s*([^|]+)\|"
                     r"\s*([^|]+)\|", ln)
        if m:
            out.append({"severity": m.group(1), "column": m.group(2).strip(),
                        "check": m.group(3).strip(),
                        "observed": m.group(4).strip()})
    return out


def _parse_case_validation(p: Path) -> dict:
    """scheme → {proven, auc, verdict} from the CASE_VALIDATION table."""
    if not p.exists():
        return {}
    out = {}
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 7 and cells[0] and " " not in cells[0] \
                and cells[0] not in ("scheme", "---"):
            out[cells[0]] = {"proven": cells[2], "auc": cells[3],
                             "verdict": cells[-1]}
    return out


def _col_to_source(sources_used: dict) -> dict:
    inv: dict[str, str] = {}
    for src, cols in sources_used.items():
        for c in cols or []:
            inv.setdefault(c, src)
    return inv


def _registry() -> dict:
    """preflight's source catalog: source key → expected folder/file/adapter."""
    try:
        from src.preflight import CORE, UNLOCKS
        return {s.key: s for s in list(CORE) + list(UNLOCKS)}
    except Exception:
        return {}


# --- selection + diagnosis ---------------------------------------------------

def select_underperformers(health: pd.DataFrame,
                           auc_floor: float = 0.55) -> pd.DataFrame:
    """Every scheme that is not pulling its weight, with the trigger named."""
    if not len(health):
        return health
    h = health.copy()
    weak_auc = (pd.to_numeric(h.get("label_auc"), errors="coerce") < auc_floor) \
        & (pd.to_numeric(h.get("n_label_pos"), errors="coerce") >= 20)
    triggered = (h["status"] != "HEALTHY") | weak_auc
    h = h.loc[triggered].copy()
    h["trigger"] = [
        (row["status"] if row["status"] != "HEALTHY"
         else f"label AUC {row.get('label_auc')} on "
              f"{int(row.get('n_label_pos') or 0)} positives")
        for _, row in h.iterrows()]
    return h.reset_index(drop=True)


def diagnose(row: pd.Series, ev: dict) -> dict:
    scheme = row["scheme"]
    wmap = DEFAULT_SCHEME_WEIGHTS.get(scheme, {})
    col2src = _col_to_source(ev["sources_used"])
    reg = _registry()
    audit = ev["sources_audit"]
    missing = [c for c in str(row.get("missing_inputs") or "").split(";") if c]
    cov = row.get("subscore_coverage")
    auc = row.get("label_auc")
    blind = scheme in EXCLUSION_BLIND
    cv = ev["case_validation"].get(scheme)

    # Q1 — why underperforming
    q1 = []
    q1.append(f"Run status: **{row['status']}**"
              + (f"; label AUC {auc} on {int(row.get('n_label_pos') or 0)} "
                 f"covered positives" if auc == auc else "; no measurable AUC"))
    if str(row.get("direction") or "").startswith("INVERTED"):
        q1.append("The subscore ranks known positives BELOW average — either "
                  "an inverted calculation (a lower-is-worse input fed in "
                  "un-flipped) or the label cannot see this conduct.")
    if blind:
        q1.append("This scheme type rarely ends in an exclusion, so a weak "
                  "exclusion-label AUC is expected BY CONSTRUCTION; the real "
                  "test is the DOJ case label"
                  + (f" (case validation: {cv['verdict']}, AUC {cv['auc']} on "
                     f"{cv['proven']} proven cases)" if cv else
                     " (no case-validation coverage yet — a harvest gap)."))
    if cov == cov and float(cov or 0) < 0.05:
        q1.append(f"Coverage is only {float(cov):.1%} of providers — with so "
                  "few scored rows the scheme cannot move any aggregate "
                  "metric, whatever its quality.")

    # Q2 — analytics built correctly (design)
    q2 = [("Design under review: inputs and weights are "
           + (", ".join(f"`{c}` (w={w})" for c, w in wmap.items())
              if wmap else "(no weights registered — scheme not in the "
                           "registry, which is itself the finding)"))]
    q2.append("Reviewer checklist: does each input measure EXCESS (one-sided), "
              "is the peer group the right comparison for this conduct, and "
              "does the spec in docs/RUNBOOK_TRAVIS.md §5.4 still match this "
              "input list?")
    if len(wmap) == 1:
        q2.append("Single-input scheme: the subscore is only as good as that "
                  "one column — consider whether the spec intended more.")

    # Q3 — fields missing from source data
    q3 = []
    for c in missing:
        src = col2src.get(c)
        if src:
            a = audit.get(src) or {}
            q3.append(f"`{c}` should come from source `{src}` "
                      f"({a.get('status', 'not in audit')}: "
                      f"{(a.get('detail') or '')[:90]})")
        else:
            r = next((s for k, s in reg.items() if k in c or c.startswith(k)),
                     None)
            q3.append(f"`{c}` is produced by NO wired source this run — "
                      + (f"expected from preclean/{r.folder}/ ({r.names[0]}), "
                         f"adapter {r.adapter}" if r else
                         "an unbuilt builder or un-procured file (see "
                         "docs/platform/12 for the drop point)."))
    if not q3:
        q3.append("No declared inputs are missing — the gap, if any, is not "
                  "absent fields.")

    # Q4 — analytics coded correctly
    scheme_cols = set(wmap) | {f"subscore_{scheme}"} \
        | {f"{c}__peerpct" for c in wmap}
    hits = [f for f in ev["expectations"] if f["column"] in scheme_cols]
    q4 = [f"{f['severity']}: `{f['column']}` — {f['check']} ({f['observed']})"
          for f in hits]
    if str(row.get("direction") or "").startswith("INVERTED"):
        q4.append("ACTION: re-derive the sign of every input by hand on 20 "
                  "known positives — one un-flipped lower-is-worse metric "
                  "inverts the whole subscore.")
    if bool(row.get("subscore_constant")):
        q4.append("The subscore is CONSTANT on its covered rows — a "
                  "miscalculation or a dead input, not a data gap.")
    if not q4:
        q4.append("No calc-integrity findings touch this scheme's columns.")

    # Q5 — data wired in correctly
    feeding = sorted({col2src[c] for c in wmap if c in col2src})
    q5 = []
    for src in feeding:
        a = audit.get(src) or {}
        q5.append(f"source `{src}`: {a.get('status', 'NOT IN AUDIT')} — "
                  f"{(a.get('detail') or 'no detail')[:110]}")
    if cov == cov and feeding and float(cov or 0) < 0.5 \
            and all((audit.get(s) or {}).get("status") == "used"
                    for s in feeding):
        q5.append(f"WIRING FLAG: every feeding source reports used, yet the "
                  f"subscore covers only {float(cov):.1%} of providers — "
                  "check the join keys (NPI vs CCN vs org grain) between the "
                  "adapter output and the matrix.")
    if not q5:
        q5.append("No feeding source resolves from the manifest — the scheme's "
                  "inputs are derived columns; wiring question moves to the "
                  "builder that computes them.")

    # Q6 — right data files
    q6 = []
    for src in feeding or []:
        a = audit.get(src) or {}
        r = reg.get(src)
        detail = a.get("detail") or ""
        fm = re.search(r"from ([\w.\-]+\.(?:csv|parquet|xlsx|zip))", detail)
        got = fm.group(1) if fm else None
        if r and got:
            q6.append(f"`{src}`: read `{got}`; registry expects "
                      f"`preclean/{r.folder}/{r.names[0]}` — "
                      + ("consistent" if any(
                          got.lower().startswith(n.split('.')[0].lower())
                          for n in r.names) else "NAME MISMATCH, verify this "
                         "is the intended file"))
        elif r:
            q6.append(f"`{src}`: registry expects `preclean/{r.folder}/"
                      f"{r.names[0]}` (audit detail names no file)")
        elif got:
            q6.append(f"`{src}`: read `{got}` (derived source, no registry "
                      "entry)")
    if not q6:
        q6.append("Inputs are computed from the core fact tables — file "
                  "choice is not in play for this scheme.")

    return {"scheme": scheme, "trigger": row.get("trigger", row["status"]),
            "q1": q1, "q2": q2, "q3": q3, "q4": q4, "q5": q5, "q6": q6}


def to_markdown(diagnoses: list[dict], ev: dict) -> str:
    L = ["# SCHEME TRIAGE — why the underperformers underperform", ""]
    L.append("_The standing feedback loop: every scheme flagged by "
             "SCHEME_HEALTH (or scoring below the AUC floor with enough "
             "positives) gets the six-question diagnosis, each answered from "
             "this run's own evidence. Conclusions feed fixes and the next "
             "pre-registration; they never gate the current run._")
    L.append("")
    if not diagnoses:
        L.append("**No underperforming schemes this run.**")
        return "\n".join(L)
    L.append("| scheme | trigger |")
    L.append("|---|---|")
    for d in diagnoses:
        L.append(f"| {d['scheme']} | {d['trigger']} |")
    QS = [("q1", "1. Why do we think it is underperforming?"),
          ("q2", "2. Were the analytics built correctly? (design)"),
          ("q3", "3. Are data fields missing from the source data?"),
          ("q4", "4. Were the analytics coded correctly?"),
          ("q5", "5. Were the data wired in correctly?"),
          ("q6", "6. Did we grab the right data files?")]
    for d in diagnoses:
        L.append("")
        L.append(f"## {d['scheme']}  —  {d['trigger']}")
        for key, title in QS:
            L.append("")
            L.append(f"**{title}**")
            for item in d[key]:
                L.append(f"- {item}")
    L.append("")
    L.append("_Reading order: Q1 says what the symptom is; Q3/Q5/Q6 are "
             "checkable from files and usually explain DEGRADED and DORMANT; "
             "Q4 explains BROKEN and INVERTED; Q2 is the human review that "
             "remains when the mechanical answers come back clean._")
    L.append("")
    return "\n".join(L)


def run_triage(run_dir: str | Path, out: str | Path | None = None,
               auc_floor: float = 0.55) -> Path:
    ev = load_run(run_dir)
    under = select_underperformers(ev["health"], auc_floor=auc_floor)
    diagnoses = [diagnose(row, ev) for _, row in under.iterrows()]
    out_p = Path(out) if out else Path(run_dir) / "SCHEME_TRIAGE.md"
    out_p.write_text(to_markdown(diagnoses, ev), encoding="utf-8")
    print(f"[scheme_triage] {len(diagnoses)} scheme(s) diagnosed -> {out_p}")
    return out_p


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--auc-floor", type=float, default=0.55)
    args = ap.parse_args()
    run_triage(args.run_dir, args.out, args.auc_floor)


if __name__ == "__main__":
    main()
