"""
nppes_verify.py — verify the top-N leads against the LIVE NPI Registry.

The manual registry checks (pasted NPI lookups) caught three real problems on
dossier targets: a stale primary-taxonomy label, an entity mislabel, and a
deactivated-but-still-billing record. This module turns that hand work into a
standing, cached, top-N verification stage.

WHAT IT IS: an offline, review-time enrichment/adjudication pass over the
handful of leads headed for human review. For each, it fetches the current
registry record and compares it to what our matrix believes — taxonomy, entity
type, active status, existence — and flags every disagreement with both values
and the retrieval date. Every raw response is cached under feeds/raw/nppes/ for
the audit trail.

WHAT IT IS NOT: a feature generator. A value fetched live today cannot be a
point-in-time training feature (it would leak and it is not reproducible), and
this never runs over the full universe. Its output is REVIEW EVIDENCE and, with
a human in the loop, a candidate LABEL correction — never a trainable X column.
It is gated to the top-N leads, so cost scales with review volume, not with the
617k-provider matrix.

    python -m src.feeds.nppes_verify --matrix provider_features_for_model.parquet \
        --top 50 --out REGISTRY_VERIFICATION.md
    # or an explicit list:
    python -m src.feeds.nppes_verify --matrix ... --npis 1588799746,1255694451
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import canonicalize_npi
from src.feeds.client import cache_raw, default_fetch_json
from src.ingest_cms.nppes_api import lookup_npi

# Ranking columns tried in order when the caller does not name one. These pick
# the leads most likely to reach a human, so verification effort goes where it
# matters. All optional — the module falls back to matrix order.
_DEFAULT_RANK_COLS = ["priority_rank", "weak_label_score", "net_paid", "gross_paid"]

_RESULT_COLS = [
    "npi", "registry_flag", "matrix_taxonomy", "registry_taxonomy",
    "registry_taxonomy_desc", "matrix_entity_type", "registry_entity_type",
    "registry_status", "registry_name", "registry_city", "registry_state",
    "registry_enumeration_date", "registry_last_updated", "retrieved_utc",
]


def _pick_rank(df: pd.DataFrame, rank_col: str | None) -> pd.DataFrame:
    """Order the frame so the highest-priority leads come first."""
    if rank_col and rank_col in df.columns:
        asc = rank_col in ("priority_rank",)          # rank 1 = most important
        return df.sort_values(rank_col, ascending=asc, na_position="last")
    for c in _DEFAULT_RANK_COLS:
        if c in df.columns:
            asc = c in ("priority_rank",)
            return df.sort_values(c, ascending=asc, na_position="last")
    return df


def _select_npis(df: pd.DataFrame, top: int, rank_col: str | None,
                 npis: list[str] | None, assessable_only: bool) -> list[str]:
    d = df.copy()
    d["npi"] = d["npi"].astype(str)
    if npis:
        want = {canonicalize_npi(n) for n in npis}
        want.discard(None)
        return [n for n in d["npi"] if n in want]
    if assessable_only and "assessable" in d.columns:
        d = d[pd.to_numeric(d["assessable"], errors="coerce").fillna(0) == 1]
    d = _pick_rank(d, rank_col)
    return d["npi"].head(top).tolist()


def _classify(matrix_row: dict, reg: dict | None) -> tuple[str, dict]:
    """Compare one matrix row to its registry record → (flag, evidence dict).

    Flags, worst-first: NOT_FOUND (registry has no live record), DEACTIVATED
    (record exists but is not Active), ENTITY_MISMATCH (individual vs org
    disagreement), TAXONOMY_MISMATCH (primary specialty disagreement), OK.
    A row can trip several checks; the returned flag is the most severe, and the
    evidence dict carries every field so the report shows the specifics.
    """
    ev = {
        "matrix_taxonomy": str(matrix_row.get("primary_taxonomy", "") or ""),
        "matrix_entity_type": str(matrix_row.get("entity_type", "") or ""),
        "registry_taxonomy": "", "registry_taxonomy_desc": "",
        "registry_entity_type": "", "registry_status": "",
        "registry_name": "", "registry_city": "", "registry_state": "",
        "registry_enumeration_date": "", "registry_last_updated": "",
    }
    if reg is None:
        return "NOT_FOUND", ev
    name = " ".join(x for x in (reg.get("last_name", ""), reg.get("first_name", ""),
                                reg.get("org_name", "")) if x).strip()
    ev.update({
        "registry_taxonomy": reg.get("taxonomy_code", ""),
        "registry_taxonomy_desc": reg.get("taxonomy_desc", ""),
        "registry_entity_type": reg.get("entity_type", ""),
        "registry_status": reg.get("status", ""),
        "registry_name": name,
        "registry_city": reg.get("city", ""),
        "registry_state": reg.get("state", ""),
        "registry_enumeration_date": reg.get("enumeration_date", ""),
        "registry_last_updated": reg.get("last_updated", ""),
    })
    flags = []
    # status: NPPES uses "A" for active; anything else is deactivated/other
    if ev["registry_status"] and ev["registry_status"].upper() != "A":
        flags.append("DEACTIVATED")
    me, re_ = ev["matrix_entity_type"], ev["registry_entity_type"]
    if me and re_ and me != re_:
        flags.append("ENTITY_MISMATCH")
    mt, rt = ev["matrix_taxonomy"], ev["registry_taxonomy"]
    if mt and rt and mt != rt:
        flags.append("TAXONOMY_MISMATCH")
    order = ["NOT_FOUND", "DEACTIVATED", "ENTITY_MISMATCH", "TAXONOMY_MISMATCH"]
    for f in order:
        if f in flags:
            return f, ev
    return "OK", ev


def verify_providers(matrix: pd.DataFrame, top: int = 50,
                     rank_col: str | None = None, npis: list[str] | None = None,
                     assessable_only: bool = True,
                     fetch_json=default_fetch_json,
                     cache: bool = True, cache_root: Path | None = None,
                     retrieved_utc: str = "") -> pd.DataFrame:
    """Verify the selected leads against the live registry → one row per NPI.

    ``retrieved_utc`` stamps the evidence with the retrieval time; the caller
    passes it (the pipeline forbids argless clock reads). ``fetch_json`` is
    injectable so tests never touch the network.
    """
    if matrix is None or not len(matrix):
        return pd.DataFrame(columns=_RESULT_COLS)
    m = matrix.copy()
    m["npi"] = m["npi"].astype(str)
    by_npi = {r["npi"]: r for r in m.to_dict("records")}
    chosen = _select_npis(m, top, rank_col, npis, assessable_only)

    rows = []
    for npi in chosen:
        canon = canonicalize_npi(npi)
        if canon is None:
            continue
        reg = lookup_npi(canon, fetch_json=fetch_json)
        if cache:
            cache_raw("nppes", f"verify_{canon}", {"npi": canon, "result": reg},
                      root=cache_root)
        flag, ev = _classify(by_npi.get(npi, {}), reg)
        rows.append({"npi": canon, "registry_flag": flag,
                     "retrieved_utc": retrieved_utc, **ev})
    return pd.DataFrame(rows, columns=_RESULT_COLS)


def to_markdown(res: pd.DataFrame) -> str:
    L = ["# REGISTRY VERIFICATION — top leads vs the live NPI Registry", ""]
    L.append("_Each lead's current federal registry record compared to what the "
             "matrix believes. Flags: NOT_FOUND (no live record), DEACTIVATED "
             "(record not Active), ENTITY_MISMATCH (individual vs organization), "
             "TAXONOMY_MISMATCH (primary specialty differs). This is review "
             "evidence, not a trainable feature._")
    L.append("")
    if not len(res):
        L.append("**No leads selected for verification.**")
        return "\n".join(L)
    n = len(res)
    counts = res["registry_flag"].value_counts().to_dict()
    ok = counts.get("OK", 0)
    L.append(f"- Verified: **{n}** leads. Clean: **{ok}**. "
             f"Flagged: **{n - ok}**.")
    order = ["NOT_FOUND", "DEACTIVATED", "ENTITY_MISMATCH", "TAXONOMY_MISMATCH"]
    for f in order:
        if counts.get(f):
            L.append(f"    - {f}: {counts[f]}")
    if res["retrieved_utc"].astype(str).str.len().gt(0).any():
        L.append(f"- Retrieved: {res['retrieved_utc'].dropna().iloc[0]} (UTC)")
    L.append("")
    flagged = res[res["registry_flag"] != "OK"]
    if len(flagged):
        L.append("## Flagged leads (review these)")
        L.append("")
        L.append("| NPI | flag | matrix says | registry says |")
        L.append("|---|---|---|---|")
        for r in flagged.itertuples():
            if r.registry_flag == "TAXONOMY_MISMATCH":
                ms = f"taxonomy {r.matrix_taxonomy}"
                rs = f"{r.registry_taxonomy} ({r.registry_taxonomy_desc})"
            elif r.registry_flag == "ENTITY_MISMATCH":
                ms = f"entity type {r.matrix_entity_type}"
                rs = f"entity type {r.registry_entity_type}"
            elif r.registry_flag == "DEACTIVATED":
                ms = "billing provider"
                rs = f"status {r.registry_status}"
            else:                                      # NOT_FOUND
                ms = "in scored universe"
                rs = "no live registry record"
            name = f" — {r.registry_name}" if r.registry_name else ""
            L.append(f"| {r.npi}{name} | {r.registry_flag} | {ms} | {rs} |")
        L.append("")
    L.append("_Registry evidence is public-identifier only. No PHI and no "
             "internal priority inference is ever sent to an external service._")
    return "\n".join(L)


def main() -> None:
    import argparse
    from datetime import datetime, timezone
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--matrix", required=True, help="provider matrix parquet")
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--rank-col", default=None,
                    help="column to rank leads by (default: priority_rank / "
                         "weak_label_score / net_paid)")
    ap.add_argument("--npis", default=None,
                    help="comma-separated explicit NPIs (overrides --top)")
    ap.add_argument("--all", action="store_true",
                    help="do not restrict to assessable leads")
    ap.add_argument("--out", default="REGISTRY_VERIFICATION.md")
    ap.add_argument("--results", default=None,
                    help="optional parquet path for the full results table")
    args = ap.parse_args()

    m = pd.read_parquet(args.matrix)
    npis = [x.strip() for x in args.npis.split(",")] if args.npis else None
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    res = verify_providers(m, top=args.top, rank_col=args.rank_col, npis=npis,
                           assessable_only=not args.all, retrieved_utc=stamp)
    Path(args.out).write_text(to_markdown(res), encoding="utf-8")
    if args.results:
        res.to_parquet(args.results, index=False)
    n = len(res)
    flagged = int((res["registry_flag"] != "OK").sum()) if n else 0
    print(f"[nppes_verify] {n} leads verified, {flagged} flagged → {args.out}")


if __name__ == "__main__":
    main()
