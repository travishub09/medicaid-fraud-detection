# Provider Feature Export — guide for the supervised model

**Audience:** Travis (and anyone training/served by the LightGBM model).
**What it is:** a per-NPI feature table produced by `src/model_a/provider_features_export.py`.
It re-points Trey's rules-based scheme engine from org-grain case ranking to a
provider-grain training matrix: one row per NPI, carrying raw provider statistics,
their peer-relative percentiles, a set of per-scheme fraud subscores, and the
positive-unlabeled (PU) label. You join it into your provider database on `npi`
and train; nothing about your model changes.

This document explains **how to run it**, **what every column means**, **the exact
math behind the subscores**, and — most importantly — **why each variable should
carry real fraud signal**, so the features survive a skeptical review and an
out-of-time backtest.

---

## 1. TL;DR for the impatient

```bash
# rebuild the graph first (ownership signal is computed there), then export
make provider-features                      # writes <DATA_ROOT>/model_a/provider_features/
# or directly:
python -m src.model_a.provider_features_export \
    --graph-dir   <DATA_ROOT>/graph \
    --leads       <DATA_ROOT>/detection/fraud_leads_v3.parquet \
    --preclean    <DATA_ROOT>/preclean \
    --processed   <DATA_ROOT>/processed \
    --out         <DATA_ROOT>/model_a/provider_features
```

Outputs:
- `provider_features_for_model.parquet` — the matrix, one row per NPI.
- `feature_manifest.json` — machine-readable column roles (label / leakage / features / subscores).
- `PROVIDER_FEATURES_DICTIONARY.md` — per-column dictionary with non-null counts.
- `PROVIDER_FEATURES_EXPORT_REPORT.md` — which sources contributed, which were skipped and why.

**Train on:** `manifest.raw_feature_cols` + `manifest.subscore_cols` + `manifest.peerpct_cols`.
**Target:** `manifest.label` (`provider_on_leie`).
**Never train on:** `manifest.leakage_hard`. **Validate carefully with:** `manifest.leakage_adjacent` (see §6).

---

## 2. The grain, and why it differs from Model A

The scheme engine was built to score **organizations** and rank them by Expected
Recoverable Value for human dossier review. Your model trains on **providers
(NPIs)** over the full universe. Three deliberate differences in this export:

1. **One row per NPI**, not per org. The v3 anomaly concepts and the raw provider
   stats are already per-NPI; the organization-level signals (entity-graph
   ownership features, and the org/CCN-grain adapter outputs) are **broadcast down**
   to each member NPI via the `npi_to_org` crosswalk — every NPI inherits its
   organization's value. `org_node_id` is carried on each row so you can see which
   NPIs share an organization and audit the broadcast.
2. **No candidate gate, no payer filter.** Model A drops signal-less orgs and
   program-infrastructure payers (correct for triage). This export keeps the
   **entire scored universe**, because your PU model needs the unlabeled/negative
   mass, not just the suspicious tail.
3. **Subscores, not the combined probability.** Model A combines the per-scheme
   subscores with a noisy-OR into one `org_prob`. Here you get the **individual
   `subscore_<scheme>` columns** instead — your tree decides how to combine them,
   which is the whole point of handing them to you rather than the rolled-up score.

---

## 3. The output schema (column groups)

