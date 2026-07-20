"""
manus_research.py — dispatch Manus browser-agent research on the top-N leads.

The unstructured-web research this project needs (state Medicaid billing-rule
memos, corporate/address resolution, license and sanctions checks) cannot come
from a fixed API — it takes an agent that reads PDFs and cross-references
registries. Manus is that agent. This module wraps its API as one more feed:
dispatch a task, poll to completion, cache the result with its citations and
retrieval date, return structured evidence.

CONTRACT (Manus v2, https://open.manus.ai/docs/v2): base https://api.manus.ai/v2,
auth header ``x-manus-api-key``, ``POST /v2/task.create`` to dispatch and
``POST /v2/task.get`` to poll. Field names beyond the documented ones are read
defensively (several aliases tried) so a minor API rename does not break a run.

RULES this module enforces, matching the rest of the feeds layer:
  * TOP-N ONLY. Manus tasks are slow and paid; callers pass a handful of leads,
    never the universe.
  * EVIDENCE, NOT FEATURES. A live-fetched value is not reproducible
    point-in-time; its output feeds dossiers and (with human review) labels,
    never a trainable column.
  * PUBLIC IDENTIFIERS ONLY OUT. ``run_research`` refuses prompts that look like
    they carry PHI or the internal whistleblower inference.
  * VERIFY, DON'T TRUST. The agent can be wrong; results carry their citations
    and a confidence field, and load-bearing claims get confirmed against
    primary sources.
  * CACHED + STAMPED. Every raw response is written under feeds/raw/manus/.

The transport is injectable, so tests never touch the network and no key is
needed to exercise the logic.
"""

from __future__ import annotations

import os
import re
import time

from src.feeds.client import cache_raw

MANUS_BASE = "https://api.manus.ai/v2"
API_KEY_ENV = "MANUS_API_KEY"

# terminal task states (documented + defensive aliases)
_DONE_OK = {"completed", "success", "succeeded", "finished", "done"}
_DONE_BAD = {"failed", "error", "errored", "stopped", "cancelled", "canceled"}

# result field aliases, tried in order
_RESULT_FIELDS = ["structured_output", "output", "result", "final_output",
                  "answer", "text", "content", "summary"]

# crude guards so we never ship PHI or the sensitive inference to an external
# agent. These are a backstop for a caller mistake, not a substitute for passing
# public identifiers only.
# No trailing \b: the stem terms (diagnos, whistleblow) must match their
# inflections (diagnosis, whistleblower), which a closing boundary would block.
_PHI_MARKERS = re.compile(
    r"\b(patient|beneficiar|member id|medical record|mrn|diagnos|dob|"
    r"date of birth|ssn|whistleblow|relator|likely.{0,20}witness)", re.IGNORECASE)


