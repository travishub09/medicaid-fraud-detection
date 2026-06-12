# 04 — Model A: Organization Fraud-Risk and Exposure

Model A ranks organizations by **expected recoverable value**:
`ERV = P(recoverable fraud by scheme) × estimated dollar exposure`. It answers
*where* signal concentrates and hands each org a scheme hypothesis, an exposure
estimate, and a confidence band.

## Peer-group construction
For provider-level metrics, the peer group is **specialty (NPPES taxonomy / CMS
specialty) × geography × site of care**. Use state or hospital referral region for
geography; enforce a minimum peer count (e.g. n ≥ 30); when a cell is too sparse,
fall back to coarser geography (state, then national). Facilities get their own peer
logic (e.g. SNFs grouped by size band and region).

## Robust standardization (one-sided)
Most fraud signals are one-sided — unusually high level-5 coding is suspicious,
unusually low is not — measured on a robust scale a few extreme billers cannot
distort. For metric `m`, provider `i`, peer group `g`:

```
robust_z(i) = ( m_i − median_g(m) ) / ( 1.4826 × MAD_g(m) )
pos_z(i)    = max(0, robust_z(i))      # one-sided: only excess counts
clipped(i)  = min(pos_z(i), 8)         # cap extreme outliers
```

Guard against zero-MAD (degenerate) peer groups. For the public tool, expose the
intuitive **percentile rank**, not the z-score.

## Feature dictionary (feature → source → scheme hypothesis)

| Feature | Source | Scheme |
|---|---|---|
| em_high_level_share, em_level_mean | Part B | Upcoding |
| services_per_bene, allowed_per_bene | Part B | Over-utilization / overbilling |
| bene_per_day_p95, time_minutes_per_day | Part B | Impossible-day / phantom |
| code_concentration_hhi | Part B | Single-service mill |
| modifier_25_rate, modifier_59_rate | Part B | Unbundling |
| controlled_substance_share | Part D | Diversion / pill mill |
| brand_generic_cost_ratio, high_cost_drug_share | Part D | Pharma steering / drug fraud |
| dme_high_cost_item_share, dme_ordering_md_concentration | DMEPOS | DME fraud / kickback ring |
| op_payment_utilization_corr, op_payment_concentration | Open Payments + claims | Kickback (AKS) |
| market_saturation_index | Market Saturation | Saturation fraud |
| pe_owned_flag, ownership_turnover | Ownership files / PECOS | Roll-up risk / concealment |
| related_party_density, referral_ring_flag | **Graph** | Ring / self-referral |
| excluded_party_distance, shell_score | **Graph** + LEIE/PECOS | Integrity / shell |
| pbj_staffing_z | PBJ + claims | Worthless services |
| hospice_live_discharge_rate | Care Compare | Hospice ineligibility |
| hcris_cost_alloc_anomaly | HCRIS | Cost-report fraud |
| yoy_utilization_growth_z, new_npi_rapid_ramp | Part B/D + PECOS | Ramp / fly-by-night |

The graph-derived features (`related_party_density`, `excluded_party_distance`,
`shell_score`, plus community/centrality) are **now produced** by
`src/entity_graph/graph_features.py`.

## Cold-start scoring (label-free, day one, explainable)
Group features into scheme-specific subscores, combine with domain-prior weights,
squash to 0–1, then combine schemes with a **noisy-OR** (an org is high-risk if *any*
scheme fires, not on the average):

```
subscore_s    = sigmoid( Σ_k  w[s,k] · clipped_feature[k] )
org_prob      = 1 − Π_s ( 1 − subscore_s )
adjusted_prob = org_prob × sector_prior_multiplier × (1 + graph_risk_boost)
exposure      = annual program payments × scheme_recovery_multiplier
ERV           = adjusted_prob × exposure
```

No labels, runs the day data lands, decomposes into named drivers — which matters for
the public tool's defamation safety and for the credibility of anything handed to
counsel.

## Supervised graduation (once outcomes accumulate)
Settlements, CIAs, exclusions, indictments are positives; everything else is
unlabeled → a **positive-unlabeled** problem (Elkan-Noto, spy, or bagged
positive-vs-random-unlabeled). Train a gradient-boosted model on the same features +
graph features, calibrate with **isotonic regression**, explain with **SHAP**.
Estimate magnitude with a **separate quantile-regression** model so you output a
recovery *distribution* — exactly what Model C needs.

## The false-positive trap (it will bite you)
The busiest legitimate providers — referral centers, subspecialists, sicker panels —
look like outliers on raw utilization. Mitigations: include **acuity controls** (HCC
risk where available, subspecialty/referral-center flags, panel complexity); prefer
features hard to explain by legitimate complexity (impossible-day, excluded-party
proximity, shell patterns, payment-utilization correlation) over raw volume; always
store and present the **drivers**, never a bare score.

## Validation and latency
Temporal holdout: train through year T, test whether flagged orgs were named in
enforcement after T. Report **precision@k** and **lift over the enforcement-prior
baseline**, not raw accuracy (base rate is tiny). Public CMS files lag ~2 years, so
Model A detects **structural, entrenched** risk (where), not this week's scheme — the
*when* comes from Model B's departure detection.

## Current state in this repo

| Spec element | Status | Where |
|---|---|---|
| Peer groups, one-sided robust-z | **Built** | `ingest/features.py`, `leads/refine_layer2_v3.py` |
| De-correlated concept anomaly + volume gate | **Built** (exceeds spec) | `leads/refine_layer2_v3.py` |
| 3-layer explainable prioritization (rules / anomaly / ownership) | **Built** | `leads/detect.py` |
| Company-grain scoring | **Built** | `leads/company_lead_tracker.py` |
| LEIE temporal validation (2.0× top-decile lift) | **Built** | `src/backtest/` |
| Graph features (distance, density, shell, community) | **Built** (Increment 1) | `src/entity_graph/graph_features.py` |
| Scheme subscores → noisy-OR → ERV (v1, label-free) | **Built** | `src/model_a/scheme_subscores.py`, `scoring.py`, `__main__.py` (run: `python -m src.model_a --fixture`) |
| Enforcement-prior sector map (placeholder multipliers) | **Built** | `src/model_a/sector_priors.py` — re-derive from the DOJ case DB (GAPS #13) |
| Target dossiers (drivers + alternative explanations + disclaimer) | **Built** | `src/model_a/dossier.py` |
| PU supervised graduation + quantile exposure | Scaffold | `src/model_a/supervised.py` |
| Temporal-holdout precision@k harness (generalized) | Scaffold | `src/model_a/validation.py` |

**v1 notes:** the composite runs on today's company-grain concept percentiles +
entity-graph features; the feature registry already lists the Part B/D/DMEPOS
columns so the procurement files (09) drop in without code changes. Graph features
feed the ownership_integrity subscore; the separate graph-risk *boost* uses
ring-structure membership only — one fact never counts twice.

**Next increments:** real exposure (annual payments per org from spending_fact at
company grain), Part B adapter, then the supervised graduation once the DOJ case DB
exists.
