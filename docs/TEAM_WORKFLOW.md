# Team workflow: several people, one codebase, one model

Written 2026-11-02 when the team grew to an operator (Trey), a technical analyst who writes
code (Daschel), a reader who asks the model questions (Michelle) and counsel (Brad). The
rules below are the ones that keep two people from editing over each other, keep the VM
and the data safe, and keep every change to model behaviour visible.

## 1. One rule above the others: every change goes through a branch and a pull request

Nobody pushes to `main` or to the integration branch directly. Each person works on a
branch named `<person>/<topic>` (Claude Code sessions make `claude/<session>` branches,
which is the same thing), pushes that branch, and opens a pull request into the
integration branch (`claude/happy-mccarthy-3skrn3` today; `main` once it is folded in).
The pull request runs the full test suite (`.github/workflows/tests.yml`) and the docs
link check; a red suite cannot merge. Two people editing the same lines produce a merge
conflict in the pull request, which Git shows and a human resolves; they never overwrite
each other silently. GitHub settings to turn on (repository Settings, Branches, Add rule,
for `main` and the integration branch): require a pull request before merging, require
status checks to pass (`tests / pytest`, `tests / docs-links`), require one approval,
restrict who can push (the operator only), and do not allow force pushes.

## 2. Who owns what (`.github/CODEOWNERS`)

A code owner is asked to review every pull request that touches their files; the file is
the map of the codebase's risk:

- the label chain, the runners, the export, the registry and the diagnostics (anything
  that changes what a run means) are owned by the operator; a change there is a new
  pre-registration, never a silent swap (CLAUDE.md, "release gating");
- the scheme adapters, the theory screens and the analytics are open to the technical
  analyst with the operator's review;
- the docs are open to everyone;
- `docs/reminders.json` is the operator's reminder memory and is edited only by the
  operator's sessions (others open an issue).

## 3. Each person has their own Claude Code session and their own clone

Claude Code sessions do not share state: each person runs their own (web, desktop or
terminal) against their own GitHub connection and their own clone or cloud container. The
shared memory between sessions is `CLAUDE.md`, which every session reads first, so the
rules travel with the repository, not with a person. Practical rules:

- one session per branch; never two people on one branch at the same time;
- a session that is about to change the label chain, a runner or the export says so in
  its pull request title ("v-semantics: ...") so the reviewer knows a pre-registration
  follows;
- a session never runs `git pull` on the VM while a model run is going (the VM rule);
- the VM is single-user: one model run at a time, started and read by the operator's
  session; anyone else who needs a run opens an issue titled "run request" with the
  flags, and the operator queues it. A second VM for the analyst is about six dollars a
  run if the queue becomes the bottleneck.

## 4. The technical analyst (writes code)

- Works on `daschel/<topic>` branches in their own clone or cloud session; every test
  runs on synthetic fixtures (`tests/fixtures/synthetic.py`), so no real data is needed
  to develop or to pass CI.
- Real-data checks run on the VM through the operator's queue; the analyst reads the
  run packet (`SOURCES_REPORT.md`, `EXPECTATIONS_REPORT.md`, `SCHEME_HEALTH`, the
  diagnostics) from OneDrive, never by logging into the VM.
- Reads `docs/RUNBOOK_TRAVIS.md` (the metric catalog) and `docs/ARCHITECTURE_AUDIT_2026-11.md`
  before touching a scheme; proposes a scheme change as a pull request with its test and,
  where it changes a subscore, the arm that grades it.
- Data files never go in git (`*.csv`, `*.parquet`, `*.xlsx` are ignored; a test refuses
  any tracked one); work product under `enforcement/` never leaves the data folder.

## 5. The reader (asks questions, does not write code)

- A Claude Code session with read rights only (GitHub role: Read; permission mode: plan
  or read-only), pointed at the docs: `docs/WHAT_WAS_BUILT.md`, `docs/HOW_IT_WORKS.md`,
  `docs/READING_THE_OUTPUTS.md`, `docs/GLOSSARY.md`, the read-outs under `docs/`, and the
  run packets on OneDrive. That session can answer "what does this score mean", "why is
  this provider on the list", "what did v27 find", from the files, with no risk to the
  code.
- Questions that need the live data ("did we ever see this company", the code-mix line)
  go to the operator's session, which runs `org_find` on the VM; the answer comes back as
  a file in the shared folder.
- A simpler path for the same reader is a Claude Project loaded with the same docs and
  the latest packets, refreshed by the operator after each run.

## 6. Counsel and investigators

They receive packets and handoffs as files in the shared folder (never git); they do not
need repository access. Field results come back as one command per card
(`field_outcomes --add`), run by the operator.

## 7. The data folder

The OneDrive data root is shared; writes are limited to the folders a task needs
(`preclean\` for downloads, `preclean\state_capacity\` for rosters,
`enforcement\ohio\` and `enforcement\pi_reports\<case>\` for case work). Everything under
`enforcement\` is attorney work product: share with named people, never a group link.
Never open a CSV in Excel and save it back (leading zeros are lost).

## 8. The pull request template

`.github/pull_request_template.md` asks three things of every change: what it changes,
whether it changes what a run means (then the pre-registration that follows), and which
tests cover it. A pull request without a test for new behaviour is sent back.
