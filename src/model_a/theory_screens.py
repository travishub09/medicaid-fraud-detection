"""theory_screens.py — rank every billing NPI in the country on a CONDUCT the government
has already prosecuted, from the spending fact, before any research credit is spent.

Why (2026-10-29, the kill round): six deep research prompts ended with three survivors,
and the deciding fact on all three labs came from one line over our own fact table (the
code mix: definitive drug-testing panels on every specimen, more definitive lines than
screens, two or three panels per patient per month). The model's rank said the lab was
unusual; the code mix said why; the research then spent credits confirming it. A screen
turns that order around: the theory is chosen first, the fact table lists every provider
in the shape with dollars and years, the national names and the standing verdicts are
set aside, and the court-record check and the research prompts go only to names that
already carry a theory.

Each screen is a numerator code family (the money the theory is about), a denominator
family (what ordinary practice bills beside it), and two ratios: lines on the numerator
over lines on the denominator (reflex: every screen confirmed) and numerator lines per
patient-month (intensity). A provider passes when both hold; the list is ranked on
numerator dollars. The innocent explanations are printed with every screen and are ruled
out first. Leads for human and counsel review, never accusations.

    python -m src.model_a.theory_screens --spending processed/spending_fact.parquet \
        --provider-dim processed/provider_dim.parquet --out <folder> [--screen definitive_reflex] [--top 50]
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.attempt_2.duck import connect as _duck_connect
from src.model_a.pursuit_dossier import is_national_name
from src.model_a.pursuit_verdicts import load_verdicts


def _sql_str(p) -> str:
    return str(p).replace("'", "''")


def _cols(con, parquet: Path) -> list[str]:
    return [c[0] for c in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{_sql_str(parquet)}')").fetchall()]


@dataclass
class Screen:
    key: str
    title: str
    theory: str
    numerator: tuple[str, ...]
    denominator: tuple[str, ...]
    flag_codes: tuple[str, ...] = ()           # the top-tier code whose share of the numerator is reported
    min_paid_num: float = 1_000_000.0          # dollars on the numerator over the window
    min_reflex: float = 1.0                    # numerator lines / denominator lines
    min_intensity: float = 1.5                 # numerator lines / numerator patient-months
    innocent: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    precedent: str = ""
    taxonomy_prefixes: tuple[str, ...] = ()    # restrict to these NUCC prefixes when provider_dim carries taxonomy ('' = any)
    extra: dict = field(default_factory=dict)
    # the second track (2026-10-29): shapes a field investigator can verify on site
    grain: str = "billing"                     # 'billing' (the biller) or 'servicing' (the individual on the claim)
    per_day: bool = False                      # report peak lines per calendar day (average daily census / units a day)
    min_per_day: float | None = None           # rule on that peak (None = reported, not ruled)
    entity_types: tuple[str, ...] = ()         # restrict to NPPES entity types ('1' individuals, '2' organizations)
    capacity_rule: bool = False                # join licensed capacity by NPI; occupancy = peak per day / capacity
    min_occupancy: float | None = None         # with a capacity on file: candidate only above this (under_capacity otherwise)
    candidate_taxonomies: tuple[str, ...] = () # the roles the theory is about (an individual outside them is 'not_<role>' and listed, not ranked)
    # the state policy library (src/policy/registry, 2026-11-01): (topic, param, column) = the state's own
    # number for this screen, joined per row as `policy_bar`; `over_policy` = the row's column above it
    policy: tuple[str, str, str] | None = None
    report_statuses: tuple[str, ...] = ()      # statuses the report tables beside the candidates (the shapes worth a look)
    numerator_like: tuple[str, ...] = ()       # SQL LIKE patterns added to the numerator (a code FAMILY: 'E%' for DME)
    exclude_taxonomy_prefixes: tuple[str, ...] = ()  # NUCC prefixes set aside as 'other_type' (a broker is not a fleet)
    address_rule: bool = False                 # candidate only when the practice address is a mailbox / PO box or shared by many organizations
    min_addr_orgs: int = 5                     # distinct organizations at the exact address that make it 'shared'
    # the national-share rule (2026-11-02): one supplier filling a large share of the NATION's lines on a
    # code (seven suppliers in one state filled 99.6 percent of the nation's hip braces); the share is the
    # max over the supplier's codes carrying at least min_code_dollars of its own dollars
    national_share_rule: bool = False
    min_national_share: float = 0.05
    min_code_dollars: float = 250_000.0
    # 2026-11-02 (the first run): the top of the table was Cochlear, Novocure, Zoll, Tactile and
    # Bioventus at 100 percent of codes only THEY bill (a maker's proprietary product under its own
    # supplier number), beside codes that two or three suppliers in the nation bill to Medicaid at
    # all. A share is a market fact only on a code MANY suppliers bill: the share is taken over
    # codes with at least min_national_suppliers billing NPIs and min_national_dollars nationally;
    # a supplier whose only dominant code is thinner is listed as proprietary_code, never ranked
    min_national_suppliers: int = 0
    min_national_dollars: float = 0.0


SCREENS: dict[str, Screen] = {
    "definitive_reflex": Screen(
        key="definitive_reflex",
        title="Definitive drug testing on every specimen",
        theory=("The laboratory bills a definitive (LC-MS/MS) drug panel, G0480-G0483, on every "
                "specimen rather than only on a positive or unexpected screen, at two or more panels "
                "per patient per month, with the 22-plus-class code carrying the money. Medical "
                "necessity of the definitive line is the claim; each unnecessary definitive line is a "
                "damages unit. Medicaid pays the definitive line three to four times the screen."),
        numerator=("G0480", "G0481", "G0482", "G0483"),
        denominator=("80305", "80306", "80307"),
        flag_codes=("G0483",),
        innocent=("an opioid-treatment program's testing schedule written into the order (monthly plus random is "
                  "required by rule, but as a screen, not a definitive panel)",
                  "a pain or addiction practice whose protocol orders definitive testing on a stated clinical "
                  "indication for every patient",
                  "a state Medicaid policy that pays only the definitive code for this provider type",
                  "a reference laboratory receiving pre-screened specimens from clients who ran the screen "
                  "on site (the denominator then sits on the client's claims, not the lab's)"),
        roles=("laboratory sales representatives who set the standing order at the clinic",
               "billing and revenue-cycle staff who built the claim edits",
               "the laboratory director and compliance officer",
               "clinic staff at the top ordering practices who saw the requisition form"),
        precedent="Millennium Health (2015, $256M), Genesis Reference Laboratories (2023), the 2024 "
                  "marketer-kickback laboratory settlements: definitive testing without individualized need.",
        taxonomy_prefixes=("291U",),
        policy=("drug_testing", "max_definitive_per_year", "intensity_per_year"),
    ),
    "uti_pcr_reflex": Screen(
        key="uti_pcr_reflex",
        title="Urine-infection PCR panels in place of culture",
        theory=("The laboratory bills multiplex PCR organism-detection panels (87798 per organism, 87505-87507, "
                "87801) on urine specimens at several hundred dollars where an eight-dollar culture is the "
                "standard, with few or no culture lines beside them. The claim is medical necessity of the "
                "panel; the Omni Healthcare qui tam (N.D. Tex. 3:22-cv-01710) pleads this theory against "
                "twenty labs, so the court-record check is the first gate on every name here."),
        # 87505-87507 are the gastrointestinal-pathogen multiplex panels (stool), not urine codes:
        # the kill round on Medical Diagnostic Laboratories (2026-10-29) read them in the numerator,
        # so they are out; a lab that bills them on urine is a records question, never a screen fact
        numerator=("87798", "87801", "87640", "87641", "87653", "87481",
                   "87486", "87491", "87591", "87661"),
        denominator=("87086", "87088", "87077", "87186"),
        flag_codes=("87798",),
        innocent=("a molecular-only laboratory whose clients run cultures elsewhere",
                  "respiratory or wound PCR panels billed under the same codes (the code family is not "
                  "urine-specific; the specimen is a records question)",
                  "COVID-era respiratory panels (check the years)"),
        roles=("sales representatives and the requisition form they placed",
               "the billing manager who mapped organisms to 87798 units",
               "the medical director who approved the panel"),
        precedent="Omni Healthcare v. Thermo Fisher et al. (pending; government motion to dismiss); "
                  "the 2022-2024 respiratory-panel settlements.",
        taxonomy_prefixes=("291U",),
    ),
    "perdiem_occupancy": Screen(
        key="perdiem_occupancy",
        title="Per-diem beds billed above the building",
        theory=("A residential or day program bills a per-diem code for more patient-days than its "
                "licensed or certified capacity can hold: the average daily census in the peak month "
                "(per-diem lines over calendar days) against the licensed places. A count on site is "
                "the claim; every patient-day above the building is a damages unit, and a census that "
                "never falls below capacity is a standing-order shape in its own right."),
        numerator=("H0017", "H0018", "H0019", "H2013", "H2020", "H2034", "H2036", "H0035", "S9480",
                   "S5102", "T2016", "T2031", "T2033", "T1020"),
        denominator=(),
        flag_codes=("H0019", "T2016", "T2033"),
        min_paid_num=1_000_000.0,
        min_intensity=15.0,
        per_day=True,
        capacity_rule=True,
        min_occupancy=1.0,
        report_statuses=("needs_capacity", "under_capacity"),
        innocent=("several licensed sites under one billing number (the capacity file sums a number's sites, "
                  "but a site the roster does not carry under the same name is missed)",
                  "a day program whose per-diem is a slot, not a bed (slots turn over inside a day)",
                  "a capacity table that is stale (a licence expanded after the roster was pulled)",
                  "a billing number that bills for sister facilities under a management agreement"),
        roles=("the house managers and direct-care staff who know the census by shift",
               "the admissions coordinator who knows the waiting list and the double-ups",
               "the biller who knows which site each per-diem was coded to",
               "the state licensing surveyor's reports (public) for the licensed count and the citations"),
        precedent="The residential and group-home per-diem cases (the 'Special Homes' shape: residents per day "
                  "against licensed places); nursing-home census cases.",
    ),
    "attendant_hours": Screen(
        key="attendant_hours",
        title="Attendant hours no one person can work",
        theory=("A personal-care or home-health aide (the individual in the servicing field of the claim) "
                "is paid for more 15-minute units in a day than a person can deliver, often across "
                "several agencies' claims in the same month. The aide's day is the claim: an "
                "investigator verifies whether the aide was at the client's home during the billed "
                "hours, and the agency's office exists. Units above the day are damages; units billed "
                "by two agencies for one aide are the kickback or the ghost."),
        numerator=("T1019", "S5125", "S5126", "S5130", "S5131", "T1004", "T1005", "G0156"),
        denominator=(),
        flag_codes=("T1019",),
        min_paid_num=150_000.0,
        min_intensity=0.0,
        grain="servicing",
        per_day=True,
        min_per_day=64.0,
        entity_types=("1",),
        # the aide roles: home health aide, personal care attendant / technician, homemaker, chore,
        # nurse's aide, religious nonmedical nursing personnel. A physician, nurse practitioner, social
        # worker or agency number in the servicing field is the 'whose number went on the claim' shape
        # (the attending-concentration lens), listed under not_aide, never ranked as an aide's day.
        candidate_taxonomies=("373H", "3747", "374U", "376J", "3725", "3726", "376K", "374T"),
        report_statuses=("self_billed", "not_in_nppes", "not_aide", "placeholder"),
        innocent=("the file's line is a client-day, not a 15-minute unit (read the distribution line under the "
                  "table first: a median peak near 1 means lines are days and the rule re-runs on clients a day)",
                  "the servicing field is the biller's own number (most states' personal-care claims carry no aide "
                  "identity in this file; the aide's day lives in the state's EVV aggregator or claims-level data)",
                  "a live-in or 24-hour shared-care arrangement billed under one aide's number by rule",
                  "an agency that puts its supervisor's or owner's NPI in the servicing field for every aide "
                  "(then the number is the agency's, and the question is who rendered)",
                  "two agencies that are the same owner's two licences"),
        roles=("the aides themselves and the clients' families (who was in the home, which hours)",
               "the agency scheduler who built the timesheets and the EVV records",
               "the biller who keyed the servicing NPI",
               "the state's EVV vendor records (the aggregator) for the check-in and check-out stamps"),
        precedent="The home-health and personal-care impossible-day prosecutions (state MFCUs; the EVV "
                  "cases since the 21st Century Cures mandate); the New York and Texas attendant-care cases.",
    ),
    # ---- the visit-decided shapes registered 2026-10-29 (operator: 'diversify beyond labs to what a PI
    # can fully validate'): a fleet can be counted in a lot, a stretcher patient can be watched walking in,
    # a storefront either exists or is a mailbox. Each card is one day in the field.
    "ambulance_repetitive": Screen(
        key="ambulance_repetitive",
        title="Non-emergency ambulance runs for patients who can sit",
        theory=("An ambulance company bills basic-life-support non-emergency transports (A0428, and A0426 "
                "ALS non-emergency) at dialysis-schedule intensity (three round trips a week per patient) "
                "with few or no emergency runs beside them. Medicaid pays a stretcher transport only when "
                "the patient cannot sit or be moved by other means; a patient who walks to the rig is the "
                "claim. Every transport of an ambulatory patient is a damages unit, and the physician "
                "certification statements on file are the records question behind the observation."),
        numerator=("A0428", "A0426"),
        denominator=("A0427", "A0429", "A0433", "A0434"),
        flag_codes=("A0428",),
        min_paid_num=1_000_000.0,
        min_reflex=3.0,
        min_intensity=8.0,
        per_day=True,
        taxonomy_prefixes=("3416",),
        innocent=("an interfacility-transfer contract (hospital to nursing home) billed under the same code: the "
                  "patients are bed-bound by definition; the destination mix is the tell",
                  "a dialysis population that is in fact stretcher-bound (documented in the physician certification "
                  "statements, which the state can audit and a relator can describe)",
                  "a company whose emergency work bills under a municipal or hospital number, so the denominator "
                  "sits elsewhere",
                  "mileage or wait-time lines counted as transports (read the distribution line: lines a day against "
                  "the rigs seen)"),
        roles=("the EMTs and drivers (who walked to the rig, who was lifted)",
               "the dispatcher who scheduled the standing dialysis runs",
               "the biller who completed the medical-necessity fields and the PCS forms",
               "the dialysis center's front-desk and charge nurses (who arrives by ambulance and how)"),
        precedent="The 2013-2020 ambulance dialysis prosecutions (Philadelphia, Houston, Los Angeles, New Jersey): "
                  "stretcher transports of patients who could sit; the DOJ ambulance settlements under the "
                  "repetitive-patient prior-authorization rule.",
    ),
    "nemt_fleet": Screen(
        key="nemt_fleet",
        title="Transport trips a fleet cannot make",
        theory=("A non-emergency medical transportation company (vans, wheelchair vans, taxis) bills more "
                "trips a day than the vehicles it runs can make, or more trips per patient-month than a "
                "treatment schedule explains. The fleet is the claim: vehicles can be counted at the base "
                "and on the state registration, and a trip rate per vehicle per day above what a day holds "
                "(about twenty short trips) is the damages arithmetic. A company with no base and no vehicles "
                "is the phantom shape."),
        numerator=("T2001", "T2002", "T2003", "T2004", "T2005", "T2007", "T2049",
                   "A0080", "A0090", "A0100", "A0110", "A0120", "A0130", "A0140"),
        denominator=(),
        flag_codes=("A0130", "T2003"),
        min_paid_num=1_000_000.0,
        min_intensity=6.0,
        per_day=True,
        min_per_day=100.0,
        taxonomy_prefixes=("34",),
        exclude_taxonomy_prefixes=("347E",),
        innocent=("a transportation broker's number (a broker carries a network's trips; brokers are set aside by "
                  "taxonomy 347E and by name, but a broker enrolled under a plain transport taxonomy reads as a fleet)",
                  "mileage lines billed per mile under a trip code (read the distribution line first: a median peak "
                  "in the thousands means lines are miles, and the rule re-runs on trips)",
                  "a subcontracted fleet (the company dispatches other owners' vehicles under its number: the "
                  "vehicles exist, at other addresses)",
                  "a shared-ride or group-trip program where one vehicle carries several billed riders"),
        roles=("the drivers (how many runs a day, how many riders, which vehicles)",
               "the dispatcher who built the manifests",
               "the biller who keyed trips and mileage",
               "the dialysis and day-program sites that account for most trips (who actually arrived, and how)"),
        precedent="The NEMT prosecutions in Texas, Michigan, Minnesota and the District of Columbia (trips never "
                  "made, fleets that did not exist, dead or hospitalized riders); the OIG NEMT audits.",
    ),
    "dme_mailbox": Screen(
        key="dme_mailbox",
        title="Equipment suppliers billing from a mailbox",
        theory=("A durable medical equipment supplier bills Medicaid at scale from a practice address that is a "
                "commercial mailbox, a PO box, or a suite shared by many unrelated organizations. A DMEPOS "
                "supplier must keep a physical facility at an appropriate site (42 CFR 424.57(c)(7): not a "
                "post office box or commercial mail-receiving agency), with inventory and posted hours. The "
                "storefront is the claim: an investigator at the address finds a store, an empty unit, or a "
                "wall of mailboxes."),
        numerator=(),
        numerator_like=("E%", "K0%", "L%", "A4%", "A5%", "A6%", "A7%", "A9%", "B4%"),
        denominator=(),
        flag_codes=("E0601", "E1390", "K0823", "L0650", "A4253", "A9270"),
        min_paid_num=1_000_000.0,
        min_intensity=0.0,
        taxonomy_prefixes=("332B",),
        address_rule=True,
        innocent=("the registration lists a mailing address in the practice-location field by mistake (the real "
                  "store is elsewhere: a records fix, not a case)",
                  "an executive-suite building with many tenants and a real office inside it (the shared count "
                  "reads the building; the visit reads the unit)",
                  "a chain that lists its corporate office on every branch's record (set aside by name; a chain's "
                  "distinct-organization count is one)",
                  "a supplier that moved and the NPPES record is stale (the visit finds the new store)"),
        roles=("the delivery drivers and warehouse staff (is there inventory, who delivered what)",
               "customer-service and intake staff (who ordered, which physicians)",
               "the mailbox store or landlord (who rents the box or the unit)",
               "the billing staff (which beneficiaries, which prescriptions)"),
        precedent="The DME storefront and shell-company cases (Operation Brace Yourself 2019, the Miami and Houston "
                  "DME cases); CMS supplier-standard revocations for a non-operational site.",
    ),
    "dme_national_share": Screen(
        key="dme_national_share",
        title="One supplier filling a national code",
        theory=("A durable medical equipment supplier fills a large share of the NATION's Medicaid lines on one "
                "code (the public example: seven suppliers in one state filled 99.6 percent of the nation's hip "
                "braces), and, where the ordering physicians are known, its orders come from doctors across many "
                "states. A supplier dominating one code nationally, ordered by physicians it could never have met, "
                "is the shape of a marketed-product or kickback ring (telemarketing, a paid telehealth signature, a "
                "brace shipped unasked), not a local store. The claim is the medical necessity and the order of each "
                "item; the out-of-state order share is computed only when a supplier x ordering-physician table is "
                "given, and the national totals are printed so the reader sees the base."),
        numerator=(),
        numerator_like=("E%", "K0%", "L%", "A4%", "A5%", "A6%", "A7%", "A9%", "B4%"),
        denominator=(),
        flag_codes=("L0650", "L1833", "L0631", "L3916", "E0748", "A4253"),
        min_paid_num=1_000_000.0,
        min_intensity=0.0,
        taxonomy_prefixes=("332B",),
        national_share_rule=True,
        min_national_share=0.05,
        min_code_dollars=250_000.0,
        min_national_suppliers=25,
        min_national_dollars=2_000_000.0,
        report_statuses=("proprietary_code",),
        innocent=("a legitimate national mail-order specialist (diabetic supplies, ostomy, CPAP resupply) whose "
                  "share is the business model, with a storefront, a licence in every state it ships to, and orders "
                  "from the patient's own physician",
                  "a manufacturer's own supplier number (the maker bills its own product nationally)",
                  "a code so rare that one supplier is most of it (read the national totals: a 60 percent share of "
                  "$300k is a niche, not a ring)",
                  "a state-contracted sole supplier (incontinence, enteral) whose share is the contract",
                  "the ordering physician's NPPES state is stale or the group's billing state (an out-of-state "
                  "order from a locum or a telehealth group is lawful when the visit was real)"),
        roles=("customer-service and intake staff (who called the patient, who found the doctor)",
               "the marketing vendor and the telehealth company that supplied the signatures",
               "the ordering physicians' office staff at the top referring practices (did they see the patient)",
               "the billing staff (which codes, which beneficiaries, which states)",
               "the delivery and warehouse staff (was the item shipped, was it returned)"),
        precedent="Operation Brace Yourself (2019, $1.2B in orthotic braces ordered through telemedicine kickbacks); "
                  "Operation Rubber Stamp (2020); the 2021-2024 DME telemarketing and genetic-testing takedowns; "
                  "the OIG DME supplier-concentration audits.",
    ),
}


# the supplier x ordering-physician table for the national-share screen (the public Medicare DMEPOS
# by-referring file carries no supplier NPI, so this is a long table from a claims extract or a
# supplier-side file: one row per supplier x referrer with dollars or lines)
REFERRER_COLS = {
    "supplier_npi": ["supplier_npi", "billing_npi", "suplr_npi", "SUPLR_NPI", "npi", "NPI"],
    "referrer_npi": ["referrer_npi", "referring_npi", "rfrg_npi", "Rfrg_NPI", "RFRG_NPI", "ordering_npi",
                     "REFERRING_NPI", "ordering_referring_npi"],
    "dollars": ["dollars", "total_paid", "paid", "allowed", "total_allowed", "allowed_dollars",
                "Avg_Suplr_Mdcr_Alowd_Amt", "amount"],
    "lines": ["lines", "services", "total_claim_lines", "Tot_Suplr_Srvcs", "TOT_SUPLR_SRVCS",
              "number_of_supplier_services", "claims"],
}


def _read_any_sql(path: Path) -> str:
    """A DuckDB source expression for a parquet or csv (csv read all-VARCHAR so an NPI keeps
    its leading zero; Latin-1 tolerant like every raw-CSV entry point)."""
    p = _sql_str(path)
    if str(path).lower().endswith((".parquet", ".pq")):
        return f"read_parquet('{p}')"
    return f"read_csv_auto('{p}', all_varchar=true, encoding='latin-1')"


def _referrer_table(con, referrers: Path, provider_dim: Path | None) -> pd.DataFrame | None:
    """Per supplier NPI on the numerator: the share of its referred weight (dollars when the
    table carries them, else lines) whose ORDERING physician's state differs from the
    supplier's, the number of referrers and of referrer states. Both states come from
    provider_dim. Returns None (the ratio is not computed) when the table or provider_dim
    is missing or the columns cannot be resolved."""
    if referrers is None or not Path(referrers).exists() or provider_dim is None or not Path(provider_dim).exists():
        return None
    from src.attempt_2.clean_data import _resolve_columns
    src = _read_any_sql(Path(referrers))
    cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]
    resolved = _resolve_columns(cols, REFERRER_COLS)
    if "supplier_npi" not in resolved or "referrer_npi" not in resolved:
        print(f"[theory_screens] referrer table {Path(referrers).name}: no supplier / referrer NPI columns "
              f"(saw {cols[:8]}); the out-of-state order share is not computed")
        return None
    if "dollars" in resolved:
        w = f'CAST("{resolved["dollars"]}" AS DOUBLE)'
    elif "lines" in resolved:
        w = f'CAST("{resolved["lines"]}" AS DOUBLE)'
    else:
        w = "1.0"
    pc = _cols(con, Path(provider_dim))
    if "addr_state" not in pc:
        print("[theory_screens] provider_dim carries no addr_state; the out-of-state order share is not computed")
        return None
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE pd_state AS
        SELECT CAST(npi AS VARCHAR) AS npi, UPPER(TRIM(COALESCE(CAST(addr_state AS VARCHAR), ''))) AS st
        FROM read_parquet('{_sql_str(provider_dim)}')""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE ref_long AS
        SELECT TRIM(CAST("{resolved['supplier_npi']}" AS VARCHAR)) AS supplier_npi,
               TRIM(CAST("{resolved['referrer_npi']}" AS VARCHAR)) AS referrer_npi,
               COALESCE({w}, 0) AS w
        FROM {src}
        WHERE TRIM(CAST("{resolved['supplier_npi']}" AS VARCHAR)) IN (SELECT npi FROM num)""")
    return con.execute("""
        SELECT r.supplier_npi AS npi,
               CASE WHEN SUM(r.w) > 0 THEN SUM(CASE WHEN p.st <> '' AND s.st <> '' AND p.st <> s.st THEN r.w ELSE 0 END) / SUM(r.w) END
                   AS out_of_state_order_share,
               COUNT(DISTINCT r.referrer_npi) AS n_referrers,
               COUNT(DISTINCT CASE WHEN p.st <> '' THEN p.st END) AS n_referrer_states,
               SUM(r.w) AS referred_weight
        FROM ref_long r
        LEFT JOIN pd_state p ON p.npi = r.referrer_npi
        LEFT JOIN pd_state s ON s.npi = r.supplier_npi
        GROUP BY 1""").df()


