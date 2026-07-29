"""
manus_research.py — dispatch Manus browser-agent research on the top-N leads.

The unstructured-web research this project needs (state Medicaid billing-rule
memos, corporate/address resolution, license and sanctions checks) cannot come
from a fixed API — it takes an agent that reads PDFs and cross-references
registries. Manus is that agent. This module wraps its API as one more feed:
dispatch a task, poll to completion, cache the result with its citations and
retrieval date, return structured evidence.

CONTRACT (Manus v2, verified against the LIVE API 2026-07-25): base
https://api.manus.ai/v2, auth header ``x-manus-api-key``.
``POST /v2/task.create`` with body {"message": {"content": [{"type": "text",
"text": <prompt>}]}} → {"ok", "task_id", "task_url", ...}. Poll with
``GET /v2/task.listMessages?task_id=<id>`` → {"messages": [...]} where the
newest ``status_update`` carries ``agent_status`` ("running" → keep polling,
"stopped" → finished, "waiting" → the agent wants input we cannot give an
unattended task, treated as failed) and the newest ``assistant_message``
carries the result text. The API has NO structured-output field: a caller's
``schema`` is folded into the prompt as a strict JSON instruction and the
reply text is parsed back into an object. Field names beyond these are still
read defensively so a minor API rename does not break a run.

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

import json
import os
import re
import time

from src.feeds.client import cache_raw

MANUS_BASE = "https://api.manus.ai/v2"
API_KEY_ENV = "MANUS_API_KEY"

# terminal task states (live API + defensive aliases). The live v2 reports
# agent_status "stopped" when FINISHED — the transport maps it to "completed"
# before it gets here, so "stopped" in the raw sense (user-cancelled via app)
# stays a bad-terminal alias. "waiting" = the agent wants human input that an
# unattended batch task can never give: terminal, not ok.
_DONE_OK = {"completed", "success", "succeeded", "finished", "done"}
_DONE_BAD = {"failed", "error", "errored", "stopped", "cancelled", "canceled",
             "waiting"}

# result field aliases, tried in order
_RESULT_FIELDS = ["structured_output", "output", "result", "final_output",
                  "answer", "text", "content", "summary"]

# crude guards so we never ship PHI or the sensitive inference to an external
# agent. These are a backstop for a caller mistake, not a substitute for passing
# public identifiers only.
# The guard fires on PHI-shaped phrasings and the sensitive whistleblower
# inference, NOT on the bare words "patient"/"beneficiary" (which appear in
# legitimate aggregate contexts: "patients per day", "patient reviews", "per
# beneficiary"). It blocks a patient/beneficiary paired with an IDENTIFIER
# field, plus the record/id/whistleblower terms. Stem terms match their
# inflections, but "diagnos" is bounded to diagnosis/diagnoses/diagnosed/
# diagnosing — NOT "diagnostic(s)", which is the most common LAB COMPANY name
# suffix in the country ("Quest Diagnostics") and blocked real enforcement
# defendants from the identifier-enrichment batches. Backstop for a caller
# mistake, not a substitute for passing public identifiers only.
_PHI_MARKERS = re.compile(
    r"\b("
    r"(patient|beneficiar\w*|member)['’]?s?\s+"
    r"(name|names|record|records|chart|charts|roster|list|dob|date of birth|ssn|address|identifier)|"
    r"member id|medical record|mrn|diagnos(?:is|es|ed|ing)|dob|date of birth|ssn|"
    r"whistleblow|relator|likely.{0,20}witness"
    r")", re.IGNORECASE)


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
        """Dispatch a task. Live-verified body shape: the prompt rides as
        message.content[0].text; anything else 400s with "message.content is
        required". ``mode`` is accepted for signature compatibility but not
        sent (the API has no such field). Extra ``opts`` (e.g. agent_profile)
        pass through at the top level for forward compatibility."""
        import requests
        body = {"message": {"content": [{"type": "text", "text": prompt}]},
                **opts}
        r = requests.post(f"{self.base}/task.create", json=body,
                          headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def get(self, task_id: str) -> dict:
        """Poll one task via GET /task.listMessages?task_id= (the only shape
        the live API serves; task.get does not exist). Synthesizes the
        {status, output} envelope the polling loop reads: the NEWEST
        status_update's agent_status → status ("stopped" = finished →
        "completed"), the NEWEST assistant_message's content → output."""
        import requests
        r = requests.get(f"{self.base}/task.listMessages",
                         params={"task_id": task_id},
                         headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        payload = r.json()
        msgs = payload.get("messages") or []

        def _newest(kind):
            best, best_ts = None, -1.0
            for m in msgs:
                if isinstance(m, dict) and m.get("type") == kind:
                    try:
                        ts = float(m.get("timestamp") or 0)
                    except (TypeError, ValueError):
                        ts = 0.0
                    if ts >= best_ts:
                        best, best_ts = m, ts
            return best

        status_msg = _newest("status_update")
        agent_status = str(((status_msg or {}).get("status_update") or {})
                           .get("agent_status") or "running").lower()
        status = {"stopped": "completed"}.get(agent_status, agent_status)
        answer_msg = _newest("assistant_message")
        am = (answer_msg or {}).get("assistant_message") or {}
        err_msg = _newest("error_message")
        return {"task_id": str(payload.get("task_id") or task_id),
                "status": status, "output": am.get("content"),
                # agents often deliver the payload as an attached file with a
                # prose summary in the reply; surface the attachment list
                # (filename, content_type, signed url) so callers can fetch it
                "attachments": am.get("attachments") or [],
                # quota/server errors arrive as error_message entries — the
                # human-readable reason ("You don't have enough credits...")
                "error_detail": ((err_msg or {}).get("error_message") or {})
                .get("content"),
                "messages": msgs}


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


def _poll_to_terminal(transport, task_id, payload, status, poll_seconds,
                      max_polls, sleep, max_poll_errors: int = 6):
    """Poll until a terminal state, tolerating transient poll failures.

    A research agent runs for tens of minutes; a single 500 on ONE poll must
    not abandon a task that is still working (and still billing) server-side.
    Consecutive-failure counting: any successful poll resets the budget; only
    ``max_poll_errors`` failures IN A ROW give up. Returns
    (status, payload, polls, last_error)."""
    polls, errors, last_err = 0, 0, None
    while (status not in _DONE_OK and status not in _DONE_BAD
           and polls < max_polls):
        sleep(poll_seconds)
        polls += 1
        try:
            payload = _unwrap(transport.get(task_id))
            errors = 0
        except Exception as e:                      # transient API hiccup
            errors += 1
            last_err = str(e)
            if errors >= max_poll_errors:
                break
            continue
        status = str(_field(payload, "status") or "running").lower()
    return status, payload, polls, last_err


def run_research(prompt: str, transport: ManusTransport | None = None,
                 mode: str = "agent", poll_seconds: float = 10.0,
                 max_polls: int = 180, sleep=time.sleep,
                 cache: bool = True, cache_root=None,
                 label: str = "task", **create_opts) -> dict:
    """Dispatch one research task and return {status, result, task_id, raw}.

    Blocks (polling) until the task reaches a terminal state or ``max_polls``
    is hit. ``sleep`` is injectable so tests do not wait. Refuses prompts that
    trip the PHI/sensitive-inference guard. Transient poll failures (a 500 on
    task.listMessages) are retried, not fatal — see _poll_to_terminal. A run
    that times out has NOT cancelled the task server-side: keep the task_id
    and finish it later with ``resume_research``.
    """
    # Guard the PROSE, not the links: DOJ/court URL slugs legitimately contain
    # guarded words ("/pr/whistleblower-lawsuit-settlement") and a URL cannot
    # carry PHI the way a sentence can. Strip URLs before scanning so a cited
    # public case link never false-positives the backstop; the sensitive
    # phrasings themselves stay blocked.
    prose = re.sub(r"https?://\S+", " ", prompt or "")
    if _PHI_MARKERS.search(prose):
        raise ValueError(
            "prompt appears to contain PHI or the sensitive whistleblower "
            "inference — Manus receives public identifiers only")
    transport = transport or ManusTransport()

    # The live v2 API has no structured-output field, so a schema becomes a
    # strict instruction appended to the prompt, and the reply text is parsed
    # back into an object below.
    schema = create_opts.pop("schema", None)
    if schema is not None:
        prompt = (f"{prompt}\n\nOUTPUT FORMAT (mandatory): respond with ONLY "
                  "a single JSON object that matches this JSON schema exactly. "
                  "No prose before or after it, no markdown code fences. "
                  "PASTE THE ENTIRE JSON DIRECTLY IN YOUR FINAL CHAT REPLY - "
                  "do NOT deliver it as an attached file; the API caller "
                  "cannot open attachments, and a summary with the data in an "
                  "attachment counts as NO ANSWER:\n"
                  + json.dumps(schema))

    created_raw = transport.create(prompt, mode=mode, **create_opts)
    created = _unwrap(created_raw)
    task_id = _field(created, "task_id", "id", "taskId")
    if not task_id:
        raise RuntimeError(f"Manus task.create returned no task id: {created_raw}")
    if cache:
        cache_raw("manus", f"{label}_create_{task_id}", created_raw, root=cache_root)

    status = str(_field(created, "status") or "running").lower()
    status, payload, polls, poll_err = _poll_to_terminal(
        transport, task_id, created, status, poll_seconds, max_polls, sleep)
    if cache:
        cache_raw("manus", f"{label}_final_{task_id}", payload, root=cache_root)
    return _envelope(task_id, status, payload, polls, max_polls, schema,
                     poll_err)


def _envelope(task_id, status, payload, polls, max_polls, schema, poll_err):
    ok = status in _DONE_OK
    result = _extract_result(payload) if ok else None
    if schema is not None and isinstance(result, str):
        parsed = _parse_json_text(result)
        if parsed is not None:
            result = parsed
    # attachment fallback: a prose reply with the JSON delivered as a file
    if (ok and schema is not None and not isinstance(result, dict)
            and isinstance(payload, dict)):
        fetched = fetch_json_attachment(payload.get("attachments"))
        if isinstance(fetched, dict):
            result = fetched
    return {
        "task_id": str(task_id),
        "status": status,
        "ok": ok,
        "timed_out": polls >= max_polls and status not in _DONE_OK | _DONE_BAD,
        "poll_error": poll_err,
        "result": result,
        "raw": payload,
    }


def resume_research(task_id: str, transport: ManusTransport | None = None,
                    poll_seconds: float = 10.0, max_polls: int = 180,
                    sleep=time.sleep, cache: bool = True, cache_root=None,
                    label: str = "task", schema: dict | None = None) -> dict:
    """Re-attach to an ALREADY-DISPATCHED task and poll it to a terminal state.

    A timed-out or poll-crashed run leaves the task running (and billing)
    server-side; re-creating it would pay twice for the same research.
    Callers keep the task_id from the create envelope (or the cached
    ``*_create_<task_id>.json``) and finish the job here. Same envelope as
    run_research; ``schema`` re-enables the JSON-reply parsing."""
    transport = transport or ManusTransport()
    status, payload, polls, poll_err = _poll_to_terminal(
        transport, task_id, {}, "running", poll_seconds, max_polls, sleep)
    if cache:
        cache_raw("manus", f"{label}_final_{task_id}", payload, root=cache_root)
    return _envelope(task_id, status, payload, polls, max_polls, schema,
                     poll_err)


def fetch_json_attachment(attachments, timeout: int = 120):
    """Download and parse the first JSON attachment from a task's reply.

    ``attachments`` is the list the transport surfaces (filename,
    content_type, signed CDN url — live-verified shape). Returns the parsed
    dict or None. The signed URL needs no auth header."""
    import requests
    for a in attachments or []:
        if not isinstance(a, dict):
            continue
        url = str(a.get("url") or "")
        ct = str(a.get("content_type") or "").lower()
        name = str(a.get("filename") or "").lower()
        if not url or not ("json" in ct or name.endswith(".json")):
            continue
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            obj = _parse_json_text(r.text)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def _parse_json_text(text: str):
    """Best-effort JSON object out of an agent reply: tolerates markdown fences
    and prose around the object; None when nothing parseable is found."""
    s = str(text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    for candidate in (s, s[s.find("{"): s.rfind("}") + 1]
                      if "{" in s and "}" in s else ""):
        if not candidate:
            continue
        try:
            out = json.loads(candidate)
            return out if isinstance(out, dict) else None
        except (json.JSONDecodeError, ValueError):
            continue
    return None


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


# ---- the reality score: does this operation exist at the scale it claims? ---

_REALITY_TEMPLATE = (
    "Research task, public sources only. A healthcare provider bills a very large "
    "amount of government money. Verify whether a real operation exists at that "
    "scale. Do NOT allege fraud; report only what public records show or do not "
    "show. Subject: {label} (NPI {npi}), reported location {city} {state}.\n\n"
    "Check each item and report a plain yes/no/unknown with the source URL:\n"
    "1. Website: does the practice/organization have its own website? If so, when "
    "was the domain first registered (WHOIS) and when did the site first appear "
    "(Wayback Machine)?\n"
    "2. Google Business / Maps listing: does it exist, is it marked open or "
    "permanently closed, does it have patient reviews, and what does Street View "
    "show at the address (a medical building, a house, a UPS-store/mailbox, a "
    "vacant lot)?\n"
    "3. Workforce: any employees on LinkedIn, any job postings ever "
    "(Indeed/LinkedIn/Glassdoor)?\n"
    "4. Phone: does the listed number connect / appear to be a real business line "
    "vs a disconnected or VOIP number?\n"
    "5. Corporate existence: state Secretary-of-State registration, incorporation "
    "date, status (active/dissolved).\n"
    "6. Licensure: is the entity on the relevant {state} licensed-facility list "
    "for what it bills, and is the named provider's professional license active?\n"
    "7. Reviews/news: any patient reviews or local news describing billing for "
    "services not received, or an investigation/raid?\n\n"
    "Then give a REALITY ASSESSMENT: on a 0-100 scale, how much real-world "
    "corroboration exists that an operation of this scale actually exists at this "
    "location (100 = fully corroborated: real building, website with history, "
    "employees, active license; 0 = no footprint found anywhere). List the "
    "specific gaps. Quote or link every source. If something cannot be found, say "
    "'no record found' rather than guessing."
)

# structured output so the result is scoreable, not prose. Matches the fields
# the dossier enrichment reads; run_research forces the agent to fill it.
REALITY_SCHEMA = {
    "type": "object",
    "properties": {
        "reality_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "has_website": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "domain_age_years": {"type": ["number", "null"]},
        "has_maps_listing": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "address_kind": {"type": "string",
                         "enum": ["medical_building", "house", "mailbox_store",
                                  "vacant", "other", "unknown"]},
        "has_reviews": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "has_employees_or_jobs": {"type": "string",
                                  "enum": ["yes", "no", "unknown"]},
        "phone_connects": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "corporate_status": {"type": "string",
                             "enum": ["active", "dissolved", "none_found",
                                      "unknown"]},
        "incorporation_date": {"type": ["string", "null"]},
        "licensed_for_billed_service": {"type": "string",
                                        "enum": ["yes", "no", "unknown"]},
        "adverse_news_or_reviews": {"type": "string",
                                    "enum": ["yes", "no", "unknown"]},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "citations": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["reality_score", "gaps", "confidence"],
}


def reality_score(npi: str, label: str, city: str, state: str,
                  transport: ManusTransport | None = None, **kw) -> dict:
    """Dispatch the existence-verification task for one high-value lead.

    Returns the run_research envelope; when the transport honors the schema the
    result is the structured REALITY_SCHEMA object. This is TOP-N dossier
    enrichment and (with review) a label input — never a universe training
    feature (a live web value is not reproducible point-in-time). Public
    identifiers only; the subject is a provider number and a city, no PHI."""
    prompt = _REALITY_TEMPLATE.format(npi=str(npi), label=str(label or "unknown"),
                                      city=str(city or ""), state=str(state or ""))
    out = run_research(prompt, transport=transport, schema=REALITY_SCHEMA,
                       label=f"reality_{npi}", **kw)
    out["query"] = {"task": "reality_score", "npi": str(npi)}
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


# ---- case-label harvest: the DOJ/MFCU backfill as structured label rows -----
#
# The single binding constraint on the whole modelling program is the LABEL:
# exclusions are binary, untyped, and blind to the billing/kickback/quality
# schemes the broad data was procured to detect. The chain that fixes this is
# already built (case_db.CASE_COLUMNS -> case_labels -> widened PU label with
# scheme + conduct window); the missing input is the multi-year backfill of
# resolved enforcement outcomes. That is browser work, so it is Manus work.
#
# Discipline (docs/platform/01): the net is WIDE but the label stays clean via
# TIERING. Everything citable is captured — resolved outcomes AND pending
# allegations — but each row carries outcome_type, and harvest_case_rows
# splits them: only resolved outcomes (settlement, judgment, plea, conviction,
# CIA) are candidates for the hard label in enforcement/doj_cases.csv; pending
# rows (indictments, complaints) go to a separate review file for weak
# supervision and monitoring, never the hard label. Every row must carry its
# source URL. NPIs are never asserted: NPPES lookups produce CANDIDATES with a
# stated basis, for human review. Frozen, versioned, reviewed — never a live
# call in the scoring path.

_CASE_HARVEST_TEMPLATE = (
    "Research task, public sources only. Build the most COMPREHENSIVE "
    "structured list you can of healthcare-fraud enforcement events involving "
    "{state} providers (or multistate cases that include {state} conduct or "
    "defendants) announced between {start_year} and {end_year}, where "
    "Medicaid, Medicare, or another government health program's money was at "
    "issue.\n\n"
    "Sweep EVERY source you can reach, including at minimum: (1) DOJ press "
    "releases (justice.gov, main and the {state} U.S. Attorney's offices); "
    "(2) HHS-OIG enforcement actions, exclusion announcements, and corporate "
    "integrity agreements (oig.hhs.gov); (3) the {state} Medicaid Fraud "
    "Control Unit and state Attorney General press releases and annual "
    "reports; (4) NAMFCU case summaries; (5) the {state} Medicaid agency's "
    "program-integrity or inspector-general pages (terminations, sanctions, "
    "recoveries); (6) federal court records (CourtListener/RECAP) for False "
    "Claims Act and healthcare-fraud judgments; (7) state licensing-board "
    "actions taken FOR billing fraud; (8) qui tam law-firm "
    "case announcements and credible news coverage — used as POINTERS whose "
    "facts you then confirm against a primary source. Do not stop at the "
    "first source; different sources catch different cases.\n\n"
    "CAPTURE BOTH TIERS, clearly typed. Tier 1, resolved outcomes: "
    "settlement, civil judgment, guilty plea, jury conviction, plea "
    "agreement, corporate integrity agreement. Tier 2, pending allegations: "
    "indictment, criminal or civil complaint, charges filed. Record BOTH, and "
    "set outcome_type honestly for each — never present an allegation as an "
    "outcome. If a case appears twice (charged, later resolved), record the "
    "RESOLVED event and note the charge date in the summary.\n\n"
    "For each case record: the announcement date; EVERY named defendant, "
    "person or organization, exactly as written, one entry per defendant; "
    "the healthcare sector (use home_health, hospice, dme, lab, behavioral, "
    "snf, pharmacy, physician, hospital when they fit, otherwise your own "
    "words); the scheme (use kickback, upcoding, medical_necessity, "
    "billing_fraud, services_not_rendered, ghost_patients, identity_theft "
    "when they fit, otherwise your own words); the dollar amount; qui tam "
    "and government-intervention status (unknown if not stated); the "
    "court/district; the conduct period as stated (e.g. 'from 2016 through "
    "2020'); a 2-4 sentence factual summary INCLUDING the conduct-period "
    "years verbatim; and the source URL. The source URL is mandatory — no "
    "URL, no row.\n\n"
    "LINKABILITY IS CRITICAL — these rows join to a provider graph. Extract "
    "VERBATIM every identifier the sources contain: any NPI printed in the "
    "release or docket; professional license numbers; CMS certification "
    "numbers (CCN); Medicaid provider numbers; every practice or business "
    "address mentioned; every d/b/a, trade, or former business name; the "
    "names and roles of owners, officers, and co-conspirators tied to an "
    "organization defendant; and every state the conduct touched. An "
    "identifier you copy from the source is worth more than any lookup.\n\n"
    "Then, for each defendant WITHOUT an NPI printed in the source, look up "
    "NPI CANDIDATES in the NPPES registry (npiregistry.cms.hhs.gov) by name "
    "and state, and by the practice address when you have one. Report "
    "candidates only: the NPI, the registry name, and the match basis. Never "
    "assert that a candidate IS the defendant — these are for human review.\n\n"
    "Report facts from the cited public record only; no characterization "
    "beyond what the sources state. More rows with honest outcome_type beats "
    "fewer rows: completeness is the goal, the tiering keeps it safe.\n\n"
    "Work AUTONOMOUSLY: this task runs unattended, so never pause to ask a "
    "question or request confirmation — nobody can answer. When something is "
    "ambiguous, make the reasonable assumption, note it in the relevant "
    "summary, and keep going to the final answer. A partial but delivered "
    "list beats a paused task."
)

# outcome types that count as RESOLVED (tier 1, hard-label candidates).
# Everything else — indictments, complaints, charges — is a pending allegation:
# weak-supervision signal and monitoring input, never the hard label.
RESOLVED_OUTCOMES = {"settlement", "civil_judgment", "guilty_plea",
                     "conviction", "plea_agreement", "cia"}

# structured output: rows land directly in enforcement/case_db.CASE_COLUMNS
# shape after harvest_case_rows(); npi candidates ride separately for review.
CASE_HARVEST_SCHEMA = {
    "type": "object",
    "properties": {
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "announced_date": {"type": "string"},
                    "defendant_name": {"type": "string"},
                    "sector": {"type": "string"},
                    "scheme": {"type": "string"},
                    "amount_usd": {"type": ["number", "null"]},
                    "qui_tam": {"type": ["boolean", "null"]},
                    "intervened": {"type": ["boolean", "null"]},
                    "jurisdiction": {"type": "string"},
                    "conduct_period": {"type": "string"},
                    "summary": {"type": "string"},
                    "source_url": {"type": "string"},
                    "outcome_type": {
                        "type": "string",
                        "enum": ["settlement", "civil_judgment", "guilty_plea",
                                 "conviction", "plea_agreement", "cia",
                                 "indictment", "criminal_complaint",
                                 "civil_complaint", "charges_filed", "other"]},
                    "npi_in_source": {"type": "string"},
                    "license_numbers": {"type": "array",
                                        "items": {"type": "string"}},
                    "ccn": {"type": "string"},
                    "medicaid_provider_id": {"type": "string"},
                    "addresses": {"type": "array", "items": {"type": "string"}},
                    "dba_names": {"type": "array", "items": {"type": "string"}},
                    "related_individuals": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"},
                                           "role": {"type": "string"}},
                            "required": ["name"],
                        }},
                    "states_involved": {"type": "array",
                                        "items": {"type": "string"}},
                    "npi_candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "npi": {"type": "string"},
                                "registry_name": {"type": "string"},
                                "match_basis": {"type": "string"},
                            },
                            "required": ["npi", "registry_name", "match_basis"],
                        }},
                },
                "required": ["announced_date", "defendant_name", "summary",
                             "source_url", "outcome_type"],
            }},
    },
    "required": ["cases"],
}


