"""
data_audit.py — "do the files on disk carry the FIELDS the adapters read, the
DATES the freeze needs, and are they the files the export would pick?"

The operator's 2026-10-21 ask: a deep scan of the data files that power the
platform, graded against the CODE's own contracts rather than a hand-written
table. For every source in the preflight registry (plus the folders the export
reads that the registry does not list) this module:

  1. finds every candidate file the way the export does (exact contract names,
     then the folder's extension globs; zip / xlsx / parquet / txt included);
  2. reads the header (vintage_inspect's tolerant probe: BOMs, banner rows,
     Latin-1, zip inner files) and resolves it against the ADAPTER'S column map
     (the ``*_COLS`` constant the adapter itself applies), so a renamed column
     is caught before a run silently blanks a feature;
  3. dates the file: the edition implied by the name (the export's
     ``_file_period``) and the MIN / MAX of the adapter's own date column(s);
  4. under ``--cutoff`` says which file(s) the export's vintage picker would read
     for a frozen run and which it would skip, and grades each snapshot source
     OK / POST-CUTOFF / UNDATED (event-grain sources are filtered by row date
     and graded on whether that date column resolved);
  5. inventories processed/ + graph*/ (schema, rows from parquet metadata,
     mtime) against the derived files the export expects;
  6. with ``--run-dir``, joins the last run's manifest ``sources_audit`` so the
     page shows "used / skipped last run" beside "what is on disk now".

Writes DATA_AUDIT.md + data_audit.json (default: <data_root>/preclean/). Read-
only on every input; ``--counts`` adds DuckDB row counts (a full scan of each
file; minutes on the VM, hours on the laptop for the spending fact).

  python -m src.data_audit --data-root /data/medicaid --cutoff 2021-12 \
      --run-dir /data/medicaid/model_a/frozen_2021-12_v17 --counts
"""
from __future__ import annotations

import argparse
import importlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from src import preflight
from src.vintage_inspect import _bom_encoding, _date_minmax, _read_header, _READ_ERRORS

# the export's own helpers: the audit must see the files the export sees
from src.model_a.provider_features_export import _asof_files, _file_period


@dataclass
class Source:
    key: str
    folder: str                      # under preclean/ ("" = root)
    names: list                      # exact contract names (first = canonical)
    exts: list                       # extension globs
    module: str | None               # adapter module holding the column map
    attr: str | None                 # the *_COLS constant
    kind: str                        # snapshot | event | reference | core
    date_keys: list = field(default_factory=list)   # logical keys of the date column(s)
    feeds: str = ""
    required: list = field(default_factory=list)    # logical keys the adapter raises on
    note: str = ""


