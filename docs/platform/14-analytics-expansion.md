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

### A3. Public-disclosure screen (the 4th legal gate, automated) — BUILT
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

### A5. Clinical-plausibility score, data-derived — BUILT (specialty half)
Manifesto: "specialty-to-code compatibility, volume vs local denominator."
No external code-to-specialty table needed: derive compatibility FROM the data
— a code billed by <1% of an org's taxonomy peers is implausible for that
specialty (v3's `rare_share_te` is the seed; extend to a proper per-code
plausibility matrix with dollar weighting and a county-population denominator
once Census county files are added — see B6).
**Where:** `src/analytics/plausibility.py`. `code_prevalence_matrix` builds the
per-(taxonomy, HCPCS) prevalence from the spending data itself (taxonomies with
too few providers can't anchor a prevalence and are excluded — a code billed by
the only provider in a specialty is 100% "prevalent" and 0% informative);
`org_clinical_plausibility` gives each org its dollar-weighted implausible share
plus a named driver ("$2.1M on T1019, billed by 0.3% of 251G peers");
`plausibility_percentiles` feeds the `clinical_implausibility` registry input,
blended 0.4 into the `specialty_mismatch` scheme alongside the v3 concept (0.6).
Wired into the Model A `--spending --provider-dim` path; driver rendered on the
dossier. **Still to come (the "volume vs local denominator" half):** billing
rate vs county population, gated on the Census county file (B6).

### A6. Government-interest overlay — BUILT
Manifesto's sixth sub-score: alignment to "OIG Work Plan, DOJ enforcement
themes, CMS RADV focus, state MFCU activity." We already derive DOJ themes
from the case DB (sector priors); the OIG Work Plan is a public, structured
list of active audit topics.
**Build:** a small curated table (work-plan item → sector/code-family →
weight, refreshed quarterly from oig.hhs.gov/reports-and-publications/workplan)
that multiplies into the sector prior. Effectively "the government is already
looking here" — which is exactly what predicts intervention (Model C reuses it).
**Where:** `src/model_a/government_interest.py` + a YAML/CSV the team can edit.

### A7. Ownership-churn / CHOW detection — BUILT (dormant until snapshots accumulate)
Manifesto: "short-lived entities, ownership churn, changes-of-ownership" as
concealment signals. We ingest the All-Owners files but only the latest
snapshot; `association_date` is already carried.
**Build:** with two+ monthly snapshots diffed (keep each month's file —
runbook change), emit owner-entry/exit events per org → `ownership_turnover`
feature (already named in the registry, currently dormant) + event rows the
docket/WARN surge logic can treat as "exit-after-event" catalysts.
**Where:** `src/entity_graph/ownership_churn.py`; runbook gains "keep monthly
owner files, don't overwrite."

### A8. Exit-after-event timing (Model B2 sharpener) — BUILT (org-level)
Manifesto: "exits after audits, acquisitions, layoffs, leadership changes,
payer terminations are high-signal." We have layoffs (WARN) and will have
ownership changes (A7) and enforcement events (case DB).
**Build:** an org-level event timeline (WARN + CHOW + enforcement + docket
events) and a B2 feature: departure within N months AFTER an event scores
higher than a cold departure. Org-level until people data exists; slots into
`model_b/propensity.py` (the weights table already anticipates it).

### A9. Enforcement lookalikes (exemplar-based, explainable) — BUILT (dormant until DOJ backfill)
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
| B1 | **PBJ Daily Nurse Staffing + Care Compare** — BUILT | data.cms.gov (runbook §1.6) | `pbj_understaffing` (negated HPRD: billed acuity vs. actual staffing = worthless-services), `hospice_live_discharge_rate` (ICP-1's sharpest signal) → `hospice_ineligibility` scheme, `deficiency_count` | `ingest_cms/facility.py` — CCN grain; facility peers = size band × state via the peer engine's custom-ladder support; `rollup_ccn_to_org` awaits the PECOS CCN↔NPI crosswalk file |
| B2 | **Market Saturation** — BUILT | data.cms.gov (runbook §1.4) | county over-supply index for HH/hospice/SNF/lab — the CMS program-integrity prior → `saturation_fraud` scheme | `ingest_cms/saturation.py` → `market_saturation_index`; state-grain attach until ZIP→county (B6) lands |
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
   ICP-1 signal package and introduces the CCN/facility grain). — DONE:
   `ingest_cms/saturation.py` + `ingest_cms/facility.py`; three new schemes
   registered (`worthless_services`, `hospice_ineligibility`,
   `saturation_fraud`) — dormant until the files land (runbook §1.4/§1.6).
