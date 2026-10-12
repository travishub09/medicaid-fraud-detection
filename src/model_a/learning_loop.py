"""
learning_loop.py — the post-run learning loop: what the model got wrong, what the
labels got wrong, what moved under the data, and how sure the probabilities are.

  python -m src.model_a.learning_loop --run-dir <run> [--prev-run-dir <earlier run>]
                                      [--data-root <root>] [--ruler <forward label csv>]

Four analyses on ONE out-of-fold scoring of the run's matrix (the shipped scorer's
policy: label tier, label_weight, seed bagging, case-cluster folds), read from
<run>/provider_oof_scores.parquet when the priority-list build wrote it, else
recomputed once and written there:

  1. LABEL NOISE (confident learning, Northcutt-style): with calibrated out-of-fold
     probabilities p and the training label y, t1 = mean p over labelled positives and
     t0 = mean p over labelled negatives. A labelled positive with p < t0 is a SUSPECT
     POSITIVE (the record says excluded but nothing in the billing looks like it:
     identity collision, administrative ban, or a scheme the features cannot see).
     A labelled negative with p >= t1 is a CANDIDATE POSITIVE (billing shaped like the
     enforced providers' with no record yet). Both lists carry the label provenance so
     a reviewer sees WHY the row is labelled as it is.
  2. ACTIVE LEARNING QUEUE: the rows where one public-records verdict moves the most
     (suspects by dollars, candidates by score x dollars) -> label_review_queue.csv +
     label_review_prompts.jsonl in the pursuit dossier's prompt schema, so
     `pursuit_dossier --run-dir <run> --dispatch-file label_review_prompts.jsonl` sends
     them through the ONE Manus dispatcher with its registry. Verdicts are evidence
     for the label store, never the label itself.
  3. FEATURE DRIFT vs the previous run: population stability index per trainable
     column on a reservoir sample of both matrices (DuckDB reads only those columns)
     plus the null-rate change. PSI >= 0.25 is a data defect until explained (a file
     edition moved, a join broke), 0.10 to 0.25 is worth a look.
  4. NEAR MISSES: every forward positive (the run's FCA ruler, else the exclusion
     ruler) that the scorer left outside the top fraction, with its rank and the five
     features where it sits furthest BELOW the top set's profile (robust z against
     the top set's median / MAD). Summed over misses, the features that recur are the
     feature requests: what the model would have needed to see.
  5. CALIBRATION MONITOR: ECE / Brier of the calibrated probabilities against the
     training label (calibration.calibration_metrics) and a conformal p-value per
     provider for membership in the positive class (conformal.conformal_pvalues,
     calibrated on the labelled positives' out-of-fold scores), written back into
     provider_oof_scores.parquet as `conformal_p`.

Writes LEARNING_LOOP.md + learning_loop.json (run_issues reads the json). Nothing
here changes a label or a model: every output is a review queue or a diagnosis.
Leads for counsel review, never accusations.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

PSI_WARN = 0.10
PSI_FAIL = 0.25
OOF_FILE = "provider_oof_scores.parquet"
PROVENANCE_COLS = ["provider_name", "org_legal_name", "practice_state", "addr_city", "entity_type",
                   "net_paid", "exclusion_label_sources", "label_basis", "case_ids", "amount_usd",
                   "label_weight", "assessable", "primary_taxonomy"]


def _num(v) -> float:
    """A plain float from any scalar the matrix can hold (pandas NA, None, strings)."""
    try:
        if v is None or v is pd.NA or (isinstance(v, float) and v != v):
            return 0.0
        f = float(v)
        return f if f == f else 0.0
    except (TypeError, ValueError):
        return 0.0


def _text(v) -> str:
    if v is None or v is pd.NA:
        return ""
    try:
        if isinstance(v, float) and v != v:
            return ""
    except TypeError:
        pass
    return str(v)


# --------------------------------------------------------------------------- #
# scores
# --------------------------------------------------------------------------- #

def load_or_score(run_dir: Path, matrix: pd.DataFrame, manifest: dict) -> pd.DataFrame:
    """npi, score (OOF), prob (cross-fit isotonic). Reads the dossier's file when
    present (the SAME numbers the shipped list used), else computes and writes it."""
    p = run_dir / OOF_FILE
    if p.exists():
        f = pd.read_parquet(p)
        f["npi"] = f["npi"].astype(str)
        if set(f["npi"]) >= set(matrix["npi"].astype(str)):
            return f
    from .company_list import calibrate_oof, oof_scores
    scores = oof_scores(matrix, manifest)
    probs = calibrate_oof(matrix, scores)
    f = pd.DataFrame({"npi": matrix["npi"].astype(str), "score": scores, "prob": probs})
    f.to_parquet(p, index=False)
    return f


def _training_y(matrix: pd.DataFrame, manifest: dict) -> np.ndarray:
    from .company_list import _training_label
    label_col = manifest.get("label") or ("provider_on_exclusion" if "provider_on_exclusion" in matrix.columns
                                          else "provider_on_leie")
    if label_col not in matrix.columns:
        return np.zeros(len(matrix), dtype=int)
    return np.asarray(_training_label(matrix, label_col)).astype(int)


# --------------------------------------------------------------------------- #
# 1. label noise
# --------------------------------------------------------------------------- #

def label_noise(matrix: pd.DataFrame, oof: pd.DataFrame, y: np.ndarray) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Confident-learning split of the labelled rows. Returns (suspect_positives,
    candidate_positives, stats). Both frames carry the provenance columns."""
    m = matrix.set_index(matrix["npi"].astype(str))
    o = oof.set_index("npi")
    p = pd.to_numeric(o["prob"], errors="coerce").reindex(m.index).fillna(0.0).to_numpy(dtype=float)
    s = pd.to_numeric(o["score"], errors="coerce").reindex(m.index).fillna(0.0).to_numpy(dtype=float)
    y = np.asarray(y).astype(int)
    if y.sum() == 0 or (y == 0).sum() == 0:
        return pd.DataFrame(), pd.DataFrame(), {"t0": None, "t1": None, "n_suspect": 0, "n_candidate": 0}
    t1 = float(p[y == 1].mean())
    t0 = float(p[y == 0].mean())
    prov = [c for c in PROVENANCE_COLS if c in m.columns]
    base = m[prov].copy() if prov else pd.DataFrame(index=m.index)
    # nullable dtypes -> plain object / float so sorting and the queue never meet pd.NA
    for c in prov:
        if pd.api.types.is_numeric_dtype(base[c]) or pd.api.types.is_bool_dtype(base[c]):
            base[c] = pd.to_numeric(base[c], errors="coerce").astype("float64")
        else:
            base[c] = base[c].astype(object).where(base[c].notna(), "")
    base.insert(0, "npi", m.index)
    base["score"] = s
    base["prob"] = p
    base["rank"] = pd.Series(-s).rank(method="min").to_numpy()
    sus = base[(y == 1) & (p < t0)].copy()
    sus["reason"] = "labelled positive scored below the mean negative: identity collision, administrative ban, or a scheme the features cannot see"
    sus = sus.sort_values(["prob", "net_paid"] if "net_paid" in sus.columns else ["prob"],
                          ascending=[True, False] if "net_paid" in sus.columns else [True])
    cand = base[(y == 0) & (p >= t1)].copy()
    cand["reason"] = "labelled negative scored at or above the mean positive: billing shaped like the enforced providers' with no record yet"
    cand = cand.sort_values("prob", ascending=False)
    stats = {"t0": round(t0, 6), "t1": round(t1, 6), "n_pos": int(y.sum()), "n_neg": int((y == 0).sum()),
             "n_suspect": int(len(sus)), "n_candidate": int(len(cand)),
             "suspect_share_of_positives": round(len(sus) / max(1, int(y.sum())), 4)}
    return sus.reset_index(drop=True), cand.reset_index(drop=True), stats


