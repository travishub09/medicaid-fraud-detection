# 03 — Entity Resolution and the Master Graph

This is the foundation. Get resolution wrong and every model downstream is noise.
**Increment 1 of the build implements this** — see `src/entity_graph/` and
`tests/test_entity_graph.py`.

## Why this is the hard part

Two largely disjoint identity worlds have to meet:
- the **claims/fraud world** is NPI-centric (providers, orgs, facilities by NPI, CCN, TIN);
- the **people/relator world** is employer-string-centric (coders, billers, compliance,
  finance staff who often have no NPI and exist only in workforce data, tied to an
  employer by a messy text string).

They join at exactly one place: the **organization node**. So the single most
important and most difficult linkage is resolving a workforce employer string
("Acme Health Partners LLC") to the same canonical organization the claims data
knows as a TIN and a set of NPIs. Nail it and a fraud signal connects to a reachable
human; miss it and the two halves of the business never touch.

## Node types

| Node | Primary keys | Source |
|---|---|---|
| Provider (individual) | NPI (Type 1), PAC ID | NPPES, PECOS, Part B |
| Organization | Org NPI (Type 2), TIN, Enrollment ID | PECOS, NPPES, claims |
| Facility | CCN | POS file, cost reports, Care Compare |
| Owner | name/EIN + role | PECOS ownership, CMS All-Owners, 990 Sch R, SEC |
| Person (workforce) | vendor IDs (PDL/LinkedIn), email | people data, WARN, dockets |
| Manufacturer | OP manufacturer ID | Open Payments |
| ExclusionEvent | LEIE/SAM record id | LEIE, SAM.gov |
| EnforcementCase | DOJ/OIG/court id | DOJ, OIG, PACER |

Geography (ZIP/county/HRR) is a node attribute and a blocking key, not its own node.

## Edge types (note the temporal ones)

| Edge | From → To | Attributes |
|---|---|---|
| reassigns_billing_to / member_of | Provider → Org | period |
| owned_by | Org → Owner | pct, role, start, end |
| also_owns | Owner → Org | pct |
| refers_to | Provider → Provider/Org | shared-patient volume, period |
| pays | Manufacturer → Provider | amount, category, product, year |
| employed_by | Person → Org | role, start, end (often inferred) |
| co_located_with | Org ↔ Org | shared address/phone |
| excluded_in | Provider/Org → ExclusionEvent | date, basis |
| named_in | Provider/Org → EnforcementCase | role, outcome, amount |

Employment and ownership edges carry **validity intervals**, and every query is
**point-in-time correct** — "who worked here when the anomaly occurred" is a
different question than "who works here now," and both matter (the tenure-overlap
gate in Model B depends on this).

## Matching methodology — a two-layer resolver

**Deterministic layer.** Exact joins on hard keys wherever they co-occur (NPI↔PAC via
PECOS, NPI↔Org via reassignment, CCN↔org via POS/cost reports). Resolves the bulk of
the claims world cleanly.

**Probabilistic layer** for soft matches (org-name canonicalization, person↔employer):
- **Blocking** to avoid all-pairs: ZIP3 + a name token, or a phonetic key (Double
  Metaphone), or standardized address.
- **Similarity:** Jaro-Winkler + token-set ratio on names; libpostal address
  standardization + geocoding; then a **Fellegi-Sunter** model (Splink is the scalable
  open-source choice; dedupe / Zingg are alternatives) that learns per-field weights.
- **Decision bands:** auto-accept above a high threshold, auto-reject below a low one,
  route the middle to human review. Persist a **confidence score and match provenance**
  on every resolved link.

**Org-name canonicalization** deserves its own pipeline: normalize legal suffixes
(LLC, Inc, PC), strip/map DBAs, then cluster high-confidence pairwise matches via
connected components into a canonical Org node carrying all aliases. This crosswalk is
what lets a workforce employer string resolve to the claims-side organization.

**People linkage & privacy.** Match on normalized employer name + location + role
plausibility. Keep raw identifiers minimal; tokenize sensitive joins rather than
holding raw PII. Treat "this person likely has knowledge of fraud at employer X" as
sensitive from creation.

## Storage

