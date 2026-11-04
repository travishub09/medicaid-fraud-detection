"""
filings_digest.py — read a folder of recorder / Secretary of State PDFs on the operator's
own machine and write a digest small enough to send (2026-11-04).

The analyst's Ohio pulls (county recorder images, Secretary of State filings) are
hundreds of megabytes of PDFs under <data_root>/enforcement/ohio/filings/, too large to
upload to a chat and work product that never goes to git. This tool runs where the files
are (the laptop is fine: pypdf reads a PDF in milliseconds and nothing here touches the
spending fact) and writes, per PDF: the page count, whether it has a text layer, a guess at
the document type from its first page (mortgage, assignment of mortgage, assignment of
rents, UCC filing, deed, lease memorandum, articles, agent change, merger, release), the
dates and dollar amounts it prints, the instrument / document number, and the party lines
(borrower / lender / mortgagor / mortgagee / grantor / grantee / secured party / debtor).
It also writes the full extracted text of every PDF that has one into a zip, so the whole
set travels as a few megabytes.

    python -m src.enforcement.filings_digest --root <data_root>/enforcement/ohio/filings \
        --out <data_root>/enforcement/ohio/filings_digest

Outputs (under --out): filings_digest.csv (one row per PDF), FILINGS_DIGEST.md (a table
per folder), filings_text.zip (one .txt per PDF with a text layer), and
NO_TEXT_LAYER.txt (scanned images that need OCR or a hand read: the recorder sites often
serve scans). Nothing is interpreted beyond the regexes; a party line is where the words
appear on the page, never a conclusion about who owes whom.
"""
from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path

import pandas as pd

DOC_TYPES = [
    ("assignment_of_rents", re.compile(r"ASSIGNMENT\s+OF\s+(LEASES\s+AND\s+)?RENTS", re.I)),
    ("assignment_of_mortgage", re.compile(r"ASSIGNMENT\s+OF\s+(OPEN-END\s+)?MORTGAGE", re.I)),
    ("ucc", re.compile(r"\bUCC\b|FINANCING\s+STATEMENT", re.I)),
    ("mortgage_release", re.compile(r"(RELEASE|SATISFACTION|DISCHARGE)\s+OF\s+MORTGAGE|MORTGAGE\s+RELEASE", re.I)),
    ("subordination", re.compile(r"SUBORDINATION", re.I)),
    ("mortgage", re.compile(r"\bMORTGAGE\b|DEED\s+OF\s+TRUST", re.I)),
    ("lease_memorandum", re.compile(r"MEMORANDUM\s+OF\s+(MASTER\s+)?LEASE", re.I)),
    ("deed", re.compile(r"\b(WARRANTY|QUIT[- ]?CLAIM|FIDUCIARY|LIMITED\s+WARRANTY)\s+DEED\b|\bDEED\b", re.I)),
    ("articles", re.compile(r"ARTICLES\s+OF\s+(ORGANIZATION|INCORPORATION)|CERTIFICATE\s+OF\s+FORMATION", re.I)),
    ("agent_change", re.compile(r"(STATUTORY|REGISTERED)\s+AGENT|AGENT\s+UPDATE|CHANGE\s+OF\s+AGENT", re.I)),
    ("merger", re.compile(r"CERTIFICATE\s+OF\s+MERGER|MERGER", re.I)),
    ("name_change", re.compile(r"NAME\s+CHANGE|AMENDMENT\s+TO\s+ARTICLES|CERTIFICATE\s+OF\s+AMENDMENT", re.I)),
    ("foreign_registration", re.compile(r"FOREIGN\s+(LIMITED\s+LIABILITY|CORPORATION)|REGISTRATION\s+OF\s+FOREIGN", re.I)),
    ("reinstatement", re.compile(r"REINSTATEMENT", re.I)),
    ("cancellation", re.compile(r"CANCELLATION|DISSOLUTION", re.I)),
]
PARTY_WORDS = re.compile(r"\b(MORTGAGOR|MORTGAGEE|BORROWER|LENDER|GRANTOR|GRANTEE|SECURED\s+PARTY|DEBTOR|"
                         r"ASSIGNOR|ASSIGNEE|LANDLORD|TENANT|TRUSTEE|BENEFICIARY)\b", re.I)
DATE_RE = re.compile(r"\b(?:(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{1,2},?\s+\d{4}"
                     r"|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})\b", re.I)
