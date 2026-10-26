"""
pi_facts.py — the billed-reality fact sheet behind each pursuit target.

A field investigator catches a fraudulent practice in a lie by holding a
NUMBER the practice cannot explain: the front desk says 25 visits a day
while the billing implies 71 hours of face time; the "facility" bills
per-diem patient days with six recorded beds; weekend staffing is one car
in the lot while weekend billing matches a Tuesday. This module extracts,
for a list of target organizations, every such quantified fact the run
matrix holds — the operator-side half of each contradiction — so dossiers
ship with specifics instead of percentiles.

For each member NPI of each target org it emits the raw value + peer
percentile (when present) of the fact columns below, plus org rollups.
Output: pi_facts.json (structured, for dossier generation) and
PI_FACTS.md (human-readable).

  python -m src.model_a.pi_facts --run-dir <run_folder> \
      --orgs "org:pac:123;org:name:FOO" --out-prefix pi_facts
  python -m src.model_a.pi_facts --run-dir @champion --orgs-file targets.txt
"""

from __future__ import annotations

import argparse
import re
import json
from pathlib import Path

import pandas as pd

# fact column -> (plain-English label, how the PI uses it in the field)
FACT_COLS: dict[str, tuple[str, str]] = {
    "time_minutes_per_day": (
        "billed face-time minutes per working day",
        "Ask how many patients a day the practice sees; 480 min is a full "
        "8-hour day of pure face time — values near or above it are the "
        "impossible-day contradiction."),
    "mue_violation_share": (
        "share of billing units above CMS per-day maximums",
        "Units the payer's own edits call unbillable; ask what a typical "
        "visit involves and compare to the units billed."),
    "pos_bed_count": (
        "beds on record for this location",
        "Count beds implied by per-diem/inpatient billing vs this number; "
        "ask where patients sleep."),
    "pbj_understaffing": (
        "staffing shortfall vs expected hours (payroll-based)",
        "Cars in the lot at shift change vs the staffing the billing "
        "implies."),
    "pbj_weekend_understaffing": (
        "weekend staffing gap (payroll-based)",
        "Saturday lot check: weekend billing at weekday levels with a "
        "skeleton crew is the contradiction."),
    "hospice_gip_share": (
        "share of hospice days billed at general-inpatient rate",
        "GIP requires inpatient-level care; ask where GIP patients are "
        "housed and verify the unit exists."),
    "hospice_live_discharge_rate": (
        "share of hospice patients discharged alive",
        "High live-discharge suggests enrolling patients who are not "
        "dying; ask about typical length of stay."),
    "hospice_nf_care_share": (
        "share of hospice care delivered in nursing facilities",
        "Nursing-home-based hospice concentration; verify the facility "
        "relationships."),
    "billing_after_deactivation": (
        "billing activity after an NPI deactivation date",
        "Ask who currently works there; billing under departed or "
        "deactivated identities is per-se false."),
    "market_saturation_index": (
        "county market saturation for this provider type",
        "Count similar agencies in the same building/plaza; shared suites "
        "and registered agents are the cluster tell."),
    "growth_level_shift": (
        "abrupt level-shift in billing volume",
        "Ask when this location opened / changed hands; compare to when "
        "the billing actually ramped."),
    "new_code_burst": (
        "sudden adoption of new billing codes",
        "Ask what new services launched and when; verify equipment/staff "
        "for them exist."),
    "em_high_level_share": (
        "share of visits billed at the highest complexity levels",
        "Ask how long a typical appointment runs; top-level codes imply "
        "40+ minute encounters."),
    "services_per_bene": (
        "services billed per patient",
        "Ask how often a typical patient is seen; compare."),
    "dme_high_cost_item_share": (
        "share of DME dollars in high-cost items",
        "Verify a storefront with actual inventory exists; ask what they "
        "stock."),
    "opioid_claim_share": (
        "share of prescriptions that are opioids",
        "Waiting-room observation; pharmacies nearby will know the "
        "prescriber."),
    "clinical_implausibility": (
        "clinically implausible service combinations",
        "Ask what conditions they treat; the billed combinations imply "
        "capabilities to verify on site."),
    "capacity_mismatch": (
        "billed volume vs physical capacity",
        "Square footage / rooms / chairs vs billed throughput."),
    "net_paid": (
        "Medicaid dollars paid (window total)",
        "The scale anchor for every other question."),
}