# kind: snapshot = one edition per file, the EDITION must sit on/before the freeze;
#       event    = rows carry their own date, the export filters by it;
#       reference = a lookup (taxonomy, minutes map, crosswalk): date is informational;
#       core     = the stage-one inputs (spending fact / NPPES / PECOS / LEIE).
_KIND = {
    "spending": "event", "nppes": "core", "pecos": "core", "leie": "event",
    "partb": "snapshot", "partd": "snapshot", "opioid": "snapshot", "dmepos": "snapshot",
    "open_payments": "event", "nadac": "snapshot", "medicaid_ndc": "event",
    "medicaid_enrollment": "event", "fda_ndc": "reference", "saturation": "snapshot",
    "hrsa_340b": "event", "nppes_deactivation": "event", "order_referring": "snapshot",
    "pbj": "event", "hospice": "snapshot", "deficiencies": "event", "hcris": "snapshot",
    "pos": "snapshot", "nucc": "reference", "revocations": "event",
    "state_terminations": "event", "docgraph": "snapshot", "state_licensing": "event",
    "nucc_xwalk": "reference", "usps_cmra": "reference", "opensanctions": "reference",
    "preclusion": "event", "dmf": "event", "owners": "snapshot", "cms_priority": "reference",
    "hcpcs_time": "reference", "zip_county": "reference",
}
# registry key -> (module, COLS attr, date logical keys)
_CONTRACT = {
    "nppes": ("src.attempt_2.ingest.integrate", "NPPES_COLS", []),
    "pecos": ("src.attempt_2.ingest.integrate", "PECOS_COLS", []),
    "leie": ("src.attempt_2.ingest.integrate", "LEIE_COLS", ["excl_date"]),
    "partb": ("src.ingest_cms.partb", "PARTB_COLS", []),
    "partd": ("src.ingest_cms.partd", "PARTD_COLS", []),
    "opioid": ("src.ingest_cms.opioid", "OPIOID_COLS", []),
    "dmepos": ("src.ingest_cms.dmepos", "DMEPOS_COLS", []),
    "open_payments": ("src.ingest_cms.openpayments", "OP_COLS", ["payment_date"]),
    "nadac": ("src.ingest_cms.nadac", "NADAC_COLS", []),
    "medicaid_ndc": ("src.ingest_cms.medicaid_ndc", "NDC_COLS", ["service_month"]),
    "medicaid_enrollment": ("src.ingest_cms.medicaid_enrollment", "ENROLL_COLS", []),
    "fda_ndc": ("src.ingest_cms.fda_ndc", "PRODUCT_COLS", []),
    "saturation": ("src.ingest_cms.saturation", "SATURATION_COLS", []),
    "hrsa_340b": ("src.ingest_cms.hrsa_340b", "OPAIS_COLS", ["begin_date", "term_date"]),
    "nppes_deactivation": ("src.ingest_cms.nppes_deactivation", "DEACT_COLS", ["deactivation_date"]),
    "order_referring": ("src.ingest_cms.order_referring", "ELIGIBLE_COLS", []),
    "pbj": ("src.ingest_cms.facility", "PBJ_COLS", ["workdate"]),
    "hospice": ("src.ingest_cms.facility", "HOSPICE_COLS", ["end_date", "start_date"]),
    "deficiencies": ("src.ingest_cms.facility", "DEFICIENCY_COLS", ["survey_date"]),
    "hcris": ("src.ingest_cms.hcris", "HCRIS_COLS", []),
    "pos": ("src.ingest_cms.pos", "POS_COLS", []),
    "nucc": ("src.ingest_cms.nucc_taxonomy", "NUCC_COLS", []),
    "nucc_xwalk": ("src.ingest_cms.nucc_taxonomy", "CROSSWALK_COLS", []),
    "revocations": ("src.enforcement.medicare_revocations", "REVOCATION_COLS", ["revocation_date"]),
    "docgraph": ("src.ingest_cms.docgraph", "DOCGRAPH_COLS", []),
    "preclusion": ("src.enforcement.preclusion", "PRECLUSION_COLS", []),
    "dmf": ("src.enforcement.death_master", "DMF_COLS", []),
    "owners": ("src.entity_graph.owners_vintage", "OWNER_VINTAGE_COLS", []),
    "zip_county": ("src.ingest_cms.census_population", "ZIP_COLS", []),
}
# folders the export reads that the preflight registry does not list
_EXTRA = [
    Source("mue", "reference/mue", [], [".csv", ".xlsx"], None, None, "snapshot",
           feeds="per-patient MUE rule (ingest_cms.mue; quarterly practitioner + facility files)"),
    Source("nh_ownership", "facility", [], [], "src.ingest_cms.facility", "NH_OWNERSHIP_COLS",
           "snapshot", feeds="nh_ownership_stakes / ownership_integrity (NH_Ownership_* editions)"),
    Source("nh_penalties", "facility", [], [], "src.ingest_cms.facility", "NH_PENALTY_COLS",
           "event", ["penalty_date"], feeds="nh_penalties (NH_Penalties_* editions)"),
    Source("cia", "cia", ["cia_list.csv"], [".csv"], "src.enforcement.cia", "CIA_COLS", "event",
           feeds="label_expansion cia (CIA roster -> label + recall ruler)"),
    Source("state_capacity", "state_capacity", [], [".csv", ".xlsx"], "src.ingest_cms.licensed_capacity",
           "ROSTER_COLS", "reference", feeds="capacity check (licensed places per NPI)"),
    Source("owners_prior", "owners_prior", [], [".csv"], "src.entity_graph.owners_vintage",
           "OWNER_VINTAGE_COLS", "snapshot", feeds="ownership_turnover (dated All-Owners editions)"),
    Source("nppes_history", "nppes_history", [], [".csv", ".zip"], "src.attempt_2.ingest.integrate",
           "NPPES_COLS", "snapshot", feeds="--asof-nppes: the point-in-time address layer"),
    Source("warn", "warn", [], [".csv", ".xlsx"], "src.sourcing.warn_monitor", "WARN_COLS", "event",
           feeds="outreach timing only (never trained on)"),
]
_FACILITY_HINT = {        # the facility folder holds several layouts; file -> contract
    "pbj": re.compile(r"pbj", re.I),
    "deficiencies": re.compile(r"citat|deficien", re.I),
    "hospice": re.compile(r"hospice", re.I),
    "nh_ownership": re.compile(r"ownership", re.I),
    "nh_penalties": re.compile(r"penalt", re.I),
}
# derived files the export / graph / resolver expect under processed/ and graph*/
_DERIVED_EXPECTED = [
    ("processed/provider_dim.parquet", "every per-NPI feature; the resolver's NPPES index"),
    ("processed/spending_fact.parquet", "the fact (stage 1)"),
    ("processed/exclusions.parquet", "LEIE exclusion nodes (integrate.build_exclusions)"),
    ("processed/corruption_audit.json", "the $ basis every stage reads (audit_corruption)"),
    ("processed/ccn_to_npi.parquet", "CCN->NPI crosswalk: facility / HCRIS / POS schemes"),
    ("processed/case_defendant_npis.parquet", "DOJ defendants -> NPIs (defendant_resolver)"),
    ("processed/medicaid_enrollment_features.parquet", "mcd_* features + state fill"),
    ("processed/medicaid_enrollment_segments.parquet", "the enrollment segments (66M rows)"),
    ("processed/exclusions_medicaid_enrollment.parquet", "mcd_enroll label source"),
    ("processed/exclusions_state_terminations.parquet", "state_term label source"),
    ("processed/exclusions_medicare_revocations.parquet", "medicare_revocation label source"),
    ("processed/exclusions_name_resolved.parquet", "LEIE name-only rows resolved to NPIs"),
    ("processed/ndc_fact.parquet", "Medicaid NDC fact (rx_* / px_* features)"),
    ("processed/licensed_capacity_by_npi.parquet", "state rosters -> capacity"),
    ("graph/nodes/exclusion_nodes.parquet", "current-day graph exclusion nodes"),
    ("graph/node_embeddings.parquet", "graph_emb_* / fraud proximity"),
    ("enforcement/doj_cases_v3.csv", "the case DB (case_labels, rulers)"),
    ("enforcement/doj_cases_v3_plus_cia.csv", "merged case file (label_expansion merge-inputs)"),
]


