# The frozen package: how to test the network features the fair way

This is the protocol for the A/B test we talked about. The goal is to find out
whether the network (entity-graph) features actually predict fraud, or whether
they only looked strong because of leakage.

## Why the current file can't answer this

On the file you have now, the network features and the billing features were
built from the whole history, including 2024 through 2026. Some of the people in
that history were already banned. So when a network feature "knows" a provider
sits two hops from a banned party, part of what it knows is the future. If you
test on that file, you are partly measuring leakage, not signal. The score will
look good for the wrong reason.

## The fix: freeze the features in the past, label from the future

We pick a cutoff date. Everything the model sees is frozen as of that date. The
label is what happened after. If a provider looked clean at the cutoff and got
banned later, that is a real forward prediction, and the network features either
saw it coming or they didn't.

We use a cutoff of **2023-12** by default. Features are built only from data known
before then. The label is anyone whose first ban lands in 2024 or later.

## Three steps (one command)

From the data root, with `MEDICAID_DATA_ROOT` set:

```
make frozen-package ASOF_CUTOFF=2023-12
```

That runs three things in order:

1. **Point-in-time graph.** The graph is rebuilt using only the exclusions and
   ownership links known before the cutoff. No future ban leaks into the
   embeddings, the fraud-proximity field, or the rings.
2. **As-of feature matrix.** The billing fact is filtered to service months before
   the cutoff, then the whole per-NPI matrix is built on top of it. Every billing
   feature is now as-of-correct too. The network columns are kept in on purpose,
   because they are the thing we are testing.
3. **Forward label.** We pull every NPI whose first ban is on or after the cutoff.
   That file is `future_bans_after_2023-12.csv`. Providers already banned before
   the cutoff are marked separately so you can drop them from the test. They were
   already known-bad, so they are not a fair forward case.

Output lands in `model_a/frozen_2023-12/`:

- `provider_features_for_model.parquet` — the frozen features (network columns in)
- `future_bans_after_2023-12.csv` — the forward label to score against
- `feature_manifest.json` — the same column contract as before

## How to run the A/B

Train and score on the frozen matrix. Use the forward file as the label. Then
run it twice:

- **A: network columns IN.** Keep the graph family (embeddings, fraud proximity,
  ring density, the two-hops flag, ownership).
- **B: network columns OUT.** Drop that whole family, keep everything else.

If A beats B on the forward label (top-decile lift, PR-AUC), the network features
are earning their place. If A and B are the same, they aren't, and we stop
leaning on them. Either answer is useful. This is the test that tells us which.

## Two things to watch

- Score only the rows that were **not** already banned before the cutoff. The
  label file flags those with `was_excluded_pre_cutoff`. Leaving them in inflates
  the result the same way the old file did.
- The forward label is thin by design. Bans are rare, and one year of forward bans
  is rarer still. Use top-decile lift and PR-AUC, not plain accuracy, and expect
  wide error bars. Small and real still beats big and leaked.

The label is built from public integrity events. It is a modelling label, not a
claim about any person. Everything downstream is a lead for review, not an
accusation.
