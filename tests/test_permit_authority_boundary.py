from __future__ import annotations

from typing import Any, cast

import pytest

from api.permit_model import (
    PermitAuthorityInput,
    PermitStatus,
    build_permit_package,
    capture_permit_authority_input,
)


def test_authority_capability_constructor_is_closed_and_capture_is_immutable():
    with pytest.raises(TypeError, match="only be created"):
        PermitAuthorityInput({"permit_decision": "REQUIRED"})

    raw = {
        "permit_decision": "NEEDS_INPUT",
        "permit_required": None,
        "permits_required": [{
            "filing_family": "building", "permit_type": "Building verification",
            "status": "NEEDS_INPUT", "required": None,
        }],
    }
    authority = capture_permit_authority_input(raw)
    raw["permit_decision"] = "REQUIRED"
    raw["permit_required"] = True
    raw["permits_required"][0]["status"] = "REQUIRED"
    _, package = build_permit_package(authority, "scope details unavailable", "Example", "TX")
    assert package.decision == "NEEDS_INPUT"
    assert package.required_items == ()


def test_package_builder_rejects_untyped_public_dto():
    with pytest.raises(TypeError, match="PermitAuthorityInput"):
        build_permit_package(
            cast(Any, {"permit_decision": "REQUIRED", "permits_required": [{"permit_type": "Building", "required": True}]}),
            "interior paint",
            "Example",
            "TX",
        )


def test_unquoted_official_url_cannot_support_hard_required_authority():
    raw = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permits_required": [{
            "permit_type": "Building Permit", "filing_family": "building",
            "required": True, "status": "REQUIRED", "source_url": "https://example.gov/permits",
        }],
        "claim_citations": [{
            "field": "permit_type", "value": "Building Permit",
            "source_url": "https://example.gov/permits",
            "confidence": "source attached; quoted snippet unavailable",
        }],
        "sources": [{"url": "https://example.gov/permits", "title": "Official permits"}],
    }
    _, package = build_permit_package(
        capture_permit_authority_input(raw), "construct a detached room addition", "Example", "TX",
    )
    assert package.decision == "VERIFY"
    assert package.required_items == ()
    assert any(item.family.value == "building" and item.status == PermitStatus.VERIFY for item in package.related_items)


def test_caller_supplied_exact_quote_still_cannot_authenticate_required_family():
    quote = "A building permit is required before constructing a detached room addition."
    raw = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permits_required": [{
            "permit_type": "Building Permit", "filing_family": "building",
            "required": True, "status": "REQUIRED", "source_url": "https://example.gov/additions",
            "source_quote": quote,
        }],
        "claim_citations": [{
            "field": "permit_type", "value": "Building Permit", "quoted_snippet": quote,
            "source_url": "https://example.gov/additions",
        }],
        "sources": [{"url": "https://example.gov/additions", "title": "Official additions"}],
    }
    _, package = build_permit_package(
        capture_permit_authority_input(raw), "construct a detached room addition", "Example", "TX",
    )
    assert package.decision == "VERIFY"
    assert package.required_items == ()
    assert any(
        item.family.value == "building" and item.status == PermitStatus.VERIFY
        for item in package.related_items
    )


def test_required_compatibility_row_cannot_promote_nonbinary_authority():
    raw = {
        "permit_decision": "VERIFY",
        "permit_required": None,
        "permits_required": [
            {
                "permit_type": "Building Permit",
                "filing_family": "building",
                "required": True,
                "status": "REQUIRED",
                "source_url": "https://example.gov/permits",
            }
        ],
        "sources": [{"url": "https://example.gov/permits", "title": "Official permits"}],
    }
    _, package = build_permit_package(
        capture_permit_authority_input(raw),
        "verify possible interior alterations",
        "Example",
        "TX",
    )
    assert package.decision == "VERIFY"
    assert package.required is False
    assert package.required_items == ()
    assert package.related_items
    assert all(item.status == PermitStatus.VERIFY for item in package.related_items)
