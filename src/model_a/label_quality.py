"""
label_quality.py — is the harvested case label GOOD? Measured, not assumed.

An agent-harvested label (DOJ/MFCU cases collected by browser agents) can
hallucinate rows, miss cases, or mangle details. Because every row carries a
citation, quality is measurable on three axes, and this module produces the
report + the working papers for all three:

  PRECISION   a stratified verification SAMPLE SHEET (every high-dollar row +
              N random rows per state) with columns for the reviewer verdict —
              fetch the cited URL, confirm defendant/outcome/amount/date. The
              filled sheet yields a per-state precision rate.
  RECALL      per-state × per-year counts of resolved outcomes, laid out for
              comparison against the official MFCU statistical reports (each
              state MFCU reports convictions/settlements to OIG annually — an
              exact public benchmark). Pass the curated benchmark CSV when it
              exists; the table renders with or without it.
  CONVERGENT  overlap between case defendants and the exclusion sources we
  VALIDITY    already trust (LEIE + state lists): resolved fraud defendants
              should often also be excluded. The overlap corroborates; the
              non-overlap is either the incremental value (settlements without
              exclusions) or the noise — the precision sample says which.

Plus mechanical sanity checks: date/amount parse rates, conduct-window
coherence (start <= end <= announcement year + 1), primary-source share of
the cited domains, duplicate case_ids.

Diagnostics for the operator and the audit trail — never itself a label.

  python -m src.model_a.label_quality --harvest-dir .../enforcement/harvest \
      --exclusions ".../processed/exclusions*.parquet" \
      --out LABEL_QUALITY.md --sample-out verification_sample.csv
"""

from __future__ import annotations

import argparse
import glob as _glob
import re
from pathlib import Path

import numpy as np
import pandas as pd

from src.entity_graph.resolve_entities import norm_org_name

PRIMARY_DOMAINS = re.compile(
    r"(?:justice\.gov|oig\.hhs\.gov|\.gov/|\.gov$|courtlistener\.com|"
    r"namfcu\.net)", re.IGNORECASE)
_TOKEN = re.compile(r"[a-z]{2,}")


def person_key(name: str) -> str:
    """Order-free person-name key: 'Karina Renee Moore' == 'MOORE, KARINA RENEE'.

    Sorted lowercase tokens (len >= 2), so comma-reversed registry names match
    press-release names. Conservative diagnostic key — used to MEASURE overlap,
    never to auto-join a label."""
    return " ".join(sorted(_TOKEN.findall(str(name or "").lower())))


def load_harvest(harvest_dir: str | Path, pattern: str = "cases_REVIEW_*.csv") -> pd.DataFrame:
    """Concatenate the per-window review CSVs; a `window` column tags each."""
    frames = []
    for p in sorted(Path(harvest_dir).glob(pattern)):
        try:
            df = pd.read_csv(p, dtype=str)
        except Exception:
            continue
        if not len(df):
            continue
        df["window"] = p.stem.replace("cases_REVIEW_", "")
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["state"] = out["window"].str.split("_").str[0]
    return out


def sanity_checks(cases: pd.DataFrame) -> dict:
    """Mechanical row-quality checks; each is a rate in [0, 1]."""
    n = len(cases)
    if not n:
        return {"n": 0}
    ann = pd.to_datetime(cases.get("announced_date"), errors="coerce")
    amt = pd.to_numeric(cases.get("amount_usd"), errors="coerce")
    urls = cases.get("source_url", pd.Series("", index=cases.index)).fillna("")
    yr = ann.dt.year
    # conduct years recovered from the summary the same way case_labels does
    from src.model_a.case_labels import extract_conduct_window
    spans = [extract_conduct_window(s, a) for s, a in
             zip(cases.get("summary", ""), cases.get("announced_date", ""))]
    coherent = sum(1 for (s, e), y in zip(spans, yr)
                   if s is not None and e is not None and s <= e
                   and (pd.isna(y) or e <= int(y) + 1))
    dup = int(cases.duplicated(subset=["case_id"]).sum()) if "case_id" in cases else 0
    return {
        "n": n,
        "date_parse_rate": float(ann.notna().mean()),
        "amount_present_rate": float(amt.notna().mean()),
        "primary_source_share": float(urls.str.contains(PRIMARY_DOMAINS).mean()),
        "conduct_window_coherent_rate": coherent / n,
        "duplicate_case_ids": dup,
    }