Persist resolved entities/edges in a graph DB (Neo4j or TigerGraph) for interactive
exploration; keep relational/columnar feature tables in the lakehouse for training;
sync graph-derived features (centrality, community, excluded-party distance) back to
the feature store on each refresh. **In this repo the graph is relational + NetworkX**;
Neo4j export is optional (`neo4j_export.py`).

## Ring & network detection

The graph catches what single-entity scoring cannot. Illustrative Cypher (mirrored by
the relational functions in `ring_detection.py`):

```cypher
// Shared-address shell clusters: recently-enrolled, thin-history orgs at one address
MATCH (o1:Org)-[:co_located_with]-(o2:Org)
WHERE o1.enroll_age_months < 18 AND o2.enroll_age_months < 18
WITH o1.address AS addr, collect(DISTINCT o1)+collect(DISTINCT o2) AS orgs
WHERE size(orgs) >= 3
RETURN addr, orgs

// Common-owner clusters, weighted up if an exclusion sits in the network
MATCH (ow:Owner)<-[:owned_by]-(o:Org)
WITH ow, count(o) AS n, collect(o) AS orgs
WHERE n >= 4
OPTIONAL MATCH (o2)-[:excluded_in]->(:ExclusionEvent) WHERE o2 IN orgs
RETURN ow, n, count(o2) AS excluded_in_network

// Excluded-party proximity: any active biller within two hops of an exclusion
MATCH p = (b:Org)-[*1..2]-(x)-[:excluded_in]->(:ExclusionEvent)
WHERE b.active_biller = true
RETURN b, length(p) AS hops, x
```

Beyond explicit patterns: run **community detection** (Louvain/Leiden) to surface dense
subgraphs and **betweenness centrality** to find the orchestrating node. These become
inputs to Model A.

## Current state in this repo (Increment 1 — built)

`src/entity_graph/` (run `python -m src.entity_graph --fixture --out /tmp/graph_out`):

| Module | What it does |
|---|---|
| `build_nodes.py` | canonical Provider / Owner / Exclusion node tables |
| `resolve_entities.py` | deterministic resolver + org-name canonicalization (PAC → shared-owner → exact-name), generalizing `leads/company_rollup.py`; emits canonical Organization nodes with aliases, basis, and a confidence band |
| `build_edges.py` | `member_of`, `owned_by`, `excluded_in`, `co_located_with` edges (temporal where dates exist) |
| `graph_features.py` | `excluded_party_distance` (BFS), `related_party_density`, `co_location_cluster_size`, `shell_score`, Louvain `community_id`, `betweenness` |
| `ring_detection.py` | shared-address shells, common-owner clusters, excluded-party proximity; `referral_rings` gated until referral data exists |
| `__main__.py` | orchestrator with row-count / no-fan-out assertions + `GRAPH_REPORT.md` |

**Reuses:** the shared normalizers in `src/attempt_2/clean_data.py`
(`_normalize_name`, `_standardize_address`) and the integration outputs from
`integrate.py`. Tested end-to-end on a synthetic fixture (`tests/test_entity_graph.py`,
9 tests: node uniqueness, alias/PAC collapse, no fan-out, exclusion distance, shell
and common-owner detection).

### Scale guards (from adversarial testing)
- **Betweenness** uses the seeded k-sample approximation above 2,000 nodes
  (exact is O(V·E) and never finishes at 617k providers); ranking quality is
  what the feature needs (`graph_features._betweenness`).
- **Mega-address clusters** (registered agents / virtual offices with hundreds+
  of orgs) emit a linear star instead of quadratic all-pairs `co_located_with`
  edges; true cluster size rides on every edge and in the ring-detection table
  (`build_edges.build_co_located_edges`).
- **Org-name keys fold unicode** (NFKD→ASCII) so accented aliases merge
  ("Café Salud" = "CAFE SALUD") — `resolve_entities.norm_org_name`.

### Next increments here
- **Probabilistic person↔employer resolution** (`person_resolver.py`, stub): the bridge
  to Model B; needs licensed people-data + Splink + the privacy guardrails above, and
  builds the temporal `employed_by` edges the tenure gate queries.
- **`refers_to` / `pays` edges:** need referral-pair and Open Payments ingestion.
- **Optional Neo4j export** (`neo4j_export.py`, stub) for interactive Cypher.
