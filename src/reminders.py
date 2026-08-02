"""
reminders.py — the operator reminder engine (cross-session memory).

The operator asked for standing reminders that survive chat context windows:
which files to re-download monthly/quarterly/yearly, when to send fresh
reports, and which queued tasks are waiting. The memory lives in the repo —
``docs/reminders.json`` — so every future session (and every machine) reads
the same state; ``docs/OPERATOR_REMINDERS.md`` is its always-regenerated
human view.

Contract (also in CLAUDE.md): every chat session reads this early and weaves
DUE items into responses, even off-topic ones. When the operator confirms an
action ABSOLUTELY (never inferred from silence), the session marks it done here
and ships the updated file in the next bundle, so the state ports forward.
Open items are re-surfaced in EVERY response until explicitly closed.

  python -m src.reminders                 # what's due / coming up / queued
  python -m src.reminders --done leie     # mark a cadence item done today
  python -m src.reminders --done-task send_vintage_report
  python -m src.reminders --add-task "text of a new queued task"
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"
STATE = DOCS / "reminders.json"
VIEW = DOCS / "OPERATOR_REMINDERS.md"

# Seed state — written only if docs/reminders.json does not exist yet.
# last_done=None means freshness UNKNOWN: it shows as due until confirmed.
SEED = {
    "data_root": "C:/Users/treyr/OneDrive/Desktop/data",
    "cadence": [
        {"key": "leie", "what": "OIG LEIE exclusion list (THE label source — new bans are training labels)",
         "where": "preclean/caught.csv (keep the old file as caught_<yyyymm>.csv.bak)",
         "url": "https://oig.hhs.gov/exclusions/exclusions_list.asp",
         "every_months": 1, "last_done": None,
         "steps": [
             "Open https://oig.hhs.gov/exclusions/exclusions_list.asp",
             "Click the 'UPDATED LEIE Database' CSV link (the full database, not the monthly supplement)",
             "Then paste the commands below (they back up the old file, then swap in the new one)"],
         "commands": [
             'copy "C:\\Users\\treyr\\OneDrive\\Desktop\\data\\preclean\\caught.csv" "C:\\Users\\treyr\\OneDrive\\Desktop\\data\\preclean\\caught_%date:~10,4%%date:~4,2%.csv.bak"',
             'move "%USERPROFILE%\\Downloads\\UPDATED.csv" "C:\\Users\\treyr\\OneDrive\\Desktop\\data\\preclean\\caught.csv"',
             'python -m src.reminders --done leie']},
        {"key": "nppes_deactivation", "what": "CMS Deactivated NPI Report (invalid_identity scheme)",
         "where": "preclean/nppes_deactivation/ (keep as the .zip; loader unzips)",
         "url": "https://download.cms.gov/nppes/NPI_Files.html",
         "every_months": 1, "last_done": None,
         "steps": [
             "Open https://download.cms.gov/nppes/NPI_Files.html",
             "Under 'Weekly/Monthly Deactivation', download the 'NPPES Deactivated NPI Report' .zip (do NOT unzip)"],
         "commands": [
             'move "%USERPROFILE%\\Downloads\\NPPES_Deactivated_*.zip" "C:\\Users\\treyr\\OneDrive\\Desktop\\data\\preclean\\nppes_deactivation\\"',
             'python -m src.reminders --done nppes_deactivation']},
        {"key": "sam_exclusions", "what": "SAM.gov exclusions extract (widens the PU label)",
         "where": "preclean/sam/",
         "url": "https://sam.gov/data-services",
         "every_months": 1, "last_done": None,
         "steps": [
             "Open https://sam.gov/data-services and pick 'Exclusions' → 'Public V2' current extract (CSV)",
             "No login needed for the public extract"],
         "commands": [
             'move "%USERPROFILE%\\Downloads\\SAM_Exclusions*.CSV" "C:\\Users\\treyr\\OneDrive\\Desktop\\data\\preclean\\sam\\sam_exclusions_%date:~10,4%%date:~4,2%.csv"',
             'python -m src.reminders --done sam_exclusions']},
        {"key": "owner_snapshot", "what": "make owner-snapshot (no download — archives owner edges; ownership_turnover needs 2+ months)",
         "where": "run from repo: make owner-snapshot",
         "url": "", "every_months": 1, "last_done": None,
         "steps": ["Nothing to download — paste the commands below"],
         "commands": [
             'cd /d C:\\Users\\treyr\\medicaid-fraud-detection',
             'python -m src.entity_graph.ownership_snapshot --owned-by "C:\\Users\\treyr\\OneDrive\\Desktop\\data\\graph\\edges\\owned_by_edges.parquet" --snapshots-dir "C:\\Users\\treyr\\OneDrive\\Desktop\\data\\owner_snapshots"',
             'python -m src.reminders --done owner_snapshot']},
        {"key": "warn_notices", "what": "WARN layoff notices for the surge monitor (whistleblower timing)",
         "where": "preclean/warn/",
         "url": "", "every_months": 1, "last_done": None,
         "steps": [
             "Google '<state> WARN notices' for target states (start: RI, NC, FL, TX, CA) — each state DOL posts a table",
             "Download/export the current-year table as CSV or XLSX per state",
             'Save as "C:\\Users\\treyr\\OneDrive\\Desktop\\data\\preclean\\warn\\warn_<state>_<yyyymm>.csv"']},
        {"key": "mue", "what": "CMS MUE quarterly files (practitioner + facility)",
         "where": "preclean/mue/",
         "url": "https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-medically-unlikely-edits",
         "every_months": 3, "last_done": "2026-08-02",
         "steps": [
             "Open the MUE page (url above); download BOTH quarterly zips: 'Practitioner Services MUE Table' + 'Facility Outpatient Hospital Services MUE Table'",
             "Extract each zip; take the DATA sheet (the AMA-copyright cover sheet is the known trap)",
             'Save as "...preclean\\mue\\mue_practitioner_<yyyyQq>.csv" and "...preclean\\mue\\mue_facility_<yyyyQq>.csv"']},
        {"key": "pos", "what": "Provider of Services file (quarterly)",
         "where": "preclean/pos/ (current editions to pos_current_hold/ while frozen builds run)",
         "url": "https://data.cms.gov/provider-characteristics/hospitals-and-other-facilities/provider-of-services-file-hospital-non-hospital-facilities",
         "every_months": 3, "last_done": "2026-08-02",
         "steps": [
             "Open the POS page (url above) → newest quarter → download the CSV set",
             "Keep the quarter in every filename (e.g. pos_other_<yyyyQq>.csv)",
             "IMPORTANT: while the 2023-12 frozen builds are the working runs, save CURRENT editions to preclean\\pos_current_hold\\ so they never leak into a frozen build"]},
        {"key": "pbj", "what": "PBJ Daily Nurse Staffing (quarterly)",
         "where": "preclean/facility/ (current to facility_current_hold/)",
         "url": "https://data.cms.gov/quality-of-care/payroll-based-journal-daily-nurse-staffing",
         "every_months": 3, "last_done": "2026-08-02",
         "steps": [
             "Open the PBJ page (url above) → newest quarter → download the Nurse Staffing CSV (the one WITH Hrs_RN columns — the 'non-nurse' file is the known trap)",
             'Save as "...preclean\\facility_current_hold\\pbj_daily_staffing_<yyyyQq>.csv" while frozen builds are the working runs (the frozen 2023Q4 edition stays in facility\\)']},
        {"key": "care_compare", "what": "Care Compare provider-data archives (hospice measures, NH citations/ownership/penalties)",
         "where": "preclean/facility/ (current to facility_current_hold/)",
         "url": "https://data.cms.gov/provider-data/archived-data",
         "every_months": 3, "last_done": "2026-08-02",
         "steps": [
             "Open https://data.cms.gov/provider-data/archived-data → 'Hospice care' + 'Nursing homes' → newest month's zip",
             "From the hospice zip keep Hospice_Provider; from the NH zip keep HealthCitations, Ownership, Penalties, SurveyDates",
             "Rename each with the month (e.g. Hospice_Provider_<Mon><yyyy>.csv) and save to preclean\\facility_current_hold\\ (frozen Nov2023 editions stay in facility\\)"]},
        {"key": "nadac", "what": "NADAC drug pricing (drug_spread_anomaly)",
         "where": "preclean/nadac/",
         "url": "https://data.medicaid.gov/datasets",
         "every_months": 3, "last_done": "2026-08-02",
         "steps": [
             "Open https://data.medicaid.gov/datasets and search 'NADAC (National Average Drug Acquisition Cost)'",
             "Download the current full CSV",
             'Save as "...preclean\\nadac\\nadac_<yyyymm>.csv"']},
        {"key": "work_plan", "what": "OIG Work Plan curated table refresh (government_interest overlay)",
         "where": "src/model_a/government_interest.py (Claude updates the code table)",
         "url": "https://oig.hhs.gov/reports-and-publications/workplan/",
         "every_months": 3, "last_done": "2026-08-02",
         "steps": [
             "Nothing to download — just tell Claude: 'work plan refresh is due'",
             "Claude re-reads the OIG Work Plan active items and updates the curated table in code"]},
        {"key": "hrsa_340b", "what": "340B OPAIS Covered Entity Daily Report (contract_pharmacy)",
         "where": "preclean/hrsa_340b/ (3-worksheet .xlsx)",
         "url": "https://340bopais.hrsa.gov/reports",
         "every_months": 3, "last_done": "2026-08-02",
         "steps": [
             "Open https://340bopais.hrsa.gov/reports → 'Covered Entity Daily Report' → Excel export (must be the 3-worksheet .xlsx with Contract Pharmacies)",
             'Save as "...preclean\\hrsa_340b\\opais_covered_entity_<yyyymmdd>.xlsx"']},
        {"key": "cms_pufs", "what": "Part B / Part D / DMEPOS / opioid annual PUFs (new year lands ~spring)",
         "where": "preclean/partb|partd|dmepos|opioid/ with the year in the filename",
         "url": "https://data.cms.gov/provider-summary-by-type-of-service",
         "every_months": 12, "last_done": "2026-08-02",
         "note": "2023 editions in hand for the freeze; annual pull matters for current-data scoring",
         "steps": [
             "Open the url; download FOUR datasets, always the 'by Provider and Service/Drug' detail versions (the 'by Provider' summaries are the known trap):",
             "1) Medicare Physician & Other Practitioners BY PROVIDER AND SERVICE → preclean\\partb\\partb_<year>.csv",
             "2) Medicare Part D Prescribers BY PROVIDER AND DRUG → preclean\\partd\\partd_<year>.csv",
             "3) Medicare DMEPOS BY REFERRING PROVIDER AND SERVICE → preclean\\dmepos\\dmepos_<year>.csv",
             "4) Medicare Part D Opioid Prescribing Rates by prescriber → preclean\\opioid\\opioid_<year>.csv"]},
        {"key": "open_payments", "what": "Open Payments annual file (kickback co-occurrence; new program year ~June)",
         "where": "preclean/open_payments/",
         "url": "https://openpaymentsdata.cms.gov/about/download",
         "every_months": 12, "last_done": "2026-08-02",
         "steps": [
             "Open https://openpaymentsdata.cms.gov/about/download → newest Program Year → General Payments detail file (it's large; the zip is fine to store)",
             'Save to "...preclean\\open_payments\\" with the program year in the name',
             "If the NPI column name differs from Covered_Recipient_NPI (older layouts), send Claude the header row"]},
        {"key": "hcris", "what": "HCRIS cost reports (cost_report_fraud)",
         "where": "preclean/hcris/ (flattened cost-report csvs)",
         "url": "https://www.cms.gov/data-research/statistics-trends-reports/cost-reports",
         "every_months": 12, "last_done": "2026-08-02",
         "steps": [
             "Open the Cost Reports page (url above) → 'Hospital 2010 form' (HOSPITAL10) newest fiscal year",
             "Use the FLATTENED csv version, not the raw worksheet format (known trap)",
             'Save to "...preclean\\hcris\\" with the fiscal year in the name']},
        {"key": "physician_time", "what": "PFS Final Rule Physician Work Time file (impossible-day minutes; new rule ~November)",
         "where": "preclean/hcpcs_time/physician_time_<year>.xlsx then run hcpcs_time_convert",
         "url": "https://www.cms.gov/medicare/payment/fee-schedules/physician",
         "every_months": 12, "last_done": "2026-08-02",
         "steps": [
             "Google 'CY <year> PFS Final Rule Physician Work Time' — the file lives in the rule's downloads (a zip with a Physician Time .xlsx)",
             "Header must start with cpt_code and contain Median_Intra_Service_Time (an RVU file like PPRRVU is the known wrong file)",
             'Save as "...preclean\\hcpcs_time\\physician_time_<year>.xlsx", then paste the commands below with <year> filled in'],
         "commands": [
             'cd /d C:\\Users\\treyr\\medicaid-fraud-detection',
             'python -m src.ingest_cms.hcpcs_time_convert --in "C:\\Users\\treyr\\OneDrive\\Desktop\\data\\preclean\\hcpcs_time\\physician_time_<year>.xlsx" --out "C:\\Users\\treyr\\OneDrive\\Desktop\\data\\preclean\\hcpcs_time\\hcpcs_minutes_<year>.csv"',
             'python -m src.reminders --done physician_time']},
        {"key": "docgraph", "what": "DocGraph/CareSet referral teaming file (referral rings)",
         "where": "preclean/docgraph/ with year window in the filename",
         "url": "https://careset.com/docgraph/",
         "every_months": 12, "last_done": "2026-08-02",
         "steps": [
             "Open https://careset.com/docgraph/ → DocGraph Hop Teaming, newest year window",
             "Required columns: from_npi, to_npi, patient_count",
             'Save to "...preclean\\docgraph\\" keeping the year window in the filename']},
        {"key": "nucc", "what": "NUCC taxonomy crosswalk (peer grouping; published Jan + Jul)",
         "where": "preclean/nucc/",
         "url": "https://www.nucc.org/index.php/code-sets-mainmenu-41/provider-taxonomy-mainmenu-40/csv-mainmenu-57",
         "every_months": 6, "last_done": "2026-08-02",
         "steps": [
             "Open the NUCC csv page (url above) → newest 'Health Care Provider Taxonomy Code Set' CSV",
             'Save as "...preclean\\nucc\\nucc_taxonomy_<yyyymm>.csv"']},
    ],
    "tasks": [
        {"key": "merge_bundle131", "what": "Merge bundle 131 (supersedes 123-130)", "added": "2026-08-02",
         "commands": [
             'cd /d C:\\Users\\treyr\\medicaid-fraud-detection',
             'git fetch "%USERPROFILE%\\Downloads\\bundle131nag.bundle" claude/happy-mccarthy-3skrn3',
             'git merge FETCH_HEAD',
             'python -m src.reminders --done-task merge_bundle131']},
        {"key": "pdf_part1_moves", "what": "Run the Fix_and_Fetch PDF Part 1 moves — the CRITICAL one (facility files out of hcpcs_minutes) is below; the rest (POS holds, renames, OPAIS park) are in the PDF", "added": "2026-08-02",
         "commands": [
             'set PRE=C:\\Users\\treyr\\OneDrive\\Desktop\\data\\preclean',
             'move "%PRE%\\hcpcs_minutes\\Hospice_Provider_Nov2023.csv" "%PRE%\\facility\\"',
             'move "%PRE%\\hcpcs_minutes\\NH_HealthCitations_Nov2023.csv" "%PRE%\\facility\\"',
             'move "%PRE%\\hcpcs_minutes\\NH_Ownership_Nov2023.csv" "%PRE%\\facility\\"',
             'move "%PRE%\\hcpcs_minutes\\NH_Penalties_Nov2023.csv" "%PRE%\\facility\\"',
             'move "%PRE%\\hcpcs_minutes\\NH_SurveyDates_Nov2023.csv" "%PRE%\\facility\\"',
             'move "%PRE%\\hcpcs_minutes\\pbj_daily_staffing_2023Q4.csv" "%PRE%\\facility\\"',
             'rmdir "%PRE%\\hcpcs_minutes"']},
        {"key": "pdf_part2_redownloads", "what": "Fetch the 4 re-downloads in Fix_and_Fetch PDF Part 2: Market Saturation State-County, Order and Referring, OPAIS dated, NADAC 2023 (URLs + save paths in the PDF)", "added": "2026-08-02"},
        {"key": "post_download_checks", "what": "After the moves + re-downloads: run the checks below, then send VINTAGE_INSPECT.md + SCHEME_DISCOVERY.md to Claude", "added": "2026-08-02",
         "commands": [
             'cd /d C:\\Users\\treyr\\medicaid-fraud-detection',
             'python -m src.vintage_inspect --data-root "C:\\Users\\treyr\\OneDrive\\Desktop\\data"',
             'python -m src.model_a.scheme_discovery --data-root "C:\\Users\\treyr\\OneDrive\\Desktop\\data"',
             'python -m src.reminders --done-task post_download_checks']},
        {"key": "dossier_poll", "what": "Collect the Manus research and rebuild the pursuit dossier, then send PURSUIT_DOSSIER.md", "added": "2026-08-02",
         "commands": [
             'cd /d C:\\Users\\treyr\\medicaid-fraud-detection',
             'python -m src.model_a.pursuit_dossier --run-dir "C:\\Users\\treyr\\OneDrive\\Desktop\\data\\model_a\\frozen_2023-12_v3" --poll',
             'python -m src.reminders --done-task dossier_poll']},
        {"key": "owners_2022_editions", "what": "While on data.cms.gov: grab a 2022-dated edition of each All-Owners dataset (version history on each dataset page) into preclean/owners_prior/ — unlocks leakage-correct FROZEN ownership churn (needs 2 pre-cutoff editions; you have one)", "added": "2026-08-02"},
        {"key": "label_night", "what": "Run run_label_night.bat on the first free night (never run yet; feeds the label harvest that lifts the v4 HOLD)", "added": "2026-08-02",
         "commands": ['"%USERPROFILE%\\Downloads\\run_label_night.bat"']},
        {"key": "brad_ri_package", "what": "Send Brad the RI dossier v4.1 + honest-grade email (ready; your call)", "added": "2026-08-02"},
        {"key": "docgraph_placement", "what": "Confirm the DocGraph file sits in preclean/docgraph/ with its year window in the filename (inspection saw DocGraph_Hop_Teaming_2022.csv — looks right; confirm and close this)", "added": "2026-08-02"},
    ],
}


def load() -> dict:
    if not STATE.exists():
        STATE.write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    return json.loads(STATE.read_text(encoding="utf-8"))


def save(cfg: dict) -> None:
    STATE.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    VIEW.write_text(render_md(cfg, date.today()), encoding="utf-8")


def mark_done(cfg: dict, key: str, when: str) -> str:
    """Mark a cadence item done. Downloads AUTO-QUEUE the post-download
    checks with exact commands — the operator never has to remember the
    standing rule; the queue carries it."""
    hit = [i for i in cfg["cadence"] if i["key"] == key]
    if not hit:
        raise SystemExit(f"unknown cadence key: {key} "
                         f"(have: {', '.join(i['key'] for i in cfg['cadence'])})")
    hit[0]["last_done"] = when
    msg = f"[reminders] {key} marked done {when}"
    if hit[0].get("url"):                      # it was a download
        root = cfg.get("data_root", "<data-root>")
        if not any(t["key"] == "post_download_checks" for t in cfg["tasks"]):
            cfg["tasks"].append({
                "key": "post_download_checks",
                "what": ("New files landed — run the checks below, then send "
                         "VINTAGE_INSPECT.md + SCHEME_DISCOVERY.md to Claude"),
                "commands": [
                    'cd /d C:\\Users\\treyr\\medicaid-fraud-detection',
                    f'python -m src.vintage_inspect --data-root "{root}"',
                    f'python -m src.model_a.scheme_discovery --data-root "{root}"',
                    'python -m src.reminders --done-task post_download_checks'],
                "added": when})
        msg += ("\n[reminders] post-download checks queued — run them after "
                "this download session ends (exact commands are in the queue)")
    return msg


def mark_run_complete(cfg: dict, run_name: str, when: str) -> str:
    """The operator says a model run finished. Queue the FULL post-run
    checklist as tracked tasks (each stays on the board, re-surfaced in
    every chat response, until explicitly closed). Idempotent per run."""
    root = cfg.get("data_root", "<data-root>").replace("/", "\\")
    run_dir = f"{root}\\model_a\\{run_name}"
    repo = "C:\\Users\\treyr\\medicaid-fraud-detection"
    checklist = [
        (f"run_packet__{run_name}",
         f"Send the run packet for {run_name} (every report + audit file, "
         "zero raw data) — Claude cannot see the run finished, so this zip "
         "IS the run's arrival",
         [f'set RUN_DIR={run_dir}',
          'powershell -Command "Compress-Archive -Force -Path '
          "'%RUN_DIR%\\*.md','%RUN_DIR%\\*.json'"
          ' -DestinationPath \'%USERPROFILE%\\Desktop\\run_packet.zip\'"',
          "REM then upload run_packet.zip to the chat",
          f'python -m src.reminders --done-task run_packet__{run_name}']),
        (f"pursuit_dossier__{run_name}",
         f"Build + dispatch the pursuit dossier for {run_name} (skip if the "
         "run script already did — check for PURSUIT_DOSSIER.md in the run "
         "folder)",
         [f'cd /d {repo}',
          f'python -m src.model_a.pursuit_dossier --run-dir "{run_dir}" --dispatch',
          f'python -m src.reminders --done-task pursuit_dossier__{run_name}']),
        (f"dossier_poll__{run_name}",
         f"~30 min after dispatch: collect Manus research into the {run_name} "
         "dossier and send PURSUIT_DOSSIER.md",
         [f'cd /d {repo}',
          f'python -m src.model_a.pursuit_dossier --run-dir "{run_dir}" --poll',
          f'python -m src.reminders --done-task dossier_poll__{run_name}']),
        (f"registry_verdict__{run_name}",
         f"Open until Claude reads the packet and returns the release-registry "
         f"verdict (PROMOTE/HOLD) + scheme-triage follow-ups for {run_name} — "
         "close only when you have them",
         None),
    ]
    added = []
    for key, what, cmds in checklist:
        if any(t["key"] == key for t in cfg["tasks"]):
            continue
        t = {"key": key, "what": what, "added": when}
        if cmds:
            t["commands"] = cmds
        cfg["tasks"].append(t)
        added.append(key)
    return (f"[reminders] run {run_name} marked complete — "
            f"{len(added)} post-run task(s) queued: {', '.join(added) or 'none new'}")


def _next_due(item: dict) -> date | None:
    """None means freshness unknown → treated as due."""
    if not item.get("last_done"):
        return None
    last = date.fromisoformat(item["last_done"])
    months = item["every_months"]
    y, m = divmod(last.month - 1 + months, 12)
    return last.replace(year=last.year + y, month=m + 1,
                        day=min(last.day, 28))


def split(cfg: dict, today: date) -> tuple[list, list]:
    """(due_now, upcoming_within_30d); unknown-freshness items are due."""
    due, soon = [], []
    for it in cfg["cadence"]:
        nd = _next_due(it)
        if nd is None or nd <= today:
            due.append({**it, "next_due": str(nd) if nd else "freshness unknown"})
        elif nd <= today + timedelta(days=30):
            soon.append({**it, "next_due": str(nd)})
    return due, sorted(soon, key=lambda r: r["next_due"])


def render_md(cfg: dict, today: date) -> str:
    due, soon = split(cfg, today)
    L = ["# OPERATOR REMINDERS (auto-generated — edit docs/reminders.json, "
         "not this file)", "",
         f"_As of {today}. Regenerate/update: `python -m src.reminders` "
         f"(`--done <key>` after you fetch something)._", "",
         "## DUE NOW", ""]
    if due:
        L.append("_Mark each fetch done with `python -m src.reminders "
                 "--done <key>` — that AUTO-QUEUES the post-download checks "
                 "(vintage inspect + scheme discovery + send reports) with "
                 "exact commands, so nothing has to be remembered._")
        L.append("")
        for it in due:
            L.append(f"- **{it['key']}** — {it['what']}")
            L.append(f"  - save to: {it['where']}"
                     + (f"  |  {it['url']}" if it.get("url") else ""))
            L.append(f"  - due: {it['next_due']}"
                     + (f"  ({it['note']})" if it.get("note") else ""))
            for i, s in enumerate(it.get("steps", []), 1):
                L.append(f"  - step {i}: {s}")
            if it.get("commands"):
                L += ["", "```bat"] + it["commands"] + ["```", ""]
    else:
        L.append("Nothing due. Good.")
    L += ["", "## COMING UP (30 days)", ""]
    L += [f"- {it['key']} — due {it['next_due']}: {it['what']}"
          for it in soon] or ["Nothing in the next 30 days."]
    L += ["", "## STANDING TASK QUEUE", ""]
    if cfg["tasks"]:
        for t in cfg["tasks"]:
            L.append(f"- [ ] **{t['key']}** — {t['what']} (added {t['added']})")
            if t.get("commands"):
                L += ["", "```bat"] + t["commands"] + ["```", ""]
    else:
        L.append("Queue empty.")
    L += ["", "## After ANY download session (standing rule)", "",
          "1. `python -m src.vintage_inspect --data-root <data>` — dates + "
          "right-file check", "2. `python -m src.model_a.scheme_discovery "
          "--data-root <data>` — new-scheme scan",
          "3. Send me VINTAGE_INSPECT.md + SCHEME_DISCOVERY.md + any header "
          "that failed.", ""]
    L += ["## After ANY model run (standing rule): send the run packet", "",
          "The results are half the point; the feedback loops (scheme triage, "
          "release gating, expectations, source audit) need the run's OWN "
          "files. One command collects every report into a zip — replace "
          "RUN_DIR with the run folder (e.g. frozen_2023-12_v4), then upload "
          "run_packet.zip to the chat:", "",
          "```bat",
          'set RUN_DIR=C:\\Users\\treyr\\OneDrive\\Desktop\\data\\model_a\\frozen_2023-12_v4',
          'powershell -Command "Compress-Archive -Force -Path '
          "'%RUN_DIR%\\*.md','%RUN_DIR%\\*.json'"
          ' -DestinationPath \'%USERPROFILE%\\Desktop\\run_packet.zip\'"',
          "```", "",
          "That captures: PROVIDER_FEATURES_EXPORT_REPORT, SOURCES_REPORT, "
          "EXPECTATIONS, SCHEME_HEALTH + SCHEME_TRIAGE, CASE_VALIDATION, "
          "PURSUIT_DOSSIER, RESULTS_DIGEST, OPERATING_POINTS, the feature "
          "manifest, and the run-registry row. No parquet/csv goes in the "
          "zip (reports only). A run is not closed until the packet is "
          "sent and Claude has updated the release registry + triage "
          "follow-ups from it.", ""]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--done", help="cadence key to mark done")
    ap.add_argument("--date", default=str(date.today()),
                    help="done-date override (YYYY-MM-DD)")
    ap.add_argument("--done-task", help="task key to complete/remove")
    ap.add_argument("--add-task", help="text of a new queued task")
    ap.add_argument("--run-complete", metavar="RUN_FOLDER",
                    help="a model run finished (e.g. frozen_2023-12_v4): "
                         "queue the full post-run checklist")
    args = ap.parse_args()
    cfg = load()
    if args.done:
        print(mark_done(cfg, args.done, args.date))
        save(cfg)
    if args.run_complete:
        print(mark_run_complete(cfg, args.run_complete, args.date))
        save(cfg)
    if args.done_task:
        n = len(cfg["tasks"])
        cfg["tasks"] = [t for t in cfg["tasks"] if t["key"] != args.done_task]
        save(cfg)
        print(f"[reminders] task {args.done_task} "
              f"{'closed' if len(cfg['tasks']) < n else 'NOT FOUND'}")
    if args.add_task:
        key = "_".join(args.add_task.lower().split()[:4])
        cfg["tasks"].append({"key": key, "what": args.add_task,
                             "added": args.date})
        save(cfg)
        print(f"[reminders] task added: {key}")
    save(cfg)
    print(render_md(cfg, date.today()))


if __name__ == "__main__":
    main()
