"""
lookup_tool — the public billing-risk lookup tool (SCAFFOLD).

The inbound funnel and SEO front door: a public, explainable provider/organization
billing-risk lookup powered by Model A's peer-relative percentile features (exposed
as intuitive percentiles, never raw z-scores or bare accusations). See
``docs/platform/07-sourcing-and-marketing.md`` and the defamation-safety notes in
``docs/platform/01-legal-compliance.md``.

Reuses the FastAPI dependency already in requirements.txt. Status: scaffold —
``api_stub.py`` defines the route shapes; no live data wiring yet.
"""