| Group | Naming | What it is |
|---|---|---|
| Identifiers | `npi`, `org_node_id`, `entity_type`, `primary_taxonomy`, `practice_state`, `org_legal_name` | keys & metadata (not features) |
| Raw provider stats | `gross_paid`, `net_paid`, `service_volume`, `total_claim_lines`, `n_distinct_hcpcs`, … | lifetime billing aggregates from the spending fact |
| v3 anomaly concepts | `concentration`, `payment_intensity`, `service_intensity`, `specialty_mismatch`, `temporal` | the five peer-relative anomaly percentiles (already 0–1) |
| Graph / ownership | `within_2_hops_of_exclusion`, `shell_score`, `related_party_density(_norm)`, `ownership_turnover`, … | entity-graph signals, broadcast org→NPI |
| Graph embeddings | `graph_emb_0..15`, `graph_fraud_proximity` | DeepWalk-style node vectors + PageRank fraud field (per-NPI position; `embedding_cols` in manifest; leakage-adjacent) |
| Graph motifs | `graph_kcore`, `graph_triangles`, `graph_clustering`, `graph_degree` | structural position (clean features) |
| Adapter raw features | `em_high_level_share`, `opioid_claim_share`, `hcris_cost_anomaly`, … | each CMS source's raw metric |
| **Peer percentiles** | `<feature>__peerpct` | one-sided taxonomy-peer percentile of each adapter metric |
| **Scheme subscores** | `subscore_<scheme>` | the 0–1 fraud-scheme scores (§4–5) |
| **Label** | `provider_on_leie` | PU positive (on the OIG exclusion list) |
| Leakage (quarantined) | `billed_after_exclusion`, `excluded_after_billing` | present but listed in `leakage_hard` |

**Why ship raw *and* percentiles *and* subscores?** Three levels of processing,
because your tree can exploit all three and you should decide which wins:
- **raw** values let the tree find its own thresholds and interactions;
- **`__peerpct`** is the platform-canonical comparison — a value's rank *within its
  specialty* (a $400/beneficiary lab is normal; a $400/beneficiary home-health
  agency is not). One-sided: only being *above* peers is ever suspicious.
- **`subscore_<scheme>`** is the distilled, domain-weighted signal — a ready-made
  feature that already encodes the fraud logic in §5.

---

## 4. How a subscore is computed (the exact math)

Every `subscore_<scheme>` is built the same way (`src/model_a/scheme_subscores.py`):

1. **Normalize each input feature to 0–1.**
   - The five v3 concepts are *already* peer-relative percentiles (computed with a
     robust median/MAD z-score, 1.4826 scaling, one-sided, inside a
     taxonomy×entity×state peer ladder with size/complexity adjustment — see
     `src/analytics/peers.py` and `attempt_2/leads/refine_layer2_v3.py`). They pass
     through unchanged.
   - The graph features are already bounded (0/1 flags, or counts squashed to 0–1).
   - **Each raw adapter metric is converted to its one-sided taxonomy-peer
     percentile** before it feeds a subscore, so a subscore never fires on an
     absolute share that ignores the specialty baseline. (That percentile is also
     exported to you as `<feature>__peerpct`.)
2. **Weighted mean** across the scheme's *present* features, using the registry
   weights in §5. Features absent from the data are skipped and the weights
   renormalize over what's present — so a scheme degrades gracefully as sources
   arrive rather than mis-firing on partial data.
3. **Sigmoid squash**, centered and sharpened:

   ```
   subscore_s = sigmoid( 6.0 · ( weighted_mean − 0.5 ) )
   ```

   So a provider at the peer median (0.5) scores 0.5; ~0.9 → ~0.92; ~0.1 → ~0.08.
   The steepness (6.0) makes the score decisive near the top of the distribution,
   which is where fraud concentrates, without hard-thresholding.

**De-correlation principle:** a given fact feeds exactly one scheme. The
entity-graph ownership features feed only `ownership_integrity`; the ring-structure
boost that Model A uses for ranking is *not* in this export, so nothing is
double-counted across subscores.

---

## 5. The schemes — what each measures, what feeds it, and why it's real fraud signal

Each scheme is one column, `subscore_<scheme>`. The "why" is the False Claims Act /
program-integrity theory that makes the variable predictive of *prosecuted* fraud,
not just unusual billing.

### Billing-shape schemes (score today, from the Medicaid spending fact)

