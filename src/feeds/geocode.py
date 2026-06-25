"""
geocode.py — live address geocoding for external grounding (depth, Pillar 4).

``address_grounding.py`` flags mailbox/PO-box addresses and address reuse OFFLINE.
The depth upgrade is asking the world whether the billing address is a real place:
the free **Census Geocoder** (no API key) resolves an address to coordinates and a
match type, so we can flag a billing provider whose address DOESN'T geocode to a
real street location (or only matches at a coarse ZIP centroid) — a strong tell for
a phantom clinic.

Reuses the feeds pattern: an injectable ``fetch_json`` transport (canned in tests,
never live in CI) and raw-response caching for provenance. Returns a callable that
``address_grounding.address_flags(geocoder=...)`` consumes, turning each address into
``addr_geocoded`` / ``addr_no_match`` / ``addr_match_type`` flags.

Live commercial add (documented, not built): USPS CMRA/DPV for definitive
mailbox/residential classification, and a parcel/landuse join for "is this a
clinic." Census alone already gives match-quality, which is most of the signal.
"""

from __future__ import annotations

from src.feeds.client import default_fetch_json

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
BENCHMARK = "Public_AR_Current"


def geocode_one(address: str, fetch_json=default_fetch_json) -> dict:
    """Geocode a single address via the Census one-line endpoint.

    Returns {matched, match_type, lat, lon, matched_address}. ``matched`` is False
    when Census finds no street match — the suspicious case for a billing provider.
    """
    addr = str(address or "").strip()
    if not addr:
        return {"matched": False, "match_type": "", "lat": None, "lon": None,
                "matched_address": ""}
    js = fetch_json(CENSUS_URL, params={"address": addr, "benchmark": BENCHMARK,
                                        "format": "json"})
    matches = (((js or {}).get("result") or {}).get("addressMatches")) or []
    if not matches:
        return {"matched": False, "match_type": "", "lat": None, "lon": None,
                "matched_address": ""}
    m = matches[0]
    coords = m.get("coordinates") or {}
    tiger = m.get("tigerLine") or {}
    return {"matched": True,
            "match_type": str(tiger.get("side") or m.get("matchType") or "match"),
            "lat": coords.get("y"), "lon": coords.get("x"),
            "matched_address": str(m.get("matchedAddress") or "")}


def census_geocoder(fetch_json=default_fetch_json, cache: dict | None = None):
    """A geocoder callable for ``address_flags(geocoder=...)``: address → flag dict
    with ``is_clinic`` (a real street match), plus ``matched`` / ``match_type``.
    An in-memory ``cache`` dedupes repeated addresses across providers."""
    cache = cache if cache is not None else {}

    def _geo(address: str) -> dict:
        key = str(address or "").strip().upper()
        if key in cache:
            return cache[key]
        try:
            r = geocode_one(address, fetch_json=fetch_json)
        except Exception:
            r = {"matched": False, "match_type": "error"}
        # a real, street-level match is our offline proxy for "a real location"
        out = {"is_clinic": bool(r.get("matched")),
               "matched": bool(r.get("matched")),
               "match_type": r.get("match_type", "")}
        cache[key] = out
        return out

    return _geo
