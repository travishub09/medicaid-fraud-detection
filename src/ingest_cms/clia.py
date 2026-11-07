"""
clia.py — the CLIA gap: a laboratory's billed test lines against the annual volume it
reported to CMS on its CLIA certificate (2026-11-02).

Source: the CMS Provider of Services file for CLIA laboratories (data.cms.gov, "Provider
of Services File - Clinical Laboratories"; the quarterly POS "Other" / CLIA layout, a
CSV). Every laboratory that bills a federal program holds a CLIA certificate; the
application (CMS-116) reports the lab's ANNUAL TEST VOLUME per specialty and the
certificate TYPE says what the lab may perform: a Certificate of Waiver covers only
waived tests (a glucose meter, a dipstick, a rapid strep), a PPM certificate adds
provider-performed microscopy, and only a Certificate of Compliance or Accreditation
covers moderate and high complexity testing (definitive drug testing, molecular /
genetic panels, PCR).

The shape (the build's cause): a public-record lab whose certificate reports about 600
tests a year billed 105,977 genetic tests; two Chicago labs certified for 10 to 15k COVID
tests a year billed 90k and 157k. The file was never loaded.

  load_clia(path_or_folder)       the POS CLIA rows, identifiers as strings, Latin-1
                                  tolerant; RAISES when the CLIA number column is
                                  missing; a row without a CLIA number is quarantined
                                  (counted in attrs); prints the columns it resolved and
                                  which volume columns fed ``clia_annual_volume``.
  clia_to_npi(clia, provider_dim, other_ids=None)
                                  CLIA number -> NPI: (a) the NPPES Other Provider
                                  Identifier rows whose issuer mentions CLIA
                                  (ccn_npi_crosswalk --other-ids-out writes them as
                                  id_type 'clia'), then (b) an exact name-key +
                                  standardized-address match to provider_dim through
                                  the SHARED normalizers. Many certificates per NPI;
                                  ``match_basis`` = other_id | name_address.
  billed_lab_lines_from_parquet(spending, asof_cutoff=None)
                                  per NPI the lab-family lines per CALENDAR YEAR (the
                                  peak year) and the high-complexity lines, in DuckDB
                                  through src.attempt_2.duck.connect: the fact never
                                  enters pandas.
  clia_features(npi_clia, billed)  per NPI: clia_certificate_type (the highest-complexity
                                  certificate held), clia_waiver_only, clia_annual_volume,
                                  clia_billed_lab_lines_per_year, clia_volume_ratio
                                  (billed / reported, NULL without a reported volume,
                                  clipped at 1000) and clia_waiver_high_complexity (a
                                  waiver-only lab billing high-complexity codes). Every
                                  numeric feature is NULL when unknown, never 0.

The two scheme inputs (clia_volume_ratio, clia_waiver_high_complexity) feed
``lab_testing`` RAW (RAW_SUBSCORE_INPUTS: the ratio is clipped 0..1 by the engine, so
Medicaid lines alone at or above the certificate's whole annual volume is the maximum
evidence; a waiver-only lab billing G0483 reads 1). v30 semantics: a new
pre-registration. Dormant until preclean/clia/ holds the file.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from src.attempt_2.clean_data import (_normalize_name, _resolve_columns, _standardize_address,
                                      read_csv_text)

# The exact CMS names vary by edition (the POS_OTHER layout is upper-case with CMS
# abbreviations; the data.cms.gov "Clinical Laboratories" release uses the same names;
# older/other exports spell them out). First candidate present wins; the loader prints
# what it resolved so the operator can check the real header against this map.
CLIA_COLS = {
    "clia_number": ["PRVDR_NUM", "prvdr_num", "CLIA_ID", "clia_id", "CLIA_NUM", "CLIA Number",
                    "clia_number", "CLIA ID", "Provider Number", "provider_number"],
    "facility_name": ["FAC_NAME", "fac_name", "Facility Name", "facility_name", "LAB_NAME",
                      "Provider Name", "provider_name", "NAME"],
    "addr_line1": ["ST_ADR", "st_adr", "Street Address", "street_address", "ADDRESS",
                   "address_line_1", "addr_line1", "Address Line 1"],
    "addr_city": ["CITY_NAME", "city_name", "City", "city", "CITY"],
    "addr_state": ["STATE_CD", "state_cd", "State", "state", "STATE", "State Code"],
    "addr_zip": ["ZIP_CD", "zip_cd", "Zip Code", "zip_code", "ZIP", "zip"],
    "cert_type": ["CRTFCT_TYPE_CD", "crtfct_type_cd", "Certificate Type Code",
                  "certificate_type_code", "CERTIFICATE_TYPE", "cert_type"],
    "cert_effective_date": ["CRTFCT_EFCTV_DT", "crtfct_efctv_dt", "Certificate Effective Date",
                            "certificate_effective_date", "CERT_EFFECTIVE_DATE"],
    "cert_expiration_date": ["CRTFCT_EXPRTN_DT", "crtfct_exprtn_dt", "Certificate Expiration Date",
                             "certificate_expiration_date", "CERT_EXPIRATION_DATE"],
    "termination_code": ["PGM_TRMNTN_CD", "pgm_trmntn_cd", "Program Termination Code",
                         "termination_code"],
    "termination_date": ["TRMNTN_EXPRTN_DT", "trmntn_exprtn_dt", "Termination Expiration Date",
                         "termination_date"],
    # 2026-11-07: when the lab FIRST entered the program. The as-of filter reads this, not the
    # current certificate's effective date (certificates renew every two years, so on a 2026
    # edition almost every open lab carries a 2024-2026 effective date)
    "orig_participation_date": ["ORGNL_PRTCPTN_DT", "orgnl_prtcptn_dt", "Original Participation Date",
                                "original_participation_date"],
    # 2026-11-05: the first real file (pos_clia_202609.csv, Q3 2026, 686,058 labs, 103 columns)
    # names these CURRENT_CLIA_LAB_CLSFCTN_CD (plus CLIA_LAB_CLASSIFICATION_CD_1..10, the history),
    # ACRDTN_SCHDL_CD, FORM_116_TEST_VOL_CNT / FORM_116_ACRDTD_TEST_VOL_CNT (the CMS-116 volumes),
    # FORM_1557_TEST_VOL_CNT (the survey form's volume, kept apart) and CLIA_MDCR_NUM
    "lab_classification": ["CURRENT_CLIA_LAB_CLSFCTN_CD", "CLIA_LAB_CLSFCTN_CD", "clia_lab_clsfctn_cd",
                           "Lab Classification Code", "lab_classification_code", "lab_classification"],
    "general_control": ["GNRL_CNTL_TYPE_CD", "gnrl_cntl_type_cd", "General Control Type Code"],
    "accreditation_org": ["ACRDTN_SCHDL_CD", "ACRDTN_TYPE_CD", "acrdtn_type_cd", "Accreditation Type Code"],
    "medicare_number": ["CLIA_MDCR_NUM", "clia_mdcr_num"],
    "chow_date": ["CHOW_DT", "chow_dt"],
    "lab_site_count": ["LAB_SITE_CNT", "lab_site_cnt"],
    # a stated TOTAL annual volume, where the edition carries one
    "annual_volume_total": ["TOT_ANNUAL_TEST_VOL_CNT", "TOTAL_ANNUAL_TEST_VOLUME",
                            "ANNUAL_TEST_VOL_CNT", "ANNUAL_TEST_VOLUME", "annual_test_volume",
                            "Total Annual Test Volume", "TEST_VOL_CNT"],
    # the CMS-116 volumes by certificate / specialty class (summed when no total exists)
    "annual_volume_accredited": ["FORM_116_ACRDTD_TEST_VOL_CNT", "ACRDTD_TEST_VOL_CNT",
                                 "acrdtd_test_vol_cnt", "Accredited Test Volume Count"],
    "annual_volume_nonaccredited": ["FORM_116_TEST_VOL_CNT", "NON_ACRDTD_TEST_VOL_CNT",
                                    "non_acrdtd_test_vol_cnt", "Non-Accredited Test Volume Count"],
    # the CMS-1557 (survey report) volume: an operational column, NOT summed into
    # clia_annual_volume (it restates a 116 volume for surveyed labs; summing would double count)
    "annual_volume_1557": ["FORM_1557_TEST_VOL_CNT", "form_1557_test_vol_cnt"],
    "annual_volume_ppm": ["PPMP_TEST_VOL_CNT", "ppmp_test_vol_cnt", "PPM_TEST_VOL_CNT",
                          "PPM Test Volume Count"],
    "annual_volume_waived": ["WVD_TEST_VOL_CNT", "wvd_test_vol_cnt", "WAIVED_TEST_VOL_CNT",
                             "Waived Test Volume Count"],
}
REQUIRED = ("clia_number",)
VOLUME_KEYS = ("annual_volume_accredited", "annual_volume_nonaccredited",
               "annual_volume_ppm", "annual_volume_waived")
# generic fallback: any unresolved column whose name reads as a test-volume count
# (the per-specialty / subspecialty volume columns of the CMS-116 block)
_VOLUME_FALLBACK_RE = re.compile(r"(test[_ ]*vol|annual[_ ]*(test[_ ]*)?vol|vol(ume)?[_ ]*(cnt|count))",
                                 re.I)
_VOLUME_EXCLUDE_RE = re.compile(r"(_dt$|date|_cd$|code|flag|ind$|indic)", re.I)

# CRTFCT_TYPE_CD: 1 compliance, 2 waiver, 3 accreditation, 4 PPM, 9 registration
CERT_TYPES = {"1": "compliance", "2": "waiver", "3": "accreditation", "4": "ppm",
              "9": "registration"}
# the complexity ladder: compliance / accreditation cover high complexity; a registration
# certificate is the pending form of one of them; PPM adds microscopy to a waiver; a waiver
# covers waived tests only
CERT_RANK = {"compliance": 4, "accreditation": 4, "registration": 3, "ppm": 2, "waiver": 1}
HIGH_COMPLEXITY_TYPES = {"compliance", "accreditation"}

# the high-complexity code families a Certificate of Waiver never covers: definitive drug
# testing, molecular / genetic (811xx-814xx + PLA), infectious-agent nucleic acid (PCR)
HIGH_COMPLEXITY_PRED = ("(hcpcs IN ('G0480','G0481','G0482','G0483','G0659')"
                        " OR (hcpcs >= '81105' AND hcpcs <= '81479') OR hcpcs LIKE '0___U'"
                        " OR (hcpcs >= '87471' AND hcpcs <= '87801'))")

RATIO_CLIP = 1000.0

FEATURE_COLS = ["npi", "clia_certificate_type", "clia_waiver_only", "clia_annual_volume",
                "clia_billed_lab_lines_per_year", "clia_volume_ratio",
                "clia_waiver_high_complexity"]
LINK_COLS = ["npi", "clia_number", "match_basis", "cert_type", "clia_annual_volume",
             "facility_name", "addr_state"]


# --------------------------------------------------------------------------- load
def _read_one(p: Path) -> pd.DataFrame:
    """One POS CLIA file: csv (UTF-8 then Latin-1), a zip holding one, or parquet."""
    suf = p.suffix.lower()
    if suf == ".parquet":
        return pd.read_parquet(p).astype(str)
    if suf == ".zip":
        with zipfile.ZipFile(p) as z:
            inner = next((n for n in z.namelist() if n.lower().endswith((".csv", ".txt"))), None)
            if inner is None:
                raise ValueError(f"no .csv inside {p.name}")
            data = z.read(inner)
        try:
            return pd.read_csv(io.BytesIO(data), dtype=str, encoding="utf-8")
        except UnicodeDecodeError:
            return pd.read_csv(io.BytesIO(data), dtype=str, encoding="latin-1")
    return read_csv_text(p)


def _canon_clia(s: pd.Series) -> pd.Series:
    """A CLIA number as the ten-character upper-case key (05D0642827); punctuation and
    spaces out; an empty or 'nan' value is None."""
    out = s.fillna("").astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    return out.where(out.str.len() >= 8, other=None)


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False).str.strip(), errors="coerce")


def _date(s: pd.Series) -> pd.Series:
    """CMS writes dates as YYYYMMDD, MM/DD/YYYY or ISO; all three parse."""
    raw = s.fillna("").astype(str).str.strip()
    d = pd.to_datetime(raw, errors="coerce", format="%Y%m%d")
    miss = d.isna() & (raw != "")
    if miss.any():
        d.loc[miss] = pd.to_datetime(raw[miss], errors="coerce")
    return d


def load_clia(path_or_folder: str | Path, log=print) -> pd.DataFrame:
    """The POS CLIA laboratory rows as a clean frame (one row per certificate record).

    Reads every ``*.csv`` / ``*.zip`` / ``*.parquet`` under a folder (or the one file
    given). RAISES when no CLIA number column resolves (the batch-1 rule: a silent blank
    identifier never feeds a scheme). Rows without a CLIA number are quarantined, never
    dropped silently: ``attrs['quarantined']`` carries the count and the reason.

    ``clia_annual_volume`` = the stated total where the edition carries one, else the SUM
    of the per-class volume columns present (accredited / non-accredited / PPM / waived
    and any further ``*TEST_VOL*`` column); ``attrs['volume_columns']`` records which
    columns fed it; when none resolves the column is all-NULL and the loader says so
    loudly (the certificate-type features still return)."""
    p = Path(path_or_folder)
    files = ([p] if p.is_file()
             else sorted(q for q in p.iterdir()
                         if q.is_file() and q.suffix.lower() in (".csv", ".zip", ".parquet")
                         and not q.name.startswith("~")))
    if not files:
        raise FileNotFoundError(f"no POS CLIA .csv / .zip / .parquet under {p}")
    frames = []
    for f in files:
        raw = _read_one(f)
        raw["_source_file"] = f.name
        frames.append(raw)
    raw = pd.concat(frames, ignore_index=True)
    header = [c for c in raw.columns if c != "_source_file"]
    resolved = _resolve_columns(header, CLIA_COLS)
    missing = [k for k in REQUIRED if k not in resolved]
    if missing:
        raise ValueError(f"POS CLIA file missing the {', '.join(missing)} column "
                         f"(candidates {CLIA_COLS['clia_number'][:4]} ...); saw {header[:15]}")
    log(f"  [clia] {len(raw):,} rows from {', '.join(f.name for f in files)}; resolved "
        + ", ".join(f"{k}={v}" for k, v in resolved.items()))
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()

    key = _canon_clia(df["clia_number"])
    n_blank = int(key.isna().sum())
    df["clia_number"] = key
    df = df[key.notna()].copy()

    # the annual volume: a stated total, else the sum of the class / specialty columns
    vol_cols: list[str] = []
    if "annual_volume_total" in df.columns:
        df["clia_annual_volume"] = _num(df["annual_volume_total"])
        vol_cols = [resolved["annual_volume_total"]]
    else:
        parts = [k for k in VOLUME_KEYS if k in df.columns]
        extra = [c for c in header if c not in resolved.values()
                 and _VOLUME_FALLBACK_RE.search(str(c)) and not _VOLUME_EXCLUDE_RE.search(str(c))]
        cols = parts + extra
        if cols:
            vals = pd.concat([_num(df[c]) for c in cols], axis=1)
            df["clia_annual_volume"] = vals.sum(axis=1, min_count=1)
            vol_cols = [resolved.get(c, c) for c in cols]
        else:
            df["clia_annual_volume"] = np.nan
    if vol_cols:
        n_vol = int(df["clia_annual_volume"].notna().sum())
        log(f"  [clia] clia_annual_volume from {vol_cols} ({n_vol:,} of {len(df):,} certificates "
            "carry a volume)")
    else:
        log("  [clia] WARNING: no annual test-volume column resolved "
            f"(looked for {CLIA_COLS['annual_volume_total'][:3]} ... and *TEST_VOL* columns): "
            "clia_volume_ratio stays NULL for every lab; the certificate-type features still ship")

    df["cert_type_code"] = (df["cert_type"].fillna("").astype(str).str.strip().str.lstrip("0")
                            if "cert_type" in df.columns else "")
    df["cert_type"] = df["cert_type_code"].map(CERT_TYPES).fillna("")
    for c in ("cert_effective_date", "cert_expiration_date", "termination_date", "orig_participation_date"):
        if c in df.columns:
            df[c] = _date(df[c])
    for c in ("facility_name", "addr_line1", "addr_city", "addr_state", "addr_zip",
              "lab_classification", "termination_code"):
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str).str.strip()
    df["addr_state"] = df["addr_state"].str.upper()
    df = df.reset_index(drop=True)
    df.attrs["resolved_columns"] = dict(resolved)
    df.attrs["volume_columns"] = vol_cols
    df.attrs["quarantined"] = n_blank
    df.attrs["quarantine_reason"] = "blank or malformed CLIA number"
    df.attrs["files"] = [f.name for f in files]
    if n_blank:
        log(f"  [clia] quarantined {n_blank:,} rows with no CLIA number")
    return df


def asof_certificates(clia: pd.DataFrame, asof_cutoff: str | None) -> pd.DataFrame:
    """Under a freeze (YYYY-MM) keep the labs that EXISTED at the cutoff month.

    2026-11-07: the start of a lab is its ORIGINAL participation date (``ORGNL_PRTCPTN_DT``),
    not its current certificate's effective date. Certificates renew every two years, so a
    2026 edition carries a 2024-2026 effective date for almost every open lab: filtering on it
    dropped the active labs (314,804 of 686,058 on pos_clia_202609) and kept mostly the ones
    that stopped renewing (closed / terminated, which leans toward the answer key). Rule:
      start = original participation date, else the certificate effective date when it is
              blank; a row with neither is kept (undated, never dropped);
      keep  = start on/before the cutoff month's end AND not terminated / expired before the
              cutoff month began (``TRMNTN_EXPRTN_DT``).
    The kept labs' details (certificate type, reported volumes) are still the edition's values:
    a post-cutoff edition stays a known current-state limit."""
    if not asof_cutoff:
        return clia
    cut_start = pd.Timestamp(str(asof_cutoff)[:7] + "-01")
    cut_end = cut_start + pd.offsets.MonthEnd(0)
    nat = pd.Series(pd.NaT, index=clia.index, dtype="datetime64[ns]")
    eff = (pd.to_datetime(clia["cert_effective_date"], errors="coerce")
           if "cert_effective_date" in clia.columns else nat)
    orig = (pd.to_datetime(clia["orig_participation_date"], errors="coerce")
            if "orig_participation_date" in clia.columns else nat)
    start = orig.where(orig.notna(), eff)
    keep = start.isna() | (start <= cut_end)
    if "termination_date" in clia.columns:
        term = pd.to_datetime(clia["termination_date"], errors="coerce")
        keep &= ~(term.notna() & (term < cut_start))
    out = clia[keep].copy()
    out.attrs = dict(clia.attrs)
    out.attrs["asof_kept"] = int(keep.sum())
    out.attrs["asof_total"] = int(len(clia))
    out.attrs["asof_basis"] = ("original participation date" if orig.notna().any()
                               else "certificate effective date (no original participation date)")
    return out


# --------------------------------------------------------------------------- link
def _other_id_clia(other_ids: pd.DataFrame) -> pd.DataFrame:
    """(npi, clia_number) from an NPPES Other Provider Identifier long table: rows typed
    'clia' (ccn_npi_crosswalk writes them so) or type-01 rows whose issuer text mentions
    CLIA. Accepts ``identifier`` or ``id_value`` as the value column."""
    if other_ids is None or not len(other_ids):
        return pd.DataFrame(columns=["npi", "clia_number"])
    o = other_ids.copy()
    val_c = next((c for c in ("identifier", "id_value", "value") if c in o.columns), None)
    if val_c is None or "npi" not in o.columns:
        return pd.DataFrame(columns=["npi", "clia_number"])
    t = o["id_type"].fillna("").astype(str).str.strip().str.lower() if "id_type" in o.columns \
        else pd.Series("", index=o.index)
    issuer = (o["issuer"].fillna("").astype(str).str.upper() if "issuer" in o.columns
              else pd.Series("", index=o.index))
    keep = (t == "clia") | (t.isin(["01", "1", "other"]) & issuer.str.contains("CLIA", regex=False))
    out = pd.DataFrame({"npi": o.loc[keep, "npi"].astype(str),
                        "clia_number": _canon_clia(o.loc[keep, val_c])})
    return out.dropna().drop_duplicates().reset_index(drop=True)


def _dim_keys(provider_dim: pd.DataFrame) -> pd.DataFrame:
    """npi, name_key, addr_key from provider_dim through the SHARED normalizers."""
    d = provider_dim.copy()
    d["npi"] = d["npi"].astype(str)
    name_src = next((c for c in ("org_legal_name", "provider_name", "name") if c in d.columns), None)
    if name_src is None:
        raise ValueError("provider_dim carries no name column (org_legal_name / provider_name)")
    name = d[name_src].fillna("").astype(str)
    if name_src == "org_legal_name" and "provider_name" in d.columns:
        name = name.where(name.str.strip() != "", d["provider_name"].fillna("").astype(str))
    d["name_key"] = _normalize_name(name)
    addr = pd.DataFrame({"line1": d.get("addr_line1", pd.Series("", index=d.index)),
                         "city": d.get("addr_city", pd.Series("", index=d.index)),
                         "state": d.get("addr_state", pd.Series("", index=d.index)),
                         "zip": d.get("addr_zip", pd.Series("", index=d.index))}, index=d.index)
    d["addr_key"] = _standardize_address(addr)
    return d[["npi", "name_key", "addr_key"]]


def clia_to_npi(clia: pd.DataFrame, provider_dim: pd.DataFrame,
                other_ids: pd.DataFrame | None = None, log=print) -> pd.DataFrame:
    """CLIA certificate -> NPI link table (one row per NPI x certificate).

    (a) ``other_ids``: the NPPES Other Provider Identifier rows (npi, id_type, identifier
        | id_value, issuer): a lab that recorded its CLIA number in NPPES is linked by the
        identifier itself (``match_basis = other_id``; several NPIs may claim one
        certificate, each keeps it);
    (b) the rest by EXACT name key + standardized address (the shared normalizers in
        clean_data; never a second normalizer) against provider_dim
        (``match_basis = name_address``); a (name, address) that lands on several NPIs is
        AMBIGUOUS and quarantined (``attrs['ambiguous']``), never fanned out.
    Many certificates per NPI are allowed (a lab system holds one per site); the
    features sum the volumes over an NPI's certificates."""
    c = clia.copy()
    c["clia_number"] = _canon_clia(c["clia_number"])
    c = c[c["clia_number"].notna()]
    cert_cols = [x for x in ("cert_type", "clia_annual_volume", "facility_name", "addr_state",
                             "lab_classification") if x in c.columns]
    certs = c.drop_duplicates("clia_number")[["clia_number"] + cert_cols]

    linked = []
    oid = _other_id_clia(other_ids)
    if len(oid):
        a = oid.merge(certs, on="clia_number", how="inner")
        a["match_basis"] = "other_id"
        linked.append(a)
    done = set(linked[0]["clia_number"]) if linked else set()

    rest = c[~c["clia_number"].isin(done)].copy()
    n_ambiguous = 0
    if len(rest):
        rest["name_key"] = _normalize_name(rest["facility_name"] if "facility_name" in rest.columns
                                           else pd.Series("", index=rest.index))
        addr = pd.DataFrame({"line1": rest.get("addr_line1", pd.Series("", index=rest.index)),
                             "city": rest.get("addr_city", pd.Series("", index=rest.index)),
                             "state": rest.get("addr_state", pd.Series("", index=rest.index)),
                             "zip": rest.get("addr_zip", pd.Series("", index=rest.index))},
                            index=rest.index)
        rest["addr_key"] = _standardize_address(addr)
        rest = rest[(rest["name_key"] != "") & (rest["addr_key"].str.len() > 6)]
        dim = _dim_keys(provider_dim)
        dim = dim[(dim["name_key"] != "") & (dim["addr_key"].str.len() > 6)]
        n_per_key = dim.groupby(["name_key", "addr_key"])["npi"].nunique()
        unique = n_per_key[n_per_key == 1].index
        b = rest.merge(dim.drop_duplicates(["name_key", "addr_key"]), on=["name_key", "addr_key"],
                       how="inner")
        amb = ~pd.MultiIndex.from_frame(b[["name_key", "addr_key"]]).isin(unique)
        n_ambiguous = int(amb.sum())
        b = b[~amb]
        b = b.drop_duplicates(["npi", "clia_number"])[["npi", "clia_number"] + cert_cols]
        b["match_basis"] = "name_address"
        linked.append(b)
    out = (pd.concat(linked, ignore_index=True) if linked
           else pd.DataFrame(columns=["npi", "clia_number", "match_basis"] + cert_cols))
    out["npi"] = out["npi"].astype(str)
    out = out.drop_duplicates(["npi", "clia_number"]).reset_index(drop=True)
    out.attrs["ambiguous"] = n_ambiguous
    out.attrs["n_certificates"] = int(certs["clia_number"].nunique())
    by = out["match_basis"].value_counts().to_dict() if len(out) else {}
    log(f"  [clia] linked {out['clia_number'].nunique() if len(out) else 0:,} of "
        f"{certs['clia_number'].nunique():,} certificates to {out['npi'].nunique() if len(out) else 0:,} NPIs "
        f"({', '.join(f'{k} {v:,}' for k, v in by.items()) or 'none'}; {n_ambiguous:,} ambiguous "
        "name+address rows set aside)")
    return out


