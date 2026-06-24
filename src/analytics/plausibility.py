"""
plausibility.py — clinical-plausibility score, data-derived (expansion plan A5).

The manifesto's clinical-plausibility sub-score is "specialty-to-code
compatibility, volume vs local denominator." We derive the compatibility FROM
the data instead of licensing a code-to-specialty table: a HCPCS billed by a
tiny fraction of an organization's taxonomy peers is implausible FOR THAT
SPECIALTY (a hospice billing surgical codes, a lab billing home-visit codes).
This generalizes v3's ``rare_share_te`` seed into a proper per-code prevalence
matrix with dollar weighting and named drivers.

Two outputs:

  code_prevalence_matrix   per (taxonomy_code, hcpcs): the fraction of that
                           taxonomy's providers who bill the code — its
                           prevalence/plausibility for the specialty. Taxonomies
                           with too few providers can't anchor a prevalence, so
                           they're marked and excluded from scoring (a code
                           billed by the only provider in a taxonomy is 100%
                           "prevalent" and 0% informative).
  org_clinical_plausibility  per org: the dollar-weighted share of billing on
                           codes that are RARE (prevalence < threshold) for the
                           billing provider's own taxonomy — plus a named driver
                           string ("$2.1M on T1019, billed by 0.3% of 251E
                           peers"). Dollars in unjudgeable thin taxonomies are
                           reported separately, never counted as implausible.

The "volume vs local denominator" half of the manifesto's spec (billing rate
vs county population) waits on the Census county file (expansion plan B6); this
module builds the specialty-compatibility half, which needs no external data.

Percentile-ranked output (``clinical_implausibility``) feeds the
``specialty_mismatch`` scheme alongside the v3 concept; one-sided and global,
because an implausible code mix is alarming in any specialty.
"""

from __future__ import annotations

import pandas as pd

RARE_THRESHOLD = 0.01          # billed by <1% of taxonomy peers = implausible
MIN_TAXONOMY_PROVIDERS = 5     # below this a taxonomy can't anchor a prevalence
MAX_DRIVERS = 3                # named implausible codes per org on the dossier


def org_clinical_plausibility_from_parquet(spending_path: str,
                                           provider_dim: pd.DataFrame,
                                           npi_to_org: pd.DataFrame,
                                           **kwargs) -> pd.DataFrame:
    """Scale-safe clinical plausibility: DuckDB collapses the spending fact to the
    (billing_npi, hcpcs) grain it needs (dropping the month dimension that bloats
    the row count) BEFORE pandas touches it, then the tested per-org logic runs on
    the small rollup. Sums and distinct-provider counts are preserved, so the
    output equals ``org_clinical_plausibility`` on the raw fact."""
    import duckdb
    con = duckdb.connect()
    p = str(spending_path).replace("'", "''")
    agg = con.execute(f"""
        SELECT CAST(billing_npi AS VARCHAR) AS billing_npi,
               UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) AS hcpcs_code,
               SUM(CAST(total_paid AS DOUBLE)) AS total_paid
        FROM read_parquet('{p}')
        GROUP BY 1, 2
    """).df()
    con.close()
    return org_clinical_plausibility(agg, provider_dim, npi_to_org, **kwargs)


def _prep_spending(spending: pd.DataFrame) -> pd.DataFrame:
    s = spending.copy()
    s["billing_npi"] = s["billing_npi"].astype(str)
    s["hcpcs"] = s.get("hcpcs_code", "").fillna("").astype(str).str.strip().str.upper()
    s["total_paid"] = pd.to_numeric(s["total_paid"], errors="coerce").fillna(0.0)
    return s[s["hcpcs"] != ""]


def code_prevalence_matrix(spending: pd.DataFrame, provider_dim: pd.DataFrame,
                           min_taxonomy_providers: int = MIN_TAXONOMY_PROVIDERS
                           ) -> pd.DataFrame:
    """Per (taxonomy_code, hcpcs) prevalence = share of the taxonomy's providers
    that bill the code. Adds ``taxonomy_n`` (providers in the taxonomy) and
    ``assessable`` (taxonomy has enough providers to judge).
    """
    s = _prep_spending(spending)
    pdim = provider_dim[["npi", "taxonomy_code"]].copy()
    pdim["npi"] = pdim["npi"].astype(str)
    pdim["taxonomy_code"] = pdim["taxonomy_code"].fillna("").astype(str)

    s = s.merge(pdim, left_on="billing_npi", right_on="npi", how="inner")
    s = s[s["taxonomy_code"] != ""]
    if not len(s):
        return pd.DataFrame(columns=["taxonomy_code", "hcpcs", "n_billing",
                                     "taxonomy_n", "prevalence", "assessable"])

    taxonomy_n = s.groupby("taxonomy_code")["billing_npi"].nunique()
    n_billing = (s.groupby(["taxonomy_code", "hcpcs"])["billing_npi"]
                 .nunique().rename("n_billing").reset_index())
    n_billing["taxonomy_n"] = n_billing["taxonomy_code"].map(taxonomy_n)
    n_billing["prevalence"] = n_billing["n_billing"] / n_billing["taxonomy_n"]
    n_billing["assessable"] = n_billing["taxonomy_n"] >= min_taxonomy_providers
    return n_billing