def case_label_harvest(state: str, start_year: int, end_year: int,
                       transport: ManusTransport | None = None, **kw) -> dict:
    """One state × window sweep of resolved enforcement outcomes → label rows.

    Batch over states/windows with research_sweep; feed the reviewed output to
    ``harvest_case_rows`` and append accepted rows to enforcement/doj_cases.csv.
    """
    prompt = _CASE_HARVEST_TEMPLATE.format(state=str(state).upper(),
                                           start_year=int(start_year),
                                           end_year=int(end_year))
    out = run_research(prompt, transport=transport, schema=CASE_HARVEST_SCHEMA,
                       label=f"caseharvest_{state}_{start_year}_{end_year}", **kw)
    out["query"] = {"task": "case_label_harvest", "state": str(state).upper(),
                    "start_year": int(start_year), "end_year": int(end_year)}
    return out


def harvest_case_rows(result: dict) -> tuple:
    """Manus harvest result → (case_rows, npi_candidates) for HUMAN REVIEW.

    ``case_rows`` matches enforcement/case_db.CASE_COLUMNS (case_id = source
    URL; the stated conduct period is folded into the summary text so
    ``case_labels.extract_conduct_window`` recovers it downstream — no schema
    change). Rows without a source URL are dropped: unverifiable, so never a
    label. ``npi_candidates`` carries (case_id, defendant_name, npi,
    registry_name, match_basis) — review evidence only, NEVER auto-joined.
    """
    import pandas as pd

    if isinstance(result, str):                      # reply that didn't parse
        result = _parse_json_text(result) or {}
    if not isinstance(result, dict):
        result = {}
    cases = result.get("cases") or []
    rows, cands = [], []
    for c in cases:
        if not isinstance(c, dict):
            continue
        url = str(c.get("source_url") or "").strip()
        if not url.lower().startswith("http"):
            continue                       # no URL, no row — unverifiable
        summary = str(c.get("summary") or "").strip()
        period = str(c.get("conduct_period") or "").strip()
        if period and period not in summary:
            summary = f"{summary} Conduct period: {period}.".strip()
        outcome = str(c.get("outcome_type") or "").strip().lower()

        def _joined(key, item_fmt=None):
            vals = c.get(key) or []
            if not isinstance(vals, list):
                vals = [vals]
            parts = []
            for v in vals:
                if item_fmt and isinstance(v, dict):
                    parts.append(item_fmt(v))
                elif v:
                    parts.append(str(v).strip())
            return " | ".join(p for p in parts if p)

        rows.append({
            "case_id": url,
            "announced_date": str(c.get("announced_date") or ""),
            "defendant_name": str(c.get("defendant_name") or ""),
            "sector": str(c.get("sector") or ""),
            "scheme": str(c.get("scheme") or ""),
            "amount_usd": c.get("amount_usd"),
            "qui_tam": ({True: 1, False: 0}.get(c.get("qui_tam"))
                        if c.get("qui_tam") is not None else None),
            "intervened": ({True: 1, False: 0}.get(c.get("intervened"))
                           if c.get("intervened") is not None else None),
            "jurisdiction": str(c.get("jurisdiction") or ""),
            "source_url": url,
            "summary": summary,
            "outcome_type": outcome,
            # TIERING: only resolved outcomes may ever reach the hard label in
            # doj_cases.csv; pending allegations feed weak supervision and
            # monitoring only. The runner splits its review files on this.
            "label_tier": ("resolved" if outcome in RESOLVED_OUTCOMES
                           else "pending"),
            # linkage extras — join keys back to the entity graph. Extra
            # columns are harmless downstream: build_case_db selects
            # CASE_COLUMNS only.
            "npi_in_source": str(c.get("npi_in_source") or "").strip(),
            "license_numbers": _joined("license_numbers"),
            "ccn": str(c.get("ccn") or "").strip(),
            "medicaid_provider_id": str(c.get("medicaid_provider_id") or "").strip(),
            "addresses": _joined("addresses"),
            "dba_names": _joined("dba_names"),
            "related_individuals": _joined(
                "related_individuals",
                lambda v: (f"{v.get('name', '')} ({v.get('role', '')})"
                           if v.get("role") else str(v.get("name", "")))),
            "states_involved": _joined("states_involved"),
        })
        for cand in (c.get("npi_candidates") or []):
            if isinstance(cand, dict) and str(cand.get("npi") or "").strip():
                cands.append({
                    "case_id": url,
                    "defendant_name": str(c.get("defendant_name") or ""),
                    "npi": str(cand.get("npi")).strip(),
                    "registry_name": str(cand.get("registry_name") or ""),
                    "match_basis": str(cand.get("match_basis") or ""),
                })
    return pd.DataFrame(rows), pd.DataFrame(
        cands, columns=["case_id", "defendant_name", "npi", "registry_name",
                        "match_basis"])


