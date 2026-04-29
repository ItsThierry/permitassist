"""Hidden Trigger Detector V1 — deterministic permit-blocker detection.

Drafted by Forge subagent 2026-04-28 from a 4-city Opus 4.7 grading triangulation.
Surfaces hidden permit blockers (hood->fire suppression, B->A-2 sprinkler trigger,
restroom->ADA path-of-travel 20%, hillside->geotech/haul, oak->urban forestry, etc.)
that contractors don't ask about but get rejected for. Pure regex/token matching,
zero LLM calls, zero added latency. Called from research_permit() after
apply_state_expert_pack() and before sanitize_free_text_urls().
"""

import copy
import re
from typing import Any


# Public trigger registry. Keep this as data, not code, so future packs can append
# jurisdiction/scenario triggers without touching the detector loop.
HIDDEN_TRIGGER_REGISTRY = [
    # ------------------------------------------------------------------
    # Commercial restaurant TI / change-of-occupancy triggers (15)
    # ------------------------------------------------------------------
    {
        "id": "phoenix_restaurant_hood_fire_suppression",
        "severity": "high",
        "title": "Type I hood triggers separate fire-suppression permit",
        "why_it_matters": "Commercial cooking suppression is usually reviewed and tested on a separate fire-prevention track. If it is missed, the restaurant can pass building roughs but still fail final approval / CO.",
        "fired_by": [r"\btype\s*(i|1)\s+hood\b", r"\bansul\b", r"\bwet[-\s]?chemical\b", r"\bhood suppression\b", r"\bfryer\b", r"\bgriddle\b"],
        "likely_required_actions": ["Submit fire-suppression shop drawings", "Show fuel/electric shutoff interlocks and manual pull station", "Schedule final suppression acceptance / discharge witness test"],
        "companion_permits": ["Fire suppression permit / Fire Prevention review"],
        "agencies": ["Phoenix Fire Prevention", "AHJ Building Dept"],
        "citations": ["Phoenix FC §901.2", "Phoenix FC §904.5", "NFPA 17A", "UL 300"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "restaurant_type_i_hood_mechanical_exhaust",
        "severity": "high",
        "title": "Grease-producing cooking requires a Type I hood and coordinated exhaust / makeup air",
        "why_it_matters": "The hood, duct, fan, clearances, makeup air, equipment schedule, and fire suppression must agree. Mismatches are a common plan-check rejection and field-change cost.",
        "fired_by": [r"\btype\s*(i|1)\s+hood\b", r"\bcommercial kitchen\b", r"\bfryer\b", r"\bgriddle\b", r"\brange\b", r"\bgrease duct\b"],
        "likely_required_actions": ["Add hood/equipment schedule", "Coordinate exhaust and makeup-air calculations", "Show grease duct routing, access, clearances, and termination"],
        "companion_permits": ["Mechanical permit / hood submittal", "Fire suppression permit"],
        "agencies": ["AHJ Building Dept", "Fire Prevention"],
        "citations": ["IMC §506", "IMC §507.2", "IMC §508", "NFPA 96"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "maricopa_restaurant_food_establishment_plan_review",
        "severity": "high",
        "title": "Restaurant work may need county food-establishment plan review before CO",
        "why_it_matters": "Health-department approval often runs parallel to building review. Missing it can block opening even when the building permit is ready for final.",
        "fired_by": [r"\brestaurant\b", r"\bfood establishment\b", r"\bcommercial kitchen\b", r"\bprep kitchen\b", r"\bdishwasher\b", r"\bwalk[-\s]?in cooler\b"],
        "likely_required_actions": ["Submit food-establishment plans and equipment schedule", "Show finish schedule, hand sinks, mop sink, warewashing, and refrigeration", "Coordinate health inspection before final CO"],
        "companion_permits": ["Food-establishment plan review / health permit"],
        "agencies": ["Maricopa County Environmental Services", "AHJ Building Dept"],
        "citations": ["Maricopa County Environmental Health Code [verify before merging]", "FDA Food Code as adopted by local health authority [verify before merging]"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "phoenix_restaurant_b_to_a2_sprinkler_change_of_occupancy",
        "severity": "high",
        "title": "B/M-to-A-2 restaurant conversion can trigger sprinkler retrofit review",
        "why_it_matters": "A dine-in restaurant often changes the occupancy classification and fire/life-safety basis. In Phoenix, change-of-occupancy sprinkler triggers can be the budget item that surprises the tenant.",
        "fired_by": [r"\bb\s*(to|-)\s*a[-\s]?2\b", r"\boffice\s+(to|into)\s+restaurant\b", r"\bretail\s+(to|into)\s+restaurant\b", r"\bchange of (use|occupancy)\b.*\brestaurant\b", r"\bformer office\b.*\brestaurant\b"],
        "likely_required_actions": ["Identify existing and proposed occupancy groups", "Run sprinkler applicability and tenant-area/building-area thresholds", "Add fire sprinkler notes or separate sprinkler deferred submittal"],
        "companion_permits": ["Fire sprinkler permit, if retrofit/relocation is required"],
        "agencies": ["Phoenix Fire Prevention", "AHJ Building Dept"],
        "citations": ["Phoenix FC §903.1.6", "IFC §903.2.1.2", "IEBC Chapter 10 [verify adopted edition before merging]"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "restaurant_occupant_load_and_egress_recalc",
        "severity": "high",
        "title": "Restaurant seating changes require occupant-load and egress recalculation",
        "why_it_matters": "Adding dining, bar, queueing, or patio seats can change the required number of exits, door swing, panic hardware, travel distance, restroom counts, and fire-alarm assumptions.",
        "fired_by": [r"\bseating\b", r"\bdining\b", r"\bbar\b", r"\bpatio\b", r"\bbanquet\b", r"\boccupant load\b", r"\ba[-\s]?2\b", r"\boffice\s+(to|into)\s+restaurant\b"],
        "likely_required_actions": ["Add occupant-load table by room/area", "Show common path, exit access travel distance, and exit count", "Coordinate exit signage/emergency lighting"],
        "companion_permits": [],
        "agencies": ["AHJ Building Dept", "Fire Prevention"],
        "citations": ["IBC §1004", "IBC §1006", "IBC §1017", "IBC §1010"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "restaurant_restroom_ada_path_of_travel_20_percent",
        "severity": "high",
        "title": "Restroom alteration can trigger ADA path-of-travel upgrades",
        "why_it_matters": "Altering a primary-function area or restroom can require upgrades to the accessible route, restrooms, drinking fountains, telephones, and parking up to the 20% disproportionality cap.",
        "fired_by": [r"\brestroom\b", r"\btoilet room\b", r"\bada bathroom\b", r"\bbathroom remodel\b", r"\baccessible restroom\b"],
        "likely_required_actions": ["Prepare ADA path-of-travel cost allocation", "Show accessible route to altered area", "Verify restroom clearances, accessories, lavatory, mirror, and door hardware"],
        "companion_permits": [],
        "agencies": ["AHJ Building Dept", "Accessibility reviewer"],
        "citations": ["28 CFR §36.403(f)", "2010 ADA Standards §202.4", "IBC Chapter 11"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "phoenix_restaurant_outdoor_patio_zoning_and_fire",
        "severity": "medium",
        "title": "Outdoor patio seating can trigger zoning, occupant-load, and canopy/fire review",
        "why_it_matters": "Patios often look like a minor furniture change but can affect land-use permissions, exits, plumbing fixture counts, accessible route, fire access, canopy construction, and sprinkler coverage.",
        "fired_by": [r"\boutdoor patio\b", r"\bpatio seating\b", r"\bsidewalk seating\b", r"\bcovered patio\b", r"\bcanopy\b"],
        "likely_required_actions": ["Confirm zoning/use permit or outdoor dining approval", "Include patio seats in occupant-load/restroom counts", "Review canopy/sprinkler coverage and fire access"],
        "companion_permits": ["Zoning/use permit or outdoor dining approval, if required"],
        "agencies": ["City Planning/Zoning", "Fire Prevention", "AHJ Building Dept"],
        "citations": ["Phoenix ZO §1207 [verify applicability before merging]", "IBC §1004", "IFC §903.3.1.1.5 [verify local adoption before merging]"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "restaurant_alcohol_service_liquor_routing",
        "severity": "medium",
        "title": "Alcohol service requires liquor licensing / clerk routing separate from building permit",
        "why_it_matters": "Liquor approval is not a building permit, but it can control opening dates, floor plan/seating approvals, notices, inspections, and operating conditions.",
        "fired_by": [r"\balcohol\b", r"\bliquor\b", r"\bbar service\b", r"\bbeer and wine\b", r"\bseries[-\s]?12\b", r"\bcocktail\b"],
        "likely_required_actions": ["Start liquor-license filing in parallel", "Coordinate city clerk/local governing body routing", "Keep bar/seating floor plan consistent across permit and liquor submittals"],
        "companion_permits": ["Liquor license / local governing body review"],
        "agencies": ["State liquor authority", "City Clerk", "AHJ Building Dept"],
        "citations": ["A.R.S. Title 4 [verify Arizona license class before merging]"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "restaurant_grease_interceptor_fog_review",
        "severity": "high",
        "title": "Grease interceptor / FOG review may be required for commercial food prep",
        "why_it_matters": "FOG requirements are often enforced by sewer/environmental agencies outside the normal building-permit checklist. Missing sizing or location approval can force plumbing redesign.",
        "fired_by": [r"\bgrease interceptor\b", r"\bgrease trap\b", r"\bfog\b", r"\bcommercial kitchen\b", r"\b3[-\s]?compartment sink\b"],
        "likely_required_actions": ["Size interceptor from fixture/equipment schedule", "Confirm location/access with sewer or environmental authority", "Show sampling port/cleanout details if required"],
        "companion_permits": ["FOG / industrial waste / sewer approval, if required"],
        "agencies": ["Sewer authority", "Environmental Services", "AHJ Plumbing Dept"],
        "citations": ["IPC §1003.3.1", "Local sewer-use ordinance [verify before merging]"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "restaurant_backflow_food_equipment",
        "severity": "medium",
        "title": "Food-service equipment can trigger backflow protection requirements",
        "why_it_matters": "Dish machines, espresso machines, ice makers, beverage dispensers, and chemical dispensers often need approved backflow protection; inspectors flag this late if it is absent from plumbing drawings.",
        "fired_by": [r"\bdish(machine|washer)\b", r"\bespresso\b", r"\bice maker\b", r"\bbeverage dispenser\b", r"\bcoffee bar\b", r"\bbackflow\b"],
        "likely_required_actions": ["List each water-connected appliance", "Show ASSE-listed backflow device or air gap", "Coordinate health/plumbing inspection details"],
        "companion_permits": [],
        "agencies": ["AHJ Plumbing Dept", "Health Department"],
        "citations": ["IPC §608", "ASSE 1022", "ASSE 1024 [verify device selection before merging]"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "restaurant_gas_line_cooking_load",
        "severity": "medium",
        "title": "New cooking equipment can require gas-load recalculation and shutoff details",
        "why_it_matters": "Gas cooking changes often outgrow the existing meter, regulator, or branch piping. Plan check and inspection will expect sizing, shutoff valves, connectors, and combustion-air coordination.",
        "fired_by": [r"\bgas line\b", r"\bnatural gas\b", r"\bgas range\b", r"\bgas fryer\b", r"\bgas meter\b", r"\bgriddle\b"],
        "likely_required_actions": ["Provide gas-piping isometric and load table", "Confirm meter/regulator capacity", "Show appliance shutoff valves and flexible connectors"],
        "companion_permits": ["Plumbing/fuel-gas permit, if separated locally"],
        "agencies": ["AHJ Plumbing/Mechanical Dept", "Gas utility"],
        "citations": ["IFGC §402", "IFGC §409", "IFGC §411"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "restaurant_fire_alarm_monitoring_modification",
        "severity": "medium",
        "title": "Restaurant occupancy or suppression work may require fire-alarm interface review",
        "why_it_matters": "Suppression systems, duct detectors, occupant load, and sprinkler changes can all require alarm interlocks, monitoring updates, or a separate fire-alarm submittal.",
        "fired_by": [r"\bfire alarm\b", r"\bmonitoring\b", r"\bduct detector\b", r"\bansul\b", r"\bsprinkler\b", r"\ba[-\s]?2\b"],
        "likely_required_actions": ["Confirm whether alarm shop drawings are deferred or separate", "Show suppression/alarm interface sequence", "Update monitoring account and acceptance testing"],
        "companion_permits": ["Fire alarm permit, if devices/interface change"],
        "agencies": ["Fire Prevention", "Alarm contractor"],
        "citations": ["IFC §907.1.1", "NFPA 72"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "restaurant_indirect_waste_and_floor_sink_layout",
        "severity": "medium",
        "title": "Commercial kitchen equipment needs indirect waste / floor-sink coordination",
        "why_it_matters": "Equipment layouts often pass design review conceptually but fail plumbing/health review when indirect drains, air gaps, floor sinks, and cleanouts are missing or inaccessible.",
        "fired_by": [r"\bfloor sink\b", r"\bindirect waste\b", r"\bprep sink\b", r"\b3[-\s]?compartment sink\b", r"\bdishwasher\b", r"\bice maker\b", r"\bwalk[-\s]?in cooler\b"],
        "likely_required_actions": ["Show indirect waste receptors and air gaps", "Coordinate floor-sink locations with equipment plan", "Confirm cleanouts and trap primers where required"],
        "companion_permits": [],
        "agencies": ["AHJ Plumbing Dept", "Health Department"],
        "citations": ["IPC §802", "IPC §1002", "Local health code [verify before merging]"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "restaurant_plumbing_fixture_count_recalc",
        "severity": "medium",
        "title": "Restaurant seat count can change required plumbing fixtures",
        "why_it_matters": "Dining, bar, patio, and assembly occupant loads can push the project over restroom fixture thresholds, affecting cost, space planning, and accessibility upgrades.",
        "fired_by": [r"\bseats?\b", r"\bdining\b", r"\bbar\b", r"\bpatio\b", r"\boccupant load\b", r"\brestroom\b"],
        "likely_required_actions": ["Prepare occupant-load and fixture-count table", "Verify male/female or all-gender fixture distribution rules", "Coordinate accessible restroom clearances"],
        "companion_permits": [],
        "agencies": ["AHJ Plumbing Dept", "AHJ Building Dept"],
        "citations": ["IPC §403.1", "IBC §2902"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "restaurant_grease_duct_access_and_cleaning",
        "severity": "medium",
        "title": "Grease duct routing needs access panels and cleanability review",
        "why_it_matters": "Long or concealed grease duct runs can be rejected if access, slope, clearances, enclosure, and cleanout locations are not shown before construction.",
        "fired_by": [r"\bgrease duct\b", r"\bhood duct\b", r"\broof fan\b", r"\bexhaust shaft\b", r"\btype\s*(i|1)\s+hood\b"],
        "likely_required_actions": ["Show duct route from hood to discharge", "Add access-panel and cleanout details", "Coordinate shaft/enclosure and clearance requirements"],
        "companion_permits": ["Mechanical hood/exhaust review"],
        "agencies": ["AHJ Mechanical Dept", "Fire Prevention"],
        "citations": ["IMC §506.3", "NFPA 96 Chapter 7 [verify edition before merging]"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "restaurant_food_establishment_health_review_general",
        "severity": "high",
        "title": "Restaurant work needs local health-department food-establishment plan review",
        "why_it_matters": "Health-department review usually runs in parallel with the building permit and approves finish schedule, hand sinks, three-compartment sink, warewashing, refrigeration, and food-flow. Missing it can hold the certificate of occupancy even when building review is finished.",
        "fired_by": [r"\brestaurant\b", r"\bfood establishment\b", r"\bcommercial kitchen\b", r"\bprep kitchen\b", r"\bcafe\b", r"\btavern\b", r"\bbar\b.*\b(food|kitchen|grill)\b", r"\bdishwasher\b", r"\bwalk[-\s]?in cooler\b"],
        "likely_required_actions": ["Submit food-establishment plans and equipment schedule to local health department", "Show finish schedule, hand sinks, mop sink, three-compartment / warewashing, refrigeration", "Coordinate health inspection before final CO"],
        "companion_permits": ["Health-department food-establishment plan review"],
        "agencies": ["Local / county health department", "AHJ Building Dept"],
        "citations": ["FDA Food Code as adopted by local health authority [verify edition before merging]"],
        "primary_scope": "commercial_restaurant",
    },
    {
        "id": "wi_class_b_tavern_liquor_license",
        "severity": "medium",
        "title": "Wisconsin Class B Tavern liquor license requires municipal council approval and runs parallel to building permit",
        "why_it_matters": "Wisconsin alcohol licensing is municipal: a Class B Tavern (intoxicating liquor for on-premises consumption) is approved by the city/village/town council, often subject to license-quota caps and 30-day public notice. Opening dates slip when the building permit is ready but the council hearing has not happened.",
        "fired_by": [r"\balcohol\b", r"\bliquor\b", r"\bbar service\b", r"\bbeer and wine\b", r"\bclass[-\s]?b\b", r"\btavern\b", r"\bcocktail\b"],
        "regions": ["wi"],
        "likely_required_actions": ["File Class B Tavern application with the local municipal clerk early", "Confirm the municipality has an available license under the population quota", "Build council-hearing date and 30-day notice into the schedule"],
        "companion_permits": ["Municipal Class B Tavern license"],
        "agencies": ["Municipal clerk / common council", "Wisconsin Department of Revenue Alcohol Beverage Bureau"],
        "citations": ["Wis. Stat. §125.51", "Wis. Stat. §125.04 [verify before merging]"],
        "primary_scope": "commercial_restaurant",
    },

    # ------------------------------------------------------------------
    # Commercial retail TI triggers (A6)
    # ------------------------------------------------------------------
    {
        "id": "retail_storefront_facade_alteration",
        "severity": "high",
        "title": "Storefront/facade changes usually need separate facade or design review",
        "why_it_matters": "Retail storefront, awning, window, and facade work is often reviewed separately from the interior TI; Specific Plan/form-based-code areas may add design review.",
        "fired_by": [r"\bfacade\b", r"\bfaçade\b", r"\bstorefront\b", r"\bawning\b", r"\bwindow(s)?\b", r"\bglazing\b", r"\bfrontage\b"],
        "likely_required_actions": ["Confirm facade/design-review path before TI submittal", "Show storefront elevations, materials, glazing, and awning attachment", "Coordinate sign locations with facade drawings"],
        "companion_permits": ["Facade Alteration Permit", "Sign Permit"],
        "agencies": ["AHJ Building Dept", "Planning / Design Review", "Sign reviewer"],
        "citations": ["IBC §105.1", "IBC §2406", "Local sign/facade design standards [verify before merging]"],
        "primary_scope": "commercial_retail_ti",
    },
    {
        "id": "retail_signage_permit",
        "severity": "high",
        "title": "Retail tenant changes almost always need a separate sign permit",
        "why_it_matters": "Signage is commonly a separate permit and missing it is one of the fastest ways a retail opening slips, especially when the sign is illuminated or tied to a master sign program.",
        "fired_by": [r"\bretail\b", r"\bstore\b", r"\bshop\b", r"\bboutique\b", r"\bshowroom\b", r"\bmall tenant\b", r"\bstrip mall\b", r"\bsign(age)?\b", r"\btenant improvement\b", r"\bti\b"],
        "likely_required_actions": ["Submit wall/window/monument sign drawings separately if required", "Confirm landlord master sign program", "Add electrical sign permit if illuminated"],
        "companion_permits": ["Sign Permit", "Electrical Sign Permit if illuminated"],
        "agencies": ["AHJ Sign/Zoning reviewer", "Electrical reviewer"],
        "citations": ["Local sign code [verify district and master sign program]", "NEC Article 600"],
        "primary_scope": "commercial_retail_ti",
    },
    {
        "id": "retail_ada_path_of_travel_20pct",
        "severity": "high",
        "title": "Retail TI valuation can trigger ADA path-of-travel upgrades",
        "why_it_matters": "Alterations to a retail primary-function area can require accessible upgrades to the entrance, route, restrooms, parking, counters, and signage up to the 20% disproportionality cap.",
        "fired_by": [r"\bretail\b", r"\btenant improvement\b", r"\bti\b", r"\bbuildout\b", r"\brenovation\b", r"\bremodel\b", r"\bfront counter\b", r"\bcash wrap\b", r"\brestroom\b", r"\$\s?\d"],
        "likely_required_actions": ["Prepare ADA path-of-travel cost allocation", "Show accessible route from site arrival to sales floor/counter/restrooms", "Verify accessible parking and entrance hardware"],
        "companion_permits": [],
        "agencies": ["AHJ Building Dept", "Accessibility reviewer"],
        "citations": ["2010 ADA Standards §202.4", "28 CFR §36.403", "IBC Chapter 11", "CBC 11B-202.4 / TAS where adopted"],
        "primary_scope": "commercial_retail_ti",
    },
    {
        "id": "retail_egress_occupant_load_recalc",
        "severity": "medium",
        "title": "Retail layout or merchandise changes require occupant-load and egress recalculation",
        "why_it_matters": "Mercantile occupant load varies by sales, stock, queueing, and display configuration; changes can affect exit count, door swing, panic hardware, travel distance, and exit signage.",
        "fired_by": [r"\blayout\b", r"\bpartition\b", r"\bsales floor\b", r"\bmerchandise\b", r"\bshowroom\b", r"\bstock(room)?\b", r"\boccupant load\b", r"\begress\b", r"\baisles?\b"],
        "likely_required_actions": ["Add occupant-load table by sales/stock/support area", "Show exit access travel distance and aisle widths", "Coordinate exit signs/emergency lighting"],
        "companion_permits": [],
        "agencies": ["AHJ Building Dept", "Fire Prevention"],
        "citations": ["IBC §1004", "IBC §1005", "IBC §1006", "IBC §1017"],
        "primary_scope": "commercial_retail_ti",
    },
    {
        "id": "retail_fire_alarm_sprinkler_modifications",
        "severity": "medium",
        "title": "Ceiling/demising changes can trigger fire alarm and sprinkler modification permits",
        "why_it_matters": "Retail ceiling clouds, demising walls, racking, and lighting grids often require sprinkler head relocation, alarm/strobe coverage changes, or separate fire shop drawings.",
        "fired_by": [r"\bceiling\b", r"\bdemising\b", r"\bsprinkler\b", r"\bfire alarm\b", r"\bstrobe\b", r"\bracking\b", r"\bshelving\b", r"\brelocat(e|ing) sprinkler\b"],
        "likely_required_actions": ["Confirm deferred vs separate fire submittals", "Show sprinkler/alarm devices affected by ceiling or wall changes", "Schedule fire final/acceptance testing"],
        "companion_permits": ["Fire Alarm Permit", "Fire Sprinkler Modification Permit"],
        "agencies": ["Fire Prevention", "AHJ Building Dept"],
        "citations": ["IFC §901.2", "IFC §907.1.1", "NFPA 13", "NFPA 72"],
        "primary_scope": "commercial_retail_ti",
    },
    {
        "id": "retail_energy_code_lighting_hvac",
        "severity": "medium",
        "title": "Retail lighting/HVAC work triggers commercial energy-code compliance",
        "why_it_matters": "New lighting, controls, HVAC, envelope, or storefront glazing can require COMcheck/IECC forms, WSEC-C, or CA Title 24 Part 6 documentation.",
        "fired_by": [r"\blighting\b", r"\blight fixtures?\b", r"\bhvac\b", r"\bduct\b", r"\bthermostat\b", r"\bcontrols?\b", r"\bstorefront\b", r"\bglazing\b", r"\bti\b", r"\btenant improvement\b"],
        "likely_required_actions": ["Prepare COMcheck/IECC or state energy forms", "Show lighting power density and control zones", "Coordinate HVAC economizer/ventilation and storefront glazing performance"],
        "companion_permits": ["Electrical Permit", "Mechanical Permit"],
        "agencies": ["Energy reviewer", "AHJ Electrical/Mechanical Dept"],
        "citations": ["IECC §C405", "IECC §C403", "WSEC-C / CA Title 24 Part 6 / local amendments where adopted"],
        "primary_scope": "commercial_retail_ti",
    },
    {
        "id": "retail_row_encroachment_outdoor_display",
        "severity": "medium",
        "title": "Sidewalk display, sandwich boards, or outdoor retail use can need ROW/encroachment approval",
        "why_it_matters": "Outdoor merchandise, sandwich-board signs, and sidewalk activation can be controlled by transportation/public-works rules outside the building permit.",
        "fired_by": [r"\bsidewalk display\b", r"\boutdoor display\b", r"\bsandwich[-\s]?board\b", r"\ba[-\s]?frame sign\b", r"\boutdoor seating\b", r"\bencroachment\b", r"\bright[-\s]?of[-\s]?way\b", r"\bROW\b"],
        "likely_required_actions": ["Check public-right-of-way permit process", "Maintain accessible sidewalk clear width", "Coordinate insurance/indemnity if required"],
        "companion_permits": ["Public Right-of-Way Permit", "Encroachment Permit"],
        "agencies": ["Public Works / Transportation", "AHJ Planning/Zoning"],
        "citations": ["Local encroachment ordinance [verify before merging]", "2010 ADA Standards §403"],
        "primary_scope": "commercial_retail_ti",
    },
    {
        "id": "retail_health_food_handling",
        "severity": "high",
        "title": "Food retail can need health-department food establishment approval",
        "why_it_matters": "Grocery, convenience, cafe, bakery, beverage, and prepared-food retail often need health plan review before building final/CO.",
        "fired_by": [r"\bgrocery\b", r"\bconvenience store\b", r"\bcafe\b", r"\bcoffee\b", r"\bbakery\b", r"\bfood handling\b", r"\bprepared food\b", r"\bwalk[-\s]?in cooler\b", r"\bthree[-\s]?compartment\b", r"\b3[-\s]?compartment\b"],
        "likely_required_actions": ["Submit health plan review early", "Show hand sinks, mop sink, food-contact finishes, refrigeration, and warewashing", "Coordinate health inspection before final CO"],
        "companion_permits": ["Health Department Food Establishment Permit"],
        "agencies": ["Local / county health department", "AHJ Building Dept"],
        "citations": ["FDA Food Code as adopted locally [verify edition]", "Local health department plan-review rules"],
        "primary_scope": "commercial_retail_ti",
    },
    {
        "id": "retail_change_of_use_or_occupancy",
        "severity": "high",
        "title": "Non-retail-to-retail or special retail changes can require change-of-use / new CO approval",
        "why_it_matters": "Warehouse/office/industrial-to-retail conversions and high-risk retail types can change occupancy, parking, accessibility, fire protection, and certificate-of-occupancy requirements.",
        "fired_by": [r"\bwarehouse\s+(to|into)\s+retail\b", r"\boffice\s+(to|into)\s+retail\b", r"\bindustrial\s+(to|into)\s+retail\b", r"\bchange of (use|occupancy)\b", r"\bnew certificate of occupancy\b", r"\bnew co\b", r"\bformer warehouse\b", r"\bformer office\b"],
        "likely_required_actions": ["Identify existing/proposed occupancy groups", "Confirm zoning/parking and CO process", "Coordinate fire/life-safety and accessibility impacts"],
        "companion_permits": ["Change of Use Permit", "Certificate of Occupancy"],
        "agencies": ["AHJ Building Dept", "Planning/Zoning", "Fire Prevention"],
        "citations": ["IBC Chapter 3", "IEBC change-of-occupancy provisions [verify adopted edition]", "Local CO ordinance"],
        "primary_scope": "commercial_retail_ti",
    },
    {
        "id": "retail_cannabis_alcohol_special_use",
        "severity": "high",
        "title": "Cannabis or alcohol retail needs special-use / state licensing parallel path",
        "why_it_matters": "Cannabis and alcohol approvals are separate from the building TI and can control zoning clearance, security plans, hearings, inspections, and opening date.",
        "fired_by": [r"\bcannabis\b", r"\bdispensary\b", r"\bmarijuana\b", r"\balcohol\b", r"\bliquor\b", r"\bbeer and wine\b", r"\bwine shop\b", r"\bottle shop\b"],
        "likely_required_actions": ["Start special-use/zoning clearance and state license in parallel", "Keep floor/security plan consistent across licensing and permit sets", "Confirm separation-distance and operating-condition rules"],
        "companion_permits": ["Special Use Permit", "State cannabis/alcohol license"],
        "agencies": ["Planning/Zoning", "State licensing agency", "AHJ Building Dept"],
        "citations": ["Local special-use ordinance [verify]", "State cannabis/alcohol licensing rules [verify]"],
        "primary_scope": "commercial_retail_ti",
    },

    # ------------------------------------------------------------------
    # LA Hillside ADU + residential ADU triggers (9)
    # ------------------------------------------------------------------
    {
        "id": "la_hillside_adu_slope_grading_soils_geology",
        "severity": "high",
        "title": "Hillside ADU on slope can trigger grading, soils, and geology review",
        "why_it_matters": "Hillside review can add pre-inspection, grading quantities, haul-route constraints, retaining-wall design, and soils/geology reports that are not obvious from a generic ADU permit answer.",
        "fired_by": [r"\bhillside\b", r"\bslope\b", r"\b>\s*15\s*%\b", r"\b15\s*percent slope\b", r"\bgrading\b", r"\bretaining wall\b", r"\bcut and fill\b"],
        "likely_required_actions": ["Confirm Hillside Ordinance / grading applicability", "Add topographic survey and grading quantities", "Budget soils/geology report or pre-inspection if triggered"],
        "companion_permits": ["Grading permit / hillside review, if required"],
        "agencies": ["LADBS", "Los Angeles grading/geology reviewer"],
        "citations": ["LAMC §91.7003 [verify before merging]", "IBC Chapter 18", "CRC/IRC Chapter 4 [verify adopted edition before merging]"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "la_hillside_adu_protected_tree_arborist",
        "severity": "high",
        "title": "Protected tree near work area can trigger arborist report and Urban Forestry review",
        "why_it_matters": "Oak, sycamore, walnut, bay, and other protected tree work can stop grading/foundation work unless the tree protection zone, fencing, and pruning/removal approvals are resolved early.",
        "fired_by": [r"\boak\b", r"\bsycamore\b", r"\bprotected tree\b", r"\bwithin\s+15\s*ft\b", r"\barborist\b", r"\btree protection\b"],
        "likely_required_actions": ["Order arborist report", "Show tree-protection-zone fencing on site plan", "Route to Urban Forestry if pruning/removal/encroachment is proposed"],
        "companion_permits": ["Protected tree permit/review, if required"],
        "agencies": ["Los Angeles Urban Forestry", "LADBS"],
        "citations": ["LAMC §§46.00-46.06 [verify before merging]"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "la_adu_detached_new_utility_service_ladwp_boe",
        "severity": "medium",
        "title": "Detached ADU with new utility service may need LADWP and BOE sewer routing",
        "why_it_matters": "Separate electric/water service, sewer laterals, and sewer facility charges can run outside the building-permit timeline and create opening/utility delays.",
        "fired_by": [r"\bdetached adu\b", r"\bnew utility service\b", r"\bseparate meter\b", r"\bladwp\b", r"\bnew sewer\b", r"\bs-permit\b", r"\bsewer connection\b"],
        "likely_required_actions": ["Ask LADWP about separate service planning", "Check BOE sewer S-permit / sewer capacity process", "Carry sewerage facility charge exposure in budget"],
        "companion_permits": ["Utility service application", "BOE sewer S-permit, if required"],
        "agencies": ["LADWP", "Los Angeles Bureau of Engineering", "LADBS"],
        "citations": ["Los Angeles BOE S-Permit requirements [verify before merging]", "LADWP service planning requirements [verify before merging]"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "la_adu_size_attached_detached_ministerial_path_check",
        "severity": "medium",
        "title": "Large ADU/addition needs ministerial-path size and 50% existing-dwelling rule check",
        "why_it_matters": "An ADU described only by square footage may fall into a different review path depending on attached/detached status, addition size, and local ministerial limits.",
        "fired_by": [r"\badu\b.*\b(8[5-9][0-9]|9[0-9]{2}|1[0-9]{3})\s*(sf|sq\.?\s*ft)\b", r"\baddition\b.*\b(28[1-9]|29[0-9]|[3-9][0-9]{2})\s*(sf|sq\.?\s*ft)\b", r"\b50%\b.*\bexisting dwelling\b"],
        "likely_required_actions": ["Confirm attached vs detached ADU classification", "Check max unit size and addition percentage", "Flag planning/zoning review if outside ministerial path"],
        "companion_permits": [],
        "agencies": ["LADBS", "Los Angeles Planning"],
        "citations": ["LAMC §12.22 A.33(e)(3) [verify before merging]", "Cal. Gov. Code §§66310-66342 [verify current ADU recodification before merging]"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "adu_bathroom_legalization_as_built_permit_history",
        "severity": "high",
        "title": "Bathroom legalization requires permit-history search and as-built strategy",
        "why_it_matters": "Unpermitted bathrooms or converted spaces are rarely a simple bathroom permit. The reviewer may require as-built plans, opening walls, correction of plumbing/venting, and legalization/penalty fees.",
        "fired_by": [r"\blegalize\b", r"\bunpermitted\b", r"\bas[-\s]?built\b", r"\bbathroom legalization\b", r"\bgarage conversion\b", r"\bexisting bathroom\b"],
        "likely_required_actions": ["Pull permit history before filing", "Prepare as-built plans and code-compliance narrative", "Warn owner about investigation/penalty fees and destructive verification"],
        "companion_permits": ["Legalization / code-enforcement clearance, if applicable"],
        "agencies": ["AHJ Building Dept", "Code Enforcement"],
        "citations": ["Local legalization / investigation fee schedule [verify before merging]", "IRC/CRC Chapters 25-31 plumbing provisions [verify scope before merging]"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "adu_fire_separation_openings_between_units",
        "severity": "high",
        "title": "ADU conversion can trigger dwelling-unit fire separation and opening-protection review",
        "why_it_matters": "Garage conversions, attached ADUs, and internal conversions often need rated separations, protected penetrations, door ratings, and exterior-wall opening checks that are not captured by a generic ADU checklist.",
        "fired_by": [r"\battached adu\b", r"\bgarage conversion\b", r"\bconvert garage\b", r"\binternal adu\b", r"\bshared wall\b", r"\bfire separation\b"],
        "likely_required_actions": ["Show rated wall/ceiling assemblies", "Protect penetrations and ducts", "Check exterior wall fire separation distance and openings"],
        "companion_permits": [],
        "agencies": ["AHJ Building Dept", "Fire reviewer if locally required"],
        "citations": ["IRC §R302.3", "IRC §R302.1", "CRC local ADU amendments [verify before merging]"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "adu_smoke_co_alarms_addressing",
        "severity": "medium",
        "title": "ADU work can force smoke/CO alarm upgrades and address/unit-number coordination",
        "why_it_matters": "Final inspection and utility setup often depend on whole-dwelling alarms and clear unit identification, even when the ADU scope looks limited.",
        "fired_by": [r"\badu\b", r"\bgarage conversion\b", r"\bjunior adu\b", r"\bjadu\b", r"\bnew bedroom\b"],
        "likely_required_actions": ["Show smoke and CO alarm locations", "Confirm hardwired/interconnected requirements for new work", "Request unit address/subaddress if local process requires it"],
        "companion_permits": ["Address assignment / unit numbering request, if required"],
        "agencies": ["AHJ Building Dept", "Addressing/Planning office"],
        "citations": ["IRC §R314", "IRC §R315", "Local addressing standard [verify before merging]"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "adu_existing_garage_structural_and_energy_upgrade",
        "severity": "medium",
        "title": "Garage conversion ADU can trigger slab, insulation, egress, and energy corrections",
        "why_it_matters": "Existing garages often lack required slab moisture protection, insulation, habitable ceiling height, emergency escape/rescue openings, and conditioned-space energy compliance.",
        "fired_by": [r"\bgarage conversion\b", r"\bconvert garage\b", r"\bcarport conversion\b", r"\bhabitable garage\b"],
        "likely_required_actions": ["Verify slab/foundation suitability", "Add insulation/energy compliance forms", "Show emergency escape/rescue opening if sleeping room is created"],
        "companion_permits": [],
        "agencies": ["AHJ Building Dept", "Energy reviewer"],
        "citations": ["IRC §R305", "IRC §R310", "IECC residential provisions [verify adopted edition before merging]"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "adu_new_bedroom_septic_or_sewer_capacity",
        "severity": "medium",
        "title": "ADU/new bedroom can trigger sewer or septic capacity review",
        "why_it_matters": "Where sewer capacity fees or septic sizing are based on bedrooms/fixtures, adding an ADU or bedroom can require a separate utility/environmental signoff before final.",
        "fired_by": [r"\bnew bedroom\b", r"\badd bedroom\b", r"\bseptic\b", r"\bsewer capacity\b", r"\bsewer connection\b", r"\badu\b.*\bbedroom\b"],
        "likely_required_actions": ["Confirm sewer/septic capacity criteria", "Get sanitation/environmental signoff if required", "Carry connection/capacity fees in budget"],
        "companion_permits": ["Sewer/septic approval, if required"],
        "agencies": ["Sewer authority", "Environmental health", "AHJ Building Dept"],
        "citations": ["IPC Chapter 7", "Local sewer/septic ordinance [verify before merging]"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "adu_sewer_capacity",
        "severity": "medium",
        "confidence": "high",
        "title": "Sewer Capacity / Utility Upgrade Likely",
        "why_it_matters": "Most ADUs require a sewer capacity letter or service upsize. Failure to confirm BEFORE submittal causes plan-check holds.",
        "fired_by": [r"\b(adu|dadu|jadu|accessory dwelling|junior adu)\b", r"\b(detached|attached)\s+adu\b", r"\bgarage conversion\b.*\b(jadu|adu)\b"],
        "likely_required_actions": ["Confirm existing sewer lateral size and available capacity", "Ask sanitation / utility provider about ADU capacity-letter process", "Carry possible service upsize or sewer connection cost in budget"],
        "companion_permits": ["Sewer Connection Permit", "Sanitation Capacity Letter"],
        "agencies": ["AHJ sanitation department", "Water/sewer utility", "AHJ Building Dept"],
        "citations": ["AHJ sanitation department capacity-letter requirement"],
        "ask_user_if_missing": ["What is the existing sewer line size?", "Has the utility provider confirmed capacity for an additional ADU?"],
        "citations_needed": ["AHJ sanitation department capacity-letter requirement"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "adu_school_impact_fees",
        "severity": "medium",
        "confidence": "medium",
        "title": "School Fees Often Applicable",
        "why_it_matters": "ADUs over 750 sf trigger school fees in CA. Other states vary widely. Budget +$2-15/sf depending on district.",
        "fired_by": [r"\b(adu|dadu|accessory dwelling)\b.*\b(7[6-9][0-9]|[89][0-9]{2}|1[0-9]{3,})\s*(sf|sq\.?\s*ft|square feet)\b"],
        "regions": ["ca"],
        "likely_required_actions": ["Confirm conditioned ADU floor area", "Ask school district or AHJ fee counter about per-square-foot school fees", "Budget school fees before submittal if over 750 sf"],
        "companion_permits": [],
        "agencies": ["Local school district", "AHJ Building Dept"],
        "citations": ["Cal. Gov. Code §65852.2(f)(3) [verify current recodification before merging]"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "adu_traffic_park_water_impact_fees",
        "severity": "medium",
        "confidence": "medium",
        "title": "Traffic / Park / Water-Sewer Connection Impact Fees",
        "why_it_matters": "Many cities charge traffic, park, and water/sewer connection fees on top of building permit. Often $3K–$15K total.",
        "fired_by": [r"\b(detached adu|dadu|new adu|new accessory dwelling)\b", r"\b(adu|dadu)\b.*\b(new utility|new sewer|new water|new connection|separate meter)\b"],
        "not_regions": ["tx"],
        "likely_required_actions": ["Ask fee counter for traffic, park, water, and sewer impact-fee estimate", "Confirm whether fees are reduced/waived for ADUs", "Include utility connection charges in the owner budget"],
        "companion_permits": ["Utility connection permit", "Impact fee assessment"],
        "agencies": ["AHJ Building Dept", "Water/sewer utility", "Planning / impact fee counter"],
        "citations": ["Local ADU impact-fee schedule [verify before merging]"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "adu_parking_replacement",
        "severity": "medium",
        "confidence": "high",
        "title": "Parking Replacement May Be Required",
        "why_it_matters": "Removing a covered parking space for ADU may require replacement (uncovered OK in CA). Verify city-specific waiver eligibility before submittal.",
        "fired_by": [r"\bgarage conversion\b", r"\bconvert garage\b", r"\bcarport conversion\b", r"\b(remove|removing|removed|demolish|demo)\b.*\b(parking|garage|carport|covered space)\b"],
        "likely_required_actions": ["Document existing parking spaces removed by the ADU", "Check state and city ADU parking waiver eligibility", "Show replacement parking location if local rules require it"],
        "companion_permits": [],
        "agencies": ["AHJ Planning/Zoning", "AHJ Building Dept"],
        "citations": ["Cal. Gov. Code §65852.2 parking provisions [verify current recodification before merging]", "Local ADU parking ordinance [verify before merging]"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "adu_tree_protection",
        "severity": "medium",
        "confidence": "high",
        "title": "Protected Tree / Tree-Removal Permit",
        "why_it_matters": "Many CA cities require a separate tree permit and arborist report. Removing protected oaks/sycamores without permit is a common stop-work violation.",
        "fired_by": [r"\b(adu|dadu|jadu|accessory dwelling)\b", r"\b(oak|sycamore|protected tree|heritage tree|significant tree|tree removal|arborist)\b"],
        "regions": ["adu_protected_tree_city", "explicit_protected_tree_context"],
        "likely_required_actions": ["Identify protected trees and tree-protection zones on the site plan", "Order arborist report if work is near protected trees", "File tree-removal/pruning permit before grading or foundation work"],
        "companion_permits": ["Tree removal / protected tree permit"],
        "agencies": ["Urban Forestry / Planning", "AHJ Building Dept"],
        "citations": ["Local protected-tree ordinance [verify before merging]"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "adu_geotech_haul_route",
        "severity": "high",
        "confidence": "high",
        "title": "Geotech Soils Report + Haul Route Plan Required",
        "why_it_matters": "Hillside ADUs require Caltrans/city-approved haul route + soils report. Without these, plan check stalls 4–8 weeks.",
        "fired_by": [r"\bhillside\b", r"\bsteep slope\b", r"\bgrading\b.*\b([5-9][0-9]|[1-9][0-9]{2,})\s*(cy|cubic yards?)\b", r"\bhaul route\b", r"\bsoils report\b", r"\bgeotech"],
        "likely_required_actions": ["Order geotechnical / soils report", "Quantify cut/fill and export on the grading plan", "Prepare haul route permit or plan if export exceeds local threshold"],
        "companion_permits": ["Haul Route Permit", "Grading Permit"],
        "agencies": ["AHJ Building/Grading Dept", "Public Works / Transportation"],
        "citations": ["Local hillside/grading ordinance [verify before merging]", "IBC Chapter 18"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "adu_legalization_co_codeenforcement",
        "severity": "high",
        "confidence": "high",
        "title": "Legalization Triggers Code Enforcement Review",
        "why_it_matters": "Legalization permits are routed through code enforcement. Unpermitted work must be brought to current code, and a CO will be re-issued. Expect inspection of every system.",
        "fired_by": [r"\blegalization\b", r"\blegalize\b", r"\bunpermitted\b", r"\bexisting converted\b", r"\bafter[-\s]?the[-\s]?fact\b", r"\bas[-\s]?built\b"],
        "likely_required_actions": ["Pull permit history before filing", "Prepare as-built plans and current-code correction narrative", "Coordinate code-enforcement clearance and final CO/occupancy documentation"],
        "companion_permits": ["Code enforcement clearance", "Certificate of Occupancy re-issue"],
        "agencies": ["Code Enforcement", "AHJ Building Dept"],
        "citations": ["Local legalization / investigation fee rules [verify before merging]"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "adu_vhfhsz_wui_compliance",
        "severity": "high",
        "confidence": "high",
        "title": "VHFHSZ / WUI Class A Roofing + Eaves + Vents",
        "why_it_matters": "ADUs in WUI/VHFHSZ require Class A roofing, ember-resistant vents, eaves protection per CRC R337 / CBC Chapter 7A. Critical in LA Hillside, San Diego, Riverside, much of Bay Area.",
        "fired_by": [r"\b(wui|wildland|very high fire|vhfhsz|fire hazard severity)\b", r"\bhillside\b"],
        "regions": ["ca"],
        "likely_required_actions": ["Confirm WUI/VHFHSZ parcel status", "Specify Class A roof assembly and ember-resistant vents", "Show exterior wall, eave, deck, glazing, and vegetation/fire-zone notes"],
        "companion_permits": [],
        "agencies": ["AHJ Building Dept", "Fire Dept"],
        "citations": ["CRC §R337", "CBC Chapter 7A", "Local VHFHSZ map [verify before merging]"],
        "primary_scope": "residential_adu",
    },
    {
        "id": "adu_hud_far_setback",
        "severity": "medium",
        "confidence": "medium",
        "title": "Detached ADU Setback / Lot Coverage / FAR Limits",
        "why_it_matters": "CA state-mandated minimums (4-ft side/rear, 0 ft for conversion) but city-specific overlays may add. FAR cap and lot coverage often constrain detached ADUs.",
        "fired_by": [r"\b(detached adu|dadu|new accessory dwelling)\b", r"\b(adu|accessory dwelling)\b.*\b(property line|setback|lot coverage|far|footprint)\b"],
        "likely_required_actions": ["Confirm side/rear setback from proposed ADU footprint", "Check lot coverage/FAR and overlay constraints", "Show conversion vs new detached footprint clearly on site plan"],
        "companion_permits": [],
        "agencies": ["AHJ Planning/Zoning", "AHJ Building Dept"],
        "citations": ["Cal. Gov. Code ADU setback provisions [verify current recodification before merging]", "Local zoning ordinance [verify before merging]"],
        "primary_scope": "residential_adu",
    },


    # ------------------------------------------------------------------
    # Generic commercial TI triggers (8)
    # ------------------------------------------------------------------
    {
        "id": "commercial_ti_demising_wall_fire_acoustic_separation",
        "severity": "high",
        "title": "Demising wall changes may require rated tenant separation and acoustic details",
        "why_it_matters": "Moving a tenant separation can affect fire-resistance continuity, penetrations, structure, ceiling plenum, egress, and landlord acoustic criteria. It is commonly under-scoped as 'just a partition.'",
        "fired_by": [r"\bdemising wall\b", r"\btenant separation\b", r"\bsuite split\b", r"\bcombine suites\b", r"\bnew demising\b"],
        "likely_required_actions": ["Identify required wall type/rating", "Show continuity to deck and protected penetrations", "Confirm landlord STC/acoustic requirement"],
        "companion_permits": [],
        "agencies": ["AHJ Building Dept", "Landlord/owner reviewer"],
        "citations": ["IBC §708", "IBC §706", "STC ≥40 landlord/acoustic criterion [verify before merging]"],
        "primary_scope": "commercial_ti",
    },
    {
        "id": "commercial_ti_sprinkler_relocation_hydraulic_recalc",
        "severity": "high",
        "title": "Sprinkler head relocation can require hydraulic recalculation and separate fire permit",
        "why_it_matters": "Ceiling, wall, storage, or occupancy changes can invalidate sprinkler spacing/coverage. Fire reviewers may require hydraulic calculations and current inspection/certification records.",
        "fired_by": [r"\bsprinkler relocation\b", r"\bsprinkler head relocation\b", r"\brelocate sprinkler\b", r"\bmove sprinkler heads\b", r"\bnew ceiling\b", r"\bopen ceiling\b", r"\bstorage racks\b"],
        "likely_required_actions": ["Have sprinkler contractor review layout", "Submit shop drawings/hydraulic calc if required", "Confirm 5-year inspection/certification status"],
        "companion_permits": ["Fire sprinkler permit"],
        "agencies": ["Fire Prevention", "Licensed sprinkler contractor"],
        "citations": ["NFPA 13", "NFPA 25", "IFC §901.6"],
        "primary_scope": "commercial_ti",
    },
    {
        "id": "commercial_ti_pre_1980_asbestos_lead_survey",
        "severity": "high",
        "title": "Pre-1980 disturbance can require asbestos / lead survey before demolition",
        "why_it_matters": "Hazardous-material survey and abatement rules can delay demolition and expose the contractor/owner to stop-work orders and worker-safety liability.",
        "fired_by": [r"\bpre[-\s]?1980\b", r"\bpre[-\s]?1978\b", r"\bold building\b", r"\bdemolition\b", r"\bdemo\b", r"\basbestos\b", r"\blead paint\b"],
        "likely_required_actions": ["Order asbestos/lead survey before disturbance", "Include abatement scope and notifications if positive", "Do not start demo until clearance path is set"],
        "companion_permits": ["Demolition/abatement notification, if required"],
        "agencies": ["Environmental regulator", "OSHA/state OSHA", "AHJ Building Dept"],
        "citations": ["29 CFR §1926.1101", "29 CFR §1926.62", "40 CFR Part 61 Subpart M"],
        "primary_scope": "commercial_ti",
    },
    {
        "id": "commercial_ti_lighting_iecc_controls",
        "severity": "medium",
        "title": "Lighting changes trigger IECC lighting-power and automatic-control checks",
        "why_it_matters": "Replacing or reconfiguring lighting can require COMcheck/energy forms, lighting power density compliance, occupancy sensors, daylight controls, and automatic shutoff details.",
        "fired_by": [r"\blighting\b", r"\blight fixtures\b", r"\bled retrofit\b", r"\btrack lighting\b", r"\boccupancy sensors?\b", r"\bdaylight controls?\b"],
        "likely_required_actions": ["Prepare lighting compliance / COMcheck if required", "Show control zones and automatic shutoff", "Coordinate emergency egress lighting"],
        "companion_permits": ["Electrical permit, if separated locally"],
        "agencies": ["AHJ Electrical/Energy reviewer"],
        "citations": ["IECC §C405", "ASHRAE 90.1 [where adopted]"],
        "primary_scope": "commercial_ti",
    },
    {
        "id": "commercial_ti_fire_smoke_damper_penetrations",
        "severity": "medium",
        "title": "Duct/shaft penetrations can trigger fire/smoke damper and rated-assembly detailing",
        "why_it_matters": "Mechanical reroutes through corridors, shafts, tenant separations, or rated walls need damper, sleeve, access, and firestopping details before inspection.",
        "fired_by": [r"\bduct\b", r"\bshaft\b", r"\bpenetration\b", r"\bfire damper\b", r"\bsmoke damper\b", r"\brated wall\b", r"\bmechanical reroute\b"],
        "likely_required_actions": ["Identify all rated assemblies penetrated", "Add damper/firestopping details and access panels", "Coordinate inspection access above ceilings"],
        "companion_permits": ["Mechanical permit, if separated locally"],
        "agencies": ["AHJ Mechanical/Building Dept", "Fire inspector"],
        "citations": ["IBC §717", "IMC §607", "IBC §714"],
        "primary_scope": "commercial_ti",
    },
    {
        "id": "commercial_ti_plumbing_fixture_count_change",
        "severity": "medium",
        "title": "Occupancy or occupant-load change can alter required restroom fixture counts",
        "why_it_matters": "A retail/office/assembly change may look like tenant buildout but can require additional fixtures or accessible upgrades if occupant load or use category changes.",
        "fired_by": [r"\bchange of occupancy\b", r"\bchange of use\b", r"\boccupant load\b", r"\brestroom\b", r"\bassembly\b", r"\btraining room\b", r"\bshowroom\b"],
        "likely_required_actions": ["Prepare occupant-load and plumbing-fixture-count table", "Check accessible fixture requirements", "Coordinate any restroom expansion with path-of-travel obligations"],
        "companion_permits": [],
        "agencies": ["AHJ Building/Plumbing Dept"],
        "citations": ["IPC §403.1", "IBC §2902", "2010 ADA Standards §213"],
        "primary_scope": "commercial_ti",
    },
    {
        "id": "commercial_ti_accessible_route_existing_building_alteration",
        "severity": "medium",
        "title": "Commercial alteration can require accessible-route/path-of-travel upgrades",
        "why_it_matters": "Even when the requested work is not 'an ADA project,' altered primary-function areas can require accessible route, restroom, drinking fountain, parking, and entrance upgrades within the disproportionality cap.",
        "fired_by": [r"\btenant improvement\b", r"\bti\b", r"\bprimary function\b", r"\baccessible route\b", r"\bpath of travel\b", r"\bfront counter\b", r"\breception\b"],
        "likely_required_actions": ["Prepare path-of-travel analysis", "Budget required accessible upgrades", "Document 20% disproportionality cap if used"],
        "companion_permits": [],
        "agencies": ["AHJ Building Dept", "Accessibility reviewer"],
        "citations": ["28 CFR §36.403", "2010 ADA Standards §202.4", "IBC Chapter 11"],
        "primary_scope": "commercial_ti",
    },
    {
        "id": "commercial_ti_exit_sign_emergency_lighting",
        "severity": "medium",
        "title": "Layout or occupant-load changes can require exit sign and emergency-lighting updates",
        "why_it_matters": "New partitions, corridors, rooms, or assembly areas can make the existing egress lighting/signage noncompliant and cause late electrical/fire inspection corrections.",
        "fired_by": [r"\bnew partitions?\b", r"\blayout change\b", r"\bexit sign\b", r"\bemergency lighting\b", r"\begress\b", r"\bcorridor\b"],
        "likely_required_actions": ["Show egress lighting photometrics/locations if required", "Add exit signs at changed paths", "Coordinate emergency circuits or battery units"],
        "companion_permits": ["Electrical permit, if separated locally"],
        "agencies": ["AHJ Electrical/Building Dept", "Fire inspector"],
        "citations": ["IBC §1008", "IBC §1013", "NFPA 101 [where adopted]"],
        "primary_scope": "commercial_ti",
    },
    {
        "id": "commercial_hvac_a2l_refrigerant_2025_transition",
        "severity": "medium",
        "title": "Commercial HVAC replacement must use A2L refrigerant per 2025 EPA AIM Act phasedown",
        "why_it_matters": "Manufacture/import of new R-410A rooftop units and split systems was largely cut off January 1, 2025 under EPA 40 CFR Part 84. Commercial replacements ship with A2L refrigerants (R-454B, R-32) that change clearance, leak detection, electrical disconnect, and EMS interlock requirements.",
        "fired_by": [r"\bhvac\b", r"\brtu\b", r"\brooftop unit\b", r"\bcondenser\b", r"\bvrf\b", r"\bvrv\b", r"\br[-\s]?410a\b", r"\br[-\s]?454b\b", r"\br[-\s]?32\b", r"\ba2l\b", r"\brefrigerant\b"],
        "likely_required_actions": ["Confirm new equipment is A2L-compatible (R-454B / R-32)", "Add refrigerant detection / EMS shutoff per ASHRAE 15 and listing", "Verify installer EPA Section 608 + manufacturer A2L training"],
        "companion_permits": ["Mechanical permit", "Electrical permit if disconnect / EMS changes"],
        "agencies": ["AHJ Mechanical Dept", "EPA"],
        "citations": ["40 CFR Part 84 (AIM Act technology transitions)", "ASHRAE 15-2022", "UL 60335-2-40", "IMC §1106 [verify adopted edition before merging]"],
        "primary_scope": "commercial_ti",
    },
    {
        "id": "commercial_change_of_occupancy_b_to_a2_or_m_sprinkler_general",
        "severity": "high",
        "title": "Change of occupancy (B/S to A-2/M) can trigger sprinkler retrofit and IEBC review",
        "why_it_matters": "Converting an office, warehouse, or retail tenant to assembly or mercantile crosses occupancy classifications. The new occupant load can cross IBC 903.2 sprinkler thresholds and require an IEBC change-of-occupancy analysis even when the construction work looks small.",
        "fired_by": [
            r"\bchange of (use|occupancy)\b",
            r"\b(b|s|s[-\s]?1|s[-\s]?2|m)\s*(to|-)\s*(a[-\s]?2|a[-\s]?3|a|m)\b",
            r"\b(office|warehouse|retail|storage)\s+(to|into|converted to|conversion to)\s+(restaurant|tavern|bar|assembly|gym|theater|mercantile|retail)\b",
            r"\bwarehouse\s+into\b",
            r"\bs[-\s]?1\s+warehouse\b",
            r"\biebc\b",
            r"\bchange[-\s]?of[-\s]?use\b",
        ],
        "likely_required_actions": [
            "Run IEBC change-of-occupancy analysis (Chapter 10)",
            "Recompute occupant load and check IBC 903.2 sprinkler thresholds",
            "Update accessible route and restroom/fixture counts to the higher occupancy",
            "Coordinate fire-alarm and egress lighting for the proposed occupancy",
        ],
        "companion_permits": ["Fire sprinkler permit if retrofit/relocation triggered", "Fire alarm permit if devices/coverage change"],
        "agencies": ["AHJ Building Dept", "Fire Prevention"],
        "citations": ["IEBC Chapter 10 [verify adopted edition before merging]", "IBC §903.2", "IBC §1004", "IBC §2902"],
        "primary_scope": "commercial_ti",
    },

    # ------------------------------------------------------------------
    # Residential single-trade triggers. These are skipped when primary is commercial. (6)
    # ------------------------------------------------------------------
    {
        "id": "residential_solar_gt_10kw_rapid_shutdown_pto",
        "severity": "medium",
        "title": "Large residential PV needs rapid-shutdown labeling and utility PTO coordination",
        "why_it_matters": "The electrical permit does not equal permission to operate. Utility interconnection/PTO and rapid-shutdown labeling often control energization.",
        "fired_by": [r"\bsolar\b", r"\bpv\b", r"\bphotovoltaic\b", r"\b(1[0-9]|[2-9][0-9])\s*kw\b", r"\b10\s*kw\b", r"\bpto\b"],
        "likely_required_actions": ["Show rapid-shutdown equipment and labels", "Submit utility interconnection application", "Do not energize before PTO"],
        "companion_permits": ["Utility interconnection / Permission to Operate"],
        "agencies": ["AHJ Electrical Dept", "Electric utility"],
        "citations": ["NEC §690.12", "NEC §690.56(C)", "NEC §705.12"],
        "primary_scope": "residential_single_trade",
    },
    {
        "id": "residential_ess_battery_clearances_ul9540a",
        "severity": "high",
        "title": "Residential ESS battery triggers NFPA 855 / UL 9540 clearance and commissioning checks",
        "why_it_matters": "Battery systems can fail plan check or inspection over location, separation from openings, listing, signage, ventilation, and emergency shutoff details.",
        "fired_by": [r"\bess\b", r"\bbattery\b", r"\bpowerwall\b", r"\benergy storage\b", r"\bul\s*9540\b", r"\bnfpa\s*855\b"],
        "likely_required_actions": ["Show listed ESS equipment and location", "Verify clearances from openings/ignition sources", "Provide commissioning or manufacturer certificate if required"],
        "companion_permits": ["ESS/electrical permit", "Utility interconnection if paired with PV"],
        "agencies": ["AHJ Electrical Dept", "Fire Dept", "Electric utility"],
        "citations": ["NFPA 855", "UL 9540", "UL 9540A", "IRC §R328"],
        "primary_scope": "residential_single_trade",
    },
    {
        "id": "residential_panel_upgrade_load_calc_utility_grounding",
        "severity": "medium",
        "title": "Panel/service upgrade requires load calculation, utility coordination, and grounding-electrode update",
        "why_it_matters": "A panel swap can become a service upgrade, meter/main change, utility disconnect/reconnect, and grounding/bonding correction once loads or service rating change.",
        "fired_by": [r"\bpanel upgrade\b", r"\bservice upgrade\b", r"\b200\s*amp\b", r"\b400\s*amp\b", r"\bmeter main\b", r"\bmain panel\b"],
        "likely_required_actions": ["Prepare dwelling load calculation", "Coordinate utility service disconnect/reconnect", "Update grounding electrode and bonding details"],
        "companion_permits": ["Electrical service permit", "Utility service order"],
        "agencies": ["AHJ Electrical Dept", "Electric utility"],
        "citations": ["NEC Article 220", "NEC Article 230", "NEC §250.50", "NEC §250.52"],
        "primary_scope": "residential_single_trade",
    },
    {
        "id": "residential_water_heater_pan_tp",
        "severity": "medium",
        "title": "Water heater replacement requires pan, T&P discharge, expansion, and combustion-air details",
        "why_it_matters": "A simple swap often fails inspection when discharge piping, pan drain, combustion air, or expansion control is missing.",
        "fired_by": [r"\bwater heater\b", r"\btankless\b", r"\bhybrid water heater\b", r"\bheat pump water heater\b", r"\bt&p\b"],
        "likely_required_actions": ["Show T&P discharge termination", "Add drain pan where leakage damage is possible", "Verify combustion air/venting or electrical circuit", "Confirm expansion control on closed systems"],
        "companion_permits": ["Plumbing/mechanical/electrical trade permit as locally required"],
        "agencies": ["AHJ Plumbing/Mechanical Dept"],
        "citations": ["IPC §504.6", "IPC §504.7", "IRC §P2801.6"],
        "primary_scope": "residential_single_trade",
    },
    {
        "id": "residential_water_heater_seismic_strap",
        "severity": "medium",
        "title": "Water heater seismic strapping required in seismic-zone amendments",
        "why_it_matters": "California, Alaska, and other state seismic amendments require water heater anchoring at the upper and lower thirds. Inspectors fail finals over missing or improper straps.",
        "fired_by": [r"\bwater heater\b", r"\btankless\b", r"\bhybrid water heater\b", r"\bheat pump water heater\b", r"\bseismic strap\b"],
        "regions": ["seismic_strap_required"],
        "likely_required_actions": ["Strap water heater at upper and lower thirds", "Use approved seismic strap or listed restraint per local amendment", "Verify gas flex connector and earthquake gas shutoff if locally required"],
        "companion_permits": [],
        "agencies": ["AHJ Plumbing/Mechanical Dept"],
        "citations": ["CPC §507.2 [California seismic amendment]", "IRC §P2801.6", "Local seismic strap ordinance [verify before merging]"],
        "primary_scope": "residential_single_trade",
    },
    {
        "id": "residential_pool_bonding_gfci_vgb",
        "severity": "high",
        "title": "Pool work triggers equipotential bonding, GFCI, and anti-entrapment review",
        "why_it_matters": "Pool electrical and suction-entrapment requirements are life-safety items. They often require coordination between pool, electrical, plumbing, and barrier inspections.",
        "fired_by": [r"\bpool\b", r"\bspa\b", r"\bhot tub\b", r"\bequipotential bonding\b", r"\bpool pump\b", r"\banti[-\s]?entrapment\b"],
        "likely_required_actions": ["Show equipotential bonding grid", "GFCI-protect pool equipment and receptacles", "Provide listed drain covers / anti-entrapment compliance", "Coordinate barrier/alarm requirements"],
        "companion_permits": ["Pool permit", "Electrical permit", "Barrier/fence permit if separated locally"],
        "agencies": ["AHJ Building/Electrical Dept", "Health Dept for public pools"],
        "citations": ["NEC §680.26", "NEC §680.21(C)", "NEC §680.22", "Virginia Graeme Baker Pool and Spa Safety Act"],
        "primary_scope": "residential_single_trade",
    },
    {
        "id": "residential_ev_charger_load_management",
        "severity": "medium",
        "title": "EV charger may require service-load calculation or listed load-management equipment",
        "why_it_matters": "A Level 2 EVSE can exceed existing service capacity unless the design uses a code-compliant load calculation or listed energy-management system.",
        "fired_by": [r"\bev charger\b", r"\bevse\b", r"\blevel\s*2\b", r"\btesla wall connector\b", r"\bvehicle charger\b", r"\bload management\b"],
        "likely_required_actions": ["Prepare service load calculation", "Show EVSE rating and circuit", "Use listed energy-management/load-shed equipment if needed"],
        "companion_permits": ["Electrical permit", "Utility notification if required"],
        "agencies": ["AHJ Electrical Dept", "Electric utility if service impact"],
        "citations": ["NEC Article 625", "NEC §220.57 [verify edition before merging]"],
        "primary_scope": "residential_single_trade",
    },
    {
        "id": "residential_hvac_a2l_refrigerant_2025_transition",
        "severity": "medium",
        "title": "New HVAC equipment must use A2L refrigerant per 2025 EPA AIM Act phasedown",
        "why_it_matters": "After January 1, 2025, EPA 40 CFR Part 84 prohibits manufacture/import of most R-410A residential split systems. New installs use A2L refrigerants (R-454B, R-32) requiring updated leak detection, electrical disconnects, and brazing/installer certification.",
        "fired_by": [r"\bhvac\b", r"\bcondenser\b", r"\bfurnace\b", r"\bheat pump\b", r"\bsplit system\b", r"\bmini[-\s]?split\b", r"\br[-\s]?410a\b", r"\br[-\s]?454b\b", r"\br[-\s]?32\b", r"\ba2l\b", r"\brefrigerant\b"],
        "likely_required_actions": ["Confirm new equipment is A2L-compatible (R-454B or R-32)", "Verify installer is EPA Section 608 certified for A2L handling", "Show service disconnect and refrigerant detection where required by listing"],
        "companion_permits": ["Mechanical permit", "Electrical permit if disconnect changes"],
        "agencies": ["AHJ Mechanical Dept", "EPA"],
        "citations": ["40 CFR Part 84 (AIM Act technology transitions)", "ASHRAE 15-2022", "UL 60335-2-40", "IMC §1106 [verify adopted edition before merging]"],
        "primary_scope": "residential_single_trade",
    },
    {
        "id": "residential_heat_pump_ira_25c_rebates",
        "severity": "low",
        "title": "Heat pump / HPWH / solar / EV qualifies for IRA 25C federal credit and state utility rebates",
        "why_it_matters": "Customers leave thousands of dollars on the table when contractors do not surface the federal 25C tax credit (up to $2,000/yr for HP HVAC and HPWH) and state/utility rebates that are time-bound and stack on top of permit costs.",
        "fired_by": [r"\bheat pump\b", r"\bhpwh\b", r"\bheat pump water heater\b", r"\bhybrid water heater\b", r"\bhp hvac\b", r"\bmini[-\s]?split\b", r"\bsolar\b", r"\bphotovoltaic\b", r"\bpv\b", r"\bev charger\b", r"\bevse\b"],
        "likely_required_actions": ["Tell customer about IRC §25C credit (HPWH/HP HVAC up to $2,000)", "Check state energy office and electric utility rebate database (e.g., Focus on Energy in WI, TECH Clean California, Mass Save)", "Verify equipment AHRI/CEE tier required for the rebate before quoting"],
        "companion_permits": [],
        "agencies": ["IRS / Treasury", "State energy office", "Electric/gas utility"],
        "citations": ["26 U.S.C. §25C (Energy Efficient Home Improvement Credit)", "Inflation Reduction Act §13301", "DOE Home Energy Rebate program guidance [verify current state allocation before quoting]"],
        "primary_scope": "residential_single_trade",
    },
    {
        "id": "fl_hvhz_noa_impact_rated_envelope",
        "severity": "high",
        "title": "Miami-Dade / Broward HVHZ work requires NOA-listed impact-rated products",
        "why_it_matters": "In the Florida HVHZ (Miami-Dade and Broward counties), windows, doors, garage doors, skylights, and roof systems must carry an active Miami-Dade Notice of Acceptance (NOA) or Florida Product Approval. Inspectors will fail final without the NOA number on the permit set.",
        "fired_by": [r"\bwindow\b", r"\bdoor\b", r"\bgarage door\b", r"\bskylight\b", r"\broof\b", r"\breroof\b", r"\bre-?roof\b", r"\bshingle\b", r"\btile roof\b", r"\bhurricane\b", r"\bimpact\b", r"\bhvhz\b", r"\bnoa\b"],
        "regions": ["fl_coastal_hvhz"],
        "likely_required_actions": ["Specify products by Miami-Dade NOA number (or active Florida Product Approval) on the permit set", "Show secondary water resistance / underlayment per FBC 1518 [verify edition]", "Provide hurricane strap / clip schedule and uplift calc consistent with the NOA"],
        "companion_permits": ["Roofing permit", "Window/door permit"],
        "agencies": ["Miami-Dade Building Code Compliance", "AHJ Building Dept"],
        "citations": ["Florida Building Code (FBC) Chapter 16 HVHZ", "Miami-Dade NOA program", "FBC §1518 [verify current edition before merging]"],
        "primary_scope": "residential_single_trade",
    },

    # ------------------------------------------------------------------
    # Multifamily triggers (6)
    # ------------------------------------------------------------------
    {
        "id": "multifamily_type_b_accessible_unit_ratio",
        "severity": "high",
        "title": "Multifamily projects must track Type B accessible-unit count and dispersion",
        "why_it_matters": "Unit mix, alterations, and additions can fail review if the accessible/adaptable unit count, accessible route, and feature dispersion are not shown early.",
        "fired_by": [r"\bmultifamily\b", r"\bmulti[-\s]?family\b", r"\bapartments?\b", r"\br[-\s]?2\b", r"\btype b\b", r"\baccessible units?\b"],
        "likely_required_actions": ["Add Type A/Type B unit matrix", "Show accessible route to units and common areas", "Verify dispersion across unit types/floors"],
        "companion_permits": [],
        "agencies": ["AHJ Building Dept", "Accessibility/Fair Housing reviewer"],
        "citations": ["IBC §1107.6.2", "ICC A117.1", "Fair Housing Act Design Manual [where applicable]"],
        "primary_scope": "multifamily",
    },
    {
        "id": "multifamily_nfpa_13r_sprinkler_coverage_limits",
        "severity": "high",
        "title": "NFPA 13R multifamily systems have coverage limits that must match building design",
        "why_it_matters": "Using 13R instead of 13 can affect height, area, concealed spaces, balconies, garages, mixed use, and allowable increases. A mismatch can force redesign late.",
        "fired_by": [r"\bnfpa\s*13r\b", r"\b13r\b", r"\bsprinkler\b", r"\bpodium\b", r"\bapartment\b", r"\br[-\s]?2\b"],
        "likely_required_actions": ["Confirm NFPA 13 vs 13R eligibility", "Review concealed spaces, balconies, garages, and mixed-use areas", "Coordinate sprinkler design basis with code analysis"],
        "companion_permits": ["Fire sprinkler permit"],
        "agencies": ["Fire Prevention", "AHJ Building Dept"],
        "citations": ["IBC §903.3.1.2", "NFPA 13R", "IBC §504.2"],
        "primary_scope": "multifamily",
    },
    {
        "id": "multifamily_dwelling_unit_fire_separation",
        "severity": "high",
        "title": "Dwelling-unit separations require rated wall/floor assemblies and penetration protection",
        "why_it_matters": "Unit-to-unit and unit-to-corridor separations are core life-safety features; MEP penetrations, recessed boxes, ducts, and shafts can break the rating if not detailed.",
        "fired_by": [r"\bunit separation\b", r"\bbetween units\b", r"\bapartment remodel\b", r"\brated assembly\b", r"\bpenetrations?\b", r"\bfloor ceiling\b"],
        "likely_required_actions": ["List rated assemblies by UL/GA design", "Detail penetrations, boxes, ducts, and firestopping", "Coordinate corridor/shaft ratings"],
        "companion_permits": [],
        "agencies": ["AHJ Building Dept", "Fire inspector"],
        "citations": ["IBC §420.2", "IBC §420.3", "IBC §714", "IBC §711"],
        "primary_scope": "multifamily",
    },
    {
        "id": "multifamily_trash_chute_fire_doors_sprinklers",
        "severity": "medium",
        "title": "Trash/laundry chutes need rated shaft, opening protectives, and sprinkler coordination",
        "why_it_matters": "Chute alterations are deceptively small but touch rated shafts, fire doors, discharge rooms, sprinklers, access control, and sanitation/odor details.",
        "fired_by": [r"\btrash chute\b", r"\brefuse chute\b", r"\blaundry chute\b", r"\bchute door\b", r"\bcompactor\b"],
        "likely_required_actions": ["Show shaft rating and chute access doors", "Coordinate sprinklers and discharge room protection", "Verify intake/discharge room ventilation and firestopping"],
        "companion_permits": ["Fire sprinkler permit if heads change"],
        "agencies": ["AHJ Building Dept", "Fire Prevention", "Health/Sanitation if applicable"],
        "citations": ["IBC §713.13", "NFPA 82 [verify edition before merging]", "IFC §304 [verify local application before merging]"],
        "primary_scope": "multifamily",
    },
    {
        "id": "multifamily_fire_alarm_r2_manual_smoke_notification",
        "severity": "medium",
        "title": "R-2 multifamily work can trigger fire-alarm, smoke detection, and occupant-notification review",
        "why_it_matters": "Adding units, corridors, common areas, or sprinkler changes can require alarm shop drawings, notification appliances, monitoring, and in-unit smoke/CO coordination.",
        "fired_by": [r"\bfire alarm\b", r"\bsmoke detector\b", r"\bnotification\b", r"\bmonitoring\b", r"\br[-\s]?2\b", r"\bapartment\b"],
        "likely_required_actions": ["Confirm fire-alarm system threshold and scope", "Coordinate in-unit smoke/CO alarms with building alarm if required", "Submit alarm shop drawings if devices change"],
        "companion_permits": ["Fire alarm permit"],
        "agencies": ["Fire Prevention", "AHJ Building Dept"],
        "citations": ["IBC §907.2.9", "NFPA 72", "IRC §R314 [for IRC-regulated portions]", "IRC §R315 [for IRC-regulated portions]"],
        "primary_scope": "multifamily",
    },
    {
        "id": "multifamily_accessible_parking_route_common_areas",
        "severity": "medium",
        "title": "Multifamily alterations can trigger accessible parking, route, and common-area upgrades",
        "why_it_matters": "Work on leasing offices, amenities, parking, mail rooms, laundry, or routes to units can create accessibility obligations outside the unit being altered.",
        "fired_by": [r"\bparking\b", r"\baccessible route\b", r"\bcommon area\b", r"\bleasing office\b", r"\blaundry room\b", r"\bmail room\b", r"\bamenity\b"],
        "likely_required_actions": ["Check accessible parking count and signage", "Show accessible route from site arrival points", "Review common-area doors, slopes, counters, and amenities"],
        "companion_permits": [],
        "agencies": ["AHJ Building Dept", "Accessibility/Fair Housing reviewer"],
        "citations": ["IBC §1104", "IBC §1106", "2010 ADA Standards §206", "Fair Housing Act Design Manual [where applicable]"],
        "primary_scope": "multifamily",
    },
]


SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

COMMERCIAL_SCOPES = {
    "commercial",
    "commercial_ti",
    "commercial_office_ti",
    "commercial_retail_ti",
    "commercial_restaurant",
    "commercial_restaurant_ti",
    "change_of_occupancy",
    "ada_path_of_travel",
}

ADU_SCOPES = {
    "residential_adu",
    "residential_jadu",
    "residential_garage_conversion",
    "residential_hillside_adu",
    "adu",
    "accessory_dwelling_unit",
    "junior_adu",
    "jadu",
    "dadu",
    "garage_conversion_adu",
}

MULTIFAMILY_SCOPES = {
    "multifamily",
    "multi_family",
    "commercial_multifamily",
    "apartments",
    "apartment",
    "r2",
    "r-2",
}

# Jurisdiction overlays kept outside the public trigger dicts so the returned
# result stays exactly on the requested trigger schema. Prefix matching means
# future Phoenix/LA IDs automatically inherit the city/state guard.
TRIGGER_JURISDICTION_PREFIXES = {
    "phoenix_": {"state": "az", "city_any": {"phoenix"}},
    "maricopa_": {"state": "az", "city_any": {"phoenix", "mesa", "tempe", "scottsdale", "glendale", "chandler", "maricopa"}},
    "la_": {"state": "ca", "city_any": {"los angeles", "la"}},
}

# Named regions for the explicit `regions` / `not_regions` schema keys. Use
# these on triggers that need geographic gating but do not follow the
# city-prefix ID convention (seismic strap, HVHZ NOA, named historic districts,
# etc.). All present constraints inside a region must match (AND semantics).
NAMED_REGIONS = {
    "seismic_strap_required": {
        # States with statewide water-heater seismic strapping amendments. Other
        # states (e.g. NV, OR, WA) have partial or local rules; expand here when
        # a verified citation justifies it.
        "states": {"ca", "ak"},
    },
    "fl_coastal_hvhz": {
        # High Velocity Hurricane Zone — Miami-Dade and Broward counties (FBC).
        "states": {"fl"},
        "city_any": {
            "miami", "miami beach", "miami gardens", "north miami", "north miami beach",
            "coral gables", "doral", "hialeah", "homestead", "key biscayne", "aventura",
            "sunny isles beach", "cutler bay", "palmetto bay", "pinecrest", "kendall",
            "fort lauderdale", "hollywood", "pompano beach", "pembroke pines", "davie",
            "plantation", "sunrise", "coral springs", "deerfield beach", "weston",
        },
    },
    "named_historic_district": {
        # Triggers gated to this region only fire when the job context names a
        # historic district / HPC review. Geography is text-driven, not
        # state-driven, so any state passes when text matches.
        "text_any_pattern": [
            r"\bhistoric district\b",
            r"\bhistoric preservation\b",
            r"\bhpc\b",
            r"\bcertificate of appropriateness\b",
            r"\bnational register\b",
            r"\blandmark commission\b",
        ],
    },
    "adu_protected_tree_city": {
        "city_any": {
            "los angeles", "oakland", "berkeley", "sacramento",
            "san francisco", "san diego", "seattle",
        },
    },
    "explicit_protected_tree_context": {
        "text_any_pattern": [
            r"\bprotected tree\b",
            r"\bheritage tree\b",
            r"\bsignificant tree\b",
            r"\boak\b",
            r"\bsycamore\b",
            r"\barborist\b",
        ],
    },
}

# Strip these helper keys if future registries add them. Current registry uses
# only the public schema, but keeping this makes the detector safe to extend.
PRIVATE_TRIGGER_KEYS = {"_notes", "_jurisdiction", "_scope_aliases"}

# Schema keys that exist on the trigger dict but should not be returned to the
# caller because they are internal gating metadata.
INTERNAL_TRIGGER_KEYS = {"regions", "not_regions"}


def _normalize(value: Any) -> str:
    """Lowercase, collapse whitespace, and normalize common punctuation."""
    text = "" if value is None else str(value)
    text = text.replace("&", " and ")
    text = re.sub(r"[_/]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _flatten_result_context(result: dict) -> str:
    """Use deterministic structured context only; no model calls or inference."""
    if not isinstance(result, dict):
        return ""

    # These fields are the ones most likely to contain occupancy markers or
    # square footage already extracted by the base engine. Avoid dumping the
    # full result to reduce accidental matches from long boilerplate templates.
    keys = (
        "existing_occupancy",
        "proposed_occupancy",
        "occupancy",
        "occupancy_group",
        "scope",
        "scope_summary",
        "project_description",
        "description",
        "work_description",
        "building_area_sqft",
        "tenant_area_sqft",
    )
    pieces: list[str] = []
    for key in keys:
        val = result.get(key)
        if val is None:
            continue
        if isinstance(val, (str, int, float, bool)):
            pieces.append(str(val))
        elif isinstance(val, list):
            pieces.extend(str(item) for item in val if isinstance(item, (str, int, float, bool)))
        elif isinstance(val, dict):
            pieces.extend(str(item) for item in val.values() if isinstance(item, (str, int, float, bool)))
    return _normalize(" ".join(pieces))


def _is_commercial_scope(primary_scope: str) -> bool:
    scope = _normalize(primary_scope)
    return scope in {_normalize(s) for s in COMMERCIAL_SCOPES} or scope.startswith("commercial")


def _scope_applies(trigger_scope: str, primary_scope: str, text: str) -> bool:
    trigger_scope_norm = _normalize(trigger_scope)
    primary_scope_norm = _normalize(primary_scope)

    if trigger_scope_norm == primary_scope_norm:
        return True

    if trigger_scope_norm == _normalize("commercial_restaurant"):
        return (
            primary_scope_norm in {_normalize("commercial_restaurant"), _normalize("commercial_restaurant_ti")}
            or ("restaurant" in text and (_is_commercial_scope(primary_scope_norm) or "tenant improvement" in text or " ti " in f" {text} "))
        )

    if trigger_scope_norm == _normalize("commercial_retail_ti"):
        return (
            primary_scope_norm == _normalize("commercial_retail_ti")
            or ("retail" in text and (_is_commercial_scope(primary_scope_norm) or "tenant improvement" in text or " ti " in f" {text} "))
        )

    if trigger_scope_norm == _normalize("commercial_ti"):
        return _is_commercial_scope(primary_scope_norm)

    if trigger_scope_norm == _normalize("residential_adu"):
        if _is_commercial_scope(primary_scope_norm):
            return False
        return primary_scope_norm in {_normalize(s) for s in ADU_SCOPES} or bool(re.search(r"\b(adu|dadu|jadu|accessory dwelling|garage conversion)\b", text))

    if trigger_scope_norm == _normalize("residential_single_trade"):
        # Explicit requirement: skip residential single-trade triggers when the
        # primary scope is commercial, even if words like "panel" or "water
        # heater" appear in a commercial TI description.
        return not _is_commercial_scope(primary_scope_norm)

    if trigger_scope_norm == "multifamily":
        return primary_scope_norm in {_normalize(s) for s in MULTIFAMILY_SCOPES} or bool(re.search(r"\b(multifamily|multi[-\s]?family|apartment|apartments|r[-\s]?2)\b", text))

    return False


def _jurisdiction_applies(trigger_id: str, city: str, state: str, text: str) -> bool:
    city_norm = _normalize(city)
    state_norm = _normalize(state)
    trigger_id_norm = _normalize(trigger_id).replace(" ", "_")

    for prefix, guard in TRIGGER_JURISDICTION_PREFIXES.items():
        if not trigger_id_norm.startswith(prefix):
            continue

        required_state = guard.get("state")
        if required_state and state_norm not in {required_state, required_state.upper(), required_state.lower()}:
            return False

        city_any = guard.get("city_any") or set()
        if city_any:
            # Also allow explicit city/county mention in job text. This helps
            # if the caller passes city="LA" but job_type says Los Angeles, or
            # if a county overlay is relevant to a Phoenix project.
            city_hit = any(c == city_norm or c in text for c in city_any)
            if not city_hit:
                return False

    return True


def _pattern_matches(pattern: str, text: str) -> bool:
    """Treat registry fired_by entries as regex; fall back to substring if bad."""
    try:
        return re.search(pattern, text, flags=re.IGNORECASE) is not None
    except re.error:
        return _normalize(pattern) in text


def _trigger_matches(trigger: dict, text: str) -> bool:
    return any(_pattern_matches(pattern, text) for pattern in trigger.get("fired_by", []))


def _region_token_applies(token: str, city: str, state: str, text: str) -> bool:
    """Resolve a `regions` / `not_regions` token against (city, state, text).

    Tokens may be a 2-letter state code (e.g. "ca") or a named region defined
    in NAMED_REGIONS. Inside a named region, every present constraint
    (`states`, `city_any`, `text_any_pattern`) must pass.
    """
    token_norm = _normalize(token).replace(" ", "_")
    if not token_norm:
        return False

    state_norm = _normalize(state)
    city_norm = _normalize(city)

    # Bare state code shortcut.
    if len(token_norm) == 2 and token_norm.isalpha():
        return state_norm == token_norm

    region = NAMED_REGIONS.get(token_norm)
    if not region:
        return False

    states = region.get("states")
    if states and state_norm not in {_normalize(s) for s in states}:
        return False

    city_any = region.get("city_any")
    if city_any:
        cities = {_normalize(c) for c in city_any}
        # Allow either an exact city-arg match or the name appearing in job text
        # (helps when caller passes a county or alt spelling).
        if city_norm not in cities and not any(c in text for c in cities):
            return False

    text_patterns = region.get("text_any_pattern")
    if text_patterns and not any(_pattern_matches(p, text) for p in text_patterns):
        return False

    return True


def _region_applies(trigger: dict, city: str, state: str, text: str) -> bool:
    """Return False iff this trigger's `regions`/`not_regions` exclude this job."""
    regions = trigger.get("regions")
    if regions and not any(_region_token_applies(t, city, state, text) for t in regions):
        return False

    not_regions = trigger.get("not_regions")
    if not_regions and any(_region_token_applies(t, city, state, text) for t in not_regions):
        return False

    return True


def _public_trigger(trigger: dict) -> dict:
    clean = {
        k: copy.deepcopy(v)
        for k, v in trigger.items()
        if k not in PRIVATE_TRIGGER_KEYS and k not in INTERNAL_TRIGGER_KEYS
    }
    # Make sure all expected public fields exist, even if a future appended
    # trigger omitted an optional list.
    clean.setdefault("likely_required_actions", [])
    clean.setdefault("companion_permits", [])
    clean.setdefault("agencies", [])
    clean.setdefault("citations", [])
    clean.setdefault("fired_by", [])
    return clean


def detect_hidden_triggers(job_type: str, city: str, state: str, primary_scope: str, result: dict) -> list[dict]:
    """Return triggers that fire for this query, sorted by severity then alphabetical.

    Deterministic V1 rules:
    - Match only lowercased user job text plus selected structured base-result
      occupancy/scope fields. No LLM calls, no network calls.
    - A trigger must pass scope gating, jurisdiction gating, region gating
      (`regions` / `not_regions`), and at least one `fired_by` regex/token match.
    - Residential single-trade triggers are suppressed for commercial primary
      scopes.
    - Returned dicts are copies, so callers can safely mutate/sanitize them.
    """
    job_text = _normalize(job_type)
    result_text = _flatten_result_context(result)
    text = _normalize(f"{job_text} {result_text}")

    fired: list[dict] = []
    seen_ids: set[str] = set()

    for trigger in HIDDEN_TRIGGER_REGISTRY:
        trigger_id = trigger.get("id", "")
        if not trigger_id or trigger_id in seen_ids:
            continue
        if not _scope_applies(trigger.get("primary_scope", ""), primary_scope, text):
            continue
        if not _jurisdiction_applies(trigger_id, city, state, text):
            continue
        if not _region_applies(trigger, city, state, text):
            continue
        if not _trigger_matches(trigger, text):
            continue

        fired.append(_public_trigger(trigger))
        seen_ids.add(trigger_id)

    fired.sort(key=lambda t: (SEVERITY_RANK.get(t.get("severity", "low"), 99), t.get("title", "")))
    return fired
