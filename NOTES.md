# Analyst notes (personal working notes for the analyst's Claude sessions)

This file lives ALONE on branch `daschel/notes` (no project code; never merged, never a pull
request). It is NOT private: anyone with access to the repository can read it. Work notes only:
no passwords, keys or tokens, no data, no lead lists or case work product, and people by ROLE,
never by name (the team rule).

## For Claude: how to use this file

1. At the start of a session, read it with
   `git fetch origin daschel/notes && git show origin/daschel/notes:NOTES.md`.
2. Follow "How to work with me" below for the whole session.
3. Keep it current WITHOUT being asked at natural milestones (a pull request opened, merged or
   reviewed; a model run started, finished or read; a decision made; a new open question; an
   item closed), and whenever the analyst says "update my notes". Edit only this file, on this
   branch, from a separate worktree so the working branch is untouched:
   ```bash
   git fetch origin daschel/notes
   git worktree add /tmp/notes origin/daschel/notes --detach
   # edit /tmp/notes/NOTES.md, then:
   git -C /tmp/notes commit -am "notes: <what changed> [skip ci]" && git -C /tmp/notes push origin HEAD:daschel/notes
   git worktree remove /tmp/notes
   ```
   Every notes commit message ends with `[skip ci]`: the branch has no code, so the project's
   test workflow (which runs on pushes to every branch) would otherwise fail on it.
   Commit author `Claude <noreply@anthropic.com>`. Keep the file short: replace stale lines,
   move finished items to "Done (recent)", and drop old done items after a couple of weeks.
4. Say in one line when the notes were updated. Never put anything here the rules above exclude.

## How to work with me

- **EMAIL RULE: before any run or server work the current operator must not disturb**, an email
  to him saying the server is in use, what is running, and what not to do (no git pull, no
  branch switch, no edits in ~/medicaid-fraud-detection, no other run or lanes cycle) until I
  confirm it is done; and a second email when it finishes. Claude drafts it; it is sent on my
  OK (unless I change this to "send automatically").
- **Writing into `/data/medicaid/processed` is allowed** (operator role); for a replacement, write
  to a new name first, keep a backup of the old file, then swap.
- **HARD RULE: KEYS STAY PRIVATE.** Every time a command would display `~/.medicaid_env` or any
  other file holding passwords, tokens or keys (`cat`, `less`, `head`, `grep`, an editor...), put
  this warning right next to the command, every time, no exceptions:
  "⚠️ Do not paste what this shows into any chat, email, document or file. Keys stay private."
  Never ask me for a key's value, never print one, never write one into any file or commit.
- I'm new to the codebase, the model and the command line. Use plain language, explain every
  code name or piece of jargon the first time it comes up, and go one step at a time.
- Give commands in their own code blocks, say whether each one only reads or changes something,
  and say what output to expect and how to read it.
- NEVER post, reply or comment on GitHub (or send email) on my behalf. Draft the text, show me,
  and I decide. Ask before any action others can see.
- Before acting on something I paste (an email, a comment), say what it is and what it asks.
- Don't make me memorise names: when a code name matters, give its plain meaning.

## The operator's rules (from his emails; check every plan against them)

