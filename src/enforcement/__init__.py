"""
enforcement — the structured DOJ/OIG case database and what it feeds.

The ground truth of what was actually prosecuted (09-data-procurement.md #6):
press releases and settlement announcements structured into one case table.
Three consumers: Model A's enforcement prior + positive labels, Model C's primary
training labels, and the derived sector priors that replace the hand-set
placeholders in ``model_a/sector_priors.py``.

  case_db.py        the case schema + press-release text parser + DB builder
  derive_priors.py  case DB → enforcement-weighted sector multipliers
  fetch.py          justice.gov / oig.hhs.gov listing fetcher (STUB — run where
                    network access and scraping review permit)

Important: labels are for discovering scheme signatures, never for legal
conclusions — and the public record is survivorship-biased toward wins
(06-model-c.md §labels). The schema carries source provenance for every row.
"""

from .case_db import CASE_COLUMNS, parse_press_release, build_case_db
from .derive_priors import derive_sector_priors

__all__ = ["CASE_COLUMNS", "parse_press_release", "build_case_db",
           "derive_sector_priors"]
