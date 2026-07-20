"""
code_rings.py — clusters of INDIVIDUAL NPIs billing a facility-only code.

The Rhode Island lead case had a shape single-entity scoring missed: nine
individual physicians billing a hospice room-and-board code (T2046) that,
nationally, is billed almost entirely by hospice ORGANIZATIONS. No one provider
looked more than "high on one code"; the signal was that a *facility-type code*
was concentrated on *personal NPIs*, in one state, across several people.

This detector generalizes that finding so the next such ring surfaces
automatically instead of by hand:

  1. For every code, measure the ORGANIZATION share of its dollars nationally.
     A code billed >= ``org_share_min`` by organizations is a "facility-type"
     code: individuals billing it are anomalous by construction.
  2. Among the individual (entity type 1) billers of those codes, cluster by
     (code, state). >= ``min_members`` individuals billing one facility-type
     code in one state is a candidate ring.
  3. Within each ring, surface the shared-office sub-clusters (members at the
     same address or phone) — the tight core an investigation starts from.

Pure and grain-agnostic: it takes a per-(npi, code) billing frame with entity
type, state, and optional address/phone, and returns one row per candidate
ring. Output is investigative leads for counsel review, never an accusation.
"""

from __future__ import annotations

import pandas as pd

_RING_COLS = ["hcpcs", "practice_state", "n_members", "total_paid",
              "code_org_share", "n_office_clusters", "max_shared_office",
              "member_npis"]


def _num(df: pd.DataFrame, col: str):
    return pd.to_numeric(df[col], errors="coerce").fillna(0.0)


def facility_code_rings(provider_code: pd.DataFrame,
                        org_share_min: float = 0.90,
                        min_members: int = 3,
                        min_member_paid: float = 50_000.0,
                        min_code_total: float = 1_000_000.0) -> pd.DataFrame:
    """Candidate rings of individuals billing an organization-dominated code.

    ``provider_code`` columns: ``npi``, ``hcpcs``, ``paid``, ``entity_type``
    ('1' individual / '2' organization), ``practice_state``; optional
    ``addr_key`` and ``phone`` sharpen the shared-office sub-clustering. One row
    per (code, state) candidate ring, strongest first.
    """
    cols = _RING_COLS
    need = {"npi", "hcpcs", "paid", "entity_type", "practice_state"}
    if provider_code is None or not len(provider_code) or \
            not need.issubset(provider_code.columns):
        return pd.DataFrame(columns=cols)

    d = provider_code.copy()
    d["npi"] = d["npi"].astype(str)
    d["hcpcs"] = d["hcpcs"].astype(str)
    d["entity_type"] = d["entity_type"].astype(str).str.strip()
    d["practice_state"] = d["practice_state"].fillna("").astype(str).str.upper()
    d["paid"] = _num(d, "paid")

    # 1. per-code organization share of dollars, national
    code_tot = d.groupby("hcpcs")["paid"].sum().rename("code_total")
    org_tot = (d[d["entity_type"] == "2"].groupby("hcpcs")["paid"].sum()
               .rename("org_paid"))
    code = pd.concat([code_tot, org_tot], axis=1).fillna({"org_paid": 0.0})
    code["org_share"] = code["org_paid"] / code["code_total"].where(code["code_total"] > 0, 1.0)
    facility_codes = set(code.index[(code["org_share"] >= org_share_min)
                                    & (code["code_total"] >= min_code_total)])
    if not facility_codes:
        return pd.DataFrame(columns=cols)

    # 2. individual billers of those codes, meaningful volume, grouped (code, state)
    ind = d[(d["entity_type"] == "1") & d["hcpcs"].isin(facility_codes)].copy()
    ind = ind[ind["paid"] >= min_member_paid]
    if not len(ind):
        return pd.DataFrame(columns=cols)

    has_addr = "addr_key" in ind.columns
    has_phone = "phone" in ind.columns

    rows = []
    for (hcpcs, state), g in ind.groupby(["hcpcs", "practice_state"]):
        members = g.drop_duplicates("npi")
        if len(members) < min_members:
            continue
        # shared-office sub-clusters: how many members share an address or phone
        office_clusters = 0
        max_shared = 1
        if has_addr or has_phone:
            keyparts = []
            if has_addr:
                keyparts.append(members["addr_key"].fillna("").astype(str))
            if has_phone:
                keyparts.append(members["phone"].fillna("").astype(str))
            key = keyparts[0]
            for extra in keyparts[1:]:
                key = key.where(key != "", extra)     # addr, else phone
            sizes = key[key != ""].value_counts()
            shared = sizes[sizes >= 2]
            office_clusters = int(len(shared))
            max_shared = int(shared.max()) if len(shared) else 1
        rows.append({
            "hcpcs": hcpcs, "practice_state": state,
            "n_members": int(len(members)),
            "total_paid": round(float(g["paid"].sum()), 2),
            "code_org_share": round(float(code.loc[hcpcs, "org_share"]), 4),
            "n_office_clusters": office_clusters,
            "max_shared_office": max_shared,
            "member_npis": "; ".join(sorted(members["npi"])),
        })
    if not rows:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(rows, columns=cols)
    # strongest first: dollars, then members, then how facility-only the code is
    return out.sort_values(["total_paid", "n_members", "code_org_share"],
                           ascending=False).reset_index(drop=True)


