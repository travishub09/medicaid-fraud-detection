# 01 — Legal & Regulatory Landscape (the gating layer)

This is Phase 0 and it gates everything else. The compliance design must be settled
with FCA counsel, advertising counsel, privacy counsel, and litigation-finance
counsel **before** lead generation begins. Nothing below is legal advice; it is the
engineering-facing summary of the constraints the system is built around.

## How the False Claims Act pays
- A relator files **under seal**; the government investigates and decides whether to
  intervene.
- Relator share: **15–25%** on intervention, **25–30%** if the government declines
  and the relator proceeds.
- Damages are **trebled** plus per-claim penalties; relator counsel works on
  contingency with statutory fee-shifting.

## The four gates (encode each as a hard check in intake/diligence)
1. **First-to-file** — only the first relator on a given set of facts recovers.
   → Model C must run a first-to-file clearance check (PACER) before financing.
2. **Public-disclosure bar** — allegations already public can be barred. → a case
   built mostly from *public* data weakens standing; public data corroborates, it
   does not originate the claim.
3. **Original source** — direct, independent, materially-additive knowledge survives
   the bar. → this is why the human/insider layer (Model B) is the actual asset.
4. **Seal + anti-retaliation** — 31 U.S.C. 3730(h). → outreach and intake must
   protect the relator and never counsel unlawful conduct.

## Enforcement environment (why healthcare, why intervention)
FY2024: $2.9B+ recovered, $2.4B from qui tam, $400M+ to relators; ~$2.2B of qui tam
recoveries came from intervened/pursued cases — **intervention is almost the whole
game** (this is why P(intervene) is Model C's primary target). A record 979 qui tam
suits were filed, 370 healthcare-related; healthcare is the largest category (~$1.67B).

## Constitutional overhang — Zafirov
A Middle District of Florida judge held the qui tam provisions unconstitutional under
the Appointments Clause (Sept. 2024); the Eleventh Circuit heard argument in Dec.
2025 with a decision pending. Other circuits have upheld qui tam. Florida sits in the
at-risk circuit. → Model C carries venue/circuit as a feature; weigh entity structure,
a state-FCA emphasis, and the dual-use hedge.

## Cross-cutting guardrails (every component must honor these)
- **FCRA** — a person-level scoring system risks producing "consumer reports" if its
  outputs drive eligibility decisions about individuals. Structure Model B outputs as
  **marketing-audience definitions, not adjudications about people**; clear scope with
  counsel.
- **Anti-solicitation** — named-individual outreach is gated by counsel and state bar
  solicitation rules. Model B emits audiences, never call lists.
- **Privacy** — the "likely whistleblower at employer X" inference is sensitive from
  the moment it exists. Minimize retention, lock down access, never expose it.
- **Fairness** — key on role, knowledge, and departure; never on demographic or
  protected-class proxies; audit for proxy leakage. Handle financial-distress proxies
  at low weight and flag them (adverse-selection / predatory-targeting risk).
- **Defamation safety** — the public lookup tool reports peer-relative **percentiles
  and named drivers with benign explanations**, never a "fraud" conclusion. Maintain
  clear separation between data-derived risk scores and legal conclusions of fraud.
- **No-accusation advertising** — advertise around "concerns", "billing pressure",
  "unsupported diagnosis capture", "Medicare/Medicaid compliance questions"; never
  accuse a named company.
- **PHI avoidance** — design intake forms to avoid PHI at top of funnel ("describe the
  concern without patient identifiers"); use secure channels and route to counsel when
  facts become case-specific. Never encourage unauthorized access, scraping of employer
  systems, taking PHI, or disclosure of privileged material.
- **Funder control** — avoid funder control over litigation strategy, settlement
  authority, or privileged strategy.
- **Data terms** — license people-data and proprietary claims properly; proprietary
  claims licenses almost always bar litigation-targeting use; CMS research files
  (T-MSIS RIF, LDS) carry DUAs restricting use to approved research.

## What this means for the build
- Phase 0 sign-off precedes Phase 4 (campaigns) and Phase 5 (intake).
- The guardrails are encoded in code where possible: Model B outputs aggregated
  audiences (`src/model_b/audiences.py`), the lookup tool returns no fraud boolean
  (`src/lookup_tool/api_stub.py`), and the entity graph treats the whistleblower
  inference as sensitive (`src/entity_graph/person_resolver.py`).