def org_clinical_plausibility(spending: pd.DataFrame, provider_dim: pd.DataFrame,
                              npi_to_org: pd.DataFrame,
                              prevalence: pd.DataFrame | None = None,
                              rare_threshold: float = RARE_THRESHOLD,
                              min_taxonomy_providers: int = MIN_TAXONOMY_PROVIDERS
                              ) -> pd.DataFrame:
    """Per-org implausible-billing share with named drivers.

    Returns one row per org_node_id:
      total_payments            org's total billing seen here
      assessable_payments       billing in taxonomies large enough to judge
      implausible_payments      billing on codes rare for the biller's taxonomy
      implausible_dollar_share  implausible / assessable  (NaN if none assessable)
      clinical_implausibility_driver  named top codes ("$2.1M on T1019, 0.3% of 251E")
    """
    if prevalence is None:
        prevalence = code_prevalence_matrix(spending, provider_dim,
                                             min_taxonomy_providers)
    s = _prep_spending(spending)
    pdim = provider_dim[["npi", "taxonomy_code"]].copy()
    pdim["npi"] = pdim["npi"].astype(str)
    pdim["taxonomy_code"] = pdim["taxonomy_code"].fillna("").astype(str)

    xw_npis = npi_to_org["npi"].astype(str)
    assert xw_npis.is_unique, "npi_to_org has duplicate NPIs (ambiguous attribution)"
    npi2org = dict(zip(xw_npis, npi_to_org["org_node_id"].astype(str)))

    s = s.merge(pdim, left_on="billing_npi", right_on="npi", how="inner")
    s["org_node_id"] = s["billing_npi"].map(npi2org)
    s = s[s["org_node_id"].notna() & (s["taxonomy_code"] != "")]
    if not len(s):
        return pd.DataFrame(columns=["org_node_id", "total_payments",
                                     "assessable_payments", "implausible_payments",
                                     "implausible_dollar_share",
                                     "clinical_implausibility_driver"])

    prev = prevalence[["taxonomy_code", "hcpcs", "prevalence", "assessable"]]
    s = s.merge(prev, on=["taxonomy_code", "hcpcs"], how="left")
    # a code never seen in the taxonomy is maximally implausible (prevalence 0),
    # but only judgeable if the taxonomy itself is assessable
    s["prevalence"] = s["prevalence"].fillna(0.0)
    s["assessable"] = s["assessable"].fillna(False)
    s["is_implausible"] = s["assessable"] & (s["prevalence"] < rare_threshold)

    rows = []
    for org, g in s.groupby("org_node_id"):
        total = float(g["total_paid"].sum())
        assessable_paid = float(g.loc[g["assessable"], "total_paid"].sum())
        impl = g[g["is_implausible"]]
        impl_paid = float(impl["total_paid"].sum())
        share = (impl_paid / assessable_paid) if assessable_paid > 0 else float("nan")

        driver = ""
        if len(impl):
            by_code = (impl.groupby(["hcpcs", "taxonomy_code"])
                       .agg(paid=("total_paid", "sum"),
                            prevalence=("prevalence", "min")).reset_index()
                       .sort_values("paid", ascending=False).head(MAX_DRIVERS))
            parts = [f"${r.paid:,.0f} on {r.hcpcs} "
                     f"(billed by {r.prevalence:.1%} of {r.taxonomy_code} peers)"
                     for r in by_code.itertuples()]
            driver = "; ".join(parts)

        rows.append({"org_node_id": org, "total_payments": total,
                     "assessable_payments": assessable_paid,
                     "implausible_payments": impl_paid,
                     "implausible_dollar_share": share,
                     "clinical_implausibility_driver": driver})
    return pd.DataFrame(rows)


def local_denominator_plausibility(org_volume_by_fips: pd.DataFrame,
                                   county_pop: pd.DataFrame,
                                   volume_col: str = "volume") -> pd.DataFrame:
    """The manifesto's "volume vs local denominator" — A5's second half.

    An org billing more services/beneficiaries per capita than its county's
    population plausibly supports is the phantom-patient shape. ``org_volume_by_
    fips``: org_node_id, fips, ``volume`` (services or distinct beneficiaries the
    org bills in that county — assembled once an org→county mapping exists via
    the Census ZIP→county crosswalk). ``county_pop``: fips, population. Returns
    org_node_id, per_capita_rate, and ``local_volume_implausibility`` (one-sided
    global percentile — the registry feature). Counties with no population are
    NaN (unjudgeable, never forced).
    """
    out_cols = ["org_node_id", "per_capita_rate", "local_volume_implausibility"]
    v = org_volume_by_fips.copy()
    v["fips"] = v["fips"].astype(str)
    v[volume_col] = pd.to_numeric(v[volume_col], errors="coerce").fillna(0.0)
    pop = dict(zip(county_pop["fips"].astype(str),
                   pd.to_numeric(county_pop["population"], errors="coerce")))
    v["population"] = v["fips"].map(pop)
    # an org may bill across counties: total volume / total population it covers
    g = v.groupby("org_node_id").agg(volume=(volume_col, "sum"),
                                     population=("population", "sum"))
    g["per_capita_rate"] = (g["volume"] / g["population"]).where(g["population"] > 0)
    g["local_volume_implausibility"] = g["per_capita_rate"].rank(
        method="average", pct=True)
    return g.reset_index()[out_cols]


def plausibility_percentiles(org_plausibility: pd.DataFrame) -> pd.DataFrame:
    """Implausible-dollar-share → global one-sided percentile (the registry input
    ``clinical_implausibility``). NaN shares (nothing assessable) stay NaN —
    unscored, never force-ranked to zero.
    """
    out = org_plausibility[["org_node_id"]].copy()
    if "implausible_dollar_share" in org_plausibility.columns:
        out["clinical_implausibility"] = (
            org_plausibility["implausible_dollar_share"].rank(method="average",
                                                              pct=True))
    if "clinical_implausibility_driver" in org_plausibility.columns:
        out["clinical_implausibility_driver"] = \
            org_plausibility["clinical_implausibility_driver"]
    return out
