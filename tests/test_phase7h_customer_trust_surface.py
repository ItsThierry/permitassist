import json
from importlib import util
from pathlib import Path

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper_phase7h_customer_trust",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_INDEX = REPO_ROOT / "frontend" / "index.html"

SAFE_INTERIM_PERMIT_LABEL = "Manual filing path confirmation in progress"
PHASE0_BANNED_CUSTOMER_STRINGS = (
    "local permit name not confirmed",
    "exact permit type needs AHJ verification",
    "name not source-confirmed",
    "permit name not confirmed",
    "best-effort permit name",
)


def _assert_phase0_no_banned_customer_strings(surface: str):
    lowered = surface.lower()
    for banned in PHASE0_BANNED_CUSTOMER_STRINGS:
        assert banned.lower() not in lowered


def _assert_no_placeholder_primary_fields(result: dict):
    primary_values = [
        result.get("permit_type"),
        result.get("permit_name"),
        result.get("_permit_display_name"),
        *(item.get("permit_type") for item in result.get("permits_required") or [] if isinstance(item, dict)),
        *(item.get("portal_selection") for item in result.get("permits_required") or [] if isinstance(item, dict)),
    ]
    raw_apply_path = result.get("apply_path")
    apply_path = raw_apply_path if isinstance(raw_apply_path, dict) else {}
    primary_values.append(apply_path.get("permit_type"))
    for value in primary_values:
        text = str(value or "")
        assert text != "Permit required"
        _assert_phase0_no_banned_customer_strings(text)


def test_phase7h_verified_permit_type_populates_visible_required_permits(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Commercial Alteration Permit",
        "permit_name": "Commercial Alteration Permit",
        "permits_required": [],
        "_evidence_pack": {
            "enabled": True,
            "matched_fields": ["permit_type", "apply_url"],
            "failed_closed_fields": ["fee_range", "inspections"],
        },
        "coverage_truth": {
            "heading": "Coverage scope",
            "official_source_backed": ["permit type", "apply URL / route"],
            "not_confirmed_from_official_source": ["fees", "timelines"],
        },
    }

    server.ensure_customer_visible_permit_trust_statement(result, "Chicago", "IL")

    assert result["permit_type"] == "Commercial Alteration Permit"
    assert result["permit_name"] == "Commercial Alteration Permit"
    assert result["permits_required"] == [
        {
            "permit_type": "Commercial Alteration Permit",
            "required": True,
            "notes": "Official-source backed permit type. Verify final filing path with the AHJ before submitting.",
        }
    ]
    assert result.get("permit_type_verified") is True


def test_phase7h_yes_verdict_without_verified_permit_type_gets_safe_customer_statement(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": None,
        "permit_name": "",
        "permits_required": [],
        "apply_phone": "(312) 744-3449",
        "applying_office": "Chicago Department of Buildings",
        "apply_path": {"verification_note": "Use the AHJ portal/contact to confirm exact filing category."},
        "_evidence_pack": {
            "enabled": True,
            "matched_fields": ["apply_url"],
            "failed_closed_fields": ["permit_type", "fee_range", "inspections"],
        },
        "coverage_truth": {
            "heading": "Coverage scope",
            "official_source_backed": ["permit type", "apply URL / route"],
            "not_confirmed_from_official_source": ["fees", "timelines"],
        },
    }

    server.ensure_customer_visible_permit_trust_statement(result, "Chicago", "IL")

    statement = SAFE_INTERIM_PERMIT_LABEL
    assert result["permit_type"] == statement
    assert result["permit_name"] == statement
    assert result["permit_type_verified"] is False
    assert result["permits_required"] == [
        {
            "permit_type": statement,
            "required": True,
            "notes": "Manual filing path check is in progress for this lookup. Confirm the final filing category with Chicago Department of Buildings or call (312) 744-3449 before submitting.",
        }
    ]
    assert "permit type" not in result["coverage_truth"]["official_source_backed"]
    assert "exact permit type" in result["coverage_truth"]["not_confirmed_from_official_source"]
    text = json.dumps(result, sort_keys=True)
    _assert_phase0_no_banned_customer_strings(text)
    _assert_no_placeholder_primary_fields(result)
    assert "Chicago Department of Buildings" in text


