"""
peers.py — hierarchical peer groups, guarded percentiles, complexity adjustment.

The four failure modes this module exists to prevent, and how:

1. WRONG PEERS (hospice vs. family practice). Peer cells come from a fallback
   LADDER — most specific first, e.g. taxonomy×entity×state → taxonomy×entity
   → taxonomy. A provider is ASSIGNED to the most specific level whose cell
   clears ``min_peer``; below every level it is not scored, with a reason.
   The level and cell size are recorded per provider (``peer_basis``,
   ``peer_n``) so every dossier can state exactly who the comparison was
   against.

   Crucial subtlety: assignment and baseline are different things. When the
   five Oklahoma family doctors fall back to the national level, they are
   ranked against ALL family doctors nationally — including the Texans who
   have their own state cell — not against just the five of themselves. Every
   level's baseline is its full population; assignment only decides which
   level's ranking a provider reads its percentile from.

2. SIZE CONFOUNDING (the referral-center trap). Raw utilization rises with
   legitimate scale. ``complexity_adjust`` residualizes each metric against
   control columns (log volume, code breadth, …) WITHIN the peer cell, so the
   percentile measures excess over what a provider of that size/breadth in
   that specialty would show. Small cells fall back to median-centering —
   recorded, never silent.

3. DEGENERATE CELLS. A cell whose metric is constant yields NaN percentiles
   plus a flag — never a confident rank from a meaningless ordering.

4. UNEXPLAINED EXCLUSIONS. Complexity is context, not exoneration:
   ``complexity_flags`` marks referral-center-shaped providers so the dossier
   says "this looks like a complex practice" instead of silently absorbing it.

One-sided convention throughout: high percentile = more than peers = the only
direction that is ever suspicious.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_PEER = 30
DEFAULT_LADDER: tuple[tuple[str, ...], ...] = (
    ("taxonomy_code", "entity_type", "state"),
    ("taxonomy_code", "entity_type"),
    ("taxonomy_code",),
)
NOT_SCORED = "__not_scored__"
MIN_FIT_N = 12                    # below this, least squares is noise
_KEY = "__peer_key_L{i}"          # per-level key columns carried on the frame


def assign_peer_groups(df: pd.DataFrame,
                       ladder: tuple[tuple[str, ...], ...] = DEFAULT_LADDER,
                       min_peer: int = MIN_PEER) -> pd.DataFrame:
    """Assign every row to the most specific ladder level whose FULL-population
    cell clears ``min_peer``.

    Adds: peer_level (int, -1 = not scored), peer_basis, peer_n (the size of
    the baseline population the row is ranked against), peer_id (readable cell
    key), and one hidden ``__peer_key_L<i>`` column per level — the ranking
    functions use those to compute each level's baseline over its full
    population, then select per row by assigned level.
    """
    out = df.copy()
    n = len(out)
    peer_level = pd.Series(-1, index=out.index)
    peer_basis = pd.Series("peer_group_too_small", index=out.index)
    peer_n = pd.Series(0, index=out.index)
    peer_id = pd.Series(NOT_SCORED, index=out.index)

    for i, level in enumerate(ladder):
        keycol = _KEY.format(i=i)
        if any(c not in out.columns for c in level):
            out[keycol] = pd.NA
            continue
        vals = out[list(level)].astype(str)
        complete = vals.ne("").all(axis=1) & vals.ne("nan").all(axis=1) \
            & vals.ne("<NA>").all(axis=1)
        key = (f"L{i}:" + vals.agg("|".join, axis=1)).where(complete)
        out[keycol] = key
        sizes = key.value_counts()                 # FULL population per cell
        cell_n = key.map(sizes)
        take = (peer_level == -1) & complete & (cell_n >= min_peer)
        peer_level[take] = i
        peer_basis[take] = "×".join(level)
        peer_n[take] = cell_n[take].astype(int)
        peer_id[take] = key[take]

    out["peer_level"] = peer_level
    out["peer_basis"] = peer_basis
    out["peer_n"] = peer_n
    out["peer_id"] = peer_id
    assert len(out) == n, "peer assignment must never change row count"
    return out


def _levels_in(df: pd.DataFrame) -> list[int]:
    return sorted(int(c.split("_L")[1]) for c in df.columns
                  if c.startswith("__peer_key_L"))


def one_sided_percentiles(df: pd.DataFrame, metric_cols: list[str]) -> pd.DataFrame:
    """Percentiles within each row's ASSIGNED level, baselined on that level's
    full population. Degenerate cells → NaN + a ``<metric>__degenerate`` flag.
    Not-scored rows → NaN."""
    out = pd.DataFrame(index=df.index)
    for m in metric_cols:
        if m not in df.columns:
            continue
        vals = pd.to_numeric(df[m], errors="coerce")
        result = pd.Series(np.nan, index=df.index)
        degenerate = pd.Series(False, index=df.index)
        for i in _levels_in(df):
            keycol = _KEY.format(i=i)
            at_level = df["peer_level"] == i
            if not at_level.any():
                continue
            pct = vals.groupby(df[keycol]).rank(method="average", pct=True)
            nun = vals.groupby(df[keycol]).transform("nunique")
            result[at_level] = pct[at_level]
            deg = at_level & vals.notna() & (nun <= 1)
            result[deg] = np.nan
            degenerate |= deg
        out[m] = result
        out[f"{m}__degenerate"] = degenerate
    return out


def complexity_adjust(df: pd.DataFrame, metric_cols: list[str],
                      control_cols: list[str],
                      min_fit_n: int = MIN_FIT_N) -> pd.DataFrame:
    """Within-cell residualization: adjusted = observed − expected-for-complexity.

    Fits metric ~ intercept + controls by least squares inside each baseline
    cell (the full level population), per level; each row takes the residual
    from its assigned level's cell. Cells below ``min_fit_n`` (or with missing
    controls) median-center instead — recorded in ``<metric>__adjustment``.
    """
    controls = [c for c in control_cols if c in df.columns]
    out = pd.DataFrame(index=df.index)
    for m in metric_cols:
        if m not in df.columns:
            continue
        y_all = pd.to_numeric(df[m], errors="coerce")
        adj = pd.Series(np.nan, index=df.index)
        how = pd.Series("", index=df.index)
        for i in _levels_in(df):
            keycol = _KEY.format(i=i)
            at_level = df["peer_level"] == i
            if not at_level.any():
                continue
            for _, idx in df[df[keycol].notna()].groupby(keycol).groups.items():
                sel = df.index.isin(idx) & at_level         # rows to fill here
                if not sel.any():
                    continue
                y = y_all.loc[idx]
                X = (df.loc[idx, controls].apply(pd.to_numeric, errors="coerce")
                     if controls else pd.DataFrame(index=idx))
                fit = y.notna() & (X.notna().all(axis=1) if controls else True)
                if controls and int(fit.sum()) >= min_fit_n:
                    Xf = np.column_stack([np.ones(int(fit.sum())),
                                          X[fit].to_numpy(float)])
                    yf = y[fit].to_numpy(float)
                    beta, *_ = np.linalg.lstsq(Xf, yf, rcond=None)
                    # robust second pass: the fit itself must not be dragged by
                    # the very outliers we're hunting (median/MAD convention) —
                    # drop residuals beyond 3×1.4826×MAD and refit on the mass
                    resid = yf - Xf @ beta
                    med = float(np.median(resid))
                    mad = float(np.median(np.abs(resid - med)))
                    if mad > 0:
                        keep = np.abs(resid - med) <= 3 * 1.4826 * mad
                        if min_fit_n <= int(keep.sum()) < len(resid):
                            beta, *_ = np.linalg.lstsq(Xf[keep], yf[keep],
                                                       rcond=None)
                    ok = sel & y_all.notna() & \
                        df[controls].apply(pd.to_numeric, errors="coerce") \
                          .notna().all(axis=1)
                    if ok.any():
                        Xp = np.column_stack([
                            np.ones(int(ok.sum())),
                            df.loc[ok, controls].apply(
                                pd.to_numeric, errors="coerce").to_numpy(float)])
                        adj[ok] = y_all[ok].to_numpy(float) - Xp @ beta
                        how[ok] = "residualized"
                    rest = sel & y_all.notna() & ~ok
                    if rest.any():
                        adj[rest] = y_all[rest] - float(y[fit].median())
                        how[rest] = "median_centered"
                else:
                    med = float(y.median()) if y.notna().any() else np.nan
                    rows = sel & y_all.notna()
                    adj[rows] = y_all[rows] - med
                    how[rows] = "median_centered"
        out[f"{m}__adj"] = adj
        out[f"{m}__adjustment"] = how
    return out


def complexity_flags(df: pd.DataFrame,
                     volume_col: str = "total_services",
                     breadth_col: str = "code_breadth",
                     top_decile: float = 0.9) -> pd.DataFrame:
    """Context flags: who LOOKS like a referral center / complex practice
    within its own peer baseline. Informs the reader; never unscores anyone."""
    helper = one_sided_percentiles(
        df, [c for c in (volume_col, breadth_col) if c in df.columns])
    out = pd.DataFrame(index=df.index)
    out["high_volume_for_peer"] = (
        (helper[volume_col] >= top_decile).fillna(False).astype(int)
        if volume_col in helper.columns else 0)
    out["high_breadth_for_peer"] = (
        (helper[breadth_col] >= top_decile).fillna(False).astype(int)
        if breadth_col in helper.columns else 0)
    out["likely_complex_practice"] = (
        (out["high_volume_for_peer"] + out["high_breadth_for_peer"]) >= 2
    ).astype(int)
    return out


def peer_report(df: pd.DataFrame) -> str:
    """Markdown summary of comparison quality: ladder usage, cell sizes, and
    how many rows fell out entirely (excluded, never force-ranked)."""
    n = len(df)
    lines = ["# PEER_REPORT — comparison quality\n",
             f"_Rows: {n:,}. Every percentile was computed against the baseline "
             "recorded on its row (peer_basis / peer_n)._\n",
             "\n## Ladder level used (peer_basis)\n",
             "| basis | rows | share | median baseline size |\n|---|--:|--:|--:|\n"]
    for basis, grp in df.groupby("peer_basis"):
        med = int(grp["peer_n"].median()) if (grp["peer_n"] > 0).any() else 0
        lines.append(f"| {basis} | {len(grp):,} | {len(grp)/n:.1%} | {med:,} |\n")
    scored = df[df["peer_level"] >= 0]
    if len(scored):
        sizes = scored.groupby("peer_id").size()
        lines.append("\n## Assigned-cell sizes (scored rows)\n")
        lines.append(f"- cells: {len(sizes):,}; min {int(sizes.min()):,}, "
                     f"median {int(sizes.median()):,}, max {int(sizes.max()):,}\n")
    lines.append(f"\n- not scored (no adequate peer group): "
                 f"{int((df['peer_level'] < 0).sum()):,} rows — EXCLUDED from "
                 "outlier claims, never force-ranked.\n")
    return "".join(lines)