def _sources() -> list[Source]:
    out = []
    for _sec, items in preflight.ALL_SECTIONS:
        for s in items:
            mod, attr, dk = _CONTRACT.get(s.key, (None, None, []))
            out.append(Source(s.key, s.folder, list(s.names), list(s.exts), mod, attr,
                              _KIND.get(s.key, "snapshot"), list(dk), s.feeds))
    out += _EXTRA
    return out


def _cols_map(src: Source) -> dict | None:
    if not src.module or not src.attr:
        return None
    try:
        return getattr(importlib.import_module(src.module), src.attr)
    except Exception as e:                       # the contract itself is broken: report it
        return {"__error__": [f"{type(e).__name__}: {e}"]}


def _norm(s: str) -> str:
    return re.sub(r"[\s_]+", " ", str(s)).strip().lower()


def resolve_contract(header: list[str] | None, cols: dict | None) -> dict:
    """{logical: header name found | None}: exact match first, then the
    adapters' usual case-insensitive / underscore-space tolerant match."""
    if not cols or header is None:
        return {}
    exact = {str(h): str(h) for h in header}
    loose = {_norm(h): str(h) for h in header}
    out = {}
    for logical, cands in cols.items():
        if logical == "__error__":
            continue
        hit = None
        for c in (cands if isinstance(cands, (list, tuple)) else [cands]):
            if c in exact:
                hit = exact[c]
                break
            if _norm(c) in loose:
                hit = loose[_norm(c)]
                break
        out[logical] = hit
    return out


