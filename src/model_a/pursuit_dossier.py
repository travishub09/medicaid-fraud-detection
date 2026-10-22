"""
pursuit_dossier.py — the standing end-of-run artifact: the top-10 pursuit list.

Every model run must end with the document a human actually acts on: the ten
cases most worth pursuing, written twice — once so anyone can read it (plain
language, no jargon) and once for the technical reader (the named signals,
peer percentiles, and dollars behind each pick). This module is that output,
hard-coded into the run flow:

  matrix ──► leak-screened out-of-fold scores (company_list's scorer, with its
             collapse guard) ──► org rollup with named scheme drivers ──►
             top-10 targets ──► PURSUIT_DOSSIER.md (plain + technical per
             target) + pursuit_research_prompts.jsonl (one Manus supplemental-
             research task per target, ready to dispatch)

Manus is part of the contract: every prospect in the dossier gets a public-
records research prompt (corporate registrations, ownership, enforcement
history, news, facility footprint). ``--dispatch`` sends them live when
MANUS_API_KEY is set (task ids saved for free re-attachment); ``--poll``
collects finished results and regenerates the dossier with a research section
per target. Without a key the prompts file still ships, so research is never
silently skipped — it is visibly pending.

Guardrails are inherited, not optional: the disclaimer and alternative-
explanation frame come from dossier.py; individuals appear as NPI + city,
never by personal name; organizations are named from public enrollment
records; no output contains a fraud boolean or an accusation.

  python -m src.model_a.pursuit_dossier --run-dir <frozen_folder> [--top 10]
      [--dispatch] [--poll]
"""

from __future__ import annotations

import argparse
import json
import re
import time as time_module
import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .company_list import oof_scores, rollup
from .dossier import DISCLAIMER, SCHEME_NARRATIVES

MANUS_KEY_ENV = "MANUS_API_KEY"


# --- target assembly --------------------------------------------------------

def top_targets(matrix: pd.DataFrame, manifest: dict, top: int = 10,
                min_billed: float = 250_000.0) -> pd.DataFrame:
    """Score, roll up, and select the pursuit list.

    ``min_billed`` keeps sub-scale orgs off a PURSUIT list (a case needs
    dollars to be worth counsel's time) without touching the underlying
    ranking, which ships separately in company_list.csv."""
    scores = oof_scores(matrix, manifest)
    orgs = rollup(matrix, scores, drop_excluded=True)
    orgs = orgs[orgs["total_billed"] >= float(min_billed)]
    return orgs.head(int(top)).reset_index(drop=True)


def top_targets_by_damages(matrix: pd.DataFrame, manifest: dict,
                           top: int = 10,
                           min_damages: float = 10_000_000.0) -> pd.DataFrame:
    """The recovery-space pursuit list under the universal rule (operator
    decision 2026-08-04): FRAUD SIGNAL is the only sort; dollars and entity
    class are FILTERS. Orgs must clear the hot-money floor, the gov screen,
    and ``min_damages``/yr of scheme-aware case size (a $3M-billing kickback
    whose tainted claims clear the bar stays; a $12M-billing org with $200k
    of excess drops) — then rank by dollar-weighted detection score, ties by
    max member score. Case size never orders the list; it only gates it."""
    from .case_size import org_case_size
    scores = oof_scores(matrix, manifest)
    orgs = rollup(matrix, scores, drop_excluded=True)
    sizes = org_case_size(matrix)
    orgs = orgs.merge(sizes, on="org_id", how="left")
    orgs["case_size_yr"] = pd.to_numeric(orgs["case_size_yr"],
                                         errors="coerce").fillna(0.0)
    hot = pd.to_numeric(orgs["score_dollar_weighted"],
                        errors="coerce").fillna(0.0) >= 0.5
    gov = government_mask(orgs["org_name"])
    keep = orgs[hot & ~gov & (orgs["case_size_yr"] >= float(min_damages))]
    return (keep.sort_values(["score_dollar_weighted", "score_max"],
                             ascending=False)
            .head(int(top)).reset_index(drop=True))


def _member_lines(matrix: pd.DataFrame, scores: np.ndarray, org_id: str,
                  k: int = 3) -> list[dict]:
    """Top-scored member NPIs for one org — NPI + city + their loudest signal."""
    m = matrix.loc[matrix["org_node_id"].astype(str) == str(org_id)].copy()
    if not len(m):
        return []
    m["_s"] = scores[m.index]
    pct_cols = [c for c in m.columns if c.endswith("__peerpct")]
    out = []
    for _, r in m.nlargest(k, "_s").iterrows():
        driver, val = "", float("nan")
        for c in pct_cols:
            v = pd.to_numeric(pd.Series([r[c]]), errors="coerce").iat[0]
            if v == v and (val != val or v > val):
                driver, val = c.replace("__peerpct", ""), float(v)
        out.append({"npi": str(r.get("npi", "")),
                    "city": str(r.get("addr_city", "") or ""),
                    "state": str(r.get("practice_state", "") or ""),
                    "top_driver": driver, "driver_pctile": val})
    return out


def _dollars(v: float) -> str:
    v = float(v or 0)
    if v >= 1e9:
        return f"${v / 1e9:.1f} billion"
    if v >= 1e6:
        return f"${v / 1e6:.1f} million"
    return f"${v:,.0f}"