Code: (1) branches `daschel/<topic>` from `claude/happy-mccarthy-3skrn3`; (2) never `main`, never
push to his branch; (3) every change is a PR into his branch, he merges; (4) no merge without a
test that plants a known case in made-up data; (5) targeted test, then the full suite, with
`OMP_NUM_THREADS=1`; (6) touching the export, graph or scorer: also `make ci-local`; (7) an
unrelated failing test: tell him, never edit it; (8) the PR says what it does, what the test
plants, what I'm unsure about.
Sessions: (9) never paste passwords / tokens / keys, never save them in files; if Claude asks
for one, stop and ask him; (10) no real data files in the repo; (11) no people's names in
committed files; (12) IDs stay text; every join checks row and dollar counts.
VM: (13) `tmux ls` first; a run (v29, v30, cd2, lanes) means wait; (14) never work in the main
checkout `~/medicaid-fraud-detection`: use my worktree `/tmp/mfd-daschel`; (15) never `git pull`
on the server without asking him, never during a run; (16) small probes only, no full export
(a registered run's job); (17) anything over a minute in `tmux`; (18) never write into
`/data/medicaid/processed` or `/data/medicaid/model_a`; my outputs go to `/tmp/mfd-daschel/out`;
(19) never open, copy or edit `~/.medicaid_env`; (20) no data leaves the server (reports and
packets may; data files and rows naming people may not: paste counts, not rows); (21) nothing
with "dispatch" without his go-ahead; (22) starting / stopping the server is his job; (23) a
probe's findings go in the PR as numbers. Environment: (24) heads-up before editing the shared
setup script. Behind all of it: (25) a new feature changes nothing now; it goes into the next
registered run and is graded there.
Target: FCA-applicable cases only (government money: Medicaid / Medicare); private-insurance
cases are out of scope except as lessons about the model's reach.
SUPERSEDED BY THE HANDOVER (2026-11-07, my decision as incoming operator): rule 14 is DROPPED
(the main checkout `~/medicaid-fraud-detection` is my working copy); rule 19 is replaced by the
HARD RULE at the top (I may view `~/.medicaid_env`; the keys-stay-private warning goes beside every
such command; sourcing it is fine); rules 15, 16, 18, 21, 22 bind the operator role no longer
(they limited a teammate). PRACTICAL CONSTRAINT THAT STAYS: the runner runs from the main
checkout, so while a run is going never switch branches, `git pull` or edit files there.
Still binding: 13 (check `tmux ls`; one run at a time), 17 (long jobs in tmux), 20 (no data
leaves the server; counts not rows naming people), 23, 24, 25, and the code rules 1-12.
Lessons from 2026-11-06 (still worth keeping): `make ci-local` for export / graph / scorer
changes; never paste rows naming individuals into chat; a CLIA lookup rebuild writes to scratch
names first, then a backup, then the swap (never overwrite `ccn_to_npi.parquet`).

## My role and permissions

- Technical analyst on the Medicaid fraud-detection platform. My work goes on `daschel/<topic>`
  branches as pull requests into the integration branch `claude/happy-mccarthy-3skrn3`
  (never into `main`, never pushed to directly). The operator reviews and merges.
- Management has authorised me to use the VM and run model runs. Habits kept anyway: a one-line
  heads-up to the operator before starting a run (one big run at a time on the VM), and a
  heads-up before editing the shared environment's setup script.
- Shared files I don't edit directly: `CLAUDE.md`, `docs/reminders.json` (the operator's).
  The operator's reminder list is not mine to work through in my sessions.
- **ROLE CHANGE IN PROGRESS (2026-11-06):** I'm taking over the operator role from the current
  operator; the CEO wants it and wants the stage-agent plan (`AGENT_PLAN.md`). The notes
  branch stays (the CEO knows). Once the handover is confirmed: the VM rules 13-23 that limited
  a teammate no longer bind me (pulling, runs, pre-registration, rebuilding data files are my
  job; sourcing `~/.medicaid_env` is fine); still binding: one run at a time, nothing during a
  run, pre-registration before a graded run, no secrets / data / names in git,
  `OMP_NUM_THREADS=1`, no research dispatch without a deliberate credit decision; the
  reminder list (`docs/reminders.json`) and CLAUDE.md upkeep become mine.
  FIRST: confirm the handover explicitly with the current operator (from when the VM is mine;
  does he have anything running or queued, e.g. cd2 or a lanes cycle) so two operators never
  act on the one-user VM at once. During the handover, have him or the CEO review my PRs.

## My objectives (set 2026-11-06; remind me of these and tick them off here)

- [ ] **Reduce bloat** (scope to agree: the codebase, the docs, or the run folders?).
- [ ] **Run the model myself**: v30 is the first one (plan below).
- [ ] **Add the Tier A check** (rendering NPI deactivated at service time): my next PR.
- [ ] **Fix why v29 failed**: HOLD on top-50 stability 0.599 vs 0.6. CAUSE FOUND 2026-11-06:
      in `accuracy_diagnostics.stability()` the rank array was named `order`, overwriting
      the book's sort key, so the pursuit-list (book) Jaccard errored on every repeat
      (v29 `night_run.log`: "book top-50 for repeat N not built ('numpy.ndarray' object has
      no attribute 'startswith')") and the check fell back to the national measure.
      FIX: branch `daschel/fix-book-jaccard` (commit 6d28546, pushed; suite 1,459 passed);
      PR NOT opened yet (my go-ahead). Must be merged before v30.
      DECIDED: v30 grades stability on the BOOK measure, bar 0.6, top 50; the pre-registration
      carries `"top50_jaccard_book >= 0.6" "jaccard_source = book"` (the second fails the run
      if the book measure silently falls back again); the national number is info only.
      Heads-up to the operator with the PR link.
