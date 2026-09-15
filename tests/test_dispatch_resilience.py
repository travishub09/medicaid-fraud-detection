"""test_dispatch_resilience.py — the Manus dispatcher never orphans a paid
task: registry saved per create, 429 retried with backoff then reported,
max_new caps a call, re-runs skip what is already dispatched."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.model_a.pursuit_dossier import dispatch_research


class _Resp:
    def __init__(self, code):
        self.status_code = code


class _RateLimited(Exception):
    def __init__(self):
        super().__init__("429 Client Error: Too Many Requests")
        self.response = _Resp(429)


class _Transport:
    """Scripted create(): a list of outcomes per call ('ok' or 'rl')."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def create(self, prompt):
        self.calls += 1
        outcome = self.script.pop(0) if self.script else "ok"
        if outcome == "rl":
            raise _RateLimited()
        return {"task_id": f"t{self.calls}"}


def _prompts(tmp_path: Path, n=5) -> Path:
    p = tmp_path / "prompts.jsonl"
    p.write_text("".join(json.dumps({"org_id": f"org:{i}", "org_name": f"O{i}",
                                     "prompt": "x"}) + "\n" for i in range(n)),
                 encoding="utf-8")
    return p


def test_registry_saved_per_task_and_rate_limit_reported(tmp_path: Path):
    prompts = _prompts(tmp_path)
    reg = tmp_path / "reg.json"
    waits = []
    # 2 ok, then 429 on every attempt for the 3rd (initial + 3 retries)
    t = _Transport(["ok", "ok", "rl", "rl", "rl", "rl"])
    out = dispatch_research(prompts, reg, transport=t, sleep=waits.append)
    assert out["dispatched"] == 2 and out["rate_limited"] is True
    assert waits == [20, 40, 80]
    saved = json.loads(reg.read_text())
    assert set(saved) == {"org:0", "org:1"}          # the two paid tasks are on disk
    # a re-run skips them and continues with the rest
    t2 = _Transport([])
    out2 = dispatch_research(prompts, reg, transport=t2, sleep=waits.append)
    assert out2["dispatched"] == 3 and t2.calls == 3
    assert set(json.loads(reg.read_text())) == {f"org:{i}" for i in range(5)}


def test_transient_429_recovers_after_backoff(tmp_path: Path):
    prompts = _prompts(tmp_path, n=2)
    reg = tmp_path / "reg.json"
    t = _Transport(["rl", "ok", "ok"])              # first call limited once
    out = dispatch_research(prompts, reg, transport=t, sleep=lambda s: None)
    assert out["dispatched"] == 2 and "rate_limited" not in out


def test_max_new_caps_a_call(tmp_path: Path):
    prompts = _prompts(tmp_path, n=5)
    reg = tmp_path / "reg.json"
    out = dispatch_research(prompts, reg, transport=_Transport([]), max_new=2,
                            sleep=lambda s: None)
    assert out["dispatched"] == 2 and out["remaining"] == 3
    assert len(json.loads(reg.read_text())) == 2


def test_non_rate_limit_error_saves_registry_then_raises(tmp_path: Path):
    prompts = _prompts(tmp_path, n=3)
    reg = tmp_path / "reg.json"

    class _Boom(_Transport):
        def create(self, prompt):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("500 server error")
            return {"task_id": f"t{self.calls}"}

    with pytest.raises(RuntimeError):
        dispatch_research(prompts, reg, transport=_Boom([]), sleep=lambda s: None)
    assert set(json.loads(reg.read_text())) == {"org:0"}
