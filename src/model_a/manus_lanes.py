"""
manus_lanes.py — every research lane, end to end, unattended (2026-10-14).

The operator's standing finding (docs/MODEL_QUESTIONS_2026-10-14.md §6): every Manus
lane was written and none dispatched, because each lane was three pasted commands
and a human review between them. This module runs the whole cycle for all lanes
from one command, with the MACHINE gates the loaders already enforce standing in
for the pre-load review, and a report that says exactly what was loaded, what was
quarantined and why, so the human review happens on the report instead of in
front of the keyboard.

Lanes (each has its own folder under <data_root>/processed/manus_lanes/<lane>/ with
prompts jsonl, task registry, results json and results/ for the raw answers):

  cia                 CIA parties missing from the case DB + the ownership resolver's
                      residue -> CSV A (the settlement) merged into the case DB as
                      civil_fca / criminal_hcf rows with a resolved outcome only;
                      CSV B (enrolled entities with NPIs) -> cia_defendant_npis rows
                      (Luhn-valid NPI only)
  names               the ambiguous name-only exclusion rows -> exclusions_name_resolved
                      rows (valid NPI, confidence >= medium, NPI among OUR candidates)
  outcome_refresh     the charged-only case rows -> outcome case rows (plea / verdict /
                      settlement), dismissals clear the charge (usable=0)
  commercial          state-year sweeps of commercial-plan fraud convictions ->
                      case rows typed hcf_commercial (training-only source)
  defendant_research  the resolver's residue -> NPIs at high / medium confidence
  label_verification  the strict label's stratified sample -> per-row verdicts
                      (evidence; LABEL_VERIFICATION.md tallies them)
  label_review        the learning loop's suspect / candidate queue -> identity and
                      public-record findings (evidence only, never a label change)

  python -m src.model_a.manus_lanes --data-root D [--run-dir R] --lanes cia,names
         [--build] [--dispatch --max-new N] [--poll] [--load] [--report]
         [--wait-minutes M --poll-every S]      # the unattended loop

Nothing here fabricates an identifier: a row reaches a label file only through the
same loader a human would have called, under the same rules. The raw answers stay
on disk for the audit. One dispatcher per registry (the lock the dossier uses).
"""

from __future__ import annotations

import io
import json
import os
import re
import time
from pathlib import Path

import pandas as pd

LANES = ("cia", "names", "outcome_refresh", "commercial", "defendant_research",
         "label_verification", "label_review")
DEFAULT_STATES = ["FL", "GA", "AL", "MS", "TN", "NC", "SC", "LA", "KY",
                  "CA", "NY", "TX", "PA", "IL", "OH", "MI"]
DEFAULT_YEARS = list(range(2019, 2026))
_OUTCOME_OK = re.compile(r"convict|plea|guilty|sentenc|settle|judgment|exclusion|civil\s+monetary", re.I)
_CHARGE_ONLY = re.compile(r"\b(?:charged|indict|arrest|pending|complaint)\b", re.I)


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #

def lane_dir(data_root: Path, lane: str) -> Path:
    d = Path(data_root) / "processed" / "manus_lanes" / lane
    d.mkdir(parents=True, exist_ok=True)
    return d


def lane_files(data_root: Path, lane: str) -> dict:
    d = lane_dir(data_root, lane)
    return {"dir": d, "prompts": d / f"{lane}_prompts.jsonl", "registry": d / f"{lane}_tasks.json",
            "results": d / f"{lane}_results.json", "raw": d / "results", "loaded": d / f"{lane}_loaded.json"}


