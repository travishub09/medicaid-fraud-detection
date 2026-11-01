"""scheme_alternatives.py — the same scheme, calculated another way, graded side by side (2026-11-01).

THE OPERATOR'S SUSPICION, made a measurement: "we have the wrong data or wired certain things
in wrong; calculate the same scheme a different type of way". The theory screens found the
labs, the day cares and the transport cases that the subscores ranked nowhere, and the
screens are the same schemes rewritten as a NUMERATOR over a DENOMINATOR plus an intensity
per patient-month on the Medicaid fact itself. So every weak scheme gets its alternatives
computed as extra columns from the same fact (DuckDB, the fact never enters pandas) and
graded beside the current subscore on every ruler the run carries: the FCA ruler, the state
ruler, and the scheme-TYPED ruler (the cases of its own conduct type). The better form is
the one that separates the cases it was written for. Nothing changes by opinion: a formula
replaces a subscore only through a pre-registered run.

Alternative classes (each a one-line formula below):
  ratio      numerator lines over denominator lines (the screens' reflex form)
  intensity  numerator lines per patient-month (the screens' intensity form)
  share      numerator dollars over all dollars
  peak       the peak month's lines per calendar day (the capacity form)
  ramp       the largest three-month rise in paid over the prior three months (the ramp form)
  peer       the current subscore's one-sided percentile within state x taxonomy (the peer
             form: a state's rule is the peer group, not the nation)

    python -m src.model_a.scheme_alternatives --run-dir <run> --spending <as-of fact> [--cutoff 2023-12]
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.ingest_cms.sector_schemes import (EM_CODES, HH_CODES, LAB_COMPONENT_CODES, LAB_DRAW_CODES,
                                           PROLONGED_CODES, PSY_60_CODES, PSY_ALL_CODES)
from src.model_a.scheme_health import FCA_MIN_POS, _label_auc, fca_verdict, forward_label_vector, load_typed_rulers, scheme_case_type

EM_HIGH = ("99204", "99205", "99214", "99215")
DEF_DRUG = ("G0480", "G0481", "G0482", "G0483", "G0659")
PRES_DRUG = ("80305", "80306", "80307")
PC_CODES = ("T1019", "T1020", "S5125", "S5126", "S5130", "S5131", "T1004", "T1005", "G0156")
BH_PERDIEM = ("H0017", "H0018", "H0019", "H2013", "H2020", "H2034", "H2036", "H0035", "S9480", "S5102", "T2016")
BH_UNITS = ("H2014", "H2015", "H2017", "H2019", "H0036", "H0038", "H2011", "H2012")
NEMT = ("T2001", "T2002", "T2003", "T2004", "T2005", "A0080", "A0090", "A0100", "A0110", "A0120", "A0130", "A0140")
DME_PREFIX = ("E", "K0", "L")


@dataclass(frozen=True)
class Alt:
    scheme: str
    name: str
    kind: str                          # ratio | intensity | share | peak | ramp | peer
    numerator: tuple = ()              # codes (exact) for the SQL forms
    denominator: tuple = ()
    num_prefix: tuple = ()             # LIKE prefixes for the numerator
    why: str = ""


ALTERNATIVES: tuple[Alt, ...] = (
    # upcoding: the current subscore reads the Medicare physician file (individuals); the Medicaid fact says it per biller
    Alt("upcoding", "em_high_share_mcd", "ratio", EM_HIGH, EM_CODES, why="high-level E/M lines over all office E/M lines on the Medicaid fact (the Medicare file scores individuals)"),
    Alt("upcoding", "em_lines_per_pm", "intensity", tuple(EM_CODES), why="office E/M lines per patient-month"),
    Alt("upcoding", "peer_state_tax", "peer", why="the current subscore within state x taxonomy"),
    # session inflation
    Alt("session_inflation", "psy60_share_mcd", "ratio", tuple(PSY_60_CODES), tuple(PSY_ALL_CODES), why="60-minute sessions over all psychotherapy lines"),
    Alt("session_inflation", "prolonged_per_em", "ratio", tuple(PROLONGED_CODES), tuple(EM_CODES), why="prolonged-service add-ons per E/M line"),
    # lab testing / unbundling
    Alt("lab_testing", "definitive_reflex", "ratio", DEF_DRUG, PRES_DRUG, why="definitive drug tests over presumptive screens (the screen that found the labs)"),
    Alt("lab_testing", "definitive_per_pm", "intensity", DEF_DRUG, why="definitive tests per patient-month"),
    Alt("lab_unbundling", "component_per_draw", "ratio", tuple(LAB_COMPONENT_CODES), tuple(LAB_DRAW_CODES), why="chemistry component lines per venipuncture"),
    Alt("lab_unbundling", "component_share", "share", tuple(LAB_COMPONENT_CODES), why="component dollars over all dollars"),
    # personal care / home health / behavioral health: the state-prosecuted conduct, in the forms that decide a visit
    Alt("evv_personal_care", "pc_units_per_pm", "intensity", PC_CODES, why="personal-care units per patient-month (16 hours a day is 64 units)"),
    Alt("evv_personal_care", "pc_peak_per_day", "peak", PC_CODES, why="the peak month's personal-care lines per calendar day"),
    Alt("evv_personal_care", "peer_state_tax", "peer", why="the current subscore within state x taxonomy (the state's unit rule is the peer)"),
    Alt("home_health_intensity", "hh_visits_per_pm", "intensity", tuple(HH_CODES), why="home-health visit lines per patient-month"),
    Alt("home_health_intensity", "hh_peak_per_day", "peak", tuple(HH_CODES), why="the peak month's home-health lines per calendar day"),
    Alt("behavioral_health", "bh_perdiem_peak", "peak", BH_PERDIEM, why="per-diem program lines per calendar day (the census against the building)"),
    Alt("behavioral_health", "bh_units_per_pm", "intensity", BH_UNITS, why="community behavioral-health units per patient-month"),
    Alt("behavioral_health", "peer_state_tax", "peer", why="the current subscore within state x taxonomy"),
    # transport, DME: the fleet and the mailbox shapes
    Alt("nemt_fraud", "nemt_peak_legs_per_day", "peak", NEMT, why="the peak month's transport legs per calendar day (vans at the base)"),
    Alt("nemt_fraud", "nemt_legs_per_pm", "intensity", NEMT, why="legs per patient-month"),
    Alt("dme_ring", "dme_share", "share", num_prefix=DME_PREFIX, why="DME dollars over all dollars"),
    Alt("dme_ring", "dme_per_pm", "intensity", num_prefix=DME_PREFIX, why="DME lines per patient-month"),
    # ramp / overutilization / single-service: the temporal and concentration forms
    Alt("rapid_ramp", "ramp_3m", "ramp", why="the largest three-month paid rise over the prior three months"),
    Alt("overutilization", "lines_per_pm_all", "intensity", why="all lines per patient-month"),
    Alt("overutilization", "peer_state_tax", "peer", why="the current subscore within state x taxonomy"),
    Alt("single_service_mill", "top_code_share", "share", why="the dominant code's dollars over all dollars (concentration)"),
    Alt("impossible_day", "peak_lines_per_day_all", "peak", why="the peak month's lines per calendar day, every code"),
    Alt("rendering_identity", "self_rendered_share", "share", why="dollars whose servicing NPI is the billing NPI itself, over all dollars"),
)


def _in(codes) -> str:
    return "(" + ",".join(f"'{c}'" for c in sorted(set(codes))) + ")"


def _pred(alt: Alt, which: str) -> str:
    codes = alt.numerator if which == "num" else alt.denominator
    prefix = alt.num_prefix if which == "num" else ()
    parts = []
    if codes:
        parts.append(f"hc IN {_in(codes)}")
    if prefix:
        parts.append("(" + " OR ".join(f"hc LIKE '{p}%'" for p in prefix) + ")")
    return "(" + " OR ".join(parts) + ")" if parts else "TRUE"


def compute_alternatives(spending: Path, alts: tuple[Alt, ...] = ALTERNATIVES, con=None,
                         matrix: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per billing NPI with every alternative as a column `alt_<scheme>__<name>`."""
    from src.attempt_2.duck import connect
    con = con or connect()
    p = str(spending).replace("'", "''")
    cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()]
    lines = "CAST(total_claim_lines AS DOUBLE)" if "total_claim_lines" in cols else "1.0"
    pats = "CAST(total_patients AS DOUBLE)" if "total_patients" in cols else "1.0"
    svc = "CAST(servicing_npi AS VARCHAR)" if "servicing_npi" in cols else "NULL"
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE alt_base AS
        SELECT CAST(billing_npi AS VARCHAR) AS npi, UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) AS hc,
               substr(CAST(service_month AS VARCHAR), 1, 7) AS mon,
               SUM(CAST(total_paid AS DOUBLE)) AS paid, SUM({lines}) AS lines, SUM({pats}) AS pats,
               SUM(CASE WHEN {svc} = CAST(billing_npi AS VARCHAR) THEN CAST(total_paid AS DOUBLE) ELSE 0 END) AS paid_self
        FROM read_parquet('{p}') WHERE billing_npi IS NOT NULL
        GROUP BY 1, 2, 3""")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE alt_tot AS
        SELECT npi, SUM(paid) AS paid_all, SUM(lines) AS lines_all, SUM(pats) AS pm_all, SUM(paid_self) AS paid_self
        FROM alt_base GROUP BY 1""")
    frames = [con.execute("SELECT npi FROM alt_tot").df()]
    for a in alts:
        col = f"alt_{a.scheme}__{a.name}"
        if a.kind == "peer":
            continue                                            # computed from the matrix below
        if a.kind == "ratio":
            q = f"""SELECT npi,
                      CASE WHEN SUM(CASE WHEN {_pred(a, 'den')} THEN lines ELSE 0 END) > 0
                           THEN SUM(CASE WHEN {_pred(a, 'num')} THEN lines ELSE 0 END)
                                / SUM(CASE WHEN {_pred(a, 'den')} THEN lines ELSE 0 END) END AS "{col}"
                    FROM alt_base GROUP BY 1 HAVING SUM(CASE WHEN {_pred(a, 'num')} THEN lines ELSE 0 END) > 0"""
        elif a.kind == "intensity":
            q = f"""SELECT npi,
                      CASE WHEN SUM(CASE WHEN {_pred(a, 'num')} THEN pats ELSE 0 END) > 0
                           THEN SUM(CASE WHEN {_pred(a, 'num')} THEN lines ELSE 0 END)
                                / SUM(CASE WHEN {_pred(a, 'num')} THEN pats ELSE 0 END) END AS "{col}"
                    FROM alt_base GROUP BY 1 HAVING SUM(CASE WHEN {_pred(a, 'num')} THEN lines ELSE 0 END) > 0"""
        elif a.kind == "share":
            if a.name == "top_code_share":
                q = f"""SELECT npi, MAX(paid_code) / NULLIF(SUM(paid_code), 0) AS "{col}" FROM (
                          SELECT npi, hc, SUM(GREATEST(paid, 0)) AS paid_code FROM alt_base GROUP BY 1, 2) GROUP BY 1"""
            elif a.name == "self_rendered_share":
                q = f"""SELECT npi, CASE WHEN paid_all > 0 THEN GREATEST(paid_self, 0) / paid_all END AS "{col}" FROM alt_tot"""
            else:
                q = f"""SELECT b.npi, SUM(CASE WHEN {_pred(a, 'num')} THEN GREATEST(paid, 0) ELSE 0 END) / NULLIF(t.paid_all, 0) AS "{col}"
                        FROM alt_base b JOIN alt_tot t USING (npi) GROUP BY b.npi, t.paid_all"""
        elif a.kind == "peak":
            q = f"""SELECT npi, MAX(l / d) AS "{col}" FROM (
                      SELECT npi, mon, SUM(lines) AS l,
                             date_diff('day', CAST(mon || '-01' AS DATE), CAST(mon || '-01' AS DATE) + INTERVAL 1 MONTH) AS d
                      FROM alt_base WHERE {_pred(a, 'num')} GROUP BY 1, 2) GROUP BY 1"""
        elif a.kind == "ramp":
            q = f"""WITH m AS (SELECT npi, mon, SUM(paid) AS paid FROM alt_base GROUP BY 1, 2),
                    w AS (SELECT npi, mon,
                                 SUM(paid) OVER (PARTITION BY npi ORDER BY mon ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS cur3,
                                 SUM(paid) OVER (PARTITION BY npi ORDER BY mon ROWS BETWEEN 5 PRECEDING AND 3 PRECEDING) AS prev3,
                                 COUNT(*) OVER (PARTITION BY npi ORDER BY mon ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) AS n6
                          FROM m)
                    SELECT npi, MAX(CASE WHEN n6 >= 6 AND prev3 > 0 THEN (cur3 - prev3) / prev3 END) AS "{col}" FROM w GROUP BY 1"""
        else:
            continue
        frames.append(con.execute(q).df())
    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on="npi", how="left")
    if matrix is not None and len(matrix):
        out = out.merge(_peer_alternatives(matrix, alts), on="npi", how="left")
    return out


