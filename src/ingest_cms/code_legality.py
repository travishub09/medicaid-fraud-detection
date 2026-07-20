"""
code_legality.py — the reviewed (code, state) -> who-may-bill reference layer.

The facility-code detector says "this code is 90% organization-billed
nationally, so an individual billing it is a statistical outlier." The billing-
rule memos (T2046 in RI, T2016 in NM, H0037 in TN/MS, ...) prove something
stronger: for a given (code, state) the code can be LEGALLY organization-only,
facility-only, or gated behind a special qualification. This reference table
carries that legal fact so the detector can escalate a ring from "statistical
outlier" to "the state's rules forbid an individual billing this," for every
provider, as a deterministic join.

DESIGN — this is a FROZEN REFERENCE ARTIFACT, not a live call:
  * Grain is (code, state), never per-provider. Built once with Manus research
    (feeds/manus_research.state_billing_rule_memo), reviewed by a human, then
    committed as a reviewed CSV under preclean/reference/code_legality/.
  * Versioned: every row carries build_date + authority_url so a run reproduces
    against a specific reference vintage (point-in-time discipline holds).
  * REVIEW-GATED: a wrong "org-only" rule mis-scores thousands of legitimate
    providers (blast radius >> a wrong dossier), so a row only counts when
    reviewed=Y. Unreviewed rows load but do not annotate.
  * The pipeline consumes the table; it never blocks on the research call.

The rule fact upgrades the RING REPORT and DOSSIERS (the human-escalation
layer). The trainable facility_code_share stays pure statistics on purpose:
the model learns the pattern, the legal rule sharpens the human read.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import read_csv_text

# the reviewed reference schema
CODE_LEGALITY_COLUMNS = [
    "code",             # HCPCS/CPT, string, uppercased
    "state",            # 2-letter, uppercased ('' = national rule)
    "individual_allowed",  # Y/N: may a Type 1 / personal NPI bill this code?
    "who_may_bill",     # plain-English billing-entity description
    "authority",        # short cite (manual name + page)
    "authority_url",    # primary-source URL
    "build_date",       # YYYY-MM-DD the row was researched
    "reviewed",         # Y/N: a human signed off (only Y rows annotate)
]

_YES = {"y", "yes", "true", "1", "t"}


def _yn(v) -> bool:
    return str(v).strip().lower() in _YES


def load_code_legality(path: str | Path) -> pd.DataFrame:
    """Load and normalize the reviewed reference table (CSV or parquet).

    Missing columns are filled empty; ``code``/``state`` are uppercased strings;
    ``individual_allowed`` and ``reviewed`` become booleans. Raises if the file
    has neither a code nor a state column (a wrong file, not an empty one)."""
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=CODE_LEGALITY_COLUMNS)
    df = (pd.read_parquet(p) if p.suffix == ".parquet"
          else read_csv_text(str(p)))
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "code" not in df.columns:
        raise ValueError(f"code_legality file {p.name} has no 'code' column; "
                         f"saw {list(df.columns)[:8]}")
    for c in CODE_LEGALITY_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df["code"] = df["code"].fillna("").astype(str).str.strip().str.upper()
    df["state"] = df["state"].fillna("").astype(str).str.strip().str.upper()
    df["individual_allowed_bool"] = df["individual_allowed"].map(_yn)
    df["reviewed_bool"] = df["reviewed"].map(_yn)
    return df[df["code"] != ""].reset_index(drop=True)


def org_only_lookup(legality: pd.DataFrame) -> dict[tuple[str, str], dict]:
    """Reviewed rows where an individual may NOT bill → {(code, state): row}.

    A national rule (blank state) is stored under state '' and applied to any
    state that has no more-specific row (the state-specific row wins)."""
    if legality is None or not len(legality):
        return {}
    good = legality[legality["reviewed_bool"] & ~legality["individual_allowed_bool"]]
    out: dict[tuple[str, str], dict] = {}
    for r in good.itertuples():
        out[(r.code, r.state)] = {"who_may_bill": getattr(r, "who_may_bill", ""),
                                  "authority": getattr(r, "authority", ""),
                                  "authority_url": getattr(r, "authority_url", ""),
                                  "build_date": getattr(r, "build_date", "")}
    return out


def annotate_rings(rings: pd.DataFrame, legality: pd.DataFrame) -> pd.DataFrame:
    """Add rule columns to a facility_code_rings table.

    For each ring (hcpcs, practice_state) look up the reviewed legality rule
    (state-specific first, then a national rule). Adds:
      * ``rule_org_only`` — bool: an individual is legally barred from this code
        in this state (a confirmed rule, not just a statistic);
      * ``rule_basis`` — the authority/citation for that rule;
      * ``rule_authority_url``.
    Rings with no reviewed rule keep rule_org_only False (statistical-only) —
    absence of a rule is not a rule that it is allowed."""
    out = rings.copy()
    if not len(out):
        for c in ("rule_org_only", "rule_basis", "rule_authority_url"):
            out[c] = pd.Series(dtype=object)
        return out
    lut = org_only_lookup(legality)
    flags, basis, urls = [], [], []
    for r in out.itertuples():
        code = str(getattr(r, "hcpcs", "")).upper()
        state = str(getattr(r, "practice_state", "")).upper()
        hit = lut.get((code, state)) or lut.get((code, ""))   # state, then national
        flags.append(bool(hit))
        basis.append(hit["authority"] if hit else "")
        urls.append(hit["authority_url"] if hit else "")
    out["rule_org_only"] = flags
    out["rule_basis"] = basis
    out["rule_authority_url"] = urls
    # rule-confirmed rings first, then by the existing order
    if "total_paid" in out.columns:
        out = out.sort_values(["rule_org_only", "total_paid"],
                              ascending=False).reset_index(drop=True)
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--legality", required=True, help="reviewed reference CSV/parquet")
    ap.add_argument("--rings", required=True, help="facility_code_rings parquet")
    ap.add_argument("--out", default="FACILITY_CODE_RINGS_RULED.md")
    ap.add_argument("--results", default=None)
    args = ap.parse_args()
    from src.entity_graph.code_rings import to_markdown
    leg = load_code_legality(args.legality)
    rings = annotate_rings(pd.read_parquet(args.rings), leg)
    n_rule = int(rings["rule_org_only"].sum()) if len(rings) else 0
    md = to_markdown(rings)
    md += (f"\n\n_Rule-confirmed org-only rings (state rules bar an individual "
           f"biller): **{n_rule}** of {len(rings)}. Confirmed rings cite a "
           f"primary source; the rest are statistical only._")
    Path(args.out).write_text(md, encoding="utf-8")
    if args.results:
        rings.to_parquet(args.results, index=False)
    print(f"[code_legality] {n_rule}/{len(rings)} rings rule-confirmed → {args.out}")


if __name__ == "__main__":
    main()