def _zip_header(p: Path) -> tuple[list[str] | None, list[str]]:
    """Header of the first csv / txt / xlsx inside a zip + the inner names."""
    try:
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            for n in names:
                low = n.lower()
                if low.endswith((".csv", ".txt")):
                    with z.open(n) as fh:
                        raw = fh.read(1 << 20)
                    for enc in ("utf-8-sig", "latin-1"):
                        try:
                            df = pd.read_csv(io.StringIO(raw.decode(enc, errors="strict")),
                                             nrows=5, dtype=str, sep=None, engine="python")
                            return [str(c) for c in df.columns], names[:12]
                        except Exception:
                            continue
                if low.endswith(".xlsx"):
                    with z.open(n) as fh:
                        df = pd.read_excel(io.BytesIO(fh.read()), nrows=5)
                    return [str(c) for c in df.columns], names[:12]
            return None, names[:12]
    except Exception as e:
        _READ_ERRORS[str(p)] = f"zip: {type(e).__name__}: {str(e)[:120]}"
        return None, []


def _row_count(p: Path) -> int | None:
    try:
        if p.suffix.lower() == ".parquet":
            import pyarrow.parquet as pq
            return pq.ParquetFile(p).metadata.num_rows
        if p.suffix.lower() in (".csv", ".txt"):
            if _bom_encoding(p) == "utf-16":
                return int(pd.read_csv(p, encoding="utf-16", dtype=str, usecols=[0]).shape[0])
            import duckdb
            con = duckdb.connect()
            return int(con.execute(
                f"SELECT count(*) FROM read_csv_auto('{p.as_posix()}', all_varchar=true, "
                f"encoding='latin-1', ignore_errors=true, sample_size=2000)").fetchone()[0])
        if p.suffix.lower() in (".xlsx", ".xls"):
            return int(pd.read_excel(p, usecols=[0]).shape[0])
    except Exception as e:
        _READ_ERRORS[str(p) + "#count"] = f"{type(e).__name__}: {str(e)[:120]}"
    return None


def _candidate_files(pre: Path, src: Source) -> list[Path]:
    base = pre / src.folder if src.folder else pre
    if not base.is_dir():
        return []
    hits: list[Path] = []
    for n in src.names:
        for p in base.iterdir():
            if p.is_file() and p.name.lower() == n.lower():
                hits.append(p)
    if src.folder:                               # a folder source: every file of the extension
        for ext in src.exts or [".csv"]:
            hits += [p for p in base.glob(f"*{ext}") if p.is_file()]
        # one level of subfolders (the NH archive lands one folder down)
        for d in base.iterdir():
            if d.is_dir() and not d.name.startswith("_"):
                for ext in src.exts or [".csv"]:
                    hits += [p for p in d.glob(f"*{ext}") if p.is_file()]
    hits = sorted(set(hits))
    if src.folder == "facility":
        pat = _FACILITY_HINT.get(src.key)
        if pat:
            hits = [p for p in hits if pat.search(p.name)]
        else:
            hits = [p for p in hits if not any(r.search(p.name) for r in _FACILITY_HINT.values())]
    return [p for p in hits if not p.name.endswith(".bak")]


def _cut(cutoff: str | None) -> str | None:
    if not cutoff:
        return None
    c = str(cutoff)[:7]
    return c + "-12" if len(c) == 4 else c


