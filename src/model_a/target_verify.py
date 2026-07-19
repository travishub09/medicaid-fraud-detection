"""
target_verify.py — verify a target list against the LIVE NPI registry before it
reaches counsel or a funder.

The manual version of this check (registry lookups by hand during dossier
review) caught a federal-agency defendant, a stale-primary-taxonomy pipeline
bug, a shared-office ring confirmation, and a probable management-company hub
in a single session. This automates it as a standing step: every headline NPI
on every target list gets a live-registry pass, and the discrepancies land in a
report instead of depending on someone thinking to look.

For each NPI: Luhn-validate, hit the NPI Registry API, and compare against our
matrix/pack grain:

  * NOT_FOUND / DEACTIVATED — a target that no longer exists or was shut off
    (kills or transforms the dossier; a deactivated target may already be
    caught).
  * TAXONOMY_MISMATCH — the registry's flagged primary differs from ours (the
    peer group and the dossier's specialty framing are wrong for this NPI).
  * STATE_MISMATCH — practice state moved.
  * CONFIRMED — identity checks out; the row still carries the registry's
    address, enumeration date, and last-updated staleness for the dossier.

Output: a per-NPI table (parquet/csv) + TARGET_VERIFICATION.md for the book.
Never a verdict on conduct — this verifies IDENTITY, nothing else.

    python -m src.model_a.target_verify --npis 1588799746,1033128848 \
        --matrix dossier_pack.parquet --out-dir .
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.ingest_cms.nppes_api import lookup_npi
from src.feeds.client import default_fetch_json

VERIFY_COLUMNS = ["npi", "verdict", "registry_status", "registry_taxonomy",
                  "registry_taxonomy_desc", "our_taxonomy", "registry_state",
                  "our_state", "registry_entity_type", "addr_line1", "city",
                  "enumeration_date", "last_updated", "note"]


def verify_targets(npis: list[str], matrix: pd.DataFrame | None = None,
                   fetch_json=default_fetch_json) -> pd.DataFrame:
    """One row per NPI with a verdict; matrix (any frame with npi +
    primary_taxonomy/practice_state) enables the comparison columns."""
    ours = {}
    if matrix is not None and len(matrix) and "npi" in matrix.columns:
        m = matrix.drop_duplicates("npi").set_index(matrix["npi"].astype(str))
        for npi in m.index:
            ours[npi] = {
                "taxonomy": str(m.loc[npi].get("primary_taxonomy", "") or ""),
                "state": str(m.loc[npi].get("practice_state", "") or ""),
            }
    rows = []
    for raw in npis:
        npi = str(raw).strip()
        mine = ours.get(npi, {"taxonomy": "", "state": ""})
        try:
            reg = lookup_npi(npi, fetch_json=fetch_json)
        except Exception as e:                       # transport failure ≠ verdict
            rows.append({"npi": npi, "verdict": "LOOKUP_FAILED", "note": str(e)[:200]})
            continue
        if reg is None:
            rows.append({"npi": npi, "verdict": "NOT_FOUND",
                         "note": "invalid NPI or no registry record"})
            continue
        notes = []
        if reg["status"] and reg["status"].upper() != "A":
            verdict = "DEACTIVATED"
            notes.append(f"registry status={reg['status']}")
        elif mine["taxonomy"] and reg["taxonomy_code"] \
                and mine["taxonomy"] != reg["taxonomy_code"]:
            verdict = "TAXONOMY_MISMATCH"
            notes.append(f"ours={mine['taxonomy']} registry={reg['taxonomy_code']}")
        elif mine["state"] and reg["state"] and mine["state"] != reg["state"]:
            verdict = "STATE_MISMATCH"
            notes.append(f"ours={mine['state']} registry={reg['state']}")
        else:
            verdict = "CONFIRMED"
        rows.append({
            "npi": npi, "verdict": verdict, "registry_status": reg["status"],
            "registry_taxonomy": reg["taxonomy_code"],
            "registry_taxonomy_desc": reg["taxonomy_desc"],
            "our_taxonomy": mine["taxonomy"], "registry_state": reg["state"],
            "our_state": mine["state"], "registry_entity_type": reg["entity_type"],
            "addr_line1": reg["addr_line1"], "city": reg["city"],
            "enumeration_date": reg["enumeration_date"],
            "last_updated": reg["last_updated"], "note": "; ".join(notes),
        })
    return pd.DataFrame(rows).reindex(columns=VERIFY_COLUMNS)


def to_markdown(res: pd.DataFrame) -> str:
    L = ["# TARGET VERIFICATION — live NPI-registry check", ""]
    L.append("_Identity verification only: does each target NPI still exist, "
             "match our specialty/state, and remain active? Never a verdict on "
             "conduct. Shared addresses and enumeration-date clusters across "
             "targets are ring evidence — compare rows._")
    L.append("")
    counts = res["verdict"].value_counts().to_dict() if len(res) else {}
    L.append("- " + ", ".join(f"**{k}: {v}**" for k, v in counts.items()) if counts
             else "- no targets checked")
    L.append("")
    L.append("| npi | verdict | registry specialty | addr | city/state | enumerated | last updated | note |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in res.itertuples():
        L.append(f"| {r.npi} | {r.verdict} | {r.registry_taxonomy_desc or '-'} "
                 f"| {r.addr_line1 or '-'} | {(r.city or '-')}, {r.registry_state or '-'} "
                 f"| {r.enumeration_date or '-'} | {r.last_updated or '-'} "
                 f"| {r.note or ''} |")
    return "\n".join(L)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--npis", help="comma-separated NPI list")
    ap.add_argument("--targets-file",
                    help="csv/parquet with an npi column (alternative to --npis)")
    ap.add_argument("--matrix", help="matrix/pack parquet for the comparison columns")
    ap.add_argument("--out-dir", default=".")
    args = ap.parse_args()
    npis: list[str] = []
    if args.npis:
        npis += [n.strip() for n in args.npis.split(",") if n.strip()]
    if args.targets_file:
        tf = (pd.read_parquet(args.targets_file)
              if args.targets_file.endswith(".parquet")
              else pd.read_csv(args.targets_file, dtype=str))
        npis += tf["npi"].astype(str).tolist()
    if not npis:
        raise SystemExit("no NPIs: pass --npis or --targets-file")
    matrix = None
    if args.matrix:
        matrix = pd.read_parquet(args.matrix)
    res = verify_targets(list(dict.fromkeys(npis)), matrix=matrix)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    res.to_csv(out / "target_verification.csv", index=False)
    (out / "TARGET_VERIFICATION.md").write_text(to_markdown(res), encoding="utf-8")
    n_bad = int((res["verdict"] != "CONFIRMED").sum())
    print(res[["npi", "verdict", "registry_taxonomy", "note"]].to_string(index=False))
    print(f"\n{len(res)} checked, {n_bad} need attention → "
          f"{out / 'TARGET_VERIFICATION.md'}")


if __name__ == "__main__":
    main()
