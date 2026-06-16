# Healthcare Fraud Whistleblower Origination Platform
## What Was Built — A Plain-English Briefing

*Prepared for the founding team and advisors. This document explains, without
technical jargon, what the platform does, what has been built, how it stays on
the right side of the law, and what remains. A companion document,
**SETUP_GUIDE.md**, gives the step-by-step instructions to run it.*

---

## Executive summary

We set out to build an intelligence-and-acquisition engine for **qui tam (False
Claims Act) cases** — the legal mechanism under which a private whistleblower who
exposes fraud against the government can receive **15–30% of what the government
recovers**. In healthcare alone the government recovered over $2.9 billion in a
recent year, the large majority from cases a whistleblower brought.

The platform does three things, in a chain:

1. **Finds where fraud concentrates.** It reads public government healthcare data
   and ranks organizations whose billing and ownership patterns match known fraud
   schemes — producing a plain-English case file ("dossier") for each.
2. **Finds who could be the whistleblower.** For a flagged organization, it maps
   the job roles that would have witnessed the scheme into compliant marketing
   *audiences* — never named individuals.
3. **Decides which cases are worth backing.** It underwrites each resulting case —
   the odds the government takes it and the likely recovery — to direct financing
   to the winners.

**Status today:** the core platform is built and verified by roughly 220
automated tests. It runs end-to-end on demonstration data right now. Turning it
on for real is mostly **data procurement** (downloading public files) plus a few
**business and legal decisions** (a people-data license, counsel sign-off) that
are described precisely at the end of this document. Critically, every output is
an **investigative hypothesis for human and legal review — never an accusation**,
and that principle is enforced in the software itself, not just in policy.

---

## 1. The opportunity

Healthcare fraud is large, persistent, and concentrated in identifiable patterns:
upcoding, billing for services never rendered, kickbacks, medically unnecessary
care, hospice and home-health abuse, and more. The government relies heavily on
**insiders** to surface it, and rewards them well for doing so.

The hard parts of the business are not legal theory — they are **finding the
right organizations, finding the right insiders, and choosing the right cases to
finance.** Each is a data and modeling problem. The platform is built to solve
all three while staying inside strict legal and ethical lines, because the asset
that makes a case is a credible human witness, and the discipline that keeps the
business alive is trust.

---

## 2. What was built

The platform is organized as three "models" (A, B, C) sitting on one shared map
of the healthcare world, plus the supporting machinery that feeds and operates
them.

### The shared map: entity resolution and the graph
Before anything can be scored, the system must know **who is who and who is
connected to whom** — which billing numbers belong to one company, who owns each
facility, who shares an address, and who sits near a party already banned from
federal programs. This "entity graph" is built and tested. It also loads into
**Neo4j**, a visual database, so an analyst can click through an ownership
network by hand, and includes an optional advanced matching engine (Splink) for
reconciling messy, inconsistently-spelled company names.

### Model A — Where is the fraud? (built)
Model A ranks organizations by **expected recoverable value** — the likelihood
of recoverable fraud multiplied by the dollars plausibly at stake — and writes a
dossier for each. Every dossier names a specific fraud-type hypothesis, shows the
evidence and the network context, estimates the dollars *specifically at issue*
(not just total billing), states a **confidence level**, runs a check for whether
the allegation is already public, and lists the **innocent explanations that must
be ruled out**. The scoring needs no prior "labeled" examples to start — it works
from domain knowledge on day one and sharpens as real case outcomes accumulate.

It detects a broad library of schemes — upcoding, impossible-day billing,
single-code "mills," over-utilization, opioid pill-mills, kickbacks, durable-
medical-equipment rings, hospice ineligibility, worthless services and
under-staffing, geographic over-saturation, ownership and shell-company
integrity, billing under deactivated or deceased identities, 340B contract-
pharmacy abuse, cost-report fraud, sudden ramp-ups, and clinical implausibility.
Each detector activates automatically when its data file is loaded.

A historical back-test is the proof point: organizations the early system ranked
in the top 10% were **twice as likely** to later be banned by the government.

### Model B — Who saw it? (built; gated on a data license)
For a flagged organization, Model B identifies the **job roles** that would have
had line-of-sight to the suspected scheme (a coder for upcoding, a sales rep for
kickbacks, a nurse for hospice abuse), weighs who was employed during the relevant
period, and estimates who is reachable and receptive — for example, someone who
recently left after a layoff. Its output is **advertising audiences (role × org ×
channel), never a list of named individuals to solicit.** That boundary is a
legal guardrail written into the code: person-identifying information is
structurally blocked from any export.

The hardest piece — matching a workforce record's employer to the right company —
is built. It runs only on licensed people-data after a privacy/FCRA legal review,
which is a business decision, not a missing feature.

### Model C — Which cases are worth backing? (built, cold-start)
Model C is the underwriting brain — the part that turns a portfolio of leads into
a financeable book. For a case in hand it predicts the **probability the
government intervenes** (which is nearly the whole difference between a recovery
and a zero), a **recovery range** (low / middle / high), and a **fund / pass /
fund-with-terms** recommendation with the capital to deploy and the take priced
to a target return — then simulates the whole fund to show the range of outcomes
and the chance of catching a large case. It runs on transparent rules today and
upgrades itself to a trained statistical model the moment real case outcomes
accumulate.

### The acquisition funnel (built; public launch gated on counsel)
The machinery to convert interest into qualified, counsel-ready leads: a website
event-tracking taxonomy that automatically suppresses sensitive information, a
composite lead score that prioritizes who to nurture, and a confidential intake
process that captures only what is appropriate and routes anything sensitive to
secure, lawyer-reviewed handling. The public launch waits on counsel sign-off.

