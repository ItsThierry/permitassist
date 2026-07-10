"""Characterization and contract tests for deploy-readiness detail precedence.

Covers the four behavior classes required before live deploy:
1. saved inspection preservation over generic packet defaults
2. specific permit-kind preservation through projection/HTTP share path
3. canonical family IDs vs sealed customer display labels
4. safe saved documents over generic packet defaults

These tests are universal (no city/case-ID hardcoding beyond existing contract fixtures)
and include positive, negative, and idempotence checks.
"""

from __future__ import annotations

import copy
import json
from importlib import util
from pathlib import Path

import pytest

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
assert _HELPER_SPEC is not None and _HELPER_SPEC.loader is not None
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server


def _normalize_insp_labels(items):
    out = []
    for item in items or []:
        if isinstance(item, dict):
            stage = str(item.get("stage") or item.get("label") or item.get("name") or "").strip()
            timing = str(item.get("timing") or item.get("when") or "").strip()
            out.append(f"{stage} — {timing}" if stage and timing else stage or timing)
        else:
            text = str(item or "").strip()
            if text:
                out.append(text)
    return out


def test_saved_inspections_beat_generic_defaults_and_idempotent(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "load_report_template", lambda: "__REPORT_DATA__")
    saved = {
        "permit_name": "Commercial Tenant Improvement Building Permit",
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permits_required": [{"permit_type": "Commercial Tenant Improvement Building Permit", "required": True}],
        "fee_range": "$1,200-$4,000 planning estimate",
        "approval_timeline": {"simple": "2-4 weeks", "complex": "4-8 weeks"},
        "what_to_bring": ["Commercial TI plans"],
        "inspections": [
            "Framing inspection before cover",
            "MEP rough inspections before cover",
            {"stage": "Final building inspection", "timing": "Before occupancy"},
        ],
        "pro_tips": ["Coordinate plan review before starting work."],
        "sources": [
            {
                "url": "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/default.aspx",
                "title": "Dallas Building Inspection",
            }
        ],
    }
    share = {
        "data": copy.deepcopy(saved),
        "job_type": "Dallas commercial office TI",
        "city": "Dallas",
        "state": "TX",
    }
    html1 = server.render_share_page(share)
    payload1 = json.loads(html1)
    labels1 = [item["label"] for item in payload1["checklist"]["items"]]
    assert any("Framing inspection before cover" in label for label in labels1)
    assert any("MEP rough inspections before cover" in label for label in labels1)
    assert any(
        "Final building inspection — Before occupancy" in label or "Final building inspection" in label
        for label in labels1
    )
    # Second render of already-projected payload must stay stable.
    share2 = {
        "data": copy.deepcopy(payload1["share"]["data"]),
        "job_type": share["job_type"],
        "city": share["city"],
        "state": share["state"],
    }
    # If the first pass sealed a packet, re-render must not drop framing detail.
    if isinstance(share2["data"].get("public_packet"), dict):
        share2["data"]["public_packet"] = copy.deepcopy(payload1["share"]["data"].get("public_packet") or {})
    html2 = server.render_share_page(share2)
    payload2 = json.loads(html2)
    labels2 = [item["label"] for item in payload2["checklist"]["items"]]
    assert any("Framing inspection before cover" in label for label in labels2)


