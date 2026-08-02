"""
test_editions.py — the file-vintage worksheet: inventory, year inference,
and operator entries surviving a refresh.
"""

from __future__ import annotations

import csv

from src.preflight import scan_editions


def _read(p):
    with open(p, newline="", encoding="utf-8") as fh:
        return {r["file"]: r for r in csv.DictReader(fh)}


def test_inventory_infers_years_and_preserves_entries(tmp_path):
    pre = tmp_path / "preclean"
    (pre / "pos").mkdir(parents=True)
    (pre / "partb").mkdir()
    (pre / "pos" / "pos.csv").write_text("a,b\n1,2\n")
    (pre / "partb" / "partb_2023.csv").write_text("a,b\n1,2\n")
    (pre / "nadac.csv").write_text("a,b\n1,2\n")

    out = scan_editions(tmp_path)
    rows = _read(out)
    assert rows["partb/partb_2023.csv"]["year_in_name"] == "2023"
    assert rows["pos/pos.csv"]["year_in_name"] == ""      # undated → to fill
    assert rows["nadac.csv"]["data_period"] == ""

    # operator fills a period; a refresh must keep it and pick up new files
    rows["pos/pos.csv"]["data_period"] = "2023Q4"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(next(iter(rows.values())).keys()))
        w.writeheader()
        w.writerows(rows.values())
    (pre / "opioid.csv").write_text("a,b\n1,2\n")
    rows2 = _read(scan_editions(tmp_path))
    assert rows2["pos/pos.csv"]["data_period"] == "2023Q4"   # survived
    assert "opioid.csv" in rows2                              # new file added
    assert "EDITIONS.csv" not in rows2                        # never lists itself
