# 14 — Analytics & Dataset Expansion Plan

What the strategy manifesto specifies that we have NOT yet built, organized by
value-per-effort. Section A costs nothing (new analytics on data already in the
pipeline). Section B is small free datasets with named adapters. Section C is
the bigger swings. Each item names where it plugs into the existing code.

---

## A. New analytics on data we ALREADY have (build first — zero downloads)

### A1. Scheme-scoped damages proxy (sharper exposure)
The manifesto's damages proxy is "payments associated with **suspect
code/service families** × estimated unsupported share × period" — not total
billing. Today `exposure.py` uses ALL of an org's payments × a scheme
multiplier, which overstates exposure for diversified orgs and understates the
precision counsel expects.
**Build:** per scheme, a HCPCS-family map (personal-care codes for EVV scheme,
E/M codes for upcoding, DME L-codes, etc. — `BROAD_HCPCS_CODES` is the seed);
exposure = payments *within the scheme's code family* × multiplier. The dossier
then says "$8.2M of personal-care billing at issue," not "$25M total billing."
**Where:** `src/model_a/exposure.py` (+ a `scheme_code_families.py` data
module); `spending_fact` already carries HCPCS per row.

### A2. Data-confidence band on every dossier
The manifesto's scoring architecture has a seventh sub-score we skipped:
**data-confidence** — "suppress small samples, poor state quality, stale
source data, broad peer groups." The spec's output line is "ranked organizations
with a scheme hypothesis, an exposure estimate, **and a confidence band**" —
the band is the missing third.
**Build:** confidence = f(peer_basis level, peer_n, volume vs. gate, years
observed, % metrics degenerate/NaN). Three bands (high/medium/low) with named
reasons, printed on the dossier and carried in `erv_ranked`.
**Where:** new `src/analytics/confidence.py`; inputs all exist
(`peer_basis`/`peer_n` from the peer engine, `years_observed` from exposure,
`not_scored_reason` from v3).

