"""
defendant_resolver.py — DOJ case defendants (people AND businesses) → NPIs.

The DOJ label line is empty by construction: press releases never print NPIs, and
the case→org name join only lands when a defendant's name equals an ENROLLED
organization's name. Cases against individuals ("John Smith, 45, of Miami,
Florida") and against businesses under a trade name never resolve, so they never
become labels. This module closes that gap deterministically, with no research
spend:

  1. ``extract_defendants``  pulls every "<name>, of <city>, <state>" and
     "<name>, a <city>, <state>-based ..." mention out of the case summary, plus
     the primary defendant with its state taken from the district; people and
     businesses are told apart by shape (honorific / person-suffix / two to four
     capitalised tokens vs. legal-suffix / business vocabulary).
  2. ``resolve_defendants`` matches each mention against the NPPES provider table
     the platform already holds — people on a FIRST LAST key within the state,
     businesses on the graph's own ``norm_org_name`` key within the state — and
     accepts ONLY a unique hit (city narrows a multi-hit; a generic name never
     matches at all). Several hits stay AMBIGUOUS and go to the research
     residual list; none stays UNMATCHED.
  3. The resolved rows feed ``case_labels.build_case_labels`` as a new label
     basis ``resolved_defendant`` — ranked below an NPI printed in a source
     (``named_npi``) and above the org-billing broadcast — so a reviewer can
     always tell how a case reached an NPI.

Outputs (``--out`` parquet + siblings): ``case_defendant_npis.parquet`` (every
mention with its status), ``case_defendants_unresolved.csv`` (the residual list
for Manus / hand research, ambiguous first, ordered by case dollars), and
``DEFENDANT_RESOLUTION.md``.

Leads for counsel review, never accusations: every row here is an already
prosecuted matter used as a TRAINING / VALIDATION positive. Prosecuted providers
are never leads (public disclosure, first-to-file).

  python -m src.enforcement.defendant_resolver --case-db <doj_cases.csv> \
      --provider-dim processed/provider_dim.parquet --out processed/case_defendant_npis.parquet
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import _normalize_name, distinctive_name_key
from src.entity_graph.resolve_entities import norm_org_name
from src.model_a.state_fca import _NAME_TO_ABBR

# ---- district → state --------------------------------------------------------------
_DISTRICT_STATE = {
    "ala": "AL", "alaska": "AK", "ariz": "AZ", "ark": "AR", "cal": "CA", "colo": "CO",
    "conn": "CT", "del": "DE", "d.c": "DC", "fla": "FL", "ga": "GA", "haw": "HI",
    "idaho": "ID", "ill": "IL", "ind": "IN", "iowa": "IA", "kan": "KS", "ky": "KY",
    "la": "LA", "me": "ME", "md": "MD", "mass": "MA", "mich": "MI", "minn": "MN",
    "miss": "MS", "mo": "MO", "mont": "MT", "neb": "NE", "nev": "NV", "n.h": "NH",
    "n.j": "NJ", "n.m": "NM", "n.y": "NY", "n.c": "NC", "n.d": "ND", "ohio": "OH",
    "okla": "OK", "or": "OR", "pa": "PA", "r.i": "RI", "s.c": "SC", "s.d": "SD",
    "tenn": "TN", "tex": "TX", "utah": "UT", "vt": "VT", "va": "VA", "wash": "WA",
    "w.va": "WV", "wis": "WI", "wyo": "WY", "p.r": "PR",
}


def district_state(jurisdiction: str) -> str:
    """'M.D. Fla.' → 'FL'; 'E.D.N.C.' → 'NC'; 'D.S.C.' → 'SC'; '' → ''."""
    j = str(jurisdiction or "").strip().lower()
    if not j:
        return ""
    # strip the division prefix ("m.d.", "n.d.", "e.d.", "w.d.", "c.d.", "s.d.", "d.")
    body = re.sub(r"^\s*(?:[nsewcm]\.?\s*d\.?|d\.?)\s*", "", j).strip(" .")
    body_dotless = body.replace(" ", "")
    for key, st in sorted(_DISTRICT_STATE.items(), key=lambda kv: -len(kv[0])):
        k = key.replace(".", "")
        if body_dotless.replace(".", "").startswith(k):
            return st
    # spelled-out state names ("District of Massachusetts")
    for name, ab in _NAME_TO_ABBR.items():
        if name.lower() in j:
            return ab
    return ""


# ---- defendant mention extraction -----------------------------------------------------
_HONORIFIC = re.compile(r"^(?:dr|mr|mrs|ms|nurse|pharmacist)\.?\s+", re.I)
_PERSON_SUFFIX = re.compile(r",?\s*\b(?:jr|sr|ii|iii|iv|m\.?d|d\.?o|d\.?d\.?s|d\.?m\.?d|d\.?p\.?m|"
                            r"ph\.?d|r\.?n|n\.?p|p\.?a|l\.?p\.?n|d\.?c|o\.?d|pharm\.?d|r\.?ph)\.?$", re.I)
_LEGAL_SUFFIX = re.compile(r"\b(?:llc|l\.l\.c|inc|incorporated|corp|corporation|co|company|ltd|"
                           r"lp|llp|pllc|pc|p\.c|pa|p\.a|plc|group|holdings)\b\.?", re.I)
_BUSINESS_WORDS = re.compile(r"\b(?:health|medical|clinic|pharmacy|pharma|rx|drugs?|hospice|home|care|services?|"
                             r"center|centre|laborator(?:y|ies)|labs?|hospital|physicians?|"
                             r"therapy|rehab|diagnostics?|imaging|dental|nursing|wellness|"
                             r"associates|partners|management|solutions|supply|dme|ambulance|"
                             r"transport|behavioral|counseling|urgent|surgical|surgery|"
                             r"orthopedic|cardiology|oncology|pain|spine|practice|consultants?|"
                             r"enterprises?|international|america|usa|network|equipment|supplies|"
                             r"hospitals?|healthcare|clinics|physician)\b", re.I)
_STATE_NAMES = "|".join(sorted((n.title() for n in _NAME_TO_ABBR), key=len, reverse=True))
_CITY = r"[A-Z][A-Za-z.'\-]+(?: [A-Z][A-Za-z.'\-]+){0,3}"
_NAME = r"[A-Z][\w.&'/\-]*(?:[ ,]+[A-Za-z&][\w.&'/\-]*){0,10}"
# "John Smith, 45, of Miami, Florida"  /  "Acme Health LLC, of Tampa, Florida"
_OF_PATTERN = re.compile(
    rf"(?P<name>{_NAME}?),\s*(?:(?:age[d]?\s+)?\d{{2}},\s*)?(?:formerly\s+)?of\s+(?P<city>{_CITY}),\s*(?P<state>{_STATE_NAMES})\b")
# "Acme Home Health LLC, a Tampa, Florida-based home health agency"
_BASED_PATTERN = re.compile(
    rf"(?P<name>{_NAME}?),\s*(?:an?\s+)?(?P<city>{_CITY}),\s*(?P<state>{_STATE_NAMES})[\s\-]based\b")
# "Acme Home Health LLC (Acme), located in Tampa, Florida"
_LOCATED_PATTERN = re.compile(
    rf"(?P<name>{_NAME}?)(?:\s*\([^)]{{1,40}}\))?,\s*(?:which is\s+|is\s+)?(?:located|headquartered|based)\s+in\s+(?P<city>{_CITY}),\s*(?P<state>{_STATE_NAMES})\b")
_LEADING_JUNK = re.compile(r"^(?:(?:the|and|that|with|against|defendants?|including|co-?defendants?|"
                           r"owner|operator|physician|doctor|its|his|her|their|by|"
                           r"a|an)\s+)+", re.I)


_CONNECTORS = {"of", "and", "&", "for", "the", "de", "la", "del", "y", "at", "on", "in", "to",
               "d/b/a", "dba", "d.b.a.", "d.b.a"}
_ABBREV_DOT = re.compile(r"\b(Dr|Mr|Mrs|Ms|Inc|Corp|Co|Ltd|Jr|Sr|St|Mt|Ft|No|[A-Z])\.(?= )")


def _clean_name(raw: str) -> str:
    """A captured span often starts mid-sentence ("... scheme. Smith owned Acme Health LLC"):
    keep only the trailing run of capitalised tokens (connectors like 'of'/'and' allowed
    inside), cut at any sentence boundary, then strip leading role words."""
    s = re.sub(r"\s+", " ", str(raw or "")).strip(" ,;:")
    # last sentence only, but "Dr. John A. Smith" / "Acme Inc. of" are not sentence ends
    protected = _ABBREV_DOT.sub(lambda m: m.group(1) + "\x00", s)
    s = protected.split(". ")[-1].replace("\x00", ".")
    toks = s.split(" ")
    keep: list[str] = []
    for t in reversed(toks):
        core = t.strip(",;:()")
        if not core:
            continue
        if core[0].isupper() or core[0].isdigit() or core.lower() in _CONNECTORS or core in ("&",):
            keep.append(t)
        else:
            break
    s = " ".join(reversed(keep)).strip(" ,")
    while True:
        t2 = _LEADING_JUNK.sub("", s).strip(" ,")
        t2 = re.sub(r"^(?:of|and|&|for|the|at|on|in|to)\s+", "", t2, flags=re.I).strip(" ,")
        if t2 == s:
            break
        s = t2
    return s


def classify_defendant(name: str) -> str:
    """'person' or 'org' from the shape of the name."""
    s = str(name or "").strip()
    if not s:
        return "org"
    if _LEGAL_SUFFIX.search(s) or "d/b/a" in s.lower() or " dba " in f" {s.lower()} ":
        return "org"
    if _HONORIFIC.match(s) or _PERSON_SUFFIX.search(s):
        return "person"
    if _BUSINESS_WORDS.search(s):
        return "org"
    toks = [t for t in re.split(r"\s+", s) if t]
    return "person" if 2 <= len(toks) <= 4 else "org"


def _split_dba(name: str) -> list[str]:
    parts = re.split(r"\s*(?:d/b/a|dba|doing business as)\s*", name, flags=re.I)
    return [p.strip(" ,") for p in parts if p.strip(" ,")]


def extract_defendants(summary: str, defendant_name: str = "", jurisdiction: str = "") -> list[dict]:
    """Every defendant mention with (name, kind, city, state). The primary defendant
    always appears (state from the district when the text names none)."""
    text = re.sub(r"\s+", " ", str(summary or ""))
    found: dict[tuple, dict] = {}

    def _add(name, city, state, source):
        for n in _split_dba(_clean_name(name)):
            if len(n) < 3:
                continue
            key = (n.upper(), (city or "").upper(), state)
            if key not in found:
                found[key] = {"defendant": n, "kind": classify_defendant(n),
                              "city": city or "", "state": state, "mention": source}

    for pat, src in ((_OF_PATTERN, "of_city_state"), (_BASED_PATTERN, "city_state_based"),
                     (_LOCATED_PATTERN, "located_in")):
        for m in pat.finditer(text):
            st = _NAME_TO_ABBR.get(m.group("state").upper(), "")
            _add(m.group("name"), m.group("city").strip(), st, src)

    primary = _clean_name(defendant_name)
    if primary:
        dst = district_state(jurisdiction)
        # if the text already located the primary defendant, keep that (city-bearing) row
        already = any(k[0] == primary.upper() or k[0] in primary.upper() for k in found)
        if not already:
            for n in _split_dba(primary):
                # the primary defendant's state: from the text if a mention shares its
                # name tokens, else the district
                st = next((v["state"] for v in found.values()
                           if v["defendant"].upper() in n.upper() or n.upper() in v["defendant"].upper()), dst)
                key = (n.upper(), "", st)
                if key not in found:
                    found[key] = {"defendant": n, "kind": classify_defendant(n), "city": "",
                                  "state": st, "mention": "primary_defendant"}
    return list(found.values())


# ---- matching against NPPES -------------------------------------------------------------
def person_key(name: str) -> str:
    """'Dr. John A. Smith, M.D.' → 'JOHN SMITH' (first token + last token)."""
    s = _HONORIFIC.sub("", str(name or "").strip())
    s = _PERSON_SUFFIX.sub("", s).strip(" ,")
    s = re.sub(r"[^A-Za-z' \-]", " ", s)
    toks = [t for t in re.split(r"\s+", s.upper()) if len(t) >= 2 or (t and t not in {"A"})]
    toks = [t for t in toks if len(t) >= 2]
    if len(toks) < 2:
        return ""
    return f"{toks[0]} {toks[-1]}"


def _nppes_person_key(provider_name: pd.Series) -> pd.Series:
    """provider_dim individuals are 'LAST, FIRST [MIDDLE]' → 'FIRST LAST'."""
    parts = provider_name.fillna("").astype(str).str.split(",", n=1, expand=True)
    last = parts[0].fillna("").str.strip().str.upper()
    first_full = parts[1].fillna("").str.strip().str.upper() if parts.shape[1] > 1 else pd.Series("", index=parts.index)
    first = first_full.str.split(r"\s+", n=1).str[0].fillna("")
    key = (first + " " + last).str.replace(r"[^A-Z' \-]", " ", regex=True).str.replace(r"\s+", " ", regex=True).str.strip()
    return key.where((first != "") & (last != ""), "")


def _index_nppes(provider_dim: pd.DataFrame, person_keys: set, org_keys: set,
                 states: set) -> tuple[dict, dict]:
    """Two lookups {(key, state): [(npi, city), ...]} restricted to the keys and states the
    mentions actually use. Never materializes per-group frames over the full NPPES table
    (8M rows): the full-table version exhausted memory on the operator's laptop."""
    cols = [c for c in ("npi", "provider_name", "name_key", "entity_type", "addr_state", "addr_city")
            if c in provider_dim.columns]
    p = provider_dim[cols]
    st = p["addr_state"].fillna("").astype(str).str.upper().str.strip() if "addr_state" in p else pd.Series("", index=p.index)
    p = p[st.isin(states)]
    st = st.loc[p.index]
    city = p["addr_city"].fillna("").astype(str).str.upper().str.strip() if "addr_city" in p else pd.Series("", index=p.index)
    et = p["entity_type"].fillna("").astype(str).str.strip() if "entity_type" in p else pd.Series("", index=p.index)
    name = p["provider_name"].fillna("").astype(str) if "provider_name" in p else pd.Series("", index=p.index)
    is_person = (et == "1") | ((et == "") & name.str.contains(","))
    npi = p["npi"].astype(str)

    people: dict = {}
    if person_keys:
        pk = _nppes_person_key(name[is_person])
        hit = pk[pk.isin(person_keys)]
        for idx, k in hit.items():
            people.setdefault((k, st[idx]), []).append((npi[idx], city[idx]))
    orgs: dict = {}
    if org_keys:
        if "name_key" in p.columns:
            ok = p.loc[~is_person, "name_key"].fillna("").astype(str)
        else:
            ok = name[~is_person].map(norm_org_name)
        hit = ok[ok.isin(org_keys)]
        for idx, k in hit.items():
            orgs.setdefault((k, st[idx]), []).append((npi[idx], city[idx]))
    return people, orgs


