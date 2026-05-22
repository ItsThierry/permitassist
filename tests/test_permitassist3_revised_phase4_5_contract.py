# pyright: reportMissingImports=false
import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
sys.path.insert(0, str(API))

from permitassist3_revised import (  # noqa: E402
    PENDING_ACTIVE_RETRIEVAL,
    VERIFIED_FINAL,
    CustomerOutputScanner,
    ExpertCompletionConsole,
    PermitAssist3LaunchQualityGate,
    PermitAssist3RevisedEngine,
    _utc_now,
)


def test_phase4_console_lists_pending_tickets_builds_review_payload_and_sla_alarm(tmp_path):
    ticket_path = tmp_path / "tickets.jsonl"
    writeback_path = tmp_path / "writeback.jsonl"
    event_path = tmp_path / "events.jsonl"
    engine = PermitAssist3RevisedEngine(ticket_path=ticket_path, writeback_path=writeback_path)

    pending = engine.lookup("restaurant tenant improvement", "Plano", "TX", live_retriever=lambda *_args, **_kwargs: [])
    assert pending["public_state"] == PENDING_ACTIVE_RETRIEVAL

    console = ExpertCompletionConsole(ticket_path=ticket_path, writeback_path=writeback_path, event_path=event_path)
    open_tickets = console.list_open_tickets()
    assert [ticket["tracker_id"] for ticket in open_tickets] == [pending["completion_ticket"]["tracker_id"]]

    payload = console.build_review_payload(pending["completion_ticket"]["tracker_id"])
    assert payload["schema"] == "permitassist3.phase4.expert_completion_console_payload.v1"
    assert payload["customer_scenario"]["vertical"] == "restaurant_ti"
    assert payload["ahj_stack"]["ahj_id"] == "tx_plano"
    assert payload["missing_exact_names"]
    assert "exact_permit_name_or_official_portal_category_path" in payload["required_evidence_fields"]
    assert payload["writeback_action"]["requires_final_gate_pass"] is True
    assert any(track["track"] == "health_food_establishment" for track in payload["inferred_permit_families"])

    breaches = console.sla_breaches(now=_utc_now() + timedelta(hours=25))
    assert breaches and breaches[0]["tracker_id"] == pending["completion_ticket"]["tracker_id"]
    assert breaches[0]["alarm"]["on_sla_breach"] == "escalate_to_owner_and_notify_controller"


def test_phase4_resolution_requires_verified_packet_and_writes_back(tmp_path):
    ticket_path = tmp_path / "tickets.jsonl"
    writeback_path = tmp_path / "writeback.jsonl"
    event_path = tmp_path / "events.jsonl"
    engine = PermitAssist3RevisedEngine(ticket_path=ticket_path, writeback_path=writeback_path)
    pending = engine.lookup("office tenant improvement", "Real City", "MA", live_retriever=lambda *_args, **_kwargs: [])
    tracker_id = pending["completion_ticket"]["tracker_id"]

    console = ExpertCompletionConsole(ticket_path=ticket_path, writeback_path=writeback_path, event_path=event_path)
    resolved = console.resolve_with_evidence(
        tracker_id,
        official_portal_category_path="Commercial > Building Permit > Tenant Improvement",
        apply_url="https://example.gov/permits/commercial-ti",
        source_url="https://example.gov/permits/commercial-ti",
        source_title="Example AHJ Commercial Tenant Improvement Permits",
        exact_quote_or_snippet="Commercial > Building Permit > Tenant Improvement is the official portal category for commercial alteration work.",
        retrieved_at_utc="2026-05-22T00:00:00Z",
        source_content_hash_sha256="b" * 64,
    )

    assert resolved["packet"]["public_state"] == VERIFIED_FINAL
    assert resolved["event"]["event"] == "phase4_resolution_written_back"
    assert resolved["event"]["writeback_record_appended"] is True
    assert resolved["event"]["corpus_promotion_required"] is True
    assert resolved["event"]["cold_ahj_became_warm"] is False
    assert resolved["event"]["repeat_lookup_ready_after_corpus_promotion"] is False
    assert resolved["event"]["sla_measurable"] is True
    assert writeback_path.exists()
    written = [json.loads(line) for line in writeback_path.read_text(encoding="utf-8").splitlines()]
    assert written[-1]["packet"]["resolved_from_tracker_id"] == tracker_id
    assert console.list_open_tickets() == []


