"""
case_qc.py — quality screens for the agent-harvested case database.

Agent-harvested rows carry per-row citations, and spot checks verified a
sample, but a sample doesn't bound the tail: individual rows can still carry
impossible dollars, garbled dates, or duplicates. This module runs every row
through mechanical sanity screens and QUARANTINES (flags, never deletes):

  BAD_DATE        announced_date unparseable, before 1990, or in the future
  IMPOSSIBLE_$    negative; > $15B (a hard ceiling exceeded by no real
                  healthcare case); missing/zero on a settlement/judgment row
  REVIEW_$        > $2B (real but rare — belongs in the re-verify queue)
  CONDUCT_WINDOW  conduct years inverted, ending after the announcement, or
                  starting before 1980
  DUPLICATE       same normalized defendant + announcement year seen twice —
                  the later row is flagged, the first kept
  PENDING_TIER    allegation-stage outcomes (indictment/complaint/charges) —
                  not an error, but marked so they NEVER hard-label

Outputs (beside the input file unless --out-dir):
  doj_cases_qc.csv    every row + flag columns + qc_status
                      (clean / review / quarantine)
  CASE_QC.md          the summary a human reads
  reverify_batch.csv  the top-N flagged rows with their source URLs, ready to
                      feed back to the research agent for re-verification

  python -m src.model_a.case_qc --cases <doj_cases.csv> [--out-dir ...]
      [--top-reverify 40]

Rows never leave the file; downstream loaders (case_labels, the export) can
filter on qc_status themselves. Screens are mechanical; a flagged row is a
QUESTION for the citation, not a verdict.
"""

from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path

import pandas as pd

from src.model_a.case_labels import extract_conduct_window

HARD_AMOUNT_CEILING = 15_000_000_000     # no real healthcare case exceeds
REVIEW_AMOUNT = 2_000_000_000            # real but rare — re-verify
PENDING_OUTCOMES = {"indictment", "criminal_complaint", "civil_complaint",
                    "charges_filed"}
SETTLED_OUTCOMES = {"settlement", "judgment", "guilty_plea", "conviction",
                    "sentencing", "cia"}

_WS = re.compile(r"\s+")


def _norm_name(s: str) -> str:
    return _WS.sub(" ", re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower())).strip()


def _parse_year(s: str) -> int | None:
    s = str(s or "").strip()
    return int(s[:4]) if len(s) >= 4 and s[:4].isdigit() else None


def qc_frame(cases: pd.DataFrame, today: date | None = None) -> pd.DataFrame:
    """Append flag columns + qc_status. Never drops or edits a source row."""
    today = today or date.today()
    c = cases.copy()
    amount = pd.to_numeric(c.get("amount_usd"), errors="coerce")
    outcome = c.get("outcome_type", pd.Series("", index=c.index)).fillna("") \
        .astype(str).str.strip().str.lower()
    ann_year = c.get("announced_date", pd.Series("", index=c.index)) \
        .astype(str).map(_parse_year)

    c["flag_bad_date"] = [
        (y is None) or y < 1990 or y > today.year for y in ann_year]

    c["flag_impossible_amount"] = (
        (amount < 0) | (amount > HARD_AMOUNT_CEILING)
        | (outcome.isin(SETTLED_OUTCOMES) & (amount.isna() | (amount <= 0)))
    ).fillna(False)
    c["flag_review_amount"] = (
        (amount > REVIEW_AMOUNT) & (amount <= HARD_AMOUNT_CEILING)
    ).fillna(False)

    starts, ends = [], []
    for summ, ann in zip(c.get("summary", pd.Series("", index=c.index)),
                         c.get("announced_date",
                               pd.Series("", index=c.index))):
        s, e = extract_conduct_window(summ, str(ann))
        starts.append(s)
        ends.append(e)
    c["conduct_start_qc"] = starts
    c["conduct_end_qc"] = ends
    c["flag_conduct_window"] = [
        (s is not None and e is not None and s > e)
        or (s is not None and s < 1980)
        or (e is not None and y is not None and e > y)
        for s, e, y in zip(starts, ends, ann_year)]

    key = c.get("defendant_name", pd.Series("", index=c.index)) \
        .map(_norm_name) + "|" + pd.Series(ann_year, index=c.index).astype(str)
    c["flag_duplicate"] = key.duplicated(keep="first") & (key.str.len() > 6)

    c["flag_pending_tier"] = outcome.isin(PENDING_OUTCOMES)

    hard = c["flag_bad_date"] | c["flag_impossible_amount"] | \
        c["flag_conduct_window"] | c["flag_duplicate"]
    soft = c["flag_review_amount"]
    c["qc_status"] = "clean"
    c.loc[soft, "qc_status"] = "review"
    c.loc[hard, "qc_status"] = "quarantine"
    return c