| Scheme | Inputs (weight) | Data source | Why it's reliable fraud signal |
|---|---|---|---|
| **single_service_mill** | `concentration` (1.0) | spending (HCPCS mix per NPI) | A legitimate practice bills a *spread* of codes reflecting real clinical variety. A "mill" exists to pump one lucrative code (a single home-health, psych, or therapy code). Extreme code concentration (high HHI / top-code share) is economically unnatural and is the signature of a billing operation built around one scheme. |
| **payment_outlier** | `payment_intensity` (1.0) | spending ($/beneficiary) | Dollars extracted per patient, ranked against same-specialty peers. Far-above-peer payment intensity is what upcoding, inflated units, and phantom add-on services all produce. Peer-relative + one-sided controls for legitimately expensive specialties. |
| **overutilization** | `service_intensity` (1.0) | spending (services/beneficiary) | Too many services per patient to be clinically plausible — the shape of phantom visits and medically unnecessary service stacking. |
| **specialty_mismatch** | `specialty_mismatch` (0.5), `clinical_implausibility` (0.3), `local_volume_implausibility` (0.2) | spending × NPPES taxonomy (+ Census) | A provider billing codes its specialty almost never bills (a hospice billing surgical codes; a lab billing home-visit codes) is either miscoding or fabricating. `clinical_implausibility` is data-derived: the dollar-weighted share of billing on codes rare for the provider's own taxonomy. `local_volume_implausibility` flags billing more per-capita than the county population can support (phantom patients). |
| **rapid_ramp** | `temporal` (0.6), `growth_level_shift` (0.8), `new_code_burst` (0.5) | spending (monthly trajectory) | Fraud schemes spin up fast and pivot. A sudden sustained step in monthly billing (`growth_level_shift`) or a burst of newly-billed codes (`new_code_burst`) is the fly-by-night / scheme-onset signature that year-over-year growth alone misses. |

### Ownership / identity schemes (the signal NOT in the claims file)

| Scheme | Inputs (weight) | Data source | Why it's reliable fraud signal |
|---|---|---|---|
| **ownership_integrity** | `within_2_hops_of_exclusion` (0.6), `shell_score` (0.5), `related_party_density_norm` (0.15), `ownership_turnover` (0.3) | entity graph (PECOS owners + LEIE/OpenSanctions) | Proximity in the ownership graph to an **already-excluded** party (rings get caught together), shell-company structure (thin name-only entities sharing an address), dense related-party webs, and rapid change-of-ownership churn are all concealment behaviors. **This is the highest-value differentiator vs. a billing-only model — none of it is visible in claims data.** |
| **invalid_identity** | `billing_after_deactivation` (0.6), `billing_after_death` (0.6) | NPPES deactivation + SSA Death Master File | Claims billed under an NPI *after* it was deactivated, or after the provider's DOB-corroborated death date, are identity-theft / phantom-billing in its starkest form. Only DOB-corroborated death matches score (name-only matches go to human review — defamation guardrail). |

### Source-specific schemes (light up as each file lands — see the data-acquisition guide)