3. **A3 + A6** together (both reuse the case DB/dockets; both feed Model C's
   first real functions). — DONE: `src/model_c/public_disclosure.py` (named
   citations, sources-checked recorded, never reads as clearance) and
   `src/model_a/government_interest.py` (curated Work Plan table, max-weight
   combination, titles as named drivers); wired into the orchestrator
   (`--case-db` / `--dockets`) and the dossier.
4. **A5** clinical plausibility (pure code on the spending fact + taxonomy).
   — DONE: `src/analytics/plausibility.py`, blended into `specialty_mismatch`;
   the county-denominator half waits on B6.
5. **A7 + A8** — DONE (code): `src/entity_graph/ownership_churn.py` (diff owner
   snapshots → entry/exit events + `ownership_turnover`) and
   `src/sourcing/event_timeline.py` (WARN+CHOW+enforcement+docket timeline →
   recency-weighted org catalyst score, with the org-level B2 propensity hook).
   Dormant until two monthly owner snapshots accumulate — start keeping them NOW.
6. **A9** — DONE (code): `src/model_a/lookalikes.py` (nearest settled org in
   subscore space; corroboration only, never a driver), wired through the Model A
   `--case-db` path; dormant until the DOJ backfill populates settled defendants.
   **B10** still pending the DocGraph shared-patient files.
7. B3/B4/B5/B6/B8/B9 fill in alongside, smallest-first.

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
  concentration) — `hospice_ineligibility` scheme now seeded by B1
  (live-discharge); LOS/diagnosis-mix features still to come
- **SNF / worthless services** (staffing-vs-acuity mismatch, PDPM case-mix
  spikes, related-party services) — `worthless_services` scheme now seeded by
  B1 (PBJ understaffing + deficiencies); PDPM/case-mix needs B5
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

### D4. Model C — cold-start underwriting BUILT; trained model remaining
BUILT (rules-based, label-free): P(intervene) with jurisdiction intervention
tendencies (incl. Zafirov venue discount), recovery-distribution P10/P50/P90,
the fund/pass/fund-with-terms terms engine, and the portfolio Monte Carlo
(`src/model_c/priors|features|underwriting|portfolio.py`, `python -m
src.model_c`). Consumes A1 (damages proxy), A3 (public-disclosure screen), A6
(government-interest) and the docket first-to-file alerts. **Remaining (gated
on data):** the trained calibrated GBM + quantile recovery model that retires
the cold-start priors; richer scienter-likelihood / evidence-specificity intake
features (need a real intake form); the reject-inference exploration tranche
once financing is live.

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

## E. June 2026 research sweep — additive sources NOT previously catalogued

A deliberate web sweep (data cutoff was Jan 2026) to confirm nothing free or
cheap-license was being left on the table, beyond the manifesto's catalog and
Sections A–D above. Findings, grouped; each names where it would plug in.

### E0. URGENT infra correction — Kùzu is retired
**Kùzu (the planned graph viewer) was acquired by Apple and its repo archived in
Oct 2025 — no longer maintained.** Replacement: **DuckPGQ**, a DuckDB community
extension adding SQL/PGQ graph pattern-matching/path-finding. We already run
DuckDB everywhere, so this adds graph queries with zero new infrastructure (no
built-in viz — pair with the Neo4j visualization JS lib or Gephi export).
Alternatives if a bundled-viz server is wanted: a maintained Kùzu fork,
FalkorDB, or Memgraph. `TREY_BUILD_PLAN.md` Phase 5 updated.

### E1. Free public datasets we had not listed
- **Splink** is infra (E3), but these are *data*:
- **SSA Death Master File (public)** — billing under a deceased provider's or
  beneficiary's ID is a clean phantom/identity-fraud signal we have no analog
  for. Public file free; Limited Access (<3 yr) needs certification. New scheme
  candidate (`deceased_identity`) or an integrity-subscore feature.
- **DEA ARCOS** (opioid distribution, court-ordered public via Univ. of Notre
  Dame / Washington Post, 2006–2019) — pharmacy/opioid corroboration; historical
  but structural. Feeds `drug_outlier` / pill-mill.
- **CMS Part D Opioid Prescriber Summary + Opioid Prescribing Rates by
  geography** (data.cms.gov) — a ready-made prescriber-level opioid-rate signal
  sharper than deriving it from Part-D-by-drug. Drops into the partd adapter.
