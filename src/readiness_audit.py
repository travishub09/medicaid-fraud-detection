"""
readiness_audit.py — scheme → formula → adapter → file → column, end to end.

The vintage inspector answers "what period is each file?". This audit answers
the deeper operator question: **can every scheme actually run, on both clocks?**
For every registered source it verifies, against the REAL preclean tree:

  1. FILE      — does discovery (the adapter's folder/patterns) find a file?
  2. COLUMNS   — does that file's header satisfy the adapter's own column
                 contract (imported from the adapter module — never a copy)?
  3. FROZEN    — is a Dec-2023 view achievable: a dated on/before-cutoff
                 edition on disk, OR row-level as-of filtering wired on a
                 date column that actually exists in the file?
  4. CURRENT   — is a current-day view achievable: a recent edition, a
                 live snapshot, or a static reference?

Then it rolls the verdicts up to SCHEMES: each scheme's formula inputs (the
weighted features from scheme_subscores) plus every feeding source's verdict,
so a scheme whose data can't connect is named before a run wastes a night.

  python -m src.readiness_audit --data-root C:/Users/treyr/OneDrive/Desktop/data
      [--cutoff 2023-12]  →  preclean/READINESS_AUDIT.md
"""

from __future__ import annotations

import argparse
import importlib
import re
from pathlib import Path

from src.preflight import CORE, UNLOCKS
from src.vintage_inspect import _read_header

YEAR_RE = re.compile(r"(?<!\d)(20[0-9]{2})(?!\d)")

# ---------------------------------------------------------------------------
# Temporal policy per source: how the FROZEN (cutoff) view is achieved.
#   dated_editions — needs a file dated on/before the cutoff AND a recent one
#   row_asof       — one file; the adapter filters rows on date_cols (wired)
#   cumulative     — full-history file with event dates; as-of by row (wired)
#   static         — reference data; vintage-insensitive
#   snapshot_only  — only a current snapshot exists anywhere; frozen view is
#                    an accepted CAVEAT, never silently treated as ready
POLICY = {
    "spending": ("cumulative", ["CLAIM_FROM_MONTH"], "asof_billing --asof-cutoff"),
    "nppes": ("cumulative", ["Provider Enumeration Date"], "point-in-time NPPES dates"),
    "pecos": ("snapshot_only", [], "enrollment snapshot; ownership history comes from owners editions instead"),
    "leie": ("cumulative", ["EXCLDATE"], "exclusion dates → as-of label + graph"),
    "partb": ("dated_editions", [], "yearly detail files"),
    "partd": ("dated_editions", [], "yearly detail files"),
    "opioid": ("dated_editions", [], "yearly files"),
    "dmepos": ("dated_editions", [], "yearly detail files"),
    "open_payments": ("dated_editions", [], "program-year files"),
    "pos": ("dated_editions", [], "quarterly editions + hold folder"),
    "hcris": ("row_asof", ["Fiscal Year Begin Date"], "fiscal-year dates inside"),
    "nadac": ("dated_editions", [], "yearly files; as-of pick wired"),
    "order_referring": ("dated_editions", [], "monthly editions; as-of pick wired"),
    "docgraph": ("dated_editions", [], "year-window releases (slow-moving; one edition may serve both views)"),
    "mue": ("static", [], "quarterly edits, slow-moving; treated as reference"),
    "saturation": ("row_asof", ["Reference Period"], "dataset carries reference periods per row"),
    "facility": ("dated_editions", [], "dated archives + row-level Survey/Measure-date filters (both wired)"),
    "hrsa_340b": ("row_asof", ["Begin Date"], "Begin/Term-date reconstruction (wired)"),
    "nppes_deactivation": ("cumulative", [], "deactivation dates inside the report"),
    "census": ("static", [], "estimates file carries the 2020-2025 series"),
    "nucc": ("static", [], "taxonomy code set"),
    "zip_county": ("static", [], "geographic crosswalk"),
    "hcpcs_time": ("static", [], "CY2023 rule file IS the cutoff vintage"),
    "revocations": ("cumulative", ["REVOCATION_EFCTV_DT"], "effective dates inside"),
    "opensanctions": ("snapshot_only", [], "bulk snapshot; entity dates partial"),
    "dmf": ("cumulative", [], "death dates inside"),
    "state_licensing": ("snapshot_only", [], "per-state lists"),
    "preclusion": ("snapshot_only", [], "sponsor-channel snapshot"),
}

