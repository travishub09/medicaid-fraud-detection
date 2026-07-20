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


# ---- label-side and case-protection tasks ----------------------------------

_STATE_EXCLUSION_TEMPLATE = (
    "Research task, public sources only. Find {state}'s official state Medicaid "
    "provider exclusion, termination, or sanction list (the state-level list, "
    "separate from the federal OIG LEIE). Locate the state Medicaid agency or "
    "MFCU page that publishes it, and report: (1) the exact URL of the current "
    "list and its file format (CSV/XLSX/PDF/HTML); (2) how often it is updated "
    "and the date of the current version; (3) the columns/fields it carries "
    "(names, NPIs, license numbers, exclusion dates, reasons); (4) whether "
    "historical versions are archived anywhere. If the state publishes no such "
    "list, say so explicitly and name the closest equivalent. Quote page text "
    "and give every URL. Do not transcribe individual provider names into your "
    "answer; describe the list, not its entries."
)

_MFCU_SWEEP_TEMPLATE = (
    "Research task, public sources only. Find {state}'s Medicaid Fraud Control "
    "Unit (usually in the state Attorney General's office) and its press "
    "release / enforcement-news archive. Report: (1) the exact URL of the "
    "archive and how far back it goes; (2) for the most recent {months} months, "
    "list each Medicaid provider-fraud settlement, conviction, or civil "
    "resolution: announcement date, defendant name as published, provider/"
    "business type, conduct described (in the release's own words), conduct "
    "period if stated, and dollar amount; (3) the URL of each release. These "
    "are published government enforcement announcements; quote them exactly and "
    "do not add characterizations beyond what the releases state."
)

_PUBLIC_DISCLOSURE_TEMPLATE = (
    "Research task, public sources only. Search for any PUBLIC DISCLOSURE "
    "about the healthcare provider identified by NPI {npi}, described as: "
    "{descriptor}. Check news outlets (national and local), court dockets and "
    "filed lawsuits, government audit reports, and prior enforcement "
    "announcements. Report every hit with its URL, date, and what it alleges "
    "or reports, and state plainly whether the billing conduct described above "
    "has already been publicly reported anywhere. A 'no findings' answer with "
    "the searches attempted listed is a valid and useful result. Do not "
    "speculate; report only what is published."
)

_INNOCENT_AUDIT_TEMPLATE = (
    "Research task, public sources only. You are auditing a data anomaly for "
    "FALSE POSITIVES. A healthcare provider (NPI {npi}) shows this billing "
    "pattern in public CMS data: {pattern}. Your job is to build the STRONGEST "
    "INNOCENT explanation: search the provider's own website, news coverage, "
    "government program directories, grant and contract databases, and state "
    "program pages for anything that legitimately explains the pattern (a "
    "government program, a waiver arrangement, a specialty niche, a contract, "
    "an institutional affiliation). Argue the provider is legitimate as hard "
    "as the evidence allows, with URLs and quotes. End with: the innocent "
    "explanation, how strong it is (strong/plausible/weak/none found), and "
    "what record would confirm or refute it. Do not accuse anyone of anything."
)


def state_exclusion_sweep(state: str, transport: ManusTransport | None = None,
                          **kw) -> dict:
    """Locate a state's own Medicaid exclusion/termination list (label source)."""
    out = run_research(_STATE_EXCLUSION_TEMPLATE.format(state=str(state).upper()),
                       transport=transport, label=f"stateexcl_{state}", **kw)
    out["query"] = {"task": "state_exclusion_sweep", "state": str(state).upper()}
    return out


def mfcu_sweep(state: str, months: int = 24,
               transport: ManusTransport | None = None, **kw) -> dict:
    """Sweep a state MFCU's enforcement announcements (case-label source)."""
    out = run_research(_MFCU_SWEEP_TEMPLATE.format(state=str(state).upper(),
                                                   months=int(months)),
                       transport=transport, label=f"mfcu_{state}", **kw)
    out["query"] = {"task": "mfcu_sweep", "state": str(state).upper(),
                    "months": int(months)}
    return out