def audit_file(p: Path, src: Source, cols: dict | None, cutoff: str | None, counts: bool) -> dict:
    st = p.stat()
    rec = {"source": src.key, "file": p.name, "path": str(p), "size_mb": round(st.st_size / 1e6, 1),
           "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
           "name_period": _file_period(p.name), "header": None, "zip_inner": [],
           "resolved": {}, "missing": [], "date_range": {}, "rows": None, "error": ""}
    if p.suffix.lower() == ".zip":
        header, inner = _zip_header(p)
        rec["zip_inner"] = inner
        if not rec["name_period"]:
            per = [_file_period(n) for n in inner]
            per = [x for x in per if x]
            rec["name_period"] = max(per) if per else None
    else:
        header = _read_header(p)
    rec["header"] = header
    if header is None:
        rec["error"] = _READ_ERRORS.get(str(p), "unreadable")
    res = resolve_contract(header, cols)
    rec["resolved"] = res
    rec["missing"] = [k for k, v in res.items() if v is None]
    if cols and "__error__" in cols:
        rec["error"] = cols["__error__"][0]
    if header and p.suffix.lower() != ".zip":
        date_cols = [res[k] for k in src.date_keys if res.get(k)]
        probe = list(dict.fromkeys(date_cols + list(header)))      # adapter's date cols first
        rec["date_range"] = _date_minmax(p, probe)
    if counts:
        rec["rows"] = _row_count(p)
    return rec


def grade_source(src: Source, files: list[dict], cutoff: str | None) -> dict:
    """The source verdict: files present, the contract, the freeze."""
    cut = _cut(cutoff)
    g = {"key": src.key, "kind": src.kind, "folder": src.folder or "(preclean root)", "feeds": src.feeds,
         "n_files": len(files), "contract": "n/a", "freeze": "n/a", "picked": [], "skipped": [],
         "issues": []}
    if not files:
        g["contract"] = "MISSING"
        g["freeze"] = "MISSING"
        g["issues"].append("no file on disk")
        return g
    unreadable = [f["file"] for f in files if f["header"] is None and not f["zip_inner"]]
    if unreadable:
        g["issues"].append("unreadable: " + ", ".join(unreadable))
    if src.module:
        bad = [(f["file"], f["missing"]) for f in files if f["missing"] and f["header"] is not None]
        g["contract"] = "OK" if not bad else "FIELDS MISSING"
        for fn, miss in bad:
            g["issues"].append(f"{fn}: no header matches {', '.join(miss)}")
    if cut and src.kind in ("snapshot",):
        paths = [Path(f["path"]) for f in files]
        elig = {Path(x).name for x in _asof_files(paths, cut)}
        g["picked"] = sorted(elig)
        g["skipped"] = sorted(f["file"] for f in files if f["file"] not in elig)
        dated = [f for f in files if f["name_period"]]
        if not elig:
            g["freeze"] = "NOTHING ELIGIBLE"
            g["issues"].append(f"every edition is after {cut} or undated-and-ignored")
        elif all(f["name_period"] is None for f in files if f["file"] in elig):
            g["freeze"] = "UNDATED (read as-is)"
            g["issues"].append("the picked file carries no date in its name: vintage unverified")
        else:
            g["freeze"] = "OK"
        for f in dated:
            if f["file"] in elig:
                hi = max((v[1] for v in f["date_range"].values()), default=None)
                if hi and hi[:7] > cut:
                    g["issues"].append(f"{f['file']}: named {f['name_period']} but rows run to {hi} "
                                       f"(post-cutoff rows inside a 'pre-cutoff' edition)")
    elif cut and src.kind == "event":
        ok = [f for f in files if any(f["resolved"].get(k) for k in src.date_keys)] if src.date_keys else files
        g["freeze"] = "OK (row-filtered)" if ok or not src.date_keys else "NO DATE COLUMN"
        if src.date_keys and not ok:
            g["issues"].append(f"the as-of filter needs {', '.join(src.date_keys)}; no header resolves it")
    elif cut:
        g["freeze"] = "reference"
    return g


