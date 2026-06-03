"""
export_csv.py  (attempt 2) — render the detection leads as legible CSVs

The detection outputs are Parquet with LIST columns (rule_reasons,
anomaly_contributing_features) that don't open cleanly in spreadsheets. This
read-only utility flattens those lists to "; "-joined text, rounds the numeric
columns, orders by priority (then anomaly_score, then dollars), and writes CSVs
alongside the parquet:

  fraud_leads.csv            full table (one row per billing NPI)
  leads_ranked.csv           actionable subset (excludes the un-flagged tier)
  layer1_rule_hits.csv       Layer-1 deterministic hits
  layer3_ownership_leads.csv Layer-3 probable-owner track

Read-only on the parquet; never modifies them. Idempotent.

Run:
    python -m src.attempt_2.export_csv
"""

import argparse
from pathlib import Path

import duckdb

from ..clean_data import PRECLEAN_DIR

# Legible, ordered column projection. List cols are flattened; numbers rounded.
SELECT = """
    SELECT
        l.priority_tier,
        l.npi,
        pf.provider_name,
        org_legal_name,
        entity_type,
        primary_taxonomy,
        practice_state,
        round(net_paid, 0)              AS net_paid,
        round(gross_paid, 0)            AS gross_paid,
        provider_on_leie,
        billed_after_exclusion,
        excluded_after_billing,
        round(paid_after_exclusion, 0)  AS paid_after_exclusion,
        n_months_after,
        strftime(excl_date, '%Y-%m-%d') AS excl_date,
        max_claim_month,
        array_to_string(rule_reasons, '; ')                  AS rule_reasons,
        anomaly_lead,
        round(anomaly_score, 2)         AS anomaly_score,
        n_anomaly_signals,
        array_to_string(anomaly_contributing_features, '; ') AS anomaly_contributing_features,
        not_scored,
        not_scored_reason,
        layer3_probable_owner,
        facility_excluded_owner_n_probable,
        excluded_owner_role,
        round(lines_per_patient_instance, 2) AS lines_per_patient_instance,
        round(paid_per_claim_line, 2)        AS paid_per_claim_line
    FROM read_parquet('{src}') l
    LEFT JOIN (SELECT npi, provider_name FROM read_parquet('{pf}')) pf USING (npi)
    {where}
    ORDER BY priority_rank ASC,
             anomaly_score DESC NULLS LAST,
             paid_after_exclusion DESC NULLS LAST,
             net_paid DESC NULLS LAST
"""


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    data = PRECLEAN_DIR.parent
    det = data / "detection"
    src = det / "fraud_leads.parquet"
    if not src.exists():
        raise FileNotFoundError(f"{src} not found — run src.attempt_2.detect first")
    # provider_name (covers individuals + orgs) lives in provider_dim
    pf = next((d / "provider_dim.parquet" for d in
               [data / "integrated", data, data / "features"]
               if (d / "provider_dim.parquet").exists()), None)
    if pf is None:
        raise FileNotFoundError("provider_dim.parquet not found for the name join")

    con = duckdb.connect()
    jobs = [
        ("fraud_leads.csv", ""),
        ("leads_ranked.csv", "WHERE priority_tier <> '6_none'"),
        ("layer1_rule_hits.csv", "WHERE layer1_hit"),
        ("layer3_ownership_leads.csv", "WHERE layer3_probable_owner"),
    ]
    for name, where in jobs:
        out = det / name
        con.execute(f"COPY ({SELECT.format(src=src, pf=pf, where=where)}) "
                    f"TO '{out}' (FORMAT CSV, HEADER, QUOTE '\"')")
        n = con.execute(f"SELECT COUNT(*) FROM read_csv_auto('{out}')").fetchone()[0]
        print(f"  wrote {name:<28} {n:>9,} rows  ({out.stat().st_size/1e6:.1f} MB)")
    con.close()
    print(f"Done. CSVs in {det}")


if __name__ == "__main__":
    main()
