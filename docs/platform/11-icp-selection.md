# 11 — ICP Selection: The First Two Typologies

The 90-day plan's Weeks 1–2 decision: choose the first two fraud typologies and
build everything (detection emphasis, personas, landing pages, campaigns) around
them. This is the recommendation with the reasoning; it is reversible, but every
week without a choice diffuses the build.

## Recommendation

**ICP 1 — Home health / hospice / personal care (incl. EVV).**
**ICP 2 — DME / orthotics / telehealth-ordering rings.**

## Why these two (and not the others)

The strategy's sector overlay ranks managed-care/MA risk adjustment first by
dollars — but ICP choice must weigh *our data, our models, and reachable relators
today*, not just sector size:

| Criterion | HH/hospice/personal care | DME/telehealth | MA risk adjustment |
|---|---|---|---|
| Our data covers it | **Yes — Medicaid spending is exactly this domain**; personal-care HCPCS already flagged in `clean_data.BROAD_HCPCS_CODES` | Partial now; full with the DMEPOS file (procurement P1 #3) | **No** — needs MA encounter/RADV-adjacent data we don't have and can't easily get |
| Detection signals built | concentration / payment / service intensity concepts fire naturally here; EVV-style phantom-visit logic is a near extension | ordering-physician HHI + ring detection (the graph is built for this) | chart-mining signals are invisible in our files |
| Sector fraud density | Highest Medicaid density; CMS's own saturation tool exists *because* of these lines | Perennial top category; telefraud enforcement priority | Largest dollars, hardest proof |
| Relator pool | Large, reachable clinical/field staff (Tier B/C personas); high turnover = constant departure signal | Sales reps and intake staff — classic kickback witnesses (Tier B) | Senior coders/executives; fewer, more cautious |
| Competition for relators | Lower (less mined than Medicare) | Moderate | Highest — every FCA firm hunts MA |
| State-FCA / venue hedge | **Strong** — Medicaid/personal care is state-FCA territory (the Zafirov hedge) | Federal-heavy | Federal-only in practice |

MA risk adjustment stays the *third* typology — entered later via partnerships or
new data, not as the wedge.

## What each ICP activates (the build implications)

### ICP 1 — Home health / hospice / personal care
- **Detection:** elevated sector prior (already in `src/model_a/sector_priors.py`);
  prioritize impossible-day/overlapping-visit features when state EVV or visit-level
  data lands (09 §EVV); hospice live-discharge once Care Compare is ingested.
- **Personas (05-model-b):** field RNs/aides, case managers, schedulers, marketing
  liaisons, medical directors; high line-of-sight per the scheme-role matrix
  (`home_health_hospice_eligibility`, `evv_personal_care`).
- **Campaign angle (07):** patient-protection framing — "When understaffing becomes
  fraud", "When hospice admissions pressure creates FCA risk".
- **Data to procure first:** Market Saturation (P1 #5), Care Compare/PBJ (P2 #8),
  WARN for the top home-health states (P3 #13 — the monitor is built).

### ICP 2 — DME / orthotics / telehealth ordering
- **Detection:** DMEPOS file (P1 #3) powers `dme_ordering_md_concentration` and
  referring-MD↔supplier graph edges — the ring detector's natural prey; shell and
  co-location signals (built) are strongest in this sector.
- **Personas:** DME sales reps, intake/order-coordination staff, call-center
  supervisors, telehealth prescriber-network coordinators.
- **Campaign angle:** "When DME lead generation and telehealth ordering become
  Medicare fraud" — kickback-awareness content for sales personas.
- **Data to procure first:** DMEPOS (P1 #3), Open Payments (P1 #4).

## Decision consequences encoded in the repo
- `sector_priors.py` elevates home_health/hospice/personal_care (1.5–1.6) and dme
  (1.5) — the two ICPs sit at the top of the prior table.
- The first two landing pages / content hubs (Phase 4) are these typologies; HCC/MA
  pages wait.
- The first WARN states to load are the top states by our spending coverage in
  these sectors.

## Revisit trigger
Re-evaluate after the first 90 days of funnel data (counsel-qualified-lead rate by
typology), or immediately if MA-relevant data or a partner with MA access appears.
