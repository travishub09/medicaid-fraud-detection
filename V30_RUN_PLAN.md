# v30 run plan (approved by the CEO: "create a new branch, be systematic")

Run v30 from branch **`daschel/v30`** = the integration branch `claude/happy-mccarthy-3skrn3`
+ PR #3 (book-Jaccard fix) + PR #4 (runner stop list / CLIA success check). The PRs stay open
for review; the run records the exact commit it used. Every phase ends in a GATE: do not start
the next phase until the gate holds. Each phase starts only on the analyst's "go".

| Phase | What | Time |
|---|---|---|
| 0 | Preconditions (decisions + checks) | in progress |
| 1 | Tell the current operator | 5 min |
| 2 | Put the v30 code on the VM | 5 min |
| 3 | Rebuild the CLIA lookup table | 30-60 min (estimate) |
| 4 | Official data scan | tens of minutes |
| 5 | Pre-register v30 | 10 min |
| 6 | Start v30 | 5 min, then overnight |
| 7 | One-hour checkpoint (after the export) | 10 min |
| 8 | Results + close-out | morning |

## Phase 0: preconditions

- [x] v29 book-Jaccard check finished (2026-11-07): `acc_top50_jaccard_book` **0.7493**,
      `source = book`, 0 "not built" lines; `acc_top50_jaccard_all` 0.5990 = v29's registered
      number exactly (the setup reproduces v29). On the book measure v29 passes. Comment on
      PR #3: drafted, posts on the analyst's OK.
