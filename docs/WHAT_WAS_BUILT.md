# What We Built, What Data to Get, and How to Turn It On

> **Two companion deliverables (also rendered as PDFs):**
> [**SETUP_GUIDE.md**](SETUP_GUIDE.md) — the numbered, do-this-then-this setup
> instructions for a non-technical owner; and
> [**CONSULTING_DELIVERABLE.md**](CONSULTING_DELIVERABLE.md) — the polished,
> shareable overview of everything that was built. This file is the quick
> reference that ties them together.

The single briefing document, in plain English. No coding knowledge needed to
read it. It covers three things: **what the platform is and what we built**,
**exactly what data to procure (and where)**, and **the step-by-step to turn it
on**. Every section links to a deeper guide if you want detail.

_Current state: 19 work packages, ~220 automated tests, all passing. The core
platform is built; what remains is mostly data procurement and a few
legal/business decisions (spelled out at the end)._

---

## 1. What this system is, in three sentences

It reads **public government healthcare data** to find organizations whose
billing and ownership patterns match known fraud schemes, ranks them by how much
money is plausibly at stake, and writes a plain-English **case file (dossier)**
for each one. It is built to then find the **insiders** who witnessed the fraud —
because under the False Claims Act a whistleblower with firsthand knowledge can
win 15–30% of what the government recovers — and to **underwrite** which resulting
cases are worth financing. Every output is a hypothesis for human and legal
review, never an accusation. (Full story: [HOW_IT_WORKS.md](HOW_IT_WORKS.md).)

The business is three chained models on one shared map:
- **Model A — where:** ranks suspect organizations and writes the dossiers.
- **Model B — who:** turns a flagged org into compliant marketing *audiences* of
  the roles that would have witnessed the scheme (never named call lists).
- **Model C — which:** underwrites which cases the government would likely take
  and what they could recover, so financing goes to the winners.

---

## 2. What we built (plain English, grouped)

**The detection core (works today on the core data):**
- **Data cleaning & integration** — validates every provider ID, attributes
  every dollar to the right biller, and quarantines corrupt data rather than let
  it poison results (it walled off $20.7 trillion of fake values in the raw
  spending file).
- **The entity graph** — the "who is connected to whom" map: which billing
  numbers are one company, who owns what, who shares an address, who sits near a
  banned party. Now also loads into **Neo4j** so an analyst can click through
  ownership networks, and has an optional **Splink** engine for fuzzy
  company-name matching.
- **Model A — the scorer** — combines everything into a ranked list by *expected
  recoverable value* (likelihood × dollars), each org with a named fraud-type
  hypothesis, a confidence band, a dollars-at-issue estimate, and a list of
  innocent explanations that must be ruled out.
- **Dossiers** — the product: a per-target case file with the evidence, the
  network context, the scoped dollars, the public-disclosure check, and the
  mandatory benign explanations.

**The fraud detectors (each lights up when its data file lands):** upcoding,
impossible-day, one-code mills, over-utilization, drug/opioid pill-mill, kickback,
DME rings, hospice ineligibility, worthless services / understaffing, market
saturation, ownership/shell integrity, billing under a deactivated or deceased
ID, 340B contract-pharmacy abuse, cost-report fraud, rapid ramp-ups, and
specialty/clinical implausibility. The loaders for all of these are pre-built and
tested against the government's real file formats — they are downloads, not
engineering.

**Model B — the witness mapper (built; gated on a data license):** for a flagged
org, which job roles would have seen the scheme, who was employed during the
window, and who is reachable — output as advertising audiences. The
person↔employer matcher is built; it only runs on real people-data after a
privacy/FCRA legal review.

**Model C — case underwriting (built, cold-start):** predicts the chance the
government intervenes, a recovery range (low/middle/high), and a **fund / pass /
fund-with-terms** recommendation with capital and take, plus a portfolio
simulation of the whole fund. It uses transparent rules today and upgrades itself
to a trained model the moment real case outcomes accumulate.

**The acquisition funnel (built; public launch gated on counsel):** the
website-event taxonomy with automatic suppression of sensitive data, a composite
lead score that prioritizes who to nurture, and confidential intake triage that
routes any sensitive content to secure, lawyer-reviewed handling.

