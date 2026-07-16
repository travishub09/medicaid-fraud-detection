"""
provider_features_export.py — per-PROVIDER (NPI) feature export for the supervised model.

Model A's normal path gates to candidate ORGS, rolls NPIs up to org grain, and
ranks by ERV for dossiers (triage). Travis's LightGBM trains the other way: one
row per NPI, on the full universe (it needs the negatives/unlabeled too, not just
the candidates). This module re-points the SAME scheme-subscore engine
(``scheme_subscores.compute_subscores`` — already grain-agnostic and skip-missing)
at the provider grain and writes a wide training table for his model:

    provider_features_for_model.parquet   one row per NPI:
        identifiers + raw provider stats + every available source's raw features
        + each feature's one-sided taxonomy-peer percentile (``*__peerpct``)
        + the scheme subscores (``subscore_<scheme>``)
        + the PU label (``provider_on_leie``)

Design (kept faithful to the platform's hard rules):
  * Grain is the NPI. The v3 concept percentiles and ``provider_features`` are
    already per-NPI; ORG-grain features (entity-graph ownership signals, and the
    org-grain adapter outputs) are broadcast DOWN to each member NPI via
    ``npi_to_org``; CCN-grain facility features broadcast via a ccn→npi crosswalk.
  * No candidate gate, no payer filter — the supervised model needs the whole
    scored universe. (That gating is correct for ERV triage, wrong for training.)
  * Raw features AND their peer-relative percentiles are both exported. Trees split
    on raw values fine; the percentile is the platform-canonical one-sided robust
    comparison (hard rule #8). Travis's model decides which carries signal.
  * The subscore inputs are peer-relative: v3 concepts and bounded graph features
    pass through (already comparable), every raw ADAPTER metric is converted to its
    one-sided taxonomy-peer percentile first, so a subscore never fires on an
    absolute share that ignores the peer baseline.
  * Label vs. leakage is made explicit, never silent. ``provider_on_leie`` is the
    PU positive label. Columns DERIVED from a provider's own exclusion
    (``billed_after_exclusion``, ``excluded_after_billing``) are emitted but listed
    in the manifest's ``leakage_hard`` block — train on them and the backtest is
    circular. Exclusion-PROXIMITY features (network/owner exclusion) are
    legitimately predictive but correlated; they go in ``leakage_adjacent`` so
    Travis makes the temporal-validation call with eyes open.

CLI (honors MEDICAID_DATA_ROOT; every source is optional / skip-missing):
    python -m src.model_a.provider_features_export \
        --graph-dir ~/Desktop/data/graph \
        --leads ~/Desktop/data/detection/fraud_leads_v3.parquet \
        --preclean ~/Desktop/data/preclean \
        --out ~/Desktop/data/model_a/provider_features
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from .scheme_subscores import compute_subscores, DEFAULT_SCHEME_WEIGHTS

# The five v3 concept percentiles (already peer-relative — pass through to subscores).
V3_CONCEPTS = ["concentration", "payment_intensity", "service_intensity",
               "specialty_mismatch", "temporal"]

# Entity-graph features (bounded 0/1 or normalized in the engine) — pass through.
GRAPH_FEATURES = ["within_2_hops_of_exclusion", "shell_score",
                  "related_party_density", "related_party_density_norm",
                  "co_location_cluster_size", "betweenness", "ownership_turnover"]

# Analytics enrichments (src/analytics): growth-shock + clinical plausibility. They
# arrive already as one-sided percentiles (the registry inputs), so they pass through
# to the subscores exactly like the v3 concepts — never re-peer-normalized.
ANALYTICS_FEATURES = ["clinical_implausibility", "local_volume_implausibility",
                      "growth_level_shift", "new_code_burst"]

# Every feature column the scheme registry references that is NOT a v3 concept or a
# graph feature — i.e. comes from a CMS source adapter. These are peer-normalized to
# one-sided taxonomy percentiles before they feed a subscore.
ADAPTER_FEATURE_COLS = sorted({
    col for wmap in DEFAULT_SCHEME_WEIGHTS.values() for col in wmap
    if col not in V3_CONCEPTS and col not in GRAPH_FEATURES
    and col not in {"clinical_implausibility", "local_volume_implausibility",
                    "growth_level_shift", "new_code_burst"}  # analytics, not adapters
})

# PU positive label for Travis's model.
LABEL_COL = "provider_on_leie"

# Features derived from the provider's OWN exclusion — circular with the label.
LEAKAGE_HARD = ["billed_after_exclusion", "excluded_after_billing",
                "provider_on_leie",
                # §E smoking-gun timelines (exclusion/deactivation/death-derived:
                # case-file enrichment, never trainable)
                *[f"{p}_{c}" for p in ("excl", "deact", "death")
                  for c in ("months_after", "paid_after", "first_after", "last_after")],
                # §E label widening: once these definitional flags feed the label,
                # they are label-derived and must never also be features
                "billing_after_deactivation", "billing_after_death"]

# Exclusion-PROXIMITY features: predictive (rings get caught together) but
# correlated with the label — use only under a strict out-of-time split. Includes
# the NPI-grain owner-role signals (sharper than the smeared graph proximity).
LEAKAGE_ADJACENT = ["within_2_hops_of_exclusion", "shell_score",
                    "related_party_density", "related_party_density_norm",
                    "subscore_ownership_integrity", "has_excluded_owner",
                    "facility_has_excluded_owner_high",
                    "facility_has_excluded_owner_probable",
                    # transforms of leakage_hard events: the peer percentile and
                    # the invalid_identity subscore are built FROM
                    # billing_after_deactivation / billing_after_death, so in-time
                    # they nearly encode the label (run-3 signal ranking: AUC
                    # 0.926). Legitimately predictive FORWARD (deactivation
                    # precedes future exclusion) — the definition of adjacent.
                    "billing_after_deactivation__peerpct",
                    "billing_after_death__peerpct",
                    "subscore_invalid_identity",
                    # O&R eligibility is CURRENT-STATE and exclusion strips it,
                    # so "referrer ineligible today" partially encodes the label
                    # (run-4 in-time AUC 0.96 — too good to be honest) and leaks
                    # post-cutoff bans into frozen matrices. Real lead signal,
                    # but out-of-time-only; the dme_ring subscore inherits it.
                    "dme_ineligible_referrer",
                    "dme_ineligible_referred_dollars",
                    "dme_ineligible_referred_dollars__peerpct",
                    "subscore_dme_ring"]

# Provider stats worth carrying as plain features (whatever the base leads has).
# org_member_count lets the model discount a broadcast org signal in a giant
# health system vs. a 2-NPI shell.
PROVIDER_STATS = ["gross_paid", "net_paid", "service_volume", "total_claim_lines",
                  "n_distinct_hcpcs", "tenure_months", "n_active_months",
                  "org_member_count"]

IDENTIFIER_COLS = ["npi", "org_node_id", "entity_type", "primary_taxonomy",
                   "practice_state", "org_legal_name"]


def _broadcast_org_to_npi(npi_to_org: pd.DataFrame, org_frame: pd.DataFrame,
                          value_cols: list[str]) -> pd.DataFrame:
    """Map org-grain values down to every member NPI (each NPI inherits its org's
    value). Returns an npi-keyed frame; never fans out (one org_node_id → many NPIs,
    each appears once)."""
    cols = [c for c in value_cols if c in org_frame.columns]
    if not cols or "org_node_id" not in org_frame.columns:
        return pd.DataFrame(columns=["npi"])
    xw = npi_to_org[["npi", "org_node_id"]].astype(str).drop_duplicates("npi")
    out = xw.merge(org_frame[["org_node_id"] + cols].astype({"org_node_id": str}),
                   on="org_node_id", how="left")
    return out.drop(columns=["org_node_id"])


def _one_sided_peer_pct(df: pd.DataFrame, cols: list[str],
                        min_peer: int = 30) -> pd.DataFrame:
    """One-sided taxonomy-peer percentile for each column in ``cols`` (the platform
    convention: high = more than peers = the only suspicious direction). Reuses the
    peer ladder in analytics.peers. Returns columns named exactly ``cols`` (NaN where
    a provider has no adequate peer group — never force-ranked)."""
    from src.analytics.peers import (assign_peer_groups, one_sided_percentiles,
                                      DEFAULT_LADDER)
    present = [c for c in cols if c in df.columns]
    if not present:
        return pd.DataFrame(index=df.index)
    work = df.copy()
    # peers.assign_peer_groups expects taxonomy_code / entity_type / state
    work["taxonomy_code"] = work.get("primary_taxonomy", pd.Series("", index=work.index))
    work["state"] = work.get("practice_state", pd.Series("", index=work.index))
    if "entity_type" not in work.columns:
        work["entity_type"] = ""
    # NUCC fix: when a raw-taxonomy cell is too thin, fall back to the clinically
    # coherent classification cohort (peer_group_key) instead of going national.
    ladder = DEFAULT_LADDER
    if "peer_group_key" in work.columns and work["peer_group_key"].astype(str).str.len().gt(0).any():
        ladder = DEFAULT_LADDER + (("peer_group_key",),)
    assigned = assign_peer_groups(work, ladder=ladder, min_peer=min_peer)
    pct = one_sided_percentiles(assigned, present)
    return pct[[c for c in present if c in pct.columns]]


def build_provider_matrix(leads: pd.DataFrame, npi_to_org: pd.DataFrame,
                          org_graph_features: pd.DataFrame | None = None,
                          adapter_npi_frames: dict[str, pd.DataFrame] | None = None,
                          org_grain_frames: dict[str, pd.DataFrame] | None = None,
                          nucc_peer_groups: pd.DataFrame | None = None,
                          widened_label: pd.DataFrame | None = None,
                          case_labels: pd.DataFrame | None = None,
                          min_peer: int = 30,
                          graph_substrate: dict | None = None,
                          ) -> tuple[pd.DataFrame, dict]:
    """Assemble the wide per-NPI training matrix and its manifest.

    ``leads``               per-NPI v3 leads (concepts + identifiers + label).
    ``npi_to_org``          crosswalk (npi, org_node_id) — the broadcast key.
    ``org_graph_features``  per-org entity-graph features (broadcast to NPI).
    ``adapter_npi_frames``  {source_name: npi-keyed feature frame} (Part B/D, …).
    ``org_grain_frames``    {source_name: org_node_id-keyed feature frame} — the
                            adapters that resolve at org grain (NADAC spread,
                            post-deactivation billing, facility rollup, 340B,
                            saturation, ineligible-referral); broadcast to NPI.

    Returns ``(matrix, manifest)``. The matrix has one row per NPI; the manifest
    records label / leakage / feature / coverage groupings for Travis.
    """
    m = leads.copy()
    m["npi"] = m["npi"].astype(str)
    assert m["npi"].is_unique, "base leads must be one row per NPI"
    n0 = len(m)
    xw = npi_to_org[["npi", "org_node_id"]].copy()
    xw["npi"] = xw["npi"].astype(str)
    xw["org_node_id"] = xw["org_node_id"].astype(str)
    m = m.merge(xw.drop_duplicates("npi"), on="npi", how="left")
    assert len(m) == n0, "npi_to_org join fanned out"

    # #4: org size — every member NPI carries its org's member count, so the model
    # can discount a broadcast ownership signal in a 5,000-NPI system vs. a 2-NPI shell.
    org_sizes = xw.drop_duplicates("npi").groupby("org_node_id")["npi"].size()
    m["org_member_count"] = m["org_node_id"].map(org_sizes).fillna(1).astype(int)
    # #4: NPI-grain owner-role signal (sharper than the smeared graph proximity)
    if "excluded_owner_role" in m.columns:
        m["has_excluded_owner"] = (m["excluded_owner_role"].fillna("").astype(str)
                                   .str.len().gt(0).astype(int))

    sources_used: dict[str, list[str]] = {}

    if widened_label is not None and len(widened_label) and "npi" in widened_label.columns:
        wl = widened_label.copy()
        wl["npi"] = wl["npi"].astype(str)
        cols = [c for c in ["npi", "provider_on_exclusion", "exclusion_label_sources"]
                if c in wl.columns]
        m = m.merge(wl[cols].drop_duplicates("npi"), on="npi", how="left")
        assert len(m) == n0, "widened-label join fanned out"
        if "provider_on_exclusion" in m.columns:
            m["provider_on_exclusion"] = m["provider_on_exclusion"].fillna(0).astype(int)

    # DOJ/qui tam outcomes: the strongest positives (prosecuted fraud), folded into
    # the same label union, plus scheme-type + conduct-window metadata that enable
    # scheme-stratified and OUT-OF-TIME training (train on features < conduct_start).
    if case_labels is not None and len(case_labels) and "npi" in case_labels.columns:
        cl = case_labels.copy()
        cl["npi"] = cl["npi"].astype(str)
        meta = [c for c in ["npi", "fraud_scheme", "conduct_start", "conduct_end",
                            "case_ids"] if c in cl.columns]
        m = m.merge(cl[meta].drop_duplicates("npi"), on="npi", how="left")
        assert len(m) == n0, "case-label join fanned out"
        hit = m["npi"].isin(set(cl["npi"]))
        if "provider_on_exclusion" not in m.columns:
            m["provider_on_exclusion"] = 0
        m["provider_on_exclusion"] = (m["provider_on_exclusion"].fillna(0).astype(int)
                                      | hit.astype(int))
        if "exclusion_label_sources" not in m.columns:
            m["exclusion_label_sources"] = ""
        m.loc[hit, "exclusion_label_sources"] = (
            m.loc[hit, "exclusion_label_sources"].fillna("").astype(str)
             .str.split(";").apply(lambda xs: ";".join(sorted(set([x for x in xs if x] + ["doj_case"])))))
        sources_used["doj_case"] = ["fraud_scheme", "conduct_start", "conduct_end"]

    if org_graph_features is not None and len(org_graph_features):
        gf = _broadcast_org_to_npi(npi_to_org, org_graph_features, GRAPH_FEATURES)
        if "npi" in gf.columns and len(gf.columns) > 1:
            m = m.merge(gf.drop_duplicates("npi"), on="npi", how="left")
            assert len(m) == n0, "graph-feature broadcast fanned out"
            sources_used["entity_graph"] = [c for c in gf.columns if c != "npi"]

    for name, fr in (adapter_npi_frames or {}).items():
        if fr is None or not len(fr) or "npi" not in fr.columns:
            continue
        f = fr.copy()
        f["npi"] = f["npi"].astype(str)
        new_cols = [c for c in f.columns if c != "npi" and c not in m.columns]
        if not new_cols:
            continue
        m = m.merge(f[["npi"] + new_cols].drop_duplicates("npi"), on="npi", how="left")
        assert len(m) == n0, f"adapter '{name}' join fanned out"
        sources_used[name] = new_cols

    for name, fr in (org_grain_frames or {}).items():
        if fr is None or not len(fr) or "org_node_id" not in fr.columns:
            continue
        val_cols = [c for c in fr.columns if c != "org_node_id" and c not in m.columns]
        if not val_cols:
            continue
        b = _broadcast_org_to_npi(npi_to_org, fr, val_cols)
        if "npi" in b.columns and len(b.columns) > 1:
            m = m.merge(b.drop_duplicates("npi"), on="npi", how="left")
            assert len(m) == n0, f"org-grain source '{name}' broadcast fanned out"
            sources_used[name] = [c for c in b.columns if c != "npi"]

    # --- NUCC canonical peer groups (coherent cohort key for the percentile ladder) ---
    if nucc_peer_groups is not None and len(nucc_peer_groups) and "npi" in nucc_peer_groups.columns:
        ng = nucc_peer_groups.copy()
        ng["npi"] = ng["npi"].astype(str)
        keep = [c for c in ["npi", "peer_group_key", "nucc_grouping", "nucc_classification"]
                if c in ng.columns]
        m = m.merge(ng[keep].drop_duplicates("npi"), on="npi", how="left")
        assert len(m) == n0, "nucc peer-group join fanned out"
        sources_used["nucc_taxonomy"] = [c for c in keep if c != "npi"]

    # --- peer-normalize the raw adapter metrics (one-sided taxonomy percentile) ---
    adapter_present = [c for c in ADAPTER_FEATURE_COLS if c in m.columns]
    peerpct = _one_sided_peer_pct(m, adapter_present, min_peer=min_peer)
    adapter_present = [c for c in adapter_present if c in peerpct.columns]
    peerpct = peerpct.rename(columns={c: f"{c}__peerpct" for c in peerpct.columns})

    # --- subscore inputs: concepts + graph pass through; adapters use peer pct ---
    subin = pd.DataFrame(index=m.index)
    for c in V3_CONCEPTS + GRAPH_FEATURES + ANALYTICS_FEATURES:
        if c in m.columns:
            subin[c] = pd.to_numeric(m[c], errors="coerce")
    for c in adapter_present:
        subin[c] = peerpct[f"{c}__peerpct"]      # peer-relative value drives the subscore
    subscores, coverage = compute_subscores(subin)

    # --- assemble the export frame ---
    pieces = [m, peerpct, subscores]
    out = pd.concat(pieces, axis=1)
    out = out.loc[:, ~out.columns.duplicated()]
    assert len(out) == n0, "matrix row count changed during assembly"

    # §E: widen the label with the near-certain smoking guns as POSITIVES —
    # billing after NPI deactivation / after death is definitional fraud conduct
    # (more positives = a stronger PU label). Provenance-tagged like every other
    # source; the source flags themselves are in LEAKAGE_HARD so they can never
    # double as features once they feed the label.
    if "provider_on_exclusion" in out.columns:
        if "exclusion_label_sources" not in out.columns:
            out["exclusion_label_sources"] = ""
        for flag, tag in (("billing_after_deactivation", "deactivation"),
                          ("billing_after_death", "death")):
            if flag not in out.columns:
                continue
            pos = pd.to_numeric(out[flag], errors="coerce").fillna(0) > 0
            if not pos.any():
                continue
            out.loc[pos, "provider_on_exclusion"] = 1
            src = out.loc[pos, "exclusion_label_sources"].fillna("").astype(str)
            out.loc[pos, "exclusion_label_sources"] = (
                src.where(src == "", src + ";") + tag)

    # Pillar 4: the expected-billing "digital twin" residual — unexplained billing
    # after conditioning on specialty/size/breadth (doesn't punish the legitimately
    # large the way a raw peer percentile does). A clean, size-adjusted feature.
    from .expected_billing import expected_billing_residual, volume_residual
    eb = expected_billing_residual(out)
    out["billing_residual"] = eb["billing_residual"].to_numpy()
    out["expected_net_paid"] = eb["expected_net_paid"].to_numpy()
    # the volume twin: claims vs EXOGENOUS capacity (tenure/active-months/org
    # size) — catches the volume inflation the price twin conditions away
    vr = volume_residual(out)
    out["volume_residual"] = vr["volume_residual"].to_numpy()
    out["expected_service_volume"] = vr["expected_service_volume"].to_numpy()

    # Pillar 4: cross-source consistency — incoherence between the NPPES/PECOS
    # registration record and the billing behavior (hard to fake on both sides).
    from .consistency import consistency_checks
    cons = consistency_checks(out)
    for c in ["incons_solo_scale", "incons_instant_scale", "incons_breadth",
              "incons_lone_org_scale", "consistency_flags"]:
        out[c] = cons[c].to_numpy()

    # Manufacture high-confidence NEGATIVES (known non-offenders) so a model can
    # contrast fraud actors against a real clean cohort, not just the unlabeled
    # mass. Runs AFTER the residual/consistency blocks on purpose: as-of (frozen)
    # matrices drop the v3 concept columns, and the clean-anchor benign check then
    # falls back to billing_residual / volume_residual / consistency_flags — which
    # must already be on `out` (and BEFORE weak_supervision, whose
    # lf_confirmed_clean labeling function reads confirmed_clean).
    from .clean_anchors import manufacture_negatives
    neg = manufacture_negatives(out)
    out["confirmed_clean"] = neg["confirmed_clean"].to_numpy()
    out["clean_basis"] = neg["clean_basis"].to_numpy()

    # Pillar 2: weak-supervision label model — many noisy labeling functions fused
    # via accuracies learned from the anchors into a soft probabilistic label for
    # EVERY provider (expands the sparse hard labels). A TARGET, not a feature.
    from .weak_supervision import weak_supervision
    ws, ws_audit = weak_supervision(out)
    for c in ["weak_label_score", "weak_label", "weak_label_votes"]:
        out[c] = ws[c].to_numpy()

    # Group id + assessability mask (don't train on these — use them).
    #  * group_id = org_node_id makes group-aware CV trivial (don't let an org's
    #    NPIs straddle the train/test split), the modeling-side fix for the
    #    org->NPI broadcast residual.
    #  * assessable = the provider has enough evidence to be SCORED at all: a real
    #    peer group (some metric got a non-null peer percentile) AND non-trivial
    #    billing. Thin-evidence providers are flagged so they aren't force-ranked
    #    into a percentile they didn't earn (precision + defamation-safety).
    out["group_id"] = (out["org_node_id"].astype(str) if "org_node_id" in out.columns
                       else pd.Series("", index=out.index))

    def _num(col: str) -> pd.Series:
        s = out[col] if col in out.columns else pd.Series(0.0, index=out.index)
        return pd.to_numeric(s, errors="coerce").fillna(0.0)

    _peer_now = [c for c in out.columns if c.endswith("__peerpct")]
    has_peer = (out[_peer_now].notna().any(axis=1) if _peer_now
                else pd.Series(False, index=out.index))
    _billing = _num("net_paid") if "net_paid" in out.columns else _num("gross_paid")
    out["assessable"] = (has_peer & ((_billing > 0) | (_num("service_volume") > 0))).astype(int)

    raw_feature_cols = sorted(
        [c for c in (V3_CONCEPTS + GRAPH_FEATURES + ANALYTICS_FEATURES
                     + adapter_present + PROVIDER_STATS)
         if c in out.columns and c not in LEAKAGE_HARD])
    subscore_cols = [c for c in out.columns if c.startswith("subscore_")]
    peerpct_cols = [c for c in out.columns if c.endswith("__peerpct")]

    # Graph node embeddings (Pillar 3): structural motifs are clean features; the
    # learned embeddings and the fraud-proximity field encode the exclusion
    # neighborhood. The node EMBEDDINGS are now computed on the exclusion-free graph
    # (ownership/co-location structure only), so they are CLEAN; only the
    # fraud-proximity field (and its delta) remains leakage-adjacent.
    from src.entity_graph.graph_embeddings import EMB_PREFIX, STRUCT_COLS, PROXIMITY_COL
    embedding_cols = [c for c in out.columns if c.startswith(EMB_PREFIX)]
    struct_present = [c for c in STRUCT_COLS if c in out.columns]
    graph_adjacent = ([PROXIMITY_COL] if PROXIMITY_COL in out.columns else [])
    pillar4 = [c for c in ["billing_residual", "expected_net_paid",
                           "volume_residual", "expected_service_volume",
                           "incons_solo_scale", "incons_instant_scale",
                           "incons_breadth", "incons_lone_org_scale",
                           "consistency_flags"] if c in out.columns]
    # external grounding + temporal-graph velocity + billing-LM + the exclusion-free
    # graph embeddings (all clean); the fraud-proximity DELTA stays leakage-adjacent.
    from .billing_lm import EMB_PREFIX as _BILL_EMB
    billing_emb_cols = [c for c in out.columns if c.startswith(_BILL_EMB)]
    extra_clean = [c for c in ["addr_is_mailbox", "addr_provider_count", "addr_shared",
                               "addr_is_cmra", "addr_distinct_orgs", "addr_cluster_degree",
                               "addr_geocoded", "addr_no_match",
                               "graph_emb_drift", "graph_degree_delta", "graph_kcore_delta",
                               "billing_surprisal", "sequence_surprisal",
                               "billing_taxonomy_mismatch", "billing_taxonomy_fit",
                               "billing_taxonomy_margin"]
                   if c in out.columns] + billing_emb_cols + embedding_cols
    if "graph_fraud_proximity_delta" in out.columns:
        graph_adjacent = graph_adjacent + ["graph_fraud_proximity_delta"]
    raw_feature_cols = sorted(set(raw_feature_cols) | set(struct_present)
                              | set(pillar4) | set(extra_clean))

    # Prefer the widened multi-source label when present (LEIE + revocations + SAM +
    # OpenSanctions), keeping provider_on_leie available for back-compat.
    label = "provider_on_exclusion" if "provider_on_exclusion" in out.columns else \
            (LABEL_COL if LABEL_COL in out.columns else None)
    leakage_hard = [c for c in (LEAKAGE_HARD + ["provider_on_exclusion"]) if c in out.columns]
    # label metadata (target-derived, never features): scheme type + conduct window
    # for scheme-stratified and out-of-time validation.
    label_metadata = [c for c in ["exclusion_label_sources", "fraud_scheme",
                                  "conduct_start", "conduct_end", "case_ids",
                                  "provider_on_leie", "confirmed_clean", "clean_basis",
                                  "weak_label_score", "weak_label", "weak_label_votes",
                                  "billing_implied_taxonomy"]
                      if c in out.columns]
    evidence_cols = [c for c in out.columns if c.startswith("evidence_n_")]
    yoy_cols = [c for c in out.columns
                if c.endswith("_yoy") or c.endswith("_slope")]
    raw_feature_cols = sorted(set(raw_feature_cols) | set(yoy_cols))
    raw_feature_cols = [c for c in raw_feature_cols
                        if c not in leakage_hard and c not in label_metadata
                        and c not in evidence_cols]

    # --- temporal vintage of every trainable feature (the cutoff question) ---
    # point_in_time: dated rows inside the data — truly frozen under --asof-cutoff.
    # annual_capped: annual PUFs — frozen by the filename-year vintage cap.
    # reference:     timeless lookups (taxonomy crosswalks, geography).
    # current_state: single-snapshot structure (NPPES, PECOS ownership, facility,
    #                addresses, eligibility) — NO historical edition exists;
    #                today's file proxies the cutoff-date world. A strict frozen
    #                model can drop this class to bound its effect; the network
    #                A/B delta is immune because both arms share these inputs.
    _VINTAGE_RANK = ["point_in_time", "annual_capped", "reference", "current_state"]
    _SRC_VINTAGE = {
        "label": "point_in_time", "smoking_gun_timeline": "point_in_time",
        "nppes_deactivation": "point_in_time", "sector_schemes": "point_in_time",
        "drug_markup": "point_in_time",
        "growth": "point_in_time", "plausibility": "point_in_time",
        "billing_lm": "point_in_time", "death_master": "point_in_time",
        "partb": "annual_capped", "partd": "annual_capped",
        "dmepos": "annual_capped", "opioid": "annual_capped",
        "open_payments": "annual_capped", "kickback": "annual_capped",
        "partb_trend": "annual_capped", "partd_trend": "annual_capped",
        "dmepos_trend": "annual_capped", "opioid_trend": "annual_capped",
        "nucc_taxonomy": "reference",
    }  # every unlisted source (entity_graph, address, facility, pos, hcris,
    #    saturation, ownership, graph_embeddings, order_referring_referrer, …)
    #    is current_state — the safe default direction.
    col_vintage: dict[str, str] = {}
    # Travis's leak, closed at the source of truth: when the graph itself was
    # built from a frozen NPPES edition (graph_build_info.json says so), its
    # address-derived features ARE point-in-time — reclassify from facts, not
    # from the old "no historical editions exist" assumption (which was wrong;
    # NBER archives monthly NPPES editions).
    _src_vintage = dict(_SRC_VINTAGE)
    if graph_substrate and graph_substrate.get("address_layer_frozen"):
        _src_vintage["entity_graph"] = "point_in_time"
        _src_vintage["graph_embeddings"] = "point_in_time"
    for src, src_cols in sources_used.items():
        v = _src_vintage.get(src, "current_state")
        for c in src_cols:
            col_vintage[c] = v
    for c in PROVIDER_STATS + pillar4 + V3_CONCEPTS + ANALYTICS_FEATURES:
        if c in out.columns:
            # billing-fact derived: recomputed on the as-of spending file (or
            # dropped as asof-uncomputable) — point-in-time under a freeze
            col_vintage[c] = "point_in_time"
    for c in peerpct_cols:
        col_vintage[c] = col_vintage.get(c[: -len("__peerpct")], "current_state")
    for scheme, scheme_cols in coverage.items():
        worst = max((_VINTAGE_RANK.index(col_vintage.get(c, "current_state"))
                     for c in scheme_cols), default=0)
        col_vintage[f"subscore_{scheme}"] = _VINTAGE_RANK[worst]
    trainable = set(raw_feature_cols) | set(peerpct_cols) | set(subscore_cols)
    feature_vintage = {v: sorted(c for c in trainable
                                 if col_vintage.get(c, "current_state") == v)
                       for v in _VINTAGE_RANK}
    scheme_vintage = {s: col_vintage[f"subscore_{s}"] for s in coverage}
    # Every column gets a class in the contract, not just the trainable ones.
    # Travis's GATE-0: the parquet carried 52 columns the manifest classified
    # nowhere (identifiers, evidence_n_* counts, raw aggregates, leftover
    # pipeline columns). They are quarantined-by-construction, but a contract
    # should say so out loud. "ignore" = present in the file, never a feature.
    leakage_adjacent_cols = ([c for c in LEAKAGE_ADJACENT if c in out.columns]
                             + graph_adjacent)
    classified = (set(trainable) | set(leakage_hard) | set(label_metadata)
                  | set(leakage_adjacent_cols) | ({label} if label else set())
                  | set(IDENTIFIER_COLS) | set(evidence_cols))
    column_class = {"ignore": sorted(c for c in out.columns
                                     if c not in classified)}

    manifest = {
        "grain": "npi",
        "n_providers": int(n0),
        "n_positives": (int(pd.to_numeric(out[label], errors="coerce").fillna(0).sum())
                        if label else 0),
        "label": label,
        "label_provenance": "exclusion_label_sources" if "exclusion_label_sources" in out.columns else None,
        "label_metadata": label_metadata,
        "group_cols": [c for c in ["group_id"] if c in out.columns],
        "assessability": [c for c in ["assessable"] if c in out.columns],
        "weak_supervision": ws_audit,
        "leakage_hard": leakage_hard,
        "leakage_adjacent": [c for c in LEAKAGE_ADJACENT if c in out.columns] + graph_adjacent,
        "identifier_cols": [c for c in IDENTIFIER_COLS if c in out.columns],
        "raw_feature_cols": raw_feature_cols,
        "peerpct_cols": peerpct_cols,
        "subscore_cols": subscore_cols,
        "embedding_cols": embedding_cols,
        "scheme_coverage": coverage,
        "sources_used": sources_used,
        "feature_vintage": feature_vintage,
        "scheme_vintage": scheme_vintage,
        "column_class": column_class,
        "n_trainable": len(trainable),
        "n_trainable_after_fence": len(trainable - set(leakage_adjacent_cols)),
        "graph_substrate": graph_substrate or {"address_layer_frozen": False,
                                               "asof": None,
                                               "asof_nppes_edition": None},
    }
    return out, manifest


# --------------------------------------------------------------------------- #
# CLI orchestration: discover source files under preclean/ and run each adapter
# --------------------------------------------------------------------------- #

def _file_year(p: Path) -> int:
    """Data year parsed from a filename (partb_2024.csv → 2024; -1 if none)."""
    import re
    yrs = re.findall(r"(20\d{2})", p.stem)
    return max((int(y) for y in yrs), default=-1)


def _year_files(folder: Path, max_year: int | None = None) -> list[Path]:
    """All candidate files in a multi-year source folder, NEWEST year first
    (partb_2024.csv before partb_2016.csv). Year parsed from the filename;
    ties broken by name.

    ``max_year`` (the frozen-run vintage cap): a file whose filename year is
    AFTER the freeze cutoff is excluded — an annual PUF for calendar 2024
    contains post-cutoff behavior and would leak it into a 2023-12 frozen
    matrix. Files with no year in the name pass the cap (their vintage is
    unknowable from the name; the SOURCES_REPORT names the file used so the
    operator can judge)."""
    if folder is None or not folder.is_dir():
        return []
    files = sorted(list(folder.glob("*.csv")) + list(folder.glob("*.xlsx"))
                   + list(folder.glob("*.parquet")))
    if max_year is not None:
        files = [p for p in files if _file_year(p) <= max_year]
    return sorted(files, key=lambda p: (_file_year(p), p.name), reverse=True)


def _latest_year_file(folder: Path) -> Path | None:
    """The newest-year file in a multi-year source folder."""
    files = _year_files(folder)
    return files[0] if files else None


def _read_one(p: Path, cols_dict: dict | None = None) -> pd.DataFrame | None:
    """Load one source file reading ONLY the columns the adapter resolves
    (memory-safe for multi-GB CMS PUFs on a 16 GB box). Falls back to all
    columns if none resolve (so the adapter raises its own clear error)."""
    if p is None:
        return None
    if p.suffix == ".parquet":
        return pd.read_parquet(p)
    from src.attempt_2.clean_data import read_csv_text, _resolve_columns
    if p.suffix.lower() in (".xlsx", ".xls"):
        return _read_any(p)
    if not cols_dict:
        return read_csv_text(p)
    header = list(read_csv_text(p, nrows=0).columns)
    resolved = _resolve_columns(header, cols_dict)
    use = list(dict.fromkeys(resolved.values()))
    return read_csv_text(p, usecols=use) if use else read_csv_text(p)


def _read_latest(folder: Path, cols_dict: dict | None = None,
                 max_year: int | None = None) -> pd.DataFrame | None:
    """Load the latest-year file in ``folder`` (see ``_read_one``), honoring the
    frozen-run vintage cap."""
    files = _year_files(folder, max_year)
    return _read_one(files[0], cols_dict) if files else None


def _read_any(path: Path) -> pd.DataFrame | None:
    """Read csv/parquet as all-string (IDs keep leading zeros — hard rule #1)."""
    if not path or not path.exists():
        return None
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    from src.attempt_2.clean_data import read_csv_text
    return read_csv_text(path)


# Leads columns derived from the FULL billing history that cannot be recomputed
# point-in-time here (the v3 pipeline built them over all months). In an as-of
# run they would leak post-cutoff billing into the frozen matrix — a 2024-banned
# provider's billing cessation is visible in `temporal` — so they are DROPPED
# (the NULL-aware subscore engine tolerates their absence; absent beats leaked).
_ASOF_UNCOMPUTABLE = ["concentration", "payment_intensity", "service_intensity",
                      "specialty_mismatch", "temporal", "anomaly_score",
                      "anomaly_pct", "n_concept_signals", "signals_tripped",
                      "log_max_single_month", "provider_hhi", "growth_slope"]


def _apply_asof_freeze(leads: pd.DataFrame, asof_spend_p: Path, cutoff: str,
                       log) -> pd.DataFrame:
    """Make the BASE leads frame point-in-time correct for a feature-freeze run.

    The as-of filter on the spending fact only freezes the enrichment adapters;
    the base frame's PROVIDER_STATS (paid totals, volume, breadth, tenure) and
    the v3 concept percentiles come from the full-history leads file. This
    (1) recomputes the stats strictly pre-cutoff via ``asof_provider_stats`` and
    (2) drops the uncomputable full-history columns. Without this the "frozen"
    matrix carried post-cutoff billing in its strongest features and the forward
    network A/B measured leakage.
    """
    from .asof_billing import asof_provider_stats
    out = leads.copy()
    out["npi"] = out["npi"].astype(str)
    pit = asof_provider_stats(str(asof_spend_p), cutoff)
    pit["npi"] = pit["npi"].astype(str)
    stat_cols = [c for c in PROVIDER_STATS if c != "org_member_count"]
    replaced = [c for c in stat_cols if c in out.columns]
    out = out.drop(columns=replaced, errors="ignore")
    keep = ["npi"] + [c for c in pit.columns
                      if c in stat_cols or c in ("first_service_month",
                                                 "last_service_month")]
    out = out.merge(pit[[c for c in keep if c in pit.columns]], on="npi", how="left")
    dropped = [c for c in _ASOF_UNCOMPUTABLE if c in out.columns]
    out = out.drop(columns=dropped, errors="ignore")
    log(f"  [asof] base features frozen: PROVIDER_STATS recomputed pre-cutoff for "
        f"{len(pit):,} NPIs (replaced {replaced}); dropped full-history columns "
        f"{dropped} (cannot be recomputed point-in-time — absent beats leaked)")
    return out


def _load_nucc_peer_groups(preclean: Path, processed: Path, log):
    """Build the canonical NUCC peer-group table if the taxonomy file is present."""
    tax_p = (preclean / "nucc" / "nucc_taxonomy.csv")
    pdim_p = _first_existing(processed, "provider_dim.parquet")
    if not tax_p.exists() or not pdim_p:
        log("    [nucc] skipped: needs preclean/nucc/nucc_taxonomy.csv + provider_dim "
            "(percentiles fall back to raw NPPES taxonomy)")
        return None
    try:
        from src.ingest_cms import nucc_taxonomy as nt
        hier = nt.load_taxonomy_hierarchy(_read_any(tax_p))
        xw_p = preclean / "nucc" / "specialty_crosswalk.csv"
        xw = nt.load_specialty_crosswalk(_read_any(xw_p)) if xw_p.exists() else None
        pg = nt.canonical_peer_group(pd.read_parquet(pdim_p), hier, xw)
        log(f"    [nucc] {pg['peer_group_key'].nunique():,} canonical peer groups "
            f"for {len(pg):,} NPIs (coherent cohort fallback enabled)")
        return pg
    except Exception as e:
        log(f"    [nucc] skipped: {e}")
        return None


_LEIE_STATUTE_PREFIXES = ("1128",)


def _label_source(excl_type: str) -> str:
    t = str(excl_type or "").lower()
    if t.startswith("opensanctions"):
        return "opensanctions"
    if t.startswith("medicare_revocation"):
        return "medicare_revocation"
    if t.startswith("preclusion"):
        return "preclusion"
    if t.startswith("sam"):
        return "sam"
    return "leie"


def _widened_label_from_graph(graph_dir: Path, log):
    """Per-NPI multi-source exclusion label from the graph's exclusion nodes
    (LEIE + merged revocations/SAM/OpenSanctions). Only NPI-matched exclusions
    count — name-only rows can't be safely attributed to a provider here.
    Returns npi, provider_on_exclusion, exclusion_label_sources."""
    ep = graph_dir / "nodes" / "exclusion_nodes.parquet"
    if not ep.exists():
        return None
    ex = pd.read_parquet(ep)
    if "npi" not in ex.columns:
        return None
    ex = ex.copy()
    ex["npi"] = ex["npi"].astype(str)
    ex = ex[ex["npi"].str.len() >= 10]                  # NPI-matched only
    if not len(ex):
        return None
    ex["src"] = ex.get("excl_type", "").map(_label_source)
    g = ex.groupby("npi")["src"].agg(lambda s: ";".join(sorted(set(s))))
    out = pd.DataFrame({"npi": g.index, "provider_on_exclusion": 1,
                        "exclusion_label_sources": g.values})
    srcs = sorted({s for v in g.values for s in v.split(";")})
    log(f"    [label] widened positives from {len(out):,} NPI-matched exclusions "
        f"across: {', '.join(srcs)}")
    return out.reset_index(drop=True)


def _billing_lm_from_parquet(spending_path: Path, provider_dim: pd.DataFrame, log):
    """Billing language model from the spending parquet, full-DuckDB: the code
    co-occurrence, provider embeddings, and per-taxonomy surprisal are all computed
    by DuckDB self-joins/GROUP BYs over the parquet (the provider x code matrix never
    materializes in pandas), so this scales to the full universe. Only the small
    (npi, hcpcs, first_month) adoption frame is pulled into pandas for the order-aware
    sequence surprisal."""
    import duckdb
    from .billing_lm import (build_code_embeddings_duckdb, provider_embeddings_duckdb,
                             billing_surprisal_duckdb, EMB_PREFIX as EMB_PREFIX_BILL)
    from .billing_sequence_lm import sequence_surprisal
    con = duckdb.connect()
    p = str(spending_path).replace("'", "''")
    codes, cvecs = build_code_embeddings_duckdb(p, con=con)
    if not len(codes):
        con.close()
        return pd.DataFrame(columns=["npi"])
    tax = provider_dim[["npi", "taxonomy_code"]]
    out = provider_embeddings_duckdb(p, codes, cvecs, con=con)
    out = out.merge(billing_surprisal_duckdb(p, provider_dim, con=con),
                    on="npi", how="outer")
    seq_in = con.execute(f"""
        SELECT CAST(billing_npi AS VARCHAR) AS npi,
               UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) AS hcpcs,
               MIN(substr(CAST(service_month AS VARCHAR), 1, 7)) AS service_month
        FROM read_parquet('{p}') WHERE hcpcs_code IS NOT NULL
        GROUP BY 1, 2""").df()
    con.close()
    out = out.merge(sequence_surprisal(seq_in, taxonomy=tax), on="npi", how="outer")
    # Billing-implied specialty: which taxonomy the provider's billing resembles,
    # vs. the claimed one — catches self-reported-taxonomy gaming. Reuses the
    # billing_emb_* just computed (no extra heavy pass).
    from .billing_specialty import implied_specialty
    emb_only = out[["npi"] + [c for c in out.columns if c.startswith(EMB_PREFIX_BILL)]]
    spec = implied_specialty(emb_only, provider_dim)
    if len(spec):
        out = out.merge(spec, on="npi", how="left")
    log(f"    [billing_lm] {len(out):,} providers, {len(codes):,} codes embedded "
        f"(+ billing_surprisal + sequence_surprisal + billing_implied_taxonomy)")
    return out


def _read_spending_cols(path: Path, cols: list[str]) -> pd.DataFrame:
    """Read only the spending columns that exist (some files lack hcpcs_code)."""
    try:
        return pd.read_parquet(path, columns=cols)
    except Exception:
        df = pd.read_parquet(path)
        return df[[c for c in cols if c in df.columns]]


def _spending_for_npis(spending_path: Path, npis) -> pd.DataFrame:
    """Stream only the spending rows for a small set of NPIs (DuckDB) — used by the
    deceased / deactivated-NPI checks so they never pull the 238M-row fact into
    pandas. Returns billing_npi, service_month, total_paid."""
    cols = ["billing_npi", "service_month", "total_paid"]
    ids = sorted({str(n) for n in npis if str(n) and str(n).lower() != "nan"})
    if not ids:
        return pd.DataFrame(columns=cols)
    import duckdb
    con = duckdb.connect()
    # register-join, never a literal IN(...): the cumulative deactivation report
    # runs to hundreds of thousands of NPIs, and a multi-MB SQL string can stall
    # or fail the parser. Path quotes are ESCAPED (''), not stripped.
    con.register("_npi_filter", pd.DataFrame({"npi": ids}))
    df = con.execute(
        f"SELECT CAST(s.billing_npi AS VARCHAR) billing_npi, "
        f"CAST(s.service_month AS VARCHAR) service_month, "
        f"CAST(s.total_paid AS DOUBLE) total_paid "
        f"FROM read_parquet('{str(spending_path).replace(chr(39), chr(39) * 2)}') s "
        f"JOIN _npi_filter f ON CAST(s.billing_npi AS VARCHAR) = f.npi").df()
    con.close()
    return df


def _warn_if_graph_stale(graph_dir: Path, leads_path: Path, log) -> None:
    """The ownership signal is computed per-org in the graph and inherited by NPI;
    a graph older than the leads it will be joined to is stale. Warn loudly (this is
    a sequencing condition, not corruption — the Makefile target rebuilds in order)."""
    try:
        gp = graph_dir / "npi_to_org.parquet"
        if gp.exists() and Path(leads_path).exists() and \
                gp.stat().st_mtime < Path(leads_path).stat().st_mtime:
            log("    [WARN] entity graph is OLDER than the leads file — the "
                "ownership_integrity signal may be stale. Rebuild the graph first "
                "(`make provider-features` enforces the order).")
    except Exception:
        pass


def _first_existing(base: Path, *names: str) -> Path | None:
    for n in names:
        # a glob pattern (e.g. "*.csv") globs the base folder itself — so a
        # year-suffixed real file (partb_2016.csv, dmepos__referring_2022.csv) is
        # found even when it isn't named the canonical partb.csv. (Callers pass the
        # exact name first, then a "*.ext" fallback; glob only fires for patterns,
        # so root-level exact lookups never match a sibling by accident.)
        if any(ch in n for ch in "*?["):
            hits = sorted(base.glob(n)) if base.is_dir() else []
            if hits:
                return hits[0]
            continue
        p = base / n
        if p.exists():
            return p
    # also accept a sibling directory named after the stem holding a single file
    for n in names:
        d = base / Path(n).stem
        if d.is_dir():
            hits = sorted(list(d.glob("*.parquet")) + list(d.glob("*.csv")))
            if hits:
                return hits[0]
    return None


class _SourceAudit:
    """Wraps the export's ``print``/``log`` so every ``    [name] …`` progress line is
    ALSO recorded as a structured (source, status, detail) row — without touching the
    dozens of call sites that emit them. One run then yields a single SOURCES_REPORT
    table: which sources lit up, which were skipped and why, instead of grepping the
    console (the silent year-suffixed Part-B/D skip class of bug is now visible at a
    glance). The convention every adapter already follows:

        ``    [name] <summary>``            → used   (row counts / columns / file)
        ``    [name] skipped: <reason>``     → skipped (the precise data-block reason)

    ``[assert …]`` and ``[WARN …]`` lines are infrastructure, not sources — ignored.
    """

    import re as _re
    _LINE = _re.compile(r"^\s*\[([^\]]+)\]\s*(.*)$")
    _IGNORE = {"assert", "assert PASS", "assert FAIL", "WARN", "asof"}

    def __init__(self, inner):
        self._inner = inner
        # source -> {"status": str, "details": [str, …]}; "used" always wins over "skipped"
        self._seen: dict[str, dict] = {}

    def __call__(self, msg: str) -> None:
        self._inner(msg)
        self._capture(str(msg))

    def _capture(self, msg: str) -> None:
        m = self._LINE.match(msg)
        if not m:
            return
        name, rest = m.group(1).strip(), m.group(2).strip()
        if name in self._IGNORE or name.startswith("assert"):
            return
        # a "skipped:" / "skipped " prefix marks a data-blocked source; anything else
        # is a source that contributed (rows/cols/file summary).
        low = rest.lower()
        if low.startswith("skipped"):
            status, detail = "skipped", rest.split(":", 1)[-1].strip() if ":" in rest else rest
        else:
            status, detail = "used", rest
        for sub in name.split("/"):           # "[growth/plausibility] skipped" → both
            sub = sub.strip()
            if not sub:
                continue
            rec = self._seen.setdefault(sub, {"status": status, "details": []})
            if status == "used" and rec["status"] != "used":
                rec["status"], rec["details"] = "used", []   # a used line supersedes earlier skips
            if (status == rec["status"]) and detail and detail not in rec["details"]:
                rec["details"].append(detail)

    def records(self) -> list[dict]:
        """Audit rows, used-first then alphabetical — the manifest/report payload."""
        rows = [{"source": s, "status": v["status"], "detail": "; ".join(v["details"])}
                for s, v in self._seen.items()]
        rows.sort(key=lambda r: (r["status"] != "used", r["source"]))
        return rows


def _run_npi_adapters(preclean: Path, log, skip: set | None = None,
                      max_year: int | None = None,
                      no_trends: bool = False,
                      trend_years: int = 2) -> dict[str, pd.DataFrame]:
    """Run the per-NPI CMS adapters against whatever raw files are present.

    Each entry is (source_name, subdir/filenames, callable(raw)->npi-keyed frame).
    Missing files are skipped silently — a source landing later just lights up its
    columns on the next run (the skip-missing philosophy of the subscore engine).
    ``skip`` (lower-cased source names) force-skips an adapter — a safety valve for a
    memory-heavy source on a tight box. ``max_year`` (set on frozen/as-of runs) caps
    the annual-PUF vintage so a post-cutoff calendar year can't leak into a frozen
    matrix.
    """
    frames: dict[str, pd.DataFrame] = {}
    skip = skip or set()
    chosen: dict[str, tuple[Path, int]] = {}     # source -> (file used, its year)

    def _try(name: str, folder: Path, cols: dict | None, fn):
        if name in skip:
            log(f"    [{name}] skipped: --skip-sources")
            return
        candidates = _year_files(folder, max_year)
        if not candidates:
            if max_year is not None and _year_files(folder):
                log(f"    [{name}] skipped: only post-{max_year} vintages in "
                    f"{folder.name}/ — a frozen run refuses annual files newer "
                    f"than the cutoff (download the {max_year} year)")
            else:
                log(f"    [{name}] skipped: no source file in {folder.name}/")
            return
        # Newest year first, but a layout mismatch (ValueError from the adapter's
        # column resolver) falls back to the next-newest file instead of skipping
        # the whole source — the dmepos-2022 class of bug: the newest download was
        # a SUMMARY layout without HCPCS while older years had the right one.
        last_err = None
        for i, src_file in enumerate(candidates):
            try:
                raw = _read_one(src_file, cols)
                if raw is None or not len(raw):
                    last_err = f"empty source file {src_file.name}"
                    continue
                res = fn(raw)
                df = res[0] if isinstance(res, tuple) else res
                if df is not None and len(df) and "npi" in df.columns:
                    frames[name] = df
                    chosen[name] = (src_file, _file_year(src_file))
                    note = (f" ({i} newer file(s) had a different layout — "
                            "re-download the current-year detail file)" if i else "")
                    if max_year is not None:
                        note += f" [vintage cap: {max_year} or older]"
                    log(f"    [{name}] {len(df):,} providers from {src_file.name}, "
                        f"cols: {', '.join(c for c in df.columns if c != 'npi')}{note}")
                    return
                # never let a source VANISH from the audit: a present file whose
                # adapter returns empty/npi-less output is a skip with a reason.
                last_err = (f"adapter returned "
                            f"{'no rows' if df is None or not len(df) else 'no npi column'} "
                            f"from {src_file.name} — check the file layout")
            except ValueError as e:          # layout mismatch — try the next year
                last_err = f"{src_file.name}: {e}"
                continue
            except Exception as e:           # one bad source must never sink the run
                log(f"    [{name}] skipped: {e}")
                return
        log(f"    [{name}] skipped: {last_err}")

    pc = preclean
    from src.ingest_cms import partb, partd, dmepos, opioid, openpayments
    # multi-year folders: use the LATEST year, and read ONLY the columns the adapter
    # needs (the PUFs run to multiple GB — usecols keeps a 16 GB box from OOMing).
    _try("partb", pc / "partb", partb.PARTB_COLS,
         lambda r: partb.compute_partb_metrics(r))
    _try("partd", pc / "partd", partd.PARTD_COLS,
         lambda r: partd.compute_partd_metrics(r))
    _try("dmepos", pc / "dmepos", dmepos.DMEPOS_COLS,
         lambda r: dmepos.compute_dmepos_metrics(r))
    _try("opioid", pc / "opioid", opioid.OPIOID_COLS,
         lambda r: opioid.compute_opioid_metrics(r))
    _try("open_payments", pc / "open_payments", openpayments.OP_COLS,
         lambda r: openpayments.compute_openpayments_metrics(r))

    # Year-over-year trends: the operator holds multi-year PUF runs (Part B/D
    # 2016-2024). A share that JUMPED between the two newest usable years is
    # ramp signal the single-year snapshot can't see (upcoding creep, opioid
    # share escalation). The previous-year lookup goes through the same
    # vintage-capped candidate list, so a frozen run only trends PRE-cutoff
    # years — the trend itself is leakage-correct by construction.
    _TREND_COLS = {
        "partb": ["em_high_level_share", "em_level_mean"],
        "partd": ["high_cost_drug_share", "brand_generic_cost_ratio"],
        "opioid": ["opioid_claim_share", "opioid_long_acting_share"],
        "dmepos": ["dme_high_cost_item_share", "dme_code_concentration"],
    }
    _fns = {"partb": partb.compute_partb_metrics,
            "partd": partd.compute_partd_metrics,
            "dmepos": dmepos.compute_dmepos_metrics,
            "opioid": opioid.compute_opioid_metrics}
    _cmaps = {"partb": partb.PARTB_COLS, "partd": partd.PARTD_COLS,
              "dmepos": dmepos.DMEPOS_COLS, "opioid": opioid.OPIOID_COLS}
    if not no_trends:
        for name, tcols in _TREND_COLS.items():
            try:
                if name not in frames or name not in chosen:
                    continue
                cur_file, cur_year = chosen[name]
                if cur_year <= 0:
                    continue
                # A year-over-year delta must span EXACTLY one year: with a gap
                # (2019 -> 2023) the change would be silently mislabeled as
                # one year of drift. Require the adjacent prior year; if it is
                # missing, say so loudly — that IS the download cue.
                older = [p for p in _year_files(pc / name, max_year)
                         if 0 < _file_year(p) < cur_year]
                adjacent = [p for p in older if _file_year(p) == cur_year - 1]
                if not adjacent:
                    have = sorted({_file_year(p) for p in older}, reverse=True)
                    log(f"    [{name}_trend] skipped: no {cur_year - 1} file "
                        f"(newest is {cur_year}; older on disk: "
                        f"{', '.join(map(str, have)) if have else 'none'}) — "
                        f"download the {cur_year - 1} year to enable the trend")
                    continue
                prev_df, prev_file = None, None
                for p in adjacent:
                    try:
                        raw = _read_one(p, _cmaps[name])
                        if raw is None or not len(raw):
                            continue
                        res = _fns[name](raw)
                        prev_df = res[0] if isinstance(res, tuple) else res
                        prev_file = p
                        break
                    except ValueError as ve:
                        log(f"    [{name}_trend] skipped: {p.name} has the right "
                            f"year but the wrong layout ({ve}) — re-download the "
                            f"{cur_year - 1} detail file")
                        break
                if prev_df is None or "npi" not in prev_df.columns:
                    continue
                cur_df = frames[name]
                share = [c for c in tcols
                         if c in cur_df.columns and c in prev_df.columns]
                if not share:
                    continue
                merged = cur_df[["npi"] + share].merge(
                    prev_df[["npi"] + share].drop_duplicates("npi"),
                    on="npi", suffixes=("", "__prev"))
                tr = pd.DataFrame({"npi": merged["npi"]})
                for c in share:
                    tr[f"{c}_yoy"] = (pd.to_numeric(merged[c], errors="coerce")
                                      - pd.to_numeric(merged[f"{c}__prev"],
                                                      errors="coerce"))
                # Multi-year slope (--trend-years N > 2): the operator's deeper
                # history separates a one-year blip from a SUSTAINED trajectory.
                # Extra years within the window are read (each is a full PUF
                # read — that is why it is opt-in) and a per-provider per-year
                # slope is fit over >=3 consecutive-ish points.
                if trend_years > 2:
                    year_frames = {cur_year: cur_df, cur_year - 1: prev_df}
                    lo = cur_year - (trend_years - 1)
                    for p in older:
                        y = _file_year(p)
                        if y in year_frames or y < lo:
                            continue
                        try:
                            raw = _read_one(p, _cmaps[name])
                            if raw is None or not len(raw):
                                continue
                            res = _fns[name](raw)
                            yf = res[0] if isinstance(res, tuple) else res
                            if yf is not None and "npi" in yf.columns:
                                year_frames[y] = yf
                        except ValueError:
                            continue
                    if len(year_frames) >= 3:
                        import numpy as np
                        years = sorted(year_frames)
                        wide = None
                        for y in years:
                            f = year_frames[y][["npi"] + [c for c in share
                                                          if c in year_frames[y].columns]]
                            f = f.drop_duplicates("npi").set_index("npi")
                            f.columns = [f"{c}@{y}" for c in f.columns]
                            wide = f if wide is None else wide.join(f, how="outer")
                        t = np.array(years, dtype=float)
                        for c in share:
                            cols_y = [f"{c}@{y}" for y in years if f"{c}@{y}" in wide.columns]
                            if len(cols_y) < 3:
                                continue
                            x = wide[cols_y].apply(pd.to_numeric, errors="coerce").to_numpy()
                            ty = np.array([float(cy.split("@")[1]) for cy in cols_y])
                            mask = ~np.isnan(x)
                            n = mask.sum(axis=1)
                            tm = np.where(mask, ty, np.nan)
                            tbar = np.nanmean(tm, axis=1, keepdims=True)
                            xbar = np.nanmean(np.where(mask, x, np.nan), axis=1,
                                              keepdims=True)
                            num = np.nansum((tm - tbar) * (x - xbar), axis=1)
                            den = np.nansum((tm - tbar) ** 2, axis=1)
                            with np.errstate(invalid="ignore", divide="ignore"):
                                slope = np.where((n >= 3) & (den > 0), num / den, np.nan)
                            tr = tr.merge(pd.DataFrame(
                                {"npi": wide.index, f"{c}_slope": slope}),
                                on="npi", how="outer")
                        log(f"    [{name}_trend] multi-year slope over "
                            f"{', '.join(map(str, years))} "
                            f"({sum(1 for c in tr.columns if c.endswith('_slope'))} "
                            "slope columns)")
                frames[f"{name}_trend"] = tr
                log(f"    [{name}_trend] {len(tr):,} providers: {prev_file.name} "
                    f"→ {cur_file.name} year-over-year deltas "
                    f"({', '.join(f'{c}_yoy' for c in share)})")
            except Exception as e:
                log(f"    [{name}_trend] skipped: {e}")

    # Part D × Open Payments kickback co-occurrence (per-NPI) needs BOTH raws.
    if "kickback" in skip:
        log("    [kickback] skipped: --skip-sources")
        return frames
    try:
        op_raw = _read_latest(pc / "open_payments", openpayments.OP_COLS, max_year)
        pd_raw = _read_latest(pc / "partd", partd.PARTD_COLS, max_year)
        if op_raw is not None and pd_raw is not None:
            kb = openpayments.kickback_co_occurrence(op_raw, pd_raw)
            if kb is not None and len(kb) and "npi" in kb.columns:
                frames["kickback"] = kb
                log(f"    [kickback] {len(kb):,} prescribers (op_payment_utilization_corr)")
    except Exception as e:
        log(f"    [kickback] skipped: {e}")
    return frames


def _run_org_grain_adapters(preclean: Path, processed: Path, npi_to_org: pd.DataFrame,
                            org_nodes: pd.DataFrame | None,
                            ccn_to_npi: pd.DataFrame | None, log,
                            with_analytics: bool = False,
                            snapshots_dir: Path | None = None,
                            asof_spending: Path | None = None,
                            skip: set | None = None,
                            asof_cutoff: str | None = None
                            ) -> dict[str, pd.DataFrame]:
    """Run the adapters that resolve at ORG or CCN grain and return org-keyed frames
    (build_provider_matrix broadcasts them down to each member NPI).

    Each block guards on ALL its inputs; a missing input logs a precise reason so the
    export report shows exactly which scenarios are scored and which are data-blocked.
    ``asof_spending`` (when set) replaces the spending fact with a pre-cutoff filtered
    copy, so the billing-derived enrichments are point-in-time correct.
    """
    frames: dict[str, pd.DataFrame] = {}
    skip = skip or set()
    pc, proc = preclean, processed
    spending_p = asof_spending or _first_existing(proc, "spending_fact.parquet")

    def _skipped(name: str) -> bool:
        if name in skip:
            log(f"    [{name}] skipped: --skip-sources")
            return True
        return False

    # --- analytics enrichments (growth-shock + clinical plausibility) -----------
    # DuckDB-streamed straight from the spending parquet (the 238M-row fact never
    # enters pandas), so they scale to the full universe. They feed rapid_ramp and
    # specialty_mismatch via pass-through (already one-sided percentiles). Opt-in
    # because they add a couple of streaming passes over the fact.
    if with_analytics and spending_p:
        try:
            from src.analytics import growth
            gp = growth.growth_percentiles(
                growth.growth_features_from_parquet(str(spending_p), npi_to_org))
            if len(gp):
                frames["growth"] = gp
                log(f"    [growth] {len(gp):,} orgs, cols: growth_level_shift, new_code_burst")
        except Exception as e:
            log(f"    [growth] skipped: {e}")
        try:
            from src.analytics import plausibility
            pdim_p = _first_existing(proc, "provider_dim.parquet")
            if pdim_p:
                pp = plausibility.plausibility_percentiles(
                    plausibility.org_clinical_plausibility_duckdb(
                        str(spending_p), pd.read_parquet(pdim_p), npi_to_org))
                if len(pp) and "clinical_implausibility" in pp.columns:
                    frames["plausibility"] = pp[["org_node_id", "clinical_implausibility"]]
                    log(f"    [plausibility] {len(pp):,} orgs, cols: clinical_implausibility")
            else:
                log("    [plausibility] skipped: needs processed/provider_dim.parquet")
        except Exception as e:
            log(f"    [plausibility] skipped: {e}")
    elif not with_analytics:
        log("    [growth/plausibility] skipped: pass --with-analytics "
            "(DuckDB-streamed; scales to the full spending fact)")

    def _emit(name: str, fr, feature_cols: list[str]):
        if fr is not None and len(fr) and "org_node_id" in fr.columns:
            frames[name] = fr
            log(f"    [{name}] {len(fr):,} orgs, cols: {', '.join(feature_cols)}")
        else:
            log(f"    [{name}] skipped: adapter returned "
                f"{'no rows' if fr is None or not len(fr) else 'no org_node_id'} "
                f"— source present but produced nothing usable")

    # --- post-deactivation billing (spending + deactivated NPIs + npi_to_org) ---
    try:
        from src.ingest_cms import nppes_deactivation as nd
        dp = _first_existing(pc / "nppes_deactivation", "deactivation.csv",
                             "deactivation.xlsx", "*.zip", "*.xlsx", "*.csv")
        if dp and spending_p:
            deact, _ = nd.deactivated_npis(nd.load_deactivation(dp))
            spend = _spending_for_npis(spending_p, deact["npi"].tolist())
            _emit("nppes_deactivation",
                  nd.billing_after_deactivation(spend, deact, npi_to_org),
                  ["billing_after_deactivation"])
        else:
            log("    [nppes_deactivation] skipped: needs deactivation file + processed/spending_fact.parquet")
    except Exception as e:
        log(f"    [nppes_deactivation] skipped: {e}")

    # --- billing after death (SSA Death Master File, DOB-corroborated) ----------
    # Only HIGH-confidence (name + DOB) matches feed the score; name-only matches
    # are review flags, never drivers (defamation guardrail in death_master.py).
    try:
        from src.enforcement import death_master as dm
        dmf_p = _first_existing(pc / "dmf", "dmf.csv", "*.csv")
        pdim_p = _first_existing(proc, "provider_dim.parquet")
        if dmf_p and pdim_p and spending_p:
            dmf = dm.parse_dmf(_read_any(dmf_p))
            matches = dm.match_deceased_providers(pd.read_parquet(pdim_p), dmf)
            hi = matches[matches["match_confidence"] == "high"]
            spend = _spending_for_npis(spending_p, hi["npi"].tolist())
            _emit("death_master",
                  dm.billing_after_death(spend, matches, npi_to_org),
                  ["billing_after_death"])
            log(f"    [death_master] {len(hi):,} high-confidence deceased-NPI matches "
                f"(of {len(matches):,}; name-only matches held for review, not scored)")
        else:
            log("    [death_master] skipped: needs SSA DMF file (preclean/dmf/) + "
                "provider_dim + spending")
    except Exception as e:
        log(f"    [death_master] skipped: {e}")

    # --- NADAC drug-spread anomaly (NDC-level claims + NADAC ref + npi_to_org) ---
    try:
        from src.ingest_cms import nadac
        nref_p = _first_existing(pc / "nadac", "nadac.csv", "*.csv")
        ndc_p = _first_existing(proc, "ndc_claims.parquet")
        if nref_p and ndc_p:
            ref = nadac.compute_nadac_reference(_read_any(nref_p))
            _emit("nadac", nadac.drug_spread_anomaly(pd.read_parquet(ndc_p), ref, npi_to_org),
                  ["drug_spread_anomaly"])
        else:
            log("    [nadac] skipped: needs NADAC reference + processed/ndc_claims.parquet (NDC-level claims)")
    except Exception as e:
        log(f"    [nadac] skipped: {e}")

    # --- ineligible-referral share (referred claims + eligibility + npi_to_org) ---
    try:
        from src.ingest_cms import order_referring as orr
        elig_p = _first_existing(pc / "order_referring", "order_referring.csv", "*.csv")
        ref_p = _first_existing(proc, "referred_claims.parquet")
        if elig_p and ref_p:
            elig, _ = orr.eligible_referrers(_read_any(elig_p))
            _emit("order_referring",
                  orr.ineligible_referral_share(pd.read_parquet(ref_p), elig, npi_to_org),
                  ["ineligible_referral_share"])
        else:
            log("    [order_referring] skipped: needs eligibility file + processed/referred_claims.parquet (referring_npi pairs)")
    except Exception as e:
        log(f"    [order_referring] skipped: {e}")

    # --- 340B contract-pharmacy concentration (OPAIS entities + org_nodes) ---
    try:
        from src.ingest_cms import hrsa_340b
        ent_p = _first_existing(pc / "hrsa_340b", "opais.xlsx", "opais.csv",
                                "*.xlsx", "*.csv")
        if ent_p and org_nodes is not None:
            ents = hrsa_340b.covered_entities(hrsa_340b.load_opais(ent_p))
            base = org_nodes[["org_node_id"]].copy()
            out = hrsa_340b.attach_340b(base, org_nodes, ents)
            _emit("hrsa_340b", out[["org_node_id", "contract_pharmacy_concentration"]],
                  ["contract_pharmacy_concentration"])
        else:
            log("    [hrsa_340b] skipped: needs OPAIS file + org_nodes")
    except Exception as e:
        log(f"    [hrsa_340b] skipped: {e}")

    # --- market saturation (county file → metrics + org_nodes) ---
    if not _skipped("saturation"):
      try:
        from src.ingest_cms import saturation as sat
        sat_p = _first_existing(pc / "saturation", "saturation.csv", "*.csv")
        if sat_p and org_nodes is not None:
            county = sat.compute_saturation_metrics(_read_any(sat_p),
                                                    max_period=asof_cutoff)
            rp = county.attrs.get("reference_period") if hasattr(county, "attrs") else None
            if rp:
                log(f"    [saturation] reference period {rp}"
                    + (f" (capped at cutoff {asof_cutoff})" if asof_cutoff else ""))
            # HUD ZIP->county crosswalk: upgrades the attach from state grain to
            # county grain. The loader existed but was never passed here, so a
            # downloaded crosswalk was dead weight. Accept both save locations.
            z2c = None
            z2c_p = _first_existing(pc / "hud", "zip_county.csv", "*.csv") or \
                _first_existing(pc / "zip_county", "zip_county.csv", "*.csv")
            if z2c_p:
                try:
                    from src.ingest_cms.census_population import zip_to_county
                    z2c = zip_to_county(_read_any(z2c_p))
                    log(f"    [saturation] county-grain attach via {z2c_p.name} "
                        f"({len(z2c):,} ZIP rows)")
                except Exception as ze:
                    log(f"    [saturation] zip_county unusable ({ze}); state grain")
            base = org_nodes[["org_node_id"]].copy()
            out = sat.attach_market_saturation(base, org_nodes, county,
                                               zip_to_county=z2c)
            _emit("saturation", out[["org_node_id", "market_saturation_index"]],
                  ["market_saturation_index"])
        else:
            log("    [saturation] skipped: needs county saturation file + org_nodes")
      except Exception as e:
        log(f"    [saturation] skipped: {e}")

    # --- ownership churn (CHOW): two paths, dated-edition diff preferred ------
    # 1) VINTAGE DIFF: preclean/owners_prior (an older All-Owners edition from
    #    the dataset's version history) vs preclean/owners — real churn today,
    #    no waiting for monthly snapshots. Post-cutoff window by nature, so it
    #    is a CURRENT-DAY feature (current_state class), never frozen.
    # 2) SNAPSHOT DIFF: the monthly graph-snapshot cadence (>=2 to fire).
    churn_done = False
    try:
        from src.entity_graph.owners_vintage import (load_editions,
                                                     multi_edition_turnover,
                                                     org_grain_turnover)
        prior_dir = preclean / "owners_prior"
        cur_dir = preclean / "owners"
        if asof_cutoff:
            log("    [ownership_churn] vintage diff not used on a frozen run "
                "(the edition window is post-cutoff); snapshot path only")
        elif prior_dir.is_dir() and cur_dir.is_dir():
            prior_ds = load_editions(prior_dir)
            cur_ds = load_editions(cur_dir)
            if prior_ds and cur_ds:
                xw_p = _first_existing(processed, "npi_xwalk.parquet")
                turn, chains = multi_edition_turnover(prior_ds, cur_ds)
                if xw_p is not None and len(turn):
                    org_t = org_grain_turnover(turn, pd.read_parquet(xw_p),
                                               npi_to_org)
                    if len(org_t):
                        _emit("ownership_churn", org_t,
                              ["ownership_turnover", "n_owner_entries",
                               "n_owner_exits"])
                        n_ch = int((org_t["ownership_turnover"] > 0).sum())
                        log(f"    [ownership_churn] per-dataset edition chains "
                            f"({'; '.join(chains)}): {len(org_t):,} orgs mapped, "
                            f"{n_ch:,} with owner changes")
                        churn_done = True
    except Exception as e:
        log(f"    [ownership_churn] vintage diff failed ({e}); trying snapshots")
    if not churn_done:
        try:
            from src.entity_graph.ownership_snapshot import (compute_ownership_turnover,
                                                             load_snapshots)
            if snapshots_dir and len(load_snapshots(snapshots_dir)) >= 2:
                turn = compute_ownership_turnover(snapshots_dir)
                if len(turn):
                    _emit("ownership_churn", turn[["org_node_id", "ownership_turnover"]],
                          ["ownership_turnover"])
            else:
                n = len(load_snapshots(snapshots_dir)) if snapshots_dir else 0
                log(f"    [ownership_churn] skipped: {n} owner snapshot(s) — needs ≥2 "
                    "(snapshot cadence, or drop an older All-Owners edition in "
                    "preclean/owners_prior/ for the vintage diff)")
        except Exception as e:
            log(f"    [ownership_churn] skipped: {e}")

    # --- facility (PBJ/hospice/deficiency) + HCRIS + POS: CCN-grain → org via ccn_to_npi ---
    if _skipped("facility"):
        pass
    elif ccn_to_npi is None:
        log("    [facility/hcris/pos] skipped: no CCN→NPI crosswalk "
            "(processed/ccn_to_npi.parquet) — the one missing link for the "
            "facility / cost-report / capacity schemes")
    else:
        _run_ccn_grain(pc, npi_to_org, ccn_to_npi, _emit, log)
    return frames


def _run_ccn_grain(pc: Path, npi_to_org, ccn_to_npi, _emit, log) -> None:
    """Facility / HCRIS / POS adapters: CCN-grain features rolled to org via the
    PECOS CCN↔NPI crosswalk, then (by the caller) broadcast to NPI."""
    from src.ingest_cms import facility as fac, hcris as hc, pos
    # facility: INDEPENDENT try per metric — one bad file (e.g. an oddly encoded
    # PBJ) must not take hospice + deficiencies down with it.
    ccn_feats = []
    try:
        pbj_files = sorted((pc / "facility").glob("pbj*.csv"))
        if pbj_files:
            parts = []
            for pf in pbj_files:            # multi-quarter: read them all, concat
                parts.append(fac.compute_pbj_metrics(_read_any(pf))[0])
                log(f"    [facility/pbj] read {pf.name}")
            m = pd.concat(parts, ignore_index=True).groupby(
                "ccn", as_index=False).mean(numeric_only=True)
            ccn_feats.append(fac.facility_peer_percentiles(m, ["pbj_understaffing"]))
    except Exception as e:
        log(f"    [facility/pbj] skipped: {e}")
    try:
        hos_p = _first_existing(pc / "facility", "hospice.csv", "hospice*.csv")
        if hos_p:
            m, _ = fac.compute_hospice_metrics(_read_any(hos_p))
            ccn_feats.append(fac.facility_peer_percentiles(m, ["hospice_live_discharge_rate"]))
    except Exception as e:
        log(f"    [facility/hospice] skipped: {e}")
    try:
        defp = _first_existing(pc / "facility", "deficiencies.csv", "*deficienc*.csv")
        if defp:
            m, _ = fac.compute_deficiency_counts(_read_any(defp))
            ccn_feats.append(fac.facility_peer_percentiles(
                m, ["deficiency_count", "deficiency_severity_weighted"]))
    except Exception as e:
        log(f"    [facility/deficiencies] skipped: {e}")
    try:
        if ccn_feats:
            merged = ccn_feats[0]
            for extra in ccn_feats[1:]:
                merged = merged.merge(extra, on="ccn", how="outer")
            org = fac.rollup_ccn_to_org(merged, ccn_to_npi, npi_to_org)
            _emit("facility", org, [c for c in org.columns if c != "org_node_id"])
    except Exception as e:
        log(f"    [facility] skipped: {e}")
    # HCRIS cost-report anomaly — ALL vintage files (hospital/HHA/SNF), not just
    # the alphabetically first one: load_hcris resolves each layout separately.
    try:
        hcris_files = sorted((pc / "hcris").glob("*.csv"))
        if hcris_files:
            m, h_dropped = hc.load_hcris(hcris_files)
            log(f"    [hcris] {len(hcris_files)} files -> {len(m):,} CCN rows "
                f"({h_dropped} dropped)")
            anom = hc.hcris_anomaly(m)[["ccn", "hcris_cost_anomaly"]]
            org = fac.rollup_ccn_to_org(anom, ccn_to_npi, npi_to_org)
            _emit("hcris", org, ["hcris_cost_anomaly"])
    except Exception as e:
        log(f"    [hcris] skipped: {e}")
    # POS facility capacity (bed counts). CMS ships TWO quarterly files (the QIES
    # hospital/non-hospital file + the iQIES HHA/hospice/SNF/ASC file) — read every
    # csv in preclean/pos and concat, keeping each CCN's max bed figure. This block
    # was missing entirely: pos was imported but never invoked, so a dropped-in
    # POS file was silently unused (the DocGraph wiring-gap class of bug).
    try:
        # rglob, not glob: the CMS zips extract into their own subfolders
        # ("Provider of Services File - .../*.csv") — top-level-only matching
        # would silently skip them.
        pos_files = sorted((pc / "pos").rglob("*.csv"))
        if pos_files:
            caps = []
            for pf in pos_files:
                try:                      # per-file: a stray reference/layout csv
                    caps.append(pos.compute_pos_capacity(_read_any(pf)))
                    log(f"    [pos] read {pf.name}")
                except Exception as fe:   # inside the zip must not sink the rest
                    log(f"    [pos] {pf.name} skipped: {fe}")
            if not caps:
                raise ValueError("no POS file parsed")
            cap = pd.concat(caps, ignore_index=True)
            cap = cap.groupby("ccn", as_index=False).agg(
                bed_count=("bed_count", "max"),
                facility_type=("facility_type", "first"),
                state=("state", "first"))
            cap = cap.rename(columns={"bed_count": "pos_bed_count"})
            org = fac.rollup_ccn_to_org(cap[["ccn", "pos_bed_count"]],
                                        ccn_to_npi, npi_to_org)
            _emit("pos", org, ["pos_bed_count"])
    except Exception as e:
        log(f"    [pos] skipped: {e}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", default=None, help="override MEDICAID_DATA_ROOT")
    ap.add_argument("--graph-dir", default=None,
                    help="entity-graph output dir (npi_to_org + org_graph_features)")
    ap.add_argument("--leads", default=None,
                    help="fraud_leads_v3.parquet (per-NPI concepts + label)")
    ap.add_argument("--preclean", default=None,
                    help="raw source root (per-NPI adapters run against subdirs here)")
    ap.add_argument("--processed", default=None,
                    help="processed root (spending_fact / ndc_claims / referred_claims)")
    ap.add_argument("--ccn-to-npi", default=None,
                    help="PECOS CCN↔NPI crosswalk parquet (csv/parquet) — unlocks the "
                         "facility / HCRIS / POS (CCN-grain) schemes")
    ap.add_argument("--out", default=None, help="output dir")
    ap.add_argument("--with-analytics", action="store_true",
                    help="also run growth-shock + clinical-plausibility enrichments "
                         "(in-memory pandas; use a filtered spending file)")
    ap.add_argument("--owner-snapshots", default=None,
                    help="owner-snapshot archive dir (ownership_turnover; default "
                         "<data-root>/owner_snapshots)")
    ap.add_argument("--case-db", default=None,
                    help="DOJ/qui tam case DB (csv/parquet) → scheme-typed, "
                         "time-boxed positives folded into the label")
    ap.add_argument("--geocode", action="store_true",
                    help="live-geocode billing addresses (Census, free) → "
                         "addr_geocoded / addr_no_match (network; off by default)")
    ap.add_argument("--case-control", action="store_true",
                    help="also write a matched case-control training set "
                         "(each positive vs. comparable clean controls)")
    ap.add_argument("--snapshot", action="store_true",
                    help="archive a valid-time snapshot of the matrix to the "
                         "point-in-time feature store (Pillar 1)")
    ap.add_argument("--asof", default=None,
                    help="valid-time stamp for --snapshot (YYYY-MM-DD; default today)")
    ap.add_argument("--asof-cutoff", default=None,
                    help="feature-freeze date (YYYY-MM-DD): compute ALL billing "
                         "features only on service months BEFORE it (point-in-time, "
                         "leakage-correct out-of-time training matrix)")
    ap.add_argument("--snapshot-dir", default=None,
                    help="feature-snapshot store dir (default <data-root>/feature_snapshots)")
    ap.add_argument("--trend-years", type=int, default=2,
                    help="years of PUF history for trends: 2 = the one-step "
                         "year-over-year delta (default); 3+ ALSO fits a "
                         "per-provider multi-year slope (<col>_slope) — each "
                         "extra year is another full PUF read")
    ap.add_argument("--no-trends", action="store_true",
                    help="skip the year-over-year PUF trend features (saves one "
                         "extra prior-year read per multi-year source)")
    ap.add_argument("--fixture", action="store_true",
                    help="build from the synthetic fixture (no real data)")
    ap.add_argument("--skip-sources", default=None,
                    help="comma-separated source names to skip (e.g. "
                         "'kickback,billing_lm') — a safety valve for a memory-heavy "
                         "adapter on a tight box; the column is simply absent (NaN)")
    args = ap.parse_args()
    skip = {s.strip().lower() for s in (args.skip_sources or "").split(",") if s.strip()}

    root = Path(args.data_root or os.environ.get(
        "MEDICAID_DATA_ROOT", str(Path.home() / "Desktop" / "data")))

    # Capture every "[source] …" progress line into a structured audit so the run
    # ends with one SOURCES_REPORT table (used/skipped + reason + file), not a console
    # the operator has to grep. ``audit`` IS the log everywhere a source is reported.
    audit = _SourceAudit(print)

    if args.fixture:
        from src.entity_graph.__main__ import run as run_graph
        from tests.fixtures.synthetic import (build_synthetic_inputs,
                                              build_provider_leads,
                                              build_npi_adapter_frames)
        tmp = Path(args.out or "/tmp/provider_export") / "_graph"
        outputs = run_graph(build_synthetic_inputs(), tmp)
        leads = build_provider_leads(build_synthetic_inputs()["provider_dim"])
        npi_to_org = outputs["npi_to_org"]
        gf = outputs["org_graph_features"]
        adapter_frames = build_npi_adapter_frames(leads["npi"].tolist())
        from src.entity_graph.graph_embeddings import to_provider_grain
        pe = to_provider_grain(outputs.get("node_embeddings"), npi_to_org)
        if len(pe):
            adapter_frames["graph_embeddings"] = pe
        from .address_grounding import address_flags
        af = address_flags(build_synthetic_inputs()["provider_dim"])
        if len(af):
            adapter_frames["address"] = af
        org_grain = {}
        nucc_pg, widened, case_lbls = None, None, None
    else:
        if not args.graph_dir or not args.leads:
            ap.error("--graph-dir and --leads are required (or use --fixture)")
        g = Path(args.graph_dir)
        npi_to_org = pd.read_parquet(g / "npi_to_org.parquet")
        gf = pd.read_parquet(g / "org_graph_features.parquet")
        org_nodes = (pd.read_parquet(g / "nodes" / "org_nodes.parquet")
                     if (g / "nodes" / "org_nodes.parquet").exists() else None)
        _warn_if_graph_stale(g, Path(args.leads), print)
        leads = pd.read_parquet(args.leads)
        if "npi" not in leads.columns:
            ap.error(f"{args.leads} is not per-NPI (no 'npi' column)")
        preclean = Path(args.preclean) if args.preclean else root / "preclean"
        processed = Path(args.processed) if args.processed else root / "processed"
        ccn_xw = Path(args.ccn_to_npi) if args.ccn_to_npi else processed / "ccn_to_npi.parquet"
        ccn_to_npi = _read_any(ccn_xw)
        print("  running per-NPI adapters …")
        # Frozen-run vintage cap: an annual PUF for a calendar year AFTER the
        # cutoff carries post-cutoff behavior. Cap at the cutoff YEAR — for a
        # 2023-12 cutoff the CY2023 files still include one post-cutoff month
        # (December); that residue is accepted rather than discarding eleven
        # good months by capping a whole year earlier.
        vintage_cap = int(str(args.asof_cutoff)[:4]) if args.asof_cutoff else None
        if vintage_cap:
            print(f"  [vintage] annual-PUF cap for the frozen run: {vintage_cap} or older")
        adapter_frames = _run_npi_adapters(preclean, audit, skip=skip,
                                           max_year=vintage_cap,
                                           no_trends=args.no_trends,
                                           trend_years=args.trend_years)
        # order_referring referrer-grain fallback: the public DMEPOS detail file
        # has no supplier NPI (org-grain ineligible_referral_share stays gated),
        # but the REFERRER's own standing is checkable today — DME dollars billed
        # off an NPI's orders while that NPI is not DME-eligible on the O&R list.
        try:
            if "dmepos" in adapter_frames:
                from src.ingest_cms import order_referring as _orr
                elig_p = _first_existing(preclean / "order_referring",
                                         "order_referring.csv", "*.csv")
                if elig_p:
                    elig, _q = _orr.eligible_referrers(_read_any(elig_p))
                    fb = _orr.referrer_ineligible_dme(adapter_frames["dmepos"], elig)
                    if len(fb):
                        adapter_frames["order_referring_referrer"] = fb
                        n_bad = int(fb["dme_ineligible_referrer"].sum())
                        audit(f"    [order_referring] referrer-grain check from "
                              f"DMEPOS + {elig_p.name}: {len(fb):,} referrers, "
                              f"{n_bad:,} not DME-eligible (org-grain share "
                              "still needs referred_claims)")
        except Exception as e:
            audit(f"    [order_referring] referrer-grain check skipped: {e}")
        ne_p = g / "node_embeddings.parquet"
        if ne_p.exists():
            from src.entity_graph.graph_embeddings import to_provider_grain
            pe = to_provider_grain(pd.read_parquet(ne_p), npi_to_org)
            if len(pe):
                adapter_frames["graph_embeddings"] = pe
                audit(f"    [graph_embeddings] {len(pe):,} providers from "
                      f"{ne_p.name}, {pe.shape[1] - 1} graph columns "
                      f"(embeddings + proximity + motifs)")
        else:
            audit("    [graph_embeddings] skipped: rebuild the graph to emit "
                  "node_embeddings.parquet")
        # Point-in-time billing: filter the spending fact to BEFORE the cutoff once,
        # then every billing builder runs unchanged on the leakage-correct file.
        asof_spend_p = None
        if args.asof_cutoff:
            base_spend = _first_existing(processed, "spending_fact.parquet")
            if base_spend:
                from .asof_billing import write_asof_spending
                asof_spend_p = root / "interim" / f"spending_asof_{args.asof_cutoff}.parquet"
                kept, dropped = write_asof_spending(str(base_spend), args.asof_cutoff,
                                                    str(asof_spend_p))
                print(f"  [asof] feature-freeze {args.asof_cutoff}: billing features "
                      f"see {kept:,} pre-cutoff rows ({dropped:,} dropped)")
                # CRITICAL: freeze the BASE frame too, not just the adapters — the
                # leads file carries full-history stats + concept percentiles.
                leads = _apply_asof_freeze(leads, asof_spend_p, args.asof_cutoff, print)
            else:
                print("  [asof] skipped: no processed/spending_fact.parquet")
        else:
            # Full-run tenure backfill: the leads file carries dollars/volume but
            # not tenure_months / n_active_months, and the instant-scale
            # consistency check needs tenure — a missing column silently made
            # incons_instant_scale constant-0 on run 3. Date-based stats only
            # (immune to the corrupt-dollar rows); fills ONLY absent columns.
            _TENURE_COLS = ["tenure_months", "n_active_months",
                            "first_service_month", "last_service_month"]
            if any(c not in leads.columns for c in _TENURE_COLS[:2]):
                spend_full = _first_existing(processed, "spending_fact.parquet")
                if spend_full:
                    from .asof_billing import asof_provider_stats
                    stats = asof_provider_stats(str(spend_full), "9999-12")
                    stats["npi"] = stats["npi"].astype(str)
                    fill = [c for c in _TENURE_COLS
                            if c in stats.columns and c not in leads.columns]
                    if fill:
                        n0 = len(leads)
                        leads = leads.merge(
                            stats[["npi"] + fill].drop_duplicates("npi"),
                            on="npi", how="left")
                        assert len(leads) == n0, "tenure-backfill join fanned out"
                        print(f"  [tenure] backfilled {', '.join(fill)} from the "
                              f"spending fact ({stats['npi'].nunique():,} NPIs) — "
                              "consistency checks need enrollment tenure")
                else:
                    print("  [tenure] backfill skipped: no processed/"
                          "spending_fact.parquet (incons_instant_scale stays dark)")

        print("  running org/CCN-grain adapters …")
        snapshots_dir = (Path(args.owner_snapshots) if args.owner_snapshots
                         else root / "owner_snapshots")
        org_grain = _run_org_grain_adapters(preclean, processed, npi_to_org,
                                            org_nodes, ccn_to_npi, audit,
                                            with_analytics=args.with_analytics,
                                            snapshots_dir=snapshots_dir,
                                            asof_spending=asof_spend_p, skip=skip,
                                            asof_cutoff=args.asof_cutoff)
        nucc_pg = _load_nucc_peer_groups(preclean, processed, audit)
        widened = _widened_label_from_graph(g, audit)

        # §E: smoking-gun TIME ATTRIBUTES — dated timelines for the definitional
        # flags (months/dollars billed AFTER exclusion or deactivation). These are
        # leakage_hard case-file enrichment, never features (LEAKAGE_HARD carries
        # the column names, so the manifest quarantines them by contract). The
        # same engine wires billing-after-death the day the SSA DMF lands.
        try:
            from .smoking_gun_timeline import billing_after_cutoff
            spend_tl = asof_spend_p or _first_existing(processed, "spending_fact.parquet")
            cuts = []
            ep = g / "nodes" / "exclusion_nodes.parquet"
            if ep.exists():
                ex = pd.read_parquet(ep)
                dcol = next((c for c in ("excl_date", "exclusion_date", "EXCLDATE")
                             if c in ex.columns), None)
                if "npi" in ex.columns and dcol:
                    ex = ex.copy()
                    ex["npi"] = ex["npi"].astype(str)
                    ex = ex[ex["npi"].str.len() >= 10]
                    cuts.append(("excl", ex.rename(columns={dcol: "cutoff_date"})
                                 [["npi", "cutoff_date"]]))
            dp = _first_existing(preclean / "nppes_deactivation", "deactivation.csv",
                                 "deactivation.xlsx", "*.zip", "*.xlsx", "*.csv")
            if dp:
                from src.ingest_cms import nppes_deactivation as nd
                deact, _ = nd.deactivated_npis(nd.load_deactivation(dp))
                if "deactivation_date" in deact.columns:
                    cuts.append(("deact", deact.rename(
                        columns={"deactivation_date": "cutoff_date"})
                        [["npi", "cutoff_date"]]))
            if spend_tl and cuts:
                tl = None
                for prefix, cut in cuts:
                    spend = _spending_for_npis(spend_tl, cut["npi"].tolist())
                    f = billing_after_cutoff(spend, cut, prefix)
                    tl = f if tl is None else tl.merge(f, on="npi", how="outer")
                if tl is not None and len(tl):
                    adapter_frames["smoking_gun_timeline"] = tl
                    audit(f"    [smoking_gun_timeline] {len(tl):,} providers with "
                          f"cutoff timelines ({', '.join(p for p, _ in cuts)}) — "
                          f"leakage_hard case-file columns, never features")
            else:
                audit("    [smoking_gun_timeline] skipped: needs spending_fact + "
                      "exclusion dates (graph) or the deactivation file")
        except Exception as e:
            audit(f"    [smoking_gun_timeline] skipped: {e}")

        # §I4/I5/I3: Medicaid-fact sector schemes — NEMT, behavioral health, and
        # the impossible-day approximation. New signal from data already in hand;
        # DuckDB-streamed, the fact never enters pandas.
        try:
            from src.ingest_cms.sector_schemes import sector_metrics_from_parquet
            spend_ss = asof_spend_p or _first_existing(processed, "spending_fact.parquet")
            mins = _first_existing(preclean / "hcpcs_time", "hcpcs_minutes.csv", "*.csv")
            if spend_ss:
                ss = sector_metrics_from_parquet(spend_ss, mins)
                if len(ss):
                    adapter_frames["sector_schemes"] = ss
                    audit(f"    [sector_schemes] {len(ss):,} providers with NEMT/BH"
                          + ("/impossible-day" if mins else "") + " metrics"
                          + ("" if mins else " (impossible-day lights up with "
                             "preclean/hcpcs_time/hcpcs_minutes.csv)"))
            else:
                audit("    [sector_schemes] skipped: needs processed/spending_fact.parquet")
        except Exception as e:
            audit(f"    [sector_schemes] skipped: {e}")

        # J-code drug markup: the drug-spread essence (priced far above same-drug
        # peers) straight from the spending fact — no NDC slice, no NADAC needed.
        # Runs on the as-of file under a freeze, so it is point_in_time.
        try:
            spend_dm = asof_spend_p or _first_existing(processed, "spending_fact.parquet")
            if spend_dm:
                from src.analytics.drug_markup import drug_markup_from_parquet
                dm = drug_markup_from_parquet(str(spend_dm))
                if len(dm):
                    adapter_frames["drug_markup"] = dm
                    audit(f"    [drug_markup] {len(dm):,} J-code billers scored "
                          f"vs same-drug peers (from {Path(spend_dm).name})")
                else:
                    audit("    [drug_markup] skipped: no J-code rows (or no "
                          "total_claim_lines column) in the spending fact")
            else:
                audit("    [drug_markup] skipped: needs processed/spending_fact.parquet")
        except Exception as e:
            audit(f"    [drug_markup] skipped: {e}")
        case_lbls = None
        if args.case_db and org_nodes is not None:
            from .case_labels import build_case_labels
            cdb = (pd.read_csv(args.case_db, dtype=str) if args.case_db.endswith(".csv")
                   else pd.read_parquet(args.case_db))
            # frozen runs box case positives to conduct that STARTED pre-cutoff
            case_lbls = build_case_labels(cdb, org_nodes, npi_to_org,
                                          asof_cutoff=args.asof_cutoff)
            audit(f"    [doj_case] {len(case_lbls):,} NPIs labeled from "
                  f"{Path(args.case_db).name} (scheme-typed + conduct windows"
                  + (f", boxed to <= {str(args.asof_cutoff)[:4]}" if args.asof_cutoff
                     else "") + ")")

        # external grounding (address) + temporal-graph velocity + billing LM
        pdim_p = _first_existing(processed, "provider_dim.parquet")
        if "address" in skip:
            audit("    [address] skipped: --skip-sources")
        elif pdim_p:
            from .address_grounding import address_flags, load_cmra_reference
            geocoder = None
            if args.geocode:
                from src.feeds.geocode import census_geocoder
                geocoder = census_geocoder()
                print("    [address] live-geocoding via Census (network) …")
            # optional USPS CMRA registry for an exact-match flag
            cmra_set = None
            cmra_p = _first_existing(preclean / "usps", "cmra.csv", "*.csv")
            if cmra_p:
                cmra_set = load_cmra_reference(_read_any(cmra_p))
                print(f"    [address] loaded {len(cmra_set):,} USPS CMRA addresses")
            # attach org_node_id so the cluster-degree (distinct orgs per address) fires
            pdim = pd.read_parquet(pdim_p)
            pdim["npi"] = pdim["npi"].astype(str)
            pdim = pdim.merge(npi_to_org[["npi", "org_node_id"]].astype(str)
                              .drop_duplicates("npi"), on="npi", how="left")
            af = address_flags(pdim, geocoder=geocoder, cmra_addresses=cmra_set)
            if len(af):
                adapter_frames["address"] = af
                _clu = int(af["addr_cluster_degree"].sum()) if "addr_cluster_degree" in af else 0
                audit(f"    [address] {int(af['addr_is_mailbox'].sum()):,} mailbox/PO-box "
                      f"addresses, {int(af['addr_shared'].sum()):,} shared-address providers, "
                      f"{_clu:,} shell-cluster addresses (from {Path(pdim_p).name})")
        else:
            audit("    [address] skipped: needs processed/provider_dim.parquet")
        try:
            from src.entity_graph.graph_velocity import velocity_from_snapshots
            vel = velocity_from_snapshots(root / "feature_snapshots")
            if len(vel):
                adapter_frames["graph_velocity"] = vel
                audit(f"    [graph_velocity] {len(vel):,} providers (snapshot diff)")
            else:
                audit("    [graph_velocity] skipped: needs ≥2 feature snapshots "
                      "(make feature-snapshot on a cadence)")
        except Exception as e:
            audit(f"    [graph_velocity] skipped: {e}")
        if args.with_analytics and pdim_p:
            spend_p = asof_spend_p or _first_existing(processed, "spending_fact.parquet")
            if spend_p:
                try:
                    adapter_frames["billing_lm"] = _billing_lm_from_parquet(
                        spend_p, pd.read_parquet(pdim_p), audit)
                except Exception as e:
                    audit(f"    [billing_lm] skipped: {e}")
            else:
                audit("    [billing_lm] skipped: no processed/spending_fact.parquet")
        elif not args.with_analytics:
            audit("    [billing_lm] skipped: pass --with-analytics")

    out_dir = Path(args.out or (root / "model_a" / "provider_features"))
    # substrate provenance from the graph build (frozen address layer or not)
    graph_substrate = None
    try:
        gb = g / "graph_build_info.json"
        if gb.exists():
            graph_substrate = json.loads(gb.read_text(encoding="utf-8"))
            if graph_substrate.get("address_layer_frozen"):
                print(f"  [graph] substrate FROZEN (NPPES edition "
                      f"{graph_substrate.get('asof_nppes_edition')}) — "
                      "entity_graph features classed point_in_time")
            elif args.asof_cutoff:
                print("  [graph] WARNING: frozen run on an UNFROZEN address "
                      "substrate — shell_score carries the co-location leak. "
                      "Rebuild the graph with --asof-nppes to close it.")
    except Exception as e:
        print(f"  [graph] build-info unreadable ({e}); substrate assumed unfrozen")
    matrix, manifest = build_provider_matrix(
        leads, npi_to_org, org_graph_features=gf,
        adapter_npi_frames=adapter_frames, org_grain_frames=org_grain,
        nucc_peer_groups=nucc_pg, widened_label=widened, case_labels=case_lbls,
        graph_substrate=graph_substrate)

    # Consolidated source audit: the single table of what each source DID this run
    # (used/skipped + reason + file), so a silent skip is impossible to miss.
    manifest["sources_audit"] = audit.records()

    # §E: bake provider name / city / zip into the export natively (kills the
    # post-hoc make_scored_parquet join). IDENTIFIERS, never features — they land
    # in manifest identifier_cols so training code drops them by contract.
    try:
        pdim_p = processed / "provider_dim.parquet"
        if pdim_p.exists():
            import duckdb as _ddb
            avail = _ddb.connect().execute(
                f"SELECT * FROM read_parquet('{pdim_p}') LIMIT 0").df().columns
            want = [c for c in ("provider_name", "first_name", "last_name",
                                "addr_city", "addr_zip") if c in avail]
            new_ids = [c for c in want if c not in matrix.columns]
            if "npi" in avail and new_ids:
                pdim = _ddb.connect().execute(
                    f"SELECT npi, {', '.join(new_ids)} FROM read_parquet('{pdim_p}')"
                ).df()
                pdim["npi"] = pdim["npi"].astype(str)
                n_m = len(matrix)
                matrix = matrix.merge(pdim.drop_duplicates("npi"), on="npi", how="left")
                assert len(matrix) == n_m, "provider_dim identifier join fanned out"
                manifest["identifier_cols"] = list(manifest.get("identifier_cols", [])
                                                   ) + new_ids
                print(f"[identifiers] baked {', '.join(new_ids)} in natively")
    except Exception as e:
        print(f"[identifiers] provider_dim enrichment skipped: {e}")

    out_dir.mkdir(parents=True, exist_ok=True)
    matrix.to_parquet(out_dir / "provider_features_for_model.parquet", index=False)
    (out_dir / "feature_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    _write_dictionary(matrix, manifest, out_dir)
    _write_report(matrix, manifest, out_dir)
    _write_sources_report(manifest["sources_audit"], out_dir)
    # per-scheme calc integrity + run-to-run regression alarm (the silent-
    # degradation feedback loop): compares against the LAST run's baseline in
    # this out_dir, so a source that quietly stopped feeding a scheme is caught
    # before the matrix ships.
    try:
        from .scheme_health import write_health
        sh = write_health(matrix, manifest, out_dir)
        msg = (f"  [scheme_health] {sh['n_broken']} BROKEN / {sh['n_degraded']} "
               f"DEGRADED → {out_dir / 'SCHEME_HEALTH.md'}")
        if sh["regressions"]:
            msg += (f"\n  *** {len(sh['regressions'])} SCHEME REGRESSION(S) vs the "
                    "last run — a source may have silently dropped. Check "
                    "SCHEME_HEALTH.md before trusting this matrix. ***")
        print(msg)
    except Exception as e:                                    # reporter, not a gate
        print(f"  [scheme_health] skipped: {e}")
    # scheme-vs-proven-cases validation: the ruler that fits the billing schemes
    if case_lbls is not None and len(case_lbls):
        try:
            from .case_validation import validate_schemes, to_markdown as _cv_md
            res = validate_schemes(matrix, case_lbls)
            n_case = int(matrix["npi"].astype(str).isin(
                set(case_lbls["npi"].astype(str))).sum())
            (out_dir / "CASE_VALIDATION.md").write_text(
                _cv_md(res, n_case), encoding="utf-8")
            val = res[res["verdict"] == "VALIDATED"]["scheme"].tolist() if len(res) else []
            print(f"  [case_validation] {n_case:,} DOJ-case NPIs; validated "
                  f"schemes: {', '.join(val) if val else 'none yet'} "
                  f"→ {out_dir / 'CASE_VALIDATION.md'}")
        except Exception as e:                                # reporter, not a gate
            print(f"  [case_validation] skipped: {e}")
    # the standing calc-integrity feedback loop: file findings, never kill the run
    try:
        from .expectations import run_expectations, write_report as _write_exp
        findings = run_expectations(matrix, manifest)
        _write_exp(findings, out_dir / "EXPECTATIONS_REPORT.md",
                   n_checked=len(manifest.get("raw_feature_cols", []))
                   + len(manifest.get("peerpct_cols", []))
                   + len(manifest.get("subscore_cols", [])))
        manifest["expectations"] = {
            "fails": int((findings["severity"] == "FAIL").sum()) if len(findings) else 0,
            "warns": int((findings["severity"] == "WARN").sum()) if len(findings) else 0,
        }
        # manifest json was already written above — refresh it with the summary
        (out_dir / "feature_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"[expectations] {manifest['expectations']['fails']} FAIL / "
              f"{manifest['expectations']['warns']} WARN "
              f"→ {out_dir / 'EXPECTATIONS_REPORT.md'}")
    except Exception as e:                                    # reporter, not a gate
        print(f"[expectations] reporter failed (run unaffected): {e}")
    used = sum(1 for r in manifest["sources_audit"] if r["status"] == "used")
    skipped = sum(1 for r in manifest["sources_audit"] if r["status"] == "skipped")
    print(f"  sources: {used} used / {skipped} skipped "
          f"→ {out_dir / 'SOURCES_REPORT.md'}")
    print(f"Wrote {out_dir}/provider_features_for_model.parquet "
          f"— {manifest['n_providers']:,} providers × {matrix.shape[1]} columns")
    print(f"  schemes scored: {', '.join(sorted(manifest['scheme_coverage']))}")
    if manifest["label"]:
        pos = int(pd.to_numeric(matrix[manifest["label"]], errors="coerce").fillna(0).sum())
        print(f"  PU positives ({manifest['label']}): {pos:,}")
    if "confirmed_clean" in matrix.columns:
        print(f"  confirmed-clean negatives: "
              f"{int(matrix['confirmed_clean'].sum()):,}")

    # Matched case-control set: each positive vs. comparable clean controls — the
    # contrast that lets the model learn what DIFFERS holding confounders fixed.
    if args.case_control and manifest["label"]:
        from .case_control import match_cohorts
        matched = match_cohorts(matrix, label_col=manifest["label"])
        if len(matched):
            matched.to_parquet(out_dir / "provider_features_matched.parquet", index=False)
            n_case = int((matched["cohort"] == "case").sum())
            n_ctrl = int((matched["cohort"] == "control").sum())
            kind = matched["control_kind"].iloc[0]
            print(f"  matched case-control: {n_case:,} cases + {n_ctrl:,} controls "
                  f"({kind}) → provider_features_matched.parquet")
        else:
            print("  case-control: no positives to match (skipped)")

    # Point-in-time: archive a valid-time-stamped snapshot so the bitemporal store
    # accumulates history for as-of (out-of-time) training (Pillar 1).
    if args.snapshot:
        import datetime as _dt
        from .feature_store import snapshot_features, load_snapshot_index
        asof = args.asof or _dt.date.today().strftime("%Y-%m-%d")
        store = Path(args.snapshot_dir) if args.snapshot_dir else root / "feature_snapshots"
        snapshot_features(matrix, asof, store)
        print(f"  snapshotted as-of {asof} → {store} "
              f"({len(load_snapshot_index(store))} point-in-time snapshot(s))")


_DICT_NOTES = {
    LABEL_COL: "PU POSITIVE LABEL — provider appears on the OIG LEIE. Train as the target, not a feature.",
    "billed_after_exclusion": "LEAKAGE (hard) — derived from the provider's own exclusion; do not train on it.",
    "excluded_after_billing": "LEAKAGE (hard) — derived from the provider's own exclusion; do not train on it.",
    "within_2_hops_of_exclusion": "Exclusion-proximity (leakage-adjacent): predictive but correlated with the label; use under a strict out-of-time split.",
}


def _write_dictionary(matrix: pd.DataFrame, manifest: dict, out_dir: Path) -> None:
    lines = ["# PROVIDER_FEATURES — data dictionary\n",
             "_One row per NPI. `*__peerpct` = one-sided taxonomy-peer percentile of "
             "the raw column (higher = more than peers). `subscore_<scheme>` = the "
             "0–1 scheme score from the rules engine. NULL means the provider is "
             "absent from that source (LightGBM handles it natively), NOT zero._\n\n",
             "| column | group | non-null | notes |\n|---|---|--:|---|\n"]

    def group_of(c: str) -> str:
        if c == manifest["label"]:
            return "LABEL"
        if c in manifest["leakage_hard"]:
            return "leakage_hard"
        if c in manifest["leakage_adjacent"]:
            return "leakage_adjacent"
        if c in manifest["identifier_cols"]:
            return "id"
        if c.startswith("subscore_"):
            return "subscore"
        if c.endswith("__peerpct"):
            return "peer_percentile"
        if c in manifest["raw_feature_cols"]:
            return "raw_feature"
        return "other"

    for c in matrix.columns:
        nn = int(matrix[c].notna().sum())
        lines.append(f"| `{c}` | {group_of(c)} | {nn:,} | {_DICT_NOTES.get(c, '')} |\n")
    (out_dir / "PROVIDER_FEATURES_DICTIONARY.md").write_text(
        "".join(lines), encoding="utf-8")


def _write_report(matrix: pd.DataFrame, manifest: dict, out_dir: Path) -> None:
    lines = ["# PROVIDER_FEATURES_EXPORT — report\n",
             f"_Per-NPI training matrix for the supervised model. "
             f"{manifest['n_providers']:,} providers × {matrix.shape[1]} columns._\n\n",
             "## Sources contributing features\n"]
    if manifest["sources_used"]:
        for src, cols in manifest["sources_used"].items():
            lines.append(f"- **{src}**: {', '.join(cols)}\n")
    else:
        lines.append("- (only the base v3 concepts — no adapter files present)\n")
    lines.append("\n## Scheme subscores produced (feature coverage)\n")
    for scheme, feats in sorted(manifest["scheme_coverage"].items()):
        lines.append(f"- `subscore_{scheme}` ← {', '.join(feats)}\n")
    lines.append("\n## Label & leakage (read before training)\n")
    lines.append(f"- **label**: `{manifest['label']}` (PU positive)\n")
    lines.append(f"- **leakage_hard** (never train on these): "
                 f"{', '.join('`'+c+'`' for c in manifest['leakage_hard']) or 'none'}\n")
    lines.append(f"- **leakage_adjacent** (exclusion-proximity; out-of-time split): "
                 f"{', '.join('`'+c+'`' for c in manifest['leakage_adjacent']) or 'none'}\n")
    lines.append("\n## Subscore coverage (non-null share)\n")
    for c in manifest["subscore_cols"]:
        share = float(matrix[c].notna().mean()) if c in matrix.columns else 0.0
        lines.append(f"- `{c}`: {share:.1%}\n")
    (out_dir / "PROVIDER_FEATURES_EXPORT_REPORT.md").write_text(
        "".join(lines), encoding="utf-8")


def _write_sources_report(audit: list[dict], out_dir: Path) -> None:
    """One glance: every source this run touched, used vs. skipped, with the reason /
    file. Built from the captured ``[source] …`` progress lines (see _SourceAudit) so
    a silently-dropped source — the year-suffixed Part-B/D skip class of bug — is now
    impossible to miss without grepping the console."""
    used = [r for r in audit if r["status"] == "used"]
    skipped = [r for r in audit if r["status"] == "skipped"]
    lines = ["# SOURCES_REPORT — what each source did this run\n\n",
             f"_{len(used)} source(s) contributed features; {len(skipped)} skipped. "
             "A skip is usually a missing/un-procured file, not an error — the column "
             "lights up on the next run once the file lands (skip-missing design)._\n\n",
             "| source | status | detail (rows / columns / file, or skip reason) |\n",
             "|---|---|---|\n"]
    if not audit:
        lines.append("| _(none recorded)_ | — | run against real data to populate |\n")
    for r in used + skipped:
        badge = "✅ used" if r["status"] == "used" else "⏭️ skipped"
        detail = (r["detail"] or "").replace("|", "\\|")
        lines.append(f"| `{r['source']}` | {badge} | {detail} |\n")
    (out_dir / "SOURCES_REPORT.md").write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
