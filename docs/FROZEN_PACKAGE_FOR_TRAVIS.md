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

`make frozen-package` now runs the A/B for you and writes `NETWORK_AB_REPORT.md`.
If you want to run it by hand, or understand what it does:

- **A: network columns IN.** Keep the graph family (embeddings, fraud proximity,
  ring density, the two-hops flag, ownership).
- **B: network columns OUT.** Drop that whole family, keep everything else.

If A beats B on the forward label (top-decile lift, PR-AUC), the network features
look like they're earning their place. If A and B are the same, they aren't.

## The size trap (why we run it twice)

There's a catch we learned the hard way. Some network features — "within two hops
of a banned party," "related-party density" — go up just because an organization
is **big and corporate-complex**. A national chain is automatically near some
banned provider and automatically has high related-party density. So "A beats B"
on the whole population could just mean "the graph knows who's big, and big orgs
get excluded more." That's not signal. That's size wearing a costume.

So the report runs the A/B a **second** time on a **size-matched** set: every
excluded provider is placed next to clean providers of the **same size, specialty,
and state** (`case_control.match_cohorts`). On that set, size can't do the work.

Read the verdict this way:
- **KEEP** — network beats no-network even against size-matched peers. Real,
  size-independent signal. Use it (under the out-of-time split).
- **SIZE ARTIFACT** — network wins on the full population but the edge vanishes
  against matched peers. The lift was size. Drop it or size-normalize it.
- **NO MEASURABLE SIGNAL** — network doesn't move the needle either way.

The matched-set number is the one that settles it. This is the test that tells us
whether the jump Travis saw was real or a mirage.

Run by hand:
```
python -m src.model_a.network_ab \
    --matrix  model_a/frozen_2023-12/provider_features_for_model.parquet \
    --manifest model_a/frozen_2023-12/feature_manifest.json \
    --future-label model_a/frozen_2023-12/future_bans_after_2023-12.csv \
    --out NETWORK_AB_REPORT.md
```

## Three things to watch

- Score only the rows that were **not** already banned at the freeze. The
  label file flags those with `was_excluded_pre_cutoff`. Leaving them in inflates
  the result the same way the old file did. A ban dated exactly on the freeze day
  counts as already known (it is in the frozen graph), not as a forward positive.
- The matched A/B inside the report is built on the **forward** label too: cases
  are future-banned providers, controls are matched not-yet-banned peers. Matching
  on the old in-time label would let the proximity flags read the answer back off
  the graph. The report header says which label the matched block used.
- The forward label is thin by design. Bans are rare, and one year of forward bans
  is rarer still. Use top-decile lift and PR-AUC, not plain accuracy, and expect
  wide error bars. Small and real still beats big and leaked.

## If the machine can't build embeddings

The DeepWalk node embeddings need ~64 GB at full graph scale. On a 16 GB machine,
build the frozen graph with `--no-embeddings` (or
`make frozen-package FROZEN_GRAPH_FLAGS=--no-embeddings`). The test stays valid:
it measures the exclusion-proximity flags plus the structural features
(shell score, related-party density), which are fair in a forward test because
they are built only from bans known before the freeze. The embeddings are a
round-2 question: if round 1 shows real forward signal, a one-time run on a
rented 64 GB box adds `graph_emb_*` and re-runs the same A/B to price their
marginal lift.

The label is built from public integrity events. It is a modelling label, not a
claim about any person. Everything downstream is a lead for review, not an
accusation.
