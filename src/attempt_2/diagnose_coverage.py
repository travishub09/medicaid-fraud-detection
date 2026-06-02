"""
diagnose_coverage.py  (attempt 2) — READ-ONLY spending dollar-coverage diagnostic

integrate.py produced spending_fact with ~96.7% of ROWS matched to provider_dim
but only ~5.0% of DOLLARS matched ($1.1T of $21.8T). The unmatched ~3.3% of rows
carry ~95% of the dollars. This script characterizes exactly what is in that 95%
so the feature base can be scoped correctly.

It is strictly descriptive: it reads the existing integration outputs
(spending_fact / provider_dim / npi_quarantine), writes COVERAGE_DIAGNOSTIC.md,
and — only if the aggregate boundary is unambiguous — a single segregated
Parquet of the unmatched rows. It NEVER modifies spending_fact or any other
existing output, and NEVER coerces an identifier to numeric.

Authoritative facts reused from the integration (not recomputed):
  * billing_npi      = canonical NPI, NULL ⇒ the raw id failed 10-digit Luhn
  * provider_matched = canonical billing NPI is present in provider_dim
So for an UNMATCHED row:
  * billing_npi IS NOT NULL  ⇒ a valid NPI that is simply absent from NPPES
  * billing_npi IS NULL      ⇒ blank / alpha-prefixed / failed-Luhn / malformed
The single NPI canonicaliser from the integration code is imported and reused
for the per-identifier Luhn flag (rule: do not reimplement).

Run (defaults to where integrate.py wrote its outputs + QA_REPORT.md):
    python -m src.attempt_2.diagnose_coverage --processed ~/Desktop/data/integrated
"""

import argparse
from pathlib import Path

import duckdb
import pandas as pd

# Reuse the integration's NPI canonicaliser so identifier handling matches exactly.
from .clean_data import PRECLEAN_DIR, canonicalize_series

# If the valid-NPI-not-in-NPPES bucket holds less than this share of unmatched
# dollars, the aggregate boundary is "clean" and we may write the segregated table.
AMBIGUITY_THRESHOLD = 0.01


