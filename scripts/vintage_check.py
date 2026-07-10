"""
vintage_check.py — validate the fresh downloads AND audit year coverage per source.

Read-only. Run after new files land, send back VINTAGE_REPORT.txt:

    cd C:\\Users\\treyr\\medicaid-fraud-detection
    python scripts\\vintage_check.py --data-root "C:\\Users\\treyr\\OneDrive\\Desktop\\data"

Two jobs in one pass:

1. Every file in each annual-PUF folder is checked with the REAL adapter column
   resolver (not the filename), so a wrong variant (a summary layout, a lost
   header) is caught before a run wastes hours.
2. Year coverage is audited against BOTH runs the pipeline does:
     - the CURRENT-DAY run wants the newest valid year, and
     - the FROZEN run (as-of cutoff, default 2023-12) refuses annual files newer
       than the cutoff year, so it needs a valid CUTOFF-YEAR-or-older file.
   Anything missing lands on a single DOWNLOAD LIST at the end: dataset, year,
   where to get it, and the exact save path — so no metric silently drops out of
   either matrix because of a vintage gap.
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
DOWNLOADS: list[dict] = []


def say(msg: str = "") -> None:
    print(msg)
    LINES.append(msg)


def _headers(p: Path) -> list[str]:
    try:
        if p.suffix.lower() == ".parquet":
            import pyarrow.parquet as pq
            return [str(c) for c in pq.ParquetFile(p).schema_arrow.names]
        if p.suffix.lower() in (".xlsx", ".xls"):
            return [str(c) for c in pd.read_excel(p, nrows=5, dtype=str).columns]
        from src.attempt_2.clean_data import read_csv_text
        return [str(c) for c in read_csv_text(p, nrows=0).columns]
    except Exception as e:
        return [f"<unreadable: {e}>"]


def _annual_sources():
    """name -> (subfolder, COLS dict, required canonicals, where to download,
    save-name pattern). Required lists mirror each adapter's own hard check."""
    from src.ingest_cms import partb, partd, dmepos, opioid, openpayments
    return {
        "partb": ("partb", partb.PARTB_COLS, ["npi", "hcpcs", "services"],
                  "data.cms.gov > search 'Medicare Physician & Other Practitioners "
                  "- by Provider and Service'", "partb_<YEAR>.csv"),
        "partd": ("partd", partd.PARTD_COLS, ["npi", "claims", "cost"],
                  "data.cms.gov > search 'Medicare Part D Prescribers - by Provider'",
                  "partd_<YEAR>.csv"),
        "dmepos": ("dmepos", dmepos.DMEPOS_COLS, ["npi", "hcpcs", "services"],
                   "data.cms.gov > search 'Medicare Durable Medical Equipment, "
                   "Devices & Supplies - by Referring Provider and Service'",
                   "dmepos_<YEAR>.csv"),
        "opioid": ("opioid", opioid.OPIOID_COLS, ["npi", "opioid_claims"],
                   "data.cms.gov > search 'Medicare Part D Opioid Prescribing Rates'",
                   "opioid_prescriber_<YEAR>.csv"),
        "open_payments": ("open_payments", openpayments.OP_COLS,
                          ["npi", "manufacturer", "amount"],
                          "openpaymentsdata.cms.gov > Download the General "
                          "Payments file for the program year",
                          "open_payments_<YEAR>.csv"),
    }


def audit_annual(root: Path, cutoff_year: int) -> None:
    from src.attempt_2.clean_data import _resolve_columns
    from src.model_a.provider_features_export import _file_year
    say("ANNUAL SOURCES — layout check per file + year coverage for both runs")
    say(f"(current-day run wants the newest valid year; frozen run needs "
        f"{cutoff_year} or older)")
    say()
    for name, (sub, cols, required, where, pattern) in _annual_sources().items():
        d = root / "preclean" / sub
        say(f"[{name}]  folder: preclean/{sub}/")
        files = (sorted(d.glob("*.csv")) + sorted(d.glob("*.parquet"))
                 + sorted(d.glob("*.xlsx"))) if d.is_dir() else []
        if not files:
            say("   NO FILES AT ALL")
            DOWNLOADS.append({"dataset": name, "year": "latest available",
                              "why": "no file on disk (current-day run)",
                              "where": where, "save_as": f"preclean/{sub}/{pattern}"})
            DOWNLOADS.append({"dataset": name, "year": str(cutoff_year),
                              "why": f"no file on disk (frozen {cutoff_year}-12 run)",
                              "where": where, "save_as": f"preclean/{sub}/{pattern}"})
            say()
            continue
        valid_years, invalid, unknown_year_valid = [], [], []
        for p in files:
            hdr = _headers(p)
            resolved = _resolve_columns(hdr, cols)
            missing = [c for c in required if c not in resolved]
            yr = _file_year(p)
            tag = f"{p.name} (year {yr if yr > 0 else '?'})"
            if missing:
                say(f"   BAD LAYOUT: {tag} — missing {missing}")
                invalid.append(p.name)
            else:
                say(f"   ok: {tag}")
                if yr > 0:
                    valid_years.append(yr)
                else:
                    unknown_year_valid.append(p.name)
        newest = max(valid_years) if valid_years else None
        frozen_ok = [y for y in valid_years if y <= cutoff_year]
        # current-day run
        if newest is None and not unknown_year_valid:
            DOWNLOADS.append({"dataset": name, "year": "latest available",
                              "why": "no valid layout on disk (current-day run)",
                              "where": where, "save_as": f"preclean/{sub}/{pattern}"})
        elif newest is not None and newest < cutoff_year:
            DOWNLOADS.append({"dataset": name, "year": "latest available",
                              "why": f"newest valid year on disk is {newest} — stale "
                                     "for the current-day run",
                              "where": where, "save_as": f"preclean/{sub}/{pattern}"})
        # frozen run
        if not frozen_ok:
            DOWNLOADS.append({"dataset": name, "year": str(cutoff_year),
                              "why": f"frozen {cutoff_year}-12 run has no valid "
                                     f"{cutoff_year}-or-older file (vintage cap "
                                     "would skip this source)",
                              "where": where, "save_as": f"preclean/{sub}/{pattern}"})
        elif max(frozen_ok) < cutoff_year:
            DOWNLOADS.append({"dataset": name, "year": str(cutoff_year),
                              "why": f"frozen run would fall back to {max(frozen_ok)} "
                                     f"— the {cutoff_year} year is closer to the cutoff",
                              "where": where, "save_as": f"preclean/{sub}/{pattern}"})
        if unknown_year_valid:
            say(f"   NOTE: {', '.join(unknown_year_valid)} has no year in the "
                "filename — the frozen vintage cap cannot judge it. Rename it "
                "with its data year (e.g. "
                f"{pattern.replace('<YEAR>', '2023')}) so both runs pick correctly.")
        say()


