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

# State pack registry last updated 2026-04-27.
# States included: AZ, CA, CO, FL, GA, IL, NC, NY, TX, WA.
STATE_PACKS = {
    "AZ": {
        "name": "Arizona expert pack",
        "expert_notes": [
            {
                "title": "ARS 9-835 / 9-836 permit shot clock",
                "note": "Arizona municipalities must publish and follow statutory licensing time frames for building permits, broken into an administrative completeness review and a substantive review. Track the published clock from your jurisdiction — exceeding the substantive review time frame triggers refund/penalty consequences under ARS 9-835.",
                "applies_to": "All municipal residential and commercial permit applications",
                "source": "https://www.azleg.gov/ars/9/00835.htm"
            },
            {
                "title": "ROC license required over $1,000 (residential vs. commercial split)",
                "note": "The Arizona Registrar of Contractors issues separate licenses for commercial, residential, and dual scopes. Any contracted work over $1,000 (or any job requiring a permit) must be performed under the correct ROC classification — pulling a permit with the wrong class (e.g., residential-only license on a commercial tenant improvement) will get the application kicked back.",
                "applies_to": "All trades pulling permits in Arizona",
                "source": "https://roc.az.gov/license-classifications"
            },
            {
                "title": "Phoenix 2024 PBCC adoption",
                "note": "On June 18, 2025, Phoenix City Council adopted the 2024 Phoenix Building Construction Code (PBCC), which references ICC A117 for ADA. Plans submitted to Phoenix must comply with the 2024 PBCC and its local amendments — do not assume the prior 2018-cycle code still applies.",
                "applies_to": "Permits in the City of Phoenix",
                "source": "https://www.phoenix.gov/administration/departments/pdd/tools-resources/codes-ordinance/building-code.html"
            },
            {
                "title": "Tucson / Pima County 2024 codes effective Jan 1, 2026",
                "note": "Tucson and Pima County jointly adopted updated building codes that took effect January 1, 2026. Submittals on or after that date in Tucson/Pima must follow the new code cycle; check effective-date language for permits in process at the rollover.",
                "applies_to": "Permits in Tucson and Pima County",
                "source": "https://www.tucsonaz.gov/Departments/Planning-Development-Services/PDSD-News/Updated-Building-Codes-Adopted-Effective-January-1"
            },
            {
                "title": "Phoenix AMA groundwater CAWS moratorium",
                "note": "Since June 2023, the Arizona Department of Water Resources has stopped issuing new groundwater-based Certificates of Assured Water Supply in the Phoenix AMA. New subdivisions relying on groundwater can stall at platting; verify the project sits on an existing CAWS, a designated provider, or qualifies under ADAWS before promising a permit timeline.",
                "applies_to": "New residential subdivisions and lot splits in the Phoenix Active Management Area",
                "source": "https://jmc-eng.com/the-loophole-in-arizonas-water-rules-bypassing-the-certificate-of-assured-water-supply/"
            },
            {
                "title": "ARS 9-468 solar permit standards",
                "note": "Arizona municipalities must adopt the standards in ARS 9-468 for issuing permits on certain solar energy devices. Cities cannot impose arbitrary restrictions beyond the statute — push back if a jurisdiction tries to add aesthetic or HOA-style review to a code-compliant rooftop PV permit.",
                "applies_to": "Residential and commercial solar permits",
                "source": "https://www.azleg.gov/ars/9/00468.htm"
            },
            {
                "title": "Post-wildfire flood overlay risk",
                "note": "Per the 2023 State Hazard Mitigation Plan, post-wildfire flooding is the greatest flood risk for many Arizona communities along the wildland-urban interface. Even outside FEMA SFHAs, parcels downstream of recent burn scars can trigger local floodplain or grading review — check the AHJ floodplain layer before promising a clean foundation submittal.",
                "applies_to": "Sites in or downstream of wildland-urban interface burn areas",
                "source": "https://dema.az.gov/sites/default/files/2023-11/SHMP_2023_Final.pdf"
            },
            {
                "title": "Maricopa County floodplain pre-check",
                "note": "Maricopa County maintains a GIS Floodplain Viewer showing currently designated floodplains. Pull the parcel on the viewer before submittal — a floodplain hit means a separate Floodplain Use Permit and elevation certificate workflow on top of the building permit.",
                "applies_to": "Permits on parcels within unincorporated Maricopa County or county-regulated floodplains",
                "source": "https://www.maricopa.gov/959/Floodplain-Information"
            }
        ]
    },
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
    },
    "CO": {
        "name": "Colorado expert pack",
        "expert_notes": [
            {
                "title": "Colorado state-issued electrical and plumbing permits",
                "note": "Electrical and plumbing permits in Colorado are issued at the state level by the DPO (Division of Professions and Occupations), not by the local building department. Registered Electrical or Plumbing Contractors can pull unlimited state permits. Local building permits are still required separately, so plan for parallel state + local intake.",
                "applies_to": "All residential electrical and plumbing scope statewide",
                "source": "https://dpo.colorado.gov/ElectricalPlumbingPermits"
            },
            {
                "title": "No statewide GC license — electrical and plumbing are state-licensed",
                "note": "Colorado has no statewide general contractor license; GC licensing is handled per-jurisdiction. However, electrical and plumbing contractors must hold a state license through the State Electrical Board / State Plumbing Board. A Colorado state electrical or plumbing license substitutes for a local supervisor certificate in jurisdictions like Denver.",
                "applies_to": "Contractor licensing for any residential project",
                "source": "https://www.procore.com/library/colorado-contractors-license"
            },
            {
                "title": "ADUs required in Subject Jurisdictions under HB24-1152",
                "note": "Under House Bill 24-1152, Subject Jurisdictions must allow at least one ADU on lots where single-unit detached dwellings are permitted, generally by June 30, 2025. The mandate does not apply to every Colorado municipality, so confirm whether the AHJ is a Subject Jurisdiction before relying on by-right ADU approval.",
                "applies_to": "ADU jobs in Subject Jurisdictions",
                "source": "https://dlg.colorado.gov/accessory-dwelling-units"
            },
            {
                "title": "Wildland-Urban Interface (WUI) Appendix K compliance",
                "note": "In WUI-adopted jurisdictions, IWUIC Appendix K requirements (ignition-resistant exterior wall coverings, decking, eaves, soffits, exterior doors, etc.) must be reviewed and approved by the fire code official prior to permit issuance and again prior to framing inspection. Build the fire-official sign-off into the schedule — it is a hard gate, not a parallel review.",
                "applies_to": "New construction, additions, and reroofs in WUI overlay zones",
                "source": "https://www.coswildfireready.org/uploads/b/2721af80-1003-11ec-bf67-0310173bc1c8/1f09bdf0-6a1e-11ef-99a9-abf65789ea5c.pdf"
            },
            {
                "title": "Model Electric Ready and Solar Ready Code",
                "note": "Colorado's Model Electric Ready and Solar Ready Code requires new buildings to be pre-wired/pre-plumbed for EV charging, rooftop solar PV/thermal, and high-efficiency electric appliances. Solar-ready zones must be at least 300 sq ft exclusive of mandatory access/setback areas required by the IFC. Jurisdictions adopting the 2021 IECC or later are required to also adopt this code.",
                "applies_to": "New residential construction and major additions",
                "source": "https://energyoffice.colorado.gov/building-energy-codes-toolkit"
            },
            {
                "title": "AHJ split — over 300 areas without active building departments",
                "note": "Colorado has over 300 governing bodies but not all have active building departments. In jurisdictions without one, the Colorado Department of Housing (DOH) oversees construction for HUD-code/factory-built homes. Roughly 60% of Colorado's 64 counties are largely unincorporated, so confirm whether the parcel falls under a city, county, regional building department, or DOH before submitting.",
                "applies_to": "Any project in unincorporated or small-jurisdiction Colorado",
                "source": "https://doh.colorado.gov/jurisdictions-without-building-departments-hud-code-homes"
            },
            {
                "title": "Denver residential trade-permit sequencing (May 22, 2025)",
                "note": "In Denver, beginning May 22, 2025, trade permit applications (electrical, plumbing, mechanical) for new construction and addition projects require an issued Residential Construction Permit before they can proceed. Submitting trades in parallel with the building permit will get them rejected — sequence the building permit issuance first.",
                "applies_to": "Denver new construction, additions, and ADUs",
                "source": "https://www.denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Community-Planning-and-Development/Plan-Review-Permits-and-Inspections/Single-Family-and-Duplex-Projects/ADU-Permits"
            },
            {
                "title": "Denver residential permit timelines approaching 180 days",
                "note": "Denver residential permit timelines for additions, ADUs, and major remodels are running close to 180 days in 2026. Set client expectations accordingly and front-load completeness review; submitting an incomplete package adds weeks per resubmittal cycle.",
                "applies_to": "Denver residential additions, ADUs, and major remodels",
                "source": "https://sdb-denver.com/2026/the-construction-industry/denver-permit-timelines-in-2026-what-homeowners-should-expect/"
            }
        ]
    },
    "FL": {
        "name": "Florida expert pack",
        "expert_notes": [
            {
                "title": "Florida 60-business-day plan review shot clock",
                "note": "Florida Statute 553.792 requires the local building department to approve, approve with conditions, or deny a complete and sufficient residential application within 30 business days (60 business days for certain applicant-selected plans reviewer scenarios). Missing the deadline can be grounds to escalate to the building official or seek fee refunds.",
                "applies_to": "All residential building permit applications",
                "source": "https://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&Search_String=&URL=0500-0599/0553/Sections/0553.792.html"
            },
            {
                "title": "Florida Building Code 8th Edition (2023) effective date",
                "note": "The Florida Building Code, 8th Edition (2023), became effective December 31, 2023. Submittals after that date must comply with the 8th Edition, including the 2023 Florida Energy Conservation Code (based on the 2021 IECC and ASHRAE 90.1-2019 with Florida amendments). Verify the AHJ has updated its checklists before submitting.",
                "applies_to": "All new permit submittals statewide",
                "source": "https://www.floridabuilding.org/"
            },
            {
                "title": "High-Velocity Hurricane Zone (HVHZ) — Miami-Dade and Broward",
                "note": "Projects in Miami-Dade and Broward counties fall under the HVHZ provisions of the Florida Building Code (Chapters 15, 16, and others). Roofing requires the HVHZ Uniform Roofing Permit Application, NOA-approved (Notice of Acceptance) products, and stricter wind-load and component testing than the rest of the state. Do not use standard FBC details outside HVHZ as substitutes.",
                "applies_to": "Miami-Dade and Broward County projects, especially roofing and exterior envelope",
                "source": "https://www.floridabuilding.org/fbc/thecode/2013_Code_Development/HVHZ/FBCB/Chapter_15_2010.htm"
            },
            {
                "title": "Notice of Commencement required before first inspection",
                "note": "Per Fla. Stat. 713.135, a Notice of Commencement must be recorded with the county clerk and posted on the jobsite before the first inspection on any project requiring a building permit. Exemptions exist for work under $2,500 and HVAC repair/replacement under $15,000 (Fla. Stat. 713.02(5)). Failing to record/post can void inspections and expose the owner to double-payment risk on liens.",
                "applies_to": "Permitted projects above the statutory thresholds",
                "source": "https://www.leg.state.fl.us/Statutes/index.cfm?App_mode=Display_Statute&URL=0700-0799/0713/Sections/0713.135.html"
            },
            {
                "title": "Certified vs. registered contractor — scope of work area",
                "note": "DBPR issues two tiers: a Certified license is valid statewide, while a Registered license is only valid in the specific local jurisdiction(s) where the contractor has met local competency requirements. Pulling permits outside your registered jurisdiction is unlicensed contracting. Confirm the qualifier's license tier matches the project location before bidding.",
                "applies_to": "All trades pulling permits in Florida (GC, electrical, HVAC, plumbing, roofing)",
                "source": "https://www.myfloridalicense.com/intentions2.asp?chBoard=true&boardid=06&SID="
            },
            {
                "title": "AHJ split — municipality vs. county jurisdiction",
                "note": "In Florida, a parcel's permitting authority is not always the county. Incorporated cities and towns typically run their own building department, while unincorporated parcels fall under the county. Using the wrong AHJ wastes intake fees and restarts the clock. Confirm jurisdiction via the city/county GIS or the local building department before drafting the application.",
                "applies_to": "Any project where city limits are ambiguous or near jurisdictional boundaries",
                "source": "https://www.elitepermits.com/find-the-municipality-jurisdiction-you-are-in/"
            },
            {
                "title": "Statewide ADU enabling law (SB 48 / 2026 session)",
                "note": "Recent state legislation requires Florida municipalities to allow ADUs in single-family residential zones, with statewide minimums permitting ADUs up to 1,000 sq ft and limiting discretionary review. Local zoning amendments are still rolling out — verify the AHJ's current ADU ordinance before promising ministerial review timelines to the homeowner.",
                "applies_to": "ADU and accessory dwelling projects in single-family zones",
                "source": "https://www.adufloridainfo.com/florida-adu-laws"
            },
            {
                "title": "Electrical contractor license renewal cycle",
                "note": "Florida certified and registered electrical contractor licenses expire August 31 of every even-numbered year. Permits cannot be pulled under a lapsed license, and inspectors will reject submittals tied to an expired qualifier. Confirm the qualifier's license is active in the DBPR portal before submitting electrical permits.",
                "applies_to": "Electrical permit applications and qualifier verification",
                "source": "https://www2.myfloridalicense.com/electrical-contractors/"
            }
        ]
    },
    "GA": {
        "name": "Georgia expert pack",
        "expert_notes": [
            {
                "title": "Georgia State Minimum Standard Codes — January 1, 2026 update",
                "note": "Georgia DCA adopted new mandatory State Minimum Standard Codes with Georgia Amendments effective January 1, 2026. Local governments enforcing these codes must adopt corresponding administrative procedures and penalties. Verify which code edition (and which Georgia Amendments) the AHJ is enforcing on the application date, since permits submitted near the transition can be reviewed under either edition.",
                "applies_to": "All permitted residential and commercial work statewide",
                "source": "https://dca.georgia.gov/announcement/2025-12-09/new-codes-jan-2026"
            },
            {
                "title": "Georgia Amendments override base I-Codes",
                "note": "Georgia adopts the building, gas, mechanical, plumbing, electrical, energy, and fire codes as the State Minimum Standard Codes, but always with Georgia Amendments. Never rely on the unamended IRC/IBC/IECC text — pull the current Georgia Amendments from DCA before scoping framing, energy, or mechanical details.",
                "applies_to": "All trades on residential and commercial permits",
                "source": "https://rules.sos.ga.gov/gac/110-11-1"
            },
            {
                "title": "Residential/Commercial GC license required above $2,500 thresholds",
                "note": "The State Licensing Board for Residential and Commercial General Contractors requires state licensure for most residential and commercial construction projects above statutory dollar thresholds. The contractor of record on a Georgia building permit generally must hold the appropriate Residential-Basic, Residential-Light Commercial, or General Contractor classification — homeowner-builder exemptions are narrow.",
                "applies_to": "General contractors pulling residential or commercial building permits",
                "source": "https://sos.ga.gov/state-licensing-board-residential-and-commercial-general-contractors"
            },
            {
                "title": "Separate state license for HVAC/Conditioned Air work",
                "note": "HVAC and refrigeration work is regulated by the Division of Conditioned Air Contractors under the State Construction Industry Licensing Board, separate from the GC license. The mechanical sub on the permit must be a licensed Conditioned Air Contractor (Class I under 175,000 BTU/h or Class II unrestricted) — a GC license alone does not authorize HVAC installation.",
                "applies_to": "Any job with HVAC, ductwork, or refrigeration scope",
                "source": "https://sos.ga.gov/georgia-state-board-conditioned-air-contractors"
            },
            {
                "title": "City vs. county AHJ split inside municipal limits",
                "note": "In Georgia, parcels inside city limits typically require a city building permit, and in some jurisdictions a county permit is also required for the same work. Confirm at intake whether the project sits inside an incorporated city — submitting only to the county (or only to the city) is a common cause of stop-work orders and re-inspection delays.",
                "applies_to": "Projects near or inside Georgia municipal boundaries",
                "source": "https://www.accg.org/docs/handbook/County%20and%20City%20Relations.pdf"
            },
            {
                "title": "ADU rules are local — no statewide ministerial shot clock",
                "note": "Georgia has no statewide ADU approval timeline equivalent to other states; ADU zoning, size limits, setbacks, and whether ADUs are allowed at all are set by each county or city. In smaller counties, permits may issue in a few weeks, while Atlanta review can stretch to several months. Pull the local ADU ordinance before scoping any accessory dwelling project.",
                "applies_to": "ADU and accessory dwelling projects",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-georgia"
            },
            {
                "title": "Atlanta plan review benchmark: 30–45 days, ~10–14 day rounds",
                "note": "Typical City of Atlanta building permit review runs roughly 30–45 days, with about 10–14 days for the initial review and similar turnarounds on each resubmittal. Schedule client expectations and trade mobilization around multiple review cycles rather than a single-pass approval.",
                "applies_to": "City of Atlanta residential and commercial permit applications",
                "source": "https://hoverarchitecture.com/building-permit-timelines-explained-how-long-does-it-really-take/"
            },
            {
                "title": "Coastal Marshlands Protection Act permit for tidal-area work",
                "note": "Any project that removes, fills, dredges, drains, or otherwise alters jurisdictional coastal marshlands or tidal water bodies (docks, bulkheads, marinas, shoreline stabilization) requires a Coastal Marshlands Protection Act permit and a jurisdictional determination from GA DNR Coastal Resources Division before local building permits will move. Contact CRD at (912) 264-7218 early — this is in addition to, not in place of, county/city permits.",
                "applies_to": "Coastal Georgia projects in or adjacent to marsh, tidal waters, or shoreline",
                "source": "https://coastalgadnr.org/MarshShore"
            }
        ]
    },
    "IL": {
        "name": "Illinois expert pack",
        "expert_notes": [
            {
                "title": "2024 IECC adoption effective 11/30/2025",
                "note": "Illinois adopted the 2024 International Energy Conservation Code with state amendments, effective November 30, 2025. All residential buildings must comply per 20 ILCS 3125. Permit applications submitted on or after that date must meet the 2024 IECC envelope, mechanical, and lighting requirements.",
                "applies_to": "New construction and alterations changing conditioned space",
                "source": "https://cdb.illinois.gov/business/codes/illinois-energy-codes/illinois-energy-conservation-code.html"
            },
            {
                "title": "Illinois Stretch Energy Code in home-rule municipalities",
                "note": "The 2023 Illinois Residential Stretch Energy Code (based on 2021 IECC with amendments establishing site energy index targets) took effect January 1, 2025 and applies in jurisdictions that have adopted it. Verify whether the AHJ enforces the base code or the stretch code before sizing envelope and HVAC.",
                "applies_to": "Residential new construction and major alterations in stretch-code jurisdictions",
                "source": "https://www.buildinghub.energy/2023-il-residential-stretch-code-guide"
            },
            {
                "title": "State plumbing license required statewide (055 prefix)",
                "note": "Under the Illinois Plumbing License Law (225 ILCS 320), IDPH licenses plumbers and plumbing contractors statewide. Municipalities such as Elgin require an Illinois Plumbing Contractor's License (055 prefix) on file before issuing a permit, and most AHJs will reject plumbing permit applications without it.",
                "applies_to": "All plumbing permits and rough-in inspections",
                "source": "https://elginil.gov/311/Contractor-Requirements"
            },
            {
                "title": "State roofing license + Cook County registration",
                "note": "Illinois law requires a state-licensed roofing contractor (IDFPR) for all roofing projects. Cook County imposes additional contractor registration on top of the state license, and many municipalities will not issue a roofing permit without proof of both.",
                "applies_to": "Residential and commercial roofing permits, especially in Cook County",
                "source": "https://www.advancedroofing.biz/blog/blog/illinois-roofing-permits-2025-when-do-you-need-one-for-repairs/"
            },
            {
                "title": "No statewide ADU law — rules are entirely local",
                "note": "Illinois has no statewide ADU statute. Size limits, setbacks, owner-occupancy requirements, permit fees, and review timelines are determined entirely by the municipality. Always confirm the local ordinance and whether a public hearing is required, which can add months to the timeline.",
                "applies_to": "ADU jobs anywhere in Illinois",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-illinois"
            },
            {
                "title": "Chicago ADU ordinance — citywide effective 4/1/2026",
                "note": "Chicago's citywide ADU ordinance becomes effective April 1, 2026, which is also the first day building permit applications for ADUs can be submitted under the new rules. Confirm parcel eligibility through the City of Chicago ADU Process portal before scoping work.",
                "applies_to": "ADU jobs within City of Chicago",
                "source": "https://www.chicago.gov/city/en/sites/additional-dwelling-units-ordinance/home/adu-process.html"
            },
            {
                "title": "Municipal vs. county zoning jurisdiction split",
                "note": "In Illinois, municipalities generally hold their own zoning and permit jurisdiction within corporate limits and are not subject to county zoning. Unincorporated parcels fall under county jurisdiction. Confirm corporate-limit status early — submitting to the wrong AHJ is a common cause of delay.",
                "applies_to": "Any project near a municipal boundary or in unincorporated area",
                "source": "https://extension.illinois.edu/energy/municipalities"
            },
            {
                "title": "IDNR Office of Water Resources floodplain permit",
                "note": "Development activity in FEMA-mapped floodplains on state-owned property requires an IDNR Office of Water Resources permit under Part 3710. Local floodplain overlay districts impose additional review on top of the local building permit, so flood-zone parcels typically need parallel state and local approvals.",
                "applies_to": "Construction in mapped floodplains or flood hazard overlay districts",
                "source": "https://dnr.illinois.gov/waterresources/permitprograms.html"
            }
        ]
    },
    "NC": {
        "name": "North Carolina expert pack",
        "expert_notes": [
            {
                "title": "NC 45-day building permit decision shot clock",
                "note": "Under G.S. 160D-1110.1, local governments must issue a building permit decision within 45 days of a complete application (extendable to 60 days only if an at-risk permit is issued). If the AHJ exceeds this window, you have statutory grounds to escalate.",
                "applies_to": "All residential and commercial building permit applications",
                "source": "https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_160D/GS_160D-1110.1.html"
            },
            {
                "title": "GC license required at $40,000 project threshold",
                "note": "By North Carolina law, a general contractor must hold an active NCLBGC license if the total project cost is $40,000 or higher. Verify license status on the NCLBGC site before submitting; permits will be rejected for unlicensed GCs on projects at or above this threshold.",
                "applies_to": "Any residential or commercial project with total cost ≥ $40,000",
                "source": "https://nclbgc.org/"
            },
            {
                "title": "Specialty trade licensing — plumbing, HVAC, electrical",
                "note": "Plumbing, heating, and fire sprinkler trades are licensed by the NC State Board of Examiners of Plumbing, Heating, and Fire Sprinkler Contractors; electrical contractors are licensed separately by NCBEEC. Homeowners may self-perform trade work on their own primary residence, but contractors performing these trades for others must hold the proper specialty license or the trade permit will not issue.",
                "applies_to": "Plumbing, HVAC, electrical, and fire sprinkler scopes performed by contractors",
                "source": "https://nclicensing.org/"
            },
            {
                "title": "HB 409 — at least one ADU must be allowed",
                "note": "House Bill 409, effective October 1, 2023, requires every local government in North Carolina to allow at least one ADU per detached single-family lot. A zoning compliance permit (often around $25 residential) is typically required before placement. Use this statute to push back if a jurisdiction's local ordinance attempts to ban ADUs outright.",
                "applies_to": "ADU jobs on detached single-family lots statewide",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-north-carolina"
            },
            {
                "title": "City vs. county AHJ split for zoning and building",
                "note": "In NC, permitting duties are frequently split: the municipality may handle zoning permits while the county handles building inspections (or vice versa). Confirm both jurisdictions before applying — applying only to the city when the county runs building inspections is a common cause of rejected or stalled submittals.",
                "applies_to": "Any project near city limits, ETJ boundaries, or in counties with split arrangements",
                "source": "https://canons.sog.unc.edu/blog/2018/09/04/administering-development-regulations-and-accounting-for-permitting-fees-2/"
            },
            {
                "title": "Permit choice and vested rights under G.S. 160D-108",
                "note": "Under G.S. 160D-108, an applicant may choose which version of a development regulation applies if rules change between application and decision (permit choice), and an issued development permit confers statutory vesting for the project. Cite this when a jurisdiction tries to apply a newly adopted code amendment to an in-flight application.",
                "applies_to": "Projects in review when a code or ordinance amendment is adopted mid-process",
                "source": "https://www.ncleg.gov/EnactedLegislation/Statutes/PDF/BySection/Chapter_160D/GS_160D-108.pdf"
            },
            {
                "title": "Coastal and flood plain construction (NCRC Chapter 46)",
                "note": "Chapter 46 of the NC Residential Code sets coastal and flood plain construction standards, and Appendix G of the NC Building Code requires the floodplain administrator to maintain a permanent record of all permits in flood hazard areas with supporting elevation/dry-floodproofing certifications. Coastal jobs typically also require higher wind-load design (commonly 110–150 mph zones) — verify the parcel's design wind speed and flood zone before sealing plans.",
                "applies_to": "Coastal counties and any parcel within a designated flood hazard area",
                "source": "https://codes.iccsafe.org/content/NCRC2018/chapter-46-coastal-and-flood-plain-construction-standards"
            },
            {
                "title": "2024 NC State Building Code adoption / effective dates",
                "note": "The 2024 North Carolina State Building Code collection, with amendments adopted by the Building Code Council, has staggered effective dates beginning July 1, 2025. Confirm which code edition (2018 vs. 2024) governs your application date — permit choice under 160D-108 may let you elect the prior edition if your application predates the new effective date.",
                "applies_to": "All permit submittals during the 2018→2024 code transition window",
                "source": "https://nclicensing.org/wp-content/uploads/2026/02/2026-02-15-Code-Update.pdf"
            }
        ]
    },
    "NY": {
        "name": "New York expert pack",
        "expert_notes": [
            {
                "title": "2025 NYS Uniform Code and Energy Code effective date",
                "note": "Beginning December 31, 2025, building permit applications must comply with the 2025 Energy Conservation Construction Code of New York State and the 2025 NYS Uniform Code. There was a transition period from October 1 to December 30, 2025 during which applicants could elect either edition — applications submitted now must use the 2025 codes. Confirm which code edition the AHJ is enforcing on the application date, since vesting depends on submittal completeness.",
                "applies_to": "All new construction, additions, and alterations statewide outside NYC",
                "source": "https://dos.ny.gov/notice-adoption"
            },
            {
                "title": "NYC has its own code and enforcement track separate from NYS",
                "note": "Due to its population, New York City is authorized to adopt building codes separate from the rest of the state. NYC enforces the 2025 NYC Energy Conservation Code (2025 NYCECC) and 2025 NYC ASHRAE 90.1 starting March 30, 2026 (Local Law 47 of 2026). Do not assume NYS Uniform Code or NYS Energy Code applies inside the five boroughs — file under NYC DOB rules and the NYC code editions instead.",
                "applies_to": "Projects within the five boroughs of New York City",
                "source": "https://www.nyc.gov/site/buildings/codes/energy-conservation-code.page"
            },
            {
                "title": "No statewide general contractor license — licensing is municipal",
                "note": "New York does not issue a statewide general contractor or home improvement contractor license. Licensing is handled at the city or county level, so a contractor licensed in one jurisdiction is not automatically authorized in the next town over. In NYC, a Home Improvement Contractor (HIC) license from DCWP is required for construction, repair, remodeling, or other home improvement work on any residential land or building.",
                "applies_to": "All residential remodel, repair, and home improvement work",
                "source": "https://www.nyc.gov/site/dca/businesses/license-checklist-home-improvement-contractor.page"
            },
            {
                "title": "Local floodplain development permit required in addition to building permit",
                "note": "Private development in mapped floodplains is subject to a local floodplain development permit issued by the municipality, separate from the standard building permit. New York State Environmental Conservation Law requires local laws to regulate development in flood hazard areas. Verify the parcel's FEMA flood zone before pricing — elevation, flood-resistant materials, and a separate floodplain permit application can add weeks to the schedule.",
                "applies_to": "Any construction or substantial improvement in a mapped flood hazard area",
                "source": "https://dec.ny.gov/environmental-protection/water/dam-safety-coastal-flood-protection/floodplain-management"
            },
            {
                "title": "ADU timelines run 3–12 months; two-family + ADU exempt from MDL",
                "note": "Plan for three to twelve months from ADU permit application to certificate of occupancy in New York; simple basement conversions sit at the shorter end. Two-family homes adding a fire-separated attached or detached ADU are exempt from New York State's Multiple Dwelling Law (MDL), which avoids triggering MDL classification and its much heavier requirements. There is no statewide ministerial shot clock equivalent to other states — local zoning controls.",
                "applies_to": "ADU projects on one- and two-family residential properties",
                "source": "https://housing.hpd.nyc.gov/adu/guidebook"
            },
            {
                "title": "NYC standard renovation permit review runs 3–6 weeks",
                "note": "For larger interior renovations in NYC, DOB plan review timelines typically range from three to six weeks depending on plan examiner workload and any required agency sign-offs (Landmarks, HPD, FDNY). Build this into customer schedules and avoid promising start dates that assume same-week pull. Incomplete submittals are the most common cause of objections that reset the clock.",
                "applies_to": "Interior renovation and alteration permits filed with NYC DOB",
                "source": "https://mdjacksonlaw.com/blog/how-long-do-permits-take-in-new-york-city/"
            },
            {
                "title": "Section 404 wetland fills — Nationwide Permit 18 PCN threshold",
                "note": "For wetland losses of 1/10-acre or less, USACE New York District may require a pre-construction notification (PCN) under Nationwide Permit 18 and will determine authorization on a case-by-case basis. Any fill, grading, or discharge into a federally regulated wetland — even a small one — needs to be screened for NWP coverage before site work, in addition to NYSDEC freshwater/tidal wetland permits where applicable.",
                "applies_to": "Sites with mapped or suspected federal wetlands or waters of the U.S.",
                "source": "https://www.nan.usace.army.mil/Portals/37/docs/regulatory/Nationwide%20Permit/NWP2020/NWP%2018.pdf?ver=2020-03-10-162119-980"
            },
            {
                "title": "NYC HVAC trade licensing varies by system type",
                "note": "In New York City there are three distinct HVAC-related licenses, and which one is required depends on the type of system being installed or serviced. A contractor cannot pull HVAC work permits in NYC under a generic out-of-city license — the licensed individual must hold the correct NYC trade license for the scope. Verify the license category before bidding mechanical scope inside the five boroughs.",
                "applies_to": "HVAC and mechanical scope on NYC projects",
                "source": "https://www.servicetitan.com/licensing/hvac/new-york"
            }
        ]
    },
    "TX": {
        "name": "Texas expert pack",
        "expert_notes": [
            {
                "title": "45-day municipal permit review shot clock",
                "note": "Texas state law requires cities to review residential and commercial building permit applications within 45 days. If a city exceeds this window on a complete application, you have grounds to escalate or push for approval.",
                "applies_to": "Residential and commercial permits filed with Texas municipalities",
                "source": "https://www.newwestern.com/blog/new-law-on-texas-building-permit-process-could-help-your-project-move-faster/"
            },
            {
                "title": "Plumbing license is regulated by TSBPE, not TDLR",
                "note": "Plumbing in Texas is licensed by the Texas State Board of Plumbing Examiners (TSBPE) — separate from TDLR which handles electrical and HVAC. Journeyman or Master plumbing license is required before bidding or performing plumbing work, and licenses renew annually.",
                "applies_to": "Any job with plumbing scope in Texas",
                "source": "https://tsbpe.texas.gov/license-types/"
            },
            {
                "title": "TDLR licensing for electrical and HVAC contractors",
                "note": "Electrical contractors must hold a TDLR Electrical Contractor license, and HVAC work requires a TDLR Air Conditioning and Refrigeration Contractor license ($115 application fee). These are separate licenses from plumbing and must be in place before pulling trade permits.",
                "applies_to": "Electrical and HVAC scopes on Texas projects",
                "source": "https://www.tdlr.texas.gov/acr/contractor-apply.htm"
            },
            {
                "title": "WPI-8 windstorm certification for First Tier coastal counties",
                "note": "Structures in Texas' designated catastrophe area (First Tier coastal counties) must be inspected during construction and certified as meeting windstorm building code requirements to qualify for a TWIA insurance policy. Missing the inspection windows during construction means costly retrofits or denial of certification.",
                "applies_to": "New construction and additions in Texas First Tier coastal counties",
                "source": "https://www.tdi.texas.gov/wind/generalquestio.html"
            },
            {
                "title": "County jurisdiction in unincorporated areas and ETJ rules",
                "note": "If the project is in an unincorporated area of a county, permits are pulled from the county rather than a municipality. A city's extraterritorial jurisdiction (ETJ) is the unincorporated area contiguous to its corporate boundaries — confirm whether the parcel is in city limits, county, or ETJ before assuming which AHJ reviews the permit.",
                "applies_to": "Projects outside city limits or in ETJ areas",
                "source": "https://statutes.capitol.texas.gov/Docs/LG/htm/LG.42.htm"
            },
            {
                "title": "No state GC license — but trade licenses are mandatory",
                "note": "Texas does not require a state-level license for general contractors, home improvement, or handyman services. However, the trade subs (electrical, HVAC, plumbing) must be individually licensed, and local cities may impose registration or permit requirements on the GC.",
                "applies_to": "General contractor and remodel scopes statewide",
                "source": "https://www.procore.com/library/texas-contractors-license"
            },
            {
                "title": "Co-op and muni utility permit requirements differ from investor-owned utilities",
                "note": "Some cities and counties served by electric cooperatives require a permit number to be issued before new electric service can be established. Munis and co-ops chose whether to opt into competitive retail markets in 2002, so service connection rules and permit coordination vary by territory.",
                "applies_to": "New electric service in co-op or muni utility territories",
                "source": "https://www.samhouston.net/member-services/permit-requirements/"
            },
            {
                "title": "Energy code is set by SECO at the state level",
                "note": "The State Energy Conservation Office (SECO) at the Texas Comptroller adopts the energy codes that apply to single-family residential buildings statewide. Texas has not updated its baseline energy code since 2015, but SECO can adopt newer IECC editions with amendments — check current SECO adoption before assuming an older code applies.",
                "applies_to": "New residential construction and additions affecting conditioned space",
                "source": "https://comptroller.texas.gov/programs/seco/code/adoption.php"
            }
        ]
    },
    "WA": {
        "name": "Washington expert pack",
        "expert_notes": [
            {
                "title": "Washington permit review shot clocks (RCW 36.70B)",
                "note": "Local jurisdictions in Washington must issue a final decision on project permits within 65 days when no public notice of application is required, 100 days when public notice is required, and 170 days when an open-record public hearing is required. The clock pauses for applicant-caused delays and incomplete submittals; track completeness determinations carefully to preserve your timeline.",
                "applies_to": "All residential project permits subject to RCW 36.70B review",
                "source": "https://mrsc.org/explore-topics/planning/administration/permit-review"
            },
            {
                "title": "HB 1337 ADU statewide standards",
                "note": "HB 1337 (Laws of 2023) preempts many local ADU restrictions in cities and counties planning under the GMA, requiring allowance of two ADUs per lot in most residential zones, capping fees, and limiting owner-occupancy and parking requirements. Confirm whether the local code has been updated to comply before assuming legacy local restrictions still apply.",
                "applies_to": "ADU and DADU projects in GMA-planning jurisdictions",
                "source": "https://www.ezview.wa.gov/Portals/_1976/Documents/adu-examples/Commerce_ADU_Guidance_0624.pdf"
            },
            {
                "title": "2021 WSEC-R effective March 15, 2024",
                "note": "The 2021 Washington State Energy Code – Residential Provisions took effect March 15, 2024 statewide and applies to new one- and two-family dwellings, townhouses, additions, and alterations. Permit applications submitted after that date must comply with the current WSEC-R credit/path requirements, including envelope, HVAC, and water-heating provisions.",
                "applies_to": "New residential construction, additions, and alterations affecting conditioned space",
                "source": "https://sbcc.wa.gov/state-codes-regulations-guidelines/state-building-code/energy-code"
            },
            {
                "title": "L&I issues electrical permits — not the city",
                "note": "In Washington, electrical permits and inspections are handled by the Department of Labor & Industries statewide, except in a handful of cities with their own approved electrical programs (e.g., Seattle, Tacoma, Spokane). Electrical permits must be purchased by a licensed electrical contractor or by an owner-occupant doing the work themselves; general contractors cannot pull electrical permits on behalf of subs.",
                "applies_to": "Any residential job with electrical scope outside city-run electrical programs",
                "source": "https://www.lni.wa.gov/licensing-permits/electrical/electrical-permits-fees-and-inspections/"
            },
            {
                "title": "Contractor registration and trade licensing through L&I",
                "note": "All construction contractors operating in Washington must be registered with L&I and carry a bond and liability insurance; specialty trades like electrical and plumbing require separate L&I licenses on top of contractor registration. Pulling a permit or signing a contract while unregistered exposes the contractor to civil penalties and lien-rights forfeiture.",
                "applies_to": "All contractors and specialty trades performing work in Washington",
                "source": "https://lni.wa.gov/licensing-permits/contractors/register-as-a-contractor/"
            },
            {
                "title": "Critical areas and Shoreline Management Act review",
                "note": "Parcels containing wetlands, streams, steep slopes, landslide hazards, or located within 200 feet of a shoreline of the state are subject to local Critical Areas Ordinances and the Shoreline Management Act, often requiring a critical area study, mitigation plan, and a separate Shoreline Substantial Development, Conditional Use, or Variance permit reviewed by Ecology. These reviews run in parallel with the building permit and can add months — screen the parcel early.",
                "applies_to": "Residential work near wetlands, streams, steep slopes, or shorelines",
                "source": "https://apps.ecology.wa.gov/publications/documents/1706029.pdf"
            },
            {
                "title": "Permits are issued locally, not by the state",
                "note": "Washington has no statewide permit portal — building permits are issued by the individual city if the parcel is incorporated, or by the county if unincorporated. A property is generally under one AHJ or the other, not both; verify incorporation status and the correct department before submitting, since misrouted applications restart intake.",
                "applies_to": "Confirming the correct AHJ before any permit submittal",
                "source": "https://www.permitflow.com/state/washington"
            },
            {
                "title": "Average WA permit timeline runs months, not weeks",
                "note": "Industry data shows the average building permit approval timeline in Washington is roughly 6.5 months, materially longer than statutory shot clocks suggest because of completeness cycles, revisions, and parallel reviews (utility, fire, health). Build that lead time into customer contracts and avoid promising occupancy dates tied to optimistic permit windows.",
                "applies_to": "Project scheduling and customer expectation-setting on residential builds",
                "source": "https://www.mbaks.com/docs/default-source/documents/advocacy/issue-briefs/permitting-issue-brief.pdf"
            }
        ]
    },
    "PA": {
        "name": "Pennsylvania expert pack",
        "expert_notes": [
            {
                "title": "2021 I-Codes adoption effective January 1, 2026",
                "note": "Pennsylvania's Uniform Construction Code adopted the 2021 International Code Council (ICC) family of codes effective January 1, 2026, replacing the 2018 ICC series. Plan reviews and inspections on new applications must comply with the 2021 IRC, IBC, and IECC, and energy-envelope requirements have tightened. Confirm which code edition the AHJ is enforcing on the application date.",
                "applies_to": "All new residential and commercial construction permits in PA",
                "source": "https://www.cohenseglias.com/construction-law-now/pennsylvania-2026-building-code-update/"
            },
            {
                "title": "UCC inspector certification required for plan review and inspections",
                "note": "Anyone who administers or enforces the UCC, inspects construction work, or performs plan reviews in Pennsylvania must hold the appropriate UCC certification (e.g., B1 Residential Building Inspector, E1 Residential Electrical Inspector, P1 Residential Plumbing Inspector). Verify your third-party agency or municipal inspector holds the matching category before scheduling inspections to avoid rejected sign-offs.",
                "applies_to": "All UCC-regulated trade inspections (building, electrical, plumbing, mechanical)",
                "source": "https://www.pa.gov/content/dam/copapwp-pagov/en/dli/documents/individuals/labor-management-relations/bois/documents/ucc/ucc_certification_booklet.pdf"
            },
            {
                "title": "Home Improvement Contractor (HIC) registration with PA Attorney General",
                "note": "Under the Home Improvement Consumer Protection Act, most home improvement contractors performing residential work in Pennsylvania must register with the Attorney General's Office. The registration fee is $100 every two years, and contractors must show proof of Commercial General Liability Insurance. The HIC number must appear on all contracts, estimates, and advertisements; unregistered contracts are unenforceable against the homeowner.",
                "applies_to": "Residential home improvement contracts over $500 in PA",
                "source": "https://hic.attorneygeneral.gov/"
            },
            {
                "title": "Municipal vs. county AHJ split \u2014 ~90% local enforcement",
                "note": "Pennsylvania has no statewide building department; roughly 90% of PA municipalities handle their own UCC enforcement, often through third-party agencies, while opt-out municipalities default to L&I for commercial only. Always confirm the specific borough/township AHJ before submitting, since neighboring municipalities can use different code amendments, fee schedules, and third-party reviewers.",
                "applies_to": "All PA permit submittals \u2014 confirming jurisdiction before applying",
                "source": "https://davisbucco.com/why-do-90-of-pa-municipalities-handle-construction-code-enforcement/"
            },
            {
                "title": "Floodplain development permit in Special Flood Hazard Areas",
                "note": "Any construction, substantial improvement, or filling within a Special Flood Hazard Area (SFHA) requires a separate Floodplain Development Permit in addition to the building permit, per PA Floodplain Management Act and local floodplain ordinances. Substantial improvements (>50% of pre-improvement market value) trigger full elevation/flood-proofing compliance. Check the parcel against FEMA FIRM maps before quoting work.",
                "applies_to": "Projects within FEMA-mapped SFHAs or local floodplain overlay districts",
                "source": "https://www.pa.gov/content/dam/copapwp-pagov/en/pema/documents/floodplain-management/pema%20floodplain%20development%20guide.pdf"
            },
            {
                "title": "Act 537 sewage planning module for on-lot or new sewage service",
                "note": "The Pennsylvania Sewage Facilities Act (Act 537) requires DEP-approved sewage planning before a permit can be issued for new on-lot disposal systems or any project that creates new sewage flows not covered by an existing municipal Act 537 plan. Local sewage enforcement officers (SEOs) issue on-lot permits under uniform standards. Add Act 537 module review time to ADU, new dwelling, and rural addition schedules.",
                "applies_to": "New dwellings, ADUs, and additions creating new sewage flows (especially on-lot/septic)",
                "source": "https://www.pa.gov/agencies/dep/programs-and-services/water/clean-water/wastewater-management/act-537-sewage-facilities-program"
            },
            {
                "title": "Act 167 stormwater management compliance",
                "note": "Once a county or watershed Act 167 Stormwater Management Plan is adopted and approved, the location, design, and construction of stormwater management systems and obstructions on any regulated site must conform to that plan. Many PA municipalities require an Act 167 stormwater review/permit for impervious surface additions \u2014 confirm thresholds (often as low as ~500\u20131,000 sq ft of new impervious) with the local AHJ.",
                "applies_to": "Projects adding impervious area (driveways, additions, ADUs, accessory structures)",
                "source": "https://www.pa.gov/agencies/dep/programs-and-services/water/clean-water/stormwater-management/act-167"
            },
            {
                "title": "No statewide general contractor license \u2014 local trade licenses still apply",
                "note": "Pennsylvania does not issue a state-level general contractor's license, but individual cities (notably Philadelphia and Pittsburgh) require their own contractor licenses, and many municipalities license plumbing and electrical trades locally. HIC registration with the AG is separate from and does not substitute for these municipal trade licenses. Verify both before pulling permits.",
                "applies_to": "Out-of-area contractors working in PA cities with local licensing (e.g., Philadelphia, Pittsburgh)",
                "source": "https://gaslampinsurance.com/how-to-get-a-contractors-license-in-pennsylvania-a-step-by-step-guide-to-obtaining-your-pa-contractor-license/"
            }
        ]
    },
    "OH": {
        "name": "Ohio expert pack",
        "expert_notes": [
            {
                "title": "Ohio 30-day permit review shot clock",
                "note": "Ohio law requires the building department to review your permit application within 30 days of receipt. If a jurisdiction blows past this without action or written deficiency, you have grounds to escalate to the certified department or the Board of Building Standards.",
                "applies_to": "All residential and commercial building permit applications in Ohio",
                "source": "https://www.cincinnati-oh.gov/buildings/building-permit-forms-applications/permit-guide/permit-review-process/"
            },
            {
                "title": "2024 Ohio code adoption \u2014 RCO and energy code change",
                "note": "Effective April 15, 2024, Ohio enforces the 2019 Residential Code of Ohio with April 2024 Amendments and the 2024 Ohio Building Code (based on IBC 2021). Energy provisions jumped from the 2012 to the 2021 IECC / ASHRAE 90.1-2019 baseline \u2014 older plan sets and prior-cycle energy calcs will be rejected.",
                "applies_to": "New construction, additions, and alterations submitted after April 15, 2024",
                "source": "https://rosscountybuilding.com/docs/News/CodeChanges.pdf"
            },
            {
                "title": "OCILB licensing required for electrical, HVAC, plumbing, hydronics, refrigeration",
                "note": "The Ohio Construction Industry Licensing Board (OCILB) licenses five specialty trades \u2014 electrical, HVAC, plumbing, hydronics, and refrigeration \u2014 for commercial work statewide. Residential work is not state-licensed but most municipalities require local registration; pull the OCILB license number into the permit application or it will be returned.",
                "applies_to": "Commercial trade permits and any jurisdiction that requires OCILB credentials on the application",
                "source": "https://com.ohio.gov/licensing-and-registration/construction-inspection-and-maintenance/contractor-licensing"
            },
            {
                "title": "Municipal vs county AHJ split \u2014 confirm certified department",
                "note": "Ohio building permits are issued by whichever municipal, township, or county building department is certified by the Board of Building Standards under OAC 4101:7-2-01 for the parcel. Many counties (e.g., Hamilton) require the local jurisdiction to issue the zoning certificate before the county will accept the building permit \u2014 confirm which department has jurisdiction before submitting.",
                "applies_to": "Any project where city and county boundaries overlap or the local township is non-certified",
                "source": "https://up.codes/s/building-department-jurisdictional-limitations"
            },
            {
                "title": "Lake Erie Coastal Erosion Area (CEA) Shore Structure Permit",
                "note": "A Shore Structure Permit from ODNR is required before constructing a beach, groin, revetment, seawall, bulkhead, breakwater, pier, or jetty along Ohio's Lake Erie shore. A separate CEA permit applies to any permanent structure located within the designated Coastal Erosion Area \u2014 pull this in parallel with the local building permit, not after.",
                "applies_to": "Lakefront construction in Lake, Ashtabula, Cuyahoga, Lorain, Erie, Ottawa, and Lucas counties",
                "source": "https://ohiodnr.gov/wps/portal/gov/odnr/buy-and-apply/regulatory-permits/lake-erie-land-and-water-permits/shore-structure-permit"
            },
            {
                "title": "Floodplain overlay development permit",
                "note": "In Ohio communities participating in the NFIP, it is unlawful to begin construction, filling, grading, or alteration in a designated Special Flood Hazard Area without a floodplain development permit from the local floodplain administrator. This is a separate approval from the building permit and is commonly missed on additions, detached structures, and grading work near streams.",
                "applies_to": "Any work within a FEMA SFHA or locally-mapped floodplain overlay district",
                "source": "https://www.eriecounty.oh.gov/Downloads/2022%20Revised%20Erie%20County%20Flood%20Plain%20Regulations.pdf?v=-102"
            },
            {
                "title": "ADUs are governed locally \u2014 no statewide ADU statute",
                "note": "Ohio has no statewide ADU enabling law or ministerial shot clock; whether an ADU is permitted, and the lot-size, owner-occupancy, and parking rules, are set by each municipality or township zoning code. Verify the local zoning text and obtain a zoning certificate before designing \u2014 denials are common in single-family-only districts.",
                "applies_to": "Detached and attached ADU / accessory dwelling unit projects",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-ohio"
            }
        ]
    },
    "MI": {
        "name": "Michigan expert pack",
        "expert_notes": [
            {
                "title": "Stille-DeRossett-Hale single state construction code preempts local code variations",
                "note": "Michigan operates under the Stille-DeRossett-Hale Single State Construction Code Act, meaning the Michigan Building Code, Michigan Residential Code, Michigan Energy Code, Michigan Electrical Code, and Michigan Rehabilitation Code apply uniformly statewide. Local jurisdictions enforce but cannot weaken these codes \u2014 confirm which jurisdiction is the enforcing agency before submitting plans.",
                "applies_to": "All residential and commercial permit work statewide",
                "source": "https://ars.apps.lara.state.mi.us/AdminCode/DownloadAdminCodeFile?FileName=R%20408.30500%20%20to%20408.30547g.pdf&ReturnHTML=True"
            },
            {
                "title": "2021 Michigan Energy Code effective April 22, 2025",
                "note": "Michigan adopted the 2021 IECC and ASHRAE 90.1-2019 with amendments, with the updated commercial energy code enforcement starting April 22, 2025. Plans submitted under prior code assumptions may fail review \u2014 confirm insulation, fenestration, and mechanical system specs against the current code edition before submittal.",
                "applies_to": "New construction, additions, and alterations affecting conditioned space",
                "source": "https://www.energycodes.gov/status/states/michigan"
            },
            {
                "title": "Permit expiration: work must begin within 180 days",
                "note": "Under the Michigan Building Code, work must typically begin within 180 days (6 months) of permit issuance, or the permit becomes void. Permits also lapse if work is suspended or abandoned for 180 days. Schedule the first inspection promptly after issuance to vest the permit.",
                "applies_to": "All issued building permits",
                "source": "http://www.constructionconcept.net/permit-deadlines-and-expiration-rules-understanding-michigan-building-code/"
            },
            {
                "title": "Trade licensing required for plumbing, electrical, and mechanical permits",
                "note": "Plumbing permits require a licensed plumbing contractor (or a homeowner installing on their own occupied dwelling). HVAC contractors require three years of experience, a passed exam, and state licensure. General work over $600 requires a residential builder or maintenance & alteration contractor license \u2014 unlicensed work is grounds for permit denial and stop-work orders.",
                "applies_to": "All plumbing, electrical, mechanical, and general contracting permit applications",
                "source": "https://www.michigan.gov/lara/bureau-list/bcc/sections/permit-section/permits/plumbing-permit-information"
            },
            {
                "title": "EGLE permits required for floodplain, wetland, and inland-lakes work",
                "note": "Construction in regulated floodplains, wetlands, inland lakes, or streams requires a separate permit from the Michigan Department of Environment, Great Lakes, and Energy (EGLE) under Part 31 and related authorities. New residential construction is prohibited in the floodway. Confirm EGLE jurisdiction before local building permit application \u2014 local permits are commonly conditioned on EGLE approval.",
                "applies_to": "Projects on or near floodplains, wetlands, inland lakes, streams, or Great Lakes shoreline",
                "source": "https://www.michigan.gov/egle/about/organization/water-resources/wetlands/permit-categories"
            },
            {
                "title": "Critical Dune Area permit required along Great Lakes shoreline",
                "note": "Approximately 29% of Michigan's sand dunes are designated Critical Dune Areas. Any construction or improvement in a CDA requires an EGLE permit and typically a written Vegetative Assurance Plan. This is a frequent gotcha for shoreline parcels in counties like Oceana, Mason, Berrien, and Leelanau.",
                "applies_to": "Construction on parcels within designated Critical Dune Areas along the Great Lakes",
                "source": "https://www.michigan.gov/egle/about/organization/water-resources/sand-dunes/critical-dunes"
            },
            {
                "title": "ADU rules are local, not state \u2014 verify township zoning first",
                "note": "Michigan has no statewide ADU shot clock or by-right ADU statute; ADU size, setbacks, parking, and permitting are controlled by local zoning at the township, city, or village level. Confirm the property's zoning district allows ADUs (and whether owner-occupancy is required) before quoting timeline or fees.",
                "applies_to": "ADU and accessory structure projects",
                "source": "https://www.zookcabins.com/regulations/michigan-adus"
            },
            {
                "title": "Three-tier jurisdiction: confirm enforcing agency before submittal",
                "note": "Michigan has three levels of jurisdiction (state, county, and municipal) and the enforcing agency for building, electrical, mechanical, and plumbing can differ within the same parcel. Local zoning ordinances commonly require zoning approval before or concurrent with building permit issuance \u2014 sequence permits correctly to avoid resubmittal.",
                "applies_to": "All projects \u2014 verify AHJ split between state, county, and municipal authorities",
                "source": "https://www.michigan.gov/-/media/Project/Websites/lara/bcc-media/Folder5/Statewide_Jurisdiction_List.pdf?rev=1cc0331538974c218c8135bc99e73d70"
            }
        ]
    },
    "NJ": {
        "name": "New Jersey expert pack",
        "expert_notes": [
            {
                "title": "20-working-day permit review under the UCC",
                "note": "New Jersey's Uniform Construction Code requires the local construction official to grant or deny a complete permit application within 20 business days of submission. Track the clock from the date your application is logged complete; if it lapses, escalate to the construction official and the DCA Division of Codes and Standards.",
                "applies_to": "All UCC construction permits (building, electrical, plumbing, fire, mechanical)",
                "source": "https://www.facebook.com/groups/253700335754858/posts/1203657674092448/"
            },
            {
                "title": "Home Improvement Contractor registration is mandatory",
                "note": "Under the NJ Contractor's Registration Act, all home improvement contractors must register annually with the Division of Consumer Affairs and display the registration number on contracts, ads, and permit applications. Building subcode forms include a field for the HIC registration number or exemption reason \u2014 leaving it blank is a common rejection trigger.",
                "applies_to": "Residential remodels, additions, and alterations performed by a contractor",
                "source": "https://www.nj.gov/state/bac/assets/pdf/quick-start/home-improvement-contractor-2019-09-R1.pdf"
            },
            {
                "title": "Separate state HVACR contractor license required",
                "note": "HVAC and refrigeration work in New Jersey must be performed under a license issued by the State Board of Examiners of Heating, Ventilating, Air Conditioning and Refrigeration Contractors. HIC registration alone does not authorize HVACR work \u2014 the licensed contractor's number must appear on the mechanical subcode application.",
                "applies_to": "HVAC, ventilation, and refrigeration scopes",
                "source": "https://www.njconsumeraffairs.gov/hvacr"
            },
            {
                "title": "Current energy subcode: IECC 2021 residential / ASHRAE 90.1-2019 commercial",
                "note": "NJAC 5:23-3.18 adopts the 2021 IECC for low-rise residential and ASHRAE 90.1-2019 for commercial and high-rise residential. Plans must include compliance documentation (REScheck/COMcheck or prescriptive paths) matching these editions; older energy code submittals are routinely rejected at intake.",
                "applies_to": "New construction, additions, and conditioned-space alterations",
                "source": "https://www.nj.gov/dca/codes/codreg/current.shtml"
            },
            {
                "title": "CAFRA, Waterfront Development, and Pinelands overlays",
                "note": "Projects in the Coastal Area Facility Review Act zone, tidal waterfront, or Pinelands Area require NJDEP authorization in addition to the municipal UCC permit. CAFRA individual permit applications must be submitted electronically through njdeponline.com, and overlay approval typically must be in hand before the construction permit is released.",
                "applies_to": "Coastal, tidal waterfront, and Pinelands-area parcels",
                "source": "https://dep.nj.gov/wp-content/uploads/wlm/downloads/caf/cp_011.pdf"
            },
            {
                "title": "Flood Hazard and Freshwater Wetlands triggers under REAL rules",
                "note": "NJDEP's Watershed & Land Management program offers Flood Hazard general permits (some by registration with instant approval) for work near streams, wetlands, and tidal flood hazard areas. The REAL rules tightened mitigation thresholds when stacking general permits and raised flood-proofing elevations \u2014 verify jurisdictional status with a Jurisdictional Request Form before designing the foundation.",
                "applies_to": "Single-family and accessory work near wetlands, streams, or flood hazard areas",
                "source": "https://dep.nj.gov/wlm/lrp/common-projects/single-family-home/"
            },
            {
                "title": "Municipalities (not counties) enforce the UCC",
                "note": "The DCA delegates plan review, permit issuance, and inspections to municipal construction departments; only where a town has not established its own enforcing agency does the county or DCA step in. Confirm which agency holds jurisdiction on your address before submitting \u2014 inter-local agreements are common and the wrong office will return the package.",
                "applies_to": "Identifying the correct AHJ for permit submittal",
                "source": "https://www.nj.gov/dca/codes/forms/pdf_bcpr/pr_app_guide.pdf"
            },
            {
                "title": "Use the State-prescribed construction permit packet",
                "note": "New Jersey requires the DCA-issued construction permit application packet (building, electrical, plumbing, mechanical, and fire subcode forms) for every UCC permit. Municipal cover sheets do not replace these state forms \u2014 submitting only a local form is a frequent cause of intake rejection.",
                "applies_to": "All UCC permit submittals statewide",
                "source": "https://www.nj.gov/dca/codes/resources/constructionpermitforms.shtml"
            }
        ]
    },
    "VA": {
        "name": "Virginia expert pack",
        "expert_notes": [
            {
                "title": "Virginia USBC is statewide and preempts local amendments",
                "note": "The Virginia Uniform Statewide Building Code (USBC) is adopted by the Board of Housing and Community Development and applies in every city, town, and county. Local jurisdictions cannot alter the technical requirements, so a permit reviewer cannot impose a stricter structural or energy standard than the USBC.",
                "applies_to": "All residential and commercial permit work statewide",
                "source": "http://www.dhcd.virginia.gov/virginia-uniform-statewide-building-code-usbc"
            },
            {
                "title": "2021 USBC effective date \u2014 January 18, 2024",
                "note": "The current USBC cycle (based on the 2021 I-Codes with Virginia amendments) went into effect January 18, 2024, with enforcement of the updated cycle beginning January 18, 2025. Verify which code edition the AHJ is reviewing under, especially for projects that were started under the prior cycle.",
                "applies_to": "Plan review and permit applications submitted in 2024\u20132026",
                "source": "https://vaeec.org/programs/building-codes/"
            },
            {
                "title": "DPOR tradesman license required for HVAC, electrical, plumbing, gas",
                "note": "The Class A/B/C Contractor Building (CBC) classification does NOT cover electrical, plumbing, HVAC, or gas fitting work. Each trade must be performed by a separately licensed tradesman, journeyman, or master through the DPOR Board for Contractors. As of April 1, 2025, new residential trades license types (Residential HVAC mechanic, etc.) provide an additional pathway.",
                "applies_to": "Any permit pulling electrical, plumbing, HVAC, or gas trade work",
                "source": "https://www.dpor.virginia.gov/sites/default/files/Records%20and%20Documents/Regulant%20List/VA%20Contractors%20Classifications%20%26%20Specialties.pdf"
            },
            {
                "title": "Chesapeake Bay Preservation Act RPA review",
                "note": "Tidewater Virginia localities (under Code \u00a7\u00a7 62.1-44.15:67\u201379) require a plan-of-development review prior to building permit issuance for parcels in a Chesapeake Bay Preservation Area. RPA (Resource Protection Area) review may be triggered even when no building or land-disturbance permit would otherwise be required.",
                "applies_to": "Projects in Tidewater Virginia jurisdictions with CBPA-designated parcels",
                "source": "https://www.deq.virginia.gov/water/chesapeake-bay/chesapeake-bay-preservation-act"
            },
            {
                "title": "VPDES Construction General Permit at 1 acre disturbance",
                "note": "Land-disturbing activities of 1 acre or more require coverage under an individual or General VPDES Construction Stormwater Permit (CGP), in addition to local erosion and sediment control approval. Disturbance of non-tidal wetlands triggers a separate DEQ permit regardless of acreage.",
                "applies_to": "Site work, new construction, and additions disturbing \u22651 acre",
                "source": "https://online.encodeplus.com/regs/deq-va/doc-viewer.aspx?secid=92"
            },
            {
                "title": "ADUs require full trade permits and septic capacity review",
                "note": "An ADU in Virginia requires a residential building permit plus separate trade reviews for plumbing, electrical, and HVAC. On parcels using on-site sewage, septic capacity must be confirmed before permit issuance \u2014 this is a frequent cause of ADU permit delays in counties without public sewer.",
                "applies_to": "ADU jobs",
                "source": "https://www.zookcabins.com/regulations/adu-regulations-in-virginia"
            },
            {
                "title": "Confirm AHJ \u2014 city, town, or county before applying",
                "note": "Virginia is split between independent cities, incorporated towns, and counties, each of which may operate its own building department. Towns within a county sometimes defer to the county for building permits but retain zoning authority. Identify the correct AHJ first; submitting to the wrong department is the most common avoidable delay.",
                "applies_to": "Every Virginia permit application",
                "source": "https://www.permitflow.com/state/virginia"
            },
            {
                "title": "VFRIS floodplain check before site/foundation design",
                "note": "Use the Virginia Flood Risk Information System (VFRIS) to verify Special Flood Hazard Area status before finalizing site plan and foundation elevation. Local floodplain ordinances often require freeboard above the BFE and may require an Elevation Certificate at permit submittal and again at final inspection.",
                "applies_to": "New construction, additions, and substantial improvements in or near mapped floodplains",
                "source": "https://www.dcr.virginia.gov/dam-safety-and-floodplains/fpvfris"
            }
        ]
    },
    "TN": {
        "name": "Tennessee expert pack",
        "expert_notes": [
            {
                "title": "Tennessee $25,000 contractor license threshold",
                "note": "A Tennessee Board for Licensing Contractors license is required before bidding or negotiating a price whenever the total project cost (materials and labor) is $25,000 or more. HVAC, electrical, and mechanical fall under the contractor's license at this threshold; bidding without one can void the contract and trigger penalties.",
                "applies_to": "Any residential or commercial project with total cost $25,000 or more",
                "source": "https://www.tn.gov/commerce/regboards/contractors/license/get/contractor.html"
            },
            {
                "title": "Limited Licensed Electrician (LLE) and Limited Licensed Plumber (LLP) scope",
                "note": "Tennessee issues separate Limited Licensed Electrician (LLE) and Limited Licensed Plumber (LLP) licenses for trade work performed under the $25,000 prime contractor threshold or as a subcontractor. Verify the LLE/LLP is current before pulling electrical or plumbing permits \u2014 local AHJs will reject permit applications without it.",
                "applies_to": "Electrical and plumbing subcontractor work statewide",
                "source": "https://www.tn.gov/commerce/regboards/contractors.html"
            },
            {
                "title": "State Fire Marshal residential permits in non-local jurisdictions",
                "note": "In Tennessee counties and cities that have not opted out of statewide standards, residential building and electrical permits are purchased directly from the State Fire Marshal's Office, not a local building department. Inspections (including re-inspections) are also requested through the SFMO portal.",
                "applies_to": "Residential jobs in jurisdictions covered by the State Fire Marshal",
                "source": "https://www.tn.gov/commerce/fire/residential-permits.html"
            },
            {
                "title": "Opt-out jurisdictions and the city/county permit split",
                "note": "Tennessee counties and municipalities may opt out of statewide one- and two-family dwelling standards under Title 68, Chapter 120. Inside city limits, you typically need both a city permit and a county permit; in opt-out counties outside city limits there may be no building code enforcement at all (septic still applies). Confirm jurisdiction before quoting timelines.",
                "applies_to": "Any project \u2014 verify AHJ before pulling permits",
                "source": "https://www.tn.gov/commerce/fire/residential-permits/opt-out-jurisdictions.html"
            },
            {
                "title": "2021 IECC energy code effective April 17, 2025",
                "note": "Tennessee adopted the 2021 IECC with amendments for both residential and commercial, effective April 17, 2025 (replacing the 2018 IECC that was in force from July 16, 2020). Plans submitted under the older code may need to be updated for envelope, duct sealing, and lighting requirements unless grandfathered by the local AHJ.",
                "applies_to": "New construction and additions involving conditioned space",
                "source": "https://www.energycodes.gov/status/states/tennessee"
            },
            {
                "title": "TDEC ARAP for any work touching waters of the state",
                "note": "Physical alterations to waters of the state (streams, wetlands, springs, wet-weather conveyances) require an Aquatic Resource Alteration Permit (ARAP) or \u00a7401 Water Quality Certification from TDEC. New General ARAPs took effect May 15, 2025 and run through May 15, 2030; recent legislation allows alteration of low-quality wetlands up to 1 acre or moderate-quality up to 0.25 acres without notice, but higher-quality resources still require a permit.",
                "applies_to": "Site work near streams, wetlands, or drainage features",
                "source": "https://www.tn.gov/environment/permit-permits/water-permits1/aquatic-resource-alteration-permit--arap-.html"
            },
            {
                "title": "Seven-year code currency rule for local amendments",
                "note": "Under Title 68, Chapter 120, building codes adopted by reference by a Tennessee local government must be current within seven years of the latest edition, and local standards must meet or exceed the state minimum. Always check the specific edition the local AHJ has adopted (e.g., Rutherford County 2018 I-Codes, Metro Nashville 2024 I-Codes) \u2014 it is not uniform statewide.",
                "applies_to": "Code reference selection for plan submittals",
                "source": "https://www.mtas.tennessee.edu/reference/amendments-building-codes"
            },
            {
                "title": "Nashville DADU permitting goes through Metro Codes",
                "note": "Detached Accessory Dwelling Units (DADUs) in Davidson County must complete the permitting process with Metro Codes and Building Safety, including zoning examiner review, before a building permit issues. ADUs elsewhere in Tennessee typically require a building permit, mechanical permit, and HVAC permit at minimum \u2014 there is no statewide ministerial shot clock, and local review can run roughly 20 days.",
                "applies_to": "ADU and DADU projects",
                "source": "https://www.nashville.gov/departments/codes/construction-and-permits/building-permits-central/detached-accessory-dwelling-unit"
            }
        ]
    },
    "MA": {
        "name": "Massachusetts expert pack",
        "expert_notes": [
            {
                "title": "10th Edition MA State Building Code (780 CMR) is in effect",
                "note": "The 10th Edition of the Massachusetts State Building Code took effect October 11, 2024. All permit applications must comply with 780 CMR 1.00 to 115.00 as amended in the 10th Edition. Confirm the AHJ is reviewing under the current edition before submitting plans.",
                "applies_to": "All residential and commercial permit applications",
                "source": "https://www.mass.gov/handbook/tenth-edition-of-the-ma-state-building-code-780"
            },
            {
                "title": "Base, Stretch, and Specialized (Opt-In) energy code tiers",
                "note": "MA municipalities operate on one of three energy code tiers: Base Code (IECC 2021 with MA amendments \u2014 780 CMR Ch. 11R residential, Ch. 13 commercial), Stretch Code, or the Specialized (net-zero) Opt-In Code. Confirm the jurisdiction's tier before scoping HVAC, envelope, or solar \u2014 Specialized Code triggers electrification-readiness and pre-wiring requirements.",
                "applies_to": "New construction, additions, and major alterations",
                "source": "https://www.mass.gov/info-details/2025-massachusetts-building-energy-codes"
            },
            {
                "title": "HIC Registration vs. Construction Supervisor License (CSL) are not interchangeable",
                "note": "Work on existing owner-occupied 1-to-4 family homes generally requires the contractor to hold both an HIC Registration and a CSL. The HIC is a consumer-protection registration ($150 + Guaranty Fund payment); the CSL authorizes structural work. Pulling a permit under the wrong credential is a common rejection reason.",
                "applies_to": "Residential remodels, additions, and structural work on 1-4 family dwellings",
                "source": "https://www.mass.gov/info-details/hic-contractor-resources"
            },
            {
                "title": "HIC must pull the permit on owner-occupied jobs",
                "note": "Under M.G.L. c. 142A, the registered Home Improvement Contractor is responsible for obtaining all permits for work covered by the HIC law. If the homeowner pulls the permit instead, they forfeit access to the Guaranty Fund \u2014 a frequent disclosure issue that can void the contract.",
                "applies_to": "HIC-covered residential improvement work",
                "source": "https://www.middleboroughma.gov/FAQ.aspx?QID=94"
            },
            {
                "title": "Wetlands Protection Act \u2014 Order of Conditions required near resource areas",
                "note": "Any work within 100 ft of a wetland, 200 ft of a perennial stream, or in a flood zone requires filing a Notice of Intent with the local Conservation Commission and obtaining an Order of Conditions (WPA Form 5) before a building permit can be issued. The Order is not final until appeal periods expire \u2014 budget 8-12 weeks.",
                "applies_to": "Projects within Wetlands Protection Act buffer zones or floodplains",
                "source": "https://www.mass.gov/how-to/wpa-form-5-order-of-conditions"
            },
            {
                "title": "Statewide Protected-Use ADUs as of right (Affordable Homes Act)",
                "note": "As of February 2, 2025, the Affordable Homes Act allows Protected Use ADUs by right in single-family zones statewide. Local procedures still vary \u2014 many towns layered site plan review onto the bylaw, so confirm whether the AHJ treats it as ministerial or discretionary before promising a timeline.",
                "applies_to": "ADU projects on single-family-zoned lots",
                "source": "https://www.mass.gov/info-details/accessory-dwelling-unit-adu-faqs"
            },
            {
                "title": "MA zoning is local \u2014 no county building departments",
                "note": "All zoning and building permit authority in Massachusetts sits with the municipality (city or town). There is no county-level permit office. Each community designates its own permit-granting authority, and bylaws can assign different boards (ZBA, Planning, Conservation) to different permit types. Always verify the issuing authority per project type.",
                "applies_to": "All MA jurisdictions",
                "source": "https://www.mass.gov/info-details/re16rc13-zoning-building-codes"
            },
            {
                "title": "Floodplain Overlay District / MC-FRM coastal flood risk",
                "note": "Coastal and riverine parcels are commonly within a Floodplain Overlay District tied to the FEMA Special Flood Hazard Area (1% annual chance / 100-year zone). MA also evaluates wetlands permits against the Massachusetts Coastal Flood Risk Model (MC-FRM), which projects up to 2.5 ft of sea level rise by 2050 \u2014 expect freeboard, breakaway-wall, or elevation conditions on coastal jobs.",
                "applies_to": "Coastal, riverine, and floodplain parcels",
                "source": "https://www.mass.gov/info-details/coastal-flood-risk-model-mc-frm-evaluation-for-wetlands-permitting"
            }
        ]
    },
    "IN": {
        "name": "Indiana expert pack",
        "expert_notes": [
            {
                "title": "Indiana Residential Code is 2020 IRC with state amendments (675 IAC 14)",
                "note": "Indiana has adopted the 2020 IRC as the Indiana Residential Code with state-specific amendments under 675 IAC. Always check the Indiana amendments rather than relying on the model IRC text \u2014 provisions like fastener schedules, electrical bonding, and insulation values diverge from the base IRC.",
                "applies_to": "All one- and two-family residential construction and alterations",
                "source": "https://up.codes/viewer/indiana/irc-2018"
            },
            {
                "title": "Indiana energy code amendment: R-15 cavity insulation in Climate Zone 4",
                "note": "Indiana amended the IECC to require R-15 cavity insulation (raised from R-13) in Climate Zone 4 wood-frame walls. Submitting plans with R-13 will trigger a correction notice \u2014 specify R-15 batt or equivalent continuous insulation up front.",
                "applies_to": "New construction and additions with conditioned wood-frame walls in Climate Zone 4",
                "source": "https://insulationinstitute.org/wp-content/uploads/2025/05/N105-IN-Energy-Code-0425.pdf"
            },
            {
                "title": "IDHS state-level plan review: 10-business-day response clock",
                "note": "For projects requiring state-level review by Indiana Department of Homeland Security, submission starts an automatic 10-business-day clock. IDHS must respond (release, release with conditions, or disapproval) within 10 business days \u2014 track the submission date and escalate if exceeded.",
                "applies_to": "Class 1 structures and other projects requiring IDHS Building Plan Review",
                "source": "https://www.in.gov/dhs/building-plan-review/building-plan-review-process/"
            },
            {
                "title": "State-licensed plumbing vs. locally licensed HVAC",
                "note": "Indiana licenses plumbing contractors and electrical work at the state level (PLA), but HVAC licensing is handled locally by city or county \u2014 there is no statewide HVAC license. Verify the local AHJ's HVAC requirement separately (e.g., Lake County issues its own); using only a state credential will fail HVAC permit pulls.",
                "applies_to": "HVAC and plumbing permit applications",
                "source": "https://www.servicetitan.com/licensing/hvac/indiana"
            },
            {
                "title": "Split jurisdiction: state oversight, local enforcement",
                "note": "Indiana's permitting is split \u2014 IDHS sets the state code and reviews Class 1 (commercial/multi-family) plans, while local building departments enforce and issue permits for Class 2 (one- and two-family) structures. Confirm whether a project is Class 1 or Class 2 before deciding where to file; misrouted submittals lose weeks.",
                "applies_to": "All commercial, multi-family, and residential permit submittals",
                "source": "https://www.permitflow.com/state/indiana"
            },
            {
                "title": "DNR Construction in a Floodway permit required before local permit",
                "note": "Any structure or fill in a regulated floodway requires a Construction in a Floodway permit from the IDNR Division of Water before local building permits can be issued. Local AHJs will not release a building permit until the DNR approval is in hand for floodway work \u2014 start the DNR application early since it runs in parallel, not sequentially.",
                "applies_to": "Projects in a regulated floodway or SFHA",
                "source": "https://www.in.gov/dnr/water/regulatory-permit-programs/"
            },
            {
                "title": "IDEM 14-working-day construction plan review for large activities",
                "note": "Under Indiana Code 13-18-27-16, IDEM must act on a construction plan for a large construction activity by the 14th working day after submission. If the deadline passes without action, the plan is deemed reviewed \u2014 document the submission date precisely.",
                "applies_to": "Large construction activities requiring IDEM construction plan review (stormwater, sewer extensions, etc.)",
                "source": "https://law.justia.com/codes/indiana/title-13/article-18/chapter-27/section-13-18-27-16/"
            },
            {
                "title": "Floodplain Overlay districts trigger extra local review",
                "note": "Many Indiana jurisdictions impose a Flood Hazard / Conservation Floodplain Overlay on top of base zoning, requiring a separate floodplain development permit demonstrating no increase in flood elevation. This is in addition to any DNR floodway permit \u2014 check the local zoning map for overlay districts before quoting timelines.",
                "applies_to": "Any development within a mapped SFHA or local floodplain overlay",
                "source": "https://www.in.gov/dnr/water/files/wa-FP_Management_Indiana_QuickGuide.pdf"
            }
        ]
    },
    "MD": {
        "name": "Maryland expert pack",
        "expert_notes": [
            {
                "title": "Separate state HVACR license plus local trade permits",
                "note": "HVACR contractors must be licensed by the Maryland Board of HVACR Contractors at the state level, but local plumbing, gasfitting, and electrical permits are still required from the county or municipality before HVACR work can begin. Pulling the state license alone does not authorize work \u2014 confirm the local AHJ permit is open.",
                "applies_to": "HVAC, gasfitting, and mechanical scope on residential and commercial jobs",
                "source": "https://www.labor.maryland.gov/license/hvacr/hvacrcounty.shtml"
            },
            {
                "title": "Maryland Building Performance Standards (MBPS) \u2014 IECC 2021 statewide",
                "note": "Maryland adopted the 2021 IECC (with ASHRAE 90.1-2019 commercial path) as the statewide energy code. All local jurisdictions were required to amend and adopt the new code for local enforcement by May 29, 2024. Energy compliance documentation (envelope, fenestration U-values, duct/air sealing) is mandatory for new construction and conditioned-space alterations.",
                "applies_to": "New construction, additions, and alterations affecting conditioned space",
                "source": "https://energy.maryland.gov/pages/policy-energy-codes.aspx"
            },
            {
                "title": "Local jurisdictions can amend codes \u2014 but not the IECC",
                "note": "Under the MBPS each Maryland local jurisdiction may modify the IBC/IRC and related codes to suit local conditions, with the explicit exception of the International Energy Conservation Code, which must be adopted as written. Always check the county or municipal amendments before submitting plans, but never assume the energy code has been weakened locally.",
                "applies_to": "Any project subject to local plan review in Maryland",
                "source": "https://labor.maryland.gov/labor/build/buildcodes.shtml"
            },
            {
                "title": "Chesapeake Bay Critical Area review for shoreline parcels",
                "note": "All private projects within the Chesapeake Bay or Atlantic Coastal Bays Critical Area \u2014 including individual building permits, additions, and grading \u2014 require Critical Area review by the local planning department in addition to the standard building permit. This adds review time and can trigger impervious-surface caps, buffer setbacks, and mitigation plantings.",
                "applies_to": "Parcels within 1,000 ft of tidal waters or tidal wetlands in the Critical Area",
                "source": "https://dnr.maryland.gov/criticalarea/pages/development_in_cac.aspx"
            },
            {
                "title": "MDE Wetlands and Waterways permit for floodplain or wetland work",
                "note": "Construction in wetlands or floodplains requires a separate state permit from the MDE Wetlands and Waterways Program (Water and Science Administration), in addition to local building and grading permits. Foundation, fill, and outbuilding work in mapped flood zones cannot proceed on local permits alone.",
                "applies_to": "Construction in mapped wetlands, tidal/non-tidal floodplains, or waterways",
                "source": "https://mde.maryland.gov/programs/water/stormwatermanagementprogram/floodhazardmitigation/pages/permitting.aspx"
            },
            {
                "title": "Statewide ADU authorization deadline \u2014 October 1, 2026",
                "note": "By October 1, 2026, all Maryland local legislative bodies must adopt laws authorizing Accessory Dwelling Units. Until a jurisdiction has adopted its ADU ordinance, ADU permitting still runs through existing local zoning (often as accessory apartments or in-law suites), so confirm the current local rule rather than assuming statewide ministerial approval.",
                "applies_to": "ADU and accessory apartment projects",
                "source": "https://www.dougpruettconstruction.com/blog/ADU-laws-and-permit-requirements-in-maryland--what-you-need-to-know"
            },
            {
                "title": "Dual county + municipal permits in incorporated towns",
                "note": "Maryland uses a dual permitting system: in many incorporated municipalities (common in Montgomery, Prince George's, and other counties) you must pull both a county building permit and a separate municipal permit for the same job. Pricing and scheduling around only the county permit is a frequent miss \u2014 confirm the town's requirement before mobilizing.",
                "applies_to": "Projects located inside incorporated towns or municipalities",
                "source": "https://www3.montgomerycountymd.gov/311/SolutionView.aspx?SolutionId=1-4WNX4B"
            }
        ]
    },
    "MO": {
        "name": "Missouri expert pack",
        "expert_notes": [
            {
                "title": "No statewide building or trade license \u2014 verify the local AHJ",
                "note": "Missouri has no statewide general contractor, plumbing, HVAC, or mechanical contractor license. Licensing for electricians, plumbers, mechanical (HVAC) contractors, drainlayers, and pool installers is handled at the city or county level, so confirm requirements with the specific AHJ before pulling a permit.",
                "applies_to": "All trade contractors operating in Missouri",
                "source": "https://adaptdigitalsolutions.com/articles/missouri-contractor-license-requirements/"
            },
            {
                "title": "Statewide electrical contractor license is reciprocity-only",
                "note": "Missouri's Office of Statewide Electrical Contractors issues a statewide electrical contractor license that is recognized by participating municipalities, but it does not replace local licensing in non-participating cities. Contractors must still register with each local AHJ that requires its own license.",
                "applies_to": "Electrical contractors working across multiple Missouri jurisdictions",
                "source": "https://pr.mo.gov/electricalcontractors.asp"
            },
            {
                "title": "No statewide energy code \u2014 adoption varies by jurisdiction",
                "note": "Missouri has not adopted a statewide residential or commercial energy code. Each jurisdiction sets its own code; check the Missouri DNR Energy Codes by Jurisdiction list before assuming any IECC version applies, because requirements (and effective dates) differ city-to-city.",
                "applies_to": "New construction and additions across Missouri",
                "source": "https://dnr.mo.gov/energy/efficiency/codes-jurisdiction"
            },
            {
                "title": "Kansas City 2021 IECC with local amendments",
                "note": "Kansas City adopted the 2021 IECC effective July 1, 2023 (with a 90-day grace period), and Ordinance 260144 amended it to provide additional compliance options including updated wall insulation requirements. Confirm which amendment package applies to a permit submittal date because KC's IECC differs from the published 2021 baseline.",
                "applies_to": "Residential and commercial projects in Kansas City, MO",
                "source": "https://www.nahb.org/blog/2026/02/kansas-city-2021-iecc-amendments"
            },
            {
                "title": "Floodplain Development Permit triggers separate review and engineer certification",
                "note": "Any development in a Special Flood Hazard Area requires a local Floodplain Development Permit in addition to the building permit, and the developer/owner must provide as-built certification by a registered engineer, architect, or land surveyor. This is a separate approval from the standard building permit and can add weeks to the timeline.",
                "applies_to": "Construction, fill, or substantial improvement in a mapped floodplain",
                "source": "https://sema.dps.mo.gov/programs/floodplain/documents/floodplain-develoment-permit.pdf"
            },
            {
                "title": "Substantial improvement rule for historic and existing structures in floodplains",
                "note": "When work in a floodplain requires a Floodplain Development Permit, the local floodplain administrator must review all proposed work and the cumulative cost of all work counts toward the substantial improvement threshold (typically 50% of market value). Phasing a remodel does not avoid the trigger; once crossed, the entire structure must be brought into floodplain compliance.",
                "applies_to": "Remodels, additions, and repairs to existing structures in a Missouri floodplain",
                "source": "https://sema.dps.mo.gov/programs/floodplain/documents/nfip-historic-structures.pdf"
            },
            {
                "title": "ADUs governed locally \u2014 expect conditional use permits in dense zones",
                "note": "Missouri has no state ADU shot clock or by-right statute. In more densely zoned residential areas ADUs may be restricted or require a conditional use permit, and review timelines run roughly two to eight weeks depending on jurisdiction and workload. Build the CUP hearing window into the project schedule from day one.",
                "applies_to": "Accessory dwelling unit projects in Missouri",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-missouri"
            },
            {
                "title": "City code applies to county-owned buildings inside city limits",
                "note": "Missouri appellate case law has held that a city's building code applies to county buildings located within that city's limits, despite county claims of state-law authority to build without restriction. For projects on government-owned parcels, do not assume county-level review supersedes municipal permitting \u2014 confirm jurisdiction in writing before submittal.",
                "applies_to": "Projects on county or public-agency property within an incorporated Missouri city",
                "source": "https://lawoftheland.wordpress.com/2012/10/11/missouri-court-of-appeals-finds-city-building-code-applies-to-county-buildings-within-the-city/"
            }
        ]
    },
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