def _national_share_table(con, p: str, key: str, code: str, lines: str, numpred: str, screen: Screen) -> pd.DataFrame:
    """Per NPI on the numerator: the code where the supplier's share of the NATION's lines (dollars when
    the file carries no lines) is largest among its codes carrying >= min_code_dollars, with the
    supplier's and the nation's lines and dollars on it and the number of suppliers billing it.
    The national base is every billing NPI on the code, not only the screen's provider type."""
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE bycode AS
        SELECT CAST({key} AS VARCHAR) npi, {code} AS hcpcs,
               SUM(CAST(total_paid AS DOUBLE)) paid, SUM({lines}) lines
        FROM read_parquet('{p}')
        WHERE {key} IS NOT NULL AND {numpred}
        GROUP BY 1, 2""")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE natcode AS
        SELECT hcpcs, SUM(paid) nat_paid, SUM(lines) nat_lines, COUNT(DISTINCT npi) nat_suppliers
        FROM bycode GROUP BY 1""")
    return con.execute(f"""
        WITH sh AS (
            SELECT b.npi, b.hcpcs, b.paid, b.lines, n.nat_paid, n.nat_lines, n.nat_suppliers,
                   CASE WHEN n.nat_lines > 0 THEN b.lines / n.nat_lines END AS line_share,
                   CASE WHEN n.nat_paid > 0 THEN b.paid / n.nat_paid END AS dollar_share,
                   (n.nat_suppliers >= {int(screen.min_national_suppliers)}
                    AND n.nat_paid >= {float(screen.min_national_dollars)}) AS is_market
            FROM bycode b JOIN natcode n USING (hcpcs)
            WHERE b.paid >= {float(screen.min_code_dollars)}
        ),
        ranked AS (
            SELECT *, COALESCE(line_share, dollar_share) AS share,
                   ROW_NUMBER() OVER (PARTITION BY npi ORDER BY COALESCE(line_share, dollar_share) DESC, paid DESC) AS rn
            FROM sh WHERE is_market
        ),
        anyranked AS (
            SELECT npi, hcpcs, COALESCE(line_share, dollar_share) AS share, nat_suppliers, nat_paid,
                   ROW_NUMBER() OVER (PARTITION BY npi ORDER BY COALESCE(line_share, dollar_share) DESC, paid DESC) AS rn
            FROM sh
        ),
        market AS (
            SELECT npi, share AS national_code_share, hcpcs AS top_share_code,
                   line_share AS top_share_lines, dollar_share AS top_share_dollars,
                   paid AS top_code_paid, lines AS top_code_lines,
                   nat_paid AS top_code_nat_paid, nat_lines AS top_code_nat_lines, nat_suppliers AS top_code_nat_suppliers
            FROM ranked WHERE rn = 1
        ),
        best_any AS (
            SELECT npi, share AS any_code_share, hcpcs AS any_code, nat_suppliers AS any_code_nat_suppliers,
                   nat_paid AS any_code_nat_paid
            FROM anyranked WHERE rn = 1
        )
        SELECT a.npi, m.national_code_share, m.top_share_code, m.top_share_lines, m.top_share_dollars,
               m.top_code_paid, m.top_code_lines, m.top_code_nat_paid, m.top_code_nat_lines, m.top_code_nat_suppliers,
               a.any_code_share, a.any_code, a.any_code_nat_suppliers, a.any_code_nat_paid
        FROM best_any a LEFT JOIN market m USING (npi)""").df()