# --------------------------------------------------------------------------- #
# 2. active-learning queue
# --------------------------------------------------------------------------- #

def _label_review_prompt(r: pd.Series, kind: str) -> str:
    who = _text(r.get("provider_name")) or _text(r.get("org_legal_name")) or f"NPI {r['npi']}"
    where = ", ".join(x for x in (_text(r.get("addr_city")), _text(r.get("practice_state"))) if x) or "unknown location"
    ask = ("Our records show a program exclusion or termination for this provider number, while its "
           "billing pattern looks ordinary. Establish from public records whether the exclusion record "
           "belongs to THIS provider (same person or organization, same location and specialty) and "
           "what the stated cause was."
           if kind == "suspect_positive" else
           "This provider's public billing pattern resembles providers who were later subject to "
           "enforcement, and we hold no record for it. Report any PUBLIC enforcement, licensing-board, "
           "settlement, lawsuit or news record that names it, with URLs; report plainly when there is none.")
    return (f"You are a healthcare public-records researcher. Using PUBLIC sources only (OIG exclusion "
            f"list, state Medicaid exclusion lists, licensing boards, CMS enrollment records, court "
            f"dockets, news), research the Medicaid provider '{who}' (NPI {r['npi']}; {where}). {ask} "
            f"Report no patient information, contact nobody, and make no accusation about any person. "
            f"Return a JSON attachment with keys: identity_match (yes / no / unclear, with the evidence), "
            f"records (each with date, body, cause, URL), summary, sources. If something cannot be found "
            f"in public records, say so rather than guessing. Do not pause to ask questions.")


