"""field_ruler.py — the HELD-OUT field-outcome ruler (batch 5b-F, 2026-11-02; the
architecture audit's experiment 6, the evaluation read's section 2).

THE ONE RULER THAT SEES THE OWNER-RUN CLASS at the rate the business cares about. Every
public ruler is somebody else's case: federal, mostly institutional, resolved years after
the conduct. A field outcome (enforcement.field_outcomes: a count at the door over the
licence, a yard with no vans, a contradiction between two filings) is an outcome on OUR
provider, in OUR segment, dated by the day it was established. Small numbers, perfect
fit. Two gates make it a ruler instead of a label the model already saw:

  THE DATE GATE     a row counts only when its date is strictly AFTER the list it is
                    graded against was built (``--list-date``; a run folder's registry
                    timestamp when ``--run-dir`` is given): a visit that the list
                    prompted is a forward outcome, a visit before the list is not.
  THE TRAINING GATE the module REFUSES any NPI that is already a training positive in
                    the matrix it is graded against (``--matrix``: provider_on_exclusion
                    = 1, or ``field`` among exclusion_label_sources): a result the model
                    trained on is never the ruler it is graded on. The diagnostics'
                    eval_frame drops training positives too; this gate is the written
                    one, with the count.

Positive = outcome in {over_capacity, no_fleet, confirmed, contradiction}, dated by the
visit; cleared = an explicit negative row (is_prospective_positive 0); inconclusive is
never a positive and is left out (a visit that settled nothing says nothing). An org_id
resolves to its member NPIs through the graph's npi_to_org, the same way
field_outcomes.to_exclusion_rows does; one row per NPI, the EARLIEST adverse date.

The file is written in the EXACT schema prospective_label writes and
accuracy_diagnostics.load_ruler reads (npi, first_excl_date, excl_type, entity_name,
is_prospective_positive, was_excluded_pre_cutoff, undated_exclusion, fca_tier,
fca_basis, label_policy) under the name _discover_rulers picks up:
future_bans_after_<cut>__field<H>.csv -> ruler key ``field`` -> KPIs acc_*_field. The
horizon is part of the NAME only; eval_frame applies it, so a visit dated beyond
cutoff + horizon is reported here and dropped there (pass a horizon that reaches the
visits: a 2023-12 freeze graded on 2026 visits needs about 36).

    python -m src.model_a.field_ruler --data-root D --cutoff 2023-12 --horizon 33 \
        --list-date 2026-10-28 --out <run_dir>/future_bans_after_2023-12__field33.csv \
        [--matrix <run_dir>/provider_features_for_model.parquet] [--graph-dir D/graph]
    python -m src.model_a.field_ruler --data-root D --cutoff 2023-12 --horizon 33 --run-dir <run_dir> \
        [--matrix ...]      # list date = the run's registry timestamp; out = <run_dir>/future_bans_after_<cut>__field<H>.csv

INTEGRATION (this module edits no existing file):
  ops/vm/run_frozen_night.sh / regrade_from.sh, step 3 (the rulers), after the fca /
  aff / quitam files:
      python -m src.model_a.field_ruler --data-root "$DATA_ROOT" --cutoff "$CUT" --horizon "$HORIZON" \
          --run-dir "$RUN" --matrix "$RUN/provider_features_for_model.parquet" --graph-dir "$GRAPH_FULL"
  accuracy_diagnostics discovers the file by name (no change needed); run_registry reads
  acc_org_hits50_field etc. from accuracy_diagnostics.json once the key exists; a
  pre-registration may hold `field_hits50 >= 1` only after an alias is added to
  run_registry's alias table (`field_hits50 -> acc_org_hits50_field`).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.enforcement.field_outcomes import ADVERSE, OUTCOMES, load_field_outcomes, to_exclusion_rows

RULER_COLS = ["npi", "first_excl_date", "excl_type", "entity_name", "is_prospective_positive",
              "was_excluded_pre_cutoff", "undated_exclusion", "fca_tier", "fca_basis", "label_policy"]
LABEL_POLICY = "field"
RULER_KEY = "field"


def ruler_filename(cutoff: str, horizon: int | None) -> str:
    """future_bans_after_<cut>__field<H>.csv (the name _discover_rulers reads as key 'field')."""
    return f"future_bans_after_{str(cutoff)[:7]}__{RULER_KEY}{int(horizon) if horizon else ''}.csv"


def _cutoff_ts(cutoff: str) -> pd.Timestamp:
    s = str(cutoff).strip()
    return pd.Timestamp(s[:7] + "-01" if len(s) == 7 else s)


def trained_on_npis(matrix: pd.DataFrame) -> set[str]:
    """The NPIs the matrix's TRAINING label calls positive (provider_on_exclusion = 1), plus
    any NPI whose label provenance carries a field source: a field result the export folded
    into the label was trained on whatever the flag says."""
    npis: set[str] = set()
    if matrix is None or not len(matrix) or "npi" not in matrix.columns:
        return npis
    n = matrix["npi"].astype(str)
    if "provider_on_exclusion" in matrix.columns:
        pos = pd.to_numeric(matrix["provider_on_exclusion"], errors="coerce").fillna(0).astype(int).eq(1)
        npis |= set(n[pos])
    if "exclusion_label_sources" in matrix.columns:
        src = matrix["exclusion_label_sources"].fillna("").astype(str).str.lower()
        npis |= set(n[src.str.contains(r"(?:^|;)field", regex=True)])
    return npis


def read_matrix_labels(path: str | Path) -> pd.DataFrame:
    """Only the label columns of a matrix parquet (never the feature block)."""
    import pyarrow.parquet as pq
    p = Path(path)
    names = set(pq.ParquetFile(p).schema_arrow.names)
    cols = [c for c in ("npi", "provider_on_exclusion", "exclusion_label_sources") if c in names]
    if "npi" not in cols:
        raise ValueError(f"{p} carries no npi column")
    return pd.read_parquet(p, columns=cols)


def list_date_from_registry(data_root: str | Path, run_dir: str | Path) -> pd.Timestamp | None:
    """The run's FIRST registry timestamp (model_a/run_registry.jsonl, matched on run_dir or
    tag), else the matrix file's mtime: the day the list was built."""
    root, d = Path(data_root), Path(run_dir)
    reg = root / "model_a" / "run_registry.jsonl"
    stamps = []
    if reg.exists():
        want = {str(d.resolve()), str(d), d.name}
        for line in reg.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if str(row.get("run_dir", "")) in want or str(row.get("tag", "")) == d.name:
                ts = pd.to_datetime(row.get("registered_at"), errors="coerce", utc=True)
                if pd.notna(ts):
                    stamps.append(ts)
    if stamps:
        return min(stamps).tz_convert(None)
    mtx = d / "provider_features_for_model.parquet"
    if mtx.exists():
        return pd.Timestamp(datetime.fromtimestamp(mtx.stat().st_mtime, tz=timezone.utc)).tz_convert(None)
    return None


