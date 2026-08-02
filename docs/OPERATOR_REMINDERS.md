# OPERATOR REMINDERS (auto-generated — edit docs/reminders.json, not this file)

_As of 2026-08-02. Regenerate/update: `python -m src.reminders` (`--done <key>` after you fetch something)._

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

## COMING UP (30 days)

Nothing in the next 30 days.

## STANDING TASK QUEUE

- [ ] **merge_bundle131** — Merge bundle 131 (supersedes 123-130) (added 2026-08-02)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
git fetch "%USERPROFILE%\Downloads\bundle131nag.bundle" claude/happy-mccarthy-3skrn3
git merge FETCH_HEAD
python -m src.reminders --done-task merge_bundle131
```

- [ ] **pdf_part2_redownloads** — Fetch the 4 re-downloads in Fix_and_Fetch PDF Part 2: Market Saturation State-County, Order and Referring, OPAIS dated, NADAC 2023 (URLs + save paths in the PDF) (added 2026-08-02)
- [ ] **post_download_checks** — After the moves + re-downloads: run the checks below, then send VINTAGE_INSPECT.md + SCHEME_DISCOVERY.md to Claude (added 2026-08-02)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -m src.vintage_inspect --data-root "C:\Users\treyr\OneDrive\Desktop\data"
python -m src.model_a.scheme_discovery --data-root "C:\Users\treyr\OneDrive\Desktop\data"
python -m src.reminders --done-task post_download_checks
```

- [ ] **dossier_poll** — Collect the Manus research and rebuild the pursuit dossier, then send PURSUIT_DOSSIER.md (added 2026-08-02)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -m src.model_a.pursuit_dossier --run-dir "C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2023-12_v3" --poll
python -m src.reminders --done-task dossier_poll
```

- [ ] **owners_2022_editions** — While on data.cms.gov: grab a 2022-dated edition of each All-Owners dataset (version history on each dataset page) into preclean/owners_prior/ — unlocks leakage-correct FROZEN ownership churn (needs 2 pre-cutoff editions; you have one) (added 2026-08-02)
- [ ] **label_night** — Run run_label_night.bat on the first free night (never run yet; feeds the label harvest that lifts the v4 HOLD) (added 2026-08-02)

```bat
"%USERPROFILE%\Downloads\run_label_night.bat"
```

- [ ] **brad_ri_package** — Send Brad the RI dossier v4.1 + honest-grade email (ready; your call) (added 2026-08-02)
- [ ] **docgraph_placement** — Confirm the DocGraph file sits in preclean/docgraph/ with its year window in the filename (inspection saw DocGraph_Hop_Teaming_2022.csv — looks right; confirm and close this) (added 2026-08-02)

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