class ManusTransport:
    """Live Manus v2 transport. ``create`` dispatches, ``get`` polls."""

    def __init__(self, api_key: str | None = None, base: str = MANUS_BASE,
                 timeout: int = 60):
        self.api_key = api_key or os.environ.get(API_KEY_ENV, "")
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict:
        if not self.api_key:
            raise RuntimeError(
                f"no Manus API key — set ${API_KEY_ENV} or pass api_key")
        return {"Content-Type": "application/json",
                "x-manus-api-key": self.api_key}

    def create(self, prompt: str, mode: str = "agent", **opts) -> dict:
        import requests
        body = {"prompt": prompt, "mode": mode, **opts}
        r = requests.post(f"{self.base}/task.create", json=body,
                          headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def get(self, task_id: str) -> dict:
        """Poll one task. POST /task.get is the documented shape; on a 404/405
        (an API that routes polling as a GET instead) fall back to the two GET
        conventions before giving up."""
        import requests
        r = requests.post(f"{self.base}/task.get", json={"task_id": task_id},
                          headers=self._headers(), timeout=self.timeout)
        if r.status_code in (404, 405):
            r = requests.get(f"{self.base}/task.get",
                             params={"task_id": task_id},
                             headers=self._headers(), timeout=self.timeout)
            if r.status_code in (404, 405):
                r = requests.get(f"{self.base}/task/{task_id}",
                                 headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return r.json()


def _unwrap(payload: dict) -> dict:
    """Peel common envelope nestings ({"data": {...}}, {"task": {...}}, and
    double-wraps like {"data": {"task": {...}}}) so the field readers see the
    task object itself regardless of wrapper style. Stops as soon as the
    current dict already looks like a task."""
    def _looks_like_task(d: dict) -> bool:
        return any(k in d for k in ("status", "task_id", "taskId", "output",
                                    "structured_output", "result"))
    seen = 0
    while isinstance(payload, dict) and seen < 4:
        if _looks_like_task(payload):
            return payload
        inner = next((payload[w] for w in ("data", "task", "response")
                      if isinstance(payload.get(w), dict)), None)
        if inner is None:
            return payload
        payload = inner
        seen += 1
    return payload if isinstance(payload, dict) else {}


def _field(d: dict, *names: str):
    for n in names:
        if isinstance(d, dict) and d.get(n) not in (None, ""):
            return d[n]
    return None


def _extract_result(payload: dict):
    for f in _RESULT_FIELDS:
        v = _field(payload, f)
        if v is None:
            continue
        # a chat-style result: {"messages": [...]} or a bare list of messages —
        # take the last message's content/text, which is the final answer
        if isinstance(v, dict) and isinstance(v.get("messages"), list):
            v = v["messages"]
        if isinstance(v, list) and v:
            last = v[-1]
            if isinstance(last, dict):
                got = _field(last, "content", "text", "message")
                if got is not None:
                    return got
            return last
        return v
    return payload


def run_research(prompt: str, transport: ManusTransport | None = None,
                 mode: str = "agent", poll_seconds: float = 10.0,
                 max_polls: int = 180, sleep=time.sleep,
                 cache: bool = True, cache_root=None,
                 label: str = "task", **create_opts) -> dict:
    """Dispatch one research task and return {status, result, task_id, raw}.

    Blocks (polling) until the task reaches a terminal state or ``max_polls``
    is hit. ``sleep`` is injectable so tests do not wait. Refuses prompts that
    trip the PHI/sensitive-inference guard.
    """
    if _PHI_MARKERS.search(prompt or ""):
        raise ValueError(
            "prompt appears to contain PHI or the sensitive whistleblower "
            "inference — Manus receives public identifiers only")
    transport = transport or ManusTransport()

    created_raw = transport.create(prompt, mode=mode, **create_opts)
    created = _unwrap(created_raw)
    task_id = _field(created, "task_id", "id", "taskId")
    if not task_id:
        raise RuntimeError(f"Manus task.create returned no task id: {created_raw}")
    if cache:
        cache_raw("manus", f"{label}_create_{task_id}", created_raw, root=cache_root)

    status = str(_field(created, "status") or "running").lower()
    payload = created
    polls = 0
    while status not in _DONE_OK and status not in _DONE_BAD and polls < max_polls:
        sleep(poll_seconds)
        polls += 1
        payload = _unwrap(transport.get(task_id))
        status = str(_field(payload, "status") or "running").lower()
    if cache:
        cache_raw("manus", f"{label}_final_{task_id}", payload, root=cache_root)

    ok = status in _DONE_OK
    return {
        "task_id": str(task_id),
        "status": status,
        "ok": ok,
        "timed_out": polls >= max_polls and status not in _DONE_OK | _DONE_BAD,
        "result": _extract_result(payload) if ok else None,
        "raw": payload,
    }


# ---- a first concrete research task: the state billing-rule memo -----------

_RULE_MEMO_TEMPLATE = (
    "Research task, public sources only. Under {state} Medicaid (fee-for-service "
    "and its managed care plans), who may be the BILLING PROVIDER for HCPCS/CPT "
    "code {code}? Read the {state} Medicaid provider manual, fee schedule, "
    "provider-enrollment/type rules, and code crosswalks. Answer specifically: "
    "(1) which enrolled provider type(s) may submit {code}; (2) whether an "
    "INDIVIDUAL practitioner (a Type 1 / personal NPI) may bill it, or only an "
    "enrolled organization/facility; (3) any prior-authorization or "
    "medical-necessity condition. Quote exact passages with document names, page "
    "numbers, and URLs. Flag ambiguity rather than guessing. End with a "
    "one-paragraph plain answer and a confidence level (high/medium/low)."
)


def state_billing_rule_memo(code: str, state: str,
                            transport: ManusTransport | None = None,
                            **kw) -> dict:
    """Dispatch the T2046-style 'who may bill this code' memo for (code, state).

    Returns the run_research envelope with an added ``query`` block so the
    dossier layer records exactly what was asked. The result is review evidence
    for counsel, cited to primary sources — never an auto-asserted rule.
    """
    prompt = _RULE_MEMO_TEMPLATE.format(code=str(code).upper(),
                                        state=str(state).upper())
    out = run_research(prompt, transport=transport,
                       label=f"rulememo_{code}_{state}", **kw)
    out["query"] = {"task": "state_billing_rule_memo",
                    "code": str(code).upper(), "state": str(state).upper()}
    return out


def main() -> None:
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rule-memo", help="who may bill (code, state)")
    r.add_argument("--code", required=True)
    r.add_argument("--state", required=True)
    r.add_argument("--out", default=None, help="write the JSON envelope here")
    g = sub.add_parser("ask", help="free-form research prompt (public data only)")
    g.add_argument("--prompt", required=True)
    g.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.cmd == "rule-memo":
        env = state_billing_rule_memo(args.code, args.state)
    else:
        env = run_research(args.prompt)
    text = json.dumps(env, indent=2, ensure_ascii=False, default=str)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(text, encoding="utf-8")
    print(f"[manus] task {env['task_id']} status={env['status']}")
    if not args.out:
        print(text[:2000])


if __name__ == "__main__":
    main()
