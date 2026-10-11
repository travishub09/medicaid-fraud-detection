# OPERATOR REMINDERS (auto-generated — edit docs/reminders.json, not this file)

_As of 2026-10-11. Regenerate/update: `python -m src.reminders` (`--done <key>` after you fetch something)._

## DUE NOW

_Mark each fetch done with `python -m src.reminders --done <key>` — that AUTO-QUEUES the post-download checks (vintage inspect + scheme discovery + send reports) with exact commands, so nothing has to be remembered._

- **state_capacity_rosters** — LOW PRIORITY (2026-09-16: the Northeast PI packet is clinics and therapy offices; a roster only moves a bed-based check). State licensed-facility rosters with LICENSED CAPACITY for waiver group homes, residential habilitation, adult day, assisted living, hospice houses. The one Northeast item that moves a current card: NJ DHS/DDD group homes (Special Homes of NJ). Everything else (FL AHCA, IL, IN, DC, TX, CA, MA, RI) is for a future capacity pass
  - save to: preclean/state_capacity/<ST>_<what>.csv (e.g. RI_hospice.csv, FL_adult_day.csv)  |  https://health.ri.gov/licenses/
  - due: freshness unknown  (2026-10-09 inventory: NY_certification.csv, NY_facilities.csv, NJ_acute_care.csv and NJ_ltc.xlsx are already under preclean/state_capacity/ (the three direct downloads are DONE); what remains is the optional hand-copied state lists (FL, NJ DDD group homes, IN, DC, TX, IL, RI, ME, MO, CO))
  - step 1: FL: https://quality.healthfinder.fl.gov/Facility-Provider/FacilityLocator -> facility type PPEC (prescribed pediatric extended care), then Assisted Living, then Adult Day Care -> Download/Export with the licensed capacity column; save as FL_ppec.csv, FL_assisted_living.csv, FL_adult_day.csv
  - step 2: NJ (group homes): https://www.nj.gov/humanservices/ddd/ -> licensed community residences / provider search (DHS licenses developmental-disability group homes; the DOH long-term-care file already loaded does not cover them); save as NJ_ddd_homes.csv
  - step 3: IN: https://www.in.gov/fssa/ddrs/ -> BDDS licensed group homes / supervised group living list with capacity; save as IN_group_homes.csv (retires the occupancy-artifact rows)
  - step 4: DC: https://dds.dc.gov/ or https://dchealth.dc.gov/ -> community residence facilities with bed capacity; save as DC_residential.csv (same purpose)
  - step 5: TX: https://www.hhs.texas.gov/providers/long-term-care-providers -> HCSSA directory export; save as TX_hcssa.csv
  - step 6: NY (two files, joined on Facility ID by the loader; the old Adult Care Facility Directory was retired in 2022): beds = https://health.data.ny.gov/api/views/2g9y-7kqm/rows.csv?accessType=DOWNLOAD (Health Facility Certification Information; downloads as Health_Facility_Certification_Information*.csv) and addresses = https://health.data.ny.gov/api/views/vn5v-hh5r/rows.csv?accessType=DOWNLOAD (Health Facility General Information; downloads as Health_Facility_General_Information*.csv); the copy lines below name them NY_certification.csv / NY_facilities.csv
  - step 7: NJ: hospitals with licensed beds = https://data.nj.gov/api/views/mrzk-zrvp/rows.csv?accessType=DOWNLOAD (Acute-Care Facilities; downloads as Acute-Care_Facilities*.csv -> NJ_acute_care.csv). Nursing homes are already covered by the CMS POS file; NJ assisted living / residential has no CSV export (https://healthapps.nj.gov/facilities/fssearch.aspx is a search form), so skip it unless a table is copied by hand
  - step 8: IL: https://dph.illinois.gov/topics-services/health-care-regulation/health-care-facilities -> facility directories (community integrated living arrangements, intermediate care facilities, assisted living); export or copy with the licensed capacity column; save as IL_<type>.csv
  - step 9: RI: https://health.ri.gov/licenses/ -> Health Care Facilities -> the licensed facility list/search; export or copy the table (hospice, assisted living, group homes) with the licensed bed/capacity column; save as RI_<type>.csv
  - step 10: FL: https://quality.healthfinder.fl.gov/Facility-Provider/FacilityLocator -> facility type (Adult Day Care, Assisted Living, Hospice) -> Download; keep the capacity/beds column; save as FL_<type>.csv
  - step 11: ME: https://www.maine.gov/dhhs/dlc -> licensed facilities lists (assisted housing, residential care, group homes) -> export; save as ME_<type>.csv
  - step 12: MO: https://health.mo.gov/safety/showmelongtermcare/ -> facility list with licensed beds (residential care, assisted living, ICF); save as MO_<type>.csv
  - step 13: CO: https://cdphe.colorado.gov/health-facilities -> 'find and compare' facility data download (assisted living, group homes, adult day); save as CO_<type>.csv
  - step 14: Any state: the file needs a facility name, street address, city, zip and a capacity/beds column under any common header; unusual headers are fine, tell Claude and a column map is added
  - step 15: Then build the capacity table with the commands below
  - DOWNLOAD FIRST (browser steps above), THEN paste the commands. The --done line refuses until this exists: preclean/state_capacity/*.csv

```bat
mkdir "C:\Users\treyr\OneDrive\Desktop\data\preclean\state_capacity"
for %f in ("%USERPROFILE%\Downloads\Health_Facility_Certification_Information*.csv") do copy /y "%f" "C:\Users\treyr\OneDrive\Desktop\data\preclean\state_capacity\NY_certification.csv"
for %f in ("%USERPROFILE%\Downloads\Health_Facility_General_Information*.csv") do copy /y "%f" "C:\Users\treyr\OneDrive\Desktop\data\preclean\state_capacity\NY_facilities.csv"
for %f in ("%USERPROFILE%\Downloads\Acute-Care_Facilities*.csv") do copy /y "%f" "C:\Users\treyr\OneDrive\Desktop\data\preclean\state_capacity\NJ_acute_care.csv"
"C:\Users\treyr\medicaid-fraud-detection\run_licensed_capacity.bat"
python -m src.reminders --done state_capacity_rosters
```


## COMING UP (30 days)

- leie — due 2026-10-16: OIG LEIE exclusion list (THE label source — new bans are training labels)
- nppes_practice_locations — due 2026-10-16: NPPES Practice Location file (pl_pfile, inside the monthly NPPES zip): secondary practice sites per NPI, so 'one suite' is a fact in the capacity check
- mue — due 2026-11-02: CMS MUE quarterly files (practitioner + facility)
- pos — due 2026-11-02: Provider of Services file (quarterly)
- pbj — due 2026-11-02: PBJ Daily Nurse Staffing (quarterly)
- care_compare — due 2026-11-02: Care Compare provider-data archives (hospice measures, NH citations/ownership/penalties)
- nadac — due 2026-11-02: NADAC drug pricing (drug_spread_anomaly)
- work_plan — due 2026-11-02: OIG Work Plan curated table refresh (government_interest overlay)
- hrsa_340b — due 2026-11-02: 340B OPAIS Covered Entity Daily Report (contract_pharmacy)
- nppes_deactivation — due 2026-11-07: CMS Deactivated NPI Report (invalid_identity scheme)
- owner_snapshot — due 2026-11-07: make owner-snapshot (no download — archives owner edges; ownership_turnover needs 2+ months)
- sam_exclusions — due 2026-11-09: SAM.gov exclusions extract (widens the PU label)
- warn_notices — due 2026-11-09: WARN layoff notices for the OUTREACH-TIMING tool only (sourcing/warn_monitor + event_timeline): not a model feature, never trained on. A notice is useful while its outreach window is open (6 to 18 months after the departure), so the current year and the one before are worth having; older years are expired windows. Monthly: the current-year table per state.

## STANDING TASK QUEUE

- [ ] **brad_ri_package** — Send Brad the RI dossier v4.1 + honest-grade email (ready; your call) (added 2026-08-02)
- [ ] **dossier_poll__frozen_2023-12_v5** — ~30 min after dispatch: collect Manus research into the frozen_2023-12_v5 dossier and send PURSUIT_DOSSIER.md (added 2026-08-04)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -m src.model_a.pursuit_dossier --run-dir "C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2023-12_v5" --poll
python -m src.reminders --done-task dossier_poll__frozen_2023-12_v5
```

- [ ] **registry_verdict__frozen_2023-12_v5** — Open until Claude reads the packet and returns the release-registry verdict (PROMOTE/HOLD) + scheme-triage follow-ups for frozen_2023-12_v5 — close only when you have them (added 2026-08-04)
- [ ] **dossier_poll__frozen_2023-12_v6** — ~30 min after dispatch: collect Manus research into the frozen_2023-12_v6 dossier and send PURSUIT_DOSSIER.md (added 2026-09-12)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -m src.model_a.pursuit_dossier --run-dir "C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2023-12_v6" --poll
python -m src.reminders --done-task dossier_poll__frozen_2023-12_v6
```

- [ ] **pi_facts_deep:_after_merging_bundle** — pi_facts_deep: after merging bundle 157, rerun the three fact layers with --deep (reads the paid-claims fact: ramp month, top codes, rendering clinicians, implied hours/day) and upload PI_FACTS_NATIONAL.md / PI_FACTS_MIAMI.md / PI_FACTS_NYNJ.md + the .json twins (added 2026-09-13)
- [ ] **run_packet__frozen_2021-12_v7** — Send the run packet for frozen_2021-12_v7 (every report + audit file, zero raw data) — Claude cannot see the run finished, so this zip IS the run's arrival (added 2026-09-14)

```bat
set RUN_DIR=C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2021-12_v7
powershell -Command "Compress-Archive -Force -Path '%RUN_DIR%\*.md','%RUN_DIR%\*.json' -DestinationPath '%USERPROFILE%\Desktop\run_packet.zip'"
REM then upload run_packet.zip to the chat
python -m src.reminders --done-task run_packet__frozen_2021-12_v7
```

- [ ] **pursuit_dossier__frozen_2021-12_v7** — Build + dispatch the pursuit dossier for frozen_2021-12_v7 (skip if the run script already did — check for PURSUIT_DOSSIER.md in the run folder) (added 2026-09-14)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -m src.model_a.pursuit_dossier --run-dir "C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2021-12_v7" --dispatch
python -m src.reminders --done-task pursuit_dossier__frozen_2021-12_v7
```

- [ ] **dossier_poll__frozen_2021-12_v7** — ~30 min after dispatch: collect Manus research into the frozen_2021-12_v7 dossier and send PURSUIT_DOSSIER.md (added 2026-09-14)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -m src.model_a.pursuit_dossier --run-dir "C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2021-12_v7" --poll
python -m src.reminders --done-task dossier_poll__frozen_2021-12_v7
```

- [ ] **peer_hard2:_after_the_fraud** — peer_hard2: after the fraud consensus finishes and bundle 168 is merged (use merge_bundle.bat), run run_peer_rank.bat (~3 h) and upload LABEL_LAG__hard2.md + the four SOURCE_ABLATION_OOF_FULL__active_*.md it names (added 2026-09-15)
- [ ] **state_letter:_every_named** — State letters: nine of ten named deactivated-NPI billers are live organizations paid under an old NPI (MI: Mariners Inn, Meridian; FL: Kidz Medical, CCS Medical, Memorial Hospital of Tampa psych unit, Miami Personal Management; AZ: Creative Innervisions; CO: Adeo; RI: Kent County Memorial Hospital). Draft the program-integrity letters per state once the last name (1831471960) lands; not a PI matter (added 2026-09-16)
- [ ] **pi_packet_v9_feedback** — PI Packet v11 (final for this round, 2026-09-16; v10 superseded by the limitations clock: paid_after check over Tiers 1-2, Tier 1 reordered by live dollars, Infinity/Jacoby/Perspectives to desk-first) sent to Trey: 9 Tier 1 + 7 Tier 2 + 2 Tier 3 + 6 national + 1 desk-first, 19 do-not-send rows, all 50 cards reviewed. OPEN until the PIs' feedback comes back and is folded into v10 (or Trey confirms no changes). Next capacity pass to-dos recorded in the packet: minutes map H2035=60; find the pre-2020 Hope Pediatrics NPI in the spending fact; Special Homes billed residential days vs 76 licensed places. Consensus read 2026-09-16: five-run national book is hospitals/health systems/national chains (do-not-send class); it adds no PI card; ACT HOME HEALTH SERVICES, INC (org:pac:3971547985, $18M/yr, hot 3/5, rapid_ramp) is the one new non-institutional name, held for a desk check next round (state unknown; no state column in the CSV). Successor-NPI check ran 2026-09-16: Renew Resources, Perspectives Counseling Center and Velocity have no claims through 2024-12; Perspectives is lowest priority, Velocity waits for the next data refresh. (added 2026-09-16)

```bat
(no command: paste the PIs' notes into the chat)
```

- [ ] **consensus_v7_disagreement** — CONSENSUS.md 2026-09-16: the 2021-12_v7 run scores the top-5 hospitals (Carolinas, Montefiore, Barnes Jewish, CEP America) at 0.00-0.02 while v3/v4/v5 score them 0.99-1.00; top_schemes are contract_pharmacy-led. Diagnose before the next pre-registration: is the 2023-12 freeze's 340B/contract_pharmacy input the driver, and is it absent under the 2021-12 freeze? Only 11 of 2,738 orgs are hot in >=3 runs, one in 4/5. Feed the answer into scheme triage; never gate a run on it. (added 2026-09-16)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -m src.model_a.scheme_triage --run "C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2021-12_v7"
```

- [ ] **editions_undated_reference** — LOW: fill EDITIONS.csv data_period for the reference files that carry no date inside or in their name (PECOS.csv, hud/zip_county.*, nucc/*, opensanctions/targets.simple.csv): the download date is the period. Operator entries are never overwritten. (added 2026-09-16)

```bat
notepad "C:\Users\treyr\OneDrive\Desktop\data\preclean\EDITIONS.csv"
```

- [ ] **preclean_orphans_2026_09** — Operator's call (nothing runs on them): preclean/pecos/enrollment.csv is a 321 MB duplicate of PECOS.csv; preclean/nppes/npi_deactivation.csv is an unreadable 8 MB orphan (the wired file is nppes_deactivation/deactivation_20260608.zip). Delete or move both to free space; no adapter reads either. (added 2026-09-16)

```bat
dir "C:\Users\treyr\OneDrive\Desktop\data\preclean\pecos" "C:\Users\treyr\OneDrive\Desktop\data\preclean\nppes"
```

- [ ] **build_ready_to_wire_schemes** — CLAUDE build queue from SCHEME_DISCOVERY 2026-09-16: three READY TO WIRE (nh_ownership_stakes from NH_Ownership_Nov2023 Ownership Percentage/Role; nh_penalties from NH_Penalties_Nov2023 Fine Amount; pbj_weekend_staffing from pbj_daily_staffing Hrs_RN by WorkDate weekday); the six hospice candidates need the long-format Measure Code pivot (facility.py already pivots hospice, so the discovery check should read the pivoted view); new idea: HCRIS HHA discipline visit counts (RN/LPN/PT/OT/SLP visits, Medicaid Title XIX) as a home-health capacity check (visits per licensed nurse). No operator action. (added 2026-09-16)
- [ ] **fl_la_state_lists** — Florida and Louisiana state Medicaid termination lists are still not loaded (the operator prefers to keep searching for a downloadable file). Florida: the AHCA search screen and the Provider Master List on the FLMMIS portal; a records request for the for-cause list goes to PublicRecordsReq@ahca.myflorida.com. Louisiana: LDH adverse actions is a search screen only. When a file lands: save it in Downloads with the state name in the filename, run the sorter dry run then --apply, rerun the loader and the census (state_terminations cadence commands), and upload STATE_TERMINATIONS_REPORT.md. (added 2026-09-27)
- [ ] **defendant_resolver_run** — 2026-09-28: resolver run 4 (bundle 183+) = 1,329 resolved mentions -> 1,052 NPIs / 1,212 cases; SE 314 NPIs; 1,680 research residual. Manus tiers 1 and 2 DISPATCHED 2026-09-27 (280 tracked tasks; ~448 orphan duplicates from concurrent windows are paid but untracked). OPERATOR (a day after dispatch, ONE window): poll with --resolved (the poll folds the answers into case_defendant_npis.parquet itself; no resolver re-run needed for that); after the poll re-run the strict export (task fca_strict_label_export) and upload DEFENDANT_RESOLUTION.md + DEFENDANT_RESEARCH.md. A second batch (excluding already-dispatched org_ids) only after this batch's answer quality is known. (added 2026-09-27)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -m src.enforcement.defendant_research --out-dir "C:\Users\treyr\OneDrive\Desktop\data\processed\defendant_research" --status
python -m src.enforcement.defendant_research --out-dir "C:\Users\treyr\OneDrive\Desktop\data\processed\defendant_research" --poll --resolved "C:\Users\treyr\OneDrive\Desktop\data\processed\case_defendant_npis.parquet"
REM then re-run the strict export (see fca_strict_label_export) and upload DEFENDANT_RESOLUTION.md
python -m src.reminders --done-task defendant_resolver_run
```

- [ ] **case_harvest_sources_1_6** — 2026-09-28 state: (1) deep DOJ backfill DONE (9,256 feed cases back to 2016-09-28; case table 15,240 after merge + reclassify: criminal_hcf 7,126 / civil_fca 4,594 / oig_cmp 614 / not_fraud 334 / untyped 2,572). (2) CourtListener docket backfill SKIPPED by operator decision (no account). (3) OIG harvest since 2015 WITH linked releases is RUNNING (6-8 h, network only; a bundle merge is safe while it runs). (4-6) harvest prompts DISPATCHED 2026-09-27 (98 tracked + 59 duplicate tasks). OPERATOR when the OIG window prints its summary: paste it, then run the merge line WITH --reclassify, the resolver, the strict export; upload OIG_ENFORCEMENT.md, DEFENDANT_RESOLUTION.md, FCA_LABEL_EXPORT.md. A day after dispatch (ONE window): poll the harvest prompts, review every CSV under processed\case_harvest\results\, load only accepted ones via research_evidence mfcu-cases. (added 2026-09-27)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -m src.enforcement.case_db --into "C:\Users\treyr\OneDrive\Desktop\data\enforcement\doj_cases_v3.csv" --add "C:\Users\treyr\OneDrive\Desktop\data\feeds\enforcement\doj_cases.csv" "C:\Users\treyr\OneDrive\Desktop\data\feeds\enforcement\oig_cases.csv" --reclassify
python -m src.enforcement.defendant_resolver --case-db "C:\Users\treyr\OneDrive\Desktop\data\enforcement\doj_cases_v3.csv" --provider-dim "C:\Users\treyr\OneDrive\Desktop\data\processed\provider_dim.parquet" --out "C:\Users\treyr\OneDrive\Desktop\data\processed\case_defendant_npis.parquet" --research-states FL,GA,AL,MS,TN,NC,SC,LA,KY
REM a day after dispatch:
python -m src.enforcement.case_harvest_prompts --out-dir "C:\Users\treyr\OneDrive\Desktop\data\processed\case_harvest" --poll
REM review each CSV under processed\case_harvest\results\; load an accepted one with:
python -m src.enforcement.research_evidence mfcu-cases --in "C:\Users\treyr\OneDrive\Desktop\data\processed\case_harvest\results\<accepted>.csv" --state <ST> --case-db "C:\Users\treyr\OneDrive\Desktop\data\enforcement\doj_cases_v3.csv"
python -m src.reminders --done-task case_harvest_sources_1_6
```

- [ ] **fca_strict_label_export** — PARKED UNTIL THE PURPLELAB POC STARTS (operator 2026-10-09: nothing sent, POC not started). When it starts, send two attachments: the handoff brief PurpleLab_POC_Handoff_Brief_v4.pdf (chat, 2026-09-29; re-rendered on request) and processed\fca_label_strong\labels_fca_strict.csv (5,155 rows / 4,672 NPIs: exact_id + strong + moderate). NEVER the processed\fca_label\ copy (5,544 rows, carries the 389 weak rows that verified at 74%). STATUS OF THE LABEL: verified by the research agent on 249 rows (strong 39/40, moderate 69/75 = 92%, weak 65/88 = 74%, so weak is held out); tiered by FCA relevance; graded by label_audit. It has been used as the EVALUATION ruler (the forward FCA ruler that graded v6 and v7 today) but NO model has been TRAINED with the resolved-defendant rows yet: v7 was built 2026-09-14, the resolver ran 2026-09-28; v8 is the first such run. Say 'PurpleLab sent' after sending. (added 2026-09-27)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
dir "C:\Users\treyr\OneDrive\Desktop\data\processed\fca_label_strong\labels_fca_strict.csv"
powershell -Command "(Get-Content 'C:\Users\treyr\OneDrive\Desktop\data\processed\fca_label_strong\labels_fca_strict.csv').Count - 1"
REM attach both to the PurpleLab email when the POC starts; then say 'PurpleLab sent' in chat
```

- [ ] **storage_sense_off** — OneDrive Storage Sense dehydrated the data folder on 2026-10-02 (86.4 GB freed; every parquet became a cloud-only placeholder and queries died with 'The cloud file provider is not running'). The folder is pinned (attrib +P) but the setting must be changed or it recurs in 30 days: Settings > System > Storage > Storage Sense: set 'Content will become online-only if not opened for more than' to Never, and turn 'Automatic User content cleanup' Off. Then check free disk space; the overnight runs need tens of GB. (added 2026-10-05)

```bat
wmic logicaldisk get caption,freespace,size
python -m src.reminders --done-task storage_sense_off
```

- [ ] **ohio_medicaid_cost_report_request** — Wesleyan / Lahasky stack: file the Ohio public records request (R.C. 149.43; OAG 76-011 says NF Medicaid cost reports are public) with the Ohio Department of Medicaid for the CY2021-2024 nursing facility cost reports of the 21 Ohio homes below, all schedules incl. related-party. The federal HCRIS A-8-1 run (2026-10-06) showed: Wesleyan Village filed NO related-organization worksheet 2021-2025 despite WV Healthcare Management LLC in managerial control; the Gardens group booked $9.2M (2021) / $7.7M (2022) to related home-office + property companies with $4.5M / $11.5M disallowed as above-cost, then the home-office entities vanish from the 2023+ filings; the six Ayden/Buckeye Forest homes' related-party charges grew $3.0M (2021) to $7.5M (2024) after the Lahasky acquisition. The Ohio report is the state-side version of the same schedule and the one Ohio rate-setting uses. CCNs: 365162 Wesleyan Village; 365330 Ayden Wauseon; 365453 Ayden Oregon; 365532 Ayden Greenville; 365607 Ayden Piqua; 365617 Ayden Waterville; 365849 Ayden Toledo; 365315 Capital City Gardens; 365355 Gardens of Mayfield Village; 365557 Eaton/Maple Gardens; 365559 Lansing Gardens/Rolling Hills; 365577 Marysville/Prestige Gardens; 365594 Gardens of Euclid Beach; 365933 Buckeye Terrace/Edgewood Manor of Westerville; 366002 Crestline; 366010 Summit Villa/Pinnacle; 366012 Country Meadow; 366014 Whispering Hills; 366085 Legends Care; 366158 Mount Royal Villa; 366187 Minerva Healthcare. Send to ODM public records (address on medicaid.ohio.gov, Public Records); Claude drafted the letter in chat 2026-10-06. Close when sent. (added 2026-10-06)

```bat
python -m src.reminders --done-task ohio_medicaid_cost_report_request
```

- [ ] **build_nursing_home_dollar_basis** — BUILD QUEUE (lesson from Wesleyan Village, 2026-10-06): the cost_report_fraud scheme put Wesleyan at the 98th peer percentile (hcris_cost_anomaly 0.984, subscore 0.95) in BOTH the v6 and v7 runs, yet it sat in priority tier 6_none with zero concept signals, because the leads ranking is dollar-weighted on the Medicaid HCPCS file and a nursing home's revenue (the LT per diem) has no HCPCS code: Wesleyan shows $1.7M of assisted-living waiver lines against a multi-million per-diem stream the file cannot see. Nursing facilities (taxonomy 31xx) are therefore systematically under-ranked. Fix: for 31xx orgs use the HCRIS cost report as the dollar basis (Worksheet S-3 Medicaid days and total patient revenue, G-2/G-3 revenue) via the CCN crosswalk, and let cost_report_fraud + worthless_services + ownership_integrity carry the rank for that class. Also: the raw-HCRIS A-8-1 related-party share (hcris_related_party.py) should feed hcris.py's related_party_cost_share, which the published file lacks. (added 2026-10-06)

```bat
python -m src.reminders --done-task build_nursing_home_dollar_basis
```

- [ ] **wesleyan_balance_sheet_readout** — Wesleyan Village (365162) balance sheet, read 2026-10-07 from the raw HCRIS Worksheet G: the mortgage ($23.3M in 2021, $21.2M in 2025) existed all along, so the $1.9M/yr interest that first appears on Worksheet A in 2023 is a preparer reclassification out of A&G, not new debt (correction to the 10-07 reading). The step-down (Worksheet B, B-1 statistics: campus 152,417 sq ft vs SNF 69,000; campus resident days still used 2023-25) allocates about half of all cost, $9.1M to $11.2M a year, to the campus nonreimbursable center, so the campus carve-out is NOT thin after allocation (second correction). What IS new from 2023: other-assets line 31 (the standard form label is 'Due from owners/officers') $0 (2022) -> $1.57M (2023) -> $9.39M (2024) -> $20.29M (2025); long-term line 47 (standard label 'Loans from owners') $0 -> $2.76M -> $9.59M -> $17.22M; $8.6M of plant moved to 'Investments' in 2023 and out again in 2025; $1.56M of land left the books in 2024; A-7 capital worksheet left blank (1s) in 2023 and 2024; fund balance $4.93M (2021) -> $2.44M (2024) while reporting losses. No A-8-1 any year. Operator: confirm the Worksheet G line labels against Pub 15-2 chapter 41 (CMS blocked from the session; open https://m.nber.org/hcris/2540-10/manual/pr2_41.pdf or the CMS 2540-10 form) before any of this goes to Brad in those words; the Ohio cost report records request already covers the related-party schedule where these loans must be disclosed. (added 2026-10-07)

```bat
python -m src.reminders --done-task wesleyan_balance_sheet_readout
```

- [ ] **grade_large_book_v7** — LEVER 4 (large-biller book), first grade on v7, after bundle 266 is merged: rebuild the v7 priority list (the builder now also writes provider_priority_list_large.csv: the operator book over $10M billed in the window, same order as the pursuit book) and grade that file on the FCA ruler. Read ACCURACY_DIAGNOSTICS__large.md in the v7 folder: hits at 16 / 50 against random, next to the pursuit book's 1 / 2. The band earns a v9 check only if it beats the pursuit book per row read; paste the verdict box in chat. No training, minutes. (added 2026-10-10)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -u -m src.model_a.pursuit_dossier --run-dir "C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2021-12_v7" --priority-list
python -u -m src.model_a.accuracy_diagnostics --run-dir "C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2021-12_v7" --case-db "C:\Users\treyr\OneDrive\Desktop\data\enforcement\doj_cases_v3.csv" --graph-dir "C:\Users\treyr\OneDrive\Desktop\data\graph" --defendant-npis "C:\Users\treyr\OneDrive\Desktop\data\processed\case_defendant_npis.parquet" --horizon-months 36 --list-name provider_priority_list_large.csv
python -m src.reminders --done-task grade_large_book_v7
```

- [ ] **merge_bundle276** — Merge bundle 276 (cumulative; supersedes 239-275; none of them need merging first): the VM kit (docs/VM_RUNBOOK.md, ops/vm/*.sh with the storage plan and the sync exclusions) + the reminders. NOT while v11 runs. Download the bundle card first, then paste. After merge_bundle.bat, press Enter once, THEN paste the push line on its own (the push is what the VM clones from). (added 2026-10-11)

```bat
for %f in ("%USERPROFILE%\Downloads\bundle276*.bundle") do copy /y "%f" "C:\Users\treyr\bundle276.bundle"
cd /d C:\Users\treyr\medicaid-fraud-detection
git checkout -- docs/reminders.json docs/OPERATOR_REMINDERS.md
"C:\Users\treyr\medicaid-fraud-detection\merge_bundle.bat" "C:\Users\treyr\bundle276.bundle"
REM press Enter once, then paste the next line on its own:
git push origin claude/happy-mccarthy-3skrn3
python -m src.reminders --done-task merge_bundle276
```

- [ ] **fuzzy_tier_verification** — BATCH 3 DISPATCHED 2026-10-11 (23 rows in 3 Manus tasks under rule 4; batches 1 and 2 were 108 and 39). NEXT: 30+ min after the dispatch poll it, paste the LABEL_VERIFICATION.md tally, then rerun the resolver with all THREE verdict files and copy the table over so v11 trains with every promoted row. Last-chance rule: 90%+ confirmed or the fuzzy tier is retired (confirmed rows promote either way). (added 2026-10-10)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -u -m src.model_a.label_verification --out-dir "C:\Users\treyr\OneDrive\Desktop\data\processed\fca_label_fuzzy\verify3" --poll
type "C:\Users\treyr\OneDrive\Desktop\data\processed\fca_label_fuzzy\verify3\LABEL_VERIFICATION.md"
python -u -m src.enforcement.defendant_resolver --case-db "C:\Users\treyr\OneDrive\Desktop\data\enforcement\doj_cases_v3.csv" --provider-dim "C:\Users\treyr\OneDrive\Desktop\data\processed\provider_dim.parquet" --out "C:\Users\treyr\OneDrive\Desktop\data\processed\fuzzy_resolver\case_defendant_npis.parquet" --research-states FL,GA,AL,MS,TN,NC,SC,LA,KY --nppes-file "C:\Users\treyr\OneDrive\Desktop\data\preclean\nppes" --verification "C:\Users\treyr\OneDrive\Desktop\data\processed\fca_label_fuzzy\verify\verification_results.csv" "C:\Users\treyr\OneDrive\Desktop\data\processed\fca_label_fuzzy\verify2\verification_results.csv" "C:\Users\treyr\OneDrive\Desktop\data\processed\fca_label_fuzzy\verify3\verification_results.csv"
copy /y "C:\Users\treyr\OneDrive\Desktop\data\processed\fuzzy_resolver\case_defendant_npis.parquet" "C:\Users\treyr\OneDrive\Desktop\data\processed\case_defendant_npis.parquet"
python -m src.reminders --done-task fuzzy_tier_verification
```

- [ ] **registry_verdict__frozen_2021-12_v8** — VERDICT DELIVERED 2026-10-10: HOLD on frozen_2021-12_v8, cause = pipeline error, not model performance (the runner's export line dropped --case-db / --defendant-npis so the matrix trained on 920 exclusion positives and ZERO DOJ case labels; 843,551 servicing-only rows doubled the universe; two sv_* shares out of bounds; five dead features). hits@50 = 0 (random 0.3), top-1% FCA lift 0.76, Jaccard 0.00. The v8 dossier was NOT dispatched to Manus (skipped: a chance-level list). Read docs/ACCURACY_READOUT_v8_2026-10-10.md (in bundle 268), then close this with the line below. (added 2026-10-10)

```bat
python -m src.reminders --done-task registry_verdict__frozen_2021-12_v8
```

- [ ] **registry_verdict__frozen_2021-12_v9** — VERDICT DELIVERED 2026-10-11: HOLD on frozen_2021-12_v9, INVALID BY CONSTRUCTION. The run used the OLD v9 runner (console: 'step 1/10' not '/12', the findstr guard printed nothing, '[doj_case] skipped: no --case-db', '843,551 servicing-only rows added'): Windows saved the refreshed download as 'run_v9_night (1).bat' and the copy line picked the stale file. v9 is v8 repeated (920 exclusion positives, zero DOJ case labels, doubled universe; hits@50 = 0 on every book and both broadcast A/B arms). No packet, no dossier dispatch, no poll. The rerun is v10 under a NEW runner filename. Close this once read. (added 2026-10-11)

```bat
python -m src.reminders --done-task registry_verdict__frozen_2021-12_v9
```

- [ ] **registry_verdict__frozen_2021-12_v10** — VERDICT DELIVERED 2026-10-11: HOLD on frozen_2021-12_v10, on ONE real check: top-50 stability (Jaccard 0.09 vs 0.7; the dollar-weighted score below rank 1 is a 0.2000 plateau so the order inside it is noise, though every re-shuffle still finds 2 hits). The other recorded FAIL (prec50_lo > base50) actually PASSED (0.011 > 0.005): the registry parser compared the metric to the literal word 'base50'; bundle 272 fixes it. v10 is the first valid run of the series: 2 hits at 16 and 50 (random 0.1 / 0.3, p 0.002 / 0.024), 3 at 100, 4 at 250, median lead 26 months, institutional share 0. Five sources died of memory (servicing, mue, sector_schemes, medicaid_enrollment, billing_lm): v11 needs a quiet machine. Operator decision 2026-10-11: NO Manus dispatch on a HOLD run (the v10 dossier tasks are closed as skipped). Read docs/ACCURACY_READOUT_v10_2026-10-11.md, re-register v10 after bundle 273 is merged (clears the false FAIL), then close. (added 2026-10-11)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -u -m src.model_a.run_registry --data-root "C:\Users\treyr\OneDrive\Desktop\data" --run-dir "C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2021-12_v10"
python -m src.reminders --done-task registry_verdict__frozen_2021-12_v10
```

- [ ] **v11_prereg_and_run** — v11 = v10 with the five memory casualties back and the regularized scorer (the plateau fix), same 2021-12 freeze. ORDER: (1) merge bundle 273; (2) the fuzzy task through its batch-3 poll (so v11 trains with every promoted row); (3) copy run_v11_night.bat (NEW filename) from Downloads; (4) the findstr line MUST print the gate line; (5) pre-register v11 (v10's checks, parser fixed, Jaccard 0.7 is the bar the plateau fix must clear); (6) close the browser and any other Python, then start the runner: it refuses to start under 10 GB free and stops after the export if any of servicing / mue / sector_schemes / medicaid_enrollment / billing_lm is skipped; (7) say 'v11 finished' when it prints DONE and zip the packet with the line it prints. Never merge a bundle while it runs. (added 2026-10-11)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
copy /y "%USERPROFILE%\Downloads\run_v11_night.bat" "C:\Users\treyr\medicaid-fraud-detection\run_v11_night.bat"
findstr /C:"free-memory gate" "C:\Users\treyr\medicaid-fraud-detection\run_v11_night.bat"
REM the findstr line must print the gate line; if it prints nothing STOP and say so in chat
python -u -m src.model_a.run_registry --data-root "C:\Users\treyr\OneDrive\Desktop\data" --preregister --tag frozen_2021-12_v11 --goal "The regularized scorer removes the 0.2000 plateau so the operator book's top 50 is stable across fold draws while keeping v10's FCA hits at 50, with every source present" --expected "hits@50 on provider_priority_list_pursuit.csv at or above v10's 2, lower Wilson bound above random, lead-time share beyond the gap at least half, institutional share at most 40 percent, top-50 Jaccard at least 0.7 across repeats, no source skipped for memory" --check "hits50 >= 2" "prec50_lo > base50" "lead_ge_gap >= 0.5" "inst_share50 <= 0.4" "top50_jaccard >= 0.7" "eval_policy = fca" "horizon = 36" "frozen = True" "seeds >= 5" "groups = case_cluster" "broadcast = weight"
"C:\Users\treyr\medicaid-fraud-detection\run_v11_night.bat"
REM when it prints [v11] DONE: say 'v11 finished' in chat
python -m src.reminders --done-task v11_prereg_and_run
```

- [ ] **compute_box_decision** — DECIDED 2026-10-11: move the night runs to a VM, cost permitting (the laptop is 16 GB with 840 MB free; the resolver alone takes 4.8 GB). Cost: AWS r6i.4xlarge or Azure E16s v5 (16 vCPU / 128 GB, about $1 an hour, about $6 a run) plus a 1 TB disk (the data root is 291 GB) at about $80 a month while stopped, and an S3 mirror made from the VM (about $7 a month) as the durable copy; the 64 GB class is half that per hour. The kit is in the repo: docs/VM_RUNBOOK.md (costs, provisioning, rclone-from-OneDrive data pull, running under tmux, bringing the packet back), ops/vm/setup_ubuntu.sh, ops/vm/sync_data.sh, ops/vm/run_frozen_night.sh (full embeddings: --max-embed-nodes 0, rebuilds graph_asof_2021-12_full, a 24 GB free-RAM gate, stops if any of the six heavy sources is skipped). v11 runs on the laptop tonight; v12 is the first VM run. Close this when the VM exists and the data pull has finished. (added 2026-10-11)

```bat
REM on the VM, after it boots (Ubuntu 22.04 / 24.04, 1 TB gp3 disk, SSH key only):
curl -fsSL https://raw.githubusercontent.com/travishub09/medicaid-fraud-detection/claude/happy-mccarthy-3skrn3/ops/vm/setup_ubuntu.sh | bash
rclone config
bash ~/medicaid-fraud-detection/ops/vm/sync_data.sh
REM then the S3 mirror (docs/VM_RUNBOOK.md section 5), then say 'VM ready' in chat
python -m src.reminders --done-task compute_box_decision
```


## After ANY download session (standing rule)

1. `python -m src.vintage_inspect --data-root <data>` — dates + right-file check
2. `python -m src.model_a.scheme_discovery --data-root <data>` — new-scheme scan
3. Send me VINTAGE_INSPECT.md + SCHEME_DISCOVERY.md + any header that failed.

## After ANY model run (standing rule): send the run packet

The results are half the point; the feedback loops (scheme triage, release gating, expectations, source audit) need the run's OWN files. One command collects every report into a zip — replace RUN_DIR with the run folder (e.g. frozen_2023-12_v4), then upload run_packet.zip to the chat:

```bat
set RUN_DIR=C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2023-12_v4
powershell -Command "Compress-Archive -Force -Path '%RUN_DIR%\*.md','%RUN_DIR%\*.json' -DestinationPath '%USERPROFILE%\Desktop\run_packet.zip'"
```

That captures: PROVIDER_FEATURES_EXPORT_REPORT, SOURCES_REPORT, EXPECTATIONS, SCHEME_HEALTH + SCHEME_TRIAGE, CASE_VALIDATION, PURSUIT_DOSSIER, RESULTS_DIGEST, OPERATING_POINTS, the feature manifest, and the run-registry row. No parquet/csv goes in the zip (reports only). A run is not closed until the packet is sent and Claude has updated the release registry + triage follow-ups from it.

