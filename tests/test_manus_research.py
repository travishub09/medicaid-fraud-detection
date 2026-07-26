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


def test_all_seven_task_builders_dispatch_and_stamp_query():
    """Every named research task builds a prompt containing its key inputs,
    passes the PHI guard, and stamps a query block for the dossier record."""
    from src.feeds import manus_research as mr
    cases = [
        (lambda t: mr.state_billing_rule_memo("T2046", "ri", transport=t,
                                              sleep=lambda s: None, cache=False),
         "state_billing_rule_memo", ["T2046", "RI", "BILLING PROVIDER"]),
        (lambda t: mr.state_exclusion_sweep("nm", transport=t,
                                            sleep=lambda s: None, cache=False),
         "state_exclusion_sweep", ["NM", "exclusion"]),
        (lambda t: mr.mfcu_sweep("tn", months=12, transport=t,
                                 sleep=lambda s: None, cache=False),
         "mfcu_sweep", ["TN", "Fraud Control", "12 months"]),
        (lambda t: mr.public_disclosure_screen(
            "1255694451", "a Bronx internal-medicine biller of hospice codes",
            transport=t, sleep=lambda s: None, cache=False),
         "public_disclosure_screen", ["1255694451", "PUBLIC DISCLOSURE"]),
        (lambda t: mr.innocent_explanation_audit(
            "1588799746", "residential per-diem codes on a solo NPI",
            transport=t, sleep=lambda s: None, cache=False),
         "innocent_explanation_audit", ["1588799746", "INNOCENT"]),
        (lambda t: mr.license_discipline_sweep(
            "1457794422", "N. DASARI", "ri", transport=t,
            sleep=lambda s: None, cache=False),
         "license_discipline_sweep", ["1457794422", "RI", "discipline"]),
        (lambda t: mr.address_ground_truth("333 Budlong Rd, Cranston, RI",
                                           transport=t, sleep=lambda s: None,
                                           cache=False),
         "address_ground_truth", ["333 Budlong Rd", "residence"]),
        (lambda t: mr.defendant_solvency("TruCare Inc", "ri", transport=t,
                                         sleep=lambda s: None, cache=False),
         "defendant_solvency", ["TruCare Inc", "bankruptcy"]),
    ]
    for call, task_name, needles in cases:
        t = _FakeTransport(polls_until_done=1)
        env = call(t)
        assert env["ok"] is True, task_name
        assert env["query"]["task"] == task_name
        for n in needles:
            assert n.lower() in t._prompt.lower(), (task_name, n)


def test_missing_task_id_raises():
    class _NoId(ManusTransport):
        def create(self, prompt, mode="agent", **opts):
            return {"status": "running"}       # no task_id
        def get(self, task_id):
            return {}
    with pytest.raises(RuntimeError):
        run_research("public prompt", transport=_NoId(), sleep=lambda s: None,
                     cache=False)


def test_reality_score_dispatches_with_schema_and_query():
    from src.feeds.manus_research import reality_score, REALITY_SCHEMA
    captured = {}
    class _T(ManusTransport):
        def __init__(self): self.calls = 0
        def create(self, prompt, mode="agent", **opts):
            captured["prompt"] = prompt; captured["opts"] = opts
            return {"task_id": "r-1", "status": "completed",
                    "structured_output": {"reality_score": 12,
                                          "gaps": ["no website", "address is a house"],
                                          "confidence": "high"}}
        def get(self, task_id): return {}
    env = reality_score("1588799746", "VEAL LAURA", "ALBUQUERQUE", "NM",
                        transport=_T(), sleep=lambda s: None, cache=False)
    assert env["ok"] and env["result"]["reality_score"] == 12
    assert env["query"] == {"task": "reality_score", "npi": "1588799746"}
    # the live API has no schema field: it must be EMBEDDED in the prompt,
    # never forwarded as a create option
    assert "schema" not in captured["opts"]
    assert "reality_score" in captured["prompt"]      # schema text in prompt
    assert "ONLY a single JSON object" in captured["prompt"]
    assert "1588799746" in captured["prompt"] and "ALBUQUERQUE" in captured["prompt"]


def test_corporate_network_map_dispatches_with_schema():
    from src.feeds.manus_research import corporate_network_map, CORP_NETWORK_SCHEMA
    captured = {}
    class _T(ManusTransport):
        def create(self, prompt, mode="agent", **opts):
            captured["prompt"] = prompt; captured["opts"] = opts
            return {"task_id": "c-1", "status": "completed",
                    "structured_output": {
                        "entities": [{"legal_name": "TRUCARE INC",
                                      "shared_link": "same office 725 Reservoir"}],
                        "shared_nodes": [{"kind": "address",
                                          "value": "725 Reservoir Ave Ste 103",
                                          "n_entities": 3,
                                          "entity_names": ["A", "B", "C"]}],
                        "confidence": "medium"}}
        def get(self, task_id): return {}
    env = corporate_network_map("725 Reservoir Ave Ste 103, Cranston, RI",
                                transport=_T(), sleep=lambda s: None, cache=False)
    assert env["ok"] and env["query"]["task"] == "corporate_network_map"
    assert env["result"]["shared_nodes"][0]["n_entities"] == 3
    assert "schema" not in captured["opts"]           # embedded, not forwarded
    assert "shared_nodes" in captured["prompt"]       # schema text in prompt
    assert "registered agent" in captured["prompt"].lower()
    assert "725 Reservoir" in captured["prompt"]


