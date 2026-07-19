"""
nppes_api.py — on-demand single-NPI lookups against the live NPI Registry.

NOT a bulk replacement (the monthly NPPES file stays the identity backbone).
This is for freshness: confirming a dossier target's current status/address at
review time, and future lookup-tool enrichment. Free, no key.

    from src.ingest_cms.nppes_api import lookup_npi
    lookup_npi("1003000126")
"""

from __future__ import annotations

from src.attempt_2.clean_data import canonicalize_npi
from src.feeds.client import default_fetch_json

NPPES_API_URL = "https://npiregistry.cms.hhs.gov/api/"
API_VERSION = "2.1"


def lookup_npi(npi: str, fetch_json=default_fetch_json) -> dict | None:
    """One NPI → a flat dict (None if invalid or not found).

    The NPI is Luhn-validated first — never send garbage to an external API."""
    canon = canonicalize_npi(npi)
    if canon is None:
        return None
    payload = fetch_json(NPPES_API_URL,
                         params={"number": canon, "version": API_VERSION})
    results = payload.get("results") or []
    if not results:
        return None
    r = results[0]
    basic = r.get("basic", {})
    addresses = r.get("addresses", []) or [{}]
    primary = next((a for a in addresses
                    if a.get("address_purpose") == "LOCATION"), addresses[0])
    taxonomies = r.get("taxonomies", []) or [{}]
    primary_tax = next((t for t in taxonomies if t.get("primary")), taxonomies[0])
    return {
        "npi": canon,
        "entity_type": str(r.get("enumeration_type", ""))
                       .replace("NPI-", ""),                   # NPI-1/NPI-2 → 1/2
        "org_name": str(basic.get("organization_name", "")),
        "first_name": str(basic.get("first_name", "")),
        "last_name": str(basic.get("last_name", "")),
        "status": str(basic.get("status", "")),
        "taxonomy_code": str(primary_tax.get("code", "")),
        "taxonomy_desc": str(primary_tax.get("desc", "")),
        "addr_line1": str(primary.get("address_1", "")),
        "city": str(primary.get("city", "")),
        "state": str(primary.get("state", "")),
        "enumeration_date": str(basic.get("enumeration_date", "")),
        "last_updated": str(basic.get("last_updated", "")),
    }
