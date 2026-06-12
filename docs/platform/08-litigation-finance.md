# 08 — Litigation-Finance Unit Economics

Model C's output is only useful if it plugs into a fund. This doc sketches the
economics; the modeling lives in [06-model-c.md](06-model-c.md).

## The single-case expectation
```
Expected relator gross = P(recover) × E[recovery | recover] × relator_share% × time_discount
```
- `P(recover)` is dominated by `P(intervene)` (~$2.2B of $2.4B FY2024 qui tam
  recoveries came from intervened/pursued cases).
- `relator_share%` is 15–25% (intervened) or 25–30% (declined-pursued).
- `time_discount` reflects multi-year seal/investigation timelines.
- The financier's take is a negotiated percentage of the relator gross, priced to the
  portfolio return target — subject to the **funder-control** guardrail (no control over
  litigation or settlement decisions).

## Why a portfolio, not a case
Recoveries are heavy-tailed: a few **whales** carry the book. A model well-calibrated
per case can still build a bad fund if it never catches a whale. So fund construction
is a **portfolio Monte Carlo** over per-case recovery distributions (the ~20-case
book), reporting the MOIC distribution, whale probability, and capital-at-risk
(`src/model_c/portfolio.py`, scaffold).

## How Model C sets terms
Per case, Model C emits P(intervene), a P10/P50/P90 recovery distribution, expected
relator gross, and a recommendation:
- **Fund** — expected value and whale-likelihood clear the bar.
- **Pass** — first-to-file risk, weak corroboration, hostile venue, or thin damages.
- **Fund-with-terms** — fund at a take percentage and capital level priced to hit the
  portfolio target given the case's risk.

## Selection-bias discipline (carries into the fund's returns)
Because outcomes are observed only for financed cases, set aside a small **exploration
tranche** of marginal cases to gather counterfactual labels, and track declined cases'
public outcomes. Validate fund/pass decisions at the portfolio level against a
historical case set (would they have hit the target MOIC?), not just per-case
calibration. See [06-model-c.md](06-model-c.md) §2.7–2.8.

## Build dependency
This is Phase 6. It depends on the structured DOJ/OIG/PACER outcome database (a Phase-1
data gap) and on accumulated platform outcomes that turn the cold-start rules into a
trained model.