def _clip_sentence(text: str, cap: int = 4000) -> str:
    """Clip a research summary at a sentence boundary, never mid-word.
    A hard cap keeps a runaway summary from swallowing the dossier; the
    clip backs up to the last '. ' before the cap so the reader is never
    left hanging on 'emergency medicin'."""
    if len(text) <= cap:
        return text
    cut = text[:cap]
    dot = cut.rfind(". ")
    return (cut[:dot + 1] if dot > cap // 2 else cut.rsplit(" ", 1)[0]) \
        + " [truncated — full text in the research registry]"


def _plain_story(row: pd.Series, members: list[dict]) -> str:
    """The 8th-grade paragraph: what we saw, in ordinary words."""
    name = row.get("org_name") or f"organization {row.get('org_id')}"
    schemes = [s for s in str(row.get("top_schemes") or "").split("|") if s]
    bits = []
    for s in schemes[:2]:
        label, desc = SCHEME_NARRATIVES.get(
            s, (f"a {s.replace('_', ' ')} pattern", ""))
        bits.append(f"{label}" + (f" ({desc})" if desc else ""))
    pattern = " and ".join(bits) if bits else "billing that stands out from peers"
    st = ", ".join(sorted({m["state"] for m in members if m["state"]})) or "its state"
    return (f"{name} billed Medicaid about {_dollars(row.get('total_billed'))} "
            f"across {int(row.get('n_providers', 0)):,} provider number(s) in "
            f"{st}. Compared with similar providers, its billing most "
            f"resembles {pattern}. That does not mean anyone did anything "
            f"wrong. It means the numbers look unusual enough that a person "
            f"should take a careful look.")


def _technical_block(row: pd.Series, members: list[dict]) -> list[str]:
    L = [f"- **Score percentile:** {float(row.get('score_pct', 0)):.4f} "
         f"(dollar-weighted score {float(row.get('score_dollar_weighted', 0)):.4f}, "
         f"max member score {float(row.get('score_max', 0)):.4f})",
         f"- **Named scheme drivers:** "
         f"{str(row.get('top_schemes') or '—').replace('|', ', ') or '—'}",
         f"- **Scale:** {_dollars(row.get('total_billed'))} billed, "
         f"{int(row.get('n_providers', 0)):,} member provider number(s)"]
    if float(row.get("case_size_yr") or 0) > 0:
        L.append(f"- **Case size (basis: "
                 f"{row.get('case_size_basis', 'n/a')}):** "
                 f"{str(row.get('case_size_line') or '')}")
    cautions = [c for c in str(row.get("screening_cautions") or "").split(" | ") if c]
    for c in cautions:
        L.append(f"- **Screening caution:** {c}")
    if members:
        L.append("- **Loudest member signals** (NPI, city, top peer-percentile "
                 "driver):")
        for m in members:
            d = (f"{m['top_driver']} at the {m['driver_pctile']:.0%} peer "
                 f"percentile" if m["top_driver"] and m["driver_pctile"] == m["driver_pctile"]
                 else "no peer-percentile driver resolved")
            L.append(f"    - {m['npi']} ({m['city']}, {m['state']}): {d}")
    return L


# --- Manus supplemental research --------------------------------------------

def research_prompt(row: pd.Series, members: list[dict]) -> str:
    """The org-grain public-records prompt for one prospect."""
    name = row.get("org_name") or f"organization {row.get('org_id')}"
    st = ", ".join(sorted({m["state"] for m in members if m["state"]})) or "unknown state"
    npis = ", ".join(m["npi"] for m in members) or "n/a"
    return (
        f"You are a healthcare business-records researcher. Using PUBLIC "
        f"sources only (state corporate registries, CMS/Medicare enrollment "
        f"and ownership records, licensing boards, court dockets, news, the "
        f"organization's own site), profile the Medicaid-billing organization "
        f"'{name}' ({st}; associated provider numbers include {npis}). Report "
        f"no patient information and make no accusation about any person. "
        f"Return a JSON attachment with keys: corporate (legal entities, "
        f"owners/officers, formation dates, registered agents), footprint "
        f"(locations, service lines, approximate workforce), enforcement "
        f"(exclusions, license actions, settlements, lawsuits — with source "
        f"URLs), news (relevant coverage with URLs), red_flags_context "
        f"(ownership changes, rapid expansion, related entities), and "
        f"sources (every URL used). If something cannot be found in public "
        f"records, say so rather than guessing. Do not pause to ask "
        f"questions.")


def write_prompts(targets: pd.DataFrame, member_map: dict, out_dir: Path) -> Path:
    p = out_dir / "pursuit_research_prompts.jsonl"
    with open(p, "w", encoding="utf-8") as fh:
        for _, row in targets.iterrows():
            fh.write(json.dumps({
                "org_id": str(row.get("org_id")),
                "org_name": str(row.get("org_name") or ""),
                "prompt": research_prompt(row, member_map.get(str(row.get("org_id")), [])),
            }) + "\n")
    return p


def _is_rate_limited(exc: Exception) -> bool:
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None) == 429 or "429" in str(exc)


def _task_id_from(created) -> str:
    """The task id from any response shape the service has used: top-level
    task_id / id, task.id, or data.task_id / data.id."""
    if not isinstance(created, dict):
        return ""
    for k in ("task_id", "id"):
        if created.get(k):
            return str(created[k])
    for holder in ("task", "data", "result"):
        inner = created.get(holder)
        if isinstance(inner, dict):
            for k in ("task_id", "id"):
                if inner.get(k):
                    return str(inner[k])
    return ""


def _is_transient(exc: Exception) -> bool:
    """429 (rate limit) or a 5xx (the research service's own error, seen live as
    'HTTP 500 for /v2/task.create'): retry with backoff, then stop cleanly with the
    registry intact so a re-run resumes — never a traceback that hides how many
    paid tasks already exist."""
    if _is_rate_limited(exc):
        return True
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    return code is not None and 500 <= int(code) < 600     # a bare RuntimeError still raises


def dispatch_research(prompts_path: Path, registry_path: Path,
                      transport=None, max_new: int | None = None,
                      backoff_s: tuple = (20, 40, 80), sleep=None) -> dict:
    """Send one Manus task per prospect; re-attach for free on re-runs.

    Resilient by construction: the registry is written after EVERY create
    (a crash or rate limit half-way never orphans paid tasks), a 429 is
    retried with backoff and, if it persists, the loop stops and reports
    ``rate_limited`` so the operator re-runs later (the registry skips what
    is already dispatched); ``max_new`` caps tasks per call for accounts
    with a concurrency limit."""
    import time as _time
    sleep = sleep or _time.sleep
    if transport is None:
        from src.feeds.manus_research import ManusTransport
        key = os.environ.get(MANUS_KEY_ENV, "")
        if not key:
            return {"dispatched": 0,
                    "note": f"{MANUS_KEY_ENV} not set — prompts written, "
                            "research pending (not silently skipped)"}
        transport = ManusTransport(api_key=key)
    # ONE dispatcher per registry: two running at once each send what the other
    # has not yet written (seen live: 280 + 270 + 178 creates for a 280-prompt
    # batch, ~450 paid duplicates). A lock file younger than 6 hours refuses.
    lock = registry_path.with_suffix(".lock")
    if lock.exists():
        age_h = (time_module.time() - lock.stat().st_mtime) / 3600.0
        if age_h < 6:
            return {"dispatched": 0, "refused": True,
                    "note": f"another dispatch of this batch is running or ended abnormally {age_h:.1f}h ago "
                            f"({lock.name}); wait for it, or delete the lock file if no other window is dispatching"}
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()), encoding="utf-8")
    try:
        return _dispatch_locked(prompts_path, registry_path, transport, max_new, backoff_s, sleep)
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _dispatch_locked(prompts_path: Path, registry_path: Path, transport, max_new, backoff_s, sleep) -> dict:
    reg = (json.loads(registry_path.read_text(encoding="utf-8"))
           if registry_path.exists() else {})
    n_new, rate_limited, skipped, last_error, no_id_streak = 0, False, 0, "", 0
    for line in prompts_path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        oid = item["org_id"]
        if reg.get(oid, {}).get("task_id"):
            continue                                  # already dispatched
        if oid in reg and not reg[oid].get("task_id"):
            reg[oid] = {**reg[oid], "note": "earlier create returned no id; retried"}
        if max_new is not None and n_new >= max_new:
            skipped += 1
            continue
        created = None
        for attempt, wait in enumerate((0,) + tuple(backoff_s)):
            if wait:
                sleep(wait)
            try:
                created = transport.create(item["prompt"])
                break
            except Exception as e:                    # noqa: BLE001
                if _is_transient(e) and attempt < len(backoff_s):
                    continue
                if _is_transient(e):
                    rate_limited = True
                    last_error = str(e)[:200]
                    break
                registry_path.write_text(json.dumps(reg, indent=2), encoding="utf-8")
                raise
        if rate_limited:
            break
        tid = _task_id_from(created)
        if not tid:
            # A 200 without a task id is NOT a created task (the service answers
            # quota / concurrency refusals in the body). Never count it, never
            # store a None id (a None id made re-runs re-create the same task —
            # seen live: 280 -> 270 -> 178 "dispatched" on three runs), and stop
            # after a few in a row so the operator sees the body.
            no_id_streak += 1
            last_error = f"create returned no task id: {json.dumps(created)[:200]}"
            if no_id_streak >= 3:
                rate_limited = True
                break
            continue
        no_id_streak = 0
        reg[oid] = {"task_id": tid, "org_name": item["org_name"]}
        n_new += 1
        registry_path.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    registry_path.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    out = {"dispatched": n_new, "total": len(reg)}
    if rate_limited:
        out["rate_limited"] = True
        out["note"] = ("the research service refused after retries (" + (last_error or "429") +
                       ") — re-run --dispatch later; already-dispatched prospects are skipped")
    if skipped:
        out["remaining"] = skipped
    return out


