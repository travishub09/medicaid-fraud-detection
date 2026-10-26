# The Fraud-Origination Model: a briefing for the new analyst and for Brad

_Prepared 2026-10-26 as the hand-off document for the deck. It is written at three
altitudes on purpose: the plain story anyone in the room can follow, the operating
picture a business reader needs, and the nuts and bolts a technical analyst will want.
Each section flags its altitude. Numbers come from the repository's own run records
(v6 to v26, docs/ACCURACY_READOUT_*.md) and its code; nothing here is estimated from
memory. Every output of this system is an investigative lead for human and legal
review, never an accusation._

---

## 1. The story in one minute (everyone)

The federal government pays hundreds of billions of dollars a year to doctors, labs,
clinics, pharmacies and home-care agencies through Medicaid and Medicare. A small share
of those providers bill for care they never gave, bill for more than they gave, or pay
for referrals. The government publishes enormous amounts of data about who billed what,
and it also publishes who got caught: exclusion lists, settlements, convictions.

Under the False Claims Act, a person with firsthand knowledge of fraud (a "relator", in
everyday words a whistleblower) can file a case on the government's behalf and receive
15 to 30 percent of whatever the government recovers. Most such cases come from
employees who saw it happen.

We built a system that reads the public data, learns from the providers who were
caught what fraud looks like in billing patterns, and ranks every provider in the
country by how much its pattern resembles the caught ones. It writes a plain-English
case file for the strongest candidates, points investigators at the kinds of employees
who would have seen the scheme, and estimates what a case would be worth. Think of it
as a detective that has read every past case and now reads this year's billing records
looking for the same fingerprints.

The core question we have been answering since the summer is the hard one: **does it
actually work?** The answer, as of this week, is yes, measurably, with a method we can
defend, and with a clear view of what it cannot yet do.

## 2. Why this is worth building (business)

- **The asset is the insider, not the data.** Public data finds where fraud signal
  concentrates and corroborates; it never makes the legal claim. The model's job is to
  make the search for relators ten times more efficient than cold outreach.
- **Three chained models on one shared map.** Model A (where): rank organizations and
  write dossiers. Model B (who): turn a flagged organization into compliant marketing
  audiences of the roles that would have witnessed the scheme (never a named call list;
  this is where the legal guardrails bite). Model C (which): underwrite which cases the
  government would likely take and what they could recover, so financing goes to the
  winners. Model A is production; B and C are built and gated on data licensing and
  counsel review.
- **The moat is the label and the plumbing, not the algorithm.** The learning
  algorithm is a standard gradient-boosted tree model. What is proprietary is the
  training label we assembled (section 7), the 44-source feature factory that turns
  raw government files into comparable provider-level signals, and the evaluation
  harness that proves the thing works without fooling ourselves.

## 3. How the system is put together (both altitudes)

**Plain version.** Five pieces in a line. (1) Pull in public files. (2) Figure out which
records are the same provider or company (the "map"). (3) Compute, for every provider,
a few hundred numbers describing how it bills compared with its peers. (4) Teach a
model which patterns belonged to providers that were later caught. (5) Rank everyone,
write up the top of the list, and send the names out for research.

**Technical version.**

1. **Ingestion (44 sources).** Medicaid provider spending (the core billing fact, about
   230 million rows, read as all-string Parquet so identifiers keep their leading zeros),
   NPPES (the provider registry), PECOS (Medicare enrollment, ownership and reassignment),
   the Medicare physician, Part D and DMEPOS public-use files, Open Payments (pharma
   payments to doctors), nursing-home staffing and inspection files, hospice and home
   health quality files, cost reports, market-saturation files, the NDC drug file, the
   Medicaid enrollment file, the Census, NADAC drug prices, and the enforcement feeds:
   the OIG exclusion list, CMS revocations, SAM, 17 state Medicaid sanction lists, the
   OIG corporate-integrity-agreement roster, and a DOJ press-release harvest of 16,234
   usable fraud cases. Every join asserts row and dollar conservation and hard-fails
   otherwise; bad rows are quarantined, never deleted.
2. **The entity graph.** Providers, organizations, owners, addresses and exclusions as
   nodes; membership, ownership, reassignment and referral as edges. It answers "which
   NPIs are the same business", carries fraud proximity (how close a provider sits to a
   known offender through ownership), and is rebuilt "as of" a date so a backtest never
   sees the future.
