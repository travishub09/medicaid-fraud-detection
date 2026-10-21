"""State-list sorter: the operator's Downloads → preclean/state_terminations/<ST>_terminations_<yyyymm>.<ext>

The reminders referenced a ``sort_state_lists.py`` that was never committed; this is it.

What it does (dry run by default; ``--apply`` copies):
  1. finds every .xlsx / .xls / .csv under ``--downloads`` whose name carries a state
     (a two-letter prefix ``PA_`` / ``oh_`` / ``NY `` / ``tx ``, a state name, or a known
     agency token: SOMIG / OMIG = NY, Medicheck = PA, suspended-ineligible = CA, ...);
  2. reads it through the loader's own header resolver (``state_terminations.read_state_file``)
     and refuses a file with no name column (the loader would skip it anyway);
  3. collapses browser duplicates ("(1)", "(2)" copies of the same bytes);
  4. names the copy ``<ST>_terminations_<yyyymm>.<ext>`` (the month from the file name when
     it carries one, else today) and, with ``--apply``, copies it into
     ``<data_root>/preclean/state_terminations/``. The download stays where it was.

Nothing here tiers anything: the reason text rides into the loader, ``fca_relevance``
tiers every row by its stated reason, and an unknown reason defaults to WEAK (never a
training positive under ``--label-tier hard``); STATE_TERMINATIONS_REPORT.md lists the
reasons the tier table does not know.

Run (laptop, from the repo folder):
  python -m src.enforcement.state_list_sorter                # dry run
  python -m src.enforcement.state_list_sorter --apply        # copy
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
from datetime import date
from pathlib import Path

from src.model_a.state_fca import _NAME_TO_ABBR

# agency / dataset tokens that name a state when the file name carries no code or state name
AGENCY_TOKENS = {
    "somig": "NY", "omig": "NY",
    "medcheck": "PA", "medicheck": "PA",
    "suspended-ineligible": "CA", "suspended_ineligible": "CA", "suspended and ineligible": "CA",
    "texasexclusions": "TX", "hhsc": "TX",
    "exclusionsuspensionlist": "OH",
    "sanctioned_provider_list": "MI", "sanctioned provider list": "MI",
    "hfs": "IL", "active sanctions": "IL",
    "tenncare": "TN", "scdhhs": "SC", "ncdhhs": "NC", "chfs": "KY",
}
_EXT = (".xlsx", ".xls", ".csv")
# files that carry a state name but are not sanction lists (the WARN layoff notices the
# sourcing monitor reads, Census population estimates): never listed
_NOT_A_LIST = re.compile(r"warn|layoff|co-est20|census|population|\bpop\b|template|smmc", re.I)
_DUP = re.compile(r"\s*\(\d+\)(?=\.[A-Za-z]+$)")           # "name (2).xlsx"
_NAME_YM = re.compile(r"(20\d{2})[-_ ]?(0[1-9]|1[0-2])(?![0-9])")
_NAME_MONTH = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-_ ]?(20\d{2})", re.I)
_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun",
                                            "jul", "aug", "sep", "oct", "nov", "dec"])}


def state_of(name: str) -> str:
    """The state a download's file name names, or ''."""
    stem = Path(name).stem
    m = re.match(r"^([A-Za-z]{2})[ _\-]", stem)
    if m and m.group(1).upper() in _NAME_TO_ABBR.values():
        return m.group(1).upper()
    low = stem.lower().replace("_", " ")
    for full, code in sorted(_NAME_TO_ABBR.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"\b" + re.escape(full.lower()) + r"\b", low):
            return code
    for tok, code in AGENCY_TOKENS.items():
        if tok.replace("_", " ") in low or tok in stem.lower():
            return code
    return ""


def edition_of(name: str, today: date | None = None) -> str:
    """YYYYMM from the file name (2026-10-21, 2026_10, september-2026, 10_15_2026), else today."""
    stem = Path(name).stem
    m = _NAME_YM.search(stem)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    m = _NAME_MONTH.search(stem)
    if m:
        return f"{m.group(2)}{_MONTHS[m.group(1)[:3].lower()]:02d}"
    m = re.search(r"(?<!\d)(0?[1-9]|1[0-2])[-_](0?[1-9]|[12]\d|3[01])[-_](20\d{2})", stem)   # 10_15_2026
    if m:
        return f"{m.group(3)}{int(m.group(1)):02d}"
    t = today or date.today()
    return f"{t.year}{t.month:02d}"


