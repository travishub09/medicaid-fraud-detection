"""
label_gates.py — the four precision gates the 2026-10-20 verification read called for.

The first live lane cycle verified 104 label rows: 96 confirmed, 5 wrong_npi, 3
wrong_person. Each wrong row was ONE of four shapes, and each shape is a rule here
(a rule, never a guess; a refused row is quarantined with the reason):

1. INDEX-PAGE URL (``is_index_url``): "Live Care Inc." was keyed to
   usao-mdfl/pr, the office's press-release INDEX, not a release. A case row whose
   source is a listing page is not evidence of anything: never a label.
2. NON-NPI OCCUPATION (``non_npi_role``): a Mississippi list row for a nurse's aide
   (no NPI, ever) name-matched the one family nurse practitioner of that name in the
   state. An occupation that never holds an NPI never resolves on a name.
3. RESEARCH NAME AGREEMENT (``name_agrees``): a researched NPI must carry the
   defendant's name in NPPES. "Mika Kamissa Harris" -> MIKALA HARRIS (a different
   first name), "Live Care Inc., Venice" -> LIVECARE FLORIDA P.A., Tampa (an extra
   distinctive word and a different city), "Dermatology Healthcare of Florida" ->
   NATIONAL DERMATOLOGY HEALTHCARE (a mention word missing) all fail; a solo
   professional corporation named for its physician ("Martin E. Cutler, M.D., P.C."
   -> MARTIN E CUTLER) agrees: the doctor is the P.C.'s operator.
4. DEFENDANT ON THE PAGE (``name_in_page``): a harvested state case named Lokesh
   Tantuwaya on a California AG release about a different physician. A research-
   harvested defendant must appear on the page the row cites, or the row is
   quarantined (``defendant_not_on_page``); an unreachable page is "unknown", kept
   but never silently promoted.

Nothing here touches a model; every gate feeds a quarantine file the operator reads.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from src.entity_graph.resolve_entities import norm_org_name

# ---- 1. index pages ---------------------------------------------------------------------
_INDEX_TAILS = {"pr", "press-releases", "press-release", "news", "newsroom", "press-room", "pressroom",
                "press", "releases", "enforcement", "fraud", "media", "news-releases", "newsreleases",
                "archive", "archives", "index.html", "index.htm", "index.php", "default.aspx"}


def is_index_url(url: str) -> bool:
    """True when the URL names a listing page rather than one announcement: the path
    is empty or every segment is a section word ('/usao-mdfl/pr', '/news',
    '/fraud/enforcement/'). A slug segment anywhere ('/pr/north-shore-...') keeps it."""
    u = str(url or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        return False
    path = urlparse(u.split("#", 1)[0]).path
    segs = [s for s in path.split("/") if s]
    if not segs:
        return True
    for s in segs:
        sl = s.lower()
        if sl in _INDEX_TAILS:
            continue
        if re.fullmatch(r"usao-[a-z]{2,6}|opa|oig|[a-z]{2}|\d{4}|\d{1,2}", sl):   # office / section / year segments
            continue
        return False
    return True


# ---- 2. occupations that never hold an NPI ---------------------------------------------
NON_NPI_ROLE_RE = re.compile(
    r"\b(nurse'?s? ?aide|nursing aide|nurse aide|aide|cna|certified nurs(e|ing) assistant|"
    r"personal care (attendant|assistant|aide|worker)|pca|home health aide|hha|direct support|dsp|"
    r"caregiver|care giver|companion|sitter|homemaker|attendant|orderly|housekeep\w*|"
    r"driver|transport(ation)? (driver|provider|worker)|cook|dietary aide|"
    r"receptionist|front desk|office manager|office worker|clerk|biller|billing|coder|"
    r"medical assistant|individual worker|unlicensed|non-?licensed|private citizen|owner/?operator)\b",
    re.I)
# words that mean an NPI-holding clinician even when an aide word sits nearby ("nurse practitioner")
_NPI_ROLE_RE = re.compile(r"\b(physician|md|do|nurse practitioner|np|physician assistant|pa-c|pharmacist|"
                          r"dentist|dds|dmd|psychologist|therapist|chiropractor|optometrist|podiatrist|"
                          r"registered nurse|rn|lpn|lvn|counselor|social worker|lcsw|audiologist|"
                          r"dietitian|midwife|crna)\b", re.I)


def non_npi_role(text: str) -> bool:
    """True when the row's own words describe an occupation that never holds an NPI and
    no NPI-holding profession is named beside it."""
    t = str(text or "")
    if not t.strip():
        return False
    return bool(NON_NPI_ROLE_RE.search(t)) and not _NPI_ROLE_RE.search(t)


# ---- 3. the researched NPI carries the defendant's name --------------------------------
_CREDENTIALS = re.compile(r"\b(m\.?d\.?|d\.?o\.?|p\.?c\.?|p\.?a\.?|pllc|llc|inc|corp|dds|dmd|phd|"
                          r"np|rn|lpn|dpm|od|crna|pa-c|jr|sr|ii|iii|iv|esq|dr|mr|mrs|ms)\b\.?", re.I)
_TAILS = frozenset({"inc", "incorporated", "llc", "llp", "ltd", "lp", "corp", "corporation", "co",
                    "company", "companies", "pllc", "plc", "pc", "pa", "the", "and", "of", "for", "dba"})
_SPLIT = {"healthcare": ("health", "care"), "homecare": ("home", "care"), "medcare": ("med", "care"),
          "livecare": ("live", "care")}


def _person_tokens(name: str) -> list[str]:
    s = _CREDENTIALS.sub(" ", str(name or ""))
    s = re.sub(r"[^A-Za-z' -]", " ", s)
    toks = [t.strip("'-") for t in s.split()]
    return [t.lower() for t in toks if len(t.strip("'-")) > 1]


def _org_words(name: str) -> set:
    out: set = set()
    for w in norm_org_name(name).lower().split():
        if len(w) < 3 or w in _TAILS:
            continue
        out.update(_SPLIT.get(w, (w,)))
    return out


def name_agrees(defendant: str, kind: str, nppes_name: str,
                nppes_city: str = "", mention_city: str = "") -> tuple[bool, str]:
    """Does the NPPES record a research answer points at carry the defendant's name?

    person: the last name must be in the NPPES name and the first name must match
    exactly (a prefix is not a match: Mika is not Mikala). NPPES names are
    'LAST, FIRST MIDDLE' or 'FIRST MIDDLE LAST'; both orders are read. A party
    written '<Name>, M.D., P.C.' is the physician's own corporation: it agrees
    with the physician's individual record (credential and corporate tails ignored).
    org: every distinctive word of the mention must appear in the NPPES name
    (healthcare = health care; legal tails ignored), the NPPES name may carry at
    most ONE extra distinctive word, and when both cities are known they must
    agree. Returns (ok, reason)."""
    d = str(defendant or "").strip()
    n = str(nppes_name or "").strip()
    if not d or not n:
        return False, "blank name"
    if str(kind or "").lower() == "person":
        dt = _person_tokens(d)
        nt = _person_tokens(n.replace(",", " "))
        if len(dt) < 2 or len(nt) < 2:
            return False, "name too short"
        d_first, d_last = dt[0], dt[-1]
        nset = set(nt)
        if d_last not in nset:
            return False, f"last name {d_last} not in NPPES name"
        # the NPPES first name is the token right after the comma ("LAST, FIRST") or the first token
        if "," in n:
            after = _person_tokens(n.split(",", 1)[1])
            n_first = after[0] if after else ""
        else:
            n_first = nt[0]
        if d_first != n_first and d_first not in nset:
            return False, f"first name {d_first} vs NPPES {n_first or '?'}"
        if d_first != n_first:
            return False, f"first name {d_first} vs NPPES {n_first}"
        return True, "person name agrees"
    dw, nw = _org_words(d), _org_words(n)
    if not dw:
        return False, "no distinctive words in the mention"
    missing = dw - nw
    if missing:
        return False, f"mention words missing from NPPES name: {' '.join(sorted(missing))}"
    extra = nw - dw
    if len(extra) > 1:
        return False, f"NPPES name carries extra words: {' '.join(sorted(extra))}"
    mc, nc = str(mention_city or "").strip().upper(), str(nppes_city or "").strip().upper()
    if mc and nc and mc != nc:
        return False, f"city {mc} vs NPPES {nc}"
    if extra and not (mc and nc):
        return False, f"NPPES name carries an extra word ({' '.join(extra)}) and no city corroborates"
    return True, "org name agrees"


# ---- 4. the defendant is on the cited page ---------------------------------------------
def name_in_page(defendant: str, kind: str, page_text: str) -> bool | None:
    """True when the defendant is named on the page, False when the page was read and
    the name is absent, None when there is no page text to read. A person needs the
    last name AND the first name within 60 characters of each other (so 'Harris' alone
    on a page of Harrises does not count); an org needs every distinctive word."""
    t = str(page_text or "")
    if not t.strip():
        return None
    low = re.sub(r"\s+", " ", t.lower())
    if str(kind or "").lower() == "person":
        toks = _person_tokens(defendant)
        if len(toks) < 2:
            return None
        first, last = toks[0], toks[-1]
        for m in re.finditer(re.escape(last), low):
            window = low[max(0, m.start() - 60): m.end() + 60]
            if re.search(r"\b" + re.escape(first) + r"\b", window):
                return True
        return False
    words = _org_words(defendant)
    if not words:
        return None
    return all(w in low for w in words)