def inputs(data_root: Path, run_dir: Path | None = None) -> dict:
    r = Path(data_root)
    return {
        "case_db": r / "enforcement" / "doj_cases_v3.csv",
        "cia_list": r / "preclean" / "cia" / "cia_list.csv",
        "cia_cases": r / "enforcement" / "cia_cases.csv",
        "cia_residue": r / "processed" / "cia_residue.csv",
        "cia_npis": r / "processed" / "cia_defendant_npis.parquet",
        "name_residue": r / "processed" / "exclusion_name_residue.csv",
        "name_resolved": r / "processed" / "exclusions_name_resolved.parquet",
        "defendant_npis": r / "processed" / "case_defendant_npis.parquet",
        "verification_sample": next((p for p in (r / "processed" / "fca_label_strong" / "verification_sample_strata.csv",
                                                 r / "processed" / "fca_label_strong" / "verification_sample.csv",
                                                 r / "processed" / "fca_label" / "verification_sample_strata.csv")
                                     if p.exists()), r / "processed" / "fca_label_strong" / "verification_sample_strata.csv"),
        "current_labels": r / "processed" / "fca_label_strong" / "labels_fca_strict.csv",
        "label_review_prompts": (Path(run_dir) / "label_review_prompts.jsonl") if run_dir else None,
    }


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _write_jsonl(items: list[dict], p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, default=str) + "\n")
    return p


def _read_table(p: Path) -> pd.DataFrame | None:
    if not p or not Path(p).exists():
        return None
    if str(p).endswith(".parquet"):
        return pd.read_parquet(p)
    from src.attempt_2.clean_data import read_csv_text
    return read_csv_text(str(p), dtype=str)


# --------------------------------------------------------------------------- #
# answers -> tables
# --------------------------------------------------------------------------- #

def _csv_text_to_df(text: str) -> pd.DataFrame | None:
    s = str(text or "").strip()
    if not s or "\n" not in s:
        return None
    try:
        df = pd.read_csv(io.StringIO(s), dtype=str, keep_default_na=False, engine="python",
                         on_bad_lines="skip")
    except Exception:  # noqa: BLE001
        return None
    if df.shape[1] < 2 or not len(df):
        return None
    df.columns = [str(c).strip().strip("|").strip() for c in df.columns]
    return df


def _pipe_table_to_df(block: str) -> pd.DataFrame | None:
    lines = [l.strip() for l in block.splitlines() if l.strip().startswith("|")]
    if len(lines) < 2:
        return None
    rows = []
    for l in lines:
        cells = [c.strip() for c in l.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c or "") for c in cells):
            continue                               # the header rule
        rows.append(cells)
    if len(rows) < 2:
        return None
    hdr = rows[0]
    body = [r + [""] * (len(hdr) - len(r)) if len(r) < len(hdr) else r[:len(hdr)] for r in rows[1:]]
    return pd.DataFrame(body, columns=hdr)


def tables_from_answer(output, attachments=None, fetch=None) -> list[pd.DataFrame]:
    """Every table an answer carries: CSV attachments (fetched through ``fetch(url)``,
    default requests), fenced ```csv blocks, markdown pipe tables, or a bare CSV body.
    Tables are returned in that order; the loaders pick by column names."""
    out: list[pd.DataFrame] = []
    for a in attachments or []:
        if not isinstance(a, dict):
            continue
        name = str(a.get("filename") or "").lower()
        ct = str(a.get("content_type") or "").lower()
        url = str(a.get("url") or "")
        if not url or not (name.endswith(".csv") or "csv" in ct or name.endswith(".txt")):
            continue
        try:
            if fetch is None:
                import requests
                resp = requests.get(url, timeout=120)
                resp.raise_for_status()
                text = resp.text
            else:
                text = fetch(url)
        except Exception:  # noqa: BLE001
            continue
        df = _csv_text_to_df(text)
        if df is not None:
            out.append(df)
    text = output if isinstance(output, str) else json.dumps(output, default=str)
    for m in re.finditer(r"```(?:csv|text|plain)?\s*\n(.*?)```", text, re.S | re.I):
        df = _csv_text_to_df(m.group(1))
        if df is not None:
            out.append(df)
    for m in re.finditer(r"((?:^\|.*\|\s*$\n?){2,})", text, re.M):
        df = _pipe_table_to_df(m.group(1))
        if df is not None:
            out.append(df)
    if not out:
        df = _csv_text_to_df(text)
        if df is not None and df.shape[1] >= 4:
            out.append(df)
    return out