def test_phase7h_public_redaction_keeps_trust_statement_but_drops_internal_evidence_fields(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": None,
        "permits_required": [],
        "apply_phone": "(832) 394-8880",
        "applying_office": "Houston Permitting Center",
        "field_evidence_confidence": "high",
        "raw_path": "/home/boban/private/raw-response.json",
        "_source_classification": [
            {"source_class": "local_ahj", "url": "https://www.houstonpermittingcenter.org", "allowed_as_official_support": True}
        ],
        "_official_sources": [{"url": "https://www.houstonpermittingcenter.org", "source_class": "local_ahj"}],
        "_private_debug_reason": "internal source-classification contract",
        "_evidence_pack": {
            "enabled": True,
            "mode": "future_registry_pack_preview",
            "public_redaction": "drop_evidence_pack",
            "matched_fields": ["apply_url"],
            "failed_closed_fields": ["permit_type"],
            "fingerprint_sha256": "a" * 64,
        },
    }

    server.ensure_customer_visible_permit_trust_statement(result, "Houston", "TX")
    public = server.redact_public_output(result)
    text = json.dumps(public, sort_keys=True)

    assert public["permit_type"] == SAFE_INTERIM_PERMIT_LABEL
    assert public["permits_required"][0]["required"] is True
    _assert_phase0_no_banned_customer_strings(text)
    _assert_no_placeholder_primary_fields(public)
    for forbidden in (
        "_evidence_pack",
        "field_evidence_confidence",
        "evidence_pack",
        "fingerprint",
        "/home/boban",
        "raw-response",
        "_source_classification",
        "_official_sources",
        "source_classification",
        "official_sources",
        "internal source-classification contract",
    ):
        assert forbidden not in text


def test_phase7h_frontend_does_not_call_unverified_placeholder_an_exact_permit():
    source = FRONTEND_INDEX.read_text(encoding="utf-8")

    for banned in PHASE0_BANNED_CUSTOMER_STRINGS:
        assert banned not in source.lower()
    assert "permitTypeNeedsAhjVerify" not in source
    assert "Permit required · name not source-confirmed" not in source
    assert "exact official permit/application name is not source-confirmed" not in source
    assert "Manual filing path check in progress" in source
    assert "Confirm final filing category before submitting" in source
    assert "Contact the AHJ permit counter to confirm the final filing category for this scope" in source


def test_phase7h_official_category_stays_visible_without_verifying_exact_permit_name(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Commercial Alteration Permit",
        "permit_name": "Commercial Alteration Permit",
        "_permit_display_name": "Commercial Alteration Permit",
        "permits_required": [{"permit_type": "Commercial Alteration Permit", "required": True}],
        "apply_path": {
            "permit_type": "Commercial Alteration Permit",
            "application_steps": ["Choose Commercial Alteration Permit in the portal."],
        },
        "_evidence_pack": {
            "enabled": True,
            "matched_fields": ["official_application_category", "apply_url"],
            "failed_closed_fields": [],
            "permit_name_source_field": "official_application_category",
        },
    }

    server.ensure_customer_visible_permit_trust_statement(result, "Chicago", "IL", "restaurant tenant improvement")

    assert result["permit_type"] == "Commercial Alteration Permit"
    assert result["permit_name"] == "Commercial Alteration Permit"
    assert result["_permit_display_name"] == "Commercial Alteration Permit"
    assert result["permit_type_verified"] is False
    assert result["permits_required"][0]["permit_type"] == "Commercial Alteration Permit"
    assert result["apply_path"]["permit_type"] == "Commercial Alteration Permit"
    assert "Manual filing path check is in progress" in result["apply_path"]["verification_note"]
    _assert_no_placeholder_primary_fields(result)
    _assert_phase0_no_banned_customer_strings(json.dumps(result, sort_keys=True))


def test_phase7h_non_evidence_city_source_exact_name_is_neutralized(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Building Permit",
        "permit_name": "Building Permit",
        "data_source": "city",
        "_meta": {"city_match_level": "city"},
        "permits_required": [{"permit_type": "Building Permit", "portal_selection": "Building Permit", "required": True}],
        "apply_path": {
            "permit_type": "Building Permit",
            "application_steps": ["Select Building Permit, then upload drawings."],
        },
    }

    server.ensure_customer_visible_permit_trust_statement(result, "Austin", "TX", "office tenant improvement")

    statement = SAFE_INTERIM_PERMIT_LABEL
    text = json.dumps(result, sort_keys=True)
    assert result["permit_type"] == statement
    assert result["permit_name"] == statement
    assert result["permit_type_verified"] is False
    assert result["permits_required"][0]["permit_type"] == statement
    assert result["permits_required"][0]["portal_selection"] == statement
    assert "Building Permit" not in text
    _assert_no_placeholder_primary_fields(result)
    _assert_phase0_no_banned_customer_strings(text)


