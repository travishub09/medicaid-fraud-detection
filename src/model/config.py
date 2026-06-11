"""
config.py (model) — paths, the label, and the feature blocklist.

Single source of truth for everything train.py / score.py / data.py share.
All real data lives OUTSIDE the repo under ~/Desktop/Data (HIPAA); this package
reads the Model/ parquets as immutable inputs and writes only NEW files under
Model/artifacts/ and Model/scores/.
"""

from pathlib import Path

DATA_DIR = Path.home() / "Desktop" / "Data"
MODEL_DATA_DIR = DATA_DIR / "Model"
DETECTION_TABLES_DIR = DATA_DIR / "detection" / "tables"

# Inputs (immutable — never overwritten)
PU_TRAINING_PARQUET = MODEL_DATA_DIR / "provider_features_pu.parquet"          # 308,038 x 57
SCORED_UNIVERSE_PARQUET = MODEL_DATA_DIR / "provider_features_scored.parquet"  # 617,062 x 57
NPI_TO_COMPANY_MAP = DETECTION_TABLES_DIR / "npi_to_company_map.parquet"
COMPANY_ROLLUP = DETECTION_TABLES_DIR / "company_rollup.parquet"

# Outputs
ARTIFACTS_DIR = MODEL_DATA_DIR / "artifacts"
SCORES_DIR = MODEL_DATA_DIR / "scores"

LABEL = "provider_on_leie"

# Identifier / bookkeeping columns — never features.
IDENTIFIER_COLS = ["npi", "org_legal_name", "first_month", "last_month"]

# Label-leakage blocklist (CLAUDE.md): anything derived from the LEIE or from
# excluded-owner linkage encodes the label. Patterns cover columns that may not
# exist in a given file — absence is fine, presence is always dropped.
LEAKAGE_COLS = [
    "provider_on_leie",                     # IS the label
    "facility_has_excluded_owner_high",
    "facility_has_excluded_owner_probable",
    "facility_excluded_owner_n_high",
    "facility_excluded_owner_n_probable",
    "excluded_owner_role",
    "any_billed_after_exclusion",
    "billed_after_exclusion",
    "excluded_after_billing",
]
LEAKAGE_SUBSTRINGS = ["excluded_owner", "billed_after_exclusion", "probable_excluded"]

# Unsupervised-detector columns joined on for the PU filter. NOT features:
# the PU negatives were SELECTED on anomaly_score == 0, so these columns encode
# the sampling design, not provider behavior — the model would learn the filter.
DETECTOR_SCORE_COLS = [
    "anomaly_score",
    "n_anomaly_signals",
    "anomaly_lead",
    "not_scored",
    "not_scored_reason",
]

CATEGORICAL_FEATURES = ["entity_type", "primary_taxonomy", "practice_state"]

SEED = 42
VAL_FRACTION = 0.20

# Expected shapes (hard-fail on drift, matching the pipeline's assertion style)
EXPECTED_PU_ROWS = 308_038
EXPECTED_PU_POSITIVES = 578
EXPECTED_UNIVERSE_ROWS = 617_062
