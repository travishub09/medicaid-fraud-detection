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
    """The recovery-space pursuit list: rank score-qualified orgs by
    ESTIMATED CASE SIZE (scheme-specific single damages, annualized) and
    keep only those clearing ``min_damages`` per year. A $3M-billing
    kickback whose tainted claims clear the bar stays; a $12M-billing org
    with $200k of unexplained excess drops. Orgs must still sit in the top
    score decile — damages without detection signal is just a big org."""
    from .case_size import org_case_size
    scores = oof_scores(matrix, manifest)
    orgs = rollup(matrix, scores, drop_excluded=True)
    sizes = org_case_size(matrix)
    orgs = orgs.merge(sizes, on="org_id", how="left")
    orgs["case_size_yr"] = pd.to_numeric(orgs["case_size_yr"],
                                         errors="coerce").fillna(0.0)
    cut = orgs["score_pct"].quantile(0.90)
    keep = orgs[(orgs["score_pct"] >= cut)
                & (orgs["case_size_yr"] >= float(min_damages))]
    return (keep.sort_values("case_size_yr", ascending=False)
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


def dispatch_research(prompts_path: Path, registry_path: Path,
                      transport=None) -> dict:
    """Send one Manus task per prospect; re-attach for free on re-runs."""
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
    n_new = 0
    for line in prompts_path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        oid = item["org_id"]
        if reg.get(oid, {}).get("task_id"):
            continue                                  # already dispatched
        created = transport.create(item["prompt"])
        tid = (created.get("task_id") or created.get("id")
               or (created.get("task") or {}).get("id"))
        reg[oid] = {"task_id": tid, "org_name": item["org_name"]}
        n_new += 1
    registry_path.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    return {"dispatched": n_new, "total": len(reg)}


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


def build(run_dir: str | Path, top: int = 10, dispatch: bool = False,
          poll: bool = False, transport=None,
          min_billed: float = 250_000.0,
          min_damages: float | None = None) -> Path:
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
        # recovery-space selection: top score decile, ranked by case size
        cut = orgs["score_pct"].quantile(0.90)
        orgs = orgs[(orgs["score_pct"] >= cut)
                    & (orgs["case_size_yr"] >= float(min_damages))]
        orgs = orgs.sort_values("case_size_yr", ascending=False)
        n_small = 0
    else:
        n_small = int((orgs["total_billed"] < float(min_billed)).sum())
        orgs = orgs[orgs["total_billed"] >= float(min_billed)]
    targets = orgs.head(int(top)).reset_index(drop=True)
    targets["case_size_line"] = targets["case_size_yr"].map(size_line)
    member_map = {str(r["org_id"]): _member_lines(matrix, scores, r["org_id"])
                  for _, r in targets.iterrows()}

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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--min-billed", type=float, default=250_000.0)
    ap.add_argument("--min-damages", type=float, default=None,
                    help="recovery-space selection: keep only top-decile-score "
                         "orgs whose estimated annual single damages clear "
                         "this bar (e.g. 10000000), ranked by case size")
    ap.add_argument("--dispatch", action="store_true",
                    help="send the Manus research tasks now (needs "
                         f"${MANUS_KEY_ENV})")
    ap.add_argument("--poll", action="store_true",
                    help="collect finished research and fold it into the dossier")
    args = ap.parse_args()
    build(args.run_dir, top=args.top, dispatch=args.dispatch, poll=args.poll,
          min_billed=args.min_billed, min_damages=args.min_damages)


if __name__ == "__main__":
    main()