def _peer_alternatives(matrix: pd.DataFrame, alts) -> pd.DataFrame:
    """The current subscore's one-sided percentile within state x taxonomy (mid-rank), the
    'state rule as peer group' form; cells under 30 rows fall back to the state."""
    m = matrix[["npi"]].copy()
    m["npi"] = m["npi"].astype(str)
    st = matrix.get("practice_state", pd.Series("", index=matrix.index)).fillna("").astype(str)
    tx = matrix.get("primary_taxonomy", pd.Series("", index=matrix.index)).fillna("").astype(str)
    for a in alts:
        if a.kind != "peer":
            continue
        sub = f"subscore_{a.scheme}"
        col = f"alt_{a.scheme}__{a.name}"
        if sub not in matrix.columns:
            continue
        v = pd.to_numeric(matrix[sub], errors="coerce")
        key = st + "|" + tx
        size = v.groupby(key).transform("count")
        key2 = key.where(size >= 30, st)
        pct = v.groupby(key2).rank(method="average", pct=True)
        m[col] = pct.where(v.notna())
    return m


def grade(matrix: pd.DataFrame, alt_frame: pd.DataFrame, rulers: dict[str, pd.DataFrame],
          alts: tuple[Alt, ...] = ALTERNATIVES) -> pd.DataFrame:
    """One row per (scheme, formula) x ruler: AUC, positives covered, verdict; the current
    subscore is the first row of every scheme so the comparison reads in one table."""
    mm = matrix[["npi"]].copy()
    mm["npi"] = mm["npi"].astype(str)
    af = alt_frame.copy()
    af["npi"] = af["npi"].astype(str)
    joined = mm.merge(af, on="npi", how="left")
    vecs = {name: forward_label_vector(matrix, r) for name, r in rulers.items()}
    rows = []
    schemes = sorted({a.scheme for a in alts})
    for scheme in schemes:
        cands = [(f"subscore_{scheme}", "current", "subscore")] + \
                [(f"alt_{a.scheme}__{a.name}", a.name, a.kind) for a in alts if a.scheme == scheme]
        for col, name, kind in cands:
            src = matrix if col.startswith("subscore_") else joined
            if col not in src.columns:
                continue
            s = pd.to_numeric(src[col], errors="coerce").to_numpy(float)
            cov = float(np.isfinite(s).mean())
            for rname, (y, keep) in vecs.items():
                if y is None:
                    continue
                auc, npos = _label_auc(y[keep], s[keep])
                rows.append({"scheme": scheme, "formula": name, "kind": kind, "ruler": rname, "coverage": round(cov, 4),
                             "auc": None if np.isnan(auc) else round(auc, 3), "positives": int(npos),
                             "verdict": fca_verdict(None if np.isnan(auc) else auc, npos)})
    return pd.DataFrame(rows)


