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
    if len(j) == 2 and j.upper() in _NAME_TO_ABBR.values():      # a bare state code passes through
        return j.upper()
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


_USAO_URL = re.compile(r"usao-(?:[nsewcm]d)?([a-z]{2})\b")
_STATE_GOV_URL = re.compile(r"(?:^|[./])(?:ag|oag|attorneygeneral|governor|myfloridalegal|law)?\.?([a-z]{2})\.gov\b")
_URL_STATE_HINTS = {"myfloridalegal.com": "FL", "ag.idaho.gov": "ID", "mass.gov": "MA", "hawaii.gov": "HI",
                    "ag.ky.gov": "KY", "law.georgia.gov": "GA", "oag.ca.gov": "CA", "ag.ny.gov": "NY",
                    "texasattorneygeneral.gov": "TX", "ag.state.la.us": "LA", "ago.mo.gov": "MO",
                    "tn.gov": "TN", "ncdoj.gov": "NC", "scag.gov": "SC", "alabamaag.gov": "AL",
                    "ag.ms.gov": "MS", "michigan.gov": "MI", "illinoisattorneygeneral.gov": "IL",
                    "ohioattorneygeneral.gov": "OH", "attorneygeneral.gov": "PA", "oag.dc.gov": "DC",
                    "atg.wa.gov": "WA", "doj.state.or.us": "OR", "coag.gov": "CO", "azag.gov": "AZ",
                    "ag.nv.gov": "NV", "nj.gov": "NJ", "ag.state.mn.us": "MN", "iowaattorneygeneral.gov": "IA",
                    "in.gov": "IN", "ky.gov": "KY", "virginia.gov": "VA", "wvago.gov": "WV",
                    "doj.state.wi.us": "WI", "ag.nebraska.gov": "NE", "ag.ks.gov": "KS", "oag.ok.gov": "OK",
                    "arkansasag.gov": "AR", "ag.utah.gov": "UT", "riag.ri.gov": "RI", "ct.gov": "CT",
                    "maine.gov": "ME", "doj.nh.gov": "NH", "ago.vermont.gov": "VT", "marylandattorneygeneral.gov": "MD",
                    "attorneygeneral.delaware.gov": "DE", "nmag.gov": "NM", "dojmt.gov": "MT",
                    "ag.nd.gov": "ND", "atg.sd.gov": "SD", "law.alaska.gov": "AK", "wyoming.gov": "WY"}


def url_state(source_url: str) -> str:
    """'https://www.justice.gov/usao-mdfl/pr/...' → 'FL'; state AG domains via a hint table."""
    u = str(source_url or "").lower()
    if not u:
        return ""
    m = _USAO_URL.search(u)
    if m:
        st = m.group(1).upper()
        if st in _NAME_TO_ABBR.values():
            return st
    for host, st in _URL_STATE_HINTS.items():
        if host in u:
            return st
    return ""


_STATE_IN_TEXT = re.compile(r"\b(" + "|".join(sorted((n.title() for n in _NAME_TO_ABBR), key=len, reverse=True)) + r")\b")
_DISTRICT_IN_TEXT = re.compile(r"\b(?:[NSEWCM]\.?D\.?|D\.)\s?(?:Fla|Ga|Ala|Miss|Tenn|N\.?C|S\.?C|La|Ky|Tex|Cal|Pa|N\.?Y|N\.?J|Mass|Ohio|Ill|Mich|Va|Md|Mo|Okla|Nev|Wash|Ariz|Colo|Conn|Minn|Wis|Ind|Iowa|Kan|Neb|Ore|Utah|W\.? ?Va|D\.?C)\.?")


def text_state(summary: str) -> str:
    """The ONE state a case text names (full state name or a district cite); '' when it
    names none or more than one. Used only when neither the district nor the URL says."""
    t = str(summary or "")
    names = {_NAME_TO_ABBR[m.upper()] for m in _STATE_IN_TEXT.findall(t)}
    for d in _DISTRICT_IN_TEXT.findall(t):
        st = district_state(d)
        if st:
            names.add(st)
    return next(iter(names)) if len(names) == 1 else ""


