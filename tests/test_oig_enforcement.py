"""oig_enforcement: tolerant listing parse, action page → case row, since-window, state filter."""
from __future__ import annotations

from src.enforcement.oig_enforcement import action_to_case, parse_listing, report, run_oig_feed

LISTING = """
<html><body><ul class="usa-list">
<li><a href="/fraud/enforcement/florida-man-pleads-guilty-to-83-million-pharmacy-fraud-scheme/">Florida Man Pleads Guilty to $8.3 Million Pharmacy Fraud Scheme</a>
<span class="date">September 3, 2025</span></li>
<li><a href="/fraud/enforcement/acme-hospice-agrees-to-pay-12-million/">Acme Hospice Care LLC Agrees to Pay $12 Million to Resolve False Claims Act Allegations</a>
<span class="date">2025-08-14</span></li>
<li><a href="/fraud/enforcement/old-case-from-2018/">Old Case Sentenced in Medicare Fraud</a> <span>March 1, 2018</span></li>
<li><a href="/fraud/enforcement/?page=2">Next</a></li>
<li><a href="/fraud/enforcement/rss/">RSS</a></li>
</ul></body></html>
"""
PAGE_FL = """<html><head><title>x</title><script>var a=1;</script></head><body>
<h1>Florida Man Pleads Guilty to $8.3 Million Pharmacy Fraud Scheme</h1><p>September 3, 2025</p>
<p>Michael Murphy, 44, of Tampa, Florida, pleaded guilty today to conspiracy to commit health care fraud.
Murphy owned Bay Rx Pharmacy LLC, a Tampa, Florida-based pharmacy, and billed Medicare for $8.3 million in
compounded creams that were never dispensed.</p></body></html>"""
PAGE_ACME = """<html><body><h1>Acme Hospice Care LLC Agrees to Pay $12 Million</h1>
<p>Acme Hospice Care LLC, located in Atlanta, Georgia, agreed to pay $12 million to resolve False Claims Act
allegations that it billed Medicare for hospice care for patients who were not terminally ill. The
whistleblower will receive a share. The government intervened in the Northern District of Georgia.</p></body></html>"""


def test_parse_listing_is_tolerant():
    items = parse_listing(LISTING)
    assert [i["date"] for i in items] == ["2025-09-03", "2025-08-14", "2018-03-01"]
    assert all(i["url"].startswith("https://oig.hhs.gov/fraud/enforcement/") for i in items)
    assert not any("page=2" in i["url"] or "/rss/" in i["url"] for i in items)
    assert parse_listing("<html><body>redesigned</body></html>") == []


def test_action_pages_become_cases_and_window_and_state_filter(tmp_path):
    pages = {
        "https://oig.hhs.gov/fraud/enforcement/": LISTING,
        "https://oig.hhs.gov/fraud/enforcement/florida-man-pleads-guilty-to-83-million-pharmacy-fraud-scheme/": PAGE_FL,
        "https://oig.hhs.gov/fraud/enforcement/acme-hospice-agrees-to-pay-12-million/": PAGE_ACME,
    }
    calls = []

    def fetch_text(url, params=None):
        calls.append((url, params))
        if params and params.get("page", 1) > 1:
            return "<html></html>"
        return pages[url]

    cases = run_oig_feed("2020-01-01", fetch_text=fetch_text, sleep_s=0, raw_root=tmp_path)
    assert len(cases) == 2                                       # the 2018 item is outside the window (never fetched)
    assert not any("old-case-from-2018" in u for u, _ in calls)
    fl = cases[cases["case_id"].str.contains("florida-man")].iloc[0]
    assert fl["defendant_name"] == "Michael Murphy" and fl["case_type"] == "criminal_hcf"
    assert fl["announced_date"] == "2025-09-03" and fl["jurisdiction"] == "FL"
    acme = cases[cases["case_id"].str.contains("acme")].iloc[0]
    assert acme["defendant_name"] == "Acme Hospice Care LLC" and acme["case_type"] == "civil_fca"
    assert acme["amount_usd"] == 12_000_000 and acme["intervened"] == 1
    assert acme["jurisdiction"].startswith("Northern District of Georgia")
    # state filter keeps FL and rows with no state; drops GA
    only_fl = run_oig_feed("2020-01-01", fetch_text=fetch_text, sleep_s=0, states=["FL"], raw_root=tmp_path)
    assert list(only_fl["defendant_name"]) == ["Michael Murphy"]
    assert "criminal_hcf 1" in report(cases)


def test_action_to_case_skips_non_healthcare():
    assert action_to_case("https://oig.hhs.gov/fraud/enforcement/x/", "OIG Names New Deputy",
                          "2025-01-01", "<p>Personnel announcement.</p>") is None