### A3. Public-disclosure screen (the 4th legal gate, automated)
We built first-to-file alerts; the manifesto's case-viability score also wants
**public-disclosure risk**: "whether allegations are already in public
litigation, news, audits, or government reports." A relator whose story is
already public loses standing — checking BEFORE outreach is cheap insurance.
**Build:** for each top-ranked org, search our own case DB + the CourtListener
docket pulls + (later) news for the org's name keys; emit
`public_disclosure_flag` + the matching citations on the dossier.
**Where:** `src/model_c/` first real function (it's a case-viability input);
reuses `norm_org_name` matching that already joins both sources.

### A4. Growth-shock score (change-points, not just YoY)
v3 has `yoy_growth_net_paid` + volatility; the manifesto wants **change-point
models** on "services, beneficiaries, payments, new codes, new locations" —
the fly-by-night ramp signature. `provider_month` and `provider_hcpcs` tables
already exist from `features.py`.
**Build:** per org: largest level-shift in monthly paid (simple two-segment
mean-split scan — explainable, no library), new-HCPCS adoption burst (codes
first billed in trailing 6mo / total codes), new-state expansion events from
enrollment dates. Output 0–1 percentiles feeding a `growth_shock` concept.
**Where:** new `src/analytics/growth.py` → registers in
`model_a/scheme_subscores.py` (registry already absorbs new columns).

### A5. Clinical-plausibility score, data-derived
Manifesto: "specialty-to-code compatibility, volume vs local denominator."
No external code-to-specialty table needed: derive compatibility FROM the data
— a code billed by <1% of an org's taxonomy peers is implausible for that
specialty (v3's `rare_share_te` is the seed; extend to a proper per-code
plausibility matrix with dollar weighting and a county-population denominator
once Census county files are added — see B6).
**Where:** extend `src/analytics/` with `plausibility.py`; feeds the
`specialty_mismatch` concept with finer drivers ("$2.1M in codes billed by
<1% of hospices").

### A6. Government-interest overlay
Manifesto's sixth sub-score: alignment to "OIG Work Plan, DOJ enforcement
themes, CMS RADV focus, state MFCU activity." We already derive DOJ themes
from the case DB (sector priors); the OIG Work Plan is a public, structured
list of active audit topics.
**Build:** a small curated table (work-plan item → sector/code-family →
weight, refreshed quarterly from oig.hhs.gov/reports-and-publications/workplan)
that multiplies into the sector prior. Effectively "the government is already
looking here" — which is exactly what predicts intervention (Model C reuses it).
**Where:** `src/model_a/government_interest.py` + a YAML/CSV the team can edit.

### A7. Ownership-churn / CHOW detection
Manifesto: "short-lived entities, ownership churn, changes-of-ownership" as
concealment signals. We ingest the All-Owners files but only the latest
snapshot; `association_date` is already carried.
**Build:** with two+ monthly snapshots diffed (keep each month's file —
runbook change), emit owner-entry/exit events per org → `ownership_turnover`
feature (already named in the registry, currently dormant) + event rows the
docket/WARN surge logic can treat as "exit-after-event" catalysts.
**Where:** `src/entity_graph/ownership_churn.py`; runbook gains "keep monthly
owner files, don't overwrite."

### A8. Exit-after-event timing (Model B2 sharpener)
Manifesto: "exits after audits, acquisitions, layoffs, leadership changes,
payer terminations are high-signal." We have layoffs (WARN) and will have
ownership changes (A7) and enforcement events (case DB).
**Build:** an org-level event timeline (WARN + CHOW + enforcement + docket
events) and a B2 feature: departure within N months AFTER an event scores
higher than a cold departure. Org-level until people data exists; slots into
`model_b/propensity.py` (the weights table already anticipates it).

### A9. Enforcement lookalikes (exemplar-based, explainable)
Manifesto: "public enforcement lookalikes" as corroboration. Once the DOJ
backfill runs, we have feature vectors for orgs that settled.
**Build:** nearest-neighbor distance from each scored org to the settled-org
set in concept-percentile space; dossier line: "feature profile most similar
to [3 settled cases, named]." Exemplar-based = inherently explainable; never a
score driver, always corroboration context (X-layer, per the manifesto).
**Where:** `src/model_a/lookalikes.py`; gated on the DOJ backfill having run.

---

## B. Small free datasets to add (adapters follow the ingest_cms contract)

| # | Dataset | Source | What it unlocks | Plugs into |
|---|---|---|---|---|
| B1 | **PBJ Daily Nurse Staffing + Care Compare** | data.cms.gov (P2 #8 in runbook) | `pbj_staffing_z` (billed acuity vs. actual staffing = worthless-services), `hospice_live_discharge_rate` (ICP-1's sharpest signal), deficiency counts | new `ingest_cms/facility.py`; CCN joined via PECOS enrollment; facility peer cells = the manifesto's "size band × region" (peer engine already supports custom ladders) |
| B2 | **Market Saturation** | data.cms.gov (P1 #5 — runbook'd, no adapter yet) | county over-supply multiplier for HH/hospice/DME — the CMS program-integrity prior | tiny `ingest_cms/saturation.py` → `market_saturation_index` (registry name exists, dormant) |
| B3 | **State Medicaid exclusion/sanction lists** | ~40 states publish their own lists beyond OIG LEIE | more exclusion nodes; state actions often precede federal | normalize to the `exclusions` schema (same as `sam_api.py` did); start with ICP states |
| B4 | **NADAC drug pricing** | data.medicaid.gov (weekly) | normalizes Part D cost signals; spread/markup anomalies; 340B groundwork | `ingest_cms/nadac.py` → refines `high_cost_drug_share` |
| B5 | **HCRIS cost reports** | cms.gov (P2 #9) | `hcris_cost_alloc_anomaly` (cost-report fraud — a distinct scheme) + the finance-persona targeting hint for Model B | `ingest_cms/hcris.py`; CCN grain |
| B6 | **Census county population** | census.gov (one small file) | the "volume vs local denominator" in clinical plausibility (A5) and saturation context | joins Market Saturation by FIPS |
| B7 | **OIG Work Plan items** | oig.hhs.gov (structured list, scrape-light) | feeds A6 | quarterly refresh, curated CSV |
| B8 | **Form 990 Schedule R / OpenCorporates / SEC EDGAR** | ProPublica API (free), opencorporates (free tier), EDGAR API (free) | owner/officer enrichment beyond CMS All-Owners: nonprofit related parties, corporate families, PE disclosures | new `owner:`/`also_owns` edges in the entity graph; EDGAR full-text for acquisition/recap events (A8 catalysts) |
| B9 | **State licensing boards** (ICP states first) | per-state | provider discipline (sanctions beyond LEIE) + later person-level B2 credibility markers | exclusion-adjacent nodes; person side waits on the people-data gate |
| B10 | **CMS DocGraph shared-patient files** (historical public releases) | archived CMS/DocGraph 30-day shared-patient data | the `refers_to` edges ring detection was built for — referral HHI, exclusivity, closed loops | `entity_graph` referral edges + un-gates `ring_detection.referral_rings`. Honest caveat: public vintages are old (2009–2015); structural rings persist, but treat as historical corroboration |

## C. Bigger swings (sequenced after A+B prove out)

- **Hospital/payer price transparency** — the manifesto's "enormous but messy"
  corroboration layer; heavy ETL; only after core signals are validated on
  real data.
- **EVV visit-level data** — the cleanest phantom-visit signal in ICP-1;
  state-by-state agreements (constrained/P3 per the manifesto).
- **State APCDs** — better peer denominators; per-state licensing.
- **Job-postings signal** ("audit-response hiring," "risk-adjustment hiring
  spikes" as MA signal + event catalysts) — valuable but collection terms need
  the same counsel review as Glassdoor; park with the collectors.
- **Patient-complaint smoke** (Google/Yelp/BBB) — qualitative only, ad-copy and
  dossier color; never a driver.

## Sequencing recommendation

1. **A1, A2, A4** the moment real data lands (pure code, immediate dossier
   quality jump: scoped damages, confidence bands, ramp detection).
2. **B2 + B1** next (Market Saturation is an hour; PBJ/Care Compare is the
   ICP-1 signal package and introduces the CCN/facility grain).
3. **A3 + A6** together (both reuse the case DB/dockets; both feed Model C's
   first real functions).
4. **A7 + A8** once two months of owner snapshots accumulate (start keeping
   snapshots NOW — it's a runbook line, not code).
5. **A9 + B10** after the DOJ backfill runs.
6. B3/B4/B5/B6/B8/B9 fill in alongside, smallest-first.

## Explicitly not doing (manifesto-consistent)

T-MSIS RIFs / LDS (DUA-barred), commercial claims (license-barred), anything
that puts person-level scoring ahead of the FCRA review, and news/social
scraping beyond ToS — the public-disclosure screen uses licensed/API sources
only until counsel clears more.
