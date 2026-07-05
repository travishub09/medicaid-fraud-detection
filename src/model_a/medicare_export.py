"""
medicare_export.py — the Medicare-grain training matrix (Run 2 §B, the runner).

Assembles ``provider_features_medicare.parquet`` from the Medicare-side pieces
the way the Medicaid export assembles its matrix — same column families, same
manifest keys, same NULL semantics — so the modeler trains it in parallel and
merges later if it wins:

  base        medicare_provider_stats (per-NPI annual fact rollup)
  growth      medicare_growth features (multi-vintage ramp — the mc_* family)
  adapters    the per-NPI Medicare adapters (Part B upcoding, Part D drugs,
              opioid, Open Payments) — the SAME frames the Medicaid export uses
  identifiers provider_dim (taxonomy / entity / state / names)
  peerpct     one-sided taxonomy-peer percentiles via the shared peer engine
  subscores   the scheme registry (skip-missing, NULL-aware)
  label       the widened multi-source exclusion label from the graph
  reports     feature_manifest_medicare.json + EXPECTATIONS_REPORT (the same
              calc-integrity loop grades this matrix too)

This is where the physician schemes stop being footnotes: upcoding, kickbacks
and pill-mill under-cover the org-heavy Medicaid label but fit the Medicare
population (§2.5 of the results write-up).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .provider_features_export import (
    ADAPTER_FEATURE_COLS, _one_sided_peer_pct, _run_npi_adapters,
    _widened_label_from_graph)
from .scheme_subscores import compute_subscores

GROWTH_COLS = ["mc_yoy_growth", "mc_max_yoy_growth", "mc_growth_cagr",
               "mc_new_code_share", "mc_entered_recently"]
STAT_COLS = ["years_active", "first_year", "last_year", "total_units",
             "total_dollars", "n_distinct_codes"]
ID_COLS = ["npi", "primary_taxonomy", "entity_type", "practice_state",
           "provider_name", "org_legal_name"]


def assemble_medicare_matrix(stats: pd.DataFrame,
                             growth: pd.DataFrame | None,
                             adapter_frames: dict[str, pd.DataFrame],
                             provider_dim: pd.DataFrame | None,
                             widened_label: pd.DataFrame | None,
                             min_peer: int = 30) -> tuple[pd.DataFrame, dict]:
    """Pure assembly (testable without files). One row per NPI, asserted."""
    m = stats.copy()
    m["npi"] = m["npi"].astype(str)
    assert m["npi"].is_unique, "medicare stats must be one row per NPI"
    n0 = len(m)

    if growth is not None and len(growth):
        g = growth.copy()
        g["npi"] = g["npi"].astype(str)
        m = m.merge(g.drop_duplicates("npi"), on="npi", how="left")
        assert len(m) == n0, "growth join fanned out"

    if provider_dim is not None and len(provider_dim):
        pd_cols = {"taxonomy_code": "primary_taxonomy",
                   "entity_type": "entity_type",
                   "addr_state": "practice_state",
                   "provider_name": "provider_name",
                   "org_name": "org_legal_name"}
        take = [c for c in pd_cols if c in provider_dim.columns]
        pdim = provider_dim[["npi"] + take].copy()
        pdim["npi"] = pdim["npi"].astype(str)
        pdim = pdim.rename(columns={c: pd_cols[c] for c in take})
        m = m.merge(pdim.drop_duplicates("npi"), on="npi", how="left")
        assert len(m) == n0, "provider_dim join fanned out"

    sources_used: dict[str, list[str]] = {}
    for name, fr in (adapter_frames or {}).items():
        if fr is None or not len(fr) or "npi" not in fr.columns:
            continue
        f = fr.copy()
        f["npi"] = f["npi"].astype(str)
        new_cols = [c for c in f.columns if c != "npi" and c not in m.columns]
        if not new_cols:
            continue
        m = m.merge(f[["npi"] + new_cols].drop_duplicates("npi"), on="npi", how="left")
        assert len(m) == n0, f"medicare adapter '{name}' join fanned out"
        sources_used[name] = new_cols

    adapter_present = [c for c in ADAPTER_FEATURE_COLS if c in m.columns]
    peerpct = _one_sided_peer_pct(m, adapter_present, min_peer=min_peer)
    adapter_present = [c for c in adapter_present if c in peerpct.columns]
    peerpct = peerpct.rename(columns={c: f"{c}__peerpct" for c in peerpct.columns})

    subin = pd.DataFrame(index=m.index)
    for c in adapter_present:
        subin[c] = peerpct[f"{c}__peerpct"]
    subscores, _ = compute_subscores(subin)

    out = pd.concat([m, peerpct, subscores], axis=1)
    out = out.loc[:, ~out.columns.duplicated()]
    assert len(out) == n0, "medicare matrix row count changed during assembly"

    if widened_label is not None and len(widened_label):
        wl = widened_label.copy()
        wl["npi"] = wl["npi"].astype(str)
        cols = [c for c in ("npi", "provider_on_exclusion", "exclusion_label_sources")
                if c in wl.columns]
        out = out.merge(wl[cols].drop_duplicates("npi"), on="npi", how="left")
        assert len(out) == n0, "widened-label join fanned out"
        out["provider_on_exclusion"] = (pd.to_numeric(
            out["provider_on_exclusion"], errors="coerce").fillna(0).astype(int))
    else:
        out["provider_on_exclusion"] = 0

    label = "provider_on_exclusion"
    raw = sorted(set(c for c in GROWTH_COLS + STAT_COLS + adapter_present
                     if c in out.columns))
    manifest = {
        "grain": "npi",
        "theater": "medicare",
        "n_providers": int(n0),
        "n_positives": int(out[label].sum()),
        "label": label,
        "label_metadata": [c for c in ["exclusion_label_sources"] if c in out.columns],
        "leakage_hard": [label],
        "leakage_adjacent": [],
        "identifier_cols": [c for c in ID_COLS if c in out.columns],
        "raw_feature_cols": raw,
        "peerpct_cols": sorted(c for c in out.columns if c.endswith("__peerpct")),
        "subscore_cols": sorted(c for c in out.columns if c.startswith("subscore_")),
        "group_cols": [],
        "sources_used": sources_used,
    }
    return out, manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--medicare-dir", required=True,
                    help="dir with medicare_provider_stats.parquet (+ optional "
                         "medicare_growth.parquet) from medicare_fact/growth")
    ap.add_argument("--preclean", required=True,
                    help="raw source root (the per-NPI Medicare adapters run here)")
    ap.add_argument("--provider-dim", default=None,
                    help="processed/provider_dim.parquet (identifiers + peer keys)")
    ap.add_argument("--graph-dir", default=None,
                    help="entity-graph dir (widened exclusion label)")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    md = Path(args.medicare_dir)
    stats = pd.read_parquet(md / "medicare_provider_stats.parquet")
    gp = md / "medicare_growth.parquet"
    growth = pd.read_parquet(gp) if gp.exists() else None
    adapter_frames = _run_npi_adapters(Path(args.preclean), print)
    pdim = pd.read_parquet(args.provider_dim) if args.provider_dim else None
    widened = (_widened_label_from_graph(Path(args.graph_dir), print)
               if args.graph_dir else None)

    matrix, manifest = assemble_medicare_matrix(stats, growth, adapter_frames,
                                                pdim, widened)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    matrix.to_parquet(out_dir / "provider_features_medicare.parquet", index=False)
    (out_dir / "feature_manifest_medicare.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    try:
        from .expectations import run_expectations, write_report
        findings = run_expectations(matrix, manifest)
        write_report(findings, out_dir / "EXPECTATIONS_REPORT_MEDICARE.md")
    except Exception as e:                                    # reporter, not a gate
        print(f"[expectations] reporter failed (run unaffected): {e}")
    print(f"wrote {out_dir / 'provider_features_medicare.parquet'} "
          f"({len(matrix):,} NPIs × {matrix.shape[1]} cols, "
          f"{manifest['n_positives']:,} positives)")


if __name__ == "__main__":
    main()
