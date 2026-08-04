import copy
import json
from pathlib import Path

import pytest

from launch_coverage import (
    SupportOutcome,
    build_supported_customer_projection,
    load_coverage_registry,
    resolve_precharge_support,
)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "api" / "launch_coverage_registry.json"


def _live100_cases():
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return payload["contracts"]


def test_registry_is_sealed_and_has_frozen_live100_plus_blind_contracts():
    registry = load_coverage_registry(REGISTRY)
    assert registry.contract_count == 110
    assert registry.verify_integrity()
    assert {c["contract_id"] for c in registry.contracts[-10:]} == {
        *(f"A-{index:03d}" for index in range(1, 6)),
        *(f"B-{index:03d}" for index in range(1, 6)),
    }
    assert {c["decision"] for c in registry.contracts} == {"REQUIRED", "NOT_REQUIRED", "CONDITIONAL"}
    assert sum(c["decision"] == "REQUIRED" for c in registry.contracts) == 95
    assert sum(c["decision"] == "NOT_REQUIRED" for c in registry.contracts) == 13
    assert sum(c["decision"] == "CONDITIONAL" for c in registry.contracts) == 2


@pytest.mark.parametrize("contract", _live100_cases(), ids=lambda c: c["contract_id"])
def test_frozen_supported_contracts_resolve_before_charge_and_never_verify(contract):
    result = resolve_precharge_support(
        job_type=contract["job_type"],
        city=contract["city"],
        state=contract["state"],
        zip_code=contract.get("zip_code", ""),
        segment=contract["segment"],
        registry_path=REGISTRY,
    )
    assert result.outcome is SupportOutcome.SUPPORTED
    assert result.contract is not None
    public = build_supported_customer_projection(result.contract)
    assert public["permit_decision"] == contract["decision"]
    assert public["permit_decision"] != "VERIFY"
    assert public["coverage_outcome"] == "SUPPORTED"
    assert public["permit_manifest"]["contract_sha256"] == contract["contract_sha256"]
    assert public["sources"]
    assert all(source.get("url", "").startswith("https://") for source in public["sources"])
    assert public["applying_office"]
    assert public["apply_path"]["maps_url"].startswith("https://www.google.com/maps/search/")


def test_unknown_scope_is_blocked_before_model_report_or_charge():
    result = resolve_precharge_support(
        job_type="Build an orbital launch tower with unspecified industrial processes",
        city="Exampleville",
        state="ZZ",
        zip_code="",
        segment="commercial",
        registry_path=REGISTRY,
    )
    assert result.outcome is SupportOutcome.UNSUPPORTED
    assert result.contract is None
    assert result.customer_report is None
    assert result.retained_charge is False
    assert result.model_call_allowed is False


def test_missing_material_fact_mapping_or_omission_is_needs_fact(tmp_path):
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    contract = payload["contracts"][0]
    contract["required_facts"] = ["controlling_condition"]
    contract.pop("contract_sha256", None)
    canonical_contract = json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    import hashlib
    contract["contract_sha256"] = hashlib.sha256(canonical_contract.encode("utf-8")).hexdigest()
    payload.pop("registry_sha256", None)
    canonical_registry = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload["registry_sha256"] = hashlib.sha256(canonical_registry.encode("utf-8")).hexdigest()
    required_registry = tmp_path / "required-fact-registry.json"
    required_registry.write_text(json.dumps(payload), encoding="utf-8")
    for supplied_facts in ({}, None):
        result = resolve_precharge_support(
            job_type=contract["job_type"],
            city=contract["city"],
            state=contract["state"],
            zip_code=contract.get("zip_code", ""),
            segment=contract["segment"],
            supplied_facts=supplied_facts,
            registry_path=required_registry,
        )
        assert result.outcome is SupportOutcome.NEEDS_FACT
        assert result.missing_facts == ("controlling_condition",)
        assert result.customer_report is None
        assert result.retained_charge is False
        assert result.model_call_allowed is False


def test_projection_is_immutable_and_idempotent():
    contract = _live100_cases()[1]
    original = copy.deepcopy(contract)
    first = build_supported_customer_projection(contract)
    second = build_supported_customer_projection(contract)
    assert first == second
    assert contract == original
    first["permit_decision"] = "VERIFY"
    third = build_supported_customer_projection(contract)
    assert third["permit_decision"] == contract["decision"]


def test_material_claims_have_claim_bound_official_evidence():
    for contract in _live100_cases():
        projection = build_supported_customer_projection(contract)
        citations = projection["claim_citations"]
        assert citations
        assert any(c["field"] == "permit_decision" and c["value"] == contract["decision"] for c in citations)
        for row in projection["permits_required"]:
            family = row["permit_family"]
            assert any(c.get("permit_family") == family for c in citations)


def test_no_internal_architecture_language_or_private_keys_in_projection():
    banned = ("decision cell", "scope_signal_only", "server-held", "resolver", "provenance")
    for contract in _live100_cases():
        projection = build_supported_customer_projection(contract)
        blob = json.dumps(projection, sort_keys=True).lower()
        assert not any(token in blob for token in banned)
        assert not any(str(key).startswith("_") for key in projection)
