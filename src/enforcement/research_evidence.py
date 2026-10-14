"""
research_evidence.py — fold REVIEWED research results into the label schemas.

The research layer (manus_research, hand-run browser sweeps) produces evidence
files: state Medicaid exclusion/termination lists and state MFCU (Medicaid
Fraud Control Unit) settlement sweeps. Until now those were PDFs and CSVs a
human read — none of it reached the model. This is the bridge:

  * ``load_state_exclusions``  — a reviewed state termination/exclusion table →
    the shared exclusions schema, written as
    ``processed/exclusions_state_<st>.parquet``. The graph's generalized
    exclusion loader merges every ``exclusions_*.parquet`` automatically, so
    the rows become exclusion nodes and widen the PU label with a
    ``state_term_<st>`` source tag on the next build. This attacks the label's
    biggest documented gap: state-terminated providers who never reach LEIE.
  * ``load_mfcu_cases`` — a reviewed state-settlement table → the canonical
    case DB schema (multi-label scheme tags via the case classifier), merged
    into the case CSV dedup-by-case_id. This thickens the DOJ label with the
    50-state enforcement stream the federal feed never carries.

HUMAN GATE, BY CONSTRUCTION: both loaders read a FILE the operator saved after
reviewing the research output. Nothing flows from a live agent response into a
label without a person having looked at it. Column resolution is alias-based
(state lists come in forty formats); rows that resolve to neither a Luhn-valid
NPI nor a usable name are quarantined to a side table, never silently dropped.

    python -m src.enforcement.research_evidence state-exclusions \\
        --in ri_terminations.csv --state RI --processed ~/Desktop/data/processed
    python -m src.enforcement.research_evidence mfcu-cases \\
        --in oh_mfcu.csv --state OH --case-db ~/Desktop/data/enforcement/doj_cases.csv
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import canonicalize_series, read_csv_text, _resolve_columns
from src.enforcement.case_db import (CASE_COLUMNS, _SCHEME_KEYWORDS,
                                     _SECTOR_KEYWORDS, _classify, _classify_all,
                                     build_case_db)
from src.enforcement.opensanctions import EXCLUSION_COLS
from src.entity_graph.resolve_entities import norm_org_name

# state lists arrive in ~40 formats; resolve the essentials by alias
_STATE_EXCL_ALIASES = {
    "npi": ["npi", "NPI", "provider npi", "npi number", "national provider identifier"],
    "name": ["name", "provider name", "provider", "entity name", "legal name",
             "business name", "last name", "provider_last_name", "sanctioned entity"],
    "first_name": ["first name", "provider_first_name", "first"],
    "excl_type": ["action", "sanction", "sanction type", "exclusion type",
                  "termination type", "reason", "authority", "action type"],
    "excl_date": ["exclusion date", "effective date", "termination date",
                  "sanction date", "action date", "date", "effective"],
    "reinstate_date": ["reinstatement date", "reinstate date", "end date",
                       "expiration date"],
}

_MFCU_ALIASES = {
    "defendant_name": ["defendant", "defendant name", "provider", "provider name",
                       "entity", "entity name", "name", "subject"],
    "announced_date": ["date", "announced", "announcement date", "press date",
                       "settlement date", "conviction date", "sentencing date"],
    "amount_usd": ["amount", "settlement amount", "recovery", "restitution",
                   "amount_usd", "dollars"],
    "summary": ["summary", "description", "text", "details", "body", "conduct"],
    "source_url": ["url", "source", "source_url", "link", "press release"],
}


def _read_any(path: str | Path) -> pd.DataFrame:
    p = str(path)
    if p.endswith(".parquet"):
        return pd.read_parquet(p)
    return read_csv_text(p, dtype=str)


def load_state_exclusions(table: pd.DataFrame, state: str,
                          source: str = "state_term"
                          ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A reviewed state termination/exclusion table → (exclusions, quarantine).

    ``exclusions`` is in the shared EXCLUSION_COLS schema with
    ``excl_type = '<source>_<st>:<action>'`` so label provenance carries the
    state and the reviewed source. ``quarantine`` holds rows with neither a
    Luhn-valid NPI nor a usable name (kept, never silently dropped).
    """
    st = str(state).strip().upper()
    if table is None or not len(table):
        return pd.DataFrame(columns=EXCLUSION_COLS), pd.DataFrame()
    resolved = _resolve_columns(list(table.columns), _STATE_EXCL_ALIASES)
    d = table.rename(columns={v: k for k, v in resolved.items()}).copy()

    name = d.get("name", pd.Series("", index=d.index)).fillna("").astype(str).str.strip()
    first = d.get("first_name", pd.Series("", index=d.index)).fillna("").astype(str).str.strip()
    full = (name + ", " + first).str.strip(", ").where(first != "", name)

    npi = canonicalize_series(d.get("npi", pd.Series("", index=d.index)).fillna(""))
    action = (d.get("excl_type", pd.Series("", index=d.index)).fillna("")
              .astype(str).str.strip().str.lower().str.slice(0, 60))
    tag = f"{source}_{st.lower()}"
    out = pd.DataFrame({
        "npi": npi.fillna(""),
        "entity_name": full,
        "name_key": full.map(norm_org_name),
        "excl_type": (tag + ":" + action).where(action != "", tag),
        "excl_date": pd.to_datetime(d.get("excl_date"), errors="coerce"),
        "reinstate_date": pd.to_datetime(d.get("reinstate_date"), errors="coerce"),
    })
    out["currently_active"] = out["reinstate_date"].isna().astype(int)

    usable = (out["npi"] != "") | (out["name_key"] != "")
    quarantine = table[~usable.to_numpy()].copy()
    out = out[usable].drop_duplicates(["npi", "name_key", "excl_date"])
    return out[EXCLUSION_COLS].reset_index(drop=True), quarantine


