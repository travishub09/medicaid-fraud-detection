"""
case_size.py — scheme-specific damages estimators + the FCA recovery walk.

The pursuit list was ranked in DETECTION space (scores) and filtered on billed
dollars. The operator chases RECOVERIES, and different schemes turn into
dollars differently: billing inflation is worth its unexplained EXCESS, a
kickback taints ALL claims it influenced, a worthless-services case taints
the whole revenue stream. One model, many estimators, one currency:

  estimate_case_size(matrix)   per-NPI, per-scheme conservative single-damages
                               estimates in ANNUALIZED dollars, from whichever
                               columns exist (skip-honest: absent inputs
                               produce no estimate, never a guess):
      billing_excess   max(0, net_paid − expected_net_paid)      [direct]
      impossible_day   mue_violation_share × net_paid            [share]
      facility_code    facility_code_share × net_paid            [share]
      dme_ring         dme_ineligible_referred_dollars           [direct]
      kickback_taint   net_paid where op-payment correlation is
                       extreme (AKS taints influenced claims)    [taint]
      whole_stream     net_paid (hospice/worthless/shell class)  [taint]
  case_size = the LARGEST single estimator (schemes overlap; summing
  double-counts the same dollars), reported with its basis.

  recovery_walk(damages_single)  the honest FCA math: 6-year lookback cap,
      settlement at 1.5–2.0× single damages (not the treble headline),
      relator share 15–25%, expected value discounted by P(intervene).
      Every dossier that shows a case size shows this walk + assumptions.

Filters move from billed space to recovery space: ``--min-damages`` keeps a
$3M-billing kickback case whose tainted claims clear the bar and drops a
$12M-billing hospital with $200k of excess.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# FCA walk defaults — conservative, documented, overridable
LOOKBACK_YEARS = 6            # 31 U.S.C. 3731(b) outer limit commonly reached
SETTLE_MULT = (1.5, 2.0)      # settlements anchor on multiples of single damages
RELATOR_SHARE = (0.15, 0.25)  # intervened-case statutory band
P_INTERVENE = 0.20            # cold-start prior (model_c refines per case)

# op-payment correlation percentile above which claims are treated as
# kickback-taint candidates (conservative: top tail only)
KICKBACK_PCTL = 0.95


def recovery_walk(damages_single: float, years: float = LOOKBACK_YEARS,
                  p_intervene: float = P_INTERVENE) -> dict:
    """Annualized single damages → the FCA recovery ladder (all floats).

    ``damages_single`` is PER-YEAR; the walk extends it over the lookback,
    then applies settlement and relator-share bands and the intervention
    discount. Treble is reported as context, never as the expectation.
    """
    d = max(0.0, float(damages_single))
    period = d * years
    return {
        "damages_yr": d,
        "damages_period": period,
        "treble_exposure": period * 3,
        "settle_lo": period * SETTLE_MULT[0],
        "settle_hi": period * SETTLE_MULT[1],
        "relator_lo": period * SETTLE_MULT[0] * RELATOR_SHARE[0],
        "relator_hi": period * SETTLE_MULT[1] * RELATOR_SHARE[1],
        "expected_relator": (period * np.mean(SETTLE_MULT)
                             * np.mean(RELATOR_SHARE) * p_intervene),
    }


def _num(m: pd.DataFrame, col: str) -> pd.Series | None:
    if col not in m.columns:
        return None
    return pd.to_numeric(m[col], errors="coerce").fillna(0.0)


def estimate_case_size(matrix: pd.DataFrame) -> pd.DataFrame:
    """Per-NPI annualized single-damages estimates. Returns a frame with one
    ``dmg_*`` column per estimator that had its inputs, plus:

      case_size_yr     the largest estimator (no double counting)
      case_size_basis  which estimator won
    """
    m = matrix
    paid = _num(m, "net_paid")
    if paid is None:
        paid = _num(m, "gross_paid")
    est: dict[str, pd.Series] = {}

    exp = _num(m, "expected_net_paid")
    if paid is not None and exp is not None:
        est["dmg_billing_excess"] = (paid - exp).clip(lower=0.0)
    for share_col, name in (("mue_violation_share", "dmg_impossible_day"),
                            ("facility_code_share", "dmg_facility_code")):
        s = _num(m, share_col)
        if paid is not None and s is not None:
            est[name] = (s.clip(0, 1) * paid).clip(lower=0.0)
    d = _num(m, "dme_ineligible_referred_dollars")
    if d is not None:
        est["dmg_dme_ring"] = d.clip(lower=0.0)
    # kickback taint: whole-claims taint for the extreme op-correlation tail
    corr = _num(m, "op_payment_utilization_corr__peerpct")
    if corr is None:
        corr = _num(m, "op_payment_utilization_corr")
    if paid is not None and corr is not None:
        tail = (corr >= corr.quantile(KICKBACK_PCTL)) & (corr > 0)
        est["dmg_kickback_taint"] = paid.where(tail, 0.0)

    out = pd.DataFrame(est, index=m.index)
    if len(out.columns):
        out["case_size_yr"] = out.max(axis=1)
        out["case_size_basis"] = out.drop(columns=["case_size_yr"]) \
            .idxmax(axis=1).str.replace("dmg_", "", regex=False)
        out.loc[out["case_size_yr"] <= 0, "case_size_basis"] = "none"
    else:
        out["case_size_yr"] = 0.0
        out["case_size_basis"] = "none"
    return out


def org_case_size(matrix: pd.DataFrame) -> pd.DataFrame:
    """NPI estimates → org grain: sum member case sizes (different members'
    dollars are distinct claims — summing is correct ACROSS providers, only
    within-provider across-scheme sums double-count)."""
    per = estimate_case_size(matrix)
    org_col = "org_node_id" if "org_node_id" in matrix.columns else "group_id"
    if org_col not in matrix.columns:
        return pd.DataFrame(columns=["case_size_yr", "case_size_basis"])
    g = per.assign(_org=matrix[org_col].astype(str)).groupby("_org")
    out = pd.DataFrame({
        "case_size_yr": g["case_size_yr"].sum(),
        "case_size_basis": g["case_size_basis"]
        .agg(lambda s: s[s != "none"].mode().iat[0]
             if (s != "none").any() else "none"),
    })
    out.index.name = "org_id"
    return out.reset_index()


def size_line(damages_yr: float) -> str:
    """One human sentence for the dossier: the walk with its assumptions."""
    w = recovery_walk(damages_yr)
    if w["damages_yr"] <= 0:
        return ("Estimated case size: not computable from current signals "
                "(no dollar-denominated estimator fired).")
    return (f"Estimated case size: ~${w['damages_yr']:,.0f}/yr single damages "
            f"(~${w['damages_period']:,.0f} over a {LOOKBACK_YEARS}-year "
            f"lookback). Settlement band ${w['settle_lo']:,.0f}–"
            f"${w['settle_hi']:,.0f}; relator share ${w['relator_lo']:,.0f}–"
            f"${w['relator_hi']:,.0f}; probability-weighted expectation "
            f"~${w['expected_relator']:,.0f}. Assumptions: excess-only where "
            "estimable, settlement at 1.5–2.0x single damages, relator "
            "15–25%, intervention prior 20%. These are screening estimates, "
            "not damages calculations.")