def poll_research(registry_path: Path, out_path: Path, transport=None) -> dict:
    """Collect finished research; returns {org_id: envelope} for the renderer."""
    if not registry_path.exists():
        return {}
    if transport is None:
        from src.feeds.manus_research import ManusTransport
        key = os.environ.get(MANUS_KEY_ENV, "")
        if not key:
            return {}
        transport = ManusTransport(api_key=key)
    reg = json.loads(registry_path.read_text(encoding="utf-8"))
    results = (json.loads(out_path.read_text(encoding="utf-8"))
               if out_path.exists() else {})
    for oid, info in reg.items():
        if oid in results and results[oid].get("status") == "completed":
            continue
        tid = info.get("task_id")
        if not tid:
            continue
        try:
            env = transport.get(tid)
            results[oid] = {"status": env.get("status"),
                            "output": env.get("output"),
                            "attachments": env.get("attachments") or [],
                            # the service's own reason for a failure ("You don't have
                            # enough credits ..."), so a tally can say WHY
                            "error_detail": env.get("error_detail")}
        except Exception as e:
            results[oid] = {"status": "error", "output": str(e)}
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False,
                                   default=str), encoding="utf-8")
    return results


# --- rendering ---------------------------------------------------------------

def render(targets: pd.DataFrame, member_map: dict, run_name: str,
           research: dict | None = None) -> str:
    research = research or {}
    L = [f"# PURSUIT DOSSIER — top {len(targets)} cases, run {run_name}",
         "", DISCLAIMER, "",
         "_Every case below is written twice: **The short story** is for any "
         "reader; **The technical picture** is the named signals behind the "
         "pick. Supplemental public-records research is dispatched for every "
         "prospect via the research agent; its status is shown per case._",
         ""]
    for i, (_, row) in enumerate(targets.iterrows(), 1):
        oid = str(row.get("org_id"))
        members = member_map.get(oid, [])
        L.append(f"## {i}. {row.get('org_name') or oid}")
        L.append("")
        L.append("**The short story.** " + _plain_story(row, members))
        L.append("")
        L.append("**The technical picture.**")
        L.extend(_technical_block(row, members))
        r = research.get(oid)
        if r and r.get("status") == "completed":
            summary = r.get("output") or ""
            if isinstance(summary, str) and summary.strip():
                L.append("- **Supplemental research (public records):** "
                         + _clip_sentence(summary.strip().replace("\n", " ")))
        elif r:
            L.append(f"- **Supplemental research:** {r.get('status')} — rerun "
                     "with --poll to collect")
        else:
            L.append("- **Supplemental research:** pending dispatch "
                     "(pursuit_research_prompts.jsonl)")
        L.append("")
    L.append("_Alternative explanations are mandatory reading before any "
             "outreach: large legitimate operations, grant-funded programs, "
             "unusual-but-lawful specialties, and data artifacts all produce "
             "high percentiles. The research step exists to surface exactly "
             "those before a human spends time or money._")
    L.append("")
    return "\n".join(L)


# entity classes that are not qui tam defendants for this product:
# sovereigns (no FCA liability per Stevens) and municipal umbrella agencies
GOV_RE = (r"\b(?:STATE OF|COMMONWEALTH|COUNTY|CITY OF|CITY &|CITY AND|"
          r"DEPARTMENT|DEPT\.?|AUTHORITY|BUREAU|DIVISION OF|"
          r"HEALTH AND HOSPITALS|PUBLIC HEALTH|BOARD OF|"
          # v6 additions: instrumentality forms the first screen missed
          r"HOSPITAL DISTRICT|HEALTH DISTRICT|HOSPITAL AUTHORITY|"
          r"REGENTS OF|BOARD OF REGENTS|PUBLIC BENEFIT|"
          # consensus-list addition: municipal corporations billing under
          # the city's own name (MAYOR AND CITY COUNCIL OF BALTIMORE)
          r"MAYOR|CITY COUNCIL|TOWN OF|VILLAGE OF|MUNICIPAL)\b")

# Known public/instrumentality health systems whose enrollment names carry no
# governmental cue — the Hennepin class (a county public corporation whose
# board the county dissolved) and unambiguous state-arm universities. A name
# regex cannot decide public vs private for universities in general
# (Rochester, Miami, Penn are private), so this list stays SHORT, curated,
# and limited to entities we have positively identified as public arms;
# ambiguous universities get a screening CAUTION instead of a filter.
PUBLIC_SYSTEM_NAMES = (
    "HENNEPIN HEALTHCARE",              # Hennepin County public corporation
    "UNIVERSITY OF NORTH CAROLINA",     # NC state instrumentality
    "UNIVERSITY OF CALIFORNIA",         # UC Regents
    "STATE UNIVERSITY OF NEW YORK",     # SUNY
    "DENVER HEALTH",                    # Denver public authority
    "COOK COUNTY HEALTH",               # already county-matched; belt+braces
    "HARRIS HEALTH",                    # Harris County district
    "PARKLAND HEALTH",                  # Dallas County district
)
_PUBLIC_SYSTEM_RE = "|".join(PUBLIC_SYSTEM_NAMES)


def government_mask(names: pd.Series) -> pd.Series:
    """The full government/public-entity screen: instrumentality name forms
    (GOV_RE) plus the curated public-system list. Filters, never sorts."""
    s = names.astype(str)
    return (s.str.contains(GOV_RE, na=False)
            | s.str.contains(_PUBLIC_SYSTEM_RE, na=False, regex=True))

# entity_segment name cues: the "institutional book" (hospitals, academic and
# multi-site systems, mega-groups, anchor classes) vs the "operator book"
# (owner-run agencies — the classic clean whistleblower-case profile)
_INSTITUTIONAL_RE = (r"\b(?:HOSPITAL|HOSPITALS|MEDICAL CENTER|HEALTH SYSTEM|"
                     r"HEALTHCARE SYSTEM|UNIVERSITY|COLLEGE|INFIRMARY|"
                     r"MEDICAL GROUP|PHYSICIANS NETWORK|HEALTH CENTER|"
                     r"HEALTH CENTERS|NEIGHBORHOOD HEALTH)\b")