def _codes_table(con, name: str, codes) -> None:
    con.execute(f"CREATE OR REPLACE TEMP TABLE {name}(hcpcs VARCHAR)")
    con.executemany(f"INSERT INTO {name} VALUES (?)", [(c.upper(),) for c in codes])


def _name_sql(pc: list[str]) -> str:
    """The display name from provider_dim: the organization's legal name when it is non-empty,
    else the person's name. Never the two glued together (a provider table that fills both
    columns with the same string doubled every name, and the docket search ran on the doubles)."""
    parts = [f"NULLIF(TRIM(CAST({c} AS VARCHAR)), '')" for c in ("org_legal_name", "provider_name") if c in pc]
    return "COALESCE(" + ", ".join(parts + ["''"]) + ")"


def _num_pred(code: str, screen: Screen) -> str:
    """The numerator predicate: exact codes in num_codes, plus any LIKE families (a DME screen's
    numerator is a code family, not a list)."""
    parts = [f"{code} IN (SELECT hcpcs FROM num_codes)"]
    parts += [f"{code} LIKE '{pat.upper()}'" for pat in screen.numerator_like]
    return "(" + " OR ".join(parts) + ")"


_UNIT_RE = r"(?i)\b(STE|SUITE|UNIT|APT|RM|ROOM|BLDG|FL|FLOOR|PMB|BOX)\b|#"   # a unit on the line: suite-level sharing


