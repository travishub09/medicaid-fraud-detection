# Model Runbook — using the provider features in your tree model (Travis)

This is the complete guide to `provider_features_for_model.parquet`: the reasoning
behind the approach, how to wire it into your graph-based tree model, and a
**metric-by-metric catalog** — for every column, what it is, what data it's computed
from, and why it's a valid fraud signal. Read alongside `feature_manifest.json`
(the machine-readable column roles) and `RUNBOOK_TREY.md` (how the file is produced).

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

## 2. What changed and improved since the first hand-off (walkthrough)

The first export you saw was essentially Trey's six rules-based scheme subscores on a
candidate set. This version is a different artifact. Here's everything that changed,
and why each helps your model — read this before the catalog so the new columns make
sense.

**Labels — from one flag to a rich, time-aware target.**
- *Before:* a single `provider_on_leie` boolean (caught, untyped, untimed).
- *Now:* `provider_on_exclusion` unions LEIE + CMS revocations + SAM + OpenSanctions
  + **DOJ/qui-tam case defendants**, with `exclusion_label_sources` provenance. DOJ
  positives carry a `fraud_scheme` and a **conduct window** (`conduct_start/end`).
- *Why it matters:* far more positives and less scheme bias; the conduct window lets
  you train **out-of-time** (score a provider on features that predate the fraud)
  and the scheme lets you measure lift **within scheme families**. Plus a Snorkel-style
  **`weak_label_score`** — a dense soft target fused from ~12 labeling functions — to
  pre-train on before fine-tuning the hard label.

**A real contrast set — from imbalanced soup to matched case-control.**
- *Now:* manufactured high-confidence negatives (`confirmed_clean`: institutional /
  long-tenure + benign + no fraud proximity) and a `--case-control` output
  (`provider_features_matched.parquet`) that pairs each positive with comparable
  clean controls (same specialty/geography/size). Train on that to learn *what
  differs* holding confounders fixed instead of fighting the 0.2% base rate.

**Peer grouping — fewer false positives.**
- *Now:* a NUCC taxonomy crosswalk rolls the noisy ~870-code taxonomy up to a
  coherent classification cohort and adds it as a fallback rung, so a thin or
  mis-coded specialty is ranked against the right peers (`peer_group_key`). Every
  peer-relative feature got cleaner as a result.

