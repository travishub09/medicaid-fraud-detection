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
- **Scaffold:** `src/model_c/features.py`, `underwriting.py`, `portfolio.py`.
- **Blocked on:** the structured DOJ/OIG/PACER case-outcome database (a Phase-1 data
  gap) and accumulated platform outcomes. Cold-start rules can ship before labels exist.
