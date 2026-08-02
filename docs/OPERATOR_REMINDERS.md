# OPERATOR REMINDERS (auto-generated — edit docs/reminders.json, not this file)

_As of 2026-08-02. Regenerate/update: `python -m src.reminders` (`--done <key>` after you fetch something)._

## DUE NOW

_Mark each fetch done with `python -m src.reminders --done <key>` — that AUTO-QUEUES the post-download checks (vintage inspect + scheme discovery + send reports) with exact commands, so nothing has to be remembered._

- **leie** — OIG LEIE exclusion list (THE label source — new bans are training labels)
  - save to: preclean/caught.csv (keep the old file as caught_<yyyymm>.csv.bak)  |  https://oig.hhs.gov/exclusions/exclusions_list.asp
  - due: freshness unknown
- **nppes_deactivation** — CMS Deactivated NPI Report (invalid_identity scheme)
  - save to: preclean/nppes_deactivation/ (keep as the .zip; loader unzips)  |  https://download.cms.gov/nppes/NPI_Files.html
  - due: freshness unknown
- **sam_exclusions** — SAM.gov exclusions extract (widens the PU label)
  - save to: preclean/sam/  |  https://sam.gov/data-services (Exclusions public extract)
  - due: freshness unknown
- **owner_snapshot** — make owner-snapshot (no download — archives owner edges; ownership_turnover needs 2+ months)
  - save to: run from repo: make owner-snapshot
  - due: freshness unknown
- **warn_notices** — WARN layoff notices for the surge monitor (whistleblower timing)
  - save to: preclean/warn/ (state DOL sites; see runbook)
  - due: freshness unknown

## COMING UP (30 days)

Nothing in the next 30 days.

## STANDING TASK QUEUE

- [ ] **send_vintage_report** — Send VINTAGE_INSPECT.md (+ EDITIONS.csv if changed) after the inspector finishes (added 2026-08-02)
- [ ] **send_scheme_discovery** — Merge bundle 123, run scheme_discovery, send SCHEME_DISCOVERY.md (added 2026-08-02)
- [ ] **dossier_poll** — Run pursuit_dossier --poll (20-30 min after dispatch) and send PURSUIT_DOSSIER.md (added 2026-08-02)
- [ ] **review_rename_plan** — Review preclean/rename_plan.bat with me BEFORE running it (added 2026-08-02)
- [ ] **label_night** — Run run_label_night.bat on the first free night (never run yet; feeds the label harvest that lifts the v4 HOLD) (added 2026-08-02)
- [ ] **brad_ri_package** — Send Brad the RI dossier v4.1 + honest-grade email (ready; your call) (added 2026-08-02)
- [ ] **docgraph_placement** — Confirm the DocGraph file's folder (preclean/docgraph/) and that the filename carries its year window (added 2026-08-02)

## After ANY download session (standing rule)

1. `python -m src.vintage_inspect --data-root <data>` — dates + right-file check
2. `python -m src.model_a.scheme_discovery --data-root <data>` — new-scheme scan
3. Send me VINTAGE_INSPECT.md + SCHEME_DISCOVERY.md + any header that failed.