_PERSON_SPLIT = re.compile(r"\s*;\s*|\s*,?\s+and\s+|\s*&\s*", re.I)


def _split_persons(name: str) -> list[str]:
    """'Jorge Perez and Ricardo Perez' / 'A Rose Sr.; B Rose; C Sanders' → one name each,
    but ONLY when every part looks like a person (an org name keeps its 'and')."""
    if ";" not in name and "," not in name and not re.search(r"\s(?:and|&)\s", name, re.I):
        return [name]
    parts = [p.strip(" ,") for p in _PERSON_SPLIT.split(name) if p.strip(" ,")]
    if len(parts) >= 2 and all(classify_defendant(p) == "person" for p in parts):
        return parts
    if ";" in name:
        return [p.strip(" ,") for p in name.split(";") if p.strip(" ,")]
    # "Karel Felipe, Tamara Quicutis" / "Patrick Boyd, Charles Boyd, Adam Brosius":
    # comma-separated PEOPLE (every part 2-3 capitalised tokens, no legal suffix)
    if "," in name:
        cparts = [p.strip() for p in name.split(",") if p.strip()]
        if len(cparts) >= 2 and all(2 <= len(p.split()) <= 3 and classify_defendant(p) == "person"
                                    and not _PERSON_SUFFIX.search(p) for p in cparts):
            return cparts
    return [name]


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
                           r"nurse|pharmacist|chiropractor|dentist|podiatrist|optometrist|psychologist|"
                           r"therapist|counselor|surgeon|psychiatrist|"
                           r"a|an)\s+)+", re.I)


_CONNECTORS = {"of", "and", "&", "for", "the", "de", "la", "del", "y", "at", "on", "in", "to",
               "d/b/a", "dba", "d.b.a.", "d.b.a"}
_ABBREV_DOT = re.compile(r"\b(Dr|Mr|Mrs|Ms|Inc|Corp|Co|Ltd|Jr|Sr|St|Mt|Ft|No|[A-Z])\.(?= )")


def _clean_name(raw: str) -> str:
    """A captured span often starts mid-sentence ("... scheme. Smith owned Acme Health LLC"):
    keep only the trailing run of capitalised tokens (connectors like 'of'/'and' allowed
    inside), cut at any sentence boundary, then strip leading role words."""
    s = re.sub(r"\s+", " ", str(raw or "")).strip(" ,;:")
    s = re.sub(r"\((?:collectively|together|now known as|formerly|f/k/a|a/k/a|the)[^)]*\)?", " ", s, flags=re.I)
    s = re.sub(r"\b(?:now known as|formerly known as|f/k/a|a/k/a)\b.*$", " ", s, flags=re.I)
    s = s.replace("(", " ").replace(")", " ").replace('"', " ").replace("\u201c", " ").replace("\u201d", " ")
    s = re.sub(r"\s+", " ", s).strip(" ,;:")
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
    # an honorific inside the run starts the person's name ("... of Justice Dr. Jane Roe")
    hm = list(re.finditer(r"\b(?:Dr|Mr|Mrs|Ms)\.?\s", s))
    if hm and hm[-1].start() > 0:
        s = s[hm[-1].start():]
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