# ---- identifier enrichment: fill the join gaps the audit found -------------

_ENRICH_TEMPLATE = (
    "Research task, public sources only. For each defendant below — all named "
    "in RESOLVED public healthcare-fraud enforcement announcements — find the "
    "public identifiers that let a record system link them to healthcare "
    "billing data. For each one report: (1) NPI candidates from the NPPES "
    "registry (npiregistry.cms.hhs.gov), searched by name + state AND by any "
    "practice location you find — report the NPI, registry name, and match "
    "basis, never asserting identity; (2) the organization(s) they billed "
    "through or owned — exact legal names and d/b/a names from the state "
    "Secretary of State registry or the enforcement release; (3) practice or "
    "business addresses; (4) professional license numbers from the state "
    "licensing board. Read the cited enforcement URL first — it often names "
    "the employer, city, or specialty that disambiguates the person. If "
    "nothing can be found for a defendant, say so explicitly for that "
    "defendant rather than guessing. Do not pause to ask questions; make "
    "reasonable assumptions and proceed to the final answer.\n\n"
    "DEFENDANTS:\n{roster}"
)

ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "defendants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "state": {"type": "string"},
                    "npi_candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "npi": {"type": "string"},
                                "registry_name": {"type": "string"},
                                "match_basis": {"type": "string"},
                            },
                            "required": ["npi", "registry_name",
                                         "match_basis"]}},
                    "org_names": {"type": "array", "items": {"type": "string"}},
                    "addresses": {"type": "array", "items": {"type": "string"}},
                    "license_numbers": {"type": "array",
                                        "items": {"type": "string"}},
                    "nothing_found": {"type": "boolean"},
                    "note": {"type": "string"},
                },
                "required": ["name"],
            }},
    },
    "required": ["defendants"],
}


