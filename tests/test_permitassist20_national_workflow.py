import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

SAFE_INTERIM = "Manual filing path confirmation in progress"


def _verified_cell(**overrides):
    cell = {
        "ahj_id": "phoenix-az",
        "ahj_name": "City of Phoenix",
        "trade": "electrical",
        "scope": "commercial_solar_battery_400a",
        "field": "portal_category",
        "value": "Commercial Alteration Permit",
        "source_url": "https://www.phoenix.gov/pdd/permits",
        "source_title": "Phoenix Planning and Development permits",
        "source_quote": "Commercial projects apply through the ShapePHX portal for building permits.",
        "source_type": "official_ahj",
        "field_support": "direct_quote",
        "freshness_days": 10,
        "freshness_expires_in_days": 20,
    }
    cell.update(overrides)
    return cell


def test_phase3_promotion_gate_requires_official_quote_field_support_and_freshness():
    from permitassist20_national_workflow import evaluate_evidence_cell

    promoted = evaluate_evidence_cell(_verified_cell())
    assert promoted.status == "promoted"
    assert promoted.can_drive_auto_answer is True

    no_quote = evaluate_evidence_cell(_verified_cell(source_quote=""))
    assert no_quote.status == "needs_verification"
    assert "missing_source_quote" in no_quote.reasons
    assert no_quote.can_drive_auto_answer is False

    weak_source = evaluate_evidence_cell(_verified_cell(source_type="private_aggregator"))
    assert weak_source.status == "needs_verification"
    assert "non_official_source" in weak_source.reasons

    stale = evaluate_evidence_cell(_verified_cell(freshness_days=45, freshness_expires_in_days=-1))
    assert stale.status == "needs_verification"
    assert "stale_or_expired_source" in stale.reasons


def test_phase3_manual_completion_ticket_has_required_private_research_fields(tmp_path):
    from permitassist20_national_workflow import create_research_ticket, write_research_ticket

    ticket = create_research_ticket(
        scenario="Commercial solar PV with battery and 400A service upgrade",
        ahj_stack=["City of Phoenix", "Arizona state electrical"],
        detected_scopes=["commercial_solar", "battery_ess", "service_upgrade_400a"],
        candidate_sources=[{"url": "https://www.phoenix.gov/pdd/permits", "title": "Phoenix permits"}],
        missing_fields=["official_application_title", "inspection_sequence"],
        tried_urls=["https://www.phoenix.gov/pdd/permits"],
        suggested_queries=["site:phoenix.gov ShapePHX commercial solar permit"],
        owner="research",
        sla_hours=24,
    )

    required = {
        "scenario",
        "ahj_stack",
        "detected_scopes",
        "candidate_sources",
        "missing_fields",
        "tried_urls",
        "suggested_queries",
        "owner",
        "sla_hours",
        "customer_visible_status",
    }
    assert required <= set(ticket)
    assert ticket["customer_visible_status"] == SAFE_INTERIM
    assert ticket["owner"] == "research"

    artifact = write_research_ticket(ticket, tmp_path)
    assert artifact.exists()
    assert artifact.parent == tmp_path
    saved = json.loads(artifact.read_text())
    assert saved["ticket_id"] == ticket["ticket_id"]


def test_phase4_benchmark_gate_fails_when_permitassist_loses_and_logs_ticket_artifact(tmp_path):
    from permitassist20_national_workflow import run_benchmark_gate

    cases = [
        {
            "case_id": "loss-case",
            "permitassist": {
                "permit_verdict": "YES",
                "permit_required": True,
                "permit_type": SAFE_INTERIM,
                "permit_name": SAFE_INTERIM,
                "permit_type_verified": False,
                "warnings": ["Manual filing path check is in progress."],
            },
            "baseline_text": "Permit required. Select Commercial Alteration Permit in the official portal. Submit plans and schedule rough and final inspections.",
            "owner": "research",
        }
    ]

    result = run_benchmark_gate(cases, ticket_dir=tmp_path)
    assert result["release_gate"] == "fail"
    assert result["losses"][0]["case_id"] == "loss-case"
    assert result["losses"][0]["owner"] == "research"
    assert result["losses"][0]["root_cause"]
    assert Path(result["losses"][0]["ticket_artifact"]).exists()


