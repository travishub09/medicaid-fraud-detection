# The label: audit and expansion plan (2026-10-13)

_Goal, in the operator's words: find cases that can be prosecuted under the False Claims Act, qui tam. Every number below is read from the v12 and v13 run packets (same label, same ruler) and the code that builds the label. The VM-side auditor (`model_a/label_audit.py`) adds the pieces no packet carries: identifier quality per source, the dates, the case-database recall against the OIG CIA roster. Leads for counsel review, never accusations._

## 1. What the label is today

Two labels, one definition of fraud.

**The training label** (what the scorer learns from): `provider_on_exclusion` under the hard tier, as of 2021-12. 1,205 positives in the 617,062-NPI billing universe, 1,082 of them from DOJ cases, the rest from exclusion and revocation statutes that are FCA-shaped on their face. 2,353 NPIs carry any exclusion node; the weak and excluded tiers are masked from training and scored out of fold. The DOJ source resolves 1,584 defendant NPIs and labels 6,372 NPIs in all, 2,377 of them by affiliation broadcast, with a per-case budget (the largest single case reaches 241 NPIs).

**The forward ruler** (what a run is graded on): DOJ and OIG cases with a resolved outcome announced in the 36 months after the freeze, plus hard-tier exclusions and revocations dated in the same window. 3,049 forward positives exist on paper: 1,801 case rows, 1,222 hard exclusions, 19 weak, 7 excluded. Of those, **509** sit in the scored universe. At the organization grain the pursuit book of about 1,400 operator orgs holds **8** of them.

The whole series is graded on those 8.

## 2. Audit findings (from the artifacts)

Ranked by how much of the gap each one explains.

| # | finding | evidence | effect |
|---|---|---|---|
| A1 | **Reach: 83 percent of forward positives never joined the universe.** 3,049 on paper, 509 in the matrix. The largest block is 745 "conviction: program-related crime" rows, which are mostly INDIVIDUALS (aides, nurses, billers, owners convicted personally) who never billed Medicaid under their own NPI. The universe is billing NPIs. | TIERS.md fca36; section 1 of the diagnostics | The single biggest gap, and it is the owner-run class: a convicted aide points at the small agency that billed. |
| A2 | **Undated exclusion rows: 24,774.** They can be neither a training positive nor a forward positive, and they cannot be placed against a cutoff. | TIERS.md header | Unknown share of them are fraud-tier events sitting outside the label. |
| A3 | **The weak tier hides fraud-tier events.** 872 "felony, basis unstated" (about 20 percent convert to a fraud event once the conviction is read), 839 license revocations with no basis, 705 "not operational at the address" (the phantom-site shape). Tiered weak because the SOURCE does not say why, not because the event is not fraud. | TIERS.md "not counted" | Several hundred recoverable positives, each needing an outside record (the court case, the board order) to upgrade. |
| A4 | **The case database's recall against the universe is low.** v12: 130 post-cutoff cases resolvable to a scored provider; $1.44B of settlement dollars eligible; $177B announced after the cutoff could not be tied to any scored provider. The $177B is mostly pharma, device and hospital-system settlements that are not Medicaid NPI billers (not a loss for this product). The 130 is the real pool, and public FCA health-care resolutions run several hundred a year. | diagnostics section 0 and 9 | Recall of the harvest is the lever the CIA cross-check measures exactly. |
| A5 | **Label noise in the case source.** The largest DOJ case in the training label is a hospital's $4M VOLUNTARY self-disclosure settlement, broadcast to 241 NPIs under the per-case budget. A self-disclosure is not a qui tam outcome and not evidence of the billing shape the model should learn; the outcome rule admits any "settlement". The learning loop flags 141 suspect positives by confident learning. | manifest sources_audit; LEARNING_LOOP | A small count, a large weight: broadcast rows dilute the owner-run signal. |
| A6 | **The qui tam flag is in the data and not in the ruler.** Every case row carries `qui_tam` (1 when the release says qui tam or whistleblower). The headline ruler grades on any resolved outcome, including government-initiated civil cases and criminal pleas. Relator-originated cases are the exact product target and are never counted separately. | case_db.py; case_labels metadata | The grade does not measure the thing the business sells. |
| A7 | **Scheme validation is thin.** 1,364 DOJ-case NPIs in the universe, but per scheme: upcoding 0 proven in coverage, NEMT 0, invalid identity 0, behavioral health 5, pill mill 5. Two schemes validate (worthless services, single-service mill). | CASE_VALIDATION.md | The label cannot yet tell the model which typologies it gets right. |
| A8 | **Horizon and freeze.** One freeze (2021-12), one horizon (36 months). The case database runs to 2026; the last 20 months of announcements are unused by the ruler, and a single cohort cannot show replication. | runner | Free positives, and a replication cohort, at zero integrity cost. |

