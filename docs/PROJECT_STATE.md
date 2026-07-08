# PROJECT_STATE.md — checkpoint

_Master checkpoint for the Healthcare Fraud Whistleblower Origination platform.
Written 2026-07-08. Keep a copy at `C:\Users\treyr\OneDrive\Desktop\data\PROJECT_STATE.md`
and the version-controlled copy at `docs/PROJECT_STATE.md` (travels in every git bundle)._

---

## 1. Objective

An intelligence-and-acquisition engine for qui tam (False Claims Act) healthcare-fraud
cases. Public data finds **where** fraud concentrates (Model A), people/role data finds
**who** plausibly witnessed it (Model B), and an underwriting model decides **which** cases
are worth financing (Model C). Operating thesis, never violated: public data produces
**investigative leads and marketing audiences for human/counsel review — never accusations**.
The human insider (relator) is the asset. Business goal: qui tam cases worth **>$5M recovery**.

People: **Trey** (owner/operator), **Travis Waters** (supervised modeler), **Brad** (funder;
owns money/licensing decisions).

## 2. Machine + data root (IMPORTANT)

- Data lives at **`C:\Users\treyr\OneDrive\Desktop\data`** (OneDrive-redirected Desktop).
  Code and docs say `~/Desktop/data`; on this machine that resolves to the OneDrive path.
  **Set `MEDICAID_DATA_ROOT=C:\Users\treyr\OneDrive\Desktop\data`** (or pass `--data-root`)
  so scripts find it. This bit us once — `Path.home()/"Desktop"` ≠ the OneDrive Desktop.
- Code is delivered by **git bundle** (network policy blocks direct push). Latest bundle
  contains branch `claude/happy-mccarthy-3skrn3` at ~165 commits ahead of GitHub. To load:
  `git fetch <bundle> claude/happy-mccarthy-3skrn3:claude/happy-mccarthy-3skrn3` then push.

## 3. Source + output file paths (verified this session via pi_locate.py)

