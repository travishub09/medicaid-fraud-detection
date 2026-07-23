"""
research_sweep.py — run the Manus research tasks over the top-N leads.

The manus_research module has the task templates (reality score, corporate
network, public-disclosure screen, innocent audit, license/address checks). This
is the orchestration that turns them into a pipeline step: pick the top-N leads,
dispatch the chosen tasks for each, cache the structured results, and write a
per-NPI enrichment artifact the dossier generator reads. It is the research-layer
analog of run_detectors.

Discipline (unchanged): top-N only (Manus is slow and paid, and a live web value
is not a reproducible universe feature); public identifiers only; every result
cached and stamped; the output is review evidence and, with a human, label
input, never an auto-asserted fact or a training column.

    python -m src.model_a.research_sweep --pack dossier_pack.parquet --top 25 \\
        --tasks reality --out research/    # needs MANUS_API_KEY for the live run
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.attempt_2.clean_data import canonicalize_npi
from src.feeds.manus_research import (ManusTransport, reality_score,
                                      public_disclosure_screen,
                                      innocent_explanation_audit)

# task name -> (callable, how to build its args from a lead row)
_TASKS = {
    "reality": lambda r, t: reality_score(
        r["npi"], r.get("name", ""), r.get("city", ""), r.get("state", ""),
        transport=t),
    "disclosure": lambda r, t: public_disclosure_screen(
        r["npi"], r.get("descriptor", "a high-dollar biller"), transport=t),
    "innocent": lambda r, t: innocent_explanation_audit(
        r["npi"], r.get("pattern", "an anomalous billing concentration"),
        transport=t),
}


def _suspect(row) -> float:
    net = pd.to_numeric(pd.Series([row.get("net_paid")]), errors="coerce").iloc[0]
    exp = pd.to_numeric(pd.Series([row.get("expected_net_paid")]),
                        errors="coerce").iloc[0]
    net = 0.0 if pd.isna(net) else net
    exp = 0.0 if pd.isna(exp) else exp
    return max(net - exp, 0.0)


def select_leads(pack: pd.DataFrame, top: int, rank_col: str | None = None,
                 npis: list[str] | None = None) -> pd.DataFrame:
    """Pick the leads to research: an explicit NPI list, or the top-N by
    suspect dollars (default) / a named rank column."""
    p = pack.copy()
    p["npi"] = p["npi"].astype(str)
    if npis:
        want = {canonicalize_npi(n) for n in npis}
        want.discard(None)
        return p[p["npi"].isin(want)]
    if rank_col and rank_col in p.columns:
        p = p.sort_values(rank_col, ascending=(rank_col == "priority_rank"),
                          na_position="last")
    else:
        p = p.assign(_suspect=p.apply(_suspect, axis=1)).sort_values(
            "_suspect", ascending=False)
    return p.head(top)


def _lead_row(row: pd.Series) -> dict:
    name = str(row.get("provider_name") or row.get("org_legal_name") or "").strip()
    return {"npi": str(row["npi"]), "name": name,
            "city": str(row.get("addr_city") or ""),
            "state": str(row.get("practice_state") or "")}


def run_sweep(pack: pd.DataFrame, tasks: list[str], top: int = 25,
              rank_col: str | None = None, npis: list[str] | None = None,
              transport: ManusTransport | None = None) -> dict[str, dict]:
    """Dispatch the chosen tasks for each selected lead.

    Returns {npi: {task: envelope}}. A failed task records its error rather than
    aborting the sweep, so one bad lookup never sinks the batch."""
    bad = [t for t in tasks if t not in _TASKS]
    if bad:
        raise ValueError(f"unknown task(s) {bad}; known: {sorted(_TASKS)}")
    leads = select_leads(pack, top, rank_col, npis)
    out: dict[str, dict] = {}
    for _, row in leads.iterrows():
        lead = _lead_row(row)
        npi = lead["npi"]
        out[npi] = {}
        for task in tasks:
            try:
                out[npi][task] = _TASKS[task](lead, transport)
            except Exception as e:                       # one lead never sinks the batch
                out[npi][task] = {"ok": False, "error": str(e), "result": None}
    return out


def write_enrichment(results: dict[str, dict], out_dir: str | Path) -> Path:
    """Write per-NPI enrichment JSON (dossier_build reads reality/disclosure/
    innocent from it) + a flat reality-score summary table."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "research_enrichment.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    rows = []
    for npi, tasks in results.items():
        r = (tasks.get("reality") or {}).get("result")
        score = r.get("reality_score") if isinstance(r, dict) else None
        rows.append({"npi": npi, "reality_score": score,
                     "reality_gaps": "; ".join((r or {}).get("gaps", [])[:5])
                     if isinstance(r, dict) else "",
                     "tasks_run": ";".join(sorted(tasks))})
    pd.DataFrame(rows).to_parquet(out / "research_summary.parquet", index=False)
    return out / "research_enrichment.json"


def load_enrichment(path: str | Path) -> dict[str, dict]:
    """Load a research_enrichment.json for dossier_batch to attach per NPI."""
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pack", required=True)
    ap.add_argument("--tasks", default="reality",
                    help="comma list: reality,disclosure,innocent")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--rank-col", default=None)
    ap.add_argument("--npis", default=None, help="explicit NPIs (overrides --top)")
    ap.add_argument("--out", default="research")
    args = ap.parse_args()
    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    npis = [x.strip() for x in args.npis.split(",")] if args.npis else None
    results = run_sweep(pd.read_parquet(args.pack), tasks, top=args.top,
                        rank_col=args.rank_col, npis=npis)
    path = write_enrichment(results, args.out)
    n_ok = sum(1 for v in results.values()
               for e in v.values() if isinstance(e, dict) and e.get("ok"))
    print(f"[research_sweep] {len(results)} leads x {len(tasks)} tasks, "
          f"{n_ok} ok -> {path}")


if __name__ == "__main__":
    main()
