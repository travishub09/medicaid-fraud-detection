# OPERATOR REMINDERS (auto-generated — edit docs/reminders.json, not this file)

_As of 2026-09-16. Regenerate/update: `python -m src.reminders` (`--done <key>` after you fetch something)._

## DUE NOW

_Mark each fetch done with `python -m src.reminders --done <key>` — that AUTO-QUEUES the post-download checks (vintage inspect + scheme discovery + send reports) with exact commands, so nothing has to be remembered._

- **nppes_deactivation** — CMS Deactivated NPI Report (invalid_identity scheme)
  - save to: preclean/nppes_deactivation/ (keep as the .zip; loader unzips)  |  https://download.cms.gov/nppes/NPI_Files.html
  - due: freshness unknown
  - step 1: Open https://download.cms.gov/nppes/NPI_Files.html
  - step 2: Under 'Weekly/Monthly Deactivation', download the 'NPPES Deactivated NPI Report' .zip (do NOT unzip)

```bat
move "%USERPROFILE%\Downloads\NPPES_Deactivated_*.zip" "C:\Users\treyr\OneDrive\Desktop\data\preclean\nppes_deactivation\"
python -m src.reminders --done nppes_deactivation
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

- **oig_cia** — LOW PRIORITY: OIG Corporate Integrity Agreement list (recall benchmark for the DOJ case DB in the label audit + a mild risk prior; changes no PI lead). Five-minute copy from the 'CIA Documents' table page, not the overview page; never worth Manus money
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

- **state_capacity_rosters** — LOW PRIORITY (2026-09-16: the Northeast PI packet is clinics and therapy offices; a roster only moves a bed-based check). State licensed-facility rosters with LICENSED CAPACITY for waiver group homes, residential habilitation, adult day, assisted living, hospice houses. The one Northeast item that moves a current card: NJ DHS/DDD group homes (Special Homes of NJ). Everything else (FL AHCA, IL, IN, DC, TX, CA, MA, RI) is for a future capacity pass
  - save to: preclean/state_capacity/<ST>_<what>.csv (e.g. RI_hospice.csv, FL_adult_day.csv)  |  https://health.ri.gov/licenses/
  - due: freshness unknown
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
- sam_exclusions — due 2026-10-16: SAM.gov exclusions extract (widens the PU label)
- nppes_practice_locations — due 2026-10-16: NPPES Practice Location file (pl_pfile, inside the monthly NPPES zip): secondary practice sites per NPI, so 'one suite' is a fact in the capacity check

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
- [ ] **post_download_checks** — New files landed — run the checks below, then send VINTAGE_INSPECT.md + SCHEME_DISCOVERY.md to Claude (added 2026-09-16)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -m src.vintage_inspect --data-root "C:/Users/treyr/OneDrive/Desktop/data"
python -m src.model_a.scheme_discovery --data-root "C:/Users/treyr/OneDrive/Desktop/data"
python -m src.reminders --done-task post_download_checks
```

- [ ] **state_letter:_every_named** — State letters: nine of ten named deactivated-NPI billers are live organizations paid under an old NPI (MI: Mariners Inn, Meridian; FL: Kidz Medical, CCS Medical, Memorial Hospital of Tampa psych unit, Miami Personal Management; AZ: Creative Innervisions; CO: Adeo; RI: Kent County Memorial Hospital). Draft the program-integrity letters per state once the last name (1831471960) lands; not a PI matter (added 2026-09-16)
- [ ] **pi_packet_v9_feedback** — PI Packet v9 (final for this round, 2026-09-16) sent to Trey: 9 Tier 1 + 7 Tier 2 + 2 Tier 3 + 6 national + 1 desk-first, 19 do-not-send rows, all 50 cards reviewed. OPEN until the PIs' feedback comes back and is folded into v10 (or Trey confirms no changes). Next capacity pass to-dos recorded in the packet: minutes map H2035=60; find the pre-2020 Hope Pediatrics NPI in the spending fact; Special Homes billed residential days vs 76 licensed places. (added 2026-09-16)

```bat
(no command: paste the PIs' notes into the chat)
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

