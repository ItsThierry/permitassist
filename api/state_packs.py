"""State-specific expert packs for PermitAssist.

These packs are deterministic guardrails that are appended after model synthesis.
They are intentionally additive: they do not replace jurisdiction research, they
surface state-level gotchas that contractors should always see.
"""

from __future__ import annotations

from copy import deepcopy
import re

try:  # package import in tests/app
    from .evidence_eligibility import filter_state_expert_notes, is_commercial_ti_scope
except ImportError:  # direct script import
    from evidence_eligibility import filter_state_expert_notes, is_commercial_ti_scope

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

CALIFORNIA_CITY_FALLBACKS = {
    "san diego",
    "san jose",
    "sacramento",
    "fresno",
}

_CALIFORNIA_UTILITY_SCOPE_TERMS = (
    "service upgrade",
    "electrical service",
    "utility coordination",
    "utility interconnection",
    "electrical interconnection",
    "grid interconnection",
    "electrical meter",
    "utility meter",
    "meter base",
    "new service",
    "service panel",
    "main panel",
    "switchgear",
)


# State pack registry last updated 2026-04-27.
# States included: AZ, CA, CO, FL, GA, IL, NC, NY, TX, WA.
STATE_PACKS = {
    "AZ": {
        "name": "Arizona expert pack",
        "expert_notes": [
            {
                "title": "Arizona Registrar of Contractors (ROC) \u2014 residential vs commercial vs dual licensing",
                "note": "Arizona is unusual in that the ROC issues SEPARATE license classifications for residential, commercial, and dual (both) work \u2014 a contractor licensed only for commercial work cannot legally pull a residential permit and vice versa. Verify the license class matches the project type (e.g., B-General Residential Contractor, CR-class residential trade licenses, KB-General Commercial) before contracting; a mismatched class voids lien rights and exposes the contractor to ROC discipline. Check status at the ROC Customer Portal before signing.",
                "applies_to": "All licensed construction work in Arizona",
                "source": "https://roc.az.gov/license-classifications"
            },
            {
                "title": "$1,000 unlicensed-work threshold (handyman exemption)",
                "note": "Arizona requires an ROC license for any single project where the combined value of labor and materials exceeds $1,000, OR that requires a building permit regardless of value. Handyman work under $1,000 with no permit required is exempt, but bundling small jobs to stay under the cap is treated as unlicensed contracting. HVAC, electrical, and plumbing work above $1,000 specifically requires the matching trade classification (e.g., CR-39 Air Conditioning & Refrigeration, CR-11 Electrical, CR-37 Plumbing).",
                "applies_to": "All paid construction work in Arizona \u2265 $1,000 or requiring a permit",
                "source": "https://www.servicetitan.com/licensing/hvac/arizona"
            },
            {
                "title": "ARS \u00a7 9-835 / \u00a7 9-836 municipal permit shot clocks",
                "note": "Arizona Revised Statutes 9-835 (administrative completeness) and 9-836 (substantive review) require every municipality to publish licensing time-frames and to complete review within them \u2014 typically 30 working days for administrative completeness review and a published substantive review window for residential building permits. Missed deadlines entitle the applicant to a refund of a percentage of fees and can be escalated. Always demand the AHJ's published time-frame document and track the clock from the date the application is deemed administratively complete.",
                "applies_to": "All municipal building permit applications in Arizona",
                "source": "https://www.flagstaff.az.gov/DocumentCenter/View/72956/Residential-Building-Permit-Plan-Review-Process-and-Timeframes?bidId="
            },
            {
                "title": "New 15-day residential plan review requirement",
                "note": "A new Arizona state law shortens the substantive review timeline for certain residential permits to 15 working days, accelerating the prior ARS 9-836 framework. AHJs are responding with expedited-review tracks, model-plan libraries, and staff capacity additions. For straightforward SFR/ADU applications using model plans or master-plan certifications, push for the 15-day track and flag any AHJ that quotes a longer turnaround as out of statutory compliance.",
                "applies_to": "Residential building permits in incorporated Arizona municipalities",
                "source": "https://citizenportal.ai/articles/6136120/state-law-shortens-review-timeline-committee-backs-model-plans-and-staff-capacity-building"
            },
            {
                "title": "No statewide building code \u2014 106 separate AHJs",
                "note": "Arizona does NOT adopt a statewide building or residential code. Instead, all 15 counties and every incorporated municipality act as independent authorities having jurisdiction (AHJs), producing roughly 106 building-regulating jurisdictions statewide, each adopting their own edition of the IBC/IRC/IECC with local amendments. Always confirm the exact code edition and local amendments for the parcel's jurisdiction before drawing \u2014 Phoenix, Tucson, Mesa, and unincorporated Maricopa County can be on different code cycles simultaneously.",
                "applies_to": "All Arizona construction \u2014 code edition varies by jurisdiction",
                "source": "https://www.commonsenseinstituteus.org/arizona/research/housing-and-our-community/brief-a-review-of-arizona-building-permit-data"
            },
            {
                "title": "Phoenix 2024 PBCC adoption",
                "note": "On June 18, 2025, the Phoenix City Council adopted the 2024 Phoenix Building Construction Code (PBCC), which is based on the 2024 ICC family of codes including the 2024 IECC for energy. Projects in Phoenix submitted after the effective date must use the 2024 PBCC; projects in adjacent jurisdictions (Scottsdale, Tempe, Glendale, unincorporated Maricopa County) may still be on 2018 or 2021 cycles. Confirm the applicable PBCC edition with Phoenix PDD before plan-set finalization.",
                "applies_to": "City of Phoenix permit applications",
                "source": "https://www.phoenix.gov/administration/departments/pdd/tools-resources/codes-ordinance/building-code.html"
            },
            {
                "title": "Tucson / Pima County 2024 code adoption effective Jan 1, 2026",
                "note": "Tucson and Pima County jointly adopted updated building codes (2024 IBC/IRC/IECC family with local amendments) that took effect January 1, 2026, after a joint recommendation from the City Manager and Pima County administrators. Permits submitted on or after that date are reviewed under the new codes; applications in process before the cutoff may be grandfathered at the AHJ's discretion. Always confirm which edition applies on the specific submittal date.",
                "applies_to": "City of Tucson and Pima County permit applications crossing 2026-01-01",
                "source": "https://www.tucsonaz.gov/Departments/Planning-Development-Services/PDSD-News/Updated-Building-Codes-Adopted-Effective-January-1"
            },
            {
                "title": "Active Management Area (AMA) 100-year Assured Water Supply",
                "note": "Arizona law requires residential developers in designated groundwater Active Management Areas (Phoenix, Tucson, Pinal, Prescott, Santa Cruz) to obtain a Certificate of Assured Water Supply (CAWS) proving a 100-year water supply before plats can be approved or building permits issued for new subdivisions. ADWR has been under a moratorium on new groundwater-based CAWS in the Phoenix AMA since June 2023, and a 2026 court ruling further unsettled the rule \u2014 confirm CAWS status and whether the project sits inside an AMA before promising a build schedule.",
                "applies_to": "New residential subdivisions inside designated AMAs",
                "source": "https://jmc-eng.com/the-loophole-in-arizonas-water-rules-bypassing-the-certificate-of-assured-water-supply/"
            },
            {
                "title": "ARS \u00a7 11-321 \u2014 county building permits and utility preemption",
                "note": "ARS 11-321 governs county-issued building permits and explicitly preserves utility providers' separate authority to operate, meaning a county building permit does NOT authorize utility connection or interconnection on its own. The contractor must file a parallel utility application (service connection, meter set, or DG interconnection) with the serving utility \u2014 county permit issuance does not waive that step. Always run the county building permit and the utility filing in parallel to avoid a finished build that cannot be energized.",
                "applies_to": "Construction in unincorporated Arizona counties",
                "source": "https://www.azleg.gov/ars/11/00321.htm"
            },
            {
                "title": "APS distributed-generation interconnection \u2014 separate filing from building permit",
                "note": "Solar PV and battery systems in APS territory require a separate Interconnection Application filed with APS in addition to the AHJ building/electrical permit. APS may request a copy of the AHJ building permit before issuing Permission to Operate (PTO), and the system cannot legally export until PTO is granted. File the APS interconnection package as soon as the design is finalized \u2014 do not wait for the building permit, since the two reviews run in parallel and APS turnaround often controls the project's energization date.",
                "applies_to": "Residential solar PV and battery storage in APS service territory",
                "source": "https://www.aps.com/-/media/APS/APSCOM-PDFs/Residential/Service-Plans/Understanding-Solar/InterconnectReq.ashx"
            },
            {
                "title": "HB 2301 / Mesa solar permit pre-interconnection requirement",
                "note": "HB 2301 reshaped Arizona's solar permitting landscape effective 2026, and for Mesa Electric Utility customers specifically, the interconnection application must be received and accepted BEFORE the system is installed (not after as in most jurisdictions). Installing first and applying after is grounds for denial of interconnection. Verify the serving utility's sequencing rule (APS, SRP, TEP, Mesa, and municipal utilities each have their own) before scheduling installation.",
                "applies_to": "Residential solar PV in Mesa Electric Utility territory",
                "source": "https://www.solarpermitsolutions.com/blog/arizona-solar-permits-2026-requirements-fees-hb2301-guide"
            },
            {
                "title": "Owner-builder affidavit required on every permit (ARS 11-1605)",
                "note": "Per ARS 11-1605, every Arizona building permit requires written documentation of either an Arizona-licensed contractor pulling the permit OR an owner-builder classification signed by the property owner. Owner-builder permits restrict resale within one year (the owner must occupy and cannot have built for sale) and shift all liability to the owner. Counties (e.g., Maricopa) will not accept a permit application without this signed contractor/owner-builder declaration \u2014 prepare it before counter intake.",
                "applies_to": "All Arizona building permit applications",
                "source": "https://www.maricopa.gov/5241/FAQs"
            },
            {
                "title": "Rezoning shot clock \u2014 separate from permit shot clock",
                "note": "A new Arizona development 'shot clock' law imposes statutory time limits on rezoning decisions, distinct from the ARS 9-835/9-836 permit clocks. Local ordinances (e.g., Prescott Valley 13-14-070) implement parallel administrative-completeness and substantive-review windows for rezoning applications. If a project requires rezoning before permitting, treat it as TWO sequential clocks (rezoning, then permit) \u2014 and document each completeness determination in writing to preserve refund/escalation rights.",
                "applies_to": "Projects requiring rezoning prior to building permit",
                "source": "https://codelibrary.amlegal.com/codes/prescottvalleyaz/latest/prescottvalley_az/0-0-0-45446"
            },
            {
                "title": "ROC license filing address and portal",
                "note": "ROC license applications, renewals, and complaints are filed via the ROC Customer Portal online, or by mail to Registrar of Contractors, P.O. Box 6748, Phoenix, AZ 85005-6748, or in person at 1700 West Washington Street, Suite 105, Phoenix, AZ 85007-2812. Use the portal for fastest turnaround and to pull license ID cards on demand. Expired or suspended licenses block permit issuance at most AHJ counters \u2014 verify status the day of submittal.",
                "applies_to": "Contractor license maintenance and verification",
                "source": "https://roc.az.gov/"
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
                "title": "No statewide residential building code \u2014 AHJ patchwork",
                "note": "Colorado does not have a unified statewide residential or building code adopted by a single state agency; each of the state's 64 counties (roughly 60% unincorporated land) and every municipality adopts and amends its own I-codes. Always confirm the exact code edition and local amendments with the building department of the specific city, county, or regional building department (e.g., PPRBD for Pikes Peak region) before drawing plans \u2014 what is permitted in unincorporated Jefferson County may not pass plan check in Denver.",
                "applies_to": "All residential and commercial permit work statewide",
                "source": "https://evstudio.com/understanding-colorados-diverse-building-jurisdictions/"
            },
            {
                "title": "Statewide electrical & plumbing licenses required (no statewide GC license)",
                "note": "Colorado requires state-issued licenses for electrical and plumbing contractors through the DORA Division of Professions and Occupations (DPO), but there is NO statewide general contractor license \u2014 GC licensing is handled at the municipal level (e.g., Denver Class A/B/C/D, Aurora, Boulder all run their own contractor registries). Verify electrical (Journeyman, Master, or Electrical Contractor) and plumbing (Apprentice, Residential, Journeyman, Master, Plumbing Contractor) credentials on DPO before signing a contract; an unlicensed trade contractor cannot pull a permit and cannot lien.",
                "applies_to": "All residential and commercial construction in Colorado",
                "source": "https://www.procore.com/library/colorado-contractors-license"
            },
            {
                "title": "State-issued electrical & plumbing permits via DPO (parallel to building permit)",
                "note": "In jurisdictions that do not run their own electrical/plumbing inspection program, electrical and plumbing permits are issued directly by the Colorado DPO Electrical and Plumbing Permits office (1560 Broadway Suite 1350, Denver) \u2014 separate from the local building permit. Registered Electrical Contractors and registered Plumbing Contractors may pull unlimited state permits; fire alarm installers also receive permits through DPO. Confirm whether the AHJ delegates inspection or whether a parallel state filing is required, otherwise rough-in inspections will be rejected.",
                "applies_to": "Electrical and plumbing scopes outside delegated municipal inspection programs",
                "source": "https://www.dora.state.co.us/pls/real/ep_web_faq_gui.show_page"
            },
            {
                "title": "2021 IECC residential energy code is the statewide default floor (since Jan 29, 2023)",
                "note": "Effective January 29, 2023, the 2021 IECC-R became Colorado's default residential energy code with no state-level amendments per the State Building Code Council. Local jurisdictions updating their building codes after July 1, 2023 must adopt energy codes at least as stringent as the 2021 IECC plus the state's Model Electric Ready and Solar Ready Code. Plans showing only 2018 IECC compliance values (e.g., undersized wall R-value, missing blower-door target) will be rejected at energy review.",
                "applies_to": "New residential construction and additions creating conditioned space",
                "source": "https://database.aceee.org/state/buildings-summary"
            },
            {
                "title": "Model Electric Ready & Solar Ready Code \u2014 EV raceway + 300 sq ft solar zone",
                "note": "Colorado's Energy Code Board model code (required as part of any post-2025 code update under HB22-1362) mandates new homes be wired/structured for future electrification: dedicated EV-ready 240V circuit and panel capacity in garages, electric-ready provisions for HVAC and water heating, and a designated solar-ready roof zone of not less than 300 sq ft (excluding mandatory access/setback areas per IFC). Drawings must call out the reserved solar zone and unobstructed conduit pathway from roof to main panel \u2014 omitting these is the most common 2026 plan-check rejection.",
                "applies_to": "New low-rise residential, ADUs, and major renovations in jurisdictions that have adopted the model code",
                "source": "https://mtcb.colorado.gov/sites/mtcb/files/documents/Model%20Colorado%20Electric%20and%20Solar%20Ready%20Code.pdf"
            },
            {
                "title": "Denver's 2024 I-code adoption (effective 2025) \u2014 confirm transition rules",
                "note": "On June 13, 2025 the City and County of Denver adopted the 2024 family of International Codes (IBC, IRC, IECC, IMC, IPC, IFC, IFGC) along with local amendments. Applications submitted before the effective date may be reviewable under the prior 2021 codes at Denver Community Planning and Development's discretion \u2014 verify in writing which edition governs your submittal and budget for the stricter 2024 envelope, mechanical, and EV-ready requirements when designing.",
                "applies_to": "Denver permit applications crossing the 2025 code-change boundary",
                "source": "https://denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Community-Planning-and-Development/Building-Codes-Policies-and-Guides/Building-and-Fire-Code-Adoption-Process"
            },
            {
                "title": "HB24-1152 ADU mandate \u2014 ministerial ADU path in 'Subject Jurisdictions' since June 30, 2025",
                "note": "Colorado House Bill 24-1152 requires designated 'Subject Jurisdictions' (generally larger metro and resort municipalities meeting population/MPO criteria) to allow at least one ADU on any lot zoned for single-unit detached dwellings as of June 30, 2025. Non-subject rural counties and small towns are NOT required to allow ADUs \u2014 confirm jurisdiction status with the Colorado Division of Local Government before quoting an ADU job. Subject jurisdictions cannot impose owner-occupancy requirements or off-street parking minimums beyond what the statute allows.",
                "applies_to": "ADU projects in HB24-1152 Subject Jurisdictions",
                "source": "https://dlg.colorado.gov/accessory-dwelling-units"
            },
            {
                "title": "Realistic permit timelines \u2014 Denver 180 days, Colorado Springs 28+14",
                "note": "There is no statewide ministerial shot clock for residential permits. Denver residential permits for additions, ADUs, and major remodels are running approximately 180 days end-to-end in 2026; Colorado Springs (PPRBD) runs roughly 28 days for initial review and 14 days per resubmittal cycle (45\u201360 days typical). Build these timelines into client contracts and avoid promising a 30-day turnaround in Front Range metros.",
                "applies_to": "Residential additions, ADUs, and major remodels in Front Range metros",
                "source": "https://sdb-denver.com/2026/the-construction-industry/denver-permit-timelines-in-2026-what-homeowners-should-expect/"
            },
            {
                "title": "WUI / Appendix K wildfire code \u2014 fire-official sign-off before permit issuance",
                "note": "Colorado adopted statewide wildfire-resistant building requirements (largely tracking the 2021 IWUIC and IRC Appendix K) for properties in mapped Wildland-Urban Interface areas. All WUI requirements \u2014 Class A roof assembly, ignition-resistant siding/eaves/soffits, ember-resistant vents, defensible space, and emergency vehicle access/water supply per IWUIC Chapter 4 \u2014 must be reviewed and approved by the fire code official BEFORE permit issuance and again prior to framing inspection. Some jurisdictions (e.g., Loveland Fire Rescue Authority via LFRA) require a separate WUI Building Permit Application and Checklist filed directly with the fire authority.",
                "applies_to": "New construction, additions, and reroofs in mapped WUI areas",
                "source": "https://planningforhazards.colorado.gov/wildland-urban-interface-code"
            },
            {
                "title": "Solar PV interconnection \u2014 separate utility filing per utility (Xcel, co-ops, munis)",
                "note": "Colorado's 22 rural electric cooperatives and municipal utilities each set their own interconnection rules independently of the Public Utilities Commission's investor-owned utility rules (4 CCR 723-3 \u00a7\u00a73850\u20133859, which govern Xcel Energy and Black Hills). Every solar PV / battery project requires a SEPARATE interconnection application to the serving utility (Xcel uses the DER Interconnection Portal) on top of the local building/electrical permit \u2014 net-metering credit and PTO will not issue without this parallel filing. Confirm the utility's specific size thresholds (\u226410 kW Level 1, \u22642 MW Level 2/3) before quoting.",
                "applies_to": "Solar PV, battery storage, and other DER installations",
                "source": "https://coloradosolarauthority.com/colorado-solar-interconnection-process"
            },
            {
                "title": "Denver contractor licensing \u2014 state cards substitute for supervisor certificate",
                "note": "Denver requires its own contractor license (Class A, B, C, or D depending on building height/area) for any GC pulling permits in the city. For trade contractors, a current State of Colorado Electrical Contractor card or Master Plumber card SUBSTITUTES for Denver's supervisor certificate requirement, but the state card and master credential must be uploaded with the application and renewed on Denver's plumbing-state-card schedule. Lapsed state cards block Denver permit issuance even if the Denver license is otherwise current.",
                "applies_to": "Electrical and plumbing contractors operating in Denver",
                "source": "https://denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Community-Planning-and-Development/Contractor-Licensing/Contractor-Licenses/Apply-for-a-Contractor-License"
            },
            {
                "title": "Electrical license classes \u2014 Journeyman vs Master vs Electrical Contractor",
                "note": "The Colorado State Electrical Board issues three license tiers: Journeyman Electrician (works under supervision), Master Electrician (can supervise and design), and Electrical Contractor (the business registration required to pull permits). All three are distinct credentials \u2014 a Master Electrician cannot pull permits in the company's name without the company also being a registered Electrical Contractor. Verify both the individual master and the company contractor registration on DPO before allowing a sub to pull power.",
                "applies_to": "Electrical scopes including service upgrades, EV chargers, and solar PV",
                "source": "https://coloradocontractorauthority.com/colorado-contractor-license-types"
            },
            {
                "title": "PPRBD / regional building departments \u2014 June 30 energy-code adoption window",
                "note": "Pikes Peak Regional Building Department (serving El Paso County, Colorado Springs, and several incorporated municipalities) and other regional departments time their code adoptions to the state's July 1 statutory deadlines \u2014 PPRBD adopted updated energy codes effective June 30 to come into compliance one day before the state mandate, sweeping away prior local amendments. Permits submitted on or after the adoption date are reviewed under the new code with no grandfathering of prior amendments; lock in submittal dates accordingly.",
                "applies_to": "Permits in PPRBD jurisdiction and other regional building departments",
                "source": "https://www.facebook.com/PPRegionalBuilding/posts/due-to-state-law-pprbd-will-be-changing-energy-codesstarting-on-june-30-pprbd-wi/1342721677901477/"
            },
            {
                "title": "Arvada 2026 code rollout \u2014 builder-impacting amendments to plan around",
                "note": "The City of Arvada is rolling out a 2026 building-code update with a published list of detailed changes affecting builders, designers, developers, and homeowners (envelope, mechanical, electric-ready, and WUI-adjacent provisions). Review Arvada's published change list before submitting any 2026 application in the city \u2014 drawings prepared under the prior code will trigger redlines on energy compliance, EV-ready raceway, and mechanical sizing.",
                "applies_to": "Permit applications in Arvada submitted in or after 2026",
                "source": "https://www.arvadaco.gov/1446/For-Builders-Detailed-changes-coming-in-"
            },
            {
                "title": "PUC interconnection rules apply only to IOUs \u2014 co-ops/munis are independent",
                "note": "The Colorado Public Utilities Commission (1560 Broadway Suite 250, Denver; 303-894-2000) regulates interconnection agreements only for investor-owned utilities (Xcel Energy, Black Hills Energy). Customers served by municipal utilities (Colorado Springs Utilities, Fort Collins, Longmont, etc.) or rural electric cooperatives (per the Colorado Rural Electric Association) are governed by that utility's own tariff, not PUC rules \u2014 do NOT cite 4 CCR 723-3 to a co-op customer. Always confirm the serving utility's regulatory category before promising a net-metering timeline.",
                "applies_to": "Solar PV, battery, and DER interconnection across utility types",
                "source": "https://puc.colorado.gov/ica"
            }
        ]
    },
    "FL": {
        "name": "Florida expert pack",
        "expert_notes": [
            {
                "title": "Florida HB 267 permit shot clock \u2014 553.792",
                "note": "Florida Statute 553.792 sets statutory turnaround windows: building departments must approve, deny, or request additional info on a complete application within specific business-day limits (e.g., 60 business days for projects using a local plans reviewer, with shorter clocks for one/two-family residential and minor work). Missed deadlines can entitle the applicant to a fee refund and escalation. Always submit a date-stamped, complete application package \u2014 incomplete submittals reset the clock.",
                "applies_to": "All building permit applications in Florida",
                "source": "https://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&Search_String=&URL=0500-0599/0553/Sections/0553.792.html"
            },
            {
                "title": "High-Velocity Hurricane Zone (HVHZ) \u2014 Miami-Dade & Broward",
                "note": "Miami-Dade and Broward counties are designated High-Velocity Hurricane Zones under FBC Chapters 15/16 and FRC Chapter 44. HVHZ jurisdictions require Notice of Acceptance (NOA) approved products for roofing, windows, doors, and shutters, plus the HVHZ Uniform Roofing Permit Application. Standard FBC product approvals are NOT sufficient \u2014 verify each component has an active Miami-Dade NOA before specifying.",
                "applies_to": "Projects in Miami-Dade and Broward counties (HVHZ)",
                "source": "https://up.codes/viewer/florida/fl-residential-code-2023/chapter/44/high-velocity-hurricane-zones"
            },
            {
                "title": "FBC 8th Edition (2023) currently in force; 9th Edition (2026) coming",
                "note": "The Florida Building Code, 8th Edition (2023), based on the 2021 I-Codes, became effective December 31, 2023 and governs all permits today. The 9th Edition (2026) is scheduled to take effect December 31, 2026. Applications submitted before the changeover may vest under the 8th Edition \u2014 confirm submittal date and which edition the AHJ is reviewing under, especially for projects designed in late 2026.",
                "applies_to": "Permits crossing the 2026-12-31 code-change boundary",
                "source": "https://www.floridabuilding.org/"
            },
            {
                "title": "9th Edition (2026) FBC pending changes \u2014 design now, build later",
                "note": "The 9th Edition Florida Building Code is being finalized with effective date December 31, 2026, including updates to roofing, energy, and product-approval provisions. Drawings produced in 2026 for permits pulled after the effective date should be designed to the 9th Edition to avoid resubmittal. Confirm with each AHJ whether they will allow vesting under the 8th Edition based on application date.",
                "applies_to": "New residential designs anticipated for late-2026/2027 permitting",
                "source": "https://www.floridaroof.com/FBC-2026-Updates"
            },
            {
                "title": "DBPR Certified vs Registered contractor licenses",
                "note": "Florida DBPR issues two tiers: a Certified license (statewide; valid in any Florida jurisdiction) and a Registered license (valid only in the local competency-board area that issued it). Verify the contractor's tier matches the project location before contracting \u2014 a Registered Polk County electrician cannot legally pull a permit in Orange County. Certified and Registered electrical/HVAC/plumbing licenses must be checked at MyFloridaLicense.com.",
                "applies_to": "Contractor selection and license verification statewide",
                "source": "https://www2.myfloridalicense.com/electrical-contractors/"
            },
            {
                "title": "DBPR construction license classes \u2014 GC, BC, RC, and trade subs",
                "note": "Florida construction licensing under Chapter 489 distinguishes General Contractor (unlimited), Building Contractor (\u22643 stories commercial, unlimited residential), Residential Contractor (1-2 family + accessory \u22642 stories), and trade licenses including Class A Air-Conditioning, Sheet Metal, and Roofing Contractor. The class determines what scope can legally be permitted \u2014 a Residential Contractor cannot pull a permit for a 4-story commercial project. Confirm classification on every quote.",
                "applies_to": "All paid construction work statewide; license-class scoping",
                "source": "https://www2.myfloridalicense.com/construction-industry/"
            },
            {
                "title": "Notice of Commencement (NOC) \u2014 required before first inspection",
                "note": "Florida Statute 713.135 requires a Notice of Commencement to be recorded with the county clerk and posted on-site BEFORE the first inspection on any permitted project over $2,500 (HVAC repair/replacement threshold is $15,000 per Fla. Stat. \u00a7 713.02(5)). Failure to record the NOC exposes the owner to paying twice if subs file liens, and inspectors will fail the first inspection without a posted NOC. Record it the same day the permit is issued.",
                "applies_to": "Permitted projects over $2,500 (HVAC > $15,000)",
                "source": "https://www.leg.state.fl.us/Statutes/index.cfm?App_mode=Display_Statute&URL=0700-0799/0713/Sections/0713.135.html"
            },
            {
                "title": "Notice to Owner \u2014 45-day sub/supplier deadline",
                "note": "Subcontractors and material suppliers not in direct contract with the owner must serve a Notice to Owner within 45 days of first furnishing labor or materials to preserve lien rights under Florida's Construction Lien Law. Owners should track NTOs received and require lien releases at each draw. Missing the 45-day window forfeits lien rights, but owners still need to track all NTOs to ensure full release at closeout.",
                "applies_to": "Subcontractors, material suppliers, and owners managing draws",
                "source": "https://www.jcohenpa.com/understanding-the-notice-of-commencement-in-florida-construction-law/"
            },
            {
                "title": "Owner-builder permit path \u2014 489.103 affidavit",
                "note": "Florida Statute 489.103 allows an owner to pull their own permit for a 1-2 family residence on land they own, provided they personally supervise construction and do not hire unlicensed labor. The owner must sign a disclosure affidavit acknowledging the rules, and the home cannot be sold or rented within one year without rebutting a presumption that the owner built for sale. Cannot be used to shield unlicensed contractors \u2014 DBPR actively prosecutes straw-owner schemes.",
                "applies_to": "Owner-pulled permits on owner-occupied 1-2 family homes",
                "source": "https://www.leg.state.fl.us/Statutes/index.cfm?App_mode=Display_Statute&URL=0400-0499/0489/Sections/0489.103.html"
            },
            {
                "title": "Jurisdiction split \u2014 municipal vs county building departments",
                "note": "Florida has no single statewide permit office; each incorporated municipality runs its own building department, while unincorporated parcels fall to the county. Two parcels on the same street can be reviewed by entirely different departments with different submittal portals, fees, and turnaround. Always confirm AHJ via the property's parcel ID at the county property appraiser before assuming city or county jurisdiction.",
                "applies_to": "Determining the correct AHJ for any Florida project",
                "source": "https://www.elitepermits.com/find-the-municipality-jurisdiction-you-are-in/"
            },
            {
                "title": "Florida Energy Efficiency Disclosure at sale",
                "note": "Florida law requires sellers of residential real property to inform prospective buyers of their right to obtain an energy-efficiency rating for the home before executing the sales contract. The disclosure does not mandate a rating be performed \u2014 only that the buyer be told they may request one. Standard FAR/BAR contracts include the language; flag it during permit closeout if the home will be sold so the seller's disclosure package is complete.",
                "applies_to": "Residential property sales statewide",
                "source": "https://www.siegfriedrivera.com/blog/understanding-floridas-energy-efficiency-disclosure-law/"
            },
            {
                "title": "Flood disclosure (FD-1) required at contract execution",
                "note": "Florida Statutes require sellers to provide a Flood Disclosure (form FD-1) to buyers at or before the sales contract is executed, covering prior flood claims, FEMA assistance received, and flood-zone status. This is separate from the energy disclosure and is not waivable. For permit work in coastal and SFHA parcels, also confirm finished-floor elevation against the FIRM panel before submitting plans, since FBC flood provisions and NFIP minimums both apply.",
                "applies_to": "Residential sales and coastal/SFHA permit work",
                "source": "https://www.floridarealtors.org/law-ethics/library/florida-real-estate-disclosure-laws"
            },
            {
                "title": "HVHZ roofing \u2014 Uniform Permit Application required",
                "note": "All roofing work in HVHZ jurisdictions (Miami-Dade, Broward) \u2014 including new construction, recover, reroof, repair, and maintenance \u2014 must be submitted on the HVHZ Uniform Roofing Permit Application per FBC Chapter 15. The form requires NOA numbers for each roofing component, wind-uplift pressure calculations, and a sealed roof system specification. Generic roofing permit applications are rejected at intake in HVHZ jurisdictions.",
                "applies_to": "All roofing scopes in Miami-Dade and Broward",
                "source": "https://www.floridabuilding.org/fbc/thecode/2013_Code_Development/HVHZ/FBCB/Chapter_15_2010.htm"
            },
            {
                "title": "Electrical contractor license renewal \u2014 even-year August 31",
                "note": "Florida certified and registered electrical contractor licenses expire August 31 of every even-numbered year (next: 2026-08-31). DBPR sends a renewal notice when the window opens, but the license must be active on the date the permit is pulled \u2014 an expired license voids the permit and exposes the contractor to unlicensed-contracting penalties. Verify the renewal status at MyFloridaLicense.com before each permit submittal in the renewal year.",
                "applies_to": "Electrical contractor permit pulls in renewal years",
                "source": "https://www2.myfloridalicense.com/electrical-contractors/"
            }
        ]
    },
    "GA": {
        "name": "Georgia expert pack",
        "expert_notes": [
            {
                "title": "Georgia State Minimum Standard Codes \u2014 2026 enforcement boundary",
                "note": "Georgia DCA adopted new State Minimum Standard Codes effective January 1, 2026, including the 2024 International Residential Code with Georgia Amendments and the 2026 Georgia Amendments to the 2023 National Electrical Code. Permit applications submitted before 2026-01-01 may still be reviewed under the prior code edition at the AHJ's discretion \u2014 confirm which edition the local building department is enforcing before stamping drawings, because mid-cycle resubmittals can flip code versions.",
                "applies_to": "All permit applications crossing the 2026-01-01 code-change boundary",
                "source": "https://dca.georgia.gov/announcement/2025-12-09/new-codes-jan-2026"
            },
            {
                "title": "Mandatory vs permissive State Minimum Standard Codes",
                "note": "Georgia divides its State Minimum Standard Codes into mandatory codes (Building, Residential, Plumbing, Mechanical, Gas, Electrical, Energy, Fire, Swimming Pool) that apply statewide whether or not a local government enforces them, and permissive codes (Property Maintenance, NGBS) that apply only if the local jurisdiction adopts them. Local governments that elect to enforce the codes must adopt administrative procedures and penalties \u2014 meaning permitting and inspection requirements vary by jurisdiction even though the technical code is uniform.",
                "applies_to": "All construction subject to Georgia State Minimum Standard Codes",
                "source": "https://rules.sos.ga.gov/gac/110-11-1"
            },
            {
                "title": "Georgia residential energy code is 2015 IECC with amendments \u2014 not the latest IECC",
                "note": "Per the DOE Building Energy Codes Program, Georgia's current residential energy code is the 2015 IECC with Georgia Amendments, and commercial is 2015 IECC / ASHRAE 90.1-2013 with amendments. Do not assume the 2021 or 2024 IECC envelope, duct-leakage, or mechanical ventilation thresholds apply \u2014 Georgia's amended baseline is materially less stringent and energy compliance forms must reference the Georgia-amended 2015 IECC.",
                "applies_to": "Residential and commercial energy compliance documentation",
                "source": "https://www.energycodes.gov/status/states/georgia"
            },
            {
                "title": "Residential General Contractor licensing threshold",
                "note": "The Georgia State Licensing Board for Residential and Commercial General Contractors requires a state contractor license for residential projects with a contract value above the statutory threshold; the board sits under the Secretary of State's Professional Licensing Boards Division (3920 Arkwright Rd., Suite 195, Macon). Verify license type (Residential-Basic, Residential-Light Commercial, or General Contractor) and active status before signing \u2014 unlicensed contracting blocks lien rights and can void the contract.",
                "applies_to": "Residential and light-commercial construction contracts in Georgia",
                "source": "https://sos.ga.gov/state-licensing-board-residential-and-commercial-general-contractors"
            },
            {
                "title": "Conditioned Air Contractor license required for HVAC work",
                "note": "HVAC installation, replacement, and ductwork in Georgia requires a Conditioned Air Contractor license issued by the Georgia State Board of Conditioned Air Contractors (a division of the State Construction Industry Licensing Board). General contractor licensure does NOT cover conditioned-air work \u2014 a separately licensed CN contractor (Class I under 175,000 BTU/h or Class II unrestricted) must pull the mechanical permit and be on the job.",
                "applies_to": "All HVAC installations, replacements, and duct alterations",
                "source": "https://sos.ga.gov/georgia-state-board-conditioned-air-contractors"
            },
            {
                "title": "Statewide trades licensing \u2014 separate boards per trade",
                "note": "Georgia licenses electrical, plumbing, conditioned air, low-voltage, and utility contractors through separate divisions of the State Construction Industry Licensing Board, each with its own classifications (e.g., Electrical Class I/II, Plumbing Class I/II, Conditioned Air Class I/II). A residential general contractor cannot self-perform regulated trade work above unlicensed-helper limits \u2014 verify each sub holds the correct class for the project scope before listing them on the permit.",
                "applies_to": "Multi-trade residential and commercial projects",
                "source": "https://contractorlicensinginc.com/national-contractor-licensing/georgia-contractor-license/"
            },
            {
                "title": "OCI Professional Services \u2014 state-level inspections and plan review",
                "note": "Georgia's Office of Commissioner of Insurance and Safety Fire (OCI) operates Professional Services online for state-level building inspections, plan submittals, and fire-marshal review of state-regulated occupancies (state buildings, hospitals, schools, hotels, daycares, large assemblies). Most 1-2 family residential work is reviewed by the local AHJ, not OCI \u2014 but state-regulated occupancies require a parallel OCI plan review on top of the local building permit.",
                "applies_to": "State-regulated occupancies (schools, hospitals, hotels, daycares, large assemblies)",
                "source": "https://oci.georgia.gov/inspections-permits-plans"
            },
            {
                "title": "HB 493 \u2014 45-day local permit review deadline",
                "note": "Under Georgia HB 493 (signed 2025), city and county building departments must review construction plans for compliance with the State Minimum Standard Codes within 45 days of a complete application. If the AHJ exceeds 45 days without issuing a determination, the applicant has statutory grounds to escalate \u2014 track the complete-application receipt date because the clock resets only if the AHJ issues a written incomplete-application notice.",
                "applies_to": "Local building permit reviews under State Minimum Standard Codes",
                "source": "https://gov.georgia.gov/document/signed-legislation/hb-493pdf/download"
            },
            {
                "title": "No statewide ADU shot clock \u2014 ADUs follow local zoning",
                "note": "Unlike California's 60-day ministerial ADU clock, Georgia has NO statewide ADU shot clock or ministerial-approval mandate. ADU permitting timelines, owner-occupancy rules, parking minimums, and whether ADUs are allowed at all are set entirely by local zoning (Atlanta, Savannah, and Decatur each have different ADU ordinances). Do not promise a 60-day or by-right ADU path \u2014 confirm the local ordinance before quoting timelines.",
                "applies_to": "Accessory dwelling unit projects statewide",
                "source": "https://www.bluejuniperconstruction.com/post/new-georgia-building-codes-coming-in-2026-what-atlanta-homeowners-need-to-know-before-remodeling"
            },
            {
                "title": "Coastal Marshlands Protection Act \u2014 separate DNR permit",
                "note": "Any project that removes, fills, dredges, drains, or otherwise alters jurisdictional marsh or tidal water bodies in the six coastal counties requires a Coastal Marshlands Protection Act permit from the Georgia DNR Coastal Resources Division (Brunswick), reviewed by the Coastal Marshlands Protection Committee under Ga. Comp. R. & Regs. 391-2-3. Docks, shoreline stabilization, and marinas need a jurisdictional determination from CRD BEFORE the local building permit \u2014 this is a parallel filing, not a step inside the building permit.",
                "applies_to": "Coastal county projects affecting marsh, tidal waters, docks, shoreline stabilization",
                "source": "https://coastalgadnr.org/MarshShore"
            },
            {
                "title": "Floodplain permits via local NFIP-participating jurisdictions",
                "note": "Georgia has no statewide floodplain building permit; instead, FEMA-mapped Special Flood Hazard Areas are regulated by NFIP-participating local governments using the GA EPD Floodplain Management program's address-level flood-risk lookup. For any work in an SFHA, the local floodplain administrator must approve elevation certificates, lowest-floor elevation, and flood-vent design as a parallel review to the building permit \u2014 substantial-improvement (>50% market value) triggers full bring-into-compliance.",
                "applies_to": "Any construction or substantial improvement within a FEMA Special Flood Hazard Area",
                "source": "https://epd.georgia.gov/watershed-protection-branch/floodplain-management"
            },
            {
                "title": "Georgia Power solar / DER interconnection \u2014 separate from building permit",
                "note": "Solar PV, battery storage, and other Distributed Energy Resources connecting to Georgia Power must go through the Georgia Power Interconnection Guidance (ICG) process, with net-metering rules administered by the Georgia Public Service Commission. The interconnection application, witness test, and Permission-to-Operate letter are separate from the local electrical permit and AHJ inspection \u2014 schedule both tracks in parallel and do not energize until PTO is issued. Customers of EMCs or municipal utilities (e.g., MEAG cities) follow their own GIP procedures, not Georgia Power's.",
                "applies_to": "Solar PV, battery storage, and generator interconnection projects",
                "source": "https://www.georgiapower.com/business/products-programs/business-solutions/commercial-solar-solutions/distributed-generation/interconnection.html"
            },
            {
                "title": "MEAG / municipal utility interconnection uses its own GIP",
                "note": "Customers served by MEAG Power or one of Georgia's municipal electric utilities follow the MEAG Standard Generator Interconnection Procedures (GIP), including the Interconnection System Impact Study Agreement in Appendix 3, rather than Georgia Power's ICG. Confirm the serving utility on the meter BEFORE filing \u2014 submitting the wrong utility's interconnection package is a common cause of multi-week delays on residential solar + battery projects.",
                "applies_to": "Solar/DER projects in MEAG and municipal-utility service territories",
                "source": "https://www.oasis.oati.com/MEAG/MEAGdocs/GIP-rev6.pdf"
            },
            {
                "title": "GDOT utility encroachment permits via GPAS",
                "note": "Any utility facility (water, sewer, electric, gas, fiber) encroaching on a state route right-of-way requires a Utility Facility Encroachment Permit submitted through the Georgia Permit Application System (GPAS) operated by GDOT. This is a separate filing from the local building permit and from the utility's own interconnection process \u2014 driveway tie-ins, service drops crossing a state route, and trenching within state ROW all trigger GPAS review.",
                "applies_to": "Utility work or driveways encroaching on a Georgia DOT state route right-of-way",
                "source": "http://www.dot.ga.gov/GDOT/pages/utilitypermitting.aspx"
            }
        ]
    },
    "IL": {
        "name": "Illinois expert pack",
        "expert_notes": [
            {
                "title": "2024 IECC adoption \u2014 effective Nov 30, 2025 statewide",
                "note": "Illinois adopted the 2024 edition of the International Energy Conservation Code with Illinois amendments; it became enforceable statewide on 11/30/2025 under 20 ILCS 3125/15. All residential buildings must comply with the Illinois Energy Conservation Code regardless of local home-rule status \u2014 there is no local opt-out. Confirm which IECC edition the AHJ is plan-checking against before submitting drawings, as applications in process at the transition may still be reviewed under the prior 2021 IECC at AHJ discretion.",
                "applies_to": "All residential and commercial new construction and additions statewide",
                "source": "https://cdb.illinois.gov/business/codes/illinois-energy-codes/illinois-energy-conservation-code.html"
            },
            {
                "title": "Illinois Stretch Energy Code \u2014 only mandatory in opt-in jurisdictions",
                "note": "The Illinois Stretch Energy Code (20 ILCS 3125/55), based on the 2021 IECC with site-energy-index amendments, took effect 1/1/2025 but only applies in municipalities and counties that have formally adopted it. The base Illinois Energy Conservation Code applies elsewhere. Verify with the AHJ which code path applies before pricing \u2014 the Stretch Code's lower site-energy-index targets typically force higher-performance envelopes, heat pumps, or PV.",
                "applies_to": "Residential projects in Stretch-Code-adopting municipalities (Chicago, Evanston, Oak Park, etc.)",
                "source": "https://www.buildinghub.energy/2023-il-residential-stretch-code-guide"
            },
            {
                "title": "No statewide residential building code for 1\u20132 family homes",
                "note": "Unlike California or New York, Illinois has NO statewide adopted residential building code for one- and two-family dwellings \u2014 building/residential code is set by each municipality or, in unincorporated areas, by the county. The Illinois Energy Conservation Code is the only statewide-mandated construction code applying to residential work. Always pull the AHJ's locally adopted IRC/IBC edition before assuming code year; neighboring towns can be on different cycles.",
                "applies_to": "Residential projects determining which IRC/IBC edition governs",
                "source": "https://iml.org/buildingcodes"
            },
            {
                "title": "Illinois Plumbing License \u2014 state-issued 055 license required",
                "note": "Per the Illinois Plumbing License Law (225 ILCS 320), all plumbing work must be performed by an IDPH-licensed plumber, and contracting for plumbing requires a State of Illinois Plumbing Contractor's License (055 prefix). Plumbing is regulated at the state level \u2014 municipalities cannot issue their own plumbing licenses but require a copy of the state license on file before pulling permits. Verify the 055 license is active before quoting; expired licenses void permit eligibility.",
                "applies_to": "All plumbing work statewide",
                "source": "https://elginil.gov/311/Contractor-Requirements"
            },
            {
                "title": "Illinois Roofing Contractor license \u2014 IDFPR-issued, statewide",
                "note": "Roofing is one of only two construction trades licensed at the state level (the other being plumbing). IDFPR issues the Illinois Roofing Contractor license; applicants must be 18+, carry liability and workers' comp insurance, pass the state roofing exam, and pay the license fee. Cook County requires additional contractor registration on top of the state license. Unlicensed roofing contracting is unenforceable and exposes the contractor to IDFPR discipline.",
                "applies_to": "All residential and commercial roofing work statewide",
                "source": "https://idfpr.illinois.gov/profs/roof.html"
            },
            {
                "title": "Electrical contractor licensing is local, not state",
                "note": "Illinois does NOT issue a statewide electrical contractor license \u2014 electrical licensing is municipal. Most suburban AHJs accept either a local electrical license or a current City of Chicago Supervising Electrician license; some require both annual local registration ($25 typical) plus the underlying Chicago/local license. Always confirm reciprocity with the specific AHJ before quoting; a license accepted in Wilmette may not be accepted in Naperville.",
                "applies_to": "All residential and commercial electrical work in Illinois municipalities",
                "source": "https://www.wilmette.gov/185/Contractor-Licensing-Requirements"
            },
            {
                "title": "Illinois HVAC licensing \u2014 state registration of contractors",
                "note": "HVAC contractors must register with the State of Illinois under the HVAC Contractor Certification program; some municipalities additionally require a local HVAC license or registration. Verify the contractor's state HVAC registration is current and that any city-specific registration (e.g., Chicago, Elgin) is also on file before pulling mechanical permits. Lack of registration is a common cause of permit-counter rejection.",
                "applies_to": "All HVAC and mechanical work statewide",
                "source": "https://www.servicetitan.com/licensing/hvac/illinois"
            },
            {
                "title": "Chicago ADU ordinance \u2014 pilot zones only, not citywide",
                "note": "Chicago's ADU ordinance ended a ~70-year ban and legalized attic/basement conversions and coach houses, but only within designated pilot zones \u2014 it is NOT a citywide right. Applications go through the Department of Housing in addition to DOB plan review. Outside Chicago, most Illinois municipalities still treat ADUs as accessory structures under standard zoning (no statewide ministerial ADU shot clock equivalent to California's exists).",
                "applies_to": "Chicago ADU/coach house/conversion-unit projects",
                "source": "https://www.thedailyline.com/chicago-city-additional-accessory-dwelling-unit-legalization-ordinance-effective-april-application-process-housing-department"
            },
            {
                "title": "No statewide permit shot clock \u2014 review timing is per-AHJ",
                "note": "Illinois currently has no statewide permit-review shot clock for residential construction; legislative proposals (e.g., SB 3037 amendments) have been introduced but not enacted. Review timing is set municipally \u2014 Wilmette publishes target turnarounds, while many AHJs publish none. Build review float into the schedule and file early; if review drags, the remedy is escalation to the building official or the village manager, not a statutory deemed-approval.",
                "applies_to": "Schedule planning for any Illinois residential permit",
                "source": "https://www.wilmette.gov/193/Target-Timeframes-to-Permit-Review"
            },
            {
                "title": "Distributed Generation Interconnection \u2014 parallel filing for solar/battery",
                "note": "Solar PV and battery storage projects require a SEPARATE Distributed Generation interconnection filing with the serving electric utility (ComEd, Ameren, or the local municipal/cooperative utility) under the Illinois Distributed Generation Interconnection Standard (83 Ill. Adm. Code 466), in addition to the local building/electrical permit. Level 1/2/3/4 review tiers are based on system size \u2014 most residential rooftop PV \u226425 kW qualifies for Level 1 fast-track. File the interconnection application in parallel with the building permit; PTO (permission to operate) cannot be issued until both are approved.",
                "applies_to": "Residential solar PV, battery storage, and standby-generator interconnection",
                "source": "https://illinoiselectricalauthority.com/illinois-utility-interconnection-standards"
            },
            {
                "title": "Municipal utility interconnection \u2014 separate rules outside ComEd/Ameren",
                "note": "If the parcel is served by a municipal electric utility (e.g., Rochelle, Breese, Naperville, Batavia) or a rural electric cooperative, the ICC's Part 466 interconnection rules do NOT directly apply \u2014 the municipal utility sets its own interconnection agreement, fees, and metering policy. The customer must procure all building, operating, and environmental permits separately. Confirm the serving utility before quoting; muni-utility interconnection timelines and net-metering credits frequently differ materially from ComEd/Ameren.",
                "applies_to": "Solar/battery/generator projects in muni-utility or co-op service territory",
                "source": "https://www.rmu.net/~documents/rochelle-municipal-utilities/electric-division/interconnection-agreement-solar/?layout=file"
            },
            {
                "title": "Chicago permit pathways \u2014 Easy Permit vs Standard Plan Review",
                "note": "The Chicago Department of Buildings runs tiered permit pathways: the Easy Permit Process (EPP) is available over-the-counter for limited-scope work like reroofing, porch repairs, and minor electrical/plumbing/HVAC; Standard Plan Review is required for additions, structural work, and new construction. Permits are filed via the city's online portal or in Room 900, City Hall, 121 N. LaSalle. Choosing the wrong pathway forces a refile \u2014 match scope to EPP eligibility before submitting.",
                "applies_to": "Chicago residential permit filings",
                "source": "https://www.chicago.gov/content/dam/city/depts/bldgs/general/Homeowner/GuidetoPermits110119.pdf"
            },
            {
                "title": "Chicago General Contractor License required for any GC-permitted work",
                "note": "In Chicago, the entity pulling a building, sign, or wrecking permit must hold a current Chicago General Contractor License from the Department of Buildings \u2014 a state-level registration is not a substitute. The license is tiered by project value (Class A through E). Homeowners pulling their own permit on an owner-occupied 1\u20134 unit building are exempt from the GC license but assume full responsibility for code compliance.",
                "applies_to": "All Chicago projects requiring a building/sign/wrecking permit",
                "source": "https://www.chicago.gov/city/en/depts/bldgs/supp_info/TLdetails/GC.html"
            },
            {
                "title": "Cook County roofing registration \u2014 additional layer above state license",
                "note": "Cook County requires roofing contractors to hold a separate Cook County contractor registration in addition to the state IDFPR Roofing Contractor license. Suburban Cook County AHJs will reject permit applications from state-licensed roofers who lack the county registration. Verify both layers before quoting Cook County roof work \u2014 the state license alone is insufficient for permit issuance.",
                "applies_to": "Roofing projects in Cook County (Chicago and suburbs)",
                "source": "https://www.advancedroofing.biz/blog/blog/illinois-roofing-permits-2025-when-do-you-need-one-for-repairs/"
            }
        ]
    },
    "NC": {
        "name": "North Carolina expert pack",
        "expert_notes": [
            {
                "title": "NC residential plan review \u2014 15 business day target",
                "note": "Under NCGS 160D-1110(b), local governments must conduct residential plan review within 15 business days of submittal of a complete application for one- and two-family dwellings. If the AHJ exceeds this window without identifying deficiencies, you have grounds to escalate to the building official or the NC Office of the State Fire Marshal (OSFM). Track submittal date in writing and request a written status if day 15 passes with no review comments.",
                "applies_to": "Residential 1-2 family dwelling permit applications",
                "source": "https://www.ncosfm.gov/formal-interpretations/231010-nchba-ncgs-160d-1110b-15-business-day-residential-plan-review/open"
            },
            {
                "title": "Licensed design professional certification \u2014 2 business day permit issuance",
                "note": "Session Law 2023-142 (and proposed H876, 2025-26 session) lets an applicant submit a licensed architect/engineer certification that drawings comply with the NC State Building Code. Upon accepting that certification, the local government must issue applicable permits within two business days and refund or waive plan-review fees. Use this path to bypass long municipal queues when an NC-licensed PE/RA stamps and certifies the set.",
                "applies_to": "Projects with stamped drawings by NC-licensed architect or PE",
                "source": "https://lrs.sog.unc.edu/bill-summaries-lookup/H/876/2025-2026%20Session/H876"
            },
            {
                "title": "5-business-day inspection scheduling right (SL 2023-142)",
                "note": "Session Law 2023-142 (SB 677) requires local governments to provide eligible building permit applicants the option to request and schedule an inspection within five business days of the request. If the AHJ cannot meet that window, the applicant may pursue the third-party inspector remedy. Document each inspection request with date/time stamps to preserve the right.",
                "applies_to": "All eligible NC building permit inspections",
                "source": "https://www.ncbels.org/wp-content/uploads/2024/07/SL2023-142.pdf"
            },
            {
                "title": "Permit Choice statute \u2014 NCGS 160D-108",
                "note": "Under NCGS 160D-108, when development rules change between submittal and decision, the applicant may CHOOSE which version of the rules applies to the project \u2014 old or new. This is permit choice, distinct from vesting. Cite the statute on the application cover sheet when a code-cycle boundary is in play to lock in the more favorable edition.",
                "applies_to": "Projects spanning code or ordinance amendments",
                "source": "https://www.ncleg.gov/EnactedLegislation/Statutes/PDF/BySection/Chapter_160D/GS_160D-108.pdf"
            },
            {
                "title": "Statutory vesting durations \u2014 12 months / 2 years / 5-7 years",
                "note": "Under 160D guidance, most development approvals (site plans, plats, special use permits) are valid for 12 months unless a longer period is specified. Site-specific vesting plans run a minimum of 2 years and up to 5 years; multi-phase projects vest for 7 years from approval. Build the schedule against these clocks \u2014 letting an approval lapse forces a fresh submittal under whatever code is then in effect.",
                "applies_to": "Multi-phase, plat, and site-plan-driven projects",
                "source": "https://www.sog.unc.edu/sites/www.sog.unc.edu/files/4_PermitChoice_VestedRights_160DGuidanceDoc_9-9-20.pdf"
            },
            {
                "title": "2024 NC State Building Code \u2014 effective July 1, 2025",
                "note": "The Building Code Council adopted the 2024 NC State Building Code collection (Building, Residential Chapters 11-24, Mechanical, Plumbing, Electrical, Energy, Fuel Gas) effective July 1, 2025. The 2018 NC Residential Code remains the base code for one- and two-family dwellings (Chapters 1-10), with the 2024 amendments layered on top. Confirm with the AHJ which edition applies at submittal \u2014 permit choice (160D-108) lets the applicant pick old vs new across the boundary.",
                "applies_to": "Permit applications submitted after 2025-07-01",
                "source": "https://nclicensing.org/wp-content/uploads/2025/11/UPDATE-ON-NEW-NORTH-CAROLINA-STATE-BUILDING-CODE-EFFECTIVE-DATES-2025-11-11.pdf"
            },
            {
                "title": "NC General Contractor \u2014 $40,000 license threshold",
                "note": "The NC Licensing Board for General Contractors (NCLBGC) requires a general contractor license for any single project where the total cost (labor + materials) is $40,000 or more. Working unlicensed above that threshold voids lien rights and exposes the contractor to misdemeanor prosecution. Verify license number and classification (Building, Residential, Highway, Public Utilities, Specialty) in the NCLBGC online lookup before signing the contract.",
                "applies_to": "All NC construction projects \u2265 $40,000",
                "source": "https://nclbgc.org/"
            },
            {
                "title": "Separate trade boards for electrical, plumbing/HVAC",
                "note": "NC splits trade licensing across three independent boards: NC State Board of Examiners of Electrical Contractors (NCBEEC) for electrical work, and NC State Board of Examiners of Plumbing, Heating, and Fire Sprinkler Contractors (NCBEPHFSC) for plumbing, heating, A/C, and fire sprinklers. Each trade requires its own classification (e.g., Limited/Intermediate/Unlimited electrical; H-1, H-2, H-3 heating). Verify each sub on its respective board's portal \u2014 a valid GC license does NOT cover the trade work.",
                "applies_to": "Multi-trade residential and commercial projects",
                "source": "https://www.ncbeec.org/licensing/"
            },
            {
                "title": "Homeowner exemption for own-residence trade work",
                "note": "NC homeowners may legally perform plumbing, electrical, HVAC, or other specialty trade work on their OWN residence without a contractor's license, but the work still requires a permit and must pass inspection under the NC State Building Code. The exemption does NOT extend to rental property, work for hire, or homes intended for sale within 12 months. Inspectors apply the same code standards regardless of who did the work.",
                "applies_to": "Owner-occupied single-family residential trade work",
                "source": "https://www.andersonlegalnc.com/heres-when-youll-need-a-specialty-trade-license-in-north-carolina/"
            },
            {
                "title": "Coastal high-hazard pile foundations \u2014 NCRC Chapter 46",
                "note": "Under 2018 NCRC Chapter 46, all one- and two-family dwellings in coastal high-hazard areas (V-zones) or ocean hazard areas (CAMA AECs) must be constructed on pile foundations of wood, steel, or reinforced concrete \u2014 slab-on-grade and conventional stem-wall foundations are NOT permitted. Coastal counties (Brunswick, New Hanover, Carteret, Dare, etc.) layer CAMA major/minor permits on top of the building permit. Confirm V-zone vs A-zone via the local FIRM before sizing the foundation.",
                "applies_to": "Coastal NC oceanfront and V-zone construction",
                "source": "https://codes.iccsafe.org/content/NCRC2018/chapter-46-coastal-and-flood-plain-construction-standards"
            },
            {
                "title": "Wind zone anchoring \u2014 130-150+ mph coastal design wind speed",
                "note": "Coastal NC counties carry design wind speeds up to 150+ mph (Vult), with the Outer Banks and oceanfront parcels at the high end. Sheds, accessory structures, and main dwelling components must be anchored, strapped, and uplift-rated for the parcel's mapped wind zone per NCRC Table R301.2(1). Inland piedmont parcels typically design to 115 mph. Pull the local wind-speed map before specifying trusses, sheathing nailing schedules, or anchor bolts.",
                "applies_to": "All NC residential construction \u2014 anchoring varies by county",
                "source": "https://www.coastalcypressbuilding.com/blog/wind-ratings-codes-compliance-what-every-coastal-home-needs"
            },
            {
                "title": "Municipal vs county jurisdiction split \u2014 zoning vs building",
                "note": "NC permitting duties are commonly split: the municipality handles zoning permits (setbacks, use, height) while the county handles building permits and inspections \u2014 particularly in smaller towns and ETJs. In some jurisdictions a project needs BOTH a city zoning sign-off AND a county building permit. Confirm with both offices at the parcel's address before assuming a single application path; one missing sign-off will block CO at final.",
                "applies_to": "Projects in small NC municipalities and extraterritorial jurisdictions (ETJs)",
                "source": "https://canons.sog.unc.edu/blog/2018/09/04/administering-development-regulations-and-accounting-for-permitting-fees-2/"
            },
            {
                "title": "Residential Property Disclosure Act \u2014 Chapter 47E",
                "note": "Under NCGS Chapter 47E, sellers of residential property of 1-4 units must deliver a completed and signed NCREC Residential Property and Owners' Association Disclosure Statement (REC 4.22) to the buyer no later than the time the buyer makes an offer. Unpermitted additions, prior flood damage, and known code violations must be disclosed or buyers gain a 3-day rescission right. Pull permit history from the AHJ before listing to avoid post-closing claims.",
                "applies_to": "Sale of NC residential property 1-4 units",
                "source": "https://www.ncleg.net/EnactedLegislation/Statutes/PDF/ByChapter/Chapter_47e.pdf"
            },
            {
                "title": "Mineral, Oil & Gas (MOG) Disclosure \u2014 new construction first sale",
                "note": "NC Realtors guidance: the MOG Mandatory Disclosure Statement must be provided for the first sale of a dwelling never inhabited (i.e., new construction), and for a lease with an option to purchase. This is separate from the standard 47E Residential Property Disclosure and is frequently missed on spec-built homes. Builders should attach the MOG form to the closing package on every first-sale unit.",
                "applies_to": "First sale of newly constructed NC dwellings",
                "source": "https://www.ncrealtors.org/question/when-does-a-seller-have-to-provide-the-mineral-and-oil-and-gas-rights-mandatory-disclosure-statement/"
            },
            {
                "title": "No statewide residential ADU shot clock \u2014 only the 15-day general clock",
                "note": "Unlike California's 60-day ADU ministerial clock, NC has NO ADU-specific state shot clock and NO state-level ministerial ADU mandate \u2014 ADUs are governed by local zoning, which varies sharply by jurisdiction (some NC cities prohibit detached ADUs outright). The only timing protection is the general 15-business-day residential plan review under 160D-1110(b). Check the local UDO for ADU allowance, owner-occupancy, and parking requirements before designing.",
                "applies_to": "ADU and accessory structure projects across NC",
                "source": "https://www.carolinajournal.com/opinion/north-carolina-deserves-a-shot-clock-for-government-with-consequences/"
            }
        ]
    },
    "NY": {
        "name": "New York expert pack",
        "expert_notes": [
            {
                "title": "2025 NYS Uniform Code & State Energy Code \u2014 Dec 31, 2025 effective",
                "note": "The 2025 Uniform Code and 2025 State Energy Conservation Construction Code take effect statewide on December 31, 2025, following the State Fire Prevention and Building Code Council's July 27, 2025 adoption. The Energy Code has NO transition period \u2014 applications filed near the boundary may switch mid-review. Confirm the exact code edition the local code enforcement officer is reviewing under before stamping drawings, and watch for amendments to 19 NYCRR.",
                "applies_to": "Permit applications crossing the 2025-12-31 code-change boundary statewide",
                "source": "https://www.questar.org/2025/10/20/2025-nys-uniform-code-and-state-energy-code-will-take-effect-on-december-31-2025/"
            },
            {
                "title": "NYC operates under its own Construction Codes \u2014 separate from state Uniform Code",
                "note": "Due to NYC's population, state law lets it adopt its own Building, Residential, Plumbing, Mechanical, Fuel Gas, and Energy Codes administered by DOB, independent of the statewide NYS Uniform Code. Work in the five boroughs follows NYC Construction Codes; every other city, town, and village follows the Uniform Code via a local code enforcement officer. Fire-rating, egress, and energy provisions diverge meaningfully \u2014 never reuse a Long Island detail set inside the city without a code crosswalk.",
                "applies_to": "Any project that crosses the NYC boundary or is referenced from out-of-state precedent",
                "source": "https://dos.ny.gov/division-building-standards-and-codes-frequently-asked-questions"
            },
            {
                "title": "NYC 2025 Energy Code \u2014 DOB enforcement begins March 30, 2026",
                "note": "NYC adopted a new Energy Conservation Code in 2025, but DOB will not begin enforcement until March 30, 2026, when updated compliance software is fully available. Filings submitted before that date may still be reviewed under the prior NYC Energy Code edition; confirm with the plan examiner which edition governs before producing the COMcheck/REScheck or whole-building energy analysis. Mid-flight code switches are the most common cause of energy-section objections in NYC.",
                "applies_to": "NYC permit filings between late 2025 and Q2 2026",
                "source": "https://www.urbangreencouncil.org/highlights-of-the-2025-new-york-city-energy-code/"
            },
            {
                "title": "No statewide general-contractor or home-improvement license in NY (NEGATIVE rule)",
                "note": "Unlike California's CSLB or Florida's CILB, New York has no single statewide general-contractor or home-improvement-contractor license \u2014 licensing is delegated entirely to cities and counties. A contractor licensed in Buffalo cannot automatically work in Yonkers, Nassau, Suffolk, Westchester, or NYC. Verify the local license requirement in every jurisdiction before quoting; there is no statewide reciprocity to fall back on.",
                "applies_to": "Any contractor expanding work area within New York State",
                "source": "https://adaptdigitalsolutions.com/articles/new-york-contractor-license-requirements/"
            },
            {
                "title": "Only three trade categories carry NY state-level licensing",
                "note": "At the state level, New York licenses only a narrow set of trades; most general construction (framing, masonry, roofing, siding, drywall) is licensed locally, not by the state. This means moving from Albany County to Queens may require a brand-new municipal license even with decades of experience. Always check the local jurisdiction's licensing index and DCWP/DOB equivalents before mobilizing crews.",
                "applies_to": "Trade licensing scoping across NY jurisdictions",
                "source": "https://www.procore.com/library/new-york-contractors-license"
            },
            {
                "title": "NYC Home Improvement Contractor (HIC) license \u2014 DCWP requirement",
                "note": "Any construction, repair, remodeling, or home-improvement work to NYC residential land or buildings requires a Home Improvement Contractor (HIC) license issued by the Department of Consumer and Worker Protection (DCWP, formerly DCA). Application requires the HIC exam, fingerprints, insurance, and a Trust Fund contribution. Unlicensed home-improvement work in NYC voids the contract under NYC Admin Code \u00a720-387 and bars mechanic's-lien rights against the homeowner.",
                "applies_to": "Residential remodeling, alterations, and home-improvement work in the five boroughs",
                "source": "https://www.nyc.gov/site/dca/businesses/license-checklist-home-improvement-contractor.page"
            },
            {
                "title": "HVAC licensing is municipal \u2014 NYC issues three distinct HVAC-related licenses",
                "note": "There is no statewide HVAC contractor license in NY. NYC alone issues three separate HVAC-related licenses depending on system type (e.g., refrigeration machine operator, oil burner installer, gas work qualification under master plumber), and Suffolk, Nassau, Westchester, and Rockland counties run their own programs. Confirm the correct license class with the local building department before bidding any A/C, heat-pump, mini-split, or boiler swap \u2014 the wrong class will fail at filing.",
                "applies_to": "HVAC, refrigeration, mini-split, heat-pump, and boiler installations",
                "source": "https://www.servicetitan.com/licensing/hvac/new-york"
            },
            {
                "title": "Floodplain development permits issued by local AHJ under NYS ECL",
                "note": "Private development in a mapped FEMA Special Flood Hazard Area requires a local floodplain-development permit from the municipality \u2014 not a state-level permit. NYS Environmental Conservation Law obligates every NFIP-participating community to enforce minimum floodplain standards: lowest-floor elevation at or above BFE, flood vents on enclosures below BFE, and anchoring. Pull the FIRM panel before drawing foundations \u2014 substantial-improvement triggers (\u226550% market value) force full code-compliant elevation of existing structures.",
                "applies_to": "Any work inside a FEMA SFHA (Zone A, AE, V, VE) statewide",
                "source": "https://dec.ny.gov/environmental-protection/water/dam-safety-coastal-flood-protection/floodplain-management"
            },
            {
                "title": "USACE Nationwide Permit 18 \u2014 small wetland fills \u2264 1/10 acre",
                "note": "For wetland losses of 1/10-acre or less, USACE Nationwide Permit 18 may authorize the discharge without an individual Section 404 permit, but pre-construction notification (PCN) to the New York District engineer is required above the de minimis threshold. The NY District reviews case-by-case; budget ~45 days for PCN review. NYSDEC freshwater-wetland permits are a SEPARATE parallel filing, required for state-regulated wetlands and the 100-ft adjacent area \u2014 federal NWP coverage does not satisfy the state.",
                "applies_to": "Construction near jurisdictional wetlands or waters of the US",
                "source": "https://www.nan.usace.army.mil/Portals/37/docs/regulatory/Nationwide%20Permit/NWP2020/NWP%2018.pdf?ver=2020-03-10-162119-980"
            },
            {
                "title": "Plus One ADU Program \u2014 funding only; local zoning still controls (NEGATIVE rule)",
                "note": "NYS HCR's Plus One ADU Program provides grants to qualifying homeowners to build or legalize ADUs across the state, but the program does NOT preempt local zoning or override municipal ADU prohibitions. Unlike California's ministerial AB 881 path, NY has NO statewide by-right ADU mandate and NO 60-day shot clock \u2014 every ADU still needs the host municipality's zoning approval (variance/special use permit if required) and a local building permit. Confirm zoning eligibility before applying for Plus One funding.",
                "applies_to": "ADU jobs anywhere in NY State",
                "source": "https://hcr.ny.gov/adu"
            },
            {
                "title": "DOB NOW: Build \u2014 mandatory online filing for most NYC permits",
                "note": "Most NYC DOB permits must be filed through DOB NOW: Build, a self-service online portal that has largely sunset paper filings. The Registered Design Professional (PE/RA) e-files; the licensee of record (master plumber, master electrician, GC) must associate to the job before work-permit issuance. Plan 2\u20133 weeks lead time for DOB NOW account setup including notarized authorization \u2014 this is the #1 schedule killer on first NYC jobs.",
                "applies_to": "All NYC DOB filings (Alt-1, Alt-2, NB, plumbing, electrical, mechanical)",
                "source": "https://www.nyc.gov/site/buildings/property-or-business-owner/obtaining-a-permit.page"
            },
            {
                "title": "Top NYC DOB violations driving Stop Work Orders",
                "note": "The most common NYC DOB violations are working without a permit, working beyond approved scope, missing site-safety signage, no posted permit on site, and failing required inspections (TR-1 special inspections, TR-8 energy progress). Penalties start at $1,250 per violation and escalate; a Stop Work Order is lifted only after corrective filings, civil penalty payment, and re-inspection. Post the permit visibly, keep approved drawings on site, and pre-schedule every TR-1 special inspection.",
                "applies_to": "Active NYC construction sites",
                "source": "https://menottienterprise.com/dob-violations-the-top-5-mistakes-contractors-make-and-how-to-avoid-them/"
            },
            {
                "title": "NY AG home-improvement disclosure & written-contract rules",
                "note": "NY Attorney General guidance and GBL Article 36-A require contractors performing home-improvement work to provide a written contract, identify required permits before starting, and disclose which party will pull each permit. Homeowners are explicitly advised to verify with the local building & codes department, so an undisclosed permit gap is highly visible. Failure to disclose required permits can support a GBL \u00a7349 deceptive-practices claim with potential treble damages \u2014 always state permit responsibility in writing.",
                "applies_to": "All residential home-improvement contracts in NY",
                "source": "https://ag.ny.gov/resources/individuals/consumer-issues/contractors-home-maintenance"
            },
            {
                "title": "Uniform Code exemptions \u2014 agricultural buildings and limited accessory work",
                "note": "The NYS Uniform Code exempts a defined set of structures \u2014 most notably qualifying agricultural buildings on farms used for agricultural purposes \u2014 from full Uniform Code review per the DOS Code Outreach Program guidance. Exemption from the Uniform Code does NOT exempt the work from the State Energy Code, the NEC, NYS DEC environmental rules, or local zoning; each must be verified separately. Don't assume 'ag building' or 'small accessory shed' means 'no permit' \u2014 check the DOS exemption list line by line.",
                "applies_to": "Agricultural, accessory, and small-structure projects outside NYC",
                "source": "https://dos.ny.gov/system/files/documents/2024/04/2024_02_exemptions_from_the_uniform_code.pdf"
            }
        ]
    },
    "TX": {
        "name": "Texas expert pack",
        "expert_notes": [
            {
                "title": "No statewide general-contractor license for residential work",
                "note": "Texas does not require general contractors, home-improvement specialists, or handyman services to hold a state license \u2014 licensing is handled by trade-specific boards (TDLR for electrical/HVAC, TSBPE for plumbing) and by individual cities/counties. Confirm the AHJ's local registration requirements (most large cities require contractor registration even though the state does not), and never assume a sister-state GC license carries over. Trade subs still must hold the specific TDLR/TSBPE license below.",
                "applies_to": "All residential GC and remodel work in Texas",
                "source": "https://www.procore.com/library/texas-contractors-license"
            },
            {
                "title": "TDLR Air Conditioning & Refrigeration Contractor license required for HVAC",
                "note": "Any HVAC contractor performing work in Texas must hold a TDLR Air Conditioning and Refrigeration Contractor license \u2014 application fee is $115 and the license must list the proper environment (Class A unlimited tonnage vs Class B \u226425 tons cooling / \u22641.5M Btu heating) and endorsement (cooling, heating, or commercial refrigeration). Verify the contractor's TDLR license number and class match the scope before pulling a mechanical permit; mismatched class is a common rejection reason.",
                "applies_to": "All HVAC installation, replacement, and alteration work",
                "source": "https://www.tdlr.texas.gov/acr/contractor-apply.htm"
            },
            {
                "title": "TSBPE plumbing license required before bidding or working",
                "note": "The Texas State Board of Plumbing Examiners requires a Journeyman or Master plumber license prior to bidding or completing any plumbing work \u2014 there is no de minimis dollar threshold like California's $500 rule. Licenses renew annually and require six hours of CPE per the Plumbing License Law; apprentices may only work under direct supervision. Confirm the responsible Master Plumber's license is active on the TSBPE lookup before submitting plumbing plans, since an expired license blocks permit issuance.",
                "applies_to": "All plumbing work statewide",
                "source": "https://tsbpe.texas.gov/license-types/"
            },
            {
                "title": "TDLR is the umbrella regulator for most building trades",
                "note": "TDLR licenses and regulates 39 industries including electricians, HVAC contractors, elevator/escalator, boilers, and industrialized housing \u2014 but plumbing sits with TSBPE, not TDLR. When a job involves multiple trades, expect parallel licensing checks at two separate state agencies plus the local AHJ's contractor-registration desk. Pull the TDLR license-search and TSBPE license-search results into the permit packet to pre-empt plan-check holds.",
                "applies_to": "Multi-trade residential and light-commercial projects",
                "source": "https://www.tdlr.texas.gov/"
            },
            {
                "title": "2021 IRC/IBC/IMC/IFGC effective for industrialized housing as of July 1, 2024",
                "note": "The Texas Commission of Licensing and Regulation adopted the 2021 editions of the IRC, IBC, IMC, IFGC, and related codes for industrialized housing and buildings on May 21, 2024, with an effective date of July 1, 2024. Modular and IHB projects built or shipped after that date must conform; site-built projects follow the AHJ's locally-adopted edition (often still 2015 or 2018 IRC in smaller jurisdictions). Always confirm the local edition before drafting \u2014 Texas has no uniform statewide site-built code.",
                "applies_to": "Industrialized housing, modular, and IHB-stamped projects",
                "source": "https://www.tdlr.texas.gov/news/2024/06/17/industrialized-housing-and-buildings-adoption-of-new-code-editions/"
            },
            {
                "title": "SB 783 (eff. Sept 1, 2025) \u2014 energy code update path via SECO",
                "note": "Texas SB 783 took effect September 1, 2025 and directs the State Energy Conservation Office (SECO) to evaluate and adopt updated IECC editions for residential and commercial construction, with amendment authority. The last statutorily-adopted residential energy code was the 2015 IECC, so any project at the 2025-2026 boundary should confirm whether the AHJ has moved to the 2021 or 2024 IECC under SECO's updated rulemaking. Build energy-compliance documentation against the AHJ's currently-enforced edition, not the state's default.",
                "applies_to": "New residential and commercial construction energy compliance",
                "source": "https://legiscan.com/TX/text/SB783/id/3057306/Texas-2025-SB783-Introduced.html"
            },
            {
                "title": "HB 3167 platting shot clock \u2014 30 days for municipal action",
                "note": "Under HB 3167 (Local Govt Code Chapter 212), municipalities must approve, approve with conditions, or disapprove a plat or plan within 30 days of filing \u2014 silence is deemed approval. Disapproval must be in writing with specific reasons tied to ordinance or statute, and the applicant has the right to a 15-day cure-and-resubmit. Use this clock as leverage when a city stalls a subdivision or replat needed for ADU or infill projects.",
                "applies_to": "Subdivision plats, replats, and site plans subject to municipal review",
                "source": "https://www.tml.org/DocumentCenter/View/4166/Platting-Shot-Clock-Process-11-2023-PDF"
            },
            {
                "title": "Development-review shot clock for permit applications",
                "note": "Recent Texas legislation extends shot-clock concepts beyond platting to broader development reviews, capping the time municipalities can take to act on permit-related submittals before approval is presumed or fee refunds are triggered. This narrows the window for cities to demand iterative resubmittals and gives applicants statutory grounds to escalate stalled residential permits. Document the application's complete-submittal date in writing so the clock is provable if escalation is needed.",
                "applies_to": "Municipal residential permit and development-review timelines",
                "source": "https://www.freese.com/blog/new-texas-legislation-sets-shot-clock-for-development-reviews/"
            },
            {
                "title": "TDI Windstorm (WPI-8) certification required in First Tier coastal counties",
                "note": "The Texas Department of Insurance Windstorm Inspection Program applies to designated catastrophe areas \u2014 the 14 First Tier coastal counties (Aransas, Brazoria, Calhoun, Cameron, Chambers, Galveston, Jefferson, Kenedy, Kleberg, Matagorda, Nueces, Refugio, San Patricio, Willacy) plus parts of Harris east of SH 146. New construction, additions, and re-roofs in these areas need a WPI-8 / WPI-8-C certificate of compliance issued by an appointed engineer or TDI inspector to be eligible for TWIA windstorm coverage. This is a SEPARATE filing from the building permit and must be scheduled in phases (foundation, framing, roof) \u2014 missing an inspection phase voids the certificate.",
                "applies_to": "New construction, additions, and re-roofs in First Tier coastal counties",
                "source": "https://www.tdi.texas.gov/wind/generalquestio.html"
            },
            {
                "title": "TWIA insurability hinges on windstorm certification",
                "note": "To bind a Texas Windstorm Insurance Association policy on a coastal property, the structure must be certified as meeting the windstorm-resistant building-code requirements for its tier \u2014 without a current WPI-8 (or post-2020 equivalent), the property is uninsurable through TWIA and most private carriers will follow suit. Schedule the engineer-of-record windstorm inspection BEFORE drywall closes the framing, since a missed framing inspection cannot be reconstructed after the fact and the homeowner loses coverage at closing.",
                "applies_to": "Coastal Texas residential construction needing windstorm insurance",
                "source": "https://www.twia.org/windstorm-certification/"
            },
            {
                "title": "Floodplain permit required separate from building permit",
                "note": "Texas participates in the NFIP through county and municipal floodplain administrators \u2014 any development (including fill, grading, accessory structures, and substantial improvements) within a SFHA Zone A/AE/VE requires a floodplain development permit in addition to the building permit, and substantial-improvement work (>50% market value) triggers full base-flood-elevation compliance for the entire structure. In Harris County and other large counties, the floodplain office is a separate desk from permitting; confirm the determination letter and elevation certificate are in the packet before submittal.",
                "applies_to": "Any work within a FEMA SFHA or local floodplain overlay",
                "source": "https://oce.harriscountytx.gov/Services/Permits/Floodplain-Management"
            },
            {
                "title": "Municipally-owned utilities and electric co-ops opted out of retail choice",
                "note": "Per PUC rule, municipally-owned electric utilities and electric cooperatives have had the right since January 1, 2002 to opt out of competitive retail choice \u2014 most have. For new service or solar interconnection in muni/co-op territory (e.g., Austin Energy, CPS Energy, Pedernales EC, Sam Houston EC), follow the utility's own interconnection and net-metering tariff, not ERCOT/PUC retail rules. This is a parallel filing to the electrical permit and often has its own application, fee, and inspection schedule.",
                "applies_to": "New service, solar PV, and battery interconnection in muni/co-op territory",
                "source": "https://www.puc.texas.gov/consumer-help/faq/muni/Default.aspx"
            },
            {
                "title": "Co-op permit-number prerequisite for new electric service",
                "note": "Many Texas electric cooperatives \u2014 including Sam Houston EC and Heart of Texas EC \u2014 will not energize new construction until the contractor provides the local AHJ's permit number and a completed New Construction Application. Submit the co-op's service application in parallel with the building permit (not after final inspection) because meter-set lead times in rural co-op territory routinely run 4-8 weeks and can stall CO issuance.",
                "applies_to": "New residential service in electric-cooperative territory",
                "source": "https://www.samhouston.net/member-services/permit-requirements/"
            },
            {
                "title": "ERCOT Chapter 25 rules govern PV/battery interconnection in competitive territory",
                "note": "In ERCOT competitive-retail areas (Oncor, CenterPoint, AEP TX, TNMP), distributed-generation interconnection follows PUC Substantive Rules Chapter 25 \u2014 the homeowner's TDU (not the REP) processes the interconnection agreement, and the project requires a signed IA before the meter is set to bidirectional. File the IA application as soon as the system design is final; TDU review can run 4-6 weeks and is independent of the city's electrical permit.",
                "applies_to": "Solar PV and battery interconnection in ERCOT competitive territory",
                "source": "https://www.puc.texas.gov/agency/rulesnlaws/subrules/electric/Default.aspx"
            }
        ]
    },
    "WA": {
        "name": "Washington expert pack",
        "expert_notes": [
            {
                "title": "Washington State Building Code 2021 edition effective March 15, 2024",
                "note": "After multiple delays, the 2021 Washington State Building Code (IBC, IRC, IMC, UPC, IFC, and WSEC-R) became enforceable statewide on March 15, 2024, replacing the 2018 edition. Confirm with the AHJ which edition applies to your application \u2014 vested permits submitted before that date may still be reviewed under 2018, but new submittals must comply with 2021 amendments. Drawings still referencing 2018 sections are a common plan-check rejection.",
                "applies_to": "All residential and commercial permit applications statewide",
                "source": "https://sbcc.wa.gov/news/revised-effective-date-2021-codes-march-15-2024"
            },
            {
                "title": "2024 Code adoption cycle delayed \u2014 May 2027 effective date expected",
                "note": "The State Building Code Council (SBCC) has slipped the 2024 IBC/IRC/IECC adoption cycle. Current staff estimate is final adoption by June 2026 with codes going into effect roughly May 2027 \u2014 about a 6-month slip from the prior schedule. Plan multi-year projects under 2021 code with an eye to the 2027 transition; permits vested before the new effective date generally remain under 2021.",
                "applies_to": "Long-lead residential projects crossing the 2027 code-change boundary",
                "source": "https://www.linkedin.com/posts/p-hanks_recap-from-todays-wa-state-building-code-activity-7376025613402329088-SuRN"
            },
            {
                "title": "Washington State Energy Code Residential (WSEC-R) 2021 \u2014 based on 2021 IECC with state amendments",
                "note": "The 2021 WSEC-R has been in effect since March 15, 2024 and applies to all one- and two-family dwellings and townhouses up to three stories. WSEC-R adopts the 2021 IECC with significant Washington amendments \u2014 notably the credit-based energy-efficiency point system in R406 (additional credits required over the 2018 edition) and high-efficiency HVAC/water-heating mandates. Submit an R406 credit worksheet with every new SFR/ADU permit; missing credits is the single most common WSEC-R rejection.",
                "applies_to": "New SFR, ADU, townhouse, and additions creating conditioned space",
                "source": "https://up.codes/viewer/washington/wa-energy-code-residential-provisions-2021"
            },
            {
                "title": "L&I contractor registration required \u2014 no statewide \"general contractor license\"",
                "note": "Washington does NOT have a CSLB-style state contractor license. Instead, the Department of Labor & Industries (L&I) requires every construction contractor to register with L&I, post a continuous bond ($12,000 general / $6,000 specialty), and carry liability insurance. Verify the contractor on the L&I \"Verify a Contractor\" lookup before signing \u2014 unregistered contracting forfeits lien rights and is grounds for civil penalties under RCW 18.27.",
                "applies_to": "All paid construction work statewide",
                "source": "https://lni.wa.gov/licensing-permits/contractors/register-as-a-contractor/"
            },
            {
                "title": "Electricians and plumbers licensed separately by L&I \u2014 not by L&I contractor registration alone",
                "note": "L&I licenses electricians and plumbers as a separate program from contractor registration. Electrical work requires a 01 General or 02 Residential electrical contractor license plus certified electrician (e.g., 06A residential specialty, 07 journey-level), and plumbing requires a plumbing contractor license plus a certified plumber. A general contractor registration alone does NOT permit a firm to pull electrical or plumbing permits \u2014 that work must be subbed to a separately licensed electrical/plumbing contractor.",
                "applies_to": "Any project with electrical or plumbing scope",
                "source": "https://www.lni.wa.gov/licensing-permits/"
            },
            {
                "title": "HVAC has no dedicated state license \u2014 relies on contractor registration + 06A/06B for line-voltage work",
                "note": "Washington does not issue a stand-alone HVAC license. HVAC firms register as L&I specialty contractors; any line-voltage electrical work on the install (e.g., disconnects, condenser whips, furnace hookups) requires a 06A (HVAC/Refrigeration) or 06B specialty electrician under the L&I electrical program. Low-voltage thermostat wiring may fall under the 06 specialty as well \u2014 confirm scope before sending an unlicensed tech to do final electrical connections.",
                "applies_to": "HVAC change-outs, new installs, and heat-pump conversions",
                "source": "https://www.lumberfi.com/wiki/washington-hvac-license-requirements"
            },
            {
                "title": "Separate L&I electrical permit + state electrical inspection (parallel filing)",
                "note": "Most jurisdictions in Washington do NOT issue electrical permits \u2014 those are pulled directly from L&I by the licensed electrical contractor (or the homeowner doing their own work on a single-family residence they own and occupy). The L&I state electrical inspector \u2014 not the city building inspector \u2014 performs the rough-in and final electrical inspections. Budget a separate L&I permit fee and a separate inspection schedule on top of the building permit; cities like Seattle and Spokane that issue their own electrical permits are the exception, not the rule.",
                "applies_to": "Electrical scope on residential projects outside Seattle/Spokane and a few other delegated cities",
                "source": "https://www.lni.wa.gov/licensing-permits/electrical/electrical-permits-fees-and-inspections/"
            },
            {
                "title": "Critical Areas Ordinance (CAO) \u2014 every city/county must regulate wetlands, streams, and geo-hazards",
                "note": "The state Growth Management Act requires every Washington city and county to adopt a Critical Areas Ordinance protecting wetlands, fish and wildlife habitat, frequently flooded areas, geologically hazardous areas (landslide, seismic, erosion), and critical aquifer recharge areas. If any portion of the parcel \u2014 including required buffers (often 50\u2013300 ft from a wetland or stream) \u2014 falls in a critical area, expect a critical-area study, mitigation plan, and possibly a reasonable-use exception before the building permit can issue. Always pull the parcel-level CAO map before quoting; a missed wetland buffer can add 3\u20136 months and a wetland biologist to the project.",
                "applies_to": "Any parcel touching a wetland, stream, steep slope, or designated geo-hazard",
                "source": "https://kingcounty.gov/en/dept/local-services/certificates-permits-licenses/permits/permits-inspections-codes-buildings-land-use/permit-forms-application-materials/land-use/critical-areas"
            },
            {
                "title": "Shoreline Management Act \u2014 separate Substantial Development / Conditional Use / Variance permit",
                "note": "Construction within 200 feet of an ordinary high-water mark on a shoreline of the state (most marine waters, lakes \u226520 acres, and streams with mean annual flow \u226520 cfs) triggers the Shoreline Management Act. Work over $9,047 (the 2024 statutory threshold, indexed every 5 years) generally requires a Shoreline Substantial Development Permit issued by the local government and reviewed by Department of Ecology \u2014 Ecology's review adds 21 days minimum, and shoreline permits do not become effective until 14 days after Ecology's filing date. File this in PARALLEL with the building permit, not after.",
                "applies_to": "Residential work within 200 ft of a shoreline of the state",
                "source": "https://apps.ecology.wa.gov/publications/documents/1706029.pdf"
            },
            {
                "title": "Statewide DNR Wildfire Hazard Map \u2014 defensible space and WUI building requirements",
                "note": "The Department of Natural Resources maintains a statewide map categorizing wildfire hazard as low, moderate, high, or very high. Parcels in high/very-high zones (much of eastern Washington and parts of the Cascade foothills) trigger IWUIC-derived ignition-resistant construction (Class A roof, ember-resistant vents, 5 ft non-combustible zone) and defensible-space inspections from the local fire marshal. Pull the DNR map for the parcel before specifying siding, eaves, or decking \u2014 substituting non-rated materials in a high-hazard zone is a common rejection.",
                "applies_to": "New construction and major remodels in DNR-mapped high/very-high wildfire hazard zones",
                "source": "https://dnr.wa.gov/wildfire-resources/wildfire-prevention/wildfire-hazard-and-risk-mapping"
            },
            {
                "title": "RCW 43.42.080 coordinated permit timelines \u2014 not a hard shot clock",
                "note": "Unlike California's 60-day ADU clock, Washington has no statewide ministerial shot clock for residential permits. RCW 43.42.080 lets the Office of Regulatory Innovation and Assistance (ORIA) coordinate timelines across participating state agencies, but timelines \"shall not be shorter than those otherwise required\" by other law. Local LDC-060(H) infill provisions target 60 days (no public hearing) or 80 days (with hearing), but these are goals, not guarantees \u2014 BIAW data puts the statewide average residential permit at ~6.5 months. Set client expectations accordingly and track vesting carefully.",
                "applies_to": "All residential permit applications \u2014 managing client timeline expectations",
                "source": "https://app.leg.wa.gov/rcw/default.aspx?cite=43.42.080"
            },
            {
                "title": "Permit vesting reform \u2014 minimum 2-year vesting for projects \u226450 units (2026 legislation)",
                "note": "Washington's vested-rights doctrine attaches to a complete building-permit application \u2014 the project is reviewed under the code in effect on the application date, even if codes change later. 2026 legislation moving through the Senate would lock that vesting for a minimum of 2 years on projects of 50 or fewer residential units. Practically: file a complete application before a known code change (e.g., the 2024 cycle effective ~May 2027) to lock in 2021 code, but track the bill's final form before relying on the 2-year floor.",
                "applies_to": "Residential infill and small-multifamily timing decisions around code-change boundaries",
                "source": "https://wacities.org/advocacy/News/advocacy-news/2026/03/06/bill-proposes-reforms-to-project-permit-vesting---permit-shot-clocks"
            },
            {
                "title": "RCW 64.06.020 Form 17 seller disclosure \u2014 improvements without permits must be disclosed",
                "note": "Almost all residential resales in Washington require the seller to deliver a completed Form 17 Seller Disclosure Statement under RCW 64.06.020. The form specifically asks whether additions, conversions, or remodels were done with required permits and final inspections. Unpermitted ADU conversions, finished basements, or garage conversions surface here \u2014 and disclosed-but-unpermitted work is a frequent trigger for retroactive permit applications, which the AHJ will review under CURRENT code, not the code in effect when the work was originally done.",
                "applies_to": "Resale prep and retroactive permit work on previously unpermitted improvements",
                "source": "https://app.leg.wa.gov/rcw/default.aspx?cite=64.06.020"
            },
            {
                "title": "Flood Hazard Overlay District \u2014 separate floodplain development permit on top of building permit",
                "note": "Parcels in a FEMA Special Flood Hazard Area (Zone A, AE, VE) fall under the local Flood Hazard Overlay District and require a separate floodplain development permit in addition to the building permit. Lowest-floor elevation (typically 1\u20132 ft above BFE depending on jurisdiction), flood vents on enclosed areas below BFE, and a post-construction Elevation Certificate sealed by a licensed surveyor are all standard requirements. File the floodplain permit and order the EC up front \u2014 discovering the parcel is in an SFHA at framing inspection is a project-killer.",
                "applies_to": "Any residential work in a FEMA-mapped Special Flood Hazard Area",
                "source": "https://experience.arcgis.com/experience/5978c68aeb2f495db07ddab1c3aa1048/page/Flood-Hazard-Overlay-District"
            }
        ]
    },
    "PA": {
        "name": "Pennsylvania expert pack",
        "expert_notes": [
            {
                "title": "PA UCC adoption of 2021 I-Codes \u2014 July 1, 2026 effective date",
                "note": "Pennsylvania's Uniform Construction Code is transitioning from the 2018 to the 2021 I-Code series (IBC, IRC, IPC, IMC, IFGC). Implementation was originally scheduled for July 13, 2025 but was postponed; effective July 1, 2026 all projects, regardless of contractual or permit-application status, will be reviewed under the 2021 I-Codes. Confirm which edition the AHJ is applying before finalizing drawings \u2014 submitting under the wrong edition is a top plan-check rejection.",
                "applies_to": "UCC permit applications crossing the 2026-07-01 code-change boundary",
                "source": "https://gawthrop.com/major-changes-to-pennsylvania-building-codes-affect-municipal-code-officials-designers-contractors-and-builders-starting-january-1-2026/"
            },
            {
                "title": "2021 IECC Pennsylvania energy code with state amendments",
                "note": "Pennsylvania officially adopted the 2021 International Energy Conservation Code into the UCC effective January 1, 2026, with targeted state amendments. Residential envelope requirements (insulation R-values, fenestration U-factors, air-leakage/blower-door testing) are stricter than the 2018 IECC baseline previously in force. Updated U-factor schedules and air-leakage compliance method must be shown on the plan set or expect a comment at first review.",
                "applies_to": "New residential construction, additions, and conditioned-space alterations on/after 2026-01-01",
                "source": "https://insulationinstitute.org/wp-content/uploads/2025/12/N109-PA-Energy-Code-1225.pdf"
            },
            {
                "title": "HIC registration with PA Attorney General \u2014 Home Improvement Consumer Protection Act",
                "note": "Under the Home Improvement Consumer Protection Act (HICPA, 73 P.S. \u00a7517.1 et seq.), home-improvement contractors performing work over $5,000 must register with the PA Office of Attorney General; the fee is $100 every two years per the recent change to 72 P.S. \u00a71603-U. Contractors must also carry Commercial General Liability insurance and use a written contract meeting HICPA disclosure rules. Verify the HIC# on the AG's public lookup before quoting \u2014 unregistered work is a violation of the Unfair Trade Practices and Consumer Protection Law.",
                "applies_to": "Residential home-improvement work over $5,000 statewide",
                "source": "https://www.attorneygeneral.gov/resources/home-improvement-contractor-registration/"
            },
            {
                "title": "No statewide general-contractor license in Pennsylvania (negative rule)",
                "note": "Unlike most states, Pennsylvania does NOT require a state-issued general-contractor license; HIC registration with the AG is a consumer-protection registration and does not verify trade competence. However, individual cities (Philadelphia, Pittsburgh, Allentown, Scranton, Erie) impose their own contractor licensing with separate exams, 8 hours of continuing education, and city Treasurer fees. Confirm city-specific licensing before bidding \u2014 a valid HIC# alone is not sufficient in any major PA city.",
                "applies_to": "Contractor qualification questions for any PA municipality",
                "source": "https://gaslampinsurance.com/how-to-get-a-contractors-license-in-pennsylvania-a-step-by-step-guide-to-obtaining-your-pa-contractor-license/"
            },
            {
                "title": "UCC inspector certification classes \u2014 who may sign off",
                "note": "Under UCC Act 45 of 1999, only Department of Labor & Industry-certified individuals may perform plan review or inspection. The residential certification categories are B1 (Residential Building Inspector), E1 (Residential Electrical), P1 (Residential Plumbing), and M1 (Residential Mechanical); commercial scopes require the corresponding B2/E2/P2/M2 or higher classes. Verify the township inspector or third-party agency holds the right class for the scope \u2014 an inspection signed by an uncertified or wrong-class inspector can be challenged and force re-inspection.",
                "applies_to": "Verifying inspectors and third-party agencies for UCC-regulated projects",
                "source": "https://www.pa.gov/content/dam/copapwp-pagov/en/dli/documents/individuals/labor-management-relations/bois/documents/ucc/ucc_certification_booklet.pdf"
            },
            {
                "title": "Municipal UCC opt-out \u2014 AHJ split between township, third-party agency, and L&I",
                "note": "Pennsylvania municipalities may opt out of administering the UCC for residential 1- and 2-family construction, in which case the owner must contract directly with a state-certified third-party agency for plan review and inspections; commercial UCC enforcement defaults to L&I if the municipality opts out. Always confirm in writing whether the township enforces UCC in-house, uses a designated third-party agency, or has opted out entirely \u2014 submitting to the wrong AHJ is one of the most common causes of project delay statewide.",
                "applies_to": "Identifying the correct UCC AHJ for any PA project",
                "source": "https://www.pa.gov/agencies/dli/programs-services/labor-management-relations/bureau-of-occupational-and-industrial-safety/uniform-construction-code-home"
            },
            {
                "title": "Act 167 watershed stormwater management plan conformance",
                "note": "After a county/watershed Act 167 Stormwater Management Plan is adopted and approved by DEP, the location, design, and construction of stormwater facilities, obstructions, and flood-control projects within that watershed must conform to the plan, and municipalities must adopt implementing ordinances. New impervious cover above the local trigger typically requires BMPs (rain gardens, infiltration trenches, detention) on the site plan. Pull the applicable Act 167 plan early \u2014 required BMPs vary by watershed and often drive lot layout and grading design.",
                "applies_to": "Projects adding impervious cover within an Act 167 watershed",
                "source": "https://www.pa.gov/agencies/dep/programs-and-services/water/clean-water/stormwater-management/act-167"
            },
            {
                "title": "Act 537 Sewage Facilities Act \u2014 on-lot disposal permits and SEO sign-off",
                "note": "The Pennsylvania Sewage Facilities Act (Act 537 of 1966) requires every municipality to have an Official Sewage Facilities Plan and authorizes local agencies to issue permits for on-lot (septic) disposal systems under DEP's uniform standards. A new SFR, ADU, or expansion on a non-sewered parcel needs a Sewage Enforcement Officer (SEO) site evaluation, percolation/probe test, and Act 537 permit before a building permit can be issued. SEO evaluations are seasonal in many counties (frozen-ground months are excluded), so schedule the soils work early in the project.",
                "applies_to": "New construction or expansion on parcels using on-lot sewage disposal",
                "source": "https://www.pa.gov/agencies/dep/programs-and-services/water/clean-water/wastewater-management/act-537-sewage-facilities-program"
            },
            {
                "title": "DEP NPDES stormwater 60-day renewal shot clock (2025 budget / Act 45)",
                "note": "The 2025 Pennsylvania budget imposed new review deadlines on DEP including a 60-day shot clock on renewals of NPDES stormwater construction general permits \u2014 if DEP misses the deadline the renewal is deemed approved. Earth disturbance \u22651 acre still triggers an NPDES permit through the County Conservation District, but contractors holding active CGPs now have enforceable renewal timing. Track submission and the 60-day milestone explicitly; the shot clock applies to renewals only, not to original NPDES applications.",
                "applies_to": "NPDES stormwater construction general permit renewals",
                "source": "https://www.foxrothschild.com/publications/pa-state-budget-imposes-new-permitting-review-deadlines-on-padep"
            },
            {
                "title": "UCC 30-business-day permit review window",
                "note": "Under the UCC the building-code official must approve, deny, or request revisions on a residential permit application within 30 business days of a complete submittal. Incomplete submissions reset the clock, so a thorough application up front beats a fast partial one \u2014 cash-flow plans and subcontractor schedules should be built around the 30-day window plus AHJ-specific resubmission cycles. If the AHJ exceeds 30 business days without action on a complete application, escalate in writing; the UCC review timeline is statutory, not a courtesy.",
                "applies_to": "Residential permit cash-flow planning and AHJ accountability",
                "source": "https://davisbucco.com/how-30-day-permit-processing-affects-construction-cash-flow/"
            },
            {
                "title": "Solar PV \u2014 parallel filings: AHJ building/electrical permit + utility interconnection",
                "note": "Residential solar in Pennsylvania requires BOTH a building permit and an electrical permit from the local AHJ AND a separate utility interconnection application with the serving EDC (PECO, PPL Electric Utilities, FirstEnergy/Met-Ed/Penelec/West Penn Power, Duquesne Light) under PA PUC interconnection rules. The utility process is a two-part flow \u2014 customer application submission, then company review and conditional approval \u2014 and the system cannot be energized until interconnection is approved even if the AHJ has signed off final inspection. Submit both filings in parallel; never promise a PTO date based on the building permit alone.",
                "applies_to": "All grid-tied residential PV and PV+battery projects",
                "source": "https://www.solarpermitsolutions.com/blog/pennsylvania-solar-permits"
            },
            {
                "title": "Plumbing permit triggers and city plumber-licensing overlay",
                "note": "Most plumbing alterations beyond simple fixture-for-fixture replacement (water-heater swap with venting/gas changes, repipes, drain reroutes, new bathrooms, water-service replacement) require a plumbing permit under the UCC and inspection by a P1-certified inspector. Some municipalities (notably Philadelphia and parts of Allegheny County under the older PA Plumbing Code Act of 1956) maintain stricter local plumber-licensing regimes layered on top of the UCC. Confirm both the permit trigger and any city plumber-licensing requirement before scheduling \u2014 homeowner-performed plumbing is allowed in many AHJs but not in those city-licensed jurisdictions.",
                "applies_to": "Residential plumbing alterations and water-heater replacements",
                "source": "https://www.aeroenergy.com/when-is-a-plumbing-permit-required-in-pennsylvania/"
            },
            {
                "title": "Municipalities Planning Code (Act 247) \u2014 zoning, subdivision, and ZHB appeal path",
                "note": "The Pennsylvania Municipalities Planning Code (Act 247 of 1968, the MPC) delegates zoning, subdivision and land-development authority to municipalities; \"subdivision\" is defined as the division or redivision of a lot into two or more lots by any means. Lot splits, ADUs, accessory structures, and use changes are governed locally \u2014 there is no state-level ministerial path equivalent to a SB-9 lot split. If a permit is denied on zoning grounds the appeal path is the municipal Zoning Hearing Board (ZHB) for variance, special-exception, or validity-challenge relief, with further appeal to the Court of Common Pleas under the MPC.",
                "applies_to": "Zoning, subdivision, ADU, and variance/appeal questions in PA",
                "source": "https://dced.pa.gov/download/pennsylvania-municipalities-planning-code-act-247-of-1968/?wpdmdl=56205&refresh=60fa1c66b55301627004006"
            }
        ]
    },
    "OH": {
        "name": "Ohio expert pack",
        "expert_notes": [
            {
                "title": "OCILB state license required only for commercial trades \u2014 not residential",
                "note": "The Ohio Construction Industry Licensing Board (OCILB) issues state licenses for five commercial trades only: Electrical, HVAC, Hydronics, Plumbing, and Refrigeration. Residential trade work is NOT licensed at the state level \u2014 it is regulated by the municipality or county. Verify the contractor's OCILB license on the eLicense lookup before any commercial trade work and confirm local registration for residential jobs.",
                "applies_to": "All commercial electrical/HVAC/plumbing/hydronics/refrigeration work statewide",
                "source": "https://com.ohio.gov/divisions-and-programs/industrial-compliance/boards/ohio-construction-industry-licensing-board/contractors-and-contracting-companies"
            },
            {
                "title": "No statewide residential general-contractor license",
                "note": "Ohio does NOT issue a state-level residential general contractor license. The OCILB regulates only commercial specialty trades; residential GC, home-improvement, and roofing licensing is handled city-by-city (e.g., Columbus issues a Home Improvement Contractor license through its own packet). Always check the AHJ's local registration requirement before quoting residential remodels or additions \u2014 assuming a state license suffices is a top rejection cause.",
                "applies_to": "Residential general-contracting and home-improvement work",
                "source": "https://www.columbus.gov/Business-Development/Business-Licenses-Resources/Contractor-Licenses"
            },
            {
                "title": "OCILB License Lookup \u2014 verify before contracting",
                "note": "All five state-licensed trades (Electrical, HVAC, Hydronics, Plumbing, Refrigeration) must be verified on the OCILB eLicense Center prior to executing a contract. The lookup also supports search by employer, which catches journeymen working under a lapsed company license. Working without an active OCILB license on commercial trade scopes voids permit eligibility.",
                "applies_to": "Pre-contract due diligence for commercial trade work",
                "source": "https://elicense4.com.ohio.gov/lookup/licenselookup.aspx"
            },
            {
                "title": "30-day permit review shot clock under Ohio law",
                "note": "Ohio law requires the building department to review a permit application within 30 days of receipt (per Cincinnati Buildings guidance reflecting state requirements). If the AHJ exceeds 30 days without issuing a determination or formal request for additional information, you have grounds to escalate. Document the application receipt date and any RFI cycles in writing.",
                "applies_to": "All building permit applications statewide",
                "source": "https://www.cincinnati-oh.gov/buildings/building-permit-forms-applications/permit-guide/permit-review-process/"
            },
            {
                "title": "Permit vesting \u2014 12-month start + 12-month extension under ORC 3791.04",
                "note": "Under Ohio Revised Code \u00a73791.04, a building permit is valid for 12 months and one extension of 12 additional months shall be granted if the owner requests it at least 10 days before expiration. If work has not commenced or the owner misses the 10-day window, the permit lapses and a new application (under whichever code edition is then in effect) is required. Calendar the expiration date the day the permit issues.",
                "applies_to": "All issued building permits \u2014 commencement and extension planning",
                "source": "https://codes.ohio.gov/ohio-revised-code/section-3791.04"
            },
            {
                "title": "180-day inspection clock after permit issuance",
                "note": "When a permit is pulled, the contractor or homeowner has 180 days to call in the first inspection or demonstrate active work; failure to do so causes the permit to be deemed abandoned in many Ohio AHJs. Schedule a footing or rough-in inspection within the first six months even on slow-moving residential jobs to keep the permit alive. This is separate from the 12-month commencement clock under ORC 3791.04.",
                "applies_to": "All issued permits \u2014 keeping permits active",
                "source": "https://www.facebook.com/PPRegionalBuilding/posts/did-you-know-when-you-pull-a-permit-the-clock-immediately-starts-ticking-on-that/599787157335511/"
            },
            {
                "title": "2024 Ohio Building Code (commercial) effective March 1, 2024",
                "note": "The 2024 Ohio Building Code, based on the 2021 International Building Code with Ohio amendments, took effect March 1, 2024 and governs all commercial and 4+ unit residential construction. Permit applications submitted before that date may have vested under the prior 2017 OBC at the AHJ's discretion \u2014 confirm which edition the plan reviewer is applying before finalizing drawings. The 2021 IECC commercial energy code took effect concurrently.",
                "applies_to": "Commercial and 4+ unit residential permit applications",
                "source": "https://dam.assets.ohio.gov/image/upload/com.ohio.gov/documents/2024%20Ohio%20Building%20Code%20Rules%20Effective%20March%201,%202024.pdf"
            },
            {
                "title": "2019 Residential Code of Ohio (RCO) with April 2024 amendments",
                "note": "One-, two-, and three-family dwellings are governed by the 2019 Residential Code of Ohio (based on the 2018 IRC), with Chapter 34 and 44 amendments adopted by the BBS on March 22, 2024 and effective April 15, 2024. The residential energy chapter still references the 2018 IECC with Ohio amendments (effective 07/01/2019), which is more lenient than the commercial 2021 IECC \u2014 do not over-spec residential envelopes to commercial standards.",
                "applies_to": "1-3 family residential dwellings statewide",
                "source": "https://com.ohio.gov/divisions-and-programs/industrial-compliance/boards/board-of-building-standards/building-codes-and-interpretations/2019-residential-code-of-ohio-amendments"
            },
            {
                "title": "Ohio Mechanical Code amendments effective October 15, 2025",
                "note": "The Board of Building Standards adopted Ohio Mechanical Code (OMC) amendments effective October 15, 2025, which substitute references to Chapter 13 of the building code for the International Energy Conservation Code in mechanical scopes. Mechanical permit drawings submitted after that date must reflect the new cross-references. Submitting a job to the prior OMC after Oct 15, 2025 is a common rejection cause for HVAC alterations.",
                "applies_to": "Mechanical/HVAC permit applications after 2025-10-15",
                "source": "https://dam.assets.ohio.gov/image/upload/com.ohio.gov/DICO/BBS/Rule%20Docs/dico_bbs_omc_amendments_october_2025.pdf"
            },
            {
                "title": "Certified AHJ jurisdiction split \u2014 state vs municipal vs county vs township",
                "note": "Per OAC 4101:7-2-01, a municipal, township, or county building department only has plan-review and inspection authority within its certified jurisdictional area; outside that, the Ohio BBS itself acts as the AHJ. Before submitting, confirm which body holds certified jurisdiction for the parcel \u2014 many unincorporated townships defer to the county, but some defer directly to the state. Filing with the wrong body wastes plan-review fees and the 30-day clock does not start.",
                "applies_to": "Determining the correct AHJ for any parcel",
                "source": "https://up.codes/s/building-department-jurisdictional-limitations"
            },
            {
                "title": "Lake Erie Shore Structure Permit (ODNR) \u2014 parallel filing",
                "note": "ODNR requires a Shore Structure Permit before constructing any beach, groin, revetment, seawall, bulkhead, breakwater, pier, or jetty along Ohio's Lake Erie shore (including Maumee Bay tributaries). This is a SEPARATE filing from the local building permit and is processed by ODNR's Office of Coastal Management \u2014 start it early because it can outlast the building plan-review timeline. Permanent structures within the Coastal Erosion Area also need a CEA permit.",
                "applies_to": "Any shoreline construction along Lake Erie",
                "source": "https://ohiodnr.gov/wps/portal/gov/odnr/buy-and-apply/regulatory-permits/lake-erie-land-and-water-permits/shore-structure-permit"
            },
            {
                "title": "Coastal Erosion Area (CEA) permit for permanent structures",
                "note": "A CEA permit from ODNR is required to erect, construct, or redevelop a permanent structure (or any portion thereof) located within Ohio's Lake Erie Coastal Erosion Area. This applies to habitable additions and ADUs on bluff-top lots in counties like Lake, Lorain, Ottawa, and Cuyahoga. Verify CEA mapping with ODNR Coastal Management before promising a footprint to the owner \u2014 setbacks can dramatically reduce buildable area.",
                "applies_to": "Permanent structures in Lake Erie Coastal Erosion Areas",
                "source": "https://dam.assets.ohio.gov/image/upload/ohiodnr.gov/documents/coastal/permits-leases/packet-TemporaryShoreStructurePermit.pdf"
            },
            {
                "title": "County floodplain regulations \u2014 separate floodplain development permit",
                "note": "Ohio counties enforce floodplain regulations (e.g., Erie County's 2022 Revised Flood Plain Regulations) that make it unlawful to begin construction, filling, grading, or alteration in a regulated floodplain without a county floodplain development permit. This is a parallel filing to the building permit and typically requires elevation certificates and BFE-based design. Skipping it voids NFIP eligibility for the structure.",
                "applies_to": "Any work in a SFHA floodplain in Ohio counties",
                "source": "https://www.eriecounty.oh.gov/Downloads/2022%20Revised%20Erie%20County%20Flood%20Plain%20Regulations.pdf?v=-102"
            },
            {
                "title": "Solar PV interconnection \u2014 parallel utility application",
                "note": "Solar PV systems require BOTH a local building/electrical permit and a separate interconnection application filed with the serving electric utility (AES Ohio, AEP Ohio, FirstEnergy, or the local municipal electric). PUCO publishes the interconnection process for investor-owned utilities; municipal utilities (e.g., Bowling Green) maintain their own customer-owned generation standards. Submit the utility interconnection application simultaneously with \u2014 or before \u2014 the local AHJ permit so PTO is not the gating step at COD.",
                "applies_to": "Residential and commercial solar PV + battery installations",
                "source": "https://puco.ohio.gov/utilities/electricity/resources/interconnection-applicant-info"
            },
            {
                "title": "Municipal-utility interconnection standards differ from investor-owned",
                "note": "If the parcel is served by a municipal electric utility (Bowling Green, Cleveland Public Power, AMP member cities), the interconnection rules are set by that municipality \u2014 NOT by PUCO. Bowling Green's customer-owned renewable generation interconnection standards, for example, govern eligibility, equipment, and operating requirements independent of state procedures. Confirm the serving utility before quoting interconnection timelines.",
                "applies_to": "Solar/storage projects in municipal-electric service territories",
                "source": "https://www.bgohio.gov/DocumentCenter/View/674/Interconnection-Standards-Customer-Owned-Renewable-Generation-PDF"
            },
            {
                "title": "ADUs \u2014 no statewide preemption, county-by-county zoning",
                "note": "Ohio has NO state-level ADU preemption law (unlike California's AB 881). ADU permissibility, setbacks, owner-occupancy, and short-term-rental rules are set entirely by the local zoning code, which varies dramatically across the 88 counties. Always pull the parcel's zoning ordinance and confirm ADU classification (accessory structure vs. duplex conversion) before designing \u2014 assuming a statewide ministerial path will get the project denied.",
                "applies_to": "Detached and attached ADU projects statewide",
                "source": "https://www.zookcabins.com/regulations/ohio-adus"
            }
        ]
    },
    "MI": {
        "name": "Michigan expert pack",
        "expert_notes": [
            {
                "title": "2021 Michigan Building Code enforcement effective May 1, 2025",
                "note": "The 2021 Michigan Building Code (MBC) became enforceable statewide on May 1, 2025. Any building permit application submitted on or after that date must be reviewed under the 2021 MBC, not the prior 2015 edition. Confirm with the AHJ which code edition governs your drawings before submitting \u2014 applications filed under stale code references are a common rejection reason.",
                "applies_to": "Commercial and multi-family building permits submitted on/after 2025-05-01",
                "source": "https://www.a2gov.org/news/posts/enforcement-of-2021-michigan-building-code-begins-may-1-2025/"
            },
            {
                "title": "Michigan Energy Code update enforceable April 22, 2025",
                "note": "Michigan's updated energy code took effect April 22, 2025 and applies to commercial construction. New compliance documentation, envelope, lighting, and mechanical efficiency requirements apply \u2014 older COMcheck or energy reports prepared under the prior code will be rejected if the application is filed on or after the effective date.",
                "applies_to": "Commercial construction permit applications filed on/after 2025-04-22",
                "source": "https://www.youtube.com/watch?v=RvY_yOqdAUc"
            },
            {
                "title": "Stille-DeRossett-Hale Act \u2014 single statewide construction code",
                "note": "Under 1972 PA 230 (Stille-DeRossett-Hale Single State Construction Code Act, MCL 125.1504), the LARA Bureau of Construction Codes director promulgates a single state construction code \u2014 the Michigan Building Code, Residential Code, Energy Code, Electrical Code, Plumbing Code, Mechanical Code, and Rehabilitation Code. Local jurisdictions cannot adopt code amendments more or less stringent than the state code unless specifically authorized; cite the state code edition, not a local one, on drawings.",
                "applies_to": "All construction permitting statewide",
                "source": "https://www.legislature.mi.gov/(S(2p5qabjfsuhksbb5qct2tepl))/documents/mcl/pdf/mcl-125-1504.pdf"
            },
            {
                "title": "Residential Builder license required for projects > $600",
                "note": "General contractors performing residential work in Michigan with a contract value (labor + materials) exceeding $600 must hold either a Residential Builder license or a Maintenance & Alterations Contractor license issued by LARA. Working unlicensed above this threshold blocks lien rights and exposes the contractor to civil and criminal penalties \u2014 verify license status at LARA before signing a contract or pulling permits.",
                "applies_to": "All paid residential construction in Michigan over $600",
                "source": "https://www.procore.com/library/michigan-contractors-license"
            },
            {
                "title": "Maintenance & Alteration Contractor \u2014 trade-specific endorsements",
                "note": "An M&A Contractor license is issued only for the specific trades or crafts listed on the license (e.g., carpentry, concrete, masonry, roofing, siding, basements, gutters, screens & storms, insulation, painting/decorating, swimming pools, garages). The licensee may not legally perform a trade not endorsed on their license \u2014 request a copy of the license and confirm the relevant trade is listed before issuing a subcontract.",
                "applies_to": "Subcontractor selection for residential alterations",
                "source": "https://www.michigan.gov/lara/bureau-list/bcc/sections/licensing-section/residential-builders/lic-info/maintenance-alteration-contractor-license-information"
            },
            {
                "title": "Plumbing permits \u2014 licensed plumbing contractor or homeowner only",
                "note": "Per the LARA Bureau of Construction Codes, plumbing permits in Michigan can only be issued to a licensed plumbing contractor OR to a homeowner installing their own plumbing/building sewer/private sewage system in their primary residence. A residential builder cannot pull the plumbing permit on behalf of an unlicensed plumbing sub \u2014 the permit must be in the licensed plumber's name.",
                "applies_to": "Any plumbing, building sewer, or private sewage work",
                "source": "https://www.michigan.gov/lara/bureau-list/bcc/sections/permit-section/permits/plumbing-permit-information"
            },
            {
                "title": "HVAC contractor licensing \u2014 3-year experience + exam",
                "note": "To become a licensed residential HVAC (Mechanical) Contractor in Michigan, the applicant must document at least 3 years of relevant experience, pass the LARA mechanical exam, and pay the licensing fee. There is no homeowner-installer exemption for mechanical work in the same way there is for plumbing \u2014 verify the mechanical contractor on the project carries a current LARA mechanical license in the applicable classifications (e.g., HVAC Equipment, Hydronic Heating & Cooling, Refrigeration) before scheduling rough-in.",
                "applies_to": "Residential HVAC and mechanical work",
                "source": "https://www.facebook.com/groups/466376124400846/posts/1644153246623122/"
            },
            {
                "title": "Building permit issuance \u2014 MCL 125.1511 examination duty",
                "note": "MCL 125.1511 obligates the enforcing agency to examine the application and plans, and to issue the permit if the application complies with the act and code. Construction must commence within the timeframe stated on the permit and follow the approved drawings \u2014 material plan changes after issuance require a revision submittal and re-approval before the work is built or it will fail inspection.",
                "applies_to": "All building permit applications and post-issuance changes",
                "source": "https://www.legislature.mi.gov/Laws/MCL?objectName=mcl-125-1511"
            },
            {
                "title": "No statewide ADU mandate \u2014 local zoning fully controls",
                "note": "Unlike California or Washington, Michigan has no statewide ADU enabling statute or ministerial shot clock. ADU permissibility, lot-size minimums, owner-occupancy, off-street parking, and short-term-rental restrictions are governed entirely by the local township/city/village zoning ordinance. Pull the local zoning code first \u2014 many Michigan jurisdictions still prohibit ADUs outright or require a Special Land Use approval with public hearing.",
                "applies_to": "All ADU / accessory dwelling unit projects in Michigan",
                "source": "https://www.zookcabins.com/regulations/michigan-adus"
            },
            {
                "title": "Critical Dunes \u2014 EGLE construction permit + Vegetative Assurance Plan",
                "note": "Construction or improvements within a designated Critical Dune Area (about 29% of Michigan's dunes along the Lake Michigan and Lake Superior shorelines) require a permit from EGLE (formerly MDEQ), and a written Vegetative Assurance Plan must be approved before disturbance. This is a SEPARATE filing from the local building permit \u2014 check the EGLE Critical Dunes mapper before quoting any shoreline parcel project.",
                "applies_to": "Construction within designated Critical Dune Areas",
                "source": "https://www.oceanaconservation.org/critical-dunes"
            },
            {
                "title": "Part 31 Floodplain \u2014 new residential construction prohibited in floodway",
                "note": "Under NREPA Part 31 (Water Resources Protection), new residential construction is specifically prohibited in the regulated floodway. Fill, grading, or building within the regulated floodplain (outside the floodway) requires an EGLE Water Resources Division floodplain permit and typically a compensating-cut analysis (WRD-031) \u2014 a permit under Part 31 is not required for alterations of existing structures within the floodplain that don't add fill or expand footprint.",
                "applies_to": "Residential construction on parcels mapped within the regulated floodplain or floodway",
                "source": "https://watershedcouncil.org/uploads/7/2/5/1/7251350/permit_guide-ercol-final-web_9.pdf"
            },
            {
                "title": "Wetlands, Inland Lakes & Streams \u2014 EGLE permit categories",
                "note": "EGLE issues separate permit categories for work in regulated wetlands, inland lakes and streams, Great Lakes bottomlands, and floodplains. Minor categories (general permits) cover small docks, seawall maintenance, and minor fills; full individual permits are required for larger fills, dredging, or new structures below the OHWM. Confirm the parcel is not within a regulated wetland on the EGLE Wetlands Map Viewer before site-planning a setback addition or detached accessory structure.",
                "applies_to": "Construction near wetlands, inland lakes, streams, or Great Lakes shoreline",
                "source": "https://www.michigan.gov/egle/about/organization/water-resources/wetlands/permit-categories"
            },
            {
                "title": "Statewide Jurisdiction List \u2014 three levels of enforcement",
                "note": "The LARA Bureau of Construction Codes maintains a Statewide Jurisdiction List identifying which level of government enforces the code on each parcel \u2014 state (BCC), county, or municipal (city/village/township). In jurisdictions where the locality has not adopted local enforcement, plan review and inspections are performed by the state BCC. Always confirm the AHJ on the Jurisdiction List before submitting \u2014 pulling a permit at city hall when the state is the enforcing agency causes weeks of delay.",
                "applies_to": "AHJ identification for any Michigan permit",
                "source": "https://www.michigan.gov/-/media/Project/Websites/lara/bcc-media/Folder5/Statewide_Jurisdiction_List.pdf?rev=1cc0331538974c218c8135bc99e73d70"
            },
            {
                "title": "Zoning approval before building permit \u2014 sequence requirement",
                "note": "Michigan local zoning ordinances commonly require that zoning compliance (and any required variance, special-use approval, or site-plan approval) be obtained before \u2014 or concurrently with \u2014 the building permit. Submitting building plans before securing the zoning sign-off typically triggers a refusal or held application. For nonconforming setbacks or a use that needs a variance, file the ZBA appeal first; building plan review will not advance until zoning clears.",
                "applies_to": "Projects needing variance, special land use, or site plan review",
                "source": "https://www.canr.msu.edu/news/sequence-of-government-permits-is-important"
            },
            {
                "title": "Seller Disclosure Act \u2014 unpermitted work disclosure obligation",
                "note": "Michigan's Seller Disclosure Act (1993 PA 92, MCL 565.951 et seq.) requires the seller of 1-4 unit residential property to deliver a standardized Seller Disclosure Statement to the buyer before an offer is binding. The form requires disclosure of known additions, alterations, or repairs and whether they were done with required permits and inspections \u2014 undisclosed unpermitted work creates post-closing liability for the seller and is a common reason buyers later demand retroactive permitting from the original contractor.",
                "applies_to": "Sellers and remodel contractors doing work that will later be sold",
                "source": "https://www.legislature.mi.gov/documents/mcl/pdf/mcl-Act-92-of-1993.pdf"
            }
        ]
    },
    "NJ": {
        "name": "New Jersey expert pack",
        "expert_notes": [
            {
                "title": "NJ Uniform Construction Code 20-business-day permit-review shot clock",
                "note": "Under the NJ Uniform Construction Code, the local construction code office must act on a complete building permit application within 20 business days. If the package is incomplete, the AHJ is required to issue a written deficiency notice within that same window \u2014 silence past day 20 is not a denial but it is grounds to escalate to the Construction Official and ultimately to DCA Codes. Track the date-stamped receipt of every submittal so you can prove the clock started.",
                "applies_to": "All UCC construction permit applications statewide",
                "source": "https://www.facebook.com/groups/912554242165999/posts/8102854223135929/"
            },
            {
                "title": "2.5-hour inspection time-window notification (SCU bill)",
                "note": "Legislation advanced by the State and Local Government Committee requires the enforcing agency to provide written notification of a 2.5-hour time window for each scheduled UCC inspection, instead of the legacy 'sometime that day' arrangement. This lets you stack subs and avoid trip charges, but only after enactment \u2014 confirm effective date with the local construction official before relying on it. When the window is provided in writing, document missed appointments in writing too.",
                "applies_to": "Field inspections under the NJ Uniform Construction Code",
                "source": "https://www.njlm.org/CivicAlerts.aspx?AID=3335"
            },
            {
                "title": "Home Improvement Contractor (HIC) registration with Division of Consumer Affairs",
                "note": "The NJ Contractors' Registration Act establishes a mandatory annual registration program for any contractor in the business of selling or performing home improvements. Initial registration applications must be submitted by mail to the Division of Consumer Affairs, Office of Consumer Protection, Home Improvement Contractor Unit. The active HIC number must appear on every contract, ad, and the Building Subcode permit form \u2014 operating without one voids contracts and is a Consumer Fraud Act violation.",
                "applies_to": "All residential home improvement work in NJ",
                "source": "https://www.njconsumeraffairs.gov/hic/Pages/applications.aspx"
            },
            {
                "title": "Home Elevation Contractor \u2014 separate registration, 5-year grandfather window",
                "note": "Under N.J.S.A. 45:5AAA-1 through 22, home elevation work requires a Home Elevation Contractor registration distinct from the general HIC. Contractors who had been registered as a home improvement or home elevation contractor in New Jersey for at least 5 years before January 8, 2024 are grandfathered into the new elevation registration; everyone else must qualify from scratch. This is the gating license for shore-county lift-and-rebuild scopes after Sandy/Ida-driven flood-zone elevations.",
                "applies_to": "Home elevation, lift-and-rebuild, and substantial-improvement projects in coastal NJ",
                "source": "https://rjilaw.com/images/NewConstructionLaw/RJI%20Law_New%20Construction%20Law.pdf"
            },
            {
                "title": "HVACR Master Contractor license \u2014 separate from HIC",
                "note": "The NJ State Board of Examiners of Heating, Ventilating, Air Conditioning and Refrigeration Contractors licenses and regulates HVACR contractors statewide. A Master HVACR Contractor license is required to bid, contract for, or supervise HVACR work \u2014 HIC registration alone is insufficient for HVAC scopes. Verify the licensee's number and status on the Division of Consumer Affairs HVACR Board lookup before signing a sub onto a permit, because an unlicensed mechanical sub will block the rough-mech inspection.",
                "applies_to": "HVAC, refrigeration, and combustion-appliance scopes",
                "source": "https://www.njconsumeraffairs.gov/hvacr"
            },
            {
                "title": "NJDEP Flood Hazard General Permits-by-Certification \u2014 15 instant approvals",
                "note": "The NJDEP Watershed & Land Management Program offers 15 Flood Hazard General Permits-by-Certification with effectively instant approval through the NJDEPonline portal for routine residential activities in regulated flood hazard areas. Use the GP-by-Cert path before considering an Individual Permit \u2014 GP processing is measured in days, while an Individual Permit easily runs 90+ days. Pull the NJDEP GIS flood-hazard and riparian-zone layers for the parcel before assuming the work is exempt.",
                "applies_to": "Residential work in NJDEP-regulated flood hazard areas, riparian zones, or freshwater wetlands",
                "source": "https://dep.nj.gov/wlm/permit-types/"
            },
            {
                "title": "NJDEP REAL Rule \u2014 Climate-Adjusted Flood Elevation (CAFE) for coastal work",
                "note": "NJDEP's Resilient Environments and Landscapes (REAL) final rule establishes a Climate-Adjusted Flood Elevation (CAFE) \u2014 reduced from the 5 feet originally proposed in 2024 \u2014 and requires flood-proofing up to that higher level in regulated coastal flood-hazard areas. New residential construction and substantial improvements in those zones must be designed to CAFE plus freeboard. Lock the design elevation with a NJ-licensed engineer at schematic design; retrofitting drawings to CAFE after plan-check is expensive and often requires re-engineering the foundation.",
                "applies_to": "New construction and substantial improvements in regulated coastal flood-hazard areas",
                "source": "https://www.csglaw.com/newsroom/csg-law-alert-njdep-issues-final-coastal-flood-rules/"
            },
            {
                "title": "NJDEP REAL Rule \u2014 stacked freshwater-wetlands mitigation thresholds",
                "note": "The REAL rules decrease mitigation thresholds when combining multiple general permits under NJDEP's Freshwater Wetlands regulations. The pre-REAL practice of stacking several wetlands GPs to stay under the Individual Permit threshold no longer works the way it did \u2014 combined-impact triggers now apply across stacked GPs. Run the cumulative-impact arithmetic against the new lower thresholds before scoping any project that touches more than one GP, or budget for an Individual Permit and its mitigation requirements.",
                "applies_to": "Projects relying on multiple Freshwater Wetlands general permits",
                "source": "https://www.daypitney.com/njdeps-real-rules-what-developers-need-to-know"
            },
            {
                "title": "CAFRA Individual Permit \u2014 mandatory electronic filing via NJDEPonline",
                "note": "All applications for a Coastal Area Facility Review Act (CAFRA) Individual Permit must be submitted electronically through NJDEPonline at https://njdeponline.com \u2014 paper packages are not accepted. CAFRA jurisdiction runs along the Atlantic and Delaware Bay coasts, and the impervious-cover and vegetative-cover limits depend on the parcel's Coastal Planning Area designation. File the Jurisdictional Request Form first when CAFRA applicability is unclear, since starting demolition without it can convert a permitted residential addition into an enforcement action.",
                "applies_to": "Residential development within the CAFRA coastal zone",
                "source": "https://dep.nj.gov/wp-content/uploads/wlm/downloads/caf/cp_011.pdf"
            },
            {
                "title": "Pinelands Area parallel review \u2014 General Permit 4 plus Pinelands Commission",
                "note": "For projects in the Pinelands Area as designated under the Pinelands Protection Act, NJDEP General Permit 4 may apply in addition to a separate Pinelands Commission Certificate of Filing \u2014 they are parallel filings, not substitutes for each other, and neither replaces the local UCC building permit. Check the Pinelands Comprehensive Management Plan land-capability map before scoping any work in Atlantic, Burlington, Camden, Cape May, Cumberland, Gloucester, or Ocean County. Septic, well, and clearing limits in the Preservation Area Zone are stricter than the statewide UCC defaults.",
                "applies_to": "Residential work within the 1.1-million-acre Pinelands Area",
                "source": "https://dep.nj.gov/wp-content/uploads/wlm/downloads/caf/cp_gp04.pdf"
            },
            {
                "title": "NJ UCC base code edition \u2014 2021 I-Codes adopted September 2022",
                "note": "In September 2022, the NJ Department of Community Affairs adopted revisions based on the 2021 editions of the International Building Code, International Residential Code, and companion subcodes under the Uniform Construction Code. Those 2021 I-codes remain the enforceable base subcodes statewide pending the next adoption cycle. Confirm with the local Construction Code Office which subcode edition is being applied to the application date, especially on legacy projects whose drawings reference the 2018 cycle.",
                "applies_to": "All UCC permit submittals statewide",
                "source": "https://www.nj.gov/dca/codes/codreg/pdf_rule_proposals/2024_prop_p1.pdf"
            },
            {
                "title": "Energy code transition \u2014 2024 IECC pending, no formal stretch code",
                "note": "NJ is advancing its base energy code to the 2024 IECC with ASHRAE 90.1-2022 without amendments, but as of 2025 the state does not have a formal stretch code that contractors can opt into. Don't promise customers an above-code 'stretch' compliance path that doesn't legally exist in NJ \u2014 design to the currently adopted IECC base and document any voluntary measures separately. Watch the NJ Energy Code Collaborative for the formal adoption date so REScheck/COMcheck inputs match what the AHJ is reviewing against.",
                "applies_to": "New residential construction and alterations affecting conditioned space",
                "source": "https://njenergycodecollaborative.org/wp-content/uploads/2025/07/2025-05-07-NJ-ECC-Energy-Codes-for-New-Construction_meeting-notes.pdf"
            },
            {
                "title": "Zero Energy Construction Act (S3576) \u2014 proposed January 1, 2027 effective date",
                "note": "Pending bill S3576, the 'Zero Energy Construction Act,' would require all new residential and commercial construction in NJ to meet zero-energy standards beginning January 1, 2027. Projects whose permit applications cross that boundary should expect AHJs to flag near-term ZEB readiness \u2014 frame envelope, PV-ready conduit, and EV-ready raceway should be specified now to avoid a redesign mid-permit. Confirm enactment status before quoting design decisions on the 2027 date, since the bill was introduced not adopted.",
                "applies_to": "New construction projects designed in 2026 or later",
                "source": "https://legiscan.com/NJ/text/S3576/id/3370225/New_Jersey-2026-S3576-Introduced.html"
            },
            {
                "title": "BPU interconnection \u2014 three-tier filing separate from the electrical permit",
                "note": "Solar PV, battery storage, and standby-generator interconnections in NJ are classified by aggregate system rating into Levels 1, 2, and 3 under the BPU interconnection rules. The interconnection application is a parallel filing with the serving utility (PSE&G, JCP&L, ACE, or RECO) and is not bundled with the UCC electrical-subcode permit \u2014 final electrical inspection and Permission to Operate require both tracks to clear. File the interconnection package concurrently with the electrical subcode submittal to keep the PTO date from slipping weeks past final inspection.",
                "applies_to": "Residential solar PV, battery storage, and private-generation interconnections",
                "source": "https://www.oru.com/en/save-money/using-private-generation-energy-sources/applying-for-private-generation-interconnection-in-new-jersey"
            }
        ]
    },
    "VA": {
        "name": "Virginia expert pack",
        "expert_notes": [
            {
                "title": "Virginia USBC 2021 I-Code adoption \u2014 effective Jan 18, 2024",
                "note": "Virginia adopted the 2021 I-Codes (IBC, IRC, IECC, IPC, IMC) as referenced in the Virginia Construction Code Part 1 of the Uniform Statewide Building Code, effective January 18, 2024, alongside the 2021 Statewide Fire Prevention Code. Permit applications submitted before that effective date may have vested under the 2018 cycle \u2014 confirm with the AHJ which code edition governs your drawings before submitting, and update IECC envelope/energy calcs if you transitioned mid-design.",
                "applies_to": "All new construction and alterations subject to USBC Part 1",
                "source": "https://www.dhcd.virginia.gov/codes"
            },
            {
                "title": "USBC statewide preemption \u2014 locals cannot add stricter technical amendments",
                "note": "The Uniform Statewide Building Code is adopted by the Virginia Board of Housing and Community Development and is binding statewide; local jurisdictions cannot alter the technical requirements or impose stricter construction standards on top of the USBC. If a city plan reviewer cites a 'local amendment' to a structural, energy, or plumbing requirement, push back and request the regulatory citation \u2014 most such 'amendments' are limited to administrative procedures, not technical content.",
                "applies_to": "Disputes over local code interpretations exceeding USBC requirements",
                "source": "https://www.energycodes.gov/status/states/virginia"
            },
            {
                "title": "DPOR contractor license classes A, B, C \u2014 dollar-threshold tiers",
                "note": "The Virginia Board for Contractors issues Class C (single contracts under $10,000 and annual gross under $150,000), Class B (single contracts $10,000\u2013$120,000 or annual gross under $750,000), and Class A (no monetary limit) licenses. The classification (e.g., RBC Residential Building, CBC Commercial Building) is separate from the class \u2014 verify both before quoting, since a Class C with only an RBC cannot legally bid a $150,000 addition.",
                "applies_to": "All paid construction contracts in Virginia",
                "source": "http://www.dpor.virginia.gov/Boards/Contractors"
            },
            {
                "title": "CBC classification does NOT cover electrical, plumbing, HVAC, or gas",
                "note": "A DPOR Commercial Building Contractor (CBC) classification \u2014 and the residential analog \u2014 explicitly does not authorize electrical, plumbing, HVAC, or gas-fitting services. A general contractor either holds the appropriate trade specialty (ELE, PLB, HVA, GFC) themselves or must subcontract those scopes to a separately licensed firm. Pulling an electrical permit on a CBC alone is a common board-discipline trigger.",
                "applies_to": "General contractors performing or self-permitting trade work",
                "source": "https://www.dpor.virginia.gov/sites/default/files/Records%20and%20Documents/Regulant%20List/VA%20Contractors%20Classifications%20%26%20Specialties.pdf"
            },
            {
                "title": "Tradesman licensing \u2014 Residential Plumbing, HVAC Mechanic, Electrician tiers",
                "note": "DPOR's Tradesmen Program issues separate Tradesman, Master, and Residential-only licenses for Electrical, Plumbing, HVAC, and Gas Fitting. Continuing education is required (3 hours per renewal, with extra hours for combined plumbing+electrical residential licensees), and the credential must be on file with the Board for Contractors before the trade firm can pull permits. Expired tradesman cards block permit issuance even when the contractor's Class A/B/C license is current.",
                "applies_to": "Electrical, plumbing, HVAC, and gas-fitting trade work",
                "source": "http://www.dpor.virginia.gov/Boards/Tradesmen"
            },
            {
                "title": "Virginia electrical code update \u2014 fully effective Jan 18, 2025",
                "note": "Amendments to the Virginia electrical code (NEC reference within USBC Part 1) were adopted January 18, 2024 with a one-year transitional grace period and became fully effective January 18, 2025. Permits issued during the transition could elect the prior cycle, but any permit issued after Jan 18, 2025 must comply with the new AFCI/GFCI, EV-charger load-calc, and service-equipment provisions \u2014 verify your panel schedule and load letter were redrawn under the current edition.",
                "applies_to": "Electrical permits and service upgrades",
                "source": "https://schroederdesignbuild.com/blog/remodeling-industry/important-electrical-code-changes-that-could-impact-your-virginia-remodel/"
            },
            {
                "title": "Three-year USBC code-development cycle",
                "note": "The Virginia Building Codes are updated on a three-year cycle through the DHCD code development process, mirroring the ICC publication cadence. Code-change proposals for the next (2024-based) cycle had a tentative submission deadline of October 1, 2025; expect statewide adoption hearings in 2026 and an effective date roughly 12\u201318 months after Board of Housing approval. Plan long-lead projects (multifamily, large additions) around the cycle so drawings don't become stranded under a superseded edition.",
                "applies_to": "Long-duration design projects spanning a code cycle",
                "source": "https://www.dhcd.virginia.gov/2021-code-development-cycle"
            },
            {
                "title": "ADUs become by-right in 2027 under new Virginia statute",
                "note": "Virginia enacted ADU-enabling legislation in the 2026 session that takes effect the following year, requiring localities to allow at least one ADU on single-family lots subject to objective standards. Until the effective date, ADUs are still governed entirely by local zoning \u2014 many counties currently prohibit them outright or treat them as 'accessory apartments' requiring a special-use permit. Confirm the locality's current ordinance and the statute's effective date before promising a client an ADU on a 2026 timeline.",
                "applies_to": "Backyard cottages, accessory apartments, and detached ADU projects",
                "source": "https://virginiamercury.com/2026/04/16/a-new-law-will-make-it-easier-to-build-a-tiny-house-in-your-back-yard-starting-next-year/"
            },
            {
                "title": "Chesapeake Bay Preservation Act \u2014 RPA review can apply without a building permit",
                "note": "In Tidewater localities subject to the Chesapeake Bay Preservation Act, a 'plan of development' review is required prior to building-permit issuance for parcels touching a Resource Protection Area (RPA) or Resource Management Area (RMA). RPA review may be triggered for land-disturbing activity even when no building permit is required (e.g., shed under threshold, fence in buffer). Pull the locality's CBPA map early \u2014 adding RPA mitigation late forces redesign of grading, septic, and tree-clearing scopes.",
                "applies_to": "Construction in Tidewater Virginia near tidal/non-tidal wetlands and tributaries",
                "source": "https://www.deq.virginia.gov/water/chesapeake-bay/chesapeake-bay-preservation-act"
            },
            {
                "title": "VESCP plan review shot clock \u2014 45 days on resubmittals",
                "note": "Under 9VAC25-875-370, the local Virginia Erosion and Sediment Control Program (VESCP) authority must act on a previously-deemed-inadequate erosion and sediment control plan within 45 days of receiving the revised plan. If the AHJ misses that window after a resubmittal, escalate to DEQ \u2014 the clock is regulatory, not advisory. This is a separate filing from the building permit and must be approved before land-disturbing activity begins.",
                "applies_to": "Land-disturbing activity requiring an E&S control plan",
                "source": "https://law.lis.virginia.gov/admincodefull/title9/agency25/chapter875/partIII/"
            },
            {
                "title": "VPDES Construction General Permit triggered at 1 acre disturbance",
                "note": "An individual or General VPDES Permit for Discharges of Stormwater from Construction Activities (CGP) is required for land-disturbing activities affecting 1 acre or more (or part of a common plan of development totaling 1+ acre). This is a separate DEQ filing from the local building permit and the local E&S plan, and requires a registration statement, SWPPP, and a qualified Stormwater Inspector. Single-lot infill is usually under threshold; subdivisions and most ADU+grading combos exceed it.",
                "applies_to": "Construction sites disturbing \u22651 acre or in a 1+ acre common plan",
                "source": "https://online.encodeplus.com/regs/deq-va/doc-viewer.aspx?secid=92"
            },
            {
                "title": "SCC interconnection \u2014 Level 1/2/3 thresholds for solar + battery",
                "note": "Virginia's Small Generator Interconnection rules under 20VAC5-314 split into Level 1 (inverter-based \u226425 kW on radial circuits, fastest path), Level 2 (certified SGF up to 2 MW), and Level 3 (anything that doesn't pass Level 1/2 screens). Most residential rooftop PV with battery falls under Level 1, but exceeding the 25 kW AC threshold or paralleling on a non-radial circuit kicks it to Level 2 with longer study timelines. File the interconnection application with the utility in parallel with \u2014 not after \u2014 the building permit, since utility approval is a separate gating step.",
                "applies_to": "Residential solar PV and battery systems exporting to the grid",
                "source": "https://law.lis.virginia.gov/admincode/title20/agency5/chapter314/section60/"
            },
            {
                "title": "Off-grid / non-exporting battery storage exempt from utility interconnection",
                "note": "Off-grid and non-exporting energy storage systems do NOT require utility interconnection approval in Virginia, but they remain fully subject to NEC Article 706 (energy storage), USBC electrical permit requirements, and local fire marshal review for indoor lithium-ion installations over the listed kWh thresholds. This is a common scope where contractors over-file (sending an unnecessary interconnection application) or under-file (skipping the local electrical permit assuming 'off-grid' means unregulated).",
                "applies_to": "Battery-only and off-grid storage retrofits",
                "source": "https://virginiaelectricalauthority.com/solar-and-renewable-energy-electrical-virginia"
            },
            {
                "title": "AHJ split \u2014 city, town, county, and state-building-only jurisdictions",
                "note": "Virginia's permit jurisdiction is fragmented: independent cities run their own building departments, counties cover unincorporated land plus any incorporated towns that have not opted out, and incorporated towns may run their own department or defer to the county. The first step on any project is confirming whether the parcel's permits are issued by a county, city, or town building department \u2014 a common cause of weeks-long delays is filing with the county when the parcel sits inside a town that pulls its own permits.",
                "applies_to": "Project intake and jurisdiction confirmation",
                "source": "https://www.permitflow.com/state/virginia"
            }
        ]
    },
    "TN": {
        "name": "Tennessee expert pack",
        "expert_notes": [
            {
                "title": "Tennessee $25,000 prime contractor license threshold",
                "note": "The Tennessee Board for Licensing Contractors requires a state Contractor's license before bidding or negotiating a price whenever the total project cost (labor + materials) is $25,000 or more. Verify the contractor's license, classification (BC residential, CMC mechanical, CME electrical, CMA plumbing), and monetary limit on the Board's lookup before quoting \u2014 bidding above your monetary limit or without the correct classification can void the contract and trigger Board discipline.",
                "applies_to": "Any prime construction contract in Tennessee at or above $25,000",
                "source": "https://www.tn.gov/commerce/regboards/contractors/license/get/contractor.html"
            },
            {
                "title": "Home Improvement Contractor license \u2014 $3,000 to $24,999 residential remodel band",
                "note": "Tennessee requires a separate Home Improvement Contractor (HIC) license for residential remodel/repair work between $3,000 and $24,999 in the counties where the HIC program is in effect (including Davidson, Hamilton, Haywood, Knox, Marion, Robertson, Rutherford, and Shelby). Above $24,999 the full Contractor's license is required instead. Confirm the county is on the HIC list before quoting a remodel \u2014 pulling a permit under the wrong license tier is a frequent rejection reason.",
                "applies_to": "Residential remodel/repair work between $3,000 and $24,999 in HIC-program counties",
                "source": "https://www.tn.gov/commerce/regboards/contractors.html"
            },
            {
                "title": "Limited Licensed Electrician (LLE) and Limited Licensed Plumber (LLP) sub-thresholds",
                "note": "For electrical or plumbing work under $25,000 that is not subcontracted under a licensed prime, Tennessee issues Limited Licensed Electrician (LLE) and Limited Licensed Plumber (LLP) credentials through the Board for Licensing Contractors. These are the correct credentials for small standalone service work (panel swap, water heater, repipe) in jurisdictions that rely on the state for trade licensing \u2014 using a CMC/CMA prime license for sub-threshold standalone work is overkill and a Contractor's license cannot substitute for the LLE/LLP where an LLE/LLP is what the AHJ is checking for.",
                "applies_to": "Standalone residential electrical or plumbing work under $25,000",
                "source": "https://www.tn.gov/commerce/regboards/contractors/license/forms.html"
            },
            {
                "title": "2021 I-Codes effective statewide April 17, 2025",
                "note": "The State Fire Marshal's Office adopted the 2021 editions of the IBC, IRC, IMC, IPC, IFGC, and IECC with Tennessee amendments, effective April 17, 2025. Per Department of Commerce and Insurance Rule 0780-02-03-.11, plans for construction submitted before that effective date may still be reviewed under the prior (2018) code at the AHJ's discretion. Confirm which code edition the local plan reviewer is enforcing before producing drawings, especially for projects straddling the transition.",
                "applies_to": "All permit applications under state-enforced jurisdictions",
                "source": "https://aiatn.org/tn-state-fire-marshal-codes-adoption-update/"
            },
            {
                "title": "Nashville/Metro adopted 2024 I-Codes ahead of the state",
                "note": "Metro Nashville-Davidson signed adoption of the 2024 International Building Codes into law on July 16, 2025, putting Nashville on a NEWER code cycle than the state's 2021 baseline. Home-rule cities and counties that have opted out of state code enforcement can adopt their own edition, so never assume the state's 2021 cycle applies inside Nashville, Memphis, Knoxville, Chattanooga, or other home-rule jurisdictions \u2014 pull the local code-adoption ordinance before drawing.",
                "applies_to": "Permits inside Metro Nashville and other home-rule jurisdictions",
                "source": "https://www.nashville.gov/departments/codes/news/metro-adopts-2024-international-building-codes"
            },
            {
                "title": "County opt-in model for residential building code (TCA Title 68 Ch. 120)",
                "note": "Under the Tennessee Building Construction Safety Act, the residential code is NOT automatically enforced in unincorporated counties \u2014 a county legislative body must enact a resolution adopting the building, plumbing, gas, or fire prevention code by reference. Many rural Tennessee counties have NOT opted in, in which case the State Fire Marshal's residential program (or no inspection at all for 1\u20132 family) applies. Always confirm whether the county has adopted the IRC locally before promising a code-compliant inspection process.",
                "applies_to": "Residential work in unincorporated Tennessee counties",
                "source": "https://www.ctas.tennessee.edu/eli/adoption-building-codes"
            },
            {
                "title": "State Fire Marshal residential permits via online portal",
                "note": "In jurisdictions where the State Fire Marshal's Office is the AHJ for 1\u20132 family dwellings (counties that have not opted into a local residential program), residential and electrical permits are purchased directly through the SFMO's online portal, with inspections requested through the same system. The state phone line for permits is 615-741-7170 (M\u2013F 7 a.m.\u20134:30 p.m. CST). Submitting to the wrong AHJ (state vs. municipal) is one of the most common Tennessee rejection reasons \u2014 confirm jurisdiction before paying.",
                "applies_to": "Residential and electrical work in state-enforced jurisdictions",
                "source": "https://www.tn.gov/commerce/fire/residential-permits.html"
            },
            {
                "title": "State electrical inspection is a SEPARATE filing from the building permit",
                "note": "Tennessee electrical permits are administered by the State Fire Marshal's Electrical Inspection Section (or a delegated municipal inspector such as KUB in Knoxville/Alcoa) and are a distinct filing from the structural/building permit. KUB and other utilities will require evidence of a state-issued electrical permit and a passed inspection before energizing the service. Plan for two separate permit numbers and two separate inspection sequences on any project that touches wiring.",
                "applies_to": "All projects involving new or altered electrical work",
                "source": "https://www.tn.gov/commerce/fire/permit/electrical.html"
            },
            {
                "title": "KUB and municipal-utility energization requires state electrical permit + inspection",
                "note": "Knoxville Utilities Board and similar municipal/cooperative utilities will not energize a service, meter set, or solar interconnection without a state electrical permit on file and a passing inspection report. KUB explicitly directs customers to call the state permit office at 615-741-7170 to purchase the permit before requesting service. Sequence the project so the state electrical permit is pulled and inspected BEFORE scheduling the utility energization appointment, or the utility crew will roll without setting the meter.",
                "applies_to": "Service upgrades, new construction energization, and PV interconnection in KUB and similar municipal-utility territories",
                "source": "https://www.kub.org/start-stop-service/utility-construction/electrical-permits/"
            },
            {
                "title": "Floodplain Overlay District triggers separate floodplain development permit",
                "note": "Tennessee participates in the National Flood Insurance Program through TEMA, and most jurisdictions enforce a Floodplain Overlay District requiring a separate floodplain development permit for any construction (including accessory structures and substantial improvements \u2265 50% of market value) within a SFHA Zone A or AE. The lowest finished floor must typically be at or above Base Flood Elevation plus local freeboard (often +1 to +2 feet). Pull the FEMA NFHL viewer for the parcel before site design \u2014 flood compliance is parallel to, not part of, the building permit.",
                "applies_to": "Any construction in or near a FEMA Special Flood Hazard Area",
                "source": "https://www.fema.gov/flood-maps/national-flood-hazard-layer"
            },
            {
                "title": "TDEC NPDES Construction Stormwater permit at 1 acre of disturbance",
                "note": "If a project disturbs 1 acre or more (or is part of a larger common plan of development), a Notice of Intent under TDEC's NPDES Stormwater Construction General Permit must be filed at least 15 days before ground disturbance, along with an SWPPP. This is a state environmental filing entirely separate from the local building/grading permit. ADUs, pool installations, and small additions on infill lots usually fall under the 1-acre threshold, but lot splits and new SFR subdivisions routinely trip it.",
                "applies_to": "Construction projects disturbing 1 acre or more",
                "source": "https://www.tn.gov/environment/sbeap/gdf/timelines.html"
            },
            {
                "title": "Tennessee Residential Property Disclosure Act (TCA \u00a7 66-5-201 et seq.)",
                "note": "Sellers of residential real property with one to four dwelling units must furnish the buyer with a written Residential Property Condition Disclosure before transfer, listing known material defects including unpermitted additions and known code issues. A disclaimer (\"as-is\") form is only valid where the buyer affirmatively waives the disclosure. Unpermitted work discovered post-closing is a frequent source of rescission and damages claims \u2014 when legalizing prior unpermitted work, document the as-built scope so the seller can disclose accurately.",
                "applies_to": "Sale of 1-to-4-unit residential property in Tennessee",
                "source": "https://law.justia.com/codes/tennessee/title-66/chapter-5/part-2/section-66-5-202/"
            },
            {
                "title": "No statewide ADU shot clock \u2014 timing is set locally",
                "note": "Tennessee has NO statewide ministerial ADU shot clock equivalent to California's 60-day rule, and has no statewide ADU enabling statute that overrides local zoning. ADU feasibility, setbacks, owner-occupancy, and parking requirements are entirely a function of the city/county zoning code (e.g., Nashville Metro DADU rules differ markedly from Knoxville and Chattanooga). Do not promise a statutory turnaround time \u2014 quote the AHJ's published target review time and treat ADUs as a discretionary local zoning question, not a ministerial state right.",
                "applies_to": "Accessory dwelling unit projects statewide",
                "source": "https://www.tn.gov/commerce/regboards/contractors.html"
            },
            {
                "title": "HVAC work falls under CMC mechanical classification, $25K threshold still applies",
                "note": "There is no separate \"Tennessee HVAC license\" \u2014 HVAC contractors operate under the Contractor's license with a CMC (Mechanical) classification, and the same $25,000 prime-contract threshold applies. For HVAC change-outs and service work under $25,000 that is not subcontracted under a licensed prime, the work can typically proceed without a state Contractor's license but still requires the appropriate local mechanical permit and, where applicable, a state electrical permit for any new circuit or disconnect. Verify the CMC classification AND monetary limit on the Board lookup before signing a change-out contract above the threshold.",
                "applies_to": "HVAC change-outs, new installs, and mechanical alterations",
                "source": "https://www.servicetitan.com/licensing/hvac/tennessee"
            }
        ]
    },
    "MA": {
        "name": "Massachusetts expert pack",
        "expert_notes": [
            {
                "title": "10th Edition Massachusetts State Building Code effective October 11, 2024",
                "note": "The 10th Edition of the Massachusetts State Building Code (780 CMR) became effective October 11, 2024 and is now the controlling code for new permit applications statewide. The 9th Edition concurrency window has closed for most jurisdictions, so designers must confirm drawings reference 10th Edition chapters and amendments before submittal \u2014 pulling templates from a 9th Edition project is the most common cause of plan-review rejection on the first round.",
                "applies_to": "All building permit applications filed after October 11, 2024",
                "source": "https://www.salemma.gov/558/New-Code-Changes-9th-Edition-Stretch-Cod"
            },
            {
                "title": "Three-tier energy code: Base / Stretch / Specialized (Municipal Opt-In)",
                "note": "Massachusetts operates three concurrent energy codes: the Base Code (IECC 2021 with MA amendments under 780 CMR Chapter 11R for residential and Chapter 13 for commercial), the Stretch Code, and the Specialized Code. Roughly 300 municipalities have adopted Stretch or Specialized \u2014 confirm which code the AHJ enforces before sizing envelope, HERS, or mechanical systems, because a Base-Code design will fail plan review in a Stretch or Specialized town.",
                "applies_to": "All new construction, additions, and major alterations affecting conditioned space",
                "source": "https://www.mass.gov/info-details/2025-massachusetts-building-energy-codes"
            },
            {
                "title": "New residential Stretch Code 225 CMR 22.00 effective February 14, 2025",
                "note": "Effective February 14, 2025, the new low-rise residential Stretch Code under 225 CMR 22.00 raised efficiency thresholds for one- and two-family homes and townhouses in Stretch-adopting municipalities. Projects permitted on or after that date in those municipalities must meet the updated HERS targets, mechanical equipment ratings, and air-leakage limits \u2014 older Stretch Code design templates from 2024 and earlier will trigger rejection.",
                "applies_to": "Low-rise residential projects in Stretch Code municipalities permitted on/after 2025-02-14",
                "source": "https://www.mbcia.org/news/new-massachusetts-stretch-code-225-cmr-22-00-specialized-energy-code-for-low-rise-residential-effective-february-14-2025"
            },
            {
                "title": "780 CMR 105.3 \u2014 30-day permit issue/deny clock",
                "note": "Under 780 CMR 105.3.1, the building official must issue or deny a complete building permit application within 30 days of filing, and the fire review portion is bound by 10 working days (extendable to 30 days total only with a written extension request). If the AHJ blows past 30 days without a denial or written extension, you have grounds to escalate to the building commissioner \u2014 track filing dates and request the timestamped intake receipt.",
                "applies_to": "All building permit applications statewide",
                "source": "https://omegapermits.com/blogs/massachusetts-building-permit-timelines-780-cmr"
            },
            {
                "title": "Construction Supervisor License (CSL) required for 1-2 family and small structures",
                "note": "The Board of Building Regulations and Standards (BBRS) issues the Construction Supervisor License required to pull permits and supervise work on 1-2 family dwellings and other structures under 35,000 cubic feet. Verify the CSL holder, license class (Unrestricted, Restricted 1&2 Family, or specialty), and active status before contracting \u2014 an expired CSL means the permit cannot lawfully be pulled in that supervisor's name.",
                "applies_to": "All structural work on 1-2 family dwellings and structures < 35,000 cu ft",
                "source": "https://www.mass.gov/construction-supervisor-licensing"
            },
            {
                "title": "Home Improvement Contractor (HIC) registration \u2014 $150 fee + Guaranty Fund",
                "note": "Any contractor performing residential remodeling on existing 1-4 family owner-occupied dwellings must register with the HIC Program ($150 registration plus a Guaranty Fund payment scaled to employee count). HIC registration is separate from a CSL \u2014 many contractors hold both because the HIC is a consumer-protection registration while the CSL is the technical permit-pulling credential. Working without HIC registration on covered jobs voids contract protections and bars Guaranty Fund recovery for the homeowner.",
                "applies_to": "Residential remodel work on existing owner-occupied 1-4 family dwellings",
                "source": "https://www.mass.gov/info-details/home-improvement-contractor-registration-and-renewal"
            },
            {
                "title": "Massachusetts electrical license tiers \u2014 Journeyman \u2192 Master with 150-hour Board education",
                "note": "Electrical work requires a state-issued license from the Board of State Examiners of Electricians: a Journeyman (1E) works under supervision, and a Master (A) can pull permits and run a shop. Upgrading from Journeyman to Master requires at least one year of Massachusetts Journeyman experience and 150 hours of Board-approved education before sitting the exam \u2014 confirm the contractor's Master license is active before any electrical permit submittal.",
                "applies_to": "All electrical work statewide",
                "source": "https://www.mass.gov/how-to/apply-for-an-individual-electrical-or-systems-license"
            },
            {
                "title": "Wetlands Protection Act \u2014 Order of Conditions from local Conservation Commission",
                "note": "Any work within a wetland resource area or its 100-foot buffer zone (or 200-foot Riverfront Area) requires an Order of Conditions issued by the local Conservation Commission under M.G.L. c. 131 \u00a740 (WPA Form 5). This is a SEPARATE filing from the building permit and must be issued, with all administrative appeal periods closed, before construction can begin \u2014 file the Notice of Intent (NOI) early because a contested order can add 60-90 days to the schedule.",
                "applies_to": "Any earthwork, addition, or new structure within wetlands, buffer zone, or Riverfront Area",
                "source": "https://www.massaudubon.org/content/download/25762/file/WPA-Fact-Sheet_2017.pdf"
            },
            {
                "title": "ADU as-of-right under the Affordable Homes Act \u2014 effective February 2, 2025",
                "note": "The Affordable Homes Act made Protected Use ADUs (one ADU up to 900 sq ft on single-family lots) allowed by-right effective February 2, 2025, removing special permit and discretionary review for qualifying units. Local zoning bylaws and procedures still vary on dimensional standards, parking, and owner-occupancy \u2014 confirm the municipality has updated its bylaw and is processing ADUs ministerially rather than still routing them through ZBA.",
                "applies_to": "Single-family-zoned residential parcels statewide",
                "source": "https://www.mass.gov/info-details/accessory-dwelling-unit-adu-faqs"
            },
            {
                "title": "Realistic ADU permitting timeline \u2014 90 to 120 days",
                "note": "Even with as-of-right zoning, end-to-end ADU permitting typically runs 90-120 days from intake to building permit issuance because of stacked reviews: zoning sign-off, building plan check, electrical/plumbing/mechanical sub-permits, and (on unsewered lots) Title 5 septic review by the local Board of Health. Septic adds the most variance \u2014 projects where Title 5 capacity is already approved finish closer to 90 days; projects requiring a new septic design routinely hit 120+.",
                "applies_to": "ADU projects statewide, particularly outside MWRA/MetroWest sewered districts",
                "source": "https://buildx.com/article/how-long-does-adu-permitting-take-in-massachusetts/"
            },
            {
                "title": "Permit jurisdiction is municipal \u2014 no county building departments",
                "note": "Massachusetts has no county-level building department or zoning authority; every city and town runs its own permitting and adopts its own zoning bylaw under the Home Rule Amendment. Always identify the specific municipal building department, electrical inspector, plumbing/gas inspector, and conservation commission for the parcel \u2014 abutting towns can have different Stretch Code adoption, ADU bylaws, and submittal portals despite identical state code.",
                "applies_to": "All permit work statewide \u2014 verify municipal AHJ at project intake",
                "source": "https://www.mass.gov/info-details/re16rc13-zoning-building-codes"
            },
            {
                "title": "Next energy code update expected to take effect in 2027",
                "note": "DOER's published code roadmap signals the next major Base / Stretch / Specialized Code update is likely to take effect in 2027, layered onto a future IECC model code with Massachusetts thermal-performance amendments. Long-cycle projects that won't permit until 2027 should design to the anticipated tighter envelope and equipment requirements rather than today's code, since vesting is by permit-application date, not contract date.",
                "applies_to": "Long-lead-time projects expected to permit in 2027 or later",
                "source": "https://ma-eeac.org/wp-content/uploads/DOER-Codes-101-for-EEAC-Sept-2025-final-9-15-25.pdf"
            },
            {
                "title": "Common rejection trigger \u2014 unpermitted wall, plumbing, or electrical alterations",
                "note": "The most frequent permit-violation patterns flagged in Massachusetts residential remodels are removing or adding walls (even non-structural), changing plumbing routing, and altering electrical circuits without pulling sub-permits \u2014 all of which require permits regardless of whether they are 'cosmetic' to the homeowner. Unpermitted work surfaces at resale through smoke-cert inspections and Title 5 review, and the AHJ can require exposure of the work for inspection before issuing a certificate of occupancy.",
                "applies_to": "All residential remodel scopes statewide",
                "source": "https://artisansrenovations.com/home-renovation/permit-mistakes-renovation-massachusetts"
            },
            {
                "title": "CSL complaint / appeal path through BBRS for 780 CMR violations",
                "note": "Complaints against a Construction Supervisor must allege a specific 780 CMR violation and be supported by a written report or letter \u2014 vague workmanship complaints alone are dismissed. For permit denials, the appeal path is first to the local Building Commissioner, then to the Massachusetts State Building Code Appeals Board under 780 CMR 113, with the ZBA handling zoning-bylaw denials separately \u2014 pick the right forum because the Building Code Appeals Board cannot overturn a zoning denial and the ZBA cannot overturn a code denial.",
                "applies_to": "Permit denials, CSL disputes, and code-interpretation appeals",
                "source": "https://www.mass.gov/info-details/file-a-complaint-against-a-licensed-construction-supervisor"
            }
        ]
    },
    "IN": {
        "name": "Indiana expert pack",
        "expert_notes": [
            {
                "title": "IDNR Construction in a Floodway permit (Division of Water)",
                "note": "Any structure, fill, excavation, or development in a regulated floodway in Indiana requires a Construction in a Floodway permit from the Indiana Department of Natural Resources, Division of Water, before local building permits can close out. All four statutory eligibility criteria (drainage area, no rise in flood elevation, low damage potential, etc.) must be met or the permit is denied. Submit the application package with an Assessment of Floodplains in Indiana attached \u2014 missing this filing is the most common reason floodway-adjacent residential additions stall.",
                "applies_to": "Any construction inside a regulated Indiana floodway",
                "source": "https://www.in.gov/dnr/water/regulatory-permit-programs/"
            },
            {
                "title": "Local floodplain development permit required before ANY SFHA work",
                "note": "Indiana communities participate in the NFIP and require a local Floodplain Development Permit before any work \u2014 including grading, fill, accessory structures, or substantial improvements \u2014 within a Special Flood Hazard Area (SFHA). The 50%-of-market-value substantial improvement / substantial damage rule triggers full code compliance for the entire structure, not just the addition. Pull the FEMA NFHL panel and the local floodplain map at the start of due diligence; a missed floodplain permit is one of the most frequent stop-work-order causes statewide.",
                "applies_to": "Parcels in FEMA Special Flood Hazard Areas",
                "source": "https://www.in.gov/dnr/water/files/wa-FP_Management_Indiana_QuickGuide.pdf"
            },
            {
                "title": "Indiana Building / Residential / Electrical / Energy Code structure \u2014 675 IAC",
                "note": "The Fire Prevention and Building Safety Commission adopts Indiana's statewide codes under Title 675 IAC: Building Code at 675 IAC 13-2.6, Residential Code at 675 IAC 14, Electrical Code at 675 IAC 17, Mechanical at 675 IAC 18, and Energy Conservation Code at 675 IAC 19. The Building Code Update Committee is actively drafting a rule to update 675 IAC 13-2.6 against the 2024 I-Codes, so the enforced edition can shift mid-project \u2014 confirm which edition the AHJ is enforcing on the date of plan submittal and lock the code cycle in writing.",
                "applies_to": "All permitted construction statewide",
                "source": "https://www.in.gov/dhs/boards-and-commissions/building-code-update-committee/"
            },
            {
                "title": "IDHS state plan-review 10-business-day shot clock (Class 1 only)",
                "note": "The Indiana Department of Homeland Security (IDHS) Plan Review division operates an automatic 10-business-day clock from the date of application: within that window IDHS must issue a release, a denial, or a Request for Additional Information. Silence past the 10th business day is grounds to escalate to the Plan Review supervisor. This clock applies to Class 1 structures (commercial, multi-family \u22654 units, public buildings) that must be filed with the state \u2014 Class 2 (1-to-3 family residential) bypasses IDHS and goes directly to the local AHJ.",
                "applies_to": "Class 1 projects required to file with IDHS",
                "source": "https://www.in.gov/dhs/building-plan-review/building-plan-review-process/"
            },
            {
                "title": "Plumbing contractors are licensed at the STATE level via PLA",
                "note": "Plumbing is one of the few trades Indiana licenses statewide: the Indiana Professional Licensing Agency (PLA) Plumbing Commission issues Plumbing Contractor and Journeyman licenses under IC 25-28.5. A Corporate Plumbing Contractor License requires a licensed Plumbing Contractor on staff. Verify the PLA license number on the state lookup before signing \u2014 unlicensed plumbing work voids mechanic's lien rights and is a Class B misdemeanor.",
                "applies_to": "All plumbing work statewide",
                "source": "https://www.in.gov/pla/professions/plumbing-home/plumbing-licensing-information/"
            },
            {
                "title": "No statewide HVAC license \u2014 handled city-by-city",
                "note": "Indiana does NOT issue HVAC contractor licenses at the state level, unlike plumbing or electrical. HVAC licensing is delegated to individual cities and counties: Indianapolis/Marion County and Lake County run their own HVAC licensing programs, while many smaller jurisdictions impose no HVAC license at all. An Indianapolis HVAC license does not transfer to Lake County or vice versa \u2014 confirm the AHJ's local rule before quoting cross-jurisdictional work.",
                "applies_to": "HVAC contractors statewide",
                "source": "https://www.servicetitan.com/licensing/hvac/indiana"
            },
            {
                "title": "Indianapolis BNS contractor license categories",
                "note": "The Indianapolis Department of Business & Neighborhood Services (BNS) issues five contractor license categories: General, Electrical, Heating & Cooling (HVAC), Wrecking, and Plumbing. Electrical, HVAC, and Wrecking each require a BNS-issued credential in addition to any state license; the BNS plumbing license layers on top of the state PLA license. An expired BNS license blocks permit issuance and inspection scheduling even when the state license is current \u2014 pull the BNS lookup at quote time and again before final inspection.",
                "applies_to": "Paid construction work in Indianapolis / Marion County",
                "source": "https://www.indy.gov/activity/contractor-licenses"
            },
            {
                "title": "Many smaller Indiana cities require NO general contractor license",
                "note": "A meaningful number of Indiana jurisdictions \u2014 Richmond is the canonical example \u2014 require NO license for general contractors or homeowners performing residential work. Code compliance and zoning still apply, but there is no contractor registration to pull, no bond, and no exam. This is a common surprise for contractors crossing in from Illinois, Ohio, or Kentucky; absence of a license does NOT relieve the obligation to permit, inspect, or meet 675 IAC code, and homeowner-act exemptions still cap who may legally pull the permit.",
                "applies_to": "Smaller Indiana jurisdictions outside Marion / Lake County",
                "source": "https://www.richmondindiana.gov/resources/licensing"
            },
            {
                "title": "Indiana Energy Conservation Code \u2014 675 IAC 19",
                "note": "Indiana's commercial energy compliance is codified at 675 IAC 19; submitted designs must demonstrate they meet or exceed the Indiana Energy Conservation Code edition currently in force, with ComCheck or an approved whole-building energy model as the typical path. Residential energy compliance is handled inside the Indiana Residential Code (chapter 11 of 675 IAC 14) rather than 675 IAC 19. Indiana lags the IECC adoption curve \u2014 confirm whether the AHJ is on the 2010-baseline 675 IAC 19 or a more recent amendment cycle before sizing envelope assemblies.",
                "applies_to": "Commercial new construction and major alterations",
                "source": "https://d363m0o6saf7np.cloudfront.net/uploads/iacdocs/iac2019/T06750/A00190.PDF"
            },
            {
                "title": "Local Floodplain / Conservation Overlay District \u2014 separate from IDNR permit",
                "note": "Indiana counties and cities typically adopt a Conservation & Flood Plain Overlay (CFO) or Flood Hazard Overlay District in zoning that mirrors but does NOT replace the IDNR Construction in a Floodway permit. Development in the SFHA needs BOTH the local overlay zoning approval AND the state IDNR floodway permit when the parcel is in a regulated floodway. Treat them as two parallel filings on the schedule \u2014 local overlay variances are heard by the BZA, while the IDNR permit is reviewed by Division of Water staff in Indianapolis.",
                "applies_to": "Parcels mapped in a local Flood Hazard / Conservation Overlay",
                "source": "https://www.in.gov/counties/white/files/floodordinance2014.pdf"
            },
            {
                "title": "Solar PV / battery / generator interconnection is a SEPARATE utility filing",
                "note": "Net-metered, behind-the-meter, or parallel-operating generation requires a separate Interconnection Application with the serving electric utility \u2014 building permit approval does NOT authorize parallel operation. Investor-owned utilities (Duke, AES Indiana, NIPSCO, I&M, CenterPoint/Vectren) file under IURC-approved tariffs; municipal utilities (Pendleton, Anderson, Richmond, Lawrenceburg, etc.) run their own interconnection process. The utility will not energize the system until the signed interconnection agreement and witness test are complete, so sequence the utility filing alongside the building permit, not after.",
                "applies_to": "Solar PV, battery storage, standby generators, and other DG",
                "source": "https://www.town.pendleton.in.us/utility-office/files/application-interconnection-renewable-generation-facilities"
            },
            {
                "title": "Municipal vs IURC-regulated utility split changes interconnection scope",
                "note": "Indiana has roughly 70 municipal electric utilities that opted out of IURC interconnection oversight under IC 8-1.5; they set their own interconnection standards, application fees, metering rules, and net-metering caps by local ordinance and are NOT bound by the statewide IURC net-metering rule. Identify the serving utility from the meter or service address before quoting solar, EV-charger upgrades, or large standby loads \u2014 the scope of work, externally-disconnect requirement, and witness-test fee can swing materially between an IURC-regulated IOU and a municipal.",
                "applies_to": "Distributed generation and large-load interconnections",
                "source": "https://codelibrary.amlegal.com/codes/lawrenceburgin/latest/lawrenceburg_in/0-0-0-14245"
            },
            {
                "title": "Indianapolis permit review timing \u2014 published dashboard",
                "note": "The City of Indianapolis publishes current permit-application review times by trade and project type on the Indy.gov review-times dashboard. Residential permits typically run faster than commercial; small-residential and ADU plan review averages roughly 3 business days when the first submittal is complete. Check the dashboard before promising the homeowner a turn-on date \u2014 review queues stretch in spring/early summer and during code-cycle transitions, and the dashboard is the cleanest evidence to escalate a stale review.",
                "applies_to": "Indianapolis / Marion County permit applications",
                "source": "https://www.indy.gov/activity/permit-application-review-times"
            },
            {
                "title": "No statewide ADU statute or ministerial shot clock \u2014 ADU rules are LOCAL",
                "note": "Indiana has NO statewide ADU enabling statute, ministerial-approval mandate, or shot-clock equivalent to California's AB 881 / Govt Code 65852.2. Each city and county sets its own ADU zoning, parking, owner-occupancy, and review rules \u2014 many Indiana jurisdictions still treat ADUs as conditional or special uses requiring a BZA or Plan Commission hearing. Expect roughly 3 business days for plan-only review in larger jurisdictions, but build the schedule around a 30-60 day public-hearing track when the local ordinance does not allow ADUs by-right.",
                "applies_to": "Accessory Dwelling Units anywhere in Indiana",
                "source": "https://www.zookcabins.com/regulations/indiana-adu-regulations"
            }
        ]
    },
    "MD": {
        "name": "Maryland expert pack",
        "expert_notes": [
            {
                "title": "Maryland Building Performance Standards (MBPS) \u2014 statewide IBC/IRC adoption",
                "note": "Maryland requires every local jurisdiction to use the same edition of the IBC, IRC, and related I-codes under the Maryland Building Performance Standards. Local jurisdictions may amend most provisions to suit local conditions, but the IECC is adopted statewide and may NOT be locally weakened. Verify which edition the AHJ is currently enforcing before producing drawings \u2014 adoption timing varies by county.",
                "applies_to": "All residential and commercial construction statewide",
                "source": "https://labor.maryland.gov/labor/build/buildcodes.shtml"
            },
            {
                "title": "Maryland statewide IECC 2021 energy code (ASHRAE 90.1-2019)",
                "note": "Maryland's currently adopted energy code is the IECC 2021 with state amendments, referencing ASHRAE 90.1-2019 for commercial work. Unlike the building code, the IECC is enforced uniformly statewide and local jurisdictions cannot weaken its requirements. Plan-check rejection is common when COMcheck/REScheck or envelope U-values are submitted under an older code edition.",
                "applies_to": "New construction, additions, and alterations affecting the thermal envelope",
                "source": "https://ayerssaintgross.com/ideas/read/understanding-maryland-energy-requirements/"
            },
            {
                "title": "2024 I-code adoption underway \u2014 Howard County first",
                "note": "Howard County became the first Maryland jurisdiction to adopt the 2024 suite of I-codes, signaling the start of the next statewide MBPS update cycle. Other counties will follow on staggered effective dates over the next 12-24 months. Confirm the AHJ's adopted edition (and effective date) on every project \u2014 drawings sized to the wrong edition are a common cause of plan-check kickback.",
                "applies_to": "Permit applications during the 2024 I-code rollout window",
                "source": "https://www.facebook.com/HoCoGovExec/posts/howard-county-is-the-first-jurisdiction-in-maryland-to-adopt-the-2024-internatio/1417791613051678/"
            },
            {
                "title": "MHIC license required for residential home-improvement work",
                "note": "The Maryland Home Improvement Commission (MHIC) requires a license for anyone who performs, offers, or agrees to perform home-improvement work on residential properties of one to four units. Salespersons require a separate MHIC salesperson license. Working without an MHIC license voids contracts, blocks the contractor's lien rights, and exposes the contractor to disciplinary action and Guaranty Fund claims by the homeowner.",
                "applies_to": "All residential home-improvement contracting in Maryland (1-4 unit dwellings)",
                "source": "https://www.adaptdigitalsolutions.com/articles/maryland-contractor-license-requirements/"
            },
            {
                "title": "Trade licenses are state-issued and separate from MHIC",
                "note": "An MHIC home-improvement license does NOT authorize electrical, plumbing, gasfitting, or HVACR work \u2014 those require separate state board licenses (e.g., Maryland Board of HVACR Contractors for HVAC). A general contractor performing trade work must either hold the trade license or subcontract to a licensed trade. Plan to pull each trade permit under the correctly licensed entity; mismatched license/permittee names are a frequent rejection reason.",
                "applies_to": "Projects bundling general contracting with electrical, plumbing, gas, or HVAC scope",
                "source": "https://www.labor.maryland.gov/license/hvacr/"
            },
            {
                "title": "HVACR work requires separate state license + local permit",
                "note": "The Maryland Board of HVACR Contractors licenses individuals statewide, but local plumbing, gasfitting, and electrical permits are still required at the county or city level before HVACR work can begin. An HVAC permit is required for any HVAC installation in new construction and most alterations in residential occupancies. File the local permit under the state-licensed master's license number \u2014 county portals will reject applications submitted under expired or out-of-class licenses.",
                "applies_to": "HVAC installations, replacements, and alterations in residential occupancies",
                "source": "https://www.labor.maryland.gov/license/hvacr/hvacrcounty.shtml"
            },
            {
                "title": "Chesapeake Bay Critical Area \u2014 1,000 ft buffer review",
                "note": "Maryland's Critical Area law regulates development within 1,000 feet of tidal waters and tidal wetlands of the Chesapeake and Atlantic Coastal Bays. ALL private projects in the Critical Area \u2014 large or small, including individual building permits, additions, decks, and grading \u2014 require Critical Area review by the local planning department in addition to the standard building permit. Expect added review time, impervious-surface caps, and buffer/setback restrictions; budget for a Critical Area packet at intake.",
                "applies_to": "Any construction within 1,000 ft of tidal waters or tidal wetlands",
                "source": "https://dnr.maryland.gov/criticalarea/pages/development_in_cac.aspx"
            },
            {
                "title": "Resource Conservation Areas (RCAs) \u2014 strictest Critical Area tier",
                "note": "Inside the Critical Area, parcels designated Resource Conservation Area (RCA) face the most restrictive rules: local regulations limit new development, cap impervious surface, and require runoff and erosion controls to protect water quality. Intensely Developed Areas (IDA) and Limited Development Areas (LDA) are less restrictive but still trigger Critical Area review. Pull the parcel's Critical Area classification BEFORE design \u2014 RCA status can downsize an addition or block a new dwelling outright.",
                "applies_to": "Parcels mapped as RCA / LDA / IDA within the Chesapeake or Atlantic Coastal Bays Critical Area",
                "source": "https://dnr.maryland.gov/criticalarea/documents/other_resources/building%20in%20the%20critical%20area/becomingbaysmart_bca.pdf"
            },
            {
                "title": "Atlantic Coastal Bays Critical Area (Worcester County)",
                "note": "The 2002 Atlantic Coastal Bays Protection Act extended Critical Area-style rules to Worcester County's coastal bays watershed (Ocean City, Berlin, Snow Hill area), separate from the Chesapeake Bay program but operating on the same 1,000-foot buffer logic. Worcester County's local Critical Area ordinance (Bill 24-05 and successors) governs development standards, buffer modifications, and grandfathering. Projects in Worcester County near tidal water need the local Critical Area packet, not the Chesapeake DNR pathway.",
                "applies_to": "Worcester County parcels within the Atlantic Coastal Bays Critical Area",
                "source": "https://www.co.worcester.md.us/sites/default/files/2024-07/Bill%2024-05%20Signed%20Critical%20Area.pdf"
            },
            {
                "title": "No statewide ADU framework yet \u2014 local rules govern until Oct 1, 2026",
                "note": "Maryland does NOT currently have a statewide ADU shot clock or by-right approval law equivalent to California's AB 881. Under recent legislation, every local legislative body must adopt ADU-authorizing laws by October 1, 2026, after which statewide minimums kick in. Until then, ADU feasibility, setbacks, owner-occupancy, and parking rules are entirely a local-zoning question \u2014 pull the specific county or municipal ADU ordinance before quoting.",
                "applies_to": "ADU projects statewide before the Oct 1, 2026 statewide ADU deadline",
                "source": "https://www.dougpruettconstruction.com/blog/ADU-laws-and-permit-requirements-in-maryland--what-you-need-to-know"
            },
            {
                "title": "Local ADU adoption tracker \u2014 staggered rollout through 2026",
                "note": "By October 1, 2026, every Maryland local jurisdiction must authorize ADUs, but counties and municipalities are rolling out their enabling ordinances on different timelines, with statewide baseline requirements expected by year-end 2026. Some jurisdictions (e.g., Montgomery, Howard, Anne Arundel) already permit ADUs by-right under specific conditions; others still require special exception or variance. Confirm the specific local ordinance status at intake \u2014 quoting against the future statewide minimums before the local body adopts them is premature.",
                "applies_to": "ADU jobs in jurisdictions that have not yet adopted statewide-compliant ADU rules",
                "source": "https://www.zookcabins.com/regulations/maryland-adu-regulations"
            },
            {
                "title": "No statewide building-permit shot clock for residential",
                "note": "Maryland has no statewide ministerial shot clock for residential building permits \u2014 review timing is set by each AHJ. Montgomery County, for example, advises 4-6 weeks or longer for new residential building permits depending on complexity. State-level Maryland Department of the Environment timelines (e.g., 30 days for a General Permit to Construct, 90 days for Air Quality) apply to MDE permits, NOT local building permits. Set client expectations against the specific AHJ's published turnaround, not a state-level number.",
                "applies_to": "Residential permit timeline expectations statewide",
                "source": "https://www3.montgomerycountymd.gov/311/SolutionView.aspx?SolutionId=1-4WU20J"
            },
            {
                "title": "MDE permit shot clocks for state environmental permits",
                "note": "Maryland Department of the Environment publishes statutory turnaround targets for its own permits: 30 days for a General Permit to Construct, 90 days for an Air Quality Permit to Construct without expanded public review, and longer windows for water/wastewater discharge permits. These apply only to MDE-issued environmental permits triggered by site disturbance, stormwater, or air emissions \u2014 not to the local building permit. If MDE review is in the critical path (e.g., NPDES general construction permit for >1 acre disturbance), file in parallel with the local building permit, not after.",
                "applies_to": "Projects requiring MDE permits in addition to local building permits",
                "source": "https://mde.maryland.gov/programs/permits/pages/turnaroundtime.aspx"
            },
            {
                "title": "Dual permitting \u2014 county vs. incorporated municipality",
                "note": "Maryland operates a dual permitting system: in incorporated municipalities, both the city and the county may have jurisdiction over different aspects of the same project (e.g., zoning at the municipality, building inspection at the county, or vice versa). In Prince George's County, for example, projects inside an incorporated town may require permits from both the town and the county depending on scope. Confirm jurisdiction split at intake by calling both the municipal and county permit desks \u2014 assuming one or the other is a frequent cause of stop-work orders.",
                "applies_to": "Projects located inside incorporated municipalities in Maryland",
                "source": "https://www.princegeorgescountymd.gov/departments-offices/faq/municipalities-permitting-inspections-and-enforcement/what-types-permits-do-i-need-county-versus-municipality-project-located-municipality-prince"
            },
            {
                "title": "County Council building-permit authority under Land Use Article \u00a720-513",
                "note": "Maryland Land Use Code \u00a720-513 expressly authorizes the County Council to provide for issuance of permits for construction, repair, or remodeling of buildings. This is the statutory basis for county-level building departments and the rule-making behind local fee schedules and permit categories. When a permit is denied, the appeal path is typically the local Board of Appeals / Zoning Board, then circuit court \u2014 confirm the exact appeal process and deadline (often 30 days from denial) in the county code before recommending an appeal.",
                "applies_to": "Permit denials, variance requests, and appeals in Maryland counties",
                "source": "https://law.justia.com/codes/maryland/land-use/division-ii/title-20/subtitle-5/part-iii/section-20-513/"
            },
            {
                "title": "Montgomery County 2021 I-code effective Dec 10, 2024",
                "note": "Montgomery County adopted the 2021 International codes via Executive Regulation 13-24, with an effective date of December 10, 2024. Permit applications submitted before that date may have been reviewed under the 2018 codes; applications after must comply with the 2021 set including IECC 2021 envelope and mechanical requirements. When working in Montgomery County, confirm submission-date code edition with DPS \u2014 vested-rights questions are common at code-cycle boundaries.",
                "applies_to": "Permit applications in Montgomery County crossing the Dec 10, 2024 code-change boundary",
                "source": "https://www3.montgomerycountymd.gov/311/SolutionView.aspx?SolutionId=1-4WNWP5"
            }
        ]
    },
    "MO": {
        "name": "Missouri expert pack",
        "expert_notes": [
            {
                "title": "No statewide general / HVAC / plumbing / mechanical contractor license",
                "note": "Missouri does not issue a statewide general contractor, plumbing, HVAC, or mechanical contractor license \u2014 recurring legislation to create a statewide mechanical license has not passed. Licensing is handled at the municipal or county level, so a contractor licensed in Kansas City is NOT automatically licensed in St. Louis County, Springfield, or Columbia. Always verify the local AHJ's contractor registration roster before submitting a permit application; a missing local license is the #1 cause of intake rejection in Missouri.",
                "applies_to": "All trades except electrical contracting statewide",
                "source": "https://www.adaptdigitalsolutions.com/articles/missouri-contractor-license-requirements/"
            },
            {
                "title": "Statewide electrical contractor license \u2014 Office of Statewide Electrical Contractors",
                "note": "Missouri DOES have one statewide trade license: the Statewide Electrical Contractor License administered by the Division of Professional Registration's Office of Statewide Electrical Contractors. A statewide-licensed EC is recognized by any participating municipality without re-testing locally, but non-participating cities can still require their own local registration. Renew/apply via the MOPRO portal; expired licenses block permit issuance and void any resulting electrical inspection.",
                "applies_to": "Electrical contractors working across multiple Missouri jurisdictions",
                "source": "https://pr.mo.gov/electricalcontractors.asp"
            },
            {
                "title": "NASCLA Master/Unlimited Electrical exam path",
                "note": "Applicants for the Missouri Statewide Electrical Contractor License can satisfy the examination requirement using the NASCLA-accredited Master/Unlimited Electrical exam in lieu of taking a separate Missouri-specific test. This is the fastest path for out-of-state ECs already holding a NASCLA-accredited license. Confirm exam acceptance with the Office of Statewide Electrical Contractors before scheduling \u2014 the accreditation list changes.",
                "applies_to": "Out-of-state electrical contractors entering the Missouri market",
                "source": "https://mycontractorslicense.com/blog/obtaining-your-missouri-electrical-contractors-license-using-the-nascla-masterunlimited-electrical-exam-accreditation/?srsltid=AfmBOopyLg3FS2I2q9_nBkGFarkdSQyZz6LzmK842yIyBxpvL0Ce-j5f"
            },
            {
                "title": "Local trade-license menu \u2014 typical Missouri AHJ (St. Charles County model)",
                "note": "A typical Missouri AHJ requires SEPARATE licenses for electricians, plumbers, mechanical (HVAC) contractors, pool installers, drainlayers, third-party inspectors, and blasters \u2014 each with its own bond, insurance, and renewal cycle. Pulling a permit under the wrong trade classification (e.g., HVAC contractor pulling a gas-line-only job that requires a plumber/gasfitter) is a frequent rejection reason. Check the AHJ's licensed-contractor list before quoting and match the scope to the licensee's classification.",
                "applies_to": "Permitted work in counties/cities with local trade licensing",
                "source": "https://www.sccmo.org/1544/Contractor-Licensing-and-Renewals"
            },
            {
                "title": "No statewide residential or commercial energy code",
                "note": "Missouri has NOT adopted a statewide residential or commercial energy code \u2014 code adoption is entirely local, and unincorporated areas of many counties have no energy code at all. Do not assume IECC, Energy Star, or HERS compliance is required; verify with the specific AHJ. This is also why utility rebates that require code-plus performance often demand third-party verification rather than a code-stamped permit.",
                "applies_to": "All new construction and major remodels in Missouri",
                "source": "https://mostpolicyinitiative.org/science-note/building-energy-codes/"
            },
            {
                "title": "DNR jurisdiction-by-jurisdiction code lookup",
                "note": "Because adoption is local, the Missouri Department of Natural Resources publishes a code-by-jurisdiction lookup on the State of Missouri Data Portal listing the building, residential, and energy code edition currently in force in each city/county. Always pull this lookup before drawing \u2014 adjacent jurisdictions can be on different IBC/IRC/IECC editions (e.g., 2018 vs 2021 vs 2024) at the same time. Bookmark the DNR page and re-check at every project kickoff.",
                "applies_to": "Multi-jurisdiction Missouri portfolios and cross-city contractors",
                "source": "https://dnr.mo.gov/energy/efficiency/codes-jurisdiction"
            },
            {
                "title": "Kansas City 2021 IECC + 2026 amendments rollback",
                "note": "Kansas City adopted the 2021 IECC effective July 1, 2023 with a 90-day grace period, then on Feb 5, 2026 the City Council passed amendments that ROLL BACK portions of the energy code \u2014 loosening insulation, relaxing energy-efficiency requirements for new homes. For KC permits applied for after the rollback effective date, design to the amended (less stringent) envelope, but confirm whether the project was vested under the pre-amendment 2021 IECC at intake.",
                "applies_to": "New residential construction inside Kansas City limits",
                "source": "https://www.nahb.org/blog/2026/02/kansas-city-2021-iecc-amendments"
            },
            {
                "title": "Columbia 2024 IBC/IRC adoption \u2014 public-notice window",
                "note": "The City of Columbia is moving from the 2018 to the 2024 I-Codes; the required 90-day public-notice period began Nov 17, 2025, with adoption expected shortly after. Permits applied for during the notice window may still be reviewed under 2018, but resubmittals/revisions after adoption will likely be bumped to 2024. Time large Columbia submittals to lock in the 2018 edition or budget extra cycles to upgrade drawings to 2024 fire-safety and energy provisions.",
                "applies_to": "Columbia, MO building permits crossing the 2024 I-Code adoption boundary",
                "source": "https://beheard.como.gov/building-code-adoption?tool=brainstormer"
            },
            {
                "title": "Proposed 30/60-day permit shot clock \u2014 HB 17-91 (2026 session)",
                "note": "Missouri does NOT currently have a statewide permit shot clock. House Committee Substitute for HB 17-91 (2026 legislature) would require local issuance of building permits within 30 days for typical lots and 60 days for larger tracts, with an appeals path. Until the bill is enacted, escalation for slow review must rely on local ordinance timelines or mandamus \u2014 track HB 17-91 status before promising clients a fixed turnaround.",
                "applies_to": "Residential permit-timing expectations statewide",
                "source": "https://citizenportal.ai/articles/7768686/missouri/2026-legislature-mo/Missouri/2026-Legislature-MO/Permit-timelines-proposed-to-speed-housing-construction-amendment-adopted"
            },
            {
                "title": "RSMo 137.177 \u2014 building permits in second-class counties",
                "note": "Revised Statutes of Missouri \u00a7137.177 governs building-permit application, fee, and issuance in second-class counties and includes a list-to-assessor requirement; failure to file is a misdemeanor. This is the statutory hook for property-tax discovery via the building-permit process \u2014 assessors receive permit lists from the AHJ, so unpermitted improvements in second-class counties carry both code-enforcement and tax-fraud exposure. Confirm the county classification (1st, 2nd, 3rd, 4th class) before advising owners on permit-skip risk.",
                "applies_to": "Construction in Missouri second-class counties",
                "source": "https://revisor.mo.gov/main/OneSection.aspx?section=137.177"
            },
            {
                "title": "ADUs \u2014 no state preemption, county-by-county zoning",
                "note": "Missouri has NO state-level ADU enabling statute analogous to California AB 881 or Oregon SB 1051 \u2014 ADU legality, size caps, owner-occupancy, and parking rules are set entirely by local zoning. Some cities (St. Louis, Kansas City, Columbia) permit ADUs under specific overlays; many suburban and rural counties prohibit them outright or treat them as a second principal dwelling triggering subdivision review. Pull the local zoning code AND the parcel's overlay before quoting an ADU.",
                "applies_to": "Accessory dwelling unit projects statewide",
                "source": "https://www.zookcabins.com/regulations/adu-regulations-in-missouri"
            },
            {
                "title": "Floodplain Development Permit \u2014 SEMA + local NFIP coordinator",
                "note": "Any development in a SFHA (Special Flood Hazard Area) requires a separate Floodplain Development Permit filed with the local NFIP-participating community's floodplain administrator, in addition to the building permit. The SEMA application requires as-built certification by a registered engineer, architect, or land surveyor of the lowest-floor elevation. Skipping the FDP is the most common reason FEMA suspends a community from the NFIP \u2014 and triggers personal liability for the property owner.",
                "applies_to": "Construction in FEMA-mapped floodplains in NFIP communities",
                "source": "https://sema.dps.mo.gov/programs/floodplain/documents/floodplain-develoment-permit.pdf"
            },
            {
                "title": "FEMA NFIP Historic Structure exemption",
                "note": "FEMA NFIP regulations allow Historic Structures (listed on the National Register, contributing to a National Register district, or on a certified state/local inventory) to be exempt from substantial-improvement elevation requirements, provided the work does NOT cause loss of historic designation. This is significant relief in Missouri's older river-town building stock (Hermann, Ste. Genevieve, Kimmswick). Document the historic determination in writing from the SHPO before relying on the exemption.",
                "applies_to": "Substantial improvements to historic structures in Missouri floodplains",
                "source": "https://sema.dps.mo.gov/programs/floodplain/documents/nfip-historic-structures.pdf"
            },
            {
                "title": "Solar PV \u2014 layered permit + utility interconnection (parallel filings)",
                "note": "Missouri residential solar requires THREE parallel filings: (1) local building/electrical permit with the AHJ, (2) utility interconnection application with the serving electric utility (Evergy, Ameren Missouri, or the local municipal/cooperative), and (3) final inspection by the municipal electrical inspector AND a witness test by the utility before PTO. Net-metering eligibility is governed by the utility's tariff, not the building department. Submit interconnection in parallel with the permit \u2014 sequential filing typically adds 4\u20138 weeks.",
                "applies_to": "Residential and small-commercial solar PV in Missouri",
                "source": "https://missourisolarauthority.com/permitting-and-inspection-concepts-for-missouri-solar-energy-systems"
            },
            {
                "title": "Microgrid / DER interconnection \u2014 PSC oversight + dual sign-off",
                "note": "Microgrid and larger DER (battery + PV, generator paralleling) interconnections in Missouri require approval from the Municipal Electrical Inspector AND a witness test from the serving utility, with PSC tariff oversight for investor-owned utilities. Cooperative and municipal utility customers fall outside PSC jurisdiction and must follow that utility's individual interconnection manual. For battery storage paralleled with the grid, expect anti-islanding documentation and UL 1741-SB inverter listings as part of the interconnection package.",
                "applies_to": "Battery storage, microgrids, and standby generators with grid paralleling",
                "source": "https://www.efis.psc.mo.gov/Document/Display/184715"
            },
            {
                "title": "HVAC mechanical permit \u2014 local trigger thresholds vary",
                "note": "Whether HVAC work requires a permit in Missouri depends entirely on the local code: replacement-in-kind of like-tonnage equipment is permit-exempt in some unincorporated areas but requires a mechanical permit + load calculation in Kansas City, St. Louis, Springfield, Columbia, and most St. Louis County municipalities. Manual J/D/S sizing is increasingly required for new ductwork or condenser changeouts in cities on the 2018+ IRC. Confirm the local mechanical-permit trigger before quoting a 'simple' changeout.",
                "applies_to": "HVAC equipment replacement and new mechanical installations",
                "source": "https://missourihvacauthority.com/missouri-hvac-permit-requirements"
            }
        ]
    },
    "WI": {
        "name": "Wisconsin expert pack",
        "expert_notes": [
            {
                "title": "Wisconsin Uniform Dwelling Code (SPS 320-325) governs all 1-2 family dwellings",
                "note": "Wisconsin's UDC (SPS 320-325) is the statewide building code for one- and two-family dwellings built since June 1, 1980 \u2014 there is no separate municipal residential code that can override it. Unlike most states, Wisconsin uses a hybrid residential code rather than a straight IRC adoption, so plans drafted to IRC-only standards will be flagged. Confirm UDC compliance for framing, energy (SPS 322), plumbing, and electrical chapters before submitting.",
                "applies_to": "All new and altered 1-2 family dwellings statewide",
                "source": "https://dsps.wi.gov/Pages/Programs/UDC/Default.aspx"
            },
            {
                "title": "10 business-day shot clock for UDC building permit decisions",
                "note": "Per SPS 320.09(8)(a), the AHJ must approve or deny a uniform building permit application within 10 business days of receiving all complete forms, fees, plans, and documents. If the municipality blows the deadline without identifying missing items, escalate to the building inspector's supervisor and document the complete-application date in writing. This is far tighter than commercial plan review (typically 6 weeks at DSPS).",
                "applies_to": "1-2 family dwelling permit applications under the UDC",
                "source": "https://docs.legis.wisconsin.gov/document/administrativecode/SPS%20320.09(8)(a)"
            },
            {
                "title": "2021 ICC code transition effective October 1, 2025",
                "note": "Wisconsin transitioned to the 2021 International Code Council model codes on October 1, 2025 after the governor's veto saga; the commercial building code went into effect September 1, 2025. Plans submitted under the prior code edition are being accepted through approximately Nov 1, 2025 per current DSPS guidance \u2014 confirm which edition your AHJ is enforcing on the application date, especially for energy and structural provisions that changed materially.",
                "applies_to": "Permit applications crossing the 2025 code-change boundary",
                "source": "https://iibec.org/wisconsin-pushes-ahead-with-new-building-codes-after-governors-veto/"
            },
            {
                "title": "DSPS Dwelling Contractor + Dwelling Contractor Qualifier licenses required to pull permits",
                "note": "Per SPS 305, anyone contracting to build or substantially remodel a 1-2 family dwelling in Wisconsin must hold both a Dwelling Contractor certification (the business credential) and employ at least one Dwelling Contractor Qualifier (the individual who passed the exam). Plumbing contractors additionally need a master-level plumber on staff. Verify both credentials in DSPS LicensE before signing \u2014 municipalities will refuse to issue permits to unlicensed dwelling contractors.",
                "applies_to": "Paid contractors performing work on 1-2 family dwellings",
                "source": "https://dsps.wi.gov/Pages/Professions/DwellingContractor/Default.aspx"
            },
            {
                "title": "Separate state-issued trade licenses for electrical, plumbing, and HVAC",
                "note": "DSPS licenses contractors by trade: Dwelling Contractor, Electrical, Plumbing, HVAC, Fire Protection, and Asbestos/Lead Abatement are each separate credentials. A general Dwelling Contractor license does NOT authorize trade work \u2014 the electrical, plumbing, and HVAC scopes must each be performed by (or supervised by) a separately licensed master in that trade. All renewals must now flow through the LicensE online platform.",
                "applies_to": "All trade work on residential and commercial projects",
                "source": "https://contractorlicensinginc.com/national-contractor-licensing/wisconsin-contractor-license/"
            },
            {
                "title": "Owner-builder exemption \u2014 no Dwelling Contractor license needed for self-performed work",
                "note": "If the property owner is listed as the general contractor on a project on their own home, no Dwelling Contractor license is required, but the owner must sign the state Cautionary Statement acknowledging they are personally responsible for code compliance, worker's comp, and warranty obligations. This is a common path for homeowner ADUs and additions \u2014 but the moment a paid sub steps in for a regulated trade, that sub still needs the trade license.",
                "applies_to": "Owner-occupants self-permitting work on their primary residence",
                "source": "https://www.kenosha.org/Document%20Center/Departments/City%20Inspection/Building%20Inspection/Permits%20and%20Applications/LicensingRequirements.pdf"
            },
            {
                "title": "Municipality-vs-county AHJ split \u2014 UDC enforcement is delegated locally",
                "note": "Wisconsin permit jurisdiction varies: incorporated cities and villages typically run their own building inspection departments, while unincorporated towns may rely on the county or contract with a certified UDC inspector. Per SPS 361.60(5)(f)1.c., the building permit application must be included with plans submitted to whichever municipality or county has jurisdiction. Always confirm whether the parcel is inside an incorporated boundary before assuming the city issues the permit.",
                "applies_to": "All projects \u2014 confirm AHJ before drafting submittal package",
                "source": "https://docs.legis.wisconsin.gov/document/administrativecode/SPS%20361.60(5)(f)1.c."
            },
            {
                "title": "DNR Chapter 30 waterway permit \u2014 separate filing from building permit",
                "note": "Construction within or adjacent to navigable waters (including ponds within 500 ft of a navigable waterway, dredging, riprap, piers, or wetland fill) requires a separate Chapter 30 waterway permit from the Wisconsin DNR \u2014 this is a parallel filing, not part of the building permit. Even projects that DNR exempts may still need floodplain and shoreland approvals from the local zoning office. Apply via the DNR Waterway Permit Application portal before breaking ground.",
                "applies_to": "Work in or near navigable waters, wetlands, or shorelands",
                "source": "https://dnr.wisconsin.gov/topic/Waterways/Permits/PermitProcess.html"
            },
            {
                "title": "County-administered shoreland zoning \u2014 75 ft / 1000 ft / 300 ft setbacks",
                "note": "Wisconsin's shoreland zoning program is delegated to counties (e.g., Brown, Dane), which regulate areas within 1,000 ft of a navigable lake/pond/flowage and 300 ft of a navigable river/stream (or to the floodplain edge, whichever is greater). Waters shown on USGS quadrangle maps or county GIS are presumed navigable until disproven. Shoreland setback, impervious-surface, and vegetation-buffer rules can disqualify ADUs and additions that the underlying zoning would otherwise allow.",
                "applies_to": "Parcels within county shoreland jurisdiction",
                "source": "https://danecountyplanning.com/Zoning/Water-Resources/Shoreland-Zoning-FAQ"
            },
            {
                "title": "Floodplain zoning is a parallel overlay \u2014 Chapter 30 alone is not enough",
                "note": "Even when a DNR Chapter 30 waterway permit is issued, the project still needs floodplain zoning approval from the local zoning office if any portion sits within the regional flood or base flood as shown on the FIRM. Per the state floodplain chapter, a copy of the approved Chapter 30 permit must accompany the local floodplain submittal. Treat floodplain, shoreland, and Chapter 30 as three distinct approvals that may all apply to a single waterfront project.",
                "applies_to": "Parcels in mapped floodplains or shoreland-floodplain overlays",
                "source": "https://www.floods.org/koha?id=6143"
            },
            {
                "title": "Wetland permitting + 2026 WiWRAM assessment rollout",
                "note": "Wetland fill, drain, or grading triggers DNR wetland permitting; identification and delineation must be completed before submittal, and statutory exemptions are narrow. DNR plans to begin accepting Wisconsin Wetland Rapid Assessment Method (WiWRAM) reports for permitting starting the 2026 growing season \u2014 projects mobilizing in spring 2026 should ask the DNR coordinator which assessment format is preferred to avoid resubmittals.",
                "applies_to": "Projects disturbing mapped or field-identified wetlands",
                "source": "https://dnr.wisconsin.gov/topic/Wetlands/permits"
            },
            {
                "title": "No statewide ADU shot-clock or ministerial pathway",
                "note": "Unlike states with statewide ADU mandates, Wisconsin has no uniform ADU statute imposing a ministerial shot clock, lot-size override, or owner-occupancy waiver \u2014 ADUs are treated as ordinary additions or accessory structures under local zoning plus the UDC. Expect a standard zoning review (sometimes including a conditional-use hearing) before the 10-business-day UDC permit clock starts. Contact the county zoning administrator first to confirm whether ADUs are even a permitted use in the district.",
                "applies_to": "ADU and accessory dwelling projects statewide",
                "source": "https://www.zookcabins.com/regulations/wi-adu-regulations"
            },
            {
                "title": "UDC SPS 322 energy provisions \u2014 Wisconsin-specific, not straight IECC",
                "note": "Wisconsin's residential energy requirements live in SPS 322 of the UDC, which is a Wisconsin-amended standard rather than a clean IECC adoption \u2014 historically the state stayed on '09 IECC for residential and '15 IECC for commercial, with state-specific tweaks layered in through the 2025 update. Energy compliance documentation must be on the SPS 322 forms; submitting only a generic IECC REScheck without the state worksheet is a frequent rejection reason.",
                "applies_to": "New construction and conditioned-space alterations on 1-2 family dwellings",
                "source": "https://www.energycodes.gov/status/states/wisconsin"
            },
            {
                "title": "Municipal electrical license overlay \u2014 state license alone may not be enough",
                "note": "Some Wisconsin municipalities (e.g., Wauwatosa) require electrical contractors to hold BOTH a valid State of Wisconsin contractor's license AND a Master Electrician's credential before pulling local permits, and may require a separately registered local business license. Always check the city's contractor-registration list in addition to DSPS LicensE \u2014 a state-licensed electrician from out of town can still be turned away at the counter.",
                "applies_to": "Electrical work in municipalities with local registration overlays",
                "source": "https://www.wauwatosa.net/government/departments/building-safety/license-certification-requirements"
            }
        ]
    },
    "MN": {
        "name": "Minnesota expert pack",
        "expert_notes": [
            {
                "title": "Minnesota 60-day rule (Statute 15.99) for zoning/permit decisions",
                "note": "Minnesota Statutes section 15.99 requires local land use authorities (cities, counties, townships) to approve or deny a written request relating to zoning within 60 days of receiving a complete application. Failure to act within the 60-day window is automatic approval by operation of law. The agency may extend once for an additional 60 days only by written notice before the original deadline expires \u2014 track the receipt date and the extension letter carefully.",
                "applies_to": "All zoning and land-use applications including ADUs, variances, CUPs, and rezonings",
                "source": "https://www.larkinhoffman.com/real-estate-construction-blog/what-happens-to-minnesotas-zoning-shot-clock-during-a-peacetime-emergency"
            },
            {
                "title": "60-Day Rule extension procedure and written denial requirement",
                "note": "Under the Minnesota 60-day rule, an agency that intends to deny must do so in writing with reasons stated at the time of denial; a verbal denial or silence past the deadline operates as approval. The statute applies broadly to written requests relating to zoning, septic systems, watershed district reviews, soil and water conservation district reviews, and county water management \u2014 not just building permits. Always submit applications in writing with date-stamped delivery to start the clock cleanly.",
                "applies_to": "Denied or stalled zoning/permit applications where written denial is missing",
                "source": "https://www.house.mn.gov/hrd/pubs/ss/ss60day.pdf"
            },
            {
                "title": "Residential building contractor license through DLI (not a state-issued GC)",
                "note": "Minnesota Department of Labor and Industry (DLI) issues residential building contractor, remodeler, and roofer licenses for anyone contracting directly with a homeowner for work in 2 or more of the special skill categories (excavation, masonry/concrete, carpentry, interior finishing, exterior finishing, drywall/plaster, residential roofing, residential siding, general installation specialties, or roof coverings). Apply or renew through DLI's iMS online system. Operating without the license when one is required exposes the contractor to administrative penalties and rescission rights for the homeowner.",
                "applies_to": "Residential remodelers and builders contracting directly with homeowners",
                "source": "https://www.dli.mn.gov/business/residential-contractors/residential-contractor-licensing"
            },
            {
                "title": "Plumbing contractor license + bond (DLI Plumbing Board)",
                "note": "Minnesota requires a state-issued plumbing contractor license through DLI before pulling a plumbing permit; the contractor must employ a licensed master plumber in responsible charge and post the required contractor bond. Pipe layer endorsements, journeyworker/restricted licenses, and continuing education are tracked separately. Verify the license is active on DLI's lookup before quoting \u2014 an expired or suspended license blocks permit issuance and inspections.",
                "applies_to": "All plumbing work requiring a permit statewide",
                "source": "https://www.dli.mn.gov/business/plumbing-contractors/licensing-plumbing-contractor-licenses"
            },
            {
                "title": "HVAC has no state license but requires a $25,000 DLI bond + local competency",
                "note": "Minnesota does not issue a statewide HVAC contractor license, but a $25,000 bond filed with DLI is required to perform mechanical work, and many cities (Minneapolis, St. Paul, Bloomington, Duluth) require a local competency card or exam before issuing mechanical permits. EPA 608 refrigerant certification is required federally. Confirm the local AHJ's competency requirements separately from the DLI bond before bidding HVAC work in any new jurisdiction.",
                "applies_to": "HVAC/mechanical contractors statewide",
                "source": "https://www.servicetitan.com/licensing/hvac/minnesota"
            },
            {
                "title": "HVAC permits in multi-unit rental properties must be pulled by a bonded HVAC contractor",
                "note": "An HVAC contractor properly bonded with the State of Minnesota must take out any HVAC permit for work being done in a multi-unit rental property \u2014 the homeowner-pulled permit option does not apply. This is enforced by AHJs at permit issuance and is a frequent rejection reason for owners trying to self-permit on duplex/triplex/4-plex jobs. Plan for the bonded contractor to be the named permit applicant on any rental mechanical work.",
                "applies_to": "Mechanical/HVAC work in 2+ unit rental properties",
                "source": "https://www.ighmn.gov/DocumentCenter/View/17736/When-Am-I-Required-to-Hire-a-Licensed-Contractor"
            },
            {
                "title": "Minnesota Residential Code = IRC 2018 with amendments (MN Rules Ch. 1309)",
                "note": "Minnesota adopts the IRC by reference under Minnesota Rules Chapter 1309 with state-specific amendments \u2014 the current cycle is the 2018 IRC adopted as the 2020 Minnesota Residential Code. References to 'IRC' in Minnesota mean the Minnesota Residential Code as amended, not the unmodified ICC text. Do not design to a stock IRC edition; pull Chapter 1309 amendments and the matching Minnesota Energy Code (Chapters 1322 and 1323) before submitting drawings.",
                "applies_to": "All 1- and 2-family residential construction statewide",
                "source": "https://www.revisor.mn.gov/rules/1309/full"
            },
            {
                "title": "2020 Minnesota State Building Code effective date and pending energy code update",
                "note": "The 2020 Minnesota State Building Code became effective March 31, 2020 (Mechanical Fuel Gas Code April 6, 2020) and remains the active code edition. A new residential energy code update is in process via TAG negotiation but is not expected to be finalized until 2027 \u2014 until then, design to Chapters 1322 (commercial energy) and 1323 (residential energy) as currently published. Confirm the effective code edition with the AHJ on long-running projects whose permits may straddle a future adoption.",
                "applies_to": "Permit applications and energy compliance documentation statewide",
                "source": "https://www.dli.mn.gov/business/codes-and-laws/2020-minnesota-state-building-codes"
            },
            {
                "title": "Statewide building code preemption \u2014 local AHJs cannot amend technical provisions",
                "note": "Minnesota is one of only eight states that does not permit local jurisdictions to amend any portion of the state building code; the code is uniform statewide and locals cannot add stricter technical requirements through ordinance. This means a contractor licensed and trained on Chapter 1309 will see the same technical code in Bemidji as in Minneapolis \u2014 but local administrative procedures, permit fees, and zoning still vary. Push back if an AHJ tries to enforce a 'local amendment' to the technical building code.",
                "applies_to": "Disputes over locally-imposed building code technical requirements",
                "source": "https://www.auditor.leg.state.mn.us/ped/1999/sbc99.htm"
            },
            {
                "title": "AHJ split \u2014 not all Minnesota jurisdictions enforce the State Building Code",
                "note": "Minnesota's State Building Code is mandatory in the 7-county metro and in cities/counties that have opted in, but some rural townships and counties have NOT adopted the code, meaning building permits are not issued there for 1-2 family dwellings. Use DLI's Local Code Lookup tool to confirm which authority (city, county, or none) handles permits for a specific parcel before quoting. Zoning authority is separately split between city and county per Minnesota Statutes Chapter 462 \u2014 a project may need zoning approval from one authority and building permit from another.",
                "applies_to": "Confirming which AHJ has building/zoning jurisdiction over a parcel",
                "source": "https://workplace.doli.state.mn.us/jurisdiction/"
            },
            {
                "title": "Wetland Conservation Act (MN Rules Ch. 8420) \u2014 separate filing from building permit",
                "note": "The Wetland Conservation Act regulates wetlands not classified as Public Waters and is administered by the local Soil and Water Conservation District (SWCD), separate from the building permit. Any grading or filling within a wetland \u2014 including for an addition, driveway, or accessory structure \u2014 must meet WCA standards under Minnesota Rules Chapter 8420 and may require a sequencing/replacement plan. Identify wetlands on the parcel during site planning; an after-the-fact WCA violation can force restoration and block CO.",
                "applies_to": "Sites with wetlands, low areas, or hydric soils",
                "source": "https://www.dnr.state.mn.us/wetlands/regulations.html"
            },
            {
                "title": "DNR Public Waters Work Permit \u2014 required for work in lakes, streams, and listed wetlands",
                "note": "The DNR Public Waters Work Permit Program applies to lakes, wetlands, and streams identified on DNR Public Water Inventory maps \u2014 riprap, docks beyond exemptions, shoreline alterations, bridges, culverts, and dredging in these waters require a DNR permit in parallel with the local building permit. Also check State Shoreland Standards if the parcel is within a shoreland zone, which impose setback, impervious-surface, and vegetation rules. Pull the DNR PWI map for the parcel before designing any waterfront or near-water work.",
                "applies_to": "Construction within or adjacent to DNR-inventoried public waters or shoreland zones",
                "source": "https://www.dnr.state.mn.us/waters/watermgmt_section/pwpermits/requirements.html"
            },
            {
                "title": "Distributed energy interconnection \u2014 separate utility filing from building permit (PUC MN DIP)",
                "note": "Solar PV, battery storage, and other distributed energy resources require an interconnection application with the serving utility under the Minnesota Distributed Energy Resource Interconnection Process overseen by the Public Utilities Commission, in addition to the local electrical/building permit. The DER cannot energize until the signed interconnection agreement is in place and any required utility-side construction is complete. Submit the interconnection application early \u2014 utility review can run weeks longer than the building permit and is often the gating item for PTO.",
                "applies_to": "Solar PV, battery storage, generators, and other DER installations",
                "source": "https://mn.gov/puc/energy/distributed-energy/interconnection/"
            },
            {
                "title": "Municipal utility interconnection runs on a separate process from investor-owned utilities",
                "note": "Minnesota has ~125 municipal electric utilities (Anoka, Chaska, Granite Falls, Rochester, etc.) that operate their own interconnection processes outside of the PUC-regulated Xcel/Minnesota Power/Otter Tail framework. Muni processes typically require a separate application fee (e.g., $100 in Anoka), their own application forms, and direct coordination with the city \u2014 not the state portal. Identify the serving utility before quoting a solar/battery job; muni-served addresses cannot use Xcel's online interconnection portal.",
                "applies_to": "Solar/battery/DER installs in municipal-utility service territories",
                "source": "https://anokamunicipalutility.com/752/Simplified-Interconnection-Process"
            },
            {
                "title": "Permit vesting clock \u2014 work must commence and continue or the permit expires",
                "note": "Once a Minnesota building permit is issued, the clock starts immediately on the permit's life \u2014 work must begin within the AHJ's stated window (commonly 180 days) and continue without lapse for more than the maximum inactive period (commonly 180 days), or the permit becomes void and a new permit/fees are required. This is a common gotcha when homeowners pull their own permits and contractors drag the start date. Have the contractor pull the permit so the inspection schedule is owned by the party doing the work.",
                "applies_to": "Permit holders who delay start or have gaps between inspections",
                "source": "https://www.facebook.com/PPRegionalBuilding/posts/did-you-know-when-you-pull-a-permit-the-clock-immediately-starts-ticking-on-that/599787157335511/"
            }
        ]
    },
    "SC": {
        "name": "South Carolina expert pack",
        "expert_notes": [
            {
                "title": "South Carolina 2021 I-Codes adoption \u2014 statewide mandatory codes",
                "note": "The South Carolina Building Codes Council adopted the 2021 editions of the IBC, IRC, IPC, IMC, IFGC, IFC, and IEBC at its October 6, 2021 meeting, with South Carolina-specific modifications. These are the mandatory statewide codes \u2014 local jurisdictions cannot adopt earlier or later editions on their own, and any local amendment requires Council approval. Confirm which 2021 code edition (and SC modifications) applies to your scope before stamping drawings, since SC modifications routinely strip or alter ICC chapters.",
                "applies_to": "All residential and commercial permit applications statewide",
                "source": "https://llr.sc.gov/bcc/BCAdoption.aspx"
            },
            {
                "title": "South Carolina energy code is locked to 2009 IECC",
                "note": "Unlike the building/residential code cycle, South Carolina's energy conservation code is statutorily fixed to the 2009 edition of the IECC (per the 2025-2026 H.5216 codification of existing practice). Newer IECC editions do NOT apply unless and until the SC Building Codes Council formally adopts them. Do not over-spec envelope, fenestration U-values, or duct leakage to 2018/2021 IECC numbers \u2014 plan check will reject for non-conformance with the actual 2009 baseline, and HERS/blower-door thresholds from newer editions are not enforceable.",
                "applies_to": "Energy compliance documentation for new construction and additions",
                "source": "https://www.billtrack50.com/billdetail/1975513"
            },
            {
                "title": "Residential Builders Commission vs Contractor's Licensing Board \u2014 two separate boards",
                "note": "South Carolina splits contractor regulation between two LLR boards: the Residential Builders Commission (residential 1-2 family, specialty residential, home inspectors) at llr.sc.gov/res, and the Contractor's Licensing Board (general contractors, mechanical contractors, construction managers \u2014 typically commercial and projects > $5,000) at llr.sc.gov/clb. Pulling residential and commercial permits requires the right board's credential \u2014 a CLB general contractor license does not authorize 1-2 family residential work, and vice versa. Verify the correct board before quoting.",
                "applies_to": "All paid construction work statewide",
                "source": "https://llr.sc.gov/clb/"
            },
            {
                "title": "Residential Builder license required \u2014 and registration threshold",
                "note": "A current South Carolina Residential Builder license or Specialty Contractor registration is required to engage in residential building, specialty contracting, or home inspecting in the state. Operating without the proper credential exposes the contractor to disciplinary action and voids lien rights. Verify the license at verify.llronline.com before signing a contract \u2014 expired or suspended licenses are common rejection reasons on permit applications.",
                "applies_to": "Residential 1-2 family construction and specialty trades",
                "source": "https://llr.sc.gov/res/licensure.aspx"
            },
            {
                "title": "HVAC contractor \u2014 $10,000 surety bond requirement",
                "note": "After passing the Residential HVAC Contractor exam, applicants must submit test results plus a $10,000 surety bond when the total cost of work exceeds the licensing board threshold. The bond is a hard prerequisite for issuance \u2014 do not promise a job start date until the bond is on file with LLR. License classifications and the bond requirement are administered by LLR's Residential Builders Commission.",
                "applies_to": "Residential HVAC contractor licensure and renewals",
                "source": "https://www.servicetitan.com/licensing/hvac/south-carolina"
            },
            {
                "title": "Contractor license renewal cycle \u2014 General vs Mechanical staggered",
                "note": "All South Carolina General Contractor licenses expire October 31 in even-numbered years; all Mechanical Contractor licenses expire October 31 in odd-numbered years. Renewals are not aligned across trades \u2014 calendar each separately to avoid an expired-license lapse that blocks permit issuance. An expired license at the moment of permit application is an automatic rejection and can also void contract enforceability.",
                "applies_to": "CLB-licensed general and mechanical contractors statewide",
                "source": "https://llr.sc.gov/clb/clb_licensure.aspx"
            },
            {
                "title": "Local government permit shot clock \u2014 pending statute (H.3215)",
                "note": "South Carolina H.3215 (2025-2026 session) proposes adding S.C. Code \u00a76-1-200 to require local planning and permitting entities to review and act on applications within a defined timeframe. As of permit submittal, confirm whether the bill has been enacted and what the statutory clock and remedies are \u2014 until enactment, there is no statewide ministerial shot clock for residential permits, and review timelines are set entirely by each AHJ. Do not promise clients a state-mandated turnaround.",
                "applies_to": "Local building/zoning permit reviews statewide",
                "source": "https://www.scstatehouse.gov/sess126_2025-2026/bills/3215.htm"
            },
            {
                "title": "AHJ split \u2014 municipal jurisdiction can extend into unincorporated county",
                "note": "Under S.C. Code Title 6, Chapter 29, unincorporated areas of a county adjacent to an incorporated municipality may be added to and included in the area under municipal planning and permitting jurisdiction by interlocal agreement. Do not assume that a parcel just outside city limits is regulated by the county \u2014 confirm in writing with both the city and county building departments which AHJ has plan-review and inspection authority before submitting, since a wrong-AHJ submittal restarts the clock and re-runs fees.",
                "applies_to": "Parcels near municipal boundaries; annexed/extraterritorial areas",
                "source": "https://www.scstatehouse.gov/code/t06c029.php"
            },
            {
                "title": "Local Building Code Enforcement Officers required at every AHJ",
                "note": "Per the SC Building Department Manual, every local jurisdiction is required to have certified Building Code Enforcement Officers (Building Official, Plan Reviewer, and Inspectors). Plan review and inspection decisions are made at the local AHJ by these certified staff \u2014 there is no state-level plan review for typical residential work. Address objections to the local Building Official first; appeals beyond that go to the local Board of Appeals, then the SC Building Codes Council.",
                "applies_to": "Plan-review disputes, inspection failures, and code-interpretation appeals",
                "source": "https://www.ibcode.com/wp-content/uploads/2022/10/SC-BO-manual-final.pdf"
            },
            {
                "title": "Modular buildings \u2014 state-level approval, not local plan review",
                "note": "Under S.C. Code Regs. \u00a78-604, the design and fabrication of modular buildings must comply with the building codes listed in Chapter 9, Title 6, and approval is administered through the SC Building Codes Council / Modular Buildings program \u2014 NOT through local plan review of the module itself. Locals retain jurisdiction over the foundation, site work, utility connections, and final setting inspection. Do not submit module structural drawings to the local AHJ \u2014 submit the foundation/site package locally and reference the state modular label.",
                "applies_to": "Factory-built modular residential and commercial structures",
                "source": "https://www.law.cornell.edu/regulations/south-carolina/R-8-604"
            },
            {
                "title": "OCRM Critical Area Permit \u2014 separate filing in 8 coastal counties",
                "note": "SCDES Bureau of Coastal Management (formerly OCRM) has direct permitting authority within the coastal critical areas (tidelands, beaches, coastal waters) and indirect certification authority across the 8 coastal counties (Beaufort, Berkeley, Charleston, Colleton, Dorchester, Georgetown, Horry, Jasper). Any work seaward of the OCRM critical line requires a separate Critical Area Permit in addition to the local building permit, and the property owner \u2014 not the contractor \u2014 must be the applicant for a Critical Area Line request. Pull the OCRM critical line and the SCDES filing into the schedule before quoting waterfront jobs.",
                "applies_to": "Residential work in the 8 coastal counties at or near tidelands/beaches",
                "source": "https://des.sc.gov/programs/bureau-coastal-management/critical-area-permitting"
            },
            {
                "title": "Critical Area Line request \u2014 owner-only, with site-access details",
                "note": "To establish where the OCRM critical line falls on a parcel, the property owner (not the contractor or designer) must submit the Critical Area Line request, state the purpose, and provide any gate codes or access details for the site visit. Build this into the project schedule early on coastal jobs \u2014 staking the line is a prerequisite to designing setbacks, decks, docks, pools, and any seaward improvements, and unstaked assumptions routinely get rejected at OCRM review.",
                "applies_to": "Coastal parcels needing critical-line determination prior to design",
                "source": "https://des.sc.gov/programs/bureau-coastal-management/critical-area-permitting/critical-area-permitting-request-critical-area-line"
            },
            {
                "title": "Residential Property Condition Disclosure Act \u2014 seller permit/condition disclosure",
                "note": "Under the South Carolina Residential Property Condition Disclosure Act (S.C. Code Title 27, Chapter 50), sellers must complete a disclosure statement giving the owner the option to indicate actual knowledge of specified characteristics or conditions of the property. Unpermitted additions, alterations, or known code violations fall within the conditions a seller may need to disclose \u2014 advise homeowner clients that closing out open permits and finalizing inspections before listing avoids both disclosure complications and buyer-side renegotiation.",
                "applies_to": "Residential resale transactions and pre-sale permit close-outs",
                "source": "https://www.scstatehouse.gov/code/t27c050.php"
            },
            {
                "title": "SCDES permit timelines \u2014 request/response cycles dominate the schedule",
                "note": "SCDES (state environmental permits \u2014 stormwater, NPDES, wetlands, coastal) explicitly warns that the request-and-response cycle, which can repeat multiple times per application, can add days to months to the overall permitting timeline. Build float into project schedules for any state environmental approval and respond to RFIs the same week \u2014 a single missed response cycle often pushes a residential coastal or stormwater project a full month. Do not quote a fixed close-out date on jobs that need a parallel SCDES filing.",
                "applies_to": "Projects triggering SCDES stormwater, coastal, or wetland permits",
                "source": "https://des.sc.gov/permits-regulations/permit-central/how-long-will-permitting-process-take"
            }
        ]
    },
    "AL": {
        "name": "Alabama expert pack",
        "expert_notes": [
            {
                "title": "No statewide residential building code for 1-2 family homes \u2014 local AHJ governs",
                "note": "Alabama historically had no statewide residential building code; the Alabama Energy and Residential Codes Board sets minimum standards but enforcement of the residential code for 1-2 family dwellings is delegated to municipalities and counties. In unincorporated areas without local adoption, no building permit may be required at all \u2014 but the home builder must still be AHBLB-licensed and the energy code still applies. Always confirm the specific code edition the local AHJ enforces before designing; cities like Birmingham, Huntsville, and Mobile run on different IRC editions than rural counties.",
                "applies_to": "Residential 1-2 family construction statewide",
                "source": "https://www.iccsafe.org/advocacy/adoptions-map/alabama/"
            },
            {
                "title": "Alabama Residential Building Code council deadline \u2014 Oct 1, 2025 adoption submission",
                "note": "Per Ala. Code \u00a7 34-14A-82, the Residential Building Code Council was required to submit a proposed Alabama Residential Building Code to the Home Builders Licensure Board for adoption by October 1, 2025, with statutory regulation changes effective March 17, 2025. This is the first time Alabama is moving toward a unified statewide residential code. Confirm the AHBLB-adopted edition and effective date with the local building official before submitting permit drawings \u2014 older 2009 IRC/IECC references in legacy AHJ checklists are being replaced.",
                "applies_to": "New residential permit applications across all Alabama AHJs",
                "source": "https://law.justia.com/codes/alabama/title-34/chapter-14a/article-3/section-34-14a-82/"
            },
            {
                "title": "Alabama Residential Energy Code \u2014 reduced 2015 IECC baseline",
                "note": "Alabama's residential energy code is a state-amended (reduced) version of the 2015 IECC, weaker than the national model code. The 2009 IECC was originally adopted Nov 7, 2011 for commercial buildings and 2009 IRC for residential, then updated to 2015 IECC with Alabama-specific weakenings. Designers must follow the Alabama-amended thresholds (envelope U-factors, duct leakage, blower-door testing) \u2014 not the unmodified IECC \u2014 and document compliance via prescriptive, performance, or ERI path on the energy compliance form.",
                "applies_to": "All new residential construction and additions statewide",
                "source": "https://www.nahb.org/-/media/NAHB/advocacy/docs/top-priorities/codes/code-adoption/state-adoption-status-iecc-nov-2024.pdf?rev=6d9ff40d170448e0aab727d0d016a488&hash=041003E9F4973D12D7450B7821215E33"
            },
            {
                "title": "Alabama Home Builders Licensure Board (AHBLB) \u2014 required for residential \u2265 $10,000",
                "note": "AHBLB licensure is required for any residential construction, repair, improvement, or reroofing project where the cost is $10,000 or more (cost of construction or value to the owner). Verify the builder holds a current AHBLB residential builder license before contract \u2014 unlicensed work at or above the threshold voids lien rights and exposes the builder to misdemeanor prosecution. AHBLB is separate from the General Contractors Board; residential and commercial use different boards, exams, and applications.",
                "applies_to": "All residential construction projects \u2265 $10,000",
                "source": "https://hblb.alabama.gov/"
            },
            {
                "title": "Alabama General Contractors Board \u2014 $50,000 commercial / non-SFR threshold",
                "note": "The Alabama General Contractors Board (created 1935) licenses non-single-family-residential and commercial projects of $50,000 or greater. SFR projects fall under AHBLB instead. Confirm the contractor's classification matches the scope (building, mechanical, electrical sub-classifications) on the Board's lookup before signing \u2014 an AHBLB residential license does NOT cover commercial multifamily, retail, or institutional work above $50,000.",
                "applies_to": "Non-single-family-residential and commercial projects \u2265 $50,000",
                "source": "https://genconbd.alabama.gov/"
            },
            {
                "title": "Separate state trade boards \u2014 Master Electrician, Plumbing, HVAC must be verified independently",
                "note": "Alabama uses separate licensing boards for each trade: Alabama Electrical Contractors Board (Master Electrician license required to pull electrical permits), Alabama Plumbers and Gas Fitters Examining Board, and Alabama HVAC/Refrigeration Board. A general builder's AHBLB or General Contractors Board license does NOT authorize trade work \u2014 each trade sub must hold the corresponding active state license, and AHJs like Orange Beach require the state license number on every trade permit application.",
                "applies_to": "All electrical, plumbing, gas, and HVAC scopes statewide",
                "source": "https://www.orangebeachal.gov/1518/Contractor-Requirements"
            },
            {
                "title": "ADEM Construction General Permit (CGP) \u2014 land disturbance \u2265 1 acre",
                "note": "Alabama Department of Environmental Management's NPDES Construction General Permit covers stormwater discharge from land-disturbing activity. The 2026 CGP (effective April 1, 2026) replaces the prior CGP and is automatically administratively continued if not reissued. File a Notice of Intent (NOI) with a CBMPP (Construction Best Management Practices Plan) before breaking ground on sites with \u2265 1 acre of disturbance, or smaller sites that are part of a larger common plan of development.",
                "applies_to": "Sites disturbing \u2265 1 acre or part of common plan \u2265 1 acre",
                "source": "https://adem.alabama.gov/water/npdes-programs/construction-general-permit"
            },
            {
                "title": "Alabama Coastal Area Management Program \u2014 separate ADEM Mobile Coastal Office permit",
                "note": "Construction in Mobile and Baldwin Counties' coastal zone requires a separate ADEM Coastal permit obtained from the Mobile Coastal Office, in addition to local building permits. This is a parallel filing \u2014 do not assume the BCCAP/local checklist covers it. Variance requests under the Coastal Area Act and multi-family projects extend the review window; expect the combined BCCAP/ADEM process to run six to eight weeks before a building permit can issue.",
                "applies_to": "Construction in Mobile/Baldwin coastal zone and tidal wetlands",
                "source": "https://adem.alabama.gov/coastal/coastal-permitting-information"
            },
            {
                "title": "USACE Mobile District Section 404/10 \u2014 federal wetland and navigable waters parallel filing",
                "note": "Any fill, dredge, or structure in waters of the U.S. (including tidal wetlands of Mobile Bay, the Tensaw delta, and inland streams) requires a separate U.S. Army Corps of Engineers Mobile District Section 404 (and/or Section 10) permit, distinct from the ADEM coastal permit and the local building permit. Public notices reference each application by USACE permit number and require comments to be furnished to ADEM as well, confirming the dual-track review. Do not break ground until both federal and state authorizations are issued.",
                "applies_to": "Fill, dredge, or structures in jurisdictional waters/wetlands",
                "source": "https://www.sam.usace.army.mil/Portals/46/docs/regulatory/public_notices/SAM-2025-00252-EAH.pdf?ver=VhEOm7ugsmSlVDFwMJumCg%3D%3D"
            },
            {
                "title": "Baldwin County BCCAP/ADEM combined checklist \u2014 6 to 8 week typical timeline",
                "note": "For projects in Baldwin County's coastal jurisdiction, the BCCAP (Baldwin County Coastal Area Program) checklist coordinates the local + ADEM coastal review, which typically takes six to eight weeks total. Multi-family projects and any project requesting a variance under the Alabama Coastal Area Act add additional time and require expanded submittals. Submit the BCCAP application package complete on first try \u2014 incomplete submittals reset the review clock.",
                "applies_to": "Baldwin County coastal-area residential and multi-family projects",
                "source": "https://baldwincountyal.gov/docs/default-source/building-inspection/building-codes/bccap-application-checklist.pdf?Status=Master&sfvrsn=910c36be_3/BCCAP-Application-Checklist.pdf"
            },
            {
                "title": "Alabama Power DER interconnection \u2014 separate parallel filing for solar + battery",
                "note": "Solar PV, battery storage, or any distributed energy resource interconnecting to Alabama Power requires submission under the APC DER Technical Interconnection Requirements (TIR) Guidebook V3.2 (rev Feb 2026), which has separate processes for systems \u2264100 kW versus larger systems. This is a parallel filing to the building/electrical permit \u2014 the AHJ inspection alone does not authorize parallel operation. Submit the interconnection application early; APC review and approval-to-energize are commonly the longest pole in residential solar timelines.",
                "applies_to": "Residential solar PV, battery, and DER projects in APC service territory",
                "source": "https://www.alabamapower.com/content/dam/alabama-power/pdfs-docs/Clean-Energy/APC%20DER%20TIR%20Guidebook.pdf"
            },
            {
                "title": "Alabama Department of Revenue Sales/Use Tax Certificate of Exemption \u2014 required per project",
                "note": "Per Alabama Division of Construction Management guidance, contractors are required to apply for a Sales and Use Tax Certificate of Exemption through the Alabama Department of Revenue for qualifying construction projects. This is a project-specific filing, not a one-time license. Failure to obtain the certificate before purchasing materials forfeits the exemption and exposes the contractor to back-tax liability \u2014 file at contract execution, not at material delivery.",
                "applies_to": "Government and qualifying tax-exempt construction projects",
                "source": "https://dcm.alabama.gov/FAQs.aspx"
            },
            {
                "title": "Alabama seller disclosure \u2014 caveat emptor with narrow exceptions",
                "note": "Alabama follows caveat emptor (buyer beware) for used residential real estate; sellers are NOT generally required to fill out a property condition disclosure form, unlike most states. Exceptions: sellers must disclose health/safety hazards, defects asked about directly, and (for new construction) builder must disclose known material defects. For renovation work, advise clients that unpermitted prior work uncovered during a project may not have been disclosed at purchase \u2014 buyers had a duty to inspect, and there is generally no recourse against the prior owner.",
                "applies_to": "Resale and renovation projects involving recently-purchased Alabama homes",
                "source": "https://www.nolo.com/legal-encyclopedia/alabama-home-sellers-your-disclosure-obligations.html"
            },
            {
                "title": "Municipal business license required in addition to state license",
                "note": "Many Alabama cities and counties require contractors to obtain a separate municipal business license, registration, or permit before performing work within their jurisdiction, on top of the state AHBLB / General Contractors / trade-board license. Cities like Fairhope and Orange Beach maintain their own contractor registration rolls and will reject permit applications from contractors who hold the state license but skipped local registration. Verify city/county registration status with the building department before quoting jobs in unfamiliar jurisdictions.",
                "applies_to": "Contractors working in any incorporated Alabama municipality",
                "source": "https://gaslampinsurance.com/alabama-contractors-license-what-you-need-to-know-to-get-licensed-in-alabama/"
            }
        ]
    },
    "LA": {
        "name": "Louisiana expert pack",
        "expert_notes": [
            {
                "title": "Act 239 of 2025 \u2014 statewide permit shot clock",
                "note": "Act 239, effective August 1, 2025, mandates that Louisiana municipalities and parishes must issue permits and conduct inspections within statutory timeframes. If your AHJ exceeds the deadline, you have grounds to escalate. Document the complete-application date and track elapsed business days; this is the closest thing Louisiana has to a ministerial shot clock and applies to residential permits statewide.",
                "applies_to": "All municipal and parish residential permit applications submitted on or after 2025-08-01",
                "source": "https://www.larealtors.org/breaking-down-updates-on-construction-from-the-2025-legislative-session"
            },
            {
                "title": "LSUCCC statewide code adoption \u2014 2021 IRC / IECC effective Jan 1, 2023",
                "note": "The Louisiana State Uniform Construction Code Council (LSUCCC), established in 2005, adopted the 2021 IRC, IBC, IMC, IFGC, IPC, and the 2021 IECC (Chapter 11 energy provisions) with state amendments, effective January 1, 2023 as the statewide minimum. All 64 parishes and municipalities are required by law to enforce these state-adopted codes \u2014 local AHJs cannot weaken them but may be more stringent. Confirm which amendment package the AHJ uses before stamping drawings.",
                "applies_to": "All residential construction statewide",
                "source": "https://www.icc-nta.org/code-update/louisiana-code-adoption-and-amendments-effective-january-1-2023/"
            },
            {
                "title": "Statewide uniform code \u2014 no parish opt-out for 1- and 2-family",
                "note": "Unlike many southern states, Louisiana law preempts parish/municipal weakening of the LSUCCC: every parish and municipality must enforce the state-adopted IRC/IBC/IECC as the floor. This means a contractor cannot rely on a rural parish 'having no code' \u2014 the 2021 IRC applies even in unincorporated areas. Verify the jurisdiction's amendments via the LSUCCC public-entity search before assuming local relaxations exist.",
                "applies_to": "Rural and unincorporated parish work where contractors assume no code applies",
                "source": "https://www.iccsafe.org/advocacy/adoptions-map/louisiana/"
            },
            {
                "title": "LSLBC residential license \u2014 $75,000 threshold",
                "note": "The Louisiana State Licensing Board for Contractors (LSLBC), 600 North Street, Baton Rouge, requires a Residential Building Contractor license for any residential project where the total cost (labor + materials) is $75,000 or more, approved through the Residential Building Contractors Subcommittee. Below that threshold a state license is not required, but local occupational licenses may still apply. Verify license status on the LSLBC portal before contracting; an expired or wrong-classification license voids contract enforceability.",
                "applies_to": "Residential new construction and major remodels at or above $75,000",
                "source": "https://www.lsuagcenter.com/topics/family_home/home/design_construction/getting%20started/professional%20services/contractors%20developers/contractors/residential-contractor"
            },
            {
                "title": "LSLBC commercial license \u2014 $50,000 threshold for Building/Highway/Heavy",
                "note": "LSLBC requires a commercial contractor's license for any building, highway, heavy, or municipal/public-works project with combined labor + materials of $50,000 or more. Subcontractors fall under the same threshold for their portion of the work. License classifications must match the scope (e.g., Building Construction, Mechanical Work, Electrical Work) \u2014 performing work outside your classification is a separate violation even if you hold a valid license.",
                "applies_to": "Commercial and mixed-use projects \u2265 $50,000",
                "source": "https://lslbc.gov/types-of-licenses/"
            },
            {
                "title": "Electrical / Mechanical / Plumbing $10,000 sub-threshold",
                "note": "Per LSLBC, any Electrical, Mechanical, or Plumbing project exceeding $10,000 (labor + materials combined) requires the appropriate state contractor's license, even if the overall project is below the $50,000/$75,000 thresholds. This is a common rejection reason \u2014 a $12,000 service-panel upgrade or AC change-out triggers state licensing even though the homeowner thinks of it as a small job. Verify the trade classification matches the scope before signing.",
                "applies_to": "Standalone electrical, mechanical, or plumbing scopes over $10,000",
                "source": "https://lslbc.gov/types-of-licenses/"
            },
            {
                "title": "Master Plumber exemption \u2014 State Plumbing Board of Louisiana",
                "note": "A plumbing contractor who currently holds a Master Plumber License from the State Plumbing Board of Louisiana is exempt from the LSLBC plumbing classification requirement. Note this is a separate state agency from LSLBC \u2014 the State Plumbing Board licenses individual journeymen and master plumbers, while LSLBC licenses the contracting business. For any mechanical contractor performing plumbing work over $10,000, a master plumber license from the Plumbing Board is still mandatory.",
                "applies_to": "Plumbing contractors and mechanical contractors crossing into plumbing scope",
                "source": "https://www.lslbc.louisiana.gov/wp-content/uploads/blue_book.pdf"
            },
            {
                "title": "State Fire Marshal plan review \u2014 parallel filing required",
                "note": "The Louisiana Office of State Fire Marshal (LASFM) requires a Plan Review for all buildings to be constructed, renovated, repaired, or where occupancy is changed \u2014 this is a SEPARATE filing from the local AHJ building permit. For most residential 1- and 2-family detached dwellings the LASFM review is not required, but ADUs configured as accessory rentals, multi-family, mixed-use, and any change-of-occupancy project must file with LASFM in parallel with the parish/municipal permit. Failing to obtain LASFM approval blocks Certificate of Occupancy.",
                "applies_to": "Multi-family, ADUs used as rentals, change-of-occupancy, and commercial projects",
                "source": "https://www.lasfm.org/plan-review/plan-review-information/"
            },
            {
                "title": "Coastal Use Permit (CUP) \u2014 LDENR Office of Coastal Management",
                "note": "Construction within the Louisiana Coastal Zone (the 19 coastal parishes south of the Coastal Zone Boundary) requires a Coastal Use Permit from LDENR's Office of Coastal Management, often coordinated with US Army Corps Section 404 wetlands review. General Permit GP-6 covers certain routine activities and must be kept on-site during work. CUP review is independent of and parallel to the local building permit \u2014 start it early because wetlands delineation and agency coordination commonly add weeks to the schedule.",
                "applies_to": "Construction in the 19-parish Louisiana Coastal Zone",
                "source": "https://www.denr.louisiana.gov/assets/OCM/permits/gp/Historic_Documents/GP06/GP06_2019.pdf"
            },
            {
                "title": "Act 1416 / R.S. 40:1730.28.5 \u2014 mandatory IECC energy code adoption",
                "note": "Louisiana's 2022 legislation (HLS 22RS-957, codified at R.S. 40:1730.28.5) mandated adoption of the energy-efficiency provisions of nationally recognized codes including the 2021 IECC and 2021 IRC Chapter 11, with a statewide effective date of July 1, 2023. This means residential envelope, duct sealing, mechanical equipment efficiency, and lighting must meet 2021 IECC \u2014 older 2009/2015 IECC details on stock plans will be rejected at plan review. Build the energy-compliance documentation into the submittal, not as a correction.",
                "applies_to": "All new residential construction and additions affecting conditioned space",
                "source": "https://www.legis.la.gov/Legis/ViewDocument.aspx?d=1269076"
            },
            {
                "title": "FEMA flood-zone elevation \u2014 BFE + freeboard",
                "note": "Much of Louisiana sits in FEMA Special Flood Hazard Areas; parishes participating in the National Flood Insurance Program enforce floodplain ordinances requiring lowest-floor elevation at or above Base Flood Elevation (BFE), with many AHJs adding 1\u20133 ft of freeboard. Verify the parcel's flood zone on FEMA's National Flood Hazard Layer before designing foundations \u2014 slab-on-grade in a Zone AE or VE will be rejected. An Elevation Certificate is typically required at final inspection for permits in mapped flood zones.",
                "applies_to": "Parcels in FEMA Zones A, AE, AO, AH, V, or VE",
                "source": "https://www.fema.gov/flood-maps/national-flood-hazard-layer"
            },
            {
                "title": "Entergy Louisiana net-metering interconnection \u2014 separate from building permit",
                "note": "Solar PV and battery systems served by Entergy Louisiana require a separate interconnection application through Entergy's net-metering process \u2014 this filing is independent of the parish/municipal electrical permit. Critically, Entergy requires a visibly open, lockable, manual AC disconnect that is clearly labeled per utility specifications and approved by the utility before PTO (Permission to Operate). Permit drawings that omit the labeled lockable disconnect will be flagged at utility inspection even if the AHJ already passed the electrical rough-in.",
                "applies_to": "Residential solar PV and battery storage in Entergy Louisiana service territory",
                "source": "https://www.entergylouisiana.com/net-metering/process"
            },
            {
                "title": "No statewide ADU shot clock \u2014 parish-level review controls",
                "note": "Louisiana has NO state ADU statute analogous to California AB 881 \u2014 there is no 60-day ministerial approval mandate, no statewide impact-fee waiver, and no preemption of local ADU bans. ADU rules vary across the 64 parishes and top municipalities; pre-permit zoning review alone can take 3\u20136 months before building permit review begins. Set client expectations accordingly and budget for variance/conditional-use proceedings where the parish does not permit ADUs by right.",
                "applies_to": "ADU and accessory-dwelling projects in any Louisiana parish",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-louisiana"
            },
            {
                "title": "Designation of Qualifying Party \u2014 LSLBC initial-license requirement",
                "note": "Every LSLBC license application must include a Designated Qualifying Party who has passed the relevant trade and business/law exams; the qualifying party must be a bona fide employee or owner of the licensed entity. Common rejection reasons include using a qualifier who is also qualifying another active license, missing exam credentials, or failing to update LSLBC when the qualifier separates from the company \u2014 which can suspend the license mid-project. Maintain a backup qualifier and notify LSLBC within the required window of any change.",
                "applies_to": "All entities applying for or maintaining an LSLBC license",
                "source": "https://lslbc.gov/checklist-of-items-required-for-initial-license-and-to-maintain-license/"
            }
        ]
    },
    "KY": {
        "name": "Kentucky expert pack",
        "expert_notes": [
            {
                "title": "Statewide HVAC permit requirement (first in nation since 2011)",
                "note": "Kentucky has had a mandatory statewide HVAC permitting and inspection program since January 1, 2011, administered by the Department of Housing, Buildings and Construction (DHBC) Division of HVAC. Every residential and commercial HVAC installation, replacement, or alteration requires a state HVAC permit AND a licensed Master HVAC contractor \u2014 local building permits do NOT substitute. Pull the HVAC permit through DHBC eServices in parallel with any local building permit; failing to do so is the single most common violation flagged on final inspection.",
                "applies_to": "All residential and commercial HVAC installation, replacement, or alteration in Kentucky",
                "source": "https://dhbc.ky.gov/newstatic_info.aspx?static_id=335"
            },
            {
                "title": "Plumbing installation permit fee schedule (effective March 1, 2022)",
                "note": "Effective March 1, 2022, Kentucky's statewide plumbing installation permit fee for one- and two-family residential is a $50 base permit fee plus per-fixture charges, issued by the DHBC Division of Plumbing. Permits must be pulled by a Kentucky-licensed Master Plumber before rough-in. Homeowner self-installation is sharply restricted compared to other trades \u2014 verify the contractor holds an active KY Master Plumber license on the DHBC verification portal before signing a contract.",
                "applies_to": "Residential plumbing installations and alterations statewide",
                "source": "https://dhbc.ky.gov/newstatic_info.aspx?static_id=337"
            },
            {
                "title": "State-licensed trades: electrical, plumbing, HVAC only",
                "note": "Kentucky requires a state contractor license ONLY for electrical, plumbing, and HVAC; most other trades (general contracting, framing, roofing, drywall, painting) are regulated locally rather than at the state level. This means a 'general contractor' is not state-licensed in Kentucky \u2014 vet GCs through local registration, insurance, and references rather than a statewide CSLB-equivalent lookup. Subs in the three regulated trades MUST be verified via DHBC license search before signing.",
                "applies_to": "All Kentucky residential construction contracting decisions",
                "source": "https://www.procore.com/library/kentucky-contractors-license"
            },
            {
                "title": "DHBC license verification before contracting (EL/PL/HVAC)",
                "note": "DHBC publishes a public license search covering Building Inspector, Electrical, Electrical Inspection, Manufactured Housing, HVAC, Plumbing, and Fire licensees. Use forms EL-2 (Electrical Contractor) and EL-3 (Electrician) as reference categories \u2014 the contractor classification on file determines which scope of work they may legally perform. Expired or suspended licenses void permit eligibility and create lien/insurance exposure; check status the same week you sign.",
                "applies_to": "Verifying any KY electrical, plumbing, or HVAC contractor before contract signing",
                "source": "https://dhbc.ky.gov/newstatic_Info.aspx?static_ID=573"
            },
            {
                "title": "2018 Kentucky Residential Code (Third Edition) \u2014 currently adopted",
                "note": "Kentucky is on the 2018 Kentucky Residential Code, Third Edition with state-specific amendments published by DHBC's Division of Building Code Enforcement; mandatory compliance was set January 1, 2019. Kentucky has NOT yet adopted the 2021 or 2024 IECC \u2014 the residential energy code remains based on the older 2009 IECC baseline per the federal status tracker. Design to the KRC 2018 amended text rather than the unmodified ICC base code, since KY amendments override several IRC sections.",
                "applies_to": "All one- and two-family residential permit drawings statewide",
                "source": "https://dhbc.ky.gov/newstatic_info.aspx?static_id=297"
            },
            {
                "title": "Kentucky residential energy code lags at 2009 IECC",
                "note": "Per the DOE Building Energy Codes Program, Kentucky's residential energy code was updated to the 2009 IECC effective 7/1/2012 with enforcement 10/1/2012, and has not been updated since \u2014 MEEA's January 2025 comment to DHBC explicitly recommended adoption of 2021 or 2024 IECC because KY remains behind. Do NOT assume 2021 IECC envelope, duct-leakage, or blower-door requirements apply to a typical KY residential permit; design to 2009 IECC unless the local AHJ has adopted a stretch code. Confirm with the AHJ before quoting expensive insulation/air-sealing upgrades you cannot bill for.",
                "applies_to": "Residential energy compliance scoping and HERS/blower-door budgeting",
                "source": "https://www.energycodes.gov/status/states/kentucky"
            },
            {
                "title": "DHBC Plan Submission \u2014 state vs. local jurisdiction split",
                "note": "DHBC's Plan Submission Application Guide (KHBC_PlanGuide) governs which projects go to state-level plan review versus the local building department. One- and two-family dwellings are typically reviewed locally, while most commercial, multifamily over 3 units, and state-owned projects route through DHBC. Submitting a duplex or 3-plex to the wrong reviewer is a frequent cause of weeks of lost time \u2014 confirm the routing in the Plan Guide before submittal.",
                "applies_to": "Determining whether a KY project is state-reviewed or locally reviewed",
                "source": "https://dhbc.ky.gov/Documents/KHBC_PlanGuide.pdf"
            },
            {
                "title": "City vs. county zoning/permit routing through Kentucky Business One Stop",
                "note": "Kentucky Business One Stop directs applicants to contact the local county clerk, city clerk, or planning/zoning office for zoning and building code information \u2014 there is no unified statewide permit portal for residential. A parcel inside city limits is generally under the city's building department, while unincorporated parcels fall to the county; you do not normally pull both. Confirm jurisdiction with the county PVA / city clerk before drawing plans, because incorporation boundaries in metro Louisville and Lexington are not intuitive.",
                "applies_to": "Determining the correct AHJ for any KY residential project",
                "source": "https://onestop.ky.gov/start/Pages/buildingzoning.aspx"
            },
            {
                "title": "KDOW Floodplain Construction Permit (KRS Chapter 151) \u2014 separate parallel filing",
                "note": "Under KRS 151, the Kentucky Division of Water (DOW) Floodplain Management Section requires a state floodplain permit BEFORE any demolition, repair, renovation, development, improvement, or construction in a regulatory floodplain \u2014 this is in addition to the local building permit and any local floodplain administrator approval. Applications go to DOW at 300 Sower Boulevard, Frankfort, KY 40601 (or by email). Skipping the state-level KDOW permit and relying only on the local floodplain administrator's sign-off is one of the top enforcement issues for flood-damaged repairs.",
                "applies_to": "Any work in a 100-year floodplain anywhere in Kentucky",
                "source": "https://eec.ky.gov/Environmental-Protection/Water/PermitCert/Pages/default.aspx"
            },
            {
                "title": "Louisville MSD dual-permit rule for floodplain work",
                "note": "Inside MSD's service area (Louisville/Jefferson County), floodplain construction requires BOTH a Kentucky Division of Water (KDOW) state permit AND a separate MSD floodplain permit \u2014 the MSD permit does NOT replace the state filing. Download both application forms from MSD's flood-permitting page and submit in parallel. Build the dual-review timeline (typically several weeks each) into project schedules; treating it as a single permit is the most common cause of Louisville floodplain project delays.",
                "applies_to": "Any construction in Jefferson County / MSD service area floodplains",
                "source": "https://louisvillemsd.org/programs/floodplain-management/flood-permitting"
            },
            {
                "title": "Stream channel and wetland work also triggers KDOW Floodplain Permit",
                "note": "Per the EEC's Guide for Working in Kentucky Stream Channels & Wetlands, stream relocations and ANY construction activity in the 100-year floodplain require a KDOW Floodplain Permit \u2014 this includes dams, bridges, culverts, and bank stabilization that homeowners often think are 'just landscaping.' A retaining wall or driveway culvert near a blue-line stream commonly trips this requirement. Pull a FIRM panel and identify any stream/wetland on or adjacent to the parcel before scoping; missing this turns a $5k landscape job into a multi-month state permit case.",
                "applies_to": "Driveway culverts, retaining walls, grading, and minor structures near streams or wetlands",
                "source": "https://eec.ky.gov/Environmental-Protection/Compliance-Assistance/DCA%20Resource%20Document%20Library/StreamChannelsandWetlandsGuide.pdf"
            },
            {
                "title": "Historic-structure exemption from substantial-improvement floodplain rules",
                "note": "Per Kentucky floodplain guidance (PDS-KC), historic structures are exempt from the substantial-improvement (50%-rule) floodplain reconstruction requirements PROVIDED the project maintains the structure's historic status. This means a contributing structure in a National Register or locally designated historic district can often be repaired beyond the 50% threshold without triggering full floodplain elevation/compliance \u2014 but only if the historic designation is preserved through the work. Get a written historic-status determination from the local Historic Preservation Officer before relying on this exemption; losing the designation mid-project retroactively triggers full elevation requirements.",
                "applies_to": "Repair/renovation of historic structures in floodplains",
                "source": "https://www.pdskc.org/services/one-stop-shop/floodplain-management/existing-structures-in-the-floodplain"
            },
            {
                "title": "Louisville Metro consolidated HVAC permit process",
                "note": "Louisville Metro Construction Review issues a streamlined consolidated HVAC permit type that is eligible for inspections and is designed to simplify residential and commercial HVAC system reviews within Louisville/Jefferson County. This is in addition to (not a replacement for) the statewide DHBC HVAC permit \u2014 Louisville requires the local permit for inspection scheduling while DHBC retains licensing/oversight authority. Plan for two HVAC permits in Louisville: state DHBC permit for licensing compliance + Metro Louisville permit for local inspections.",
                "applies_to": "HVAC work inside Louisville Metro / Jefferson County",
                "source": "https://louisvilleky.gov/government/construction-review/hvac-permit"
            },
            {
                "title": "Kentucky has NO statewide permit shot clock \u2014 manage timelines locally",
                "note": "Unlike California's 60-day ADU shot clock, Kentucky has NOT enacted a statewide permit-approval time limit; AFP/AEI/Grassroot Institute tracking confirms shot-clock legislation has been proposed but not adopted in KY as of 2026. There is no statutory remedy if a local building department sits on a complete application \u2014 contractors must rely on local published timelines and political escalation rather than state preemption. Build extra schedule slack into KY residential projects and document every submission timestamp in case the AHJ delays.",
                "applies_to": "Schedule planning and client expectation-setting for KY permits",
                "source": "https://americansforprosperity.org/wp-content/uploads/2025/11/Permit-Approval-Time-Limit-One-Pager.pdf"
            },
            {
                "title": "Wildfire overlay districts are local, not state-mandated in Kentucky",
                "note": "Kentucky's mitigation guidance (FEMA Mitigation Ideas distributed by EEC) treats wildfire overlay districts as a LOCAL zoning tool \u2014 there is no statewide VHFHSZ-style designation like California's. Defensible-space and fire-resistive material requirements only apply where a county or city has adopted a wildfire overlay; most KY jurisdictions have not. Do not assume Class A roofing or 100-ft defensible space is required by state law \u2014 confirm with the local zoning office before bidding fire-hardening upgrades.",
                "applies_to": "Eastern KY mountain and rural-interface residential work",
                "source": "https://eec.ky.gov/Environmental-Protection/Water/FloodDrought/Documents/FEMA-MitigationIdeas.pdf"
            }
        ]
    },
    "OR": {
        "name": "Oregon expert pack",
        "expert_notes": [
            {
                "title": "Oregon is a statewide-uniform code state \u2014 no local amendments to structural code",
                "note": "Unlike home-rule states, Oregon's Building Codes Division (BCD) adopts a single statewide specialty code set (ORSC, OSSC, OEESC, OPSC, OMSC, OESC) and cities/counties may NOT amend the technical provisions. This means a detail accepted in Bend must be accepted in Portland \u2014 if a plans examiner cites a 'local amendment' on a structural, energy, plumbing, mechanical, or electrical issue, push back and ask for the statewide code section. Local jurisdictions can only set administrative rules (fees, submittal format, inspection scheduling).",
                "applies_to": "All residential and commercial permit work statewide",
                "source": "https://www.oregon.gov/bcd/jurisdictions/pages/index.aspx"
            },
            {
                "title": "2023 ORSC effective Oct 1, 2023 \u2014 fully mandatory April 1, 2024",
                "note": "The 2023 Oregon Residential Specialty Code became effective Oct. 1, 2023, with Chapter 1 mandatory immediately and Chapters 2\u201344 plus appendices mandatory April 1, 2024 after a six-month phase-in/grace period. During phase-in windows, applicants may choose either the prior or new edition; after the mandatory date the new code applies regardless of permit submittal date unless vested. Always confirm with the AHJ which edition the drawings are stamped to before submittal.",
                "applies_to": "Residential 1\u20132 family permit submittals",
                "source": "https://www.oregon.gov/bcd/codes-stand/pages/adopted-codes.aspx"
            },
            {
                "title": "2025 OSSC mandatory April 1, 2026 \u2014 six-month phase-in ends",
                "note": "The 2025 Oregon Structural Specialty Code (commercial) became effective Oct. 1, 2025 with a six-month phase-in window allowing submittals under the prior 2022 OSSC. Starting April 1, 2026 only the 2025 OSSC may be used. For mixed-use, multifamily R-2/R-3 over 3 stories, and commercial TI work, applications submitted on or after April 1, 2026 must be re-stamped to the 2025 edition or risk a plan-check rejection.",
                "applies_to": "Commercial and multifamily permit submittals crossing 2026-04-01",
                "source": "https://www.jr-dba.com/insights-portland-architecture/oregon-building-code-change-deadline-what-happens-april-1-2026"
            },
            {
                "title": "2025 OEESC adopted ASHRAE 90.1-2022 \u2014 effective Apr 24, 2026",
                "note": "Oregon's Energy Efficiency Specialty Code (OEESC) for commercial and low-rise multifamily was adopted by BCD with the 2025 edition replacing the 2021 edition. The OEESC adopts ASHRAE 90.1-2022 with Oregon amendments and is effective Apr 24, 2026. Envelope U-values, lighting power density, and mechanical efficiencies tighten \u2014 pull updated COMcheck/ASHRAE 90.1-2022 compliance forms before commercial energy review.",
                "applies_to": "Commercial new construction, additions, and tenant improvements",
                "source": "https://up.codes/viewer/oregon/ashrae-90.1-2022"
            },
            {
                "title": "ORS 455.467 permit shot clocks \u2014 15 vs 20 business days by population",
                "note": "ORS 455.467 sets statutory plan-review timelines: jurisdictions with population \u2265 300,000 must approve or disapprove a complete specialty-code building plan within 15 business days; smaller jurisdictions have 20 business days. Failure to meet the deadline does not auto-approve, but it triggers refund/escalation provisions and is grounds for a complaint to BCD. Document your 'complete application' date in writing \u2014 the clock only starts when the AHJ deems the submittal complete.",
                "applies_to": "All specialty-code building permit applications",
                "source": "https://oregon.public.law/statutes/ors_455.467"
            },
            {
                "title": "2025 housing-permit law \u2014 120-day engineering review timer",
                "note": "A 2025 Oregon law set a 120-day deadline for local officials to review final engineering plans (infrastructure/site civil) for housing projects, with auto-approval if the city misses the deadline. This is separate from the ORS 455.467 building-plan shot clock and applies to subdivision/site-development engineering. For ADU and small infill jobs that don't trigger engineering review, the 15/20 business-day building shot clock still controls.",
                "applies_to": "Housing subdivisions and projects requiring final engineering plan review",
                "source": "https://www.oregonlive.com/business/2025/06/housing-permits-age-like-fine-wine-lawmaker-says-a-new-law-seeks-to-speed-approvals.html"
            },
            {
                "title": "CCB licensing required for all paid construction \u2014 bond minimums by class",
                "note": "Oregon Construction Contractors Board (CCB) licensure is required for anyone bidding, arranging, or performing construction for compensation. Residential general contractors must post a $25,000 bond; residential specialty contractors a smaller bond per CCB rules. License + bond + liability insurance must be active at contract signing \u2014 a lapsed CCB number voids the contract and blocks lien rights under ORS 87. Verify the CCB number, classification, and endorsement (RGC vs RSC vs Locksmith vs Home Inspector) on the CCB lookup before quoting.",
                "applies_to": "All paid construction work in Oregon",
                "source": "https://www.oregon.gov/ccb/pages/ccb%20license.aspx"
            },
            {
                "title": "Dual-license rule \u2014 electrical and plumbing need CCB + BCD trade license",
                "note": "Electrical and plumbing contractors in Oregon must hold BOTH a CCB construction contractor license AND the appropriate BCD trade contractor license (electrical contractor, plumbing contractor). Individual workers also need a BCD-issued journeyman or limited license. HVAC/mechanical work is covered under CCB but technicians performing electrical hookups need a BCD electrical license. A single CCB number is not sufficient for trade work \u2014 plan reviewers will reject permits pulled by CCB-only contractors on electrical or plumbing scopes.",
                "applies_to": "Electrical and plumbing contractors and permit pullers",
                "source": "https://www.oregon.gov/bcd/lbdd/pages/licensed-work.aspx"
            },
            {
                "title": "BCD issues all electrical, plumbing, boiler, elevator, and manufactured-dwelling licenses",
                "note": "Oregon centralizes trade licensing at the state Building Codes Division \u2014 there is no separate state HVAC contractor license (HVAC falls under CCB with electrical/mechanical trade permits as needed). BCD issues electrical (general supervising, journeyman, limited residential), plumbing (journeyman, contractor), boiler, elevator, and manufactured dwelling licenses. Apply through BCD's eLicense system; CCB and BCD numbers are tracked separately and both must appear on permit applications.",
                "applies_to": "Trade licensing for electrical, plumbing, boiler, elevator, manufactured dwelling work",
                "source": "https://www.oregon.gov/bcd/licensing/pages/index.aspx"
            },
            {
                "title": "One- and two-family residential limited-energy permit fee \u2014 $25 flat",
                "note": "Per OAR Division 309, the electrical permit fee for a one- and two-family residential limited-energy installation is $25 flat when all limited-energy systems (low-voltage, security, AV, thermostat wiring, doorbells) are installed at the same time by the same contractor. Pulling separate limited-energy permits per system multiplies the fee unnecessarily \u2014 bundle them on a single application when the work is concurrent.",
                "applies_to": "Residential low-voltage / limited-energy electrical work",
                "source": "https://secure.sos.state.or.us/oard/displayDivisionRules.action?selectedDivision=4162"
            },
            {
                "title": "Goal 18 coastal-shoreline development prohibition",
                "note": "Statewide Planning Goal 18 prohibits new development on beaches, active foredunes, and dunes subject to severe erosion or flooding along the Oregon coast. Building on these landforms is barred outright; properties landward of the foredune may still need a Goal 18 exception, an Ocean Shore Permit from Oregon Parks and Recreation Department, and coastal-zone consistency review. Always pull the parcel's Goal 18 status before quoting a coastal addition or new SFR \u2014 a permit denial here is jurisdictional, not curable through redesign.",
                "applies_to": "Coastal parcels in Oregon's 17 coastal counties/cities",
                "source": "https://www.oregon.gov/lcd/op/pages/goal-18.aspx"
            },
            {
                "title": "DSL wetland/waterway removal-fill permits \u2014 four tiers",
                "note": "Oregon Department of State Lands (DSL) administers four permit tiers for wetland or waterway impacts: General Authorization (pre-approved low-impact activity types), General Permit (programmatic), Individual Permit (project-specific impacts), and Emergency Authorization. Any removal or fill of 50+ cubic yards in non-essential salmonid habitat (or ANY amount in essential salmonid/scenic waterway/state-designated waters) triggers DSL review separate from local building permits. File DSL and U.S. Army Corps Section 404 permits in parallel \u2014 both are required for jurisdictional waters.",
                "applies_to": "Projects with wetland, stream, or waterway impacts",
                "source": "https://www.nawm.org/pdf_lib/how_to_apply_for_a_permit_oregon.pdf"
            },
            {
                "title": "Utility interconnection is owner/developer responsibility \u2014 separate from building permit",
                "note": "Per Energy Trust of Oregon's Interconnection Guidebook, the project developer (not the utility) is responsible for obtaining all permits needed to build new lines, transformers, or service upgrades. Interconnection applications go to the serving utility (PGE, Pacific Power, or municipal/co-op like EWEB, Springfield Utility Board) and are processed in PARALLEL to the BCD/local electrical permit, not as a substitute. For solar PV + battery, plan for: (1) BCD/local electrical permit, (2) utility interconnection application, (3) net-metering agreement, (4) Energy Trust incentive paperwork if claiming rebates.",
                "applies_to": "Solar PV, battery storage, generator, and service-upgrade projects",
                "source": "https://www.energytrust.org/wp-content/uploads/2016/10/100908_Interconnection_Guidebook.pdf"
            },
            {
                "title": "AHJ split \u2014 state BCD vs delegated city/county building departments",
                "note": "Most Oregon cities and counties have assumed local building-department authority and run their own plan review and inspections; where they have NOT, BCD itself acts as the AHJ (common in unincorporated rural counties). Confirm at the BCD jurisdictions page which entity handles structural, mechanical, plumbing, and electrical for the parcel \u2014 they may be split (e.g., city does structural, state does electrical). For ePermitting jurisdictions, applications are filed through oregonepermitting.com regardless of whether the AHJ is state or local.",
                "applies_to": "Determining permit-filing jurisdiction for any Oregon parcel",
                "source": "https://www.oregon.gov/bcd/jurisdictions/pages/index.aspx"
            },
            {
                "title": "Owner-builder exemption \u2014 no CCB license, but strict resale and labor limits",
                "note": "Oregon law permits a homeowner to pull permits and perform work on their own primary residence without a CCB license, but the exemption is narrow: the owner cannot hire unlicensed labor, cannot sell the property within 24 months without CCB-violation exposure, and the exemption does NOT cover electrical or plumbing work (those still require BCD-licensed individuals). Plan reviewers will require an owner-builder declaration form; lying on it is a Class A violation. Confirm scope eligibility before advising a client to self-permit.",
                "applies_to": "Homeowners considering self-permitting and DIY construction",
                "source": "https://www.oregon.gov/bcd/lbdd/pages/oregon-permits.aspx"
            }
        ]
    },
    "OK": {
        "name": "Oklahoma expert pack",
        "expert_notes": [
            {
                "title": "OUBCC base-code adoption \u2014 locally enforced",
                "note": "The Oklahoma Uniform Building Code Commission (OUBCC) adopts the statewide base codes (currently the 2015 IRC with state amendments correcting scrivener's errors and updating referenced standards), but actual enforcement happens at the city or county level \u2014 there is no state building inspector for 1- and 2-family dwellings. Cities may run ahead of or behind the OUBCC base, so confirm the exact IRC/IBC edition the AHJ has locally adopted before stamping drawings.",
                "applies_to": "All residential construction in Oklahoma",
                "source": "https://oklahoma.gov/oubcc/codes-and-rules/international-residential-code-adoptions.html"
            },
            {
                "title": "2023 National Electrical Code in effect statewide",
                "note": "The OUBCC has adopted the 2023 National Electrical Code and it is in effect statewide for all electrical work. AFCI/GFCI scope, EV charging branch-circuit sizing, load-calculation methods, and surge-protection rules now follow 2023 NEC requirements. Drawings and panel schedules submitted with 2020 NEC details are a frequent same-day rejection.",
                "applies_to": "All electrical permits in Oklahoma",
                "source": "https://oklahoma.gov/oubcc.html"
            },
            {
                "title": "CIB licensing for electrical, plumbing, mechanical, and roofing",
                "note": "The Oklahoma Construction Industries Board (CIB) is the state agency that licenses electrical, plumbing, mechanical (HVAC/refrigeration), home inspector, and roofing contractors. Any paid trade work in those categories requires a CIB-licensed contractor named on the permit \u2014 homeowners cannot stand in for an unlicensed installer. Verify license status on the CIB lookup before signing a contract or quoting a job.",
                "applies_to": "Electrical, plumbing, mechanical, and roofing scopes statewide",
                "source": "https://oklahoma.gov/cib.html"
            },
            {
                "title": "CIB Active Contractor $50,000 commercial insurance requirement",
                "note": "An Active Plumbing, Electrical, or Mechanical Contractor must provide a certificate of insurance evidencing a minimum of $50,000.00 commercial general liability coverage on file with the CIB. A lapse in coverage automatically suspends the license, and permit clerks pulling the CIB record will reject submissions during a coverage gap. Confirm the COI is current and on file before submitting any trade permit.",
                "applies_to": "Active CIB-licensed plumbing, electrical, and mechanical contractors",
                "source": "https://oklahoma.gov/cib/your-industry/active-contractor-requirements.html"
            },
            {
                "title": "Mechanical/Plumbing license \u2014 residential vs commercial classification",
                "note": "A CIB Mechanical (HVAC) or Plumbing license issued in the residential class is allowed to install in residential homes only; commercial work (including residential change-of-use into commercial) requires the unrestricted classification. Pulling a residential-class license onto a commercial job is grounds for permit denial and a CIB enforcement complaint. Verify the classification on the license matches the project type before quoting.",
                "applies_to": "HVAC and plumbing contractors taking on commercial scopes",
                "source": "https://oklahoma.gov/cib/your-industry/mechanical.html"
            },
            {
                "title": "HVAC apprentice / journeyman / contractor license required",
                "note": "To legally perform heating, air conditioning, and refrigeration work in Oklahoma you must be licensed or registered as an apprentice through the CIB. Unlicensed HVAC work \u2014 including condenser swaps, mini-split installs, and duct replacements \u2014 voids the contract and exposes the contractor to CIB fines and customer restitution orders. Apprentices may only work under direct supervision of a journeyman or contractor.",
                "applies_to": "All HVAC scopes statewide",
                "source": "https://www.servicetitan.com/licensing/hvac/oklahoma"
            },
            {
                "title": "Verify CIB license on the public lookup before contracting",
                "note": "The CIB publishes a public license-verification portal (cibverify.ok.gov) where you can confirm legal name, classification, expiration date, and any discipline action against an electrical, plumbing, mechanical, or roofing licensee. Plan-check staff at most municipal building departments cross-reference this on submission. Pull and archive a screenshot before issuing a notice-to-proceed so a license expiration mid-job does not derail final inspection.",
                "applies_to": "Pre-contract due diligence for any trade permit",
                "source": "http://cibverify.ok.gov/Clients/OKCIB/Public/Licensee/LicenseeSearch.aspx"
            },
            {
                "title": "County jurisdiction stops at incorporated city limits",
                "note": "Oklahoma counties do not have land-use or building jurisdiction within incorporated cities and towns \u2014 those municipalities run their own building, zoning, and floodplain programs. For unincorporated parcels the County Planning Commission and county floodplain administrator are the AHJ; for parcels inside city limits, file with the city's building department instead. Confirm the parcel's incorporation status on the county GIS before assuming jurisdiction or you will refile from scratch.",
                "applies_to": "All residential and ADU projects in unincorporated vs incorporated areas",
                "source": "https://www.oklahomacounty.org/Departments/Planning-Commission"
            },
            {
                "title": "OWRB floodplain development permit \u2014 parallel filing",
                "note": "Construction within an OWRB-mapped Special Flood Hazard Area requires a separate floodplain development permit from the local floodplain administrator (county, city, or town), in addition to the building permit. The OWRB Floodplain Permit Instructions, Application & Checklist requires base-flood elevation, lowest-floor elevation certificates, and a substantial-improvement determination for additions to existing structures. Submit the floodplain permit in parallel \u2014 most AHJs will not release the building permit until the floodplain permit is approved.",
                "applies_to": "Any work within a designated SFHA or floodway",
                "source": "https://oklahoma.gov/owrb/floodplain-management/forms-and-guidance.html"
            },
            {
                "title": "Oklahoma City trade permits \u2014 separate from building permit",
                "note": "In Oklahoma City, residential new-construction electrical, mechanical, and plumbing permits are pulled separately from the building permit. Apply online, by phone at (405) 297-2948 option 3, or in person at the first-floor Business Center. Each trade permit must list the CIB-licensed contractor by license number; a missing, expired, or wrong-classification CIB credential is the most common same-day rejection at intake.",
                "applies_to": "Residential new construction and remodels in the City of Oklahoma City",
                "source": "https://www.okc.gov/Services/Permits/Building-Trade-Permits"
            },
            {
                "title": "No statewide ADU statute \u2014 local zoning controls",
                "note": "Oklahoma has no statewide ADU enabling statute \u2014 minimum size, lot coverage, setback, owner-occupancy, and parking rules are set entirely by city or county zoning. Oklahoma City and Tulsa have specific ADU ordinances; many smaller jurisdictions still treat ADUs as accessory structures or guest houses requiring conditional-use review. Pull the local zoning code and confirm the ADU pathway before designing \u2014 there is no ministerial backstop in Oklahoma law.",
                "applies_to": "Accessory Dwelling Units statewide",
                "source": "https://www.zookcabins.com/regulations/ok-adu-regulations"
            },
            {
                "title": "NEGATIVE \u2014 no state-mandated permit-approval shot clock",
                "note": "Unlike states with statutory shot clocks (e.g., California's 60-day ADU rule), Oklahoma has no state law setting a maximum review period for residential building permits \u2014 review timelines are set by each AHJ. Legislative advocacy for a 60-day permit shot clock has been raised but not enacted as of 2026, so there is no statutory remedy for slow plan check. Plan project schedules around the AHJ's published service-level target, not a statewide backstop.",
                "applies_to": "All residential permit timelines in Oklahoma",
                "source": "https://americansforprosperity.org/wp-content/uploads/2025/11/Permit-Approval-Time-Limit-One-Pager.pdf"
            },
            {
                "title": "Local floodplain Development Permit \u2014 parallel to OWRB and building permit",
                "note": "Local floodplain ordinances (e.g., Oklahoma County's 2024 Floodplain Regulation) require a Development Permit filed with the local floodplain administrator on jurisdiction-supplied forms before any structure, fill, grading, mining, or drilling occurs in the SFHA. The application typically includes an elevation certificate and, for additions to existing structures, a substantial-improvement worksheet (50% rule). Treat this as a third parallel filing distinct from the OWRB submittal and the building permit itself.",
                "applies_to": "Any development within local SFHA boundaries",
                "source": "https://www.oklahomacounty.org/Portals/0/Images/Planning%20Commission/OKLAHOMA%20COUNTY%20FLOODPLAIN%20REGULATION%20(2024).pdf?ver=4CzR45HpqSHUt7zMhtk1Jg%3D%3D&timestamp=1711563437978"
            },
            {
                "title": "Zoning denial appeal path \u2014 Board of Adjustment then district court",
                "note": "Oklahoma municipal zoning, variance, and special-use decisions are governed by Title 11 of the Oklahoma Statutes; appeals from a Board of Adjustment or planning commission denial run to district court via writ of certiorari rather than an administrative appeals board. A permit denied on zoning grounds (setback, use, lot coverage) should first be challenged through the BOA variance or hardship route, not an administrative appeal of the building official. Document the staff denial reason in writing \u2014 district courts review the administrative record on the briefs.",
                "applies_to": "Permits denied on zoning, setback, or use grounds",
                "source": "https://www.okbar.org/barjournal/november-2022/childs_2/"
            }
        ]
    },
    "CT": {
        "name": "Connecticut expert pack",
        "expert_notes": [
            {
                "title": "DCP Home Improvement Contractor (HIC) registration \u2014 annual March 31 expiry",
                "note": "Connecticut Department of Consumer Protection requires every contractor performing residential repair/remodel work to hold a Home Improvement Contractor (HIC) registration. All HIC registrations expire annually on March 31, the renewal fee is $220, and applicants must carry general liability insurance. Verify the HIC number on eLicense before quoting \u2014 an expired registration voids the contract and blocks the contractor from suing for payment under CGS \u00a720-429.",
                "applies_to": "All residential repair, remodel, and alteration work in Connecticut",
                "source": "https://portal.ct.gov/dcp/license-services-division/all-license-applications/home-improvement-applications"
            },
            {
                "title": "Plumbing & Pipefitting licensing \u2014 October 31 annual expiry",
                "note": "Connecticut DCP issues separate plumbing and pipefitting contractor (P-1/P-2) and journeyperson licenses, all of which expire annually on October 31. Contractor renewal is $150, journeyperson renewal is $120. Plumbing permits will not be issued unless the contractor of record is a currently-licensed P-1 (unlimited) or appropriate limited classification \u2014 confirm the license is active, not just issued.",
                "applies_to": "All plumbing and pipefitting work statewide",
                "source": "https://portal.ct.gov/dcp/license-services-division/all-license-applications/plumbing-and-pipefitting-licensing"
            },
            {
                "title": "Major Contractor designation under CGS \u00a720-340 et seq.",
                "note": "Connecticut defines a 'Major Contractor' as a person engaged in construction, structural repair, structural alteration, dismantling, or demolition that exceeds the scope of an HIC. Major Contractor registration is separate from the HIC registration and is required for new home construction and substantial structural work \u2014 using only an HIC for new-home or major structural jobs is a common DCP violation that voids consumer-protection rights.",
                "applies_to": "New home construction and structural alteration/demolition projects",
                "source": "https://portal.ct.gov/dcp/common-elements/consumer-facts-and-contacts/major-contractor"
            },
            {
                "title": "2024 IRC/IECC adoption underway \u2014 2026 Connecticut State Building Code effective",
                "note": "The Codes & Standards Committee began accepting Code Change Proposals against the 2024 IRC and 2024 IECC starting September 1, 2024, and the resulting 2026 Connecticut State Building Code is scheduled to take effect in 2026. Confirm with the local building official which edition (current 2022 base or new 2026 base) governs your application date \u2014 projects submitted right around the changeover are often kicked back for using the wrong energy compliance forms.",
                "applies_to": "Permit applications crossing the 2026 CT State Building Code effective date",
                "source": "https://portal.ct.gov/das/office-of-state-building-inspector/building-and-fire-code-adoption-process"
            },
            {
                "title": "Statewide stretch energy code \u2014 ~10% above base for new commercial",
                "note": "Connecticut maintains a state stretch energy code that requires new large commercial construction to exceed the baseline state energy code by approximately 10%, or to use an alternative compliance path. Municipalities may adopt the stretch code as mandatory locally \u2014 check whether the AHJ has opted in before sizing the envelope, HVAC, or lighting power density on commercial drawings.",
                "applies_to": "New large commercial construction in stretch-code municipalities",
                "source": "https://database.aceee.org/state/buildings-summary"
            },
            {
                "title": "Statewide building code preempts municipal code variation",
                "note": "Connecticut is a statewide-code state \u2014 the State Building Code adopted by DAS Office of the State Building Inspector applies in every municipality, and local building officials enforce it but cannot adopt a stricter base building code. This means residential code interpretations should be consistent across towns; if a local official imposes a requirement not in the State Code, request the specific code section in writing and escalate to the OSBI for an interpretation if needed.",
                "applies_to": "All construction subject to the Connecticut State Building Code",
                "source": "https://portal.ct.gov/das/knowledge-base/articles/ctbuys/safety-codes-and-inspections/connecticut-state-building-codes-adoption-process"
            },
            {
                "title": "DEEP Coastal Permit \u2014 separate filing for tidal/coastal/navigable waters work",
                "note": "DEEP's Land & Water Resources Division regulates all activities in tidal wetlands and in tidal, coastal, or navigable waters under the Structures, Dredging & Fill and Tidal Wetlands Acts. This is a parallel state filing on top of the local building permit \u2014 docks, seawalls, shoreline retaining walls, and any fill below mean high water require a DEEP Coastal Permit before the town will sign off on the building permit.",
                "applies_to": "Shoreline structures, docks, seawalls, fill, and dredging in coastal waters",
                "source": "https://portal.ct.gov/DEEP/Coastal-Resources/Coastal-Permitting/Coastal-Permitting"
            },
            {
                "title": "Inland wetlands \u2014 municipal commission jurisdiction, not DEEP",
                "note": "Inland wetlands and watercourses in Connecticut are regulated at the municipal level by each town's Inland Wetlands Commission, NOT directly by DEEP (DEEP only handles tidal/coastal). Any regulated activity within the wetlands or upland review area requires a separate Inland Wetlands permit from the local commission before the building permit can be issued \u2014 wetlands sign-off is one of the most common holds on residential additions and pools.",
                "applies_to": "Construction within or near inland wetlands and watercourses",
                "source": "https://ctwetlands.org/uploads/1/3/0/0/130028447/pp2_caws_-_deep_presentation_25.pdf"
            },
            {
                "title": "DEEP statutory permit timeframes \u2014 predictable approval windows",
                "note": "DEEP publishes statutory permitting timeframes for each environmental permit category so applicants know how long review will take from a complete application. When DEEP exceeds its published timeframe without issuing a deficiency notice, the applicant has grounds to contact the program supervisor and escalate \u2014 keep date-stamped proof of submittal and any agency correspondence to support the timeline argument.",
                "applies_to": "DEEP-issued environmental permits (coastal, water diversion, stormwater, etc.)",
                "source": "https://portal.ct.gov/-/media/DEEP/Permits_and_Licenses/Factsheets_General/PermittingTimeframespdf.pdf"
            },
            {
                "title": "Climate Risk Mapping Tool \u2014 verify flood/storm exposure before design",
                "note": "Governor Lamont launched a free statewide online climate risk mapping tool in September 2025 that lets owners and contractors look up property-specific flood, storm-surge, and heat exposure. Pull the report for any coastal or low-lying CT parcel before finalizing foundation/elevation drawings \u2014 FEMA flood zone alone understates risk, and elevation requirements in shoreline towns (Old Saybrook, Stonington, Fairfield, Madison) are frequently driven by these overlay maps.",
                "applies_to": "Coastal and flood-prone Connecticut parcels",
                "source": "https://portal.ct.gov/governor/news/press-releases/2025/09-2025/governor-lamont-announces-launch-of-online-climate-risk-mapping-tool-for-homeowners-and-businesses"
            },
            {
                "title": "Municipal overlay zones layer on top of base zoning",
                "note": "Connecticut municipalities use overlay zones (flood hazard, aquifer protection, historic district, ridgeline, coastal area management) that add a second layer of regulation on top of the base zoning district without changing the underlying use. Always pull the property's overlay status from the town GIS before scoping work \u2014 historic district and aquifer protection overlays in particular trigger separate commission approvals and can block exterior changes that would otherwise be by-right.",
                "applies_to": "Properties in overlay zones (historic, flood, aquifer, ridgeline, coastal)",
                "source": "https://resilientconnecticut.media.uconn.edu/wp-content/uploads/sites/3830/2023/10/Overlay-Zones-10.12.23.pdf"
            },
            {
                "title": "ADU enabling law \u2014 municipal opt-out preserved, no statewide ministerial path",
                "note": "Connecticut's ADU statute (PA 21-29) made ADUs allowable as-of-right statewide, BUT towns were given an explicit opt-out by a 2/3 vote of the legislative body, and many CT municipalities have opted out or imposed owner-occupancy and size caps. Unlike states with a true ministerial shot clock, CT ADU permits still go through the full local zoning process where the town has not opted in \u2014 confirm the specific town's ADU regulations and any opt-out vote before promising a 'by-right' timeline.",
                "applies_to": "Accessory Dwelling Unit projects in Connecticut",
                "source": "https://www.zookcabins.com/regulations/adu-regulations-in-connecticut"
            },
            {
                "title": "Uniform Property Condition Disclosure \u2014 CGS \u00a720-327b at listing",
                "note": "Connecticut General Statutes \u00a720-327b requires the seller of residential property (1-4 family) to deliver the Residential Property Condition Disclosure Report to the buyer before the buyer signs the purchase contract. Failure to deliver entitles the buyer to a $500 credit at closing. Unpermitted additions, finished basements, or HVAC/electrical work performed without permits MUST be disclosed \u2014 and they routinely surface during the buyer's inspection, killing deals or forcing retroactive permits.",
                "applies_to": "Sellers of 1-4 family residential property in Connecticut",
                "source": "https://portal.ct.gov/-/media/DCP/pdf/2019-Residential-Property-and-Fondation-Condition-Reports-Effective-October-1-2019-04.pdf"
            },
            {
                "title": "HIC contracts \u2014 written contract requirements under CGS \u00a720-429",
                "note": "Every home improvement contract over $200 in Connecticut must be in writing, signed by both parties, contain start and completion dates, include the HIC registration number, and provide a 3-day cancellation notice. A contractor who fails any of these requirements cannot enforce the contract or place a mechanic's lien \u2014 this is the single most common reason CT contractors lose payment disputes, so ensure every job folder contains a fully-compliant signed contract before any work begins.",
                "applies_to": "All home improvement contracts over $200",
                "source": "https://portal.ct.gov/dcp/trade-practices-division/home-improvement-for-consumers"
            }
        ]
    },
    "UT": {
        "name": "Utah expert pack",
        "expert_notes": [
            {
                "title": "Utah DOPL contractor licensing \u2014 B100 / R100 / E100 classifications",
                "note": "The Utah Division of Professional Licensing (DOPL), under the Department of Commerce, issues all contractor licenses. General contractors fall under B100 (General Building), R100 (Residential/Small Commercial), or E100 (General Engineering). Verify the contractor's license number, classification, and active status on the DOPL Licensee Lookup before signing \u2014 an expired or wrong-class license blocks permit issuance and voids lien rights.",
                "applies_to": "All licensed construction work in Utah",
                "source": "https://commerce.utah.gov/dopl/contracting/apply-for-a-license/general-contractor/"
            },
            {
                "title": "S350 Specialty Contractor classification for HVAC",
                "note": "HVAC work in Utah requires the S350 Specialty Contractor license classification through DOPL \u2014 there is no standalone 'mechanical contractor' license. Specialty contractors must complete a 25-hour pre-licensure course; general, plumbing, and electrical contractors require a 30-hour course. Confirm the S350 endorsement specifically before quoting heat-pump or AC change-outs.",
                "applies_to": "HVAC change-outs, new installs, and ductwork in Utah",
                "source": "https://www.servicetitan.com/licensing/hvac/utah"
            },
            {
                "title": "Master license required on staff for plumbing and electrical contractors",
                "note": "Utah requires every licensed plumbing or electrical contracting company to keep a Master-level license holder on staff (not just a journeyman). This is separate from the company's contractor license and is a common audit failure point when the master leaves the company. Confirm the master's name and license number when vetting an electrical or plumbing sub.",
                "applies_to": "Plumbing and electrical contracting firms in Utah",
                "source": "https://www.procore.com/library/utah-contractors-license"
            },
            {
                "title": "DOPL license verification before contract signing",
                "note": "Use the secure.utah.gov Licensee Lookup & Verification system to confirm any DOPL-issued license is active and unsuspended before contracting. The system shows current status, classification, and disciplinary history. Hiring an unlicensed or suspended contractor can void the contract, block the permit, and forfeit consumer protection remedies.",
                "applies_to": "Homeowner due diligence and GC/sub vetting",
                "source": "https://secure.utah.gov/llv/search/index.html"
            },
            {
                "title": "2021 I-Codes adopted statewide effective July 1, 2023",
                "note": "Utah adopted the 2021 International Codes (IBC, IRC, IFC, IMC, IPC) statewide effective July 1, 2023 under the State Construction Code. Local jurisdictions cannot regress to the 2018 cycle but may add limited amendments. Drawings must reference the 2021 edition explicitly \u2014 referencing the 2018 edition is a common plan-check rejection.",
                "applies_to": "All new permit applications statewide",
                "source": "https://www.iccsafe.org/about/periodicals-and-newsroom/utah-enhances-building-safety-statewide-with-adoption-of-2021-international-codes/"
            },
            {
                "title": "2021 IECC + ASHRAE 90.1-2019 energy code with Utah amendments",
                "note": "Utah's residential energy code is the 2021 IECC (effective 7/1/2023) and the commercial energy code is ASHRAE 90.1-2019 (effective 7/1/2024), each with state amendments. Utah amendments soften several prescriptive envelope and mechanical requirements relative to the model code, so use Utah-specific REScheck/COMcheck inputs rather than the unamended IECC defaults.",
                "applies_to": "New construction, additions, and conditioned-space alterations",
                "source": "https://www.energycodes.gov/status/states/utah"
            },
            {
                "title": "Wildland Urban Interface (WUI) map adoption deadline \u2014 Jan 1, 2026 (HB 48)",
                "note": "House Bill 48 (2025 session) requires every Utah city to adopt a local WUI map by January 1, 2026 and apply the Utah Wildland Urban Interface Code to construction within mapped zones. WUI provisions typically apply to new construction, additions, and remodels over $50,000 in mapped areas \u2014 triggering ignition-resistant exterior materials, ember-resistant venting, and defensible-space landscaping. Confirm the parcel's WUI status with the AHJ before specifying siding, eaves, or vents.",
                "applies_to": "Construction in mapped wildfire-prone Utah jurisdictions",
                "source": "https://saltlakecountyem.gov/utah-hb-48-wildland-urban-interface-modifications/"
            },
            {
                "title": "Floodplain Development Permit is a separate parallel filing",
                "note": "Any development inside a FEMA Special Flood Hazard Area in Utah requires a Floodplain Development Permit from the local floodplain administrator in addition to the building permit, satisfying NFIP rules in 44 CFR. Required documentation includes elevation certificates, lowest-floor elevation relative to BFE, and substantial-improvement valuation if the project value exceeds 50% of the structure. Skipping this filing voids NFIP coverage and is a common rejection reason on riverside parcels.",
                "applies_to": "Construction within FEMA SFHA-designated floodplains",
                "source": "https://floodhazards.utah.gov/wp-content/uploads/2023/06/Floodplain-Development-Permit-Checklist-MG-3-30-23.pdf"
            },
            {
                "title": "City vs. county vs. MSD permit jurisdiction split",
                "note": "Utah does not have a single statewide residential permit office \u2014 jurisdiction depends on whether the parcel is inside city limits, in unincorporated county, or inside a Municipal Services District (e.g., Greater Salt Lake MSD covers several unincorporated SLC-area townships). Confirm jurisdiction first via parcel lookup; submitting to the wrong office is the single most common cause of week-long delays for cross-border projects.",
                "applies_to": "Determining the correct AHJ before submittal",
                "source": "https://msd.utah.gov/373/Do-I-Need-a-Building-Permit"
            },
            {
                "title": "DFCM State Building Official governs state-owned buildings only",
                "note": "The Utah Division of Facilities Construction and Management (DFCM) Building Official enforces codes on state-owned buildings (universities, prisons, capitol facilities) \u2014 NOT on private residential or municipal projects. Private residential permits go to the city/county AHJ, not DFCM. This is a frequent confusion when contractors search 'Utah state building official' and call the wrong office.",
                "applies_to": "Clarifying that DFCM is not the AHJ for private work",
                "source": "https://dfcm.utah.gov/construction-management/building-official/"
            },
            {
                "title": "County plan-review screening clock cannot be paused by late filings (17-79-810)",
                "note": "Under Utah Code 17-79-810, if an applicant submits a complete application before 5 p.m. on the last day of the county's screening period, the county may not pause the screening clock and must begin substantive plan review. Document the timestamp of submittal \u2014 this is the leverage point if a county tries to restart the clock after a late-day filing.",
                "applies_to": "County-level building permit applications",
                "source": "https://le.utah.gov/xcode/Title17/Chapter79/17-79-S810.html?v=C17-79-S810_2025110620251206"
            },
            {
                "title": "Building permit validity \u2014 180 days from approval",
                "note": "Many Utah jurisdictions (e.g., Ivins City Code 16.11.128) limit use approvals and building permit approvals to a maximum of 180 days from the date of approval before they expire. If construction has not commenced or inspections have lapsed, the permit voids and a new application (and often new fees) is required. Schedule the first footing/foundation inspection well within the 180-day window.",
                "applies_to": "All issued building permits in Utah",
                "source": "https://codelibrary.amlegal.com/codes/ivinsut/latest/ivins_ut/0-0-0-7822"
            },
            {
                "title": "ADUs require permits \u2014 no statewide ministerial shot clock yet",
                "note": "Utah law has streamlined ADU approval but ALL ADUs still require a building permit before construction. Unlike California's 60-day ministerial shot clock, Utah does NOT yet have an enacted statewide ADU shot clock \u2014 H 876 (a 30-day development approval shot clock) saw no action in the 2025 session. Plan timelines based on the local AHJ's posted review times, not on a statutory deadline.",
                "applies_to": "Accessory dwelling unit projects statewide",
                "source": "https://www.zookcabins.com/regulations/ut-adu-regulations"
            },
            {
                "title": "DOPL license renewal cycle \u2014 bi-annual, $113",
                "note": "DOPL contractor licenses are valid for two years and must be renewed bi-annually with a $113 renewal fee plus continuing-education compliance. Lapsed licenses cannot pull permits and may require re-examination if the lapse exceeds the grace period. Verify the renewal date on the Licensee Lookup; lapsed-license issues spike every odd-numbered year as the two-year cycle resets.",
                "applies_to": "Active Utah contractor license maintenance",
                "source": "https://www.housecallpro.com/licensing/hvac/utah/"
            }
        ]
    },
    "IA": {
        "name": "Iowa expert pack",
        "expert_notes": [
            {
                "title": "Iowa State Building Code is voluntary for most local jurisdictions",
                "note": "Unlike many states, adoption of the Iowa State Building Code (Iowa Code Chapter 103A) is voluntary for cities and counties \u2014 there is no single statewide residential code automatically enforced everywhere. In areas where a local code has been adopted (or where the local jurisdiction has adopted the State Building Code by reference), the local jurisdiction is responsible for enforcement. Always confirm which code edition the AHJ has actually adopted before drawing plans; assuming a uniform state code will get plans rejected.",
                "applies_to": "Code-edition determination for any Iowa permit",
                "source": "https://www.energycodes.gov/status/states/iowa"
            },
            {
                "title": "Some Iowa counties have NO building code or permit process at all",
                "note": "A material number of Iowa counties \u2014 particularly rural and unincorporated areas \u2014 have no zoning ordinance and issue no building permits for residential construction. Iowa Code Chapter 335 lets the board of supervisors adopt zoning by ordinance, but it is not mandatory. Before assuming a permit is required, check directly with the county zoning/recorder's office; in unincorporated areas the only filings may be a driveway/access permit and septic/well approvals, not a structural permit.",
                "applies_to": "Unincorporated Iowa county projects",
                "source": "https://www.legis.iowa.gov/docs/ico/chapter/335.pdf"
            },
            {
                "title": "State Building Code Bureau plan review required regardless of local code",
                "note": "Construction documents for certain project types (state-owned buildings, schools, multi-family of three or more units, and other categories listed by the Building Code Bureau) must be submitted to DIAL's Building Code Bureau for plan review regardless of whether the local jurisdiction has adopted a code. This is a parallel filing on top of any local building permit \u2014 a city permit alone does not satisfy state plan review for these occupancies. Confirm scope before quoting; missing the state submittal blocks occupancy.",
                "applies_to": "Multi-family (3+ units), schools, state-owned, and other Bureau-listed projects",
                "source": "https://dial.iowa.gov/licenses/building/plan-review"
            },
            {
                "title": "Iowa contractor registration with DIAL \u2014 $50 annual fee, separate from trade licensure",
                "note": "Iowa law requires every construction contractor and business performing construction work in the state to register with the Department of Inspections, Appeals, and Licensing (DIAL). The annual fee is $50 and is non-refundable; renewal is online. Registration is separate from trade-specific licensure (electrical, plumbing, mechanical) \u2014 you need BOTH the DIAL registration and the trade license. Working without registration exposes the contractor to penalties and bars enforcement of mechanic's lien rights.",
                "applies_to": "All construction contractors and businesses operating in Iowa",
                "source": "https://dial.iowa.gov/licenses/building/contractors/how-do-i-contractor-registration"
            },
            {
                "title": "Plumbing & Mechanical Systems Board licensure required statewide",
                "note": "Iowa law requires all plumbing and mechanical (including HVAC/HVAC-R) contractors to be licensed by the Plumbing and Mechanical Systems Board AND registered with DIAL \u2014 both are mandatory and there is no local opt-out. Licenses are issued at apprentice, journeyperson, master, and contractor levels through DIAL's online self-service portal. Cities such as Iowa City require the Master-level credential before issuing the permit, so verify the master's name on the application matches the firm pulling the permit.",
                "applies_to": "All plumbing, mechanical, and HVAC work in Iowa",
                "source": "https://dial.iowa.gov/licenses/building/plumbing-mechanical/plumbing-licensure/contractor-license"
            },
            {
                "title": "Master-trade credential required to pull permits in major Iowa cities",
                "note": "In municipalities like Iowa City, only Master electricians, Master plumbers, and Master HVAC license-holders may pull the corresponding trade permit \u2014 journeyperson or apprentice credentials are not sufficient at the permit counter. The Master must be associated with the registered contracting business. Plan for the Master's license number and signature on the application; submitting under a journeyperson is one of the most common reasons for same-day permit rejection.",
                "applies_to": "Trade permits in Iowa City and other Iowa municipalities with master-credential requirements",
                "source": "https://www.icgov.org/government/departments-and-divisions/neighborhood-and-development-services/development-services/building-inspection-services/licensing-requirements"
            },
            {
                "title": "Iowa has no state-mandated permit shot-clock \u2014 review timing follows local rule",
                "note": "Iowa has not enacted a statewide ministerial shot-clock for residential building permits; legislative proposals such as a 90-day land-use shot clock and HF 876 (third-party approval backstop) have been introduced but did not pass. For ADUs specifically, the AIA Iowa guide states the review timeline cannot be longer than the AHJ's normal review timeline \u2014 meaning ADU review is bounded by city policy, not a statutory clock. Set client expectations accordingly and document the AHJ's published target review window.",
                "applies_to": "All residential permit timing assumptions",
                "source": "https://www.housingaffordabilityinstitute.org/housing-reform-2026/"
            },
            {
                "title": "ADU permits require BOTH zoning approval and a building permit",
                "note": "Every Accessory Dwelling Unit in Iowa requires two separate approvals: zoning approval (lot eligibility, setbacks, owner-occupancy or other local conditions) and a building permit. Site plans must show placement, setbacks, and utility connections; some cities (e.g., Urbandale \u00a7160.37) impose temporary-use permitting layers on top. Submitting the building permit before zoning sign-off is a frequent rework trigger \u2014 sequence the zoning approval first.",
                "applies_to": "All Iowa ADU projects",
                "source": "https://www.zookcabins.com/regulations/iowa"
            },
            {
                "title": "Floodplain Development Permit required for construction along most Iowa waterways",
                "note": "Iowa DNR requires a Floodplain Development Permit for construction along most of the state's waterways, and permits are also generally required for dams and stream alterations. This is a separate filing from the local building permit, submitted via the DNR PERMT system, and the DNR will not act until the application is determined complete (use the 3-step PQC tool to confirm jurisdiction). Skipping this filing on a riverside lot can void the building permit and trigger after-the-fact violation penalties.",
                "applies_to": "Construction in or near Iowa floodplains and waterways",
                "source": "https://www.iowadnr.gov/environmental-protection/land-quality/flood-plain-management/development-permits"
            },
            {
                "title": "Sovereign Lands Construction Permit for state-owned land or water",
                "note": "Any construction on Iowa state-owned lands or waters (most navigable rivers, lake beds, meandered streams) requires a Sovereign Lands Permit from Iowa DNR via the PERMT site. This is in addition to \u2014 not in lieu of \u2014 a Floodplain Development Permit, and is mailed to DNR's Flood Plain & Sovereign Lands Sections at 6200 Park with location map and construction plans attached. Docks, seawalls, intake lines, and bank stabilization on lakes/rivers are the most common triggers.",
                "applies_to": "Construction on or in Iowa state-owned land or water (docks, seawalls, intakes)",
                "source": "https://www.iowadnr.gov/environmental-protection/land-quality/sovereign-lands-permits"
            },
            {
                "title": "Distributed-generation 30-day pre-installation utility notice required",
                "note": "Iowa law requires the owner of a distributed generation system (rooftop solar, battery, small wind) to notify the serving electric utility at least 30 days before installing the system. This applies to investor-owned and municipal/cooperative utilities alike, and is separate from the building/electrical permit. Build the 30-day clock into the project schedule \u2014 energizing without the notice and an executed interconnection agreement violates the utility's tariff and can force disconnection.",
                "applies_to": "Residential solar PV, battery storage, and small-wind interconnection",
                "source": "https://netamu.com/wp-content/uploads/2025/02/consumer_guide_for_distributed_generation_3.2023.pdf"
            },
            {
                "title": "IUB interconnection standards (199 IAC 15.10) \u2014 isolation device + tariff compliance",
                "note": "Iowa Utilities Board rule 199\u201415.10(476) sets the standards for interconnecting customer-owned generation. Among other things, the customer must allow the utility access to a visible, lockable isolation device \u2014 and if the device is in a building or area that may be unoccupied, the customer must provide the utility with access. Confirm the disconnect location and access method with the utility before final inspection; AHJs increasingly verify the IUB-compliant disconnect at electrical sign-off.",
                "applies_to": "All grid-tied PV, storage, and DG installs in Iowa",
                "source": "https://www.legis.iowa.gov/docs/iac/rule/01-03-2018.199.15.10.pdf"
            },
            {
                "title": "Municipal-utility DG interconnection is governed by the muni's own standards, not IUB",
                "note": "Customers served by a municipal utility (e.g., Greenfield Municipal Utilities, Muscatine Power & Water) interconnect under that utility's own DG standards \u2014 typically 50 kW or less for residential \u2014 rather than the IUB rules that govern investor-owned utilities. The muni standards still require all construction and facilities to meet applicable building and electrical codes, and require a signed parallel-operation agreement before energizing. Always pull the specific muni's DG packet; using a MidAmerican/Alliant template at a muni can stall the project at PTO.",
                "applies_to": "Solar/storage projects in Iowa municipal-utility service territories",
                "source": "https://www.gmu-ia.com/documents/01%20Greenfield%20Municipal%20Utilities%20Distributed%20Generation%20Interconnection%20Standards.pdf"
            },
            {
                "title": "Local floodplain overlay regulations apply on top of DNR permitting",
                "note": "Counties such as Allamakee administer their own Flood Plain (Overlay) District regulations and require a county-level Floodplain Development Permit/Application. The county overlay explicitly does not imply that areas outside the mapped floodplain are free from flooding \u2014 fill, finished-floor elevation, and certificate-of-elevation requirements still apply inside the overlay. Pull both the county overlay permit and the DNR floodplain permit; one does not substitute for the other.",
                "applies_to": "Projects inside mapped county floodplain overlay districts",
                "source": "https://allamakeecounty.iowa.gov/documents/flood-plain-development-application-permit"
            },
            {
                "title": "Pending SF 2433 \u2014 statutory definition of 'national energy code' (2026 session)",
                "note": "Senate File 2433 (introduced in the 2026 session) would amend Iowa Code \u00a7103.1 to add a defined term 'national energy code,' signaling movement toward a clearer statutory anchor for the energy code Iowa references. The bill has not been enacted as of this writing \u2014 current energy-code adoption still flows through the State Building Code Commissioner's rulemaking and local adoption decisions. Track the bill's status before quoting which IECC edition applies; do not assume the new definition is in force.",
                "applies_to": "Energy-code edition determination for projects spanning the 2026 legislative session",
                "source": "https://www.legis.iowa.gov/docs/publications/LGI/91/SF2433.pdf"
            },
            {
                "title": "County zoning office \u2014 not a building department \u2014 often issues the permit",
                "note": "In counties such as Marion, building permits are issued by the Zoning department, and the county does not perform building inspections at all \u2014 the zoning permit is essentially a land-use sign-off, with code-compliance left to the contractor. Do not assume an inspector will catch errors before final; document compliance internally and retain stamped engineering for any structural element. This split is typical of many Iowa counties and is the single biggest source of confusion for out-of-state contractors.",
                "applies_to": "Iowa county-jurisdiction projects with zoning-only permitting",
                "source": "https://www.marioncountyiowa.gov/zoning/faq/"
            }
        ]
    },
    "NV": {
        "name": "Nevada expert pack",
        "expert_notes": [
            {
                "title": "NSCB license classifications \u2014 A, B, and 42 C-subclasses",
                "note": "The Nevada State Contractors Board issues licenses under three classes: Class A (General Engineering), Class B (General Building), and Classification C with 42 distinct subcontracting fields (e.g., C-2 Electrical, C-1 Plumbing, C-21 HVAC/Refrigeration). Per NRS 624.215, a contractor may only bid or contract within their licensed classification AND under their monetary limit \u2014 exceeding either voids contract enforceability and lien rights. Always print the license number and monetary limit on bids and contracts before submitting to the AHJ.",
                "applies_to": "All paid construction work in Nevada requiring a contractor license",
                "source": "https://www.nvcontractorsboard.com/licensing/license-classifications/"
            },
            {
                "title": "NSCB Licensure by Endorsement \u2014 4-year same-QI rule",
                "note": "Out-of-state contractors can shortcut Nevada licensing via the Endorsement path, but only if they (1) hold an active license in the endorsing state with the SAME qualified individual for the past four years and (2) have not been investigated for misconduct. Switching the qualifying individual within those four years disqualifies the application and forces the full Nevada exam path. Confirm QI continuity in the endorsing state's records before filing the endorsement application.",
                "applies_to": "Out-of-state contractors seeking to operate in Nevada",
                "source": "https://www.nvcontractorsboard.com/licensing/licensure-by-endorsement/"
            },
            {
                "title": "Statewide energy code \u2014 2024 IECC adopted with March 12, 2025 revisions (NAC 701)",
                "note": "The Governor's Office of Energy adopted the 2024 IECC as published effective August 18, 2024, then issued revisions on March 12, 2025 under NAC 701. Local jurisdictions adopt on their own schedules, so confirm which edition (and revision date) the AHJ is enforcing on the application date \u2014 drawings produced under the unrevised 2024 IECC may need envelope or mechanical updates before plan check accepts them.",
                "applies_to": "All new construction and additions subject to Nevada energy code review",
                "source": "https://www.energy.nv.gov/notices2/nac-701-building-energy-codes/"
            },
            {
                "title": "Washoe County code transition \u2014 July 1, 2025 effective with Jan 1, 2026 cutoff",
                "note": "In Washoe County (Reno/Sparks/unincorporated), the new code editions took effect July 1, 2025 with a transition window allowing in-flight projects to remain under the prior cycle through January 1, 2026. Submittals dated on or after Jan 1, 2026 must comply fully with the new editions; partial submissions made before the cutoff should be tracked carefully because the AHJ can require resubmission under the newer code if the application goes stale.",
                "applies_to": "Permit applications in Washoe County crossing the 2026-01-01 cutoff",
                "source": "https://snarsca.com/blog/iecc-adoption-and-its-affects-on-las-vegas-contractors/"
            },
            {
                "title": "Southern Nevada Energy Conservation Code 2024 \u2014 effective January 11, 2026",
                "note": "Clark County and the Southern Nevada code-coordinated jurisdictions adopted the S. NV Energy Conservation Code 2024 (built on the 2024 IECC) effective January 11, 2026. The Southern Nevada Amendments are published separately from the IECC base text and modify mechanical, envelope, and rating-index sections \u2014 verify drawings reference the Southern Nevada amended sections, not the unamended IECC, for any Clark County project.",
                "applies_to": "Permits in Clark County and coordinated Southern Nevada jurisdictions",
                "source": "https://up.codes/viewer/clark-nevada/s-nv-energy-conservation-code-2024"
            },
            {
                "title": "Southern Nevada HERS\u00ae Index (ERI) compliance path \u2014 Section 406 amendment",
                "note": "All Southern Nevada code jurisdictions amended Section 406 to accept a HERS Index / Energy Rating Index option as an alternative compliance path to prescriptive or performance methods. This is significant for builders who can't easily meet prescriptive envelope U-values \u2014 a third-party HERS rater can certify ERI compliance instead. Confirm the rater is RESNET-certified and that the AHJ accepts the ERI target in effect at the application date.",
                "applies_to": "Residential energy compliance in Clark County and Southern Nevada jurisdictions",
                "source": "https://www.resnet.us/articles/southern-nv-code-jurisdictions-accept-a-hers-index-option-to-energy-code/"
            },
            {
                "title": "No unified statewide residential building code \u2014 local AHJ controls",
                "note": "Nevada has statewide baseline codes enforced by the State Fire Marshal, but residential building permits for 1- and 2-family homes are handled entirely by local city and county building departments. This means there is no single state-issued residential permit; you must identify the correct AHJ (incorporated city OR unincorporated county) for each parcel and follow that jurisdiction's adopted code edition and amendments \u2014 they often differ between Clark, Washoe, and rural counties.",
                "applies_to": "All residential permit scoping in Nevada",
                "source": "https://permitsguide.com/nevada"
            },
            {
                "title": "City-or-county jurisdiction split under NRS Chapter 278",
                "note": "Under NRS 278 (Planning and Zoning), a parcel falls under the jurisdiction of the incorporated city OR the surrounding county, not both \u2014 overlap is rare and limited to specific extraterritorial agreements. Pull the parcel's APN from the county assessor and confirm whether it sits inside city limits before filing; submitting to the wrong AHJ is a common cause of weeks of delay because the receiving jurisdiction cannot transfer the application \u2014 it must be refiled.",
                "applies_to": "Determining the correct permitting AHJ for any Nevada parcel",
                "source": "https://www.leg.state.nv.us/nrs/nrs-278.html"
            },
            {
                "title": "State Public Works Division iWorQ portal \u2014 state-owned projects only",
                "note": "Construction on state-owned land or state buildings is permitted through the State Public Works Division (SPWD) via the iWorQ portal, NOT through local city/county building departments. SPWD reviews against the codes adopted for state facilities, which can lag local adoption. Private residential and commercial work never goes through SPWD; routing a private project to iWorQ will be rejected and you'll lose the filing window.",
                "applies_to": "Construction on State of Nevada-owned property and state facilities",
                "source": "https://publicworks.nv.gov/uploadedFiles/publicworksnvgov/content/Services/Permitting_Code_Enforcement/Permitting%20%20Code%20Enforcement%20Process.pdf"
            },
            {
                "title": "Floodplain management \u2014 NFIP coordination via Nevada Division of Water Resources",
                "note": "Nevada Division of Water Resources runs the state's floodplain management program in coordination with FEMA's National Flood Insurance Program; local jurisdictions adopt floodplain ordinances to remain NFIP-eligible. Before designing in or near a SFHA (Zone A/AE/AO/X-shaded), confirm the adopted base flood elevation, finished-floor freeboard requirement, and whether a CLOMR/LOMR will be required \u2014 many Nevada AHJs require finished floor at BFE+1 ft or higher and reject drawings without an elevation certificate at final.",
                "applies_to": "Construction within FEMA Special Flood Hazard Areas in Nevada",
                "source": "https://water.nv.gov/index.php/programs/floodplain-management"
            },
            {
                "title": "Water rights and federal-lands coordination \u2014 State Engineer per NRS 328",
                "note": "Per NRS 328.120, the State Engineer (within the Division of Water Resources) provides technical advice on water rights, reclamation, flood control, and watershed protection \u2014 and water rights are required separately from the building permit for new wells, surface diversions, or changes of use. On parcels adjacent to or crossing BLM/federal land, the project may also need a federal right-of-way or special-use authorization that runs in parallel with the local building permit and often takes longer than the construction permit itself.",
                "applies_to": "Projects requiring water rights or touching federal lands in Nevada",
                "source": "https://www.leg.state.nv.us/nrs/NRS-328.html"
            },
            {
                "title": "Clark County plan-review timing tiers \u2014 7-day commercial path requires <$100k valuation",
                "note": "Clark County operates tiered plan-review timelines: the Commercial 7-Day expedited path requires prior Zoning approval AND a project valuation under $100,000; everything else falls into Residential Minor, Standard Plan, or full Commercial review with longer queues. Inflate the valuation to be safe and you knock yourself out of the 7-day track \u2014 itemize labor and materials honestly to stay eligible, and have the Zoning sign-off in hand before submitting to plan review.",
                "applies_to": "Clark County commercial and residential plan submittals",
                "source": "https://www.clarkcountynv.gov/government/departments/building___fire_prevention/plan_review/plan-review-timelines"
            },
            {
                "title": "Northern Nevada amendments to the 2024 IECC \u2014 Elko County and rural N. NV",
                "note": "Northern/rural counties (Elko County and the Northern Nevada code-coordinated jurisdictions) adopted their own amendments to the 2024 IECC for residential energy efficiency. These amendments diverge from the Southern Nevada amendment package, so envelope assemblies, mechanical efficiencies, and duct-leakage targets that pass Clark County plan check may fail in Elko or vice versa. Pull the local amendment PDF for the specific AHJ and check it against the drawings before submission.",
                "applies_to": "Residential energy compliance in Elko County and Northern Nevada jurisdictions",
                "source": "https://www.elkocountynv.gov/calendar_app/departments/Building%20&%20Safety/2024%20Elko%20County%20Amendments.pdf?t=202601291912310"
            },
            {
                "title": "Architect/residential-designer notification under NRS 278.589",
                "note": "NRS 278.589 requires the city or county building official to notify the State Board of Architecture, Interior Design and Residential Design when certain plan submittals are made \u2014 this is the mechanism by which the Board polices unlicensed practice of architecture/residential design on Nevada permits. Plans submitted by an unlicensed designer for projects that exceed the residential-designer scope can trigger a Board complaint and stall the permit; confirm the designer's NSBAIDRD credentials and stamp authority before submission.",
                "applies_to": "Permit submittals using stamped or designer-prepared drawings in Nevada",
                "source": "https://www.leg.state.nv.us/nrs/nrs-278.html"
            }
        ]
    },
    "AR": {
        "name": "Arkansas expert pack",
        "expert_notes": [
            {
                "title": "Act 313 ADU statute \u2014 statewide ministerial path effective Jan 1, 2026",
                "note": "Arkansas Act 313 of 2025 requires every city and town to allow at least one accessory dwelling unit on any lot zoned for single-family use, with ministerial review and no discretionary public hearing. The law takes effect January 1, 2026, so ADU applications submitted to municipalities that have not yet updated their ordinance can still be processed under the state preemption. Cities cannot impose owner-occupancy requirements or off-street parking mandates that would block a qualifying ADU.",
                "applies_to": "ADU permit applications in any Arkansas municipality on or after 2026-01-01",
                "source": "https://www.housingwire.com/articles/arkansas-adu-law-sets-fast-approaching-housing-deadline/"
            },
            {
                "title": "2021 Arkansas Fire Prevention Code \u2014 bundles Building, Residential, and Fire codes",
                "note": "Arkansas does not adopt the IBC/IRC/IFC as separate documents. Instead the State Fire Marshal publishes the Arkansas Fire Prevention Code (AFPC), Vol. I-III, based on the 2021 IBC/IRC/IFC with Arkansas amendments, on a three-year cycle. Each county, city, or political subdivision that issues building permits may only adopt and enforce the AFPC \u2014 local 'home-grown' building codes are preempted. Confirm which AFPC edition the AHJ is currently enforcing before sealing drawings.",
                "applies_to": "All commercial and residential permit submittals statewide",
                "source": "https://sas.arkansas.gov/wp-content/uploads/CurrentCodes072023.pdf"
            },
            {
                "title": "2021 Arkansas Energy Code adoption is mandatory for permit-issuing jurisdictions",
                "note": "Per C101.6 of the 2021 Arkansas Energy Code, all counties, cities, or municipalities that issue building permits for new building construction are required to adopt the state energy code \u2014 it is not optional. The 2021 Arkansas IRC has been in effect since January 1, 2023. Residential plans must show compliance through prescriptive, UA-tradeoff, or performance (ResCheck/REM-Rate) paths; missing envelope U-values or duct-leakage testing notes are common rejection reasons.",
                "applies_to": "New construction and additions in Arkansas jurisdictions that issue building permits",
                "source": "https://www.adeq.state.ar.us/energy/initiatives/pdfs/DRAFT%202021%20Arkansas%20Energy%20Code%20Amendments%20and%20Supplements%20vMar3.pdf"
            },
            {
                "title": "No statewide residential building code in unincorporated areas without local adoption",
                "note": "Arkansas does not impose the AFPC residential provisions on counties or unincorporated areas that have not affirmatively adopted a building permit program. Many rural Arkansas counties issue no building permits at all for 1- and 2-family dwellings, though septic (ADH) and electrical (state inspector) permits are still required. Confirm whether the parcel sits inside a city limit, a planning-area extraterritorial jurisdiction, or true unincorporated county before assuming a building permit is needed.",
                "applies_to": "Single-family and duplex projects in rural / unincorporated Arkansas",
                "source": "https://up.codes/s/locally-adopted-codes"
            },
            {
                "title": "Arkansas Contractors Licensing Board \u2014 $50,000 commercial / $2,000 residential thresholds",
                "note": "The Arkansas Contractors Licensing Board (ACLB) requires a Commercial Contractor license for any project of $50,000 or more (labor + materials) and a Residential Builder/Remodeler license for residential projects over $2,000. Working without the proper license at or above these thresholds blocks lien rights and is enforced by ACLB civil penalties. Verify the contractor's license number, classification, and good-standing status before contracting.",
                "applies_to": "All paid construction work in Arkansas at or above the dollar thresholds",
                "source": "https://www.procore.com/library/arkansas-contractors-license"
            },
            {
                "title": "Separate state HVAC/R, electrical, and plumbing licenses (not under ACLB)",
                "note": "HVAC/R contractors are licensed by the Arkansas Department of Labor and Licensing, Code Enforcement Division \u2014 NOT by the Contractors Licensing Board. Electrical contractors are licensed through the state electrical board (501-682-4549) and plumbing/gas through the plumbing & HVAC/R section. A general residential builder cannot self-perform HVAC, electrical, or plumbing work without holding (or subcontracting to a holder of) the matching trade license, even on their own permit.",
                "applies_to": "Any project involving mechanical, electrical, plumbing, or gas work",
                "source": "https://labor.arkansas.gov/labor/code-enforcement/hvac-r/"
            },
            {
                "title": "Arkansas residential builder license \u2014 90-day validity on initial issuance",
                "note": "Once an Arkansas Contractors Licensing Board residential or commercial license is initially issued, it is only valid for 90 days unless the contractor completes the renewal/qualifying-party requirements within that window. A license that has lapsed past the 90-day initial window will fail plan-check verification at most municipal building departments. Always pull a fresh license-status check on the ACLB lookup the day you submit, not just when you sign the contract.",
                "applies_to": "Newly licensed Arkansas contractors and their first projects",
                "source": "https://labor.arkansas.gov/licensing/arkansas-contractors-licensing-board/apply-for-contractors-license-registration/"
            },
            {
                "title": "Floodplain Development Permit required separately from building permit",
                "note": "For any site located in a FEMA 100-year (Special Flood Hazard Area) floodplain, ADEQ and most AHJs require a Floodplain Development Permit issued by the county or city Floodplain Administrator BEFORE the building permit is approved. This is a parallel filing \u2014 the building department will not issue under the AFPC unless the floodplain permit (or a no-rise / LOMA letter) is attached. Elevation certificates are typically required for habitable structures with finished floors at or below BFE+1.",
                "applies_to": "Any construction inside a FEMA SFHA in Arkansas",
                "source": "https://www.adeq.state.ar.us/water/permits/pdfs/apppitdrilling_permit_p1-5.pdf"
            },
            {
                "title": "AGFC Rule 19.13 \u2014 permit required for work near Commission waterbodies",
                "note": "Arkansas Game & Fish Commission Rule 19.13 requires a separate AGFC permit for activities on or adjacent to Commission-owned or Commission-managed waterbodies, including shoreline stabilization, lake dredging, shoreline deepening, and herbicide/pesticide use. This is in addition to any USACE Section 404 wetlands permit and ADEQ Section 401 water-quality certification. Skipping the AGFC filing is a common cause of stop-work orders on lakeside docks, retaining walls, and boathouses.",
                "applies_to": "Construction adjacent to AGFC lakes, WMAs, or managed waterbodies",
                "source": "https://apps.agfc.com/regulations/19.13/"
            },
            {
                "title": "ADEQ permit for land application of drilling/oilfield fluids",
                "note": "The Arkansas Department of Environmental Quality (ADEQ) Water Division requires a separate permit for land application of drilling fluids produced during oil and gas exploration and production, in addition to any USACE or AGFC permits. Sites in 100-year floodplains must attach the county Floodplain Development Permit to the ADEQ application. This catches residential GCs who do not realize that pit-disposal of mud or fluids triggers a state environmental filing.",
                "applies_to": "Sites involving drilling-fluid disposal, including some rural residential developments",
                "source": "https://www.uaex.uada.edu/publications/pdf/FSPPC103.pdf"
            },
            {
                "title": "Net-metering interconnection \u2014 Standard Interconnection Agreement is mandatory",
                "note": "Per 23 CAR \u00a7 457-301, an Arkansas net-metering customer (and the facility owner if different) must execute the PSC-approved Standard Interconnection Agreement with the serving electric utility before the system is energized. This is a parallel filing to the building/electrical permit \u2014 the AHJ will sign off on the install, but the utility will not authorize PTO without the signed SIA and any required external disconnect. Submit the interconnection application early; investor-owned utilities typically take 4-8 weeks to approve.",
                "applies_to": "Residential and small commercial solar PV + battery installations",
                "source": "https://codeofarrules.arkansas.gov/Rules/Rule?levelType=section&titleID=23&chapterID=40&subChapterID=52&partID=916&subPartID=4325&sectionID=27191"
            },
            {
                "title": "Solar PV \u2014 both building AND electrical permits required",
                "note": "Arkansas homeowners installing solar PV must obtain both a building permit (for structural / roof attachment review) and a separate electrical permit before installation begins. The electrical permit is administered through the state electrical inspector in jurisdictions without a local electrical department. Plan submittals should include rapid-shutdown labeling per 2020 NEC 690.12 and a one-line diagram showing AC disconnect location accessible to the utility.",
                "applies_to": "Residential solar PV and battery storage installations statewide",
                "source": "https://www.solarpermitsolutions.com/blog/navigate-arkansas-solar-permitting-2025-fees-timelines-net-metering-changes"
            },
            {
                "title": "Municipal utility interconnection agreement (e.g., Clarksville Connected Utilities)",
                "note": "Arkansas customers served by a municipal electric utility (Clarksville, Conway Corp, Jonesboro CWL, Paragould L&W, etc.) sign an interconnection agreement directly with that municipal utility \u2014 not with the investor-owned utility's PSC-tariffed process. Terms (system-size cap, REC ownership, external disconnect requirements) can vary materially from Entergy/SWEPCO/OG&E rules. Confirm the serving utility on the meter base before quoting net-metering credit value.",
                "applies_to": "Solar / DER projects in municipal utility service territories",
                "source": "https://www.clarksvilleconnected.net/305/Interconnection-Agreement"
            },
            {
                "title": "Arkansas is a buyer-beware state \u2014 no statutory seller disclosure form",
                "note": "Arkansas has no statute requiring a seller of residential real estate to deliver a property condition disclosure statement to the buyer (unlike most states). The Arkansas Real Estate Commission notes that disclosure is industry custom but not a legal mandate, and there is no state-level requirement to disclose unpermitted work or expired permits at sale. Buyers and lenders should pull a permit history from the AHJ themselves rather than relying on a seller form.",
                "applies_to": "Residential resale transactions and post-sale unpermitted-work disputes",
                "source": "https://arec.arkansas.gov/news_post/is-property-condition-disclosure-required-by-law/"
            },
            {
                "title": "Arkansas Forward \u2014 expedited permitting for qualifying economic-development projects",
                "note": "Governor Sanders' Arkansas Forward executive order directs state agencies to consolidate and accelerate permit review timelines for qualifying economic-development projects. This affects state-issued environmental, water, and air permits but does NOT shorten municipal building-permit review for typical residential work. Use this as escalation leverage only on industrial or large-scale commercial projects coordinated through AEDC.",
                "applies_to": "Large commercial / industrial development projects with state-permit components",
                "source": "https://governor.arkansas.gov/news_post/sanders-signs-executive-order-to-speed-permitting-for-economic-development-projects/"
            }
        ]
    },
    "KS": {
        "name": "Kansas expert pack",
        "expert_notes": [
            {
                "title": "Kansas is home rule \u2014 no statewide residential building code",
                "note": "Kansas does not adopt a statewide residential building code; responsibility for code adoption, permitting, and inspection rests entirely with local jurisdictions (city or county). Some Kansas counties have no building code at all, while others adopt the IRC/IBC at varying edition years. Always confirm the AHJ's adopted code edition and whether the parcel falls inside a city's building-permit jurisdiction or in unincorporated county territory before quoting a residential job.",
                "applies_to": "All residential construction statewide",
                "source": "https://permitsguide.com/kansas"
            },
            {
                "title": "No statewide HVAC, electrical, or general-contractor license",
                "note": "Kansas has no state board for HVAC, electrical, plumbing, or general contracting and issues no statewide trade licenses or technician certifications. Licensing is handled at the city or county level \u2014 for example KCMO Permits Division, Johnson County Contractor Licensing, and Sedgwick County MABCD each maintain their own license classes, exams, and reciprocity rules. Verify the contractor holds the correct local license for the specific AHJ before signing the contract; a license valid in one Kansas jurisdiction is not automatically valid in the next.",
                "applies_to": "All trade contracting statewide (HVAC, electrical, plumbing, general)",
                "source": "https://www.budgetheating.com/blog/kansas-hvac-regulatory-oversight-complete-guide/?srsltid=AfmBOopzQSvDClz-VSRcfW59ob1tAIT3lvbhdXYTw2D1OO-LkQpvV23w"
            },
            {
                "title": "State-adopted 2006 IECC baseline \u2014 local jurisdictions may exceed",
                "note": "The Kansas Corporation Commission's Kansas Energy Office notes that the State has formally adopted only the 2006 International Energy Conservation Code, while local jurisdictions retain authority to adopt and enforce newer editions. As a result, residential energy compliance in Kansas can range from a 2006 IECC envelope (in jurisdictions that never updated) to 2021 IECC with local amendments in major metros. Confirm the AHJ's enforced IECC edition before sizing insulation, fenestration U-factors, and duct/envelope air leakage targets.",
                "applies_to": "Residential energy compliance statewide",
                "source": "https://www.kcc.ks.gov/kansas-energy-office/ks-building-energy-codes"
            },
            {
                "title": "Kansas City, MO 2021 IECC with Ordinance 260144 amendments",
                "note": "KCMO enforces the 2021 IECC for new residential construction but adopted Ordinance No. 260144 to add compliance options and modified wall-insulation requirements after a contentious rollout. The amendments live inside the Kansas City Building and Rehabilitation Code (KCBRC) and change how prescriptive vs. performance vs. ERI paths apply, so use the local KCBRC energy chapter \u2014 not the unamended 2021 IECC \u2014 when running REScheck or specifying assemblies inside KCMO city limits.",
                "applies_to": "New residential construction inside KCMO city limits",
                "source": "https://www.kcmo.gov/city-hall/departments/city-planning-development/energy-code-update"
            },
            {
                "title": "HB 2088 Fast-Track Permits Act \u2014 local permit shot clock for SFR",
                "note": "Kansas HB 2088 (the Fast-Track Permits Act, 2025-26 session) requires a local government or local governmental authority to approve or deny a building permit for improvement of single-family residential within a statutorily defined window. If the AHJ misses the deadline, the applicant has statutory grounds to escalate or seek deemed-approved relief. Confirm the AHJ's logged 'complete application' date in writing \u2014 that date, not the original drop-off, starts the clock.",
                "applies_to": "Single-family residential building permits statewide",
                "source": "https://www.kslegislature.gov/li/b2025_26/measures/documents/summary_hb_2088_2025"
            },
            {
                "title": "Ministerial approval for housing that already meets zoning",
                "note": "Kansas's new housing-permit law requires local governments to quickly approve housing developments that already meet the criteria outlined in their zoning codes \u2014 i.e., qualifying projects move through a ministerial path rather than a discretionary public-hearing review. If the project meets the existing objective standards (use, setbacks, height, lot coverage, parking), the city cannot demand a rezoning, special use permit, or hearing as a precondition to issuance. Document objective compliance in the application packet so a reviewer cannot reroute the project into discretionary review.",
                "applies_to": "Residential infill that complies with existing zoning standards",
                "source": "https://pacificlegal.org/press-release/kansas-law-cuts-red-tape-opens-door-for-more-housing/"
            },
            {
                "title": "Johnson County contractor license classes \u2014 Class A excludes trades",
                "note": "Johnson County issues separate contractor classes; the Class A (highest tier) license does NOT entitle the holder to perform HVAC, plumbing, electrical, or fire-protection work \u2014 those require the corresponding trade-specific licenses. Class B (Building) and lower classes have additional scope limits. Pulling a Class-A-only permit and attempting to subcontract trade work without separately licensed subs is a common rejection cause; verify each trade subcontractor is independently licensed under the correct Johnson County class.",
                "applies_to": "Contractor licensing in Johnson County, KS",
                "source": "https://www.jocogov.org/department/contractor-licensing/new-license-license-types"
            },
            {
                "title": "Sedgwick County MABCD homeowner exam requirement",
                "note": "The Metropolitan Area Building and Construction Department (MABCD), which serves Sedgwick County and Wichita-area cities, requires homeowners doing their own electrical, plumbing, water-heater, or mechanical work on their owner-occupied home to pass a competency exam before pulling the permit. Homeowner permits are not automatic and cannot be transferred to a contractor mid-project. Schedule the homeowner exam before the permit drop or use a licensed trade contractor.",
                "applies_to": "Homeowner-pulled trade permits in Sedgwick County / MABCD jurisdiction",
                "source": "https://www.sedgwickcounty.org/mabcd/contractor-licensing/"
            },
            {
                "title": "KCMO contractor licensing administered by Permits Division",
                "note": "Inside Kansas City, MO city limits, the City Planning and Development Department's Permits Division administers contractor licensing registration and renewals \u2014 a separate process from MO state professional registration and from any reciprocal license held elsewhere. Registration must be active on the date the permit is issued, not just on the bid date; an expired KCMO registration will block permit issuance even if the underlying state license is current. Confirm registration status in the KCMO contractor portal before applying.",
                "applies_to": "Contracted work inside KCMO city limits",
                "source": "https://www.kcmo.gov/city-hall/departments/city-planning-development/contractor-licensing"
            },
            {
                "title": "Kansas Department of Agriculture \u2014 Stream and Floodplain Permits",
                "note": "Separate from the local building permit, the Kansas Department of Agriculture Division of Water Resources requires a Stream and Floodplain Permit for: construction, modification, or repair of a dam 25 ft or more in height (or 6 ft or more in height that meets storage thresholds), and certain stream and floodplain works. This is a parallel state-level filing \u2014 the AHJ building permit does not satisfy DWR review. File with KDA-DWR in parallel with the local floodplain-development permit so neither agency holds up the other.",
                "applies_to": "Construction in or near streams, dams, and floodplains",
                "source": "https://www.agriculture.ks.gov/divisions-programs/division-of-water-resources/water-structures/stream-and-floodplain-permits"
            },
            {
                "title": "K.S.A. 12-766 \u2014 local floodplain regs must meet NFIA minimums",
                "note": "Under K.S.A. 12-766, any local floodplain regulations adopted in Kansas shall comply with the minimum requirements of the National Flood Insurance Act of 1968 (42 U.S.C. \u00a7 4001 et seq.). That means lowest-floor elevation, flood-resistant materials, and venting requirements at least as strict as 44 CFR \u00a760.3 apply even in jurisdictions that otherwise have no building code. Pull the FEMA FIRM panel for the parcel before design \u2014 a Zone A or AE finding triggers state-mandated NFIP-compliant construction regardless of how minimal the local code otherwise is.",
                "applies_to": "Construction in mapped Special Flood Hazard Areas statewide",
                "source": "https://www.kslegislature.gov/li/b2025_26/statute/012_000_0000_chapter/012_007_0000_article/012_007_0066_section/012_007_0066_k/"
            },
            {
                "title": "Floodplain Development Permit required before any site work",
                "note": "In counties such as Jefferson County, KS, no development of any kind is permitted in the FP Floodplain District except through issuance of a Floodplain Development Permit, granted by the County Floodplain Administrator. 'Development' is broad \u2014 fill, grading, pads, sheds, fences, and even substantial improvements to existing structures all require the permit before site disturbance. Pull the floodplain-development permit before mobilizing equipment; starting work first is a common violation that triggers stop-work and elevation-certificate retrofits.",
                "applies_to": "Any site work inside a county-mapped floodplain district",
                "source": "https://www.jfcountyks.com/DocumentCenter/View/436"
            },
            {
                "title": "Kansas seller disclosure \u2014 customary, not statutorily required",
                "note": "Kansas law does not specifically require a written property disclosure form when selling a home, but providing one is customary and the broker community treats it as standard practice. Practically, this means unpermitted work and open permits do NOT have to be disclosed by statute the way they do in many other states \u2014 but failing to disclose known defects can still expose the seller to common-law fraud and misrepresentation claims. Resolve open permits and finalize inspections before listing rather than relying on the absence of a statutory disclosure form.",
                "applies_to": "Resale of Kansas residential property after permitted/unpermitted work",
                "source": "https://www.nolo.com/legal-encyclopedia/selling-kansas-home-what-are-my-disclosure-obligations.html"
            },
            {
                "title": "Kansas Business One Stop \u2014 trade-license patchwork by city",
                "note": "The state's Construction and Contracting Starter Kit confirms that beyond any general contractor registration, specific licenses may be required for electrical, plumbing, HVAC, or other construction trades, and explicitly lists per-city regimes (e.g., McPherson, Newton, and other municipal contractor licenses). Because no state reciprocity covers these, working across multiple Kansas cities on a single project can require holding three or four parallel municipal licenses simultaneously. Map every AHJ the job touches and pre-register in each before bidding multi-jurisdiction work.",
                "applies_to": "Multi-jurisdiction contracting across Kansas cities and counties",
                "source": "https://ksbiz.kansas.gov/business-starter-kit/construction/"
            }
        ]
    },
    "MS": {
        "name": "Mississippi expert pack",
        "expert_notes": [
            {
                "title": "MSBOC residential builder license \u2014 $50,000 threshold",
                "note": "The Mississippi State Board of Contractors (MSBOC) requires a Residential Builder license for any residential construction or remodel where the total contract is $50,000 or more. Below that threshold a license is not required by the state, but the contractor still needs general liability coverage and any local business license. Verify the license number and classification on MSBOC before signing \u2014 bidding or performing covered work without one voids the contract and exposes the contractor to disciplinary fines.",
                "applies_to": "Residential construction and remodel contracts \u2265 $50,000 in Mississippi",
                "source": "https://mycontractorslicense.com/mississippi-residential-builder/?srsltid=AfmBOoqQxrDOZJhCK-7zX1hroZrK08wHYTo0zRjUtRRVFwUsUy8pfwVn"
            },
            {
                "title": "MSBOC commercial + roofer licensing requirement",
                "note": "MSBOC regulates both commercial and residential contractors and explicitly requires roofers to be licensed by the Board to bid on or perform work in Mississippi. Run the contractor through the MSBOC license lookup before issuing a contract \u2014 an out-of-state roofer must hold a current Mississippi State Board of Contractors license, not just a license from their home state.",
                "applies_to": "Commercial GC work and all roofing contracts statewide",
                "source": "https://www.msboc.us/"
            },
            {
                "title": "Residential HVAC / Plumbing classification \u2014 SC exam required",
                "note": "MSBOC's residential application (rev. 10-2025) requires applicants for the Residential HVAC or Residential Plumbing classifications to have passed the Standards of Conduct (SC) exam in addition to the trade exam. Out-of-state applicants seeking a Mississippi license go through the same MSBOC application. Confirm the specific classification on the license matches the scope of work \u2014 a residential builder license alone does not authorize HVAC or plumbing work.",
                "applies_to": "Residential HVAC and plumbing contractors applying for or holding a MSBOC license",
                "source": "https://www.msboc.us/wp-content/uploads/2025/11/RESIDENTIAL-APPLICATION-updated-10-2025.pdf"
            },
            {
                "title": "2024 IBC adoption \u2014 effective Jan 1, 2025 statewide",
                "note": "Mississippi adopted the 2024 International Building Code without amendments, effective January 1, 2025. Drawings and structural calculations submitted after that date should reference the 2024 IBC, not the 2018 or 2021 edition. Confirm with the AHJ which edition applies to permits filed before the effective date \u2014 some local plan checkers continue to accept submissions under the prior edition for a transition window.",
                "applies_to": "Commercial and multifamily permit applications statewide",
                "source": "https://up.codes/viewer/mississippi/ibc-2024"
            },
            {
                "title": "2024 IRC adoption \u2014 effective Jan 1, 2025",
                "note": "Mississippi adopted the 2024 International Residential Code effective January 1, 2025. The state edition includes Appendix NC (Zero Net Energy Residential Building Provisions) and other appendices that are only enforceable if the local AHJ specifically adopts them. Check the city/county ordinance for which IRC appendices have been pulled in before relying on optional provisions like ND or NC.",
                "applies_to": "1- and 2-family dwellings and townhouses up to 3 stories",
                "source": "https://up.codes/viewer/mississippi/irc-2024"
            },
            {
                "title": "Local code adoption is discretionary \u2014 no universal residential code",
                "note": "Per Miss. Code \u00a7 21-19-25, any municipality MAY adopt building, plumbing, electrical, and gas codes at the discretion of its governing authority \u2014 adoption is not automatic. The Mississippi Building Code Council updates model codes on a 3-year cycle, but local jurisdictions decide whether to enforce them. Always confirm directly with the city or county which codes (and which edition) are actively enforced before designing \u2014 some smaller jurisdictions enforce no building code at all.",
                "applies_to": "All municipalities; verify before assuming a code edition applies",
                "source": "https://law.justia.com/codes/mississippi/title-21/chapter-19/section-21-19-25/"
            },
            {
                "title": "Counties may opt out of permitting entirely (HB 937 / \u00a7 19-5-9)",
                "note": "Under Miss. Code \u00a7 19-5-9 (as amended by 2024 HB 937 / 2022 HB 1163), county boards of supervisors may choose whether to require permits for construction in unincorporated areas, and certain counties can opt out of permitting altogether. This means an unincorporated parcel may have NO county building permit requirement \u2014 but state licensing, MDEQ stormwater, and FEMA floodplain rules still apply. Verify with the county Chancery Clerk or Building Department whether permits are required before assuming none are needed.",
                "applies_to": "Unincorporated county jurisdictions for residential construction",
                "source": "https://billstatus.ls.state.ms.us/documents/2024/html/HB/0900-0999/HB0937IN.htm"
            },
            {
                "title": "Energy code \u2014 ASHRAE 90.1-2010 only, no statewide residential IECC",
                "note": "Mississippi's only formal statewide energy code adoption is ASHRAE 90.1-2010 (effective July 2013) for commercial buildings; there is no set schedule for further updates and no statewide residential IECC mandate. Residential energy compliance is therefore driven by whatever the local jurisdiction has adopted (often the IRC chapter 11 or nothing). Do not assume an IECC 2021/2024 envelope or duct-leakage test is required \u2014 confirm at the AHJ before specifying.",
                "applies_to": "Residential and commercial energy compliance scoping",
                "source": "https://www.energycodes.gov/status/states/mississippi"
            },
            {
                "title": "MDEQ Construction Stormwater \u2014 Large Construction General Permit at 5+ acres",
                "note": "MDEQ's Environmental Permits Division requires Construction Stormwater general permit coverage for sites disturbing 5 acres or more (Large Construction GP), with a Small Construction GP for 1\u20135 acres. Coverage and the SWPPP must be filed electronically through MDEQ before earthwork begins \u2014 this is a SEPARATE filing from the local building permit and is commonly missed on subdivision and commercial pad work. Operating without coverage triggers stop-work and per-day penalties.",
                "applies_to": "Site work disturbing \u22651 acre (separate large vs small permit tiers)",
                "source": "https://www.mdeq.ms.gov/permits/environmental-permits-division/applications-forms/generalpermits/construction-stormwater/"
            },
            {
                "title": "MDMR Coastal Wetlands permit \u2014 separate filing in 3 coastal counties",
                "note": "Any project impacting wetlands within the Mississippi Coastal Zone (Hancock, Harrison, and Jackson counties) must file a Wetlands Permit application electronically through the Mississippi Department of Marine Resources (MDMR) Wetlands Permitting Portal. This is independent of the local building permit and the federal Section 404/401 process \u2014 the COE 404 permit will not issue until MDEQ provides the 401 water-quality certification, and MDMR has separate coastal jurisdiction. Plan 60\u2013120 days minimum for coastal projects involving fill, piers, or bulkheads.",
                "applies_to": "Coastal-zone construction with any wetland or tidal-water impact",
                "source": "https://dmr.ms.gov/permitting/"
            },
            {
                "title": "Floodplain Management Permit required in SFHA Zones A/AE/V/VE",
                "note": "Per the State of Mississippi Floodplain Manual, any structure in a Special Flood Hazard Area (Zone A, AE, V, or VE) must have a Floodplain Management Permit Application on file with the local floodplain administrator before construction. This is in addition to the building permit and triggers elevation certificates, lowest-floor requirements, and (in V/VE) breakaway-wall and pile-foundation rules. Pull the FIRM panel before quoting any Gulf Coast or riverine job \u2014 V-zone construction roughly doubles structural cost.",
                "applies_to": "New construction or substantial improvement in a FEMA SFHA",
                "source": "https://www.sos.ms.gov/adminsearch/ACCode/00000699c.pdf"
            },
            {
                "title": "Distributed Generation interconnection \u2014 PSC MDGIR rule (separate from permit)",
                "note": "Solar PV, battery storage, and other distributed generation under the Mississippi Public Service Commission's jurisdiction must follow the Mississippi Distributed Generator Interconnection and Net Metering Rule (MDGIR), which sets technical and procedural requirements. This means a SEPARATE interconnection application to the serving utility (e.g., Entergy Mississippi Level 1 application for inverter-based systems \u226425 kW) on top of the building/electrical permit. Net metering eligibility, system-size caps, and approval-to-energize sign-off all run through the utility, not the AHJ.",
                "applies_to": "Residential and small commercial solar PV and battery interconnections",
                "source": "https://www.psc.ms.gov/sites/default/files/Documents/Net%20Meeting%20and%20Interconnection%20Rules.pdf"
            },
            {
                "title": "Entergy Mississippi Level 1 interconnection application \u2014 inverter-based \u226425 kW",
                "note": "For investor-owned utility customers, Entergy Mississippi requires the Level 1 Standard Interconnection Agreement form for inverter-based DER systems up to 25 kW. The signed agreement and one-line diagram must be submitted to Entergy before permission-to-operate is granted, and meter swap / PTO typically lags AHJ final inspection by 2\u20136 weeks. Build that lag into the customer schedule \u2014 final payment milestones tied to PTO will slip if the utility application is filed late.",
                "applies_to": "Residential rooftop solar in Entergy Mississippi service territory",
                "source": "https://www.entergymississippi.com/wp-content/uploads/Interconnection_App_and_Agreement_Level_1.pdf"
            },
            {
                "title": "Local plumbing-permit license attachment (Olive Branch model)",
                "note": "Many Mississippi municipalities (Olive Branch is a documented example) require the contractor's state license to be physically submitted at the time the plumbing permit is applied for, and explicitly require HVAC contractors to hold a Mississippi State Contractors license. A common rejection at intake is missing or expired license documentation attached to the application. Pull a fresh MSBOC license printout the day of submittal to avoid an automatic counter-rejection.",
                "applies_to": "Trade permit submittals in municipalities requiring license-on-file",
                "source": "https://www.obms.us/DocumentCenter/View/328/Contractor-License-Requirements-PDF"
            },
            {
                "title": "MSBOC license classifications \u2014 match scope before bidding",
                "note": "MSBOC issues distinct classifications (Residential Builder, Residential Remodeler, Residential HVAC, Residential Plumbing, plus the commercial classifications administered by MSBC) and the classification on the license must match the scope of work being bid. The Mississippi State Board of Health regulates separate plumbing/gas trade licensing in parallel with MSBOC contractor licensing. A residential builder cannot self-perform HVAC or plumbing without the matching trade classification or a properly licensed subcontractor on the job.",
                "applies_to": "Scope-of-work / trade matching for any MSBOC-licensed contractor",
                "source": "https://mississippicontractorauthority.com/mississippi-contractor-license-types"
            }
        ]
    },
    "NM": {
        "name": "New Mexico expert pack",
        "expert_notes": [
            {
                "title": "CID is the licensing authority \u2014 GB-98, GA-98, EE-98, MM-98 classifications",
                "note": "The New Mexico Construction Industries Division (CID) under the Regulation and Licensing Department issues all contractor licenses statewide. Residential general building work falls under GB-98, general engineering under GA-98, electrical under EE-98, and mechanical/HVAC/plumbing under MM-98 (with sub-classifications like MS-3 plumbing or MS-4 HVAC). Verify the qualifying party (QP) license is active and matches the trade scope before quoting; an expired or mismatched classification will block permit issuance.",
                "applies_to": "All licensed construction trades in New Mexico",
                "source": "https://www.rld.nm.gov/construction-industries/"
            },
            {
                "title": "QP exam process \u2014 PSI application with $36 fee and notarized signature",
                "note": "To qualify a CID license, the qualifying party must submit a completed QP application and Work Experience Affidavit to PSI with a $36 fee, and the applicant's signature must be notarized. This is in addition to the trade exam itself and the bond/insurance requirements. New crews onboarding a QP should budget 8\u201312 weeks for the affidavit-to-exam-to-license cycle before they can pull permits.",
                "applies_to": "Contractors qualifying or transferring a CID license",
                "source": "https://www.rld.nm.gov/construction-industries/apply-for-a-construction-industries-license/"
            },
            {
                "title": "2021 NM Residential Code \u2014 effective July 14, 2023 with Dec 14, 2023 grace cutoff",
                "note": "The 2021 New Mexico Residential Building Code took effect July 14, 2023, with a grace period through December 14, 2023 during which permits could be issued under either the prior or new edition at AHJ discretion. After December 14, 2023, all residential permit applications must be reviewed under the 2021 NMRC. Confirm with the local building department which edition governs any project whose drawings predate the cutoff before resubmitting plans.",
                "applies_to": "Residential permit applications statewide",
                "source": "https://www.icc-nta.org/code-update/2021-new-mexico-residential-building-code-effective-date-july-14-2023/"
            },
            {
                "title": "2021 NM Residential Energy Conservation Code \u2014 effective Jan 30, 2024",
                "note": "The 2021 New Mexico Residential Energy Conservation Code (14.7.6 NMAC) is the current statewide minimum, effective January 30, 2024, replacing the 2018 NMECC. It applies to new construction and to alterations that change the conditioned envelope. Plan check rejections are common when the energy compliance path (prescriptive vs. UA tradeoff vs. performance) is not declared on the cover sheet \u2014 pick a path before submitting.",
                "applies_to": "New residential construction and conditioned-space alterations",
                "source": "https://up.codes/viewer/new_mexico/iecc-2021"
            },
            {
                "title": "NMRC 105.2 \u2014 permit exemptions for small accessory structures",
                "note": "Per New Mexico Residential Code 105.2, a building permit is not required for one-story detached accessory buildings used as tool/storage sheds and similar uses below the area threshold, plus fences under the height limit, retaining walls under the height limit, sidewalks/driveways on grade, and like-for-like cabinet/finish replacement. This does NOT exempt electrical, plumbing, or mechanical permits, which are pulled separately through CID or the local AHJ. Always confirm the local jurisdiction has not amended 105.2 downward \u2014 several NM counties have.",
                "applies_to": "Small accessory structures and minor residential work",
                "source": "https://www.rld.nm.gov/wp-content/uploads/2021/06/BLDG-RES-GUIDE-jrr-03-09-12.pdf"
            },
            {
                "title": "County permit jurisdiction outside municipalities \u2014 NMSA \u00a73-21-2",
                "note": "Under New Mexico Statutes \u00a73-21-2, a county may require building permits in unincorporated areas only if it has adopted proper zoning ordinances; otherwise CID is the default permitting authority for unincorporated parcels. This produces a three-way AHJ split: incorporated cities run their own building departments, zoned counties run county permit offices, and the rest defaults to CID. Identify the AHJ from the parcel address before filling out any application \u2014 submitting to the wrong office is the single most common rejection cause on rural NM jobs.",
                "applies_to": "Projects outside municipal boundaries",
                "source": "https://law.justia.com/codes/new-mexico/chapter-3/article-21/section-3-21-2/"
            },
            {
                "title": "State-CID vs. municipal permit split \u2014 confirm AHJ before filing",
                "note": "Permitting requirements in New Mexico vary materially by jurisdiction: most large cities (Albuquerque, Santa Fe, Las Cruces, Rio Rancho, Farmington) run their own building departments and accept city permit forms, while smaller towns and most unincorporated areas route through CID. The application package (city, county, or CID) determines the form set, the plan-review timeline, and the inspector pool. Pull the correct form from the AHJ website rather than reusing a CID packet \u2014 fields, attachments, and stamping requirements differ.",
                "applies_to": "All residential and light-commercial permit applications",
                "source": "https://www.permitflow.com/state/new-mexico"
            },
            {
                "title": "OSE water rights filing is a parallel, separate process from the building permit",
                "note": "Any new well, change in point of diversion, or change in place/purpose of use must be filed with the New Mexico Office of the State Engineer (OSE) using the Ground Water or Surface Water Filing Forms \u2014 this is independent of the building/CID permit and is required before a meter, well, or septic system tied to a new water source can be approved. OSE forms include WR-01 through WR-19 series for various filings. Build OSE timelines into the project schedule; protests can extend approvals well past 90 days.",
                "applies_to": "New wells, septic, and any change to existing water rights",
                "source": "https://www.ose.nm.gov/WR/forms.php"
            },
            {
                "title": "Pueblo and federally reserved water rights \u2014 \"time immemorial\" priority",
                "note": "For Pueblo water rights the priority date is \"time immemorial,\" and federally reserved water rights also predate most state-issued permits. On parcels near or within Pueblo lands or federal reservations, an OSE-issued water right can be subordinated to a senior Pueblo claim, which means a constructed well may be curtailed even after permits are pulled. Run a title and water-adjudication check before designing any project that depends on new groundwater diversion in the middle Rio Grande, Jemez, or San Juan basins.",
                "applies_to": "Construction near Pueblo lands or in adjudicated basins",
                "source": "https://www.nmlegis.gov/handouts/EDPC%20071422%20Item%205%20OSE%20water%20law.pdf"
            },
            {
                "title": "OSE Acequia Construction Program \u2014 grant funding for diversion/headgate work",
                "note": "The New Mexico Interstate Stream Commission's Acequia Construction Program funds up to 90% of project cost (max $150,000 per project) for headgates, diversion structures, and acequia infrastructure, with total construction costs capped at $167,000 under the program. Acequia parciantes and mayordomos planning culvert, headgate, or ditch-lining work should apply for ISC funding before pulling local permits, since approval terms can dictate engineering scope. Construction touching an acequia easement also requires sign-off from the acequia commission.",
                "applies_to": "Acequia infrastructure and adjacent construction",
                "source": "https://www.ose.nm.gov/Acequias/isc_acequiasConstruction.php"
            },
            {
                "title": "Flood Hazard Overlay District triggers separate floodplain review",
                "note": "Parcels mapped in a local Flood Hazard Overlay District (e.g., \u00a717-4-2.2 in Albuquerque-area zoning) require a separate floodplain development permit on top of the building permit, with elevation certificates, lowest-floor elevation above BFE, and venting for any enclosed area below the BFE. The overlay applies to any structure, fill, grading, or substantial improvement (\u226550% of pre-improvement value) within the SFHA. Pull the FIRM panel and overlay map at the start of design; retrofits can balloon if the elevation certificate isn't built into the foundation plan.",
                "applies_to": "Construction within mapped Flood Hazard Overlay Districts",
                "source": "https://experience.arcgis.com/experience/5978c68aeb2f495db07ddab1c3aa1048/page/Flood-Hazard-Overlay-District"
            },
            {
                "title": "Wildfire Community Mitigation Maps \u2014 flame/ember zones drive WUI requirements",
                "note": "The NM Energy, Minerals and Natural Resources Department (Forestry Division) publishes Community Mitigation Maps with county-level flame and ember zone data drawn from CWPPs and insurance industry datasets. Parcels in mapped flame or ember zones can trigger ignition-resistant exterior material, vent-ember-screen, and defensible-space requirements at the local AHJ level \u2014 particularly in Santa Fe, Los Alamos, Sandoval, Lincoln, and Otero counties. Pull the map for the parcel before specifying siding, eave, deck, and vent assemblies.",
                "applies_to": "Construction in wildland-urban interface areas",
                "source": "https://www.emnrd.nm.gov/sfd/fire-prevention-programs/community-mitigation-maps/"
            },
            {
                "title": "NPDES Stormwater Construction General Permit on disturbed sites \u22651 acre",
                "note": "Construction, development, or redevelopment that disturbs one or more acres (or is part of a larger common plan of development) requires coverage under the EPA NPDES Stormwater Construction General Permit, with a SWPPP on site before ground disturbance \u2014 applicable on top of any local grading permit (e.g., Rio Rancho Municipal Code Chapter 153). Filing the NOI and posting the SWPPP is the contractor's responsibility, not the AHJ's. Building inspectors in NPDES jurisdictions routinely red-tag pours when the SWPPP and rain-event log aren't on site.",
                "applies_to": "Grading and construction disturbing \u22651 acre",
                "source": "https://rrnm.gov/DocumentCenter/View/81808/New-Home-Submittal-PDF-"
            },
            {
                "title": "No statewide ADU statute or ministerial shot clock \u2014 ADUs are 100% local",
                "note": "Unlike California (AB 881) or Oregon, New Mexico has no statewide accessory-dwelling-unit statute, no state-mandated ministerial review path, and no 60-day permit shot clock for ADUs. ADU rules \u2014 minimum size, setbacks, owner-occupancy, parking, max number per lot \u2014 are entirely set by the municipal or county zoning code. Always pull the local ADU ordinance (Albuquerque IDO, Santa Fe SFCC, Las Cruces LDC, etc.) before scoping; a design that's by-right in Albuquerque may require a special-use permit or variance two counties over.",
                "applies_to": "Accessory dwelling unit projects in any NM jurisdiction",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-new-mexico"
            }
        ]
    },
    "NE": {
        "name": "Nebraska expert pack",
        "expert_notes": [
            {
                "title": "Nebraska has no statewide licensing for general contractors, HVAC, or plumbing \u2014 registration only",
                "note": "Nebraska does NOT issue state-level licenses for general contractors, HVAC contractors, HVAC technicians, or plumbers \u2014 there is no statewide trade exam or license card to verify for these trades. The only mandatory statewide credential is Contractor Registration with the Nebraska Department of Labor under the Nebraska Contractor Registration Act, which is a registration (not a competency license) used for project permit and workers' comp tracking. Contractors and clients should NOT assume state registration vouches for skill or insurance \u2014 verify the actual municipal license (Omaha, Lincoln, etc.) and certificate of insurance separately.",
                "applies_to": "All construction work statewide; common misconception for HVAC, plumbing, and GC trades",
                "source": "https://www.servicetitan.com/licensing/hvac/nebraska"
            },
            {
                "title": "Nebraska Contractor Registration Act \u2014 mandatory NDOL registration before any work",
                "note": "Under the Nebraska Contractor Registration Act, all contractors and subcontractors doing business in Nebraska must register with the Nebraska Department of Labor (NDOL) before performing any work. Registration ties the contractor to project permit requirements, workers' compensation verification, contractor tax option, and registration-fee exemption eligibility. Cities (e.g., Omaha) require a current NDOL registration as a prerequisite to issuing a local contractor license \u2014 quoting or pulling permits without it will block the job.",
                "applies_to": "All contractors and subcontractors performing work in Nebraska",
                "source": "https://dol.nebraska.gov/LaborStandards/Contractors/Overview"
            },
            {
                "title": "State Electrical Division licenses electricians statewide \u2014 separate from building permit",
                "note": "Electrical work in Nebraska is regulated by the Nebraska State Electrical Division, which issues contractor and homeowner electrical permits, runs license exams, and maintains the licensee list and e-permit verification. Even when the city issues the building permit, the electrical scope typically requires a SEPARATE state electrical permit and inspection by a State Electrical Inspector \u2014 not the city building inspector. Always pull the state e-permit in parallel with the local building permit, or the rough-in inspection will fail.",
                "applies_to": "All electrical work statewide (residential, ADU, EV charger, solar PV, generator interlock)",
                "source": "https://electrical.nebraska.gov/welcome"
            },
            {
                "title": "Omaha contractor registration \u2014 $85 + $6.80 tech fee, requires current state registration",
                "note": "City of Omaha Planning Department requires a separate contractor registration in addition to NDOL registration: $85.00 initial plus $6.80 tech fee (same for renewal). A current State of Nebraska (NDOL) contractor registration must be on file before Omaha will issue or renew the local registration. Plan submittals from an unregistered contractor are rejected at intake \u2014 register at the city level before quoting Omaha-jurisdiction work.",
                "applies_to": "Any contractor pulling permits inside City of Omaha jurisdiction",
                "source": "https://permits.cityofomaha.org/licensing-information"
            },
            {
                "title": "2018 IECC + 2018 IRC are the current state baseline (effective July 16, 2019 / July 1, 2020)",
                "note": "Nebraska adopted the 2018 IRC and 2018 IECC with amendments for residential construction in August 2019, with the IRC change effective July 16, 2019 and the 2018 IECC effective July 1, 2020. The Nebraska Department of Water, Energy, and Environment (DWEE) administers the energy code. Use 2018 IECC envelope/Manual J inputs for plan check unless the local AHJ has adopted a newer edition \u2014 older 2009/2012 assumptions will be rejected.",
                "applies_to": "Residential new construction, additions, and energy compliance documents",
                "source": "https://dwee.nebraska.gov/state-energy-information/energy-codes"
            },
            {
                "title": "Default-adoption rule \u2014 local code must 'conform generally' to state code or state code applies",
                "note": "Effective August 28, 2021, Nebraska's state building code law provides that if a local jurisdiction fails to adopt a construction code that at least 'conforms generally' to the state building code, the state code applies by default. This means rural counties and small municipalities that have NOT formally adopted a code still cannot drop below the state baseline (2018 IRC/IECC). When working in an unincorporated or small-town AHJ that claims 'no code', design to the state minimum anyway \u2014 the inspector or a future buyer's appraiser can still enforce it.",
                "applies_to": "Unincorporated areas and small municipalities without formally adopted local codes",
                "source": "https://www.iccsafe.org/advocacy/adoptions-map/nebraska/"
            },
            {
                "title": "DWEE is the energy-code authority of last resort when no local code is adopted",
                "note": "If a local jurisdiction has not adopted an energy code, the Nebraska Department of Water, Energy, and Environment (DWEE) administers the 2018 IECC as the applicable code. Builders in jurisdictions without an active energy plan check still owe DWEE-level compliance (envelope U-values, mandatory air sealing, duct testing, mechanical ventilation per 2018 IECC). Document compliance via REScheck or component performance \u2014 do not assume 'no local reviewer' equals 'no requirement'.",
                "applies_to": "Residential projects in jurisdictions without a locally adopted energy code",
                "source": "https://dwee.nebraska.gov/state-energy-information/energy-codes"
            },
            {
                "title": "LB611 (2025) \u2014 pending legislation on local code conformity to state building code",
                "note": "Legislative Bill 611 (2025) is pending at the Nebraska Legislature and addresses local adoption of building or construction codes that conform generally with the state building code. Until LB611 is enacted and an effective date is published, do NOT cite it as binding \u2014 but flag for clients on long-lead projects (large subdivisions, multifamily) that the state-vs-local code framework may shift mid-project. Confirm bill status before relying on any provision in a permit narrative.",
                "applies_to": "Long-horizon projects where state/local code adoption boundaries matter",
                "source": "https://nebraskalegislature.gov/FloorDocs/109/PDF/Intro/LB611.pdf"
            },
            {
                "title": "County boards \u2014 Neb. Rev. Stat. \u00a723-172 building-code adoption authority in unincorporated areas",
                "note": "Under Neb. Rev. Stat. \u00a723-172, the county board may adopt by resolution (with force and effect of law) the conditions, provisions, limitations, and terms of a building or construction code applicable to unincorporated county territory. Counties also enforce zoning by requiring permits prior to erection, alteration, or repair of structures. For rural parcels outside any city's extraterritorial jurisdiction, the county \u2014 not a city \u2014 is the AHJ; pull the county building/zoning permit, not a municipal one.",
                "applies_to": "Construction in unincorporated county territory outside municipal ETJ",
                "source": "https://nebraskalegislature.gov/laws/statutes.php?statute=23-172"
            },
            {
                "title": "Floodplain Development Permit is SEPARATE from the building permit",
                "note": "A Nebraska Department of Natural Resources (NeDNR) Structural Floodplain Development Permit is required for any structure within an SFHA, and the agency is explicit that 'a floodplain development permit is not to be construed as a building permit, nor as a zoning/land use permit.' Use the 2025 Model Structural Floodplain Development Permit application and submit elevation certificates / floodproofing certifications as applicable. When a determination falls between two BFE lines on a NeDNR map, best practice is to use the higher of the two values for design.",
                "applies_to": "Any construction in a Special Flood Hazard Area or designated floodway/flood fringe",
                "source": "https://dnr.nebraska.gov/sites/default/files/doc/2025%20Model%20Structural%20Floodplain%20Development%20Permit%20-%20Fillable_0.pdf"
            },
            {
                "title": "Floodplain overlay zoning \u2014 floodway vs. flood-fringe AE district restrictions",
                "note": "Nebraska's model floodplain overlay ordinance creates two overlay districts: a floodway district (development restricted to prevent obstruction of floodwaters that would increase downstream flooding) and a flood-fringe (Zone AE) district. In the floodway, new residential structures and most fill are prohibited; in the AE fringe, structures must be elevated to or above BFE with proper venting/floodproofing. Confirm overlay district BEFORE design \u2014 a 'flood zone' label alone is insufficient; floodway placement can kill an ADU or addition outright.",
                "applies_to": "Parcels mapped within FEMA Zone AE, floodway, or NeDNR-regulated flood hazard overlay",
                "source": "https://dnr.nebraska.gov/sites/default/files/doc/desk-reference/legal-authority/Model-DZoneAE-FIRMFway.pdf"
            },
            {
                "title": "15-day expedited review shot clock for certain residential permits",
                "note": "A new Nebraska state law requires a 15-day review timeline for certain residential permits and has been folded into expedited-review proposals (model plans and staff-capacity building) for local jurisdictions. If a complete residential application sits past 15 days without action, the contractor has grounds to escalate to the city manager or planning director and request the model-plan/expedited path. Document the application-complete date in writing at intake \u2014 the clock only runs from completeness, not initial submittal.",
                "applies_to": "Qualifying residential permits subject to the state expedited-review timeline",
                "source": "https://citizenportal.ai/articles/6136120/state-law-shortens-review-timeline-committee-backs-model-plans-and-staff-capacity-building"
            },
            {
                "title": "Floodplain permit \u2260 building/zoning permit \u2014 three parallel approvals required",
                "note": "NeDNR's administrative procedures guide is explicit that the floodplain development permit is a standalone instrument and does NOT substitute for a building permit or a zoning/land-use permit. Projects in the SFHA therefore need three parallel approvals: (1) NeDNR or local-administered floodplain permit, (2) AHJ building permit, and (3) AHJ zoning/land-use permit. Sequence them in parallel \u2014 waiting for the floodplain permit before opening building plan check adds weeks unnecessarily.",
                "applies_to": "All SFHA projects requiring building, zoning, and floodplain approvals",
                "source": "https://dnr.nebraska.gov/sites/default/files/doc/desk-reference/admin-procedures/4AdmProcGuide.pdf"
            },
            {
                "title": "MEEA-flagged risk \u2014 proposed rollback of Nebraska Residential Energy Code to 2009 IECC",
                "note": "Per MEEA's February 2026 comments to the Nebraska Urban Affairs Committee, proposed legislation would roll the current Nebraska Residential Energy Code (2018 IECC) back to the 2009 version. Until/unless that bill is enacted with a published effective date, design to the 2018 IECC \u2014 but flag the risk on multi-year subdivision and multifamily projects where the energy-code basis affects HERS, Manual J, and incentive eligibility. Do not pre-emptively design to 2009 envelope values; that would currently fail plan check.",
                "applies_to": "Long-horizon residential projects sensitive to the IECC vintage",
                "source": "https://www.mwalliance.org/sites/default/files/meea-research/meea_comments_to_nebraska_urban_affairs_committe_2026.pdf"
            }
        ]
    },
    "ID": {
        "name": "Idaho expert pack",
        "expert_notes": [
            {
                "title": "Idaho DOPL trade licensing \u2014 separate boards for Electrical, Plumbing, HVAC",
                "note": "The Idaho Division of Occupational and Professional Licenses (DOPL) in Boise issues and enforces separate licenses through the Electrical Board, State Plumbing Board, and HVAC Board. Verify the contractor's license status, classification, and trade match before quoting; specialty journeyman licenses (e.g., Specialty Electrical Journeyman) do NOT satisfy a full electrical contractor requirement. Use the DOPL license-search tool for each trade \u2014 a suspended or wrong-class license will block permit issuance and inspections.",
                "applies_to": "All paid electrical, plumbing, and HVAC work in Idaho",
                "source": "https://dopl.idaho.gov/"
            },
            {
                "title": "Idaho has NO statewide general contractor license \u2014 registration only",
                "note": "Unlike California's CSLB, Idaho does NOT require a state general contractor's license; only a Public Works Contractor License is required for state/public projects. Private residential GCs must hold an Idaho Contractor Registration through DOPL but there is no skills/exam requirement at the state level for general building work. This is a common point of confusion \u2014 do not assume an out-of-state GC license satisfies Idaho, and conversely do not over-promise that 'licensed in Idaho' means tested. Trade work (electrical, plumbing, HVAC) still requires a full DOPL trade license.",
                "applies_to": "General contracting / homebuilding in Idaho (non-trade scopes)",
                "source": "https://nationalcontractorlicenseagency.com/idaho-contractor-license-information"
            },
            {
                "title": "Idaho Energy Code \u2014 2018 IECC with state amendments effective 2021-01-01",
                "note": "Effective January 1, 2021, all residential and commercial building projects in Idaho must comply with the 2018 International Energy Conservation Code with Idaho-specific amendments. Idaho has not yet adopted the 2021 or 2024 IECC statewide, so envelope, fenestration U-values, and duct-leakage testing follow 2018 IECC. Check the latest amendments before sizing insulation or specifying windows \u2014 the Idaho amendments soften several 2018 IECC provisions for cold-climate framing.",
                "applies_to": "All new residential and commercial construction and major alterations",
                "source": "https://www.idahoenergycode.com/"
            },
            {
                "title": "Idaho Building Code Board \u2014 2018 IRC adoption and amendment cycle",
                "note": "The Idaho Building Code Board (under DOPL) adopts the IRC/IBC family on a delayed cycle; the 2018 edition with state amendments is currently in force statewide. Stakeholders may submit proposed amendments to the Board (e.g., 2025 cycle deadline was April 28, 2025, for May 22 hearing). Confirm which edition and amendment package the AHJ enforces before drafting plans, because adoption lags national ICC publication by several years.",
                "applies_to": "Permit applications statewide where the IRC/IBC governs",
                "source": "https://dopl.idaho.gov/wp-content/uploads/2024/01/BLD-Prospective-Analysis-2023.pdf"
            },
            {
                "title": "Local code amendments \u2014 restricted under Idaho Code",
                "note": "Idaho law sharply limits local jurisdictions' ability to amend the Idaho Residential Code. Local jurisdictions may amend the remainder of Part III of the Idaho residential code only on a finding that good cause for building or life safety exists; they cannot freely impose stricter envelope or structural amendments. This means an AHJ pushing a non-statutory local amendment is challengeable \u2014 request the written good-cause finding before complying with anything stricter than the state code.",
                "applies_to": "Disputes over locally-imposed amendments to the Idaho Residential Code",
                "source": "https://cdn.ymaws.com/idahocities.org/resource/resmgr/publications/2020/constraints_on_code_amendmen.pdf"
            },
            {
                "title": "SB 1164 \u2014 building permit review timeline reform",
                "note": "Idaho Senate Bill 1164 adds a new section to the Idaho Building Code Act establishing clear timelines and requirements for processing building permits \u2014 addressing the lack of an explicit statewide shot clock that long plagued Idaho residential permitting. If a jurisdiction is exceeding the SB 1164 review window on a complete application, that is statutory grounds to escalate to the AHJ director or the Idaho Building Code Board.",
                "applies_to": "Building permit applications experiencing AHJ review delays",
                "source": "https://www.billtrack50.com/billdetail/1857111"
            },
            {
                "title": "Idaho ADU / starter-home preemption law",
                "note": "Idaho recently enacted housing legislation that allows smaller starter homes and accessory dwelling units, overriding local zoning to boost housing affordability. ADUs are now permitted by-right in many single-family zones notwithstanding contrary local ordinances. Before quoting an ADU project, check whether the city's existing ADU ordinance has been preempted \u2014 many local rules limiting size, owner-occupancy, or off-street parking are no longer enforceable.",
                "applies_to": "ADU jobs and starter-home subdivisions in single-family zones",
                "source": "https://www.realtor.com/news/real-estate-news/idaho-new-laws-starter-homes-zoning/"
            },
            {
                "title": "DOPL Permits and Inspections \u2014 state-issued trade permits in non-delegated areas",
                "note": "DOPL directly issues HVAC, electrical, and plumbing permits and conducts inspections in jurisdictions that have not assumed local trade-permit authority. This is a SEPARATE filing from any city/county building permit \u2014 you purchase the trade permit online through DOPL and request inspection from the DOPL inspector, not the city. Failing to pull the state trade permit (and pulling only the city building permit) is a common rejection cause in unincorporated Idaho and small cities.",
                "applies_to": "HVAC, electrical, and plumbing work in DOPL-administered jurisdictions",
                "source": "https://dopl.idaho.gov/hvac/hvac-permits-and-inspections/"
            },
            {
                "title": "AHJ split \u2014 state vs local building plan review",
                "note": "Idaho Code allows public school building plans to be approved by either the local government or the Division of Building Safety/DOPL, whichever the school district elects \u2014 and a similar split exists for several state-funded and special-occupancy projects. For typical private residential work, the city or county building department is the AHJ if it has adopted the Idaho Building Code; otherwise DOPL is the AHJ. Confirm which agency holds plan-review authority before submitting, because submitting to the wrong office wastes 2\u20134 weeks.",
                "applies_to": "Determining the correct plan-review AHJ for a given parcel",
                "source": "https://dopl.idaho.gov/wp-content/uploads/2023/09/BLD-Building-Statutes-Rules.pdf"
            },
            {
                "title": "Joint Application for Permit \u2014 IDWR/IDL/USACE stream and lake work",
                "note": "Work in, on, or near Idaho streams, lakes, or wetlands requires the Joint Application for Permit (NWW Form 1145-2 / IDWR 3804-B) which simultaneously addresses the Idaho Stream Protection Act (Title 42, Chapter 38, Idaho Code), the Lake Protection Act (Title 58, Chapter 13), and federal Section 404 permitting. This is a SEPARATE filing from the building permit and is processed by IDWR, the Idaho Department of Lands, and the U.S. Army Corps of Engineers. Build at least 60\u2013120 days of lead time into the schedule for any dock, bank stabilization, or stream-crossing work.",
                "applies_to": "Construction in or adjacent to Idaho streams, lakes, or jurisdictional wetlands",
                "source": "https://www.idl.idaho.gov/wp-content/uploads/sites/116/2020/01/InstructionGuide-3.pdf"
            },
            {
                "title": "IDWR Floodplain Development Permit \u2014 separate from building permit",
                "note": "If the parcel falls in a FEMA Special Flood Hazard Area, the local floodplain administrator (with IDWR oversight) must issue a Floodplain Development Permit before any grading, fill, or structure work. The IDWR floodplain manager helps communities plan for floods, conducts training, and reviews work; the floodplain permit is only a permit to complete the proposed development and does NOT replace the building permit. Foundation elevation certificates and venting must meet 44 CFR \u00a760.3 \u2014 submit the Elevation Certificate at footing inspection, not at final.",
                "applies_to": "Any construction within a FEMA mapped floodplain in Idaho",
                "source": "https://idwr.idaho.gov/floods/"
            },
            {
                "title": "Idaho Power generator interconnection \u2014 parallel filing for solar/battery/standby",
                "note": "Customer-sited solar PV, battery storage, and standby generators that can operate in parallel with the grid in Idaho Power territory must complete the Idaho Power Generator Interconnection process \u2014 a SEPARATE application from the city/county electrical permit. The interconnection request is reviewed against IPUC-approved Schedule 72/68 procedures and must be approved before Permission to Operate (PTO) is granted, even after final electrical inspection. Submit the interconnection application in parallel with the building/electrical permit to avoid 4\u20138 week post-inspection delays waiting on PTO.",
                "applies_to": "Solar PV, battery storage, and parallel-capable standby generators in Idaho Power territory",
                "source": "https://www.idahopower.com/about-us/doing-business-with-us/generator-interconnection/"
            },
            {
                "title": "Idaho Falls Power and other municipal utilities \u2014 local construction permit required",
                "note": "Municipal utilities such as Idaho Falls Power run their own distributed-generation programs separate from Idaho Power. A construction permit from the City Building Department is required before modifying the electrical system on your home or business, and interconnection approval is issued by the municipal utility \u2014 not the IPUC. If the project is in a muni-utility service territory (Idaho Falls, Heyburn, Soda Springs, etc.), do not use Idaho Power forms; pull the muni utility's interconnection packet plus the city electrical permit.",
                "applies_to": "DG, EV charger, and service upgrade work in municipal utility service territories",
                "source": "https://www.ifpower.org/accounts-and-services/distributed-generation-program"
            },
            {
                "title": "Idaho Transportation Department \u2014 utility accommodation on state ROW",
                "note": "Any utility installation, service drop, or driveway work that crosses or occupies a state highway right-of-way must comply with the ITD Utility Accommodation Policy and obtain an ITD encroachment/utility permit before construction. This is a parallel filing to the local building permit and is governed by Idaho Code requirements for federal-aid and state highway facilities. Plan an extra 2\u20136 weeks for ITD review on any service upgrade where the meter base or trench crosses an ITD-maintained roadway.",
                "applies_to": "Service drops, trenching, or driveway work crossing ITD-maintained right-of-way",
                "source": "https://itd.idaho.gov/wp-content/uploads/2022/06/RM_Utility_Policy_Draft-1.pdf"
            }
        ]
    },
    "WV": {
        "name": "West Virginia expert pack",
        "expert_notes": [
            {
                "title": "West Virginia State Building Code \u2014 2015 IECC with amendments, effective Aug 1, 2022",
                "note": "The WV State Building Code (87 CSR 4) incorporates the 2015 IECC with West Virginia amendments, plus the ICC suite (IBC, IRC, IMC, IFGC, IPC, IPMC, IEBC). The current edition was approved by the Legislature on January 7, 2022 and became effective August 1, 2022. WV is one of the slowest states on the energy-code cycle, so verify the 2015 IECC envelope/duct-leakage values rather than assuming 2018 or 2021 IECC numbers from neighboring states.",
                "applies_to": "All residential and commercial construction in WV jurisdictions that enforce the State Building Code",
                "source": "http://www.wvcoa.com/code-adoption-information/"
            },
            {
                "title": "Local opt-in: statewide codes only apply where the jurisdiction has formally adopted them",
                "note": "West Virginia is a permissive code-adoption state \u2014 the Fire Commission adopts the model codes statewide, but local jurisdictions must affirmatively adopt them to enforce them at the local level. Many unincorporated WV counties have no building-permit process at all for 1- and 2-family dwellings, while incorporated cities like Charleston, Morgantown, and Huntington fully enforce. Always confirm with the AHJ before relying on the statewide code as the floor.",
                "applies_to": "Determining whether a parcel is in a code-enforcing jurisdiction or unregulated area",
                "source": "https://www.energycodes.gov/status/states/west-virginia"
            },
            {
                "title": "WV Fire Commission ICC suite adoption",
                "note": "The West Virginia Fire Commission has adopted the IBC, IRC, IMC, IFGC, IPC, IPMC, and IEBC statewide for any jurisdiction that chooses to enforce codes. Note the Fire Commission has NOT adopted the IECC for residential under the same vehicle \u2014 the energy code path runs separately through the State Building Code rule. This split matters when an AHJ enforces only the residential code but defers energy compliance to the contractor.",
                "applies_to": "Identifying which model codes apply at plan review",
                "source": "https://www.iccsafe.org/advocacy/adoptions-map/west-virginia/"
            },
            {
                "title": "WV Contractor License \u2014 $2,500 labor + materials threshold",
                "note": "Any business performing construction work valued at $2,500 or more (combined labor and materials) must hold a West Virginia Contractor License issued by the WV Contractor Licensing Board, administered through the WV Division of Labor. Operating above the threshold without a license is a violation that can void the contract, block lien rights, and expose the contractor to penalties. This threshold is dramatically lower than most states (CA is $500, but many are $5k\u2013$25k), so even small handyman-scale projects in WV often trigger licensing.",
                "applies_to": "All paid construction work in WV \u2265 $2,500",
                "source": "https://gaslampinsurance.com/west-virgina-contractors-license-requirements/"
            },
            {
                "title": "WV contractor classifications and exam requirements",
                "note": "The WV Contractor Licensing Board issues licenses by classification: Residential Building Contractor, General Building Contractor (commercial), and specialty trades including Electrical, HVAC, Plumbing, Piping, and others. Each classification has its own exam (administered through PSI/1ExamPrep) and the license must match the scope of work on the permit application. Pulling a permit under the wrong classification (e.g., a Residential Building Contractor pulling a commercial reroof) is a common rejection reason.",
                "applies_to": "Selecting the correct license classification before bidding or permitting",
                "source": "https://1examprep.com/blogs/news-insight/west-virginia-contractor-license-2026-types-exams-and-tips"
            },
            {
                "title": "HVAC Technician Certification is a separate parallel filing",
                "note": "In addition to the company-level Contractor License, every individual technician installing or servicing HVAC in WV must hold a Certified HVAC Technician credential issued by the Division of Labor. The contractor must show proof of the WV contractor's license to obtain building permits, and the tech-level certification must be on file separately. Pulling an HVAC permit with only the company license \u2014 and no certified tech of record \u2014 is a frequent rejection reason at AHJ intake.",
                "applies_to": "All HVAC installation, replacement, and major service work",
                "source": "https://www.servicetitan.com/licensing/hvac/west-virginia"
            },
            {
                "title": "Verify license + certification status at the Division of Labor database before quoting",
                "note": "The WV Division of Labor publishes a public Database Search with four lookups: Contractor License, Certified HVAC Technicians, Certified Plumbers, and Manufactured Housing Licensees. Run the lookup before quoting to confirm the license is active (not expired or suspended) and that the classification covers the scope. Around 29,000\u201330,000 contractor licenses and HVAC certifications are renewed annually, so lapses around the renewal window are common.",
                "applies_to": "Pre-bid license verification and subcontractor vetting",
                "source": "https://labor.wv.gov/database-search"
            },
            {
                "title": "HB 3052 (2025) \u2014 municipal ADU regulation preemption",
                "note": "HB 3052 (2025 Regular Session) was introduced to prohibit West Virginia municipalities from adopting certain regulations restricting accessory dwelling units. WV does NOT yet have a California-style ministerial shot clock or statewide ADU-by-right law, so individual cities can still impose objective design standards, parking, and setback rules. Track HB 3052's enactment status before promising a client a streamlined ADU path \u2014 and confirm with the AHJ that any municipal ADU ordinance has been updated to conform.",
                "applies_to": "ADU projects in incorporated WV municipalities",
                "source": "https://www.wvlegislature.gov/Bill_Status/bills_text.cfm?billdoc=hb3052%20intr.htm&yr=2025&sesstype=RS&i=3052"
            },
            {
                "title": "Caveat emptor \u2014 WV does NOT require a statutory seller disclosure form",
                "note": "West Virginia is a 'buyer beware' (caveat emptor) state and does not mandate a standardized statutory seller disclosure statement for residential property. There is no statewide requirement to disclose unpermitted work, open permits, or energy-performance data at sale, though most transactions use a voluntary WV Realtors disclosure form. This means buyers cannot rely on a statutory disclosure to surface unpermitted additions \u2014 a permit-history check at the AHJ is the only reliable path.",
                "applies_to": "Pre-purchase due diligence and remodel scoping on existing homes",
                "source": "https://www.thejamilbrothers.com/blog/west-virginia-seller-disclosure-requirements"
            },
            {
                "title": "Floodplain Construction Permit \u2014 county or community permit officer",
                "note": "Any construction within a designated Special Flood Hazard Area in WV requires a Floodplain Construction Permit issued by the county or community floodplain permit officer (separate from the building permit). Before issuing it, the permit officer will require copies of all other federal/state permits required by law. Permit cost varies by community. Skipping the floodplain permit is a leading cause of NFIP non-compliance findings and post-construction stop-work orders in WV's flood-prone river valleys.",
                "applies_to": "New construction, substantial improvement, or substantial damage repair in mapped SFHA",
                "source": "https://dep.wv.gov/WWE/Programs/nonptsource/streamdisturbance/Documents/Floodplainpermits.pdf"
            },
            {
                "title": "WVDEP Stream Disturbance / Stormwater Construction Permit \u2014 $300",
                "note": "Land disturbance that affects a stream or wetland requires a Stream Disturbance Permit from the WV Department of Environmental Protection, Division of Water. The permit costs $300, and the disturbed area does not have to be contiguous to qualify for the threshold. Driveway culverts, bank stabilization, and grading near intermittent streams routinely trip this requirement on rural residential lots \u2014 file in parallel with the building permit, not after.",
                "applies_to": "Site work involving streams, wetlands, or significant grading",
                "source": "https://dep.wv.gov/WWE/Programs/nonptsource/streamdisturbance/Documents/WVStreamDisturbancePermitGuide.pdf"
            },
            {
                "title": "WV Flood Tool (MapWV.gov) \u2014 mandatory pre-permit check",
                "note": "The WV Flood Tool at mapwv.gov/flood is the state's official portal for flood hazard determinations and offers three views: PUBLIC, EXPERT, and RISK MAP. Pull the EXPERT view BFE and zone designation before drawing finish-floor elevations, and screenshot the result for the permit packet. WV uses this tool \u2014 not just FEMA's NFHL \u2014 for community-level floodplain administration, and the local permit officer will compare it to your plans.",
                "applies_to": "Any parcel near rivers, creeks, or low-lying terrain statewide",
                "source": "https://www.mapwv.gov/flood/"
            },
            {
                "title": "No statutory shot clock \u2014 plan review typically 2 to 6 weeks",
                "note": "Unlike California's 60-day ADU clock, West Virginia has no statewide statutory shot clock for residential plan review. Plan review alone typically takes 2 to 6 weeks depending on the jurisdiction's workload and whether the submission is complete. Build this into the schedule and front-load completeness \u2014 incomplete submissions reset the queue at most WV AHJs rather than triggering a deemed-approved remedy.",
                "applies_to": "Project scheduling and client expectations on permit timeline",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-west-virginia"
            },
            {
                "title": "WVDEP umbrella permits \u2014 air, water, and soil",
                "note": "The WV Department of Environmental Protection issues permits across air, water, and soil for facilities and activities so that releases stay within acceptable standards. For typical residential work this rarely applies, but additions over disturbed acreage thresholds, on-site septic work, or any activity touching a regulated waterway can trigger a separate WVDEP filing in addition to the AHJ building permit. When in doubt, check the WVDEP Permits portal before site work begins to avoid a stop-work order mid-project.",
                "applies_to": "Larger residential sites, septic work, and any environmental-discharge activity",
                "source": "https://dep.wv.gov/Permits"
            }
        ]
    },
    "HI": {
        "name": "Hawaii expert pack",
        "expert_notes": [
            {
                "title": "Hawaii has no single statewide enforcement AHJ \u2014 counties are the building department",
                "note": "Hawaii Revised Statutes \u00a7107-28 gives the four counties (Honolulu, Hawai\u02bbi, Maui, Kaua\u02bbi) authority to amend and adopt the Hawai\u02bbi State Building Codes. There is no state-level building department issuing residential permits \u2014 all building permits are issued and inspected by the county Department of Planning and Permitting (Honolulu / Kaua\u02bbi) or Planning Department (Hawai\u02bbi / Maui). Always confirm which county AHJ governs the parcel before pulling drawings, because amendments diverge significantly between counties.",
                "applies_to": "All residential and commercial permit work statewide",
                "source": "https://ags.hawaii.gov/bcc/building-code-rules/"
            },
            {
                "title": "State Building Code default \u2014 counties have 2 years to adopt amendments",
                "note": "Under HRS \u00a7107-28, the four counties must amend and adopt the Hawai\u02bbi state building codes within two years of state adoption; if they do not, the state code applies by default. The Hawai\u02bbi State Building Code currently includes the 2018 IBC, 2018 IRC, 2018 IPC, and the 2021 fire code as the baseline reference codes for county adoption. Verify the exact code edition in force at the county level before submitting drawings \u2014 the state baseline and the county-amended version often differ.",
                "applies_to": "Code-edition determination for all permit applications",
                "source": "https://www.seaoh.org/Building-Code-Status"
            },
            {
                "title": "2018 IECC with amendments is the energy code (effective 2020-12-15)",
                "note": "Hawai\u02bbi adopted the 2018 IECC with state amendments and ASHRAE 90.1-2016 effective December 15, 2020 for both residential and commercial buildings. The state energy code is sent to the counties for local adoption \u2014 confirm the county has formally adopted (not all amendments are uniform). Energy compliance documentation (envelope, HVAC, water heating, lighting) must be included in the permit set for new construction and conditioned-space additions.",
                "applies_to": "New construction and conditioned-space additions/alterations",
                "source": "https://www.energycodes.gov/status/states/hawaii"
            },
            {
                "title": "60-day permit shot clock takes effect 2026-07-01",
                "note": "Hawai\u02bbi enacted a permit \"shot clock\" requiring counties to act on residential dwelling permit applications within 60 days; the law does not take effect until July 1, 2026. Applications submitted before that date are still subject to the historic county backlogs (Hawai\u02bbi County ~143 days, Honolulu ~108 days residential / ~432 days commercial). For projects filing in mid-to-late 2026, time the submittal to fall under the shot-clock regime so you have grounds to escalate non-action.",
                "applies_to": "Residential dwelling permit applications submitted on or after 2026-07-01",
                "source": "https://www.hawaiifreepress.com/Articles-Main/ID/45847/Permitting-progress-prevails"
            },
            {
                "title": "Realistic county permit timelines before the shot clock",
                "note": "Pre-shot-clock benchmarks: Hawai\u02bbi County residential permits average ~143 days; Honolulu averages ~108 days for residential and ~432 days for commercial. Set client expectations against these baselines, not the 60-day statutory target \u2014 the statutory clock does not apply retroactively, and complex jobs (variance, shoreline, historic) routinely exceed the average. Use the published averages when scheduling subcontractor mobilization and material orders.",
                "applies_to": "Schedule and contract milestones for permits filed before 2026-07-01",
                "source": "https://www.facebook.com/mykailua/posts/building-permits-in-hawaii-i-just-read-this-morning-that-the-new-system-that-the/1248940687272413/"
            },
            {
                "title": "DCCA Contractors License Board \u2014 state-issued, not county",
                "note": "Contractor licensing in Hawai\u02bbi is issued exclusively by the state DCCA Professional & Vocational Licensing (PVL) Contractors License Board \u2014 counties do not issue trade licenses. A and B (general engineering / general building) licenses require a minimum of 4 years documented journey-level or supervisory experience; C (specialty) licenses also require 4 years of trade experience. Verify license + classification on the DCCA PVL lookup before contracting; an unlicensed or wrong-class license blocks permit pulling and lien rights.",
                "applies_to": "All paid construction work statewide",
                "source": "https://cca.hawaii.gov/pvl/boards/contractor/"
            },
            {
                "title": "C-13 Electrical / C-37 Plumbing / C-52 & C-53 HVACR specialty classifications",
                "note": "HVACR work in Hawai\u02bbi requires a Class C Specialty Contractor License from the DCCA Board (typically C-52 ventilating/air-conditioning or C-53 refrigeration); electrical requires C-13 with a supervisory RME (Responsible Managing Employee), and plumbing requires C-37. The C-class license is held by the company, but each specialty license must name a qualified RME with the trade experience. Confirm the RME is actively listed on the license \u2014 RME departures invalidate the license for new permit pulls.",
                "applies_to": "Electrical, plumbing, and HVAC subcontractor selection and permit pulling",
                "source": "https://www.contractortests.com/hawaii-contractor-licenses-updated-requirements-guide/?srsltid=AfmBOoqoG5k9S-ezrYcyU4ZVLqio7-SDXUd9utHauDcI_cxrItF5temn"
            },
            {
                "title": "Electrician & Plumber individual licenses \u2014 triennial renewal by June 30",
                "note": "Individual electricians and plumbers are licensed separately from the contractor license through the DCCA Board of Electricians and Plumbers and must be renewed triennially by June 30 (electrician cycles: 2026, 2029, 2032). Field workers performing electrical or plumbing work \u2014 not just the company's RME \u2014 must hold current individual licenses. An expired individual license on a job site is a common cause of inspection failure and stop-work orders.",
                "applies_to": "All journey-level and apprentice electrical/plumbing work",
                "source": "https://cca.hawaii.gov/pvl/boards/electrician/"
            },
            {
                "title": "License restoration window \u2014 60 days after expiration",
                "note": "Hawai\u02bbi DCCA contractor licenses can be restored within 60 days after expiration; beyond that window, restoration becomes substantially more difficult and may require re-examination or re-application. If a license lapsed, do not pull permits or sign new contracts until restoration is complete \u2014 work performed under a lapsed license is treated as unlicensed contracting and voids lien rights. Run a license check the week of every permit submittal, not just at job start.",
                "applies_to": "Contractor license maintenance and renewal scheduling",
                "source": "https://cca.hawaii.gov/pvl/boards/contractor/"
            },
            {
                "title": "Hawaiian Electric DER interconnection \u2014 separate parallel filing from building permit",
                "note": "Solar PV, battery storage, and any distributed energy resource (DER) >10 kW requires a separate interconnection application with Hawaiian Electric (HECO/MECO/HELCO) under Rule 14H, in addition to the county building/electrical permit. The customer (Facility Parties) must obtain all authorizations, approvals, permits, and licenses for construction and operation \u2014 interconnection approval is independent of permit issuance and can be the critical-path item. File the IRA/interconnection application in parallel with the permit, not after, to avoid commissioning delays.",
                "applies_to": "Solar PV, battery storage, EV chargers >10 kW, and standby generator interconnection",
                "source": "https://www.hawaiianelectric.com/documents/products_and_services/customer_renewable_programs/HELCO_rules_14_appendix_II_A_greater_than_10kW.pdf"
            },
            {
                "title": "HRS \u00a7508D residential seller disclosure \u2014 15-day buyer rescission window",
                "note": "Under HRS \u00a7508D-5, a seller of residential real property must deliver a signed disclosure statement, and the buyer has 15 calendar days to examine it and decide whether to rescind the purchase contract. Disclosure must include known material facts including unpermitted work, prior permits, and structural conditions. For any addition/ADU/remodel that closed without a final inspection or COC, document this on the disclosure \u2014 undisclosed unpermitted work is a top source of post-closing litigation.",
                "applies_to": "Residential resale where prior or current permit work is involved",
                "source": "https://law.justia.com/codes/hawaii/title-28/chapter-508d/section-508d-5/"
            },
            {
                "title": "State projects are NOT exempt from county building permits",
                "note": "Despite legislative pushes (e.g., HB 761 in 2025), state-funded and state-owned projects in Hawai\u02bbi are still subject to county building permits, inspections, and certificates of occupancy \u2014 there is no statewide exemption. Do not assume DOE schools, UH facilities, or DOT-related ancillary structures bypass county review. Plan reviews and inspections proceed through the county DPP/Planning Department exactly as for private work.",
                "applies_to": "State-agency-funded or state-owned construction in any county",
                "source": "https://www.grassrootinstitute.org/2025/01/exempt-state-projects-from-needing-county-permits/"
            },
            {
                "title": "Honolulu DPP permit pickup \u2014 Frank Fasi Municipal Building",
                "note": "Approved building permits in the City & County of Honolulu are picked up at the Permit Issuance Branch, Department of Planning and Permitting, 650 South King Street, Honolulu HI 96813 (Frank Fasi Municipal Building). Online status is tracked through Honolulu DPP's permit portal, but the physical issuance and fee payment historically still route through this office. Confirm pickup vs e-issuance procedure at submittal because Honolulu has been actively migrating to electronic permitting.",
                "applies_to": "Honolulu (O\u02bbahu) building-permit logistics",
                "source": "https://www.honolulu.gov/dpp/home/faq/"
            },
            {
                "title": "Hawai\u02bbi County subdivision notice \u2014 owner acknowledgement required",
                "note": "Hawai\u02bbi County requires owners applying for a building permit within a subdivision to acknowledge subdivision-related conditions (CC&Rs, drainage, road maintenance, agricultural restrictions). Contact the Building Division at 808-961-8331 (East Hawai\u02bbi) for permit questions. Missing the subdivision acknowledgement form is a common Hawai\u02bbi County intake-rejection reason \u2014 include it in the initial submittal package, not as a correction response.",
                "applies_to": "Building permits for parcels inside recorded subdivisions on Hawai\u02bbi Island",
                "source": "https://www.planning.hawaiicounty.gov/resources/notice-to-owners-applying-for-a-building-permit-within-subdivisions"
            }
        ]
    },
    "NH": {
        "name": "New Hampshire expert pack",
        "expert_notes": [
            {
                "title": "NH State Building Code \u2014 2021 I-Codes effective July 1, 2024 with Oct 15, 2025 amendments",
                "note": "New Hampshire adopted the 2021 IBC, IRC, IEBC, IMC, IPC, and IFGC as the State Building Code, retroactively effective July 1, 2024, with full enforcement required by January 1, 2025. The State Fire Marshal published amendments effective October 15, 2025 \u2014 confirm which edition the AHJ is reviewing under, because the code in effect is determined by the date the building permit application is received under RSA 155-A:4. Submitting drawings prepared to an older edition is a common rejection reason at plan review.",
                "applies_to": "All building permit applications statewide",
                "source": "https://mm.nh.gov/files/uploads/fmo/remote-docs/summary-of-2021-building-code-amendments-effective-1oct2025.pdf"
            },
            {
                "title": "NH Energy Code \u2014 2018 IECC adopted statewide (HB 1681, July 1, 2022)",
                "note": "New Hampshire's energy code is the 2018 International Energy Conservation Code, adopted under HB 1681 effective July 1, 2022, and it remained the operative energy code when the 2021 building code package took effect in 2024. Residential envelope, fenestration U-factors, and duct/air-leakage testing must comply with 2018 IECC \u2014 do not assume newer 2021 IECC values apply unless the AHJ has locally amended upward. Blower-door and duct-leakage results are required deliverables before final inspection.",
                "applies_to": "New construction and additions involving conditioned space",
                "source": "https://www.firemarshal.dos.nh.gov/laws-rules-regulatory/state-building-code/historical-adoption-dates-nh-state-building-code"
            },
            {
                "title": "No statewide general contractor license \u2014 trade licenses only via OPLC",
                "note": "New Hampshire does not issue a statewide general contractor license. The Office of Professional Licensure and Certification (OPLC) licenses individual trades \u2014 electrical, plumbing, gas fitting, fuel oil, mechanical \u2014 and general/remodeling contractors operate under local registration only. Verify the trade license through OPLC's online licensing system before subcontracting; using an unlicensed electrician or plumber will void permit sign-off even though no GC license exists.",
                "applies_to": "All paid residential construction in New Hampshire",
                "source": "https://contractorlicensinginc.com/national-contractor-licensing/new-hampshire-contractor-license-requirements-application-help-contractor-licensing-inc/"
            },
            {
                "title": "Electrical and plumbing work require state trade licenses \u2014 no $-threshold exemption",
                "note": "Unlike states with a dollar-value unlicensed-work threshold, New Hampshire requires an OPLC-issued electrical license (Master/Journeyman/Apprentice) or plumbing license for any electrical or plumbing work performed for hire, regardless of project size. Homeowners may self-perform on their own primary residence, but anyone paid must hold the trade license. Permit applications listing an unlicensed installer are routinely rejected at intake.",
                "applies_to": "All paid electrical and plumbing scopes",
                "source": "https://www.oplc.nh.gov/apply-renew"
            },
            {
                "title": "HVAC / mechanical contracting \u2014 gas fitter license required for fuel-gas work",
                "note": "There is no dedicated 'HVAC contractor' license in New Hampshire, but any work on natural-gas or LP fuel-gas piping and appliance connections requires an OPLC gas fitter license, and oil-burner work requires a fuel oil license. Pure refrigerant-side HVAC is not state-licensed but federal EPA Section 608 certification is still required for refrigerant handling. Plan submittals for furnace/boiler/water-heater swaps must list the gas fitter license number, not just the installing company.",
                "applies_to": "Furnace, boiler, gas water heater, gas range, and rooftop unit installations",
                "source": "https://www.servicetitan.com/licensing/hvac/new-hampshire"
            },
            {
                "title": "ADU statewide right \u2014 HB 577 (2025) raises cap to 950 sq ft, one ADU by-right",
                "note": "Under the revised RSA 674:71\u201373 as amended by HB 577 (2025), every municipality that has adopted zoning must allow at least one accessory dwelling unit by right in all districts that allow single-family dwellings, and the size cap a town may impose is now up to 950 sq ft. Building, electrical, plumbing, and mechanical permits and inspections are still required. Towns cannot require a special exception, conditional-use permit, or owner-occupancy covenant beyond what the statute allows \u2014 flag any such local requirement as preempted.",
                "applies_to": "Detached and attached ADU jobs in zoned NH municipalities",
                "source": "https://www.canbury.com/post/new-hampshire-adu-law-hb-577"
            },
            {
                "title": "NHDES Shoreland Water Quality Protection Act \u2014 250 ft protected shoreland",
                "note": "Construction, fill, excavation, or dredging within 250 feet of the reference line of a public water (great pond \u226510 acres, fourth-order or larger river, or designated coastal water) requires a Shoreland Permit from NH DES under RSA 483-B in addition to the local building permit. Impervious surface, vegetation removal, and setback rules are stricter inside the 250 ft band, and the Shoreland Permit must be in hand before the town will issue building approval in many AHJs. This is a parallel filing \u2014 separate from the wetlands permit and from the local zoning permit.",
                "applies_to": "Any project within 250 ft of a protected NH waterbody",
                "source": "https://www.miltonnh-us.com/code-enforcement/pages/nhdes-shore-land-permit"
            },
            {
                "title": "NHDES Wetlands Permit (RSA 482-A) \u2014 required before any wetland disturbance",
                "note": "Any excavation, fill, dredge, or construction in or adjacent to wetlands, surface waters, or banks requires a Wetlands Permit from the NHDES Wetlands Bureau under RSA 482-A, classified as Minimum Impact, Minor Impact, or Major Impact based on area disturbed. Priority Resource Areas (PRAs) \u2014 bogs, peatlands, designated rivers, undeveloped tidal buffer \u2014 trigger heightened review and longer timelines. The wetlands permit must be issued before the AHJ will release the building permit; do not let the homeowner sign a fixed-date contract until the wetlands tier is confirmed.",
                "applies_to": "Projects with grading, foundations, or structures near wetlands or surface waters",
                "source": "https://www.des.nh.gov/water/wetlands/permit-assistance"
            },
            {
                "title": "NHDES Alteration of Terrain (AoT) permit \u2014 disturbance thresholds",
                "note": "An Alteration of Terrain permit from NHDES is required when a project disturbs 100,000 sq ft or more contiguously, 50,000 sq ft or more within the protected shoreland, or any disturbance over 25% slope \u2014 covering stormwater control, sediment treatment, and earth-moving operations. AoT review runs in parallel with the building permit and the engineered stormwater plan can take months; flag this early on subdivisions, large additions on sloped lots, and small commercial pads. Most single-lot residential additions stay under thresholds, but driveway and septic regrading on shoreland lots routinely cross the 50,000 sq ft trigger.",
                "applies_to": "Site-development projects exceeding AoT disturbance thresholds",
                "source": "https://www.des.nh.gov/land/land-development"
            },
            {
                "title": "Floodplain construction \u2014 local ordinance + FEMA elevation certificate",
                "note": "New Hampshire delegates floodplain regulation to municipalities under the National Flood Insurance Program; if the parcel is in a Special Flood Hazard Area (Zone A, AE, or VE on the FIRM), the lowest floor of new construction and substantial improvements must be elevated to or above the Base Flood Elevation, with an Elevation Certificate required pre- and post-construction. Many NH towns add freeboard (typically +1 or +2 ft) above BFE \u2014 confirm the local ordinance, not just the FEMA minimum. Substantial improvement (\u226550% of market value) triggers full floodplain compliance even on existing structures.",
                "applies_to": "Construction in FEMA-mapped Special Flood Hazard Areas",
                "source": "https://www.nheconomy.com/getmedia/ce650a9e-ab1e-4c51-9afa-482e728cb730/Floodplain-Mang-Handbook.pdf"
            },
            {
                "title": "Solar PV / battery interconnection \u2014 Puc 904 application is a parallel filing",
                "note": "Net-metered solar PV and battery storage systems require an Interconnection Application filed with the serving electric utility under N.H. Admin. Code Puc 900, with the application submitted by certified mail and a dated acknowledgment of receipt obtained per Puc 904.02. This is a separate process from the local electrical permit and the OPLC-licensed electrician's installation \u2014 do not energize until both the AHJ inspection passes and the utility issues Permission to Operate. Eversource, Unitil, NHEC, and Liberty each have slightly different forms; confirm the serving utility before submitting.",
                "applies_to": "Grid-tied solar PV and energy-storage installations",
                "source": "https://www.law.cornell.edu/regulations/new-hampshire/N-H-Admin-Code-SS-Puc-904.02"
            },
            {
                "title": "Local fire code amendments \u2014 HB 428 (2025) altered municipal authority",
                "note": "HB 428, enacted in the 2025 session, modifies how municipalities may adopt local amendments to the state fire code and building code, narrowing the procedure and timing for those amendments. Always pull the local amendment list directly from the AHJ rather than assuming the unamended state code controls \u2014 local fire code overlays (sprinklers, knox box, fire-lane geometry) frequently survive even where building-code amendments do not. The Fire Marshal's Office maintains the searchable amendment library.",
                "applies_to": "Confirming which local amendments are still enforceable post-HB 428",
                "source": "https://www.nhmunicipal.org/sites/default/files/uploads/Guidance_Documents/changes-to-building-code-laws-in-2025-guide-municipalties.pdf"
            },
            {
                "title": "AHJ split \u2014 State Fire Marshal enforces where no local enforcement exists",
                "note": "The State Building Code applies in every municipality statewide and sets the minimum, but enforcement is primarily local. In towns without a building inspector or building department (common in smaller NH municipalities), the State Fire Marshal's Office is the default authority having jurisdiction for the State Building Code. Always verify whether the town has a designated building inspector before assuming local plan review \u2014 in unincorporated places and the small towns that have opted out of local enforcement, plan review and inspections route through the Fire Marshal.",
                "applies_to": "Determining the correct AHJ in small or unincorporated NH jurisdictions",
                "source": "https://www.firemarshal.dos.nh.gov/laws-rules-regulatory/state-building-code"
            },
            {
                "title": "No statewide residential permit shot clock \u2014 local timelines govern",
                "note": "Unlike California's 60-day ADU shot clock, New Hampshire has no statewide ministerial review deadline for residential building permits or ADUs; review timelines are set locally by each municipality's zoning ordinance and building department procedures. Planning Board site-plan or subdivision review is governed by RSA 676:4 timelines, but pure building-permit review is not. Build schedule contingencies around the specific town's published turnaround, and use RSA 676:4 only when Planning Board approval is in the path.",
                "applies_to": "Schedule planning for NH residential permits",
                "source": "https://www.nhmunicipal.org/sites/default/files/uploads/Guidance_Documents/adus_guidance_revised_nov25.pdf"
            },
            {
                "title": "Wetlands Priority Resource Area (PRA) \u2014 heightened review tier",
                "note": "Wetlands work that touches a Priority Resource Area under RSA 482-A \u2014 including bogs, peatlands, sand-dune systems, designated rivers under the Rivers Management and Protection Program, and undeveloped tidal buffer zones \u2014 is not eligible for the Minimum Impact expedited tier and gets bumped to Minor or Major Impact review with public notice. Confirm PRA status via the NHDES Wetlands GIS layer before quoting; misclassifying a PRA project as Minimum Impact is a frequent cause of permit denial and re-submission. Mitigation may also be required for any PRA disturbance.",
                "applies_to": "Wetlands permit tier classification near sensitive resources",
                "source": "https://www.des.nh.gov/sites/g/files/ehbemt341/files/documents/2020-01/wb-25.pdf"
            }
        ]
    },
    "ME": {
        "name": "Maine expert pack",
        "expert_notes": [
            {
                "title": "MUBEC 2021 ICC adoption \u2014 effective April 7, 2025",
                "note": "Maine adopted the 2021 ICC model codes (IRC, IBC, IECC, IMC, etc.) into the Maine Uniform Building and Energy Code (MUBEC) effective April 7, 2025, replacing the prior 2015 cycle. The Commissioner of Public Safety signed off on the new edition with state amendments listed in MUBEC Rule Chapters 1-7. Confirm which code edition the AHJ is enforcing and whether plans submitted before the cutover are still vested under the 2015 code, since some jurisdictions allow a grace period.",
                "applies_to": "Permit applications crossing the 2025-04-07 code-change boundary",
                "source": "https://aiamaine.org/aiamainenews/2025/2/27/updated-maine-building-codes"
            },
            {
                "title": "MUBEC enforcement only mandatory in municipalities \u2265 4,000 population",
                "note": "The Maine Uniform Building and Energy Code must be adopted and enforced only in municipalities with a population of 4,000 or more, or in any municipality (any size) that previously adopted any building code as of September 28, 2011. Smaller towns may opt in but are not required to enforce MUBEC, which means many rural Maine jurisdictions have no local building inspector and rely on contractor self-certification. Confirm AHJ status before assuming a plan-review process exists.",
                "applies_to": "Determining AHJ split between state code, local enforcement, and unincorporated areas",
                "source": "https://www.maine.gov/dps/fmo/sites/maine.gov.dps.fmo/files/inline-files/MUBEC%20Standards%20and%20Amendments.pdf"
            },
            {
                "title": "No statewide general contractor license \u2014 only electrical and plumbing",
                "note": "Maine is one of roughly 15 states that does NOT issue a statewide general contractor license. The only construction trades the state licenses are electricians (Electricians' Examining Board) and plumbers (Plumbers' Examining Board). General contractors, framers, roofers, and remodelers can legally operate without any state credential \u2014 but municipalities may impose local registration, and a 2025 legislative push to fund a statewide GC licensing program is still pending appropriations.",
                "applies_to": "Verifying contractor credentials for general construction work in Maine",
                "source": "https://www.procore.com/library/maine-contractors-license"
            },
            {
                "title": "Maine electrical permit \u2014 separate state-issued, not bundled with building permit",
                "note": "All electrical work in Maine requires a separate electrical permit issued through the Electricians' Examining Board (not through the local building department). Permits can be applied for online 24/7 through the Office of Professional and Occupational Regulation; only Master, Limited, or Journeyman electricians with valid Maine licenses may pull them. This is a parallel filing \u2014 a building permit alone does not authorize electrical work, and inspections are scheduled directly with the state inspector or a municipal inspector under contract.",
                "applies_to": "Any project involving electrical wiring, service upgrades, EV chargers, or solar PV",
                "source": "http://www.maine.gov/pfr/professionallicensing/professions/electricians/licensing/electrical-permit"
            },
            {
                "title": "HVAC licensed through the Fuel Board \u2014 no separate HVAC contractor license",
                "note": "Maine does not issue a standalone HVAC contractor license. Heating, ventilation, and fuel-burning appliance work is regulated through the Maine Fuel Board (oil, propane, natural gas technicians) under the Office of Professional and Occupational Regulation. Refrigerant and cooling-only work generally requires federal EPA Section 608 certification but no state license. For combination jobs (heat pump + gas backup), confirm the installer holds the appropriate Fuel Board credential for the fuel type before signing the permit application.",
                "applies_to": "HVAC, heat pump, boiler, and fuel-burning appliance installations",
                "source": "https://adaptdigitalsolutions.com/articles/maine-contractor-license-requirements/"
            },
            {
                "title": "Mandatory Shoreland Zoning \u2014 250-foot setback from protected waters",
                "note": "Maine's Mandatory Shoreland Zoning Act (38 M.R.S. \u00a7\u00a7435-449) requires every municipality to adopt zoning controls within 250 feet of the normal high-water line of great ponds, rivers, coastal wetlands, and within 75 feet of certain streams. The Maine DEP provides technical assistance, but enforcement is at the municipal Code Enforcement Officer level. New construction, expansions over 30%, and earthmoving in the shoreland zone trigger a separate municipal shoreland permit on top of any building permit \u2014 verify the parcel's distance to mapped resources before designing.",
                "applies_to": "Any construction within 250 ft of lakes, rivers, coastal wetlands, or 75 ft of streams",
                "source": "https://www.maine.gov/dep/land/slz/"
            },
            {
                "title": "DEP NRPA permit for activity in or adjacent to protected natural resources",
                "note": "The Natural Resources Protection Act (38 M.R.S. \u00a7480-A et seq.) requires a Maine DEP permit when an activity is located in, on, or over a protected natural resource (coastal wetland, freshwater wetland of special significance, great pond, river, stream, fragile mountain area) OR adjacent to one. NRPA review is separate from local building permits and from Shoreland Zoning. Tiers run from Permit-by-Rule (PBR \u2014 fast-track, fixed checklist) up to full Individual Permits depending on impact area; most residential additions near wetlands qualify for PBR if they meet the standards.",
                "applies_to": "Construction in, over, or adjacent to mapped wetlands, streams, ponds, or coastal resources",
                "source": "https://www.maine.gov/dep/land/nrpa/"
            },
            {
                "title": "NRPA \u00a7480-Q exemptions \u2014 when no state environmental permit is required",
                "note": "Title 38 \u00a7480-Q lists activities that do NOT require an NRPA permit even if near a protected resource \u2014 including maintenance and repair of existing structures, certain agricultural activities, and emergency repairs, provided the work stays solely within the specified area and meets the listed conditions. Don't waste cycle time filing an NRPA application for in-kind maintenance that clearly falls within \u00a7480-Q. However, the exemption is narrow: any expansion of footprint or change in use typically pulls the project back under NRPA.",
                "applies_to": "Repair, maintenance, and minor work near protected natural resources",
                "source": "https://www.mainelegislature.org/legis/statutes/38/title38sec480-q.html"
            },
            {
                "title": "DEP \u00a7344-B published processing timetables \u2014 escalation lever",
                "note": "Title 38 \u00a7344-B requires the DEP Commissioner to determine and annually publish a processing time for each type of permit or license issued by the department. If a DEP permit (NRPA, Site Location of Development, Stormwater) blows past its published timetable, the applicant has documented grounds to escalate to the Commissioner's office for status. Pull the current published times before quoting any DEP-permit-dependent project schedule, since they are updated yearly and vary by permit class.",
                "applies_to": "Projects requiring any Maine DEP permit (NRPA, Site Law, Stormwater, MEPDES)",
                "source": "https://www.mainelegislature.org/legis/statutes/38/title38sec344-B.html"
            },
            {
                "title": "ADU statewide permit timeline \u2014 typically 1 to 6 months",
                "note": "The Maine ADU Guide reports the permitting phase for an accessory dwelling unit typically takes 1-6 months, with overall project timelines of 12-18 months and outliers at 24+ months. Maine has no statewide ministerial 'shot clock' equivalent to California's 60-day ADU rule \u2014 review pace is set by the local CEO and varies dramatically between Portland-area towns and rural municipalities. Quote conservatively and front-load septic/well/shoreland review, which are usually the long pole.",
                "applies_to": "ADU projects statewide \u2014 schedule and customer expectation setting",
                "source": "https://maineaduguide.org/permitting/"
            },
            {
                "title": "CMP / utility interconnection \u2014 separate filing required for solar + battery",
                "note": "Solar PV, battery storage, and standby generators that interconnect with the grid require a separate interconnection application with the serving utility (Central Maine Power, Versant, or a consumer-owned utility) \u2014 this is parallel to and independent of the building/electrical permit. CMP's interconnection portal lists the steps, technical requirements, and fees. Do not energize the system until the utility issues Permission to Operate (PTO); contractors who skip this step routinely have to disconnect and refile.",
                "applies_to": "Grid-tied solar PV, battery storage, and interconnected standby generators",
                "source": "https://www.cmpco.com/suppliersandpartners/servicesandresources/interconnection"
            },
            {
                "title": "PUC Chapter 324 Small Generator Interconnection Procedures \u2014 tier thresholds",
                "note": "The Maine Public Utilities Commission's Small Generator Interconnection Procedures Rule (Chapter 324) sets the tiered process for distributed generation interconnection. Smaller residential PV systems (typically \u2264 25 kW inverter-based and meeting screens) qualify for the simplified Level 1/Fast Track path; larger or non-compliant systems drop to Level 2 or full study, which adds months and cost. Confirm which tier your system falls into before promising a customer install date \u2014 a project that flunks Fast Track screens can stall significantly at study.",
                "applies_to": "Residential and small commercial solar/storage interconnection sizing decisions",
                "source": "https://www.renewableenergyworld.com/energy-business/policy-and-regulation/maine-puc-amends-small-generator-interconnection-procedures-rules/"
            },
            {
                "title": "State Fire Marshal plan review \u2014 required for many non-1&2-family projects",
                "note": "The Office of State Fire Marshal performs construction plan review and issues construction permits for buildings outside the scope of the IRC (e.g., multifamily 3+ units, assembly, commercial, institutional). A request for a permit must be accompanied by a true copy \u2014 accurate dimensioned plans and specifications of the final construction. This is a parallel state filing in addition to the local building permit; missing or under-detailed drawings are the most common cause of Fire Marshal rejection and weeks of resubmittal delay.",
                "applies_to": "Multifamily, commercial, assembly, and institutional construction statewide",
                "source": "http://www.maine.gov/dps/fmo/inspections-plans-review/construction"
            },
            {
                "title": "Consumer protection \u2014 AG Division enforcement of contractor disputes",
                "note": "Because Maine has no general contractor licensing board, consumer disputes against unlicensed-trade contractors are handled by the Attorney General's Consumer Protection Division under 5 M.R.S. (Unfair Trade Practices Act). The AG can investigate and pursue enforcement against deceptive practices, but cannot suspend a 'license' that doesn't exist \u2014 remedies are restitution, civil penalties, and injunctions. Maine's Home Construction Contracts Act (10 M.R.S. \u00a7\u00a71486-1490) requires written contracts for any residential work over $3,000, with specific disclosures; missing contract elements weaken the contractor's position in any dispute.",
                "applies_to": "Residential construction contracts and consumer-dispute exposure statewide",
                "source": "https://mainecontractorauthority.com/maine-contractor-complaints-and-disputes"
            }
        ]
    },
    "RI": {
        "name": "Rhode Island expert pack",
        "expert_notes": [
            {
                "title": "Rhode Island State Building Code amendments effective December 1, 2025",
                "note": "The Rhode Island State Building Code amendments published by the State Building Code Commission take effect December 1, 2025 statewide. Applications submitted before Dec 1, 2025 may still be reviewed under the prior edition, but anything stamped after that date must comply with the updated amendments. Confirm in writing with the AHJ which code edition will govern review before finalizing drawings, especially for projects straddling the boundary.",
                "applies_to": "Permit applications crossing the 2025-12-01 code-change boundary",
                "source": "https://rbfc.ri.gov/media/46/download"
            },
            {
                "title": "2024 IECC adopted \u2014 first in the Northeast, effective November 2024",
                "note": "Rhode Island became the first Northeast state to adopt the 2024 IECC, with the new energy code effective November 2024. The 2024 IECC mandates electric-readiness provisions (heat-pump-ready, EV-ready, induction-ready raceway/circuits), tighter envelope U-factors, and blower-door plus duct-leakage testing on new residential. Drawings must show these provisions explicitly or expect plan-check rejection.",
                "applies_to": "New residential construction and additions altering conditioned space",
                "source": "https://energygeeksinc.com/how-the-2024-iecc-changes-residential-building-in-rhode-island/"
            },
            {
                "title": "Updated RI Energy Code with Appendices RK \u2014 effective December 1, 2025",
                "note": "Rhode Island published an updated Energy Code that became effective December 1, 2025, adding Appendices RK covering electrification/electric-readiness measures on top of the 2024 IECC base. New residential plans submitted on or after Dec 1, 2025 must demonstrate panel capacity and conduit pathways for future electric space heating, water heating, cooking, and EV charging where Appendix RK applies.",
                "applies_to": "Residential permits filed on or after 2025-12-01",
                "source": "https://neep.org/blog/building-energy-codes-roundup-2025-regional-progress-and-pushback"
            },
            {
                "title": "Mandatory CRLB registration for all residential and commercial contractors",
                "note": "Rhode Island General Laws require any contractor or subcontractor performing residential or commercial construction, remodeling, or repair work to be registered with the Contractors' Registration and Licensing Board (CRLB). Verify the registration is active on crb.ri.gov before signing a contract \u2014 unregistered contractors cannot pull permits, can be denied lien rights, and homeowners can void contracts. Renewals lapse silently, so check status at quote time, not just at intake.",
                "applies_to": "All paid residential and commercial construction work in Rhode Island",
                "source": "https://crb.ri.gov/general-contractor-registration"
            },
            {
                "title": "Trade licenses live at RI DLT \u2014 separate from CRLB registration",
                "note": "Trade licensing is decoupled from the CRLB and is administered by the RI Department of Labor & Training (DLT) Professional Regulation division, which runs the Board of Examiners of Electricians, the Mechanical Board (HVAC, refrigeration, sheet-metal, pipefitting), the plumbing examiners, and the Board of Hoisting Engineers. A general contractor with active CRLB registration still needs DLT-licensed electrical, plumbing, mechanical, and hoisting subs on the job. Confirm BOTH the CRLB registration and the DLT trade card before work starts.",
                "applies_to": "Electrical, plumbing, HVAC/mechanical, and hoisting subcontractors",
                "source": "https://dlt.ri.gov/regulation-and-safety/professional-regulation"
            },
            {
                "title": "30-day municipal permit review shot clock",
                "note": "By Rhode Island state statute, a city or town has 30 days to review a building permit application after a complete submission. If the AHJ blows the 30-day clock without issuing or denying, escalate by filing a complaint with the State Building Office (within the State Building Code Commission). Always document the date of the complete-application submission \u2014 that is when the clock starts, and an incomplete submission resets it.",
                "applies_to": "Municipal building permit applications statewide",
                "source": "https://www.facebook.com/groups/rirealestateinvestors/posts/5955246111265907/"
            },
            {
                "title": "ADUs allowed by right \u2014 no special use permit or discretionary zoning approval",
                "note": "Under Rhode Island state law, accessory dwelling units must be allowed by right on residentially-zoned parcels, meaning no special use permit, variance, or discretionary zoning hearing can be required to authorize the ADU. Municipalities can still impose objective standards (setbacks, height, square-footage caps, parking), but they cannot route the ADU through a public hearing or subjective design review. If a town demands a special use permit for a conforming ADU, cite state preemption and escalate.",
                "applies_to": "ADU projects on residentially-zoned lots",
                "source": "https://www.zookcabins.com/regulations/adu-regulations-in-rhode-island"
            },
            {
                "title": "CRMC Assent required for any work on a coastal feature",
                "note": "The Rhode Island Coastal Resources Management Council (CRMC) requires a permit (Assent) for any construction or alteration on a coastal feature \u2014 coastal beach, barrier, dune, coastal wetland, headland, bluff, or cliff \u2014 across the 21 coastal municipalities. CRMC review is parallel to and independent of the local building permit; the AHJ should not issue a building permit on coastal-feature work until CRMC has Assented. File CRMC and the local building application in parallel to keep the schedule.",
                "applies_to": "Construction within or affecting CRMC-jurisdictional coastal features",
                "source": "https://www.crmc.ri.gov/applicationforms.html"
            },
            {
                "title": "RIDEM Freshwater Wetlands permit \u2014 separate parallel filing",
                "note": "The RI Department of Environmental Management (DEM) regulates freshwater wetlands and requires a Freshwater Wetlands permit for projects or activities within (or sometimes near) a Jurisdictional Area \u2014 the wetland plus the statutory perimeter/riverbank buffers. This is a separate permit from the local building permit and from any CRMC Assent; coastal projects can need all three. File the DEM application early because Jurisdictional Area determinations and field walks add weeks to the schedule.",
                "applies_to": "Construction within or near freshwater wetlands and their buffer zones",
                "source": "https://dem.ri.gov/environmental-protection-bureau/water-resources/permitting/freshwater-wetlands"
            },
            {
                "title": "AHJ split \u2014 local code official enforces; State Building Commissioner for state buildings",
                "note": "In Rhode Island the State Building Code is enforced by the local code official in each city or town, while the State Building Commissioner enforces the code on all state-owned buildings. The substantive code, amendments, and interpretations come from the state, but the actual plan reviewer and inspector is the municipal building official. Code-interpretation disputes are appealed to the State Building Code Standards Committee, not litigated at the local counter.",
                "applies_to": "All building permit reviews in Rhode Island",
                "source": "https://www.energycodes.gov/status/states/rhode-island"
            },
            {
                "title": "Statewide solar permit \u2014 one consolidated form for residential PV",
                "note": "Rhode Island has consolidated residential solar PV permitting into a single, predictable statewide solar permit process so contractors do not face inconsistent town-by-town intake forms. Use the statewide solar permit packet (with structural and electrical attachments) rather than the town's generic building permit form for rooftop residential PV. The statewide permit covers the building/electrical side only \u2014 utility net-metering interconnection is a separate, parallel filing.",
                "applies_to": "Residential rooftop solar PV systems",
                "source": "https://vishtik.com/simplifying-solar-permitting-in-rhode-island-what-you-need-to-know/"
            },
            {
                "title": "Rhode Island Energy interconnection \u2014 parallel filing for PV, storage, and EV",
                "note": "Grid-connected solar PV, battery storage, and Level 2/3 EV-charging installations require a separate interconnection application filed with Rhode Island Energy through the portalconnect.rienergy.com portal. There are tiered tracks \u2014 Simplified (small residential), Expedited, and Standard \u2014 chosen by system size and inverter rating, each with its own forms and completion documentation. The utility will not energize the system without an executed interconnection authorization, so submit it in parallel with the building permit.",
                "applies_to": "Grid-connected solar PV, energy storage, and EV charging",
                "source": "https://portalconnect.rienergy.com/RI/s/article/RI-Interconnection-Documents"
            },
            {
                "title": "EFSB jurisdiction does NOT apply to typical residential work",
                "note": "The Energy Facility Siting Board (EFSB), within the RI Public Utilities Commission, is the licensing and permitting authority for major energy facilities \u2014 and EFSB approval supersedes any other state or local license or municipal ordinance for projects in its jurisdiction. EFSB applies to large-scale generation, transmission, and major infrastructure, NOT to residential rooftop solar, ADU service upgrades, or routine commercial work. Do not waste schedule pursuing EFSB review on residential-scale projects.",
                "applies_to": "Major energy generation and transmission facilities (NOT typical residential)",
                "source": "https://ripuc.ri.gov/general-information/efsb"
            },
            {
                "title": "Coastal Resiliency and Special Flood Hazard Area overlay districts",
                "note": "Shoreline RI municipalities (e.g., South Kingstown) have adopted Special Flood Hazard Area overlay districts and Coastal Resiliency (CR) overlay districts tied to CRMC Design Elevation Maps for 100-year storm-surge vulnerability. Inside these overlays expect elevated finished-floor requirements, V-zone breakaway-wall detailing, additional freeboard, and restrictions on enclosed below-elevation space. Pull the parcel's overlay status from the town zoning map before locking foundation height, slab elevation, or grade-level enclosures.",
                "applies_to": "Coastal and flood-prone parcels in Rhode Island shoreline towns",
                "source": "https://www.southkingstownri.gov/DocumentCenter/View/3488/Special-Flood-Hazard-Area-Overlay-District-Zoning-Ordinance-Amendment"
            }
        ]
    },
    "MT": {
        "name": "Montana expert pack",
        "expert_notes": [
            {
                "title": "Montana statewide ADU enabling law (effective Jan 1, 2024)",
                "note": "Montana law (passed 2023) requires municipalities to adopt regulations allowing at least one ADU per residential lot, effective January 1, 2024. Most jurisdictions cap ADUs at 1,000 sq ft or 75% of the primary dwelling's floor area, whichever is smaller. Cities cannot ban ADUs outright in single-family zones \u2014 if a local ordinance attempts to, cite the state preemption.",
                "applies_to": "ADU jobs on single-family-zoned lots statewide",
                "source": "https://www.zookcabins.com/regulations/mt-adu-regulations"
            },
            {
                "title": "No statewide permit shot clock for residential permits",
                "note": "Unlike California or Washington, Montana has NOT enacted statewide 'shot clock' provisions setting a hard deadline for local building permit decisions \u2014 Sightline Institute identifies this as a future legislative gap. That means there is no state law you can cite to force a city to act within a fixed window; escalation has to go through local appeals or the city council, not a state-mandated deadline.",
                "applies_to": "Permit timing disputes with Montana AHJs",
                "source": "https://www.sightline.org/2025/04/25/montanas-housing-miracle-strikes-twice/"
            },
            {
                "title": "Montana 2021 IECC residential energy code with state amendments",
                "note": "Montana adopted the 2021 IECC with state-specific amendments effective June 10, 2022 for residential construction. Certified local jurisdictions get up to 90 days from notification to adopt the state code; counties had until January 29, 2025 to adopt the 2021 IECC-R with local amendments or default to the state code. Confirm which edition the AHJ is enforcing before submitting REScheck/energy compliance documents.",
                "applies_to": "New residential construction and additions affecting conditioned space",
                "source": "https://deq.mt.gov/files/Energy/Documents/Residential_Buildings_Energy_Code_Summary_2024.pdf"
            },
            {
                "title": "Montana 2024 Building Code adoption amendments \u2014 code transition rule",
                "note": "Montana's Building Codes Bureau (DLI/BSD) publishes adoption amendments specifying which model code edition (IBC, IRC, IMC, etc.) applies. Per the 2024 adoption amendments, upon the effective date of new requirements any building or project not yet permitted falls under the new edition \u2014 projects already submitted may be reviewed under the prior edition at the AHJ's discretion. Verify the active edition with the local building official before drafting plans across an adoption boundary.",
                "applies_to": "Permit applications spanning a code-edition change",
                "source": "https://bsd.dli.mt.gov/_docs/building-codes-permits/2024-BUILDING-CODE-ADOPTION-AMENDMENTS.pdf"
            },
            {
                "title": "Construction Contractor Registration (DLI) \u2014 $70 application",
                "note": "Montana requires Construction Contractor Registration through the Department of Labor & Industry (DLI) \u2014 not a license, a registration \u2014 with a non-refundable $70 application fee. Unregistered contractors can be fined and cannot legally bid public projects. Independent Contractor Exemption Certificate (ICEC) is a separate $125 filing for sole operators who carry no employees and want to opt out of workers' comp.",
                "applies_to": "All paid construction contractors operating in Montana",
                "source": "https://erd.dli.mt.gov/work-comp-regulations/montana-contractor/applications-and-forms"
            },
            {
                "title": "Electrical Contractor License \u2014 separate state board credential",
                "note": "Montana requires a state Electrical Contractor License issued by the Board of Electrical Contractors (DLI/BSD) \u2014 this is SEPARATE from the DLI Contractor Registration and from any local business license. Use the online portal application and review the Electrical Contractor checklist before submitting. An electrical permit is required for any installation in new construction, remodeling, or repair except as exempted by MCA \u00a750-60-602.",
                "applies_to": "All electrical work including EV chargers, solar PV, generators, panel upgrades",
                "source": "https://boards.bsd.dli.mt.gov/electrical/license-information/electrical-contractor"
            },
            {
                "title": "Master Plumber requirement for public/commercial work",
                "note": "Montana law requires the services of a Montana-licensed Master Plumber on all public and commercial buildings \u2014 verify the master's license at the Board of Plumbers Licensee Lookup before pulling a plumbing permit. Residential single-family work has different requirements but the responsible party on the permit must still be properly credentialed. Plumbing licensure questions: Board of Plumbers, (406) 444-6880.",
                "applies_to": "Plumbing permits on public/commercial projects and verification of residential trades",
                "source": "https://bsd.dli.mt.gov/building-codes-permits/permit-applications/plumbing-permits/"
            },
            {
                "title": "No state HVAC license \u2014 but business license required to own a shop",
                "note": "Montana does NOT require a dedicated state HVAC license to install or service heating and cooling equipment \u2014 a common surprise for out-of-state contractors. However, you must hold a business license to own an HVAC company, and any electrical or gas work crossing into those trades still triggers the electrical/plumbing licensing regimes. Don't assume the absence of an HVAC license means no permitting \u2014 duct, refrigerant, and combustion-air work still requires mechanical permits in adopted jurisdictions.",
                "applies_to": "HVAC installations and replacements statewide",
                "source": "https://www.housecallpro.com/licensing/hvac/montana/"
            },
            {
                "title": "310 Permit for stream construction \u2014 local conservation district",
                "note": "Under the Natural Streambed and Land Preservation Act ('310 Law'), any individual or corporation proposing construction in a perennial stream must apply for a 310 Permit through the LOCAL conservation district \u2014 not DNRC directly, though DNRC publishes the form. This is a parallel filing in addition to any building or floodplain permit. Submit the Joint Application early; review timelines depend on the conservation district's meeting schedule.",
                "applies_to": "Any work in or near a perennial stream, including bridges, culverts, bank stabilization, and intakes",
                "source": "https://dnrc.mt.gov/licenses-and-permits/stream-permitting/"
            },
            {
                "title": "DNRC Floodplain permit \u2014 separate from building permit",
                "note": "Work in a designated 100-year floodplain requires a separate Floodplain Permit administered through DNRC's Floodplain program (typically delegated to the local floodplain administrator). Use the Joint Application for projects also touching streams or wetlands. Note: areas inside the FEMA-mapped floodplain may not require a 310 stream permit if the work is outside the active streambed \u2014 verify which permit(s) apply before assuming both are needed.",
                "applies_to": "Construction, fill, or substantial improvements in mapped 100-year floodplains",
                "source": "https://dnrc.mt.gov/Water-Resources/Floodplains/Permitting-and-Regulations"
            },
            {
                "title": "Seller disclosure of adverse material facts (MCA 70-20-502)",
                "note": "Montana law (MCA \u00a770-20-502) requires sellers of residential real property to provide a written disclosure statement of any adverse material facts that concern the property \u2014 this includes unpermitted work, known code violations, and conditions affecting use or enjoyment. Closing out open permits and obtaining final inspection sign-offs before listing avoids triggering disclosure liability for the seller. Unresolved permit issues found post-closing can become buyer claims.",
                "applies_to": "Residential real estate transactions and pre-sale permit close-out",
                "source": "https://archive.legmt.gov/bills/mca/title_0700/chapter_0200/part_0050/section_0020/0700-0200-0050-0020.html"
            },
            {
                "title": "Contractor Registration overhaul \u2014 HB 239 / MAR 2025-209.1",
                "note": "Montana's 69th Legislature passed HB 239 (Chapter 644) revising contractor laws and transferring construction contractor registration to a department licensing program; DLI issued MAR Notice 2025-209.1 to repeal the old registration rules and adopt new ones. Confirm the current registration/license name and fee on the DLI site before applying \u2014 the program is in transition and old forms may be rejected.",
                "applies_to": "New contractor applications and renewals during the 2025-2026 transition",
                "source": "https://dli.mt.gov/_docs/rules/MAR-NOTICE-NO-2025-209.1pro-arm.pdf"
            },
            {
                "title": "Local code adoption split \u2014 IBC 2021 in certified cities, state default elsewhere",
                "note": "Montana operates a tiered AHJ model: 'certified' cities and counties enforce the IBC/IRC (e.g., Conrad adopted IBC 2021 by ordinance \u00a74-1-1); jurisdictions that have NOT been certified by DLI fall under state Building Codes Bureau enforcement, which covers commercial only \u2014 1- and 2-family dwellings in uncertified rural areas have NO state building permit requirement. Always check whether the project's jurisdiction is state-certified before quoting plan-review timelines.",
                "applies_to": "Determining which AHJ reviews a residential project",
                "source": "https://www.meltplan.com/blogs/what-building-codes-does-montana-use-a-guide-to-state-adoptions-amendments-and-enforcement"
            },
            {
                "title": "Electrical permit required for all installations \u2014 MCA 50-60-602 exemption",
                "note": "An electrical permit is required for any installation in any new construction, remodeling, or repair EXCEPT as specifically exempted by MCA \u00a750-60-602 (homeowner exemption for owner-occupied single-family dwellings under defined conditions, like-for-like minor repairs). Solar PV, EV chargers, battery storage, and standby generators do NOT qualify for the homeowner exemption when interconnected to the utility \u2014 a licensed electrical contractor must pull the permit and a state electrical inspection is required before utility connection.",
                "applies_to": "EV chargers, solar PV, batteries, generators, service upgrades, and any non-trivial electrical work",
                "source": "https://bsd.dli.mt.gov/building-codes-permits/permit-applications/electrical-permits/"
            },
            {
                "title": "ADU size cap \u2014 1,000 sq ft or 75% of primary, whichever is smaller",
                "note": "Most Montana jurisdictions cap ADUs at 1,000 square feet OR 75% of the primary home's floor area, whichever is smaller \u2014 this is the statewide baseline established by the 2024 ADU law. Some cities allow larger via local ordinance, but you cannot assume so. Design to the smaller of the two limits unless the local zoning code explicitly allows more, or your plans will be rejected at zoning review before reaching building plan check.",
                "applies_to": "ADU sizing decisions during schematic design",
                "source": "https://www.zookcabins.com/regulations/mt-adu-regulations"
            }
        ]
    },
    "DE": {
        "name": "Delaware expert pack",
        "expert_notes": [
            {
                "title": "No statewide residential building code \u2014 counties and municipalities adopt their own",
                "note": "Delaware has NO unified statewide building code for 1-2 family residential construction. Per 16 Del.C. Chapter 76, the Levy Court of Kent County and the County Councils of New Castle and Sussex Counties each adopt and enforce their own building, plumbing, mechanical, and electrical codes. Always confirm which IRC/IBC edition the specific AHJ has adopted before producing drawings \u2014 Sussex adopted the 2021 IBC/IRC on May 17, 2022, while New Castle and Kent may be on different cycles.",
                "applies_to": "All residential and commercial construction statewide \u2014 code edition varies by jurisdiction",
                "source": "https://delcode.delaware.gov/title16/c076/index.html"
            },
            {
                "title": "State Energy Conservation Code \u2014 2026 amendment effective April 11, 2026",
                "note": "Delaware's State Energy Conservation Code (7 DE Admin. Code 2101) is regulated by DNREC, not by the building code authorities. The latest amendment was issued March 10, 2026 with an effective date of April 11, 2026. Permit applications submitted around the changeover should confirm with the AHJ which edition of the IECC governs the project, since 16 Del.C. \u00a77602 also requires zero-net-energy-capable residential buildings to phase in.",
                "applies_to": "All new construction and major renovations statewide affecting the thermal envelope or HVAC",
                "source": "https://regulations.delaware.gov/register/april2026/final/29%20DE%20Reg%20875%2004-01-26"
            },
            {
                "title": "No statewide general-contractor license \u2014 DOL contractor registration is mandatory",
                "note": "Delaware does NOT require a state-issued general contractor license, but 19 Del.C. Chapter 36 requires every business performing construction services or maintenance in Delaware to register with the Delaware Department of Labor. This registration is separate from the Division of Revenue business license \u2014 both are required. Working without DOL contractor registration is the single most common Delaware compliance failure for out-of-state GCs.",
                "applies_to": "Any contractor (in-state or out-of-state) performing paid construction work in Delaware",
                "source": "https://onestop.delaware.gov/Operate_Contractors"
            },
            {
                "title": "Plumbing, HVACR, and Restricted HVACR licensing \u2014 DPR Board",
                "note": "The Delaware Board of Plumbing, Heating, Ventilation, Air Conditioning and Refrigeration Examiners (under the Division of Professional Regulation) issues Master Plumber, Master HVACR, and Master Restricted HVACR licenses. Restricted licenses limit the holder to a specialty (e.g., air conditioning only). Verify license type and active status on the DPR lookup before quoting \u2014 a Restricted HVACR cannot legally pull a full mechanical permit covering combustion heating.",
                "applies_to": "All plumbing and HVAC/R work statewide",
                "source": "https://dpr.delaware.gov/boards/plumbers/"
            },
            {
                "title": "Residential plumbing permits \u2014 licensed plumber or homeowner only",
                "note": "Delaware's residential plumbing permit program (administered through Business First Steps) issues permits only to Delaware-licensed plumbers or to the homeowner doing work on their own primary residence. A general contractor cannot pull a plumbing permit on a homeowner's behalf without the licensed master plumber on the application. Plan for the master plumber's license number to appear on every plumbing permit submission.",
                "applies_to": "All residential plumbing work statewide",
                "source": "https://firststeps.delaware.gov/plumbing_residential/"
            },
            {
                "title": "HVAC permitting \u2014 5-phase county process",
                "note": "Delaware HVAC permits follow a consistent 5-phase structure (application, plan review, permit issuance, inspections, final approval) across all three counties, but documentation requirements and review timelines differ between New Castle, Kent, and Sussex. Always pull the AHJ-specific HVAC submittal checklist before drafting load calcs \u2014 a Manual J/S/D package accepted in Sussex may be returned for additional information in New Castle.",
                "applies_to": "HVAC equipment installation, replacement, and ductwork modifications",
                "source": "https://delawarehvacauthority.com/delaware-hvac-permit-requirements"
            },
            {
                "title": "Executive Order 18 \u2014 120-business-day target for qualifying housing permits",
                "note": "Governor Matt Meyer signed an executive order on Feb 26, 2026 directing state agencies to streamline permitting after timelines stretched to 18-24 months on major projects. Qualifying housing projects now have a TARGET (not a hard shot clock) permitting timeline of 120 business days, with priority for infill development. This is an administrative target, not statutory \u2014 it does NOT create a deemed-approved remedy if missed, but it is a documented escalation lever for stalled state-level reviews.",
                "applies_to": "State-level reviews on housing, broadband, and infrastructure projects",
                "source": "https://news.delaware.gov/2026/02/26/governor-matt-meyer-signs-executive-order-streamlining-state-permitting-regulations/"
            },
            {
                "title": "State-level building permit pre-review for state-funded/state-land projects",
                "note": "Per 2 Del. Admin. Code \u00a72152-6.0, a building permit issued by the county or municipality having land-use jurisdiction must FIRST be reviewed by the Delaware Office of State Planning Coordination when the project involves state land, state funding, or PLUS-review thresholds. This is a parallel filing in addition to the local building permit. Skipping the OSPC pre-review is a common cause of permit revocation late in construction.",
                "applies_to": "Projects on state land, state-funded, or triggering PLUS review thresholds",
                "source": "https://www.law.cornell.edu/regulations/delaware/2-Del-Admin-Code-SS-2152-6.0"
            },
            {
                "title": "Coastal Construction Permit \u2014 required seaward of the building line",
                "note": "DNREC's Division of Watershed Stewardship requires a Coastal Construction Permit (CCP) or Coastal Construction Letter of Approval for ANY construction seaward of the established building line. This is a separate, parallel filing on top of the county building permit and applies to dunes, beaches, and shoreline parcels in Sussex County in particular. Start the CCP application early \u2014 review and any required public notice can add weeks to the schedule.",
                "applies_to": "Any construction, additions, or alterations seaward of the coastal building line",
                "source": "https://dnrec.delaware.gov/watershed-stewardship/beaches/coastal-construction/permits/"
            },
            {
                "title": "Wetlands & Subaqueous Lands Permit + 401 Water Quality Certification",
                "note": "DNREC Wetlands and Subaqueous Lands permits are required for tidal/non-tidal wetland impacts and bottomlands work. A project-specific 401 Water Quality Certification is generally required for any project that needs an individual permit from the U.S. Army Corps of Engineers \u2014 file these in parallel, not sequentially. Driveway crossings, dock construction, and bulkhead repair all routinely trigger this overlay.",
                "applies_to": "Construction that disturbs wetlands, waterways, or subaqueous lands",
                "source": "https://dnrec.delaware.gov/water/wetlands/permits/"
            },
            {
                "title": "Coastal Zone Act \u2014 heavy industry restrictions, NOT residential",
                "note": "The Delaware Coastal Zone Act (and the 2017 Conversion Permit amendment) governs heavy industrial uses on 14 grandfathered sites within the coastal zone \u2014 it does NOT regulate residential ADUs, single-family homes, or typical commercial fit-outs. Contractors often confuse the CZA with the Coastal Construction Permit; they are different programs with different triggers. For residential shoreline work, focus on the CCP and wetlands permits, not the CZA.",
                "applies_to": "Heavy industrial siting only \u2014 NOT residential or light commercial",
                "source": "https://dnrec.delaware.gov/coastal-zone-act/"
            },
            {
                "title": "Sussex County structure-specific permit triggers",
                "note": "Per 9 Del.C. Chapter 63, Sussex County requires a building permit specifically for the construction, erection, placement, or alteration of smokestacks, silos, flagpoles, elevated tanks, power lines, and similar structures \u2014 these are easily missed because they aren't 'buildings.' A backyard ham-radio tower or replacement flagpole on a Sussex parcel needs a permit even when it would be exempt elsewhere. Confirm height-triggered review thresholds with the Sussex County Planning & Zoning office.",
                "applies_to": "Towers, poles, tanks, and similar non-building structures in Sussex County",
                "source": "https://delcode.delaware.gov/title9/c063/index.html"
            },
            {
                "title": "ADU regulations \u2014 zoning-driven, not statewide",
                "note": "Delaware has no statewide ADU enabling statute (unlike California's AB 881 or Vermont's S.100). ADU permissibility, size limits, owner-occupancy requirements, and parking minimums are governed entirely by county and municipal zoning codes. Always start with the local zoning ordinance \u2014 many Delaware jurisdictions still treat ADUs as conditional or special-exception uses requiring a public hearing, NOT ministerial review.",
                "applies_to": "Accessory dwelling unit projects statewide",
                "source": "https://www.zookcabins.com/regulations/adu-regulations-in-delaware"
            },
            {
                "title": "New Castle County contractor licensing \u2014 county-level overlay",
                "note": "New Castle County maintains its own contractor licensing program separate from the state DOL registration and DPR trade boards. Contractors performing work in unincorporated New Castle County must hold the county license in addition to state-level registrations. Contact NCC Licensing at (302) 395-5420 or licensing@newcastlede.gov to confirm scope \u2014 this is a frequent gap when out-of-county contractors bid NCC jobs.",
                "applies_to": "Contractors working in unincorporated New Castle County",
                "source": "https://www.newcastlede.gov/180/Contractor-Licensing"
            }
        ]
    },
    "SD": {
        "name": "South Dakota expert pack",
        "expert_notes": [
            {
                "title": "No statewide general contractor license in South Dakota",
                "note": "South Dakota does NOT issue a state-level general contractor license \u2014 there is no equivalent to California's CSLB. Many municipalities and counties (Sioux Falls, Rapid City, Pierre, etc.) impose their own contractor registration or licensing on residential and commercial work, so the requirement is purely local. Verify the AHJ's contractor registry before quoting; assuming 'no state license = no license needed' is one of the most common rejection causes for out-of-state contractors.",
                "applies_to": "All construction trades operating in South Dakota except plumbing and electrical",
                "source": "https://contractorsliability.com/blog/general-contractor-license-south-dakota/"
            },
            {
                "title": "South Dakota Plumbing Commission state license required for plumbing work",
                "note": "Unlike GC and HVAC, plumbing IS regulated at the state level by the South Dakota Plumbing Commission (SDPlumbing@state.sd.us, 605.773.3429). Pipe installations, system repairs, and plumbing design must be performed by a state-licensed plumber, and the contract/permit will be voided without a valid Commission license. Confirm the plumber's active SD Plumbing Commission license before scheduling rough-in.",
                "applies_to": "All plumbing work statewide including water, waste, vent, and gas piping",
                "source": "https://dlr.sd.gov/plumbing/licensing.aspx"
            },
            {
                "title": "No statewide HVAC contractor license \u2014 municipal-only regulation",
                "note": "South Dakota has NO state HVAC license; licensing varies city by city. Sioux Falls runs its own two-tier HVACR licensing system, while many smaller jurisdictions require no HVAC-specific credential at all. Always check the specific municipality's licensing page before bidding \u2014 assuming reciprocity from a neighboring city's license is invalid.",
                "applies_to": "HVAC/mechanical contractors working in South Dakota municipalities",
                "source": "https://www.servicetitan.com/licensing/hvac/south-dakota"
            },
            {
                "title": "HVAC parallel filings \u2014 State Electrical Commission and Plumbing Commission",
                "note": "Even though HVAC itself isn't state-licensed, an HVAC install often triggers TWO separate state filings: (1) any electrical work (disconnects, whips, new circuits) requires a South Dakota State Electrical Commission licensee and a state electrical inspection, and (2) gas piping for furnaces, boilers, or rooftop units must be performed under a Plumbing Commission licensee. Schedule the state electrical and plumbing inspections separately from the local mechanical permit \u2014 they are not bundled.",
                "applies_to": "HVAC installations involving electrical circuits or gas piping",
                "source": "https://contractorlicenserequirements.com/assets/south-dakota-hvac-roadmap-2026.pdf"
            },
            {
                "title": "180-day construction permit shot clock under ARSD 74:36:20:10",
                "note": "South Dakota Administrative Rule 74:36:20:10 requires the department to recommend issuance or denial of a construction permit within 180 days of a complete application. This is the state-level analog to a permit shot clock, but note it is far longer than California's 60-day ADU clock \u2014 set client expectations accordingly. If the 180-day window is exceeded without action, escalate in writing citing the rule.",
                "applies_to": "State-issued construction permits in South Dakota",
                "source": "https://www.law.cornell.edu/regulations/south-dakota/ARSD-74-36-20-10"
            },
            {
                "title": "SDCL 11-10 \u2014 building codes are local-option, not state-mandated for 1\u20132 family homes",
                "note": "Under South Dakota Codified Law Chapter 11-10, the state does NOT impose a mandatory residential building code; adoption is at the local governing body's discretion. This means a parcel in unincorporated territory may have NO building code enforcement at all, while the next town over enforces a full IRC. Always confirm which (if any) edition of the IRC/IECC the AHJ has adopted before assuming a code minimum applies.",
                "applies_to": "1- and 2-family residential construction statewide",
                "source": "https://sdlegislature.gov/Statutes/11-10"
            },
            {
                "title": "2018 IRC + 2018 IECC adopted with amendments effective July 16, 2019",
                "note": "In August 2019, South Dakota adopted the 2018 IRC and 2018 IECC with state amendments for residential construction, with the change taking effect July 16, 2019. This is the current statewide reference for jurisdictions that opt into a code; older 2009/2012 IRC plan sets will be rejected by AHJs that have updated. Verify the AHJ's adopted edition because some jurisdictions still enforce the prior cycle.",
                "applies_to": "Residential drawings submitted to AHJs that have adopted the state code",
                "source": "https://database.aceee.org/state/buildings-summary"
            },
            {
                "title": "2009 IECC voluntary residential energy standard under SDCL 11-10-7",
                "note": "SB 94 (2011) codified the 2009 IECC as a VOLUNTARY residential energy standard at SDCL 11-10-7 \u2014 it applies only in jurisdictions that have explicitly adopted it. Many rural counties have not adopted any energy code, so blower-door, U-value, and envelope-compliance requirements may be unenforceable. Do not assume IECC compliance documentation is required; confirm with the AHJ before pricing energy upgrades.",
                "applies_to": "Energy code compliance for residential new construction",
                "source": "https://www.mwalliance.org/south-dakota/south-dakota-building-energy-codes"
            },
            {
                "title": "County code default \u2014 2021 IECC-R adoption deadline January 29, 2025",
                "note": "Counties had until January 29, 2025 to adopt the 2021 IECC-R with their own local amendments; if a county failed to act, the state code becomes the county code by default. This means residential projects permitted in 2026 in counties that missed the deadline are now subject to 2021 IECC-R enforcement automatically. Confirm county adoption status before relying on older energy compliance paths.",
                "applies_to": "Residential energy code in unincorporated county jurisdictions",
                "source": "https://database.aceee.org/state/residential-codes"
            },
            {
                "title": "SD Game, Fish & Parks Shoreline Alterations Permit",
                "note": "Any construction, dredging, fill, riprap, or dock work that alters the bottom or shoreline of a public water body in South Dakota requires a Shoreline Alterations Permit from SDGFP. All construction must be completed before the permit expires; the permit may be renewed without a new application or plans if work is ongoing. File this in PARALLEL with the local building permit \u2014 it is not bundled, and the AHJ will not issue a CO until SDGFP signs off.",
                "applies_to": "Lakefront, riverfront, and reservoir-adjacent residential construction",
                "source": "https://www.brookingscountysd.gov/DocumentCenter/View/3050/SDGFP-Guidelines-for-Alter-of-Bottom-Lands-or-Lake-Shores"
            },
            {
                "title": "Homeowner plumbing permit limited to single-family dwellings only",
                "note": "The Plumbing Commission's homeowner permit pathway is restricted to a single-family DWELLING \u2014 it explicitly excludes detached garages, ADUs, workshops, barns, or any other structure on the parcel. Owners attempting to self-permit plumbing for an ADU, pool house, or accessory structure will be rejected and must hire a licensed plumber. Flag this early on owner-builder ADU and outbuilding projects.",
                "applies_to": "Owner-builder plumbing on accessory structures and ADUs",
                "source": "https://dlr.sd.gov/plumbing/homeowner_plumbing.aspx"
            },
            {
                "title": "Sioux Falls Residential Building Contractor License required for 1- & 2-family work",
                "note": "The City of Sioux Falls requires a Residential Building Contractor's License of ALL contractors performing work on 1- and 2-family dwellings and townhomes inside city limits. This is separate from any trade license and from the (nonexistent) state GC license. Out-of-state and rural contractors taking jobs in Sioux Falls routinely get blocked at permit intake by missing this credential.",
                "applies_to": "Residential contractors operating inside Sioux Falls city limits",
                "source": "https://www.siouxfalls.gov/business-permits/permits-licenses-inspections/licensing/contractor-licensing/building-contractor-residential"
            },
            {
                "title": "Sioux Falls ADU size cap \u2014 75% of primary dwelling under \u00a7159.305",
                "note": "Sioux Falls Zoning Code \u00a7159.305 caps ADU finished floor area at 75% of the primary dwelling's finished floor area, and the ADU must meet all underlying district setbacks. Unlike California's SB 9 / Govt Code 65852.2 ministerial path, South Dakota has NO statewide ADU shot clock or ministerial-review preemption \u2014 each jurisdiction sets its own rules. Pull the local ADU ordinance before sizing the unit; the 75% rule is Sioux Falls-specific and varies elsewhere.",
                "applies_to": "ADU projects in Sioux Falls and reference for SD jurisdictions generally",
                "source": "https://codelibrary.amlegal.com/codes/siouxfalls/latest/siouxfalls_sd/0-0-0-81216"
            },
            {
                "title": "County zoning authority and district splits under SDCL Chapter 11-2",
                "note": "Under SD Codified Law Chapter 11-2, county boards may divide the county into zoning districts of any number, shape, or area they deem fit \u2014 meaning unincorporated parcels can sit in radically different overlays from neighboring properties. The same county may have one township enforcing IRC + setbacks and another with no zoning at all. Always pull the county zoning map and the specific district's ordinance text rather than assuming uniform county-wide rules.",
                "applies_to": "Unincorporated and county-jurisdiction residential projects",
                "source": "https://sdlegislature.gov/Statutes/11-2"
            }
        ]
    },
    "ND": {
        "name": "North Dakota expert pack",
        "expert_notes": [
            {
                "title": "NDCC ch. 43-07 contractor license \u2014 $4,000 project threshold",
                "note": "Any person performing construction work where the cost, value, or price of the job exceeds $4,000 must hold a North Dakota contractor's license issued by the Secretary of State under NDCC ch. 43-07. License classes are based on dollar value of work (Class A unlimited, Class B up to $500,000, Class C up to $300,000, Class D up to $100,000). Verify the contractor's class covers the contract value before signing \u2014 an under-classed license is treated the same as no license for lien-rights and enforcement purposes.",
                "applies_to": "All construction projects in North Dakota with cost, value, or price over $4,000",
                "source": "https://www.sos.nd.gov/business/licensing-registration/contractors"
            },
            {
                "title": "No statewide HVAC contractor license \u2014 municipal-only",
                "note": "North Dakota does NOT mandate a state-level license for HVAC contractors, technicians, or apprentices. Authority is delegated to municipalities, so an HVAC contractor licensed in Fargo may be unlicensed for the same work in Bismarck or Minot. Always check the city/county AHJ for a separate mechanical/HVAC registration in addition to the SOS contractor license \u2014 assuming statewide reciprocity is a common rejection reason.",
                "applies_to": "HVAC and mechanical work statewide",
                "source": "https://www.servicetitan.com/licensing/hvac/north-dakota"
            },
            {
                "title": "Electrical contractor licensing through NDSEB (parallel filing)",
                "note": "Electrical contractors must license through the North Dakota State Electrical Board (NDSEB), which is a SEPARATE filing from the Secretary of State contractor registration under NDCC 43-07. The NDSEB process requires first registering the business with the SOS (701-328-2900), then filing with NDSEB for the electrical contractor license. Both must be active before pulling an electrical permit \u2014 missing the NDSEB step is a frequent cause of permit denial even when the SOS license is valid.",
                "applies_to": "Any electrical work requiring a permit in North Dakota",
                "source": "https://www.ndseb.com/licensing/electrical-contractor-guidelines-requirements/"
            },
            {
                "title": "Home rule state \u2014 no mandatory statewide enforcement of the State Building Code",
                "note": "North Dakota is a home rule state: adoption of the State Building Code is permissive at the local level, not mandatory. Jurisdictions that do adopt may further amend the code to conform to local needs, and fully chartered home rule cities may adopt something different entirely. Never assume IRC/IECC applies in an unincorporated area or small jurisdiction \u2014 confirm with the AHJ which code edition (if any) is locally enforced before drafting plans.",
                "applies_to": "Any project outside major North Dakota cities or in unincorporated county areas",
                "source": "https://bcapcodes.org/code-status/state/north-dakota/"
            },
            {
                "title": "State Building Code amendment cycle \u2014 Building Code Advisory Committee",
                "note": "The State Building Code is updated on a defined cycle managed by the Department of Commerce Building Code Advisory Committee. For the 2025 update, proposed amendments had to be submitted by 5:00 p.m. April 28, 2025 to be considered at the May 22, 2025 adoption meeting. Confirm which code edition the AHJ has actually adopted \u2014 local enforcement of the new edition lags the state vote, so submitting under the wrong edition is a common rejection.",
                "applies_to": "Permit applications crossing the State Building Code amendment cycle",
                "source": "https://www.commerce.nd.gov/sites/www/files/documents/Community%20Services/Building%20Codes/2025%20BLDGcode%20amendment%20schedule.pdf"
            },
            {
                "title": "DWR 90-day shot clock under NDAC 89-08-02",
                "note": "Under North Dakota Administrative Code chapter 89-08-02, the Department of Water Resources has up to 90 days to review and approve or deny a construction permit application from the date of receipt of the most recently requested information. The clock resets each time DWR requests additional information, so respond to RFIs in a single complete batch rather than piecemeal. Track your submission date \u2014 exceeding 90 days from a complete submittal is grounds to escalate to the State Engineer.",
                "applies_to": "DWR water-resources construction permits (dams, dikes, water control structures)",
                "source": "https://ndlegis.gov/prod/acdata/pdf/89-08-02.pdf"
            },
            {
                "title": "DWR construction permit for water control structures (parallel filing)",
                "note": "A construction permit is required from the Department of Water Resources whenever a water control structure is constructed or modified that is capable of retaining, diverting, or obstructing water. This is a SEPARATE state-level filing from the local building permit and from any floodplain development permit \u2014 culverts, berms, retention ponds, and drainage modifications on rural parcels frequently trigger it. File with DWR before the local building permit is issued so plan-check comments can incorporate the DWR conditions.",
                "applies_to": "Projects that retain, divert, or obstruct surface water (drainage, ponds, dams, berms)",
                "source": "https://www.swc.nd.gov/reg_approp/construction_permits/"
            },
            {
                "title": "Floodplain Development Permit \u2014 local Floodplain Administrator, not DWR",
                "note": "A floodplain development permit must be obtained from the LOCAL Floodplain Administrator before beginning ANY work \u2014 not just buildings, but also fill, grading, and accessory structures \u2014 in a Special Flood Hazard Area. This is distinct from the DWR construction permit; both may be required on the same project. The Floodplain Administrator is typically housed in the city or county building/planning office, and applications must precede the building permit submittal.",
                "applies_to": "Any development in a FEMA Special Flood Hazard Area",
                "source": "https://www.swc.nd.gov/reg_approp/floodplain_management/"
            },
            {
                "title": "45% of NFIP claims fall OUTSIDE the mapped SFHA",
                "note": "Per the DWR floodplain quick guide, over 45% of all NFIP claims in North Dakota occur in areas OUTSIDE the identified Special Flood Hazard Area. Do not treat 'not in the SFHA' as 'no flood risk' \u2014 recommend elevation certificates, flood vents, and flood insurance even in Zone X for low-lying parcels along the Red River, Souris, Sheyenne, and Missouri corridors. This drives both client liability conversations and finished-floor-elevation choices.",
                "applies_to": "Residential and accessory construction near North Dakota river corridors regardless of SFHA mapping",
                "source": "https://www.swc.nd.gov/pdfs/floodplain_quick_guide.pdf"
            },
            {
                "title": "Extraterritorial zoning jurisdiction under NDCC ch. 40-47",
                "note": "Under NDCC ch. 40-47, a North Dakota city's zoning and permit authority can extend BEYOND its corporate limits into adjacent unincorporated territory (extraterritorial jurisdiction, or ETJ). The split between city ETJ and county jurisdiction can be reallocated by written agreement between the city and county. For rural-edge parcels, do not assume county-only review \u2014 confirm with both the city and county which AHJ holds permitting authority before submitting drawings.",
                "applies_to": "Construction on parcels within roughly 0.5\u20134 miles of an incorporated city boundary",
                "source": "https://ndlegis.gov/cencode/t40c47.pdf"
            },
            {
                "title": "Local AHJ is supreme \u2014 verify city/county amendments before drafting",
                "note": "The local Authority Having Jurisdiction (the city or county building department) is the primary decision-maker for code interpretation, amendments, and permit issuance in North Dakota. Local amendments to wind/snow loads, frost depth, and energy compliance vary materially between jurisdictions like Fargo, Bismarck, Grand Forks, and unincorporated counties. Pull the local amendments package from the AHJ at project start \u2014 relying on unamended IRC/IECC text is a recurring cause of plan-check correction lists.",
                "applies_to": "All permitted construction in North Dakota",
                "source": "https://www.meltplan.com/blogs/navigating-north-dakota-building-permits-local-code-amendments"
            },
            {
                "title": "NDDEQ air-quality construction permit (parallel filing for fuel-burning equipment)",
                "note": "The North Dakota Department of Environmental Quality issues separate Permits to Construct (PTC) for stationary sources of air emissions, including some commercial boilers, generators, and process heaters. Review time depends on completeness and complexity of the application \u2014 there is no fixed shot clock, and the queue is published on the NDDEQ 'Construction Permits In Progress' page. For projects with fuel-burning equipment above NDDEQ thresholds, file the PTC in parallel with (not after) the building permit so the schedules align.",
                "applies_to": "Commercial/industrial projects with stationary air-emission sources (boilers, large generators, process equipment)",
                "source": "https://deq.nd.gov/aq/permitting/ptcinprogress.aspx"
            },
            {
                "title": "Typical ADU permit timeline \u2014 4 to 10 weeks once complete",
                "note": "There is no statewide ADU shot clock in North Dakota (unlike California's 60-day rule). Permit timelines in most North Dakota cities run four to ten weeks once your documents are complete. Set client expectations accordingly and front-load the application with site survey, floor plans, energy compliance, and utility-service letters in the FIRST submittal \u2014 incomplete applications restart the clock and are the dominant cause of timelines stretching past 10 weeks.",
                "applies_to": "ADU and small residential addition projects in North Dakota cities",
                "source": "https://www.steadily.com/blog/adu-laws-and-regulations-in-north-dakota"
            },
            {
                "title": "Building permits required even for moved/relocated structures",
                "note": "Per county zoning ordinances such as Divide County's, a building permit is required if any structure or building is being BUILT OR MOVED ONTO the property \u2014 relocation of an existing shed, cabin, or modular unit is treated the same as new construction. This catches owners who assume that 'pre-built' or 'used' structures avoid permitting. Confirm permit requirements for relocation, foundation, and utility tie-in separately, because each may be a distinct submittal.",
                "applies_to": "Relocation of sheds, modulars, cabins, or accessory structures onto a parcel",
                "source": "https://deq.nd.gov/WQ/2_NDPDES_Permits/1_AFO_CAFO/CountyZoning/Divide/DivideZoning20190402.pdf"
            }
        ]
    },
    "AK": {
        "name": "Alaska expert pack",
        "expert_notes": [
            {
                "title": "No statewide residential building code \u2014 AHJ determines code applicability",
                "note": "Alaska does not adopt the IRC or IECC statewide for 1-2 family dwellings. Code applicability depends entirely on the local borough or municipality, and unorganized boroughs often have no building codes, permits, inspections, or fees at all. Always confirm whether the project parcel is inside an organized borough or city that has adopted a residential code before assuming permits are required.",
                "applies_to": "All residential construction in Alaska \u2014 pre-permit jurisdictional check",
                "source": "https://www.iccsafe.org/advocacy/adoptions-map/alaska/"
            },
            {
                "title": "AHFC 2018 IRC + Alaska-amended IECC for state-funded residential",
                "note": "Alaska Housing Finance Corporation (AHFC) adopts the 2018 IRC as its residential standard and the 2018 IECC with Alaska-Specific Amendments (BEES) for any project receiving AHFC financing or weatherization funds. The Alaska amendments supersede the IECC base requirements for cold-climate envelope, vapor retarder, and ventilation, and replace the BEES amendments to IECC 2012 adopted June 18, 2014. Confirm AHFC funding status early \u2014 the energy package and CCHRC-style detailing must be designed in, not retrofitted.",
                "applies_to": "AHFC-financed or weatherization-funded residential projects",
                "source": "https://www.ahfc.us/application/files/1815/5191/6053/2018_IECC_Alaska_Specific_Amendments.pdf"
            },
            {
                "title": "Alaska State Fire Marshal commercial code \u2014 2021 IBC with amendments",
                "note": "For commercial buildings statewide, the Alaska State Fire Marshal enforces the 2021 IBC with Alaska-focused amendments. This applies even in unorganized boroughs that have no local building department, because plan review and life-safety inspection roll up to the State Fire Marshal's office. Mixed-use and small commercial (including some larger ADU-style accessory structures) can trigger State Fire Marshal review separate from any local building permit.",
                "applies_to": "Commercial and mixed-use construction statewide",
                "source": "https://www.uvm.edu/d10-files/documents/2024-06/State_Building_And_Energy_Codes.pdf"
            },
            {
                "title": "DCCED Construction Contractor registration + Residential Endorsement",
                "note": "All contractors performing construction in Alaska must register with the DCCED Division of Corporations, Business, and Professional Licensing (Construction Contractors Section). The Residential Contractor Endorsement requires completing a 16-hour cold climate course and passing the residential exam before working on 1-4 family dwellings. Registration fee is $350 plus bond and insurance; verify on the Professional License Search before signing \u2014 unregistered work voids contracts and lien rights.",
                "applies_to": "All paid construction work in Alaska",
                "source": "https://www.commerce.alaska.gov/web/cbpl/ProfessionalLicensing/ConstructionContractors"
            },
            {
                "title": "Anchorage Municipal Contractor License \u2014 separate from state endorsement",
                "note": "The Municipality of Anchorage requires its own Municipal Contractors License for any individual or business performing construction within the Anchorage Service Area, in addition to the state DCCED endorsement. This is a parallel filing \u2014 being state-registered does not make you legal in Anchorage, and Anchorage building inspectors will reject permits if the local license is missing or expired. Apply through Anchorage Development Services before pulling permits within the MOA.",
                "applies_to": "Construction within the Municipality of Anchorage",
                "source": "https://www.muni.org/Departments/OCPD/development-services/for-contractors/Pages/Contractor-Licensing.aspx"
            },
            {
                "title": "Plumbing license \u2014 1,000 hours field experience or 125 schooling hours",
                "note": "Alaska plumbing licensure requires either 125 hours of approved schooling or 1,000 hours of fieldwork, plus a $50 application fee and $200 licensing fee. Gas-fitting work requires the gas endorsement on top of the base plumbing license. Plumbers working without proper endorsement on gas piping is a common rejection reason at rough-in inspection.",
                "applies_to": "Plumbing and gas-piping work statewide",
                "source": "https://www.housecallpro.com/licensing/plumbing/alaska/"
            },
            {
                "title": "ADF&G Fish Habitat Permit (Title 16) for in-water and anadromous-stream work",
                "note": "Any activity in or affecting an anadromous fish stream \u2014 including culverts, bank stabilization, dock pilings, dewatering, fill, or stream crossings for driveways serving new ADUs/SFRs \u2014 requires a Fish Habitat Permit from ADF&G under AS 16.05.871. Special Area Permits apply within designated state refuges, sanctuaries, and critical habitat areas. Contact the local Habitat Section office before designing the access route \u2014 retroactive permits are not issued and unpermitted work triggers restoration orders.",
                "applies_to": "Construction near or crossing anadromous waters and special areas",
                "source": "https://www.adfg.alaska.gov/index.cfm?adfg=uselicense.fish_habitat_permits"
            },
            {
                "title": "Wetlands cover 43.3% of Alaska \u2014 assume wetland delineation before clearing",
                "note": "Wetlands make up 43.3% of Alaska's surface area, far above any other state, so a Section 404 Clean Water Act permit from the U.S. Army Corps of Engineers (Alaska District) is frequently triggered for fill, dredge, or grading on otherwise buildable-looking lots. Get a JD (jurisdictional determination) or ADF&G Habitat Biologist consult before site work \u2014 clearing wetlands without a 404 permit is a federal violation that halts the project and triggers restoration. Interior river corridors and coastal lowlands are highest risk.",
                "applies_to": "Site grading, fill, and clearing on undeveloped Alaska parcels",
                "source": "https://www.adfg.alaska.gov/index.cfm?adfg=wetlands.main"
            },
            {
                "title": "DNR Lands Section permit for structures on state tidelands or submerged lands",
                "note": "A DNR Land Use Permit is required to place any structure, facility, or equipment on state-owned uplands, tidelands, or submerged lands \u2014 this catches docks, boat ramps, water-line crossings, and seasonal camps. This is a separate filing from any local building permit and from ADF&G Habitat Permits; both may be required in parallel for a single shoreline project. Apply through DNR Mining, Land & Water before finalizing site plans.",
                "applies_to": "Structures on state-owned uplands, tidelands, or submerged lands",
                "source": "https://dnr.alaska.gov/mlw/lands/permitting/"
            },
            {
                "title": "USACE Alaska District floodplain & FIRM check before foundation design",
                "note": "Flood hazard data for Alaska communities is published through the USACE Alaska District Floodplain Management Services Program, not consistently through FEMA FIRM panels (many rural communities have unmapped or outdated panels). Confirm BFE and floodway status with USACE Alaska District before pouring foundations near rivers, coastlines, or ice-jam-prone reaches; missing this is a common cause of insurance and resale problems even where no local permit was required.",
                "applies_to": "Construction in or near flood-prone areas statewide",
                "source": "https://www.poa.usace.army.mil/About/Offices/Engineering/Floodplain-Management/"
            },
            {
                "title": "DCCED RiskMAP multi-hazard screening for flood, wildfire, earthquake",
                "note": "The Alaska DCCRA RiskMAP web tool provides relative risk scores for flooding, earthquake, wildfire, and community vulnerability at the parcel/community level. Run RiskMAP early on any new construction or major addition \u2014 wildfire and seismic risk often drive design decisions (defensible space, anchorage details, soil class) that are easier to address pre-design than at plan check. This is screening, not a permit, but it informs which overlay permits and engineering reports you'll actually need.",
                "applies_to": "Pre-design hazard screening for new construction and major additions",
                "source": "https://www.commerce.alaska.gov/web/dcra/ResiliencePlanningLandManagement/RiskMAP/AlaskaMappingResources"
            },
            {
                "title": "Fairbanks adopts 2018 IRC + 2018 IECC with local amendments",
                "note": "The City of Fairbanks has adopted the 2018 IRC and 2018 IECC, each with local amendments, as the residential and energy code inside city limits. Cold-climate envelope, frost-protected foundation, and ventilation amendments differ from the IRC base text \u2014 pull the current local amendment list before submitting drawings, and note that the Fairbanks North Star Borough outside city limits has different (and in some areas no) building-code coverage.",
                "applies_to": "Residential construction inside the City of Fairbanks",
                "source": "https://www.fairbanks.gov/building/building-codes"
            },
            {
                "title": "No statewide ADU shot clock \u2014 timing is set by local code",
                "note": "Alaska has no statewide ADU ministerial-approval shot clock equivalent to California's 60 days; review timing is governed entirely by the local borough or municipal code (e.g., Anchorage Title 21, Juneau Title 49). Juneau's 2025 Title 49 Phase 1 amendments are actively reducing conditional-use requirements for ADUs, but until adopted, many ADU paths still require discretionary review. Confirm local timing and whether the project is ministerial vs. conditional-use before quoting a permit timeline to the client.",
                "applies_to": "ADU and accessory-unit projects statewide",
                "source": "https://juneau.org/wp-content/uploads/2025/02/T49-P1W1-Support-Materials-22JAN25.pdf"
            },
            {
                "title": "Anchorage Title 21 zoning \u2014 appeals via Planning & Zoning Commission",
                "note": "Within Anchorage, use regulations are governed by Title 21 (Municipal Code Chapter 21.05). Higher-order uses are allowed within lower zones, and variances or conditional uses denied at staff level can be appealed to the Anchorage Planning & Zoning Commission, then to the Assembly. Build the appeal record at staff-level review \u2014 issues not raised then are typically waived on appeal.",
                "applies_to": "Zoning denials, variances, and conditional-use appeals in Anchorage",
                "source": "https://www.muni.org/Departments/OCPD/Planning/Projects/t21/Documents/6-18-PZC-Chapter5.pdf"
            },
            {
                "title": "Unorganized borough \u2014 no permits, but disclosure & lender risk remain",
                "note": "Roughly half of Alaska's land area is in unorganized boroughs with no building codes, no permits, no inspections, no property taxes, and no state income tax. This does not eliminate federal wetland, ADF&G habitat, DNR lands, or USACE floodplain obligations, and lenders/insurers frequently require a stamped engineer's letter or third-party inspection in lieu of a local CO. Document the absence of local jurisdiction in the contract so the homeowner understands resale and insurance implications.",
                "applies_to": "Construction in unorganized boroughs",
                "source": "https://www.alaskahomebuilder.com/permits-zoning-and-building-regulations-in-remote-rural-alaska-what-you-must-know-before-you-break-ground/"
            },
            {
                "title": "Planning Commission is the variance/conditional-use authority",
                "note": "In organized boroughs and first-class cities, the local Planning Commission has statutory authority to approve or deny variances, conditional-use permits, and land-use permits under Title 29 planning powers. Conditional-use permits are the most common discretionary approval and require a noticed public hearing \u2014 build a 30-60 day cushion into the schedule when one is required. Findings on the record at the Commission are the basis for any subsequent superior court appeal.",
                "applies_to": "Variances, conditional-use permits, and land-use appeals",
                "source": "https://www.commerce.alaska.gov/web/portals/4/pub/Planning%20Commission%20Handbook%20Jan%202012.pdf"
            }
        ]
    },
    "VT": {
        "name": "Vermont expert pack",
        "expert_notes": [
            {
                "title": "No statewide general contractor license \u2014 SOS Residential Contractor registration required",
                "note": "Vermont does not issue an overall general contractor license. Instead, anyone performing residential construction valued at $10,000 or more (labor + materials) for a homeowner must register as a Residential Contractor with the Vermont Secretary of State's Office of Professional Regulation before signing the contract. Verify active registration on the SOS portal before quoting \u2014 an unregistered residential contractor cannot enforce the contract or perfect a mechanic's lien, and operating without registration is grounds for OPR discipline.",
                "applies_to": "Residential construction contracts \u2265 $10,000 statewide",
                "source": "https://sos.vermont.gov/residential-contractors"
            },
            {
                "title": "Electrical and plumbing licensed through DFS \u2014 parallel filing from the town building permit",
                "note": "Electrical and plumbing licensing and inspection in Vermont are run by the Department of Public Safety, Division of Fire Safety (DFS) at 45 State Drive, Waterbury \u2014 not by the Office of Professional Regulation. Initial Plumbing, Electrical, and Elevator/Conveyance licenses are not available through the online Trade Licenses Web Portal and require paper application. State electrical and plumbing inspections are a separate parallel filing from the municipal building/zoning permit; both AHJs must sign off before final occupancy.",
                "applies_to": "Any electrical or plumbing work statewide",
                "source": "https://firesafety.vermont.gov/licensing/licenses-web-portal"
            },
            {
                "title": "2024 RBES in effect; April 2026 revisions take effect July 14, 2026",
                "note": "Vermont's Residential Building Energy Standards (RBES) is updated on a multi-year cycle under 30 V.S.A. \u00a7 51. The 2024 RBES (based on the 2021 IECC) is the current statewide code, and on April 10, 2026 the Public Service Department adopted further revisions that take effect July 14, 2026. Permit applications submitted before July 14, 2026 are typically reviewed under the prior edition \u2014 confirm with the AHJ which RBES edition governs the project and which version of the RBES Certificate must be filed.",
                "applies_to": "Permit applications crossing the 2026-07-14 RBES update boundary",
                "source": "https://publicservice.vermont.gov/efficiency/building-energy-standards/residential-building-energy-standards"
            },
            {
                "title": "RBES applies to certain renovations, not just new construction",
                "note": "RBES governs all residential new construction AND certain renovation projects in homes of three stories or fewer \u2014 additions, alterations to the thermal envelope, and HVAC or water-heater replacements can all trigger compliance. A common rejection is treating a remodel as code-exempt and omitting the RBES Certificate from the closeout package. File the RBES Certificate with the town clerk and post a copy on the electrical panel before requesting final inspection.",
                "applies_to": "Residential additions, alterations, and HVAC replacements",
                "source": "https://www.efficiencyvermont.com/Media/Default/docs/trade-partners/code-support/municipal-guide-for-vermont-energy-codes.pdf"
            },
            {
                "title": "Stretch Code is optional statewide but mandatory in opt-in towns",
                "note": "Act 89 of 2013 created Vermont's Stretch Code as an above-base tier on top of RBES. Adoption is at municipal option \u2014 base RBES applies by default, but towns that have opted in (and projects receiving certain state housing or energy incentives) must meet the higher Stretch tier. Confirm with the town clerk or zoning administrator which tier applies before sizing the envelope; designing to base RBES in a Stretch Code town will fail energy plan review.",
                "applies_to": "Residential projects in municipalities that have adopted the Stretch Code",
                "source": "https://codes.iccsafe.org/s/VTRES2024P1/preface/VTRES2024P1-Background"
            },
            {
                "title": "ADUs are by-right statewide under 24 V.S.A. \u00a7 4412",
                "note": "Updates to 24 V.S.A. \u00a7 4412 require every Vermont municipality to allow one ADU on any lot with a single-family home as a permitted use, without conditional-use review or a public hearing. The ADU still needs a zoning permit and, in most cases, a building permit, but the town cannot deny it on use grounds or impose owner-occupancy or minimum-lot-size hurdles that effectively block it. Push back in writing if a zoning administrator tries to route a code-compliant ADU through conditional-use or DRB review.",
                "applies_to": "Accessory Dwelling Units on single-family lots statewide",
                "source": "https://www.newframeworks.com/blog/building-an-adu-in-vermont-what-you-need-to-know"
            },
            {
                "title": "Statewide 15-day appeal period after zoning permit approval",
                "note": "Once a municipal zoning permit is approved in Vermont, state law requires a 15-day appeal period before the permit becomes effective \u2014 construction cannot lawfully begin during that window. Bake this into the schedule: do not promise a start date that ignores the 15-day clock, and do not order deliveries or stage subs until the appeal period closes. The clock runs from the date of the decision, not the date the applicant picks up the permit.",
                "applies_to": "All municipal zoning permits statewide",
                "source": "https://www.burlingtonvt.gov/690/The-Permit-Process---Super-Simplified"
            },
            {
                "title": "Act 250 trigger thresholds \u2014 separate state filing via ANROnline",
                "note": "Act 250 is Vermont's state-level land-use review and applies only to certain development and subdivision activities \u2014 common residential triggers include subdivisions of 10 or more lots, any construction above 2,500 feet elevation, and housing or commercial development above the size thresholds in the rule. When triggered, the application is a separate electronic filing through the ANROnline portal with all supporting documents, on top of any municipal permit. Screen every project against the Act 250 rules early; an after-the-fact violation can stop work and force redesign.",
                "applies_to": "Subdivisions, large residential projects, and high-elevation construction",
                "source": "https://act250.vermont.gov/act250-permit"
            },
            {
                "title": "Act 250 Criterion 1G \u2014 wetlands and associated buffers",
                "note": "Act 250 Criterion 1G prohibits projects from impacting jurisdictional (Class I, II, or III) wetlands or their associated buffers without a Vermont Wetlands Permit from the Agency of Natural Resources \u2014 typically a 50-foot buffer for Class II wetlands. Even a driveway, retaining wall, or stormwater outfall placed in a wetland buffer can trigger a permit. Pull the ANR Atlas wetlands layer during site planning; redesigning to stay outside the buffer is almost always cheaper than pursuing a wetland authorization.",
                "applies_to": "Projects on or adjacent to mapped Vermont wetlands",
                "source": "https://anr.vermont.gov/planning-and-permitting/planning-tools/act-250/act-250-criterion-1g-wetlands"
            },
            {
                "title": "Flood Hazard Area and River Corridor Protection Procedure",
                "note": "Act 250 Criteria 1 and 1A\u20131F govern water resources and require projects in mapped Flood Hazard Areas and River Corridors to meet the ANR Flood Hazard Area and River Corridor Protection Procedure \u2014 including elevation, setback, and no-rise requirements. Many Vermont towns also enforce overlapping local Flood Hazard Bylaws under the NFIP. Check both the ANR Atlas and the town's flood overlay map; designing slab-on-grade in a SFHA without freeboard above BFE is a frequent rejection reason.",
                "applies_to": "Projects in FEMA SFHAs or mapped Vermont river corridors",
                "source": "https://anr.vermont.gov/planning-and-permitting/planning-tools/act-250/act-250-criteria-1-1a-1c-1d-1e-1f-water-resources"
            },
            {
                "title": "Act 250 modernization \u2014 tier-based maps reshape jurisdiction",
                "note": "Vermont is overhauling Act 250 with a tier-based map system designed to loosen permit requirements for housing in designated growth areas while tightening review in environmentally sensitive areas. Until the new tier maps are formally adopted and effective, the existing Act 250 thresholds continue to apply \u2014 but the map a parcel sits on can flip whether Act 250 review is required. Re-check Act 250 jurisdiction at the start of every multi-unit or subdivision project rather than relying on a prior determination.",
                "applies_to": "Multi-unit, subdivision, and large residential projects",
                "source": "https://www.vermontpublic.org/local-news/2026-02-05/vermont-is-overhauling-act-250-heres-what-the-development-maps-look-like-so-far"
            },
            {
                "title": "Seller's Property Information Report \u2014 disclose unpermitted work",
                "note": "Vermont sellers complete a Seller's Property Information Report (SPIR) at listing, with affirmative disclosure of known property conditions including additions, structural changes, and work performed without permits. Unpermitted work that surfaces during a real estate transaction is a frequent driver of after-the-fact permit applications and can hold up closing. When taking on a remodel for a homeowner planning to sell, pull permits and get a final inspection on the record so the SPIR can be answered cleanly.",
                "applies_to": "Remodels and additions on properties expected to be sold",
                "source": "https://legislature.vermont.gov/Documents/2022/WorkGroups/Senate%20Natural%20Resources/Energy/W~Peter%20Tucker~Seller's%20Property%20Information~2-4-2021.pdf"
            },
            {
                "title": "AHJ split \u2014 municipal zoning + state trades + state environmental, no county building department",
                "note": "Vermont permitting is split across multiple AHJs: zoning and most local building permits are issued at the municipal (town/city) level under 24 V.S.A. \u2014 counties do not run building departments. State-level overlays add Act 250 land-use review (ANR), electrical and plumbing licensing/inspection (DFS), and RBES energy compliance (Public Service Department). Identify all four AHJs at project intake; missing the state electrical inspection or the Act 250 trigger after the town signs off is the single most common Vermont compliance gap.",
                "applies_to": "All Vermont residential and light commercial projects",
                "source": "https://codes.findlaw.com/vt/title-24-municipal-and-county-government/vt-st-tit-24-sect-2793c.html/"
            },
            {
                "title": "Vermont CPG Category I \u2014 streamlined registration for net-metered residential solar (\u226415 kW)",
                "note": "Vermont is the only New England state requiring a PUC Certificate of Public Good (CPG) for every solar installation. Net-metered systems with a plant capacity up to 15 kW qualify for the Category I track \u2014 a streamlined registration via the PUC e-File (epuc.vermont.gov), distinct from the full Section 248 review used for utility-scale projects. Verify the system size against the 15 kW Category I threshold before quoting a permit timeline; Category II (>15 kW\u2013150 kW) and full Section 248 paths take materially longer.",
                "applies_to": "Residential net-metered solar PV \u226415 kW seeking the simplified Category I CPG path",
                "source": "https://puc.vermont.gov/electric/net-metering"
            },
            {
                "title": "Green Mountain Power interconnection application \u2014 required BEFORE the PUC CPG, not in parallel",
                "note": "Net-metered solar projects in GMP territory must file a Net-Metering Application + Interconnection Approval directly with Green Mountain Power, then submit the CPG to the Vermont PUC after GMP grants interconnection eligibility. The two filings run in series, not in parallel \u2014 GMP eligibility is a prerequisite for the CPG. Don't promise a customer a turn-on date from the CPG timeline alone; add the GMP queue. Same pattern applies to other VT utilities (Burlington Electric, Stowe Electric, etc.) with their own forms.",
                "applies_to": "Solar PV in Green Mountain Power service territory (most of central + northern Vermont) and other VT utility territories",
                "source": "https://greenmountainpower.com/help/net-metering-project-requirements-process/"
            }
        ]
    },
    "WY": {
        "name": "Wyoming expert pack",
        "expert_notes": [
            {
                "title": "No statewide building code for 1-2 family dwellings \u2014 codes adopted locally",
                "note": "Wyoming does not have a single, statewide building code binding on residential construction. Cities and counties adopt and enforce their own editions of the I-Codes (IBC, IRC, IECC, IMC, IPC, IFC), and which edition applies depends entirely on the AHJ. Always confirm the adopted code edition with the local building department before producing drawings \u2014 neighboring jurisdictions can be one or two cycles apart.",
                "applies_to": "All residential and commercial permit work statewide",
                "source": "https://www.iccsafe.org/advocacy/adoptions-map/wyoming/"
            },
            {
                "title": "Wyoming Fast-Track Permit Act \u2014 30-day shot clock effective July 1, 2026",
                "note": "2026 HB0002 (the Fast-Track Permit Act) requires municipalities and counties to approve or deny building permit applications for dwellings within a statutory shot-clock window. The act applies to dwelling-permit applications filed on or after July 1, 2026; applications filed before that date are not covered. Track the application date carefully \u2014 pre-July 1, 2026 filings remain governed by the AHJ's normal timeline (Teton County has historically averaged ~112 days for residential new construction).",
                "applies_to": "Dwelling building permit applications filed on or after 2026-07-01",
                "source": "https://www.wyoleg.gov/Legislation/2026/HB0002"
            },
            {
                "title": "Pre-Fast-Track baseline: ~112-day average residential review",
                "note": "Before the Fast-Track Permit Act takes effect, county officials report averaging roughly 112 days to review residential new-construction permits (Teton County data cited by building officials). Use this as the planning baseline for any application filed before July 1, 2026, and budget client expectations accordingly. After July 1, 2026, the statutory shot clock supersedes this baseline for qualifying dwellings.",
                "applies_to": "Residential permits filed before 2026-07-01 in Wyoming counties",
                "source": "https://www.jhnewsandguide.com/news/town_county/wyoming-building-officials-prepare-to-enter-the-fast-track/article_fcd2e2e2-418d-48d4-8bd8-2970b2802a4a.html"
            },
            {
                "title": "State Electrical License + Wiring Permit through Wyoming State Fire Marshal",
                "note": "Electrical work in Wyoming is regulated at the state level by the Wyoming State Fire Marshal's Electrical Safety division, which issues electrician licenses (Master, Journeyman, etc.) and wiring permits. Effective April 1, 2025, per Wyo. Stat. \u00a7 9-4-217(h), a 2.5% processing fee applies to all electrical license and wiring permit transactions. Pull the wiring permit from the State Fire Marshal even when a separate municipal building permit is also required \u2014 they are parallel filings, not substitutes.",
                "applies_to": "All electrical installations statewide",
                "source": "https://wsfm.wyo.gov/electrical-safety/licensing"
            },
            {
                "title": "No statewide GC, plumbing, or HVAC license \u2014 local Contractor Boards govern",
                "note": "Wyoming has no state-level general contractor, plumbing, or HVAC/mechanical license; only electrical is licensed at the state level. Cities like Jackson and Cody require contractors working inside city limits to obtain a local Contractor's License through the municipal Contractor Licensing Board, typically starting with a Certificate of Qualification (COQ) card for the master of record. Verify license requirements with each AHJ before quoting \u2014 an out-of-town contractor cannot rely on a 'state license' that does not exist.",
                "applies_to": "GC, plumbing, and HVAC contractors working in Wyoming municipalities",
                "source": "https://www.jacksonwy.gov/184/Contractor-Licensing"
            },
            {
                "title": "Local-license suspension and revocation grounds (Cody example)",
                "note": "Municipal Contractor Licensing Boards may suspend, revoke, limit, or reclassify a contractor's license \u2014 Cody Municipal Code 9-3-4 is representative of this enforcement authority. Common triggers include working without permits, working outside the licensed classification, and unresolved code violations. A revocation in one Wyoming municipality typically triggers disclosure obligations when applying in others, so resolve open violations before reapplying.",
                "applies_to": "Locally-licensed contractors facing disciplinary action",
                "source": "https://codelibrary.amlegal.com/codes/codywy/latest/cody_wy/0-0-0-7537"
            },
            {
                "title": "Laramie County code-enforcement authority under Wyo. Stat. \u00a7 35-9-121",
                "note": "Under Wyo. Stat. \u00a7 35-9-121, the State of Wyoming has delegated authority to counties such as Laramie County to enforce and interpret local or state construction codes. Laramie County has adopted the 2024 International Building Codes for use within its jurisdiction. When working in unincorporated areas, confirm the county's adopted edition rather than assuming a statewide default \u2014 Laramie County's 2024 IBC adoption may not match the edition used in adjacent counties.",
                "applies_to": "Construction in unincorporated Laramie County and similar delegated-authority counties",
                "source": "https://www.laramiecountywy.gov/files/sharedassets/public/v/1/planning/documents/building/2024-final-ibc-adoption.pdf"
            },
            {
                "title": "Teton County 2024 codes effective Feb 1, 2025; 2025 code adoption pending",
                "note": "Teton County operates under the 2024 I-Codes adopted in 2024 and made effective February 1, 2025, and is evaluating adoption of the 2025 cycle. For projects straddling an adoption transition, confirm whether the application date or the permit-issuance date controls the applicable code edition \u2014 Teton County's resolution language governs the cutover. Mountain-resort jurisdictions (Teton, Jackson) routinely move to the newest cycle ahead of more rural counties.",
                "applies_to": "Permit applications in Teton County crossing a code-edition boundary",
                "source": "https://tetoncountywy.gov/DocumentCenter/View/36954/06179-Adoption-of-2025-Building-Code-Resolution"
            },
            {
                "title": "Jackson 2021 I-Codes baseline (effective January 2022)",
                "note": "The Town of Jackson moved to the 2021 IBC, IEBC, IFC, IMC, and Fuel Gas Code effective January 2022. Drawings and specifications for Jackson projects must reference the 2021 editions unless the Town has since adopted a newer cycle \u2014 verify with the Building Department before submittal. Note that Jackson and surrounding Teton County may be on different cycles at any given time.",
                "applies_to": "Permit drawings and specifications inside Town of Jackson limits",
                "source": "https://www.jacksonwy.gov/CivicAlerts.asp?AID=1030&ARC=2095"
            },
            {
                "title": "Statewide IBC 2024 reference adoption (June 26, 2024)",
                "note": "Wyoming has a statewide reference adoption of the 2024 International Building Code, effective June 26, 2024, adopted without amendments. This is the default the State leans on for state-owned buildings and projects coordinated through the State Construction Department, but it does NOT preempt local 1-2 family residential code choices made by cities and counties. Use the 2024 IBC reference for state-construction work and as a fallback when an AHJ has not formally adopted its own edition.",
                "applies_to": "State-owned buildings and AHJs without their own current adoption",
                "source": "https://up.codes/viewer/wyoming/ibc-2024"
            },
            {
                "title": "ADU 90-day issuance proposal (24LSO-0280) \u2014 not yet enacted",
                "note": "Wyoming legislative draft 24LSO-0280 would require each county to issue a building permit for an accessory residential unit no later than 90 days after a complete application. As of now this is draft language, not law \u2014 do NOT cite it as a binding shot clock. For ADU timing today, fall back on the local AHJ's posted timeline (e.g., Laramie's standard ~3-week building-permit review) and, post-July 1, 2026, the Fast-Track Permit Act if the ADU qualifies as a 'dwelling' under HB0002.",
                "applies_to": "ADU permitting timing expectations and client communications",
                "source": "https://wyoleg.gov/InterimCommittee/2023/S37-2023110924LSO-0280v0.5.pdf"
            },
            {
                "title": "ADU dimensional standards are determined locally \u2014 no state floor or ceiling",
                "note": "Wyoming has no statewide ADU size, setback, parking, or owner-occupancy mandate; every dimensional standard is determined by the local zoning ordinance. Laramie County permits ADUs in all urbanized residential zoning districts with locally-defined size and setback rules; other counties may prohibit them entirely or restrict them to specific overlays. Always pull the AHJ's ADU guide before designing \u2014 assumptions imported from California, Oregon, or Colorado will not survive Wyoming plan review.",
                "applies_to": "All ADU design and permitting work statewide",
                "source": "https://adujournal.com/rules/wyoming/"
            },
            {
                "title": "WYPDES Construction General Permit \u2014 1+ acre disturbance",
                "note": "Wyoming DEQ's WYPDES program issues Large and Small Construction General Permits for stormwater discharges from construction activity. Operators disturbing one or more acres (or less than an acre if part of a larger common plan of development) must obtain coverage under the appropriate CGP, prepare a SWPPP, and file the Notice of Intent before ground disturbance. This is a parallel filing \u2014 the building permit does not cover stormwater, and AHJs increasingly require proof of WYPDES coverage before issuing the building permit.",
                "applies_to": "Site work disturbing \u22651 acre or part of a larger common plan",
                "source": "http://deq.wyoming.gov/water-quality/wypdes/discharge-permitting/storm-water-permitting/large-and-small-construction-general-permit/"
            },
            {
                "title": "Wyoming Game & Fish floodplain / stream-alteration permit",
                "note": "Work in or adjacent to streams, wetlands, or floodplains may require a Wyoming Game & Fish Department permit in addition to any Army Corps Section 404 authorization \u2014 see WGFD's Permit Forms & Applications page for the full list of fish, wildlife, and habitat-alteration permits. Typical triggers include diversions, head gates, fish barriers, bank stabilization, and culvert replacement (e.g., the Park County Grizzly Peak diversion-replacement permit approved by commissioners). Plan for parallel WGFD review on any near-water residential or ranch work; AHJs will not issue the building permit without it where applicable.",
                "applies_to": "Construction in/near streams, wetlands, or floodplains",
                "source": "https://wgfd.wyo.gov/licenses-applications/permits/permit-forms-applications"
            },
            {
                "title": "Wetland identification \u2014 coordinate with WGFD before disturbance",
                "note": "Wyoming Game & Fish manages and helps restore wetland complexes statewide and can identify whether a parcel contains regulated wetlands or natural meadows that trigger habitat review. Disturbing unmapped wetlands without coordination is a common cause of stop-work orders and after-the-fact mitigation costs. For any rural site with seasonal water, sub-irrigated meadow, or meandering channel, request a WGFD habitat consultation before grading.",
                "applies_to": "Rural and ranch construction with potential wetland features",
                "source": "https://wgfd.wyo.gov/habitat/wyoming-wetlands"
            },
            {
                "title": "State Building Commission / SCD oversight for state-funded projects",
                "note": "Wyoming's State Construction Department and State Building Commission oversee state-funded and state-owned construction projects, with standardized forms and approvals routed through SCD (main line 307-777-8670, scd@wyo.gov). Private residential work is NOT under SCD jurisdiction, but any project with state funding, on state land, or for a state agency must coordinate with SCD in addition to local permitting. Confirm jurisdiction early \u2014 misrouting a state-funded project through purely local channels is a frequent rework driver.",
                "applies_to": "State-funded, state-owned, or state-agency construction projects",
                "source": "https://stateconstruction.wyo.gov/construction-management/state-building-commission"
            }
        ]
    },
}


def get_state_expert_notes(state: str, city: str = "", job_description: str = "", primary_scope: str | None = None) -> list[dict]:
    """Return expert notes for a state/city/job combination.

    The result is a new list of dicts so callers can safely mutate it.
    """
    state_upper = (state or "").strip().upper()
    pack = STATE_PACKS.get(state_upper)
    if not pack:
        return []

    notes = deepcopy(pack.get("expert_notes", []))
    city_key = (city or "").strip().lower()
    commercial_ti = is_commercial_ti_scope(job_description, primary_scope)

    utility = CALIFORNIA_MUNICIPAL_UTILITIES.get(city_key) if state_upper == "CA" else None
    if utility and (not commercial_ti or _california_utility_scope_requested(job_description, primary_scope)):
        notes.append(
            {
                "title": "California municipal utility coordination",
                "note": (
                    f"Local utility is {utility} — electrical service and utility coordination go through that utility, "
                    "not the surrounding investor-owned utility."
                ),
                "applies_to": "Electrical service and utility coordination work",
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

    if state_upper == "CA" and commercial_ti and city_key in CALIFORNIA_CITY_FALLBACKS:
        notes.append(_california_city_fallback_note(city))

    return filter_state_expert_notes(
        notes,
        state=state_upper,
        city=city,
        job_description=job_description,
        primary_scope=primary_scope,
    )


def _california_utility_scope_requested(job_description: str = "", primary_scope: str | None = None) -> bool:
    text = f" {primary_scope or ''} {job_description or ''} ".lower()
    for clause in re.split(r"[.;,]|\band\b", text):
        for term in _CALIFORNIA_UTILITY_SCOPE_TERMS:
            pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
            if not re.search(pattern, clause):
                continue
            if re.search(r"\b(no|not|without|exclude|excluding|decline|declines|avoid|avoids)\b", clause):
                continue
            return True
    return False


def _california_city_fallback_note(city: str = "") -> dict:
    city_name = (city or "this California city").strip() or "this California city"
    return {
        "title": "California city-level verification fallback",
        "note": (
            f"California state-level guidance applies; Title 24/local AHJ verification is still required. Verify {city_name} city-specific portal, checklist, "
            "fees, plan-check timeline, inspections, and local AHJ amendments before quoting or submitting. "
            "Do not borrow another California city or county's permit portal, utility, fire-zone, or historic-overlay rules."
        ),
        "applies_to": "California commercial tenant-improvement scopes where city-level evidence is not yet verified",
        "source": "planning guidance; verify local AHJ",
    }
