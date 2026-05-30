# Medicaid Fraud Detection

A data pipeline that surfaces high-dollar Medicaid providers whose billing patterns look
unusual compared to their peers, and hands investigators a ranked shortlist to review.

## What this is (and what it isn't)

Medicaid fraud costs the U.S. healthcare system tens of billions of dollars a year, but
investigators can only look at so many providers. This tool reads national Medicaid
provider-spending data and scores each provider on how far its billing behavior strays from
others in the same specialty.

It is **anomaly detection, not an accusation.** A high score means "this provider bills
differently from its peers and is worth a closer look" — not "this provider committed fraud."
The output is a prioritized worklist for human reviewers. Nothing here decides a case on its own.

## How it works — three stages

The pipeline runs as three scripts, each feeding the next.

### Stage 1 — Clean and filter the data (`clean_data.py`)
Pulls together three public data sources:
- **Medicaid provider spending** — how much each provider was paid, for which procedures, by month.
- **NPPES** — the national provider registry (names, specialty, state, when the provider's ID was issued or deactivated).
- **OIG LEIE** — the official list of providers excluded from federal healthcare programs (our "known bad" reference).

It then applies one important filter: **only providers paid more than $10 million in total
Medicaid dollars are kept.** This keeps the analysis focused on cases where real money is at
stake, and makes the peer comparisons fair — big billers are compared against other big billers,
not against a tiny clinic. (The $10M cutoff is adjustable; see *The dollar threshold* below.)

### Stage 2 — Build the "fraud signals" (`build_features.py`)
For every provider, for every year, the pipeline computes **15 signals** that describe *how*
the provider bills — not *how much*. Raw dollar totals are deliberately left out of the scoring,
so a large, legitimate hospital isn't flagged just for being large. Instead the signals capture
things like "charges far more per claim than peers" or "billing spiked suddenly then collapsed."
The 15 signals are explained in the next section.

### Stage 3 — Score, explain, and rank (`analyze_anomalies.py`)
An **Isolation Forest** model learns what "normal" billing looks like across all providers, then
gives each provider-year an **anomaly score from 0 (normal) to 1 (highly unusual).** For every
flagged provider, a technique called **DIFFI** identifies *which signals* drove the score, so the
output explains itself in plain language (e.g. "pays far more per claim than peers; billed after
the provider ID was deactivated"). Results are ranked, rolled up to one row per provider for easy
triage, and checked against the known OIG exclusion list to measure how well the model is working.

## The 15 fraud signals, in plain English

**Billing-level — how this provider bills in absolute terms**

| Signal | What it means |
|--------|----------------|
| `avg_claims_per_beneficiary` | How many claims the provider files per patient |
| `avg_paid_per_claim` | Average dollars paid per claim |
| `hcpcs_concentration` | How concentrated billing is in just a few procedure codes |
| `billing_on_deactivated_npi` | Whether the provider billed *after* its ID was deactivated (a red flag) |
| `npi_age_days_at_first_claim` | How new the provider's ID was when billing began (brand-new IDs that immediately bill big are unusual) |

**Versus peers — how this provider compares to others in the same specialty**

| Signal | What it means |
|--------|----------------|
| `paid_vs_peer_ratio` | Dollars per claim vs. same-specialty peers (above 1 = pricier than peers) |
| `claims_vs_peer_ratio` | Claims per patient vs. peers (above 1 = more claims per patient than peers) |
| `n_distinct_hcpcs_vs_peer` | How many different procedure codes the provider bills vs. peers |

**Timing and trend — how the provider's billing changes over time (T1–T7)**

| Signal | What it means |
|--------|----------------|
| `mom_paid_growth_volatility` (T1) | How erratic the month-to-month payments are |
| `cv_monthly_paid` (T2) | How uneven monthly payment volume is |
| `peak_to_median_paid` (T3) | How much the biggest month spiked above a typical month |
| `onset_ramp_slope` (T4) | How fast billing ramped up when the provider first started |
| `post_peak_dropoff` (T5) | How sharply billing fell off after hitting its peak |
| `new_hcpcs_fraction` (T6) | What share of the year's procedure codes were brand-new for this provider |
| `excess_yoy_growth` (T7) | How much the provider's year-over-year growth outran its peers' growth |

No single signal proves anything. The model looks at all 15 together and flags providers that are
unusual on several at once.

## What comes out

The pipeline produces two result files (full column definitions in `DATA_DICTIONARY.md`):

**`provider_rankings.csv` — the investigation shortlist (one row per provider per year).**
Ranked from most to least unusual. Each row carries the anomaly score, the total dollars paid that
year, a plain-English `description` of *why* it was flagged, and whether the provider was on the OIG
exclusion list. This is where a reviewer starts: the worst-scoring provider-years, with the reasons
spelled out. By default the top 200 are saved.

**`provider_summary.csv` — one row per provider, for triage (optional).**
Collapses a provider's multiple years into a single line: its worst year, its average score, how many
years were flagged, and **its total Medicaid dollars across all years.** This lets a reviewer
re-prioritize by what matters to them — focus on the biggest-dollar exposure, or on repeat offenders
flagged year after year. By default the top 200 providers are saved.

**How we know it's working:** the pipeline compares its top-ranked providers against the OIG's known
exclusion list and reports **precision and "lift"** — how much more likely a top-ranked provider is to
be a known excluded provider than a randomly chosen one. Higher lift means the ranking is doing real work.

## The dollar threshold

Medicaid spending is extremely top-heavy: a large share of providers bill only a few thousand dollars,
while a small fraction account for most of the money. Without a floor, the "most unusual" list fills up
with tiny providers whose ratios look weird simply because the numbers are small (e.g. a site that billed
a few hundred dollars). To avoid that, providers under **$10 million** in lifetime Medicaid payments are
filtered out up front in Stage 1.

That $10M figure is the current default and is set with `--min-total-paid`. The right cutoff — the point
where a provider is big enough to be worth investigating — is a policy decision, so it's left adjustable.

## Project structure

```
medicaid-fraud-detection/
├── data/
│   ├── raw/             # Source extracts (spending, NPPES, LEIE) — never committed
│   ├── processed/       # Cleaned tables, features, and ranked output
│   └── reference/       # Static lookups
├── src/
│   ├── clean_data.py        # Stage 1 — ingest & filter
│   ├── build_features.py    # Stage 2 — build the 15 signals
│   └── analyze_anomalies.py # Stage 3 — score, explain, rank
├── tests/
├── requirements.txt
├── DATA_DICTIONARY.md
└── README.md
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running the pipeline

```bash
# Stage 1 — clean, join metadata, and filter to providers paid > $10M
python -m src.clean_data \
  --spending data/raw/medicaid-provider-spending.parquet \
  --nppes    data/raw/nppes/npidata_pfile.csv \
  --leie     data/raw/leie.csv \
  --output   data/processed
# (use --min-total-paid to change the $10M cutoff)

# Stage 2 — build the per-(provider, year) fraud signals
python -m src.build_features \
  --monthly   data/processed/provider_monthly.parquet \
  --providers data/processed/providers_clean.csv \
  --annual    data/processed/provider_annual.parquet \
  --output    data/processed/fraud_features.csv

# Stage 3 — score, rank, and roll up to a triage list
python -m src.analyze_anomalies data/processed/fraud_features.csv \
  --names            data/processed/providers_clean.csv \
  --output           data/processed/provider_rankings.csv \
  --provider-summary data/processed/provider_summary.csv

# Run tests
pytest tests/
```

Useful Stage 3 options: `--contamination` (expected share of anomalies, default 0.05),
`--n-estimators` (number of trees, default 300), and `--save-top` / `--summary-top` (how many rows
to write, default 200 each; set to 0 to save everything).

## Compliance & privacy

All data used in this pipeline is subject to HIPAA. Raw data files are excluded from version control
via `.gitignore` and must never be committed. De-identification rules must be applied before any data
leaves the secure environment.
