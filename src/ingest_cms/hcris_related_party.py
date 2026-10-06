"""
hcris_related_party.py — pull the related-organization worksheet (A-8-1) and the
facility identity worksheet (S-2) out of the RAW HCRIS SNF cost-report files for a
set of CCNs (or a state), as a long-form table a human can read.

Why: the published data.cms.gov "Skilled Nursing Facility Cost Report" file has no
related-party column (see hcris.py). The management fees, lease payments and
"administrative services" paid to companies the owners also own — the structure the
New York Attorney General's Lahasky complaint is built on — are disclosed on
Worksheet A-8-1 of Form CMS-2540-10, which only exists in the raw HCRIS download.

Source (free, no registration): CMS "Cost Reports by Fiscal Year",
https://www.cms.gov/data-research/statistics-trends-and-reports/cost-reports/cost-reports-fiscal-year
→ Skilled Nursing Facility (2540-10) → SNF10FY<year>.zip. Each zip holds three
header-less CSVs: snf10_<year>_RPT.CSV (one row per cost report), snf10_<year>_ALPHA.CSV
(text cells) and snf10_<year>_NMRC.CSV (numeric cells), keyed by RPT_REC_NUM +
worksheet code + line + column.

Usage:
  python -m src.ingest_cms.hcris_related_party --hcris-dir ~/Desktop/data/preclean/hcris/raw \
      --state OH --out ~/Desktop/data/processed/hcris_related_party_OH.csv
  python -m src.ingest_cms.hcris_related_party --hcris-dir ... --ccn 365162,365330 --out ...

CCN is a string (leading zeros kept); the first two digits are the state code
(Ohio = 36). Output columns: ccn, npi, fy_begin, fy_end, report_status, worksheet,
line, column, value (text or number). Rows are the raw cells of A-8-1 (code
A810000) and S-2 Part I (S200001); nothing is interpreted, so a reader sees exactly
what the facility filed. Dormant until the zips are downloaded.
"""

from __future__ import annotations

import argparse
import io
import zipfile
from pathlib import Path

import pandas as pd

STATE_CODES = {
    "AL": "01", "AK": "02", "AZ": "03", "AR": "04", "CA": "05", "CO": "06", "CT": "07",
    "DE": "08", "DC": "09", "FL": "10", "GA": "11", "HI": "12", "ID": "13", "IL": "14",
    "IN": "15", "IA": "16", "KS": "17", "KY": "18", "LA": "19", "ME": "20", "MD": "21",
    "MA": "22", "MI": "23", "MN": "24", "MS": "25", "MO": "26", "MT": "27", "NE": "28",
    "NV": "29", "NH": "30", "NJ": "31", "NM": "32", "NY": "33", "NC": "34", "ND": "35",
    "OH": "36", "OK": "37", "OR": "38", "PA": "39", "PR": "40", "RI": "41", "SC": "42",
    "SD": "43", "TN": "44", "TX": "45", "UT": "46", "VT": "47", "VI": "48", "VA": "49",
    "WA": "50", "WV": "51", "WI": "52", "WY": "53",
}

RPT_COLS = ["RPT_REC_NUM", "PRVDR_CTRL_TYPE_CD", "PRVDR_NUM", "NPI", "RPT_STUS_CD",
            "FY_BGN_DT", "FY_END_DT", "PROC_DT", "INITL_RPT_SW", "LAST_RPT_SW",
            "TRNSMTL_NUM", "FI_NUM", "ADR_VNDR_CD", "FI_CREAT_DT", "UTIL_CD", "NPR_DT",
            "SPEC_IND", "FI_RCPT_DT"]
CELL_COLS = ["RPT_REC_NUM", "WKSHT_CD", "LINE_NUM", "CLMN_NUM", "VALUE"]
# Worksheet codes are matched by PREFIX: the SNF layout splits A-8-1 into parts
# (A81000A / A81000B / A81000C), the hospital layout uses A810000; a prefix catches
# every vintage. A-8 (A800000) is kept too: its line 12 carries the A-8-1 total.
WORKSHEETS = {"A81": "A-8-1 related organizations", "A800": "A-8 adjustments",
              "S2000": "S-2 facility identity"}


def _read_headerless(raw: bytes, names: list[str]) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw), header=None, dtype=str, keep_default_na=False,
                     encoding="latin-1")
    df = df.iloc[:, : len(names)]
    df.columns = names[: df.shape[1]]
    return df


