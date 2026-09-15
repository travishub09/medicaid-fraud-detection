"""
capacity.py — physical plausibility: what the billing implies vs what the
building and staff could deliver (the "700 patients a day, ten cars in the
lot" test, computed from public data before anyone drives there).

Two sides per billing NPI:

  DEMAND (from the paid-claims fact, via pi_facts.billing_facts)
    patients per working day, implied clinician-hours per working day (timed
    codes x minutes map), per-diem patient-days per month, lines per day.

  CAPACITY (from the public record already on disk)
    documented clinicians  = the largest of three independent counts:
                             rendering NPIs on the claims, individuals who
                             reassign benefits to the organization (PECOS
                             reassignment edges), individuals whose NPPES
                             practice address is the same suite (provider_dim
                             addr_key). An INDIVIDUAL NPI is one person: 1.
    sites                  = NPPES practice locations (1 + secondary locations
                             from the pl_pfile when supplied).
    beds                   = Provider of Services certified beds through the
                             CCN<->NPI crosswalk (facilities only).

  RATIOS -> FLAGS (ratios, not raw volume, so a big documented staff passes)
    hours_per_clinician_day  >= 12 long; >= 24 impossible
    patients_per_clinician_day >= 40 (clinic / behavioral families)
    occupancy = per-diem patient-days / (beds x days) > 1.0 impossible

Service families set which ratio applies: labs are specimens (patients/day
is not a room count), transport is vehicles, DME is deliveries, home and
community services are aides who have no NPI (documented-clinician counts
undercount them, so their hours ratio is reported, never flagged). Every
row is a lead for counsel review, never an accusation; the innocent
explanations are listed in the report.

  python -m src.model_a.capacity --spending processed/spending_fact.parquet \
      --matrix <run>/provider_features_for_model.parquet --min-paid 2000000 \
      --provider-dim processed/provider_dim.parquet --graph-dir graph \
      [--pos-dir preclean/pos --ccn-crosswalk processed/ccn_npi_crosswalk.parquet] \
      [--practice-locations preclean/nppes/pl_pfile_*.csv] [--minutes ...] --out <dir>
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .hcpcs_descriptions import HCPCS_DESC

WORKING_DAYS = 21.0
DAYS_PER_MONTH = 30.4
HOURS_LONG, HOURS_IMPOSSIBLE = 12.0, 24.0
PATIENTS_PER_CLINICIAN = 40.0

_LAB = re.compile(r"^(8\d{4}|U0\d{3}|\d{4}U|G04[89]\d|803\d\d)$")
_TRANSPORT = re.compile(r"^A04")
_DME = re.compile(r"^(A4|A5|A6|A7|A9|E0|E1|E2|K0|L\d|V2|V5|B4|B9)")
_HCBS = re.compile(r"^(T10|T20|S51|S91|S5|H0|H2)")
_REV_ROOM = re.compile(r"^0(1\d\d|2\d\d)$")            # UB revenue codes: room & board

INNOCENT = [
    "the provider bills Medicare and commercial payers too, so our Medicaid-only view "
    "UNDERSTATES demand; a flag here is conservative, not generous",
    "rendering clinicians credentialed elsewhere (locums, telehealth, contracted groups) "
    "do not appear in the reassignment file or at the address",
    "a minutes map assigns a time value to a code billed per unit rather than per session",
    "the NPPES address is an administrative office while care is delivered at unlisted "
    "sites or in patients' homes",
    "beds in the POS file lag a licensed expansion",
]


def service_family(code: str) -> str:
    c = str(code or "").upper()
    d = HCPCS_DESC.get(c, "").lower()
    if _LAB.match(c):
        return "lab"
    if _TRANSPORT.match(c):
        return "transport"
    if _DME.match(c):
        return "dme"
    if _REV_ROOM.match(c) or "room and board" in d or "per diem" in d or "residential" in d:
        return "facility"
    if _HCBS.match(c) or any(k in d for k in ("home health", "home care", "personal care",
                                              "attendant", "companion", "private-duty",
                                              "per 15 min", "per hour", "waiver", "habilitation")):
        return "hcbs"
    if c.startswith("9"):
        return "clinic"
    return "other"


def is_per_diem(code: str) -> bool:
    c = str(code or "").upper()
    d = HCPCS_DESC.get(c, "").lower()
    return bool(_REV_ROOM.match(c)) or "per diem" in d or "room and board" in d


# --------------------------------------------------------------------------- #
# capacity inputs
# --------------------------------------------------------------------------- #

def clinician_counts(npis: list[str], provider_dim: pd.DataFrame | None,
                     npi_to_org: pd.DataFrame | None,
                     reassign_edges: pd.DataFrame | None) -> pd.DataFrame:
    """npi, entity_type, n_reassign (individuals reassigning to this NPI's org),
    n_colocated (individuals at the same addr_key), addr_key, org_node_id."""
    out = pd.DataFrame({"npi": [str(n) for n in npis]})
    out["entity_type"] = ""
    out["addr_key"] = ""
    out["org_node_id"] = ""
    out["n_reassign"] = 0
    out["n_colocated"] = 0
    if provider_dim is not None and len(provider_dim):
        pdim = provider_dim.copy()
        pdim["npi"] = pdim["npi"].astype(str)
        et = pdim.get("entity_type", pd.Series("", index=pdim.index)).fillna("").astype(str).str.strip()
        ak = pdim.get("addr_key", pd.Series("", index=pdim.index)).fillna("").astype(str)
        m = pdim.set_index("npi")
        out["entity_type"] = out["npi"].map(et.set_axis(pdim["npi"])).fillna("")
        out["addr_key"] = out["npi"].map(ak.set_axis(pdim["npi"])).fillna("")
        # individuals per address (exclude the NPI itself if it is an individual)
        ind = pdim[(et == "1") & (ak != "")]
        per_addr = ind.groupby(ak[ind.index]).size()
        out["n_colocated"] = out["addr_key"].map(per_addr).fillna(0).astype(int)
        out.loc[out["entity_type"] == "1", "n_colocated"] = (
            out.loc[out["entity_type"] == "1", "n_colocated"] - 1).clip(lower=0)
    if npi_to_org is not None and len(npi_to_org):
        xw = npi_to_org[["npi", "org_node_id"]].astype(str).drop_duplicates("npi")
        out["org_node_id"] = out["npi"].map(dict(zip(xw["npi"], xw["org_node_id"]))).fillna("")
        if reassign_edges is not None and len(reassign_edges) and {"src_id", "dst_id"} <= set(reassign_edges.columns):
            e = reassign_edges[["src_id", "dst_id"]].astype(str)
            per_org = e.drop_duplicates().groupby("dst_id")["src_id"].nunique()
            out["n_reassign"] = out["org_node_id"].map(per_org).fillna(0).astype(int)
    return out


def bed_counts(npis: list[str], pos_capacity: pd.DataFrame | None,
               ccn_to_npi: pd.DataFrame | None) -> pd.Series:
    """Certified beds per NPI (max over its CCNs); NaN when unknown."""
    s = pd.Series(np.nan, index=[str(n) for n in npis], dtype=float)
    if pos_capacity is None or ccn_to_npi is None or not len(pos_capacity) or not len(ccn_to_npi):
        return s
    xw = ccn_to_npi[["ccn", "npi"]].astype(str)
    cap = pos_capacity[["ccn", "bed_count"]].copy()
    cap["ccn"] = cap["ccn"].astype(str)
    cap["bed_count"] = pd.to_numeric(cap["bed_count"], errors="coerce")
    j = xw.merge(cap, on="ccn", how="inner").groupby("npi")["bed_count"].max()
    return s.index.to_series().map(j).astype(float)


def site_counts(npis: list[str], practice_locations: pd.DataFrame | None) -> pd.Series:
    """1 + distinct secondary practice locations from the NPPES pl_pfile."""
    s = pd.Series(1, index=[str(n) for n in npis], dtype=int)
    if practice_locations is None or not len(practice_locations):
        return s
    pl = practice_locations.copy()
    npi_col = next((c for c in pl.columns if str(c).strip().upper() == "NPI"), None)
    addr_cols = [c for c in pl.columns if re.search(r"address.*line\s*1|address_1|addr.*1", str(c), re.I)]
    if not npi_col or not addr_cols:
        return s
    pl["_npi"] = pl[npi_col].astype(str)
    pl["_addr"] = pl[addr_cols[0]].fillna("").astype(str).str.upper().str.strip()
    n = pl[pl["_addr"] != ""].groupby("_npi")["_addr"].nunique()
    return (s.index.to_series().map(n).fillna(0).astype(int) + 1)


# --------------------------------------------------------------------------- #
# the table
# --------------------------------------------------------------------------- #

def merge_licensed(beds: pd.Series, licensed_by_npi: pd.DataFrame | None) -> tuple[pd.Series, pd.Series]:
    """Combine POS certified beds with state licensed capacity (max of the two);
    returns (beds, source) where source is 'pos', 'licensed', 'pos+licensed' or ''."""
    src = pd.Series(np.where(beds.notna(), "pos", ""), index=beds.index, dtype=object)
    if licensed_by_npi is None or not len(licensed_by_npi):
        return beds, src
    lic = licensed_by_npi.copy()
    lic["npi"] = lic["npi"].astype(str)
    lc = beds.index.to_series().map(dict(zip(lic["npi"], pd.to_numeric(lic["licensed_capacity"], errors="coerce"))))
    both = beds.notna() & lc.notna()
    out = beds.where(~lc.notna() | both, lc)
    out[both] = np.maximum(beds[both], lc[both])
    src[lc.notna() & ~both] = "licensed"
    src[both] = "pos+licensed"
    return out, src


def capacity_table(facts: dict, counts: pd.DataFrame, beds: pd.Series, sites: pd.Series,
                   names: dict | None = None, bed_source: pd.Series | None = None) -> pd.DataFrame:
    """One row per NPI with demand, capacity, ratios and flags."""
    names = names or {}
    bed_source = bed_source if bed_source is not None else pd.Series("", index=beds.index, dtype=object)
    c = counts.set_index("npi")
    rows = []
    for npi, bf in facts.items():
        npi = str(npi)
        if not bf:
            continue
        tc = bf.get("top_codes") or []
        top = tc[0]["hcpcs"] if tc else ""
        fam = service_family(top)
        pm, am = bf.get("patient_months") or 0, bf.get("active_months") or 0
        patients_day = (pm / am / WORKING_DAYS) if pm and am else np.nan
        ih = bf.get("implied_hours") or {}
        h_worst = ih.get("worst_hours_per_working_day")
        h_med = ih.get("median_hours_per_working_day")
        ns = bf.get("n_servicing_npis")
        et = str(c.loc[npi, "entity_type"]) if npi in c.index else ""
        n_re = int(c.loc[npi, "n_reassign"]) if npi in c.index else 0
        n_co = int(c.loc[npi, "n_colocated"]) if npi in c.index else 0
        if et == "1":
            doc = 1
            basis = "individual NPI: one person"
        else:
            cands = {"claims": int(ns or 0), "reassignment": n_re, "same address": n_co}
            k = max(cands, key=cands.get)
            doc = max(cands[k], 1)
            basis = f"{k} ({', '.join(f'{a} {b}' for a, b in cands.items())})"
        per_diem_lines = sum((t.get("lines") or 0) for t in tc if is_per_diem(t.get("hcpcs")))
        pd_days_month = (per_diem_lines / am) if am else np.nan
        b = float(beds.get(npi, np.nan)) if len(beds) else np.nan
        occ = (pd_days_month / (b * DAYS_PER_MONTH)) if (b and b > 0 and pd_days_month == pd_days_month) else np.nan
        hpc = (h_worst / doc) if h_worst else np.nan
        ppc = (patients_day / doc) if patients_day == patients_day else np.nan
        flags = []
        if fam in ("clinic", "facility", "other") or et == "1":
            if hpc == hpc and hpc >= HOURS_IMPOSSIBLE:
                flags.append("impossible_hours")
            elif hpc == hpc and hpc >= HOURS_LONG:
                flags.append("long_hours")
            if ppc == ppc and ppc >= PATIENTS_PER_CLINICIAN and fam in ("clinic", "other"):
                flags.append("patients_per_clinician")
        if occ == occ and occ > 1.0:
            flags.append("occupancy_over_100")
        rows.append({
            "npi": npi, "name": names.get(npi, ""), "entity_type": et, "family": fam, "top_code": top,
            "paid_total": float(bf.get("paid_total") or 0), "active_months": am,
            "patients_per_day": patients_day, "hours_per_day_worst": h_worst, "hours_per_day_median": h_med,
            "n_servicing": ns, "n_reassign": n_re, "n_colocated": n_co,
            "documented_clinicians": doc, "clinician_basis": basis,
            "sites": int(sites.get(npi, 1)) if len(sites) else 1, "beds": b,
            "beds_source": str(bed_source.get(npi, "")) if len(bed_source) else "",
            "per_diem_days_per_month": pd_days_month,
            "hours_per_clinician_day": hpc, "patients_per_clinician_day": ppc, "occupancy": occ,
            "flags": ";".join(flags),
        })
    t = pd.DataFrame(rows)
    if not len(t):
        return t
    t["severity"] = (t["flags"].str.contains("impossible|occupancy").astype(int) * 2
                     + t["flags"].str.contains("long|patients_per").astype(int))
    return t.sort_values(["severity", "paid_total"], ascending=[False, False]).reset_index(drop=True)


def to_markdown(t: pd.DataFrame, inputs: dict) -> str:
    L = ["# CAPACITY CHECK — what the billing implies vs what the record could deliver", "",
         "_Per billing NPI: patients and clinician-hours per working day implied by the paid claims, "
         "against the clinicians the public record documents (rendering NPIs on claims, PECOS "
         "reassignments to the organization, individuals at the same address), practice sites, and "
         "certified beds. Flags fire on the RATIOS, so a large documented staff passes and a large "
         "biller with two names in the record does not. Labs, transport, DME and home-and-community "
         "services are reported but not flagged on clinician ratios (specimens, vehicles, deliveries, "
         "and aides without NPIs). Leads for counsel review, never accusations._", "",
         f"_Inputs: {', '.join(f'{k}={v}' for k, v in inputs.items() if v)}._", ""]
    if not len(t):
        return "\n".join(L + ["_no rows_"]) + "\n"
    fl = t[t["flags"] != ""]
    L += [f"**{len(fl):,} of {len(t):,} NPIs flagged** "
          f"({int(t['flags'].str.contains('impossible').sum()):,} impossible hours, "
          f"{int(t['flags'].str.contains('long').sum()):,} long hours, "
          f"{int(t['flags'].str.contains('patients_per').sum()):,} patients per clinician, "
          f"{int(t['flags'].str.contains('occupancy').sum()):,} occupancy over 100%).", ""]
    L += ["## Flagged (ranked by severity, then dollars)", "",
          "| # | NPI | name | type | family | paid | patients/day | hours/day (worst) | documented clinicians (basis) | "
          "hours per clinician | patients per clinician | beds | occupancy | flags |",
          "|--:|---|---|---|---|--:|--:|--:|---|--:|--:|--:|--:|---|"]

    def f(v, fmt):
        return fmt.format(v) if isinstance(v, (int, float)) and v == v else ""
    for i, r in enumerate(fl.head(150).itertuples(), 1):
        L.append(f"| {i} | {r.npi} | {str(r.name)[:36]} | {r.entity_type} | {r.family} | ${r.paid_total/1e6:,.1f}M | "
                 f"{f(r.patients_per_day, '{:,.0f}')} | {f(r.hours_per_day_worst, '{:,.0f}')} | "
                 f"{r.documented_clinicians} ({str(r.clinician_basis)[:40]}) | {f(r.hours_per_clinician_day, '{:,.1f}')} | "
                 f"{f(r.patients_per_clinician_day, '{:,.0f}')} | {f(r.beds, '{:,.0f}')} | {f(r.occupancy, '{:.0%}')} | {r.flags} |")
    L += ["", "## Innocent explanations to rule out first", ""]
    L += [f"- {x}." for x in INNOCENT]
    L += ["", "_Full table with every NPI scanned: capacity.csv._",
          "", "_Leads for counsel review, never accusations._"]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _first(root: Path, *patterns: str):
    for pat in patterns:
        hits = sorted(root.glob(pat))
        if hits:
            return hits[0]
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spending", required=True)
    ap.add_argument("--matrix", default=None, help="run matrix: picks NPIs by --min-paid and supplies names")
    ap.add_argument("--min-paid", type=float, default=2_000_000.0)
    ap.add_argument("--npis-file", default=None, help="alternative universe: CSV with an npi column, or one NPI per line")
    ap.add_argument("--provider-dim", default=None)
    ap.add_argument("--graph-dir", default=None, help="graph folder with npi_to_org.parquet and edges/reassigns_to_edges.parquet")
    ap.add_argument("--pos-dir", default=None, help="preclean/pos (Provider of Services csv, rglob)")
    ap.add_argument("--ccn-crosswalk", default=None, help="processed/ccn_npi_crosswalk.parquet")
    ap.add_argument("--practice-locations", default=None, help="NPPES pl_pfile csv (secondary practice locations)")
    ap.add_argument("--licensed-capacity", default=None,
                    help="processed/licensed_capacity_by_npi.parquet (state rosters via ingest_cms.licensed_capacity)")
    ap.add_argument("--minutes", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    root = Path(os.environ.get("MEDICAID_DATA_ROOT", str(Path.home() / "Desktop/data")))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from src.attempt_2.clean_data import read_csv_text

    names: dict = {}
    if a.npis_file:
        p = Path(a.npis_file)
        if p.suffix.lower() == ".csv":
            df = pd.read_csv(p, dtype=str)
            npis = df["npi"].dropna().astype(str).tolist()
        else:
            npis = [ln.strip() for ln in p.read_text().splitlines() if ln.strip()]
    elif a.matrix:
        import pyarrow.parquet as pq
        cols = [c for c in ("npi", "net_paid", "provider_name", "org_legal_name")
                if c in pq.ParquetFile(a.matrix).schema_arrow.names]
        m = pd.read_parquet(a.matrix, columns=cols)
        m["npi"] = m["npi"].astype(str)
        m = m[pd.to_numeric(m.get("net_paid"), errors="coerce").fillna(0) >= a.min_paid]
        npis = m["npi"].tolist()
        nm = m.get("provider_name", m.get("org_legal_name", pd.Series("", index=m.index))).fillna("")
        names = dict(zip(m["npi"], nm.astype(str)))
    else:
        raise SystemExit("give --matrix (with --min-paid) or --npis-file")
    print(f"[capacity] universe: {len(npis):,} billing NPIs")

    pdim = pd.read_parquet(a.provider_dim) if a.provider_dim else (
        pd.read_parquet(root / "processed" / "provider_dim.parquet")
        if (root / "processed" / "provider_dim.parquet").exists() else None)
    g = Path(a.graph_dir) if a.graph_dir else root / "graph"
    n2o = pd.read_parquet(g / "npi_to_org.parquet") if (g / "npi_to_org.parquet").exists() else None
    edges_p = g / "edges" / "reassigns_to_edges.parquet"
    edges = pd.read_parquet(edges_p) if edges_p.exists() else None
    print(f"[capacity] provider_dim {'ok' if pdim is not None else 'MISSING'}; npi_to_org "
          f"{'ok' if n2o is not None else 'MISSING'}; reassignment edges {'ok' if edges is not None else 'MISSING'}")

    pos_cap = None
    pos_dir = Path(a.pos_dir) if a.pos_dir else root / "preclean" / "pos"
    if pos_dir.exists():
        from src.ingest_cms import pos
        caps = []
        for pf in sorted(pos_dir.rglob("*.csv")):
            try:
                caps.append(pos.compute_pos_capacity(read_csv_text(pf)))
            except Exception as e:  # noqa: BLE001
                print(f"  [pos] {pf.name} skipped: {e}")
        if caps:
            pos_cap = pd.concat(caps, ignore_index=True).groupby("ccn", as_index=False)["bed_count"].max()
    # the export writes processed/ccn_to_npi.parquet (ingest_cms.ccn_npi_crosswalk --out);
    # accept an explicit path or either conventional name
    cands = ([Path(a.ccn_crosswalk)] if a.ccn_crosswalk else []) + [
        root / "processed" / "ccn_to_npi.parquet", root / "processed" / "ccn_npi_crosswalk.parquet"]
    xw_p = next((p for p in cands if p.exists()), None)
    ccn_xw = pd.read_parquet(xw_p) if xw_p is not None else None
    print(f"[capacity] POS beds {'ok' if pos_cap is not None else 'MISSING'}; CCN crosswalk "
          f"{'ok' if ccn_xw is not None else 'MISSING'}")
    pl = read_csv_text(a.practice_locations) if a.practice_locations else None

    minutes = Path(a.minutes) if a.minutes else _first(root / "preclean" / "hcpcs_time", "hcpcs_minutes*.csv")
    from .pi_facts import billing_facts
    facts = billing_facts(a.spending, npis, minutes_csv=minutes)
    print(f"[capacity] billing facts for {len(facts):,} NPIs (minutes map {minutes.name if minutes else 'MISSING'})")

    counts = clinician_counts(npis, pdim, n2o, edges)
    beds = bed_counts(npis, pos_cap, ccn_xw)
    lic_p = Path(a.licensed_capacity) if a.licensed_capacity else root / "processed" / "licensed_capacity_by_npi.parquet"
    lic = pd.read_parquet(lic_p) if lic_p.exists() else None
    beds, bed_source = merge_licensed(beds, lic)
    print(f"[capacity] licensed capacity (state rosters) {'ok: ' + format(len(lic), ',') + ' NPIs' if lic is not None else 'MISSING'}; "
          f"beds known for {int(beds.notna().sum()):,} NPIs")
    sites = site_counts(npis, pl)
    t = capacity_table(facts, counts, beds, sites, names, bed_source)
    t.to_csv(out / "capacity.csv", index=False)
    inputs = {"spending": Path(a.spending).name, "universe": f"{len(npis):,} NPIs",
              "provider_dim": "yes" if pdim is not None else "no", "reassignment": "yes" if edges is not None else "no",
              "pos_beds": "yes" if pos_cap is not None else "no", "practice_locations": "yes" if pl is not None else "no",
              "licensed_capacity": f"{len(lic):,} NPIs" if lic is not None else "no",
              "minutes": minutes.name if minutes else "no"}
    (out / "CAPACITY.md").write_text(to_markdown(t, inputs), encoding="utf-8")
    (out / "capacity_summary.json").write_text(json.dumps({
        "rows": int(len(t)), "flagged": int((t["flags"] != "").sum()) if len(t) else 0,
        "by_flag": {k: int(t["flags"].str.contains(k).sum()) for k in
                    ("impossible_hours", "long_hours", "patients_per_clinician", "occupancy_over_100")} if len(t) else {}},
        indent=2), encoding="utf-8")
    print(f"[capacity] {len(t):,} rows, {(t['flags'] != '').sum() if len(t) else 0:,} flagged -> {out / 'CAPACITY.md'}")


if __name__ == "__main__":
    main()
