import copy
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
for path in (ROOT, API):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from api.permit_manifest import (  # noqa: E402
    FAMILIES,
    MANIFEST_FLAG,
    build_permit_manifest_projection,
    canonical_family,
)
from api import permit_manifest as permit_manifest_module  # noqa: E402


def _payload() -> dict:
    return {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_kind": "Commercial Building / Tenant Improvement",
        "permit_name": "Commercial Tenant Improvement Permit",
        "city": "Anchorage",
        "state": "AK",
        "applying_office": "Municipality of Anchorage Development Services",
        "source_urls": ["https://www.muni.org/official-permits"],
        "customer_result_summary": {
            "jurisdiction": {
                "name": "Municipality of Anchorage",
                "type": "municipality",
                "city": "Anchorage",
                "state": "AK",
                "jurisdiction_id": "us-ak-anchorage",
            }
        },
        "permits_required": [
            {
                "permit_type": "Commercial Tenant Improvement Permit",
                "filing_family": "building",
                "status": "REQUIRED",
                "required": True,
                "source_url": "https://www.muni.org/official-permits",
                "scope_trigger": "commercial_tenant_improvement",
            },
            {
                "permit_type": "Electrical Permit",
                "filing_family": "electrical",
                "status": "REQUIRED",
                "required": True,
                "source_ref": "cell:w4:anchorage:electrical",
                "source_url": "https://www.muni.org/electrical",
            },
        ],
        "related_permits": [
            {
                "permit_type": "Fire alarm review",
                "filing_family": "fire",
                "decision": "CONDITIONAL",
                "required_if": "fire alarm devices are altered",
                "source_url": "https://www.muni.org/fire-review",
            },
            {
                "permit_type": "Accessibility review",
                "filing_family": "accessibility",
                "decision": "REQUIRED",
                "rationale": "Commercial work commonly triggers accessibility review.",
            },
        ],
        "companion_permits": [
            {
                "permit_type": "Planning review",
                "family": "zoning",
                "status": "CONDITIONAL",
                "trigger": "change of use",
            }
        ],
        "apply_url": "https://www.muni.org/permit-portal",
        "apply_path": {"portal_url": "https://www.muni.org/permit-portal"},
    }


def test_session2_canonical_family_uses_sealed_ontology_not_display_label():
    assert canonical_family("Commercial Building / Tenant Improvement") == "BUILDING"
    assert canonical_family("Fire review") == "FIRE_LIFE_SAFETY"
    assert canonical_family("certificate of occupancy") == "OCCUPANCY_CO"
    assert canonical_family("Sign Permit") == "SIGN"
    assert canonical_family("Fuel Gas Permit") == "GAS"
    assert canonical_family("unmapped made up family") == "VERIFY"
    adversarial_expectations = {
        "design review": "OTHER",
        "facade design review": "OTHER",
        "assignment review": "OTHER",
        "assigned building review": "BUILDING",
        "Las Vegas building permit": "BUILDING",
        "Vegas permit office": "OTHER",
    }
    for adversarial_label, expected_family in adversarial_expectations.items():
        assert canonical_family(adversarial_label) == expected_family


def test_session2_verify_row_keeps_compatibility_boolean_indeterminate():
    row = permit_manifest_module._canonical_row({
        "permit_type": "Electrical Permit",
        "family": "electrical",
        "status": "VERIFY",
    }, primary=True)
    assert row["status"] == "VERIFY"
    assert row["required"] is None


def test_session2_flag_off_is_exact_object_and_byte_identity(monkeypatch):
    monkeypatch.delenv(MANIFEST_FLAG, raising=False)
    payload = _payload()
    before = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    projected = build_permit_manifest_projection(payload)
    after = json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode()
    assert projected == payload
    assert after == before


def test_session2_flag_on_builds_manifest_without_mutating_decision(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "shadow")
    payload = _payload()
    projected = build_permit_manifest_projection(payload)
    manifest = projected["permit_manifest"]
    assert projected["permit_decision"] == payload["permit_decision"]
    assert projected["permit_required"] is payload["permit_required"]
    assert projected["permit_verdict"] == payload["permit_verdict"]
    assert projected["primary_permit_family"] == "BUILDING"
    assert manifest["permit_decision"] == "REQUIRED"
    assert manifest["primary"]["family"] == "BUILDING"
    assert manifest["primary"]["local_name"] == "Commercial Tenant Improvement Permit"
    assert manifest["jurisdiction"] == projected["jurisdiction_identity"]
    assert manifest["jurisdiction"]["jurisdiction_id"] == "us-ak-anchorage"
    assert manifest["jurisdiction"]["name"] == "Municipality of Anchorage"


