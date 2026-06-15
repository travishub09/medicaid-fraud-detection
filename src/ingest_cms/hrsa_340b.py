"""
hrsa_340b.py — HRSA 340B OPAIS covered entities + contract pharmacies (sweep E1).

Source: the 340B OPAIS daily public report (free Excel, 340bopais.hrsa.gov). The
340B drug-discount program is a recurring fraud theater (diversion, duplicate
discounts, contract-pharmacy schemes). The structural signal we can compute from
the public file: a covered entity with an unusually large CONTRACT-PHARMACY
footprint — many pharmacies dispensing on its 340B eligibility — is the
arbitrage/diversion shape the manifesto's pharmacy typology targets.

  covered_entities        parse OPAIS → entity_id, name_key (shared normalizer),
                          entity_type, state, n_contract_pharmacies.
  attach_340b             join to org nodes by name_key → is_340b_covered_entity
                          flag + contract_pharmacy_concentration (0–1, bounded),
                          feeding the new ``contract_pharmacy`` scheme.

Match is name-key based (OPAIS has no NPI), so the join is conservative and the
flag is corroborative context, not proof. Dormant until the file is loaded.
"""

from __future__ import annotations

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns
from src.entity_graph.resolve_entities import norm_org_name

PHARMACY_SATURATION = 25        # contract pharmacies at/above this → concentration 1.0

OPAIS_COLS = {
    "entity_id": ["340B ID", "ID_340B", "id_340b", "Entity ID", "entity_id"],
    "name": ["Entity Name", "entity_name", "Covered Entity Name", "Name"],
    "entity_type": ["Entity Type", "entity_type", "Entity Subtype"],
    "state": ["State", "state", "Entity State"],
    "contract_pharmacy": ["Contract Pharmacy Name", "contract_pharmacy_name",
                          "Pharmacy Name"],
}


def covered_entities(raw: pd.DataFrame) -> pd.DataFrame:
    """OPAIS rows → one row per covered entity with its contract-pharmacy count.

    OPAIS lists one row per (entity × contract pharmacy); we group to the entity
    and count distinct pharmacies. Returns entity_id, name_key, entity_type,
    state, n_contract_pharmacies.
    """
    resolved = _resolve_columns(list(raw.columns), OPAIS_COLS)
    if "name" not in resolved:
        raise ValueError(f"340B OPAIS file missing an entity-name column; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()
    df["name_key"] = df["name"].map(norm_org_name)
    for c in ("entity_id", "entity_type", "state", "contract_pharmacy"):
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str).str.strip()
    df = df[df["name_key"] != ""]

    g = df.groupby(["entity_id", "name_key"], dropna=False)
    out = pd.DataFrame({
        "entity_type": g["entity_type"].agg(lambda s: s.mode().iat[0] if len(s) else ""),
        "state": g["state"].agg(lambda s: s.mode().iat[0] if len(s) else ""),
        "n_contract_pharmacies": g["contract_pharmacy"].apply(
            lambda s: int(s[s != ""].nunique())),
    }).reset_index()
    return out


def attach_340b(features: pd.DataFrame, org_nodes: pd.DataFrame,
                entities: pd.DataFrame,
                saturation: int = PHARMACY_SATURATION) -> pd.DataFrame:
    """Attach is_340b_covered_entity + contract_pharmacy_concentration to org
    features by name_key. No fan-out (max per org name_key); asserted."""
    ent = entities.copy()
    by_key = ent.groupby("name_key", as_index=False)["n_contract_pharmacies"].max()
    pharm = dict(zip(by_key["name_key"], by_key["n_contract_pharmacies"]))

    orgs = org_nodes.set_index("org_node_id")

    def _key(org_id: str) -> str:
        if org_id not in orgs.index:
            return ""
        row = orgs.loc[org_id]
        names = {str(row.get("org_name", "") or "")}
        names.update(a.strip() for a in str(row.get("aliases", "") or "").split(";"))
        for n in names:
            k = norm_org_name(n)
            if k in pharm:
                return k
        return ""

    out = features.copy()
    n0 = len(out)
    keys = out["org_node_id"].astype(str).map(_key)
    out["is_340b_covered_entity"] = (keys != "").astype(int)
    counts = keys.map(pharm).fillna(0.0)
    out["contract_pharmacy_concentration"] = (counts / float(saturation)).clip(upper=1.0)
    assert len(out) == n0, "340B attach fan-out"
    return out
