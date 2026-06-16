# Setup Guide — Step by Step (for non-technical owners)

This is the "do this, then this" guide to get the platform running. It assumes
**no coding background**. You will copy and paste a few commands; that is all.
Where a step is genuinely technical, it says so and tells you to hand it to your
technical helper (Travis).

There are five phases. Do them in order. You can stop after any phase — each one
leaves you with something working.

- **Phase 1 — Prove it works** on a computer, using fake data. *(~30 min, no data needed.)*
- **Phase 2 — Set up safe storage** for the real files. *(Travis; ~1 hr.)*
- **Phase 3 — Get the core data files.** *(You + Travis; depends on downloads.)*
- **Phase 4 — Run it for real** and read your first results. *(~30 min.)*
- **Phase 5 — Expand** by adding more data files over time. *(Ongoing.)*

A note before you start: the project lives in a code repository on GitHub. You
already have access. "Running a command" means typing (or pasting) a line into a
black text window called a **Terminal** and pressing Enter. That is the only
skill you need.

---

## Phase 1 — Prove it works (fake data, ~30 minutes)

**Goal:** see the system run and produce an example case file, so you know the
machine works before you invest in data.

**Step 1.1 — Open a Terminal.**
- On a **Mac**: press `Cmd + Space`, type `Terminal`, press Enter.
- On **Windows**: install "Windows Terminal" from the Microsoft Store, or use
  "PowerShell" (search for it in the Start menu).

**Step 1.2 — Install the two free tools the project needs.** If these are already
installed, the commands will just say so.
- **Python 3.11 or newer** — download from python.org (the big yellow "Download"
  button), run the installer, and on Windows **check the box that says "Add
  Python to PATH."**
- **Git** — download from git-scm.com and run the installer with default options.
- *If this step is fiddly, this is the one place to ask Travis to do it once.*

**Step 1.3 — Download the project.** Paste this into the Terminal and press Enter:
```
git clone https://github.com/travishub09/medicaid-fraud-detection.git
cd medicaid-fraud-detection
```

**Step 1.4 — Install the project's parts.** Paste:
```
pip install -r requirements.txt
```
*(If `pip` is not found, try `pip3` instead. On a Mac you may need `python3 -m pip`.)*

**Step 1.5 — Run the self-check.** This plants known fraud patterns in fake data
and confirms the system catches every one:
```
python3 -m pytest tests/ -q
```
**Success looks like:** a line ending in something like `220 passed`. If you see
that, the machine works.

**Step 1.6 — Run the demo and read a case file.** Paste:
```
python3 -m src.model_a --fixture --out demo_output --top-k 3
```
Then open the folder `demo_output/dossiers/` (it is inside the project folder)
and open the file beginning with `001_`. **That file is the product** — a
plain-English case file. To also see the financing side:
```
python3 -m src.model_c --fixture --out demo_underwriting
```
and open `demo_underwriting/memos/`.

You have now seen the whole system work. Everything from here is feeding it real
data.

---

## Phase 2 — Set up safe storage (Travis; ~1 hour)

**Goal:** a secure, shared place to keep the real data files, which can be large
and sensitive.

This phase is technical and belongs to Travis. In plain terms, he will:
1. Create a secure cloud storage bucket (**Amazon S3**) **with a healthcare data
   agreement (a "BAA")** in place — this is required because some files are
   sensitive.
2. Give you (Trey) access to upload and download from it.

**Important rule, no exceptions:** sensitive data and the raw files **never** go
into the GitHub code repository. They live only in the secure storage. The code
and the data stay separate. (The project is built to enforce this.)

If you do not yet have any sensitive data, you can still proceed with the free
public files in Phase 3 and add storage later — but set it up before handling
anything sensitive.

---

## Phase 3 — Get the core data files

**Goal:** the five files that let the system run end-to-end on real data.

The **exact website, file, filename, and how to check it** for every file is in
the **[Data Runbook](platform/12-data-runbook.md)** — follow that for the clicks.
This is just the checklist of what to get and why. All five except the last are
free public downloads.

