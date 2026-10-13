# Accuracy read-out: frozen_2021-12_v14 (2026-10-13)

_v13's matrix re-graded under the restored book definition (`ops/vm/regrade_from.sh v14 v13 2021-12`: no retrain; the national-name list is a flag column on the lists and a mask on the dossier's target pick only). Same recipe as v12 and v13 (full embeddings, ten seeds, train = hard with 1,205 positives of which 1,082 DOJ-case, 40 sources, 22 schemes, 0 calc FAILs, 617,062 rows, 248 trainable features), same ruler (509 FCA forward positives, 614,401 eval rows). Leads for counsel review, never accusations._

## Verdict: HOLD on one check, seven of eight pass

| check | bar | v14 | v12 (same recipe, own scorer) | v13 |
|---|---|---|---|---|
| large_hits50 | >= 2 | **2** (p 0.049) | 3 | 1 |
| large_prec50_lo > large_base50 | | pass (0.011 > 0.008) | pass | fail |
| hits100 (pursuit) | >= 4 | **3** (p 0.015) | 4 | 1 |
| hits250 (pursuit) | >= 6 | 7 (p < 0.001) | 7 | 2 |
| lead_ge_gap | >= 0.5 | 1.0 | 1.0 | n/a |
| inst_share50 | <= 0.4 | 0 | 0 | 0 |
| top50_jaccard | >= 0.6 | 0.607 | 0.616 | 0.607 |
| seeds / groups / broadcast / train | | all pass | all pass | all pass |

Pursuit book, FCA ruler: 1 / 1 / 3 / 7 at 16 / 50 / 100 / 250 (random 0.1 / 0.3 / 0.6 / 1.4). Large book: 1 / 2 / 4 / 5 (random 0.1 / 0.4 / 0.8 / 1.9; p 0.049 / 0.002 / 0.008 at 50 / 100 / 250). Median lead 29 months on the pursuit book, 16.5 on the large book. NPI grain: top 1 percent FCA lift 3.7 on the shipped scores (the refit in v12 read 6.7; this re-grade reads the shipped list, so the two are not the same measurement). Ledger: 4 FAIL (the three stale editions, hits100), 75 WARN.

## What the re-grade settled

1. **The v13 HOLD was the code.** With the chains back in the graded book, the eight-hit pool and the v12-shaped numbers return: 7 at 250 on the pursuit book, 2 at 50 on the large book. The three facility / PBJ / POS editions dated after the freeze remain the only recurring FAILs and are a procurement item, not a model one.
2. **The one miss is a single hit at the 100 boundary.** v12 found its fourth hit inside the top 100; v14 finds it between 100 and 250. Same matrix recipe, a different scorer draw (v13's seeds), so the 100 budget sits on the noise floor the Jaccard already describes (0.61: 30 of the top 50 orgs hold across draws, 20 move).
3. **The dossier is chain-free for the first time.** The ten operator-book targets are home-health, toxicology, pain, hospice and spine operators; no hospital system or national lab is named. Not dispatched (HOLD rule: no Manus credits on a HOLD run).
4. **The order sweep, second reading.** "Basis-backed first in the top 50" on the dollar-weighted score reads 1 / 3 / 3 / 7 against the shipped 1 / 1 / 3 / 7: it wins the 50 budget and ties the rest. In v12 it won 50 and lost 100 (3 vs 4). Two runs, one win and one split: the sort stays dollar, and the v15 sweep carries the row again; a third reading that wins or ties every budget moves the ship order.
5. **Broadcast A/B (single-seed refits):** weight 1 / 2 / 2 / 3, mask 0 / 1 / 1 / 3. Weight stays, as in v11 and v12.
6. **Label noise (learning loop):** 141 suspect positives, the largest by dollars all `org_billing` broadcasts of hospital-system and dental-chain settlements (Easter Seals NH, Community Health Systems, AnMed, Kool Smiles, Dental Dreams). The weight budget already dilutes them; the case-size rule in docs/LABEL_EXPANSION_PLAN section 2 (institutional settlements as metadata, not positives, for the owner-run objective) is the structural answer and belongs in the v15 pre-registration.

## v15

Everything that changed since v13 is label-side (bundles 307 to 312: affiliated convictions, the CIA roster, name-only exclusion resolution with the UPIN join, self-disclosures out, the charged tier and the charged-only case rows, the org-resolution fix for convictions without a stated dollar amount). v15 is a full VM run on the new label, pre-registered under the v14 checks plus `train = hard` versus the `hard+charged` A/B on the same matrix, with the three rulers (FCA, qui tam, the widened exclusion ruler) as a sweep. Grade it on hits@50 on the large book and hits@100 / @250 on the pursuit book; the 100 bar stays at 4.
