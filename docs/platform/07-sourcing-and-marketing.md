# 07 — Relator-Sourcing Engine, Marketing & Intake

Operating frame, repeated because it governs everything: **public data finds
defendants and corroborates a story. It does not make the claim.** A case built mostly
from public data weakens original-source standing and invites the public-disclosure
bar. The asset that finds the actual human is people-and-role data cross-referenced
against where fraud structurally concentrates. That is the engine.

## Who you are looking for
The ideal relator sits at the intersection of four traits: **knowledge** (firsthand
line of sight), **documentation** (access to records that evidence it), **credibility**
(senior enough to be believed, not so senior they architected the fraud), and
**motivation** (recently exited, passed over, retaliated against, ethically distressed).

Personas, ranked:
- **Tier A — highest credibility + documentary trail:** compliance officers, internal
  auditors, revenue-integrity managers; medical directors and UM physicians/nurses.
- **Tier B:** billing/RCM managers and certified coders; DME/lab/genetic-testing/pharma
  sales reps; pharmacists, techs, PBM/340B analysts.
- **Tier C:** home health/hospice clinical staff; data/EHR analysts; finance staff (high
  value, high culpability risk).
- **Force multiplier:** former staff of billing companies, MSOs, RCM vendors, coding
  firms, EHR consultancies.

## Departure & distress detection
Sweet spot **6–18 months post-departure**. Signals: title/employer change and
open-to-work; abrupt short-tenure exits; WARN cross-referenced to rosters; employment
litigation (the warmest leads); fraud-adjacent review language; license-board changes.
Sources: LinkedIn, WARN, PACER/state dockets, Glassdoor/Indeed, licensing boards.

## Enforcement-sector overlay (priority)
Managed care / MA risk adjustment; home health / hospice; DME/orthotics/genetic testing
(telefraud); clinical labs; behavioral health / SUD (patient brokering); skilled
nursing; pharmacy / PBM / 340B; telehealth rings; personal-care / EVV (often state-FCA
territory).

## Lead score
```
Lead Score = Sector_Fraud_Base_Rate
           × Defendant_Outlier_Signal     (Model A)
           × Persona_Credibility          (Model B1)
           × Reachability                 (Model B2 + contactability)
           × Recovery_Magnitude           (Model A magnitude)
```

## Marketing motion (education-first, not bounty ads)
Recruit through **typology-specific, education-first content**, not accusations.
Advertise around "concerns", "billing pressure", "unsupported diagnosis capture",
"Medicare/Medicaid compliance questions". Convert through a **trust architecture**:
confidentiality, attorney review, safe-document guidance, anti-retaliation education,
transparent economics.

The **public billing-risk lookup tool** ([scaffold](../../src/lookup_tool/)) is the
SEO front door and top of funnel — explainable peer-relative percentiles with named
drivers, never a fraud label.

## Conversion funnel by persona (illustrative — coders)
Awareness (SEO on coding ethics) → Education ("The Medical Coder's Rights Under the
FCA") → Engagement (anonymous "is this actually fraud?" self-assessment) → Conversion
(secure intake: type of fraud, duration, approximate dollars) → Nurture (confidentiality
+ the under-seal process). Clinical staff lead with patient-safety framing; compliance
officers convert fast once decided; retaliation leads are time-sensitive and aggressive.

## Intake & qualification workflow (counsel-mediated)
1. Anonymous/low-identification pre-screen: role, sector, program, timeframe, type of
   concern, firsthand vs. not.
2. **Safety screen:** no PHI through generic forms; warn against unauthorized access;
   flag privileged/confidential materials.
3. **Triage score:** persona evidence-proximity, program linkage, systemic nature,
   damages proxy, originality, credibility risks.
4. **Counsel review:** route promising leads to FCA counsel before deep factual
   development where privilege may matter.
5. **Corroboration plan:** claims data, provider IDs, documents, witnesses, public
   enforcement lookalikes, damages model.
6. **Relator readiness:** risk tolerance, employment status, retaliation concerns,
   document access, timeline.
7. **Case packet:** counsel-ready memo — target entities, theory, public-data anomalies
   (Model A), witness timeline, evidence map, damages range.
8. **Funding review:** only after legal structure is designed; avoid funder control over
   strategy or settlement.

## Compliance guardrails (recap)
Don't promise recovery/intervention/job protection/specific share. Don't encourage
unauthorized access, scraping, taking PHI, or disclosing privileged material. Avoid PHI
at top of funnel. Keep data-derived risk scores separate from legal conclusions of
fraud. Respect state bar advertising/referral rules. Document consent, privacy policy,
retention, and deletion. Full detail in [01-legal-compliance.md](01-legal-compliance.md).

## Current state in this repo
Specified here; the public lookup tool is scaffolded (`src/lookup_tool/`). The content
hub, landing pages, CRM/nurture, analytics instrumentation, and the privileged intake
system are not built (Phases 4–5).
