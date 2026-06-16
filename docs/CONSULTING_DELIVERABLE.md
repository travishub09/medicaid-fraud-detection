# Healthcare Fraud Whistleblower Origination Platform
### Capabilities Briefing & Build Assessment

*Prepared by: ________________________  |  Date: ____________  |  Confidential*

---

This briefing describes a working software platform for originating, qualifying,
and underwriting qui tam (False Claims Act) healthcare-fraud cases. It is written
for a non-technical audience. It covers what the platform does, the capabilities
delivered to date, how it maintains legal and ethical defensibility, and the
specific steps required to bring it into production.

---

## Executive summary

Under the False Claims Act, a private whistleblower who exposes fraud against the
government may receive 15–30% of the funds recovered. Healthcare is the largest
category of these recoveries — several billion dollars annually, the majority
originating from whistleblower-brought cases.

The platform operationalizes this opportunity through three connected models:

- **Model A — Where:** reads public government healthcare data and ranks
  organizations whose billing and ownership patterns match known fraud schemes,
  producing a plain-English case file for each.
- **Model B — Who:** for a flagged organization, maps the job roles that would
  have witnessed the scheme into compliant marketing audiences.
- **Model C — Which:** underwrites each resulting case — the likelihood the
  government intervenes and the probable recovery — to direct financing to the
  strongest opportunities.

**Assessment of current state.** The core platform is built and verified by
approximately 220 automated tests, and runs end-to-end on demonstration data
today. Bringing it into production is principally a matter of **data procurement**
(loading public files) and a small number of **business and legal decisions**
(a people-data license, counsel sign-off), each itemized in Section 5. Every
output is an investigative hypothesis for human and legal review — never an
accusation — a principle enforced in the software itself.

---

## 1. The opportunity

Healthcare fraud is large, persistent, and expressed in identifiable patterns:
upcoding, billing for services never rendered, kickbacks, medically unnecessary
care, and hospice and home-health abuse, among others. The government relies on
insiders to surface these schemes and rewards them substantially for doing so.

The commercial challenge is not legal theory; it is execution against three data
problems: identifying the right organizations, identifying the right insiders,
and selecting the right cases to finance. The platform addresses all three while
operating inside strict legal and ethical boundaries — because the asset that
makes a case is a credible human witness, and the discipline that sustains the
business is trust.

---

## 2. Capabilities delivered

The platform comprises three models on a shared data foundation, plus the
supporting systems that feed and operate them.

### The shared foundation: entity resolution and the graph
Before any scoring occurs, the system establishes who is who and who is connected
to whom — which billing numbers belong to one company, who owns each facility,
who shares an address, and who sits near a party already barred from federal
programs. This relationship map is built and tested. It loads into Neo4j, a
visual database that lets an analyst explore ownership networks interactively,
and includes an advanced matching capability for reconciling inconsistently
recorded company names.

### Model A — organization risk and case files
Model A ranks organizations by expected recoverable value — the likelihood of
recoverable fraud multiplied by the dollars plausibly at stake — and produces a
case file for each. Every case file states a specific fraud-type hypothesis,
presents the supporting evidence and network context, estimates the dollars
specifically at issue, assigns a confidence level, checks whether the matter is
already public, and lists the innocent explanations that must be ruled out. The
scoring requires no prior labeled examples to begin; it operates from domain
knowledge immediately and improves as real case outcomes accumulate.

The system detects a broad library of schemes — including upcoding,
impossible-day billing, single-code mills, over-utilization, opioid diversion,
kickbacks, durable-medical-equipment rings, hospice ineligibility, worthless
services, geographic over-saturation, ownership and shell-company integrity,
billing under invalid provider identities, 340B pharmacy abuse, and cost-report
fraud. Each detector activates automatically when its data file is loaded.

A historical validation supports the approach: organizations the early system
ranked in its top decile were approximately twice as likely to be subsequently
barred by the government.

### Model B — witness identification (audiences)
For a flagged organization, Model B identifies the job roles that would have had
direct line of sight to the suspected scheme, weighs who was employed during the
relevant period, and estimates who is reachable. Its output is marketing
audiences defined by role, organization, and channel — never lists of named
individuals to solicit. That boundary is enforced in code: person-identifying
information is structurally prevented from leaving the system. The component is
built; it operates on licensed people-data only after a privacy review.

### Model C — case underwriting
Model C is the underwriting function that converts a pipeline of leads into a
financeable portfolio. For each case it estimates the probability of government
intervention, a recovery range, and a fund / pass / fund-with-terms recommendation
with capital and economic terms, then simulates the portfolio to characterize the
range of outcomes. It operates on transparent rules today and graduates to a
trained statistical model once real case outcomes accumulate.

