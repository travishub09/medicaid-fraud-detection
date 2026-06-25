# Provider-Signal Architecture — a ground-up redesign

**Status:** strategy / roadmap (not yet built, except where noted).
**Purpose:** how to produce provider-level attributes that let a graph-based tree
model (Travis's) actually separate **known fraud actors** from **known
non-offenders** — rather than separating "statistically weird" from "normal."
This document is the north star; `PROVIDER_FEATURES_FOR_MODEL.md` documents what
exists today, and `DATA_ACQUISITION_GUIDE.md` what to procure.

---

## The core reframe

Everything we ship today is, at bottom, a **better anomaly detector**. But the four
honest limitations (label ceiling, peer-grouping noise, org→NPI smearing, scale)
all point at the same three root problems, and anomaly-engineering doesn't fix any
of them:

1. **Labels are weak.** "On an exclusion list" is a binary, untyped, untimed,
   after-the-fact flag for *caught* fraud. It says nothing about *what* the
   provider did or *when*.
2. **There are no real negatives.** Treating "not on a list" as clean is false and
   poisons the contrast the model is supposed to learn.
3. **Anomaly ≠ fraud.** A rural solo provider is anomalous and innocent; a
   sophisticated ring bills *perfectly normally per-provider* and is only visible
   *relationally*.

So the redesign organizes around three pillars the current architecture lacks —
**point-in-time correctness, outcome-derived labels, and graph representation
learning** — and reframes the deliverable as a **case-control contrast**, which is
literally what "compare fraud actors to known non-offenders" means.

---

## Pillar 1 — A bitemporal provider feature store (kills leakage by design)

Today leakage is a *caveat in a manifest*. Make it an *architectural guarantee*.
Every attribute and every label carries a **valid-time**, so we can reconstruct
"what did this provider look like as of 2018-06" using only what was knowable then.

- **Out-of-time validation becomes the default, not an option.** Train on features
  dated before the fraud window; test on whether we'd have flagged the actor
  *before* they were caught. That is the only number worth defending to counsel.
- The `leakage_hard` / `leakage_adjacent` distinction dissolves — a feature that
  postdates the label date simply isn't visible at training time.
- Mechanically: timestamp every source and snapshot monthly. **(BUILT —
  `model_a/feature_store.py`.)** The export stamps each matrix with a valid-time
  (`--snapshot --asof`), `asof_join` reconstructs a provider's features from the
  latest snapshot *strictly before* a label date (and reports events with no prior
  snapshot rather than leaking future data), and `temporal_split` produces the
  out-of-time train/test masks from the conduct year. History accumulates from the
  first snapshot forward; the remaining work is back-filling per-source valid-times.

---

## Pillar 2 — A label engine (outcomes + weak supervision + manufactured negatives)

Replace the single exclusion flag with a **label engine** that produces rich,
probabilistic, time-boxed labels:

- **Scheme-typed, time-boxed positives from DOJ / qui tam outcomes. (BUILT —
  `model_a/case_labels.py`.)** A settlement isn't "this NPI is bad" — it's "this NPI
  committed *upcoding* from *2016–2019* for *$4.2M*." The case DB is resolved case →
  org → member NPIs, scheme is carried, and a **conduct window** is extracted from
  the case text; the export folds these into the widened label as source `doj_case`
  and exposes `fraud_scheme` / `conduct_start` / `conduct_end` as label metadata, so
  you can validate per-scheme and train out-of-time today.
- **Weak supervision (Snorkel-style).** Instead of one hard label, write dozens of
  noisy *labeling functions* — "billed after death → fraud," "FQHC continuously
  enrolled 15yr → clean," "named in a DOJ indictment → fraud," "address geocodes to
  a mailbox store → suspicious," "UPIC-audited and cleared → clean." A generative
  label model fuses them into **probabilistic labels with confidence**, expanding
  labeled data 10–100× beyond LEIE and giving the tree soft labels to weight.
- **Manufactured high-confidence negatives. (BUILT — `clean_anchors.py`.)** We
  construct known non-offenders: VA / academic / FQHC-RHC (institutionally audited,
  ~zero base rate) or long-tenured providers, **and** benign on every anomaly
  concept, **and** with no exclusion/case/graph-proximity signal → `confirmed_clean`
  with a `clean_basis`. Conservative: ambiguous providers stay unlabeled, never
  forced negative.
- **Distant supervision from text.** NER (the GLiNER wrapper) over DOJ press
  releases, news, and dockets → defendant names → NPIs → more positives.

---

## Pillar 3 — Graph representation learning (the graph-based-tree keystone)

Travis's model is graph-tree-shaped, so this is the bridge. Fraud is *relational* —
rings, shared addresses, shell webs, referral funnels, sequential CHOWs — and
per-provider billing features are blind to it. The move: **learn dense node
embeddings on the entity graph and feed them in as columns.**

- **Node embeddings (DeepWalk/node2vec-style)** over the provider/owner/address/
  exclusion graph → a vector per node capturing "who you're connected to." A
  provider whose own billing is clean but who sits in a fraud-dense neighborhood
  lights up. Drops straight into LightGBM as N columns. **(Built — see below.)**
- **Fraud-proximity field** via personalized PageRank seeded from exclusion nodes →
  a continuous "fraud field strength" per node (guilt-by-association, principled).
  **(Built.)**
- **Structural motif features** — k-core, triangle count, clustering: is this
  provider the hub of a star (one owner, many single-NPI shells), in a tight
  clique, the apex of a referral pyramid? **(Built.)**
- **Temporal graph velocity** — how fast the ownership/address/reassignment
  structure churns (the fly-by-night signature at the graph level). *(Next.)*

This also **dissolves the org→NPI broadcast problem**: a provider that sits in the
graph gets its *own* embedding from its *own* position, instead of inheriting one
smeared org value.

---

## Pillar 4 — Attributes that separate fraud from anomaly

Four families, all fed raw + engineered to the tree:

1. **Expected-billing residuals (a "digital twin"). (BUILT — `expected_billing.py`.)**
   A robust per-taxonomy regression predicts *expected* billing from legitimate
   covariates (volume, claim lines, code breadth); fraud is the **unexplained excess
   after conditioning on everything legitimate** (`billing_residual`, one-sided).
   Unlike a raw peer percentile it does NOT punish the legitimately large — a
   high-volume referral center whose dollars track its volume scores low; a small
   provider billing 30× what its volume justifies scores high. *Next: add patient-mix
   and geography covariates.*
2. **Cross-source consistency checks. (BUILT — `consistency.py`.)** Fraud surfaces
   as *incoherence across independent systems*: an individual billing at
   institutional scale, a no-tenure provider already at full scale, a solo billing
   implausibly broad codes, a one-NPI "organization" at institutional scale →
   `consistency_flags`. **Hard to fake** — they require forging the NPPES/PECOS
   record AND the billing in sync. *Next: address-geocode reality and shared-phone/
   TIN checks as those sources land.*
3. **Behavioral-sequence signatures.** Treat the monthly claim stream as a sequence
   and extract the *lifecycle* — "ramp → harvest → dissolve," code-mix drift,
   change-point density. Shape, not just level.
4. **External-world grounding (the wild one).** Does the claimed footprint exist?
   Geocode + street/satellite of the billing address (real clinic vs. mailbox store
   vs. residential), business-registration records, web presence. "Bills $8M from a
   UPS-Store mailbox" is one of the strongest fraud priors there is — and it lives
   *outside* every CMS file.

---

## The moonshot — a foundation model for billing ("BillingBERT")

Pre-train a self-supervised transformer on the *sequence of claims* across all
providers (tokens = billing codes). Two payoffs:

- Every provider gets a **learned embedding** from millions of unlabeled sequences
  — rich features with no labels needed.
- **Surprisal as a feature**: model perplexity on a provider's billing = "how
  unlikely is this sequence." Fraud is often low-probability billing the model is
  "surprised" by. Turns the entire unlabeled universe into self-supervision.

---

## The output reframe — case-control matching

"Compare fraud actors to known non-offenders" is, literally, a **matched
case-control study**. **(BUILT — `case_control.py`.)** Instead of a 0.2%-positive
imbalanced soup, the export (`--case-control`) writes `provider_features_matched.parquet`:
each fraud actor paired with clean controls of the same specialty, geography, and
size (a taxonomy×state×size strata ladder that relaxes until enough controls exist,
recording the `match_tier`), drawn from the `confirmed_clean` anchors. The model
learns *what differs holding confounders fixed*, the way epidemiology isolates a
risk factor — sharper attributes and interpretable "fraud vs. matched-clean"
comparisons for counsel.

---

## Architecture, layered

```
raw sources ─► entity resolution (have) ─► BITEMPORAL feature store (P1: timestamp everything)
                                                │
        ┌───────────────────────────────────────┼───────────────────────────────┐
   behavioral/billing          GRAPH embeddings (P3, built)        consistency + external (P4)
   (have: subscores)           + motifs + fraud-field              + expected-residual twin
        └───────────────────────────────────────┼───────────────────────────────┘
                                          wide per-NPI matrix
                                                │
              LABEL ENGINE (P2): outcomes + weak supervision + manufactured negatives
                                                │
                       case-control matched training set ─► Travis's graph-tree model
                                                │
                       adjudications ─► label store ─► compounding moat (active learning)
```

---

## Sequencing (leverage order)

1. **Graph node embeddings into the export** — highest impact/effort; the graph is
   built, embeddings are N columns billing data can't produce, and it fixes the
   broadcast problem. **(Built — `entity_graph/graph_embeddings.py`.)**
2. **Bitemporal snapshotting + outcome-derived, scheme-typed labels** — makes
   validation honest and labels rich. **(BUILT — scheme-typed/time-boxed DOJ labels
   in `case_labels.py` + the point-in-time store in `feature_store.py`. Remaining:
   back-fill per-source valid-times as snapshot history accumulates.)**
3. **Weak-supervision label model + manufactured negatives** — **manufactured
   negatives BUILT (`clean_anchors.py`)**; the Snorkel-style weak-supervision label
   model is the remaining half.
4. **Case-control matching at export time** — **BUILT (`case_control.py`,
   `--case-control`).**
5. **Expected-billing residual twin BUILT (`expected_billing.py`)**; remaining
   Pillar-4 families (cross-source consistency, external grounding) and then the
   **foundation-model embedding** (moonshot).

Net: stop shipping "anomaly percentiles," start shipping **three independent views
of each provider — what they bill, who they're connected to, and whether their
story is internally consistent — all point-in-time correct, against a matched clean
control, with labels that say what and when.**

---

*Investigative-analytics framework. Outputs are statistical risk rankings of public
data, surfaced as model inputs and leads for human and counsel review — never
accusations or adjudications of any person or organization. The legal guardrails in
`docs/platform/01-legal-compliance.md` bind every pillar above (no PHI at intake,
audiences not call-lists, no dependency on data we cannot lawfully use).*
