# How This Works — In Plain English

No code, no jargon. This is the rationale for the whole system, written for
someone encountering it for the first time. (Terms in **bold** are in the
[GLOSSARY](GLOSSARY.md).)

## The problem we're solving

Healthcare fraud against Medicare and Medicaid costs tens of billions of dollars
a year. The U.S. has a powerful law against it — the **False Claims Act** — with
an unusual feature: a private citizen who knows about fraud (a **whistleblower**,
legally called a **relator**) can file a lawsuit on the government's behalf and
receive 15–30% of whatever the government recovers. Recoveries are often in the
tens or hundreds of millions. In 2024, whistleblowers were paid over $400 million.

Here's the catch that shapes everything we build: **the lawsuit needs an insider.**
Public billing data can show that a company's numbers look deeply abnormal, but a
court case needs a person who saw what happened — the coder who was told to
inflate codes, the nurse who saw patients admitted to hospice who weren't dying,
the sales rep who watched kickbacks get paid. The law itself enforces this: cases
built only on public information get thrown out (the "public disclosure bar"),
and only insiders with direct, independent knowledge ("original sources") get paid.

So the business is not "detect fraud with data." It is: **use data to figure out
where fraud is probably happening, then find and earn the trust of the people who
saw it, then back the strongest resulting cases.** Three questions, three models.

## Question 1 — WHERE is fraud concentrating? (Model A)

The government publishes enormous amounts of healthcare billing data: every
provider's billing patterns, who owns which facilities, who has been banned from
the programs, what enforcement actions have happened. We combine these and look
for organizations whose patterns are hard to explain honestly.

**How we avoid flagging the wrong people** — the core design choices:

- **Compare like with like.** A cardiologist bills differently from a family
  doctor. Every comparison is against true peers (same specialty, similar
  setting), never against the whole population.
- **Only excess is suspicious.** Billing unusually *low* is not fraud. We only
  look one direction.
- **Robust math.** We measure "unusual" in a way that a few extreme billers
  can't distort (medians, not averages).
- **One fact counts once.** If a company bills one code heavily, that shows up
  in several statistics at once. We collapse correlated signals so a single fact
  can't masquerade as five independent red flags.
- **The network matters.** Some fraud is invisible at the single-company level:
  five "different" companies at the same address, one owner controlling many
  entities with a banned person in the network, suppliers fed by a single
  doctor. We build a map (a graph) of who owns, controls, shares addresses with,
  and has been excluded alongside whom — and detect those shapes directly.
- **Dollar-weight the result.** A suspicious $50M/year company matters more than
  a suspicious $200K one. Each flagged organization gets an **expected
  recoverable value (ERV)**: roughly, how likely the pattern is real fraud,
  times how much money is at stake.

The output is never "this company is a fraud." It is a **dossier**: here is the
pattern, here is why it's abnormal versus peers, here are the *innocent
explanations that must be ruled out*, here is what it would be worth if true.
A human reviews every one. This isn't politeness — accusing a company publicly on
statistics alone is defamation, and the explainability is what makes the work
credible to the lawyers who take it forward.

**Does it actually work?** We back-tested it: companies our score ranked in the
top 10% were **2× more likely** to later appear on the government's exclusion
list than average — and a ranking by sheer size alone showed *no* such lift. The
signal is real and it is not just "big companies."

**One honest limitation:** the public data lags about two years. Model A finds
*entrenched, structural* schemes — which is fine, because those make the best
cases. The *timing* signal comes from the next model.

## Question 2 — WHO saw it, and WHEN are they reachable? (Model B)

Every fraud scheme has a workflow, and every workflow has witnesses. Upcoding
runs through coders and billing managers. Kickbacks run through sales reps and
contracting. Hospice eligibility fraud runs through admissions nurses. We
maintain a map from each scheme type to the job titles that would have seen it.

Three multiplied factors rank who matters:

- **Knowledge** — did their role have line of sight, and *were they employed
  there during the period the anomaly shows in the data*? (That timing check is
  a hard gate. Someone who left before the scheme started knows nothing,
  whatever their title.)
- **Propensity** — will they act? Empirically, the people who come forward are
  *former* employees, 6–18 months after leaving (memory fresh, fear faded,
  deadlines still open), especially after an involuntary exit. Someone who sued
  for wrongful termination after reporting concerns internally is the warmest
  signal there is. Mass-layoff notices (which employers must file publicly) tell
  us exactly when a cohort of insiders just became reachable.
- **Reachability** — does a lawful channel exist to put education in front of
  them?

**The line we never cross:** the output is *advertising audiences* — "former
billing staff of flagged home-health companies in Texas" — not a list of named
individuals to cold-call. People find *us* through education ("is what I saw
actually illegal?", "what are my rights?"), through a confidential, attorney-
mediated intake. That's both the law (solicitation rules, privacy, fair-credit
rules about scoring individuals) and good strategy: whistleblowing is
terrifying, and trust is the only currency that converts.

## Question 3 — Is the case WORTH backing? (Model C)

Filing a whistleblower case takes years. The single biggest factor in whether it
pays: does the government **intervene** (take over the case)? In 2024, ~$2.2B of
the $2.4B recovered in these suits came from intervened cases. So we predict
intervention — from the scheme type (does it match current enforcement
priorities?), the strength and documentation of the insider's account, whether
*our own public-data signal independently corroborates it* (our unique edge), the
court district's track record, and the size of the damages. The output prices a
litigation-finance decision: fund, pass, or fund on terms — managed as a
portfolio, because returns are carried by a few large wins.

## The flywheel

The three models chain: A finds the organizations → B finds the people and the
moment → education and intake convert them → C picks the cases worth backing →
the outcomes of those cases retrain A and C, and conversion data retrains B.
Every closed loop makes the system smarter, and that accumulated outcome data is
the durable advantage — nobody can buy it off the shelf.

## What keeps this legal and ethical

These are built into the code, not bolted on:
- We **never publish accusations** — outlier statistics with context, drivers,
  and alternative explanations, always for human/counsel review.
- We **never sell or expose** the inference "this person likely witnessed
  fraud" — it's treated as sensitive from the moment it exists.
- We **never cold-solicit named individuals** — audiences and education only,
  with counsel gating any direct contact.
- We **never use data we aren't licensed to use** — several restricted datasets
  are explicitly off-limits (see the data catalog) and the system has no hard
  dependency on any of them.
- We **never collect patient information** at intake, and never encourage anyone
  to take documents or access systems unlawfully.

## Where to go next

- Want to run it? → [GETTING_STARTED.md](GETTING_STARTED.md)
- Want the technical depth? → [platform/ARCHITECTURE.md](platform/ARCHITECTURE.md)
  and the numbered specs in [platform/](platform/README.md)
- Lost in an acronym? → [GLOSSARY.md](GLOSSARY.md)
