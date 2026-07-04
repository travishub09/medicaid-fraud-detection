# Model Retrospective — how to find what actually failed

_A disciplined post-mortem on the first run. The goal is not to confirm "it failed" — parts
worked. The goal is to prove, one axis at a time, which parts are real, which are unproven, and
which underperformed, and to isolate the single biggest cause._

## First: separate three verdicts (don't lump them as "failed")
- **WORKED, keep:** the open-and-shut billed-after-ban cases; network / ownership lift (top 1%
  at 7.8x); market saturation (top 1% at 10.3x). Real and reproducible from the file.
- **UNPROVEN, the headline 0.875:** the 0.615 to 0.875 jump is not yet trustworthy (circular
  negatives + leakage + no out-of-time split are all live suspects). Not a failure, an
  unverified number.
- **UNDERPERFORMED, fix:** the physician-scheme subscores (lossy — see section K), the org-heavy
  exclusion label versus the qui-tam goal, and the small model that could not capture clustering.

A good retrospective produces evidence for each bucket. It does not assume the whole thing failed.

## The method: three passes, in order
1. **Reproduce.** Nail the exact numbers (0.615 / 0.875 / the anomaly-removal delta) before
   explaining them. If we cannot reproduce them, that is the first finding.
2. **Ablate.** Change ONE thing at a time and measure the delta. The deltas, not opinions, tell
   you what mattered.
3. **Error-analyze.** Read the actual misses. Pull the known-bad providers the model ranked LOW
   (false negatives) and the clean-looking providers it ranked HIGH (false positives), and
   categorize *why* each one missed. This is where the real story usually is.

## The five failure axes
Each: the hypothesis, the evidence we already have, the diagnostic to run, the fix.

### 1. Labels — the #1 suspect
- **Hypothesis A (circular negatives).** Travis's README defines negatives as providers with
  "zero anomaly signals." If so, the model is rewarded for reconstructing the anomaly score, not
  for finding fraud, and the jump is partly an artifact.
  - _Evidence:_ his own README; and removing the anomaly score moved the number (the A/B question).
  - _Diagnostic (THE crux test):_ retrain twice, identical except the negatives — once with his
    "zero-signal" negatives, once with **fair random negatives** (`settle_it.py` already does
    this). If 0.875 collapses toward 0.615 under random negatives, the lift was circular.
- **Hypothesis B (wrong label for the goal).** LEIE exclusion is not the same as a $5M qui-tam
  case. 64% of the excluded are organizations; the physician signals reach only 3-8% of them. The
  model may be learning "who gets excluded" (often small operators) instead of "who is worth
  financing."
  - _Diagnostic:_ label audit by entity type; per-scheme coverage among positives vs population
    (already in `FINDINGS_LOG`); retrain against **DOJ-case labels** (prosecuted dollars, via the
    built `model_a/case_labels.py`) as an alternative target and compare who rises.
  - _Fix:_ the widened multi-source label + weak-supervision + `confirmed_clean` anchors (all
    built); DOJ-case labels for a qui-tam-shaped target.

### 2. Evaluation — is 0.875 even real?
- **Hypothesis:** leakage + no out-of-time split + group leakage inflate it.
  - _Evidence:_ `subscore_ownership_integrity` has AUC **0.991** — that is a screaming leakage
    tell (it is exclusion-derived); the leakage_hard / leakage_adjacent columns exist; the run
    matched the *present*, it did not predict the *future*.
  - _Diagnostics:_ (a) leakage audit — confirm no leakage_hard/adjacent column reached the
    trained features (`verify_travis.py`); (b) **group-aware CV** on `group_id` so related NPIs
    (same org/owner) do not straddle the split; (c) the **out-of-time 2024-26 future-ban recall
    test** — the un-gameable number; (d) report **PU-corrected lift** (`pu_prior.py`), not raw AUC.
  - _Fix:_ strict out-of-time split, group CV, drop both leakage tiers, report PU-corrected lift +
    FDR + conformal intervals (all built).

### 3. Features / representation
- **Hypothesis:** lossy subscores + a coverage artifact + the missing network embeddings.
  - _Evidence:_ section K (subscores flatten their raw signal at the top decile); the coverage
    artifact (Open Payments covers ~6% of the *excluded*, so it cannot help the Medicaid label
    even though it is real); embeddings OOM'd, so the strongest representation never trained.
  - _Diagnostic:_ per-feature AUC x coverage (`signal_ranking.csv`); the subscore-vs-raw audit
    (section K); **coverage-stratified AUC** — score each signal only on the subpopulation it
    actually covers, so thin coverage is not mistaken for weak signal.
  - _Fix:_ train on raw + `__peerpct` (section K); embeddings on the 64 GB box; coverage-aware
    weighting.

### 4. Model capacity
- **Hypothesis:** the small LightGBM could not capture the signal-clustering (Travis said as much).
  - _Diagnostic:_ a capacity sweep (depth / num_leaves / min_child); add the explicit
    signal-count feature and re-measure; check whether interactions are being found at all.
  - _Fix:_ the signal-count feature, a deeper model, engineered interaction features.

### 5. Target / design
- **Hypothesis:** the run ranked on size / intensity and dropped the big complex cases, and the
  training objective (PR-AUC on a rare exclusion label) is not the business goal (find $5M qui-tam
  cases).
  - _Diagnostic:_ decompose the top-ranked providers vs the actually-valuable cases; look at the
    dollar distribution of the top leads (are they all tiny?).
  - _Fix:_ size-as-filter (section A) + the Model C / ERV recovery gate.

## The single experiment to run first
The **fair-negatives ablation** (axis 1A), paired with the **out-of-time test** (axis 2). Between
them they answer the whole question:
- If 0.875 holds under random negatives AND on an out-of-time split → the model works, and
  everything else is tuning.
- If it collapses under either → the headline number was an artifact of how negatives were chosen
  or of leakage, and *that* is the real "why it failed."
Run this before touching features or hyperparameters. Everything else is secondary to it.

## Build: one retrospective harness
Most of the diagnostics already exist (`pu_prior`, `verify_travis`, `settle_it`, calibration
reliability, `fdr`, `conformal`, `case_control` covariate balance). The missing piece is an
orchestrator. Proposed `model_a/retrospective.py` that, given Travis's train file, emits a single
`RETRO_REPORT.md`:
1. **Reproduce** the reported numbers.
2. **Ablation grid:** negatives {zero-signal, random} x leakage {in, out} x features {raw+peerpct,
   +subscores, +anomaly composite} x split {random, out-of-time}. One row per cell, with the delta.
3. **Error analysis:** the top false-negatives and false-positives with their named drivers.
4. **Verdict per axis** (worked / unproven / underperformed), with the number behind each.

One command, one report, and the retrospective stops being a debate and becomes a table.
