"""
priors.py — Model C cold-start assumptions (the underwriting rule tables).

Model C ships label-free first (06-model-c.md §labels: "cold-start on rules —
DOJ priority schemes, jurisdiction intervention rates, scheme base rates — until
enough structured outcomes exist"). Every number here is a DOCUMENTED PLACEHOLDER
ordered by the strategy doc and DOJ statistics; all of it RETIRES once the
structured DOJ/OIG/PACER outcome database exists (re-derive the same way
`derive_priors` rebuilds Model A's sector priors). Keeping them in one auditable
table is the point — no magic constants scattered through the math.

Sourcing notes for the starting values:
  * Base intervention rate ~0.20: of ~979 FY2024 qui tam filings the government
    intervenes in a minority, but those carry ~$2.2B of $2.4B recovered — so
    P(intervene) dominates expected value (the §"why intervention is the target").
  * Relator shares are the statutory bands (15–25% intervened, 25–30% declined-
    pursued); midpoints used.
  * The Zafirov venue penalty reflects the 11th-Circuit constitutional overhang
    (01-legal-compliance.md) — a real, current discount on Florida-district venue.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# scheme_hypothesis (Model A names) → DOJ enforcement-priority multiplier on the
# intervention odds. Ordered by the enforcement-sector overlay (07 §4.3).
SCHEME_PRIORITY_MULTIPLIER: dict[str, float] = {
    "upcoding": 1.5,                 # MA risk-adjustment / chart-mining: largest dollars
    "pharma_kickback": 1.4,
    "drug_outlier": 1.4,
    "dme_ring": 1.3,
    "hospice_ineligibility": 1.3,
    "worthless_services": 1.2,
    "impossible_day": 1.2,
    "single_service_mill": 1.1,
    "overutilization": 1.1,
    "payment_outlier": 1.0,
    "specialty_mismatch": 1.0,
    "saturation_fraud": 0.9,         # a geographic prior alone rarely drives intervention
    "ownership_integrity": 1.1,
    "rapid_ramp": 1.0,
    "default": 1.0,
}

# US district / USAO → historical healthcare-fraud intervention tendency, as a
# multiplier on the base rate (1.0 = national average). Placeholder; re-derive
# from the case DB's jurisdiction column. Zafirov-exposed FL districts discounted.
JURISDICTION_MULTIPLIER: dict[str, float] = {
    "Eastern District of Pennsylvania": 1.25,   # historically active FCA venue
    "District of Massachusetts": 1.25,
    "Southern District of New York": 1.2,
    "Southern District of Florida": 0.85,       # 11th Cir. Zafirov overhang
    "Middle District of Florida": 0.85,         # where Zafirov was decided
    "Northern District of Florida": 0.85,
    "default": 1.0,
}


@dataclass(frozen=True)
class UnderwritingAssumptions:
    """Every tunable Model C uses, in one place (re-derive from the case DB)."""
    base_intervention_rate: float = 0.20

    # multiplier ranges: each 0–1 feature maps linearly into [lo, hi]
    corroboration_range: tuple[float, float] = (0.70, 1.60)   # Model A signal: the edge
    evidence_range: tuple[float, float] = (0.60, 1.50)
    credibility_range: tuple[float, float] = (0.80, 1.30)
    defendant_size_range: tuple[float, float] = (1.00, 1.25)  # bigger defendant → likelier intervention; neutral at size 0
    culpability_penalty: float = 0.40            # high culpability cuts up to 40%
    public_disclosure_penalty: float = 0.55      # already-public allegations
    intervention_floor: float = 0.01
    intervention_cap: float = 0.95

    # outcome split for the non-intervened mass, and how often each recovers
    declined_pursued_share: float = 0.15         # of non-intervened cases
    intervened_recovery_prob: float = 0.95
    declined_pursued_recovery_prob: float = 0.25

    # magnitude: single damages → realized settlement (between single and treble)
    settlement_multiple_of_single: float = 1.8   # FCA settlements cluster ~1.5–2×
    treble_multiple: float = 3.0                 # the statutory ceiling (context)
    p10_factor: float = 0.40                     # recovery-distribution spread …
    p90_factor: float = 2.50                     # … heavy right tail (whales)
    low_confidence_spread: float = 1.40          # widen the band when data-confidence is low

    # relator economics
    relator_share_intervened: float = 0.18       # 15–25% band midpoint-ish
    relator_share_declined: float = 0.27         # 25–30% band
    time_discount: float = 0.66                  # ~3.5 yr seal/investigation @ ~12%

    # financing
    capital_per_case: float = 250_000.0          # cold-start relator-support tranche per funded case
    max_take_fraction: float = 0.25              # cap on funder's % of relator gross (passive)
    comfortable_take_fraction: float = 0.15      # below this → clean "fund"
    min_expected_gross: float = 1_000_000.0      # EV floor to bother funding

    def scheme_multiplier(self, scheme: str) -> float:
        return SCHEME_PRIORITY_MULTIPLIER.get(
            scheme, SCHEME_PRIORITY_MULTIPLIER["default"])

    def jurisdiction_multiplier(self, jurisdiction: str) -> float:
        return JURISDICTION_MULTIPLIER.get(
            str(jurisdiction or ""), JURISDICTION_MULTIPLIER["default"])


DEFAULT_ASSUMPTIONS = UnderwritingAssumptions()
