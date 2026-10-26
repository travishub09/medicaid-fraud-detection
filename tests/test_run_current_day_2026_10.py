"""test_run_current_day_2026_10.py — the current-day runner (the operational list; no
freeze, no ruler): syntax, the stop guards, and that it never passes an as-of cutoff."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

RUNNER = Path("ops/vm/run_current_day.sh")


def test_runner_syntax_and_guards():
    assert subprocess.run(["bash", "-n", str(RUNNER)]).returncode == 0
    s = RUNNER.read_text()
    assert "set -uo pipefail" in s
    cmds = "\n".join(ln for ln in s.splitlines() if ln.lstrip().startswith("python"))
    assert "--asof-cutoff" not in cmds and "--asof-nppes" not in cmds    # current day, never a freeze
    assert "--case-db" in s and "--defendant-npis" in s                   # the case labels are the point
    assert "--no-servicing-rows" in s
    assert "NO DOJ case labels" in s                                      # the v8 lesson, carried
    assert "run_frozen_night.sh" in s and "never beside it" in s          # never beside a frozen run
    assert "attending_concentration" in s and "contradictions" in s      # the reports the scorer cannot see
    assert "--dispatch" not in cmds                                     # the dossier is never sent by a runner
    # every $VAR inside an echo is guarded or defined above set -u (the v26 crash class)
    for m in re.finditer(r"\$\{([A-Z_]+)\}", s):
        name = m.group(1)
        assert f"{name}=" in s[:m.start()] or f"${{{name}:-" in s or f"${{{name}+" in s, name


def test_resume_gate_rejects_unknown_step(tmp_path: Path):
    s = RUNNER.read_text()
    head = s.split("[ -n \"$RESUME_FROM\" ] && echo")[0]
    probe = head.replace('TAG="${1:?tag, e.g. cd1}"', 'TAG=t') \
                .replace('exec > >(tee -a "$LOG") 2>&1', '') + '\nrun_step 2; echo "rc=$?"\n'
    probe = re.sub(r'mkdir -p .*\n', '', probe).replace('cd "$REPO"', '')
    p = tmp_path / "probe.sh"
    p.write_text(probe)
    r = subprocess.run(["bash", p.as_posix()], capture_output=True, text=True,
                       env={"RESUME_FROM": "zz", "MEDICAID_DATA_ROOT": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert "unknown RESUME_FROM" in r.stdout + r.stderr
    r = subprocess.run(["bash", p.as_posix()], capture_output=True, text=True,
                       env={"RESUME_FROM": "4", "MEDICAID_DATA_ROOT": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert "step 2 skipped" in r.stdout and "rc=1" in r.stdout
