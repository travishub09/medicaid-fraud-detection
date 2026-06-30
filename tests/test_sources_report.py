"""
test_sources_report.py — the consolidated SOURCES_REPORT audit.

Every export run ends with one table: which sources contributed features, which
were skipped and why, with the file they read. It is built by capturing the
``    [source] …`` progress lines the adapters already emit (no new call-site
plumbing), so a silently-dropped source — the year-suffixed Part-B/D skip class of
bug — is impossible to miss. These tests pin the capture rules and the renderer.
"""

from __future__ import annotations

from src.model_a.provider_features_export import (_SourceAudit,
                                                  _write_sources_report)


def _audit(lines):
    captured = []
    a = _SourceAudit(captured.append)        # inner sink just collects, so we also
    for ln in lines:                          # prove the original log still passes through
        a(ln)
    return a, captured


def test_used_and_skipped_are_classified():
    a, passthrough = _audit([
        "  running per-NPI adapters …",                       # not a [source] line
        "    [partb] 12,345 providers from partb_2024.csv, cols: upcoding_ratio",
        "    [partd] skipped: no source file in partd/",
    ])
    recs = {r["source"]: r for r in a.records()}
    assert recs["partb"]["status"] == "used"
    assert "partb_2024.csv" in recs["partb"]["detail"]
    assert recs["partd"]["status"] == "skipped"
    assert "no source file" in recs["partd"]["detail"]
    # the inner log still received every line verbatim (pass-through intact)
    assert passthrough[0].strip().startswith("running per-NPI adapters")


def test_assert_and_warn_lines_are_ignored():
    a, _ = _audit([
        "    [assert PASS] dollar_conservation",
        "    [WARN] entity graph is OLDER than the leads file",
        "    [hrsa_340b] 4 orgs, cols: contract_pharmacy_concentration",
    ])
    sources = {r["source"] for r in a.records()}
    assert sources == {"hrsa_340b"}          # infra lines never become "sources"


def test_used_supersedes_an_earlier_skip():
    # address logs multiple lines; a later "used" line must win over an earlier skip
    a, _ = _audit([
        "    [graph_velocity] skipped: needs ≥2 feature snapshots",
        "    [graph_velocity] 100 providers (snapshot diff)",
    ])
    rec = a.records()[0]
    assert rec["source"] == "graph_velocity"
    assert rec["status"] == "used"
    assert "snapshot diff" in rec["detail"]
    assert "needs" not in rec["detail"]      # the stale skip detail was dropped


def test_slash_named_source_splits_into_two_rows():
    a, _ = _audit([
        "    [growth/plausibility] skipped: pass --with-analytics",
    ])
    recs = {r["source"]: r for r in a.records()}
    assert set(recs) == {"growth", "plausibility"}
    assert all(r["status"] == "skipped" for r in recs.values())
    assert all("with-analytics" in r["detail"] for r in recs.values())


def test_records_sorted_used_first_then_alphabetical():
    a, _ = _audit([
        "    [zebra] skipped: no file",
        "    [partb] 10 providers from partb_2024.csv",
        "    [alpha] skipped: no file",
        "    [omega] 5 orgs, cols: x",
    ])
    order = [r["source"] for r in a.records()]
    # used first (alphabetical within), then skipped (alphabetical within)
    assert order == ["omega", "partb", "alpha", "zebra"]


def test_report_file_renders_table(tmp_path):
    a, _ = _audit([
        "    [partb] 12,345 providers from partb_2024.csv, cols: upcoding_ratio",
        "    [partd] skipped: no source file in partd/",
    ])
    _write_sources_report(a.records(), tmp_path)
    text = (tmp_path / "SOURCES_REPORT.md").read_text(encoding="utf-8")
    assert "SOURCES_REPORT" in text
    assert "`partb`" in text and "✅ used" in text
    assert "`partd`" in text and "⏭️ skipped" in text
    assert "1 source(s) contributed features; 1 skipped" in text


def test_empty_audit_still_writes_a_table(tmp_path):
    _write_sources_report([], tmp_path)
    text = (tmp_path / "SOURCES_REPORT.md").read_text(encoding="utf-8")
    assert "none recorded" in text
