"""
lead_gate.py — the scheme-aware recovery gate (docs/OUTPUT_METHODOLOGY.md).

Run 2 §A, item 3, built to the corrected design: leads are ranked on
size-adjusted anomaly FIRST; this gate then decides what SURFACES — it never
re-ranks, and it is never applied to the training feed (Output 1 ships the
full universe, ungated, always).

The gate's dollar basis is per-scheme (``SCHEME_EXPOSURE_BASIS``): a blanket
own-billing threshold would delete the orchestrator cases — the $136M
telemedicine nurse billed almost nothing herself. Bases:

  own_billing        the provider's own program payments (org-grain exposure,
                     or the lead row's own net_paid as fallback)
  influenced_dollars claims billed by OTHERS on this provider's orders or
                     inducement: DMEPOS allowed dollars per referring NPI +
                     (kickback co-occurrence share × Part D drug cost)
  ring_aggregate     the canonical org's combined payments (members inherit
                     the ring's pass/fail)
  facility_program   the facility org's program payments

A lead passes if ANY scheme it evidences clears the threshold on that scheme's
basis. Evidence = its ``subscore_<scheme>`` at/above ``evidence_threshold``
(or an explicit ``schemes`` list column). Expected recovery per scheme =
basis dollars × the scheme's recovery multiplier.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .sector_priors import SCHEME_EXPOSURE_BASIS, SCHEME_RECOVERY_MULTIPLIER

DEFAULT_THRESHOLD = 5_000_000.0
DEFAULT_EVIDENCE = 0.5
DEFAULT_MULTIPLIER = 0.25          # schemes with no calibrated multiplier yet


def influenced_dollars(dmepos_metrics: pd.DataFrame | None = None,
                       partd_metrics: pd.DataFrame | None = None,
                       kickback: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per-NPI INFLUENCED dollars — the flow billed by others on this
    provider's orders/inducement. Skip-missing: sums whatever components are
    available; NPIs with no component stay absent (never zero-scored).

      * DMEPOS by-Referring metrics → ``total_allowed`` (DME dollars suppliers
        billed on this referrer's orders);
      * kickback co-occurrence share × Part D ``total_cost`` (drug dollars on
        products of manufacturers who paid this prescriber).
    """
    parts: list[pd.DataFrame] = []
    if dmepos_metrics is not None and "total_allowed" in dmepos_metrics.columns:
        d = dmepos_metrics[["npi", "total_allowed"]].copy()
        d["npi"] = d["npi"].astype(str)
        parts.append(d.rename(columns={"total_allowed": "dollars"}))
    if (kickback is not None and partd_metrics is not None
            and "op_payment_utilization_corr" in kickback.columns
            and "total_cost" in partd_metrics.columns):
        k = kickback[["npi", "op_payment_utilization_corr"]].copy()
        k["npi"] = k["npi"].astype(str)
        p = partd_metrics[["npi", "total_cost"]].copy()
        p["npi"] = p["npi"].astype(str)
        m = k.merge(p, on="npi", how="inner")
        m["dollars"] = (pd.to_numeric(m["op_payment_utilization_corr"], errors="coerce")
                        * pd.to_numeric(m["total_cost"], errors="coerce"))
        parts.append(m[["npi", "dollars"]])
    if not parts:
        return pd.DataFrame(columns=["npi", "influenced_dollars"])
    allp = pd.concat(parts, ignore_index=True)
    out = (allp.groupby("npi", as_index=False)["dollars"].sum()
           .rename(columns={"dollars": "influenced_dollars"}))
    return out


def apply_recovery_gate(leads: pd.DataFrame,
                        org_payments: pd.DataFrame | None = None,
                        influenced: pd.DataFrame | None = None,
                        threshold: float = DEFAULT_THRESHOLD,
                        evidence_threshold: float = DEFAULT_EVIDENCE,
                        basis_map: dict[str, str] | None = None,
                        multipliers: dict[str, float] | None = None
                        ) -> pd.DataFrame:
    """Annotate leads with ``expected_recovery`` / ``gate_basis`` /
    ``passes_gate``. NEVER re-ranks — output row order equals input order,
    asserted. A lead with NO evidenced scheme fails the gate with basis "" (it
    has nothing to size a case on), which keeps the gate conservative.

    ``org_payments``: org_node_id → ``payments`` (mean annual, from
    ``exposure.annual_payments_per_org``) — serves own_billing (org grain),
    ring_aggregate and facility_program. ``influenced``: npi →
    ``influenced_dollars`` (from :func:`influenced_dollars`).
    """
    basis_map = basis_map or SCHEME_EXPOSURE_BASIS
    multipliers = multipliers or SCHEME_RECOVERY_MULTIPLIER
    out = leads.copy()
    n0 = len(out)

    org_pay: dict[str, float] = {}
    if org_payments is not None and "payments" in org_payments.columns:
        org_pay = dict(zip(org_payments["org_node_id"].astype(str),
                           pd.to_numeric(org_payments["payments"], errors="coerce")))
    infl: dict[str, float] = {}
    if influenced is not None and "influenced_dollars" in influenced.columns:
        infl = dict(zip(influenced["npi"].astype(str),
                        pd.to_numeric(influenced["influenced_dollars"],
                                      errors="coerce")))

    sub_cols = {c[len("subscore_"):]: c for c in out.columns
                if c.startswith("subscore_")}
    own_fallback = (pd.to_numeric(out["net_paid"], errors="coerce")
                    if "net_paid" in out.columns
                    else pd.Series(np.nan, index=out.index))
    org_ids = (out["org_node_id"].astype(str) if "org_node_id" in out.columns
               else pd.Series("", index=out.index))
    npis = (out["npi"].astype(str) if "npi" in out.columns
            else pd.Series("", index=out.index))

    def _basis_dollars(basis: str, i) -> float:
        if basis == "influenced_dollars":
            return float(infl.get(npis.loc[i], np.nan))
        # own_billing / ring_aggregate / facility_program: org-grain payments,
        # with the lead's own net_paid as the own_billing fallback
        v = org_pay.get(org_ids.loc[i], np.nan)
        if basis == "own_billing" and (v is None or pd.isna(v)):
            v = own_fallback.loc[i]
        return float(v) if v is not None else float("nan")

    recovery = pd.Series(np.nan, index=out.index)
    basis_used = pd.Series("", index=out.index)
    for i in out.index:
        best, best_basis = np.nan, ""
        for scheme, col in sub_cols.items():
            v = out.at[i, col]
            if pd.isna(v) or float(v) < evidence_threshold:
                continue
            basis = basis_map.get(scheme, "own_billing")
            dollars = _basis_dollars(basis, i)
            if pd.isna(dollars):
                continue
            exp = dollars * float(multipliers.get(scheme, DEFAULT_MULTIPLIER))
            if pd.isna(best) or exp > best:
                best, best_basis = exp, f"{basis}:{scheme}"
        recovery.loc[i] = best
        basis_used.loc[i] = best_basis

    out["expected_recovery"] = recovery
    out["gate_basis"] = basis_used
    out["passes_gate"] = recovery.ge(threshold).fillna(False)
    assert len(out) == n0 and (out.index == leads.index).all(), \
        "recovery gate must never re-rank or fan out"
    return out
