"""
servicing_grain.py — the RENDERING clinician's view of the Medicaid spending fact.

Why this exists (the 2026-10-08 accuracy audit): every feature in the matrix was
computed per BILLING NPI, and the servicing (rendering) NPI on each claim line was
carried as an attribute nobody read. The forward-label diagnostic on the v7 run
(LABEL_LAG.md) found only 20 percent of the FCA-shaped positives inside the scored
universe: a convicted physician usually renders under a group's or a hospital's
billing NPI, so the person the label names never had a row. The model could not
rank what it never saw. This module gives every rendering NPI its own stats and its
own row, and adds the signals the rendering grain alone can carry:

  sv_n_billing_orgs          distinct billing NPIs this clinician rendered under
  sv_max_orgs_same_month     the most billing NPIs in ONE month (a clinician whose
                             NPI appears on five organizations' claims in the same
                             month is moonlighting at best, lending an identity at
                             worst: the phantom-rendering shape)
  sv_peak_lines_month        the busiest month's claim lines (an individual NPI is one
                             person; a peak nobody can render is the impossible-day
                             shape at month grain)
  sv_top_billing_share       share of this clinician's rendered dollars at one biller
  sv_paid_under_others       dollars rendered under a DIFFERENT billing NPI
  sv_share_under_others      same, as a share of everything rendered
  sv_paid_per_patient_month, sv_lines_per_active_month, sv_distinct_hcpcs,
  sv_active_months, sv_tenure_months, sv_first_month, sv_last_month

THE PATIENT-MONTH BASE (2026-11-02, architecture audit C4): ``sv_patient_months`` is
SUM(patients) over the rendering NPI's (billing NPI x code x month) rows, so a patient
seen on two codes (or under two billers) in one month counted twice and the dollars
per patient-month under-read. Distinct patients cannot be recovered from aggregate
rows; ``sv_patient_months_max`` = SUM over months of MAX(patients over that month's
rows) is the best available base (a lower bound on distinct patient-months), and
``sv_paid_per_patient_month`` now = POSITIVE-only rendered dollars over it (an upper
bound on the true intensity, the one-sided direction the scheme reads). The old
formula (net dollars over the code-month sum) is kept as
``sv_paid_per_patient_month_codemonth``.

THE BILLER'S SERVICING MIX (2026-11-05, ``billing_servicing_mix``; export source
``servicing_mix``, its own partition group so a run grades it on its own). The sv_*
columns above describe the CLINICIAN; these three describe the BILLING provider: the
share of its gross (positive) paid dollars that went through servicing NPIs of a kind.
Every numerator leaves out self-rendered rows (servicing NPI = billing NPI) and rows
with no servicing NPI; the denominator is every positive dollar the biller was paid.

  srv_hub_share           dollars under servicing NPIs that appear under 10 or more
                          distinct billing NPIs in the window (THIS biller counted
                          among the 10; the clinician's own NPI billing for itself is
                          not a biller it "shows up under")
  srv_out_of_state_share  dollars under servicing NPIs whose NPPES practice state
                          differs from the biller's NPPES practice state (rows where
                          either state is unknown never count; NULL when the
                          biller's own state is unknown)
  srv_inactive_share      dollars under servicing NPIs deactivated in NPPES on/before
                          the first day of the service month and not reactivated by
                          then (the integrate ``active_at_claim`` month rule; a
                          reactivation dated before the deactivation is an older
                          cycle and does not count, as in provider_dim.is_active)

NULL (never 0) for a biller with no positive dollars, and srv_inactive_share is NULL
everywhere when provider_dim carries no deactivation date. No exclusion list is read.
KNOWN LIMITS (for the reviewer): provider_dim is TODAY's NPPES, so on a frozen run a
clinician who moved states after the cutoff reads with the new state (the export has
no as-of provider_dim; the graph's --asof-nppes covers only the graph), and NPPES keeps
one deactivation / reactivation pair per NPI, so an earlier cycle is invisible.

Point-in-time: pass the as-of spending parquet (already filtered to pre-cutoff
months) and nothing here can see the future. Dollars are NEVER re-attributed: the
billing NPI keeps the money (CLAUDE.md rule 3); these are per-clinician views of the
same rows, and the matrix row for a servicing-only NPI carries its rendered totals
under PROVIDER_STATS names so peer normalization and the twin residual treat it like
any other row, with ``row_grain = "servicing"`` saying what it is.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

SV_COLS = ["sv_paid_total", "sv_rows", "sv_lines", "sv_patient_months", "sv_patient_months_max",
           "sv_distinct_hcpcs",
           "sv_active_months", "sv_first_month", "sv_last_month", "sv_tenure_months",
           "sv_n_billing_orgs", "sv_top_billing_share", "sv_paid_under_others",
           "sv_share_under_others", "sv_max_orgs_same_month", "sv_peak_lines_month",
           "sv_lines_per_active_month", "sv_paid_per_patient_month",
           "sv_paid_per_patient_month_codemonth"]
# the ones the scheme engine reads (peer-normalized one-sided, higher = more unusual)
SV_SIGNALS = ["sv_max_orgs_same_month", "sv_peak_lines_month", "sv_n_billing_orgs",
              "sv_paid_per_patient_month", "sv_lines_per_active_month"]


def _pq(p) -> str:
    return str(p).replace("\\", "/").replace("'", "''")


def servicing_stats(spending_path: str | Path, cutoff: str | None = None,
                    con=None) -> pd.DataFrame:
    """Per SERVICING NPI stats (DuckDB-streamed; the fact never enters pandas).
    ``cutoff`` ("YYYY-MM") keeps service months strictly before it; pass the as-of
    parquet instead on frozen runs (then cutoff is redundant but harmless)."""
    from src.attempt_2.duck import connect as _duck
    con = con or _duck()
    where = (f"AND substr(CAST(service_month AS VARCHAR), 1, 7) < '{cutoff[:7]}'" if cutoff else "")
    df = con.execute(f"""
        WITH s AS (
            SELECT CAST(servicing_npi AS VARCHAR) sv, CAST(billing_npi AS VARCHAR) bn,
                   UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) hcpcs,
                   substr(CAST(service_month AS VARCHAR), 1, 7) ym,
                   CAST(total_paid AS DOUBLE) paid,
                   CAST(total_claim_lines AS DOUBLE) lines,
                   CAST(total_patients AS DOUBLE) pts
            FROM read_parquet('{_pq(spending_path)}')
            WHERE servicing_npi IS NOT NULL AND length(CAST(servicing_npi AS VARCHAR)) = 10
                  AND COALESCE(total_paid, 0) <= 500000000 {where}),
        -- shares are built on POSITIVE dollars only: refund / recoupment rows carry
        -- negative total_paid and pushed sv_top_billing_share and
        -- sv_share_under_others outside [0, 1] on v8 (EXPECTATIONS FAILs), so the
        -- gross paid per billing org is the denominator and the result is clipped
        by_bn AS (SELECT sv, bn, SUM(paid) paid, SUM(GREATEST(paid, 0)) gross FROM s GROUP BY 1, 2),
        tot AS (SELECT sv, SUM(gross) tg FROM by_bn GROUP BY 1),
        conc AS (
            SELECT b.sv, COUNT(DISTINCT b.bn) AS sv_n_billing_orgs,
                   LEAST(1.0, GREATEST(0.0, MAX(b.gross) / NULLIF(MAX(t.tg), 0))) AS sv_top_billing_share,
                   SUM(CASE WHEN b.bn IS DISTINCT FROM b.sv THEN b.paid ELSE 0 END) AS sv_paid_under_others,
                   LEAST(1.0, GREATEST(0.0,
                       SUM(CASE WHEN b.bn IS DISTINCT FROM b.sv THEN b.gross ELSE 0 END)
                       / NULLIF(MAX(t.tg), 0))) AS sv_share_under_others
            FROM by_bn b JOIN tot t USING (sv) GROUP BY b.sv),
        -- audit C4: MAX(patients) over the month's rows = the month's lower bound on
        -- distinct patients; summed over months it is sv_patient_months_max
        by_month AS (SELECT sv, ym, COUNT(DISTINCT bn) n_orgs, SUM(lines) lines, MAX(pts) pts_max
                     FROM s GROUP BY 1, 2),
        peaks AS (SELECT sv, MAX(n_orgs) AS sv_max_orgs_same_month, MAX(lines) AS sv_peak_lines_month,
                         SUM(pts_max) AS sv_patient_months_max
                  FROM by_month GROUP BY 1),
        base AS (
            SELECT sv, SUM(paid) sv_paid_total, COUNT(*) sv_rows, SUM(lines) sv_lines,
                   SUM(pts) sv_patient_months, COUNT(DISTINCT hcpcs) sv_distinct_hcpcs,
                   COUNT(DISTINCT ym) sv_active_months, MIN(ym) sv_first_month, MAX(ym) sv_last_month
            FROM s GROUP BY 1)
        SELECT b.sv AS npi, b.sv_paid_total, b.sv_rows, b.sv_lines, b.sv_patient_months,
               p.sv_patient_months_max,
               b.sv_distinct_hcpcs, b.sv_active_months, b.sv_first_month, b.sv_last_month,
               c.sv_n_billing_orgs, c.sv_top_billing_share, c.sv_paid_under_others,
               c.sv_share_under_others,
               p.sv_max_orgs_same_month, p.sv_peak_lines_month,
               b.sv_lines / NULLIF(b.sv_active_months, 0) AS sv_lines_per_active_month,
               -- audit C4: positive-only dollars over the max-based patient-months;
               -- the old net-over-code-month formula keeps the _codemonth suffix
               t.tg / NULLIF(p.sv_patient_months_max, 0) AS sv_paid_per_patient_month,
               b.sv_paid_total / NULLIF(b.sv_patient_months, 0) AS sv_paid_per_patient_month_codemonth
        FROM base b LEFT JOIN conc c ON c.sv = b.sv LEFT JOIN peaks p ON p.sv = b.sv
             LEFT JOIN tot t ON t.sv = b.sv
    """).df()
    df["npi"] = df["npi"].astype(str)
    if len(df):
        fm = pd.to_datetime(df["sv_first_month"], errors="coerce")
        lm = pd.to_datetime(df["sv_last_month"], errors="coerce")
        df["sv_tenure_months"] = ((lm.dt.year - fm.dt.year) * 12 + (lm.dt.month - fm.dt.month) + 1).clip(lower=0)
    else:
        df["sv_tenure_months"] = pd.Series(dtype=float)
    return df[["npi"] + SV_COLS]


SRV_COLS = ["srv_hub_share", "srv_out_of_state_share", "srv_inactive_share"]
HUB_MIN_BILLERS = 10


def _dim_select(cols) -> str:
    """The provider_dim columns the mix reads, NULL-typed when the table lacks one."""
    cols = set(cols)
    st = next((c for c in ("addr_state", "practice_state", "state") if c in cols), None)
    parts = ["CAST(npi AS VARCHAR) AS npi",
             (f"NULLIF(UPPER(TRIM(CAST({st} AS VARCHAR))), '') AS st" if st else "CAST(NULL AS VARCHAR) AS st"),
             ("TRY_CAST(deactivation_date AS DATE) AS deact" if "deactivation_date" in cols
              else "CAST(NULL AS DATE) AS deact"),
             ("TRY_CAST(reactivation_date AS DATE) AS react" if "reactivation_date" in cols
              else "CAST(NULL AS DATE) AS react")]
    return ", ".join(parts)


def billing_servicing_mix(spending_path: str | Path, provider_dim, cutoff: str | None = None,
                          con=None, hub_min_billers: int = HUB_MIN_BILLERS) -> pd.DataFrame:
    """Per BILLING NPI: ``npi`` + SRV_COLS (see the module docstring). DuckDB-streamed;
    ``provider_dim`` is a parquet path or a DataFrame (npi, addr_state,
    deactivation_date, reactivation_date). ``cutoff`` ("YYYY-MM") keeps service months
    strictly before it; frozen runs pass the as-of parquet instead."""
    from src.attempt_2.duck import connect as _duck
    con = con or _duck()
    if isinstance(provider_dim, pd.DataFrame):
        con.register("_srv_pdim", provider_dim)
        dim_src, dim_cols = "_srv_pdim", list(provider_dim.columns)
    else:
        dim_src = f"read_parquet('{_pq(provider_dim)}')"
        dim_cols = list(con.execute(f"SELECT * FROM {dim_src} LIMIT 0").df().columns)
    has_deact = "deactivation_date" in dim_cols
    where = (f"AND substr(CAST(service_month AS VARCHAR), 1, 7) < '{cutoff[:7]}'" if cutoff else "")
    df = con.execute(f"""
        WITH s AS (
            SELECT CAST(billing_npi AS VARCHAR) bn,
                   CASE WHEN length(CAST(servicing_npi AS VARCHAR)) = 10
                        THEN CAST(servicing_npi AS VARCHAR) END sv,
                   TRY_CAST(substr(CAST(service_month AS VARCHAR), 1, 7) || '-01' AS DATE) m0,
                   GREATEST(CAST(total_paid AS DOUBLE), 0) gross
            FROM read_parquet('{_pq(spending_path)}')
            WHERE billing_npi IS NOT NULL AND length(CAST(billing_npi AS VARCHAR)) = 10
                  AND COALESCE(total_paid, 0) <= 500000000 {where}),
        dim AS (SELECT * FROM (SELECT {_dim_select(dim_cols)},
                                      row_number() OVER (PARTITION BY CAST(npi AS VARCHAR)) rn
                               FROM {dim_src}) WHERE rn = 1),
        -- distinct billers per clinician, its own NPI billing for itself left out:
        -- THIS biller is one of them whenever the clinician renders for it
        hub AS (SELECT sv, COUNT(DISTINCT bn) n_billers FROM s
                WHERE sv IS NOT NULL AND sv <> bn GROUP BY 1),
        r AS (
            SELECT s.bn, s.gross, (s.sv IS NOT NULL AND s.sv <> s.bn) AS other,
                   h.n_billers, b.st AS b_st, v.st AS v_st,
                   (v.deact IS NOT NULL AND v.deact <= s.m0
                    AND NOT (v.react IS NOT NULL AND v.react >= v.deact AND v.react <= s.m0)) AS inactive
            FROM s LEFT JOIN hub h ON h.sv = s.sv
                   LEFT JOIN dim b ON b.npi = s.bn
                   LEFT JOIN dim v ON v.npi = s.sv)
        SELECT bn AS npi,
               SUM(gross) AS tg,
               MAX(b_st) AS b_st,
               SUM(CASE WHEN other AND n_billers >= {int(hub_min_billers)} THEN gross ELSE 0 END) AS hub,
               SUM(CASE WHEN other AND b_st IS NOT NULL AND v_st IS NOT NULL AND b_st <> v_st
                        THEN gross ELSE 0 END) AS oos,
               SUM(CASE WHEN other AND inactive THEN gross ELSE 0 END) AS inact
        FROM r GROUP BY bn
    """).df()
    out = pd.DataFrame({"npi": df["npi"].astype(str)})
    tg = df["tg"].where(df["tg"] > 0)

    def _share(num, mask=None):
        v = (num / tg).clip(0.0, 1.0)
        return v.where(mask) if mask is not None else v

    out["srv_hub_share"] = _share(df["hub"])
    out["srv_out_of_state_share"] = _share(df["oos"], df["b_st"].notna())
    out["srv_inactive_share"] = _share(df["inact"]) if has_deact else float("nan")
    for c in SRV_COLS:
        out[c] = out[c].astype(float)
    return out[["npi"] + SRV_COLS].reset_index(drop=True)


def servicing_only_rows(stats: pd.DataFrame, billing_npis, provider_dim: pd.DataFrame,
                        positive_npis=None, min_paid: float = 0.0) -> pd.DataFrame:
    """Matrix rows for rendering NPIs that have NO billing-NPI row: identifiers from
    provider_dim, PROVIDER_STATS mapped from the rendered totals, ``row_grain`` =
    'servicing'. Only NPIs present in provider_dim (a valid, enrolled NPI) qualify;
    ``positive_npis`` sets provider_on_leie for the rows the exclusion lists name."""
    have = set(str(n) for n in billing_npis)
    s = stats[~stats["npi"].isin(have) & (stats["sv_paid_total"] > min_paid)].copy()
    if not len(s):
        return pd.DataFrame()
    d = provider_dim.copy()
    d["npi"] = d["npi"].astype(str)
    keep = {"npi": "npi", "entity_type": "entity_type", "taxonomy_code": "primary_taxonomy",
            "addr_state": "practice_state", "org_legal_name": "org_legal_name"}
    d = d[[c for c in keep if c in d.columns]].rename(columns=keep).drop_duplicates("npi")
    out = s.merge(d, on="npi", how="inner")
    out["gross_paid"] = out["sv_paid_total"]
    out["net_paid"] = out["sv_paid_total"]
    out["service_volume"] = out["sv_rows"]
    out["total_claim_lines"] = out["sv_lines"]
    out["n_distinct_hcpcs"] = out["sv_distinct_hcpcs"]
    out["n_active_months"] = out["sv_active_months"]
    out["tenure_months"] = out["sv_tenure_months"]
    out["first_service_month"] = out["sv_first_month"]
    out["last_service_month"] = out["sv_last_month"]
    pos = set(str(n) for n in (positive_npis or []))
    out["provider_on_leie"] = out["npi"].isin(pos).astype(int)
    out["row_grain"] = "servicing"
    id_cols = ["npi", "entity_type", "primary_taxonomy", "practice_state", "org_legal_name", "row_grain",
               "gross_paid", "net_paid", "service_volume", "total_claim_lines", "n_distinct_hcpcs",
               "n_active_months", "tenure_months", "first_service_month", "last_service_month",
               "provider_on_leie"]
    return out[[c for c in id_cols if c in out.columns]].reset_index(drop=True)


def write_report(stats: pd.DataFrame, extra: pd.DataFrame, out_path: Path, n_billing: int) -> None:
    L = ["# SERVICING GRAIN — the rendering clinician's view", "",
         f"- rendering NPIs with claims: {len(stats):,}; billing-NPI universe: {n_billing:,}; "
         f"rendering NPIs WITHOUT a billing row (new matrix rows): {len(extra):,}",
         f"- rendered dollars on the new rows: ${extra['net_paid'].sum():,.0f}" if len(extra) else "- no new rows",
         f"- rendering NPIs seen under 2+ billing organizations: {int((stats['sv_n_billing_orgs'] >= 2).sum()):,}; "
         f"under 5+ in a single month: {int((stats['sv_max_orgs_same_month'] >= 5).sum()):,}",
         f"- peak monthly lines p99 across rendering NPIs: {stats['sv_peak_lines_month'].quantile(0.99):,.0f}" if len(stats) else "",
         "", "_A clinician rendering for several organizations is ordinary (locums, hospitalists, "
         "telehealth groups); the signal is the combination of many organizations in one month with "
         "a volume one person cannot render. Leads for review, never findings._"]
    out_path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spending", required=True)
    ap.add_argument("--provider-dim", required=True)
    ap.add_argument("--leads", default=None, help="fraud_leads_v3.parquet (the billing universe); optional")
    ap.add_argument("--cutoff", default=None)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stats = servicing_stats(a.spending, cutoff=a.cutoff)
    stats.to_parquet(out / "servicing_stats.parquet", index=False)
    billing = set(pd.read_parquet(a.leads, columns=["npi"])["npi"].astype(str)) if a.leads else set()
    pdim = pd.read_parquet(a.provider_dim)
    extra = servicing_only_rows(stats, billing, pdim)
    extra.to_parquet(out / "servicing_only_rows.parquet", index=False)
    write_report(stats, extra, out / "SERVICING_GRAIN.md", len(billing))
    print(f"[servicing_grain] {len(stats):,} rendering NPIs; {len(extra):,} servicing-only rows -> {out}")


if __name__ == "__main__":
    main()
