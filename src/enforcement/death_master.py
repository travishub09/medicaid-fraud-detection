"""
death_master.py — SSA Death Master File: billing under a deceased provider (E1).

Source: the public SSA Death Master File (free, FOIA) / NTIS Limited-Access DMF.
The fraud shape is billing under a dead provider's identity. We flagged this as
hard earlier for a real reason — the DMF is keyed by name + DOB, not NPI, and a
name-only match to providers is NOISY. So this module builds it WITH that
honesty baked in:

  parse_dmf(raw)                → last/first/dob/dod, name_key.
  match_deceased_providers(provider_dim, dmf)  → NPI → deceased date, matched on
      normalized name AND date-of-birth WHEN provider_dim carries a DOB; name-only
      matches are returned but flagged ``match_confidence="low"`` and must be
      treated as corroboration to verify, never proof.
  billing_after_death(spending, matches, npi_to_org)  → per-org dollars billed
      on/after a matched provider's death date → ``billing_after_death`` share.

This feeds the ``invalid_identity`` scheme ONLY through high-confidence (DOB-
corroborated) matches; low-confidence name-only matches surface as a review flag,
never a score driver (the explainability / defamation guardrail).
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import canonicalize_series
from src.entity_graph.resolve_entities import norm_org_name

DMF_COLS = {
    "last": ["last_name", "LAST", "Last Name", "lname"],
    "first": ["first_name", "FIRST", "First Name", "fname"],
    "dob": ["date_of_birth", "DOB", "birth_date", "BIRTH"],
    "dod": ["date_of_death", "DOD", "death_date", "DEATH"],
}


def parse_dmf(raw: pd.DataFrame) -> pd.DataFrame:
    """DMF rows → last, first, dob, dod, name_key (LAST FIRST)."""
    cols = {k: next((c for c in opts if c in raw.columns), None)
            for k, opts in DMF_COLS.items()}
    if not cols["last"] or not cols["dod"]:
        raise ValueError(f"DMF missing name/death columns; saw {list(raw.columns)[:12]}")
    df = pd.DataFrame({
        "last": raw[cols["last"]].fillna("").astype(str).str.strip(),
        "first": (raw[cols["first"]].fillna("").astype(str).str.strip()
                  if cols["first"] else ""),
        "dob": pd.to_datetime(raw[cols["dob"]], errors="coerce") if cols["dob"] else pd.NaT,
        "dod": pd.to_datetime(raw[cols["dod"]], errors="coerce"),
    })
    df["name_key"] = (df["last"] + " " + df["first"]).map(norm_org_name)
    return df[(df["name_key"] != "") & df["dod"].notna()].reset_index(drop=True)


def _sorted_name_key(name: str) -> str:
    """Order-invariant person key: sorted tokens of the normalized name, so
    'John Smith' and 'Smith, John' match ('JOHN SMITH')."""
    return " ".join(sorted(norm_org_name(name).split()))


def match_deceased_providers(provider_dim: pd.DataFrame, dmf: pd.DataFrame
                             ) -> pd.DataFrame:
    """NPI → deceased date, with a match-confidence flag.

    Matches the provider's name key to the DMF (order-invariant on name tokens);
    if BOTH carry a DOB and they agree, ``match_confidence="high"`` — otherwise
    ``"low"`` (name-only, noisy). Returns npi, deceased_date, match_confidence.
    """
    p = provider_dim.copy()
    p["npi"] = canonicalize_series(p["npi"]).astype(str)
    name_src = (p["provider_name"] if "provider_name" in p.columns
                else p.get("name_key", "")).fillna("").astype(str)
    p["name_key"] = name_src.map(_sorted_name_key)
    p_dob = pd.to_datetime(p["dob"], errors="coerce") if "dob" in p.columns else None

    dmf_by_key: dict[str, list] = {}
    for r in dmf.itertuples():
        dmf_by_key.setdefault(" ".join(sorted(str(r.name_key).split())), []
                              ).append((r.dod, r.dob))

    rows = []
    for i, r in p.iterrows():
        cands = dmf_by_key.get(r["name_key"])
        if not r["name_key"] or not cands:
            continue
        dod, ddob = cands[0]
        conf = "low"
        if p_dob is not None and pd.notna(p_dob.iloc[i]) and pd.notna(ddob) \
                and p_dob.iloc[i].date() == ddob.date():
            conf = "high"
        rows.append({"npi": r["npi"], "deceased_date": dod, "match_confidence": conf})
    return pd.DataFrame(rows, columns=["npi", "deceased_date", "match_confidence"])


def billing_after_death(spending: pd.DataFrame, matches: pd.DataFrame,
                        npi_to_org: pd.DataFrame,
                        high_confidence_only: bool = True) -> pd.DataFrame:
    """Per-org dollars billed on/after a matched provider's death date.

    By default only HIGH-confidence (DOB-corroborated) matches feed the score;
    low-confidence name-only matches are excluded from the driver and should be
    routed to human review instead. Returns org_node_id, post_death_paid,
    total_paid, billing_after_death (0–1 share — the registry feature).
    """
    cols = ["org_node_id", "post_death_paid", "total_paid", "billing_after_death"]
    m = matches.copy()
    if high_confidence_only:
        m = m[m["match_confidence"] == "high"]
    if not len(m):
        return pd.DataFrame(columns=cols)
    s = spending.copy()
    s["billing_npi"] = s["billing_npi"].astype(str)
    s["total_paid"] = pd.to_numeric(s["total_paid"], errors="coerce").fillna(0.0)
    s["month_ts"] = pd.to_datetime(s["service_month"].astype(str).str.slice(0, 7),
                                   format="%Y-%m", errors="coerce")
    xw = npi_to_org["npi"].astype(str)
    assert xw.is_unique, "npi_to_org has duplicate NPIs (ambiguous attribution)"
    s["org_node_id"] = s["billing_npi"].map(
        dict(zip(xw, npi_to_org["org_node_id"].astype(str))))
    s = s[s["org_node_id"].notna()]
    if not len(s):
        return pd.DataFrame(columns=cols)

    dead = dict(zip(m["npi"].astype(str), pd.to_datetime(m["deceased_date"])))
    s["dod"] = s["billing_npi"].map(dead)
    s["is_post"] = s["dod"].notna() & (s["month_ts"] >= s["dod"])
    g = s.groupby("org_node_id")
    out = pd.DataFrame({
        "post_death_paid": g.apply(lambda x: float(x.loc[x["is_post"], "total_paid"].sum())),
        "total_paid": g["total_paid"].sum(),
    })
    out["billing_after_death"] = (out["post_death_paid"] / out["total_paid"]).where(
        out["total_paid"] > 0, 0.0).clip(0, 1)
    return out.reset_index()[cols]
