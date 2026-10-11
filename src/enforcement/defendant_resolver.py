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
_DOCTORAL_SUFFIX = re.compile(r",?\s*\b(?:m\.?d|d\.?o|d\.?d\.?s|d\.?m\.?d|d\.?p\.?m|pharm\.?d|d\.?c|o\.?d|ph\.?d)\.?$", re.I)
_LEGAL_SUFFIX = re.compile(r"\b(?:llc|l\.l\.c|inc|incorporated|corp|corporation|co|company|ltd|"
                           r"lp|llp|pllc|pc|p\.c|pa|p\.a|plc|group|holdings)\b\.?", re.I)
_BUSINESS_WORDS = re.compile(r"\b(?:health|medical|clinic|pharmacy|pharma|rx|drugs?|hospice|home|care|services?|"
                             r"center|centre|laborator(?:y|ies)|labs?|hospital|physicians?|"
                             r"therapy|rehab|diagnostics?|imaging|dental|nursing|wellness|"
                             r"neurology|cardiology|oncology|radiology|urology|dermatology|orthopedics?|"
                             r"pediatrics?|psychiatry|anesthesia|podiatry|optometry|ophthalmology|"
                             r"pathology|surgery|surgical|urgent|"
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
# "Frank Monte (40, Valrico)"  /  "John Baird (55, Louisville, KY)"  /  "Acme LLC (Tampa, Florida)"
_PAREN_PATTERN = re.compile(
    rf"(?P<name>{_NAME}?)\s*\((?:(?:age[d]?\s+)?\d{{2}},\s*)?(?P<city>{_CITY}),\s*(?P<state>{_STATE_NAMES}|[A-Z]{{2}})\)")
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
                    kind = classify_defendant(n)
                    role, rclass, rfam = role_near(text, n) if kind == "person" else ("", "unknown", "")
                    found[key] = {"defendant": n, "kind": kind,
                                  "city": city or "", "state": state, "mention": source,
                                  "role": role, "role_class": rclass, "role_family": rfam}

    for pat, src in ((_OF_PATTERN, "of_city_state"), (_BASED_PATTERN, "city_state_based"),
                     (_LOCATED_PATTERN, "located_in"), (_PAREN_PATTERN, "paren_city_state")):
        for m in pat.finditer(text):
            raw_st = m.group("state").upper()
            st = _NAME_TO_ABBR.get(raw_st, raw_st if raw_st in _NAME_TO_ABBR.values() else "")
            if not st:
                continue
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
                        kind = classify_defendant(n)
                        role, rclass, rfam = role_near(text, n, primary=True) if kind == "person" else ("", "unknown", "")
                        found[key] = {"defendant": n, "kind": kind, "city": "",
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
    if not len(provider_name):                      # no individuals in the table (an org-only fixture)
        return pd.Series("", index=provider_name.index, dtype=object)
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
            # commonness for businesses too (2026-10-08): the evidence ladder reads
            # name_national_hits for every resolved row, and an org that never had
            # a count was graded 'weak' (hits 0) however distinctive its name
            national[k] = national.get(k, 0) + 1
    return people, orgs, national


CHAIN_MIN_CANDIDATES = 5   # an org name with this many NPIs in one state is a chain, not a target

# a headline descriptor, not a party: "Worcester Pharmacy Agrees to Pay" names Trang Pharmacy
# inside; "Plea" / "Settlement" are fragments. Such a name never matches an org by name.
_DESCRIPTOR_NOUN = re.compile(r"^(?:pharmacy|pharmacist|doctor|physician|dentist|clinic|hospital|nurse|man|woman|"
                              r"resident|company|owner|couple|provider|therapist|chiropractor|podiatrist|"
                              r"psychiatrist|business|firm|executive|lab|laboratory|practice|group|"
                              r"contractor|supplier|agency|facility|home|center)$", re.I)
_FRAGMENT = re.compile(r"^(?:plea|pleads?|settlement|settles?|agreed?|sentenced?|charged|indicted|"
                       r"convicted|guilty|verdict|complaint|united states|us|u\.s\.)$", re.I)


def is_descriptor_name(name: str) -> bool:
    toks = [t for t in re.split(r"\s+", str(name or "").strip(" .,")) if t]
    if not toks:
        return True
    if len(toks) == 1:      # "Plea" / "Pharmacy" are fragments; "Walgreens" is a brand and stays
        return bool(_FRAGMENT.match(toks[0]) or _DESCRIPTOR_NOUN.match(toks[0]))
    if _LEGAL_SUFFIX.search(name):
        return False
    if len(toks) == 2:
        return bool(_DESCRIPTOR_NOUN.match(toks[1]))
    # "Florida Medical Practice" / "Texas Dental Practice" / "New Jersey Doctor": a place name, an
    # adjective from the sector list, and a descriptor noun
    if len(toks) not in (3, 4) or not _DESCRIPTOR_NOUN.match(toks[-1]):
        return False
    lead = " ".join(toks[:-1]).upper()
    return bool(re.match(r"^(?:medical|dental|health|healthcare|home|pain|mental|behavioral|urgent|"
                         r"physical|area|based|local|county)$", toks[-2], re.I)) or lead in _NAME_TO_ABBR


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
    ("chiropractor", r"chiropractor"),
    ("pt_ot", r"physical therapist|occupational therapist|speech(?:[- ]language)?[- ]pathologist|respiratory therapist"),
    ("podiatrist", r"podiatrist|\bDPM\b"),
    ("optometrist", r"optometrist"),
    ("behavioral", r"psychologist|\bcounselor|social worker|\bLCSW\b|\bLPC\b|marriage and family therap|behavioral health therap|\btherapist"),
    ("aide", r"home health aide|personal care (?:aide|attendant|assistant)|certified nurs(?:e|ing) assistant|\bCNA\b|caregiver"),
    ("psychiatrist", r"psychiatrist"),     # a physician, but NPPES sometimes files them under a behavioral code
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
    r"straw owner|nominee owner|nominal owner|\bcapper\b|\brunner\b|\bkickback recipient|"
    r"\btechnician\b|\btech\b|head of marketing|marketing director|director of marketing|chief marketing|"
    r"non-provider|not a (?:medical|health ?care|licensed|healthcare) (?:provider|professional)|no medical (?:license|training)|"
    r"former employee|hospital employee|clinic employee|trucking", re.I)
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


def _find_name(t: str, n: str) -> tuple[int, str]:
    """Where ``n`` (or the closest printed form of it) sits in ``t``: exact, then 'First Last',
    then 'Dr. Last', then the bare surname when it occurs once AND is not the tail of a
    different person's name (a father and son share a surname). (-1, '') when absent."""
    i = t.lower().find(n.lower())
    if i >= 0:
        return i, t[i: i + len(n)]
    pk = person_key(n)
    first, last = (pk.split(" ")[0], pk.split(" ")[-1]) if pk else ("", "")
    for probe in ([f"{first} {last}"] if first else []) + ([f"dr. {last}", f"dr {last}"] if last else []):
        j = t.lower().find(probe.lower())
        if j >= 0:
            return j, t[j: j + len(probe)]
    if len(last) >= 4:
        occ = list(re.finditer(rf"\b{re.escape(last)}\b", t, re.I))
        if len(occ) == 1:
            m = occ[0]
            prev = re.search(r"([A-Z][a-z]+)\s+$", t[max(0, m.start() - 30): m.start()])
            mine = {tok.upper() for tok in re.split(r"[\s.]+", _HONORIFIC.sub("", n)) if tok}
            if not prev or prev.group(1).upper() in mine:
                return m.start(), m.group(0)
    return -1, ""


def role_near(text: str, name: str, primary: bool = False) -> tuple[str, str, str]:
    """(role_phrase, role_class, role_family) read from the clause around the mention of ``name``
    in ``text``. role_class: clinical / nonclinical / owner_exec / unknown. role_family may list
    several families joined by '|' when the text gives the person more than one ("a physician,
    dentist, and nurse practitioner"). The clause stops at the next sentence, a semicolon, a
    co-defendant introduced by "and <Name>" / "and nurse <Name>" / "and his sister", or a
    comma-separated next name, so a co-defendant's role is not read as this defendant's. A
    role word that itself introduces another capitalised name ("nurse Ngozi") is never this
    person's. When the name is absent from the text, the first sentence's opening words stand
    in (the summaries open with the primary defendant's role: "Physician/owner of ...")."""
    t = re.sub(r"\s+", " ", str(text or ""))
    n = str(name or "").strip()
    if not n:
        return "", "unknown", ""
    # a doctor by the NAME, before any fallback: a leading "Dr." or a trailing doctoral degree
    # ("Gerald Congdon, M.D." was rejected as a 'marketer' when only the prefix counted)
    is_dr = bool(_HONORIFIC.match(n) and n.lower().startswith("dr")) or bool(_DOCTORAL_SUFFIX.search(n))
    i, printed = _find_name(t, n)
    prot = lambda seg: _ABBREV_DOT.sub(lambda m: m.group(1) + "\x00", seg)     # "Dr. " is not a sentence end
    if i >= 0:
        # back to the last sentence end, and never across the previous list item ("(physician), " / ", and ")
        before = re.split(r"[.;]\s|\)\s*,\s*|,\s+and\s+", prot(t[max(0, i - 90): i]))[-1].replace("\x00", ".")
        after_raw = prot(t[i + len(printed): i + len(printed) + 140])
    elif primary:
        # the summaries open with the primary defendant's role ("Physician/owner of ...")
        before, after_raw = "", re.split(r"[.;]\s", prot(t[:_APPOSITIVE_ZONE + 1]))[0]
    else:
        return "", "unknown", ""
    after = re.split(r"(?<=[.;])\s|\s(?:and|while|along with|together with)\s+"
                     r"(?=(?:[a-z][a-z\-]+\s+){0,2}(?:Dr[.\x00]\s)?[A-Z][a-z]+\s[A-Z]|(?:his|her|their|the|a|an|two|three|co-defendants?)\b)"
                     r"|,\s+(?!(?:New|North|South|West|Rhode)\s[A-Z])(?=(?:Dr[.\x00]\s)?[A-Z][a-z]+\s[A-Z][a-z]+\s*[,(])",
                     after_raw)[0].replace("\x00", ".")
    if re.search(r"\bDr\.?\s*$", before):
        is_dr = True                              # "convicted Dr. Patrick Ifediba" with the Dr. in the text only
    hits: list[tuple[int, int, str, str, tuple]] = []    # (distance, -len, phrase, family, span)

    def _wend(seg: str, e: int) -> int:           # a stem match ("patholog") extends to the end of its word
        while e < len(seg) and seg[e].isalpha():
            e += 1
        return e

    for f, pat in _ROLE_CLINICAL:
        for m in pat.finditer(before):
            hits.append((len(before) - m.end(), -len(m.group(0)), m.group(0), f, ("b", m.start(), _wend(before, m.end()))))
        for m in pat.finditer(after):
            hits.append((m.start(), -len(m.group(0)), m.group(0), f, ("a", m.start(), _wend(after, m.end()))))
    # a phrase inside a longer phrase is the same words, not a second role ("pathologist" in "speech pathologist")
    hits = [h for h in hits if not any(o is not h and o[4][0] == h[4][0] and o[4][1] <= h[4][1] and h[4][2] <= o[4][2]
                                       and (o[4][2] - o[4][1]) > (h[4][2] - h[4][1]) for o in hits)]
    # a role phrase that itself introduces another capitalised name is that person's ("nurse practitioner Donna A.")
    hits = [h for h in hits if not (h[4][0] == "a" and re.match(r"\s+[A-Z][a-z]+\s+[A-Z]", after[h[4][2]: h[4][2] + 40]))]
    if hits:
        hits.sort()
        dist, _, phrase, fam, _sp = hits[0]
        if is_dr and dist > _APPOSITIVE_ZONE:
            return "Dr.", "clinical", "doctorate"   # the honorific on the name outranks a distant word
        fams = [fam] + [h[3] for h in hits[1:] if h[0] <= _APPOSITIVE_ZONE and h[3] not in (fam,)]
        seen: list[str] = []
        for x in fams:
            if x not in seen:
                seen.append(x)
        return phrase, "clinical", "|".join(seen)
    win = f"{before} {printed} {after}"
    if is_dr:
        m2 = _ROLE_OWNER.search(win)             # a doctor who also owned or marketed is still a provider
        return (m2.group(0), "owner_exec", "doctorate") if m2 else ("Dr.", "clinical", "doctorate")
    m = _ROLE_NONCLINICAL.search(win)
    if m:
        return m.group(0), "nonclinical", ""
    m2 = _ROLE_OWNER.search(win)
    if m2:
        return m2.group(0), "owner_exec", ""
    return "", "unknown", ""


def role_agreement(role_family: str, npi_family: str) -> str:
    """agree / disagree / unknown for the text role family (or '|'-joined families) against the
    NPI's taxonomy family: agree when the taxonomy is compatible with ANY listed family."""
    fams = [f for f in str(role_family or "").split("|") if f]
    if not fams or npi_family in ("", "other", "student"):
        return "unknown"
    return "agree" if any(npi_family in _ROLE_COMPAT.get(f, {f}) for f in fams) else "disagree"


def _middle_agrees(mids: list[str], cmid: str) -> bool:
    """Do the release's middle tokens agree with the NPPES middle name? An initial agrees with a
    name that starts with it; a full name agrees only with the same full name (or the matching
    initial in NPPES). 'Scott' vs NPPES 'STETSON' is a conflict although both start with S."""
    ctoks = [t for t in str(cmid or "").upper().split() if t]
    for m in mids:
        m = m.upper().strip(".")
        for c in ctoks:
            if len(m) == 1 and c.startswith(m):
                return True
            if len(m) > 1 and (m == c or (len(c) == 1 and m.startswith(c))):
                return True
    return False


def _middle_tokens(defendant: str) -> list[str]:
    s = _HONORIFIC.sub("", str(defendant or "").strip())
    s = _PERSON_SUFFIX.sub("", s).strip(" ,")
    toks = [t.strip(".") for t in re.split(r"\s+", re.sub(r"[^A-Za-z' .\-]", " ", s).upper()) if t.strip(".")]
    return toks[1:-1] if len(toks) > 2 else []


def candidate_person_npis(mentions: pd.DataFrame, provider_dim: pd.DataFrame) -> set:
    """Every NPI the person index holds for the mentions' name keys and states: the set whose
    NPPES middle names can settle a tie or contradict a unique hit. (The 2026-09-29 run loaded
    '0 of 0' because the first pass read a column resolve_defendants drops on its way out.)"""
    if not len(mentions):
        return set()
    pk = set(mentions["defendant"].map(person_key)) - {""}
    states = set(mentions["state"].fillna("").astype(str).str.upper()) - {""}
    people, _, _ = _index_nppes(provider_dim, pk, set(), states)
    return {str(c[0]) for cands in people.values() for c in cands}


def load_nppes_middle_names(nppes_path, npis: set) -> dict:
    """{npi: 'MIDDLE'} for the given NPIs, streamed from the raw NPPES data file with DuckDB
    (processed/provider_dim stores 'LAST, FIRST' only, so a middle-name conflict could never
    fire: 'Matthew David Carter' matched NPPES 'CARTER, MATTHEW R' unchallenged). A directory
    resolves to its newest npidata_pfile_*.csv."""
    import duckdb
    from pathlib import Path
    path = Path(nppes_path)
    if path.is_dir():
        files = sorted(path.glob("npidata_pfile_*.csv"))
        if not files:
            raise FileNotFoundError(f"no npidata_pfile_*.csv under {path}")
        path = files[-1]
    if not npis:
        return {}
    con = duckdb.connect()
    con.register("want", pd.DataFrame({"npi": sorted(str(n) for n in npis)}))
    rows = None
    for enc in ("utf-8", "latin-1"):     # the monthly NPPES file is UTF-8; a Latin-1 hint on it is refused
        try:
            rows = con.execute(
                f"SELECT n.\"NPI\", n.\"Provider Middle Name\" FROM read_csv_auto('{path.as_posix()}', header=true, "
                f"all_varchar=true, encoding='{enc}') n JOIN want w ON n.\"NPI\" = w.npi").fetchall()
            break
        except duckdb.InvalidInputException as e:
            if "encoded" not in str(e) or enc == "latin-1":
                con.close()
                raise
    con.close()
    return {str(a): str(b or "").strip().upper() for a, b in rows if b and str(b).strip()}


_last_fuzzy: dict = {}


def resolve_defendants(mentions: pd.DataFrame, provider_dim: pd.DataFrame,
                       middle_lookup: dict | None = None) -> pd.DataFrame:
    """mentions: case_id, defendant, kind, city, state (+ any other columns carried).
    Returns the same rows with npi, status, match_level, n_candidates, basis."""
    out = mentions.copy()
    for c in ("npi", "match_level", "npi_taxonomy_family", "npi_candidate"):
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
    if middle_lookup:
        # the raw NPPES middle name fills the blank provider_dim leaves for every person candidate
        for key, cands in people.items():
            people[key] = [(c[0], c[1], c[2] or middle_lookup.get(str(c[0]), ""), c[3]) for c in cands]

    def _lookup(kind, r, st):
        if kind == "person":
            k = r["_pk"]
            return k, (people.get((k, st)) if k else None)
        k = r["_ok"]
        if not distinctive_name_key(_normalize_name(pd.Series([k])).iloc[0]) or is_descriptor_name(r["defendant"]):
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
        out.at[i, "name_national_hits"] = int(national.get(key, 0))
        if len(uniq) == 1 and out.at[i, "kind"] == "person" and not level.endswith("_middle"):
            # "Matthew David Carter" is not NPPES "MATTHEW R CARTER": a middle name in the release
            # that contradicts the candidate's middle name is the wrong person (5 of 10 wrong_npi
            # verdicts on the 2026-09-29 verification). Silence on either side is not a conflict.
            mids = _middle_tokens(r["defendant"])
            cmid = next((c[2] for c in cands if c[0] == uniq[0]), "")
            if mids and cmid and not _middle_agrees(mids, cmid):
                out.at[i, "status"] = "middle_conflict"
                out.at[i, "npi_candidate"] = uniq[0]
                continue
        if len(uniq) == 1:
            rclass = str(r.get("role_class") or "unknown")
            rfam = str(r.get("role_family") or "")
            npi_fam = next((c[3] for c in cands if c[0] == uniq[0]), "")
            out.at[i, "npi_taxonomy_family"] = npi_fam
            if out.at[i, "kind"] == "person" and rclass == "nonclinical":
                # a recruiter / marketer / biller has no NPI; the unique name hit is someone else
                out.at[i, "status"] = "nonclinical_role"
                out.at[i, "npi_candidate"] = uniq[0]
                continue
            agree = role_agreement(rfam, npi_fam) if out.at[i, "kind"] == "person" else "n/a"
            out.at[i, "role_agree"] = agree
            if agree == "disagree":
                out.at[i, "status"] = "role_mismatch"       # text says pharmacist, NPI is a chiropractor
                out.at[i, "npi_candidate"] = uniq[0]
                out.at[i, "match_level"] = level
                continue
            out.at[i, "npi"] = uniq[0]
            out.at[i, "match_level"] = level
            out.at[i, "status"] = "resolved"
        elif out.at[i, "kind"] == "org" and len(uniq) >= CHAIN_MIN_CANDIDATES:
            out.at[i, "status"] = "chain_org"        # the org_billing tier covers enrolled chains
        else:
            out.at[i, "status"] = "ambiguous"
    # the fuzzy business tier on what the exact tier left unmatched (status
    # resolved_fuzzy: held out of training until verified)
    out["matched_org_key"] = ""
    out, fz = fuzzy_resolve_orgs(out, provider_dim)
    _last_fuzzy.update(fz)
    # SIBLING VETO: a case usually mentions the same person twice (the headline defendant
    # without a middle name or role, and the located mention "Matthew David Carter, 45, of
    # Ely, a rehabilitation technician"). A rejection on either mention (middle_conflict,
    # nonclinical_role, role_mismatch) vetoes the sibling that resolved to the same NPI on
    # the thinner evidence; the 2026-09-29 verification found five middle-name conflicts
    # still in the label through their headline mention.
    out["veto_from"] = ""
    rej = out[out["status"].isin(["middle_conflict", "nonclinical_role", "role_mismatch"])]
    veto = {}
    for _, d in rej.iterrows():          # iterrows: itertuples renames '_pk' (leading underscore)
        veto.setdefault((str(d["case_id"]), str(d["_pk"])), (str(d["status"]), str(d.get("npi_candidate") or "")))
    if veto:
        for i, r in out[out["status"] == "resolved"].iterrows():
            v = veto.get((str(r["case_id"]), str(r["_pk"])))
            if v and (not v[1] or v[1] == str(r["npi"])):
                out.at[i, "npi_candidate"] = str(r["npi"])
                out.at[i, "npi"] = ""
                out.at[i, "status"] = v[0]
                out.at[i, "veto_from"] = "sibling mention"
    # a role_mismatch on one release is overruled when another release resolved the SAME person
    # to the SAME NPI with an agreeing role ("a Cave City physician" vs the dentist NPI that a
    # fuller release lists as "physician, dentist, and nurse practitioner")
    if "npi_candidate" in out.columns:
        ok = out[(out["status"] == "resolved") & (out["role_agree"] == "agree")]
        agreed = set(zip(ok["_pk"], ok["state"].fillna("").astype(str).str.upper(), ok["npi"]))
        for i, r in out[out["status"] == "role_mismatch"].iterrows():
            if (r["_pk"], str(r.get("state") or "").upper(), r["npi_candidate"]) in agreed:
                out.at[i, "npi"] = r["npi_candidate"]
                out.at[i, "npi_candidate"] = ""
                out.at[i, "status"] = "resolved"
                out.at[i, "role_agree"] = "agree_other_mention"
    # npi_candidate stays on the row (blank when resolved): the rejected candidate is what the
    # quarantine file and a human re-check need to see
    out = out.drop(columns=["_pk", "_ok"])
    out["basis"] = "resolved_defendant"
    return out


# ---------------------------------------------------------------------------
# FUZZY BUSINESS TIER (label recall, 2026-10-10). The exact tier lands only on an
# enrolled organization's exact normalized name; LABEL_AUDIT found 99% of usable
# DOJ cases resolve to no scored NPI. A release writes "Sunrise Home Health of
# Miami" where NPPES says "SUNRISE HOME HEALTH SERVICES LLC". This tier matches
# on the DISTINCTIVE name tokens (generic / institutional vocabulary dropped)
# within the mention's state: every distinctive token of the defendant must
# appear in the candidate's name, the overlap must be at least half of the
# longer side, and exactly one organization key may remain after the city
# narrows the field. The result is status "resolved_fuzzy" with match_level
# fuzzy_name_state / fuzzy_name_city_state: it is NOT "resolved", so case_labels
# (training) and the strict export ignore it until the research agent verifies
# a sample (label_verification), when it can be promoted. Prosecuted providers
# are labels, never leads.
FUZZY_MIN_JACCARD = 0.5


# The fuzzy tier's stoplist is NARROWER than the graph's merge-key vocabulary
# (clean_data.GENERIC_NAME_TOKENS drops SUNRISE / PEDIATRIC / MERCY so two local
# clinics never fuse into one canonical org). Here the question is the opposite,
# "which enrolled business IS this defendant", and those words identify it;
# in-state uniqueness is the guard. Only pure filler and legal suffixes drop.
from src.enforcement.case_db import NAME_STOP_TOKENS as FUZZY_STOP_TOKENS


def _distinctive_tokens(key: str) -> frozenset:
    return frozenset(t for t in str(key or "").lower().split() if len(t) >= 3 and t not in FUZZY_STOP_TOKENS)


# TIGHTENED 2026-10-10 after the first fuzzy verification batch (108 rows, 26% confirmed:
# 45 wrong_npi, 33 wrong_person). Three failure classes, each now a rule:
#   1. HEADLINE DESCRIPTORS reached the tier ("Telehealth Company", "Medical Director",
#      "Tampa Bay Autism Service Provider", "Fresno-Based Community Health System"): a
#      mention whose every token is a place word, a sector word or a legal tail names
#      nobody and is never fuzzy-matched (`is_headline_descriptor`).
#   2. SURNAME FRAGMENTS ("ILYAYEV", "RIBEIRO", "Shtindler") matched the physician's
#      practice ("ALEKSANDR ILYAYEV MEDICAL PC"): a one-word mention never enters the tier.
#   3. ONE-TOKEN CONTAINMENT ("Target Corporation" -> TARGET PHARMACY, "Humana Inc" ->
#      HUMANA HEALTH PLAN, "Baxter International" -> BAXTER SUPPLY SERVICES): the
#      candidate may carry NO distinctive token the mention lacks when the mention has
#      one distinctive token, and at most ONE extra when it has two or more, and that
#      extra is allowed only if the mention's city agrees with the candidate's (or the
#      mention names no city). A legal-tail difference (Inc vs P.A. vs PLLC) still
#      matches: the tails are stripped on both sides.
_PLACE_WORDS = frozenset({"bay", "area", "based", "metro", "metropolitan", "greater", "region",
                          "regional", "county", "north", "south", "east", "west", "central",
                          "downtown", "valley", "coast", "coastal"})
_DESCRIPTOR_VOCAB = re.compile(
    r"^(?:telehealth|telemedicine|manufacturing|distribution|director|assistant|consultant|consulting|"
    r"office|offices|chronic|disease|diseases|autism|independent|testing|disability|disabilities|"
    r"radiation|surgeon|surgeons|women|womens|men|mens|children|childrens|family|community|county|"
    r"metro|metropolitan|area|based|primary|specialty|specialist|specialists|diagnostic|facility|"
    r"northern|southern|eastern|western|investors|investor|owners|ambulances|radiology|"
    r"facilities|agency|agencies|provider|providers|company|companies|corporation|group|holdings|"
    r"operator|operators|owner|owners|employee|employees|chain|plan|plans|insurer|insurance|vision|"
    r"physical|mental|behavioral|occupational|speech|senior|seniors|living|assisted|residential|"
    r"ambulatory|outpatient|inpatient|acute|post|long|term|skilled|supplier|suppliers|device|devices|"
    r"product|products|pharmaceutical|pharmaceuticals|biotech|technology|technologies|science|sciences|"
    r"resource|resources|institute|foundation|university|college|school|district|city|state|federal|"
    r"public|private|nonprofit|executive|executives|staffing|billing|marketing|marketer|recruiter|"
    r"ambulance|transportation|lab|labs|laboratory|laboratories|sleep|eye|skin|foot|ankle|"
    r"therapy|therapist|therapists|treatment|wellness|hospice|rehabilitation|rehab|chiropractic|"
    r"dentist|dentists|physician|physicians|doctor|doctors|nurse|nurses|pharmacist|pharmacists|"
    r"pharmacy|pharmacies|clinic|clinics|hospital|hospitals|practice|practices|services|service|"
    r"system|systems|network|networks|partners|associates|management|solutions|enterprises|"
    r"health|healthcare|medical|medicine|care|home|center|centers|centre)$", re.I)


_FUZZY_TAILS = frozenset({"inc", "incorporated", "llc", "llp", "ltd", "lp", "corp", "corporation", "co",
                          "company", "companies", "pllc", "plc", "opco", "dba", "et", "al", "the", "and",
                          "for", "of", "de", "la", "del", "los", "las", "les"})
_SPLIT_WORDS = {"healthcare": ("health", "care"), "homecare": ("home", "care"), "medcare": ("med", "care")}


def _name_words(key: str) -> frozenset:
    """Every word of a normalized organization name except legal tails and connectors;
    'healthcare' reads as 'health care' so the spelling never decides a match."""
    out: set = set()
    for w in str(key or "").lower().split():
        if len(w) < 3 or w in _FUZZY_TAILS:
            continue
        out.update(_SPLIT_WORDS.get(w, (w,)))
    return frozenset(out)


def _place_words(city: str, state: str) -> set:
    place = set(str(city or "").lower().split()) | {str(state or "").lower()}
    st = str(state or "").upper()
    for n, ab in _NAME_TO_ABBR.items():
        if ab == st:
            place |= set(n.lower().split())
    return place | _PLACE_WORDS


def is_headline_descriptor(name: str, city: str = "", state: str = "") -> bool:
    """True when the mention names nobody: every token is a place word, a sector /
    descriptor word, a filler token or a legal tail ("Florida Pediatric Practice and
    Physicians", "Chronic Disease Health Care Provider", "Distribution Services, Inc.").
    A single distinctive token ("Lakeside Pediatric Therapy") keeps the mention."""
    toks = [t for t in norm_org_name(name).lower().split() if t]
    if not toks:
        return True
    place = _place_words(city, state)
    # "Fresno-Based", "Dallas-Area", "Tampa Bay-Based": the words before the hyphen are
    # a place by construction, whether or not the mention carries a city
    for m in re.finditer(r"([A-Za-z.']+(?:\s[A-Za-z.']+)?)-(?:based|area)\b", str(name or ""), re.I):
        place |= set(m.group(1).lower().replace(".", "").split())
    for t in toks:
        if (len(t) <= 2 or t in place or t in FUZZY_STOP_TOKENS or _DESCRIPTOR_VOCAB.match(t)
                or _BUSINESS_WORDS.fullmatch(t) or _DESCRIPTOR_NOUN.match(t)):
            continue
        return False
    return True


def _fuzzy_org_index(provider_dim: pd.DataFrame, states: set, universe: set) -> dict:
    """{state: [(org_key, tokens, npi, city), ...]} for organizations in `states`
    whose name shares at least one token with `universe` (the unmatched mentions'
    distinctive tokens), so the index stays small on the full 8M-row NPPES table."""
    cols = [c for c in ("npi", "provider_name", "name_key", "entity_type", "addr_state", "addr_city")
            if c in provider_dim.columns]
    p = provider_dim[cols]
    st = p["addr_state"].fillna("").astype(str).str.upper().str.strip() if "addr_state" in p else pd.Series("", index=p.index)
    p = p[st.isin(states)]
    if not len(p):
        return {}
    st = st.loc[p.index]
    et = p["entity_type"].fillna("").astype(str).str.strip() if "entity_type" in p else pd.Series("", index=p.index)
    name = p["provider_name"].fillna("").astype(str) if "provider_name" in p else pd.Series("", index=p.index)
    is_org = ~((et == "1") | ((et == "") & name.str.contains(",")))
    key = (p["name_key"].fillna("").astype(str) if "name_key" in p.columns else name.map(norm_org_name))[is_org]
    toks = key.str.lower().str.split()
    expl = toks.explode()
    expl = expl[expl.isin(universe)]
    hit_idx = expl.index.unique()
    out: dict = {}
    city = p["addr_city"].fillna("").astype(str).str.upper().str.strip() if "addr_city" in p else pd.Series("", index=p.index)
    npi = p["npi"].astype(str)
    for idx in hit_idx:
        k = key[idx]
        t = _distinctive_tokens(k)
        if not t:
            continue
        out.setdefault(st[idx], []).append((k, t, npi[idx], city[idx]))
    return out


def fuzzy_resolve_orgs(out: pd.DataFrame, provider_dim: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Second pass over business mentions the exact tier left `unmatched`."""
    out = out.copy()
    stats = {"tried": 0, "resolved_fuzzy": 0, "ambiguous_fuzzy": 0, "chain_org_fuzzy": 0}
    cand_rows = out[(out["status"] == "unmatched") & (out["kind"] == "org")]
    if not len(cand_rows):
        return out, stats
    def _mention_tokens(r) -> frozenset:
        # a release writes "Sunrise Home Health of Miami": the city is where the
        # business is, not part of its registered name; drop the mention's own
        # city / state words (and "Bay" / "Area" / "Based") before the containment test
        name = str(r["defendant"])
        key = norm_org_name(name)
        if len(key.split()) < 2:                                   # rule 2: a one-word mention
            return frozenset()
        if is_headline_descriptor(name, r.get("city") or "", r.get("state") or ""):
            return frozenset()                                     # rule 1: names nobody
        t = set(_distinctive_tokens(key))
        return frozenset(t - _place_words(r.get("city") or "", r.get("state") or ""))
    dtoks = {i: _mention_tokens(r) for i, r in cand_rows.iterrows()}
    dtoks = {i: t for i, t in dtoks.items() if t}
    if not dtoks:
        return out, stats
    states = {str(out.at[i, "state"] or "").upper() for i in dtoks} - {""}
    universe = set().union(*dtoks.values())
    index = _fuzzy_org_index(provider_dim, states, universe)
    for i, d in dtoks.items():
        st = str(out.at[i, "state"] or "").upper()
        rows = index.get(st) or []
        stats["tried"] += 1
        city = str(out.at[i, "city"] or "").upper()
        place = _place_words(out.at[i, "city"] or "", st)
        words_m = _name_words(norm_org_name(str(out.at[i, "defendant"]))) - place
        hits = []
        for (k, t, n, c) in rows:
            tt = t - place
            if not d <= tt:
                continue
            # rule 4 (batch 2, 2026-10-11): EVERY word of the mention that is not a legal
            # tail must appear in the candidate, stop words included. "Kindred
            # Healthcare" is not KINDRED HOSPITALS EAST, "Vibra Healthcare" is not VIBRA
            # CLINICS, "Medco Health Solutions" is not MEDCO LLC: the stop list had
            # collapsed each mention to one brand word and the in-state index found a
            # random local business carrying it (23 of 39 wrong_npi).
            if not words_m <= (_name_words(k) - place):
                continue
            extras = tt - d
            if not extras:
                hits.append((k, t, n, c))
            elif len(d) >= 2 and len(extras) == 1 and (not city or c == city):
                hits.append((k, t, n, c))                          # rule 3: one extra, city agrees
        if not hits:
            continue
        keys = {h[0] for h in hits}
        level = "fuzzy_name_state"
        if len(keys) > 1 and city:
            narrowed = [h for h in hits if h[3] == city]
            if narrowed:
                hits, keys, level = narrowed, {h[0] for h in narrowed}, "fuzzy_name_city_state"
        out.at[i, "n_candidates"] = len({h[2] for h in hits})
        if len(keys) > 1:
            out.at[i, "status"] = "ambiguous_fuzzy"
            out.at[i, "npi_candidate"] = ";".join(sorted(keys)[:4])
            stats["ambiguous_fuzzy"] += 1
            continue
        npis = sorted({h[2] for h in hits})
        if len(npis) >= CHAIN_MIN_CANDIDATES:
            out.at[i, "status"] = "chain_org_fuzzy"
            stats["chain_org_fuzzy"] += 1
            continue
        out.at[i, "status"] = "resolved_fuzzy"
        out.at[i, "match_level"] = level
        out.at[i, "npi"] = npis[0] if len(npis) == 1 else ""
        out.at[i, "npi_candidate"] = ";".join(npis) if len(npis) > 1 else ""
        out.at[i, "matched_org_key"] = next(iter(keys))
        stats["resolved_fuzzy"] += 1
    return out, stats


RESEARCH_STATUSES = ("research_low", "research_no_npi")


def carry_research(new: pd.DataFrame, prior: pd.DataFrame | None) -> tuple[pd.DataFrame, int]:
    """A resolver rerun rebuilds the table from the case text; the research answers folded
    into the PRIOR table (defendant_research --poll) would be lost. Re-apply them by
    (case_id, defendant): a high/medium research NPI wins over anything but a name
    resolution to the same NPI; a low / no-NPI verdict applies when the name match found
    nothing. Returns (table, rows carried)."""
    if prior is None or not len(prior) or "research_confidence" not in prior.columns:
        return new, 0
    pr = prior[prior["research_confidence"].fillna("").astype(str).str.strip() != ""]
    if not len(pr):
        return new, 0
    out = new.copy()
    for c in ("task_id", "research_confidence"):
        if c not in out.columns:
            out[c] = ""
    key = lambda df: list(zip(df["case_id"].astype(str), df["defendant"].astype(str)))
    idx = {k: i for i, k in zip(pr.index, key(pr))}
    n = 0
    for i, k in zip(out.index, key(out)):
        j = idx.get(k)
        if j is None:
            continue
        r = pr.loc[j]
        conf = str(r.get("research_confidence") or "")
        rnpi = str(r.get("npi") or "")
        st = str(r.get("status") or "")
        out.at[i, "task_id"] = str(r.get("task_id") or "")
        out.at[i, "research_confidence"] = conf
        if st == "resolved" and str(r.get("match_level") or "").startswith("research_") and rnpi:
            if out.at[i, "status"] == "resolved" and out.at[i, "npi"] == rnpi:
                continue                                     # the name match already agrees
            out.at[i, "npi"], out.at[i, "status"], out.at[i, "match_level"] = rnpi, "resolved", str(r["match_level"])
            n += 1
        elif st in RESEARCH_STATUSES and out.at[i, "status"] != "resolved":
            out.at[i, "status"] = st
            n += 1
    return out, n


def apply_verification(out: pd.DataFrame, results: pd.DataFrame | None) -> tuple[pd.DataFrame, dict]:
    """Promote / reject fuzzy rows from label_verification verdicts (verification_results.csv:
    case_id, defendant or npi, verdict). A 'confirmed' verdict on a resolved_fuzzy row makes it
    'resolved' (match_level <level>_verified: it enters training and the strict export); a
    wrong_person / wrong_npi / not_a_provider verdict makes it 'fuzzy_rejected'. Exact-tier
    rows are untouched here (their verdicts are read by the operator)."""
    stats = {"promoted": 0, "rejected": 0, "carried": 0}
    if results is None or not len(results) or "verdict" not in results.columns:
        return out, stats
    out = out.copy()
    r = results.copy()
    r["verdict"] = r["verdict"].fillna("").astype(str).str.lower()
    r["npi"] = r["npi"].fillna("").astype(str).str.strip() if "npi" in r.columns else ""
    by_def = {(str(a), str(b)): v for a, b, v in zip(r.get("case_id", ""), r.get("defendant", ""), r["verdict"])
              if str(b)}
    by_npi = {(str(a), str(b)): v for a, b, v in zip(r.get("case_id", ""), r["npi"], r["verdict"])
              if str(b)}
    for i, row in out[out["status"] == "resolved_fuzzy"].iterrows():
        v = by_def.get((str(row["case_id"]), str(row["defendant"]))) or by_npi.get((str(row["case_id"]), str(row["npi"])))
        if not v or v == "pending":
            continue
        if v == "confirmed":
            out.at[i, "status"] = "resolved"
            out.at[i, "match_level"] = str(row["match_level"]) + "_verified"
            stats["promoted"] += 1
        elif v in ("wrong_person", "wrong_npi", "not_a_provider"):
            out.at[i, "status"] = "fuzzy_rejected"
            out.at[i, "npi_candidate"] = str(row["npi"])
            out.at[i, "npi"] = ""
            stats["rejected"] += 1
    # A confirmed verdict is per-row EVIDENCE and outlives the matching rule that produced
    # the candidate (2026-10-11: the tightened tier no longer matches 17 of the 47 mentions
    # the first batch confirmed, so they stopped promoting). A confirmed (case, defendant,
    # NPI) whose mention is still unresolved is promoted on the verdict itself, at
    # match_level fuzzy_verified (strength moderate on the strict export's ladder).
    from src.attempt_2.clean_data import is_valid_npi
    conf = r[(r["verdict"] == "confirmed") & r["npi"].map(lambda n: bool(n) and is_valid_npi(n))]
    conf_npi = {(str(a), str(b)): n for a, b, n in zip(conf.get("case_id", ""), conf.get("defendant", ""), conf["npi"])
                if str(b)}
    if conf_npi and "npi_candidate" not in out.columns:
        out["npi_candidate"] = ""
    for i, row in out[out["status"] != "resolved"].iterrows():
        n = conf_npi.get((str(row["case_id"]), str(row["defendant"])))
        if not n:
            continue
        out.at[i, "status"] = "resolved"
        out.at[i, "npi"] = n
        out.at[i, "npi_candidate"] = ""
        out.at[i, "match_level"] = "fuzzy_verified"
        stats["carried"] += 1
    return out, stats


FUZZY_TIER_RETIRED = True   # 2026-10-11: three verified batches read 26% / 26% / 39%


def retire_unverified_fuzzy(out: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """THE FUZZY TIER IS RETIRED (2026-10-11). Three Manus batches graded it at 26, 26 and
    39 percent confirmed, and the survivors were the same two shapes every time: a parent
    corporation landing on a differently named local business (Prime Healthcare Services
    -> PRIME CARE HEALTH SERVICES, Encompass Health Corporation -> ENCOMPASS HEALTHCARE
    GROUP) and a headline descriptor no word rule catches ("O.C.-Based Health Care
    Organization", "Berks County Group"). Those are ownership and research questions, not
    name-matching ones. The tier still RUNS so that ``apply_verification`` can promote the
    47 rows a verdict confirmed (they stay 'resolved' on every rerun); every row it leaves
    as 'resolved_fuzzy' is demoted here to 'fuzzy_retired' with the candidate kept in
    ``npi_candidate``, so nothing unverified ever reaches training or the strict export.
    ``--fuzzy`` on the CLI keeps the unverified rows (for a review file only)."""
    out = out.copy()
    m = out["status"] == "resolved_fuzzy"
    n = int(m.sum())
    if n:
        out.loc[m, "npi_candidate"] = out.loc[m, "npi"].astype(str)
        out.loc[m, "npi"] = ""
        out.loc[m, "status"] = "fuzzy_retired"
    return out, n


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
    fz = resolved[resolved["status"] == "resolved_fuzzy"] if len(resolved) else resolved
    if len(resolved):
        amt = pd.to_numeric(resolved.get("amount_usd", pd.Series(index=resolved.index)), errors="coerce").fillna(0)
        fz_cases = fz["case_id"].nunique()
        exact_cases = set(resolved[resolved["status"] == "resolved"]["case_id"])
        new_cases = set(fz["case_id"]) - exact_cases
        new_dollars = float(amt[resolved["case_id"].isin(new_cases)].groupby(resolved["case_id"]).max().sum()) if new_cases else 0.0
        L += ["", "## Fuzzy business tier (recall candidates, NOT in training until verified)", "",
              f"- mentions resolved by distinctive-token match within the state: **{len(fz):,}** "
              f"({fz_cases:,} cases, {len(new_cases):,} of them with NO exact-tier NPI; "
              f"${new_dollars:,.0f} of settlement dollars newly reachable)",
              f"- ambiguous (several organizations fit): {int((resolved['status'] == 'ambiguous_fuzzy').sum()):,}; "
              f"chains (5+ NPIs under the matched name): {int((resolved['status'] == 'chain_org_fuzzy').sum()):,}",
              "- promotion path: label_verification samples these rows (strength weak) first; a confirmed row is "
              "re-stamped 'resolved' by the next resolver run (carry_research) and enters the label."]
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
    ap.add_argument("--nppes-file", default=None,
                    help="raw NPPES data file (npidata_pfile_*.csv) or its folder: supplies the middle names "
                         "provider_dim lacks, so a release middle name that contradicts NPPES is caught")
    ap.add_argument("--fuzzy", action="store_true",
                    help="keep UNVERIFIED fuzzy-tier rows as resolved_fuzzy (for a review file only); "
                         "default: the tier is retired and such rows are demoted to fuzzy_retired")
    ap.add_argument("--verification", default=None, nargs="+",
                    help="label_verification's verification_results.csv (one or more files: every "
                         "batch's verdicts): confirmed verdicts promote resolved_fuzzy rows to resolved; "
                         "wrong_* verdicts reject them")
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
    # a provider with no address state can never match a "<name>, of <city>, <state>"
    # mention; the Medicaid enrollment file's primary state fills the blank (2026-10-10)
    feats_p = Path(a.provider_dim).with_name("medicaid_enrollment_features.parquet")
    if feats_p.exists() and "addr_state" in pdim.columns:
        from src.ingest_cms.medicaid_enrollment import fill_state
        pdim, n_fill = fill_state(pdim, pd.read_parquet(feats_p, columns=["npi", "mcd_primary_state"]))
        print(f"  provider state filled from {feats_p.name} for {n_fill:,} NPIs with no address state")
    mentions = mentions_from_case_db(cdb)
    middle = None
    if a.nppes_file:
        want = candidate_person_npis(mentions, pdim)                  # every person candidate, ties included
        middle = load_nppes_middle_names(a.nppes_file, want)
        print(f"  NPPES middle names loaded for {len(middle):,} of {len(want):,} candidate NPIs from {a.nppes_file}")
    resolved = resolve_defendants(mentions, pdim, middle_lookup=middle)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():                                   # keep the research answers folded into the prior table
        resolved, n_carried = carry_research(resolved, pd.read_parquet(out))
        if n_carried:
            print(f"  carried {n_carried:,} research verdicts forward from the prior table")
    fz = _last_fuzzy
    print(f"  fuzzy business tier: {fz.get('tried', 0):,} unmatched org mentions tried -> "
          f"{fz.get('resolved_fuzzy', 0):,} resolved_fuzzy (held out of training until verified), "
          f"{fz.get('ambiguous_fuzzy', 0):,} ambiguous, {fz.get('chain_org_fuzzy', 0):,} chains")
    if a.verification:
        # several batches' verdicts at once (batch 1 promoted the first 28 fuzzy rows, batch 2
        # grades the tightened tier): every file is read and concatenated
        resolved, vs = apply_verification(resolved, pd.concat([pd.read_csv(p, dtype=str) for p in a.verification],
                                                              ignore_index=True))
        print(f"  verification verdicts applied to fuzzy rows: {vs['promoted']:,} promoted to resolved, "
              f"{vs['rejected']:,} rejected, {vs['carried']:,} confirmed mentions promoted on the verdict alone "
              f"(the tier no longer matches them)")
    if not a.fuzzy:
        resolved, n_ret = retire_unverified_fuzzy(resolved)
        print(f"  fuzzy tier RETIRED (2026-10-11: 26% / 26% / 39% confirmed over three batches): "
              f"{n_ret:,} unverified fuzzy rows demoted to fuzzy_retired; verified promotions kept")
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
