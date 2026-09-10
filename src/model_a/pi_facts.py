"""
pi_facts.py — the billed-reality fact sheet behind each pursuit target.

A field investigator catches a fraudulent practice in a lie by holding a
NUMBER the practice cannot explain: the front desk says 25 visits a day
while the billing implies 71 hours of face time; the "facility" bills
per-diem patient days with six recorded beds; weekend staffing is one car
in the lot while weekend billing matches a Tuesday. This module extracts,
for a list of target organizations, every such quantified fact the run
matrix holds — the operator-side half of each contradiction — so dossiers
ship with specifics instead of percentiles.

For each member NPI of each target org it emits the raw value + peer
percentile (when present) of the fact columns below, plus org rollups.
Output: pi_facts.json (structured, for dossier generation) and
PI_FACTS.md (human-readable).

  python -m src.model_a.pi_facts --run-dir <run_folder> \
      --orgs "org:pac:123;org:name:FOO" --out-prefix pi_facts
  python -m src.model_a.pi_facts --run-dir @champion --orgs-file targets.txt
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

# fact column -> (plain-English label, how the PI uses it in the field)
FACT_COLS: dict[str, tuple[str, str]] = {
    "time_minutes_per_day": (
        "billed face-time minutes per working day",
        "Ask how many patients a day the practice sees; 480 min is a full "
        "8-hour day of pure face time — values near or above it are the "
        "impossible-day contradiction."),
    "mue_violation_share": (
        "share of billing units above CMS per-day maximums",
        "Units the payer's own edits call unbillable; ask what a typical "
        "visit involves and compare to the units billed."),
    "pos_bed_count": (
        "beds on record for this location",
        "Count beds implied by per-diem/inpatient billing vs this number; "
        "ask where patients sleep."),
    "pbj_understaffing": (
        "staffing shortfall vs expected hours (payroll-based)",
        "Cars in the lot at shift change vs the staffing the billing "
        "implies."),
    "pbj_weekend_understaffing": (
        "weekend staffing gap (payroll-based)",
        "Saturday lot check: weekend billing at weekday levels with a "
        "skeleton crew is the contradiction."),
    "hospice_gip_share": (
        "share of hospice days billed at general-inpatient rate",
        "GIP requires inpatient-level care; ask where GIP patients are "
        "housed and verify the unit exists."),
    "hospice_live_discharge_rate": (
        "share of hospice patients discharged alive",
        "High live-discharge suggests enrolling patients who are not "
        "dying; ask about typical length of stay."),
    "hospice_nf_care_share": (
        "share of hospice care delivered in nursing facilities",
        "Nursing-home-based hospice concentration; verify the facility "
        "relationships."),
    "billing_after_deactivation": (
        "billing activity after an NPI deactivation date",
        "Ask who currently works there; billing under departed or "
        "deactivated identities is per-se false."),
    "market_saturation_index": (
        "county market saturation for this provider type",
        "Count similar agencies in the same building/plaza; shared suites "
        "and registered agents are the cluster tell."),
    "growth_level_shift": (
        "abrupt level-shift in billing volume",
        "Ask when this location opened / changed hands; compare to when "
        "the billing actually ramped."),
    "new_code_burst": (
        "sudden adoption of new billing codes",
        "Ask what new services launched and when; verify equipment/staff "
        "for them exist."),
    "em_high_level_share": (
        "share of visits billed at the highest complexity levels",
        "Ask how long a typical appointment runs; top-level codes imply "
        "40+ minute encounters."),
    "services_per_bene": (
        "services billed per patient",
        "Ask how often a typical patient is seen; compare."),
    "dme_high_cost_item_share": (
        "share of DME dollars in high-cost items",
        "Verify a storefront with actual inventory exists; ask what they "
        "stock."),
    "opioid_claim_share": (
        "share of prescriptions that are opioids",
        "Waiting-room observation; pharmacies nearby will know the "
        "prescriber."),
    "clinical_implausibility": (
        "clinically implausible service combinations",
        "Ask what conditions they treat; the billed combinations imply "
        "capabilities to verify on site."),
    "capacity_mismatch": (
        "billed volume vs physical capacity",
        "Square footage / rooms / chairs vs billed throughput."),
    "net_paid": (
        "Medicaid dollars paid (window total)",
        "The scale anchor for every other question."),
}


def extract(matrix: pd.DataFrame, org_ids: list[str]) -> dict:
    """Per-org, per-member fact sheets from the run matrix."""
    mkey = matrix["org_node_id"].astype(str)
    out: dict = {}
    for oid in org_ids:
        m = matrix.loc[mkey == str(oid)]
        if not len(m):
            out[str(oid)] = {"error": "org not found in matrix"}
            continue
        members = []
        for _, r in m.iterrows():
            facts = {}
            for col, (label, use) in FACT_COLS.items():
                val = r.get(col)
                pct = r.get(f"{col}__peerpct")
                val = None if pd.isna(val) else float(val)
                pct = None if pct is None or pd.isna(pct) else float(pct)
                if val is not None or pct is not None:
                    facts[col] = {"label": label, "value": val,
                                  "peer_percentile": pct, "field_use": use}
            members.append({
                "npi": str(r.get("npi", "")),
                "name": str(r.get("provider_name", "") or ""),
                "city": str(r.get("addr_city", "") or ""),
                "state": str(r.get("practice_state", "") or ""),
                "facts": facts,
            })
        paid = pd.to_numeric(m.get("net_paid"), errors="coerce").fillna(0)
        beds = pd.to_numeric(m.get("pos_bed_count"), errors="coerce").fillna(0) \
            if "pos_bed_count" in m.columns else pd.Series(dtype=float)
        out[str(oid)] = {
            "org_name": str(m.iloc[0].get("org_legal_name", "") or
                            m.iloc[0].get("provider_name", "") or ""),
            "n_members": int(len(m)),
            "total_net_paid": float(paid.sum()),
            "total_beds_recorded": float(beds.sum()) if len(beds) else None,
            "members": members,
        }
    return out


def to_markdown(res: dict) -> str:
    L = ["# PI FACT SHEETS — billed-reality numbers per target", "",
         "_Each fact is the operator-side half of a field contradiction: "
         "what the billing claims, so the investigator knows which innocent "
         "answer is impossible. Values are from the run matrix; peer "
         "percentile 0.98 = higher than 98% of comparable providers._", ""]
    for oid, o in res.items():
        if "error" in o:
            L.append(f"## {oid}: {o['error']}")
            continue
        L.append(f"## {o['org_name']}  ({oid})")
        L.append(f"- members: {o['n_members']} | Medicaid paid (window): "
                 f"${o['total_net_paid']:,.0f}"
                 + (f" | beds recorded: {o['total_beds_recorded']:.0f}"
                    if o.get("total_beds_recorded") else ""))
        for mem in o["members"]:
            if not mem["facts"]:
                continue
            L.append(f"### NPI {mem['npi']} — {mem['city']}, {mem['state']}")
            for col, f in sorted(
                    mem["facts"].items(),
                    key=lambda kv: -(kv[1]["peer_percentile"] or 0)):
                v = f["value"]
                vtxt = (f"{v:,.2f}" if v is not None and abs(v) < 1e6
                        else (f"${v:,.0f}" if v is not None else "—"))
                p = f["peer_percentile"]
                ptxt = f" (peer pct {p:.2f})" if p is not None else ""
                L.append(f"- **{f['label']}**: {vtxt}{ptxt}")
                L.append(f"  - field use: {f['field_use']}")
        L.append("")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True,
                    help="run folder (or '@champion')")
    ap.add_argument("--orgs", default=None,
                    help="semicolon-separated org_node_ids")
    ap.add_argument("--orgs-file", default=None,
                    help="file with one org_node_id per line (or a "
                         "priority-list CSV: its org_id column is used)")
    ap.add_argument("--top", type=int, default=None,
                    help="with a priority-list CSV in --orgs-file: only the "
                         "first N rows")
    ap.add_argument("--out-prefix", default="pi_facts")
    args = ap.parse_args()
    from .run_registry import resolve_run_dir
    run_dir = Path(resolve_run_dir(args.run_dir))

    org_ids: list[str] = []
    if args.orgs:
        org_ids += [o.strip() for o in args.orgs.split(";") if o.strip()]
    if args.orgs_file:
        p = Path(args.orgs_file)
        if p.suffix.lower() == ".csv":
            df = pd.read_csv(p, dtype=str)
            col = "org_id" if "org_id" in df.columns else df.columns[0]
            ids = df[col].dropna().tolist()
            org_ids += ids[:args.top] if args.top else ids
        else:
            org_ids += [ln.strip() for ln in p.read_text().splitlines()
                        if ln.strip()]
    if not org_ids:
        raise SystemExit("no orgs given — use --orgs or --orgs-file")

    matrix = pd.read_parquet(run_dir / "provider_features_for_model.parquet")
    res = extract(matrix, org_ids)
    jp = run_dir / f"{args.out_prefix}.json"
    mp = run_dir / f"{args.out_prefix.upper()}.md"
    jp.write_text(json.dumps(res, indent=1), encoding="utf-8")
    mp.write_text(to_markdown(res), encoding="utf-8")
    found = sum(1 for v in res.values() if "error" not in v)
    print(f"[pi_facts] {found}/{len(org_ids)} orgs -> {jp}")
    print(f"[pi_facts] fact sheets -> {mp}")


if __name__ == "__main__":
    main()
