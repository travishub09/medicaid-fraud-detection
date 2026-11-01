"""policy/registry.py — the state Medicaid billing-policy library (2026-11-01).

THE RULE IS THE RULER. Every verdict that moved in October turned on a state's own billing
rule: New York pays a PA's work under the supervising physician at the physician fee (so
"many clinicians under one NPI" has no rate damages there), Illinois wants a patient-
specific order with no screen-first rule, Oregon covers definitive drug testing only on a
discrepant screen and about twelve dates a year, New Jersey's adult-day per diem needs five
service hours and allows a declared second shift, New Mexico bundles the in-clinic dipstick.
Each was learned by one research prompt and kept as prose in a memo. This module makes the
library systematic: one row per state x program x topic with the rule in the state's words,
the citation and URL on the state's own domain, the effective dates, and the NUMERIC
PARAMETERS a screen or a scheme can read (pays the PA under the physician: yes / no;
definitive tests covered a year: 12; requires a presumptive first: yes; per-diem minimum
hours: 5; NEMT paid per leg / seat / mile; EVV required since ...).

Three consumers: the theory screens read the parameters so a threshold is the state's own
number (`policy_param`); the kill prompts carry the rule text so the research agent argues
against our number instead of discovering the rule for us (`policy_context`); the schemes get
a RULE-EXCESS metric class (tests billed above what the state covers, days billed above the
licence, PA visits billed where the state does not allow it: the payer's own policy is the
most defensible comparison we can write).

Rows come from the `policy` research lane (manus_lanes): one task per (state, topic), the
answer CSV gated here (`load_policy_rows`: the URL on a government domain, a quotation of at
least 40 characters, a parseable effective date when given, the parameter keys known to the
topic), kept rows appended to processed/policy/state_policy.parquet, refused rows to the
lane's quarantine with the reason. Confidence and retrieval date ride on every row; a rule is
EVIDENCE for a human read, never a legal conclusion.

    python -m src.policy.registry --data-root D --state NJ --topic perdiem_day_program
    python -m src.policy.registry --data-root D --report
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

POLICY_COLS = ["state", "program", "topic", "param", "value", "value_num", "rule_text", "citation", "url",
               "effective_from", "effective_to", "confidence", "retrieved_at", "source_task", "note"]
MIN_QUOTE_CHARS = 40
_CONF = {"high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True)
class Topic:
    key: str
    title: str
    question: str                       # what the agent must find, in plain words
    params: tuple[str, ...]             # the parameter keys the answer may carry (numeric / yes-no / enum)
    consumers: tuple[str, ...] = ()     # which screens / schemes read it


TOPICS: dict[str, Topic] = {
    "rendering_supervision": Topic(
        "rendering_supervision", "Rendering and supervising provider rules (NP / PA under a physician)",
        "Whether a nurse practitioner's or physician assistant's service may be BILLED under the supervising "
        "physician's NPI, at what fee (the physician fee, a percentage, the NP/PA fee), whether the rendering "
        "clinician must be named on the claim, the supervision ratio (PAs per physician), and whether an "
        "unlicensed or unenrolled renderer is payable at all; fee-for-service AND the managed-care plans' rules.",
        ("pa_billed_under_physician", "np_billed_under_physician", "pa_fee_pct_of_physician", "np_fee_pct_of_physician",
         "rendering_npi_required_on_claim", "max_pas_per_physician", "unenrolled_renderer_payable"),
        ("capacity", "rendering_identity", "impossible_day")),
    "drug_testing": Topic(
        "drug_testing", "Drug-testing coverage and order path (presumptive vs definitive)",
        "Whether Medicaid pays definitive (LC-MS/MS, G0480-G0483) drug testing only after a presumptive screen or "
        "on a specific clinical indication, the covered number of definitive tests per patient per year, whether "
        "standing orders or laboratory reflex are allowed, the per-test or per-day limits, prior authorization, "
        "and the opioid-treatment-program testing schedule the state requires.",
        ("requires_presumptive_first", "max_definitive_per_year", "max_presumptive_per_year", "standing_orders_allowed",
         "lab_reflex_allowed", "definitive_prior_auth", "otp_tests_per_year"),
        ("definitive_reflex", "lab_testing")),
    "perdiem_day_program": Topic(
        "perdiem_day_program", "Adult day / residential per-diem rules",
        "The daily-rate conditions for adult medical day care and residential programs: minimum service hours "
        "for a per diem, maximum days per week per beneficiary, whether a second shift or session may be declared "
        "and what it requires, whether the daily census may exceed licensed capacity, and any managed-care cap "
        "on reimbursable daily attendance.",
        ("perdiem_min_hours", "max_days_per_week", "second_shift_allowed", "census_may_exceed_license",
         "mco_daily_cap_rule"),
        ("perdiem_occupancy",)),
    "personal_care": Topic(
        "personal_care", "Personal care / attendant services units and documentation",
        "The billing unit (15 minutes, hour, day), the daily and weekly hour caps per beneficiary and per aide, "
        "whether electronic visit verification is required and since when, whether the aide must be identified "
        "on the claim, and whether a legally responsible relative may be paid.",
        ("unit_minutes", "max_hours_per_day_per_beneficiary", "max_hours_per_day_per_aide", "evv_required_since",
         "aide_identified_on_claim", "relative_caregiver_payable"),
        ("attendant_hours", "evv_personal_care")),
    "nemt": Topic(
        "nemt", "Non-emergency medical transportation payment units",
        "Whether NEMT is paid per leg, per seat, per mile or per trip, whether a broker holds the contract and "
        "providers bill the broker, the vehicle and driver registration a provider must hold, and the daily or "
        "per-beneficiary limits.",
        ("nemt_payment_unit", "broker_model", "vehicle_registration_required", "max_legs_per_day"),
        ("nemt_fleet", "nemt_fraud")),
    "home_health": Topic(
        "home_health", "Home health visit rules",
        "The visit codes and units, the plan-of-care and face-to-face requirements, who may order home health "
        "(physician, NP, PA), the visit caps, and whether aide visits need a nurse supervisory visit.",
        ("np_may_order", "pa_may_order", "max_visits_per_week", "supervisory_visit_every_days"),
        ("home_health_intensity",)),
    "lab_panels": Topic(
        "lab_panels", "Laboratory panel and unbundling rules",
        "Whether component tests of a defined panel must be billed as the panel, the state's panel definitions, "
        "venipuncture payment rules, and any per-day limits on laboratory lines.",
        ("components_must_bill_as_panel", "venipuncture_paid_separately", "max_lab_lines_per_day"),
        ("lab_unbundling",)),
    "behavioral_health": Topic(
        "behavioral_health", "Community behavioral-health unit and per-diem rules",
        "The units for psychosocial rehabilitation, day treatment, crisis and case-management codes (H-codes), "
        "the daily and weekly caps, the staff-to-client ratios, and whether group services must name the group size.",
        ("unit_minutes", "max_units_per_day", "staff_client_ratio", "group_size_required"),
        ("behavioral_health",)),
    "psychotherapy": Topic(
        "psychotherapy", "Psychotherapy session rules",
        "Whether 60-minute psychotherapy (90837) needs documentation beyond 45 minutes, any prior authorization or "
        "frequency limit, and whether interactive complexity (90785) is payable and when.",
        ("psy_60_requires_justification", "max_sessions_per_week", "interactive_complexity_payable"),
        ("session_inflation",)),
    "telehealth": Topic(
        "telehealth", "Telehealth coverage",
        "Which services may be delivered by telehealth, audio-only coverage, originating-site rules, and the "
        "modifier and place-of-service required; the dates each rule took effect and ended.",
        ("audio_only_covered", "pos_modifier_required"),
        ()),
    "dme": Topic(
        "dme", "Durable medical equipment ordering and rental rules",
        "Prior authorization, the ordering-provider requirement, rental caps and conversion to purchase, and "
        "face-to-face requirements for the main DME families.",
        ("prior_auth_required", "rental_cap_months", "face_to_face_required"),
        ("dme_mailbox", "dme_ring")),
    "audit_findings": Topic(
        "audit_findings", "Rules applied in state audit reports (filled by the state_audits lane)",
        "The billing rule a state audit report applied to a named provider, quoted from the report.",
        (), ()),
    "hospice": Topic(
        "hospice", "Hospice election and nursing-facility room-and-board rules",
        "The election and certification requirements, the room-and-board pass-through for nursing-facility "
        "residents, the level-of-care rules, and length-of-stay review triggers.",
        ("rb_passthrough_pct", "los_review_days"),
        ("hospice_ineligibility",)),
}

DEFAULT_STATES = ["CA", "NY", "TX", "FL", "PA", "IL", "OH", "MI", "NJ", "GA", "NC", "AL", "MS", "TN", "SC", "LA", "KY"]

POLICY_PROMPT = (
    "Public-records research task about {state_name} Medicaid billing policy. TOPIC: {title}. Find, in the state's "
    "own documents (the Medicaid provider manual, fee schedule, provider bulletins, the state administrative code, "
    "managed-care plan provider manuals, the state plan), the current rule and every change since 2018: "
    "{question} Use only public sources on the state's or the plans' own websites. Return a CSV with exactly "
    "these columns: state, program (FFS or the managed-care plan's name), topic (copy ours: {topic}), param (one "
    "of: {params}; or 'rule' for a row that carries the rule text alone), value (the parameter's value: a number, "
    "yes / no, or a short enum such as per_leg / per_seat / per_mile), rule_text (the governing sentence or "
    "paragraph QUOTED from the source, at least one full sentence), citation (the manual section, bulletin "
    "number or code section), url (the page or PDF on the state's or the plan's own domain), effective_from "
    "(YYYY-MM-DD or YYYY-MM when the source says), effective_to (blank when current), confidence (high / medium "
    "/ low), note (one sentence on ambiguity). One row per parameter per program; a parameter the sources do not "
    "settle is a row with value blank and the note saying so. Blank beats a guess; a quotation beats a paraphrase."
)

# the state's own domain (.gov / .us), or its Medicaid fiscal agent's (njmmis, emedny, tmhp,
# medi-cal, the *mmis portals, Gainwell / Conduent / DXC operate most of them)
_GOV_HOST = re.compile(r"\.(gov|us|state\.[a-z]{2}\.us)$|\.(gov|us)/|(medicaid|healthcare|dhs|dhhs|hhs|dss|dmas|ahca|hfs|"
                       r"mass\.gov|emedny|medi-cal|ohca|dch|dhcs|hhsc|dmahs|omig|ahcccs|tmhp|emedicaid|mmis|gainwell|"
                       r"conduent|dxc|forwardhealth|mihealth|ctdssmap|kymmis|mmisportal|"
                       # 2026-11-01: official codes and state portals on .com / .org hosts
                       r"govt\.westlaw|flrules|myflorida|myflfamilies|wadsworth|casetext\.com/regulation|"
                       r"regulations\.justia|law\.cornell|ecfr)", re.I)
_PLAN_HOST = re.compile(r"(molina|centene|wellcare|amerigroup|anthem|uhc|unitedhealth|aetna|humana|caresource|"
                        r"horizon|fidelis|healthfirst|metroplus|emblem|meridian|bcbs|bluecross|sunshine|simply|"
                        r"superior|amerihealth|upmc|geisinger|carefirst|neighborhood|tufts|priority|mdwise|"
                        r"passport|peachstate|magnolia|absolute|ambetter|"
                        # the plans the plan_policy lane asks about (PLANS_BY_STATE) answered from
                        # their own provider sites (2026-11-01: 234 rows refused)
                        r"healthnet|texaschildrens|lacare|driscoll|iehp|wellpoint|parkland|cookchp|elpaso|"
                        r"hpsm|hfproviders|healthfirst|molina|kaiser|blueshield|calopt|ccah|partnership|"
                        r"alameda|sfhp|goldcoast|kernfamily|scfhp|communityhealthchoice|amerigroup|"
                        r"superiorhealth|dellchildrens|firstcare|sendero|bcbstx|sunshinehealth|"
                        r"humana|aetnabetterhealth|simplyhealthcare|vivida|mcna|uhccommunityplan)", re.I)


def _state_names() -> dict[str, str]:
    try:
        from src.enforcement.defendant_resolver import _STATE_NAME
        return dict(_STATE_NAME)
    except Exception:          # pragma: no cover
        return {}


def _oid(key: str) -> str:
    return f"policy:{hashlib.md5(key.encode()).hexdigest()[:10]}"


def policy_prompts(states: list[str] | None = None, topics: list[str] | None = None) -> list[dict]:
    """One dispatch item per (state, topic) in the dossier's schema."""
    names = _state_names()
    out = []
    for st in (states or DEFAULT_STATES):
        for tk in (topics or [k for k, t in TOPICS.items() if t.params]):   # a topic without parameters is filled by another lane
            t = TOPICS[tk]
            out.append({"org_id": _oid(f"{st}|{tk}"), "org_name": f"policy {st} {tk}", "state": st, "topic": tk,
                        "prompt": POLICY_PROMPT.format(state_name=names.get(st, st), title=t.title, question=t.question,
                                                       topic=tk, params=", ".join(t.params))})
    return out


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    low = {str(c).strip().lower().replace(" ", "_"): c for c in df.columns}
    c = low.get(name)
    return df[c].fillna("").astype(str).str.strip() if c else pd.Series("", index=df.index)


