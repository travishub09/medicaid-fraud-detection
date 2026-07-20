"""
test_dossier_batch.py — fan the dossier builder over a ring/lead set.
"""

from __future__ import annotations

import pandas as pd

from src.model_a.dossier_batch import _npis_from_rings, build_batch


def _pack():
    return pd.DataFrame([
        {"npi": "1457794422", "provider_name": "DASARI, NARESH",
         "primary_taxonomy": "207R00000X", "entity_type": "1",
         "addr_city": "CRANSTON", "practice_state": "RI",
         "net_paid": 7_002_672.0, "expected_net_paid": 34_485.0,
         "subscore_facility_code_billing": 0.95},
        {"npi": "1093079006", "provider_name": "PARKS, JUN",
         "primary_taxonomy": "207R00000X", "entity_type": "1",
         "addr_city": "CRANSTON", "practice_state": "RI",
         "net_paid": 5_038_436.0, "expected_net_paid": 35_513.0,
         "subscore_facility_code_billing": 0.95},
    ])


def test_npis_from_rings_explodes_member_list():
    rings = pd.DataFrame({"hcpcs": ["T2046"], "practice_state": ["RI"],
                          "member_npis": ["1457794422; 1093079006; 1457794422"]})
    assert _npis_from_rings(rings) == ["1457794422", "1093079006"]   # deduped, ordered


def test_build_batch_writes_one_per_npi_plus_index(tmp_path):
    written = build_batch(["1457794422", "1093079006", "9999999999"],
                          _pack(), tmp_path)
    names = {p.name for p in written}
    assert names == {"DOSSIER_1457794422.md", "DOSSIER_1093079006.md",
                     "DOSSIER_9999999999.md"}
    idx = (tmp_path / "INDEX.md").read_text()
    assert "3 dossiers generated" in idx
    assert "1457794422" in idx and "CRANSTON RI" in idx
    assert "not in pack" in idx                     # the unknown NPI is flagged
    # a real dossier has the suspect math
    d = (tmp_path / "DOSSIER_1457794422.md").read_text()
    assert "$6,968,187" in d                         # 7,002,672 - 34,485
