"""
owners_intake.py — file a Downloads folder full of All-Owners zips into the archive.

The operator bulk-downloads CMS All-Owners editions (Hospital/SNF/HHA/Hospice/
FQHC/RHC) as zips named either ``Hospital_All_Owners_Sep_2023.zip`` or
``RHC_All_Owners_Q4_2023.zip`` (plus browser ``(1)`` duplicates and the odd
0-byte failed download). This tool:

  1. finds every ``*All_Owners*.zip`` in the downloads folder,
  2. parses the dataset family + edition date from the name (month names and
     quarters both; quarters map to their end month, Q4 -> 12),
  3. extracts the largest inner .csv,
  4. writes it as ``{FAMILY}_All_Owners_{YYYY-MM}.csv`` in owners_prior/ —
     the exact shape ``owners_vintage.load_editions`` parses,
  5. skips duplicates (same family+date), never overwrites an existing
     edition, and reports empty/corrupt zips by name.

  python -m src.owners_intake --downloads "%USERPROFILE%\\Downloads" ^
      --dest "C:/Users/treyr/OneDrive/Desktop/data/preclean/owners_prior"
"""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path

NAME_RE = re.compile(
    r"(?P<fam>[A-Za-z]+)[_ ]?All[_ ]Owners[_ ]"
    r"(?:(?P<mon>[A-Za-z]+)|Q(?P<q>[1-4]))[_ ](?P<yr>20\d{2})"
    r"(?:\s*\(\d+\))?\.zip$", re.IGNORECASE)

FAMILIES = {"hospital": "HOSPITAL", "snf": "SNF", "hha": "HHA",
            "hospice": "HOSPICE", "fqhc": "FQHC", "rhc": "RHC"}
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
QUARTER_END = {1: 3, 2: 6, 3: 9, 4: 12}


def parse_name(name: str) -> tuple[str, str] | None:
    """'Hospital_All_Owners_Sep_2023.zip' -> ('HOSPITAL', '2023-09');
    'RHC_All_Owners_Q4_2023 (1).zip' -> ('RHC', '2023-12'); None if not ours."""
    m = NAME_RE.search(name)
    if not m:
        return None
    fam = FAMILIES.get(m.group("fam").lower())
    if not fam:
        return None
    if m.group("q"):
        month = QUARTER_END[int(m.group("q"))]
    else:
        month = MONTHS.get(m.group("mon").lower()[:3])
        if not month:
            return None
    return fam, f"{m.group('yr')}-{month:02d}"


def intake(downloads: Path, dest: Path) -> dict:
    """File every All-Owners zip. Returns a summary dict; prints as it goes."""
    dest.mkdir(parents=True, exist_ok=True)
    done: set[tuple[str, str]] = set()
    out = {"filed": [], "skipped_existing": [], "skipped_duplicate": [],
           "failed": []}
    # originals win dedupe: sort by the name with the ' (n)' marker stripped,
    # marker-free files first (a bare sort puts ' (1)' BEFORE '.zip')
    def _key(p: Path):
        return (re.sub(r"\s*\(\d+\)", "", p.name).lower(),
                1 if "(" in p.name else 0)
    for z in sorted(downloads.glob("*.zip"), key=_key):
        parsed = parse_name(z.name)
        if not parsed:
            continue
        fam, ym = parsed
        target = dest / f"{fam}_All_Owners_{ym}.csv"
        if (fam, ym) in done:
            out["skipped_duplicate"].append(z.name)
            continue
        if target.exists():
            done.add((fam, ym))
            out["skipped_existing"].append(target.name)
            continue
        try:
            if z.stat().st_size == 0:
                raise zipfile.BadZipFile("0-byte file (failed download)")
            with zipfile.ZipFile(z) as zf:
                members = [i for i in zf.infolist()
                           if i.filename.lower().endswith(".csv")]
                if not members:
                    raise zipfile.BadZipFile("no csv inside")
                inner = max(members, key=lambda i: i.file_size)
                with zf.open(inner) as src, open(target, "wb") as dst:
                    while chunk := src.read(1 << 20):
                        dst.write(chunk)
        except Exception as e:
            out["failed"].append(f"{z.name}: {e}")
            continue
        done.add((fam, ym))
        out["filed"].append(target.name)
        print(f"[owners_intake] {z.name} -> {target.name}")
    print(f"[owners_intake] filed {len(out['filed'])}; already had "
          f"{len(out['skipped_existing'])}; duplicate zips skipped "
          f"{len(out['skipped_duplicate'])}; FAILED {len(out['failed'])}")
    for f in out["failed"]:
        print(f"[owners_intake]   FAILED {f}  — re-download this one")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--downloads", required=True)
    ap.add_argument("--dest", required=True)
    args = ap.parse_args()
    intake(Path(args.downloads), Path(args.dest))


if __name__ == "__main__":
    main()
