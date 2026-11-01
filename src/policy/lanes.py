"""policy/lanes.py — four more research lanes on the state's own records (2026-11-01).

The policy library (registry.py) asks each state for its rules. These four ask for the
records beside the rules, each one unlocking a card or a label the model cannot reach:

  roster_finder   where each state publishes its LICENSED-FACILITY ROSTER (adult day,
                  residential / group homes, assisted living, opioid treatment programs,
                  clinical laboratories, home health, hospice), whether it carries the
                  licence number and the capacity, and how to download it. The per-diem
                  card exists only where a roster is loaded (6,087 numbers sit with none);
                  the answer is a download checklist (STATE_ROSTERS.md), never a label.
  fee_schedule    what the state pays per code and year for the theory families (definitive
                  vs presumptive drug tests, the per diems, the personal-care unit, the NEMT
                  leg, 90837 vs 90834): every damages line becomes the payer's own number.
  state_audits    the state auditor / comptroller / Medicaid inspector general AUDIT REPORTS
                  that name a provider and the rule it broke (the NJ comptroller's TrueTox
                  audit is the model). Loaded two ways: as case rows (jurisdiction
                  "<ST> audit", case_type state_audit; a row without a final outcome is
                  usable=0) and as policy rows (the rule the audit quoted, topic
                  audit_findings) so the library grows from enforcement itself.
  plan_policy     the MANAGED-CARE PLANS' own provider-manual rules for the two questions
                  the state task answers thinly (rendering / supervision, drug testing),
                  one task per state x plan x topic for the top plans in the ten largest
                  states; rows land in the policy library with program = the plan's name.

Every answer is gated (government or plan URL, a quotation, parseable numbers and dates)
and refused rows go to the lane's quarantine with the reason. Nothing here is a legal
conclusion; a rate, a roster or an audit finding is evidence for a human read.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pandas as pd

from src.policy.registry import (DEFAULT_STATES, POLICY_COLS, _col, _date, _num, _state_names, append_rows,
                                 government_domain, clean_url)

# --------------------------------------------------------------------------- roster finder

FACILITY_TYPES = {
    "adult_day": "adult day health / adult medical day care centers (the licensed daily census capacity)",
    "residential": "licensed residential treatment, group homes and community residences (licensed beds)",
    "assisted_living": "assisted living and residential care facilities (licensed beds or units)",
    "otp": "opioid treatment programs (certified capacity or patient limit)",
    "clinical_lab": "clinical laboratories (state licence or CLIA listing with the director)",
    "home_health": "home health and home care agencies (licence number, branch addresses)",
    "hospice": "hospice providers (licence number, inpatient beds)",
}
ROSTER_COLS = ["state", "facility_type", "roster_name", "agency", "url", "download_method", "format",
               "has_license_number", "has_capacity", "capacity_field", "has_address", "update_cadence",
               "confidence", "retrieved_at", "source_task", "note"]

ROSTER_PROMPT = (
    "Public-records research task about {state_name}. Find where the state publishes its licensed-facility "
    "roster for: {ftype_desc}. We need the DOWNLOADABLE list (CSV, Excel, a searchable database with an export, "
    "or an open-data portal dataset), not a one-at-a-time lookup. For each roster return a CSV row with exactly "
    "these columns: state ({state}), facility_type ({ftype}), roster_name, agency (the licensing agency), url "
    "(the page or file on the state's own domain), download_method (direct_csv / direct_xlsx / portal_export / "
    "search_export / pdf_only / lookup_only), format, has_license_number (yes/no), has_capacity (yes/no: a "
    "licensed-capacity, beds, slots or client-limit field), capacity_field (the column's name), has_address "
    "(yes/no), update_cadence (daily / monthly / quarterly / unknown), confidence (high / medium / low), note. "
    "If the only public form is a lookup page, say so (download_method lookup_only) and name the agency to "
    "request the list from under the state's public-records law. One row per roster; blank beats a guess."
)


def _oid(kind: str, key: str) -> str:
    return f"{kind}:{hashlib.md5(key.encode()).hexdigest()[:10]}"


def roster_prompts(states: list[str] | None = None, ftypes: list[str] | None = None) -> list[dict]:
    names = _state_names()
    out = []
    for st in (states or DEFAULT_STATES):
        for ft in (ftypes or list(FACILITY_TYPES)):
            out.append({"org_id": _oid("roster", f"{st}|{ft}"), "org_name": f"roster {st} {ft}", "state": st,
                        "facility_type": ft,
                        "prompt": ROSTER_PROMPT.format(state_name=names.get(st, st), state=st, ftype=ft,
                                                       ftype_desc=FACILITY_TYPES[ft])})
    return out


def load_roster_rows(table: pd.DataFrame, state: str, ftype: str, task_id: str = "",
                     retrieved_at: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    st = str(state).upper()
    retrieved_at = retrieved_at or pd.Timestamp.now("UTC").strftime("%Y-%m-%d")
    url, name = _col(table, "url").map(clean_url), _col(table, "roster_name")
    method = _col(table, "download_method").str.lower()
    ok, bad = [], []
    for i in range(len(table)):
        reason = ""
        if not url.iloc[i]:
            reason = "no url"
        elif not government_domain(url.iloc[i]):
            reason = "url is not on a government domain"
        elif not name.iloc[i]:
            reason = "no roster name"
        row = {k: (table.iloc[i].get(k, "") if k in table.columns else "") for k in table.columns}
        if reason:
            bad.append({**row, "drop_reason": reason})
            continue
        ok.append({"state": st, "facility_type": ftype, "roster_name": name.iloc[i][:200],
                   "agency": _col(table, "agency").iloc[i][:200], "url": url.iloc[i][:500],
                   "download_method": method.iloc[i] or "unknown", "format": _col(table, "format").iloc[i][:40],
                   "has_license_number": _col(table, "has_license_number").iloc[i].lower()[:5],
                   "has_capacity": _col(table, "has_capacity").iloc[i].lower()[:5],
                   "capacity_field": _col(table, "capacity_field").iloc[i][:80],
                   "has_address": _col(table, "has_address").iloc[i].lower()[:5],
                   "update_cadence": _col(table, "update_cadence").iloc[i][:30],
                   "confidence": _col(table, "confidence").iloc[i].lower()[:10], "retrieved_at": retrieved_at,
                   "source_task": task_id, "note": _col(table, "note").iloc[i][:400]})
    return pd.DataFrame(ok, columns=ROSTER_COLS), pd.DataFrame(bad)


def roster_path(data_root: Path) -> Path:
    return Path(data_root) / "processed" / "policy" / "state_rosters.parquet"


def roster_report(df: pd.DataFrame) -> str:
    L = ["# STATE LICENSED-FACILITY ROSTERS: the download checklist", "",
         "_Where each state publishes its licensed-facility list, from the roster_finder research lane. A roster "
         "with a licence number and a capacity field unlocks the per-diem occupancy card for that state "
         "(ingest_cms.licensed_capacity loads it; save under preclean/state_capacity/<ST>_<type>.csv). "
         "lookup_only = ask the agency for the list under the public-records law._", ""]
    if not len(df):
        return "\n".join(L + ["_no rows yet_", ""])
    d = df.copy()
    d["_score"] = (d["has_capacity"].eq("yes").astype(int) * 2 + d["has_license_number"].eq("yes").astype(int)
                   + d["download_method"].isin(["direct_csv", "direct_xlsx", "portal_export", "search_export"]).astype(int))
    d = d.sort_values(["_score", "state", "facility_type"], ascending=[False, True, True])
    L += ["| state | type | roster | capacity field | licence no. | download | url | note |", "|---|---|---|---|---|---|---|---|"]
    for r in d.itertuples():
        L.append(f"| {r.state} | {r.facility_type} | {r.roster_name} | {r.capacity_field or ('yes' if r.has_capacity == 'yes' else '-')} | "
                 f"{r.has_license_number} | {r.download_method} | {r.url} | {str(r.note)[:80]} |")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- fee schedule

FEE_FAMILIES = {
    "drug_testing": ("G0480", "G0481", "G0482", "G0483", "G0659", "80305", "80306", "80307"),
    "day_program": ("S5102", "S5100", "S5101", "H2012", "H2036", "T2016", "H0035", "S9480"),
    "personal_care": ("T1019", "T1020", "S5125", "S5126", "S5130", "S5131", "T1004", "T1005"),
    "nemt": ("T2001", "T2002", "T2003", "T2004", "T2005", "A0080", "A0100", "A0120", "A0130", "A0140"),
    "psychotherapy": ("90832", "90834", "90837", "90785", "99213", "99214", "99215", "99417"),
    "home_health": ("G0299", "G0300", "G0156", "T1021", "T1022", "S9122", "S9123", "S9124"),
}
FEE_COLS = ["state", "program", "hcpcs", "year", "rate", "unit", "modifier", "citation", "url", "effective_from",
            "confidence", "retrieved_at", "source_task", "note"]

FEE_PROMPT = (
    "Public-records research task about {state_name} Medicaid fee-for-service rates. For each of these HCPCS / CPT "
    "codes: {codes}, find the Medicaid fee-schedule rate for each year 2019 through 2025 from the state's own "
    "published fee schedule (the Medicaid agency's or fiscal agent's site). Return a CSV with exactly these "
    "columns: state ({state}), program (FFS, or the plan's name when a plan publishes its own rate), hcpcs, year, "
    "rate (dollars, numeric), unit (per test / per diem / per 15 minutes / per leg / per visit / per mile), "
    "modifier (blank unless the rate is modifier-specific), citation (the schedule's name and version), url (the "
    "schedule page or file on the state's or fiscal agent's domain), effective_from (YYYY-MM-DD when the "
    "schedule says), confidence (high / medium / low), note (manual pricing, By Report, bundled, not covered). "
    "One row per code per year; a code the state does not cover is a row with rate blank and note 'not covered'. "
    "Blank beats a guess."
)


def fee_prompts(states: list[str] | None = None, families: list[str] | None = None) -> list[dict]:
    names = _state_names()
    out = []
    for st in (states or DEFAULT_STATES):
        for fam in (families or list(FEE_FAMILIES)):
            codes = ", ".join(FEE_FAMILIES[fam])
            out.append({"org_id": _oid("fee", f"{st}|{fam}"), "org_name": f"fee schedule {st} {fam}", "state": st,
                        "family": fam, "prompt": FEE_PROMPT.format(state_name=names.get(st, st), state=st, codes=codes)})
    return out


def load_fee_rows(table: pd.DataFrame, state: str, task_id: str = "", retrieved_at: str | None = None
                  ) -> tuple[pd.DataFrame, pd.DataFrame]:
    st = str(state).upper()
    retrieved_at = retrieved_at or pd.Timestamp.now("UTC").strftime("%Y-%m-%d")
    hc, yr, rate, url = (_col(table, "hcpcs").str.upper().str.replace(r"\s", "", regex=True), _col(table, "year"),
                         _col(table, "rate"), _col(table, "url").map(clean_url))
    ok, bad = [], []
    for i in range(len(table)):
        reason = ""
        y = re.search(r"(20\d{2})", yr.iloc[i])
        r = _num(rate.iloc[i]) if rate.iloc[i] else float("nan")
        if not re.fullmatch(r"[A-Z0-9]{5}", hc.iloc[i] or ""):
            reason = "hcpcs is not a five-character code"
        elif not y:
            reason = "year does not parse"
        elif rate.iloc[i] and (r != r or r < 0):
            reason = "rate is not a number"
        elif not url.iloc[i]:
            reason = "no url"
        elif not government_domain(url.iloc[i]):
            reason = "url is not on a government, fiscal-agent or plan domain"
        elif _date(_col(table, "effective_from").iloc[i]) == "INVALID":
            reason = "effective date does not parse"
        row = {k: (table.iloc[i].get(k, "") if k in table.columns else "") for k in table.columns}
        if reason:
            bad.append({**row, "drop_reason": reason})
            continue
        ok.append({"state": st, "program": _col(table, "program").iloc[i] or "FFS", "hcpcs": hc.iloc[i],
                   "year": int(y.group(1)), "rate": (r if rate.iloc[i] else float("nan")), "unit": _col(table, "unit").iloc[i][:40],
                   "modifier": _col(table, "modifier").iloc[i][:10], "citation": _col(table, "citation").iloc[i][:300],
                   "url": url.iloc[i][:500], "effective_from": _date(_col(table, "effective_from").iloc[i]),
                   "confidence": _col(table, "confidence").iloc[i].lower()[:10], "retrieved_at": retrieved_at,
                   "source_task": task_id, "note": _col(table, "note").iloc[i][:300]})
    return pd.DataFrame(ok, columns=FEE_COLS), pd.DataFrame(bad)


def fee_path(data_root: Path) -> Path:
    return Path(data_root) / "processed" / "policy" / "fee_schedule.parquet"


def fee(df: pd.DataFrame, state: str, hcpcs: str, year: int, program: str = "FFS"):
    """The rate for a code in a state and year (the nearest earlier year when that year is
    absent); NaN when the library has none."""
    if df is None or not len(df):
        return float("nan")
    m = (df["state"].astype(str).str.upper() == str(state).upper()) & (df["hcpcs"] == str(hcpcs).upper())
    mp = m & (df["program"].astype(str).str.upper() == str(program).upper())
    sub = df[mp if mp.any() else m]
    sub = sub[pd.to_numeric(sub["rate"], errors="coerce").notna() & (pd.to_numeric(sub["year"], errors="coerce") <= int(year))]
    if not len(sub):
        return float("nan")
    return float(sub.sort_values("year", ascending=False).iloc[0]["rate"])


def fee_report(df: pd.DataFrame) -> str:
    L = ["# STATE FEE SCHEDULES: the theory codes", "",
         "_What each state pays per code and year for the theory families, from the fee_schedule research lane. "
         "A damages line in a packet is the payer's own number from this table, with its citation._", ""]
    if not len(df):
        return "\n".join(L + ["_no rows yet_", ""])
    d = df[pd.to_numeric(df["rate"], errors="coerce").notna()].copy()
    L += ["| state | program | code | year | rate | unit | citation |", "|---|---|---|--:|--:|---|---|"]
    for r in d.sort_values(["state", "hcpcs", "year"]).itertuples():
        L.append(f"| {r.state} | {r.program} | {r.hcpcs} | {r.year} | ${float(r.rate):,.2f} | {r.unit} | {str(r.citation)[:60]} |")
    # the ratio the lab theory turns on
    L += ["", "## Definitive over presumptive (G0483 / 80307), latest year on file", "", "| state | G0483 | 80307 | ratio |", "|---|--:|--:|--:|"]
    for st in sorted(d["state"].unique()):
        g, p = fee(d, st, "G0483", 2025), fee(d, st, "80307", 2025)
        if g == g and p == p and p > 0:
            L.append(f"| {st} | ${g:,.2f} | ${p:,.2f} | {g / p:.1f}x |")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- state audits

AUDIT_PROMPT = (
    "Public-records research task about {state_name}, calendar year {year}. Find every published AUDIT REPORT by "
    "the state auditor, state comptroller, the Medicaid inspector general / program-integrity unit, or the state "
    "Medicaid agency's audit office that examined a named Medicaid PROVIDER'S billing and found overpayments, "
    "disallowed claims or rule violations (not a review of the agency itself). Return a CSV with exactly these "
    "columns: audit_date (YYYY-MM-DD, the report date), agency, provider_name, provider_type, city, state "
    "({state}), npi (10 digits from the NPPES NPI Registry when a unique record fits, else blank), "
    "amount_disallowed (dollars), period_audited (e.g. 2019-2021), finding_summary (one or two sentences in the "
    "report's words), rule_cited (the billing rule the report applied, quoted), rule_citation (the manual section "
    "or code section), report_url (the report on the agency's own domain), outcome (repayment_ordered / "
    "contested / settled / referred_for_prosecution / final / pending / unknown), outcome_url. One row per "
    "provider per report. Blank beats a guess."
)
AUDIT_COLS = ["audit_date", "agency", "provider_name", "provider_type", "city", "state", "npi", "amount_disallowed",
              "period_audited", "finding_summary", "rule_cited", "rule_citation", "report_url", "outcome", "outcome_url"]
_FINAL = re.compile(r"repayment_ordered|settled|final|referred_for_prosecution|judgment|conviction|plea", re.I)


def audit_prompts(states: list[str] | None = None, years: list[int] | None = None) -> list[dict]:
    names = _state_names()
    out = []
    for st in (states or DEFAULT_STATES):
        for y in (years or list(range(2019, 2026))):
            out.append({"org_id": _oid("audit", f"{st}|{y}"), "org_name": f"state audits {st} {y}", "state": st, "year": y,
                        "prompt": AUDIT_PROMPT.format(state_name=names.get(st, st), state=st, year=y)})
    return out


def load_audit_rows(table: pd.DataFrame, state: str, task_id: str = "") -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """-> (case rows in the case-DB schema, policy rows in POLICY_COLS, quarantine)."""
    from src.attempt_2.clean_data import is_valid_npi
    from src.enforcement.case_db import CASE_COLUMNS, _SCHEME_KEYWORDS, _SECTOR_KEYWORDS, _classify, _classify_all, build_case_db
    from src.enforcement.label_gates import is_index_url
    from src.entity_graph.resolve_entities import norm_org_name
    st = str(state).upper()
    rows, pol, bad = [], [], []
    name, url, date = _col(table, "provider_name"), _col(table, "report_url").map(clean_url), _col(table, "audit_date")
    for i in range(len(table)):
        reason = ""
        if not name.iloc[i]:
            reason = "no provider name"
        elif not url.iloc[i]:
            reason = "no report url"
        elif not government_domain(url.iloc[i]):
            reason = "report url is not on a government domain"
        elif is_index_url(url.iloc[i]):
            reason = "an index page, not a report"
        elif _date(date.iloc[i]) in ("", "INVALID"):
            reason = "audit date missing or unparseable"
        row = {k: (table.iloc[i].get(k, "") if k in table.columns else "") for k in table.columns}
        if reason:
            bad.append({**row, "drop_reason": reason})
            continue
        outcome = _col(table, "outcome").iloc[i].lower()
        summ = _col(table, "finding_summary").iloc[i]
        npi = re.sub(r"\D", "", _col(table, "npi").iloc[i])
        key = norm_org_name(name.iloc[i]).upper().replace(" ", "_")
        text = f"{name.iloc[i]}. {outcome}. {summ}"
        rows.append({"case_id": f"{url.iloc[i]}#{key}", "announced_date": _date(date.iloc[i]), "defendant_name": name.iloc[i],
                     "sector": _classify(text, _SECTOR_KEYWORDS), "scheme": _classify_all(text, _SCHEME_KEYWORDS),
                     "amount_usd": _num(_col(table, "amount_disallowed").iloc[i]) if _col(table, "amount_disallowed").iloc[i] else float("nan"),
                     "qui_tam": pd.NA, "intervened": pd.NA, "jurisdiction": f"{st} audit ({_col(table, 'agency').iloc[i][:60]})",
                     "source_url": url.iloc[i], "summary": (f"{outcome}. " if outcome else "") + summ[:460],
                     "npi_in_source": npi if (re.fullmatch(r"\d{10}", npi) and is_valid_npi(npi)) else "",
                     "usable": 1 if _FINAL.search(outcome) else 0,
                     "qc_note": "" if _FINAL.search(outcome) else "audit finding without a final outcome (contested / pending / unknown)"})
        rule = _col(table, "rule_cited").iloc[i]
        if len(rule) >= 40:
            pol.append({"state": st, "program": "FFS", "topic": "audit_findings", "param": "rule", "value": "",
                        "value_num": float("nan"), "rule_text": rule[:2000], "citation": _col(table, "rule_citation").iloc[i][:300],
                        "url": url.iloc[i][:500], "effective_from": "", "effective_to": "",
                        "confidence": _col(table, "confidence").iloc[i].lower()[:10] or "medium",
                        "retrieved_at": pd.Timestamp.now("UTC").strftime("%Y-%m-%d"), "source_task": task_id,
                        "note": f"applied in the {_col(table, 'agency').iloc[i][:60]} audit of {name.iloc[i][:60]}"})
    cases = build_case_db(rows) if rows else pd.DataFrame(columns=CASE_COLUMNS)
    if len(cases):
        by = {r["case_id"]: r for r in rows}
        cases["case_type"] = "state_audit"
        cases["npi_in_source"] = cases["case_id"].map(lambda c: by.get(c, {}).get("npi_in_source", ""))
        cases["usable"] = cases["case_id"].map(lambda c: by.get(c, {}).get("usable", 0))
        cases["qc_note"] = cases["case_id"].map(lambda c: by.get(c, {}).get("qc_note", ""))
    return cases, pd.DataFrame(pol, columns=POLICY_COLS), pd.DataFrame(bad)


# --------------------------------------------------------------------------- plan policy

PLANS_BY_STATE = {
    "CA": ["L.A. Care Health Plan", "Health Net", "Molina Healthcare of California", "Anthem Blue Cross", "Inland Empire Health Plan", "CalOptima"],
    "NY": ["Fidelis Care", "Healthfirst", "MetroPlus Health", "UnitedHealthcare Community Plan of New York", "Wellpoint (Amerigroup) New York", "Molina Healthcare of New York"],
    "TX": ["Superior HealthPlan", "Wellpoint (Amerigroup) Texas", "Molina Healthcare of Texas", "Texas Children's Health Plan", "UnitedHealthcare Community Plan of Texas", "Driscoll Health Plan"],
    "FL": ["Sunshine Health", "Humana Healthy Horizons in Florida", "Simply Healthcare", "Molina Healthcare of Florida", "UnitedHealthcare Community Plan of Florida", "Aetna Better Health of Florida"],
    "PA": ["UPMC for You", "Geisinger Health Plan", "Keystone First", "AmeriHealth Caritas Pennsylvania", "Health Partners Plans", "Highmark Wholecare"],
    "IL": ["Meridian Health Plan of Illinois", "Blue Cross Community Health Plans", "Aetna Better Health of Illinois", "Molina Healthcare of Illinois", "CountyCare"],
    "OH": ["CareSource", "Molina Healthcare of Ohio", "Buckeye Health Plan", "UnitedHealthcare Community Plan of Ohio", "Anthem Blue Cross and Blue Shield Ohio", "Humana Healthy Horizons in Ohio", "AmeriHealth Caritas Ohio"],
    "MI": ["Meridian Health Plan of Michigan", "Molina Healthcare of Michigan", "Priority Health", "McLaren Health Plan", "UnitedHealthcare Community Plan of Michigan", "Blue Cross Complete of Michigan"],
    "NJ": ["Horizon NJ Health", "Aetna Better Health of New Jersey", "Wellpoint (Amerigroup) New Jersey", "UnitedHealthcare Community Plan of New Jersey", "Fidelis Care New Jersey (Wellcare)"],
    "GA": ["Peach State Health Plan", "Amerigroup Community Care Georgia", "CareSource Georgia"],
    "NC": ["WellCare of North Carolina", "Healthy Blue North Carolina", "UnitedHealthcare Community Plan of North Carolina", "AmeriHealth Caritas North Carolina", "Carolina Complete Health"],
}
PLAN_TOPICS = ("rendering_supervision", "drug_testing")

PLAN_PROMPT = (
    "Public-records research task about the Medicaid managed-care plan {plan} in {state_name}. TOPIC: {title}. In "
    "the PLAN'S OWN provider manual, billing guidelines, clinical / payment policies and provider bulletins (on "
    "the plan's domain), find the current rule and every change since 2018: {question} Return a CSV with exactly "
    "these columns: state ({state}), program ({plan}), topic ({topic}), param (one of: {params}; or 'rule' for a "
    "row that carries the rule text alone), value (a number, yes / no, or a short enum), rule_text (the governing "
    "sentence or paragraph QUOTED from the plan's document), citation (the manual section or policy number), url "
    "(the document on the plan's own domain), effective_from (YYYY-MM-DD or YYYY-MM when stated), effective_to "
    "(blank when current), confidence (high / medium / low), note (one sentence: whether the plan follows the "
    "state fee-for-service rule or departs from it). Blank beats a guess; a quotation beats a paraphrase."
)


def plan_prompts(states: list[str] | None = None, topics: tuple[str, ...] = PLAN_TOPICS) -> list[dict]:
    from src.policy.registry import TOPICS
    names = _state_names()
    out = []
    for st in (states or list(PLANS_BY_STATE)):
        for plan in PLANS_BY_STATE.get(st, []):
            for tk in topics:
                t = TOPICS[tk]
                out.append({"org_id": _oid("plan", f"{st}|{plan}|{tk}"), "org_name": f"plan policy {st} {plan} {tk}",
                            "state": st, "topic": tk, "plan": plan,
                            "prompt": PLAN_PROMPT.format(plan=plan, state_name=names.get(st, st), state=st, title=t.title,
                                                         question=t.question, topic=tk, params=", ".join(t.params))})
    return out


def load_plan_rows(table: pd.DataFrame, state: str, topic: str, plan: str, task_id: str = "") -> tuple[pd.DataFrame, pd.DataFrame]:
    """The policy loader with the plan forced into `program` (an answer that names the
    state FFS rule instead is kept, program FFS, since the plan says it follows it)."""
    from src.policy.registry import load_policy_rows
    t = table.copy()
    if "program" not in {str(c).lower() for c in t.columns}:
        t["program"] = plan
    ok, bad = load_policy_rows(t, state, topic, task_id=task_id)
    if len(ok):
        prog = ok["program"].fillna("").astype(str).str.strip()
        is_ffs = prog.str.upper().isin(["FFS", "FEE-FOR-SERVICE", "STATE", "MEDICAID FFS"])
        ok["program"] = plan
        ok.loc[is_ffs, "program"] = "FFS"
    return ok, bad


__all__ = ["FACILITY_TYPES", "ROSTER_COLS", "roster_prompts", "load_roster_rows", "roster_path", "roster_report",
           "FEE_FAMILIES", "FEE_COLS", "fee_prompts", "load_fee_rows", "fee_path", "fee", "fee_report",
           "AUDIT_COLS", "audit_prompts", "load_audit_rows",
           "PLANS_BY_STATE", "PLAN_TOPICS", "plan_prompts", "load_plan_rows", "append_rows"]
