"""
probabilistic_resolver.py — Splink-backed entity resolution (optional backend).

The default resolver (``resolve_entities.py``) is deterministic: exact normalized
name + PAC/alias keys. That is precise but brittle to spelling, abbreviation, and
address noise — and the manifesto calls entity resolution "the hard part." This
module is the probabilistic upgrade path, built on **Splink** (Fellegi-Sunter
record linkage on DuckDB — the same engine our heavy joins already use).

How it works (cold-start, no labels):
  * Each record's name is reduced with the SHARED ``norm_org_name`` (reuse, hard
    rule #6), so "Acme Health, Inc." and "ACME HEALTH LLC" already collapse.
  * Splink compares record pairs on the normalized name (exact / Jaro-Winkler
    levels), state, and (optional) address, combining per-level match weights
    into a posterior match probability via Fellegi-Sunter.
  * The match weights here are DOCUMENTED COLD-START values (like Model A's
    sector priors and Model C's underwriting assumptions) — they rank obvious
    duplicates far above non-duplicates out of the box. On real data, call with
    ``calibrate=True`` to refine them with Splink's unsupervised EM. Blocking
    keeps it from comparing all N² pairs.
  * Pairs above ``match_threshold`` are clustered (connected components) into
    resolved entities.

Splink is an OPTIONAL dependency (``requirements-trey.txt``, not core). Import is
lazy; a clear error fires if it's missing. CI skips this when splink is absent.
"""

from __future__ import annotations

import pandas as pd

from .resolve_entities import norm_org_name

# Cold-start Fellegi-Sunter weights. m = P(level | match), u = P(level | non-match).
# Recalibrate with EM on real data (calibrate=True). Tuned so an exact normalized
# name scores ~0.99 and a near-name ~0.85 at the default prior.
PRIOR_TWO_RANDOM_MATCH = 0.10      # expected duplicate density (org files are dup-heavy)
DEFAULT_MATCH_THRESHOLD = 0.85


def _require_splink():
    try:
        import splink  # noqa: F401
    except ImportError as e:                                # pragma: no cover
        raise ImportError(
            "probabilistic_resolver needs Splink (an optional backend). "
            "Install it: pip install splink  (it's in requirements-trey.txt)."
        ) from e


def _settings(use_address: bool):
    from splink import SettingsCreator, block_on
    import splink.comparison_level_library as cll
    from splink.comparison_library import CustomComparison

    name_cmp = CustomComparison(
        output_column_name="name_key",
        comparison_levels=[
            cll.NullLevel("name_key"),
            cll.ExactMatchLevel("name_key").configure(
                m_probability=0.6, u_probability=0.001),
            cll.JaroWinklerLevel("name_key", 0.9).configure(
                m_probability=0.35, u_probability=0.02),
            cll.ElseLevel().configure(m_probability=0.05, u_probability=0.979),
        ])
    state_cmp = CustomComparison(
        output_column_name="state",
        comparison_levels=[
            cll.NullLevel("state"),
            cll.ExactMatchLevel("state").configure(
                m_probability=0.95, u_probability=0.25),
            cll.ElseLevel().configure(m_probability=0.05, u_probability=0.75),
        ])
    comparisons = [name_cmp, state_cmp]
    if use_address:
        comparisons.append(CustomComparison(
            output_column_name="address",
            comparison_levels=[
                cll.NullLevel("address"),
                cll.ExactMatchLevel("address").configure(
                    m_probability=0.7, u_probability=0.02),
                cll.LevenshteinLevel("address", 3).configure(
                    m_probability=0.2, u_probability=0.05),
                cll.ElseLevel().configure(m_probability=0.1, u_probability=0.93),
            ]))
    # block on state, and on the first 4 chars of the name key — together these
    # catch same-state and same-name-cross-state candidates without N² blowup.
    return SettingsCreator(
        link_type="dedupe_only",
        blocking_rules_to_generate_predictions=[
            block_on("state"),
            block_on("substr(name_key, 1, 4)"),
        ],
        comparisons=comparisons,
        probability_two_random_records_match=PRIOR_TWO_RANDOM_MATCH,
    )


def resolve_probabilistic(records: pd.DataFrame, name_col: str = "name",
                          state_col: str | None = "state",
                          address_col: str | None = None,
                          match_threshold: float = DEFAULT_MATCH_THRESHOLD,
                          calibrate: bool = False) -> pd.DataFrame:
    """Cluster records into resolved entities by probabilistic matching.

    ``records`` needs a unique ``unique_id`` column (created if absent) and the
    name column; state/address are optional. Returns the input plus
    ``cluster_id`` (the resolved-entity id, shared by all records of one entity).
    With ``calibrate=True`` the cold-start weights are refined by Splink EM on
    the data itself (use on real data, not tiny fixtures).
    """
    _require_splink()
    from splink import Linker, DuckDBAPI, block_on

    df = records.copy().reset_index(drop=True)
    if "unique_id" not in df.columns:
        df["unique_id"] = range(len(df))
    df["name_key"] = df[name_col].map(norm_org_name)
    df["state"] = (df[state_col].fillna("").astype(str).str.upper()
                   if state_col and state_col in df.columns else "")
    use_address = bool(address_col and address_col in df.columns)
    if use_address:
        df["address"] = df[address_col].fillna("").astype(str).str.upper()

    linker = Linker(df, _settings(use_address), db_api=DuckDBAPI())
    if calibrate:                                           # pragma: no cover
        linker.training.estimate_u_using_random_sampling(max_pairs=1e6)
        linker.training.estimate_parameters_using_expectation_maximisation(
            block_on("state"))

    preds = linker.inference.predict(threshold_match_probability=0.5)
    clusters = linker.clustering.cluster_pairwise_predictions_at_threshold(
        preds, threshold_match_probability=match_threshold)
    cdf = clusters.as_pandas_dataframe()[["unique_id", "cluster_id"]]
    out = df.drop(columns=["name_key", "state", "address"], errors="ignore")
    return out.merge(cdf, on="unique_id", how="left")
