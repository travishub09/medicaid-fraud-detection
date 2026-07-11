# Final Handoff: the Frozen Package for Travis and the Result for Brad

Written July 11, 2026, after run 5 completed. Every number below comes from the
final frozen build (the one with 2023 vintage caps, as-of billing, 5-year
trajectory slopes, and the full leakage fence).

## 1. The one-paragraph result

We froze the model's knowledge at December 2023 and asked it to find the
providers who got banned afterward. The graph features (shell addresses,
related-party webs) beat a model without them by a real, statistically clean
margin against same-size, same-specialty controls. We have now rebuilt the
test three times, each time making the non-graph model stronger, and the graph
edge held every time: +0.042, then +0.037, then +0.035 ROC-AUC, with this
final run's confidence intervals fully above zero on BOTH metrics (ROC-AUC
+0.035, CI +0.014 to +0.054; PR-AUC +0.044, CI +0.014 to +0.072). An effect
that survives three rebuilds against rising baselines is not an artifact.

## 2. What the frozen package contains (all in model_a\frozen_2023-12\)

| File | What it is |
|---|---|
| provider_features_for_model.parquet | 617,062 providers x 211 columns, 126 trainable |
| feature_manifest.json | THE contract: label, leakage lists, vintage classes, group_id |
| PROVIDER_FEATURES_DICTIONARY.md | per-column notes and coverage |
| future_bans_after_2023-12.csv | the held-out forward label: 4,401 first bans after the freeze |
| NETWORK_AB_REPORT.md | the A/B result above, with all three test designs |
| SOURCES_REPORT.md / EXPECTATIONS_REPORT.md | data provenance + calc integrity (1 known FAIL: betweenness, disabled at scale) |

## 3. Message to send Travis

Subject: Final frozen training package. The graph edge replicated a third time.

Travis,

The frozen package is final. Same folder as before (frozen_2023-12), new
build: 617,062 providers by 211 columns, 126 trainable features, built
entirely from data available before 2024. What is new since the last file:

1. Every annual CMS file is now vintage-capped at 2023, so no post-cutoff
   behavior leaks in through a newer file edition.
2. Year-over-year deltas AND 5-year trajectory slopes for Part B, Part D,
   opioid, and DME metrics, computed from pre-cutoff years only. Solo they
   test weak in-time; they are there for the tree to use in interactions and
   for the forward test.
3. The billing language model ran leakage-correct on the pre-cutoff claims:
   billing_surprisal (AUC 0.67 at 96 percent coverage) and sequence_surprisal
   (0.66 at 100 percent) are the two best broad-coverage honest features we
   have. The billing_emb_* columns are the same model's embedding.
4. A J-code drug markup score (priced above same-drug peers), a 340B contract
   pharmacy concentration, and a DME referrer eligibility check.

Read the manifest before training. The rules that matter:

- Train the target as provider_on_exclusion. Never train on leakage_hard.
- leakage_adjacent (11 columns now) is out-of-time only. Two additions since
  last time and both matter: billing_after_deactivation and the DME
  ineligible-referrer set score AUC 0.93 to 0.96 in-time BECAUSE they encode
  the label (exclusion strips O&R eligibility and deactivates NPIs). They are
  lead flags, not training wins.
- New: feature_vintage in the manifest classes every feature as
  point_in_time, annual_capped, reference, or current_state. The current_state
  class (ownership structure, facility files, addresses) cannot be truly
  frozen because no historical editions exist. Train a strict variant without
  that class and compare: the difference bounds how much the structure proxies
  matter. The A/B delta is immune to this either way (both arms share them).
- Use group_id for group-aware CV (providers in one org must not straddle
  folds) and respect the assessable flag.
- betweenness is constant zero (disabled at 9.6M-node scale). Drop it.

The A/B on this exact matrix: structural graph features only, forward label,
size-and-specialty matched: ROC-AUC 0.779 vs 0.746, delta +0.035 (CI +0.014
to +0.054); PR-AUC delta +0.044 (CI +0.014 to +0.072). Full-population
forward is null, as expected at a 0.16 percent base rate; judge the family on
the matched design. Third rebuild, third KEEP.

Trey

## 4. Message to send Brad

Subject: The result held through three rebuilds. Two small asks.

Brad,

The number I told you about held up, and the way it held up is the story. We
rebuilt the blind forward test three times. Each rebuild made the non-graph
model smarter: correct-year data files everywhere, multi-year trend
trajectories, a billing language model over 194 million claim rows, drug
markup versus same-drug peers. If the graph edge were an artifact, the
stronger baselines would have eaten it. Instead: +0.042, +0.037, +0.035, and
the final run is the cleanest statistically (both confidence intervals fully
above zero).

The machine itself is essentially fully lit: 28 of 31 data sources feeding
617,062 scored providers across 19 fraud scheme types, including three new
ones this week (drug markup, 340B contract pharmacies, DME referrer
eligibility). We also turned historical ownership filings into a churn signal:
20,353 organizations with owner changes since 2023, an early top-ten honest
feature and a direct lead source for the ownership-flip pattern.

Two decisions for you:

1. About $20 to $40: one weekend rental of a 64GB cloud machine to run the
   full graph embeddings and repeat the same blind test. The 2-feature
   structural family gives +0.035; this tests whether the full field adds more.
2. The OpenSanctions commercial license (the standing item): unlocks their
   consolidated enforcement graph for commercial use.

The paid SSA death file stays deferred; our deactivation signal covers most
of it for free.

Trey

## 5. State of the skip list (nothing else is fixable by effort)

- death_master: paid SSA subscription. Brad decision. Deferred.
- nadac drug-spread (org grain): needs a claims extract with NDC per row.
  Counsel question for the state data source. The J-code markup covers the
  essence meanwhile.
- graph_velocity: unlocks after the 64GB embeddings run plus one later
  snapshot. The clock started this week.

## 6. Standing cadence (2 minutes each, monthly)

- Owner snapshot: python -m src.entity_graph.ownership_snapshot --owned-by
  "%DATA%\graph\edges\owned_by_edges.parquet" --snapshots-dir
  "%DATA%\owner_snapshots"
- Feature snapshot: make feature-snapshot (or the export --snapshot flag).
