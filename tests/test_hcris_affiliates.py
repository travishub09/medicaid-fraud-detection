"""Affiliate-disclosure check: A-8-1 presence vs PECOS organization owners, on synthetic frames."""
import pandas as pd

from src.ingest_cms.hcris_affiliates import (affiliate_disclosure, facility_year_table,
                                             pecos_org_owners)


def _rows():
    # two Ohio homes, one year: A files A-8-1 (rent 500k on 5M opex), B files none
    mk = lambda ccn, ws, line, col, val: dict(ccn=ccn, npi="1" * 10, fy_begin="01/01/2024", fy_end="12/31/2024",
                                              report_status="2", worksheet=ws, worksheet_name="", line=line,
                                              column=col, kind="number", value=val)
    return pd.DataFrame([
        mk("365001", "A810001", "01000", "00400", "500000"),
        mk("365001", "A810002", "00100", "00400", "PROPCO LLC"),
        mk("365001", "G300000", "00400", "00100", "5000000"),
        mk("365001", "S300001", "00100", "00100", "100"),
        mk("365001", "S300001", "00100", "00700", "30000"),
        mk("365002", "G300000", "00400", "00100", "22000000"),
        mk("365002", "S300001", "00100", "00100", "99"),
        mk("365002", "S300001", "00100", "00700", "31500"),
    ])


def _pecos():
    ccn_to_npi = pd.DataFrame({"ccn": ["365001", "365002"], "npi": ["1000000001", "1000000002"]})
    member = pd.DataFrame({"src_id": ["provider:1000000001", "provider:1000000002"],
                           "dst_id": ["org:a", "org:b"]})
    owned = pd.DataFrame({"src_id": ["org:a", "org:b", "org:b"],
                          "dst_id": ["owner:PROPCO", "owner:HOLDING", "owner:SMITH JOHN"],
                          "owner_role": ["5% OR GREATER DIRECT OWNERSHIP INTEREST",
                                         "OPERATIONAL/MANAGERIAL CONTROL", "CORPORATE OFFICER"]})
    nodes = pd.DataFrame({"owner_key": ["PROPCO", "HOLDING", "SMITH JOHN"], "owner_type": ["O", "O", "I"],
                          "owner_display_name": ["PROPCO LLC", "HOLDING LLC", "SMITH, JOHN"]})
    return ccn_to_npi, member, owned, nodes


def test_gap_flags_the_home_with_org_owners_and_no_a81():
    t = facility_year_table(_rows())
    owners = pecos_org_owners(*_pecos())
    d = affiliate_disclosure(t, owners).set_index("ccn")
    assert d.loc["365001", "has_a81"] and not d.loc["365001", "affiliate_undisclosed"]
    assert abs(d.loc["365001", "affiliate_share_opex"] - 0.1) < 1e-9
    assert not d.loc["365002", "has_a81"] and d.loc["365002", "affiliate_undisclosed"]
    assert d.loc["365002", "n_org_owners"] == 1          # HOLDING counts; SMITH is an individual
    assert d.loc["365002", "org_owner_names"] == "HOLDING LLC"
    assert d.loc["365001", "affiliate_share_pct_state"] == 1.0   # the only disclosing home ranks top


def test_no_org_owner_is_not_a_gap():
    t = facility_year_table(_rows())
    ccn_to_npi, member, owned, nodes = _pecos()
    owned = owned[owned["dst_id"] == "owner:SMITH JOHN"]          # 365002 owned by a person only
    d = affiliate_disclosure(t, pecos_org_owners(ccn_to_npi, member, owned, nodes)).set_index("ccn")
    assert not d.loc["365002", "affiliate_undisclosed"]