def test_case_label_harvest_dispatches_and_converts_rows():
    """The harvest task must dispatch with its schema, and harvest_case_rows must
    (a) map rows into case_db.CASE_COLUMNS shape with case_id = source URL,
    (b) fold the conduct period into the summary so extract_conduct_window can
    recover it, (c) DROP rows without a source URL, and (d) route NPI candidates
    to the review frame, never into the case rows."""
    from src.feeds.manus_research import case_label_harvest, harvest_case_rows

    result = {"cases": [
        {"announced_date": "2023-05-01", "defendant_name": "Acme Home Health LLC",
         "sector": "home_health", "scheme": "billing_fraud", "amount_usd": 2500000,
         "qui_tam": True, "intervened": None, "jurisdiction": "D.R.I.",
         "conduct_period": "from 2018 through 2021",
         "summary": "Settled FCA allegations of billing for visits not made.",
         "source_url": "https://www.justice.gov/usao-ri/pr/acme",
         "outcome_type": "settlement",
         "npi_candidates": [{"npi": "1234567893", "registry_name": "ACME HOME HEALTH",
                             "match_basis": "exact name + state"}]},
        {"announced_date": "2023-06-01", "defendant_name": "No Url Corp",
         "summary": "Missing link.", "source_url": "", "outcome_type": "conviction"},
    ]}
    t = _FakeTransport(polls_until_done=1, result=result)
    out = case_label_harvest("RI", 2015, 2025, transport=t,
                             sleep=lambda s: None, cache=False)
    assert out["ok"] and out["query"]["task"] == "case_label_harvest"
    # wide net, tiered: BOTH tiers captured, allegations honestly typed,
    # linkability demanded verbatim
    assert "BOTH TIERS" in t._prompt and "indictment" in t._prompt
    assert "LINKABILITY" in t._prompt and "d/b/a" in t._prompt

    rows, cands = harvest_case_rows(out["result"])
    assert len(rows) == 1                                  # the no-URL row dropped
    r = rows.iloc[0]
    assert r["case_id"] == "https://www.justice.gov/usao-ri/pr/acme"
    assert r["qui_tam"] == 1
    assert r["label_tier"] == "resolved"                   # settlement = tier 1
    assert "from 2018 through 2021" in r["summary"]        # window recoverable
    assert "npi" not in rows.columns                       # candidates never in rows
    assert list(cands["npi"]) == ["1234567893"]

    # the folded window is recoverable by the downstream extractor
    from src.model_a.case_labels import extract_conduct_window
    start, end = extract_conduct_window(r["summary"], r["announced_date"])
    assert (start, end) == (2018, 2021)


# ---- live-verified transport shapes (probed against api.manus.ai 2026-07-25) --

def test_live_create_sends_message_content_list(monkeypatch):
    """task.create must send {"message": {"content": [{"type","text"}]}} —
    every other shape 400s with 'message.content is required' on the live API.
    mode/schema must never ride in the body."""
    captured = {}

    class _R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"ok": True, "task_id": "t-1"}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"], captured["body"], captured["headers"] = url, json, headers
        return _R()

    monkeypatch.setattr("requests.post", fake_post)
    t = ManusTransport(api_key="k")
    out = t.create("do the research", mode="agent")
    assert out["task_id"] == "t-1"
    assert captured["url"].endswith("/task.create")
    assert captured["body"] == {
        "message": {"content": [{"type": "text", "text": "do the research"}]}}
    assert captured["headers"]["x-manus-api-key"] == "k"


def test_live_get_polls_listmessages_and_synthesizes(monkeypatch):
    """Polling is GET /task.listMessages?task_id=. The newest status_update's
    agent_status 'stopped' maps to completed; the newest assistant_message's
    content is the output. (Payload copied from the live probe.)"""
    payload = {"ok": True, "task_id": "JPEB", "has_more": False, "messages": [
        {"id": "n1", "type": "status_update", "timestamp": "1785003616335",
         "status_update": {"agent_status": "stopped",
                           "brief": "Manus finished working"}},
        {"id": "a1", "type": "assistant_message", "timestamp": "1785003616160",
         "assistant_message": {"content": "ok"}},
        {"id": "s1", "type": "status_update", "timestamp": "1785003614188",
         "status_update": {"agent_status": "running"}},
        {"id": "u1", "type": "user_message", "timestamp": "1785003613597",
         "user_message": {"content": "Reply with exactly the single word: ok"}},
    ]}
    captured = {}

    class _R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return payload

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"], captured["params"] = url, params
        return _R()

    monkeypatch.setattr("requests.get", fake_get)
    t = ManusTransport(api_key="k")
    out = t.get("JPEB")
    assert captured["url"].endswith("/task.listMessages")
    assert captured["params"] == {"task_id": "JPEB"}
    assert out["status"] == "completed" and out["output"] == "ok"

    # still running: newest status_update says running, no answer yet
    payload["messages"] = payload["messages"][2:]
    out = t.get("JPEB")
    assert out["status"] == "running" and out["output"] is None