def _sql_str(p) -> str:
    return str(p).replace("'", "''")


def billing_facts(spending_path: str | Path, npis: list[str],
                  minutes_csv: str | Path | None = None,
                  top_codes: int = 8, work_days_per_month: int = 22,
                  con=None) -> dict[str, dict]:
    """The facts a PI can actually put to a witness, from the BILLING FACT
    itself (processed/spending_fact.parquet), per billing NPI:

      paid_by_year      the ramp: when the money started and how fast it grew
      ramp_month        first month the run-rate crossed half of the peak month
      top_codes         what the money is: code, paid, share, claim lines,
                        patient-months (a witness can be asked what each is)
      servicing_npis    distinct rendering clinicians under this billing NPI
                        (48M through one biller and three clinicians is a question)
      patient_months    sum of monthly patient counts; lines_per_patient_month
      implied_hours     with a hcpcs->minutes map: claim lines x minutes, per
                        working day, in the worst month and at the median
                        (the "31 hours a day" contradiction), plus the share
                        of paid dollars that sits on timed codes

    Everything is computed only for the NPIs asked for (a WHERE IN over the
    parquet), so a 238M-row fact answers in minutes, not hours. Values are
    reported as billed; a fact is a question for the field, not a finding."""
    import duckdb
    own = con is None
    con = con or duckdb.connect()
    p = _sql_str(spending_path)
    npis = [str(n) for n in npis if str(n).strip()]
    if not npis:
        return {}
    con.execute("CREATE OR REPLACE TEMP TABLE want(npi VARCHAR)")
    con.executemany("INSERT INTO want VALUES (?)", [(n,) for n in npis])
    cols = [c[0] for c in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()]
    lines_sel = ("CAST(total_claim_lines AS DOUBLE)" if "total_claim_lines" in cols
                 else "NULL")
    pats_sel = ("CAST(total_patients AS DOUBLE)" if "total_patients" in cols
                else "NULL")
    srv_sel = ("CAST(servicing_npi AS VARCHAR)" if "servicing_npi" in cols
               else "NULL")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE s AS
        SELECT CAST(billing_npi AS VARCHAR) npi,
               UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) hcpcs,
               substr(CAST(service_month AS VARCHAR), 1, 7) ym,
               CAST(total_paid AS DOUBLE) paid,
               {lines_sel} AS lines, {pats_sel} AS pats, {srv_sel} AS srv
        FROM read_parquet('{p}')
        WHERE CAST(billing_npi AS VARCHAR) IN (SELECT npi FROM want)
    """)
    by_year = con.execute("""
        SELECT npi, substr(ym, 1, 4) yr, SUM(paid) paid, SUM(lines) lines
        FROM s GROUP BY npi, yr ORDER BY npi, yr""").df()
    by_month = con.execute("""
        SELECT npi, ym, SUM(paid) paid FROM s GROUP BY npi, ym ORDER BY npi, ym
    """).df()
    top = con.execute(f"""
        SELECT * FROM (
            SELECT npi, hcpcs, SUM(paid) paid, SUM(lines) lines, SUM(pats) pats,
                   ROW_NUMBER() OVER (PARTITION BY npi ORDER BY SUM(paid) DESC) rn
            FROM s GROUP BY npi, hcpcs)
        WHERE rn <= {int(top_codes)} ORDER BY npi, rn""").df()
    tot = con.execute("""
        SELECT npi, SUM(paid) paid, SUM(lines) lines, SUM(pats) patient_months,
               COUNT(DISTINCT hcpcs) n_codes, COUNT(DISTINCT ym) n_months,
               COUNT(DISTINCT srv) n_servicing, MIN(ym) first_month, MAX(ym) last_month
        FROM s GROUP BY npi""").df()

    # the rendering NPIs by dollars (who the claims say rendered) and, per top
    # code, the worst / median month in patients and in lines per calendar day
    # (for a per-diem code, lines per day = residents per day: the occupancy
    # ratio against licensed places; for a visit code, patients that month)
    renderers = con.execute("""
        SELECT * FROM (
            SELECT npi, srv, SUM(paid) AS paid, COUNT(DISTINCT ym) AS n_months,
                   ROW_NUMBER() OVER (PARTITION BY npi ORDER BY SUM(paid) DESC) AS rn
            FROM s WHERE srv IS NOT NULL GROUP BY npi, srv)
        WHERE rn <= 6 ORDER BY npi, rn""").df()
    code_months = con.execute("""
        SELECT s.npi, s.hcpcs, s.ym, SUM(s.pats) AS pats, SUM(s.lines) AS lines,
               SUM(s.lines) / day(last_day(CAST(s.ym || '-01' AS DATE))) AS lines_per_day
        FROM s JOIN (SELECT npi, hcpcs FROM (
                SELECT npi, hcpcs, ROW_NUMBER() OVER (PARTITION BY npi ORDER BY SUM(paid) DESC) AS rn
                FROM s GROUP BY npi, hcpcs) WHERE rn <= 3) AS t
          ON s.npi = t.npi AND s.hcpcs = t.hcpcs
        GROUP BY s.npi, s.hcpcs, s.ym""").df()

    hours = {}
    mins_p = Path(minutes_csv) if minutes_csv else None
    if mins_p and mins_p.exists():
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE mins AS
            SELECT UPPER(TRIM(CAST("hcpcs" AS VARCHAR))) AS hcpcs,
                   CAST("minutes" AS DOUBLE) AS mins_per_line
            FROM read_csv_auto('{_sql_str(mins_p)}', header=true)""")
        hm = con.execute(f"""
            SELECT s.npi, s.ym,
                   SUM(s.lines * m.mins_per_line) / 60.0 / {int(work_days_per_month)} AS hours_per_day,
                   SUM(s.paid) AS timed_paid
            FROM s JOIN mins m ON s.hcpcs = m.hcpcs
            GROUP BY s.npi, s.ym""").df()
        for npi, g in hm.groupby("npi"):
            g = g.dropna(subset=["hours_per_day"])
            if not len(g):
                continue
            worst = g.loc[g["hours_per_day"].idxmax()]
            hours[npi] = {
                "worst_month": str(worst["ym"]),
                "worst_hours_per_working_day": float(worst["hours_per_day"]),
                "median_hours_per_working_day": float(g["hours_per_day"].median()),
                "months_over_24h": int((g["hours_per_day"] > 24).sum()),
                "timed_paid": float(g["timed_paid"].sum()),
            }
    if own:
        con.close()

    out: dict[str, dict] = {}
    for _, t in tot.iterrows():
        npi = str(t["npi"])
        yrs = by_year[by_year["npi"] == npi]
        months = by_month[by_month["npi"] == npi]
        ramp = None
        if len(months):
            peak = float(months["paid"].max())
            if peak > 0:
                crossed = months[months["paid"] >= 0.5 * peak]
                ramp = {"month": str(crossed["ym"].iloc[0]),
                        "peak_month": str(months.loc[months["paid"].idxmax(), "ym"]),
                        "peak_month_paid": peak}
        tc = top[top["npi"] == npi]
        paid_total = float(t["paid"] or 0)
        pm = float(t["patient_months"]) if pd.notna(t["patient_months"]) else None
        lines_total = float(t["lines"]) if pd.notna(t["lines"]) else None
        rec = {
            "paid_total": paid_total,
            "paid_by_year": {str(r["yr"]): float(r["paid"]) for _, r in yrs.iterrows()},
            "ramp": ramp,
            "first_month": str(t["first_month"]), "last_month": str(t["last_month"]),
            "active_months": int(t["n_months"]),
            "n_codes": int(t["n_codes"]),
            "n_servicing_npis": int(t["n_servicing"]) if pd.notna(t["n_servicing"]) else None,
            "patient_months": pm,
            "lines_total": lines_total,
            "lines_per_patient_month": (lines_total / pm if pm and lines_total else None),
            "top_codes": [{
                "hcpcs": str(r["hcpcs"]), "paid": float(r["paid"]),
                "share": (float(r["paid"]) / paid_total if paid_total else None),
                "lines": (float(r["lines"]) if pd.notna(r["lines"]) else None),
                "patient_months": (float(r["pats"]) if pd.notna(r["pats"]) else None),
            } for _, r in tc.iterrows()],
        }
        rd = renderers[renderers["npi"] == npi]
        if len(rd):
            rec["renderers"] = [{"npi": str(r["srv"]), "paid": float(r["paid"]),
                                 "share": (float(r["paid"]) / paid_total if paid_total else None),
                                 "months": int(r["n_months"])} for _, r in rd.iterrows()]
        cm = code_months[code_months["npi"] == npi]
        if len(cm):
            per_code = {}
            for code, g in cm.groupby("hcpcs"):
                g = g.dropna(subset=["lines_per_day"])
                if not len(g):
                    continue
                w = g.loc[g["lines_per_day"].idxmax()]
                entry = {"worst_month": str(w["ym"]),
                         "worst_lines_per_day": float(w["lines_per_day"]),
                         "median_lines_per_day": float(g["lines_per_day"].median())}
                gp = g.dropna(subset=["pats"])
                if len(gp):
                    wp = gp.loc[gp["pats"].idxmax()]
                    entry.update({"worst_patients_month": str(wp["ym"]),
                                  "worst_patients": float(wp["pats"]),
                                  "median_patients": float(gp["pats"].median())})
                per_code[str(code)] = entry
            for c in rec["top_codes"]:
                if c["hcpcs"] in per_code:
                    c["by_month"] = per_code[c["hcpcs"]]
        if npi in hours:
            h = dict(hours[npi])
            h["timed_share_of_paid"] = (h["timed_paid"] / paid_total
                                        if paid_total else None)
            rec["implied_hours"] = h
        out[npi] = rec
    return out