**The graph view — the signal your billing-only model can't see.**
- *Now:* DeepWalk-style node **embeddings** (`graph_emb_*`), a personalized-PageRank
  **fraud-proximity field**, structural **motifs** (k-core/triangles/clustering),
  and **temporal velocity** (how fast a provider's graph position is changing). A
  clean-billing provider in a fraud-dense neighborhood now lights up. This also
  **fixes the org→NPI broadcast problem** — each NPI carries its own graph position
  instead of one smeared org value (plus `org_member_count` + `has_excluded_owner`
  to sharpen it).

**New attribute families — separating fraud from mere anomaly.**
- *Now:* the **expected-billing residual** (`billing_residual` — unexplained billing
  after conditioning on size/specialty/breadth, so a big *honest* biller isn't
  punished); **cross-source consistency** flags (`consistency_flags` — registration
  record vs. billing); **address grounding** (mailbox/PO-box + live geocode); and a
  self-supervised **billing language model** (`billing_emb_*`, `billing_surprisal`,
  order-aware `sequence_surprisal`).

**Packaging — built for honest training.**
- Each adapter metric ships **raw + `__peerpct` + subscore** (use what wins).
- A **leakage manifest** splits `leakage_hard` (never train) from `leakage_adjacent`
  (train, but validate out-of-time) — keeps your backtest non-circular.
- **Point-in-time correctness:** a snapshot store + `--asof` graph builds let you
  reconstruct features as they stood before a label date (leakage handled
  architecturally, not as a caveat).
- **Scale:** the heavy enrichments (growth, plausibility, billing LM) are
  DuckDB-streamed (`--with-analytics`) so they run on the full universe.
- **No candidate gate:** the export now covers the full provider universe (you need
  the negatives), not just the suspicious tail.

The rest of this runbook details how to load it (§3), train on it (§4), and what
every resulting column means (§5).

## 3. Loading and column roles

Read the roles from the manifest — don't hard-code column lists:

```python
import json, pandas as pd
m = json.load(open("feature_manifest.json"))
df = pd.read_parquet("provider_features_for_model.parquet")

features = [c for c in (m["raw_feature_cols"] + m["peerpct_cols"]
                        + m["subscore_cols"] + m["embedding_cols"])
            if c not in m["leakage_hard"]]
X = df[features]                         # keep NaNs — LightGBM splits on "missing" natively
y = df[m["label"]].fillna(0).astype(int) # provider_on_exclusion (PU positive)
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

## 4. The recommended training recipe

1. **PU learning.** A `1` is a confirmed bad actor; a `0` is *unlabeled*, not
   confirmed clean. Use your Elkan–Noto / PU setup; treat `confirmed_clean == 1`
   (a manufactured negative) as your reliable-negative anchor set.
2. **Train on the matched set.** Use `provider_features_matched.parquet` (from
   `--case-control`): each positive paired with comparable clean controls (same
   specialty/geography/size, `match_id` ties them). The model learns *what differs*
   holding confounders fixed — sharper than fighting the 0.2% base rate.
3. **Validate out-of-time** (the only number worth defending). Use the conduct
   window: train on positives with `conduct_start < cutoff`, test on `≥ cutoff`
   (`feature_store.temporal_split`). For strict correctness, score each positive on
   features reconstructed *before* its `conduct_start` (`feature_store.asof_join`
   against snapshots, or an `--asof` graph build).
4. **Stratify metrics by `fraud_scheme`** so the model can't look good by only
   learning the schemes LEIE over-represents.
5. **Optionally distill the soft label.** `weak_label_score` is a dense
   probabilistic target — useful for semi-supervised pre-training before fine-tuning
   on the hard label.
6. **Group-aware splits.** Don't let two NPIs of the same `org_node_id` straddle
   train/test (the ownership signal is org-level).

---

## 5. Metric catalog — what each is, its data, and why it's fraud signal

The "why" is the False Claims Act / program-integrity theory that makes the variable
predict *prosecuted* fraud, not just unusual billing.

### 4.1 Identifiers & metadata (not features)
`npi`, `org_node_id`, `entity_type` (1=individual, 2=org), `primary_taxonomy`,
`practice_state`, `org_legal_name`, `peer_group_key` / `nucc_classification` /
`nucc_grouping` (the coherent specialty cohort used for percentiles). Keys, peer
context, and join handles.

### 4.2 Raw provider statistics (Medicaid spending + NPPES/PECOS) — clean features
| Metric | Definition | From | Why signal |
|---|---|---|---|
| `gross_paid` / `net_paid` | lifetime dollars billed / net of reversals | spending fact | scale context; net vs gross gap can flag reversal games |
| `service_volume` / `total_claim_lines` | services / claim lines | spending fact | denominators for intensity; raw scale |
| `n_distinct_hcpcs` | distinct procedure codes billed | spending fact | breadth — too broad for one provider is implausible |
| `tenure_months` / `n_active_months` | enrollment tenure / active months | PECOS/NPPES + spending | short tenure + big billing = fly-by-night |
| `org_member_count` | NPIs under the provider's org | entity graph | lets the model discount org-level signals in giant systems vs. 2-NPI shells |

### 4.3 Billing-anomaly concepts (peer percentiles, 0–1) — clean features
Computed as one-sided robust (median/MAD) percentiles within the taxonomy peer group.
| Metric | Definition | From | Why signal |
|---|---|---|---|
| `concentration` | how concentrated billing is in one code/service | spending HCPCS mix | mills pump one lucrative code; real practice spreads |
| `payment_intensity` | dollars per beneficiary vs peers | spending | upcoding/inflation/phantom add-ons extract more per patient |
| `service_intensity` | services per beneficiary vs peers | spending | too many services per patient to be real |
| `specialty_mismatch` | billed codes vs the provider's taxonomy | spending × NPPES taxonomy | billing codes your specialty never does = miscoding/fabrication |
| `temporal` | abnormal year-over-year billing change | spending trajectory | schemes ramp fast |

### 4.4 Scheme subscores (`subscore_*`, 0–1) — clean features
Each is a domain-weighted, sigmoid-squashed blend of peer-relative inputs (the
weights and inputs are in `PROVIDER_FEATURES_FOR_MODEL.md` §5). One column per scheme;
your tree decides how to combine them (we don't pre-collapse them).
`single_service_mill`, `payment_outlier`, `overutilization`, `specialty_mismatch`,
`rapid_ramp` (billing today); `upcoding` (Part B), `drug_outlier` (Part D + NADAC),
`pill_mill` (opioid), `pharma_kickback` (Open Payments × Part D), `dme_ring` (DMEPOS),
`worthless_services` (PBJ/POS/Care Compare), `hospice_ineligibility` (Care Compare),
`saturation_fraud` (Market Saturation), `contract_pharmacy` (340B), `cost_report_fraud`
(HCRIS), `invalid_identity` (deactivation + death); `ownership_integrity` (graph —
leakage-adjacent). Each fires only on an EXCESS vs peers; each maps to a real,
prosecuted FCA fact pattern.

### 4.5 Source-adapter raw features (+ `__peerpct`) — clean features
Each ships raw and as its one-sided taxonomy-peer percentile.
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

### 4.6 Entity-graph features — `leakage_adjacent` (validate out-of-time)
| Metric | Definition | From | Why signal |
|---|---|---|---|
| `within_2_hops_of_exclusion` | within 2 graph hops of an excluded party | graph | rings are caught together |
| `shell_score` | shell-company structure (thin name-only entities at one address) | graph | concealment |
| `related_party_density` / `_norm` | density of shared-owner relationships | graph | shell webs |
| `ownership_turnover` | owner entries+exits (diffed snapshots, bounded) | owner snapshots | change-of-ownership churn = concealment |

### 4.7 Graph representation learning
| Metric | Role | From | Why signal |
|---|---|---|---|
| `graph_emb_0..15` | DeepWalk-style node embedding (who you're connected to) | graph random walks → PPMI → SVD | a clean-billing provider in a fraud-dense neighborhood still lights up; *leakage-adjacent* (encodes exclusion neighborhood) |
| `graph_fraud_proximity` | personalized-PageRank fraud field, seeded from exclusions | graph | continuous guilt-by-association; *leakage-adjacent* |
| `graph_kcore`, `graph_triangles`, `graph_clustering`, `graph_degree` | structural motifs | graph | star hub / clique / pyramid apex shapes — **clean** |
| `graph_emb_drift`, `graph_degree_delta`, `graph_kcore_delta` | how fast the position is changing (snapshot diff) | feature snapshots | fly-by-night at the graph level — **clean** (`graph_fraud_proximity_delta` is leakage-adjacent) |

### 4.8 Fraud-vs-anomaly attributes — clean features
| Metric | Definition | From | Why signal |
|---|---|---|---|
| `billing_residual` | unexplained billing after a robust per-specialty regression on volume/lines/breadth | spending | conditions OUT legitimate size — a big honest biller scores low, a small inflater high |
| `expected_net_paid` | the model's expected billing | spending | the driver/denominator for the residual |
| `clinical_implausibility` | $-weighted share on codes rare for the taxonomy | spending × taxonomy | a hospice billing surgical codes |
| `local_volume_implausibility` | per-capita volume vs county population | spending × Census | phantom patients beyond what the population supports |
| `growth_level_shift`, `new_code_burst` | sustained billing step / burst of new codes | spending trajectory | scheme onset the YoY concept misses |
| `consistency_flags` (+ `incons_*`) | count of cross-source incoherences (individual at institutional scale, no-tenure full-scale biller, solo billing implausibly broad codes, one-NPI org at institutional scale) | NPPES/PECOS × billing | contradictions across independent systems are hard to fake |

### 4.9 External grounding — clean features
| Metric | Definition | From | Why signal |
|---|---|---|---|
| `addr_is_mailbox` | address is a CMRA/PMB/PO-box/mailbox-store | NPPES address string | can't deliver care from a mailbox |
| `addr_provider_count`, `addr_shared` | providers billing from the exact same address | NPPES addresses | shell farm at one address |
| `addr_geocoded`, `addr_no_match` | does the address resolve to a real location (live Census geocode) | Census geocoder | a billing address that doesn't geocode is a phantom-clinic tell |

### 4.10 Billing language model — clean features
| Metric | Definition | From | Why signal |
|---|---|---|---|
| `billing_emb_0..15` | self-supervised provider embedding (claim-weighted mean of code vectors) | spending code co-occurrence (PPMI/SVD) | dense representation of billing CONTENT, no labels |
| `billing_surprisal` | cross-entropy of the code mix vs the specialty's distribution | spending × taxonomy | "how unlikely is this billing for the specialty" — scheme-agnostic novelty |
| `sequence_surprisal` | negative log-likelihood of the code-ADOPTION transitions | spending (ordered) | unusual ORDER of adopting codes — what bag-of-codes misses |

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
| `confirmed_clean` / `clean_basis` | manufactured high-confidence negative (institutional/long-tenure + benign + no fraud proximity) and why |
| `weak_label_score` / `weak_label` / `weak_label_votes` | soft Snorkel-style label fused from ~12 labeling functions (a target; per-LF audit in the manifest) |
| `billed_after_exclusion`, `excluded_after_billing` | **`leakage_hard`** — derived from the provider's own exclusion; never train on these |

---

## 7. The leakage discipline (read before you trust a number)
- **`leakage_hard`** is circular (it encodes the answer). The export quarantines it
  out of `raw_feature_cols`; keep it out of `X`.
- **`leakage_adjacent`** (exclusion-proximity: `within_2_hops_of_exclusion`,
  `shell_score`, `graph_emb_*`, `graph_fraud_proximity`, `subscore_ownership_integrity`,
  `has_excluded_owner`, `graph_fraud_proximity_delta`) is genuinely predictive but
  correlated with the label and time-sensitive. Train with it, but make the
  **out-of-time split the headline** — if it dominates a random-split model, re-check
  temporally (use an `--asof` graph build so the proximity reflects only
  pre-conduct exclusions).
- **Honest metrics at a ~0.2% base rate**: PR-AUC, precision@k, recall@k, top-decile
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
