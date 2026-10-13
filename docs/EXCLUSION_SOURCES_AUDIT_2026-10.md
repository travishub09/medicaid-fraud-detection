# Exclusion sources: how LEIE, the state lists and OpenSanctions are used for the label, and how to use them better (2026-10-13)

_Companion to docs/LABEL_EXPANSION_PLAN_2026-10.md. Read against the code (the loaders and the tier table) and the auditor's numbers (LABEL_AUDIT_v13.md). Every record here is a public integrity event, never a finding about a provider._

## 1. LEIE (HHS-OIG List of Excluded Individuals and Entities)

**What the file carries.** One row per exclusion: LASTNAME, FIRSTNAME, MIDNAME, BUSNAME, GENERAL (the provider category), SPECIALTY, UPIN, NPI, DOB, ADDRESS, CITY, STATE, ZIP, EXCLTYPE (the statute), EXCLDATE, REINDATE, WAIVERDATE, WVRSTATE. 71,558 rows in the operator's edition; 5,363 carry an NPI; the rest (66,081) are mostly pre-NPI-era rows, and a share of post-2007 individuals who never had or never listed an NPI.

**What the pipeline used (before today).** The graph path (`integrate.build_exclusions`) kept NPI, the names, the statute and the two dates. It dropped UPIN, DOB, GENERAL, SPECIALTY, ZIP and the waiver fields, and until bundle 299 the STATE too. The stage-one path (`clean_data.clean_leie`) additionally keeps one row per NPI, the EARLIEST exclusion, so a provider excluded in 2005 for a loan default and again in 2022 for fraud shows only the 2005 row there; the graph path keeps every record, so the label was never affected, but the stage-one features were.

**What was wrong or wasted.**
1. **UPIN is a hard identifier and was thrown away.** NPPES records the Medicare UPIN as Other Provider Identifier type 02. A pre-NPI LEIE row with a UPIN joins to an NPI without any name logic.
2. **ZIP, specialty and DOB were thrown away.** ZIP and specialty are the tie-breakers for the ambiguous name matches; DOB joins the death master.
3. **1128(b)(8) and (b)(15) are tiered weak.** (b)(8) is an entity controlled by a sanctioned individual, (b)(15) an owner or officer of an excluded entity. When the underlying individual's exclusion is hard (a fraud conviction), the entity is the billing vehicle, which is exactly the policy the operator set for criminal cases ("the company whose owner was convicted stays as the billing vehicle"). Today those rows never train.
4. **1128(a)(4) felony controlled-substance convictions are weak.** Many are pill-mill prescribers, and a pill mill billing Medicaid for medically unnecessary prescriptions is an FCA theory the DOJ prosecutes. Not FCA on its face; worth a label ablation, not a tier change.
5. **The exclusion date lags the conduct.** OIG excludes one to three years after the conviction. Under a 36-month forward horizon, a conviction inside the window with an exclusion outside it is lost. Where the NPI also resolves to a DOJ case, the case date should govern.

**Changed today (bundle 309).** `integrate.build_exclusions` carries UPIN, DOB, GENERAL, SPECIALTY, ZIP, CITY, STATE and the waiver fields into processed/exclusions.parquet, and the exclusion nodes keep them. `ccn_npi_crosswalk --other-ids-out` writes the NPPES Other Provider Identifiers of type 02 (UPIN) and 05 (state Medicaid id, with the issuing state) as a long table. `label_expansion names --other-ids` resolves by UPIN first (`excl_upin`), then by unique name in state, then hands the ambiguous rows to Manus with the candidates listed.

**Still to do.** The (b)(8) / (b)(15) tier when the controlling individual is hard-tier (needs the LEIE's own linkage, which the file does not print; the DOJ release usually names both, so the resolver's affiliation path is the route); the (a)(4) ablation; the case-date-over-exclusion-date rule.

## 2. State Medicaid exclusion and termination lists

**What is loaded.** Six Southeast states with NPIs (GA, KY, MS, NC, SC, TN) and Alabama by unique in-state name; the loader finds NPI, names, dates, the reason, a second cause column, provider type and the timeframe by header pattern. The reason text is tiered by `STATE_TERM_RULES`. The auditor: 4,666 rows, 834 distinct NPIs (19 percent of rows), 82 hard-tier NPIs in the universe.