def extract(matrix: pd.DataFrame, org_ids: list[str]) -> dict:
    """Per-org, per-member fact sheets from the run matrix."""
    mkey = matrix["org_node_id"].astype(str)
    out: dict = {}
    for oid in org_ids:
        m = matrix.loc[mkey == str(oid)]
        if not len(m):
            out[str(oid)] = {"error": "org not found in matrix"}
            continue
        members = []
        for _, r in m.iterrows():
            facts = {}
            for col, (label, use) in FACT_COLS.items():
                val = r.get(col)
                pct = r.get(f"{col}__peerpct")
                val = None if pd.isna(val) else float(val)
                pct = None if pct is None or pd.isna(pct) else float(pct)
                if val is not None or pct is not None:
                    facts[col] = {"label": label, "value": val,
                                  "peer_percentile": pct, "field_use": use}
            members.append({
                "npi": str(r.get("npi", "")),
                "name": str(r.get("provider_name", "") or ""),
                "city": str(r.get("addr_city", "") or ""),
                "state": str(r.get("practice_state", "") or ""),
                "facts": facts,
            })
        paid = pd.to_numeric(m.get("net_paid"), errors="coerce").fillna(0)
        beds = pd.to_numeric(m.get("pos_bed_count"), errors="coerce").fillna(0) \
            if "pos_bed_count" in m.columns else pd.Series(dtype=float)
        out[str(oid)] = {
            "org_name": str(m.iloc[0].get("org_legal_name", "") or
                            m.iloc[0].get("provider_name", "") or ""),
            "n_members": int(len(m)),
            "total_net_paid": float(paid.sum()),
            "total_beds_recorded": float(beds.sum()) if len(beds) else None,
            "members": members,
        }
    return out