- [ ] **Fix the leakage we found**: the export reads today's NPPES on frozen runs; build an
      as-of `provider_dim` (on the operator's backlog; agree it with the operator first).
      Steps: (1) on frozen runs build `processed/provider_dim_asof_<cut>.parquet` from the as-of
      NPPES (reuse the graph's `--asof-nppes` / `apply_asof_addresses` method); (2) point every
      export read of `provider_dim` at it under a freeze (~30 reads; PR #2's table lists the 11
      blocks); (3) a test that a frozen run sees only the old registry. **THEN, in the same PR,
      the one-line label switch** in `provider_features_export.py`'s `_SRC_VINTAGE`:
      `"servicing_mix": "current_state"` -> `"point_in_time"`, and re-check the vintage tag of
      every other source that reads provider_dim. Never flip a tag before the fix exists.
- [ ] **Finish the state capacity rosters**: NH, NY, TEN (Tennessee?) (licensed-facility
      rosters that give each facility's licensed capacity; NJ and NY were the first loaded).
- [ ] **Send the operator a master list** of everything I've done and where each piece is saved.
- [ ] **Finish the Ohio recordings** (the Ohio records work; details stay in the work-product
      folder, never in this file).
- [ ] **Read the PurpleLab emails** (the claims-data proof of concept; the plan's outline is
      `docs/ANALYST_BRIEFING_2026-10-26.md` section 8).
- [ ] **Learn about .md (Markdown) files**: how they work and how the project uses them.
- [ ] **Make sure I can test model features** myself (the test suite on fake data, and a
      full-size check on the VM).
- [ ] **Build a data research plan for import/export** (scope to agree: researching new data
      to bring INTO the pipeline (import) and what the pipeline sends OUT, i.e. the export
      step and the files it produces? or something else?).
- [ ] **Independent stage agents under a master agent** (run each part of the model on its
      own, e.g. reuse a graph for a new export; subagents for discrete tasks; maximise
      efficiency; avoid code bloat). Plan: `AGENT_PLAN.md` on this branch
      (`git show origin/daschel/notes:AGENT_PLAN.md`). Next step: Phase 0, the stage map.

- [ ] **v31: a pre-cutoff CLIA edition** (so the labs' certificate type and test volumes are
      their 2023 values, not 2026's). Needs (1) an older CLIA file downloaded from CMS (e.g. the
      Q4 2023 edition of "Provider of Services File - Clinical Laboratories", historical
      versions on data.cms.gov) into `preclean/clia/` with the quarter in its name, and (2) a
      code change so the export picks the edition for each freeze date instead of stacking every
      file in the folder (else each lab is read twice). Then PR D. The original-date filter
      (PR C) stays either way. v30's pre-registration already says "a pre-cutoff edition comes
      in v31".

## WHERE WE LEFT OFF (2026-11-07, night) -> START HERE IN THE MORNING

- v30 is READY TO RUN; the analyst chose to run it in the morning. Phase 0 of
  `V30_RUN_PLAN.md` is complete (all items ticked). Next: phase 2 (create `~/mfd-v30` at
  e7e8939 after `tmux ls`), phase 3 (CLIA lookup rebuild) and phase 4 (official data scan),
  optionally 3 + 4 side by side with `MEDICAID_DUCKDB_MEM=24GB` each (saves about an hour),
  phase 5 (pre-register: drafted, bar 0.7, CLIA notes in), phase 6 (start v30 + send the
  saved Gmail draft to the operator with the time). About 1.5-3 hours to v30 running, about
  30 min of typing.
- PRs open: #3 (book Jaccard, evidence posted), #4 (runner stop list), C (CLIA original-date
  fix; see below). None blocks v30: it runs from `daschel/v30`.
- Nothing is running on the VM (both checks finished).

## Current state (2026-11-06)

- **PR #2 merged** (2026-11-06): three "ghost staff" features in `src/model_a/servicing_grain.py`
  (`srv_hub_share`, `srv_out_of_state_share`, `srv_inactive_share`), their own export source
  `servicing_mix`. They first take effect in **v30** (with CLIA). v29 did not include them.
- **v29 read**: HOLD on one check, top-50 stability (Jaccard) 0.599 vs bar 0.6. Its pursuit
  report shows `acc_top50_jaccard_book = None`, `source = all_orgs`: the failing number looks
  like the all-orgs fallback, not the pursuit-list measure. Open question for the operator.
- **v30 = v29's setup (2023-12 freeze, HORIZON=33, SEEDS=10, train hard) PLUS** (summary
  2026-11-07; v29 ran on 3256366):
  - NEW SOURCES: `servicing_mix` (my PR #2, merged) and `clia` (code already there; v29 left the
    file out on purpose; file on the VM, 686,058 certificates, all columns resolve).
  - FIXES (two PRs, opened 2026-11-07): PR #3 = the book-Jaccard fix (branch
    `daschel/fix-book-jaccard`; v29's real book number from the VM check to be added as a
    comment); PR #4 = the runner stop list + `servicing_mix` / `clia` and a CLIA success-line
    check (branch `daschel/runner-stop-list`; stops only at ZERO CLIA links; 9 tests; ci-local
    and the full suite green). Both need review + merge before v30.
    v29 check running on the VM: worktree `/tmp/mfd-daschel` at 6d28546, output
    `/tmp/mfd-daschel/out/v29_book_jaccard_check/`; settings line identical to v29's. Data scan
    PREVIEW (pre-pull) log `/tmp/mfd-daschel/out/data_audit_2023-12.log`; the official scan
    reruns on v30 day after the pull + CLIA rebuild. Two jobs at once: cap each with
    `export MEDICAID_DUCKDB_MEM=24GB` next time.
  - OTHER MERGED CODE SINCE v29: licensed-capacity loader (agency capacity column) and the policy
    library's quote / domain gates: they change v30 only if the capacity rosters or the policy
    library were rebuilt / reloaded since v29 (the data scan shows it).
  - DATA: CLIA lookup table rebuild (`nppes_other_ids.parquet` has no clia rows): scratch names,
    backup, swap; never overwrite `ccn_to_npi.parquet`.
  - NOT IN v30: the NPPES leak fix (v31), Tier A (outside the model), rendering_identity weights.
  - PRE-REGISTRATION: v29's checks with `top50_jaccard >= 0.6` replaced by
    `"top50_jaccard_book >= 0.6" "jaccard_source = book"`; the goal names both new sources and
    says to read servicing_mix on the pursuit / operator books, near misses and the state ruler.
    NEEDS: v29's line (`grep "frozen_2023-12_v29" /data/medicaid/model_a/preregistrations.jsonl`).