# --------------------------------------------------------------------------- billed lines
def billed_lab_lines_from_parquet(spending_path: str | Path, asof_cutoff: str | None = None,
                                  npis: list[str] | None = None) -> pd.DataFrame:
    """Per billing NPI, from the spending fact in DuckDB (the fact never enters pandas):
    ``lab_lines_peak_year`` = lab-family lines in the busiest CALENDAR year (the figure a
    certificate's annual volume must cover; a partial first or last year never raises
    it), ``lab_years`` = calendar years with lab lines, ``lab_lines_total``, and
    ``high_complexity_lines`` = lines on the high-complexity families. The lab family is
    the sector_schemes lab set plus the unbundling codes (scheme_code_families)."""
    from src.attempt_2.duck import connect as _duck
    from src.ingest_cms.sector_schemes import _LAB_PRED, _in, LAB_UNBUNDLING_CODES
    lab_pred = f"({_LAB_PRED} OR {_in(LAB_UNBUNDLING_CODES)})"
    p = str(spending_path).replace("'", "''")
    conds = []
    if asof_cutoff:
        conds.append(f"CAST(service_month AS VARCHAR) <= '{str(asof_cutoff)[:7]}-99'")
    if npis:
        conds.append("CAST(billing_npi AS VARCHAR) IN (" + ",".join(f"'{str(n)}'" for n in npis) + ")")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    con = _duck()
    try:
        df = con.execute(f"""
            WITH b AS (
                SELECT CAST(billing_npi AS VARCHAR) AS npi,
                       UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) AS hcpcs,
                       SUBSTR(CAST(service_month AS VARCHAR), 1, 4) AS yr,
                       SUM(CAST(total_claim_lines AS DOUBLE)) AS lines
                FROM read_parquet('{p}') {where}
                GROUP BY 1, 2, 3),
            y AS (
                SELECT npi, yr,
                       SUM(CASE WHEN {lab_pred} THEN lines ELSE 0 END) AS lab_lines,
                       SUM(CASE WHEN {HIGH_COMPLEXITY_PRED} THEN lines ELSE 0 END) AS hc_lines
                FROM b GROUP BY 1, 2)
            SELECT npi,
                   MAX(lab_lines)                                   AS lab_lines_peak_year,
                   SUM(CASE WHEN lab_lines > 0 THEN 1 ELSE 0 END)   AS lab_years,
                   SUM(lab_lines)                                   AS lab_lines_total,
                   SUM(hc_lines)                                    AS high_complexity_lines
            FROM y GROUP BY npi
        """).df()
    finally:
        con.close()
    df["npi"] = df["npi"].astype(str)
    return df


