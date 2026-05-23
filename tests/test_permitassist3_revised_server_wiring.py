import importlib
# pyright: reportMissingImports=false
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _install_server_import_stubs():
    requests_stub = types.ModuleType("requests")
    response = types.SimpleNamespace(status_code=200)
    setattr(requests_stub, "post", lambda *a, **k: None)
    setattr(requests_stub, "get", lambda *a, **k: response)
    setattr(requests_stub, "head", lambda *a, **k: response)
    setattr(requests_stub, "exceptions", types.SimpleNamespace(Timeout=TimeoutError, RequestException=Exception))
    # Replace earlier minimalist test stubs so server.validate_url has the
    # attributes it uses when this file runs after Phase 1B tests.
    sys.modules["requests"] = requests_stub

    openai_stub = types.ModuleType("openai")
    setattr(openai_stub, "OpenAI", lambda *a, **k: object())
    sys.modules.setdefault("openai", openai_stub)

    google_stub = types.ModuleType("google")
    genai_stub = types.ModuleType("google.generativeai")
    setattr(genai_stub, "configure", lambda *a, **k: None)
    sys.modules.setdefault("google", google_stub)
    sys.modules.setdefault("google.generativeai", genai_stub)

    research_stub = types.ModuleType("research_engine")
    setattr(research_stub, "research_permit", lambda *a, **k: {"permit_verdict": "MAYBE"})
    setattr(research_stub, "build_google_maps_url", lambda *a, **k: "")
    setattr(research_stub, "strip_pdf_from_result", lambda result: result)
    setattr(research_stub, "get_cache_hit_rate", lambda: 0)
    setattr(research_stub, "detect_primary_scope", lambda job_type: "residential" if "residential" in str(job_type).lower() else "generic")
    setattr(research_stub, "classify_scope_required_permits", lambda job_type, city="", state="": [])
    sys.modules["research_engine"] = research_stub