| Path (under the data root) | What it is |
|---|---|
| `preclean\Spending.csv` | RAW Medicaid claims — the core input (238M rows: billing NPI, servicing NPI, HCPCS, month, patients, claim lines, paid) |
| `preclean\NPPES.csv` | RAW national provider registry |
| `processed\spending_fact.parquet` | the built spending fact (billing_npi × service_month × hcpcs_code × total_paid …) |
| `processed\provider_dim.parquet` | per-NPI dimension (taxonomy, entity_type, addr_*, names) |
| `features\spending_provider_base.parquet` | per-NPI feature base (ratios, concentration, temporal) |
| `detection\fraud_leads_v3.parquet` | v3 concept leads (per-NPI concepts + priority tier + label) |
| `graph\nodes\exclusion_nodes.parquet` | exclusion nodes (LEIE + revocations + …), npi + excl_date |
| `graph\` (nodes/edges/npi_to_org.parquet) | the entity-resolution graph |
| `model_a\provider_features\provider_features_for_model.parquet` | **THE training matrix** (617,062 × 111) sent to Travis |
| `model_a\provider_features\provider_scored.parquet` | same + names/state + anomaly_score/pct/signals_tripped |
| `model_a\provider_features\feature_manifest.json` | the column contract (label/leakage/families/groups) |

## 4. Training-matrix schema (feature_manifest.json keys)

- **label**: `provider_on_exclusion` (PU positive; v1 = 1,943 positives). Also `provider_on_leie`.
- **leakage_hard** (NEVER train): `billed_after_exclusion`, `excluded_after_billing`,
  `provider_on_leie`, `provider_on_exclusion`, the smoking-gun timeline columns, the
  label-widening flags.
- **leakage_adjacent** (out-of-time split only): `within_2_hops_of_exclusion`, `shell_score`,
  `related_party_density`, `subscore_ownership_integrity`, `has_excluded_owner`,
  `graph_fraud_proximity`.
- **raw_feature_cols / peerpct_cols / subscore_cols**: the trainable families.
  `*__peerpct` = one-sided taxonomy-peer percentile. `subscore_<scheme>` = 0–1 rules score.
- **identifier_cols**: npi, org_node_id, entity_type, primary_taxonomy, practice_state,
  org_legal_name (+ provider_name/addr_city/addr_zip added this session).
- **group_cols**: `group_id` (group-aware CV). **assessability**: `assessable`.
- **NULL means "absent from that source," not zero.** Do not `fillna(0)`.

## 5. Scheme catalog (15 subscores; the `subscore_<scheme>` family)

single_service_mill, payment_outlier, overutilization, specialty_mismatch, rapid_ramp,
ownership_integrity (leakage-adjacent), upcoding, pharma_kickback, drug_outlier, pill_mill,
worthless_services, hospice_ineligibility, saturation_fraud, **nemt_fraud** (new),
**behavioral_health** (new); plus dormant impossible_day, contract_pharmacy, invalid_identity,
cost_report_fraud, drug_spread_anomaly, dme_ring. Exposure basis per scheme in
`sector_priors.SCHEME_EXPOSURE_BASIS` (own_billing / influenced_dollars / ring_aggregate /
facility_program) — see `docs/OUTPUT_METHODOLOGY.md`.

## 6. Scripts created this session

**Repo modules (in the bundle, version-controlled):**
- `src/model_a/expectations.py` — per-run calc-integrity report (EXPECTATIONS_REPORT.md).
- `src/model_a/retrospective.py` — negatives/features/split ablation grid (RETRO_REPORT.md).
- `src/model_a/lead_gate.py` — scheme-aware recovery gate (+ influenced-dollars exposure).
- `src/model_a/lead_export.py` — ranked+gated lead lists (no size cut; dollars never rank).
- `src/model_a/signal_ranking.py` — per-feature AUC×coverage (coverage-honest).
- `src/model_a/verify_training.py` — audit a trained model (leakage/subscore/split checks).
- `src/model_a/smoking_gun_timeline.py` — dated timelines for billed-after-ban/deactivation.
- `src/model_a/expected_billing.py` — added `volume_residual` (the exogenous-capacity twin).
- `src/model_a/state_fca.py` — State-FCA case-value overlay (Ohio has none; CA/NY/TX/FL do).
- `src/model_a/identity_flags.py` — ghost-NPI report (billing NPIs absent from NPPES).
- `src/model_a/medicare_export.py` — the Medicare-grain training matrix runner.
- `src/model_a/prospective_label.py` — the FORWARD label for Travis's network test
  (first-ban-on/after-cutoff → `future_bans_after_<cutoff>.csv`); `make frozen-package`
  chains the as-of graph + as-of matrix + this label into one leakage-correct package.
  Protocol for Travis in `docs/FROZEN_PACKAGE_FOR_TRAVIS.md`.
- `src/ingest_cms/medicare_fact.py` + `medicare_growth.py` — Medicare fact + multi-year ramp.
- `src/ingest_cms/sector_schemes.py` — NEMT / behavioral-health / impossible-day from the fact.
- `src/enforcement/cms_priority_lists.py` — moratoria / revalidation / SFF overlay flags.

**Local helper scripts (Downloads, NOT in the repo):**
- `pi_extract3.py` — pulls PI case-file data (identity, banned-party links, billing series,
  drivers) for 6 candidate NPIs → `pi_out.txt`. (v1/v2 assumed the wrong data root.)
- `pi_locate.py` — finds the data root (how we found the OneDrive path).
- `scheme_examples.py` — top real provider per scheme from the scored parquet.

**Deliverables (scratchpad → sent as PDFs): Brad briefing, Travis notes, output methodology,
scheme audit, model retrospective, phase plans. Not in the repo.**

## 7. Key transformations + modeling decisions

- **Fact build**: Spending.csv → spending_fact (all-VARCHAR first; identifiers stay strings;
  dollar conservation + no-fanout asserted on every join).
- **v3 concepts** (`attempt_2/leads/refine_layer2_v3.py`): volume-gated, de-correlated,
  peer-percentile-ranked; **concept re-rank fix** this session (the max-of-percentiles bias).
- **Subscores**: NULL-aware weighted mean (the zero-imputation floor bug is FIXED); subscores
  are for explainability, model should train on raw + `__peerpct`.
- **Size as a FILTER, not a ranking variable** (§A): leads rank on size-adjusted anomaly;
  dollars gate the OUTPUT via the scheme-aware `lead_gate`, never the rank.
- **Digital twins**: `billing_residual` (price) + `volume_residual` (volume vs exogenous
  capacity — catches the inflation the price twin launders).
- **PU framing**: 0 ≠ clean; `confirmed_clean` anchors; widened multi-source label
  (LEIE + revocations + SAM + OpenSanctions + deactivation/death) with provenance.
- **CMS suppression**: blanks kept as NaN (opioid/partb), never false zeros.

## 8. Commands to rerun everything (from the data root; see docs/RUNBOOK_TREY.md §7)

```
set MEDICAID_DATA_ROOT=C:\Users\treyr\OneDrive\Desktop\data
pip install openpyxl                          # unlocks 340B + deactivation

python -m src.preflight --data-root %MEDICAID_DATA_ROOT%           # what's present
python -m src.enforcement.opensanctions --in preclean\opensanctions\targets.simple.csv ^
    --out processed\exclusions_opensanctions.parquet               # free bulk CSV
python -m src.attempt_2.ingest.integrate                           # fact
python -m src.entity_graph --input processed --out graph           # graph
python -m src.model_a.provider_features_export                     # THE matrix (+ EXPECTATIONS_REPORT)

python -m src.model_a.signal_ranking  --matrix ...\provider_features_for_model.parquet ^
    --manifest ...\feature_manifest.json --out signal_ranking.csv
