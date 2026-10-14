"""
fca_label_export.py — the STRICT False Claims Act label, one NPI per row, for the
PurpleLab proof of concept (or any claims-data join).

"Strict" is a written policy this module enforces, not a judgment call per row:

  Tier fca_civil     a RESOLVED civil False Claims Act outcome: case_type civil_fca AND
                     an outcome signal (a dollar amount, a stated intervention, or
                     settle / judgment / consent / agreed-to-pay wording). Filed-but-
                     unresolved cases, dockets and allegations never qualify.
  Tier hcf_criminal  a criminal health-care-fraud CONVICTION or PLEA (case_type
                     criminal_hcf AND sentenced / pleaded guilty / convicted / jury
                     wording). Charged / indicted only → excluded.
  Tier excl_statute  an exclusion-list statute that is fraud on its face
                     (1128a1, 1128a3, 1128b1, 1128b6, 1128b7, BRCH CIA / SA, 1128b16),
                     with the statute on the row. Optional (--policy with_exclusions).

Identifier rules (every tier): a Luhn-valid 10-digit NPI; the case→NPI basis is on
the row (named_npi, resolved_defendant with its match level, or research with high
or medium confidence); the NPI exists in NPPES; entity type agrees with the
defendant kind; the NPI was not deactivated before the conduct began. Rows that
fail a rule are written to the QUARANTINE file with the reason, never dropped.

Time-boxing: announced_date plus the conduct window from the case text
(``case_labels.extract_conduct_window``); ``window_inferred`` says when the text
named no period and a lookback was used.

Provenance: source URL, case type, outcome words, amount, basis, match level on
every row, and a 30-row random sample (verification_sample.csv) for a human check
against the public document before upload. Leads for counsel review, never
accusations: every row here is an already-prosecuted matter used as a label.

  python -m src.model_a.fca_label_export --case-db enforcement/doj_cases_v3.csv \
      --defendant-npis processed/case_defendant_npis.parquet --provider-dim processed/provider_dim.parquet \
      --out-dir processed/fca_label [--exclusion-nodes graph/nodes/exclusion_nodes.parquet --policy with_exclusions]
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import is_valid_npi
from src.enforcement.defendant_resolver import district_state, url_state, text_state
from src.model_a.case_labels import extract_conduct_window
from src.model_a.fca_relevance import LEIE_STATUTES, _NORM

POLICIES = ("strict", "strict_plus_criminal", "with_exclusions", "fca_shaped")
# fca_shaped = everything above PLUS fraud that COULD have been a False Claims Act
# matter but was handled another way: state Medicaid terminations for a fraud-shaped
# cause (fca_relevance hard tier: criminal conduct, financial integrity, fraud
# conviction, false statements) and state attorney-general / MFCU case outcomes.
_STATE_FRAUD = re.compile(r"fraud|false claim|kickback|false (?:statement|record|document)|misrepresent|"
                          r"criminal conduct|financial integrity|convict|guilty|theft|embezzl|overbill|upcod|"
                          r"billing (?:abuse|irregularit)|abuse of billing", re.I)
_CIVIL_OUTCOME = re.compile(r"agree[sd]?\s+to\s+pay|settle|settlement|judgment|consent\s+(?:decree|judgment)|"
                            r"resolve[sd]?\s+(?:the\s+)?(?:allegations|claims)|will\s+pay|has\s+paid|paid\s+\$", re.I)
_CRIMINAL_OUTCOME = re.compile(r"sentenced|pleaded\s+guilty|pled\s+guilty|plead\s+guilty|convicted|jury\s+(?:found|returned)|"
                               r"found\s+guilty|guilty\s+plea|guilty\s+verdict", re.I)
_CHARGED_ONLY = re.compile(r"\b(?:charged|indicted|indictment|arrested|complaint\s+filed|accused)\b", re.I)
STRICT_STATUTES = {"1128a1", "1128a3", "1128b1", "1128b6", "1128b7", "brchcia", "brchsa", "1128b16"}
OK_MATCH_LEVELS = {"name_state", "name_city_state", "name_state_middle", "name_city_state_middle",
                   "research_high", "research_medium",
                   # the fuzzy business tier (resolver, 2026-10-10): accepted only with
                   # include_fuzzy, always at strength weak until a verdict promotes the row
                   "fuzzy_name_state", "fuzzy_name_city_state",
                   "fuzzy_name_state_verified", "fuzzy_name_city_state_verified",
                   # a confirmed verdict carried onto a mention the retired tier no longer
                   # matches (resolver apply_verification, 2026-10-11): strength moderate
                   "fuzzy_verified"}


def _s(v) -> str:
    """A clean string: None / NaN / the literal 'nan' become '' (never a 'nan' state or type)."""
    if v is None or (isinstance(v, float) and v != v):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "<na>") else s


def outcome_tier(case_type: str, text: str, amount) -> tuple[str, str]:
    """(tier, outcome_words) for a case row; tier '' when it does not qualify."""
    t = str(text or "")
    ct = _s(case_type)          # an untyped case (NaN in the table) takes the state-case path below
    amt = pd.to_numeric(pd.Series([amount]), errors="coerce").iloc[0]
    if ct == "civil_fca":
        m = _CIVIL_OUTCOME.search(t)
        if m or (pd.notna(amt) and float(amt) > 0):
            return "fca_civil", (m.group(0) if m else "amount stated")
        return "", "civil_fca without a resolved outcome"
    if ct == "criminal_hcf":
        m = _CRIMINAL_OUTCOME.search(t)
        if m:
            return "hcf_criminal", m.group(0)
        return "", "charged only" if _CHARGED_ONLY.search(t) else "criminal_hcf without an outcome"
    if ct == "not_fraud":
        return "", "OIG action that is not billing fraud (EMTALA / excluded employee)"
    if ct == "oig_cmp":
        # fraud-shaped conduct settled under the CMP law: fca_shaped only
        if _STATE_FRAUD.search(t) or re.search(r"submitting\s+(?:false\s+)?claims|not\s+(?:provided|rendered)|"
                                               r"remuneration|medically\s+unnecessary|unlicensed|not\s+covered|"
                                               r"misidentified|improper\s+claims", t, re.I):
            m = _CIVIL_OUTCOME.search(t)
            return "oig_cmp", (m.group(0) if m else "CMP settlement")
        return "", "CMP settlement without fraud-shaped conduct"
    if ct.startswith("docket"):
        return "", "docket (allegation)"
    if ct == "hcf_commercial":
        # a commercial-plan fraud conviction is a TRAINING source (case_labels
        # COMMERCIAL_SOURCE), never a strict-label row: the PurpleLab label is FCA-shaped
        return "", "commercial-plan fraud conviction: training source only, never the strict label"
    # a state AG / MFCU / research-loaded case with no case_type: fraud-shaped words + an outcome
    if not ct and _STATE_FRAUD.search(t) and (_CRIMINAL_OUTCOME.search(t) or _CIVIL_OUTCOME.search(t)
                                              or (pd.notna(amt) and float(amt) > 0)):
        m = _CRIMINAL_OUTCOME.search(t) or _CIVIL_OUTCOME.search(t)
        return "state_case", (m.group(0) if m else "amount stated")
    return "", f"case_type {ct or 'blank'}"


def _row_state(c) -> str:
    return (district_state(c.get("jurisdiction", "")) or url_state(c.get("source_url", ""))
            or text_state(c.get("summary", "")))


# The evidence ladder: how sure the identifier on the row is. exact_id = the NPI was printed
# on the list itself; strong = name + city + state, a middle name, a research answer at high
# confidence, or a name + state hit whose text role agrees with the NPI taxonomy; moderate =
# name + state on a name that is rare nationally (<= 3 NPIs), or research medium, or a unique
# in-state name-only exclusion match; weak = name + state on a common name with no role
# corroboration (the stratum the verification batch must oversample).
COMMON_NAME_HITS = 4


def evidence_strength(basis: str, match_level: str, role_agree: str = "", national_hits=0) -> str:
    ml = str(match_level or "")
    if basis in ("npi_on_list", "named_npi"):
        return "exact_id"
    if ml.startswith("fuzzy_"):
        # a verified fuzzy row verified exactly as a moderate name match would
        return "moderate" if ml.endswith("_verified") else "weak"
    if basis == "name_match":
        return "moderate"
    if ml.startswith("cia_") or ml == "research_cia":
        # a CIA party resolved by EXACT enrolled name / PECOS owner / a component of
        # the party line, or an enrolled entity the research answer named with its
        # NPI: an official roster entry joined without name logic (moderate, the
        # same rung as a unique business match)
        return "moderate"
    if ml == "research_high" or ml.startswith("name_city_state") or ml.endswith("_middle"):
        return "strong"
    if ml == "research_medium":
        return "moderate"
    if str(role_agree) == "agree":
        return "strong"
    try:
        hits = int(float(national_hits or 0))
    except (TypeError, ValueError):
        hits = 0
    return "moderate" if 0 < hits < COMMON_NAME_HITS else "weak"


def stratified_sample(labels: pd.DataFrame, per_stratum: int = 12, seed: int = 11,
                      rungs: list[str] | None = None) -> pd.DataFrame:
    """The verification sample that attacks the label where it is weakest: up to
    ``per_stratum`` rows from every (evidence_strength, label_tier) cell, weak first;
    ``rungs`` restricts the cells (e.g. ["weak", "moderate"] for the second batch)."""
    if not len(labels):
        return labels
    order = {"weak": 0, "moderate": 1, "strong": 2, "exact_id": 3}
    parts = []
    pool = labels[labels["evidence_strength"].isin(rungs)] if rungs else labels
    if not len(pool):
        return pool
    for (s, t), g in pool.groupby(["evidence_strength", "label_tier"]):
        parts.append(g.sample(min(per_stratum, len(g)), random_state=seed).assign(_o=order.get(s, 9)))
    out = pd.concat(parts).sort_values(["_o", "label_tier"]).drop(columns="_o")
    return out.reset_index(drop=True)


def fuzzy_rows(labels: pd.DataFrame) -> pd.DataFrame:
    """The rows the resolver's fuzzy business tier supplied (match_level
    fuzzy_*, not yet verified): the verification batch for that tier, in full."""
    if not len(labels) or "match_level" not in labels.columns:
        return labels.iloc[0:0]
    lv = labels["match_level"].astype(str)
    return labels[lv.str.startswith("fuzzy_") & ~lv.str.endswith("_verified")].reset_index(drop=True)


def build_strict_labels(case_db: pd.DataFrame, defendant_npis: pd.DataFrame, provider_dim: pd.DataFrame,
                        policy: str = "strict_plus_criminal",
                        exclusion_nodes: pd.DataFrame | None = None,
                        include_fuzzy: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (labels, quarantine). Both carry a ``reason``-style column set.
    include_fuzzy admits the resolver's resolved_fuzzy rows at strength weak (so the
    verification strata can sample them); the PurpleLab file (--min-strength
    moderate) never carries an unverified fuzzy row either way."""
    assert policy in POLICIES, policy
    if include_fuzzy and "status" in defendant_npis.columns:
        defendant_npis = defendant_npis.copy()
        fz = defendant_npis["status"].astype(str) == "resolved_fuzzy"
        defendant_npis.loc[fz, "status"] = "resolved"
    cases = case_db.drop_duplicates("case_id").set_index("case_id")
    pd_ = provider_dim[["npi"] + [c for c in ("entity_type", "deactivation_date", "addr_state") if c in provider_dim.columns]].copy()
    pd_["npi"] = pd_["npi"].astype(str)
    pd_ = pd_.drop_duplicates("npi").set_index("npi")

    rows, quar = [], []
    # mentions the resolver rejected on role coherence are kept visible in the quarantine
    st_all = defendant_npis["status"].astype(str)
    for r in defendant_npis[st_all.isin(["nonclinical_role", "role_mismatch", "middle_conflict"])].itertuples(index=False):
        d = r._asdict()
        st = d.get("status")
        if st == "nonclinical_role":
            why = f"non-clinical role in the release: '{d.get('role', '')}'"
        elif st == "middle_conflict":
            why = "middle name in the release contradicts the NPPES middle name of the unique candidate (npi_candidate column)"
        else:
            why = (f"role mismatch: release says '{d.get('role', '')}' [{d.get('role_family', '') or '-'}], "
                   f"NPI taxonomy family is {d.get('npi_taxonomy_family', '') or '-'}")
        if str(d.get("veto_from") or ""):
            why += " (vetoed via the sibling mention)"
        quar.append({**d, "reason": why})
    dn = defendant_npis[st_all == "resolved"].copy()
    for r in dn.itertuples(index=False):
        d = r._asdict()
        cid = str(d.get("case_id", ""))
        if cid not in cases.index:
            quar.append({**d, "reason": "case_id not in the case table"})
            continue
        c = cases.loc[cid]
        tier, words = outcome_tier(c.get("case_type", ""), f"{c.get('summary', '')} {c.get('scheme', '')}", c.get("amount_usd"))
        if tier == "hcf_criminal" and policy == "strict":
            quar.append({**d, "reason": "criminal tier excluded by policy strict"})
            continue
        if tier in ("state_case", "oig_cmp") and policy != "fca_shaped":
            quar.append({**d, "reason": f"{tier} tier excluded by policy {policy}"})
            continue
        if not tier:
            quar.append({**d, "reason": f"no qualifying outcome: {words}"})
            continue
        npi = str(d.get("npi", ""))
        if not (len(npi) == 10 and is_valid_npi(npi)):
            quar.append({**d, "reason": "NPI fails Luhn / length"})
            continue
        ml = str(d.get("match_level", ""))
        basis = "named_npi" if str(d.get("mention", "")) == "named_npi" else "resolved_defendant"
        if basis == "resolved_defendant" and ml not in OK_MATCH_LEVELS:
            quar.append({**d, "reason": f"match level not accepted: {ml or 'blank'}"})
            continue
        start, end = extract_conduct_window(c.get("summary", ""), str(c.get("announced_date", "")))
        inferred = not bool(re.search(r"\b(19\d{2}|20\d{2})\b", str(c.get("summary", ""))))
        flags = []
        if npi not in pd_.index:
            quar.append({**d, "reason": "NPI not in NPPES (provider_dim)"})
            continue
        p = pd_.loc[npi]
        et = _s(p.get("entity_type", ""))
        kind = _s(d.get("kind", ""))
        if et and kind and ((kind == "person" and et == "2") or (kind == "org" and et == "1")):
            quar.append({**d, "reason": f"entity type {et} disagrees with defendant kind {kind}"})
            continue
        deact = pd.to_datetime(p.get("deactivation_date"), errors="coerce") if "deactivation_date" in p else pd.NaT
        if pd.notna(deact) and start and deact.year < int(start):
            quar.append({**d, "reason": f"NPI deactivated {deact.date()} before conduct began {start}"})
            continue
        if inferred:
            flags.append("window_inferred")
        if kind == "org" and tier == "hcf_criminal":
            # the operator's policy (2026-09-29): a company named in a criminal release is the billing
            # vehicle of the convicted owner; its NPI is the one that submitted the claims, so it stays
            # in the label, flagged for the reader and the model
            flags.append("org_billing_party")
        rows.append({
            "npi": npi, "label_tier": tier, "policy": policy, "case_id": cid,
            "source": "oig" if "oig.hhs.gov" in cid else ("doj" if "justice.gov" in cid else "other"),
            "case_type": c.get("case_type", ""), "outcome_words": words,
            "amount_usd": c.get("amount_usd"), "announced_date": c.get("announced_date", ""),
            "conduct_start": start, "conduct_end": end,
            "state": _s(d.get("state")) or _row_state(c) or _s(p.get("addr_state")), "basis": basis, "match_level": ml,
            "defendant": d.get("defendant", ""), "kind": kind, "entity_type": et,
            "role": _s(d.get("role")), "role_agree": _s(d.get("role_agree")) or "unknown",
            "name_national_hits": d.get("name_national_hits", 0) or 0,
            "evidence_strength": evidence_strength(basis, ml, _s(d.get("role_agree")), d.get("name_national_hits", 0)),
            "flags": ";".join(flags), "source_url": c.get("source_url", cid),
        })

    if policy in ("with_exclusions", "fca_shaped") and exclusion_nodes is not None and len(exclusion_nodes):
        e = exclusion_nodes.copy()
        e["npi"] = e["npi"].fillna("").astype(str).str.strip()
        for r in e.itertuples(index=False):
            npi = str(r.npi)
            if not (len(npi) == 10 and is_valid_npi(npi)):
                continue
            et_raw = str(getattr(r, "excl_type", "") or "")
            if policy == "fca_shaped" and et_raw.lower().startswith(("state_term", "mcd_enroll")):
                # a state Medicaid termination (state list) or a Medicaid enrollment segment
                # ended for cause (CMS enrollment file) for a FRAUD-SHAPED cause, in the
                # state's own words
                enroll = et_raw.lower().startswith("mcd_enroll")
                body = re.sub(r"\|name_match$", "", et_raw.split(":", 2)[2] if et_raw.count(":") >= 2 else "")
                if not _STATE_FRAUD.search(body) or npi not in pd_.index:
                    continue
                d = pd.to_datetime(getattr(r, "excl_date", None), errors="coerce")
                st = et_raw.split(":")[1].upper() if et_raw.count(":") >= 2 else ""
                rows.append({
                    "npi": npi, "label_tier": "state_fraud", "policy": policy,
                    "case_id": f"{'mcd_enroll' if enroll else 'state_term'}:{st}:{npi}",
                    "source": f"{'mcd_enroll_' if enroll else 'state_'}{st.lower()}",
                    "case_type": "medicaid_enrollment_end" if enroll else "state_termination",
                    "outcome_words": body[:80], "amount_usd": None,
                    "announced_date": d.strftime("%Y-%m-%d") if pd.notna(d) else "",
                    "conduct_start": d.year - 4 if pd.notna(d) else None, "conduct_end": d.year if pd.notna(d) else None,
                    "state": st, "basis": "npi_on_list" if "name_match" not in et_raw else "name_match",
                    "match_level": "exact" if "name_match" not in et_raw else "unique_in_state",
                    "defendant": "", "kind": "", "entity_type": _s(pd_.loc[npi].get("entity_type", "")),
                    "role": "", "role_agree": "n/a", "name_national_hits": 0,
                    "evidence_strength": "exact_id" if "name_match" not in et_raw else "moderate",
                    "flags": "window_inferred", "source_url": "",
                })
                continue
            key = _NORM.sub("", et_raw.lower())
            code = next((k for k in sorted(LEIE_STATUTES, key=len, reverse=True) if key.startswith(k)), "")
            if code not in STRICT_STATUTES:
                continue
            if npi not in pd_.index:
                quar.append({"npi": npi, "case_id": str(getattr(r, "excl_type", "")), "reason": "NPI not in NPPES (provider_dim)"})
                continue
            d = pd.to_datetime(getattr(r, "excl_date", None), errors="coerce")
            rows.append({
                "npi": npi, "label_tier": "excl_statute", "policy": policy,
                "case_id": f"leie:{npi}:{code}", "source": "leie", "case_type": "exclusion",
                "outcome_words": LEIE_STATUTES[code][1], "amount_usd": None,
                "announced_date": d.strftime("%Y-%m-%d") if pd.notna(d) else "",
                "conduct_start": d.year - 4 if pd.notna(d) else None, "conduct_end": d.year if pd.notna(d) else None,
                "state": _s(pd_.loc[npi].get("addr_state", "")) or _s(getattr(r, "state", "")),
                "basis": "npi_on_list", "match_level": "exact",
                "defendant": "", "kind": "", "entity_type": _s(pd_.loc[npi].get("entity_type", "")),
                "role": "", "role_agree": "n/a", "name_national_hits": 0, "evidence_strength": "exact_id",
                "flags": "window_inferred", "source_url": "https://oig.hhs.gov/exclusions/",
            })

    cols = ["npi", "label_tier", "policy", "case_id", "source", "case_type", "outcome_words", "amount_usd",
            "announced_date", "conduct_start", "conduct_end", "state", "basis", "match_level", "evidence_strength",
            "role", "role_agree", "name_national_hits", "defendant", "kind", "entity_type", "flags", "source_url"]
    labels = pd.DataFrame(rows, columns=cols).drop_duplicates(["npi", "case_id"]).reset_index(drop=True)
    # the same case harvested twice (DOJ national + local USAO release, or a sentencing
    # re-announcement): one row per NPI per (year, amount); the first source kept
    if len(labels):
        yr = labels["announced_date"].fillna("").astype(str).str[:4]
        amt = pd.to_numeric(labels["amount_usd"], errors="coerce").round(0).astype("string")
        key = labels["npi"] + "|" + labels["label_tier"] + "|" + yr + "|" + amt.fillna("na")
        dup = key.duplicated() & amt.notna()
        labels = labels[~dup].reset_index(drop=True)
    return labels, pd.DataFrame(quar)


