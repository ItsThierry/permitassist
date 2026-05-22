import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
sys.path.insert(0, str(API))

from permitassist3_revised import (  # noqa: E402
    PENDING_ACTIVE_RETRIEVAL,
    VERIFIED_FINAL,
    CustomerOutputScanner,
    PermitAssist3RevisedEngine,
    PermitAssist3RevisedFinalGate,
    StateOverlayRegistry,
    load_verified_corpus_slice,
)


FORBIDDEN_CUSTOMER_PATTERNS = [
    r"NO_VERIFIED_SOURCE",
    r"AHJ unsupported",
    r"not supported",
    r"check with (?:the )?AHJ",
    r"contact (?:your|the) AHJ",
    r"\bmay be required\b",
    r"\blikely required\b",
    r"\btypically\b",
    r"\bgenerally\b",
    r"\bvaries by\b",
    r"unable to determine",
    r"\bPermit required\b(?!.*(?:official|source-backed|portal|application|form|category|path))",
]


def _assert_customer_scan_clean(packet):
    scanner = CustomerOutputScanner(FORBIDDEN_CUSTOMER_PATTERNS)
    scan = scanner.scan(packet)
    assert scan["pass"], scan


def test_phase2_static_verified_corpus_slice_is_real_source_backed_not_generated_round_robin():
    corpus = load_verified_corpus_slice()
    assert 25 <= len(corpus.records) <= 50
    assert {record.vertical for record in corpus.records} >= {"restaurant_ti", "medical_clinic_ti", "office_ti"}
    assert corpus.source_artifacts
    assert not corpus.generated_from_round_robin
    for record in corpus.records:
        assert record.ahj.city and record.ahj.state
        assert record.exact_permit_name or record.official_portal_category_path
        assert record.provenance.source_url.startswith("http")
        assert record.provenance.exact_quote_or_snippet
        assert re.fullmatch(r"[a-f0-9]{64}", record.provenance.source_content_hash_sha256)
        assert record.provenance.retrieved_at_utc.endswith("Z")


def test_phase2_customer_lookup_has_only_verified_final_or_pending_active_retrieval_states(tmp_path):
    engine = PermitAssist3RevisedEngine(ticket_path=tmp_path / "tickets.jsonl", writeback_path=tmp_path / "writeback.jsonl")

    verified = engine.lookup("restaurant tenant improvement with hood", "Austin", "TX")
    assert verified["public_state"] == VERIFIED_FINAL
    assert verified["customer_final"] is True
    assert verified["source_backed_exact_permit_name"] or verified["source_backed_official_portal_category_path"]
    assert verified["official_source_provenance"]
    assert verified["completion_ticket"] is None
    assert verified["state_overlay"]["wired_into_customer_path"] is True
    _assert_customer_scan_clean(verified["customer_output"])

    pending = engine.lookup("restaurant tenant improvement", "Plano", "TX", live_retriever=lambda *_args, **_kwargs: [])
    assert pending["public_state"] == PENDING_ACTIVE_RETRIEVAL
    assert pending["customer_final"] is False
    assert pending["source_backed_exact_permit_name"] is None
    assert pending["source_backed_official_portal_category_path"] is None
    ticket = pending["completion_ticket"]
    assert ticket["tracker_id"].startswith("pa3-")
    assert ticket["owner"] == "PermitAssist retrieval queue"
    assert 0 < ticket["sla_hours"] <= 24
    assert ticket["alarm"]["on_sla_breach"] == "escalate_to_owner_and_notify_controller"
    assert ticket["writeback"]["required"] is True
    assert pending["missing_fields"]
    assert pending["live_retrieval"]["attempted"] is True
    _assert_customer_scan_clean(pending["customer_output"])


def test_phase2_live_official_source_retrieval_can_promote_to_verified_final_and_writeback(tmp_path):
    def fake_live_retriever(_job, ahj, _vertical):
        return [
            {
                "official_source_classification": "delegated_or_ahj_official",
                "city": ahj["city"],
                "state": ahj["state"],
                "vertical": "office_ti",
                "official_portal_category_path": "Commercial Building > Tenant Improvement",
                "source_url": "https://www.sandiego.gov/development-services/permits",
                "source_title": "City of San Diego Development Services Permits",
                "exact_quote_or_snippet": "Commercial Building > Tenant Improvement applications are submitted through the City permitting portal.",
                "retrieved_at_utc": "2026-05-22T00:00:00Z",
                "source_content_hash_sha256": "a" * 64,
            }
        ]

    writeback = tmp_path / "writeback.jsonl"
    engine = PermitAssist3RevisedEngine(ticket_path=tmp_path / "tickets.jsonl", writeback_path=writeback)
    result = engine.lookup("office tenant improvement", "San Diego", "CA", live_retriever=fake_live_retriever)
    assert result["public_state"] == VERIFIED_FINAL
    assert result["source_backed_official_portal_category_path"] == "Commercial Building > Tenant Improvement"
    assert result["live_retrieval"]["promoted_to_verified_final"] is True
    assert writeback.exists()
    written = [json.loads(line) for line in writeback.read_text(encoding="utf-8").splitlines()]
    assert written and written[-1]["packet"]["public_state"] == VERIFIED_FINAL
    _assert_customer_scan_clean(result["customer_output"])


def test_final_gate_rejects_generic_permit_required_and_routes_pending(tmp_path):
    gate = PermitAssist3RevisedFinalGate()
    generic_packet = {
        "public_state": VERIFIED_FINAL,
        "customer_output": {"headline": "Permit required — check with AHJ"},
        "source_backed_exact_permit_name": None,
        "source_backed_official_portal_category_path": None,
        "official_source_provenance": [],
    }
    ok, reasons = gate.validate_verified_final(generic_packet)
    assert ok is False
    assert "missing_source_backed_exact_name_or_portal_path" in reasons
    assert "forbidden_customer_final_phrase" in reasons

    engine = PermitAssist3RevisedEngine(ticket_path=tmp_path / "tickets.jsonl", writeback_path=tmp_path / "writeback.jsonl")
    result = engine.lookup("weird commercial buildout", "Real City", "MA", live_retriever=lambda *_args, **_kwargs: [generic_packet])
    assert result["public_state"] == PENDING_ACTIVE_RETRIEVAL
    assert result["customer_final"] is False
    _assert_customer_scan_clean(result["customer_output"])


def test_phase3_state_overlays_are_source_backed_and_customer_path_wired_for_all_wedges(tmp_path):
    registry = StateOverlayRegistry()
    cases = [
        ("Los Angeles", "CA", "restaurant_ti"),
        ("Miami", "FL", "medical_clinic_ti"),
        ("Dallas", "TX", "office_ti"),
        ("Boston", "MA", "restaurant_ti"),
    ]
    engine = PermitAssist3RevisedEngine(ticket_path=tmp_path / "tickets.jsonl", writeback_path=tmp_path / "writeback.jsonl")
    for city, state, vertical in cases:
        overlay = registry.for_state_and_vertical(state, vertical)
        assert overlay["state"] == state
        assert overlay["wired_into_customer_path"] is True
        assert overlay["source_backed"] is True
        assert overlay["provenance"]
        assert all(item["source_url"].startswith("http") for item in overlay["provenance"])
        packet = engine.lookup(f"{vertical.replace('_', ' ')} project", city, state)
        assert packet["state_overlay"]["state"] == state
        assert packet["state_overlay"]["vertical"] == vertical
        assert packet["state_overlay"]["wired_into_customer_path"] is True
        _assert_customer_scan_clean(packet["customer_output"])
