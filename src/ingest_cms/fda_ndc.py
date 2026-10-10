"""
fda_ndc.py — the FDA National Drug Code Directory (product.txt): labeler code ->
manufacturer, and the DEA schedule per product.

Why: the Medicaid "Spending by NDC" file names drugs by 11-digit NDC and Open Payments
names the MANUFACTURER that paid a prescriber. The bridge is the NDC's labeler code
(its first five digits) -> the labeler's name, which this directory carries. With it
the pharma_kickback scheme can run on Medicaid prescribing instead of Medicare Part D
only (medicaid_ndc.medicaid_kickback).

File: https://www.fda.gov/drugs/drug-approvals-and-databases/national-drug-code-directory
("NDC Database File", text version: product.txt + package.txt, tab-delimited). Save
under preclean/fda_ndc/ as the two .txt files or the zip; the loader reads either.

Outputs (pure functions, no files written):
  load_fda_product(path)      the product table as strings (path = product.txt, the folder,
                              or the zip)
  labeler_map(product)        labeler5, labeler_name, mfr_key (the name key Open Payments'
                              manufacturer names are matched on)
  product_schedule(product)   ndc9 (labeler5 + product4), dea_schedule, is_controlled
  manufacturer_key(name)      the shared normalizer for manufacturer names on both sides
"""

from __future__ import annotations

import re
import zipfile
from io import StringIO
from pathlib import Path

import pandas as pd

PRODUCT_COLS = {
    "product_ndc": ["PRODUCTNDC", "productndc", "PRODUCT_NDC"],
    "proprietary": ["PROPRIETARYNAME", "proprietaryname"],
    "nonproprietary": ["NONPROPRIETARYNAME", "nonproprietaryname"],
    "labeler": ["LABELERNAME", "labelername", "LABELER_NAME"],
    "substance": ["SUBSTANCENAME", "substancename"],
    "dea": ["DEASCHEDULE", "deaschedule", "DEA_SCHEDULE"],
}

# corporate boilerplate that never distinguishes a manufacturer
_MFR_STOP = frozenset({
    "INC", "INCORPORATED", "LLC", "LLP", "LTD", "LIMITED", "CORP", "CORPORATION", "CO",
    "COMPANY", "PLC", "SA", "AG", "GMBH", "NV", "BV", "SRL", "SPA", "PTY", "THE", "AND", "OF",
    "PHARMACEUTICALS", "PHARMACEUTICAL", "PHARMA", "PHARMACY", "LABORATORIES", "LABS", "LAB",
    "LABORATORY", "USA", "US", "NORTH", "AMERICA", "AMERICAN", "INTERNATIONAL", "GLOBAL",
    "HOLDINGS", "GROUP", "DIVISION", "DIV", "A", "AN", "DBA", "D/B/A", "BIOSCIENCES",
    "BIOSCIENCE", "BIOTECH", "BIOTECHNOLOGY", "THERAPEUTICS", "HEALTHCARE", "HEALTH", "MEDICAL",
    "PRODUCTS", "SCIENCES", "SCIENCE", "MANUFACTURING", "MFG", "DISTRIBUTION", "DISTRIBUTORS",
})


def manufacturer_key(name: str) -> str:
    """The first distinctive token of a manufacturer name ("PFIZER LABORATORIES DIV
    PFIZER INC" -> "PFIZER", "Teva Pharmaceuticals USA, Inc." -> "TEVA"). The same
    key is built from Open Payments' payer names, so a subsidiary, a division and
    the parent fall together. Precision is traded for recall on purpose: the
    feature is a one-sided exposure share, and the trade is documented here."""
    toks = [t for t in re.split(r"[^A-Z0-9]+", str(name or "").upper()) if t]
    toks = [t for t in toks if t not in _MFR_STOP and not t.isdigit()]
    if not toks:
        return ""
    for t in toks:
        if len(t) >= 4:
            return t
    return toks[0]


def _read_product_text(text: str) -> pd.DataFrame:
    return pd.read_csv(StringIO(text), sep="\t", dtype=str, keep_default_na=False,
                       on_bad_lines="skip", engine="python")


def load_fda_product(path: str | Path) -> pd.DataFrame:
    """product.txt as an all-string frame. ``path`` may be the file, the folder
    holding it, or the FDA zip (product.txt inside). Latin-1 tolerant."""
    p = Path(path)
    if p.is_dir():
        cand = p / "product.txt"
        if not cand.exists():
            zips = sorted(p.glob("*.zip"))
            if not zips:
                raise FileNotFoundError(f"no product.txt or zip under {p}")
            cand = zips[-1]
        p = cand
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            name = next((n for n in z.namelist() if n.lower().endswith("product.txt")), None)
            if name is None:
                raise FileNotFoundError(f"{p.name} holds no product.txt")
            raw = z.read(name)
    else:
        raw = p.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    df = _read_product_text(text)
    from src.attempt_2.clean_data import _resolve_columns
    resolved = _resolve_columns(list(df.columns), PRODUCT_COLS)
    if "product_ndc" not in resolved or "labeler" not in resolved:
        raise ValueError(f"FDA product file missing PRODUCTNDC / LABELERNAME; saw {list(df.columns)[:10]}")
    return df.rename(columns={v: k for k, v in resolved.items()})


def _split_product_ndc(s: pd.Series) -> tuple[pd.Series, pd.Series]:
    """FDA product NDCs are 'labeler-product' with 4- or 5-digit labelers and 3- or
    4-digit products; the Medicaid file's 11-digit NDC is 5-4-2. Pad both parts."""
    parts = s.fillna("").astype(str).str.strip().str.split("-", n=1, expand=True)
    lab = parts[0].str.replace(r"\D", "", regex=True).str.zfill(5)
    prod = (parts[1] if parts.shape[1] > 1 else pd.Series("", index=s.index)).fillna("") \
        .str.replace(r"\D", "", regex=True).str.zfill(4)
    return lab, prod


def labeler_map(product: pd.DataFrame) -> pd.DataFrame:
    """labeler5 -> labeler_name (the modal name) + mfr_key. One row per labeler."""
    lab, _ = _split_product_ndc(product["product_ndc"])
    df = pd.DataFrame({"labeler5": lab, "labeler_name": product["labeler"].fillna("").astype(str).str.strip()})
    df = df[(df["labeler5"].str.len() == 5) & (df["labeler_name"] != "")]
    out = (df.groupby("labeler5")["labeler_name"]
             .agg(lambda s: s.mode().iat[0] if len(s) else "").reset_index())
    out["mfr_key"] = out["labeler_name"].map(manufacturer_key)
    return out


def product_schedule(product: pd.DataFrame) -> pd.DataFrame:
    """ndc9 (labeler5 + product4) -> dea_schedule ('CII' ...), is_controlled."""
    lab, prod = _split_product_ndc(product["product_ndc"])
    dea = (product["dea"] if "dea" in product.columns else pd.Series("", index=product.index)) \
        .fillna("").astype(str).str.strip().str.upper()
    df = pd.DataFrame({"ndc9": lab + prod, "dea_schedule": dea})
    df = df[df["ndc9"].str.len() == 9]
    out = df.groupby("ndc9", as_index=False).agg(dea_schedule=("dea_schedule", lambda s: max(s, key=len)))
    out["is_controlled"] = out["dea_schedule"].str.startswith("C").astype(int)
    return out