def inventory_processed(data_root: Path) -> list[dict]:
    rows = []
    for rel, why in _DERIVED_EXPECTED:
        p = data_root / rel
        rec = {"file": rel, "why": why, "exists": p.exists(), "rows": None, "mtime": "", "columns": None}
        if p.exists():
            st = p.stat()
            rec["mtime"] = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
            rec["size_mb"] = round(st.st_size / 1e6, 1)
            if p.suffix == ".parquet":
                try:
                    import pyarrow.parquet as pq
                    pf = pq.ParquetFile(p)
                    rec["rows"] = pf.metadata.num_rows
                    rec["columns"] = list(pf.schema_arrow.names)[:40]
                except Exception as e:
                    rec["error"] = f"{type(e).__name__}: {str(e)[:100]}"
            elif p.suffix == ".csv":
                rec["columns"] = _read_header(p)
        rows.append(rec)
    # every other parquet under processed/ and graph*/: name + rows + mtime
    extra = []
    for d in ["processed"] + [x.name for x in data_root.glob("graph*") if x.is_dir()]:
        base = data_root / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.parquet")):
            rel = str(p.relative_to(data_root)).replace("\\", "/")
            if any(rel == r for r, _ in _DERIVED_EXPECTED):
                continue
            try:
                import pyarrow.parquet as pq
                n = pq.ParquetFile(p).metadata.num_rows
            except Exception:
                n = None
            extra.append({"file": rel, "rows": n,
                          "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d"),
                          "size_mb": round(p.stat().st_size / 1e6, 1)})
    return rows + extra


def last_run_sources(run_dir: Path | None) -> dict:
    if not run_dir:
        return {}
    mp = run_dir / "feature_manifest.json"
    if not mp.exists():
        return {"__missing__": str(mp)}
    try:
        m = json.loads(mp.read_text(encoding="utf-8"))
    except Exception as e:
        return {"__error__": f"{type(e).__name__}: {e}"}
    sa = m.get("sources_audit") or {}
    out = {}
    items = sa.items() if isinstance(sa, dict) else [(r.get("source"), r) for r in sa]
    for k, v in items:
        if isinstance(v, dict):
            out[str(k)] = {"status": v.get("status", v.get("used", "")), "detail": str(v.get("detail", v.get("reason", "")))[:160]}
        else:
            out[str(k)] = {"status": str(v)[:40], "detail": ""}
    return out


def run(data_root: Path, cutoff: str | None, run_dir: Path | None, counts: bool,
        only: set | None = None) -> dict:
    pre = data_root / "preclean"
    report = {"data_root": str(data_root), "cutoff": cutoff, "generated": datetime.now().isoformat(timespec="minutes"),
              "sources": [], "files": [], "processed": [], "last_run": last_run_sources(run_dir)}
    for src in _sources():
        if only and src.key not in only:
            continue
        cols = _cols_map(src)
        files = [audit_file(p, src, cols, cutoff, counts) for p in _candidate_files(pre, src)]
        g = grade_source(src, files, cutoff)
        g["contract_fields"] = [k for k in (cols or {}) if k != "__error__"]
        if cols and "__error__" in cols:
            g["issues"].append("contract import failed: " + cols["__error__"][0])
        report["sources"].append(g)
        report["files"] += files
    report["processed"] = inventory_processed(data_root)
    return report


