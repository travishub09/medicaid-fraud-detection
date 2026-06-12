# 13 — API Feeds (the event layer)

The bulk files stay primary for full-universe statistics (peer percentiles need
the whole file anyway). These APIs deliver the *events* — enforcement actions,
dockets, exclusions — plus targeted lookups. All built, all tested on canned
responses (no live network in CI); live runs happen on the analysis server via
`make feeds-backfill` (once) and `make feeds-refresh` (cron, weekly).

## Built feeds

| Feed | Endpoint | Auth | Module | Feeds |
|---|---|---|---|---|
| DOJ press releases | justice.gov/api/v1/press_releases.json (50/page) | none | `src/enforcement/fetch.py` | case DB → **evidence-based sector priors**, Model A labels, Model C cold-start, label store |
| CourtListener dockets | courtlistener.com/api/rest/v4/dockets/ (NOS 376 qui tam; NOS 442/445 retaliation) | free token → `COURTLISTENER_TOKEN` | `src/sourcing/docket_monitor.py` | **first-to-file alerts** (existential for Model C), outcome label candidates, Model B2 grievance events (org-level only) |
| SAM exclusions | api.sam.gov/entity-information/v4/exclusions | free key → `SAM_API_KEY` | `src/enforcement/sam_api.py` | exclusion nodes alongside LEIE (concat before the graph build) |
| NPPES registry | npiregistry.cms.hhs.gov/api (v2.1) | none | `src/ingest_cms/nppes_api.py` | on-demand single-NPI freshness checks (dossier review, lookup tool) — NOT a bulk replacement |
| CMS catalog probe | data.cms.gov/data.json | none | `src/feeds/freshness.py` | tells us when a new bulk vintage drops (Part B/D, DMEPOS, Open Payments, Saturation) |

## Shared plumbing (`src/feeds/`)

- `client.py` — retry/backoff transport; **every raw response cached** under
  `MEDICAID_DATA_ROOT/feeds/raw/<source>/<date>/` (provenance for counsel,
  replayable re-parse). Transport is injectable → tests run on canned JSON.
- `state.py` — per-feed incremental cursors in `feeds/state.json`; cron runs
  fetch deltas only; backfills bypass the cursor explicitly.

## Setup (two free signups)

1. CourtListener: create an account at courtlistener.com → profile → API token
   → put in `.env` as `COURTLISTENER_TOKEN`.
2. SAM.gov: sign in → Account Details → request a public API key →
   `SAM_API_KEY` in `.env`.

## DOJ feed notes

- Filters client-side on "False Claims Act" in title+body (robust to API
  quirks); strips HTML; extracts the defendant from the two dominant headline
  shapes ("X to Pay $Y…", "X Settles…") — unmatched titles keep an empty
  defendant for human fill-in, never a guess.
- Existing case rows are immutable on re-run (the case DB dedupes on case_id);
  settled-with-dollars cases append to the label store as `settled` outcomes.
- Each run re-derives sector priors and writes `FEEDS_REPORT.md` with counts.

## CourtListener feed notes

- Defendant parsed from the case caption (text after " v. "), resolved against
  the canonical org names + aliases with the same key as the WARN matcher;
  ambiguous keys are flagged, unmatched dockets are **kept** (a first-to-file
  conflict matters before we know the org).
- Alerts mark `in_top_targets` when the defendant sits in the top fraction of
  the ERV ranking — those are the never-miss notifications.
- Retaliation events are org-level only; no person identification (guardrails
  in 05-model-b.md unchanged).

## Deferred (cost money — Brad decisions, schema-ready when licensed)

| Source | What it adds | Cost model |
|---|---|---|
| PACER Case Locator | sealed-case coverage CourtListener lacks | per-query fees |
| UniCourt / Docket Alarm | normalized parties/attorneys (better entity resolution) | commercial API |
| Violation Tracker (Good Jobs First) | pre-aggregated FCA settlements **with parent-company rollups** — drops into our case-DB schema | bulk license |

No API (manual/scrape later, terms review first): HHS OIG enforcement pages,
Bass Berry settlement DB, Gibson Dunn updates, DOJ fraud-stats XLSX.
