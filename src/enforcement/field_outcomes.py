"""field_outcomes.py — the operator's own verified field results as a label source (2026-11-01).

THE ONLY LABEL AT OUR EXACT GRAIN. Every other positive is a public record about somebody
else's case: federal, mostly Medicare, mostly institutional, 83 percent of it on people
who never billed under their own number. A per-diem count at the door that comes back
over the licence, a transport yard with no vans behind the legs billed, a lab's own
requisition rule in a former employee's hands: these are outcomes on OUR provider, on
OUR program, in OUR segment, dated by the day they were established. Small numbers,
perfect fit, and the only label that grows with the investigators' work.

The file (attorney work product, NEVER git): <data_root>/enforcement/field_outcomes.csv

    org_id | npi | date | outcome | basis | note | source
    outcome: over_capacity | no_fleet | confirmed | contradiction | cleared | inconclusive
    basis:   one line (the count against the licence, the vans against the legs)
    source:  the packet or report file the finding came from (a path under enforcement/)

Rows carry either an org_id (org:...) or an NPI; an org_id resolves to its member NPIs
through the graph's npi_to_org (every member is the organization's billing number).

Output: processed/exclusions_field.parquet in the exclusions schema with excl_type
"field:<outcome>", which the next graph build turns into exclusion nodes. fca_relevance
tiers an adverse outcome (over_capacity / no_fleet / confirmed / contradiction) HARD, a
cleared or inconclusive check EXCLUDED (never a positive, kept for the record), and
prospective_label.filter_sources NEVER admits "field:" rows to a forward ruler: a result
we established ourselves is training evidence, not a ruler the model is graded on.

    python -m src.enforcement.field_outcomes --data-root D [--graph-dir D/graph]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import is_valid_npi

EXCL_COLS = ["npi", "entity_name", "name_key", "excl_type", "excl_date", "reinstate_date", "currently_active"]
OUTCOMES = ("over_capacity", "no_fleet", "confirmed", "contradiction", "cleared", "inconclusive")
ADVERSE = ("over_capacity", "no_fleet", "confirmed", "contradiction")


def load_field_outcomes(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=["org_id", "npi", "date", "outcome", "basis", "note", "source"])
    df = pd.read_csv(p, dtype=str).fillna("")
    df.columns = [str(c).strip().lower() for c in df.columns]
    for c in ("org_id", "npi", "date", "outcome", "basis", "note", "source"):
        if c not in df.columns:
            df[c] = ""
    df["outcome"] = df["outcome"].str.strip().str.lower()
    bad = ~df["outcome"].isin(OUTCOMES)
    if bad.any():
        raise ValueError(f"field_outcomes.csv: unknown outcome(s) {sorted(df.loc[bad, 'outcome'].unique())}; "
                         f"one of {OUTCOMES}")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if df["date"].isna().any():
        raise ValueError("field_outcomes.csv: every row needs a date (YYYY-MM-DD): the day the fact was established")
    return df


def to_exclusion_rows(outcomes: pd.DataFrame, npi_to_org: pd.DataFrame | None = None,
                      provider_dim: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(exclusion rows, residue). An org_id resolves to every member NPI; a bare NPI must
    be Luhn-valid; a row with neither is residue with the reason."""
    rows, residue = [], []
    members: dict[str, list[str]] = {}
    if npi_to_org is not None and len(npi_to_org):
        oc = "org_id" if "org_id" in npi_to_org.columns else "org_node_id"
        for o, g in npi_to_org.groupby(npi_to_org[oc].astype(str)):
            members[o] = g["npi"].astype(str).tolist()
    names: dict[str, str] = {}
    if provider_dim is not None and len(provider_dim):
        pdim = provider_dim.drop_duplicates("npi")
        nm = pdim.get("org_legal_name", pd.Series("", index=pdim.index)).fillna("").astype(str)
        nm = nm.where(nm != "", pdim.get("provider_name", pd.Series("", index=pdim.index)).fillna("").astype(str))
        names = dict(zip(pdim["npi"].astype(str), nm))
    for r in outcomes.itertuples():
        npis: list[str] = []
        if str(r.org_id).strip():
            npis = members.get(str(r.org_id).strip(), [])
            if not npis:
                residue.append({**r._asdict(), "drop_reason": "org_id not in npi_to_org (rebuild the graph or give the NPI)"})
                continue
        elif str(r.npi).strip():
            n = re.sub(r"\D", "", str(r.npi))
            if not is_valid_npi(n):
                residue.append({**r._asdict(), "drop_reason": "NPI fails the Luhn check"})
                continue
            npis = [n]
        else:
            residue.append({**r._asdict(), "drop_reason": "neither org_id nor npi"})
            continue
        for n in npis:
            rows.append({"npi": n, "entity_name": names.get(n, ""), "name_key": "",
                         "excl_type": f"field:{r.outcome}", "excl_date": r.date.strftime("%Y-%m-%d"),
                         "reinstate_date": "", "currently_active": r.outcome in ADVERSE,
                         "field_basis": str(r.basis)[:200], "field_source": str(r.source)[:200]})
    out = pd.DataFrame(rows, columns=EXCL_COLS + ["field_basis", "field_source"])
    res = pd.DataFrame(residue)
    if "Index" in res.columns:
        res = res.drop(columns=["Index"])
    return out.drop_duplicates(["npi", "excl_type", "excl_date"]).reset_index(drop=True), res