def test_phase4_rejects_resolution_without_source_backed_exact_name_or_path(tmp_path):
    ticket_path = tmp_path / "tickets.jsonl"
    writeback_path = tmp_path / "writeback.jsonl"
    event_path = tmp_path / "events.jsonl"
    engine = PermitAssist3RevisedEngine(ticket_path=ticket_path, writeback_path=writeback_path)
    pending = engine.lookup("restaurant tenant improvement", "Plano", "TX", live_retriever=lambda *_args, **_kwargs: [])
    console = ExpertCompletionConsole(ticket_path=ticket_path, writeback_path=writeback_path, event_path=event_path)

    try:
        console.resolve_with_evidence(
            pending["completion_ticket"]["tracker_id"],
            source_url="https://example.gov/permits",
            source_title="Example permits",
            exact_quote_or_snippet="Permit information.",
            retrieved_at_utc="2026-05-22T00:00:00Z",
            source_content_hash_sha256="c" * 64,
        )
    except ValueError as exc:
        assert "exact permit name or official portal category/path is required" in str(exc)
    else:
        raise AssertionError("Phase 4 accepted a generic/non-exact resolution")
    assert not writeback_path.exists()
    assert not event_path.exists()


def test_phase4_resolved_tracker_ids_ignore_blank_events(tmp_path):
    ticket_path = tmp_path / "tickets.jsonl"
    writeback_path = tmp_path / "writeback.jsonl"
    event_path = tmp_path / "events.jsonl"
    engine = PermitAssist3RevisedEngine(ticket_path=ticket_path, writeback_path=writeback_path)
    pending = engine.lookup("restaurant tenant improvement", "Plano", "TX", live_retriever=lambda *_args, **_kwargs: [])
    event_path.write_text(json.dumps({"event": "phase4_resolution_written_back"}) + "\n", encoding="utf-8")

    console = ExpertCompletionConsole(ticket_path=ticket_path, writeback_path=writeback_path, event_path=event_path)

    assert [ticket["tracker_id"] for ticket in console.list_open_tickets()] == [pending["completion_ticket"]["tracker_id"]]


def test_customer_output_scanner_rejects_generic_verified_final_without_source_backing():
    scanner = CustomerOutputScanner()
    report = {
        "rows": [
            {
                "scenario": {"vertical": "restaurant_ti"},
                "result": {
                    "public_state": VERIFIED_FINAL,
                    "customer_output": {"filing_path": "Building Permit"},
                    "official_source_provenance": [],
                    "source_backed_exact_permit_name": "Building Permit",
                    "source_backed_official_portal_category_path": None,
                },
            }
        ]
    }

    scan = scanner.scan(report)

    assert scan["pass"] is False
    assert scan["hits"]


def test_phase5_launch_quality_gate_negative_case_fails_when_scanner_finds_generic_final(tmp_path):
    gate = PermitAssist3LaunchQualityGate(report_path=tmp_path / "phase5_negative.json")
    row = gate._evaluate_case(
        {
            "case_id": "negative-generic-final",
            "job_type": "restaurant tenant improvement",
            "city": "Austin",
            "state": "TX",
            "vertical": "restaurant_ti",
            "expected_state": VERIFIED_FINAL,
            "baseline_best_tool": "manual_search_fixture",
            "baseline_best_exact_packet_complete": False,
        },
        {
            "public_state": VERIFIED_FINAL,
            "customer_final": True,
            "vertical": "restaurant_ti",
            "customer_output": {"filing_path": "Building Permit"},
            "official_source_provenance": [],
            "source_backed_exact_permit_name": "Building Permit",
            "source_backed_official_portal_category_path": None,
        },
    )

    assert row["customer_scan_pass"] is False
    assert row["ok"] is False


def test_phase5_launch_quality_gate_passes_target_verticals_without_generic_finals(tmp_path):
    report_path = tmp_path / "phase5_report.json"
    report = PermitAssist3LaunchQualityGate(report_path=report_path).run()

    assert report_path.exists()
    assert report["schema"] == "permitassist3.phase5.launch_quality_gate.v1"
    assert report["acceptance"]["phase5_launch_quality_gate_pass"] is True
    assert report["acceptance"]["restaurant_ti_beats_baseline"] is True
    assert report["acceptance"]["medical_clinic_ti_beats_baseline"] is True
    assert report["acceptance"]["office_ti_beats_baseline"] is True
    assert report["acceptance"]["no_final_generic_output"] is True
    assert report["acceptance"]["unresolved_cases_have_sla_backed_completion_path"] is True
    assert all(row["ok"] for row in report["rows"])

    scanner = CustomerOutputScanner()
    assert scanner.scan(report)["pass"], scanner.scan(report)