def resolve_defendants(mentions: pd.DataFrame, provider_dim: pd.DataFrame) -> pd.DataFrame:
    """mentions: case_id, defendant, kind, city, state (+ any other columns carried).
    Returns the same rows with npi, status, match_level, n_candidates, basis."""
    out = mentions.copy()
    for c in ("npi", "match_level"):
        out[c] = ""
    out["n_candidates"] = 0
    out["status"] = "unmatched"
    if not len(out):
        out["basis"] = "resolved_defendant"
        return out
    # keys for BOTH kinds (the shape guess can be wrong, so both lookups are tried)
    out["_pk"] = out["defendant"].map(person_key)
    out["_ok"] = out["defendant"].map(norm_org_name)
    states = set(out["state"].fillna("").astype(str).str.upper()) - {""}
    people, orgs = _index_nppes(provider_dim, set(out["_pk"]) - {""}, set(out["_ok"]) - {""}, states)

    def _lookup(kind, r, st):
        if kind == "person":
            k = r["_pk"]
            return k, (people.get((k, st)) if k else None)
        k = r["_ok"]
        if not distinctive_name_key(_normalize_name(pd.Series([k])).iloc[0]):
            return k, "generic"
        return k, (orgs.get((k, st)) if k else None)

    for i, r in out.iterrows():
        st = str(r.get("state") or "").upper()
        if not st:
            out.at[i, "status"] = "no_state"
            continue
        key, cands = _lookup(r["kind"], r, st)
        if isinstance(cands, str):                 # generic org name: never matched
            out.at[i, "status"] = "generic_name"
            continue
        if not cands:
            # shape guessed the kind wrong ("Bayou Rx" is a pharmacy, "J Smith Jr" a person)
            other = "org" if r["kind"] == "person" else "person"
            key2, cands2 = _lookup(other, r, st)
            if not isinstance(cands2, str) and cands2:
                key, cands = key2, cands2
                out.at[i, "kind"] = other
        if not key or not cands:
            continue
        level = "name_state"
        city = str(r.get("city") or "").upper()
        if len({n for n, _ in cands}) > 1 and city:
            narrowed = [(n, c) for n, c in cands if c == city]
            if narrowed:
                cands, level = narrowed, "name_city_state"
        uniq = sorted({n for n, _ in cands})
        out.at[i, "n_candidates"] = len(uniq)
        if len(uniq) == 1:
            out.at[i, "npi"] = uniq[0]
            out.at[i, "match_level"] = level
            out.at[i, "status"] = "resolved"
        else:
            out.at[i, "status"] = "ambiguous"
    out = out.drop(columns=["_pk", "_ok"])
    out["basis"] = "resolved_defendant"
    return out


