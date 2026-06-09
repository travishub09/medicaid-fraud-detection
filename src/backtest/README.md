# `src/backtest` — LEIE exclusion-list backtest of the Layer-2 anomaly score

Validates the **Layer-2 company anomaly score** against the **OIG LEIE** exclusion list as
held-out ground truth: does the (LEIE-independent) score concentrate fraud-relevant excluded
providers more than the universe at large, and does it **beat a billing-size-only baseline**?

## Headline finding

> The size-neutral Layer-2 anomaly score gives a **2.0× top-decile lift** on fraud-relevant
> LEIE exclusions vs a **0.9× billing-only baseline** — and the lift **survives within every
> billing quartile** (1.6×–2.4×), so it is real signal, not a size artifact. Permutation
> **p = 0.001**; bootstrap 95% CI on top-decile lift **[1.6×, 2.4×]**. Base rate 0.080%.

The score also carries through to the score≥0.70 anomaly leads (31,269 companies, 2.2× lift)
and to the quasi-prospective **after-2024** exclusion subset (2.3×, same direction).

## Why we backtest the SCORE, not the anomaly tier

The anomaly **tier** in the shipped pipeline is **LEIE-disjoint by design**: any company with an
excluded NPI routes to the higher-priority on-LEIE tier, so the anomaly-tier leads contain **zero**
LEIE-NPI companies (verified 0/1985). That is *correct* behavior — the anomaly tier exists to
surface fraud-shaped billing among providers **not yet caught**. So we backtest the underlying
score across the **whole universe** (on-LEIE companies included), not the tier-filtered leads.

## Run

```
python -m src.backtest.score_universe   # re-score ALL companies (exact company_lead_tracker score)
python -m src.backtest.backtest_leie    # label vs LEIE, lift, size baseline, timing, null tests
```

## Inputs (read-only)
- `~/Desktop/data/preclean/Caught.csv` — OIG LEIE (label source)
- `~/Desktop/data/detection/tables/company_rollup.parquet`, `features/`, spending base — for scoring

## Outputs (in this folder)
- `company_scores_full.parquet` — exact company-grain score for all ~419k scored companies
  (regenerable intermediate; gitignored, not committed)
- `backtest_results.csv` — one row per anomaly lead (score ≥ 0.70): company, score, hit,
  matched_npi, exclusion_type/date, match_confidence, timing_bucket, score_decile, size_band
- `backtest_report.json` — prose narrative (methodology, label definition, results, size-baseline
  finding, timing finding, disjointness finding, limitations, conclusion) + a `metrics` object

## Caveats (see `limitations` in the JSON)
Precision/lift, **not recall**; score is **in-sample** (optimistic); ground truth is **caught**
fraud; only **~10%** of LEIE rows carry an NPI so positives are undercounted; name+state matches
are noisier than NPI matches.
