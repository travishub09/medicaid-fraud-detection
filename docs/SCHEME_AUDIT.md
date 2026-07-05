# Scheme Audit — calculations, joins, and statistical soundness vs. the actual files

_Audit of all 20 schemes: the adapter code (calculations + joins), the statistical method, and
whether the assumptions match the published layouts of the underlying CMS files (verified
against public documentation where reachable; verification status stated per scheme). Companion
to section K (subscore transform) and the analytics review (digital twin, plausibility)._

## Cross-cutting findings (affect many schemes at once)

**X1 — CMS PUF suppression is being converted to zero. FIXED (see below).** Precision on the
mechanism: in the Part B and Part D by-provider-and-service/drug PUFs CMS **drops the whole
row** when it derives from ≤10 beneficiaries/claims (the bias is missing rows — not fixable
in-adapter, only documentable), while the **opioid by-provider summary blank-suppresses cells
in place** — and `opioid.py` was `fillna(0.0)`-ing those blanks, so "suppressed 1–10 opioid
claims" became "zero opioid claims" (falsely clean). A secondary effect in Part B: rows whose
bene count is unavailable still contributed their services to the per-bene ratio's numerator
while contributing nothing to its denominator. **Fixes landed:** `opioid.py` keeps blanks as
NaN (suppressed prescribers are unscored, never clean; `min_count=1` sums), and `partb.py`
pairs the per-bene numerator and denominator over the same benes-observed rows. The
row-dropping bias in Part B/D remains a documented limitation (HHI can still read concentrated
for heavily-suppressed small providers).

**X2 — Medicare-file features score a Medicaid-label universe.** Part B/D/opioid/Open Payments
describe *Medicare* behavior; the label is Medicaid-era exclusion and the fact table is Medicaid.
Coverage among positives is 3–8% for these schemes (documented). Their per-scheme AUCs against
this label mostly measure coverage, not signal quality — the fair read waits for Medicare Phase 2.

**X3 — Subscore zero-imputation** (`scheme_subscores.py:132`) floods every thin-coverage scheme's
0–1 score with an identical floor (separately reported; fix NULL propagation).

