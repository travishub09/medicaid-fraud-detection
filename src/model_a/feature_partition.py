"""
feature_partition.py — split the feature set into CORE vs EXTRA by data source.

Settles the "keep it simple" debate mechanically instead of by argument. The
CORE files are the five the detection core was built on (the data runbook's
Block 0): NPPES, the LEIE exclusion list, PECOS enrollment, the CMS All-Owners
files, and the Medicaid spending fact. Every feature the manifest records under
`sources_used` traces to a source, so we can label each trainable column CORE
(derivable from those five files) or EXTRA (needs one of the ~25 additional
sources), and hand a modeler two explicit column lists to ablate.

This is what makes the source-ablation test turnkey: "core vs full" stops being
a phrase and becomes two named sets, and "which extra source pulls weight"
becomes a leave-one-source-out over the EXTRA groups.
"""

from __future__ import annotations

# Sources derivable from the five core files (data runbook Block 0).
# Spending fact: the per-NPI billing stats, billing LM, sector/drug/growth/
# plausibility/facility-code signals, residuals, consistency. NPPES: identity +
# address/co-location (shell_score lives here). Owners: ownership churn/
# integrity. LEIE: the label + exclusion proximity. PECOS: enrollment.
CORE_SOURCES = {
    "spending", "provider_stats", "billing_lm", "drug_markup", "sector_schemes",
    "growth", "plausibility", "facility_code", "expected_billing", "consistency",
    "v3", "v3_concepts",
    "entity_graph", "graph", "graph_embeddings", "address",
    "owners", "ownership_churn", "ownership",
    "label", "smoking_gun_timeline", "leie",
    "pecos", "nucc", "nucc_taxonomy",       # nucc is peer-grouping support, not a signal source
}

# Everything below needs a file OUTSIDE the five core inputs.
EXTRA_SOURCE_GROUPS = {
    "medicare_puf": {"partb", "partd", "dmepos", "partb_trend", "partd_trend",
                     "dmepos_trend", "opioid_trend"},
    "open_payments": {"open_payments", "kickback"},
    "opioid": {"opioid"},
    "hrsa_340b": {"hrsa_340b"},
    "saturation": {"saturation"},
    "facility_quality": {"facility", "pbj"},
    "hcris": {"hcris"},
    "pos": {"pos"},
    "deactivation": {"nppes_deactivation"},
    "death_master": {"death_master"},
    "nadac": {"nadac"},
    "docgraph": {"docgraph", "order_referring"},
    "doj_case": {"doj_case"},
}

_SRC_TO_GROUP = {s: g for g, ss in EXTRA_SOURCE_GROUPS.items() for s in ss}

# Derived features computed from the spending fact / NPPES themselves (they are
# not listed under a source in sources_used because the export builds them):
# residual twins, consistency flags, weak-supervision target. All CORE.
_DERIVED_CORE = {
    "billing_residual", "expected_net_paid", "volume_residual",
    "expected_service_volume", "consistency_flags", "incons_solo_scale",
    "incons_instant_scale", "incons_breadth", "incons_lone_org_scale",
    "weak_label_score", "weak_label", "org_member_count", "has_excluded_owner",
    "co_location_cluster_size", "related_party_density", "shell_score",
    "betweenness", "within_2_hops_of_exclusion", "tenure_months",
    "n_active_months", "n_distinct_hcpcs", "gross_paid", "net_paid",
    "service_volume", "total_claim_lines",
}


def _trainable_set(manifest: dict) -> set:
    """The columns a model may train on (features minus the hard-leak fence)."""
    fams = (manifest.get("raw_feature_cols", []) + manifest.get("peerpct_cols", [])
            + manifest.get("subscore_cols", []) + manifest.get("embedding_cols", []))
    return set(fams) - set(manifest.get("leakage_hard", []))


def partition_features(manifest: dict) -> dict:
    """Split trainable features into core vs extra-by-group using sources_used.

    Returns {core: [...], extra: {group: [...]}, unmapped: [...],
    core_sources: [...], extra_sources: [...]}. A feature attributed to no
    source falls to `unmapped` (surfaced, never silently dropped)."""
    trainable = _trainable_set(manifest)
    sources_used = manifest.get("sources_used", {})
    scheme_cov = manifest.get("scheme_coverage", {})

    # invert: feature -> the sources that produced it
    feat_sources: dict[str, list[str]] = {}
    for src, cols in sources_used.items():
        for c in cols:
            feat_sources.setdefault(c, []).append(src)

    def _resolve(c: str) -> list[str]:
        """Sources for a column, resolving the three derived forms sources_used
        does not list directly: X__peerpct inherits X's source; subscore_S
        inherits its scheme's input sources; known spending/NPPES-derived
        columns are core."""
        if c in feat_sources:
            return feat_sources[c]
        if c.endswith("__peerpct"):
            return _resolve(c[: -len("__peerpct")])
        if c.startswith("subscore_"):
            scheme = c[len("subscore_"):]
            srcs: list[str] = []
            for inp in scheme_cov.get(scheme, []):
                srcs.extend(_resolve(inp))
            return srcs
        if c in _DERIVED_CORE or c.startswith(("billing_emb_", "graph_emb_",
                                               "addr_", "sequence_", "billing_")):
            return ["spending"]        # spending/NPPES-derived → core
        return []

    core: list[str] = []
    extra: dict[str, list[str]] = {}
    unmapped: list[str] = []
    core_srcs: set[str] = set()
    extra_srcs: set[str] = set()

    for c in sorted(trainable):
        srcs = _resolve(c)
        if not srcs:
            unmapped.append(c)
            continue
        if any(s in CORE_SOURCES for s in srcs):
            core.append(c)
            core_srcs.update(s for s in srcs if s in CORE_SOURCES)
        else:
            grp = next((_SRC_TO_GROUP.get(s) for s in srcs if s in _SRC_TO_GROUP), None) \
                or "other_extra"
            extra.setdefault(grp, []).append(c)
            extra_srcs.update(srcs)

    return {
        "core": core,
        "extra": {g: sorted(cs) for g, cs in sorted(extra.items())},
        "unmapped": unmapped,
        "core_sources": sorted(core_srcs),
        "extra_sources": sorted(extra_srcs),
        "n_core": len(core),
        "n_extra": sum(len(cs) for cs in extra.values()),
    }


def full_feature_list(part: dict) -> list[str]:
    """Core plus every extra group (the full model's columns)."""
    out = list(part["core"])
    for cs in part["extra"].values():
        out.extend(cs)
    return sorted(dict.fromkeys(out))


def main() -> None:
    import argparse
    import json
    from pathlib import Path
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", default=None, help="write the partition JSON here")
    args = ap.parse_args()
    manifest = json.loads(Path(args.manifest).read_text())
    part = partition_features(manifest)
    if args.out:
        Path(args.out).write_text(json.dumps(part, indent=2), encoding="utf-8")
    print(f"[feature_partition] core={part['n_core']} features from "
          f"{len(part['core_sources'])} core sources; extra={part['n_extra']} "
          f"features across {len(part['extra'])} groups: "
          f"{', '.join(f'{g}({len(cs)})' for g, cs in part['extra'].items())}")
    if part["unmapped"]:
        print(f"  unmapped (no source): {len(part['unmapped'])} → "
              f"{part['unmapped'][:8]}")


if __name__ == "__main__":
    main()
