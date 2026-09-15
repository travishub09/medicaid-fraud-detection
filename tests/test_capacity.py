"""test_capacity.py — physical plausibility: documented-clinician counts from
three sources, beds via the CCN crosswalk, sites, ratios and flags."""

from __future__ import annotations

import pandas as pd

from src.model_a.capacity import (bed_counts, capacity_table, clinician_counts,
                                  is_per_diem, service_family, site_counts, to_markdown)

ORG, DOC, LAB, SNF = "1003000209", "1003000415", "1003000126", "1003000308"


def test_service_family_and_per_diem():
    assert service_family("99213") == "clinic"
    assert service_family("87633") == "lab" and service_family("U0003") == "lab"
    assert service_family("A0427") == "transport" and service_family("E0466") == "dme"
    assert service_family("T1019") == "hcbs" and service_family("S9124") == "hcbs"
    assert service_family("T2046") == "facility" and is_per_diem("T2046")
    assert service_family("0120") == "facility" and is_per_diem("0120")
    assert not is_per_diem("99213")


def _pdim():
    return pd.DataFrame({"npi": [ORG, DOC, LAB, SNF, "1", "2", "3"],
                         "entity_type": ["2", "1", "2", "2", "1", "1", "1"],
                         "addr_key": ["A", "A", "B", "C", "A", "A", "B"]})


def test_clinician_counts_three_sources():
    n2o = pd.DataFrame({"npi": [ORG, DOC, "1", "2"], "org_node_id": ["org:x", "org:x", "org:x", "org:x"]})
    edges = pd.DataFrame({"src_id": ["provider:1", "provider:2", "provider:9", "provider:9"],
                          "dst_id": ["org:x", "org:x", "org:x", "org:x"], "edge_type": "reassigns_to"})
    c = clinician_counts([ORG, DOC, LAB], _pdim(), n2o, edges).set_index("npi")
    assert c.loc[ORG, "n_colocated"] == 3          # DOC, 1, 2 share address A
    assert c.loc[DOC, "n_colocated"] == 2          # an individual does not count itself
    assert c.loc[ORG, "n_reassign"] == 3           # 1, 2, 9 reassign to org:x
    assert c.loc[LAB, "n_reassign"] == 0 and c.loc[LAB, "n_colocated"] == 1


def test_beds_and_sites():
    pos = pd.DataFrame({"ccn": ["111111", "222222"], "bed_count": [60, 120]})
    xw = pd.DataFrame({"ccn": ["111111", "222222"], "npi": [SNF, SNF]})
    b = bed_counts([SNF, ORG], pos, xw)
    assert b[SNF] == 120 and b[ORG] != b[ORG]      # NaN when no CCN
    pl = pd.DataFrame({"NPI": [ORG, ORG, ORG], "Provider Secondary Practice Location Address- Address Line 1":
                       ["1 MAIN ST", "1 main st ", "9 OAK AVE"]})
    s = site_counts([ORG, DOC], pl)
    assert s[ORG] == 3 and s[DOC] == 1


def _facts():
    return {
        ORG: {"paid_total": 12e6, "patient_months": 21 * 100 * 24, "active_months": 24,
              "implied_hours": {"worst_hours_per_working_day": 60.0, "median_hours_per_working_day": 50.0},
              "n_servicing_npis": 2, "top_codes": [{"hcpcs": "99213", "lines": 1000}]},
        DOC: {"paid_total": 3e6, "patient_months": 21 * 30 * 12, "active_months": 12,
              "implied_hours": {"worst_hours_per_working_day": 26.0, "median_hours_per_working_day": 20.0},
              "n_servicing_npis": 1, "top_codes": [{"hcpcs": "99214", "lines": 500}]},
        LAB: {"paid_total": 50e6, "patient_months": 21 * 2000 * 12, "active_months": 12,
              "implied_hours": {}, "n_servicing_npis": 1, "top_codes": [{"hcpcs": "87633", "lines": 9}]},
        SNF: {"paid_total": 8e6, "patient_months": 200 * 12, "active_months": 12,
              "implied_hours": {}, "n_servicing_npis": 1,
              "top_codes": [{"hcpcs": "T2046", "lines": 12 * 30.4 * 150}]},   # 150 patient-days a day
    }


def test_capacity_table_ratios_and_flags():
    counts = pd.DataFrame({"npi": [ORG, DOC, LAB, SNF], "entity_type": ["2", "1", "2", "2"],
                           "addr_key": "", "org_node_id": "", "n_reassign": [3, 0, 0, 0],
                           "n_colocated": [2, 0, 0, 0]})
    beds = pd.Series({ORG: float("nan"), DOC: float("nan"), LAB: float("nan"), SNF: 100.0})
    sites = pd.Series({ORG: 1, DOC: 1, LAB: 1, SNF: 1})
    t = capacity_table(_facts(), counts, beds, sites, {ORG: "BIG CLINIC"}).set_index("npi")
    # org: 60 hours across the largest count (3 reassigning) = 20 h/clinician -> long; 100 patients/3 = 33 -> no
    assert t.loc[ORG, "documented_clinicians"] == 3 and t.loc[ORG, "clinician_basis"].startswith("reassignment")
    assert abs(t.loc[ORG, "hours_per_clinician_day"] - 20.0) < 1e-9 and t.loc[ORG, "flags"] == "long_hours"
    # individual: one person, 26 h -> impossible
    assert t.loc[DOC, "documented_clinicians"] == 1 and t.loc[DOC, "flags"] == "impossible_hours"
    # lab: 2000 patients/day through one clinician, but labs are specimens -> not flagged
    assert t.loc[LAB, "family"] == "lab" and t.loc[LAB, "flags"] == ""
    # facility: 150 patient-days a day on 100 beds -> occupancy 150%
    assert abs(t.loc[SNF, "occupancy"] - 1.5) < 1e-6 and t.loc[SNF, "flags"] == "occupancy_over_100"
    # severity order: impossible/occupancy first
    order = list(capacity_table(_facts(), counts, beds, sites)["npi"])
    assert order.index(ORG) > max(order.index(DOC), order.index(SNF))
    md = to_markdown(capacity_table(_facts(), counts, beds, sites, {ORG: "BIG CLINIC"}), {"spending": "x"})
    assert "3 of 4 NPIs flagged" in md and "occupancy_over_100" in md and "never accusations" in md
