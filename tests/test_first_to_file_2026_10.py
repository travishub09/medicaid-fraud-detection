"""test_first_to_file_2026_10.py — the named-party first-to-file check over a fake
CourtListener transport: the query shape, the FCA flag, the pagination, the report."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.enforcement.first_to_file import _query_for, check_names, to_markdown


def _fake(url, params=None, headers=None):
    q = (params or {}).get("q", "") if params else ""
    if params is None and url.endswith("page2"):
        return {"results": [{"docket_id": 3, "caseName": "Doe v. Acme Home Care LLC", "court_id": "nyed",
                             "dateFiled": "2019-02-01", "suitNature": "442 Civil Rights: Jobs", "party": ["Acme Home Care LLC"]}],
                "next": None}
    if "Acme Home Care" in q:
        return {"results": [
            {"docket_id": 1, "caseName": "United States ex rel. Roe v. Acme Home Care LLC", "court_id": "nyed",
             "dateFiled": "2024-05-01", "dateTerminated": "", "suitNature": "376 Qui Tam (31 U.S.C. 3730(b))",
             "docketNumber": "1:24-cv-01234", "party": ["Acme Home Care LLC", "United States"],
             "docket_absolute_url": "/docket/1/"},
            {"docket_id": 2, "caseName": "Smith v. Acme Home Care LLC", "court_id": "nyed", "dateFiled": "2021-01-01",
             "suitNature": "442 Civil Rights: Jobs", "party": ["Acme Home Care LLC"]}],
            "next": "https://www.courtlistener.com/page2"}
    return {"results": [], "next": None}


def test_query_shapes():
    assert _query_for("ANDES, SCOTT") == 'party:("SCOTT ANDES") OR caseName:("SCOTT ANDES")'
    assert _query_for("Acme Home Care LLC") == 'party:("Acme Home Care") OR caseName:("Acme Home Care")'
    assert _query_for("Norris Morrison DPM Inc") == 'party:("Norris Morrison DPM") OR caseName:("Norris Morrison DPM")'


def test_check_names_flags_fca_and_pages(tmp_path: Path):
    t = check_names(["Acme Home Care LLC", "Nobody Corp"], fetch_json=_fake, raw_root=tmp_path, sleep_s=0)
    acme = t[t["query"] == "Acme Home Care LLC"]
    assert len(acme) == 3 and acme.iloc[0]["fca_shaped"] and acme.iloc[0]["docket_id"] == "1"   # FCA first
    assert not acme.iloc[1]["fca_shaped"] and not acme.iloc[2]["fca_shaped"]
    assert len(t[t["query"] == "Nobody Corp"]) == 0
    md = to_markdown(t, ["Acme Home Care LLC", "Nobody Corp"], None)
    assert "sealed qui tam" in md and "YES | 2024-05-01" in md and "No public docket names this party" in md


def test_cli_needs_names(tmp_path: Path):
    r = subprocess.run([sys.executable, "-m", "src.enforcement.first_to_file", "--out", str(tmp_path / "o")],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "no names" in r.stderr
