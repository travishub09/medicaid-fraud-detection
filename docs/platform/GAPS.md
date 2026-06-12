# GAPS — What Is Missing and Needs to Be Completed

The honest punch list, ordered by leverage. Data gaps are detailed separately in
[09-data-procurement.md](09-data-procurement.md); this covers everything else.

## Engineering (build next, roughly in order)

1. ~~**Model A ERV composite**~~ — **DONE (v1)**: scheme subscores → noisy-OR →
   sector prior × graph boost → ERV + target dossiers (`src/model_a/`, tested;
   `python -m src.model_a --fixture`). Real per-org annual payments now flow
   from spending via `src/model_a/exposure.py` (`--spending` flag; dollar
   conservation asserted). Remaining: the docket twin
   (`src/sourcing/docket_monitor.py`, stub).
2. ~~**Part B / Part D / DMEPOS adapters**~~ — **DONE** (`src/ingest_cms/`):
   real-PUF-header column maps, NPI quarantine, per-NPI metrics → one-sided peer
   percentiles → org rollup; tested against the published header names so the
   downloads drop in unmodified. Open Payments adapter + kickback co-occurrence now ALSO done
   (`src/ingest_cms/openpayments.py`, incl. `pays` edges). Remaining: run
   against the real files once procured.
3. ~~**DOJ/OIG case-database builder**~~ — **DONE (parse/build/derive)**
   (`src/enforcement/`): press-release parser (amount, sector, scheme, qui tam,
   intervention, jurisdiction), validated case schema with the graph join key, and
   `derive_sector_priors` that replaces the placeholder multipliers (wired into
   `sector_priors.sector_prior_series(priors=...)`). Remaining: the live fetcher
   (`fetch.py`, stub — needs network + scraping review) and the 10-year backfill.
4. **Dossier generator** — render a flagged org's full story (drivers, benign
   explanations, graph context, exposure) to Markdown/PDF. The product artifact
   counsel actually consumes; everything upstream exists.
5. ~~**Temporal-holdout harness generalization**~~ — **DONE**
   (`src/model_a/validation.py`): precision@k + lift vs. baseline on arbitrary
   cut dates and outcome tables; `outcomes_from_case_db` joins DOJ cases to the
   graph by name key. Run it for real once Model A scores a real universe.
6. **Person↔employer resolver** — Splink-based probabilistic linkage + temporal
   `employed_by` edges (`src/entity_graph/person_resolver.py`). *Gated on people-data
   license + FCRA review.* NOTE: Model B's scoring chain is now LOGIC-COMPLETE
   (`src/model_b/` — knowledge gate, propensity + distress review flag,
   reachability, audience roll-up with the no-identifier tripwire) and tested on
   synthetic people; this resolver is the only missing piece to activate it.
7. **Public lookup tool** — **v1 PREVIEW BUILT** (`src/lookup_tool/app.py`):
   FastAPI over a percentile parquet; plain-language risk cards with drivers,
   benign explanations, disclaimer, and structurally no fraud field (tested).
   PUBLIC launch remains gated on Part B data + Phase-0 sign-off; binds to
   localhost by default.
8. **Tests for the attempt_2 core** — **MOSTLY DONE**: shared core covered
   (`tests/test_clean_data.py`); `integrate.py` now covered end-to-end on a
   raw-shaped five-source fixture (`tests/fixtures/raw_sources.py`,
   `tests/test_integrate.py`: dedup determinism, type-match collapse,
   active_at_claim truth table, quarantine routing, PAC-ambiguity guard,
   two-tier owner↔LEIE matching, dollar conservation). Remaining: v3 concept
   scoring (`refine_layer2_v3`) fixture tests.
9. ~~**CI**~~ — **DONE** (`.github/workflows/tests.yml`): pytest + fixture
   end-to-end + doc-link check on every push/PR. Activates when the branch
   reaches GitHub.