**The live feeds & monitors (built):** Department of Justice settlement fetcher,
court-docket monitor (first-to-file alerts + retaliation-suit leads), layoff
(WARN) monitor, plus on-demand lookups (provider registry, nonprofit 990s,
federal awards, FDA recalls). All cache what they pull for an audit trail.

**Safety rails everywhere:** the system checks its own math at every step and
stops rather than emit wrong numbers; person-identifying exports are structurally
blocked; the lookup tool shows percentiles and context, never a "fraud" verdict;
~220 automated tests re-verify all of it on every change.

---

## 3. See it work in 15 minutes (no data needed)

```bash
git clone https://github.com/travishub09/medicaid-fraud-detection.git
cd medicaid-fraud-detection
pip install -r requirements.txt
make test       # ~220 checks against planted fraud patterns — all should pass
make demo       # runs Model A on synthetic data, writes example dossiers
```
Then open `/tmp/demo/dossiers/001_*.md` — that file is the product. To see the
underwriting end, run `python3 -m src.model_c --fixture --out /tmp/mc` and read
`/tmp/mc/memos/`. Full walkthrough: [GETTING_STARTED.md](GETTING_STARTED.md).

---

## 4. The data shopping list (what to procure, in priority order)

Everything goes in one folder, `~/Desktop/data/preclean/`. The **click-by-click
instructions** (exact website, file, filename, how to verify) live in the
**[Data Runbook](platform/12-data-runbook.md)**; this is the prioritized summary.
"Free" means a public download or free signup. "Powers" means what it switches on.

### Tier 0 — the five core files (get these first; the system runs end-to-end)
| File | Where | Free? | Powers |
|---|---|---|---|
| **NPPES** (provider registry) | download.cms.gov/nppes | Free | Who every provider is — the identity backbone |
| **LEIE** (exclusion list) | oig.hhs.gov/exclusions | Free | Who is banned — the strongest red flag + our accuracy yardstick |
| **PECOS** (enrollment) | data.cms.gov | Free | Which billing numbers are one enterprise |
| **CMS All-Owners** | data.cms.gov | Free | Who owns each facility — the network map |
| **Medicaid Spending** | your Medicaid data arrangement | Arranged | Who billed what — the dollars everything is ranked by |

→ Then your technical helper runs `make pipeline` → `make graph` → `make model-a`
and you have your **first real ranked dossier list**.

### Tier 1 — Medicare files that light up most detectors (all free, data.cms.gov)
| File | Powers |
|---|---|
| **Part B** (by Provider & Service), ~3 yrs | upcoding, over-utilization, one-code mills, the lookup tool |
| **Part D** (by Provider & Drug), ~3 yrs | brand-steering, high-cost-drug schemes |
| **DMEPOS** (by Referring Provider), ~2 yrs | medical-equipment fraud (focus area) |
| **Open Payments**, ~3 yrs | the kickback signal (drug-maker payments vs. prescribing) |
| **Market Saturation** | home-health/hospice over-supply (focus area) |
| **SAM exclusions** | government-wide bans beyond the health list |

### Tier 1b — two free signups (5 minutes, unlock the live feeds)
- **CourtListener API token** (courtlistener.com) → first-to-file docket alerts +
  retaliation-suit leads.
- **SAM.gov API key** (sam.gov) → automated exclusion refresh.
*(The DOJ settlement feed and the provider/nonprofit/awards lookups need no key.)*

### Tier 2 — the extra free files (each switches on its named detector)
All free; drop in and the detector activates. Detail in the runbook §1.6–1.8.
| File | Powers |
|---|---|
| **Part D Opioid Prescriber** | pill-mill / diversion |
| **NPPES deactivation file** | billing under a deactivated ID |
| **HRSA 340B OPAIS** | contract-pharmacy abuse |
| **Provider of Services (POS)** | "impossible org" (billing beyond capacity) |
| **Order & Referring** | DME ordered by ineligible referrers |
| **PBJ staffing + Care Compare** | worthless services, hospice ineligibility |
| **NADAC drug pricing** | drug markup/spread anomalies |
| **HCRIS cost reports** | cost-report fraud |
| **DocGraph shared-patient** (archived) | referral rings (closed kickback loops) |
| **Census county population + ZIP→county** | "more patients than the county holds" |
| **SSA Death Master File** | billing under a deceased provider |
| **State licensing boards** | provider discipline beyond the federal ban list |

