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
action, the session marks it done here and ships the updated file in the next
bundle, so the state ports forward.

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
    "cadence": [
        {"key": "leie", "what": "OIG LEIE exclusion list (THE label source — new bans are training labels)",
         "where": "preclean/caught.csv (keep the old file as caught_<yyyymm>.csv.bak)",
         "url": "https://oig.hhs.gov/exclusions/exclusions_list.asp",
         "every_months": 1, "last_done": None},
        {"key": "nppes_deactivation", "what": "CMS Deactivated NPI Report (invalid_identity scheme)",
         "where": "preclean/nppes_deactivation/ (keep as the .zip; loader unzips)",
         "url": "https://download.cms.gov/nppes/NPI_Files.html",
         "every_months": 1, "last_done": None},
        {"key": "sam_exclusions", "what": "SAM.gov exclusions extract (widens the PU label)",
         "where": "preclean/sam/",
         "url": "https://sam.gov/data-services (Exclusions public extract)",
         "every_months": 1, "last_done": None},
        {"key": "owner_snapshot", "what": "make owner-snapshot (no download — archives owner edges; ownership_turnover needs 2+ months)",
         "where": "run from repo: make owner-snapshot",
         "url": "", "every_months": 1, "last_done": None},
        {"key": "warn_notices", "what": "WARN layoff notices for the surge monitor (whistleblower timing)",
         "where": "preclean/warn/ (state DOL sites; see runbook)",
         "url": "", "every_months": 1, "last_done": None},
        {"key": "mue", "what": "CMS MUE quarterly files (practitioner + facility)",
         "where": "preclean/mue/",
         "url": "https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-medically-unlikely-edits",
         "every_months": 3, "last_done": "2026-08-02"},
        {"key": "pos", "what": "Provider of Services file (quarterly)",
         "where": "preclean/pos/ (current editions to pos_current_hold/ while frozen builds run)",
         "url": "https://data.cms.gov/provider-characteristics/hospitals-and-other-facilities/provider-of-services-file-hospital-non-hospital-facilities",
         "every_months": 3, "last_done": "2026-08-02"},
        {"key": "pbj", "what": "PBJ Daily Nurse Staffing (quarterly)",
         "where": "preclean/facility/ (current to facility_current_hold/)",
         "url": "https://data.cms.gov/quality-of-care/payroll-based-journal-daily-nurse-staffing",
         "every_months": 3, "last_done": "2026-08-02"},
        {"key": "care_compare", "what": "Care Compare provider-data archives (hospice measures, NH citations/ownership/penalties)",
         "where": "preclean/facility/ (current to facility_current_hold/)",
         "url": "https://data.cms.gov/provider-data/archived-data",
         "every_months": 3, "last_done": "2026-08-02"},
        {"key": "nadac", "what": "NADAC drug pricing (drug_spread_anomaly)",
         "where": "preclean/nadac/",
         "url": "https://data.medicaid.gov/dataset (NADAC)",
         "every_months": 3, "last_done": "2026-08-02"},
        {"key": "work_plan", "what": "OIG Work Plan curated table refresh (government_interest overlay — code table, I update it; just tell me it's due)",
         "where": "src/model_a/government_interest.py",
         "url": "https://oig.hhs.gov/reports-and-publications/workplan/",
         "every_months": 3, "last_done": "2026-08-02"},
        {"key": "hrsa_340b", "what": "340B OPAIS Covered Entity Daily Report (contract_pharmacy)",
         "where": "preclean/hrsa_340b/ (3-worksheet .xlsx)",
         "url": "https://340bopais.hrsa.gov/reports",
         "every_months": 3, "last_done": "2026-08-02"},
        {"key": "cms_pufs", "what": "Part B / Part D / DMEPOS / opioid annual PUFs (new year lands ~spring)",
         "where": "preclean/partb|partd|dmepos|opioid/ with the year in the filename",
         "url": "https://data.cms.gov/provider-summary-by-type-of-service",
         "every_months": 12, "last_done": "2026-08-02",
         "note": "2023 editions in hand for the freeze; annual pull matters for current-data scoring"},
        {"key": "open_payments", "what": "Open Payments annual file (kickback co-occurrence; new program year ~June)",
         "where": "preclean/open_payments/",
         "url": "https://openpaymentsdata.cms.gov/about/download",
         "every_months": 12, "last_done": "2026-08-02"},
        {"key": "hcris", "what": "HCRIS cost reports (cost_report_fraud)",
         "where": "preclean/hcris/ (flattened cost-report csvs)",
         "url": "https://www.cms.gov/data-research/statistics-trends-reports/cost-reports",
         "every_months": 12, "last_done": "2026-08-02"},
        {"key": "physician_time", "what": "PFS Final Rule Physician Work Time file (impossible-day minutes; new rule ~November)",
         "where": "preclean/hcpcs_time/physician_time_<year>.xlsx then run hcpcs_time_convert",
         "url": "https://www.cms.gov/medicare/payment/fee-schedules/physician (rule supporting files)",
         "every_months": 12, "last_done": "2026-08-02"},
        {"key": "docgraph", "what": "DocGraph/CareSet referral teaming file (referral rings)",
         "where": "preclean/docgraph/ with year window in the filename",
         "url": "https://careset.com/docgraph/",
         "every_months": 12, "last_done": "2026-08-02"},
        {"key": "nucc", "what": "NUCC taxonomy crosswalk (peer grouping; published Jan + Jul)",
         "where": "preclean/nucc/",
         "url": "https://www.nucc.org/index.php/code-sets-mainmenu-41/provider-taxonomy-mainmenu-40/csv-mainmenu-57",
         "every_months": 6, "last_done": "2026-08-02"},
    ],
    "tasks": [
        {"key": "send_vintage_report", "what": "Send VINTAGE_INSPECT.md (+ EDITIONS.csv if changed) after the inspector finishes", "added": "2026-08-02"},
        {"key": "send_scheme_discovery", "what": "Merge bundle 123, run scheme_discovery, send SCHEME_DISCOVERY.md", "added": "2026-08-02"},
        {"key": "dossier_poll", "what": "Run pursuit_dossier --poll (20-30 min after dispatch) and send PURSUIT_DOSSIER.md", "added": "2026-08-02"},
        {"key": "review_rename_plan", "what": "Review preclean/rename_plan.bat with me BEFORE running it", "added": "2026-08-02"},
        {"key": "label_night", "what": "Run run_label_night.bat on the first free night (never run yet; feeds the label harvest that lifts the v4 HOLD)", "added": "2026-08-02"},
        {"key": "brad_ri_package", "what": "Send Brad the RI dossier v4.1 + honest-grade email (ready; your call)", "added": "2026-08-02"},
        {"key": "docgraph_placement", "what": "Confirm the DocGraph file's folder (preclean/docgraph/) and that the filename carries its year window", "added": "2026-08-02"},
    ],
}