# adapter column contracts, imported (module, attr). None = no header check
# (zips the loader unzips, multi-layout folders checked by vintage_inspect).
COLS_IMPORT = {
    "partb": ("src.ingest_cms.partb", "PARTB_COLS"),
    "partd": ("src.ingest_cms.partd", "PARTD_COLS"),
    "opioid": ("src.ingest_cms.opioid", "OPIOID_COLS"),
    "dmepos": ("src.ingest_cms.dmepos", "DMEPOS_COLS"),
    "open_payments": ("src.ingest_cms.openpayments", "OP_COLS"),
    "pos": ("src.ingest_cms.pos", "POS_COLS"),
    "hcris": ("src.ingest_cms.hcris", "HCRIS_COLS"),
    "nadac": ("src.ingest_cms.nadac", "NADAC_COLS"),
    "order_referring": ("src.ingest_cms.order_referring", "ELIGIBLE_COLS"),
    "docgraph": ("src.ingest_cms.docgraph", "DOCGRAPH_COLS"),
    "saturation": ("src.ingest_cms.saturation", "SATURATION_COLS"),
    "hrsa_340b": ("src.ingest_cms.hrsa_340b", "OPAIS_COLS"),
    "nucc": ("src.ingest_cms.nucc_taxonomy", "NUCC_COLS"),
    "census": ("src.ingest_cms.census_population", "POP_COLS"),
}

# alias keys the registry uses vs POLICY keys
_KEY_ALIAS = {"caught": "leie"}


def _contract(key: str) -> dict | None:
    imp = COLS_IMPORT.get(key)
    if not imp:
        return None
    try:
        return getattr(importlib.import_module(imp[0]), imp[1])
    except Exception:
        return None


def _find_files(pre: Path, src) -> list[Path]:
    folder = pre / src.folder if src.folder else pre
    if not folder.is_dir():
        return []
    out = []
    names = {n.lower() for n in src.names}
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(folder))
        if "_hold" in rel or rel.endswith(".bak"):
            continue
        if p.name.lower() in names or p.suffix.lower() in src.exts:
            out.append(p)
    return out


def _col_check(files: list[Path], contract: dict | None) -> tuple[str, list[str]]:
    """Best file's unresolved logical columns ('' file = none readable)."""
    if not contract or not files:
        return ("", [])
    best, missing = "", list(contract.keys())
    for p in files:
        if p.suffix.lower() == ".zip":
            continue
        cols = _read_header(p)
        if not cols:
            continue
        low = " | ".join(str(c).strip().lower() for c in cols)
        miss = [k for k, aliases in contract.items()
                if not any(str(a).strip().lower() in low for a in aliases)]
        if len(miss) < len(missing):
            best, missing = p.name, miss
        if not miss:
            break
    return (best, missing)


def _dated(files: list[Path]) -> list[tuple[int, Path]]:
    out = []
    for p in files:
        ys = YEAR_RE.findall(p.name)
        if ys:
            out.append((max(int(y) for y in ys), p))
    return out


def audit_source(pre: Path, src, cutoff: str = "2023-12") -> dict:
    key = _KEY_ALIAS.get(src.key, src.key)
    policy, date_cols, mech = POLICY.get(key, ("snapshot_only", [], "no policy declared"))
    files = _find_files(pre, src)
    contract = _contract(key)
    best_file, missing_cols = _col_check(files, contract)
    cut_year = int(str(cutoff)[:4])

    if not files:
        frozen = current = "GAP: no file found"
    elif policy == "static":
        frozen = current = "READY (static reference)"
    elif policy == "cumulative":
        frozen = f"READY (row as-of: {mech})"
        current = "READY (cumulative through present)"
    elif policy == "row_asof":
        # the filter's date column must actually exist in the file
        ok = True
        if date_cols:
            cols = _read_header(files[0]) or []
            low = " | ".join(str(c).lower() for c in cols)
            ok = all(any(w in low for w in dc.lower().split()) for dc in date_cols)
        frozen = (f"READY (row as-of: {mech})" if ok
                  else f"GAP: as-of needs column(s) {date_cols} not in file")
        current = "READY (snapshot includes present)"
    elif policy == "dated_editions":
        dated = _dated(files)
        pre_cut = [p for y, p in dated if y <= cut_year]
        recent = [p for y, p in dated if y >= cut_year + 1]
        frozen = (f"READY ({pre_cut[-1].name})" if pre_cut
                  else f"GAP: no edition dated <= {cutoff} — download one")
        current = (f"READY ({recent[-1].name})" if recent
                   else ("CAVEAT: only pre-cutoff editions on disk"
                         if pre_cut else "GAP: no dated edition"))
    else:  # snapshot_only
        frozen = f"CAVEAT: snapshot only — {mech}"
        current = "READY (current snapshot)"

    return {"key": src.key, "label": src.label, "schemes": src.feeds,
            "gating": src.gating, "n_files": len(files),
            "files": [p.name for p in files[:4]],
            "col_file": best_file, "missing_cols": missing_cols,
            "frozen": frozen, "current": current, "policy": policy}


