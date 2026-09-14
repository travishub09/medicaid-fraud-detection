"""
label_variants.py — one matrix, several rulers: compare forward-label policies.

The v6 post-mortem's finding was that the answer key, not the feature set,
caps forward lift: the exclusion list mixes fraud with loan defaults and
license actions no billing model can predict. The cheap experiment is to
re-label the SAME frozen matrix under each FCA-relevance policy
(``prospective_label --fca-tier all|hard|hard+weak``), re-run the network
A/B and the OOF source ablation per policy, and read the lifts side by side.

Run-folder convention (what run_v7.bat / run_label_experiment.bat write):

  future_bans_after_<cutoff>__<variant>.csv       the label under that policy
  future_bans_after_<cutoff>__<variant>.TIERS.md  its tier audit
  NETWORK_AB_REPORT__<variant>.md
  SOURCE_ABLATION_OOF_FULL__<variant>.md

The UNSUFFIXED files are the pre-registered primary policy (what the registry
scores). This module parses every variant present and writes LABEL_VARIANTS.md:
positives, full/core top-decile lift, the extra-data delta with its CI, and the
network verdict per policy — the table that says whether the ruler moved.

    python -m src.model_a.label_variants --run-dir <frozen_dir> [--cutoff 2023-12]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

from .run_registry import _NUM, _read, _search

VARIANTS = ("all", "hard", "hard+weak", "active_hard", "active_all")


def _slug(v: str) -> str:
    return v.replace("+", "_")


def parse_variant(run_dir: Path, cutoff: str, variant: str | None) -> dict | None:
    """Metrics for one policy; ``variant=None`` reads the unsuffixed primary."""
    sfx = f"__{_slug(variant)}" if variant else ""
    d = Path(run_dir)
    fb = d / f"future_bans_after_{cutoff}{sfx}.csv"
    if not fb.exists():
        return None
    row: dict = {"variant": variant or "primary", "label_file": fb.name}
    try:
        f = pd.read_csv(fb, dtype=str)
        row["policy"] = (str(f["label_policy"].iloc[0])
                         if "label_policy" in f.columns and len(f) else "all")
        row["positives"] = int((pd.to_numeric(f.get("is_prospective_positive"),
                                              errors="coerce").fillna(0) == 1).sum())
        if "fca_tier" in f.columns:
            pos = f[pd.to_numeric(f["is_prospective_positive"], errors="coerce") == 1]
            row["tiers"] = pos["fca_tier"].value_counts().to_dict()
    except Exception as e:  # noqa: BLE001 — a bad CSV is reported, not fatal
        row["error"] = f"label unreadable: {e}"

    ab = _read(d / f"NETWORK_AB_REPORT{sfx}.md")
    if ab:
        v = re.search(r"\*\*VERDICT:\s*([A-Z /-]+)", ab)
        row["network_verdict"] = v.group(1).strip() if v else None
        row["network_delta"] = _search(r"delta ([+-]?\d+\.?\d*), CI", ab)

    oof = _read(d / f"SOURCE_ABLATION_OOF_FULL{sfx}.md") or _read(
        d / f"SOURCE_ABLATION_OOF{sfx}.md")
    if oof:
        m = re.search(rf"\| top-decile lift \| {_NUM} \| {_NUM} \| {_NUM} "
                      rf"\[{_NUM}, {_NUM}\]", oof)
        if m:
            row["lift_core"], row["lift_full"], row["lift_delta"] = (
                float(m.group(1)), float(m.group(2)), float(m.group(3)))
            row["lift_lo"], row["lift_hi"] = float(m.group(4)), float(m.group(5))
        m = re.search(rf"\| ROC-AUC \| {_NUM} \| {_NUM} \| {_NUM} "
                      rf"\[{_NUM}, {_NUM}\]", oof)
        if m:
            row["roc_core"], row["roc_full"] = float(m.group(1)), float(m.group(2))
    return row


def collect(run_dir: Path, cutoff: str) -> list[dict]:
    rows = []
    primary = parse_variant(run_dir, cutoff, None)
    if primary:
        rows.append(primary)
    for v in VARIANTS:
        r = parse_variant(run_dir, cutoff, v)
        if r:
            rows.append(r)
    return rows


def _f(v, fmt="{:.3f}"):
    return fmt.format(v) if isinstance(v, (int, float)) else "n/a"


def to_markdown(rows: list[dict], run_dir: Path, cutoff: str) -> str:
    lines = [f"# LABEL VARIANTS — same matrix, different rulers ({Path(run_dir).name}, "
             f"cutoff {cutoff})", "",
             "_Every row is the SAME frozen feature matrix scored against a different "
             "forward label. `all` = every post-cutoff exclusion (the v1-v6 ruler). "
             "`hard` = only statutes/reasons that look like a False Claims Act case. "
             "`hard+weak` adds misconduct that is not FCA on its face. If the full-"
             "model lift leaves the 3.7x-4.0x band under `hard`, the answer key was the "
             "ceiling. Lifts under different rulers are NOT comparable to the registry's "
             "champion delta; this table compares rulers, not runs._", "",
             "| variant | policy | positives | tiers (hard/weak/excl) | core lift | "
             "full lift | extra-data delta [95% CI] | ROC core -> full | network |",
             "|---|---|--:|---|--:|--:|---|---|---|"]
    for r in rows:
        t = r.get("tiers") or {}
        tiers = "/".join(str(int(t.get(k, 0))) for k in ("hard", "weak", "excluded")) if t else "n/a"
        delta = (f"{r['lift_delta']:+.3f} [{r['lift_lo']:+.3f}, {r['lift_hi']:+.3f}]"
                 if "lift_delta" in r else "n/a")
        roc = (f"{r['roc_core']:.3f} -> {r['roc_full']:.3f}" if "roc_full" in r else "n/a")
        net = r.get("network_verdict") or "n/a"
        if r.get("network_delta") is not None:
            net += f" ({r['network_delta']:+.3f})"
        lines.append(f"| {r['variant']} | {r.get('policy', 'n/a')} | "
                     f"{r.get('positives', 'n/a')} | {tiers} | {_f(r.get('lift_core'))} | "
                     f"{_f(r.get('lift_full'))} | {delta} | {roc} | {net} |")
    lines += ["", "## Reading it", "",
              "- **Positives fall, lift rises** under `hard`: the dropped exclusions were "
              "unreachable noise; adopt the tier policy for the next pre-registration.",
              "- **Positives fall, lift flat**: the label was not the binding constraint "
              "at this cutoff; move to segment-matched labels (DOJ settlements for "
              "institutions) and rolling-origin freezes.",
              "- **Lift falls**: the dropped statutes carried real billing signal "
              "(license actions often follow billing investigations); keep `hard+weak`.",
              "", "_Leads for counsel review, never accusations. The label is a public "
              "integrity event, not a finding about any provider._"]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--cutoff", default="2023-12")
    ap.add_argument("--out", default=None, help="default <run-dir>/LABEL_VARIANTS.md")
    args = ap.parse_args()
    rows = collect(Path(args.run_dir), args.cutoff)
    out = Path(args.out) if args.out else Path(args.run_dir) / "LABEL_VARIANTS.md"
    out.write_text(to_markdown(rows, Path(args.run_dir), args.cutoff), encoding="utf-8")
    print(f"wrote {out} — {len(rows)} variant(s)")
    for r in rows:
        print(f"  {r['variant']:<10} policy={r.get('policy')} positives={r.get('positives')} "
              f"full_lift={_f(r.get('lift_full'))} delta={_f(r.get('lift_delta'), '{:+.3f}')}")


if __name__ == "__main__":
    main()