def identifier_enrichment(defendants: list, transport: ManusTransport | None = None,
                          **kw) -> dict:
    """One batch of join-gap defendants → public identifiers for review.

    ``defendants``: dicts with name, state, source_url, and optionally a short
    context line (the audit's gaps worklist rows). Keep batches to ~15 so the
    agent stays thorough."""
    lines = []
    for d in defendants:
        ctx = str(d.get("context") or d.get("summary") or "")[:200]
        lines.append(f"- {d.get('name')} | state: {d.get('state', '?')} | "
                     f"case: {d.get('source_url', '')} | context: {ctx}")
    prompt = _ENRICH_TEMPLATE.format(roster="\n".join(lines))
    out = run_research(prompt, transport=transport, schema=ENRICH_SCHEMA,
                       label=f"enrich_{len(defendants)}", **kw)
    out["query"] = {"task": "identifier_enrichment", "n": len(defendants)}
    return out


_REVERIFY_TEMPLATE = (
    "Research task, public records only. Below are previously collected "
    "case rows whose recorded facts failed a mechanical quality screen and "
    "must be RE-VERIFIED against their cited sources. For each case: open "
    "the cited URL and re-read it carefully (if the URL is dead, find the "
    "same announcement on the official agency site and report the "
    "replacement URL). Then report, for each field listed under 'check', "
    "exactly what the source document says, QUOTING the sentence that "
    "contains it verbatim: the announcement date, the dollar amount (as a "
    "number; distinguish alleged loss vs settlement/judgment amount and say "
    "which the document states), the outcome type, and the conduct period. "
    "Give a per-case verdict: 'confirmed' when the recorded value matches "
    "the document, 'corrected' when the document says something different "
    "(report the correct value), 'cannot_verify' when the document does not "
    "state it or cannot be found. Never guess; a cannot_verify is a good "
    "answer. Do not pause to ask questions.\n\nCASES:\n{roster}"
)

REVERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "defendant_name": {"type": "string"},
                    "source_url": {"type": "string"},
                    "replacement_url": {"type": "string"},
                    "verdict": {"type": "string",
                                "enum": ["confirmed", "corrected",
                                         "cannot_verify"]},
                    "announced_date_in_source": {"type": "string"},
                    "amount_usd_in_source": {"type": ["number", "null"]},
                    "amount_kind": {"type": "string"},
                    "outcome_type_in_source": {"type": "string"},
                    "conduct_period_in_source": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["defendant_name", "verdict"],
            }},
    },
    "required": ["cases"],
}


def record_reverification(rows: list, transport: ManusTransport | None = None,
                          **kw) -> dict:
    """One batch of QC-flagged case rows → per-field verdicts with quotes.

    ``rows``: dicts with defendant_name, announced_date, amount_usd,
    outcome_type, source_url, and what_to_check (from case_qc's
    reverify_batch.csv). Keep batches to ~10 so every citation gets read."""
    lines = []
    for r in rows:
        lines.append(
            f"- {r.get('defendant_name')} | recorded date: "
            f"{r.get('announced_date', '?')} | recorded amount: "
            f"{r.get('amount_usd', '?')} | recorded outcome: "
            f"{r.get('outcome_type', '?')} | cited: {r.get('source_url', '')}"
            f" | check: {r.get('what_to_check', 'all fields')}")
    prompt = _REVERIFY_TEMPLATE.format(roster="\n".join(lines))
    out = run_research(prompt, transport=transport, schema=REVERIFY_SCHEMA,
                       label=f"reverify_{len(rows)}", **kw)
    out["query"] = {"task": "record_reverification", "n": len(rows)}
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


