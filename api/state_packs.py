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
