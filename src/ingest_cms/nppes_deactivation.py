"""
nppes_deactivation.py — billing under a deactivated NPI (research sweep E1).

Source: the CMS NPPES monthly deactivation file (NPI + deactivation date; free
at download.cms.gov/nppes). An NPI that keeps billing AFTER it was deactivated is
a clean integrity signal — a fly-by-night entity, a stolen/retired identity, or
billing that should have stopped. Unlike the SSA Death Master File (which needs
SSN/name linkage we don't hold publicly), this is keyed by NPI, so the match is
exact and the signal is defensible.

  deactivated_npis        parse NPI → deactivation_date (string IDs, real headers)
  billing_after_deactivation  join to the spending fact → per DEACTIVATED NPI the
                          dollars billed on/after ITS OWN deactivation date and the
                          share of its billing that is (grain="npi", the default);
                          grain="org" gives the same fact at org grain for
                          SINGLE-NPI organizations only. Feeds the
                          ``invalid_identity`` scheme — a high recovery-multiplier
                          scheme, since billing under a dead NPI is near-fully
                          unsupported.

THE GRAIN (2026-11-02, architecture audit C5): until this date the function returned
one row per ORGANIZATION (every org with a deactivated member), carrying the member's
own post/pre share as the org's, and the export broadcast it to every member NPI, so
every clinician of a health system with one retiree still billing read as billing
under a dead number. The fact belongs to the NPI that was deactivated. The default
frame is now NPI-grain; the org-grain frame is produced only where the org IS that one
NPI (member count 1 in npi_to_org), where broadcast and own-event coincide.

Dormant until the deactivation file + spending are loaded; exact-match, no fuzz.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import _resolve_columns, canonicalize_series

DEACT_COLS = {
    "npi": ["NPI", "npi"],
    "deactivation_date": ["NPI Deactivation Date", "Deactivation Date",
                          "deactivation_date", "NPPES Deactivation Date"],
}


def _find_header_row(df0: pd.DataFrame, max_scan: int = 10) -> int:
    """The CMS Deactivated NPI Report has a title/notice banner above the real
    header. Find the first row that contains an 'NPI' cell → that's the header."""
    for i in range(min(max_scan, len(df0))):
        cells = [str(x).strip().upper() for x in df0.iloc[i].tolist()]
        if "NPI" in cells:
            return i
    return 0


def load_deactivation(path: str | Path) -> pd.DataFrame:
    """Load the CMS NPPES Deactivated NPI Report in whatever form CMS hands you.

    The native download is a ``.zip`` containing an Excel report (NPI + NPPES
    Deactivation Date) with a title/notice banner above the header row. This unzips
    in memory if needed, finds the real header row, and returns a clean frame — so
    the raw file drops in as-is. A flat ``.csv``/``.parquet`` is read directly.
    """
    p = Path(path)
    suf = p.suffix.lower()
    if suf == ".parquet":
        return pd.read_parquet(p)
    if suf == ".csv":
        from src.attempt_2.clean_data import read_csv_text
        return read_csv_text(p)

    data, name = None, p.name
    if suf == ".zip":                                   # unzip the Excel/CSV inside
        with zipfile.ZipFile(p) as z:
            inner = next((n for n in z.namelist()
                          if n.lower().endswith((".xlsx", ".xls", ".csv"))), None)
            if inner is None:
                raise ValueError(f"no .xlsx/.csv inside {p.name}")
            data, name = z.read(inner), inner

    if name.lower().endswith(".csv"):
        from src.attempt_2.clean_data import read_csv_text
        return read_csv_text(io.BytesIO(data) if data else p)
    src = io.BytesIO(data) if data is not None else p
    probe = pd.read_excel(src, dtype=str, header=None, nrows=12)
    hdr = _find_header_row(probe)
    src = io.BytesIO(data) if data is not None else p   # re-seek for the real read
    return pd.read_excel(src, dtype=str, header=hdr)


_EDITION_RES = (
    re.compile(r"(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])"),        # 20260914
    re.compile(r"_(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(2\d)(?=[_.])", re.I),   # CMS: _091426_ or _091426. (MMDDYY)
)
_BANNER_RE = re.compile(r"as of\s+([A-Za-z]{3})[a-z]*\s+(\d{1,2}),?\s+(20\d{2})", re.I)
_MON = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}


