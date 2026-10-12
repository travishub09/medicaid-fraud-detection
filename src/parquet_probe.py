"""parquet_probe: answer "is this column alive?" and "what values does it carry?" on any
Parquet file WITHOUT loading it into pandas (DuckDB streams the file).

    python -m src.parquet_probe --file <matrix.parquet> --pattern "deact|after_death|invalid_identity"
        -> per matching column: rows, non-null, > 0, distinct
    python -m src.parquet_probe --file <segments.parquet> --values status reason --top 40
        -> the top values of each named column with counts (the enrollment "0 ended for cause"
           question of 2026-10-12: which end statuses / reasons the file really carries)

Read-only. Prints a Markdown table so the output can be pasted into chat as-is.
"""
from __future__ import annotations

import argparse
import re

from src.attempt_2.duck import connect as _duck_connect


def _pq(p: str) -> str:
    return str(p).replace("\\", "/").replace("'", "''")


def column_names(file: str) -> list[str]:
    con = _duck_connect()
    rows = con.execute(f"describe select * from read_parquet('{_pq(file)}')").fetchall()
    return [r[0] for r in rows]


def probe_columns(file: str, pattern: str) -> list[dict]:
    """rows / non-null / positive / distinct for every column whose name matches."""
    rx = re.compile(pattern, re.I)
    cols = [c for c in column_names(file) if rx.search(c)]
    if not cols:
        return []
    con = _duck_connect()
    q = lambda c: '"' + c.replace('"', '""') + '"'
    parts = ["count(*) as n_rows"]
    for c in cols:
        parts.append(f"count({q(c)}) as nn__{len(parts)}")
        parts.append(f"sum(case when try_cast({q(c)} as double) > 0 then 1 else 0 end) as pos__{len(parts)}")
        parts.append(f"count(distinct {q(c)}) as nd__{len(parts)}")
    row = con.execute(f"select {', '.join(parts)} from read_parquet('{_pq(file)}')").fetchone()
    out, i = [], 1
    for c in cols:
        out.append({"column": c, "rows": int(row[0]), "non_null": int(row[i] or 0),
                    "positive": int(row[i + 1] or 0), "distinct": int(row[i + 2] or 0)})
        i += 3
    return out


def value_counts(file: str, column: str, top: int = 30) -> list[tuple[str, int]]:
    con = _duck_connect()
    q = '"' + column.replace('"', '""') + '"'
    rows = con.execute(f"select coalesce(cast({q} as varchar), '<null>') v, count(*) n "
                       f"from read_parquet('{_pq(file)}') group by 1 order by n desc limit {int(top)}").fetchall()
    return [(str(v), int(n)) for v, n in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True)
    ap.add_argument("--pattern", default=None, help="regex over column names: liveness table")
    ap.add_argument("--values", nargs="*", default=None, help="columns whose top values to list")
    ap.add_argument("--top", type=int, default=30)
    a = ap.parse_args()
    if a.pattern:
        rows = probe_columns(a.file, a.pattern)
        print(f"columns matching /{a.pattern}/ in {a.file}: {len(rows)}")
        print("| column | rows | non-null | > 0 | distinct |\n|---|--:|--:|--:|--:|")
        for r in rows:
            print(f"| {r['column']} | {r['rows']:,} | {r['non_null']:,} | {r['positive']:,} | {r['distinct']:,} |")
    for c in (a.values or []):
        print(f"\ntop {a.top} values of {c}:")
        print("| value | n |\n|---|--:|")
        for v, n in value_counts(a.file, c, a.top):
            print(f"| {v} | {n:,} |")


if __name__ == "__main__":
    main()