def test_phase4_benchmark_gate_is_deterministic_for_same_inputs(tmp_path):
    from permitassist20_national_workflow import run_benchmark_gate

    case = {
        "case_id": "phoenix-win",
        "permitassist": {
            "permit_verdict": "YES",
            "permit_required": True,
            "permit_type": "Commercial Alteration Permit",
            "permit_name": "Commercial Alteration Permit",
            "permit_type_verified": True,
            "apply_path": {"portal": "ShapePHX", "permit_type": "Commercial Alteration Permit", "support_level": "verified path"},
            "applying_office": "Phoenix Planning and Development Department",
            "docs_required": ["plans"],
            "inspections": ["rough", "final"],
            "sources": [{"url": "https://www.phoenix.gov/pdd/permits", "snippet": "official permits"}],
        },
        "baseline_text": "You likely need a permit. Check with Phoenix.",
    }

    first = run_benchmark_gate([case], ticket_dir=tmp_path / "one")
    second = run_benchmark_gate([case], ticket_dir=tmp_path / "two")
    assert first["release_gate"] == second["release_gate"] == "pass"
    assert first["case_results"] == second["case_results"]


def test_phase5_route_verified_auto_interim_manual_and_invalid_ahj_fail_closed(tmp_path):
    from permitassist20_national_workflow import route_lookup_outcome

    verified = route_lookup_outcome(
        scenario="solar battery service upgrade",
        ahj_stack=["City of Phoenix"],
        detected_scopes=["commercial_solar"],
        evidence_cells=[_verified_cell()],
        candidate_sources=[],
        tried_urls=[],
        ticket_dir=tmp_path,
    )
    assert verified["route"] == "verified_auto"
    assert verified["permit_type"] == "Commercial Alteration Permit"
    assert verified["permit_type_verified"] is True

    interim = route_lookup_outcome(
        scenario="medical clinic tenant improvement",
        ahj_stack=["City of Phoenix"],
        detected_scopes=["medical_ti"],
        evidence_cells=[_verified_cell(source_quote="")],
        candidate_sources=[{"url": "https://www.phoenix.gov/pdd/permits"}],
        tried_urls=["https://www.phoenix.gov/pdd/permits"],
        ticket_dir=tmp_path,
    )
    assert interim["route"] == "safe_interim_manual"
    assert interim["permit_type"] == SAFE_INTERIM
    assert interim["permit_type_verified"] is False
    assert Path(interim["manual_completion_ticket"]).exists()

    invalid = route_lookup_outcome(
        scenario="restaurant TI",
        ahj_stack=["Definitely Fake AHJ"],
        detected_scopes=["restaurant_ti"],
        evidence_cells=[],
        candidate_sources=[],
        tried_urls=[],
        ticket_dir=tmp_path,
        ahj_valid=False,
    )
    assert invalid["route"] == "invalid_ahj_fail_closed"
    assert invalid["permit_type"] == SAFE_INTERIM
    assert invalid["permit_type_verified"] is False
    assert invalid["manual_completion_ticket"] is None


def test_phase5_research_ticket_fields_do_not_leak_into_customer_visible_result(tmp_path):
    from permitassist20_national_workflow import route_lookup_outcome
    import server

    outcome = route_lookup_outcome(
        scenario="office tenant improvement",
        ahj_stack=["City of Phoenix"],
        detected_scopes=["office_ti"],
        evidence_cells=[_verified_cell(source_quote="")],
        candidate_sources=[{"url": "https://internal.example/candidate", "title": "draft candidate"}],
        tried_urls=["https://internal.example/tried"],
        suggested_queries=["site:phoenix.gov office TI permit"],
        ticket_dir=tmp_path,
    )

    customer = server.sanitize_customer_visible_result(outcome["customer_result"], "office tenant improvement", "Phoenix", "AZ")
    visible = json.dumps(customer)
    assert "candidate_sources" not in visible
    assert "tried_urls" not in visible
    assert "suggested_queries" not in visible
    assert "internal.example" not in visible
    assert SAFE_INTERIM in visible
