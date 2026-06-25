# Model Runbook — using the provider features in your tree model (Travis)

This is the complete guide to `provider_features_for_model.parquet`: what it is, how
to **fold these provider-level attributes into your model training**, and a
metric-by-metric catalog — for every column, what it is, what data it's computed
from, in plain terms how it's calculated, and why it's a valid fraud signal
(including the full per-scheme breakdown, now merged in). Read alongside
`feature_manifest.json` (the machine-readable column roles) and `RUNBOOK_TREY.md`
(how the file is produced).

**The one-line summary:** this file is one row per provider (NPI) and a few hundred
columns. You join it into your provider table on `npi`, treat the documented columns
as additional features, train your existing tree model on them against the provided
label, and validate out-of-time. Nothing about your model architecture changes — you
just have far richer, fraud-specific inputs.

---

## 1. The approach, and why it's shaped this way

The goal is to let your model **separate known fraud actors from known
non-offenders**, not "weird from normal." Three principles drive the design:

1. **Three independent views of each provider.** Fraud that hides in one view shows
   in another, so the file carries all three and lets your tree combine them:
   - **what they bill** — peer-relative billing anomalies + an expected-billing
     residual + a self-supervised billing language model;
   - **who they're connected to** — entity-graph embeddings, a fraud-proximity
     field, structural motifs, and how fast that structure is changing;
   - **whether their story is internally consistent** — cross-source incoherence
     between the NPPES/PECOS registration record and the billing behavior, and
     whether the billing address is even a real place.
2. **Anomaly ≠ fraud.** Raw peer percentiles punish the legitimately large. Several
   features (the expected-billing residual especially) are *conditioned* on
   legitimate covariates so only the **unexplained** part scores.
3. **Labels are the binding constraint, so we widen them.** The target unions every
   exclusion/enforcement source, adds scheme-typed conduct windows, manufactures
   high-confidence clean negatives, and adds a soft weak-supervision label — far
   more signal than the raw LEIE flag.

Everything is **point-in-time-able** (snapshot store + `--asof` graph builds) so you
can train and validate without leaking the future.

---

## 2. The ground-up redesign — what we built and why (walkthrough)

The export you last reviewed was essentially Trey's rules-based scheme subscores.
Since then we ran a **ground-up redesign of the whole architecture and data
strategy** — the goal being to produce provider attributes that let your model
separate *known fraud actors from known non-offenders*, not "weird from normal."
That redesign (documented in full in `docs/platform/17-provider-signal-architecture.md`)
is everything below. Each item is *what we built → why it helps your model*.

**Labels — from one flag to a rich, time-aware target.**
- *Before:* a single `provider_on_leie` boolean (caught, untyped, untimed).
- *Now:* `provider_on_exclusion` unions LEIE + CMS revocations + SAM + OpenSanctions
  + **DOJ/qui-tam case defendants**, with `exclusion_label_sources` provenance. DOJ
  positives carry a `fraud_scheme` and a **conduct window** (`conduct_start/end`).
- *Why it matters:* far more positives and less scheme bias; the conduct window lets
  you train **out-of-time** (score a provider on features that predate the fraud)
  and the scheme lets you measure lift **within scheme families**. Plus a Snorkel-style
  **`weak_label_score`** — a dense soft target fused from ~12 labeling functions.

**A real contrast set — from imbalanced soup to matched case-control.**
- *Now:* manufactured high-confidence negatives (`confirmed_clean`) and a
  `--case-control` output (`provider_features_matched.parquet`) that pairs each
  positive with comparable clean controls (same specialty/geography/size). Train on
  that to learn *what differs* holding confounders fixed instead of fighting the
  0.2% base rate.

**Peer grouping — fewer false positives.**
- *Now:* a NUCC taxonomy crosswalk rolls the noisy ~870-code taxonomy up to a
  coherent classification cohort, so a thin or mis-coded specialty is ranked against
  the right peers (`peer_group_key`). Every peer-relative feature got cleaner.

**The graph view — the signal your billing-only model can't see.**
- *Now:* DeepWalk-style node **embeddings** (`graph_emb_*`), a personalized-PageRank
  **fraud-proximity field**, structural **motifs**, and **temporal velocity**. A
  clean-billing provider in a fraud-dense neighborhood now lights up. This also
  **fixes the org→NPI broadcast problem** — each NPI carries its own graph position
  instead of one smeared org value (plus `org_member_count` + `has_excluded_owner`).