def extract_defendants(summary: str, defendant_name: str = "", jurisdiction: str = "",
                       source_url: str = "") -> list[dict]:
    """Every defendant mention with (name, kind, city, state). The primary defendant
    always appears (state from the district, else from the source URL, when the text
    names none)."""
    text = re.sub(r"\s+", " ", str(summary or ""))
    found: dict[tuple, dict] = {}

    def _add(name, city, state, source):
        for n0 in _split_dba(_clean_name(name)):
            for n in _split_persons(n0):
                if len(n) < 3:
                    continue
                key = (n.upper(), (city or "").upper(), state)
                if key not in found:
                    role, rclass, rfam = role_near(text, n)
                    found[key] = {"defendant": n, "kind": classify_defendant(n),
                                  "city": city or "", "state": state, "mention": source,
                                  "role": role, "role_class": rclass, "role_family": rfam}

    for pat, src in ((_OF_PATTERN, "of_city_state"), (_BASED_PATTERN, "city_state_based"),
                     (_LOCATED_PATTERN, "located_in")):
        for m in pat.finditer(text):
            st = _NAME_TO_ABBR.get(m.group("state").upper(), "")
            _add(m.group("name"), m.group("city").strip(), st, src)

    primary = _clean_name(defendant_name)
    if primary:
        dst = district_state(jurisdiction) or url_state(source_url) or text_state(text)
        # if the text already located the primary defendant, keep that (city-bearing) row
        already = any(k[0] == primary.upper() or k[0] in primary.upper() for k in found)
        if not already:
            for n0 in _split_dba(primary):
                for n in _split_persons(n0):
                    # the primary defendant's state: from the text if a mention shares its
                    # name tokens, else the district / URL
                    st = next((v["state"] for v in found.values()
                               if v["defendant"].upper() in n.upper() or n.upper() in v["defendant"].upper()), dst)
                    key = (n.upper(), "", st)
                    if key not in found:
                        role, rclass, rfam = role_near(text, n)
                        found[key] = {"defendant": n, "kind": classify_defendant(n), "city": "",
                                      "state": st, "mention": "primary_defendant",
                                      "role": role, "role_class": rclass, "role_family": rfam}
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
                 states: set) -> tuple[dict, dict, dict]:
    """Two lookups {(key, state): [(npi, city, middle, taxonomy_family), ...]} restricted to the
    keys and states the mentions actually use, plus {person_key: hits across ALL indexed states}
    (the name-commonness count: DOJ cases span the country, so the indexed states are close to
    the nation). Never materializes per-group frames over the full NPPES table (8M rows): the
    full-table version exhausted memory on the operator's laptop."""
    cols = [c for c in ("npi", "provider_name", "name_key", "entity_type", "addr_state", "addr_city", "taxonomy_code")
            if c in provider_dim.columns]
    p = provider_dim[cols]
    st = p["addr_state"].fillna("").astype(str).str.upper().str.strip() if "addr_state" in p else pd.Series("", index=p.index)
    p = p[st.isin(states)]
    st = st.loc[p.index]
    city = p["addr_city"].fillna("").astype(str).str.upper().str.strip() if "addr_city" in p else pd.Series("", index=p.index)
    et = p["entity_type"].fillna("").astype(str).str.strip() if "entity_type" in p else pd.Series("", index=p.index)
    name = p["provider_name"].fillna("").astype(str) if "provider_name" in p else pd.Series("", index=p.index)
    tax = p["taxonomy_code"].fillna("").astype(str) if "taxonomy_code" in p else pd.Series("", index=p.index)
    is_person = (et == "1") | ((et == "") & name.str.contains(","))
    npi = p["npi"].astype(str)

    people: dict = {}
    national: dict = {}
    if person_keys:
        pk = _nppes_person_key(name[is_person])
        hit = pk[pk.isin(person_keys)]
        for idx, k in hit.items():
            full = name[idx].upper()
            middle = full.split(",", 1)[1].strip().split(" ")[1:] if "," in full else []
            people.setdefault((k, st[idx]), []).append((npi[idx], city[idx], " ".join(middle), taxonomy_family(tax[idx])))
            national[k] = national.get(k, 0) + 1
    orgs: dict = {}
    if org_keys:
        if "name_key" in p.columns:
            ok = p.loc[~is_person, "name_key"].fillna("").astype(str)
        else:
            ok = name[~is_person].map(norm_org_name)
        hit = ok[ok.isin(org_keys)]
        for idx, k in hit.items():
            orgs.setdefault((k, st[idx]), []).append((npi[idx], city[idx], "", ""))
    return people, orgs, national


CHAIN_MIN_CANDIDATES = 5   # an org name with this many NPIs in one state is a chain, not a target