def clean_url(value: str) -> str:
    """The URL as the agent wrote it -> a bare URL. 2026-11-01: 294 + 234 rows carried a
    markdown link ("[www.healthnet.com](https://www.healthnet.com/...)"), which urlparse
    reads as no host at all, so the domain gate refused every one of them."""
    s = str(value or "").strip()
    m = re.search(r"https?://[^\s)\]>\"']+", s)
    if m:
        return m.group(0).rstrip(".,;")
    m = re.search(r"\[([^\]]+)\]", s)                  # "[www.x.gov]" with no href
    if m:
        s = m.group(1).strip()
    if s and not s.lower().startswith("http") and re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", s, re.I):
        return "https://" + s
    return s


def government_domain(url: str) -> bool:
    try:
        url = clean_url(url)
        host = urlparse(str(url or "").strip()).netloc.lower()
    except Exception:
        return False
    if not host:
        return False
    return bool(_GOV_HOST.search(host) or _GOV_HOST.search(str(url).lower()) or _PLAN_HOST.search(host))


def norm_state(value: str) -> str:
    """A state as the agent wrote it -> the two-letter code the gate compares. 2026-11-01:
    the first policy cycle refused 1,482 rows because the agent wrote CALIFORNIA and the
    gate compared it to CA."""
    s = str(value or "").strip().upper().replace(".", "")
    if len(s) == 2 or not s:
        return s
    inv = {name.upper(): code for code, name in _state_names().items()}
    return inv.get(s, s)