def attach_billing_facts(res: dict, spending_path: str | Path,
                         minutes_csv: str | Path | None = None,
                         log=print) -> dict:
    """Add billing_facts (from the spending fact) to every member in ``res``."""
    npis = [m["npi"] for o in res.values() if "error" not in o
            for m in o["members"] if m.get("npi")]
    log(f"[pi_facts] billing facts for {len(npis):,} NPIs from {spending_path}"
        + (f" (+ minutes map {Path(minutes_csv).name})" if minutes_csv else
           " (no hcpcs minutes map: implied hours skipped)"))
    bf = billing_facts(spending_path, npis, minutes_csv=minutes_csv)
    for o in res.values():
        if "error" in o:
            continue
        for m in o["members"]:
            if m["npi"] in bf:
                m["billing_facts"] = bf[m["npi"]]
    return res


def _org_level_split(members: list[dict]) -> tuple[dict, set]:
    """Facts whose value is identical for every member are ORG-GRAIN scores
    broadcast down to NPIs (growth level-shift, clinical implausibility...);
    printing them 13 times as if each clinician earned them misleads the
    reader. Returns (org_level facts, the column names to skip per member)."""
    if len(members) < 2:
        return {}, set()
    org, skip = {}, set()
    cols = set().union(*(m["facts"].keys() for m in members))
    for c in cols:
        vals = {(m["facts"].get(c) or {}).get("value") for m in members}
        if len(vals) == 1 and None not in vals and all(c in m["facts"] for m in members):
            org[c] = members[0]["facts"][c]
            skip.add(c)
    return org, skip