# ---- role coherence: what the release SAYS the person is vs what the NPI IS ---------------
# A press release names recruiters, marketers, billers, office managers and owners next to
# the clinicians. A unique in-state name hit for "John Smith, a patient recruiter" is the
# wrong person by construction (the recruiter has no NPI; some other John Smith does). The
# role near the mention is read from the text, mapped to a family, and compared with the
# NPPES taxonomy family of the candidate: a non-clinical role never resolves on a name
# alone, a clinical role that contradicts the taxonomy is a role_mismatch, and an agreeing
# role is recorded as corroboration (role_agree = agree) for the evidence ladder.
_ROLE_CLINICAL: list[tuple[str, re.Pattern]] = [(f, re.compile(p, re.I)) for f, p in (
    ("np", r"nurse[- ]practitioner|\bAPRN\b|\bARNP\b|\bFNP\b|\bNP-C\b"),
    ("pa", r"physician'?s? assistant|\bPA-C\b"),
    ("crna", r"nurse anesthetist|\bCRNA\b"),
    ("nurse", r"registered nurse|licensed (?:practical|vocational) nurse|\bnurse\b|\bRN\b|\bLPN\b|\bLVN\b"),
    ("pharmacist", r"pharmacist"),
    ("dentist", r"\bdentist|orthodontist|oral surgeon|\bDDS\b|\bDMD\b"),
    ("chiropractor", r"chiropract"),
    ("pt_ot", r"physical therap|occupational therap|speech(?:[- ]language)?[- ]patholog|respiratory therap"),
    ("podiatrist", r"podiatr|\bDPM\b"),
    ("optometrist", r"optometr"),
    ("behavioral", r"psychologist|\bcounselor|social worker|\bLCSW\b|\bLPC\b|marriage and family therap|behavioral health therap|\btherapist"),
    ("aide", r"home health aide|personal care (?:aide|attendant|assistant)|certified nurs(?:e|ing) assistant|\bCNA\b|caregiver"),
    ("psychiatrist", r"psychiatr"),        # a physician, but NPPES sometimes files them under a behavioral code
    ("physician", r"physician|\bdoctor\b|\bM\.D\.|\bD\.O\.|surgeon|anesthesiologist|cardiologist|"
                  r"dermatologist|neurologist|oncologist|radiologist|urologist|pediatrician|internist|"
                  r"gynecologist|ophthalmologist|\bpathologist|rheumatologist|gastroenterologist|"
                  r"pulmonologist|endocrinologist|nephrologist"),
)]
_APPOSITIVE_ZONE = 60   # chars after the name in which a role phrase is read as this person's appositive
_ROLE_NONCLINICAL = re.compile(
    r"patient recruiter|\brecruiter|\bmarketer|marketing (?:company|firm|representative|agent|director)|"
    r"\bbiller\b|billing (?:manager|clerk|specialist|agent|company)|office manager|\baccountant|bookkeeper|"
    r"sales(?:man|woman|person| representative| rep\b)|\bdriver\b|receptionist|telemarketer|call[- ]center|"
    r"straw owner|nominee owner|money launder|\bcapper\b|\brunner\b|\bkickback recipient", re.I)
_ROLE_OWNER = re.compile(r"\bowner|\boperator|\bCEO\b|\bCFO\b|\bCOO\b|chief (?:executive|financial|operating)|"
                         r"\bpresident|\bfounder|\badministrator|medical director|\bdirector\b|\bmanager\b|\bexecutive\b", re.I)
_ROLE_COMPAT: dict[str, set[str]] = {
    "physician": {"physician"}, "psychiatrist": {"physician", "behavioral"},
    "np": {"np", "nurse"}, "pa": {"pa"}, "crna": {"crna", "nurse"},
    "nurse": {"nurse", "np", "crna"}, "pharmacist": {"pharmacist"}, "dentist": {"dentist"},
    "chiropractor": {"chiropractor"}, "pt_ot": {"pt_ot"}, "podiatrist": {"podiatrist"},
    "optometrist": {"optometrist"}, "behavioral": {"behavioral"}, "aide": {"aide", "nurse"},
    # "Dr." with no specialty word: any doctoral clinician
    "doctorate": {"physician", "dentist", "chiropractor", "podiatrist", "optometrist", "behavioral"},
}


