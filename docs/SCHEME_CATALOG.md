# Scheme Catalog — what each fraud scheme detects, its metrics, files, and calc

This is the reference behind the `subscore_<scheme>` columns. Each scheme is a named
fraud pattern; its subscore is a 0–1 score for how strongly a provider matches that
pattern. Read with `RUNBOOK_TRAVIS.md` (how to train on them) and
`PROVIDER_FEATURES_FOR_MODEL.md` (the column dictionary).

---

## How every subscore is calculated (one formula for all schemes)

1. **Each input metric is put on a 0–1 scale.** Billing-anomaly concepts and graph
   signals are already 0–1; every source-adapter metric is converted to its
   **one-sided taxonomy-peer percentile** first (higher = more than same-specialty
   peers; only excess is ever suspicious — robust median/MAD, the platform's hard
   rule #8).
2. **Weighted mean** of the scheme's *present* inputs (weights below). Inputs whose
   source file isn't loaded are skipped and the weights renormalize over what's
   present — a scheme degrades gracefully instead of mis-firing on partial data.
3. **Sigmoid squash**, centered at the peer median and sharpened:

   `subscore = sigmoid( 6.0 × ( weighted_mean − 0.5 ) )`

   So a provider at the peer median scores 0.5; ~0.9 → ~0.92; ~0.1 → ~0.08. The
   steepness (6.0) makes the score decisive at the top of the distribution, where
   fraud concentrates.

**De-correlation:** a given fact feeds exactly one scheme (the graph signals feed
only `ownership_integrity`), so nothing is double-counted. The scheme is "scored
today" if its inputs come from data you already have; otherwise it lights up when
its source file lands.

---

## Billing-shape schemes — score today, from the Medicaid spending fact

### single_service_mill
- **Detects:** a "mill" built to pump one lucrative code, vs. a real practice that
  bills a spread of codes.
- **Subscore:** `subscore_single_service_mill`
- **Inputs (weight):** `concentration` (1.0) — HHI / top-code share of the provider's billing.
- **Files:** Medicaid spending fact (HCPCS mix per NPI).
- **Calc:** sigmoid(6·(concentration_pct − 0.5)); concentration is the peer-percentile of billing concentration.

### payment_outlier
- **Detects:** extracting far more dollars per patient than same-specialty peers (upcoding / inflated units / phantom add-ons).
- **Subscore:** `subscore_payment_outlier`
- **Inputs (weight):** `payment_intensity` (1.0) — dollars per beneficiary vs peers.
- **Files:** Medicaid spending fact.
- **Calc:** sigmoid(6·(payment_intensity_pct − 0.5)).

### overutilization
- **Detects:** more services per beneficiary than is clinically plausible (phantom visits, unnecessary stacking).
- **Subscore:** `subscore_overutilization`
- **Inputs (weight):** `service_intensity` (1.0) — services per beneficiary vs peers.
- **Files:** Medicaid spending fact.
- **Calc:** sigmoid(6·(service_intensity_pct − 0.5)).

### specialty_mismatch
- **Detects:** billing codes a provider's specialty almost never bills (miscoding or fabrication — a hospice billing surgery; phantom patients beyond county capacity).
- **Subscore:** `subscore_specialty_mismatch`
- **Inputs (weight):** `specialty_mismatch` (0.5), `clinical_implausibility` (0.3), `local_volume_implausibility` (0.2).
- **Files:** spending × NPPES taxonomy (mismatch + clinical implausibility); + Census county population (local volume).
- **Calc:** sigmoid(6·(0.5·mismatch + 0.3·clin_implaus + 0.2·local_implaus)/Σweights − 0.5), over present inputs.

### rapid_ramp
- **Detects:** a scheme spinning up — a sudden sustained billing step or a burst of newly-billed codes (the fly-by-night signature YoY growth misses).
- **Subscore:** `subscore_rapid_ramp`
- **Inputs (weight):** `temporal` (0.6), `growth_level_shift` (0.8), `new_code_burst` (0.5).
- **Files:** Medicaid spending fact (monthly trajectory; growth signals from `analytics/growth`).
- **Calc:** weighted mean of the three peer/global percentiles → sigmoid.

---

## Ownership / identity schemes — the signals NOT in the claims file

### ownership_integrity
- **Detects:** concealment structure — proximity to an already-excluded party (rings caught together), shell companies, dense shared-owner webs, change-of-ownership churn.
- **Subscore:** `subscore_ownership_integrity` (leakage-adjacent)
- **Inputs (weight):** `within_2_hops_of_exclusion` (0.6), `shell_score` (0.5), `related_party_density_norm` (0.15), `ownership_turnover` (0.3).
- **Files:** entity graph (PECOS owner edges + LEIE/OpenSanctions exclusions); `ownership_turnover` from diffed monthly owner snapshots.
- **Calc:** weighted mean of the (already 0–1) graph signals → sigmoid.

### invalid_identity
- **Detects:** claims billed under a deactivated NPI, or after the provider's DOB-corroborated death — identity theft / phantom billing.
- **Subscore:** `subscore_invalid_identity`
- **Inputs (weight):** `billing_after_deactivation` (0.6), `billing_after_death` (0.6).
- **Files:** NPPES Deactivation report + spending (deactivation); SSA Death Master File + spending (death).
- **Calc:** weighted mean of the two post-event dollar shares → sigmoid. Only DOB-corroborated death matches feed it.

---

## Source-specific schemes — light up as each file lands

### upcoding
- **Detects:** disproportionate billing of the highest-complexity E&M codes (the textbook upcoding OIG prosecutes).
- **Subscore:** `subscore_upcoding`
- **Inputs (weight):** `em_high_level_share` (0.7), `em_level_mean` (0.3).
- **Files:** Medicare Part B (by Provider and Service).
- **Calc:** weighted mean of the peer-percentiles of high-level E&M share + mean E&M level → sigmoid.

### pharma_kickback
- **Detects:** prescribing that tracks the manufacturers paying the prescriber (Anti-Kickback / FCA).
- **Subscore:** `subscore_pharma_kickback`
- **Inputs (weight):** `op_payment_utilization_corr` (0.7), `op_payment_concentration` (0.3).
- **Files:** Open Payments × Medicare Part D (by Provider and Drug).
- **Calc:** weighted mean of payment-utilization correlation + payment concentration → sigmoid.

### drug_outlier
- **Detects:** controlled / high-cost drug concentration and billing above the national acquisition-cost benchmark (diversion / markup fraud).
- **Subscore:** `subscore_drug_outlier`
- **Inputs (weight):** `controlled_substance_share` (0.4), `high_cost_drug_share` (0.4), `drug_spread_anomaly` (0.3).
- **Files:** Medicare Part D (shares); NADAC + NDC claims (`drug_spread_anomaly`).
- **Calc:** weighted mean of the three peer-percentiles → sigmoid.

### pill_mill
- **Detects:** opioid (and long-acting opioid) prescribing share far above specialty peers — the pill-mill / diversion pattern.
- **Subscore:** `subscore_pill_mill`
- **Inputs (weight):** `opioid_claim_share` (0.6), `opioid_long_acting_share` (0.4).
- **Files:** CMS Part D Prescribers — by-Provider summary (opioid breakout columns).
- **Calc:** weighted mean → sigmoid. The long-acting split separates chronic-pain practices from diversion mills.

### dme_ring
- **Detects:** DME fraud — high-cost item concentration, orders from referrers ineligible to order DME, and a narrow ordering-physician funnel.
- **Subscore:** `subscore_dme_ring`
- **Inputs (weight):** `dme_high_cost_item_share` (0.4), `ineligible_referral_share` (0.4), `dme_ordering_md_concentration` (0.4, dormant).
- **Files:** DMEPOS by Referring Provider and Service; Order & Referring + referred-claims (ineligible referral). Ordering-MD concentration needs supplier↔referrer pair data (dormant).
- **Calc:** weighted mean of present inputs → sigmoid.

### worthless_services
- **Detects:** a facility billing for care it cannot physically deliver — understaffed, over capacity, heavily cited.
- **Subscore:** `subscore_worthless_services`
- **Inputs (weight):** `pbj_understaffing` (0.7), `capacity_mismatch` (0.6), `deficiency_count` (0.4).
- **Files:** PBJ Daily Nurse Staffing; Provider of Services (capacity); Care Compare deficiencies. (CCN-grain → needs the CCN→NPI crosswalk.)
- **Calc:** weighted mean of the peer-percentiles → sigmoid.

### hospice_ineligibility
- **Detects:** enrolling patients who were never terminally ill (high live-discharge rate).
- **Subscore:** `subscore_hospice_ineligibility`
- **Inputs (weight):** `hospice_live_discharge_rate` (1.0).
- **Files:** Care Compare hospice measures (CCN-grain → CCN→NPI crosswalk).
- **Calc:** sigmoid(6·(live_discharge_pct − 0.5)).

### saturation_fraud
- **Detects:** provider density beyond what the local population supports (supply-driven fraud markets, e.g. HHA/DME clusters).
- **Subscore:** `subscore_saturation_fraud`
- **Inputs (weight):** `market_saturation_index` (1.0).
- **Files:** CMS Market Saturation & Utilization (+ org_nodes for sector/geography).
- **Calc:** sigmoid(6·(saturation_index − 0.5)).

### contract_pharmacy
- **Detects:** unusual 340B contract-pharmacy footprints (diversion / duplicate-discount risk).
- **Subscore:** `subscore_contract_pharmacy`
- **Inputs (weight):** `contract_pharmacy_concentration` (1.0).
- **Files:** HRSA 340B OPAIS (+ org_nodes).
- **Calc:** sigmoid(6·(contract_pharmacy_concentration − 0.5)).

### cost_report_fraud
- **Detects:** cost-report inflation — wage-index, DSH, related-party, and cost-to-charge abuses.
- **Subscore:** `subscore_cost_report_fraud`
- **Inputs (weight):** `hcris_cost_anomaly` (1.0).
- **Files:** HCRIS cost reports (CCN-grain → CCN→NPI crosswalk).
- **Calc:** sigmoid(6·(hcris_cost_anomaly_pct − 0.5)); the anomaly is the max one-sided percentile across the abused cost ratios.

---

## Dormant by data (no public file carries the field — stays null, not a code gap)

### impossible_day
- **Detects:** more services / procedure-minutes in a day than physically possible.
- **Inputs (weight):** `bene_per_day_p95` (0.6), `time_minutes_per_day` (0.4).
- **Needs:** claim/line-level data with service dates (the annual by-provider PUF can't express per-day counts). Stays silent until that lands — never interpret its absence as "no impossible-day risk."

### dme_ring · ordering-MD concentration
- **Detects:** one physician funneling one DME supplier.
- **Needs:** DMEPOS line-level pairing supplier ↔ ordering MD (the public PUF doesn't pair them). The other two `dme_ring` inputs score without it.
