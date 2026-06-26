"""
preflight.py — "do I have the data, and is it named right?" in one command.

Run before the pipeline to confirm every expected input file is present in
``preclean/`` and named in a form the loaders recognize. Read-only: it just stats
files. Honors ``MEDICAID_DATA_ROOT`` or ``--data-root`` (point it at your Desktop
data folder on Windows). For each dataset it reports:

  [ok]      a recognized file is present
  [MISSING] no file found (folder absent or empty)
  [rename?] the folder has files but NONE match an expected name/extension — likely
            a naming problem (this is the most common foot-gun)

Datasets are grouped: CORE (required for anything to run), UNLOCKS (each adds
schemes), and OPTIONAL/GATED (licensing or legal gates — fine to skip). It ends with
how many unlocks are ready, what each missing one would add, and the next command.

This checks INPUTS; ``python -m src.pipeline_status`` checks which pipeline OUTPUTS
have been built. Run preflight first, then pipeline_status.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

# key, label, folder (relative to preclean; "" = preclean root), accepted names
# (case-insensitive; first is canonical), accepted extensions for a glob fallback,
# what it unlocks.
CORE = [
    ("spending", "Medicaid spending by HCPCS", "", ["Spending.csv"], [".csv", ".parquet"],
     "the whole pipeline — builds spending_fact"),
    ("nppes", "NPPES provider registry", "", ["NPPES.csv"], [".csv"],
     "provider_dim (every per-NPI feature)"),
    ("pecos", "PECOS enrollment / ownership", "", ["PECOS.csv"], [".csv"],
     "ownership graph + CCN↔NPI crosswalk"),
    ("leie", "LEIE exclusions (the PU label)", "", ["Caught.csv", "leie.csv", "LEIE.csv"],
     [".csv"], "exclusion nodes + the provider_on_leie training label"),
]

UNLOCKS = [
    ("partb", "Medicare Part B by Provider & Service", "partb", ["partb.csv"], [".csv"],
     "upcoding"),
    ("partd", "Medicare Part D by Provider & Drug", "partd", ["partd.csv"], [".csv"],
     "drug_outlier, pharma_kickback"),
    ("opioid", "Part D by Provider (opioid summary)", "opioid", ["opioid.csv"], [".csv"],
     "pill_mill"),
    ("dmepos", "DMEPOS by Referring Provider & Service", "dmepos", ["dmepos.csv"], [".csv"],
     "dme_ring"),
    ("open_payments", "Open Payments (general + research)", "open_payments",
     ["open_payments.csv"], [".csv"], "pharma_kickback co-occurrence"),
    ("nadac", "NADAC drug acquisition cost", "nadac", ["nadac.csv"], [".csv"],
     "drug_spread_anomaly (with the NDC claim slice)"),
    ("saturation", "Market Saturation & Utilization", "saturation", ["saturation.csv"],
     [".csv"], "saturation_fraud"),
    ("hrsa_340b", "340B OPAIS Covered Entity Daily Report", "hrsa_340b",
     ["opais.xlsx", "opais.csv"], [".xlsx", ".csv"], "contract_pharmacy"),
    ("nppes_deactivation", "NPPES Deactivated NPI Report", "nppes_deactivation",
     ["deactivation.zip", "deactivation.xlsx", "deactivation.csv"], [".zip", ".xlsx", ".csv"],
     "invalid_identity (post-deactivation billing)"),
    ("order_referring", "Order & Referring file", "order_referring",
     ["order_referring.csv"], [".csv"], "dme_ring (ineligible-referral share)"),
    ("pbj", "PBJ daily nurse staffing", "facility", ["pbj.csv"], [".csv"],
     "worthless_services (understaffing)"),
    ("hospice", "Care Compare hospice provider data", "facility", ["hospice.csv"], [".csv"],
     "hospice_ineligibility (live discharge)"),
    ("deficiencies", "Care Compare health deficiencies", "facility", ["deficiencies.csv"],
     [".csv"], "worthless_services (citations)"),
    ("hcris", "HCRIS cost reports", "hcris", ["hcris.csv"], [".csv", ".parquet"],
     "cost_report_fraud"),
    ("pos", "Provider of Services file", "pos", ["pos.csv"], [".csv"],
     "worthless_services (capacity)"),
    ("nucc", "NUCC taxonomy code set", "nucc", ["nucc_taxonomy.csv"], [".csv"],
     "coherent peer grouping — improves EVERY scheme (build this one)"),
    ("revocations", "CMS revoked Medicare providers", "revocations",
     ["revoked_providers.csv", "revocations.csv"], [".csv"], "widens the PU label"),
]

OPTIONAL = [
    ("nucc_xwalk", "NUCC specialty crosswalk", "nucc", ["specialty_crosswalk.csv"], [".csv"],
     "sharper peer cohorts (optional companion to nucc_taxonomy)"),
    ("usps_cmra", "USPS CMRA registry", "usps", ["cmra.csv"], [".csv"],
     "addr_is_cmra exact-match mailbox flag"),
    ("opensanctions", "OpenSanctions debarment export", "opensanctions",
     ["targets.simple.csv", "entities.ftm.json"], [".csv", ".json"],
     "widened exclusions / label (commercial license — Brad decision)"),
    ("preclusion", "CMS Preclusion List", "preclusion", ["preclusion_list.csv"], [".csv"],
     "widened label (sponsor-channel data — gated)"),
    ("dmf", "SSA Death Master File", "dmf", ["dmf.csv"], [".csv"],
     "billing_after_death (needs name/DOB; gated)"),
    ("owners", "PECOS ownership detail", "owners", ["owners.csv"], [".csv"],
     "richer ownership edges (optional; PECOS already carries owners)"),
]

# derived files YOU build from the above (land in processed/) — informational.
DERIVED = [
    ("processed/ccn_to_npi.parquet",
     "python -m src.ingest_cms.ccn_npi_crosswalk  (from PECOS) — unlocks facility/HCRIS/POS"),
    ("processed/exclusions_medicare_revocations.parquet",
     "python -m src.enforcement.medicare_revocations --in preclean/revocations/... --out ..."),
    ("processed/ndc_claims.parquet",
     "python -m src.ingest_cms.claim_slices --kind ndc ...  (needs an NDC-level claims feed; often gated)"),
]


def _scan(preclean: Path, folder: str, names: list[str], exts: list[str]):
    """Return (status, detail). status in {ok, missing, rename}."""
    base = preclean / folder if folder else preclean
    if not base.exists():
        return "missing", "folder not found"
    listing = [p for p in base.iterdir() if p.is_file()]
    lower = {p.name.lower(): p for p in listing}
    for n in names:                                   # exact (case-insensitive) name
        if n.lower() in lower:
            return "ok", lower[n.lower()].name
    # Extension fallback ONLY for dataset-specific subfolders (mirrors loader glob).
    # Root-level core files share preclean/, so a bare-ext match there would falsely
    # pass one core file for another — require an exact name for those.
    if folder:
        ext_hits = [p for p in listing if p.suffix.lower() in exts]
        if ext_hits:
            return "ok", f"{ext_hits[0].name} (matched by extension)"
        if listing:                                   # files there, but none recognized
            sample = ", ".join(sorted(p.name for p in listing)[:3])
            return "rename", f"has {len(listing)} file(s) but none match (e.g. {sample})"
    return "missing", "no recognized file"


_MARK = {"ok": "[ok]     ", "missing": "[MISSING]", "rename": "[rename?]"}


def run(data_root: Path) -> dict:
    preclean = data_root / "preclean"
    print(f"Preflight data check — {preclean}")
    if not preclean.exists():
        print(f"  !! preclean/ not found under {data_root}. Pass --data-root <your data folder>.")
        return {"core_ok": 0, "core_total": len(CORE)}
    results = {}

    def _section(title, items):
        print(f"\n=== {title} ===")
        ok = 0
        for key, label, folder, names, exts, unlocks in items:
            status, detail = _scan(preclean, folder, names, exts)
            ok += status == "ok"
            loc = f"preclean/{folder}/" if folder else "preclean/"
            print(f"  {_MARK[status]} {label}")
            print(f"            {loc}{names[0]}  →  {unlocks}")
            if status != "ok":
                print(f"            ({detail})")
            results[key] = status
        return ok

    core_ok = _section("CORE — required for the pipeline to run", CORE)
    unlock_ok = _section("UNLOCKS — each adds fraud schemes", UNLOCKS)
    _section("OPTIONAL / GATED — fine to skip", OPTIONAL)

    print("\n=== DERIVED — you BUILD these from the files above (land in processed/) ===")
    for path, how in DERIVED:
        exists = (data_root / path).exists()
        print(f"  {'[ok]     ' if exists else '[ to build ]'} {path}")
        if not exists:
            print(f"            {how}")

    print("\n--- summary ---")
    print(f"  CORE:    {core_ok}/{len(CORE)} present"
          + ("  ✓ pipeline can run" if core_ok == len(CORE)
             else "  !! pipeline BLOCKED until all core files are present"))
    print(f"  UNLOCKS: {unlock_ok}/{len(UNLOCKS)} present  "
          f"({len(UNLOCKS) - unlock_ok} schemes/feature-sets not yet enabled)")
    if core_ok == len(CORE):
        print("\n  Next: python -m src.attempt_2.ingest.integrate   (then `make graph`, "
              "`make provider-features`)")
        print("  Then: python -m src.pipeline_status   (checks which OUTPUTS are built)")
    results.update({"core_ok": core_ok, "core_total": len(CORE),
                    "unlock_ok": unlock_ok, "unlock_total": len(UNLOCKS)})
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", default=None,
                    help="data folder containing preclean/ (default: MEDICAID_DATA_ROOT "
                         "or ~/Desktop/data)")
    args = ap.parse_args()
    root = Path(args.data_root or os.environ.get(
        "MEDICAID_DATA_ROOT", str(Path.home() / "Desktop" / "data")))
    run(root)


if __name__ == "__main__":
    main()