def taxonomy_family(code: str) -> str:
    """NUCC taxonomy code → the coarse family used for role coherence ('' when unknown)."""
    c = str(code or "").strip().upper()
    if len(c) < 3 or c in ("NAN", "NONE"):
        return ""
    for prefix, fam in (("20", "physician"), ("363L", "np"), ("363A", "pa"), ("3675", "crna"), ("364", "np"),
                        ("163", "nurse"), ("164", "nurse"), ("183", "pharmacist"), ("122", "dentist"),
                        ("124", "dentist"), ("111", "chiropractor"), ("225", "pt_ot"), ("235", "pt_ot"),
                        ("213", "podiatrist"), ("152", "optometrist"), ("101", "behavioral"),
                        ("102", "behavioral"), ("103", "behavioral"), ("104", "behavioral"),
                        ("106", "behavioral"), ("3747", "aide"), ("39", "student")):
        if c.startswith(prefix):
            return fam
    return "other"


def role_near(text: str, name: str) -> tuple[str, str, str]:
    """(role_phrase, role_class, role_family) read from the clause around the first mention of
    ``name`` in ``text``. role_class: clinical / nonclinical / owner_exec / unknown. The window
    stops at the next sentence, semicolon, or another capitalised name after ' and ', so a
    co-defendant's role is not read as this defendant's."""
    t = re.sub(r"\s+", " ", str(text or ""))
    n = str(name or "").strip()
    if not n:
        return "", "unknown", ""
    is_dr = bool(_HONORIFIC.match(n) and n.lower().startswith("dr"))   # from the NAME, before any fallback
    i = t.lower().find(n.lower())
    if i < 0:
        # the text may print the name without the honorific / middle name: try "First Last",
        # then "Dr. Last", then the bare surname only when it occurs ONCE (a father and son
        # share a surname; the first hit would be the wrong person)
        pk = person_key(n)
        first, last = (pk.split(" ")[0], pk.split(" ")[-1]) if pk else ("", "")
        cand = ""
        for probe in ([f"{first} {last}"] if first else []) + ([f"dr. {last}", f"dr {last}"] if last else []):
            j = t.lower().find(probe.lower())
            if j >= 0:
                i, cand = j, probe
                break
        if i < 0 and len(last) >= 4 and len(re.findall(rf"\b{re.escape(last)}\b", t, re.I)) == 1:
            i, cand = t.lower().find(last.lower()), last
        if i < 0:
            return "", "unknown", ""
        n = t[i: i + len(cand)]
    # the clause: back to the last sentence break, forward to the next sentence break or to a
    # co-defendant introduced by "and <Name>" / "and his sister" / "along with the"
    before = re.split(r"[.;]\s", t[max(0, i - 90): i])[-1]
    after = re.split(r"(?<=[.;])\s|\s(?:and|while|along with|together with)\s+"
                     r"(?=(?:Dr\.\s)?[A-Z][a-z]+\s[A-Z]|(?:his|her|their|the|a|an|two|three|co-defendant)\b)",
                     t[i + len(n): i + len(n) + 140])[0]
    # every clinical phrase in the clause, ranked by distance to the name (an appositive right
    # after the name is distance 0); the nearest wins, a longer phrase wins a tie
    # ("speech pathologist" over "pathologist")
    hits: list[tuple[int, int, str, str]] = []
    for f, pat in _ROLE_CLINICAL:
        for m in pat.finditer(before):
            hits.append((len(before) - m.end(), -len(m.group(0)), m.group(0), f))
        for m in pat.finditer(after):
            hits.append((0 if m.start() <= _APPOSITIVE_ZONE else m.start(), -len(m.group(0)), m.group(0), f))
    if hits:
        hits.sort()
        dist, _, phrase, fam = hits[0]
        # "Dr." on the name outranks a clinical word that sits outside the appositive zone
        # ("Doctor, pharmacists and marketers sentenced ... Dr. Baldizzi, 46, of Tampa")
        if is_dr and dist > _APPOSITIVE_ZONE:
            return "Dr.", "clinical", "doctorate"
        return phrase, "clinical", fam
    win = f"{before} {n} {after}"
    if is_dr:
        # a doctor who also marketed or owned is still a provider: never nonclinical
        m2 = _ROLE_OWNER.search(win)
        return (m2.group(0), "owner_exec", "doctorate") if m2 else ("Dr.", "clinical", "doctorate")
    m = _ROLE_NONCLINICAL.search(win)
    if m:
        return m.group(0), "nonclinical", ""
    m2 = _ROLE_OWNER.search(win)
    if m2:
        return m2.group(0), "owner_exec", ""
    return "", "unknown", ""


