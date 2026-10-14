"""
label_enrichment.py — what the forward cases light up (the label-conditioned
enrichment report; docs/MODEL_QUESTIONS_2026-10-14.md §3).

The supervised scorer uses whatever pattern the label rewards, but it cannot say in
words what that pattern is, and a scheme nobody wrote has no subscore and no dossier
driver. This report turns the FORWARD positives (the FCA ruler: cases announced after
the freeze) into a design brief for new schemes: for every value of every dimension,
how over-represented it is among the positives versus the scored universe, with
counts and a hypergeometric tail probability so a lift on three providers is never
read as a finding.

Dimensions
  from the matrix   taxonomy, state, entity type, billing-size decile, tenure band,
                    distinct-code band, active-month band, org size band, the top
                    decile of every scheme subscore
  from the fact     HCPCS codes billed (share of the provider's gross >= 1 percent),
                    the provider's DOMINANT code, the code's SHARE band
                    (DuckDB over the as-of spending fact; the fact never enters pandas)

  python -m src.model_a.label_enrichment --run-dir <run> [--future-label <csv>]
      [--spending <parquet>] [--min-pos 5] [--top 25]

Writes LABEL_ENRICHMENT.md + label_enrichment.csv + label_enrichment.json in the run
folder. Read-only on its inputs. Lift is descriptive: it names where the cases
concentrate, never who is a case.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

MIN_POS_DEFAULT = 5
TOP_DEFAULT = 25
CODE_SHARE_FLOOR = 0.01

_SIZE_BINS = [0, 1e5, 5e5, 1e6, 5e6, 1e7, 5e7, float("inf")]
_SIZE_LABELS = ["<100k", "100k-500k", "500k-1M", "1M-5M", "5M-10M", "10M-50M", ">=50M"]
_TENURE_BINS = [-1, 12, 24, 48, 96, float("inf")]
_TENURE_LABELS = ["<=12 mo", "13-24 mo", "25-48 mo", "49-96 mo", ">96 mo"]
_CODES_BINS = [-1, 5, 15, 40, 100, float("inf")]
_CODES_LABELS = ["<=5 codes", "6-15", "16-40", "41-100", ">100"]
_MEMBERS_BINS = [-1, 1, 3, 10, 50, float("inf")]
_MEMBERS_LABELS = ["solo", "2-3", "4-10", "11-50", ">50"]


def _hyper_tail(k: int, n_pos: int, n_dim: int, n_univ: int) -> float:
    """P(X >= k) for X ~ Hypergeometric(N=n_univ, K=n_dim, n=n_pos): the chance that
    random positives would land on this value at least k times."""
    try:
        from scipy.stats import hypergeom
        return float(hypergeom.sf(k - 1, n_univ, n_dim, n_pos))
    except Exception:  # noqa: BLE001  (no scipy: normal approximation)
        if n_univ <= 0 or n_dim <= 0:
            return 1.0
        p = n_dim / n_univ
        mu, sd = n_pos * p, math.sqrt(max(n_pos * p * (1 - p), 1e-9))
        z = (k - 0.5 - mu) / sd
        return float(0.5 * math.erfc(z / math.sqrt(2)))


def enrich_dimension(values: pd.Series, pos: np.ndarray, keep: np.ndarray,
                     dimension: str, min_pos: int = MIN_POS_DEFAULT) -> pd.DataFrame:
    """One row per value of ``values`` (aligned to the matrix): positives, universe,
    shares, lift and the hypergeometric tail. Rows under ``min_pos`` positives are
    kept with ``supported = 0`` so the CSV is complete and the tables are honest."""
    v = values.astype("string").fillna("").str.strip()
    v = v.where(v != "", "(blank)")
    v = v[keep]
    p = pos[keep].astype(bool)
    n_univ, n_pos = int(len(v)), int(p.sum())
    if n_univ == 0 or n_pos == 0:
        return pd.DataFrame()
    cnt_u = v.value_counts()
    cnt_p = v[p].value_counts()
    rows = []
    for val, nu in cnt_u.items():
        k = int(cnt_p.get(val, 0))
        share_p, share_u = k / n_pos, nu / n_univ
        rows.append({"dimension": dimension, "value": str(val), "n_pos": k, "n_univ": int(nu),
                     "share_pos": round(share_p, 4), "share_univ": round(share_u, 4),
                     "lift": round(share_p / share_u, 2) if share_u > 0 else float("nan"),
                     "p_tail": _hyper_tail(k, n_pos, int(nu), n_univ) if k else 1.0,
                     "supported": int(k >= min_pos)})
    out = pd.DataFrame(rows)
    return out.sort_values(["supported", "lift", "n_pos"], ascending=[False, False, False]).reset_index(drop=True)


def _band(s: pd.Series, bins, labels) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    return pd.cut(x, bins=bins, labels=labels).astype("string").fillna("(unknown)")


def matrix_dimensions(matrix: pd.DataFrame, manifest: dict | None = None) -> dict[str, pd.Series]:
    """The matrix-side dimensions available on this matrix (skip-missing)."""
    dims: dict[str, pd.Series] = {}
    for col, name in (("primary_taxonomy", "taxonomy"), ("practice_state", "state"),
                      ("addr_state", "state"), ("entity_type", "entity_type"),
                      ("billing_implied_taxonomy", "billing_implied_taxonomy")):
        if col in matrix.columns and name not in dims:
            dims[name] = matrix[col]
    if "gross_paid" in matrix.columns:
        dims["billing_size"] = _band(matrix["gross_paid"], _SIZE_BINS, _SIZE_LABELS)
    if "tenure_months" in matrix.columns:
        dims["tenure"] = _band(matrix["tenure_months"], _TENURE_BINS, _TENURE_LABELS)
    if "n_distinct_hcpcs" in matrix.columns:
        dims["distinct_codes"] = _band(matrix["n_distinct_hcpcs"], _CODES_BINS, _CODES_LABELS)
    if "n_active_months" in matrix.columns:
        dims["active_months"] = _band(matrix["n_active_months"], _TENURE_BINS, _TENURE_LABELS)
    if "org_member_count" in matrix.columns:
        dims["org_size"] = _band(matrix["org_member_count"], _MEMBERS_BINS, _MEMBERS_LABELS)
    subs = (manifest or {}).get("subscore_cols") or [c for c in matrix.columns if c.startswith("subscore_")]
    for c in subs:
        if c not in matrix.columns:
            continue
        x = pd.to_numeric(matrix[c], errors="coerce")
        if x.notna().sum() < 100:
            continue
        q90 = x.quantile(0.9)
        top = pd.Series(np.where(x.isna(), "(not scored)", np.where(x >= q90, "top decile", "rest")),
                        index=matrix.index)
        dims[c.replace("subscore_", "scheme:")] = top
    return dims


def code_dimensions(spending_path: str | Path, npis: pd.Series, cutoff: str | None = None,
                    con=None) -> dict[str, pd.DataFrame]:
    """Per-NPI code facts from the spending fact: (npi, hcpcs) pairs where the code
    carries >= 1 percent of the provider's gross; the dominant code; the dominant
    code's share band. Returns {'codes': long frame, 'dominant': per-NPI frame}."""
    own = con is None
    if own:
        from src.attempt_2.duck import connect
        con = connect()
    try:
        p = str(spending_path).replace("'", "''")
        cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()]
        if "hcpcs_code" not in cols:
            return {"codes": pd.DataFrame(columns=["npi", "hcpcs", "share"]),
                    "dominant": pd.DataFrame(columns=["npi", "dominant_code", "dominant_share"])}
        cut = ""
        if cutoff:
            cut = f" AND substr(CAST(service_month AS VARCHAR), 1, 7) < '{str(cutoff)[:7]}'"
        con.register("le_npis", pd.DataFrame({"npi": npis.astype(str).unique()}))
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE le_codes AS
            WITH g AS (
                SELECT CAST(billing_npi AS VARCHAR) npi, UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) hcpcs,
                       SUM(CASE WHEN CAST(total_paid AS DOUBLE) > 0 THEN CAST(total_paid AS DOUBLE) ELSE 0 END) gross
                FROM read_parquet('{p}')
                WHERE CAST(total_paid AS DOUBLE) <= 500000000{cut}
                GROUP BY 1, 2),
            t AS (SELECT npi, SUM(gross) tot FROM g GROUP BY 1)
            SELECT g.npi, g.hcpcs, g.gross / NULLIF(t.tot, 0) AS share
            FROM g JOIN t USING (npi) JOIN le_npis USING (npi)
            WHERE t.tot > 0""")
        codes = con.execute(f"SELECT npi, hcpcs, share FROM le_codes WHERE share >= {CODE_SHARE_FLOOR}").df()
        dom = con.execute("""
            SELECT npi, arg_max(hcpcs, share) AS dominant_code, MAX(share) AS dominant_share
            FROM le_codes GROUP BY npi""").df()
        con.unregister("le_npis")
    finally:
        if own:
            con.close()
    for d in (codes, dom):
        d["npi"] = d["npi"].astype(str)
    return {"codes": codes, "dominant": dom}


def enrich_codes(codes: pd.DataFrame, matrix_npi: pd.Series, pos: np.ndarray, keep: np.ndarray,
                 min_pos: int = MIN_POS_DEFAULT) -> pd.DataFrame:
    """Per HCPCS code: providers billing it (>= 1 percent of gross) among the forward
    positives vs the universe."""
    npi = matrix_npi.astype(str)
    univ = set(npi[keep])
    posset = set(npi[keep][pos[keep].astype(bool)])
    n_univ, n_pos = len(univ), len(posset)
    if not n_pos or not len(codes):
        return pd.DataFrame()
    c = codes[codes["npi"].isin(univ)]
    per_code_u = c.groupby("hcpcs")["npi"].nunique()
    per_code_p = c[c["npi"].isin(posset)].groupby("hcpcs")["npi"].nunique()
    rows = []
    for code, nu in per_code_u.items():
        k = int(per_code_p.get(code, 0))
        share_p, share_u = k / n_pos, nu / n_univ
        rows.append({"dimension": "hcpcs_code", "value": str(code), "n_pos": k, "n_univ": int(nu),
                     "share_pos": round(share_p, 4), "share_univ": round(share_u, 4),
                     "lift": round(share_p / share_u, 2) if share_u > 0 else float("nan"),
                     "p_tail": _hyper_tail(k, n_pos, int(nu), n_univ) if k else 1.0,
                     "supported": int(k >= min_pos)})
    out = pd.DataFrame(rows)
    return out.sort_values(["supported", "lift", "n_pos"], ascending=[False, False, False]).reset_index(drop=True)


def build_report(matrix: pd.DataFrame, forward: pd.DataFrame, manifest: dict | None = None,
                 spending_path: str | Path | None = None, cutoff: str | None = None,
                 min_pos: int = MIN_POS_DEFAULT, log=print) -> tuple[pd.DataFrame, dict]:
    from .scheme_health import forward_label_vector
    pos, keep = forward_label_vector(matrix, forward)
    if pos is None:
        raise ValueError("the forward label needs npi + is_prospective_positive columns")
    n_pos_u = int(pos[keep].sum())
    info = {"n_universe": int(keep.sum()), "n_forward_positives": n_pos_u,
            "n_known_at_freeze_dropped": int((~keep).sum()), "min_pos": min_pos,
            "dimensions": []}
    log(f"[label_enrichment] {n_pos_u:,} forward positives in a {int(keep.sum()):,}-provider "
        f"universe ({int((~keep).sum()):,} known-at-freeze rows dropped)")
    frames = []
    for name, series in matrix_dimensions(matrix, manifest).items():
        t = enrich_dimension(series, pos, keep, name, min_pos=min_pos)
        if len(t):
            frames.append(t)
            info["dimensions"].append(name)
    if spending_path and Path(spending_path).exists():
        cd = code_dimensions(spending_path, matrix["npi"], cutoff=cutoff)
        t = enrich_codes(cd["codes"], matrix["npi"], pos, keep, min_pos=min_pos)
        if len(t):
            frames.append(t)
            info["dimensions"].append("hcpcs_code")
        dom = cd["dominant"]
        if len(dom):
            m = matrix[["npi"]].copy()
            m["npi"] = m["npi"].astype(str)
            m = m.merge(dom, on="npi", how="left")
            t = enrich_dimension(m["dominant_code"], pos, keep, "dominant_code", min_pos=min_pos)
            if len(t):
                frames.append(t)
                info["dimensions"].append("dominant_code")
            band = pd.cut(pd.to_numeric(m["dominant_share"], errors="coerce"),
                          bins=[-0.01, 0.25, 0.5, 0.75, 0.9, 1.0],
                          labels=["<=25%", "25-50%", "50-75%", "75-90%", ">90%"]).astype("string")
            t = enrich_dimension(band, pos, keep, "dominant_code_share", min_pos=min_pos)
            if len(t):
                frames.append(t)
                info["dimensions"].append("dominant_code_share")
        info["spending"] = str(spending_path)
    else:
        info["spending"] = None
        log("[label_enrichment] no spending fact given / found: code dimensions skipped")
    table = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return table, info


def to_markdown(table: pd.DataFrame, info: dict, top: int = TOP_DEFAULT) -> str:
    L = ["# LABEL_ENRICHMENT — what the forward cases light up", ""]
    L.append(f"_{info['n_forward_positives']:,} forward positives (the FCA ruler) in a "
             f"{info['n_universe']:,}-provider universe; {info['n_known_at_freeze_dropped']:,} "
             f"providers known at the freeze dropped. lift = share among positives / share in "
             f"the universe; p = the hypergeometric chance that random positives land on the value "
             f"at least this often; rows under {info['min_pos']} positives are listed in the CSV but "
             "not here. A lift names where the cases concentrate (a scheme to design), never who "
             "is a case. Leads for counsel review, never accusations._")
    if not len(table):
        L.append("\n_no dimension had positives_")
        return "\n".join(L)
    # the headline: the strongest supported rows across every dimension
    sup = table[(table["supported"] == 1) & (table["p_tail"] < 0.01)]
    sup = sup[~sup["value"].isin(["(blank)", "(unknown)", "(not scored)", "rest"])]
    L.append("")
    L.append("## Headline: the strongest enrichments (p < 0.01, supported)")
    L.append("")
    L.append("| dimension | value | positives | universe | share pos | share univ | lift | p |")
    L.append("|---|---|--:|--:|--:|--:|--:|--:|")
    for r in sup.sort_values(["lift", "n_pos"], ascending=[False, False]).head(top).itertuples():
        L.append(f"| {r.dimension} | {r.value} | {r.n_pos:,} | {r.n_univ:,} | {r.share_pos:.1%} | "
                 f"{r.share_univ:.1%} | {r.lift:.2f} | {r.p_tail:.1e} |")
    for dim in info["dimensions"]:
        t = table[(table["dimension"] == dim) & (table["supported"] == 1)]
        if not len(t):
            continue
        L.append("")
        L.append(f"## {dim}")
        L.append("")
        L.append("| value | positives | universe | share pos | share univ | lift | p |")
        L.append("|---|--:|--:|--:|--:|--:|--:|")
        for r in t.sort_values(["lift", "n_pos"], ascending=[False, False]).head(top).itertuples():
            L.append(f"| {r.value} | {r.n_pos:,} | {r.n_univ:,} | {r.share_pos:.1%} | "
                     f"{r.share_univ:.1%} | {r.lift:.2f} | {r.p_tail:.1e} |")
    L.append("")
    L.append("## How to read it into a scheme")
    L.append("")
    L.append("- A code or dominant code with lift > 2 and p < 0.01 that no scheme's weight table "
             "names is a candidate input (scheme_discovery lists the files that carry it).")
    L.append("- A taxonomy or state with high lift is a peer-group or coverage question first "
             "(are the positives there because fraud is, or because enforcement is).")
    L.append("- A scheme subscore whose top decile reads lift near 1 on the forward ruler is not "
             "separating the forward cases, whatever its exclusion-label AUC says.")
    L.append("- Size and tenure bands say which shape the cases take (the owner-run operator "
             "band vs the institutional band); the pursuit book's filters should match.")
    return "\n".join(L)


def run(run_dir: Path, future_label: Path | None, spending: Path | None,
        out: Path | None, min_pos: int, top: int) -> Path:
    run_dir = Path(run_dir)
    matrix = pd.read_parquet(run_dir / "provider_features_for_model.parquet")
    manifest = json.loads((run_dir / "feature_manifest.json").read_text(encoding="utf-8"))
    cutoff = manifest.get("asof_cutoff") or (manifest.get("graph_substrate") or {}).get("asof")
    if future_label is None:
        for cand in (run_dir / f"future_bans_after_{cutoff}__fca36.csv",
                     run_dir / f"future_bans_after_{cutoff}.csv"):
            if cutoff and cand.exists():
                future_label = cand
                break
    if future_label is None or not Path(future_label).exists():
        raise SystemExit("no forward label: pass --future-label <future_bans_after_<cut>__fca36.csv>")
    fl = pd.read_csv(future_label, dtype=str)
    if spending is None and cutoff:
        root = run_dir.parent.parent
        for cand in (root / "interim" / f"spending_asof_{cutoff}.parquet",
                     root / "processed" / "spending_fact.parquet"):
            if cand.exists():
                spending = cand
                break
    table, info = build_report(matrix, fl, manifest, spending_path=spending, cutoff=cutoff,
                               min_pos=min_pos)
    info["future_label"] = str(future_label)
    out = Path(out) if out else run_dir / "LABEL_ENRICHMENT.md"
    out.write_text(to_markdown(table, info, top=top), encoding="utf-8")
    table.to_csv(out.with_name("label_enrichment.csv"), index=False)
    out.with_name("label_enrichment.json").write_text(json.dumps(info, indent=2, default=str),
                                                      encoding="utf-8")
    print(f"[label_enrichment] {len(table):,} rows over {len(info['dimensions'])} dimensions -> {out}")
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--future-label", default=None)
    ap.add_argument("--spending", default=None,
                    help="spending parquet for the code dimensions (default: the run's as-of fact)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-pos", type=int, default=MIN_POS_DEFAULT)
    ap.add_argument("--top", type=int, default=TOP_DEFAULT)
    a = ap.parse_args()
    run(Path(a.run_dir), Path(a.future_label) if a.future_label else None,
        Path(a.spending) if a.spending else None, Path(a.out) if a.out else None,
        a.min_pos, a.top)


if __name__ == "__main__":
    main()
