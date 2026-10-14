"""
case_labels.py — scheme-typed, time-boxed positives from DOJ/qui tam outcomes.

The exclusion-list label is a binary, untyped, untimed flag. A *settlement* carries
far more: the scheme ("upcoding"), the dollars, and — critically — the **conduct
window** ("from 2015 through 2019"). This module turns the enforcement case DB into
per-NPI labels that say *what* and *when*, which is what makes two things possible:

  * SCHEME-STRATIFIED evaluation — measure lift within each scheme family, so a
    model can't hide by only learning LEIE-flavored fraud;
  * OUT-OF-TIME training — train on features dated BEFORE ``conduct_start`` (the
    Pillar-1 leakage fix, usable today even before the full bitemporal store).

Resolution is org-grain and conservative (reuses ``resolve_settled_orgs`` — only
*successful* enforcement with a recovery or an intervention), then broadcast to the
org's member NPIs. These are the strongest positives we have (prosecuted fraud),
folded into the widened label with source ``doj_case``.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name
from src.model_a.lookalikes import resolve_settled_orgs

_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
DEFAULT_LOOKBACK = 4         # conduct typically precedes announcement; conservative


def extract_conduct_window(text: str, announced_date: str = "",
                           lookback: int = DEFAULT_LOOKBACK) -> tuple:
    """(start_year, end_year) of the conduct period from the case text.

    Takes the span of plausible years mentioned (≤ the announcement year); falls
    back to a lookback window ending at the announcement year when the text names
    no period. Returns (None, None) when nothing is determinable."""
    ann_year = None
    s = str(announced_date or "")
    if len(s) >= 4 and s[:4].isdigit():
        ann_year = int(s[:4])
    cap = ann_year or 2026
    years = sorted({int(y) for y in _YEAR.findall(str(text or "")) if 1995 <= int(y) <= cap})
    if years:
        return years[0], years[-1]
    if ann_year:
        return ann_year - lookback, ann_year
    return None, None


def _fuzzy_settled(org_nodes: pd.DataFrame, case_db: pd.DataFrame,
                   already: set, threshold: float) -> pd.DataFrame:
    """Second-pass FUZZY defendant↔org matching (Run 2 §F) for cases the exact
    name-key join missed — 'ACME HEALTH SERVICES LLC' vs 'ACME HEALTH SERVICES'.
    difflib on normalized keys, conservative threshold, same output shape as
    resolve_settled_orgs. Only cases with a real recovery."""
    import difflib
    cols = ["org_node_id", "matched_case_id"]
    name_col = next((c for c in ("org_name", "name", "entity_name")
                     if c in org_nodes.columns), None)
    def_col = next((c for c in ("defendant", "defendant_name", "defendants")
                    if c in case_db.columns), None)
    if not name_col or not def_col or "case_id" not in case_db.columns:
        return pd.DataFrame(columns=cols)
    orgs = org_nodes[["org_node_id", name_col]].dropna().copy()
    orgs["key"] = orgs[name_col].astype(str).map(norm_org_name)
    orgs = orgs[orgs["key"].str.len() >= 8]           # short keys over-match
    # key -> org ids built ONCE (2026-10-08): the previous per-hit scan compared a
    # 500k-row string column for every matched case and ran out of memory on the
    # operator's laptop (ArrowMemoryError inside the Arrow string comparison)
    key_to_ids: dict[str, list[str]] = {}
    for k, oid in zip(orgs["key"].astype(str).to_numpy(), orgs["org_node_id"].astype(str).to_numpy()):
        key_to_ids.setdefault(k, []).append(oid)
    # candidate buckets by the first four characters: at a 0.92 similarity cutoff a
    # match shares its opening characters, and difflib over the whole national key
    # list (500k+ strings) per case was minutes per case
    buckets: dict[str, list[str]] = {}
    for k in key_to_ids:
        buckets.setdefault(k[:4], []).append(k)
    amount = pd.to_numeric(case_db.get("amount_usd"), errors="coerce").fillna(0.0)
    live = case_db[(amount > 0) & ~case_db["case_id"].astype(str).isin(already)]
    rows = []
    for r in live.itertuples():
        dkey = norm_org_name(str(getattr(r, def_col, "") or ""))
        if len(dkey) < 8:
            continue
        cands = buckets.get(dkey[:4])
        if not cands:
            continue
        hit = difflib.get_close_matches(dkey, cands, n=1, cutoff=threshold)
        if hit:
            for oid in key_to_ids.get(hit[0], []):
                rows.append({"org_node_id": oid, "matched_case_id": str(r.case_id)})
    return pd.DataFrame(rows, columns=cols)


CHARGED_TIER = "charged"
CHARGED_SOURCE = "charged_case"
# COMMERCIAL-PLAN FRAUD (operator 2026-10-14; docs/EXCLUSION_SOURCES_AUDIT §commercial):
# a provider convicted of billing a commercial health plan falsely has the same hands,
# codes and staff on its Medicaid claims. case_type ``hcf_commercial`` (state AG /
# insurance-department / state-court convictions and judgments, loaded through
# research_evidence commercial-cases) is a TRAINING-only source: it trains under
# --label-tier hard+commercial, never flips the hard label, is never a forward
# positive (the ruler stays on the federal business outcome) and never enters the
# strict PurpleLab label. Admitted on a conviction, plea or judgment only.
COMMERCIAL_TIER = "hcf_commercial"
COMMERCIAL_SOURCE = "hcf_commercial"
COMMERCIAL_CASE_TYPE = "hcf_commercial"
# a government civil complaint (the United States intervened / filed) is the civil
# counterpart of an indictment: the government investigated and joined. A relator's
# own complaint is a docket row and stays an allegation.
_GOV_COMPLAINT_RE = re.compile(r"\bintervene[sd]?\b|\bfiled\s+(?:a\s+|an\s+)?(?:civil\s+|amended\s+)?complaint\b|"
                               r"\bcomplaint\s+(?:was\s+)?filed\b|\bcomplaint[- ]in[- ]intervention\b", re.I)


def charged_outcome(case_type: str, text: str) -> bool:
    """True when the case row is a CHARGE with no resolved outcome: a criminal
    health-care-fraud indictment / charge / arrest, or a civil FCA row where the
    government filed or intervened in the complaint. The caller has already
    established that ``outcome_tier`` found no resolved outcome."""
    from .fca_label_export import _CHARGED_ONLY, _CIVIL_OUTCOME, _CRIMINAL_OUTCOME
    ct = str(case_type or "").strip().lower()
    t = str(text or "")
    if ct == "criminal_hcf":
        return bool(_CHARGED_ONLY.search(t)) and not _CRIMINAL_OUTCOME.search(t)
    if ct == "civil_fca":
        return bool(_GOV_COMPLAINT_RE.search(t)) and not _CIVIL_OUTCOME.search(t)
    return False


def charged_only_cases(case_db: pd.DataFrame) -> pd.DataFrame:
    """The case rows that are charged-only today (the outcome-refresh harvest list):
    usable rows whose text carries a charge and no resolved outcome. Columns:
    case_id, announced_date, defendant_name, jurisdiction, case_type, source_url,
    summary (truncated)."""
    from .fca_label_export import outcome_tier
    cols = ["case_id", "announced_date", "defendant_name", "jurisdiction", "case_type", "source_url", "summary"]
    if case_db is None or not len(case_db):
        return pd.DataFrame(columns=cols)
    d = case_db.copy()
    if "usable" in d.columns:
        d = d[pd.to_numeric(d["usable"], errors="coerce").fillna(1) == 1]
    out = []
    for c in d.to_dict("records"):
        ct = str(c.get("case_type") or "").strip().lower()
        if ct in ("", "nan", "none") or ct.startswith("docket"):
            continue
        text = f"{c.get('title', '') or ''} {c.get('summary', '') or ''}"
        if outcome_tier(ct, text, c.get("amount_usd"))[0]:
            continue
        if charged_outcome(ct, text):
            out.append({k: c.get(k, "") for k in cols})
    df = pd.DataFrame(out, columns=cols)
    if len(df):
        df["summary"] = df["summary"].fillna("").astype(str).str.slice(0, 300)
        df = df.drop_duplicates("case_id")
    return df


def build_case_labels(case_db: pd.DataFrame, org_nodes: pd.DataFrame,
                      npi_to_org: pd.DataFrame,
                      fuzzy_threshold: float | None = 0.92,
                      medicaid_only: bool = False,
                      asof_cutoff: str | None = None,
                      affiliations: pd.DataFrame | None = None,
                      max_org_members: int = 150,
                      defendant_npis: pd.DataFrame | None = None,
                      max_case_npis: int | None = 100) -> pd.DataFrame:
    """Per-NPI DOJ-case labels: npi, fraud_label, fraud_scheme, conduct_start,
    conduct_end, case_ids, amount_usd, label_source, label_basis, label_weight,
    case_fanout. One row per NPI (a provider in multiple cases takes the union of
    schemes, the widest window, summed dollars, the LARGEST weight).

    PER-CASE BUDGET (``max_case_npis``, 2026-10-10, label audit finding: the top 5
    DOJ cases supplied 64% of the 4,044 DOJ positives in v7). The per-org guard
    above caps each settled ORG, but one case names many orgs (a chain settling
    under twenty enrolled names) and fans out across all of them. Every broadcast
    row (org_billing / affiliated_individual) now carries
    ``label_weight = min(1, max_case_npis / case_fanout)`` where ``case_fanout`` is
    the number of distinct NPIs the case reaches through the broadcast tiers, so
    a case contributes at most ``max_case_npis`` positives' worth of training
    mass however many NPIs it touches. Direct tiers (named_npi /
    resolved_defendant) always weigh 1.0. Rows are never dropped (the metadata
    stays for stratified evaluation); the scorer reads the column as a
    sample weight (company_list.oof_scores). None / 0 disables (every weight 1.0).
    A matrix built with weights trains a different model: new pre-registration.

    FOUR broadcast tiers, strongest first, recorded in ``label_basis``:
      named_npi              the case file names this NPI directly
      resolved_defendant     a defendant (person or business) named in the case
                             text, resolved to a UNIQUE NPPES record by name +
                             state (+ city) — ``enforcement/defendant_resolver``
      org_billing            billing NPIs of the settled organization
      affiliated_individual  individual clinicians whose reassignment edge
                             points at the settled org — the bridge that puts
                             case labels on providers who appear in the
                             per-provider public files (org NPIs never do).
    The affiliation tier is a WEAK label ("worked at a settling org"), so it
    is size-guarded: orgs with more than ``max_org_members`` affiliated
    individuals broadcast to NOBODY (a 5,000-doctor system settling an
    upcoding case does not implicate each clinician). Downstream evals can
    filter on label_basis.

    ``affiliations``: the graph's ``edges/reassigns_to_edges.parquet``
    (src_id ``provider:<npi>`` → dst_id org node id). None disables tier 3.

    ``asof_cutoff`` (YYYY-MM, frozen runs): a case is a leakage-safe positive only
    when its conduct STARTED on/before the cutoff year. Cases whose fraud began
    after the freeze, or whose conduct window is unknown, are dropped from a
    frozen label — otherwise the model would be graded on conduct it could not
    have seen. Current-day runs pass None and keep every resolved case.

    ``defendant_npis``: the resolver's output (case_id, npi, status); only rows
    with status == "resolved" are used.

    ``fuzzy_threshold`` adds a conservative difflib second pass for defendants the
    exact name-key join missed (None disables — Run 2 §F). ``medicaid_only`` keeps
    only cases whose text mentions Medicaid (the §F filter for the Medicaid label)."""
    cols = ["npi", "fraud_label", "fraud_scheme", "case_sector", "conduct_start",
            "conduct_end", "case_ids", "amount_usd", "label_source",
            "label_basis", "fca_strict_tier", "intervened", "qui_tam",
            "label_weight", "case_fanout"]
    if case_db is None or not len(case_db) or org_nodes is None or not len(org_nodes):
        return pd.DataFrame(columns=cols)
    # v3+ case files carry QC provenance: superseded duplicates and
    # cannot-verify rows are marked usable=0 and must never become labels
    # (a duplicate would double-weight its case; an unverified row is not
    # evidence). Older files without the column pass through unchanged.
    if "usable" in case_db.columns:
        n0 = len(case_db)
        keep = pd.to_numeric(case_db["usable"], errors="coerce").fillna(1) == 1
        case_db = case_db.loc[keep]
        if n0 - len(case_db):
            print(f"    [case_labels] dropped {n0 - len(case_db):,} "
                  f"non-usable rows (superseded dups / cannot-verify) "
                  f"per QC provenance")
        if not len(case_db):
            return pd.DataFrame(columns=cols)
    if medicaid_only:
        text = (case_db.get("summary", pd.Series("", index=case_db.index)).fillna("")
                .astype(str) + " "
                + case_db.get("title", pd.Series("", index=case_db.index)).fillna("")
                .astype(str))
        case_db = case_db[text.str.contains("medicaid", case=False)]
        if not len(case_db):
            return pd.DataFrame(columns=cols)
    # exact-name org resolution in TWO passes (2026-10-13): the OUTCOME rule below is
    # the gate, not a stated recovery (a conviction release rarely prints a dollar
    # amount; a charged row never does), so resolve_settled_orgs runs without its
    # amount filter. Resolved rows go first so a case's outcome release owns the
    # org's name key ahead of the same case's earlier charge (one case per key).
    charged_ids = set(charged_only_cases(case_db)["case_id"].astype(str))
    is_charged = case_db["case_id"].astype(str).isin(charged_ids)
    settled = resolve_settled_orgs(org_nodes, case_db[~is_charged], require_recovery=False)
    if is_charged.any():
        extra = resolve_settled_orgs(org_nodes, case_db[is_charged], require_recovery=False)
        if len(extra):
            settled = (pd.concat([settled, extra], ignore_index=True)
                       .drop_duplicates(["org_node_id", "matched_case_id"]))
    if fuzzy_threshold is not None:
        extra = _fuzzy_settled(org_nodes, case_db,
                               set(settled["matched_case_id"].astype(str))
                               if len(settled) else set(),
                               threshold=fuzzy_threshold)
        if len(extra):
            settled = (pd.concat([settled, extra], ignore_index=True)
                       .drop_duplicates(["org_node_id", "matched_case_id"]))
    # NOTE: an empty `settled` is not an early exit — tier-1 named-NPI rows
    # can still label even when no defendant org resolves.

    cases = case_db.drop_duplicates("case_id").set_index("case_id")
    xw = npi_to_org[["npi", "org_node_id"]].astype(str)
    org_to_npis: dict[str, list[str]] = {}
    for r in xw.itertuples():
        org_to_npis.setdefault(r.org_node_id, []).append(r.npi)

    # tier 3: org -> affiliated individual clinicians (reassignment edges),
    # size-guarded so institutional settlements don't smear every clinician
    org_to_individuals: dict[str, list[str]] = {}
    n_guarded = 0
    if affiliations is not None and len(affiliations) and \
            {"src_id", "dst_id"} <= set(affiliations.columns):
        aff = affiliations[["src_id", "dst_id"]].astype(str)
        aff_npi = aff["src_id"].str.removeprefix("provider:")
        grouped: dict[str, set] = {}
        for oid, npi in zip(aff["dst_id"], aff_npi):
            grouped.setdefault(oid, set()).add(npi)
        for oid, members in grouped.items():
            if len(members) <= max_org_members:
                org_to_individuals[oid] = sorted(members)
            else:
                n_guarded += 1
    if n_guarded:
        print(f"    [case_labels] {n_guarded:,} settled-org affiliation "
              f"fan-outs suppressed (> {max_org_members} members — "
              f"institutional, not individual, evidence)")

    def _case_source(c, otier: str | None = None) -> str:
        ct = str(c.get("case_type") or "") if hasattr(c, "get") else ""
        if ct.startswith("docket"):
            return "docket_case"
        if ct.strip().lower() == COMMERCIAL_CASE_TYPE:
            return COMMERCIAL_SOURCE
        return CHARGED_SOURCE if otier == CHARGED_TIER else "doj_case"

    def _case_fields(c):
        scheme = str(c.get("scheme") or "unknown")
        sector = str(c.get("sector") or "")
        start, end = extract_conduct_window(
            c.get("summary", ""), str(c.get("announced_date", "")))
        amt = pd.to_numeric(pd.Series([c.get("amount_usd")]),
                            errors="coerce").iloc[0]
        return scheme, sector, start, end, \
            float(amt) if pd.notna(amt) else 0.0

    # the strict label's OUTCOME rule, applied to the matrix line too (2026-10-08):
    # an indictment or a complaint is an allegation; only a resolved outcome
    # (settlement / judgment / conviction / plea / CMP) is a positive. Docket rows
    # keep their allegation source and never flip the label anyway.
    from .fca_label_export import evidence_strength, outcome_tier
    stats = {"no_outcome": 0, "weak_strength": 0, "self_disclosure": 0, "charged": 0,
             "commercial": 0}

    def _outcome(c) -> str:
        if _case_source(c) == "docket_case":
            return "docket"
        sd = pd.to_numeric(pd.Series([c.get("self_disclosure")]), errors="coerce").iloc[0]
        if pd.notna(sd) and int(sd) == 1:
            # a voluntary self-disclosure settlement is never a positive (L0.3)
            stats["self_disclosure"] += 1
            return ""
        text = f"{c.get('title', '') or ''} {c.get('summary', '') or ''}"
        ct = str(c.get("case_type") or "").strip().lower()
        if ct == COMMERCIAL_CASE_TYPE:
            # a commercial-plan fraud row trains only on a conviction / plea / judgment
            # (the loader already requires one; the rule is enforced here too)
            from .fca_label_export import _CIVIL_OUTCOME, _CRIMINAL_OUTCOME
            if _CRIMINAL_OUTCOME.search(text) or re.search(r"\bjudgment\b", text, re.I) \
                    or _CIVIL_OUTCOME.search(text):
                stats["commercial"] += 1
                return COMMERCIAL_TIER
            return ""
        if ct in ("", "nan", "none"):
            # an UNTYPED case row (the pre-2026-09 harvest never typed its rows):
            # a stated dollar amount or a civil-outcome phrase is the resolved
            # outcome; otherwise the row is a bare announcement
            amt = pd.to_numeric(pd.Series([c.get("amount_usd")]), errors="coerce").iloc[0]
            if (pd.notna(amt) and float(amt) > 0) or outcome_tier("civil_fca", text, None)[0]:
                return "fca_civil"
            return ""
        tier, _ = outcome_tier(ct, text, c.get("amount_usd"))
        if not tier and charged_outcome(ct, text):
            # the CHARGED tier (operator 2026-10-13): a fraud indictment / charge, or a
            # government civil complaint, with no resolved outcome yet. Source
            # charged_case: trains under hard+charged, never flips the hard label,
            # never grades. Converts to doj_case when the outcome release lands.
            stats["charged"] += 1
            return CHARGED_TIER
        return tier

    def _flag(v):
        v = pd.to_numeric(pd.Series([v]), errors="coerce").iloc[0]
        return int(v) if pd.notna(v) else None

    def _extra(c, otier):
        return {"fca_strict_tier": otier, "intervened": _flag(c.get("intervened")),
                "qui_tam": _flag(c.get("qui_tam"))}

    rows = []
    n_guarded_org = 0
    # tier 1: NPIs named in the case file itself — the strongest basis
    npi_col = next((c for c in ("npi_in_source", "npi") if c in case_db.columns),
                   None)
    if npi_col:
        named = case_db[case_db[npi_col].astype(str)
                        .str.fullmatch(r"\d{10}").fillna(False)]
        for r in named.itertuples():
            c = cases.loc[str(r.case_id)] if str(r.case_id) in cases.index else None
            if c is None:
                continue
            otier = _outcome(c)
            if not otier:
                stats["no_outcome"] += 1
                continue
            scheme, sector, start, end, amt = _case_fields(c)
            rows.append({"npi": str(getattr(r, npi_col)), "fraud_scheme": scheme,
                         "case_sector": sector, "conduct_start": start,
                         "conduct_end": end, "case_ids": str(r.case_id),
                         "amount_usd": amt, "label_basis": "named_npi",
                         "label_source": _case_source(c, otier), **_extra(c, otier)})
    # tier 1b: defendants resolved to a unique NPPES record by the resolver
    if defendant_npis is not None and len(defendant_npis):
        ok = defendant_npis[(defendant_npis.get("status", "resolved") == "resolved")
                            & defendant_npis["npi"].astype(str).str.fullmatch(r"\d{10}").fillna(False)]
        for r in ok.drop_duplicates(["case_id", "npi"]).itertuples():
            cid = str(r.case_id)
            if cid not in cases.index:
                continue
            c = cases.loc[cid]
            otier = _outcome(c)
            if not otier:
                stats["no_outcome"] += 1
                continue
            # the evidence ladder's floor: a name-and-state match on a common name
            # with no corroboration ('weak', 74% verified) never labels the matrix
            strength = (evidence_strength("resolved_defendant", getattr(r, "match_level", ""),
                                          getattr(r, "role_agree", ""), getattr(r, "name_national_hits", 0))
                        if "match_level" in ok.columns else "moderate")   # no ladder columns: unknown, not weak
            if strength == "weak":
                stats["weak_strength"] += 1
                continue
            scheme, sector, start, end, amt = _case_fields(c)
            # a resolver row may carry its own per-party budget (label_expansion cia:
            # budget / NPIs of the party); honoured below as a CAP on the row's weight
            pw = pd.to_numeric(pd.Series([getattr(r, "label_weight", None)]), errors="coerce").iloc[0] \
                if "label_weight" in ok.columns else float("nan")
            rows.append({"npi": str(r.npi), "fraud_scheme": scheme, "case_sector": sector,
                         "conduct_start": start, "conduct_end": end, "case_ids": cid,
                         "amount_usd": amt, "label_basis": "resolved_defendant",
                         "label_source": _case_source(c, otier), "provided_weight": pw,
                         **_extra(c, otier)})
    for s in settled.itertuples():
        cid = str(s.matched_case_id)
        if cid not in cases.index:
            continue
        c = cases.loc[cid]
        otier = _outcome(c)                                   # the same outcome rule as
        if not otier:                                         # the named / resolved tiers
            stats["no_outcome"] += 1
            continue
        scheme, sector, start, end, amt = _case_fields(c)
        oid = str(s.org_node_id)
        members = org_to_npis.get(oid, [])
        if len(members) > max_org_members:                    # a chain: one finding is not
            n_guarded_org += 1                                # evidence against 2,000 NPIs
            continue
        for npi in members:                                   # tier 2
            rows.append({"npi": npi, "fraud_scheme": scheme, "case_sector": sector,
                         "conduct_start": start, "conduct_end": end, "case_ids": cid,
                         "amount_usd": amt, "label_basis": "org_billing",
                         "label_source": _case_source(c, otier), **_extra(c, otier)})
        if otier == CHARGED_TIER:
            continue                                          # "worked at a CHARGED org" is no signal
        for npi in org_to_individuals.get(oid, []):           # tier 3
            rows.append({"npi": npi, "fraud_scheme": scheme, "case_sector": sector,
                         "conduct_start": start, "conduct_end": end, "case_ids": cid,
                         "amount_usd": amt,
                         "label_basis": "affiliated_individual",
                         "label_source": _case_source(c, otier), **_extra(c, otier)})
    if n_guarded_org:
        print(f"    [case_labels] {n_guarded_org:,} settled-org billing broadcasts suppressed "
              f"(> {max_org_members} billing NPIs — a chain, not individual evidence)")
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)

    if asof_cutoff:
        cut_year = int(str(asof_cutoff)[:4])
        n_before = len(df)
        df = df[df["conduct_start"].notna()
                & (pd.to_numeric(df["conduct_start"], errors="coerce") <= cut_year)]
        # information availability: a case settled AFTER the freeze was unknown at
        # the freeze, however early its conduct began. Keep only cases announced on
        # or before the cutoff month; undated announcements are dropped too (strict).
        if "announced_date" in cases.columns and len(df):
            ann = pd.to_datetime(df["case_ids"].astype(str).map(cases["announced_date"]), errors="coerce")
            cut_ts = pd.Timestamp(f"{str(asof_cutoff)[:7]}-01") + pd.offsets.MonthEnd(0)
            n_post = int(((ann > cut_ts) | ann.isna()).sum())
            df = df[ann.notna() & (ann <= cut_ts)]
            if n_post:
                print(f"    [case_labels] {n_post:,} case rows dropped for the frozen run: "
                      f"announced after {str(asof_cutoff)[:7]} or undated (conduct window alone "
                      f"would have leaked a post-cutoff settlement into the label)")
        if not len(df):
            return pd.DataFrame(columns=cols)

    def _tag_union(vals) -> str:
        tags = set()
        for v in vals:
            tags.update(t.strip() for t in str(v or "").split(";") if t.strip())
        return ";".join(sorted(tags))

    # the per-case budget: how many distinct NPIs each case reaches through the
    # broadcast tiers, and the weight that caps its training mass at the budget
    bc = df["label_basis"].isin(BROADCAST_BASES)
    fan = (df.loc[bc].drop_duplicates(["case_ids", "npi"]).groupby("case_ids")["npi"].size()
           if bc.any() else pd.Series(dtype=int))
    df["case_fanout"] = df["case_ids"].map(fan).fillna(0).astype(int)
    df["label_weight"] = 1.0
    if max_case_npis and max_case_npis > 0 and bc.any():
        df.loc[bc, "label_weight"] = np.minimum(
            1.0, float(max_case_npis) / df.loc[bc, "case_fanout"].clip(lower=1).astype(float))
    # a resolver row's own budget (a CIA party spread over its enrolled NPIs) caps
    # the direct-tier weight: 100 NPIs of one settlement are one settlement's mass
    if "provided_weight" in df.columns:
        pw = pd.to_numeric(df["provided_weight"], errors="coerce")
        has = pw.notna()
        if has.any():
            df.loc[has, "label_weight"] = np.minimum(df.loc[has, "label_weight"], pw[has].clip(0.0, 1.0))
        df = df.drop(columns=["provided_weight"])

    _BASIS_RANK = {"named_npi": 0, "resolved_defendant": 1, "org_billing": 2,
                   "affiliated_individual": 3}
    out_rows = []
    for npi, g in df.groupby("npi"):
        starts = g["conduct_start"].dropna()
        ends = g["conduct_end"].dropna()
        # a case's dollars count once per NPI even when the NPI reaches the
        # case via more than one tier
        dedup = g.drop_duplicates("case_ids")
        out_rows.append({
            "npi": npi, "fraud_label": 1,
            "fraud_scheme": _tag_union(g["fraud_scheme"]) or "unknown",
            "case_sector": _tag_union(g["case_sector"]),
            "conduct_start": int(starts.min()) if len(starts) else None,
            "conduct_end": int(ends.max()) if len(ends) else None,
            "case_ids": ";".join(sorted(set(g["case_ids"]))),
            "amount_usd": float(dedup["amount_usd"].sum()),
            # a prosecuted outcome (doj_case) outranks a charge (charged_case), which
            # outranks a relator's allegation (docket_case)
            "label_source": ("doj_case" if (g["label_source"] == "doj_case").any()
                             else CHARGED_SOURCE if (g["label_source"] == CHARGED_SOURCE).any()
                             else COMMERCIAL_SOURCE if (g["label_source"] == COMMERCIAL_SOURCE).any()
                             else "docket_case"),
            "label_basis": min(g["label_basis"],
                               key=lambda b: _BASIS_RANK.get(b, 9)),
            "fca_strict_tier": _tag_union(g.get("fca_strict_tier", pd.Series(dtype=str))),
            "intervened": (1 if (pd.to_numeric(g.get("intervened"), errors="coerce") == 1).any()
                           else (0 if (pd.to_numeric(g.get("intervened"), errors="coerce") == 0).any() else None)),
            "qui_tam": 1 if (pd.to_numeric(g.get("qui_tam"), errors="coerce") == 1).any() else None,
            # a direct basis anywhere gives the NPI full weight; otherwise the
            # least-diluted broadcast it sits in
            "label_weight": float(g["label_weight"].max()),
            "case_fanout": int(g["case_fanout"].max()),
        })
    if stats["no_outcome"] or stats["weak_strength"] or stats["charged"] or stats["commercial"]:
        print(f"    [case_labels] outcome rule: {stats['self_disclosure']:,} self-disclosure rows set aside; "
              f"{stats['no_outcome']:,} case rows without a resolved "
              f"outcome (bare announcements) and {stats['weak_strength']:,} weak-evidence "
              f"defendant rows kept OUT of the label; {stats['charged']:,} charged-only rows "
              f"carry source {CHARGED_SOURCE} (trains under hard+charged, never grades); "
              f"{stats['commercial']:,} commercial-plan conviction rows carry source "
              f"{COMMERCIAL_SOURCE} (trains under hard+commercial, never grades)")
    out = pd.DataFrame(out_rows, columns=cols)
    conc = case_concentration(out, top=5)
    if len(conc) and conc["n_cases"].iat[0] > 5:
        print(f"    [case_labels] broadcast concentration: the top 5 of {int(conc['n_cases'].iat[0]):,} "
              f"cases supply {conc['share'].sum():.0%} of {len(out):,} case-labelled NPIs"
              + (f"; {conc['weighted_share'].sum():.0%} of the training mass after the per-case "
                 f"budget of {max_case_npis} (label_weight)" if max_case_npis else
                 "; per-case budget OFF (every label_weight 1.0)"))
    return out


BROADCAST_BASES = frozenset({"org_billing", "affiliated_individual"})


def case_concentration(labels: pd.DataFrame, top: int = 5) -> pd.DataFrame:
    """How concentrated the case label is: one row per case (the ``top`` largest
    first) with the NPIs it labels, its share of all case-labelled NPIs, and the
    same share after ``label_weight`` (the per-case budget). An NPI in several
    cases counts once per case, so shares can sum past 1.0 on a heavily
    cross-labelled book; the column ``n_cases`` carries the total for the log
    line. Empty frame when there are no labels."""
    cols = ["case_id", "n_npis", "share", "weighted_mass", "weighted_share", "n_cases"]
    if labels is None or not len(labels) or "case_ids" not in labels.columns:
        return pd.DataFrame(columns=cols)
    w = (pd.to_numeric(labels.get("label_weight", pd.Series(1.0, index=labels.index)),
                       errors="coerce").fillna(1.0))
    ex = (pd.DataFrame({"npi": labels["npi"].astype(str),
                        "case_id": labels["case_ids"].astype(str).str.split(";"),
                        "w": w.to_numpy(dtype=float)})
          .explode("case_id"))
    ex = ex[ex["case_id"].astype(str).str.strip() != ""]
    if not len(ex):
        return pd.DataFrame(columns=cols)
    per = ex.groupby("case_id").agg(n_npis=("npi", "size"), weighted_mass=("w", "sum"))
    total_npis = float(len(labels))
    total_mass = float(w.sum()) if float(w.sum()) > 0 else 1.0
    per["share"] = per["n_npis"] / total_npis
    per["weighted_share"] = per["weighted_mass"] / total_mass
    per["n_cases"] = int(len(per))
    per = per.sort_values(["n_npis", "weighted_mass"], ascending=False).head(top).reset_index()
    return per[cols]


def main() -> None:
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case-db", required=True, help="case DB csv/parquet")
    ap.add_argument("--graph-dir", required=True, help="entity-graph output dir")
    ap.add_argument("--out", required=True, help="output case_labels parquet")
    args = ap.parse_args()
    g = Path(args.graph_dir)
    case_db = (pd.read_csv(args.case_db, dtype=str) if args.case_db.endswith(".csv")
               else pd.read_parquet(args.case_db))
    org_nodes = pd.read_parquet(g / "nodes" / "org_nodes.parquet")
    npi_to_org = pd.read_parquet(g / "npi_to_org.parquet")
    aff_p = g / "edges" / "reassigns_to_edges.parquet"
    affiliations = pd.read_parquet(aff_p) if aff_p.exists() else None
    labels = build_case_labels(case_db, org_nodes, npi_to_org,
                               affiliations=affiliations)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    labels.to_parquet(args.out, index=False)
    print(f"Wrote {args.out} — {len(labels):,} NPIs labeled from DOJ cases "
          f"({labels['fraud_scheme'].nunique() if len(labels) else 0} scheme types)")


if __name__ == "__main__":
    main()
