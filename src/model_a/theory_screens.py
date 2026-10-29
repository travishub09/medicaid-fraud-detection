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
    report_statuses: tuple[str, ...] = ()      # statuses the report tables beside the candidates (the shapes worth a look)


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
}


def _codes_table(con, name: str, codes) -> None:
    con.execute(f"CREATE OR REPLACE TEMP TABLE {name}(hcpcs VARCHAR)")
    con.executemany(f"INSERT INTO {name} VALUES (?)", [(c.upper(),) for c in codes])


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


def run_screen(spending: Path, screen: Screen, con=None, provider_dim: Path | None = None,
               verdicts: pd.DataFrame | None = None, top: int = 50, capacity: Path | None = None) -> pd.DataFrame:
    """Every NPI on the screen's numerator (the billing NPI, or the servicing NPI when the
    screen's grain is 'servicing') with the ratios, the dollars by year, the status
    (candidate / national / hospital / government / verdict:<v> / below_floor / fails_<rule>)
    and the rank among candidates. Per-day screens add the peak month's lines per calendar
    day (a per-diem's average daily census; an aide's units a day) and, with a capacity
    table (licensed_capacity_by_npi.parquet: npi, licensed_capacity), the occupancy ratio.
    DuckDB end to end; the fact never enters pandas."""
    con = con or _duck_connect()
    p = _sql_str(spending)
    cols = _cols(con, Path(spending))
    lines = "CAST(total_claim_lines AS DOUBLE)" if "total_claim_lines" in cols else "NULL"
    pats = "CAST(total_patients AS DOUBLE)" if "total_patients" in cols else "NULL"
    key = "servicing_npi" if screen.grain == "servicing" and "servicing_npi" in cols else "billing_npi"
    month = "substr(CAST(service_month AS VARCHAR), 1, 7)"
    _codes_table(con, "num_codes", screen.numerator)
    _codes_table(con, "den_codes", screen.denominator or ("__none__",))
    _codes_table(con, "flag_codes", screen.flag_codes or ("__none__",))
    code = "UPPER(TRIM(CAST(hcpcs_code AS VARCHAR)))"
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
        WHERE {key} IS NOT NULL AND {code} IN (SELECT hcpcs FROM num_codes)
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
        WHERE {key} IS NOT NULL AND {code} IN (SELECT hcpcs FROM num_codes)
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
            WHERE {key} IS NOT NULL AND {code} IN (SELECT hcpcs FROM num_codes)
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
        name = " || ' ' || ".join(f"COALESCE(CAST({c} AS VARCHAR), '')"
                                  for c in ("org_legal_name", "provider_name") if c in pc) or "''"
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
    df["capacity_known"] = bool(df["licensed_capacity"].notna().any())
    out = classify(df, screen, verdicts, top=top)
    return out.drop(columns=["capacity_known"])


_GOVERNMENT_RE = re.compile(
    r"^(STATE|CITY|COUNTY|TOWN|COMMONWEALTH|DEPARTMENT|DEPT|BOARD)\s+OF\b|\bPUBLIC HEALTH (LAB|DEPT|DEPARTMENT)\b"
    r"|\b(COUNTY|CITY|STATE|PARISH|TOWN) (OF|HEALTH|LABORATORY|LAB|COMMISSION|DEPARTMENT|DEPT|BOARD|HOSPITAL DISTRICT)\b"
    r"|\bDEPARTMENT OF (PUBLIC HEALTH|HEALTH|MENTAL HEALTH|HUMAN SERVICES|SOCIAL SERVICES|BEHAVIORAL HEALTH)\b"
    r"|\bBOARD OF (HEALTH|EDUCATION|DEVELOPMENTAL DISABILITIES|MENTAL HEALTH)\b"
    r"|\b(HEALTH|HOSPITAL|SCHOOL) DISTRICT\b|\bUNIFIED SCHOOL\b|\bPUBLIC SCHOOLS\b|\bVETERANS AFFAIRS\b"
)
_PLACEHOLDER_NPI_RE = re.compile(r"^\d?([1-9])\1{6,}\d*$")   # 1999999984, 1111111112: a filler number (a run of zeros is a fixture)


