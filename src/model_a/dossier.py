"""
dossier.py — render the target dossier for a scored organization.

The product artifact a human (and eventually counsel) actually reads. It leads
with a plain-English narrative — what the model thinks it found, what each signal
means relative to peers, and how the signals combine into a pattern — then gives
the detailed drivers, graph context, exposure, mandatory ALTERNATIVE EXPLANATIONS
(defamation safety), and the review disclaimer.

These are investigative hypotheses for human review — never accusations. The
renderer hard-codes that frame; do not remove the disclaimer or the benign
explanations to "clean up" the output.
"""

from __future__ import annotations

import pandas as pd

# --- plain-English translations ------------------------------------------------

# scheme → (short label, plain-English description of the pattern / fraud theory)
SCHEME_NARRATIVES = {
    "single_service_mill": ("a single-service mill",
        "almost all revenue funneled through a tiny set of service codes — the "
        "shape of a high-volume, one-procedure operation"),
    "payment_outlier": ("a payment outlier",
        "paid materially more per patient or per claim than peers — the shape "
        "upcoding or inflated-unit billing produces"),
    "overutilization": ("over-utilization",
        "far more services delivered per patient than peers — the shape of "
        "medically-unnecessary volume"),
    "specialty_mismatch": ("a specialty mismatch",
        "billing procedure codes that providers in its specialty rarely use — "
        "consistent with out-of-scope or miscoded billing"),
    "rapid_ramp": ("a rapid ramp",
        "billing that spun up or spiked abnormally over the window — the trajectory "
        "enforcement often sees before a scheme is caught"),
    "ownership_integrity": ("an ownership-integrity concern",
        "a structure resembling concealment: shared owners across many entities, "
        "thin shell-like entities at clustered addresses, or proximity to a party "
        "excluded from federal health programs"),
    "upcoding": ("upcoding", "a billing mix skewed toward higher-paying service levels"),
    "drug_outlier": ("a drug-billing outlier",
        "high-cost or controlled-substance billing above peers"),
    "dme_ring": ("a DME-ring shape",
        "durable-equipment billing patterns enforcement associates with referral rings"),
    "worthless_services": ("a worthless-services concern",
        "billing volume out of line with the capacity or quality to deliver it"),
    "hospice_ineligibility": ("a hospice-eligibility concern",
        "patterns associated with billing for patients who may not qualify"),
    "pill_mill": ("a pill-mill shape", "opioid prescribing concentration above peers"),
    "invalid_identity": ("an identity-integrity concern",
        "billing tied to a deactivated or deceased identifier"),
    "cost_report_fraud": ("a cost-report concern",
        "cost ratios out of line with peer facilities"),
}

# concept percentile column → plain-English description of what it measures
CONCEPT_NARRATIVES = {
    "concentration":     "billing concentrated in very few service codes",
    "payment_intensity": "paid more per patient/claim than peers",
    "service_intensity": "more services delivered per patient than peers",
    "specialty_mismatch": "billing codes rarely used by its specialty",
    "temporal":          "abnormal growth or month-to-month billing volatility",
}

ALTERNATIVE_EXPLANATIONS = [
    "Legitimate referral center / subspecialty practice (case-mix not yet controlled)",
    "Peer group too coarse for this niche specialty or geography",
    "Billing-policy or code-definition change during the window",
    "Data-quality artifact (state reporting gaps; ~2-year file lag)",
    "Ownership-data lag (stale or recently divested ownership records)",
    "Legitimate aggregate/locum billing concentrated on one NPI",
]

DISCLAIMER = (
    "> **This dossier is an investigative hypothesis for human review.** It is a "
    "statistical description of how this organization's billing and structure "
    "compare with peers in public data. It is not an accusation, a legal "
    "conclusion, or evidence of fraud.\n")


def _pctile_phrase(p: float) -> str:
    p = float(p)
    if p >= 0.999:
        return "in the top ~0.1% of peers"
    return f"in the top {max(0.1, (1.0 - p) * 100):.1f}% of peers"


def _num(row, key):
    v = pd.to_numeric(pd.Series([row.get(key)]), errors="coerce").iloc[0]
    return None if pd.isna(v) else float(v)


def _firing_signals(row) -> list[tuple[str, float]]:
    """The concept percentiles that actually elevated this org, strongest first."""
    out = []
    for concept in CONCEPT_NARRATIVES:
        v = _num(row, concept)
        if v is not None and v >= 0.75:        # only genuinely elevated signals
            out.append((concept, v))
    out.sort(key=lambda kv: -kv[1])
    return out


def _graph_signals(row) -> list[str]:
    """Plain-English structural/graph findings, when present."""
    s = []
    if _num(row, "within_2_hops_of_exclusion") and _num(row, "within_2_hops_of_exclusion") >= 1:
        d = row.get("excluded_party_distance")
        s.append(f"sits within {d} ownership/address hop(s) of a party excluded "
                 f"from federal health programs")
    if _num(row, "shell_score") and _num(row, "shell_score") >= 0.5:
        s.append("has a thin, name-only structure at a clustered address "
                 "(shell-like)")
    rpd = _num(row, "related_party_density")
    if rpd and rpd >= 2:
        s.append(f"shares owners with {int(rpd)} other organization(s)")
    coloc = _num(row, "co_location_cluster_size")
    if coloc and coloc >= 3:
        s.append(f"is co-located with {int(coloc)} other organizations at one address")
    return s