- **HRSA 340B OPAIS** (free Excel: covered entities + contract pharmacies) —
  lights up the pharmacy/340B typology (D1): contract-pharmacy exclusivity,
  geography mismatch. New `ingest_cms`-style adapter.
- **CMS Provider of Services (POS) file** — facility characteristics/capacity
  (beds, CLIA, services) → the "capacity-vs-billing reconciliation" idea in D2
  and sharper facility peer cells. Free.
- **CMS Order & Referring file** — who is eligible to order/refer → validate
  DME/lab referral chains and catch orders from ineligible/excluded orderers.
  Free; pairs with the DME ring scheme.
- **CMS Revalidation / Clinic-Group-Practice Reassignment file** — physician↔
  group reassignment edges, an affiliation-graph layer complementing PECOS
  ownership. Free on data.cms.gov.
- **NPPES deactivation file (weekly)** — short-lived / deactivated-entity signal
  (fly-by-night), complements `rapid_ramp` and ownership-churn (A7). Free.
- **T-MSIS DQ Atlas** — NOT the (gated) claims: the public per-state Medicaid
  data-QUALITY scores. Feeds the data-confidence band (A2): down-weight states
  with poor reporting. The lawful public face of T-MSIS. Free.
- **USAspending.gov API** (no key) — federal grants/contracts to providers
  (HRSA grants, COVID relief) → the procurement/vendor graph (D2) + a
  defendant size/solvency feature for Model C. Free.
- **openFDA** (warning letters, 483 inspections, debarment list; real API) —
  lab/pharma/device integrity signal. Free.
- **ProPublica Nonprofit Explorer API** (free, no key) — 990s incl. nonprofit
  hospital exec comp + Schedule R related orgs → available NOW for the B8
  owner/related-party enrichment without parsing raw EDGAR.
- **CMS DE-SynPUF** (synthetic Medicare claims, public) — a realistic synthetic
  claims set to harden the pipeline and dev the supervised graduations WITHOUT
  touching PHI (beyond our tiny fixture). Free.

### E2. Cheap-license data (Brad decision; modest, not enterprise)
- **OpenSanctions** — aggregates HHS-OIG LEIE + ~45 state Medicaid exclusion
  lists + SAM + global PEP/sanctions into ONE normalized feed with an API.
  This essentially *is* our B3 (state exclusions) pre-built, plus owner PEP
  screening. Free for non-commercial; a commercial license is required for us
  (modest) — likely cheaper than building/maintaining 45 per-state scrapers.
- **Definitive Healthcare alternatives** — Provyx (pay-per-record, no annual
  contract) / AcuityMD for facility org-charts and affiliation data when Model B
  persona-mapping needs it; far cheaper entry than DH / IQVIA.

### E3. ML / tooling (Hugging Face + open source) beyond the current HF plan
- **Splink** (MOJ, MIT, free) — production-grade probabilistic record linkage
  (Fellegi-Sunter) on DuckDB, unsupervised, millions of records on a laptop.
  The manifesto calls entity resolution "the hard part"; our resolver is
  exact-key + alias today. This is the upgrade path for person↔employer
  resolution (Model B) and org dedup. Highest-leverage infra find.
- **GLiNER / GLiNER2** (free, CPU) — zero-shot NER + text classification +
  relation extraction in one small model; extracts org/person/role entities
  from dockets, news, 990 PDFs, reviews, intake text. Serves both the planned
  grievance classifier and the D2 creative-data extraction.
- **Embedding models** — our planned `bge-small` is fine; newer small open
  options (Jina v5-small, Qwen3-Embedding) are available if we want more headroom
  for the name-matching embeddings.
- **Clinical coding models** (OpenMed NER 2025, MedGemma, automated ICD/CPT
  coders) — for the eventual intake-document parsing and plausibility
  enrichment; lower priority since A5 derives plausibility from data directly.

**Bottom line of the sweep:** one urgent correction (Kùzu → DuckPGQ), one
high-value infra upgrade (Splink for entity resolution), and ~a dozen free or
cheap additive data sources — none of which change the legal frame or require
crossing a new gate. The full procurement detail belongs in
`09-data-procurement.md`; the highest-ROI next adds are SSA DMF, the CMS opioid
files, 340B OPAIS, T-MSIS DQ Atlas (cheap A2 win), and Splink.

## Explicitly not doing (manifesto-consistent)

T-MSIS RIFs / LDS (DUA-barred), commercial claims (license-barred), anything
that puts person-level scoring ahead of the FCRA review, and news/social
scraping beyond ToS — the public-disclosure screen uses licensed/API sources
only until counsel clears more.