**X4 — Join hygiene is good.** Every export merge carries a fan-out assert (rule #2 honored);
CCN→NPI→org and org→NPI broadcasts are guarded, `org_member_count` ships so the model can
discount broadcast signals.

## Live schemes (13 in the v1 export)

| # | Scheme | Calculation & join | Verdict | Findings |
|---|---|---|---|---|
| 1 | **upcoding** | `partb.py`: share of office E/M services at levels 4–5 (99204/05/14/15 over 99202–99215) + services-weighted mean level; NPI groupby over the NPI×HCPCS×place-of-service grain | **Sound** | Code sets match the post-2021 E/M set (99201 correctly absent). Place-of-service dual rows handled by the groupby. Caveats: X1 suppression; `Tot_Benes` overlap across rows is documented as an upper bound. |
| 2 | **overutilization** | `service_intensity` (v3 concept off the Medicaid fact) + `services_per_bene` (Part B) | **Sound with bias note** | `total_benes` sums per-code bene counts, double-counting patients across codes → ratio biased *down* for broad-code providers, *up* for narrow billers. One-sided ranking survives it, but it systematically favors flagging narrow-mix providers — overlaps mill logic rather than being independent of it. v3 concept layer itself not deep-audited this pass (see "remaining debt"). |
| 3 | **single_service_mill** | `concentration` (v3, Medicaid fact) + `code_concentration_hhi` (Part B: HHI of allowed dollars across HCPCS) | **Sound / one artifact** | HHI arithmetic correct. Artifact via X1: heavily-suppressed small providers keep 1–2 rows → HHI ≈ 1 → false mill flag at low Medicare volume. Medicaid-side `concentration` is unaffected (no suppression in the fact). |
| 4 | **payment_outlier** | `payment_intensity` (v3 concept) | **Not deep-audited** | Concept layer pass pending. |
| 5 | **specialty_mismatch** | v3 concept + data-derived `clinical_implausibility` | **Method risk** | Plausibility prevalence is self-referential: a ≥5-provider taxonomy where a ring bills the same code makes that code "prevalent" — coordinated behavior defines its own normal. Needs a minimum absolute biller count + shrinkage to a cross-taxonomy prior. Self-reported taxonomy is the peer key (gameable) — the built billing-implied taxonomy is the fix. |
| 6 | **rapid_ramp** | `temporal` (v3) + `growth.py` level-shift / new-code burst | **Sound core, one confound** | Level-shift is a valid one-sided CUSUM-style statistic with MAD scaling and a flat-series guard; own-months indexing correctly refuses to zero-pad short histories. Confound: `new_code_burst` is mechanically ≈1 for orgs with 8–13 months of history (all codes are "new"), making it collinear with tenure instead of an independent pivot signal. Require pre-window history. |
| 7 | **ownership_integrity** | graph features (2-hop exclusion proximity, shell score, related-party density) | **Correct as built, leakage-flagged** | AUC 0.991 because it's exclusion-derived; correctly quarantined to `leakage_adjacent` and barred from training. Operations-only use is the right call. |
| 8 | **pharma_kickback** | `openpayments.py`: single-manufacturer concentration + payment↔utilization co-occurrence | **Concentration sound; co-occurrence broken** | Concentration (max/sum by manufacturer) is faithful to the file. Co-occurrence matches OP product names to Part D brand/generic by **exact string equality** and reads only product field 1 of 5 → near-zero hit rate (raw AUC 0.482 ≈ noise). Needs token/crosswalk matching + all five fields. Also X1 on the Part D side. |
| 9 | **drug_outlier** | `partd.py`: `high_cost_drug_share` = cost on top-decile cost-per-claim drugs / total cost; brand≠generic heuristic | **Defensible, two caveats** | Threshold is file-wide cost-per-claim; day-supply is ignored, so 90-day fills read as "high cost per claim" vs 30-day fills of the same drug — normalize per day-supply. Specialty mixes (oncology) legitimately dominate: the peer percentile downstream is what makes it fair — the raw share should never be ranked globally. Brand-vs-generic heuristic (Brnd_Name==Gnrc_Name ⇒ generic) is the standard published approximation. |
| 10 | **pill_mill** | `opioid.py`: opioid share of claims + long-acting share | **Layout verified, one bias** | Column mapping (Prscrbr_NPI / Tot_Clms / Opioid_Tot_Clms / Opioid_LA_Tot_Clms) matches the published Part D Prescribers by-Provider summary layout. Ratios and one-sided direction correct. X1 applies directly: suppressed blanks → 0 understates shares for low-volume prescribers; CMS also notes the OMS opioid drug list changes by year (cross-vintage comparability caveat). |
| 11 | **saturation_fraud** | `saturation.py`: providers per 1k FFS beneficiaries, percentile within service type; attached to orgs by **sector × STATE** | **Biggest finding of the audit** | (a) Attachment is **state-grain** — the code's own docstring says the county join waits on ZIP→county. Outbound docs say the signal compares "local area" supply — overstated: the index has ~50 distinct values per sector. The 10.3× lift is therefore substantially a **sector × state fixed effect** (e.g. "Florida hospice"), not local oversupply. The within-state, within-sector re-cut is required before this headline is used again. (b) `SECTOR_TO_SERVICE` maps only home-health / hospice / SNF / lab — the PUF also carries **ambulance and DME**, two of CMS's own highest-risk sectors, currently unmapped → silently NaN. Add them. (c) Per-org lookup loop is order-dependent on substring match and O(orgs×cells) — deterministic-ize. |
| 12 | **worthless_services** | `facility.py`: PBJ nurse-hours-per-resident-day (negated) + deficiency counts, CCN grain → org | **HPRD sound; deficiencies too crude** | HPRD = Σ(RN+LPN+CNA hours)/Σ census on census>0 days matches CMS's published PBJ method; negation preserves the one-sided convention. `deficiency_count` is a raw row count: bigger facilities get more surveys → count correlates with size, not badness (its raw AUC 0.44 is consistent). The file carries scope/severity — weight by severity and normalize per bed. |
| 13 | **hospice_ineligibility** | Care Compare hospice measures, long format, filter measure name ~ "live discharge" | **Empirically wired, loosely specified** | 3,518 CCNs matched in the real run, so the measure exists in the procured file. But the substring filter could match multiple live-discharge measures with different denominators; CMS's JS-rendered doc pages blocked full verification of measure codes this pass. Pin the exact measure code(s) after inspecting the matched rows. |

## Dormant / gated schemes (7 — correct-by-refusal, audited for design only)

| # | Scheme | Status | Audit note |
|---|---|---|---|
| 14 | **impossible_day** | dormant by data | Correctly refuses to fire (skip-missing); needs day-level counts or the PFS time-file approximation (§I3). Design sound. |
| 15 | **dme_ring** | dormant — wrong file layout procured | The procured by-referring SUMMARY lacks `hcpcs`; the adapter wants the by-Referring-Provider-AND-SERVICE layout (Rfrg_NPI × HCPCS) — which doubles as the influenced-dollars exposure basis (docs/OUTPUT_METHODOLOGY.md). Registry corrected. Re-pull unlocks it. |
| 16 | **contract_pharmacy (340B)** | gated on `openpyxl` | OPAIS 3-worksheet loader built; not audited against a real file this pass. |
| 17 | **invalid_identity** | gated on `openpyxl` | Deactivation-report loader (banner-header handling) built; billing-after-deactivation is a DuckDB month filter — design correct, unverified on real file. |
| 18 | **cost_report_fraud (HCRIS)** | dormant — needs `ccn_to_npi` | Not audited beyond wiring. |
| 19 | **drug_spread_anomaly (NADAC)** | gated — needs NDC-level claims | Correctly dormant; the Medicaid fact has no NDCs (HCPCS only), so this cannot light up on current data. |
| 20 | **billing_after_death** | gated on SSA DMF | DOB-corroborated DuckDB filter design is right; unverified on real file. |

## Remaining audit debt (stated, not hidden)
1. **The v3 concept layer** (`concentration`, `payment_intensity`, `service_intensity`,
   `specialty_mismatch`, `temporal` — computed in `attempt_2/leads` off the Medicaid fact) feeds
   five live schemes and Travis's top features, and was **not** line-audited in this pass. It is
   the highest-value next audit target.
2. Hospice measure-code pinning (see #13); 340B/deactivation/DMF loaders vs real files.
3. The within-state-within-sector saturation re-cut (one query on the real data) — decides
   whether the 10.3× headline survives.

## Priority fixes from this audit — status
1. **Saturation** — DONE in part: DME + ambulance added to `SECTOR_TO_SERVICE` (+ an
   `ambulance` sector in `sector_priors`), lookup made deterministic. REMAINING: correct the
   outbound "local area" claim wherever repeated; build the ZIP→county join; run the
   within-sector/state re-cut on real data.
2. **Suppression handling (X1)** — DONE (`opioid.py` NaN-preserving; `partb.py` paired
   per-bene ratios). Row-drop suppression in Part B/D stays a documented limitation.
3. **Kickback co-occurrence** — DONE: form-stripped-name + first-distinctive-token matching
   across ALL five OP product fields, with a loud warning when no product column exists.
   The token fallback trades some precision for the recall that makes the signal exist;
   documented in `_match_keys`. Re-measure AUC on real data after the next run.
4. **Subscore NULL propagation** — DONE (`scheme_subscores.py`: per-row weight
   renormalization; no observed evidence → NaN, the zero-imputed floor is gone). Downstream
   note: subscore coverage in the export report will now reflect true source coverage
   instead of a fake 100%.
5. REMAINING: **deficiency severity weighting + per-bed normalization** (`facility.py`);
   **day-supply normalization** for `high_cost_drug_share`; **pre-window history** for
   `new_code_burst`; **prevalence shrinkage** for plausibility; digital-twin exogenous
   controls (tracked in the analytics review).

_All fixes above ship with regression tests in `tests/test_scheme_audit_fixes.py`; full suite
376 passing._
