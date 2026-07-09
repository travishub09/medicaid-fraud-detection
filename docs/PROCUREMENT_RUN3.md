# Run 3 procurement — every file to fetch, in value order

The plan for the full rerun: run `data_audit.py` first (it tells you which of
these you already have, with the right layout). Then work this list top to
bottom. Every item is a free public download unless marked PAID. Save paths are
exact — the export's file-finders look in `preclean/<source>/`.

Full URL detail lives in `docs/DATA_ACQUISITION_GUIDE.md`; this is the
prioritized operator checklist for Run 3.

## Tier 0 — refresh what the frozen test depends on (do first, 20 min)

| # | File | Why it matters for Run 3 |
|---|---|---|
| 0.1 | **LEIE update** — https://oig.hhs.gov/exclusions/exclusions_list.asp → monthly CSV → `preclean/` (replace the old one) | The forward label = bans after 2023-12. A stale LEIE means fewer positives and wider error bars on Travis's test. |
| 0.2 | **OpenSanctions** — already downloaded per Trey → confirm at `preclean/opensanctions/targets.simple.csv`, then run `make opensanctions` | Adds ~45 state Medicaid exclusion lists → more forward positives → tighter test. |

## Tier 1 — free, each one lights up a scheme (an afternoon)

| # | Get | Save to | Unlocks |
|---|---|---|---|
| 1.1 | **NUCC taxonomy** — nucc.org → provider-taxonomy CSV | `preclean/nucc/nucc_taxonomy.csv` | Coherent peer grouping — **improves every scheme's percentile**. The single highest-value 2-minute download in this list. |
| 1.2 | **Medicare Part B, by Provider AND Service** — data.cms.gov, 3–5 annual CSVs | `preclean/partb/partb_<year>.csv` | upcoding (E&M level distribution) |
| 1.3 | **Medicare Part D, by Provider AND Drug** — data.cms.gov | `preclean/partd/partd_<year>.csv` | drug_outlier + half of pharma_kickback |
| 1.4 | **CMS opioid metrics** | `preclean/opioid/opioid.csv` | pill_mill |
| 1.5 | **Open Payments** general payments, latest 3 years | `preclean/open_payments/open_payments.csv` | pharma_kickback (pairs with 1.3) |
| 1.6 | **DMEPOS by Referring Provider AND Service** (NOT the plain by-referring summary — the audit checks for the HCPCS column) | `preclean/dmepos/dmepos.csv` | dme_ring + the influenced-dollars exposure for referrers |
| 1.7 | **Order & Referring** | `preclean/order_referring/order_referring.csv` | ineligible-referral share |
| 1.8 | **NPPES deactivation report** (.zip fine as-is) | `preclean/nppes_deactivation/deactivation.zip` | invalid_identity / billing-after-deactivation |
| 1.9 | **Medicare revocations** | `preclean/revocations/revocations.csv` | widens the PU label (more positives) |
| 1.10 | **Market Saturation & Utilization** | `preclean/saturation/saturation.csv` | saturation_fraud |
| 1.11 | **HRSA 340B OPAIS daily report** (.xlsx as-is; `pip install openpyxl`) | `preclean/hrsa_340b/opais.xlsx` | contract_pharmacy |

## Tier 2 — free, needs one build step after download (a second afternoon)

| # | Get | Save to | Then run | Unlocks |
|---|---|---|---|---|
| 2.1 | **PECOS enrollment** (you have PECOS; confirm the enrollment file with PAC ids is present) | `preclean/pecos/enrollment.csv` | `make ccn-crosswalk` | facility/HCRIS/POS schemes + better org resolution |
| 2.2 | **PBJ staffing + Care Compare hospice + deficiencies** | `preclean/facility/{pbj,hospice,deficiencies}.csv` | (auto) | worthless_services + hospice_ineligibility |
| 2.3 | **HCRIS cost reports** | `preclean/hcris/hcris.csv` | needs 2.1 | cost_report_fraud |
| 2.4 | **Provider of Services** | `preclean/pos/pos.csv` | needs 2.1 | capacity checks |
| 2.5 | **DocGraph / shared-patient** (docgraph.org, free tier) | `preclean/docgraph/` | (auto) | referral edges → referral-ring detection — the best NEW network signal available, and a real test for embeddings |
| 2.6 | **HUD ZIP→county crosswalk** — huduser.gov | `preclean/zip_county/zip_county.csv` | (auto) | county-grain saturation (fixes the state-grain caveat) |
| 2.7 | **NADAC** — data.medicaid.gov | `preclean/nadac/nadac.csv` | (auto) | drug_spread_anomaly |

## Tier 3 — paid or gated (Brad decisions; do NOT block Run 3 on these)

| # | What | Cost | Unlocks |
|---|---|---|---|
| 3.1 | SSA Death Master File (NTIS) | ~$200/yr | billing-after-death |
| 3.2 | USPS CMRA list | licensing | exact mailbox-storefront flag |
| 3.3 | OpenSanctions commercial-USE sign-off | license | (testing already fine on the free bulk) |
| 3.4 | People-data vendor + FCRA review | $$ | Model B activation |
| 3.5 | 64 GB compute for embeddings | **~$10–40 one-time, NOT $500** — see PROJECT_STATE §review | graph_emb_* on the full graph |

## Run order once files land

```
python data_audit.py                       # confirm layouts (again)
make opensanctions OPENSANCTIONS_FILE=preclean/opensanctions/targets.simple.csv
make ccn-crosswalk PECOS_FILE=preclean/pecos/enrollment.csv
make graph                                 # rebuild with the grouping fixes
make provider-features                     # Run-3 full matrix (all sources)
make frozen-package FROZEN_GRAPH_FLAGS=--no-embeddings   # Travis's forward test
```

Read `RESULTS_DIGEST.md` + `SOURCES_REPORT.md` after each build — the sources
report shows exactly which files were used vs skipped and why.
