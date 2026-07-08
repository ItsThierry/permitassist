from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from customer_boundary_validator import canonical_payload_diffs, validate_customer_boundary  # noqa: E402


def _required_public() -> dict:
    row = {
        "permit_name": "Building Permit",
        "permit_type": "Building Permit",
        "family": "building",
        "decision": "REQUIRED",
        "required": True,
    }
    packet = {
        "schema_version": "final_public_permit_packet.v1",
        "decision": "REQUIRED",
        "required_families": ["building"],
        "rows": [row],
        "authority": {"name": "Test Building Department", "apply_url": "", "source_urls": []},
        "fees": [],
    }
    return {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permits_required": [row],
        "public_packet": packet,
        "required_permit_families": ["building"],
        "required_permit_names": ["Building Permit"],
        "fee_range": None,
        "fee_estimate": None,
        "fee_notes": "none",
        "source_urls": [],
    }


def test_fee_validator_ignores_json_null_none_empty_machine_values() -> None:
    public = _required_public()
    findings = validate_customer_boundary(public, visible_text="Permit required. Fee: verify with the permit office.")
    assert "fee_or_report_dump_corruption" not in {f.code for f in findings}


def test_fee_validator_flags_actual_customer_copy_template_or_duplicate_dump() -> None:
    public = _required_public()
    public["fee_range"] = "Fee Estimate: Fee Estimate: {{fee_total}}"
    findings = validate_customer_boundary(public, visible_text="Permit required.")
    assert "fee_or_report_dump_corruption" in {f.code for f in findings}


def test_share_payload_contract_uses_packet_and_required_rows_not_legacy_mirrors() -> None:
    public = _required_public()
    payload = {
        "permit_decision": public["permit_decision"],
        "permit_required": public["permit_required"],
        "permit_verdict": public["permit_verdict"],
        "permits_required": public["permits_required"],
        "public_packet": public["public_packet"],
    }
    assert canonical_payload_diffs(public, payload) == []


def test_share_payload_contract_rejects_legacy_required_family_mirrors() -> None:
    public = _required_public()
    payload = {
        "permit_decision": public["permit_decision"],
        "permit_required": public["permit_required"],
        "permit_verdict": public["permit_verdict"],
        "permits_required": public["permits_required"],
        "public_packet": public["public_packet"],
        "required_permit_families": ["stale"],
    }
    findings = canonical_payload_diffs(public, payload)
    assert [(f.code, f.detail) for f in findings] == [("legacy_render_mirror_present", "required_permit_families")]
