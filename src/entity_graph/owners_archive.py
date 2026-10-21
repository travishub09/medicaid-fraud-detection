"""
owners_archive.py — build our own dated archive of the CMS All-Owners editions
(2026-10-21, docs/FREEZE_DESIGN_NOTE_2026-10-21.md rule 2: "build our own archive
from now on").

CMS publishes only the latest All-Owners state, so a future freeze can diff
ownership only if WE kept the earlier editions. ``preclean/owners/`` holds the
current download; ``preclean/owners_prior/`` is the archive the frozen churn chain
reads (``owners_vintage.load_editions``: files named ``<DATASET>_All_Owners_<date>``,
grouped per dataset, chained by edition date). This module copies every
All-Owners file in ``owners/`` into the archive under that naming, dated by the
filename when it carries a date, else by the file's modification month, and skips
a file whose dataset + edition (or identical content) is already archived, so the
monthly VM run can call it unconditionally. Read-only on ``owners/``; nothing is
ever deleted from the archive.

  python -m src.entity_graph.owners_archive --data-root /data/medicaid [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import re
import shutil
from pathlib import Path

from src.attempt_2.clean_data import _resolve_columns, read_csv_text

from .owners_vintage import OWNER_VINTAGE_COLS, _dataset_of, _edition_date


def _is_all_owners(p: Path) -> bool:
    try:
        head = read_csv_text(p, nrows=2)
    except Exception:
        return False
    return len(_resolve_columns(list(head.columns), OWNER_VINTAGE_COLS)) == 2


def _digest(p: Path) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _archive_name(p: Path) -> tuple[str, str, str]:
    """(dataset, edition YYYY-MM-DD, archived file name)."""
    ds = _dataset_of(p.name)
    ed = _edition_date(p.name)
    if not ed:
        ed = _dt.date.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
    elif len(ed) == 7:
        ed += "-01"
    return ds, ed, f"{ds}_All_Owners_{ed.replace('-', '.')}.csv"


def archive_editions(data_root: Path, dry_run: bool = False) -> list[dict]:
    """Copy the current All-Owners files into owners_prior/ under dated names.
    Returns one row per file in owners/: {file, dataset, edition, action}."""
    cur = data_root / "preclean" / "owners"
    arc = data_root / "preclean" / "owners_prior"
    rows: list[dict] = []
    if not cur.is_dir():
        return rows
    arc.mkdir(parents=True, exist_ok=True)
    existing = {q.name: q for q in arc.glob("*.csv")}
    digests: dict[str, str] = {}
    for p in sorted(cur.glob("*.csv")):
        if not _is_all_owners(p):
            rows.append({"file": p.name, "dataset": "", "edition": "", "action": "not_all_owners"})
            continue
        ds, ed, name = _archive_name(p)
        row = {"file": p.name, "dataset": ds, "edition": ed, "action": ""}
        if name in existing:
            row["action"] = "already_archived"
        else:
            same_ds = [q for n, q in existing.items() if re.match(rf"{re.escape(ds)}_All_Owners_", n, re.I)]
            d = _digest(p)
            dup = next((q for q in same_ds if digests.setdefault(str(q), _digest(q)) == d), None)
            if dup is not None:
                row["action"] = f"identical_to {dup.name}"
            elif dry_run:
                row["action"] = f"would_copy -> {name}"
            else:
                shutil.copy2(p, arc / name)
                existing[name] = arc / name
                row["action"] = f"copied -> {name}"
        rows.append(row)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    rows = archive_editions(Path(a.data_root), a.dry_run)
    if not rows:
        print("[owners_archive] no preclean/owners/*.csv to archive")
        return
    for r in rows:
        print(f"[owners_archive] {r['file']}: {r['dataset'] or '-'} {r['edition'] or '-'} {r['action']}")
    n = sum(1 for r in rows if r["action"].startswith("copied"))
    print(f"[owners_archive] {n} edition(s) added to owners_prior/")


if __name__ == "__main__":
    main()
