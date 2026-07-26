"""
harvest_join_audit.py — can each harvested case row ATTACH to our entities?

A label row that cannot be joined to a provider or organization trains
nothing: the harvest's value is measured in JOINABLE rows, not rows. This
audit classifies every harvested defendant by its best available join path,
in precedence order:

  direct_npi          an NPI printed in the source itself (strongest)
  candidate_npi       an NPPES candidate reported by the agent (human review)
  org_name_match      org name-key matches an org node in our graph (the join
                      path case_labels actually uses)
  nppes_unique_name   the person's order-free name key matches EXACTLY ONE
                      NPPES individual in the case's state (local NPPES
                      matching — free, no agent needed)
  nppes_ambiguous     name matches several NPPES rows — needs a discriminator
                      (address, specialty) to resolve
  unmatched           nothing attaches

For the unmatched/ambiguous remainder it asks the feasibility question the
operator posed: is the missing identifier plausibly ON the public internet?
A physician or an agency almost certainly has an NPI/registration to find; a
beneficiary, an office manager, or an unenrolled aide usually does not, and
no amount of agent work will conjure one. Rows classified likely/unknown are
emitted as ``gaps_for_manus.csv`` — the go-back-and-fill worklist for the
identifier-enrichment task.

Diagnostics + worklists only; nothing here asserts an identity or becomes a
label without review.

  python -m src.model_a.harvest_join_audit --harvest-dir .../enforcement/harvest \
      --provider-dim .../processed/provider_dim.parquet \
      --org-nodes .../graph/nodes/org_nodes.parquet \
      --out JOIN_AUDIT.md --gaps-out gaps_for_manus.csv
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name
from src.model_a.label_quality import load_harvest, person_key

# roles that typically have NO public provider identifier: agent work cannot
# conjure an NPI that was never issued.
_UNLIKELY = re.compile(
    r"beneficiar|office manager|office administrator|bookkeeper|biller\b|"
    r"billing manager|receptionist|patient recruiter|marketer|"
    r"personal care aide|caregiver|home care aide|sales rep", re.IGNORECASE)
# markers that an identifier almost certainly exists publicly
_LIKELY = re.compile(
    r"\b(m\.?d\.?|d\.?o\.?|d\.?d\.?s\.?|n\.?p\.?|p\.?a\.?-?c?|r\.?n\.?)\b|"
    r"physician|doctor|dentist|nurse|pharmac|chiropract|therapist|podiatr|"
    r"psycholog|psychiatr|clinic|center|agency|home health|hospice|"
    r"laborator|\bllc\b|\binc\b|\bcorp\b|\bp\.?s\.?\b|\bp\.?c\.?\b",
    re.IGNORECASE)


def _pick(df: pd.DataFrame, *cands: str) -> str | None:
    for c in cands:
        if c in df.columns:
            return c
    return None


def load_candidates(harvest_dir: str | Path) -> pd.DataFrame:
    """Concatenate the npi_candidates_REVIEW_*.csv files."""
    frames = []
    for p in sorted(Path(harvest_dir).glob("npi_candidates_REVIEW_*.csv")):
        try:
            df = pd.read_csv(p, dtype=str)
            if len(df):
                frames.append(df)
        except Exception:
            continue
    return (pd.concat(frames, ignore_index=True)
            if frames else pd.DataFrame(columns=["case_id", "defendant_name",
                                                 "npi"]))


def classify_joins(cases: pd.DataFrame, candidates: pd.DataFrame,
                   provider_dim: pd.DataFrame | None = None,
                   org_nodes: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per-row join_status + gap_feasibility (see module docstring)."""
    if not len(cases):
        return cases.copy()
    # CSVs read with dtype=str still yield NaN for empty cells, and NaN is
    # TRUTHY in python — str(nan) == "nan" classified every row direct_npi on
    # the first live run. Blank everything textual up front.
    c = cases.copy().fillna("")
    c = c.replace({"nan": "", "None": ""})
    c["_org_key"] = c["defendant_name"].map(norm_org_name)
    c["_person_key"] = c["defendant_name"].map(person_key)

    # NPIs in OUR universe: a found NPI only matters if that provider ever
    # appears in our billing data — a label with no provider to land on is
    # worthless, and hunting identifiers for out-of-universe defendants is
    # wasted agent spend.
    universe_npis: set = set()
    if provider_dim is not None and len(provider_dim) and "npi" in provider_dim:
        universe_npis = set(provider_dim["npi"].astype(str).str.strip())

    cand_npi_by_key: dict = {}
    if candidates is not None and len(candidates):
        has_npi = candidates["npi"].fillna("").str.strip() != ""
        for cid, dname, npi in zip(candidates.loc[has_npi, "case_id"].astype(str),
                                   candidates.loc[has_npi, "defendant_name"],
                                   candidates.loc[has_npi, "npi"].astype(str)):
            cand_npi_by_key.setdefault((cid, person_key(dname)), []).append(
                npi.strip())

    org_keys: set = set()
    if org_nodes is not None and len(org_nodes):
        col = _pick(org_nodes, "name_key", "org_name", "display_name", "name")
        if col:
            keys = (org_nodes[col].map(norm_org_name)
                    if col != "name_key" else org_nodes[col].fillna(""))
            org_keys = set(k for k in keys if isinstance(k, str) and len(k) >= 8)
    # blocked fuzzy candidates: org keys grouped by first token, so the
    # difflib pass compares against dozens, not the whole universe
    org_blocks: dict = {}
    for k in org_keys:
        org_blocks.setdefault(k.split(" ", 1)[0], []).append(k)

    # local NPPES person matching: order-free name key, individuals only.
    # In-state unique is the strong match; nationally unique is the fallback
    # (defendants often bill from a neighboring state).
    pd_state: dict = {}
    pd_national: dict = {}
    if provider_dim is not None and len(provider_dim):
        name_col = _pick(provider_dim, "name_key", "display_name",
                         "provider_name", "name")
        state_col = _pick(provider_dim, "addr_state", "state", "pecos_state")
        ent_col = _pick(provider_dim, "entity_type")
        if name_col:
            pdim = provider_dim
            if ent_col:
                ent = pdim[ent_col].astype(str).str.strip()
                pdim = pdim[ent.isin(("1", "1.0", "I", "individual"))]
            keys = pdim[name_col].map(person_key)
            states = (pdim[state_col].fillna("").str.upper()
                      if state_col else pd.Series("", index=pdim.index))
            counts = pd.DataFrame({"k": keys, "s": states})
            counts = counts[counts["k"].str.len() >= 7]
            pd_state = counts.groupby(["k", "s"]).size().to_dict()
            pd_national = counts.groupby("k").size().to_dict()

    def _fuzzy_org(key):
        """case_labels' conservative difflib pass, blocked by first token."""
        import difflib
        if len(key) < 8:
            return False
        block = org_blocks.get(key.split(" ", 1)[0], [])
        if not block or len(block) > 2000:
            return False
        return bool(difflib.get_close_matches(key, block, n=1, cutoff=0.92))

    def _status(row):
        src_npi = str(row.get("npi_in_source") or "").strip()
        if src_npi:
            return ("direct_npi" if not universe_npis
                    or src_npi in universe_npis else "npi_outside_universe")
        cands = cand_npi_by_key.get((str(row.get("case_id")),
                                     row["_person_key"]))
        if cands:
            if not universe_npis or any(n in universe_npis for n in cands):
                return "candidate_npi"
            return "npi_outside_universe"
        if len(row["_org_key"]) >= 8 and row["_org_key"] in org_keys:
            return "org_name_match"
        if pd_state and len(row["_person_key"]) >= 7:
            n = pd_state.get((row["_person_key"],
                              str(row.get("state") or "").upper()), 0)
            if n == 1:
                return "nppes_unique_name"
            if n > 1:
                return "nppes_ambiguous"
            if pd_national.get(row["_person_key"], 0) == 1:
                return "nppes_unique_national"
        if _fuzzy_org(row["_org_key"]):
            return "org_fuzzy_match"
        return "unmatched"

    c["join_status"] = c.apply(_status, axis=1)

    def _feasibility(row):
        if row["join_status"] == "npi_outside_universe":
            # identifier FOUND, provider simply never bills in our data — no
            # enrichment can change that; case-level fact only.
            return "outside_universe"
        if row["join_status"] not in ("unmatched", "nppes_ambiguous"):
            return ""
        text = f"{row.get('defendant_name', '')} {row.get('summary', '')}"
        if _UNLIKELY.search(text) and not _LIKELY.search(
                str(row.get("defendant_name", ""))):
            return "unlikely_public"        # no identifier plausibly exists
        if _LIKELY.search(text):
            return "likely_public"          # go get it
        return "unknown"

    c["gap_feasibility"] = c.apply(_feasibility, axis=1)
    return c.drop(columns=["_org_key", "_person_key"])