def build_provider_code(spending_path: str, provider_dim_path: str,
                        min_paid: float = 10_000.0) -> pd.DataFrame:
    """Join the spending fact to provider_dim → the per-(npi, hcpcs) input this
    detector needs, in one DuckDB scan (the provider x code matrix never enters
    pandas). Emits npi, hcpcs, paid, entity_type, practice_state, addr_key.

    Keeps only (npi, code) pairs above ``min_paid`` so the individual-biller
    filter downstream is not swamped by de-minimis lines. Column names follow
    the pipeline: spending fact = billing_npi / hcpcs_code / total_paid;
    provider_dim = npi / entity_type / practice_state|addr_state / addr_key.
    """
    import duckdb
    con = duckdb.connect()

    # resolve the spending-fact columns defensively — the canonical fact uses
    # billing_npi / hcpcs_code / total_paid, but an older or re-exported fact may
    # use variants. Fail loudly with the actual header if none match.
    fact_cols = con.execute(
        f"SELECT * FROM read_parquet('{spending_path}') LIMIT 0").df().columns.tolist()

    def _pick(aliases: list[str], what: str) -> str:
        low = {c.lower(): c for c in fact_cols}
        for a in aliases:
            if a.lower() in low:
                return low[a.lower()]
        raise ValueError(f"spending fact has no {what} column (tried {aliases}); "
                         f"saw {fact_cols[:12]}")

    npi_c = _pick(["billing_npi", "npi", "billing_provider_npi_num"], "billing NPI")
    code_c = _pick(["hcpcs_code", "hcpcs", "hcpcs_cd", "code"], "HCPCS")
    paid_c = _pick(["total_paid", "net_paid", "paid", "paid_amt"], "paid amount")

    dim = pd.read_parquet(provider_dim_path)
    dim["npi"] = dim["npi"].astype(str)
    state_col = next((c for c in ("practice_state", "addr_state", "provider_state",
                                  "state") if c in dim.columns), None)
    keep = ["npi", "entity_type"] + ([state_col] if state_col else []) \
        + (["addr_key"] if "addr_key" in dim.columns else [])
    d = dim[keep].rename(columns={state_col: "practice_state"} if state_col else {})
    if "practice_state" not in d.columns:
        d["practice_state"] = ""
    if "addr_key" not in d.columns:
        d["addr_key"] = ""
    con.register("dim", d)
    out = con.execute(f"""
        SELECT CAST(s."{npi_c}" AS VARCHAR)      AS npi,
               CAST(s."{code_c}" AS VARCHAR)     AS hcpcs,
               SUM(CAST(s."{paid_c}" AS DOUBLE)) AS paid,
               ANY_VALUE(dim.entity_type)        AS entity_type,
               ANY_VALUE(dim.practice_state)     AS practice_state,
               ANY_VALUE(dim.addr_key)           AS addr_key
        FROM read_parquet('{spending_path}') s
        JOIN dim ON CAST(s."{npi_c}" AS VARCHAR) = dim.npi
        GROUP BY 1, 2
        HAVING SUM(CAST(s."{paid_c}" AS DOUBLE)) >= {float(min_paid)}
    """).df()
    con.close()
    out["entity_type"] = out["entity_type"].astype(str).str.strip()
    return out


def to_markdown(rings: pd.DataFrame) -> str:
    L = ["# FACILITY-CODE RINGS — individuals billing organization-only codes", ""]
    L.append("_A code billed almost entirely by organizations nationally, but "
             "concentrated on personal NPIs in one state across several people, "
             "is the Rhode Island lead-case shape. Each row is a candidate ring: "
             "the code, the state, the members, and how many share an office._")
    L.append("")
    if not len(rings):
        L.append("**No facility-code rings surfaced at the current thresholds.**")
        return "\n".join(L)
    L.append(f"- Candidate rings: **{len(rings)}**")
    L.append("")
    L.append("| code | state | members | total paid | code is org-billed | shared offices | biggest office |")
    L.append("|---|---|--:|--:|--:|--:|--:|")
    for r in rings.head(30).itertuples():
        L.append(f"| {r.hcpcs} | {r.practice_state} | {r.n_members} | "
                 f"${r.total_paid:,.0f} | {r.code_org_share:.0%} | "
                 f"{r.n_office_clusters} | {r.max_shared_office} |")
    L.append("")
    L.append("_Investigative leads for counsel review. A ring is a billing "
             "pattern to examine, not a finding of fraud. Confirm each code's "
             "billing rules against the relevant state Medicaid manual._")
    return "\n".join(L)


def main() -> None:
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider-code", default=None,
                    help="parquet: npi, hcpcs, paid, entity_type, practice_state "
                         "(+ optional addr_key, phone). If omitted, build it from "
                         "--spending + --provider-dim.")
    ap.add_argument("--spending", default=None, help="processed/spending_fact.parquet")
    ap.add_argument("--provider-dim", default=None, help="processed/provider_dim.parquet")
    ap.add_argument("--org-share-min", type=float, default=0.90)
    ap.add_argument("--min-members", type=int, default=3)
    ap.add_argument("--out", default="FACILITY_CODE_RINGS.md")
    ap.add_argument("--results", default=None)
    args = ap.parse_args()
    if args.provider_code:
        pc = pd.read_parquet(args.provider_code)
    elif args.spending and args.provider_dim:
        pc = build_provider_code(args.spending, args.provider_dim)
    else:
        ap.error("give --provider-code, or both --spending and --provider-dim")
    rings = facility_code_rings(pc, org_share_min=args.org_share_min,
                                min_members=args.min_members)
    Path(args.out).write_text(to_markdown(rings), encoding="utf-8")
    if args.results:
        rings.to_parquet(args.results, index=False)
    print(f"[code_rings] {len(rings)} candidate rings → {args.out}")


if __name__ == "__main__":
    main()
