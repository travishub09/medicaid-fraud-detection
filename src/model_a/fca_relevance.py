"""
fca_relevance.py — does a label source look like a False Claims Act case?

The platform exists to originate qui tam cases. A label that does not
resemble an FCA prosecution teaches the model the wrong thing: the OIG
exclusion list mixes felony healthcare fraud with student-loan defaults,
license revocations for any reason, and patient abuse; the Medicare
revocation report mixes abuse of billing privileges with "not operational
at the address on file". Five frozen runs sat at the same forward-lift
ceiling while features grew — the answer key was partly measuring the
wrong thing. This module is the filter.

Every exclusion/revocation record is classified into a TIER from its
statute or reason code (already carried in ``excl_type``):

  hard      looks like an FCA case: healthcare-fraud convictions, fraud and
            kickback exclusions, excessive/unnecessary claims, breach of a
            corporate integrity or settlement agreement (a CIA IS an FCA
            settlement), abuse/misuse of billing privileges, false or
            misleading enrollment information.
  weak      real misconduct signal that is not FCA on its face: license
            revocations of unstated basis, patient abuse, controlled-
            substance offenses, derivative exclusions (owned/controlled by
            a sanctioned person; excluded by another agency), prescribing
            abuses, QIO sanctions, Part D preclusion.
  excluded  not FCA-relevant: loan default, failure to supply information
            or grant access, HMO corrective-action failures, non-compliance
            and not-operational revocations, OpenSanctions, SAM debarments
            without a healthcare-fraud cause.

Used by the forward label (``prospective_label --fca-tier``) and, next, by
the training label. The tiers are conservative and documented per code so
counsel can audit the mapping; ``weak`` rows belong in weak supervision,
never as hard positives.
"""

from __future__ import annotations

import re

import pandas as pd

TIERS = ("hard", "weak", "excluded")

# OIG LEIE EXCLTYPE codes -> (tier, plain-English basis)
LEIE_STATUTES: dict[str, tuple[str, str]] = {
    "1128a1": ("hard", "conviction: program-related crime"),
    "1128a3": ("hard", "conviction: felony healthcare fraud"),
    "1128b1": ("hard", "conviction: misdemeanor healthcare fraud"),
    "1128b6": ("hard", "excessive claims / unnecessary services"),
    "1128b7": ("hard", "fraud, kickbacks, prohibited activities"),
    "brchcia": ("hard", "breach of corporate integrity agreement"),
    "brchsa": ("hard", "breach of settlement agreement"),
    "1128b16": ("hard", "false statements / omissions on enrollment"),
    "1128aa": ("hard", "civil monetary penalty: false or fraudulent claims / kickbacks (1128A(a))"),
    "1128c3gi": ("weak", "second mandatory exclusion, 10-year minimum (underlying conviction unstated)"),
    "1160": ("weak", "PSRO / peer-review sanction (historic, pre-1986)"),
    "1128a2": ("weak", "conviction: patient abuse or neglect"),
    "1128a4": ("weak", "conviction: felony controlled substance"),
    "1128b2": ("weak", "conviction: obstruction of investigation"),
    "1128b3": ("weak", "conviction: misdemeanor controlled substance"),
    "1128b4": ("weak", "license revocation or surrender (basis unstated)"),
    "1128b5": ("weak", "excluded or suspended by another federal/state program"),
    "1128b8": ("weak", "entity controlled by a sanctioned individual"),
    "1128b15": ("weak", "owner/officer of an excluded entity"),
    "1128c3gii": ("weak", "conviction-based exclusion (derivative)"),
    "1156": ("weak", "QIO sanction: substandard or unnecessary care"),
    "1128b9": ("excluded", "failure to disclose required information"),
    "1128b10": ("excluded", "failure to supply subcontractor information"),
    "1128b11": ("excluded", "failure to supply payment information"),
    "1128b12": ("excluded", "failure to grant immediate access"),
    "1128b13": ("excluded", "HMO failure to take corrective action"),
    "1128b14": ("excluded", "default on health education loan"),
}