def _digest(p: Path) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def plan(downloads: Path, dest: Path, today: date | None = None) -> list[dict]:
    """One row per candidate file: state, edition, target name, header verdict, duplicate note."""
    from src.enforcement.state_terminations import read_state_file
    rows: list[dict] = []
    seen: dict[str, str] = {}
    cands = sorted((p for p in Path(downloads).iterdir()
                    if p.suffix.lower() in _EXT and not p.name.startswith("~")),
                   key=lambda p: (bool(_DUP.search(p.name)), p.name.lower()))   # originals before "(1)" copies
    for p in cands:
        st = state_of(p.name)
        if not st or _NOT_A_LIST.search(p.stem):
            continue
        row = {"file": p.name, "state": st, "edition": edition_of(p.name, today),
               "target": f"{st}_terminations_{edition_of(p.name, today)}{p.suffix.lower()}",
               "rows": 0, "name_col": "", "npi_col": "", "date_col": "", "reason_col": "", "note": ""}
        d = _digest(p)
        if d in seen:
            row["note"] = f"duplicate of {seen[d]} (same bytes): skipped"
            rows.append(row)
            continue
        seen[d] = p.name
        try:
            df, fields, sheet = read_state_file(p)
        except ImportError as ex:
            row["note"] = f"needs a reader: {ex} (pip install xlrd for .xls)"
            rows.append(row)
            continue
        except Exception as ex:  # noqa: BLE001
            row["note"] = f"unreadable: {type(ex).__name__}: {str(ex)[:80]}"
            rows.append(row)
            continue
        row.update({"rows": int(len(df)), "name_col": fields.get("org_name") or fields.get("last_name") or "",
                    "npi_col": fields.get("npi", ""), "date_col": fields.get("excl_date", ""),
                    "reason_col": fields.get("reason", "") or fields.get("reason2", ""), "sheet": sheet})
        if not row["name_col"]:
            row["note"] = f"NO NAME COLUMN: header {list(df.columns)[:6]}; the loader would skip it"
        elif not row["date_col"] and not row["reason_col"]:
            # 2026-10-21: the FLMMIS Provider Master List (466,694 enrolled providers) carried a
            # state name and a name column; copied, it would make every enrolled Florida
            # provider an exclusion node. A sanction list carries a date or a reason.
            row["note"] = (f"NO DATE AND NO REASON COLUMN: a provider roster, not a sanction list "
                           f"(header {list(df.columns)[:6]}); NOT copied")
        elif not row["reason_col"]:
            row["note"] = "no reason column: every row tiers WEAK (never trains under hard)"
        if (dest / row["target"]).exists():
            row["note"] = (row["note"] + "; " if row["note"] else "") + "target exists (would overwrite)"
        rows.append(row)
    return rows


def apply(rows: list[dict], downloads: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    done = []
    for r in rows:
        if not r["name_col"] or r["note"].startswith(("duplicate", "unreadable", "needs a reader",
                                                         "NO DATE AND NO REASON")):
            continue
        src = downloads / r["file"]
        tgt = dest / r["target"]
        shutil.copy2(src, tgt)
        done.append(tgt)
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--downloads", default=str(Path.home() / "Downloads"))
    ap.add_argument("--data-root", default=os.environ.get("MEDICAID_DATA_ROOT") or str(Path.home() / "Desktop" / "data"))
    ap.add_argument("--apply", action="store_true", help="copy the files (default: dry run)")
    a = ap.parse_args()
    # `set MEDICAID_DATA_ROOT=C:\...\data && python ...` keeps the space before && in the
    # value on Windows: the 2026-10-21 apply aimed at a folder named "data " and failed
    downloads, dest = Path(a.downloads.strip()), Path(a.data_root.strip()) / "preclean" / "state_terminations"
    rows = plan(downloads, dest)
    if not rows:
        print(f"[sorter] no state-named .xlsx/.xls/.csv under {downloads}")
        return
    print(f"[sorter] {downloads} -> {dest}" + ("" if a.apply else "   (DRY RUN: add --apply to copy)"))
    print(f"{'file':52s} {'st':2s} {'rows':>7s}  name / npi / date / reason columns  -> target | note")
    for r in rows:
        cols = " / ".join(str(r.get(k) or "-")[:18] for k in ("name_col", "npi_col", "date_col", "reason_col"))
        print(f"{r['file'][:52]:52s} {r['state']:2s} {r['rows']:7,d}  {cols}  -> {r['target']}"
              + (f" | {r['note']}" if r["note"] else ""))
    if a.apply:
        done = apply(rows, downloads, dest)
        print(f"[sorter] copied {len(done)} file(s):")
        for p in done:
            print(f"  {p}")
        print("[sorter] next: the files reach the VM with the preclean sync; the runner loads them "
              "(step 2a) and rebuilds the current-day graph (step 2b). Read STATE_TERMINATIONS_REPORT.md "
              "for the reasons the tier table does not know.")


if __name__ == "__main__":
    main()