AMOUNT_RE = re.compile(r"\$\s?\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\$\s?\d{4,}(?:\.\d{2})?")
INSTR_RE = re.compile(r"(?:INSTRUMENT|INST\.?|DOCUMENT|DOC\.?|FILE|RECORDING|OR\s+BOOK)\s*(?:NO\.?|NUMBER|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{3,20})", re.I)
MIN_CHARS_PER_PAGE = 40


_READER = None


def _pdf_reader():
    """pypdf when it imports; else None (one sandbox's cryptography build panics at
    import, a BaseException no `except Exception` catches), and the poppler
    `pdftotext` / `pdfinfo` binaries are the fallback."""
    global _READER
    if _READER is None:
        try:
            from pypdf import PdfReader
            _READER = PdfReader
        except BaseException:      # noqa: BLE001
            _READER = False
    return _READER or None


def _poppler_text(path: Path) -> tuple[str, int]:
    import shutil
    import subprocess
    if not shutil.which("pdftotext"):
        raise RuntimeError("neither pypdf nor pdftotext is available")
    txt = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, errors="replace")
    info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, errors="replace")
    m = re.search(r"Pages:\s+(\d+)", info.stdout)
    pages = int(m.group(1)) if m else 0
    if txt.returncode != 0 and not txt.stdout:
        raise RuntimeError((txt.stderr or "pdftotext failed").strip()[:160])
    return txt.stdout, pages


def pdf_text(path: Path, max_pages: int | None = None) -> tuple[str, int]:
    """(text, pages): pypdf, else poppler; a page that raises contributes nothing."""
    PdfReader = _pdf_reader()
    if PdfReader is None:
        return _poppler_text(path)
    reader = PdfReader(str(path))
    n = len(reader.pages)
    parts = []
    for i, page in enumerate(reader.pages):
        if max_pages is not None and i >= max_pages:
            break
        try:
            parts.append(page.extract_text() or "")
        except Exception:      # noqa: BLE001
            parts.append("")
    return "\n".join(parts), n


def classify(text: str) -> str:
    head = text[:4000]
    for name, rx in DOC_TYPES:
        if rx.search(head):
            return name
    return "other"


def party_lines(text: str, limit: int = 8) -> list[str]:
    out = []
    for line in text.splitlines():
        s = " ".join(line.split())
        if PARTY_WORDS.search(s) and 8 < len(s) < 220:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def ocr_available() -> bool:
    import shutil
    return bool(shutil.which("tesseract") and shutil.which("pdftoppm"))


def ocr_text(path: Path, dpi: int = 200, max_pages: int = 40) -> str:
    """OCR a scanned PDF: pdftoppm rasterizes each page, tesseract reads it. The recorder
    sites serve mortgage images with no text layer; this is how their borrower / lender
    lines reach the digest. Needs the two binaries (Ubuntu: apt-get install -y
    tesseract-ocr poppler-utils)."""
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="filings_ocr_") as tmp:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-l", str(max_pages), "-png", str(path), str(Path(tmp) / "pg")],
                       capture_output=True)
        parts = []
        for png in sorted(Path(tmp).glob("pg*.png")):
            r = subprocess.run(["tesseract", str(png), "-", "--psm", "6"], capture_output=True, text=True, errors="replace")
            parts.append(r.stdout)
    return "\n".join(parts)


def digest_pdf(path: Path, root: Path, ocr: bool = False) -> dict:
    try:
        text, pages = pdf_text(path)
        err = ""
    except Exception as e:      # noqa: BLE001
        text, pages, err = "", 0, str(e)[:160]
    chars = len(text.strip())
    has_text = pages > 0 and chars / max(pages, 1) >= MIN_CHARS_PER_PAGE
    ocr_used = False
    if ocr and not has_text and pages > 0 and ocr_available():
        try:
            text = ocr_text(path)
            chars = len(text.strip())
            has_text = chars / max(pages, 1) >= MIN_CHARS_PER_PAGE
            ocr_used = has_text
        except Exception as e:      # noqa: BLE001
            err = f"ocr: {str(e)[:120]}"
    amounts = [a.replace(" ", "") for a in AMOUNT_RE.findall(text)]
    amount_max = ""
    if amounts:
        try:
            amount_max = f"{max(float(a.strip('$').replace(',', '')) for a in amounts):,.2f}"
        except ValueError:
            amount_max = ""
    dates = DATE_RE.findall(text)
    instr = INSTR_RE.search(text[:3000])
    return {
        "folder": str(path.parent.relative_to(root)) if path.parent != root else ".",
        "file": path.name,
        "pages": pages,
        "chars": chars,
        "has_text_layer": has_text,
        "ocr": ocr_used,
        "doc_type": classify(text) if has_text else ("" if not err else "error"),
        "first_date": dates[0] if dates else "",
        "dates": " | ".join(dict.fromkeys(dates[:6])),
        "amount_max": amount_max,
        "amounts": " | ".join(dict.fromkeys(amounts[:6])),
        "instrument": instr.group(1) if instr else "",
        "parties": " || ".join(party_lines(text)),
        "error": err,
        "_text": text if has_text else "",
    }