# --------------------------------------------------------------------------- features
def clia_features(npi_clia: pd.DataFrame, billed: pd.DataFrame | None) -> pd.DataFrame:
    """Per NPI (the link table rolled up):

      clia_certificate_type         the highest-complexity certificate held
                                    (compliance / accreditation > registration > ppm > waiver;
                                    '' when no type resolved)
      clia_waiver_only              1 when every certificate is a waiver, 0 otherwise, NULL
                                    when no certificate type is known
      clia_annual_volume            the SUM of the reported annual volumes over the NPI's
                                    certificates (NULL when none reports one)
      clia_billed_lab_lines_per_year  lab-family lines in the busiest calendar year (NULL
                                    when the NPI bills no lab line)
      clia_volume_ratio             billed per year / reported volume, one-sided, NULL when
                                    either side is unknown or the volume is 0, clipped at 1000
      clia_waiver_high_complexity   1 when a waiver-only lab bills high-complexity codes,
                                    0 when linked and not, NULL when the type is unknown

    Every numeric feature is NULL when unknown, never 0."""
    if npi_clia is None or not len(npi_clia):
        return pd.DataFrame(columns=FEATURE_COLS)
    l = npi_clia.copy()
    l["npi"] = l["npi"].astype(str)
    l["cert_type"] = l["cert_type"].fillna("").astype(str) if "cert_type" in l.columns else ""
    l["_rank"] = l["cert_type"].map(CERT_RANK).fillna(0)
    vol = _num(l["clia_annual_volume"]) if "clia_annual_volume" in l.columns else pd.Series(np.nan, index=l.index)
    l["_vol"] = vol
    g = l.groupby("npi")
    top = l.sort_values(["npi", "_rank"], ascending=[True, False]).drop_duplicates("npi").set_index("npi")
    out = pd.DataFrame({"npi": top.index})
    out["clia_certificate_type"] = top["cert_type"].where(top["_rank"] > 0, "").values
    known = out["clia_certificate_type"] != ""
    n_typed = g["_rank"].apply(lambda s: int((s > 0).sum()))
    n_waiver = g["cert_type"].apply(lambda s: int((s == "waiver").sum()))
    waiver_only = ((n_waiver == n_typed) & (n_typed > 0)).reindex(out["npi"]).values
    out["clia_waiver_only"] = np.where(known, waiver_only.astype(float), np.nan)
    out["clia_annual_volume"] = g["_vol"].sum(min_count=1).reindex(out["npi"]).values

    if billed is not None and len(billed):
        b = billed.copy()
        b["npi"] = b["npi"].astype(str)
        b = b.drop_duplicates("npi").set_index("npi")
        peak = pd.to_numeric(b["lab_lines_peak_year"], errors="coerce").reindex(out["npi"])
        hc = pd.to_numeric(b["high_complexity_lines"], errors="coerce").reindex(out["npi"])
    else:
        peak = pd.Series(np.nan, index=out["npi"])
        hc = pd.Series(np.nan, index=out["npi"])
    peak = peak.where(peak > 0)
    out["clia_billed_lab_lines_per_year"] = peak.values
    volume = out["clia_annual_volume"]
    ratio = (out["clia_billed_lab_lines_per_year"] / volume.where(volume > 0))
    out["clia_volume_ratio"] = ratio.where(ratio.notna()).clip(lower=0.0, upper=RATIO_CLIP)
    hc_pos = (hc.fillna(0) > 0).values
    out["clia_waiver_high_complexity"] = np.where(
        known, ((out["clia_waiver_only"] == 1) & hc_pos).astype(float), np.nan)
    return out[FEATURE_COLS].reset_index(drop=True)


