"""
data_refresh.py — "what data do I need to download, and which files can I not date?"
answered from the run's own artifacts (2026-10-14).

The operator's question that needed a human to read five reports. This writes
DATA_REFRESH.md in the run folder from:

  * the manifest's sources_audit (every source, the file it read, used / skipped)
  * the file names those lines carry (the same date tokens the vintage picker reads)
  * the freeze (manifest asof_cutoff)
  * docs/reminders.json (the download cadence: what is due, the reminder key)
  * preclean/VINTAGE_INSPECT.md when the inspector has run (its re-download list)

Per source: the file(s) read, the inferred edition, the verdict against the freeze
(OK / POST-CUTOFF / UNDATED / DERIVED / SKIPPED), the action, and the reminder key
whose steps carry the URL and the save path. Read-only.

  python -m src.model_a.data_refresh --run-dir <run> [--data-root D] [--reminders docs/reminders.json]
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# sources computed from the spending fact / NPPES / the graph: no file to refresh
DERIVED = {"address", "billing_lm", "graph_embeddings", "growth", "plausibility", "sector_schemes",
           "smoking_gun_timeline", "label", "kickback", "capacity", "entity_graph", "servicing", "mue",
           "drug_markup", "facility_code", "asof_v3", "doj_case", "medicaid_ndc", "medicaid_enrollment",
           "ownership_churn", "graph_velocity", "nucc", "saturation"}
# source -> reminder key (docs/reminders.json cadence)
REMINDER_KEY = {
    "facility": "pre_cutoff_editions_2021", "pbj": "pre_cutoff_editions_2021", "pos": "pre_cutoff_editions_2021",
    "nh_ownership": "pre_cutoff_editions_2021", "nh_penalties": "pre_cutoff_editions_2021",
    "deficiencies": "pre_cutoff_editions_2021", "order_referring": "pre_cutoff_editions_2021",
    "nadac": "nadac", "hospice": "hospice_2021_archive", "hcris": "hcris", "hrsa_340b": "hrsa_340b",
    "nppes_deactivation": "nppes_deactivation", "open_payments": "open_payments", "partb": "cms_pufs",
    "partd": "cms_pufs", "dmepos": "cms_pufs", "opioid": "cms_pufs", "partb_trend": "cms_pufs",
    "partd_trend": "cms_pufs", "dmepos_trend": "cms_pufs", "opioid_trend": "cms_pufs",
    "death_master": "ssa_dmf", "docgraph": "docgraph", "label_leie": "leie", "sam": "sam_exclusions",
}
_FILE_RE = re.compile(r"([\w\-. ()]+?\.(?:csv|zip|xlsx|parquet|txt))\b", re.I)


def _file_period(name: str) -> str | None:
    from .provider_features_export import _file_period
    return _file_period(name)


def _files_in(detail: str) -> list[str]:
    seen, out = set(), []
    for m in _FILE_RE.finditer(detail or ""):
        f = m.group(1).strip()
        if f.lower() in seen:
            continue
        seen.add(f.lower())
        out.append(f)
    return out


def classify_sources(audit: list[dict], cutoff: str | None) -> pd.DataFrame:
    cut = (str(cutoff)[:7] + ("-12" if len(str(cutoff)) == 4 else "")) if cutoff else None
    rows = []
    for r in audit:
        src, status, detail = str(r.get("source", "")), str(r.get("status", "")), str(r.get("detail", "") or "")
        files = [f for f in _files_in(detail) if not f.endswith(".parquet") or "asof" not in f]
        periods = [p for p in (_file_period(f) for f in files) if p]
        vintage = max(periods) if periods else ""
        if status != "used":
            verdict, action = "SKIPPED", detail[:140]
        elif src in DERIVED and not periods:
            verdict, action = "DERIVED", "computed from the spending fact / NPPES / the graph; nothing to download"
        elif cut and periods and vintage > cut:
            pre = [p for p in periods if p <= cut]
            verdict = "POST-CUTOFF" if not pre else "MIXED"
            action = (f"edition {vintage} is after the freeze {cut}: download the pre-cutoff edition"
                      if not pre else f"editions {', '.join(sorted(set(periods)))}: the picker keeps those on/before {cut}")
        elif not periods and files:
            verdict, action = "UNDATED", "no date in the file name: run vintage_inspect, then rename or record in EDITIONS.csv"
        elif not files:
            verdict, action = "DERIVED", "no file named in the log line"
        else:
            verdict, action = "OK", f"edition {vintage}" + (f" on/before {cut}" if cut else "")
        rows.append({"source": src, "status": status, "files": "; ".join(files)[:160], "vintage": vintage,
                     "verdict": verdict, "action": action, "reminder": REMINDER_KEY.get(src, "")})
    order = {"POST-CUTOFF": 0, "UNDATED": 1, "MIXED": 2, "SKIPPED": 3, "OK": 4, "DERIVED": 5}
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values(["verdict", "source"], key=lambda s: s.map(order) if s.name == "verdict" else s)
    return df.reset_index(drop=True)


def due_downloads(reminders_path: Path, today: date | None = None, horizon_days: int = 30) -> pd.DataFrame:
    today = today or date.today()
    if not reminders_path or not Path(reminders_path).exists():
        return pd.DataFrame(columns=["key", "what", "due", "status", "url"])
    d = json.loads(Path(reminders_path).read_text(encoding="utf-8"))
    rows = []
    for c in d.get("cadence", []):
        last, every = c.get("last_done"), c.get("every_months")
        if last and every:
            y, m, dd = (int(x) for x in str(last)[:10].split("-"))
            mm = m + int(every)
            y += (mm - 1) // 12
            mm = (mm - 1) % 12 + 1
            due = date(y, mm, min(dd, 28))
        else:
            due = None
        status = ("DUE NOW" if (due is None or due <= today) else
                  ("coming up" if due <= today + timedelta(days=horizon_days) else "ok"))
        if status == "ok":
            continue
        rows.append({"key": c.get("key"), "what": str(c.get("what", ""))[:110], "due": str(due) if due else "never done",
                     "status": status, "url": c.get("url", "")})
    return pd.DataFrame(rows).sort_values(["status", "due"]).reset_index(drop=True) if rows else \
        pd.DataFrame(columns=["key", "what", "due", "status", "url"])


def inspector_redownload_list(data_root: Path | None) -> list[str]:
    if not data_root:
        return []
    p = Path(data_root) / "preclean" / "VINTAGE_INSPECT.md"
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"re-?download[^\n]*\n(.*?)(?:\n## |\Z)", text, re.S | re.I)
    if not m:
        return []
    return [l.strip() for l in m.group(1).splitlines() if l.strip().startswith(("-", "|")) and "---" not in l][:40]


def to_markdown(table: pd.DataFrame, due: pd.DataFrame, redl: list[str], cutoff: str | None, run_name: str) -> str:
    L = [f"# DATA_REFRESH — what to download, what cannot be dated ({run_name})", "",
         f"_Built from the run's sources audit, the file names it logged, the freeze ({cutoff or 'current-day'}) "
         "and docs/reminders.json. POST-CUTOFF = the only edition on disk is after the freeze (the block read "
         "nothing or the vintage picker skipped it): download the pre-cutoff edition. UNDATED = a real file with "
         "no date token: run `python -m src.vintage_inspect --data-root <root>` and record the edition. DERIVED = "
         "computed, nothing to fetch. SKIPPED = the source had no file (the reason is the skip line)._", ""]
    for verdict in ("POST-CUTOFF", "MIXED", "UNDATED", "SKIPPED"):
        t = table[table["verdict"] == verdict] if len(table) else table
        if not len(t):
            continue
        L += [f"## {verdict} ({len(t)})", "", "| source | file(s) read | edition | action | reminder key |",
              "|---|---|---|---|---|"]
        for r in t.itertuples():
            L.append(f"| `{r.source}` | {r.files or '-'} | {r.vintage or '-'} | {r.action} | {r.reminder or '-'} |")
        L.append("")
    n_ok = int((table["verdict"].isin(["OK", "DERIVED"])).sum()) if len(table) else 0
    L.append(f"_{n_ok} sources read an edition on/before the freeze or are derived (full table in data_refresh.csv)._")
    L += ["", "## Scheduled downloads due (docs/reminders.json)", ""]
    if len(due):
        L += ["| key | status | due | what |", "|---|---|---|---|"]
        for r in due.itertuples():
            L.append(f"| `{r.key}` | {r.status} | {r.due} | {r.what} |")
        L.append("")
        L.append("_Steps, URLs and save paths: `python -m src.reminders`; mark done with `--done <key>` (proof-guarded)._")
    else:
        L.append("_nothing due within 30 days_")
    if redl:
        L += ["", "## The vintage inspector's re-download list (preclean/VINTAGE_INSPECT.md)", ""] + redl
    else:
        L += ["", "_preclean/VINTAGE_INSPECT.md not found: the inspector has not run on this data root; it is the "
                  "one tool that dates a file from its contents (step 0 of the runner runs it weekly)._"]
    return "\n".join(L) + "\n"


def run(run_dir: Path, data_root: Path | None, reminders: Path | None, out: Path | None = None) -> Path:
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "feature_manifest.json").read_text(encoding="utf-8"))
    cutoff = manifest.get("asof_cutoff") or (manifest.get("graph_substrate") or {}).get("asof")
    table = classify_sources(manifest.get("sources_audit") or [], cutoff)
    due = due_downloads(reminders) if reminders else pd.DataFrame(columns=["key", "what", "due", "status", "url"])
    redl = inspector_redownload_list(data_root)
    out = Path(out) if out else run_dir / "DATA_REFRESH.md"
    out.write_text(to_markdown(table, due, redl, cutoff, run_dir.name), encoding="utf-8")
    table.to_csv(out.with_name("data_refresh.csv"), index=False)
    out.with_name("data_refresh.json").write_text(json.dumps({
        "cutoff": cutoff, "n_post_cutoff": int((table["verdict"] == "POST-CUTOFF").sum()) if len(table) else 0,
        "n_undated": int((table["verdict"] == "UNDATED").sum()) if len(table) else 0,
        "n_skipped": int((table["verdict"] == "SKIPPED").sum()) if len(table) else 0,
        "due_now": [r["key"] for r in due.to_dict("records") if r["status"] == "DUE NOW"],
        "coming_up": [r["key"] for r in due.to_dict("records") if r["status"] == "coming up"],
    }, indent=1), encoding="utf-8")
    print(f"[data_refresh] -> {out}")
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--reminders", default=str(Path(__file__).resolve().parents[2] / "docs" / "reminders.json"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    run(Path(a.run_dir), Path(a.data_root) if a.data_root else None, Path(a.reminders) if a.reminders else None,
        Path(a.out) if a.out else None)


if __name__ == "__main__":
    main()