# NATIONAL NAMES (2026-10-13, the v12 operator-book dossier named Mayo Clinic
# Arizona, SSM Health, Sutter VNA, Assurex, Ameritox and Alere): hospital systems,
# national labs and national chains whose subsidiaries carry ONE billing NPI and
# no cue word, so neither the 25-provider rule nor _INSTITUTIONAL_RE catches
# them. Curated, not inferred; a name here is the do-not-send-a-PI class (the
# consensus-book read of 2026-09-16), never a signal about the organization and
# NEVER a filter on the graded book: these chains settle qui tam cases (an
# employee relator), so they stay in the pursuit book and its grade; the list
# only keeps them out of the dossier's PI target pick (and flags them as
# ``national_chain`` on the priority list).
_NATIONAL_NAMES = (
    "MAYO CLINIC", "SSM HEALTH", "SUTTER", "KAISER", "CLEVELAND CLINIC", "GEISINGER",
    "INTERMOUNTAIN", "ASCENSION", "HCA HEALTHCARE", "TENET", "COMMONSPIRIT", "DIGNITY HEALTH",
    "PROVIDENCE HEALTH", "ADVENTHEALTH", "ADVENTIST HEALTH", "BAPTIST HEALTH", "NORTHWELL",
    "MOUNT SINAI", "NYU LANGONE", "MONTEFIORE", "JOHNS HOPKINS", "MASS GENERAL",
    "BRIGHAM", "TRINITY HEALTH", "ATRIUM HEALTH", "NOVANT", "UPMC", "HIGHMARK",
    "CHRISTUS", "BANNER HEALTH", "SCRIPPS", "SHARP HEALTHCARE", "CEDARS", "OCHSNER",
    "LIFEPOINT", "COMMUNITY HEALTH SYSTEMS", "PRIME HEALTHCARE", "STEWARD HEALTH",
    "UNIVERSAL HEALTH SERVICES", "ACADIA HEALTHCARE", "SELECT MEDICAL", "ENCOMPASS HEALTH",
    "KINDRED", "AMEDISYS", "LHC GROUP", "GENTIVA", "BAYADA", "MAXIM HEALTHCARE", "AVEANNA",
    "ADDUS", "HELP AT HOME", "BRIGHTSPRING", "RESCARE", "DAVITA", "FRESENIUS",
    "QUEST DIAGNOSTICS", "LABCORP", "LABORATORY CORPORATION OF AMERICA", "BIOREFERENCE",
    "AMERITOX", "ALERE", "ASSUREX", "MILLENNIUM HEALTH", "MYRIAD GENETICS", "NATERA",
    "EXACT SCIENCES", "GENOMIC HEALTH", "SONIC HEALTHCARE", "CVS", "WALGREENS", "RITE AID",
    "OMNICARE", "PHARMERICA", "LINCARE", "APRIA", "ROTECH", "ADAPTHEALTH", "HANGER",
    "MEDNAX", "PEDIATRIX", "TEAM HEALTH", "TEAMHEALTH", "ENVISION HEALTHCARE",
    "SOUND PHYSICIANS", "US ANESTHESIA PARTNERS", "ASPEN DENTAL", "SMILE BRANDS",
    "VISITING NURSE ASSOCIATION", "VISITING NURSE SERVICE", "VNA OF", "PLANNED PARENTHOOD",
    # v15 dossier (2026-10-14): a Wisconsin hospital system and a national transplant-testing lab
    "AURORA HEALTH CARE", "ADVOCATE AURORA", "CAREDX",
    # v19 dossier (2026-10-22): a Virginia hospital system and a national genetic-testing lab
    "INOVA", "INVITAE",
)
_NATIONAL_NAMES_RE = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in _NATIONAL_NAMES) + r")\b")


def is_national_name(name: str) -> bool:
    """True when the org name carries a curated hospital-system / national-lab /
    national-chain name (the do-not-send class the cue words miss)."""
    return bool(_NATIONAL_NAMES_RE.search(str(name or "").upper()))


# a "LAST, FIRST [MIDDLE]" name with no business token is a person, whatever the
# org roster says (v11 dossier: "MERKLEY, PATRICIA" sat at target 9 because the
# solo-individual test needs entity_type 1 on a one-NPI org and the row had two)
_PERSON_NAME_RE = re.compile(r"^[A-Z][A-Z'\-\. ]{1,40},\s*[A-Z][A-Z'\-\. ]{1,40}$")
_BUSINESS_TOKEN_RE = re.compile(r"\b(?:INC|LLC|LLP|L\.L\.C|PC|P\.C|PA|P\.A|PLLC|CORP|CO|LTD|LP|GROUP|"
                                r"ASSOC|ASSOCIATES|PARTNERS|SERVICES|CENTER|CLINIC|CARE|HEALTH|"
                                r"MEDICAL|PHARMACY|HOSPICE|HOME|AGENCY|FOUNDATION|TRUST|DBA)\b")


def person_shaped_name(name: str) -> bool:
    """True for 'LAST, FIRST' names that carry no business token."""
    n = str(name or "").strip().upper()
    return bool(_PERSON_NAME_RE.match(n)) and not _BUSINESS_TOKEN_RE.search(n)


def unnamed_org(name: str) -> bool:
    n = str(name or "").strip()
    return n == "" or n.lower() == "nan" or n.upper().startswith("UNNAMED ORGANIZATION")


# single provider number billing above this is intermediary-shaped: fiscal
# agents for self-directed care channel MANY caregivers' services through one
# NPI, so org-level anomaly math describes the program, not the biller
INTERMEDIARY_SCALE = 250e6


def screening_cautions(row: pd.Series, matrix: pd.DataFrame) -> list[str]:
    """Visible per-target cautions the v5 real-data list demanded. These
    ANNOTATE rather than exclude: universities and FQHCs do settle FCA cases
    (Duke paid $112M), so silently dropping them would hide real targets —
    but the reader must know the entity class before spending research money.
    """
    out = []
    if (int(row.get("n_providers", 0) or 0) == 1
            and float(row.get("total_billed", 0) or 0) >= INTERMEDIARY_SCALE):
        out.append(
            "Single provider number at institutional scale — verify whether "
            "this NPI is a fiscal intermediary / payroll agent for "
            "self-directed care, where one number aggregates many "
            "caregivers' services and billing-anomaly math describes the "
            "program rather than the biller.")
    from .clean_anchors import INSTITUTIONAL_NAME, INSTITUTIONAL_TAXONOMIES
    name = str(row.get("org_name") or "")
    inst_name = bool(INSTITUTIONAL_NAME.search(name))
    inst_tax = False
    if "org_node_id" in matrix.columns and "primary_taxonomy" in matrix.columns:
        mem = matrix.loc[matrix["org_node_id"].astype(str)
                         == str(row.get("org_id")), "primary_taxonomy"]
        if len(mem):
            inst_tax = (mem.fillna("").astype(str).str.upper()
                        .isin(INSTITUTIONAL_TAXONOMIES).mean() > 0.5)
    if inst_name or inst_tax:
        out.append(
            "Institutional-anchor class (FQHC / public clinic / academic / "
            "government-affiliated): such entities can be FCA defendants, "
            "but confirm the entity's legal status (sovereign arms are not) "
            "and public-disclosure exposure before outreach.")
    if "UNIVERSITY" in name.upper() and not out:
        out.append(
            "University-affiliated: a name screen cannot decide public vs "
            "private (Rochester and Penn are private; UNC and UC are state "
            "arms). Confirm whether the entity is a sovereign arm before "
            "spending research money — state arms cannot be FCA defendants.")
    return out