def test_session2_source_backed_companions_preserved_and_unsourced_demoted(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    projected = build_permit_manifest_projection(_payload())
    companions = projected["permit_manifest"]["companions"]
    by_family = {}
    for row in companions:
        by_family.setdefault(row["family"], []).append(row)
    assert "BUILDING" not in by_family, "primary family must never be repeated as a companion"
    assert all(len(rows) == 1 for rows in by_family.values()), "one customer row per companion family"
    electrical = by_family["ELECTRICAL"][0]
    fire = by_family["FIRE_LIFE_SAFETY"][0]
    accessibility = by_family["ACCESSIBILITY"][0]
    zoning = by_family["ZONING_PLANNING"][0]
    assert electrical["status"] == "REQUIRED"
    assert electrical["source_ref"] == "cell:w4:anchorage:electrical"
    assert fire["status"] == "CONDITIONAL"
    assert fire["source_ref"] == "https://www.muni.org/fire-review"
    assert accessibility["status"] == "VERIFY"
    assert accessibility["required"] is None
    assert accessibility["source_ref"] is None
    assert "confirm" in accessibility["customer_guidance"].lower()
    assert zoning["status"] == "VERIFY"
    assert zoning["required"] is None
    assert zoning["source_ref"] is None
    assert all(
        row.get("source_ref")
        for row in companions
        if row.get("status") in {"REQUIRED", "CONDITIONAL"}
    )
    assert any(row.get("source_ref") == "cell:w4:anchorage:electrical" for row in companions)
    assert any(row.get("source_ref") == "https://www.muni.org/fire-review" for row in companions)


def test_session2_companion_uses_same_family_typed_route_provenance(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    payload = _payload()
    electrical = payload["permits_required"][1]
    electrical.pop("source_ref")
    electrical.pop("source_url")
    payload["family_authority_routes"] = [{
        "family": "electrical",
        "authority": {"application_authority": "Anchorage Electrical Inspections"},
        "application_route": {
            "office_name": "Anchorage Electrical Inspections",
            "apply_url": "https://www.muni.org/electrical-application",
            "channel": "online_portal",
            "rationale": "Typed electrical route requires separate filing.",
            "provenance": [
                {"source_url": "https://www.muni.org/electrical-route"},
                {"source_url": "https://www.muni.org/electrical-checklist"},
            ],
        },
    }]
    projected = build_permit_manifest_projection(payload)
    row = next(item for item in projected["permit_manifest"]["companions"] if item["family"] == "ELECTRICAL")
    assert row["status"] == "REQUIRED"
    assert row["source_ref"] == "https://www.muni.org/electrical-route"
    assert row["source_refs"] == [
        "https://www.muni.org/electrical-route",
        "https://www.muni.org/electrical-checklist",
    ]
    assert row["rationale"] == "Typed electrical route requires separate filing."
    assert row["authority"] == "Anchorage Electrical Inspections"
    assert row["apply_url"] == "https://www.muni.org/electrical-application"
    assert row["route_channel"] == "online_portal"


def test_session2_projection_is_idempotent_and_does_not_add_companions(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    payload = _payload()
    once = build_permit_manifest_projection(payload)
    twice = build_permit_manifest_projection(copy.deepcopy(once))
    assert twice == once
    original_rows = (
        len(payload["permits_required"]) - 1
        + len(payload["related_permits"])
        + len(payload["companion_permits"])
    )
    assert len(once["permit_manifest"]["companions"]) <= original_rows


def test_session2_conflicting_manifest_reentry_rejects_stale_projection(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    projected = build_permit_manifest_projection(_payload())
    projected["permit_decision"] = "NOT_REQUIRED"
    projected["permit_required"] = False
    failed = build_permit_manifest_projection(projected)
    assert failed["permit_decision"] == "NOT_REQUIRED"
    assert failed["permit_manifest"]["permit_decision"] == "NOT_REQUIRED"
    assert failed["permit_manifest"] != projected["permit_manifest"]

    stale_row = build_permit_manifest_projection(_payload())
    stale_row["permits_required"][1]["status"] = "NOT_REQUIRED"
    stale_row["permits_required"][1]["source_ref"] = "https://tampered.invalid/source"
    rebuilt_row = build_permit_manifest_projection(stale_row)
    assert rebuilt_row != stale_row
    assert rebuilt_row["permit_manifest"] != stale_row["permit_manifest"]
    assert rebuilt_row["permit_manifest"]["companions"][0]["status"] == "NOT_REQUIRED"

    tampered_manifest = build_permit_manifest_projection(_payload())
    tampered_manifest["permit_manifest"]["primary"]["rationale"] = "tampered"
    rebuilt_manifest = build_permit_manifest_projection(tampered_manifest)
    assert rebuilt_manifest != tampered_manifest
    assert rebuilt_manifest["permit_manifest"]["primary"].get("rationale") != "tampered"


def test_session2_preserves_multiple_source_refs_and_rationale(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    payload = _payload()
    row = payload["permits_required"][1]
    row["notes"] = "Panel replacement and feeder work require electrical review."
    row["evidence"] = [
        {"source_url": "https://www.muni.org/electrical-one"},
        {"source_url": "https://www.muni.org/electrical-two"},
    ]
    companion = build_permit_manifest_projection(payload)["permit_manifest"]["companions"][0]
    assert companion["rationale"] == row["notes"]
    assert companion["source_refs"] == [
        "cell:w4:anchorage:electrical",
        "https://www.muni.org/electrical",
        "https://www.muni.org/electrical-one",
        "https://www.muni.org/electrical-two",
    ]


def test_session2_untrusted_nested_surface_is_projected_once(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    wrapper = {"share": {"data": _payload()}, "other": "unchanged"}
    projected = build_permit_manifest_projection(wrapper)
    assert projected["other"] == "unchanged"
    assert projected["share"]["data"]["permit_manifest"]["primary"]["family"] == "BUILDING"


def _server(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    from api import server

    return server


def test_session2_server_api_egress_uses_single_manifest_projection(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    server = _server(monkeypatch)
    projected = server.project_customer_response_egress(_payload())
    assert projected["permit_manifest"]["primary"]["family"] == "BUILDING"
    assert projected["primary_permit_family"] == "BUILDING"
    assert projected["jurisdiction_identity"]["jurisdiction_id"] == "us-ak-anchorage"
    assert projected["companion_permits"] == projected["permit_manifest"]["companions"]
    assert server.project_customer_response_egress(projected) == projected


def test_session2_report_allowlist_and_white_label_render_manifest(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    server = _server(monkeypatch)
    payload = _payload()
    payload["family_authority_routes"] = [{
        "family": "electrical",
        "authority": {"application_authority": "Anchorage Electrical Inspections"},
        "application_route": {
            "office_name": "Anchorage Electrical Inspections",
            "apply_url": "https://www.muni.org/electrical-application",
            "channel": "online_portal",
            "provenance": [{"source_url": "https://www.muni.org/electrical-route"}],
        },
    }]
    projected = server.project_customer_response_egress(payload)
    public = server.to_public_share_payload(
        {"data": projected, "job_type": "commercial tenant improvement", "city": "Anchorage", "state": "AK"},
        {},
    )
    assert public["share"]["data"]["permit_manifest"] == projected["permit_manifest"]
    white_label = server.render_white_label_report_html({
        "result": server._VerifiedCustomerProjection(projected),
        "job_type": "commercial tenant improvement",
        "job_category": "commercial",
        "city": "Anchorage",
        "state": "AK",
    })
    assert "Commercial Tenant Improvement Permit" in white_label
    assert "Accessibility review" in white_label
    assert "VERIFY" in white_label
    assert "Anchorage Electrical Inspections" in white_label
    assert "https://www.muni.org/electrical-application" in white_label


def test_session2_verified_share_render_embeds_same_manifest(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    server = _server(monkeypatch)
    projected = server.project_customer_response_egress(_payload())
    canonical = json.dumps(projected, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    import hashlib

    share = server._VerifiedSharePayload({
        "data": projected,
        "job_type": "commercial tenant improvement",
        "city": "Anchorage",
        "state": "AK",
    })
    share._verified_payload_sha256 = hashlib.sha256(canonical.encode()).hexdigest()
    rendered = server.render_share_page(share)
    assert '"permit_manifest"' in rendered
    assert '"primary_permit_family":"BUILDING"' in rendered
    assert "Accessibility review" in rendered


def test_session2_flag_off_shipped_frontend_assets_are_opening_byte_identical():
    import hashlib

    assert hashlib.sha256((ROOT / "frontend" / "index.html").read_bytes()).hexdigest() == "c7e570fa9c8a57c84bb344b736742df5660a39761d77aaf88ddea4829c1489f0"
    assert hashlib.sha256((ROOT / "frontend" / "report.html").read_bytes()).hexdigest() == "4294e4fcecaf2317c89bf729c543843a983d0b5e25cd6b9764cd57e057d2da36"


def test_session2_runtime_family_projection_closes_over_frozen_ontology():
    ontology = json.loads((ROOT / "benchmarks" / "permit_accuracy_v1_2" / "permit_family_ontology_v1.json").read_text())
    assert set(FAMILIES) == set(ontology["families"])
    for alias, expected in ontology["exact_aliases"].items():
        assert canonical_family(alias) == expected
    for rule in ontology["ordered_display_rules"]:
        for phrase in rule["contains_any"]:
            assert canonical_family(phrase) == rule["family"]


def test_session2_runtime_protected_hash_successor_links_to_frozen_session1_record():
    baseline = json.loads((ROOT / "benchmarks" / "permit_accuracy_v1_2" / "SESSION1_OPENING_BASELINE.json").read_text())
    successor = json.loads((ROOT / "benchmarks" / "permit_accuracy_v1_2_session2" / "SESSION2_RUNTIME_PROTECTED_HASH_SUCCESSOR.json").read_text())
    authorized = successor["authorized_successors"]
    assert set(authorized) == {"api/server.py"}
    server_record = authorized["api/server.py"]
    assert server_record["session1_opening_sha256"] == baseline["protected_path_sha256"]["api/server.py"]
    assert len(server_record["session2_current_sha256"]) == 64
    assert all(character in "0123456789abcdef" for character in server_record["session2_current_sha256"])
    assert successor["session1_artifacts_unchanged"] is True
    frozen_test = ROOT / "benchmarks" / "permit_accuracy_v1_2" / "test_benchmark_v12.py"
    assert hashlib.sha256(frozen_test.read_bytes()).hexdigest() == "0435199886e13c80739ff106ea2a52a815c042bd2f7058f170f5f40d8d106c09"
