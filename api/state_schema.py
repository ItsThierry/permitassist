"""State rule schema framework and Phase 4 populated overlays for PermitAssist.

Phase 3 created citation-ready CA/TX/FL/MA state overlay slots without fake
state-rule claims. Phase 4 populates those slots one careful state/vertical at a
time. The first populated slice is Phase 4A: Texas medical/dental clinic tenant
improvement.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

PHASE3_TARGET_STATES = ("CA", "TX", "FL", "MA")

PHASE4A_TX_VERIFIED_ON = "2026-05-02"

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

_TX_MEDICAL_CLINIC_RULES: list[dict[str, Any]] = [
    {
        "id": "tx_municipal_ibc_local_ahj",
        "overlay": "occupancy_classification",
        "title": "Texas municipal commercial building code baseline and local AHJ amendments",
        "applies": "all_tx_medical_clinic_ti",
        "summary": "Texas Local Government Code Sec. 214.216 adopts the IBC as the municipal commercial building-code baseline for commercial buildings and alterations; cities can adopt local amendments and later editions by ordinance, so the city AHJ still controls the exact permit/code edition.",
        "contractor_guidance": [
            "For Texas clinic TI, verify the city-adopted IBC/existing-building/fire/mechanical/plumbing editions and local amendments before pricing; the state baseline does not replace Dallas/Austin/Houston/local AHJ rules.",
            "Show occupancy basis, suite separation, egress, fire/life-safety, accessibility, and certificate-of-occupancy path on the permit set."
        ],
        "watch_out": [
            "Do not treat Texas as one statewide commercial permit path; commercial building permits and inspections are still local AHJ-driven."
        ],
        "companion_permits": [],
        "trigger_terms": [],
        "source_title": "Texas Local Government Code Sec. 214.216 — International Building Code",
        "source_url": "https://statutes.capitol.texas.gov/Docs/LG/htm/LG.214.htm#214.216",
        "source_quote": "The International Building Code ... applies to all commercial buildings in a municipality and to any alteration, remodeling, enlargement, or repair of those commercial buildings.",
        "confidence": "high",
    },
    {
        "id": "tx_accessibility_tdlr_tas",
        "overlay": "accessibility",
        "title": "Texas Accessibility Standards / TDLR Architectural Barriers review",
        "applies": "all_tx_medical_clinic_ti",
        "summary": "Texas clinics must account for Texas Accessibility Standards. TDLR guidance says projects under $50,000 are not required to register/review with TDLR but still must comply with TAS; projects $50,000 or more require construction-document submission under the Architectural Barriers rules.",
        "contractor_guidance": [
            "For Texas clinic TI, check total estimated project cost early: at $50,000 or more, plan for TDLR/TABS accessibility registration and RAS review/inspection coordination; below $50,000, still design to TAS.",
            "Include accessible route, parking/passenger loading if affected, doors/hardware, reception/check-in counters, restrooms, exam rooms, and signage in the accessibility scope."
        ],
        "watch_out": [
            "A city building permit approval does not by itself clear Texas Accessibility Standards/TAS obligations."
        ],
        "companion_permits": [
            {
                "permit_type": "TDLR/TABS Architectural Barriers registration / RAS review if project cost is $50,000 or more",
                "reason": "Texas Accessibility Standards review can apply separately from the city building permit for qualifying commercial alterations.",
                "certainty": "conditional",
            }
        ],
        "trigger_terms": [],
        "source_title": "TDLR Architectural Barriers FAQ — project registration and review threshold",
        "source_url": "https://www.tdlr.texas.gov/ab/abfaq.htm",
        "source_quote": "If your project's total estimated cost is less than $50,000.00, you are not required to submit the project to the Department for registration and review, however, the project is still required to comply with TAS.",
        "confidence": "high",
    },
    {
        "id": "tx_asc_license_required_when_primary_surgical_services",
        "overlay": "ambulatory_care_thresholds",
        "title": "Texas ambulatory surgical center licensing trigger",
        "applies": "triggered_by_surgery_asc_anesthesia_pacu",
        "summary": "Texas Health and Safety Code Chapter 243 defines an ambulatory surgical center as a facility primarily providing surgical services to patients who do not require overnight hospital care, and requires an ASC license unless an exemption applies.",
        "contractor_guidance": [
            "If the Texas clinic scope includes operating rooms, ASC/day-surgery use, anesthesia/sedation, PACU/recovery bays, or surgical services as a primary service, verify HHSC ASC licensing and architectural/life-safety review before pricing it like ordinary clinic TI.",
            "Do not promise an opening date from building permit final alone when ASC licensing, inspection, or certification remains unresolved."
        ],
        "watch_out": [
            "Texas ASC licensing is a separate opening-risk path from the city building permit when the program is primarily surgical/day-surgery."
        ],
        "companion_permits": [
            {
                "permit_type": "Texas HHSC Ambulatory Surgical Center licensing / architectural review if surgical/ASC program is in scope",
                "reason": "Texas Health and Safety Code Chapter 243 and HHSC rules govern ASCs separately from local building permits.",
                "certainty": "conditional",
            }
        ],
        "trigger_terms": ["surgery", "surgical", "operating room", "operating rooms", "operating room suite", "operating suite", "asc", "ambulatory surgical", "day surgery", "general anesthesia", "deep sedation", "iv sedation", "moderate sedation", "pacu", "recovery bay", "recovery bays"],
        "source_title": "Texas HHSC — Ambulatory Surgical Centers",
        "source_url": "https://www.hhs.texas.gov/providers/health-care-facilities-regulation/ambulatory-surgical-centers",
        "source_quote": "Texas Health and Safety Code Chapter 243 establishes the state licensing requirements for ASCs. HHSC is responsible for the licensing and regulation of ASCs in Texas.",
        "secondary_source_title": "Texas Health and Safety Code Chapter 243 — Ambulatory Surgical Centers",
        "secondary_source_url": "https://statutes.capitol.texas.gov/GetStatute.aspx?Code=HS&Value=243",
        "confidence": "high",
    },
    {
        "id": "tx_dental_xray_registration",
        "overlay": "radiology_xray",
        "title": "Texas dental/medical X-ray registration and radiation-control review",
        "applies": "triggered_by_xray_radiology_ct",
        "summary": "Texas DSHS Radiation Control registers businesses that use X-ray machines for medical, dental, academic, veterinary, and industrial uses. Dental facilities submit radiation-machine registration materials and fees for dental radiation machines.",
        "contractor_guidance": [
            "If the Texas dental/medical clinic includes X-ray, CBCT, panoramic, CT, fluoroscopy, or radiology equipment, coordinate DSHS radiation-machine registration, shielding/vendor documentation, and equipment install timing before final inspection/opening.",
            "Keep radiation registration/shielding documentation separate from the city building permit checklist so the owner understands both paths."
        ],
        "watch_out": [
            "X-ray equipment can create a state registration/shielding/equipment-operation blocker even when the city TI permit is otherwise ready."
        ],
        "companion_permits": [
            {
                "permit_type": "Texas DSHS X-ray machine registration / shielding verification if radiology equipment is included",
                "reason": "DSHS Radiation Control registers dental and medical X-ray machines and related use locations.",
                "certainty": "conditional",
            }
        ],
        "trigger_terms": ["x-ray", "xray", "radiology", "radiographic", "panoramic", "cbct", "cone beam", "ct scanner", "fluoroscopy", "c-arm"],
        "source_title": "Texas DSHS — Dental X-Ray Machine Registration",
        "source_url": "https://www.dshs.texas.gov/texas-radiation-control/x-ray-machines-x-ray-services/dental-x-ray-machine",
        "source_quote": "To obtain a certificate of registration for dental radiation machines, submit the required forms with the appropriate fee.",
        "secondary_source_title": "Texas DSHS — X-Ray Machines and X-Ray Services",
        "secondary_source_url": "https://www.dshs.texas.gov/texas-radiation-control/x-ray-machines-x-ray-services",
        "confidence": "high",
    },
    {
        "id": "tx_medical_gas_verify_local_nfp99",
        "overlay": "medical_gas",
        "title": "Texas medical gas / nitrous / oxygen / vacuum local permit and verifier path",
        "applies": "triggered_by_medical_gas_nitrous_oxygen_vacuum",
        "summary": "Texas sources verified for Phase 4A do not create a single standalone statewide medical-gas permit path for ordinary clinics. Treat oxygen, nitrous, vacuum, alarms, zone valves, and outlets as a local AHJ/MEP/fire review item tied to adopted codes and specialty verifier documentation.",
        "contractor_guidance": [
            "If Texas clinic scope includes oxygen, nitrous, vacuum, medical-gas outlets, alarms, zone valves, or source equipment, carry a separate MEP/fire/local AHJ coordination line item and verifier documentation allowance.",
            "Verify whether the city requires a separate plumbing/mechanical/fire permit or third-party medical-gas verifier paperwork before rough-in and final."
        ],
        "watch_out": [
            "Do not price Texas medical gas/nitrous/vacuum as ordinary plumbing only; local AHJ and specialty verifier requirements can affect rough-in, pressure testing, and final approval."
        ],
        "companion_permits": [
            {
                "permit_type": "Medical gas / nitrous / oxygen / vacuum specialty verification if included",
                "reason": "Texas Phase 4A treats this as local AHJ + adopted-code verification, not a confirmed standalone statewide permit.",
                "certainty": "conditional",
            }
        ],
        "trigger_terms": ["medical gas", "med gas", "medical oxygen", "oxygen piping", "oxygen outlet", "nitrous", "medical vacuum", "dental vacuum", "vacuum line", "vacuum lines", "zone valve", "zone valves", "gas outlet", "gas outlets", "med gas alarm", "medical gas alarm", "zone valve alarm", "medical gas source equipment", "gas manifold", "bulk oxygen"],
        "source_title": "Texas State Law Library — Building Codes in Texas",
        "source_url": "https://guides.sll.texas.gov/building-codes/texas",
        "source_quote": "Local governments may have adopted different or newer versions than the minimum statewide requirements.",
        "secondary_source_title": "TDLR Electricians Compliance Guide — local inspecting authority handles inspections",
        "secondary_source_url": "https://www.tdlr.texas.gov/electricians/compliance-guide.htm",
        "confidence": "medium",
    },
]


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
        "populated_rules": [],
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


def _populate_tx_medical_clinic_schema(schema: dict[str, Any]) -> None:
    schema["phase"] = 4
    schema["coverage_level"] = "phase4a_tx_medical_clinic_ti"
    schema["population_status"] = "partially_populated"
    schema["requires_population_before_state_specific_claims"] = False
    schema["populated_verticals"] = ["medical_clinic_ti"]
    schema["contractor_warning"] = (
        "Texas medical/dental clinic TI overlay is populated for Phase 4A with cited state sources. "
        "Use it as state-level triage; city AHJ/local amendments and owner/licensing facts still control final submittal requirements."
    )
    schema["citation_policy"]["phase4a_note"] = "TX medical_clinic_ti populated rules may appear under state_schema_context, but code_citation remains reserved for renderer-ready citations."

    for rule in _TX_MEDICAL_CLINIC_RULES:
        slot = schema["healthcare_overlays"][rule["overlay"]]
        slot["status"] = "populated"
        slot.setdefault("populated_rules", []).append(deepcopy(rule))
        slot["contractor_guidance"].extend(rule.get("contractor_guidance") or [])
        slot["risk_flags"].extend(rule.get("watch_out") or [])
        if rule.get("summary") and rule["summary"] not in slot.get("rule_summary", ""):
            slot["rule_summary"] = (slot.get("rule_summary") + "\n" + rule["summary"]).strip()
        if not any(hook.get("source_url") == rule["source_url"] for hook in slot.get("citation_hooks", [])):
            if slot.get("status") == "populated" and slot.get("citation_hooks") and slot["citation_hooks"][0].get("citation_status") == "needs_population":
                slot["citation_hooks"] = []
            slot.setdefault("citation_hooks", []).append(
                {
                    "topic": rule["title"],
                    "source_url": rule["source_url"],
                    "source_title": rule["source_title"],
                    "citation_status": "verified",
                    "verified_on": PHASE4A_TX_VERIFIED_ON,
                }
            )
        if rule.get("secondary_source_url") and not any(hook.get("source_url") == rule["secondary_source_url"] for hook in slot.get("citation_hooks", [])):
            slot.setdefault("citation_hooks", []).append({
                "topic": f"Secondary source for {rule['title']}",
                "source_url": rule["secondary_source_url"],
                "source_title": rule.get("secondary_source_title", ""),
                "citation_status": "verified",
                "verified_on": PHASE4A_TX_VERIFIED_ON,
            })


STATE_RULE_SCHEMAS: dict[str, dict[str, Any]] = {
    state: _build_schema(state)
    for state in PHASE3_TARGET_STATES
}
_populate_tx_medical_clinic_schema(STATE_RULE_SCHEMAS["TX"])


def get_state_rule_schema(state: str) -> dict[str, Any] | None:
    """Return a copy of the schema for a supported target state."""
    state_upper = (state or "").strip().upper()
    schema = STATE_RULE_SCHEMAS.get(state_upper)
    return deepcopy(schema) if schema else None


def validate_state_rule_schema(schema: dict[str, Any] | None) -> list[str]:
    """Validate shape and safety constraints for state schemas.

    Phase 3 schemas must remain unpopulated with blank hooks. Phase 4 populated
    slices may contain verified source URLs, but only under populated slots/rules.
    """
    errors: list[str] = []
    if not isinstance(schema, dict):
        return ["schema must be a dict"]

    state = schema.get("state")
    if state not in PHASE3_TARGET_STATES:
        errors.append("state must be one of Phase 3 target states")
    phase = schema.get("phase")
    if phase not in (3, 4):
        errors.append("phase must be 3 or 4")
    populated_schema = phase == 4 or schema.get("population_status") != "not_populated"
    if not populated_schema:
        if schema.get("coverage_level") != "schema_only":
            errors.append("coverage_level must remain schema_only until Phase 4 population")
        if schema.get("population_status") != "not_populated":
            errors.append("population_status must remain not_populated in Phase 3")
    else:
        coverage_level = str(schema.get("coverage_level") or "")
        if coverage_level == "schema_only" or not coverage_level.startswith("phase4"):
            errors.append("Phase 4 schema coverage_level must identify the populated phase4 slice")
        if schema.get("population_status") not in {"partially_populated", "populated"}:
            errors.append("Phase 4 schema population_status must be partially_populated or populated")

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
            status = slot.get("status")
            if status not in {"needs_population", "populated"}:
                errors.append(f"{group_name}.{key}.status must be needs_population or populated")
            rules = slot.get("populated_rules") or []
            if status == "populated" and not rules:
                errors.append(f"{group_name}.{key}.populated_rules required when status is populated")
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
                citation_status = hook.get("citation_status")
                if status == "needs_population":
                    if citation_status != "needs_population":
                        errors.append(f"{group_name}.{key}.citation_hooks[{idx}].citation_status must be needs_population")
                    if hook.get("source_url"):
                        errors.append(f"{group_name}.{key}.citation_hooks[{idx}].source_url must stay blank until populated")
                else:
                    if citation_status != "verified":
                        errors.append(f"{group_name}.{key}.citation_hooks[{idx}].citation_status must be verified for populated slots")
                    if not hook.get("source_url") or not hook.get("source_title"):
                        errors.append(f"{group_name}.{key}.citation_hooks[{idx}] needs source_url and source_title when populated")
            for rule_idx, rule in enumerate(rules):
                if not isinstance(rule, dict):
                    errors.append(f"{group_name}.{key}.populated_rules[{rule_idx}] must be a dict")
                    continue
                for required in ("id", "title", "summary", "source_url", "source_title", "confidence"):
                    if not rule.get(required):
                        errors.append(f"{group_name}.{key}.populated_rules[{rule_idx}].{required} is required")
    return errors


def _term_is_negated(text: str, normalized_term: str) -> bool:
    direct_markers = (
        f" no {normalized_term}",
        f" without {normalized_term}",
        f" excluding {normalized_term}",
        f" exclude {normalized_term}",
        f" not including {normalized_term}",
    )
    if any(marker in text for marker in direct_markers):
        return True
    for marker in (" no ", " without ", " excluding ", " not including "):
        start = text.find(marker)
        while start != -1:
            segment = text[start:start + 100]
            stop_positions = [pos for pos in (segment.find(","), segment.find(";"), segment.find(".")) if pos != -1]
            if stop_positions:
                segment = segment[: min(stop_positions)]
            if normalized_term in segment:
                return True
            start = text.find(marker, start + 1)
    return False


def _job_has_any(job: str, terms: list[str]) -> bool:
    text = f" {job.lower().replace('-', ' ')} "
    for term in terms:
        normalized = term.lower().replace("-", " ")
        if normalized not in text:
            continue
        if not _term_is_negated(text, normalized):
            return True
    return False


def _rule_applies(rule: dict[str, Any], job_type: str) -> bool:
    applies = rule.get("applies")
    if applies == "all_tx_medical_clinic_ti":
        return True
    return _job_has_any(job_type or "", list(rule.get("trigger_terms") or []))


def _safe_rule_for_context(rule: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "id", "title", "overlay", "applies", "summary", "contractor_guidance", "watch_out",
        "companion_permits", "source_title", "source_url", "source_quote", "secondary_source_title",
        "secondary_source_url", "confidence",
    }
    return {key: deepcopy(value) for key, value in rule.items() if key in allowed}


def compact_state_schema_context(state: str, vertical: str, job_type: str = "") -> dict[str, Any] | None:
    """Small customer-safe schema context for attaching to results."""
    schema = get_state_rule_schema(state)
    if not schema:
        return None

    if vertical == "medical_clinic_ti":
        overlays = schema["healthcare_overlays"]
    elif vertical in {"restaurant_ti", "office_ti"}:
        overlays = schema["general_overlays"]
    else:
        return None

    triggered_rules: list[dict[str, Any]] = []
    if schema.get("state") == "TX" and vertical == "medical_clinic_ti":
        for slot in overlays.values():
            for rule in slot.get("populated_rules") or []:
                if _rule_applies(rule, job_type):
                    triggered_rules.append(_safe_rule_for_context(rule))

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
                "verified_sources": [
                    {
                        "title": hook["source_title"],
                        "url": hook["source_url"],
                        "verified_on": hook.get("verified_on", ""),
                    }
                    for hook in slot["citation_hooks"]
                    if hook.get("citation_status") == "verified" and hook.get("source_url")
                ],
            }
            for key, slot in overlays.items()
        ],
        "triggered_rules": triggered_rules,
        "contractor_warning": schema["contractor_warning"],
    }