| Scheme | Inputs (weight) | Data source | Why it's reliable fraud signal |
|---|---|---|---|
| **upcoding** | `em_high_level_share` (0.7), `em_level_mean` (0.3) | Medicare Part B | Disproportionate billing of the highest-complexity evaluation-and-management codes is the textbook upcoding pattern OIG prosecutes. |
| **pharma_kickback** | `op_payment_utilization_corr` (0.7), `op_payment_concentration` (0.3) | Open Payments × Part D | Prescribing that tracks the manufacturers paying the prescriber is the Anti-Kickback / FCA fact pattern — payment-driven utilization rather than clinical need. |
| **drug_outlier** | `controlled_substance_share` (0.4), `high_cost_drug_share` (0.4), `drug_spread_anomaly` (0.3) | Part D + NADAC | Concentration in controlled / high-cost drugs, and billing above the national acquisition-cost benchmark (NADAC spread), are markup-fraud and diversion signatures. |
| **pill_mill** | `opioid_claim_share` (0.6), `opioid_long_acting_share` (0.4) | CMS opioid file | Opioid (and long-acting opioid) prescribing share far above specialty peers is the pill-mill pattern. |
| **dme_ring** | `dme_high_cost_item_share` (0.4), `ineligible_referral_share` (0.4), `dme_ordering_md_concentration` (0.4) | DMEPOS + Order/Referring | DME fraud: concentration in high-cost items, orders flowing from referrers not eligible to order DME, and a suspiciously narrow set of ordering physicians funneling a supplier. |
| **worthless_services** | `pbj_understaffing` (0.7), `capacity_mismatch` (0.6), `deficiency_count` (0.4) | PBJ staffing + POS + Care Compare | A facility billing for care it cannot physically deliver — understaffed below safe nurse-hours, billing beyond bed capacity, heavily cited — is the "worthless services" FCA theory. |
| **hospice_ineligibility** | `hospice_live_discharge_rate` (1.0) | Care Compare hospice | High live-discharge rates indicate enrolling patients who were never terminally ill — the core hospice-fraud pattern. |
| **saturation_fraud** | `market_saturation_index` (1.0) | CMS Market Saturation | Provider density far beyond what the local population needs marks supply-driven fraud markets (e.g., HHA/DME clusters). |
| **contract_pharmacy** | `contract_pharmacy_concentration` (1.0) | HRSA 340B | Unusual 340B contract-pharmacy footprints are a known diversion/duplicate-discount risk pattern. |
| **cost_report_fraud** | `hcris_cost_anomaly` (1.0) | HCRIS cost reports | Inflated wage-index / DSH / related-party / cost-to-charge ratios are direct cost-report fraud levers. |

### Dormant by data (need claim/line-level data not in the public PUFs)

| Scheme | Inputs | Why it can't run yet |
|---|---|---|
| **impossible_day** | `bene_per_day_p95`, `time_minutes_per_day` | Needs per-*day* service counts / procedure-time minutes (claim/line level with service dates). The annual by-provider PUF can't express "more hours than exist in a day." |
| **dme_ring** (ordering-MD piece) | `dme_ordering_md_concentration` | Needs supplier×ordering-MD claim pairs, which the by-referring-provider PUF doesn't pair. |

---

## 6. The label, PU learning, and the leakage discipline

**Label (`provider_on_exclusion`, falling back to `provider_on_leie`):** the
provider appears on an exclusion/debarment list. When supplementary sources are
loaded, the manifest's `label` is the **widened** `provider_on_exclusion` — a union
of LEIE + CMS revocations + SAM + OpenSanctions — with `exclusion_label_sources`
recording which list(s) matched, so you can weight or stratify by source.
`provider_on_leie` remains for back-compat. This is a **positive-unlabeled**
target: a `1` is a confirmed bad actor, but a `0` is *not* confirmed-clean — it's
merely uncaught. Train accordingly (PU learning / treat unlabeled as unlabeled).
Filter to fraud-relevant statutes for a cleaner positive set; the platform already
filters LEIE to the fraud statutes elsewhere.

**Leakage is made explicit so the backtest stays honest.** The manifest separates:

- **`leakage_hard`** — `billed_after_exclusion`, `excluded_after_billing`,
  `provider_on_leie`. These are derived from the provider's *own* exclusion, so
  training on them is circular (the model "predicts" the answer using the answer).
  **Never put these in the feature matrix.** They're exported only for analysis.
- **`leakage_adjacent`** — `within_2_hops_of_exclusion`, `shell_score`,
  `related_party_density(_norm)`, `subscore_ownership_integrity`. These are
  *proximity* to *other* parties' exclusions — genuinely predictive (rings are
  caught together), but correlated with the label and time-sensitive (an exclusion
  recorded after your feature window can leak). **Use them, but validate under a
  strict out-of-time split** (train on pre-cutoff exclusions, test on post-cutoff),
  not a random split. If they dominate a random-split model, that's a red flag to
  re-check temporally.