What is sound and should not be touched: the hard / weak / excluded tiering by statute; the outcome rule (allegations and docket-only cases are never positives); the evidence ladder and the 90-percent verification bar; the per-case budget; the as-of discipline (the v6 lesson); the retirement of fuzzy name matching. Every expansion below is built to pass through those same gates.

## 3. The expansion plan, ranked by integrity

Integrity rule for every lever: a positive needs (a) an official record of a resolved outcome or a fraud-shaped statute, (b) an identifier that joins to billing without a name guess (an NPI, a PECOS enrollment, a license number, an exact legal name plus address), (c) a date, and (d) a tier and a provenance string on the row. Anything that fails one of the four goes to the research queue or the quarantine, never to the label.

### Tier 0: no new data, no integrity risk

- **L0.1 Employer resolution for convicted individuals (fixes A1, the owner-run gap).** For every post-cutoff hard-tier INDIVIDUAL (LEIE 1128(a)(1) program-related crime, felony health-care fraud), find the billing organization they rendered for in the 24 months before the exclusion date: the servicing-NPI grain of the spending fact first (whose claims carried their NPI), the PECOS reassignment edge second, the NPPES employer and same-address organization third. The organization becomes a forward positive under a new basis `affiliated_conviction`, carrying the individual's NPI, the exclusion date and the dollars rendered, weight-budgeted like the DOJ broadcast (one conviction never labels more than one organization without evidence). A convicted aide at a 12-person home-health agency is exactly the case shape a relator brings. Expected: several hundred organization positives.
- **L0.2 Qui tam as the headline ruler (fixes A6).** Add `--ruler qui_tam` to the forward label: resolved cases with `qui_tam = 1`. Report hits under it beside the FCA ruler. Pre-register v15 on it once the count is known. It is the business outcome; grading on it stops rewarding criminal-only shapes.
- **L0.3 Self-disclosure out (fixes A5).** A case whose release says voluntary disclosure or self-disclosed is tagged `self_disclosure` in the case database and tiered weak for training and never a forward positive. Same for settlements with no allegation of false claims (billing-error repayments).
- **L0.4 A second freeze and a longer horizon (fixes A8).** Freeze 2020-12 with a 48-month horizon as a replication cohort; 2021-12 at 48 months as well. The pre-registration requires the result on both. Costs one VM run per cohort; the case database already holds the announcements.
- **L0.5 Date the undated (fixes A2).** The auditor names the source of the 24,774 undated rows. Where the source carries a date under another column (state lists often do), map it; where it does not, the row stays out of the label and the count is reported, never silently dropped.

### Tier 1: official records with identifiers (new or under-used)

- **L1.1 OIG Corporate Integrity Agreement roster (the recall ruler for A4).** The file is on disk (preclean/cia/cia_list.csv). Every CIA is a resolved FCA matter with the entity's legal name, state and effective date. Two uses: measure the harvest's recall (share of CIA parties present in the case database, by year), and add the missing ones as positives through an exact legal-name plus state join to PECOS enrollment (no fuzzy matching; unmatched names go to the research queue). `label_audit --cia` does the measurement today.
- **L1.2 Unsealed qui tam dockets with outcomes (A4, A6).** CourtListener nature-of-suit 376 dockets with "ex rel." captions are relator cases by definition. Today they enter as allegations only. A docket whose entries carry a notice of intervention, a settlement, or a stipulation of dismissal with government consent is a resolved qui tam outcome; `docket_backfill` grows a docket-entry pass (RECAP covers most). Defendants resolve through the same resolver with the same ladder.
- **L1.3 State attorney general and MFCU resolutions (A4, owner-run recall).** Thirty-one states have their own false claims acts with qui tam provisions, and state AG press releases name the provider, the city and the amount. The MFCU sweep prompts (`case_harvest_prompts`) exist for nine Southeast states; extend to the ten largest Medicaid states (CA, NY, TX, FL, IL, PA, OH, MI, NJ, GA), reviewed through the same verification sample. These are the small-operator cases the DOJ feed under-reports.
- **L1.4 Big-state Medicaid sanction lists with reasons (A3).** NY OMIG, CA DHCS suspended-and-ineligible, TX HHSC-OIG, FL AHCA, IL HFS-OIG, PA, OH, MI, NJ publish exclusion and termination lists with NPIs or license numbers AND a reason. `state_terminations` covers six Southeast states; extending it is adapter work with the existing tier rules (fraud-shaped reason = hard; bare termination = weak).
- **L1.5 License-number joins for board actions (A3).** NPPES carries state license numbers per NPI. Board orders carry the license number. Joining on the number instead of the name turns 839 unusable license revocations into joinable rows, and the board's order text (billing fraud versus substance versus boundary) sets the tier. No name matching anywhere in the chain.
- **L1.6 Read the unstated felonies (A3).** For the 872 "felony, basis unstated" revocations, the court record (CourtListener criminal dockets by defendant name and district) states the offense; a health-care-fraud or false-statement count upgrades the row to hard with the docket as provenance. Research-queue work, verified by sample.