def report(out: pd.DataFrame, res: pd.DataFrame, src: Path, dst: Path) -> str:
    L = ["# FIELD OUTCOMES -> label rows", "",
         f"_{src}: the operator's verified field results, written to {dst} in the exclusions schema "
         "(excl_type field:<outcome>; adverse outcomes are hard-tier TRAINING positives at the next graph build; "
         "cleared / inconclusive rows are kept, never positives; no field row ever enters a forward ruler)._", ""]
    if len(out):
        vc = out["excl_type"].value_counts()
        L += ["| excl_type | NPIs |", "|---|--:|"] + [f"| {k} | {v} |" for k, v in vc.items()]
    else:
        L.append("_no rows_")
    if len(res):
        L += ["", "## Residue (not written)", "", "| org_id | npi | outcome | reason |", "|---|---|---|---|"]
        L += [f"| {r.get('org_id', '')} | {r.get('npi', '')} | {r.get('outcome', '')} | {r.get('drop_reason', '')} |"
              for r in res.to_dict("records")]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--graph-dir", default=None, help="graph root with npi_to_org.parquet (default <data_root>/graph)")
    ap.add_argument("--in", dest="inp", default=None, help="default <data_root>/enforcement/field_outcomes.csv")
    ap.add_argument("--out", default=None, help="default <data_root>/processed/exclusions_field.parquet")
    a = ap.parse_args()
    root = Path(a.data_root)
    src = Path(a.inp) if a.inp else root / "enforcement" / "field_outcomes.csv"
    dst = Path(a.out) if a.out else root / "processed" / "exclusions_field.parquet"
    g = Path(a.graph_dir) if a.graph_dir else root / "graph"
    outcomes = load_field_outcomes(src)
    n2o = pd.read_parquet(g / "npi_to_org.parquet") if (g / "npi_to_org.parquet").exists() else None
    pdim = pd.read_parquet(root / "processed" / "provider_dim.parquet") if (root / "processed" / "provider_dim.parquet").exists() else None
    out, res = to_exclusion_rows(outcomes, n2o, pdim)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(dst, index=False)
    rep = dst.with_name("FIELD_OUTCOMES.md")
    rep.write_text(report(out, res, src, dst), encoding="utf-8")
    print(f"[field_outcomes] {len(outcomes)} rows -> {len(out)} label rows ({int(out['currently_active'].sum()) if len(out) else 0} adverse), "
          f"{len(res)} residue -> {dst}; rebuild the graph so they become exclusion nodes")


if __name__ == "__main__":
    main()