def _has(df: pd.DataFrame, *cols) -> bool:
    low = {str(c).strip().lower().replace(" ", "_") for c in df.columns}
    return all(any(c == x or c in x for x in low) for c in cols)


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #

def build_lane(lane: str, data_root: Path, run_dir: Path | None = None, states=None, years=None,
               per_prompt: int | None = None, log=print) -> list[dict]:
    from src.enforcement import case_harvest_prompts as chp
    inp = inputs(data_root, run_dir)
    files = lane_files(data_root, lane)
    prompts: list[dict] = []
    if lane == "cia":
        cia = _read_table(inp["cia_list"])
        if cia is None:
            log(f"[manus_lanes:cia] no {inp['cia_list']}: nothing to build")
        else:
            from src.enforcement.cia import normalize_cia
            prompts = chp.cia_prompts(normalize_cia(cia), _read_table(inp["case_db"]),
                                      _read_table(inp["cia_residue"]), per_prompt=per_prompt or 5)
    elif lane == "names":
        res = _read_table(inp["name_residue"])
        if res is None:
            log(f"[manus_lanes:names] no {inp['name_residue']} (label_expansion names not run)")
        else:
            prompts = chp.exclusion_name_prompts(res, per_prompt=per_prompt or 10)
    elif lane == "outcome_refresh":
        cdb = _read_table(inp["case_db"])
        if cdb is None:
            log(f"[manus_lanes:outcome_refresh] no {inp['case_db']}")
        else:
            prompts = chp.outcome_refresh_prompts(cdb, per_prompt=per_prompt or 10)
    elif lane == "commercial":
        prompts = chp.build_prompts(["commercial"], years or DEFAULT_YEARS, states or DEFAULT_STATES)
    elif lane == "defendant_research":
        from src.enforcement import defendant_research as dr
        res = _read_table(inp["defendant_npis"])
        if res is None:
            log(f"[manus_lanes:defendant_research] no {inp['defendant_npis']}")
        else:
            batch = dr.select_batch(res, (1, 2), 2_000_000.0, states or DEFAULT_STATES, None)
            dr.write_prompts(batch, files["dir"])
            prompts = _read_jsonl(files["dir"] / "defendant_research_prompts.jsonl")
    elif lane == "label_verification":
        from src.model_a import label_verification as lv
        sp = inp["verification_sample"]
        if not sp.exists():
            log(f"[manus_lanes:label_verification] no {sp} (fca_label_export not run)")
        else:
            sample = pd.read_csv(sp, dtype=str)
            sample.to_csv(files["dir"] / "verification_sample.csv", index=False)
            prompts = lv.build_prompts(sample, per_prompt or 10, sp.name)
    elif lane == "label_review":
        src = inp["label_review_prompts"]
        if not src or not src.exists():
            log("[manus_lanes:label_review] no label_review_prompts.jsonl in the run folder (learning loop not run)")
        else:
            prompts = _read_jsonl(src)
    else:
        raise ValueError(f"unknown lane {lane!r} (one of {LANES})")
    _write_jsonl(prompts, files["prompts"])
    log(f"[manus_lanes:{lane}] {len(prompts)} prompt(s) -> {files['prompts']}")
    return prompts


# --------------------------------------------------------------------------- #
# dispatch / poll
# --------------------------------------------------------------------------- #

def dispatch_lane(lane: str, data_root: Path, max_new: int | None = None, transport=None, log=print) -> dict:
    from src.model_a.pursuit_dossier import dispatch_research
    files = lane_files(data_root, lane)
    if not files["prompts"].exists() or not _read_jsonl(files["prompts"]):
        return {"dispatched": 0, "note": "no prompts (build first, or the lane has nothing to ask)"}
    info = dispatch_research(files["prompts"], files["registry"], transport=transport, max_new=max_new)
    log(f"[manus_lanes:{lane}] dispatch: {info}")
    return info


