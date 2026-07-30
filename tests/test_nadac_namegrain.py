"""NADAC name-grain brand-premium: the lawful no-NDC-claims path."""

import pandas as pd

from src.ingest_cms.nadac import (_name_key, brand_premium_by_prescriber,
                                  name_price_index)


def _nadac_raw():
    return pd.DataFrame({
        "NDC": ["00071015523", "00093505698", "00002143380", "55111011111"],
        "NDC Description": ["LIPITOR 10 MG TABLET",
                            "ATORVASTATIN CALCIUM 10 MG TABLET",
                            "HUMALOG 100 UNIT/ML VIAL",
                            "ATORVASTATIN CALCIUM 20 MG TABLET"],
        "NADAC Per Unit": ["5.00", "0.50", "30.00", "0.70"],
        "Classification for Rate Setting": ["B", "G", "B", "G"],
    })


def test_name_key_strips_strength():
    s = pd.Series(["ATORVASTATIN CALCIUM 10 MG TABLET", "LIPITOR 10 MG",
                   "INSULIN GLARGINE 100/ML"])
    assert _name_key(s).tolist() == ["ATORVASTATIN CALCIUM", "LIPITOR",
                                     "INSULIN GLARGINE"]


def test_price_index_medians_by_class():
    idx = name_price_index(_nadac_raw()).set_index("name_key")
    # generic atorvastatin median of 0.50/0.70; no brand row under that name
    assert abs(idx.loc["ATORVASTATIN CALCIUM", "generic_per_unit"] - 0.6) < 1e-9
    assert pd.isna(idx.loc["ATORVASTATIN CALCIUM", "brand_per_unit"])
    assert idx.loc["LIPITOR", "brand_per_unit"] == 5.0


def test_brand_premium_share():
    # hand-built index where the GENERIC name has both prices: brand 5, gen 0.5
    idx = pd.DataFrame({"name_key": ["ATORVASTATIN CALCIUM"],
                        "generic_per_unit": [0.5], "brand_per_unit": [5.0]})
    partd = pd.DataFrame({
        "Prscrbr_NPI": ["1234567893", "1234567893", "1992708770"],
        "Brnd_Name": ["LIPITOR", "ATORVASTATIN CALCIUM", "LIPITOR"],
        "Gnrc_Name": ["ATORVASTATIN CALCIUM", "ATORVASTATIN CALCIUM",
                      "ATORVASTATIN CALCIUM"],
        "Tot_Clms": [10, 10, 10],
        "Tot_Drug_Cst": [1000.0, 1000.0, 500.0],
    })
    out = brand_premium_by_prescriber(partd, idx).set_index("npi")
    # NPI 1: $1000 brand with 90% savings frac -> 900 avoidable of 2000 = .45
    assert abs(out.loc["1234567893", "nadac_brand_premium_share"] - 0.45) < 1e-9
    # generic row contributes matched dollars but zero avoidable
    assert out.loc["1234567893", "nadac_matched_share"] == 1.0
    # NPI 2: all brand -> 90% of dollars avoidable
    assert abs(out.loc["1992708770", "nadac_brand_premium_share"] - 0.9) < 1e-9


def test_one_sided_generic_prescriber_scores_zero():
    idx = pd.DataFrame({"name_key": ["ATORVASTATIN CALCIUM"],
                        "generic_per_unit": [0.5], "brand_per_unit": [5.0]})
    partd = pd.DataFrame({
        "Prscrbr_NPI": ["1234567893"],
        "Brnd_Name": ["ATORVASTATIN CALCIUM"],
        "Gnrc_Name": ["ATORVASTATIN CALCIUM"],
        "Tot_Clms": [10], "Tot_Drug_Cst": [100.0],
    })
    out = brand_premium_by_prescriber(partd, idx)
    assert out["nadac_brand_premium_share"].iloc[0] == 0.0
