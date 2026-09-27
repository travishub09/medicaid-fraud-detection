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
                            "attachments": env.get("attachments") or []}
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
          include_government: bool = False) -> Path:
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


def build_priority_list(run_dir: str | Path,
                        min_damages: float = 10_000_000.0,
                        include_government: bool = False,
                        mask_individuals: bool = False) -> Path:
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
    orgs = rollup(matrix, scores, drop_excluded=True)
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
    inst = (out["org_name"].astype(str).str.contains(_INSTITUTIONAL_RE, na=False)
            | (pd.to_numeric(out["n_providers"], errors="coerce")
               .fillna(1) >= 25)
            | (out["screening_cautions"].astype(str).str.len() > 0))
    seg[inst] = "institutional"
    seg[out["org_id"].astype(str).isin(solo_person)] = "individual"
    out["entity_segment"] = seg

    out = out.sort_values(["score_dollar_weighted", "score_max"],
                          ascending=False).reset_index(drop=True)
    out.insert(0, "priority_rank", out.index + 1)
    cols = [c for c in ("priority_rank", "org_name", "entity_segment",
                        "org_id", "member_npis",
                        "states", "cities",
                        "n_providers", "score_dollar_weighted", "score_max",
                        "score_pct", "top_schemes", "case_size_yr",
                        "case_size_basis", "case_size_capped", "total_billed",
                        "n_excluded_members", "screening_cautions")
            if c in out.columns]
    csv_path = d / "provider_priority_list.csv"
    out[cols].to_csv(csv_path, index=False)

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
    ap.add_argument("--label-tier", default="all", choices=["all", "hard", "hard+weak"],
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
    args = ap.parse_args()
    from .run_registry import resolve_run_dir
    args.run_dir = resolve_run_dir(args.run_dir)
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
            mask_individuals=args.mask_individuals)
        return
    build(args.run_dir, top=args.top, dispatch=args.dispatch, poll=args.poll,
          min_billed=args.min_billed, min_damages=args.min_damages,
          min_hot=args.min_hot, include_government=args.include_government)


if __name__ == "__main__":
    main()