def poll_lane(lane: str, data_root: Path, transport=None, log=print) -> dict:
    from src.model_a.pursuit_dossier import poll_research
    from src.enforcement.case_harvest_prompts import save_results
    files = lane_files(data_root, lane)
    if not files["registry"].exists():
        return {}
    results = poll_research(files["registry"], files["results"], transport=transport)
    n = save_results(results, files["dir"])
    done = sum(1 for v in results.values() if v.get("status") == "completed")
    log(f"[manus_lanes:{lane}] poll: {done}/{len(results)} completed ({n} raw answers saved)")
    return results


# --------------------------------------------------------------------------- #
# load (the machine gates)
# --------------------------------------------------------------------------- #

def _harvest_cases_from_table(df: pd.DataFrame, default_type: str = "") -> tuple[pd.DataFrame, pd.DataFrame]:
    """CSV_SCHEMA rows (the harvest sets' shared schema) -> case-DB rows with a resolved
    outcome; charged-only rows quarantined. case_type from the outcome column
    (civil settlement -> civil_fca; conviction / plea -> criminal_hcf)."""
    from src.attempt_2.clean_data import _resolve_columns
    from src.enforcement.case_db import build_case_db, _classify, _classify_all, _SECTOR_KEYWORDS, _SCHEME_KEYWORDS
    aliases = {"defendant_name": ["defendant_name", "defendant", "name"], "announced_date": ["announced_date", "date"],
               "state": ["state", "st"], "city": ["city"], "npi": ["npi"], "scheme": ["scheme"],
               "amount_usd": ["amount_usd", "amount"], "outcome": ["outcome", "disposition"],
               "source_url": ["source_url", "url", "source"], "qui_tam": ["qui_tam"],
               "conduct_summary": ["conduct_summary", "summary", "conduct"],
               "docket": ["docket_or_case_number", "docket"]}
    resolved = _resolve_columns(list(df.columns), aliases)
    d = df.rename(columns={v: k for k, v in resolved.items()}).copy()
    rows, bad = [], []
    for r in d.to_dict("records"):
        name = str(r.get("defendant_name") or "").strip()
        outcome = str(r.get("outcome") or "").strip()
        if not name:
            continue
        if not _OUTCOME_OK.search(outcome) or (_CHARGE_ONLY.search(outcome) and not _OUTCOME_OK.search(outcome)):
            bad.append({**r, "drop_reason": f"no resolved outcome ({outcome or 'blank'})"})
            continue
        ct = default_type or ("criminal_hcf" if re.search(r"convict|plea|guilty|sentenc", outcome, re.I) else "civil_fca")
        summary = (f"{outcome}. " + str(r.get("conduct_summary") or r.get("scheme") or "")).strip()
        url = str(r.get("source_url") or "").strip()
        st = str(r.get("state") or "").strip().upper()
        npi = re.sub(r"\D", "", str(r.get("npi") or ""))
        qt = str(r.get("qui_tam") or "").strip().lower()
        rows.append({"case_id": url or f"harvest:{st.lower()}:{abs(hash((name, str(r.get('announced_date') or '')))) & 0xFFFFFFFF:x}",
                     "announced_date": str(r.get("announced_date") or "").strip(), "defendant_name": name,
                     "sector": _classify(f"{name}. {summary}", _SECTOR_KEYWORDS),
                     "scheme": _classify_all(f"{name}. {summary}", _SCHEME_KEYWORDS),
                     "amount_usd": pd.to_numeric(str(r.get("amount_usd") or "").replace("$", "").replace(",", ""), errors="coerce"),
                     "qui_tam": 1 if qt in ("yes", "y", "true", "1") else (0 if qt in ("no", "n", "false", "0") else pd.NA),
                     "intervened": pd.NA, "jurisdiction": st, "source_url": url, "summary": summary[:500],
                     "case_type": ct, "npi_in_source": npi if len(npi) == 10 else ""})
    cases = build_case_db(rows) if rows else pd.DataFrame()
    if len(cases):
        cases["case_type"] = [r["case_type"] for r in rows]
        if "npi_in_source" not in cases.columns:
            cases["npi_in_source"] = [r["npi_in_source"] for r in rows]
    return cases, pd.DataFrame(bad)