def role_agreement(role_family: str, npi_family: str) -> str:
    """agree / disagree / unknown for a text role family against the NPI's taxonomy family."""
    if not role_family or npi_family in ("", "other", "student"):
        return "unknown"
    return "agree" if npi_family in _ROLE_COMPAT.get(role_family, {role_family}) else "disagree"


def _middle_tokens(defendant: str) -> list[str]:
    s = _HONORIFIC.sub("", str(defendant or "").strip())
    s = _PERSON_SUFFIX.sub("", s).strip(" ,")
    toks = [t.strip(".") for t in re.split(r"\s+", re.sub(r"[^A-Za-z' .\-]", " ", s).upper()) if t.strip(".")]
    return toks[1:-1] if len(toks) > 2 else []


def resolve_defendants(mentions: pd.DataFrame, provider_dim: pd.DataFrame) -> pd.DataFrame:
    """mentions: case_id, defendant, kind, city, state (+ any other columns carried).
    Returns the same rows with npi, status, match_level, n_candidates, basis."""
    out = mentions.copy()
    for c in ("npi", "match_level", "npi_taxonomy_family"):
        out[c] = ""
    for c in ("role", "role_class", "role_family"):
        if c not in out.columns:
            out[c] = "" if c != "role_class" else "unknown"
    out["role_agree"] = "unknown"
    out["n_candidates"] = 0
    out["name_national_hits"] = 0
    out["status"] = "unmatched"
    if not len(out):
        out["basis"] = "resolved_defendant"
        return out
    # keys for BOTH kinds (the shape guess can be wrong, so both lookups are tried)
    out["_pk"] = out["defendant"].map(person_key)
    out["_ok"] = out["defendant"].map(norm_org_name)
    states = set(out["state"].fillna("").astype(str).str.upper()) - {""}
    people, orgs, national = _index_nppes(provider_dim, set(out["_pk"]) - {""}, set(out["_ok"]) - {""}, states)

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
        if len({c[0] for c in cands}) > 1 and city:
            narrowed = [c for c in cands if c[1] == city]
            if narrowed:
                cands, level = narrowed, "name_city_state"
        if len({c[0] for c in cands}) > 1 and out.at[i, "kind"] == "person":
            # a middle name / initial in the case text settles a two-way tie
            mids = _middle_tokens(r["defendant"])
            if mids:
                narrowed = [c for c in cands if c[2] and any(
                    (len(m) == 1 and c[2].startswith(m)) or (len(m) > 1 and m in c[2].split()) for m in mids)]
                if narrowed:
                    cands, level = narrowed, level + "_middle"
        uniq = sorted({c[0] for c in cands})
        out.at[i, "n_candidates"] = len(uniq)
        if out.at[i, "kind"] == "person":
            out.at[i, "name_national_hits"] = int(national.get(key, 0))
        if len(uniq) == 1:
            rclass = str(r.get("role_class") or "unknown")
            rfam = str(r.get("role_family") or "")
            npi_fam = next((c[3] for c in cands if c[0] == uniq[0]), "")
            out.at[i, "npi_taxonomy_family"] = npi_fam
            if out.at[i, "kind"] == "person" and rclass == "nonclinical":
                # a recruiter / marketer / biller has no NPI; the unique name hit is someone else
                out.at[i, "status"] = "nonclinical_role"
                continue
            agree = role_agreement(rfam, npi_fam) if out.at[i, "kind"] == "person" else "n/a"
            out.at[i, "role_agree"] = agree
            if agree == "disagree":
                out.at[i, "status"] = "role_mismatch"       # text says pharmacist, NPI is a chiropractor
                continue
            out.at[i, "npi"] = uniq[0]
            out.at[i, "match_level"] = level
            out.at[i, "status"] = "resolved"
        elif out.at[i, "kind"] == "org" and len(uniq) >= CHAIN_MIN_CANDIDATES:
            out.at[i, "status"] = "chain_org"        # the org_billing tier covers enrolled chains
        else:
            out.at[i, "status"] = "ambiguous"
    out = out.drop(columns=["_pk", "_ok"])
    out["basis"] = "resolved_defendant"
    return out


