# What Was Built, and What Happens Next

The single briefing document. Written for a non-technical reader; every section
links to a deeper guide if you want more. Last updated at the end of the initial
platform build (9+ commits, 88 automated tests, all passing).

---

## 1. What this system is, in three sentences

It reads public government healthcare data to find organizations whose billing
and ownership patterns look like known fraud schemes, ranks them by how much
money is plausibly at stake, and writes a plain-English **case file (dossier)**
for each one. It is built to eventually find the *insiders* who witnessed the
fraud — because under the False Claims Act, a whistleblower with firsthand
knowledge can win 15–30% of what the government recovers. Every output is a
hypothesis for human review, never an accusation. (Full story:
[HOW_IT_WORKS.md](HOW_IT_WORKS.md).)

## 2. What was built (plain English, piece by piece)

| # | Piece | What it does for you | Status |
|---|---|---|---|
| 1 | **Data cleaning & integration** | Takes the giant raw government files, validates every provider ID, attributes every dollar to the right biller, and quarantines corrupt data instead of letting it poison the results. Found and walled off $20.7 trillion of fake values in the raw spending file. | Built, tested |
| 2 | **The original lead detector** | Three layers of checks — hard facts (billed while banned), statistical anomalies vs. true peers, and suspicious-ownership links — rolled up from individual billing numbers to whole companies. Validated against history: companies it ranked in the top 10% were **2× more likely** to later be banned by the government. | Built, tested |
| 3 | **The entity graph** | The "who is connected to whom" map: which billing numbers are really one company, who owns what, who shares an address, who sits near a banned party. Catches ring-shaped fraud invisible company-by-company. | Built, tested |
| 4 | **Model A — the scorer** | Combines everything into a ranked list by **expected recoverable value** (likelihood × dollars), with a named fraud-type hypothesis per organization. | Built (v1), tested |
| 5 | **Dossiers** | The product: a per-target case file with the evidence, the network context, the dollars, and a mandatory list of innocent explanations. | Built, tested |
| 6 | **Data adapters, ready and waiting** | Pre-built loaders for the five big Medicare datasets (Part B, Part D, medical equipment, Open Payments) keyed to the government's real file formats — download a file, drop it in, it works. | Built, tested; **awaiting your downloads** |
| 7 | **Enforcement case database** | Turns Department of Justice settlement announcements into structured data: which sectors get prosecuted, for how much. Replaces guesswork priors with evidence. | Built; **awaiting backfill** |
| 8 | **WARN layoff monitor** | Watches public mass-layoff notices and flags when a high-risk employer just had one — meaning potential witnesses are entering the reachable window. | Built, tested |
| 9 | **Model B — the witness mapper** | For a flagged organization: which job roles would have seen the suspected scheme, who was employed during the window, who is likely willing and reachable — output as advertising *audiences*, never contact lists (a legal guardrail enforced in the code itself). | Logic complete; **needs a people-data license to activate** |
| 10 | **Model C — case underwriting** | Will predict whether the government would take a case and what it could recover. | Skeleton only; needs case outcomes data |
| 11 | **Public lookup tool** | A website preview where anyone can look up a provider's billing percentile vs. peers — the future marketing front door. Deliberately shows context and caveats, never a "fraud" verdict. | Preview built; **public launch needs lawyer sign-off** |
| 12 | **Safety rails everywhere** | The system checks its own math at every step and stops rather than produce wrong numbers; person-identifying exports are structurally blocked; 88 automated tests re-verify all of it on every change. | Built |
| 13 | **Documentation** | 22 documents: plain-English explainer, getting-started, data runbook, how to read outputs, troubleshooting, glossary, plus full technical specs. | Done |

## 3. Get it running — the 15-minute proof (no data needed)

```bash
git clone https://github.com/travishub09/medicaid-fraud-detection.git
cd medicaid-fraud-detection
pip install -r requirements.txt
make test     # 88 checks against planted fraud patterns — all should pass
make demo     # runs the whole system on synthetic data, writes 3 dossiers
```
Then read `/tmp/demo/dossiers/001_*.md` — that file format is the product.
Full walkthrough: [GETTING_STARTED.md](GETTING_STARTED.md).

## 4. Populate it with real data — step by step

Everything goes in one folder on your machine: `~/Desktop/data/preclean/`.
The click-by-click instructions (exact website, exact file, exact name, how to
verify) are in the **[Data Runbook](platform/12-data-runbook.md)** — this is
the summary of what feeds what:

### Step 1 — The five core files (run the system end-to-end)
| File | Where | What it's used for |
|---|---|---|
| `NPPES.csv` | download.cms.gov/nppes | Who every provider IS (names, specialties, addresses) — the identity backbone |
| `Caught.csv` (LEIE) | oig.hhs.gov/exclusions | Who is BANNED from federal programs — the strongest red flag and our accuracy yardstick |
| `PECOS.csv` | data.cms.gov | Which billing numbers belong to the same enterprise |
| `owners/*.csv` | data.cms.gov ("All Owners") | Who OWNS each facility — feeds the network map |
| `Spending.csv` | your Medicaid data arrangement | Who billed what — the dollars everything is ranked by |

Then run, in order: `make pipeline` → `make graph` → `make model-a`.
Result: your first **real ranked dossier list**.

### Step 2 — The Medicare files (light up the dormant fraud detectors)
| File | Where | What it unlocks |
|---|---|---|
| Part B (by Provider & Service), 3 yrs | data.cms.gov | Upcoding, impossible-day, one-code-mill detection — and the lookup tool's data |
| Part D (by Provider & Drug), 3 yrs | data.cms.gov | Brand-steering and high-cost-drug schemes |
| DMEPOS (by Referring Provider), 2 yrs | data.cms.gov | Medical-equipment fraud (your focus area #2) |
| Open Payments, 3 yrs | openpaymentsdata.cms.gov | The kickback signal: drug-maker payments crossed with prescribing |
| Market Saturation | data.cms.gov | Where home-health/hospice over-supply concentrates (focus area #1) |
| SAM exclusions | sam.gov | Government-wide bans beyond the health list |

The loaders for ALL of these already exist and are tested against the real
file formats — these are downloads, not engineering.

### Step 3 — The free people-side signals
| File | Where | What it unlocks |
|---|---|---|
| WARN layoff notices (your top states) | each state's workforce site | "Witnesses just became reachable" timing alerts |
| DOJ settlements (hand-collected CSV, ~100 rows) | justice.gov/news | Evidence-based sector risk weights + future Model C training data |

### What's still missing (can't be downloaded — needs decisions)
| Missing | Why it matters | What it takes |
|---|---|---|
| **People/employment data** (e.g. People Data Labs) | Activates the witness mapper (Model B) — the heart of the business | A commercial license + a privacy/FCRA legal review **first** |
| **Court docket feed** (PACER/CourtListener) | First-to-file checks (existential for case value) + retaliation-suit leads | An account + a small fetcher build |
| **Case outcomes at scale** | Trains Model C (underwriting) | Grows from the DOJ backfill + time |
| **Never acquire:** T-MSIS research files, commercial claims products (Komodo/IQVIA etc.) | Their licenses legally bar this use | — (the system deliberately has no dependency on them) |

## 5. Next steps, in order

**This week (you):**
1. Grant repository write access → the 9 queued commits get pushed.
2. Confirm the automated checks go green on GitHub (they start automatically on push).
3. Download Step-1 files per the runbook → run the first real-data pass.
4. Start the DOJ backfill spreadsheet (even an afternoon's worth helps).

**This month (business, not code):**
5. Engage counsel (FCA + advertising + privacy) — the "Phase-0" sign-off that
   gates marketing, intake, and the lookup tool's public launch.
6. Decide on the people-data vendor + commission the FCRA/privacy review.

**Next engineering sprint (after the first real-data run):**
7. Tune from reality: the first real run will surface data quirks worth fixing —
   that's the trigger for the next coding session, not before.
8. Then: the person↔employer resolver (activates Model B), the docket monitor,
   the DOJ auto-fetcher, and Model C's cold-start rules.
   Full list with status: [platform/GAPS.md](platform/GAPS.md).

## 6. Post-push checklist (do once write access lands)

- [ ] `git push -u origin claude/happy-mccarthy-3skrn3` succeeds (9 commits)
- [ ] Also push the safety snapshot: `git push origin original-build pre-platform-snapshot`
- [ ] GitHub → Actions tab → the **tests** workflow runs and shows green
      (it runs the 88 tests + a full synthetic end-to-end + a doc-link check)
- [ ] Commits show **Verified** badges on GitHub
- [ ] Open a pull request into `main` when you're ready to review the whole build