def public_disclosure_screen(npi: str, descriptor: str,
                             transport: ManusTransport | None = None,
                             **kw) -> dict:
    """Has this lead's conduct already been publicly disclosed? (FCA-critical:
    a publicly disclosed fraud can bar the relator.) ``descriptor`` is a
    neutral public-data description, e.g. 'a Cranston RI internal-medicine
    practice billing hospice room-and-board codes'."""
    out = run_research(_PUBLIC_DISCLOSURE_TEMPLATE.format(
        npi=str(npi), descriptor=descriptor),
        transport=transport, label=f"pubdisc_{npi}", **kw)
    out["query"] = {"task": "public_disclosure_screen", "npi": str(npi)}
    return out


def innocent_explanation_audit(npi: str, pattern: str,
                               transport: ManusTransport | None = None,
                               **kw) -> dict:
    """Red-team a lead: build the strongest innocent explanation before any
    money is spent (the Tulare/hemophilia/IHS kill, institutionalized)."""
    out = run_research(_INNOCENT_AUDIT_TEMPLATE.format(npi=str(npi),
                                                       pattern=pattern),
                       transport=transport, label=f"innocent_{npi}", **kw)
    out["query"] = {"task": "innocent_explanation_audit", "npi": str(npi)}
    return out


_LICENSE_SWEEP_TEMPLATE = (
    "Research task, public records only. On {state}'s official professional "
    "license verification site(s), look up the {profession} license of the "
    "provider named {name} (NPI {npi}). Report exactly what the public record "
    "shows: license number(s), status (active/expired/suspended/surrendered/"
    "pending), issue and expiration dates, and ANY board discipline, consent "
    "orders, probation, or public complaints, each with its date and document "
    "URL. Also check the FSMB/NPDB public lookups if applicable, and any other "
    "state where the same provider holds a license per the record. Quote the "
    "record verbatim; report 'no discipline shown' explicitly when that is "
    "what the record says."
)

_ADDRESS_TRUTH_TEMPLATE = (
    "Research task, public sources only. Classify the physical premises at "
    "this address: {address}. Using public map/street imagery, business "
    "directories, and property listings, determine whether it is: a private "
    "residence, an apartment, a commercial office, a medical office/clinic "
    "building, a licensed healthcare facility, a mail store / UPS-type mailbox, "
    "a virtual-office/registered-agent suite, or vacant. Report what is "
    "visibly/publicly there, what businesses are listed at it, and your "
    "classification with confidence (high/medium/low) and source URLs. This is "
    "a premises classification only; make no statement about any person."
)

_SOLVENCY_TEMPLATE = (
    "Research task, public records only. Assess the public financial footprint "
    "of the organization {org_name} ({state}). From state corporate registries, "
    "PACER/court dockets visible in search, UCC filing indexes, bankruptcy "
    "records, news, and (if nonprofit) IRS Form 990 data: report (1) corporate "
    "status and any recent dissolutions, mergers, or registered-agent changes; "
    "(2) any bankruptcy filings or judgments; (3) UCC liens or secured "
    "creditors where indexed publicly; (4) revenue scale if published (990s, "
    "news); (5) parent/subsidiary structure. Every item with a URL and date. "
    "This informs whether a civil judgment against the organization would be "
    "collectible; state facts only, no conclusions about wrongdoing."
)


def license_discipline_sweep(npi: str, name: str, state: str,
                             profession: str = "medical",
                             transport: ManusTransport | None = None,
                             **kw) -> dict:
    """State licensing-board status + discipline history for one lead. Board
    sites search by name, so the published provider name is required; this is
    the same public-registry lookup the boards exist to serve."""
    out = run_research(_LICENSE_SWEEP_TEMPLATE.format(
        npi=str(npi), name=name, state=str(state).upper(), profession=profession),
        transport=transport, label=f"license_{npi}", **kw)
    out["query"] = {"task": "license_discipline_sweep", "npi": str(npi),
                    "state": str(state).upper(), "profession": profession}
    return out