def render(report: dict) -> str:
    cut = report["cutoff"]
    L = [f"# DATA AUDIT — {report['data_root']}", "",
         f"_Generated {report['generated']}; freeze {cut or '(none: current-day)'}. Every header is "
         "graded against the adapter's OWN column map; every edition against the export's vintage "
         "picker. Read-only._", ""]
    bad = [s for s in report["sources"] if s["issues"]]
    L += ["## 1. Verdicts (sources with an issue first)", "",
          "| source | kind | files | contract | freeze | issues |", "|---|---|---|---|---|---|"]
    for s in sorted(report["sources"], key=lambda s: (not s["issues"], s["key"])):
        L.append(f"| {s['key']} | {s['kind']} | {s['n_files']} | {s['contract']} | {s['freeze']} | "
                 + ("; ".join(s["issues"]) or "") + " |")
    L += ["", f"{len(bad)} of {len(report['sources'])} sources carry an issue.", ""]
    if cut:
        L += ["## 2. What a frozen run would read", "",
              "| source | picked (on/before the freeze) | skipped |", "|---|---|---|"]
        for s in report["sources"]:
            if s["kind"] == "snapshot" and s["n_files"]:
                L.append(f"| {s['key']} | {', '.join(s['picked']) or '(nothing)'} | {', '.join(s['skipped']) or ''} |")
        L.append("")
    L += ["## 3. Every file: header vs contract, dates, rows", ""]
    by_src: dict[str, list] = {}
    for f in report["files"]:
        by_src.setdefault(f["source"], []).append(f)
    for s in report["sources"]:
        files = by_src.get(s["key"], [])
        L.append(f"### {s['key']}  ({s['folder']}; {s['kind']})  — feeds: {s['feeds']}")
        if not files:
            L += ["", "_no file on disk_", ""]
            continue
        L += ["", "| file | size MB | modified | name period | rows | dates in data | contract |", "|---|---|---|---|---|---|---|"]
        for f in files:
            dr = "; ".join(f"{k}: {v[0]}..{v[1]}" for k, v in list(f["date_range"].items())[:3])
            if f["header"] is None and not f["zip_inner"]:
                con = f"UNREADABLE ({f['error']})"
            elif not f["resolved"]:
                con = "(no contract map)" if not s["contract_fields"] else "(header unread: zip without a readable inner file)"
            else:
                found = [f"{k}={v}" for k, v in f["resolved"].items() if v]
                con = ("MISSING " + ", ".join(f["missing"]) + "; " if f["missing"] else "all fields; ") + "found " + ", ".join(found)
            L.append(f"| {f['file']} | {f['size_mb']} | {f['mtime']} | {f['name_period'] or ''} | "
                     f"{f['rows'] if f['rows'] is not None else ''} | {dr} | {con} |")
        L.append("")
    L += ["## 4. Derived files (processed/, graph*/)", "",
          "| file | exists | rows | modified | size MB | why |", "|---|---|---|---|---|---|"]
    for r in report["processed"]:
        L.append(f"| {r['file']} | {'yes' if r.get('exists', True) else 'NO'} | {r.get('rows') if r.get('rows') is not None else ''} | "
                 f"{r.get('mtime','')} | {r.get('size_mb','')} | {r.get('why','')} |")
    if report["last_run"]:
        L += ["", "## 5. Last run's sources_audit", "", "| source | status | detail |", "|---|---|---|"]
        for k, v in sorted(report["last_run"].items()):
            if isinstance(v, dict):
                L.append(f"| {k} | {v.get('status','')} | {v.get('detail','')} |")
            else:
                L.append(f"| {k} | {v} | |")
    L.append("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--cutoff", default=None, help="the freeze, YYYY-MM (omit for current-day)")
    ap.add_argument("--run-dir", default=None, help="a run folder whose feature_manifest.json sources_audit is shown")
    ap.add_argument("--counts", action="store_true", help="DuckDB row counts (full scans)")
    ap.add_argument("--only", default=None, help="comma list of source keys")
    ap.add_argument("--out", default=None, help="output folder (default <data_root>/preclean)")
    a = ap.parse_args()
    root = Path(a.data_root)
    rep = run(root, a.cutoff, Path(a.run_dir) if a.run_dir else None, a.counts,
              set(a.only.split(",")) if a.only else None)
    out = Path(a.out) if a.out else root / "preclean"
    out.mkdir(parents=True, exist_ok=True)
    (out / "DATA_AUDIT.md").write_text(render(rep), encoding="utf-8")
    (out / "data_audit.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    bad = [s for s in rep["sources"] if s["issues"]]
    print(f"[data_audit] {len(rep['sources'])} sources, {len(rep['files'])} files, "
          f"{len(bad)} sources with issues -> {out / 'DATA_AUDIT.md'}")
    for s in bad:
        print(f"  - {s['key']}: " + "; ".join(s["issues"])[:200])


if __name__ == "__main__":
    main()
