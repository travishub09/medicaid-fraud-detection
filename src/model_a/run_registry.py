"""
run_registry.py — the audit log of model runs: KPIs, versions, trends, rollback.

Every major run already writes its evidence into its output folder (manifest,
expectations, network A/B, ablation, digests). This module turns those folders
into an APPEND-ONLY registry so releases are comparable over time and a bad
release can be rolled back by pointing consumers at the previous champion.

  preregister  python -m src.model_a.run_registry --preregister --tag v3 \
                   --goal "..." --expected "lift delta >= +0.5, CI clear"
               LOCKS the hypothesis and acceptance bar with a timestamp
               BEFORE the run exists — measured-vs-declared is then shown
               side by side, so impact can never be narrated after the fact.
  register     python -m src.model_a.run_registry --run-dir <folder> --tag v2
               parses the run folder's reports into one KPI row (plus a
               REPRODUCIBILITY CAPSULE: git commit, library versions, input
               hashes) and appends it to run_registry.jsonl.
  signoff      python -m src.model_a.run_registry --signoff --tag v2 \
                   --by travis --status reproduced|objection --note "..."
               the independent-reviewer gate: a release without a
               'reproduced' sign-off wears an UNREPRODUCED chip.
  report       python -m src.model_a.run_registry --report-only
               regenerates MODEL_RUNS.md (and MODEL_RUNS.html with --html):
               policy, table, declared-vs-measured, trends, recommendation,
               cohort-usage warnings, the reconstructed pre-registry release
               history (run_history.py), and — when their ledgers exist —
               economics and lead-disposition sections. The HTML has two
               view toggles (plain/technical wording, short/full
               recommendations) and per-run revert instructions.

TEST-SET HYGIENE (cohort tracking): every registered run records its eval
cohort (the freeze cutoff). Each decision against the same cohort is a peek
that slowly overfits the release process to those specific positives; the
report warns when one cohort has supported more than 5 registered runs —
time to mint a fresh freeze (prospective_label --cutoff <newer month>).

KPIs captured per run: matrix shape, git commit, PU/forward-label counts,
expectation FAIL count, the network A/B verdict + matched structural ROC
delta, the OOF ablation deltas (lift / PR / ROC with CIs), complementarity
counts, and the FILE PATHS everything lives at — so "where is that run on my
machine" is always one lookup.

PROMOTION POLICY (encoded in recommend()):
  PROMOTE   0 expectation FAILs, network verdict KEEP, ablation lift CI clear
            of zero, and lift delta not more than 0.15 below the reigning
            champion's.
  HOLD      any FAILs, a CI that includes zero, or a >0.15 lift-delta drop —
            investigate before shipping; the champion stays the champion.
  Rollback = the champion pointer: consumers (Travis handoffs, dossiers,
  company lists) read from the champion run's folder, which is never deleted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_NUM = r"([+-]?\d+\.?\d*)"


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _search(pattern: str, text: str, cast=float, group: int = 1):
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return cast(m.group(group))
    except (TypeError, ValueError):
        return None


def parse_run_dir(run_dir: str | Path) -> dict:
    """One KPI row from a run folder's standard reports. Missing files leave
    nulls — a partial run is still registrable, its gaps visible."""
    d = Path(run_dir)
    row: dict = {"run_dir": str(d.resolve()), "tag": d.name}

    # matrix shape without loading the data
    mtx = d / "provider_features_for_model.parquet"
    if mtx.exists():
        try:
            import pyarrow.parquet as pq
            pf = pq.ParquetFile(mtx)
            row["n_providers"] = int(pf.metadata.num_rows)
            row["n_columns"] = int(len(pf.schema_arrow.names))
        except Exception:
            pass
        row["matrix_path"] = str(mtx.resolve())
        row["matrix_mtime"] = datetime.fromtimestamp(
            mtx.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")

    man = d / "feature_manifest.json"
    if man.exists():
        try:
            mj = json.loads(_read(man))
            row["asof_cutoff"] = mj.get("asof_cutoff")
            row["label"] = mj.get("label")
            subs = mj.get("graph_substrate") or {}
            row["substrate_frozen"] = bool(subs.get("address_layer_frozen"))
        except Exception:
            pass
        row["manifest_path"] = str(man.resolve())

    exp = _read(d / "EXPECTATIONS_REPORT.md")
    if exp:
        row["expectation_fails"] = _search(r"(\d+)\s+FAIL", exp, int)
        row["expectation_warns"] = _search(r"(\d+)\s+WARN", exp, int)

    ab = _read(d / "NETWORK_AB_REPORT.md")
    if ab:
        v = re.search(r"\*\*VERDICT:\s*([A-Z /-]+)", ab)
        row["network_verdict"] = v.group(1).strip() if v else None
        row["network_matched_roc_delta"] = _search(
            r"delta ([+-]?\d+\.?\d*), CI", ab)

    fb = d / "future_bans_after_2023-12.csv"
    if fb.exists():
        try:
            import pandas as pd
            f = pd.read_csv(fb, dtype=str)
            row["forward_positives"] = int(
                (pd.to_numeric(f.get("is_prospective_positive"),
                               errors="coerce").fillna(0) == 1).sum())
        except Exception:
            pass
        row["future_bans_path"] = str(fb.resolve())

    oof = _read(d / "SOURCE_ABLATION_OOF_FULL.md") or _read(
        d / "SOURCE_ABLATION_OOF.md")
    if oof:
        row["ablation_positives"] = _search(r"positives=(\d[\d,]*)", oof,
                                            lambda s: int(s.replace(",", "")))
        for key, name in (("lift", "top-decile lift"), ("pr", "PR-AUC"),
                          ("roc", "ROC-AUC")):
            m = re.search(
                rf"\| {re.escape(name)} \| {_NUM} \| {_NUM} \| {_NUM} "
                rf"\[{_NUM}, {_NUM}\]", oof)
            if m:
                row[f"ablation_{key}_core"] = float(m.group(1))
                row[f"ablation_{key}_full"] = float(m.group(2))
                row[f"ablation_{key}_delta"] = float(m.group(3))
                row[f"ablation_{key}_lo"] = float(m.group(4))
                row[f"ablation_{key}_hi"] = float(m.group(5))
        row["catches_full_only"] = _search(
            r"full only[^|]*\|\s*(\d+)", oof, int)
        row["catches_core_only"] = _search(
            r"core only[^|]*\|\s*(\d+)", oof, int)

    try:
        row["git_commit"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True,
            text=True, timeout=10).stdout.strip() or None
    except Exception:
        row["git_commit"] = None
    row["eval_cohort"] = row.get("asof_cutoff")
    row["capsule"] = _capsule(d)
    row["registered_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    return row


def _capsule(run_dir: Path) -> dict:
    """Reproducibility capsule: enough to rebuild bit-for-bit or prove drift.

    Small inputs get full sha256; the multi-GB matrix gets size+mtime (hashing
    it on every registration would take minutes for no added assurance — the
    manifest hash pins its schema and provenance)."""
    import hashlib
    cap: dict = {"python": None, "libs": {}, "inputs": {}}
    try:
        import sys
        cap["python"] = sys.version.split()[0]
        from importlib.metadata import version
        for lib in ("lightgbm", "scikit-learn", "pandas", "numpy", "duckdb"):
            try:
                cap["libs"][lib] = version(lib)
            except Exception:
                pass
    except Exception:
        pass
    for name in ("feature_manifest.json", "future_bans_after_2023-12.csv"):
        p = run_dir / name
        if p.exists():
            try:
                cap["inputs"][name] = {
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest()[:16],
                    "bytes": p.stat().st_size}
            except Exception:
                pass
    m = run_dir / "provider_features_for_model.parquet"
    if m.exists():
        cap["inputs"][m.name] = {
            "bytes": m.stat().st_size,
            "mtime": datetime.fromtimestamp(
                m.stat().st_mtime, tz=timezone.utc).isoformat(
                timespec="seconds")}
    return cap


def _load_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(ln) for ln in
            p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _append_jsonl(p: Path, row: dict) -> None:
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def cohort_usage(rows: list[dict], warn_at: int = 5) -> list[str]:
    """Test-set hygiene: warn when one freeze cohort has supported too many
    registered runs — repeated peeks overfit the process to those positives."""
    counts: dict = {}
    for r in rows:
        c = r.get("eval_cohort") or "unknown"
        counts[c] = counts.get(c, 0) + 1
    return [f"cohort {c}: {n} registered runs — mint a fresh freeze "
            f"(prospective_label --cutoff <newer month>) before more decisions"
            for c, n in counts.items() if n > warn_at]


def recommend(rows: list[dict]) -> dict:
    """PROMOTE / HOLD for the newest run + the current champion pointer."""
    if not rows:
        return {"decision": "NONE", "champion": None, "reasons": []}
    latest = rows[-1]
    champion = None
    for r in rows[:-1]:
        if r.get("decision") == "PROMOTE":
            champion = r
    reasons = []
    fails = latest.get("expectation_fails")
    if fails is None:
        reasons.append("no expectations report found (treating as HOLD)")
    elif fails > 0:
        reasons.append(f"{fails} expectation FAIL(s)")
    verdict = latest.get("network_verdict") or ""
    if not verdict.startswith("KEEP"):
        reasons.append(f"network verdict is '{verdict or 'missing'}', not KEEP")
    lo = latest.get("ablation_lift_lo")
    if lo is None:
        reasons.append("no ablation lift CI found")
    elif lo <= 0:
        reasons.append(f"ablation lift CI includes zero (lo={lo:+.3f})")
    if champion is not None:
        prev = champion.get("ablation_lift_delta")
        cur = latest.get("ablation_lift_delta")
        if prev is not None and cur is not None and cur < prev - 0.15:
            reasons.append(f"lift delta dropped {prev - cur:.2f} vs champion "
                           f"'{champion.get('tag')}' (>0.15 threshold)")
    decision = "HOLD" if reasons else "PROMOTE"
    new_champion = latest if decision == "PROMOTE" else champion
    return {"decision": decision, "reasons": reasons,
            "champion": (new_champion or {}).get("tag"),
            "champion_dir": (new_champion or {}).get("run_dir")}


def _fmt(v, spec=""):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return format(v, spec) if spec else str(v)


def to_markdown(rows: list[dict], rec: dict,
                prereg: list[dict] | None = None,
                signoffs: list[dict] | None = None,
                history: list[dict] | None = None) -> str:
    prereg = prereg or []
    signoffs = signoffs or []
    so_by_tag = {}
    for s in signoffs:                        # latest sign-off per tag wins
        so_by_tag[s.get("tag")] = s
    pr_by_tag = {p.get("tag"): p for p in prereg}
    L = ["# MODEL RUNS — the release audit log", ""]
    L.append("**Policy.** Every major run is registered here with its KPIs and "
             "file locations. PROMOTE requires: zero expectation FAILs, a KEEP "
             "network verdict, an ablation lift CI clear of zero, and no more "
             "than a 0.15 lift-delta drop vs the reigning champion. Anything "
             "else is HOLD: investigate before shipping, and keep pointing "
             "consumers (Travis handoffs, company lists, dossiers) at the "
             "champion run's folder below. Run folders are immutable — "
             "rollback is a pointer change, never a rebuild.")
    L.append("")
    L.append(f"**Current champion:** `{rec.get('champion') or 'none yet'}`"
             + (f" — `{rec.get('champion_dir')}`" if rec.get("champion_dir")
                else ""))
    L.append(f"**Latest run decision:** **{rec.get('decision')}**"
             + ("" if not rec.get("reasons")
                else " — " + "; ".join(rec["reasons"])))
    L.append("")
    L.append("| run | registered | providers×cols | fwd positives | expect "
             "FAILs | network | ablation lift Δ [CI] | full/core-only catches "
             "| reviewer | commit |")
    L.append("|---|---|---|--:|--:|---|---|---|---|---|")
    for r in reversed(rows):
        shape = (f"{_fmt(r.get('n_providers'), ',')}×"
                 f"{_fmt(r.get('n_columns'))}")
        lift = (f"{_fmt(r.get('ablation_lift_delta'), '+.3f')} "
                f"[{_fmt(r.get('ablation_lift_lo'), '+.3f')}, "
                f"{_fmt(r.get('ablation_lift_hi'), '+.3f')}]")
        catches = (f"{_fmt(r.get('catches_full_only'))}/"
                   f"{_fmt(r.get('catches_core_only'))}")
        so = so_by_tag.get(r.get("tag"))
        rev = ("UNREPRODUCED" if so is None else
               f"{so.get('status', '?').upper()} ({so.get('by', '?')})")
        L.append(f"| {r.get('tag')} | {str(r.get('registered_at', ''))[:10]} "
                 f"| {shape} | {_fmt(r.get('forward_positives'), ',')} "
                 f"| {_fmt(r.get('expectation_fails'))} "
                 f"| {(r.get('network_verdict') or '—')[:14]} | {lift} "
                 f"| {catches} | {rev} | {r.get('git_commit') or '—'} |")

    # declared vs measured — the pre-registration receipt
    declared = [(t, p) for t, p in pr_by_tag.items()]
    if declared:
        L.append("")
        L.append("## Declared vs measured (pre-registered goals)")
        L.append("")
        L.append("_Goals and acceptance bars locked with a timestamp BEFORE "
                 "the run existed — impact is judged against these, never "
                 "narrated afterward._")
        L.append("")
        reg_tags = {r.get("tag"): r for r in rows}
        for tag, p in declared:
            L.append(f"- **{tag}** (declared {str(p.get('declared_at', ''))[:16]}): "
                     f"goal — {p.get('goal', '—')}; acceptance — "
                     f"{p.get('expected', '—')}")
            r = reg_tags.get(tag)
            if r:
                L.append(f"  measured — lift Δ "
                         f"{_fmt(r.get('ablation_lift_delta'), '+.3f')} "
                         f"[{_fmt(r.get('ablation_lift_lo'), '+.3f')}, "
                         f"{_fmt(r.get('ablation_lift_hi'), '+.3f')}], "
                         f"FAILs {_fmt(r.get('expectation_fails'))}, "
                         f"verdict {(r.get('network_verdict') or '—')[:20]}")
            else:
                L.append("  measured — run not registered yet")
    warns = cohort_usage(rows)
    if warns:
        L.append("")
        L.append("## Test-set hygiene warnings")
        L.append("")
        for w in warns:
            L.append(f"- ⚠ {w}")
    if len(rows) >= 2:
        a, b = rows[-2], rows[-1]
        L.append("")
        L.append("## Trend: latest vs previous")
        L.append("")
        for key, name in (("ablation_lift_delta", "ablation lift delta"),
                          ("forward_positives", "forward positives"),
                          ("n_columns", "matrix columns"),
                          ("expectation_fails", "expectation FAILs")):
            va, vb = a.get(key), b.get(key)
            if va is not None and vb is not None:
                L.append(f"- {name}: {va} → {vb} ({vb - va:+})"
                         if isinstance(va, int) else
                         f"- {name}: {va:.3f} → {vb:.3f} ({vb - va:+.3f})")
    if history:
        L.append("")
        L.append("## Release history before the registry (reconstructed)")
        L.append("")
        L.append("_Curated after the fact from the working chat, git log, "
                 "and docs — numbers verbatim from the reports quoted at "
                 "the time. Informational only: these rows never drive a "
                 "PROMOTE/HOLD decision._")
        L.append("")
        for e in history:
            d = e.get("date", "?") + ("~" if e.get("date_approx") else "")
            L.append(f"- **{d} — {e.get('title', e.get('tag'))}**: "
                     f"{e.get('results_text', '—')}")
    L.append("")
    L.append("## Where the files live")
    L.append("")
    for r in reversed(rows):
        L.append(f"- **{r.get('tag')}**: `{r.get('run_dir')}` — matrix "
                 f"`provider_features_for_model.parquet`, manifest, "
                 f"`future_bans_after_2023-12.csv`, and the report .md files "
                 f"all inside this folder.")
    L.append("")
    L.append("## Terms")
    L.append("")
    for g in GLOSSARY:
        L.append(f"- **{g['term']}** — {g['def']}")
    L.append("")
    L.append("_Registered by run_registry; append-only jsonl beside this "
             "report is the machine-readable source of truth._")
    return "\n".join(L)


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


# ---------------------------------------------------------------------------
# Glossary — every piece of house lingo gets a hover definition so a new
# reader (Brad, counsel, a future hire) can read the dashboard cold.
# Plain wording on purpose; aliases are matched in free text.
# ---------------------------------------------------------------------------
GLOSSARY: list[dict] = [
    {"term": "frozen",
     "aliases": ["frozen", "freeze date", "freeze"],
     "def": "We stopped the clock at the end of 2023. The model only sees "
            "data from before that date, and we grade it on who got banned "
            "after. It cannot peek at the future."},
    {"term": "withcases",
     "aliases": ["withcases"],
     "def": "A run that included the court-case file our research agents "
            "gathered (DOJ and court fraud cases used as extra labels)."},
    {"term": "clean run",
     "aliases": ["clean run", "clean-room"],
     "def": "A run with the agent-gathered case file locked away, to prove "
            "the results hold on official government data alone."},
    {"term": "core model",
     "aliases": ["core model", "core files", "five core files",
                 "core-only", "core only"],
     "def": "The core model is trained only on the five basic public "
            "files Travis started with. 'Core only' counts banned "
            "providers that rank high on the core model's list but not "
            "the full model's."},
    {"term": "full model",
     "aliases": ["full model", "full data set", "full-only", "full only"],
     "def": "The full model is trained on everything: the five core files "
            "plus every extra source we added (referrals, ownership, "
            "inspections, payments, and more). 'Full only' counts banned "
            "providers that rank high on the full model's list but not "
            "the core model's."},
    {"term": "lift",
     "aliases": ["top-decile lift", "lift delta", "lift gap", "lift"],
     "def": "Lift looks at the top 10% of the ranked list and asks how "
            "many times more future-banned providers land there than "
            "chance would put there. The lift delta (or lift gap) is the "
            "full model's lift minus the core model's: above zero means "
            "the extra data helped."},
    {"term": "graph features",
     "aliases": ["graph features", "network features", "network verdict",
                 "KEEP"],
     "def": "Graph (network) features are signals from connections "
            "between providers: shared addresses, shared owners, links "
            "to banned providers. KEEP is the test verdict saying they "
            "add real signal and stay in the model; CUT would mean they "
            "get dropped."},
    {"term": "future bans",
     "aliases": ["forward positives", "future bans", "future-banned",
                 "fwd positives"],
     "def": "Providers banned from Medicare or Medicaid AFTER our freeze "
            "date. The model never saw these outcomes. They are the "
            "answer key we grade against."},
    {"term": "integrity FAILs",
     "aliases": ["expectation FAILs", "integrity FAILs", "FAILs",
                 "expectation FAIL(s)", "data-integrity checks"],
     "def": "Automatic sanity checks on every data column. A FAIL means "
            "a column looked wrong (empty, constant, or out of range) "
            "and needs a human look before the run is trusted."},
    {"term": "out-of-fold",
     "aliases": ["out-of-fold", "OOF"],
     "def": "Every provider is scored by a model that never trained on "
            "that provider, so nobody grades their own homework."},
    {"term": "95% CI",
     "aliases": ["95% CI", "whisker", "CI"],
     "def": "The error-bar range around a number: where the true value "
            "very likely sits. If the whole range is above zero, the "
            "win is too big to be luck."},
    {"term": "ROC-AUC",
     "aliases": ["ROC-AUC", "ROC"],
     "def": "A whole-list score: how often the model ranks a random bad "
            "actor above a random normal provider. 0.5 is a coin flip; "
            "1.0 is perfect."},
    {"term": "PR-AUC",
     "aliases": ["PR-AUC"],
     "def": "A score focused on how pure the top of the list is when "
            "the thing you hunt is rare, as fraud is here."},
    {"term": "PROMOTE",
     "aliases": ["PROMOTE"],
     "def": "This run met every requirement and becomes the new "
            "champion that everyone reads from."},
    {"term": "HOLD",
     "aliases": ["HOLD"],
     "def": "Something needs a look before shipping. The previous "
            "champion stays in charge until it is resolved."},
    {"term": "champion",
     "aliases": ["champion"],
     "def": "The run everyone currently uses (Travis handoffs, company "
            "lists, dossiers all read from its folder). A new run "
            "replaces it only by meeting every promotion requirement."},
    {"term": "pre-registered",
     "aliases": ["pre-registered", "Declared before the run",
                 "pre-registration"],
     "def": "The goal and the pass bar were written down and "
            "time-stamped BEFORE the run existed, so the result cannot "
            "be spun after the fact."},
    {"term": "reconstructed",
     "aliases": ["reconstructed"],
     "def": "Rebuilt afterward from the working chat, the git log, and "
            "the docs, not measured live by this system. Shown for "
            "context; never used for decisions."},
    {"term": "UNREPRODUCED",
     "aliases": ["UNREPRODUCED"],
     "def": "No second person has independently re-run these numbers "
            "yet. The chip clears when a reviewer signs off."},
]

_ALIAS_DEF: dict[str, str] = {}
for _g in GLOSSARY:
    for _a in _g["aliases"]:
        _ALIAS_DEF[_a] = _g["def"]
_GLOSS_RE = re.compile(
    r"(?<![\w>])("
    + "|".join(re.escape(a) for a in
               sorted(_ALIAS_DEF, key=len, reverse=True))
    + r")(?![\w<])")


def _attr(s: str) -> str:
    return _esc(s).replace('"', "&quot;")


def _dfn(alias: str, display: str | None = None) -> str:
    """One glossed term for hand-placed template text."""
    d = _ALIAS_DEF.get(alias)
    if not d:
        return _esc(display or alias)
    return (f'<span class="dfn" title="{_attr(d)}">'
            f'{_esc(display or alias)}</span>')


def _gloss(escaped_text: str) -> str:
    """Wrap known terms in already-ESCAPED plain text (no tags inside)."""
    return _GLOSS_RE.sub(
        lambda m: f'<span class="dfn" title="{_attr(_ALIAS_DEF[m.group(1)])}"'
                  f'>{m.group(1)}</span>', escaped_text)


def _tag_span(tag: str) -> str:
    """Version tags explain themselves: anything named *frozen*,
    *withcases*, or *clean* carries its definition on hover."""
    t = str(tag)
    parts = []
    if "frozen" in t.lower():
        parts.append(_ALIAS_DEF["frozen"])
    if "withcases" in t.lower():
        parts.append(_ALIAS_DEF["withcases"])
    if "clean" in t.lower():
        parts.append(_ALIAS_DEF["clean run"])
    if not parts:
        return _esc(t)
    return f'<span class="dfn" title="{_attr(" ".join(parts))}">{_esc(t)}</span>'


def _plain_run_sentence(r: dict) -> str:
    """One plain-language sentence per registered run for the simple view."""
    bits = []
    d = r.get("ablation_lift_delta")
    lo = r.get("ablation_lift_lo")
    if d is not None:
        if lo is not None and lo > 0:
            bits.append(f"the full data set beat the five core files at "
                        f"putting future-banned providers near the top of "
                        f"the list (advantage {d:+.2f}, too big to be "
                        f"luck)")
        elif lo is not None:
            bits.append(f"the full-vs-core gap ({d:+.2f}) was inside the "
                        f"noise range, so this run alone can't call a "
                        f"winner")
        else:
            bits.append(f"full-vs-core advantage {d:+.2f} (no error bars "
                        f"recorded)")
    fo, co = r.get("catches_full_only"), r.get("catches_core_only")
    if fo is not None and co is not None:
        bits.append(f"{fo} banned providers were caught only by the full "
                    f"model vs {co} only by the core files")
    v = r.get("network_verdict") or ""
    if v.startswith("KEEP"):
        bits.append("the network features passed their keep-or-cut test")
    f = r.get("expectation_fails")
    if f:
        bits.append(f"{f} data-integrity checks failed and need a look")
    elif f == 0:
        bits.append("every data-integrity check passed")
    return ("In this run, " + "; ".join(bits) + "."
            if bits else "No headline numbers were recorded for this run.")


def _rec_html(rec: dict, warns: list[str]) -> str:
    """Recommendation block with short/full variants."""
    dec = rec.get("decision", "NONE")
    champ = rec.get("champion") or "none yet"
    short = (f"<p><b>{_dfn(dec, dec)}</b>. Keep everyone reading from "
             f"<code>{_tag_span(champ)}</code>."
             + (" Investigate the flagged issues before shipping anything "
                "new." if rec.get("reasons") else
                " The newest run met every requirement.") + "</p>")
    full = [f"<p><b>Decision: {_dfn(dec, dec)}.</b> "
            f"{_dfn('champion', 'Champion')}: "
            f"<code>{_tag_span(champ)}</code></p>"]
    if rec.get("reasons"):
        full.append(f"<p>Why not {_dfn('PROMOTE')}:</p><ul>")
        full += [f"<li>{_gloss(_esc(x))}</li>" for x in rec["reasons"]]
        full.append("</ul>")
    else:
        full.append(f"<p>All four promotion requirements met: zero "
                    f"{_dfn('integrity FAILs')}, a "
                    f"{_dfn('KEEP')} {_dfn('network verdict')}, a "
                    f"{_dfn('lift delta')} interval clear of zero, and no "
                    f"big drop vs the {_dfn('champion')}.</p>")
    if warns:
        full.append("<p>Standing warnings:</p><ul>")
        full += [f"<li>{_gloss(_esc(w))}</li>" for w in warns]
        full.append("</ul>")
    full.append(f"<p>Rolling back never means rebuilding: point consumers "
                f"(Travis handoffs, company lists, dossiers) back at the "
                f"previous {_dfn('champion')}'s folder, and check out its "
                f"git commit if code must match.</p>")
    return ('<div class="card"><h2>Recommendation '
            '<span class="mini"><button onclick="recmode(0)" id="rb0" '
            'class="on">short</button><button onclick="recmode(1)" '
            'id="rb1">full</button></span></h2>'
            f'<div class="rec-short">{short}</div>'
            f'<div class="rec-full" style="display:none">'
            f'{"".join(full)}</div></div>')


def _trend_svg(rows: list[dict], history: list[dict]) -> str:
    """Lift-delta whiskers: grey/dashed = reconstructed history, blue =
    registered runs. Auto-scaled to the data."""
    pts = []
    for e in history or []:
        res = e.get("results") or {}
        if res.get("lift_delta") is not None:
            pts.append({"tag": e.get("tag", "?"),
                        "d": float(res["lift_delta"]),
                        "lo": res.get("lift_lo"), "hi": res.get("lift_hi"),
                        "hist": True})
    for r in rows:
        if r.get("ablation_lift_delta") is not None:
            pts.append({"tag": r.get("tag", "?"),
                        "d": float(r["ablation_lift_delta"]),
                        "lo": r.get("ablation_lift_lo"),
                        "hi": r.get("ablation_lift_hi"), "hist": False})
    if not pts:
        return "<p>No lift-delta data yet.</p>"
    vals = [p["d"] for p in pts] + [p[k] for p in pts
                                    for k in ("lo", "hi")
                                    if p[k] is not None] + [0.0]
    vmin, vmax = min(vals), max(vals)
    pad = max((vmax - vmin) * 0.15, 0.05)
    vmin, vmax = vmin - pad, vmax + pad

    def y(v):
        return 190 - (v - vmin) / (vmax - vmin) * 160

    parts = []
    for i, p in enumerate(pts):
        x = 90 + i * 120
        col = "#8a8a86" if p["hist"] else "#2a78d6"
        dash = ' stroke-dasharray="4 3"' if p["hist"] else ""
        lo = p["lo"] if p["lo"] is not None else p["d"]
        hi = p["hi"] if p["hi"] is not None else p["d"]
        hover = (f"{p['tag']}: lift delta {p['d']:+.3f}"
                 + (f" [{lo:+.3f}, {hi:+.3f}]" if p["lo"] is not None
                    else "")
                 + (". Reconstructed from the working record."
                    if p["hist"] else ""))
        parts.append(
            f'<g><title>{_esc(hover)}</title>'
            f'<line x1="{x}" y1="{y(hi):.1f}" x2="{x}" y2="{y(lo):.1f}" '
            f'stroke="{col}" stroke-width="2"{dash}/>'
            f'<circle cx="{x}" cy="{y(p["d"]):.1f}" r="5" fill="{col}"/>'
            f'<text x="{x}" y="212" text-anchor="middle" font-size="10">'
            f'{_esc(p["tag"])[:16]}</text>'
            f'<text x="{x + 9}" y="{y(p["d"]) - 6:.1f}" font-size="10" '
            f'font-weight="bold">{p["d"]:+.3f}</text></g>')
    w = max(460, 90 + len(pts) * 120)
    note = ("Grey dashed points are reconstructed from the working record "
            "before this system existed. Blue points are registered runs. "
            "Each whisker is the 95% CI; a point above the zero line means "
            "the full model beat the core model, so the extra data helped. "
            "Hover any point for its numbers.")
    return (f'<svg viewBox="0 0 {w} 230" style="max-width:100%;'
            f'font-family:monospace">'
            f'<line x1="40" y1="{y(0):.1f}" x2="{w - 10}" y2="{y(0):.1f}" '
            f'stroke="#888" stroke-width="1.5"/>'
            f'<text x="34" y="{y(0) + 4:.1f}" text-anchor="end" '
            f'font-size="10">0</text>' + "".join(parts) + "</svg>"
            "<p class='note'>" + _gloss(_esc(note)) + "</p>")


def _timeline_html(rows: list[dict], history: list[dict],
                   pr_by_tag: dict, so_by_tag: dict) -> str:
    """Merged chronological cards: what changed, why, results, revert."""
    cards = []
    for e in history or []:
        d = e.get("date", "?") + ("~" if e.get("date_approx") else "")
        ch = "".join(f"<li>{_esc(c)}</li>" for c in e.get("changes", []))
        tech = (f"<p><b>Changes:</b></p><ul>{ch}</ul>" if ch else "") + (
            f"<p><b>Raw numbers:</b> <code>"
            f"{_esc(json.dumps(e.get('results') or {}))}</code></p>"
            f"<p class='note'>Evidence: {_esc(e.get('evidence', '—'))}</p>")
        cards.append((d, (
            f'<div class="card hist"><span class="badge" '
            f'title="{_attr(_ALIAS_DEF["reconstructed"])}">reconstructed'
            f'</span><h3>{_esc(d)} — {_esc(e.get("title", e.get("tag")))}'
            f'</h3><p><b>What we were changing:</b> '
            f'{_gloss(_esc(e.get("goal")))}'
            f'</p><p><b>Why:</b> {_gloss(_esc(e.get("why")))}</p>'
            f'<p><b>Result:</b> '
            f'{_gloss(_esc(e.get("results_text", "—")))}</p>'
            + (f'<p><b>Lesson:</b> {_gloss(_esc(e["lesson"]))}</p>'
               if e.get("lesson") else "")
            + f'<div class="tech">{tech}</div></div>')))
    for r in rows:
        tag = r.get("tag", "?")
        d = str(r.get("registered_at", "?"))[:10]
        so = so_by_tag.get(tag)
        chip = (f'<span class="badge warn" '
                f'title="{_attr(_ALIAS_DEF["UNREPRODUCED"])}">UNREPRODUCED'
                f'</span>' if so is None
                else f'<span class="badge ok">{_esc(so.get("status", "?")).upper()}'
                     f' ({_esc(so.get("by", "?"))})</span>')
        dec = r.get("decision", "—")
        pill = (f'<span class="badge {"ok" if dec == "PROMOTE" else "warn"}"'
                f'{" title=" + chr(34) + _attr(_ALIAS_DEF[dec]) + chr(34) if dec in _ALIAS_DEF else ""}>'
                f'{dec}</span>')
        p = pr_by_tag.get(tag)
        declared = ""
        if p:
            declared = (f'<p><b>{_dfn("pre-registered", "Declared before the run")}</b> '
                        f'({_esc(str(p.get("declared_at", ""))[:16])}): '
                        f'{_gloss(_esc(p.get("goal", "")))} — acceptance: '
                        f'{_gloss(_esc(p.get("expected", "")))}</p>')
        lift = (f"{_fmt(r.get('ablation_lift_delta'), '+.3f')} "
                f"[{_fmt(r.get('ablation_lift_lo'), '+.3f')}, "
                f"{_fmt(r.get('ablation_lift_hi'), '+.3f')}]")
        tech = (
            f"<table><tr><th>providers×cols</th>"
            f"<th>{_dfn('fwd positives')}</th>"
            f"<th>{_dfn('FAILs')}</th>"
            f"<th>{_dfn('network verdict', 'network')}</th>"
            f"<th>{_dfn('lift delta', 'lift Δ')} [{_dfn('CI')}]</th>"
            f"<th>{_dfn('full-only', 'full')}/{_dfn('core-only', 'core')}"
            f"-only</th><th>commit</th></tr>"
            f"<tr><td>{_fmt(r.get('n_providers'), ',')}×"
            f"{_fmt(r.get('n_columns'))}</td>"
            f"<td>{_fmt(r.get('forward_positives'), ',')}</td>"
            f"<td>{_fmt(r.get('expectation_fails'))}</td>"
            f"<td>{_esc(r.get('network_verdict') or '—')}</td>"
            f"<td>{lift}</td>"
            f"<td>{_fmt(r.get('catches_full_only'))}/"
            f"{_fmt(r.get('catches_core_only'))}</td>"
            f"<td><code>{_esc(r.get('git_commit') or '—')}</code></td>"
            f"</tr></table>"
            f"<p><b>Files:</b> <code>{_esc(r.get('run_dir', '—'))}</code>"
            f"</p>"
            + (f"<p><b>Revert to this exact code:</b> <code>git checkout "
               f"{_esc(r['git_commit'])}</code> (read-only look; "
               f"<code>git checkout -b rollback-{_esc(tag)} "
               f"{_esc(r['git_commit'])}</code> to work from it). Data "
               f"rollback is just pointing consumers at this folder.</p>"
               if r.get("git_commit") else ""))
        cards.append((d, (
            f'<div class="card"><h3>{_esc(d)} — {_tag_span(tag)} {pill} '
            f'{chip}</h3>{declared}'
            f'<div class="simple"><p>'
            f'{_gloss(_esc(_plain_run_sentence(r)))}</p>'
            f'</div><div class="tech">{tech}</div></div>')))
    cards.sort(key=lambda t: t[0])
    return "".join(c for _, c in cards)


def to_html(rows: list[dict], rec: dict, prereg: list[dict],
            signoffs: list[dict], history: list[dict] | None = None,
            economics_md: str | None = None,
            dispositions_md: str | None = None) -> str:
    """Self-contained data-driven dashboard — regenerated on every
    registration, so KPIs never travel by clipboard. Two view toggles:
    plain/technical wording, and short/full recommendations."""
    history = history or []
    so_by_tag = {}
    for s in signoffs:
        so_by_tag[s.get("tag")] = s
    pr_by_tag = {p.get("tag"): p for p in prereg}
    warns = cohort_usage(rows)
    md = to_markdown(rows, rec, prereg, signoffs, history)

    champ = rec.get("champion") or "none yet"
    dec = rec.get("decision", "NONE")
    banner = (f'<div class="card champ"><b>Current '
              f'{_dfn("champion")}:</b> '
              f'<code>{_tag_span(champ)}</code>'
              + (f' — <code>{_esc(rec.get("champion_dir"))}</code>'
                 if rec.get("champion_dir") else "")
              + f'<br><b>Latest run decision:</b> {_dfn(dec, dec)}'
              + (": " + _gloss(_esc("; ".join(rec.get("reasons", []))))
                 if rec.get("reasons") else "") + "</div>")

    terms = ('<div class="card"><h2>Terms used on this page</h2>'
             '<p class="note">Hover any dotted-underline word for its '
             'meaning, or read them all here.</p><dl>'
             + "".join(f"<dt><b>{_esc(g['term'])}</b></dt>"
                       f"<dd>{_esc(g['def'])}</dd>" for g in GLOSSARY)
             + "</dl></div>")

    extra = ""
    if economics_md:
        extra += ('<div class="card tech"><h2>Economics</h2><pre>'
                  + _esc(economics_md) + "</pre></div>")
    if dispositions_md:
        extra += ('<div class="card"><h2>Lead outcomes</h2><pre>'
                  + _esc(dispositions_md) + "</pre></div>")

    return (
        "<title>Model runs</title>"
        "<style>"
        "body{font:14px/1.55 system-ui;max-width:960px;margin:24px auto;"
        "padding:0 16px;color:#141410}"
        ".card{background:#f7f7f4;border:1px solid #e2e2dc;border-radius:"
        "10px;padding:14px 16px;margin:14px 0}"
        ".card.champ{border-left:5px solid #2a78d6}"
        ".card.hist{border-left:5px solid #c9a227;background:#faf8f0}"
        ".badge{font-size:11px;padding:2px 8px;border-radius:10px;"
        "background:#ddd;vertical-align:middle}"
        ".badge.ok{background:#cde8cd}.badge.warn{background:#f3d9b0}"
        "table{border-collapse:collapse;width:100%;font-size:12.5px;"
        "overflow-x:auto;display:block}"
        "th,td{border:1px solid #ddd;padding:4px 8px;text-align:left}"
        "pre{white-space:pre-wrap;background:#f4f4f1;border:1px solid "
        "#e2e2dc;border-radius:8px;padding:12px;font-size:12px;"
        "overflow-x:auto}"
        ".note{font-size:12px;color:#666}"
        ".dfn{border-bottom:1px dotted #888;cursor:help}"
        "dl dd{margin:0 0 8px 0;font-size:13px}dl dt{margin-top:6px}"
        "button{font:12px system-ui;padding:3px 10px;border-radius:8px;"
        "border:1px solid #bbb;background:#fff;cursor:pointer}"
        "button.on{background:#2a78d6;color:#fff;border-color:#2a78d6}"
        ".mini{float:right}"
        "body.plain .tech{display:none}body.techmode .simple{display:none}"
        "@media(prefers-color-scheme:dark){body{background:#1a1a19;"
        "color:#f2f1ea}.card{background:#232322;border-color:#38382f}"
        ".card.hist{background:#26241d}pre{background:#202020;"
        "border-color:#38382f}th,td{border-color:#3a3a35}"
        "button{background:#2c2c2b;color:#eee;border-color:#555}"
        ".badge{background:#444}.badge.ok{background:#2c5230}"
        ".badge.warn{background:#6b5423}.note{color:#aaa}}"
        "</style>"
        "<body class='plain'>"
        "<h1 style='font-size:21px;margin-bottom:4px'>Model runs</h1>"
        "<p class='note' style='margin-top:0'>Auto-generated from the "
        "registry files. "
        "<button onclick=\"view(0)\" id=\"vb0\" class=\"on\">plain</button> "
        "<button onclick=\"view(1)\" id=\"vb1\">technical</button></p>"
        + banner
        + _rec_html(rec, warns)
        + "<div class='card'><h2>Is the extra data winning?</h2>"
        + _trend_svg(rows, history) + "</div>"
        + "<h2>Timeline</h2>"
        + _timeline_html(rows, history, pr_by_tag, so_by_tag)
        + extra
        + terms
        + "<div class='card tech'><h2>Full technical report</h2><pre>"
        + _esc(md) + "</pre></div>"
        + "<script>"
        "function view(t){document.body.className=t?'techmode':'plain';"
        "document.getElementById('vb0').className=t?'':'on';"
        "document.getElementById('vb1').className=t?'on':'';}"
        "function recmode(t){"
        "for(const el of document.querySelectorAll('.rec-short'))"
        "el.style.display=t?'none':'';"
        "for(const el of document.querySelectorAll('.rec-full'))"
        "el.style.display=t?'':'none';"
        "document.getElementById('rb0').className=t?'':'on';"
        "document.getElementById('rb1').className=t?'on':'';}"
        "</script></body>")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default=None,
                    help="run folder to register (omit with --report-only)")
    ap.add_argument("--tag", default=None,
                    help="version tag (default: the folder name)")
    ap.add_argument("--data-root",
                    default=os.environ.get("MEDICAID_DATA_ROOT",
                                           str(Path.home() / "Desktop/data")))
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--html", action="store_true",
                    help="also regenerate MODEL_RUNS.html (data-driven "
                         "dashboard, no clipboard KPIs)")
    # pre-registration (lock the goal BEFORE the run)
    ap.add_argument("--preregister", action="store_true")
    ap.add_argument("--goal", default=None,
                    help="with --preregister: the hypothesis in one sentence")
    ap.add_argument("--expected", default=None,
                    help="with --preregister: the acceptance bar, e.g. "
                         "'lift delta CI clear of zero and >= champion-0.15'")
    # independent-review sign-off
    ap.add_argument("--signoff", action="store_true")
    ap.add_argument("--by", default=None, help="with --signoff: reviewer name")
    ap.add_argument("--status", default=None,
                    choices=[None, "reproduced", "objection"],
                    help="with --signoff: reproduced or objection")
    ap.add_argument("--note", default="", help="with --signoff: free text")
    args = ap.parse_args()

    reg_dir = Path(args.data_root) / "model_a"
    reg_dir.mkdir(parents=True, exist_ok=True)
    reg_path = reg_dir / "run_registry.jsonl"
    pre_path = reg_dir / "preregistrations.jsonl"
    so_path = reg_dir / "signoffs.jsonl"
    rows = _load_jsonl(reg_path)
    prereg = _load_jsonl(pre_path)
    signoffs = _load_jsonl(so_path)

    if args.preregister:
        if not (args.tag and args.goal and args.expected):
            raise SystemExit("--preregister needs --tag, --goal, --expected")
        if any(p.get("tag") == args.tag for p in prereg):
            raise SystemExit(f"tag '{args.tag}' already pre-registered — "
                             "pre-registrations are locked, pick a new tag")
        entry = {"tag": args.tag, "goal": args.goal, "expected": args.expected,
                 "declared_at": datetime.now(timezone.utc).isoformat(
                     timespec="seconds")}
        _append_jsonl(pre_path, entry)
        prereg.append(entry)
        print(f"[run_registry] pre-registered '{args.tag}' — goal locked at "
              f"{entry['declared_at']}")
    elif args.signoff:
        if not (args.tag and args.by and args.status):
            raise SystemExit("--signoff needs --tag, --by, --status")
        entry = {"tag": args.tag, "by": args.by, "status": args.status,
                 "note": args.note,
                 "signed_at": datetime.now(timezone.utc).isoformat(
                     timespec="seconds")}
        _append_jsonl(so_path, entry)
        signoffs.append(entry)
        print(f"[run_registry] sign-off recorded: {args.tag} — "
              f"{args.status} by {args.by}")
    elif not args.report_only:
        if not args.run_dir:
            raise SystemExit("--run-dir required (or pass --report-only / "
                             "--preregister / --signoff)")
        row = parse_run_dir(args.run_dir)
        if args.tag:
            row["tag"] = args.tag
        rec_now = recommend(rows + [row])
        row["decision"] = rec_now["decision"]
        rows.append(row)
        _append_jsonl(reg_path, row)

    # history: the file if the operator wrote one, else the baked-in curation
    hist_rows = _load_jsonl(reg_dir / "run_history.jsonl")
    if not hist_rows:
        try:
            from src.model_a.run_history import HISTORY as hist_rows
        except Exception:
            hist_rows = []

    rec = recommend(rows)
    report = reg_dir / "MODEL_RUNS.md"
    report.write_text(to_markdown(rows, rec, prereg, signoffs, hist_rows),
                      encoding="utf-8")
    if args.html:
        econ_md = disp_md = None
        try:
            from src.model_a import economics as _eco
            erows = _eco.load_log(reg_dir / "economics.jsonl")
            if erows:
                econ_md = _eco.to_markdown(_eco.summarize(erows))
        except Exception:
            pass
        try:
            from src.model_a import lead_dispositions as _ld
            drows = _ld.load_log(reg_dir / "lead_dispositions.jsonl")
            if drows:
                disp_md = _ld.to_markdown(_ld.summarize(drows))
        except Exception:
            pass
        (reg_dir / "MODEL_RUNS.html").write_text(
            to_html(rows, rec, prereg, signoffs, hist_rows,
                    econ_md, disp_md), encoding="utf-8")
    print(f"[run_registry] {len(rows)} run(s) | latest decision: "
          f"{rec['decision']} | champion: {rec.get('champion')} | "
          f"report -> {report}")


if __name__ == "__main__":
    main()
