"""
medicare_growth.py — multi-year Medicare growth/ramp features (Run 2 §B).

The Medicaid growth module works on MONTHS; Medicare PUFs are ANNUAL, so the
fly-by-night signature is read across vintages instead: how fast did paid
dollars ramp year over year, and did the code mix pivot? Inputs come from
``medicare_fact.build_fact`` (npi · code · year · units · dollars).

Per-NPI features (raw; one-sided — only excess is suspicious; percentile them
through the peer engine downstream):

  mc_yoy_growth        latest-year dollars / prior-year dollars − 1 (NaN when
                       the provider lacks a prior year — young providers are
                       UNSCORED here, never force-scored; entry itself is
                       carried by mc_entered_recently)
  mc_max_yoy_growth    the largest single year-over-year jump in the series
                       (a spike three years ago still counts)
  mc_growth_cagr       (last/first)^(1/years) − 1 over the observed span
                       (≥2 distinct years, else NaN)
  mc_new_code_share    share of the LATEST year's distinct codes never billed
                       in any prior year — requires ≥2 prior YEARS of history
                       (the pre-window lesson from the Medicaid burst signal:
                       with less history most codes are "new" by arithmetic
                       and the signal collapses into tenure)
  mc_entered_recently  1 if the provider's first observed year is the latest
                       vintage (new entrant — context flag, not a growth rate)

All ratios NULLIF-guarded; a provider with zero prior-year dollars gets NaN
growth (0 → X is entry, not growth — mc_entered_recently carries it).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

MIN_PRE_YEARS = 2      # history required BEFORE the latest year for new-code share


def growth_features(fact: pd.DataFrame) -> pd.DataFrame:
    """Per-NPI multi-year growth features from the medicare fact."""
    f = fact.copy()
    f["npi"] = f["npi"].astype(str)
    f["year"] = pd.to_numeric(f["year"], errors="coerce").astype(int)
    f["dollars"] = pd.to_numeric(f["dollars"], errors="coerce").fillna(0.0)

    yearly = f.groupby(["npi", "year"], as_index=False)["dollars"].sum()
    latest = int(yearly["year"].max())

    rows = []
    for npi, grp in yearly.groupby("npi"):
        s = grp.set_index("year")["dollars"].sort_index()
        years = s.index.to_numpy()
        first, last = int(years.min()), int(years.max())
        # consecutive-year ratios only (a gap year is absence, not 0 growth)
        yoy_vals = []
        for y0, y1 in zip(years[:-1], years[1:]):
            if y1 == y0 + 1 and s[y0] > 0:
                yoy_vals.append(s[y1] / s[y0] - 1.0)
        yoy_latest = (s[latest] / s[latest - 1] - 1.0
                      if latest in s.index and (latest - 1) in s.index
                      and s[latest - 1] > 0 else np.nan)
        n_years = len(years)
        cagr = ((s[last] / s[first]) ** (1.0 / (last - first)) - 1.0
                if n_years >= 2 and last > first and s[first] > 0 else np.nan)
        rows.append({
            "npi": npi,
            "mc_yoy_growth": yoy_latest,
            "mc_max_yoy_growth": max(yoy_vals) if yoy_vals else np.nan,
            "mc_growth_cagr": cagr,
            "mc_entered_recently": int(first == latest),
        })
    out = pd.DataFrame(rows)

    # new-code share in the latest year, guarded by pre-window history
    codes = f[["npi", "year", "code"]].drop_duplicates()
    latest_codes = codes[codes["year"] == latest].groupby("npi")["code"].agg(set)
    prior = codes[codes["year"] < latest]
    prior_codes = prior.groupby("npi")["code"].agg(set)
    prior_years = prior.groupby("npi")["year"].nunique()

    def _new_share(npi: str) -> float:
        cur = latest_codes.get(npi)
        if cur is None or prior_years.get(npi, 0) < MIN_PRE_YEARS:
            return np.nan                      # young provider: unscored
        old = prior_codes.get(npi, set())
        return len(cur - old) / len(cur) if cur else np.nan

    out["mc_new_code_share"] = out["npi"].map(_new_share)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fact", required=True, help="medicare_fact.parquet")
    ap.add_argument("--out", required=True, help="medicare_growth.parquet")
    args = ap.parse_args()
    fact = pd.read_parquet(args.fact)
    out = growth_features(fact)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(args.out, index=False)
    covered = int(out["mc_yoy_growth"].notna().sum())
    print(f"wrote {args.out} — {len(out):,} NPIs, {covered:,} with a scored "
          f"latest-year growth (the rest lack a prior year: unscored, not zero)")


if __name__ == "__main__":
    main()
