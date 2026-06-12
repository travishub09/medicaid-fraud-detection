# Reading the Outputs — A Guide for Investigators and Analysts

You've run the system (or someone ran it for you) and you're looking at a folder
of files. This guide explains, in plain English, what each output is, how to
read it, and what to do next. No technical background assumed. Terms are in the
[GLOSSARY](GLOSSARY.md); the why-it-works story is in [HOW_IT_WORKS.md](HOW_IT_WORKS.md).

**The one rule that governs everything you read here:** every number in these
files is a *statistical comparison with peers*, not a finding of wrongdoing.
Your job as the reader is to be the skeptic the math can't be — the alternative
explanations listed in each dossier are genuinely the most common reality.

---

## 1. The target dossier (`model_a/dossiers/*.md`) — read these first

One file per high-priority organization, ranked (001_ is the top target). Each
section and how to read it:

| Section | What it tells you | How to read it |
|---|---|---|
| **The disclaimer** (top) | This is a hypothesis, not an accusation | It's there for a legal reason. Never forward a dossier without it. |
| **Entity summary** | Who this organization is: its canonical identity, how many billing numbers (NPIs) it spans, its known name variants, states, specialty | Check `confidence` — `high` means hard-ID linkage (very reliable); `medium`/`low` means name-based merging: **verify the NPIs really are one company before anything else**. |
| **Scheme hypothesis** | The *type* of fraud the pattern most resembles (e.g. `single_service_mill`, `ownership_integrity`) | This drives everything downstream — which insiders would have seen it, what evidence would matter. If the hypothesis doesn't make sense for this kind of provider, that's a red flag about the lead, not the provider. |
| **Per-scheme subscores** | How strongly each fraud pattern fires, 0–1, with the exact statistics behind each | 0.9+ = extreme vs. peers. 0.5 = unremarkable. Look at *which features* drove it — "concentration" alone is weaker than concentration + intensity + a network flag together. |
| **Graph context** | Network red flags: distance to a banned (excluded) party, shared owners, shared addresses, ring membership | `Hops to exclusion: 1–2` means a banned person/company sits very close in the ownership network — historically one of the strongest signals. `-1` means no connection found. |
| **Exposure / ERV** | The dollars: annual program payments × an assumed recoverable share | This is a **size proxy for prioritization**, not a damages calculation. "unknown (payments not yet loaded)" means spending data hasn't been attached yet. |
| **Alternative explanations** | The innocent stories that must be ruled out | Work this list seriously. Most flagged organizations have one of these explanations. The lead is only interesting once you can say why each one doesn't fit. |
| **Next steps** | The standard path | Human review → corroborate against raw billing → only then request a witness map. |

**Verdicts you can reach on a dossier:** (a) *advance* — drivers hold up,
alternatives don't explain them → corroboration plan; (b) *park* — interesting
but an alternative explanation is plausible → note why, revisit on next data
refresh; (c) *discard* — record the reason (this feeds back into making the
model better).

## 2. The ranked list (`model_a/MODEL_A_REPORT.md` + `erv_ranked.parquet`)

The report shows the top organizations by **ERV** (expected recoverable value —
probability-weighted dollars at stake) and which features were available to the
scoring run ("feature coverage"). Two reading notes:
- **ERV ranks attention, not guilt.** A #3 ranking means "look here third."
- **Check coverage first.** If only a few schemes had features (e.g. Medicare
  Part B not yet loaded), the ranking only reflects what it could see.

## 3. The company tracker CSVs (`detection/` — open in a spreadsheet)

These come from the original detection pipeline and are the working queue:

- **`triage_priority.csv`** — start here. The highest-confidence queue.
- **`company_leads_clean.csv`** — every lead.
- **`probable_owner_backlog.csv`** — the noisier ownership-only leads.

Columns that matter most (left to right as they appear):

| Column | Plain English |
|---|---|
| `rank`, `tier_label` | Position in the queue and *why* it's queued (Direct = billed-while-banned or similar hard fact; Company-anomaly = statistical pattern; Probable-owner = name-matched ownership link, noisiest) |
| `company_name`, `specialty`, `states` | Who, what kind of provider, where |
| `company_total_billing_size_proxy_not_case_value` | Total billing. The long name is deliberate: it is a SIZE proxy, **not** what a case would be worth |
| `reasons` | The lead's story in one sentence — the single most useful column |
| `review_flags` | Warnings about the lead itself: `low_merge_confidence` (the company grouping may be wrong), `possible_same_operator`, `name_unresolved` |
| `company_anomaly_score`, `n_concept_signals` | The statistical strength (0–1) and how many *independent* patterns fired (2–3 independent signals beats one extreme one) |
| `any_billed_after_exclusion`, `any_provider_on_leie` | Hard facts: someone billed while banned / is on the exclusion list |
| `npi_list` | The constituent billing numbers, for verification |

## 4. WARN surge leads (`sourcing/warn_surge_leads.parquet`)

Flagged organizations that just had a mass layoff — meaning a cohort of
potential witnesses is entering the reachable window:

| Column | Plain English |
|---|---|
| `window_opens` / `window_closes` / `window_status` | The 6–18-month post-departure outreach window. `pending` = schedule it; `active` = act now; `expired` = the moment passed |
| `erv_rank` | How high the employer sits in the risk ranking |
| `match_ambiguous` | **1 = the employer name matches more than one company in our data — verify which one before doing anything** |

## 5. The QA reports (`QA_REPORT.md`, `GRAPH_REPORT.md`, `MODEL_A_REPORT.md`…)

Every pipeline stage writes one. You don't need to understand every line — scan
for two things: every assertion line says **✅/PASS**, and the row/dollar counts
look like the data you expected. If anything says FAIL, stop and read
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## The triage path (what to actually do, in order)

1. Open `triage_priority.csv`, take the top unworked row.
2. Read its dossier if one exists (`model_a/dossiers/`).
3. Check the lead's own quality flags (`review_flags`, `merge_confidence`,
   `match_ambiguous`) — disqualify the *lead's data* before judging the provider.
4. Work the alternative-explanations list against the drivers.
5. Reach a verdict: advance / park / discard — **always with a written reason**.
6. Advanced leads go to corroboration (raw billing detail), and only after that
   to counsel and (eventually) a witness-map request.
7. Record outcomes — they make every future ranking smarter.