def _money(v) -> str:
    return f"${v:,.0f}" if v is not None else "—"


def _billing_lines(bf: dict) -> list[str]:
    L = []
    yrs = bf.get("paid_by_year") or {}
    if yrs:
        L.append("- **paid by year**: " + " -> ".join(
            f"{y} {_money(v)}" for y, v in sorted(yrs.items())))
    r = bf.get("ramp")
    if r:
        L.append(f"- **ramp**: run-rate crossed half its peak in {r['month']}; "
                 f"peak month {r['peak_month']} at {_money(r['peak_month_paid'])}")
        L.append("  - field use: ask when this location opened, changed hands, "
                 "or added staff; the month the money started is the month to "
                 "reconcile against leases, licenses and payroll.")
    ns = bf.get("n_servicing_npis")
    if ns is not None:
        L.append(f"- **rendering clinicians under this billing NPI**: {ns} "
                 f"(across {bf.get('active_months')} active months, "
                 f"{bf.get('n_codes')} distinct codes)")
        L.append("  - field use: count the clinicians on site; the billing "
                 "says how many rendered.")
        rd = bf.get("renderers") or []
        if rd:
            L.append("  - by dollars: " + "; ".join(
                f"{r['npi']} {_money(r['paid'])}"
                + (f" ({r['share']:.0%}, {r['months']} mo)" if r.get("share") is not None else "")
                for r in rd))
            L.append("  - field use: the rendering NPIs the claims name, with their share; "
                     "a clinician seen on site who is not on this list did their work "
                     "under someone else's number.")
    pm = bf.get("patient_months")
    if pm:
        lpp = bf.get("lines_per_patient_month")
        L.append(f"- **patient-months billed**: {pm:,.0f}"
                 + (f"; claim lines per patient-month {lpp:,.1f}" if lpp else ""))
        L.append("  - field use: ask how many patients a day and how many "
                 "services each gets; compare.")
    tc = bf.get("top_codes") or []
    if tc:
        L.append("- **what the money is (top codes)**:")
        for c in tc:
            bits = [f"{c['hcpcs']} {_money(c['paid'])}"]
            if c.get("share") is not None:
                bits.append(f"{c['share']:.0%} of paid")
            if c.get("lines") is not None:
                bits.append(f"{c['lines']:,.0f} lines")
            if c.get("patient_months") is not None:
                bits.append(f"{c['patient_months']:,.0f} patient-months")
            L.append("  - " + ", ".join(bits))
            bm = c.get("by_month")
            if bm:
                sub = (f"    - per month: worst {bm['worst_month']} = "
                       f"{bm['worst_lines_per_day']:,.1f} lines per calendar day"
                       f" (median {bm['median_lines_per_day']:,.1f})")
                if bm.get("worst_patients") is not None:
                    sub += (f"; patients worst {bm['worst_patients']:,.0f} "
                            f"({bm['worst_patients_month']}), median {bm['median_patients']:,.0f}")
                L.append(sub)
        L.append("  - field use: for a per-diem code (residential, day program) lines "
                 "per calendar day is residents per day and patients per month is the "
                 "census: compare both to the licensed places; for a visit code, "
                 "patients per month against the clinicians on site.")
        L.append("  - field use: ask what each code is in their own words and "
                 "who performs it; the answer must match the license, the "
                 "equipment and the room.")
    h = bf.get("implied_hours")
    if h:
        L.append(f"- **implied clinician hours per working day (timed codes)**: "
                 f"worst month {h['worst_month']} = {h['worst_hours_per_working_day']:,.1f} h; "
                 f"median month {h['median_hours_per_working_day']:,.1f} h; "
                 f"{h['months_over_24h']} month(s) over 24 h"
                 + (f"; timed codes are {h['timed_share_of_paid']:.0%} of paid"
                    if h.get("timed_share_of_paid") is not None else ""))
        L.append("  - field use: hours per day above what the staff on site "
                 "could deliver is the contradiction no explanation survives; "
                 "ask how many clinicians and what shifts.")
    return L


