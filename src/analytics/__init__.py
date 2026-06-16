"""
analytics — the apples-to-apples comparison layer.

Outlier signal is only as credible as the peer group behind it: a hospice
compared against family practices, or a 10,000-patient referral center compared
against solo clinics, produces confident nonsense. This package is the shared
peer engine the spec calls for (04-model-a.md §peer groups, §false-positive
trap; GAPS #15):

  peers.py   hierarchical peer assignment (specialty × entity × geography with
             an explicit fallback ladder), degenerate-cell-guarded one-sided
             percentiles, within-peer complexity adjustment (residualization
             against volume/breadth so "busy" stops looking like "fraudulent"),
             complexity context flags, and a peer-quality report.
  growth.py  change-point growth-shock detection (level shifts, new-code bursts).
  plausibility.py  data-derived clinical-plausibility: a per-code, per-specialty
             prevalence matrix flags codes implausibly rare for the biller's
             taxonomy, dollar-weighted, with named drivers (A5).
  confidence.py  graded data-confidence band with named reasons.

Every provider's row records WHICH ladder level it was compared at and against
HOW MANY peers — the explainability requirement applies to the comparison
itself, not just the score.
"""

from .confidence import confidence_band
from .tmsis_quality import state_quality, attach_state_quality
from .growth import growth_features, growth_percentiles
from .plausibility import (
    code_prevalence_matrix,
    org_clinical_plausibility,
    plausibility_percentiles,
    local_denominator_plausibility,
)
from .peers import (
    assign_peer_groups,
    one_sided_percentiles,
    complexity_adjust,
    complexity_flags,
    peer_report,
    DEFAULT_LADDER,
)

__all__ = [
    "confidence_band",
    "state_quality",
    "attach_state_quality",
    "growth_features",
    "growth_percentiles",
    "code_prevalence_matrix",
    "org_clinical_plausibility",
    "plausibility_percentiles",
    "local_denominator_plausibility",
    "assign_peer_groups",
    "one_sided_percentiles",
    "complexity_adjust",
    "complexity_flags",
    "peer_report",
    "DEFAULT_LADDER",
]
