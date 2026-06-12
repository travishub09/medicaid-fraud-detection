# Trey's Build Plan

My side of the work, in order. This is the companion to the team email: the
downloads I'm coordinating, plus the pieces I'm building (Hugging Face models,
the Playwright collectors, the Kùzu graph viewer) and where each one plugs into
the platform that already exists.

Status legend: [ ] not started, [~] in progress, [x] done.

---

## Phase 0 — Unblock (this week)

Nothing I build matters until the data can flow and the team can reach it.

- [x] Push the platform build to `Trey-fork` (11 commits, 88 tests, CI).
- [ ] Travis: stand up the shared S3 bucket and add me as a user. (his task; I
      help on a screen share)
- [ ] Me: install AWS CLI locally, confirm I can `aws s3 sync` both directions.
- [ ] Me: set `MEDICAID_DATA_ROOT` to wherever I sync the data so the pipeline
      finds it (the code already reads this env var).

**Done when:** I can pull a file Travis uploaded and run `make test` clean.

---

## Phase 1 — First real-data run (week 1–2, gated on the core-five downloads)

The payoff milestone: the first real ranked target list. No new code, this is
running what's built against real files.

- [ ] Get the core five into the bucket (NPPES, LEIE, PECOS, owners, Medicaid
      spending) — see `docs/platform/12-data-runbook.md`.
- [ ] `make pipeline` → `make graph` → `make model-a` on real data.
- [ ] Read the first dossiers, sanity-check against `docs/READING_THE_OUTPUTS.md`.
- [ ] Note what breaks or looks wrong on real data — that list drives Phase 2.

**Done when:** a real `erv_ranked` list with dossiers exists, reviewed by a human.

---

## Phase 2 — Light up the Medicare detectors (week 2–3)

The adapters are already built and tested against the real file headers. This is
downloads plus one wiring step.

- [ ] Add Part B / Part D / DMEPOS / Open Payments to the bucket.
- [ ] Wire the `ingest_cms` adapters into the Model A feature build (they output
      the 0–1 percentile features the registry already expects; today they run
      standalone — connect them to `model_a` feature assembly).
- [ ] Re-run, confirm the upcoding / drug / DME / kickback schemes now fire.

**Done when:** dossiers show scheme hypotheses that need the Medicare files.

---

## Phase 3 — The Hugging Face models (my main build, week 2–5)

Two free models, both off the scoring path. The rule that governs both: **they
find and sort; the explainable math still decides the score.** Nothing here ever
becomes a fraud driver.

### 3a. Org-name matching (entity resolution v2)
- [ ] Pull `BAAI/bge-small-en-v1.5` (sentence embeddings, CPU, free).
- [ ] New module `src/entity_graph/name_embeddings.py`: embed org names, find
      near-duplicate candidates the exact-key resolver misses
      ("Sunrise Home Health LLC" ≈ "Sunrise HHC Services Inc").
- [ ] Feed candidates into the deterministic resolver as *suggestions* with a
      similarity score; auto-accept high, route the middle to human review.
      (This is the upgrade path already described in `03-entity-resolution.md`.)
- [ ] Later: graduate to Splink/Fellegi-Sunter for the probabilistic layer.

**Why it matters:** shell companies hide behind name variations. This catches
them at scale and strengthens every downstream org rollup.

### 3b. Grievance text classification (Model B2 input)
- [ ] Pull `MoritzLaurer/deberta-v3-base-zeroshot-v2.0` (zero-shot, free).
- [ ] New module `src/model_b/grievance_nlp.py`: classify review/forum text into
      "pressured to bill improperly / services not rendered" vs. ordinary
      complaint. Zero-shot = no training data needed.
- [ ] Output a per-employer grievance score that feeds the propensity layer
      (`propensity.py` already has the slot).

**Why it matters:** turns thousands of reviews into the handful of people most
likely to come forward.

**Gate:** 3b only runs on text we've lawfully collected (see Phase 4).

---

## Phase 4 — The collectors (Playwright + APIs, week 3–7)

Feed the grievance model and the departure-timing signal. New package
`src/collectors/`. Build in this order (easiest + lowest legal risk first).

- [ ] **Layoff trackers** (`layoffs_collector.py`): plain `requests`/BeautifulSoup
      against layoffs.fyi-style trackers. Pairs with the WARN monitor already
      built (`src/sourcing/warn_monitor.py`). No ToS issue.
- [ ] **Reddit** (`reddit_collector.py`): official API via `praw`. Sanctioned,
      free at our volume. Pull posts mentioning flagged employers.
- [ ] **Glassdoor** (`glassdoor_collector.py`): Playwright (real browser, handles
      JS + bot detection). **Gated:** Glassdoor ToS restricts scraping — counsel
      reviews the collection method before this runs in production.
- [ ] Each collector writes to a per-employer text table that `grievance_nlp.py`
      consumes; resolve employer names through the same `norm_org_name` key the
      graph uses, so collected text joins flagged orgs.

**Done when:** a flagged employer's layoffs + negative reviews surface together
as a "window open" signal alongside its ERV rank.

---

## Phase 5 — Kùzu graph viewer (week 4–6, parallel, low effort)

The interactive lens for investigating ownership networks. Optional to the
pipeline, valuable to a human analyst.

- [ ] `pip install kuzu` (free, embedded, nothing to buy/host).
- [ ] Build out `src/entity_graph/neo4j_export.py`'s sibling `kuzu_export.py`:
      load the node/edge parquet the graph build already produces into a local
      Kùzu database.
- [ ] Write 5–6 canned Cypher queries (within-2-hops-of-an-exclusion,
      common-owner clusters, shared-address shells) — the patterns
      `ring_detection.py` already computes, but clickable.
- [ ] Runs locally on the analysis machine, inherits the same security posture
      as the rest (no server to expose).

**Done when:** I can pull up any flagged org and walk its ownership network live.

---

## Phase 6 — DOJ case backfill (ongoing, anyone can help)

Not a build, but it's on my list to coordinate because it makes the scoring
smarter immediately.

- [ ] Collect healthcare FCA settlements from justice.gov press releases into a
      CSV (defendant, date, amount, sector) → `preclean/enforcement/doj_cases.csv`.
- [ ] The parser + sector-prior derivation are already built
      (`src/enforcement/`); 100+ rows replaces the placeholder risk weights with
      evidence-based ones.

**Done when:** `derive_sector_priors` runs on real cases and Model A uses them.

---

## Dependencies I'll add (all free, CPU-only, no GPU/hosting)

`sentence-transformers`, `transformers`, `torch` (CPU build), `praw`,
`playwright`, `beautifulsoup4`, `kuzu`. I'll pin these in a separate
`requirements-trey.txt` so they don't weigh down the core install until the
features land.

## Sequencing logic (why this order)

1. Data + first run first — everything else is guessing without it.
2. Name-matching model early — it improves every org rollup, so the sooner it's
   in, the better every later result.
3. Collectors before the grievance model is "on" — no point classifying text we
   haven't collected.
4. Kùzu and DOJ backfill run in parallel whenever; neither blocks anything.

## What stays gated (not mine to unblock)

- People-data vendor (Apollo test → PDL) — FCRA/license review first.
- Glassdoor collector in production — counsel on collection method.
- Anything customer-facing (lookup tool public launch, intake, ads) — Phase-0
  counsel sign-off.
