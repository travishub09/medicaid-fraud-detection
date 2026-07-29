"""
case_corrections.py — apply agent re-verification verdicts to the case DB.

Input: the case file + case_corrections_REVIEW.csv (per-row verdicts from
record_reverification: confirmed / corrected / cannot_verify, with the value
the source actually states for each field and a verbatim quote).

Rules (quarantine-never-delete, every change carries provenance):
  confirmed      row marked verify_status=confirmed; values untouched.
  corrected      each field with an in-source value replaces the recorded
                 one; the OLD value is kept in orig_<field>; amount_kind is
                 recorded (settlement paid vs alleged scheme size vs
                 restitution); a replacement_url supersedes a dead citation
                 (original kept in orig_source_url).
  cannot_verify  verify_status=cannot_verify — the row stays but downstream
                 loaders must not hard-label from it (usable=0).
  duplicate      a note confirming this row duplicates another case marks
                 superseded=1 (usable=0) — dollars counted once, row kept.

The output total is split by amount kind: PAID dollars (settlements,
judgments, restitution, recoveries) vs ALLEGED dollars (billed/intended
scheme size) vs unknown. Only PAID is ever quoted as recovered money.

  python -m src.model_a.case_corrections --cases doj_cases.csv \
      --corrections case_corrections_REVIEW.csv --out doj_cases_v3.csv

Idempotent: v3 is a pure function of (cases, corrections); rerun after more
verdicts land and it regenerates.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

_DUP = re.compile(r"duplicat|same (case|settlement|action|announcement|"
                  r"press release|resolution)", re.IGNORECASE)
_PAREN = re.compile(r"\([^)]*\)")
_STAGE = re.compile(r"[–—-]\s*(sentenced|convicted|indicted|"
                    r"charged|pleads?( guilty)?|settle[ds]?)\s*$",
                    re.IGNORECASE)
_NONWORD = re.compile(r"[^a-z0-9 ]")


def _match_key(name: str) -> str:
    """Normalized defendant key tolerant of agent rephrasing: drops
    parentheticals, '– Sentenced'-style stage suffixes, punctuation, and
    corporate suffix tokens."""
    s = _PAREN.sub(" ", str(name or "").lower())
    s = _STAGE.sub(" ", s)
    s = _NONWORD.sub(" ", s)
    toks = [t for t in s.split() if t not in
            ("llc", "inc", "corp", "corporation", "plc", "group", "the",
             "of", "and", "co", "company", "companies", "pc", "pa", "md")]
    return " ".join(toks)
_ALLEGED = re.compile(r"alleg|billed|scheme size|intended|fraud loss",
                      re.IGNORECASE)
_PAID = re.compile(r"settle|restitution|judgment|recover|paid|penalt|"
                   r"forfeit|fine", re.IGNORECASE)


def classify_amount_kind(kind: str) -> str:
    s = str(kind or "")
    if _ALLEGED.search(s):
        return "alleged"          # alleged wins when both words appear:
    if _PAID.search(s):           # "restitution ... scheme was $200M" is
        return "paid"             # describing an alleged size context
    return "unknown"


def apply_corrections(cases: pd.DataFrame,
                      corrections: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    c = cases.copy()
    for col in ("defendant_name", "source_url", "announced_date",
                "amount_usd", "outcome_type"):
        if col not in c.columns:
            c[col] = ""
    for col in ("verify_status", "amount_kind", "amount_kind_class",
                "conduct_period_verified", "orig_amount_usd",
                "orig_announced_date", "orig_outcome_type",
                "orig_source_url", "correction_note"):
        if col not in c.columns:
            c[col] = ""
    c["superseded"] = c.get("superseded", pd.Series(0, index=c.index))
    c["superseded"] = 0
    c["usable"] = 1

    # index cases for matching: exact (name, url), then unique name
    by_key: dict = {}
    for i, (n, u) in enumerate(zip(c.get("defendant_name", ""),
                                   c.get("source_url", ""))):
        by_key.setdefault((str(n).strip(), str(u).strip()), []).append(i)
    by_name: dict = {}
    by_norm: dict = {}
    for i, n in enumerate(c.get("defendant_name", "")):
        by_name.setdefault(str(n).strip(), []).append(i)
        by_norm.setdefault(_match_key(n), []).append(i)

    stats = {"confirmed": 0, "corrected": 0, "cannot_verify": 0,
             "superseded": 0, "unmatched": [], "fields_changed": 0}
    for _, r in corrections.iterrows():
        name = str(r.get("defendant_name") or "").strip()
        url = str(r.get("source_url") or "").strip()
        norm = _match_key(name)
        idxs = (by_key.get((name, url))
                or (by_name.get(name)
                    if len(by_name.get(name, [])) == 1 else None)
                or (by_norm.get(norm)
                    if norm and len(by_norm.get(norm, [])) == 1 else None))
        if not idxs:
            stats["unmatched"].append(name)
            continue
        i = idxs[0]
        verdict = str(r.get("verdict") or "").strip().lower()
        note = str(r.get("note") or "")
        c.at[c.index[i], "correction_note"] = note[:300]
        if _DUP.search(note):
            c.at[c.index[i], "superseded"] = 1
            c.at[c.index[i], "usable"] = 0
            stats["superseded"] += 1
        if verdict == "confirmed":
            c.at[c.index[i], "verify_status"] = "confirmed"
            stats["confirmed"] += 1
        elif verdict == "cannot_verify":
            c.at[c.index[i], "verify_status"] = "cannot_verify"
            c.at[c.index[i], "usable"] = 0
            stats["cannot_verify"] += 1
        elif verdict == "corrected":
            c.at[c.index[i], "verify_status"] = "corrected"
            stats["corrected"] += 1
            row = c.index[i]
            amt = str(r.get("amount_usd_in_source") or "").strip()
            if amt and amt.lower() not in ("nan", "none"):
                if str(c.at[row, "amount_usd"]) != amt:
                    c.at[row, "orig_amount_usd"] = str(c.at[row, "amount_usd"])
                    c.at[row, "amount_usd"] = amt
                    stats["fields_changed"] += 1
            dt = str(r.get("announced_date_in_source") or "").strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}$", dt) and \
                    str(c.at[row, "announced_date"]) != dt:
                c.at[row, "orig_announced_date"] = str(
                    c.at[row, "announced_date"])
                c.at[row, "announced_date"] = dt
                stats["fields_changed"] += 1
            oc = str(r.get("outcome_type_in_source") or "").strip()
            if oc and oc != str(c.at[row, "outcome_type"]):
                c.at[row, "orig_outcome_type"] = str(
                    c.at[row, "outcome_type"])
                c.at[row, "outcome_type"] = oc
                stats["fields_changed"] += 1
            ru = str(r.get("replacement_url") or "").strip()
            if ru.startswith("http"):
                c.at[row, "orig_source_url"] = str(c.at[row, "source_url"])
                c.at[row, "source_url"] = ru
                stats["fields_changed"] += 1
        kind = str(r.get("amount_kind") or "").strip()
        if kind:
            c.at[c.index[i], "amount_kind"] = kind[:160]
            c.at[c.index[i], "amount_kind_class"] = classify_amount_kind(kind)
        cp = str(r.get("conduct_period_in_source") or "").strip()
        if cp:
            c.at[c.index[i], "conduct_period_verified"] = cp[:120]
    return c, stats


def to_markdown(c: pd.DataFrame, stats: dict) -> str:
    amt = pd.to_numeric(c["amount_usd"], errors="coerce").fillna(0)
    usable = c["usable"].astype(int) == 1
    kind = c["amount_kind_class"].replace("", "unknown")
    paid = amt[usable & (kind == "paid")].sum()
    alleged = amt[usable & (kind == "alleged")].sum()
    unknown = amt[usable & (kind == "unknown")].sum()
    L = ["# CASE CORRECTIONS — verdicts applied", ""]
    L.append(f"Verdicts applied: {stats['confirmed']} confirmed, "
             f"{stats['corrected']} corrected "
             f"({stats['fields_changed']} field changes), "
             f"{stats['cannot_verify']} cannot-verify, "
             f"{stats['superseded']} marked duplicate/superseded.")
    if stats["unmatched"]:
        L.append(f"Unmatched corrections ({len(stats['unmatched'])}): "
                 + "; ".join(stats["unmatched"][:10])
                 + (" …" if len(stats["unmatched"]) > 10 else ""))
    L.append("")
    L.append(f"Usable rows: {int(usable.sum()):,} of {len(c):,} "
             f"(superseded {int((c['superseded'].astype(int) == 1).sum()):,}, "
             f"cannot-verify "
             f"{int((c['verify_status'] == 'cannot_verify').sum()):,}).")
    L.append("")
    L.append("## Dollars (usable rows only, duplicates counted once)")
    L.append("")
    L.append("| kind | dollars |")
    L.append("|---|--:|")
    L.append(f"| PAID (settlements/judgments/restitution) | "
             f"${paid / 1e9:,.1f}B |")
    L.append(f"| ALLEGED (billed/intended scheme size) | "
             f"${alleged / 1e9:,.1f}B |")
    L.append(f"| unverified kind | ${unknown / 1e9:,.1f}B |")
    L.append("")
    L.append("_Quote PAID as recovered money. ALLEGED is scheme size, a "
             "different fact. Rows are never deleted: superseded and "
             "cannot-verify rows remain with usable=0 and their original "
             "values in orig_* columns._")
    return "\n".join(L)


def emit_kind_worklist(applied: pd.DataFrame, top_n: int = 250) -> pd.DataFrame:
    """The biggest usable rows whose dollar KIND is still unverified —
    the worklist that turns the 'unverified kind' bucket into paid vs
    alleged. Same shape the re-verification runner reads."""
    c = applied.copy()
    amt = pd.to_numeric(c["amount_usd"], errors="coerce").fillna(0)
    kind = c["amount_kind_class"].replace("", "unknown")
    mask = (c["usable"].astype(int) == 1) & (kind == "unknown") & (amt > 0)
    w = c[mask].copy()
    w["_amt"] = amt[mask]
    w["what_to_check"] = ("state whether the amount is a settlement/judgment "
                          "paid or the alleged/billed scheme size")
    cols = [x for x in ["case_id", "defendant_name", "announced_date",
                        "amount_usd", "outcome_type", "source_url",
                        "what_to_check"] if x in w.columns]
    return w.sort_values("_amt", ascending=False).head(top_n)[cols]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--corrections", required=True, nargs="+",
                    help="one or more verdict CSVs (later files win)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--emit-kind-worklist", type=int, default=0,
                    help="also write kind_check_batch.csv with the top-N "
                         "usable rows whose dollar kind is unverified")
    args = ap.parse_args()
    cases = pd.read_csv(args.cases, dtype=str).fillna("")
    corr = pd.concat([pd.read_csv(p, dtype=str).fillna("")
                      for p in args.corrections], ignore_index=True)
    out, stats = apply_corrections(cases, corr)
    out.to_csv(args.out, index=False)
    if args.emit_kind_worklist:
        wp = Path(args.out).with_name("kind_check_batch.csv")
        w = emit_kind_worklist(out, args.emit_kind_worklist)
        w.to_csv(wp, index=False)
        amt = pd.to_numeric(w["amount_usd"], errors="coerce").fillna(0).sum()
        print(f"[case_corrections] kind worklist: {len(w)} rows carrying "
              f"${amt / 1e9:,.1f}B -> {wp}")
    report = Path(args.out).with_name("CASE_CORRECTIONS_REPORT.md")
    report.write_text(to_markdown(out, stats), encoding="utf-8")
    print(f"[case_corrections] {stats['confirmed']}+{stats['corrected']}"
          f"+{stats['cannot_verify']} verdicts, {stats['superseded']} dups "
          f"superseded -> {args.out} + {report.name}")


if __name__ == "__main__":
    main()