# ---- corporate-network mining: find the hidden ring the owners files miss ---

_CORP_NETWORK_TEMPLATE = (
    "Research task, public records only, neutral association mapping (not an "
    "allegation). Starting from this seed: {seed}. Using state Secretary-of-State "
    "business registries (all states as needed), map the network of healthcare-"
    "related entities connected to it by SHARED INFRASTRUCTURE, the kind of link "
    "that ownership-disclosure files miss:\n"
    "  - entities at the same street address or suite;\n"
    "  - entities sharing a registered agent;\n"
    "  - entities sharing an officer, director, incorporator, or manager;\n"
    "  - entities sharing a phone number, website, or website template;\n"
    "  - clusters of entities incorporated within a short window of each other.\n"
    "For each entity report: legal name, state entity id, incorporation date, "
    "status (active/dissolved), registered agent, officers, address, and which "
    "shared link ties it to the seed. Then identify any cluster of 3 or more "
    "entities bound by a common agent, officer, address, or phone, and name the "
    "shared node. Quote each record with its official URL. Report co-location or "
    "a shared agent as exactly that, a shared link, not proof of common "
    "ownership. Where a paid status report would be needed for officer detail, "
    "say so rather than inferring."
)

CORP_NETWORK_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "legal_name": {"type": "string"},
                    "state_entity_id": {"type": ["string", "null"]},
                    "incorporation_date": {"type": ["string", "null"]},
                    "status": {"type": "string"},
                    "registered_agent": {"type": ["string", "null"]},
                    "officers": {"type": "array", "items": {"type": "string"}},
                    "address": {"type": ["string", "null"]},
                    "phone": {"type": ["string", "null"]},
                    "shared_link": {"type": "string"},
                    "source_url": {"type": ["string", "null"]},
                },
                "required": ["legal_name", "shared_link"],
            },
        },
        "shared_nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string",
                             "enum": ["registered_agent", "officer", "address",
                                      "phone", "website", "incorporation_window"]},
                    "value": {"type": "string"},
                    "n_entities": {"type": "integer"},
                    "entity_names": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["kind", "value", "n_entities"],
            },
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["entities", "shared_nodes"],
}