def reverify_batch(qc: pd.DataFrame, top_n: int = 40) -> pd.DataFrame:
    """Flagged rows, biggest dollars first — the re-verification worklist."""
    bad = qc[qc["qc_status"].isin(["quarantine", "review"])].copy()
    bad["_amt"] = pd.to_numeric(bad.get("amount_usd"), errors="coerce") \
        .fillna(0)
    bad["what_to_check"] = bad.apply(
        lambda r: "; ".join(
            m for m, f in [
                ("re-read the announcement date", r.get("flag_bad_date")),
                ("confirm the dollar amount against the cited document",
                 r.get("flag_impossible_amount") or r.get(
                     "flag_review_amount")),
                ("confirm the conduct period", r.get("flag_conduct_window")),
                ("check whether this duplicates an earlier row",
                 r.get("flag_duplicate")),
            ] if f), axis=1)
    cols = [x for x in ["case_id", "defendant_name", "announced_date",
                        "amount_usd", "outcome_type", "source_url",
                        "qc_status", "what_to_check"] if x in bad.columns]
    return bad.sort_values("_amt", ascending=False).head(top_n)[cols]


def to_markdown(qc: pd.DataFrame) -> str:
    n = len(qc)
    counts = qc["qc_status"].value_counts()
    amount = pd.to_numeric(qc.get("amount_usd"), errors="coerce")
    L = ["# CASE QC — mechanical screens on the harvested case DB", ""]
    L.append(f"{n:,} rows: **{counts.get('clean', 0):,} clean**, "
             f"{counts.get('review', 0):,} review, "
             f"{counts.get('quarantine', 0):,} quarantined.")
    L.append("")
    L.append("| screen | rows flagged |")
    L.append("|---|--:|")
    for col, name in [("flag_bad_date", "bad announcement date"),
                      ("flag_impossible_amount", "impossible dollars"),
                      ("flag_review_amount", "review-size dollars (> $2B)"),
                      ("flag_conduct_window", "broken conduct window"),
                      ("flag_duplicate", "duplicate defendant+year"),
                      ("flag_pending_tier",
                       "allegation-stage (never hard-labels)")]:
        L.append(f"| {name} | {int(qc[col].sum()):,} |")
    L.append("")
    L.append(f"Clean-row dollars: ${amount[qc['qc_status'] == 'clean'].sum() / 1e9:,.1f}B "
             f"of ${amount.sum() / 1e9:,.1f}B total — quarantined dollars are "
             "excluded from any quoted total until re-verified.")
    L.append("")
    L.append("_Flags are questions for the citation, not verdicts. Rows are "
             "never deleted; downstream loaders filter on `qc_status`. Feed "
             "`reverify_batch.csv` back to the research agent to settle the "
             "flagged rows._")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--top-reverify", type=int, default=40)
    args = ap.parse_args()
    src = Path(args.cases)
    out = Path(args.out_dir) if args.out_dir else src.parent
    out.mkdir(parents=True, exist_ok=True)
    cases = pd.read_csv(src, dtype=str)
    qc = qc_frame(cases)
    qc.to_csv(out / "doj_cases_qc.csv", index=False)
    reverify_batch(qc, args.top_reverify).to_csv(
        out / "reverify_batch.csv", index=False)
    (out / "CASE_QC.md").write_text(to_markdown(qc), encoding="utf-8")
    n_bad = int((qc["qc_status"] != "clean").sum())
    print(f"[case_qc] {len(qc):,} rows, {n_bad:,} flagged -> "
          f"{out / 'CASE_QC.md'}")


if __name__ == "__main__":
    main()