- [x] v30 stability bar: **0.7** (analyst's decision, 2026-11-07, before v30). Reason: 0.6 would
      pass a quarter of the 50 reshuffling (about 37-38 of 50 shared), too weak to catch a real
      drop; 0.7 = about 41 of 50 shared, about two companies of margin below v29's re-measured
      0.749 (about 43) to absorb 3-repeat noise; not set at v29's own number.
- [ ] Data scan PREVIEW read (report folder `/data/medicaid/audit_20261107_0028/`, the frozen
      report under `frozen_2023-12/`): no REQUIRED MISSING on a source v30 uses. Flagged:
      "clia: every edition is after 2023-12": the export reads the 2026-09 file anyway and keeps
      only certificates effective on/before 2023-12 (clia.asof_certificates); the labs' details
      (volumes, certificate type) are 2026 values = a known current_state limit (pending the
      analyst's decision: run as is + record it, or fix first; v31 fix = an edition picker + the
      CMS Q4-2023 CLIA edition). Owners editions all after 2023-12 and the absent optional files
      were the same for v29.
- [x] Branch `daschel/v30` pushed at **e6f5030**; `make ci-local` green on it (1,468 passed,
      2 skipped, both fixture end-to-end runs OK; 2026-11-07).
- [x] v29's pre-registration line captured (21 checks, 2026-11-07):
      `grep "frozen_2023-12_v29" /data/medicaid/model_a/preregistrations.jsonl`
**GATE 0:** all five ticked.

## Phase 1: tell the current operator (email; Claude drafts, sent on the analyst's OK)

Subject "VM in use: v30 run starting [time]". Body: v30 starting at [time] from `daschel/v30`,
running overnight; until the all-clear, no git pull / branch switch / edits in
~/medicaid-fraud-detection, no other run or lanes cycle on the server; v30 = v29's setup +
servicing_mix + CLIA + the book-Jaccard fix + the runner stop list; an email when it finishes.
**GATE 1:** email sent.

## Phase 2: the v30 code on the VM, in its OWN worktree (never switch the main folder)

The run happens in `~/mfd-v30`, a separate git worktree of `daschel/v30`. The main folder
`~/medicaid-fraud-detection` (the integration branch, the current operator's tools) is never
switched. A home folder, not /tmp (a reboot can wipe /tmp mid-run). Outputs still land in
`/data/medicaid` as usual.
```bash
tmux ls
```
Only finished `jaccard` / `audit` or nothing. Any run (v29, v30, cd2, lanes): STOP.

Branch check (the run must measure exactly "the integration branch + PR #3 + PR #4"):
```bash
cd ~/medicaid-fraud-detection && git fetch origin claude/happy-mccarthy-3skrn3 daschel/v30 && git log --oneline origin/claude/happy-mccarthy-3skrn3 -1 && git log --oneline origin/claude/happy-mccarthy-3skrn3..origin/daschel/v30
```
Expect the integration head **2924671** and exactly five commits (e6f5030, 9dad277, 8d97fa8,
8bf0930, 6d28546). If the integration branch has moved: STOP, Claude rebuilds `daschel/v30`
on the new head (re-test, re-push), then repeat this check.

Create the worktree:
```bash
cd ~/medicaid-fraud-detection && git worktree add --detach ~/mfd-v30 origin/daschel/v30 && git -C ~/mfd-v30 log --oneline -1
```
Must show **e6f5030** (or the rebuilt commit).
PYTHON: the runner activates `$REPO/.venv`, which a worktree does not have (it would silently
fall back to the system Python). EVERY command in `~/mfd-v30` therefore starts with
`source ~/medicaid-fraud-detection/.venv/bin/activate` (child steps inherit it).
**GATE 2:** `~/mfd-v30` exists at the expected commit; the main folder untouched.

## Phase 3: rebuild the CLIA lookup table (`processed/nppes_other_ids.parquet`)

Why: the current table (2026-10-13) holds only `medicaid` ids, no `clia`, so CLIA can only link
labs by name + address. The rebuild adds the CLIA (and UPIN) ids.
1. Find the raw NPPES file: `ls -la /data/medicaid/preclean/ | grep -i nppes; ls -la /data/medicaid/preclean/nppes/ | head`
2. Build to NEW names, in tmux (nothing existing is touched; `--out` goes to scratch so
   `processed/ccn_to_npi.parquet` is never overwritten):
   ```bash
   mkdir -p /data/medicaid/interim/otherids_rebuild && tmux new -d -s otherids 'cd ~/mfd-v30 && source ~/medicaid-fraud-detection/.venv/bin/activate && python -m src.ingest_cms.ccn_npi_crosswalk --in <NPPES file> --out /data/medicaid/interim/otherids_rebuild/ccn_to_npi_scratch.parquet --other-ids-out /data/medicaid/interim/otherids_rebuild/nppes_other_ids.parquet > /data/medicaid/interim/otherids_rebuild/build.log 2>&1'
   ```
   Run it alone (it loads the whole registry into memory).
3. Check: `tail -3 /data/medicaid/interim/otherids_rebuild/build.log` prints
   "other identifiers: N rows (medicaid ..., clia ..., upin ...)": the `clia` count must be > 0.
4. Back up, then swap (only the lookup table):
   ```bash
   cp -p /data/medicaid/processed/nppes_other_ids.parquet /data/medicaid/processed/nppes_other_ids_backup_20261013.parquet && cp /data/medicaid/interim/otherids_rebuild/nppes_other_ids.parquet /data/medicaid/processed/nppes_other_ids.parquet && ls -la /data/medicaid/processed/nppes_other_ids*.parquet
   ```
**GATE 3:** new table in place with clia > 0; the backup exists. (If clia = 0: do not swap; v30
can still run on name + address matching. Note it and decide.)
ROLLBACK: copy the backup back over `nppes_other_ids.parquet`.

## Phase 4: the official data scan (after the pull and the rebuild)

```bash
tmux new -d -s audit 'cd ~/mfd-v30 && source ~/medicaid-fraud-detection/.venv/bin/activate && bash ops/vm/data_audit.sh 2023-12 > /tmp/mfd-daschel/out/data_audit_v30.log 2>&1'
```
Read the summary (`DATA_AUDIT.md` in the folder the log names).
**GATE 4:** no REQUIRED MISSING on a source v30 uses; anything SILENT BLANK or POST-CUTOFF is
understood and written here.

## Phase 5: pre-register v30

Checks = v29's list, with `top50_jaccard >= 0.6` REPLACED by
`"top50_jaccard_book >= <bar>" "jaccard_source = book"`. Goal names: the two new sources
(servicing_mix, clia), the book-Jaccard fix, the runner stop list, the branch `daschel/v30` at
its commit, and the reading plan (servicing_mix on the pursuit / operator books, the near
misses and the state ruler; SOURCE_ABLATION's servicing_mix group). Claude drafts the exact
`python -m src.model_a.run_registry --data-root /data/medicaid --preregister --tag
frozen_2023-12_v30 ...` command from v29's line.
DRAFT (2026-11-07; bar filled in: 0.7; the other 20 checks are v29's word for word; before
running, add the CLIA known-limit sentence to --expected if the analyst runs CLIA as is):
```bash
cd ~/mfd-v30 && source ~/medicaid-fraud-detection/.venv/bin/activate && python -m src.model_a.run_registry --data-root /data/medicaid --preregister --tag frozen_2023-12_v30 \
  --goal "v30 = v29's setup (2023-12 freeze, horizon 33, 10 seeds, train hard, batch-5b policy) run from branch daschel/v30 at e6f5030, adding two sources: servicing_mix (srv_hub_share, srv_out_of_state_share, srv_inactive_share; PR #2) and clia (clia_volume_ratio, clia_waiver_high_complexity into lab_testing; CLIA ids from the rebuilt nppes_other_ids); plus the book top-50 Jaccard fix (PR #3) and the runner stop list (PR #4)" \
  --expected "v29's checks with the stability check read on the pursuit book (top50_jaccard_book >= 0.7, jaccard_source = book; 0.7 chosen before this run from v29's re-measured book Jaccard 0.749, reason in V30_RUN_PLAN.md); read servicing_mix's per-group SOURCE_ABLATION row, the pursuit / operator books, the near misses and the state ruler; read lab_testing with CLIA in SCHEME_HEALTH" \
  --check "hits100 >= 2" "hits250 >= 4" "large_hits50 >= 2" "npi_lift1pct >= 3" "score_scale >= 0.1" "inst_share50 <= 0.2" "top50_jaccard_book >= 0.7" "jaccard_source = book" "train = hard" "policy = hard" "frozen = True" "seeds >= 10" "groups = case_cluster" "broadcast = weight" "priors = none" "prune = none" "embeddings = 0" "fca_reach >= 0.02" "ruler_graph = asof" "facility_rollup = own_ccn" "segment = all" "case_budget = npi100"
```
HARD STOP: the word `BAR` must NOT be in the command when it runs. A literal `BAR` cannot be
measured, an unmeasured check counts as a FAIL, and v30 would HOLD automatically. Replace it
with the chosen number, then re-read the --check list before pressing Enter; if `BAR` appears
anywhere, do not run it.
**GATE 5:** `grep "frozen_2023-12_v30" /data/medicaid/model_a/preregistrations.jsonl` shows it.

## Phase 6: start v30

```bash
tmux ls
```
Nothing running. Then:
```bash
tmux new -d -s v30 'cd ~/mfd-v30 && source ~/medicaid-fraud-detection/.venv/bin/activate && HORIZON=33 SEEDS=10 PREV_RUN=/data/medicaid/model_a/frozen_2023-12_v29 bash ops/vm/run_frozen_night.sh v30 2023-12 >> /data/medicaid/model_a/v30.log 2>&1' && sleep 5 && tmux ls && tail -5 /data/medicaid/model_a/v30.log
```
The runner refuses to start under 24 GB free RAM and names the problem.
**GATE 6:** `v30` in `tmux ls`; the log shows step 0 starting.

## Phase 7: one-hour checkpoint (after the export, step 2)

```bash
grep -E "step [0-9a-z]+/13|FAILED|stop" /data/medicaid/model_a/frozen_2023-12_v30/night_run.log | tail -8
grep -E "servicing_mix|\`clia\`" /data/medicaid/model_a/frozen_2023-12_v30/SOURCES_REPORT.md
grep "\[clia\] linked" /data/medicaid/model_a/frozen_2023-12_v30/night_run.log
```
- Both sources "used"; CLIA's line shows `other_id N` (the rebuilt table worked) and/or
  `name_address N`.
- If the runner STOPPED (it names the reason): paste the last 40 lines of
  `night_run.log`; fix; resume in the same folder with `RESUME_FROM=<step id>` (no new
  pre-registration needed for a resume).
**GATE 7:** past step 2 with both sources used.

## Phase 8: results + close-out

- DONE banner in `night_run.log`; the registry verdict (`tail` of `MODEL_RUNS.md`): PROMOTE or HOLD
  with the failing checks.
- Read: `RESULTS_DIGEST.md`; `ACCURACY_DIAGNOSTICS__pursuit.md` (hits, the book Jaccard);
  `SOURCE_ABLATION.md` (the `servicing_mix` group: did the features help?); `SCHEME_HEALTH.md`
  (lab_testing with CLIA); `RUN_ISSUES.md`.
- Email the current operator: the server is free.
- Post the evidence on PRs #3 / #4 and keep them open for the integration-branch owner to
  review and merge. The main folder was never switched; remove the worktree when v30's
  read-out is done (`git -C ~/medicaid-fraud-detection worktree remove ~/mfd-v30`).
- Update NOTES.md (objectives: "Run the model myself" ticked).

## Rules that apply throughout

One run at a time; nothing in `~/mfd-v30` (and no data rebuilds) while v30 runs; long jobs in tmux; keys stay
private (warning beside any command that shows them); no data or rows naming people leave the
server; nothing with "dispatch".