def build(run_dir: str | Path, top: int = 10, dispatch: bool = False,
          poll: bool = False, transport=None,
          min_billed: float = 250_000.0,
          min_damages: float | None = None,
          min_hot: float = 0.5,
          include_government: bool = False,
          operator_book: bool = False) -> Path:
    d = Path(run_dir)
    matrix = pd.read_parquet(d / "provider_features_for_model.parquet")
    manifest = json.loads((d / "feature_manifest.json").read_text(encoding="utf-8"))
    scores = oof_scores(matrix, manifest)
    orgs = rollup(matrix, scores, drop_excluded=True)
    from .case_size import org_case_size, size_line
    sizes = org_case_size(matrix)
    orgs = orgs.merge(sizes, on="org_id", how="left")
    orgs["case_size_yr"] = pd.to_numeric(
        orgs.get("case_size_yr"), errors="coerce").fillna(0.0)
    if operator_book:
        # THE OPERATOR BOOK (2026-10-11, from the v10 dossier): the default top-10
        # drew five hospitals and a national DME chain from the main list, the
        # do-not-send class the priority list's pursuit companion already filters
        # out. Same rule as entity_segment: no hospital / system / academic names,
        # no 25+ provider orgs, no government entities, no solo individuals.
        names = orgs["org_name"].astype(str)
        inst = (names.map(lambda s: bool(re.search(_INSTITUTIONAL_RE, s)))
                | names.map(is_national_name)        # v13: Mayo / SSM / Sutter / Assurex / Ameritox / Alere
                | (pd.to_numeric(orgs["n_providers"], errors="coerce").fillna(1) >= 25))
        gov = government_mask(names)
        solo = pd.Series(False, index=orgs.index)
        if "entity_type" in matrix.columns and "org_node_id" in matrix.columns:
            mk = matrix["org_node_id"].astype(str)
            solo_ids = set(matrix.loc[~mk.duplicated(keep=False)
                                      & (matrix["entity_type"].astype(str) == "1"), "org_node_id"].astype(str))
            solo = orgs["org_id"].astype(str).isin(solo_ids)
        person = names.map(person_shaped_name)
        unnamed = names.map(unnamed_org)
        n_before = len(orgs)
        orgs = orgs[~inst & ~gov & ~solo & ~person & ~unnamed]
        print(f"[pursuit_dossier] operator book: {len(orgs):,} of {n_before:,} orgs kept "
              f"(institutional / national-chain / government / solo-individual / person-named / unnamed rows set aside)")
        # and the case-size bar the pursuit book already applied (v17: sub-$1M targets)
        orgs = restrict_to_pursuit_book(orgs, d)
    if min_damages is not None:
        # recovery-space selection under the universal rule (operator
        # decision 2026-08-04): SIGNAL SORTS, EVERYTHING ELSE FILTERS.
        # Filters: hot money (dollar-weighted score floor), damages over the
        # bar (scheme-aware — kickback orgs size on tainted claims, not
        # billing), no government entities (states can't be FCA defendants;
        # counties/cities aren't this product's market). Sort: dollar-
        # weighted detection score, ties by max member score. Case size
        # gates the list; it never orders it — sorting on it re-ranked by
        # org size the moment every estimate hit the 40% ceiling (v5).
        hot = pd.to_numeric(orgs["score_dollar_weighted"],
                            errors="coerce").fillna(0.0) >= float(min_hot)
        gov = government_mask(orgs["org_name"])
        n_gov = int((gov & hot).sum())
        if n_gov:
            print(f"[pursuit_dossier] {n_gov} government-entity org(s) "
                  "excluded (use --include-government to keep)")
        keep = hot & (orgs["case_size_yr"] >= float(min_damages))
        if not include_government:
            keep &= ~gov
        orgs = orgs[keep].sort_values(
            ["score_dollar_weighted", "score_max"], ascending=False)
        n_small = 0
    else:
        n_small = int((orgs["total_billed"] < float(min_billed)).sum())
        orgs = orgs[orgs["total_billed"] >= float(min_billed)]
    targets = orgs.head(int(top)).reset_index(drop=True)
    capped = targets.get("case_size_capped",
                         pd.Series(False, index=targets.index)).fillna(False)
    targets["case_size_line"] = [
        size_line(v, capped=bool(c))
        for v, c in zip(targets["case_size_yr"], capped)]
    member_map = {str(r["org_id"]): _member_lines(matrix, scores, r["org_id"])
                  for _, r in targets.iterrows()}
    targets["screening_cautions"] = [
        " | ".join(screening_cautions(r, matrix)) for _, r in targets.iterrows()]

    prompts = write_prompts(targets, member_map, d)
    reg_path = d / "pursuit_research_tasks.json"
    res_path = d / "pursuit_research_results.json"
    if dispatch:
        info = dispatch_research(prompts, reg_path, transport=transport)
        print(f"[pursuit_dossier] research dispatch: {info}")
    research = poll_research(reg_path, res_path, transport=transport) if poll else (
        json.loads(res_path.read_text(encoding="utf-8")) if res_path.exists() else {})

    out = d / "PURSUIT_DOSSIER.md"
    out.write_text(render(targets, member_map, d.name, research), encoding="utf-8")
    targets.to_csv(d / "pursuit_targets.csv", index=False)
    print(f"[pursuit_dossier] {len(targets)} targets -> {out}")
    return out


LARGE_LIST_NAME = "provider_priority_list_large.csv"
PMAX_LIST_NAME = "provider_priority_list_pmax.csv"


BASIS_HEAD = 50
PURSUIT_ORDERS = ("dollar", "basis50", "pmax", "pmax_basis50")


def pursuit_order(book: pd.DataFrame, order: str = "dollar", head: int = BASIS_HEAD) -> pd.DataFrame:
    """The pursuit book's sort, ONE definition shared by the ship and the sweep.

    dollar        score_dollar_weighted desc, score_max tiebreak (the v7-settled order).
    basis50       the MIXED order (v13 sweep row): the basis-backed rows (case_size_capped
                  false) fill the first ``head`` slots in dollar-weighted order, then
                  everything else in dollar-weighted order. v10-v12: 44 of the top 50 were
                  cap-pinned rows with zero hits in three runs while the full basis-first
                  block won the 50 budget and lost 100 / 250; this promotes basis rows
                  only as far as one review round reaches and leaves the deep budgets to
                  the shipped order. Pre-registered as a sweep row; shipped only when it
                  beats the dollar order across budgets.
    pmax          score_p_max desc (the calibrated max member probability), dollar tiebreak.
    pmax_basis50  pmax with the basis-backed rows filling the first ``head`` slots. The
                  v17 sweep (2026-10-20) read this row 2 / 3 / 5 / 9 against the shipped
                  1 / 2 / 5 / 7: the first probability order to beat the dollar order at
                  every budget since the v7 sweep retired P(any). Graded as a THIRD book
                  (PMAX_LIST_NAME) from v18; shipped only if it holds on a second matrix.
    A book without score_p_max falls back to the dollar order for the pmax variants."""
    if order not in PURSUIT_ORDERS:
        raise ValueError(f"pursuit order {order!r}: choose from {PURSUIT_ORDERS}")
    if order.startswith("pmax") and "score_p_max" in book.columns \
            and pd.to_numeric(book["score_p_max"], errors="coerce").notna().any():
        keys = ["score_p_max", "score_dollar_weighted"] + (["score_max"] if "score_max" in book.columns else [])
        work = book.copy()
        work["score_p_max"] = pd.to_numeric(work["score_p_max"], errors="coerce").fillna(-1.0)
        ranked = work.sort_values(keys, ascending=False, kind="mergesort")
        ranked["score_p_max"] = book.loc[ranked.index, "score_p_max"]
        basis_first = order == "pmax_basis50"
    else:
        keys = ["score_dollar_weighted"] + (["score_max"] if "score_max" in book.columns else [])
        ranked = book.sort_values(keys, ascending=False, kind="mergesort")
        basis_first = order in ("basis50", "pmax_basis50")
    if not basis_first or "case_size_capped" not in ranked.columns:
        return ranked
    capped = ranked["case_size_capped"].astype(str).str.lower().isin(["true", "1"])
    basis = ranked[~capped]
    top = basis.head(head)
    rest = ranked.drop(top.index)
    return pd.concat([top, rest])


