# Getting Started — Zero to Running

This guide assumes nothing: not familiarity with this repo, not healthcare
knowledge, not data-science background. Follow it top to bottom. If a term
confuses you, it's in the [GLOSSARY](GLOSSARY.md); if you want the *why* before
the *how*, read [HOW_IT_WORKS.md](HOW_IT_WORKS.md) first (10 minutes, no jargon).

## Part 1 — See it work in 10 minutes (no data needed)

The repo ships a tiny synthetic dataset with known fraud patterns planted in it,
so you can run the entire system end-to-end and verify it finds them — before
touching any real data.

**Prerequisites:** Python 3.11+ and git. Check with `python3 --version`.

```bash
# 1. Get the code
git clone https://github.com/travishub09/medicaid-fraud-detection.git
cd medicaid-fraud-detection

# 2. Install dependencies (one-time, ~2 minutes)
pip install -r requirements.txt

# 3. Prove everything works: the test suite plants fraud patterns in fake data
#    and verifies the system catches every one (and doesn't flag the clean orgs)
python3 -m pytest tests/ -v

# 4. Run the full scoring pipeline on the synthetic data
python3 -m src.model_a --fixture --out /tmp/demo --top-k 3
```

**What you should see:** lines like `[assert PASS] ...` (the system checks its
own math at every step and refuses to continue if anything is off), ending with
`Done — scored 12 orgs; wrote 3 dossiers`.

**Now look at what it found:**

```bash
cat /tmp/demo/MODEL_A_REPORT.md        # the ranked list
cat /tmp/demo/dossiers/001_*.md        # the #1 target's full dossier
```

The top-ranked organization is the planted "billing mill" (one clinic billing
$25M/year almost entirely through one service code). Its dossier shows: the
scheme hypothesis, every statistic that fired and why, the ownership/network
context, the dollar exposure — and a mandatory list of innocent explanations,
plus a disclaimer that this is an investigative hypothesis, not an accusation.
That dossier format **is the product**.

## Part 2 — What just happened (the 60-second version)

1. **Entity graph** (`src/entity_graph`): the fake providers were resolved into
   canonical organizations (two NPIs that are really one company got merged; two
   spellings of "Acme Health LLC" got merged), and the network was mapped —
   which orgs share addresses, share owners, or sit near a banned (excluded)
   party.
2. **Model A** (`src/model_a`): each organization was scored against the planted
   billing features, scheme by scheme; network red flags boosted the ring of
   companies controlled by an excluded owner; scores were multiplied by real
   dollars at stake to produce the ERV ranking.
3. **Dossiers**: the top targets were rendered as human-readable case files.

The full rationale is in [HOW_IT_WORKS.md](HOW_IT_WORKS.md); the technical specs
are in [platform/](platform/README.md).

## Part 3 — Run it on real data

Real data does NOT live in this repo (it's large and legally sensitive; the
`.gitignore` enforces that). It lives on your machine under `~/Desktop/data/`:

```
~/Desktop/data/
├── preclean/          ← raw downloaded files go here (inputs)
│   ├── Spending.csv      Medicaid provider spending
│   ├── NPPES.csv         the national provider registry
│   ├── PECOS.csv         Medicare enrollment records
│   ├── Caught.csv        the OIG exclusion list (LEIE)
│   └── owners/*.csv      CMS facility-ownership files (5 files)
├── processed/         ← created by the pipeline
├── features/          ← created by the pipeline
├── detection/         ← created by the pipeline
├── graph/             ← created by the entity-graph step
└── model_a/           ← created by the scoring step
```

**Where do the files come from?** Follow the click-by-click
[Data Runbook](platform/12-data-runbook.md) — it lists, for every file: the
exact website, what to download, what to name it, where to put it, and how to
check it worked.

**Then run the stages in order** (each one prints `[assert PASS]` checks and
writes a human-readable report next to its outputs):

