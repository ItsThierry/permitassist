import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
sys.path.insert(0, str(API))

from permitassist3_exact_name_engine import (  # noqa: E402
    FINAL_VERIFIED,
    NON_FINAL,
    AHJCapabilityRegistry,
    FinalAnswerGate,
    JobDecomposer,
    OfficialSourceFilter,
    PermitAssist3ExactNameEngine,
    Verifier,
    WriteBackCorpus,
    apply_permitassist3_contract,
    contains_forbidden_final_string,
)

CORPUS = ROOT / "data" / "permitassist3" / "launch_corpus.json"


def test_launch_registry_has_50_ahjs_and_eval_100_cases():
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert len(data["ahj_registry"]) == 50
    assert len(data["eval_manifest"]) == 100
    assert data["metadata"]["record_count"] >= 80
    assert data["metadata"]["solved_ahj_count"] >= 25


def test_launch_corpus_records_are_official_source_and_forbidden_string_clean():
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    registry = {row["ahj_id"]: row for row in data["ahj_registry"]}
    source_filter = OfficialSourceFilter()
    for record in data["exact_name_records"]:
        assert len(record.get("source_content_hash_sha256", "")) == 64
        assert record.get("exact_name_or_category")
        assert record.get("exact_quote_or_snippet")
        assert not contains_forbidden_final_string(record.get("exact_name_or_category"))
        assert source_filter.official_enough(record, registry[record["ahj_id"]])


def test_job_decomposer_maps_restaurant_medical_office_to_multi_permit_families():
    decomposer = JobDecomposer()
    restaurant = decomposer.decompose("Restaurant tenant improvement with kitchen hood and grease interceptor")
    assert restaurant.vertical == "restaurant_ti"
    assert {"building_tenant_improvement", "mechanical", "plumbing", "fire_suppression", "health_food_establishment"} <= set(restaurant.permit_families)
    medical = decomposer.decompose("Medical clinic tenant improvement with exam rooms")
    assert medical.vertical == "medical_clinic_ti"
    assert "healthcare_state_overlay" in medical.permit_families
    office = decomposer.decompose("Office tenant improvement remodel")
    assert office.vertical == "office_ti"
    assert "building_tenant_improvement" in office.permit_families


def test_exact_engine_returns_verified_packet_for_seeded_launch_ahj():
    engine = PermitAssist3ExactNameEngine(CORPUS)
    result = engine.lookup("Restaurant tenant improvement", "Tampa", "FL", explicit_vertical="restaurant_ti", eval_mode=True)
    assert result["final_answer_state"] == FINAL_VERIFIED
    assert result["permit_names_or_categories"]
    assert "Commercial Alteration Building Permit" in result["permit_names_or_categories"][0]
    assert result["source_evidence"][0]["source_url"].startswith("http")
    assert len(result["source_evidence"][0]["source_content_hash_sha256"]) == 64
    assert not contains_forbidden_final_string(result)


def test_profile_only_ahj_fails_closed_to_completion_ticket_without_final_fields():
    engine = PermitAssist3ExactNameEngine(CORPUS)
    result = engine.lookup("Restaurant tenant improvement", "Phoenix", "AZ", explicit_vertical="restaurant_ti", eval_mode=True)
    assert result["final_answer_state"] == NON_FINAL
    assert result["permit_names_or_categories"] == []
    assert result["permits_required"] == []
    assert result["completion_ticket"]["ticket_id"]
    assert result["completion_ticket"]["writeback_required_on_resolution"] is True
    assert not contains_forbidden_final_string(result)


def test_final_gate_rejects_generic_or_evidence_free_packet():
    gate = FinalAnswerGate()
    ok, reasons = gate.validate({"permit_names_or_categories": ["Permit required — exact permit type needs AHJ verification"], "permits_required": [{"required": True}], "source_evidence": []})
    assert not ok
    assert "forbidden_final_fallback_string_present" in reasons
    packet = {
        "permit_names_or_categories": ["Commercial Alteration Building Permit"],
        "permits_required": [{"permit_name_or_portal_category": "Commercial Alteration Building Permit", "required": True}],
        "source_evidence": [{"source_url": "https://example.gov", "retrieved_at_utc": "2026-05-22T00:00:00Z"}],
    }
    ok, reasons = gate.validate(packet)
    assert not ok
    assert "source_evidence_missing_url_hash_or_retrieved_at" in reasons