def to_markdown(res: dict) -> str:
    L = ["# PI FACT SHEETS — billed-reality numbers per target", "",
         "_Each fact is the operator-side half of a field contradiction: "
         "what the billing claims, so the investigator knows which innocent "
         "answer is impossible. Model indicators (0 to 1) are from the run "
         "matrix; peer percentile 0.98 = higher than 98% of comparable "
         "providers. Billing facts are read straight from the paid-claims "
         "fact, as billed. Every number is a question for the field, not a "
         "finding; leads for counsel review, never accusations._", ""]
    for oid, o in res.items():
        if "error" in o:
            L.append(f"## {oid}: {o['error']}")
            continue
        L.append(f"## {o['org_name'] or oid}  ({oid})")
        L.append(f"- members: {o['n_members']} | Medicaid paid (window): "
                 f"${o['total_net_paid']:,.0f}"
                 + (f" | beds recorded: {o['total_beds_recorded']:.0f}"
                    if o.get("total_beds_recorded") else ""))
        org_facts, skip = _org_level_split(o["members"])
        if org_facts:
            L.append("- **organization-level indicators** (same value for every "
                     "member; scored at the org, not per clinician): " + "; ".join(
                         f"{f['label']} {f['value']:,.2f}" for f in org_facts.values()
                         if f.get("value") is not None))
        for mem in o["members"]:
            facts = {c: f for c, f in mem["facts"].items() if c not in skip}
            bf = mem.get("billing_facts")
            if not facts and not bf:
                continue
            who = f" {mem['name']}" if mem.get("name") and mem["name"] != o["org_name"] else ""
            L.append(f"### NPI {mem['npi']}{who} — {mem['city']}, {mem['state']}")
            if bf:
                L += _billing_lines(bf)
            for col, f in sorted(
                    facts.items(),
                    key=lambda kv: -(kv[1]["peer_percentile"] or 0)):
                v = f["value"]
                vtxt = (f"{v:,.2f}" if v is not None and abs(v) < 1e6
                        else (f"${v:,.0f}" if v is not None else "—"))
                p = f["peer_percentile"]
                ptxt = f" (peer pct {p:.2f})" if p is not None else ""
                L.append(f"- **{f['label']}**: {vtxt}{ptxt}")
                L.append(f"  - field use: {f['field_use']}")
        L.append("")
    return "\n".join(L) + "\n"


