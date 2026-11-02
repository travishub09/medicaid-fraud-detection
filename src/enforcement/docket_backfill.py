"""
docket_backfill.py — every unsealed False Claims Act docket → case rows (source 2).

CourtListener indexes federal dockets with a nature-of-suit code: 375 (False Claims
Act) and 376 (Qui Tam). The docket monitor already pulls 376 going forward; this
module backfills BOTH codes across every district since a start date and writes
them as case rows the defendant resolver can turn into NPIs:

  case_id        the docket URL (stable)
  defendant_name whatever follows the last " v. " in the caption
                 ("United States ex rel. Smith v. Acme Health LLC" → "Acme Health LLC")
  jurisdiction   the court id spelled as a district ("flsd" → "S.D. Fla.")
  announced_date date_filed
  case_type      docket_fca / docket_qui_tam
  amount_usd     blank (a docket carries no outcome)

A docket is an ALLEGATION. Most qui tams are declined and dismissed, so docket-only
cases are weak-tier evidence: ``case_labels`` records them under source
``docket_case`` and they never flip the hard PU label unless the same case also
appears as a settlement or judgment in the press-release harvest. Leads for counsel
review, never accusations.

DOCKET OUTCOMES (architecture audit experiment 7, 2026-11-02): a docket whose ENTRIES
carry a settlement, a consent judgment, a judgment entered, or a stipulation of
dismissal pursuant to a settlement is no longer an allegation: ``docket_outcome_rows``
turns such a docket into its OWN case row (case_type ``docket_outcome``,
announced_date = the entry date, the summary in the outcome rule's words, the docket
id and the entry number kept) so the forward ruler can admit it. A plain dismissal,
a motion, a brief, or a judgment for the defendant is never an outcome.

  python -m src.enforcement.docket_backfill --since 2015-01-01 --out feeds/enforcement/fca_dockets.csv
  python -m src.enforcement.docket_backfill --since 2015-01-01 --out ... --merge-into enforcement/doj_cases_v3.csv
  python -m src.enforcement.docket_backfill --outcomes-from feeds/enforcement/docket_entries.csv \\
      --dockets feeds/enforcement/dockets.csv --out feeds/enforcement/docket_outcomes.csv [--merge-into ...]
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.enforcement.case_db import build_case_db
from src.entity_graph.resolve_entities import norm_org_name
from src.feeds.client import default_fetch_json
from src.sourcing.docket_monitor import _fetch_dockets

NOS_FCA = "375"
NOS_QUI_TAM = "376"
FEED_NAME = "courtlistener_fca_backfill"

_DIVISION = {"n": "N.D.", "s": "S.D.", "e": "E.D.", "w": "W.D.", "m": "M.D.", "c": "C.D."}
_STATE_ABBR_DISTRICT = {
    "al": "Ala.", "ak": "Alaska", "az": "Ariz.", "ar": "Ark.", "ca": "Cal.", "co": "Colo.", "ct": "Conn.",
    "de": "Del.", "dc": "D.C.", "fl": "Fla.", "ga": "Ga.", "hi": "Haw.", "id": "Idaho", "il": "Ill.",
    "in": "Ind.", "ia": "Iowa", "ks": "Kan.", "ky": "Ky.", "la": "La.", "me": "Me.", "md": "Md.",
    "ma": "Mass.", "mi": "Mich.", "mn": "Minn.", "ms": "Miss.", "mo": "Mo.", "mt": "Mont.", "ne": "Neb.",
    "nv": "Nev.", "nh": "N.H.", "nj": "N.J.", "nm": "N.M.", "ny": "N.Y.", "nc": "N.C.", "nd": "N.D.",
    "oh": "Ohio", "ok": "Okla.", "or": "Or.", "pa": "Pa.", "ri": "R.I.", "sc": "S.C.", "sd": "S.D.",
    "tn": "Tenn.", "tx": "Tex.", "ut": "Utah", "vt": "Vt.", "va": "Va.", "wa": "Wash.", "wv": "W. Va.",
    "wi": "Wis.", "wy": "Wyo.", "pr": "P.R.", "gu": "Guam", "vi": "V.I.",
}
_COURT_ID = re.compile(r"^([a-z]{2})([nsewmc])?d$")


def court_to_district(court_id: str) -> str:
    """'flsd' → 'S.D. Fla.'; 'scd' → 'D.S.C.'; anything else → ''."""
    m = _COURT_ID.match(str(court_id or "").strip().lower())
    if not m:
        return ""
    st = _STATE_ABBR_DISTRICT.get(m.group(1))
    if not st:
        return ""
    div = _DIVISION.get(m.group(2) or "")
    return f"{div} {st}" if div else f"D. {st}"


def court_to_state(court_id: str) -> str:
    m = _COURT_ID.match(str(court_id or "").strip().lower())
    return m.group(1).upper() if m and m.group(1) in _STATE_ABBR_DISTRICT else ""


def defendant_from_caption(case_name: str) -> str:
    """The party after the last ' v. ' with 'et al' and trailing docket noise stripped."""
    name = str(case_name or "")
    for sep in (" v. ", " v "):
        if sep in name:
            d = name.rsplit(sep, 1)[1]
            d = re.sub(r"\bet\s+al\.?$", "", d, flags=re.I).strip(" ,;")
            return d
    return ""


def dockets_to_cases(dockets: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for d in dockets.itertuples(index=False):
        dd = d._asdict()
        defendant = defendant_from_caption(dd.get("case_name", ""))
        if not defendant:
            continue
        nos = str(dd.get("nature_of_suit") or "")
        ctype = "docket_qui_tam" if nos.startswith(NOS_QUI_TAM) or "376" in nos else "docket_fca"
        court = str(dd.get("court") or "")
        rows.append({
            "case_id": str(dd.get("docket_url") or dd.get("docket_id") or ""),
            "announced_date": str(dd.get("date_filed") or "")[:10],
            "defendant_name": defendant,
            "defendant_name_key": norm_org_name(defendant),
            "sector": "", "scheme": "", "amount_usd": pd.NA,
            "qui_tam": 1 if ctype == "docket_qui_tam" else pd.NA,
            "intervened": pd.NA,
            "jurisdiction": court_to_district(court),
            "source_url": str(dd.get("docket_url") or ""),
            "summary": f"{dd.get('case_name', '')}; nature of suit {nos}; court {court}; filed {dd.get('date_filed', '')}"
                       + (f"; terminated {dd.get('date_terminated')}" if dd.get("date_terminated") else ""),
            "case_type": ctype,
        })
    return build_case_db(rows) if rows else build_case_db([])


# ------------------------------------------------------------ docket outcomes

DOCKET_OUTCOME_CASE_TYPE = "docket_outcome"
OUTCOME_EXTRA_COLS = ["docket_id", "entry_number", "outcome_kind"]   # beside case_db.CASE_COLUMNS

# the outcome vocabulary is the strict export's (fca_label_export._CIVIL_OUTCOME reads
# "settle|settlement|judgment|consent judgment|agreed to pay" off the summary we write);
# on a DOCKET ENTRY the words sit in a one-line description, so the entry has to be the
# court's or the parties' ACT, never a request: a motion, brief, response or objection is
# pending; a plain dismissal is the usual end of a declined qui tam; a judgment for the
# defendant or of dismissal is a loss; summary judgment is unread (whose?); a vacated or
# denied entry undoes one.
_ENTRY_SETTLEMENT = re.compile(r"\bsettle(?:d|ment|s)?\b|agree[sd]?\s+to\s+pay|consent\s+(?:judgment|decree)", re.I)
_ENTRY_JUDGMENT = re.compile(r"^\s*(?:(?:final|default|consent|amended|clerk'?s)\s+)*judgment\b|"
                             r"\bjudgment\s+(?:is\s+)?(?:hereby\s+)?entered\b|\bentry\s+of\s+(?:final\s+|default\s+)?judgment\b|"
                             r"\bjudgment\s+(?:in\s+favor\s+of|for|against)\s+|\bsatisfaction\s+of\s+judgment\b", re.I)
_ENTRY_NOT_AN_OUTCOME = re.compile(r"^\s*(?:(?:joint|unopposed|emergency|amended|renewed|first|second|third|ex\s+parte)\s+)*"
                                   r"(?:motion|brief|memorandum|response|reply|opposition|objection|proposed|"
                                   r"notice\s+of\s+(?:appeal|motion))\b|"
                                   r"\bjudgment\s+of\s+dismissal\b|\bin\s+favor\s+of\s+(?:the\s+)?defendants?\b|"
                                   r"\bfor\s+(?:the\s+)?defendants?\b|\bsummary\s+judgment\b|\bdenying\b|\bdenied\b|\bvacat|"
                                   r"\bstricken\b|\bwithdraw", re.I)
_INTERVENE = re.compile(r"election\s+to\s+intervene|notice\s+of\s+intervention|government'?s?\s+complaint\s+in\s+intervention", re.I)
_DECLINE = re.compile(r"election\s+(?:to\s+)?declin|declin\w*\s+to\s+intervene|notice\s+of\s+declination", re.I)


def entry_outcome(description: str) -> str:
    """'settlement' | 'judgment' | '' for one docket-entry description."""
    d = str(description or "")
    if not d.strip() or _ENTRY_NOT_AN_OUTCOME.search(d):
        return ""
    if _ENTRY_SETTLEMENT.search(d):
        return "settlement"
    if _ENTRY_JUDGMENT.search(d):
        return "judgment"
    return ""


def _col(df: pd.DataFrame, *names: str) -> pd.Series:
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series("", index=df.index, dtype=object)


_TRAILING_ID = re.compile(r"(\d+)/?\s*$")


def _docket_key(v) -> str:
    """A docket id as the API gives it ('123', 123.0, or the resource URL
    '.../dockets/123/') -> '123'."""
    s = str(v if v is not None else "").strip()
    if s.lower() in ("", "nan", "none"):
        return ""
    if s.startswith(("http://", "https://", "/")):
        m = _TRAILING_ID.search(s)
        return m.group(1) if m else s
    return re.sub(r"\.0$", "", s)


def docket_outcome_rows(dockets: pd.DataFrame | None, entries: pd.DataFrame) -> pd.DataFrame:
    """Dockets whose entries carry an outcome -> case rows (case_type ``docket_outcome``).

    ``entries``: one row per docket entry (docket_id | docket, entry_number, date_filed |
    entry_date, description); ``dockets``: the listing (_fetch_dockets shape: docket_id,
    case_name, court, nature_of_suit, date_filed, docket_url); when None, the entries must
    carry case_name / court themselves. The FIRST outcome entry (by date) dates the case:
    announced_date = that entry's date. One row per docket, keyed by the entry's own URL
    (never the docket's: the allegation row keeps its key, and case_db.merge_case_files
    appends the outcome beside it). The summary states the outcome in the outcome rule's
    words so ``fca_label_export.outcome_tier`` / ``case_labels`` admit the row once they
    read ``docket_outcome`` like ``civil_fca`` (integration note in the batch report).
    ``intervened`` is read off the docket's entries (election to intervene / to decline);
    ``amount_usd`` off the outcome entry when it states one. A docket with only a plain
    dismissal yields nothing."""
    from src.enforcement.case_db import _extract_amount
    if entries is None or not len(entries):
        return build_case_db([])
    en = entries.copy()
    en["_did"] = _col(en, "docket_id", "docket").map(_docket_key)
    en["_n"] = pd.to_numeric(_col(en, "entry_number", "entry", "recap_sequence_number"), errors="coerce")
    en["_dt"] = pd.to_datetime(_col(en, "date_filed", "entry_date", "date"), errors="coerce")
    en["_desc"] = _col(en, "description", "entry_description", "text", "short_description").fillna("").astype(str)
    en = en[en["_did"] != ""]
    if not len(en):
        return build_case_db([])
    dk: dict[str, dict] = {}
    if dockets is not None and len(dockets):
        d = dockets.copy()
        d["_did"] = _col(d, "docket_id", "id").map(_docket_key)
        for r in d[d["_did"] != ""].to_dict("records"):
            dk.setdefault(r["_did"], r)
    en["_kind"] = en["_desc"].map(entry_outcome)
    rows = []
    for did, g in en.groupby("_did", sort=True):
        hits = g[(g["_kind"] != "") & g["_dt"].notna()].sort_values(["_dt", "_n"])
        if not len(hits):
            continue
        h = hits.iloc[0]
        meta = dk.get(did) or {}
        first = g.sort_values("_dt").iloc[0]
        case_name = str(meta.get("case_name") or _col(g, "case_name").iloc[0] or "")
        court = str(meta.get("court") or _col(g, "court").iloc[0] or "")
        nos = str(meta.get("nature_of_suit") or _col(g, "nature_of_suit").iloc[0] or "")
        docket_url = str(meta.get("docket_url") or meta.get("absolute_url") or _col(g, "docket_url").iloc[0] or "")
        defendant = defendant_from_caption(case_name)
        if not defendant:
            continue
        n = int(h["_n"]) if pd.notna(h["_n"]) else 0
        entry_url = (docket_url.rstrip("/") + f"/entry-{n}/") if docket_url else f"courtlistener:docket:{did}/entry-{n}/"
        texts = " ".join(g["_desc"].tolist())
        intervened = 1 if _INTERVENE.search(texts) else (0 if _DECLINE.search(texts) else pd.NA)
        qui_tam = 1 if ("376" in nos or re.search(r"\bex\s+rel\b", case_name, re.I)) else pd.NA
        words = "settlement" if h["_kind"] == "settlement" else "judgment entered"
        amt = _extract_amount(h["_desc"])
        filed = str(meta.get("date_filed") or "")[:10] or (str(first["_dt"].date()) if pd.notna(first["_dt"]) else "")
        rows.append({
            "case_id": entry_url,
            "announced_date": h["_dt"].strftime("%Y-%m-%d"),
            "defendant_name": defendant,
            "defendant_name_key": norm_org_name(defendant),
            "sector": "", "scheme": "",
            "amount_usd": amt if amt else pd.NA,
            "qui_tam": qui_tam, "intervened": intervened,
            "jurisdiction": court_to_district(court),
            "source_url": entry_url,
            "summary": (f"{case_name}; nature of suit {nos}; court {court}; docket {did} filed {filed}. "
                        f"Resolved by {words} on the docket: entry {n} of {h['_dt'].date()}: {h['_desc'][:300]}"),
            "case_type": DOCKET_OUTCOME_CASE_TYPE,
            "docket_id": did, "entry_number": n, "outcome_kind": h["_kind"],
        })
    out = build_case_db(rows) if rows else build_case_db([])
    if rows:
        extra = pd.DataFrame(rows)[["case_id"] + OUTCOME_EXTRA_COLS]
        out = out.merge(extra.drop_duplicates("case_id"), on="case_id", how="left")
    else:
        for c in OUTCOME_EXTRA_COLS:
            out[c] = pd.Series(dtype=object)
    return out


def run_backfill(since: str, fetch_json=default_fetch_json, max_pages: int = 2000,
                 sleep_s: float = 0.5) -> pd.DataFrame:
    dockets = _fetch_dockets((NOS_FCA, NOS_QUI_TAM), since, FEED_NAME, fetch_json=fetch_json,
                             max_pages=max_pages, sleep_s=sleep_s)
    return dockets_to_cases(dockets)


def merge_into_case_db(cases: pd.DataFrame, case_db_path: Path) -> pd.DataFrame:
    """Append docket rows to an existing case CSV; existing case_ids win."""
    existing = pd.read_csv(case_db_path, dtype=str) if case_db_path.exists() else build_case_db([])
    merged = build_case_db(pd.concat([existing, cases], ignore_index=True))
    return merged


def report(cases: pd.DataFrame) -> str:
    L = ["# FCA DOCKET BACKFILL (CourtListener nature of suit 375 + 376)", "",
         "_Dockets are allegations; docket-only cases are weak-tier evidence until an outcome is known._", "",
         f"- docket cases: **{len(cases):,}**; with a district: **{int((cases['jurisdiction'].fillna('') != '').sum()):,}**",
         "- by type: " + ", ".join(f"{k} {v:,}" for k, v in cases["case_type"].value_counts().items()), ""]
    if len(cases):
        st = cases["jurisdiction"].fillna("").str.extract(r"([A-Z][A-Za-z.]+\.?(?: [A-Z][a-z]+\.?)?)$")[0]
        L += ["## By district (top 25)", ""]
        for j, n in cases["jurisdiction"].fillna("(none)").value_counts().head(25).items():
            L.append(f"- {j}: {n}")
        yrs = cases["announced_date"].fillna("").str[:4].value_counts().sort_index()
        L += ["", "## By year filed", ""] + [f"- {y}: {n}" for y, n in yrs.items() if y]
    return "\n".join(L)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="2015-01-01")
    ap.add_argument("--out", required=True, help="fca_dockets.csv")
    ap.add_argument("--merge-into", default=None, help="an existing case CSV to append the docket rows to")
    ap.add_argument("--max-pages", type=int, default=2000)
    ap.add_argument("--outcomes-from", default=None,
                    help="a docket ENTRIES csv (docket_id, entry_number, date_filed, description): write the "
                         "docket_outcome case rows (settlement / consent judgment / judgment entered / dismissal "
                         "pursuant to settlement) to --out instead of fetching; no network, no token")
    ap.add_argument("--dockets", default=None,
                    help="with --outcomes-from: the docket listing csv (docket_id, case_name, court, nature_of_suit, "
                         "date_filed, docket_url); without it the entries csv must carry case_name / court")
    a = ap.parse_args()
    import os
    if a.outcomes_from:
        from src.attempt_2.clean_data import read_csv_text
        entries = read_csv_text(a.outcomes_from, dtype=str)
        dockets = read_csv_text(a.dockets, dtype=str) if a.dockets else None
        cases = docket_outcome_rows(dockets, entries)
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        cases.to_csv(out, index=False)
        kinds = ", ".join(f"{k} {v:,}" for k, v in cases["outcome_kind"].value_counts().items()) if len(cases) else "none"
        n_dockets = entries.get("docket_id", entries.get("docket", pd.Series(dtype=str))).nunique()
        msg = (f"docket outcomes: {len(cases):,} dockets with an outcome entry of {n_dockets:,} dockets in "
               f"{a.outcomes_from} ({kinds}) -> {out}")
        if a.merge_into:
            merged = merge_into_case_db(cases, Path(a.merge_into))
            merged.to_csv(a.merge_into, index=False)
            msg += f"\n  merged into {a.merge_into}: {len(merged):,} cases total"
        print(msg)
        return
    if not os.environ.get("COURTLISTENER_TOKEN"):
        raise SystemExit("CourtListener's API refuses anonymous requests (HTTP 401). Make a free account at "
                         "https://www.courtlistener.com/sign-in/, open your profile page, copy the API token, "
                         "then in this window:  set COURTLISTENER_TOKEN=<the token>  and re-run.")
    cases = run_backfill(a.since, max_pages=a.max_pages)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cases.to_csv(out, index=False)
    rep = out.with_name("FCA_DOCKET_BACKFILL.md")
    rep.write_text(report(cases), encoding="utf-8")
    msg = f"fca dockets: {len(cases):,} cases since {a.since} -> {out}\n  report: {rep}"
    if a.merge_into:
        merged = merge_into_case_db(cases, Path(a.merge_into))
        merged.to_csv(a.merge_into, index=False)
        msg += f"\n  merged into {a.merge_into}: {len(merged):,} cases total"
    print(msg)


if __name__ == "__main__":
    main()
