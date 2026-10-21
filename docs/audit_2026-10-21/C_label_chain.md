# LABEL CHAIN AUDIT (code only, read-only) — 2026-10-21

Scope: every path that turns a public record into a training positive, a forward-ruler positive, or a strict-export row. Data files were not inspected; every statement below is about code, with file:line.

Freeze conventions actually in force (the runner `ops/vm/run_frozen_night.sh` passes `CUT=YYYY-MM`):

| layer | cut instant | where |
|---|---|---|
| as-of graph exclusions | `excl_date <= Timestamp("YYYY-MM")` = **month START** (undated dropped) | temporal_sources.py:43-48, 67-68; entity_graph/__main__.py:341-359 |
| as-of billing fact | service month **< cutoff month** (cutoff month excluded) | asof_billing.py:26, 43-59 |
| training tier label (exclusions) | `excl_date <= f"{cutoff[:7]}-01"` = month START | company_list.py:220-226 (pinned by tests/test_audit_fixes_2026_10.py:108-115) |
| training DOJ case label | conduct_start year <= cut year AND `announced <= Timestamp(cutoff-01) + MonthEnd(0)` = **month END** | case_labels.py:472-488 |
| forward exclusion ruler | first exclusion **strictly > month START** | prospective_label.py:50-53, 123-125 |
| forward FCA case ruler | first announcement **strictly > month START**, conduct_start <= cut year (NaN passes), horizon | prospective_label.py:190-198 |
| affiliated ruler | exclusion **> month START**, <= horizon | label_expansion.py:166-173 |

---

## A. Source table

"NPI rule" = how the row's `npi` is validated and what happens to name-only rows. "Ruler rule" = the branch of `fca_relevance.fca_tier` (fca_relevance.py:225-308) that matches the prefix written. "Default" = the tier when the branch's inner rules do not match.

