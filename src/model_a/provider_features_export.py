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
                "provider_on_leie"]

# Exclusion-PROXIMITY features: predictive (rings get caught together) but
# correlated with the label — use only under a strict out-of-time split.
LEAKAGE_ADJACENT = ["within_2_hops_of_exclusion", "shell_score",
                    "related_party_density", "related_party_density_norm",
                    "subscore_ownership_integrity"]

# Provider stats worth carrying as plain features (whatever the base leads has).
PROVIDER_STATS = ["gross_paid", "net_paid", "service_volume", "total_claim_lines",
                  "n_distinct_hcpcs", "tenure_months", "n_active_months"]

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
    from src.analytics.peers import assign_peer_groups, one_sided_percentiles
    present = [c for c in cols if c in df.columns]
    if not present:
        return pd.DataFrame(index=df.index)
    work = df.copy()
    # peers.assign_peer_groups expects taxonomy_code / entity_type / state
    work["taxonomy_code"] = work.get("primary_taxonomy", pd.Series("", index=work.index))
    work["state"] = work.get("practice_state", pd.Series("", index=work.index))
    if "entity_type" not in work.columns:
        work["entity_type"] = ""
    assigned = assign_peer_groups(work, min_peer=min_peer)
    pct = one_sided_percentiles(assigned, present)
    return pct[[c for c in present if c in pct.columns]]


def build_provider_matrix(leads: pd.DataFrame, npi_to_org: pd.DataFrame,
                          org_graph_features: pd.DataFrame | None = None,
                          adapter_npi_frames: dict[str, pd.DataFrame] | None = None,
                          org_grain_frames: dict[str, pd.DataFrame] | None = None,
                          min_peer: int = 30,
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

    sources_used: dict[str, list[str]] = {}

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

    # --- peer-normalize the raw adapter metrics (one-sided taxonomy percentile) ---
    adapter_present = [c for c in ADAPTER_FEATURE_COLS if c in m.columns]
    peerpct = _one_sided_peer_pct(m, adapter_present, min_peer=min_peer)
    adapter_present = [c for c in adapter_present if c in peerpct.columns]
    peerpct = peerpct.rename(columns={c: f"{c}__peerpct" for c in peerpct.columns})

    # --- subscore inputs: concepts + graph pass through; adapters use peer pct ---
    subin = pd.DataFrame(index=m.index)
    for c in V3_CONCEPTS + GRAPH_FEATURES:
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

    raw_feature_cols = sorted(
        [c for c in V3_CONCEPTS + GRAPH_FEATURES + adapter_present + PROVIDER_STATS
         if c in out.columns and c not in LEAKAGE_HARD])
    subscore_cols = [c for c in out.columns if c.startswith("subscore_")]
    peerpct_cols = [c for c in out.columns if c.endswith("__peerpct")]

    manifest = {
        "grain": "npi",
        "n_providers": int(n0),
        "label": LABEL_COL if LABEL_COL in out.columns else None,
        "leakage_hard": [c for c in LEAKAGE_HARD if c in out.columns],
        "leakage_adjacent": [c for c in LEAKAGE_ADJACENT if c in out.columns],
        "identifier_cols": [c for c in IDENTIFIER_COLS if c in out.columns],
        "raw_feature_cols": raw_feature_cols,
        "peerpct_cols": peerpct_cols,
        "subscore_cols": subscore_cols,
        "scheme_coverage": coverage,
        "sources_used": sources_used,
    }
    return out, manifest


# --------------------------------------------------------------------------- #
# CLI orchestration: discover source files under preclean/ and run each adapter
# --------------------------------------------------------------------------- #

def _read_any(path: Path) -> pd.DataFrame | None:
    """Read csv/parquet as all-string (IDs keep leading zeros — hard rule #1)."""
    if not path or not path.exists():
        return None
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype=str)


def _first_existing(base: Path, *names: str) -> Path | None:
    for n in names:
        p = base / n
        if p.exists():
            return p
    # also accept a directory containing a single csv/parquet
    for n in names:
        d = base / Path(n).stem
        if d.is_dir():
            hits = sorted(list(d.glob("*.parquet")) + list(d.glob("*.csv")))
            if hits:
                return hits[0]
    return None


