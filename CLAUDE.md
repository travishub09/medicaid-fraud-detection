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

## MODEL SCAFFOLD: DONE (2026-06-11, MERGED TO MAIN — `src/model/` is live)
`src/model/` is built and ran end-to-end on real data:
- `config.py` — paths, label, leakage blocklist + **detector-score exclusions**
  (`anomaly_score`/`n_anomaly_signals`/`anomaly_lead`/`not_scored`/`not_scored_reason`
  are NOT features: PU negatives were selected on `anomaly_score == 0`, so those
  columns encode the sampling design, not provider behavior).
- `data.py` — loads the PU frame (asserts 308,038 rows / 578 positives / all
  negatives anomaly_score==0), builds the shared leakage-free matrix
  (**42 features**: 57 cols − 4 identifiers − 6 leakage − 5 detector), stratified
  80/20 split (seed 42). Train and inference both go through `build_feature_matrix()`.
- `train.py` — LightGBM binary, **heavy regularization is load-bearing** (only 462
  train positives): `num_leaves=15, min_data_in_leaf=100, lambda_l2=10,
  feature_fraction=0.6, lr=0.03`, early stopping on val average_precision.
  Selected by 3-seed comparison (loose params → PR-AUC ~0.01; these → 0.35–0.47).
  **Held-out val: PR-AUC 0.465 (base rate 0.19%), ROC-AUC 0.931, P@100 0.53,
  recall@1000 63%, top-decile lift 7.9x.** Artifacts + `MODEL_REPORT.md` →
  `~/Desktop/Data/Model/artifacts/` (booster `lgbm_leie.txt`, `feature_list.json`
  incl. categorical levels, `metrics.json`, PR curve, val predictions, importances).
- `score.py` — scores all 617,062 NPIs (`provider_features_scored.parquet`),
  reapplies training categorical levels, then company rollup via
  `detection/tables/npi_to_company_map.parquet` (non-fan-out asserted).
  **`score_reliable = NOT not_scored` gate is essential**: the 33,920 unscoreable
  providers get pure missing-value extrapolation (median score 0.9996 at median
  $0 net_paid) — flagged, never ranked; company scores aggregate reliable
  constituents only. Outputs → `~/Desktop/Data/Model/scores/`
  (`provider_model_scores.parquet` w/ segment + rank_reliable,
  `company_model_scores.parquet`, top-500 CSV, `MODEL_SCORING_REPORT.md`).
- Segment separation (the PU design working): LEIE positives mean score 0.875,
  clean negatives 0.0001, held-out high-anomaly spread between (mean 0.225) —
  reliable top-1,000 = 989 high-anomaly candidates + 11 LEIE. NOTE: universe
  recall@K of LEIE is LOW and that is expected (unlabeled ≠ negative; the top
  ranks are the not-yet-caught candidates) — judge the model on held-out val.
- `lightgbm>=4.3` added to requirements.txt (installed locally for python3.13).

## LEAD LIST EXPORT (2026-06-11, `src/model/export_leads.py`, on main)
Agreed selection: **top 5,000 companies by `company_model_score_max` with
>= $10M consolidated billing** (size-defined list, NOT a score threshold — the
model's scores are uncalibrated and a "0.7" here is NOT comparable to the
unsupervised 0.70 bar). Output: `~/Desktop/Data/Model/model_leads_top5000_over10m.csv`
($354.5B billing; 16,751 companies were eligible; scores in-list run 1.0 down
to 0.008 — only ~2,687 exceed 0.7, so the tail is low-confidence padding).
Null rollup names (single-NPI companies) resolved from the best constituent
NPI's org_legal_name. Top leads: behavioral-health/treatment orgs.

## FP SCREENING APPLIED (2026-06-11, `src/model/screen_leads.py`, on main)
The validated build_final_leads screens (imported verbatim from attempt_2) ran
on the model lead list; company specialty = dominant (highest-billing)
constituent NPI's taxonomy. **Removed 967 of 5,000** (hospital_taxonomy 276,
hospital_name 196, government 192, fqhc 149, public_academic 63,
national_nonprofit 50, tribal 41) → **`model_leads_top5000_over10m_screened.csv`
(4,033 leads, $236.6B)** + `..._removed_audit.csv` (quarantine, never delete).
Notes: SOUTHCENTRAL FOUNDATION survives (no keyword matches — same behavior as
the unsupervised FinalLeads screen); 66 leads are individual NPIs with no org
name → "UNKNOWN NAME (NPI x)" (export_leads now treats blank org_legal_name as
missing; person-name resolution from NPPES is a possible follow-up).

**FINAL CUT (2026-06-11): score >= 0.90.** First cut was 0.70 (2,168 screened
leads), then tightened to 0.90 after held-out validation showed the bands
differ sharply: val precision 0.897 for score>0.9 (35/39) vs 0.40 for 0.7–0.9
(4/10, small n); all 9 known-LEIE companies in the list sat above 0.9.
`export_leads --min-score 0.9` → screen_leads →
**`~/Desktop/Data/Model/output/final/model_leads_score090_over10m_screened.csv`
= 1,816 leads, $110.7B** (+ removed_audit, 415 institutional FPs). This is THE
model handoff list. The 0.7–0.9 band (352 companies, $24.9B) is kept in the
score070 files as a second-tier reserve. NOTE: Travis reorganized the Model dir
— handoff CSVs live in `Model/output/final/`, superseded ones in
`Model/output/unimportant/`; scores/artifacts unchanged. Scores are saturated
near 1.0 (uncalibrated sigmoid; ranking valid — 2,000 distinct values in top
2,000; a raw-margin column is a pending nice-to-have).

**GitHub branch protection (2026-06-11):** main now has "changes via PR" +
locked-branch rules; pushes as travishub09 succeed via admin bypass. Consider
PR workflow for future substantive changes.

## Next steps (not started)
1. Calibration / threshold pick for the ad-targeting handoff (company grain).
2. Compare model ranking vs unsupervised `company_anomaly_score` (agreement,
   uniques each finds); consider LEIE-timing backtest like src/backtest.
3. Per-lead explainability: SHAP contributions (`pred_contrib=True`) so each
   lead carries its driving features, like the rest of the pipeline.
4. NPPES person-name resolution for the 66 individual-NPI leads.