def test_apply_contract_overwrites_legacy_safe_placeholder_when_exact_seed_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("PERMITASSIST3_TICKET_PATH", str(tmp_path / "tickets.jsonl"))
    legacy = {
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_type": "Manual filing path confirmation in progress",
        "permit_name": "Permit required — exact permit type needs AHJ verification",
        "permits_required": [{"permit_type": "Permit required — exact permit type needs AHJ verification", "required": True}],
    }
    result = apply_permitassist3_contract(legacy, "Restaurant tenant improvement", "Tampa", "FL", explicit_vertical="restaurant_ti")
    assert result["final_answer_state"] == FINAL_VERIFIED
    assert result["permit_type"] != "Manual filing path confirmation in progress"
    assert "Commercial Alteration Building Permit" in result["permit_type"]
    assert not contains_forbidden_final_string(result)


def test_apply_contract_turns_missing_exact_name_into_non_final_state(tmp_path, monkeypatch):
    ticket_path = tmp_path / "tickets.jsonl"
    monkeypatch.setenv("PERMITASSIST3_TICKET_PATH", str(ticket_path))
    legacy = {
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_type": "Permit required — exact permit type needs AHJ verification",
        "permit_name": "Permit required — exact permit type needs AHJ verification",
        "permits_required": [{"permit_type": "Permit required — exact permit type needs AHJ verification", "required": True}],
    }
    result = apply_permitassist3_contract(legacy, "Restaurant tenant improvement", "Phoenix", "AZ", explicit_vertical="restaurant_ti")
    assert result["final_answer_state"] == NON_FINAL
    assert result["permit_type"] is None
    assert result["permit_name"] is None
    assert result["permits_required"] == []
    assert result["completion_ticket"]["ticket_id"]
    assert ticket_path.exists()
    assert result["completion_ticket"]["ticket_id"] in ticket_path.read_text(encoding="utf-8")
    assert not contains_forbidden_final_string(result)


def test_eval_mode_does_not_write_back(tmp_path):
    writeback = tmp_path / "writeback.jsonl"
    engine = PermitAssist3ExactNameEngine(CORPUS, writeback_path=writeback)
    before = WriteBackCorpus(writeback).hash()
    engine.lookup("Restaurant tenant improvement", "Tampa", "FL", explicit_vertical="restaurant_ti", eval_mode=True)
    after = WriteBackCorpus(writeback).hash()
    assert before == after
    assert not writeback.exists()


def test_non_eval_writeback_requires_explicit_writeback_verified(tmp_path):
    writeback = tmp_path / "writeback.jsonl"
    engine = PermitAssist3ExactNameEngine(CORPUS, writeback_path=writeback)
    engine.lookup("Restaurant tenant improvement", "Tampa", "FL", explicit_vertical="restaurant_ti", eval_mode=False)
    assert not writeback.exists()
    engine.lookup(
        "Restaurant tenant improvement",
        "Tampa",
        "FL",
        explicit_vertical="restaurant_ti",
        eval_mode=False,
        writeback_verified=True,
    )
    assert writeback.exists()


def test_verifier_rejects_mistagged_exact_name_when_snippet_does_not_contain_name():
    registry = AHJCapabilityRegistry(CORPUS)
    profile = registry.resolve("Tampa", "FL")
    record = copy.deepcopy(registry.records_for(profile["ahj_id"], "restaurant_ti")[0])
    record["exact_name_or_category"] = "Imaginary Commercial Permit Name"
    record["verification_status"] = "verified_exact_name"
    record["exact_quote_or_snippet"] = "Official page text that does not contain the imaginary title."
    assert Verifier().verify_record(record, profile) is False


def test_source_filter_rejects_source_url_outside_ahj_official_urls():
    registry = AHJCapabilityRegistry(CORPUS)
    profile = registry.resolve("Tampa", "FL")
    record = copy.deepcopy(registry.records_for(profile["ahj_id"], "restaurant_ti")[0])
    record["source_url"] = "https://example.com/not-an-official-ahj-source"
    assert OfficialSourceFilter().official_enough(record, profile) is False


def test_registry_profile_only_records_cannot_final_route():
    registry = AHJCapabilityRegistry(CORPUS)
    profile = registry.resolve("Phoenix", "AZ")
    assert profile and profile["profile_only"] is True
    assert registry.records_for(profile["ahj_id"], "restaurant_ti") == []
