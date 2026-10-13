"""
case_harvest_prompts.py — research prompt sets that harvest prosecuted cases the feeds
never structure (sources 4, 5, 6), dispatched through the platform's Manus plumbing.

Every prompt asks for the SAME CSV schema, so a reviewed answer loads through
``research_evidence.load_mfcu_cases`` (human gate by construction) into the case
table, and the defendant resolver then turns the names into NPIs:

  announced_date, defendant_name, defendant_type, city, state, npi, provider_type_or_specialty,
  scheme, amount_usd, outcome, source_url, docket_or_case_number, conduct_start_year, conduct_end_year

Sets:
  mfcu       state Medicaid Fraud Control Unit / attorney general sweeps, one per state-year
  takedown   the National Health Care Fraud Takedown court-document pages, one per year
  yir        DOJ Fraud Section Health Care Fraud Unit year in review, one per year
  hcfac      the HHS/DOJ Health Care Fraud and Abuse Control Program annual report, one per FY
  oig_sar    OIG Semiannual Report to Congress case narratives, two per year

Workflow: build (writes case_harvest_prompts.jsonl + a review CSV) → --dispatch →
--poll (saves each answer under results/ for review; nothing loads by itself).

  python -m src.enforcement.case_harvest_prompts --out-dir processed/case_harvest --sets mfcu,takedown,yir,hcfac,oig_sar
  python -m src.enforcement.case_harvest_prompts --out-dir processed/case_harvest --dispatch
  python -m src.enforcement.case_harvest_prompts --out-dir processed/case_harvest --poll
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from src.enforcement.defendant_resolver import SE_STATES, mfcu_sweep_prompts

CSV_SCHEMA = ("announced_date (YYYY-MM-DD), defendant_name, defendant_type (individual or organization), city, "
              "state, npi (10 digits or blank), provider_type_or_specialty, scheme (billing for services not "
              "rendered / upcoding / kickbacks / medically unnecessary / identity or credential misuse / other), "
              "amount_usd (blank if none), outcome (conviction / plea / civil settlement / exclusion / charged), "
              "source_url, docket_or_case_number, conduct_start_year, conduct_end_year")
_RULES = ("Use only public sources. Do not contact anyone. One row per defendant. Blank beats a guess. Look "
          "up each provider's NPI in the NPPES NPI Registry (npiregistry.cms.hhs.gov) by name and city; leave "
          "it blank when more than one record fits. Return the CSV as an attachment plus a final row count "
          "and the list of pages you walked.")

TAKEDOWN_PROMPT = (
    "Public-records research task. The United States Department of Justice announces a National Health Care "
    "Fraud Takedown each year with a court-documents page listing every defendant by federal district. For the "
    "{year} takedown, build a CSV of every defendant charged in these districts: {districts}. Columns: "
    + CSV_SCHEMA + ". Take the city, state and role from the charging document or the district press release. " + _RULES)

YIR_PROMPT = (
    "Public-records research task. The DOJ Criminal Division Fraud Section publishes a Year in Review that "
    "lists Health Care Fraud Unit prosecutions (trials, pleas, sentencings) with defendant names and districts. "
    "For calendar year {year}, build a CSV of every health-care-fraud defendant whose district is in these "
    "states: {states}. Columns: " + CSV_SCHEMA + ". " + _RULES)

HCFAC_PROMPT = (
    "Public-records research task. The Health Care Fraud and Abuse Control Program annual report (HHS and DOJ) "
    "contains narrative case summaries naming providers and companies. For fiscal year {year}, extract every "
    "case summary whose provider or company is located in these states: {states}. Columns: " + CSV_SCHEMA
    + ". Use the report's own text for the amount and outcome; find the source press release for the date. " + _RULES)

OIG_SAR_PROMPT = (
    "Public-records research task. The HHS Office of Inspector General Semiannual Report to Congress for "
    "{period} contains case narratives naming providers, owners and companies. Extract every narrative "
    "involving a provider or company located in these states: {states}. Columns: " + CSV_SCHEMA + ". " + _RULES)

_SE_DISTRICTS = ("Middle, Northern and Southern Districts of Florida; Northern, Middle and Southern Districts of "
                 "Georgia; Northern, Middle and Southern Districts of Alabama; Northern and Southern Districts of "
                 "Mississippi; Eastern, Middle and Western Districts of Tennessee; Eastern, Middle and Western "
                 "Districts of North Carolina; District of South Carolina; Eastern, Middle and Western Districts "
                 "of Louisiana; Eastern and Western Districts of Kentucky")


CIA_PROMPT = (
    "Public-records research task. Each party below signed an OIG Corporate Integrity Agreement, which "
    "accompanies a resolved False Claims Act settlement. For EACH party: (1) find the settlement announcement "
    "(DOJ or U.S. Attorney press release, HHS-OIG, or a state attorney general release) and give one row in "
    "CSV A with columns: " + CSV_SCHEMA + ", qui_tam (yes/no/unknown), relator_role (former employee / "
    "competitor / other / unknown), conduct_summary (one sentence); (2) list the Medicare- or Medicaid-enrolled "
    "entities that belong to the party (the subsidiaries, d/b/a names and practice entities named in the "
    "release, the CIA, or the party's own site) in CSV B with columns: party, enrolled_entity_name, city, state, "
    "npi (10 digits from the NPPES NPI Registry, blank when more than one record fits), evidence_url. "
    "Parties: {parties}. " + _RULES)

EXCL_NAME_PROMPT = (
    "Public-records research task. Each row below is a person or business excluded from federal health "
    "programs (OIG LEIE / SAM / a state Medicaid list) whose record carries no NPI; our own exact lookup found "
    "several NPPES records with that name in that state (the candidate NPIs are listed). For EACH row, decide "
    "which NPPES record is the excluded party, using the specialty, the exclusion-era address, the conviction "
    "press release or the state board order, and return CSV columns: source_node_id, entity_name, state, npi "
    "(the chosen 10-digit NPI, blank when it cannot be settled), confidence (high / medium / low), "
    "employer_name (the practice or company the person worked for at the time, if a public record says), "
    "employer_npi (from NPPES, blank if unsure), evidence_url. Rows: {rows}. " + _RULES)


def _oid(kind: str, key: str) -> str:
    return f"harv:{kind}:{hashlib.md5(key.encode()).hexdigest()[:10]}"


def build_prompts(sets: list[str], years: list[int] | None = None, states: list[str] | None = None) -> list[dict]:
    years = years or list(range(2019, 2026))
    states = states or SE_STATES
    st_txt = ", ".join(states)
    out: list[dict] = []
    if "mfcu" in sets:
        for p in mfcu_sweep_prompts(states, years):
            out.append({"org_id": _oid("mfcu", f"{p['state']}{p['year']}"), "org_name": f"MFCU sweep {p['state']} {p['year']}",
                        "set": "mfcu", "year": p["year"], "state": p["state"], "prompt": p["prompt"]})
    if "takedown" in sets:
        for y in years:
            out.append({"org_id": _oid("takedown", str(y)), "org_name": f"Takedown {y}", "set": "takedown", "year": y,
                        "state": "", "prompt": TAKEDOWN_PROMPT.format(year=y, districts=_SE_DISTRICTS)})
    if "yir" in sets:
        for y in years:
            out.append({"org_id": _oid("yir", str(y)), "org_name": f"Fraud Section year in review {y}", "set": "yir",
                        "year": y, "state": "", "prompt": YIR_PROMPT.format(year=y, states=st_txt)})
    if "hcfac" in sets:
        for y in years:
            out.append({"org_id": _oid("hcfac", str(y)), "org_name": f"HCFAC report FY{y}", "set": "hcfac", "year": y,
                        "state": "", "prompt": HCFAC_PROMPT.format(year=y, states=st_txt)})
    if "oig_sar" in sets:
        for y in years:
            for half, period in ((1, f"October 1, {y - 1} to March 31, {y}"), (2, f"April 1, {y} to September 30, {y}")):
                out.append({"org_id": _oid("oig_sar", f"{y}h{half}"), "org_name": f"OIG semiannual {y} H{half}",
                            "set": "oig_sar", "year": y, "state": "", "prompt": OIG_SAR_PROMPT.format(period=period, states=st_txt)})
    return out


def _cia_missing(cia_norm: pd.DataFrame, case_db: pd.DataFrame | None, fuzzy: float = 0.92) -> pd.DataFrame:
    """CIA parties with no exact (or conservative difflib) name-key match in the case DB:
    the harvest list (each has a public settlement)."""
    import difflib
    from src.entity_graph.resolve_entities import norm_org_name
    if cia_norm is None or not len(cia_norm):
        return cia_norm
    keys = cia_norm["name_key"].fillna("").astype(str).str.upper()
    if case_db is None or not len(case_db):
        return cia_norm
    ck = case_db.get("defendant_name_key")
    if ck is None and "defendant_name" in case_db.columns:
        ck = case_db["defendant_name"].fillna("").astype(str).map(norm_org_name)
    target = {str(k).upper() for k in (ck.fillna("").astype(str) if ck is not None else [])} - {""}
    pool = [k for k in target if len(k) >= 8]
    hit = keys.isin(target)
    for i, k in keys[~hit].items():
        if len(k) >= 8 and difflib.get_close_matches(k, pool, n=1, cutoff=fuzzy):
            hit.loc[i] = True
    return cia_norm[~hit].reset_index(drop=True)


def cia_prompts(cia_norm: pd.DataFrame, case_db: pd.DataFrame | None = None,
                residue: pd.DataFrame | None = None, per_prompt: int = 5) -> list[dict]:
    """One prompt per ``per_prompt`` CIA parties: the parties MISSING from the case DB
    (the settlement release) plus the parties the ownership resolver could not place
    (the subsidiary crosswalk).  A party in both lists is sent once."""
    miss = _cia_missing(cia_norm, case_db)
    want = {}
    for r in miss.itertuples():
        want[str(r.name_key).upper()] = (str(r.entity_name), str(getattr(r, "state", "") or ""),
                                         str(getattr(r, "effective_date", "") or "")[:10], "missing_from_case_db")
    if residue is not None and len(residue):
        for r in residue.itertuples():
            k = str(getattr(r, "name_key", "") or "").upper()
            if k and k not in want:
                want[k] = (str(r.defendant_name), "", "", f"unresolved:{getattr(r, 'reason', '')}")
    items = list(want.items())
    out = []
    for i in range(0, len(items), max(1, int(per_prompt))):
        chunk = items[i:i + per_prompt]
        parties = "; ".join(f"{nm} ({st}, CIA effective {eff}; {why})" if st or eff else f"{nm} ({why})"
                            for _, (nm, st, eff, why) in chunk)
        out.append({"org_id": _oid("cia", "|".join(k for k, _ in chunk)), "org_name": f"CIA parties batch {i // per_prompt + 1}",
                    "set": "cia", "year": "", "state": "", "parties": [k for k, _ in chunk],
                    "prompt": CIA_PROMPT.format(parties=parties)})
    return out


def exclusion_name_prompts(residue: pd.DataFrame, per_prompt: int = 10) -> list[dict]:
    """One prompt per ``per_prompt`` ambiguous name-only exclusion rows (label_expansion names)."""
    if residue is None or not len(residue):
        return []
    rows = residue.to_dict("records")
    out = []
    for i in range(0, len(rows), max(1, int(per_prompt))):
        chunk = rows[i:i + per_prompt]
        txt = " | ".join(f"[{r.get('source_node_id', '')}] {r.get('entity_name', '')}, {r.get('state', '')}, excluded "
                         f"{r.get('excl_date', '')} under {r.get('excl_type', '')}; candidates {r.get('candidate_npis', '')}"
                         for r in chunk)
        out.append({"org_id": _oid("excl_names", "|".join(str(r.get("source_node_id", "")) for r in chunk)),
                    "org_name": f"Excluded names batch {i // per_prompt + 1}", "set": "exclusion_names", "year": "",
                    "state": "", "prompt": EXCL_NAME_PROMPT.format(rows=txt)})
    return out


def write_prompts(prompts: list[dict], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pj = out_dir / "case_harvest_prompts.jsonl"
    with open(pj, "w", encoding="utf-8") as fh:
        for p in prompts:
            fh.write(json.dumps(p) + "\n")
    pc = out_dir / "case_harvest_batch.csv"
    pd.DataFrame(prompts).reindex(columns=["set", "year", "state", "org_id", "org_name"]).to_csv(pc, index=False)
    return pj, pc


def save_results(results: dict, out_dir: Path) -> int:
    """One file per completed task under results/ for human review; returns the count."""
    rd = out_dir / "results"
    rd.mkdir(parents=True, exist_ok=True)
    n = 0
    for oid, env in results.items():
        if env.get("status") != "completed":
            continue
        safe = oid.replace(":", "_")
        (rd / f"{safe}.md").write_text(str(env.get("output") or ""), encoding="utf-8")
        (rd / f"{safe}.attachments.json").write_text(json.dumps(env.get("attachments") or [], indent=1), encoding="utf-8")
        n += 1
    return n


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--sets", default="mfcu,takedown,yir,hcfac,oig_sar",
                    help="also: cia (needs --cia [--case-db] [--cia-residue]) and exclusion_names (needs --exclusion-residue)")
    ap.add_argument("--cia", default=None, help="preclean/cia/cia_list.csv")
    ap.add_argument("--case-db", default=None, help="enforcement/doj_cases_v3.csv (parties already harvested are skipped)")
    ap.add_argument("--cia-residue", default=None, help="processed/cia_residue.csv from label_expansion cia")
    ap.add_argument("--exclusion-residue", default=None, help="processed/exclusion_name_residue.csv from label_expansion names")
    ap.add_argument("--per-prompt", type=int, default=None, help="parties / rows per prompt (cia 5, exclusion_names 10)")
    ap.add_argument("--years", default="2019-2025", help="YYYY-YYYY")
    ap.add_argument("--states", default=",".join(SE_STATES))
    ap.add_argument("--dispatch", action="store_true")
    ap.add_argument("--max-new", type=int, default=None)
    ap.add_argument("--status", action="store_true", help="show the registry: tasks with ids, without, completed")
    ap.add_argument("--poll", action="store_true")
    a = ap.parse_args()
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prompts = out_dir / "case_harvest_prompts.jsonl"
    registry = out_dir / "case_harvest_tasks.json"
    results_p = out_dir / "case_harvest_results.json"
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
        info = dispatch_research(prompts, registry, max_new=a.max_new)
        print(f"case harvest dispatch: {info}")
        return
    if a.poll:
        from src.model_a.pursuit_dossier import poll_research
        results = poll_research(registry, results_p)
        n = save_results(results, out_dir)
        print(f"case harvest poll: {n}/{len(results)} completed -> {out_dir / 'results'} (review each CSV, then load an "
              "accepted one with: python -m src.enforcement.research_evidence mfcu-cases --in <csv> --state <ST> --case-db <doj_cases_v3.csv>)")
        return
    y0, y1 = (int(x) for x in a.years.split("-"))
    sets = [s.strip() for s in a.sets.split(",") if s.strip()]
    ps = build_prompts([s for s in sets if s not in ("cia", "exclusion_names")], list(range(y0, y1 + 1)),
                       [s.strip() for s in a.states.split(",") if s.strip()])
    if "cia" in sets:
        if not a.cia:
            ap.error("--sets cia needs --cia <cia_list.csv>")
        from src.enforcement.cia import normalize_cia
        from src.attempt_2.clean_data import read_csv_text
        cia = normalize_cia(read_csv_text(a.cia))
        cdb = (pd.read_csv(a.case_db, dtype=str) if str(a.case_db).endswith(".csv") else pd.read_parquet(a.case_db)) if a.case_db else None
        rsd = pd.read_csv(a.cia_residue, dtype=str) if a.cia_residue else None
        ps += cia_prompts(cia, cdb, rsd, per_prompt=a.per_prompt or 5)
    if "exclusion_names" in sets:
        if not a.exclusion_residue:
            ap.error("--sets exclusion_names needs --exclusion-residue <exclusion_name_residue.csv>")
        ps += exclusion_name_prompts(pd.read_csv(a.exclusion_residue, dtype=str), per_prompt=a.per_prompt or 10)
    pj, pc = write_prompts(ps, out_dir)
    by = pd.DataFrame(ps).groupby("set").size().to_dict()
    print(f"case harvest prompts: {len(ps)} (" + ", ".join(f"{k} {v}" for k, v in by.items()) + f") -> {pj}\n  review: {pc}")


if __name__ == "__main__":
    main()