def is_government_name(name: str) -> bool:
    """A public-health laboratory (STATE OF / COUNTY OF / CITY OF ...): not an operator a relator
    comes from, so it leaves the candidate table under its own status (the first sweep's read,
    2026-10-29)."""
    nm = (name or "").upper().strip()
    return bool(nm) and bool(_GOVERNMENT_RE.search(nm))


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
        elif screen.entity_types and et and et not in screen.entity_types:
            status.append("other_type"); reason.append(f"entity type {et} outside the screen's provider type")
        elif float(r.paid_num or 0) < screen.min_paid_num:
            status.append("below_floor"); reason.append(f"numerator dollars under {screen.min_paid_num:,.0f}")
        elif screen.candidate_taxonomies and tx and not any(tx.startswith(p) for p in screen.candidate_taxonomies):
            status.append("not_aide"); reason.append(f"taxonomy {tx}: an individual's number at agency scale in the servicing field (whose number went on the claim)")
        elif screen.grain == "servicing" and float(getattr(r, "self_share", 0) or 0) >= 0.9:
            status.append("self_billed"); reason.append("the servicing field is the biller's own number (an agency enrolled under a person's number): the aide grain is absent for these claims")
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
    cand = df["status"].eq("candidate")
    df["rank"] = pd.NA
    if screen.capacity_rule and "occupancy_ratio" in df.columns and df["occupancy_ratio"].notna().any():
        # a count above the building first (the on-site fact), then dollars; rows without a capacity last
        order = df[cand].assign(_oc=df.loc[cand, "occupancy_ratio"].astype(float).fillna(-1.0)) \
                        .sort_values(["_oc", "paid_num"], ascending=[False, False]).index
        df.loc[order, "rank"] = range(1, len(order) + 1)
    else:
        df.loc[cand, "rank"] = range(1, int(cand.sum()) + 1)
    return df


def to_markdown(results: dict[str, pd.DataFrame], top: int = 50) -> str:
    L = ["# THEORY SCREENS: every billing NPI ranked on a prosecuted conduct", "",
         "_From the Medicaid spending fact. A row is a question for the field and for counsel: the "
         "innocent explanations under each screen are ruled out first, the court-record check runs on "
         "every candidate before a research prompt, and a standing verdict or a national name is set "
         "aside, never ranked. Leads for human review, never accusations._", ""]
    for key, df in results.items():
        s = SCREENS[key]
        L += [f"## {s.title} (`{key}`)", "", f"**Theory.** {s.theory}", "",
              f"**Codes.** numerator {', '.join(s.numerator)}; denominator "
              f"{', '.join(s.denominator) if s.denominator else 'none (no ordinary practice beside it)'}"
              + (f"; flagged code {', '.join(s.flag_codes)}" if s.flag_codes else "")
              + (f"; grain = the {s.grain} NPI" if s.grain != "billing" else "") + ".", "",
              f"**Rules.** numerator dollars >= ${s.min_paid_num:,.0f}"
              + (f"; numerator lines / denominator lines >= {s.min_reflex} (or no denominator at all)" if s.denominator else "")
              + (f"; numerator lines per patient-month >= {s.min_intensity}" if s.min_intensity > 0 else "")
              + (f"; peak lines per calendar day >= {s.min_per_day}" if s.min_per_day is not None else "")
              + ("; occupancy = peak lines a calendar day over licensed capacity where a capacity is known (over 1.0 "
                 "ranks first); open-day occ. = the same over weekdays (a five-day program); two-shift occ. = half of "
                 "that, the reading when the licence approves two sessions a day: a row above 1.0 on two shifts is over "
                 "the building on any lawful schedule"
                 if s.capacity_rule else "") + ".", "",
              "**Innocent explanations to rule out first.** " + " ".join(f"({i + 1}) {t}." for i, t in enumerate(s.innocent)), "",
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
        cand = df[df["status"].eq("candidate")].sort_values("rank").head(top)
        order_note = "over-capacity first, then numerator dollars" if s.capacity_rule else "by numerator dollars"
        L.append(f"### Candidates (top {min(top, len(cand))} of {int(df['status'].eq('candidate').sum())}, {order_note})")
        L.append("")
        if s.per_day:
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
        WHERE CAST(servicing_npi AS VARCHAR) IN (SELECT npi FROM sel) AND {code} IN (SELECT hcpcs FROM num_codes)
        GROUP BY 1, 2 ORDER BY paid DESC""").df()
    ag["biller_name"] = ""
    ag["biller_state"] = ""
    if provider_dim is not None and Path(provider_dim).exists() and len(ag):
        pc = _cols(con, Path(provider_dim))
        name = " || ' ' || ".join(f"COALESCE(CAST({c} AS VARCHAR), '')"
                                  for c in ("org_legal_name", "provider_name") if c in pc) or "''"
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
    ap.add_argument("--screen", default=None, help="one screen key (default: every screen)")
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--out", required=True, help="output folder")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    keys = [a.screen] if a.screen else list(SCREENS)
    for k in keys:
        if k not in SCREENS:
            raise SystemExit(f"unknown screen {k}; choose from {list(SCREENS)}")
    verdicts = load_verdicts(Path(a.verdicts) if a.verdicts else None)
    con = _duck_connect()
    results = {}
    for k in keys:
        df = run_screen(Path(a.spending), SCREENS[k], con=con, provider_dim=Path(a.provider_dim),
                        verdicts=verdicts, top=a.top, capacity=Path(a.capacity) if a.capacity else None)
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
    (out / "THEORY_SCREENS.md").write_text(to_markdown(results, top=a.top), encoding="utf-8")
    print(f"[theory_screens] -> {out / 'THEORY_SCREENS.md'}")


if __name__ == "__main__":
    main()
