# OPERATOR REMINDERS (auto-generated — edit docs/reminders.json, not this file)

_As of 2026-09-16. Regenerate/update: `python -m src.reminders` (`--done <key>` after you fetch something)._

## DUE NOW

_Mark each fetch done with `python -m src.reminders --done <key>` — that AUTO-QUEUES the post-download checks (vintage inspect + scheme discovery + send reports) with exact commands, so nothing has to be remembered._

- **leie** — OIG LEIE exclusion list (THE label source — new bans are training labels)
  - save to: preclean/caught.csv (keep the old file as caught_<yyyymm>.csv.bak)  |  https://oig.hhs.gov/exclusions/exclusions_list.asp
  - due: freshness unknown
  - step 1: Open https://oig.hhs.gov/exclusions/exclusions_list.asp
  - step 2: Click the 'UPDATED LEIE Database' CSV link (the full database, not the monthly supplement)
  - step 3: Then paste the commands below (they back up the old file, then swap in the new one)

```bat
copy "C:\Users\treyr\OneDrive\Desktop\data\preclean\caught.csv" "C:\Users\treyr\OneDrive\Desktop\data\preclean\caught_%date:~10,4%%date:~4,2%.csv.bak"
move "%USERPROFILE%\Downloads\UPDATED.csv" "C:\Users\treyr\OneDrive\Desktop\data\preclean\caught.csv"
python -m src.reminders --done leie
```

- **nppes_deactivation** — CMS Deactivated NPI Report (invalid_identity scheme)
  - save to: preclean/nppes_deactivation/ (keep as the .zip; loader unzips)  |  https://download.cms.gov/nppes/NPI_Files.html
  - due: freshness unknown
  - step 1: Open https://download.cms.gov/nppes/NPI_Files.html
  - step 2: Under 'Weekly/Monthly Deactivation', download the 'NPPES Deactivated NPI Report' .zip (do NOT unzip)

```bat
move "%USERPROFILE%\Downloads\NPPES_Deactivated_*.zip" "C:\Users\treyr\OneDrive\Desktop\data\preclean\nppes_deactivation\"
python -m src.reminders --done nppes_deactivation
```

- **sam_exclusions** — SAM.gov exclusions extract (widens the PU label)
  - save to: preclean/sam/  |  https://sam.gov/data-services
  - due: freshness unknown
  - step 1: Open https://sam.gov/data-services and pick 'Exclusions' → 'Public V2' current extract (CSV)
  - step 2: No login needed for the public extract

```bat
move "%USERPROFILE%\Downloads\SAM_Exclusions*.CSV" "C:\Users\treyr\OneDrive\Desktop\data\preclean\sam\sam_exclusions_%date:~10,4%%date:~4,2%.csv"
python -m src.reminders --done sam_exclusions
```

