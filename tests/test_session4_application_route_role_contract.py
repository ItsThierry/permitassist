from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "api"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from api import permit_rule_engine as pre  # noqa: E402


def _provenance(url: str, quote: str) -> dict:
    return {
        "source_url": url,
        "source_quote": quote,
        "snapshot_hash": "a" * 64,
        "snapshot_path": "synthetic://route",
        "publishable": True,
    }


def _cell(*, family: str, permit_name: str, apply_url: str, quote: str) -> dict:
    return {
        "project_family": "commercial_tenant_improvement",
        "tier1": {
            "trade_authority": [
                {
                    "trade": family,
                    "issuing_authority": {"name": "Example Building Department", "tier": "city"},
                    "application_authority": {"name": "Example Building Department"},
                    "handled_by_local_ahj": True,
                }
            ],
            "apply": [
                {
                    "permit_name": permit_name,
                    "office_name": "Example Building Department",
                    "apply_url": apply_url,
                    "channel": "online",
                    "provenance": _provenance(apply_url, quote),
                }
            ],
        },
    }


def test_statute_or_code_source_is_not_customer_application_route() -> None:
    routes = pre.build_family_authority_routes(
        _cell(
            family="building",
            permit_name="Commercial Building Permit",
            apply_url="https://legislature.example.gov/statutes/section/24/117/04449",
            quote="Any application for an approval or permit must meet this section.",
        )
    )

    assert len(routes) == 1
    assert routes[0].application_route.apply_url == ""
    assert routes[0].application_route.channel == "verify"
    assert "application_route_role_not_proven" in routes[0].application_route.validation_issue_codes


def test_trade_specific_form_cannot_serve_different_family() -> None:
    routes = pre.build_family_authority_routes(
        _cell(
            family="building",
            permit_name="Commercial Building Permit",
            apply_url="https://example.gov/forms/electrical-permit-application.pdf",
            quote="APPLICATION FOR ELECTRICAL INSTALLATION PERMIT. Submit this form to Electrical Inspections.",
        )
    )

    assert routes[0].application_route.apply_url == ""
    assert "application_route_family_mismatch" in routes[0].application_route.validation_issue_codes


def test_forbidden_search_or_maps_host_is_never_an_apply_route() -> None:
    routes = pre.build_family_authority_routes(
        _cell(
            family="building",
            permit_name="Commercial Building Permit",
            apply_url="https://www.google.com/maps/search/example+permit+office",
            quote="Apply for a building permit through this link.",
        )
    )

    assert routes[0].application_route.apply_url == ""
    assert "application_route_host_forbidden" in routes[0].application_route.validation_issue_codes


def test_official_portal_with_action_evidence_remains_actionable() -> None:
    routes = pre.build_family_authority_routes(
        _cell(
            family="building",
            permit_name="Commercial Building Permit",
            apply_url="https://permits.example.gov/portal/apply/building",
            quote="Apply online for a commercial building permit through the permit portal.",
        )
    )

    assert routes[0].application_route.apply_url == "https://permits.example.gov/portal/apply/building"
    assert routes[0].application_route.channel == "online"
    assert routes[0].application_route.validation_issue_codes == ()


def test_primary_never_falls_back_to_another_family_route() -> None:
    building = pre.normalize_family_decision(
        {
            "family": "building",
            "verdict": "REQUIRED",
            "trigger": "commercial alteration",
            "provenance": [_provenance("https://example.gov/building-rule", "A permit is required for alterations.")],
        }
    )
    electrical = pre.normalize_family_decision(
        {
            "family": "electrical",
            "verdict": "REQUIRED",
            "trigger": "new branch circuit",
            "provenance": [_provenance("https://example.gov/electrical-rule", "An electrical permit is required for new circuits.")],
        }
    )
    authority = pre.AuthorityRef(
        family="electrical",
        issuing_authority="Example Electrical Inspections",
        application_authority="Example Electrical Inspections",
        authority_tier="city",
        handled_by_local_ahj=True,
    )
    route = pre.ApplicationRoute(
        permit_name="Electrical Permit",
        office_name="Example Electrical Inspections",
        apply_url="https://permits.example.gov/portal/apply/electrical",
        channel="online",
        provenance=tuple(pre._provenance_records(_provenance(
            "https://permits.example.gov/portal/apply/electrical",
            "Apply online for an electrical permit.",
        ))),
    )
    payload = pre.build_sealed_projection_payload(
        jurisdiction_id="us-ex-example",
        jurisdiction_name="Example",
        state="EX",
        project_family="commercial_tenant_improvement",
        main_decision=building,
        family_decisions=(building, electrical),
        family_routes=(pre.FamilyAuthorityRoute("electrical", authority, route),),
        coverage_status="validated_exact_partial",
        coverage_reason="synthetic route isolation",
        source_cell_id="synthetic-route-isolation",
        seed_classification=pre.SeedClassification.EXACT_PARTIAL,
    )

    assert payload["apply_url"] == ""
    assert payload["applying_office"] == ""
    electrical_row = next(row for row in payload["family_decisions"] if row["family"] == "electrical")
    assert electrical_row["apply_url"] == "https://permits.example.gov/portal/apply/electrical"