**New attribute families — separating fraud from mere anomaly.**
- *Now:* the **expected-billing residual** (`billing_residual`), **cross-source
  consistency** flags (`consistency_flags`), **address grounding** (mailbox/PO-box +
  live geocode), and a self-supervised **billing language model** (`billing_emb_*`,
  `billing_surprisal`, order-aware `sequence_surprisal`).

**Packaging — built for honest training.**
- Each adapter metric ships **raw + `__peerpct` + subscore** (use what wins).
- A **leakage manifest** splits `leakage_hard` (never train) from `leakage_adjacent`
  (train, but validate out-of-time) — keeps your backtest non-circular.
- **Point-in-time correctness:** a snapshot store + `--asof` graph builds.
- **Scale:** heavy enrichments are DuckDB-streamed (`--with-analytics`).
- **No candidate gate:** the export covers the full provider universe (you need the
  negatives), not just the suspicious tail.

---

## 3. Loading the file and reading the column roles

Don't hard-code column lists — read the roles from the manifest:

```python
import json, pandas as pd
m  = json.load(open("feature_manifest.json"))
df = pd.read_parquet("provider_features_for_model.parquet")
```

| Manifest key | What it is | Use |
|---|---|---|
| `label` | `provider_on_exclusion` (falls back to `provider_on_leie`) | the PU target |
| `raw_feature_cols` | clean, trainable raw + engineered features | train on |
| `peerpct_cols` | one-sided taxonomy-peer percentile of each adapter metric | train on |
| `subscore_cols` | the 0–1 scheme subscores | train on |
| `embedding_cols` | graph + billing embedding columns | train on (graph_emb_* are leakage-adjacent — see §7) |
| `leakage_hard` | derived from the provider's OWN exclusion | **never train on** |
| `leakage_adjacent` | exclusion-PROXIMITY signals | train, but validate out-of-time |
| `label_metadata` | target-derived (scheme, conduct window, weak label, clean anchors) | targets / stratifiers, **not features** |
| `weak_supervision` | per-labeling-function accuracy + coverage | audit |

**Null ≠ zero.** A null source column means the provider isn't in that source
(doesn't prescribe, isn't a facility). Don't impute — let the trees handle missing.

---

## 4. Using these attributes in your model training (step by step)

These columns are meant to become **new features in your existing provider table**,
trained by your existing graph-based tree model. Here's the whole flow.

**Step 1 — Join into your provider table on `npi`.** It's one row per NPI, so a
left join adds the columns with no fan-out:

```python
provider_table = provider_table.merge(df, on="npi", how="left")
```

**Step 2 — Build the feature matrix from the manifest, excluding hard leakage:**

```python
feat_cols = [c for c in (m["raw_feature_cols"] + m["peerpct_cols"]
                         + m["subscore_cols"] + m["embedding_cols"])
             if c not in m["leakage_hard"]]
X = provider_table[feat_cols]                  # keep NaNs - the tree splits on "missing"
y = provider_table[m["label"]].fillna(0).astype(int)   # provider_on_exclusion
```

**Step 3 — Train your tree as usual, with PU framing.** A `1` is a confirmed bad
actor; a `0` is *unlabeled*, not confirmed clean. Use your Elkan–Noto / PU setup and
treat `confirmed_clean == 1` as the reliable-negative anchor set. Don't impute the
NaNs — LightGBM/XGBoost handle missing natively and the missingness is informative.

**Step 4 — Prefer the matched training set when you have it.** Train on
`provider_features_matched.parquet` (from `--case-control`): each positive paired with
comparable clean controls, `match_id` ties a case to its controls. The model learns
*what differs* holding specialty/geography/size fixed — much sharper than the raw
0.2%-positive population.

**Step 5 — Validate out-of-time** (the only number worth defending to counsel):

```python
from src.model_a.feature_store import temporal_split
train_mask, test_mask = temporal_split(provider_table, cutoff_year=2021)  # by conduct_start
```