def _num(value: str):
    s = str(value or "").strip().lower()
    if s in ("yes", "y", "true", "allowed", "required"):
        return 1.0
    if s in ("no", "n", "false", "not allowed", "barred", "prohibited"):
        return 0.0
    nums = re.findall(r"-?\d+(?:\.\d+)?", s.replace(",", ""))
    # "1:4_recommended", "45_25_15", "5_onsite_10_offsite": several numbers = an ambiguous
    # value; the screens never read the first one as THE parameter (the text stays)
    return float(nums[0]) if len(nums) == 1 else float("nan")


def _date(s: str) -> str:
    s = str(s or "").strip()
    if not s:
        return ""
    d = pd.to_datetime(s, errors="coerce")
    return d.strftime("%Y-%m-%d") if pd.notna(d) else "INVALID"


def load_policy_rows(table: pd.DataFrame, state: str, topic: str, task_id: str = "",
                     retrieved_at: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """An answer table -> (kept rows in POLICY_COLS, quarantine rows with the reason)."""
    st = str(state).upper()
    t = TOPICS.get(topic)
    allowed = set(t.params) | {"rule"} if t else {"rule"}
    retrieved_at = retrieved_at or pd.Timestamp.now('UTC').strftime("%Y-%m-%d")
    rs, rt = _col(table, "state").map(norm_state), _col(table, "topic")
    param, value, text = _col(table, "param").str.lower().str.replace(" ", "_"), _col(table, "value"), _col(table, "rule_text")
    cit, url, conf = _col(table, "citation"), _col(table, "url").map(clean_url), _col(table, "confidence").str.lower()
    ef, et, note, prog = _col(table, "effective_from"), _col(table, "effective_to"), _col(table, "note"), _col(table, "program")
    ok, bad = [], []
    for i in range(len(table)):
        reason = ""
        if rs.iloc[i] and rs.iloc[i] != st:
            reason = f"state {rs.iloc[i]} is not the state asked ({st})"
        elif rt.iloc[i] and rt.iloc[i] != topic:
            reason = f"topic {rt.iloc[i]} is not the topic asked ({topic})"
        elif param.iloc[i] and param.iloc[i] not in allowed:
            reason = f"param {param.iloc[i]} is not one of the topic's keys"
        elif len(text.iloc[i]) < MIN_QUOTE_CHARS:
            reason = f"rule_text under {MIN_QUOTE_CHARS} characters (a quotation is required)"
        elif not url.iloc[i]:
            reason = "no url"
        elif not government_domain(url.iloc[i]):
            reason = "url is not on a government or plan domain"
        elif _date(ef.iloc[i]) == "INVALID" or _date(et.iloc[i]) == "INVALID":
            reason = "effective date does not parse"
        elif _CONF.get(conf.iloc[i], 0) < 1:
            reason = "confidence blank"
        row = {k: (table.iloc[i].get(k, "") if k in table.columns else "") for k in table.columns}
        if reason:
            bad.append({**row, "drop_reason": reason})
            continue
        p = param.iloc[i] or "rule"
        ok.append({"state": st, "program": prog.iloc[i] or "FFS", "topic": topic, "param": p,
                   "value": value.iloc[i], "value_num": (_num(value.iloc[i]) if p != "rule" and value.iloc[i] else float("nan")),
                   "rule_text": text.iloc[i][:2000], "citation": cit.iloc[i][:300], "url": url.iloc[i][:500],
                   "effective_from": _date(ef.iloc[i]), "effective_to": _date(et.iloc[i]), "confidence": conf.iloc[i],
                   "retrieved_at": retrieved_at, "source_task": task_id, "note": note.iloc[i][:400]})
    return pd.DataFrame(ok, columns=POLICY_COLS), pd.DataFrame(bad)


# --------------------------------------------------------------------------- store + readers

def policy_path(data_root: Path) -> Path:
    return Path(data_root) / "processed" / "policy" / "state_policy.parquet"


def append_rows(path: Path, rows: pd.DataFrame) -> int:
    if rows is None or not len(rows):
        return 0
    path = Path(path)
    if path.exists():
        old = pd.read_parquet(path)
        both = pd.concat([old, rows], ignore_index=True)
        n_old = len(old)
    else:
        both, n_old = rows, 0
    both = both.drop_duplicates(["state", "program", "topic", "param", "url", "rule_text"], keep="first")
    path.parent.mkdir(parents=True, exist_ok=True)
    both.to_parquet(path, index=False)
    return int(len(both) - n_old)


_CACHE: dict[str, pd.DataFrame] = {}


def load_policy(data_root: Path | None = None, path: Path | None = None) -> pd.DataFrame:
    p = Path(path) if path else policy_path(Path(data_root))
    k = str(p)
    if k not in _CACHE:
        _CACHE[k] = pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=POLICY_COLS)
    return _CACHE[k]


