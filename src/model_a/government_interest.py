"""
government_interest.py — the OIG Work Plan / enforcement-theme overlay (A6).

The manifesto's sixth sub-score: alignment to "OIG Work Plan, DOJ enforcement
themes, CMS RADV focus, state MFCU activity." Encoded as a small curated table
(work-plan item → sector → weight) that multiplies into the sector prior —
"the government is already looking here" is exactly what predicts intervention,
so Model C reuses the same overlay as a case-viability input.

Provenance of the table: the items below are curated from the public OIG Work
Plan (oig.hhs.gov/reports-and-publications/workplan) — REFRESH QUARTERLY by
editing WORK_PLAN_ITEMS (or pass an external table to the functions / the
``--gov-interest`` CLI flag as a parquet with the same columns). Weights are
deliberately modest (≤1.3 each): this is a prior nudge, not a signal, and per
the explainability rule every applied multiplier carries the item titles as
named drivers.

Combination rule: the MAX matching weight per org, never a product — three
audit topics on one sector is one fact ("the government is watching this
sector"), and one fact never counts twice. Capped at MAX_MULTIPLIER.
"""

from __future__ import annotations

import pandas as pd

MAX_MULTIPLIER = 1.5

# item_id, title (the named driver), sector (matches sector_priors sectors),
# weight, source. Curated from the public OIG Work Plan; refresh quarterly.
WORK_PLAN_ITEMS: list[dict] = [
    {"item_id": "WP-HOSPICE-ELIG", "sector": "hospice", "weight": 1.3,
     "title": "OIG Work Plan: hospice eligibility and live-discharge review",
     "source": "oig.hhs.gov/reports-and-publications/workplan"},
    {"item_id": "WP-HH-RECERT", "sector": "home_health", "weight": 1.25,
     "title": "OIG Work Plan: home health recertification and medical necessity",
     "source": "oig.hhs.gov/reports-and-publications/workplan"},
    {"item_id": "WP-PCS-EVV", "sector": "personal_care", "weight": 1.25,
     "title": "OIG Work Plan: Medicaid personal care services / EVV compliance",
     "source": "oig.hhs.gov/reports-and-publications/workplan"},
    {"item_id": "WP-DME-TELE", "sector": "dme", "weight": 1.25,
     "title": "OIG Work Plan: DME ordered via telehealth (telefraud takedowns)",
     "source": "oig.hhs.gov/reports-and-publications/workplan"},
    {"item_id": "WP-LAB-GENETIC", "sector": "lab", "weight": 1.2,
     "title": "OIG Work Plan: genetic testing and definitive drug testing billing",
     "source": "oig.hhs.gov/reports-and-publications/workplan"},
    {"item_id": "WP-BH-SUD", "sector": "behavioral", "weight": 1.15,
     "title": "OIG Work Plan: SUD treatment and behavioral-health billing",
     "source": "oig.hhs.gov/reports-and-publications/workplan"},
    {"item_id": "WP-SNF-PDPM", "sector": "snf", "weight": 1.15,
     "title": "OIG Work Plan: SNF PDPM case-mix and staffing review",
     "source": "oig.hhs.gov/reports-and-publications/workplan"},
    {"item_id": "WP-RX-340B", "sector": "pharmacy", "weight": 1.1,
     "title": "OIG Work Plan: pharmacy / 340B contract-pharmacy oversight",
     "source": "oig.hhs.gov/reports-and-publications/workplan"},
]


def work_plan_table(items: list[dict] | pd.DataFrame | None = None) -> pd.DataFrame:
    """Validate the curated table (or an external replacement) into a frame."""
    df = pd.DataFrame(items if items is not None else WORK_PLAN_ITEMS)
    required = {"item_id", "title", "sector", "weight"}
    assert required <= set(df.columns), f"work plan table missing {required - set(df.columns)}"
    df["weight"] = pd.to_numeric(df["weight"], errors="raise")
    assert (df["weight"] >= 1.0).all(), "weights are multipliers; must be ≥ 1.0"
    return df


def government_interest_overlay(sectors: pd.Series,
                                items: list[dict] | pd.DataFrame | None = None
                                ) -> pd.DataFrame:
    """Per-org multiplier + named items for a series of sector labels.

    Returns columns ``gov_interest_multiplier`` (1.0 where nothing matches,
    max-weight capped otherwise) and ``gov_interest_items`` (semicolon-joined
    titles — the named drivers; empty where nothing matches).
    """
    table = work_plan_table(items)
    by_sector: dict[str, pd.DataFrame] = dict(tuple(table.groupby("sector")))

    mults, titles = [], []
    for sector in sectors.fillna("").astype(str):
        hits = by_sector.get(sector)
        if hits is None or not len(hits):
            mults.append(1.0)
            titles.append("")
        else:
            mults.append(min(float(hits["weight"].max()), MAX_MULTIPLIER))
            titles.append("; ".join(hits["title"]))
    return pd.DataFrame({"gov_interest_multiplier": mults,
                         "gov_interest_items": titles}, index=sectors.index)
