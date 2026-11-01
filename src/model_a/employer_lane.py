"""employer_lane.py — convicted individuals -> the business that billed for them (2026-11-01).

THE RECALL CEILING, in one number: 83 percent of the forward positives on paper never
join the training table, because a convicted nurse, aide, marketer, biller or owner
rarely billed Medicaid under their OWN provider number; the claims went out under the
agency's or the clinic's NPI. The matrix is billing NPIs, so a positive on a person
who never billed teaches the model nothing. The affiliated-conviction path
(label_expansion.affiliated_convictions) already resolves the individuals who appear
as the RENDERING NPI on another biller's claims. This lane takes the residue: the
hard-tier individuals with no billing row and no rendering row at all, and asks the
research agent for the employer at the time of the conduct and that employer's NPI.

Outputs, two files for two consumers:
  * processed/affiliated_research.parquet  (label_expansion.AFF_COLS + evidence):
    `prospective_label --affiliated` folds the employer in as a forward positive dated
    by the individual's exclusion / conviction (basis affiliated_research);
  * processed/exclusions_employer_resolved.parquet  (the exclusions schema, excl_type
    "affiliated:<the individual's excl_type>|research_employer"): the next graph build
    makes the employer an exclusion node, fca_relevance tiers it from the individual's
    statute, and the training label takes it under --label-tier hard.

GATES on every answer (the machine gate a human review would apply): the person NPI is
one WE sent; the employer NPI is Luhn-valid, is an ORGANIZATION in NPPES (entity type
2), and NPPES's name agrees with the employer name the agent gave (label_gates.
name_agrees); confidence >= medium; the employment window overlaps the conduct window
when both are stated. Everything else goes to the lane's quarantine file with the reason.
A chain (an employer NPI the agent names for 25+ different individuals in one load) is
listed, never labelled.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import is_valid_npi

CAND_COLS = ["person_npi", "person_name", "state", "city", "excl_type", "excl_date", "fca_tier",
             "fca_basis", "source", "case_id", "summary"]
RESEARCH_COLS = ["npi", "excluded_npi", "excl_date", "excl_type", "fca_tier", "fca_basis", "entity_name",
                 "dollars_rendered", "biller_dollars_window", "share_of_biller", "months_rendered",
                 "rank_for_individual", "basis", "kept", "drop_reason", "employer_name", "confidence",
                 "evidence_url", "employment_start", "employment_end"]
EXCL_COLS = ["npi", "entity_name", "name_key", "excl_type", "excl_date", "reinstate_date", "currently_active"]
MAX_PER_EMPLOYER = 25          # an employer named for more individuals than this is a chain: listed, never labelled
_CONF = {"high": 3, "medium": 2, "low": 1}

EMPLOYER_PROMPT = (
    "Public-records research task. Each row below is a health-care worker or owner who was CONVICTED of, "
    "pleaded guilty to, or was excluded from federal health programs for health-care fraud or a related "
    "program crime, and who did NOT bill Medicaid under their own provider number. For EACH row, find the "
    "employer or business the person worked for or owned DURING the conduct (the agency, clinic, pharmacy, "
    "laboratory, nursing home or practice whose claims carried their work), using only public sources: the "
    "conviction or settlement press release (DOJ, U.S. Attorney, state Attorney General, Medicaid Fraud "
    "Control Unit), the court docket (PACER / CourtListener), the OIG exclusion record, the state licensing "
    "board order, and the NPPES NPI Registry (npiregistry.cms.hhs.gov) for the employer's NPI. Do not contact "
    "anyone. Return a CSV with exactly these columns: person_npi (copy ours exactly), person_name, "
    "employer_name (the legal or d/b/a name as enrolled), employer_npi (the ORGANIZATION's 10-digit NPI from "
    "NPPES; blank when more than one record fits or none is found), employer_city, employer_state, "
    "employment_start (YYYY-MM or YYYY), employment_end (YYYY-MM or YYYY), role (what the person did there), "
    "confidence (high / medium / low), evidence_url (the release, docket or order that names the employer), "
    "note (one sentence). One row per person; a person with two employers in the conduct window gets two rows. "
    "Blank beats a guess; a guessed NPI is worse than no NPI. Rows: {rows}"
)


# --------------------------------------------------------------------------- #
# candidates
# --------------------------------------------------------------------------- #

def _person_shape(name: str) -> bool:
    """'LAST, FIRST' or 'FIRST LAST' without a business token."""
    s = str(name or "").strip()
    if not s:
        return False
    if re.search(r"\b(inc|llc|l\.l\.c|corp|co|company|pllc|pc|p\.c|ltd|lp|llp|group|services?|center|"
                 r"centre|clinic|pharmacy|lab|laboratory|laboratories|health|care|medical|hospital|home|"
                 r"associates|partners|enterprises|solutions|management|agency|hospice|dme|supply)\b", s, re.I):
        return False
    return ("," in s) or (len(s.split()) in (2, 3))


def employer_candidates(exclusion_nodes: pd.DataFrame, spending: str | Path | None = None,
                        defendant_npis: pd.DataFrame | None = None, case_db: pd.DataFrame | None = None,
                        since: str = "2015-01-01", tier: str = "hard", con=None,
                        provider_dim: pd.DataFrame | None = None) -> pd.DataFrame:
    """The residue the rendering join cannot reach: hard-tier INDIVIDUALS (a valid NPI,
    a date on/after ``since``) that are neither a billing NPI nor a servicing NPI in
    the spending fact, plus DOJ resolved defendants of kind person in the same state.
    Without a spending file every individual is a candidate (logged by the caller)."""
    from .fca_relevance import classify_series, tier_mask
    rows: list[dict] = []
    if exclusion_nodes is not None and len(exclusion_nodes):
        e = exclusion_nodes.copy()
        e["npi"] = e.get("npi").astype("string").fillna("").str.strip()
        e = e[e["npi"].map(lambda s: is_valid_npi(str(s)))].copy()
        e["_dt"] = pd.to_datetime(e.get("excl_date"), errors="coerce")
        e = e[e["_dt"].notna() & (e["_dt"] >= pd.Timestamp(since))].copy()
        e["excl_type"] = e.get("excl_type", "").fillna("").astype(str)
        e = e[~e["excl_type"].str.lower().str.startswith("affiliated:")]      # never research an employer's employer
        t = classify_series(e["excl_type"])
        e["fca_tier"], e["fca_basis"] = t["fca_tier"].values, t["fca_basis"].values
        e = e[tier_mask(e["fca_tier"], tier).values]
        e["entity_name"] = e.get("entity_name", "").fillna("").astype(str)
        if provider_dim is not None and "entity_type" in provider_dim.columns:
            et = provider_dim.set_index(provider_dim["npi"].astype(str))["entity_type"].astype(str).str.replace(r"\.0$", "", regex=True)
            is_person = e["npi"].map(et).fillna("").eq("1") | (e["npi"].map(et).isna() & e["entity_name"].map(_person_shape))
        else:
            is_person = e["entity_name"].map(_person_shape)
        e = e[is_person]
        e = e.sort_values(["npi", "_dt"]).groupby("npi", as_index=False).first()
        e = e.rename(columns={"_dt": "dt_"})          # itertuples drops underscore-led names
        for r in e.itertuples():
            rows.append({"person_npi": str(r.npi), "person_name": r.entity_name,
                         "state": str(getattr(r, "state", "") or ""), "city": str(getattr(r, "city", "") or ""),
                         "excl_type": r.excl_type, "excl_date": r.dt_.strftime("%Y-%m-%d"),
                         "fca_tier": r.fca_tier, "fca_basis": r.fca_basis, "source": "exclusion",
                         "case_id": "", "summary": ""})
    if defendant_npis is not None and len(defendant_npis):
        d = defendant_npis.copy()
        st = d.get("status", pd.Series("", index=d.index)).astype(str)
        kind = d.get("kind", pd.Series("", index=d.index)).astype(str)
        d = d[(st == "resolved") & (kind == "person")]
        d["npi"] = d.get("npi", pd.Series("", index=d.index)).astype(str).str.strip()
        d = d[d["npi"].map(lambda s: is_valid_npi(str(s)))]
        have = {r["person_npi"] for r in rows}
        cdb = None
        if case_db is not None and len(case_db) and "case_id" in case_db.columns:
            cdb = case_db.drop_duplicates("case_id").set_index(case_db["case_id"].astype(str))
        for r in d.itertuples():
            if r.npi in have:
                continue
            cid = str(getattr(r, "case_id", "") or "")
            date = str(getattr(r, "announced_date", "") or "")
            summ = ""
            if cdb is not None and cid in cdb.index:
                date = date or str(cdb.loc[cid].get("announced_date") or "")
                summ = str(cdb.loc[cid].get("summary") or "")[:300]
            dt = pd.to_datetime(date, errors="coerce")
            if pd.isna(dt) or dt < pd.Timestamp(since):
                continue
            rows.append({"person_npi": r.npi, "person_name": str(getattr(r, "defendant", "") or ""),
                         "state": str(getattr(r, "state", "") or ""), "city": str(getattr(r, "city", "") or ""),
                         "excl_type": f"doj:{cid}", "excl_date": dt.strftime("%Y-%m-%d"), "fca_tier": "hard",
                         "fca_basis": "DOJ / court case (direct tier)", "source": "doj_case", "case_id": cid,
                         "summary": summ})
            have.add(r.npi)
    cand = pd.DataFrame(rows, columns=CAND_COLS)
    if not len(cand):
        return cand
    if spending is not None and Path(spending).exists():
        from src.attempt_2.duck import connect
        con = con or connect()
        con.register("emp_cand", cand[["person_npi"]])
        p = str(spending).replace("'", "''")
        cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()]
        parts = ["SELECT DISTINCT CAST(billing_npi AS VARCHAR) AS n FROM read_parquet('{p}') "
                 "WHERE CAST(billing_npi AS VARCHAR) IN (SELECT person_npi FROM emp_cand)".format(p=p)]
        if "servicing_npi" in cols:
            parts.append("SELECT DISTINCT CAST(servicing_npi AS VARCHAR) AS n FROM read_parquet('{p}') "
                         "WHERE CAST(servicing_npi AS VARCHAR) IN (SELECT person_npi FROM emp_cand)".format(p=p))
        seen = con.execute(" UNION ".join(parts)).df()
        con.unregister("emp_cand")
        billed = set(seen["n"].astype(str)) if len(seen) else set()
        cand = cand[~cand["person_npi"].isin(billed)].copy()
    return cand.sort_values("excl_date", ascending=False).reset_index(drop=True)


def _oid(key: str) -> str:
    return f"employer:{hashlib.md5(key.encode()).hexdigest()[:10]}"


def employer_prompts(cand: pd.DataFrame, per_prompt: int = 10) -> list[dict]:
    """The dispatch items (the dossier's schema: org_id / org_name / prompt + our rows)."""
    out: list[dict] = []
    if cand is None or not len(cand):
        return out
    c = cand.sort_values(["state", "excl_date"], ascending=[True, False]).reset_index(drop=True)
    for i in range(0, len(c), int(per_prompt)):
        chunk = c.iloc[i:i + int(per_prompt)]
        lines = []
        for r in chunk.itertuples():
            why = r.summary or r.fca_basis
            lines.append(f"[{r.person_npi}] {r.person_name}; {r.city + ', ' if r.city else ''}{r.state}; "
                         f"record dated {r.excl_date}; basis: {why[:160]}")
        key = ";".join(chunk["person_npi"].astype(str))
        out.append({"org_id": _oid(key), "org_name": f"employer resolution batch {i // int(per_prompt) + 1} "
                                                      f"({chunk['state'].iloc[0] or 'multi-state'})",
                    "person_npis": chunk["person_npi"].astype(str).tolist(),
                    "prompt": EMPLOYER_PROMPT.format(rows=" | ".join(lines))})
    return out


# --------------------------------------------------------------------------- #
# load
# --------------------------------------------------------------------------- #

def _col(df: pd.DataFrame, name: str) -> pd.Series:
    low = {str(c).strip().lower().replace(" ", "_"): c for c in df.columns}
    c = low.get(name)
    return df[c].fillna("").astype(str).str.strip() if c else pd.Series("", index=df.index)


def _overlaps(start: str, end: str, conduct_date: str) -> bool:
    """A stated employment window must contain the record date (give or take a year);
    a blank window is not a contradiction."""
    s, e = pd.to_datetime(start, errors="coerce"), pd.to_datetime(end, errors="coerce")
    d = pd.to_datetime(conduct_date, errors="coerce")
    if pd.isna(d) or (pd.isna(s) and pd.isna(e)):
        return True
    lo = (s - pd.DateOffset(years=1)) if not pd.isna(s) else None
    hi = (e + pd.DateOffset(years=1)) if not pd.isna(e) else None
    return (lo is None or d >= lo) and (hi is None or d <= hi)


def load_employer_rows(table: pd.DataFrame, cand: pd.DataFrame, provider_dim: pd.DataFrame | None,
                       min_confidence: str = "medium") -> tuple[pd.DataFrame, pd.DataFrame]:
    """An employer answer -> (research rows in the affiliated schema, quarantine rows)."""
    from src.enforcement.label_gates import name_agrees
    need = _CONF.get(str(min_confidence).lower(), 2)
    c = cand.set_index(cand["person_npi"].astype(str)) if cand is not None and len(cand) else pd.DataFrame()
    pdim = None
    if provider_dim is not None and len(provider_dim):
        pdim = provider_dim.copy()
        pdim["npi"] = pdim["npi"].astype(str)
        pdim = pdim.drop_duplicates("npi").set_index("npi")
    person = _col(table, "person_npi").str.replace(r"\D", "", regex=True)
    emp_npi = _col(table, "employer_npi").str.replace(r"\D", "", regex=True)
    emp_name = _col(table, "employer_name")
    conf = _col(table, "confidence").str.lower()
    url = _col(table, "evidence_url")
    start, end = _col(table, "employment_start"), _col(table, "employment_end")
    city = _col(table, "employer_city")
    ok_rows, bad_rows = [], []
    for i in range(len(table)):
        reason = ""
        pn, en = person.iloc[i], emp_npi.iloc[i]
        if not len(c) or pn not in c.index:
            reason = "person_npi is not one we sent"
        elif not en:
            reason = "no employer NPI (research_no_npi)"
        elif not is_valid_npi(en):
            reason = "employer NPI fails the Luhn check"
        elif _CONF.get(conf.iloc[i], 0) < need:
            reason = f"confidence {conf.iloc[i] or 'blank'} below {min_confidence}"
        elif en == pn:
            reason = "employer NPI is the person's own NPI"
        elif pdim is not None and en not in pdim.index:
            reason = "employer NPI not in NPPES (provider_dim)"
        elif pdim is not None and str(pdim.loc[en].get("entity_type", "")).replace(".0", "") != "2":
            reason = "employer NPI is an individual, not an organization"
        elif pdim is not None:
            nppes = str(pdim.loc[en].get("org_legal_name") or pdim.loc[en].get("provider_name") or "")
            ncity = str(pdim.loc[en].get("addr_city", pdim.loc[en].get("city", "")) or "")
            agree, why = name_agrees(emp_name.iloc[i], "org", nppes, ncity, city.iloc[i])
            if not agree:
                reason = f"NPPES name disagrees: {why}"
        if not reason and not _overlaps(start.iloc[i], end.iloc[i], str(c.loc[pn, "excl_date"]) if len(c) else ""):
            reason = "stated employment window does not contain the record date"
        row = {k: (table.iloc[i].get(k, "") if k in table.columns else "") for k in table.columns}
        if reason:
            bad_rows.append({**row, "drop_reason": reason})
            continue
        cr = c.loc[pn]
        ok_rows.append({"npi": en, "excluded_npi": pn, "excl_date": cr["excl_date"], "excl_type": cr["excl_type"],
                        "fca_tier": cr["fca_tier"], "fca_basis": cr["fca_basis"], "entity_name": cr["person_name"],
                        "dollars_rendered": float("nan"), "biller_dollars_window": float("nan"),
                        "share_of_biller": float("nan"), "months_rendered": float("nan"), "rank_for_individual": 1,
                        "basis": "affiliated_research", "kept": True, "drop_reason": "",
                        "employer_name": emp_name.iloc[i], "confidence": conf.iloc[i], "evidence_url": url.iloc[i],
                        "employment_start": start.iloc[i], "employment_end": end.iloc[i]})
    ok = pd.DataFrame(ok_rows, columns=RESEARCH_COLS)
    bad = pd.DataFrame(bad_rows)
    if len(ok):
        # the chain guard: an employer named for many individuals is a chain, listed never labelled
        n_per = ok.groupby("npi")["excluded_npi"].transform("nunique")
        chain = n_per > MAX_PER_EMPLOYER
        if chain.any():
            bad = pd.concat([bad, ok[chain].assign(drop_reason=f"chain: employer named for > {MAX_PER_EMPLOYER} individuals")],
                            ignore_index=True)
            ok = ok[~chain].reset_index(drop=True)
    return ok, bad


def to_exclusion_rows(research: pd.DataFrame) -> pd.DataFrame:
    """The research rows as exclusion-schema rows (the graph's glob makes them nodes)."""
    if research is None or not len(research):
        return pd.DataFrame(columns=EXCL_COLS)
    from src.attempt_2.clean_data import _normalize_name
    names = research["employer_name"].fillna("").astype(str)
    out = pd.DataFrame({
        "npi": research["npi"].astype(str),
        "entity_name": names,
        "name_key": _normalize_name(names).fillna("").astype(str),
        "excl_type": "affiliated:" + research["excl_type"].astype(str) + "|research_employer",
        "excl_date": research["excl_date"].astype(str),
        "reinstate_date": "",
        "currently_active": True,
    })
    return out.drop_duplicates(["npi", "excl_type", "excl_date"]).reset_index(drop=True)


def append_parquet(path: Path, rows: pd.DataFrame, keys: list[str]) -> int:
    path = Path(path)
    if rows is None or not len(rows):
        return 0
    if path.exists():
        old = pd.read_parquet(path)
        both = pd.concat([old, rows], ignore_index=True)
        for k in keys:
            both[k] = both[k].astype(str)
        both = both.drop_duplicates(keys, keep="first")
        n = len(both) - len(old)
    else:
        both, n = rows, len(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    both.to_parquet(path, index=False)
    return int(n)


def summary_line(cand: pd.DataFrame, prompts: list[dict]) -> str:
    by = cand["source"].value_counts().to_dict() if len(cand) else {}
    return (f"[employer_lane] {len(cand):,} convicted individuals with no billing or rendering row "
            f"({json.dumps(by)}) -> {len(prompts)} prompt(s)")
