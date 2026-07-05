"""
preflight.py — the source REGISTRY + "do I have the data?" check, in one place.

This module is the single source of truth for every external source the platform
knows about. Each entry records, together:
  * whether it is WIRED IN (the ``adapter`` module that loads it — every source
    listed here has a built adapter), and
  * whether the DATA is present (a read-only stat of ``preclean/``).

Two things that were previously easy to confuse — "is the adapter wired?" vs.
"do I have the file?" — now live side by side.

Modes:
  (default)      data check: [ok] / [MISSING] / [rename?] per source, grouped
                 CORE / UNLOCKS / OPTIONAL, honoring ``--data-root`` / MEDICAID_DATA_ROOT.
  --registry     print the full catalog (wired adapter + expected file + schemes fed
                 + gating), with the data column if preclean/ exists. Works with no data.
  --markdown     emit the catalog as a Markdown table to stdout. Regenerate the doc with:
                     python -m src.preflight --markdown > docs/SOURCE_REGISTRY.md

This checks INPUTS + wiring; ``python -m src.pipeline_status`` checks which pipeline
OUTPUTS have been built. Run preflight first.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import NamedTuple


class Src(NamedTuple):
    key: str
    label: str
    folder: str            # relative to preclean/; "" = preclean root
    names: list            # accepted file names (case-insensitive; first is canonical)
    exts: list             # extension fallback for a glob match in a subfolder
    feeds: str             # schemes / features this source produces
    adapter: str           # the module that WIRES it in (all of these are built)
    gating: str = "public"  # "public" | "gated" (license / DUA / counsel)


CORE = [
    Src("spending", "Medicaid spending by HCPCS", "", ["Spending.csv"], [".csv", ".parquet"],
        "the whole pipeline — builds spending_fact", "attempt_2.ingest.integrate"),
    Src("nppes", "NPPES provider registry", "", ["NPPES.csv"], [".csv"],
        "provider_dim (every per-NPI feature)", "attempt_2 (provider_dim) + ingest_cms.nppes_api"),
    Src("pecos", "PECOS enrollment / ownership", "", ["PECOS.csv"], [".csv"],
        "ownership graph + CCN↔NPI crosswalk", "entity_graph + ingest_cms.ccn_npi_crosswalk"),
    Src("leie", "LEIE exclusions (the PU label)", "", ["Caught.csv", "leie.csv", "LEIE.csv"],
        [".csv"], "exclusion nodes + provider_on_leie label", "enforcement.label_store"),
]

UNLOCKS = [
    Src("partb", "Medicare Part B by Provider & Service", "partb", ["partb.csv"], [".csv"],
        "upcoding (em_high_level_share, em_level_mean)", "ingest_cms.partb"),
    Src("partd", "Medicare Part D by Provider & Drug", "partd", ["partd.csv"], [".csv"],
        "drug_outlier, pharma_kickback (high_cost_drug_share)", "ingest_cms.partd"),
    Src("opioid", "Part D by Provider (opioid summary)", "opioid", ["opioid.csv"], [".csv"],
        "pill_mill (opioid_claim_share, long_acting_share)", "ingest_cms.opioid"),
    Src("dmepos", "DMEPOS by Referring Provider & Service", "dmepos", ["dmepos.csv"], [".csv"],
        "dme_ring signals + the INFLUENCED-dollars exposure basis for referrers "
        "(needs the by-referring-AND-SERVICE layout: Rfrg_NPI + HCPCS; the "
        "by-referring summary lacks HCPCS and fails)", "ingest_cms.dmepos"),
    Src("open_payments", "Open Payments (general + research)", "open_payments",
        ["open_payments.csv"], [".csv"],
        "pharma_kickback (op_payment_concentration, utilization_corr)", "ingest_cms.openpayments"),
    Src("nadac", "NADAC drug acquisition cost", "nadac", ["nadac.csv"], [".csv"],
        "drug_spread_anomaly (with the NDC claim slice)", "ingest_cms.nadac", "gated"),
    Src("saturation", "Market Saturation & Utilization", "saturation", ["saturation.csv"],
        [".csv"], "saturation_fraud (market_saturation_index)", "ingest_cms.saturation"),
    Src("hrsa_340b", "340B OPAIS Covered Entity Daily Report", "hrsa_340b",
        ["opais.xlsx", "opais.csv"], [".xlsx", ".csv"],
        "contract_pharmacy (needs openpyxl)", "ingest_cms.hrsa_340b"),
    Src("nppes_deactivation", "NPPES Deactivated NPI Report", "nppes_deactivation",
        ["deactivation.zip", "deactivation.xlsx", "deactivation.csv"], [".zip", ".xlsx", ".csv"],
        "invalid_identity / post-deactivation billing (needs openpyxl)",
        "ingest_cms.nppes_deactivation"),
    Src("order_referring", "Order & Referring file", "order_referring",
        ["order_referring.csv"], [".csv"],
        "dme_ring (ineligible-referral share)", "ingest_cms.order_referring"),
    Src("pbj", "PBJ daily nurse staffing", "facility", ["pbj.csv"], [".csv"],
        "worthless_services (understaffing)", "ingest_cms.facility"),
    Src("hospice", "Care Compare hospice provider data", "facility", ["hospice.csv"], [".csv"],
        "hospice_ineligibility (live-discharge rate)", "ingest_cms.facility"),
    Src("deficiencies", "Care Compare health deficiencies", "facility", ["deficiencies.csv"],
        [".csv"], "worthless_services (citations)", "ingest_cms.facility"),
    Src("hcris", "HCRIS cost reports", "hcris", ["hcris.csv"], [".csv", ".parquet"],
        "cost_report_fraud (needs ccn_to_npi)", "ingest_cms.hcris"),
    Src("pos", "Provider of Services file", "pos", ["pos.csv"], [".csv"],
        "worthless_services (capacity; needs ccn_to_npi)", "ingest_cms.pos"),
    Src("nucc", "NUCC taxonomy code set", "nucc", ["nucc_taxonomy.csv"], [".csv"],
        "coherent peer grouping — improves EVERY scheme", "ingest_cms.nucc_taxonomy"),
    Src("revocations", "CMS revoked Medicare providers", "revocations",
        ["revoked_providers.csv", "revocations.csv"], [".csv"],
        "widens the PU label", "enforcement.medicare_revocations"),
    Src("docgraph", "Physician Shared Patient Patterns", "docgraph",
        ["docgraph.csv", "shared_patient.csv"], [".csv"],
        "referral edges → referral-ring detection", "ingest_cms.docgraph"),
    Src("state_licensing", "State medical/nursing/pharmacy license + discipline",
        "state_licensing", ["licenses.csv"], [".csv"],
        "identity corroboration + soft exclusions", "enforcement.state_licensing"),
]

OPTIONAL = [
    Src("nucc_xwalk", "NUCC specialty crosswalk", "nucc", ["specialty_crosswalk.csv"], [".csv"],
        "sharper peer cohorts (companion to nucc)", "ingest_cms.nucc_taxonomy"),
    Src("usps_cmra", "USPS CMRA registry", "usps", ["cmra.csv"], [".csv"],
        "addr_is_cmra exact-match mailbox flag", "model_a.address_grounding"),
    Src("opensanctions", "OpenSanctions debarment export", "opensanctions",
        ["targets.simple.csv", "entities.ftm.json"], [".csv", ".json"],
        "widened exclusions / label + ~45 STATE Medicaid exclusion lists in one "
        "file (bulk CSV is a FREE download — only the API costs; commercial-USE "
        "sign-off from Brad runs in parallel, doesn't block testing)",
        "enforcement.opensanctions"),
    Src("preclusion", "CMS Preclusion List", "preclusion", ["preclusion_list.csv"], [".csv"],
        "widened label (sponsor-channel data)", "enforcement.preclusion", "gated"),
    Src("dmf", "SSA Death Master File", "dmf", ["dmf.csv"], [".csv"],
        "billing_after_death (needs name/DOB)", "enforcement.death_master", "gated"),
    Src("owners", "PECOS ownership detail", "owners", ["owners.csv"], [".csv"],
        "richer ownership edges", "entity_graph (ownership)"),
    Src("cms_priority", "CMS priority lists (moratoria/revalidation/SFF)", "cms_priority",
        ["moratoria.csv", "revalidation_due.csv", "sff.csv"], [".csv"],
        "revalidation-wave overlay flags for government-interest + Model C",
        "enforcement.cms_priority_lists"),
    Src("hcpcs_time", "HCPCS→minutes map (time-based codes)", "hcpcs_time",
        ["hcpcs_minutes.csv"], [".csv"],
        "impossible_day approximation (implied hours/day in the busiest month)",
        "ingest_cms.sector_schemes"),
    Src("zip_county", "HUD/Census ZIP→county crosswalk", "hud",
        ["zip_county.csv"], [".csv"],
        "county-grain market saturation (upgrades the state fallback)",
        "ingest_cms.saturation (attach_market_saturation)"),
]

ALL_SECTIONS = [("CORE", CORE), ("UNLOCK", UNLOCKS), ("OPTIONAL", OPTIONAL)]

# derived files YOU build from the above (land in processed/) — informational.
DERIVED = [
    ("processed/ccn_to_npi.parquet",
     "python -m src.ingest_cms.ccn_npi_crosswalk  (from PECOS) — unlocks facility/HCRIS/POS"),
    ("processed/exclusions_medicare_revocations.parquet",
     "python -m src.enforcement.medicare_revocations --in preclean/revocations/... --out ..."),
    ("processed/ndc_claims.parquet",
     "python -m src.ingest_cms.claim_slices --kind ndc ...  (needs an NDC-level feed; often gated)"),
]


def _scan(preclean: Path, folder: str, names: list, exts: list):
    """Return (status, detail). status in {ok, missing, rename}."""
    base = preclean / folder if folder else preclean
    if not base.exists():
        return "missing", "folder not found"
    listing = [p for p in base.iterdir() if p.is_file()]
    lower = {p.name.lower(): p for p in listing}
    for n in names:                                   # exact (case-insensitive) name
        if n.lower() in lower:
            return "ok", lower[n.lower()].name
    if folder:
        ext_hits = [p for p in listing if p.suffix.lower() in exts]
        if ext_hits:
            return "ok", f"{ext_hits[0].name} (matched by extension)"
        if listing:
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
        for s in items:
            status, detail = _scan(preclean, s.folder, s.names, s.exts)
            ok += status == "ok"
            loc = f"preclean/{s.folder}/" if s.folder else "preclean/"
            print(f"  {_MARK[status]} {s.label}   ({s.adapter})")
            print(f"            {loc}{s.names[0]}  →  {s.feeds}")
            if status != "ok":
                print(f"            ({detail})")
            results[s.key] = status
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


def _data_status(preclean: Path | None, s: Src) -> str:
    if preclean is None or not preclean.exists():
        return "—"
    status, _ = _scan(preclean, s.folder, s.names, s.exts)
    return {"ok": "present", "missing": "MISSING", "rename": "rename?"}[status]


def registry(data_root: Path | None, as_markdown: bool) -> None:
    """Emit the full catalog: wired adapter + expected file + schemes + gating (+ data)."""
    preclean = (data_root / "preclean") if data_root else None
    checked = preclean is not None and preclean.exists()
    if as_markdown:
        print("# SOURCE REGISTRY\n")
        print("_Generated by `python -m src.preflight --markdown`. The single source of truth "
              "for every external source: whether its adapter is **wired in**, the file it "
              "expects, the schemes it **feeds**, and its gating. Every source listed has a "
              "built adapter. The Data column is filled only when run against a `--data-root`._\n")
        note = f"checked against {preclean}" if checked else "not checked (run with --data-root)"
        print(f"_Data column: {note}._\n")
        print("| Source | Tier | Wired adapter | Expected file | Feeds | Gating | Data |")
        print("|---|---|---|---|---|---|---|")
        for tier, items in ALL_SECTIONS:
            for s in items:
                loc = f"preclean/{s.folder}/{s.names[0]}" if s.folder else f"preclean/{s.names[0]}"
                print(f"| `{s.key}` | {tier} | `{s.adapter}` | `{loc}` | {s.feeds} | "
                      f"{s.gating} | {_data_status(preclean, s)} |")
        print("\n**Derived (you build these):**\n")
        for path, how in DERIVED:
            print(f"- `{path}` — {how}")
        return
    # plain text
    print("SOURCE REGISTRY  (wired = adapter built; data = file present)\n")
    for tier, items in ALL_SECTIONS:
        print(f"=== {tier} ===")
        for s in items:
            print(f"  {s.key:20s} data={_data_status(preclean, s):8s} "
                  f"gating={s.gating:7s} wired={s.adapter}")
            print(f"  {'':20s} feeds: {s.feeds}")
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", default=None,
                    help="data folder containing preclean/ (default: MEDICAID_DATA_ROOT "
                         "or ~/Desktop/data)")
    ap.add_argument("--registry", action="store_true",
                    help="print the full source catalog (wired + feeds + gating), with data "
                         "status if --data-root has data")
    ap.add_argument("--markdown", action="store_true",
                    help="emit the catalog as Markdown (regenerates docs/SOURCE_REGISTRY.md)")
    args = ap.parse_args()
    env_root = os.environ.get("MEDICAID_DATA_ROOT")
    root = Path(args.data_root or env_root) if (args.data_root or env_root) else None
    if args.markdown or args.registry:
        registry(root, as_markdown=args.markdown)
    else:
        run(root or Path.home() / "Desktop" / "data")


if __name__ == "__main__":
    main()
