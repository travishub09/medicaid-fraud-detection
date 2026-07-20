"""
dossier_batch.py — generate a dossier for every NPI in a lead set.

Fans ``dossier_build`` over a batch: takes a rings/results table (or an explicit
NPI list) and writes one counsel-grade brief per NPI into an output directory,
plus an INDEX.md linking them. This is what turns a detector's output (the
facility-code rings, a signal-ranked top-N) into a folder of review-ready
dossiers in one command.

    python -m src.model_a.dossier_batch --rings facility_code_rings.parquet \\
        --pack dossier_pack.parquet --monthly ... --codes ... --out-dir dossiers/
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .dossier_build import build_npi_dossier


def _npis_from_rings(rings: pd.DataFrame) -> list[str]:
    """Every distinct member NPI across the ring rows (member_npis is ';'-joined)."""
    if rings is None or not len(rings):
        return []
    if "member_npis" in rings.columns:
        seen: list[str] = []
        for cell in rings["member_npis"].fillna("").astype(str):
            for n in cell.split(";"):
                n = n.strip()
                if n and n not in seen:
                    seen.append(n)
        return seen
    if "npi" in rings.columns:
        return list(dict.fromkeys(rings["npi"].astype(str)))
    return []


def build_batch(npis: list[str], pack: pd.DataFrame, out_dir: str | Path,
                monthly: pd.DataFrame | None = None,
                codes: pd.DataFrame | None = None,
                owners: pd.DataFrame | None = None) -> list[Path]:
    """Write one dossier per NPI + an INDEX.md. Returns the written paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pack = pack.copy()
    pack["npi"] = pack["npi"].astype(str)
    present = set(pack["npi"])

    written: list[Path] = []
    index_rows = []
    for npi in npis:
        npi = str(npi)
        md = build_npi_dossier(npi, pack, monthly, codes, owners)
        p = out / f"DOSSIER_{npi}.md"
        p.write_text(md, encoding="utf-8")
        written.append(p)
        # a one-line index entry: NPI, suspect dollars if we can read them
        r = pack[pack["npi"] == npi]
        if len(r) and npi in present:
            net = pd.to_numeric(r.iloc[0].get("net_paid"), errors="coerce")
            exp = pd.to_numeric(r.iloc[0].get("expected_net_paid"), errors="coerce")
            suspect = max((net or 0) - (exp or 0), 0)
            city = str(r.iloc[0].get("addr_city") or "")
            state = str(r.iloc[0].get("practice_state") or "")
            index_rows.append((npi, f"${suspect:,.0f}", f"{city} {state}".strip()))
        else:
            index_rows.append((npi, "not in pack", ""))

    idx = ["# Dossier index", "", f"{len(written)} dossiers generated.", "",
           "| NPI | suspect $ | location | file |", "|---|--:|---|---|"]
    for npi, susp, loc in index_rows:
        idx.append(f"| {npi} | {susp} | {loc} | DOSSIER_{npi}.md |")
    (out / "INDEX.md").write_text("\n".join(idx), encoding="utf-8")
    return written


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pack", required=True)
    ap.add_argument("--rings", default=None,
                    help="rings/results parquet (member_npis or npi column)")
    ap.add_argument("--npis", default=None, help="comma-separated NPIs instead")
    ap.add_argument("--monthly", default=None)
    ap.add_argument("--codes", default=None)
    ap.add_argument("--owners", default=None)
    ap.add_argument("--out-dir", default="dossiers")
    args = ap.parse_args()

    def _load(p):
        return pd.read_parquet(p) if p and Path(p).exists() else None

    if args.npis:
        npis = [x.strip() for x in args.npis.split(",") if x.strip()]
    elif args.rings and Path(args.rings).exists():
        npis = _npis_from_rings(pd.read_parquet(args.rings))
    else:
        print("[dossier_batch] no --npis and no readable --rings; nothing to do")
        return
    if not npis:
        print("[dossier_batch] the lead set resolved to zero NPIs; nothing to do")
        return
    written = build_batch(npis, pd.read_parquet(args.pack), args.out_dir,
                          _load(args.monthly), _load(args.codes), _load(args.owners))
    print(f"[dossier_batch] wrote {len(written)} dossiers + INDEX.md → {args.out_dir}")


if __name__ == "__main__":
    main()