For strict correctness, score each positive on features reconstructed *before* its
`conduct_start` — either `feature_store.asof_join` against snapshots, or train on an
`--asof` graph build (`RUNBOOK_TREY.md` §5). Report **PR-AUC, precision@k, recall@k,
top-decile lift**, stratified **within `fraud_scheme`** and within size bands — not
accuracy (everything is "accurate" at a 0.2% base rate).

**Step 6 — Keys, not features.** Keep `npi` and `org_node_id` aside for joining
predictions back and for **group-aware splits** — don't let two NPIs of the same org
straddle train/test (the ownership signal is org-level).

**Step 7 — Optional: pre-train on the soft label.** `weak_label_score` is a dense
probabilistic target; semi-supervised pre-training on it, then fine-tuning on the
hard label, can lift performance when hard positives are scarce.

**What you do NOT train on:** anything in `leakage_hard` (circular) or
`label_metadata` (target-derived). The manifest lists both explicitly.

---

## 5. Metric catalog — what each column is, its data, how it's calculated, and why it's signal

The "why" is the False Claims Act / program-integrity theory that makes the variable
predict *prosecuted* fraud, not just unusual billing.

### 5.1 Identifiers & metadata (not features)
`npi`, `org_node_id`, `entity_type` (1=individual, 2=org), `primary_taxonomy`,
`practice_state`, `org_legal_name`, `peer_group_key` / `nucc_classification` /
`nucc_grouping` (the coherent specialty cohort used for percentiles). Keys, peer
context, and join handles.

### 5.2 Raw provider statistics (Medicaid spending + NPPES/PECOS) — clean features
| Metric | Definition | From | Why signal |
|---|---|---|---|
| `gross_paid` / `net_paid` | lifetime dollars billed / net of reversals | spending fact | scale context; net vs gross gap can flag reversal games |
| `service_volume` / `total_claim_lines` | services / claim lines | spending fact | denominators for intensity; raw scale |
| `n_distinct_hcpcs` | distinct procedure codes billed | spending fact | breadth — too broad for one provider is implausible |
| `tenure_months` / `n_active_months` | enrollment tenure / active months | PECOS/NPPES + spending | short tenure + big billing = fly-by-night |
| `org_member_count` | NPIs under the provider's org | entity graph | lets the model discount org-level signals in giant systems vs. 2-NPI shells |

### 5.3 Billing-anomaly concepts (peer percentiles, 0–1) — clean features
Each is a one-sided robust (median/MAD) percentile within the taxonomy peer group.
| Metric | Definition | From | Why signal |
|---|---|---|---|
| `concentration` | how concentrated billing is in one code | spending HCPCS mix | mills pump one lucrative code; real practice spreads |
| `payment_intensity` | dollars per beneficiary vs peers | spending | upcoding/inflation/phantom add-ons extract more per patient |
| `service_intensity` | services per beneficiary vs peers | spending | too many services per patient to be real |
| `specialty_mismatch` | billed codes vs the provider's taxonomy | spending × NPPES taxonomy | billing codes your specialty never does = miscoding/fabrication |
| `temporal` | abnormal year-over-year billing change | spending trajectory | schemes ramp fast |

### 5.4 The fraud schemes (`subscore_*`, 0–1) — the merged scheme catalog