def test_wrong_scope_saved_inspections_do_not_mutate_decision(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    dirty = {
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_kind": "Electrical",
        "permit_name": "Electrical Permit",
        "permits_required": [{"permit_type": "Electrical Permit", "required": True, "family": "electrical"}],
        "inspections": ["\x00INTERNAL DEBUG TRACE for residential kitchen only"],
        "sources": [
            {
                "url": "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/default.aspx",
                "title": "Dallas Building Inspection",
            }
        ],
    }
    public = server.build_customer_permit_view_model(dirty, "electrical panel upgrade", "Dallas", "TX")
    assert public["permit_decision"] == "REQUIRED"
    blob = json.dumps(public, sort_keys=True)
    assert "INTERNAL DEBUG TRACE" not in blob
    assert "\\u0000" not in blob and "\x00" not in blob


@pytest.mark.parametrize(
    ("job_type", "city", "state", "permit_kind"),
    [
        ("Residential kitchen remodel", "Dallas", "TX", "Residential Building / Remodel"),
        ("Commercial restaurant tenant improvement", "Austin", "TX", "Commercial Building / Tenant Improvement"),
    ],
)
def test_specific_permit_kind_survives_repeated_projection(tmp_path, monkeypatch, job_type, city, state, permit_kind):
    server = _import_server(tmp_path, monkeypatch)
    base = {
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_decision": "REQUIRED",
        "permit_kind": permit_kind,
        "customer_headline": f"Permit required: {permit_kind}",
        "customer_next_step": f"File the {permit_kind} before starting work.",
        "permit_name": permit_kind,
        "permit_type": permit_kind,
        "permits_required": [{"permit_type": permit_kind, "required": True}],
        "sources": [
            {
                "url": "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/default.aspx"
                if city == "Dallas"
                else "https://www.austintexas.gov/department/development-services",
                "title": f"{city} permit office",
            }
        ],
        "source_urls": [
            "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/default.aspx"
            if city == "Dallas"
            else "https://www.austintexas.gov/department/development-services"
        ],
    }
    p1 = server.build_customer_permit_view_model(copy.deepcopy(base), job_type, city, state)
    assert p1["permit_decision"] == "REQUIRED"
    assert p1["permit_kind"] == permit_kind
    p2 = server.build_customer_permit_view_model(copy.deepcopy(p1), job_type, city, state)
    assert p2["permit_kind"] == permit_kind
    assert p2["permit_decision"] == p1["permit_decision"]
    assert list(p2.get("required_permit_families") or []) == list(p1.get("required_permit_families") or [])


def test_cross_segment_permit_kind_is_rejected_without_decision_change(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    dirty = {
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_decision": "REQUIRED",
        "permit_kind": "Commercial Building / Tenant Improvement",
        "permit_name": "Building Permit",
        "permits_required": [{"permit_type": "Building Permit", "required": True}],
        "sources": [
            {
                "url": "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/default.aspx",
                "title": "Dallas Building Inspection",
            }
        ],
    }
    public = server.build_customer_permit_view_model(
        dirty, "Residential kitchen remodel like-for-like cabinets", "Dallas", "TX", job_category="residential"
    )
    assert public["permit_decision"] == "REQUIRED"
    assert "commercial" not in str(public.get("permit_kind") or "").lower()
    assert "tenant improvement" not in str(public.get("permit_kind") or "").lower()


def test_canonical_family_ids_and_display_labels_are_separated(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    from api.server import build_customer_permit_view_model
    import api.research_engine as engine

    job = (
        "Install one ductless mini-split heat pump system for an existing single-family residence, "
        "including one outdoor condenser and two indoor wall-mounted heads. New electrical circuit/disconnect as required."
    )
    scope_contract = engine.build_scope_contract(job, "Seattle", "WA", job_category="residential")
    raw = {
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_name": "Mechanical Permit — HVAC Equipment Changeout",
        "permit_type": "Mechanical Permit — HVAC Equipment Changeout",
        "job_category": "residential",
        "permits_required": [
            {"permit_type": "Mechanical Permit — HVAC Equipment Changeout", "required": True, "notes": "model output"}
        ],
        "companion_permits": [
            {"permit_type": "Electrical Permit", "reason": "Required for new circuit/disconnect", "certainty": "almost_certain"}
        ],
        "sources": [
            {
                "url": "https://www.seattle.gov/sdci/permits/permits-we-issue-(a-z)/mechanical-permit",
                "title": "Mechanical Permit - SDCI | seattle.gov",
            },
            {
                "url": "https://www.seattle.gov/sdci/permits/permits-we-issue-(a-z)/electrical-permit",
                "title": "Electrical Permit - SDCI | seattle.gov",
            },
            {
                "url": "https://www.seattle.gov/sdci/permits/permits-we-issue-(a-z)/refrigeration-permit",
                "title": "Refrigeration Permit - SDCI | seattle.gov",
            },
        ],
        "_scope_contract": scope_contract,
    }
    raw = engine.apply_scope_aware_permit_classification(raw, job, scope_contract)
    public = build_customer_permit_view_model(raw, job, "Seattle", "WA", job_category="residential")
    packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
    canonical_ids = {str(x).lower() for x in (public.get("required_permit_families") or [])}
    packet_ids = {str(x).lower() for x in (packet.get("required_families") or [])}
    display_labels = list(packet.get("required_family_display_labels") or [])
    assert {"electrical", "mechanical", "refrigeration"} <= (canonical_ids | packet_ids)
    # Top-level canonical field must not mix title-case labels into the ID list.
    assert not any(str(x)[:1].isupper() and str(x).lower() != str(x) for x in (public.get("required_permit_families") or []) if " " not in str(x))
    assert set(display_labels) >= {"Electrical", "Mechanical", "Refrigeration"}
    p2 = build_customer_permit_view_model(copy.deepcopy(public), job, "Seattle", "WA", job_category="residential")
    packet2 = p2.get("public_packet") if isinstance(p2.get("public_packet"), dict) else {}
    assert list(packet2.get("required_families") or []) == list(packet.get("required_families") or [])
    assert list(packet2.get("required_family_display_labels") or []) == display_labels


def test_saved_documents_beat_generic_defaults_without_truth_mutation(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    dirty = {
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_kind": "Commercial Building / Tenant Improvement",
        "permit_name": "Building Permit — Tenant Improvement / Office Interior Alteration",
        "permits_required": [
            {
                "permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration",
                "required": True,
            }
        ],
        "customer_headline": "Permit required: Commercial Building / Tenant Improvement.",
        "customer_next_step": "File with Dallas Development Services Department.",
        "applying_office": "Dallas Development Services Department",
        "sources": [
            {
                "url": "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/default.aspx",
                "title": "Dallas Building Inspection",
            }
        ],
        "apply_path": {
            "support_level": "needs verification",
            "platform": "UNKNOWN",
            "portal_url": "",
            "login_required": "likely",
            "permit_category": "Commercial Building / Tenant Improvement",
            "permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration",
            "portal_selection_path": ["Open the UNKNOWN start URL."],
            "likely_documents": ["scope of work", "plans/drawings if required"],
            "steps": ["Create or sign into the contractor/applicant account if likely required."],
        },
        "companion_permits": [
            {
                "permit_type": "Electrical Permit — Tenant Improvement",
                "label": "Companion / secondary permit",
                "requirement_label": "Likely required based on scope",
                "certainty": "likely",
                "reason": "Companion permit for electrical alterations only.",
            }
        ],
    }
    public = server.build_customer_permit_view_model(
        dirty, "commercial office tenant improvement", "Dallas", "TX"
    )
    assert public["permit_decision"] == "REQUIRED"
    apply_path = public.get("apply_path") or {}
    assert "likely_documents" not in apply_path
    assert apply_path.get("documents_to_prepare") == ["scope of work", "plans/drawings if required"]
    public2 = server.build_customer_permit_view_model(
        copy.deepcopy(public), "commercial office tenant improvement", "Dallas", "TX"
    )
    assert (public2.get("apply_path") or {}).get("documents_to_prepare") == [
        "scope of work",
        "plans/drawings if required",
    ]


def test_source_backed_packet_documents_outrank_saved_documents(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    dirty = {
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_kind": "Building",
        "permit_name": "Building Permit",
        "permits_required": [
            {
                "permit_type": "Building Permit",
                "required": True,
                "family": "building",
                "documents": ["NOA / Florida Product Approval for impact-rated windows"],
            }
        ],
        "apply_path": {
            "likely_documents": ["scope of work", "plans/drawings if required"],
        },
        "sources": [
            {
                "url": "https://www.miamidade.gov/building/library/guidelines/permit-guidelines.pdf",
                "title": "Miami-Dade Building",
            }
        ],
    }
    public = server.build_customer_permit_view_model(
        dirty, "replace impact-rated windows like-for-like", "Miami", "FL"
    )
    docs = list((public.get("apply_path") or {}).get("documents_to_prepare") or public.get("documents_to_prepare") or [])
    assert any("NOA" in str(d) or "Product Approval" in str(d) for d in docs)
    assert public["permit_decision"] == "REQUIRED"


def test_uncertainty_permit_kind_is_not_force_restored(tmp_path, monkeypatch):
    """Fable HIGH: uncertainty/generic kinds must not reappear after post-projection restore."""
    server = _import_server(tmp_path, monkeypatch)
    for kind in (
        "Building Permit Likely Required?",
        "Verify with permit office",
        "Unknown Permit Type",
    ):
        data = {
            "permit_required": True,
            "permit_verdict": "YES",
            "permit_decision": "REQUIRED",
            "permit_kind": kind,
            "permit_name": kind,
            "permit_type": kind,
            "customer_headline": "Permit required",
            "customer_next_step": "File with the building department.",
            "permits_required": [{"permit_type": kind}],
            "sources": [
                {
                    "url": "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/default.aspx",
                    "title": "Dallas",
                }
            ],
            "source_urls": [
                "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/default.aspx"
            ],
        }
        pub = server.build_customer_permit_view_model(
            copy.deepcopy(data), "Residential kitchen remodel", "Dallas", "TX"
        )
        kind_out = str(pub.get("permit_kind") or "")
        assert "likely" not in kind_out.lower()
        assert "unknown" not in kind_out.lower()
        assert "verify with permit office" not in kind_out.lower()
        assert kind_out != kind
        assert pub.get("permit_decision") == "REQUIRED"
        pkt = pub.get("public_packet") or {}
        display = str(pkt.get("display_permit_kind") or "")
        assert "likely" not in display.lower()
        assert "unknown" not in display.lower()


def test_wrong_scope_companion_rows_are_scrubbed_or_dropped(tmp_path, monkeypatch):
    """Fable MED: companion restore must not reintroduce wrong-scope or internal keys."""
    server = _import_server(tmp_path, monkeypatch)
    data = {
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_decision": "REQUIRED",
        "permit_kind": "Commercial Building / Tenant Improvement",
        "permit_name": "Commercial Building / Tenant Improvement",
        "permit_type": "Commercial Building / Tenant Improvement",
        "customer_headline": "Permit required",
        "customer_next_step": "File with the building department.",
        "permits_required": [{"permit_type": "Commercial Building / Tenant Improvement"}],
        "companion_permits": [
            {
                "permit_type": "Owner-builder ADU solar PV Electrical Permit",
                "reason": "homeowner ADU solar circuit",
                "certainty": "likely",
                "_debug_source": "should_not_leak",
                "ahj_contact_source": "AHJ internal",
            }
        ],
        "sources": [
            {
                "url": "https://www.austintexas.gov/department/development-services",
                "title": "Austin",
            }
        ],
        "source_urls": ["https://www.austintexas.gov/department/development-services"],
    }
    pub = server.build_customer_permit_view_model(
        copy.deepcopy(data),
        "Commercial restaurant tenant improvement with Type I hood",
        "Austin",
        "TX",
    )
    serialized = json.dumps(pub, sort_keys=True, default=str).lower()
    assert "homeowner" not in serialized
    assert "owner-builder" not in serialized
    assert "adu" not in serialized
    assert "_debug_source" not in serialized
    assert "ahj_contact_source" not in serialized
    assert pub.get("permit_decision") == "REQUIRED"


def test_legitimate_same_scope_companion_survives_scrub(tmp_path, monkeypatch):
    """Positive control: same-scope companion rows must survive scrub with safe fields."""
    server = _import_server(tmp_path, monkeypatch)
    data = {
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_decision": "REQUIRED",
        "permit_kind": "Commercial Building / Tenant Improvement",
        "permit_name": "Commercial Building / Tenant Improvement",
        "permit_type": "Commercial Building / Tenant Improvement",
        "customer_headline": "Permit required",
        "customer_next_step": "File with the building department.",
        "permits_required": [{"permit_type": "Commercial Building / Tenant Improvement"}],
        "companion_permits": [
            {
                "permit_type": "Electrical Permit — Tenant Improvement",
                "reason": "Companion permit for electrical alterations only.",
                "certainty": "likely",
                "label": "Companion / secondary permit",
                "_debug_source": "should_not_leak",
                "ahj_contact_source": "AHJ internal",
            }
        ],
        "sources": [
            {
                "url": "https://www.austintexas.gov/department/development-services",
                "title": "Austin",
            }
        ],
        "source_urls": ["https://www.austintexas.gov/department/development-services"],
    }
    pub = server.build_customer_permit_view_model(
        copy.deepcopy(data),
        "Commercial restaurant tenant improvement with Type I hood",
        "Austin",
        "TX",
    )
    comps = pub.get("companion_permits") or []
    assert comps, "legitimate same-scope companion must survive scrub"
    row = comps[0]
    assert "Electrical" in str(row.get("permit_type") or "")
    assert "electrical alterations" in str(row.get("reason") or "").lower()
    serialized = json.dumps(pub, sort_keys=True, default=str)
    assert "_debug_source" not in serialized
    assert "ahj_contact_source" not in serialized
    assert pub.get("permit_decision") == "REQUIRED"