def load_hcris_year(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(reports, cells) from one SNF10FY<year>.zip or a folder holding its three CSVs."""
    files: dict[str, bytes] = {}
    if path.is_file() and path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                key = n.upper()
                for tag in ("_RPT", "_ALPHA", "_NMRC"):
                    if tag in key and key.endswith(".CSV"):
                        files[tag] = z.read(n)
    else:
        for p in Path(path).glob("*"):
            key = p.name.upper()
            for tag in ("_RPT", "_ALPHA", "_NMRC"):
                if tag in key and key.endswith(".CSV"):
                    files[tag] = p.read_bytes()
    missing = [t for t in ("_RPT", "_ALPHA", "_NMRC") if t not in files]
    if missing:
        raise FileNotFoundError(f"{path}: missing HCRIS parts {missing}")
    rpt = _read_headerless(files["_RPT"], RPT_COLS)
    alpha = _read_headerless(files["_ALPHA"], CELL_COLS)
    nmrc = _read_headerless(files["_NMRC"], CELL_COLS)
    cells = pd.concat([alpha.assign(kind="text"), nmrc.assign(kind="number")], ignore_index=True)
    return rpt, cells


def related_party_rows(rpt: pd.DataFrame, cells: pd.DataFrame, ccns: set[str] | None = None,
                       state: str | None = None, worksheets: dict | None = None) -> pd.DataFrame:
    """Long-form cells of the chosen worksheets for the chosen CCNs (or a state)."""
    worksheets = worksheets or WORKSHEETS
    r = rpt.copy()
    r["PRVDR_NUM"] = r["PRVDR_NUM"].astype(str).str.strip().str.zfill(6)
    if ccns:
        r = r[r["PRVDR_NUM"].isin({str(c).zfill(6) for c in ccns})]
    if state:
        code = STATE_CODES[state.upper()]
        r = r[r["PRVDR_NUM"].str[:2] == code]
    if not len(r):
        return pd.DataFrame(columns=["ccn", "npi", "fy_begin", "fy_end", "report_status",
                                     "worksheet", "worksheet_name", "line", "column", "kind", "value"])
    prefixes = tuple(worksheets)
    wk = cells["WKSHT_CD"].astype(str).str.strip()
    c = cells[wk.str.startswith(prefixes)]
    j = c.merge(r[["RPT_REC_NUM", "PRVDR_NUM", "NPI", "FY_BGN_DT", "FY_END_DT", "RPT_STUS_CD"]],
                on="RPT_REC_NUM", how="inner")

    def _name(code: str) -> str:
        return next((n for p, n in worksheets.items() if str(code).startswith(p)), "")

    out = pd.DataFrame({
        "ccn": j["PRVDR_NUM"], "npi": j["NPI"].astype(str).str.strip(),
        "fy_begin": j["FY_BGN_DT"], "fy_end": j["FY_END_DT"], "report_status": j["RPT_STUS_CD"],
        "worksheet": j["WKSHT_CD"].astype(str).str.strip(), "worksheet_name": j["WKSHT_CD"].map(_name),
        "line": j["LINE_NUM"].astype(str).str.zfill(5), "column": j["CLMN_NUM"].astype(str).str.zfill(5),
        "kind": j["kind"], "value": j["VALUE"],
    })
    return out.sort_values(["ccn", "fy_end", "worksheet", "line", "column"]).reset_index(drop=True)


def a81_summary(rows: pd.DataFrame) -> pd.DataFrame:
    """One line per CCN x fiscal year x A-8-1 line: the text cells joined (names, symbols,
    business type) next to the numeric cells (percent owned, amounts). The column numbers
    are kept in the headers so nothing is guessed about which is which."""
    a = rows[rows["worksheet"].astype(str).str.startswith("A81")]
    if not len(a):
        return pd.DataFrame(columns=["ccn", "fy_end", "worksheet", "line"])
    piv = a.pivot_table(index=["ccn", "fy_end", "worksheet", "line"], columns="column", values="value",
                        aggfunc="first").reset_index()
    piv.columns = [c if c in ("ccn", "fy_end", "worksheet", "line") else f"col_{c}" for c in piv.columns]
    return piv


def worksheet_census(cells: pd.DataFrame) -> pd.Series:
    """Distinct worksheet codes with cell counts: the diagnostic that would have shown
    the A-8-1 code on the first real run instead of an empty summary."""
    return cells["WKSHT_CD"].astype(str).str.strip().value_counts()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hcris-dir", required=True, help="folder of SNF10FY<year>.zip / SNF24FY<year>.zip files (or extracted CSV folders)")
    ap.add_argument("--ccn", default="", help="comma-separated CCNs (leading zeros kept)")
    ap.add_argument("--state", default=None, help="two-letter state; filters by CCN state prefix")
    ap.add_argument("--out", required=True, help="CSV path for the long-form cells; a _summary.csv sits beside it")
    a = ap.parse_args()
    ccns = {c.strip() for c in a.ccn.split(",") if c.strip()} or None
    root = Path(a.hcris_dir)
    sources = sorted({q for pat in ("SNF*FY*.zip", "snf*fy*.zip") for q in root.glob(pat)}) + sorted(p for p in root.glob("*") if p.is_dir())
    if not sources:
        raise SystemExit(f"no SNF10FY<year>.zip (or extracted folders) under {root}")
    frames = []
    for src in sources:
        try:
            rpt, cells = load_hcris_year(src)
        except FileNotFoundError as e:
            print(f"  skip {src.name}: {e}")
            continue
        part = related_party_rows(rpt, cells, ccns=ccns, state=a.state)
        n81 = int(part["worksheet"].astype(str).str.startswith("A81").sum())
        print(f"  {src.name}: {len(rpt):,} cost reports, {len(part):,} cells kept, {n81:,} on A-8-1")
        if n81 == 0:
            census = worksheet_census(cells)
            a_codes = census[census.index.str.startswith("A")]
            print(f"    no A-8-1 cells; worksheet codes starting with A in this file: "
                  f"{', '.join(f'{k}={v:,}' for k, v in a_codes.items())}")
        frames.append(part)
    out = pd.concat(frames, ignore_index=True) if frames else related_party_rows(
        pd.DataFrame(columns=RPT_COLS), pd.DataFrame(columns=CELL_COLS + ["kind"]))
    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    summ = a81_summary(out)
    summ_path = out_path.with_name(out_path.stem + "_summary.csv")
    summ.to_csv(summ_path, index=False)
    print(f"hcris related-party: {out['ccn'].nunique():,} CCNs, {len(out):,} cells -> {out_path}")
    print(f"  A-8-1 summary: {len(summ):,} lines -> {summ_path}")


if __name__ == "__main__":
    main()
