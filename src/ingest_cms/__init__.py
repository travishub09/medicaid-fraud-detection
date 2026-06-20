"""
ingest_cms — adapters for the Priority-1 CMS public-use files.

Implements docs/platform/09-data-procurement.md's adapter contract for the files
that power the dormant schemes in Model A's feature registry:

  partb.py             Medicare Physician & Other Practitioners (by Provider and
                       Service) → upcoding / overutilization / concentration metrics
  partd.py             Medicare Part D Prescribers (by Provider and Drug) →
                       brand-steering / high-cost-drug metrics
  dmepos.py            Medicare DMEPOS (by Referring Provider and Service) →
                       high-cost-item metrics
  openpayments.py      Open Payments (manufacturer→physician) → payment
                       concentration, pays edges, kickback co-occurrence with Part D
  saturation.py        Market Saturation & Utilization (county × service) →
                       market_saturation_index, the CMS program-integrity prior (B2)
  facility.py          PBJ nurse staffing + Care Compare hospice/deficiencies at
                       the CCN grain → worthless-services / hospice-ineligibility
                       signals; facility peer cells = size band × region (B1)
  opioid.py            Part D Opioid Prescriber rates → pill_mill scheme (sweep E1)
  nppes_deactivation.py  billing on/after an NPI's deactivation date →
                       invalid_identity scheme (sweep E1)
  hrsa_340b.py         340B OPAIS covered entities + contract-pharmacy footprint →
                       contract_pharmacy scheme (sweep E1)
  pos.py               Provider of Services capacity → capacity-vs-billing
                       "impossible org" → worthless_services (sweep 2.5)
  order_referring.py   Order & Referring eligibility → orders from ineligible
                       referrers → dme_ring sharpener (sweep 2.6)
  peer_percentiles.py  raw metrics → one-sided peer-relative percentiles (the 0–1
                       inputs the registry expects) → NPI→org rollup

Contract (every adapter, same as integrate.py):
  * column maps name the REAL PUF headers (plus variants) so the downloaded files
    work unmodified; resolved via the shared ``_resolve_columns``;
  * everything read as strings; NPIs through ``canonicalize_series`` with a
    quarantine count returned, never silently dropped;
  * output: one tidy per-NPI metric table; peer normalization is a separate,
    shared step (metrics and percentiles are different things).

The metric names match Model A's registry (``model_a/scheme_subscores.py``); the
registry consumes the *percentile* versions (0–1), produced by peer_percentiles.
"""

from .partb import compute_partb_metrics
from .partd import compute_partd_metrics
from .dmepos import compute_dmepos_metrics
from .openpayments import compute_openpayments_metrics, kickback_co_occurrence
from .saturation import (compute_saturation_metrics, state_saturation_index,
                         attach_market_saturation)
from .facility import (compute_pbj_metrics, compute_hospice_metrics,
                       compute_deficiency_counts, facility_peer_percentiles,
                       rollup_ccn_to_org)
from .opioid import compute_opioid_metrics
from .nppes_deactivation import deactivated_npis, billing_after_deactivation
from .hrsa_340b import covered_entities, attach_340b
from .pos import compute_pos_capacity, capacity_billing_mismatch
from .order_referring import eligible_referrers, ineligible_referral_share
from .census_population import county_population, zip_to_county
from .nadac import compute_nadac_reference, drug_spread_anomaly
from .hcris import compute_hcris_metrics, hcris_anomaly, load_hcris
from .docgraph import build_referral_edges
from .peer_percentiles import to_peer_percentiles, rollup_to_org
# data-expansion sprint stubs (docs/platform/16) — built contracts, dormant
from .hospital_puf import compute_hospital_drg_metrics, hospital_upcoding_anomaly
from .geographic_variation import compute_geo_baseline, attach_geo_expectation
from .nucc_taxonomy import (load_taxonomy_hierarchy, load_specialty_crosswalk,
                            canonical_peer_group)
from .sdud import compute_sdud_reference
from .chow import normalize_chow_events

__all__ = [
    "compute_partb_metrics",
    "compute_partd_metrics",
    "compute_dmepos_metrics",
    "compute_openpayments_metrics",
    "kickback_co_occurrence",
    "compute_saturation_metrics",
    "state_saturation_index",
    "attach_market_saturation",
    "compute_pbj_metrics",
    "compute_hospice_metrics",
    "compute_deficiency_counts",
    "facility_peer_percentiles",
    "rollup_ccn_to_org",
    "compute_opioid_metrics",
    "deactivated_npis",
    "billing_after_deactivation",
    "covered_entities",
    "attach_340b",
    "compute_pos_capacity",
    "capacity_billing_mismatch",
    "eligible_referrers",
    "ineligible_referral_share",
    "county_population",
    "zip_to_county",
    "compute_nadac_reference",
    "drug_spread_anomaly",
    "compute_hcris_metrics",
    "hcris_anomaly",
    "load_hcris",
    "build_referral_edges",
    "to_peer_percentiles",
    "rollup_to_org",
    # data-expansion sprint (docs/platform/16)
    "compute_hospital_drg_metrics",
    "hospital_upcoding_anomaly",
    "compute_geo_baseline",
    "attach_geo_expectation",
    "load_taxonomy_hierarchy",
    "load_specialty_crosswalk",
    "canonical_peer_group",
    "compute_sdud_reference",
    "normalize_chow_events",
]
