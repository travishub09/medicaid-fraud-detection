# Public Billing-Risk Lookup Tool (scaffold)

The inbound funnel and SEO front door for the platform. A public, explainable
lookup that shows how a provider/organization bills **relative to its peers** —
exposed as intuitive percentiles with named drivers and benign-explanation
context, never as a bare fraud accusation.

## Why it exists
- **Acquisition:** it is the top of the marketing funnel — educational, shareable,
  SEO-rich, and the natural place to capture inbound leads under the trust
  architecture (see `docs/platform/07-sourcing-and-marketing.md`).
- **Powered by Model A:** it surfaces the peer-relative percentile features the
  detection core already computes (`src/attempt_2/ingest/features.py`,
  `refine_layer2_v3.py`), presented for a lay audience.

## Hard constraints (see `docs/platform/01-legal-compliance.md`)
- Report **percentiles and drivers**, not a "fraud" label — defamation safety.
- Always show benign explanations and peer context.
- No PHI; education-first framing ("billing concerns", "compliance questions").

## Status
**v1 preview built** (`app.py`): `GET /healthz`, `GET /lookup/{npi}` serving
plain-language percentile risk cards from a percentile parquet
(`ingest_cms.to_peer_percentiles` output shape) — drivers, benign explanations,
disclaimer, structurally no fraud field (tested in
`tests/test_label_store_lookup.py`). Run locally:
`python -m src.lookup_tool --features <percentiles.parquet>`.

**Public launch is gated** on Part B data + Phase-0 counsel sign-off
(GAPS #18); the server binds to localhost by default. `api_stub.py` retains the
original contract notes; `/search` is a later increment.