def policy_param(df: pd.DataFrame, state: str, topic: str, param: str, program: str = "FFS",
                 asof: str | None = None):
    """The numeric parameter for a state / topic / program (the newest effective row on or
    before ``asof``, highest confidence first); NaN when the library has no row."""
    if df is None or not len(df):
        return float("nan")
    m = (df["state"].astype(str).str.upper() == str(state).upper()) & (df["topic"] == topic) & (df["param"] == param)
    if program:
        mp = m & (df["program"].astype(str).str.upper() == str(program).upper())
        m = mp if mp.any() else m
    sub = df[m].copy()
    if asof:
        ef = pd.to_datetime(sub["effective_from"], errors="coerce")
        sub = sub[ef.isna() | (ef <= pd.Timestamp(asof))]
    sub = sub[pd.to_numeric(sub["value_num"], errors="coerce").notna()]
    if not len(sub):
        return float("nan")
    sub["_c"] = sub["confidence"].map(_CONF).fillna(0)
    sub["_d"] = pd.to_datetime(sub["effective_from"], errors="coerce").fillna(pd.Timestamp("1900-01-01"))
    return float(sub.sort_values(["_c", "_d"], ascending=[False, False]).iloc[0]["value_num"])


def rule_text(df: pd.DataFrame, state: str, topic: str, max_rows: int = 4) -> list[dict]:
    """The rule rows (text + citation + url) for a state / topic, highest confidence first."""
    if df is None or not len(df):
        return []
    m = (df["state"].astype(str).str.upper() == str(state).upper()) & (df["topic"] == topic)
    sub = df[m].copy()
    if not len(sub):
        return []
    sub["_c"] = sub["confidence"].map(_CONF).fillna(0)
    sub = sub.sort_values(["_c", "effective_from"], ascending=[False, False]).drop_duplicates("rule_text")
    return sub.head(max_rows)[["program", "param", "value", "rule_text", "citation", "url", "effective_from"]].to_dict("records")