### Live feeds and monitors (built)
The platform watches the outside world: a Department of Justice settlement
fetcher (which also trains the risk weights), a court-docket monitor that raises
**first-to-file alerts** (existential — only the first whistleblower recovers)
and surfaces retaliation lawsuits (the warmest leads), a layoff monitor, and
on-demand lookups against provider, nonprofit, federal-award, and FDA records.
Everything pulled is cached for an audit trail.

### Safety and quality (built)
The system checks its own arithmetic at every step and **stops rather than emit a
wrong number**. Person-identifying exports are blocked in code. The public lookup
tool shows comparative percentiles and context, **never a "fraud" verdict**.
Roughly 220 automated tests re-verify the whole system on every change, and a
dedicated review pass hardened the recent additions.

---

## 3. How it works, end to end

Public data finds and corroborates; it does **not** make the legal claim — the
human insider does. The flow:

> **Model A** ranks suspect organizations and writes dossiers → **Model B** maps
> the witness roles inside them into compliant audiences → the **funnel** educates
> and converts those audiences into confidential, counsel-reviewed intake →
> **Model C** underwrites which resulting cases to finance. Outcomes feed back and
> make all three models smarter over time.

The compounding data advantage — every resolved case sharpening the next
prediction — is the real moat.

---

## 4. Legal and ethical posture (built into the software)

This is not a bolt-on; it shapes the design:

- **Outputs are hypotheses, never accusations.** Every dossier carries the
  innocent explanations that must be ruled out and a standing disclaimer.
- **No accusations about people.** Model B produces role-based audiences; named
  individual handling is gated behind counsel and a privacy/FCRA review, and the
  "likely whistleblower at employer X" inference is treated as sensitive from the
  moment it exists.
- **The public lookup tool never labels anyone a fraudster** — only comparative,
  factual percentiles with benign explanations and a method disclaimer.
- **No protected health information** ever enters the code repository, and the
  system has **no dependency on any dataset whose license bars this use**
  (research claims files, commercial claims products) — by deliberate design.
- **The math is conservative and self-checking** — it refuses to produce a number
  it cannot stand behind.

These are the credibility foundations for working with relator-side counsel and
for any public-facing product.

---

## 5. Current status — what is live, what is gated

**Built and verified (runs today on demonstration data):** the entity graph and
Neo4j layer, Model A and all its detectors, the dossier product, Model C
cold-start underwriting, the acquisition-funnel machinery, the live feeds and
monitors, Model B's logic and the person-matching engine, and the full safety and
test infrastructure.

**Waiting on data you can download (no engineering required):** the detectors are
pre-built against the government's real file formats. Each one switches on the day
its public file is loaded. The full, prioritized shopping list — where to get
each file, whether it is free, and what it unlocks — is in **WHAT_WAS_BUILT.md**
and the click-by-click **Data Runbook**.

**Gated on a business or legal decision (the code is done; the gate is real-world):**

| Gate | Unlocks | What it takes |
|---|---|---|
| People-data license + FCRA/privacy review | Model B activation (the witness engine) | A vendor decision and a legal review |
| Defamation / advertising counsel sign-off | Public lookup tool + marketing launch | The "Phase-0" legal review |
| Accumulated case outcomes | Model C's trained (vs. rules) version | The DOJ backfill + time |
| A modest commercial license | A pre-built feed of ~45 state exclusion lists | A purchasing decision |
| Secure storage with a healthcare agreement | A safe home for large/sensitive files | Standard cloud setup (S3 + BAA) |

---

## 6. The path to first revenue

1. **Stand up secure storage and load the core public data** → produce the first
   real ranked dossiers. *(Days, once data is in hand.)*
2. **Engage counsel** (False Claims Act + advertising + privacy) for the Phase-0
   sign-off that gates marketing, intake, and the public tool. *(In parallel.)*
3. **License people-data and complete the FCRA review** → activate Model B and
   begin building compliant audiences. *(The key unlock for origination.)*
4. **Launch the education-first funnel and the lookup tool** → inbound,
   self-qualifying whistleblower leads.
5. **Run intake through partner relator-side counsel; underwrite with Model C;**
   finance the strongest cases. **Outcomes feed back and compound the advantage.**

The engineering is largely complete. The remaining work is procurement,
partnerships, and the legal foundation — the things that turn a built platform
into a operating business.

---

## Appendix — technical inventory (for a technical reviewer)

- **Languages/tools:** Python; DuckDB and pandas for data; NetworkX and Neo4j for
  the graph; LightGBM and scikit-learn for the trained models; Splink for
  probabilistic matching.
- **Scale of build:** ~220 automated tests, all passing; a synthetic-data
  generator so the whole system is verifiable without any sensitive data.
- **Major components:** `entity_graph` (resolution, graph features, rings, Neo4j
  export, person resolver), `model_a` (scoring, schemes, dossiers, supervised
  graduation), `model_b` (knowledge, propensity, reachability, audiences),
  `model_c` (priors, features, underwriting, portfolio, public-disclosure screen),
  `ingest_cms` (~14 data adapters), `feeds` (DOJ, dockets, exclusions, registry,
  nonprofits, awards, FDA), `enforcement`, `sourcing`, `analytics`, `funnel`,
  `lookup_tool`, `nlp`.
- **Deeper documentation:** `docs/HOW_IT_WORKS.md` (the concepts),
  `docs/platform/` (full architecture and component specs), and
  `docs/platform/12-data-runbook.md` (the data click-by-click).