def to_markdown(g: pd.DataFrame, rulers: list[str]) -> str:
    L = ["# SCHEME ALTERNATIVES: the same scheme, calculated another way", "",
         "_Every weak scheme's alternative formulas (the screens' ratio / intensity / peak forms, the ramp, the "
         "state x taxonomy peer form) computed from the as-of fact and graded beside the CURRENT subscore on "
         "every ruler the run carries. The better form separates the cases the scheme was written for (the "
         "typed ruler first). A formula replaces a subscore only through a pre-registered run._", ""]
    if not len(g):
        return "\n".join(L + ["_no rows_", ""])
    best_notes = []
    for scheme, gs in g.groupby("scheme", sort=True):
        L += [f"## {scheme}", "", "| formula | kind | coverage | " + " | ".join(f"{r} AUC (pos)" for r in rulers) + " |",
              "|---|---|--:|" + "--:|" * len(rulers)]
        for (formula, kind), gf in gs.groupby(["formula", "kind"], sort=False):
            cells = []
            for r in rulers:
                row = gf[gf["ruler"] == r]
                cells.append(f"{row.iloc[0]['auc']} ({row.iloc[0]['positives']}) {row.iloc[0]['verdict']}" if len(row) and row.iloc[0]["auc"] is not None else "-")
            cov = gf["coverage"].iloc[0]
            L.append(f"| {formula} | {kind} | {cov:.1%} | " + " | ".join(cells) + " |")
        # the read: the best formula on the typed ruler (else fca) with >= FCA_MIN_POS positives
        pref = next((r for r in rulers if r.startswith("typed")), None) or ("fca" if "fca" in rulers else rulers[0])
        gp = gs[(gs["ruler"] == pref) & (gs["positives"] >= FCA_MIN_POS) & gs["auc"].notna()]
        if len(gp):
            top = gp.sort_values("auc", ascending=False).iloc[0]
            cur = gp[gp["formula"] == "current"]
            cur_auc = cur.iloc[0]["auc"] if len(cur) else None
            note = (f"- **{scheme}** on {pref}: best = {top['formula']} ({top['auc']})"
                    + (f" vs current {cur_auc}" if cur_auc is not None else " (current not gradable)")
                    + (" -> a candidate replacement" if cur_auc is not None and top["formula"] != "current" and top["auc"] >= cur_auc + 0.05 else ""))
            best_notes.append(note)
        L.append("")
    L += ["## The read", ""] + (best_notes or ["_no scheme has a gradable formula on its own ruler yet_"]) + [""]
    return "\n".join(L)