def mentions_from_case_db(case_db: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for c in case_db.itertuples(index=False):
        d = c._asdict()
        for m in extract_defendants(d.get("summary", ""), d.get("defendant_name", ""), d.get("jurisdiction", "")):
            rows.append({"case_id": str(d.get("case_id", "")), "announced_date": d.get("announced_date", ""),
                         "amount_usd": d.get("amount_usd", ""), "scheme": d.get("scheme", ""), **m})
    return pd.DataFrame(rows, columns=["case_id", "announced_date", "amount_usd", "scheme", "defendant",
                                       "kind", "city", "state", "mention"])


# ---- residuals for research (no dispatch here; prompts are written for review) -----------
RESEARCH_PROMPT = (
    "Public-records task. Find the National Provider Identifier (NPI) for a defendant named in a "
    "United States Department of Justice healthcare-fraud announcement. Use only public sources: the "
    "NPPES NPI Registry (npiregistry.cms.hhs.gov), the DOJ press release, court dockets (PACER / "
    "CourtListener), state licensing boards. Do not contact anyone. Return JSON with keys: npi "
    "(10 digits or null), entity_type (individual or organization), practice_city, practice_state, "
    "specialty_or_taxonomy, confidence (high/medium/low), evidence (one sentence per source with its "
    "URL), alternates (other NPIs that could be this defendant, with why they were rejected).\n"
    "Defendant: {defendant} ({kind}); city: {city}; state: {state}; case announced {announced_date}; "
    "DOJ case id or URL: {case_id}; amount: {amount_usd}; candidate NPIs already found by name "
    "({n_candidates}): the task is to pick the right one or reject all.")


SE_STATES = ["FL", "GA", "AL", "MS", "TN", "NC", "SC", "LA", "KY"]
MFCU_SWEEP_PROMPT = (
    "Public-records research task. Build a table of every Medicaid fraud enforcement outcome announced "
    "by the {state_name} Medicaid Fraud Control Unit (MFCU) or the {state_name} Attorney General in "
    "calendar year {year}: convictions, guilty pleas, civil settlements, and False Claims Act "
    "resolutions involving a healthcare provider or supplier. Use only public sources: the state "
    "Attorney General press-release archive, the MFCU annual report, court dockets, and the NPPES NPI "
    "Registry (npiregistry.cms.hhs.gov) to look up each provider's NPI. Do not contact anyone. Return a "
    "CSV with exactly these columns: announced_date (YYYY-MM-DD), defendant_name, defendant_type "
    "(individual or organization), city, state, npi (10 digits or blank), provider_type_or_specialty, "
    "scheme (billing for services not rendered / upcoding / kickbacks / medically unnecessary / "
    "identity or credential misuse / other, in a few words), amount_usd (restitution or settlement, "
    "blank if none), outcome (conviction / plea / civil settlement / exclusion), source_url, "
    "docket_or_case_number, conduct_start_year, conduct_end_year. One row per defendant. Blank beats "
    "a guess. Include a final row count and the list of press-release index pages you walked.")
_STATE_NAME = {v: k.title() for k, v in _NAME_TO_ABBR.items()}


def mfcu_sweep_prompts(states: list[str] | None = None, years: list[int] | None = None) -> list[dict]:
    """One Manus / hand-research task per state per year; the reviewed CSV loads through
    ``research_evidence.load_mfcu_cases`` (human gate by construction)."""
    states = states or SE_STATES
    years = years or list(range(2020, 2026))
    return [{"state": st, "year": y,
             "prompt": MFCU_SWEEP_PROMPT.format(state_name=_STATE_NAME.get(st, st), year=y)}
            for st in states for y in years]


def residuals(resolved: pd.DataFrame, min_amount: float = 0.0) -> pd.DataFrame:
    r = resolved[resolved["status"].isin(["ambiguous", "unmatched"])].copy()
    r["amount_num"] = pd.to_numeric(r["amount_usd"], errors="coerce").fillna(0.0)
    r = r[(r["status"] == "ambiguous") | (r["amount_num"] >= float(min_amount))]
    r["priority"] = r["status"].map({"ambiguous": 0, "unmatched": 1})
    return r.sort_values(["priority", "amount_num"], ascending=[True, False]).drop(columns=["priority"])


def research_prompts(res: pd.DataFrame) -> list[dict]:
    return [{"case_id": r.case_id, "defendant": r.defendant,
             "prompt": RESEARCH_PROMPT.format(defendant=r.defendant, kind=r.kind, city=r.city or "unknown",
                                              state=r.state or "unknown", announced_date=r.announced_date,
                                              case_id=r.case_id, amount_usd=r.amount_usd or "unknown",
                                              n_candidates=r.n_candidates)}
            for r in res.itertuples(index=False)]


def report(resolved: pd.DataFrame, res: pd.DataFrame) -> str:
    L = ["# DEFENDANT RESOLUTION: DOJ case defendants → NPIs", "",
         "_Training and validation positives only. Prosecuted providers are never leads (public disclosure, first-to-file)._", ""]
    n_cases = resolved["case_id"].nunique() if len(resolved) else 0
    by = resolved["status"].value_counts().to_dict() if len(resolved) else {}
    L += [f"- cases with at least one defendant mention: **{n_cases:,}**; mentions: **{len(resolved):,}**",
          "- mention status: " + ", ".join(f"{k} {v:,}" for k, v in by.items()),
          f"- cases with at least one RESOLVED NPI: **{resolved[resolved['status'] == 'resolved']['case_id'].nunique():,}**; "
          f"distinct NPIs: **{resolved[resolved['status'] == 'resolved']['npi'].nunique():,}**", ""]
    if len(resolved):
        L += ["## Resolved by state (distinct NPIs)", "", "| state | resolved | ambiguous | unmatched |", "|---|--:|--:|--:|"]
        for st, g in resolved.groupby("state"):
            if not st:
                continue
            L.append(f"| {st} | {g[g.status == 'resolved']['npi'].nunique()} | {(g.status == 'ambiguous').sum()} | {(g.status == 'unmatched').sum()} |")
        L += ["", "## Match level", "", "| level | mentions |", "|---|--:|"]
        for lv, n in resolved[resolved["status"] == "resolved"]["match_level"].value_counts().items():
            L.append(f"| {lv} | {n} |")
    L += ["", f"## Residual list for research: {len(res):,} mentions (ambiguous first, then unmatched by case dollars)", ""]
    for r in res.head(25).itertuples(index=False):
        L.append(f"- {r.status}: {r.defendant} ({r.kind}) {r.city or ''} {r.state} — case {r.case_id}, ${r.amount_num:,.0f}, candidates {r.n_candidates}".replace(" — ", "; "))
    return "\n".join(L)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case-db", required=True, help="DOJ case DB csv/parquet")
    ap.add_argument("--provider-dim", required=True, help="processed/provider_dim.parquet")
    ap.add_argument("--out", required=True, help="processed/case_defendant_npis.parquet")
    ap.add_argument("--min-amount", type=float, default=250_000.0,
                    help="unmatched mentions below this case amount are left out of the research list")
    ap.add_argument("--mfcu-prompts", default=None,
                    help="also write state-MFCU sweep research prompts (jsonl) for the Southeast, 2020-2025")
    a = ap.parse_args()
    if a.mfcu_prompts:
        with open(a.mfcu_prompts, "w", encoding="utf-8") as fh:
            for pr in mfcu_sweep_prompts():
                fh.write(json.dumps(pr) + "\n")
        print(f"mfcu sweep prompts: {len(mfcu_sweep_prompts())} -> {a.mfcu_prompts}")
    cdb = pd.read_csv(a.case_db, dtype=str) if a.case_db.endswith(".csv") else pd.read_parquet(a.case_db)
    pdim = pd.read_parquet(a.provider_dim)
    mentions = mentions_from_case_db(cdb)
    resolved = resolve_defendants(mentions, pdim)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    resolved.to_parquet(out, index=False)
    res = residuals(resolved, a.min_amount)
    res.to_csv(out.with_name("case_defendants_unresolved.csv"), index=False)
    with open(out.with_name("case_defendant_research_prompts.jsonl"), "w", encoding="utf-8") as fh:
        for p in research_prompts(res):
            fh.write(json.dumps(p) + "\n")
    rep = out.with_name("DEFENDANT_RESOLUTION.md")
    rep.write_text(report(resolved, res), encoding="utf-8")
    n_res = int((resolved["status"] == "resolved").sum())
    print(f"defendant resolver: {len(cdb):,} cases, {len(resolved):,} mentions, {n_res:,} resolved to an NPI "
          f"({resolved[resolved['status'] == 'resolved']['case_id'].nunique():,} cases), {len(res):,} for research -> {out}\n  report: {rep}")


if __name__ == "__main__":
    main()