def overlap_with_exclusions(cases: pd.DataFrame, exclusions: pd.DataFrame) -> dict:
    """Share of case defendants that also appear on an exclusion source.

    Matches org name-keys AND order-free person keys. Returns counts plus the
    matched subset (defendant, matched exclusion name, source) for eyeballing.
    A defendant matching a 6,000-row exclusion universe by name is EVIDENCE of
    convergence, not an identity assertion."""
    if not len(cases) or exclusions is None or not len(exclusions):
        return {"n_cases": int(len(cases)), "n_matched": 0, "rate": float("nan"),
                "matched": pd.DataFrame()}
    ex = exclusions.copy()
    name_col = "entity_name" if "entity_name" in ex.columns else ex.columns[0]
    ex["_org_key"] = ex[name_col].map(norm_org_name)
    ex["_person_key"] = ex[name_col].map(person_key)
    org_keys = set(ex.loc[ex["_org_key"].str.len() >= 8, "_org_key"])
    person_keys = set(ex.loc[ex["_person_key"].str.len() >= 7, "_person_key"])

    c = cases.copy()
    c["_org_key"] = c["defendant_name"].map(norm_org_name)
    c["_person_key"] = c["defendant_name"].map(person_key)
    hit = (c["_org_key"].isin(org_keys) & (c["_org_key"].str.len() >= 8)) | \
          (c["_person_key"].isin(person_keys) & (c["_person_key"].str.len() >= 7))
    matched = c.loc[hit, ["defendant_name", "state", "outcome_type"]].copy()
    return {"n_cases": int(len(c)), "n_matched": int(hit.sum()),
            "rate": float(hit.mean()), "matched": matched}


def per_state_year_counts(cases: pd.DataFrame,
                          benchmark: pd.DataFrame | None = None) -> pd.DataFrame:
    """Resolved-outcome counts by state × announcement year, with the official
    MFCU benchmark column joined when a curated benchmark CSV
    (state, year, official_count) is supplied."""
    if not len(cases):
        return pd.DataFrame()
    c = cases.copy()
    c["year"] = pd.to_datetime(c.get("announced_date"), errors="coerce").dt.year
    tier = c.get("label_tier", pd.Series("resolved", index=c.index)).fillna("resolved")
    counts = (c[tier == "resolved"].groupby(["state", "year"], dropna=True)
              .size().reset_index(name="harvested_resolved"))
    if benchmark is not None and len(benchmark):
        b = benchmark.copy()
        b["year"] = pd.to_numeric(b["year"], errors="coerce")
        counts = counts.merge(b, on=["state", "year"], how="left")
        counts["recall_proxy"] = (counts["harvested_resolved"]
                                  / pd.to_numeric(counts["official_count"],
                                                  errors="coerce"))
    return counts.sort_values(["state", "year"]).reset_index(drop=True)


def verification_sample(cases: pd.DataFrame, per_state: int = 5,
                        big_dollar: float = 1_000_000.0,
                        max_big: int = 100, seed: int = 42) -> pd.DataFrame:
    """The precision working paper: the LARGEST ``max_big`` rows at/above
    big_dollar plus per_state random rows per state, with empty reviewer
    columns (verified yes/no + note). Deterministic so the sheet is
    reproducible. Capped — the first live harvest carried ~1,500 rows over
    $1M (multistate settlements), and an unreviewable sample is no sample."""
    if not len(cases):
        return pd.DataFrame()
    amt = pd.to_numeric(cases.get("amount_usd"), errors="coerce").fillna(0.0)
    big = (cases.assign(_amt=amt)[amt >= big_dollar]
           .sort_values("_amt", ascending=False).head(max_big)
           .drop(columns=["_amt"]))
    rng = np.random.RandomState(seed)
    rest = []
    for st, grp in cases[amt < big_dollar].groupby("state"):
        take = min(per_state, len(grp))
        rest.append(grp.iloc[rng.choice(len(grp), size=take, replace=False)])
    sample = pd.concat([big] + rest, ignore_index=True) if rest else big.copy()
    sample = sample.drop_duplicates(subset=["case_id", "defendant_name"])
    keep = [c for c in ["state", "window", "announced_date", "defendant_name",
                        "outcome_type", "amount_usd", "scheme", "source_url",
                        "summary"] if c in sample.columns]
    out = sample[keep].copy()
    out["verified"] = ""          # reviewer fills: yes / no / partial
    out["review_note"] = ""
    return out.reset_index(drop=True)


