# 05 — Model B: Whistleblower Identification and Propensity

For a Model-A-flagged organization, Model B ranks individuals by
`knowledge × propensity × reachability` and emits **marketing audiences** with
recommended channels and message angles.

> **The line that shapes the whole design.** Model B produces ranked segments and
> audiences for compliant marketing, **not** a call list of named individuals you
> believe hold a claim. Named-individual outreach is gated by counsel and solicitation
> rules. Build it as prioritization-and-segmentation feeding the funnel, and the
> privacy/FCRA/solicitation constraints (see [01-legal-compliance.md](01-legal-compliance.md))
> fall out naturally.

`person_priority = knowledge × propensity × reachability`, prioritized across orgs by
the org's expected recoverable value from Model A.

## B1 — Knowledge / Access
Inputs: the scheme hypothesis from Model A, plus the person's role, department,
seniority, and tenure.

```
knowledge = role_weight[scheme, role]
          × seniority_modifier
          × documentation_access_modifier
          × tenure_overlap(person_tenure, scheme_period)
```

The core artifact is the **scheme-to-role line-of-sight matrix** (high vs. medium
line-of-sight per scheme) — **populated** in `src/model_b/scheme_role_matrix.py`. Two
modifiers matter most:
- **Documentation access** lifts roles that can evidence the scheme (compliance,
  finance, coders, analysts) — relator value scales with documentation.
- **Tenure overlap** is a **hard gate**: the person must have worked there during the
  anomaly window. This is a point-in-time query against the entity graph's temporal
  `employed_by` edges — which is *why* those edges exist. Someone who left before the
  scheme started has no knowledge of it regardless of role.

Scheme → high-line-of-sight roles (abbreviated; full matrix in code):
- **Upcoding / MA risk adjustment** → coders/CDI, revenue integrity, billing managers,
  compliance/audit, medical directors.
- **Kickbacks (AKS/Stark)** → sales reps, contracting/BD, compliance.
- **Medical necessity** → UM nurses/physicians, clinical staff, medical directors.
- **Home health / hospice eligibility** → field RNs/aides, case managers, marketing
  liaisons, medical directors.
- **Lab / genetic testing** → sales reps, order-coordination staff.
- **Pharmacy / 340B** → pharmacists, 340B program managers, PBM analysts.
- **EVV / personal care** → schedulers/coordinators, billing.
- **Cost-report fraud** → controllers, reimbursement analysts, CFO.

## B2 — Propensity to Come Forward (the hard, novel model)

| Feature | Signal direction | Note |
|---|---|---|
| Departure status | former > current | current staff know most but rarely act (fear) |
| Months since departure | peak 6–18 mo | fresh memory, past acute fear, statute open |
| Departure type | involuntary/retaliation strongly positive | retaliation is the warmest signal in the system |
| Grievance signals | wrongful-termination/EEOC very strong; matched negative review moderate | already-burned internal whistleblowers |
| Tenure shape | mid is the sweet spot | U-shaped: too short = low knowledge, too long = loyalty fusion |
| Culpability | instructed/pressured > architect | architects are less willing and less valuable (reduced share) |
| Career stage | vested/exited > mid-climb | less to lose raises willingness |
| Professional identity | compliance certs, ethics-forward, prior internal reporting | higher propensity |
| Network proximity | near existing relators / your community | trust transfers |

Start as a transparent additive heuristic (weights in
`src/model_b/propensity.py`); graduate to an uplift/propensity model using engagement
as the proxy label (the "came forward" label is rare and slow). Treat the output as a
ranking aid, never a deterministic prediction of human behavior.

**Handle with care:** financial-distress proxies raise receptiveness to a financing
offer but also raise adverse-selection and predatory-targeting risk. Low weight,
flagged for review, never the primary driver.

## Reachability
`reachability = channel_availability × channel_fit`. Does a compliant channel exist
(LinkedIn, enriched email, geo-targetable location), and does it fit the persona
(LinkedIn/email for compliance and executives; community/social for clinical staff)?

## Output
Ranked individuals per org, rolled up into **audiences keyed by role × org × channel**,
each with a message angle mapped to scheme and persona. This drives ad targeting and
nurture branching — it is not a solicitation list. `person_priority` stays internal.

## Data & pipeline
People Data Labs / LinkedIn / Revelio for roster, tenure, departures; WARN; PACER and
employment dockets for grievance; Glassdoor/Indeed NLP for sentiment. Every person is
resolved to the canonical Org node via the entity-resolution spec
([03-entity-resolution.md](03-entity-resolution.md)), with point-in-time tenure
overlap against the scheme period.

## Guardrails (scoring people raises the stakes)
Audiences, not call lists · FCRA (audience definitions, not adjudications) · privacy
(the inference is sensitive from creation; minimize/lock down/never expose) · fairness
(key on role/knowledge/departure, audit for proxy leakage) · license people-data
properly.

## Current state in this repo
- **Built:** the scheme-to-role line-of-sight matrix (`scheme_role_matrix.py`).
- **Scaffold:** `knowledge.py`, `propensity.py`, `reachability.py`, `audiences.py`.
- **Blocked on:** people-data licensing + FCRA review, and the probabilistic
  person↔employer resolver + temporal `employed_by` edges
  (`src/entity_graph/person_resolver.py`).
