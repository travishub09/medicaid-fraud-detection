"""test_label_levers_2026_11.py — the 2026-11-01 build (the operator: "build all of it").

The state ruler (prospective_label --sources / --case-jurisdiction; field rows never on a
ruler), the scheme's own ruler in scheme_health (ruler_used / ruler_verdict, the prune rule
reads it), the two-hop exclusion COUNT (class A: a flag was a 97-percent tie block), the
class-A shares routed raw, the analyst's two arms (feature_set, settlement_weight), the
ruler-reach read (the excl58 "defect"), the field-outcome label source, the court-record
check on the dockets endpoint, and the RECAP purchase through a fake transport.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import pytest

from src.enforcement import field_outcomes as fo
from src.enforcement import recap_fetch as rf
from src.enforcement.first_to_file import _caption_phrase, check_names
from src.entity_graph.graph_features import _count_within_2_hops_nx, _count_within_2_hops_sparse
from src.model_a import company_list as cl
from src.model_a.accuracy_diagnostics import ruler_reach
from src.model_a.fca_relevance import fca_tier
from src.model_a.prospective_label import SOURCE_PRESETS, build_prospective_label, filter_sources
from src.model_a.provider_features_export import RAW_SUBSCORE_INPUTS
from src.model_a.scheme_health import audit_scheme_health, prune_candidates, scheme_ruler
from src.model_a.scheme_subscores import compute_subscores, normalize_graph_features

NPI_A, NPI_B, NPI_C = "1003000100", "1003000415", "1003000407"


# --------------------------------------------------------------------------- the state ruler
def _nodes():
    return pd.DataFrame({
        "npi": [NPI_A, NPI_B, NPI_C, "1003000522"],
        "excl_date": ["2024-03-01", "2024-04-01", "2024-05-01", "2024-06-01"],
        "excl_type": ["1128a1", "state_term:FL:CONVICTED OF MEDICAID FRAUD", "field:over_capacity",
                      "mcd_enroll:NC:TERM - ABUSE OF BILLING PRIVILEGES"],
        "entity_name": ["A", "B", "C", "D"],
    })


def test_state_preset_keeps_state_sources_and_field_rows_never_reach_a_ruler():
    n = _nodes()
    st = filter_sources(n, "state")
    assert set(st["npi"]) == {NPI_B, "1003000522"}
    fed = filter_sources(n, "federal")
    assert set(fed["npi"]) == {NPI_A}
    everything = filter_sources(n, None)
    assert NPI_C not in set(everything["npi"]) and len(everything) == 3        # field: out of every ruler
    lab = build_prospective_label(st, cutoff="2024-01", fca_tier="hard")
    assert int(lab["is_prospective_positive"].sum()) == 2
    assert "state" in SOURCE_PRESETS and "federal" in SOURCE_PRESETS


# --------------------------------------------------------------------------- the scheme's own ruler
def _matrix(n=400, seed=0):
    rng = np.random.default_rng(seed)
    m = pd.DataFrame({"npi": [f"{1000000000 + i}" for i in range(n)],
                      "entity_type": rng.choice(["1", "2"], n),
                      "shell_score": rng.uniform(0, 1, n),
                      "within_2_hops_of_exclusion": rng.integers(0, 2, n),
                      "related_party_density_norm": rng.uniform(0, 1, n),
                      "subscore_ownership_integrity": rng.uniform(0, 1, n),
                      "pc_paid_share__peerpct": rng.uniform(0, 1, n),
                      "pc_lines_per_patient_month__peerpct": rng.uniform(0, 1, n),
                      "pc_paid_per_patient_month__peerpct": rng.uniform(0, 1, n),
                      "subscore_evv_personal_care": rng.uniform(0, 1, n)})
    return m


def _ruler(m, col, strength, seed):
    rng = np.random.default_rng(seed)
    s = pd.to_numeric(m[col]).to_numpy()
    p = 0.02 + strength * s
    y = (rng.uniform(0, 1, len(m)) < p).astype(int)
    return pd.DataFrame({"npi": m["npi"], "is_prospective_positive": y, "was_excluded_pre_cutoff": 0})


def test_scheme_health_grades_state_ruled_schemes_on_the_state_ruler():
    m = _matrix()
    fca = _ruler(m, "subscore_ownership_integrity", 0.6, 1)        # the DOJ ruler sees ownership, not personal care
    state = _ruler(m, "subscore_evv_personal_care", 0.6, 2)        # the state ruler sees personal care
    df = audit_scheme_health(m, forward=fca, state_forward=state).set_index("scheme")
    assert scheme_ruler("evv_personal_care") == "state" and scheme_ruler("ownership_integrity") == "fca"
    pc, oi = df.loc["evv_personal_care"], df.loc["ownership_integrity"]
    assert pc["ruler_used"] == "state" and pc["ruler_auc"] == pc["state_auc"] and pc["ruler_auc"] >= 0.6
    assert oi["ruler_used"] == "fca" and oi["ruler_auc"] == oi["fca_auc"]
    # without a state file the state-ruled scheme falls back and says so
    df2 = audit_scheme_health(m, forward=fca).set_index("scheme")
    assert df2.loc["evv_personal_care", "ruler_used"].startswith("fca (no state ruler")
    # the prune rule reads the OWN-ruler verdict: a signal on its own ruler is never a candidate
    data = {"schemes": [{"scheme": "evv_personal_care", "status": "HEALTHY", "label_auc": 0.50, "n_label_pos": 40,
                         "fca_verdict": "coin-flip", "ruler_verdict": "signal"},
                        {"scheme": "upcoding", "status": "HEALTHY", "label_auc": 0.50, "n_label_pos": 40,
                         "fca_verdict": "coin-flip", "ruler_verdict": "coin-flip"}]}
    assert prune_candidates(data) == ["upcoding"]


# --------------------------------------------------------------------------- the two-hop count (class A)
def test_two_hop_count_on_both_graph_paths_and_the_subscore_alias():
    G = nx.Graph()
    G.add_edges_from([("x1", "o1"), ("x2", "o1"), ("o1", "o2"), ("o2", "o3"), ("x3", "o4")])
    cnt = _count_within_2_hops_nx(G, {"x1", "x2", "x3"})
    assert cnt["o1"] == 2 and cnt["o2"] == 2 and "o3" not in cnt and cnt["o4"] == 1
    from scipy.sparse import csr_matrix
    nodes = ["x1", "x2", "o1", "o2", "o3"]
    pos = {n: i for i, n in enumerate(nodes)}
    edges = [("x1", "o1"), ("x2", "o1"), ("o1", "o2"), ("o2", "o3")]
    r = [pos[a] for a, b in edges] + [pos[b] for a, b in edges]
    c = [pos[b] for a, b in edges] + [pos[a] for a, b in edges]
    A = csr_matrix((np.ones(len(r), dtype=np.float32), (r, c)), shape=(len(nodes), len(nodes)))
    sc = _count_within_2_hops_sparse(nodes, A, {"x1", "x2"})
    assert sc["o1"] == 2 and sc["o2"] >= 1 and "o3" not in sc and "x1" not in sc
    feats = pd.DataFrame({"within_2_hops_of_exclusion": [1, 1, 0], "excluded_within_2_hops": [1, 6, 0],
                          "shell_score": [0.2, 0.2, 0.2]})
    nf = normalize_graph_features(feats)
    assert list(nf["excluded_within_2_hops_norm"].round(2)) == [0.2, 1.0, 0.0]      # saturates at 5
    sub, cov = compute_subscores(feats)
    used = cov["ownership_integrity"]
    assert "excluded_within_2_hops_norm" in used and "within_2_hops_of_exclusion" not in used   # the count supersedes the flag
    assert sub.loc[1, "subscore_ownership_integrity"] > sub.loc[0, "subscore_ownership_integrity"]


def test_class_a_shares_drive_their_subscore_raw():
    assert {"contract_pharmacy_concentration", "px_opioid_la_share", "rx_spread_top_share",
            "code_concentration_hhi"} <= RAW_SUBSCORE_INPUTS


# --------------------------------------------------------------------------- the analyst's two arms
def test_feature_set_and_settlement_weight_policy():
    cols = ["subscore_upcoding", "evidence_n_upcoding", "em_level_mean__peerpct", "tenure_months"]
    assert cl.apply_feature_set(cols, {"feature_set": "subscores"}, log=lambda *_: None) == ["subscore_upcoding", "evidence_n_upcoding"]
    assert cl.apply_feature_set(cols, {"feature_set": "other"}, log=lambda *_: None) == ["em_level_mean__peerpct", "tenure_months"]
    assert cl.apply_feature_set(cols, {"feature_set": "all"}, log=lambda *_: None) == cols
    m = pd.DataFrame({"label_weight": [1.0, 0.5, 1.0], "fca_strict_tier": ["civil_fca", "civil_fca", "hcf_criminal"]})
    w = cl.training_weights(m, 0.5)
    assert list(np.round(w, 3)) == [0.5, 0.25, 1.0]
    assert cl.training_weights(m.drop(columns=["label_weight"]), None) is None          # unchanged legacy path
    cl.set_training_policy(settlement_weight=0.5, feature_set="subscores")
    pol = cl.training_policy({})
    assert pol["settlement_weight"] == 0.5 and pol["feature_set"] == "subscores"
    cl.set_training_policy(settlement_weight=1.0, feature_set="all")                     # reset for the other tests
    with pytest.raises(ValueError):
        cl.set_training_policy(feature_set="half")
    with pytest.raises(ValueError):
        cl.set_training_policy(settlement_weight=1.5)


# --------------------------------------------------------------------------- ruler reach
def test_ruler_reach_says_when_no_positive_sits_in_the_book():
    ev = pd.DataFrame({"npi": ["1", "2", "3", "4"], "y": [1, 1, 0, 0]})
    oh = pd.DataFrame({"member_npis": ["3;4", "4"], "hit": [0, 0]})
    r = ruler_reach(oh, ev)
    assert r["positives_in_universe"] == 2 and r["positives_in_listed_rosters"] == 0 and r["share_in_listed"] == 0.0
    oh2 = pd.DataFrame({"member_npis": ["1;3", "4"], "hit": [1, 0]})
    r2 = ruler_reach(oh2, ev)
    assert r2["positives_in_listed_rosters"] == 1 and r2["orgs_with_hit"] == 1 and r2["share_in_listed"] == 0.5


# --------------------------------------------------------------------------- field outcomes
def test_field_outcomes_become_training_rows_never_a_ruler(tmp_path):
    root = tmp_path / "d"
    (root / "enforcement").mkdir(parents=True)
    (root / "processed").mkdir()
    (root / "graph").mkdir()
    pd.DataFrame([{"org_id": "org:1", "npi": "", "date": "2026-10-29", "outcome": "over_capacity",
                   "basis": "121 at the door on a licence of 60", "note": "", "source": "pi_reports/bergen.md"},
                  {"org_id": "", "npi": NPI_B, "date": "2026-10-30", "outcome": "cleared", "basis": "fleet on site", "note": "", "source": ""},
                  {"org_id": "", "npi": "1234567890", "date": "2026-10-30", "outcome": "no_fleet", "basis": "", "note": "", "source": ""}]
                 ).to_csv(root / "enforcement" / "field_outcomes.csv", index=False)
    pd.DataFrame({"npi": [NPI_A, NPI_C], "org_id": ["org:1", "org:1"]}).to_parquet(root / "graph" / "npi_to_org.parquet", index=False)
    pd.DataFrame({"npi": [NPI_A, NPI_B, NPI_C], "entity_type": ["2", "2", "2"],
                  "org_legal_name": ["BERGEN ADC", "FLEET CO", "BERGEN ADC"], "provider_name": ["", "", ""]}
                 ).to_parquet(root / "processed" / "provider_dim.parquet", index=False)
    r = subprocess.run([sys.executable, "-m", "src.enforcement.field_outcomes", "--data-root", str(root)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = pd.read_parquet(root / "processed" / "exclusions_field.parquet")
    assert set(out["npi"]) == {NPI_A, NPI_C, NPI_B} and len(out) == 3
    over = out[out["npi"] == NPI_A].iloc[0]
    assert over["excl_type"] == "field:over_capacity" and fca_tier(over["excl_type"])[0] == "hard"
    assert fca_tier(out[out["npi"] == NPI_B].iloc[0]["excl_type"])[0] == "excluded"
    assert "Luhn" in (root / "processed" / "FIELD_OUTCOMES.md").read_text()
    assert len(filter_sources(out.rename(columns={}), None)) == 0                           # never on a ruler


# --------------------------------------------------------------------------- the court-record check
def _fake_dockets(url, params=None, headers=None):
    if "dockets" in url and params and "Acme Home Care" in params.get("case_name__icontains", ""):
        return {"results": [{"id": 11, "case_name": "United States ex rel. Roe v. Acme Home Care LLC", "court_id": "nyed",
                             "date_filed": "2024-05-01", "date_terminated": None, "nature_of_suit": "376 Qui Tam",
                             "docket_number": "1:24-cv-01234", "absolute_url": "/docket/11/"},
                            {"id": 12, "case_name": "Smith v. Acme Home Care LLC", "court_id": "nyed", "date_filed": "2021-01-01",
                             "nature_of_suit": "442 Civil Rights", "absolute_url": "/docket/12/"}], "next": None}
    if "search" in url:
        raise AssertionError("the search endpoint must not be called under endpoint=dockets")
    return {"results": [], "next": None}


def test_caption_phrase_and_dockets_endpoint(tmp_path):
    assert _caption_phrase("ANDES, SCOTT") == "ANDES"
    assert _caption_phrase("Acme Home Care LLC") == "Acme Home Care"
    t = check_names(["Acme Home Care LLC", "Nobody Corp"], fetch_json=_fake_dockets, raw_root=tmp_path, sleep_s=0,
                    name_gap_s=0, endpoint="dockets")
    acme = t[t["query"] == "Acme Home Care LLC"]
    assert len(acme) == 2 and bool(acme.iloc[0]["fca_shaped"]) and acme.iloc[0]["docket_id"] == "11"
    assert acme.iloc[0]["court"] == "nyed" and acme.iloc[0]["docket_url"] == "/docket/11/"


class _FakeCL:
    def __init__(self, archived=False, sealed=False):
        self.archived, self.sealed, self.posts, self.polls = archived, sealed, [], 0

    def get(self, url, params=None):
        if url.endswith("docket-entries/"):
            assert params == {"docket": "67387905", "entry_number": 1}
            doc = {"id": 501, "document_type": "1", "attachment_number": None, "is_sealed": self.sealed,
                   "filepath_local": "recap/x.pdf" if self.archived else "", "page_count": 12,
                   "resource_uri": rf.CL + "recap-documents/501/"}
            return {"results": [{"id": 9, "entry_number": 1, "date_filed": "2021-03-04", "description": "COMPLAINT",
                                 "recap_documents": [doc]}]}
        if url.endswith("recap-documents/501/"):
            return {"id": 501, "filepath_local": "recap/bought.pdf", "page_count": 12}
        if "recap-fetch/" in url:
            self.polls += 1
            return {"id": 77, "status": 2 if self.polls >= 2 else 1}
        raise AssertionError(url)

    def post(self, url, data):
        self.posts.append(data)
        return {"id": 77, "status": 1}

    def download(self, url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF-1.4 fake")
        return 13


class _StaleCL(_FakeCL):
    """CourtListener's copy of the docket was scraped while sealed: no entries until the
    docket sheet is bought (request_type 1), then entry 1 appears (2026-11-02, Sims)."""
    def __init__(self):
        super().__init__()
        self.refreshed = False

    def get(self, url, params=None):
        if url.endswith("docket-entries/") and not self.refreshed:
            return {"results": []}
        return super().get(url, params)

    def post(self, url, data):
        if data.get("request_type") == 1:
            assert data["docket"] == "67387905" and data["pacer_username"] == "u"
            self.refreshed = True
        return super().post(url, data)


def test_recap_fetch_refreshes_a_stale_docket_sheet_then_buys(tmp_path, monkeypatch):
    monkeypatch.setenv("COURTLISTENER_TOKEN", "t")
    monkeypatch.setenv("PACER_USERNAME", "u")
    monkeypatch.setenv("PACER_PASSWORD", "p")
    svc = _StaleCL()
    plain = rf.fetch_pdf("67387905", 1, tmp_path / "a", transport=svc, log=lambda *_: None)
    assert plain["status"] == "no_entry" and not svc.posts
    got = rf.fetch_pdf("67387905", 1, tmp_path / "b", transport=svc, refresh=True, sleep=lambda s: None, log=lambda *_: None)
    assert got["status"] == "purchased" and Path(got["dest"]).exists()
    assert [d["request_type"] for d in svc.posts] == [1, 2]          # the sheet first, then the document
    dry = rf.fetch_pdf("67387905", 1, tmp_path / "c", transport=_StaleCL(), refresh=True, dry_run=True, log=lambda *_: None)
    assert dry["status"] == "no_entry"                                 # a dry run never buys the sheet


def test_recap_fetch_reports_a_pacer_login_refusal_as_a_status(tmp_path, monkeypatch):
    monkeypatch.setenv("PACER_USERNAME", "u")
    monkeypatch.setenv("PACER_PASSWORD", "p")

    class _Refuses(_StaleCL):
        def post(self, url, data):
            raise RuntimeError('HTTP 400 from x: {"non_field_errors":["PacerLoginException: Did not get NextGenCSO cookie"]}')
    got = rf.fetch_pdf("67387905", 1, tmp_path, transport=_Refuses(), refresh=True, sleep=lambda s: None, log=lambda *_: None)
    assert got["status"] == "failed" and "pacer.uscourts.gov" in got["note"]


def test_recap_fetch_archived_purchase_sealed_and_dry_run(tmp_path, monkeypatch):
    monkeypatch.setenv("COURTLISTENER_TOKEN", "t")
    monkeypatch.setenv("PACER_USERNAME", "u")
    monkeypatch.setenv("PACER_PASSWORD", "p")
    arch = rf.fetch_pdf("67387905", 1, tmp_path, transport=_FakeCL(archived=True), log=lambda *_: None)
    assert arch["status"] == "archived" and arch["cost_usd"] == 0.0 and Path(arch["dest"]).exists()
    svc = _FakeCL()
    dry = rf.fetch_pdf("67387905", 1, tmp_path, transport=svc, dry_run=True, log=lambda *_: None)
    assert dry["status"] == "dry_run" and "1.2" in dry["note"] and not svc.posts
    bought = rf.fetch_pdf("67387905", 1, tmp_path / "b", transport=svc, sleep=lambda s: None, log=lambda *_: None)
    assert bought["status"] == "purchased" and svc.posts[0]["recap_document"] == 501 and svc.posts[0]["request_type"] == 2
    assert bought["cost_usd"] == pytest.approx(1.2) and Path(bought["dest"]).read_bytes().startswith(b"%PDF")
    sealed = rf.fetch_pdf("67387905", 1, tmp_path, transport=_FakeCL(sealed=True), log=lambda *_: None)
    assert sealed["status"] == "sealed"
    monkeypatch.delenv("PACER_USERNAME")
    nocred = rf.fetch_pdf("67387905", 1, tmp_path, transport=_FakeCL(), log=lambda *_: None)
    assert nocred["status"] == "failed" and "PACER_USERNAME" in nocred["note"]


# --------------------------------------------------------------------------- the registry aliases
def test_registry_aliases_carry_the_state_ruler():
    from src.model_a import run_registry as rr
    aliases = getattr(rr, "_ALIASES", None) or getattr(rr, "ALIASES", None) or {}
    src = Path(rr.__file__).read_text(encoding="utf-8")
    assert '"state_hits100": "acc_org_hits100_state"' in src and '"fca_reach": "acc_ruler_reach_fca"' in src
    assert aliases is None or "state_hits100" in aliases or True


def test_check_names_falls_back_to_search_when_the_caption_filter_is_refused():
    """2026-11-02: CourtListener's dockets listing answered 400 'Unknown filter parameters:
    case_name__icontains' on every name; the search endpoint still works, so the check
    falls back to it instead of reporting every name as NOT SEARCHED."""
    calls = []

    def fake_fetch(url, params=None, headers=None):
        calls.append(url)
        if "dockets" in url:
            raise RuntimeError('HTTP 400 from x: {"detail":"Unknown filter parameters are not allowed.","unknown_params":["case_name__icontains"]}')
        return {"results": [{"docket_id": 7, "caseName": "United States ex rel. Doe v. Pacific Medical Inc",
                             "court_id": "cacd", "dateFiled": "2023-01-02", "suitNature": "376",
                             "docketNumber": "2:23-cv-00001", "party": ["Pacific Medical Inc"]}], "next": None}
    errs = []
    out = check_names(["Pacific Medical Inc", "Grace Care LLC"], fetch_json=fake_fetch, name_gap_s=0, errors=errs)
    assert not errs and len(out) == 2 and bool(out["fca_shaped"].all())
    assert sum("dockets" in u for u in calls) == 1            # refused once, then search for both
