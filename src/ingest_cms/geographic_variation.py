"""
geographic_variation.py — CMS Medicare Geographic Variation (data-expansion
sprint, docs/platform/16 §2).

Source: data.cms.gov "Medicare Geographic Variation - by National, State &
County". Free CSV, annual. County/state standardized per-capita Medicare spend
and utilization (already risk/price standardized by CMS). This is the REGIONAL
EXPECTED-SPEND DENOMINATOR Model A lacks: an org billing far above the
standardized per-capita norm for its county is more suspect than raw dollars
alone reveal — it plugs straight into the one-sided robust-z design (hard rule
#8) as a peer baseline.

  compute_geo_baseline       per-FIPS standardized_per_capita_cost (+ state
                             rollup) from the county file. FIPS are strings.
  attach_geo_expectation     join the county baseline to org-grain features via
                             the org's county (ZIP->county from
                             census_population.zip_to_county) → a
                             ``regional_cost_index`` driver feeding
                             specialty_mismatch / overutilization. State-grain
                             fallback until every org has a county.

Dormant until the file + the org->county attach land; see docs/platform/16 §2.
"""

from __future__ import annotations

import pandas as pd

GEO_VARIATION_COLS = {
    "fips": ["BENE_GEO_CD", "State and County FIPS Code", "FIPS", "fips"],
    "geo_level": ["BENE_GEO_LVL", "Geographic Level", "geo_level"],
    "state": ["BENE_GEO_DESC", "State", "state"],
    "std_per_capita": ["Tot_Mdcr_Stdzd_Pymt_PC", "Standardized Per Capita Costs",
                       "std_per_capita_cost"],
}


def compute_geo_baseline(raw: pd.DataFrame) -> pd.DataFrame:
    """INPUT CONTRACT: the Geographic Variation county file. OUTPUT: one row per
    FIPS with standardized_per_capita_cost + state. Keep only county rows
    (BENE_GEO_LVL == 'County'); FIPS as zero-padded strings. docs/platform/16 §2."""
    raise NotImplementedError(
        "data-expansion sprint stub — implement per docs/platform/16 §2 "
        "(mirror saturation.py's _resolve_columns + string-FIPS handling)")


def attach_geo_expectation(features: pd.DataFrame, org_nodes: pd.DataFrame,
                           geo_baseline: pd.DataFrame,
                           zip_to_county: pd.DataFrame | None = None
                           ) -> pd.DataFrame:
    """Attach ``regional_cost_index`` to org-grain features (no fan-out, asserted).
    Org county via zip_to_county on the org address ZIP; state-grain fallback
    otherwise. NaN where no county/region resolves. docs/platform/16 §2."""
    raise NotImplementedError(
        "data-expansion sprint stub — implement per docs/platform/16 §2")