def mentions_from_case_db(case_db: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for c in case_db.itertuples(index=False):
        d = c._asdict()
        for m in extract_defendants(d.get("summary", ""), d.get("defendant_name", ""), d.get("jurisdiction", ""),
                                    d.get("source_url", "")):
            rows.append({"case_id": str(d.get("case_id", "")), "announced_date": d.get("announced_date", ""),
                         "amount_usd": d.get("amount_usd", ""), "scheme": d.get("scheme", ""), **m})
    return pd.DataFrame(rows, columns=["case_id", "announced_date", "amount_usd", "scheme", "defendant",
                                       "kind", "city", "state", "mention", "role", "role_class", "role_family"])


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


def residuals(resolved: pd.DataFrame, min_amount: float = 0.0, states: list[str] | None = None) -> pd.DataFrame:
    """The research queue: ambiguous PEOPLE first (a docket settles them), then ambiguous
    small orgs, then unmatched mentions above the dollar floor; chains never appear."""
    r = resolved[resolved["status"].isin(["ambiguous", "unmatched"])].copy()
    if states:
        r = r[r["state"].isin([s.upper() for s in states])]
    r["amount_num"] = pd.to_numeric(r["amount_usd"], errors="coerce").fillna(0.0)
    r = r[(r["status"] == "ambiguous") | (r["amount_num"] >= float(min_amount))]
    r["priority"] = [0 if (s == "ambiguous" and k == "person") else 1 if s == "ambiguous" else 2
                     for s, k in zip(r["status"], r["kind"])]
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
    src = resolved["mention"].value_counts().to_dict() if len(resolved) else {}
    L += [f"- cases with at least one defendant mention: **{n_cases:,}**; mentions: **{len(resolved):,}**",
          "- mention status: " + ", ".join(f"{k} {v:,}" for k, v in by.items()),
          "- where mentions came from: " + ", ".join(f"{k} {v:,}" for k, v in src.items()),
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
        res_p = resolved[(resolved["status"] == "resolved") & (resolved["kind"] == "person")]
        if "role_agree" in resolved.columns and len(res_p):
            L += ["", "## Role coherence (resolved people: what the release says they are vs the NPI's taxonomy)", "",
                  "- role read from the text: " + ", ".join(f"{k} {v:,}" for k, v in res_p["role_class"].value_counts().items()),
                  "- role vs taxonomy: " + ", ".join(f"{k} {v:,}" for k, v in res_p["role_agree"].value_counts().items()),
                  f"- rejected by role: nonclinical_role {int((resolved['status'] == 'nonclinical_role').sum()):,} "
                  f"(recruiters / marketers / billers whose unique name hit is someone else), "
                  f"role_mismatch {int((resolved['status'] == 'role_mismatch').sum()):,} (text role contradicts the NPI taxonomy)"]
            hits = pd.to_numeric(res_p["name_national_hits"], errors="coerce").fillna(0)
            L += ["- name commonness (NPIs with the same first+last name across the indexed states): "
                  f"unique {int((hits <= 1).sum()):,}, 2-3 {int(((hits >= 2) & (hits <= 3)).sum()):,}, "
                  f"4-9 {int(((hits >= 4) & (hits <= 9)).sum()):,}, 10+ {int((hits >= 10).sum()):,}"]
            for st_, n in resolved[resolved["status"].isin(["nonclinical_role", "role_mismatch"])].groupby("status").head(6).iterrows():
                L.append(f"  - {n['status']}: {n['defendant']} ({n['state']}) role '{n['role']}' [{n['role_family'] or '-'}] vs NPI {n['npi_taxonomy_family'] or '-'}; case {n['case_id']}")
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
    ap.add_argument("--research-states", default=None,
                    help="comma list; restrict the research list to these states (e.g. the Southeast)")
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
    res = residuals(resolved, a.min_amount,
                    [x.strip() for x in a.research_states.split(",")] if a.research_states else None)
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
