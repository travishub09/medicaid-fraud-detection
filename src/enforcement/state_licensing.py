"""
state_licensing.py — state licensing-board discipline → exclusion schema (B9).

Source: per-state medical/nursing/pharmacy board disciplinary actions (free, but
every board publishes a different format). Board discipline is sanction-adjacent
intelligence beyond the OIG LEIE — a provider disciplined by their state board is
an integrity signal and, later, a person-level credibility marker. We normalize
each state's list into the shared ``exclusions`` schema so the rows become
exclusion-adjacent nodes in the graph exactly like LEIE/SAM.

  normalize_state_licensing(raw, column_map, state, action_to_exclude=...)
      map a state's columns → npi/entity_name/name_key/excl_type/excl_date/
      reinstate_date/currently_active. ``column_map`` names the state's actual
      headers (each board differs); only ADVERSE actions (revocation, suspension,
      surrender) become exclusion rows — routine renewals are skipped.

Person-side credibility use waits on the people-data/FCRA gate; the org/provider
exclusion-node use is available now.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import canonicalize_series
from src.entity_graph.resolve_entities import norm_org_name

EXCLUSION_COLS = ["npi", "entity_name", "name_key", "excl_type", "excl_date",
                  "reinstate_date", "currently_active"]
# action keywords that count as an adverse (exclusion-equivalent) board action
ADVERSE_ACTIONS = ("revok", "revoc", "suspend", "surrender", "bar", "exclud",
                   "denial", "probation")


def normalize_state_licensing(raw: pd.DataFrame, column_map: dict[str, str],
                              state: str,
                              adverse_actions: tuple[str, ...] = ADVERSE_ACTIONS
                              ) -> pd.DataFrame:
    """One state's disciplinary list → rows in the shared exclusions schema.

    ``column_map`` maps logical → the state's actual header for any of:
    name, npi, action, action_date, reinstate_date. Only rows whose action text
    contains an adverse keyword are kept (a license renewal is not an exclusion).
    """
    df = raw.rename(columns={v: k for k, v in column_map.items()}).copy()
    if "name" not in df.columns:
        raise ValueError(f"state licensing column_map must map 'name'; "
                         f"got {list(column_map)}")
    action = (df["action"] if "action" in df.columns else "").fillna("").astype(str).str.lower()
    keep = action.apply(lambda a: any(k in a for k in adverse_actions)) \
        if "action" in df.columns else pd.Series(True, index=df.index)
    df = df[keep].copy()
    if not len(df):
        return pd.DataFrame(columns=EXCLUSION_COLS)

    npi = (canonicalize_series(df["npi"]) if "npi" in df.columns
           else pd.Series(pd.NA, index=df.index))
    out = pd.DataFrame({
        "npi": npi.fillna("").astype(str),
        "entity_name": df["name"].fillna("").astype(str),
        "name_key": df["name"].map(norm_org_name),
        "excl_type": (f"state_board:{state.upper()}:"
                      + (df["action"].fillna("").astype(str) if "action" in df.columns else "")),
        "excl_date": pd.to_datetime(df.get("action_date"), errors="coerce"),
        "reinstate_date": pd.to_datetime(df.get("reinstate_date"), errors="coerce"),
    })
    out["currently_active"] = out["reinstate_date"].isna().astype(int)
    return out[out["name_key"] != ""].reset_index(drop=True)[EXCLUSION_COLS]
