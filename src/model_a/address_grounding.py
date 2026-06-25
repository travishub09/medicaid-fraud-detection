"""
address_grounding.py — external-world grounding of the billing address (Pillar 4).

The strongest fraud priors often live OUTSIDE the claims data: "bills $8M from a
UPS-Store mailbox" is one of them. Full geocoding (is this a real clinic, a
residence, a vacant lot?) needs a live service; this module captures the
high-precision, OFFLINE signals from the address string and from address REUSE —
no network required, and the reuse signal is the NPI-grain complement to the graph's
org-level co-location rings.

  addr_is_mailbox        the address is a commercial mail-receiving agency / PMB /
                         PO box / mailbox-store brand — a provider can't deliver
                         care from a mailbox.
  addr_provider_count    how many distinct providers bill from the EXACT same
                         address (a clinic has a few; a shell farm has dozens).
  addr_shared            that count is at/above a shell threshold.

An optional injectable ``geocoder`` hook lets a USPS/CMRA or geocoding service be
plugged in later for the "is this a real clinic" check; offline it stays unused.
All clean features.
"""

from __future__ import annotations

import re

import pandas as pd

# Commercial mail-receiving agencies, private mailboxes, PO boxes, mailbox brands.
_MAILBOX = re.compile(
    r"\bPMB\b|\bP\.?\s?O\.?\s?BOX\b|\bPOST\s?OFFICE\s?BOX\b|\bMAILBOX(?:ES)?\b|"
    r"UPS\s?STORE|\bPAK\s?MAIL\b|POSTAL\s?ANNEX|PACK\s?(?:N|AND)\s?(?:SHIP|MAIL)|"
    r"\bMAIL\s?BOXES?\s?ETC\b|\bCMRA\b",
    re.IGNORECASE)
SHELL_ADDR_PROVIDERS = 5        # distinct providers at one address ≥ this = suspicious


def address_flags(provider_dim: pd.DataFrame, addr_col: str = "addr_key",
                  geocoder=None) -> pd.DataFrame:
    """Per-NPI address-grounding flags from the registration address.

    ``provider_dim`` needs ``npi`` and an address column (``addr_key`` by default).
    ``geocoder`` (optional): callable(addr_str)->dict to attach a live realness
    check; unused offline."""
    cols = ["npi", "addr_is_mailbox", "addr_provider_count", "addr_shared"]
    if provider_dim is None or not len(provider_dim) or addr_col not in provider_dim.columns:
        npis = (provider_dim["npi"].astype(str) if provider_dim is not None
                and "npi" in provider_dim.columns else pd.Series(dtype=str))
        return pd.DataFrame({"npi": npis, "addr_is_mailbox": 0,
                             "addr_provider_count": 0, "addr_shared": 0})[cols] \
            if len(npis) else pd.DataFrame(columns=cols)

    df = provider_dim[["npi", addr_col]].copy()
    df["npi"] = df["npi"].astype(str)
    addr = df[addr_col].fillna("").astype(str)
    is_mailbox = addr.str.contains(_MAILBOX).astype(int)

    # providers per non-empty address (exact addr_key match)
    counts = addr[addr.str.strip() != ""].value_counts()
    prov_count = addr.map(counts).fillna(0).astype(int)

    out = pd.DataFrame({
        "npi": df["npi"].to_numpy(),
        "addr_is_mailbox": is_mailbox.to_numpy(),
        "addr_provider_count": prov_count.to_numpy(),
        "addr_shared": (prov_count >= SHELL_ADDR_PROVIDERS).astype(int).to_numpy(),
    })
    if geocoder is not None:                  # optional live realness check
        try:
            matched = addr.map(lambda a: 1 if geocoder(a).get("matched") else 0)
            out["addr_geocoded"] = matched.to_numpy()
            # a billing provider whose address doesn't resolve to a real location
            out["addr_no_match"] = ((matched == 0) & (addr.str.strip() != "")).astype(int).to_numpy()
        except Exception:
            pass
    return out.drop_duplicates("npi")[[c for c in out.columns]]
