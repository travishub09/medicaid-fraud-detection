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
from .peer_percentiles import to_peer_percentiles, rollup_to_org

__all__ = [
    "compute_partb_metrics",
    "compute_partd_metrics",
    "compute_dmepos_metrics",
    "to_peer_percentiles",
    "rollup_to_org",
]
