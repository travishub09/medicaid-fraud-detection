"""
consistency.py — cross-source consistency checks (Pillar 4, docs/platform/17).

The highest-precision fraud signals come not from any single number but from
INCOHERENCE ACROSS INDEPENDENT SYSTEMS: the NPPES registration says one thing, the
PECOS enrollment another, and the billing behavior a third. A fraudster can inflate
billing, but making it *coherent* with the registration record requires forging
several independent systems at once — so a contradiction is hard to fake and rarely
innocent.

Each check crosses a STRUCTURAL/identity attribute (entity type, enrollment tenure,
org size) against a BEHAVIORAL one (billing scale, code breadth), within the
provider's taxonomy peer group:

  incons_solo_scale       a TYPE-1 INDIVIDUAL billing at institutional scale
                          (top of its specialty by dollars) — one person can't
                          personally deliver an institution's volume.
  incons_instant_scale    a provider with almost no enrollment tenure already
                          billing at full scale — legitimate practices ramp;
                          fly-by-night schemes materialize at scale.
  incons_breadth          a solo individual billing an implausibly broad set of
                          distinct codes (more procedure types than one clinician
                          competently performs).
  incons_lone_org_scale   a ONE-NPI "organization" billing at institutional scale —
                          an institution with a single provider behind it.

``consistency_flags`` sums them: the more independent systems disagree, the stronger
the signal. All clean (NPPES + billing, no exclusion data). Flags, not verdicts —
the tree learns each one's weight.
"""

from __future__ import annotations

import pandas as pd

HI = 0.90                 # top-decile of the specialty on the behavioral metric
INSTANT_TENURE_MONTHS = 12
INSTANT_HI = 0.75


def _num(df: pd.DataFrame, col: str, default=0.0) -> pd.Series:
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce")
    return pd.Series(default, index=df.index)


def consistency_checks(matrix: pd.DataFrame, hi: float = HI) -> pd.DataFrame:
    """Per-NPI cross-source incoherence flags + a ``consistency_flags`` count."""
    df = matrix
    idx = df.index
    group = df.get("primary_taxonomy", pd.Series("", index=idx)).fillna("").astype(str)
    etype = df.get("entity_type", pd.Series("", index=idx)).fillna("").astype(str)
    tenure = _num(df, "tenure_months", default=None)
    members = _num(df, "org_member_count", default=1)

    # behavioral percentiles WITHIN the specialty (controls for legitimately
    # expensive fields — being high *for your specialty* is the signal)
    paid_pct = _num(df, "net_paid").groupby(group).rank(method="average", pct=True)
    breadth_pct = _num(df, "n_distinct_hcpcs").groupby(group).rank(method="average", pct=True)

    is_individual = etype == "1"
    out = pd.DataFrame(index=idx)
    out["incons_solo_scale"] = (is_individual & (paid_pct >= hi)).astype(int)
    out["incons_instant_scale"] = (
        tenure.notna() & (tenure < INSTANT_TENURE_MONTHS) & (paid_pct >= INSTANT_HI)
    ).fillna(False).astype(int)
    out["incons_breadth"] = (is_individual & (breadth_pct >= hi)).astype(int)
    out["incons_lone_org_scale"] = ((members <= 1) & (paid_pct >= hi)).astype(int)
    out["consistency_flags"] = out[[c for c in out.columns]].sum(axis=1)
    out.insert(0, "npi", df["npi"].astype(str).to_numpy())
    return out.reset_index(drop=True)
