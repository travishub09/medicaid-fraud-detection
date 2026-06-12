"""
model_b — Whistleblower Identification and Propensity (SCAFFOLD).

For a Model-A-flagged organization, Model B ranks individuals by
``knowledge × propensity × reachability`` and emits **marketing audiences** —
NOT a call list of named individuals. See ``docs/platform/05-model-b.md``.

  scheme_role_matrix.py  the scheme→role line-of-sight matrix (real data)
  knowledge.py           B1: role × seniority × documentation-access × tenure-overlap gate
  propensity.py          B2: departure recency/type, grievance, tenure shape, culpability…
  reachability.py        compliant-channel availability × channel fit
  audiences.py           roll ranked individuals into role×org×channel audiences

Guardrails are first-class (FCRA, privacy of the inference, fairness, solicitation):
this is a prioritization/segmentation system feeding the funnel, never an
adjudication about people. Outreach to named individuals is gated by counsel.

Status: scaffold — only the scheme-to-role matrix is populated; scoring functions
raise NotImplementedError and cite the spec.
"""