def write_review_queue(sus: pd.DataFrame, cand: pd.DataFrame, out_dir: Path, k: int = 60) -> tuple[Path, Path, int]:
    """The verification batch: up to k rows, suspects first by dollars, then candidates
    by prob x dollars. CSV for the operator, jsonl in the dossier's dispatch schema."""
    rows = []
    if len(sus):
        s = sus.copy()
        s["_rank"] = pd.to_numeric(s.get("net_paid", 0), errors="coerce").fillna(0.0)
        s["kind"] = "suspect_positive"
        rows.append(s.sort_values("_rank", ascending=False).head(k // 2 or 1))
    if len(cand):
        c = cand.copy()
        c["_rank"] = (pd.to_numeric(c.get("net_paid", 0), errors="coerce").fillna(0.0).clip(lower=1.0)
                      * pd.to_numeric(c["prob"], errors="coerce").fillna(0.0))
        c["kind"] = "candidate_positive"
        rows.append(c.sort_values("_rank", ascending=False).head(k - (len(rows[0]) if rows else 0)))
    q = (pd.concat(rows, ignore_index=True).drop(columns=["_rank"]) if rows
         else pd.DataFrame(columns=["npi", "kind"]))
    csv_p = out_dir / "label_review_queue.csv"
    q.to_csv(csv_p, index=False)
    jl = out_dir / "label_review_prompts.jsonl"
    with open(jl, "w", encoding="utf-8") as fh:
        for _, r in q.iterrows():
            fh.write(json.dumps({"org_id": f"npi:{r['npi']}:{r['kind']}",
                                 "org_name": str(r.get("provider_name") or r.get("org_legal_name") or ""),
                                 "kind": r["kind"], "npi": str(r["npi"]),
                                 "prompt": _label_review_prompt(r, r["kind"])}) + "\n")
    return csv_p, jl, int(len(q))


# --------------------------------------------------------------------------- #
# 3. feature drift
# --------------------------------------------------------------------------- #

def _psi(prev: np.ndarray, cur: np.ndarray, bins: int = 10) -> float:
    prev = prev[np.isfinite(prev)]
    cur = cur[np.isfinite(cur)]
    if len(prev) < 50 or len(cur) < 50:
        return float("nan")
    edges = np.unique(np.quantile(prev, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0 if np.array_equal(np.unique(prev), np.unique(cur)) else float("nan")
    edges[0], edges[-1] = -np.inf, np.inf
    pc = np.histogram(prev, edges)[0] / len(prev)
    cc = np.histogram(cur, edges)[0] / len(cur)
    eps = 1e-4
    pc, cc = np.clip(pc, eps, None), np.clip(cc, eps, None)
    return float(np.sum((cc - pc) * np.log(cc / pc)))


def _sample(path: Path, cols: list[str], n: int) -> pd.DataFrame:
    import duckdb
    sel = ", ".join(f'"{c}"' for c in cols)
    return duckdb.connect().execute(
        f"SELECT {sel} FROM read_parquet('{str(path).replace(chr(39), chr(39) * 2)}') "
        f"USING SAMPLE reservoir({int(n)} ROWS) REPEATABLE (42)").df()


def feature_drift(cur_path: Path, prev_path: Path, cols: list[str], n: int = 100_000) -> pd.DataFrame:
    """PSI + null-rate delta per column present in both matrices."""
    import pyarrow.parquet as pq
    have = set(pq.ParquetFile(cur_path).schema_arrow.names) & set(pq.ParquetFile(prev_path).schema_arrow.names)
    cols = [c for c in cols if c in have]
    if not cols:
        return pd.DataFrame(columns=["feature", "psi", "null_rate_prev", "null_rate_cur", "null_delta", "flag"])
    cur, prev = _sample(cur_path, cols, n), _sample(prev_path, cols, n)
    rows = []
    for c in cols:
        a = pd.to_numeric(prev[c], errors="coerce").to_numpy(dtype=float)
        b = pd.to_numeric(cur[c], errors="coerce").to_numpy(dtype=float)
        nr_p, nr_c = float(np.isnan(a).mean()), float(np.isnan(b).mean())
        psi = _psi(a, b)
        flag = ("FAIL" if psi == psi and psi >= PSI_FAIL else "WARN" if psi == psi and psi >= PSI_WARN
                else "WARN" if abs(nr_c - nr_p) >= 0.2 else "")
        rows.append({"feature": c, "psi": round(psi, 4) if psi == psi else None,
                     "null_rate_prev": round(nr_p, 4), "null_rate_cur": round(nr_c, 4),
                     "null_delta": round(nr_c - nr_p, 4), "flag": flag})
    out = pd.DataFrame(rows)
    return out.sort_values(["flag", "psi"], ascending=[False, False], na_position="last").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 4. near misses
# --------------------------------------------------------------------------- #

def near_misses(matrix: pd.DataFrame, oof: pd.DataFrame, ruler: pd.DataFrame, cols: list[str],
                top_frac: float = 0.01, n_gap: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Forward positives outside the top fraction, with the features where they sit
    furthest below the top set. Returns (misses, feature_counts)."""
    m = matrix.set_index(matrix["npi"].astype(str))
    s = pd.to_numeric(oof.set_index("npi")["score"], errors="coerce").reindex(m.index).fillna(0.0)
    r = ruler.copy()
    r["npi"] = r["npi"].astype(str)
    pos = set(r.loc[pd.to_numeric(r["is_prospective_positive"], errors="coerce").fillna(0) == 1, "npi"])
    known = set(r.loc[pd.to_numeric(r.get("was_excluded_pre_cutoff", 0), errors="coerce").fillna(0) == 1, "npi"]) \
        if "was_excluded_pre_cutoff" in r.columns else set()
    pos = (pos - known) & set(m.index)
    if not pos:
        return pd.DataFrame(), pd.DataFrame()
    rank = (-s).rank(method="min")
    k = max(1, int(round(len(m) * top_frac)))
    top = m.index[rank <= k]
    cols = [c for c in cols if c in m.columns and pd.api.types.is_numeric_dtype(m[c])]
    # plain float64 throughout: the matrix carries pandas nullable dtypes (Int64 /
    # Float64 / boolean) whose NA has no truth value (the 2026-10-10 v8 crash in
    # `mad.get(c, 0) and ...`)
    X_top = m.loc[top, cols].apply(pd.to_numeric, errors="coerce").astype("float64")
    med = X_top.median()
    mad = ((X_top - med).abs().median() * 1.4826).astype("float64")
    # a near-constant top set gives a MAD of 1e-15 and a z of minus 2.7e14 (v11,
    # rx_spread_coverage): the floor is a millionth of the column's scale, and the
    # z is clipped so one degenerate column can never dominate the gap list
    scale = X_top.abs().max().fillna(0.0)
    floor = (scale * 1e-6).clip(lower=1e-9)
    ok = [c for c in cols if c in mad.index and np.isfinite(mad[c]) and mad[c] > floor[c]]
    miss_idx = [n for n in m.index if n in pos and rank[n] > k]
    rows, counts = [], {}
    if miss_idx and ok:
        Xm = m.loc[miss_idx, ok].apply(pd.to_numeric, errors="coerce").astype("float64")
        Z = ((Xm - med[ok]) / mad[ok]).clip(-50.0, 50.0)
        for n in miss_idx:
            z = Z.loc[n].dropna().sort_values()
            gaps = z.head(n_gap)
            for c in gaps.index:
                counts[c] = counts.get(c, 0) + 1
            row = m.loc[n]
            rows.append({"npi": n, "rank": int(rank[n]), "score": float(s[n]),
                         "name": _text(row.get("provider_name")) or _text(row.get("org_legal_name")),
                         "state": _text(row.get("practice_state")),
                         "net_paid": _num(row.get("net_paid")),
                         "gap_features": "; ".join(f"{c} (z {v:+.1f})" for c, v in gaps.items())})
    misses = pd.DataFrame(rows).sort_values("rank").reset_index(drop=True) if rows else pd.DataFrame(
        columns=["npi", "rank", "score", "name", "state", "net_paid", "gap_features"])
    fc = (pd.DataFrame(sorted(counts.items(), key=lambda kv: -kv[1]), columns=["feature", "misses_below_top_on_it"])
          if counts else pd.DataFrame(columns=["feature", "misses_below_top_on_it"]))
    misses.attrs["n_forward_positives"] = len(pos)
    misses.attrs["top_k"] = k
    return misses, fc


# --------------------------------------------------------------------------- #
# 5. calibration monitor
# --------------------------------------------------------------------------- #

def calibration_monitor(oof: pd.DataFrame, y: np.ndarray, matrix_npis: pd.Series) -> tuple[dict, np.ndarray]:
    """ECE / Brier of the calibrated probabilities against the training label, and a
    conformal p-value per provider (positives' OOF scores as the calibration set)."""
    from .calibration import calibration_metrics
    from .conformal import conformal_pvalues
    o = oof.set_index("npi")
    idx = matrix_npis.astype(str)
    p = pd.to_numeric(o["prob"], errors="coerce").reindex(idx).fillna(0.0).to_numpy(dtype=float)
    s = pd.to_numeric(o["score"], errors="coerce").reindex(idx).fillna(0.0).to_numpy(dtype=float)
    y = np.asarray(y).astype(int)
    metrics = {}
    if y.sum() > 0 and (y == 0).sum() > 0:
        try:
            metrics = {k: (round(float(v), 6) if isinstance(v, (int, float, np.floating)) else v)
                       for k, v in calibration_metrics(p, y).items()}
        except Exception as e:                           # noqa: BLE001
            metrics = {"error": str(e)[:120]}
    conf = conformal_pvalues(s[y == 1], s) if y.sum() > 0 else np.full(len(s), np.nan)
    metrics["n_conformal_p_le_0.05"] = int(np.nansum(conf <= 0.05))
    return metrics, conf


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #

def _trainable_cols(manifest: dict, matrix: pd.DataFrame) -> list[str]:
    adj = set(manifest.get("leakage_adjacent") or []) | set(manifest.get("leakage_hard") or [])
    cols = (list(manifest.get("raw_feature_cols") or []) + list(manifest.get("peerpct_cols") or [])
            + list(manifest.get("subscore_cols") or []))
    seen, out = set(), []
    for c in cols:
        if c in matrix.columns and c not in adj and c not in seen and pd.api.types.is_numeric_dtype(matrix[c]):
            seen.add(c)
            out.append(c)
    return out


def to_markdown(tag: str, res: dict, drift: pd.DataFrame, misses: pd.DataFrame, fc: pd.DataFrame,
                sus: pd.DataFrame, cand: pd.DataFrame) -> str:
    ln = res["label_noise"]
    L = [f"# LEARNING LOOP: {tag}", "",
         "_What the model got wrong, what the labels got wrong, what moved under the data, and how sure "
         "the probabilities are. Every list here is a review queue or a diagnosis; nothing changed a label "
         "or a model. Leads for counsel review, never accusations._", "",
         "## 1. Label noise (confident learning)", "",
         f"- labelled positives {ln.get('n_pos', 0):,}, negatives {ln.get('n_neg', 0):,}; thresholds: mean probability of "
         f"positives {ln.get('t1')}, of negatives {ln.get('t0')}",
         f"- SUSPECT positives (label says excluded, billing says ordinary): {ln.get('n_suspect', 0):,} "
         f"({ln.get('suspect_share_of_positives', 0):.0%} of positives). The innocent explanations first: an "
         "administrative or derivative ban, an identity collision on the list, a scheme the features cannot see.",
         f"- CANDIDATE positives (no record, billing shaped like the enforced): {ln.get('n_candidate', 0):,}.",
         f"- review queue: {res['queue']['n']} rows -> label_review_queue.csv; prompts -> label_review_prompts.jsonl "
         "(dispatch with pursuit_dossier --dispatch-file, one dispatcher at a time)", ""]
    if len(sus):
        L += ["Top suspect positives by dollars:", "", "| NPI | name | st | $ paid | prob | label sources | basis |", "|---|---|---|--:|--:|---|---|"]
        for r in sus.sort_values("net_paid", ascending=False).head(15).itertuples() if "net_paid" in sus.columns else sus.head(15).itertuples():
            L.append(f"| {r.npi} | {(_text(getattr(r, 'provider_name', '')) or _text(getattr(r, 'org_legal_name', '')))[:36]} | "
                     f"{_text(getattr(r, 'practice_state', ''))} | ${_num(getattr(r, 'net_paid', 0))/1e6:,.1f}M | "
                     f"{r.prob:.4f} | {_text(getattr(r, 'exclusion_label_sources', ''))} | {_text(getattr(r, 'label_basis', ''))} |")
        L.append("")
    L += ["## 3. Feature drift vs the previous run", ""]
    if len(drift):
        flagged = drift[drift["flag"] != ""]
        L += [f"- {len(drift):,} features compared on a {res['drift']['sample']:,}-row sample; "
              f"{int((drift['flag'] == 'FAIL').sum())} with PSI >= {PSI_FAIL} (a data defect until explained), "
              f"{int((drift['flag'] == 'WARN').sum())} with PSI >= {PSI_WARN} or a null-rate move >= 20 points", ""]
        if len(flagged):
            L += ["| feature | PSI | null rate before | null rate now | flag |", "|---|--:|--:|--:|---|"]
            for r in flagged.head(40).itertuples():
                L.append(f"| `{r.feature}` | {r.psi if r.psi is not None else 'n/a'} | {r.null_rate_prev:.2f} | {r.null_rate_cur:.2f} | {r.flag} |")
            L.append("")
    else:
        L += ["- no previous run given (pass --prev-run-dir), or no shared trainable columns", ""]
    L += ["## 4. Near misses on the forward ruler", ""]
    if len(misses):
        L += [f"- {misses.attrs.get('n_forward_positives', 0):,} forward positives not known at the freeze; "
              f"{len(misses):,} ranked outside the top {misses.attrs.get('top_k', 0):,}; the median miss sits at rank "
              f"{int(misses['rank'].median()):,}", ""]
        if len(fc):
            L += ["Features the misses most often sit far below the top set on (the feature requests):", "",
                  "| feature | misses below the top set on it |", "|---|--:|"]
            L += [f"| `{r.feature}` | {r.misses_below_top_on_it} |" for r in fc.head(15).itertuples()]
            L.append("")
        L += ["| NPI | name | st | rank | $ paid | where it falls short |", "|---|---|---|--:|--:|---|"]
        for r in misses.head(25).itertuples():
            L.append(f"| {r.npi} | {str(r.name)[:36]} | {r.state} | {r.rank:,} | ${r.net_paid/1e6:,.1f}M | {r.gap_features} |")
        L.append("")
    else:
        L += ["- no forward ruler found, or every forward positive ranked inside the top fraction", ""]
    cm = res["calibration"]
    L += ["## 5. Calibration monitor", "",
          "- " + (", ".join(f"{k} {v}" for k, v in cm.items()) if cm else "no labelled rows to calibrate against"),
          "- conformal_p (per provider, in provider_oof_scores.parquet): the p-value that the provider's score "
          "is as high as a known positive's; a small value means the score is unusual even among positives.", "",
          "_Leads for counsel review, never accusations. A suspect label is a question about a record, not a finding about a person._"]
    return ("\n".join(L) + "\n").replace(" — ", ": ").replace("—", "-").replace("–", "-")


def run(run_dir: Path, prev_run_dir: Path | None, ruler_path: Path | None, top_frac: float,
        queue_size: int, sample_n: int, tag: str | None = None) -> dict:
    tag = tag or run_dir.name
    matrix = pd.read_parquet(run_dir / "provider_features_for_model.parquet")
    matrix["npi"] = matrix["npi"].astype(str)
    manifest = json.loads((run_dir / "feature_manifest.json").read_text(encoding="utf-8"))
    oof = load_or_score(run_dir, matrix, manifest)
    y = _training_y(matrix, manifest)
    cols = _trainable_cols(manifest, matrix)

    sus, cand, ln = label_noise(matrix, oof, y)
    sus.to_csv(run_dir / "label_noise_suspect_positives.csv", index=False)
    cand.to_csv(run_dir / "label_noise_candidate_positives.csv", index=False)
    csv_p, jl, nq = write_review_queue(sus, cand, run_dir, queue_size)

    drift = pd.DataFrame()
    if prev_run_dir and (Path(prev_run_dir) / "provider_features_for_model.parquet").exists():
        drift = feature_drift(run_dir / "provider_features_for_model.parquet",
                              Path(prev_run_dir) / "provider_features_for_model.parquet", cols, sample_n)
        drift.to_csv(run_dir / "feature_drift.csv", index=False)

    misses, fc = pd.DataFrame(), pd.DataFrame()
    if ruler_path is None:
        cutoff = str(manifest.get("asof_cutoff") or (manifest.get("graph_substrate") or {}).get("asof") or "")[:7]
        for cand_p in (run_dir / f"future_bans_after_{cutoff}__fca36.csv", run_dir / f"future_bans_after_{cutoff}.csv"):
            if cutoff and cand_p.exists():
                ruler_path = cand_p
                break
    if ruler_path and Path(ruler_path).exists():
        ruler = pd.read_csv(ruler_path, dtype=str)
        misses, fc = near_misses(matrix, oof, ruler, cols, top_frac)
        misses.to_csv(run_dir / "near_misses.csv", index=False)
        fc.to_csv(run_dir / "near_miss_feature_requests.csv", index=False)

    cm, conf = calibration_monitor(oof, y, matrix["npi"])
    o = oof.set_index("npi")
    o.loc[matrix["npi"].astype(str), "conformal_p"] = conf
    o.reset_index().to_parquet(run_dir / OOF_FILE, index=False)

    res = {"tag": tag, "label_noise": ln, "queue": {"n": nq, "csv": str(csv_p), "prompts": str(jl)},
           "drift": {"sample": int(sample_n), "n_features": int(len(drift)),
                     "n_fail": int((drift["flag"] == "FAIL").sum()) if len(drift) else 0,
                     "n_warn": int((drift["flag"] == "WARN").sum()) if len(drift) else 0,
                     "worst": drift.head(10).to_dict("records") if len(drift) else [],
                     "prev_run": str(prev_run_dir) if prev_run_dir else None},
           "near_misses": {"n_forward_positives": int(misses.attrs.get("n_forward_positives", 0)) if len(misses) else 0,
                           "n_missed": int(len(misses)), "top_k": int(misses.attrs.get("top_k", 0)) if len(misses) else 0,
                           "feature_requests": fc.head(15).to_dict("records") if len(fc) else [],
                           "ruler": str(ruler_path) if ruler_path else None},
           "calibration": cm}
    (run_dir / "learning_loop.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    (run_dir / "LEARNING_LOOP.md").write_text(to_markdown(tag, res, drift, misses, fc, sus, cand), encoding="utf-8")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--data-root", default=os.environ.get("MEDICAID_DATA_ROOT", str(Path.home() / "Desktop/data")))
    ap.add_argument("--prev-run-dir", default=None)
    ap.add_argument("--ruler", default=None, help="forward label csv (default: the run's __fca36 file, else the exclusion ruler)")
    ap.add_argument("--top-frac", type=float, default=0.01)
    ap.add_argument("--queue-size", type=int, default=60)
    ap.add_argument("--sample", type=int, default=100_000, help="rows sampled from each matrix for drift")
    ap.add_argument("--label-tier", default="all", choices=["all", "hard", "hard+weak"])
    ap.add_argument("--exclusion-nodes", default=None)
    ap.add_argument("--tag", default=None)
    a = ap.parse_args()
    from .run_registry import resolve_run_dir
    d = Path(resolve_run_dir(a.run_dir, a.data_root))
    if a.label_tier != "all":
        from .company_list import default_exclusion_nodes, set_label_policy
        ex = Path(a.exclusion_nodes) if a.exclusion_nodes else default_exclusion_nodes(d, a.data_root)
        mj = json.loads((d / "feature_manifest.json").read_text(encoding="utf-8"))
        set_label_policy(a.label_tier, ex, mj.get("asof_cutoff") or (mj.get("graph_substrate") or {}).get("asof"))
    res = run(d, Path(a.prev_run_dir) if a.prev_run_dir else None, Path(a.ruler) if a.ruler else None,
              a.top_frac, a.queue_size, a.sample, a.tag)
    ln, dr, nm = res["label_noise"], res["drift"], res["near_misses"]
    print(f"[learning_loop] {res['tag']}: {ln.get('n_suspect', 0):,} suspect positives, {ln.get('n_candidate', 0):,} "
          f"candidate positives, queue {res['queue']['n']}; drift FAIL {dr['n_fail']} / WARN {dr['n_warn']} over "
          f"{dr['n_features']} features; near misses {nm['n_missed']} of {nm['n_forward_positives']} forward positives; "
          f"calibration {res['calibration']} -> {d / 'LEARNING_LOOP.md'}")


if __name__ == "__main__":
    main()
