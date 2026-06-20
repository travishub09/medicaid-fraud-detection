"""
Model C orchestrator — cold-start underwriting over the Model A ERV output.

Reads a Model A ``erv_ranked.parquet`` (optionally an intake table and a DOJ/OIG
case DB for the public-disclosure screen), assembles case features, underwrites
each case (fund / pass / fund-with-terms), runs the portfolio Monte Carlo, and
writes ``case_underwriting.parquet`` + per-case investment memos +
``MODEL_C_REPORT.md``.

Run on the synthetic fixture end-to-end (builds the graph + Model A first):
    python -m src.model_c --fixture --out /tmp/model_c_out

Run on real Model A output:
    python -m src.model_c --erv ~/Desktop/data/model_a/erv_ranked.parquet \
        --out ~/Desktop/data/model_c [--intake intake.parquet] [--target-moic 3.0]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .features import build_case_features
from .underwriting import underwrite
from .portfolio import monte_carlo_portfolio
from .priors import DEFAULT_ASSUMPTIONS

DISCLAIMER = (
    "> **Underwriting estimate for human/counsel review.** Cold-start, rules-based, "
    "label-free. P(intervene), recovery, and terms are model estimates from public "
    "data and curated priors — not legal advice, not a promise of outcome, and not "
    "an accusation against any organization.\n")


def log(m: str) -> None:
    print(m, flush=True)


def run(erv_ranked: pd.DataFrame, out_dir: Path,
        intake: pd.DataFrame | None = None,
        case_db: pd.DataFrame | None = None,
        target_moic: float = 3.0, top_k_memos: int = 10,
        fund_size: int = 20) -> pd.DataFrame:
    """Underwrite every case; write the table, memos, and portfolio report."""
    signal = erv_ranked.copy()
    if case_db is not None and "public_disclosure_flag" not in signal.columns:
        from .public_disclosure import public_disclosure_screen
        screen = public_disclosure_screen(signal, case_db=case_db)
        signal = signal.merge(screen, on="org_node_id", how="left")

    feats = build_case_features(signal, intake=intake)
    decided = underwrite(feats, portfolio_target_moic=target_moic)
    decided = decided.sort_values("expected_relator_gross",
                                  ascending=False).reset_index(drop=True)

    book = monte_carlo_portfolio(decided, fund_size=fund_size)

    out_dir.mkdir(parents=True, exist_ok=True)
    decided.to_parquet(out_dir / "case_underwriting.parquet", index=False)

    memo_dir = out_dir / "memos"
    memo_dir.mkdir(exist_ok=True)
    funded = decided[decided["recommendation"] != "pass"]
    for rank, (_, row) in enumerate(funded.head(top_k_memos).iterrows(), 1):
        safe = str(row["org_node_id"]).replace(":", "_").replace("/", "_")
        (memo_dir / f"{rank:03d}_{safe}.md").write_text(render_memo(row), encoding="utf-8")

    _write_report(decided, book, target_moic, out_dir)
    counts = decided["recommendation"].value_counts().to_dict()
    log(f"Done — underwrote {len(decided)} cases ({counts}); "
        f"book MOIC p50 {book.get('moic_p50', 0):.2f}× on {book.get('funded_cases', 0)} funded")
    return decided


def render_memo(row: pd.Series) -> str:
    """One case's investment memo (the drivers ARE the memo)."""
    name = row.get("org_name") or row.get("org_node_id")
    L = [f"# Case underwriting memo — {name}\n", DISCLAIMER,
         f"\n**Recommendation: {str(row['recommendation']).upper()}** — "
         f"{row.get('decision_reasons', '')}\n",
         "\n## Outcome model\n",
         f"- P(intervene): {row['p_intervene']:.1%}  ·  "
         f"P(recover): {row['p_recover']:.1%}  ·  "
         f"P(dismissed): {row['p_dismissed']:.1%}\n",
         f"- Scheme hypothesis: {row['scheme']}  "
         f"(relator in intake: {'yes' if int(row.get('has_relator', 0)) else 'no — pre-relator pre-screen'})\n",
         "- Intervention drivers (base × multipliers):\n"]
    for c in [c for c in row.index if c.startswith("mult_")]:
        L.append(f"    - {c.replace('mult_', '')}: ×{row[c]}\n")
    L += ["\n## Recovery distribution\n",
          f"- Single damages proxy: ${row['single_damages']:,.0f}\n",
          f"- Realized recovery P10/P50/P90: ${row['recovery_p10']:,.0f} / "
          f"${row['recovery_p50']:,.0f} / ${row['recovery_p90']:,.0f}\n",
          f"- Expected recovery | recover: ${row['expected_recovery']:,.0f}\n",
          "\n## Economics\n",
          f"- Blended relator share: {row['blended_relator_share']:.1%}\n",
          f"- **Expected relator gross: ${row['expected_relator_gross']:,.0f}**\n"]
    if str(row["recommendation"]) != "pass":
        L += [f"- Capital deployed: ${row['capital_deployed']:,.0f}  ·  "
              f"funder take: {row['take_fraction']:.1%}\n",
              f"- Funder expected value: ${row['funder_expected_value']:,.0f}  "
              f"(expected MOIC {row['expected_moic']:.2f}×)\n",
              f"- Whale-likelihood flag: {row['whale_likelihood']}\n"]
    L.append("\n## Next steps\n- Counsel review of first-to-file, public-disclosure, "
             "and original-source posture before any financing commitment.\n")
    return "".join(L)