# Medicare revocation reasons (42 CFR 424.535(a)) — keyword rules on the
# REVOCATION_RSN text the adapter preserves after "medicare_revocation: ".
# First match wins; ordered most specific -> least.
REVOCATION_RULES: list[tuple[str, str, str]] = [
    (r"abuse of billing", "hard", "424.535(a)(8) abuse of billing privileges"),
    (r"misuse of billing", "hard", "424.535(a)(7) misuse of billing number"),
    (r"false|misleading", "hard", "424.535(a)(4) false or misleading information"),
    (r"fraud|kickback|false claim", "hard", "felony: healthcare fraud"),
    # tier_transitions (Sept 2026, full exclusion nodes): 20.4% of (a)(3)
    # felony revocations and 16.6% of (a)(12) program terminations are
    # followed by a fraud-tier event within ~14-18 months; no other
    # revocation ground exceeds 5%. They are the front edge of a case.
    (r"felon", "hard", "424.535(a)(3) felony (20% convert to a fraud-tier event, median 14 mo)"),
    (r"program termination", "hard",
     "424.535(a)(12) terminated by another program (17% convert to a fraud-tier event, median 18 mo)"),
    (r"prescrib", "weak", "prescribing abuses"),
    (r"exclusion|debar", "weak", "424.535(a)(2) derivative of exclusion/debarment"),
    (r"licen", "weak", "license action (basis unstated)"),
    # the four reasons the first tier audit (Sept 2026) found unmapped
    (r"affiliation", "weak", "424.535(a)(19) affiliation posing undue risk (derivative)"),
    (r"provider and supplier", "weak",
     "424.535(a)(23) provider/supplier standards violation"),
    (r"treasury|debt", "excluded", "424.535(a)(17) debt referred to Treasury (collections)"),
    # (a)(5): CMS visited the address on file and found no practice there
    # while claims were flowing. A claim for a service at a site that does
    # not exist is a false claim by definition — the phantom-site pattern
    # (DME, home health, shell labs). NOT paperwork; weak until the billing
    # fact confirms claims continued into the non-operational period.
    (r"on-?site review|not operational|non-?operational", "weak",
     "424.535(a)(5) not operational at the address on file (phantom-site indicator)"),
    # DME supplier standards: 0.1% convert (tier_transitions) — paperwork
    (r"dme standards|supplier standards", "excluded",
     "424.535(a)(1) DME supplier standards not met"),
    (r"non-?compliance|failure to report|"
     r"failure to document|failure to provide access|screening|"
     r"reporting|address|cms.?855|application", "excluded",
     "administrative / operational revocation"),
]
_TIER_RANK = {"hard": 2, "weak": 1, "excluded": 0}

# State Medicaid termination / exclusion lists (enforcement.state_terminations):
# excl_type = "state_term:<ST>:<reason text> [provider type]|name_match". The reason
# text is the state's own words, so the rules read for the FCA-shaped causes and
# fall back to WEAK, never hard: "terminated for cause" on its own is not a fraud
# finding, and an entry that merely mirrors a federal exclusion carries no
# statute of its own (the LEIE row of the same NPI already sets the tier).
STATE_TERM_RULES = [
    (r"fraud|false claim|kickback|anti-?kickback|bribe|credible allegation|program integrity|"
     r"mfcu|medicaid fraud control|convict|felony|guilty|indict|plea|theft|embezzl|forg|"
     r"misrepresent|abuse of billing|billing (abuse|irregularit|fraud)|overbill|upcod|"
     r"phantom|false (record|statement|document)|health ?care (fraud|offense)", "hard",
     "state termination for a fraud-shaped cause"),
    (r"oig|leie|federal exclusion|1128|hhs exclusion|medicare exclusion|sam\b|reciprocal|"
     r"another state|other state|derivative", "weak",
     "state action derivative of a federal or other-state exclusion (the underlying record sets the tier)"),
    (r"licens|board action|certification|credential|revok|surrender", "weak",
     "state termination on a license or credential action"),
    (r"patient abuse|neglect|abuse of (a |an )?(patient|recipient|member|resident)|"
     r"assault|sexual|exploitation of", "weak",
     "state termination for patient abuse or neglect (not FCA-shaped on its face)"),
    (r"overpayment|failure to (repay|return|refund)|debt", "weak",
     "state termination for an unpaid overpayment"),
    (r"revalidat|enrollment|screening|fingerprint|non-?compli|failure to (report|disclose|respond|cooperate|submit|maintain)|"
     r"record ?keeping|documentation|paperwork|address|application|ownership|site visit|not operational", "weak",
     "state termination on an administrative or enrollment ground"),
    (r"^(terminat|exclu|suspen|sanction|debar)[a-z ]*(for cause)?$", "weak",
     "state termination with no cause stated beyond the action itself"),
]


def _classify_state_reason(reason: str) -> tuple[str, str]:
    r = reason.lower()
    for pat, tier, basis in STATE_TERM_RULES:
        if re.search(pat, r):
            return tier, basis
    if not r or r.startswith("unspecified"):
        return "weak", "state termination with no reason recorded"
    return "weak", f"state termination reason unmapped: {reason.strip()[:60]}"


