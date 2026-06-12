# 14 — Analytics & Dataset Expansion Plan

What the strategy manifesto specifies that we have NOT yet built, organized by
value-per-effort. Section A costs nothing (new analytics on data already in the
pipeline). Section B is small free datasets with named adapters. Section C is
the bigger swings. Each item names where it plugs into the existing code.

---

## A. New analytics on data we ALREADY have (build first — zero downloads)

### A1. Scheme-scoped damages proxy (sharper exposure) — BUILT
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

### A2. Data-confidence band on every dossier — BUILT
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

### A4. Growth-shock score (change-points, not just YoY) — BUILT
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

## D. Manifesto features not captured anywhere else (inventory, June 2026)

Recorded here so the full manifesto surface stays visible. Not sequenced; most
sit behind Phase-0 counsel, real data, or the marketing build.

### D1. Typology blueprints not yet seeded in the scheme registry
The registry has 11 schemes; the manifesto's typology table (App. G) also
specifies, each with named features and witness personas:
- **MA risk adjustment** (HCC revenue-opportunity, unsupported-diagnosis
  proxies, chart-review hiring signals, RADV pressure) — the largest-dollar
  typology; needs MA-specific files
- **Hospice** (long LOS, non-cancer mix, live-discharge proxies, SNF referral
  concentration) — unlocked by B1 (Care Compare/PBJ)
- **SNF / worthless services** (staffing-vs-acuity mismatch, PDPM case-mix
  spikes, related-party services) — unlocked by B1 + B5
- **Labs** (panel stacking, CLIA capacity mismatch, prescriber concentration)
- **Behavioral health / ABA / SUD** (hours-per-patient, billing at
  authorization limits, staffing mismatch, patient-acquisition ads)
- **NEMT** (mileage/trip outliers, impossible routing, provider-driver
  related parties) — state Medicaid data, geospatial
- **Telehealth rings** (cross-state ordering, lead-gen ad infrastructure)
- **Pharmacy / PBM / 340B** (prescriber-pharmacy exclusivity, geography
  mismatch, contract-pharmacy patterns)
- **Cost-report fraud** (HCRIS DSH / wage-index / allocation anomalies) — B5

### D2. Creative data sources (manifesto §13) beyond the A/B/C lists
- Job-posting **semantic drift** (audit-response hiring, risk-adjustment
  hiring spikes) — partially parked in C; the drift analytic itself is unbuilt
- **Internet Archive deleted-page monitoring** (services/claims quietly
  removed from target websites after audits)
- **Domain / phone / address reuse graph** (shared-address shells exist;
  domain + phone reuse edges do not — DME/telehealth ring infrastructure)
- **Capacity-vs-billing reconciliation** (billed hours vs roster/CLIA/bed
  capacity — the "impossible org" version of impossible-day)
- **Policy-shock arbitrage** (orgs whose billing pivots immediately after a
  coverage/payment rule change)
- **Competitor-enforcement cloning** (when one org settles, score its
  structural twins — overlaps A9 but event-driven)
- **Payer directory termination monitoring** (network terminations as
  catalysts, A8 events)
- **Procurement / vendor graph** (USAspending, state contracts → `pays`
  edges)
- **Credentialing / roster inconsistencies** (directory vs NPPES vs claims
  mismatches)
- **Local news / court mining** and **ad-library monitoring** (Meta/Google ad
  libraries: patient-recruitment ad spend as a DME/telehealth/ABA signal)
- **FOIA strategy** (state Medicaid program-integrity reports, audit lists)

### D3. The funnel / marketing / intake software layer (manifesto §§12, 14–16, 20)
Deliberately unbuilt until Phase-0 counsel and a partner firm exist:
7-stage funnel metrics (visitor→MQL→CQL→EQL→filed), ListenLayer event
taxonomy + data-layer fields (`fraud_typology`, `persona_segment`,
`trust_stage`, `target_cluster_id`), composite behavioral lead score
(org-anomaly 25 / persona 25 / behavior 25 / intake 15 / recency 10), offline
conversion optimization to CQL, landing-page families per typology × persona,
SEO content hub (conversion hubs, distress/protection clusters, persona +
scheme pillars), 7-touch nurture sequences, self-assessment + reward
estimator + evidence-checklist lead magnets, confidential structured intake
(first-to-file / public-disclosure / original-source capture, privilege from
first contact), triage scoring, counsel case-packet generator, and
campaign-brief generation from dossiers ("data-to-audience transformation").

### D4. Model C remaining surface (beyond the scaffold)
P(intervene) + recovery-distribution model; USAO/jurisdiction intervention
tendencies; scienter-likelihood and evidence-specificity intake features;
counsel-interest fit; litigation-finance terms engine (the §7 unit-economics
model). A1 built the damages-proxy input; A3/A6 build two more.

### D5. The activation gate ("Final Practical Test")
A buildable checklist gate: no target enters outbound activation until the
system answers (1) what abnormal pattern vs what peer group, (2) benign
explanations, (3) which job titles would know, (4) what documents would
validate, (5) the lowest-pressure trust path. Dossiers already answer 1–2;
3–5 arrive with Model B activation and the content layer. Implement as a
hard gate in the eventual activation pipeline, not a score.

### D6. Platform plumbing contemplated, not yet needed
Feature-store / medallion marts (bronze raw → silver conformed → gold
model-ready by typology) — our preclean/interim/processed/features flow is
the small-scale equivalent; revisit at real-data volume. `fraud_typology_score`
output tables per provider/org. Supervised graduation (PU + isotonic + SHAP +
quantile-regression exposure) remains scaffolded in `model_a/supervised.py`.

## Explicitly not doing (manifesto-consistent)

T-MSIS RIFs / LDS (DUA-barred), commercial claims (license-barred), anything
that puts person-level scoring ahead of the FCRA review, and news/social
scraping beyond ToS — the public-disclosure screen uses licensed/API sources
only until counsel clears more.
