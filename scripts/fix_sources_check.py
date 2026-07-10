"""
fix_sources_check.py — diagnose every skipped data source against the data on disk.

Read-only. Run it on the operator machine and send back FIX_SOURCES_REPORT.txt:

    cd C:\\Users\\treyr\\medicaid-fraud-detection
    python scripts\\fix_sources_check.py --data-root "C:\\Users\\treyr\\OneDrive\\Desktop\\data"

For each source the run skipped, this answers: is it a missing flag, a missing
snapshot, a bad file, or data we still need to acquire — and names the exact file
it checked. It also scans the preclean/processed files for columns that could
build the two gated claim slices (ndc_claims / referred_claims), so we know
whether the NADAC and order_referring schemes are unlockable from data already
on disk.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pandas as pd  # noqa: E402

LINES: list[str] = []


def say(msg: str = "") -> None:
    print(msg)
    LINES.append(msg)


def _headers(p: Path, n: int = 0) -> list[str]:
    """Column names of a csv/xlsx/parquet without loading data."""
    try:
        if p.suffix.lower() == ".parquet":
            import pyarrow.parquet as pq
            return [str(c) for c in pq.ParquetFile(p).schema_arrow.names]
        if p.suffix.lower() in (".xlsx", ".xls"):
            df = pd.read_excel(p, nrows=5, dtype=str)
            return [str(c) for c in df.columns]
        from src.attempt_2.clean_data import read_csv_text
        return [str(c) for c in read_csv_text(p, nrows=0).columns]
    except Exception as e:
        return [f"<unreadable: {e}>"]


def check_flags(root: Path) -> None:
    say("1) billing_lm / growth / plausibility  (fix = a flag, no data needed)")
    spend = root / "processed" / "spending_fact.parquet"
    pdim = root / "processed" / "provider_dim.parquet"
    ok = spend.exists() and pdim.exists()
    say(f"   spending_fact.parquet: {'FOUND' if spend.exists() else 'MISSING'}")
    say(f"   provider_dim.parquet:  {'FOUND' if pdim.exists() else 'MISSING'}")
    try:
        from src.model_a.billing_lm import _PAID_GUARD  # noqa: F401
        say("   billing_lm corruption guard: PRESENT (bundle 17 loaded — the "
            "run-3 crash is fixed)")
    except ImportError:
        say("   billing_lm corruption guard: NOT FOUND — load bundle 17 first or "
            "billing_lm will crash again")
    action = ("add --with-analytics to the export command; all three light up"
              if ok else "processed files missing — rebuild processed/ first")
    say(f"   ACTION: {action}")
    say()


def check_dmepos(root: Path) -> None:
    say("2) dmepos  (fix = code fallback already in bundle 17 + one re-download)")
    d = root / "preclean" / "dmepos"
    if not d.is_dir():
        say(f"   {d} does not exist — create it and download the file (see PDF #1)")
        say()
        return
    from src.attempt_2.clean_data import _resolve_columns
    from src.ingest_cms.dmepos import DMEPOS_COLS
    good = []
    for p in sorted(d.glob("*.csv")):
        resolved = _resolve_columns(_headers(p), DMEPOS_COLS)
        missing = [c for c in ("npi", "hcpcs", "services") if c not in resolved]
        say(f"   {p.name}: "
            f"{'usable (adapter columns resolve)' if not missing else 'BAD LAYOUT — missing ' + str(missing)}")
        if not missing:
            good.append(p.name)
    if good:
        say(f"   ACTION: works now (falls back to {sorted(good)[-1]}). For current-year "
            "signal, download the latest 'by Referring Provider and Service' detail "
            "file (PDF #1).")
    else:
        say("   ACTION: download the detail file (PDF #1) — no usable layout on disk.")
    say()


def check_340b(root: Path) -> None:
    say("3) hrsa_340b  (fix = header sniff in bundle 17; verifying your actual file)")
    d = root / "preclean" / "hrsa_340b"
    hits = (sorted(d.glob("opais.*")) + sorted(d.glob("*.xlsx"))
            + sorted(d.glob("*.csv"))) if d.is_dir() else []
    if not hits:
        say(f"   no OPAIS file in {d} — download it (PDF #2)")
        say()
        return
    p = hits[0]
    say(f"   probing {p.name} headers (the full parse is slow on this big file "
        "and happens during the run instead) ...")
    try:
        marks = ("340b id", "id_340b", "entity name", "covered entity name")
        found = []
        if p.suffix.lower() in (".xlsx", ".xls"):
            # never open the workbook here: even read-only, openpyxl loads the
            # full shared-string table (minutes on the OPAIS export). Stream the
            # raw zip members instead — header text lives in sharedStrings.xml
            # (Excel-style) or inline in worksheet XML (openpyxl-style), both
            # near the front of their member.
            import zipfile
            with zipfile.ZipFile(p) as z:
                members = ([n for n in z.namelist()
                            if n.endswith("sharedStrings.xml")]
                           + [n for n in z.namelist()
                              if n.startswith("xl/worksheets/")
                              and n.endswith(".xml")])
                targets = [m.encode() for m in marks]
                for name in members:
                    residual = b""
                    read = 0
                    with z.open(name) as f:
                        while read < (8 << 20):
                            chunk = f.read(1 << 20)
                            if not chunk:
                                break
                            read += len(chunk)
                            buf = (residual + chunk).lower()
                            hit = next((t for t in targets if t in buf), None)
                            if hit:
                                found = [f"'{hit.decode()}' found in {name}"]
                                break
                            residual = chunk[-64:]
                    if found:
                        break
        else:
            from src.attempt_2.clean_data import read_csv_text
            head = read_csv_text(p, nrows=10)
            rows = [[str(c) for c in head.columns]] + head.astype(str).values.tolist()
            for i, row in enumerate(rows):
                if any(str(x).strip().lower() in marks for x in row):
                    found.append(f"csv (header row {i + 1})")
                    break
        if found:
            say(f"   SUCCESS: real header located in {', '.join(found)} — the "
                "fixed loader will parse it during the run.")
            say("   ACTION: none — this source lights up on the next run.")
        else:
            say("   STILL FAILING: no 340B ID / Entity Name header in the first "
                "10 rows of any sheet.")
            say("   ACTION: re-download a fresh Daily Report export (PDF #2).")
    except Exception as e:
        say(f"   probe failed: {e}")
        say("   ACTION: send me this error text.")
    say()


def check_death_master(root: Path) -> None:
    say("4) death_master  (fix = a PAID data purchase — defer to Brad)")
    d = root / "preclean" / "dmf"
    say(f"   preclean/dmf/: {'exists' if d.is_dir() else 'not present (expected)'}")
    say("   ACTION: the SSA Limited Access Death Master File requires NTIS "
        "certification and a paid subscription (PDF #5). Skip unless Brad "
        "approves; NPPES deactivation already covers part of this signal.")
    say()


def scan_claim_slices(root: Path) -> None:
    say("5) nadac + order_referring  (each needs a reference file + a claim slice)")
    nref = root / "preclean" / "nadac"
    nref_hit = sorted(nref.glob("*.csv")) if nref.is_dir() else []
    say(f"   NADAC reference file: {nref_hit[0].name if nref_hit else 'MISSING — free download (PDF #3)'}")
    elig = root / "preclean" / "order_referring"
    elig_hit = sorted(elig.glob("*.csv")) if elig.is_dir() else []
    say(f"   Order/Referring eligibility file: {elig_hit[0].name if elig_hit else 'MISSING — free download (PDF #4)'}")
    for slc in ("ndc_claims.parquet", "referred_claims.parquet"):
        p = root / "processed" / slc
        say(f"   processed/{slc}: {'FOUND' if p.exists() else 'not built yet'}")

    say("   scanning your files for columns that could BUILD the two slices ...")
    say("   (a candidate must satisfy the REAL slice builder: distinct columns "
        "for every required field, not just look-alike names)")
    from src.attempt_2.clean_data import _resolve_columns
    from src.ingest_cms.claim_slices import _NDC_WANTED, _REFERRAL_WANTED
    ndc_cands, ref_cands = [], []
    roots = [root / "preclean", root / "processed", root / "interim"]
    seen = 0
    for base in roots:
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.suffix.lower() not in (".csv", ".parquet") or not p.is_file():
                continue
            seen += 1
            if seen > 400:      # safety valve on a huge tree
                break
            hdr = _headers(p)
            ndc_r = _resolve_columns(hdr, _NDC_WANTED)
            if (len(ndc_r) == len(_NDC_WANTED)
                    and len(set(ndc_r.values())) == len(_NDC_WANTED)):
                ndc_cands.append(str(p.relative_to(root)))
            ref_r = _resolve_columns(hdr, _REFERRAL_WANTED)
            if (len(ref_r) == len(_REFERRAL_WANTED)
                    and len(set(ref_r.values())) == len(_REFERRAL_WANTED)):
                ref_cands.append(str(p.relative_to(root)))
    say(f"   NDC-claims candidates (npi + ndc columns): "
        f"{', '.join(ndc_cands[:6]) if ndc_cands else 'NONE found'}")
    say(f"   referral-claims candidates (npi + referring columns): "
        f"{', '.join(ref_cands[:6]) if ref_cands else 'NONE found'}")
    say("   ACTION: download the two free reference files (PDF #3, #4). If a "
        "candidate file is listed above, send me its name and I will wire the "
        "slice builder to it; if NONE, the two schemes stay dormant until a "
        "line-level claims extract exists (PDF explains what qualifies).")
    say()


def check_snapshots(root: Path) -> None:
    say("6) ownership_churn  (fix = start the monthly snapshot cadence today)")
    d = root / "owner_snapshots"
    snaps = sorted(d.glob("owners_*.parquet")) if d.is_dir() else []
    say(f"   owner snapshots on disk: {len(snaps)}"
        + (f" ({', '.join(p.name for p in snaps[-3:])})" if snaps else ""))
    edges = root / "graph" / "edges" / "owned_by_edges.parquet"
    say(f"   graph owner edges: {'FOUND' if edges.exists() else 'MISSING — rebuild graph'}")
    say("   ACTION: run fix_sources.bat (archives this month's snapshot). The "
        "feature lights up automatically once a second monthly snapshot exists "
        "in August.")
    say()
    say("7) graph_velocity  (fix = snapshot cadence + the embeddings run)")
    fd = root / "feature_snapshots"
    fsnaps = sorted(fd.glob("*.parquet")) if fd.is_dir() else []
    say(f"   feature snapshots on disk: {len(fsnaps)}")
    say("   NOTE: velocity diffs the graph-embedding columns, and the 16GB runs "
        "were --no-embeddings, so this source needs BOTH (a) the 64GB embeddings "
        "run and (b) two snapshots after it. fix_sources.bat takes snapshot #1 "
        "now so the clock starts.")
    say()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=str(Path.home() / "Desktop" / "data"))
    args = ap.parse_args()
    root = Path(args.data_root)
    say("FIX-SOURCES DIAGNOSTIC — every skipped source, checked against disk")
    say(f"data root: {root}")
    say("=" * 70)
    say()
    check_flags(root)
    check_dmepos(root)
    check_340b(root)
    check_death_master(root)
    scan_claim_slices(root)
    check_snapshots(root)
    out = Path("FIX_SOURCES_REPORT.txt")
    out.write_text("\n".join(LINES), encoding="utf-8")
    say(f"wrote {out.resolve()} — send this file back.")


if __name__ == "__main__":
    main()
