"""The frozen runner stops when a source v30 declares did not produce features (2026-11-07).

`servicing_mix` and `clia` join STOP_SOURCES, and CLIA needs its own success-line check:
`_SourceAudit` keeps 'used' over a later 'skipped', and CLIA logs its file-load line before it
links certificates to NPIs, so a failed link would still read as used in SOURCES_REPORT.md.
These tests run the runner's OWN lines (cut from ops/vm/run_frozen_night.sh) in bash against a
planted run folder, so the test breaks if the script's guard changes."""
from __future__ import annotations

import subprocess
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "ops" / "vm" / "run_frozen_night.sh"


def _guard_block() -> str:
    lines = RUNNER.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("STOP_SOURCES="))
    end = next(i for i, l in enumerate(lines) if i > start and "NPIs with a CLIA certificate" in l
               and l.startswith("grep"))
    return "\n".join(lines[start:end + 1])


def _row(src: str, used: bool, detail: str = "detail") -> str:
    return f"| `{src}` | {'✅ used' if used else '⏭️ skipped'} | {detail} |"


def _run(tmp_path: Path, rows: list[str], log: str) -> subprocess.CompletedProcess:
    run = tmp_path / "run"
    run.mkdir()
    (run / "SOURCES_REPORT.md").write_text(
        "# SOURCES_REPORT\n\n| source | status | detail |\n|---|---|---|\n" + "\n".join(rows) + "\n",
        encoding="utf-8")
    (run / "night_run.log").write_text(log, encoding="utf-8")
    script = ("set -uo pipefail\nTAG=vtest\nEMBEDDINGS=0\nRUN=" + str(run) + "\n"
              "fail() { echo \"FAILED at step $1\"; exit 3; }\n" + _guard_block() + "\necho GUARD_PASSED\n")
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)


OK_ROWS = [_row("servicing_mix", True, "617,062 billing NPIs from spending_asof_2023-12.parquet"),
           _row("clia", True, "686,058 rows from pos_clia_202609.csv")]
OK_LOG = "    [clia] 4,321 NPIs with a CLIA certificate from pos_clia_202609.csv: 3,000 with a volume ratio\n"


def test_v30_sources_are_on_the_stop_list():
    first = _guard_block().splitlines()[0]                     # STOP_SOURCES="a b c"; ...
    names = first.split('"')[1].split()
    assert {"servicing_mix", "clia"} <= set(names)
    assert {"servicing", "mue", "sector_schemes", "medicaid_enrollment", "billing_lm"} <= set(names)


def test_both_sources_used_and_clia_linked_passes(tmp_path):
    r = _run(tmp_path, OK_ROWS, OK_LOG)
    assert r.returncode == 0 and "GUARD_PASSED" in r.stdout, r.stdout + r.stderr


def test_skipped_servicing_mix_stops_the_run(tmp_path):
    rows = [_row("servicing_mix", False, "needs processed/spending_fact.parquet + provider_dim.parquet"),
            OK_ROWS[1]]
    r = _run(tmp_path, rows, OK_LOG)
    assert r.returncode == 3 and "source servicing_mix was SKIPPED" in r.stdout


def test_skipped_clia_stops_the_run(tmp_path):
    rows = [OK_ROWS[0], _row("clia", False, "no preclean/clia/*.csv")]
    r = _run(tmp_path, rows, "")
    assert r.returncode == 3 and "source clia was SKIPPED" in r.stdout


def test_clia_used_but_never_linked_stops_the_run(tmp_path):
    """The case the stop list alone misses: the file loaded (row reads 'used'), no NPI linked."""
    log = ("    [clia] 686,058 rows from pos_clia_202609.csv; resolved clia_number=PRVDR_NUM\n"
           "    [clia] skipped: no certificate linked to an NPI\n")
    r = _run(tmp_path, OK_ROWS, log)
    assert r.returncode == 3 and "CLIA produced no linked certificates" in r.stdout


def test_used_row_with_the_word_skipped_in_its_detail_does_not_stop(tmp_path):
    """The v29 lesson: only the STATUS cell decides, never the detail text."""
    rows = [_row("servicing_mix", True, "617,062 billing NPIs; an inner check skipped: nothing to do"),
            OK_ROWS[1]]
    r = _run(tmp_path, rows, OK_LOG)
    assert r.returncode == 0 and "GUARD_PASSED" in r.stdout, r.stdout + r.stderr