def _money(v) -> str:
    return f"${float(v):,.0f}"


def _billing_story(name: str, ev: dict) -> str:
    """A paragraph built from this org's actual billing numbers."""
    paid = ev["total_paid"]
    head = (f"Between {ev['first_month']} and {ev['last_month']}, **{name}** was "
            f"paid **{_money(paid)}** by Medicaid across {ev['n_codes']} distinct "
            f"procedure code(s)")
    if ev.get("n_patients"):
        head += f" and {ev['n_patients']:,} beneficiaries"
    if ev.get("paid_per_patient"):
        head += f" — about **{_money(ev['paid_per_patient'])} per beneficiary**"
    parts = [head + "."]
    if ev.get("top_codes"):
        code, cpaid, share = ev["top_codes"][0]
        s = (f"Its single largest line is procedure code **{code}** at "
             f"{_money(cpaid)} — **{share*100:.0f}%** of everything it billed")
        if len(ev["top_codes"]) >= 3:
            top3 = sum(sh for _, _, sh in ev["top_codes"][:3]) * 100
            nxt = ", ".join(c for c, _, _ in ev["top_codes"][1:3])
            s += f"; with the next two ({nxt}) the top three reach {top3:.0f}%"
        parts.append(s + ".")
    r = ev.get("ramp")
    if r:
        parts.append(
            f"Monthly billing ran from {_money(r['first_month_paid'])} at the start "
            f"to {_money(r['last_month_paid'])} most recently, peaking at "
            f"{_money(r['peak_paid'])} in {r['peak_month']}.")
    return " ".join(parts)