def _address_table(con, provider_dim: Path) -> None:
    """Per-NPI address grounding from provider_dim, in DuckDB: the practice line read against the
    mailbox / PO-box pattern (address_grounding._MAILBOX, one definition), and the number of distinct
    ORGANIZATION names and NPIs at the exact line + ZIP5 (a real clinic is one org with many NPIs; a
    dozen unrelated orgs at one suite is a shell cluster). Writes TEMP TABLE addr_flags."""
    from src.model_a.address_grounding import _MAILBOX
    pc = _cols(con, Path(provider_dim))
    line = "addr_line1" if "addr_line1" in pc else None
    if line is None:
        con.execute("CREATE OR REPLACE TEMP TABLE addr_flags(npi VARCHAR, addr_line1 VARCHAR, addr_is_mailbox BOOLEAN, "
                    "addr_distinct_orgs BIGINT, addr_npis BIGINT)")
        return
    zipc = "substr(CAST(addr_zip AS VARCHAR), 1, 5)" if "addr_zip" in pc else "''"
    org = _name_sql(pc)
    pattern = "(?i)" + _MAILBOX.pattern
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE addr_all AS
        SELECT CAST(npi AS VARCHAR) AS npi,
               UPPER(TRIM(COALESCE(CAST({line} AS VARCHAR), ''))) AS line1,
               COALESCE({zipc}, '') AS zip5,
               UPPER(TRIM({org})) AS orgname
        FROM read_parquet('{_sql_str(provider_dim)}')""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE addr_flags AS
        SELECT a.npi, a.line1 AS addr_line1,
               regexp_matches(a.line1, ?) AS addr_is_mailbox,
               c.n_orgs AS addr_distinct_orgs, c.n_npis AS addr_npis,
               regexp_matches(a.line1, ?) AS addr_has_unit
        FROM addr_all a
        JOIN (SELECT line1, zip5, COUNT(DISTINCT orgname) AS n_orgs, COUNT(*) AS n_npis
              FROM addr_all WHERE line1 <> '' GROUP BY 1, 2) c
          ON a.line1 = c.line1 AND a.zip5 = c.zip5
        WHERE a.npi IN (SELECT npi FROM num)""", [pattern, _UNIT_RE])


def _days_in_month_sql(month_expr: str) -> str:
    """DuckDB: calendar days in the month of a 'YYYY-MM' expression."""
    return f"EXTRACT(day FROM last_day(CAST({month_expr} || '-01' AS DATE)))"


def _weekdays_in_month_sql(month_expr: str) -> str:
    """DuckDB: Monday-to-Friday days in the month (a day program runs about 22 of them; the
    New Jersey adult-day per diem is capped at five days a week per beneficiary, so a
    calendar-day census understates the open-day census: the 2026-10-29 kill round)."""
    first = f"CAST({month_expr} || '-01' AS DATE)"
    return (f"(SELECT COUNT(*) FROM range({first}, last_day({first}) + INTERVAL 1 DAY, INTERVAL 1 DAY) r(d) "
            f"WHERE dayofweek(CAST(r.d AS DATE)) BETWEEN 1 AND 5)")


NATIONAL_SHARE_COLS = ("national_code_share", "top_share_code", "top_share_lines", "top_share_dollars",
                       "top_code_paid", "top_code_lines", "top_code_nat_paid", "top_code_nat_lines",
                       "top_code_nat_suppliers",
                       # the best share over EVERY code (no market filter): a proprietary / thin code is
                       # reported here and never ranked
                       "any_code_share", "any_code", "any_code_nat_suppliers", "any_code_nat_paid")
REFERRER_SHARE_COLS = ("out_of_state_order_share", "n_referrers", "n_referrer_states", "referred_weight")


def run_screen(spending: Path, screen: Screen, con=None, provider_dim: Path | None = None,
               verdicts: pd.DataFrame | None = None, top: int = 50, capacity: Path | None = None,
               policy_df: pd.DataFrame | None = None, referrers: Path | None = None) -> pd.DataFrame:
    """Every NPI on the screen's numerator (the billing NPI, or the servicing NPI when the
    screen's grain is 'servicing') with the ratios, the dollars by year, the status
    (candidate / national / hospital / government / verdict:<v> / below_floor / fails_<rule>)
    and the rank among candidates. Per-day screens add the peak month's lines per calendar
    day (a per-diem's average daily census; an aide's units a day) and, with a capacity
    table (licensed_capacity_by_npi.parquet: npi, licensed_capacity), the occupancy ratio.
    National-share screens add the supplier's largest share of the nation's lines on one
    code and, with a supplier x referrer table (``referrers``), the out-of-state order share
    (NULL, and the report says so, without the table). DuckDB end to end; the fact never
    enters pandas."""
    con = con or _duck_connect()
    p = _sql_str(spending)
    cols = _cols(con, Path(spending))
    lines = "CAST(total_claim_lines AS DOUBLE)" if "total_claim_lines" in cols else "NULL"
    pats = "CAST(total_patients AS DOUBLE)" if "total_patients" in cols else "NULL"
    key = "servicing_npi" if screen.grain == "servicing" and "servicing_npi" in cols else "billing_npi"
    month = "substr(CAST(service_month AS VARCHAR), 1, 7)"
    _codes_table(con, "num_codes", screen.numerator or ("__none__",))
    _codes_table(con, "den_codes", screen.denominator or ("__none__",))
    _codes_table(con, "flag_codes", screen.flag_codes or ("__none__",))
    code = "UPPER(TRIM(CAST(hcpcs_code AS VARCHAR)))"
    numpred = _num_pred(code, screen)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE tot AS
        SELECT CAST({key} AS VARCHAR) npi, SUM(CAST(total_paid AS DOUBLE)) paid_all,
               MAX({month}) last_month_all
        FROM read_parquet('{p}') WHERE {key} IS NOT NULL GROUP BY 1""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE num AS
        SELECT CAST({key} AS VARCHAR) npi,
               SUM(CAST(total_paid AS DOUBLE)) paid_num,
               SUM({lines}) lines_num, SUM({pats}) pm_num,
               SUM(CASE WHEN {code} IN (SELECT hcpcs FROM flag_codes) THEN {lines} ELSE 0 END) lines_flag,
               COUNT(DISTINCT {month}) months_num,
               MIN({month}) first_month, MAX({month}) last_month,
               COUNT(DISTINCT CAST(billing_npi AS VARCHAR)) n_billers,
               SUM(CASE WHEN CAST(billing_npi AS VARCHAR) = CAST({key} AS VARCHAR) THEN {lines} ELSE 0 END) lines_self
        FROM read_parquet('{p}')
        WHERE {key} IS NOT NULL AND {numpred}
        GROUP BY 1""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE den AS
        SELECT CAST({key} AS VARCHAR) npi, SUM(CAST(total_paid AS DOUBLE)) paid_den,
               SUM({lines}) lines_den, SUM({pats}) pm_den
        FROM read_parquet('{p}')
        WHERE {key} IS NOT NULL AND {code} IN (SELECT hcpcs FROM den_codes)
        GROUP BY 1""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE byyear AS
        SELECT CAST({key} AS VARCHAR) npi, substr(CAST(service_month AS VARCHAR), 1, 4) yr,
               SUM(CAST(total_paid AS DOUBLE)) paid
        FROM read_parquet('{p}')
        WHERE {key} IS NOT NULL AND {numpred}
        GROUP BY 1, 2""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE bymonth AS
        SELECT npi, mon,
               lines_sum / {_days_in_month_sql('mon')} AS lines_per_day,
               lines_sum / {_weekdays_in_month_sql('mon')} AS lines_per_weekday,
               pats
        FROM (
            SELECT CAST({key} AS VARCHAR) npi, {month} AS mon, SUM({lines}) AS lines_sum, SUM({pats}) AS pats
            FROM read_parquet('{p}')
            WHERE {key} IS NOT NULL AND {numpred}
            GROUP BY 1, 2
        )""")
    df = con.execute("""
        SELECT n.npi, n.paid_num, t.paid_all,
               CASE WHEN t.paid_all > 0 THEN n.paid_num / t.paid_all END AS num_share,
               n.lines_num, COALESCE(d.lines_den, 0) AS lines_den,
               CASE WHEN COALESCE(d.lines_den, 0) > 0 THEN n.lines_num / d.lines_den END AS reflex_ratio,
               CASE WHEN n.pm_num > 0 THEN n.lines_num / n.pm_num END AS intensity,
               CASE WHEN n.lines_num > 0 THEN n.lines_flag / n.lines_num END AS flag_share,
               COALESCE(d.paid_den, 0) AS paid_den, n.months_num, n.first_month, n.last_month, n.n_billers,
               CASE WHEN n.lines_num > 0 THEN n.lines_self / n.lines_num END AS self_share,
               (SELECT yr FROM byyear b WHERE b.npi = n.npi ORDER BY paid DESC, yr DESC LIMIT 1) AS peak_year,
               (SELECT MAX(paid) FROM byyear b WHERE b.npi = n.npi) AS peak_year_paid,
               (SELECT paid FROM byyear b WHERE b.npi = n.npi
                  AND b.yr = (SELECT MAX(yr) FROM byyear c WHERE c.npi = n.npi)) AS last_year_paid,
               (SELECT MAX(yr) FROM byyear b WHERE b.npi = n.npi) AS last_year,
               (SELECT MAX(lines_per_day) FROM bymonth m WHERE m.npi = n.npi) AS peak_lines_per_day,
               (SELECT mon FROM bymonth m WHERE m.npi = n.npi ORDER BY lines_per_day DESC, mon DESC LIMIT 1) AS peak_day_month,
               (SELECT MEDIAN(lines_per_day) FROM bymonth m WHERE m.npi = n.npi) AS median_lines_per_day,
               (SELECT MAX(lines_per_weekday) FROM bymonth m WHERE m.npi = n.npi) AS peak_lines_per_weekday
        FROM num n JOIN tot t USING (npi) LEFT JOIN den d USING (npi)
        ORDER BY n.paid_num DESC""").df()
    if provider_dim is not None and Path(provider_dim).exists() and len(df):
        pc = _cols(con, Path(provider_dim))
        name = _name_sql(pc)
        et = "CAST(entity_type AS VARCHAR)" if "entity_type" in pc else "NULL"
        st = "CAST(addr_state AS VARCHAR)" if "addr_state" in pc else "NULL"
        tx = "CAST(taxonomy_code AS VARCHAR)" if "taxonomy_code" in pc else "NULL"
        dim = con.execute(f"""
            SELECT CAST(npi AS VARCHAR) AS npi, TRIM({name}) AS name, {et} AS entity_type,
                   {st} AS state, {tx} AS taxonomy
            FROM read_parquet('{_sql_str(provider_dim)}')
            WHERE CAST(npi AS VARCHAR) IN (SELECT npi FROM num)""").df()
        df = df.merge(dim, on="npi", how="left")
    for c in ("name", "entity_type", "state", "taxonomy"):
        if c not in df.columns:
            df[c] = ""
    df["name"] = df["name"].fillna("").astype(str).str.strip()
    df["taxonomy"] = df["taxonomy"].fillna("").astype(str)
    df["entity_type"] = df["entity_type"].fillna("").astype(str).str.strip()
    df["licensed_capacity"] = pd.NA
    df["occupancy_ratio"] = pd.NA
    df["occupancy_weekday"] = pd.NA
    df["occupancy_two_shifts"] = pd.NA
    if screen.capacity_rule and capacity is not None and Path(capacity).exists() and len(df):
        cc = _cols(con, Path(capacity))
        cap_col = next((c for c in ("licensed_capacity", "capacity", "certified_beds", "beds") if c in cc), None)
        if cap_col:
            cap = con.execute(f"""
                SELECT CAST(npi AS VARCHAR) AS npi, MAX(CAST({cap_col} AS DOUBLE)) AS licensed_capacity
                FROM read_parquet('{_sql_str(capacity)}')
                WHERE CAST(npi AS VARCHAR) IN (SELECT npi FROM num) GROUP BY 1""").df()
            df = df.drop(columns=["licensed_capacity"]).merge(cap, on="npi", how="left")
            df["occupancy_ratio"] = df["peak_lines_per_day"] / df["licensed_capacity"].where(df["licensed_capacity"] > 0)
            # the two readings the kill round asked for: the open-day census (weekdays) against the
            # licence, and against TWO approved shifts (N.J.A.C. 8:43F-2.6 lets a proposal declare a
            # second shift; a facility still may not exceed its licensed capacity at any one time)
            df["occupancy_weekday"] = df["peak_lines_per_weekday"] / df["licensed_capacity"].where(df["licensed_capacity"] > 0)
            df["occupancy_two_shifts"] = df["occupancy_weekday"] / 2.0
    df["over_capacity"] = df["occupancy_ratio"].astype(float).gt(1.0).fillna(False) if df["occupancy_ratio"].notna().any() else False
    # THE STATE'S OWN NUMBER (2026-11-01): the policy library's parameter for this screen, per
    # row by state; `intensity_per_year` = numerator lines per patient-month x 12 (the unit the
    # coverage rules speak in); over_policy = above the state's covered count where one is on file
    df["policy_bar"] = pd.NA
    df["over_policy"] = pd.NA
    if screen.policy and policy_df is not None and len(policy_df) and len(df):
        from src.policy.registry import policy_param
        topic, param, col = screen.policy
        if col == "intensity_per_year":
            df["intensity_per_year"] = pd.to_numeric(df["intensity"], errors="coerce") * 12.0
        bars = {st: policy_param(policy_df, st, topic, param) for st in df["state"].fillna("").astype(str).unique() if st}
        df["policy_bar"] = df["state"].map(bars)
        if col in df.columns:
            v = pd.to_numeric(df[col], errors="coerce")
            b = pd.to_numeric(df["policy_bar"], errors="coerce")
            df["over_policy"] = (v > b).where(b.notna())
    df["capacity_known"] = bool(df["licensed_capacity"].notna().any())
    for c, v in (("addr_line1", ""), ("addr_is_mailbox", False), ("addr_distinct_orgs", pd.NA), ("addr_npis", pd.NA),
                 ("addr_has_unit", False)):
        df[c] = v
    if screen.address_rule and provider_dim is not None and Path(provider_dim).exists() and len(df):
        _address_table(con, Path(provider_dim))
        af = con.execute("SELECT * FROM addr_flags").df()
        if len(af):
            df = df.drop(columns=["addr_line1", "addr_is_mailbox", "addr_distinct_orgs", "addr_npis", "addr_has_unit"]) \
                   .merge(af, on="npi", how="left")
            df["addr_is_mailbox"] = df["addr_is_mailbox"].fillna(False).astype(bool)
            df["addr_has_unit"] = df["addr_has_unit"].fillna(False).astype(bool)
            df["addr_line1"] = df["addr_line1"].fillna("")
    for c in NATIONAL_SHARE_COLS + REFERRER_SHARE_COLS:
        df[c] = pd.NA
    df["referrers_known"] = False
    if screen.national_share_rule and len(df):
        ns = _national_share_table(con, p, key, code, lines, numpred, screen)
        if len(ns):
            df = df.drop(columns=list(NATIONAL_SHARE_COLS)).merge(ns, on="npi", how="left")
        rt = _referrer_table(con, Path(referrers) if referrers else None, Path(provider_dim) if provider_dim else None)
        if rt is not None:
            df = df.drop(columns=list(REFERRER_SHARE_COLS)).merge(rt, on="npi", how="left")
            df["referrers_known"] = True
            print(f"[theory_screens] {screen.key}: out-of-state order share computed for "
                  f"{int(df['out_of_state_order_share'].notna().sum())} suppliers from {Path(referrers).name}")
        else:
            print(f"[theory_screens] {screen.key}: no supplier x referrer table; the out-of-state order share "
                  "is not computed (--dme-referrers <parquet|csv> with supplier_npi, referrer_npi, dollars|lines)")
    out = classify(df, screen, verdicts, top=top)
    return out.drop(columns=["capacity_known"])


