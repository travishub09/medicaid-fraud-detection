"""
test_label_store_lookup.py — the append-only label store and the lookup tool.

Label store: append-only immutability, schema/outcome validation, the
validation-shaped view. Lookup tool: the risk card carries percentiles, drivers,
benign explanations, and the disclaimer — and structurally no fraud field.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.enforcement.label_store import (
    record_outcomes, load_outcomes, outcomes_for_validation, LABEL_COLUMNS,
)


def _batch(label_id: str, outcome: str = "settled", org: str = "org:x") -> pd.DataFrame:
    return pd.DataFrame([{
        "label_id": label_id, "org_node_id": org, "case_id": "c1",
        "outcome": outcome, "outcome_date": "2025-01-15",
        "amount_usd": 1_000_000, "source": "doj", "note": "",
    }])


def test_label_store_appends_and_never_mutates(tmp_path):
    store = tmp_path / "outcomes.parquet"
    out1 = record_outcomes(_batch("L1"), store)
    assert len(out1) == 1 and list(out1.columns) == LABEL_COLUMNS

    # same label_id again, different amount → existing row is IMMUTABLE
    tampered = _batch("L1")
    tampered["amount_usd"] = 999
    out2 = record_outcomes(tampered, store)
    assert len(out2) == 1
    assert out2.iloc[0]["amount_usd"] == 1_000_000     # original survives

    out3 = record_outcomes(_batch("L2", outcome="intervened"), store)
    assert len(out3) == 2
    assert len(load_outcomes(store)) == 2


def test_label_store_validates(tmp_path):
    store = tmp_path / "outcomes.parquet"
    with pytest.raises(AssertionError):
        record_outcomes(_batch("L1", outcome="not_a_real_outcome"), store)
    with pytest.raises(AssertionError):
        record_outcomes(_batch("L1").drop(columns=["source"]), store)


def test_outcomes_for_validation_shape(tmp_path):
    store = tmp_path / "outcomes.parquet"
    record_outcomes(_batch("L1"), store)
    unresolved = _batch("L2")
    unresolved["org_node_id"] = ""                      # not yet org-resolved
    record_outcomes(unresolved, store)
    v = outcomes_for_validation(store)
    assert list(v.columns) == ["org_node_id", "outcome_date"]
    assert len(v) == 1                                  # unresolved rows excluded


# ------------------------------------------------------------ lookup tool ---

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    httpx = pytest.importorskip("httpx")                # TestClient transport
    from fastapi.testclient import TestClient
    from src.lookup_tool.app import build_app

    feats = pd.DataFrame([
        {"npi": "1003000415", "display_name": "INDEPENDENT CLINIC LLC",
         "peer_group": "family_medicine_tx",
         "em_high_level_share": 0.97, "services_per_bene": 0.91,
         "code_concentration_hhi": 0.99},
        {"npi": "1003000407", "display_name": "CLEAN, JANE",
         "peer_group": "family_medicine_tx",
         "em_high_level_share": 0.20, "services_per_bene": 0.35,
         "code_concentration_hhi": 0.10},
    ])
    path = tmp_path_factory.mktemp("lookup") / "percentiles.parquet"
    feats.to_parquet(path, index=False)
    return TestClient(build_app(path))


def test_lookup_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["providers"] == 2


def test_lookup_returns_percentiles_drivers_and_safety_text(client):
    r = client.get("/lookup/1003000415")
    assert r.status_code == 200
    card = r.json()
    assert card["display_name"] == "INDEPENDENT CLINIC LLC"
    # plain-language metric names, 0–1 percentiles
    assert "billing concentration in few service codes" in card["percentile_by_metric"]
    assert card["top_drivers"][0]["percentile"] == 0.99
    # the safety surface is mandatory
    assert card["benign_explanations"]
    assert "not evidence of fraud" in card["disclaimer"]
    # and there is structurally no fraud verdict anywhere in the card
    assert not any("fraud" in k.lower() for k in card)


def test_lookup_unknown_npi_404(client):
    assert client.get("/lookup/9999999999").status_code == 404