10. ~~**Orchestration**~~ — **DONE** (`Makefile`): `make demo|test|pipeline|
    graph|model-a|warn|ci-local`; refresh scheduling later.
11. ~~**Config hygiene**~~ — **DONE (root)**: `MEDICAID_DATA_ROOT` env var now
    controls the data root (`clean_data.DATA_ROOT`/`PRECLEAN_DIR`, tested);
    per-stage CLI flags still override. Full config module deferred until a
    second knob actually exists.
12. ~~**Label store**~~ — **DONE** (`src/enforcement/label_store.py`):
    append-only (existing label_ids immutable), validated outcomes vocabulary,
    `outcomes_for_validation` feeds the holdout harness directly. Lives under
    `MEDICAID_DATA_ROOT/labels/` — runtime data, never in git.

## Modeling / data-science

13. **Sector priors + scheme recovery multipliers** — constants for the ERV formula,
    derived from the DOJ case DB (#3); documented, not hardcoded magic numbers.
14. **Exposure model** — annual program payments per org (we have Medicaid spending;
    Part B adds Medicare) × scheme multiplier; later a quantile model.
15. **Case-mix / acuity controls** — referral-center and subspecialty flags before
    Model A graduates to supervised (the false-positive trap).
16. **Model B propensity calibration plan** — define the engagement proxy label and
    the measurement design *before* campaigns launch, or the data is unusable.
17. **Model C survivorship-bias handling** — track the full intake→filed→outcome
    funnel from day one (the public record only shows wins).

## Legal / compliance / operations (external actions — not code)

18. **Phase-0 counsel engagement** — FCA, advertising, privacy, litigation-finance
    counsel; approved compliance playbook (01-legal-compliance.md is the brief, not
    the sign-off). *Blocks Phases 4–6.*
19. **People-data licensing + FCRA scope review** — blocks Model B entirely.
20. **Repo write access** — `treyrawles` needs Write on
    `travishub09/medicaid-fraud-detection` (or the Claude GitHub App authorized);
    one commit is waiting locally.
21. **Entity formation / venue strategy** — the Zafirov hedge (state-FCA emphasis,
    circuit selection) is a business decision the docs flag but cannot make.
22. **Partner FCA firm + privileged-intake design** — required before any lead
    touches case facts.
23. **Data-governance policy** — retention/deletion schedules, access controls for
    the sensitive Model B inference, consent + privacy policy for the funnel.

## Product / go-to-market (Phases 4–5; spec'd in 07, not built)

24. Content hub + 4 typology landing pages (HCC/MA, DME, hospice, home care).
25. CRM + nurture sequences + secure intake forms (no-PHI design).
26. Analytics instrumentation (event stream, lead scoring, attribution).
27. Lookup-tool SEO architecture (it is the acquisition engine, not a side tool).

## Known technical debt

- Adversarial bug hunt (round 2) fixed: stale input columns overriding the
  computed ERV ranking, "$3M" parsed as $3 in the case DB, NaN dollars in
  dossiers, silent ambiguous WARN name matches — regression-locked in
  `tests/test_round2_fixes.py` (+ the integrate.py core test suite).
- Adversarial bug hunt (round 1) fixed: betweenness scaling, mega-address edge
  explosion, silent duplicate-crosswalk attribution, unicode alias misses —
  regression-locked in `tests/test_edge_cases.py`. Residual known limit: the
  shared `_normalize_name` in attempt_2 does NOT unicode-fold (changing it
  would shift the production pipeline's name keys; fold it when the pipeline
  is next re-run end-to-end on real data).

- `attempt_1/` is dead code kept for reference — fine, but say so in its docstring.
- `sqlalchemy`/`psycopg2` in requirements are unused (presumably for the future API).
- The fixture's hardcoded normalizer outputs (`tests/fixtures/synthetic.py`) will
  drift if `_normalize_name` changes — acceptable, but the test failure will look
  like a resolver bug; a comment guards this.
- `git push` retry loop in session tooling misread a piped exit code once — pushes
  must check exit codes explicitly.
