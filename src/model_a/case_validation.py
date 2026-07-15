"""
case_validation.py — validate each scheme against PROVEN DOJ cases.

The exclusion list is the wrong ruler for billing-fraud schemes: worthless
services, upcoding, and phantom billing usually end in a civil False Claims Act
settlement, not an OIG exclusion. So a scheme that scores ~0.5 against the
exclusion label may be blind-spotted by the label, not weak. Settled DOJ cases
are the ruler that FITS those schemes, because a DOJ case names the actual
conduct type.

This scores each of our scheme subscores against the DOJ cases OF THAT SCHEME
TYPE:

  for scheme S:
    positives = NPIs tied to a settled DOJ case whose conduct is scheme S
    control   = the rest of the scored universe (on the subscore's covered rows)
    metric    = how well subscore_S ranks the S-positives above everyone else
                (AUC), plus top-decile lift, plus a SPECIFICITY read (does
                subscore_S fire on S cases MORE than on other-scheme cases?)

Output: one row per scheme — n proven cases, AUC, lift, specificity, and a
plain verdict (VALIDATED / WEAK / THIN). This is the artifact that turns "the
model is plausible" into "our worthless-services detector fires on the providers
who already settled worthless-services cases."

  python -m src.model_a.case_validation --matrix <matrix.parquet> \
      --case-labels <case_labels.parquet> --out CASE_VALIDATION.md
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# DOJ conduct vocabulary (case_db._SCHEME_KEYWORDS) -> our subscore scheme names.
# A DOJ scheme can map to more than one of ours; each mapped subscore is scored.
DOJ_TO_SUBSCHEME = {
    "kickback": ["pharma_kickback", "dme_ring"],
    "upcoding": ["upcoding"],
    "medical_necessity": ["overutilization", "worthless_services",
                          "hospice_ineligibility"],
    "phantom_billing": ["single_service_mill", "nemt_fraud", "behavioral_health"],
    "eligibility": ["invalid_identity", "dme_ring"],
    "worthless_services": ["worthless_services"],
}

MIN_PROVEN = 5             # fewer known cases in coverage than this = THIN
_MID = 0.5


def _auc(y: np.ndarray, s: np.ndarray) -> float:
    """Rank AUC on the covered (non-null score) rows; mid-rank ties."""
    m = ~np.isnan(s)
    y, s = y[m], s[m]
    n1, n0 = int(y.sum()), int((y == 0).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), float)
    ranks[order] = np.arange(1, len(s) + 1)
    # average ranks for ties
    _, inv, cnt = np.unique(s, return_inverse=True, return_counts=True)
    csum = np.cumsum(cnt)
    avg = {i: (csum[i] - (cnt[i] - 1) / 2) for i in range(len(cnt))}
    ranks = np.array([avg[i] for i in inv])
    return (ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def _top_decile_lift(y: np.ndarray, s: np.ndarray) -> float:
    m = ~np.isnan(s)
    y, s = y[m], s[m]
    if y.sum() == 0 or len(y) < 10:
        return float("nan")
    k = max(1, int(len(s) * 0.10))
    top = np.argsort(s)[-k:]
    base = y.mean()
    return float(y[top].mean() / base) if base > 0 else float("nan")


def validate_schemes(matrix: pd.DataFrame, case_labels: pd.DataFrame,
                     min_proven: int = MIN_PROVEN) -> pd.DataFrame:
    """One row per scheme: AUC/lift/specificity of subscore_<scheme> against the
    DOJ cases of that conduct type. ``case_labels`` carries npi + fraud_scheme
    (from build_case_labels; ';'-joined scheme tags)."""
    cols = ["scheme", "subscore", "n_proven", "auc", "top_decile_lift",
            "specificity_auc", "verdict"]
    if not len(matrix) or case_labels is None or not len(case_labels):
        return pd.DataFrame(columns=cols)
    m = matrix.copy()
    m["npi"] = m["npi"].astype(str)
    cl = case_labels.copy()
    cl["npi"] = cl["npi"].astype(str)
    cl["fraud_scheme"] = cl.get("fraud_scheme", "").fillna("").astype(str)

    # explode DOJ scheme tags -> the set of NPIs proven for each DOJ scheme
    doj_npis: dict[str, set] = {}
    for r in cl.itertuples():
        for tag in str(r.fraud_scheme).split(";"):
            tag = tag.strip().lower()
            if tag:
                doj_npis.setdefault(tag, set()).add(r.npi)
    all_case_npis = set(cl["npi"])

    rows = []
    for doj_scheme, subs in DOJ_TO_SUBSCHEME.items():
        proven = doj_npis.get(doj_scheme, set())
        if not proven:
            continue
        for sub in subs:
            col = f"subscore_{sub}"
            if col not in m.columns:
                continue
            s = pd.to_numeric(m[col], errors="coerce").to_numpy(float)
            y = m["npi"].isin(proven).to_numpy().astype(int)
            n_cov_pos = int(((~np.isnan(s)) & (y == 1)).sum())
            auc = _auc(y, s)
            lift = _top_decile_lift(y, s)
            # specificity: on the case NPIs only, does subscore_sub rank the
            # THIS-scheme cases above OTHER-scheme cases? (guards against a
            # subscore that just fires on any excluded provider)
            spec = float("nan")
            case_mask = m["npi"].isin(all_case_npis).to_numpy()
            if case_mask.sum() > 10:
                ys = m.loc[case_mask, "npi"].isin(proven).to_numpy().astype(int)
                ss = pd.to_numeric(m.loc[case_mask, col], errors="coerce").to_numpy(float)
                if ys.sum() >= 3 and (ys == 0).sum() >= 3:
                    spec = _auc(ys, ss)
            if n_cov_pos < min_proven:
                verdict = f"THIN ({n_cov_pos} proven in coverage)"
            elif not np.isnan(auc) and auc >= 0.62:
                verdict = "VALIDATED"
            elif not np.isnan(auc) and auc >= 0.55:
                verdict = "PARTIAL"
            else:
                verdict = "WEAK"
            rows.append({"scheme": sub, "subscore": col, "n_proven": n_cov_pos,
                         "auc": round(auc, 3) if not np.isnan(auc) else None,
                         "top_decile_lift": round(lift, 2) if not np.isnan(lift) else None,
                         "specificity_auc": round(spec, 3) if not np.isnan(spec) else None,
                         "verdict": verdict})
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values(["verdict", "auc"], ascending=[True, False]).reset_index(drop=True)


def to_markdown(res: pd.DataFrame, n_case_npis: int) -> str:
    L = ["# CASE VALIDATION — do our schemes fire on PROVEN DOJ cases?", ""]
    L.append("_The exclusion list is blind to billing fraud. This scores each "
             "scheme subscore against settled DOJ cases of that conduct type — "
             "the ruler that fits. AUC = how well the subscore ranks the proven "
             "cases above everyone else on its covered rows. Specificity = does "
             "it fire on THIS scheme's cases more than on other schemes' cases._")
    L.append("")
    L.append(f"- DOJ-case NPIs in the scored universe: **{n_case_npis:,}**")
    if not len(res):
        L.append("\n**No overlap between DOJ cases and scored providers "
                 "(empty case DB, or no resolved NPIs).**")
        return "\n".join(L)
    L.append("")
    L.append("| scheme | proven cases | AUC | top-decile lift | specificity | verdict |")
    L.append("|---|--:|--:|--:|--:|---|")
    for r in res.itertuples():
        L.append(f"| {r.scheme} | {r.n_proven} | "
                 f"{r.auc if r.auc is not None else '-'} | "
                 f"{r.top_decile_lift if r.top_decile_lift is not None else '-'} | "
                 f"{r.specificity_auc if r.specificity_auc is not None else '-'} | "
                 f"{r.verdict} |")
    val = res[res["verdict"] == "VALIDATED"]["scheme"].tolist()
    L.append("")
    L.append(f"**VALIDATED against proven cases: {', '.join(val) if val else 'none yet'}.** "
             "These are the schemes to lead with for origination. WEAK/PARTIAL "
             "may still be real but need more cases, or the DOJ->subscore mapping "
             "refined. THIN = too few proven cases in coverage to judge yet.")
    return "\n".join(L)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--case-labels", required=True,
                    help="parquet from build_case_labels (npi + fraud_scheme)")
    ap.add_argument("--out", default="CASE_VALIDATION.md")
    ap.add_argument("--min-proven", type=int, default=MIN_PROVEN)
    args = ap.parse_args()
    m = pd.read_parquet(args.matrix)
    cl = (pd.read_parquet(args.case_labels)
          if args.case_labels.endswith(".parquet")
          else pd.read_csv(args.case_labels, dtype=str))
    res = validate_schemes(m, cl, min_proven=args.min_proven)
    n_case = int(m["npi"].astype(str).isin(set(cl["npi"].astype(str))).sum())
    report = to_markdown(res, n_case)
    Path(args.out).write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
