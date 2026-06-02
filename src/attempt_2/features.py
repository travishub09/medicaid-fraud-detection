"""
features.py  (attempt 2) — clean provider base + provider-level fraud features

The corruption audit (CORRUPTION_AUDIT.md) established that the trustworthy,
attributable spending base is spending_fact filtered to
`provider_matched = TRUE AND total_paid <= 500000000`. spending_fact itself was
NEVER modified and still contains the $20T of corruption + aggregate billing, so
nothing here may read it for features.

This script:
  0. materializes that clean base as spending_provider_base.parquet and asserts
     it reconciles to the audit (rows + dollars); fails the build on mismatch;
  1. builds provider_month + provider_hcpcs intermediates;
  2. builds provider_features.parquet — exactly one row per billing NPI — with
     volume/payment, ratio, concentration, robust peer-relative (median/MAD),
     temporal (mature months only), specialty-vs-code proxy, and deduped
     non-fan-out linkage features.

Correctness baked in (see report): TOTAL_PATIENTS is never summed and called
"distinct patients" — SUM is a service-volume proxy and ratios are labelled
"per patient-service-instance"; negatives are reversals (gross/net/reversal
split, never silently cancelled); temporal features use mature months only;
peer baselines use robust median/MAD with a minimum group size; every linkage
source is collapsed to one row per NPI and each join is asserted non-fan-out.

Read-only on spending_fact and the other integration outputs (never modified);
writes only to the attempt_2 output dir. Idempotent. Assertions raise and stop.

Run:
    python -m src.attempt_2.features --in-dir ~/Desktop/data/integrated
"""

import argparse
from pathlib import Path

import duckdb

from .clean_data import PRECLEAN_DIR

# Parameters
PLAUSIBILITY_CEILING = 500_000_000.0      # from the corruption audit
MIN_PEER_GROUP = 30                       # robust peer scoring requires >= this
MATURITY_MONTHS = 6                       # exclude the final N months (claims run-out)
RARE_CODE_THRESHOLD = 0.01                # code billed by <1% of a taxonomy's providers = rare
SURGE_RATIO = 5.0                         # max month / median month ⇒ surge onset

# Audit reconciliation targets (step 0 must match these or fail).
AUDIT_BASE_ROWS = 230_133_234
AUDIT_BASE_PAID = 1_100_631_960_143.0

# Robust peer metrics: (column, short alias)
PEER_METRICS = [("net_paid", "net_paid"),
                ("paid_per_claim_line", "ppcl"),
                ("lines_per_patient_instance", "lppi"),
                ("paid_per_patient_instance", "pppi")]


def log(m: str) -> None:
    print(m, flush=True)