def d(v: float) -> str:
    return f"${v:,.0f}"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # Same dir integrate.py wrote spending_fact.parquet + QA_REPORT.md into.
    p.add_argument("--processed", default=str(PRECLEAN_DIR.parent / "integrated"),
                   help="Directory holding integrate.py's outputs (read-only inputs)")
    p.add_argument("--report-name", default="COVERAGE_DIAGNOSTIC.md")
    args = p.parse_args()

    proc = Path(args.processed)
    sf_path = proc / "spending_fact.parquet"
    quar_path = proc / "npi_quarantine.parquet"
    pdim_path = proc / "provider_dim.parquet"
    for pth in (sf_path, quar_path, pdim_path):
        if not pth.exists():
            raise FileNotFoundError(f"required integration output missing: {pth}")

    con = duckdb.connect()                       # in-memory; reads parquet read-only
    con.execute(f"CREATE VIEW sf AS SELECT * FROM read_parquet('{sf_path}')")
    out = ["# COVERAGE_DIAGNOSTIC — attempt_2 spending dollar-coverage gap\n",
           "_Read-only diagnostic over `spending_fact.parquet`. Identifiers kept as "
           "strings; no rows dropped; no existing output modified._\n"]

    # ----------------------------------------------------------------- #
    # 1. Matched vs unmatched split + TOTAL_PAID distribution
    # ----------------------------------------------------------------- #
    split = con.execute("""
        SELECT
            provider_matched,
            COUNT(*)                          AS n_rows,
            SUM(total_paid)                   AS paid,
            MIN(total_paid)                   AS p_min,
            approx_quantile(total_paid, 0.50) AS p50,
            approx_quantile(total_paid, 0.90) AS p90,
            approx_quantile(total_paid, 0.99) AS p99,
            MAX(total_paid)                   AS p_max,
            AVG(total_paid)                   AS p_mean
        FROM sf GROUP BY 1
    """).df()
    tot_rows = int(split["n_rows"].sum())
    tot_paid = float(split["paid"].sum())
    out.append("\n## 1. Matched vs unmatched split\n")
    out.append("| group | rows | % rows | total_paid | % dollars | mean | min | p50 | p90 | p99 | max |\n")
    out.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|\n")
    for grp_val, label in [(True, "matched (real NPI in NPPES)"),
                           (False, "unmatched")]:
        r = split[split["provider_matched"] == grp_val]
        if not len(r):
            continue
        r = r.iloc[0]
        out.append(
            f"| {label} | {int(r.n_rows):,} | {r.n_rows/tot_rows:.1%} | {d(r.paid)} | "
            f"{r.paid/tot_paid:.1%} | {d(r.p_mean)} | {d(r.p_min)} | {d(r.p50)} | "
            f"{d(r.p90)} | {d(r.p99)} | {d(r.p_max)} |\n")
    unmatched_paid = float(split.loc[split["provider_matched"] == False, "paid"].iloc[0])
    matched_paid = float(split.loc[split["provider_matched"] == True, "paid"].iloc[0])
    matched_rows = int(split.loc[split["provider_matched"] == True, "n_rows"].iloc[0])
    unmatched_rows = int(split.loc[split["provider_matched"] == False, "n_rows"].iloc[0])

    # ----------------------------------------------------------------- #
    # Per-identifier aggregation of the UNMATCHED rows (one row per literal id)
    # ----------------------------------------------------------------- #
    um = con.execute("""
        SELECT
            billing_npi_raw          AS raw,         -- literal string, leading zeros intact
            billing_npi              AS canonical,    -- NULL ⇒ failed Luhn
            COUNT(*)                 AS n_rows,
            SUM(total_paid)          AS paid
        FROM sf WHERE NOT provider_matched
        GROUP BY 1, 2
    """).df()
    # keep identifiers as strings; canonicalise raw with the SHARED helper for the flag
    um["raw"] = um["raw"].astype("string")
    raw_str = um["raw"].fillna("").str.strip()
    um["luhn_valid"] = canonicalize_series(um["raw"]).notna()
    um["has_alpha_prefix"] = raw_str.str.match(r"^[A-Za-z]").fillna(False)
    um["is_10_digit_numeric"] = raw_str.str.fullmatch(r"\d{10}").fillna(False)
    um["is_blank"] = raw_str.eq("")

    quar = con.execute(
        f"SELECT DISTINCT TRIM(raw_value) AS rv FROM read_parquet('{quar_path}') "
        f"WHERE source = 'spending_billing'").df()
    quar_set = set(quar["rv"].astype(str))
    um["in_quarantine"] = raw_str.isin(quar_set)
    um["in_provider_dim"] = False                # unmatched ⇒ not in provider_dim by construction

    # mutually-exclusive format class (precedence order)
    def classify(r) -> str:
        if r.is_blank:
            return "blank / missing (no billing NPI)"
        if r.has_alpha_prefix:
            return "alpha/prefixed identifier"
        if r.is_10_digit_numeric and r.luhn_valid:
            return "10-digit valid NPI, not in NPPES"
        if r.is_10_digit_numeric and not r.luhn_valid:
            return "10-digit, failed Luhn"
        return "other / malformed"
    um["bucket"] = um.apply(classify, axis=1)

    # ----------------------------------------------------------------- #
    # 2. Top 50 unmatched identifiers by summed TOTAL_PAID
    # ----------------------------------------------------------------- #
    out.append("\n## 2. Top 50 unmatched billing identifiers by summed TOTAL_PAID\n")
    out.append("| # | billing id (literal) | total_paid | rows | alpha_prefix | 10-digit | "
               "luhn_valid | in_provider_dim | in_quarantine |\n")
    out.append("|--:|---|--:|--:|:-:|:-:|:-:|:-:|:-:|\n")
    top = um.sort_values("paid", ascending=False).head(50).reset_index(drop=True)
    for i, r in top.iterrows():
        lit = "(blank/NULL)" if (r.raw is pd.NA or pd.isna(r.raw) or r.raw == "") else f"`{r.raw}`"
        b = lambda x: "✓" if x else "·"
        out.append(f"| {i+1} | {lit} | {d(r.paid)} | {int(r.n_rows):,} | {b(r.has_alpha_prefix)} | "
                   f"{b(r.is_10_digit_numeric)} | {b(r.luhn_valid)} | {b(r.in_provider_dim)} | "
                   f"{b(r.in_quarantine)} |\n")

    # ----------------------------------------------------------------- #
    # 3. Format breakdown of ALL unmatched dollars by identifier class
    # ----------------------------------------------------------------- #
    bk = (um.groupby("bucket").agg(rows=("n_rows", "sum"), paid=("paid", "sum"),
                                   distinct_ids=("raw", "size")).reset_index()
          .sort_values("paid", ascending=False))
    out.append("\n## 3. Unmatched dollars by identifier class\n")
    out.append("| identifier class | distinct ids | rows | total_paid | % unmatched $ |\n")
    out.append("|---|--:|--:|--:|--:|\n")
    for _, r in bk.iterrows():
        out.append(f"| {r.bucket} | {int(r.distinct_ids):,} | {int(r.rows):,} | {d(r.paid)} | "
                   f"{r.paid/unmatched_paid:.1%} |\n")
    valid_not_nppes_paid = float(bk.loc[bk["bucket"] == "10-digit valid NPI, not in NPPES",
                                        "paid"].sum())

    # ----------------------------------------------------------------- #
    # 4. Quarantine accounting
    # ----------------------------------------------------------------- #
    q_rows = int(um.loc[um["in_quarantine"], "n_rows"].sum())
    q_paid = float(um.loc[um["in_quarantine"], "paid"].sum())
    out.append("\n## 4. Quarantine accounting (the spending_billing quarantine set)\n")
    out.append(f"- distinct quarantined billing identifiers: {len(quar_set):,}\n")
    out.append(f"- spending_fact rows on quarantined ids: {q_rows:,}\n")
    out.append(f"- TOTAL_PAID on quarantined ids: {d(q_paid)} "
               f"({q_paid/unmatched_paid:.2%} of unmatched dollars)\n")

    # ----------------------------------------------------------------- #
    # 5. Unmatched dollars by HCPCS code (top 25)
    # ----------------------------------------------------------------- #
    hc = con.execute("""
        SELECT hcpcs_code, SUM(total_paid) AS paid, COUNT(*) AS n_rows
        FROM sf WHERE NOT provider_matched
        GROUP BY 1 ORDER BY paid DESC LIMIT 25
    """).df()
    out.append("\n## 5. Top 25 HCPCS codes by unmatched TOTAL_PAID\n")
    out.append("| HCPCS | total_paid | rows | % unmatched $ |\n|---|--:|--:|--:|\n")
    for _, r in hc.iterrows():
        code = r.hcpcs_code if (r.hcpcs_code not in (None, "")) else "(blank)"
        out.append(f"| `{code}` | {d(r.paid)} | {int(r.n_rows):,} | {r.paid/unmatched_paid:.1%} |\n")

    # ----------------------------------------------------------------- #
    # 6. Scope recommendation
    # ----------------------------------------------------------------- #
    ambiguous_share = valid_not_nppes_paid / unmatched_paid if unmatched_paid else 0.0
    clean_boundary = ambiguous_share < AMBIGUITY_THRESHOLD
    out.append("\n## 6. Scope recommendation\n")
    out.append(f"- **Identifiable-provider feature base** (matched, real NPPES NPI): "
               f"{matched_rows:,} rows, {d(matched_paid)}.\n")
    out.append(f"- **Aggregate / non-provider billing track** (unmatched): "
               f"{unmatched_rows:,} rows, {d(unmatched_paid)}.\n")
    out.append(f"- Within the unmatched track, the *ambiguous* class "
               f"'10-digit valid NPI, not in NPPES' is {d(valid_not_nppes_paid)} "
               f"({ambiguous_share:.2%} of unmatched dollars). "
               + ("This is below the cleanliness threshold, so the unmatched set is treated "
                  "as a single aggregate/non-provider track.\n"
                  if clean_boundary else
                  "This is material, so it should be reviewed as its own bucket (possible real "
                  "providers absent from the NPPES snapshot) rather than lumped with aggregates.\n"))
    out.append("- Recommendation: build features ONLY on the identifiable-provider base above; "
               "analyse the aggregate-billing track separately (it is dominated by blank/"
               "bulk identifiers, not rendering providers).\n")

    # ----------------------------------------------------------------- #
    # Optional: write the segregated aggregate table iff the boundary is clean
    # ----------------------------------------------------------------- #
    seg_path = proc / "spending_aggregate_billing.parquet"
    if clean_boundary:
        con.execute(f"COPY (SELECT * FROM sf WHERE NOT provider_matched) "
                    f"TO '{seg_path}' (FORMAT PARQUET)")
        out.append(f"\n_Segregated aggregate-billing rows written to "
                   f"`{seg_path.name}` ({unmatched_rows:,} rows; spending_fact untouched)._\n")
    else:
        out.append("\n_Segregated table NOT written: the aggregate boundary is ambiguous "
                   "(see §6); recommending the split in this report only._\n")

    report = proc / args.report_name
    report.write_text("".join(out))
    con.close()
    print(f"Wrote {report}")
    print(f"  matched: {matched_rows:,} rows / {d(matched_paid)}")
    print(f"  unmatched: {unmatched_rows:,} rows / {d(unmatched_paid)}")
    print(f"  ambiguous (valid NPI not in NPPES): {ambiguous_share:.2%} of unmatched $  "
          f"→ segregated table {'WRITTEN' if clean_boundary else 'skipped'}")


if __name__ == "__main__":
    main()
