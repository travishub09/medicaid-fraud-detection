"""list_case_overlap: how many organizations in the top N of a priority list carry a government
case TODAY, by kind (the operator's question before the 2026-10-29 Brad call: 'how many of the top
250 have open False Claims Act cases').

Reads a run matrix (provider_features_for_model.parquet: npi, org_node_id, exclusion_label_sources)
and a priority list (org_id, <rank column>), joins members to organizations, and counts the top-N
organizations with at least one member tagged:

  doj_case      a RESOLVED government case (settlement, judgment, conviction, plea)
  charged_case  an indictment or a government complaint with no outcome yet (open)
  docket_case   a whistleblower (qui tam) docket on the public record with no outcome yet (open)
  any_case      any of the three

The matrix should be the CURRENT-DAY run (its tags carry everything known today); the list may be
any run's (a frozen run's list against today's tags says how many of its picks the government has
since acted on, resolved or pending, but a case known at that run's freeze was a training positive
there, so the forward grade in ACCURACY_DIAGNOSTICS stays the honest number for 'predicted').
Writes a CSV of the matched organizations with the tags, and prints the counts. DuckDB throughout.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd

TAGS = ("doj_case", "charged_case", "docket_case")


def overlap(matrix: Path, listing: Path, top: int = 250, rank_col: str | None = None) -> tuple[pd.DataFrame, dict]:
    con = duckdb.connect()
    lcols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_csv_auto('{listing}', all_varchar=true)").fetchall()]
    rank_col = rank_col or next((c for c in ("pursuit_rank", "pmax_rank", "large_rank", "priority_rank", "rank") if c in lcols), None)
    if rank_col is None:
        raise SystemExit(f"no rank column in {listing}: {lcols}")
    name_col = next((c for c in ("org_name", "name") if c in lcols), None)
    con.execute(f"""
        CREATE TEMP TABLE lst AS
        SELECT CAST(org_id AS VARCHAR) AS org_id, CAST({rank_col} AS INTEGER) AS rnk,
               {('CAST(' + name_col + ' AS VARCHAR)') if name_col else "''"} AS org_name
        FROM read_csv_auto('{listing}', all_varchar=true)
        WHERE TRY_CAST({rank_col} AS INTEGER) <= {int(top)}""")
    tag_sql = ", ".join(f"MAX(CASE WHEN src LIKE '%{t}%' THEN 1 ELSE 0 END) AS {t}" for t in TAGS)
    df = con.execute(f"""
        SELECT l.rnk AS rank, l.org_id, l.org_name, m.n_members, {', '.join('COALESCE(m.' + t + ', 0) AS ' + t for t in TAGS)},
               CASE WHEN {' OR '.join('COALESCE(m.' + t + ', 0) = 1' for t in TAGS)} THEN 1 ELSE 0 END AS any_case
        FROM lst l
        LEFT JOIN (
            SELECT CAST(org_node_id AS VARCHAR) AS org_id, COUNT(*) AS n_members, {tag_sql}
            FROM (SELECT org_node_id, COALESCE(CAST(exclusion_label_sources AS VARCHAR), '') AS src
                  FROM read_parquet('{matrix}'))
            GROUP BY 1) m USING (org_id)
        ORDER BY l.rnk""").df()
    n = len(df)
    counts = {"top": int(top), "listed": n}
    for t in TAGS + ("any_case",):
        counts[t] = int(df[t].sum())
    counts["open_only"] = int((((df["charged_case"] == 1) | (df["docket_case"] == 1)) & (df["doj_case"] == 0)).sum()) if n else 0
    return df, counts


def name_overlap(listing: Path, case_db: Path, top: int = 250, rank_col: str | None = None) -> tuple[pd.DataFrame, dict]:
    """The second pass: every top-N organization NAME matched exactly (the shared norm_org_name
    key) against the full case database's defendant_name_key, independent of NPI resolution.
    Each matched case row is classed: open_docket (an unsealed whistleblower docket, no
    outcome), charged (an indictment or a government complaint, no outcome), or resolved
    (a settlement / judgment / conviction / plea). Duplicate settlement rows are skipped."""
    from src.entity_graph.resolve_entities import norm_org_name
    from src.model_a.case_labels import charged_outcome
    con = duckdb.connect()
    lcols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_csv_auto('{listing}', all_varchar=true)").fetchall()]
    rank_col = rank_col or next((c for c in ("pursuit_rank", "pmax_rank", "large_rank", "priority_rank", "rank") if c in lcols), None)
    name_col = next((c for c in ("org_name", "name") if c in lcols), None)
    if rank_col is None or name_col is None:
        raise SystemExit(f"need a rank and a name column in {listing}: {lcols}")
    lst = con.execute(f"""SELECT CAST(org_id AS VARCHAR) AS org_id, CAST({rank_col} AS INTEGER) AS rank,
                                 CAST({name_col} AS VARCHAR) AS org_name
                          FROM read_csv_auto('{listing}', all_varchar=true)
                          WHERE TRY_CAST({rank_col} AS INTEGER) <= {int(top)} ORDER BY 1""").df()
    lst["name_key"] = lst["org_name"].fillna("").map(norm_org_name)
    cases = pd.read_csv(case_db, dtype=str, keep_default_na=False)
    if "defendant_name_key" not in cases.columns:
        cases["defendant_name_key"] = cases.get("defendant_name", "").map(norm_org_name)
    if "duplicate_of" in cases.columns:
        cases = cases[cases["duplicate_of"].fillna("").astype(str) == ""]
    if "usable" in cases.columns:
        cases = cases[cases["usable"].astype(str).str.strip().isin(("", "1", "1.0", "True", "true"))]
    cases = cases[cases["defendant_name_key"].astype(str).str.strip() != ""]

    def _kind(r):
        ct = str(r.get("case_type") or "").lower()
        if ct.startswith("docket"):
            return "open_docket"
        if charged_outcome(ct, str(r.get("summary") or "")):
            return "charged"
        return "resolved"
    cases = cases.assign(kind=[_kind(r) for _, r in cases.iterrows()])
    m = lst.merge(cases[["defendant_name_key", "case_id", "announced_date", "case_type", "kind", "amount_usd", "jurisdiction"]],
                  left_on="name_key", right_on="defendant_name_key", how="inner")
    rows = []
    for (oid, rk, nm), g in m.groupby(["org_id", "rank", "org_name"], sort=False):
        rows.append({"rank": rk, "org_id": oid, "org_name": nm, "n_cases": len(g),
                     "resolved": int((g["kind"] == "resolved").sum()), "charged": int((g["kind"] == "charged").sum()),
                     "open_docket": int((g["kind"] == "open_docket").sum()),
                     "latest": str(g["announced_date"].max()), "case_types": ";".join(sorted(set(g["case_type"].astype(str)))),
                     "case_ids": ";".join(g["case_id"].astype(str).head(5))})
    out = pd.DataFrame(rows, columns=["rank", "org_id", "org_name", "n_cases", "resolved", "charged", "open_docket",
                                      "latest", "case_types", "case_ids"]).sort_values("rank") if rows else \
        pd.DataFrame(columns=["rank", "org_id", "org_name", "n_cases", "resolved", "charged", "open_docket", "latest", "case_types", "case_ids"])
    counts = {"top": int(top), "listed": int(len(lst)), "orgs_with_any": int(len(out)),
              "orgs_resolved": int((out["resolved"] > 0).sum()) if len(out) else 0,
              "orgs_charged": int((out["charged"] > 0).sum()) if len(out) else 0,
              "orgs_open_docket": int((out["open_docket"] > 0).sum()) if len(out) else 0}
    return out, counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", required=True, help="the CURRENT-DAY run's provider_features_for_model.parquet")
    ap.add_argument("--list", required=True, dest="listing", help="a provider_priority_list*.csv (any run)")
    ap.add_argument("--top", type=int, default=250)
    ap.add_argument("--rank-col", default=None)
    ap.add_argument("--out", required=True, help="CSV of the top-N organizations with their case tags")
    ap.add_argument("--case-db", default=None, help="the merged case database csv: adds the NAME-match pass "
                                                   "(every top-N name against every defendant, independent of NPI resolution)")
    a = ap.parse_args()
    df, c = overlap(Path(a.matrix), Path(a.listing), top=a.top, rank_col=a.rank_col)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(a.out, index=False)
    # the names in rank order, one per line, for `first_to_file --names-file` (the public-docket pass)
    names_out = Path(a.out).with_name(Path(a.out).stem + "_names.txt")
    names = [n for n in df.sort_values("rank")["org_name"].astype(str).str.strip().tolist() if n]
    names_out.write_text("\n".join(dict.fromkeys(names)) + ("\n" if names else ""), encoding="utf-8")
    print(f"[list_case_overlap] top {c['top']} of {a.listing}: {c['listed']} organizations listed; "
          f"resolved government case {c['doj_case']}; open indictment or government complaint {c['charged_case']}; "
          f"open whistleblower docket {c['docket_case']}; any case {c['any_case']} "
          f"({c['any_case'] / max(c['listed'], 1) * 100:.1f}%); open with no resolution yet {c['open_only']}")
    hit = df[df["any_case"] == 1]
    for r in hit.itertuples():
        tags = ",".join(t for t in TAGS if getattr(r, t) == 1)
        print(f"  {r.rank:>4}  {str(r.org_name)[:50]:<50}  {tags}")
    print(f"[list_case_overlap] -> {a.out}")
    if a.case_db:
        nm, nc = name_overlap(Path(a.listing), Path(a.case_db), top=a.top, rank_col=a.rank_col)
        out2 = Path(a.out).with_name(Path(a.out).stem + "_by_name.csv")
        nm.to_csv(out2, index=False)
        print(f"[list_case_overlap] NAME MATCH against {a.case_db}: {nc['orgs_with_any']} of {nc['listed']} organizations "
              f"carry a case row by exact name; resolved {nc['orgs_resolved']}, charged (open) {nc['orgs_charged']}, "
              f"open whistleblower docket {nc['orgs_open_docket']}")
        for r in nm.itertuples():
            print(f"  {r.rank:>4}  {str(r.org_name)[:50]:<50}  cases {r.n_cases} (resolved {r.resolved}, charged {r.charged}, "
                  f"docket {r.open_docket})  latest {r.latest}  {r.case_types}")
        print(f"[list_case_overlap] -> {out2}")


if __name__ == "__main__":
    main()
