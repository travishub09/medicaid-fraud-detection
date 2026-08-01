"""
inbound_lookup.py — the standard first-pass on an inbound lead: name → NPIs →
address expansion → dollars → detection rank → named drivers.

Compleat Kidz set the pattern this tool bakes in. The brand name matched 19
enrolled NPIs with ZERO spending rows — which read as "not in our data" until
an ADDRESS pass found COMPREHAB LLC billing $129.7M from the chain's own HQ.
An inbound lead's billing NPI often doesn't carry the brand name (holding
company, legacy entity, billing group), so a name-only lookup has a known
false-quiet failure mode. This runs the whole ladder every time:

  1. NAME: every enrolled NPI matching the pattern (orgs + individuals);
  2. ADDRESS EXPANSION: every OTHER NPI enrolled at those NPIs' addresses —
     the billing entity hides here when the name misses;
  3. DOLLARS: spending totals, by year and top codes, for everything found;
  4. DETECTION RANK: the layer-3 leads-file position, with the file size so
     the rank reads correctly;
  5. DRIVERS: the champion-matrix row — peer percentiles >= the 95th and any
     lit scheme subscores. Absence of drivers is a real answer: unremarkable
     versus peers on everything we compute.

All reads are DuckDB-filtered (no full-matrix load), so it is safe to run
next to a build. Output is investigative context about billing DATA for
review — never an accusation, and a middle-of-the-pack result means the data
shows nothing remarkable.

  python -m src.model_a.inbound_lookup --name "compleat|comprehab" \
      --run-dir ~/Desktop/data/model_a/frozen_2023-12_v4 \
      --out INBOUND_compleat.md
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import pandas as pd


def _q(con, sql: str) -> pd.DataFrame:
    return con.execute(sql).df()


def _pq(p: Path) -> str:
    return str(p).replace("\\", "/")


def _like_clauses(pattern: str, cols: list[str]) -> str:
    """'compleat|comprehab' → OR of UPPER(col) LIKE '%COMPLEAT%' … terms."""
    terms = [t.strip().upper().replace("'", "''")
             for t in re.split(r"[|,]", pattern) if t.strip()]
    if not terms:
        raise ValueError("empty --name pattern")
    expr = " OR ".join(f"UPPER(COALESCE({c},'')) LIKE '%{t}%'"
                       for t in terms for c in cols)
    return f"({expr})"


def name_matches(con, dim: Path, pattern: str) -> pd.DataFrame:
    cond = _like_clauses(pattern, ["org_legal_name", "provider_name"])
    return _q(con, f"""
        select npi, provider_name, org_legal_name, entity_type,
               addr_line1, addr_city, addr_state, taxonomy_code
        from '{_pq(dim)}' where {cond}
        order by entity_type desc, addr_state, addr_city""")


def address_expansion(con, dim: Path, fact: Path,
                      seed: pd.DataFrame) -> pd.DataFrame:
    """Other NPIs enrolled at the seed NPIs' street addresses, with dollars —
    ranked so a hidden billing entity surfaces at the top."""
    lines = sorted({str(a).strip().upper() for a in seed["addr_line1"].dropna()
                    if str(a).strip()})
    if not lines:
        return pd.DataFrame()
    addr = " OR ".join("UPPER(TRIM(addr_line1)) = '{}'"
                       .format(a.replace("'", "''")) for a in lines)
    seen = ",".join(f"'{n}'" for n in seed["npi"].astype(str))
    return _q(con, f"""
        with here as (
            select npi, provider_name, entity_type, addr_line1, addr_city,
                   addr_state
            from '{_pq(dim)}'
            where ({addr}) and npi not in ({seen})
        )
        select h.npi, h.provider_name, h.entity_type, h.addr_line1,
               h.addr_city, h.addr_state,
               round(coalesce(sum(f.total_paid), 0)/1e6, 3) as paid_millions,
               min(f.service_month) as first_month,
               max(f.service_month) as last_month
        from here h left join '{_pq(fact)}' f on f.billing_npi = h.npi
        group by 1,2,3,4,5,6
        order by paid_millions desc""")


def spending_profile(con, fact: Path, npis: list[str]) -> dict:
    if not npis:
        return {"by_npi": pd.DataFrame(), "by_year": pd.DataFrame(),
                "top_codes": pd.DataFrame()}
    ns = ",".join(f"'{n}'" for n in npis)
    return {
        "by_npi": _q(con, f"""
            select billing_npi, any_value(provider_name) as name,
                   round(sum(total_paid)/1e6, 3) as paid_millions,
                   count(distinct hcpcs_code) as n_codes,
                   min(service_month) as first_month,
                   max(service_month) as last_month
            from '{_pq(fact)}' where billing_npi in ({ns})
            group by 1 order by paid_millions desc"""),
        "by_year": _q(con, f"""
            select substr(service_month, 1, 4) as year,
                   round(sum(total_paid)/1e6, 3) as paid_millions
            from '{_pq(fact)}' where billing_npi in ({ns})
            group by 1 order by 1"""),
        "top_codes": _q(con, f"""
            select hcpcs_code, round(sum(total_paid)/1e6, 3) as paid_millions,
                   round(sum(total_paid) / nullif(sum(total_claim_lines), 0), 0)
                       as paid_per_line
            from '{_pq(fact)}' where billing_npi in ({ns})
            group by 1 order by paid_millions desc limit 15""")}


def detection_rank(con, leads: Path, npis: list[str]) -> tuple[pd.DataFrame, int]:
    if not leads.exists() or not npis:
        return pd.DataFrame(), 0
    cols = _q(con, f"describe select * from '{_pq(leads)}'")["column_name"].tolist()
    keep = [c for c in ("npi", "provider_name", "anomaly_score_v3",
                        "priority_rank", "priority_tier", "n_concept_signals")
            if c in cols]
    ns = ",".join(f"'{n}'" for n in npis)
    rows = _q(con, f"select {', '.join(keep)} from '{_pq(leads)}' "
                   f"where npi in ({ns})")
    total = int(_q(con, f"select count(*) n from '{_pq(leads)}'")["n"][0])
    return rows, total


def matrix_drivers(con, matrix: Path, npis: list[str],
                   pct_floor: float = 0.95) -> list[dict]:
    """Per NPI: label flags + every peer percentile >= the floor + lit
    subscores. Reads only the matched rows (DuckDB filter, no full load)."""
    if not matrix.exists() or not npis:
        return []
    cols = _q(con, f"describe select * from '{_pq(matrix)}'")["column_name"].tolist()
    pct = [c for c in cols if c.endswith("__peerpct")]
    sub = [c for c in cols if c.startswith("subscore_")]
    base = [c for c in ("npi", "provider_name", "net_paid",
                        "provider_on_exclusion", "assessable",
                        "org_member_count") if c in cols]
    ns = ",".join(f"'{n}'" for n in npis)
    rows = _q(con, f'select {", ".join(base + pct + sub)} '
                   f"from '{_pq(matrix)}' where npi in ({ns})")
    out = []
    for _, r in rows.iterrows():
        drivers = sorted(((c.replace("__peerpct", ""), float(r[c]))
                          for c in pct
                          if pd.notna(r[c]) and float(r[c]) >= pct_floor),
                         key=lambda t: -t[1])
        subs = sorted(((c.replace("subscore_", ""), float(r[c]))
                       for c in sub if pd.notna(r[c]) and float(r[c]) > 0),
                      key=lambda t: -t[1])
        out.append({"npi": str(r["npi"]),
                    "provider_name": str(r.get("provider_name", "")),
                    "net_paid": float(r.get("net_paid", 0) or 0),
                    "on_exclusion": int(r.get("provider_on_exclusion", 0) or 0),
                    "assessable": (int(r["assessable"])
                                   if "assessable" in rows.columns
                                   and pd.notna(r.get("assessable")) else None),
                    "drivers": drivers, "subscores": subs})
    return out


def _t(df: pd.DataFrame) -> str:
    """Markdown table without the optional `tabulate` dependency."""
    if not len(df):
        return "_(none)_"
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |",
             "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = ["" if pd.isna(v) else str(v) for v in r.tolist()]
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells)
                     + " |")
    return "\n".join(lines)


def run_lookup(pattern: str, dim: Path, fact: Path, leads: Path,
               matrix: Path) -> dict:
    import duckdb
    con = duckdb.connect()
    names = name_matches(con, dim, pattern)
    expansion = (address_expansion(con, dim, fact, names)
                 if len(names) else pd.DataFrame())
    npis = list(dict.fromkeys(
        names["npi"].astype(str).tolist()
        + (expansion[pd.to_numeric(expansion.get("paid_millions", 0),
                                   errors="coerce").fillna(0) > 0]["npi"]
           .astype(str).tolist() if len(expansion) else [])))
    prof = spending_profile(con, fact, npis)
    ranks, leads_total = detection_rank(con, leads, npis)
    drivers = matrix_drivers(con, matrix, npis)
    con.close()
    return {"pattern": pattern, "names": names, "expansion": expansion,
            "profile": prof, "ranks": ranks, "leads_total": leads_total,
            "drivers": drivers}


def to_markdown(res: dict) -> str:
    L = [f"# INBOUND LOOKUP — `{res['pattern']}`", ""]
    L.append("_Billing DATA about the matched providers, assembled for review. "
             "A quiet section is a real answer, not a gap: it means the data "
             "shows nothing remarkable there. Nothing here is an accusation._")
    L.append("")
    L.append("## 1. Name matches (enrollment)")
    L.append("")
    L.append(_t(res["names"]))
    L.append("")
    L.append("## 2. Address expansion — other NPIs at the same addresses")
    L.append("")
    L.append("_The billing entity often hides here when the brand name "
             "misses. Rows with dollars are folded into every section below._")
    L.append("")
    exp = res["expansion"]
    if len(exp):
        hot = exp[pd.to_numeric(exp["paid_millions"],
                                errors="coerce").fillna(0) > 0]
        L.append(_t(hot if len(hot) else exp.head(10)))
        if len(exp) > len(hot):
            L.append(f"\n_+ {len(exp) - len(hot)} zero-dollar NPIs at the same "
                     "addresses (typically rendering staff — the group bills, "
                     "they render)._")
    else:
        L.append("_(none)_")
    L.append("")
    L.append("## 3. Dollars")
    L.append("")
    L.append(_t(res["profile"]["by_npi"]))
    L.append("")
    L.append("### By year")
    L.append("")
    L.append(_t(res["profile"]["by_year"]))
    L.append("")
    L.append("### Top codes")
    L.append("")
    L.append(_t(res["profile"]["top_codes"]))
    L.append("")
    L.append(f"## 4. Detection-leads rank (of {res['leads_total']:,} rows; "
             "rank 1 = highest priority)")
    L.append("")
    L.append(_t(res["ranks"]))
    L.append("")
    L.append("## 5. Named drivers (champion matrix, >= 95th peer percentile)")
    L.append("")
    if res["drivers"]:
        for d in res["drivers"]:
            L.append(f"- **{d['npi']}** {d['provider_name']} — net paid "
                     f"${d['net_paid']:,.0f}; on_exclusion={d['on_exclusion']}")
            if d["drivers"]:
                for name, v in d["drivers"][:12]:
                    L.append(f"  - {name}: {v:.3f}")
            else:
                L.append("  - no peer percentile at or above the 95th — "
                         "unremarkable vs peers on every metric we compute")
            if d["subscores"]:
                L.append("  - subscores: "
                         + ", ".join(f"{n}={v:.2f}"
                                     for n, v in d["subscores"][:8]))
    else:
        L.append("_(no matched NPI is in the matrix)_")
    L.append("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--name", required=True,
                    help="pattern(s), | separated — e.g. 'compleat|comprehab'")
    ap.add_argument("--data-root",
                    default=os.environ.get("MEDICAID_DATA_ROOT",
                                           str(Path.home() / "Desktop/data")))
    ap.add_argument("--run-dir", default=None,
                    help="champion run folder for the drivers section "
                         "(default: newest frozen_* under model_a/)")
    ap.add_argument("--out", default=None,
                    help="markdown report path (default: print to console)")
    args = ap.parse_args()
    root = Path(args.data_root)
    if args.run_dir:
        run_dir = Path(args.run_dir)
    else:
        frozen = sorted((root / "model_a").glob("frozen_*"))
        run_dir = frozen[-1] if frozen else root / "model_a"
    res = run_lookup(args.name,
                     dim=root / "processed" / "provider_dim.parquet",
                     fact=root / "processed" / "spending_fact.parquet",
                     leads=root / "detection" / "fraud_leads_v3.parquet",
                     matrix=run_dir / "provider_features_for_model.parquet")
    md = to_markdown(res)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"[inbound_lookup] {len(res['names'])} name matches, "
              f"{len(res['expansion'])} address-expansion NPIs -> {args.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