def _classify_reason(reason: str) -> tuple[str, str]:
    for pat, tier, basis in REVOCATION_RULES:
        if re.search(pat, reason):
            return tier, basis
    return "weak", f"revocation reason unmapped: {reason.strip()[:60]}"

_NORM = re.compile(r"[^a-z0-9]")
_CODES_LONGEST_FIRST = sorted(LEIE_STATUTES, key=len, reverse=True)


def fca_tier(excl_type) -> tuple[str, str]:
    """Classify one exclusion-record ``excl_type`` into (tier, basis)."""
    t = str(excl_type or "").strip().lower()
    if not t:
        return "weak", "no statute or reason recorded"
    if t.startswith("opensanctions"):
        return "excluded", "OpenSanctions: not an FCA source"
    if t.startswith("sam"):
        if re.search(r"fraud|health|medicare|medicaid", t):
            return "weak", "SAM debarment with a healthcare cause"
        return "excluded", "SAM debarment without a healthcare-fraud cause"
    if t.startswith("preclusion"):
        return "weak", "Part D preclusion list"
    if t.startswith("medicare_revocation"):
        reason = t.split(":", 1)[1] if ":" in t else ""
        # a revocation often cites SEVERAL grounds ("(a)(9) failure to
        # report;(a)(3) felonies"): classify each and keep the strongest —
        # a felony ground is not neutralized by a paperwork ground beside it
        parts = [p.strip() for p in reason.split(";") if p.strip()] or [reason]
        best = max((_classify_reason(p) for p in parts),
                   key=lambda tb: _TIER_RANK.get(tb[0], -1))
        return best
    if t.startswith("state_term"):
        # "state_term:<ST>:<reason> [type]|name_match" -> the reason only
        body = t.split(":", 2)[2] if t.count(":") >= 2 else ""
        body = re.sub(r"\|name_match$", "", body)
        body = re.sub(r"\s*\[[^\]]*\]\s*$", "", body)
        return _classify_state_reason(body)
    if t.startswith("doj") or t.startswith("case"):
        return "hard", "DOJ / court case (direct tier)"
    # LEIE statute codes: normalize "1128(a)(1)", "1128 A1", "BRCH CIA" -> key
    key = _NORM.sub("", t)
    if key in LEIE_STATUTES:
        return LEIE_STATUTES[key]
    # trailing text ("1128a1 conviction of ...") -> LONGEST matching code, so
    # 1128b14 (loan default) is never swallowed by the 1128b1 (fraud) prefix
    for code in _CODES_LONGEST_FIRST:
        if key.startswith(code):
            return LEIE_STATUTES[code]
    if key.startswith("1128"):
        return "weak", f"LEIE statute unmapped: {excl_type}"
    return "weak", f"unrecognized source: {str(excl_type)[:60]}"


def classify_series(excl_type: pd.Series) -> pd.DataFrame:
    """Vectorized: returns a frame with fca_tier + fca_basis aligned to input."""
    pairs = excl_type.map(fca_tier)
    return pd.DataFrame({
        "fca_tier": pairs.map(lambda p: p[0]),
        "fca_basis": pairs.map(lambda p: p[1]),
    }, index=excl_type.index)


def tier_mask(tiers: pd.Series, keep: str) -> pd.Series:
    """Boolean mask for a --fca-tier policy: 'all' | 'hard' | 'hard+weak'."""
    keep = (keep or "all").lower()
    if keep == "all":
        return pd.Series(True, index=tiers.index)
    if keep == "hard":
        return tiers == "hard"
    if keep in ("hard+weak", "hardweak", "hard_weak"):
        return tiers.isin(["hard", "weak"])
    raise ValueError(f"unknown --fca-tier policy {keep!r}")


def tier_report(df: pd.DataFrame, positive_col: str = "is_prospective_positive") -> str:
    """Markdown table: positives per tier and basis (the audit counsel reads)."""
    if "fca_tier" not in df.columns:
        return "_no fca_tier column_"
    pos = df[df[positive_col] == 1] if positive_col in df.columns else df
    lines = ["| tier | basis | positives |", "|---|---|--:|"]
    g = pos.groupby(["fca_tier", "fca_basis"]).size().sort_values(ascending=False)
    order = {"hard": 0, "weak": 1, "excluded": 2}
    for (tier, basis), n in sorted(g.items(), key=lambda kv: (order.get(kv[0][0], 9), -kv[1])):
        lines.append(f"| {tier} | {basis} | {n:,} |")
    tot = pos.groupby("fca_tier").size()
    lines.append("")
    lines.append("Totals: " + ", ".join(
        f"{t} = {int(tot.get(t, 0)):,}" for t in TIERS))
    return "\n".join(lines)
