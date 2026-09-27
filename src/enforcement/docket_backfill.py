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

  python -m src.enforcement.docket_backfill --since 2015-01-01 --out feeds/enforcement/fca_dockets.csv
  python -m src.enforcement.docket_backfill --since 2015-01-01 --out ... --merge-into enforcement/doj_cases_v3.csv
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
    a = ap.parse_args()
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