def restrict_to_pursuit_book(orgs: pd.DataFrame, run_dir: Path, log=print) -> pd.DataFrame:
    """The dossier's operator-book targets come from the PURSUIT BOOK's rows (the
    orgs that passed the ~$10M/yr scheme-aware case-size bar), in the book's order.
    v17's dossier (2026-10-20) named six California nursing homes billing $250k to
    $1.2M in the window because ``build()`` ranked the whole rollup and only set the
    institutional / government / solo rows aside; the pursuit book sat beside it,
    unread. Without a pursuit book on disk the frame is returned unchanged."""
    p = Path(run_dir) / "provider_priority_list_pursuit.csv"
    if not p.exists() or "org_id" not in orgs.columns:
        return orgs
    book = pd.read_csv(p, dtype=str)
    id_col = next((c for c in ("org_id", "org_node_id") if c in book.columns), None)
    if id_col is None:
        return orgs
    rank_col = "pursuit_rank" if "pursuit_rank" in book.columns else None
    order = {str(i): (int(float(r)) if rank_col else n)
             for n, (i, r) in enumerate(zip(book[id_col], book[rank_col] if rank_col else book[id_col]))}
    keep = orgs["org_id"].astype(str).isin(order)
    out = orgs[keep].copy()
    out["_book_rank"] = out["org_id"].astype(str).map(order)
    out = out.sort_values("_book_rank").drop(columns="_book_rank")
    log(f"[pursuit_dossier] operator book restricted to the pursuit book: {len(out):,} of {len(orgs):,} "
        f"orgs (the case-size bar the book carries), in the book's order")
    return out