def build_field_ruler(outcomes: pd.DataFrame, cutoff: str, list_date, horizon_months: int | None = None,
                      npi_to_org: pd.DataFrame | None = None, provider_dim: pd.DataFrame | None = None,
                      trained_on: set[str] | None = None) -> tuple[pd.DataFrame, dict]:
    """(ruler in RULER_COLS, counts). ``outcomes`` is load_field_outcomes' frame; ``list_date``
    the day the graded list was built (rows on/before it are refused); ``trained_on`` the
    NPIs refused as training positives."""
    counts = {"rows": int(len(outcomes)), "resolved_npi_rows": 0, "unresolved": 0, "refused_by_date": 0,
              "refused_trained_on": 0, "inconclusive_dropped": 0, "positives": 0, "negatives_cleared": 0,
              "known_pre_cutoff": 0, "beyond_horizon": 0, "npis": 0, "list_date": str(pd.Timestamp(list_date).date()),
              "cutoff": str(cutoff)[:7], "horizon_months": horizon_months}
    empty = pd.DataFrame(columns=RULER_COLS)
    if outcomes is None or not len(outcomes):
        return empty, counts
    bad = ~outcomes["outcome"].isin(OUTCOMES)
    if bad.any():
        raise ValueError(f"unknown outcome(s) {sorted(outcomes.loc[bad, 'outcome'].unique())}")
    ld = pd.Timestamp(list_date)
    cut = _cutoff_ts(cutoff)
    rows, residue = to_exclusion_rows(outcomes, npi_to_org, provider_dim)
    counts["resolved_npi_rows"], counts["unresolved"] = int(len(rows)), int(len(residue))
    counts["residue"] = residue.to_dict("records") if len(residue) else []
    if not len(rows):
        return empty, counts
    r = rows.copy()
    r["outcome"] = r["excl_type"].str.replace("field:", "", regex=False)
    r["date"] = pd.to_datetime(r["excl_date"], errors="coerce")
    # the date gate: strictly after the list was built
    late = r["date"] > ld
    counts["refused_by_date"] = int((~late).sum())
    r = r[late]
    # the training gate: never the label the model trained on
    trained = set(trained_on or ())
    on = r["npi"].astype(str).isin(trained)
    counts["refused_trained_on"] = int(on.sum())
    r = r[~on]
    inc = r["outcome"] == "inconclusive"
    counts["inconclusive_dropped"] = int(inc.sum())
    r = r[~inc]
    if not len(r):
        return empty, counts
    r["adverse"] = r["outcome"].isin(ADVERSE)
    # one row per NPI: the earliest adverse outcome wins over any cleared row
    r = r.sort_values(["npi", "adverse", "date"], ascending=[True, False, True])
    first = r.drop_duplicates("npi", keep="first")
    pos = first["adverse"] & (first["date"] > cut)
    known = first["adverse"] & (first["date"] <= cut)
    out = pd.DataFrame({
        "npi": first["npi"].astype(str).to_numpy(),
        "first_excl_date": first["date"].dt.strftime("%Y-%m-%d").to_numpy(),
        "excl_type": first["excl_type"].to_numpy(),
        "entity_name": first["entity_name"].fillna("").to_numpy(),
        "is_prospective_positive": pos.astype(int).to_numpy(),
        "was_excluded_pre_cutoff": known.astype(int).to_numpy(),
        "undated_exclusion": 0,
        "fca_tier": first["adverse"].map({True: "hard", False: "excluded"}).to_numpy(),
        "fca_basis": first["excl_type"].to_numpy(),
        "label_policy": LABEL_POLICY,
    })
    counts["positives"] = int(out["is_prospective_positive"].sum())
    counts["negatives_cleared"] = int((~first["adverse"]).sum())
    counts["known_pre_cutoff"] = int(known.sum())
    counts["npis"] = int(len(out))
    if horizon_months:
        end = cut + pd.DateOffset(months=int(horizon_months))
        counts["beyond_horizon"] = int((pos & (first["date"] > end)).sum())
    return out.sort_values(["is_prospective_positive", "first_excl_date"], ascending=[False, True]).reset_index(drop=True), counts