| # | module → function | output file | excl_type convention written | date col | NPI rule / name-only handling | ruler branch that matches | default tier if inner rule misses |
|---|---|---|---|---|---|---|---|
| 1 | attempt_2/ingest/integrate.py `build_exclusions` :491-527 (LEIE) | processed/exclusions.parquet (+ revocations unioned by `merge_exclusion_sources` :530-558 when `--revocations` file exists, :704) | raw `EXCLTYPE` ("1128a1", "1128Aa", "BRCH CIA", "1128c3gi" …), no prefix | `excl_date` = EXCLDATE `%Y%m%d` (:500-505); 0/00000000 → NaT | `_canonicalize_with_quarantine` (Luhn; invalid → None + quarantine row) :498; name-only rows KEPT with `npi` None, no marker except the empty npi; `state/city/zip/upin/dob/general/specialty/waiver_*` carried :519-524; `name_key` via `clean_data._normalize_name` :516 | LEIE statute path: `_NORM`-key exact → `LEIE_STATUTES` :298-300, else longest-prefix :303-305 | `1128*` unmapped → **weak** "LEIE statute unmapped" :306-307; anything else → **weak** "unrecognized source" :308 |
| 2 | enforcement/medicare_revocations.py `normalize_revocations` :44-84 | processed/exclusions_medicare_revocations.parquet (CLI :87-102) AND/OR unioned into exclusions.parquet (integrate :543-550) | `"medicare_revocation: <REVOCATION_RSN>"` :75-76 | `excl_date` = REVOCATION_EFCTV_DT :67 | `canonicalize_series` (Luhn; invalid → "") :58, :72; name-only rows KEPT (`npi == ""`) :83; `name_key` via `norm_org_name` :74 | `startswith("medicare_revocation")` :241-249 → `REVOCATION_RULES` :88-126 per `;`-separated ground, strongest wins | **weak** "revocation reason unmapped" :219 |
| 3 | enforcement/state_terminations.py `normalize_state_list` :180-238 + `match_names_to_nppes` :241-285 | processed/exclusions_state_terminations.parquet — **only `EXCLUSION_COLS` written** :397 (`state`, `source_file`, `match_basis`, `non_npi_role` dropped) | `"state_term:<ST>:<reason> [<ptype>]"` :210-211, `"|name_match"` appended on a name match :282 | `excl_date` via `_dates` (multi-format, sentinel-clamped) :146-177, 206 | 10-digit + `is_valid_npi` :187-200 (a cell with several NPIs explodes to one row each); blank-NPI rows KEPT; unique + `distinctive_name_key` in-state NPPES match fills them (`match_basis=name_match`), `non_npi_role` gate :270-274 refuses aides/clerks | `startswith("state_term")` :250-255 → strips `|name_match` + `[...]`, `_STATE_LEIE_CODE` :194,201-206 then `STATE_TERM_RULES` :135-179 | **weak** "no reason recorded" / "unmapped" :210-212 |
| 4 | enforcement/research_evidence.py `load_state_exclusions` :196-233 (reviewed state lists) | processed/exclusions_state_<st>.parquet :659-663 | `f"{source}_{st}:<action>"` with **default `source="state_term"`** → `"state_term_ga:<action>"` :197, 219-224; the CLI exposes no `--source` :557-562 | `excl_date` to_datetime :225 | `canonicalize_series` (Luhn) :216; name-only KEPT; `name_key` `norm_org_name` :223 | **`startswith("state_term")` fires first** :250 → `t.count(":") >= 2` is False (one colon) → body `""` → `_classify_state_reason("")` :210-211 | **always weak** "no reason recorded" (the `<src>_<st>:` branch at :290-296 is unreachable for this writer) — see F2 |
| 5 | enforcement/opensanctions.py `normalize_opensanctions` :42-94 | processed/exclusions_opensanctions.parquet | `"opensanctions:<dataset[0] or topic[0] or 'entity'>"` :61-63 | `excl_date` = startDate else listingDate (+ `listed_date` knowledge-time col) :76-84, 89-91 | `npi` = raw `npiCode` or **`idNumber`, no Luhn** :65; name-only KEPT | `startswith("opensanctions")` :230-231 | **excluded** (fixed) |
| 6 | enforcement/preclusion.py `normalize_preclusion` :47-84 | processed/exclusions_preclusion.parquet | `"preclusion: <reason>"` :78 | `excl_date` = PRECLUSION_DATE/EFFECTIVE_DATE :70 | `canonicalize_series` (Luhn) :61,75; name-only KEPT :83 | `startswith("preclusion")` :239-240 | **weak** (fixed) |
| 7a | enforcement/sam_api.py `fetch_sam_exclusions` :31-73 | <feeds>/sam/exclusions_sam.parquet :158 | `"sam:<exclusionType>"` :57-58 | `excl_date` = activateDate :59-61 | `npi` always `""` :51 — every row name-only | `startswith("sam")` :232-238: weak if `fraud|health|medicare|medicaid|\|hhs\b` | **excluded** |
| 7b | enforcement/sam_api.py `load_sam_extract` :93-136 | same file | `"sam:<Exclusion Type>|<Excluding Agency>|<Exclusion Program>"` :121 | `excl_date` = Active Date :127 | **10-digit length only, no Luhn** :119-120 (the documented 11,234 bad-Luhn UEI/DUNS values pass when 10 digits); name-only KEPT | as 7a | **excluded** |
| 8 | enforcement/state_licensing.py `normalize_state_licensing` :35-68 (library only, no CLI) | caller-named exclusions_*.parquet | `"state_board:<ST>:<action>"` :62-63 | `excl_date` = action_date :64 | `canonicalize_series` :56; name-only KEPT :68 | `startswith("state_board")` :288-289 | **weak** (fixed) |
| 9 | enforcement/death_master.py :38-134 | **not an exclusion source**: `billing_after_death` feature; the export widens the label with tag `death` from the NPI's own `death_paid_after` (provider_features_export.py:576-600) | provenance tag `"death"` in `exclusion_label_sources` (not an excl_type) | death date vs service month :124-126 | DOB-corroborated matches only :107-108 | n/a — never tiered; counted under policy `all` only (company_list.py:254-255), never under `hard` (tier_label ignores it) | n/a |
| 10 | enforcement/cia.py `cia_case_rows` :108-143 → model_a/label_expansion.py `cia_resolve` :254-371 | enforcement/cia_cases.csv (case-DB schema; `case_type=civil_fca`, summary carries "agreed to pay a False Claims Act settlement" :136-137, `intervened=1`, `qui_tam=NA`) + processed/cia_defendant_npis.parquet (resolver schema: `match_level` cia_exact_org / cia_owner / cia_component, `label_weight=min(1,budget/NPIs)` :363-368) | n/a (case path; forward `excl_type` becomes `"doj:fca_civil"`) | `announced_date` = CIA effective date :118-121 | NPIs come from graph membership (`npi_to_org`) — valid by construction; chains > 150 NPIs → residue `chain_org` :359-362 | case path: `fca_tier("doj:…")` :286-287 | **hard** (fixed) |
| 11 | ingest_cms/medicaid_enrollment.py `termination_events` :238-282 / `termination_events_from_parquet` :636-694 → `split_label_candidates` :318-385 | processed/exclusions_medicaid_enrollment.parquet (+ `medicaid_enrollment_ends_administrative.parquet` quarantine) :551-552 | `"mcd_enroll:<ST>:<status | reason> [<ptype>]"` :262-263, :671 | `excl_date` = first month-end carrying the status, or the START of an open-ended terminated status :258, :672 | `^\d{10}$` regex only, **no Luhn** :160, :589, :665; `entity_name`/`name_key` always `""` :275-276 | `startswith("mcd_enroll")` :256-285 → benign regex → `MCD_ENROLL_EXCLUDED` :184-192 → felony-unstated weak → exclusion-derivative weak → `_classify_state_reason` | **weak** (via `_classify_state_reason` default) |
| 12 | model_a/label_expansion.py `exclusion_name_resolve` :640-715 + `name_resolved_exclusions` :718-731 (names lane, offline) | processed/exclusions_name_resolved.parquet | the ORIGINAL statute (e.g. `"1128a1"`) :728; the match basis lives in a separate `match_level` column (`excl_upin` / `excl_name_state_unique` / `excl_org_state_unique`) :730 that **the graph loader drops** (build_nodes.py:115-118) | original `excl_date` :728 | `npi` = the unique in-state NPPES record (valid by provider_dim construction); no `non_npi_role` gate (see F5) | LEIE statute path (as #1) | as #1 |
| 13 | enforcement/research_evidence.py `load_exclusion_npis` :498-534 (names lane, Manus) | appended to processed/exclusions_name_resolved.parquet :647-657; manus_lanes.py:452-456 | original statute; `match_level = research_<conf>` (dropped by the loader) | original `excl_date` :528 | `is_valid_npi` + confidence >= medium + NPI ∈ listed candidates :518-523 | as #1 | as #1 |
| 14 | model_a/label_expansion.py `affiliated_convictions` :65-148 + `merge_affiliated` :151-190 | processed/affiliated_convictions.parquet → forward-ruler rows only (never an exclusions_*.parquet, never training) | `"affiliated:<original excl_type>"` :180 with `fca_tier` copied from the individual's row :182 | `first_excl_date` = the individual's exclusion date :179 | biller NPI from the spending fact's `billing_npi` (no Luhn check; real by construction) | **no branch**: `fca_tier("affiliated:1128a1")` → regex :290 fails (colon not underscore) → key `affiliated1128a1` not in LEIE_STATUTES → :308 | **weak** "unrecognized source" — only matters if a consumer re-classifies the file (the ruler file carries the copied tier) |
| 15 | case sources → enforcement/case_db.py (`CASE_COLUMNS` :25-41): DOJ harvest, docket_backfill, oig_enforcement, `research_evidence.load_mfcu_cases` :236-278 (untyped), `load_commercial_cases` :289-345 (`hcf_commercial`), `load_outcome_refresh` :389-445 (typed from the answer), manus_lanes `_harvest_cases_from_table` :300-354, CIA (#10) | enforcement/doj_cases_v3.csv → merged `doj_cases_v3_plus_cia.csv` (label_expansion `merge_label_inputs` :374-418) | training `label_source` ∈ {doj_case, charged_case, hcf_commercial, docket_case} (case_labels.py:313-319, 536-539); forward `excl_type = "doj:<fca_strict_tier>"` (prospective_label.py:202) | `announced_date`; conduct window from the summary's years else `[ann-4, ann]` (case_labels.py:34-51) | NPI via `npi_in_source` (`\d{10}`, no Luhn, case_labels.py:390-391), the resolver (status `resolved`, `\d{10}`) :408-409, org membership, reassignment edges | `startswith("doj")|("case")` :286-287 | **hard** |

Prefix coverage summary — written vs known to the ruler:

- Known branches (fca_relevance.py): `opensanctions`, `sam`, `preclusion`, `medicare_revocation`, `state_term`, `mcd_enroll`, `doj`/`case`, `state_board`, `<letters>_<st>[:…]` (research rows, src ∉ {state, medicare, mcd}), bare LEIE statute keys.
- Written prefixes with **no matching branch**: `affiliated:` (#14, cosmetic as long as the copied `fca_tier` column is used); `state_term_<st>:` (#4) **matches the wrong branch** and loses its reason.
- `_label_source` (provider_features_export.py:1097-1118) mirrors the same prefixes and returns `"leie"` for anything else (:1118) — `affiliated:` or any future prefix is recorded as LEIE provenance.

---

## B. The chain, hop by hop

```
RAW RECORD
 ├─ LEIE csv ─── integrate.build_exclusions (:491) ─┐
 ├─ revocations csv ─ medicare_revocations.normalize_revocations ─┤ (also unioned by integrate.merge_exclusion_sources :530)
 ├─ state lists ── state_terminations.load_all → normalize_state_list → match_names_to_nppes [gate 2 non_npi_role :270] ─┤
 ├─ reviewed state list ─ research_evidence.load_state_exclusions (:196) ─┤
 ├─ OpenSanctions / SAM / preclusion / state boards ─ their normalize_* ─┤
 ├─ CMS enrollment segments ─ medicaid_enrollment.termination_events_from_parquet (:636) → split_label_candidates (:318) ─┤
 ├─ LEIE name-only rows ─ label_expansion.exclusion_name_resolve (:640) → name_resolved_exclusions (:718) ─┤
 └─ Manus names answers ─ research_evidence.load_exclusion_npis (:498) [Luhn, conf>=medium, NPI ∈ candidates] ─┘
                                   │  processed/exclusions.parquet + processed/exclusions_*.parquet
                                   ▼
 entity_graph/__main__._load (:48-69): pd.concat of every exclusions_*.parquet (no dedup)
   └─ --asof: temporal_sources.point_in_time_tables (:51) → asof_filter(excl_date <= asof, undated DROPPED) (:37)
   └─ build_nodes.build_exclusion_nodes (:105): node_id "exclusion:<i>", keeps a FIXED column list (:115-117)
   └─ build_edges.build_excluded_in_edges (:160): exact by npi, probable by name_key (+distinctive) for owners
                                   │  graph/nodes/exclusion_nodes.parquet (full) ; graph_asof_<cut>_full/… (frozen)
                                   ▼
 TIERING (one function everywhere): fca_relevance.fca_tier (:225) / classify_series (:311) / tier_mask (:341) / TIER_POLICIES (:324)
                                   │
         ┌─────────────────────────┴──────────────────────────────┐
         ▼ TRAINING                                               ▼ FORWARD RULER
 provider_features_export._widened_label_from_graph (:1121)      prospective_label.build_prospective_label (:56) on the FULL graph
   reads <graph-dir>/nodes/exclusion_nodes (as-of graph on a        first exclusion per NPI; positive iff dated & > cutoff
   frozen run); npi len>=10 → provider_on_exclusion=1,              & kept tier; pre-cutoff (ANY tier) → was_excluded_pre_cutoff
   exclusion_label_sources via _label_source (:1097)             prospective_label.build_forward_case_label (:150):
 + case_labels.build_case_labels (:164) [--case-db]                build_case_labels(current-day, no asof) → label_source==doj_case
   usable==1 (:233) → is_index_url drop (:245-251)                  & fca_strict_tier != "" → announced > cutoff (& <= horizon)
   → charged_only_cases split (:267) → resolve_settled_orgs          & conduct_start <= cut year (NaN passes) ; qui_tam_only
   (require_recovery=False, two passes :269-274) → _fuzzy_settled   merge_forward_labels (:218): OR positives, known under either
   (difflib 0.92 :275-282) → tiers named_npi / resolved_defendant     ruler → never positive
   (evidence_strength != weak :421-426) / org_billing (<=150        label_expansion.merge_affiliated (:151) [--affiliated]
   members :449-451) / affiliated_individual (<=150, never for      → future_bans_after_<cut>[__fca36|__aff36|__quitam36].csv
   charged :457-464) → _outcome rule (:339-374: self_disclosure              │
   out, commercial on conviction only, untyped on amount>0 or               ▼
   civil words, charged_outcome → charged_case)                   accuracy_diagnostics.load_ruler (:181) → eval_frame (:195):
   → frozen box: conduct_start<=cut year AND announced<=MONTH END     known = was_excluded_pre_cutoff | provider_on_exclusion==1
   (:472-488) → label_weight = min(1, 100/case_fanout) on             | case_ids non-empty (:209-217) → dropped; horizon box;
   broadcast rows, capped by a resolver row's own weight (:498-515)  other rulers → ambiguous
   → one row per NPI, precedence doj_case > charged_case >         (network_ab :263-266, source_ablation :146-152,
   hcf_commercial > docket_case (:536-539)                            label_ablation :146-150, scheme_health :89-94,
 export merge (:386-453): hard flip only for basis != affiliated      learning_loop :274-277 use ONLY was_excluded_pre_cutoff)
   and source doj_case; docket/charged/commercial recorded as
   sources without flipping; §E deact/death own-event widening (:576-600)
                                   │
                                   ▼
 company_list: set_label_policy (:51, validates vs TIER_POLICIES) → _training_label (:251, RAISES on tier policy w/o nodes)
   → tier_label (:207): kept-tier exclusion nodes dated <= cutoff-01 | doj_case ∀ policy | charged_case iff policy keeps
   'charged' | hcf_commercial iff policy keeps 'commercial' ; oof_scores (:333): ambiguous = in-matrix positive & tier 0 →
   MASKED (never negative); training_weights (:280) = label_weight; broadcast_only_mask (:97) under policy mask;
   case_cluster_groups (:109) merges orgs sharing a case into one fold group.
                                   │
                                   ▼
 STRICT EXPORT fca_label_export.build_strict_labels (:190): resolver rows status==resolved → outcome_tier (:82) →
   Luhn → OK_MATCH_LEVELS (:63-71) → NPI in NPPES → entity type agrees → not deactivated before conduct →
   evidence_strength (:135) ; with_exclusions/fca_shaped: exclusion nodes by STRICT_STATUTES (:322-341) or
   state_term/mcd_enroll by _STATE_FRAUD words (:296-321) ; --min-strength drops the weak stratum (:436-441)

 RESEARCH → LABEL ROW gates (label_gates.py): is_index_url (:42) · non_npi_role (:79) · name_agrees (:113) · name_in_page (:168)
   applied at: case_labels:245-251 ✔ · manus_lanes:326 ✔ · research_evidence:256,317 ✔ (gate 1)
               state_terminations:218-235,270-274 ✔ ONLY (gate 2)
               defendant_research.fold_results:200-213 ✔ only when provider_dim is passed (manus_lanes:488-492 passes it; the CLI :269 does not) (gate 3)
               research_evidence.verify_names_on_pages/quarantine_absent_defendants:89-160 ✔ only via the manual `verify-sources` CLI (:601-605) (gate 4)
```

---

## C. FINDINGS (ranked)

### HIGH

**F1. Month-END vs month-START boundary on the DOJ case label — the same case can be a training positive and a forward positive.**
- `src/model_a/case_labels.py:482` boxes a frozen run's case rows to `announced <= Timestamp(f"{cutoff[:7]}-01") + MonthEnd(0)` (month END), while every other freeze is month START: the as-of graph (`temporal_sources.py:43-45` with `--asof YYYY-MM` → the 1st), `tier_label` (`company_list.py:226`, pinned by `tests/test_audit_fixes_2026_10.py:108-115`), the exclusion ruler (`prospective_label.py:123-125`) and the case ruler (`prospective_label.py:190-198`, `_ann > cutoff_ts` with `cutoff_ts` = the 1st). The billing fact ends a month earlier still (`asof_billing.py:26`).
- Effect: a case announced on day 2..last of the cutoff month enters `provider_on_exclusion` / `exclusion_label_sources=doj_case` (`provider_features_export.py:425-451`) AND `is_prospective_positive=1` with `was_excluded_pre_cutoff=0` in `future_bans_after_<cut>__fca36.csv`.
- `accuracy_diagnostics.eval_frame` (`:213-217`) happens to neutralise it by also dropping any matrix row with `provider_on_exclusion==1` or non-empty `case_ids` — so the headline hits@k are safe. The measurement layer is not: `network_ab.py:263-266`, `source_ablation.py:146-152`, `label_ablation.py:146-150`, `scheme_health.py:89-94` (the `fca_auc` / `fca_verdict` that drive `prune_candidate`), `learning_loop.py:274-277` (near misses) read only `was_excluded_pre_cutoff` from the ruler file.
- Fix: `cut_ts = pd.Timestamp(f"{str(asof_cutoff)[:7]}-01")` at `case_labels.py:482` (keep `<=`), and add a planted test beside `test_frozen_case_labels_require_announcement_before_cutoff` with an announcement dated the 15th of the cutoff month expecting 0 rows.

**F2. The reviewed-state-list loader writes a prefix the ruler reads as an empty reason: every such row tiers weak.**
- `src/enforcement/research_evidence.py:197,219-224` writes `excl_type = "state_term_<st>:<action>"` (default `source="state_term"`; the CLI at `:557-562, 659-660` exposes no `--source`). `fca_relevance.fca_tier:250` tests `t.startswith("state_term")` BEFORE the `<src>_<st>:` regex at `:290`; the body is taken as `t.split(":", 2)[2] if t.count(":") >= 2 else ""` (`:252`) → one colon → `""` → `_classify_state_reason("")` → `("weak", "state termination with no reason recorded")` (`:210-211`). A reviewed row whose action says "convicted of Medicaid fraud" never becomes hard; the fca_shaped strict export likewise skips it (`fca_label_export.py:296-302`, same split).
- The repo's own expectation is the `mfcu_ga:` form (`tests/test_audit_fixes_2026_10.py:135`, `_label_source` branch at `provider_features_export.py:1115-1117`), which the writer never produces.
- Fix: default `source="mfcu"` (or add `--source`) in `load_state_exclusions`, or write `"state_term:<ST>:<action>"`; add a test `fca_tier(load_state_exclusions(...).excl_type[0])` for a fraud action == hard.

**F3. The v18 runner will stop at step 8 once commercial rows exist: `--label-tier hard+commercial` is rejected by argparse.**
- `ops/vm/run_frozen_night.sh:158-160` runs `accuracy_diagnostics --label-tier hard+commercial` when the merged case file contains `hcf_commercial` (the 2026-10-20 lane load put 76 such rows in the case DB), but `src/model_a/accuracy_diagnostics.py:1176` has `choices=["all","hard","hard+charged","hard+weak"]`; argparse exits 2 → `|| fail 8`. The same omission is in `pursuit_dossier.py:1012`, `learning_loop.py:488`, `consensus.py:498` while `fca_relevance.TIER_POLICIES` (`:324-325`) and `company_list.set_label_policy` (`:51-57`) accept it.
- Fix: `choices=list(TIER_POLICIES)` in all four CLIs.

### MEDIUM

**F4. The strict export's `fca_shaped` policy admits state rows on fraud WORDS, not on the ruler's tier — an indictment or a credible-allegation suspension can enter the PurpleLab label as `state_fraud` / `exact_id`.**
- `src/model_a/fca_label_export.py:296-320` gates `state_term`/`mcd_enroll` nodes with `_STATE_FRAUD` (`:54-56`, "fraud|…|convict|…") while `fca_relevance.STATE_TERM_RULES:142-146` tiers "indicted for Medicaid fraud" as `charged` and "credible allegation of fraud / payment suspension" as `weak`. Two definitions of "fraud-shaped" now live in two files and have already diverged ("program integrity", "mfcu", "phantom" are hard in `fca_relevance:147-152` and absent from `_STATE_FRAUD`; "indict" is admitted by `_STATE_FRAUD` via "fraud").
- Also the bracketed `[provider type]` is not stripped before the search at `:301` (fca_relevance strips it at `:254`).
- Fix: replace the word test with `fca_tier(et_raw)[0] == "hard"` and import the basis string into `outcome_words`.

**F5. Gate 2 (`non_npi_role`) is applied only in `state_terminations`; the names lane resolves LEIE aides by unique name.**
- `label_expansion.exclusion_name_resolve:640-715` resolves a name-only hard-tier LEIE row to the unique in-state NPPES FIRST-LAST / org-key hit without consulting the row's `general` / `specialty` columns that `integrate.build_exclusions:519-521` now carries (and `build_exclusion_nodes:116-117` keeps). `label_gates.non_npi_role` (`:79-85`) is called from `state_terminations.py:218,235,270-274` only (grep: no other caller). The Manus residue (`candidate_npis`) and `research_evidence.load_exclusion_npis:518-523` inherit the same unguarded candidate set.
- Fix: before the lookup at `:689`, `if non_npi_role(f"{getattr(r,'general','')} | {getattr(r,'specialty','')}"): status = "non_npi_role"; continue` (quarantined, listed).

**F6. Gate 3 (`name_agrees`) is bypassed on the standalone research CLI.**
- `src/enforcement/defendant_research.py:269` calls `fold_results(table, prompts, results)` with no `provider_dim`; the gate at `:200-213` runs only when `provider_dim is not None`. `manus_lanes.load_lane:488-492` passes it (with a note when absent, `:490`), the CLI path does not and has no flag for it.
- Fix: add `--provider-dim` (default `<data_root>/processed/provider_dim.parquet`) and pass it; refuse to fold without it unless `--no-name-gate` is explicit.

**F7. Gate 4 (`name_in_page`) is never called by any loader; harvested case rows enter the case DB unverified until an operator runs `verify-sources`.**
- `manus_lanes.load_lane` merges commercial and CIA CSV-A rows (`:384-418`) via `merge_into_case_db`; `name_in_page` is only reached through `research_evidence.quarantine_absent_defendants` (`:134-160`) from the manual `verify-sources` sub-command (`:601-605`). Neither `run_cycle` (`manus_lanes.py:593-638`), `ops/vm/manus_lanes.sh` nor `run_frozen_night.sh` invokes it, and `case_labels` does not read `name_in_source`. The CLAUDE.md order ("re-load the lanes, re-run the state loader, verify-sources, THEN v18") is a convention, not code.
- Fix: call `quarantine_absent_defendants(inp["case_db"], fetch or browser_fetch_text, …)` at the end of `load_lane` for lanes `cia`/`commercial`, or have the runner's step 0 refuse a merged case file that carries research-jurisdiction rows with `name_in_source == ""`.

**F8. Provenance of the match basis is lost at the graph loader; name-resolved LEIE rows are rated `exact_id` by the strict export.**
- `build_nodes.build_exclusion_nodes:115-118` keeps a fixed list; `match_level` (`label_expansion.name_resolved_exclusions:730`, `research_evidence.load_exclusion_npis:530`) and `state` / `match_basis` / `non_npi_role` of state lists (`state_terminations.py:397` writes only `EXCLUSION_COLS`) do not survive. `fca_label_export.build_strict_labels:322-341` then treats every node with a strict statute as `basis="npi_on_list"`, `match_level="exact"`, `evidence_strength="exact_id"` — a unique-name-in-state or Manus `research_medium` resolution is exported with the strongest evidence rung. (`state_terminations` avoids this by encoding `|name_match` in `excl_type` :282, which the export reads at `:314-318`.)
- Fix: encode the basis in `excl_type` for the names lane (`f"{statute}|{match_level}"`) and teach `fca_label_export:322-323` to strip/read it, or add `match_level` to the loader's keep list and read it at `:336-339`.

**F9. CIA research entities are accepted on Luhn alone — no NPPES existence, name or state check at load; `case_labels` takes them without one either.**
- `research_evidence.load_cia_entities:483-493` sets `status=resolved, match_level=research_cia` for any Luhn-valid NPI; `fca_label_export.evidence_strength:144-149` rates `research_cia` moderate; `case_labels.build_case_labels:407-436` admits status `resolved` + `\d{10}` with no NPPES look-up (the strict export does check NPPES / entity type at `:254-262`, training does not). An agent typo that is a valid NPI labels an unrelated provider in training.
- Fix: in `load_cia_entities` look the NPI up in `provider_dim` and require `name_agrees(enrolled_entity_name, "org", nppes_name)` (plus state agreement); otherwise `status=research_name_conflict`.

**F10. Rule order in `STATE_TERM_RULES` demotes convictions that mention the charge, and `charged` is unanchored.**
- `fca_relevance.py:142-146`: the `charged` pattern precedes the hard pattern (`:147-152`), so "CONVICTED OF MEDICAID FRAUD; INDICTMENT 2019" → `charged` (second alternative `(fraud|…medicaid…).*(indict|charged|…)`), and the bare `charged` token matches "DISCHARGED … MEDICAID". Same ruler is reused for `mcd_enroll` via `_classify_state_reason` (`:284`).
- Fix: test the outcome regex (`convict|guilty|plea|sentenc`) before the charged rule, and use `\bcharged\b`.

**F11. Policy inconsistencies between CLIs and the code they call.**
- (a) `prospective_label.py:256` `--fca-tier choices=["all","hard","hard+weak"]`: the guard at `:289-290` for `hard+charged` is unreachable (argparse rejects it first) — harmless, but the message the operator sees is argparse's, not the policy's.
- (b) `label_expansion.py:465,488` offer `--tier hard+charged` for `affiliated` / `names`; `merge_affiliated` (`:151-190`) feeds the FORWARD ruler, so a `charged`-tier exclusion of an individual can become a forward "affiliated" biller positive although `prospective_label` refuses the charged tier for the ruler (`:289-290`). Fix: restrict `affiliated --tier` to `{hard, hard+weak}` or have `merge_affiliated` apply `tier_mask(..., "hard")`.
- (c) `fca_relevance._policy_tiers:336-337` silently adds `charged` to `hard+weak`; `tier_label`'s comment (`company_list.py:236-239`) says so, but `TIERS`'s docstring (`:21-26`) says charged trains under `hard+charged` only. Document or split.

**F12. The outcome rule is written three times and diverges.**
- `fca_label_export.outcome_tier:82-118` (untyped rows need `_STATE_FRAUD` words) vs `case_labels._outcome:358-365` (an untyped row is `fca_civil` on `amount>0` alone) vs `manus_lanes._OUTCOME_OK/_CHARGE_ONLY:59-60` (accepts "exclusion" and "civil monetary" as resolved outcomes, which `outcome_tier` then rejects for `civil_fca`). `research_evidence.load_mfcu_cases:236-278` has no outcome gate at all, so an MFCU row with a dollar amount and no fraud words is a `doj_case` hard training positive while the strict export quarantines it. Fix: route every loader through `outcome_tier` and make the untyped-amount path explicit (a `state_case` tier) in one place.

**F13. Duplicate exclusion nodes when both the integrate union and the standalone adapter run.**
- `integrate.merge_exclusion_sources:543-550` unions revocations into `exclusions.parquet` when `preclean/revocations/revoked_providers.csv` exists (`:667-670`), and `medicare_revocations.main` writes `exclusions_medicare_revocations.parquet`; `entity_graph/__main__._load:63-69` concatenates both with no de-duplication. Same for any source run twice under two names. Effect: doubled `excluded_in` edges / proximity weight (labels are set-valued, so the label itself is unaffected). Fix: `drop_duplicates(["npi","name_key","excl_type","excl_date"])` after the concat.

### LOW

**F14. Identifier validation is uneven across adapters (rule 1/7 spirit).** `sam_api.load_sam_extract:119-120` (length only), `opensanctions.py:65` (`idNumber` raw), `medicaid_enrollment.py:160,589,665` (regex only), `case_labels.py:390-391` / `manus_lanes.py:348` / `research_evidence.py:338` (`\d{10}` for `npi_in_source`). A non-Luhn 10-digit string becomes an exclusion node that `_widened_label_from_graph:1134` counts (`len >= 10`) and `prospective_label` lists; it cannot join a real NPI so the label is unaffected, but counts, funnels and the `names` residue are polluted. Fix: `canonicalize_series` everywhere an `npi` column is written.

**F15. Two name normalizers inside one schema (rule 6).** LEIE `name_key` and owner keys use `clean_data._normalize_name` (`integrate.py:454-457,516`); every enforcement adapter and `name_resolved_exclusions` use `resolve_entities.norm_org_name`. `build_excluded_in_edges:181-210` compares `owner_key` (one normalizer) to a `name_to_excl` index built from both. Fix: one normalizer for `name_key` across adapters, or normalize again inside `build_exclusion_nodes`.

**F16. `_label_source` defaults to `"leie"`** (`provider_features_export.py:1118`): any unrecognised prefix (`affiliated:`, a future adapter) is recorded as LEIE provenance in `exclusion_label_sources`, which the registry, `label_ablation` (per-source removal) and `broadcast_only_mask` read. Fix: return `f"other:{t.split(':')[0][:20]}"`.

**F17. Forward vs training asymmetries that are not leaks but should be stated in the read-out.** (a) `build_forward_case_label:194` fills an unknown `conduct_start` with the cut year (counts), `case_labels:475` drops it (training); (b) the forward ruler is built with `affiliations=None` and `max_case_npis=100` defaults (`prospective_label.py:170`) — no affiliated_individual tier on the ruler; (c) `merge_affiliated:171-179` adds a biller whose affiliated exclusion predates the cutoff as an ordinary NEGATIVE (not `known`), and the training side has no `affiliated_conviction` basis at all (`affiliated_convictions.parquet` is not read by the export); (d) `eval_frame:215-216` drops every NPI with non-empty `case_ids`, including `affiliated_individual` / `docket_case` metadata rows that were never training positives.

**F18. `cia_case_rows` conduct window is always inferred** (`cia.py:136-137` summary carries no year → `extract_conduct_window` falls back to `[eff-4, eff]`, `case_labels.py:49-50`), so every CIA effective within 4 years after the cutoff passes `conduct_start <= cut_year` on the ruler. Expected by design; flag it in the ruler's `fca_basis` so a read-out can separate inferred-window hits.

**F19. Duplicated constants that can drift.** `EXCLUSION_COLS` is defined in `medicare_revocations.py:27`, `state_terminations.py:44`, `opensanctions.py:31`, `preclusion.py:30`, `state_licensing.py:28`, `research_evidence.py` (imported from one of them by `label_expansion.py:722`); `_cutoff_ts` in `prospective_label.py:50` and `accuracy_diagnostics.py:172`; `BROADCAST_BASES` in `case_labels.py:570` (frozenset) and `company_list.py:75` (tuple). Fix: one `enforcement/schema.py` for the column list; import `_cutoff_ts` from `prospective_label`.

**F20. `tier_label` counts undated exclusion nodes for the current-day scorer (`company_list.py:220-226`, cutoff None) while the as-of graph drops them (`temporal_sources.py:37-48`)** — a current-day run and a frozen run at the same instant do not share a positive set. Documented; worth a line in `training_label_policy.json` (`n_undated_positives`).

### Verified correct (no finding)

- Index-URL gate on every case path: `case_labels.py:245-251`, `manus_lanes.py:326`, `research_evidence.py:256,317`.
- `usable==0` rows never label (`case_labels.py:233-242`); dismissals/acquittals quarantine the charged row (`research_evidence.clear_charged_rows:448-467`).
- Self-disclosures never positive (`case_labels.py:342-346`); docket/charged/commercial never flip the hard label (`provider_features_export.py:413-446`); `prospective_label` refuses charged (`:289`) and `build_forward_case_label` filters `label_source == "doj_case"` (`:173`).
- The forward FCA ruler needs an outcome on every tier incl. org_billing (`case_labels.py:442-444`).
- `tier_label` month-start, strict `<=`; `_training_label` raises on a tier policy without nodes (`company_list.py:256-262`); ambiguous rows are masked, never negatives (`:397-402`).
- Fuzzy tier: unverified rows demoted (`defendant_resolver.retire_unverified_fuzzy:1128-1147`), verdict-carried promotions require a Luhn NPI (`apply_verification:1107-1108`), strict export accepts only `_verified`/`fuzzy_verified` at moderate (`fca_label_export.py:63-71,139-141`).
- `research_evidence.load_exclusion_npis:518-523` enforces Luhn + confidence + NPI ∈ our candidates.
- §E deactivation/death widening is own-event (`provider_features_export.py:587-600`) and as-of safe by construction (post-cutoff months cannot exist in the as-of fact: `smoking_gun_timeline` reads `asof_spend_p`, `:2672`).

### Suggested fix order
F3 (one-line, blocks v18) → F1 (one-line + test; re-register) → F2 (loader tag) → F4/F12 (one outcome/fraud-shape definition) → F5/F6/F7/F9 (gates on every research path) → F8/F15/F13 (loader provenance + dedup + normalizer) → the rest.