def build_priority_list(run_dir: str | Path,
                        min_damages: float = 10_000_000.0,
                        include_government: bool = False,
                        mask_individuals: bool = False,
                        large_min_billed: float = 50_000_000.0,
                        pursuit_sort: str = "dollar") -> Path:
    """The full funder-facing priority list: EVERY qualifying organization,
    ranked by FRAUD SIGNAL alone. Dollars and entity class are FILTERS,
    never sort keys:

      sort     score_dollar_weighted desc (how hot the money is — a 0-1
               signal independent of how MUCH money there is), ties by
               score_max
      filter   government/public entities out (GOV_RE name screen);
               scheme-aware case size under ``min_damages``/yr out (the
               kickback estimator sizes tainted claims, so a modest-billing
               kickback org clears the bar the billing metrics would hide)

    Writes ``provider_priority_list.csv`` + ``PRIORITY_LIST.md`` (the frame,
    the column dictionary, and what the filters removed). Individuals appear
    as NPI + city, never by personal name."""
    from .case_size import org_case_size
    d = Path(run_dir)
    matrix = pd.read_parquet(d / "provider_features_for_model.parquet")
    manifest = json.loads((d / "feature_manifest.json").read_text(encoding="utf-8"))
    scores = oof_scores(matrix, manifest)
    from .company_list import calibrate_oof
    probs = calibrate_oof(matrix, scores)
    # the SAME numbers the lists below are built from, persisted so the learning loop
    # and the diagnostics analyse what shipped instead of a refit (2026-10-10)
    pd.DataFrame({"npi": matrix["npi"].astype(str), "score": scores, "prob": probs}).to_parquet(
        d / "provider_oof_scores.parquet", index=False)
    # the TRAINING label policy of the shipped list, for the registry (--check "train = hard"):
    # the forward ruler's policy is a different thing and is recorded separately
    from .company_list import training_label_summary
    tls = training_label_summary(matrix)
    (d / "training_label_policy.json").write_text(json.dumps(tls, indent=1), encoding="utf-8")
    print(f"[pursuit_dossier] training label policy {tls['label_tier']}: {tls['n_positives']:,} positives "
          f"({tls['n_doj_positives']:,} DOJ-case; in-matrix label {tls['n_in_matrix_label']:,})")
    orgs = rollup(matrix, scores, drop_excluded=True, probs=probs)
    orgs = orgs.merge(org_case_size(matrix), on="org_id", how="left")
    orgs["case_size_yr"] = pd.to_numeric(orgs["case_size_yr"],
                                         errors="coerce").fillna(0.0)

    # org → states + solo-individual detection (for NPI+city masking)
    mkey = matrix["org_node_id"].astype(str)
    states = (matrix.assign(_k=mkey)
              .groupby("_k")["practice_state"]
              .agg(lambda s: ",".join(sorted({str(x) for x in s
                                              if str(x or "").strip()})))
              if "practice_state" in matrix.columns else pd.Series(dtype=str))
    orgs["states"] = orgs["org_id"].astype(str).map(states).fillna("")

    gov = government_mask(orgs["org_name"])
    small = orgs["case_size_yr"] < float(min_damages)
    out = orgs[~small & (~gov | include_government)].copy()

    # every identifier in the data ships: the full member-NPI roster per org
    # (names come from public enrollment records — NPPES/PECOS)
    npis = (matrix.assign(_k=mkey).groupby("_k")["npi"]
            .agg(lambda s: ";".join(sorted(str(x) for x in s)))
            if "npi" in matrix.columns else pd.Series(dtype=str))
    out["member_npis"] = out["org_id"].astype(str).map(npis).fillna("")
    cities = (matrix.assign(_k=mkey).groupby("_k")["addr_city"]
              .agg(lambda s: ";".join(sorted({str(x) for x in s
                                              if str(x or "").strip()})[:5]))
              if "addr_city" in matrix.columns else pd.Series(dtype=str))
    out["cities"] = out["org_id"].astype(str).map(cities).fillna("")

    # optional masking (--mask-individuals) for versions of this file that
    # leave the operator's hands; the default ships enrollment names as-is
    if mask_individuals and "entity_type" in matrix.columns:
        solo = matrix.loc[~mkey.duplicated(keep=False)
                          & (matrix["entity_type"].astype(str) == "1")]
        mask_map = {
            str(r["org_node_id"]):
            f"Individual provider (NPI {r.get('npi', '')}, "
            f"{str(r.get('addr_city', '') or '')}, "
            f"{str(r.get('practice_state', '') or '')})"
            for _, r in solo.iterrows()}
        hit = out["org_id"].astype(str).map(mask_map)
        out["org_name"] = hit.fillna(out["org_name"])

    out["screening_cautions"] = [
        " | ".join(screening_cautions(r, matrix)) for _, r in out.iterrows()]

    # entity_segment — the two-books triage lens (institutional vs operator
    # vs individual). Labels rows; never touches score or rank.
    solo_person = set()
    if "entity_type" in matrix.columns:
        solo_person = set(
            matrix.loc[~mkey.duplicated(keep=False)
                       & (matrix["entity_type"].astype(str) == "1"),
                       "org_node_id"].astype(str))
    seg = pd.Series("operator", index=out.index)
    # NOTE (v13, 2026-10-13): the curated national-name list is NOT applied here.
    # It moved 43 orgs out of the graded pursuit book and five of v12's eight FCA
    # hits with them: national home-health / DME / lab chains ARE qui tam
    # defendants (an employee relator, a settlement), they are only not a PI
    # visit. The list filters the dossier's PI target pick (below) and is
    # written here as a flag column so the reader can see it.
    inst = (out["org_name"].astype(str).str.contains(_INSTITUTIONAL_RE, na=False)
            | (pd.to_numeric(out["n_providers"], errors="coerce")
               .fillna(1) >= 25)
            | (out["screening_cautions"].astype(str).str.len() > 0))
    seg[inst] = "institutional"
    seg[out["org_id"].astype(str).isin(solo_person)] = "individual"
    out["entity_segment"] = seg
    out["national_chain"] = out["org_name"].astype(str).map(is_national_name)

    out = out.sort_values(["score_dollar_weighted", "score_max"],
                          ascending=False).reset_index(drop=True)
    out.insert(0, "priority_rank", out.index + 1)
    cols = [c for c in ("priority_rank", "org_name", "entity_segment", "national_chain",
                        "org_id", "member_npis",
                        "states", "cities",
                        "n_providers", "score_dollar_weighted", "score_max",
                        "score_p_any", "score_p_max",
                        "score_pct", "top_schemes", "case_size_yr",
                        "case_size_basis", "case_size_capped", "total_billed",
                        "n_excluded_members", "screening_cautions")
            if c in out.columns]
    csv_path = d / "provider_priority_list.csv"
    out[cols].to_csv(csv_path, index=False)

    # THE PURSUIT BOOK (2026-10-09, from the v6 / v7 accuracy diagnostics): the
    # dollar-weighted list's first 50 were 19-24 institutional + 24-30 case-size-
    # capped organizations and zero operator-class ones, with zero or one future
    # case among them. This companion file holds the OPERATOR book only (no
    # hospital / system / 25+ provider orgs, no solo individuals). The main list
    # above is unchanged for registry continuity.
    #
    # SORT KEY (settled by the v7 order sweep, ACCURACY_DIAGNOSTICS section 2b,
    # FCA ruler, 1,855 operator orgs, 8 future cases in the pool):
    #   score_dollar_weighted   1 / 2 / 3 / 4 hits at 16 / 50 / 100 / 250
    #   score_p_max, score_max  0 / 2 / 2 / 3
    #   score_p_any             0 / 0 / 0 / 2
    #   basis-backed first, any key   0 / 0 / 0 / 0-3
    # The operator FILTER is the win (the main list had 0 / 1 / 2 / 3 at the
    # same budgets); the calibrated P(any) sort and the basis-first block both
    # LOST hits, so the book keeps the dollar-weighted order with score_max as
    # the tiebreak. score_p_any / score_p_max stay as COLUMNS for the reader.
    pursuit = out[out["entity_segment"] == "operator"].copy()
    pursuit = pursuit_order(pursuit, order=pursuit_sort).reset_index(drop=True)
    print(f"[priority_list] pursuit book order: {pursuit_sort}"
          + (f" (basis-backed rows fill the first {BASIS_HEAD} slots, dollar-weighted below)"
             if pursuit_sort == "basis50" else " (dollar-weighted, score_max tiebreak)"))
    pursuit.insert(0, "pursuit_rank", pursuit.index + 1)
    pursuit_cols = ["pursuit_rank"] + [c for c in cols if c != "priority_rank"] + ["priority_rank"]
    pursuit[[c for c in pursuit_cols if c in pursuit.columns]].to_csv(d / "provider_priority_list_pursuit.csv", index=False)

    # THE LARGE-BILLER BOOK (2026-10-10, the fourth post-audit lever): the v7
    # segment table found the model's precision highest among the largest
    # billers (over $10M billed in the matrix window: top 1% hit at 4.6%, lift
    # 16) while the main list buried them under hospitals and small agencies.
    # This companion holds the operator book restricted to that band (the same
    # ``net_paid`` sum the diagnostics' size_band uses), in the SAME order as the
    # pursuit book, so the within-band ranking can be graded on its own with
    # --list-name provider_priority_list_large.csv. A filter, never a sort key.
    large = pursuit[pd.to_numeric(pursuit["total_billed"], errors="coerce").fillna(0.0)
                    >= float(large_min_billed)].copy()
    large = large.reset_index(drop=True)
    large.insert(0, "large_rank", large.index + 1)
    large_cols = ["large_rank"] + [c for c in pursuit_cols if c in large.columns]
    large[large_cols].to_csv(d / LARGE_LIST_NAME, index=False)
    print(f"[priority_list] large-biller book: {len(large):,} operator orgs billed >= "
          f"${large_min_billed:,.0f} -> {d / LARGE_LIST_NAME}")

    # THE PMAX BOOK (2026-10-20, from the v17 order sweep): the same operator pool
    # under the pmax_basis50 order, written as its own file so the diagnostics grade
    # it beside the pursuit and large books (--list-name provider_priority_list_pmax.csv,
    # outputs __pmax, registry acc_pmax_*). A graded candidate, never the shipped order
    # until a second matrix confirms the v17 sweep.
    pmax = pursuit_order(pursuit.drop(columns=["pursuit_rank"]), order="pmax_basis50").reset_index(drop=True)
    pmax.insert(0, "pmax_rank", pmax.index + 1)
    pmax_cols = ["pmax_rank"] + [c for c in pursuit_cols if c in pmax.columns and c != "pursuit_rank"]
    pmax[pmax_cols].to_csv(d / PMAX_LIST_NAME, index=False)
    print(f"[priority_list] pmax book: {len(pmax):,} operator orgs under the pmax_basis50 order "
          f"(a graded candidate, not the shipped sort) -> {d / PMAX_LIST_NAME}")

    md = d / "PRIORITY_LIST.md"
    md.write_text("\n".join([
        f"# PROVIDER PRIORITY LIST — run {d.name}", "", DISCLAIMER, "",
        f"- **{len(out):,} organizations** ranked by fraud signal "
        "(dollar-weighted detection score). Dollars are FILTERS here, "
        "never the sort: a big biller with cold money ranks below a modest "
        "biller with hot money.",
        f"- Filters applied: {int(gov.sum()):,} government/public entities "
        f"removed; {int((small & ~gov).sum()):,} organizations under the "
        f"~${min_damages:,.0f}/yr scheme-aware case-size bar removed "
        "(kickback-theory orgs are sized on tainted claims, not billing).",
        "- `case_size_capped` = the estimate sits at the 40%-of-billed "
        "ceiling: an upper-bound screen, not a damages estimate.",
        "- Providers are named from public enrollment records (NPPES/PECOS) "
        "and every identifier ships: the full member-NPI roster, states, and "
        "cities per organization. Every row is a statistical lead for "
        "counsel review — not an accusation, not an adjudication.",
        f"- Two books, one list: {int((out['entity_segment'] == 'institutional').sum()):,} "
        f"institutional, {int((out['entity_segment'] == 'operator').sum()):,} operator, "
        f"{int((out['entity_segment'] == 'individual').sum()):,} individual "
        "(`entity_segment` — a triage lens, never a scoring input).",
        f"- Companion books, same order: `provider_priority_list_pursuit.csv` "
        f"({len(pursuit):,} operator orgs) and `{LARGE_LIST_NAME}` "
        f"({len(large):,} of them billed >= ${large_min_billed:,.0f} in the matrix window, "
        "the band where the v7 and v11 size tables found precision highest). Filters, never sorts. "
        f"`{PMAX_LIST_NAME}` = the pursuit pool under the pmax_basis50 order (the v17 sweep's "
        "winning row), graded as a candidate, not the shipped sort.",
        "", "Columns: priority_rank; org_name; entity_segment "
        "(institutional / operator / individual); member_npis (full roster); "
        "states; cities; n_providers; "
        "score_dollar_weighted (the SORT — 0-1 signal); score_max; "
        "score_pct; top_schemes (named drivers); case_size_yr/basis/capped "
        "(scheme-aware annual single-damages screen); total_billed "
        "(context only); n_excluded_members; screening_cautions.", "",
    ]), encoding="utf-8")
    print(f"[priority_list] {len(out):,} orgs -> {csv_path}")
    print(f"[priority_list] frame + dictionary -> {md}")
    return csv_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--run-dir", required=True,
                    help="run folder, or '@champion' to resolve the promoted "
                         "run through the release registry (honors "
                         "MEDICAID_DATA_ROOT)")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--min-billed", type=float, default=250_000.0)
    ap.add_argument("--min-damages", type=float, default=None,
                    help="recovery-space selection: keep only hot-money orgs "
                         "whose estimated annual single damages clear this "
                         "bar (e.g. 10000000), ranked by case size")
    ap.add_argument("--min-hot", type=float, default=0.5,
                    help="minimum dollar-weighted score for the recovery "
                         "list (the money itself must be hot; default 0.5)")
    ap.add_argument("--include-government", action="store_true",
                    help="keep state/county/city umbrella entities on the "
                         "list (excluded by default)")
    ap.add_argument("--operator-book", action="store_true",
                    help="pick the dossier's targets from the OPERATOR book only (no "
                         "hospital / system / academic names, no 25+ provider orgs, no "
                         "government entities, no solo individuals): the class the "
                         "whistleblower strategy targets and the pursuit companion list "
                         "grades; the default top-10 is the main list")
    ap.add_argument("--dispatch", action="store_true",
                    help="send the Manus research tasks now (needs "
                         f"${MANUS_KEY_ENV})")
    ap.add_argument("--dispatch-file", default=None,
                    help="dispatch a CUSTOM prompts .jsonl (one {org_id, "
                         "org_name, prompt} per line) through the run's "
                         "research registry WITHOUT rebuilding the dossier "
                         "— for metro/national batches authored elsewhere")
    ap.add_argument("--poll", action="store_true",
                    help="collect finished research and fold it into the dossier")
    ap.add_argument("--priority-list", action="store_true",
                    help="write the FULL signal-ranked priority list "
                         "(provider_priority_list.csv): every org, sorted by "
                         "fraud signal; government entities and sub-bar case "
                         "sizes are FILTERS, dollars never sort")
    ap.add_argument("--label-tier", default="all", choices=list(__import__("src.model_a.fca_relevance", fromlist=["TIER_POLICIES"]).TIER_POLICIES),
                    help="training label for the production scorer: 'all' = the "
                         "in-matrix PU label (every exclusion source; ~75%% "
                         "administrative/weak events); 'hard' = only FCA-lookalike "
                         "statutes from the exclusion nodes (fraud-shaped ranking); "
                         "'hard+weak' adds misconduct not FCA on its face")
    ap.add_argument("--exclusion-nodes", default=None,
                    help="exclusion_nodes.parquet for --label-tier (default: the "
                         "run's as-of graph when frozen, else the current graph)")
    ap.add_argument("--mask-individuals", action="store_true",
                    help="priority list only: replace solo individuals' "
                         "enrollment names with NPI + city (default ships "
                         "names as enrolled)")
    ap.add_argument("--seeds", type=int, default=None,
                    help="LightGBM fits averaged per fold for the production scorer (default: the "
                         "manifest's training_policy.n_seeds, else 1)")
    ap.add_argument("--broadcast-rows", default=None, choices=["weight", "mask"],
                    help="broadcast-row training policy override (default: the manifest's)")
    ap.add_argument("--order", default="dollar", choices=list(PURSUIT_ORDERS),
                    help="pursuit-book sort: dollar (v7-settled, default) or basis50 "
                         "(basis-backed rows fill the first 50, dollar-weighted below; "
                         "ship only when the sweep says it wins)")
    ap.add_argument("--large-min-billed", type=float, default=50_000_000.0,
                    help="priority list only: the billed-in-window floor of the "
                         "large-biller companion book (provider_priority_list_large.csv; "
                         "default 50000000: at 10M every pursuit-book org qualified on v11, "
                         "since the list's case-size bar already implies 10M billed)")
    args = ap.parse_args()
    from .run_registry import resolve_run_dir
    args.run_dir = resolve_run_dir(args.run_dir)
    if args.seeds is not None or args.broadcast_rows is not None:
        from .company_list import set_training_policy
        set_training_policy(n_seeds=args.seeds, broadcast=args.broadcast_rows)
        print(f"[pursuit_dossier] training policy override: seeds={args.seeds}, broadcast={args.broadcast_rows}")
    if args.label_tier != "all":
        from .company_list import default_exclusion_nodes, set_label_policy
        ex = Path(args.exclusion_nodes) if args.exclusion_nodes else \
            default_exclusion_nodes(args.run_dir)
        if not ex.exists():
            raise SystemExit(f"--label-tier {args.label_tier} needs exclusion nodes; "
                             f"not found at {ex} (pass --exclusion-nodes)")
        mj = json.loads((Path(args.run_dir) / "feature_manifest.json").read_text(
            encoding="utf-8"))
        cutoff = mj.get("asof_cutoff") or (mj.get("graph_substrate") or {}).get("asof")
        set_label_policy(args.label_tier, ex, cutoff)
        print(f"[pursuit_dossier] production scorer label policy: {args.label_tier} "
              f"from {ex}" + (f" (records <= {cutoff})" if cutoff else ""))
    if args.dispatch_file:
        d = Path(args.run_dir)
        info = dispatch_research(Path(args.dispatch_file),
                                 d / "pursuit_research_tasks.json")
        print(f"[pursuit_dossier] custom batch: {info}")
        return
    if args.priority_list:
        build_priority_list(
            args.run_dir,
            min_damages=(args.min_damages if args.min_damages is not None
                         else 10_000_000.0),
            include_government=args.include_government,
            mask_individuals=args.mask_individuals,
            large_min_billed=args.large_min_billed,
            pursuit_sort=args.order)
        return
    build(args.run_dir, top=args.top, dispatch=args.dispatch, poll=args.poll,
          min_billed=args.min_billed, min_damages=args.min_damages,
          min_hot=args.min_hot, include_government=args.include_government,
          operator_book=args.operator_book)


if __name__ == "__main__":
    main()