def run(root: Path, out: Path, ocr: bool = False) -> pd.DataFrame:
    out.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(p for p in root.rglob("*.pdf") if p.is_file())
    if ocr and not ocr_available():
        print("[filings_digest] --ocr asked but tesseract / pdftoppm are not installed: scans stay unread "
              "(Ubuntu: sudo apt-get install -y tesseract-ocr poppler-utils)")
    rows = []
    for i, p in enumerate(pdfs, 1):
        rows.append(digest_pdf(p, root, ocr=ocr))
        if ocr and i % 10 == 0:
            print(f"[filings_digest] {i}/{len(pdfs)} ...", flush=True)
    df = pd.DataFrame(rows)
    if len(df):
        with zipfile.ZipFile(out / "filings_text.zip", "w", zipfile.ZIP_DEFLATED) as zf:
            for r in rows:
                if r["_text"]:
                    name = (Path(r["folder"]) / r["file"]).with_suffix(".txt")
                    zf.writestr(str(name).replace("\\", "/"), r["_text"])
        df = df.drop(columns=["_text"])
        df.to_csv(out / "filings_digest.csv", index=False)
        (out / "NO_TEXT_LAYER.txt").write_text(
            "\n".join(f"{r.folder}/{r.file} ({r.pages} pages)" for r in df[~df["has_text_layer"]].itertuples()) + "\n",
            encoding="utf-8")
    (out / "FILINGS_DIGEST.md").write_text(to_markdown(df, root), encoding="utf-8")
    return df


def to_markdown(df: pd.DataFrame, root: Path) -> str:
    L = ["# FILINGS DIGEST", "",
         f"_Folder {root}: {len(df)} PDF(s). A document type is a regex over the first page; a party line is "
         "where the words appear, never a conclusion. Scanned images (no text layer) are listed at the end and "
         "need OCR or a hand read. Work product: this digest stays under enforcement/, never git._", ""]
    if not len(df):
        return "\n".join(L + ["_No PDFs found._"]) + "\n"
    for folder, g in df.groupby("folder", sort=True):
        L += [f"## {folder}  ({len(g)} files, {int(g['has_text_layer'].sum())} with text)", "",
              "| file | pages | type | first date | largest amount | instrument | parties |", "|---|--:|---|---|--:|---|---|"]
        for r in g.sort_values("file").itertuples():
            typ = (r.doc_type + (" (ocr)" if getattr(r, "ocr", False) else "")) if r.has_text_layer else "(no text layer)"
            L.append(f"| {r.file} | {r.pages} | {typ} | {r.first_date} | {r.amount_max} | {r.instrument} | {str(r.parties)[:200]} |")
        L.append("")
    nt = df[~df["has_text_layer"]]
    L += [f"## Scanned, no text layer: {len(nt)} file(s)", ""]
    L += [f"- {r.folder}/{r.file} ({r.pages} pages){(': ' + r.error) if r.error else ''}" for r in nt.itertuples()] or ["_none_"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="folder of PDFs (searched recursively)")
    ap.add_argument("--out", required=True, help="output folder for the digest")
    ap.add_argument("--ocr", action="store_true", help="OCR the scanned PDFs (tesseract + pdftoppm)")
    a = ap.parse_args()
    df = run(Path(a.root), Path(a.out), ocr=a.ocr)
    n_text = int(df["has_text_layer"].sum()) if len(df) else 0
    n_ocr = int(df["ocr"].sum()) if len(df) and "ocr" in df.columns else 0
    print(f"[filings_digest] {len(df)} PDF(s), {n_text} with a text layer ({n_ocr} by OCR), {len(df) - n_text} scanned -> {Path(a.out) / 'FILINGS_DIGEST.md'}")
    if len(df):
        print(df["doc_type"].value_counts().to_string())


if __name__ == "__main__":
    main()
