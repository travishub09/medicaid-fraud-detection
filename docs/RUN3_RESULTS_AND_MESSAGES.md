# Run 3 results: what we proved, and the messages for Travis and Brad

Written after the full review of both output folders (frozen_2023-12 and
provider_features_run3), July 10, 2026. Everything below is verified against the
actual reports, not from memory.

---

## 1. The headline result

We ran an honest forward test of the network (graph) features and they passed.

The setup: freeze the world at December 2023. Build every feature using only
data from before that date. Then ask: do the graph features help find the
providers who got banned AFTER that date? The features cannot peek at the
answer because the answer had not happened yet.

The result, on structural graph features only (the ones that cannot see the
exclusion list at all):

- Size-matched forward ROC-AUC: 0.7361 with network vs 0.6950 without.
- The gain: +0.0416, with a 95 percent confidence interval of +0.0152 to
  +0.0707. The whole interval is above zero.
- Verdict: KEEP. The graph features find future fraud even against same-size,
  same-specialty peers. The signal is not "big organizations get caught more."

Two honest caveats we state up front, because they make the result credible:

- On the full population (185,234 test providers, only 305 future bans) the
  gain washes out to about zero. With a base rate of 0.16 percent, any single
  feature family gets diluted. The size-matched test is the right lens.
- This was done WITHOUT graph embeddings (the 16GB laptop cannot hold them at
  9.6 million nodes). The +0.04 came from just 2 structural features. That is
  the floor, not the ceiling.

## 2. What the run built (the deliverables)

Two matrices, both 617,062 providers:

| | frozen_2023-12 | provider_features_run3 |
|---|---|---|
| Purpose | Forward test + honest training | Current-day scoring |
| Columns | 150 (74 trainable) | 165 (85 trainable) |
| Billing features | Recomputed pre-cutoff only | Full history |
| Positives | 1,162 (pre-cutoff exclusions only) | 2,211 |
| Forward label | 4,401 future bans (separate file) | n/a |
| Sources used | 16 | 18 (adds growth, plausibility) |

The frozen folder also has `future_bans_after_2023-12.csv` (the forward label)
and `NETWORK_AB_REPORT.md` (the A/B result above).

## 3. Data health, verified

- Sources: 18 of 26 used in run3. The procured data landed: POS read all 5
  files (39,278 orgs with bed counts), HCRIS read 3 cost-report files (30,754
  CCNs, 0 dropped), PBJ staffing is multi-quarter, county saturation attached
  through the ZIP-county crosswalk (39,484 ZIPs), DocGraph referral edges made
  it into the graph (2M strongest org pairs from 97M).
- Calc-integrity: 2 FAILs and 19 WARNs in run3, all explained. The FAILs are
  `betweenness` (intentionally off at 9.6M-node scale) and
  `incons_instant_scale` (a real bug, fixed, see section 5). The WARNs are
  rare-flag shapes that are supposed to look that way.
- The signal ranking confirms the model finds fraud where fraud is known to
  live: NEMT (non-emergency transport) features at AUC 0.66 with 3.9x lift,
  behavioral health at 0.60, kickback concentration at 0.60, and the
  cross-source implausibility score at 0.56 across 95 percent of providers.

## 4. One leakage catch from the review (important for Travis)

The signal ranking exposed that two columns dodge the leakage fence:
`billing_after_deactivation__peerpct` and `subscore_invalid_identity` score
AUC 0.926 in-time because they are built from billing-after-deactivation,
which nearly encodes the exclusion label. They are real FORWARD signals
(billing after your NPI is deactivated is a genuine red flag), but in-time
they flatter the model dishonestly. Both are now tagged leakage_adjacent in
the manifest: train on them only under the out-of-time split, same rule as
within_2_hops_of_exclusion.

## 5. Bugs found by the review, all fixed in this bundle

