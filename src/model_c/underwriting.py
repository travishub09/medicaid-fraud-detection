"""
underwriting.py — the Model C underwriting decision (cold-start, rules-based).

Per ``docs/platform/06-model-c.md`` and ``08-litigation-finance.md``. Label-free
and explainable: P(intervene) is a base rate times named, bounded multipliers
(scheme priority, public-data corroboration, evidence, relator credibility,
jurisdiction, public-disclosure penalty); the recovery distribution is the
damages proxy carried to a realized settlement with a heavy right tail; the two
combine into expected relator gross and a fund / pass / fund-with-terms call with
capital and a take percentage priced to the portfolio target.

    Expected relator gross = P(recover) × E[recovery|recover]
                             × relator_share% × time_discount

Every output row carries its drivers — the same explainability contract Model A
honors (hard rule #5). These are underwriting ESTIMATES for human/counsel review,
never legal advice or a promise of outcome; the renderer hard-codes that frame.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .priors import UnderwritingAssumptions, DEFAULT_ASSUMPTIONS


def _lerp(x, lo: float, hi: float):
    """Map a 0–1 feature into [lo, hi]."""
    return lo + (hi - lo) * np.clip(x, 0.0, 1.0)


def predict_intervention(case_features: pd.DataFrame,
                         a: UnderwritingAssumptions = DEFAULT_ASSUMPTIONS
                         ) -> pd.DataFrame:
    """P(intervene) + the outcome-class split, with named multiplier drivers.

    Returns per case: p_intervene, p_declined_pursued, p_dismissed, p_recover,
    and one ``mult_*`` column per driver (the investment-memo decomposition).
    """
    f = case_features
    scheme_mult = f["scheme"].map(a.scheme_multiplier)
    corrob_mult = _lerp(f["corroboration_strength"], *a.corroboration_range)
    evidence_mult = _lerp(f["evidence_strength"], *a.evidence_range)
    cred_mult = _lerp(f["relator_credibility"], *a.credibility_range)
    culp_mult = 1.0 - a.culpability_penalty * f["relator_culpability"].clip(0, 1)

    if "jurisdiction_multiplier_override" in f.columns:
        juris_mult = f["jurisdiction_multiplier_override"].where(
            f["jurisdiction_multiplier_override"].notna(),
            f["jurisdiction"].map(a.jurisdiction_multiplier))
    else:
        juris_mult = f["jurisdiction"].map(a.jurisdiction_multiplier)
    juris_mult = pd.to_numeric(juris_mult, errors="coerce").fillna(1.0)

    disc_mult = np.where(f["public_disclosure_flag"].astype(int) == 1,
                         a.public_disclosure_penalty, 1.0)

    raw = (a.base_intervention_rate * scheme_mult * corrob_mult * evidence_mult
           * cred_mult * culp_mult * juris_mult * disc_mult)
    p_int = pd.Series(raw, index=f.index).clip(a.intervention_floor, a.intervention_cap)

    # first-to-file not cleared: someone may already own the claim → near-zero
    ftf = f["first_to_file_cleared"].astype(int)
    p_int = p_int.where(ftf == 1, a.intervention_floor)

    p_not = 1.0 - p_int
    p_declined = p_not * a.declined_pursued_share
    p_dismissed = p_not - p_declined
    p_recover = (p_int * a.intervened_recovery_prob
                 + p_declined * a.declined_pursued_recovery_prob)

    return pd.DataFrame({
        "p_intervene": p_int.round(4),
        "p_declined_pursued": p_declined.round(4),
        "p_dismissed": p_dismissed.round(4),
        "p_recover": p_recover.round(4),
        "mult_scheme_priority": np.round(scheme_mult, 3),
        "mult_corroboration": np.round(corrob_mult, 3),
        "mult_evidence": np.round(evidence_mult, 3),
        "mult_relator_credibility": np.round(cred_mult, 3),
        "mult_culpability": np.round(culp_mult, 3),
        "mult_jurisdiction": np.round(juris_mult, 3),
        "mult_public_disclosure": np.round(disc_mult, 3),
    }, index=f.index)


def recovery_distribution(case_features: pd.DataFrame,
                          a: UnderwritingAssumptions = DEFAULT_ASSUMPTIONS
                          ) -> pd.DataFrame:
    """P10/P50/P90 realized-recovery distribution from the damages proxy.

    Single damages → a realized settlement (between single and treble; FCA
    settlements cluster ~1.5–2× single), with a heavy right tail for the whales.
    Low data-confidence widens the band (more uncertainty), never shifts the
    median (no free magnitude from thin data).
    """
    f = case_features
    single = pd.to_numeric(f["damages_single"], errors="coerce").fillna(0.0).clip(lower=0)
    p50 = single * a.settlement_multiple_of_single

    # widen the spread as data-confidence drops (1.0 at high, → low_confidence_spread)
    widen = 1.0 + (a.low_confidence_spread - 1.0) * (1.0 - f["data_confidence"].clip(0, 1))
    p10 = p50 * a.p10_factor / widen
    p90 = p50 * a.p90_factor * widen
    treble_ceiling = single * a.treble_multiple
    # the realized mean of a right-skewed band, capped at the statutory treble
    e_recovery = np.minimum((p10 + p50 + p90) / 3.0 * 1.10, treble_ceiling * 1.5)

    return pd.DataFrame({
        "single_damages": single.round(2),
        "recovery_p10": p10.round(2),
        "recovery_p50": p50.round(2),
        "recovery_p90": p90.round(2),
        "expected_recovery": pd.Series(e_recovery, index=f.index).round(2),
    }, index=f.index)


def underwrite(case_features: pd.DataFrame, portfolio_target_moic: float = 3.0,
               a: UnderwritingAssumptions = DEFAULT_ASSUMPTIONS) -> pd.DataFrame:
    """fund / pass / fund-with-terms per case, with capital, take %, and drivers.

    Combines P(intervene) and the recovery distribution into expected relator
    gross, then prices the funder's take to hit ``portfolio_target_moic`` on the
    deployed capital. The decision and every input ride on the row.
    """
    f = case_features.reset_index(drop=True)
    interv = predict_intervention(f, a).reset_index(drop=True)
    recov = recovery_distribution(f, a).reset_index(drop=True)

    # blended relator share across the recovering outcome mass
    recov_mass = (interv["p_intervene"] * a.intervened_recovery_prob
                  + interv["p_declined_pursued"] * a.declined_pursued_recovery_prob)
    share = np.where(
        recov_mass > 0,
        (interv["p_intervene"] * a.intervened_recovery_prob * a.relator_share_intervened
         + interv["p_declined_pursued"] * a.declined_pursued_recovery_prob
         * a.relator_share_declined) / recov_mass.replace(0, np.nan),
        a.relator_share_intervened)
    share = pd.Series(share, index=f.index).fillna(a.relator_share_intervened)

    expected_gross = (interv["p_recover"] * recov["expected_recovery"]
                      * share * a.time_discount)

    capital = pd.Series(a.capital_per_case, index=f.index)
    # take needed so EV(take) / capital ≥ target MOIC; take is a % of relator gross
    with np.errstate(divide="ignore", invalid="ignore"):
        take_needed = (portfolio_target_moic * capital / expected_gross).replace(
            [np.inf, -np.inf], np.nan)

    rec, take, cap_out, reasons = [], [], [], []
    for i in f.index:
        eg = float(expected_gross[i])
        tn = float(take_needed[i]) if pd.notna(take_needed[i]) else np.inf
        why = []
        if f["public_disclosure_flag"][i] == 1:
            why.append("public-disclosure flag (counsel must clear the §3730(e)(4) bar)")
        if int(f["first_to_file_cleared"][i]) == 0:
            why.append("first-to-file not cleared")
        if eg < a.min_expected_gross:
            why.append(f"expected gross ${eg:,.0f} below ${a.min_expected_gross:,.0f} floor")

        if why:                                     # any hard gate → pass
            rec.append("pass"); take.append(0.0); cap_out.append(0.0)
            reasons.append("; ".join(why))
        elif tn <= a.comfortable_take_fraction:     # clears the bar at a gentle take
            rec.append("fund")
            take.append(round(max(tn, 0.0), 4))
            cap_out.append(float(capital[i]))
            reasons.append(f"EV clears target at a {max(tn,0.0):.1%} take")
        elif tn <= a.max_take_fraction:             # marginal: fund with priced terms
            rec.append("fund-with-terms")
            take.append(round(tn, 4))
            cap_out.append(float(capital[i]))
            reasons.append(f"priced to {portfolio_target_moic:.1f}× at a {tn:.1%} take")
        else:                                       # can't hit target even at max take
            rec.append("pass"); take.append(0.0); cap_out.append(0.0)
            reasons.append(f"can't reach {portfolio_target_moic:.1f}× target even at "
                           f"the {a.max_take_fraction:.0%} take cap")

    out = pd.concat([f[["org_node_id", "org_name", "scheme", "has_relator"]],
                     interv, recov], axis=1)
    out["blended_relator_share"] = share.round(4)
    out["expected_relator_gross"] = expected_gross.round(2)
    out["recommendation"] = rec
    out["take_fraction"] = take
    out["capital_deployed"] = cap_out
    out["funder_expected_value"] = (out["take_fraction"] * out["expected_relator_gross"]).round(2)
    out["expected_moic"] = np.where(out["capital_deployed"] > 0,
                                    (out["funder_expected_value"]
                                     / out["capital_deployed"]).round(3), 0.0)
    out["decision_reasons"] = reasons
    # whale-likelihood: P(recover) weighted shot at a top-decile P90 outcome
    p90_thresh = out["recovery_p90"].quantile(0.9) if len(out) else 0.0
    out["whale_likelihood"] = (out["p_recover"]
                               * (out["recovery_p90"] >= p90_thresh).astype(float)).round(4)
    return out