# --------------------------------------------------------------------------- report
def write_report(path: Path, clia: pd.DataFrame, link: pd.DataFrame, feats: pd.DataFrame,
                 asof: str | None = None) -> None:
    lines = ["# CLIA report", "",
             f"Files: {', '.join(clia.attrs.get('files', []))}"
             + (f"; as-of {asof}" if asof else ""), "",
             "Resolved columns: " + ", ".join(f"`{k}` = `{v}`" for k, v in
                                             clia.attrs.get("resolved_columns", {}).items()), "",
             f"Annual volume from: {clia.attrs.get('volume_columns') or 'NO VOLUME COLUMN RESOLVED'}",
             f"Quarantined rows (no CLIA number): {clia.attrs.get('quarantined', 0):,}", "",
             "## Certificates loaded by type", "", "| type | certificates | with a volume |", "|---|---:|---:|"]
    ct = clia.drop_duplicates("clia_number")
    for t, grp in ct.groupby(ct["cert_type"].replace("", "(unknown)")):
        lines.append(f"| {t} | {len(grp):,} | {int(grp['clia_annual_volume'].notna().sum()):,} |")
    lines += ["", "## NPIs matched by basis", ""]
    if len(link):
        for k, v in link["match_basis"].value_counts().items():
            lines.append(f"- {k}: {v:,} NPI x certificate rows")
        lines.append(f"- NPIs linked: {link['npi'].nunique():,}; certificates linked: "
                     f"{link['clia_number'].nunique():,} of {link.attrs.get('n_certificates', 0):,}; "
                     f"ambiguous name+address rows set aside: {link.attrs.get('ambiguous', 0):,}")
    else:
        lines.append("- none")
    lines += ["", "## Volume ratio (billed lab lines per year / reported annual volume)", ""]
    r = pd.to_numeric(feats["clia_volume_ratio"], errors="coerce").dropna() if len(feats) else pd.Series(dtype=float)
    if len(r):
        q = r.quantile([0.5, 0.9, 0.99])
        lines += [f"- NPIs with a ratio: {len(r):,}; median {q.iloc[0]:.2f}, p90 {q.iloc[1]:.2f}, "
                  f"p99 {q.iloc[2]:.2f}; at or above 1.0: {int((r >= 1).sum()):,}; "
                  f"at or above 10: {int((r >= 10).sum()):,}", ""]
        top = feats.dropna(subset=["clia_volume_ratio"]).sort_values("clia_volume_ratio", ascending=False).head(25)
        names = (link.drop_duplicates("npi").set_index("npi")[["facility_name", "addr_state"]]
                 if "facility_name" in link.columns else None)
        lines += ["| npi | name | state | type | reported volume | billed lines (peak year) | ratio |",
                  "|---|---|---|---|---:|---:|---:|"]
        for _, row in top.iterrows():
            nm = st = ""
            if names is not None and row["npi"] in names.index:
                nm, st = names.loc[row["npi"], "facility_name"], names.loc[row["npi"], "addr_state"]
            lines.append(f"| {row['npi']} | {nm} | {st} | {row['clia_certificate_type']} | "
                         f"{row['clia_annual_volume']:,.0f} | {row['clia_billed_lab_lines_per_year']:,.0f} | "
                         f"{row['clia_volume_ratio']:.1f} |")
    else:
        lines.append("- no NPI carries both a reported volume and billed lab lines")
    n_whc = int(pd.to_numeric(feats["clia_waiver_high_complexity"], errors="coerce").fillna(0).sum()) if len(feats) else 0
    n_wo = int(pd.to_numeric(feats["clia_waiver_only"], errors="coerce").fillna(0).sum()) if len(feats) else 0
    lines += ["", "## Waiver-only labs billing high-complexity codes", "",
              f"- waiver-only NPIs: {n_wo:,}; of them billing definitive drug testing / molecular / "
              f"PCR codes: {n_whc:,}", "",
              "Innocent explanations to rule out first: a certificate upgraded after the edition; "
              "a reference lab billing under the ordering site's NPI; an NPPES identifier recorded on "
              "the wrong record; a volume reported for one site of a system that bills under one NPI.",
              "A ratio is a contradiction between two of the provider's own filings, never a finding."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True, help="preclean/clia (folder) or one POS CLIA file")
    ap.add_argument("--provider-dim", required=True, help="processed/provider_dim.parquet")
    ap.add_argument("--spending", required=True, help="processed/spending_fact.parquet (or the as-of file)")
    ap.add_argument("--other-ids", default=None,
                    help="processed/nppes_other_ids.parquet (ccn_npi_crosswalk --other-ids-out; the 'clia' rows)")
    ap.add_argument("--asof", default=None, help="YYYY-MM: certificates effective on/before, service months on/before")
    ap.add_argument("--out", required=True, help="processed/clia_features.parquet (CLIA_REPORT.md beside it)")
    a = ap.parse_args()
    clia = asof_certificates(load_clia(a.inp), a.asof)
    dim = pd.read_parquet(a.provider_dim)
    other = pd.read_parquet(a.other_ids) if a.other_ids else None
    link = clia_to_npi(clia, dim, other_ids=other)
    billed = (billed_lab_lines_from_parquet(a.spending, asof_cutoff=a.asof,
                                            npis=sorted(set(link["npi"])))
              if len(link) else None)
    feats = clia_features(link, billed)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    feats.to_parquet(out, index=False)
    link_p = out.with_name(out.stem + "_link.parquet")
    link.to_parquet(link_p, index=False)
    rep = out.with_name("CLIA_REPORT.md")
    write_report(rep, clia, link, feats, asof=a.asof)
    print(f"Wrote {out} ({len(feats):,} NPIs), {link_p.name} ({len(link):,} links), {rep.name}")


if __name__ == "__main__":
    main()