- **THE CEO APPROVED v30 (2026-11-07): "create a new branch, be systematic".** v30 runs from
  branch `daschel/v30` (integration branch + PR #3 + PR #4 merged in; PRs stay open for review).
  THE AUTHORITATIVE STEP-BY-STEP IS `V30_RUN_PLAN.md` on this branch
  (`git show origin/daschel/notes:V30_RUN_PLAN.md`): 9 phases, each ending in a gate, each
  started only on my "go". The list below is the earlier summary.
- **v30 order on the day:**
  1. PR merged (fix + stop list).
  2. Email the current operator that the server is in use (EMAIL RULE below).
  3. VM: `tmux ls` (nothing running), then `git pull --ff-only`.
  4. Data scan: `bash ops/vm/data_audit.sh 2023-12` (before every pre-registration).
  5. Rebuild the CLIA lookup table.
  6. Pre-register v30.
  7. Start in tmux: `HORIZON=33 SEEDS=10 PREV_RUN=/data/medicaid/model_a/frozen_2023-12_v29
     bash ops/vm/run_frozen_night.sh v30 2023-12`.
  8. About an hour in, confirm `servicing_mix` and `clia` read "used" in the run's
     `SOURCES_REPORT.md` (PR #4 now stops the run itself if either is skipped, or if CLIA
     links ZERO labs: decided, keep the stop). Then read how CLIA matched:
     `grep "\[clia\] linked" /data/medicaid/model_a/frozen_2023-12_v30/night_run.log`:
     `other_id N` = matched through the rebuilt lookup table (good); only `name_address` =
     the table was not used (CLIA still runs, less efficiently: note it, fix for the next run).
  9. After the run: read `SOURCE_ABLATION.md` in the run folder. Its per-group row for
     `servicing_mix` (the run re-scored without my three features) is the answer to "did
     my features help?". Also read them on the pursuit/operator books and the state ruler.
- **Operator's PR #2 answers (2026-11-06)**: `srv_inactive_share` trains; `current_state`
  label stays until the leak fix; hub rule as built. Side note (optional FYI to the
  operator, not sent): his reason "billing_after_deactivation already trains" does not
  match the code (it is on `LEAKAGE_HARD`); his conclusion still holds (deactivation is not
  in the hard-tier training label, self rows are excluded, the single-column screen).
- **Next PR (requested by the operator)**: Tier A contradiction = paid months under a rendering
  NPI deactivated at service time, innocent explanations printed first. Start by reading
  `src/model_a/contradictions.py` and explaining the existing deactivation check before coding.
- **Known leak (operator's backlog, credited to me)**: on frozen runs the export reads today's
  NPPES (`processed/provider_dim.parquet`) instead of an as-of copy; the graph is already fixed
  via `--asof-nppes`. A possible PR of mine if the operator wants it.

## Open questions

- CLIA for v30: the file is on the VM (`preclean/clia/pos_clia_202609.csv`, 686,058 rows, all
  columns incl. the four volume columns resolved). BUT `processed/nppes_other_ids.parquet`
  (2026-10-13) holds only `medicaid` ids, no `clia` (or UPIN) rows: rebuild with
  `ccn_npi_crosswalk --other-ids-out` into interim/ scratch names first (never overwrite
  `ccn_to_npi.parquet`), check, back up the old table, then swap; heads-up to the operator.
- Does the operator want the as-of provider_dim fix before v30, or later?

## Done (recent)

- 2026-11-05: setup script fixed (operator's guarded install line, tested in a fresh session).
- 2026-11-05/06: PR #2 written, reviewed, merged; branches renamed to lowercase `daschel/...`.
- 2026-11-06: checked a publicly reported ABA (autism therapy) case against the current-day run:
  the entities never billed Medicaid under their own NPIs, so the model could not score them
  (outside its data, not a miss). Worth noting to the operator as a private-insurance gap.

## Quick reference

- VM login: `ssh -i ~/.ssh/medicaid-vm ubuntu@<VM address>`, then
  `cd ~/medicaid-fraud-detection && source .venv/bin/activate && source ~/.medicaid_env`
  (the operator's standard line; never switch branches / pull / edit there while a run is going).
  Optional scratch worktree for experiments that must not touch the running code:
  `cd ~/medicaid-fraud-detection && git worktree add /tmp/mfd-daschel HEAD`.
- Run results: `/data/medicaid/model_a/<run folder>/`. Current-day run: `current_202610_cd1`.
  Newest test run: `frozen_2023-12_v29`. Verdicts: `/data/medicaid/model_a/MODEL_RUNS.md`.
- Search a run's lists for a company: `grep -in "name" <run folder>/provider_priority_list*.csv`
  (rank = line number - 1). Rows are leads for review, never accusations.
- Read-only by default on the VM: `ls`, `head`, `less`, `grep`, `cat`. Never `git pull` or
  restart the VM while a run is going.
- Glossary: NPI = 10-digit provider ID; NPPES = the national provider registry; servicing /
  rendering NPI = the clinician who did the work; freeze / frozen run = a test run rewound to an
  old date; pre-registration = the pass/fail checks written before a run; Tier A = hard,
  checkable facts that contradict each other in the records (not model guesses); PI = private
  investigator.