def edition_of(path: str | Path) -> str | None:
    """The report's edition as YYYY-MM-DD, from the file name (20260914 or the CMS
    MMDDYY stamp _091426_), else the zip's inner name, else the banner cell
    ('NPPES Deactivated Records as of Sep 14 2026'). 2026-10-21: the export picked
    the alphabetically first zip, so a September report sat unread beside June's."""
    p = Path(path)
    names = [p.name]
    if p.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(p) as z:
                names += z.namelist()
        except Exception:
            pass
    for n in names:
        m = _EDITION_RES[0].search(n)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        m = _EDITION_RES[1].search(n)
        if m:
            return f"20{m.group(3)}-{m.group(1)}-{m.group(2)}"
    try:                                               # the banner cell
        if p.suffix.lower() in (".zip", ".xlsx", ".xls"):
            if p.suffix.lower() == ".zip":
                with zipfile.ZipFile(p) as z:
                    inner = next((n for n in z.namelist() if n.lower().endswith((".xlsx", ".xls"))), None)
                    if inner is None:
                        return None
                    src = io.BytesIO(z.read(inner))
            else:
                src = p
            probe = pd.read_excel(src, dtype=str, header=None, nrows=3)
            text = " ".join(str(x) for x in probe.values.ravel() if pd.notna(x))
            m = _BANNER_RE.search(text)
            if m:
                return f"{m.group(3)}-{_MON[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    except Exception:
        return None
    return None


def pick_deactivation_file(folder: str | Path, asof_cutoff: str | None = None
                           ) -> tuple[Path | None, str | None, list]:
    """The deactivation report to read: under a freeze (``asof_cutoff`` YYYY-MM) the
    NEWEST edition dated on/before the cutoff month (the pre-cutoff edition: a later
    cumulative file would also carry reactivations and corrections recorded after the
    freeze), else the newest edition on disk (current-day, or a frozen run with no
    pre-cutoff edition: the adapter still filters rows by deactivation date, and the
    export's vintage ledger records the substitution). Returns
    (path, edition, [(name, edition), ...])."""
    d = Path(folder)
    if not d.is_dir():
        return None, None, []
    cands = [q for q in sorted(d.iterdir()) if q.is_file() and q.suffix.lower() in (".zip", ".xlsx", ".xls", ".csv")
             and not q.name.endswith(".bak")]
    dated = [(q, edition_of(q)) for q in cands]
    known = [(q, e) for q, e in dated if e]
    listing = [(x.name, y) for x, y in dated]
    if known:
        if asof_cutoff:
            cut = str(asof_cutoff)[:7]
            eligible = [(q, e) for q, e in known if e[:7] <= cut]
            if eligible:
                q, e = max(eligible, key=lambda t: t[1])
                return q, e, listing
        q, e = max(known, key=lambda t: t[1])
        return q, e, listing
    if cands:
        return cands[0], None, listing
    return None, None, []