def render_dossier(row: pd.Series, subscore_cols: list[str],
                   coverage: dict[str, list[str]], evidence: dict | None = None) -> str:
    name = row.get("org_name") or row.get("org_node_id")
    scheme = str(row.get("scheme_hypothesis") or "").strip()
    sector = str(row.get("primary_taxonomy") or "").strip()
    label, desc = SCHEME_NARRATIVES.get(scheme, (f"a {scheme or 'billing'} pattern", ""))
    lines = [f"# Target dossier — {name}\n", DISCLAIMER]

    # ---- a story built from this org's actual data ---------------------------
    lines.append("\n## What the data shows\n")
    if evidence:
        lines.append(_billing_story(name, evidence) + "\n")
        lines.append(f"\n*Every figure above is drawn from the "
                     f"{evidence['provenance']}; the structural facts below come "
                     f"from NPPES, the CMS ownership files, and the OIG exclusion "
                     f"list.*\n")
        lines.append(f"\n**The model's read:** against its specialty-and-geography "
                     f"peers, this most resembles **{label}** — {desc or scheme}.\n")
    else:
        exposure_txt = _dollars(row.get("payments"))
        lines.append(
            f"Among ~9 million organizations, this one surfaced because its billing "
            f"and structure most resemble **{label}** — {desc or scheme}. It bills "
            f"roughly {exposure_txt} of Medicaid a year, which is what makes the "
            f"pattern worth a look rather than noise.\n")

    signals = _firing_signals(row)
    graph = _graph_signals(row)
    if signals or graph:
        lines.append("\n**What the signals picked up** (each compares this org to "
                     "its specialty-and-geography peers):\n")
        for concept, v in signals:
            strength = "extreme" if v >= 0.95 else "elevated"
            lines.append(f"- *{strength}* — {CONCEPT_NARRATIVES[concept]}: "
                         f"{_pctile_phrase(v)} ({concept} percentile {v:.2f})\n")
        for g in graph:
            lines.append(f"- *structural* — {g}\n")
    else:
        lines.append("\n*No single concept is extreme; this org ranks on the "
                     "combination of moderate signals plus its billing size.*\n")

    # the pattern synthesis
    if len(signals) >= 2:
        top2 = " and ".join(CONCEPT_NARRATIVES[c] for c, _ in signals[:2])
        concealment = " plus a concealment-type structure" if graph else ""
        lines.append(
            f"\n**The pattern:** {top2} appearing together{concealment} is the "
            f"combination the model associates with {label}. No single signal is "
            f"proof; it is the *co-occurrence*, at this dollar scale, that ranked it.\n")
    elif graph and not signals:
        lines.append(
            "\n**The pattern:** the flag here is structural (ownership/address), "
            "not billing — the model is pointing at how the entity is organized, "
            "which a reviewer should weigh differently than a billing anomaly.\n")

    # ---- entity summary ------------------------------------------------------
    lines.append("\n## Entity summary\n")
    lines.append(f"- Canonical org: `{row.get('org_node_id')}`  "
                 f"(basis: {row.get('merge_basis', '?')}, "
                 f"confidence: {row.get('merge_confidence', '?')})\n")
    lines.append(f"- Constituent NPIs: {row.get('n_constituent_npis', '?')} "
                 f"({str(row.get('member_npis', ''))[:120]})\n")
    lines.append(f"- State(s): {row.get('addr_state', '?')} · taxonomy: {sector or '?'}\n")
    if row.get("aliases"):
        lines.append(f"- Known aliases: {row.get('aliases')}\n")

    # ---- the detail (for the analyst) ----------------------------------------
    lines.append("\n## Scoring detail (drivers, not a bare score)\n")
    lines.append(f"- Scheme hypothesis: {scheme or '?'} "
                 f"(top subscore {row.get('top_subscore')})\n")
    if row.get("confidence"):
        lines.append(f"- **Confidence: {str(row.get('confidence')).upper()}** — "
                     f"{row.get('confidence_reasons', '')}\n")
    lines.append(f"- Composite org_prob (noisy-OR): {row.get('org_prob')}  →  "
                 f"adjusted {row.get('adjusted_prob')} "
                 f"(sector prior ×{row.get('sector_prior_base', row.get('sector_prior'))}, "
                 f"gov interest ×{row.get('gov_interest_multiplier', 1.0)}, "
                 f"graph boost +{row.get('graph_risk_boost')})\n")
    if str(row.get("gov_interest_items") or ""):
        lines.append(f"- Government-interest driver: {row.get('gov_interest_items')}\n")
    lines.append("- Per-scheme subscores:\n")
    for c in subscore_cols:
        sc = c.replace("subscore_", "")
        feats = ", ".join(coverage.get(sc, []))
        lines.append(f"    - {sc}: {round(float(row.get(c, 0)), 3)}  (features: {feats})\n")
    if str(row.get("clinical_implausibility_driver") or ""):
        lines.append(f"- Clinical-implausibility driver: "
                     f"{row.get('clinical_implausibility_driver')}\n")
    if str(row.get("enforcement_lookalikes") or ""):
        lines.append(f"- Enforcement lookalikes (corroboration, NOT a driver): "
                     f"{row.get('enforcement_lookalikes')}\n")

    lines.append("\n## Graph context\n")
    lines.append(f"- Hops to nearest exclusion: {row.get('excluded_party_distance')}"
                 f" · related-party density: {row.get('related_party_density')}"
                 f" · co-location cluster: {row.get('co_location_cluster_size')}\n")
    lines.append(f"- Ring membership: shell cluster={row.get('in_shell_cluster')}, "
                 f"excluded-owner cluster={row.get('in_excluded_owner_cluster')}\n")

    lines.append("\n## Exposure (size proxy, NOT case value)\n")
    if str(row.get("exposure_scope", "")) == "scheme_code_family":
        lines.append(f"- Payments at issue ({scheme} code family): "
                     f"{_dollars(row.get('payments_at_issue'))} of "
                     f"{_dollars(row.get('payments'))} total annual billing\n")
        lines.append(f"- × recovery multiplier {row.get('scheme_recovery_multiplier')} "
                     f"= exposure {_dollars(row.get('exposure'))}\n")
    else:
        lines.append(f"- Annual program payments: {_dollars(row.get('payments'))} × "
                     f"recovery multiplier {row.get('scheme_recovery_multiplier')} "
                     f"= exposure {_dollars(row.get('exposure'))} "
                     f"(scope: all payments — no code family for this scheme)\n")
    lines.append(f"- **ERV (expected recoverable value): {_dollars(row.get('erv'))}**  "
                 f"— a triage size proxy (probability × dollars), *not* an estimate "
                 f"of case value.\n")

    if pd.notna(row.get("public_disclosure_flag", pd.NA)):
        lines.append("\n## Public-disclosure screen (31 U.S.C. §3730(e)(4))\n")
        if int(row.get("public_disclosure_flag", 0)) == 1:
            lines.append("- **FLAGGED — allegations may already be public.** Counsel "
                         "must assess the public-disclosure bar before any relator "
                         "outreach.\n")
            lines.append(f"- Citations: {row.get('public_disclosure_citations')}\n")
        else:
            lines.append(f"- No name matches (checked "
                         f"{row.get('disclosure_sources_checked')}). This is a screen, "
                         f"not clearance — counsel makes the call.\n")

    lines.append("\n## Alternative explanations (must be ruled out)\n")
    lines.append("Every signal above has an innocent reading. Before this is treated "
                 "as anything more than a lead, a reviewer must rule out:\n")
    for alt in ALTERNATIVE_EXPLANATIONS:
        lines.append(f"- {alt}\n")

    lines.append("\n## Next steps\n")
    lines.append("- Human review of the drivers above against raw billing detail.\n")
    lines.append("- Witness-map request (Model B) only after review confirms the "
                 "hypothesis is worth pursuing.\n")
    return "".join(lines)


def _dollars(value) -> str:
    """NaN/missing dollars render as 'unknown', never '$nan' in a counsel artifact."""
    v = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return f"${float(v):,.0f}" if pd.notna(v) else "unknown (payments not yet loaded)"