python -m src.model_a.lead_export     --matrix ...\provider_features_for_model.parquet --out-dir detection\leads
python -m src.model_a.identity_flags  --spending processed\spending_fact.parquet ^
    --provider-dim processed\provider_dim.parquet --out ghost_npis.csv
```

**Point-in-time freeze for Travis's network test (2023-12):**
```
python -m src.entity_graph --input processed --out graph_asof --asof 2023-12
python -m src.model_a.provider_features_export --asof-cutoff 2023-12 --graph-dir graph_asof --out export_asof
```

**Medicare phase (once partb_<year>.csv / partd_<year>.csv are staged):**
```
python -m src.ingest_cms.medicare_fact   --partb-dir preclean\partb --partd-dir preclean\partd --out-dir processed\medicare
python -m src.ingest_cms.medicare_growth --fact processed\medicare\medicare_fact.parquet --out processed\medicare\medicare_growth.parquet
python -m src.model_a.medicare_export    --medicare-dir processed\medicare --preclean preclean ^
    --provider-dim processed\provider_dim.parquet --graph-dir graph --out-dir model_a\medicare
```

## 9. Current status

- **Run 1 (Medicaid) complete**: 617,062 providers × 111 features delivered to Travis;
  leads + backtest done. Reproducible from the matrix.
- **All buildable plan items DONE**: every Run-2 §A–§K item, the audit fix lists, the
  analytics/output-methodology reviews, Medicare Phase 2 core + runner, the new schemes, the
  feedback loop, the verification/retrospective harnesses. **~165 commits; 414 tests pass.**
- **Model verdict UNRESOLVED**: Travis reported 0.615 → 0.875 with my data; whether that jump
  is real (scenario A) or an artifact of circular negatives (scenario B) is the open question.

## 10. Unresolved issues / open externals

1. **Travis A/B**: which scenario (A ~0.863 / B ~0.603 for anomaly-scores-removed). He's
   rerunning the anomaly-score toggle. **Do NOT run the network/future-ban test on the
   CURRENT file** — its network + billing features already contain 2024–26 info (leakage);
   it needs the frozen file from §8.
2. **v1 concept columns carry the old max-of-percentiles calibration bug** — the Run-2 export
   supersedes them. Travis's v1 matrix should be regenerated.
3. **Revived signals need real-data re-measurement**: kickback matcher, digital twins,
   NULL-aware subscores — fixes proven on synthetic tests, AUC impact not yet measured.
4. **Saturation 10.3× headline** is state-grain, not local — needs the within-sector re-cut
   (and the HUD ZIP→county file for the county upgrade).
5. **Gated (Brad)**: OpenSanctions commercial-USE sign-off; IQVIA/PurpleLab; people-data +
   FCRA (Model B); T-MSIS DUA. Bundle push to GitHub still pending.
6. **PI targets — honest finding (2026-07-08)**: after screening, the Medicaid-only
   data yields **no clean, PI-ready "billing under banned ownership" target**. The
   `has_excluded_owner` probable tier was matching generic name keys ("HOMECARE") to
   stale LEIE rows (a 1990 entry) → false leads. **Fixed**: `distinctive_name_key`
   guard in `clean_data.py`, applied in `integrate.build_facility_flags` (tier B) and
   `entity_graph.build_edges.build_excluded_in_edges` (probable). What the data DOES
   give: (a) validation — 1,943 already-excluded providers re-surfaced by billing
   alone; (b) sector leads (hospice/home-health chains) = Model-B insider-recruitment
   targets, not PI targets. Real PI targets need real ownership data (PECOS/SNF/hospice
   ownership), not name-key matching. Local selectors `pi_candidates{,2,3}.py` (v3 =
   banned-ownership, screens institutions, names owner, EXACT vs PROBABLE tier, computes
   $ billed after owner exclusion) built + delivered.

## 11. Exact next steps (in order)

1. **Run `pi_extract3.py`** → upload `pi_out.txt` → build the 3 PI case files (ring 9234041708;
   NPI 1053311514; NPI 1336141779).
2. **Push the latest bundle** to GitHub from a machine with access.
3. **Generate Travis's frozen package** (§8 point-in-time) + a `future_bans_2024plus.csv`
   label file → send with the two Travis notes (A/B answer + the network-test protocol).
4. **Rerun the Medicaid export** with the fixes + `pip install openpyxl` + OpenSanctions;
   read `EXPECTATIONS_REPORT.md` first; rerun `signal_ranking` to re-measure the revived signals.
5. **Procure the cheap unlocks**: HUD ZIP→county, hcpcs_minutes.csv, SSA DMF, DMEPOS
   by-referring-AND-service, docgraph shared-patient; drop in `preclean\` and rerun.
6. **Medicare phase** once vintages are staged (§8).
7. **64GB box** for the full graph embeddings (Run 2b).
