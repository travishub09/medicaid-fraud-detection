# Medicaid Fraud Detection

A machine learning pipeline for identifying fraudulent and anomalous activity in Medicaid claims, provider billing records, and beneficiary utilization data.

## Overview

Medicaid fraud costs the U.S. healthcare system tens of billions of dollars annually. This project applies statistical and unsupervised ML techniques to surface providers and beneficiaries whose billing or utilization patterns deviate significantly from their peers, flagging them for investigator review.

Primary fraud patterns targeted:

| Pattern | Description |
|---------|-------------|
| Billing for services not rendered | Claims submitted for visits or procedures that never occurred |
| Upcoding | Billing a higher-complexity procedure code than the service delivered |
| Unbundling | Splitting a bundled service into separately billed components to inflate reimbursement |
| Duplicate billing | Submitting the same claim multiple times or across multiple payers |
| Impossible service combinations | Patient billed at two locations simultaneously, or receiving mutually exclusive procedures |
| Excluded provider billing | Services billed by providers on the OIG exclusion list |
| Pill mill / prescription fraud | Providers prescribing controlled substances at volumes far exceeding clinical norms |
| Doctor shopping | Beneficiaries obtaining overlapping prescriptions from multiple prescribers |
| Dead patient billing | Claims submitted for beneficiaries deceased at the time of service |
| Identity theft | Beneficiary credentials used by a third party to obtain services |

## Data Sources

| Source | Description |
|--------|-------------|
| Claims — Part A | Inpatient and outpatient facility claims (DRG, revenue codes) |
| Claims — Part B | Professional and supplier claims (CPT/HCPCS procedure codes) |
| Claims — Part D | Prescription drug event records |
| Provider enrollment | NPI registry, specialty, practice address, enrollment date |
| Beneficiary eligibility | Demographics, enrollment period, managed care plan |
| OIG exclusion list | Providers excluded from federal healthcare programs |
| Fee schedules | CMS-published allowed amounts by procedure and geography |
| Prior authorization | Approved authorizations and their utilized/expired status |

## Project Structure

```
medicaid-fraud-detection/
├── data/
│   ├── raw/                  # Source extracts — never committed
│   ├── processed/            # Cleaned and feature-engineered tables
│   └── reference/            # Static lookups (fee schedules, ICD/CPT codes, OIG list)
├── notebooks/                # Exploratory analysis and model prototyping
├── src/
│   ├── ingestion/            # Loaders per data source
│   ├── features/             # Feature engineering (provider and beneficiary level)
│   ├── models/               # Anomaly detection and rule-based scoring
│   ├── scoring/              # Batch scoring and alert generation
│   └── alerts/               # Case deduplication and queue management
├── tests/
├── configs/
├── requirements.txt
├── DATA_DICTIONARY.md
└── README.md
```

## Analytical Approach

### Stage 1 — Rule-Based Flags
Fast, deterministic checks run against every claim:
- Claim date after beneficiary date of death
- Provider NPI on the OIG exclusion list at the time of service
- Duplicate claim (same beneficiary + provider + date + procedure)
- Procedure combinations flagged as mutually exclusive by CMS edits (NCCI)
- Billed amount exceeds the Medicare fee schedule by a configurable threshold

### Stage 2 — Peer-Group Anomaly Scoring
Providers and beneficiaries are clustered by specialty/demographics into peer groups. Within each peer group, size-invariant features (rates per beneficiary, procedure mix ratios, utilization percentiles) are scored using an **Isolation Forest with DIFFI** feature importance to:
1. Assign each entity an anomaly score relative to its peers
2. Identify which billing dimensions drove the score
3. Generate a plain-English description of the top risk factors

### Stage 3 — Network Analysis *(planned)*
Graph-based analysis to surface coordinated fraud rings: clusters of providers, beneficiaries, and pharmacies that refer each other at abnormal rates.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in credentials
```

## Running the Pipeline

```bash
# Step 1 — clean claims and compute provider/beneficiary features
python -m src.clean_data --claims data/raw/claims_part_b.csv --output data/processed/provider_features.csv

# Step 2 — score and rank providers by anomalousness
python -m src.analyze_anomalies data/processed/provider_features.csv --output data/processed/provider_rankings.csv

# Run tests
pytest tests/
```

## Compliance & Privacy

All data used in this pipeline is subject to HIPAA. Raw data files are excluded from version control via `.gitignore`. De-identification must be applied before any data is shared outside the secure enclave. See `configs/privacy.yaml` for de-identification rules.

## Contributing

Branch from `main` using `feature/<short-description>`. All new logic must include unit tests. Do not commit any data files or credentials.
