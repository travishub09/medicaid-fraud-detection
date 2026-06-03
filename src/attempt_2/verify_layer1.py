"""
verify_layer1.py  (attempt 2) — verify "billed-while-excluded" leads into cases

The detection step flagged providers as billed_after_exclusion using a MONTH-level
">= exclusion month" test and WITHOUT waiver/reinstatement data, so those leads
aren't yet defensible. This step re-derives the classification correctly:

  * restores REINDATE / WAIVERDATE / WVRSTATE from the RAW LEIE (Caught.csv),
    because the cleaned exclusions table dropped them;
  * builds per-NPI exclusion INTERVALS (a provider may have multiple LEIE records);
  * classifies each claim month with strict-month logic, keeping month-granularity
    ambiguity explicit (same-month == AMBIGUOUS, never silently "after");
  * assembles a factual per-lead dossier.

Output is candidate LEADS for legal review — dispositions, never a "fraud"/
"violation" determination. Read-only on every input. Idempotent; assertions stop.

Run:
    python -m src.attempt_2.verify_layer1
"""

import argparse
from pathlib import Path

import duckdb
import pandas as pd

from .clean_data import PRECLEAN_DIR, canonicalize_series, _normalize_name

# Exclusion-type families that are program-fraud / false-claims / kickback related.
# CONTEXT ONLY — never used to include/exclude a lead. Normalized (lower, alnum-only).
FRAUD_EXCLTYPES = {"1128a1",  # conviction of program-related crimes
                   "1128a3",  # felony conviction relating to health care fraud
                   "1128b1",  # misdemeanor conviction relating to health care fraud
                   "1128b2",  # conviction relating to fraud (other programs)
                   "1128b6",  # claims for excessive charges / unnecessary services
                   "1128b7"}  # fraud, kickbacks, and other prohibited activities


def log(m: str) -> None:
    print(m, flush=True)