def load() -> dict:
    if not STATE.exists():
        STATE.write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    return json.loads(STATE.read_text(encoding="utf-8"))


def save(cfg: dict) -> None:
    STATE.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    VIEW.write_text(render_md(cfg, date.today()), encoding="utf-8")


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
        for it in due:
            L.append(f"- **{it['key']}** — {it['what']}")
            L.append(f"  - save to: {it['where']}"
                     + (f"  |  {it['url']}" if it.get("url") else ""))
            L.append(f"  - due: {it['next_due']}"
                     + (f"  ({it['note']})" if it.get("note") else ""))
    else:
        L.append("Nothing due. Good.")
    L += ["", "## COMING UP (30 days)", ""]
    L += [f"- {it['key']} — due {it['next_due']}: {it['what']}"
          for it in soon] or ["Nothing in the next 30 days."]
    L += ["", "## STANDING TASK QUEUE", ""]
    L += [f"- [ ] **{t['key']}** — {t['what']} (added {t['added']})"
          for t in cfg["tasks"]] or ["Queue empty."]
    L += ["", "## After ANY download session (standing rule)", "",
          "1. `python -m src.vintage_inspect --data-root <data>` — dates + "
          "right-file check", "2. `python -m src.model_a.scheme_discovery "
          "--data-root <data>` — new-scheme scan",
          "3. Send me VINTAGE_INSPECT.md + SCHEME_DISCOVERY.md + any header "
          "that failed.", ""]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--done", help="cadence key to mark done")
    ap.add_argument("--date", default=str(date.today()),
                    help="done-date override (YYYY-MM-DD)")
    ap.add_argument("--done-task", help="task key to complete/remove")
    ap.add_argument("--add-task", help="text of a new queued task")
    args = ap.parse_args()
    cfg = load()
    if args.done:
        hit = [i for i in cfg["cadence"] if i["key"] == args.done]
        if not hit:
            raise SystemExit(f"unknown cadence key: {args.done} "
                             f"(have: {', '.join(i['key'] for i in cfg['cadence'])})")
        hit[0]["last_done"] = args.date
        save(cfg)
        print(f"[reminders] {args.done} marked done {args.date}")
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
