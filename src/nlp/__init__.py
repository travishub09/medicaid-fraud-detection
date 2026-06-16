"""
nlp — text-extraction helpers for unstructured sources.

GLiNER zero-shot NER/relation extraction (docs/platform/15 §4.3) for pulling
org/person/role/allegation entities out of dockets, DOJ releases, 990 PDFs, and
(counsel-cleared) reviews. The model is an OPTIONAL dependency (requirements-trey)
and is loaded lazily; the wrapper accepts an injected model so tests run without
downloading weights.
"""

from .extract import extract_entities, GLINER_DEFAULT_LABELS, load_gliner

__all__ = ["extract_entities", "GLINER_DEFAULT_LABELS", "load_gliner"]