def load_mfcu_cases(table: pd.DataFrame, state: str) -> pd.DataFrame:
    """A reviewed state MFCU settlement table → canonical case-DB rows.

    Scheme is multi-label (same classifier as the DOJ feed) from the summary
    text; ``jurisdiction`` records the state MFCU; case_id falls back to a
    stable hash of (state, defendant, date) when no URL exists.
    """
    st = str(state).strip().upper()
    if table is None or not len(table):
        return pd.DataFrame(columns=CASE_COLUMNS)
    resolved = _resolve_columns(list(table.columns), _MFCU_ALIASES)
    d = table.rename(columns={v: k for k, v in resolved.items()}).copy()

    rows = []
    for r in d.to_dict("records"):
        defendant = str(r.get("defendant_name") or "").strip()
        summary = str(r.get("summary") or "").strip()
        if not defendant and not summary:
            continue
        url = str(r.get("source_url") or "").strip()
        date = str(r.get("announced_date") or "").strip()
        cid = url or f"mfcu:{st.lower()}:{abs(hash((st, defendant, date))) & 0xFFFFFFFF:x}"
        text = f"{defendant}. {summary}"
        rows.append({
            "case_id": cid,
            "announced_date": date,
            "defendant_name": defendant,
            "sector": _classify(text, _SECTOR_KEYWORDS),
            "scheme": _classify_all(text, _SCHEME_KEYWORDS),
            "amount_usd": pd.to_numeric(
                str(r.get("amount_usd") or "").replace("$", "").replace(",", ""),
                errors="coerce"),
            "qui_tam": pd.NA,
            "intervened": pd.NA,
            "jurisdiction": f"{st} MFCU",
            "source_url": url,
            "summary": summary[:500],
        })
    if not rows:
        return pd.DataFrame(columns=CASE_COLUMNS)
    return build_case_db(rows)


_COMMERCIAL_ALIASES = dict(_MFCU_ALIASES, **{
    "outcome": ["outcome", "disposition", "result"],
    "payer_type": ["payer_type", "payer", "plan type"],
    "npi": ["npi", "npi number"],
    "outcome_date": ["outcome_date", "disposition date"],
})


