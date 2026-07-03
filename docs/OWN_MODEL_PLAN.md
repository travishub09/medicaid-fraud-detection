# Plan — Run Our Own Model (independent, apples-to-apples with Travis's)
_A plan, not a build. Goal: we can train and, more importantly, **honestly evaluate** a
model on our own data, so we control the validation and don't have to take anyone's
numbers on faith. Same model family as Travis's so it's a fair comparison._

## Why do this
Not because ours will magically beat his. It's about **control and trust**: right now the
verdict on our data swings depending on how someone else set up their test (which negatives,
which split). If we run it, we set the rules, we run the fair tests his setup couldn't, and
we can settle the value question ourselves.

## What "graph-based tree, apples-to-apples" means (setting expectations)
Travis's model is **LightGBM** — a gradient-boosted decision tree — that uses graph-derived
columns (who's connected to whom) as inputs. It is *not* a neural network. So "matching it"
means the same thing: **LightGBM, one row per provider, with the entity-graph features
included as columns.** Nothing exotic. The tree does the work; the graph shows up as features.

## What we already have (so this is smaller than it sounds)
- **The feature matrix** — `provider_features_for_model.parquet`, 617K providers, ready.
- **The manifest** — already labels which columns are the target, which are leakage (drop),
  and the group key for splitting.
- **A model scaffold already in the repo** — `src/model/` and `src/model_a/supervised.py`
  are a LightGBM PU scorer. We adapt that rather than start from zero.
- **Compute reality:** training LightGBM on 617K rows is **fast and fits a laptop.** The
  model was never the memory problem. The one heavy step is generating the graph embeddings
  (that's the part that needs a bigger machine).

## The model + the honest evaluation protocol (this is the whole point)
The modeling is routine. The evaluation is where we fix what was murky:

1. **Label** — providers on fraud-relevant federal + state exclusion lists, time-stamped by
   the year they were excluded (so we can split by time). Match Travis's label definition so
   it's truly apples-to-apples.
2. **Negatives — the fix.** Draw negatives as a **representative random sample of unlabeled
   providers** (or a proper positive-unlabeled setup), **not** the "zero-anomaly" cherry-pick.
   This is the single change that removes the circular, too-easy comparison.
3. **Drop the leakage columns** exactly per the manifest; **leave blanks as blanks** (no
   fill-with-zero).
4. **Split two ways, both honest:**
   - **Group-aware** — same owner/company stays on one side, so it can't memorize the family.
   - **Out-of-time** — train on providers banned through a cutoff year, test on those banned
     after. This is the "does it predict the future" test.
5. **Metrics that matter** — precision-recall (PR-AUC), top-1%/top-decile lift, and the one
   that can't be gamed: **how many of the later-banned providers land in our top-ranked
   list.** Plus a calibration step so scores are usable probabilities for the $5M
   underwriting filter downstream.

## The graph-based part (two phases)
- **Phase A (now, on a laptop):** train and evaluate on the current feature set. Gets us an
  independent model and the honest numbers immediately. Uses the graph *structure* features
  we already have (co-location, ownership, address counts).
- **Phase B (after the bigger-box run):** add the full graph **embeddings + motifs** (the
  ones we shipped without). That's the true "graph-based" version and matches his ceiling.
  Only Phase B needs cloud compute, and only for generating the embeddings.

## The four comparisons that actually settle things
Run each as with-vs-without, so we quantify every claim:
1. **Fair negatives vs cherry-picked** — reproduces Travis's test honestly. Tells us if the
   strong numbers were real or a setup artifact.
2. **With vs without our features** — the real marginal value of our data over a plain
   billing model.
3. **With vs without the graph features** — what the embeddings actually buy.
4. **With vs without ordering/referring** (once Run 2 adds it) — the value of the net-new
   data that was never tested.

## Who runs it, and the honest effort
This is a scripted pipeline, not a research project. Rough shape: a few days to wire the
clean evaluation harness on top of the existing repo model code, then it's a repeatable
command. Two realistic options for *who*:
- **We/you script it** off the existing `src/model` code (cheapest, keeps it in-house).
- **A second modeler or contractor** runs it independently (gives you a truly separate
  read from Travis's).
Either way, you own the harness and the rules.

## What this gets you — and what it doesn't
- **Gets you:** control of the evaluation, the ability to run the fair tests, an independent
  number you can trust, and a model you can iterate without waiting on anyone.
- **Doesn't get you:** a guaranteed better model. The honest eval will probably confirm that
  billing intensity dominates, same as his. The win is *trustworthy* numbers and *your* hands
  on the wheel, not a higher score.

## Deliverables when built
- A repeatable training + evaluation script (one command).
- The honest metric report (PR-AUC, lift, future-ban recall) under fair negatives + both
  splits.
- The four with-vs-without comparisons.
- A calibrated per-provider score + the ranked, $-filtered lead list.

## How this fits the other plans
- **RUN_2_PLAN** improves the **data** (ordering/referring, embeddings, Medicare).
- **This plan** puts the **modeling and evaluation** in our hands.
They're complementary: Run 2 makes the inputs better; this makes the scoring ours.
