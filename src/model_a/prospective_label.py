"""
prospective_label.py — the held-out FORWARD label for the frozen-file network test.

The A/B network test Travis is running needs a label that lives strictly in the
FUTURE relative to the feature freeze. If the label and the features share a
time window, network + billing features already "know" who was banned and the
test measures leakage, not signal. The honest experiment is:

  1. freeze features as-of a cutoff (graph + billing built only from data known
     before the cutoff — see ``entity_graph --asof`` and ``asof_billing``);
  2. label a provider positive ONLY if their FIRST exclusion lands on/after the
     cutoff (they looked clean at freeze time and were banned later);
  3. score the frozen file against that forward label.

This module produces step 2 — the ``future_bans_2024plus.csv`` file. It reads the
exclusion nodes (``npi`` + ``excl_date``), collapses to one row per NPI at its
EARLIEST exclusion, and splits the universe three ways for a given cutoff:

  is_prospective_positive   first exclusion strictly AFTER the freeze instant → the
                            forward label (1)
  was_excluded_pre_cutoff   first exclusion at/before the freeze instant → drop from
                            the test (already known-bad at freeze; not a fair forward
                            case). The freeze instant itself counts as KNOWN because
                            the as-of graph keeps exclusions dated <= the cutoff —
                            counting a same-day ban as a forward positive would put
                            the same event in both the features and the label.
  (neither)                 never excluded                       → candidate negative

Only the FIRST exclusion per NPI matters: a provider banned in 2019 and re-listed
in 2025 was already known-bad at a 2023 freeze, so 2025 is not a "new" ban. Undated
exclusions can't be placed relative to the cutoff and are reported separately so
they are never silently counted as forward positives.

Leads for review, not accusations: an exclusion is a public integrity event, and
this file is a modelling label, not a claim about any person.

  build_prospective_label(exclusion_nodes, cutoff)  → per-NPI label frame
  python -m src.model_a.prospective_label --exclusion-nodes graph/nodes/exclusion_nodes.parquet \
      --cutoff 2024-01 --out future_bans_2024plus.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def _cutoff_ts(cutoff: str) -> pd.Timestamp:
    """Cutoff string (YYYY-MM or YYYY-MM-DD) → month-start Timestamp."""
    s = str(cutoff).strip()
    return pd.Timestamp(s[:7] + "-01" if len(s) == 7 else s)


def build_prospective_label(exclusion_nodes: pd.DataFrame, cutoff: str,
                            fca_tier: str = "all") -> pd.DataFrame:
    """One row per NPI, keyed to its EARLIEST exclusion, split by ``cutoff``.

    Returns columns: npi, first_excl_date, excl_type, entity_name,
    is_prospective_positive, was_excluded_pre_cutoff, undated_exclusion,
    fca_tier, fca_basis, label_policy.
    NPIs with a blank/missing NPI on the exclusion record are dropped (a name-only
    exclusion can't be joined to a billing provider).

    ``fca_tier`` is the label policy ('all' | 'hard' | 'hard+weak', see
    fca_relevance): a provider is a forward positive only when it has a
    post-cutoff exclusion whose statute/reason sits in a kept tier. The
    pre-cutoff drop still uses EVERY exclusion (a loan-default exclusion node
    is a feature in the as-of graph regardless of policy, so that provider
    can't be in the forward test either way). The row's fca_tier/fca_basis
    describe the earliest KEPT record when one exists, else the earliest record.
    """
    from .fca_relevance import classify_series, tier_mask

    cols = ["npi", "first_excl_date", "excl_type", "entity_name",
            "is_prospective_positive", "was_excluded_pre_cutoff", "undated_exclusion",
            "fca_tier", "fca_basis", "label_policy"]
    if exclusion_nodes is None or not len(exclusion_nodes):
        return pd.DataFrame(columns=cols)

    e = exclusion_nodes.copy()
    e["npi"] = e.get("npi").astype("string").str.strip()
    e = e[e["npi"].notna() & (e["npi"] != "") & (e["npi"].str.lower() != "none")].copy()
    if not len(e):
        return pd.DataFrame(columns=cols)

    e["_excl_dt"] = pd.to_datetime(e.get("excl_date"), errors="coerce")
    if "excl_type" not in e.columns:
        e["excl_type"] = ""
    e["excl_type"] = e["excl_type"].fillna("").astype(str)
    tiers = classify_series(e["excl_type"])
    e["fca_tier"] = tiers["fca_tier"].values
    e["fca_basis"] = tiers["fca_basis"].values
    e["_kept"] = tier_mask(e["fca_tier"], fca_tier).values
    # Earliest exclusion per NPI. Sort dated rows ahead of undated so a provider
    # with any known date keeps that date as its first exclusion; kept-tier rows
    # ahead of dropped ones so the row's tier describes the record that counts.
    e["_undated"] = e["_excl_dt"].isna()
    e["_dropped"] = ~e["_kept"]
    e = e.sort_values(["npi", "_undated", "_excl_dt"])
    first = e.groupby("npi", as_index=False).first()
    kept_first = (e[e["_kept"]].sort_values(["npi", "_undated", "_excl_dt"])
                  .groupby("npi", as_index=False).first()
                  .set_index("npi"))

    cutoff_ts = _cutoff_ts(cutoff)
    undated = first["_excl_dt"].isna()
    dated = ~undated
    has_kept = first["npi"].isin(kept_first.index)
    tier_col = first["npi"].map(kept_first["fca_tier"]).where(has_kept, first["fca_tier"])
    basis_col = first["npi"].map(kept_first["fca_basis"]).where(has_kept, first["fca_basis"])
    out = pd.DataFrame({
        "npi": first["npi"].astype(str),
        "first_excl_date": first["_excl_dt"].dt.strftime("%Y-%m-%d").where(dated, ""),
        "excl_type": first["excl_type"],
        "entity_name": first.get("entity_name", pd.Series("", index=first.index)).fillna(""),
        # strictly AFTER the freeze instant: the as-of graph keeps exclusions dated
        # <= the cutoff, so a ban dated exactly at the cutoff is already a feature
        # and must not also be a label (the one-day boundary leak). Under a tier
        # policy the provider must ALSO carry a kept-tier record (which is then
        # necessarily post-cutoff, since its earliest record of any kind is).
        "is_prospective_positive": (dated & (first["_excl_dt"] > cutoff_ts)
                                    & has_kept).astype(int),
        "was_excluded_pre_cutoff": (dated & (first["_excl_dt"] <= cutoff_ts)).astype(int),
        "undated_exclusion": undated.astype(int),
        "fca_tier": tier_col.values,
        "fca_basis": basis_col.values,
        "label_policy": (fca_tier or "all").lower(),
    })
    return out.sort_values(["is_prospective_positive", "first_excl_date"],
                           ascending=[False, True]).reset_index(drop=True)


def dropped_by_policy(label: pd.DataFrame, cutoff: str) -> pd.DataFrame:
    """Post-cutoff exclusions the policy did NOT count (the audit counsel reads)."""
    cutoff_ts = _cutoff_ts(cutoff)
    dt = pd.to_datetime(label["first_excl_date"], errors="coerce")
    return label[(dt > cutoff_ts) & (label["is_prospective_positive"] == 0)]


def _load(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix == ".parquet":
        return pd.read_parquet(p)
    return pd.read_csv(p, dtype=str)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exclusion-nodes", required=True,
                    help="graph/nodes/exclusion_nodes.parquet (npi + excl_date)")
    ap.add_argument("--cutoff", required=True,
                    help="feature-freeze cutoff, YYYY-MM (e.g. 2023-12). Positives = "
                         "first exclusion strictly AFTER the freeze instant (the "
                         "as-of graph keeps exclusions <= it, so same-day bans are "
                         "features, not labels). Pass the SAME cutoff as --asof.")
    ap.add_argument("--out", default="future_bans_2024plus.csv")
    ap.add_argument("--positives-only", action="store_true",
                    help="write only the forward positives (default writes the full "
                         "split so you can build the negative pool too).")
    ap.add_argument("--fca-tier", default="all", choices=["all", "hard", "hard+weak"],
                    help="label policy from fca_relevance: 'all' = every exclusion "
                         "(the v1-v6 ruler); 'hard' = only statutes/reasons that look "
                         "like an FCA case (fraud convictions, kickbacks, excessive "
                         "claims, CIA breach, abuse of billing privileges); "
                         "'hard+weak' adds misconduct that is not FCA on its face "
                         "(license actions, patient abuse, derivative exclusions). "
                         "Loan defaults, paperwork failures, not-operational "
                         "revocations and OpenSanctions are never positives under a "
                         "tier policy.")
    ap.add_argument("--tier-report", default=None,
                    help="write the per-tier/basis positive counts as Markdown "
                         "(default: <out>.TIERS.md)")
    args = ap.parse_args()

    from .fca_relevance import tier_report

    nodes = _load(args.exclusion_nodes)
    label = build_prospective_label(nodes, args.cutoff, fca_tier=args.fca_tier)
    pos = int(label["is_prospective_positive"].sum())
    pre = int(label["was_excluded_pre_cutoff"].sum())
    und = int(label["undated_exclusion"].sum())
    dropped = dropped_by_policy(label, args.cutoff)

    written = label[label["is_prospective_positive"] == 1] if args.positives_only else label
    written.to_csv(args.out, index=False)

    rep_path = Path(args.tier_report) if args.tier_report else Path(
        str(args.out).rsplit(".", 1)[0] + ".TIERS.md")
    rep = [f"# Forward-label tier report — policy `{args.fca_tier}`, cutoff {args.cutoff}",
           "",
           f"_{pos:,} forward positives; {len(dropped):,} post-cutoff exclusions NOT "
           f"counted under this policy; {pre:,} excluded at/before the cutoff (dropped "
           f"from the test); {und:,} undated. Tiers per `src/model_a/fca_relevance.py` "
           f"— hard = looks like an FCA case, weak = misconduct not FCA on its face, "
           f"excluded = not FCA-relevant (loan default, paperwork, not operational)._",
           "", "## Positives counted", "", tier_report(label)]
    if len(dropped):
        rep += ["", "## Post-cutoff exclusions NOT counted (by policy)", "",
                tier_report(dropped.assign(is_prospective_positive=1))]
    rep_path.write_text("\n".join(rep) + "\n", encoding="utf-8")

    print(f"wrote {args.out} — {len(written):,} rows  (label policy: {args.fca_tier})")
    print(f"  forward positives (first ban after {args.cutoff}): {pos:,}  <- the held-out label")
    print(f"  post-cutoff exclusions not counted by policy:     {len(dropped):,}")
    print(f"  excluded at/before cutoff (drop from the test):    {pre:,}")
    print(f"  undated exclusions (not counted as positive):  {und:,}")
    print(f"  tier report: {rep_path}")
    print("  Modelling label from public integrity events — leads for review, not accusations.")
    if pos == 0:
        # Fail LOUD and NONZERO: a zero-positive forward label means the exclusion
        # file was stale or (worse) an as-of-filtered graph was passed by mistake —
        # every date <= cutoff. Downstream the A/B would train on an all-zero label
        # and print a confident garbage verdict, so stop the batch here instead.
        print("  ERROR: zero forward positives. Causes: (a) --exclusion-nodes points "
              "at an AS-OF graph (all dates <= cutoff) — pass the FULL graph's "
              "exclusion_nodes.parquet; (b) the exclusion download is older than the "
              "cutoff — refresh LEIE/OpenSanctions. Aborting so the frozen test "
              "cannot silently run on an empty label.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
