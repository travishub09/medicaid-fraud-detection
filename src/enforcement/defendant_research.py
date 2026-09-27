"""
defendant_research.py — the research queue for defendants the resolver could not settle.

The deterministic resolver (``defendant_resolver``) leaves three kinds of residue:
AMBIGUOUS mentions (several NPPES records share the name in the state), UNMATCHED
mentions (no record with that name — often a non-provider: an owner, a recruiter, a
marketer; sometimes a trade name), and chains (never researched). This module turns
the residue into a reviewable, tiered batch of public-records research tasks, sends
them through the platform's existing Manus plumbing (``pursuit_dossier.dispatch_research``
/ ``poll_research``, re-attachable registry, rate-limit safe), and folds the answers
back into ``case_defendant_npis.parquet`` so the next provider-features build labels
those NPIs with basis ``resolved_defendant``.

Tiers (the operator picks; nothing is sent without an explicit --dispatch):
  1  ambiguous people + ambiguous small organizations        (a docket settles a tie)
  2  unmatched mentions on cases at/above --min-amount       (default $2M)
  3  everything else                                          (never sent)

Batch hygiene before anything is sent: one task per (defendant, state); single-token
fragments dropped ("Thomley"); role phrases dropped ("Pharmacist of A to Z Pharmacy");
national manufacturers / public health-care corporations dropped (their NPIs are not
provider labels and the org tier already covers enrolled chains).

Fold-back rule: an answer counts only when it carries a Luhn-valid 10-digit NPI and
confidence high or medium; low-confidence answers are recorded but never become a
label. Every folded row keeps the task id so the evidence is re-readable.

  python -m src.enforcement.defendant_research --resolved processed/case_defendant_npis.parquet \
      --out-dir processed/defendant_research --tiers 1,2 --states FL,GA,AL,MS,TN,NC,SC,LA,KY
  ... review defendant_research_batch.csv, then:
  python -m src.enforcement.defendant_research --out-dir processed/defendant_research --dispatch
  python -m src.enforcement.defendant_research --out-dir processed/defendant_research --poll \
      --resolved processed/case_defendant_npis.parquet
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import is_valid_npi
from src.enforcement.defendant_resolver import RESEARCH_PROMPT, SE_STATES

_ROLE_PREFIX = re.compile(r"^(?:pharmacist|owner|operator|ceo|cfo|president|physician|doctor|nurse|"
                          r"administrator|manager|director|employee|former)\b", re.I)
_NATIONAL = re.compile(r"\b(?:pharmaceutical|pharmaceuticals|therapeutics|biosciences|corporation|corp|"
                       r"holdings|mckesson|tenet|davita|insys|mallinckrodt|walgreens|walgreen|cvs|omnicare|"
                       r"kindred|hca|community health systems|unitedhealth|humana|anthem|cigna|aetna|"
                       r"labcorp|quest diagnostics|molina|centene|wellcare|amerigroup|purdue|abbott|"
                       r"pfizer|novartis|amgen|genentech|johnson & johnson|medtronic|boston scientific|"
                       r"fresenius|encompass|lifepoint|universal health services|acadia)\b", re.I)


from src.model_a.state_fca import _NAME_TO_ABBR as _STATE_NAMES_MAP
_PLACE = re.compile(r",\s*(?:" + "|".join(n.title() for n in _STATE_NAMES_MAP) + r")\s*$")


def _is_place(name: str) -> bool:
    """'Fort Lauderdale, Florida' captured as a defendant: a place, not a party."""
    return bool(_PLACE.search(str(name or "")))


def _org_id(case_id: str, defendant: str, state: str) -> str:
    h = hashlib.md5(f"{case_id}|{defendant.upper()}|{state}".encode("utf-8")).hexdigest()[:12]
    return f"dfd:{h}"


def select_batch(resolved: pd.DataFrame, tiers: tuple[int, ...] = (1, 2), min_amount: float = 2_000_000.0,
                 states: list[str] | None = None, cap: int | None = None) -> pd.DataFrame:
    """The tiered, de-duplicated research batch. Returns the mention rows with ``tier``."""
    r = resolved.copy()
    r["amount_num"] = pd.to_numeric(r.get("amount_num", r.get("amount_usd")), errors="coerce").fillna(0.0)
    if states:
        r = r[r["state"].fillna("").str.upper().isin([s.upper() for s in states])]
    r["tier"] = 0
    r.loc[(r["status"] == "ambiguous"), "tier"] = 1
    r.loc[(r["status"] == "unmatched") & (r["amount_num"] >= float(min_amount)), "tier"] = 2
    r = r[r["tier"].isin(tiers)]
    d = r["defendant"].fillna("").astype(str).str.strip()
    keep = (d.str.split().str.len() >= 2) & ~d.map(lambda s: bool(_ROLE_PREFIX.match(s))) \
        & ~d.map(lambda s: bool(_NATIONAL.search(s))) & ~d.map(_is_place)
    r = r[keep]
    r["_k"] = d[keep].str.upper() + "|" + r["state"].fillna("").str.upper()
    r = (r.sort_values(["tier", "amount_num"], ascending=[True, False])
          .drop_duplicates("_k").drop(columns=["_k"]))
    if cap is not None:
        r = r.head(int(cap))
    r["org_id"] = [_org_id(str(c), str(dn), str(s)) for c, dn, s in zip(r["case_id"], r["defendant"], r["state"])]
    return r.reset_index(drop=True)


def write_prompts(batch: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pj = out_dir / "defendant_research_prompts.jsonl"
    with open(pj, "w", encoding="utf-8") as fh:
        for r in batch.itertuples(index=False):
            prompt = RESEARCH_PROMPT.format(defendant=r.defendant, kind=r.kind, city=r.city or "unknown",
                                            state=r.state or "unknown", announced_date=r.announced_date,
                                            case_id=r.case_id, amount_usd=r.amount_usd or "unknown",
                                            n_candidates=r.n_candidates)
            fh.write(json.dumps({"org_id": r.org_id, "org_name": r.defendant, "case_id": r.case_id,
                                 "defendant": r.defendant, "state": r.state, "tier": int(r.tier),
                                 "prompt": prompt}) + "\n")
    pc = out_dir / "defendant_research_batch.csv"
    batch[["tier", "org_id", "case_id", "defendant", "kind", "city", "state", "status", "n_candidates",
           "amount_num", "announced_date"]].to_csv(pc, index=False)
    return pj, pc


# ---- fold-back ---------------------------------------------------------------------------
_NPI_RE = re.compile(r"(?<!\d)(\d{10})(?!\d)")
_CONF_RE = re.compile(r"confidence\W{0,5}(high|medium|low)", re.I)


def parse_answer(output) -> dict:
    """{npi, confidence, entity_type} from a task's output (JSON preferred; text fallback).
    An NPI counts only when Luhn-valid."""
    text = output if isinstance(output, str) else json.dumps(output, default=str)
    npi, conf, et = "", "", ""
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            j = json.loads(m.group(0))
            if isinstance(j, dict):
                npi = re.sub(r"\D", "", str(j.get("npi") or ""))
                conf = str(j.get("confidence") or "").lower()
                et = str(j.get("entity_type") or "")
        except json.JSONDecodeError:
            pass
    if not npi:
        cands = [c for c in _NPI_RE.findall(text) if is_valid_npi(c)]
        npi = cands[0] if len(set(cands)) == 1 else ""
    if not conf:
        mc = _CONF_RE.search(text)
        conf = mc.group(1).lower() if mc else ""
    if npi and not (len(npi) == 10 and is_valid_npi(npi)):
        npi = ""
    return {"npi": npi, "confidence": conf, "entity_type": et}


def fold_results(resolved: pd.DataFrame, prompts_path: Path, results: dict) -> tuple[pd.DataFrame, dict]:
    """Apply completed answers to the resolved table. Returns (table, summary)."""
    items = {json.loads(l)["org_id"]: json.loads(l) for l in prompts_path.read_text(encoding="utf-8").splitlines() if l.strip()}
    out = resolved.copy()
    for c in ("task_id", "research_confidence"):
        if c not in out.columns:
            out[c] = ""
    n_hi = n_lo = n_none = 0
    for oid, env in results.items():
        if env.get("status") != "completed" or oid not in items:
            continue
        it = items[oid]
        ans = parse_answer(env.get("output") or "")
        mask = (out["case_id"].astype(str) == str(it["case_id"])) & (out["defendant"].astype(str) == str(it["defendant"]))
        if not mask.any():
            continue
        out.loc[mask, "task_id"] = str(env.get("task_id") or oid)
        out.loc[mask, "research_confidence"] = ans["confidence"]
        if ans["npi"] and ans["confidence"] in ("high", "medium"):
            out.loc[mask, "npi"] = ans["npi"]
            out.loc[mask, "status"] = "resolved"
            out.loc[mask, "match_level"] = f"research_{ans['confidence']}"
            n_hi += 1
        elif ans["npi"]:
            out.loc[mask, "status"] = "research_low"
            n_lo += 1
        else:
            out.loc[mask, "status"] = "research_no_npi"     # a non-provider, per the research
            n_none += 1
    return out, {"folded_resolved": n_hi, "low_confidence": n_lo, "no_npi": n_none}


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--resolved", default=None, help="processed/case_defendant_npis.parquet")
    ap.add_argument("--out-dir", required=True, help="folder for the batch, registry and results")
    ap.add_argument("--tiers", default="1,2")
    ap.add_argument("--min-amount", type=float, default=2_000_000.0)
    ap.add_argument("--states", default=",".join(SE_STATES))
    ap.add_argument("--cap", type=int, default=None, help="at most this many tasks (by tier, then dollars)")
    ap.add_argument("--dispatch", action="store_true", help="send the batch via Manus (needs $MANUS_API_KEY)")
    ap.add_argument("--max-new", type=int, default=None, help="cap tasks created per call")
    ap.add_argument("--status", action="store_true", help="show the registry: tasks with ids, without, completed")
    ap.add_argument("--poll", action="store_true", help="collect answers and fold them into --resolved")
    a = ap.parse_args()
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prompts = out_dir / "defendant_research_prompts.jsonl"
    registry = out_dir / "defendant_research_tasks.json"
    results_p = out_dir / "defendant_research_results.json"

    if a.status:
        reg = json.loads(registry.read_text(encoding="utf-8")) if registry.exists() else {}
        res = json.loads(results_p.read_text(encoding="utf-8")) if results_p.exists() else {}
        with_id = sum(1 for v in reg.values() if v.get("task_id"))
        done = sum(1 for v in res.values() if v.get("status") == "completed")
        n_prompts = sum(1 for l in prompts.read_text(encoding="utf-8").splitlines() if l.strip()) if prompts.exists() else 0
        print(f"registry: {len(reg)} entries, {with_id} with a task id, {len(reg) - with_id} without; "
              f"prompts in batch: {n_prompts}; completed answers on file: {done}")
        return
    if a.dispatch:
        from src.model_a.pursuit_dossier import dispatch_research
        if not prompts.exists():
            raise SystemExit(f"no batch at {prompts}; build it first (run without --dispatch/--poll)")
        info = dispatch_research(prompts, registry, max_new=a.max_new)
        print(f"defendant research dispatch: {info}")
        return
    if a.poll:
        from src.model_a.pursuit_dossier import poll_research
        if not a.resolved:
            raise SystemExit("--poll needs --resolved to fold answers into")
        results = poll_research(registry, results_p)
        table = pd.read_parquet(a.resolved)
        table, summ = fold_results(table, prompts, results)
        table.to_parquet(a.resolved, index=False)
        done = sum(1 for v in results.values() if v.get("status") == "completed")
        print(f"defendant research poll: {done}/{len(results)} completed; folded {summ} -> {a.resolved}")
        return
    if not a.resolved:
        raise SystemExit("--resolved is required to build the batch")
    res = pd.read_parquet(a.resolved) if a.resolved.endswith(".parquet") else pd.read_csv(a.resolved, dtype=str)
    tiers = tuple(int(t) for t in a.tiers.split(",") if t.strip())
    batch = select_batch(res, tiers, a.min_amount, [s.strip() for s in a.states.split(",") if s.strip()], a.cap)
    pj, pc = write_prompts(batch, out_dir)
    by = batch.groupby("tier").size().to_dict()
    print(f"defendant research batch: {len(batch)} tasks (" + ", ".join(f"tier {k}: {v}" for k, v in by.items())
          + f") -> {pj}\n  review: {pc}\n  then: --dispatch (sends), later --poll --resolved <parquet> (folds answers back)")


if __name__ == "__main__":
    main()
