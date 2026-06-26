"""
test_hrsa_340b_xlsx.py — load the native OPAIS Covered Entity Daily Report .xlsx.

The native HRSA download is a 3-worksheet Excel (Covered Entities / Shipping
Addresses / Contract Pharmacies) with a title + "Exported On" banner above the real
header row. ``load_opais`` must read the Contract Pharmacies worksheet at the
(entity x pharmacy) grain and merge State from the Covered Entities worksheet, so the
raw file drops straight into ``covered_entities`` with no manual worksheet export.
"""

from __future__ import annotations

import pandas as pd
import pytest

pytest.importorskip("openpyxl")


def _write_opais(path):
    """A miniature OPAIS Covered Entity Daily Report with the real banner layout
    (title in row 1, headers in row 3 → header=2)."""
    cov = pd.DataFrame({
        "340B ID": ["CAH01-00", "DSH02-00"],
        "Entity Name": ["WRANGELL MEDICAL CENTER", "UH PORTAGE MEDICAL CENTER"],
        "Entity Type": ["CAH", "DSH"],
        "State": ["AK", "OH"],
    })
    pharm = pd.DataFrame({
        "340B ID": ["CAH01-00", "CAH01-00", "CAH01-00", "DSH02-00"],
        "Entity Name": ["WRANGELL MEDICAL CENTER"] * 3 + ["UH PORTAGE MEDICAL CENTER"],
        "Entity Type": ["CAH", "CAH", "CAH", "DSH"],
        "Pharmacy Name": ["CVS 001", "WALGREENS 002", "RITE AID 003", "CVS 001"],
    })
    ship = pd.DataFrame({"340B ID": ["CAH01-00"], "Shipping Address 1": ["310 BENNETT ST"]})
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        for name, df in [("Covered Entities", cov), ("Shipping Addresses", ship),
                         ("Contract Pharmacies", pharm)]:
            # banner: title row + a spacer, real header on the 3rd row (startrow=2)
            pd.DataFrame({"Public Covered Entity Daily Report": []}).to_excel(
                xl, sheet_name=name, index=False)
            df.to_excel(xl, sheet_name=name, index=False, startrow=2)


def test_load_opais_xlsx_picks_pharmacy_grain_and_merges_state(tmp_path):
    from src.ingest_cms.hrsa_340b import load_opais, covered_entities
    p = tmp_path / "opais.xlsx"
    _write_opais(p)

    raw = load_opais(str(p))
    # the Contract Pharmacies worksheet (entity x pharmacy grain), not the entity sheet
    assert "Pharmacy Name" in raw.columns and len(raw) == 4
    # State merged in from the Covered Entities worksheet on 340B ID
    assert "State" in raw.columns
    assert set(raw.loc[raw["340B ID"] == "CAH01-00", "State"]) == {"AK"}

    ents = covered_entities(raw).set_index("entity_id")
    assert ents.loc["CAH01-00", "n_contract_pharmacies"] == 3   # 3 distinct pharmacies
    assert ents.loc["DSH02-00", "n_contract_pharmacies"] == 1
    assert ents.loc["CAH01-00", "state"] == "AK"
    assert ents.loc["CAH01-00", "entity_type"] == "CAH"


def test_load_opais_csv_passthrough(tmp_path):
    from src.ingest_cms.hrsa_340b import load_opais
    p = tmp_path / "opais.csv"
    pd.DataFrame({"340B ID": ["X-00"], "Entity Name": ["ACME"],
                  "Pharmacy Name": ["CVS"]}).to_csv(p, index=False)
    raw = load_opais(str(p))
    assert list(raw["Entity Name"]) == ["ACME"]
