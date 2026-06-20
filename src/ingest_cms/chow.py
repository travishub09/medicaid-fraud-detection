"""
chow.py — CMS Change-of-Ownership (CHOW) transaction files (data-expansion
sprint, docs/platform/16 §5).

Source: data.cms.gov change-of-ownership datasets for SNF / Hospital / HHA /
Hospice (free CSV, quarterly). Today ``entity_graph.ownership_churn`` recovers
churn only by DIFFING monthly All-Owners snapshots; the CHOW files give the
NATIVE buyer/seller transaction events (legal names, PAC IDs, transaction type,
effective date, ownership %), plus — under the Nov-2023 rule — explicit
private-equity / REIT flags. That turns inferred churn into real M&A events,
serial-acquirer rollup chains, and post-acquisition billing-shock windows
(strongest in hospice/HHA/SNF, the PE-rollup sectors).

  normalize_chow_events      CHOW csv -> the SAME event schema
                             ``ownership_churn.ownership_turnover_features``
                             consumes: org_node_id, owner_key, event_type
                             ('entry'|'exit'), event_date — so it drops straight
                             into the A7 pipeline in place of (or alongside) the
                             snapshot diff. A buyer is an 'entry', a seller an
                             'exit'; carries is_pe_reit through for the prior.

Org resolution is by CCN/PAC ID against the graph (reuse the facility CCN<->org
and org:pac:<pac> conventions). IDs are strings. docs/platform/16 §5.
"""

from __future__ import annotations

import pandas as pd

CHOW_COLS = {
    "ccn": ["CCN", "PRVDR_NUM", "Provider CCN", "ccn"],
    "buyer_name": ["Buyer Name", "buyer_organization_name", "buyer_name"],
    "buyer_pac": ["Buyer PAC ID", "buyer_pac_id", "Buyer Enrollment ID"],
    "seller_name": ["Seller Name", "seller_organization_name", "seller_name"],
    "seller_pac": ["Seller PAC ID", "seller_pac_id", "Seller Enrollment ID"],
    "transaction_type": ["Transaction Type", "transaction_type", "Type"],
    "effective_date": ["CHOW Effective Date", "effective_date", "Transaction Date"],
    "pe_reit_flag": ["Private Equity", "REIT", "pe_reit_flag"],
}


def normalize_chow_events(raw: pd.DataFrame,
                          ccn_to_org: pd.DataFrame | None = None) -> pd.DataFrame:
    """INPUT CONTRACT: a CHOW transaction csv (columns in CHOW_COLS) + an optional
    CCN/PAC->org_node_id crosswalk. OUTPUT: ownership-event rows matching
    ownership_churn's schema (org_node_id, owner_key, event_type, event_date)
    plus is_pe_reit. Unresolvable orgs dropped. docs/platform/16 §5."""
    raise NotImplementedError(
        "data-expansion sprint stub — implement per docs/platform/16 §5; the "
        "output feeds entity_graph.ownership_churn.ownership_turnover_features")
