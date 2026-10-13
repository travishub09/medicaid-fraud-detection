# Accuracy read-out: frozen_2021-12_v13 (2026-10-13)

_The second VM run: v12's matrix recipe (full embeddings, ten seeds, hard-tier training, 40 sources, 22 schemes, 0 calc FAILs, 617,062 rows, 248 trainable features) with the v13 code: the pursuit order kept at dollar after the sweep, the large book graded as co-primary, and a curated national-name list added to the institutional rule. Same ruler as v12 (509 FCA forward positives, 614,401 eval rows). Leads for counsel review, never accusations._

## Verdict: HOLD, and the cause is the v13 code, not the model

| check | bar | v13 | v12 (same matrix recipe) |
|---|---|---|---|
| large_hits50 | >= 2 | **1** (pool 2) | 3 (pool 5) |
| large_prec50_lo > large_base50 | | fail | pass |
| hits100 (pursuit) | >= 4 | **1** (pool 3) | 4 (pool 8) |
| hits250 (pursuit) | >= 6 | **2** | 7 |
| lead_ge_gap | >= 0.5 | n/a (0 hits in the top 50) | 1.0 |
| inst_share50 | <= 0.4 | 0 | 0 |
| top50_jaccard | >= 0.6 | 0.607 (pass) | 0.616 |
| seeds / groups / broadcast / train | | all pass | all pass |

Pursuit book, FCA ruler: 0 / 0 / 1 / 2 at 16 / 50 / 100 / 250 (random 0.0 / 0.1 / 0.2 / 0.6). Large book: 0 / 1 / 2 / 2 (random 0.1 / 0.2 / 0.3 / 0.8; p at 100 = 0.025). NPI grain unchanged from v12 (top 1 percent FCA lift 6.3 vs 6.7). Ledger: 8 FAIL (the three stale editions, the five checks), 75 WARN.

## What happened

The ruler did not move (509 positives both runs) and the scorer did not move (NPI lift 6.3 vs 6.7, Jaccard 0.61 both). What moved is the BOOK. The v13 code added a curated list of hospital systems, national labs and national chains to the `entity_segment` institutional rule, to stop the dossier naming Mayo Clinic and Ameritox. That rule also defines the pursuit book, so 43 organizations left it (1,395 to 1,352; the large book 658 to 634), and FIVE OF THE EIGHT FCA hits went with them: the forward hits the model had been finding were national home-health, DME and lab chains that settled qui tam cases inside the horizon. The rule conflated two different things:

- **Do not send a PI** (the consensus-book read of 2026-09-16): a hospital system or a national chain is not a door a field investigator stands outside of. True, and the dossier's PI target pick should enforce it.
- **Not a case**: false. A national chain that settles for $100M on an employee relator's complaint is exactly a qui tam case, and under the FCA ruler it is a hit. Removing it from the graded book removes real outcomes from the grade.

Two more facts the shrunken book exposes, both worth knowing:

1. **Inside the owner-run operator class the model is near chance.** With the chains out, 1,352 operator orgs hold 3 FCA hits in 36 months and the list finds 0, 0, 1, 2 of them (random 0, 0.1, 0.2, 0.6). The precision the series has reported since v10 lives in the chains and the large billers. This is not a surprise given the label: DOJ press releases about owner-run operators name PEOPLE (criminal cases), and the resolver's recall on those is the open recall problem; the chains are named as companies and resolve cleanly.
2. **The dossier still named Mayo, SSM, Sutter, Assurex, Ameritox and Alere**, because the `--operator-book` target pick in `build()` has its own institutional mask and the v13 list was only wired into `build_priority_list`. So the v13 code removed the chains from where they belong (the grade) and left them where they do not (the dossier). Not dispatched.

## Fixed in code (bundle 305)

- The national-name list is OUT of `entity_segment` and the graded books; `build_priority_list` writes it as a `national_chain` flag column on every list instead, so the reader sees it.
- The list is IN the dossier's `--operator-book` target pick (`build()`), beside the institutional cue words and the 25-provider rule. A chain is a case; it is not a PI job.
- A test pins both placements.

## v14 (the same matrix, the book restored)

v13's matrix is sound and identical in recipe to v12. The honest next step is not a retrain but a re-grade of v13's matrix with the book definition restored, registered as v14 under the v13 checks (large_hits50 >= 2, hits100 >= 4, hits250 >= 6, top50_jaccard >= 0.6, train = hard). It needs the VM for about an hour: rebuild the priority list, run the four diagnostics, the dossier (operator book, now chain-free), register, ledger. Expected: v12's numbers back (1 / 1 / 4 / 7 pursuit, 3 / 5 / 5 large), with the dossier naming no institution for the first time.

Beyond v14 the finding stands: the model's demonstrated forward precision is in national chains and large billers. For the owner-run class the lever is label recall (the research residue, the state MFCU sweeps, the PurpleLab join), not the scorer.