_GOVERNMENT_RE = re.compile(
    r"^(STATE|CITY|COUNTY|TOWN|COMMONWEALTH|DEPARTMENT|DEPT|BOARD)\s+OF\b|\bPUBLIC HEALTH (LAB|DEPT|DEPARTMENT)\b"
    r"|\b(COUNTY|CITY|STATE|PARISH|TOWN) (OF|HEALTH|LABORATORY|LAB|COMMISSION|DEPARTMENT|DEPT|BOARD|HOSPITAL DISTRICT)\b"
    r"|\bDEPARTMENT OF (PUBLIC HEALTH|HEALTH|MENTAL HEALTH|HUMAN SERVICES|SOCIAL SERVICES|BEHAVIORAL HEALTH)\b"
    r"|\bBOARD OF (HEALTH|EDUCATION|DEVELOPMENTAL DISABILITIES|MENTAL HEALTH)\b"
    # a county agency named by its programme (SHASTA COUNTY ALCOHOL AND DRUG PROGRAMS sat
    # first in the per-diem table on 2026-10-30 with a 15-bed detox licence matched by address)
    r"|\b(COUNTY|CITY|PARISH) (ALCOHOL|DRUG|MENTAL HEALTH|BEHAVIORAL|PROGRAMS?|SERVICES|AGENCY|OFFICE|HUMAN)\b"
    r"|\b(HEALTH|HOSPITAL|SCHOOL) DISTRICT\b|\bUNIFIED SCHOOL\b|\bPUBLIC SCHOOLS\b|\bVETERANS AFFAIRS\b"
)
_PLACEHOLDER_NPI_RE = re.compile(r"^\d?([1-9])\1{6,}\d*$")   # 1999999984, 1111111112: a filler number (a run of zeros is a fixture)