### The acquisition funnel
The system to convert interest into qualified, counsel-ready leads: a website
analytics framework that automatically suppresses sensitive information, a lead
score that prioritizes outreach, and a confidential intake process that routes
any sensitive content to secure, attorney-reviewed handling. The public launch is
contingent on counsel sign-off.

### Live monitoring and lookups
The platform monitors external developments: a Department of Justice settlement
feed, a court-docket monitor that raises first-to-file alerts and surfaces
retaliation suits, a layoff monitor, and on-demand lookups against provider,
nonprofit, federal-award, and FDA records. All retrieved data is retained for
audit.

### Quality and safety
The system validates its own calculations at each step and halts rather than
emit an unreliable figure. Person-identifying exports are blocked in code. The
public lookup tool presents comparative percentiles and context, never a fraud
determination. Approximately 220 automated tests re-verify the system on every
change.

---

## 3. How the platform operates, end to end

Public data identifies and corroborates; it does not constitute the legal claim,
which rests with the human insider. The operating sequence:

> Model A ranks suspect organizations and produces case files. Model B maps the
> witness roles within them into compliant audiences. The funnel educates and
> converts those audiences into confidential, attorney-reviewed intake. Model C
> underwrites which resulting cases to finance. Outcomes feed back and improve all
> three models over time.

The compounding data advantage — each resolved case sharpening the next
prediction — is the durable competitive moat.

---

## 4. Legal and ethical posture

Defensibility is a design principle, not an afterthought:

- Outputs are hypotheses, never accusations; each case file includes the innocent
  explanations to rule out and a standing disclaimer.
- Model B produces role-based audiences; named-individual handling is gated behind
  counsel and a privacy review.
- The public lookup tool never labels any party fraudulent — only factual,
  comparative percentiles with benign explanations and a method disclaimer.
- No protected health information enters the code repository, and the platform has
  no dependency on any dataset whose license would bar this use.
- The analytics are conservative and self-checking, declining to produce figures
  they cannot support.

These properties are the foundation for working with relator-side counsel and for
any public-facing product.

---

## 5. Path to production

The remaining work is procurement, partnerships, and legal foundation — not
engineering. The sequence:

1. **Establish secure storage and load the core public data**, producing the
   first real ranked case files. *(Days, once data is in hand.)*
2. **Engage counsel** (False Claims Act, advertising, and privacy) for the
   sign-off that gates marketing, intake, and the public tool. *(In parallel.)*
3. **License people-data and complete the privacy review**, activating Model B
   and compliant audience construction. *(The principal origination unlock.)*
4. **Launch the education-first funnel and lookup tool**, generating inbound,
   self-qualifying leads.
5. **Route intake through partner counsel, underwrite with Model C, and finance
   the strongest cases.** Outcomes feed back and compound the advantage.

The items below are deliberate holds for legal or business reasons; in each case
the software is complete and the gate is an external decision.

| Gate | Unlocks | Requirement |
|---|---|---|
| People-data license + privacy review | Model B activation | Vendor selection + legal review |
| Advertising / defamation counsel | Public tool + marketing | Counsel sign-off |
| Accumulated case outcomes | Model C trained model | Data collection over time |
| Commercial data license | State exclusion-list feed | Purchasing decision |
| Secure storage agreement | Safe handling of large files | Standard cloud setup |

---

## 6. Summary

The platform delivers a working, defensible engine for the three hardest problems
in qui tam origination: finding the organizations, finding the witnesses, and
selecting the cases. The engineering is substantially complete and verified. The
path to revenue runs through data procurement, legal sign-off, and partnerships —
well-defined steps, each itemized above.

---

### Appendix — technical inventory

| Area | Detail |
|---|---|
| Core technologies | Python; DuckDB and pandas; NetworkX and Neo4j; LightGBM and scikit-learn; Splink |
| Verification | ~220 automated tests, all passing; synthetic-data generator (no sensitive data required) |
| Model A | scoring, scheme detectors, case files, supervised graduation |
| Model B | knowledge, propensity, reachability, audiences, person resolver |
| Model C | priors, features, underwriting, portfolio simulation, public-disclosure screen |
| Data adapters | ~14 government data loaders; live feeds (DOJ, dockets, exclusions, registry, nonprofits, awards, FDA) |
| Supporting | entity graph, enforcement database, sourcing monitors, analytics, funnel, lookup tool, text extraction |

*Detailed setup instructions accompany this briefing in the Setup Guide.*
