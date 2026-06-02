"""
audit_corruption.py  (attempt 2) — root-cause & quarantine corrupted spending rows

COVERAGE_DIAGNOSTIC.md proved the dataset's $21.8T total is fake: it is dominated
by a handful of physically-impossible TOTAL_PAID values (e.g. HCPCS '20' rows of
$7.47T each, with fabricated repeating-digit amounts). The prior step wrongly
labelled the whole $20.7T unmatched set "aggregate billing". This step corrects
that: it confirms the root cause from the raw rows, defines a PRINCIPLED,
generalizable corruption rule (not hardcoded row ids), quarantines the corruption
as a data defect, separates genuine aggregate billing, and re-establishes a
trustworthy dollar total.

Three-way split — quarantine, never delete; every row lands in exactly one bucket:
  * spending_corruption_quarantine.parquet  — flagged-corrupt rows (+ `reason`)
  * spending_aggregate_billing.parquet      — OVERWRITES the prior mislabeled file;
                                              ONLY legitimate non-provider/aggregate
                                              billing (valid, plausible, blank/agg id)
  * identifiable-provider feature base       — the matched subset of spending_fact;
                                              recorded as a FILTER, spending_fact is
                                              never rewritten.

Read-only on raw + spending_fact; idempotent; assertions raise and stop the run.

Run:
    python -m src.attempt_2.audit_corruption --processed ~/Desktop/data/integrated
"""

import argparse
from pathlib import Path

import duckdb

from .clean_data import PRECLEAN_DIR, _read_any  # shared raw-reader (all-VARCHAR)

# ----------------------------------------------------------------------------
# PRINCIPLED corruption rule (documented + generalizable; no hardcoded row ids)
# ----------------------------------------------------------------------------
# Rule: a single (provider/aggregate) NPI × HCPCS × month line whose TOTAL_PAID
# exceeds this ceiling is physically impossible for Medicaid and is treated as a
# data defect. Justification, from the legitimate distribution observed in this
# very dataset:
#   * matched real-provider base: max = $118,999,818 (~$119M), p99 ≈ $69K
#   * largest legitimate state-level aggregate line (T1019 personal care): ~$470M
#   * empirical gap: the largest value at/below this ceiling is ~$499.4M, the
#     smallest above it is ~$504.5M — no rows lie in between, so the threshold is
#     not sensitive across (~$470M, ~$692M).
# $500M is ~4× the matched max and above every legitimate value, while every
# corrupt row (HCPCS '20', Z9525, W1793) exceeds it. HCPCS-format alone is NOT
# used to quarantine, because the matched real-provider base legitimately
# contains hundreds of thousands of rows with non-standard/local codes.
PLAUSIBILITY_CEILING = 500_000_000.0

# Codes called out by the diagnostic; used ONLY for the verbatim root-cause dump.
SUMMARY_CODE_SAMPLES = ["Z9525", "Z9105", "Z9125", "Z9029", "Z9124"]


def log(m: str) -> None:
    print(m, flush=True)