def window_sheet(res: dict, active_share: float = 0.25) -> tuple[str, list[dict]]:
    """The WHEN sheet for the field (2026-10-12, after Sage Intelligence's first
    report on PI packet v11: the investigator identified former employees per
    practice WITH their tenure windows and will interview them next). Per org,
    from the members' billing facts: the years the billing was active (a year with
    at least ``active_share`` of the peak year's dollars), the ramp month, the peak
    year, the last billed month. An interview is worth most when the former's
    tenure overlaps the active window; the sheet says which years those are, and
    names no person. Returns (markdown, rows)."""
    rows: list[dict] = []
    for oid, o in res.items():
        if "error" in o:
            continue
        by_year: dict[str, float] = {}
        ramps, firsts, lasts = [], [], []
        for m in o.get("members", []):
            bf = m.get("billing_facts")
            if not bf:
                continue
            for y, v in (bf.get("paid_by_year") or {}).items():
                by_year[str(y)] = by_year.get(str(y), 0.0) + float(v or 0)
            if bf.get("ramp") and bf["ramp"].get("month"):
                ramps.append(str(bf["ramp"]["month"]))
            if bf.get("first_month"):
                firsts.append(str(bf["first_month"]))
            if bf.get("last_month"):
                lasts.append(str(bf["last_month"]))
        if not by_year:
            continue
        peak_year = max(by_year, key=by_year.get)
        peak = by_year[peak_year]
        active = sorted(y for y, v in by_year.items() if peak > 0 and v >= active_share * peak)
        rows.append({
            "org_id": oid, "org_name": o.get("org_name") or oid,
            "active_years": active, "active_from": active[0] if active else None,
            "active_to": active[-1] if active else None,
            "ramp_month": min(ramps) if ramps else None,
            "peak_year": peak_year, "peak_year_paid": peak,
            "first_month": min(firsts) if firsts else None,
            "last_month": max(lasts) if lasts else None,
            "paid_by_year": by_year,
        })
    L = ["# PI WINDOW SHEET: when the billing was active, per practice", "",
         "_For the field investigator. A former employee's account is worth most when "
         "their time at the practice overlaps the years the billing pattern was active. "
         "Active years = years with at least a quarter of the peak year's Medicaid dollars; "
         "the ramp month is when the run-rate first crossed half its peak. Billing is as "
         "billed (Medicaid Provider Spending by HCPCS); names no person; leads for counsel "
         "review, never accusations._", "",
         "| practice | active years | ramp month | peak year (paid) | first billed | last billed | prioritize formers whose tenure overlaps |",
         "|---|---|---|---|---|---|---|"]
    for r in rows:
        yrs = ", ".join(r["active_years"]) if r["active_years"] else ""
        L.append(f"| {r['org_name']} | {yrs} | {r['ramp_month'] or ''} | {r['peak_year']} "
                 f"(${r['peak_year_paid']:,.0f}) | {r['first_month'] or ''} | {r['last_month'] or ''} | "
                 f"{r['active_from']} to {r['active_to']} |")
    if not rows:
        L.append("_no billing facts attached (run with --deep)_")
    return "\n".join(L) + "\n", rows


def _is_npi(tok: str) -> bool:
    return len(tok) == 10 and tok.isdigit()