def to_markdown(sanity: dict, overlap: dict, counts: pd.DataFrame,
                n_sample: int) -> str:
    L = ["# LABEL QUALITY — the harvested case label, measured", ""]
    L.append(f"- case rows examined: {sanity.get('n', 0):,}")
    if sanity.get("n"):
        L.append("")
        L.append("## Sanity (mechanical)")
        L.append("")
        L.append("| check | value |")
        L.append("|---|--:|")
        L.append(f"| announcement date parses | {sanity['date_parse_rate']:.0%} |")
        L.append(f"| dollar amount present | {sanity['amount_present_rate']:.0%} |")
        L.append(f"| cited to a primary domain | {sanity['primary_source_share']:.0%} |")
        L.append(f"| conduct window coherent | {sanity['conduct_window_coherent_rate']:.0%} |")
        L.append(f"| duplicate case ids | {sanity['duplicate_case_ids']} |")
    L.append("")
    L.append("## Convergent validity (defendants also on exclusion lists)")
    L.append("")
    L.append(f"- {overlap['n_matched']:,} of {overlap['n_cases']:,} defendants "
             f"({overlap['rate']:.0%}) name-match an exclusion source. The "
             "overlap corroborates both labels; the remainder is either the "
             "incremental value (settlements without exclusions) or noise — "
             "the precision sample decides which.")
    if len(counts):
        L.append("")
        L.append("## Recall proxy (vs official MFCU statistics when supplied)")
        L.append("")
        cols = list(counts.columns)
        L.append("| " + " | ".join(cols) + " |")
        L.append("|" + "---|" * len(cols))
        for _, r in counts.iterrows():
            L.append("| " + " | ".join("" if pd.isna(v) else
                                       (f"{v:.0%}" if c == "recall_proxy"
                                        else str(int(v) if isinstance(v, float)
                                                 and v == int(v) else v))
                                       for c, v in zip(cols, r)) + " |")
    L.append("")
    L.append(f"_Precision working paper: {n_sample} rows in the verification "
             "sample sheet — fetch each cited URL, confirm defendant/outcome/"
             "amount/date, fill the `verified` column. Diagnostics only; this "
             "report never becomes a label._")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--harvest-dir", required=True)
    ap.add_argument("--exclusions", default=None,
                    help="glob of exclusion parquets (LEIE + state lists)")
    ap.add_argument("--benchmark", default=None,
                    help="curated CSV: state,year,official_count (MFCU reports)")
    ap.add_argument("--per-state", type=int, default=5)
    ap.add_argument("--out", default="LABEL_QUALITY.md")
    ap.add_argument("--sample-out", default="verification_sample.csv")
    args = ap.parse_args()

    cases = load_harvest(args.harvest_dir)
    excl = None
    if args.exclusions:
        parts = [pd.read_parquet(p) for p in sorted(_glob.glob(args.exclusions))]
        excl = pd.concat(parts, ignore_index=True) if parts else None
    bench = pd.read_csv(args.benchmark, dtype=str) if args.benchmark else None

    sanity = sanity_checks(cases)
    overlap = overlap_with_exclusions(cases, excl)
    counts = per_state_year_counts(cases, bench)
    sample = verification_sample(cases, per_state=args.per_state)
    sample.to_csv(args.sample_out, index=False)
    Path(args.out).write_text(
        to_markdown(sanity, overlap, counts, len(sample)), encoding="utf-8")
    print(f"[label_quality] {sanity.get('n', 0):,} rows | overlap "
          f"{overlap['rate']:.0%} | sample {len(sample)} -> {args.sample_out} | "
          f"report -> {args.out}")


if __name__ == "__main__":
    main()
