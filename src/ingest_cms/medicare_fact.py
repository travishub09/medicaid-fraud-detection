"""
medicare_fact.py — Medicare Part B + Part D PUFs → an annual billing fact
(Run 2 §B / docs/MEDICARE_BATCH_PLAN.md).

The Medicaid pipeline's fact is NPI × HCPCS × MONTH. The Medicare PUFs are
ANNUAL (one vintage per year, no month grain), so the Medicare fact is
NPI × code × YEAR, with the year taken from each file's name:

    preclean/partb/partb_2019.csv … partb_2024.csv   (by Provider & Service)
    preclean/partd/partd_2019.csv … partd_2024.csv   (by Provider & Drug)

Outputs (written by the CLI, consumed by medicare_growth + the export later):
  medicare_fact.parquet            npi · code · year · source (partb|partd) ·
                                   units (services|claims) · dollars
                                   (Part B allowed dollars = avg_allowed ×
                                   services; Part D total drug cost)
  medicare_provider_stats.parquet  per-NPI: years_active, first/last year,
                                   total_dollars, n_distinct_codes

Discipline: dollar conservation asserted per file (fact total == input total
to the cent); NPIs that fail Luhn are counted as quarantined; identifiers stay
strings (hard rule #1). Files are processed one at a time so a 6-vintage load
never holds more than one raw file in memory.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns, canonicalize_series, read_csv_text
from src.ingest_cms.partb import PARTB_COLS
from src.ingest_cms.partd import PARTD_COLS

_YEAR_RE = re.compile(r"(20\d{2})")

# Part B payment column (not in PARTB_COLS — the per-NPI adapter doesn't need it)
_PARTB_PAY = {"avg_payment": ["Avg_Mdcr_Pymt_Amt", "AVG_MDCR_PYMT_AMT",
                              "average_Medicare_payment_amt"]}


def _year_of(path: Path) -> int:
    m = _YEAR_RE.search(path.stem)
    if not m:
        raise ValueError(f"cannot infer year from file name: {path.name} "
                         "(expected e.g. partb_2023.csv)")
    return int(m.group(1))


def partb_year_fact(raw: pd.DataFrame, year: int) -> tuple[pd.DataFrame, int]:
    """One Part B vintage → (npi, code, year, units, dollars). Dollars =
    allowed × services (allowed is the program-size measure; payment is a
    subset of allowed). Returns (fact, n_quarantined)."""
    resolved = _resolve_columns(list(raw.columns), PARTB_COLS)
    missing = [c for c in ("npi", "hcpcs", "services") if c not in resolved]
    if missing:
        raise ValueError(f"Part B file missing {missing}; saw {list(raw.columns)[:10]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()})
    npi = canonicalize_series(df["npi"])
    quarantined = int((npi.isna()
                       & df["npi"].fillna("").astype(str).str.strip().ne("")).sum())
    df = df.assign(npi=npi)[npi.notna()].copy()
    df["code"] = df["hcpcs"].fillna("").astype(str).str.strip().str.upper()
    df["units"] = pd.to_numeric(df["services"], errors="coerce").fillna(0.0)
    avg_allowed = pd.to_numeric(df.get("avg_allowed"), errors="coerce").fillna(0.0)
    df["dollars"] = avg_allowed * df["units"]
    out = (df.groupby(["npi", "code"], as_index=False)[["units", "dollars"]].sum())
    out["year"] = year
    out["source"] = "partb"
    total_in = float((avg_allowed * df["units"]).sum())
    total_out = float(out["dollars"].sum())
    assert abs(total_in - total_out) < 0.01, \
        f"Part B {year}: dollar conservation broke ({total_in} vs {total_out})"
    return out[["npi", "code", "year", "source", "units", "dollars"]], quarantined


def partd_year_fact(raw: pd.DataFrame, year: int) -> tuple[pd.DataFrame, int]:
    """One Part D vintage → (npi, code, year, units, dollars). Code = generic
    name (stable across brand renames); dollars = total drug cost."""
    resolved = _resolve_columns(list(raw.columns), PARTD_COLS)
    missing = [c for c in ("npi", "claims", "cost") if c not in resolved]
    if missing:
        raise ValueError(f"Part D file missing {missing}; saw {list(raw.columns)[:10]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()})
    npi = canonicalize_series(df["npi"])
    quarantined = int((npi.isna()
                       & df["npi"].fillna("").astype(str).str.strip().ne("")).sum())
    df = df.assign(npi=npi)[npi.notna()].copy()
    generic = df.get("generic_name", pd.Series("", index=df.index))
    brand = df.get("brand_name", pd.Series("", index=df.index))
    df["code"] = (generic.fillna("").astype(str).str.strip().str.upper()
                  .where(lambda s: s != "",
                         brand.fillna("").astype(str).str.strip().str.upper()))
    df["units"] = pd.to_numeric(df["claims"], errors="coerce").fillna(0.0)
    df["dollars"] = pd.to_numeric(df["cost"], errors="coerce").fillna(0.0)
    total_in = float(df["dollars"].sum())
    out = (df.groupby(["npi", "code"], as_index=False)[["units", "dollars"]].sum())
    out["year"] = year
    out["source"] = "partd"
    assert abs(total_in - float(out["dollars"].sum())) < 0.01, \
        f"Part D {year}: dollar conservation broke"
    return out[["npi", "code", "year", "source", "units", "dollars"]], quarantined


def provider_stats(fact: pd.DataFrame) -> pd.DataFrame:
    """Per-NPI stats over the multi-year fact (the Medicare analogue of the
    Medicaid provider base): years_active, first/last year, totals, breadth."""
    g = fact.groupby("npi")
    out = pd.DataFrame({
        "years_active": g["year"].nunique(),
        "first_year": g["year"].min(),
        "last_year": g["year"].max(),
        "total_units": g["units"].sum(),
        "total_dollars": g["dollars"].sum(),
        "n_distinct_codes": g["code"].nunique(),
    }).reset_index()
    return out


def build_fact(partb_dir: Path | None, partd_dir: Path | None
               ) -> tuple[pd.DataFrame, dict]:
    """All vintages in the two folders → one fact + an audit dict
    (per-file rows/dollars/quarantined — feeds the SOURCES_REPORT pattern)."""
    parts: list[pd.DataFrame] = []
    audit: dict[str, dict] = {}
    for d, builder, tag in ((partb_dir, partb_year_fact, "partb"),
                            (partd_dir, partd_year_fact, "partd")):
        if d is None or not Path(d).exists():
            continue
        files = sorted(Path(d).glob(f"{tag}_*.csv")) or sorted(Path(d).glob("*.csv"))
        for p in files:
            year = _year_of(p)
            fact, quarantined = builder(read_csv_text(p), year)
            parts.append(fact)
            audit[p.name] = {"rows": int(len(fact)),
                             "dollars": float(fact["dollars"].sum()),
                             "quarantined_npis": quarantined}
    if not parts:
        raise FileNotFoundError("no Part B/D vintage files found "
                                "(expected partb_<year>.csv / partd_<year>.csv)")
    fact = pd.concat(parts, ignore_index=True)
    fact["npi"] = fact["npi"].astype(str)
    return fact, audit


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--partb-dir", default=None, help="folder of partb_<year>.csv")
    ap.add_argument("--partd-dir", default=None, help="folder of partd_<year>.csv")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fact, audit = build_fact(args.partb_dir and Path(args.partb_dir),
                             args.partd_dir and Path(args.partd_dir))
    fact.to_parquet(out_dir / "medicare_fact.parquet", index=False)
    provider_stats(fact).to_parquet(out_dir / "medicare_provider_stats.parquet",
                                    index=False)
    for name, a in audit.items():
        print(f"[medicare_fact] {name}: {a['rows']:,} rows, "
              f"${a['dollars']:,.0f}, {a['quarantined_npis']} quarantined NPIs")
    print(f"wrote {out_dir / 'medicare_fact.parquet'} ({len(fact):,} rows) "
          f"+ medicare_provider_stats.parquet")


if __name__ == "__main__":
    main()