3. **The feature matrix.** One row per billing provider: 617,062 rows by 413 columns.
   Columns are raw statistics, peer percentiles (every billing metric is compared with
   the provider's own specialty and state, one-sided, robust to outliers), and 27 named
   scheme subscores: upcoding, phantom "impossible day" billing, pill mills, DME rings,
   pharma kickbacks, hospice ineligibility, worthless nursing-home services, lab
   unbundling, personal-care inflation, session inflation, rapid ramp-up, ownership
   integrity, and so on. Each subscore has named inputs and weights, so every score ships
   with its drivers (explainability is a hard rule, for defamation safety and for
   counsel's credibility).
4. **The scorer.** A LightGBM gradient-boosted classifier trained on the label, scored
   out of fold (five folds grouped so an organization never trains on itself; ten random
   seeds averaged), with a per-case budget so one large settlement with forty NPIs does
   not teach the model that forty independent frauds exist. Scores roll up to
   organizations with a dollar-weighted score, a calibrated probability, named drivers
   and a damages estimate.
5. **The books and the dossier.** The main list; the pursuit book (the operator class:
   no hospital systems, no 25-plus-provider institutions, no government, no solo
   individuals: the class a whistleblower strategy can actually approach); the
   large-biller book; and the dossier (the top ten written twice, a short story and a
   technical picture, with one public-records research prompt per target that an AI
   research agent runs).
6. **The harness around it.** A release registry with pre-registered checks (the pass
   bars are written down before a run, so results cannot be re-interpreted after the
   fact); the accuracy diagnostics; scheme health; the learning loop; a run-issue
   ledger; a data scanner; an operator reminder engine. About 1,260 automated tests run
   on synthetic data (no real data is ever committed).

## 4. How we know whether it works (the part to teach slowly)

**The 8th-grade version.** Imagine you want to test a weather forecaster. You cannot
wait for next year, so you give the forecaster all the data up to December 2021, hide
everything after, ask for a forecast, and then check it against what really happened in
2022 to 2024. That is exactly what we do. We "freeze" the world at December 2021,
rebuild every file as it was then, train the model, take its top 50, 100 and 250
organizations, and count how many of them the Department of Justice or the OIG
announced a fraud case against in the following three years. Then we ask: how many
would you get by picking organizations at random? If the model finds eight where random
would find two, the model is doing something real.

**The ruler.** The forward ruler is the set of organizations with a resolved federal
fraud outcome (a settlement, a judgment, a conviction, a plea) or a fraud-shaped
exclusion announced after the freeze. We grade only on outcomes, never on
accusations. The 2021-12 freeze has 611 such positives inside the scored universe; the
second freeze (December 2023, 33-month window) has 273.

**The checks** (pre-registered before each run, enforced by the registry):

- hits at 50, 100 and 250 on the pursuit book, each with a one-sided p-value against
  random draws from the same pool, and a precision interval;
- the large-biller book graded on its own;
- stability: three re-shuffles of the training folds, and the share of the top 50 that
  persists (the "Jaccard"; the bar is 0.6);
- the NPI-grain lift: how many future cases sit in the top 1 percent of individual
  providers, against chance;
- a score-scale sanity check (a model that calls nearly everyone "clean" is broken no
  matter how many hits it has: we learned this the hard way);
- lead time (how many months before the public record the model ranked the
  organization), institutional share of the top 50, and policy facts (the label tier,
  the freeze, the number of seeds).

A run is PROMOTE only if every check passes; one miss is HOLD. No HOLD run's dossier
spends research credits without the operator's explicit word.

## 5. Where the numbers stand (business, with the technical table)

The current production list (v25, frozen at December 2021, no class priors, ten seeds):

| budget | hits | random would give | one-sided p |
|---|---|---|---|
| top 16 | 2 | 0.1 | |
| top 50 | 2 | 0.4 | 0.047 |
| top 100 | 3 | 0.7 | 0.030 |
| top 250 | 6 | 1.8 | 0.004 |

Every hit is at least six months ahead of the public record (median 22 months), the
top 50 contains zero institutions, 31 of the 50 persist across re-shuffles, and the
top 1 percent of individual providers carries 8.7 times the random rate of future cases.

The replication (v26, frozen at December 2023, graded on 2024 to 2026 cases, a matrix
two years newer):

| budget | hits | random | p |
|---|---|---|---|
| top 50 | 2 | 0.2 | 0.014 |
| top 100 | 5 | 0.4 | under 0.001 |
| top 250 | 6 | 0.9 | under 0.001 |

NPI top-1-percent lift 15, the best of the series. The two-freeze comparison report
finds eight of eight comparable checks holding on both freezes. Plain words: the method
finds future federal fraud cases at roughly ten times the random rate on two different
snapshots of the world.

What the hits are: national labs and health systems that later settled qui tam cases
(LabCorp, BioReference, Genomic Health, Precision Toxicology, Cleveland Clinic, Community
Health Network, Providence, Dana-Farber, Tactile Systems, Medline). That tells us where
the model's demonstrated precision lives: chains and large billers. Inside the
owner-run class (the small agency with one owner, the class the whistleblower strategy
most wants) the model is close to chance, and the reason is the label, not the
features: 83 percent of convicted individuals never billed under their own NPI, so the
cases that would teach the model that class are invisible to a billing join. That is
the single largest open problem and section 8 says what we are doing about it.

## 6. The journey, run by run (the progress story Brad asked for)

Every run is a dated read-out in the repository. The series had three phases.

**Phase 1, getting to a valid measurement (v6 to v11, late September to mid October).**
The first grades were a HOLD for the right reasons: the top 50 was full of hospitals
and dollar-capped rows, the individual-provider ranking was a 285-way tie. v8 and v9 were
pipeline errors, not model results (the runner dropped the case labels; a stale runner
file was launched), and both were caught by the harness and written up as rules. v10 was
the first valid run: 2 hits at 50 against 0.3 random. v11 added the memory-safe
database layer and the regularized scorer and was the first complete run (40 sources).

**Phase 2, the VM and the label (v12 to v20, mid to late October).** Runs moved to a
cloud machine (16 cores, 128 GB) so the full graph embeddings and every source could
run; cost about a dollar an hour. The label was audited and expanded: the OIG
corporate-integrity roster resolved to enrolled providers, convicted individuals tied
to the billers whose claims carried them, 843 name-only exclusions resolved to NPIs,
the big-state sanction lists loaded, an AI research agent dispatched 212 tasks to
resolve defendants the public record names without identifiers (92 percent of the
verified sample confirmed). A platform audit of the code and the data found twelve
high-severity defects (a nine-month data leak in one market file, post-cutoff editions
inside the freeze, silent-blank fields) and fixed them in three batches; the as-of
versions of five billing scores were built so a frozen run computes what a live run
computes. v17 was the first run to pass the 100 budget (5 hits).

**Phase 3, the hard lessons and the convergence (v21 to v26, 22 to 26 October).** We
added class priors (the base rate of fraud per specialty as a feature) and the lists
looked better than ever (3 hits at 16). Three runs later the reason surfaced: the
encoder leaked the training label into its own feature, the model memorised it, and
the list was a ranking of specialties, not of providers. We proved it on a synthetic
twin, fixed it, added the score-scale check that would have caught it, and found that
honestly encoded priors add nothing. Then we found the diagnostics' refit used a
different fold design than the shipped model and aligned them. The four honest runs on
the first freeze (v17, v19, v24, v25) read as one model within noise. v25 was frozen as
the baseline (a dated code tag plus an operator-pinned pointer the tools read), and v26
replicated the method on the second freeze.

Honest tally of the whole series: 26 run tags, 17 written read-outs, 5 defects that
would have corrupted a result caught by the harness before anyone acted on it, 0 runs
dispatched for research until the method held on two freezes. The dispatch of the v26
dossier is the first.

## 7. The data assets we now own (business, with specifics)

These exist because the model was built; they have value independent of any one run.

- **The label.** A fraud label is the scarcest thing in this field: nobody publishes a
  list of fraudulent NPIs. Ours is assembled from the OIG exclusion list tiered by
  statute (fraud-shaped versus license or paperwork), CMS revocations, SAM, 17 state
  Medicaid sanction lists tiered by stated reason, Medicaid enrollment terminations for
  cause (one million episodes read, state bulk codes demoted), the DOJ and OIG
  press-release harvest (16,234 cases, dated, typed civil or criminal, with the
  settlement dollars), a defendant resolver that turns press-release names into NPIs
  under strict identity gates (4,066 resolved NPIs), the corporate-integrity roster
  (185 of 339 parties resolved), convicted individuals tied to the billers whose claims
  carried them, and research-agent answers verified by sample. Every row carries a
  tier, a basis, a date and provenance. The strict export (5,155 rows, 4,672 NPIs,
  time-boxed conduct windows, a written policy) is the file a claims-data partner can
  join on. The training label is 2,470 hard-tier positives at the 2023 freeze.
- **The feature matrix** (617,062 providers by 413 columns, with a data dictionary and
  a leakage manifest) and the as-of versions at two freezes.
- **The entity graph** (providers, organizations, owners, addresses, exclusions,
  referral and reassignment edges, embeddings), current-day and as-of.
- **The forward rulers**: every post-freeze federal outcome resolved to the universe,
  the thing any future model (ours or a partner's) is graded against.
- **The research corpus**: the dossiers' public-records research, the lane answers,
  the verification verdicts.
- **The method and its record**: the pre-registration registry, 26 run rows with
  their checks, the ledger of every defect and its fix.

## 8. What is next, and how the model feeds the PurpleLab proof of concept

**The PurpleLab POC.** PurpleLab holds claims-level data: individual claim lines with
diagnosis, procedure, rendering provider, dates, and the patient dimension we do not
have in public aggregates. Our public-data model is bounded by its aggregates (monthly
totals per provider and code). The POC asks whether claims-level detail lifts
precision beyond that ceiling, and the model gives the POC three things it would
otherwise lack:

1. **The label and the ruler.** A claims partner has rows; they do not have 4,672
   providers with a tiered, dated, provenance-carrying fraud label and a 33-month
   forward ruler to grade against. The strict label export is the join key and the
   written policy says what each row means.
2. **The targets and the typologies.** The 27 schemes name what to look for at the
   claim level (per-patient medically-unlikely-edit violations, impossible days,
   rendering-provider mismatches, add-ons without base visits, capacity against
   licensed beds), and the pursuit book says where to look first.
3. **The evaluation harness.** The same pre-registered checks, run on a claims-level
   feature set, answer the POC's question without self-deception.

**The next runs.** A current-day export (no freeze) for the operational list once the
v26 research returns; v27 on the second freeze once two data gaps close (the
December-2023 ownership editions, a hospice edition fix); a wider freeze pair (2020
against 2023) for a cleaner independence test.

**The label is the lever.** Recall on owner-run operators is the one thing the
features cannot fix. The research lanes (defendant resolution, outcome refresh, state
sweeps) are built and gated on credits; each cycle adds hundreds of resolved rows.

**Models B and C.** Model B's chain is complete and waits on a people-data licence and
the FCRA review; Model C's cold-start underwriting runs today on rules and retires to
a trained model when the case-outcome database is large enough. The lookup tool
(percentiles and drivers, no fraud boolean) and the intake funnel are built and gated
on counsel.

## 9. What to be honest about in the room

- Precision at the top of the list is about 5 percent, against 0.4 percent for random.
  That is a ten-fold improvement and still means 19 of 20 names in the top 50 will not
  become federal cases in three years. The product is a better place to look, not a
  verdict.
- Only 3 percent of the DOJ cases we harvested resolve to a scored provider, because
  press releases name people, not NPIs. That is the recall ceiling and the research
  lanes are the answer.
- The two freezes' rulers overlap on 2024 cases; the matrices are independent, the
  rulers are partly so. The next pair fixes that.
- Several features and schemes read as coin flips on the label and are kept only
  because pruning them did not help; the scheme deep dive explains each one.
- We found and fixed our own leaks four times. The harness exists because the first
  three versions of every good idea were wrong in a way that looked like success.

## 10. Glossary (for the deck's appendix)

- **NPI**: the ten-digit National Provider Identifier every billing provider carries.
- **False Claims Act / qui tam / relator**: the law, the private-party suit under it,
  the whistleblower who files it.
- **LEIE**: the OIG's exclusion list; the oldest public fraud label.
- **Freeze**: a backtest cutoff; every input rebuilt as of that month.
- **Ruler**: the set of post-freeze outcomes a run is graded against.
- **hits@k**: how many of the top k organizations later had a public outcome.
- **Jaccard (top-50)**: the share of the top 50 that survives re-shuffled training folds.
- **Out of fold**: every provider is scored by a model that never saw it in training.
- **LightGBM**: the gradient-boosted decision-tree library the scorer uses.
- **PU learning**: learning from positives and unlabeled rows, because "not caught" is
  not "clean".
- **Label leak**: when a feature carries the answer; the thing the harness hunts.
- **Pre-registration**: writing the pass bars down before the run.

## 11. A suggested shape for the deck (for ChatGPT to expand, for Manus to build)

1. Title: what we built and what it proved.
2. The one-minute story (section 1), one slide, one picture: public data in, ranked
   list and dossier out.
3. Why it matters: the relator economics and the three models (section 2).
4. The architecture, plain (section 3, five boxes in a line).
5. The architecture, technical (section 3, one slide per layer for the analyst, kept
   in the appendix for the main room).
6. How we test it: the weather-forecaster analogy, the freeze, the ruler (section 4).
7. The checks we pre-register, as a checklist slide.
8. The scoreboard: v25 and v26 tables against random (section 5).
9. What the hits are and what that tells us about where precision lives.
10. The journey: a timeline from v6 to v26 with the three phases and the four leaks
    found and fixed (section 6).
11. What the AI spend produced: the code, the tests, the documents, the assets, the
    instruments (sections 6 and 7 numbers).
12. The data assets, with the label as the centrepiece (section 7).
13. The PurpleLab POC: what we bring and what we hope to learn (section 8).
14. What is next: the runs, the label lanes, Models B and C (section 8).
15. What to be honest about (section 9).
16. Appendix: glossary, the full run table, the source list.

_Speaker guidance: on the technical slides, say the plain sentence first, then the
mechanism, then the number. On the scoreboard, always show random beside the model;
the comparison is the claim._
