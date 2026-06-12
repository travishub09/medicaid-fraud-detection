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
    lines.append(f"- Composite org_prob (noisy-OR): {row.get('org_prob')}  →  "
                 f"adjusted {row.get('adjusted_prob')} "
                 f"(sector prior ×{row.get('sector_prior')}, "
                 f"graph boost +{row.get('graph_risk_boost')})\n")
    lines.append("- Per-scheme subscores:\n")
    for c in subscore_cols:
        scheme = c.replace("subscore_", "")
        feats = ", ".join(coverage.get(scheme, []))
        lines.append(f"    - {scheme}: {round(float(row.get(c, 0)), 3)}"
                     f"  (features: {feats})\n")

    lines.append("\n## Graph context\n")
    lines.append(f"- Hops to nearest exclusion: {row.get('excluded_party_distance')}"
                 f" · related-party density: {row.get('related_party_density')}"
                 f" · co-location cluster: {row.get('co_location_cluster_size')}\n")
    lines.append(f"- Ring membership: shell cluster={row.get('in_shell_cluster')}, "
                 f"excluded-owner cluster={row.get('in_excluded_owner_cluster')}\n")

    lines.append("\n## Exposure (size proxy, NOT case value)\n")
    lines.append(f"- Annual program payments: ${float(row.get('payments', 0)):,.0f} × "
                 f"recovery multiplier {row.get('scheme_recovery_multiplier')} "
                 f"= exposure ${float(row.get('exposure', 0)):,.0f}\n")
    lines.append(f"- **ERV (expected recoverable value): "
                 f"${float(row.get('erv', 0)):,.0f}**\n")

    lines.append("\n## Alternative explanations (must be ruled out)\n")
    for alt in ALTERNATIVE_EXPLANATIONS:
        lines.append(f"- {alt}\n")

    lines.append("\n## Next steps\n")
    lines.append("- Human review of drivers against raw billing detail.\n")
    lines.append("- Witness-map request (Model B) only after review confirms the "
                 "hypothesis is worth pursuing.\n")
    return "".join(lines)
