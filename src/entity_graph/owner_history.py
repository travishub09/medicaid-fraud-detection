"""
owner_history.py — when did CMS first list this owner at this facility? (2026-11-03)

The analyst's Ohio read: a person signed the December 2021 financing as authorized
signatory for every landlord and every operator, yet the CMS enrollment record lists
him at one home only from December 2025. 42 CFR 424.516(e) gives a provider 30 days
to report a change in ownership or managing control, so a gap between the recorded
documents and the enrollment record is a concrete, checkable fact. The operator's
owners/ download plus the owners_prior/ archive (every All-Owners edition since 2023,
`entity_graph.owners_archive`) answer it from our own files: per facility and owner,
the FIRST and LAST edition in which the row appears, the role, the association date
CMS carries, and whether the row is still present in the newest edition.

    python -m src.entity_graph.owner_history --data-root /data/medicaid \
        --org "BUCKEYE FOREST|WESLEYAN VILLAGE" [--owner "LAHASKY|STEIN"] --out OWNER_HISTORY.md

A match is a regex over the facility's ORGANIZATION NAME (--org) and over the owner's
LAST NAME / ORGANIZATION NAME - OWNER (--owner); either alone works. All DuckDB; the
editions never enter pandas whole. Leads for human review, never accusations: an
association date is the enrollment record's, not the deal's.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.attempt_2.duck import connect
from .owners_vintage import _dataset_of, _edition_date, is_empty_edition

COLS = ["edition", "dataset", "facility_enrollment_id", "facility_name", "owner_pac_id",
        "owner_type", "owner_role", "association_date", "owner_name", "owner_state", "pct_ownership"]
_SRC = {
    "facility_enrollment_id": "ENROLLMENT ID", "facility_name": "ORGANIZATION NAME",
    "owner_pac_id": "ASSOCIATE ID - OWNER", "owner_type": "TYPE - OWNER", "owner_role": "ROLE TEXT - OWNER",
    "association_date": "ASSOCIATION DATE - OWNER", "owner_first": "FIRST NAME - OWNER",
    "owner_last": "LAST NAME - OWNER", "owner_org": "ORGANIZATION NAME - OWNER",
    "owner_state": "STATE - OWNER", "pct_ownership": "PERCENTAGE OWNERSHIP",
}


def edition_files(data_root: Path) -> list[tuple[str, str, Path]]:
    """Every All-Owners csv under owners/ and owners_prior/ as (edition, dataset, path);
    an undated current file is tagged 'current'. Empty files are skipped."""
    out = []
    for folder, tag in ((data_root / "preclean" / "owners_prior", ""), (data_root / "preclean" / "owners", "current")):
        if not folder.is_dir():
            continue
        for p in sorted(folder.glob("*.csv")):
            if is_empty_edition(p):
                continue
            ed = _edition_date(p.name) or tag
            if not ed:
                continue
            out.append((ed, _dataset_of(p.name), p))
    return out


def _q(s: str) -> str:
    return s.replace("'", "''")


def owner_rows(data_root: Path, org: str = "", owner: str = "", con=None,
               datasets: list[str] | None = None) -> pd.DataFrame:
    """One row per (edition, facility, owner) matching the regexes, over every edition
    (``datasets`` limits the families read: FACILITY = the SNF editions)."""
    files = edition_files(Path(data_root))
    if not files or not (org or owner):
        return pd.DataFrame(columns=COLS)
    own = con is None
    con = con or connect()
    frames = []
    try:
        for ed, ds, p in files:
            if datasets and ds.upper() not in {d.upper() for d in datasets}:
                continue
            # the shared raw-CSV convention: UTF-8 first, Latin-1 fallback, and a file the
            # sniffer refuses in strict mode (the 2026-11-04 crash on the Hospital edition)
            # is read tolerant; one that fails both ways is skipped BY NAME, never fatal
            read = ""
            for enc in ("utf-8", "latin-1"):
                try:
                    opts = f"all_varchar=true, header=true, ignore_errors=true, strict_mode=false, encoding='{enc}'"
                    cols = [r[0] for r in con.execute(
                        f"DESCRIBE SELECT * FROM read_csv_auto('{_q(str(p))}', {opts})").fetchall()]
                    read = opts
                    break
                except Exception as e:      # noqa: BLE001
                    last = e
            if not read:
                print(f"[owner_history] skipped {p.name}: unreadable ({str(last)[:120]})")
                continue
            if not {"ORGANIZATION NAME", "ASSOCIATE ID - OWNER"} <= set(cols):
                continue
            sel = []
            for k, src in _SRC.items():
                sel.append(f'"{src}" AS {k}' if src in cols else f"'' AS {k}")
            where = []
            if org:
                where.append(f"regexp_matches(upper(\"ORGANIZATION NAME\"), '{_q(org.upper())}')")
            if owner:
                parts = [f"regexp_matches(upper(COALESCE(\"{c}\", '')), '{_q(owner.upper())}')"
                         for c in ("LAST NAME - OWNER", "ORGANIZATION NAME - OWNER") if c in cols]
                if parts:
                    where.append("(" + " OR ".join(parts) + ")")
            f = con.execute(
                f"SELECT '{_q(ed)}' AS edition, '{_q(ds)}' AS dataset, {', '.join(sel)} "
                f"FROM read_csv_auto('{_q(str(p))}', {read}) "
                + ("WHERE " + " AND ".join(where) if where else "")).df()
            frames.append(f)
    finally:
        if own:
            con.close()
    if not frames:
        return pd.DataFrame(columns=COLS)
    df = pd.concat(frames, ignore_index=True)
    df["owner_name"] = (df["owner_org"].fillna("").str.strip().where(
        df["owner_org"].fillna("").str.strip() != "",
        (df["owner_last"].fillna("") + ", " + df["owner_first"].fillna("")).str.strip(", ")))
    return df[COLS].drop_duplicates().reset_index(drop=True)


def owner_timeline(rows: pd.DataFrame) -> pd.DataFrame:
    """Per (facility, owner, role): first / last edition the row appears in, the association
    date CMS carries, and whether it is in the newest edition (an owner who dropped out)."""
    cols = ["facility_name", "facility_enrollment_id", "owner_name", "owner_type", "owner_role",
            "association_date", "first_edition", "last_edition", "n_editions", "in_newest"]
    if rows is None or not len(rows):
        return pd.DataFrame(columns=cols)
    newest = max(rows["edition"].replace("current", "9999"))
    g = (rows.groupby(["facility_name", "facility_enrollment_id", "owner_name", "owner_type", "owner_role"], as_index=False)
             .agg(association_date=("association_date", lambda s: min(x for x in s if x) if any(s) else ""),
                  first_edition=("edition", lambda s: min(s.replace("current", "9999"))),
                  last_edition=("edition", lambda s: max(s.replace("current", "9999"))),
                  n_editions=("edition", "nunique")))
    g["in_newest"] = g["last_edition"].eq(newest)
    for c in ("first_edition", "last_edition"):
        g[c] = g[c].replace("9999", "current")
    return g.sort_values(["facility_name", "first_edition", "owner_name"])[cols].reset_index(drop=True)


def to_markdown(tl: pd.DataFrame, editions: list[tuple[str, str, Path]], org: str, owner: str) -> str:
    L = ["# OWNER HISTORY: when the CMS enrollment record first listed each owner", "",
         "_From the operator's All-Owners download and the owners_prior archive. `first_edition` is the "
         "first edition in which the owner row appears; `association_date` is what CMS carries. A gap "
         "between a recorded document and `first_edition` is a question under 42 CFR 424.516(e) (30 days "
         "to report a change in ownership or managing control), never a finding; an association date is "
         "the enrollment record's, not the deal's; a signatory on a financing is not necessarily an owner._", "",
         f"Filters: org `{org or '(any)'}`, owner `{owner or '(any)'}`. Editions read: "
         + ", ".join(sorted({f'{ds} {ed}' for ed, ds, _ in editions})), ""]
    if not len(tl):
        L.append("_No matching owner rows in any edition._")
        return "\n".join(L) + "\n"
    L += ["| facility | enrollment id | owner | type | role | association date | first edition | last edition | editions | in newest |",
          "|---|---|---|---|---|---|---|---|--:|---|"]
    for r in tl.itertuples():
        L.append(f"| {r.facility_name} | {r.facility_enrollment_id} | {r.owner_name} | {r.owner_type} | {r.owner_role} | "
                 f"{r.association_date} | {r.first_edition} | {r.last_edition} | {r.n_editions} | {'yes' if r.in_newest else 'NO'} |")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--org", default="", help="regex over the facility ORGANIZATION NAME (case-insensitive)")
    ap.add_argument("--owner", default="", help="regex over the owner's LAST NAME / ORGANIZATION NAME - OWNER")
    ap.add_argument("--out", default="", help="Markdown path (default: print)")
    ap.add_argument("--datasets", default="", help="comma list of families to read (FACILITY = SNF, HOSPITAL, AGENCY, HOSPICE, FQHC, CLINIC); default all")
    a = ap.parse_args()
    root = Path(a.data_root)
    ds = [d.strip() for d in a.datasets.split(",") if d.strip()] or None
    eds = [e for e in edition_files(root) if not ds or e[1].upper() in {d.upper() for d in ds}]
    rows = owner_rows(root, a.org, a.owner, datasets=ds)
    tl = owner_timeline(rows)
    md = to_markdown(tl, eds, a.org, a.owner)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
        Path(a.out).with_suffix(".csv").write_text(tl.to_csv(index=False), encoding="utf-8")
        print(f"[owner_history] {len(tl):,} owner rows over {len(eds)} edition file(s) -> {a.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