def _append_parquet(path: Path, rows: pd.DataFrame, keys: list[str]) -> int:
    if rows is None or not len(rows):
        return 0
    prev = pd.read_parquet(path) if path.exists() else None
    allrows = pd.concat([prev, rows], ignore_index=True) if prev is not None and len(prev) else rows
    before = len(prev) if prev is not None else 0
    allrows = allrows.drop_duplicates(keys, keep="first")
    path.parent.mkdir(parents=True, exist_ok=True)
    allrows.to_parquet(path, index=False)
    return int(len(allrows) - before)


def load_lane(lane: str, data_root: Path, run_dir: Path | None = None, fetch=None, log=print) -> dict:
    """Apply every completed answer through the lane's loader (idempotent: the files
    are de-duplicated on their keys). Returns the tally for the report."""
    from src.enforcement import research_evidence as rev
    files = lane_files(data_root, lane)
    inp = inputs(data_root, run_dir)
    results = json.loads(files["results"].read_text(encoding="utf-8")) if files["results"].exists() else {}
    items = {it["org_id"]: it for it in _read_jsonl(files["prompts"])}
    done = {k: v for k, v in results.items() if v.get("status") == "completed"}
    tally = {"lane": lane, "answers": len(done), "loaded": 0, "quarantined": 0, "no_table": 0, "files": [], "notes": []}
    if not done:
        files["loaded"].write_text(json.dumps(tally, indent=1), encoding="utf-8")
        return tally
    quarantine_rows: list[pd.DataFrame] = []

    if lane in ("cia", "commercial"):
        case_rows, ent_rows = [], []
        for oid, env in done.items():
            tabs = tables_from_answer(env.get("output"), env.get("attachments"), fetch)
            if not tabs:
                tally["no_table"] += 1
                continue
            for t in tabs:
                if _has(t, "enrolled_entity") or (_has(t, "party") and _has(t, "npi") and not _has(t, "outcome")):
                    ent_rows.append(t)
                elif _has(t, "defendant"):
                    if lane == "commercial":
                        st = str(items.get(oid, {}).get("state") or "")
                        if "state" not in {c.lower() for c in t.columns}:
                            t = t.assign(state=st)
                        for s, g in t.groupby(t[[c for c in t.columns if c.lower() == "state"][0]].str.upper()):
                            cases, q = rev.load_commercial_cases(g, s or st or "XX")
                            case_rows.append(cases)
                            if len(q):
                                quarantine_rows.append(q)
                                tally["quarantined"] += int(len(q))
                    else:
                        cases, q = _harvest_cases_from_table(t)
                        case_rows.append(cases)
                        if len(q):
                            quarantine_rows.append(q)
                            tally["quarantined"] += int(len(q))
        new_cases = pd.concat([c for c in case_rows if c is not None and len(c)], ignore_index=True) if any(
            c is not None and len(c) for c in case_rows) else pd.DataFrame()
        if len(new_cases):
            cdb0 = _read_table(inp["case_db"])
            before = len(cdb0) if cdb0 is not None else 0
            merged = rev.merge_into_case_db(new_cases, inp["case_db"])
            tally["loaded"] += int(len(merged) - before)
            tally["files"].append(str(inp["case_db"]))
        if lane == "cia" and ent_rows:
            case_ids = None
            cc = _read_table(inp["cia_cases"])
            if cc is not None and {"defendant_name_key", "case_id"} <= set(cc.columns):
                case_ids = dict(zip(cc["defendant_name_key"].astype(str).str.upper(), cc["case_id"].astype(str)))
            ents = pd.concat([rev.load_cia_entities(t, case_ids) for t in ent_rows], ignore_index=True)
            ok = ents[ents["status"] == "resolved"]
            tally["quarantined"] += int((ents["status"] != "resolved").sum())
            n = _append_parquet(inp["cia_npis"], ok, ["case_id", "npi"])
            tally["loaded"] += n
            tally["files"].append(str(inp["cia_npis"]))
            if (ents["status"] != "resolved").any():
                quarantine_rows.append(ents[ents["status"] != "resolved"].assign(drop_reason="no Luhn-valid NPI"))
    elif lane == "names":
        residue = _read_table(inp["name_residue"])
        rows = []
        for oid, env in done.items():
            tabs = [t for t in tables_from_answer(env.get("output"), env.get("attachments"), fetch) if _has(t, "npi")]
            if not tabs:
                tally["no_table"] += 1
                continue
            for t in tabs:
                got = rev.load_exclusion_npis(t, residue, min_confidence="medium") if residue is not None else pd.DataFrame()
                rows.append(got)
                tally["quarantined"] += max(0, len(t) - len(got))
        if rows:
            new = pd.concat(rows, ignore_index=True)
            n = _append_parquet(inp["name_resolved"], new, ["npi", "excl_type", "excl_date"])
            tally["loaded"] += n
            tally["files"].append(str(inp["name_resolved"]))
            tally["notes"].append("rebuild the graph so the resolved names become exclusion nodes")
    elif lane == "outcome_refresh":
        cdb = _read_table(inp["case_db"])
        n_clear = pending = 0
        news = []
        if cdb is None:
            done = {}
            tally["notes"].append(f"no case DB at {inp['case_db']}")
        for oid, env in done.items():
            tabs = [t for t in tables_from_answer(env.get("output"), env.get("attachments"), fetch) if _has(t, "case_id")]
            if not tabs:
                tally["no_table"] += 1
                continue
            for t in tabs:
                new, cleared, pend = rev.load_outcome_refresh(t, cdb)
                pending += pend
                if len(new):
                    news.append(new)
                if len(cleared):
                    n_clear += rev.clear_charged_rows(inp["case_db"], cleared)
        if news:
            allnew = pd.concat(news, ignore_index=True)
            before = len(cdb) if cdb is not None else 0
            merged = rev.merge_into_case_db(allnew, inp["case_db"])
            tally["loaded"] += int(len(merged) - before)
            tally["files"].append(str(inp["case_db"]))
        tally["notes"].append(f"{n_clear} charged rows cleared (dismissal / acquittal); {pending} still pending")
    elif lane == "defendant_research":
        from src.enforcement import defendant_research as dr
        res = _read_table(inp["defendant_npis"])
        if res is not None:
            table, summ = dr.fold_results(res, files["dir"] / "defendant_research_prompts.jsonl", results)
            table.to_parquet(inp["defendant_npis"], index=False)
            tally["loaded"] += int(summ.get("folded_resolved", 0))
            tally["quarantined"] += int(summ.get("low_confidence", 0) + summ.get("no_npi", 0))
            tally["files"].append(str(inp["defendant_npis"]))
            tally["notes"].append(json.dumps(summ))
    elif lane == "label_verification":
        from src.model_a import label_verification as lv
        sp = files["dir"] / "verification_sample.csv"
        if sp.exists():
            sample = pd.read_csv(sp, dtype=str)
            rows = lv.fold_results(sample, results)
            if inp["current_labels"].exists():
                rows = lv.rescore_against(rows, pd.read_csv(inp["current_labels"], dtype=str))
            rows.to_csv(files["dir"] / "verification_results.csv", index=False)
            (files["dir"] / "LABEL_VERIFICATION.md").write_text(lv.report(rows), encoding="utf-8")
            tally["loaded"] = int((rows["verdict"] != "pending").sum())
            tally["files"].append(str(files["dir"] / "LABEL_VERIFICATION.md"))
            vc = rows["verdict"].value_counts().to_dict()
            tally["notes"].append("verdicts: " + ", ".join(f"{k} {v}" for k, v in vc.items()))
    elif lane == "label_review":
        from src.feeds.manus_research import _parse_json_text, fetch_json_attachment
        out_rows = []
        for oid, env in done.items():
            it = items.get(oid, {})
            obj = _parse_json_text(env.get("output") if isinstance(env.get("output"), str) else json.dumps(env.get("output") or ""))
            if obj is None and fetch is None:
                obj = fetch_json_attachment(env.get("attachments"))
            out_rows.append({"org_id": oid, "npi": it.get("npi", ""), "kind": it.get("kind", ""),
                             "identity_match": str((obj or {}).get("identity_match") or ""),
                             "n_records": len((obj or {}).get("records") or []) if isinstance((obj or {}).get("records"), list) else 0,
                             "summary": str((obj or {}).get("summary") or (env.get("output") if isinstance(env.get("output"), str) else ""))[:400]})
        if out_rows:
            pd.DataFrame(out_rows).to_csv(files["dir"] / "label_review_results.csv", index=False)
            tally["loaded"] = len(out_rows)
            tally["files"].append(str(files["dir"] / "label_review_results.csv"))
            tally["notes"].append("evidence only: a verdict never changes a label by itself")
    if quarantine_rows:
        q = pd.concat(quarantine_rows, ignore_index=True)
        q.to_csv(files["dir"] / f"{lane}_quarantine.csv", index=False)
        tally["files"].append(str(files["dir"] / f"{lane}_quarantine.csv"))
    files["loaded"].write_text(json.dumps(tally, indent=1, default=str), encoding="utf-8")
    log(f"[manus_lanes:{lane}] load: {tally['loaded']} rows loaded, {tally['quarantined']} quarantined, "
        f"{tally['no_table']} answers without a table")
    return tally


