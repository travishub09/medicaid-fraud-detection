# Healthcare Fraud Whistleblower Origination — Status & First Findings

**Prepared for:** client review call
**Date:** June 2026
**Stage:** working end-to-end prototype on full national data; first real lead list produced

---

## 1. Executive summary

We set out to build an intelligence engine that finds *where* healthcare fraud
concentrates in public data, ranks the organizations worth investigating by
**recoverable dollars at stake**, and produces investigator-ready case files —
all under a strict legal frame: every output is an **investigative hypothesis for
counsel review, never an accusation**.

As of this week, the platform runs **end to end on real, national data** for the
first time. It ingested 238 million Medicaid/Medicare spending records across
9.6 million providers, resolved them into ~9.1 million organizations, scored them,
and produced a ranked lead list with per-target dossiers.

Three things matter for tomorrow's conversation:

1. **It works at national scale.** The full pipeline — ingest → integrity audit →
   detection → entity graph → scoring → dossiers — completes on the real data
   with every internal accounting check passing.
2. **The leads land in the right place.** With no prior tuning, the top of the
   list is dominated by **home-health and personal-care agencies — the single
   highest-fraud sector in Medicaid** — and it independently surfaced a company
   that paid a **$150M federal fraud settlement** into the top five. That is the
   strongest possible early signal that the engine points at real risk.
3. **It is honest about what it is.** This is a v1 triage that ranks by signal ×
   dollars. It is not yet a calibrated probability of fraud, and we can show
   exactly what closes that gap. No output is presented as a finding of wrongdoing.

---

## 2. What has been built

A single platform on a shared healthcare entity-resolution graph, with three
model tracks:

- **Model A — "where & who" (the engine that produced today's results).** Scores
  every organization on fraud-signal strength × dollar exposure → an Expected
  Recoverable Value (ERV) ranking, each with named, explainable drivers.
- **Model B — whistleblower audiences.** Identifies the roles/organizations where
  a plausible insider would sit and turns them into compliant marketing audiences
  (role × employer × channel) — **never named-individual lists** (FCRA/privacy
  frame). Built; activation gated on counsel review.
- **Model C — case underwriting.** Decides which resulting cases are worth
  financing (fund / pass / fund-with-terms). Built as a cold-start rules engine.

Supporting this: a 13-stage, assertion-driven ingestion pipeline (every join
checks row-count and dollar conservation and **hard-fails** rather than silently
corrupting), a canonical entity graph (providers → companies → owners →
exclusions → shared-address/owner rings), and an enforcement case database built
from a decade of Department of Justice settlements.

---

## 3. What we produced on the real data

**Data integrity (the foundation).** The raw spending file totals a nonsensical
$21.8 trillion. The pipeline traced that to **122 corrupted records**, quarantined
them (never deleted), and reconciled the rest to the cent — leaving a **real,
usable universe of ~$1.42 trillion** in provider-attributed Medicaid spending.
That clean-up, fully auditable, is itself a deliverable: it is the difference
between a credible analysis and a garbage-in number.

**Entity resolution.** 9.6M provider identifiers resolved into **9.08M canonical
organizations** (merging subparts, shared-owner groups, and name matches).

**Detection signal.** Of 617,000 scored providers, the rule layer flagged **14
that billed Medicaid *after* being formally excluded** — close to a prima facie
signal — plus 574 excluded after a billing history, and ~2,500 statistical
anomaly leads after de-correlation.

**Model A lead list.** ~538,000 organizations carry a real fraud signal; ~30,000
program-infrastructure entities (state agencies, fiscal intermediaries, transport
brokers, national labs) were identified and **excluded from targeting** as
structurally not qui tam defendants. The resulting top of the ranking is
overwhelmingly **home-health and personal-care agencies** — exactly the sector
where Medicaid qui tam cases concentrate.

**External validation.** **Maxim Healthcare Services**, which settled a Medicaid
fraud case with the federal government for **$150 million**, surfaced in the top
five from cold, label-free data. The engine found a known bad actor without being
told it existed.

**Enforcement calibration (initiated).** We ingested **332 healthcare False
Claims Act settlements** (10-year DOJ backfill) and used the matched defendants to
begin training the model to recognize the real fraud signature. The model already
learned a coherent one — **payment intensity, clustered addresses, specialty
mismatch, billing concentration** — consistent with how these cases actually look.

---

## 4. Why the output is credible (methodology)

- **Explainability is mandatory.** Every score ships with named drivers; there is
  no black-box number anywhere in an output. This is both a legal-defensibility
  requirement and what makes a dossier usable by counsel.
- **Peer-relative, one-sided statistics.** Providers are compared only to true
  peers (specialty × geography), and only *excess* is treated as suspicious.
- **Conservation is enforced.** Dollars are attributed once, by billing provider,
  and every step asserts the money reconciles.
- **It has been backtested.** On historical exclusion data, the scoring approach
  delivered ~**2× lift** in the top decile — it concentrates known-bad outcomes
  near the top.
- **Legal guardrails are built into the code**, not bolted on: audiences not
  call-lists, no PHI at intake, no reliance on data we cannot lawfully use.

---

## 5. Honest limitations (what this is *not*, yet)

- **It is dollar-ranked triage, not a calibrated fraud probability.** Today rank
  order is driven mostly by exposure among signal-bearing orgs. Telling rank 1
  apart from rank 40 as *more likely* fraud needs calibration against real
  outcomes — which we have now started but not finished.
- **The calibration is early.** Only 23 of 332 DOJ defendants matched the graph on
  this first pass (corporate-name matching is lossy), which is too few to produce
  a trustworthy probability. The fix is known and mechanical (below).
- **Three secondary signals are temporarily off** at full scale (scoped damages,
  growth shocks, clinical implausibility) pending an engineering pass; the core
  ranking does not depend on them.
- **Every lead is a hypothesis for human/counsel review.** Large legitimate
  systems can still appear on signal that correlates with size; a reviewer, not
  the model, decides what is real.

---

## 6. Recommended next steps

In priority order:

1. **Finish the enforcement calibration.** Add the OIG enforcement-actions feed
   and the ~335 active Corporate Integrity Agreements (settled cases = clean
   training labels), and improve defendant-to-organization name matching. Goal:
   move from 23 matched cases into the hundreds, which is what turns the
   probability model from noisy to trustworthy.
2. **Re-enable the three deferred signals** at scale (an engineering task, not a
   data dependency).
3. **Decision needed — OpenSanctions license.** One commercial license unlocks
   ~45 state Medicaid exclusion lists (the integrity signal that federal data
   misses); the ingestion code is already built and waiting on the license.
4. **Stand up Model B audience generation** for the top-ranked targets — gated on
   a counsel/FCRA review, which we recommend scheduling now.
5. **Run Model C underwriting** on the qualified leads to produce fund/pass
   recommendations.

---

## 7. The dossiers

The accompanying dossiers are the per-target deliverable: for each top-ranked
organization, the case file states the **scheme hypothesis**, the **statistics
that fired and why**, the **ownership/network context**, the **dollar exposure**,
and — mandatory — a list of **innocent explanations** plus a disclaimer that the
file is an investigative hypothesis, not a determination. That format is the
product a relator's counsel would actually work from.

*(Top-ranked organizations and their dossiers follow.)*