def _load_ruler(p: Path) -> pd.DataFrame:
    return pd.read_csv(p, dtype=str)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--spending", required=True, help="the as-of spending fact (interim/spending_asof_<cut>.parquet) or the full fact")
    ap.add_argument("--cutoff", default=None, help="freeze month (to pick the rulers); default: the manifest's asof_cutoff")
    ap.add_argument("--horizon", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    run = Path(a.run_dir)
    matrix = pd.read_parquet(run / "provider_features_for_model.parquet")
    manifest = json.loads((run / "feature_manifest.json").read_text())
    cutoff = a.cutoff or str(manifest.get("asof_cutoff") or "")[:7]
    rulers: dict[str, pd.DataFrame] = {}
    for key, pat in (("fca", "__fca"), ("state", "__state")):
        ps = sorted(run.glob(f"future_bans_after_{cutoff}{pat}*.csv")) if cutoff else sorted(run.glob(f"future_bans_after_*{pat}*.csv"))
        if ps:
            rulers[key] = _load_ruler(ps[-1])
    for k, v in load_typed_rulers(run, cutoff or None).items():
        rulers[f"typed_{k}"] = v
    if not rulers:
        raise SystemExit(f"no ruler files under {run} (future_bans_after_*.csv)")
    print(f"[scheme_alternatives] rulers: {', '.join(rulers)}")
    alt_frame = compute_alternatives(Path(a.spending), matrix=matrix)
    alt_frame.to_parquet(run / "scheme_alternatives.parquet", index=False)
    g = grade(matrix, alt_frame, rulers)
    # a scheme's typed ruler is its OWN; the other typed rulers are noise in its row
    g = g[~g["ruler"].str.startswith("typed_") | (g["ruler"] == "typed_" + g["scheme"].map(scheme_case_type))]
    g.to_csv(run / "scheme_alternatives.csv", index=False)
    ruler_names = ["fca"] if "fca" in rulers else []
    ruler_names += ["state"] if "state" in rulers else []
    ruler_names += sorted({r for r in g["ruler"].unique() if r.startswith("typed_")})
    out = Path(a.out) if a.out else run / "SCHEME_ALTERNATIVES.md"
    out.write_text(to_markdown(g, ruler_names), encoding="utf-8")
    (run / "scheme_alternatives.json").write_text(g.to_json(orient="records", indent=1), encoding="utf-8")
    print(f"[scheme_alternatives] {len(alt_frame):,} NPIs, {len(g)} graded rows -> {out}")


if __name__ == "__main__":
    main()