def check_fresh_reference_files(root: Path) -> None:
    say("FRESH DOWNLOADS — validated with the real loaders")
    say()
    from src.attempt_2.clean_data import _resolve_columns

    # NADAC reference
    from src.ingest_cms.nadac import NADAC_COLS
    d = root / "preclean" / "nadac"
    hits = sorted(d.glob("*.csv")) if d.is_dir() else []
    if hits:
        resolved = _resolve_columns(_headers(hits[0]), NADAC_COLS)
        ok = "ndc" in resolved and "per_unit" in resolved
        say(f"[nadac] {hits[0].name}: {'VALID (ndc + per-unit resolve)' if ok else 'BAD — ndc/per-unit columns not found: ' + str(_headers(hits[0])[:8])}")
        say("   note: NADAC vintage does not matter for the frozen run design; the "
            "reference is a price benchmark. The scheme itself stays dormant until "
            "an NDC claims slice exists (see fix_sources diagnostic).")
    else:
        say("[nadac] no file yet (PDF #3)")
    say()

    # Order & Referring eligibility
    from src.ingest_cms.order_referring import ELIGIBLE_COLS
    d = root / "preclean" / "order_referring"
    hits = sorted(d.glob("*.csv")) if d.is_dir() else []
    if hits:
        resolved = _resolve_columns(_headers(hits[0]), ELIGIBLE_COLS)
        say(f"[order_referring] {hits[0].name}: "
            f"{'VALID (NPI column resolves)' if 'npi' in resolved else 'BAD — no NPI column: ' + str(_headers(hits[0])[:8])}")
        say("   note: this is a CURRENT-STATE eligibility list; CMS does not "
            "publish 2023 versions. Using today's list on the frozen run is a "
            "disclosed limitation, not a fixable gap.")
    else:
        say("[order_referring] no file yet (PDF #4)")
    say()

    # 340B OPAIS via the full loader
    d = root / "preclean" / "hrsa_340b"
    hits = (sorted(d.glob("opais.*")) + sorted(d.glob("*.xlsx"))
            + sorted(d.glob("*.csv"))) if d.is_dir() else []
    if hits:
        try:
            from src.ingest_cms.hrsa_340b import load_opais, covered_entities
            ents = covered_entities(load_opais(hits[0]))
            say(f"[hrsa_340b] {hits[0].name}: VALID — {len(ents):,} covered entities")
        except Exception as e:
            say(f"[hrsa_340b] {hits[0].name}: STILL FAILING — {e}")
            DOWNLOADS.append({"dataset": "hrsa_340b", "year": "today's daily report",
                              "why": "file on disk does not parse",
                              "where": "340bopais.hrsa.gov/reports > Covered Entity "
                                       "Daily Export",
                              "save_as": "preclean/hrsa_340b/opais.xlsx"})
    else:
        say("[hrsa_340b] no file yet (PDF #2)")
    say()

    # DMEPOS detail (also covered in the annual audit; this is the fresh-file echo)
    say("[dmepos] see the annual audit above — the fresh detail file is checked "
        "there with every other year.")
    say()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=str(Path.home() / "Desktop" / "data"))
    ap.add_argument("--cutoff-year", type=int, default=2023,
                    help="frozen-run cutoff year (default 2023 for the 2023-12 freeze)")
    args = ap.parse_args()
    root = Path(args.data_root)
    say("VINTAGE + FRESH-FILE CHECK")
    say(f"data root: {root}   frozen cutoff year: {args.cutoff_year}")
    say("=" * 70)
    say()
    audit_annual(root, args.cutoff_year)
    check_fresh_reference_files(root)

    say("=" * 70)
    if DOWNLOADS:
        say(f"DOWNLOAD LIST — {len(DOWNLOADS)} item(s), in this order:")
        say()
        for i, dl in enumerate(DOWNLOADS, 1):
            say(f"{i}. {dl['dataset']} — year: {dl['year']}")
            say(f"   why: {dl['why']}")
            say(f"   where: {dl['where']}")
            say(f"   save as: {dl['save_as']}")
            say()
    else:
        say("DOWNLOAD LIST: empty — every source has a valid file for BOTH runs. "
            "Nothing to fetch.")
    out = Path("VINTAGE_REPORT.txt")
    out.write_text("\n".join(LINES), encoding="utf-8")
    say(f"wrote {out.resolve()} — send this file back.")


if __name__ == "__main__":
    main()
