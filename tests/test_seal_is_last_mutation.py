from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import server
from public_packet import _seal_hash, apply_public_packet_projection, apply_render_parity_seal
from scope_contract import build_scope_facts_v4


def _required_payload() -> dict:
    return {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "segment": "commercial",
        "permit_kind": "Sign",
        "permit_name": "Sign Permit",
        "permit_type": "Sign Permit",
        "permits_required": [
            {
                "permit_type": "Sign Permit",
                "permit_name": "Sign Permit",
                "family": "sign",
                "filing_family": "sign",
                "kind": "Sign",
                "status": "REQUIRED",
                "decision": "REQUIRED",
                "required": True,
                "rationale": "Storefront sign installation requires sign permit review.",
            }
        ],
        "related_permits": [],
        "customer_headline": "Permit required: Sign Permit.",
        "customer_next_step": "File the required sign permit with the permit office.",
        "applying_office": "Phoenix Planning & Development",
        "building_dept_name": "Phoenix Planning & Development",
        "apply_url": "https://www.phoenix.gov/pdd",
        "source_urls": ["https://www.phoenix.gov/pdd"],
        "sources": [{"url": "https://www.phoenix.gov/pdd", "title": "Phoenix Planning & Development", "source_type": "official_local"}],
        "watch_out": "A sign permit is not required unless a new sign is installed.",
    }


def _sealed_payload() -> dict:
    facts = build_scope_facts_v4("install new storefront sign", "Phoenix", "AZ", job_category="commercial", scope_contract={"category": "commercial"})
    projected = apply_public_packet_projection(copy.deepcopy(_required_payload()), facts)
    sealed = apply_render_parity_seal(projected, facts=facts, fail_hard=True)
    assert sealed.get("public_packet")
    return sealed


def test_recovery_guard_preserves_public_packet_and_seal_until_reprojection() -> None:
    sealed = _sealed_payload()
    guarded = server._live100_core_truth_recovery_guard(sealed, "install new storefront sign", "Phoenix", "AZ")
    assert guarded.get("public_packet"), json.dumps(guarded, sort_keys=True, default=str)[:1000]
    assert guarded.get("public_packet_rows")


def test_no_truth_inverting_rewrites_in_recovery_guard() -> None:
    source = (API / "server.py").read_text(encoding="utf-8")
    assert "_strip_required_no_permit_copy" not in source
    assert 're.sub(r"\\bnot\\s+required\\b", "required"' not in source


def test_seal_hash_matches_public_packet_after_terminal_seal() -> None:
    sealed = _sealed_payload()
    guarded = server._live100_core_truth_recovery_guard(sealed, "install new storefront sign", "Phoenix", "AZ")
    packet = guarded.get("public_packet")
    assert isinstance(packet, dict) and packet
    assert packet.get("sealed_public_packet_hash") == _seal_hash(packet)
    assert guarded.get("sealed_public_packet_hash") == packet.get("sealed_public_packet_hash")
