"""
dossier.py — render the minimum target dossier for a scored organization.

The product artifact a human (and eventually counsel) actually reads. Follows the
"minimum target dossier" spec (docs/platform/10-workflows.md W2, master strategy):
entity summary, program exposure, top anomaly drivers, ALTERNATIVE EXPLANATIONS
(mandatory — defamation safety), graph context, and the review disclaimer.

These are investigative hypotheses for human review — never accusations. The
renderer hard-codes that frame; do not remove the disclaimer or the benign
explanations to "clean up" the output.
"""

from __future__ import annotations

import pandas as pd

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


def render_dossier(row: pd.Series, subscore_cols: list[str],
                   coverage: dict[str, list[str]]) -> str:
    """One org's dossier as Markdown. ``row`` is a joined scored-org row."""
    name = row.get("org_name") or row.get("org_node_id")
    lines = [f"# Target dossier — {name}\n", DISCLAIMER]

    lines.append("\n## Entity summary\n")
    lines.append(f"- Canonical org: `{row.get('org_node_id')}`  "
                 f"(basis: {row.get('merge_basis', '?')}, "
                 f"confidence: {row.get('merge_confidence', '?')})\n")
    lines.append(f"- Constituent NPIs: {row.get('n_constituent_npis', '?')} "
                 f"({str(row.get('member_npis', ''))[:120]})\n")
    lines.append(f"- State(s): {row.get('addr_state', '?')} · "
                 f"taxonomy: {row.get('primary_taxonomy', '?')}\n")
    if row.get("aliases"):
        lines.append(f"- Known aliases: {row.get('aliases')}\n")

    lines.append("\n## Risk picture (drivers, not a bare score)\n")
    lines.append(f"- **Scheme hypothesis:** {row.get('scheme_hypothesis')} "
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
        scheme = c.replace("subscore_", "")
        feats = ", ".join(coverage.get(scheme, []))
        lines.append(f"    - {scheme}: {round(float(row.get(c, 0)), 3)}"
                     f"  (features: {feats})\n")
    if str(row.get("clinical_implausibility_driver") or ""):
        lines.append(f"- Clinical-implausibility driver: "
                     f"{row.get('clinical_implausibility_driver')}\n")

    lines.append("\n## Graph context\n")
    lines.append(f"- Hops to nearest exclusion: {row.get('excluded_party_distance')}"
                 f" · related-party density: {row.get('related_party_density')}"
                 f" · co-location cluster: {row.get('co_location_cluster_size')}\n")
    lines.append(f"- Ring membership: shell cluster={row.get('in_shell_cluster')}, "
                 f"excluded-owner cluster={row.get('in_excluded_owner_cluster')}\n")

    def _dollars(value) -> str:
        """NaN/missing dollars render as 'unknown', never as '$nan' in a
        counsel-facing artifact (found by probing)."""
        v = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        return f"${float(v):,.0f}" if pd.notna(v) else "unknown (payments not yet loaded)"

    lines.append("\n## Exposure (size proxy, NOT case value)\n")
    if str(row.get("exposure_scope", "")) == "scheme_code_family":
        lines.append(f"- Payments at issue ({row.get('scheme_hypothesis')} code "
                     f"family): {_dollars(row.get('payments_at_issue'))} of "
                     f"{_dollars(row.get('payments'))} total annual billing\n")
        lines.append(f"- × recovery multiplier "
                     f"{row.get('scheme_recovery_multiplier')} "
                     f"= exposure {_dollars(row.get('exposure'))}\n")
    else:
        lines.append(f"- Annual program payments: {_dollars(row.get('payments'))} × "
                     f"recovery multiplier {row.get('scheme_recovery_multiplier')} "
                     f"= exposure {_dollars(row.get('exposure'))} "
                     f"(scope: all payments — no code family for this scheme)\n")
    lines.append(f"- **ERV (expected recoverable value): {_dollars(row.get('erv'))}**\n")

    if pd.notna(row.get("public_disclosure_flag", pd.NA)):
        lines.append("\n## Public-disclosure screen (31 U.S.C. §3730(e)(4))\n")
        if int(row.get("public_disclosure_flag", 0)) == 1:
            lines.append(f"- **FLAGGED — allegations may already be public.** "
                         f"Counsel must assess the public-disclosure bar before "
                         f"any relator outreach.\n")
            lines.append(f"- Citations: {row.get('public_disclosure_citations')}\n")
        else:
            lines.append(f"- No name matches (checked "
                         f"{row.get('disclosure_sources_checked')}). This is a "
                         f"screen, not clearance — counsel makes the call.\n")

    lines.append("\n## Alternative explanations (must be ruled out)\n")
    for alt in ALTERNATIVE_EXPLANATIONS:
        lines.append(f"- {alt}\n")

    lines.append("\n## Next steps\n")
    lines.append("- Human review of drivers against raw billing detail.\n")
    lines.append("- Witness-map request (Model B) only after review confirms the "
                 "hypothesis is worth pursuing.\n")
    return "".join(lines)