def test_ef04_build_claim_citations_omits_claims_without_quoted_snippets(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Commercial Alteration Permit",
        "permits_required": [{"permit_type": "Commercial Alteration Permit", "required": True}],
        "apply_url": "https://chicago.gov/permits",
        "fee_range": "$500-$1,000",
        "confidence": "high",
        "sources": [{"url": "https://chicago.gov/permits", "title": "Chicago Permits", "snippet": ""}],
    }

    citations = server.build_claim_citations(result)

    assert citations == []
    assert result["claim_citations"] == []
    assert [claim["field"] for claim in result["unverified_claims"]] == ["permit_type", "apply_url", "fee_range"]
    assert all(claim["confidence"] == "needs_verification" for claim in result["unverified_claims"])
    assert result["confidence"] == "medium"
    assert result["needs_review"] is True
    assert "not shown as verified" in " ".join(result["quality_warnings"])


def test_ef04_build_claim_citations_keeps_only_nonempty_quoted_support(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Commercial Alteration Permit",
        "permits_required": [{"permit_type": "Commercial Alteration Permit", "required": True}],
        "confidence": "medium",
        "sources": [
            {"url": "https://chicago.gov/empty", "title": "Empty", "snippet": "   "},
            {"url": "https://chicago.gov/permit-type", "title": "Permit Type", "snippet": "Alteration work requires a building permit."},
        ],
    }

    citations = server.build_claim_citations(result)

    assert len(citations) == 1
    assert citations[0]["field"] == "permit_type"
    assert citations[0]["source_url"] == "https://chicago.gov/permit-type"
    assert citations[0]["quoted_snippet"] == "Alteration work requires a building permit."
    assert "unverified_claims" not in result


def test_ef04_report_renderer_does_not_recreate_unverified_claim_citations(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Permit required — exact permit type needs AHJ verification",
        "permit_name": "Permit required — exact permit type needs AHJ verification",
        "permit_type_verified": False,
        "permits_required": [{"permit_type": "Permit required — exact permit type needs AHJ verification", "required": True}],
        "claim_citations": [],
        "unverified_claims": [{"field": "permit_type", "confidence": "needs_verification"}],
        "sources": [{"url": "https://chicago.gov/permits", "title": "Chicago Permits", "snippet": ""}],
    }

    html = server.render_white_label_report_html({
        "result": result,
        "job_type": "restaurant tenant improvement",
        "city": "Chicago",
        "state": "IL",
    })

    assert result["claim_citations"] == []
    assert "Source quote not attached for this field yet" not in html
    assert "No citations attached yet" in html


def test_phase0_category_only_lookup_has_no_banned_strings_across_public_and_report_surfaces(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Commercial Alteration Permit",
        "permit_name": "Commercial Alteration Permit",
        "_permit_display_name": "Commercial Alteration Permit",
        "permits_required": [
            {"permit_type": "Commercial Alteration Permit", "portal_selection": "Commercial Alteration Permit", "required": True}
        ],
        "applying_office": "Chicago Department of Buildings",
        "apply_phone": "(312) 744-3449",
        "apply_path": {
            "permit_type": "Commercial Alteration Permit",
            "verification_note": "Use the AHJ portal/contact to confirm exact filing category.",
        },
        "_evidence_pack": {
            "enabled": True,
            "matched_fields": ["official_application_category", "apply_url"],
            "failed_closed_fields": ["permit_type", "fee_range", "inspections"],
            "permit_name_source_field": "official_application_category",
        },
    }

    server.ensure_customer_visible_permit_trust_statement(result, "Chicago", "IL", "restaurant tenant improvement")
    public = server.redact_public_output(result)
    html = server.render_white_label_report_html({
        "result": public,
        "job_type": "restaurant tenant improvement",
        "city": "Chicago",
        "state": "IL",
    })
    surface = json.dumps(public, sort_keys=True, default=str) + "\n" + html

    assert "Commercial Alteration Permit" in surface
    assert "Manual filing path check is in progress" in surface
    assert "Chicago Department of Buildings" in surface
    _assert_no_placeholder_primary_fields(public)
    _assert_phase0_no_banned_customer_strings(surface)


