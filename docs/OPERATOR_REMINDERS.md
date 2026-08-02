# OPERATOR REMINDERS (auto-generated — edit docs/reminders.json, not this file)

_As of 2026-08-02. Regenerate/update: `python -m src.reminders` (`--done <key>` after you fetch something)._

## DUE NOW

_Mark each fetch done with `python -m src.reminders --done <key>` — that AUTO-QUEUES the post-download checks (vintage inspect + scheme discovery + send reports) with exact commands, so nothing has to be remembered._

- **leie** — OIG LEIE exclusion list (THE label source — new bans are training labels)
  - save to: preclean/caught.csv (keep the old file as caught_<yyyymm>.csv.bak)  |  https://oig.hhs.gov/exclusions/exclusions_list.asp
  - due: freshness unknown
  - step 1: Open https://oig.hhs.gov/exclusions/exclusions_list.asp
  - step 2: Click the 'UPDATED LEIE Database' CSV link (the full database, not the monthly supplement)
  - step 3: Back up the old file first: copy "C:\Users\treyr\OneDrive\Desktop\data\preclean\caught.csv" "C:\Users\treyr\OneDrive\Desktop\data\preclean\caught_<yyyymm>.csv.bak"
  - step 4: Then replace it: move "%USERPROFILE%\Downloads\UPDATED.csv" "C:\Users\treyr\OneDrive\Desktop\data\preclean\caught.csv"
- **nppes_deactivation** — CMS Deactivated NPI Report (invalid_identity scheme)
  - save to: preclean/nppes_deactivation/ (keep as the .zip; loader unzips)  |  https://download.cms.gov/nppes/NPI_Files.html
  - due: freshness unknown
  - step 1: Open https://download.cms.gov/nppes/NPI_Files.html
  - step 2: Under 'Weekly/Monthly Deactivation', download the 'NPPES Deactivated NPI Report' .zip (do NOT unzip)
  - step 3: move "%USERPROFILE%\Downloads\NPPES_Deactivated_*.zip" "C:\Users\treyr\OneDrive\Desktop\data\preclean\nppes_deactivation\"
- **sam_exclusions** — SAM.gov exclusions extract (widens the PU label)
  - save to: preclean/sam/  |  https://sam.gov/data-services
  - due: freshness unknown
  - step 1: Open https://sam.gov/data-services and pick 'Exclusions' → 'Public V2' current extract (CSV)
  - step 2: No login needed for the public extract
  - step 3: Save with the month in the name: move "%USERPROFILE%\Downloads\SAM_Exclusions*.CSV" "C:\Users\treyr\OneDrive\Desktop\data\preclean\sam\sam_exclusions_<yyyymm>.csv"
- **owner_snapshot** — make owner-snapshot (no download — archives owner edges; ownership_turnover needs 2+ months)
  - save to: run from repo: make owner-snapshot
  - due: freshness unknown
  - step 1: Open a command window in C:\Users\treyr\medicaid-fraud-detection
  - step 2: set MEDICAID_DATA_ROOT=C:\Users\treyr\OneDrive\Desktop\data
  - step 3: make owner-snapshot   (no make? ask Claude for the python -m equivalent)
- **warn_notices** — WARN layoff notices for the surge monitor (whistleblower timing)
  - save to: preclean/warn/
  - due: freshness unknown
  - step 1: Google '<state> WARN notices' for target states (start: RI, NC, FL, TX, CA) — each state DOL posts a table
  - step 2: Download/export the current-year table as CSV or XLSX per state
  - step 3: Save as "C:\Users\treyr\OneDrive\Desktop\data\preclean\warn\warn_<state>_<yyyymm>.csv"

## COMING UP (30 days)

Nothing in the next 30 days.

## STANDING TASK QUEUE

- [ ] **send_scheme_discovery** — Merge bundle 123, run scheme_discovery, send SCHEME_DISCOVERY.md (added 2026-08-02)
- [ ] **dossier_poll** — Run pursuit_dossier --poll (20-30 min after dispatch) and send PURSUIT_DOSSIER.md (added 2026-08-02)
- [ ] **review_rename_plan** — Review preclean/rename_plan.bat with me BEFORE running it (added 2026-08-02)
- [ ] **label_night** — Run run_label_night.bat on the first free night (never run yet; feeds the label harvest that lifts the v4 HOLD) (added 2026-08-02)
- [ ] **brad_ri_package** — Send Brad the RI dossier v4.1 + honest-grade email (ready; your call) (added 2026-08-02)
- [ ] **docgraph_placement** — Confirm the DocGraph file's folder (preclean/docgraph/) and that the filename carries its year window (added 2026-08-02)
- [ ] **run_the_moves_in** — Run the MOVES in Fix_and_Fetch PDF Part 1: facility files out of hcpcs_minutes, POS 2026-Q1 subfolders to hold, corrected renames (added 2026-08-02)
- [ ] **fetch_the_4_re-downloads** — Fetch the 4 re-downloads in Fix_and_Fetch PDF Part 2: Market Saturation State-County, Order and Referring, OPAIS dated, NADAC 2023 (added 2026-08-02)

## After ANY download session (standing rule)

1. `python -m src.vintage_inspect --data-root <data>` — dates + right-file check
2. `python -m src.model_a.scheme_discovery --data-root <data>` — new-scheme scan
3. Send me VINTAGE_INSPECT.md + SCHEME_DISCOVERY.md + any header that failed.
