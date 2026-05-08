#!/usr/bin/env python3
"""Core vertical trust-gate regression tests before 60-AHJ expansion.

These are deterministic: no live model/API calls. They lock the failure modes
from the 2026-05-07 QA/handoff: office TI must not inherit restaurant hood /
grease / health adders through negated words, ordinary professional office must
not become medical clinic, and residential work must not leak commercial warnings.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

import api.research_engine as engine  # noqa: E402
import api.server as server  # noqa: E402
from api.evidence_pack_runtime import _vertical_for_job  # noqa: E402
from api.fee_realism_guardrail import apply_fee_realism_guardrail  # noqa: E402
from api.hidden_trigger_detector import detect_hidden_triggers  # noqa: E402


def _trigger_blob(triggers):
    return " | ".join(
        " ".join(str(t.get(k, "")) for k in ("id", "title", "why_it_matters"))
        for t in triggers
        if isinstance(t, dict)
    ).lower()


def _companion_blob(result):
    return " | ".join(
        " ".join(str(c.get(k, "")) for k in ("permit_type", "reason", "required_if"))
        for c in result.get("companion_permits", [])
        if isinstance(c, dict)
    ).lower()


def _checklist_blob(job, city="Dallas", state="TX"):
    result = {"_primary_scope": engine.detect_primary_scope(job), "license_required": "", "applying_office": ""}
    return " | ".join(engine.generate_permit_checklist(job, city, state, result)).lower()


def _required_families(result):
    return {engine._permit_family(p) for p in result.get("permits_required", []) if isinstance(p, dict)}


def _required_permit_blob(result):
    fields = []
    for key in ("permits_required", "permits_required_logic"):
        value = result.get(key)
        if isinstance(value, list):
            fields.extend(str(item) for item in value)
    return " | ".join(fields).lower()


def _apply_core_layers(job, city="Dallas", state="TX"):
    result = {
        "permit_verdict": "YES",
        "confidence": "high",
        "fee_range": "$800-$1,500",
        "permits_required": [],
        "permits_required_logic": [],
        "companion_permits": [],
        "pro_tips": [],
        "watch_out": [],
        "common_mistakes": [],
        "inspections": [],
    }
    result["_primary_scope"] = engine.detect_primary_scope(job)
    engine.apply_scope_aware_permit_classification(result, job)
    result["hidden_triggers"] = detect_hidden_triggers(job, city, state, result["_primary_scope"], result)
    engine.apply_office_ti_rulebook(result, job, city, state)
    engine.apply_retail_ti_rulebook(result, job, city, state)
    engine.apply_medical_clinic_ti_rulebook(result, job, city, state)
    engine.enforce_ti_min_permits_floor(result, job, city, state)
    result = apply_fee_realism_guardrail(result, job, city, state, result["_primary_scope"])
    return result


def test_office_ti_strong_negation_has_no_restaurant_hood_grease_or_health_adders():
    job = (
        "3,000 sf commercial office TI for accounting suite with partitions, conference rooms, "
        "lighting controls and HVAC diffuser relocation; no restaurant, no food service, "
        "no commercial kitchen, no Type I hood, no fryer, no griddle, no ANSUL, "
        "and no grease interceptor."
    )

    out = _apply_core_layers(job, "Dallas", "TX")

    assert out["_primary_scope"] == "commercial_office_ti"
    assert out["_fee_floor_components"]["scope"] == "commercial_office_ti"
    assert {a["key"] for a in out["_fee_floor_components"]["trigger_adders"]}.isdisjoint({"hood_fire_suppression", "grease_interceptor"})
    restaurant_blob = _trigger_blob(out["hidden_triggers"]) + " | " + _companion_blob(out) + " | " + out["fee_range"].lower()
    assert "grease" not in restaurant_blob
    assert "hood" not in restaurant_blob
    assert "ansul" not in restaurant_blob
    assert "food establishment" not in restaurant_blob
    assert "health department" not in restaurant_blob


def test_restaurant_cosmetic_no_kitchen_does_not_create_hood_or_grease_adders():
    job = (
        "1,800 sf existing restaurant dining-room cosmetic refresh: paint, flooring, seating, "
        "decor and lighting only; no kitchen work, no Type I hood, no grease interceptor, "
        "no fryer, no griddle, no ANSUL and no food-prep equipment changes."
    )

    out = _apply_core_layers(job, "Chicago", "IL")

    assert out["_primary_scope"] == "commercial_restaurant"
    adder_keys = {a["key"] for a in out["_fee_floor_components"]["trigger_adders"]}
    assert "hood_fire_suppression" not in adder_keys
    assert "grease_interceptor" not in adder_keys
    assert "hood" not in out["fee_range"].lower()
    assert "grease" not in out["fee_range"].lower()
    assert "mechanical" not in _required_families(out)
    assert "plumbing" not in _required_families(out)
    assert "fire" not in _required_families(out)
    required_blob = _required_permit_blob(out)
    assert "hood" not in required_blob
    assert "grease" not in required_blob
    assert "ansul" not in required_blob
    customer_blob = _trigger_blob(out["hidden_triggers"]) + " | " + _companion_blob(out) + " | " + " | ".join(engine.generate_permit_checklist(job, "Miami", "FL", out)).lower()
    assert "health department" not in customer_blob
    assert "food establishment" not in customer_blob
    assert "food-establishment" not in customer_blob


def test_pc02_restaurant_cosmetic_negative_los_angeles_no_required_trade_permits():
    job = (
        "PC02 QA marker: restaurant cosmetic refresh in Los Angeles CA, dining room finishes, "
        "paint, flooring, seating and decor only; no kitchen, no hood, no fryer, no grease, "
        "no fire suppression, no ANSUL, no food-prep equipment changes."
    )

    out = _apply_core_layers(job, "Los Angeles", "CA")

    assert out["_primary_scope"] == "commercial_restaurant"
    assert "building" in _required_families(out)
    for forbidden in ("mechanical", "plumbing", "fire"):
        assert forbidden not in _required_families(out)
    required_blob = _required_permit_blob(out)
    for forbidden in ("mechanical permit", "grease", "hood", "ansul"):
        assert forbidden not in required_blob


def test_full_restaurant_kitchen_still_triggers_health_hood_and_grease():
    job = (
        "3,200 sf restaurant TI converting retail to full commercial kitchen with Type I hood, "
        "ANSUL fire suppression, fryer, griddle, dishwashing, walk-in cooler, grease interceptor, "
        "new kitchen plumbing and health department food-establishment review."
    )

    out = _apply_core_layers(job, "Chicago", "IL")

    assert out["_primary_scope"] == "commercial_restaurant"
    blob = _trigger_blob(out["hidden_triggers"]) + " | " + _companion_blob(out) + " | " + out["fee_range"].lower()
    assert "hood" in blob or "suppression" in blob or "ansul" in blob
    assert "grease" in blob
    assert "health" in blob or "food-establishment" in blob or "food establishment" in blob
    adder_keys = {a["key"] for a in out["_fee_floor_components"]["trigger_adders"]}
    assert {"hood_fire_suppression", "grease_interceptor"}.issubset(adder_keys)


def test_retail_packaged_food_without_prep_does_not_surface_health_department_caution():
    job = (
        "retail tenant improvement for packaged snack and beverage store with shelving, "
        "checkout counter, back stockroom and restroom refresh; no food preparation, "
        "no restaurant, no kitchen, no hood, no grease work."
    )
    checklist = _checklist_blob(job, "Dallas", "TX")
    out = _apply_core_layers(job, "Dallas", "TX")
    gated = server.apply_permitiq_quality_gate(out, job, "Dallas", "TX")
    blob = checklist + " | " + _customer_surface_blob(gated)

    assert out["_primary_scope"] == "commercial_retail_ti"
    assert "mechanical" not in _required_families(out)
    assert "fire" not in _required_families(out)
    for forbidden in ("health department", "food establishment", "food-establishment", "commercial kitchen", "hood", "grease"):
        assert forbidden not in blob


def test_rc04_random_commercial_packaged_retail_phoenix_no_required_mechanical_without_hvac():
    job = (
        "RC04 QA marker: packaged retail tenant improvement in Phoenix AZ for shelving, checkout, "
        "back stockroom and sealed snack and bottled beverage sales only; no food prep, no kitchen, "
        "no hood, no grease, no HVAC, no mechanical work stated."
    )

    out = _apply_core_layers(job, "Phoenix", "AZ")

    assert out["_primary_scope"] == "commercial_retail_ti"
    assert "building" in _required_families(out)
    for forbidden in ("mechanical", "fire"):
        assert forbidden not in _required_families(out)
    required_blob = _required_permit_blob(out)
    for forbidden in ("mechanical permit", "hood", "grease"):
        assert forbidden not in required_blob


class _FakeSerperResponse:
    status_code = 200

    def __init__(self, organic):
        self._organic = organic

    def json(self):
        return {"organic": self._organic}


def test_pa300_packaged_retail_source_cache_is_query_aware(tmp_path, monkeypatch):
    restaurant_query = (
        "Los Angeles CA restaurant tenant improvement with commercial kitchen, Type I hood, "
        "grease interceptor and food preparation code section permit"
    )
    packaged_query = (
        "Los Angeles CA packaged snack and bottled beverage retail tenant improvement with shelving, "
        "checkout, stockroom and restroom refresh; no food preparation, no restaurant, no kitchen, "
        "no hood, no grease code section permit"
    )
    monkeypatch.setattr(engine, "SERPER_CACHE_DB", str(tmp_path / "serper_cache.db"))
    monkeypatch.setattr(engine, "SERPER_API_KEY", "")
    engine._set_cached_serper_source(
        "Los Angeles",
        "CA",
        "code_section",
        restaurant_query,
        {
            "title": "[PDF] AB 671 - Accelerated Restaurant Building Plan Review: California ...",
            "url": "https://publichealth.lacounty.gov/eh/docs/ab-671-plan-review-tenant-improvements-faq-en.pdf",
            "snippet": "Restaurant plan review and public health food establishment routing.",
        },
    )

    stats = {"queries": 0, "cache_hits": 0}
    source = engine._serper_claim_source(packaged_query, "code_section", "Los Angeles", "CA", stats)

    assert source is None
    assert stats["cache_hits"] == 0


def test_pa300_packaged_retail_source_search_rejects_restaurant_public_health_source(tmp_path, monkeypatch):
    packaged_query = (
        "Los Angeles CA packaged snack and bottled beverage retail tenant improvement with shelving, "
        "checkout, stockroom and restroom refresh; no food preparation, no restaurant, no kitchen, "
        "no hood, no grease code section permit"
    )
    monkeypatch.setattr(engine, "SERPER_CACHE_DB", str(tmp_path / "serper_cache.db"))
    monkeypatch.setattr(engine, "SERPER_API_KEY", "test-not-real")

    def fake_post(*args, **kwargs):
        return _FakeSerperResponse([
            {
                "title": "[PDF] AB 671 - Accelerated Restaurant Building Plan Review: California ...",
                "link": "https://publichealth.lacounty.gov/eh/docs/ab-671-plan-review-tenant-improvements-faq-en.pdf",
                "snippet": "Restaurant plan review and public health food establishment routing.",
            },
            {
                "title": "Permit and Inspection Report - Los Angeles Department of Building and Safety",
                "link": "https://www.ladbs.org/services/core-services/plan-check-permit/plan-check-permit-special-assistance",
                "snippet": "Building permit and plan check assistance for Los Angeles construction work.",
            },
        ])

    monkeypatch.setattr(engine, "_http_post_with_backoff", fake_post)

    stats = {"queries": 0, "cache_hits": 0}
    source = engine._serper_claim_source(packaged_query, "code_section", "Los Angeles", "CA", stats)
    source_blob = f"{source}".lower()

    assert source is not None
    assert "ladbs.org" in source["url"]
    for forbidden in ("publichealth.lacounty.gov", "ab 671", "restaurant", "food establishment"):
        assert forbidden not in source_blob


def test_pa300_source_cache_mismatch_is_symmetric_for_restaurant_query(tmp_path, monkeypatch):
    packaged_query = (
        "Los Angeles CA packaged snack and bottled beverage retail tenant improvement with shelving, "
        "checkout, stockroom and restroom refresh; no food preparation, no restaurant, no kitchen, "
        "no hood, no grease code section permit"
    )
    restaurant_query = (
        "Los Angeles CA restaurant tenant improvement with commercial kitchen, Type I hood, "
        "grease interceptor and food preparation code section permit"
    )
    monkeypatch.setattr(engine, "SERPER_CACHE_DB", str(tmp_path / "serper_cache.db"))
    monkeypatch.setattr(engine, "SERPER_API_KEY", "")
    engine._set_cached_serper_source(
        "Los Angeles",
        "CA",
        "code_section",
        packaged_query,
        {
            "title": "Permit and Inspection Report - Los Angeles Department of Building and Safety",
            "url": "https://www.ladbs.org/services/core-services/plan-check-permit/plan-check-permit-special-assistance",
            "snippet": "Building permit and plan check assistance for Los Angeles construction work.",
        },
    )

    stats = {"queries": 0, "cache_hits": 0}
    source = engine._serper_claim_source(restaurant_query, "code_section", "Los Angeles", "CA", stats)

    assert source is None
    assert stats["cache_hits"] == 0


def test_pa300_restaurant_positive_can_still_use_public_health_restaurant_source(tmp_path, monkeypatch):
    restaurant_query = (
        "Los Angeles CA restaurant tenant improvement with commercial kitchen, Type I hood, "
        "grease interceptor and food preparation code section permit"
    )
    monkeypatch.setattr(engine, "SERPER_CACHE_DB", str(tmp_path / "serper_cache.db"))
    monkeypatch.setattr(engine, "SERPER_API_KEY", "test-not-real")

    def fake_post(*args, **kwargs):
        return _FakeSerperResponse([
            {
                "title": "[PDF] AB 671 - Accelerated Restaurant Building Plan Review: California ...",
                "link": "https://publichealth.lacounty.gov/eh/docs/ab-671-plan-review-tenant-improvements-faq-en.pdf",
                "snippet": "Restaurant plan review and public health food establishment routing.",
            }
        ])

    monkeypatch.setattr(engine, "_http_post_with_backoff", fake_post)

    stats = {"queries": 0, "cache_hits": 0}
    source = engine._serper_claim_source(restaurant_query, "code_section", "Los Angeles", "CA", stats)

    assert source is not None
    assert "publichealth.lacounty.gov" in source["url"]
    assert "restaurant" in source["title"].lower()


def test_pa300_exact_cache_hit_scrubs_stale_ab671_source_metadata(tmp_path, monkeypatch):
    job = (
        "Retail tenant improvement for packaged snacks and bottled drinks only: shelving, checkout counter, "
        "lighting, no food prep, no commercial kitchen, no cooking, no hood, no grease. "
        "QA marker PA300-008; marker is not permit scope."
    )
    stale_result = {
        "permit_verdict": "YES",
        "confidence": "medium",
        "permit_name": "Commercial Building Permit — Tenant Improvement / Retail Interior Alteration",
        "permit_type": "Commercial Building Permit — Tenant Improvement / Retail Interior Alteration",
        "applying_office": "Los Angeles Department of Building and Safety (LADBS)",
        "apply_url": "https://www.ladbs.org/services/apply-for-a-permit",
        "sources": [],
        "code_section_source": {
            "url": "http://publichealth.lacounty.gov/eh/docs/faq/ab-671-plan-review-tenant-improvements-faq-en.pdf",
            "title": "[PDF] AB 671 - Accelerated Restaurant Building Plan Review: California ...",
            "verified_at": "2026-05-07",
            "source_type": "official",
        },
    }
    monkeypatch.setattr(engine, "CACHE_DB", str(tmp_path / "cache.db"))
    monkeypatch.setattr(engine, "SERPER_CACHE_DB", str(tmp_path / "serper_cache.db"))
    monkeypatch.setattr(engine, "SERPER_API_KEY", "")
    engine.init_cache()
    key = engine.cache_key(job, "Los Angeles", "CA", "commercial")
    engine.save_cache(key, job, "commercial", "Los Angeles", "CA", "90012", stale_result)

    out = engine.research_permit(job, "Los Angeles", "CA", "90012", use_cache=True, job_category="commercial")
    customer_visible = {k: v for k, v in out.items() if not str(k).startswith("_")}
    out_blob = f"{customer_visible}".lower()

    assert out.get("_cached") is True
    assert out.get("code_section_source") in ({}, None)
    for forbidden in ("ab 671", "accelerated restaurant", "publichealth.lacounty.gov", "restaurant building plan"):
        assert forbidden not in out_blob
    dropped = out.get("_sources_locality_dropped") or []
    assert any(d.get("field") == "code_section_source" and d.get("kind") == "source_scope" for d in dropped)


def test_pa300_restaurant_source_metadata_preserved_for_true_restaurant_scope():
    job = (
        "Los Angeles restaurant tenant improvement with commercial kitchen, Type I hood, "
        "grease interceptor and food preparation code section permit"
    )
    result = {
        "sources": [],
        "code_section_source": {
            "url": "http://publichealth.lacounty.gov/eh/docs/faq/ab-671-plan-review-tenant-improvements-faq-en.pdf",
            "title": "[PDF] AB 671 - Accelerated Restaurant Building Plan Review: California ...",
            "verified_at": "2026-05-07",
            "source_type": "official",
        },
    }

    engine.apply_source_locality_hard_block(result, "Los Angeles", "CA", job)

    assert result["code_section_source"]["title"].startswith("[PDF] AB 671")


def test_pa300_negated_office_source_metadata_rejects_restaurant_public_health():
    job = (
        "Los Angeles office tenant improvement for admin desks, conference rooms, and breakroom sink; "
        "no restaurant, no food prep, no commercial kitchen, no cooking, no hood, no grease."
    )
    result = {
        "sources": [],
        "code_section_source": {
            "url": "http://publichealth.lacounty.gov/eh/docs/faq/ab-671-plan-review-tenant-improvements-faq-en.pdf",
            "title": "[PDF] AB 671 - Accelerated Restaurant Building Plan Review: California ...",
            "verified_at": "2026-05-07",
            "source_type": "official",
        },
    }

    engine.apply_source_locality_hard_block(result, "Los Angeles", "CA", job)

    assert result.get("code_section_source") in ({}, None)
    dropped = result.get("_sources_locality_dropped") or []
    assert any(d.get("field") == "code_section_source" for d in dropped)


def test_pa300_non_code_claim_source_also_rejects_restaurant_leak_for_packaged_retail(tmp_path, monkeypatch):
    packaged_query = (
        "Los Angeles CA packaged snack and bottled beverage retail tenant improvement with shelving, "
        "checkout, stockroom and restroom refresh; no food preparation, no restaurant, no kitchen, "
        "no hood, no grease inspection process permit"
    )
    monkeypatch.setattr(engine, "SERPER_CACHE_DB", str(tmp_path / "serper_cache.db"))
    monkeypatch.setattr(engine, "SERPER_API_KEY", "test-not-real")

    def fake_post(*args, **kwargs):
        return _FakeSerperResponse([
            {
                "title": "Restaurant Plan Review Inspection Process - Public Health",
                "link": "https://publichealth.lacounty.gov/eh/business/food-plan-review.htm",
                "snippet": "Environmental health inspection routing for restaurants and food establishments.",
            },
            {
                "title": "Los Angeles Building Inspection Information",
                "link": "https://www.ladbs.org/services/core-services/inspection",
                "snippet": "Building inspections for permitted construction work in Los Angeles.",
            },
        ])

    monkeypatch.setattr(engine, "_http_post_with_backoff", fake_post)

    stats = {"queries": 0, "cache_hits": 0}
    source = engine._serper_claim_source(packaged_query, "inspection_process", "Los Angeles", "CA", stats)
    source_blob = f"{source}".lower()

    assert source is not None
    assert "ladbs.org" in source["url"]
    for forbidden in ("publichealth.lacounty.gov", "restaurant", "food establishment", "environmental health"):
        assert forbidden not in source_blob


def test_pa300_admin_office_source_search_rejects_medical_clinic_source(tmp_path, monkeypatch):
    office_query = (
        "Denver CO professional office tenant improvement for insurance administration, billing, "
        "private offices and records room; no clinic, no exam rooms, no x-ray, no medical gas code section permit"
    )
    monkeypatch.setattr(engine, "SERPER_CACHE_DB", str(tmp_path / "serper_cache.db"))
    monkeypatch.setattr(engine, "SERPER_API_KEY", "test-not-real")

    def fake_post(*args, **kwargs):
        return _FakeSerperResponse([
            {
                "title": "Medical Clinic Plan Review Requirements",
                "link": "https://www.denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Community-Planning-and-Development/Medical-Clinic-Plan-Review",
                "snippet": "Medical clinic, x-ray and medical gas plan review routing.",
            },
            {
                "title": "Commercial Tenant Finish Permits - Denver",
                "link": "https://www.denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Community-Planning-and-Development/Permits/Commercial-Construction",
                "snippet": "Commercial tenant finish permits for office construction.",
            },
        ])

    monkeypatch.setattr(engine, "_http_post_with_backoff", fake_post)

    stats = {"queries": 0, "cache_hits": 0}
    source = engine._serper_claim_source(office_query, "code_section", "Denver", "CO", stats)
    source_blob = f"{source}".lower()

    assert source is not None
    assert "commercial-construction" in source["url"].lower()
    for forbidden in ("medical clinic", "x-ray", "medical gas"):
        assert forbidden not in source_blob


def test_pa300_real_medical_clinic_can_still_use_medical_source(tmp_path, monkeypatch):
    medical_query = (
        "Denver CO medical clinic tenant improvement with exam rooms, x-ray room and medical gas coordination "
        "code section permit"
    )
    monkeypatch.setattr(engine, "SERPER_CACHE_DB", str(tmp_path / "serper_cache.db"))
    monkeypatch.setattr(engine, "SERPER_API_KEY", "test-not-real")

    def fake_post(*args, **kwargs):
        return _FakeSerperResponse([
            {
                "title": "Medical Clinic Plan Review Requirements",
                "link": "https://www.denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Community-Planning-and-Development/Medical-Clinic-Plan-Review",
                "snippet": "Medical clinic, x-ray and medical gas plan review routing.",
            }
        ])

    monkeypatch.setattr(engine, "_http_post_with_backoff", fake_post)

    stats = {"queries": 0, "cache_hits": 0}
    source = engine._serper_claim_source(medical_query, "code_section", "Denver", "CO", stats)

    assert source is not None
    assert "medical-clinic-plan-review" in source["url"].lower()


def test_pa300_cache_query_normalization_uses_case_and_whitespace_insensitive_match(tmp_path, monkeypatch):
    stored_query = "Los Angeles CA restaurant tenant improvement code section permit"
    lookup_query = "  LOS   ANGELES   CA   RESTAURANT   TENANT   IMPROVEMENT   CODE   SECTION   PERMIT  "
    monkeypatch.setattr(engine, "SERPER_CACHE_DB", str(tmp_path / "serper_cache.db"))
    monkeypatch.setattr(engine, "SERPER_API_KEY", "")
    engine._set_cached_serper_source(
        "Los Angeles",
        "CA",
        "code_section",
        stored_query,
        {
            "title": "Los Angeles Restaurant Plan Review",
            "url": "https://publichealth.lacounty.gov/eh/docs/restaurant-plan-review.pdf",
            "snippet": "Restaurant plan review guidance.",
        },
    )

    stats = {"queries": 0, "cache_hits": 0}
    source = engine._serper_claim_source(lookup_query, "code_section", "Los Angeles", "CA", stats)

    assert source is not None
    assert source["cache"] == "hit"
    assert stats["cache_hits"] == 1


def test_pa300_cache_single_slot_semantics_are_safe_on_query_overwrite(tmp_path, monkeypatch):
    first_query = "Los Angeles CA restaurant tenant improvement code section permit"
    second_query = "Los Angeles CA office tenant improvement code section permit"
    monkeypatch.setattr(engine, "SERPER_CACHE_DB", str(tmp_path / "serper_cache.db"))
    monkeypatch.setattr(engine, "SERPER_API_KEY", "")
    engine._set_cached_serper_source(
        "Los Angeles",
        "CA",
        "code_section",
        first_query,
        {"title": "First Source", "url": "https://www.lacity.gov/first", "snippet": "First source."},
    )
    engine._set_cached_serper_source(
        "Los Angeles",
        "CA",
        "code_section",
        second_query,
        {"title": "Second Source", "url": "https://www.lacity.gov/second", "snippet": "Second source."},
    )

    stats = {"queries": 0, "cache_hits": 0}
    stale_source = engine._serper_claim_source(first_query, "code_section", "Los Angeles", "CA", stats)

    assert stale_source is None
    assert stats["cache_hits"] == 0


def test_pa300_packaged_retail_scope_filter_rejects_public_health_food_code_url():
    packaged_scope = (
        "Los Angeles CA retail tenant improvement for packaged snacks and bottled beverages only; "
        "no food preparation, no restaurant, no kitchen, no hood, no grease."
    )
    source = {
        "title": "California Retail Food Code - Food Program",
        "url": "https://www.slocounty.ca.gov/Departments/Health-Agency/Public-Health/Environmental-Health/Forms-Documents/Food-Program/California-Retail-Food-Code.aspx",
        "snippet": "Retail food code and public health food program guidance.",
    }

    assert engine._source_incompatible_with_scope_text(source, packaged_scope, "code_section") is True


def test_pa300_scope_filter_removes_food_code_url_from_customer_sources():
    job = (
        "Los Angeles CA retail tenant improvement for packaged snacks and bottled beverages only; "
        "no food preparation, no restaurant, no kitchen, no hood, no grease."
    )
    food_code_url = (
        "https://www.slocounty.ca.gov/Departments/Health-Agency/Public-Health/"
        "Environmental-Health/Forms-Documents/Food-Program/California-Retail-Food-Code.aspx"
    )
    ladbs_url = "https://www.ladbs.org/services/core-services/plan-check-permit/plan-check-permit-special-assistance"
    result = {
        "applying_office": "Los Angeles Department of Building and Safety",
        "sources": [food_code_url, ladbs_url],
    }

    engine.apply_source_locality_hard_block(result, "Los Angeles", "CA", job)

    assert food_code_url not in result["sources"]
    assert ladbs_url in result["sources"]
    dropped_blob = str(result.get("_sources_locality_dropped") or "").lower()
    assert "source_scope" in dropped_blob


def test_pa300_packaged_retail_negation_phrase_variants_trigger_source_filter():
    source = {
        "title": "AB 671 Accelerated Restaurant Building Plan Review",
        "url": "https://publichealth.lacounty.gov/eh/docs/ab-671-plan-review-tenant-improvements-faq-en.pdf",
        "content": "Restaurant food establishment plan review.",
    }
    scope_variants = [
        "Los Angeles convenience store packaged-only grocery tenant improvement without food preparation",
        "Los Angeles dry-goods retail TI, non-restaurant, shelving and checkout only",
        "Los Angeles mercantile tenant improvement, not a restaurant, no kitchen",
    ]

    for scope in scope_variants:
        assert engine._source_incompatible_with_scope_text(source, scope)


def test_pa300_mixed_use_scope_does_not_overfilter_restaurant_or_medical_sources():
    restaurant_source = {
        "title": "Restaurant Plan Review",
        "url": "https://publichealth.lacounty.gov/eh/docs/restaurant-plan-review.pdf",
        "content": "Restaurant plan review.",
    }
    medical_source = {
        "title": "Medical Clinic Plan Review",
        "url": "https://www.denvergov.org/medical-clinic-plan-review",
        "content": "Medical clinic x-ray and medical gas routing.",
    }

    assert not engine._source_incompatible_with_scope_text(
        restaurant_source,
        "Los Angeles mixed-use retail and cafe tenant improvement with food preparation",
    )
    assert not engine._source_incompatible_with_scope_text(
        medical_source,
        "Denver medical clinic tenant improvement with exam rooms and x-ray",
    )


def test_pa300_original_incident_prose_does_not_attach_public_health_restaurant_source(tmp_path, monkeypatch):
    job = (
        "Los Angeles CA packaged snack and bottled beverage retail tenant improvement with shelving, "
        "checkout, stockroom and restroom refresh; no food preparation, no restaurant, no commercial kitchen, "
        "no cooking, no hood, no grease"
    )
    result = {"permit_type": "Commercial Tenant Improvement"}
    monkeypatch.setattr(engine, "SERPER_CACHE_DB", str(tmp_path / "serper_cache.db"))
    monkeypatch.setattr(engine, "SERPER_API_KEY", "test-not-real")

    def fake_post(*args, **kwargs):
        return _FakeSerperResponse([
            {
                "title": "[PDF] AB 671 - Accelerated Restaurant Building Plan Review: California ...",
                "link": "https://publichealth.lacounty.gov/eh/docs/ab-671-plan-review-tenant-improvements-faq-en.pdf",
                "snippet": "Restaurant plan review and public health food establishment routing.",
            },
            {
                "title": "Permit and Inspection Report - Los Angeles Department of Building and Safety",
                "link": "https://www.ladbs.org/services/core-services/plan-check-permit/plan-check-permit-special-assistance",
                "snippet": "Building permit and plan check assistance for Los Angeles construction work.",
            },
        ])

    monkeypatch.setattr(engine, "_http_post_with_backoff", fake_post)

    claim_results, stats = engine._serper_claim_sources_sequential(job, "Los Angeles", "CA", result)
    attached = engine._attach_serper_claim_results(result, claim_results, stats)
    customer_blob = f"{result}".lower()

    assert attached > 0
    assert "ladbs.org" in customer_blob
    for forbidden in ("publichealth.lacounty.gov", "ab 671", "restaurant building plan", "food establishment"):
        assert forbidden not in customer_blob


def test_law_firm_office_coffee_bar_does_not_return_restaurant_or_health_hidden_triggers():
    job = (
        "law firm office tenant improvement with private offices, conference rooms, reception, "
        "records storage and break room coffee bar; no medical use, no restaurant, "
        "no clinic, no kitchen cooking, no hood."
    )

    out = _apply_core_layers(job, "Dallas", "TX")
    gated = server.apply_permitiq_quality_gate(out, job, "Dallas", "TX")
    server.build_apply_path(gated, job, "Dallas", "TX")
    blob = _trigger_blob(gated.get("hidden_triggers", [])) + " | " + _customer_surface_blob(gated)
    apply_blob = " | ".join(gated.get("apply_path", {}).get("steps", [])).lower()

    assert out["_primary_scope"] == "commercial_office_ti"
    assert "mechanical" not in _required_families(out)
    for forbidden in ("health department", "food establishment", "food-establishment", "clinic", "exam room", "x-ray", "medical gas"):
        assert forbidden not in blob
        assert forbidden not in apply_blob


def test_hidden_trigger_public_payload_excludes_internal_fired_by_regexes():
    job = "restaurant tenant improvement with Type I hood, fryer, seating, and grease interceptor"

    triggers = detect_hidden_triggers(job, "Los Angeles", "CA", "commercial_restaurant_ti", {})

    assert triggers
    assert any(trigger.get("likely_required_actions") for trigger in triggers)
    assert any(trigger.get("title") for trigger in triggers)
    assert all("fired_by" not in trigger for trigger in triggers)
    assert all("regions" not in trigger and "not_regions" not in trigger for trigger in triggers)


def test_cached_hidden_trigger_payload_scrubs_internal_matcher_metadata():
    cached = {
        "hidden_triggers": [
            {
                "id": "commercial_change_of_occupancy_b_to_a2_or_m_sprinkler_general",
                "title": "Change of occupancy review",
                "fired_by": [r"\b(office|warehouse)\s+to\s+(restaurant|retail)\b"],
                "regions": ["ca"],
                "not_regions": ["outside_ca"],
            }
        ]
    }

    engine.scrub_hidden_trigger_internal_metadata(cached)

    assert cached["hidden_triggers"][0]["title"] == "Change of occupancy review"
    assert "fired_by" not in cached["hidden_triggers"][0]
    assert "regions" not in cached["hidden_triggers"][0]
    assert "not_regions" not in cached["hidden_triggers"][0]
    assert cached["_hidden_trigger_metadata_sanitized"]


def test_pc10_office_coffee_trap_chicago_no_required_mechanical_without_hvac():
    job = (
        "PC10 QA marker: Chicago law office tenant improvement with private offices, conference room, "
        "reception, records storage and break-room coffee bar; no cooking, no hood, no restaurant, "
        "no clinic, no HVAC, no mechanical work stated."
    )

    out = _apply_core_layers(job, "Chicago", "IL")

    assert out["_primary_scope"] == "commercial_office_ti"
    assert "building" in _required_families(out)
    assert "mechanical" not in _required_families(out)
    required_blob = _required_permit_blob(out)
    for forbidden in ("mechanical permit", "health department", "food establishment", "clinic"):
        assert forbidden not in required_blob


def test_pc23_san_jose_office_breakroom_kitchenette_filters_grease_from_downstream_surfaces():
    job = (
        "PC23 QA marker: San Jose CA commercial office tenant improvement for a software office "
        "with kitchenette/breakroom sink, microwave, refrigerator, and coffee machine; no restaurant, "
        "no food service, no commercial kitchen, no cooking line, no Type I hood, no fryer, "
        "no ANSUL, and no grease interceptor."
    )
    out = _apply_core_layers(job, "San Jose", "CA")
    out.update({
        "what_to_bring": ["Office TI plans, breakroom sink plumbing sheet, and grease interceptor sizing sheet."],
        "pro_tips": ["Keep the scope labeled as office breakroom sink and kitchenette only."],
        "inspections": [
            {
                "stage": "Grease interceptor final",
                "title": "Grease interceptor / FOG final inspection",
                "description": "Inspector verifies the office breakroom sink and grease interceptor installation before final.",
                "timing": "Final inspection",
            }
        ],
        "claim_citations": [
            {
                "field": "inspections",
                "claim": "Likely inspections",
                "value": "Final plumbing inspection may verify grease interceptor access.",
                "source_title": "San Jose FOG requirements",
                "quoted_snippet": "Grease interceptor approval is required for food service.",
            }
        ],
        "companion_permits": [
            {"permit_type": "FOG / industrial waste / sewer approval", "reason": "Office breakroom sink was mistaken for commercial food prep."},
            {"permit_type": "Electrical Permit — Commercial Tenant Improvement", "reason": "Office lighting and receptacles."},
        ],
        "hidden_triggers": [
            {
                "id": "restaurant_grease_interceptor_fog_review",
                "title": "Grease interceptor / FOG review may be required",
                "why_it_matters": "FOG requirements are often enforced by sewer agencies.",
                "companion_permits": ["FOG / industrial waste / sewer approval, if required"],
            }
        ],
    })

    gated = server.apply_permitiq_quality_gate(out, job, "San Jose", "CA")
    pre_citation_blob = " | ".join(str(c) for c in gated.get("claim_citations", [])).lower()
    assert "grease" not in pre_citation_blob
    assert "fog" not in pre_citation_blob
    server.build_claim_citations(gated)
    server.build_apply_path(gated, job, "San Jose", "CA")
    blob = (
        _customer_surface_blob(gated)
        + " | " + " | ".join(str(i) for i in gated.get("inspections", [])).lower()
        + " | " + " | ".join(str(c) for c in gated.get("claim_citations", [])).lower()
        + " | " + _companion_blob(gated)
        + " | " + _trigger_blob(gated.get("hidden_triggers", []))
    )

    assert gated["_primary_scope"] == "commercial_office_ti"
    assert "building" in _required_families(gated)
    assert "plumbing" in _required_families(gated)
    assert "breakroom sink" in blob or "kitchenette" in blob
    for forbidden in ("grease interceptor", "grease", "fog", "food establishment", "health department", "commercial kitchen", "type i hood", "ansul"):
        assert forbidden not in blob


def test_general_office_breakroom_kitchenette_negative_filters_restaurant_language():
    job = (
        "commercial office tenant improvement with staff kitchenette, breakroom sink, refrigerator, "
        "microwave, coffee maker, partitions, lighting, and data cabling; not a restaurant, "
        "no food service, no commercial kitchen, no hood, and no grease interceptor."
    )
    out = _apply_core_layers(job, "Dallas", "TX")
    out.update({
        "permits_required_logic": out.get("permits_required_logic", []) + [{
            "permit_type": "Plumbing Permit — Commercial Tenant Improvement",
            "included_because": "Breakroom sink is in scope; grease interceptor / FOG review is not required for this office kitchenette.",
            "scope_trigger": "model leakage",
        }],
        "inspections": ["Final inspection includes sink trap, venting, and grease interceptor check."],
        "what_to_bring": ["Bring FOG interceptor sizing and FOG wastewater worksheet."],
        "companion_permits": [{"permit_type": "FOG / industrial waste / sewer approval", "reason": "Office kitchenette was mistaken for food service."}],
        "hidden_triggers": [{"id": "restaurant_grease_interceptor_fog_review", "title": "FOG requirements", "why_it_matters": "FOG / industrial waste / sewer approval may be required."}],
        "pro_tips": ["Label the space as office breakroom only, not food service."],
        "zoning_hoa_flag": (
            "For a commercial office tenant improvement, zoning and HOA issues are usually limited to the lease, "
            "building rules, and any change-of-use or occupancy limits. The stated kitchenette scope does not by "
            "itself trigger a grease interceptor or restaurant zoning review."
        ),
    })

    gated = server.apply_permitiq_quality_gate(out, job, "Dallas", "TX")
    server.build_apply_path(gated, job, "Dallas", "TX")
    blob = (
        _customer_surface_blob(gated)
        + " | " + " | ".join(str(i) for i in gated.get("inspections", [])).lower()
        + " | " + _companion_blob(gated)
        + " | " + _trigger_blob(gated.get("hidden_triggers", []))
    )

    assert gated["_primary_scope"] == "commercial_office_ti"
    assert "office" in blob
    for forbidden in ("grease interceptor", "grease", "fog", "food establishment", "health department", "commercial kitchen", "food service", "restaurant zoning"):
        assert forbidden not in blob
    assert "zoning" in blob or "hoa" in blob


def test_office_breakroom_customer_text_sanitizer_rewrites_negated_restaurant_mistake():
    job = (
        "Commercial office tenant improvement with staff kitchenette/breakroom sink, microwave, "
        "refrigerator, lighting, and data cabling; no restaurant, no food service, "
        "no commercial kitchen, no hood, and no grease interceptor."
    )
    result = _apply_core_layers(job, "Los Angeles", "CA")
    result["common_mistakes"] = [
        "Missing accessible route details for the breakroom sink.",
        "Assuming no permit is needed because the kitchenette is not a restaurant — the sink and electrical work still trigger permits",
    ]
    result["inspections"] = [
        {
            "stage": "Final Building / Plumbing / Electrical Inspection",
            "description": "Inspector checks that the breakroom matches the approved commercial TI scope with no restaurant-style equipment installed.",
            "timing": "Final inspection",
        }
    ]
    result["claim_citations"] = [
        {
            "field": "inspections",
            "claim": "Likely inspections",
            "value": "Inspector checks that the breakroom has no restaurant-style equipment installed.",
            "source_title": "Official source",
        }
    ]

    engine.sanitize_non_food_office_breakroom_text(result, job)

    blob = " | ".join(str(x) for x in result.get("common_mistakes", [])).lower()
    full_blob = str(result).lower()
    assert "restaurant" not in blob
    assert "food service" not in blob
    assert "commercial kitchen" not in blob
    assert "grease" not in blob
    assert "breakroom sink" in blob
    assert "not a restaurant" not in full_blob
    assert "restaurant-style" not in full_blob
    assert "restaurant" not in full_blob
    assert "food service" not in full_blob
    assert "commercial kitchen" not in full_blob
    assert "grease interceptor" not in full_blob


def test_restaurant_customer_text_sanitizer_does_not_touch_true_restaurant_scope():
    job = "restaurant tenant improvement with commercial kitchen, Type I hood, fryer, and grease interceptor"
    result = {
        "_primary_scope": "commercial_restaurant",
        "common_mistakes": ["Forgetting health department restaurant plan review and grease interceptor sizing."],
    }

    engine.sanitize_non_food_office_breakroom_text(result, job)

    blob = " | ".join(result["common_mistakes"]).lower()
    assert "restaurant" in blob
    assert "grease interceptor" in blob


def test_restaurant_fog_interceptor_positive_survives_office_breakroom_filter():
    job = (
        "restaurant tenant improvement with full commercial kitchen, Type I hood, fryer, griddle, "
        "ANSUL suppression, three-compartment sink, mop sink, FOG interceptor, and FOG approval."
    )
    out = _apply_core_layers(job, "San Jose", "CA")
    out.update({
        "inspections": ["Plumbing final verifies FOG interceptor approval before opening."],
        "what_to_bring": ["Bring FOG interceptor sizing and commercial kitchen plumbing sheets."],
    })

    gated = server.apply_permitiq_quality_gate(out, job, "San Jose", "CA")
    blob = _customer_surface_blob(gated) + " | " + " | ".join(str(i) for i in gated.get("inspections", [])).lower()

    assert gated["_primary_scope"] == "commercial_restaurant"
    assert "grease_interceptor" in {a["key"] for a in gated["_fee_floor_components"]["trigger_adders"]}
    assert "fog" in _trigger_blob(gated.get("hidden_triggers", []))
    assert "fog" in blob
    assert "commercial kitchen" in blob or "food establishment" in blob or "health department" in blob


def test_pr24_negative_trap_customer_prose_does_not_repeat_absent_restaurant_or_medical_terms():
    cases = [
        (
            "restaurant dining-room cosmetic refresh: paint, flooring, decorative lighting, tables and chairs only; "
            "no kitchen work, no cooking equipment, no hood, no fryer, no grease interceptor, no fire suppression modification",
            "Los Angeles",
            "CA",
            ["ansul", "grease interceptor", "type i hood", "commercial kitchen", "health department food establishment"],
        ),
        (
            "coffee shop tenant improvement with espresso bar, hand sink, undercounter refrigerator, pastry display, seating and restroom refresh; "
            "no cooking line, no Type I hood, no grease fryer, no ANSUL changes",
            "Orlando",
            "FL",
            ["type i hood", "grease interceptor", "ansul"],
        ),
        (
            "bar tenant improvement with serving counter, beer taps, glass washer, seating, restrooms and occupancy layout; "
            "no commercial kitchen, no fryer, no Type I hood, no grease interceptor work",
            "Miami",
            "FL",
            ["type i hood", "grease interceptor", "commercial kitchen"],
        ),
        (
            "dental insurance administration office remodel with cubicles, training room and records storage only; "
            "no dental chairs, no treatment rooms, no exam rooms, no x-ray equipment, no medical gas",
            "Irvine",
            "CA",
            ["treatment room", "x-ray", "medical gas", "patient care"],
        ),
        (
            "law firm office tenant improvement with private offices, conference rooms, reception, records storage and break room coffee bar; "
            "no medical use, no restaurant, no clinic, no kitchen cooking, no hood",
            "Chicago",
            "IL",
            ["hood", "grease interceptor", "health department", "clinic", "exam room", "x-ray", "medical gas"],
        ),
        (
            "packaged snack and beverage retail tenant improvement with shelving, checkout, stockroom, restroom refresh; "
            "no food preparation, no restaurant, no kitchen, no hood, no grease work",
            "Phoenix",
            "AZ",
            ["hood", "ansul", "grease interceptor", "food establishment", "commercial kitchen"],
        ),
    ]

    for job, city, state, forbidden_terms in cases:
        out = _apply_core_layers(job, city, state)
        out.update({
            "job_summary": f"Model echo: {job}.",
            "summary": f"Model echo: {job}.",
            "description": f"Model echo: {job}.",
            "recommendation": f"Model echo: {job}.",
            "next_steps": [f"Model echo: {job} before filing."],
            "quality_warnings": [f"Model echo: {job}."] + out.get("quality_warnings", []),
        })
        gated = server.apply_permitiq_quality_gate(out, job, city, state)
        server.build_apply_path(gated, job, city, state)
        blob = _customer_surface_blob(gated)
        assert any(expected in blob for expected in ("restaurant", "coffee", "bar", "dental", "law firm", "office", "retail", "tenant improvement"))
        assert "building permit" in blob or "tenant improvement" in blob or "interior alteration" in blob
        for forbidden in forbidden_terms:
            assert forbidden not in blob


def test_pr24_lab_fume_hood_positive_scope_survives_restaurant_hood_hygiene():
    job = (
        "office research/admin suite adding a small laboratory fume hood for non-food sample preparation "
        "and chemical ventilation; no cooking hood, no restaurant, no food prep, no grease, no clinic"
    )
    out = _apply_core_layers(job, "Portland", "OR")
    out.update({
        "job_summary": "Office research/admin suite with laboratory fume hood and chemical ventilation; no cooking hood or restaurant scope.",
        "pro_tips": ["Include laboratory fume hood ventilation details and mechanical drawings."],
        "quality_warnings": [],
    })

    gated = server.apply_permitiq_quality_gate(out, job, "Portland", "OR")
    blob = _customer_surface_blob(gated)

    assert "fume hood" in blob
    assert "ventilation" in blob or "mechanical" in blob
    for forbidden in ("grease interceptor", "food establishment", "commercial kitchen", "ansul", "clinic"):
        assert forbidden not in blob


def test_professional_office_for_medical_billing_company_is_not_medical_clinic():
    job = (
        "2,400 sf professional office TI for medical billing and insurance administration company; "
        "ordinary office desks and conference rooms only, no clinic, no exam rooms, no patient care, "
        "no x-ray, no medical gas, no treatment rooms."
    )

    assert engine.detect_primary_scope(job) == "commercial_office_ti"
    assert _vertical_for_job(job) == "office_ti"
    out = _apply_core_layers(job, "Boston", "MA")
    assert out["_primary_scope"] == "commercial_office_ti"
    assert out.get("occupancy_analysis") is None
    medical_blob = _companion_blob(out) + " | " + _trigger_blob(out["hidden_triggers"])
    assert "clinic" not in medical_blob
    assert "exam room" not in medical_blob
    assert "medical gas" not in medical_blob
    assert "x-ray" not in medical_blob


def test_medical_billing_admin_office_does_not_get_clinic_checklist_or_warning():
    job = (
        "2,400 sf professional office TI for medical billing and insurance administration company; "
        "ordinary office desks and conference rooms only, no clinic, no exam rooms, no patient care, "
        "no x-ray, no medical gas, no treatment rooms."
    )

    checklist = _checklist_blob(job, "Boston", "MA")
    for forbidden in ("commercial clinic", "exam-room", "medical gas", "x-ray", "radiology"):
        assert forbidden not in checklist

    gated = server.apply_permitiq_quality_gate(
        {
            "_primary_scope": "commercial_office_ti",
            "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration"}],
            "companion_permits": [
                {"permit_type": "Electrical Permit — Commercial Tenant Improvement"},
                {"permit_type": "Mechanical Permit — Commercial Tenant Improvement"},
                {"permit_type": "Plumbing Permit — Commercial Tenant Improvement"},
            ],
            "quality_warnings": [],
            "pro_tips": [
                "Keep the scope clearly non-clinical in the permit narrative: 'office administration only, no patient care, no exam rooms, no medical gas' — that helps avoid healthcare review confusion",
                "Use the city's commercial TI permit category for partitions and lighting.",
            ],
            "sources": [{"url": "https://example.com/permit", "title": "Official source"}],
            "confidence": "medium",
        },
        job,
        "Boston",
        "MA",
    )
    warning_blob = " | ".join(gated.get("quality_warnings", [])).lower()
    tip_blob = " | ".join(gated.get("pro_tips", [])).lower()
    assert "medical gas" not in warning_blob
    assert "exam room" not in tip_blob
    assert "medical gas" not in tip_blob
    assert "commercial ti permit" in tip_blob


def test_clinic_with_exam_rooms_but_no_medgas_or_xray_filters_item_specific_checklist_and_warnings():
    job = (
        "3,000 sf medical clinic TI with exam rooms, patient check-in, accessible restroom, "
        "and HVAC ventilation updates; no x-ray, no radiology and no medical gas."
    )

    checklist = _checklist_blob(job, "Boston", "MA")
    assert "commercial clinic" in checklist
    assert "medical gas" not in checklist
    assert "x-ray" not in checklist
    assert "radiology" not in checklist

    gated = server.apply_permitiq_quality_gate(
        {
            "_primary_scope": "commercial_medical_clinic_ti",
            "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Medical Clinic Interior Alteration"}],
            "companion_permits": [
                {"permit_type": "Electrical Permit — Commercial Tenant Improvement"},
                {"permit_type": "Mechanical Permit — Commercial Tenant Improvement"},
                {"permit_type": "Plumbing Permit — Commercial Tenant Improvement"},
                {"permit_type": "Fire Alarm / Fire Sprinkler Permit — Commercial Tenant Improvement"},
                {"permit_type": "Accessibility review"},
            ],
            "quality_warnings": [],
            "sources": [{"url": "https://example.com/permit", "title": "Official source"}],
            "confidence": "medium",
        },
        job,
        "Boston",
        "MA",
    )
    assert "medical gas" not in " | ".join(gated.get("quality_warnings", [])).lower()


def test_real_medical_clinic_still_triggers_clinic_rulebook():
    job = (
        "4,000 sf medical clinic TI with exam rooms, hand sinks, patient check-in, x-ray room, "
        "medical gas outlets, HVAC ventilation updates, ADA restroom and fire alarm changes."
    )

    assert engine.detect_primary_scope(job) == "commercial_medical_clinic_ti"
    assert _vertical_for_job(job) == "medical_clinic_ti"
    out = _apply_core_layers(job, "Cambridge", "MA")
    assert out["_primary_scope"] == "commercial_medical_clinic_ti"
    assert out.get("occupancy_analysis", {}).get("applies") is True
    blob = _companion_blob(out) + " | " + " | ".join(out.get("watch_out", [])).lower()
    assert "health" in blob or "clinic" in blob
    assert "medical gas" in blob or "x-ray" in blob


def test_residential_bathroom_remodel_has_no_commercial_warning_leakage():
    job = "residential bathroom remodel replacing vanity, tub and tile in single-family home"
    out = _apply_core_layers(job, "Orlando", "FL")

    assert out["_primary_scope"] == "residential"
    assert out["_fee_floor_check"] == "residential_no_override"
    blob = " | ".join(
        str(x) for key in ("watch_out", "common_mistakes", "pro_tips", "inspections", "companion_permits") for x in out.get(key, [])
    ).lower()
    for forbidden in ("commercial", "tenant improvement", "restaurant", "food establishment", "medical clinic", "health-care", "low-voltage"):
        assert forbidden not in blob


def test_short_restaurant_tokens_do_not_match_inside_unrelated_words():
    cases = [
        "2,000 sf barber shop tenant improvement with reception, stations, retail shelving and lighting; no food service or kitchen work.",
        "4,500 sf indoor shooting range tenant improvement with office, classroom and acoustic partitions; no kitchen, hood or grease work.",
        "1,200 sf neighborhood market office buildout with back-office partitions and lighting only; no kitchen equipment.",
        "2,800 sf theater tenant improvement with stage fog machine and lighting controls; no food service, no kitchen, no grease interceptor.",
    ]

    for job in cases:
        out = _apply_core_layers(job, "Dallas", "TX")
        blob = _trigger_blob(out["hidden_triggers"]) + " | " + _companion_blob(out) + " | " + out["fee_range"].lower()
        adder_keys = {a["key"] for a in (out.get("_fee_floor_components") or {}).get("trigger_adders", [])}
        assert out["_primary_scope"] != "commercial_restaurant"
        assert {"hood_fire_suppression", "grease_interceptor"}.isdisjoint(adder_keys)
        assert "food establishment" not in blob
        assert "health department" not in blob


def test_healthcare_adjacent_admin_office_stays_office_without_clinical_scope():
    job = (
        "2,200 sf healthcare-adjacent corporate office TI for insurance administration, "
        "desks and conference rooms only; no clinic, no exam rooms, no patient care, "
        "no x-ray and no medical gas."
    )

    assert engine.detect_primary_scope(job) == "commercial_office_ti"
    assert _vertical_for_job(job) == "office_ti"
    out = _apply_core_layers(job, "Boston", "MA")
    assert out["_primary_scope"] == "commercial_office_ti"
    assert out.get("occupancy_analysis") is None
    medical_blob = _companion_blob(out) + " | " + _trigger_blob(out["hidden_triggers"])
    assert "clinic" not in medical_blob
    assert "exam room" not in medical_blob
    assert "medical gas" not in medical_blob
    assert "x-ray" not in medical_blob


def test_post_position_negation_suppresses_restaurant_fee_adders():
    job = (
        "1,900 sf existing restaurant front-of-house refresh with fryer not included in scope, "
        "hood outside the scope, and grease interceptor excluded; paint, flooring and seating only."
    )

    out = _apply_core_layers(job, "Chicago", "IL")
    adder_keys = {a["key"] for a in (out.get("_fee_floor_components") or {}).get("trigger_adders", [])}
    assert "hood_fire_suppression" not in adder_keys
    assert "grease_interceptor" not in adder_keys


def test_fog_as_stage_or_glass_word_does_not_trigger_grease_interceptor():
    cases = [
        "2,800 sf theater tenant improvement with stage fog machine and lighting controls; no food service or kitchen.",
        "4,000 sf auditorium tenant improvement with stage fog approval forms for theatrical effects and lighting controls; no food service, no kitchen, no hood, no grease interceptor.",
        "3,200 sf event venue TI with stage fog review for smoke effects and no restaurant, no food prep, no kitchen plumbing.",
        "3,000 sf office TI with fog-resistant glass partitions; no food service, no kitchen, no hood and no grease interceptor.",
    ]

    for job in cases:
        out = _apply_core_layers(job, "Dallas", "TX")
        adder_keys = {a["key"] for a in (out.get("_fee_floor_components") or {}).get("trigger_adders", [])}
        blob = _trigger_blob(out["hidden_triggers"]) + " | " + _companion_blob(out) + " | " + out["fee_range"].lower()
        assert "grease_interceptor" not in adder_keys
        assert "grease" not in blob


def test_commercial_kitchen_with_negated_hood_and_grease_does_not_create_hidden_triggers():
    job = (
        "restaurant TI with commercial kitchen equipment storage and prep tables only; "
        "no Type I hood, no fryer, no griddle, no ANSUL, no grease interceptor, "
        "and no grease duct work."
    )

    out = _apply_core_layers(job, "Chicago", "IL")
    assert out["_primary_scope"] == "commercial_restaurant"
    blob = _trigger_blob(out["hidden_triggers"]) + " | " + _companion_blob(out) + " | " + out["fee_range"].lower()
    adder_keys = {a["key"] for a in (out.get("_fee_floor_components") or {}).get("trigger_adders", [])}
    assert {"hood_fire_suppression", "grease_interceptor"}.isdisjoint(adder_keys)
    assert "hood" not in blob
    assert "ansul" not in blob
    assert "grease" not in blob


def test_commercial_kitchen_with_negated_hood_and_grease_does_not_get_kitchen_checklist():
    job = (
        "restaurant TI with commercial kitchen equipment storage and prep tables only; "
        "no Type I hood, no fryer, no griddle, no ANSUL, no grease interceptor, "
        "and no grease duct work."
    )

    checklist = _checklist_blob(job, "Chicago", "IL")
    for forbidden in ("type i commercial exhaust hood", "ansul", "grease interceptor", "hood exhaust"):
        assert forbidden not in checklist

    out = _apply_core_layers(job, "Chicago", "IL")
    permit_blob = " | ".join(str(p) for p in out.get("permits_required", []) + out.get("permits_required_logic", [])).lower()
    assert "grease-interceptor" not in permit_blob

    gated = server.apply_permitiq_quality_gate(
        {
            "_primary_scope": "commercial_restaurant",
            "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Restaurant Interior Alteration"}],
            "companion_permits": [
                {"permit_type": "Electrical Permit — Commercial Tenant Improvement"},
                {"permit_type": "Mechanical Permit — Commercial Tenant Improvement"},
                {"permit_type": "Plumbing Permit — Commercial Tenant Improvement"},
                {"permit_type": "Health Department / Food Establishment Review"},
            ],
            "common_mistakes": ["Assuming no hood means no permit — the interior alteration still needs a building permit"],
            "pro_tips": [
                "Keep the scope explicit: no Type I hood, no grease duct, no grease interceptor, no ANSUL. That helps avoid being bounced into fire suppression or plumbing review unnecessarily",
                "Confirm whether prep tables and storage affect health review.",
            ],
            "quality_warnings": [],
            "sources": [{"url": "https://example.com/permit", "title": "Official source"}],
            "confidence": "medium",
        },
        job,
        "Chicago",
        "IL",
    )
    surface_blob = " | ".join(gated.get("common_mistakes", []) + gated.get("pro_tips", [])).lower()
    assert "hood" not in surface_blob
    assert "ansul" not in surface_blob
    assert "grease" not in surface_blob
    assert "prep tables" in surface_blob


def test_restaurant_dishwasher_without_hood_or_grease_filters_item_specific_checklist():
    job = (
        "restaurant TI with commercial dishwasher and prep sink plumbing; "
        "no Type I hood, no fryer, no griddle, no ANSUL, and no grease interceptor."
    )

    checklist = _checklist_blob(job, "Chicago", "IL")
    assert "dishwasher" in checklist or "indirect waste" in checklist
    for forbidden in ("type i commercial exhaust hood", "ansul", "grease interceptor", "hood exhaust"):
        assert forbidden not in checklist

    out = _apply_core_layers(job, "Chicago", "IL")
    permit_blob = " | ".join(str(p) for p in out.get("permits_required", []) + out.get("permits_required_logic", [])).lower()
    assert "grease-interceptor" not in permit_blob


def _customer_surface_blob(result):
    fields = []
    for key in (
        "what_to_bring", "common_mistakes", "pro_tips", "watch_out",
        "quality_warnings", "permits_required_logic", "permits_required", "apply_path",
        "zoning_hoa_flag",
    ):
        value = result.get(key)
        if isinstance(value, list):
            fields.extend(str(item) for item in value)
        elif isinstance(value, dict):
            fields.append(str(value))
        elif value:
            fields.append(str(value))
    return " | ".join(fields).lower()


def test_negative_restaurant_scope_filters_what_to_bring_and_advice_surfaces():
    job = (
        "restaurant TI with prep-table storage and interior finishes only; no Type I hood, "
        "no fryer, no griddle, no ANSUL, no grease interceptor, and no grease duct work."
    )
    gated = server.apply_permitiq_quality_gate(
        {
            "_primary_scope": "commercial_restaurant",
            "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Restaurant Interior Alteration"}],
            "companion_permits": [
                {"permit_type": "Electrical Permit — Commercial Tenant Improvement"},
                {"permit_type": "Mechanical Permit — Commercial Tenant Improvement"},
                {"permit_type": "Plumbing Permit — Commercial Tenant Improvement"},
                {"permit_type": "Health Department / Food Establishment Review"},
            ],
            "what_to_bring": [
                "Bring hood, ANSUL, grease interceptor, and plumbing sheets even though they are not in scope.",
                "Bring architectural floor plan and finish schedule.",
            ],
            "common_mistakes": ["Repeating no hood/no grease/no ANSUL in the permit narrative."],
            "pro_tips": ["Keep prep-table storage clearly described."],
            "sources": [{"url": "https://example.com/permit", "title": "Official source"}],
            "confidence": "medium",
        },
        job,
        "Chicago",
        "IL",
    )
    blob = _customer_surface_blob(gated)
    for forbidden in ("hood", "ansul", "grease", "grease interceptor", "plumbing sheets"):
        assert forbidden not in blob
    assert "architectural floor plan" in blob
    assert "prep-table storage" in blob


def test_barber_shop_false_bar_does_not_get_food_health_checklist_language():
    job = (
        "2,000 sf barber shop tenant improvement with reception, haircut stations, retail shelving, "
        "and lighting; no bar, no alcohol, no food service, no kitchen, no hood, no grease interceptor."
    )
    checklist = _checklist_blob(job, "Dallas", "TX")
    out = _apply_core_layers(job, "Dallas", "TX")
    out["checklist"] = list(checklist.split(" | ")) + ["Health department clearance if any food/beverage prep beyond pre-packaged sales"]
    out["what_to_bring"] = [
        "Bring barber shop floor plan and finish schedule.",
        "Bring food establishment and health department forms if the barber shop serves beverages.",
    ]
    out["quality_warnings"] = ["Health department food service review may be required."]
    out = server.apply_permitiq_quality_gate(out, job, "Dallas", "TX")
    blob = checklist + " | " + _customer_surface_blob(out)
    for forbidden in ("food", "beverage", "health department", "food-establishment", "commercial kitchen", "hood", "grease"):
        assert forbidden not in blob
    assert out["_primary_scope"] != "commercial_restaurant"


def test_theater_fog_machine_does_not_repeat_hood_or_grease_in_permit_logic():
    job = (
        "2,800 sf theater tenant improvement with stage fog machine and lighting controls; "
        "no food service, no kitchen, no hood, no grease interceptor."
    )
    out = _apply_core_layers(job, "Dallas", "TX")
    out["permits_required_logic"].append({
        "permit_type": "Building Permit — Commercial Tenant Improvement",
        "included_because": "No hood or grease work is included, but the theater TI still needs building review.",
        "scope_trigger": "model echo",
    })
    out["pro_tips"] = ["Include the stage fog machine cut sheet and fire alarm interface notes if the AHJ asks for theatrical effects documentation."]
    gated = server.apply_permitiq_quality_gate(out, job, "Dallas", "TX")
    blob = _customer_surface_blob(gated)
    for forbidden in ("hood", "grease", "food service", "commercial kitchen"):
        assert forbidden not in blob
    assert "fog machine" in blob


def test_dental_admin_office_filters_clinic_terms_from_permit_logic_and_docs():
    job = (
        "2,400 sf professional office TI for dental billing and insurance administration company; "
        "ordinary office desks and conference rooms only, no clinic, no exam rooms, no patient care, "
        "no x-ray, no medical gas, no treatment rooms."
    )
    out = _apply_core_layers(job, "Boston", "MA")
    out["permits_required_logic"].append({
        "permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration",
        "included_because": "No exam rooms, x-ray, or medical gas are included, so clinic submittals are not needed.",
        "scope_trigger": "model echo",
    })
    out["what_to_bring"] = [
        "Office floor plan and lighting schedule.",
        "Do not bring clinic, exam-room, x-ray, or medical-gas sheets because they are out of scope.",
    ]
    out["quality_warnings"] = ["Clinic, x-ray, and medical gas review were mentioned by the model but are not in scope."]
    gated = server.apply_permitiq_quality_gate(out, job, "Boston", "MA")
    assert gated["_primary_scope"] == "commercial_office_ti"
    blob = _customer_surface_blob(gated)
    for forbidden in ("clinic", "exam room", "exam-room", "x-ray", "medical gas", "medical-gas"):
        assert forbidden not in blob
    assert "office floor plan" in blob


def test_veterinary_admin_office_filters_medical_exclusions_from_logic_and_docs():
    job = (
        "2,400 sf professional office TI for veterinary billing and records administration company; "
        "ordinary office desks and conference rooms only, no clinic, no exam rooms, no animal treatment, "
        "no x-ray, no medical gas, no treatment rooms."
    )
    out = _apply_core_layers(job, "Boston", "MA")
    out["permits_required_logic"].append({
        "permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration",
        "included_because": "No medical-gas or exam-room scope is present; keep veterinary clinic docs out.",
        "scope_trigger": "model echo",
    })
    out["what_to_bring"] = ["Office reflected ceiling plan.", "No exam room, x-ray, medical gas, or clinic documents are required."]
    out["quality_warnings"] = ["Veterinary clinic, exam room, x-ray, and medical gas documents should be excluded."]
    gated = server.apply_permitiq_quality_gate(out, job, "Boston", "MA")
    assert gated["_primary_scope"] == "commercial_office_ti"
    blob = _customer_surface_blob(gated)
    for forbidden in ("clinic", "exam room", "exam-room", "x-ray", "medical gas", "medical-gas"):
        assert forbidden not in blob
    assert "office reflected ceiling plan" in blob


def test_residential_dallas_water_heater_overrides_commercial_ti_model_leak_and_apply_path():
    job = "like-for-like residential water heater replacement in single-family house garage, same fuel type, no structural or commercial work."
    result = {
        "_primary_scope": "commercial",
        "apply_url": "https://example.com/commercial-tenant-improvement-application",
        "permits_required": [{"permit_type": "Building Permit — Commercial Tenant Improvement", "required": True}],
        "permits_required_logic": [{
            "permit_type": "Building Permit — Commercial Tenant Improvement",
            "included_because": "Model selected commercial TI.",
            "scope_trigger": "model leak",
        }],
        "companion_permits": [],
        "sources": [{"url": "https://example.com/permit", "title": "Official source"}],
        "confidence": "medium",
    }
    gated = server.apply_permitiq_quality_gate(result, job, "Dallas", "TX")
    apply_path = server.build_apply_path(gated, job, "Dallas", "TX")
    blob = _customer_surface_blob(gated)
    assert gated["_primary_scope"] == "residential"
    assert "water heater" in gated["permits_required"][0]["permit_type"].lower()
    assert "commercial tenant improvement" not in blob
    assert apply_path["permit_category"] == "Residential / Trade Permit"
    assert apply_path["permit_type"] == "Plumbing Permit — Water Heater Replacement"
    assert apply_path["portal_url"] == ""


def test_commercial_medical_ti_still_repairs_stale_residential_primary():
    job = "medical clinic tenant improvement with exam rooms, x-ray room, and medical gas outlets"
    result = {
        "_primary_scope": "residential",
        "permits_required": [{"permit_type": "Residential HVAC Permit", "required": True}],
        "companion_permits": [],
        "sources": [{"url": "https://example.com/permit", "title": "Official source"}],
        "confidence": "medium",
    }
    gated = server.apply_permitiq_quality_gate(result, job, "Dallas", "TX")
    blob = _customer_surface_blob(gated)
    assert "commercial" in blob
    assert "tenant improvement" in blob
    assert "residential hvac permit" not in blob


def test_commercial_conversion_with_residential_words_still_repairs_to_commercial():
    job = "commercial tenant improvement to convert a single-family house to a medical clinic with water heater relocation"
    result = {
        "_primary_scope": "residential",
        "permits_required": [{"permit_type": "Residential Plumbing Permit", "required": True}],
        "companion_permits": [],
        "sources": [{"url": "https://example.com/permit", "title": "Official source"}],
        "confidence": "medium",
    }
    gated = server.apply_permitiq_quality_gate(result, job, "Dallas", "TX")
    blob = _customer_surface_blob(gated)
    assert "commercial" in blob
    assert "tenant improvement" in blob
    assert "residential plumbing permit" not in blob