- **owner_snapshot** — make owner-snapshot (no download — archives owner edges; ownership_turnover needs 2+ months)
  - save to: run from repo: make owner-snapshot
  - due: freshness unknown
  - step 1: Nothing to download — paste the commands below

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -m src.entity_graph.ownership_snapshot --owned-by "C:\Users\treyr\OneDrive\Desktop\data\graph\edges\owned_by_edges.parquet" --snapshots-dir "C:\Users\treyr\OneDrive\Desktop\data\owner_snapshots"
python -m src.reminders --done owner_snapshot
```

- **warn_notices** — WARN layoff notices for the surge monitor (whistleblower timing)
  - save to: preclean/warn/
  - due: freshness unknown
  - step 1: Google '<state> WARN notices' for target states (start: RI, NC, FL, TX, CA) — each state DOL posts a table
  - step 2: Download/export the current-year table as CSV or XLSX per state
  - step 3: Save as "C:\Users\treyr\OneDrive\Desktop\data\preclean\warn\warn_<state>_<yyyymm>.csv"
- **nppes_2021_12** — NPPES December-2021 monthly edition (freezes the address layer for the v7 2021-12 rolling-origin run; one-time)
  - save to: preclean/nppes/npidata_pfile_20050523-20211212.csv (extracted CSV, several GB; next to the 2023 edition)  |  https://data.nber.org/npi/
  - due: freshness unknown
  - step 1: Open https://data.nber.org/npi/ (NBER archives every monthly NPPES release; CMS itself keeps only the current month)
  - step 2: Find the December 2021 full NPPES Data Dissemination (the zip whose main csv is named npidata_pfile_20050523-20211212.csv or the closest December-2021 date)
  - step 3: Download and EXTRACT the zip; keep only the big npidata_pfile_*.csv
  - step 4: Then paste the commands below (moves it next to the 2023 edition and marks the item done)

```bat
move "%USERPROFILE%\Downloads\npidata_pfile_20050523-2021*.csv" "C:/Users/treyr/OneDrive/Desktop/data\preclean\nppes\"
dir "C:/Users/treyr/OneDrive/Desktop/data\preclean\nppes\npidata_pfile_*2021*.csv"
python -m src.reminders --done nppes_2021_12
```

- **hospice_2021_archive** — Archived Hospice Provider (Care Compare) file with measure periods ending <= 2021-12 (the 2021-12 freeze emptied the Nov-2023 edition: hospice_live_discharge_rate all-NaN, 2 expectation FAILs in v7; one-time)
  - save to: preclean/facility/Hospice_Provider_<yyyymm>.csv (keep the Nov-2023 file too; the export picks the edition by vintage)  |  https://data.cms.gov/provider-data/archived-data/hospice-care
  - due: freshness unknown
  - step 1: Open https://data.cms.gov/provider-data/archived-data/hospice-care
  - step 2: Pick a release from late 2021 or early 2022 (measure periods ending in 2021) and download its zip
  - step 3: Extract; keep the Hospice_Provider_*.csv (the provider-level file with live-discharge / GIP / NF-care measures)
  - step 4: Then paste the commands below

```bat
move "%USERPROFILE%\Downloads\Hospice_Provider*.csv" "C:/Users/treyr/OneDrive/Desktop/data\preclean\facility\"
dir "C:/Users/treyr/OneDrive/Desktop/data\preclean\facility\Hospice_Provider*.csv"
python -m src.reminders --done hospice_2021_archive
```

- **oig_cia** — OIG Corporate Integrity Agreement list (official roster of orgs that settled an FCA matter; the recall benchmark for the DOJ case DB + a Model A risk prior)
  - save to: preclean/cia/cia_list.csv  |  https://oig.hhs.gov/compliance/corporate-integrity-agreements/cia-documents.asp
  - due: freshness unknown
  - step 1: Open https://oig.hhs.gov/compliance/corporate-integrity-agreements/cia-documents.asp
  - step 2: Use the page's 'Export' / 'Download CSV' control if present; otherwise select the whole table, paste into Excel, and save as CSV with the headers Party, State, Effective Date, Status, Link
  - step 3: Save the file as cia_list.csv, then paste the commands below (they create the folder and move the file)

```bat
mkdir "C:\Users\treyr\OneDrive\Desktop\data\preclean\cia"
move "%USERPROFILE%\Downloads\cia_list.csv" "C:\Users\treyr\OneDrive\Desktop\data\preclean\cia\cia_list.csv"
python -m src.reminders --done oig_cia
```

- **nppes_practice_locations** — NPPES Practice Location file (pl_pfile, inside the monthly NPPES zip): secondary practice sites per NPI, so 'one suite' is a fact in the capacity check
  - save to: preclean/nppes/pl_pfile_<dates>.csv  |  https://download.cms.gov/nppes/NPI_Files.html
  - due: freshness unknown
  - step 1: Open https://download.cms.gov/nppes/NPI_Files.html and download the 'NPPES Data Dissemination' monthly zip (the full file, ~1 GB zipped) if you do not already have this month's
  - step 2: Extract ONLY the practice-location file from the zip with the first command below (the zip is huge; do not unzip everything)
  - step 3: Move it into preclean\nppes with the second command; run_capacity.bat finds it automatically

```bat
for %f in ("%USERPROFILE%\Downloads\NPPES_Data_Dissemination_*.zip") do tar -xf "%f" -C "%USERPROFILE%\Downloads" "pl_pfile_*.csv"
move "%USERPROFILE%\Downloads\pl_pfile_*.csv" "C:\Users\treyr\OneDrive\Desktop\data\preclean\nppes\"
python -m src.reminders --done nppes_practice_locations
```

- **state_capacity_rosters** — State licensed-facility rosters with LICENSED CAPACITY (beds/slots) for waiver group homes, residential habilitation, adult day, assisted living, hospice houses: the capacity side of the Nick Shirley check for providers the CMS POS file never covers. Priority after the capacity run of 2026-09-16: IN and DC first (the occupancy-over-100% table is Indiana and DC waiver residential providers whose POS beds cover only the certified ICF), then IL ($507M on facility codes), TX (pediatric therapy home-health agencies: therapist headcount on the HHSC license), CA, MA, FL, RI
  - save to: preclean/state_capacity/<ST>_<what>.csv (e.g. RI_hospice.csv, FL_adult_day.csv)  |  https://health.ri.gov/licenses/
  - due: freshness unknown
  - step 1: IN: https://www.in.gov/fssa/ddrs/ -> Bureau of Developmental Disabilities Services -> provider search / licensed group homes and supervised group living list (export or copy with capacity); save as IN_group_homes.csv
  - step 2: DC: https://dhcf.dc.gov/ or https://dds.dc.gov/ -> DDS provider directory / licensed community residence facilities (DC Health licensing list with bed capacity); save as DC_residential.csv
  - step 3: TX: https://www.hhs.texas.gov/providers/long-term-care-providers -> home and community support services agencies (HCSSA) directory export; save as TX_hcssa.csv (no capacity column: the agency list still names the license and address)
  - step 4: NY (two files, joined on Facility ID by the loader; the old Adult Care Facility Directory was retired in 2022): beds = https://health.data.ny.gov/api/views/2g9y-7kqm/rows.csv?accessType=DOWNLOAD (Health Facility Certification Information; downloads as Health_Facility_Certification_Information*.csv) and addresses = https://health.data.ny.gov/api/views/vn5v-hh5r/rows.csv?accessType=DOWNLOAD (Health Facility General Information; downloads as Health_Facility_General_Information*.csv); the copy lines below name them NY_certification.csv / NY_facilities.csv
  - step 5: NJ: hospitals with licensed beds = https://data.nj.gov/api/views/mrzk-zrvp/rows.csv?accessType=DOWNLOAD (Acute-Care Facilities; downloads as Acute-Care_Facilities*.csv -> NJ_acute_care.csv). Nursing homes are already covered by the CMS POS file; NJ assisted living / residential has no CSV export (https://healthapps.nj.gov/facilities/fssearch.aspx is a search form), so skip it unless a table is copied by hand
  - step 6: IL: https://dph.illinois.gov/topics-services/health-care-regulation/health-care-facilities -> facility directories (community integrated living arrangements, intermediate care facilities, assisted living); export or copy with the licensed capacity column; save as IL_<type>.csv
  - step 7: RI: https://health.ri.gov/licenses/ -> Health Care Facilities -> the licensed facility list/search; export or copy the table (hospice, assisted living, group homes) with the licensed bed/capacity column; save as RI_<type>.csv
  - step 8: FL: https://quality.healthfinder.fl.gov/Facility-Provider/FacilityLocator -> facility type (Adult Day Care, Assisted Living, Hospice) -> Download; keep the capacity/beds column; save as FL_<type>.csv
  - step 9: ME: https://www.maine.gov/dhhs/dlc -> licensed facilities lists (assisted housing, residential care, group homes) -> export; save as ME_<type>.csv
  - step 10: MO: https://health.mo.gov/safety/showmelongtermcare/ -> facility list with licensed beds (residential care, assisted living, ICF); save as MO_<type>.csv
  - step 11: CO: https://cdphe.colorado.gov/health-facilities -> 'find and compare' facility data download (assisted living, group homes, adult day); save as CO_<type>.csv
  - step 12: Any state: the file needs a facility name, street address, city, zip and a capacity/beds column under any common header; unusual headers are fine, tell Claude and a column map is added
  - step 13: Then build the capacity table with the commands below

```bat
mkdir "C:\Users\treyr\OneDrive\Desktop\data\preclean\state_capacity"
for %f in ("%USERPROFILE%\Downloads\Health_Facility_Certification_Information*.csv") do copy /y "%f" "C:\Users\treyr\OneDrive\Desktop\data\preclean\state_capacity\NY_certification.csv"
for %f in ("%USERPROFILE%\Downloads\Health_Facility_General_Information*.csv") do copy /y "%f" "C:\Users\treyr\OneDrive\Desktop\data\preclean\state_capacity\NY_facilities.csv"
for %f in ("%USERPROFILE%\Downloads\Acute-Care_Facilities*.csv") do copy /y "%f" "C:\Users\treyr\OneDrive\Desktop\data\preclean\state_capacity\NJ_acute_care.csv"
"C:\Users\treyr\medicaid-fraud-detection\run_licensed_capacity.bat"
python -m src.reminders --done state_capacity_rosters
```


## COMING UP (30 days)

Nothing in the next 30 days.

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

- [ ] **registry_verdict__frozen_2023-12_v6** — Open until Claude reads the packet and returns the release-registry verdict (PROMOTE/HOLD) + scheme-triage follow-ups for frozen_2023-12_v6 — close only when you have them (added 2026-09-12)
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

- [ ] **registry_verdict__frozen_2021-12_v7** — Open until Claude reads the packet and returns the release-registry verdict (PROMOTE/HOLD) + scheme-triage follow-ups for frozen_2021-12_v7 — close only when you have them (added 2026-09-14)
- [ ] **peer_hard2:_after_the_fraud** — peer_hard2: after the fraud consensus finishes and bundle 168 is merged (use merge_bundle.bat), run run_peer_rank.bat (~3 h) and upload LABEL_LAG__hard2.md + the four SOURCE_ABLATION_OOF_FULL__active_*.md it names (added 2026-09-15)
- [ ] **v7_research_(manus,_20_prompts** — v7_research (Manus, 20 prompts off the Tier A shortlist): 4 dispatched 2026-09-16, 16 waiting. Loop until 20/20 completed, then --fetch and upload v7_research_results.json to Claude (added 2026-09-16)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python field_research.py --poll --prompts v7_research_prompts.jsonl
python field_research.py --dispatch --batch 4 --prompts v7_research_prompts.jsonl
python field_research.py --fetch --prompts v7_research_prompts.jsonl
python -m src.reminders --done-task v7_research_(manus,_20_prompts
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
