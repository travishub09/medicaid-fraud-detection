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
CONFLICT TO SETTLE WITH THE OPERATOR: his setup email says every VM command starts
`cd ~/medicaid-fraud-detection && source .venv/bin/activate && source ~/.medicaid_env`; his rules
list says use `/tmp/mfd-daschel` (14) and never open `~/.medicaid_env` (19). Likely reading of 19:
sourcing (loads, shows nothing) is fine; viewing / copying / editing is not. Until he answers:
probes in `/tmp/mfd-daschel`, source the env only when a step needs the service logins, never
view it.
Known slips to avoid repeating (2026-11-06): probes run in the main checkout; telling the
analyst that viewing `~/.medicaid_env` was fine; a planned `git pull` and full v30 run on the VM; a CLIA
lookup rebuild that would have written into processed/; query rows naming individuals pasted
into chat; `make ci-local` skipped for PR #2. v30 itself: confirm with the operator how the
management go-ahead fits rule 16 before starting it.

## My role and permissions

- Technical analyst on the Medicaid fraud-detection platform. My work goes on `daschel/<topic>`
  branches as pull requests into the integration branch `claude/happy-mccarthy-3skrn3`
  (never into `main`, never pushed to directly). The operator reviews and merges.
- Management has authorised me to use the VM and run model runs. Habits kept anyway: a one-line
  heads-up to the operator before starting a run (one big run at a time on the VM), and a
  heads-up before editing the shared environment's setup script.
- Shared files I don't edit directly: `CLAUDE.md`, `docs/reminders.json` (the operator's).
  The operator's reminder list is not mine to work through in my sessions.

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

## Current state (2026-11-06)

- **PR #2 merged** (2026-11-06): three "ghost staff" features in `src/model_a/servicing_grain.py`
  (`srv_hub_share`, `srv_out_of_state_share`, `srv_inactive_share`), their own export source
  `servicing_mix`. They first take effect in **v30** (with CLIA). v29 did not include them.
- **v29 read**: HOLD on one check, top-50 stability (Jaccard) 0.599 vs bar 0.6. Its pursuit
  report shows `acc_top50_jaccard_book = None`, `source = all_orgs`: the failing number looks
  like the all-orgs fallback, not the pursuit-list measure. Open question for the operator.
- **v30 plan (I run it)**, in order:
  1. `tmux ls` (nothing running), `git pull --ff-only` on the VM (expect merge commit 606aab4).
  2. Test my features at full size on the v29 as-of fact before the run (they are NOT on the
     runner's stop-if-skipped list, so a failure would be silent).
  3. Settle with the operator: the v30 checks (draft from v29's preregistration line +
     `servicing_mix` + CLIA), whether the CLIA file is synced (`ops/vm/sync_data.sh
     down-preclean`), and the Jaccard question.
  4. Pre-register, then start in tmux:
     `HORIZON=33 SEEDS=10 bash ops/vm/run_frozen_night.sh v30 2023-12`.
  5. After the export step, confirm `servicing_mix` reads "used" in the run's
     `SOURCES_REPORT.md`; if skipped, stop the run.
  6. After the run: read `SOURCE_ABLATION.md` in the run folder. Its per-group row for
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
  `cd /tmp/mfd-daschel && source ~/medicaid-fraud-detection/.venv/bin/activate` (rule 14; add
  `&& source ~/.medicaid_env` only when a step needs the service logins, never view it: see
  the CONFLICT note above). One-time worktree setup (redo if /tmp was cleared):
  `cd ~/medicaid-fraud-detection && git worktree add /tmp/mfd-daschel HEAD && mkdir -p /tmp/mfd-daschel/out`.
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