JOINABLE = ["direct_npi", "candidate_npi", "org_name_match",
            "org_fuzzy_match", "nppes_unique_name", "nppes_unique_national"]


def gaps_worklist(classified: pd.DataFrame) -> pd.DataFrame:
    """The go-back-to-Manus rows: unmatched/ambiguous where an identifier is
    plausibly findable. Carries the context the enrichment task needs."""
    if not len(classified):
        return pd.DataFrame()
    m = classified[classified["join_status"].isin(["unmatched",
                                                   "nppes_ambiguous"])
                   & classified["gap_feasibility"].isin(["likely_public",
                                                         "unknown"])].copy()
    # PRIORITIZED: likely_public before unknown, big dollars first — the
    # enrichment spend starts where a recovered identifier moves the label
    # most, and the long tail of tiny cases can wait for the label to prove
    # itself.
    m["_amt"] = pd.to_numeric(m.get("amount_usd"), errors="coerce").fillna(0.0)
    m["_feas"] = (m["gap_feasibility"] == "likely_public").astype(int)
    m = m.sort_values(["_feas", "_amt"], ascending=[False, False])
    keep = [c for c in ["state", "window", "defendant_name", "join_status",
                        "gap_feasibility", "outcome_type", "amount_usd",
                        "source_url", "summary"] if c in m.columns]
    return m[keep].reset_index(drop=True)


