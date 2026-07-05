# Output Methodology — what we ship, to whom, under what gates

_The review of the output contract. Trigger: the planned blanket ">$5M recovery" filter
would have been applied to a provider's OWN billing — which throws away exactly the
orchestrator cases (the $136M telemedicine nurse billed almost nothing herself). This doc
fixes the design before it's built and states the full contract for both outputs._

## The core distinction: two outputs, two contracts

**Output 1 — the training/scoring feed (to Travis's model).**
NO gates of any kind. The supervised model needs the full universe — positives, unlabeled,
big, small, thin — because gating the input biases the model toward whatever the gate
encoded (that is the circular-negatives lesson). Rules:

1. **Full universe, one row per NPI.** No candidate gate, no payer filter, no dollar
   floor, no anomaly threshold. (Already the export's behavior — reaffirmed as contract.)
2. **Dollars are FEATURES here, never filters.** `net_paid`/`gross_paid` ship as columns;
   the model learns what size means. The "size never drives rank" rule governs the lead
   list, not the feature set.
3. **NULL means absent-from-source.** No zero-imputation anywhere in the feed (subscore
   engine now complies). LightGBM handles NaN natively.
4. **Leakage tiers travel with the data** (`leakage_hard` / `leakage_adjacent` in the
   manifest) and the anomaly composite / signals-tripped / priority columns are marked
   convenience-only — never training inputs.
5. **Labels ship with provenance and metadata** (`exclusion_label_sources`,
   `confirmed_clean` anchors, weak-label columns as targets-not-features), PU framing
   documented.
6. **`group_id` + `assessable`** so Travis can do group-aware CV and down-weight
   thin-evidence rows instead of us silently dropping them.
7. **Evaluation protocol asks** (fair random negatives, group split, out-of-time when
   snapshots exist) — now runnable by us via `model_a/retrospective.py` instead of only
   requested of him.

**Output 2 — the investigative lead/case output (to humans + Model C).**
Rank on size-ADJUSTED anomaly (never raw dollars), THEN gate on recovery potential.
The gate is where the fix below lands.

## The fix: the recovery gate must be scheme-aware, not own-billing

"Worth >$5M" is about the CASE, and different schemes put the dollars in different
places. Gating every scheme on the provider's own billed dollars silently deletes the
orchestrator schemes. The gate's dollar basis per scheme:

| Exposure basis | Schemes | Where the dollars live | Data source for the basis |
|---|---|---|---|
| **own_billing** | upcoding, overutilization, payment_outlier, single_service_mill, rapid_ramp, specialty_mismatch, drug_outlier, pill_mill | the provider's own claims | spending fact (`exposure.py` annual payments) |
| **influenced_dollars** | dme_ring (ordering/referring), pharma_kickback, telemedicine-style orchestration | claims BILLED BY OTHERS on this provider's orders/inducement | DMEPOS **by-Referring** PUF (ordered DME dollars per referrer — the adapter's actual grain), `op_payment_utilization_corr` × Part D cost (induced drug dollars), order/referring pairs when the eligibility file lands |
| **ring_aggregate** | ownership_integrity, saturation_fraud (ring form), any scheme at org/ring grain | the RING's combined billing, not any single NPI | org rollup + graph ring membership (the 555 pattern) |
| **facility_program** | hospice_ineligibility, worthless_services, cost_report_fraud | the facility's program payments at CCN/org grain | exposure at org grain via ccn→npi→org |

Gate rules that follow:
- **own_billing** schemes: gate at the recovery threshold on scoped own-dollars ×
  scheme recovery multiplier (as planned).
- **influenced_dollars** schemes: gate on the INFLUENCED flow, never own billing. A
  referrer with $40K own billing ordering $8M of DME passes; today's design would have
  dropped them.
- **ring_aggregate**: gate at the ring level; members inherit the ring's pass/fail.
- A lead passing under ANY applicable basis passes (schemes are evaluated on their own
  basis, then max over schemes).
- The gate NEVER re-ranks — ranking stays anomaly-first; the gate only decides what
  surfaces, per section A of the Run 2 plan.

Single source of truth in code: `SCHEME_EXPOSURE_BASIS` in `model_a/sector_priors.py`
(consumed by Model C / ERV and the lead gate when built).

## Corrections this review surfaced
- **DMEPOS registry label was wrong (mine):** the built adapter reads `Rfrg_NPI` × HCPCS —
  the **by-Referring-Provider-and-Service** layout. The earlier failed file was the
  by-referring *summary* (no HCPCS column), not evidence the adapter wants by-supplier.
  The by-referring file is ALSO the influenced-dollars basis for dme_ring — one file, two
  uses. (A by-supplier file remains desirable later for supplier-side signals; separate
  entry, not a replacement.)
- The exposure module (`exposure.py`) computes own-billing exposure only; influenced /
  ring / facility bases are follow-on builds it should absorb (contract above).

## What still gates on judgment (unchanged)
Explainability (named drivers on every lead), the legal frame (leads, never accusations),
and counsel review before anything leaves the building.
