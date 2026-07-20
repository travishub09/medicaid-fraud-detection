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

    args = ap.parse_args()
    table = _read_any(args.inp)
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