**Recommended validation:** out-of-time split is the credibility anchor; report
PR-AUC, precision@k, recall@k, and top-decile lift (accuracy is meaningless at a
~0.2% base rate). This mirrors the existing backtest that gets 7.9× top-decile lift.

---

## 7. Null semantics (important for LightGBM)

**Null means "this provider does not appear in this source," not zero.** A provider
absent from Part D has null Part-D columns — that is information (they don't
prescribe), not a zero-dollar prescriber. LightGBM handles nulls natively by
learning the best split direction for missing values, so **do not impute zeros** on
the source-derived columns; let the model treat them as missing. The dictionary's
non-null counts tell you each source's coverage. Subscores are null where a scheme
has no present input for that provider.

---

## 8. How to wire it into training (suggested)

```python
import json, pandas as pd
m = json.load(open("provider_features/feature_manifest.json"))
df = pd.read_parquet("provider_features/provider_features_for_model.parquet")

feature_cols = [c for c in (m["raw_feature_cols"] + m["peerpct_cols"] + m["subscore_cols"])
                if c not in m["leakage_hard"]]
X = df[feature_cols]                      # keep NaNs — LightGBM handles them
y = df[m["label"]].fillna(0).astype(int)  # PU positive label
# For the headline backtest, exclude m["leakage_adjacent"] from feature_cols and/or
# split out-of-time on exclusion date before trusting them.
```

Keep `npi` (and `org_node_id`) aside as keys for joining predictions back and for
group-aware splits (don't let two NPIs of the same org straddle train/test).

---

## 9. Honest limitations — and how each is now mitigated

Each of the four is real, but the platform now actively battles it:

- **Label ceiling.** LEIE captures *caught* fraud and skews toward certain schemes;
  precision@k and lift remain the honest metrics, not accuracy/recall against an
  incomplete ground truth. **Mitigation (built):** the label is now a *widened*
  multi-source positive — `provider_on_exclusion` unions LEIE + CMS revocations +
  SAM + OpenSanctions (each merged via `processed/exclusions_*.parquet`), with an
  `exclusion_label_sources` provenance column so you can weight or stratify by
  source. `provider_on_leie` is retained for back-compat. Evaluate lift *within*
  scheme families so a model can't hide by learning only LEIE-flavored fraud.
- **Peer grouping depends on taxonomy quality.** The percentiles are only as good
  as the NPPES taxonomy. **Mitigation (built):** the NUCC crosswalk
  (`ingest_cms/nucc_taxonomy.py`) rolls the noisy ~870-code taxonomy up to a
  clinically coherent **classification** cohort and adds it as a fallback rung on
  the peer ladder, so a thin/mis-coded taxonomy cell now ranks against its
  specialty group instead of going national or unscored.
- **Org→NPI broadcast** gives every NPI in an org the same ownership value.
  **Mitigation (built):** `org_member_count` lets the model discount a broadcast
  signal in a 5,000-NPI system vs. a 2-NPI shell, and `has_excluded_owner` (from
  the NPI's own owner-role) sharpens the smeared graph proximity into an NPI-level
  signal. Most importantly, the **graph embeddings** (`graph_emb_*`) give a provider
  that sits in the graph its *own* position vector rather than a smeared org value —
  the structural fix for broadcast. (Owner-role + embedding signals are
  `leakage_adjacent` — validate out-of-time. See `docs/platform/17` for the full
  representation-learning roadmap.)
- **Scale of `--with-analytics`.** **Mitigation (built):** growth and clinical
  plausibility now stream straight from the spending parquet via DuckDB
  (`*_from_parquet`); the 238M-row fact never enters pandas, so the enrichments
  scale to the full universe rather than a by-state slice.

---

*Investigative-analytics framework. These features are statistical risk rankings of
public data, surfaced as model inputs and leads for human and counsel review —
never accusations or adjudications of any person or organization.*
