# GAPS — What Is Missing and Needs to Be Completed

The honest punch list, ordered by leverage. Data gaps are detailed separately in
[09-data-procurement.md](09-data-procurement.md); this covers everything else.

## Engineering (build next, roughly in order)

1. ~~**Model A ERV composite**~~ — **DONE (v1)**: scheme subscores → noisy-OR →
   sector prior × graph boost → ERV + target dossiers (`src/model_a/`, tested;
   `python -m src.model_a --fixture`). Remaining inside this item: feed *real*
   per-org annual payments from `spending_fact` (currently expects a `payments`
   column in the features input), and the WARN monitor's docket twin
   (`src/sourcing/docket_monitor.py`, stub).
2. **Part B / Part D / DMEPOS / Open Payments adapters** — ingestion modules following
   the `integrate.py` adapter contract (column map, VARCHAR, quarantine, assertions).
   Unblocks most of the Model A feature dictionary.
3. **DOJ/OIG case-database builder** — scraper/structurer for press releases,
   settlements, and CIAs into `enforcement/doj_cases.csv` (defendant, scheme, amount,
   intervention, jurisdiction). Feeds Model A labels *and* Model C cold-start.
4. **Dossier generator** — render a flagged org's full story (drivers, benign
   explanations, graph context, exposure) to Markdown/PDF. The product artifact
   counsel actually consumes; everything upstream exists.
5. **Temporal-holdout harness generalization** — extend `src/backtest/` from LEIE to
   enforcement-action labels with arbitrary cut years (`src/model_a/validation.py`).
6. **Person↔employer resolver** — Splink-based probabilistic linkage + temporal
   `employed_by` edges (`src/entity_graph/person_resolver.py`). *Gated on people-data
   license + FCRA review.*
7. **Public lookup tool** — FastAPI app behind Model A percentile features
   (`src/lookup_tool/`). Gated on Part B ingestion (its data) + Phase-0 sign-off
   (its legal frame).
8. **Tests for the attempt_2 core** — the 13-stage pipeline has assertions but zero
   unit tests; `tests/` covers only the entity graph. Add fixture-driven tests for
   `clean_data` normalizers, `integrate` joins, and v3 concept scoring.
9. **CI** — no `.github/workflows/`. Add: pytest on PR, plus a docs link-checker.
   (Blocked on repo write access; ready to add the moment we can push.)
10. **Orchestration** — a `Makefile` or single `run_all` entry point encoding the
    stage order (currently a README list); refresh scheduling later.
11. **Config hygiene** — `.env.example` exists but stages hardcode
    `~/Desktop/data` defaults; route through one config module.
12. **Label store** — a small, append-only outcomes table (case → outcome → date)
    that W6 retraining reads. Trivial now, priceless in year two.

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

- `attempt_1/` is dead code kept for reference — fine, but say so in its docstring.
- `sqlalchemy`/`psycopg2` in requirements are unused (presumably for the future API).
- The fixture's hardcoded normalizer outputs (`tests/fixtures/synthetic.py`) will
  drift if `_normalize_name` changes — acceptable, but the test failure will look
  like a resolver bug; a comment guards this.
- `git push` retry loop in session tooling misread a piped exit code once — pushes
  must check exit codes explicitly.