**What was wrong or wasted.**
1. **The hard rule admitted allegations.** "Indicted" and "credible allegation of fraud" (a 42 CFR 455.23 payment suspension) were hard-tier. An indictment is a charge; a suspension is an allegation. Under the integrity gate neither is an outcome. Changed today: both are a weak rule of their own ("state termination on an allegation or charge"), still graph nodes, never positives. One pinned test moved with it.
2. **No license-number or Medicaid-id field.** Big-state lists (NY OMIG, CA DHCS suspended and ineligible, TX HHSC-OIG, FL AHCA, IL HFS-OIG, PA, OH, MI, NJ) print a license number and often the state Medicaid provider id. NPPES holds the Medicaid id as Other Provider Identifier type 05 with the issuing state, and the license number in its own fields. Both are hard joins that turn name-only rows into NPIs. The type-05 table is written today; the state-list aliases for `license_number` and `medicaid_id` and the join are the next adapter step, with the big-state downloads.
3. **Coverage.** Six states plus Alabama. The ten largest Medicaid states are the recall gap for owner-run operators; the download task exists (fl_la_state_lists); the Manus sweep set now takes any state list.

## 3. OpenSanctions

**What is loaded.** The FtM entity export normalized into the exclusions schema: name, the source dataset as the type, start and listing dates, and an NPI only when the entity carries `npiCode` or `idNumber`. Tiered `excluded` (never a positive) because the aggregator carries no statute; the audit shows 6 forward rows tiered out and no identifier contribution.

**What it is actually good for.** OpenSanctions DE-DUPLICATES across the LEIE, SAM and about 45 state lists into one entity per person or company, so a name-only LEIE row can sit in the same entity as a state-list row that carries the NPI, a DOB, an address and the statute (`program`, `reason`, `notes` properties, which the loader does not read). Three changes:
1. Read `program` / `reason` / `notes` and tier them with the same tables (then a row is `hard` when the underlying statute is, instead of blanket `excluded`); keep the provenance as the underlying dataset, never "opensanctions".
2. Use the entity as an IDENTIFIER CROSSWALK: every identifier the merged entity holds (NPI, UPIN, DOB, license) is attached to every member record, which resolves name-only LEIE and state rows through an official aggregation rather than a name guess.
3. Never a label on its own: the tier and the date come from the underlying record. The commercial license (Brad) gates running it on the bulk file; the code can be written against the FtM schema now.

## 4. SAM.gov

6 percent of rows carry a valid NPI and 11,234 "NPIs" fail the Luhn check because SAM's identifier field holds UEI, CAGE and DUNS values. Quarantine those at ingest. SAM's health-care rows are reciprocal HHS exclusions (duplicates of the LEIE) or procurement debarments; tiered weak without a stated health-care cause, which is right. No further value for the label beyond corroboration.

## 5. Commercial-plan fraud convictions as a label signal

Yes, with the boundary drawn in the right place.

