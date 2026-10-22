"""
sector_schemes.py — Medicaid-fact sector schemes (Run 2 §I3/I4/I5).

Three schemes that read NEW signal out of the spending fact already in hand —
no procurement. All DuckDB-streamed (the 238M-row fact never enters pandas).

  NEMT (I4)               transport HCPCS (A0xxx ambulance/mileage, T2001–T2005,
                          T2049, S0209/S0215). Phantom trips and mileage
                          inflation are Medicaid-specific fraud; two of Run 1's
                          own billed-after-ban smoking guns were transport
                          companies. → nemt_paid_share, nemt_lines_per_patient
  Behavioral health (I5)  H-code services + psychotherapy (90791–90853) + ABA
                          (97151–97158). Patient brokering / therapy-never-
                          rendered / ABA unit mills — the 500-hours-a-day DOJ
                          conviction was exactly this sector.
                          → bh_paid_share, bh_lines_per_patient
  Impossible day (I3)     OPTIONAL hcpcs→minutes map (preclean/hcpcs_time/
                          hcpcs_minutes.csv: hcpcs,minutes — time-based codes
                          define their own duration). Implied minutes per
                          working day in the provider's BUSIEST month →
                          time_minutes_per_day, which lights up the dormant
                          ``impossible_day`` scheme. Skip-missing without the
                          map — absent, never zero.

Shares are of the provider's OWN paid total (a 100%-transport biller is a
transport company — the peer percentile downstream is what makes it fair);
lines-per-patient on the sector codes is the padding signal. One-sided as
always: only excess vs peers is suspicious.

  Laboratory testing (v21, 2026-10-22)
                          THE v19 LABEL_ENRICHMENT LESSON: the forward FCA cases
                          concentrate in clinical laboratories (taxonomy 291U, lift
                          26) and on one family of codes no scheme named —
                          definitive drug testing G0480-G0483 (lift 19-32),
                          genetic panels 81420 / 81406 / 81220 (52-85), bilirubin
                          82247 / 82248 (95-120), HbA1c 83036 as a dominant code
                          (229), respiratory / infectious panels 87804 / U0003 /
                          87536 / 86480, venipuncture 36416. The typology is the
                          one DOJ settles every year: medically unnecessary
                          definitive toxicology, genetic-testing mills, respiratory
                          panels bundled onto COVID swabs, standing-order panels.
                          → lab_paid_share, lab_lines_per_patient,
                            lab_definitive_drug_share, lab_genetic_share,
                            lab_resp_panel_share (the last three are shares OF the
                            lab dollars: the mix, not the size)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

WORKING_DAYS_PER_MONTH = 22

_NEMT_PRED = ("(hcpcs LIKE 'A0%' OR hcpcs IN ('T2001','T2002','T2003','T2004',"
              "'T2005','T2049','S0209','S0215'))")
_BH_PRED = ("(hcpcs LIKE 'H0%' OR hcpcs LIKE 'H2%' "
            "OR (hcpcs >= '90791' AND hcpcs <= '90853') "
            "OR (hcpcs >= '97151' AND hcpcs <= '97158'))")

# --- laboratory testing families (v21) -------------------------------------
# definitive drug testing (the toxicology-mill code family) + presumptive screens
_LAB_DRUG_DEF = ("(hcpcs IN ('G0480','G0481','G0482','G0483','G0659'))")
_LAB_DRUG_PRES = ("(hcpcs IN ('80305','80306','80307'))")
# molecular / genetic: the 811xx-814xx range + the PLA codes (0xxxU)
_LAB_GENETIC = ("((hcpcs >= '81105' AND hcpcs <= '81479') OR hcpcs LIKE '0___U')")
# respiratory / infectious panels and rapid tests (the COVID-era bundling shape)
_LAB_RESP = ("(hcpcs IN ('87426','87428','87631','87632','87633','87634','87635','87636',"
             "'87637','87804','87807','87811','87880','86328','86769','86480','87389',"
             "'87536','87210','U0001','U0002','U0003','U0004','U0005'))")
# chemistry / hematology panels, the enriched singles, and venipuncture
_LAB_PANEL = ("((hcpcs >= '80047' AND hcpcs <= '80076') OR hcpcs IN ('80081','82247','82248',"
              "'83036','85025','85027','85610','84702','86803','36415','36416'))")
_LAB_PRED = f"({_LAB_DRUG_DEF} OR {_LAB_DRUG_PRES} OR {_LAB_GENETIC} OR {_LAB_RESP} OR {_LAB_PANEL})"

LAB_METRICS = ("lab_paid_share", "lab_lines_per_patient", "lab_definitive_drug_share",
               "lab_genetic_share", "lab_resp_panel_share")


def lab_code_set() -> frozenset[str]:
    """The explicit HCPCS set behind _LAB_PRED (for scheme_code_families' dollars-at-
    issue scoping; the PLA 0xxxU pattern is expanded as a prefix rule there)."""
    codes = {"G0480", "G0481", "G0482", "G0483", "G0659", "80305", "80306", "80307",
             "87426", "87428", "87631", "87632", "87633", "87634", "87635", "87636", "87637",
             "87804", "87807", "87811", "87880", "86328", "86769", "86480", "87389", "87536",
             "87210", "U0001", "U0002", "U0003", "U0004", "U0005", "80081", "82247", "82248",
             "83036", "85025", "85027", "85610", "84702", "86803", "36415", "36416"}
    codes |= {str(c) for c in range(81105, 81480)}
    codes |= {str(c) for c in range(80047, 80077)}
    return frozenset(codes)


def sector_metrics_from_parquet(spending_path: str | Path,
                                minutes_csv: str | Path | None = None
                                ) -> pd.DataFrame:
    """Per-NPI sector metrics straight from the spending parquet."""
    from src.attempt_2.duck import connect as _duck
    con = _duck()
    p = str(spending_path).replace("'", "''")
    base = f"""
        SELECT CAST(billing_npi AS VARCHAR) AS npi,
               UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) AS hcpcs,
               CAST(service_month AS VARCHAR) AS month,
               SUM(CAST(total_paid AS DOUBLE)) AS paid,
               SUM(CAST(total_claim_lines AS DOUBLE)) AS lines,
               SUM(CAST(total_patients AS DOUBLE)) AS patients
        FROM read_parquet('{p}')
        GROUP BY 1, 2, 3
    """
    df = con.execute(f"""
        WITH b AS ({base})
        SELECT npi,
               SUM(paid) AS total_paid,
               SUM(CASE WHEN {_NEMT_PRED} THEN paid ELSE 0 END)     AS nemt_paid,
               SUM(CASE WHEN {_NEMT_PRED} THEN lines ELSE 0 END)    AS nemt_lines,
               SUM(CASE WHEN {_NEMT_PRED} THEN patients ELSE 0 END) AS nemt_patients,
               SUM(CASE WHEN {_BH_PRED} THEN paid ELSE 0 END)       AS bh_paid,
               SUM(CASE WHEN {_BH_PRED} THEN lines ELSE 0 END)      AS bh_lines,
               SUM(CASE WHEN {_BH_PRED} THEN patients ELSE 0 END)   AS bh_patients,
               SUM(CASE WHEN {_LAB_PRED} THEN paid ELSE 0 END)      AS lab_paid,
               SUM(CASE WHEN {_LAB_PRED} THEN lines ELSE 0 END)     AS lab_lines,
               SUM(CASE WHEN {_LAB_PRED} THEN patients ELSE 0 END)  AS lab_patients,
               SUM(CASE WHEN {_LAB_DRUG_DEF} THEN paid ELSE 0 END)  AS lab_def_paid,
               SUM(CASE WHEN {_LAB_GENETIC} THEN paid ELSE 0 END)   AS lab_gen_paid,
               SUM(CASE WHEN {_LAB_RESP} THEN paid ELSE 0 END)      AS lab_resp_paid
        FROM b GROUP BY npi
    """).df()
    out = pd.DataFrame({"npi": df["npi"].astype(str)})
    out["nemt_paid_share"] = (df["nemt_paid"] / df["total_paid"]).where(
        (df["total_paid"] > 0) & (df["nemt_paid"] > 0))
    out["nemt_lines_per_patient"] = (df["nemt_lines"] / df["nemt_patients"]).where(
        df["nemt_patients"] > 0)
    out["bh_paid_share"] = (df["bh_paid"] / df["total_paid"]).where(
        (df["total_paid"] > 0) & (df["bh_paid"] > 0))
    out["bh_lines_per_patient"] = (df["bh_lines"] / df["bh_patients"]).where(
        df["bh_patients"] > 0)
    # laboratory testing (v21): the size of the lab book and its MIX. Shares of the
    # lab dollars are NULL (absent, never zero) for a provider with no lab dollars; a
    # provider with lab dollars and none of a sub-family reads 0 on that share (a
    # real observation: a lab that bills no definitive toxicology).
    has_lab = (df["total_paid"] > 0) & (df["lab_paid"] > 0)
    out["lab_paid_share"] = (df["lab_paid"] / df["total_paid"]).where(has_lab)
    out["lab_lines_per_patient"] = (df["lab_lines"] / df["lab_patients"]).where(
        has_lab & (df["lab_patients"] > 0))
    out["lab_definitive_drug_share"] = (df["lab_def_paid"] / df["lab_paid"]).where(has_lab)
    out["lab_genetic_share"] = (df["lab_gen_paid"] / df["lab_paid"]).where(has_lab)
    out["lab_resp_panel_share"] = (df["lab_resp_paid"] / df["lab_paid"]).where(has_lab)

    # impossible-day approximation: needs the public code→minutes map
    if minutes_csv and Path(minutes_csv).exists():
        mp = str(minutes_csv).replace("'", "''")
        md = con.execute(f"""
            WITH b AS ({base}),
            tm AS (SELECT UPPER(TRIM(CAST(hcpcs AS VARCHAR))) AS hcpcs,
                          CAST(minutes AS DOUBLE) AS minutes
                   FROM read_csv_auto('{mp}', header=true)),
            per_month AS (
                SELECT b.npi, b.month, SUM(b.lines * tm.minutes) AS mins
                FROM b JOIN tm ON b.hcpcs = tm.hcpcs
                GROUP BY b.npi, b.month)
            SELECT npi, MAX(mins) / {WORKING_DAYS_PER_MONTH} AS time_minutes_per_day
            FROM per_month GROUP BY npi
        """).df()
        md["npi"] = md["npi"].astype(str)
        out = out.merge(md, on="npi", how="left")
        assert len(out) == len(df), "minutes-map join fanned out"
    con.close()
    # rows with NO sector presence at all are dropped (absent from these
    # sources, never zero-scored — the NULL convention)
    metric_cols = [c for c in out.columns if c != "npi"]
    return out[out[metric_cols].notna().any(axis=1)].reset_index(drop=True)