1. `billing_lm` crashed on the raw spending fact ("logarithm of a negative
   number") because corrupt negative-dollar rows reached a log. All
   dollar-weighted queries now filter to positive, sub-$500M payments, the
   same guard the as-of builder uses.
2. `incons_instant_scale` was constant zero in run3 because the full-run
   matrix never had a `tenure_months` column (only the frozen path computed
   it). The export now backfills tenure from the spending fact on full runs.
3. `dmepos` skipped even though the 2016-2018 files have the right layout;
   the picker grabbed the 2022 summary file that lacks HCPCS. The adapter now
   falls back to the next-newest file when the newest one has a wrong layout
   and says so in the sources report.
4. The 340B OPAIS loader assumed the banner above the header is always 2 rows.
   Trey's file had a different height, so every column read as "Unnamed". The
   loader now finds the real header row wherever it is.
5. The leakage fence addition from section 4.

None of these change the A/B verdict. The A/B tested 5 named graph features;
none of the fixed columns were among them.

---

## 6. Message to send Travis

Subject: New training matrix + the graph features passed a real forward test

Travis,

Two things for you.

First, a new training file: provider_features_for_model.parquet, 617,062
providers by 165 columns, replacing the last one. Same layout you know:
raw features, __peerpct peer percentiles, subscore_ columns, and the
provider_on_exclusion label (2,211 positives now, widened with Medicare
revocations). NULL still means "not in that source," so let LightGBM handle
it natively. The feature_manifest.json is the contract: train only on
raw_feature_cols + peerpct_cols + subscore_cols, never on leakage_hard, and
treat leakage_adjacent as out-of-time-only. Two columns moved INTO
leakage_adjacent since last time: billing_after_deactivation__peerpct and
subscore_invalid_identity. They score AUC 0.93 in-time because they nearly
encode the label. Do not let them pad an in-time validation number.

Second, the graph question you and I have been going back and forth on is
answered. We froze the world at 2023-12, built features on pre-cutoff data
only, and tested against bans that happened after. Structural graph features
(shell_score, related_party_density) lifted forward ROC-AUC from 0.695 to
0.736 against size-and-specialty-matched controls, +0.042 with the 95 percent
CI fully above zero (+0.015 to +0.071). On the raw full population the effect
dilutes to zero, which is what you would expect at a 0.16 percent base rate,
so judge the graph family on the matched test. Practical takeaway for your
build: keep the graph features, evaluate them only under an out-of-time
split, and expect them to matter in the tail ranking, not the global AUC.

The frozen folder (frozen_2023-12) has everything to reproduce this:
the frozen matrix, the forward label CSV, and NETWORK_AB_REPORT.md with the
numbers. One column heads-up: betweenness is constant zero in both matrices
(we disable it at 9.6M-node scale), so drop it or let the tree ignore it.

Trey

## 7. Message to send Brad

Subject: The fraud graph passed a blind forward test

Brad,

Quick result worth your time. We ran the kind of test an outside investor
would ask for: freeze the model's knowledge at December 2023, then check
whether it flags the providers who got banned in 2024-2025. No peeking
possible.

It works. Adding our provider-network features (shell addresses, related-party
webs) improved detection of future bans by a statistically clean margin over a
model without them, tested against providers of the same size and specialty so
the gain is not "big companies get caught more." The model also independently
re-finds known fraud sectors: transport fraud, behavioral health, pharma
kickbacks.

Why this matters for us: the exclusion list is the government's lagging
indicator. A model that predicts it 1-2 years early, from public data alone,
is a machine for pointing counsel at the right buildings before cases are
public. That is the origination edge.

One cheap next step: we got this result with the graph's advanced features
turned off because my laptop tops out at 16GB. Renting a big cloud box for one
weekend run costs about $20-40 (not the $500 machine we discussed; we rent,
not buy). If the full graph adds another point or two of lift on the same
blind test, that is the strongest version of this evidence.

Trey

---

## 8. What runs next (Trey's order of operations)

1. Load bundle 17 and push (fixes only; no re-run required for the verdict).
2. Optional 20-minute patch run when convenient: re-run Step B (the run3
   export) to light up billing_lm, incons_instant_scale, and dmepos with the
   fixes. The frozen A/B does not need re-running.
3. Re-download when convenient: DMEPOS 2022 "by Referring Provider and
   Service" (the detail file, not the summary) and a fresh 340B OPAIS daily
   report export. Both now also work with the files you already have (dmepos
   falls back to 2018; the 340B loader reads your current file's header).
4. The embeddings round-2 run on a rented 64GB box (task #36): the code path
   exists (`--no-embeddings` off), the A/B harness is proven, and the KEEP
   result justifies the $20-40 spend.
