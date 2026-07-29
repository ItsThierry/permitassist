from __future__ import annotations

from api import permit_rule_engine as pre


def test_sealed_family_rows_carry_only_their_own_publishable_provenance() -> None:
    building = pre.normalize_family_decision(
        {
            "family": "building",
            "verdict": "REQUIRED",
            "trigger": "commercial alteration",
            "provenance": [{
                "source_url": "https://example.gov/building-rule",
                "source_quote": "A building permit is required for an alteration.",
                "snapshot_hash": "a" * 64,
                "snapshot_path": "synthetic://building",
                "publishable": True,
            }],
        }
    )
    electrical = pre.normalize_family_decision(
        {
            "family": "electrical",
            "verdict": "VERIFY",
            "trigger": "electrical applicability is not closed",
            "provenance": [],
        }
    )
    payload = pre.build_sealed_projection_payload(
        jurisdiction_id="us-ex-exampleville",
        jurisdiction_name="Exampleville",
        state="EX",
        project_family="commercial_tenant_improvement",
        main_decision=building,
        family_decisions=(building, electrical),
        family_routes=(),
        coverage_status="validated_exact_partial",
        coverage_reason="building is sourced; electrical remains unresolved",
        source_cell_id="synthetic-family-provenance",
        seed_classification=pre.SeedClassification.EXACT_PARTIAL,
    )

    building_family, electrical_family = payload["family_decisions"]
    building_permit, electrical_permit = payload["permits_required"]
    assert building_family["source_ref"] == "https://example.gov/building-rule"
    assert building_family["source_refs"] == ["https://example.gov/building-rule"]
    assert building_permit["source_ref"] == "https://example.gov/building-rule"
    assert building_permit["source_refs"] == ["https://example.gov/building-rule"]
    assert electrical_family["source_ref"] is None
    assert electrical_family["source_refs"] == []
    assert electrical_permit["source_ref"] is None
    assert electrical_permit["source_refs"] == []


def test_shared_office_does_not_alias_building_route_to_electrical_family() -> None:
    provenance = {
        "source_url": "https://example.gov/building-permit-application",
        "source_quote": "Apply online for a Building Permit Application.",
        "snapshot_hash": "b" * 64,
        "snapshot_path": "synthetic://building-route",
        "publishable": True,
    }
    cell = {
        "project_family": "commercial_tenant_improvement",
        "tier1": {
            "trade_authority": [
                {
                    "trade": "building",
                    "issuing_authority": {"name": "Exampleville Permit Office", "tier": "local"},
                    "application_authority": {"name": "Exampleville Permit Office"},
                    "handled_by_local_ahj": True,
                },
                {
                    "trade": "electrical",
                    "issuing_authority": {"name": "Exampleville Permit Office", "tier": "local"},
                    "application_authority": {"name": "Exampleville Permit Office"},
                    "handled_by_local_ahj": True,
                },
            ],
            "apply": [
                {
                    "permit_name": "Building Permit",
                    "office_name": "Exampleville Permit Office",
                    "apply_url": "https://example.gov/building-permit-application",
                    "channel": "online_portal",
                    "provenance": provenance,
                }
            ],
        },
    }

    routes = {route.family: route for route in pre.build_family_authority_routes(cell)}
    assert routes["building"].application_route.apply_url == provenance["source_url"]
    assert routes["electrical"].application_route.apply_url == ""
    assert routes["electrical"].application_route.channel == "verify"

    decisions = tuple(
        pre.normalize_family_decision(
            {
                "family": family,
                "verdict": "REQUIRED",
                "trigger": f"{family} work",
                "provenance": provenance,
            }
        )
        for family in ("building", "electrical")
    )
    assert pre._families_missing_actionable_routes(decisions, routes.values()) == ("electrical",)


def test_shared_office_does_not_alias_gas_route_to_mechanical_family() -> None:
    provenance = {
        "source_url": "https://example.gov/portal/apply/gas",
        "source_quote": "Apply online for a Gas Permit Application.",
        "snapshot_hash": "c" * 64,
        "snapshot_path": "synthetic://gas-route",
        "publishable": True,
    }
    cell = {
        "tier1": {
            "trade_authority": [
                {
                    "trade": family,
                    "issuing_authority": {"name": "Exampleville Trade Office", "tier": "local"},
                    "application_authority": {"name": "Exampleville Trade Office"},
                }
                for family in ("gas", "mechanical")
            ],
            "apply": [{
                "permit_name": "Gas Permit",
                "office_name": "Exampleville Trade Office",
                "apply_url": provenance["source_url"],
                "channel": "online_portal",
                "provenance": provenance,
            }],
        }
    }

    routes = {route.family: route for route in pre.build_family_authority_routes(cell)}
    assert routes["gas"].application_route.apply_url == provenance["source_url"]
    assert routes["mechanical"].application_route.apply_url == ""
    assert routes["mechanical"].application_route.channel == "verify"


def test_family_route_matching_uses_tokens_not_substrings() -> None:
    provenance = {
        "source_url": "https://example.gov/portal/apply/design-review",
        "source_quote": "Apply online for a Design Review Permit.",
        "snapshot_hash": "d" * 64,
        "snapshot_path": "synthetic://design-review-route",
        "publishable": True,
    }
    cell = {
        "tier1": {
            "trade_authority": [{
                "trade": "sign",
                "issuing_authority": {"name": "Exampleville Planning Office", "tier": "local"},
                "application_authority": {"name": "Exampleville Planning Office"},
            }],
            "apply": [{
                "permit_name": "Design Review Permit",
                "office_name": "Exampleville Planning Office",
                "apply_url": provenance["source_url"],
                "channel": "online_portal",
                "provenance": provenance,
            }],
        }
    }

    routes = {route.family: route for route in pre.build_family_authority_routes(cell)}
    assert routes["sign"].application_route.apply_url == ""
    assert routes["sign"].application_route.channel == "verify"