def md_table(df, cols=None) -> str:
    cols = cols or list(df.columns)
    head = "| " + " | ".join(cols) + " |\n"
    sep = "|" + "|".join("---" for _ in cols) + "|\n"
    body = "".join("| " + " | ".join("" if v is None else str(v) for v in row) + " |\n"
                   for row in df[cols].itertuples(index=False))
    return head + sep + body


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--processed", default=str(PRECLEAN_DIR.parent / "integrated"),
                   help="Dir with spending_fact.parquet + where reports/outputs go")
    p.add_argument("--raw-spending",
                   default=str(PRECLEAN_DIR.parent / "interim" / "raw_parquet" / "Spending.parquet"),
                   help="Verbatim all-VARCHAR image of Spending.csv (falls back to the CSV)")
    p.add_argument("--report-name", default="CORRUPTION_AUDIT.md")
    args = p.parse_args()

    proc = Path(args.processed)
    sf_path = proc / "spending_fact.parquet"
    if not sf_path.exists():
        raise FileNotFoundError(f"spending_fact.parquet not found in {proc}")
    raw_src = args.raw_spending
    if not Path(raw_src).exists():
        raw_src = str(PRECLEAN_DIR / "Spending.csv")        # fall back to the CSV itself
    if not Path(raw_src).exists():
        raise FileNotFoundError("raw Spending source not found (parquet image or CSV)")

    con = duckdb.connect()
    con.execute(f"CREATE VIEW raw AS SELECT * FROM {_read_any(raw_src)}")
    con.execute(f"CREATE VIEW sf AS SELECT * FROM read_parquet('{sf_path}')")
    C = PLAUSIBILITY_CEILING
    out = [f"# CORRUPTION_AUDIT — attempt_2\n",
           "_Read-only on raw + spending_fact (never modified). Identifiers kept as strings; "
           "rows are quarantined, never deleted._\n"]

    # ---------------------------------------------------------------- #
    # 1. Raw-row inspection (root cause)
    # ---------------------------------------------------------------- #
    log("1. Inspecting raw rows (root cause) …")
    out.append("\n## 1. Root cause — verbatim raw rows\n")

    twenty = con.execute("""
        SELECT BILLING_PROVIDER_NPI_NUM AS billing, SERVICING_PROVIDER_NPI_NUM AS servicing,
               HCPCS_CODE AS hcpcs, CLAIM_FROM_MONTH AS month,
               TOTAL_PATIENTS AS patients, TOTAL_CLAIM_LINES AS claim_lines, TOTAL_PAID AS paid
        FROM raw WHERE HCPCS_CODE = '20' ORDER BY TRY_CAST(TOTAL_PAID AS DOUBLE) DESC
    """).df()
    out.append(f"\n**All {len(twenty)} rows with HCPCS_CODE = '20'** (verbatim):\n\n")
    out.append(md_table(twenty))
    out.append("\nObservations: billing NPI is BLANK on every row; servicing NPI is the single "
               "value `5200000300`; patient/claim-line counts are sane and stable (~900–1,200 / "
               "~8K–12K), but TOTAL_PAID swings from sane (~$345K in 2021) to physically impossible "
               "($889M–$7.47T in 2018–19) with fabricated repeating-digit values "
               "(e.g. `7469333333258.64`). The defect is isolated to TOTAL_PAID (not a column "
               "shift — counts are intact). `'20'` is also not a valid 5-char HCPCS/CPT code.\n")

    zc = con.execute(f"""
        SELECT HCPCS_CODE AS hcpcs, COUNT(*) AS rows,
               MIN(TRY_CAST(TOTAL_PAID AS DOUBLE)) AS min_paid,
               MAX(TRY_CAST(TOTAL_PAID AS DOUBLE)) AS max_paid,
               SUM(TRY_CAST(TOTAL_PAID AS DOUBLE)) AS sum_paid,
               COUNT(*) FILTER (WHERE BILLING_PROVIDER_NPI_NUM IS NULL) AS blank_billing
        FROM raw WHERE HCPCS_CODE IN ({",".join(f"'{c}'" for c in SUMMARY_CODE_SAMPLES)})
        GROUP BY 1 ORDER BY sum_paid DESC
    """).df()
    for col in ["min_paid", "max_paid", "sum_paid"]:
        zc[col] = zc[col].map(lambda v: f"${v:,.0f}")
    out.append("\n**Summary/Z codes** (all on blank billing NPIs — the fingerprint of embedded "
               "total/summary rows). Note these split two ways under the rule below: only `Z9525` "
               "has impossible per-row values (≥$692M) and is quarantined as corruption; "
               "`Z9105/Z9125/Z9029/Z9124` have *plausible* magnitudes ($22M–$124M/row) and are "
               "retained as legitimate aggregate billing — they are summary rows, not a dollar "
               "defect:\n\n")
    out.append(md_table(zc))

    big = con.execute("""
        SELECT BILLING_PROVIDER_NPI_NUM AS billing, SERVICING_PROVIDER_NPI_NUM AS servicing,
               HCPCS_CODE AS hcpcs, CLAIM_FROM_MONTH AS month,
               TOTAL_CLAIM_LINES AS claim_lines, TOTAL_PAID AS paid
        FROM raw WHERE BILLING_PROVIDER_NPI_NUM IS NULL AND TRY_CAST(TOTAL_PAID AS DOUBLE) > 1e9
        ORDER BY TRY_CAST(TOTAL_PAID AS DOUBLE) DESC LIMIT 8
    """).df()
    out.append("\n**Sample blank-billing-NPI rows with TOTAL_PAID > $1B:**\n\n")
    out.append(md_table(big))
    out.append("\n**Confirmed root cause:** embedded total/summary rows (blank billing NPI, a "
               "single servicing id, summary HCPCS codes) carrying corrupted/overflowed TOTAL_PAID "
               "values. The counts are intact, so this is corrupted dollar values on summary rows, "
               "not a column shift.\n")

    # ---------------------------------------------------------------- #
    # 2. Principled corruption rule
    # ---------------------------------------------------------------- #
    legit_max = con.execute(
        f"SELECT MAX(total_paid) FROM sf WHERE COALESCE(total_paid,0) <= {C}").fetchone()[0]
    corrupt_min = con.execute(
        f"SELECT MIN(total_paid) FROM sf WHERE COALESCE(total_paid,0) > {C}").fetchone()[0]
    matched_max = con.execute(
        "SELECT MAX(total_paid) FROM sf WHERE provider_matched").fetchone()[0]
    out.append("\n## 2. Corruption rule\n")
    out.append(f"- **Rule:** `TOTAL_PAID > ${C:,.0f}` per row ⇒ corrupt (physically impossible "
               f"Medicaid line payment).\n")
    out.append(f"- **Justification:** matched real-provider max = ${matched_max:,.0f}; "
               f"largest legitimate value at/below the ceiling = ${legit_max:,.0f}; smallest value "
               f"above the ceiling = ${corrupt_min:,.0f}. Clean empirical gap, no rows between — "
               f"threshold insensitive across that range. Ceiling is ~4× the matched max and above "
               f"the largest legitimate state-level aggregate line (~$470M, T1019 personal care).\n")
    out.append("- **Why not HCPCS-format:** the matched real-provider base legitimately contains "
               "hundreds of thousands of non-standard/local codes, so code format alone would "
               "mislabel real claims. Malformed format is recorded as informational context only.\n")

    # ---------------------------------------------------------------- #
    # 3. Leak check across the WHOLE table
    # ---------------------------------------------------------------- #
    leak = con.execute(f"""
        SELECT provider_matched, COUNT(*) AS rows, SUM(total_paid) AS paid
        FROM sf WHERE COALESCE(total_paid,0) > {C} GROUP BY 1
    """).df()
    matched_corrupt = int(leak.loc[leak["provider_matched"] == True, "rows"].sum()) if len(leak) else 0
    out.append("\n## 3. Did corruption leak into the matched provider base?\n")
    out.append(f"- Rows tripping the rule in the **matched** base: **{matched_corrupt}**.\n")
    out.append("- Conclusion: corruption is "
               + ("isolated to blank-NPI / summary rows; the matched provider base is clean.\n"
                  if matched_corrupt == 0 else
                  "PRESENT in the matched base — see rows above; investigate before feature work.\n"))

    # ---------------------------------------------------------------- #
    # 4. Three-way split (quarantine, never delete)
    # ---------------------------------------------------------------- #
    log("4. Writing three-way split …")
    quar_path = proc / "spending_corruption_quarantine.parquet"
    agg_path = proc / "spending_aggregate_billing.parquet"
    con.execute(f"""
        COPY (
            SELECT *,
                   'TOTAL_PAID > ${C:,.0f} (implausible per-row Medicaid payment)' AS reason,
                   NOT regexp_full_match(COALESCE(hcpcs_code,''), '[0-9]{{5}}|[A-Za-z][0-9]{{4}}')
                       AS hcpcs_malformed
            FROM sf WHERE COALESCE(total_paid,0) > {C}
        ) TO '{quar_path}' (FORMAT PARQUET)
    """)
    con.execute(f"""
        COPY (SELECT * FROM sf WHERE NOT provider_matched AND COALESCE(total_paid,0) <= {C})
        TO '{agg_path}' (FORMAT PARQUET)
    """)

    n_corrupt, p_corrupt = con.execute(
        f"SELECT COUNT(*), COALESCE(SUM(total_paid),0) FROM sf WHERE COALESCE(total_paid,0) > {C}").fetchone()
    n_agg, p_agg = con.execute(
        f"SELECT COUNT(*), COALESCE(SUM(total_paid),0) FROM sf "
        f"WHERE NOT provider_matched AND COALESCE(total_paid,0) <= {C}").fetchone()
    n_base, p_base = con.execute(
        f"SELECT COUNT(*), COALESCE(SUM(total_paid),0) FROM sf "
        f"WHERE provider_matched AND COALESCE(total_paid,0) <= {C}").fetchone()
    n_total, p_total = con.execute("SELECT COUNT(*), COALESCE(SUM(total_paid),0) FROM sf").fetchone()

    # partition assertions (every row in exactly one bucket; total preserved)
    assert n_corrupt + n_agg + n_base == n_total, \
        f"partition broken: {n_corrupt}+{n_agg}+{n_base} != {n_total}"
    assert abs((p_corrupt + p_agg + p_base) - p_total) <= max(0.01, 1e-9 * abs(p_total)), \
        "dollar partition does not reconcile"
    post_total = p_total - p_corrupt
    assert post_total < 5e12, f"post-quarantine total still implausible: ${post_total:,.0f}"
    log(f"    [assert PASS] partition exact ({n_total:,} rows) and dollars reconcile")
    log(f"    [assert PASS] post-quarantine total sane: ${post_total:,.0f}")

    out.append("\n## 4. Three-way split (quarantine, never delete)\n")
    out.append("| bucket | output | rows | total_paid |\n|---|---|--:|--:|\n")
    out.append(f"| corruption (defect) | `spending_corruption_quarantine.parquet` | {n_corrupt:,} | ${p_corrupt:,.0f} |\n")
    out.append(f"| legitimate aggregate billing | `spending_aggregate_billing.parquet` (overwritten) | {n_agg:,} | ${p_agg:,.0f} |\n")
    out.append(f"| identifiable-provider feature base | filter on `spending_fact` (not rewritten) | {n_base:,} | ${p_base:,.0f} |\n")
    out.append("\n**Provider feature-base filter** (spending_fact left unchanged):\n"
               f"`provider_matched = TRUE AND COALESCE(total_paid,0) <= {C:,.0f}`\n")

    # ---------------------------------------------------------------- #
    # 5. Reconciliation
    # ---------------------------------------------------------------- #
    out.append("\n## 5. Total reconciliation (before vs after quarantine)\n")
    out.append(f"- Dataset total **before** quarantine: {n_total:,} rows, **${p_total:,.2f}**\n")
    out.append(f"- Quarantined (corruption): {n_corrupt:,} rows, **${p_corrupt:,.2f}** "
               f"({p_corrupt/p_total:.1%} of the fake total)\n")
    out.append(f"- **Real total after quarantine: ${post_total:,.2f}** "
               f"(= provider base ${p_base:,.0f} + legitimate aggregate ${p_agg:,.0f})\n")
    out.append(f"- spending_fact.parquet: **unchanged** (no rows dropped; buckets written as new files).\n")

    report = proc / args.report_name
    report.write_text("".join(out))
    con.close()
    log(f"Wrote {report}")
    log(f"  corruption: {n_corrupt:,} rows / ${p_corrupt:,.0f}")
    log(f"  aggregate : {n_agg:,} rows / ${p_agg:,.0f}")
    log(f"  provider base: {n_base:,} rows / ${p_base:,.0f}")
    log(f"  real total after quarantine: ${post_total:,.0f}")


if __name__ == "__main__":
    main()