def address_ground_truth(address: str, transport: ManusTransport | None = None,
                         label: str = "addr", **kw) -> dict:
    """Classify a top-lead practice address (residence / office / mail store /
    facility / virtual suite). Premises only; no person is evaluated."""
    out = run_research(_ADDRESS_TRUTH_TEMPLATE.format(address=address),
                       transport=transport, label=f"addrtruth_{label}", **kw)
    out["query"] = {"task": "address_ground_truth", "address": address}
    return out


def defendant_solvency(org_name: str, state: str = "",
                       transport: ManusTransport | None = None, **kw) -> dict:
    """Public financial footprint of a target org (Model C: is a judgment
    collectible?). Facts with citations; no wrongdoing conclusions."""
    out = run_research(_SOLVENCY_TEMPLATE.format(org_name=org_name,
                                                 state=str(state).upper()),
                       transport=transport,
                       label=f"solvency_{re.sub(r'[^A-Za-z0-9]+', '_', org_name)[:40]}",
                       **kw)
    out["query"] = {"task": "defendant_solvency", "org_name": org_name,
                    "state": str(state).upper()}
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
    se = sub.add_parser("state-exclusions", help="find a state's own exclusion list")
    se.add_argument("--state", required=True)
    se.add_argument("--out", default=None)
    mf = sub.add_parser("mfcu", help="sweep a state MFCU's enforcement news")
    mf.add_argument("--state", required=True)
    mf.add_argument("--months", type=int, default=24)
    mf.add_argument("--out", default=None)
    pdisc = sub.add_parser("public-disclosure", help="prior-disclosure screen for a lead")
    pdisc.add_argument("--npi", required=True)
    pdisc.add_argument("--descriptor", required=True,
                       help="neutral public-data description of the pattern")
    pdisc.add_argument("--out", default=None)
    ia = sub.add_parser("innocent-audit", help="red-team a lead's innocent explanation")
    ia.add_argument("--npi", required=True)
    ia.add_argument("--pattern", required=True,
                    help="the billing pattern, in neutral public-data terms")
    ia.add_argument("--out", default=None)
    lic = sub.add_parser("license", help="board status + discipline for a lead")
    lic.add_argument("--npi", required=True)
    lic.add_argument("--name", required=True, help="provider name as published")
    lic.add_argument("--state", required=True)
    lic.add_argument("--profession", default="medical")
    lic.add_argument("--out", default=None)
    ad = sub.add_parser("address", help="classify a practice address premises")
    ad.add_argument("--address", required=True)
    ad.add_argument("--out", default=None)
    sol = sub.add_parser("solvency", help="public financial footprint of an org")
    sol.add_argument("--org", required=True)
    sol.add_argument("--state", default="")
    sol.add_argument("--out", default=None)
    g = sub.add_parser("ask", help="free-form research prompt (public data only)")
    g.add_argument("--prompt", required=True)
    g.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.cmd == "rule-memo":
        env = state_billing_rule_memo(args.code, args.state)
    elif args.cmd == "state-exclusions":
        env = state_exclusion_sweep(args.state)
    elif args.cmd == "mfcu":
        env = mfcu_sweep(args.state, months=args.months)
    elif args.cmd == "public-disclosure":
        env = public_disclosure_screen(args.npi, args.descriptor)
    elif args.cmd == "innocent-audit":
        env = innocent_explanation_audit(args.npi, args.pattern)
    elif args.cmd == "license":
        env = license_discipline_sweep(args.npi, args.name, args.state,
                                       profession=args.profession)
    elif args.cmd == "address":
        env = address_ground_truth(args.address)
    elif args.cmd == "solvency":
        env = defendant_solvency(args.org, args.state)
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