def test_schema_embedded_and_json_reply_parsed():
    """With the API lacking a structured-output field, run_research must fold
    the schema into the prompt and parse the JSON reply text, including a
    fenced reply."""
    t = _FakeTransport(polls_until_done=1)
    t._result = '```json\n{"cases": [{"defendant_name": "X"}]}\n```'
    out = run_research("public prompt", transport=t, sleep=lambda s: None,
                       cache=False, schema={"type": "object",
                                            "properties": {"cases": {}}})
    assert "ONLY a single JSON object" in t._prompt
    assert '"cases"' in t._prompt                      # schema text embedded
    assert out["result"] == {"cases": [{"defendant_name": "X"}]}


def test_waiting_agent_is_terminal_not_ok():
    """agent_status 'waiting' means the agent wants human input an unattended
    task can never give — the poll loop must stop and report not-ok, not spin
    for 30 minutes."""
    t = _FakeTransport(polls_until_done=1, status="waiting")
    out = run_research("public prompt", transport=t, sleep=lambda s: None,
                       cache=False)
    assert out["ok"] is False and out["status"] == "waiting"
    assert t.calls["get"] <= 2                         # stopped immediately


def test_transient_poll_errors_are_retried_not_fatal():
    """One 500 on task.listMessages must not abandon a task that is still
    running server-side: consecutive-failure counting, reset on success."""
    class _Flaky(_FakeTransport):
        def get(self, task_id):
            self.calls["get"] += 1
            if self.calls["get"] in (1, 3):          # sporadic 500s
                raise RuntimeError("500 Server Error: Internal Server Error")
            if self.calls["get"] >= 4:
                return {"task_id": task_id, "status": "completed",
                        "structured_output": {"answer": "survived"}}
            return {"task_id": task_id, "status": "running"}
    out = run_research("public prompt", transport=_Flaky(),
                       sleep=lambda s: None, cache=False)
    assert out["ok"] is True and out["result"] == {"answer": "survived"}


def test_six_consecutive_poll_errors_give_up():
    class _Dead(_FakeTransport):
        def get(self, task_id):
            self.calls["get"] += 1
            raise RuntimeError("500 Server Error")
    t = _Dead()
    out = run_research("public prompt", transport=t,
                       sleep=lambda s: None, cache=False)
    assert out["ok"] is False
    assert "500" in (out["poll_error"] or "")
    assert t.calls["get"] == 6                       # gave up at the cap


def test_resume_research_reattaches_by_task_id():
    """A timed-out run's task keeps working server-side; resume_research must
    poll it to completion WITHOUT creating a new task, and parse a schema
    reply."""
    from src.feeds.manus_research import resume_research
    t = _FakeTransport(polls_until_done=2)
    t._result = '{"cases": []}'
    out = resume_research("t-123", transport=t, sleep=lambda s: None,
                          cache=False, schema={"type": "object"})
    assert t.calls["create"] == 0                    # never re-dispatched
    assert out["ok"] is True and out["result"] == {"cases": []}
    assert out["task_id"] == "t-123"


def test_pending_allegations_are_tiered_never_hard_label():
    """An indictment must be CAPTURED (comprehensiveness) but tiered 'pending'
    so it can never enter the hard label; linkage identifiers come through
    verbatim and joined."""
    from src.feeds.manus_research import harvest_case_rows

    result = {"cases": [
        {"announced_date": "2024-01-10", "defendant_name": "Dr. A Person",
         "summary": "Indicted for billing for visits not made.",
         "source_url": "https://www.justice.gov/usao/pr/indicted",
         "outcome_type": "indictment",
         "npi_in_source": "1234567893",
         "license_numbers": ["MD-12345", "MD-99"],
         "addresses": ["1 Main St, Providence, RI"],
         "dba_names": ["A Person Clinic"],
         "related_individuals": [{"name": "B Owner", "role": "co-owner"},
                                 {"name": "C Biller"}],
         "states_involved": ["RI", "MA"]},
        {"announced_date": "2024-02-01", "defendant_name": "Settled Org LLC",
         "summary": "Paid to resolve FCA allegations.",
         "source_url": "https://www.justice.gov/usao/pr/settled",
         "outcome_type": "settlement"},
    ]}
    rows, _ = harvest_case_rows(result)
    tiers = dict(zip(rows["defendant_name"], rows["label_tier"]))
    assert tiers["Dr. A Person"] == "pending"
    assert tiers["Settled Org LLC"] == "resolved"
    r = rows[rows["defendant_name"] == "Dr. A Person"].iloc[0]
    assert r["npi_in_source"] == "1234567893"
    assert r["license_numbers"] == "MD-12345 | MD-99"
    assert r["related_individuals"] == "B Owner (co-owner) | C Biller"
    assert r["states_involved"] == "RI | MA"
