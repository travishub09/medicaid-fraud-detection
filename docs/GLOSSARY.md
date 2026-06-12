# Glossary — Every Term, In Plain English

Alphabetical. Legal terms first explained as they matter *to this system*, not as
full legal definitions.

**AKS (Anti-Kickback Statute)** — federal law making it illegal to pay for
healthcare referrals (e.g., a device maker paying doctors to prescribe its
product). Many whistleblower cases are AKS cases.

**Audience (marketing)** — a *group definition* for advertising (e.g., "former
billing managers at home-health companies in Texas"), as opposed to a list of
named individuals. This system only ever outputs audiences on the people side.

**Backtest** — checking a model against history: "would it have flagged the
companies that later got caught?" Ours showed a 2× lift (see HOW_IT_WORKS).

**CCN (CMS Certification Number)** — the ID a *facility* (hospital, nursing
home, hospice) carries in Medicare's systems. Join key for facility data.

**CIA (Corporate Integrity Agreement)** — a compliance agreement a healthcare
company signs with the government after a fraud settlement. A prior CIA marks a
repeat-offender risk.

**CMS (Centers for Medicare & Medicaid Services)** — the federal agency running
Medicare and Medicaid; publisher of most of our data.

**Dossier** — this system's core output: a per-organization case file with the
risk pattern, the drivers, the network context, the dollar exposure, and the
innocent explanations that must be ruled out. A hypothesis, never an accusation.

**DME / DMEPOS** — durable medical equipment (wheelchairs, braces, supplies). A
perennial top fraud category and one of our two starting focus areas (ICPs).

**E/M codes** — "evaluation and management" billing codes for office visits,
leveled 1–5 by complexity. Billing too many high levels is **upcoding**.

**Entity resolution** — figuring out that different records refer to the same
real-world thing ("Acme Health LLC" = "ACME HEALTH, L.L.C." = NPI 123…). The
foundation of the whole system; done by `src/entity_graph`.

**ERV (Expected Recoverable Value)** — Model A's ranking number: (probability
the pattern is real fraud) × (dollars at stake). Lets us prioritize by value,
not just weirdness.

**EVV (Electronic Visit Verification)** — GPS/time logging that home-care
visits actually happened. Where available, it exposes "phantom visit" fraud.

**Exclusion / excluded party** — a person or company banned from billing federal
health programs (listed on the **LEIE**). Billing while excluded — or being
closely connected to an excluded party — is one of our strongest signals.

**FCA (False Claims Act)** — the federal law this whole business runs on: triple
damages for defrauding the government, and 15–30% of the recovery to the
whistleblower who brings the case. See also **qui tam**, **relator**.

**First-to-file** — only the *first* whistleblower to file on a given fraud gets
paid. Why speed and docket monitoring matter existentially.

**Graph (entity graph)** — the network map: organizations, owners, facilities,
exclusions, and the links between them (owns, shares address with, banned…).
Catches ring-shaped fraud invisible at the single-company level.

**HCPCS code** — the standardized code for a billed healthcare service or
product. Billing concentrated in one code = the "mill" pattern.

**HHI (Herfindahl index)** — a 0–1 concentration measure. HHI of billing
across codes near 1.0 = a single-service mill shape.

**ICP (Ideal Customer Profile)** — startup-speak for "the segment we focus on
first." Ours: (1) home health/hospice/personal care, (2) DME/telehealth. See
platform doc 11.

**Intervention** — the Department of Justice taking over a whistleblower's case.
Nearly all the money is in intervened cases, so predicting intervention is Model
C's main job.

**LEIE (List of Excluded Individuals and Entities)** — the OIG's published list
of banned parties. Our integrity backbone and our backtest's ground truth.

**Luhn check** — the checksum that validates an NPI is a real, well-formed
number. Every NPI entering the system is validated; failures are quarantined.

**MA (Medicare Advantage) / risk adjustment** — private-plan Medicare, paid more
for sicker-coded patients — making diagnosis inflation ("chart mining") the
biggest-dollar fraud category. Deliberately our *third* focus, not first.

**Model A / B / C** — the three models: A = which *organizations* look like
fraud (where), B = which *people* likely saw it and when they're reachable
(who/when), C = which resulting *cases* are worth financing (worth it).

**Noisy-OR** — the math for combining scheme scores: an org is risky if *any*
scheme fires, not the average of all of them.

**NPI (National Provider Identifier)** — the 10-digit ID every healthcare
provider (person or organization) has. The master join key of the claims world.

**NPPES** — the public registry of all NPIs (names, specialties, addresses).

**OIG (Office of Inspector General, HHS)** — the health-fraud watchdog;
publishes the LEIE and enforcement actions.

**Org node / canonical organization** — one real-world company in our graph,
after entity resolution has merged its NPIs, name variants, and locations.

**PAC ID** — the ID that groups a provider's Medicare enrollments in **PECOS**;
our most reliable signal that two NPIs are the same enterprise.

**PECOS** — Medicare's enrollment system; source of NPI↔organization links.

**Peer group** — the set of comparable providers (same specialty, similar
setting) a provider is measured against. Outliers only count vs. true peers.

**Percentile (one-sided)** — where a provider ranks among peers on a metric,
0–1, where only the *high* side is treated as suspicious.

**Public-disclosure bar / original source** — FCA rules: cases built on already-
public info get dismissed, unless the whistleblower has direct independent
knowledge. Why public data corroborates but insiders make the case.

**Qui tam** — the FCA mechanism letting a private person sue on the government's
behalf ("who as well for the king as for himself sues").

**Quarantine** — our handling of bad data: never silently deleted, always set
aside with a reason and counted in the QA report.

**Relator** — the legal term for the whistleblower who files a qui tam case.

**Robust z-score (median/MAD)** — measuring "how unusual" using medians, so a
few extreme values can't distort the baseline.

**Sector prior** — a multiplier reflecting how enforcement-dense a sector is
(hospice ≠ dermatology). Placeholder constants today; derived from the DOJ case
database as it fills.

**Shell pattern** — several thin, recently-created companies at one address —
the classic disposable-billing-entity shape. Detected by the graph.

**Surge lead** — a flagged organization that just had a layoff (WARN): a cohort
of potential witnesses entering the reachable 6–18-month window.

**Taxonomy code** — the NPPES code for a provider's specialty/type; how we
build peer groups and map providers to sectors.

**Tenure-overlap gate** — the hard rule in Model B: a person only counts as a
potential witness if they worked there *during* the anomaly window.

**TIN / EIN** — tax IDs; how the claims world identifies the corporate entity.

**Upcoding** — billing a more expensive code than the service justified.

**WARN notice** — the public filing employers must make before mass layoffs;
our free, timed signal that insiders just became reachable.

**Whale** — the rare huge-recovery case that carries a litigation-finance
portfolio's returns.