def _import_server(monkeypatch, tmp_path, phase23_env="1"):
    _install_server_import_stubs()
    monkeypatch.setenv("FREE_LOOKUP_DB", str(tmp_path / "ip_lookups.db"))
    monkeypatch.setenv("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    if phase23_env is None:
        monkeypatch.delenv("PERMITASSIST3_REVISED_PHASE23_ENABLED", raising=False)
    else:
        monkeypatch.setenv("PERMITASSIST3_REVISED_PHASE23_ENABLED", phase23_env)
    api = ROOT / "api"
    if str(api) not in sys.path:
        sys.path.insert(0, str(api))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    sys.modules.pop("server", None)
    from api import server
    loaded = importlib.reload(server)

    from permitassist3_revised import CustomerOutputScanner, PermitAssist3RevisedEngine, apply_permitassist3_revised_contract

    def temp_contract(result, job_type, city, state, *, explicit_vertical=None, live_retriever=None):
        original_engine = PermitAssist3RevisedEngine

        class TempPathEngine(original_engine):
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("ticket_path", tmp_path / "phase2_3_completion_tickets.jsonl")
                kwargs.setdefault("writeback_path", tmp_path / "phase2_3_verified_writeback.jsonl")
                super().__init__(*args, **kwargs)

        module = sys.modules["permitassist3_revised"]
        monkeypatch.setattr(module, "PermitAssist3RevisedEngine", TempPathEngine)
        try:
            return apply_permitassist3_revised_contract(
                result,
                job_type,
                city,
                state,
                explicit_vertical=explicit_vertical,
                live_retriever=live_retriever,
            )
        finally:
            monkeypatch.setattr(module, "PermitAssist3RevisedEngine", original_engine)

    setattr(loaded, "apply_permitassist3_revised_contract", temp_contract)
    return loaded


def _assert_customer_response_scan_clean(value):
    from permitassist3_revised import CustomerOutputScanner

    scan = CustomerOutputScanner().scan(value)
    assert scan["pass"], scan


def test_revised_phase2_3_contract_is_wired_into_finalize_customer_path(monkeypatch, tmp_path):
    server = _import_server(monkeypatch, tmp_path)
    result = server.finalize_permit_lookup_result(
        {
            "permit_verdict": "YES",
            "permit_required": True,
            "permit_type": "Generic permit required",
            "permit_name": "Generic permit required",
            "permits_required": [{"permit_type": "Generic permit required", "required": True}],
        },
        "office tenant improvement",
        "Austin",
        "TX",
        evidence_allowed=False,
    )
    assert result["final_answer_state"] == "verified_final"
    assert result["permitassist3_revised"]["public_state"] == "verified_final"
    assert result["permit_type"] != "Generic permit required"
    assert result["claim_citations"]
    assert result["permits_required"] == [
        {
            "permit_type": result["permit_type"],
            "required": True,
            "source_backed": True,
            "official_source_url": result["claim_citations"][0]["source_url"],
        }
    ]
    assert result["state_overlay"]["wired_into_customer_path"] is True
    _assert_customer_response_scan_clean(result)


def test_revised_phase2_3_contract_strips_legacy_final_fields_on_pending(monkeypatch, tmp_path):
    server = _import_server(monkeypatch, tmp_path)
    result = server.finalize_permit_lookup_result(
        {
            "permit_verdict": "YES",
            "permit_required": True,
            "permit_type": "Generic permit required",
            "permit_name": "Generic permit required",
            "apply_url": "https://example.com/legacy",
            "apply_path": {"permit_type": "Generic permit required"},
            "permits_required": [{"permit_type": "Generic permit required", "required": True}],
        },
        "restaurant tenant improvement",
        "Plano",
        "TX",
        evidence_allowed=False,
    )
    assert result["final_answer_state"] == "pending_active_retrieval"
    assert result["customer_final"] is False
    assert result["permit_required"] is None
    assert result["permit_type"] is None
    assert result["apply_url"] is None
    assert result["apply_path"] is None
    assert result["permits_required"] == []
    assert result["completion_ticket"]["tracker_id"].startswith("pa3-")
    assert result.get("unverified_claims") is None
    _assert_customer_response_scan_clean(result)


def test_revised_phase2_3_customer_path_uses_live_retriever_and_writeback(monkeypatch, tmp_path):
    server = _import_server(monkeypatch, tmp_path)

    def fake_research(job_type, city, state, *args, **kwargs):
        assert kwargs.get("use_cache") is False
        assert kwargs.get("job_category") == "commercial"
        assert kwargs.get("suppress_cache_write") is True
        return {
            "permit_type": "Commercial Tenant Improvement Building Permit",
            "apply_url": "https://sandiego.gov/development-services/permits/commercial-ti",
            "sources": [
                {
                    "url": "https://sandiego.gov/development-services/permits/commercial-ti",
                    "title": "City of San Diego Commercial Tenant Improvement Permits",
                    "snippet": "Commercial tenant improvement projects use the Commercial Tenant Improvement Building Permit filing path.",
                    "official_source_classification": "ahj_official",
                }
            ],
        }

    monkeypatch.setattr(server, "research_permit", fake_research)
    result = server.finalize_permit_lookup_result(
        {
            "permit_verdict": "YES",
            "permit_required": True,
            "permit_type": "Generic permit required",
            "permit_name": "Generic permit required",
            "permits_required": [{"permit_type": "Generic permit required", "required": True}],
        },
        "office tenant improvement",
        "San Diego",
        "CA",
        explicit_vertical="office_ti",
        evidence_allowed=False,
    )

    assert result["final_answer_state"] == "verified_final"
    assert result["permit_type"] == "Commercial Tenant Improvement Building Permit"
    assert result["completion_ticket"] is None
    assert result["claim_citations"][0]["source_url"] == "https://sandiego.gov/development-services/permits/commercial-ti"
    assert (tmp_path / "phase2_3_verified_writeback.jsonl").exists()
    _assert_customer_response_scan_clean(result)


def test_revised_phase2_3_does_not_hijack_non_wedge_customer_path(monkeypatch, tmp_path):
    server = _import_server(monkeypatch, tmp_path)
    result = server.finalize_permit_lookup_result(
        {
            "permit_verdict": "YES",
            "permit_required": True,
            "permit_type": "Residential Plumbing Permit — Water Heater Replacement",
            "permit_name": "Residential Plumbing Permit — Water Heater Replacement",
            "permits_required": [
                {"permit_type": "Residential Plumbing Permit — Water Heater Replacement", "required": True}
            ],
            "sources": [{"url": "https://example.gov/residential-water-heater", "title": "Official source"}],
        },
        "residential water heater replacement",
        "Dallas",
        "TX",
        evidence_allowed=False,
    )

    assert "permitassist3_revised" not in result
    assert result.get("final_answer_state") != "pending_active_retrieval"
    assert not str(result.get("completion_ticket") or "").startswith("pa3-")


def test_revised_phase2_3_can_be_explicitly_disabled(monkeypatch, tmp_path):
    server = _import_server(monkeypatch, tmp_path, phase23_env="0")
    result = server.finalize_permit_lookup_result(
        {
            "permit_verdict": "YES",
            "permit_required": True,
            "permit_type": "Generic permit required",
            "permit_name": "Generic permit required",
            "permits_required": [{"permit_type": "Generic permit required", "required": True}],
        },
        "office tenant improvement",
        "Austin",
        "TX",
        evidence_allowed=False,
    )

    assert "permitassist3_revised" not in result