def _run_npi_adapters(preclean: Path, log) -> dict[str, pd.DataFrame]:
    """Run the per-NPI CMS adapters against whatever raw files are present.

    Each entry is (source_name, subdir/filenames, callable(raw)->npi-keyed frame).
    Missing files are skipped silently — a source landing later just lights up its
    columns on the next run (the skip-missing philosophy of the subscore engine).
    """
    frames: dict[str, pd.DataFrame] = {}

    def _try(name: str, finder, fn):
        try:
            p = finder()
            raw = _read_any(p) if p else None
            if raw is None or not len(raw):
                return
            res = fn(raw)
            df = res[0] if isinstance(res, tuple) else res
            if df is not None and len(df) and "npi" in df.columns:
                frames[name] = df
                log(f"    [{name}] {len(df):,} providers, "
                    f"cols: {', '.join(c for c in df.columns if c != 'npi')}")
        except Exception as e:               # one bad source must never sink the run
            log(f"    [{name}] skipped: {e}")

    pc = preclean
    from src.ingest_cms import partb, partd, dmepos, opioid, openpayments
    _try("partb", lambda: _first_existing(pc / "partb", "partb.csv", "*.csv"),
         lambda r: partb.compute_partb_metrics(r))
    _try("partd", lambda: _first_existing(pc / "partd", "partd.csv", "*.csv"),
         lambda r: partd.compute_partd_metrics(r))
    _try("dmepos", lambda: _first_existing(pc / "dmepos", "dmepos.csv", "*.csv"),
         lambda r: dmepos.compute_dmepos_metrics(r))
    _try("opioid", lambda: _first_existing(pc / "opioid", "opioid.csv", "*.csv"),
         lambda r: opioid.compute_opioid_metrics(r))
    _try("open_payments",
         lambda: _first_existing(pc / "open_payments", "open_payments.csv", "*.csv"),
         lambda r: openpayments.compute_openpayments_metrics(r))
    return frames


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
    ap.add_argument("--out", default=None, help="output dir")
    ap.add_argument("--fixture", action="store_true",
                    help="build from the synthetic fixture (no real data)")
    args = ap.parse_args()

    root = Path(args.data_root or os.environ.get(
        "MEDICAID_DATA_ROOT", str(Path.home() / "Desktop" / "data")))

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
        org_grain = {}
    else:
        if not args.graph_dir or not args.leads:
            ap.error("--graph-dir and --leads are required (or use --fixture)")
        g = Path(args.graph_dir)
        npi_to_org = pd.read_parquet(g / "npi_to_org.parquet")
        gf = pd.read_parquet(g / "org_graph_features.parquet")
        leads = pd.read_parquet(args.leads)
        if "npi" not in leads.columns:
            ap.error(f"{args.leads} is not per-NPI (no 'npi' column)")
        preclean = Path(args.preclean) if args.preclean else root / "preclean"
        adapter_frames = _run_npi_adapters(preclean, print)
        org_grain = {}                      # org-grain adapters wired on Trey's box

    out_dir = Path(args.out or (root / "model_a" / "provider_features"))
    matrix, manifest = build_provider_matrix(
        leads, npi_to_org, org_graph_features=gf,
        adapter_npi_frames=adapter_frames, org_grain_frames=org_grain)

    out_dir.mkdir(parents=True, exist_ok=True)
    matrix.to_parquet(out_dir / "provider_features_for_model.parquet", index=False)
    (out_dir / "feature_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    _write_dictionary(matrix, manifest, out_dir)
    _write_report(matrix, manifest, out_dir)
    print(f"Wrote {out_dir}/provider_features_for_model.parquet "
          f"— {manifest['n_providers']:,} providers × {matrix.shape[1]} columns")
    print(f"  schemes scored: {', '.join(sorted(manifest['scheme_coverage']))}")
    if manifest["label"]:
        pos = int(pd.to_numeric(matrix[manifest["label"]], errors="coerce").fillna(0).sum())
        print(f"  PU positives ({manifest['label']}): {pos:,}")


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


if __name__ == "__main__":
    main()