def _write_report(decided: pd.DataFrame, book: dict, target_moic: float,
                  out_dir: Path) -> None:
    counts = decided["recommendation"].value_counts().to_dict()
    L = ["# MODEL_C_REPORT — cold-start underwriting\n", DISCLAIMER,
         "\n## Decision mix\n"]
    for k in ("fund", "fund-with-terms", "pass"):
        L.append(f"- {k}: {counts.get(k, 0)}\n")
    L.append(f"\n## Portfolio Monte Carlo (target {target_moic:.1f}×)\n")
    if book.get("funded_cases"):
        L += [f"- Funded cases: {book['funded_cases']}  ·  "
              f"capital ${book['capital_deployed']:,.0f}\n",
              f"- Book MOIC p10/p50/p90: {book['moic_p10']:.2f}× / "
              f"{book['moic_p50']:.2f}× / {book['moic_p90']:.2f}×  "
              f"(mean {book['moic_mean']:.2f}×)\n",
              f"- P(loss): {book['prob_loss']:.1%}  ·  "
              f"P(≥3×): {book['prob_target_3x']:.1%}  ·  "
              f"whale probability: {book['whale_probability']:.1%}\n"]
    else:
        L.append("- no fundable cases\n")
    L.append("\n## Top cases by expected relator gross\n"
             "| org | scheme | P(int) | exp. recovery | exp. gross | rec |\n"
             "|---|---|--:|--:|--:|---|\n")
    for _, r in decided.head(15).iterrows():
        L.append(f"| {(r.get('org_name') or r['org_node_id'])[:32]} | {r['scheme']} "
                 f"| {r['p_intervene']:.0%} | ${r['expected_recovery']:,.0f} "
                 f"| ${r['expected_relator_gross']:,.0f} | {r['recommendation']} |\n")
    (out_dir / "MODEL_C_REPORT.md").write_text("".join(L), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--erv", default=None, help="Model A erv_ranked.parquet")
    ap.add_argument("--intake", default=None, help="optional relator intake parquet")
    ap.add_argument("--case-db", default=None,
                    help="optional DOJ/OIG case table (csv/parquet) for the "
                         "public-disclosure screen")
    ap.add_argument("--target-moic", type=float, default=3.0)
    ap.add_argument("--out", default="/tmp/model_c_out")
    ap.add_argument("--top-k", type=int, default=10, help="memos to render")
    ap.add_argument("--fixture", action="store_true",
                    help="build graph + Model A from the synthetic fixture first")
    args = ap.parse_args()

    if args.fixture:
        from src.entity_graph.__main__ import run as run_graph
        from src.model_a.__main__ import run as run_model_a
        from tests.fixtures.synthetic import (build_synthetic_inputs,
                                              build_company_features)
        out = Path(args.out)
        g = run_graph(build_synthetic_inputs(), out / "_graph")
        org_nodes = g["nodes/org_nodes"]
        erv = run_model_a(org_nodes, g["org_graph_features"],
                          build_company_features(org_nodes),
                          g["rings/shared_address_shells"],
                          g["rings/common_owner_clusters"], out / "_model_a",
                          top_k_dossiers=1)
    else:
        if not args.erv:
            ap.error("--erv is required (or use --fixture)")
        erv = pd.read_parquet(args.erv)

    intake = pd.read_parquet(args.intake) if args.intake else None
    case_db = None
    if args.case_db:
        case_db = (pd.read_csv(args.case_db, dtype=str) if args.case_db.endswith(".csv")
                   else pd.read_parquet(args.case_db))

    run(erv, Path(args.out), intake=intake, case_db=case_db,
        target_moic=args.target_moic, top_k_memos=args.top_k)


if __name__ == "__main__":
    main()
