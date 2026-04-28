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
            {
                "title": "CSLB licensing — $500 unlicensed-work threshold",
                "note": (
                    "California Contractors State License Board (CSLB) requires a license for any single project "
                    "with combined labor + materials at $500 or more. Unlicensed contracting above that threshold is "
                    "a misdemeanor (B&P §7028). Verify license + classification (e.g. B General Building, C-10 "
                    "Electrical, C-36 Plumbing, C-20 HVAC) on the CSLB lookup before quoting; expired or suspended "
                    "licenses void the contract and block lien rights."
                ),
                "applies_to": "All paid construction work in California ≥ $500",
                "source": "https://www.cslb.ca.gov/Consumers/HireAContractor/CheckTheLicenseFirst.aspx",
            },
            {
                "title": "SB 9 lot split + 2-unit ministerial path",
                "note": (
                    "SB 9 (Govt Code §65852.21 / §66411.7) lets owners of single-family-zoned parcels split a lot "
                    "into two and/or build a duplex per resulting parcel under ministerial review (no public hearing, "
                    "no CEQA). Combined with ADU/JADU rules this can yield up to 4 units on a former SFR lot. Owner "
                    "must sign a 3-year owner-occupancy affidavit on lot splits. Cities can impose objective design "
                    "standards but cannot deny qualifying SB 9 projects."
                ),
                "applies_to": "Single-family-zoned residential infill and duplex conversions",
                "source": "https://www.hcd.ca.gov/policy-and-research/accessory-dwelling-units",
            },
            {
                "title": "California Building Standards Code triennial cycle (2025 → 2026 enforcement)",
                "note": (
                    "California adopts a new Title 24 / CBSC edition every three years. The 2025 California "
                    "Building/Residential/Plumbing/Electrical/Mechanical/Energy/Green Building Standards Codes were "
                    "published Jul 1, 2025 and become enforceable Jan 1, 2026 statewide. Permit applications submitted "
                    "before Jan 1, 2026 may still be reviewed under the 2022 code at the AHJ's discretion — confirm "
                    "which code edition applies before submitting drawings."
                ),
                "applies_to": "Permit applications crossing the 2026-01-01 code-change boundary",
                "source": "https://www.dgs.ca.gov/BSC",
            },
            {
                "title": "CALGreen mandatory + EV-ready / solar-ready requirements",
                "note": (
                    "CALGreen (Title 24 Part 11) sets statewide green-building minimums: low-VOC finishes, water-use "
                    "reduction (≤1.28 gpf toilets, ≤1.8 gpm kitchen faucets), EV-ready raceway in new SFR garages, "
                    "and solar-ready zone for new low-rise residential. Tier 1/Tier 2 voluntary measures may be locally "
                    "mandated (e.g., San Francisco, Santa Monica). Plan check rejection is common when the EV raceway "
                    "or solar-ready zone is omitted from new SFR/ADU drawings."
                ),
                "applies_to": "New construction and major remodels statewide",
                "source": "https://www.dgs.ca.gov/BSC/CALGreen",
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
    "WI": {
        "name": "Wisconsin expert pack",
        "expert_notes": [
            {
                "title": "Wisconsin UDC 10-business-day permit shot clock",
                "note": "Under SPS 320.09(8)(a), a municipality must approve or deny a Uniform Building Permit application within 10 business days of receiving all required forms, fees, plans, and documents for a 1- or 2-family dwelling. If a jurisdiction sits past that, you have grounds to escalate.",
                "applies_to": "1- and 2-family dwelling permits statewide",
                "source": "https://docs.legis.wisconsin.gov/document/administrativecode/SPS%20320.09(8)(a)"
            },
            {
                "title": "Dwelling Contractor + Qualifier required to pull permits",
                "note": "To pull permits on 1- or 2-family dwellings in Wisconsin, the company must hold a Dwelling Contractor Certification and employ a Dwelling Contractor Qualifier. Both are issued by DSPS through the LicensE portal \u2014 verify both are active before submitting, since municipalities like Wauwatosa reject applications missing either credential.",
                "applies_to": "All new construction and alterations to 1- and 2-family dwellings",
                "source": "https://dsps.wi.gov/Pages/Professions/DwellingContractor/Default.aspx"
            },
            {
                "title": "Separate trade credentials for HVAC, electrical, and plumbing",
                "note": "Wisconsin licenses each trade separately through DSPS. HVAC work requires a Wisconsin HVAC Qualifier and registered HVAC Contractor; electrical requires an Electrical Contractor registration with a Master Electrician; plumbing requires a Dwelling Contractor Qualifier plus a master-level plumber. A general Dwelling Contractor cert does not authorize trade work.",
                "applies_to": "Jobs involving HVAC, electrical, or plumbing scope",
                "source": "https://dsps.wi.gov/Pages/Professions/HVACContractor/Default.aspx"
            },
            {
                "title": "Wisconsin uses a hybrid UDC, not the IRC \u2014 and stays on older IECC",
                "note": "Residential 1- and 2-family construction follows the Wisconsin Uniform Dwelling Code (SPS 320\u2013325), a state-specific hybrid code, not the IRC. Wisconsin remains on the 2009 IECC for residential energy compliance and 2015 IECC for commercial \u2014 do not assume current IECC editions when speccing envelope, fenestration, or mechanical efficiency.",
                "applies_to": "Residential new construction, additions, and alterations affecting energy compliance",
                "source": "https://www.iccsafe.org/advocacy/adoptions-map/wisconsin/"
            },
            {
                "title": "AHJ split: state vs. delegated municipality vs. county",
                "note": "Under the UDC, municipalities may issue a single combined building permit or split permits by category, and counties administer the UDC where towns have not been delegated. Confirm at the start whether the municipality, county, or state DSPS inspector has jurisdiction over plan review and inspections \u2014 the wrong submittal route is the most common cause of delay.",
                "applies_to": "Any 1- or 2-family permit, especially in unincorporated towns",
                "source": "https://dsps.wi.gov/Documents/Programs/UDC/CodeArchives/SPS320Commentary.pdf"
            },
            {
                "title": "Shoreland, floodplain, and Chapter 30 navigable-waters overlays",
                "note": "Parcels within 1,000 ft of a lake/flowage or 300 ft of a navigable river/stream fall under county shoreland zoning, and work in or near navigable water requires a DNR Chapter 30 permit. UDC building permit exemptions do NOT exempt the project from shoreland, floodplain, or wetland approvals \u2014 check county GIS and DNR surface water viewer before site work.",
                "applies_to": "Sites near lakes, rivers, streams, wetlands, or mapped floodplains",
                "source": "https://dnr.wisconsin.gov/topic/Waterways/Permits/PermitProcess.html"
            },
            {
                "title": "ADUs follow local zoning \u2014 no statewide ministerial approval",
                "note": "Wisconsin has no statewide ADU ministerial-approval law; ADU allowance, setbacks, and owner-occupancy rules are set by the municipality or county. Realistic permit timelines run 4\u201312 weeks depending on AHJ (e.g., Madison plan review alone is 10\u201315 business days), so sequence zoning approval before building permit submittal.",
                "applies_to": "Accessory Dwelling Unit projects",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-wisconsin"
            },
            {
                "title": "Watch the 2021 IECC / commercial code transition",
                "note": "DSPS is moving the commercial building code forward (2021 IECC adoption tracked under CR 23-007), with the updated Commercial Building Code expected to take effect September 1, 2025. Projects straddling the effective date should confirm which code edition governs at permit issuance to avoid mid-project envelope or mechanical redesign.",
                "applies_to": "Commercial new construction and major alterations near the code-transition date",
                "source": "https://www.wisbuild.org/news-1/cbcchanges2025"
            }
        ]
    },
    "MN": {
        "name": "Minnesota expert pack",
        "expert_notes": [
            {
                "title": "Minnesota State Building Code adoption (Chapter 1309 / IRC)",
                "note": "Minnesota Rules Chapter 1309 adopts the International Residential Code by reference with state-specific amendments. References to the IRC in the code mean the Minnesota Residential Code adopted under Chapter 1309 and Minnesota Statutes 326B.106. Confirm which IRC edition the local AHJ is enforcing before submittal, since amendments and effective dates differ from the base IRC.",
                "applies_to": "All one- and two-family residential construction in Minnesota",
                "source": "https://www.revisor.mn.gov/rules/1309/full"
            },
            {
                "title": "Three-year residential energy code update cycle (326B.106)",
                "note": "Per Minnesota Statute 326B.106, a new commercial and residential energy code is adopted every three years. Insulation minimums, air sealing, and mechanical efficiency requirements change each cycle. Verify the energy code edition in effect at permit application \u2014 projects vested under the prior cycle may have different envelope requirements than projects submitted after adoption.",
                "applies_to": "New residential construction and additions/alterations affecting conditioned space",
                "source": "https://www.mncee.org/what-are-energy-codes-and-why-should-i-care"
            },
            {
                "title": "HVAC is not a state-issued license \u2014 $25K DLI bond plus local competency",
                "note": "Minnesota does not issue a state HVAC contractor license, but HVAC contractors must post a $25,000 bond with the Department of Labor and Industry. Local jurisdictions (e.g., Minneapolis) require their own competency cards and exams. Confirm both the DLI bond and the city-specific competency credential before pulling an HVAC permit.",
                "applies_to": "HVAC/mechanical permit applications statewide",
                "source": "https://www.servicetitan.com/licensing/hvac/minnesota"
            },
            {
                "title": "Plumbing contractor licensing is state-administered through DLI",
                "note": "Plumbing contractor licensing in Minnesota is handled by the Department of Labor and Industry, which issues plumbing contractor licenses, pipe layer bonds, and tracks continuing education. Unlike HVAC, plumbing requires a state-issued contractor license \u2014 verify the license is active and the bond is current before submitting plumbing permit applications.",
                "applies_to": "Plumbing permit applications statewide",
                "source": "https://www.dli.mn.gov/business/plumbing-contractors/licensing-plumbing-contractor-licenses"
            },
            {
                "title": "No statewide ADU statute \u2014 fully delegated to local zoning",
                "note": "Minnesota has no statewide or countywide ADU law. There is no ministerial shot clock, no state-level impact-fee waiver, and no preemption of local zoning. ADU permittability, setbacks, owner-occupancy, and parking rules are determined entirely by city or county ordinance. Plan review typically runs two to eight weeks depending on the AHJ.",
                "applies_to": "ADU and accessory structure jobs",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-minnesota"
            },
            {
                "title": "City/county jurisdiction split \u2014 use the DLI Local Code Lookup",
                "note": "Zoning and building authority in Minnesota is divided between cities and counties, with each able to set its own regulations. Incorporated cities typically run their own building department; townships and unincorporated areas usually fall to the county. Use the DLI Local Code Lookup to confirm which jurisdiction enforces the State Building Code at a given address before applying.",
                "applies_to": "Determining the correct AHJ for any Minnesota permit",
                "source": "https://workplace.doli.state.mn.us/jurisdiction/"
            },
            {
                "title": "Wetland Conservation Act and shoreland overlay triggers",
                "note": "The Wetland Conservation Act (Minnesota Rules Chapter 8420) regulates non-public-water wetlands; grading or filling must meet WCA standards, typically administered by the county Soil and Water Conservation District. Shoreland and floodplain areas add a separate overlay enforced via local zoning. Screen the parcel for wetland, shoreland, and floodplain overlays early \u2014 these can require a separate permit track in addition to the building permit.",
                "applies_to": "Sites near wetlands, lakes, rivers, or mapped floodplains",
                "source": "https://www.dnr.state.mn.us/wetlands/regulations.html"
            },
            {
                "title": "State plan review covers only major State Building Code features",
                "note": "DLI building plan review examines major State Building Code features and health/safety regulatory elements \u2014 it does not replace local plan review for zoning, setbacks, or local amendments. Expect a two-track review (state where required, plus local) and budget time accordingly; relying on a state-level approval to clear local issues is a common mistake.",
                "applies_to": "Projects subject to state-level plan review (state-licensed facilities, certain larger projects)",
                "source": "https://www.dli.mn.gov/licenses-permits-and-plan-reviews/building-plan-review/faqs-building-plan-review"
            }
        ]
    },
    "SC": {
        "name": "South Carolina expert pack",
        "expert_notes": [
            {
                "title": "2021 SC Building Codes are the adopted statewide baseline",
                "note": "The South Carolina Building Codes Council adopted the 2021 editions of the IBC, IRC, IECC, IMC, IPC, IFGC, and IFC (with state modifications) on October 6, 2021. Local jurisdictions cannot enforce amendments that have not been pre-approved by the Building Codes Council, so verify any city/county 'extra' requirement is on the BCC-approved list before complying.",
                "applies_to": "All residential and commercial permit submittals statewide",
                "source": "https://llr.sc.gov/bcc/BCAdoption.aspx"
            },
            {
                "title": "Residential Builder license vs. Specialty trade waivers",
                "note": "A South Carolina Residential General Contractor licensed without a waiver may pull electrical, plumbing, and HVAC permits for residential projects; with a waiver, those trades must be subcontracted to RBE/RBP/RBH license holders. Confirm the waiver status on the LLR license before signing the permit application as the trade of record.",
                "applies_to": "Residential GC pulling electrical, plumbing, or HVAC permits",
                "source": "https://mycontractorslicense.com/blog/south-carolina-residential-contractors-license-with-waiver/?srsltid=AfmBOortZ3q4GAoaSFJnS8kbTJ9X3RJMay8dyvLtiq8C2Kd9lXESloij"
            },
            {
                "title": "HVAC contractor $10,000 surety bond threshold",
                "note": "Residential HVAC contractors licensed by the SC Residential Builders Commission must post a $10,000 surety bond when the total cost of work exceeds the statutory threshold. Some AHJs will not issue a mechanical permit until the bond is on file with LLR, so verify before scheduling rough-in.",
                "applies_to": "Residential HVAC permit pulls",
                "source": "https://www.servicetitan.com/licensing/hvac/south-carolina"
            },
            {
                "title": "180-day permit vesting / expiration rule",
                "note": "Permits issued under the SC-adopted IBC/IRC become invalid if work authorized does not commence within 180 days of issuance, or if work is suspended or abandoned for 180 days after starting. Schedule at least one inspection within each 180-day window to keep the permit alive and avoid re-permitting under newer code cycles.",
                "applies_to": "Any active building permit (residential or commercial)",
                "source": "https://www.tompsc.com/1111/Frequently-Asked-Questions"
            },
            {
                "title": "OCRM Critical Area / Critical Line review on the coast",
                "note": "On the eight coastal counties, any alteration seaward of the OCRM-mapped critical line (tidelands, coastal waters, beaches, primary dunes) requires a SCDES Bureau of Coastal Management Critical Area permit in addition to the local building permit. Request a Critical Area Line determination early \u2014 the property owner (not the contractor) must be the applicant.",
                "applies_to": "Coastal county projects near tidelands, marsh, or beachfront",
                "source": "https://des.sc.gov/programs/bureau-coastal-management/critical-area-permitting"
            },
            {
                "title": "Modular buildings preempt local plan review",
                "note": "Under the SC Modular Buildings Construction Act, modular units approved by the state and bearing the SC insignia are deemed to comply with the state building codes; local AHJs may inspect site work (foundation, set, utility hook-ups) but cannot require a second structural plan review of the module itself.",
                "applies_to": "Modular / factory-built residential and commercial structures",
                "source": "https://www.scstatehouse.gov/code/t23c043.php"
            },
            {
                "title": "ADUs require local zoning approval \u2014 no state ministerial shot clock",
                "note": "South Carolina has no statewide ADU statute equivalent to ministerial 60-day approval; ADUs require a building permit, zoning approval, and inspections set by each municipality or county. Initial plan review typically runs about five business days with the permit issued shortly after, but allow extra time for HOA, septic (DHEC/DES), and setback review.",
                "applies_to": "Accessory Dwelling Unit projects statewide",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-south-carolina"
            },
            {
                "title": "Municipal vs. county AHJ split under Title 6, Chapter 29",
                "note": "Under SC Code Title 6 Chapter 29, unincorporated parcels can be brought under municipal jurisdiction by agreement, so a parcel just outside city limits may still be reviewed by the city. Always confirm jurisdiction by parcel (not just address) before submitting \u2014 pulling a permit with the wrong AHJ is a common cause of duplicate fees and re-review delays.",
                "applies_to": "Projects on annexed, fringe, or unincorporated parcels",
                "source": "https://www.scstatehouse.gov/code/t06c029.php"
            }
        ]
    },
    "AL": {
        "name": "Alabama expert pack",
        "expert_notes": [
            {
                "title": "Alabama Residential Building Code minimum standard (effective 2027)",
                "note": "Per Alabama Code 34-14A-12, any local building code adopted by a county or municipality after January 1, 2027 must meet the minimum standards of the Alabama Residential Building Code. Verify your AHJ's adopted code edition and confirm it is at or above the state floor before submittal.",
                "applies_to": "All residential new construction and additions in Alabama jurisdictions",
                "source": "https://alison.legislature.state.al.us/code-of-alabama?section=34-14A-12"
            },
            {
                "title": "Statewide residential energy code is 2021 IECC-R",
                "note": "As of January 29, 2023, the 2021 IECC-R is Alabama's residential energy code by default with no state amendments. Plans must demonstrate 2021 IECC-R compliance for envelope, mechanical, and lighting unless the local AHJ has formally adopted a different edition.",
                "applies_to": "New residential construction, additions, and conditioned-space alterations",
                "source": "https://database.aceee.org/state/residential-codes"
            },
            {
                "title": "Separate state boards govern residential, electrical, and HVAC licensing",
                "note": "Alabama splits contractor licensing across multiple boards: the Home Builders Licensure Board (residential), the State Licensing Board for General Contractors (commercial/$50k+), the Alabama Electrical Contractors Board, and the Board of Heating, Air Conditioning & Refrigeration Contractors. Subs working under a residential GC still need their own trade license, and a missing trade license is a common cause of permit denial or stop-work orders.",
                "applies_to": "Any project pulling residential building, electrical, HVAC, or plumbing permits",
                "source": "https://1examprep.com/blogs/news-insight/which-contractor-license-do-you-need-in-alabama-the-complete-2026-guide"
            },
            {
                "title": "Municipal vs. county jurisdiction split \u2014 confirm AHJ before applying",
                "note": "Alabama permitting follows the city/unincorporated-county boundary. If the parcel is inside a municipality, the city issues the permit; if it is unincorporated, the county building department (or in some cases a city under an interlocal agreement) is the AHJ. Submitting to the wrong office is a common delay \u2014 verify the parcel's jurisdiction first.",
                "applies_to": "All Alabama building permits, especially near city limits",
                "source": "https://poolbrokersusa.com/alabama-pool-permit-process/"
            },
            {
                "title": "Municipal building-permit fee requires an actual inspection",
                "note": "Under Alabama Code 11-40-10, a municipality may not collect a building-permit fee unless it actually conducts a building inspection. If a city is charging a permit fee without providing inspection services, the fee is not authorized \u2014 useful leverage when a small-town AHJ tries to collect without performing reviews.",
                "applies_to": "Municipal permit fee disputes",
                "source": "https://codes.findlaw.com/al/title-11-counties-and-municipal-corporations/al-code-sect-11-40-10/"
            },
            {
                "title": "ADEM Construction General Permit (CGP) for land disturbance",
                "note": "Land-disturbance activities that discharge stormwater require coverage under ADEM's NPDES Construction General Permit. This is separate from the local building permit and is typically triggered for sites disturbing 1+ acre. Coverage and an SWPPP must be in place before earthwork begins.",
                "applies_to": "Sites with land disturbance discharging stormwater, typically 1 acre or larger",
                "source": "https://adem.alabama.gov/water/npdes-programs/construction-general-permit"
            },
            {
                "title": "ADEM Coastal Program permit for Mobile/Baldwin coastal work",
                "note": "Construction in Alabama's coastal area (Mobile and Baldwin counties) within the Coastal Zone may require a separate permit from ADEM's Mobile Coastal Office at 1615 South Broad Street, Mobile, AL 36605, in addition to local and Corps approvals. Build this lead time into the schedule for any waterfront or near-shore work.",
                "applies_to": "Coastal-zone construction in Mobile and Baldwin counties",
                "source": "https://adem.alabama.gov/coastal"
            },
            {
                "title": "ADU permits are local \u2014 no statewide ADU shot clock",
                "note": "Alabama has no state-level ADU statute or ministerial review timeline. ADUs require a building permit plus electrical/plumbing permits and local zoning plan review, and whether ADUs are even allowed depends entirely on the county or city zoning district. Confirm allowance with the local planning department before design \u2014 do not assume by-right approval.",
                "applies_to": "ADU and accessory-structure projects",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-alabama"
            }
        ]
    },
    "LA": {
        "name": "Louisiana expert pack",
        "expert_notes": [
            {
                "title": "LSUCCC statewide code adoption (2021 IRC/IECC)",
                "note": "Louisiana enforces the Louisiana State Uniform Construction Code (LSUCC) statewide, with the 2021 IRC and 2021 IECC (with LSUCCC amendments) effective January 1, 2023. All parishes and municipalities must enforce these state-adopted codes as the minimum standard, so local AHJs cannot drop below them.",
                "applies_to": "All residential and commercial construction statewide",
                "source": "https://www.icc-nta.org/code-update/louisiana-code-adoption-and-amendments-effective-january-1-2023/"
            },
            {
                "title": "2021 IECC blower door and duct leakage testing",
                "note": "All of Louisiana is energy Climate Zone 2. Mandatory blower door testing took effect July 1, 2024, and duct leakage limits tightened under the 2021 IECC amendments. Plan for a third-party rater and budget for the test report at rough-in/final, or the inspector will fail the energy compliance check.",
                "applies_to": "New residential construction and additions creating conditioned space",
                "source": "https://insulationinstitute.org/wp-content/uploads/2025/05/N133-LA-Energy-Code-0425.pdf"
            },
            {
                "title": "LSLBC contractor licensing thresholds",
                "note": "A Louisiana State Licensing Board for Contractors (LSLBC) license is required for residential projects over $75,000 and for any electrical, mechanical, or plumbing work exceeding $10,000 (labor + materials). Mechanical contractors performing plumbing over $10,000 also need a Master Plumber license from the State Plumbing Board. Pulling permits without the right classification will get the application rejected.",
                "applies_to": "Residential GC, electrical, mechanical, and plumbing scopes",
                "source": "https://lslbc.gov/types-of-licenses/"
            },
            {
                "title": "Coastal Use Permit (CUP) in the LA Coastal Zone",
                "note": "Any activity affecting the Louisiana Coastal Zone requires a Coastal Use Permit from the Office of Coastal Management (DENR) in addition to local building permits. Check whether the parcel is inside the Coastal Zone boundary before bidding \u2014 CUP review adds significant time and can require mitigation for wetland impacts.",
                "applies_to": "Construction in coastal parishes and wetland-adjacent parcels",
                "source": "https://www.denr.louisiana.gov/page/applying-for-a-coastal-use-permit"
            },
            {
                "title": "Council-certified building official requirement",
                "note": "Louisiana law requires each parish and municipality to appoint a council-certified building official or contract that role to another governmental entity or qualified third party. Confirm which AHJ actually issues and inspects in unincorporated areas \u2014 some parishes contract enforcement out, which changes the portal, fee schedule, and inspector pool.",
                "applies_to": "Confirming permit jurisdiction and inspection authority",
                "source": "https://www.legis.la.gov/legis/Law.aspx?d=97799"
            },
            {
                "title": "Municipal vs. parish jurisdiction split (e.g., St. George vs. Baton Rouge)",
                "note": "Newly incorporated municipalities (such as St. George inside East Baton Rouge Parish) operate their own permitting separate from the parish. Verify the exact incorporation boundary and submit to the correct portal \u2014 submitting to the wrong jurisdiction is a common cause of permit delays in mixed parish/city areas.",
                "applies_to": "Projects near municipal boundaries inside parishes",
                "source": "https://ceejaysells.com/blog/br-vs-st-george-who-handles-your-permit"
            },
            {
                "title": "Building permit 180-day expiration",
                "note": "Most Louisiana building permits expire 180 days (6 months) from issuance if no inspection is scheduled or no visible progress is made on site. Schedule at least a footing or rough-in inspection within that window to keep the permit alive, otherwise re-application and re-fees are required.",
                "applies_to": "Active permits with delayed start of work",
                "source": "https://statedataindex.com/building-permits/adu-construction/louisiana/vernon"
            },
            {
                "title": "ADU permits follow IRC; no statewide ministerial shot clock",
                "note": "Louisiana does not have an ADU-specific state law mandating ministerial review or fee waivers \u2014 ADUs are permitted as accessory dwellings under the 2021 IRC and local zoning. Expect full discretionary zoning review (variance, setback, parking) which can run 3\u20136 months before building permit review even begins.",
                "applies_to": "ADU and accessory dwelling jobs",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-louisiana"
            }
        ]
    },
    "KY": {
        "name": "Kentucky expert pack",
        "expert_notes": [
            {
                "title": "Statewide HVAC permit and license required",
                "note": "Kentucky was the first state to implement a statewide HVAC permitting and inspections program (effective Jan. 1, 2011) covering both residential and commercial work. HVAC installations must be performed under a state-issued HVAC license and pulled through the Department of Housing, Buildings and Construction (HBC), not just the local AHJ.",
                "applies_to": "All residential and commercial HVAC installations and replacements",
                "source": "https://dhbc.ky.gov/newstatic_info.aspx?static_id=335"
            },
            {
                "title": "State-licensed trades: electrical, plumbing, HVAC",
                "note": "Kentucky requires a state contractor license for electrical, plumbing, and HVAC trades; most other trades (general contracting) are not state-licensed and are regulated locally. Verify the trade contractor on each job holds a current HBC license before submitting permits \u2014 local AHJs will reject applications tied to an unlicensed trade.",
                "applies_to": "Electrical, plumbing, and HVAC scopes on any KY project",
                "source": "https://www.procore.com/library/kentucky-contractors-license"
            },
            {
                "title": "Plumbing residential permit base fee ($50)",
                "note": "Effective March 1, 2022, Kentucky residential (one- and two-family) plumbing installation permits carry a $50 base permit fee set by the state Division of Plumbing, in addition to per-fixture charges. Plumbing permits in KY are issued under the state plumbing program, separate from the local building permit.",
                "applies_to": "One- and two-family residential plumbing permits",
                "source": "https://dhbc.ky.gov/newstatic_info.aspx?static_id=337"
            },
            {
                "title": "2018 Kentucky Building Code and Residential Code in force",
                "note": "Kentucky has adopted the 2018 Kentucky Building Code (Second Edition) and the 2018 Kentucky Residential Code statewide, with state-specific amendments to the IBC/IRC base. Out-of-state plan sets prepared to a different IRC/IBC edition must be reconciled to the KY amendments before submittal.",
                "applies_to": "All new construction, additions, and alterations regulated by KBC/KRC",
                "source": "https://dhbc.ky.gov/newstatic_info.aspx?static_id=297"
            },
            {
                "title": "KY Division of Water floodplain permit (separate from local)",
                "note": "Under KRS 151, any demolition, repair, renovation, development, improvement, or construction in a floodplain requires a Kentucky Division of Water (KDOW) floodplain permit before work starts \u2014 in addition to the local floodplain administrator's permit. In Louisville, MSD is also a required floodplain permit authority alongside KDOW.",
                "applies_to": "Any work on parcels in a mapped floodplain (including post-flood repairs)",
                "source": "https://eec.ky.gov/Environmental-Protection/Water/Reports/FactSheets/DOW-FloodPlainManagement-ADA.pdf"
            },
            {
                "title": "ADUs are local \u2014 no statewide ADU law",
                "note": "Kentucky has no statewide ADU statute or shot clock; ADU permits run through local planning and building departments, with rules varying by city/county. Lexington (LFUCG) now requires a pre-construction meeting with Planning to review ADU regulations before permit submittal, which adds calendar time you must build into the schedule.",
                "applies_to": "ADU projects statewide, especially Lexington-Fayette and Louisville Metro",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-kentucky"
            },
            {
                "title": "KDOW general vs. individual floodplain permit",
                "note": "KDOW issues two floodplain permit types: a General Permit (faster, for development that meets pre-set conditions) and an Individual Permit (longer review, required when the project doesn't fit the general permit criteria). Identify which track applies early \u2014 an Individual Permit can add weeks of state review on top of the local building permit.",
                "applies_to": "Floodplain development scoping and schedule planning",
                "source": "https://eec.ky.gov/Environmental-Protection/Water/FloodDrought/Pages/UnderstandYourFloodHazards.aspx"
            },
            {
                "title": "Plan submission and KBC Section 121 fees",
                "note": "Per 2018 KBC Section 121, a permit is required to begin work for new construction, alteration, removal, or demolition, and plan review/inspection fees are set by the code. Use the HBC Plan Submission Application Guide to confirm what drawings, calcs, and forms must accompany a state-level plan review submittal to avoid intake rejection.",
                "applies_to": "Projects requiring state plan review submittals to HBC",
                "source": "https://dhbc.ky.gov/Documents/KHBC_PlanGuide.pdf"
            }
        ]
    },
    "OR": {
        "name": "Oregon expert pack",
        "expert_notes": [
            {
                "title": "ORS 455.467 permit review shot clock",
                "note": "Oregon law sets statutory timelines for building permit approval or disapproval. For jurisdictions with populations of 300,000 or more, the AHJ must act within 15 business days of receiving a complete application, or implement an alternative process. Track your complete-application date \u2014 exceeding the timeline is grounds to escalate.",
                "applies_to": "Residential and commercial building permits in larger Oregon jurisdictions",
                "source": "https://oregon.public.law/statutes/ors_455.467"
            },
            {
                "title": "2025 OEESC energy code in effect",
                "note": "The 2025 Oregon Energy Efficiency Specialty Code (OEESC) took effect January 1, 2025, adopting ASHRAE 90.1-2022 with Oregon amendments and replacing the 2021 edition. New commercial construction and alterations affecting the building envelope, HVAC, lighting, or service water heating must comply with the 2025 OEESC.",
                "applies_to": "New commercial construction and alterations after 2025-01-01",
                "source": "https://www.portland.gov/ppd/news/2025/1/10/new-oregon-energy-efficiency-specialty-code-oeesc-replaces-2021-edition"
            },
            {
                "title": "2023 ORSC residential code baseline",
                "note": "The 2023 Oregon Residential Specialty Code took effect April 1, 2024 and remains the baseline for one- and two-family dwellings and townhouses up to three stories. The 2026 ORSC is anticipated to take effect October 1, 2026 \u2014 projects nearing that boundary should confirm which edition governs based on complete-application date.",
                "applies_to": "One- and two-family dwellings and townhouses",
                "source": "https://www.energycodes.gov/status/states/oregon"
            },
            {
                "title": "CCB vs. BCD licensing split",
                "note": "Oregon splits contractor licensing between two agencies: the Construction Contractors Board (CCB) licenses general and specialty construction contractors and requires a residential or commercial bond ($25,000 for residential general contractors). The Building Codes Division (BCD) issues the individual and business licenses for electrical, plumbing, boiler, elevator, and manufactured dwelling trades. A trade contractor typically needs both a CCB license and the BCD trade license.",
                "applies_to": "All contractors performing residential or commercial work in Oregon",
                "source": "https://www.oregon.gov/ccb/pages/ccb%20license.aspx"
            },
            {
                "title": "Statewide ADU authorization (SB 1051 / SB 391)",
                "note": "SB 1051 (2017) and HB 2001 require Oregon cities (and SB 391, effective June 23, 2021, expanded to certain rural residential lots) to allow ADUs on single-family lots subject to reasonable siting and design standards. Most ADU applications are processed as Type I (ministerial) land use reviews, typically completed in roughly two weeks before building permit review begins.",
                "applies_to": "ADU projects on single-family residential lots",
                "source": "https://www.oregon.gov/lcd/Publications/ADU_Guidance_updatedSept2019.pdf"
            },
            {
                "title": "DSL/DEQ removal-fill permit for wetlands and waterways",
                "note": "Projects involving removal or fill in wetlands, streams, or other waters of the state require a permit from the Oregon Department of State Lands (DSL), in addition to any federal Section 404 authorization. DSL offers individual, general, and other permit types \u2014 wetland delineation and a DSL permit must typically be secured before the local building permit can be finalized.",
                "applies_to": "Construction with grading, fill, or removal in or near wetlands or waterways",
                "source": "https://www.nawm.org/pdf_lib/how_to_apply_for_a_permit_oregon.pdf"
            },
            {
                "title": "Statewide Planning Goal 18 coastal restrictions",
                "note": "Goal 18 prohibits new development on beaches, active foredunes, and dunes subject to severe erosion or flooding along the Oregon coastal shoreline. Projects in the coastal zone may also require a separate coastal zone consistency review through DLCD. Confirm Goal 18 mapping early \u2014 it can be a hard 'no-build' constraint regardless of zoning.",
                "applies_to": "Construction in the Oregon coastal zone, especially on dunes or shoreline parcels",
                "source": "https://www.oregon.gov/lcd/op/pages/goal-18.aspx"
            },
            {
                "title": "City vs. county building department jurisdiction",
                "note": "Oregon allows cities and counties to administer the state building code locally, but not every city runs its own building department \u2014 many defer to the county or to BCD. Before pulling permits, confirm whether the parcel falls under city, county, or state-administered (BCD) jurisdiction, since fees, submittal portals, and inspection scheduling differ.",
                "applies_to": "Determining the correct AHJ for any Oregon permit",
                "source": "https://www.oregon.gov/bcd/jurisdictions/pages/index.aspx"
            }
        ]
    },
    "OK": {
        "name": "Oklahoma expert pack",
        "expert_notes": [
            {
                "title": "Oklahoma adopts model codes at the state level via OUBCC",
                "note": "The Oklahoma Uniform Building Code Commission (OUBCC) adopts the base IRC, IBC, IECC, and related model codes by reference with state-specific amendments. Only the amendments are published on the OUBCC site \u2014 you must read the adopted model code together with the Oklahoma amendments to know what actually applies on a residential job.",
                "applies_to": "All residential and commercial permit work in Oklahoma",
                "source": "https://oklahoma.gov/oubcc/codes-and-rules.html"
            },
            {
                "title": "2018 IECC with Oklahoma amendments effective Sept 14, 2022",
                "note": "Oklahoma adopted the 2018 IECC with state amendments, effective September 14, 2022. New residential construction and additions must meet the amended 2018 IECC envelope, fenestration, and duct/air-sealing requirements \u2014 confirm the amendment package before submitting energy compliance documentation.",
                "applies_to": "New residential construction, additions, and conditioned-space alterations",
                "source": "https://insulationinstitute.org/wp-content/uploads/2022/12/OK_Code_2022_v6.pdf"
            },
            {
                "title": "2023 NEC adopted statewide by OUBCC",
                "note": "The Oklahoma Uniform Building Code Commission has adopted the 2023 National Electrical Code, and it is in effect statewide. All electrical permit submittals and inspections should be designed to the 2023 NEC unless a local amendment narrows it.",
                "applies_to": "Electrical permits and inspections statewide",
                "source": "https://oklahoma.gov/oubcc.html"
            },
            {
                "title": "CIB licensing required for electrical, mechanical/HVAC, and plumbing trades",
                "note": "The Oklahoma Construction Industries Board (CIB) issues and verifies the electrical, mechanical/HVAC, and plumbing licenses required to pull trade permits. A residential-only Mechanical HVAC or Plumbing license cannot be used on commercial work \u2014 commercial installation requires the commercial-class license. Verify license class and active status at cibverify.ok.gov before submitting.",
                "applies_to": "Trade permits (electrical, HVAC, plumbing) on residential and commercial work",
                "source": "https://oklahoma.gov/cib/frequently-asked-questions.html"
            },
            {
                "title": "Permits are issued by the local AHJ \u2014 city, town, or county",
                "note": "Under 74 O.S. \u00a7324.11, the building permit application is made to and the permit is issued by the city, town, or county with jurisdiction over the parcel. There is no state-level building permit office \u2014 confirm whether the parcel sits inside city limits or in unincorporated county before choosing the AHJ, because a wrong-jurisdiction submittal will be rejected.",
                "applies_to": "All Oklahoma building permit submittals \u2014 jurisdiction determination",
                "source": "https://law.justia.com/codes/oklahoma/title-74/section-74-324-11/"
            },
            {
                "title": "OWRB floodplain development permit required in SFHA",
                "note": "Any development in a designated floodplain in Oklahoma requires a local Floodplain Development Permit issued through the local floodplain administrator using OWRB-coordinated forms \u2014 this is separate from and typically a prerequisite to the building permit. Pull the OWRB Floodplain Permit Instructions, Application, and Checklist and route it before plan review to avoid mid-review holds.",
                "applies_to": "Any construction or substantial improvement within a FEMA Special Flood Hazard Area",
                "source": "https://oklahoma.gov/owrb/floodplain-management/forms-and-guidance.html"
            },
            {
                "title": "No statewide ADU preemption \u2014 local zoning controls",
                "note": "Oklahoma has no statewide ADU statute that overrides municipal zoning. ADU eligibility, setbacks, owner-occupancy, parking, and review timeline are set entirely by the city or county. For example, Oklahoma City adopted its accessory dwelling ordinance on May 20, 2025 \u2014 always pull the specific municipal ordinance rather than assuming statewide rules.",
                "applies_to": "ADU and accessory dwelling projects",
                "source": "https://www.okc.gov/Infrastructure-Development/Development-Planning/Code-Update/Accessory-Dwellings"
            },
            {
                "title": "State Fire Marshal plan review \u2014 no deferred submittals until base plans approved",
                "note": "For projects that require Oklahoma State Fire Marshal plan review, deferred submittals (sprinkler, alarm, hood, etc.) will not be accepted for review until the base construction plans have been reviewed, approved, and issued. Sequence deferred trade packages after base-plan approval to avoid wasted review cycles.",
                "applies_to": "Commercial and assembly projects subject to State Fire Marshal review",
                "source": "https://oklahoma.gov/fire/plan-reviews.html"
            }
        ]
    },
    "CT": {
        "name": "Connecticut expert pack",
        "expert_notes": [
            {
                "title": "Connecticut ADUs allowed as-of-right on single-family lots",
                "note": "State law requires municipalities to allow ADUs as-of-right on single-family residential properties, meaning no special-use permit or variance is needed in most cases. Building, zoning, and health permits are still required, so confirm local zoning conformance and whether the town opted out before scoping the job.",
                "applies_to": "ADU jobs on single-family parcels",
                "source": "https://bioshomes.com/accessory-dwelling-units/"
            },
            {
                "title": "State Building Code adoption based on 2021 IRC/IBC/IECC with CT amendments",
                "note": "Connecticut's State Building Code is based on the 2021 I-codes (IRC, IBC, IECC) with Connecticut amendments and General Statute requirements. The Codes and Standards Committee began accepting CCPs for the 2024 IRC and IECC starting September 1, 2024, so verify which edition applies at permit submittal since the next adoption cycle is in progress.",
                "applies_to": "All new construction, alterations, and additions",
                "source": "https://portal.ct.gov/das/office-of-state-building-inspector/building-and-fire-code-adoption-process"
            },
            {
                "title": "DCP Home Improvement Contractor (HIC) registration required",
                "note": "Any contractor performing residential work must register annually with the Department of Consumer Protection as a Home Improvement Contractor. Registration expires every March 31, the renewal fee is $220, and general liability insurance is required. The HIC number must appear on all advertising and contracts or the contract may be unenforceable.",
                "applies_to": "Residential remodeling, additions, and repairs",
                "source": "https://portal.ct.gov/dcp/license-services-division/all-license-applications/home-improvement-applications"
            },
            {
                "title": "Major Contractor registration for structural work over threshold",
                "note": "A 'major contractor' \u2014 anyone engaged in construction, structural repair, structural alteration, dismantling, or demolition above statutory thresholds \u2014 must register separately with DCP in addition to any HIC registration. Misclassifying a structural job as routine home improvement is a common compliance gotcha that can stall permit issuance.",
                "applies_to": "Structural alteration, demolition, and large residential/commercial projects",
                "source": "https://portal.ct.gov/dcp/common-elements/consumer-facts-and-contacts/major-contractor"
            },
            {
                "title": "Separate plumbing and pipefitting trade licenses with October 31 expiration",
                "note": "Plumbing and pipefitting work requires a state-issued trade license through DCP, separate from the HIC registration. Licenses expire annually on October 31 (Contractor renewal $150, Journeyperson $120). Verify the trade license is current before pulling sub-permits \u2014 an expired license will block inspection sign-off.",
                "applies_to": "Plumbing, pipefitting, and HVAC subcontractor permits",
                "source": "https://portal.ct.gov/dcp/license-services-division/all-license-applications/plumbing-and-pipefitting-licensing"
            },
            {
                "title": "DEEP coastal and tidal wetlands permits required separately from local building permit",
                "note": "DEEP's Land & Water Resources Division regulates all activities in tidal wetlands and tidal, coastal, or navigable waters under the Structures, Dredging, and Fill statutes. A local building permit does not authorize work waterward of the coastal jurisdiction line \u2014 secure the DEEP coastal permit first to avoid stop-work orders on shoreline projects.",
                "applies_to": "Shoreline, dock, seawall, and coastal construction",
                "source": "https://portal.ct.gov/DEEP/Coastal-Resources/Coastal-Permitting/Overview-of-the-Connecticut-Coastal-Permit-Program"
            },
            {
                "title": "Municipal-only permitting jurisdiction (no county layer)",
                "note": "Connecticut has no functioning county government for permitting; municipalities have only the powers expressly conferred by general statutes or special act. Building permits, zoning, and inland wetlands approvals are issued by the town/city building official and local commissions \u2014 there is no county building department to fall back on, so confirm the correct municipal AHJ early.",
                "applies_to": "All Connecticut permit jurisdiction lookups",
                "source": "https://www.cga.ct.gov/2023/pub/chap_098.htm"
            },
            {
                "title": "DEEP permit timeframe categories (immediate, 3, 6, 12 months)",
                "note": "DEEP-issued permits are categorized into four statutory review timeframes \u2014 immediate, within 3 months, within 6 months, or within 12 months \u2014 based on permit type and complexity. Build the schedule around the applicable category rather than assuming a uniform turnaround, especially when a project triggers both a local building permit and a DEEP environmental permit.",
                "applies_to": "Projects requiring DEEP environmental or land-use permits",
                "source": "https://portal.ct.gov/-/media/DEEP/Permits_and_Licenses/Factsheets_General/PermittingTimeframespdf.pdf"
            }
        ]
    },
    "UT": {
        "name": "Utah expert pack",
        "expert_notes": [
            {
                "title": "Utah adopted 2021 IRC/IBC/IECC with state amendments",
                "note": "Utah has adopted the 2021 International Codes (IRC, IBC, IECC) statewide. The 2021 IECC with Utah-specific amendments took effect 07/01/2024 for residential energy compliance. Confirm which amendment cycle the AHJ is enforcing before submitting plans, since legislative amendments are revisited each session.",
                "applies_to": "All new construction, additions, and alterations subject to the State Construction Code",
                "source": "https://www.energycodes.gov/status/states/utah"
            },
            {
                "title": "DOPL contractor license required \u2014 trade scope matters",
                "note": "Utah contractors must be licensed through the Division of Professional Licensing (DOPL). General B100, R100 (Residential/Small Commercial), and E100 classifications cannot perform plumbing, electrical, or mechanical work \u2014 those require separately licensed S-classification specialty contractors (e.g., S350 for HVAC). Verify all subs at the state license lookup before pulling permits.",
                "applies_to": "All licensed trade work statewide",
                "source": "https://commerce.utah.gov/dopl/contracting/apply-for-a-license/general-contractor/"
            },
            {
                "title": "Verify license status at Utah's License Lookup before subbing work",
                "note": "Utah maintains a public Licensee Lookup & Verification System for confirming a contractor or tradesperson's active DOPL license, classification, and disciplinary status. Run this check before signing subcontracts or submitting permit applications listing subs \u2014 an expired or wrong-classification license is a common cause of permit rejection.",
                "applies_to": "Any project relying on subcontractors or specialty trades",
                "source": "https://secure.utah.gov/llv/search/index.html"
            },
            {
                "title": "Pre-licensure education for specialty and general contractors",
                "note": "Specialty contractors must complete a 25-hour pre-licensure course; general contractors (E100, R100, B100) and plumbing/electrical contractors must complete a 30-hour course. Plan for this lead time when adding a new classification or onboarding a qualifier \u2014 applications without proof of the course will be rejected.",
                "applies_to": "New license applications and added classifications",
                "source": "https://commerce.utah.gov/dopl/contracting/apply-for-a-license/specialty-contractor-license/"
            },
            {
                "title": "Statewide ADU permitting \u2014 internal/attached ADUs are permitted use",
                "note": "Since 10/01/2021, Utah law makes internal and attached ADUs on single-family lots a permitted use statewide, removing the conditional-use approval step in most municipalities. A building permit is still required, and detached ADUs and local design/parking standards may still apply \u2014 check the AHJ's adopted ADU ordinance.",
                "applies_to": "ADU projects on single-family lots",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-utah"
            },
            {
                "title": "Wildland Urban Interface (WUI) map and code compliance",
                "note": "Per HB 48 (2025), local jurisdictions must adopt a WUI map by 01/01/2026 and update local building codes referencing the International Wildland Urban Interface Code. Parcels mapped as WUI can trigger ignition-resistant materials, defensible space, and an annual high-risk fee assessed against the property owner. Confirm WUI status during site review.",
                "applies_to": "Projects in or near mapped wildland urban interface areas",
                "source": "https://saltlakecountyem.gov/utah-hb-48-wildland-urban-interface-modifications/"
            },
            {
                "title": "Floodplain Development Permit is separate from building permit",
                "note": "Any development in a mapped Special Flood Hazard Area requires a Floodplain Development Permit demonstrating compliance with NFIP performance standards (44 CFR) in addition to the building permit. Use the state checklist to assemble elevation certificates, lowest-floor data, and engineered design before submittal \u2014 missing items are a frequent cause of delay.",
                "applies_to": "Construction, additions, fill, or grading within a mapped floodplain",
                "source": "https://floodhazards.utah.gov/wp-content/uploads/2023/06/Floodplain-Development-Permit-Checklist-MG-3-30-23.pdf"
            },
            {
                "title": "City vs. county AHJ split \u2014 confirm jurisdiction before applying",
                "note": "Utah permitting is split between municipal building departments and county building departments for unincorporated areas. In the Salt Lake region, the Greater Salt Lake Municipal Services District (MSD) handles permits via the CityWorks portal for member townships and unincorporated areas. Confirm which AHJ the parcel falls under \u2014 submitting to the wrong office is a common first-week mistake.",
                "applies_to": "All permit applications, especially near city/county boundaries",
                "source": "https://msd.utah.gov/205/Building-Services"
            }
        ]
    },
    "IA": {
        "name": "Iowa expert pack",
        "expert_notes": [
            {
                "title": "Iowa State Building Code is voluntary for local jurisdictions",
                "note": "Adoption of the Iowa State Building Code is voluntary for cities and counties, but certain provisions (notably energy) apply statewide. Confirm with each AHJ which code edition and amendments they enforce before submitting plans, since requirements vary city-to-city.",
                "applies_to": "All Iowa residential and commercial permit jobs",
                "source": "https://www.mwalliance.org/iowa/iowa-building-energy-codes"
            },
            {
                "title": "Local jurisdiction owns enforcement when state code is adopted",
                "note": "Any local jurisdiction that adopts the state building code by local ordinance may further adopt provisions for administration and enforcement. This means inspections, fees, and appeal procedures are set locally even when the underlying technical code is the state code.",
                "applies_to": "Permit applications in jurisdictions that have locally adopted the Iowa State Building Code",
                "source": "https://www.law.cornell.edu/regulations/iowa/Iowa-Admin-Code-r-661-300-6"
            },
            {
                "title": "Contractor registration with DIAL is mandatory",
                "note": "Iowa law requires construction contractors and businesses performing construction work to be registered with the Department of Inspections, Appeals, & Licensing (DIAL). Annual registration fee is $50 and is non-refundable. Permits will be denied if the contractor is not actively registered.",
                "applies_to": "All contractors pulling permits in Iowa",
                "source": "https://dial.iowa.gov/licenses/building/contractors"
            },
            {
                "title": "Trade licensing required before permit issuance",
                "note": "Plumbing, mechanical (HVAC), and electrical work must be performed by a state-licensed trade professional. Many AHJs (e.g., Iowa City) require that only Master electricians, Master plumbers, and Master HVAC contractors can pull the related permit. Verify license tier matches scope.",
                "applies_to": "Plumbing, mechanical, and electrical permit pulls",
                "source": "https://www.icgov.org/government/departments-and-divisions/neighborhood-and-development-services/development-services/building-inspection-services/licensing-requirements"
            },
            {
                "title": "Floodplain development permit required near waterways",
                "note": "For construction along most Iowa waterways, a floodplain development permit from Iowa DNR is required in addition to the local building permit. Permits are also generally required for dams and certain in-stream work. Run the DNR PERMT screening before site work to avoid stop-work orders.",
                "applies_to": "Construction in or near floodplains, streams, rivers, and lakes",
                "source": "https://www.iowadnr.gov/environmental-protection/land-quality/flood-plain-management/development-permits"
            },
            {
                "title": "Sovereign Lands Permit for state-owned land or water",
                "note": "A Sovereign Land Permit from Iowa DNR is required to conduct construction on state-owned lands or water (most navigable rivers, lakes, and beds). Apply through the DNR PERMT site. This is separate from, and additional to, any floodplain permit.",
                "applies_to": "Docks, bank stabilization, utility crossings, and any construction on state-owned land or water",
                "source": "https://www.iowadnr.gov/environmental-protection/land-quality/sovereign-lands-permits"
            },
            {
                "title": "ADU review cannot exceed normal residential review timeline",
                "note": "Iowa state law requires that the review timeline for an ADU permit cannot be longer than the normal residential review timeline for that jurisdiction. Both zoning approval and a building permit are required, and the lot must be confirmed eligible with site plans showing placement.",
                "applies_to": "ADU permit applications statewide",
                "source": "https://cdn.ymaws.com/www.aiaiowa.org/resource/resmgr/clientresources/guidetobuildinganadu.pdf"
            },
            {
                "title": "Some Iowa counties have no building permits or zoning",
                "note": "Unlike most states, several Iowa counties have no county-level zoning or building permit process \u2014 building permits are typically issued by the city, and counties like Marion only issue zoning permits without doing building inspections. Always confirm whether the parcel falls inside city limits and which (if any) AHJ inspects.",
                "applies_to": "Rural and unincorporated parcels in Iowa",
                "source": "https://www.marioncountyiowa.gov/zoning/faq/"
            }
        ]
    },
    "NV": {
        "name": "Nevada expert pack",
        "expert_notes": [
            {
                "title": "Clark County plan review timeframe goals",
                "note": "Clark County publishes target first-review timeframes: Standard Plan and full Commercial reviews aim for 21 days, Commercial Minor 14 days, and Commercial 7 Day reviews 7 days. Use these as escalation benchmarks when a submittal stalls past target.",
                "applies_to": "Permit submittals in Clark County (Las Vegas metro)",
                "source": "https://www.clarkcountynv.gov/government/departments/building___fire_prevention/plan_review/plan-review-timelines"
            },
            {
                "title": "Statewide 2024 ICC code adoption with grace period",
                "note": "Nevada Public Works adopted the 2024 ICC code editions, with any projects submitted after the six-month grace period ending June 30, 2025 required to be designed under the new codes. Washoe County mirrored this with the 2024 ICC and 2023 NEC effective July 1, 2025.",
                "applies_to": "New construction and alteration projects designed in 2025-2026",
                "source": "https://publicworks.nv.gov/uploadedFiles/publicworksnvgov/content/Documents/Permitting_Code_Enforcement/2024%20Adopted_Codes.pdf"
            },
            {
                "title": "Southern Nevada Energy Conservation Code 2024",
                "note": "Clark County and other Southern Nevada jurisdictions adopted the S. NV ECC 2024 (based on the 2024 IECC with regional amendments) effective January 11, 2026. Energy compliance documentation must reflect the local amendments, not just unmodified IECC.",
                "applies_to": "New construction and conditioned-space alterations in Clark County and Southern Nevada",
                "source": "https://up.codes/viewer/clark-nevada/s-nv-energy-conservation-code-2024"
            },
            {
                "title": "HERS Index compliance path in Southern Nevada",
                "note": "Southern Nevada code jurisdictions accept a HERS Index Option (amended Section 406 Energy Rating Index) as an alternative compliance path to the energy code. This can simplify residential energy compliance versus prescriptive or performance routes.",
                "applies_to": "Residential energy compliance in Southern Nevada jurisdictions",
                "source": "https://www.resnet.us/articles/southern-nv-code-jurisdictions-accept-a-hers-index-option-to-energy-code/"
            },
            {
                "title": "NSCB monetary limit and classification scope",
                "note": "Nevada State Contractors Board licenses are issued under Class A (General Engineering), Class B (General Building), and Class C (specialty, with 42 subclassifications). License number AND monetary limit must be on every contract and bid; exceeding the limit or working outside your classification voids the license for that job.",
                "applies_to": "All licensed contractors bidding or contracting in Nevada",
                "source": "https://www.nvcontractorsboard.com/licensing/license-classifications/"
            },
            {
                "title": "City vs county AHJ split \u2014 no statewide residential permit",
                "note": "Nevada has statewide baseline codes enforced by the State Fire Marshal, but residential building permits are handled entirely by local city and county building departments. A parcel falls under one AHJ or the other (not both); confirm jurisdiction by address before submitting.",
                "applies_to": "All residential permit jobs in Nevada",
                "source": "https://permitsguide.com/nevada"
            },
            {
                "title": "Floodplain management coordination with NDWR",
                "note": "The Nevada Division of Water Resources administers the state floodplain management program. Construction in mapped floodplains requires local floodplain development permits aligned with NDWR/NFIP standards before a building permit can be finalized.",
                "applies_to": "Projects on parcels within mapped FEMA/NDWR floodplains",
                "source": "https://water.nv.gov/index.php/programs/floodplain-management"
            },
            {
                "title": "C-1 Plumbing and Heating classification for HVAC",
                "note": "HVAC contractors in Nevada must hold the appropriate NSCB Classification C license (commonly C-1 Plumbing and Heating, or related C-21 refrigeration/A-C subclassifications). Trade-specific permits will not be issued to a contractor whose classification does not cover the scope of work.",
                "applies_to": "HVAC, plumbing, and refrigeration scopes",
                "source": "https://www.servicetitan.com/licensing/hvac/nevada"
            }
        ]
    },
    "AR": {
        "name": "Arkansas expert pack",
        "expert_notes": [
            {
                "title": "Arkansas HB 1503 statewide ADU legalization deadline",
                "note": "Arkansas HB 1503 requires cities to legalize accessory dwelling units statewide by January 1. If a local ordinance still bans ADUs outright after that deadline, state preemption applies and the local prohibition is unenforceable \u2014 escalate to the city attorney before redesigning the project.",
                "applies_to": "ADU jobs in any Arkansas municipality",
                "source": "https://www.housingwire.com/articles/arkansas-adu-law-sets-fast-approaching-housing-deadline/"
            },
            {
                "title": "Arkansas ADU permit timing expectation",
                "note": "Expect roughly four to twelve weeks from a complete ADU application to permit issuance in Arkansas, assuming plans meet local development standards. Build this window into client schedules and flag review delays beyond 12 weeks as escalation-worthy.",
                "applies_to": "ADU jobs",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-arkansas"
            },
            {
                "title": "Residential builder license required over $2,000",
                "note": "A residential builders license from the Arkansas Contractors Licensing Board is required to build a single-family residence when the project cost (materials included) exceeds $2,000. Unlicensed work above this threshold is a common cause of permit denial and stop-work orders.",
                "applies_to": "Single-family residential new construction and major remodels",
                "source": "https://labor.arkansas.gov/licensing/arkansas-contractors-licensing-board/apply-for-contractors-license-registration/"
            },
            {
                "title": "2021 Arkansas Fire Prevention Code governs building, fire, and residential",
                "note": "The 2021 Arkansas Fire Prevention Code is a consolidated volume that includes the Fire Code, Building Code, and Residential Code (based on IBC 2021 with Arkansas amendments). Each district, county, or municipality may only adopt and enforce the provisions of the AFPC \u2014 local codes that deviate from the AFPC are not enforceable.",
                "applies_to": "All permitted construction statewide",
                "source": "https://sas.arkansas.gov/wp-content/uploads/CurrentCodes072023.pdf"
            },
            {
                "title": "Arkansas Energy Code adoption is mandatory for permit-issuing jurisdictions",
                "note": "All counties, cities, or municipalities that issue building permits for new building construction are required to adopt the Arkansas Energy Code. Confirm which edition the AHJ has adopted (2021 amendments are current draft; older jurisdictions may still enforce 2014/IECC 2009 supplements) before sizing envelope and HVAC.",
                "applies_to": "New construction and additions in any permit-issuing AHJ",
                "source": "https://www.adeq.state.ar.us/energy/initiatives/pdfs/DRAFT%202021%20Arkansas%20Energy%20Code%20Amendments%20and%20Supplements%20vMar3.pdf"
            },
            {
                "title": "100-year floodplain triggers separate county Floodplain Development Permit",
                "note": "For sites in a 100-year floodplain, a Floodplain Development Permit from the county Floodplain Administrator is required in addition to the building permit. ADEQ permit applications and federal NEPA-tied projects will not be accepted without it, and below-grade utility work can still trigger local floodplain permitting.",
                "applies_to": "Any construction within a FEMA 100-year floodplain",
                "source": "https://www.adeq.state.ar.us/water/permits/pdfs/apppitdrilling_permit_p1-5.pdf"
            },
            {
                "title": "AGFC permit required on Commission-controlled lakes",
                "note": "Constructing or possessing platforms, piers, boat slides, boathouses, or irrigation systems on Arkansas Game & Fish Commission-owned or controlled lakes (including lake management areas) requires an AGFC permit with specification compliance \u2014 separate from the building permit.",
                "applies_to": "Waterfront work on AGFC-controlled lakes",
                "source": "https://apps.agfc.com/regulations/detail/04c2d5c7-4ec7-430d-bbef-03338040b8e2/"
            },
            {
                "title": "Separate boards for HVACR, electrical, and plumbing licensing",
                "note": "Arkansas splits trade licensing across boards: contractors (501-372-4661), electricians (501-682-4549), and plumbing/HVACR (separate Department of Labor and Licensing division). Each trade subcontractor must hold the correct board's license \u2014 a general residential builder license does not cover trade work.",
                "applies_to": "Any project using HVAC, electrical, or plumbing subcontractors",
                "source": "https://labor.arkansas.gov/labor/code-enforcement/hvac-r/"
            }
        ]
    },
    "KS": {
        "name": "Kansas expert pack",
        "expert_notes": [
            {
                "title": "Kansas is a home-rule state \u2014 no statewide residential building code",
                "note": "Kansas has no statewide residential building code. Code adoption, permit issuance, and inspections are entirely controlled by local jurisdictions (city or county), and some rural counties have no building code at all. Always confirm the AHJ for the parcel before pulling plans.",
                "applies_to": "All Kansas residential and light-commercial projects",
                "source": "https://permitsguide.com/kansas"
            },
            {
                "title": "No state contractor license \u2014 verify local trade licensing",
                "note": "Kansas does not have a state contractor license board and does not license HVAC contractors at the state level. Electrical, plumbing, HVAC, and general contractor licensing is handled city-by-city and county-by-county (e.g., Sedgwick County MABCD, Johnson County, KCMO Permits Division). Pulling a permit with the wrong class license is a frequent rejection cause.",
                "applies_to": "All trades on Kansas projects",
                "source": "https://www.servicetitan.com/licensing/hvac/kansas"
            },
            {
                "title": "State energy baseline is 2006 IECC \u2014 local amendments vary widely",
                "note": "The State of Kansas has adopted the 2006 IECC as the baseline energy code, but local jurisdictions retain authority to adopt newer or amended codes. Do not assume a single energy code applies statewide; check the AHJ's current adoption and any local amendments before sizing envelope, mechanical, and fenestration assemblies.",
                "applies_to": "New construction, additions, and conditioned-space alterations",
                "source": "https://www.kcc.ks.gov/kansas-energy-office/ks-building-energy-codes"
            },
            {
                "title": "Kansas City, MO rolled back portions of its 2021 IECC adoption (Feb 2026)",
                "note": "On Feb 5, 2026 the Kansas City Council voted 7\u20136 to amend its 2021 IECC adoption, loosening insulation, air-sealing, and other residential energy provisions. Projects in KCMO permitted under the original 2021 IECC rollout may face different requirements than projects permitted after the amendment \u2014 verify the effective code at application date.",
                "applies_to": "Kansas City, MO residential new construction permits",
                "source": "https://flatlandkc.org/news-issues/hoping-for-more-affordable-housing-kansas-city-rolls-back-energy-efficiency-codes/"
            },
            {
                "title": "60-day permit review shot clock proposed in HB 2088",
                "note": "Kansas HB 2088 (2025\u201326 session) would impose a 60-day review window on building permit applications, with an exception only if the applicant agrees in writing to phased permitting. Track adoption status before relying on it; if enacted in the AHJ, document complete-application date to preserve escalation grounds.",
                "applies_to": "Permit timing and escalation strategy",
                "source": "https://kslegislature.gov/li/b2025_26/measures/documents/supp_note_hb2088_01_0000.pdf"
            },
            {
                "title": "Stream and floodplain work needs KDA Division of Water Resources approval",
                "note": "The Kansas Department of Agriculture, Division of Water Resources requires a permit for construction, modification, or repair of dams 25 ft or higher (or 6 ft or higher in certain cases) and for work affecting streams or floodplains. Federal USACE Clean Water Act permits and local floodplain permits may also stack on top \u2014 coordinate all three before breaking ground near water.",
                "applies_to": "Projects in or near streams, wetlands, dams, or floodplains",
                "source": "https://www.agriculture.ks.gov/divisions-programs/division-of-water-resources/water-structures/stream-and-floodplain-permits"
            },
            {
                "title": "Local floodplain regulations must meet NFIP minimums",
                "note": "Per K.S.A. 12-766k, any local floodplain regulations must comply with the minimum requirements of the National Flood Insurance Act of 1968. Check the AHJ's floodplain overlay district and any hydrologic/hydraulic study requirements (e.g., Douglas County requires an approved H&H study for UGA + floodplain overlay parcels) before committing to a foundation design.",
                "applies_to": "Projects in floodplain overlay districts or NFIP-mapped zones",
                "source": "https://www.kslegislature.gov/li/b2025_26/statute/012_000_0000_chapter/012_007_0000_article/012_007_0066_section/012_007_0066_k/"
            },
            {
                "title": "ADUs in Kansas \u2014 administrative review largely replaced by standard building permit",
                "note": "In most Kansas jurisdictions ADUs now move through a standard building permit rather than a separate administrative review, which shortens the timeline. Some counties (e.g., Johnson County) still apply administrative review with performance standards before the building permit. Confirm the local path before quoting a schedule to the homeowner.",
                "applies_to": "ADU projects in Kansas",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-kansas"
            }
        ]
    },
    "MS": {
        "name": "Mississippi expert pack",
        "expert_notes": [
            {
                "title": "Local-option code adoption (no statewide mandate)",
                "note": "Mississippi has no uniform statewide building code mandate for residential work. Per Miss. Code 21-19-25, municipalities may adopt building/plumbing/electrical/gas codes at their discretion, and HB 331 confirms counties have the same option. Always confirm which edition (and which amendments) the specific city or county has adopted before pulling permits \u2014 adjacent jurisdictions can be on different code cycles.",
                "applies_to": "All permitted work statewide",
                "source": "https://law.justia.com/codes/mississippi/title-21/chapter-19/section-21-19-25/"
            },
            {
                "title": "MBCC three-year cycle but local enforcement controls",
                "note": "The Mississippi Building Code Council adopts and updates IBC/IRC/IECC on a three-year cycle, but local jurisdictions decide whether to enforce them. In practice, locals are operating on the 2018, 2015, or even 2012 editions. Do not assume the latest IRC/IBC applies \u2014 verify the adopted edition with the AHJ at intake.",
                "applies_to": "Code-cycle-sensitive scopes (energy, egress, fire-resistive, structural)",
                "source": "https://awc.org/priorities/codes-standards/adoption/mississippi/"
            },
            {
                "title": "DOR permit prerequisite for residential contractors",
                "note": "Per the MSBOC Residential Laws and Rules, all residential contractors must possess a permit from the Mississippi Department of Revenue before a building permit can be issued in the State of Mississippi. Missing the DOR permit is a common reason intake clerks reject an otherwise complete residential application.",
                "applies_to": "Residential contractor permit applications",
                "source": "https://www.msboc.us/wp-content/uploads/2022/07/RESIDENTIAL-LAWS-AND-RULES-REVISED-2022-Web-Version.pdf"
            },
            {
                "title": "$10,000 trade-work threshold for state licensing",
                "note": "MSBOC guidance: residential electrical, plumbing, or HVAC work under $10,000 does not require a state contractor license, but the local building department must still be contacted and may require its own license/permit. Plumbing contractors above the threshold must hold a Mississippi State Plumbing Contractors license. Confirm both state and local requirements before quoting trade scopes.",
                "applies_to": "Residential electrical, plumbing, and HVAC scopes",
                "source": "https://www.msboc.us/one-stop-shop/"
            },
            {
                "title": "MSBOC licensing for commercial and residential contractors and roofers",
                "note": "Commercial and residential contractors and roofers performing or bidding work in Mississippi must be licensed by the Mississippi State Board of Contractors. Verify the contractor's MSBOC license class and status before submitting \u2014 AHJs frequently cross-check at permit issuance.",
                "applies_to": "Contractor and roofer permit applications",
                "source": "https://www.msboc.us/"
            },
            {
                "title": "Coastal Zone wetlands permit via MDMR portal",
                "note": "Applications for wetland impacts within the Mississippi Coastal Zone (Hancock, Harrison, Jackson counties) must be submitted to the Mississippi Department of Marine Resources electronically through the Wetlands Permitting Portal \u2014 not to the local building department. Identify wetland/tidal impacts early; this is a parallel permit, not a building-department add-on.",
                "applies_to": "Coastal Zone projects with wetland or tidal impacts",
                "source": "https://dmr.ms.gov/permitting/"
            },
            {
                "title": "MDEQ environmental permit coordination",
                "note": "MDEQ's Environmental Permits Division oversees most state environmental permitting (stormwater/CGP, air, water). Construction disturbing land area typically needs MDEQ general-permit coverage via Notice of Intent before ground-breaking \u2014 coordinate with the building permit timeline so the local department doesn't issue stop-work for missing NOI coverage.",
                "applies_to": "Land-disturbing construction projects",
                "source": "https://www.mdeq.ms.gov/permits/environmental-permits-division/applications-forms/generalpermits/"
            },
            {
                "title": "Realistic ADU permit timeline: 30\u201390 days, no state portal",
                "note": "Mississippi has no state-level ADU permit portal and no statutory ADU shot clock. Plan 30 to 90 days from application to final approval depending on the county's workload and which codes the AHJ has adopted. Set client expectations against the local jurisdiction's queue, not a state-mandated deadline.",
                "applies_to": "ADU and accessory-structure permits",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-mississippi"
            }
        ]
    },
    "NM": {
        "name": "New Mexico expert pack",
        "expert_notes": [
            {
                "title": "2021 New Mexico Residential Energy Conservation Code in effect",
                "note": "The 2021 New Mexico Residential Energy Conservation Code (14.7.6 NMAC, based on 2021 IECC with state amendments) was adopted January 30, 2024 with an effective date of July 30/31, 2024. New residential construction and additions/alterations that create or modify conditioned space must comply with the current envelope, fenestration, and mechanical provisions \u2014 older 2018 NMECC submittals will be rejected.",
                "applies_to": "New residential construction, additions, and conditioned-space alterations statewide",
                "source": "https://www.rld.nm.gov/wp-content/uploads/2024/01/2021-New-Mexico-Residential-Energy-Conservation-Code-NMAC-14.7.6-effective-7.30.24.pdf"
            },
            {
                "title": "CID licensing required for mechanical and water heater work \u2014 no owner-builder exemption",
                "note": "New Mexico's Construction Industries Division (CID) does not allow owner-builders to perform mechanical or water heater installations anywhere in the state \u2014 a properly classified licensed contractor (e.g., MM-98 plumbing/mechanical, ME for electrical) must pull the permit. Electrical wiring permits and inspections are required even if a homeowner does the work themselves on their primary residence.",
                "applies_to": "Mechanical, plumbing, water heater, and electrical scopes on residential jobs",
                "source": "https://www.rld.nm.gov/construction-industries/frequently-asked-questions/"
            },
            {
                "title": "HB 425 removed public hearing / planning-board step for ADUs",
                "note": "Under House Bill 425, ADU permits in New Mexico no longer require a public hearing or planning board review \u2014 applications run through standard zoning and building permit review. If a local AHJ tries to route an ADU through a discretionary hearing or board, that conflicts with state law and is grounds to escalate.",
                "applies_to": "ADU permit applications statewide",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-new-mexico"
            },
            {
                "title": "County vs. municipal vs. CID permit jurisdiction split",
                "note": "In New Mexico, permits are issued by the city if inside municipal limits, by the county if the county has adopted zoning/building ordinances under NMSA 3-21-2, or directly by state CID for jurisdictions that have not assumed plan review. Confirm the AHJ before submittal \u2014 submitting to the wrong office is a common cause of rejected applications and lost time.",
                "applies_to": "All residential and commercial permit submittals statewide",
                "source": "https://law.justia.com/codes/new-mexico/chapter-3/article-21/section-3-21-2/"
            },
            {
                "title": "NM Residential Code 105.2 permit exemptions",
                "note": "Per New Mexico Residential Code 105.2, a building permit is not required for one-story detached accessory structures used as tool/storage sheds and similar uses under the listed size threshold, fences under the code height, and other narrowly enumerated items. Electrical, plumbing, and mechanical work on those structures still requires its own trade permit.",
                "applies_to": "Small accessory structures, sheds, and minor residential work",
                "source": "https://www.rld.nm.gov/wp-content/uploads/2021/06/BLDG-RES-GUIDE-jrr-03-09-12.pdf"
            },
            {
                "title": "Acequia and OSE water rights coordination",
                "note": "Construction near acequias or that affects diversions, ditches, or surface/ground water requires coordination with the Office of the State Engineer (OSE) and the local acequia association. Acequia easements and 'time immemorial' Pueblo/community water rights can predate parcel ownership and constrain grading, driveways, and drainage near the ditch.",
                "applies_to": "Projects on parcels adjacent to acequias, ditches, or with water-rights implications",
                "source": "https://www.ose.nm.gov/WR/forms.php"
            },
            {
                "title": "Wildfire overlay and community mitigation mapping",
                "note": "Several New Mexico counties use wildfire overlay districts and CWPP-based risk maps to impose defensible-space, ignition-resistant material, and access requirements. Check the EMNRD Forestry Community Mitigation Maps for the parcel before finalizing exterior assemblies \u2014 overlay triggers can drive ignition-resistant siding/roof, ember-resistant vents, and 5-ft non-combustible zones.",
                "applies_to": "Residential projects in wildfire-prone counties and overlay districts",
                "source": "https://www.emnrd.nm.gov/sfd/fire-prevention-programs/community-mitigation-maps/"
            },
            {
                "title": "Albuquerque Historic Protection Overlay (HPO) review",
                "note": "Within City of Albuquerque HPO zones, exterior alterations, additions, demolitions, and new construction require Landmarks Commission or staff-level certificate review before a building permit is issued. Plan for the extra review cycle and design-guideline compliance \u2014 submitting to building permit first without HPO sign-off causes mandatory rework.",
                "applies_to": "Albuquerque projects within a Historic Protection Overlay zone",
                "source": "https://www.cabq.gov/planning/boards-commissions/landmarks-commission/historic-protection-overlay-zones"
            }
        ]
    },
    "NE": {
        "name": "Nebraska expert pack",
        "expert_notes": [
            {
                "title": "Nebraska Energy Code is the 2018 IECC (effective July 1, 2020)",
                "note": "Nebraska's adopted state energy code is the 2018 IECC, effective July 1, 2020. If a local jurisdiction has not adopted its own energy code, the Nebraska Department of Water, Energy, and Environment's state code applies by default. Build envelope, blower-door, and duct-leakage compliance to 2018 IECC unless the AHJ has formally adopted something else.",
                "applies_to": "New residential construction, additions, and ADUs statewide",
                "source": "https://dwee.nebraska.gov/state-energy-information/energy-codes"
            },
            {
                "title": "Local energy code amendments must be reported within 30 days",
                "note": "A county, city, or village may amend or modify the state energy code, but must notify the State Energy Office within 30 days after adoption. Before submitting plans, confirm directly with the AHJ whether they have adopted local amendments \u2014 assuming the base 2018 IECC applies has burned crews when a town quietly amended envelope or mechanical provisions.",
                "applies_to": "All jurisdictions where local amendments may differ from state code",
                "source": "https://insulationinstitute.org/wp-content/uploads/2025/05/N130-NE-Energy-Code-0425.pdf"
            },
            {
                "title": "Nebraska Contractor Registration Act (NDOL) is mandatory before any work",
                "note": "All contractors and subcontractors doing business in Nebraska must register with the Nebraska Department of Labor under the Contractor Registration Act before performing work. The database is shared with the Department of Revenue. Failing to register before pulling permits or starting work exposes the contractor to penalties and can void permit eligibility on some AHJs.",
                "applies_to": "All contractors and subcontractors performing work in Nebraska",
                "source": "https://dol.nebraska.gov/conreg"
            },
            {
                "title": "Electrical work requires a state license from the Nebraska Electrical Board",
                "note": "Electrical contractors must hold a Nebraska State Electrical license issued by the State Electrical Division \u2014 homeowner permits and contractor permits are issued through that board. Cities like Omaha additionally require a current State of Nebraska license before issuing local registration ($85 + $6.80 tech fee), so the state license is the gating credential.",
                "applies_to": "All electrical scopes statewide",
                "source": "https://electrical.nebraska.gov/welcome"
            },
            {
                "title": "No state-level HVAC license \u2014 verify city/county requirements",
                "note": "Nebraska does not mandate licenses for HVAC apprentices, technicians, or contractors at the state level. HVAC licensing is set locally, so always verify the AHJ's specific HVAC contractor registration and permit rules before bidding mechanical work \u2014 Omaha and Lincoln have their own requirements that differ from rural counties.",
                "applies_to": "HVAC/mechanical scopes statewide",
                "source": "https://www.servicetitan.com/licensing/hvac/nebraska"
            },
            {
                "title": "Floodplain Development Permit required before any work in SFHA",
                "note": "A separate Floodplain Development Permit must be issued before any development, construction, or substantial improvement is undertaken in a regulated floodplain \u2014 this is in addition to the building permit. Use NeDNR's model permit application; when a property falls between two BFE lines on the determination map, use the higher elevation. Missing this permit is a common cause of stop-work orders.",
                "applies_to": "Any construction or substantial improvement within a mapped floodplain",
                "source": "https://dnr.nebraska.gov/sites/default/files/doc/floodplain/NeDNR_Model_Ordinance_B.pdf"
            },
            {
                "title": "Extraterritorial zoning jurisdiction (ETJ) splits permit authority near cities",
                "note": "Under Neb. Rev. Stat. 17-1001, Nebraska municipalities have extraterritorial zoning jurisdiction extending beyond city limits, sometimes into adjacent counties. A parcel that looks 'county' on a map may actually be under the city's zoning authority for permits. Always confirm which AHJ has jurisdiction before applying \u2014 submitting to the wrong office wastes weeks.",
                "applies_to": "Projects on parcels near city limits, especially in unincorporated areas",
                "source": "https://nebraskalegislature.gov/laws/statutes.php?statute=17-1001"
            },
            {
                "title": "Omaha ADU permits typically take ~6 weeks; ADUs allowed by-right post-2024",
                "note": "Under Omaha's 2024 zoning update, ADUs are allowed on most single-family lots by-right, but the specific zoning district controls eligibility and dimensional standards. Typical ADU permit approval time in Omaha runs about 6 weeks \u2014 set client expectations accordingly and front-load site plan review since it is required even for by-right ADUs.",
                "applies_to": "ADU projects in Omaha and other Nebraska jurisdictions allowing ADUs by-right",
                "source": "https://permitmint.com/permits/nebraska/omaha/adu/"
            }
        ]
    },
    "ID": {
        "name": "Idaho expert pack",
        "expert_notes": [
            {
                "title": "DOPL issues trade permits and licenses statewide",
                "note": "The Idaho Division of Occupational and Professional Licenses (DOPL) issues electrical, HVAC, and plumbing licenses and sells the corresponding trade permits and inspections statewide. Specialty Plumbing and Electrical licenses do not satisfy a general contractor licensing requirement, so confirm the correct license class before pulling. Permits and inspections are purchased online through DOPL rather than through most local building departments.",
                "applies_to": "Electrical, HVAC, and plumbing work in Idaho",
                "source": "https://dopl.idaho.gov/"
            },
            {
                "title": "Idaho adopted codes \u2014 2018 IBC/IRC/IECC with 2023 NEC",
                "note": "Idaho's adopted codes include the 2018 IBC and IRC families with amendments, the 2018 IECC residential provisions, and the 2023 NEC for electrical. Code amendments can be adopted in any legislative cycle regardless of the ICC code cycle, so verify the current adoption table before submitting plans relying on a newer edition.",
                "applies_to": "All new construction, additions, alterations, and electrical work",
                "source": "https://dopl.idaho.gov/wp-content/uploads/2025/06/2025-Updated-Idaho-Adopted-Codes.pdf"
            },
            {
                "title": "Local amendment authority is limited by statute",
                "note": "Local jurisdictions in Idaho can only amend portions of the residential code where they make a written finding that good cause exists for building or life-safety reasons; they cannot freely deviate from the state-adopted code. If a city inspector cites an amendment, ask for the adopting ordinance and the good-cause finding required by statute.",
                "applies_to": "Disputes over local code amendments on residential projects",
                "source": "https://cdn.ymaws.com/idahocities.org/resource/resmgr/publications/2020/constraints_on_code_amendmen.pdf"
            },
            {
                "title": "Building permit processing \u2014 typical 2\u20134 week window",
                "note": "Idaho building permits for sheds, shops, outbuildings, and ADUs typically take about 2\u20134 weeks to process, with longer waits during peak building season (spring/summer). Idaho has no statewide ministerial shot clock for ADUs, so build schedule float around the local AHJ's stated review window and submit in the off-season when possible.",
                "applies_to": "Residential building permit scheduling, including ADUs and outbuildings",
                "source": "https://stormorsheds.com/guide-to-pulling-building-permits-for-sheds-shops-outbuildings-or-adus-in-idaho/"
            },
            {
                "title": "City vs. county jurisdiction split",
                "note": "Building permits in Idaho are issued by the city inside incorporated limits and by the county outside them \u2014 for example, Ada County Development Services handles unincorporated Ada County while Boise, Meridian, Eagle, Star, and Kuna run their own permitting. Confirm jurisdiction by parcel before submitting; mixed-use or annexation-edge parcels can route to the wrong AHJ and lose weeks.",
                "applies_to": "Determining the correct permitting authority for a parcel",
                "source": "https://adacounty.id.gov/developmentservices/permitting-division/"
            },
            {
                "title": "Floodplain development permit required in SFHA",
                "note": "Any construction or development inside a Special Flood Hazard Area requires a Floodplain Development Permit before work begins, in addition to the building permit. IDWR provides the model form, but the permit is issued by the participating local community's floodplain administrator and is reviewed against the FIRM and local floodplain ordinance.",
                "applies_to": "Projects on parcels mapped within a FEMA SFHA or local flood overlay",
                "source": "https://idwr.idaho.gov/floods/"
            },
            {
                "title": "Overlay districts can add review steps",
                "note": "Many Idaho jurisdictions apply overlay districts \u2014 flood hazard, character/historic, hillside, and airport overlays \u2014 that trigger additional findings on top of the base zone. In Ada County and Boise, overlays such as the Flood Protection Overlay and character overlays require their own permits or design review and can add weeks to a project that looks 'by-right' under the base zoning.",
                "applies_to": "Parcels in flood, historic, hillside, or character overlays",
                "source": "https://adacounty.id.gov/developmentservices/community-planning-division/hazardous-overlay-districts/"
            },
            {
                "title": "Joint Application for work near water (IDWR/IDL/Corps)",
                "note": "Construction near streams, lakes, or wetlands typically requires a Joint Application for Permit, which routes a single application to IDWR, the Idaho Department of Lands, and the U.S. Army Corps of Engineers. Stream-channel alteration and encroachment permits are separate from the building permit and should be filed early because federal review can run months.",
                "applies_to": "Docks, bank stabilization, culverts, and any construction in or adjacent to waters of the state",
                "source": "https://www.idl.idaho.gov/wp-content/uploads/sites/116/2020/01/InstructionGuide-3.pdf"
            }
        ]
    },
    "WV": {
        "name": "West Virginia expert pack",
        "expert_notes": [
            {
                "title": "WV State Building Code is opt-in at local level",
                "note": "The West Virginia Fire Commission has adopted the IBC, IRC, IMC, IFGC, IPC, IPMC, and IEBC statewide, but enforcement only applies in jurisdictions that have formally adopted the State Building Code. Confirm with the local AHJ whether codes are enforced before assuming plan-review requirements \u2014 many rural counties have no building department.",
                "applies_to": "All construction projects statewide",
                "source": "https://www.iccsafe.org/advocacy/adoptions-map/west-virginia/"
            },
            {
                "title": "Contractor license required for jobs $2,500+",
                "note": "Any business performing construction work valued at $2,500 or more (labor plus materials) must hold a West Virginia Contractor License issued by the WV Contractor Licensing Board. Permit officers will not issue a building permit to anyone lacking a valid license when required by WV Code \u00a730-42-10.",
                "applies_to": "All contractors on jobs $2,500 or greater",
                "source": "https://code.wvlegislature.gov/30-42-10/"
            },
            {
                "title": "Separate state certifications for HVAC, plumbing, and electrical trades",
                "note": "The WV Division of Labor issues and renews roughly 29,000\u201330,000 contractor licenses, HVAC technician certifications, and plumber certifications annually. Trade techs must hold individual state certification in addition to the company's contractor license \u2014 verify each tech via the Division of Labor database before pulling trade permits.",
                "applies_to": "HVAC, plumbing, and trade subcontractor work",
                "source": "https://labor.wv.gov/licensing"
            },
            {
                "title": "2021 I-codes effective August 1, 2022",
                "note": "The State Fire Commission permanently adopted the 2021 editions of the I-codes (Title 87 Series 4) effective August 1, 2022. Projects in code-enforcing jurisdictions must comply with the 2021 IBC/IRC/IECC and any state amendments \u2014 do not submit drawings referencing older cycles without confirming the AHJ's adopted edition.",
                "applies_to": "New permit submittals in code-enforcing jurisdictions",
                "source": "https://www.icc-nta.org/code-update/west-virginia-state-fire-commission-adopted-new-codes/"
            },
            {
                "title": "Floodplain permit required before building permit issuance",
                "note": "Before issuing a building permit, the county or community floodplain officer must require copies of all other permits required by federal or state law, including a local floodplain development permit for any work in a Special Flood Hazard Area. Check the WV Flood Tool early \u2014 missing floodplain sign-off is a common cause of permit holds.",
                "applies_to": "Any construction within a mapped floodplain or SFHA",
                "source": "https://dep.wv.gov/WWE/Programs/nonptsource/streamdisturbance/Documents/Floodplainpermits.pdf"
            },
            {
                "title": "Stream disturbance permit ($300) for land disturbance near waters",
                "note": "WVDEP Division of Water requires a Stream Disturbance Permit for qualifying land-disturbing activities; the permit fee is $300 and disturbance does not need to be contiguous to qualify. Coordinate with WVDEP before any grading, crossings, or utility trenching that touches a stream or its buffer.",
                "applies_to": "Site work, grading, or utility installs near streams",
                "source": "https://dep.wv.gov/WWE/Programs/nonptsource/streamdisturbance/Documents/WVStreamDisturbancePermitGuide.pdf"
            },
            {
                "title": "Plan review timelines vary 2\u20136 weeks by jurisdiction",
                "note": "There is no statewide ministerial shot clock for residential permits in West Virginia; plan review alone typically takes two to six weeks depending on the AHJ's workload and submittal completeness. Build this float into client timelines and submit complete packages on the first try to avoid restarting the queue.",
                "applies_to": "Residential permit submittals including ADUs",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-west-virginia"
            },
            {
                "title": "Verify municipal vs. county jurisdiction before applying",
                "note": "WV permitting is split between municipal building departments and county offices, and many parcels fall outside any code-enforcing jurisdiction entirely. Before pulling permits, research local codes and ordinances for the specific parcel \u2014 HBAWV recommends doing this even before purchasing a lot, because applicable rules differ sharply between incorporated and unincorporated areas.",
                "applies_to": "Site selection and pre-permit due diligence",
                "source": "https://www.hbawv.org/resources/builder-resources/building-permits/"
            }
        ]
    },
    "HI": {
        "name": "Hawaii expert pack",
        "expert_notes": [
            {
                "title": "Hawaii is county-permitted, not municipal \u2014 four AHJs",
                "note": "Hawaii has no city building departments. All building permits are issued by one of four county building/permitting departments: Honolulu DPP, Hawaii County DPW Building Division, Maui County, or Kauai County. Confirm which county AHJ governs the parcel before submitting plans.",
                "applies_to": "All Hawaii building permit jobs",
                "source": "https://www.honolulu.gov/dpp/permitting/building-permits-home/building-permit-requriements/"
            },
            {
                "title": "State Building Code adoption and the two-year county amendment window",
                "note": "Each county must amend and adopt the Hawaii State Building Code within two years of state adoption. If a county fails to adopt within that window, the state code becomes applicable as the interim county building code. This means the code edition in force can differ between Honolulu, Maui, Kauai, and Hawaii County \u2014 verify the current adopted edition with the county before designing.",
                "applies_to": "All Hawaii new construction and alterations",
                "source": "https://hawaiienergy.com/education/codes/"
            },
            {
                "title": "2018 IECC with Hawaii amendments is the baseline energy code",
                "note": "In December 2020 the Hawaii State Building Code Council adopted the 2018 International Energy Conservation Code with state amendments. New construction and conditioned-space alterations must comply with the 2018 IECC as amended (or the more current edition where a county has adopted one).",
                "applies_to": "New construction and conditioned-space alterations",
                "source": "https://energy.hawaii.gov/hawaii-building-energy-codes/"
            },
            {
                "title": "Special Management Area (SMA) permit for shoreline work",
                "note": "Development within a county-mapped Special Management Area along the shoreline requires an SMA permit (Minor or Use) in addition to the building permit. Shoreline setbacks are typically established during SMA review under HRS Chapter 205A. Check the SMA boundary early \u2014 it can add months to the timeline and trigger additional review.",
                "applies_to": "Coastal/shoreline parcels in any Hawaii county",
                "source": "https://planning.hawaii.gov/czm/special-management-area-permits/"
            },
            {
                "title": "Conservation District land requires a CDUA from BLNR \u2014 not a county permit",
                "note": "Parcels in the State Conservation District require a Conservation District Use Application (CDUA) approved by the Board of Land and Natural Resources (BLNR) through DLNR's Office of Conservation and Coastal Lands. Departmental Permit fee is $250 plus possible hearing/publication costs. The county building permit cannot proceed without the CDUA.",
                "applies_to": "Parcels in the State Conservation District",
                "source": "https://dlnr.hawaii.gov/occl/application-process/"
            },
            {
                "title": "Trade contractor licenses are state-issued, not county-issued",
                "note": "HVAC, electrical, plumbing, and general contractor licenses are issued by the State of Hawaii DCCA Contractors License Board (and the Board of Electricians and Plumbers for trades). Counties do not issue contractor licenses. Each license requires a qualifying Responsible Managing Employee (RME). Verify the RME is current before pulling permits \u2014 an expired RME blocks permit issuance.",
                "applies_to": "All licensed trade work in Hawaii",
                "source": "https://cca.hawaii.gov/pvl/boards/contractor/"
            },
            {
                "title": "ADU covenant must be recorded before building permit issuance",
                "note": "For an ADU (ohana dwelling) in Hawaii, the owner must record a covenant with the Bureau of Conveyances or Land Court before the building permit is issued. The covenant restricts the unit (commonly to non-sale/long-term occupancy terms). Skipping this recordation is a common cause of permit holds.",
                "applies_to": "ADU / ohana dwelling jobs",
                "source": "https://www.coastalviewconstructionllc.com/adu-hawaii-guide/"
            },
            {
                "title": "Honolulu OTR-60 one-cycle review for residential permits",
                "note": "Honolulu DPP offers OTR-60, an optional residential building permit process that limits agency review to a single review cycle to shorten approval time. Plans must be complete and code-compliant on first submission \u2014 any reviewer comment requiring substantive correction can drop the project out of the OTR-60 track.",
                "applies_to": "Residential building permits in the City and County of Honolulu",
                "source": "https://www.honolulu.gov/dpp/home/faq/"
            }
        ]
    },
    "NH": {
        "name": "New Hampshire expert pack",
        "expert_notes": [
            {
                "title": "NH State Building Code applies statewide \u2014 no local technical amendments allowed",
                "note": "The State Building Code applies in every NH municipality and sets the minimum requirements for all buildings. No technical amendments to the state building code are permitted at the local level; municipalities may only impose stricter administrative provisions where the state code is silent or where the municipal provision is more stringent. Confirm with the local AHJ which administrative add-ons (fees, application forms, inspection sequencing) apply.",
                "applies_to": "All NH building permit applications",
                "source": "https://www.firemarshal.dos.nh.gov/laws-rules-regulatory/state-building-code"
            },
            {
                "title": "Currently adopted code editions (2021 IRC/IBC, 2018 IECC) with NH amendments",
                "note": "New Hampshire has adopted the 2021 International Residential Code (IRC) and 2021 IBC with NH amendments, and the 2018 International Energy Conservation Code (IECC) with NH amendments. Additional NH amendment packages took effect July 1, 2025 and October 15, 2025 \u2014 verify the amendment edition in force at submittal date for energy and structural provisions.",
                "applies_to": "New construction, additions, and energy-compliance submittals statewide",
                "source": "https://www.merrimacknh.gov/sites/g/files/vyhlif3456/f/news/nh-adopted-codes-august-2024.pdf"
            },
            {
                "title": "No statewide GC license \u2014 electrical and plumbing trades are state-licensed",
                "note": "New Hampshire does not require a state general contractor or HVAC contractor license. However, electrical work at 30 volts or higher requires an active NH Electrician license through the OPLC Electricians' Board, and plumbing work requires a state trade license. Apprentices must register with OPLC. Confirm local municipal contractor registration separately.",
                "applies_to": "All trades \u2014 confirm electrical/plumbing licensure at permit submittal",
                "source": "https://www.oplc.nh.gov/find-board/electricians-board"
            },
            {
                "title": "ADUs allowed by right in all single-family zones (2025 law)",
                "note": "Under the revised 2025 NH ADU law, a municipality that adopts a zoning ordinance must allow one accessory dwelling unit by right in all zoning districts that permit single-family dwellings \u2014 no special exception, conditional use, or public hearing required. A building permit (plus electrical/plumbing/HVAC permits as applicable) and inspections are still required.",
                "applies_to": "ADU projects on single-family parcels statewide",
                "source": "https://www.nhmunicipal.org/sites/default/files/uploads/Guidance_Documents/adus_guidance_revised_nov25.pdf"
            },
            {
                "title": "NHDES Alteration of Terrain (AoT) permit triggers on larger sites",
                "note": "NHDES Alteration of Terrain permitting regulates stormwater control, treatment, and earth-moving on larger project sites. Trigger thresholds depend on contiguous disturbed area and proximity to surface waters. Plan for AoT review in parallel with the local building permit \u2014 failure to secure AoT before earth-moving is a common cause of stop-work orders.",
                "applies_to": "Site work involving significant grading, clearing, or stormwater impact",
                "source": "https://www.des.nh.gov/land/land-development"
            },
            {
                "title": "NHDES Shoreland and Wetlands permits required before excavation/fill",
                "note": "Construction, fill, excavation, or dredge activities within surface waters, banks, or the protected shoreland (generally within 250 feet of a public water body) require an NHDES Shoreland permit. Any work in jurisdictional wetlands requires a separate NHDES Wetlands permit. Both must be obtained from NHDES before the local building department can authorize ground-disturbing work.",
                "applies_to": "Projects within 250 ft of public waters or affecting wetlands",
                "source": "https://www.mclane.com/insights/new-hampshire-wetlands-regulation-what-property-owners-need-to-know/"
            },
            {
                "title": "Local floodplain overlay and SFHA disclosure",
                "note": "NH municipalities administer floodplain management through local overlay districts under the NH Floodplain Management Handbook. The state recommends building permit applications include the question 'Is this property in a Special Flood Hazard Area?' Confirm SFHA status against current FIRM panels \u2014 projects in the SFHA trigger elevation certificates, lowest-floor elevation requirements, and additional plan-review steps.",
                "applies_to": "Any parcel within or adjacent to a FEMA Special Flood Hazard Area",
                "source": "https://www.nheconomy.com/getmedia/ce650a9e-ab1e-4c51-9afa-482e728cb730/Floodplain-Mang-Handbook.pdf"
            },
            {
                "title": "Ground-snow-load values dictated by NH State Building Code amendments",
                "note": "NH State Building Code Amendments require use of the state-adopted ground snow load document for buildings governed by the IRC and IBC. Do not default to the IRC base table \u2014 pull the site-specific ground snow load from the NH-amendment reference for structural design of roofs, decks, and accessory structures, particularly in the White Mountains and higher-elevation towns.",
                "applies_to": "Structural design submittals statewide",
                "source": "https://senh.org/New_Hampshire_State_Building_Code"
            }
        ]
    },
    "ME": {
        "name": "Maine expert pack",
        "expert_notes": [
            {
                "title": "Maine ADU statute - municipalities must permit one ADU on single-family lots",
                "note": "Under MRS Title 30-A \u00a74364-B, every Maine municipality must permit at least one ADU on lots zoned for single-family use, and the application/permitting process cannot require planning board approval. Town Council municipalities had to comply by January 1, 2024; Town Meeting municipalities had a later deadline. If a local AHJ is routing your ADU through planning board review, that is grounds to push back.",
                "applies_to": "ADU jobs in any Maine municipality with single-family zoning",
                "source": "https://legislature.maine.gov/statutes/30-a/title30-Asec4364-B.pdf"
            },
            {
                "title": "MUBEC 2021 ICC code cycle effective April 7, 2025",
                "note": "Maine adopted the 2021 ICC model codes (IRC, IBC, IECC) as the Maine Uniform Building and Energy Code (MUBEC) effective April 7, 2025, with state amendments in Rule Chapters 1-7. Verify which code edition the AHJ is reviewing under for permits filed near the transition - older submittals may need updates to meet the new energy and attic insulation requirements.",
                "applies_to": "New construction and alterations subject to MUBEC",
                "source": "https://aiamaine.org/aiamainenews/2025/2/27/updated-maine-building-codes"
            },
            {
                "title": "MUBEC enforcement threshold - 4,000 population",
                "note": "MUBEC must be adopted and enforced in municipalities with 4,000 or more residents (or any smaller municipality that had previously adopted any building code). Towns under 4,000 may choose not to enforce a building code at all, meaning some Maine jurisdictions issue no building permits. Confirm whether the AHJ enforces MUBEC before assuming standard plan review applies.",
                "applies_to": "All Maine residential construction permits",
                "source": "https://www.maine.gov/dps/fmo/sites/maine.gov.dps.fmo/files/inline-files/MUBEC%20Standards%20and%20Amendments.pdf"
            },
            {
                "title": "No statewide GC or HVAC license - electrical and plumbing only",
                "note": "Maine does not license general contractors or HVAC contractors at the state level. The only construction trades licensed by the State of Maine are electrical (Electricians' Examining Board) and plumbing. HVAC work is covered through the Fuel Board's licensing rather than a standalone HVAC contractor license. Pull electrical permits through the state Electricians' Examining Board online portal.",
                "applies_to": "Trade licensing and permit pulling for Maine residential jobs",
                "source": "https://www.contractorlicenserequirements.com/maine/general-contractor-license-requirements/"
            },
            {
                "title": "Shoreland Zoning - 250 ft setback rule near water bodies",
                "note": "Maine's Mandatory Shoreland Zoning (DEP Chapter 1000) imposes setbacks and permit requirements for construction within 250 feet of great ponds, rivers, coastal wetlands, and other protected waters, plus 75 feet of certain streams. Municipal shoreland ordinances must meet DEP minimums. Check the parcel against the local shoreland overlay before scoping foundation, addition, or earthmoving work.",
                "applies_to": "Construction near coastal waters, lakes, rivers, streams, or wetlands",
                "source": "https://www.maine.gov/dep/land/slz/"
            },
            {
                "title": "NRPA permit for work in or near protected natural resources",
                "note": "The Natural Resources Protection Act (NRPA) requires a DEP permit for activities that alter coastal wetlands, freshwater wetlands of special significance, great ponds, rivers, streams, fragile mountain areas, or significant wildlife habitat. NRPA permits are filed through the DEP Land Bureau online licensing system and are separate from the municipal building permit - both are typically required.",
                "applies_to": "Construction or earthmoving touching wetlands, waterbodies, or other NRPA-protected resources",
                "source": "https://www.maine.gov/dep/land/nrpa/"
            },
            {
                "title": "Floodplain development permit (60.3(b) form)",
                "note": "Communities participating in the NFIP require a Flood Hazard Development Permit for any construction in a Special Flood Hazard Area. Use the 60.3(b) Flood Hazard Development Permit Application and the Two-Part Permit Form supplied by Maine DACF. This is a separate permit from the building permit and must be issued before construction begins in a mapped floodplain.",
                "applies_to": "Construction in NFIP Special Flood Hazard Areas in Maine",
                "source": "https://www.maine.gov/dacf/flood/ordinances.shtml"
            },
            {
                "title": "Subdivision rule blocks building permits without approval",
                "note": "Under Title 30-A \u00a74103, the licensing authority may not issue any building or use permit within a subdivision (as defined in \u00a74401(4)) unless that subdivision has received the required municipal approval. Before applying, confirm the lot is on an approved subdivision plan or is grandfathered - otherwise the building permit will be denied at intake regardless of code compliance.",
                "applies_to": "Lots created by division of land or located within a subdivision",
                "source": "https://legislature.maine.gov/statutes/30-a/title30-Asec4103.html"
            }
        ]
    },
    "RI": {
        "name": "Rhode Island expert pack",
        "expert_notes": [
            {
                "title": "Rhode Island 30-day permit review statute",
                "note": "By Rhode Island state statute, the local building official has 30 days to review and act on a permit application. If the deadline passes without action, you can file a complaint with the State Building Office (Building Code Commission) to escalate.",
                "applies_to": "All residential building permits in Rhode Island",
                "source": "https://www.facebook.com/groups/rirealestateinvestors/posts/5955246111265907/"
            },
            {
                "title": "ADUs allowed by-right under state law",
                "note": "Rhode Island state law requires ADUs to be allowed by-right in residential zones \u2014 no special use permit, variance, or discretionary zoning approval is necessary. Push back if a municipality tries to route an ADU through a special-use or discretionary process.",
                "applies_to": "ADU projects on residentially zoned lots",
                "source": "https://www.zookcabins.com/regulations/adu-regulations-in-rhode-island"
            },
            {
                "title": "Statewide uniform building code (no county layer)",
                "note": "Rhode Island administers a single statewide building code through the Building Code Standards Committee \u2014 same base building, residential, plumbing, and mechanical codes apply in every city and town. Permits are issued by the municipal building official; there is no county building department layer. State-owned buildings are permitted by the Building Code Commission, not the local AHJ.",
                "applies_to": "All Rhode Island construction projects",
                "source": "https://www.iccsafe.org/advocacy/adoptions-map/rhode-island/"
            },
            {
                "title": "2024 IECC adoption with electric-ready provisions",
                "note": "Rhode Island enacted legislation in August 2023 requiring adoption of the 2024 IECC for new residential and commercial construction, including electric-ready provisions. Energy compliance documentation must reflect the current RISBC-8 Energy Conservation Code \u2014 older 2021 IECC envelope/mechanical assumptions will fail plan review on new submissions.",
                "applies_to": "New residential and commercial construction, additions, and conditioned-space alterations",
                "source": "https://modelclimatelaws.org/resources/rhode-islands-iecc-2024-adoption/"
            },
            {
                "title": "CRLB registration required for all residential and commercial work",
                "note": "Rhode Island General Laws require any contractor or subcontractor performing residential or commercial construction, remodeling, or repair work to be registered with the Contractors' Registration and Licensing Board (CRLB). Building officials routinely verify CRLB registration before issuing permits \u2014 an unregistered contractor will block permit issuance.",
                "applies_to": "Any GC or sub pulling a Rhode Island building permit",
                "source": "https://crb.ri.gov/general-contractor-registration"
            },
            {
                "title": "Trade licenses issued separately by DLT Professional Regulation",
                "note": "Electrical, plumbing, mechanical (HVAC), and hoisting licenses are issued by the RI Department of Labor & Training Professional Regulation boards, not by the CRLB. A GC's CRLB registration does not authorize trade work \u2014 each trade pull must come from a DLT-licensed master/journeyman in that specific trade.",
                "applies_to": "Electrical, plumbing, HVAC, and hoisting permits",
                "source": "https://dlt.ri.gov/regulation-and-safety/professional-regulation"
            },
            {
                "title": "CRMC Assent required for coastal construction",
                "note": "Any construction or alteration on a coastal feature \u2014 coastal beaches, barriers, dunes, coastal wetlands, headlands, bluffs, and cliffs \u2014 requires a permit (Assent) from the Coastal Resources Management Council before the local building permit can move forward. Coastal projects often need both a CRMC Assent and a DEM Wetlands permit.",
                "applies_to": "Projects on or near tidal coastal features in any of the 21 RI coastal communities",
                "source": "https://www.crmc.ri.gov/applicationforms.html"
            },
            {
                "title": "DEM Freshwater Wetlands permit for inland buffers",
                "note": "Projects within or near jurisdictional freshwater wetland areas (including buffers and perimeter wetlands) must obtain a permit from the RI Department of Environmental Management before any site work or building permit issuance. This is a common cause of permit holds on inland additions, ADUs, and septic-served sites.",
                "applies_to": "Inland projects within or near freshwater wetlands or their jurisdictional buffers",
                "source": "https://dem.ri.gov/environmental-protection-bureau/water-resources/permitting/freshwater-wetlands"
            }
        ]
    },
    "MT": {
        "name": "Montana expert pack",
        "expert_notes": [
            {
                "title": "310 Permit required for work in perennial streams",
                "note": "Any individual or entity proposing construction in or near a perennial stream in Montana must apply for a 310 Permit through the local conservation district under the Natural Streambed and Land Preservation Act. This is separate from a building permit and must be secured before in-stream work begins.",
                "applies_to": "Any project with construction activity in or adjacent to a perennial stream",
                "source": "https://dnrc.mt.gov/licenses-and-permits/stream-permitting/"
            },
            {
                "title": "Joint Application required for floodplain, stream, and wetland work",
                "note": "Montana DNRC requires a Joint Application for any work proposed in floodplains, streams, or wetlands. Floodplain development permits are issued by local floodplain administrators and must be coordinated with the 310 process if a stream is involved.",
                "applies_to": "Projects in mapped floodplains, streams, or wetlands",
                "source": "https://dnrc.mt.gov/Water-Resources/Floodplains/Permitting-and-Regulations"
            },
            {
                "title": "Statewide electrical and plumbing permits required for most work",
                "note": "An electrical permit is required for any installation in new construction, remodeling, or repair, except for narrow exemptions in MCA 50-60-602. A Montana licensed Master Plumber is required on all public/commercial plumbing work. These permits are issued by the state DLI Building Codes Bureau, not by local AHJs unless certified.",
                "applies_to": "Electrical and plumbing scopes statewide",
                "source": "https://bsd.dli.mt.gov/building-codes-permits/permit-applications/electrical-permits/"
            },
            {
                "title": "Code enforcement only by certified cities, counties, and towns",
                "note": "A city, county, or town in Montana may not enforce a building code unless its program has been certified by the state Building Codes Program. In uncertified jurisdictions, the state DLI is the AHJ for state-regulated permits (electrical, plumbing, mechanical, building). Confirm certification before assuming local plan review applies.",
                "applies_to": "All Montana projects \u2014 verify AHJ before submitting",
                "source": "https://bsd.dli.mt.gov/building-codes-permits/certified-government"
            },
            {
                "title": "Contractor registration with DLI before any work",
                "note": "Construction contractors with employees must register with the Montana Department of Labor and Industry by submitting an application with a $70 non-refundable fee. Contractors operating without registration can be fined. Independent contractors without employees may need an Independent Contractor Exemption Certificate (ICEC) instead.",
                "applies_to": "Any contractor performing construction work in Montana",
                "source": "https://erd.dli.mt.gov/work-comp-regulations/montana-contractor/construction-contractor-registration"
            },
            {
                "title": "Plan review averages three weeks at the state level",
                "note": "The Montana DLI Building Codes Bureau reports an average plan review timeline of about three weeks for state-issued building permits. Local certified jurisdictions (e.g., Bozeman, Missoula) commonly run 4\u201312 weeks depending on complexity. Build this into project schedules and avoid promising faster turnaround.",
                "applies_to": "Permit scheduling and customer expectations",
                "source": "https://bsd.dli.mt.gov/building-codes-permits/permit-applications/building-permits/"
            },
            {
                "title": "Statewide ADU size cap of 1,000 sq ft or 75% of primary",
                "note": "Montana state law generally allows ADUs up to 1,000 square feet or 75% of the primary home's floor area, whichever is smaller. Local jurisdictions may layer additional zoning requirements (setbacks, parking, owner-occupancy) on top of this baseline, so always confirm local ADU rules in addition to the state standard.",
                "applies_to": "ADU projects statewide",
                "source": "https://www.zookcabins.com/regulations/mt-adu-regulations"
            },
            {
                "title": "Adopted codes and Montana-specific amendments",
                "note": "Montana adopts the IBC, IRC, IECC, and related codes via Administrative Rules of Montana Title 24, Chapter 301, with state-specific amendments. A notable residential energy amendment allows building cavities to serve as return ductwork. Always check the current ARM 24.301 amendments \u2014 Montana edits commonly differ from the base ICC text.",
                "applies_to": "Code compliance, plan review, and design decisions",
                "source": "https://bsd.dli.mt.gov/building-codes-permits/current-codes"
            }
        ]
    },
    "DE": {
        "name": "Delaware expert pack",
        "expert_notes": [
            {
                "title": "County-level code adoption \u2014 no statewide building code",
                "note": "Delaware does not have a uniform statewide building code. Title 16 Chapter 76 authorizes Kent County's Levy Court and the County Councils of New Castle and Sussex to adopt and enforce their own building, plumbing, and related codes. Sussex adopted the 2021 IBC/IRC effective May 17, 2022, while Kent County is on the 2018 IBC/IRC with county amendments. Always confirm the exact code edition with the AHJ before submitting plans.",
                "applies_to": "All residential and commercial construction statewide",
                "source": "https://delcode.delaware.gov/title16/c076/index.html"
            },
            {
                "title": "Municipal vs. county permit jurisdiction split",
                "note": "Building permit authority in Delaware is split between counties and incorporated municipalities. Many cities and towns issue their own permits independent of the county, while unincorporated areas fall under county building departments. Confirm jurisdiction first \u2014 submitting to the wrong AHJ is a common cause of weeks-long delays.",
                "applies_to": "All projects \u2014 first step before application",
                "source": "https://www.permitflow.com/state/delaware"
            },
            {
                "title": "Trade licensing through DPR (plumbing, HVACR, electrical)",
                "note": "Master plumber, master HVACR, master restricted HVACR, and electrician licenses are issued by the Delaware Division of Professional Regulation, not by counties. Permits for plumbing, HVAC, and electrical work must be pulled by a DPR-licensed master (or a homeowner under a homeowner permit). County contractor licensing (e.g., New Castle County) is a separate registration on top of DPR trade licensure.",
                "applies_to": "Plumbing, HVAC, and electrical permits",
                "source": "https://dpr.delaware.gov/boards/plumbers/"
            },
            {
                "title": "Department of Revenue contractor business license required",
                "note": "Any contractor doing business in Delaware must register with and obtain a business license from the Delaware Division of Revenue before performing work, in addition to Department of Labor registration under Chapter 36 and any trade license. Non-resident contractors face additional bonding/withholding requirements. Missing the Revenue license is a frequent cause of permit denial for out-of-state contractors.",
                "applies_to": "All contractors, including non-resident contractors",
                "source": "https://revenue.delaware.gov/business-tax-forms/contractors-resident-and-non-resident/"
            },
            {
                "title": "DNREC Coastal Construction Permit seaward of the building line",
                "note": "No construction may take place seaward of the established building line in Delaware's beach jurisdictions without a Coastal Construction Permit or Letter of Approval from DNREC. Full Coastal Construction Permits carry a $4,500 application fee and require a public notice/comment process, which adds significant time. Scope this early on any beachfront or near-beach project in Sussex County.",
                "applies_to": "Beachfront and coastal construction in Sussex County",
                "source": "https://dnrec.delaware.gov/watershed-stewardship/beaches/coastal-construction/permits/"
            },
            {
                "title": "DNREC wetlands and subaqueous lands permits",
                "note": "Work in tidal wetlands, non-tidal state-regulated wetlands, or subaqueous (submerged) lands requires a permit from the DNREC Wetlands and Waterways Section. General permits are available for some routine activities, but individual permits are required otherwise. Confirm wetland status before site work \u2014 unpermitted impacts trigger restoration orders and stop-work actions.",
                "applies_to": "Sites with tidal/non-tidal wetlands or work in/over state waters",
                "source": "https://dnrec.delaware.gov/water/wetlands/permits/"
            },
            {
                "title": "Energy code \u2014 2018 IECC / ASHRAE 90.1-2016 (overhaul paused)",
                "note": "Delaware's residential and commercial energy code is the 2018 IECC and ASHRAE 90.1-2016, adopted by DNREC in June 2020. A 2025 DNREC proposal to adopt a sweeping update (with mandates for new homes starting Dec. 31) was paused in November 2025 after Home Builders Association concerns. Design to the 2018 IECC baseline today, but watch for the next DNREC rulemaking before locking long-lead specs.",
                "applies_to": "New construction and conditioned-space alterations statewide",
                "source": "https://dnrec.delaware.gov/climate-coastal-energy/efficiency/building-energy-codes/"
            },
            {
                "title": "SB 23 \u2014 local governments must allow ADUs",
                "note": "Delaware Senate Bill 23 requires local governments to permit ADU construction within their jurisdictions without prohibitive barriers or onerous application or zoning requirements. Local rules vary widely on size caps, parking, and owner-occupancy, and typical local review still runs roughly four to eight weeks. Use SB 23 to push back when a municipality is imposing a flat ADU prohibition or clearly excessive standards.",
                "applies_to": "ADU jobs in any Delaware municipality or county",
                "source": "https://legis.delaware.gov/json/BillDetail/GenerateHtmlDocument?legislationId=141116&legislationTypeId=1&docTypeId=2&legislationName=SB23"
            }
        ]
    },
    "SD": {
        "name": "South Dakota expert pack",
        "expert_notes": [
            {
                "title": "No statewide building code \u2014 adoption is local",
                "note": "South Dakota is one of eight states without a statewide building code. The IRC, IBC, and IECC only apply where a municipality or county has adopted them. Always confirm which edition (and which local amendments) the AHJ has adopted before submitting plans, because adopted editions vary widely across the state.",
                "applies_to": "All residential and commercial permit work statewide",
                "source": "https://insulationinstitute.org/wp-content/uploads/2025/05/N160-SD-Energy-Code-0425.pdf"
            },
            {
                "title": "2009 IECC is voluntary, not mandatory",
                "note": "Under SDCL 11-10-7 (SB 94, signed March 15, 2011), the 2009 IECC was adopted only as a voluntary energy standard for new residential construction. It is not enforceable unless the local jurisdiction has adopted it by ordinance, so do not assume IECC compliance is required outside cities like Sioux Falls that have explicitly adopted a code.",
                "applies_to": "New residential construction and energy compliance scoping",
                "source": "https://sdlegislature.gov/Statutes/11-10"
            },
            {
                "title": "No state GC license, but trades are state-regulated",
                "note": "South Dakota does not issue a state general contractor license, but plumbing contractors are licensed through the SD Plumbing Commission and electrical work must be performed by a State Electrical Commission licensee. Gas piping for HVAC installs may also require a Plumbing Commission licensee. Verify trade credentials separately from any local GC registration.",
                "applies_to": "Any job involving plumbing, electrical, or gas piping scope",
                "source": "https://contractorlicenserequirements.com/assets/south-dakota-hvac-roadmap-2026.pdf"
            },
            {
                "title": "Sioux Falls requires local contractor licensing on top of state trade licenses",
                "note": "Sioux Falls requires a Residential Building Contractor's License for any contractor working on 1- and 2-family dwellings or townhomes within city limits, and a separate mechanical/HVAC contractor license for HVAC work. State-level trade licenses do not substitute for these city licenses, so register with the city before pulling a permit.",
                "applies_to": "Residential and HVAC work inside Sioux Falls city limits",
                "source": "https://www.siouxfalls.gov/business-permits/permits-licenses-inspections/licensing/contractor-licensing/building-contractor-residential"
            },
            {
                "title": "No state ADU law \u2014 rules are entirely local",
                "note": "South Dakota has no statewide ADU statute, so dimensional standards, owner-occupancy rules, and timelines are set by each city or county. In Sioux Falls (\u00a7 159.305), the ADU must share one platted lot with the primary dwelling, and only one ADU is allowed per property. Rural county reviews can stretch 4\u20138 weeks when staff is limited.",
                "applies_to": "ADU jobs anywhere in South Dakota",
                "source": "https://codelibrary.amlegal.com/codes/siouxfalls/latest/siouxfalls_sd/0-0-0-81216"
            },
            {
                "title": "Homeowner plumbing permits are limited to single-family residences",
                "note": "The SD Plumbing Commission allows homeowner plumbing permits only for the homeowner's own single-family dwelling. Detached structures, duplexes, rentals, and accessory buildings on the same parcel are not covered, so a licensed plumbing contractor must pull the permit for ADUs, shops, and outbuildings even when the owner lives on site.",
                "applies_to": "Owner-pulled plumbing permits on residential parcels",
                "source": "https://dlr.sd.gov/plumbing/homeowner_plumbing.aspx"
            },
            {
                "title": "GF&P shoreline alteration permit required for lake/stream work",
                "note": "Any alteration of the bottom or shoreline of public waters (docks, riprap, fill, dredging, boat ramps) requires a Game, Fish & Parks shoreline alteration permit. All work must be completed before the permit expires, though renewals are available without resubmitting plans. Build this lead time into projects on lakefront or riparian parcels.",
                "applies_to": "Construction touching public lake bottoms or shorelines",
                "source": "https://gfp.sd.gov/shoreline-alterations/"
            },
            {
                "title": "ETJ building permit requirement under SDCL 11-6-38",
                "note": "Under SDCL 11-6-38, no building permit may be issued and no building erected on any lot within the territorial (extraterritorial) jurisdiction of a municipal planning commission and council without compliance with their regulations. Confirm whether a parcel near a city falls inside its ETJ, since the city \u2014 not just the county \u2014 may control permitting and zoning.",
                "applies_to": "Parcels in unincorporated areas near municipal boundaries",
                "source": "https://sdlegislature.gov/Statutes/11-6-38"
            }
        ]
    },
    "ND": {
        "name": "North Dakota expert pack",
        "expert_notes": [
            {
                "title": "North Dakota is a home-rule state \u2014 no mandatory statewide residential code enforcement",
                "note": "North Dakota allows local jurisdictions to choose whether to adopt the state building code, and they may amend it to fit local needs. Always confirm with the local AHJ which code edition and amendments are in force before pulling permits \u2014 neighboring cities may be on different cycles.",
                "applies_to": "All residential and ADU projects statewide",
                "source": "https://bcapcodes.org/code-status/state/north-dakota/"
            },
            {
                "title": "2026 North Dakota State Building Code takes effect Jan 1, 2026",
                "note": "The new ND State Building Code went into effect January 1, 2026. Verify the local jurisdiction's adoption date and whether they are enforcing the 2026 edition or still operating under the prior 2025 code, since adoption is local. Check the Local Code Enforcement Directory before submittal.",
                "applies_to": "New construction and alterations permitted on or after 2026-01-01",
                "source": "https://www.commerce.nd.gov/community-services/building-codes"
            },
            {
                "title": "2024 IECC residential energy code is voluntary and unenforceable in ND",
                "note": "There is no mandatory statewide residential energy code. The 2024 IECC with amendments took effect 1/1/2026 but is voluntary and unenforceable. Any energy-code requirements come from the local jurisdiction \u2014 confirm the AHJ's specific envelope, fenestration, and blower-door requirements rather than assuming IECC applies.",
                "applies_to": "Residential new construction, ADUs, and envelope alterations",
                "source": "https://www.mwalliance.org/north-dakota/north-dakota-building-energy-codes"
            },
            {
                "title": "Plumbing and electrical licenses are state-issued; HVAC is local-only",
                "note": "Plumbing licenses are issued by the ND State Plumbing Board and electrical licenses by the ND State Electrical Board \u2014 both required statewide. HVAC, however, has no state-level licensing for apprentices, technicians, or contractors; many municipalities require their own local HVAC license. Verify city HVAC licensing before bidding mechanical scope.",
                "applies_to": "Trade subcontractor scoping and bid preparation",
                "source": "https://www.servicetitan.com/licensing/hvac/north-dakota"
            },
            {
                "title": "Electrical contractors must register the business with Secretary of State before licensure",
                "note": "Per the ND State Electrical Board, electrical contractors must first contact the Secretary of State (701-328-2900) to register the business AND license the business as a contractor before the Electrical Board will issue the contractor license. Skipping the SOS step delays project start.",
                "applies_to": "New electrical contractors or out-of-state contractors entering ND",
                "source": "https://www.ndseb.com/licensing/electrical-contractor-guidelines-requirements/"
            },
            {
                "title": "Floodplain development permit required before any work in a Special Flood Hazard Area",
                "note": "A floodplain development permit must be obtained from the local Floodplain Administrator before beginning any work in a Special Flood Hazard Area. Note that over 45% of NFIP claims in ND occur outside the mapped SFHA, so confirm flood status with the local permitting office even on parcels that appear out of the floodplain.",
                "applies_to": "Any construction, fill, grading, or structure placement near waterways",
                "source": "https://www.swc.nd.gov/reg_approp/floodplain_management/"
            },
            {
                "title": "Extraterritorial zoning jurisdiction can shift permitting authority outside city limits",
                "note": "Under NDCC Title 40 Chapter 47, cities may exercise extraterritorial zoning jurisdiction beyond municipal boundaries, and the authority to receive applications and issue permits in that area can be reassigned by written agreement between the city and county. On parcels near a city edge, confirm in writing whether the city or the county is the AHJ.",
                "applies_to": "Projects on parcels within ETJ buffer zones around incorporated cities",
                "source": "https://ndlegis.gov/cencode/t40c47.pdf"
            },
            {
                "title": "Typical ADU permit timeline runs 4\u201310 weeks once the application is complete",
                "note": "Permit timelines in most North Dakota cities run four to ten weeks after documents are complete. There is no statewide ministerial shot clock for ADUs \u2014 set client expectations accordingly and front-load completeness review, since the clock effectively only starts once the AHJ deems the submittal complete.",
                "applies_to": "ADU and residential permit scheduling and client expectations",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-north-dakota"
            }
        ]
    },
    "AK": {
        "name": "Alaska expert pack",
        "expert_notes": [
            {
                "title": "No statewide building code \u2014 AHJ varies by borough",
                "note": "Alaska does not have a single statewide building code. Boroughs and municipalities adopt their own codes, and unorganized boroughs may have no permits or inspections at all. Always confirm the AHJ and adopted code edition before scoping a project; what applies in Anchorage will not match Fairbanks, the Mat-Su Borough, or rural areas.",
                "applies_to": "All Alaska residential and commercial projects",
                "source": "https://www.alaskahomebuilder.com/permits-zoning-and-building-regulations-in-remote-rural-alaska-what-you-must-know-before-you-break-ground/"
            },
            {
                "title": "AHFC 2018 IRC / IECC with Alaska-specific amendments",
                "note": "Alaska Housing Finance Corporation has adopted the 2018 IRC as its residential standard and the 2018 IECC with Alaska-specific amendments. Projects financed or inspected under AHFC programs (BEES) must meet these, and many local AHJs reference them. Pull the AHFC amendment package early \u2014 Alaska amendments cover cold-climate envelope, vapor retarder, and ventilation specifics that differ from the model code.",
                "applies_to": "AHFC-financed homes and jurisdictions referencing the AHFC standard",
                "source": "https://www.ahfc.us/efficiency/codes-standards"
            },
            {
                "title": "Residential Contractor Endorsement requires 16-hour cold climate course",
                "note": "To pull residential permits, the general contractor must hold a Residential Contractor Endorsement from DCBPL, which requires completing a 16-hour cold climate housing course and passing the residential endorsement exam \u2014 on top of the base contractor registration ($350 fee). Verify the endorsement, not just the license number, before signing a homeowner contract.",
                "applies_to": "Residential GCs statewide",
                "source": "https://www.commerce.alaska.gov/web/cbpl/ProfessionalLicensing/ConstructionContractors"
            },
            {
                "title": "Specialty trades licensed separately from GC registration",
                "note": "Electrical, plumbing, and mechanical contractors are governed by separate specialty licensing boards under DCBPL \u2014 a general contractor endorsement does not authorize trade work. Plumbers must complete 125 hours of schooling or 1,000 fieldwork hours and pay the $50 application + $200 license fee, and gas work requires its own endorsement. Confirm each sub holds the correct trade license before submitting permit paperwork.",
                "applies_to": "Electrical, plumbing, mechanical, and gas scopes",
                "source": "https://alaskacontractorauthority.com/alaska-contractor-licensing-requirements"
            },
            {
                "title": "Anchorage Municipal Contractor License required in addition to state",
                "note": "Any individual or business doing construction inside the Municipality of Anchorage Service Area must hold a Municipal Contractors License on top of the state DCBPL endorsement. Out-of-municipality contractors are routinely caught at permit intake without it \u2014 register with MOA OCPD before submitting an Anchorage permit application.",
                "applies_to": "Projects within the Municipality of Anchorage",
                "source": "https://www.muni.org/Departments/OCPD/development-services/for-contractors/Pages/Contractor-Licensing.aspx"
            },
            {
                "title": "ADF&G Fish Habitat Permit for work in or near anadromous waters",
                "note": "Construction, fill, or equipment crossings in or adjacent to specified anadromous fish-bearing waters or designated Special Areas require a Fish Habitat Permit from Alaska Department of Fish & Game before work begins. This is independent of any local building permit and is a frequent miss on driveway culverts, bank stabilization, and waterfront ADUs. Contact the regional Habitat Biologist early \u2014 review can run several weeks.",
                "applies_to": "Sites near streams, rivers, lakes, or coastal waters",
                "source": "https://www.adfg.alaska.gov/index.cfm?adfg=uselicense.fish_habitat_permits"
            },
            {
                "title": "Palmer ADU 30-day review and recorded covenant requirement",
                "note": "The City of Palmer commits to reviewing ADU permits within 30 days where an ADU-specific ordinance is in place. As a permit condition, the property owner must record a covenant with the State of Alaska Recorder's Office (typically restricting short-term rental or owner-occupancy). Build the covenant recording step into the closeout checklist \u2014 CO is held until it's filed.",
                "applies_to": "ADU jobs in Palmer and similar Mat-Su jurisdictions",
                "source": "https://www.palmerak.org/media/18416"
            },
            {
                "title": "Check Alaska RiskMAP for flood, wildfire, and erosion overlays",
                "note": "Use the State's RiskMAP tool to confirm whether the parcel sits in a mapped flood, wildfire, erosion, or permafrost-thaw hazard area before design is locked in. Communities participating in NFIP enforce floodplain elevation and venting; coastal/riverine sites may also need a USACE Alaska District floodplain consultation. Pulling this before foundation design avoids costly redesigns at plan check.",
                "applies_to": "Parcels in mapped hazard zones statewide",
                "source": "https://www.commerce.alaska.gov/web/dcra/ResiliencePlanningLandManagement/RiskMAP/AlaskaMappingResources"
            }
        ]
    },
    "VT": {
        "name": "Vermont expert pack",
        "expert_notes": [
            {
                "title": "ADU allowed by-right under 24 V.S.A. \u00a7 4412",
                "note": "Every single-family home in Vermont can add one ADU as a permitted use under 24 V.S.A. \u00a7 4412. Municipalities cannot require a conditional use permit, special parking, or owner-occupancy as a condition of approval. A zoning permit (and usually a building permit) is still required, but denial on discretionary grounds is not allowed.",
                "applies_to": "ADU jobs on single-family lots statewide",
                "source": "https://www.newframeworks.com/blog/building-an-adu-in-vermont-what-you-need-to-know"
            },
            {
                "title": "2024 RBES energy code in effect (2021 IECC with VT amendments)",
                "note": "Vermont's Residential Building Energy Standards (RBES) based on the 2021 IECC with state amendments took effect July 1, 2024. Further RBES revisions adopted April 10, 2026 take effect July 14, 2026. New residential construction and qualifying renovations must include an RBES certificate posted on the electrical panel before occupancy.",
                "applies_to": "All residential new construction and qualifying renovations (3 stories or fewer)",
                "source": "https://publicservice.vermont.gov/efficiency/building-energy-standards/residential-building-energy-standards"
            },
            {
                "title": "No statewide general contractor license \u2014 trades licensed separately",
                "note": "Vermont does not issue a general contractor license. Electrical, plumbing, and elevator/conveyance licenses are issued by the Department of Public Safety, Division of Fire Safety \u2014 not by OPR \u2014 and initial licenses for those trades are NOT available through the online portal (paper application required). Residential contractors performing work over $10,000 must register with the Secretary of State.",
                "applies_to": "All residential projects involving electrical, plumbing, or registered residential contracting",
                "source": "https://firesafety.vermont.gov/licensing/licenses-web-portal"
            },
            {
                "title": "Act 250 land-use permit thresholds",
                "note": "An Act 250 permit may be required in addition to local zoning depending on project size, location, and whether the parcel is in a designated area. Housing thresholds and review requirements differ inside vs. outside designated downtowns/village centers. Confirm jurisdiction early via the Act 250 District Office \u2014 discovering Act 250 jurisdiction mid-build can halt the project.",
                "applies_to": "Subdivisions, multi-unit housing, and development above municipal thresholds",
                "source": "https://act250.vermont.gov/act250-permit/need-a-permit"
            },
            {
                "title": "Wetlands and 50-ft buffer review",
                "note": "Projects impacting jurisdictional wetlands or their buffers require a Vermont Wetland Permit from ANR. If a flood hazard area, river corridor, or perennial stream is within 50 feet of the work area, a state permit is likely required in addition to local approval. Use the ANR Atlas to check before scoping foundation, septic, or grading work.",
                "applies_to": "Any ground disturbance near wetlands, streams, river corridors, or floodplains",
                "source": "https://anr.vermont.gov/planning-and-permitting/planning-tools/act-250/act-250-criterion-1g-wetlands"
            },
            {
                "title": "River corridor and floodplain state permits",
                "note": "Development inside mapped floodplains or river corridors may trigger a state Stream Alteration permit, Flood Hazard Area permit, or other DEC review independent of FEMA NFIP compliance. Many Vermont towns rely on the state for floodplain enforcement, so a local permit alone is not sufficient \u2014 confirm DEC sign-off before pouring foundations.",
                "applies_to": "Construction in floodplains, river corridors, or near perennial streams",
                "source": "https://dec.vermont.gov/watershed/rivers/river-corridor-and-floodplain-protection/floodplains/state-permits"
            },
            {
                "title": "Municipal Stretch Code option (Act 89)",
                "note": "Under Act 89 of 2013, Vermont municipalities may adopt the Residential Stretch Code, which exceeds baseline RBES requirements. Always confirm with the local Zoning Administrator whether Stretch Code applies \u2014 using baseline RBES details in a Stretch town will fail energy compliance and block the certificate of occupancy.",
                "applies_to": "Residential new construction in towns that have adopted the Stretch Code",
                "source": "https://www.efficiencyvermont.com/Media/Default/docs/trade-partners/code-support/municipal-guide-for-vermont-energy-codes.pdf"
            },
            {
                "title": "Act 250 application is electronic via ANROnline",
                "note": "Act 250 applications must be submitted electronically through the ANROnline portal, including all supporting documents. Paper-only submissions are not accepted, and incomplete electronic packages restart the review clock. Build the ANROnline filing into your permit timeline rather than treating it as a same-day step.",
                "applies_to": "Any project requiring an Act 250 land use permit",
                "source": "https://act250.vermont.gov/act250-permit"
            }
        ]
    },
    "WY": {
        "name": "Wyoming expert pack",
        "expert_notes": [
            {
                "title": "No statewide general contractor license \u2014 verify local requirements",
                "note": "Wyoming does not issue a statewide general contractor, plumbing, or HVAC license. Licensing is handled at the municipal or county level, so requirements vary widely between Jackson, Cheyenne, Casper, and unincorporated counties. Always confirm trade-license rules with the local AHJ before bidding.",
                "applies_to": "All residential and commercial construction jobs in Wyoming",
                "source": "https://www.procore.com/library/wyoming-contractors-license"
            },
            {
                "title": "Statewide electrical licensing through State Fire Marshal",
                "note": "Electrical contractors and electricians are the exception to Wyoming's hands-off licensing approach: Master and Journeyman electrical licenses are issued and enforced by the Wyoming State Fire Marshal's Office. Failed exam candidates are not eligible for reciprocal licensure until they retest and pass.",
                "applies_to": "Electrical contractors and electricians working anywhere in Wyoming",
                "source": "https://wsfm.wyo.gov/electrical-safety/licensing"
            },
            {
                "title": "Fast-Track Permit Act \u2014 10-day completeness review",
                "note": "Wyoming's Fast-Track Permit Act requires local governments to notify residential building permit applicants within 10 business days whether the application is complete. Track the submission date \u2014 missing this clock is grounds to escalate, especially in jurisdictions like Teton County that have historically averaged 100+ day reviews.",
                "applies_to": "Residential building permit applications statewide",
                "source": "https://pacificlegal.org/press-release/fast-track-permit-act-wyoming/"
            },
            {
                "title": "30-day review shot clock for qualifying residential permits",
                "note": "Under the Fast-Track Permit Act rollout, municipalities must complete review of certain residential building permits within 30 days. Teton County previously averaged 112 days, so flag any jurisdiction running long and use the statute as leverage to push for issuance.",
                "applies_to": "Residential single-family and ADU permits in Wyoming municipalities",
                "source": "https://www.jhnewsandguide.com/news/town_county/wyoming-building-officials-prepare-to-enter-the-fast-track/article_fcd2e2e2-418d-48d4-8bd8-2970b2802a4a.html"
            },
            {
                "title": "Code adoption is local \u2014 confirm edition before plan submittal",
                "note": "Wyoming has no single statewide building code; cities and counties each adopt their own edition. Some jurisdictions (e.g., Jackson) moved to the 2021 I-Codes in January 2022, while Laramie County adopted 2024 IBC amendments, and the state separately references the 2024 IBC effective June 26, 2024. Always verify the exact code edition with the AHJ before producing plans.",
                "applies_to": "Plan preparation and code compliance for any WY jurisdiction",
                "source": "https://www.iccsafe.org/advocacy/adoptions-map/wyoming/"
            },
            {
                "title": "WYPDES stormwater permit for sites disturbing 1+ acre",
                "note": "Wyoming DEQ requires coverage under the Large or Small Construction General Permit (WYPDES) for any construction activity disturbing one acre or more, including smaller sites that are part of a larger common plan of development. SWPPP and Notice of Intent must be filed before earth disturbance begins.",
                "applies_to": "Site work, subdivisions, and ground-disturbing construction",
                "source": "http://deq.wyoming.gov/water-quality/wypdes/discharge-permitting/storm-water-permitting/large-and-small-construction-general-permit/"
            },
            {
                "title": "Wyoming Game & Fish floodplain/stream alteration permit",
                "note": "Work that alters a stream channel, floodplain, or wetland \u2014 including diversions, head gates, bank work, or fish barriers \u2014 typically requires a Wyoming Game & Fish Department permit in addition to any Army Corps Section 404 authorization. Coordinate early; commissioner approval can be required for floodplain work on private parcels.",
                "applies_to": "Construction in or near streams, floodplains, and wetlands",
                "source": "https://wgfd.wyo.gov/licenses-applications/permits/permit-forms-applications"
            },
            {
                "title": "County water/sewer adequacy gate on building permits",
                "note": "Per Wyo. Stat. Title 18 Ch. 5, no county building permit may be issued for structures in areas not adequately served by water or sewer until proposed sanitary facilities are approved. On rural and unincorporated parcels, secure septic/well sign-off before expecting the building permit to move.",
                "applies_to": "County-jurisdiction permits on parcels without municipal water/sewer",
                "source": "https://law.justia.com/codes/wyoming/2010/Title18/chapter5.html"
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