def to_markdown(classified: pd.DataFrame, gaps: pd.DataFrame) -> str:
    L = ["# HARVEST JOIN AUDIT — do the case rows attach to our entities?", ""]
    n = len(classified)
    L.append(f"- rows audited: {n:,}")
    if not n:
        return "\n".join(L)
    L.append("")
    L.append("| join path | rows | share |")
    L.append("|---|--:|--:|")
    order = JOINABLE + ["npi_outside_universe", "nppes_ambiguous", "unmatched"]
    vc = classified["join_status"].value_counts()
    for k in order:
        v = int(vc.get(k, 0))
        L.append(f"| {k} | {v:,} | {v / n:.0%} |")
    joinable = int(sum(vc.get(k, 0) for k in JOINABLE))
    L.append("")
    L.append(f"**Joinable now: {joinable:,} of {n:,} ({joinable / n:.0%}).**")
    fz = classified.loc[classified["gap_feasibility"] != "",
                        "gap_feasibility"].value_counts()
    if len(fz):
        L.append("")
        L.append("## The remainder — is the identifier on the public internet?")
        L.append("")
        L.append("| feasibility | rows |")
        L.append("|---|--:|")
        for k, v in fz.items():
            L.append(f"| {k} | {int(v):,} |")
        L.append("")
        L.append(f"- go-back-to-Manus worklist (likely/unknown): {len(gaps):,} "
                 "rows -> gaps_for_manus.csv (identifier-enrichment batches)")
        L.append("- unlikely_public rows (beneficiaries, office managers, "
                 "unenrolled aides) have no identifier to find; they remain "
                 "case-level facts, never provider labels.")
    L.append("")
    st = (classified.assign(ok=classified["join_status"]
                            .isin(JOINABLE)).groupby("state")["ok"].mean()
          .sort_values())
    L.append("## Joinability by state (worst first)")
    L.append("")
    L.append("| state | joinable |")
    L.append("|---|--:|")
    for s, v in st.items():
        L.append(f"| {s} | {v:.0%} |")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--harvest-dir", required=True)
    ap.add_argument("--provider-dim", default=None)
    ap.add_argument("--org-nodes", default=None)
    ap.add_argument("--out", default="JOIN_AUDIT.md")
    ap.add_argument("--gaps-out", default="gaps_for_manus.csv")
    ap.add_argument("--priority-n", type=int, default=500,
                    help="also write the TOP-N prioritized gaps (big dollars, "
                         "likely_public first) as *_priority.csv — the "
                         "enrichment worklist that is actually worth agent "
                         "spend")
    args = ap.parse_args()

    cases = load_harvest(args.harvest_dir)
    cands = load_candidates(args.harvest_dir)
    pdim = pd.read_parquet(args.provider_dim) if args.provider_dim else None
    orgs = pd.read_parquet(args.org_nodes) if args.org_nodes else None

    classified = classify_joins(cases, cands, pdim, orgs)
    gaps = gaps_worklist(classified)
    gaps.to_csv(args.gaps_out, index=False)
    prio_path = Path(args.gaps_out).with_name(
        Path(args.gaps_out).stem + "_priority.csv")
    gaps.head(args.priority_n).to_csv(prio_path, index=False)
    classified.to_csv(Path(args.out).with_suffix(".rows.csv"), index=False)
    Path(args.out).write_text(to_markdown(classified, gaps), encoding="utf-8")
    n = len(classified)
    ok = int(classified["join_status"].isin(JOINABLE).sum()) if n else 0
    print(f"[join_audit] {n:,} rows | joinable {ok:,} "
          f"({(ok / n if n else 0):.0%}) | gaps {len(gaps):,} -> "
          f"{args.gaps_out} (top {min(args.priority_n, len(gaps))} -> "
          f"{prio_path.name}) | report -> {args.out}")


if __name__ == "__main__":
    main()
