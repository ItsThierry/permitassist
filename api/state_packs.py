"""State-specific expert packs for PermitAssist.

These packs are deterministic guardrails that are appended after model synthesis.
They are intentionally additive: they do not replace jurisdiction research, they
surface state-level gotchas that contractors should always see.
"""

from __future__ import annotations

from copy import deepcopy

CALIFORNIA_VHFHSZ_URL = (
    "https://osfm.fire.ca.gov/divisions/community-wildfire-preparedness-and-mitigation/"
    "wildland-hazards-building-codes/fire-hazard-severity-zones-maps/"
)

CALIFORNIA_MUNICIPAL_UTILITIES = {
    "pasadena": "Pasadena Water and Power (PWP)",
    "los angeles": "Los Angeles Department of Water and Power (LADWP)",
    "sacramento": "Sacramento Municipal Utility District (SMUD)",
    "anaheim": "Anaheim Public Utilities (APU)",
    "burbank": "Burbank Water and Power (BWP)",
    "glendale": "Glendale Water and Power (GWP)",
    "riverside": "Riverside Public Utilities (RPU)",
}

CALIFORNIA_HISTORIC_OVERLAYS = {
    "pasadena": "Pasadena has historic district overlays including Bungalow Heaven, Garfield Heights, and Madison Heights.",
    "san francisco": "San Francisco has multiple local historic districts and conservation districts.",
    "berkeley": "Berkeley has historic districts and landmarks that can add discretionary review.",
    "santa monica": "Santa Monica has historic districts, landmarks, and neighborhood conservation overlays.",
}

CALIFORNIA_VHFHSZ_CITIES = {
    "pasadena",
    "malibu",
    "santa barbara",
    "los angeles",
    "la county",
    "los angeles county",
}

STATE_PACKS = {
    "CA": {
        "name": "California expert pack",
        "expert_notes": [
            {
                "title": "California ADU 60-day ministerial shot clock",
                "note": (
                    "California state law (AB 881 / Govt Code 65852.2) requires the city to approve or deny "
                    "ADU permits within 60 days of a complete application. If they exceed this, you have grounds "
                    "to escalate."
                ),
                "applies_to": "ADU jobs",
                "source": "California Government Code 65852.2(b)(1)",
            },
            {
                "title": "California ADU impact-fee exemption under 750 sq ft",
                "note": "Impact fees waived for ADUs under 750 sq ft per California Government Code 65852.2(f)(3).",
                "applies_to": "ADU jobs under 750 sq ft",
                "source": "California Government Code 65852.2(f)(3)",
            },
            {
                "title": "Title 24 / CF1R energy compliance",
                "note": (
                    "Title 24 energy compliance and CF1R documentation are required for new construction and "
                    "alterations that change conditioned space. Solar mandate may apply for new detached ADUs "
                    "depending on scope."
                ),
                "applies_to": "New construction, ADUs, and conditioned-space alterations",
                "source": "California Energy Code / Title 24",
            },
            {
                "title": "Cal Fire VHFHSZ check",
                "note": (
                    "Check whether the parcel is in a Cal Fire Very High Fire Hazard Severity Zone (VHFHSZ). "
                    "Pasadena, Malibu, Santa Barbara, and parts of Los Angeles County can trigger additional "
                    "fire-resistive material and defensible-space requirements."
                ),
                "applies_to": "Wildfire-prone California jurisdictions",
                "source": CALIFORNIA_VHFHSZ_URL,
            },
        ],
    }
}


def get_state_expert_notes(state: str, city: str = "", job_description: str = "") -> list[dict]:
    """Return expert notes for a state/city/job combination.

    The result is a new list of dicts so callers can safely mutate it.
    """
    state_upper = (state or "").strip().upper()
    pack = STATE_PACKS.get(state_upper)
    if not pack:
        return []

    notes = deepcopy(pack.get("expert_notes", []))
    city_key = (city or "").strip().lower()

    utility = CALIFORNIA_MUNICIPAL_UTILITIES.get(city_key) if state_upper == "CA" else None
    if utility:
        notes.append(
            {
                "title": "California municipal utility coordination",
                "note": (
                    f"Local utility is {utility} — service/interconnection coordination goes through the city utility, "
                    "not SoCal Edison/PG&E."
                ),
                "applies_to": "Electrical service, solar, battery, EV, and utility-interconnection work",
                "source": "California municipal utility rules",
            }
        )

    historic = CALIFORNIA_HISTORIC_OVERLAYS.get(city_key) if state_upper == "CA" else None
    if historic:
        notes.append(
            {
                "title": "California historic district overlay warning",
                "note": (
                    f"{historic} Even with state ministerial ADU approval, design/historic review can add weeks "
                    "or months if exterior changes are visible."
                ),
                "applies_to": "ADUs, exterior alterations, solar/roofing visibility, and historic parcels",
                "source": "Local historic preservation overlay rules",
            }
        )

    if state_upper == "CA" and city_key in CALIFORNIA_VHFHSZ_CITIES:
        notes.append(
            {
                "title": "Local VHFHSZ risk flag",
                "note": (
                    f"{city or 'This California city'} may include Very High Fire Hazard Severity Zone parcels. "
                    "Verify the address on the Cal Fire VHFHSZ map before quoting exterior materials or ADU/solar scope."
                ),
                "applies_to": "Address-specific wildfire overlay check",
                "source": CALIFORNIA_VHFHSZ_URL,
            }
        )

    return notes
