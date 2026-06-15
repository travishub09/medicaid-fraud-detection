# 06 — Model C: Case-Selection and Intervention-Likelihood (Underwriting)

The underwriting brain. For a relator and claim already in intake, Model C predicts
whether the government will intervene, the recovery distribution, and the financing
terms.

## Why intervention is the target
~$2.2B of the $2.4B in FY2024 qui tam recoveries came from cases the government
intervened in or pursued. Intervention is very nearly the difference between a
recovery and a zero, so **P(intervene) is the primary prediction** and recovery
magnitude is second.

## Targets
```
y1 = outcome class  ∈ { intervened, declined-pursued, dismissed/zero }
y2 = recovery amount | recovery     # heavy-tailed → model in log space, quantile regression
Expected relator gross = P(recover) × E[recovery | recover] × relator_share% × time_discount
```

## Features

| Group | Features |
|---|---|
| Subject matter | scheme type weighted by current DOJ priority; match to active enforcement initiatives |
| Defendant | size, solvency, public/private, repeat-offender (prior CIAs/settlements raise intervention odds), entity vs individual |
| Evidence strength | documentary support, specificity, firsthand vs hearsay, corroborating-witness count (coded from intake) |
| Independent corroboration | whether Model A's public-data signal supports the allegation — the unique, defensible input |
| Relator | credibility, knowledge tier, culpability (high lowers value), first-to-file clearance, original-source strength, public-disclosure exposure |
| Jurisdiction | district/USAO historical intervention rate and healthcare-fraud activity; venue/circuit (Zafirov exposure) |
| Magnitude | estimated single damages → trebled; claim volume driving penalties |
| Timing | statute-of-limitations runway |

## Labels and training data
Assemble case-level outcomes from unsealed PACER dockets (intervened/declined +
amounts), settlement databases, and DOJ press releases, with DOJ annual statistics as
aggregate priors. **Cold-start on rules** — DOJ priority schemes, jurisdiction
intervention rates, scheme base rates — until enough structured outcomes exist.

**Data hazard:** the public record is survivorship-biased toward successes; declined
cases that quietly died are under-observed. Model the full filed-to-outcome funnel
where you can, not only the wins.

## Modeling
A calibrated gradient-boosted classifier for the outcome class; quantile regression
(or log-normal/GBM quantile) for the recovery distribution. Combine into expected
value + a financing recommendation; run a portfolio-level Monte Carlo for fund
construction (the ~20-case book). SHAP decomposition per case becomes the spine of the
investment memo.

## Output and the underwriting decision
Per case: P(intervene), a recovery distribution (P10/P50/P90), expected relator gross,
and a recommendation — **fund / pass / fund-with-terms** — with capital to deploy and
a take percentage priced to the portfolio return target. The model also surfaces
**whale-likelihood** cases, which is what actually carries the fund.

## The selection-bias trap (design for it explicitly)
You only observe outcomes for cases you finance, so the model never learns about the
cases it declined — the classic reject-inference problem; naive retraining entrenches
early biases. Mitigations: finance a small **exploration tranche** of marginal cases to
gather counterfactual labels; track declined cases' eventual public outcomes; feed the
broad funnel (intake → filed → intervened) into training, not just the financed subset.

## Validation
Temporal holdout on historical resolved cases: would the model have predicted the
intervention decisions and recovery sizes that occurred? Check calibration of
P(intervene) and whether the predicted recovery distribution matches realized
outcomes. Then validate at the **portfolio level** — would the fund/pass decisions
have produced the target MOIC on a historical case set — because a per-case
well-calibrated model can still build a bad book if it never catches a whale.

## Current state in this repo
- **Built — public-disclosure screen:** `src/model_c/public_disclosure.py` — the
  §3730(e)(4) screen (A3). Matches every org's name + aliases against the DOJ/OIG
  case DB and CourtListener docket pulls via `norm_org_name`; emits a flag with
  NAMED citations plus a record of which sources were checked. Wired into the
  Model A run (`--case-db` / `--dockets`) and every dossier. A flag routes to
  counsel; no flag is a screen result, never clearance.
- **Built — cold-start underwriting (rules-based, label-free, explainable):**
  - `priors.py` — the curated rule tables (scheme-priority multipliers,
    jurisdiction intervention tendencies incl. the Zafirov FL-venue discount,
    statutory relator shares, magnitude/discount assumptions). One auditable
    `UnderwritingAssumptions` dataclass; every constant RETIRES once the case DB
    exists, the same way Model A's sector priors do.
  - `features.py` — `build_case_features` assembles a per-case row from the
    Model A ERV signal plus optional relator intake; with no intake the row is a
    PRE-RELATOR pre-screen (the manifesto's org-level Case Viability Score),
    `has_relator = 0`, neutral relator priors — the underwriter never pretends a
    witness exists.
  - `underwriting.py` — `predict_intervention` (base rate × named bounded
    multipliers → P(intervene) + the outcome-class split; first-to-file-not-
    cleared floors it, public-disclosure penalizes it), `recovery_distribution`
    (damages proxy → realized settlement P10/P50/P90, low data-confidence widens
    the band but never shifts the median), and `underwrite` (expected relator
    gross → **fund / pass / fund-with-terms** with capital and a take % priced to
    the portfolio MOIC target; hard gates: first-to-file, public-disclosure, EV
    floor, take cap). Every row carries its drivers — the investment-memo spine.
  - `portfolio.py` — `monte_carlo_portfolio` simulates the funded book
    (Bernoulli recovery × log-normal magnitude) → MOIC P10/P50/P90, P(loss),
    P(≥3×), and whale probability, because a per-case-calibrated model can still
    build a bad book if it never catches a whale.
  - Orchestrator: `python -m src.model_c --fixture` (builds graph + Model A
    first) or `--erv <erv_ranked.parquet> [--intake] [--case-db]`; writes
    `case_underwriting.parquet`, per-case memos, and `MODEL_C_REPORT.md`.
- **Graduates to** a calibrated GBM (outcome class) + quantile recovery model the
  moment the structured DOJ/OIG/PACER outcome database accumulates; the portfolio
  Monte Carlo and the decision/terms layer run unchanged on the trained
  distributions. Mind the selection-bias trap (§above) when that data arrives.
