"""
rank_stability.py — how much did the shipped ranking move between two runs?

The v3→v5 diff measured top-25 overlap at 7/25, with NORTON swinging from
rank 1 (score 1.00) to rank 134 (score 0.08). A single run's top ranks are
fragile, and nobody knew until the lists were hand-diffed. This module makes
that diagnostic standing: given two runs' priority lists (or any two ranked
CSVs with an org_name + a sort score), it reports

  * top-N overlap (N = 10/25/50/100) — the headline stability number
  * Spearman rank correlation on the names both lists contain
  * the biggest movers in both directions, with both scores side by side

Names are the join key (org ids can differ across graph builds); duplicate
names within one list are collapsed to their best rank, which slightly
FLATTERS stability — read the overlap as an upper bound.

Consumers: the release registry (a stability row per registered run), the
night runner (RANK_STABILITY.md next to the new list), and any pre-Brad QA.

  python -m src.model_a.rank_stability --a <old_run_dir> --b <new_run_dir>
  python -m src.model_a.rank_stability --a old.csv --b new.csv --out report.md
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

TOP_NS = (10, 25, 50, 100)


def _load(path: str | Path) -> pd.DataFrame:
    """Accept a priority-list CSV or a run folder containing one."""
    p = Path(path)
    if p.is_dir():
        p = p / "provider_priority_list.csv"
    df = pd.read_csv(p, dtype=str)
    need = {"org_name", "score_dollar_weighted"}
    missing = need - set(df.columns)
    if missing:
        raise SystemExit(f"{p}: missing columns {sorted(missing)}")
    df["score_dollar_weighted"] = pd.to_numeric(
        df["score_dollar_weighted"], errors="coerce")
    df["_key"] = df["org_name"].astype(str).str.upper().str.strip()
    # duplicate names collapse to their BEST rank (flatters stability; the
    # report says so rather than pretending names are identities)
    df = (df.sort_values("score_dollar_weighted", ascending=False)
          .drop_duplicates("_key").reset_index(drop=True))
    df["_rank"] = df.index + 1
    return df


def compare(a: pd.DataFrame, b: pd.DataFrame, movers: int = 15) -> dict:
    """The stability report as a dict; render with to_markdown."""
    out: dict = {"n_a": len(a), "n_b": len(b)}
    for n in TOP_NS:
        ka, kb = set(a.head(n)["_key"]), set(b.head(n)["_key"])
        out[f"top{n}_overlap"] = len(ka & kb)
    m = a[["_key", "_rank", "score_dollar_weighted"]].merge(
        b[["_key", "_rank", "score_dollar_weighted"]],
        on="_key", suffixes=("_a", "_b"))
    out["n_common"] = len(m)
    out["spearman"] = (float(m["_rank_a"].corr(m["_rank_b"], method="spearman"))
                       if len(m) > 2 else float("nan"))
    m["move"] = m["_rank_a"] - m["_rank_b"]          # + = rose in the new list
    top_a = m[m["_rank_a"] <= 50]
    out["movers_up"] = (m.sort_values("move", ascending=False).head(movers)
                        [["_key", "_rank_a", "_rank_b",
                          "score_dollar_weighted_a", "score_dollar_weighted_b"]]
                        .to_dict("records"))
    out["movers_down"] = (top_a.sort_values("move").head(movers)
                          [["_key", "_rank_a", "_rank_b",
                            "score_dollar_weighted_a", "score_dollar_weighted_b"]]
                          .to_dict("records"))
    return out


def to_markdown(rep: dict, label_a: str = "A", label_b: str = "B") -> str:
    L = [f"# RANK STABILITY — {label_a} vs {label_b}", "",
         f"- lists: {rep['n_a']:,} vs {rep['n_b']:,} rows; "
         f"{rep['n_common']:,} names in common",
         f"- Spearman rank correlation (common names): "
         f"{rep['spearman']:.3f}" if rep["spearman"] == rep["spearman"]
         else "- Spearman: n/a", ""]
    L.append("| top-N | overlap |")
    L.append("|--:|--:|")
    for n in TOP_NS:
        L.append(f"| {n} | {rep[f'top{n}_overlap']}/{n} |")
    L += ["",
          "_Low top-N overlap means the shipped list depends heavily on which "
          "run produced it — ship from the champion, and treat names that "
          "hold rank across runs as the highest-conviction leads. Duplicate "
          "names were collapsed to best rank, so overlap is an upper bound._",
          ""]

    def _tbl(rows, title):
        out = [f"## {title}", "",
               f"| org | rank {label_a} | rank {label_b} | "
               f"score {label_a} | score {label_b} |", "|---|--:|--:|--:|--:|"]
        for r in rows:
            out.append(f"| {r['_key'][:45]} | {r['_rank_a']} | {r['_rank_b']} "
                       f"| {r['score_dollar_weighted_a']:.3f} "
                       f"| {r['score_dollar_weighted_b']:.3f} |")
        return out
    L += _tbl(rep["movers_down"], f"Biggest FALLS out of {label_a}'s top 50")
    L += [""] + _tbl(rep["movers_up"], "Biggest RISES")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--a", required=True, help="older run dir or CSV")
    ap.add_argument("--b", required=True, help="newer run dir or CSV")
    ap.add_argument("--out", default=None,
                    help="write markdown here (default: RANK_STABILITY.md "
                         "next to --b when --b is a folder, else stdout)")
    args = ap.parse_args()
    a, b = _load(args.a), _load(args.b)
    rep = compare(a, b)
    md = to_markdown(rep, Path(args.a).name, Path(args.b).name)
    out = args.out
    if out is None and Path(args.b).is_dir():
        out = str(Path(args.b) / "RANK_STABILITY.md")
    if out:
        Path(out).write_text(md, encoding="utf-8")
        print(f"[rank_stability] -> {out}")
    else:
        print(md)
    for n in TOP_NS:
        print(f"[rank_stability] top-{n} overlap: {rep[f'top{n}_overlap']}/{n}")


if __name__ == "__main__":
    main()
