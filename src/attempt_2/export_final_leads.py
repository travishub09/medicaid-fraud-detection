"""
export_final_leads.py  (attempt 2) — final filtered leads CSV

Reads the MOST UPDATED leads (fraud_leads_v3.parquet: refined v3 Layer-2 + re-tiered
Layer-1/Layer-3, one row per billing NPI) and writes two human-readable CSVs:

  final_leads_over_10m.csv         every LEAD (tiers 1-5, i.e. surfaced by >=1 layer)
                                   with net_paid >= --min-net-paid, sorted tier then $.
  high_precision_excluded_leads.csv  the billed-while-excluded leads (tier 1 +
                                   any QUALIFIED disposition) regardless of dollar —
                                   highest precision, naturally below the threshold.

Tier 6 ("none") providers are NOT leads (no contributing signal) and are excluded
from the main file even when they bill >= the threshold.

`net_paid` is TOTAL lifetime billing — a coarse SIZE proxy, NOT an adjudicated case
value — and is labelled as such. Layer-1 rows are enriched with verified disposition
+ paid_after + exclusion details from layer1_candidate_cases.parquet when present.

Read-only on all inputs; modifies nothing. Identifiers written as strings.

Run:
    python -m src.attempt_2.export_final_leads --min-net-paid 10000000
"""

import argparse
from pathlib import Path

import duckdb

from .clean_data import PRECLEAN_DIR, _TAXONOMY_SEED


def _find(name: str, root: Path) -> Path | None:
    """Locate a parquet by name under detection/ (handles the user's subfolders)."""
    direct = root / name
    if direct.exists():
        return direct
    hits = sorted(root.rglob(name))
    return hits[0] if hits else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-net-paid", type=float, default=10_000_000.0)
    ap.add_argument("--no-preserve-excluded", action="store_true",
                    help="skip the companion billed-while-excluded file")
    args = ap.parse_args()
    thr = args.min_net_paid

    det = PRECLEAN_DIR.parent / "detection"
    v3 = _find("fraud_leads_v3.parquet", det)
    if v3 is None:
        raise FileNotFoundError(f"fraud_leads_v3.parquet not found under {det}")
    cc = _find("layer1_candidate_cases.parquet", det)   # optional enrichment
    print(f"Leads source : {v3}")
    print(f"Layer-1 cases: {cc if cc else '(none — using v3 Layer-1 fields)'}")

    con = duckdb.connect()
    # taxonomy description crosswalk (small seed from clean_data; blank otherwise)
    tax_values = ", ".join(f"('{k}', $${v}$$)" for k, v in _TAXONOMY_SEED.items()) or "('','')"
    con.execute(f"CREATE TABLE tax_xwalk(code VARCHAR, descr VARCHAR)")
    con.execute(f"INSERT INTO tax_xwalk VALUES {tax_values}")

    cc_join = (f"LEFT JOIN (SELECT npi, disposition AS layer1_disposition, paid_after, "
               f"excl_months AS excl_date, excltype AS excl_type FROM read_parquet('{cc}')) cc "
               f"USING (npi)") if cc else ""
    cc_cols = ("cc.layer1_disposition, cc.paid_after, cc.excl_date, cc.excl_type"
               if cc else "NULL AS layer1_disposition, NULL AS paid_after, "
                          "NULL AS excl_date, NULL AS excl_type")

    # one row per NPI; reasons = concat of whichever layers fired (skips empties)
    base = f"""
        SELECT
            l.npi,
            CASE l.entity_type WHEN '1' THEN 'individual' WHEN '2' THEN 'organization'
                 ELSE '(unknown)' END                         AS entity_type,
            l.primary_taxonomy,
            COALESCE(x.descr, '')                              AS taxonomy_description,
            l.practice_state                                   AS state,
            l.priority_tier,
            ROUND(l.net_paid, 2)                               AS total_billing_size_proxy_not_case_value,
            l.provider_on_leie,
            l.billed_after_exclusion,
            {cc_cols},
            ROUND(l.anomaly_score_v3, 4)                       AS anomaly_score_v3,
            l.n_concept_signals,
            array_to_string(l.anomaly_contributing_concepts, '; ') AS contributing_concepts,
            l.layer3_probable_owner                            AS probable_excluded_owner,
            concat_ws(' | ',
                NULLIF(array_to_string(l.rule_reasons, '; '), ''),
                NULLIF(array_to_string(l.anomaly_contributing_concepts, '; '), ''),
                CASE WHEN l.layer3_probable_owner THEN 'probable_excluded_owner' END
            )                                                  AS reasons,
            l.priority_rank, l.net_paid AS _net
        FROM read_parquet('{v3}') l
        LEFT JOIN tax_xwalk x ON l.primary_taxonomy = x.code
        {cc_join}
    """

    drop = "priority_rank, _net"  # internal sort keys, removed from CSV
    main_csv = det / "final_leads_over_10m.csv"
    con.execute(f"""
        COPY (SELECT * EXCLUDE ({drop}) FROM ({base})
              WHERE priority_tier <> '6_none' AND _net >= {thr}
              ORDER BY priority_rank ASC, _net DESC)
        TO '{main_csv}' (FORMAT CSV, HEADER, QUOTE '"', FORCE_QUOTE (npi))
    """)

    comp_csv = det / "high_precision_excluded_leads.csv"
    if not args.no_preserve_excluded:
        qualified = (f"OR npi IN (SELECT npi FROM read_parquet('{cc}') "
                     f"WHERE disposition = 'QUALIFIED')") if cc else ""
        con.execute(f"""
            COPY (SELECT * EXCLUDE ({drop}) FROM ({base})
                  WHERE priority_tier = '1_L1_billed_after_exclusion' {qualified}
                  ORDER BY paid_after DESC NULLS LAST, _net DESC)
            TO '{comp_csv}' (FORMAT CSV, HEADER, QUOTE '"', FORCE_QUOTE (npi))
        """)

    # ---- summary ----
    print(f"\nThreshold (net_paid, total billing size proxy): ${thr:,.0f}")
    print("Main file — leads surviving the threshold, per priority_tier:")
    sm = con.execute(f"""
        SELECT priority_tier, COUNT(*) n, ROUND(SUM(net_paid),0) total_paid
        FROM read_parquet('{v3}') WHERE priority_tier <> '6_none' AND net_paid >= {thr}
        GROUP BY 1 ORDER BY 1
    """).df()
    for r in sm.itertuples():
        print(f"  {r.priority_tier:<32} {r.n:>6,}   ${r.total_paid:,.0f}")
    print(f"  {'TOTAL':<32} {int(sm.n.sum()):>6,}   ${sm.total_paid.sum():,.0f}")
    n_excl_high = con.execute(
        f"SELECT COUNT(*) FROM read_csv_auto('{main_csv}')").fetchone()[0]
    print(f"  (main CSV rows: {n_excl_high:,})")
    if not args.no_preserve_excluded:
        nc = con.execute(f"SELECT COUNT(*) FROM read_csv_auto('{comp_csv}')").fetchone()[0]
        print(f"\nCompanion — billed-while-excluded leads preserved (any $): {nc:,} → {comp_csv.name}")
    con.close()
    print(f"\nWrote: {main_csv}")


if __name__ == "__main__":
    main()
