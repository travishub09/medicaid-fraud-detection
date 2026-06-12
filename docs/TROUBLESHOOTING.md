# Troubleshooting — Plain-English Error Guide

Something printed an error or a report shows a FAIL. This guide explains what
the common ones mean and what to do — without assuming you write code.

**The mindset:** this system is built to **stop loudly rather than produce
quietly wrong numbers**. An error is usually the system protecting you from bad
data, not the system being broken. The one thing you must never do is "patch
around" a failed check — the check is telling you a number downstream would
have been wrong.

---

## "ASSERTION FAILED: …" — the self-checks

Every stage checks its own math and refuses to continue if anything is off.
The message names the check. The frequent ones:

| Message contains | What it means | What to do |
|---|---|---|
| `rowcount_preserved` / `no_fanout` | A join tried to duplicate rows — the same dollars would have been counted twice | Almost always a data problem: a file has unexpected duplicate IDs. Check the QA report's row counts against what you downloaded; re-download if the file looks truncated or doubled. |
| `dollars_preserved` / `dollar_conservation` | Money went missing (or appeared) between two steps | Same as above — the input changed shape. Never proceed; the totals downstream would be wrong. |
| `unique_npi` / `one_row_per` | A table that must have exactly one row per provider has duplicates | Usually two overlapping source files were dropped in (e.g. two NPPES vintages in the folder). Keep exactly one per source. |
| `duplicate NPIs (ambiguous attribution)` | The provider→company crosswalk maps one billing number to two companies | The entity-graph build is stale relative to the spending file. Re-run the graph step, then retry. |
| `audience export contains forbidden columns` | Code tried to put person identifiers into a marketing-audience export | This is a guardrail doing its job. Do not bypass it — report it to whoever changed the code. |

## Common setup problems

**"required input missing: …provider_dim.parquet"** — the step you ran needs an
earlier step's output. Run the stages in the order in
[GETTING_STARTED.md](GETTING_STARTED.md) Part 3 (or `make pipeline` then
`make graph` then `make model-a`).

**"FileNotFoundError" pointing at `~/Desktop/data/...`** — the raw files aren't
where the pipeline looks. Either put them at the paths in the
[Data Runbook](platform/12-data-runbook.md), or tell the system where your data
root is: run with `MEDICAID_DATA_ROOT=/path/to/your/data` in front of the
command.

**"WARN file missing an employer column"** — that state's layoff file uses a
header we haven't seen. Open the CSV, find the employer-name column, and rename
that header to `COMPANY` (this is the one edit that's safe to make).

**A column of IDs looks like `1.003e+09` or lost its leading zeros** — the file
was opened and re-saved in **Excel**, which silently converts ID numbers.
Re-download the original; never open raw files in Excel (view them in a text
editor or load a copy).

**"Part B/D/DMEPOS file missing required columns …"** — you likely downloaded a
different dataset variant than the runbook names (e.g. the "by Provider"
summary instead of "by Provider and Service"). The error prints the headers it
saw — compare with the runbook entry and fetch the named variant.

## Things that look like errors but aren't

- **`(optional input absent) owner_edges`** — that source wasn't provided; the
  step continues without it. Fine for a partial run; the graph just has fewer
  edges.
- **`(cached) NPPES.parquet`** — the conversion was already done and is being
  reused. Saves time; not a problem.
- **`unresolved billing NPIs` in the exposure log** — some spending rows
  belong to providers not in the registry. They are *reported and kept aside*,
  never silently dropped; a small percentage is normal.
- **A huge number quarantined from `spending`** — expected: the raw national
  file genuinely contains millions of summary rows with blank IDs. The QA
  report shows exactly how much; the headline analysis was built on this fact.

## How to read a QA report when something failed

1. Open the stage's `QA_REPORT.md` (it's written even when the run fails).
2. Find the ❌ line — the failed check names the table and the counts.
3. The section above it shows the row counts per table — compare against the
   previous successful run (the git history of the report, or your notes).
4. The fix is almost always: identify which *input file* changed unexpectedly,
   restore/re-download it, re-run the stage.

## When to escalate

Escalate to an engineer (with the full error text and the QA report) when:
- the same assertion fails after re-downloading the input;
- the error is a Python traceback that doesn't match anything above;
- a guardrail assertion fired (forbidden columns, fraud-field check) — that is
  a code defect by definition, not a data problem.

Include: the exact command you ran, the full output, and which files you placed
where. The error messages are designed to carry the context an engineer needs.
