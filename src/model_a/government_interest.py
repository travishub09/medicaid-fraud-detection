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
    # 2026-11-02 quarterly refresh (the first real one since the table was written on
    # 2026-06-12). oig.hhs.gov is not reachable from the build session, so the project
    # numbers and dates below come from search snippets of the item pages and are marked
    # as such in `source`; the operator spot-checks them on the live page. Retired this
    # refresh: hospice eligibility (nationwide review completed 2026-06-18; the compliance
    # series 2026-07-28), DME via telehealth (OEI-02-24-00310 completed 2026-09-09; the
    # Part B telehealth audit series 2026-04-23), 340B contract pharmacy (the only Part D /
    # 340B item is marked Removed). Re-titled: home health recertification -> enrollment
    # trends + admission source.
    {"item_id": "WP-HOSPICE-NH-RHC", "sector": "hospice", "weight": 1.25,
     "title": "OIG Work Plan: hospice routine home care in nursing homes (OEI-02-26-00180)",
     "source": "oig.hhs.gov work plan, announced 2026-07-15 (snippet, verify on the live page)"},
    {"item_id": "WP-HH-ENROLL", "sector": "home_health", "weight": 1.2,
     "title": "OIG Work Plan: home health agency enrollment trends and admission-source billing (OAS-26-05-110, OAS-26-05-056)",
     "source": "oig.hhs.gov work plan, announced 2026-06-24 / 2026-04-23 (snippet, verify on the live page)"},
    {"item_id": "WP-PCS-EVV", "sector": "personal_care", "weight": 1.25,
     "title": "OIG Work Plan: Medicaid personal care services / EVV compliance (series active; CO audit completed 2026-07-17)",
     "source": "oig.hhs.gov/reports-and-publications/workplan"},
    {"item_id": "WP-HCBS-ELIG", "sector": "personal_care", "weight": 1.2,
     "title": "OIG Work Plan: Medicaid HCBS eligibility audits, personal care / adult day care (OAS-26-06-008)",
     "source": "oig.hhs.gov work plan, announced 2026-05-20 (snippet, verify on the live page)"},
    {"item_id": "WP-NEMT-STATE-AUDITS", "sector": "ambulance", "weight": 1.25,
     "title": "OIG Work Plan: Medicaid non-emergency medical transportation state audits (SRS-A-26-028, OAS-26-02-087)",
     "source": "oig.hhs.gov work plan, announced 2026-05-28 (snippet, verify on the live page)"},
    {"item_id": "WP-DME-TAKEDOWN", "sector": "dme", "weight": 1.1,
     "title": "DME ordered via telehealth: the 2026 national takedown (enforcement, not an active Work Plan item)",
     "source": "justice.gov health care fraud takedown 2026; the OIG DME items completed 2026-08/09"},
    {"item_id": "WP-LAB-GENETIC", "sector": "lab", "weight": 1.2,
     "title": "OIG Work Plan: genetic tests under Part B (OEI-09-26-00270) and definitive drug testing billing",
     "source": "oig.hhs.gov work plan, announced 2026-06-15 (snippet, verify on the live page)"},
    {"item_id": "WP-LAB-PANELS", "sector": "lab", "weight": 1.2,
     "title": "OIG Work Plan: expanded laboratory panels and Medicaid clinical lab audits (OAS-26-09-071, OAS-26-04-140)",
     "source": "oig.hhs.gov work plan, announced 2026-06-11 / 2026-08-12 (snippet, verify on the live page)"},
    {"item_id": "WP-BH-EPSDT-MC", "sector": "behavioral", "weight": 1.15,
     "title": "OIG Work Plan: Medicaid managed care EPSDT behavioral health audits (OAS-26-02-108, OAS-26-02-114) and SUD treatment billing",
     "source": "oig.hhs.gov work plan, announced 2026-05-18 / 2026-06-08 (snippet, verify on the live page)"},
    {"item_id": "WP-SNF-PDPM", "sector": "snf", "weight": 1.15,
     "title": "OIG Work Plan: SNF PDPM case-mix audits (OAS-26-03-134, series SRS-A-25-010) and staffing review",
     "source": "oig.hhs.gov work plan, new audit announced 2026-07-15 (snippet, verify on the live page)"},
    {"item_id": "WP-SNF-ANTIPSYCH", "sector": "snf", "weight": 1.1,
     "title": "OIG Work Plan: nationwide analysis of antipsychotic drug use in nursing homes (OEI-02-23-00202)",
     "source": "oig.hhs.gov work plan, announced 2026-06-29 (snippet, verify on the live page)"},
    {"item_id": "WP-RX-PARTD-FRAUD", "sector": "pharmacy", "weight": 1.15,
     "title": "OIG Work Plan: Part D pharmacy fraud and GLP-1 overdispensing (OEI-02-25-00410, OEI-BL-26-00370)",
     "source": "oig.hhs.gov work plan, announced 2026-01-15 / 2026-09-15 (snippet, verify on the live page)"},
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
