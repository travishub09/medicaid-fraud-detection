"""
nucc_taxonomy.py — NUCC Health Care Provider Taxonomy + CMS specialty crosswalk
(data-expansion sprint, docs/platform/16 §3).

Sources: NUCC "Provider Taxonomy" code set (nucc.org, free, twice/yr) and the
CMS "Medicare Provider and Supplier Taxonomy Crosswalk" (data.cms.gov). Together
they give an authoritative taxonomy hierarchy (~870 codes, 3 levels) and a map
between Medicare specialty codes (used in the claims files) and NUCC taxonomy
codes (used in NPPES). This FIXES PEER GROUPING: every one-sided robust-z signal
(hard rule #8) is only as good as its peer cohort, and a consistent
specialty<->taxonomy key cuts false positives from mis-grouped specialties. It
also makes ``model_a.sector_priors`` authoritative instead of prefix-coded.

  load_taxonomy_hierarchy    NUCC csv → taxonomy_code, grouping, classification,
                             specialization (the canonical 3-level tree).
  load_specialty_crosswalk   CMS csv → medicare_specialty_code <-> taxonomy_code.
  canonical_peer_group       given a provider dim (NPI, taxonomy and/or medicare
                             specialty), return one stable peer_group_key per
                             NPI — the grouping key the peer engine should use.

Pure reference data, no provider PII. Dormant until the two files land; see
docs/platform/16 §3 (this one is the highest-leverage, lowest-effort add).
"""

from __future__ import annotations

import pandas as pd

NUCC_COLS = {
    "taxonomy_code": ["Code", "code", "taxonomy_code"],
    "grouping": ["Grouping", "grouping"],
    "classification": ["Classification", "classification"],
    "specialization": ["Specialization", "specialization"],
}
CROSSWALK_COLS = {
    "medicare_specialty_code": ["MEDICARE SPECIALTY CODE",
                                "Medicare Specialty Code", "specialty_code"],
    "taxonomy_code": ["PROVIDER TAXONOMY CODE", "Provider Taxonomy Code",
                      "taxonomy_code"],
}


def _resolve(raw: pd.DataFrame, wanted: dict[str, list[str]]) -> dict[str, str]:
    return {k: next((c for c in opts if c in raw.columns), None)
            for k, opts in wanted.items()}


def load_taxonomy_hierarchy(raw: pd.DataFrame) -> pd.DataFrame:
    """INPUT: NUCC taxonomy csv. OUTPUT: taxonomy_code, grouping, classification,
    specialization (strings). docs/platform/16 §3."""
    cols = _resolve(raw, NUCC_COLS)
    if not cols["taxonomy_code"] or not cols["classification"]:
        raise ValueError(f"NUCC file missing Code/Classification; saw {list(raw.columns)[:8]}")
    def _col(k):
        return (raw[cols[k]].fillna("").astype(str).str.strip()
                if cols[k] else pd.Series("", index=raw.index))
    out = pd.DataFrame({
        "taxonomy_code": _col("taxonomy_code").str.upper(),
        "grouping": _col("grouping"),
        "classification": _col("classification"),
        "specialization": _col("specialization"),
    })
    return out[out["taxonomy_code"] != ""].drop_duplicates("taxonomy_code").reset_index(drop=True)


def load_specialty_crosswalk(raw: pd.DataFrame) -> pd.DataFrame:
    """INPUT: CMS specialty<->taxonomy crosswalk csv. OUTPUT: one row per
    (medicare_specialty_code, taxonomy_code) pair. docs/platform/16 §3."""
    cols = _resolve(raw, CROSSWALK_COLS)
    if not cols["taxonomy_code"]:
        raise ValueError(f"crosswalk missing taxonomy code; saw {list(raw.columns)[:8]}")
    out = pd.DataFrame({
        "medicare_specialty_code": (raw[cols["medicare_specialty_code"]].fillna("").astype(str).str.strip()
                                    if cols["medicare_specialty_code"] else ""),
        "taxonomy_code": raw[cols["taxonomy_code"]].fillna("").astype(str).str.strip().str.upper(),
    })
    return out[out["taxonomy_code"] != ""].drop_duplicates().reset_index(drop=True)


def canonical_peer_group(provider_dim: pd.DataFrame,
                         hierarchy: pd.DataFrame,
                         crosswalk: pd.DataFrame | None = None) -> pd.DataFrame:
    """INPUT: provider_dim (npi + taxonomy and/or medicare_specialty_code) plus
    the loaded hierarchy/crosswalk. OUTPUT: npi, peer_group_key (the canonical
    grouping the peer engine and sector_priors should consume), plus the
    nucc_grouping / nucc_classification levels for ladder fallback. docs/platform/16 §3.

    peer_group_key rolls the noisy ~870-code taxonomy up to its CLASSIFICATION —
    a clinically coherent cohort (all "Nurse Practitioner" specializations score
    against each other, not in 30 thin sub-cells) — falling back to grouping, then
    the raw code when the hierarchy doesn't cover it.
    """
    p = provider_dim.copy()
    p["npi"] = p["npi"].astype(str)
    tax = (p["taxonomy_code"].fillna("").astype(str).str.strip().str.upper()
           if "taxonomy_code" in p.columns else pd.Series("", index=p.index))
    # if provider_dim only carries a medicare specialty, map it to a taxonomy first
    if (tax == "").all() and crosswalk is not None and "medicare_specialty_code" in p.columns:
        sp2tax = dict(zip(crosswalk["medicare_specialty_code"], crosswalk["taxonomy_code"]))
        tax = p["medicare_specialty_code"].fillna("").astype(str).str.strip().map(sp2tax).fillna("")
    p["taxonomy_code"] = tax

    h = hierarchy.set_index("taxonomy_code")
    grouping = p["taxonomy_code"].map(h["grouping"]).fillna("")
    classification = p["taxonomy_code"].map(h["classification"]).fillna("")
    # most specific available coherent cohort, never empty
    key = classification.where(classification != "", grouping)
    key = key.where(key != "", p["taxonomy_code"]).where(p["taxonomy_code"] != "", "unknown")
    return pd.DataFrame({
        "npi": p["npi"],
        "peer_group_key": key,
        "nucc_grouping": grouping,
        "nucc_classification": classification,
    })


def _data_root_main() -> None:
    """CLI: build the canonical peer-group table from the two raw files."""
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--taxonomy", required=True, help="NUCC taxonomy csv")
    ap.add_argument("--crosswalk", default=None, help="CMS specialty crosswalk csv")
    ap.add_argument("--provider-dim", required=True, help="provider_dim parquet")
    ap.add_argument("--out", required=True, help="output peer_group parquet")
    args = ap.parse_args()
    hier = load_taxonomy_hierarchy(pd.read_csv(args.taxonomy, dtype=str))
    xw = load_specialty_crosswalk(pd.read_csv(args.crosswalk, dtype=str)) if args.crosswalk else None
    pdim = pd.read_parquet(args.provider_dim)
    pg = canonical_peer_group(pdim, hier, xw)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    pg.to_parquet(args.out, index=False)
    print(f"Wrote {args.out} — {len(pg):,} NPIs, "
          f"{pg['peer_group_key'].nunique():,} canonical peer groups "
          f"(vs {pdim['taxonomy_code'].nunique() if 'taxonomy_code' in pdim else '?'} raw taxonomies)")


if __name__ == "__main__":
    _data_root_main()
