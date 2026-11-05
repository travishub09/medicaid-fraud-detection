"""
ccn_lookup.py — every row about ONE facility across a folder of CMS CSVs.

Counsel's "what does CMS say about this home?" answered from the operator's own
files: for each CSV under --dir whose header carries a CCN column (Care Compare
NH_HealthCitations / NH_Penalties / NH_Ownership / ProviderInfo, POS, PBJ, the
hospice and home-health files), print the rows whose CCN matches. Streamed
through DuckDB with a WHERE on the CCN, so a folder of multi-GB files costs
seconds and no RAM: safe on the laptop (nothing here reads the spending fact).

    python -m src.ingest_cms.ccn_lookup --dir <data_root>/preclean/facility --ccn 365162
    python -m src.ingest_cms.ccn_lookup --dir ... --ccn 365162 --glob "NH_HealthCitations*.csv" --out rows.md
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns
from src.attempt_2.duck import connect as _duck_connect
from .pbj_therapy import _csv_encoding

CCN_COLS = {"ccn": ["CMS Certification Number (CCN)", "cms_certification_number_ccn",
                    "Federal Provider Number", "federal_provider_number", "PROVNUM",
                    "provnum", "CCN", "ccn", "PRVDR_NUM", "Provider ID", "provider_id"]}


def rows_for_ccn(path: str | Path, ccn: str, con=None) -> pd.DataFrame | None:
    """The file's rows for the CCN (all columns, as text), or None when the file
    has no CCN column. CCNs compare zero-padded to 6 and with spaces removed."""
    con = con or _duck_connect()
    path = str(path)
    want = str(ccn).strip().replace(" ", "").zfill(6)
    # 2026-11-05: NH_Penalties_Nov2021.csv carries a CP1252 curly apostrophe (byte 0x92)
    # on line 2,375; the one-row encoding sniff said UTF-8, the full scan raised, and
    # DuckDB's own 'latin-1' decoder refuses the 0x80-0x9F range CP1252 uses, so the
    # file read as "neither UTF-8 nor Latin-1" and every Ohio ccn file's Nov-2021 penalty
    # section said 'could not read'. DuckDB first; on any read failure the file goes
    # through pandas with Python's Latin-1 (which decodes every byte), the shared
    # raw-CSV convention of clean_data.read_csv_text. These files are tens of MB.
    try:
        enc = _csv_encoding(con, path)
        hdr = [r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_csv_auto(?, all_varchar=true, encoding='{enc}')", [path]).fetchall()]
        col = _resolve_columns(hdr, CCN_COLS).get("ccn")
        if not col:
            return None
        q = (f"SELECT * FROM read_csv_auto(?, all_varchar=true, encoding='{enc}') "
             f"WHERE lpad(replace(trim(\"{col}\"), ' ', ''), 6, '0') = ?")
        return con.execute(q, [path, want]).df()
    except Exception:  # noqa: BLE001 - the pandas fallback is the point
        pass
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="latin-1")
    col = _resolve_columns(list(df.columns), CCN_COLS).get("ccn")
    if not col:
        return None
    key = df[col].astype(str).str.strip().str.replace(" ", "", regex=False).str.zfill(6)
    return df[key == want].reset_index(drop=True)


def _pipe_table(df: pd.DataFrame) -> str:
    """A Markdown pipe table without the tabulate dependency (the laptop may lack it)."""
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = ["" if pd.isna(v) else str(v).replace("|", "\\|").replace("\n", " ") for v in r.tolist()]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--ccn", required=True)
    ap.add_argument("--glob", default="*.csv")
    ap.add_argument("--out", default=None, help="Markdown path (default: print)")
    args = ap.parse_args()
    con = _duck_connect()
    parts = [f"# CCN {str(args.ccn).zfill(6)} across {args.dir}", ""]
    n_files = 0
    for f in sorted(Path(args.dir).glob(args.glob)):
        try:
            df = rows_for_ccn(f, args.ccn, con)
        except Exception as e:  # noqa: BLE001 — one unreadable file never hides the rest
            parts += [f"## {f.name}", "", f"could not read: {e}", ""]
            continue
        if df is None:
            continue
        n_files += 1
        parts += [f"## {f.name} — {len(df)} rows", ""]
        if len(df):
            parts.append(_pipe_table(df))
            parts.append("")
    if not n_files:
        parts.append("No file under the folder carries a CCN column.")
    md = "\n".join(parts) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"[ccn_lookup] wrote {args.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
