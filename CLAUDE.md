# Project Context: medicaid-fraud-detection — Supervised Model Build

## What this repo does
A pipeline that detects suspicious Medicaid billing and outputs ranked **company-level**
fraud leads. Those leads feed Equitable Claims' advertising (detected fraud-suspect
companies → advertise to potential whistleblowers/insiders at those companies).
`select_advertise_leads.py` writes ad targets to `~/Desktop/linked in ads/`.

## CRITICAL: data lives OUTSIDE the repo
- All real data is under **`~/Desktop/Data/`** — NOT the repo's `data/` folder.
- `preclean/` = raw inputs. Never recompute features; treat feature parquets as inputs.
- Key data subfolders: `features/`, `integrated/`, `detection/tables/`, `Model/`.

## Existing pipeline (already built, do not rewrite)
Lives in `src/attempt_2/`. Three detection layers (see `leads/detect.py`):
- **Layer 1** — hard rules: billed-while-excluded (LEIE), physically-implausible rates.
- **Layer 2** — anomaly scoring on size-normalized rate features vs taxonomy peers.
  Uses robust-z = 1.4826*(x-median)/MAD within peer group, clipped ±50, signal at
  z>=3.5. `anomaly_score` = SUM of clipped z over fired features → unbounded
  **0–215 z-scale** (NOT 0–1). Stored in `detection/tables/fraud_leads.parquet`.
- **Layer 3** — low-confidence ownership track (probable excluded owner), kept separate.

Company rollup produces a separate **0–1** score (`company_anomaly_score`, mean of
peer-percentile concepts) in `Model/company_scores_full.parquet`. The per-NPI z-scale
and the company 0–1 score are DIFFERENT constructs — there is no formula converting
one to the other.

## THE NEW MODEL PLAN (supervised LightGBM, branch: feat/model-scaffold)
Goal: a supervised model at the **NPI level**, rolled up to company LATER.
- Code goes in repo `src/model/`. Model data in `~/Desktop/Data/Model/`.
- **Label:** `provider_on_leie` (provider appears on the LEIE exclusion list).
- **Algorithm:** LightGBM, binary classification.
- **Train at NPI grain**, then aggregate predictions to company afterward.

### PU (Positive-Unlabeled) learning design — "confident-clean negatives"
LEIE positives are reliable, but unlabeled != negative. So:
- Keep **ALL** positives.
- Negatives = only **LOW-anomaly** providers (confident clean).
- **Hold out** high-anomaly and unscored providers (ambiguous — neither pos nor clean-neg).

### NEVER use these as features (label leakage)
- `provider_on_leie` (it IS the label)
- all `facility_*excluded_owner*` columns
- `excluded_owner_role`
- `any_billed_after_exclusion` / `billed_after_exclusion` / `excluded_after_billing`
- any "probable excluded owner" field

### Feature source
`~/Desktop/Data/Model/provider_features.parquet` — 617,062 NPIs × 52 cols.
Includes raw volume/dollars, rate features, and peer-normalized features in two
peer bases: `_tax` (taxonomy) and `_taxstate` (taxonomy×state), each with a robust-z
(`_rz_`) and percentile (`_pct_`) variant. 578 LEIE positives (0.094% base rate).

### Evaluation (NOT accuracy)
PR-AUC, recall of held-out LEIE positives, precision@K. Base rate is ~0.1–0.2%, so
accuracy is meaningless. Report PR curves and ranked precision.

## WHAT WE JUST DID (data prep, already complete & verified)
All done with pandas/pyarrow, originals preserved. New files in `~/Desktop/Data/Model/`:

1. **`provider_features_scored.parquet`** (617,062 × 57)
   = `provider_features.parquet` LEFT JOINed on `npi` with 5 score columns from
   `detection/tables/fraud_leads.parquet`: `anomaly_score`, `n_anomaly_signals`,
   `anomaly_lead`, `not_scored`, `not_scored_reason`. 1:1 join, 0 unmatched.
   (33,977 rows have null anomaly_score — these are the `not_scored=True` providers,
   legitimately unscorable: low volume / no peer group.)

2. **`provider_features_pu.parquet`** (308,038 × 57) <- **USE THIS FOR TRAINING**
   PU-filtered from the scored file:
   - Keep ALL 578 LEIE positives (0 dropped).
   - Keep clean negatives = scored AND `anomaly_score < 0.5` -> 307,460 rows.
   - Dropped: 33,920 not_scored + 275,104 high-anomaly (>=0.5) negatives.
   - NOTE: on this data, no scores fall in (0, 0.5), so the clean negatives all have
     anomaly_score EXACTLY 0 (zero signals fired). Unambiguous clean set.
   - Resulting positive rate: 0.1876% (578 / 308,038).

(For reference, a company-level analogue exists too:
`Model/company_scores_filtered.parquet`, 388,275 rows — confident-clean companies
<=0.7 plus all 578 LEIE-positive companies. Company grain, not for NPI training.)

## Conventions / guardrails
- Backend runs LOCALLY (host Mac), not Docker.
- Do not modify the existing attempt_2 pipeline; build new code in `src/model/`.
- Treat all parquet feature files as immutable inputs; write new outputs, never
  overwrite source files.
- Remember the leakage column blocklist above when assembling the feature matrix.

## Your first task (suggested)
Scaffold `src/model/` on branch `feat/model-scaffold`:
1. A data-loading module that reads `Model/provider_features_pu.parquet`, drops the
   leakage columns, sets `provider_on_leie` as `y`, and splits train/val (stratified).
2. A LightGBM training script with PR-AUC / recall@LEIE / precision@K eval.
3. An inference/scoring script + an NPI->company rollup step.
Confirm the feature list (52 cols minus blocklist minus identifiers like `npi`,
`org_legal_name`, `first_month`, `last_month`) before training.
