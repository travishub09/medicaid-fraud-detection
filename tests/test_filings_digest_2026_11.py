"""filings_digest (2026-11-04): a folder of recorder / SOS PDFs -> a digest + the texts zipped."""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pandas as pd

from src.enforcement import filings_digest as fd


def _pdf(path: Path, lines: list[str]) -> None:
    """A minimal one-page PDF with Helvetica text, no library needed."""
    content = "BT /F1 11 Tf 50 740 Td 14 TL " + " ".join(f"({ln.replace('(', '').replace(')', '')}) Tj T*" for ln in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out.encode("latin-1")))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    path.write_bytes(out.encode("latin-1"))


def test_digest_classifies_and_zips_texts_and_lists_scans(tmp_path):
    root = tmp_path / "filings"
    (root / "piqua").mkdir(parents=True)
    (root / "sos").mkdir()
    _pdf(root / "piqua" / "doc1.pdf", ["OPEN-END MORTGAGE", "Instrument No. 2021-00012345",
                                       "MORTGAGOR: SOME PROPCO LLC", "MORTGAGEE: SOME LENDER FUNDING I LP",
                                       "Dated December 31, 2021 in the amount of $46,500,000.00"])
    _pdf(root / "piqua" / "doc2.pdf", ["ASSIGNMENT OF LEASES AND RENTS", "ASSIGNOR: SOME PROPCO LLC", "01/15/2022"])
    _pdf(root / "sos" / "articles.pdf", ["ARTICLES OF ORGANIZATION", "Statutory agent: AGENT SERVICES INC"])
    (root / "sos" / "scan.pdf").write_bytes(b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
                                            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
                                            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj\n"
                                            b"trailer << /Root 1 0 R >>\n%%EOF\n")
    out = tmp_path / "digest"
    df = fd.run(root, out)
    by = {r.file: r for r in df.itertuples()}
    assert by["doc1.pdf"].doc_type == "mortgage" and by["doc1.pdf"].amount_max == "46,500,000.00"
    assert "MORTGAGOR" in by["doc1.pdf"].parties and by["doc1.pdf"].instrument.startswith("2021-00012345")
    assert by["doc1.pdf"].first_date.upper().startswith("DECEMBER 31")
    assert by["doc2.pdf"].doc_type == "assignment_of_rents" and by["articles.pdf"].doc_type == "articles"
    assert not by["scan.pdf"].has_text_layer
    with zipfile.ZipFile(out / "filings_text.zip") as zf:
        names = set(zf.namelist())
    assert "piqua/doc1.txt" in names and "sos/scan.txt" not in names
    md = (out / "FILINGS_DIGEST.md").read_text(encoding="utf-8")
    assert "## piqua" in md and "(no text layer)" in md and "sos/scan.pdf" in md
    assert "scan.pdf" in (out / "NO_TEXT_LAYER.txt").read_text(encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "src.enforcement.filings_digest", "--root", str(root), "--out", str(tmp_path / "d2")],
                       capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert r.returncode == 0 and "4 PDF(s), 3 with a text layer, 1 scanned" in r.stdout
    assert fd.run(tmp_path / "empty", tmp_path / "d3").empty