def load_commercial_cases(table: pd.DataFrame, state: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A reviewed commercial-plan fraud table (the `commercial` harvest set) -> case-DB
    rows with ``case_type = hcf_commercial`` (case_labels.COMMERCIAL_CASE_TYPE): the
    TRAINING-only source for fraud against private plans. Returns (cases, quarantine):
    a row is admitted only when its outcome column or summary states a conviction,
    plea, judgment or settlement; charged-only rows are quarantined with the reason.
    Never a forward positive, never the strict label (both enforced downstream)."""
    from src.model_a.fca_label_export import _CIVIL_OUTCOME, _CRIMINAL_OUTCOME
    st = str(state).strip().upper()
    empty = pd.DataFrame(columns=CASE_COLUMNS)
    if table is None or not len(table):
        return empty, pd.DataFrame()
    resolved = _resolve_columns(list(table.columns), _COMMERCIAL_ALIASES)
    d = table.rename(columns={v: k for k, v in resolved.items()}).copy()
    rows, bad = [], []
    for r in d.to_dict("records"):
        defendant = str(r.get("defendant_name") or "").strip()
        summary = str(r.get("summary") or "").strip()
        outcome = str(r.get("outcome") or "").strip()
        if not defendant:
            continue
        text = f"{outcome}. {summary}"
        ok = bool(_CRIMINAL_OUTCOME.search(text) or _CIVIL_OUTCOME.search(text)
                  or re.search(r"\b(?:judgment|convict\w*|plea\w*|sentenc\w*|settle\w*)\b", text, re.I))
        if not ok:
            bad.append({**r, "drop_reason": "no resolved outcome (charge / arrest / pending)"})
            continue
        url = str(r.get("source_url") or "").strip()
        date = str(r.get("outcome_date") or r.get("announced_date") or "").strip()
        cid = url or f"commercial:{st.lower()}:{abs(hash((st, defendant, date))) & 0xFFFFFFFF:x}"
        npi = str(r.get("npi") or "").strip()
        rows.append({
            "case_id": cid, "announced_date": date, "defendant_name": defendant,
            "sector": _classify(f"{defendant}. {summary}", _SECTOR_KEYWORDS),
            "scheme": _classify_all(f"{defendant}. {summary}", _SCHEME_KEYWORDS),
            "amount_usd": pd.to_numeric(str(r.get("amount_usd") or "").replace("$", "").replace(",", ""),
                                        errors="coerce"),
            "qui_tam": pd.NA, "intervened": pd.NA,
            "jurisdiction": f"{st} commercial-plan ({str(r.get('payer_type') or 'commercial').strip()})",
            "source_url": url,
            "summary": (f"{outcome}. " if outcome else "") + summary[:480],
            "case_type": "hcf_commercial",
            "npi_in_source": npi if re.fullmatch(r"\d{10}", npi) else "",
        })
    cases = build_case_db(rows) if rows else empty
    if len(cases):
        cases["case_type"] = "hcf_commercial"
        if "npi_in_source" not in cases.columns:
            cases["npi_in_source"] = [r["npi_in_source"] for r in rows]
    return cases, pd.DataFrame(bad)


_CIA_ENTITY_ALIASES = {
    "party": ["party", "cia_party", "parent", "parent_name"],
    "enrolled_entity_name": ["enrolled_entity_name", "entity_name", "entity", "subsidiary", "name"],
    "city": ["city"], "state": ["state", "st"],
    "npi": ["npi", "npi_number"],
    "evidence_url": ["evidence_url", "source_url", "url", "source"],
}
_EXCL_NPI_ALIASES = {
    "source_node_id": ["source_node_id", "node_id", "id"],
    "entity_name": ["entity_name", "name"], "state": ["state", "st"],
    "npi": ["npi"], "confidence": ["confidence", "conf"],
    "employer_name": ["employer_name", "employer"], "employer_npi": ["employer_npi"],
    "evidence_url": ["evidence_url", "source_url", "url", "source"],
}


_OUTCOME_ALIASES = {
    "case_id": ["case_id", "our_case_id", "id"],
    "defendant_name": ["defendant_name", "defendant", "name"],
    "outcome": ["outcome", "result", "disposition"],
    "outcome_date": ["outcome_date", "date", "resolved_date", "sentencing_date", "plea_date"],
    "amount_usd": ["amount_usd", "amount", "restitution", "settlement_amount"],
    "jurisdiction": ["jurisdiction", "district", "court"],
    "evidence_url": ["evidence_url", "source_url", "url", "source", "link"],
    "outcome_summary": ["outcome_summary", "summary", "description", "notes"],
}
# the answer vocabulary -> (case_type of the NEW case row, the outcome words the
# case-label outcome rule recognises). Dismissed / acquitted CLEAR the charge.
_OUTCOME_WORDS = {
    "pleaded_guilty": ("criminal_hcf", "pleaded guilty"),
    "pled_guilty": ("criminal_hcf", "pleaded guilty"),
    "guilty_plea": ("criminal_hcf", "pleaded guilty"),
    "convicted": ("criminal_hcf", "convicted"),
    "sentenced": ("criminal_hcf", "sentenced"),
    "settled": ("civil_fca", "agreed to pay a settlement"),
    "settlement": ("civil_fca", "agreed to pay a settlement"),
    "judgment": ("civil_fca", "judgment"),
}
_OUTCOME_CLEARED = {"dismissed", "acquitted", "not_guilty", "acquittal", "dismissal"}


def load_outcome_refresh(table: pd.DataFrame, case_db: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """A reviewed outcome-refresh answer (case_harvest_prompts --sets outcome_refresh)
    -> (new case rows, cleared charges, n_pending).

    A RESOLVED outcome (plea / conviction / sentencing / settlement / judgment) becomes
    its OWN case row in the case-DB schema (the outcome release, dated by the outcome,
    keyed by its evidence URL, summary in words the outcome rule reads), so the next
    label build labels the defendant as a doj_case positive and the charged row
    converts by precedence. A dismissal or acquittal is returned as a CLEARED row
    (case_id, outcome, outcome_date, evidence_url): the CLI marks the charged row
    usable=0 so it never labels again. Rows answered 'pending' / 'unknown' are
    counted, never loaded. A case_id not in the case DB is ignored (the answer must
    be about a case we asked about); an outcome without an evidence URL is ignored
    (no record, no label)."""
    empty = pd.DataFrame(columns=CASE_COLUMNS)
    cleared_cols = ["case_id", "outcome", "outcome_date", "evidence_url"]
    if table is None or not len(table) or case_db is None or not len(case_db):
        return empty, pd.DataFrame(columns=cleared_cols), 0
    resolved = _resolve_columns(list(table.columns), _OUTCOME_ALIASES)
    d = table.rename(columns={v: k for k, v in resolved.items()}).copy()
    if "case_id" not in d.columns or "outcome" not in d.columns:
        return empty, pd.DataFrame(columns=cleared_cols), 0
    known = case_db.drop_duplicates("case_id").set_index(case_db.drop_duplicates("case_id")["case_id"].astype(str))
    rows, cleared, pending = [], [], 0
    for r in d.to_dict("records"):
        cid = str(r.get("case_id") or "").strip()
        if cid not in known.index:
            continue
        orig = known.loc[cid]
        oc = str(r.get("outcome") or "").strip().lower().replace(" ", "_")
        url = str(r.get("evidence_url") or "").strip()
        date = str(r.get("outcome_date") or "").strip()[:10]
        if oc in _OUTCOME_CLEARED:
            if url:
                cleared.append({"case_id": cid, "outcome": oc, "outcome_date": date, "evidence_url": url})
            continue
        if oc not in _OUTCOME_WORDS:
            pending += 1
            continue
        if not url:
            continue
        ctype, words = _OUTCOME_WORDS[oc]
        defendant = str(r.get("defendant_name") or "").strip() or str(orig.get("defendant_name") or "")
        juris = str(r.get("jurisdiction") or "").strip() or str(orig.get("jurisdiction") or "")
        note = str(r.get("outcome_summary") or "").strip()
        summary = (f"{defendant} {words} ({date or 'date not stated'}); outcome of the charge announced "
                   f"{str(orig.get('announced_date') or '')[:10]} [{cid}]. {note}").strip()
        rows.append({
            "case_id": url, "announced_date": date, "defendant_name": defendant,
            "sector": str(orig.get("sector") or ""), "scheme": str(orig.get("scheme") or ""),
            "amount_usd": pd.to_numeric(str(r.get("amount_usd") or "").replace("$", "").replace(",", ""),
                                        errors="coerce"),
            "qui_tam": pd.NA, "intervened": pd.NA, "jurisdiction": juris,
            "source_url": url, "summary": summary[:1500], "case_type": ctype,
        })
    new = build_case_db(rows) if rows else empty          # case_type passes through as given
    return new, pd.DataFrame(cleared, columns=cleared_cols), pending


def clear_charged_rows(case_csv: str | Path, cleared: pd.DataFrame) -> int:
    """Mark the charged case rows a dismissal / acquittal cleared as usable=0 (with a
    qc_note) in the case CSV: quarantine, never delete. Returns the rows changed."""
    if cleared is None or not len(cleared):
        return 0
    p = Path(case_csv)
    if not p.exists():
        return 0
    db = read_csv_text(str(p), dtype=str)
    if "usable" not in db.columns:
        db["usable"] = "1"
    if "qc_note" not in db.columns:
        db["qc_note"] = ""
    note = {str(r.case_id): f"cleared: {r.outcome} {r.outcome_date} {r.evidence_url}".strip()
            for r in cleared.itertuples()}
    hit = db["case_id"].astype(str).isin(note)
    db.loc[hit, "usable"] = "0"
    db.loc[hit, "qc_note"] = db.loc[hit, "case_id"].astype(str).map(note)
    db.to_csv(p, index=False)
    return int(hit.sum())


def load_cia_entities(table: pd.DataFrame, case_ids: dict[str, str] | None = None) -> pd.DataFrame:
    """A reviewed CIA-entity table (research answer CSV B) -> rows in the defendant-resolver
    shape (case_id, defendant_name, npi, status, match_level) for ``cia_defendant_npis``.
    Only Luhn-valid NPIs resolve (status resolved, match_level research_cia); a row without
    one is kept with status research_no_npi so the review is visible, never a label."""
    from src.attempt_2.clean_data import is_valid_npi
    from src.enforcement.cia import normalize_cia  # noqa: F401  (schema owner)
    if table is None or not len(table):
        return pd.DataFrame(columns=["case_id", "defendant_name", "npi", "org_node_id", "status", "match_level",
                                     "n_orgs_for_party", "label_weight", "enrolled_entity_name", "evidence_url"])
    resolved = _resolve_columns(list(table.columns), _CIA_ENTITY_ALIASES)
    d = table.rename(columns={v: k for k, v in resolved.items()}).copy()
    rows = []
    for r in d.to_dict("records"):
        party = str(r.get("party") or "").strip()
        key = norm_org_name(party).upper()
        npi = str(r.get("npi") or "").strip()
        ok = bool(npi) and is_valid_npi(npi)
        rows.append({"case_id": (case_ids or {}).get(key) or f"cia:{key}", "defendant_name": party,
                     "npi": npi if ok else "", "org_node_id": "",
                     "status": "resolved" if ok else "research_no_npi", "match_level": "research_cia" if ok else "",
                     "n_orgs_for_party": 1, "label_weight": 1.0,
                     "enrolled_entity_name": str(r.get("enrolled_entity_name") or "").strip(),
                     "evidence_url": str(r.get("evidence_url") or "").strip()})
    out = pd.DataFrame(rows)
    return out.drop_duplicates(["case_id", "npi", "enrolled_entity_name"]).reset_index(drop=True)


def load_exclusion_npis(table: pd.DataFrame, residue: pd.DataFrame,
                        min_confidence: str = "medium") -> pd.DataFrame:
    """A reviewed excluded-names answer (research CSV) joined back to the residue rows by
    source_node_id -> the exclusions schema (exclusions_name_resolved rows, match_level
    research_<confidence>). Luhn-valid NPI and confidence >= ``min_confidence`` required;
    the rest never leave the review file."""
    from src.attempt_2.clean_data import is_valid_npi
    rank = {"high": 3, "medium": 2, "low": 1}
    need = rank.get(str(min_confidence).lower(), 2)
    cols = EXCLUSION_COLS + ["match_level", "source_node_id", "employer_name", "employer_npi", "evidence_url"]
    if table is None or not len(table) or residue is None or not len(residue):
        return pd.DataFrame(columns=cols)
    resolved = _resolve_columns(list(table.columns), _EXCL_NPI_ALIASES)
    d = table.rename(columns={v: k for k, v in resolved.items()}).copy()
    rs = residue.set_index(residue["source_node_id"].astype(str))
    rows = []
    for r in d.to_dict("records"):
        sid = str(r.get("source_node_id") or "").strip()
        npi = str(r.get("npi") or "").strip()
        conf = str(r.get("confidence") or "").strip().lower()
        if sid not in rs.index or not npi or not is_valid_npi(npi) or rank.get(conf, 0) < need:
            continue
        src = rs.loc[sid]
        cands = str(src.get("candidate_npis") or "")
        if cands and npi not in cands.split(";"):
            continue                                        # the agent must choose among the records our index found
        emp = str(r.get("employer_npi") or "").strip()
        rows.append({"npi": npi, "entity_name": str(src.get("entity_name") or ""),
                     "name_key": norm_org_name(str(src.get("entity_name") or "")),
                     "excl_type": str(src.get("excl_type") or ""),
                     "excl_date": pd.to_datetime(src.get("excl_date"), errors="coerce"),
                     "reinstate_date": pd.NaT, "currently_active": 1,
                     "match_level": f"research_{conf}", "source_node_id": sid,
                     "employer_name": str(r.get("employer_name") or "").strip(),
                     "employer_npi": emp if emp and is_valid_npi(emp) else "",
                     "evidence_url": str(r.get("evidence_url") or "").strip()})
    return pd.DataFrame(rows, columns=cols).drop_duplicates(["npi", "excl_type", "excl_date"]).reset_index(drop=True)


def merge_into_case_db(new_cases: pd.DataFrame, case_csv: str | Path) -> pd.DataFrame:
    """Union new cases into the case CSV, dedup by case_id (existing rows win —
    the DOJ feed's richer parses are never overwritten by a sweep row)."""
    p = Path(case_csv)
    if p.exists():
        existing = read_csv_text(str(p), dtype=str)
        merged = pd.concat([existing, new_cases.astype(str)], ignore_index=True)
        merged = merged.drop_duplicates(subset=["case_id"], keep="first")
    else:
        merged = new_cases.astype(str)
    p.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(p, index=False)
    return merged


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    se = sub.add_parser("state-exclusions",
                        help="reviewed state termination list → exclusions parquet")
    se.add_argument("--in", dest="inp", required=True)
    se.add_argument("--state", required=True)
    se.add_argument("--processed", required=True,
                    help="processed/ dir (writes exclusions_state_<st>.parquet)")

    mf = sub.add_parser("mfcu-cases",
                        help="reviewed MFCU settlement table → case DB merge")
    mf.add_argument("--in", dest="inp", required=True)
    mf.add_argument("--state", required=True)
    mf.add_argument("--case-db", required=True,
                    help="cases csv to merge into (created if absent)")

    cm = sub.add_parser("commercial-cases",
                        help="reviewed commercial-plan fraud convictions -> case DB rows typed hcf_commercial "
                             "(training-only source; never the forward ruler or the strict label)")
    cm.add_argument("--in", dest="inp", required=True)
    cm.add_argument("--state", required=True)
    cm.add_argument("--case-db", required=True, help="cases csv to merge into (created if absent)")

    ce = sub.add_parser("cia-entities", help="reviewed CIA entity table (research CSV B) -> cia_defendant_npis rows")
    ce.add_argument("--in", dest="inp", required=True)
    ce.add_argument("--cia-cases", default=None, help="enforcement/cia_cases.csv (maps party -> case_id)")
    ce.add_argument("--out", required=True, help="processed/cia_defendant_npis.parquet (appended, de-duplicated)")

    en = sub.add_parser("exclusion-npis", help="reviewed excluded-names answer -> exclusions_name_resolved rows")
    en.add_argument("--in", dest="inp", required=True)
    en.add_argument("--residue", required=True, help="processed/exclusion_name_residue.csv")
    en.add_argument("--min-confidence", default="medium", choices=["high", "medium", "low"])
    en.add_argument("--out", required=True, help="processed/exclusions_name_resolved.parquet (appended, de-duplicated)")

    orf = sub.add_parser("outcome-refresh", help="reviewed outcome-refresh answer -> outcome case rows merged "
                                                 "into the case DB; dismissals / acquittals clear the charged row")
    orf.add_argument("--in", dest="inp", required=True)
    orf.add_argument("--case-db", required=True, help="enforcement/doj_cases_v3.csv (merged in place)")

    args = ap.parse_args()
    table = _read_any(args.inp)
    if args.cmd == "commercial-cases":
        cases, quarantine = load_commercial_cases(table, args.state)
        merged = merge_into_case_db(cases, args.case_db) if len(cases) else None
        print(f"[research_evidence] {len(cases):,} {args.state.upper()} commercial-plan fraud rows "
              f"(case_type hcf_commercial) -> {args.case_db}"
              + (f" (case DB now {len(merged):,} rows)" if merged is not None else "")
              + f"; {len(quarantine):,} rows without a resolved outcome quarantined")
        if len(quarantine):
            qp = Path(args.inp).with_name(Path(args.inp).stem + "_quarantine.csv")
            quarantine.to_csv(qp, index=False)
            print(f"[research_evidence] quarantine -> {qp}")
        print("Trains under --label-tier hard+commercial (an A/B refit first: accuracy_diagnostics "
              "--score-source refit --label-tier hard+commercial); never a forward positive.")
        return
    if args.cmd == "outcome-refresh":
        cdb = read_csv_text(args.case_db, dtype=str)
        new, cleared, pending = load_outcome_refresh(table, cdb)
        merged = merge_into_case_db(new, args.case_db) if len(new) else cdb
        n_clear = clear_charged_rows(args.case_db, cleared)
        print(f"[research_evidence] outcome refresh: {len(new):,} resolved-outcome case rows merged "
              f"({len(merged):,} rows in {args.case_db}); {n_clear:,} charged rows cleared (usable=0: "
              f"dismissal / acquittal); {pending:,} still pending / unknown. Rebuild the case labels "
              "(the next export) so the converted charges train as doj_case.")
        return
    if args.cmd == "cia-entities":
        case_ids = None
        if args.cia_cases:
            cc = pd.read_csv(args.cia_cases, dtype=str)
            case_ids = dict(zip(cc["defendant_name_key"].astype(str).str.upper(), cc["case_id"].astype(str)))
        rows = load_cia_entities(table, case_ids)
        out = Path(args.out)
        prev = pd.read_parquet(out) if out.exists() else None
        allrows = pd.concat([prev, rows], ignore_index=True) if prev is not None and len(prev) else rows
        allrows = allrows.drop_duplicates(["case_id", "npi"], keep="first")
        out.parent.mkdir(parents=True, exist_ok=True)
        allrows.to_parquet(out, index=False)
        print(f"[research_evidence] {int((rows['status'] == 'resolved').sum()):,} CIA entity rows resolved "
              f"({int((rows['status'] != 'resolved').sum()):,} without an NPI, kept for review) -> {out} "
              f"({len(allrows):,} rows)")
        return
    if args.cmd == "exclusion-npis":
        residue = pd.read_csv(args.residue, dtype=str)
        rows = load_exclusion_npis(table, residue, min_confidence=args.min_confidence)
        out = Path(args.out)
        prev = pd.read_parquet(out) if out.exists() else None
        allrows = pd.concat([prev, rows], ignore_index=True) if prev is not None and len(prev) else rows
        allrows = allrows.drop_duplicates(["npi", "excl_type", "excl_date"], keep="first")
        out.parent.mkdir(parents=True, exist_ok=True)
        allrows.to_parquet(out, index=False)
        print(f"[research_evidence] {len(rows):,} excluded names resolved at >= {args.min_confidence} -> {out} "
              f"({len(allrows):,} rows); rebuild the graph to use them")
        return
    if args.cmd == "state-exclusions":
        excl, quarantine = load_state_exclusions(table, args.state)
        out = Path(args.processed) / f"exclusions_state_{args.state.strip().lower()}.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        excl.to_parquet(out, index=False)
        print(f"[research_evidence] {len(excl):,} exclusion rows "
              f"({int((excl['npi'] != '').sum()):,} with NPI) → {out}")
        if len(quarantine):
            qp = out.with_name(out.stem + "_quarantine.csv")
            quarantine.to_csv(qp, index=False)
            print(f"[research_evidence] {len(quarantine):,} rows quarantined "
                  f"(no NPI and no usable name) → {qp}")
        print("The next graph build merges every processed/exclusions_*.parquet "
              "automatically; the widened label picks the rows up from there.")
    else:
        cases = load_mfcu_cases(table, args.state)
        merged = merge_into_case_db(cases, args.case_db)
        print(f"[research_evidence] {len(cases):,} {args.state.upper()} MFCU "
              f"cases → {args.case_db} (case DB now {len(merged):,} rows)")


if __name__ == "__main__":
    main()