```bash
# A. Integrate the raw files into clean, verified tables (~minutes to hours
#    depending on file sizes; run once per data refresh)
python3 -m src.attempt_2.ingest.integrate
python3 -m src.attempt_2.audit.diagnose_coverage
python3 -m src.attempt_2.audit.audit_corruption
python3 -m src.attempt_2.ingest.features

# B. Run the original 3-layer lead detection (per-provider, then per-company)
python3 -m src.attempt_2.leads.detect
python3 -m src.attempt_2.leads.verify_layer1
python3 -m src.attempt_2.leads.refine_layer2
python3 -m src.attempt_2.leads.refine_layer2_v3
python3 -m src.attempt_2.leads.company_rollup
python3 -m src.attempt_2.leads.company_lead_tracker --min-net-paid 10000000
python3 -m src.attempt_2.leads.finalize_tracker
python3 -m src.attempt_2.export.export_final_leads --min-net-paid 10000000

# C. Build the entity graph (organizations, owners, exclusions, networks)
python3 -m src.entity_graph --input ~/Desktop/data/processed --out ~/Desktop/data/graph

# D. Score organizations and render dossiers (with REAL dollars from spending)
python3 -m src.model_a \
    --graph-dir ~/Desktop/data/graph \
    --features <your company-features parquet> \
    --spending ~/Desktop/data/processed/spending_fact.parquet \
    --out ~/Desktop/data/model_a

# E. (Optional) Cross WARN layoff notices against the flagged orgs for
#    outreach-timing leads
python3 -m src.sourcing.warn_monitor --warn <state WARN csv> \
    --graph-dir ~/Desktop/data/graph \
    --erv ~/Desktop/data/model_a/erv_ranked.parquet \
    --out ~/Desktop/data/sourcing
```

**How you know each step worked:** every stage writes a `*_REPORT.md` /
`QA_REPORT.md` beside its outputs with row counts, dollar-conservation checks,
and pass/fail assertions. If a stage fails an assertion it stops loudly — that
is by design; never patch around a failed assertion.

## Part 4 — The documentation map

| You want… | Read… |
|---|---|
| The one-page briefing (what exists + next steps) | [WHAT_WAS_BUILT.md](WHAT_WAS_BUILT.md) |
| The plain-English rationale | [HOW_IT_WORKS.md](HOW_IT_WORKS.md) |
| How to read what it produces | [READING_THE_OUTPUTS.md](READING_THE_OUTPUTS.md) |
| An error / failed check | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| Every acronym defined | [GLOSSARY.md](GLOSSARY.md) |
| Click-by-click data acquisition | [platform/12-data-runbook.md](platform/12-data-runbook.md) |
| The big-picture architecture | [platform/ARCHITECTURE.md](platform/ARCHITECTURE.md) |
| What's built vs. planned, in order | [platform/ROADMAP.md](platform/ROADMAP.md) + [platform/GAPS.md](platform/GAPS.md) |
| Deep spec of any component | [platform/](platform/README.md) (numbered docs 01–11) |
| Every table and column | [../DATA_DICTIONARY.md](../DATA_DICTIONARY.md) |
| Rules for working on the code | [../CLAUDE.md](../CLAUDE.md) |
| The legal guardrails | [platform/01-legal-compliance.md](platform/01-legal-compliance.md) |

## Part 5 — The rules you must not break

1. **Never commit data.** No CSVs, no parquet, no patient anything. The
   `.gitignore` blocks it; don't force it.
2. **Never treat an output as an accusation.** Every lead is a hypothesis for
   human review. The dossiers say so; keep it that way.
3. **Never skip a failed assertion.** A failed check means the data or the code
   is wrong. Fix the cause.
4. **Never use restricted data.** Some datasets (T-MSIS research files,
   commercial claims products) are legally barred from this use. The
   [data catalog](platform/02-data-sources.md) marks them — they are off-limits
   even if you can technically obtain them.
5. **Never build person-level contact lists from the people signals.** Audiences
   and education only; individual outreach is gated by counsel.
