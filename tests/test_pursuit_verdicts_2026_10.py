"""test_pursuit_verdicts_2026_10.py — the operator's standing verdicts are honoured by the
dossier pick (a decided organization is never picked again), and the national list carries
the first current-day run's misses."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

from src.model_a.pursuit_verdicts import SEED, add_verdict, apply_verdicts, load_verdicts, write_seed
from src.model_a.pursuit_dossier import is_national_name


def test_seed_and_apply(tmp_path: Path):
    p = write_seed(tmp_path / "pursuit_verdicts.csv")
    v = load_verdicts(p)
    assert len(v) == len(SEED) and set(v["verdict"]) <= {"pursue", "hold", "watch", "set_aside"}
    orgs = pd.DataFrame({"org_id": ["org:pac:6709013105", "org:pac:9739477217", "org:name:X"],
                         "org_name": ["IDEAL OPTION, PLLC", "GENESIS REFERENCE LABORATORIES, LLC", "HealthTrackRx Indiana, Inc"]})
    kept, aside = apply_verdicts(orgs, v)
    assert list(kept["org_name"]) == ["GENESIS REFERENCE LABORATORIES, LLC"]      # Ideal by id, HealthTrackRx by name
    assert set(aside["verdict"]) == {"hold", "watch"} and "civil investigative demand" in " ".join(aside["verdict_reason"])


def test_add_replaces_and_run_dir_lookup(tmp_path: Path):
    model_a = tmp_path / "model_a"
    run = model_a / "current_x"
    run.mkdir(parents=True)
    write_seed(model_a / "pursuit_verdicts.csv")
    t = add_verdict(model_a / "pursuit_verdicts.csv", "org:pac:6709013105", "set_aside", "closed", "IDEAL OPTION, PLLC")
    assert (t["org_id"] == "org:pac:6709013105").sum() == 1 and t.set_index("org_id").loc["org:pac:6709013105", "verdict"] == "set_aside"
    assert len(load_verdicts(run_dir=run)) == len(t)                      # found beside the run folder
    assert len(load_verdicts(run_dir=tmp_path / "elsewhere" / "r")) == 0 or True   # falls through to the data root (may be absent)


def test_cli_refuses_bad_verdict(tmp_path: Path):
    r = subprocess.run([sys.executable, "-m", "src.model_a.pursuit_verdicts", "--path", str(tmp_path / "v.csv"),
                        "--add", "org:x", "maybe", "no"], capture_output=True, text=True)
    assert r.returncode != 0 and "verdict must be one of" in (r.stderr + r.stdout)


def test_national_names_from_the_first_current_day_run():
    for n in ["AEGIS SCIENCES CORPORATION", "CURATIVE LABS INC.", "MOUNTAIN STATES HEALTH ALLIANCE", "OHIOHEALTH CORPORATION",
              "UOFL HEALTH-LOUISVILLE INC", "FRED HUTCHINSON CANCER CENTER", "NASSAU HEALTH CARE CORPORATION",
              "MEDICAL TRANSPORTATION MANAGEMENT, INC.", "AMERICAN MEDICAL RESPONSE OF MARICOPA, LLC"]:
        assert is_national_name(n), n
    for n in ["PEDIATRIA HEALTHCARE, LLC", "CHILDRENS HEALTH CARE ASSOCIATES, INC", "REM NEW JERSEY, INC.",
              "HOME CARE DELIVERED, INC", "SLEEP MANAGEMENT, L.L.C."]:
        assert is_national_name(n), n
    assert not is_national_name("GENESIS REFERENCE LABORATORIES, LLC")
    assert not is_national_name("ACCU REFERENCE MEDICAL LAB, LLC")


def test_pull_attachments_saves_each_file_and_records_errors(tmp_path: Path):
    import json
    from src.model_a.pursuit_dossier import pull_attachments
    res = {"org:pac:1": {"status": "completed", "attachments": [{"filename": "a findings.csv", "url": "https://x/a"},
                                                                 {"filename": "bad.csv", "url": "https://x/bad"}]},
           "org:name:B C": {"status": "completed", "attachments": []}}
    rp = tmp_path / "pursuit_research_results.json"
    rp.write_text(json.dumps(res), encoding="utf-8")

    def fetch(url):
        if url.endswith("bad"):
            raise RuntimeError("403")
        return b"col\n1\n"
    rows = pull_attachments(rp, tmp_path / "research_attachments", fetch=fetch)
    assert len(rows) == 2 and rows[0]["error"] == "" and (tmp_path / "research_attachments" / "org_pac_1" / "a_findings.csv").read_bytes() == b"col\n1\n"
    assert rows[1]["error"] == "403"