def resolve_npis_to_orgs(matrix: pd.DataFrame, npis: list[str]) -> tuple[list[str], list[str]]:
    """NPIs -> the org_node_ids that carry them in the run matrix (first-seen
    order, de-duplicated) + the NPIs the matrix does not hold. The PI packet
    prints NPIs, pi_facts works per organization: this is the bridge."""
    key = matrix["npi"].astype(str)
    oid = matrix["org_node_id"].astype(str) if "org_node_id" in matrix.columns \
        else pd.Series([""] * len(matrix), index=matrix.index)
    lookup = dict(zip(key, oid))
    out: list[str] = []
    missing: list[str] = []
    for n in npis:
        o = lookup.get(str(n), "")
        if not o or o in ("nan", "None"):
            missing.append(str(n))
        elif o not in out:
            out.append(o)
    return out, missing


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True,
                    help="run folder (or '@champion')")
    ap.add_argument("--orgs", default=None,
                    help="semicolon-separated org_node_ids")
    ap.add_argument("--orgs-file", default=None,
                    help="file with one org_node_id per line (or a "
                         "priority-list CSV: its org_id column is used). A line "
                         "that is a bare 10-digit NPI is resolved to its org "
                         "through the run matrix (the PI packet lists NPIs)")
    ap.add_argument("--npis", default=None,
                    help="NPIs separated by ; , or space, resolved to their orgs "
                         "through the run matrix")
    ap.add_argument("--top", type=int, default=None,
                    help="with a priority-list CSV in --orgs-file: only the "
                         "first N rows")
    ap.add_argument("--out-prefix", default="pi_facts")
    ap.add_argument("--deep", action="store_true",
                    help="also read the paid-claims fact for each member NPI: "
                         "paid by year + ramp month, top codes with lines and "
                         "patient-months, rendering-clinician count, and (with a "
                         "hcpcs minutes map) implied clinician hours per working "
                         "day — the facts a PI can put to a witness")
    ap.add_argument("--spending", default=None,
                    help="processed/spending_fact.parquet (default: under "
                         "MEDICAID_DATA_ROOT or ~/Desktop/data)")
    ap.add_argument("--minutes", default=None,
                    help="hcpcs,minutes CSV for implied hours (default: the "
                         "first preclean/hcpcs_time/hcpcs_minutes*.csv found)")
    args = ap.parse_args()
    from .run_registry import resolve_run_dir
    run_dir = Path(resolve_run_dir(args.run_dir))

    org_ids: list[str] = []
    npis: list[str] = []
    if args.orgs:
        org_ids += [o.strip() for o in args.orgs.split(";") if o.strip()]
    if args.npis:
        npis += [n.strip() for n in re.split(r"[;,\s]+", args.npis) if n.strip()]
    if args.orgs_file:
        p = Path(args.orgs_file)
        if p.suffix.lower() == ".csv":
            df = pd.read_csv(p, dtype=str)
            col = "org_id" if "org_id" in df.columns else df.columns[0]
            ids = df[col].dropna().tolist()
            org_ids += ids[:args.top] if args.top else ids
        else:
            for ln in p.read_text().splitlines():
                ln = ln.strip().split("#", 1)[0].strip()
                if not ln:
                    continue
                (npis if _is_npi(ln) else org_ids).append(ln)
    if not org_ids and not npis:
        raise SystemExit("no orgs given — use --orgs, --npis or --orgs-file")

    matrix = pd.read_parquet(run_dir / "provider_features_for_model.parquet")
    if npis:
        resolved, missing = resolve_npis_to_orgs(matrix, npis)
        print(f"[pi_facts] {len(npis)} NPIs -> {len(resolved)} orgs"
              + (f"; {len(missing)} NPI(s) not in the matrix: "
                 + ", ".join(missing[:20]) + (" ..." if len(missing) > 20 else "")
                 if missing else ""))
        org_ids += [o for o in resolved if o not in org_ids]
    res = extract(matrix, org_ids)
    if args.deep:
        import os
        root = Path(os.environ.get("MEDICAID_DATA_ROOT",
                                   str(Path.home() / "Desktop/data")))
        spending = Path(args.spending) if args.spending else \
            root / "processed" / "spending_fact.parquet"
        if not spending.exists():
            raise SystemExit(f"--deep needs the paid-claims fact; not found at "
                             f"{spending} (pass --spending)")
        minutes = Path(args.minutes) if args.minutes else next(
            iter(sorted((root / "preclean" / "hcpcs_time").glob("hcpcs_minutes*.csv"))),
            None)
        res = attach_billing_facts(res, spending, minutes_csv=minutes)
    # --out-prefix may carry a directory part (an absolute path lands outside the run
    # folder: the operator's pi_reports/ tree, 2026-10-26); only the NAME is upper-cased
    # for the Markdown sheet, never the folders (an upper-cased path did not exist).
    _pp = Path(args.out_prefix)
    _pp.parent.mkdir(parents=True, exist_ok=True) if _pp.parent != Path(".") else None
    jp = run_dir / f"{args.out_prefix}.json"
    mp = run_dir / _pp.parent / f"{_pp.name.upper()}.md"
    jp.write_text(json.dumps(res, indent=1), encoding="utf-8")
    mp.write_text(to_markdown(res), encoding="utf-8")
    found = sum(1 for v in res.values() if "error" not in v)
    print(f"[pi_facts] {found}/{len(org_ids)} orgs -> {jp}")
    print(f"[pi_facts] fact sheets -> {mp}")
    if args.deep:
        wmd, wrows = window_sheet(res)
        wp = run_dir / f"{args.out_prefix.upper()}_WINDOWS.md"
        wp.write_text(wmd, encoding="utf-8")
        (run_dir / f"{args.out_prefix}_windows.json").write_text(json.dumps(wrows, indent=1), encoding="utf-8")
        print(f"[pi_facts] window sheet ({len(wrows)} practices) -> {wp}")


if __name__ == "__main__":
    main()
