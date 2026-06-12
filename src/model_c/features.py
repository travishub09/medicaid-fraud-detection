"""
features.py — Model C feature groups (SCAFFOLD).

Per ``docs/platform/06-model-c.md`` §2.3. Feature groups assembled from intake +
public records + Model A:

    subject_matter   scheme type weighted by current DOJ priority
    defendant        size, solvency, public/private, repeat-offender (prior CIAs)
    evidence         documentary support, specificity, firsthand vs hearsay, witnesses
    corroboration    whether Model A's public-data signal supports the allegation
    relator          credibility, knowledge tier, culpability, first-to-file clearance,
                     original-source strength, public-disclosure exposure
    jurisdiction     district/USAO intervention rate; venue/circuit (Zafirov) exposure
    magnitude        single damages → trebled; claim volume driving penalties
    timing           statute-of-limitations runway
"""

from __future__ import annotations

import pandas as pd


def build_case_features(intake: pd.DataFrame, model_a_signal: pd.DataFrame,
                        enforcement_context: pd.DataFrame) -> pd.DataFrame:
    """Assemble the per-case feature row Model C scores."""
    raise NotImplementedError("Model C feature assembly — see 06-model-c.md §2.3")