def report(labels: pd.DataFrame, quar: pd.DataFrame, policy: str) -> str:
    L = [f"# STRICT FCA LABEL EXPORT (policy: {policy})", "",
         "_One NPI per row per case. Every row is an already-prosecuted matter with its public source; leads for counsel review, never accusations._", "",
         f"- label rows: **{len(labels):,}**; distinct NPIs: **{labels['npi'].nunique():,}**; distinct cases: **{labels['case_id'].nunique():,}**",
         f"- quarantined (kept with a reason, never dropped): **{len(quar):,}**", ""]
    if len(labels):
        L += ["## By tier", ""] + [f"- {t}: {n:,} rows, {labels[labels.label_tier == t]['npi'].nunique():,} NPIs"
                                   for t, n in labels["label_tier"].value_counts().items()]
        L += ["", "## Distinct NPIs by state (top 20)", ""] + [f"- {s or '(none)'}: {n:,}"
                                                             for s, n in labels.groupby("state")["npi"].nunique().sort_values(ascending=False).head(20).items()]
        yrs = labels["announced_date"].fillna("").astype(str).str[:4].value_counts().sort_index()
        L += ["", "## By announcement year", ""] + [f"- {y}: {n:,}" for y, n in yrs.items() if y]
        L += ["", "## Basis and match level", ""] + [f"- {b} / {m or '-'}: {n:,}"
                                                   for (b, m), n in labels.groupby(["basis", "match_level"]).size().items()]
        L += ["", f"- rows with an inferred conduct window: {int(labels['flags'].fillna('').str.contains('window_inferred').sum()):,}"]
        if "evidence_strength" in labels.columns:
            L += ["", "## Evidence strength (how sure the identifier on the row is)", "",
                  "_exact_id = the NPI was printed on the list; strong = name+city+state, a middle name, research high, or a "
                  "name+state hit whose text role agrees with the NPI taxonomy; moderate = name+state on a rare name (under "
                  f"{COMMON_NAME_HITS} NPIs nationally) or research medium; weak = name+state on a common name with no role "
                  "corroboration. Weak rows are the ones the verification batch oversamples; drop them with --min-strength "
                  "moderate if the verification says so._", ""]
            for s in ("exact_id", "strong", "moderate", "weak"):
                g = labels[labels["evidence_strength"] == s]
                if len(g):
                    L.append(f"- {s}: {len(g):,} rows, {g['npi'].nunique():,} NPIs")
            rp = labels[labels["basis"] == "resolved_defendant"]
            if len(rp):
                L += ["", "- role read from the release, resolved-defendant rows: "
                      + ", ".join(f"{k or '(none)'} {v:,}" for k, v in rp["role_agree"].fillna("").value_counts().items())]
    if len(quar):
        L += ["", "## Quarantine reasons", ""] + [f"- {r}: {n:,}" for r, n in quar["reason"].value_counts().head(20).items()]
    L += ["", "## The written policy", "",
          "- fca_civil: case_type civil_fca AND (amount > 0 OR settle/judgment/consent/agreed-to-pay wording).",
          "- hcf_criminal: case_type criminal_hcf AND sentenced/pleaded guilty/convicted/jury wording; charged-only excluded.",
          "- excl_statute (with_exclusions / fca_shaped): LEIE 1128a1/a3/b1/b6/b7, BRCH CIA/SA, b16, statute on the row.",
          "- oig_cmp (fca_shaped only): an OIG civil-monetary-penalty settlement for fraud-shaped conduct (claims not provided,",
          "  remuneration, medically unnecessary, unlicensed); EMTALA and excluded-employee CMPs are never fraud labels.",
          "- state_case / state_fraud (fca_shaped only): fraud that could have been an FCA matter but was handled by a state:",
          "  a state AG / MFCU outcome with fraud-shaped words, or a state Medicaid termination whose stated cause is fraud-shaped.",
          "- identifier: Luhn-valid NPI; basis named_npi or resolved_defendant at an accepted match level or research high/medium;",
          "  NPI present in NPPES; entity type agrees with defendant kind; not deactivated before the conduct began.",
          "- time: announced_date + conduct window from the text (window_inferred flag when a lookback was used).",
          "- org_billing_party (flag): a company named in a CRIMINAL release whose owner was convicted stays in the label as the",
          "  billing vehicle (its NPI submitted the claims); the flag marks it so a reader can separate it from a convicted party.",
          "- dockets, allegations, license and paperwork actions, chains and broadcast-to-employees: never."]
    return "\n".join(L)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case-db", required=True)
    ap.add_argument("--defendant-npis", required=True)
    ap.add_argument("--provider-dim", required=True)
    ap.add_argument("--exclusion-nodes", default=None, nargs="+",
                    help="exclusion_nodes.parquet and/or processed/exclusions_*.parquet (several allowed; "
                         "pass the state-terminations file directly until the graph is rebuilt)")
    ap.add_argument("--policy", default="strict_plus_criminal", choices=POLICIES)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--strata-rungs", default=None,
                    help="comma list of evidence rungs the strata sample draws from (e.g. weak,moderate); default all")
    ap.add_argument("--strata-seed", type=int, default=11, help="change it to draw a fresh strata sample")
    ap.add_argument("--strata-sample", type=int, default=12,
                    help="rows per (evidence_strength x tier) cell in verification_sample_strata.csv (0 = skip)")
    ap.add_argument("--include-fuzzy", action="store_true",
                    help="admit the resolver's resolved_fuzzy rows at strength weak (verification strata only; "
                         "never the moderate+ PurpleLab file)")
    ap.add_argument("--min-strength", default="weak", choices=["weak", "moderate", "strong", "exact_id"],
                    help="rows below this evidence strength go to the quarantine instead of the label")
    a = ap.parse_args()
    cdb = pd.read_csv(a.case_db, dtype=str) if a.case_db.endswith(".csv") else pd.read_parquet(a.case_db)
    dn = pd.read_parquet(a.defendant_npis)
    pdim = pd.read_parquet(a.provider_dim)
    ex = pd.concat([pd.read_parquet(x) for x in a.exclusion_nodes], ignore_index=True) if a.exclusion_nodes else None
    labels, quar = build_strict_labels(cdb, dn, pdim, a.policy, ex, include_fuzzy=a.include_fuzzy)
    rank = {"weak": 0, "moderate": 1, "strong": 2, "exact_id": 3}
    if a.min_strength != "weak" and len(labels):
        low = labels["evidence_strength"].map(rank).fillna(0) < rank[a.min_strength]
        dropped = labels[low].copy()
        dropped["reason"] = "evidence strength below --min-strength " + a.min_strength + ": " + dropped["evidence_strength"]
        quar = pd.concat([quar, dropped], ignore_index=True)
        labels = labels[~low].reset_index(drop=True)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    labels.to_csv(out / "labels_fca_strict.csv", index=False)
    quar.to_csv(out / "labels_fca_quarantine.csv", index=False)
    if len(labels):
        labels.sample(min(a.sample, len(labels)), random_state=7).to_csv(out / "verification_sample.csv", index=False)
        if a.strata_sample:
            rungs = [x.strip() for x in a.strata_rungs.split(",") if x.strip()] if a.strata_rungs else None
            stratified_sample(labels, a.strata_sample, a.strata_seed, rungs).to_csv(out / "verification_sample_strata.csv", index=False)
        if a.include_fuzzy:
            # the fuzzy tier's own batch: every fuzzy row (they are few and every
            # one needs a verdict before it can train), not a sample of the weak cell
            fz = fuzzy_rows(labels)
            fz.to_csv(out / "verification_sample_fuzzy.csv", index=False)
            print(f"  fuzzy business tier: {len(fz):,} rows to verify -> {out / 'verification_sample_fuzzy.csv'}")
    rep = out / "FCA_LABEL_EXPORT.md"
    rep.write_text(report(labels, quar, a.policy), encoding="utf-8")
    print(f"strict FCA label ({a.policy}): {len(labels):,} rows, {labels['npi'].nunique():,} NPIs, "
          f"{len(quar):,} quarantined -> {out / 'labels_fca_strict.csv'}\n  report: {rep}\n  verify by hand: {out / 'verification_sample.csv'}"
          + (f"\n  stratified (weak first): {out / 'verification_sample_strata.csv'}" if a.strata_sample and len(labels) else ""))


if __name__ == "__main__":
    main()
