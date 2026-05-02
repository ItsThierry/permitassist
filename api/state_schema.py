"""Phase 3 state schema framework for PermitAssist.

This module is deliberately a schema/design layer, not a populated rule engine.
Phase 3 gives the engine a stable place to attach state-specific healthcare,
accessibility, energy, and local-amendment overlays with citation hooks from day
one, while avoiding fake state-rule claims before Phase 4 population.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

PHASE3_TARGET_STATES = ("CA", "TX", "FL", "MA")

_STATE_NAMES = {
    "CA": "California",
    "TX": "Texas",
    "FL": "Florida",
    "MA": "Massachusetts",
}

_HEALTHCARE_OVERLAY_TEMPLATES: dict[str, dict[str, Any]] = {
    "occupancy_classification": {
        "label": "B vs I-2 / ambulatory-care occupancy basis",
        "question": "Does the adopted state/local code keep this outpatient clinic in Business Group B or require I-2 / ambulatory-care review?",
        "citation_topics": ["adopted building code occupancy chapter", "Group B outpatient clinic basis", "I-2 / ambulatory-care facility provisions"],
    },
    "ambulatory_care_thresholds": {
        "label": "Ambulatory-care threshold / IBC 422 applicability",
        "question": "Do procedure, anesthesia/sedation, recovery/PACU, or self-preservation conditions trigger ambulatory-care provisions?",
        "citation_topics": ["IBC 422 or state equivalent", "patient self-preservation threshold", "procedure/anesthesia/recovery threshold"],
    },
    "healthcare_licensing": {
        "label": "Health-care licensing / state health review",
        "question": "Is a separate state health, clinic, ASC, dental, pharmacy, lab, or licensing review required before opening?",
        "citation_topics": ["state health licensing agency", "ASC/clinic licensing rule", "certificate/opening approval path"],
    },
    "medical_gas": {
        "label": "Medical gas / vacuum / nitrous verification",
        "question": "Does the scope include oxygen, nitrous, vacuum, alarms, zone valves, or verifier documentation?",
        "citation_topics": ["medical gas code adoption", "verification/testing standard", "state/local permit or inspection path"],
    },
    "radiology_xray": {
        "label": "Radiology / x-ray shielding and registration",
        "question": "Does radiation-producing equipment require shielding plans, state registration, or equipment approval?",
        "citation_topics": ["state radiation control agency", "shielding plan requirement", "x-ray registration/inspection path"],
    },
    "infection_control_hvac": {
        "label": "Infection-control HVAC / ventilation assumptions",
        "question": "Do procedure, sterilization, lab, or treatment rooms require special exhaust, pressure, filtration, or air-balance verification?",
        "citation_topics": ["mechanical/health ventilation standard", "sterilization/procedure room requirement", "TAB/commissioning expectation"],
    },
    "accessibility": {
        "label": "Accessibility / path-of-travel overlay",
        "question": "Which federal/state accessibility rules and path-of-travel obligations apply to the clinic TI?",
        "citation_topics": ["state accessibility standard", "ADA/accessible route", "path-of-travel alteration trigger"],
    },
}

_GENERAL_OVERLAY_TEMPLATES: dict[str, dict[str, Any]] = {
    "adopted_code_editions": {
        "label": "Adopted code editions",
        "question": "Which building, existing-building, fire, mechanical, plumbing, electrical, energy, and accessibility editions are adopted for this jurisdiction?",
        "citation_topics": ["state code adoption page", "local amendment ordinance", "effective date"],
    },
    "energy_code": {
        "label": "Energy-code overlay",
        "question": "Which commercial/residential energy-code edition, forms, commissioning, or envelope/HVAC documentation applies?",
        "citation_topics": ["state energy code", "local stretch/reach code", "commercial compliance form"],
    },
    "accessibility": {
        "label": "Accessibility / path-of-travel overlay",
        "question": "Which state accessibility, ADA, path-of-travel, entrance, restroom, parking, counter, and signage obligations apply to the TI?",
        "citation_topics": ["state accessibility standard", "ADA/accessible route", "path-of-travel alteration trigger"],
    },
    "local_amendments": {
        "label": "Local amendments / AHJ overlays",
        "question": "Does the city/county amend permit names, fees, inspections, fire review, accessibility, or submittal requirements?",
        "citation_topics": ["local municipal code", "building department bulletin", "permit application/checklist"],
    },
}


def _citation_hooks(topics: list[str]) -> list[dict[str, str]]:
    return [
        {
            "topic": topic,
            "source_url": "",
            "source_title": "",
            "citation_status": "needs_population",
            "verified_on": "",
        }
        for topic in topics
    ]


def _overlay_slot(template: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": template["label"],
        "question": template["question"],
        "status": "needs_population",
        "citation_hooks": _citation_hooks(list(template["citation_topics"])),
        "rule_summary": "",
        "contractor_guidance": [],
        "risk_flags": [],
    }


def _build_schema(state: str) -> dict[str, Any]:
    state_upper = (state or "").strip().upper()
    state_name = _STATE_NAMES[state_upper]
    return {
        "state": state_upper,
        "state_name": state_name,
        "phase": 3,
        "coverage_level": "schema_only",
        "population_status": "not_populated",
        "requires_population_before_state_specific_claims": True,
        "target_verticals": ["restaurant_ti", "medical_clinic_ti", "office_ti"],
        "citation_policy": {
            "no_fake_citations": True,
            "source_url_required_for_populated_rules": True,
            "snippet_or_title_required_for_customer_claims": True,
            "population_phase": 4,
        },
        "healthcare_overlays": {
            key: _overlay_slot(template)
            for key, template in _HEALTHCARE_OVERLAY_TEMPLATES.items()
        },
        "general_overlays": {
            key: _overlay_slot(template)
            for key, template in _GENERAL_OVERLAY_TEMPLATES.items()
        },
        "contractor_warning": (
            f"{state_name} state overlay schema is ready, but rules are not populated yet. "
            "Use this as a checklist only; verify with AHJ and cited state/local sources before quoting."
        ),
    }


STATE_RULE_SCHEMAS: dict[str, dict[str, Any]] = {
    state: _build_schema(state)
    for state in PHASE3_TARGET_STATES
}


def get_state_rule_schema(state: str) -> dict[str, Any] | None:
    """Return a copy of the Phase 3 schema for a supported target state."""
    state_upper = (state or "").strip().upper()
    schema = STATE_RULE_SCHEMAS.get(state_upper)
    return deepcopy(schema) if schema else None


def validate_state_rule_schema(schema: dict[str, Any] | None) -> list[str]:
    """Validate shape and safety constraints for a Phase 3 state schema."""
    errors: list[str] = []
    if not isinstance(schema, dict):
        return ["schema must be a dict"]

    state = schema.get("state")
    if state not in PHASE3_TARGET_STATES:
        errors.append("state must be one of Phase 3 target states")
    if schema.get("phase") != 3:
        errors.append("phase must be 3")
    if schema.get("coverage_level") != "schema_only":
        errors.append("coverage_level must remain schema_only until Phase 4 population")
    if schema.get("population_status") != "not_populated":
        errors.append("population_status must remain not_populated in Phase 3")
    policy = schema.get("citation_policy")
    if not isinstance(policy, dict):
        errors.append("citation_policy must be a dict")
        policy = {}
    if policy.get("no_fake_citations") is not True:
        errors.append("citation_policy.no_fake_citations must be true")
    if policy.get("source_url_required_for_populated_rules") is not True:
        errors.append("populated rules must require source URLs")

    for group_name in ("healthcare_overlays", "general_overlays"):
        group = schema.get(group_name)
        if not isinstance(group, dict) or not group:
            errors.append(f"{group_name} must be a non-empty dict")
            continue
        for key, slot in group.items():
            if not isinstance(slot, dict):
                errors.append(f"{group_name}.{key} must be a dict")
                continue
            if slot.get("status") != "needs_population":
                errors.append(f"{group_name}.{key}.status must be needs_population")
            hooks = slot.get("citation_hooks")
            if not isinstance(hooks, list) or not hooks:
                errors.append(f"{group_name}.{key}.citation_hooks must be a non-empty list")
                continue
            for idx, hook in enumerate(hooks):
                if not isinstance(hook, dict):
                    errors.append(f"{group_name}.{key}.citation_hooks[{idx}] must be a dict")
                    continue
                if not hook.get("topic"):
                    errors.append(f"{group_name}.{key}.citation_hooks[{idx}].topic is required")
                if hook.get("citation_status") != "needs_population":
                    errors.append(f"{group_name}.{key}.citation_hooks[{idx}].citation_status must be needs_population")
                if hook.get("source_url"):
                    errors.append(f"{group_name}.{key}.citation_hooks[{idx}].source_url must stay blank until populated")
    return errors


def compact_state_schema_context(state: str, vertical: str) -> dict[str, Any] | None:
    """Small customer-safe schema context for attaching to results.

    This intentionally carries overlay names/questions and blank citation hooks,
    not state-specific legal conclusions. Phase 4 will populate actual rules.
    """
    schema = get_state_rule_schema(state)
    if not schema:
        return None

    if vertical == "medical_clinic_ti":
        overlays = schema["healthcare_overlays"]
    elif vertical in {"restaurant_ti", "office_ti"}:
        overlays = schema["general_overlays"]
    else:
        return None
    return {
        "state": schema["state"],
        "state_name": schema["state_name"],
        "phase": schema["phase"],
        "coverage_level": schema["coverage_level"],
        "population_status": schema["population_status"],
        "requires_population_before_state_specific_claims": schema["requires_population_before_state_specific_claims"],
        "vertical": vertical,
        "overlay_slots": [
            {
                "key": key,
                "label": slot["label"],
                "status": slot["status"],
                "citation_topics": [hook["topic"] for hook in slot["citation_hooks"]],
            }
            for key, slot in overlays.items()
        ],
        "contractor_warning": schema["contractor_warning"],
    }
