"""
run3_preflight.py - dry-run the REAL adapters on samples of the REAL data.

Header checks (data_audit.py) prove the columns look right. This goes further:
it imports the actual pipeline code and runs every adapter's compute function on
a sample of each real file, so any parse, mapping, dtype, or logic failure shows
up NOW instead of three hours into run3.bat. Also sanity-checks the core fact
(negative dollars, corrupt rows, join rates).

Run (takes ~5-15 minutes):
    python "C:\\Users\\treyr\\Downloads\\run3_preflight.py" > "C:\\Users\\treyr\\Downloads\\RUN3_PREFLIGHT.txt"
Then upload RUN3_PREFLIGHT.txt.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

REPO = Path(r"C:\Users\treyr\medicaid-fraud-detection")
DATA = Path(r"C:\Users\treyr\OneDrive\Desktop\data")
sys.path.insert(0, str(REPO))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402

con = duckdb.connect()
RESULTS = []


def sample_csv(path, nrows=50_000):
    from src.attempt_2.clean_data import read_csv_text
    return read_csv_text(path, nrows=nrows)


def newest(*patterns):
    hits = []
    for p in patterns:
        hits += list(DATA.rglob(p))
    hits = [h for h in hits if h.is_file() and h.stat().st_size > 200]
    return max(hits, key=lambda h: h.stat().st_size) if hits else None


def run(label, fn):
    print(f"\n--- {label} ---")
    try:
        detail = fn()
        print(f"  PASS  {detail}")
        RESULTS.append((label, "PASS", detail))
    except FileNotFoundError as e:
        print(f"  SKIP  {e} (file absent - the run just skips this source)")
        RESULTS.append((label, "SKIP", str(e)))
    except Exception:
        tb = traceback.format_exc().strip().splitlines()[-1]
        print(f"  FAIL  {tb}")
        RESULTS.append((label, "FAIL", tb))


print("=" * 70)
print("RUN3 PREFLIGHT - real adapters on real data samples")
print("repo:", REPO)
print("data:", DATA)
print("=" * 70)

git_head = ""
try:
    git_head = (REPO / ".git" / "HEAD").read_text().strip()
except Exception:
    pass
print("repo HEAD:", git_head or "unknown", "(should be worklatest)")


# ---------- core fact sanity ----------
def fact_check():
    p = str(DATA / "processed" / "spending_fact.parquet").replace("'", "''")
    r = con.execute(f"""
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN CAST(total_paid AS DOUBLE) < 0 THEN 1 ELSE 0 END) AS neg,
               SUM(CASE WHEN CAST(total_paid AS DOUBLE) > 500000000 THEN 1 ELSE 0 END) AS corrupt,
               COUNT(DISTINCT billing_npi) AS npis,
               MIN(CAST(service_month AS VARCHAR)) AS lo,
               MAX(CAST(service_month AS VARCHAR)) AS hi
        FROM read_parquet('{p}')""").df().iloc[0]
    cols = [c[0] for c in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()]
    pm = "provider_matched" in cols
    return (f"{int(r['n']):,} rows | {int(r['npis']):,} NPIs | {r['lo']}..{r['hi']} | "
            f"negative-paid rows: {int(r['neg']):,} | corrupt(>$500M) rows: {int(r['corrupt']):,} "
            f"| provider_matched col: {pm}")


run("spending_fact sanity", fact_check)


def join_rate():
    """What share of Part B NPIs resolve into provider_dim (registry coverage)?"""
    pb = newest("partb_2*.csv")
    if pb is None:
        return "no partb file"
    s = sample_csv(pb, 30_000)
    npi_col = next(c for c in s.columns if c.strip().lower() in ("rndrng_npi", "npi"))
    npis = s[npi_col].astype(str).str.strip()
    con.register("sample_npis", pd.DataFrame({"npi": npis.unique()}))
    pd_p = str(DATA / "processed" / "provider_dim.parquet").replace("'", "''")
    hit = con.execute(f"""
        SELECT COUNT(*) FROM sample_npis s
        WHERE EXISTS (SELECT 1 FROM read_parquet('{pd_p}') d
                      WHERE CAST(d.npi AS VARCHAR) = s.npi)""").fetchone()[0]
    n = npis.nunique()
    return f"{hit:,}/{n:,} sampled Part B NPIs found in provider_dim ({hit/max(n,1):.0%})"


run("NPI join-rate (partb -> provider_dim)", join_rate)

# ---------- adapters on real samples ----------
from src.ingest_cms import (partb, partd, dmepos, opioid, openpayments,  # noqa: E402
                            saturation, order_referring, nppes_deactivation,
                            hrsa_340b, facility, hcris, pos, nucc_taxonomy,
                            docgraph)
from src.enforcement import medicare_revocations, opensanctions  # noqa: E402


def mk(label, path_fn, compute):
    def _f():
        p = path_fn()
        if p is None:
            raise FileNotFoundError("no file found")
        out = compute(p)
        return out
    run(label, _f)


mk("partb (largest year file)", lambda: newest("partb_2*.csv"),
   lambda p: (lambda m: f"{p.name}: {len(m[0]):,} provider rows from 50k sample")(
       partb.compute_partb_metrics(sample_csv(p))))
mk("partd 2017 (drug-level)", lambda: newest("partd_2017.csv"),
   lambda p: (lambda m: f"{p.name}: {len(m[0]):,} provider rows")(
       partd.compute_partd_metrics(sample_csv(p))))
mk("partd 2018 (the re-downloaded one)", lambda: newest("partd_2018.csv"),
   lambda p: (lambda m: f"{p.name}: {len(m[0]):,} provider rows "
              "(FAIL here = still the wrong summary layout)")(
       partd.compute_partd_metrics(sample_csv(p))))
mk("opioid 2024", lambda: newest("opioid_prescriber_2024.csv", "opioid*.csv"),
   lambda p: (lambda m: f"{p.name}: {len(m[0]):,} prescriber rows")(
       opioid.compute_opioid_metrics(sample_csv(p))))
mk("dmepos (referring+service)", lambda: newest("dmepos*referring*2022*.csv", "dmepos*.csv"),
   lambda p: (lambda m: f"{p.name}: {len(m[0]):,} referrer rows")(
       dmepos.compute_dmepos_metrics(sample_csv(p))))
mk("open_payments (200k sample of 8.9GB)", lambda: newest("open_payments*.csv", "OP_DTL*.csv"),
   lambda p: (lambda m: f"{p.name}: {len(m[0]):,} recipient rows")(
       openpayments.compute_openpayments_metrics(sample_csv(p, 200_000))))
mk("saturation", lambda: newest("market_saturation.csv", "*saturation*.csv"),
   lambda p: f"{p.name}: {len(saturation.compute_saturation_metrics(sample_csv(p))):,} rows")
mk("order_referring", lambda: newest("order_referring.csv"),
   lambda p: (lambda m: f"{p.name}: {len(m[0]):,} eligible referrers")(
       order_referring.eligible_referrers(sample_csv(p, 200_000))))
mk("nppes deactivation (.zip native)",
   lambda: newest("deactivation.zip", "*Deactivated*"),
   lambda p: f"{p.name}: {len(nppes_deactivation.load_deactivation(p)):,} deactivated NPIs")
mk("hrsa 340B OPAIS (.xlsx native)", lambda: newest("opais.xlsx", "opais*"),
   lambda p: f"{p.name}: {len(hrsa_340b.load_opais(p)):,} rows")
mk("facility PBJ", lambda: newest("pbj*.csv"),
   lambda p: (lambda m: f"{p.name}: {len(m[0]):,} facility rows")(
       facility.compute_pbj_metrics(sample_csv(p, 200_000))))
mk("facility hospice measures", lambda: newest("hospice.csv", "hospice_measures.csv"),
   lambda p: (lambda m: f"{p.name}: {len(m[0]):,} hospice rows")(
       facility.compute_hospice_metrics(sample_csv(p, 200_000))))
mk("facility deficiencies", lambda: newest("deficiencies.csv", "health_deficiencies.csv"),
   lambda p: (lambda m: f"{p.name}: {len(m[0]):,} facility rows")(
       facility.compute_deficiency_counts(sample_csv(p, 200_000))))


def hcris_all():
    files = sorted((DATA / "preclean" / "hcris").glob("*.csv"))
    if not files:
        raise FileNotFoundError("no hcris files")
    m, dropped = hcris.load_hcris(files)
    return f"{len(files)} files -> {len(m):,} CCN rows (dropped {dropped})"


run("hcris (all 3 cost-report files)", hcris_all)


def pos_all():
    files = sorted((DATA / "preclean" / "pos").rglob("*.csv"))
    if not files:
        raise FileNotFoundError("no pos csvs")
    total, with_beds, done = 0, 0, []
    for f in files[:6]:
        cap = pos.compute_pos_capacity(sample_csv(f, 100_000))
        total += len(cap)
        with_beds += int(cap["bed_count"].notna().sum())
        done.append(f.name)
    return f"{len(done)} files sampled -> {total:,} CCNs, {with_beds:,} with bed counts"


run("pos (recursive, incl. zip-extract subfolders)", pos_all)
mk("nucc taxonomy", lambda: newest("nucc_taxonomy.csv"),
   lambda p: f"{p.name}: {len(nucc_taxonomy.load_taxonomy_hierarchy(sample_csv(p))):,} taxonomy rows")
mk("medicare revocations", lambda: newest("*revoked*.csv", "*revocation*.csv"),
   lambda p: f"{p.name}: {len(medicare_revocations.normalize_revocations(sample_csv(p))):,} rows")
mk("opensanctions bulk", lambda: newest("targets.simple.csv"),
   lambda p: (lambda x: f"{p.name}: {len(x if isinstance(x, pd.DataFrame) else x[0]):,} records")(
       opensanctions.load_opensanctions_file(p)))


def docgraph_check():
    files = sorted((DATA / "preclean" / "docgraph").glob("*.csv*"))
    if not files:
        raise FileNotFoundError("no docgraph file staged")
    p = str(files[0]).replace("'", "''")
    hdr = list(con.execute(
        f"SELECT * FROM read_csv_auto('{p}', SAMPLE_SIZE=2048) LIMIT 0").df().columns)
    sel = docgraph.resolve_docgraph_columns(hdr)
    if not sel:
        raise ValueError(f"columns not resolvable: {hdr[:6]}")
    pc = sel.get("patient_count")
    kept = con.execute(f"""
        SELECT COUNT(*) FROM (SELECT * FROM read_csv_auto('{p}') LIMIT 2000000) t
        WHERE CAST(t."{pc}" AS DOUBLE) >= 20""").fetchone()[0] if pc else -1
    return (f"{files[0].name}: columns resolve {list(sel)}; in the first 2M pairs, "
            f"{kept:,} survive the >=20-patient threshold")


run("docgraph (CareSet staged file)", docgraph_check)


def reassignment_check():
    files = sorted((DATA / "preclean" / "reassignment").glob("*.csv"))
    if not files:
        raise FileNotFoundError("not downloaded yet (Priority 4 - fine to run without)")
    from src.attempt_2.clean_data import _resolve_columns
    from src.entity_graph.build_edges import _REASSIGN_COLS
    s = sample_csv(files[0], 20_000)
    resolved = _resolve_columns(list(s.columns), _REASSIGN_COLS)
    if "individual_npi" not in resolved:
        raise ValueError(f"headers do not resolve: {list(s.columns)[:8]}")
    return f"{files[0].name}: headers resolve -> {list(resolved)}"


run("reassignment file", reassignment_check)


def zip_county_check():
    p = DATA / "preclean" / "zip_county" / "zip_county.csv"
    if not p.exists():
        raise FileNotFoundError("zip_county.csv missing")
    s = sample_csv(p, 5_000)
    zc = next((c for c in s.columns if c.strip().upper() in ("ZIP", "ZIP_CODE", "ZCTA")), None)
    if zc is None:
        raise ValueError(f"no ZIP column: {list(s.columns)[:8]}")
    lens = s[zc].astype(str).str.len().value_counts().to_dict()
    short = sum(v for k, v in lens.items() if k < 5)
    note = " (WARNING: short ZIPs = leading zeros lost!)" if short > 0 else " (leading zeros intact)"
    return f"ZIP col '{zc}', length distribution {lens}{note}"


run("HUD zip_county", zip_county_check)

# ---------- verdict ----------
print("\n\n========== PREFLIGHT VERDICT ==========")
w = max(len(r[0]) for r in RESULTS) + 2
fails = 0
for label, status, detail in RESULTS:
    print(f"  {label:<{w}} {status}")
    if status == "FAIL":
        fails += 1
skips = sum(1 for r in RESULTS if r[1] == "SKIP")
print(f"\n{len(RESULTS) - fails - skips} PASS / {skips} SKIP / {fails} FAIL.")
if fails == 0:
    print("ALL CLEAR - run3.bat is safe to launch.")
else:
    print(f"{fails} FAIL(s) - upload RUN3_PREFLIGHT.txt before launching run3.bat.")
print("done.")