A "scheme" is a named fraud pattern; its `subscore_<scheme>` is a 0–1 score for how
strongly a provider matches it. One column per scheme — your tree decides how to
combine them (we don't pre-collapse them).

**How every subscore is calculated, in plain terms.** All schemes work the same way,
in three steps:
1. **Measure the behavior from the data** — each scheme watches one or a few specific
   numbers (e.g., how concentrated a provider's billing is in one code, or what
   fraction of its drug claims are opioids).
2. **Compare to same-specialty peers** — we rank each number against every other
   provider in the same specialty and turn it into a percentile (0–1), counting only
   the suspicious direction (billing *more* than peers). A family doctor is judged
   against family doctors, not labs. (Robust median/MAD ranking; hard rule #8.)
3. **Blend and squash** — when a scheme watches several numbers we take a weighted
   average, then squeeze it onto a 0–1 score where ~0.5 is a typical provider and
   values near 1 are strong matches. A behavior whose source file isn't loaded is
   simply skipped and the others reweighted. (A given fact feeds exactly one scheme,
   so nothing is double-counted.)

#### Billing-shape schemes — score today, from the Medicaid spending file
- **`single_service_mill`** — *detects* a "mill" built to pump one lucrative code vs.
  a real practice that bills a spread. *How:* we measure the share of the provider's
  dollars sitting in its single biggest code (from the spending file), rank it against
  same-specialty peers, and score high when one code dominates far more than peers.
- **`payment_outlier`** — *detects* extracting far more money per patient than peers
  (upcoding, inflated units, phantom add-ons). *How:* we compute dollars per
  beneficiary from the spending file and score how far above same-specialty peers it
  sits.
- **`overutilization`** — *detects* billing more services per patient than is
  clinically plausible. *How:* services per beneficiary from the spending file, ranked
  against peers; high = far more services per patient than the specialty norm.
- **`specialty_mismatch`** — *detects* billing codes the provider's specialty almost
  never bills (miscoding/fabrication). *How:* we blend three things — how far the
  code mix drifts from the taxonomy, the dollar share spent on codes that are rare for
  that specialty (from spending × NPPES taxonomy), and how far per-capita volume
  exceeds what the county population supports (× Census) — then rank and squash.
- **`rapid_ramp`** — *detects* a scheme spinning up. *How:* we look at the monthly
  billing trajectory for a sudden sustained jump and for a burst of newly-billed
  codes, rank both, and score high when billing ramps or the code mix pivots abruptly.

#### Ownership / identity schemes — the signals not in the claims file
- **`ownership_integrity`** *(leakage-adjacent)* — *detects* concealment structure.
  *How:* from the entity graph (PECOS owners + LEIE/OpenSanctions), we combine whether
  the provider sits within two hops of an already-excluded party, a shell-company
  score, how many other orgs share its owners, and how much its ownership has churned
  across monthly snapshots — weighted toward the exclusion link.
- **`invalid_identity`** — *detects* billing under a deactivated or deceased identity.
  *How:* we take the provider's deactivation date (NPPES report) and DOB-corroborated
  death date (SSA Death Master File), then measure the share of its dollars billed
  *on or after* that date; any meaningful post-event billing scores high.

#### Source-specific schemes — light up as each file lands
- **`upcoding`** — *detects* over-billing the highest-complexity office-visit codes.
  *How:* from Medicare Part B, the share of E&M billing at the top levels and the mean
  E&M level, ranked against peers.
- **`pharma_kickback`** — *detects* prescribing driven by industry money (Anti-Kickback).
  *How:* we correlate a prescriber's Part D drug use with the manufacturers paying them
  (Open Payments) and how concentrated those payments are; high correlation = scoring high.
- **`drug_outlier`** — *detects* diversion / drug-markup fraud. *How:* from Part D, the
  share of controlled and high-cost drugs, plus how much the provider bills *above* the
  national acquisition-cost benchmark (NADAC) — blended and ranked.
- **`pill_mill`** — *detects* opioid diversion. *How:* from the opioid file, the share
  of claims that are opioids and the share that are long-acting opioids, ranked against
  peers (the long-acting split separates chronic-pain practices from diversion mills).
- **`dme_ring`** — *detects* durable-equipment fraud. *How:* from DMEPOS, the
  concentration in high-cost items; plus the share of referred dollars whose referrer
  isn't even eligible to order DME (Order & Referring). (A third signal — one physician
  funneling one supplier — waits on pairing data.)
- **`worthless_services`** — *detects* a facility billing for care it can't deliver.
  *How:* from PBJ staffing, Care Compare deficiencies, and the Provider-of-Services
  file, how understaffed it is, how heavily cited, and whether billing exceeds physical
  capacity — ranked against facility peers.
- **`hospice_ineligibility`** — *detects* enrolling patients who were never terminal.
  *How:* the live-discharge rate from Care Compare, ranked against hospice peers.
- **`saturation_fraud`** — *detects* supply-driven fraud markets. *How:* the
  market-saturation index (providers per capita for the service) from CMS Market
  Saturation; high = far more supply than the population needs.
- **`contract_pharmacy`** — *detects* 340B diversion / duplicate-discount risk. *How:*
  the contract-pharmacy concentration from HRSA 340B OPAIS.
- **`cost_report_fraud`** — *detects* cost-report inflation. *How:* from HCRIS cost
  reports, the worst of the abused cost ratios (wage-index, DSH, related-party,
  cost-to-charge), ranked against facility peers.

#### Dormant by data (stay null until the data exists — not a code gap)
- **`impossible_day`** — more services/procedure-minutes in a day than physically
  possible. Needs claim/line-level data with service dates (the annual PUF can't
  express per-day counts).
- **`dme_ring` ordering-MD concentration** — one physician funneling one supplier.
  Needs DMEPOS line-level pairing supplier ↔ ordering MD.

### 5.5 Source-adapter raw features (+ `__peerpct`) — clean features
Each ships raw and as its one-sided taxonomy-peer percentile (so your tree can use the
absolute value or the peer-relative rank).
| Metric | From | Why signal |
|---|---|---|
| `em_high_level_share`, `em_level_mean` | Part B | disproportionate top-level E&M = upcoding |
| `controlled_substance_share`, `high_cost_drug_share` | Part D | diversion / markup mix |
| `drug_spread_anomaly` | Part D × NADAC | billing above the national acquisition cost |
| `opioid_claim_share`, `opioid_long_acting_share` | opioid file | pill-mill pattern; long-acting split distinguishes chronic pain from diversion |
| `op_payment_concentration`, `op_payment_utilization_corr` | Open Payments (× Part D) | prescribing that tracks who pays you = AKS/FCA |
| `dme_high_cost_item_share`, `ineligible_referral_share` | DMEPOS, Order&Referring | high-cost DME concentration; orders from ineligible referrers |
| `contract_pharmacy_concentration` | 340B OPAIS | diversion/duplicate-discount footprint |
| `market_saturation_index` | Market Saturation | supply-driven fraud markets |
| `hospice_live_discharge_rate` | Care Compare | enrolling non-terminal patients |
| `pbj_understaffing`, `deficiency_count`, `capacity_mismatch` | PBJ, Care Compare, POS | billing for care a facility can't deliver |
| `hcris_cost_anomaly` | HCRIS | wage-index/DSH/related-party cost-report inflation |
| `billing_after_deactivation`, `billing_after_death` | NPPES deactivation, SSA DMF | billing under a dead/deactivated identity |

### 5.6 Entity-graph features — `leakage_adjacent` (validate out-of-time)
| Metric | Definition | From | Why signal |
|---|---|---|---|
| `within_2_hops_of_exclusion` | within 2 graph hops of an excluded party | graph | rings are caught together |
| `shell_score` | shell-company structure (thin name-only entities at one address) | graph | concealment |
| `related_party_density` / `_norm` | density of shared-owner relationships | graph | shell webs |
| `ownership_turnover` | owner entries+exits (diffed snapshots, bounded) | owner snapshots | change-of-ownership churn = concealment |

### 5.7 Graph representation learning
| Metric | Role | From | Why signal |
|---|---|---|---|
| `graph_emb_0..15` | node embedding (who you're connected to) | graph random walks → PPMI → SVD | a clean-billing provider in a fraud-dense neighborhood still lights up; *leakage-adjacent* |
| `graph_fraud_proximity` | personalized-PageRank fraud field, seeded from exclusions | graph | continuous guilt-by-association; *leakage-adjacent* |
| `graph_kcore`, `graph_triangles`, `graph_clustering`, `graph_degree` | structural motifs | graph | star hub / clique / pyramid apex shapes — **clean** |
| `graph_emb_drift`, `graph_degree_delta`, `graph_kcore_delta` | how fast the position is changing (snapshot diff) | feature snapshots | fly-by-night at the graph level — **clean** (`graph_fraud_proximity_delta` is leakage-adjacent) |

### 5.8 Fraud-vs-anomaly attributes — clean features
| Metric | Definition | From | Why signal |
|---|---|---|---|
| `billing_residual` | unexplained billing after a robust per-specialty regression on volume/lines/breadth | spending | conditions OUT legitimate size — a big honest biller scores low, a small inflater high |
| `expected_net_paid` | the model's expected billing | spending | the driver/denominator for the residual |
| `clinical_implausibility` | $-weighted share on codes rare for the taxonomy | spending × taxonomy | a hospice billing surgical codes |
| `local_volume_implausibility` | per-capita volume vs county population | spending × Census | phantom patients beyond what the population supports |
| `growth_level_shift`, `new_code_burst` | sustained billing step / burst of new codes | spending trajectory | scheme onset the YoY concept misses |
| `consistency_flags` (+ `incons_*`) | count of cross-source incoherences (individual at institutional scale, no-tenure full-scale biller, solo billing implausibly broad codes, one-NPI org at institutional scale) | NPPES/PECOS × billing | contradictions across independent systems are hard to fake |

### 5.9 External grounding — clean features
| Metric | Definition | From | Why signal |
|---|---|---|---|
| `addr_is_mailbox` | address is a CMRA/PMB/PO-box/mailbox-store | NPPES address string | can't deliver care from a mailbox |
| `addr_provider_count`, `addr_shared` | providers billing from the exact same address | NPPES addresses | shell farm at one address |
| `addr_geocoded`, `addr_no_match` | does the address resolve to a real location (live Census geocode) | Census geocoder | a billing address that doesn't geocode is a phantom-clinic tell |

### 5.10 Billing language model — clean features
| Metric | Definition | From | Why signal |
|---|---|---|---|
| `billing_emb_0..15` | self-supervised provider embedding (claim-weighted mean of code vectors) | spending code co-occurrence (PPMI/SVD) | dense representation of billing CONTENT, no labels |
| `billing_surprisal` | how unlikely the code mix is for the specialty | spending × taxonomy | scheme-agnostic novelty signal |
| `sequence_surprisal` | how unusual the ORDER of adopting codes is | spending (ordered) | what bag-of-codes misses |

---

## 6. Label & label-metadata catalog (targets, not features)
| Column | Meaning |
|---|---|
| `provider_on_exclusion` | **the label** — on any exclusion list OR a resolved DOJ defendant (PU positive) |
| `exclusion_label_sources` | which source(s) matched: `leie`, `medicare_revocation`, `sam`, `opensanctions`, `doj_case` |
| `provider_on_leie` | the original LEIE-only flag (back-compat) |
| `fraud_scheme` | the scheme from the DOJ case (for scheme-stratified eval) |
| `conduct_start` / `conduct_end` | the conduct window from the case text (for OUT-OF-TIME training) |
| `case_ids` | the source case id(s) |
| `confirmed_clean` / `clean_basis` | manufactured high-confidence negative and why |
| `weak_label_score` / `weak_label` / `weak_label_votes` | soft Snorkel-style label fused from ~12 labeling functions |
| `billed_after_exclusion`, `excluded_after_billing` | **`leakage_hard`** — never train on these |

---

## 7. The leakage discipline (read before you trust a number)
- **`leakage_hard`** is circular (it encodes the answer). Quarantined out of
  `raw_feature_cols`; keep it out of `X`.
- **`leakage_adjacent`** (exclusion-proximity: `within_2_hops_of_exclusion`,
  `shell_score`, `graph_emb_*`, `graph_fraud_proximity`, `subscore_ownership_integrity`,
  `has_excluded_owner`, `graph_fraud_proximity_delta`) is genuinely predictive but
  correlated with the label and time-sensitive. Train with it, but make the
  **out-of-time split the headline** — if it dominates a random-split model, re-check
  temporally (use an `--asof` graph build so proximity reflects only pre-conduct
  exclusions).
- **Honest metrics at a ~0.2% base rate:** PR-AUC, precision@k, recall@k, top-decile
  lift, within-scheme and within-size stratification — not accuracy.

---

## 8. Honest limitations
- The label is still an incomplete ground truth (caught fraud); widening + weak
  supervision mitigate but don't erase the bias — lead with lift, not recall.
- The graph embedding/fraud-field encode exclusion neighborhood (leakage-adjacent).
- `--with-analytics` (billing LM, plausibility, growth) is opt-in and heavier; run on
  a filtered spending file if RAM-bound.
- The sequence model is an n-gram today; a neural transformer is a drop-in upgrade
  behind the same `sequence_surprisal` interface once a GPU + line-level claims exist.

*Investigative-analytics framework. These features are statistical risk rankings of
public data, surfaced as model inputs and leads for human and counsel review — never
accusations or adjudications of any person or organization.*
