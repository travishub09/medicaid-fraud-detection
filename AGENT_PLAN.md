# Plan: independent stage agents under a master agent (draft, 2026-11-06)

Goal: run each part of the model on its own (e.g. reuse a previous graph for a new export),
with one Claude **subagent per stage** under a **master agent**, as efficiently as possible,
and **without adding code bloat**.

Status: DRAFT for the analyst; agree the "where it lives" and the reuse rules with the
operator before Phase 2. Nothing here changes the pipeline.

## 1. Design principles

1. **No new pipeline code.** Every stage is already its own command, and the runner
   (`ops/vm/run_frozen_night.sh`) already runs any slice of a run (`RESUME_FROM=<step>`) or
   re-grades an old run (`ops/vm/regrade_from.sh`). The agents are short Markdown
   instruction files that call those commands. No wrapper scripts, no copies of logic.
2. **One source of truth for the stages:** a single stage map (section 3). Each agent file
   points to its rows instead of repeating them, so a change is made once.
3. **Agents check; the machine computes.** The heavy work (hours) runs in `tmux` on the VM.
   An agent prepares, starts, checks the result and reports in a few lines. It never sits
   watching a multi-hour job.
4. **Small, discrete tasks per agent:** each stage agent has one job, a fixed list of
   inputs to verify, one command, and a fixed list of outputs to check. That keeps each one
   cheap and lets it run on a smaller, faster model; the master uses the stronger model
   for decisions.
5. **Safety rules live in the master only:** one run at a time, pre-registration before any
   graded run, never `git pull` during a run, no research-agent (Manus) dispatch, never
   print `~/.medicaid_env`, read-only unless the task says otherwise.

## 2. Where the agents live (decide with the operator)

| Option | Location | Pros | Cons |
|---|---|---|---|
| **A (recommended to start)** | On the VM, user-level: `~/.claude/agents/*.md` | Nothing added to the repository (no bloat, no PR); persists on the VM disk | Only on that VM; not shared with the team |
| B | In the repository: `.claude/agents/*.md` | Shared with every session and teammate | Shared files, operator's review; adds files to the repo |

Either way Claude Code has to run **on the VM** (the data and the compute are there; a cloud
session cannot reach it).

## 3. The stage map (the 21 runner step ids grouped into 7 stages)

| Stage agent | Runner steps | What it does | Main output | Typical reuse |
|---|---|---|---|---|
| **1. inputs** | 0, 0m, 2a | Picks the as-of NPPES edition for the freeze; merges the case/CIA label inputs; loads newer state termination lists | `freeze_inputs.json`, merged case + defendant files, state exclusions | Reusable while the raw data and label files are unchanged |
| **2. graph** | 1, 2b | Builds the point-in-time graph for the freeze (and the current-day graph when exclusions are newer) | `graph_asof_<cut>_full/` | Reusable for any export with the **same freeze date** and unchanged inputs |
| **3. export** | 2, 2c | Builds the feature table (one row per provider), then the state-side employer file | `provider_features_for_model.parquet`, manifest, `SOURCES_REPORT.md` | Rerun after any feature change (e.g. new `srv_*` columns) |
| **4. rulers** | 3 | Builds the "answer keys": who was caught after the freeze (FCA, exclusion, qui tam, state, typed) | `future_bans_after_<cut>__*.csv` | Reusable while label inputs and the as-of graph are unchanged |
| **5. scoring + books** | 6, 7, 8, 8p, 8c, 8x | Scores providers, builds the lead lists, grades them; the A/B arms | `provider_priority_list*.csv`, `ACCURACY_DIAGNOSTICS__*.md`, `ab_*/` | Re-grade only (`regrade_from.sh`) when only scoring policy changes |
| **6. measurement** | 4, 5, 11, 12, 13 | Network A/B, scheme triage, learning loop, ablations (incl. per-source groups), issue ledger | `NETWORK_AB_REPORT.md`, `SCHEME_*.md`, `SOURCE_ABLATION.md`, `RUN_ISSUES.md` | Rerun after stage 5 |
| **7. report + register** | 9, 10 | Results digest, pursuit dossier (no dispatch), registers the run against its pre-registration | `RESULTS_DIGEST.md`, `PURSUIT_DOSSIER.md`, `MODEL_RUNS.md` row | Always last |

**Reuse rule of thumb (to confirm with the operator before relying on it):** when something
changes, rerun that stage and every stage after it; reuse the earlier ones. A reused piece must
come from the **same freeze date** and the same input files, and a graded run must say in its
pre-registration what it reused. Examples:
- new feature in the export: reuse 1, 2, (4); rerun 3, 5, 6, 7;
- new scoring policy only: reuse 1–4; re-grade 5, then 6, 7;
- new label data: rerun 1 (0m), 4, then 5, 6, 7; the export too if the label enters the table.

## 4. The agents

**Master agent** (`run-master.md`, stronger model):
1. Reads the request ("export with the existing graph for 2023-12"), the stage map and
   the run registry.
2. Decides which stages to run and which outputs to reuse, using the reuse rules, and
   checks that reused outputs exist and match the freeze date.
3. Checks the safety rules: nothing running (`tmux ls`), code up to date, pre-registration
   present for a graded run, enough free memory.
4. Calls stage agents in order, one at a time, passing exact paths.
5. Reports at the end: what ran, what was reused, where the results are, anything skipped.

**Stage agent** (`stage-<name>.md`, smaller model; one file per stage, about 30 lines each):
- **Inputs check:** the files it needs exist, with the right freeze date.
- **Command:** the exact command, started in `tmux` (or `RESUME_FROM` / `regrade_from.sh`
  for runner slices), with the log path.
- **Done check:** the log line or files that prove success (e.g. export:
  `SOURCES_REPORT.md` shows `servicing_mix` as used).
- **Report:** 3 to 5 lines: success or failure, outputs, time taken, anything skipped.

Waiting: for multi-hour stages the stage agent starts the job and stops; the master (or the
analyst) checks back with the done check later, instead of an agent polling for hours.

## 5. Phases and time

| Phase | What | Time |
|---|---|---|
| 0 | Write the stage map in full: each step's exact command, inputs, outputs, run time, done check (from the runner and each module) | 2–3 hours |
| 1 | Write the master + 7 stage agent files (Option A) | half a day |
| 2 | Dry run on the VM with something cheap: a re-grade of v29 (stage 5 to 7 only) through the agents | a few hours of VM time |
| 3 | Use the agents for v30 (stages 1 to 7) and note every snag | one run (overnight) |
| 4 | Fix the agent files from the snags; decide with the operator whether to move to Option B | 1–2 hours |

Total: about 1 to 2 days of sessions plus 1 to 2 runs of VM time.

## 6. Avoiding bloat (checklist)

- No new Python or shell files; agents only call existing commands.
- One stage map; agent files reference it, they do not copy it.
- Each agent file stays short (target under 40 lines); if one grows, the stage map is missing
  something.
- Option A keeps everything out of the repository until it has proven itself.
- Delete or merge any agent that is not used in two runs.

## 7. Open questions for the operator

1. OK to install and run Claude Code on the VM, with agents in `~/.claude/agents/` (Option A)?
2. Are the reuse rules in section 3 right, especially which outputs depend on the as-of graph?
3. Should a run that reuses earlier stages carry a pre-registration note listing them?
4. Is the dry run a re-grade of v29 acceptable (it writes a new run folder)?