def corporate_network_map(seed: str, transport: ManusTransport | None = None,
                          label: str = "seed", **kw) -> dict:
    """Map the shared-infrastructure corporate network around a seed (an address,
    a registered agent, an officer name, or an org). Surfaces the ring shape
    that CMS ownership files cannot, because the ownership is hidden but the
    agent/officer/address/phone plumbing is shared. Structured output so the
    reviewed result can seed shared-node graph edges. Neutral association
    mapping; a shared agent is a link, not proof of common ownership. Reviewed
    before it ever becomes a graph edge."""
    out = run_research(_CORP_NETWORK_TEMPLATE.format(seed=seed),
                       transport=transport, schema=CORP_NETWORK_SCHEMA,
                       label=f"corpnet_{label}", **kw)
    out["query"] = {"task": "corporate_network_map", "seed": seed}
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


def propose_code_legality_row(code: str, state: str,
                              transport: ManusTransport | None = None,
                              build_date: str = "", **kw) -> dict:
    """Dispatch the rule memo AND shape a PROPOSED code_legality row from it.

    Returns {code, state, individual_allowed, who_may_bill, authority_url,
    build_date, reviewed} plus the full memo under 'memo'. ``reviewed`` is
    ALWAYS 'N' — a human confirms the row before it can score anything (the
    blast-radius gate). ``individual_allowed`` is left blank when the memo did
    not clearly resolve it, so an unclear memo never silently becomes a rule."""
    env = state_billing_rule_memo(code, state, transport=transport, **kw)
    text = str(env.get("result") or "").lower()
    allowed = ""
    if env.get("ok") and text:
        # conservative: only set N when the memo affirmatively bars individuals
        bars = any(p in text for p in (
            "not bill", "cannot bill", "may not bill", "no provision",
            "only an enrolled", "facility", "agency", "program", "may only be billed"))
        allows = any(p in text for p in (
            "individual may bill", "personal npi may bill",
            "type 1 npi may bill", "individual practitioner may"))
        allowed = "N" if (bars and not allows) else ("Y" if allows else "")
    return {
        "code": str(code).upper(), "state": str(state).upper(),
        "individual_allowed": allowed, "who_may_bill": "", "authority": "",
        "authority_url": "", "build_date": build_date, "reviewed": "N",
        "memo": env,
    }


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