- **Already in the label.** 18 U.S.C. 1347 (health care fraud) covers any health care benefit program, public or private, and OIG's mandatory exclusion under 1128(a)(3) follows a felony health-care-fraud conviction regardless of the payer. Those rows are hard-tier today (223 forward positives in v13's ruler), and the DOJ harvest keeps criminal health-care-fraud cases whether the victim was Medicare, Medicaid or an insurer. So a federal conviction for defrauding a commercial plan already trains as fraud.
- **The gap.** State-court insurance-fraud convictions (state attorney general and insurance-department fraud bureaus: CA CDI, FL DFS, NY DFS, TX TDI), civil judgments won by insurers, and state licensing actions citing commercial billing fraud. These rarely reach the LEIE promptly and are not in the DOJ feed.
- **Why it is a good signal.** The billing shape is the same hands, the same codes and the same staff across payers; a provider proven to bill a commercial plan falsely is far more likely to be billing Medicaid falsely too, and the Medicaid claims are the FCA case.
- **Why it is not an FCA outcome.** No federal claim was adjudicated. It cannot be a forward-ruler positive (that would grade the model on something other than the business target) and it cannot enter the strict PurpleLab label (which is FCA-shaped by policy).
- **The design.** A training-only source tag `hcf_commercial`, admitted on a conviction or a court judgment (never an insurer's SIU allegation or an NICB referral), dated by the conviction, with provenance and the court record, weight-budgeted like broadcast rows, and switched on through a label ablation (train with and without it, grade on the FCA ruler). If it lifts the FCA ruler it stays; if not it stays a feature (a flag on the provider) rather than a label. Sources, in integrity order: state AG and insurance-department press releases (the MFCU prompt set already reads AG pages; an `insurance_fraud` set is the same shape), state court dockets, licensing-board orders that cite billing fraud.

## 6. Order of work

1. Run `ccn_npi_crosswalk --other-ids-out` on the VM once (the NPPES file is there), then `label_expansion names --other-ids` (task label_expansion_run).
2. The big-state lists with license and Medicaid-id aliases, and the type-05 join (adapter work, with the downloads).
3. OpenSanctions as a crosswalk (code against the FtM schema; data gated on the license).
4. The `hcf_commercial` training source as a label ablation after the CIA lane returns.

## 7. The operator's decision (2026-10-13) and what it changed

The bar, in the operator's words: the biggest possible label, where every provider in it carries a strong signal that they likely committed fraud in any way. That is the right bar for the TRAINING label and a different bar from the grading ruler, which has to stay on resolved outcomes so a run is graded on the business target. The two are now separated by tier:

| tier | what it holds | trains | grades |
|---|---|---|---|
| hard | convictions, pleas, settlements, fraud statutes, CIA, abuse of billing privileges, and now 1128(b)(8) / (b)(15) (the entity a sanctioned person controls, the owner of a sanctioned entity: the billing vehicle and its operator) | yes | yes |
| **charged** (new) | a fraud INDICTMENT or charge with no resolved outcome yet (a federal health-care-fraud charge ends in a conviction or plea about nine times in ten) | under `hard+charged` | never |
| weak | license actions, patient abuse, controlled substances, derivative exclusions, allegations of unstated kind, credible-allegation payment suspensions (a referral, not a charge) | no | no |
| excluded | paperwork, loan default, not operational, SAM without a health-care cause | no | no |

`--label-tier hard+charged` is accepted by the training-side tools (pursuit_dossier, accuracy_diagnostics, learning_loop, consensus, label_expansion); `prospective_label` refuses it, so the forward ruler cannot drift onto allegations. The registry records `train = hard+charged` as its own policy; v15 runs the A/B (hard versus hard+charged on the same matrix, graded on the FCA ruler) before it becomes the default. (a)(4) controlled-substance convictions stay weak pending the pill-mill ablation. Commercial-plan convictions enter as the training-only source `hcf_commercial` once harvested, under the same ablation discipline.

Two follow-ups make the charged tier much larger than the state lists alone: the case database holds 452 "charged only" rows that today never label (case_labels' outcome rule), which should become `charged` positives under the same policy, and an OUTCOME REFRESH harvest (for each charged case, the later plea or sentencing release by defendant and district) converts them to hard as the cases resolve. **Both built the same day (bundle 311):**

- `case_labels` keeps a charged-only row as source `charged_case` (criminal indictment / charge / arrest with no outcome, or a civil FCA row where the government filed or intervened in the complaint with no settlement). Direct and org_billing bases only; never the affiliated-staff tier. The export records the source and leaves the hard label at 0; `tier_label` admits the rows under `hard+charged` (and `hard+weak`) only. Per NPI, doj_case outranks charged_case, so a later outcome converts the charge on its own.
- A side finding fixed on the way: the org-name resolver kept only cases with a stated dollar amount or an intervention, so every criminal conviction release without a restitution figure (most of them) had been missing the billing-vehicle tier. The outcome rule is the gate now; the amount filter is off inside case_labels (the lookalike exemplar set keeps it).
- The outcome refresh: `case_harvest_prompts --sets outcome_refresh --case-db` lists every charged row for the research agent (plea / verdict / sentencing / settlement / dismissal / acquittal, with the evidence URL); `research_evidence outcome-refresh --in <answer.csv> --case-db` merges each resolved outcome as its own case row and marks a dismissed or acquitted charge `usable=0` (quarantined, never deleted). The registry reads `train_charged` (charged positives the policy admitted) so the v15 A/B rows are distinguishable.
