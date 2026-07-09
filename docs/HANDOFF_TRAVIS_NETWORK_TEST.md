# For Travis — the frozen network test

This answers the open question from our last round: was the jump you saw
(~0.615 → ~0.875 when my data was added) real signal, or leakage? Here's a
leakage-correct package to settle it, plus a size check we added after finding
that some graph features were tracking organization size, not fraud.

## What's attached

All files come from one folder, `model_a/frozen_2023-12/`:

- **`provider_features_for_model.parquet`** — the training matrix, but frozen as of
  2023-12. Every feature (billing and graph) is built only from data known before
  then, so nothing from the future leaks in. The network/graph columns are kept in
  on purpose — they're what we're testing.
- **`future_bans_after_2023-12.csv`** — the label. A provider is a positive only if
  its first exclusion lands in 2024 or later. So it looked clean at the freeze and
  was banned afterward. Providers already excluded before the freeze are flagged
  `was_excluded_pre_cutoff` — drop those; they're not a fair forward case.
- **`NETWORK_AB_REPORT.md`** — the result of the A/B we ran for you (details below).
- **`RESULTS_DIGEST.md`** — a one-page plain-English summary of the whole run.
- **`feature_manifest.json`** — the column contract (which columns are trainable,
  which are leakage, the feature families).

## The test (already run; here's how to read it, or rerun)

We train with vs. without the graph family (embeddings, fraud proximity, ring
density, the two-hops flag, ownership), and score the forward label. We run it
**twice**:

1. **Full population** — the headline comparison.
2. **Size-matched** — each future-banned provider placed next to clean providers of
   the *same size, specialty, and state*. This is the one that matters, because
   some graph features go up just because an org is big and corporate-complex. On a
   size-matched set, size can't do the work, so a win here is real.

Read the verdict in `NETWORK_AB_REPORT.md`:

- **KEEP** — graph family beats no-graph even against size-matched peers → real,
  size-independent signal. Your jump was earned. Use the graph features under an
  out-of-time split (they're leakage-adjacent, so never on a same-time split).
- **SIZE ARTIFACT** — graph wins on the full population but the edge vanishes when
  matched on size → the lift was size in disguise. Drop the family or size-normalize.
- **NO MEASURABLE SIGNAL** — graph doesn't move the metrics beyond noise.

To rerun by hand (it's already in the report, this is just for transparency):
```
python -m src.model_a.network_ab \
    --matrix   model_a/frozen_2023-12/provider_features_for_model.parquet \
    --manifest model_a/frozen_2023-12/feature_manifest.json \
    --future-label model_a/frozen_2023-12/future_bans_after_2023-12.csv \
    --out NETWORK_AB_REPORT.md
```

## Two cautions

- Score only rows where `was_excluded_pre_cutoff = 0`. Leaving the already-banned in
  inflates the result the same way the old file did.
- The forward label is thin (bans are rare, one year of forward bans rarer still).
  Use top-decile lift and PR-AUC, not accuracy, and expect wide error bars. Small
  and real beats big and leaked.

The label is built from public integrity events. It's a modeling label, not a claim
about any person; everything downstream is a lead for review, not an accusation.