def policy_context(df: pd.DataFrame, state: str, topics: list[str]) -> str:
    """The paragraph a kill prompt carries: the state's own rule for each topic, with the
    citation, so the agent argues against our number instead of finding the rule for us."""
    L = []
    for tk in topics:
        rows = rule_text(df, state, tk)
        if not rows:
            L.append(f"{state} {TOPICS[tk].title if tk in TOPICS else tk}: no rule on file (find it and cite it).")
            continue
        for r in rows:
            prog = f" ({r['program']})" if r.get("program") and str(r["program"]).upper() != "FFS" else ""
            L.append(f"{state} {TOPICS[tk].title if tk in TOPICS else tk}{prog}: \"{r['rule_text']}\" "
                     f"[{r['citation']}; {r['url']}{'; effective ' + r['effective_from'] if r.get('effective_from') else ''}]")
    return "STATE RULES ON FILE: " + " ".join(L)


def report(df: pd.DataFrame) -> str:
    L = ["# STATE POLICY LIBRARY", "",
         "_One row per state x program x topic x parameter from the policy research lane, gated on a government "
         "or plan URL, a quotation and a parseable date. A rule here is evidence for a human read, never a legal "
         "conclusion; the screens read the numeric parameters, the kill prompts carry the text._", ""]
    if not len(df):
        return "\n".join(L + ["_no rows yet (run the policy lane)_", ""])
    cov = df.groupby(["state", "topic"]).size().unstack(fill_value=0)
    L += ["## Coverage (rows per state x topic)", "", "| state | " + " | ".join(cov.columns) + " |",
          "|---|" + "---|" * len(cov.columns)]
    for st, r in cov.iterrows():
        L.append(f"| {st} | " + " | ".join(str(int(v)) for v in r.values) + " |")
    L += ["", "## Parameters with a numeric value", "", "| state | program | topic | param | value | effective | confidence | citation |",
          "|---|---|---|---|---|---|---|---|"]
    num = df[pd.to_numeric(df["value_num"], errors="coerce").notna()].sort_values(["topic", "state", "param"])
    for r in num.itertuples():
        L.append(f"| {r.state} | {r.program} | {r.topic} | {r.param} | {r.value} | {r.effective_from or ''} | {r.confidence} | {str(r.citation)[:60]} |")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--state", default=None)
    ap.add_argument("--topic", default=None, help="comma list; default every topic")
    ap.add_argument("--report", action="store_true", help="write processed/policy/STATE_POLICY.md")
    ap.add_argument("--topics", action="store_true", help="list the topics and their parameter keys")
    a = ap.parse_args()
    if a.topics:
        for t in TOPICS.values():
            print(f"{t.key}: {t.title}\n    params: {', '.join(t.params)}\n    consumers: {', '.join(t.consumers) or '-'}")
        return
    df = load_policy(Path(a.data_root))
    if a.report:
        out = policy_path(Path(a.data_root)).with_name("STATE_POLICY.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report(df), encoding="utf-8")
        print(f"[policy] {len(df)} rows -> {out}")
    if a.state:
        topics = [t.strip() for t in a.topic.split(",")] if a.topic else list(TOPICS)
        print(policy_context(df, a.state.upper(), topics))


if __name__ == "__main__":
    main()