### Tier 3 — needs a decision or a purchase (not a download)
| Item | Why it matters | What it takes |
|---|---|---|
| **People/employment data** (Apollo now; People Data Labs later) | Activates Model B — the witness engine, the heart of the business | A license **plus** a privacy/FCRA legal review first |
| **OpenSanctions license** | Pre-built feed of ~45 state exclusion lists (saves building 45 scrapers) | A modest commercial license (the loader is already built) |
| **DOJ 10-year backfill** | Makes the sector risk weights real + trains Model C | Mostly a run + time (the fetcher is built) |
| **Secure storage with a healthcare agreement (S3 + BAA)** | A safe, shareable home for large/sensitive files | Travis sets up; grants Trey access |

**Never acquire:** T-MSIS research files and commercial claims products
(Komodo/IQVIA and similar) — their licenses legally bar this use, and the system
is deliberately built with no dependency on them.

---

## 5. How to turn it on — the order of operations

A non-technical owner coordinating with one technical helper. Each step is short.

1. **Stand up storage.** Travis creates the S3 bucket with the healthcare
   agreement (BAA) and gives Trey access. No protected health information ever
   goes into the code repository.
2. **Get the Tier-0 files** (table above) into `~/Desktop/data/preclean/`.
3. **First real run.** Helper runs `make pipeline → make graph → make model-a`.
   You get a ranked dossier list. Read three dossiers
   ([READING_THE_OUTPUTS.md](READING_THE_OUTPUTS.md) explains every line).
4. **Add Tier-1 files** and re-run — the fraud detectors and the lookup-tool data
   light up. Add the two Tier-1b signups and the live feeds start.
5. **Add Tier-2 files** as you procure them — each one switches on its detector
   automatically; nothing to re-engineer.
6. **Run Model C** on the ranked orgs (`make model-c`) for the
   fund/pass/fund-with-terms view.
7. **In parallel, start the business/legal track** (Section 6) — that, not more
   code, is what unlocks Model B and the public launch.

The first real-data run will surface real-world data quirks worth fixing — that
is the right trigger for the next engineering session, not before.

---

## 6. What's gated, and why (the non-code work)

These are deliberate holds for legal/safety reasons. The **code is built**; the
gate is a real-world decision, not a missing feature:

- **Model B activation** needs the people-data license **and** an FCRA/privacy
  legal review — the "likely whistleblower at employer X" inference is sensitive
  the moment it exists, so it never runs on real people until counsel clears it.
- **The public lookup tool and the marketing funnel** need defamation/advertising
  counsel sign-off before launch (the "Phase-0" review).
- **Model C's trained version** needs real case outcomes to accumulate; until
  then it runs on transparent cold-start rules.
- **The DOJ backfill, owner-file snapshots, and file procurement** are runs and
  downloads on your side that progressively sharpen everything.

---

## 7. Next steps, in order

**This week (Trey + Travis):**
1. Stand up the S3 bucket with BAA; grant access.
2. Download the Tier-0 files; do the first real run.
3. Start the DOJ backfill (even an afternoon helps) and begin keeping monthly
   owner-file snapshots (un-gates ownership-churn detection later).

**This month (business, not code):**
4. Engage counsel (False Claims Act + advertising + privacy) — the Phase-0
   sign-off that gates marketing, intake, and the lookup launch.
5. Decide the people-data path (we have Apollo month-to-month now; plan the
   switch to People Data Labs) and commission the FCRA review.
6. Decide on the OpenSanctions license (cheap win for state exclusions).

**Next engineering sprint (after the first real-data run):**
7. Tune from reality — fix the data quirks the first run surfaces.
8. Run the trained-model graduation once outcomes accumulate.

Full component status: [platform/14-analytics-expansion.md](platform/14-analytics-expansion.md)
and [platform/15-new-sources-and-models.md](platform/15-new-sources-and-models.md).
