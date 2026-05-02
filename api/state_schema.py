"""State rule schema framework and Phase 4 populated overlays for PermitAssist.

Phase 3 created citation-ready CA/TX/FL/MA state overlay slots without fake
state-rule claims. Phase 4 populates those slots one careful state/vertical at a
time. Phase 4A populated Texas medical/dental clinic TI, Phase 4B populated
California, and Phase 4C populates Florida.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

PHASE3_TARGET_STATES = ("CA", "TX", "FL", "MA")

PHASE4A_TX_VERIFIED_ON = "2026-05-02"
PHASE4B_CA_VERIFIED_ON = "2026-05-02"
PHASE4C_FL_VERIFIED_ON = "2026-05-02"

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
    "energy_code": {
        "label": "Energy-code forms / nonresidential compliance",
        "question": "Does the clinic TI include lighting, HVAC, envelope, controls, water-heating, acceptance-testing, or other energy-code form scope?",
        "citation_topics": ["state energy code", "nonresidential compliance forms", "acceptance testing"],
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
        "trigger_terms": ["medical gas", "med gas", "medical oxygen", "oxygen piping", "oxygen outlet", "nitrous", "medical vacuum", "dental vacuum", "vacuum line", "vacuum lines", "zone valve", "zone valves", "medical gas outlet", "medical gas outlets", "med gas outlet", "med gas outlets", "dental gas outlet", "dental gas outlets", "med gas alarm", "medical gas alarm", "zone valve alarm", "medical gas source equipment", "gas manifold", "bulk oxygen"],
        "source_title": "Texas State Law Library — Building Codes in Texas",
        "source_url": "https://guides.sll.texas.gov/building-codes/texas",
        "source_quote": "Local governments may have adopted different or newer versions than the minimum statewide requirements.",
        "secondary_source_title": "TDLR Electricians Compliance Guide — local inspecting authority handles inspections",
        "secondary_source_url": "https://www.tdlr.texas.gov/electricians/compliance-guide.htm",
        "confidence": "medium",
    },
]


_CA_MEDICAL_CLINIC_RULES: list[dict[str, Any]] = [
    {
        "id": "ca_title24_local_ahj_oshpd3_awareness",
        "overlay": "occupancy_classification",
        "title": "California Title 24 baseline, local AHJ, and OSHPD 3 clinic awareness",
        "applies": "all_ca_medical_clinic_ti",
        "summary": "California commercial clinic TI must be coordinated against the California Building Standards Code, Title 24, plus local AHJ amendments. HCAI explains that OSHPD 3 clinic requirements apply only to clinics licensed under Health and Safety Code Section 1200 and outpatient services of a hospital licensed under Section 1250.",
        "contractor_guidance": [
            "For California clinic TI, verify the locally enforced Title 24 edition/parts, city amendments, fire marshal requirements, and whether the owner program is a licensed clinic/outpatient hospital service before pricing it as ordinary medical office TI.",
            "Show occupancy basis, accessibility, CALGreen/energy scope, fire/life-safety, plumbing/mechanical/electrical, and certificate-of-occupancy path on the permit set."
        ],
        "watch_out": [
            "Do not assume every California medical office is OSHPD 3/HCAI-reviewed; HCAI says OSHPD 3 clinic requirements apply to specific licensed clinic/hospital outpatient categories."
        ],
        "companion_permits": [],
        "trigger_terms": [],
        "source_title": "California Building Standards Commission — Codes",
        "source_url": "https://www.dgs.ca.gov/bsc/codes",
        "source_quote": "The California Building Standards Code is a compilation of three types of building standards from three different origins:",
        "secondary_source_title": "HCAI — Codes and Regulations / OSHPD 3 Clinics",
        "secondary_source_url": "https://hcai.ca.gov/facilities/building-safety/codes-and-regulations/",
        "confidence": "high",
    },
    {
        "id": "ca_cdph_pcc_license_when_primary_care_clinic",
        "overlay": "healthcare_licensing",
        "title": "California CDPH primary care clinic licensure trigger",
        "applies": "triggered_by_primary_care_clinic_license_terms",
        "summary": "CDPH explains that primary care clinic applications require a physical plant and control-of-property documentation, and HCAI identifies licensed Health and Safety Code Section 1200 clinics as OSHPD 3 clinic territory. This is a licensing/opening path, not just a local building permit issue.",
        "contractor_guidance": [
            "If the California scope is a licensed primary care/community/free clinic, FQHC-like clinic, or hospital outpatient clinic, verify CDPH licensing/CAB packet status and OSHPD 3/HCAI applicability before promising opening after local permit final.",
            "Ask the owner whether this is a private physician/dental office, a licensed primary care clinic, a community/free clinic, or outpatient services of a hospital; the answer changes the state review path."
        ],
        "watch_out": [
            "Licensed clinic status can create state licensing/survey/application blockers separate from city plan check and inspection."
        ],
        "companion_permits": [
            {
                "permit_type": "CDPH primary care clinic licensing / CAB application path if HSC 1200 licensed clinic is in scope",
                "reason": "California licensed primary care clinics have CDPH licensing and physical-plant documentation requirements beyond local permit issuance.",
                "certainty": "conditional",
            }
        ],
        "trigger_terms": ["primary care clinic", "community clinic", "free clinic", "fqhc", "federally qualified health center", "licensed clinic", "hsc 1200", "section 1200", "hospital outpatient", "outpatient services of a hospital", "oshpd 3", "oshpd-3"],
        "source_title": "CDPH — Primary Care Clinic FAQs",
        "source_url": "https://www.cdph.ca.gov/Programs/CHCQ/LCP/Pages/Primary-Care-Clinic-FAQs.aspx",
        "source_quote": "Yes. According to HSC § 1226(a), a physical plant and control of property documentation are required to apply for PCC licensure.",
        "secondary_source_title": "HCAI — Codes and Regulations / OSHPD 3 Clinics",
        "secondary_source_url": "https://hcai.ca.gov/facilities/building-safety/codes-and-regulations/",
        "confidence": "high",
    },
    {
        "id": "ca_surgc_asc_license_certification_trigger",
        "overlay": "ambulatory_care_thresholds",
        "title": "California surgical clinic / ASC licensure or certification trigger",
        "applies": "triggered_by_surgery_asc_anesthesia_pacu",
        "summary": "CDPH states that a state license is required to operate a California Surgical Clinic (SURGC) unless exempt, and defines SURGC/ASC around ambulatory surgical care or surgical services for patients not requiring hospitalization/over-24-hour stays. Physician/dentist-owned office exemptions may matter and must be verified by counsel/CDPH.",
        "contractor_guidance": [
            "If the California clinic includes operating rooms, ASC/SURGC/day surgery, surgical services, PACU/recovery, general anesthesia, or deep sedation, verify CDPH SURGC/ASC licensing or certification and any HCAI/OSHPD 3/local life-safety path before pricing it like ordinary clinic TI.",
            "Do not promise opening from local final inspection alone when SURGC/ASC licensing, exemption status, certification, or survey remains unresolved."
        ],
        "watch_out": [
            "California surgical/ASC programs can have CDPH/CMS/licensure/exemption questions separate from local building permit approval."
        ],
        "companion_permits": [
            {
                "permit_type": "CDPH Surgical Clinic (SURGC) license / ASC certification path if surgical/ASC program is in scope",
                "reason": "California CDPH application materials and FAQs identify SURGC/ASC licensing/certification requirements and exemptions.",
                "certainty": "conditional",
            }
        ],
        "trigger_terms": ["surgery", "surgical", "operating room", "operating rooms", "operating suite", "asc", "ambulatory surgery", "ambulatory surgical", "surgc", "surgery center", "day surgery", "general anesthesia", "deep sedation", "pacu", "recovery bay", "recovery bays"],
        "source_title": "CDPH — Ambulatory Surgery Center FAQs",
        "source_url": "https://www.cdph.ca.gov/Programs/CHCQ/LCP/Pages/Ambulatory-Surgery-Center-FAQs.aspx",
        "source_quote": "A state license is required to operate a SURGC in California unless exempt.",
        "secondary_source_title": "CDPH — SURGC-ASC Initial Application Packet",
        "secondary_source_url": "https://www.cdph.ca.gov/Programs/CHCQ/LCP/Pages/AppPacket/SURGC-ASC-Initial.aspx",
        "confidence": "high",
    },
    {
        "id": "ca_rhb_xray_registration_dental_medical",
        "overlay": "radiology_xray",
        "title": "California CDPH/RHB radiation-machine registration and dental X-ray inspection",
        "applies": "triggered_by_xray_radiology_ct",
        "summary": "California CDPH Radiologic Health Branch says entities acquiring radiation machines must register with RHB within 30 days, and dental X-ray providers are inspected by RHB on average every five years with posted-room and radiation-protection-program expectations.",
        "contractor_guidance": [
            "If California dental/medical clinic TI includes X-ray, CBCT, panoramic, CT, fluoroscopy, or radiology equipment, coordinate CDPH/RHB registration, shielding/vendor documentation, room posting, and equipment install timing before opening.",
            "Keep RHB registration/inspection/radiation safety tasks separate from the city building permit checklist so the owner understands both paths."
        ],
        "watch_out": [
            "Radiation-machine registration and dental X-ray compliance can become an opening blocker even when the local TI permit is ready."
        ],
        "companion_permits": [
            {
                "permit_type": "CDPH/RHB radiation machine registration / dental X-ray compliance if radiology equipment is included",
                "reason": "California RHB registration is required after acquiring radiation machines and dental X-ray providers are subject to RHB inspection/compliance expectations.",
                "certainty": "conditional",
            }
        ],
        "trigger_terms": ["x-ray", "xray", "radiology", "radiographic", "panoramic", "cbct", "cone beam", "ct scanner", "fluoroscopy", "c-arm"],
        "source_title": "CDPH/RHB — Radiation Machine Registration",
        "source_url": "https://www.cdph.ca.gov/Programs/CEH/DRSEM/pages/rhb-x-ray/registration.aspx",
        "source_quote": "Title 17, California Code of Regulations § 30108 requires that when an entity acquires a radiation machine, they must register with the Radiologic Health Branch (RHB) within 30 days.",
        "secondary_source_title": "CDPH/RHB — Dental X-ray Providers",
        "secondary_source_url": "https://www.cdph.ca.gov/Programs/CEH/DRSEM/Pages/RHB-X-ray/ICE/Dental.aspx",
        "confidence": "high",
    },
    {
        "id": "ca_title24_part6_energy_forms_nonresidential_ti",
        "overlay": "energy_code",
        "title": "California Title 24 Part 6 nonresidential energy forms for clinic alterations",
        "applies": "triggered_by_energy_alteration_scope",
        "summary": "California clinic TI may need Title 24 Part 6 nonresidential energy compliance forms when lighting, mechanical, envelope, controls, or water-heating scope is touched; Energy Code Ace provides utility-sponsored California Energy Code compliance-form tools for identifying required forms before AHJ submittal.",
        "contractor_guidance": [
            "For California clinic TI, budget for Title 24 Part 6 nonresidential energy compliance forms when lighting, mechanical, envelope, controls, or water-heating scope is touched.",
            "Use the current code-year NRCC/NRCI form path and verify any HERS/acceptance testing requirements before permit submittal."
        ],
        "watch_out": [
            "California energy documentation can delay plan check even for ordinary clinic TI if forms/acceptance testing are missing."
        ],
        "companion_permits": [],
        "trigger_terms": ["lighting", "hvac", "mechanical", "envelope", "lighting controls", "water heating", "water-heating", "title 24 energy", "energy forms", "nrcc", "nrci", "acceptance testing"],
        "source_title": "Energy Code Ace — Get Forms",
        "source_url": "https://energycodeace.com/content/get-forms",
        "source_quote": "Use this tool before your permit submittal to determine which forms will be required for your Addition, Alteration or New Construction project.",
        "confidence": "high",
    },
    {
        "id": "ca_cpc_dental_medgas_vacuum",
        "overlay": "medical_gas",
        "title": "California Plumbing Code dental gas/vacuum and medical gas coordination",
        "applies": "triggered_by_medical_gas_nitrous_oxygen_vacuum",
        "summary": "California Plumbing Code materials include dental gas and vacuum systems and medical gas/vacuum content tied to Title 24/local enforcement. Treat nitrous, oxygen, medical/dental vacuum, medical-gas outlets, zone valves, manifolds, and alarms as local plumbing/mechanical/fire review plus verifier/vendor coordination.",
        "contractor_guidance": [
            "If California dental/clinic scope includes nitrous, medical oxygen, medical gas outlets, dental/medical vacuum, zone valves, manifolds, or gas alarms, carry a separate plumbing/mechanical/fire coordination and verification line item.",
            "Verify local AHJ permit split and inspection/third-party verifier expectations before rough-in; do not price it as ordinary plumbing only."
        ],
        "watch_out": [
            "California dental gas/vacuum/medical gas details can affect rough-in, pressure testing, labeling, inspection, and final approval."
        ],
        "companion_permits": [
            {
                "permit_type": "Dental/medical gas and vacuum local plumbing/mechanical/fire review if included",
                "reason": "California Title 24/CPC/local enforcement can require specialty review and verification for dental/medical gas and vacuum systems.",
                "certainty": "conditional",
            }
        ],
        "trigger_terms": ["medical gas", "med gas", "medical oxygen", "oxygen piping", "oxygen outlet", "nitrous", "medical vacuum", "dental vacuum", "vacuum line", "vacuum lines", "zone valve", "zone valves", "medical gas outlet", "medical gas outlets", "med gas outlet", "med gas outlets", "med gas alarm", "medical gas alarm", "zone valve alarm", "medical gas source equipment", "gas manifold", "bulk oxygen"],
        "source_title": "2025 California Plumbing Code — IAPMO ePub",
        "source_url": "https://epubs.iapmo.org/2025/CPC/",
        "source_quote": "Part V – Dental Gas and Vacuum Systems. 1327.0 Dental Gas and Vacuum Systems.",
        "confidence": "medium",
    },
]

_FL_MEDICAL_CLINIC_RULES: list[dict[str, Any]] = [
    {
        "id": "fl_fbc_local_ahj_accessibility_baseline",
        "overlay": "accessibility",
        "title": "Florida Building Code, local AHJ, and accessibility baseline for clinic alterations",
        "applies": "all_fl_medical_clinic_ti",
        "summary": "Florida Statutes make the Florida Building Code applicable to building alteration, modification, repair, and related work; Florida accessibility law incorporates ADA-aligned accessibility requirements. For clinic TI, use this as state-level code/accessibility triage while the local building/fire AHJ controls permit intake, local amendments, inspections, and CO/opening path.",
        "contractor_guidance": [
            "For Florida clinic TI, verify the current Florida Building Code edition, local AHJ amendments, fire/life-safety review, accessibility scope, and certificate/opening requirements before quoting.",
            "Show occupancy basis, egress, fire/life-safety, accessible route/restrooms/reception/exam-room features if affected, MEP scope, and local inspection path on the permit set."
        ],
        "watch_out": [
            "Do not treat Florida medical/dental clinic TI as one statewide online permit path; local AHJ submittal, fire review, inspections, and CO/opening requirements still control."
        ],
        "companion_permits": [],
        "trigger_terms": [],
        "source_title": "Florida Statutes Sec. 553.73 — Florida Building Code",
        "source_url": "https://www.leg.state.fl.us/Statutes/index.cfm?App_mode=Display_Statute&URL=0500-0599/0553/Sections/0553.73.html",
        "source_quote": "The Florida Building Code shall contain or incorporate by reference all laws and rules which pertain to and govern the design, construction, erection, alteration, modification, repair, and demolition of public and private buildings, structures, and facilities",
        "secondary_source_title": "Florida Statutes Sec. 553.502 — Accessibility intent",
        "secondary_source_url": "https://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&Search_String=&URL=0500-0599/0553/Sections/0553.502.html",
        "confidence": "high",
    },
    {
        "id": "fl_ahca_health_care_clinic_license_check",
        "overlay": "healthcare_licensing",
        "title": "Florida AHCA health care clinic licensure verification",
        "applies": "triggered_by_ahca_health_care_clinic_license_terms",
        "summary": "Florida AHCA licenses health care clinics where health care services are provided and charges are tendered for reimbursement, with special application/inspection and MRI accreditation requirements for applicable licensed clinics. Ordinary private physician or dental offices should not be assumed to require AHCA clinic licensure without owner/program facts.",
        "contractor_guidance": [
            "If the Florida scope mentions AHCA/licensed health care clinic status, reimbursement clinic operations, MRI services, mobile clinic, or portable equipment provider use, verify AHCA health care clinic licensing and inspection requirements before promising opening from building final alone.",
            "Ask the owner whether this is a private physician/dental office or an AHCA-licensed health care clinic; the answer changes the state opening path."
        ],
        "watch_out": [
            "AHCA licensure inspection or MRI accreditation can be a separate opening blocker from local building permit final when the owner program falls under AHCA clinic rules."
        ],
        "companion_permits": [
            {
                "permit_type": "Florida AHCA health care clinic licensing / inspection verification if licensed clinic, MRI, mobile clinic, portable equipment provider, or reimbursement-clinic facts apply",
                "reason": "AHCA health care clinic rules can apply separately from local building-permit issuance for covered clinic programs.",
                "certainty": "conditional",
            }
        ],
        "trigger_terms": ["ahca licensed health care clinic", "florida licensed health care clinic", "health care clinic license", "healthcare clinic license", "health care clinic licensure", "healthcare clinic licensure", "reimbursement clinic", "mri services", "magnetic resonance imaging", "mobile clinic", "portable equipment provider"],
        "source_title": "Florida AHCA — Health Care Clinics",
        "source_url": "https://ahca.myflorida.com/health-quality-assurance/bureau-of-health-facility-regulation/hospital-outpatient-services-unit/health-care-clinics",
        "source_quote": "licenses entities where health care services are provided to individuals and which tender charges for reimbursement for such services, including a mobile clinic and a portable equipment provider",
        "secondary_source_title": "Florida AHCA — Health Care Clinic Licensing Requirements",
        "secondary_source_url": "https://ahca.myflorida.com/health-quality-assurance/bureau-of-health-facility-regulation/hospital-outpatient-services-unit/health-care-clinics/licensing-requirements",
        "confidence": "high",
    },
    {
        "id": "fl_ahca_asc_opc_review_when_surgical",
        "overlay": "ambulatory_care_thresholds",
        "title": "Florida AHCA ambulatory surgical center / Office of Plans and Construction trigger",
        "applies": "triggered_by_surgery_asc_anesthesia_pacu",
        "summary": "Florida AHCA defines an ambulatory surgery center as a licensed facility not part of a hospital with the primary purpose of elective surgical care where the patient is admitted and discharged within 24 hours. AHCA says initial ASC applicants must have a current project under Office of Plans and Construction review before licensure, and a license is required before patient care.",
        "contractor_guidance": [
            "If the Florida clinic includes an ASC/day-surgery program, operating rooms, surgical services, general anesthesia, deep sedation, PACU, or recovery bays, verify AHCA ASC licensing and Office of Plans and Construction review before pricing it like ordinary clinic TI.",
            "Do not promise patient-care/opening from local final inspection alone when AHCA ASC licensure, OPC review, survey, or license issuance remains unresolved."
        ],
        "watch_out": [
            "Florida ASC/surgical projects can require AHCA Office of Plans and Construction review and licensure before patient care, separate from the local building permit."
        ],
        "companion_permits": [
            {
                "permit_type": "Florida AHCA ASC licensing / Office of Plans and Construction review if ASC/surgical program is in scope",
                "reason": "Florida AHCA identifies ASC licensure and OPC building-code-compliance review as part of initial ASC opening.",
                "certainty": "conditional",
            }
        ],
        "trigger_terms": ["surgery", "surgical", "operating room", "operating rooms", "operating room suite", "operating suite", "asc", "ambulatory surgery", "ambulatory surgical", "ambulatory surgery center", "day surgery", "surgery center", "general anesthesia", "deep sedation", "pacu", "recovery bay", "recovery bays"],
        "source_title": "Florida AHCA — Ambulatory Surgical Center",
        "source_url": "https://ahca.myflorida.com/health-quality-assurance/bureau-of-health-facility-regulation/hospital-outpatient-services-unit/ambulatory-surgical-center",
        "source_quote": "Initial applicants must first have a current project under review with AHCA’s Office of Plans and Construction for building code compliance.",
        "confidence": "high",
    },
    {
        "id": "fl_doh_xray_registration_dental_medical",
        "overlay": "radiology_xray",
        "title": "Florida DOH ionizing radiation machine / dental X-ray registration and inspection",
        "applies": "triggered_by_xray_radiology_ct",
        "summary": "Florida Department of Health Radiation Control provides annual radiation machine registration and fee payment for X-ray machines, registers more than 21,000 facilities, and lists dental machines among inspected equipment categories. X-ray/CBCT/radiology equipment should trigger DOH registration/inspection/shielding coordination; ultrasound or MRI alone should not trigger X-ray registration.",
        "contractor_guidance": [
            "If the Florida dental/medical clinic includes X-ray, CBCT/cone beam, panoramic, CT, fluoroscopy, C-arm, or mammography equipment, coordinate Florida Department of Health radiation machine registration, shielding/vendor documentation, and equipment install timing before opening.",
            "Keep DOH radiation-machine registration/inspection tasks separate from the local TI permit checklist so the owner understands both paths."
        ],
        "watch_out": [
            "Radiation-machine registration, inspection, or shielding documentation can become an opening blocker even when the local Florida TI permit is ready."
        ],
        "companion_permits": [
            {
                "permit_type": "Florida DOH radiation machine registration / X-ray inspection and shielding verification if ionizing equipment is included",
                "reason": "Florida DOH Radiation Control handles radiation machine registration and inspection categories including dental machines.",
                "certainty": "conditional",
            }
        ],
        "trigger_terms": ["x-ray", "xray", "radiology", "radiographic", "panoramic", "cbct", "cone beam", "ct scanner", "fluoroscopy", "c-arm", "mammography"],
        "source_title": "Florida Department of Health — Ionizing Radiation Machines (X-Ray)",
        "source_url": "https://www.floridahealth.gov/licensing-regulations/radiation-control/ionizing-radiation-machines-x-ray/",
        "source_quote": "View annual radiation machine registration information and pay registration fees.",
        "confidence": "high",
    },
    {
        "id": "fl_medical_gas_certified_contractor",
        "overlay": "medical_gas",
        "title": "Florida medical gas contractor and workforce certification trigger",
        "applies": "triggered_by_medical_gas_nitrous_oxygen_vacuum",
        "summary": "Florida medical gas work has contractor/workforce certification requirements. Florida Administrative Code 61G4-15.031 requires licensed plumbing contractors and workers performing medical gas systems work to complete a 32-hour Board-approved medical gas course tied to NFPA 99C and ASSE Series 6000; F.S. 489.1136 covers tubing, pipe, or conduit transporting gaseous substances for medical purposes.",
        "contractor_guidance": [
            "If the Florida clinic scope includes nitrous, medical oxygen, medical/dental vacuum, medical-gas outlets, zone valves, manifolds, or alarms, verify certified medical-gas contractor/workforce credentials and local plumbing/mechanical/fire permit split before rough-in.",
            "Carry verifier/testing/documentation time separately from ordinary plumbing; certification and local inspection details can affect rough-in, pressure testing, labeling, and final approval."
        ],
        "watch_out": [
            "Do not price Florida medical gas/nitrous/vacuum as ordinary plumbing only; certified contractor/workforce and local AHJ inspection requirements can affect schedule and cost."
        ],
        "companion_permits": [
            {
                "permit_type": "Certified medical gas / nitrous / oxygen / vacuum contractor verification and local MEP/fire permit coordination if included",
                "reason": "Florida statutes and administrative rules impose medical-gas certification requirements in addition to local permit/inspection requirements.",
                "certainty": "conditional",
            }
        ],
        "trigger_terms": ["medical gas", "med gas", "medical oxygen", "oxygen piping", "oxygen outlet", "nitrous", "medical vacuum", "dental vacuum", "vacuum line", "vacuum lines", "zone valve", "zone valves", "medical gas outlet", "medical gas outlets", "med gas outlet", "med gas outlets", "dental gas outlet", "dental gas outlets", "med gas alarm", "medical gas alarm", "zone valve alarm", "medical gas source equipment", "gas manifold", "bulk oxygen"],
        "source_title": "Florida Statutes Sec. 489.1136 — Medical gas certification",
        "source_url": "https://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&Search_String=&URL=0400-0499/0489/Sections/0489.1136.html",
        "source_quote": "any plumbing contractor who wishes to engage in the business of installation, improvement, repair, or maintenance of any tubing, pipe, or similar conduit used to transport gaseous or partly gaseous substances for medical purposes",
        "secondary_source_title": "Florida Administrative Code 61G4-15.031 — Medical Gas Certification",
        "secondary_source_url": "https://www.flrules.org/gateway/ruleNo.asp?id=61G4-15.031",
        "confidence": "high",
    },
    {
        "id": "fl_energy_code_commercial_alteration_scope",
        "overlay": "energy_code",
        "title": "Florida Energy Conservation Code commercial alteration coordination",
        "applies": "triggered_by_energy_alteration_scope",
        "summary": "The Florida Building Code, 8th Edition (2023), is effective statewide for the current code cycle. Clinic TI that touches lighting, HVAC/mechanical systems, envelope, lighting/HVAC/energy controls, water heating, or energy-calculation scope should verify commercial energy-code submittal and inspection requirements with the local AHJ; finish-only work should not be over-warned.",
        "contractor_guidance": [
            "For Florida clinic TI, budget for Florida Energy Conservation Code documentation when lighting, HVAC/mechanical systems, envelope, lighting/HVAC/energy controls, or water-heating scope is touched.",
            "Verify the AHJ-required commercial energy forms/calculations and inspection responsibilities before permit submittal."
        ],
        "watch_out": [
            "Energy-code documentation can delay Florida plan review when lighting/HVAC/controls scope is included but forms or calculations are missing."
        ],
        "companion_permits": [],
        "trigger_terms": ["lighting", "hvac", "mechanical alteration", "mechanical alterations", "mechanical systems", "envelope", "lighting controls", "hvac controls", "energy controls", "water heating", "water-heating", "energy code", "energy forms", "energy calculation", "energy calculations"],
        "source_title": "Florida Building Commission — Florida Building Code homepage",
        "source_url": "https://www.floridabuilding.org/",
        "source_quote": "The Effective Date for the Florida Building Code, 8th Edition (2023), is December 31, 2023.",
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


def _populate_medical_clinic_schema(schema: dict[str, Any], *, rules: list[dict[str, Any]], coverage_level: str, warning: str, note_key: str, note: str, verified_on: str) -> None:
    schema["phase"] = 4
    schema["coverage_level"] = coverage_level
    schema["population_status"] = "partially_populated"
    schema["requires_population_before_state_specific_claims"] = False
    populated = set(schema.get("populated_verticals") or [])
    populated.add("medical_clinic_ti")
    schema["populated_verticals"] = sorted(populated)
    schema["contractor_warning"] = warning
    schema["citation_policy"][note_key] = note

    for rule in rules:
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
                    "verified_on": verified_on,
                }
            )
        if rule.get("secondary_source_url") and not any(hook.get("source_url") == rule["secondary_source_url"] for hook in slot.get("citation_hooks", [])):
            slot.setdefault("citation_hooks", []).append({
                "topic": f"Secondary source for {rule['title']}",
                "source_url": rule["secondary_source_url"],
                "source_title": rule.get("secondary_source_title", ""),
                "citation_status": "verified",
                "verified_on": verified_on,
            })


STATE_RULE_SCHEMAS: dict[str, dict[str, Any]] = {
    state: _build_schema(state)
    for state in PHASE3_TARGET_STATES
}
_populate_medical_clinic_schema(
    STATE_RULE_SCHEMAS["TX"],
    rules=_TX_MEDICAL_CLINIC_RULES,
    coverage_level="phase4a_tx_medical_clinic_ti",
    warning="Texas medical/dental clinic TI overlay is populated for Phase 4A with cited state sources. Use it as state-level triage; city AHJ/local amendments and owner/licensing facts still control final submittal requirements.",
    note_key="phase4a_note",
    note="TX medical_clinic_ti populated rules may appear under state_schema_context, but code_citation remains reserved for renderer-ready citations.",
    verified_on=PHASE4A_TX_VERIFIED_ON,
)
_populate_medical_clinic_schema(
    STATE_RULE_SCHEMAS["CA"],
    rules=_CA_MEDICAL_CLINIC_RULES,
    coverage_level="phase4b_ca_medical_clinic_ti",
    warning="California medical/dental clinic TI overlay is populated for Phase 4B with cited state sources. Use it as state-level triage; local AHJ, Title 24 edition, HCAI/CDPH licensing facts, and owner program still control final submittal requirements.",
    note_key="phase4b_note",
    note="CA medical_clinic_ti populated rules may appear under state_schema_context, but code_citation remains reserved for renderer-ready citations.",
    verified_on=PHASE4B_CA_VERIFIED_ON,
)

_populate_medical_clinic_schema(
    STATE_RULE_SCHEMAS["FL"],
    rules=_FL_MEDICAL_CLINIC_RULES,
    coverage_level="phase4c_fl_medical_clinic_ti",
    warning="Florida medical/dental clinic TI overlay is populated for Phase 4C with cited state sources. Use it as state-level triage; local AHJ, AHCA/DOH/licensing facts, current Florida Building Code edition, and owner program still control final submittal/opening requirements.",
    note_key="phase4c_note",
    note="FL medical_clinic_ti populated rules may appear under state_schema_context, but code_citation remains reserved for renderer-ready citations.",
    verified_on=PHASE4C_FL_VERIFIED_ON,
)


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
    if phase == 3 and schema.get("population_status") in {"partially_populated", "populated"}:
        errors.append("phase must be 4 when population_status is populated or partially_populated")
    if phase == 4 and schema.get("population_status") == "not_populated":
        errors.append("phase must be 3 when population_status is not_populated")
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
        f" not {normalized_term}",
        f" not including {normalized_term}",
    )
    if any(marker in text for marker in direct_markers):
        return True
    if normalized_term == "licensed clinic" and " not hsc 1200 licensed clinic" in text:
        return True
    if re.search(rf"\bnot\s+(?:a\s+|an\s+|the\s+|performing\s+|used\s+as\s+|including\s+)?{re.escape(normalized_term)}\b", text):
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
    if applies in {"all_tx_medical_clinic_ti", "all_ca_medical_clinic_ti", "all_fl_medical_clinic_ti"}:
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
    if schema.get("state") in {"TX", "CA", "FL"} and vertical == "medical_clinic_ti":
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