def report(counts: dict, out: Path) -> str:
    residue = counts.get("residue") or []
    L = ["# FIELD RULER", "",
         f"_The held-out field-outcome ruler at cutoff {counts['cutoff']} (horizon {counts.get('horizon_months') or '-'} months): "
         f"field outcomes dated strictly after the list date {counts['list_date']}, never an NPI the matrix trained on. "
         f"Positive = over_capacity / no_fleet / confirmed / contradiction; cleared = an explicit negative; inconclusive "
         f"left out. Written to {out.name} (ruler key `{RULER_KEY}`)._", "",
         "| count | n |", "|---|--:|"]
    for k in ("rows", "resolved_npi_rows", "unresolved", "refused_by_date", "refused_trained_on", "inconclusive_dropped",
              "npis", "positives", "negatives_cleared", "known_pre_cutoff", "beyond_horizon"):
        L.append(f"| {k} | {counts.get(k, 0)} |")
    if counts.get("beyond_horizon"):
        L += ["", f"_{counts['beyond_horizon']} positive(s) dated beyond cutoff + horizon: eval_frame will drop them; "
                  "pass a longer --horizon (the horizon is part of the file name only)._"]
    if residue:
        L += ["", "## Residue (not resolved to an NPI)", "", "| org_id | npi | outcome | reason |", "|---|---|---|---|"]
        L += [f"| {r.get('org_id', '')} | {r.get('npi', '')} | {r.get('outcome', '')} | {r.get('drop_reason', '')} |"
              for r in residue]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--cutoff", required=True, help="YYYY-MM, the graded run's freeze")
    ap.add_argument("--horizon", type=int, default=None, help="months; part of the file name (eval_frame applies it)")
    ap.add_argument("--list-date", default=None,
                    help="YYYY-MM-DD: the day the graded list was built; rows on/before it are refused "
                         "(default: the run's registry timestamp when --run-dir is given, else required)")
    ap.add_argument("--run-dir", default=None, help="the run folder: default list date and default --out")
    ap.add_argument("--matrix", default=None,
                    help="provider_features_for_model.parquet: NPIs it trained on as positives are refused "
                         "(default <run_dir>/provider_features_for_model.parquet when --run-dir is given)")
    ap.add_argument("--in", dest="inp", default=None, help="default <data_root>/enforcement/field_outcomes.csv")
    ap.add_argument("--graph-dir", default=None, help="graph root with npi_to_org.parquet (default <data_root>/graph)")
    ap.add_argument("--out", default=None, help="default <run_dir>/future_bans_after_<cut>__field<H>.csv")
    a = ap.parse_args()
    root = Path(a.data_root)
    run_dir = Path(a.run_dir) if a.run_dir else None
    if a.list_date:
        list_date = pd.Timestamp(a.list_date)
    elif run_dir is not None:
        list_date = list_date_from_registry(root, run_dir)
        if list_date is None:
            ap.error(f"no registry timestamp or matrix for {run_dir}: pass --list-date")
        print(f"[field_ruler] list date {list_date.date()} from the run's registry / matrix")
    else:
        ap.error("--list-date is required without --run-dir (the day the graded list was built)")
    out = Path(a.out) if a.out else (run_dir / ruler_filename(a.cutoff, a.horizon) if run_dir else None)
    if out is None:
        ap.error("--out is required without --run-dir")
    src = Path(a.inp) if a.inp else root / "enforcement" / "field_outcomes.csv"
    outcomes = load_field_outcomes(src)
    g = Path(a.graph_dir) if a.graph_dir else root / "graph"
    n2o = pd.read_parquet(g / "npi_to_org.parquet") if (g / "npi_to_org.parquet").exists() else None
    pdim_p = root / "processed" / "provider_dim.parquet"
    pdim = pd.read_parquet(pdim_p) if pdim_p.exists() else None
    mtx_p = Path(a.matrix) if a.matrix else (run_dir / "provider_features_for_model.parquet" if run_dir else None)
    trained: set[str] = set()
    if mtx_p is not None and mtx_p.exists():
        trained = trained_on_npis(read_matrix_labels(mtx_p))
        print(f"[field_ruler] {len(trained):,} training positives in {mtx_p.name} will be refused")
    elif mtx_p is not None:
        print(f"[field_ruler] WARNING: matrix {mtx_p} not found; no training-positive refusal applied")
    ruler, counts = build_field_ruler(outcomes, a.cutoff, list_date, horizon_months=a.horizon,
                                      npi_to_org=n2o, provider_dim=pdim, trained_on=trained)
    out.parent.mkdir(parents=True, exist_ok=True)
    ruler.to_csv(out, index=False)
    rep = out.with_name(out.name[:-4] + ".FIELD.md")
    rep.write_text(report(counts, out), encoding="utf-8")
    print(f"[field_ruler] {counts['rows']} outcome rows -> {counts['npis']} NPIs: {counts['positives']} positives, "
          f"{counts['negatives_cleared']} cleared negatives; refused {counts['refused_trained_on']} as trained-on, "
          f"{counts['refused_by_date']} on/before the list date {counts['list_date']}, {counts['inconclusive_dropped']} "
          f"inconclusive, {counts['unresolved']} unresolved"
          + (f"; {counts['beyond_horizon']} beyond the horizon" if counts.get("beyond_horizon") else "")
          + f" -> {out}")


if __name__ == "__main__":
    main()
