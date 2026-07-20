"""
test_manus_research.py — the Manus research adapter with an injected transport.

No network, no API key: a fake transport returns canned create/get payloads so
the dispatch → poll → extract → cache logic is exercised end to end, plus the
PHI guardrail and the poll-until-terminal loop.
"""

from __future__ import annotations

import pytest

from src.feeds.manus_research import (run_research, state_billing_rule_memo,
                                      ManusTransport)


class _FakeTransport(ManusTransport):
    """Returns 'running' for a few polls, then 'completed' with a result."""

    def __init__(self, polls_until_done=2, status="completed", result=None,
                 create_status="running"):
        self.calls = {"create": 0, "get": 0}
        self._polls_until_done = polls_until_done
        self._status = status
        self._result = result if result is not None else {"answer": "hospice only"}
        self._create_status = create_status

    def create(self, prompt, mode="agent", **opts):
        self.calls["create"] += 1
        self._prompt = prompt
        return {"task_id": "t-123", "status": self._create_status}

    def get(self, task_id):
        self.calls["get"] += 1
        if self.calls["get"] >= self._polls_until_done:
            return {"task_id": task_id, "status": self._status,
                    "structured_output": self._result}
        return {"task_id": task_id, "status": "running"}


def test_dispatch_poll_and_extract():
    t = _FakeTransport(polls_until_done=2)
    out = run_research("public research prompt about code T2046 in Rhode Island",
                       transport=t, sleep=lambda s: None, cache=False)
    assert out["ok"] is True
    assert out["status"] == "completed"
    assert out["task_id"] == "t-123"
    assert out["result"] == {"answer": "hospice only"}
    assert t.calls["create"] == 1 and t.calls["get"] >= 2


def test_failed_task_returns_no_result():
    t = _FakeTransport(polls_until_done=1, status="failed")
    out = run_research("public prompt", transport=t, sleep=lambda s: None,
                       cache=False)
    assert out["ok"] is False and out["result"] is None


def test_timeout_when_never_terminal():
    t = _FakeTransport(polls_until_done=999)   # never reaches done
    out = run_research("public prompt", transport=t, sleep=lambda s: None,
                       cache=False, max_polls=3)
    assert out["ok"] is False and out["timed_out"] is True
    assert t.calls["get"] == 3


def test_phi_guard_blocks_sensitive_prompts():
    t = _FakeTransport()
    for bad in ["look up the patient record for ...",
                "who is the likely whistleblower at this employer",
                "find the beneficiary date of birth"]:
        with pytest.raises(ValueError):
            run_research(bad, transport=t, sleep=lambda s: None, cache=False)
    assert t.calls["create"] == 0        # never dispatched


def test_rule_memo_builds_prompt_and_query_block():
    t = _FakeTransport(polls_until_done=1)
    env = state_billing_rule_memo("T2046", "RI", transport=t,
                                  sleep=lambda s: None, cache=False)
    assert env["query"] == {"task": "state_billing_rule_memo",
                            "code": "T2046", "state": "RI"}
    assert "T2046" in t._prompt and "RI" in t._prompt
    assert "BILLING PROVIDER" in t._prompt


def test_enveloped_payloads_unwrap():
    """A {"data": {"task": {...}}} wrapper style must still parse: id, status,
    and a chat-style messages result."""
    class _Enveloped(ManusTransport):
        def __init__(self):
            self.calls = 0
        def create(self, prompt, mode="agent", **opts):
            return {"data": {"task_id": "t-9", "status": "pending"}}
        def get(self, task_id):
            self.calls += 1
            if self.calls < 2:
                return {"data": {"task": {"task_id": task_id, "status": "running"}}}
            return {"data": {"task": {
                "task_id": task_id, "status": "finished",
                "output": {"messages": [
                    {"role": "assistant", "content": "interim note"},
                    {"role": "assistant", "content": "FINAL: hospice agencies only"},
                ]}}}}
    out = run_research("public billing rules question", transport=_Enveloped(),
                       sleep=lambda s: None, cache=False)
    assert out["ok"] is True
    assert out["task_id"] == "t-9"
    assert out["result"] == "FINAL: hospice agencies only"


def test_missing_task_id_raises():
    class _NoId(ManusTransport):
        def create(self, prompt, mode="agent", **opts):
            return {"status": "running"}       # no task_id
        def get(self, task_id):
            return {}
    with pytest.raises(RuntimeError):
        run_research("public prompt", transport=_NoId(), sleep=lambda s: None,
                     cache=False)