# --------------------------------------------------------------------------- #
# status + report
# --------------------------------------------------------------------------- #

def lane_status(lane: str, data_root: Path) -> dict:
    files = lane_files(data_root, lane)
    prompts = _read_jsonl(files["prompts"])
    reg = json.loads(files["registry"].read_text(encoding="utf-8")) if files["registry"].exists() else {}
    res = json.loads(files["results"].read_text(encoding="utf-8")) if files["results"].exists() else {}
    loaded = json.loads(files["loaded"].read_text(encoding="utf-8")) if files["loaded"].exists() else {}
    return {"lane": lane, "prompts": len(prompts), "dispatched": sum(1 for v in reg.values() if v.get("task_id")),
            "completed": sum(1 for v in res.values() if v.get("status") == "completed"),
            "errors": sum(1 for v in res.values() if v.get("status") in ("error", "failed", "waiting")),
            "loaded": loaded.get("loaded", 0), "quarantined": loaded.get("quarantined", 0),
            "no_table": loaded.get("no_table", 0), "notes": loaded.get("notes", []), "files": loaded.get("files", [])}


def write_report(data_root: Path, lanes, out: Path | None = None) -> Path:
    rows = [lane_status(l, data_root) for l in lanes]
    L = ["# MANUS_LANES — every research lane, where it stands", "",
         f"_as of {time.strftime('%Y-%m-%d %H:%M')}. prompts = tasks written; dispatched = tasks with an id at the "
         "research service; completed = answers on file; loaded = rows that passed the lane's machine gate into "
         "the label inputs (the same loader a human review would call: Luhn-valid NPI, confidence >= medium, "
         "the NPI among OUR candidates, a resolved outcome); quarantined = rows the gate refused, kept in "
         "<lane>_quarantine.csv for the human read. The raw answers are under processed/manus_lanes/<lane>/results/._",
         "", "| lane | prompts | dispatched | completed | errors | loaded | quarantined | no table | notes |",
         "|---|--:|--:|--:|--:|--:|--:|--:|---|"]
    for r in rows:
        L.append(f"| {r['lane']} | {r['prompts']} | {r['dispatched']} | {r['completed']} | {r['errors']} | "
                 f"{r['loaded']} | {r['quarantined']} | {r['no_table']} | {'; '.join(r['notes'])[:200]} |")
    L += ["", "## What happens next", "",
          "- cia / outcome_refresh / commercial rows went into enforcement/doj_cases_v3.csv (case_type set per row); "
          "`label_expansion merge-inputs` (runner step 0) folds them into the next run's label.",
          "- names rows went into processed/exclusions_name_resolved.parquet: the next graph build makes them exclusion nodes.",
          "- cia entities went into processed/cia_defendant_npis.parquet (status resolved, match_level research_cia).",
          "- defendant_research answers were folded into processed/case_defendant_npis.parquet (research_high / medium).",
          "- label_verification and label_review are EVIDENCE: read LABEL_VERIFICATION.md and label_review_results.csv; "
          "a tighten-or-ship verdict is the operator's call.",
          "- Every quarantine file is the human review: a row there was refused by a rule, never by a guess."]
    out = Path(out) if out else Path(data_root) / "processed" / "manus_lanes" / "MANUS_LANES.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    out.with_name("manus_lanes.json").write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
