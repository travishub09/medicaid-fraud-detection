# 02 — Data Sources (prioritized catalog)

Three jobs, and most data does one well: **find the relator** (people data), **find
the defendant** (claims/utilization), **corroborate** (claims, pricing, ownership).
Sources are tiered by leverage. For each: what it is, why it matters, its modeling
role, and — critically — its legal/usage constraint.

## Ingested today (in this repo)

| Source | Grain | Role | Where |
|---|---|---|---|
| CMS Medicaid Spending | billing×servicing NPI × HCPCS × month | Model A billing-outlier base | `spending_fact` |
| NPPES | one row per NPI | provider identity / taxonomy / address | `provider_dim` |
| PECOS | NPI × enrollment | NPI↔PAC↔enrollment crosswalk | `npi_xwalk` |
| OIG LEIE | one row per exclusion | integrity signal + ground-truth labels | `exclusions` |
| CMS All-Owners | facility × owner | ownership graph edges | `owner_edges` |

## Needed next (the immediate gaps)

- **Medicare Part B (Physician & Other Practitioners)** — the core billing-outlier
  file; upcoding, impossible-day, code-concentration, services-per-bene. Primary
  Model A feature source and the engine behind the public lookup tool. ~2-year lag →
  structural detection, not real-time.
- **Medicare Part D Prescribers** — controlled-substance/opioid over-prescribing,
  brand-over-generic steering, high-cost-drug concentration. Cross with Open Payments
  and NADAC.
- **Medicare DMEPOS** — supplier + referring-provider DME; ordering-physician HHI for
  ring/kickback shapes.
- **Open Payments** — manufacturer→physician payments; crossed with utilization it is
  the anti-kickback (AKS) correlation. Adds a `pays` edge to the graph.
- **Market Saturation & Utilization** — CMS geographic over-supply prior for
  home-health/hospice/DME; a sector-prior multiplier.
- **DOJ settlements + FCA stats / OIG actions + CIAs** — structure into a case DB:
  the scheme taxonomy, sector base rates, Model A enforcement prior + positive labels,
  and Model C's primary training labels.
- **SAM.gov exclusions** — government-wide debarment + entity registry (integrity +
  entity-resolution aid).
- **Care Compare / PBJ staffing / HCRIS** — facility quality, staffing-vs-acuity
  (worthless services), cost-report anomalies. Join by CCN.
- **People-data (People Data Labs / Apollo / ZoomInfo / LinkedIn / Revelio)** — the
  individual-resolution layer for Model B (roles, tenure, departures). Gated on
  licensing + FCRA review.
- **WARN notices** — free, timed, involuntary-departure cohort signal for Model B2.
- **Glassdoor/Indeed review text** — fraud-adjacent grievance NLP (weak Model A corroboration + Model B2 propensity).
- **PACER / CourtListener (RECAP)** — first-to-file intelligence, unsealed FCA
  outcomes (Model C labels), retaliation suits (Model B2's warmest leads). Continuous monitoring.

## Tier 2 — differentiated, constrained
- **T-MSIS Analytic Files (TAF)** — national Medicaid claims; the highest-fraud-density
  domain and far less mined than Medicare. **Constraint:** Research Identifiable Files
  carry a DUA restricting use to approved research; commercial litigation-targeting use
  is very likely barred. Treat as research-only unless counsel clears a specific
  permissible use; otherwise substitute published Medicaid files.
- **EVV (Electronic Visit Verification)** — visit-level home/personal-care logs with
  time + GPS → impossible/overlapping/phantom-visit detection. State-controlled access.

## Tier 3 — corroboration
Hospital/payer price transparency, state All-Payer Claims Databases, NADAC/SMAC drug
pricing, Form 990 / SEC / Secretary of State / licensing boards, USAspending,
professional-org rosters (AAPC/HCCA/AHIMA — the highest-credibility personas).

## Tier 4 — proprietary, when scaling
Komodo, IQVIA, Optum, Merative, HealthVerity, Definitive Healthcare, Datavant.
**Constraint:** these licenses almost certainly bar litigation-targeting use — at most
a corroboration/market-intelligence layer, never the case-building engine. Definitive
Healthcare (org-chart/affiliation) has the fewest restrictions. Do **not** build a hard
dependency on data you cannot lawfully obtain (CMS Preclusion List, restricted RIFs).

## Modeling-join cheat sheet
NPI joins Part B/D/DMEPOS/Open Payments to providers; CCN joins facility quality/cost
reports; TIN/PAC/Enrollment join NPIs to orgs (entity resolution); geography joins
Market Saturation; name+EIN+NPI join LEIE/SAM exclusions. The people world joins the
claims world **only at the canonical Org node** — see [03-entity-resolution.md](03-entity-resolution.md).
