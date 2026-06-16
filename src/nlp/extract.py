"""
extract.py — GLiNER zero-shot entity extraction (docs/platform/15 §4.3).

GLiNER recognizes ANY entity label you pass at inference time (no per-type
training) and runs on CPU. We use it to pull structured entities out of the
unstructured text the platform already touches — CourtListener docket case names,
DOJ press releases, ProPublica 990 narratives, and (only after counsel clears the
collection terms) review text.

  load_gliner(model_name)   lazy-load a GLiNER model (optional dep; clear error
                            if absent). Downloads weights on first use — runtime
                            only, never in CI.
  extract_entities(texts, labels=…, model=…)   run extraction over texts → a tidy
                            frame (text_id, label, text, score). ``model`` is
                            INJECTABLE: pass any object exposing
                            ``predict_entities(text, labels)`` (the GLiNER API) so
                            tests use a fake and never download weights.

Output is structured entities for downstream matching/linkage, never a
conclusion; what gets done with a "fraud allegation" span is a human/counsel call.
"""

from __future__ import annotations

import pandas as pd

# the entity labels most useful across our sources (org/person/role/scheme)
GLINER_DEFAULT_LABELS = [
    "organization", "person", "job title", "medical specialty",
    "fraud allegation", "government agency", "monetary amount", "location",
]
DEFAULT_MODEL = "urchade/gliner_small-v2.1"


def load_gliner(model_name: str = DEFAULT_MODEL):
    """Lazy-load a GLiNER model. Raises a clear error if the optional dep is
    missing. Downloads weights on first use (runtime only)."""
    try:
        from gliner import GLiNER          # pragma: no cover (optional dep)
    except ImportError as e:               # pragma: no cover
        raise ImportError(
            "extract_entities needs GLiNER (an optional dep). "
            "Install it: pip install gliner  (it's in requirements-trey.txt)."
        ) from e
    return GLiNER.from_pretrained(model_name)   # pragma: no cover (network)


def extract_entities(texts, labels: list[str] | None = None, model=None,
                     threshold: float = 0.5, model_name: str = DEFAULT_MODEL
                     ) -> pd.DataFrame:
    """Extract labeled entities from ``texts`` → (text_id, label, text, score).

    ``model`` is injectable (anything with ``predict_entities(text, labels,
    threshold=…)`` returning ``[{"text", "label", "score"}, …]``); when None, a
    GLiNER model is lazy-loaded. ``texts`` may be a list of strings or a
    {id: text} mapping (ids preserved in ``text_id``).
    """
    labels = labels or GLINER_DEFAULT_LABELS
    if isinstance(texts, dict):
        items = list(texts.items())
    else:
        items = list(enumerate(texts))
    mdl = model if model is not None else load_gliner(model_name)

    rows = []
    for tid, text in items:
        if not str(text or "").strip():
            continue
        try:
            ents = mdl.predict_entities(str(text), labels, threshold=threshold)
        except TypeError:                          # fakes/models without kwarg
            ents = mdl.predict_entities(str(text), labels)
        for ent in ents or []:
            rows.append({"text_id": tid,
                         "label": str(ent.get("label", "")),
                         "text": str(ent.get("text", "")),
                         "score": float(ent.get("score", 1.0))})
    return pd.DataFrame(rows, columns=["text_id", "label", "text", "score"])