def deactivated_npis(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Parse the deactivation file → npi, deactivation_date. (table, quarantined)."""
    resolved = _resolve_columns(list(raw.columns), DEACT_COLS)
    if "npi" not in resolved:
        raise ValueError(f"deactivation file missing an NPI column; "
                         f"saw {list(raw.columns)[:12]}")
    df = raw.rename(columns={v: k for k, v in resolved.items()}).copy()
    npi = canonicalize_series(df["npi"])
    quarantined = int((npi.isna()
                       & df["npi"].fillna("").astype(str).str.strip().ne("")).sum())
    df = df.assign(npi=npi)[npi.notna()].copy()
    df["deactivation_date"] = pd.to_datetime(
        df.get("deactivation_date"), errors="coerce")
    out = (df[df["deactivation_date"].notna()]
           .groupby("npi", as_index=False)["deactivation_date"].min())
    return out, quarantined


NPI_GRAIN_COLS = ["npi", "post_deactivation_paid", "total_paid", "billing_after_deactivation"]
ORG_GRAIN_COLS = ["org_node_id", "post_deactivation_paid", "total_paid",
                  "billing_after_deactivation", "org_member_count"]


def _npi_grain(spending: pd.DataFrame, deactivated: pd.DataFrame) -> pd.DataFrame:
    """One row per DEACTIVATED NPI that appears in ``spending``."""
    s = spending.copy()
    s["billing_npi"] = s["billing_npi"].astype(str)
    s["total_paid"] = pd.to_numeric(s["total_paid"], errors="coerce").fillna(0.0)
    s["month_ts"] = pd.to_datetime(s["service_month"].astype(str).str.slice(0, 7),
                                   format="%Y-%m", errors="coerce")
    deact = dict(zip(deactivated["npi"].astype(str),
                     pd.to_datetime(deactivated["deactivation_date"], errors="coerce")))
    s["deact_date"] = s["billing_npi"].map(deact)
    s = s[s["deact_date"].notna()]
    if not len(s):
        return pd.DataFrame(columns=NPI_GRAIN_COLS)
    # positive-only dollars (a refund month never reads as negative post-deactivation
    # billing, and the share stays inside [0, 1] by construction)
    s["gross"] = s["total_paid"].clip(lower=0.0)
    s["post_gross"] = s["gross"].where(s["month_ts"] >= s["deact_date"], 0.0)
    g = s.groupby("billing_npi")
    out = pd.DataFrame({"post_deactivation_paid": g["post_gross"].sum(),
                        "total_paid": g["gross"].sum()})
    # NULL when the NPI billed nothing positive (nothing to assess); an observed 0
    # (deactivated, billed only before) is real
    out["billing_after_deactivation"] = (
        out["post_deactivation_paid"] / out["total_paid"]).where(out["total_paid"] > 0).clip(0, 1)
    out.index.name = "npi"
    return out.reset_index()[NPI_GRAIN_COLS]


def billing_after_deactivation(spending: pd.DataFrame, deactivated: pd.DataFrame,
                               npi_to_org: pd.DataFrame | None = None,
                               grain: str = "npi") -> pd.DataFrame:
    """Dollars billed on/after an NPI's OWN NPPES deactivation date.

    ``spending``: billing_npi, service_month (YYYY-MM), total_paid (the export passes
    only the deactivated NPIs' rows; a full fact works too, non-deactivated NPIs are
    simply absent from the result: NULL downstream, never 0).

    grain="npi" (default) returns ``npi, post_deactivation_paid, total_paid,
    billing_after_deactivation`` — one row per deactivated NPI with billing; the share
    is the NPI's post-deactivation gross over its gross (0..1; 0 = deactivated and
    billed only before; NULL when nothing positive was billed). This is the frame
    the matrix feature must come from (audit C5: the event is the NPI's, never the
    organization's). ``npi_to_org`` is ignored.

    grain="org" returns ``org_node_id, post_deactivation_paid, total_paid,
    billing_after_deactivation, org_member_count`` for SINGLE-NPI organizations whose
    one member is deactivated (member count 1 in ``npi_to_org``); a multi-member org
    never gets a row, so nothing can be broadcast to a sibling. ``npi_to_org`` is
    required. The column name is the same so an org-grain consumer needs no rename;
    the two frames are alternatives for the same matrix column, never merged together.

    Before 2026-11-02 this returned the org-grain frame for EVERY org with a
    deactivated member (the member's own share as the org's), which the export
    broadcast to every member NPI.
    """
    if grain not in ("npi", "org"):
        raise ValueError(f"grain must be 'npi' or 'org', got {grain!r}")
    npi_frame = _npi_grain(spending, deactivated)
    if grain == "npi":
        return npi_frame
    if npi_to_org is None:
        raise ValueError("grain='org' needs npi_to_org")
    xw = pd.DataFrame({"npi": npi_to_org["npi"].astype(str),
                       "org_node_id": npi_to_org["org_node_id"].astype(str)})
    assert xw["npi"].is_unique, "npi_to_org has duplicate NPIs (ambiguous attribution)"
    members = xw.groupby("org_node_id")["npi"].size()
    if not len(npi_frame):
        return pd.DataFrame(columns=ORG_GRAIN_COLS)
    o = npi_frame.merge(xw, on="npi", how="inner")
    o["org_member_count"] = o["org_node_id"].map(members).astype(int)
    o = o[o["org_member_count"] == 1]
    assert o["org_node_id"].is_unique, "a single-NPI org produced two rows"
    return o[ORG_GRAIN_COLS].reset_index(drop=True)


def billing_after_deactivation_frames(spending: pd.DataFrame, deactivated: pd.DataFrame,
                                      npi_to_org: pd.DataFrame
                                      ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(npi_grain_frame, single_npi_org_frame): both grains in one call."""
    return (billing_after_deactivation(spending, deactivated, grain="npi"),
            billing_after_deactivation(spending, deactivated, npi_to_org, grain="org"))