def is_government_name(name: str) -> bool:
    """A public-health laboratory (STATE OF / COUNTY OF / CITY OF ...): not an operator a relator
    comes from, so it leaves the candidate table under its own status (the first sweep's read,
    2026-10-29)."""
    nm = (name or "").upper().strip()
    return bool(nm) and bool(_GOVERNMENT_RE.search(nm))


def _shared_suite(r, screen: Screen) -> bool:
    """The shared-address basis: a UNIT on the line (suite, floor, box) shared by min_addr_orgs
    distinct organizations. 5640 HUDSON INDUSTRIAL PKWY with 17 tenants is a building (the first
    run ranked Tandem Diabetes and OptumRx on it); 1234 NW 7TH ST STE 200 with 12 is a cluster."""
    n = getattr(r, "addr_distinct_orgs", pd.NA)
    return bool(getattr(r, "addr_has_unit", False)) and not pd.isna(n) and int(n) >= screen.min_addr_orgs


def classify(df: pd.DataFrame, screen: Screen, verdicts: pd.DataFrame | None, top: int = 50) -> pd.DataFrame:
    """Status per row, in this order: a standing verdict wins (the operator's word), then the
    national list and hospital-licensed numbers (not an operator a relator comes from), then
    the dollar floor, then the two ratio rules. Candidates are ranked on numerator dollars."""
    v_by_name: dict[str, str] = {}
    if verdicts is not None and len(verdicts):
        v_by_name = dict(zip(verdicts["org_name"].astype(str).str.upper().str.strip(), verdicts["verdict"]))
    status, reason = [], []
    for r in df.itertuples():
        nm = str(r.name).upper().strip()
        tx = str(r.taxonomy or "")
        et = str(getattr(r, "entity_type", "") or "").strip()
        hit = v_by_name.get(nm) or next((v for k, v in v_by_name.items() if k and nm and (k in nm or nm in k)), None)
        in_nppes = bool(nm) or bool(tx) or bool(et)
        if _PLACEHOLDER_NPI_RE.match(str(r.npi)):
            status.append("placeholder"); reason.append("a filler number, not a provider")
        elif hit:
            status.append(f"verdict:{hit}"); reason.append("standing verdict")
        elif screen.grain == "servicing" and not in_nppes and float(r.paid_num or 0) >= screen.min_paid_num:
            status.append("not_in_nppes"); reason.append("the number is not in the NPPES file: deactivated or invalid, check the deactivation report")
        elif nm and is_national_name(nm):
            status.append("national"); reason.append("national name")
        elif is_government_name(nm):
            status.append("government"); reason.append("public-health laboratory (state / county / city)")
        elif tx[:2] in ("27", "28"):
            status.append("hospital"); reason.append("hospital-licensed number")
        elif screen.taxonomy_prefixes and tx and not any(tx.startswith(p) for p in screen.taxonomy_prefixes):
            status.append("other_type"); reason.append(f"taxonomy {tx} outside the screen's provider type")
        elif screen.exclude_taxonomy_prefixes and tx and any(tx.startswith(p) for p in screen.exclude_taxonomy_prefixes):
            status.append("other_type"); reason.append(f"taxonomy {tx} is set aside by the screen (a broker, not a fleet)")
        elif screen.entity_types and et and et not in screen.entity_types:
            status.append("other_type"); reason.append(f"entity type {et} outside the screen's provider type")
        elif float(r.paid_num or 0) < screen.min_paid_num:
            status.append("below_floor"); reason.append(f"numerator dollars under {screen.min_paid_num:,.0f}")
        elif screen.candidate_taxonomies and tx and not any(tx.startswith(p) for p in screen.candidate_taxonomies):
            status.append("not_aide"); reason.append(f"taxonomy {tx}: an individual's number at agency scale in the servicing field (whose number went on the claim)")
        elif screen.grain == "servicing" and float(getattr(r, "self_share", 0) or 0) >= 0.9:
            status.append("self_billed"); reason.append("the servicing field is the biller's own number (an agency enrolled under a person's number): the aide grain is absent for these claims")
        elif screen.address_rule and not (bool(getattr(r, "addr_is_mailbox", False)) or _shared_suite(r, screen)):
            status.append("address_clean"); reason.append("the practice address is neither a mailbox nor a SUITE shared by "
                                                           f"{screen.min_addr_orgs}+ organizations (a building without a unit "
                                                           "on the line is an office park, not a cluster)")
        elif screen.national_share_rule and pd.isna(getattr(r, "national_code_share", pd.NA)) \
                and not pd.isna(getattr(r, "any_code_share", pd.NA)) \
                and float(r.any_code_share) >= screen.min_national_share:
            status.append("proprietary_code"); reason.append(
                f"{float(r.any_code_share):.0%} of {getattr(r, 'any_code', '')} but only {int(r.any_code_nat_suppliers)} "
                f"suppliers / ${float(r.any_code_nat_paid):,.0f} bill it nationally (under {screen.min_national_suppliers} / "
                f"${screen.min_national_dollars:,.0f}): a maker's own product or a thin code, not a market share")
        elif screen.national_share_rule and pd.isna(getattr(r, "national_code_share", pd.NA)):
            status.append("fails_national_share"); reason.append(f"no code billed by {screen.min_national_suppliers}+ suppliers nationally "
                                                                 f"carries ${screen.min_code_dollars:,.0f} of the supplier's dollars")
        elif screen.national_share_rule and float(r.national_code_share) < screen.min_national_share:
            status.append("fails_national_share"); reason.append(f"largest share of a national code {float(r.national_code_share):.1%} "
                                                                 f"(rule {screen.min_national_share:.0%})")
        elif screen.capacity_rule and screen.min_occupancy is not None and bool(getattr(r, "capacity_known", False)) \
                and pd.isna(getattr(r, "licensed_capacity", pd.NA)):
            status.append("needs_capacity"); reason.append("no licensed capacity on file for this number (the roster for its state is not loaded)")
        elif screen.capacity_rule and screen.min_occupancy is not None and not pd.isna(getattr(r, "occupancy_ratio", pd.NA)) \
                and float(r.occupancy_ratio) < screen.min_occupancy:
            status.append("under_capacity"); reason.append(f"peak census under the licensed capacity (occupancy {float(r.occupancy_ratio):.2f})")
        else:
            ratio = r.reflex_ratio
            inten = r.intensity
            no_den = float(r.lines_den or 0) == 0 and float(r.lines_num or 0) > 0
            reflex_ok = no_den or (ratio is not None and not pd.isna(ratio) and float(ratio) >= screen.min_reflex)
            inten_ok = screen.min_intensity <= 0 or (inten is not None and not pd.isna(inten) and float(inten) >= screen.min_intensity)
            pday = getattr(r, "peak_lines_per_day", None)
            pday_ok = screen.min_per_day is None or (pday is not None and not pd.isna(pday) and float(pday) >= screen.min_per_day)
            if reflex_ok and inten_ok and pday_ok:
                status.append("candidate"); reason.append("passes the screen's rules")
            elif not reflex_ok:
                status.append("fails_reflex"); reason.append("more screens than definitive lines")
            elif not inten_ok:
                status.append("fails_intensity"); reason.append(f"under {screen.min_intensity} per patient-month")
            else:
                status.append("fails_per_day"); reason.append(f"peak under {screen.min_per_day} lines a day")
    df = df.copy()
    df["status"] = status
    df["status_reason"] = reason
    df["screen"] = screen.key
    if screen.address_rule:
        def _basis(r):
            if bool(r.addr_is_mailbox):
                return "mailbox"
            if _shared_suite(r, screen):
                return f"shared suite:{int(r.addr_distinct_orgs)} orgs"
            return ""
        df["address_basis"] = [_basis(r) for r in df.itertuples()]
    cand = df["status"].eq("candidate")
    df["rank"] = pd.NA
    if screen.address_rule and cand.any():
        # a mailbox first (the per-se site rule), then a shared suite, then dollars
        order = df[cand].assign(_mb=df.loc[cand, "addr_is_mailbox"].astype(bool).astype(int)) \
                        .sort_values(["_mb", "paid_num"], ascending=[False, False]).index
        df.loc[order, "rank"] = range(1, len(order) + 1)
    elif screen.capacity_rule and "occupancy_ratio" in df.columns and df["occupancy_ratio"].notna().any():
        # a count above the building first (the on-site fact), then dollars; rows without a capacity last
        order = df[cand].assign(_oc=df.loc[cand, "occupancy_ratio"].astype(float).fillna(-1.0)) \
                        .sort_values(["_oc", "paid_num"], ascending=[False, False]).index
        df.loc[order, "rank"] = range(1, len(order) + 1)
    elif screen.national_share_rule and cand.any():
        # the largest national share first (the per-se shape), then numerator dollars
        order = df[cand].assign(_ns=pd.to_numeric(df.loc[cand, "national_code_share"], errors="coerce").fillna(-1.0)) \
                        .sort_values(["_ns", "paid_num"], ascending=[False, False]).index
        df.loc[order, "rank"] = range(1, len(order) + 1)
    else:
        df.loc[cand, "rank"] = range(1, int(cand.sum()) + 1)
    return df