def require(name: str, ok: bool, detail: str = "") -> None:
    log(f"    [assert {'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        raise AssertionError(f"ASSERTION FAILED: {name} — {detail}")


def _norm_excltype(s: str) -> str:
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def _ymd_month(s) -> str | None:
    """'YYYYMMDD' → 'YYYY-MM'; blanks / 0 / 00000000 → None."""
    s = str(s or "").strip()
    if s in ("", "0", "00000000") or len(s) < 6 or not s[:6].isdigit():
        return None
    return f"{s[:4]}-{s[4:6]}"


def classify_month(month: str, recs: list[dict]) -> str:
    """Most-severe status of a claim month across all of a provider's LEIE records.
    Precedence: clean_after > same_month > waivered > reinstated > before."""
    statuses = set()
    for r in recs:
        em = r["excl_month"]
        if em is None:
            continue
        if month < em:
            statuses.add("before")
        elif month == em:
            statuses.add("same_month")            # day-level unknown ⇒ ambiguous
        else:                                      # strictly after the exclusion month
            rm, wm = r["rein_month"], r["waiver_month"]
            if rm is not None and month >= rm:
                statuses.add("reinstated")
            elif wm is not None and month >= wm:
                statuses.add("waivered")
            else:
                statuses.add("clean_after")
    for s in ("clean_after", "same_month", "waivered", "reinstated", "before"):
        if s in statuses:
            return s
    return "before"


def name_match(nppes: str, leie: str) -> str:
    a, b = (nppes or "").strip(), (leie or "").strip()
    if not a or not b:
        return "unknown"
    if a == b:
        return "exact"
    ta, tb = set(a.split()), set(b.split())
    return "partial" if (ta & tb) else "mismatch"


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    data = PRECLEAN_DIR.parent
    feat = data / "features"
    pf = feat / "provider_features.parquet"
    base = feat / "spending_provider_base.parquet"
    for p in (pf, base):
        if not p.exists():
            raise FileNotFoundError(f"required input missing: {p}")
    raw = next((d / "Caught.csv" for d in [data / "raw", data, data / "preclean"]
                if (d / "Caught.csv").exists()), None)
    if raw is None:
        # last resort: recursive search under ~/Desktop/data
        hits = list(data.rglob("Caught.csv"))
        raw = hits[0] if hits else None
    if raw is None:
        raise FileNotFoundError(
            "Raw LEIE 'Caught.csv' not found under ~/Desktop/data/raw/ or ~/Desktop/data/. "
            "It is required (cleaned exclusions dropped waiver/reinstatement) — STOPPING.")
    out_dir = data / "detection"
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"Inputs: features={feat}  raw_leie={raw}")

    con = duckdb.connect()

    # ---- the 578 LEIE-matched billing NPIs + their NPPES context ----
    ctx = con.execute(f"""
        SELECT npi, org_legal_name, entity_type, primary_taxonomy, practice_state
        FROM read_parquet('{pf}') WHERE provider_on_leie
    """).df()
    ctx["npi"] = ctx["npi"].astype(str)
    leie_npis = set(ctx["npi"])
    require("matched_npis_unique", ctx["npi"].is_unique, f"{len(ctx):,}")

    # ---- Step 1: restore waiver + reinstatement from RAW LEIE; build intervals ----
    log("Step 1: restoring waiver/reinstatement from raw LEIE; building intervals …")
    rawdf = con.execute(f"SELECT * FROM read_csv_auto('{raw}', all_varchar=true)").df()
    rawdf["npi_canon"] = canonicalize_series(rawdf["NPI"])
    rec = rawdf[rawdf["npi_canon"].isin(leie_npis)].copy()
    for col in ["LASTNAME", "FIRSTNAME", "BUSNAME", "EXCLTYPE", "WVRSTATE"]:
        rec[col] = rec[col].fillna("").astype(str).str.strip()
    rec["excl_month"] = rec["EXCLDATE"].map(_ymd_month)
    rec["rein_month"] = rec["REINDATE"].map(_ymd_month)
    wvd = rec["WAIVERDATE"].map(_ymd_month)
    rec["waiver_month"] = [wd if wd is not None else (em if ws else None)
                           for wd, ws, em in zip(wvd, rec["WVRSTATE"] != "", rec["excl_month"])]
    rec["leie_name"] = rec.apply(
        lambda r: (r["LASTNAME"] + ", " + r["FIRSTNAME"]).strip(", ")
        if (r["LASTNAME"] or r["FIRSTNAME"]) else r["BUSNAME"], axis=1)

    # collapse multiple records → one row per NPI (intervals + carried fields) BEFORE any claims join
    records_by_npi: dict[str, list[dict]] = {}
    for npi, g in rec.groupby("npi_canon"):
        records_by_npi[str(npi)] = g[["excl_month", "rein_month", "waiver_month",
                                      "WVRSTATE", "EXCLTYPE", "leie_name"]].to_dict("records")

    # ---- Step 2: claim months (and per-hcpcs paid) for the matched NPIs ----
    log("Step 2: pulling claim months for matched NPIs …")
    con.register("leie_npis", pd.DataFrame({"npi": sorted(leie_npis)}))
    pm = con.execute(f"""
        SELECT b.billing_npi AS npi, b.service_month,
               SUM(b.total_paid) AS paid, SUM(b.total_claim_lines) AS claim_lines
        FROM read_parquet('{base}') b
        WHERE b.billing_npi IN (SELECT npi FROM leie_npis)
        GROUP BY 1, 2
    """).df()
    pm["npi"] = pm["npi"].astype(str)

    # ---- Step 3 + 4: classify each claim month, roll up per NPI ----
    log("Step 3-4: classifying claim months + per-NPI disposition …")
    pm["status"] = [classify_month(m, records_by_npi.get(npi, []))
                    for npi, m in zip(pm["npi"], pm["service_month"])]

    rows = []
    clean_after_pairs = []
    for npi in sorted(leie_npis):
        recs = records_by_npi.get(npi, [])
        sub = pm[pm["npi"] == npi]
        def paid_where(st):
            return float(sub.loc[sub["status"] == st, "paid"].fillna(0).sum())
        def lines_where(st):
            return float(sub.loc[sub["status"] == st, "claim_lines"].fillna(0).sum())
        ca = sub[sub["status"] == "clean_after"]
        clean_after_paid = float(ca["paid"].fillna(0).sum())
        same_paid = paid_where("same_month")
        legit_paid = paid_where("reinstated") + paid_where("waivered")
        before_paid = paid_where("before")
        if clean_after_paid > 0:
            disp = "QUALIFIED"
        elif same_paid > 0:
            disp = "AMBIGUOUS"
        elif legit_paid > 0:
            disp = "DISQUALIFIED"
        else:
            disp = "CONTEXT_ONLY"
        ca_months = sorted(ca.loc[ca["paid"].fillna(0) > 0, "service_month"])
        for m in ca_months:
            clean_after_pairs.append((npi, m))
        excltypes = [r["EXCLTYPE"] for r in recs if r["EXCLTYPE"]]
        rows.append({
            "npi": npi,
            "disposition": disp,
            "paid_after": round(clean_after_paid, 2),
            "n_clean_after_months": len(ca_months),
            "claim_lines_after": round(lines_where("clean_after"), 0),
            "same_month_paid": round(same_paid, 2),
            "reinstated_or_waivered_paid": round(legit_paid, 2),
            "billed_before_paid": round(before_paid, 2),
            "first_clean_after_month": ca_months[0] if ca_months else None,
            "last_clean_after_month": ca_months[-1] if ca_months else None,
            "excl_months": "; ".join(sorted({r["excl_month"] for r in recs if r["excl_month"]})),
            "reindates": "; ".join(sorted({r["rein_month"] for r in recs if r["rein_month"]})) or None,
            "waiver_states": "; ".join(sorted({r["WVRSTATE"] for r in recs if r["WVRSTATE"]})) or None,
            "n_leie_records": len(recs),
            "excltype": "; ".join(excltypes),
            "fraud_related_excltype": any(_norm_excltype(e) in FRAUD_EXCLTYPES for e in excltypes),
            "leie_name": next((r["leie_name"] for r in recs if r["leie_name"]), ""),
        })
    cases = pd.DataFrame(rows)
    require("one_row_per_matched_npi",
            len(cases) == len(leie_npis) and cases["npi"].is_unique,
            f"{len(cases):,} vs {len(leie_npis):,}")

    # ---- top HCPCS billed in clean_after months (per NPI) ----
    if clean_after_pairs:
        con.register("ca_pairs", pd.DataFrame(clean_after_pairs, columns=["npi", "service_month"]))
        top = con.execute(f"""
            WITH ca AS (
                SELECT b.billing_npi AS npi, b.hcpcs_code, SUM(b.total_paid) AS paid
                FROM read_parquet('{base}') b
                JOIN ca_pairs p ON b.billing_npi = p.npi AND b.service_month = p.service_month
                GROUP BY 1, 2)
            SELECT npi, string_agg(hcpcs_code || '($' || CAST(round(paid,0) AS BIGINT) || ')', '; '
                          ORDER BY paid DESC) AS top_hcpcs_after
            FROM (SELECT npi, hcpcs_code, paid,
                         row_number() OVER (PARTITION BY npi ORDER BY paid DESC) rn FROM ca)
            WHERE rn <= 5 GROUP BY npi
        """).df()
        top["npi"] = top["npi"].astype(str)
        cases = cases.merge(top, on="npi", how="left")
        require("no_fanout_after_tophcpcs_join", len(cases) == len(leie_npis))
    else:
        cases["top_hcpcs_after"] = None

    # ---- Step 5: identity sanity check (NPI match authoritative; flag discrepancies) ----
    cases = cases.merge(ctx, on="npi", how="left")
    require("no_fanout_after_ctx_join", len(cases) == len(leie_npis))
    # NPPES name available in provider_features is the legal business name (orgs);
    # individuals have it blank there → name_match = 'unknown' (NPI match is authoritative).
    nppes_disp = cases["org_legal_name"].fillna("")
    nppes_key = _normalize_name(nppes_disp)
    leie_key = _normalize_name(cases["leie_name"])
    cases["nppes_name"] = nppes_disp
    cases["name_match"] = [name_match(a, b) for a, b in zip(nppes_key, leie_key)]

    # ---- write outputs ----
    out_cols = ["npi", "disposition", "paid_after", "n_clean_after_months", "claim_lines_after",
                "first_clean_after_month", "last_clean_after_month", "top_hcpcs_after",
                "same_month_paid", "reinstated_or_waivered_paid", "billed_before_paid",
                "excl_months", "reindates", "waiver_states", "n_leie_records",
                "excltype", "fraud_related_excltype", "name_match", "nppes_name", "leie_name",
                "org_legal_name", "entity_type", "primary_taxonomy", "practice_state"]
    cases = cases[out_cols].sort_values(
        ["disposition", "paid_after"], ascending=[True, False]).reset_index(drop=True)
    cases.to_parquet(out_dir / "layer1_candidate_cases.parquet", index=False)
    cases.to_csv(out_dir / "layer1_candidate_cases.csv", index=False)

    write_report(cases, out_dir, len(leie_npis))
    con.close()
    log("Done. Disposition counts:")
    log(cases["disposition"].value_counts().to_string())