def require(name: str, ok: bool, detail: str = "") -> None:
    log(f"    [assert {'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        raise AssertionError(f"ASSERTION FAILED: {name} — {detail}")


def _n(con, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _unique_npi(con, table: str) -> bool:
    r = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT npi) FROM {table}").fetchone()
    return r[0] == r[1]


def peer_sql(table: str, group_expr: str, suffix: str) -> str:
    """Generate the robust-peer CREATE TABLE for one peer-group definition.

    Robust z = 1.4826 * (x - median) / MAD; percentile = percent_rank within
    group. Undersized groups (< MIN_PEER_GROUP) get NULL scores + a too_small
    flag so we never emit a confident outlier score off a tiny baseline.
    """
    med = ", ".join(f"median({c}) AS med_{a}" for c, a in PEER_METRICS)
    madj = ", ".join(f"median(abs(m.{c} - g.med_{a})) AS mad_{a}" for c, a in PEER_METRICS)
    pcts = ", ".join(
        f"percent_rank() OVER (PARTITION BY {group_expr} ORDER BY {c}) AS p_{a}"
        for c, a in PEER_METRICS)
    cols = []
    for c, a in PEER_METRICS:
        cols.append(f"CASE WHEN g.sz < {MIN_PEER_GROUP} OR COALESCE(mad.mad_{a},0) = 0 "
                    f"THEN NULL ELSE 1.4826*(m.{c}-g.med_{a})/mad.mad_{a} END AS {c}_rz_{suffix}")
        cols.append(f"CASE WHEN g.sz < {MIN_PEER_GROUP} THEN NULL ELSE pct.p_{a} END "
                    f"AS {c}_pct_{suffix}")
    cols.append(f"g.sz AS peer_group_size_{suffix}")
    cols.append(f"(g.sz < {MIN_PEER_GROUP}) AS peer_group_too_small_{suffix}")
    return f"""
        CREATE OR REPLACE TABLE {table} AS
        WITH g AS (SELECT {group_expr} AS gk, COUNT(*) AS sz, {med} FROM metrics GROUP BY 1),
        mad AS (SELECT {group_expr} AS gk, {madj}
                FROM metrics m JOIN g ON {group_expr} = g.gk GROUP BY 1),
        pct AS (SELECT npi, {pcts} FROM metrics)
        SELECT m.npi, {", ".join(cols)}
        FROM metrics m
        JOIN g   ON {group_expr} = g.gk
        JOIN mad ON {group_expr} = mad.gk
        JOIN pct ON m.npi = pct.npi
    """


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--in-dir", default=str(PRECLEAN_DIR.parent / "integrated"),
                   help="Dir with integrate.py outputs (spending_fact etc.; read-only)")
    p.add_argument("--out-dir", default=None,
                   help="Output dir (default <in-dir>/attempt_2)")
    p.add_argument("--db", default=None, help="DuckDB working file (default <out-dir>/_features.duckdb)")
    args = p.parse_args()

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir) if args.out_dir else in_dir / "attempt_2"
    out_dir.mkdir(parents=True, exist_ok=True)
    db = args.db or str(out_dir / "_features.duckdb")
    C = PLAUSIBILITY_CEILING

    sf = in_dir / "spending_fact.parquet"
    excl = in_dir / "exclusions.parquet"
    fof = in_dir / "facility_owner_exclusion_flags.parquet"
    oe = in_dir / "owner_edges.parquet"
    for pth in (sf, excl, fof, oe):
        if not pth.exists():
            raise FileNotFoundError(f"required integration output missing: {pth}")

    con = duckdb.connect(db)
    rep: list[str] = ["# FEATURES_REPORT — attempt_2\n",
                      "_Provider-level fraud features built ONLY from the clean provider base "
                      "(spending_fact is read-only and never modified)._\n"]

    # ---------------------------------------------------------------- #
    # Step 0 — materialize the clean provider base + reconcile to audit
    # ---------------------------------------------------------------- #
    log("Step 0: materializing spending_provider_base.parquet …")
    base_path = out_dir / "spending_provider_base.parquet"
    con.execute(f"""
        COPY (SELECT * FROM read_parquet('{sf}')
              WHERE provider_matched = TRUE AND COALESCE(total_paid,0) <= {C})
        TO '{base_path}' (FORMAT PARQUET)
    """)
    con.execute(f"CREATE OR REPLACE VIEW base AS SELECT * FROM read_parquet('{base_path}')")
    chk = con.execute(f"""
        SELECT COUNT(*) n, COALESCE(SUM(total_paid),0) paid,
               COUNT(*) FILTER (WHERE NOT provider_matched) bad_pm,
               COUNT(*) FILTER (WHERE COALESCE(total_paid,0) > {C}) bad_ceil
        FROM base""").fetchone()
    n_base, paid_base, bad_pm, bad_ceil = chk
    require("base_all_provider_matched", bad_pm == 0, f"{bad_pm} rows not matched")
    require("base_under_ceiling", bad_ceil == 0, f"{bad_ceil} rows over ${C:,.0f}")
    require("base_rows_reconcile_audit", n_base == AUDIT_BASE_ROWS,
            f"base={n_base:,} audit={AUDIT_BASE_ROWS:,}")
    require("base_dollars_reconcile_audit",
            abs(paid_base - AUDIT_BASE_PAID) <= max(1.0, 1e-6 * AUDIT_BASE_PAID),
            f"base=${paid_base:,.2f} audit=${AUDIT_BASE_PAID:,.2f}")
    expected = con.execute("SELECT COUNT(DISTINCT billing_npi) FROM base").fetchone()[0]
    rep.append("\n## Base reconciliation (step 0)\n"
               f"- spending_provider_base.parquet: {n_base:,} rows, ${paid_base:,.2f}\n"
               f"- reconciles to audit ({AUDIT_BASE_ROWS:,} rows, ${AUDIT_BASE_PAID:,.0f}) ✓\n"
               f"- distinct billing NPIs (= final feature row count): {expected:,}\n"
               f"- filter: `provider_matched = TRUE AND COALESCE(total_paid,0) <= {C:,.0f}`\n")

    # ---------------------------------------------------------------- #
    # 1 — intermediates
    # ---------------------------------------------------------------- #
    log("Building provider_hcpcs + provider_month intermediates …")
    con.execute("""
        CREATE OR REPLACE TABLE provider_hcpcs AS
        SELECT billing_npi, hcpcs_code,
               SUM(total_paid)                                          AS net_paid_code,
               SUM(CASE WHEN total_paid > 0 THEN total_paid ELSE 0 END) AS gross_paid_code
        FROM base GROUP BY 1, 2
    """)
    con.execute("""
        CREATE OR REPLACE TABLE provider_month AS
        SELECT billing_npi, service_month,
               TRY_CAST(service_month || '-01' AS DATE)                 AS month_date,
               SUM(total_paid)                                          AS net_paid,
               SUM(CASE WHEN total_paid > 0 THEN total_paid ELSE 0 END) AS gross_paid,
               SUM(total_claim_lines)                                   AS claim_lines,
               SUM(total_patients)                                      AS service_volume
        FROM base GROUP BY 1, 2, 3
    """)
    con.execute(f"COPY provider_hcpcs TO '{out_dir/'provider_hcpcs.parquet'}' (FORMAT PARQUET)")
    con.execute(f"COPY provider_month TO '{out_dir/'provider_month.parquet'}' (FORMAT PARQUET)")

    # ---------------------------------------------------------------- #
    # f_core: lifetime volume/payment + ratios + carried dims (one row per NPI)
    # ---------------------------------------------------------------- #
    log("Building lifetime/volume/ratio features …")
    con.execute("""
        CREATE OR REPLACE TABLE f_core AS
        WITH agg AS (
            SELECT billing_npi AS npi,
                   ANY_VALUE(entity_type)    AS entity_type,
                   ANY_VALUE(taxonomy_code)  AS primary_taxonomy,
                   ANY_VALUE(provider_state) AS practice_state,
                   ANY_VALUE(org_legal_name) AS org_legal_name,
                   SUM(CASE WHEN total_paid > 0 THEN total_paid ELSE 0 END) AS gross_paid,
                   SUM(total_paid)                                          AS net_paid,
                   SUM(CASE WHEN total_paid < 0 THEN total_paid ELSE 0 END) AS reversal_amount,
                   SUM(total_claim_lines)                                   AS total_claim_lines,
                   SUM(total_patients)                                      AS service_volume,
                   COUNT(DISTINCT hcpcs_code)                               AS n_distinct_hcpcs,
                   COUNT(DISTINCT service_month)                            AS n_active_months,
                   MIN(service_month)                                       AS first_month,
                   MAX(service_month)                                       AS last_month
            FROM base GROUP BY billing_npi)
        SELECT *,
               abs(reversal_amount) / NULLIF(gross_paid, 0)         AS reversal_ratio,
               net_paid / NULLIF(total_claim_lines, 0)              AS paid_per_claim_line,
               net_paid / NULLIF(service_volume, 0)                 AS paid_per_patient_instance,
               total_claim_lines / NULLIF(service_volume, 0)        AS lines_per_patient_instance,
               datediff('month', TRY_CAST(first_month||'-01' AS DATE),
                                 TRY_CAST(last_month||'-01' AS DATE)) + 1 AS tenure_months
        FROM agg
    """)
    require("f_core_one_row_per_npi", _unique_npi(con, "f_core"))
    require("f_core_rowcount_eq_distinct_npi", _n(con, "f_core") == expected,
            f"{_n(con,'f_core')} vs {expected}")

    # ---------------------------------------------------------------- #
    # Concentration (over positive/gross dollars per code, to avoid negative-share artifacts)
    # ---------------------------------------------------------------- #
    con.execute("""
        CREATE OR REPLACE TABLE f_conc AS
        WITH tot AS (SELECT billing_npi, SUM(gross_paid_code) g FROM provider_hcpcs GROUP BY 1)
        SELECT p.billing_npi AS npi,
               MAX(p.gross_paid_code) / NULLIF(t.g, 0)            AS top_hcpcs_paid_share,
               CASE WHEN t.g > 0
                    THEN SUM(POWER(p.gross_paid_code / t.g, 2)) END AS hcpcs_hhi
        FROM provider_hcpcs p JOIN tot t USING (billing_npi)
        GROUP BY p.billing_npi, t.g
    """)
    require("f_conc_one_row_per_npi", _unique_npi(con, "f_conc"))

    # ---------------------------------------------------------------- #
    # Robust peer features (taxonomy, and taxonomy × state)
    # ---------------------------------------------------------------- #
    log("Building robust peer features (median/MAD) …")
    con.execute("""
        CREATE OR REPLACE TABLE metrics AS
        SELECT npi, primary_taxonomy, practice_state,
               net_paid, paid_per_claim_line, lines_per_patient_instance, paid_per_patient_instance
        FROM f_core
    """)
    con.execute(peer_sql("f_peer_tax", "primary_taxonomy", "tax"))
    con.execute(peer_sql("f_peer_taxstate",
                         "(primary_taxonomy || '|' || COALESCE(practice_state,''))", "taxstate"))
    require("f_peer_tax_one_row_per_npi", _unique_npi(con, "f_peer_tax"))
    require("f_peer_taxstate_one_row_per_npi", _unique_npi(con, "f_peer_taxstate"))

    # ---------------------------------------------------------------- #
    # Temporal (mature months only) + maturity cutoff
    # ---------------------------------------------------------------- #
    log("Building temporal features (mature months only) …")
    dmin, dmax = con.execute("SELECT MIN(month_date), MAX(month_date) FROM provider_month").fetchone()
    con.execute(f"""
        CREATE OR REPLACE TABLE f_temporal AS
        SELECT npi, max_single_month_net_paid, month_to_month_volatility,
               recent12 / NULLIF(prior12, 0) - 1                       AS yoy_growth_net_paid,
               (not_left_censored
                AND max_single_month_net_paid / NULLIF(med_month_net, 0) >= {SURGE_RATIO})
                                                                       AS new_biller_surge_onset
        FROM (
            WITH b AS (SELECT MAX(month_date) dmax, MIN(month_date) dmin FROM provider_month),
            mat AS (SELECT pm.billing_npi, pm.month_date, pm.net_paid
                    FROM provider_month pm, b
                    WHERE pm.month_date <= b.dmax - INTERVAL {MATURITY_MONTHS} MONTH)
            SELECT mat.billing_npi AS npi,
                   MAX(net_paid)                                       AS max_single_month_net_paid,
                   CASE WHEN AVG(net_paid) <> 0
                        THEN stddev_samp(net_paid) / abs(AVG(net_paid)) END
                                                                       AS month_to_month_volatility,
                   median(net_paid)                                    AS med_month_net,
                   (MIN(month_date) > (SELECT dmin FROM b))            AS not_left_censored,
                   SUM(net_paid) FILTER (
                        WHERE month_date > (SELECT dmax FROM b) - INTERVAL 18 MONTH) AS recent12,
                   SUM(net_paid) FILTER (
                        WHERE month_date <= (SELECT dmax FROM b) - INTERVAL 18 MONTH
                          AND month_date >  (SELECT dmax FROM b) - INTERVAL 30 MONTH) AS prior12
            FROM mat GROUP BY mat.billing_npi)
    """)
    require("f_temporal_one_row_per_npi", _unique_npi(con, "f_temporal"))
    mature_cutoff = con.execute(
        f"SELECT (MAX(month_date) - INTERVAL {MATURITY_MONTHS} MONTH)::DATE FROM provider_month"
    ).fetchone()[0]

    # ---------------------------------------------------------------- #
    # Specialty-vs-code proxy (share of $ on codes rare for the provider's taxonomy)
    # ---------------------------------------------------------------- #
    log("Building specialty-vs-code proxy …")
    con.execute(f"""
        CREATE OR REPLACE TABLE rare_codes AS
        WITH tax_prov AS (SELECT taxonomy_code AS tax, COUNT(DISTINCT billing_npi) np FROM base GROUP BY 1),
        tax_code AS (SELECT taxonomy_code AS tax, hcpcs_code, COUNT(DISTINCT billing_npi) npc FROM base GROUP BY 1,2)
        SELECT tc.tax, tc.hcpcs_code
        FROM tax_code tc JOIN tax_prov tp USING (tax)
        WHERE tp.np > 0 AND CAST(tc.npc AS DOUBLE) / tp.np < {RARE_CODE_THRESHOLD}
    """)
    con.execute("""
        CREATE OR REPLACE TABLE f_mismatch AS
        WITH ph AS (SELECT p.billing_npi, p.hcpcs_code, p.gross_paid_code, c.primary_taxonomy AS tax
                    FROM provider_hcpcs p JOIN f_core c ON p.billing_npi = c.npi)
        SELECT ph.billing_npi AS npi,
               SUM(CASE WHEN r.hcpcs_code IS NOT NULL THEN gross_paid_code ELSE 0 END)
                   / NULLIF(SUM(gross_paid_code), 0) AS rare_for_taxonomy_paid_share
        FROM ph LEFT JOIN rare_codes r ON ph.tax = r.tax AND ph.hcpcs_code = r.hcpcs_code
        GROUP BY ph.billing_npi
    """)
    require("f_mismatch_one_row_per_npi", _unique_npi(con, "f_mismatch"))

    # ---------------------------------------------------------------- #
    # Linkage (deduped, non-fan-out): LEIE + facility excluded-owner
    # ---------------------------------------------------------------- #
    log("Building linkage features (deduped) …")
    require("facility_flags_unique_npi",
            con.execute(f"SELECT COUNT(*)=COUNT(DISTINCT facility_npi) FROM read_parquet('{fof}')"
                        ).fetchone()[0], "facility_owner_exclusion_flags not 1-per-facility")
    # strongest excluded-owner role per facility (one row per facility_npi)
    con.execute(f"""
        CREATE OR REPLACE TABLE owner_role AS
        WITH excl_npi  AS (SELECT DISTINCT npi      FROM read_parquet('{excl}') WHERE npi IS NOT NULL),
             excl_name AS (SELECT DISTINCT name_key FROM read_parquet('{excl}') WHERE COALESCE(name_key,'') <> ''),
        matched AS (
            SELECT oe.facility_npi, oe.owner_role,
                   CASE WHEN UPPER(COALESCE(oe.owner_role,'')) LIKE '%MANAG%'
                          OR UPPER(COALESCE(oe.owner_role,'')) LIKE '%OWNER%' THEN 3
                        WHEN UPPER(COALESCE(oe.owner_role,'')) LIKE '%DIRECTOR%'
                          OR UPPER(COALESCE(oe.owner_role,'')) LIKE '%OFFICER%'
                          OR UPPER(COALESCE(oe.owner_role,'')) LIKE '%PARTNER%' THEN 2
                        ELSE 1 END AS w
            FROM read_parquet('{oe}') oe
            WHERE oe.facility_npi IS NOT NULL
              AND ( (oe.owner_npi IS NOT NULL AND oe.owner_npi IN (SELECT npi FROM excl_npi))
                 OR (COALESCE(oe.owner_org_name_key, oe.owner_name_key) IN (SELECT name_key FROM excl_name)) ))
        SELECT facility_npi, arg_max(owner_role, w) AS excluded_owner_role
        FROM matched GROUP BY facility_npi
    """)
    require("owner_role_one_row_per_facility", con.execute(
        "SELECT COUNT(*)=COUNT(DISTINCT facility_npi) FROM owner_role").fetchone()[0])
    con.execute(f"""
        CREATE OR REPLACE TABLE f_linkage AS
        WITH excl_npi AS (SELECT DISTINCT npi FROM read_parquet('{excl}') WHERE npi IS NOT NULL)
        SELECT c.npi,
               (c.npi IN (SELECT npi FROM excl_npi))                       AS provider_on_leie,
               COALESCE(fof.has_high_excluded_owner, 0) > 0                AS facility_has_excluded_owner_high,
               COALESCE(fof.has_probable_excluded_owner, 0) > 0            AS facility_has_excluded_owner_probable,
               fof.n_high     AS facility_excluded_owner_n_high,
               fof.n_probable AS facility_excluded_owner_n_probable,
               r.excluded_owner_role
        FROM f_core c
        LEFT JOIN read_parquet('{fof}') fof ON c.npi = fof.facility_npi
        LEFT JOIN owner_role r              ON c.npi = r.facility_npi
    """)
    require("f_linkage_one_row_per_npi", _unique_npi(con, "f_linkage"))
    require("f_linkage_rowcount_eq_distinct_npi", _n(con, "f_linkage") == expected)

    # ---------------------------------------------------------------- #
    # Assemble — LEFT JOIN every (asserted-unique) intermediate; non-fan-out
    # ---------------------------------------------------------------- #
    log("Assembling provider_features …")
    con.execute("""
        CREATE OR REPLACE TABLE provider_features AS
        SELECT * FROM f_core
        LEFT JOIN f_conc          USING (npi)
        LEFT JOIN f_peer_tax      USING (npi)
        LEFT JOIN f_peer_taxstate USING (npi)
        LEFT JOIN f_temporal      USING (npi)
        LEFT JOIN f_mismatch      USING (npi)
        LEFT JOIN f_linkage       USING (npi)
    """)
    require("provider_features_one_row_per_npi", _unique_npi(con, "provider_features"))
    require("provider_features_rowcount_eq_distinct_npi",
            _n(con, "provider_features") == expected,
            f"{_n(con,'provider_features')} vs {expected}")
    feat_path = out_dir / "provider_features.parquet"
    con.execute(f"COPY provider_features TO '{feat_path}' (FORMAT PARQUET)")

    # ---------------------------------------------------------------- #
    # Report
    # ---------------------------------------------------------------- #
    log("Writing FEATURES_REPORT.md …")
    rep.append("\n## Correctness handling\n"
               "- **TOTAL_PATIENTS is NOT distinct patients across rows.** A patient recurs across "
               "codes/months, so `SUM(TOTAL_PATIENTS)` is exposed as `service_volume` (a "
               "service-volume proxy). All per-patient ratios are 'per patient-service-instance', "
               "NOT per distinct patient.\n"
               "- **Negatives are reversals/adjustments.** `gross_paid` = sum of positives, "
               "`net_paid` = sum of all, `reversal_amount` = sum of negatives, `reversal_ratio` = "
               "|reversal| / gross. Negatives never silently cancel volume.\n"
               f"- **Claims run-out:** data spans {dmin} … {dmax}; temporal/growth features use only "
               f"MATURE months (month_date <= {mature_cutoff}, i.e. the final {MATURITY_MONTHS} "
               "months excluded). Lifetime aggregates use ALL months.\n"
               f"- **Robust peers:** baselines use median + MAD (robust z = 1.4826·(x−med)/MAD), "
               f"not mean/stddev. Groups with < {MIN_PEER_GROUP} members get NULL scores and "
               "`peer_group_too_small = TRUE`.\n"
               "- **Concentration** (top_hcpcs_paid_share, hcpcs_hhi) is computed over positive "
               "(gross) dollars per code to avoid negative-share artifacts.\n"
               f"- **Specialty-vs-code proxy** flags codes billed by < {RARE_CODE_THRESHOLD:.0%} of "
               "a taxonomy's providers; `rare_for_taxonomy_paid_share` is a PROXY, not a verdict.\n")

    # numeric feature summaries
    summ_feats = ["gross_paid", "net_paid", "reversal_amount", "reversal_ratio",
                  "total_claim_lines", "service_volume", "n_distinct_hcpcs", "n_active_months",
                  "tenure_months", "paid_per_claim_line", "paid_per_patient_instance",
                  "lines_per_patient_instance", "top_hcpcs_paid_share", "hcpcs_hhi",
                  "net_paid_rz_tax", "net_paid_rz_taxstate", "rare_for_taxonomy_paid_share",
                  "yoy_growth_net_paid", "month_to_month_volatility", "max_single_month_net_paid"]
    rep.append("\n## Numeric feature summaries (one row per billing NPI)\n")
    rep.append("| feature | non-null | nulls | median | p90 | p99 |\n|---|--:|--:|--:|--:|--:|\n")
    total_rows = _n(con, "provider_features")
    for f in summ_feats:
        r = con.execute(f"""
            SELECT COUNT({f}) nn, COUNT(*)-COUNT({f}) nu,
                   approx_quantile({f},0.5) p50, approx_quantile({f},0.9) p90,
                   approx_quantile({f},0.99) p99
            FROM provider_features WHERE {f} IS NOT NULL
        """).df().iloc[0]
        nn = con.execute(f"SELECT COUNT({f}) FROM provider_features").fetchone()[0]
        fmt = lambda v: "—" if v is None else (f"{v:,.4f}" if abs(v) < 1000 else f"{v:,.0f}")
        rep.append(f"| `{f}` | {nn:,} | {total_rows-nn:,} | {fmt(r.p50)} | {fmt(r.p90)} | {fmt(r.p99)} |\n")

    # peer-group coverage
    rep.append("\n## Peer-group coverage (too-small groups get NULL scores)\n")
    for suf, label in [("tax", "taxonomy"), ("taxstate", "taxonomy × state")]:
        small = con.execute(f"SELECT COUNT(*) FROM provider_features WHERE peer_group_too_small_{suf}").fetchone()[0]
        rep.append(f"- {label}: {small:,} of {total_rows:,} providers in groups < {MIN_PEER_GROUP} "
                   f"({small/total_rows:.1%}) → not confidently scored\n")

    # linkage match counts
    lk = con.execute("""
        SELECT COUNT(*) FILTER (WHERE provider_on_leie)                       AS on_leie,
               COUNT(*) FILTER (WHERE facility_has_excluded_owner_high)       AS fac_high,
               COUNT(*) FILTER (WHERE facility_has_excluded_owner_probable)   AS fac_prob
        FROM provider_features
    """).df().iloc[0]
    rep.append("\n## Linkage match counts (tiers kept separate)\n"
               f"- `provider_on_leie` (exact NPI, high confidence): {int(lk.on_leie):,}\n"
               f"- `facility_has_excluded_owner_high`: {int(lk.fac_high):,}\n"
               f"- `facility_has_excluded_owner_probable`: {int(lk.fac_prob):,}\n")

    rep.append("\n## Integrity\n"
               f"- provider_features rows = {total_rows:,} = distinct billing NPIs ({expected:,}) ✓\n"
               "- every linkage join asserted non-fan-out (row count unchanged) ✓\n"
               "- spending_fact.parquet: unchanged (read-only).\n")

    (out_dir / "FEATURES_REPORT.md").write_text("".join(rep))
    con.close()
    log(f"Done. Outputs in {out_dir}")
    log(f"  provider_features: {total_rows:,} rows (one per billing NPI)")
    log(f"  on_leie={int(lk.on_leie):,}  facility_excl_high={int(lk.fac_high):,}  "
        f"facility_excl_probable={int(lk.fac_prob):,}")


if __name__ == "__main__":
    main()