def _policy_lines(s: Screen, df: pd.DataFrame, policy_df: pd.DataFrame | None) -> list[str]:
    """The state rules on file for the candidates' states (the library's text + citation),
    so the innocent explanation is answered by the payer's own words where we have them."""
    if policy_df is None or not len(policy_df) or not s.policy:
        return []
    from src.policy.registry import rule_text
    topic = s.policy[0]
    cand = df[df["status"].eq("candidate")] if "status" in df.columns else df
    states = [st for st in cand["state"].fillna("").astype(str).value_counts().index.tolist() if st][:6]
    L = []
    for st in states:
        for r in rule_text(policy_df, st, topic, max_rows=2):
            L.append(f"- **{st} rule on file** ({r['citation']}; {r['url']}): \"{str(r['rule_text'])[:400]}\"")
    if L:
        L = ["**The state's own rule (policy library).** The `policy_bar` column is the state's covered count; "
             "`over_policy` marks a row above it."] + L + [""]
    return L


def to_markdown(results: dict[str, pd.DataFrame], top: int = 50, policy_df: pd.DataFrame | None = None) -> str:
    L = ["# THEORY SCREENS: every billing NPI ranked on a prosecuted conduct", "",
         "_From the Medicaid spending fact. A row is a question for the field and for counsel: the "
         "innocent explanations under each screen are ruled out first, the court-record check runs on "
         "every candidate before a research prompt, and a standing verdict or a national name is set "
         "aside, never ranked. Leads for human review, never accusations._", ""]
    for key, df in results.items():
        s = SCREENS[key]
        L += [f"## {s.title} (`{key}`)", "", f"**Theory.** {s.theory}", "",
              f"**Codes.** numerator {', '.join(s.numerator + tuple(f'{p} (family)' for p in s.numerator_like))}; denominator "
              f"{', '.join(s.denominator) if s.denominator else 'none (no ordinary practice beside it)'}"
              + (f"; flagged code {', '.join(s.flag_codes)}" if s.flag_codes else "")
              + (f"; grain = the {s.grain} NPI" if s.grain != "billing" else "") + ".", "",
              f"**Rules.** numerator dollars >= ${s.min_paid_num:,.0f}"
              + (f"; numerator lines / denominator lines >= {s.min_reflex} (or no denominator at all)" if s.denominator else "")
              + (f"; numerator lines per patient-month >= {s.min_intensity}" if s.min_intensity > 0 else "")
              + (f"; peak lines per calendar day >= {s.min_per_day}" if s.min_per_day is not None else "")
              + (f"; the practice address is a mailbox / PO box, or a SUITE (a unit on the line) shared by {s.min_addr_orgs}+ "
                 "distinct organizations (a mailbox ranks first, then dollars)" if s.address_rule else "")
              + (f"; the supplier's share of the NATION's lines on one code >= {s.min_national_share:.0%} (the max over its "
                 f"codes carrying >= ${s.min_code_dollars:,.0f} of its own dollars, on codes that >= {s.min_national_suppliers} "
                 f"suppliers and >= ${s.min_national_dollars:,.0f} nationally bill, so a maker's proprietary code is listed as "
                 "proprietary_code and never ranked; the national base counts every billing NPI on the code); the out-of-state order share = the share of the supplier's referred dollars (or lines) "
                 "whose ordering physician's NPPES state differs from the supplier's, reported, not ruled"
                 if s.national_share_rule else "")
              + ("; occupancy = peak lines a calendar day over licensed capacity where a capacity is known (over 1.0 "
                 "ranks first); open-day occ. = the same over weekdays (a five-day program); two-shift occ. = half of "
                 "that, the reading when the licence approves two sessions a day: a row above 1.0 on two shifts is over "
                 "the building on any lawful schedule"
                 if s.capacity_rule else "") + ".", "",
              "**Innocent explanations to rule out first.** " + " ".join(f"({i + 1}) {t}." for i, t in enumerate(s.innocent)), "",
              *_policy_lines(s, df, policy_df),
              "**Who would know.** " + "; ".join(s.roles) + ".", "",
              f"**Precedent.** {s.precedent}", ""]
        tally = df["status"].value_counts().to_dict()
        L.append("Status tally: " + ", ".join(f"{k} {v}" for k, v in sorted(tally.items())) + ".")
        L.append("")
        if s.per_day and "peak_lines_per_day" in df.columns and df["peak_lines_per_day"].notna().any():
            q = df["peak_lines_per_day"].astype(float).quantile([0.5, 0.9, 0.99])
            L.append(f"Peak lines per calendar day over every NPI on the numerator: median {q.iloc[0]:.1f}, "
                     f"p90 {q.iloc[1]:.1f}, p99 {q.iloc[2]:.1f} (read this before the table: it says what a "
                     f"line is in this file).")
            L.append("")
        if s.national_share_rule:
            known = bool(df["referrers_known"].astype(bool).any()) if "referrers_known" in df.columns else False
            L.append("Out-of-state order share: " + ("computed from the supplier x referrer table (both states from NPPES)."
                                                     if known else
                                                     "NOT computed (no supplier x ordering-physician table was given: "
                                                     "`--dme-referrers`); the column is blank, not zero."))
            L.append("")
        cand = df[df["status"].eq("candidate")].sort_values("rank").head(top)
        order_note = ("over-capacity first, then numerator dollars" if s.capacity_rule
                      else "mailbox first, then numerator dollars" if s.address_rule
                      else "largest national share first, then numerator dollars" if s.national_share_rule
                      else "by numerator dollars")
        L.append(f"### Candidates (top {min(top, len(cand))} of {int(df['status'].eq('candidate').sum())}, {order_note})")
        L.append("")
        if s.national_share_rule:
            L.append("| rank | npi | name | st | numerator $ | top code | share of national lines | share of national $ | "
                     "supplier $ on code | national $ on code | national suppliers on code | out-of-state order share | "
                     "referrers | referrer states | first | last | peak year | last year $ |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")

            def _pct(v):
                return "" if pd.isna(v) else f"{float(v) * 100:.1f}%"

            def _usd(v):
                return "" if pd.isna(v) else f"${float(v):,.0f}"

            def _int(v):
                return "" if pd.isna(v) else f"{int(v)}"

            for r in cand.itertuples():
                L.append(f"| {r.rank} | {r.npi} | {str(r.name)[:48]} | {r.state or ''} | ${r.paid_num:,.0f} | "
                         f"{'' if pd.isna(r.top_share_code) else r.top_share_code} | {_pct(r.top_share_lines)} | "
                         f"{_pct(r.top_share_dollars)} | {_usd(r.top_code_paid)} | {_usd(r.top_code_nat_paid)} | "
                         f"{_int(r.top_code_nat_suppliers)} | {_pct(r.out_of_state_order_share)} | {_int(r.n_referrers)} | "
                         f"{_int(r.n_referrer_states)} | {r.first_month} | {r.last_month} | "
                         f"{r.peak_year} (${(r.peak_year_paid or 0):,.0f}) | ${(r.last_year_paid or 0):,.0f} |")
        elif s.address_rule:
            L.append("| rank | npi | name | st | address | basis | orgs at address | NPIs at address | numerator $ | share of all | first | last | peak year | last year $ |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for r in cand.itertuples():
                no = "" if pd.isna(r.addr_distinct_orgs) else f"{int(r.addr_distinct_orgs)}"
                nn = "" if pd.isna(r.addr_npis) else f"{int(r.addr_npis)}"
                L.append(f"| {r.rank} | {r.npi} | {str(r.name)[:48]} | {r.state or ''} | {str(r.addr_line1)[:40]} | "
                         f"{getattr(r, 'address_basis', '')} | {no} | {nn} | ${r.paid_num:,.0f} | {(r.num_share or 0) * 100:.0f}% | "
                         f"{r.first_month} | {r.last_month} | {r.peak_year} (${(r.peak_year_paid or 0):,.0f}) | ${(r.last_year_paid or 0):,.0f} |")
        elif s.per_day:
            L.append("| rank | npi | name | st | numerator $ | share of all | per patient-month | peak lines/day (month) | median lines/day | per weekday | capacity | occupancy | open-day occ. | two-shift occ. | billers | first | last | peak year | last year $ |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for r in cand.itertuples():
                cap = "" if pd.isna(r.licensed_capacity) else f"{float(r.licensed_capacity):,.0f}"
                occ = "" if pd.isna(r.occupancy_ratio) else f"{float(r.occupancy_ratio):.2f}"
                occw = "" if pd.isna(r.occupancy_weekday) else f"{float(r.occupancy_weekday):.2f}"
                occ2 = "" if pd.isna(r.occupancy_two_shifts) else f"{float(r.occupancy_two_shifts):.2f}"
                pwd = "" if pd.isna(r.peak_lines_per_weekday) else f"{float(r.peak_lines_per_weekday):.1f}"
                L.append(f"| {r.rank} | {r.npi} | {str(r.name)[:48]} | {r.state or ''} | ${r.paid_num:,.0f} | "
                         f"{(r.num_share or 0) * 100:.0f}% | {0 if pd.isna(r.intensity) else r.intensity:.1f} | "
                         f"{0 if pd.isna(r.peak_lines_per_day) else r.peak_lines_per_day:.1f} ({r.peak_day_month}) | "
                         f"{0 if pd.isna(r.median_lines_per_day) else r.median_lines_per_day:.1f} | {pwd} | {cap} | {occ} | {occw} | {occ2} | "
                         f"{int(r.n_billers or 0)} | {r.first_month} | {r.last_month} | "
                         f"{r.peak_year} (${(r.peak_year_paid or 0):,.0f}) | ${(r.last_year_paid or 0):,.0f} |")
        else:
            L.append("| rank | npi | name | st | numerator $ | share of all | reflex (num/den lines) | per patient-month | top-tier share | first | last | peak year | last year $ |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for r in cand.itertuples():
                rr = "no screens" if pd.isna(r.reflex_ratio) else f"{r.reflex_ratio:.1f}"
                L.append(f"| {r.rank} | {r.npi} | {str(r.name)[:48]} | {r.state or ''} | ${r.paid_num:,.0f} | "
                         f"{(r.num_share or 0) * 100:.0f}% | {rr} | {0 if pd.isna(r.intensity) else r.intensity:.1f} | "
                         f"{0 if pd.isna(r.flag_share) else r.flag_share * 100:.0f}% | {r.first_month} | {r.last_month} | "
                         f"{r.peak_year} (${(r.peak_year_paid or 0):,.0f}) | ${(r.last_year_paid or 0):,.0f} |")
        L.append("")
        for st in s.report_statuses:
            sub = df[df["status"].eq(st)].sort_values("paid_num", ascending=False).head(15)
            if not len(sub):
                continue
            why = sub["status_reason"].iloc[0]
            L.append(f"### {st} ({int(df['status'].eq(st).sum())} rows; listed, not ranked): {why}")
            L.append("")
            L.append("| npi | name | st | taxonomy | numerator $ | per patient-month | peak lines/day (month) | capacity | occupancy | billers | first | last |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
            for r in sub.itertuples():
                cap = "" if pd.isna(r.licensed_capacity) else f"{float(r.licensed_capacity):,.0f}"
                occ = "" if pd.isna(r.occupancy_ratio) else f"{float(r.occupancy_ratio):.2f}"
                L.append(f"| {r.npi} | {str(r.name)[:40]} | {r.state or ''} | {str(r.taxonomy)[:10]} | ${r.paid_num:,.0f} | "
                         f"{0 if pd.isna(r.intensity) else r.intensity:.1f} | "
                         f"{0 if pd.isna(r.peak_lines_per_day) else r.peak_lines_per_day:.1f} ({r.peak_day_month}) | {cap} | {occ} | "
                         f"{int(r.n_billers or 0)} | {r.first_month} | {r.last_month} |")
            L.append("")
        aside = df[df["status"].str.startswith("verdict") | df["status"].isin(["national", "hospital"])]
        aside = aside[aside["paid_num"] >= s.min_paid_num].head(25)
        if len(aside):
            L.append("### Set aside above the floor (verdict, national name, hospital number)")
            L.append("")
            L.append("| npi | name | st | numerator $ | status |")
            L.append("|---|---|---|---|---|")
            for r in aside.itertuples():
                L.append(f"| {r.npi} | {str(r.name)[:48]} | {r.state or ''} | ${r.paid_num:,.0f} | {r.status} |")
            L.append("")
    return "\n".join(L) + "\n"


def agencies_for(con, spending: Path, screen: Screen, servicing_npis: list[str], provider_dim: Path | None) -> pd.DataFrame:
    """For a servicing-grain screen: every billing NPI that carried the given individuals on the
    numerator codes, with dollars and the biller's NPPES name (blank when the biller is not in the
    file), ordered by dollars. The court-record check runs on these names."""
    con.execute("CREATE OR REPLACE TEMP TABLE sel(npi VARCHAR)")
    con.executemany("INSERT INTO sel VALUES (?)", [(n,) for n in servicing_npis])
    code = "UPPER(TRIM(CAST(hcpcs_code AS VARCHAR)))"
    ag = con.execute(f"""
        SELECT CAST(servicing_npi AS VARCHAR) AS servicing_npi, CAST(billing_npi AS VARCHAR) AS biller_npi,
               SUM(CAST(total_paid AS DOUBLE)) AS paid, MIN(substr(CAST(service_month AS VARCHAR), 1, 7)) AS first_month,
               MAX(substr(CAST(service_month AS VARCHAR), 1, 7)) AS last_month
        FROM read_parquet('{_sql_str(spending)}')
        WHERE CAST(servicing_npi AS VARCHAR) IN (SELECT npi FROM sel) AND {_num_pred(code, screen)}
        GROUP BY 1, 2 ORDER BY paid DESC""").df()
    ag["biller_name"] = ""
    ag["biller_state"] = ""
    if provider_dim is not None and Path(provider_dim).exists() and len(ag):
        pc = _cols(con, Path(provider_dim))
        name = _name_sql(pc)
        st = "CAST(addr_state AS VARCHAR)" if "addr_state" in pc else "NULL"
        dim = con.execute(f"""SELECT CAST(npi AS VARCHAR) AS biller_npi, TRIM({name}) AS biller_name, {st} AS biller_state
                               FROM read_parquet('{_sql_str(provider_dim)}')""").df()
        ag = ag.drop(columns=["biller_name", "biller_state"]).merge(dim, on="biller_npi", how="left")
        ag["biller_name"] = ag["biller_name"].fillna("").astype(str).str.strip()
        ag["biller_state"] = ag["biller_state"].fillna("")
    return ag


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    root = Path(os.environ.get("MEDICAID_DATA_ROOT", str(Path.home() / "Desktop/data")))
    ap.add_argument("--spending", default=str(root / "processed" / "spending_fact.parquet"))
    ap.add_argument("--provider-dim", default=str(root / "processed" / "provider_dim.parquet"))
    ap.add_argument("--verdicts", default=None, help=f"verdicts file (default <data_root>/model_a/pursuit_verdicts.csv)")
    ap.add_argument("--capacity", default=str(root / "processed" / "licensed_capacity_by_npi.parquet"),
                    help="licensed capacity by NPI (ingest_cms.licensed_capacity) for the occupancy screens")
    ap.add_argument("--screen", default=None, help="screen key(s), comma-separated (default: every screen); one report per run")
    ap.add_argument("--policy", default=str(root / "processed" / "policy" / "state_policy.parquet"),
                    help="the state policy library (src/policy/registry; the policy lane writes it); '' = none")
    ap.add_argument("--dme-referrers", default=None,
                    help="supplier x ordering-physician table (parquet or csv: supplier_npi, referrer_npi, dollars|lines; "
                         "the DMEPOS referring file's Rfrg_NPI / Tot_Suplr_Srvcs names are accepted) for the national-share "
                         "screen's out-of-state order share; without it the ratio is not computed")
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--out", required=True, help="output folder")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    keys = [k.strip() for k in a.screen.split(",") if k.strip()] if a.screen else list(SCREENS)
    for k in keys:
        if k not in SCREENS:
            raise SystemExit(f"unknown screen {k}; choose from {list(SCREENS)}")
    verdicts = load_verdicts(Path(a.verdicts) if a.verdicts else None)
    policy_df = None
    if a.policy and Path(a.policy).exists():
        from src.policy.registry import load_policy
        policy_df = load_policy(path=Path(a.policy))
        print(f"[theory_screens] policy library: {len(policy_df)} rows from {a.policy}")
    con = _duck_connect()
    results = {}
    for k in keys:
        df = run_screen(Path(a.spending), SCREENS[k], con=con, provider_dim=Path(a.provider_dim),
                        verdicts=verdicts, top=a.top, capacity=Path(a.capacity) if a.capacity else None,
                        policy_df=policy_df, referrers=Path(a.dme_referrers) if a.dme_referrers else None)
        df.to_csv(out / f"screen_{k}.csv", index=False)
        # the top candidates' names, one per line, for `first_to_file --names-file` (the free court-record
        # check that runs on every candidate before a research prompt)
        cand = df[df["status"].eq("candidate")].sort_values("rank").head(a.top)
        names = [n for n in cand["name"].astype(str).str.strip().tolist() if n]
        (out / f"names_{k}.txt").write_text("\n".join(dict.fromkeys(names)) + ("\n" if names else ""), encoding="utf-8")
        if SCREENS[k].grain == "servicing" and len(cand):
            # the agencies that billed under the top aides: the court-record check belongs on them, not
            # on a person's name (the 2026-10-29 attendant run searched twenty aides and read collisions)
            ag = agencies_for(con, Path(a.spending), SCREENS[k], cand["npi"].tolist(), Path(a.provider_dim))
            ag.to_csv(out / f"agencies_{k}.csv", index=False)
            anames = [n for n in ag["biller_name"].astype(str).str.strip().tolist() if n]
            (out / f"agencies_{k}.txt").write_text("\n".join(dict.fromkeys(anames)) + ("\n" if anames else ""), encoding="utf-8")
        results[k] = df
        print(f"[theory_screens] {k}: {len(df)} NPIs on the numerator; "
              f"{int(df['status'].eq('candidate').sum())} candidates; "
              + ", ".join(f"{s} {n}" for s, n in sorted(df['status'].value_counts().items())))
    (out / "THEORY_SCREENS.md").write_text(to_markdown(results, top=a.top, policy_df=policy_df), encoding="utf-8")
    print(f"[theory_screens] -> {out / 'THEORY_SCREENS.md'}")


if __name__ == "__main__":
    main()
