"""
cutoff_census.py — the complete temporal census of EVERY file under preclean/.

Read-only. Run it and send back CUTOFF_CENSUS.txt:

    cd C:\\Users\\treyr\\medicaid-fraud-detection
    python scripts\\cutoff_census.py --data-root "C:\\Users\\treyr\\OneDrive\\Desktop\\data"

For each file in each folder (and the big root files: Spending, PECOS, NPPES,
Caught) this answers the only temporal question that matters for both the
frozen test and the design of every analytic: WHERE DO THIS FILE'S DATES LIVE?

  DATED ROWS      the file carries a date/month column per row. The script
                  reads the actual MIN and MAX out of the data (DuckDB scan),
                  because that range IS the file's cutoff story — the pipeline
                  freezes these by filtering the rows, so the download date is
                  irrelevant.
  FILENAME YEAR   an annual vintage in the name (partb_2023). Governed by the
                  frozen vintage cap. No content scan needed.
  SNAPSHOT        no dates in the name or the rows (NPPES, PECOS, structure
                  files). The save date is the ONLY date, and the report says
                  so honestly — these are the current_state class in the
                  manifest: usable, but never truly frozen.

Big files are scanned with DuckDB (streaming, memory-capped) — the 10 GB
Spending.csv takes a couple of minutes and prints progress, it is not hung.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pandas as pd  # noqa: E402

LINES: list[str] = []


def say(msg: str = "") -> None:
    print(msg, flush=True)
    LINES.append(msg)


# column-name shapes that carry per-row time
_DATE_TOKENS = ("date", "month", "_dt", "year", "period", "time")
# name shapes that are NOT event time (publication/system stamps)
_NOT_EVENT = ("publica", "created", "updated", "release", "record", "certif",
              "expir", "birth", "dob")


def _headers(p: Path) -> list[str]:
    try:
        if p.suffix.lower() == ".parquet":
            import pyarrow.parquet as pq
            return [str(c) for c in pq.ParquetFile(p).schema_arrow.names]
        if p.suffix.lower() in (".xlsx", ".xls", ".zip"):
            return []            # probed elsewhere / listed only
        from src.attempt_2.clean_data import read_csv_text
        return [str(c) for c in read_csv_text(p, nrows=0).columns]
    except Exception as e:
        return [f"<unreadable: {e}>"]


def _date_cols(header: list[str]) -> list[str]:
    out = []
    for c in header:
        lc = str(c).lower()
        if any(t in lc for t in _DATE_TOKENS) and not any(t in lc for t in _NOT_EVENT):
            out.append(str(c))
    return out[:4]


def _minmax(p: Path, cols: list[str]) -> dict[str, tuple[str, str]]:
    """Exact per-column MIN/MAX via a streaming DuckDB scan (memory-capped)."""
    import duckdb
    con = duckdb.connect()
    con.execute("PRAGMA memory_limit='4GB'")
    esc = str(p).replace("'", "''")
    src = (f"read_parquet('{esc}')" if p.suffix.lower() == ".parquet"
           else f"read_csv_auto('{esc}', ALL_VARCHAR=TRUE, ignore_errors=true, "
                "encoding='latin-1')")
    out: dict[str, tuple[str, str]] = {}
    for c in cols:
        try:
            qc = c.replace('"', '""')
            lo, hi = con.execute(
                f'SELECT MIN("{qc}"), MAX("{qc}") FROM {src} '
                f'WHERE "{qc}" IS NOT NULL AND TRIM(CAST("{qc}" AS VARCHAR)) <> \'\''
            ).fetchone()
            if lo is not None:
                out[c] = (str(lo)[:10], str(hi)[:10])
        except Exception as e:
            out[c] = (f"<scan failed: {e}>", "")
    con.close()
    return out


def _classify(p: Path, quick_mb: int) -> None:
    import datetime as _dt
    from src.model_a.provider_features_export import _file_year
    size_mb = p.stat().st_size / 1e6
    mtime = _dt.date.fromtimestamp(p.stat().st_mtime)
    yr = _file_year(p)
    if p.suffix.lower() in (".xlsx", ".xls", ".zip"):
        say(f"   {p.name}  ({size_mb:,.0f} MB, saved {mtime})")
        say(f"      SNAPSHOT (binary container): the loader probes/parses it at "
            "run time; no per-row date scan here."
            + (f" Filename year {yr}." if yr > 0 else ""))
        return
    header = _headers(p)
    if header and header[0].startswith("<unreadable"):
        say(f"   {p.name}: {header[0]}")
        return
    dcols = _date_cols(header)
    tag = f"   {p.name}  ({size_mb:,.0f} MB, saved {mtime}"
    tag += f", filename year {yr})" if yr > 0 else ")"
    say(tag)
    if dcols:
        big_note = " (large file — a couple of minutes, not hung)" if size_mb > 1000 else ""
        say(f"      DATED ROWS — scanning min/max of {', '.join(dcols)}{big_note} ...")
        for c, (lo, hi) in _minmax(p, dcols).items():
            say(f"        {c}: {lo} .. {hi}")
        if yr > 0:
            say("      -> this range confirms the filename year; the frozen "
                "vintage cap governs which run uses it.")
        else:
            say("      -> this range IS the file's cutoff story. The pipeline "
                "date-filters the wired dated files (spending, exclusions, "
                "deactivations, revocations); if the MAX date above is later "
                "than your freeze cutoff on a file NOT in that list, flag it "
                "to me and I will confirm how it is filtered.")
    elif yr > 0:
        say("      FILENAME YEAR — governed by the frozen vintage cap; no "
            "per-row dates (whole file = that calendar year).")
    else:
        say("      SNAPSHOT — no date column, no filename year. The save date "
            "above is the ONLY date. current_state class: usable, never truly "
            "frozen (both A/B arms share it; strict frozen models can drop its "
            "features via the manifest's feature_vintage).")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=str(Path.home() / "Desktop" / "data"))
    ap.add_argument("--quick-mb", type=int, default=0,
                    help="reserved (all scans stream via DuckDB)")
    args = ap.parse_args()
    pre = Path(args.data_root) / "preclean"
    say("CUTOFF CENSUS — where every file's dates live")
    say(f"root: {pre}")
    say("=" * 70)
    if not pre.is_dir():
        say("preclean/ not found — check --data-root")
        return
    # root files first (Spending / PECOS / NPPES / Caught live at the top level)
    root_files = sorted([p for p in pre.iterdir() if p.is_file()
                         and p.suffix.lower() in (".csv", ".parquet", ".xlsx",
                                                  ".xls", ".zip")])
    say()
    say("[preclean ROOT FILES]")
    if not root_files:
        say("   (none)")
    for p in root_files:
        _classify(p, args.quick_mb)
    for d in sorted([d for d in pre.iterdir() if d.is_dir()]):
        say()
        say(f"[{d.name}/]")
        files = sorted([p for p in d.rglob("*") if p.is_file()
                        and p.suffix.lower() in (".csv", ".parquet", ".xlsx",
                                                 ".xls", ".zip")])
        if not files:
            say("   (no data files)")
        for p in files:
            _classify(p, args.quick_mb)
    say()
    say("=" * 70)
    say("HOW TO READ THIS")
    say("- DATED ROWS files are safe for every analytic: the pipeline (and any")
    say("  future analytic) filters them by the dates you see above. If a max")
    say("  date surprises you (older than expected), that is a re-download cue.")
    say("- FILENAME YEAR files are safe under the vintage cap.")
    say("- SNAPSHOT files are the honest limitation: no historical edition")
    say("  exists. They are tagged current_state in the training manifest so")
    say("  the model can be run with and without them.")
    out = Path("CUTOFF_CENSUS.txt")
    out.write_text("\n".join(LINES), encoding="utf-8")
    say(f"wrote {out.resolve()} — send this file back.")


if __name__ == "__main__":
    main()