def test_phase0_phoenix_solar_battery_400a_requires_non_final_completion_across_customer_surfaces(tmp_path, monkeypatch):
    monkeypatch.setenv("PERMITASSIST3_TICKET_PATH", str(tmp_path / "pa3_tickets.jsonl"))
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=4: True)
    job = "Install rooftop solar PV, battery storage, and a 400A service upgrade on a Phoenix commercial building"
    result = server.finalize_permit_lookup_result(
        {
            "permit_verdict": "YES",
            "permit_required": True,
            "permit_type": "Permit required — exact permit type needs AHJ verification",
            "permit_name": "Permit required — local permit name not confirmed",
            "_permit_display_name": "Permit required · name not source-confirmed",
            "permits_required": [
                {"permit_type": "Permit required — exact permit type needs AHJ verification", "required": True}
            ],
            "applying_office": "Phoenix Planning and Development Department",
            "apply_phone": "(602) 262-7811",
            "apply_url": "https://www.phoenix.gov/pdd",
            "sources": [
                {"url": "https://www.gosolarapp.org/solarapp/residential-solar-permit", "title": "SolarAPP", "snippet": "Residential solar permit"},
                {"url": "https://www.phoenix.gov/pdd", "title": "Phoenix Planning and Development", "snippet": "Phoenix Planning and Development Department permit services."},
            ],
        },
        job,
        "Phoenix",
        "AZ",
        evidence_allowed=False,
    )
    public = server.redact_public_output(result)
    html = server.render_white_label_report_html({"result": public, "job_type": job, "city": "Phoenix", "state": "AZ"})
    pdf_text = server.render_report_pdf(job, "Phoenix", "AZ", public).decode("latin-1", errors="ignore")
    surface = json.dumps(public, sort_keys=True, default=str) + "\n" + html + "\n" + pdf_text

    assert result["permit_type_verified"] is False
    assert result["final_answer_state"] == server.PA3_NON_FINAL
    assert result["permit_verdict"] == "NON_FINAL"
    assert result["completion_ticket"]["ticket_id"].startswith("pa3_")
    assert SAFE_INTERIM_PERMIT_LABEL not in surface
    _assert_no_placeholder_primary_fields(public)
    _assert_phase0_no_banned_customer_strings(surface)


def test_phase0_harris_overlay_sets_explicit_local_ahj_source_classification(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = server.apply_harris_county_residential_development_overlay(
        {"permit_verdict": "YES", "sources": []},
        "Residential detached garage/workshop with new 100A service on an unincorporated Harris County lot",
        "Unincorporated Harris County",
        "TX",
    )

    classifications = result.get("_source_classification")
    assert isinstance(classifications, list)
    assert any(item.get("source_class") == "local_ahj" for item in classifications if isinstance(item, dict))
    assert server._phase7h_permit_type_verified(result, result["permit_type"]) is True


def test_phase0_harris_non_overlay_scopes_require_non_final_completion_without_banned_strings(tmp_path, monkeypatch):
    monkeypatch.setenv("PERMITASSIST3_TICKET_PATH", str(tmp_path / "pa3_tickets.jsonl"))
    server = _import_server(tmp_path, monkeypatch)
    for job in (
        "Build a swimming pool on a residential lot in unincorporated Harris County",
        "Build a new single-family home on an unincorporated Harris County lot",
        "Repair a garage door opener on a residential home in unincorporated Harris County",
    ):
        result = server.finalize_permit_lookup_result(
            {
                "permit_verdict": "YES",
                "permit_required": True,
                "permit_type": "Permit required — exact permit type needs AHJ verification",
                "permit_name": "Permit required — local permit name not confirmed",
                "sources": [{"url": "https://epermits.harriscountytx.gov", "title": "Harris County ePermits", "snippet": "Permit applications and project status."}],
            },
            job,
            "Unincorporated Harris County",
            "TX",
            evidence_allowed=False,
        )
        public = server.redact_public_output(result)
        surface = json.dumps(public, sort_keys=True, default=str)

        assert result["permit_type_verified"] is False
        assert result["final_answer_state"] == server.PA3_NON_FINAL
        assert result["completion_ticket"]["ticket_id"].startswith("pa3_")
        assert SAFE_INTERIM_PERMIT_LABEL not in surface
        _assert_no_placeholder_primary_fields(public)
        _assert_phase0_no_banned_customer_strings(surface)


def test_phase0_local_ahj_flag_does_not_verify_category_only_name(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Commercial Alteration Permit",
        "official_application_category": "Commercial Alteration Permit",
        "permit_name_source_field": "official_application_category",
        "_permit_type_official_source_verified": True,
        "_source_classification": [{"source_class": "local_ahj", "allowed_as_official_support": True}],
    }

    assert server._phase7h_permit_type_verified(result, "Commercial Alteration Permit") is False


def test_phase0_local_ahj_flag_does_not_self_verify_permit_type_field(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Commercial Alteration Permit",
        "permit_name_source_field": "permit_type",
        "_permit_type_official_source_verified": True,
        "_source_classification": [{"source_class": "local_ahj", "allowed_as_official_support": True}],
    }

    assert server._phase7h_permit_type_verified(result, "Commercial Alteration Permit") is False
