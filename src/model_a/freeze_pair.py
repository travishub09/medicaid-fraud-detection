"""
freeze_pair.py — the two-ruler read-out (2026-10-21, the second freeze).

docs/FREEZE_DESIGN_NOTE_2026-10-21.md: the 2021-12 freeze is a LOWER BOUND of the
production model because several sources have no 2021 edition; a 2023-06 freeze
lights every source up but grades on fewer resolved cases. "A model that holds on
both rulers is stronger evidence than one ruler with half the features." This
module puts two (or more) frozen runs side by side from their own artifacts:

  * the grade: hits / random / p at every budget per book (accuracy_diagnostics
    json, pursuit + large + pmax), lead time, NPI top-1% lift, Jaccard;
  * the inputs: cutoff, horizon, forward positives in the universe, training
    positives and policy, sources USED at one freeze and SKIPPED at the other
    (the features the second freeze exists to test), the vintage ledger verdicts;
  * the verdict per check: HOLDS ON BOTH / holds on one / neither, for the checks
    that are comparable across freezes (hits above random at 50 / 100 / 250 on
    each book, institutional share, Jaccard).

Nothing here changes a bar; the pre-registration of a run is still per tag.

  python -m src.model_a.freeze_pair --runs <run_a> <run_b> [--out FREEZE_PAIR.md]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BOOKS = (("pursuit", ""), ("large", "__large"), ("pmax", "__pmax"))
BUDGETS = (16, 50, 100, 250)


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_run(run_dir: str | Path) -> dict:
    d = Path(run_dir)
    man = _json(d / "feature_manifest.json")
    tl = _json(d / "training_label_policy.json")
    fi = _json(d / "freeze_inputs.json")
    cutoff = man.get("asof_cutoff") or fi.get("cutoff")
    books = {}
    for name, suffix in BOOKS:
        acc = _json(d / f"accuracy_diagnostics{suffix}.json")
        if not acc:
            continue
        head = acc.get("headline_ruler") or next(iter(acc.get("rulers", {})), None)
        rul = (acc.get("rulers") or {}).get(head, {}) if head else {}
        rows = {r["budget"]: r for r in rul.get("org_budgets", []) if "budget" in r}
        npi = next((r for r in rul.get("npi_budgets", []) if r.get("budget") == "top 1.0%"), {})
        tx = rul.get("error_taxonomy", [])
        n50 = sum(r.get("n", 0) for r in tx if r.get("k") == 50) or 1
        books[name] = {
            "ruler": head, "n_forward_universe": (rul.get("universe") or {}).get("positives"),
            "budgets": {k: {"hits": rows[k].get("hits"), "random": rows[k].get("random_expected"),
                            "p": rows[k].get("random_p"), "prec_lo": rows[k].get("wilson_lo")}
                        for k in BUDGETS if k in rows},
            "lead_median": (rul.get("lead_time") or {}).get("median_lead_months"),
            "npi_lift1pct": npi.get("lift_vs_random"),
            "inst_share50": sum(r.get("n", 0) for r in tx if r.get("k") == 50 and r.get("cause") == "institutional") / n50,
            "jaccard": (acc.get("stability") or {}).get("jaccard_top_org"),
            "horizon": acc.get("horizon_months"),
        }
    sa = {str(r.get("source")): r.get("status") for r in (man.get("sources_audit") or [])}
    vint = {str(v.get("source")): v.get("verdict") for v in (man.get("vintage_audit") or [])}
    return {"run": d.name, "cutoff": cutoff, "horizon": fi.get("horizon_months"),
            "train_policy": tl.get("label_tier"), "train_positives": tl.get("n_positives"),
            "train_doj": tl.get("n_doj_positives"), "sources": sa, "vintage": vint, "books": books,
            "nppes_rule": (fi.get("asof_nppes") or {}).get("rule"),
            "nppes_staleness": (fi.get("asof_nppes") or {}).get("staleness_months")}


def compare(runs: list[dict]) -> dict:
    """Per check: holds on which runs (hits > random at 50 / 100 / 250 per book;
    inst share <= 0.4; jaccard >= 0.6); plus the source difference."""
    checks = []
    for name, _ in BOOKS:
        for k in (50, 100, 250):
            holds = []
            for r in runs:
                b = (r["books"].get(name) or {}).get("budgets", {}).get(k)
                holds.append(bool(b and b["hits"] is not None and b["random"] is not None and b["hits"] > b["random"]))
            if any((r["books"].get(name) or {}).get("budgets", {}).get(k) for r in runs):
                checks.append({"check": f"{name} hits@{k} > random", "holds": holds})
        inst = [(r["books"].get(name) or {}).get("inst_share50") for r in runs]
        if any(x is not None for x in inst):
            checks.append({"check": f"{name} inst_share50 <= 0.4", "holds": [x is not None and x <= 0.4 for x in inst]})
    jac = [(r["books"].get("pursuit") or {}).get("jaccard") for r in runs]
    if any(x is not None for x in jac):
        checks.append({"check": "pursuit top50_jaccard >= 0.6", "holds": [x is not None and x >= 0.6 for x in jac]})
    for c in checks:
        c["verdict"] = ("HOLDS ON BOTH" if all(c["holds"]) and len(c["holds"]) > 1 else
                        "holds on " + ", ".join(runs[i]["run"] for i, h in enumerate(c["holds"]) if h) if any(c["holds"])
                        else "neither")
    all_sources = sorted({s for r in runs for s in r["sources"]})
    diff = [{"source": s, **{r["run"]: r["sources"].get(s, "absent") for r in runs}}
            for s in all_sources if len({r["sources"].get(s, "absent") for r in runs}) > 1]
    return {"checks": checks, "source_diff": diff}


def render(runs: list[dict], cmp: dict) -> str:
    L = ["# FREEZE PAIR — the same model graded on two freezes", "",
         "_docs/FREEZE_DESIGN_NOTE_2026-10-21.md: the 2021-12 freeze is a lower bound (sources without a "
         "2021 edition are untested there); the 2023-06 freeze lights every source but grades on fewer "
         "resolved cases. A check that HOLDS ON BOTH is the evidence; one that holds on only one names "
         "either a feature the early freeze could not test or a ruler too thin to tell._", "",
         "## 1. The runs", "", "| run | cutoff | horizon | train policy | train positives (DOJ) | NPPES edition rule | sources used |",
         "|---|---|---|---|---|---|---|"]
    for r in runs:
        used = sum(1 for v in r["sources"].values() if v == "used")
        npp = r["nppes_rule"] or ""
        if r["nppes_staleness"]:
            npp += f" ({r['nppes_staleness']:+d} mo)"
        L.append(f"| {r['run']} | {r['cutoff']} | {r['horizon'] or ''} | {r['train_policy'] or ''} | "
                 f"{r['train_positives'] or ''} ({r['train_doj'] or ''}) | {npp} | {used} |")
    L += ["", "## 2. The grade (hits / random expected, p vs random)", ""]
    for name, _ in BOOKS:
        if not any(r["books"].get(name) for r in runs):
            continue
        L += [f"### {name} book", "", "| run | forward positives in universe | " + " | ".join(f"@{k}" for k in BUDGETS)
              + " | lead (mo) | NPI top-1% lift | inst share@50 | Jaccard |",
              "|---|---|" + "---|" * len(BUDGETS) + "---|---|---|---|"]
        for r in runs:
            b = r["books"].get(name)
            if not b:
                L.append(f"| {r['run']} | (not graded) |" + " |" * (len(BUDGETS) + 4))
                continue
            cells = []
            for k in BUDGETS:
                x = b["budgets"].get(k)
                cells.append("" if not x else f"{x['hits']} / {x['random']:.1f} (p {x['p']:.3f})" if x.get("p") is not None
                             else f"{x['hits']} / {x['random']}")
            L.append(f"| {r['run']} | {b['n_forward_universe'] or ''} | " + " | ".join(cells)
                     + f" | {b['lead_median'] or ''} | {b['npi_lift1pct'] or ''} | {b['inst_share50']:.2f} | "
                     f"{'' if b['jaccard'] is None else round(b['jaccard'], 2)} |")
        L.append("")
    L += ["## 3. The checks across freezes", "", "| check | " + " | ".join(r["run"] for r in runs) + " | verdict |",
          "|---|" + "---|" * len(runs) + "---|"]
    for c in cmp["checks"]:
        L.append(f"| {c['check']} | " + " | ".join("yes" if h else "no" for h in c["holds"]) + f" | {c['verdict']} |")
    n_both = sum(1 for c in cmp["checks"] if c["verdict"] == "HOLDS ON BOTH")
    L += ["", f"{n_both} of {len(cmp['checks'])} comparable checks hold on both freezes.", ""]
    L += ["## 4. Sources that differ between the freezes (what the second freeze tests)", ""]
    if cmp["source_diff"]:
        L += ["| source | " + " | ".join(r["run"] for r in runs) + " |", "|---|" + "---|" * len(runs)]
        for row in cmp["source_diff"]:
            L.append(f"| `{row['source']}` | " + " | ".join(str(row.get(r["run"], "")) for r in runs) + " |")
    else:
        L.append("_every source has the same status on both freezes_")
    L += ["", "## 5. Vintage verdicts that are not pre_cutoff", ""]
    rows = [(r["run"], s, v) for r in runs for s, v in r["vintage"].items() if v not in ("pre_cutoff", "current_day_newest")]
    if rows:
        L += ["| run | source | verdict |", "|---|---|---|"] + [f"| {a} | `{b}` | {c} |" for a, b, c in rows]
    else:
        L.append("_none: every edition read was on/before its freeze_")
    L.append("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--runs", nargs="+", required=True, help="two or more run folders")
    ap.add_argument("--out", default=None, help="FREEZE_PAIR.md (default: beside the first run)")
    a = ap.parse_args()
    runs = [read_run(r) for r in a.runs]
    cmp = compare(runs)
    out = Path(a.out) if a.out else Path(a.runs[0]) / "FREEZE_PAIR.md"
    out.write_text(render(runs, cmp), encoding="utf-8")
    (out.with_suffix(".json")).write_text(json.dumps({"runs": runs, "compare": cmp}, indent=1, default=str),
                                          encoding="utf-8")
    n_both = sum(1 for c in cmp["checks"] if c["verdict"] == "HOLDS ON BOTH")
    print(f"[freeze_pair] {len(runs)} runs; {n_both}/{len(cmp['checks'])} comparable checks hold on both -> {out}")


if __name__ == "__main__":
    main()
