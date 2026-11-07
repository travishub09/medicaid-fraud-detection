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

- [ ] v29 book-Jaccard check finished (`/tmp/mfd-daschel/out/v29_book_jaccard_check/`):
      `acc_top50_jaccard_book` is a number, `acc_top50_jaccard_source` = `book`, 0
      "book top-50 ... not built" lines. Number posted on PR #3.
- [ ] v30 stability bar chosen with the reason written here: ______
- [ ] Data scan PREVIEW read (`/tmp/mfd-daschel/out/data_audit_2023-12.log` -> `DATA_AUDIT.md`):
      no REQUIRED MISSING on a source v30 uses.
- [ ] Branch `daschel/v30` pushed; `make ci-local` green on it.
- [ ] v29's pre-registration line captured:
      `grep "frozen_2023-12_v29" /data/medicaid/model_a/preregistrations.jsonl`
**GATE 0:** all five ticked.

## Phase 1: tell the current operator (email; Claude drafts, sent on the analyst's OK)

Subject "VM in use: v30 run starting [time]". Body: v30 starting at [time] from `daschel/v30`,
running overnight; until the all-clear, no git pull / branch switch / edits in
~/medicaid-fraud-detection, no other run or lanes cycle on the server; v30 = v29's setup +
servicing_mix + CLIA + the book-Jaccard fix + the runner stop list; an email when it finishes.
**GATE 1:** email sent.

## Phase 2: the v30 code on the VM

```bash
tmux ls
```
Only `jaccard` / `audit` (finished) or nothing. Any run (v29, v30, cd2, lanes): STOP.
```bash
cd ~/medicaid-fraud-detection && git status --short | head && git fetch origin daschel/v30 && git checkout -B daschel/v30 origin/daschel/v30 && git log --oneline -1
```
`git status` must be empty first (local edits in the main checkout: STOP and look). The last
line must show the pushed `daschel/v30` commit.
**GATE 2:** the main checkout is on `daschel/v30` at the pushed commit.

## Phase 3: rebuild the CLIA lookup table (`processed/nppes_other_ids.parquet`)

Why: the current table (2026-10-13) holds only `medicaid` ids, no `clia`, so CLIA can only link
labs by name + address. The rebuild adds the CLIA (and UPIN) ids.
1. Find the raw NPPES file: `ls -la /data/medicaid/preclean/ | grep -i nppes; ls -la /data/medicaid/preclean/nppes/ | head`
2. Build to NEW names, in tmux (nothing existing is touched; `--out` goes to scratch so
   `processed/ccn_to_npi.parquet` is never overwritten):
   ```bash
   mkdir -p /data/medicaid/interim/otherids_rebuild && tmux new -d -s otherids 'cd ~/medicaid-fraud-detection && source .venv/bin/activate && python -m src.ingest_cms.ccn_npi_crosswalk --in <NPPES file> --out /data/medicaid/interim/otherids_rebuild/ccn_to_npi_scratch.parquet --other-ids-out /data/medicaid/interim/otherids_rebuild/nppes_other_ids.parquet > /data/medicaid/interim/otherids_rebuild/build.log 2>&1'
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
tmux new -d -s audit 'cd ~/medicaid-fraud-detection && source .venv/bin/activate && bash ops/vm/data_audit.sh 2023-12 > /tmp/mfd-daschel/out/data_audit_v30.log 2>&1'
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
**GATE 5:** `grep "frozen_2023-12_v30" /data/medicaid/model_a/preregistrations.jsonl` shows it.

## Phase 6: start v30

```bash
tmux ls
```
Nothing running. Then:
```bash
tmux new -d -s v30 'cd ~/medicaid-fraud-detection && HORIZON=33 SEEDS=10 PREV_RUN=/data/medicaid/model_a/frozen_2023-12_v29 bash ops/vm/run_frozen_night.sh v30 2023-12 >> /data/medicaid/model_a/v30.log 2>&1' && sleep 5 && tmux ls && tail -5 /data/medicaid/model_a/v30.log
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
- Post the evidence on PRs #3 / #4; ask for the merge; afterwards return the main checkout to the
  integration branch (`git checkout claude/happy-mccarthy-3skrn3 && git pull --ff-only`) once
  the PRs are in.
- Update NOTES.md (objectives: "Run the model myself" ticked).

## Rules that apply throughout

One run at a time; nothing in the main checkout while v30 runs; long jobs in tmux; keys stay
private (warning beside any command that shows them); no data or rows naming people leave the
server; nothing with "dispatch".