### Tier 2: lower integrity or gated

- Commercial settlement databases (licensed, name-keyed): only with a license and only as a recall check, never as a direct positive.
- SEC disclosures for public chains: cheap, but the chains are already the class the model finds.
- Credible-allegation payment suspensions (42 CFR 455.23): the strongest early signal of a pending case, but rarely public; where a state publishes them they are allegations, which belong in the per-se contradictions layer, never in the label.

## 4. Order of work

1. Run the auditor on v13 on the VM (one command, below) and read LABEL_AUDIT.md: the undated sources (A2), the CIA recall number (A4), the identifier grades per source. This decides whether L1.1 or L1.3 goes first.
2. Build L0.1 (employer resolution for convictions), L0.2 (qui tam ruler) and L0.3 (self-disclosure out): code only, no new data, one bundle. Re-grade v14's matrix under the new ruler as a sweep row before anything ships.
3. L0.4 as the v15 pre-registration (two cohorts, 48 months).
4. L1.1 and L1.3 as the next procurement and research sessions; L1.4 and L1.5 as adapter work in parallel.
5. Every added positive carries provenance, tier, date and basis; every new source gets a 30-row verification sample before it trains; the strict FCA label export (the PurpleLab file) takes only `fca_civil`, `hcf_criminal`, `excl_statute` and the state-case tiers, as written.

The claim to hold onto: the model's job is to rank; the label's job is to be right. Everything above grows the label only where an official record and a hard identifier already agree.

## 5. The auditor's read-out (LABEL_AUDIT_v13.md, 2026-10-13) and what it changed

| finding | number | consequence |
|---|---|---|
| Name-only rows | LEIE 66,081 of 71,558 (8 percent carry a valid NPI); SAM 102,232 of 120,313 (6 percent, 11,234 fail the Luhn check); state lists 3,795 of 4,666 (19 percent) | 172,172 exclusion records can never label a provider as they stand. LEIE rows before the NPI era are mostly beyond the billing window; the post-2015 name-only rows are the recoverable part, through NPPES name + state + specialty with the same ladder the defendant resolver uses. |
| Reach | 13,141 distinct valid NPIs across the feeds; 1,125 (9 percent) in the scored universe; 2,902 NPIs with a hard-tier event | Confirms A1: the excluded are individuals who never billed under their own number. Employer resolution (L0.1) is the lever. |
| The training label | 2,353 positives; DOJ 1,082 of them, and of those 959 by ORGANIZATION-BILLING BROADCAST (University of Miami 81 NPIs, Florida Cancer Specialists 64, AnMed 58, Abington 50 ...), 123 by resolved defendant, 2 by affiliation | 89 percent of the DOJ training positives are hospital and health-system settlements broadcast to every member NPI. The scorer learns institutional shapes from them; the per-case budget caps the weight but not the count. |
| The case database | 16,911 usable cases; 0 carry an NPI in the source; 315 (2 percent) resolve to a scored NPI; 5,976 say qui tam; 2,681 near-duplicate pairs double-count $547B; $1.92T unresolved, of which pharma, DME, lab and telehealth defendants are unreachable by design | The qui tam flag is populated on 43 percent of cases: the ruler L0.2 has a base. The duplicate pairs need usable = 0 on the second row. |
| CIA roster | 339 parties; 107 (32 percent) in the case DB; 93 (27 percent) resolve to a graph org by name; 232 missing from the harvest, listed with state and date | The parent-versus-subsidiary gap is exactly the ownership path L1.1 now takes. |
| Identity collisions | 24 NPIs carry two unrelated names across exclusion records | Review before use; small. |

Built the same day (bundle 307): `model_a/label_expansion.py` (`affiliated` and `cia` commands), the `self_disclosure` flag in the case database with the outcome rule setting those rows aside, the qui tam ruler in `prospective_label --qui-tam-only` and `accuracy_diagnostics --qui-tam-ruler`, and `prospective_label --affiliated` to fold the convicted-individual billers into the forward label. The run lines are in the reminder task `label_expansion_run`. Not yet built: name-only LEIE / SAM / state rows to NPIs (the resolver ladder applied to exclusion feeds), the duplicate-pair usable flag, the state AG / MFCU harvest for the ten largest Medicaid states, license-number joins.
