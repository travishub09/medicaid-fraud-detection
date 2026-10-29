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
        numerator=("87798", "87505", "87506", "87507", "87801", "87640", "87641", "87653", "87481",
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
}


def _codes_table(con, name: str, codes) -> None:
    con.execute(f"CREATE OR REPLACE TEMP TABLE {name}(hcpcs VARCHAR)")
    con.executemany(f"INSERT INTO {name} VALUES (?)", [(c.upper(),) for c in codes])


def run_screen(spending: Path, screen: Screen, con=None, provider_dim: Path | None = None,
               verdicts: pd.DataFrame | None = None, top: int = 50) -> pd.DataFrame:
    """Every billing NPI on the screen's numerator with the ratios, the dollars by year,
    the status (candidate / national / hospital / verdict:<v> / below_floor / fails_<rule>)
    and the rank among candidates. DuckDB end to end; the fact never enters pandas."""
    con = con or _duck_connect()
    p = _sql_str(spending)
    cols = _cols(con, Path(spending))
    lines = "CAST(total_claim_lines AS DOUBLE)" if "total_claim_lines" in cols else "NULL"
    pats = "CAST(total_patients AS DOUBLE)" if "total_patients" in cols else "NULL"
    _codes_table(con, "num_codes", screen.numerator)
    _codes_table(con, "den_codes", screen.denominator)
    _codes_table(con, "flag_codes", screen.flag_codes or ("__none__",))
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE tot AS
        SELECT CAST(billing_npi AS VARCHAR) npi, SUM(CAST(total_paid AS DOUBLE)) paid_all,
               MAX(substr(CAST(service_month AS VARCHAR), 1, 7)) last_month_all
        FROM read_parquet('{p}') GROUP BY 1""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE num AS
        SELECT CAST(billing_npi AS VARCHAR) npi,
               SUM(CAST(total_paid AS DOUBLE)) paid_num,
               SUM({lines}) lines_num, SUM({pats}) pm_num,
               SUM(CASE WHEN UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) IN (SELECT hcpcs FROM flag_codes)
                        THEN {lines} ELSE 0 END) lines_flag,
               COUNT(DISTINCT substr(CAST(service_month AS VARCHAR), 1, 7)) months_num,
               MIN(substr(CAST(service_month AS VARCHAR), 1, 7)) first_month,
               MAX(substr(CAST(service_month AS VARCHAR), 1, 7)) last_month
        FROM read_parquet('{p}')
        WHERE UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) IN (SELECT hcpcs FROM num_codes)
        GROUP BY 1""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE den AS
        SELECT CAST(billing_npi AS VARCHAR) npi, SUM(CAST(total_paid AS DOUBLE)) paid_den,
               SUM({lines}) lines_den, SUM({pats}) pm_den
        FROM read_parquet('{p}')
        WHERE UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) IN (SELECT hcpcs FROM den_codes)
        GROUP BY 1""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE byyear AS
        SELECT CAST(billing_npi AS VARCHAR) npi, substr(CAST(service_month AS VARCHAR), 1, 4) yr,
               SUM(CAST(total_paid AS DOUBLE)) paid
        FROM read_parquet('{p}')
        WHERE UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) IN (SELECT hcpcs FROM num_codes)
        GROUP BY 1, 2""")
    df = con.execute("""
        SELECT n.npi, n.paid_num, t.paid_all,
               CASE WHEN t.paid_all > 0 THEN n.paid_num / t.paid_all END AS num_share,
               n.lines_num, COALESCE(d.lines_den, 0) AS lines_den,
               CASE WHEN COALESCE(d.lines_den, 0) > 0 THEN n.lines_num / d.lines_den END AS reflex_ratio,
               CASE WHEN n.pm_num > 0 THEN n.lines_num / n.pm_num END AS intensity,
               CASE WHEN n.lines_num > 0 THEN n.lines_flag / n.lines_num END AS flag_share,
               COALESCE(d.paid_den, 0) AS paid_den, n.months_num, n.first_month, n.last_month,
               (SELECT yr FROM byyear b WHERE b.npi = n.npi ORDER BY paid DESC, yr DESC LIMIT 1) AS peak_year,
               (SELECT MAX(paid) FROM byyear b WHERE b.npi = n.npi) AS peak_year_paid,
               (SELECT paid FROM byyear b WHERE b.npi = n.npi
                  AND b.yr = (SELECT MAX(yr) FROM byyear c WHERE c.npi = n.npi)) AS last_year_paid,
               (SELECT MAX(yr) FROM byyear b WHERE b.npi = n.npi) AS last_year
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
    return classify(df, screen, verdicts, top=top)


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
        hit = v_by_name.get(nm) or next((v for k, v in v_by_name.items() if k and nm and (k in nm or nm in k)), None)
        if hit:
            status.append(f"verdict:{hit}"); reason.append("standing verdict")
        elif nm and is_national_name(nm):
            status.append("national"); reason.append("national name")
        elif tx[:2] in ("27", "28"):
            status.append("hospital"); reason.append("hospital-licensed number")
        elif screen.taxonomy_prefixes and tx and not any(tx.startswith(p) for p in screen.taxonomy_prefixes):
            status.append("other_type"); reason.append(f"taxonomy {tx} outside the screen's provider type")
        elif float(r.paid_num or 0) < screen.min_paid_num:
            status.append("below_floor"); reason.append(f"numerator dollars under {screen.min_paid_num:,.0f}")
        else:
            ratio = r.reflex_ratio
            inten = r.intensity
            no_den = float(r.lines_den or 0) == 0 and float(r.lines_num or 0) > 0
            reflex_ok = no_den or (ratio is not None and not pd.isna(ratio) and float(ratio) >= screen.min_reflex)
            inten_ok = inten is not None and not pd.isna(inten) and float(inten) >= screen.min_intensity
            if reflex_ok and inten_ok:
                status.append("candidate"); reason.append("passes reflex and intensity")
            elif not reflex_ok:
                status.append("fails_reflex"); reason.append("more screens than definitive lines")
            else:
                status.append("fails_intensity"); reason.append(f"under {screen.min_intensity} per patient-month")
    df = df.copy()
    df["status"] = status
    df["status_reason"] = reason
    df["screen"] = screen.key
    cand = df["status"].eq("candidate")
    df["rank"] = pd.NA
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
              f"**Codes.** numerator {', '.join(s.numerator)}; denominator {', '.join(s.denominator)}"
              + (f"; top-tier code {', '.join(s.flag_codes)}" if s.flag_codes else "") + ".", "",
              f"**Rules.** numerator dollars >= ${s.min_paid_num:,.0f}; numerator lines / denominator lines >= "
              f"{s.min_reflex} (or no denominator at all); numerator lines per patient-month >= {s.min_intensity}.", "",
              "**Innocent explanations to rule out first.** " + " ".join(f"({i + 1}) {t}." for i, t in enumerate(s.innocent)), "",
              "**Who would know.** " + "; ".join(s.roles) + ".", "",
              f"**Precedent.** {s.precedent}", ""]
        tally = df["status"].value_counts().to_dict()
        L.append("Status tally: " + ", ".join(f"{k} {v}" for k, v in sorted(tally.items())) + ".")
        L.append("")
        cand = df[df["status"].eq("candidate")].head(top)
        L.append(f"### Candidates (top {min(top, len(cand))} of {int(df['status'].eq('candidate').sum())}, by numerator dollars)")
        L.append("")
        L.append("| rank | npi | name | st | numerator $ | share of all | reflex (num/den lines) | per patient-month | top-tier share | first | last | peak year | last year $ |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in cand.itertuples():
            rr = "no screens" if pd.isna(r.reflex_ratio) else f"{r.reflex_ratio:.1f}"
            L.append(f"| {r.rank} | {r.npi} | {str(r.name)[:48]} | {r.state or ''} | ${r.paid_num:,.0f} | "
                     f"{(r.num_share or 0) * 100:.0f}% | {rr} | {0 if pd.isna(r.intensity) else r.intensity:.1f} | "
                     f"{0 if pd.isna(r.flag_share) else r.flag_share * 100:.0f}% | {r.first_month} | {r.last_month} | "
                     f"{r.peak_year} (${(r.peak_year_paid or 0):,.0f}) | ${(r.last_year_paid or 0):,.0f} |")
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    root = Path(os.environ.get("MEDICAID_DATA_ROOT", str(Path.home() / "Desktop/data")))
    ap.add_argument("--spending", default=str(root / "processed" / "spending_fact.parquet"))
    ap.add_argument("--provider-dim", default=str(root / "processed" / "provider_dim.parquet"))
    ap.add_argument("--verdicts", default=None, help=f"verdicts file (default <data_root>/model_a/pursuit_verdicts.csv)")
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
                        verdicts=verdicts, top=a.top)
        df.to_csv(out / f"screen_{k}.csv", index=False)
        results[k] = df
        print(f"[theory_screens] {k}: {len(df)} NPIs on the numerator; "
              f"{int(df['status'].eq('candidate').sum())} candidates; "
              + ", ".join(f"{s} {n}" for s, n in sorted(df['status'].value_counts().items())))
    (out / "THEORY_SCREENS.md").write_text(to_markdown(results, top=a.top), encoding="utf-8")
    print(f"[theory_screens] -> {out / 'THEORY_SCREENS.md'}")


if __name__ == "__main__":
    main()