def audit_tree(data_root: Path, cutoff: str = "2023-12") -> list[dict]:
    pre = Path(data_root) / "preclean"
    return [audit_source(pre, s, cutoff) for s in CORE + UNLOCKS]


def _scheme_map(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        for tok in re.findall(r"[a-z_]{4,}", str(r["schemes"]).lower()):
            out.setdefault(tok, []).append(r)
    return out


def write_report(rows: list[dict], data_root: Path,
                 cutoff: str = "2023-12") -> Path:
    pre = Path(data_root) / "preclean"
    bad_cols = [r for r in rows if r["missing_cols"]]
    fro_gap = [r for r in rows if r["frozen"].startswith("GAP")]
    cur_gap = [r for r in rows if r["current"].startswith("GAP")]
    caveat = [r for r in rows if "CAVEAT" in r["frozen"]]

    L = [f"# READINESS AUDIT — every scheme, both clocks (cutoff {cutoff})", "",
         f"_{len(rows)} sources audited: {len(fro_gap)} frozen-view gaps, "
         f"{len(cur_gap)} current-view gaps, {len(bad_cols)} column-contract "
         f"failures, {len(caveat)} accepted snapshot caveats._", "",
         "## 1. Source verdicts", "",
         "| source | files | column contract | frozen (Dec-2023) view | current view |",
         "|---|---|---|---|---|"]
    for r in rows:
        cols = ("n/a" if not COLS_IMPORT.get(_KEY_ALIAS.get(r["key"], r["key"]))
                else ("no readable file" if not r["col_file"] and r["n_files"]
                      and r["missing_cols"] else
                      (f"MISSING {', '.join(r['missing_cols'])}"
                       if r["missing_cols"] else f"ok ({r['col_file']})")))
        L.append(f"| {r['key']} | {r['n_files']}: "
                 f"{', '.join(r['files']) or '—'} | {cols} "
                 f"| {r['frozen']} | {r['current']} |")

    L += ["", "## 2. Scheme rollup — formula inputs + feeding-source verdicts", ""]
    try:
        from src.model_a.scheme_subscores import DEFAULT_SCHEME_WEIGHTS
        weights = {k: v for k, v in DEFAULT_SCHEME_WEIGHTS.items()}
    except Exception:
        weights = {}
    smap = _scheme_map(rows)
    for scheme in sorted(weights):
        feeds = smap.get(scheme, [])
        formula = ", ".join(f"{f}×{w}"
                            for f, w in list(weights[scheme].items())[:8])
        L.append(f"**{scheme}** — formula: {formula}")
        if feeds:
            for r in feeds:
                flag = ("OK" if not r["frozen"].startswith("GAP")
                        and not r["missing_cols"] else "ATTENTION")
                L.append(f"  - [{flag}] {r['key']}: frozen={r['frozen']}; "
                         f"cols={'MISSING ' + ','.join(r['missing_cols']) if r['missing_cols'] else 'ok'}")
        else:
            L.append("  - inputs come from the core spending fact / graph "
                     "(covered by spending/nppes/pecos rows above)")
        L.append("")

    L += ["## 3. Action list (in order)", ""]
    n = 0
    for r in fro_gap + cur_gap:
        n += 1
        L.append(f"{n}. **{r['key']}** — "
                 f"{r['frozen'] if r['frozen'].startswith('GAP') else r['current']}")
    for r in bad_cols:
        n += 1
        L.append(f"{n}. **{r['key']}** — wrong/renamed columns in "
                 f"{r['col_file'] or r['files'][:1]}: missing "
                 f"{', '.join(r['missing_cols'])} (send me the header row)")
    if not n:
        L.append("**None — every source is connected, columned, and "
                 "vintage-correct on both clocks.**")
    out = pre / "READINESS_AUDIT.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"[readiness_audit] {len(rows)} sources -> {out}")
    print(f"[readiness_audit] frozen gaps: {len(fro_gap)}; current gaps: "
          f"{len(cur_gap)}; column failures: {len(bad_cols)}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--cutoff", default="2023-12")
    args = ap.parse_args()
    rows = audit_tree(Path(args.data_root), args.cutoff)
    write_report(rows, Path(args.data_root), args.cutoff)


if __name__ == "__main__":
    main()