Put every file in a folder named `preclean` (the runbook says exactly where).

| # | File | Free? | Why it's needed |
|---|---|---|---|
| 1 | **NPPES** (provider registry) | Free | The identity backbone — who every provider is |
| 2 | **LEIE** (exclusion list) | Free | Who is banned from federal programs — the strongest red flag |
| 3 | **PECOS** (enrollment) | Free | Which billing numbers belong to one company |
| 4 | **CMS All-Owners** | Free | Who owns each facility — the network map |
| 5 | **Medicaid Spending** | From your data arrangement | Who billed what — the dollars everything is ranked by |

When all five are in place, you are ready for Phase 4.

---

## Phase 4 — Run it for real (~30 minutes)

**Goal:** your first real ranked list of suspect organizations and their case
files.

In the Terminal, inside the project folder, run these three commands **in order**
(each may take a few minutes depending on file sizes):
```
make pipeline     # cleans and integrates the raw files
make graph        # builds the "who's connected to whom" map
make model-a      # scores every organization and writes the dossiers
```
*(If `make` is not available on Windows, the runbook lists the plain commands to
run instead. This is a good moment to involve Travis if needed.)*

**The result:** a ranked list and a folder of dossiers. Open the highest-ranked
ones. **[READING_THE_OUTPUTS.md](READING_THE_OUTPUTS.md)** explains every line of
a dossier in plain English — read it alongside your first few.

Remember: each dossier is a **hypothesis for review**, with innocent explanations
listed. It is a lead to investigate, not a conclusion.

---

## Phase 5 — Expand over time

**Goal:** switch on more fraud detectors and the live monitors as you procure more
data. Nothing here requires new engineering — each file activates its detector
automatically.

1. **Add the Medicare files** (Part B, Part D, medical-equipment, Open Payments,
   Market Saturation, SAM) — all free. Re-run `make model-a`. More detectors and
   the public lookup-tool data switch on. *(Runbook Tier 1.)*
2. **Do the two free signups** (CourtListener token, SAM key) to turn on the live
   court-docket and exclusion feeds. *(Runbook Tier 1b.)*
3. **Add the extra free files** (opioid, 340B, facility staffing, and more) as you
   go — each one lights up its named detector. *(Runbook Tier 2.)*
4. **Run Model C** on your ranked list for the financing view:
   ```
   make model-c
   ```

For the full, prioritized data shopping list and what each file unlocks, see
**[WHAT_WAS_BUILT.md](WHAT_WAS_BUILT.md)** Section 4.

---

## Who does what

| Person | Role |
|---|---|
| **You (Trey)** | Run the commands, procure the data files, read the dossiers, drive the business |
| **Travis** | Secure storage (S3 + BAA), any tricky install steps, the large-file plumbing |
| **Counsel** | The Phase-0 legal sign-off (False Claims Act + advertising + privacy) that gates marketing, intake, and the public tool |
| **The system** | Does the detection, scoring, and underwriting — and refuses to produce a number it can't stand behind |

---

## If something goes wrong

- The system is designed to **stop and tell you** rather than produce a wrong
  answer. An error message that mentions an "assertion" usually means a data file
  is not in the expected shape — re-check it against the runbook's "Verify" line.
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** covers the common issues in plain
  English.
- When in doubt, send Travis the exact command you ran and the full message you
  got back — that is almost always enough to diagnose it.

---

## What this gets you, and what's next

After Phase 4 you have a working detection engine producing real, ranked,
explainable case files. After Phase 5 you have the full detector library and the
live monitors running.

The remaining pieces — activating the witness engine (Model B) and launching the
public tool and marketing — are **not more setup steps**. They are business and
legal decisions: a people-data license with a privacy review, and counsel
sign-off. Those are described in **[CONSULTING_DELIVERABLE.md](CONSULTING_DELIVERABLE.md)**,
the companion overview of everything that was built.
