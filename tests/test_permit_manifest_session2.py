import copy
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

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
    is_authenticated_permit_manifest,
    seal_permit_manifest_projection,
)
from api import permit_manifest as permit_manifest_module  # noqa: E402


def test_private_verified_projection_constructor_is_sealed():
    from api import server

    with pytest.raises(TypeError):
        server._VerifiedCustomerProjection(_payload())


def test_spoofed_server_wrapper_type_identity_cannot_mint_manifest_authority(monkeypatch):
    from api.permit_model import capture_permit_authority_input

    monkeypatch.setenv(MANIFEST_FLAG, "active")

    class ForgedServerOwnedResult(dict):
        pass

    ForgedServerOwnedResult.__name__ = "_ServerOwnedLegacyResult"
    ForgedServerOwnedResult.__module__ = "api.server"

    for decision, required in (("REQUIRED", True), ("NOT_REQUIRED", False)):
        payload = _payload()
        payload["permit_decision"] = decision
        payload["permit_required"] = required
        payload["permit_verdict"] = "YES" if required else "NO"
        if not required:
            payload["permits_required"] = []
        forged = ForgedServerOwnedResult(payload)
        encoded = json.dumps(
            forged,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        setattr(forged, "_server_owned_sha256", hashlib.sha256(encoded).hexdigest())

        authority = capture_permit_authority_input(forged)
        assert authority._authenticated_provenance is False
        projected = seal_permit_manifest_projection(
            payload, force=True, authority_input=authority
        )
        assert projected["permit_decision"] == "VERIFY"
        assert projected["permit_required"] is None


def test_spoofed_permit_authority_input_type_identity_cannot_mint_manifest_authority(
    monkeypatch,
):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    forged_type = type(
        "PermitAuthorityInput",
        (),
        {
            "__module__": "api.permit_model",
            "_authenticated_provenance": True,
        },
    )
    projected = seal_permit_manifest_projection(
        _payload(), force=True, authority_input=forged_type()
    )
    assert projected["permit_decision"] == "VERIFY"
    assert projected["permit_required"] is None


def test_unauthenticated_manifest_sealing_cannot_create_binary_authority(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")

    required = seal_permit_manifest_projection(_payload(), force=True)
    assert required["permit_decision"] == "VERIFY"
    assert required["permit_required"] is None
    assert required["permits_required"] == []

    not_required_payload = _payload()
    not_required_payload.update({
        "permit_decision": "NOT_REQUIRED",
        "permit_required": False,
        "permit_verdict": "NO",
        "permits_required": [],
    })
    not_required = seal_permit_manifest_projection(not_required_payload, force=True)
    assert not_required["permit_decision"] == "VERIFY"
    assert not_required["permit_required"] is None
    assert not_required["permits_required"] == []


def test_authenticated_private_authority_can_seal_exact_binary_manifest(monkeypatch):
    from api import server
    from api.permit_model import capture_permit_authority_input

    monkeypatch.setenv(MANIFEST_FLAG, "active")
    payload = _payload()
    capability = server._issue_server_owned_legacy_result(payload)
    authority = capture_permit_authority_input(capability)
    assert authority._authenticated_provenance is True

    projected = seal_permit_manifest_projection(
        payload, force=True, authority_input=authority
    )
    assert projected["permit_decision"] == "REQUIRED"
    assert projected["permit_required"] is True
    assert projected["permits_required"]


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
                "source_quote": "A commercial building permit is required for tenant improvement construction.",
                "scope_trigger": "commercial_tenant_improvement",
            },
            {
                "permit_type": "Electrical Permit",
                "filing_family": "electrical",
                "status": "REQUIRED",
                "required": True,
                "source_ref": "cell:w4:anchorage:electrical",
                "source_url": "https://www.muni.org/electrical",
                "source_quote": "An electrical permit is required before new commercial wiring or circuits are installed.",
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


def test_session2_unauthenticated_seal_builds_typed_manifest_and_demotes_binary(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "shadow")
    payload = _payload()
    projected = seal_permit_manifest_projection(payload)
    manifest = projected["permit_manifest"]
    assert projected["permit_decision"] == "VERIFY"
    assert projected["permit_required"] is None
    assert projected["permit_verdict"] == "VERIFY"
    assert projected["primary_permit_family"] == "BUILDING"
    assert manifest["permit_decision"] == "VERIFY"
    assert manifest["primary"]["family"] == "BUILDING"
    assert manifest["primary"]["local_name"] == "Commercial Tenant Improvement Permit"
    assert manifest["jurisdiction"] == projected["jurisdiction_identity"]
    assert manifest["jurisdiction"]["jurisdiction_id"] == "us-ak-anchorage"
    assert manifest["jurisdiction"]["name"] == "Municipality of Anchorage"


def test_session2_typed_nonbinary_companions_preserved_and_unsourced_required_demoted(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    projected = seal_permit_manifest_projection(_payload())
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
    assert electrical["status"] == "VERIFY"
    assert electrical["source_ref"] == "cell:w4:anchorage:electrical"
    assert fire["status"] == "CONDITIONAL"
    assert fire["source_ref"] == "https://www.muni.org/fire-review"
    assert accessibility["status"] == "VERIFY"
    assert accessibility["required"] is None
    assert accessibility["source_ref"] is None
    assert "confirm" in accessibility["customer_guidance"].lower()
    assert zoning["status"] == "CONDITIONAL"
    assert zoning["required"] is None
    assert zoning["source_ref"] is None
    assert not [row for row in companions if row.get("status") == "REQUIRED"]
    assert any(row.get("source_ref") == "cell:w4:anchorage:electrical" for row in companions)
    assert any(row.get("source_ref") == "https://www.muni.org/fire-review" for row in companions)


def test_session2_typed_route_without_claim_snapshot_does_not_create_binary_authority(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    payload = _payload()
    electrical = payload["permits_required"][1]
    electrical.pop("source_ref")
    electrical.pop("source_url")
    electrical.pop("source_quote")
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
    projected = seal_permit_manifest_projection(payload)
    row = next(item for item in projected["permit_manifest"]["companions"] if item["family"] == "ELECTRICAL")
    assert row["status"] == "VERIFY"
    assert row["required"] is None
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
    once = seal_permit_manifest_projection(payload)
    twice = build_permit_manifest_projection(copy.deepcopy(once))
    assert twice == once
    original_rows = (
        len(payload["permits_required"]) - 1
        + len(payload["related_permits"])
        + len(payload["companion_permits"])
    )
    assert len(once["permit_manifest"]["companions"]) <= original_rows


def test_session2_manifest_reentry_ignores_mutable_compatibility_mirrors(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    projected = seal_permit_manifest_projection(_payload())
    projected["permit_decision"] = "NOT_REQUIRED"
    projected["permit_required"] = False
    failed = build_permit_manifest_projection(projected)
    assert failed["permit_decision"] == "VERIFY"
    assert failed["permit_required"] is None
    assert failed["permit_manifest"]["permit_decision"] == "VERIFY"

    stale_row = seal_permit_manifest_projection(_payload())
    electrical_mirror = next(row for row in stale_row["family_decisions"] if row.get("canonical_family") == "ELECTRICAL")
    electrical_mirror["status"] = "NOT_REQUIRED"
    electrical_mirror["source_ref"] = "https://tampered.invalid/source"
    rebuilt_row = build_permit_manifest_projection(stale_row)
    assert rebuilt_row != stale_row
    assert rebuilt_row["permit_manifest"] == stale_row["permit_manifest"]
    assert rebuilt_row["permit_manifest"]["companions"][0]["status"] == "VERIFY"

    tampered_manifest = seal_permit_manifest_projection(_payload())
    tampered_manifest["permit_manifest"]["primary"]["rationale"] = "tampered"
    rebuilt_manifest = build_permit_manifest_projection(tampered_manifest)
    assert "permit_manifest" not in rebuilt_manifest
    assert not is_authenticated_permit_manifest(tampered_manifest.get("permit_manifest"))


def test_session2_preserves_multiple_source_refs_and_rationale(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    payload = _payload()
    row = payload["permits_required"][1]
    row["notes"] = "Panel replacement and feeder work require electrical review."
    row["evidence"] = [
        {"source_url": "https://www.muni.org/electrical-one"},
        {"source_url": "https://www.muni.org/electrical-two"},
    ]
    companion = seal_permit_manifest_projection(payload)["permit_manifest"]["companions"][0]
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
    projected = seal_permit_manifest_projection(wrapper)
    assert projected["other"] == "unchanged"
    assert projected["share"]["data"]["permit_manifest"]["primary"]["family"] == "BUILDING"


def _server(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    from api import server

    return server


def test_session2_server_api_egress_sanitizes_but_only_finalizer_seals(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    server = _server(monkeypatch)
    sanitized = server.project_customer_response_egress(_payload())
    assert "permit_manifest" not in sanitized
    projected = server.finalize_customer_public_projection(
        sanitized, "commercial tenant improvement", "Anchorage", "AK",
    )
    assert projected["permit_manifest"]["primary"]["family"] == "BUILDING"
    assert projected["primary_permit_family"] == "BUILDING"
    assert projected["jurisdiction_identity"]["jurisdiction_id"] == "us-ak-anchorage"
    assert projected["companion_permits"] == projected["permit_manifest"]["companions"]
    assert "permit_manifest" not in projected.get("customer_result_summary", {})
    assert server.project_customer_response_egress(projected) == projected


def test_session2_signature_free_public_manifest_cannot_repair_mutated_mirrors(monkeypatch):
    server = _server(monkeypatch)
    canonical = server.finalize_customer_public_projection(
        _payload(), "commercial tenant improvement", "Anchorage", "AK",
    )
    mutated = copy.deepcopy(canonical)
    mutated["permit_decision"] = "NOT_REQUIRED"
    mutated["permit_required"] = False
    mutated["permit_verdict"] = "NO"
    mutated["permit_name"] = "No permit required"
    mutated["permits_required"] = []

    repaired = server.finalize_customer_public_projection(
        mutated,
        "commercial tenant improvement",
        "Anchorage",
        "AK",
    )
    assert "authority_tag" not in canonical["permit_manifest"]
    assert repaired["permit_decision"] == "VERIFY"
    assert repaired["permit_required"] is None
    assert repaired["permit_verdict"] == "VERIFY"
    assert repaired.get("permits_required") == []


def test_session2_trusted_egress_preserves_needs_input_nonbinary(monkeypatch):
    server = _server(monkeypatch)
    raw = {
        "permit_decision": "NEEDS_INPUT",
        "permit_required": None,
        "permit_verdict": "NEEDS_INPUT",
        "permit_kind": "verification",
        "permit_name": "Additional project details needed",
        "permits_required": [{
            "permit_type": "Building permit verification",
            "filing_family": "building",
            "status": "NEEDS_INPUT",
            "decision": "NEEDS_INPUT",
            "required": None,
            "trigger_condition": "Provide the missing project scope details.",
        }],
    }
    public = server._build_trusted_customer_response_egress(
        raw,
        "project details unavailable",
        "Anchorage",
        "AK",
    )
    assert public["permit_decision"] == "NEEDS_INPUT"
    assert public["permit_required"] is None
    assert public["permit_verdict"] == "NEEDS_INPUT"


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
    projected = server.finalize_customer_public_projection(
        payload, "commercial tenant improvement", "Anchorage", "AK",
    )
    public = server.to_public_share_payload(
        {"data": projected, "job_type": "commercial tenant improvement", "city": "Anchorage", "state": "AK"},
        {},
    )
    assert public["share"]["data"]["permit_manifest"] == projected["permit_manifest"]
    white_label = server.render_white_label_report_html({
        "result": projected,
        "job_type": "commercial tenant improvement",
        "job_category": "commercial",
        "city": "Anchorage",
        "state": "AK",
    })
    assert "Commercial Tenant Improvement Permit" in white_label
    assert "Fire alarm review" in white_label
    assert "VERIFY" in white_label
    assert "CONDITIONAL" in white_label
    assert "Planning/Zoning Use Verification" in white_label


def test_session2_verified_share_render_embeds_same_manifest(monkeypatch):
    monkeypatch.setenv(MANIFEST_FLAG, "active")
    server = _server(monkeypatch)
    projected = server.finalize_customer_public_projection(
        _payload(), "commercial tenant improvement", "Anchorage", "AK",
    )
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
    assert "Fire alarm review" in rendered


def test_schema_literal_cannot_authenticate_or_redirect_manifest_authority(monkeypatch):
    server = _server(monkeypatch)
    fake = {
        "permit_decision": "NEEDS_INPUT",
        "permit_required": None,
        "permit_verdict": "NEEDS_INPUT",
        "permit_name": "Permit verification",
        "permit_kind": "verification",
        "permits_required": [],
        "permit_manifest": {
            "schema_version": "permit_manifest_v1",
            "permit_decision": "REQUIRED",
            "primary": {"family": "ELECTRICAL", "status": "REQUIRED", "local_name": "Electrical"},
            "companions": [],
            "jurisdiction": {},
            "filing_destination": {"apply_url": "https://attacker.invalid/apply"},
        },
    }
    sanitized = server.project_customer_response_egress(fake)
    assert "permit_manifest" not in sanitized
    assert sanitized["permit_decision"] == "NEEDS_INPUT"
    assert sanitized.get("apply_url") != "https://attacker.invalid/apply"

    finalized = server.finalize_customer_public_projection(
        fake, "project details unavailable", "Anchorage", "AK",
    )
    assert finalized["permit_decision"] == "NEEDS_INPUT"
    assert finalized["permit_required"] is None
    assert finalized["permit_manifest"]["primary"]["family"] != "ELECTRICAL"
    assert finalized.get("apply_url") != "https://attacker.invalid/apply"
    assert not is_authenticated_permit_manifest(finalized["permit_manifest"])
    assert "authority_tag" not in finalized["permit_manifest"]


def test_decision_contract_exception_cannot_promote_terminal_nonbinary(monkeypatch):
    server = _server(monkeypatch)
    original_apply = server.apply_permit_decision_contract
    calls = {"count": 0}

    def fail_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("forced once")
        return original_apply(*args, **kwargs)

    monkeypatch.setattr(server, "apply_permit_decision_contract", fail_once)
    monkeypatch.setattr(server, "resolve_customer_decision", lambda *_args, **_kwargs: {
        "permit_decision": "REQUIRED", "permit_required": True,
        "permit_verdict": "YES", "permit_name": "Forged fallback",
        "permits_required": [{"filing_family": "electrical", "status": "REQUIRED", "required": True}],
    })
    raw = {
        "permit_decision": "NEEDS_INPUT", "permit_required": None,
        "permit_verdict": "NEEDS_INPUT", "permit_name": "Scope verification",
        "permits_required": [{
            "filing_family": "building", "permit_type": "Building verification",
            "status": "NEEDS_INPUT", "required": None,
            "trigger_condition": "Provide missing project scope details.",
        }],
    }
    public = server._build_trusted_customer_response_egress(
        raw, "project details unavailable", "Anchorage", "AK",
    )
    assert public["permit_decision"] == "NEEDS_INPUT"
    assert public["permit_required"] is None
    assert public["permit_verdict"] == "NEEDS_INPUT"
    assert public["permits_required"] == []


def test_session2_shipped_frontend_assets_enforce_v2_five_status_contract():
    index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    report = (ROOT / "frontend" / "report.html").read_text(encoding="utf-8")

    for status in ("REQUIRED", "NOT_REQUIRED", "CONDITIONAL", "VERIFY", "NEEDS_INPUT"):
        assert status in index
        assert status in report
    assert "canonicalCustomerStatus" in index
    assert "permit_manifest_v1" in report
    assert "⚠️ MAYBE" not in index


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
    remediation = json.loads(
        (
            ROOT
            / "benchmarks"
            / "permit_accuracy_v1_2"
            / "REMEDIATION_PROTECTED_HASH_SUCCESSOR_20260731.json"
        ).read_text()
    )
    gate_successor = remediation["benchmark_gate_successor"]
    assert gate_successor["opening_sha256"] == "0435199886e13c80739ff106ea2a52a815c042bd2f7058f170f5f40d8d106c09"
    assert hashlib.sha256(frozen_test.read_bytes()).hexdigest() == gate_successor["candidate_sha256"]