def write_report(cases: pd.DataFrame, out_dir: Path, n_leie: int) -> None:
    log("Writing LAYER1_VERIFICATION_REPORT.md …")
    r = ["# LAYER1_VERIFICATION_REPORT — attempt_2\n",
         "_Candidate leads for legal review. NPI match is authoritative; dispositions are computed "
         "with waiver + reinstatement + strict-month logic. These are NOT fraud/violation "
         "determinations. Read-only on all inputs._\n"]

    r.append(f"\n## Disposition counts (of {n_leie} LEIE-matched billing NPIs)\n| disposition | n |\n|---|--:|\n")
    for d, c in cases["disposition"].value_counts().items():
        r.append(f"| {d} | {c:,} |\n")

    # reconcile vs the prior month-level billed_after_exclusion flag, if available
    fl = out_dir / "fraud_leads.parquet"
    if fl.exists():
        con = duckdb.connect()
        prior = con.execute(
            f"SELECT npi, billed_after_exclusion FROM read_parquet('{fl}') "
            f"WHERE billed_after_exclusion").df()
        con.close()
        prior["npi"] = prior["npi"].astype(str)
        merged = cases.merge(prior, on="npi", how="inner")
        n_prior = len(prior)
        surv = (merged["disposition"] == "QUALIFIED").sum()
        amb = (merged["disposition"] == "AMBIGUOUS").sum()
        ko = (merged["disposition"] == "DISQUALIFIED").sum()
        ctxonly = (merged["disposition"] == "CONTEXT_ONLY").sum()
        r.append(f"\n## Reconciliation vs prior month-level `billed_after_exclusion` ({n_prior} leads)\n"
                 f"- survive as **QUALIFIED** (≥1 strictly-after clean month, positive paid): **{surv}**\n"
                 f"- reclassified **AMBIGUOUS** (only same-month billing — day-level unknown): **{amb}**\n"
                 f"- knocked out by waiver/reinstatement (**DISQUALIFIED**): **{ko}**\n"
                 f"- reclassified **CONTEXT_ONLY** (raw/authoritative exclusion date moves billing to "
                 f"pre-exclusion, or post-exclusion paid ≤ 0): **{ctxonly}**\n"
                 f"- (REINDATE/WAIVERDATE are absent in this LEIE download, so waiver/reinstatement "
                 f"knockouts ≈0; the real refinement is strict-after vs same-month and using the raw "
                 f"per-record exclusion dates. {surv}+{amb}+{ko}+{ctxonly} = {n_prior}.)\n")

    nm = cases.loc[cases["disposition"].isin(["QUALIFIED", "AMBIGUOUS"]), "name_match"].value_counts()
    r.append("\n## Identity sanity (QUALIFIED/AMBIGUOUS) — NPI authoritative, names flagged\n"
             + ", ".join(f"{k}={v}" for k, v in nm.items()) + "\n")

    q = cases[cases["disposition"] == "QUALIFIED"].copy()
    r.append(f"\n## QUALIFIED candidate dossiers ({len(q)}) — facts for review\n")
    if not len(q):
        r.append("_None._\n")
    for x in q.itertuples():
        flag = " ⚠️name_mismatch" if x.name_match == "mismatch" else ""
        r.append(
            f"\n### NPI {x.npi} — {x.nppes_name or '(no NPPES name)'} ({x.practice_state}){flag}\n"
            f"- LEIE entity name: {x.leie_name or '(none)'}  |  name_match: {x.name_match}\n"
            f"- exclusion: {x.excl_months}  (type {x.excltype}; fraud-related EXCLTYPE: {x.fraud_related_excltype})"
            + (f"; reinstated {x.reindates}" if x.reindates else "")
            + (f"; waiver state {x.waiver_states}" if x.waiver_states else "") + "\n"
            f"- billed AFTER exclusion (strict): **${x.paid_after:,.0f}** across "
            f"**{x.n_clean_after_months}** month(s) ({x.first_clean_after_month}…{x.last_clean_after_month}), "
            f"{x.claim_lines_after:,.0f} claim lines\n"
            f"- top HCPCS after exclusion: {x.top_hcpcs_after or '(n/a)'}\n"
            f"- taxonomy {x.primary_taxonomy}; entity_type {x.entity_type}\n")

    r.append("\n## Integrity\n"
             f"- layer1_candidate_cases.parquet: {len(cases):,} rows = one per LEIE-matched billing NPI ✓\n"
             "- multiple LEIE records collapsed to per-NPI intervals BEFORE the claims join; "
             "joins asserted non-fan-out.\n"
             "- same-month billing preserved as AMBIGUOUS (never silently 'after').\n"
             "- No input files modified.\n")
    (out_dir / "LAYER1_VERIFICATION_REPORT.md").write_text("".join(r))


if __name__ == "__main__":
    main()