# the unattended cycle
# --------------------------------------------------------------------------- #

def run_cycle(data_root: Path, lanes, run_dir: Path | None, build: bool, dispatch: bool, poll: bool, load: bool,
              max_new: int | None, total_budget: int | None, wait_minutes: float, poll_every: float,
              transport=None, fetch=None, sleep=time.sleep, log=print, states=None, years=None) -> dict:
    spent = 0
    for lane in lanes:
        if build:
            build_lane(lane, data_root, run_dir, states=states, years=years, log=log)
        if dispatch:
            cap = max_new
            if total_budget is not None:
                cap = max(0, min(cap if cap is not None else total_budget, total_budget - spent))
            if cap == 0:
                log(f"[manus_lanes:{lane}] dispatch skipped: total budget {total_budget} spent")
            else:
                info = dispatch_lane(lane, data_root, max_new=cap, transport=transport, log=log)
                spent += int(info.get("dispatched", 0))
    if poll:
        deadline = time.time() + wait_minutes * 60.0
        while True:
            pending = 0
            for lane in lanes:
                res = poll_lane(lane, data_root, transport=transport, log=log)
                pending += sum(1 for v in res.values() if v.get("status") not in ("completed", "error", "failed", "waiting"))
            if pending == 0 or time.time() >= deadline:
                break
            log(f"[manus_lanes] {pending} task(s) still running; next poll in {poll_every:.0f}s")
            sleep(poll_every)
    tallies = {}
    if load:
        for lane in lanes:
            tallies[lane] = load_lane(lane, data_root, run_dir, fetch=fetch, log=log)
    rep = write_report(data_root, lanes)
    log(f"[manus_lanes] report -> {rep}")
    return {"dispatched": spent, "tallies": tallies, "report": str(rep)}


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", default=os.environ.get("MEDICAID_DATA_ROOT"), required=False)
    ap.add_argument("--run-dir", default=None, help="the latest run folder (label_review prompts)")
    ap.add_argument("--lanes", default=",".join(LANES))
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--dispatch", action="store_true")
    ap.add_argument("--max-new", type=int, default=None, help="tasks per lane per call")
    ap.add_argument("--total-budget", type=int, default=None, help="tasks across all lanes this call")
    ap.add_argument("--poll", action="store_true")
    ap.add_argument("--wait-minutes", type=float, default=0.0, help="with --poll: keep polling this long")
    ap.add_argument("--poll-every", type=float, default=600.0, help="seconds between polls")
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--states", default=None)
    ap.add_argument("--years", default=None, help="YYYY-YYYY")
    a = ap.parse_args()
    if not a.data_root:
        ap.error("--data-root (or MEDICAID_DATA_ROOT)")
    lanes = [l.strip() for l in a.lanes.split(",") if l.strip()]
    bad = [l for l in lanes if l not in LANES]
    if bad:
        ap.error(f"unknown lane(s) {bad}; choose from {LANES}")
    states = [s.strip().upper() for s in a.states.split(",")] if a.states else None
    years = None
    if a.years:
        y0, y1 = (int(x) for x in a.years.split("-"))
        years = list(range(y0, y1 + 1))
    if not any((a.build, a.dispatch, a.poll, a.load)):
        a.report = True
    if a.report and not any((a.build, a.dispatch, a.poll, a.load)):
        print(write_report(Path(a.data_root), lanes))
        for l in lanes:
            print(json.dumps(lane_status(l, Path(a.data_root)), default=str))
        return
    run_cycle(Path(a.data_root), lanes, Path(a.run_dir) if a.run_dir else None, a.build, a.dispatch, a.poll, a.load,
              a.max_new, a.total_budget, a.wait_minutes, a.poll_every, states=states, years=years)


if __name__ == "__main__":
    main()
