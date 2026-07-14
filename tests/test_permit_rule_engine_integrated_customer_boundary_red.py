"""Integrated release RED contracts for the universal customer egress boundary.

These tests are intentionally endpoint-level. They prove that feature/access/cache
headers may change internal execution but may never select a different public
response schema.
"""
from __future__ import annotations

import copy
import importlib
import json
import sys
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest


FORBIDDEN_PUBLIC_KEYS = {
    "quality_warnings",
    "permit_decision_contract",
    "source_evidence_floor",
    "exact_name_status",
    "exact_apply_url_status",
    "needs_review",
    "confidence_modifier",
    "complexity_modifier",
    "jurisdiction_multiplier",
    "hidden_triggers",
    "claim_citations",
    "missing_fields",
    "model",
    "provider",
    "debug",
    "retrieval_metadata",
    "evidence_metadata",
    "source_metadata",
    "decision_cell",
    "cell_id",
    "resolver",
    "customerdecisiondto",
}
OFFICIAL_URL = "https://www.buckeyeaz.gov/business/development-services/permit-center"


def _walk_keys(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield child_path, str(key)
            yield from _walk_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_keys(child, f"{path}[{index}]")


def _assert_public_boundary(payload: dict) -> None:
    leaked = [
        path
        for path, key in _walk_keys(payload)
        if key.startswith("_") or key.lower() in FORBIDDEN_PUBLIC_KEYS
    ]
    assert leaked == []

    # No-neuter contract: the egress fix cannot remove or demote supported truth.
    assert payload["permit_decision"] == "REQUIRED"
    assert payload["permit_required"] is True
    assert payload["permit_verdict"] == "YES"
    assert payload["applying_office"] == "City of Buckeye Development Services Permit Center"
    assert payload["apply_url"] == OFFICIAL_URL
    assert payload["source_urls"] == [OFFICIAL_URL]
    assert payload["sources"][0]["url"] == OFFICIAL_URL
    assert len(payload["permits_required"]) == 10
    assert payload["permits_required"][0]["required_status"] == "REQUIRED"
    assert payload["permits_required"][0]["provenance"]["decision_source"] == "sealed_core_decision_cell"
    assert [row["required_status"] for row in payload["permits_required"][1:]] == ["VERIFY"] * 9
    assert payload["requirements"]
    assert payload["documents_needed"]
    assert payload["inspections"]


def _import_server(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FREE_LOOKUP_DB", str(tmp_path / "ip_lookups.db"))
    monkeypatch.setenv("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    repo_root = Path(__file__).resolve().parents[1]
    api_dir = repo_root / "api"
    for path in (str(repo_root), str(api_dir)):
        if path not in sys.path:
            sys.path.insert(0, path)
    research_module = sys.modules.get("research_engine")
    if research_module is not None and not hasattr(research_module, "classify_source_tier"):
        sys.modules.pop("research_engine", None)
    sys.modules.pop("api.server", None)
    api_pkg = sys.modules.get("api")
    if api_pkg is not None and hasattr(api_pkg, "server"):
        delattr(api_pkg, "server")
    server = importlib.import_module("api.server")
    server.CACHE_DB = str(tmp_path / "cache.db")
    server.DATA_DIR = str(tmp_path)
    server.init_db()
    return server


class _LiveServer:
    def __init__(self, handler):
        self.httpd = HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.thread.join(timeout=5)


def _dirty_internal_result() -> dict:
    return {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_kind": "Residential Roofing",
        "permit_type": "Residential Roofing Permit",
        "permits_required": [{"permit_type": "internal raw row"}],
        "sources": [OFFICIAL_URL],
        "source_urls": [OFFICIAL_URL],
        "claim_citations": [{"field": "permit_decision", "source_url": OFFICIAL_URL}],
        "permit_decision_contract": {"decision": "REQUIRED", "cell_id": "internal-cell"},
        "source_evidence_floor": {"status": "satisfied"},
        "quality_warnings": ["internal warning"],
        "missing_fields": ["internal missing"],
        "hidden_triggers": ["internal trigger"],
        "model": "internal-model",
        "provider": "internal-provider",
        "_meta": {"request_id": "internal"},
        "retrieval_metadata": {"query": "internal"},
    }


def _projected_customer_payload() -> dict:
    families = [
        "building",
        "electrical",
        "mechanical",
        "plumbing",
        "fire",
        "planning_zoning",
        "health",
        "liquor",
        "wastewater_fog",
        "certificate_of_occupancy",
    ]
    rows = []
    for index, family in enumerate(families):
        status = "REQUIRED" if index == 0 else "VERIFY"
        rows.append(
            {
                "filing_family": family,
                "permit_type": f"{family.replace('_', ' ').title()} permit",
                "required_status": status,
                "required": True if index == 0 else None,
                "apply_url": OFFICIAL_URL,
                "source_urls": [OFFICIAL_URL],
                "provenance": {
                    "issuing_authority": "City of Buckeye Development Services",
                    "decision_source": "sealed_core_decision_cell",
                },
                "_route_provenance": {"cell_id": "internal-cell"},
            }
        )
    return {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_kind": "Residential Roofing",
        "permit_type": "Residential Roofing Permit",
        "permits_required": rows,
        "applying_office": "City of Buckeye Development Services Permit Center",
        "apply_url": OFFICIAL_URL,
        "source_urls": [OFFICIAL_URL],
        "sources": [{"title": "City permit center", "url": OFFICIAL_URL}],
        "requirements": ["Submit the roofing scope and product information."],
        "documents_needed": ["Roof plan or manufacturer details if requested."],
        "inspections": ["Schedule required roof inspections with the permit office."],
        # Deliberately dirty projection: the final universal egress must remove
        # these even if an upstream helper accidentally reintroduces them.
        "claim_citations": [{"field": "permit_decision", "source_url": OFFICIAL_URL}],
        "permit_decision_contract": {"decision": "REQUIRED"},
        "quality_warnings": ["internal"],
        "_projection_debug": {"provider": "internal"},
        "nested": {"retrieval_metadata": {"query": "internal"}},
    }


def _active_core_wrapped_result(monkeypatch):
    """Build one real active-core result and its authoritative sealed DTO."""
    from api import permit_rule_engine as pre
    from api.v24_decision_cells import V24ResolutionStatus, resolve_v24_cell

    job_type = "residential reroof"
    job_category = "residential"
    city = "Buckeye"
    state = "AZ"
    resolution = resolve_v24_cell(city, state, job_type, job_category, force=True)
    assert resolution.status is V24ResolutionStatus.EXACT_CELL_PUBLISHABLE
    jurisdiction_id = str((resolution.cell or {}).get("jurisdiction_id") or "")
    assert jurisdiction_id
    monkeypatch.setenv(pre.CORE_SETTING, "active")
    monkeypatch.setenv(pre.CORE_ALLOWLIST_SETTING, jurisdiction_id)
    envelope = pre.build_core_decision_envelope(
        resolution,
        job_type=job_type,
        city=city,
        state=state,
        job_category=job_category,
    )
    wrapped = pre.attach_core_decision_envelope(
        {
            "permit_decision": "NOT_REQUIRED",
            "permit_required": False,
            "permit_verdict": "NO",
            "permit_name": "POISON_LEGACY_BINARY",
            "_internal_secret": "POISON_INTERNAL_SECRET",
        },
        envelope,
    )
    sealed = json.loads(envelope.sealed_projection.payload_json)
    assert sealed["permit_decision"] == "REQUIRED"
    assert sealed["permit_required"] is True
    return {
        "job_type": job_type,
        "job_category": job_category,
        "city": city,
        "state": state,
        "wrapped": wrapped,
        "sealed": sealed,
    }


def _insert_cached_active_core_result(server, case) -> None:
    """Persist the private wrapped result so HTTP share/checklist can rehydrate it."""
    with server.sqlite3.connect(server.CACHE_DB) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO permit_cache
            (cache_key, job_type, job_category, city, state, zip_code,
             result_json, created_at, hits)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            [
                "single-projection-red-cache-key",
                case["job_type"],
                case["job_category"],
                case["city"],
                case["state"],
                "85326",
                server.json.dumps(case["wrapped"]),
                server.utc_now().isoformat(),
            ],
        )


def _install_endpoint_stubs(server, monkeypatch, *, evidence_allowed: bool) -> None:
    monkeypatch.setattr(server, "ADMIN_TOKEN", "integrated-red-admin-token")
    monkeypatch.setattr(server, "check_rate_limit", lambda _ip: (False, 0))
    monkeypatch.setattr(server, "get_effective_free_usage", lambda *_args: 0)
    monkeypatch.setattr(server, "record_lookup_usage", lambda *_args: (1, 1))
    monkeypatch.setattr(server, "record_lookup_stat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "record_beta_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_research_permit_with_budget", lambda *_args, **_kwargs: copy.deepcopy(_dirty_internal_result()))
    monkeypatch.setattr(server, "research_permit", lambda *_args, **_kwargs: copy.deepcopy(_dirty_internal_result()))
    monkeypatch.setattr(server, "finalize_permit_lookup_result", lambda result, *_args, **_kwargs: copy.deepcopy(result))
    monkeypatch.setattr(server, "_source_dicts", lambda *_args, **_kwargs: [{"title": "City permit center", "url": OFFICIAL_URL}])
    monkeypatch.setattr(server, "evidence_pack_allowed_for_request", lambda *_args, **_kwargs: evidence_allowed)
    monkeypatch.setattr(server, "build_customer_permit_view_model", lambda *_args, **_kwargs: copy.deepcopy(_projected_customer_payload()))


def _install_real_active_core_endpoint_stubs(server, monkeypatch, case) -> None:
    """Stub I/O/auth while retaining the real finalizer and customer egress."""
    real_finalize = server.finalize_permit_lookup_result
    real_builder = server.build_customer_permit_view_model
    _install_endpoint_stubs(server, monkeypatch, evidence_allowed=True)
    monkeypatch.setattr(server, "finalize_permit_lookup_result", real_finalize)
    monkeypatch.setattr(server, "build_customer_permit_view_model", real_builder)
    monkeypatch.setattr(
        server,
        "_research_permit_with_budget",
        lambda *_args, **_kwargs: copy.deepcopy(case["wrapped"]),
    )
    monkeypatch.setattr(
        server,
        "research_permit",
        lambda *_args, **_kwargs: copy.deepcopy(case["wrapped"]),
    )
    monkeypatch.setattr(
        server,
        "_source_dicts",
        lambda result, *_args, **_kwargs: copy.deepcopy(result.get("sources") or []),
    )
    monkeypatch.setattr(
        server,
        "validate_api_key",
        lambda _authorization: tuple(["paid" + chr(64) + "example.test", {"id": 1}]),
    )
    monkeypatch.setattr(server, "is_paid_user", lambda _email: True)


def _post_permit(base: str, headers: dict[str, str]) -> dict:
    body = json.dumps(
        {
            "job_type": "replace an existing residential asphalt shingle roof",
            "job_category": "residential",
            "city": "Buckeye",
            "state": "AZ",
            "zip_code": "85326",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/api/permit",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


@pytest.mark.parametrize(
    ("request_class", "headers", "evidence_allowed"),
    [
        ("ordinary-public", {}, False),
        ("sample-demo-evidence-preview", {"X-Sample-Demo": "1"}, True),
        ("admin-rate-limit-bypass", {"X-Admin-Token": "integrated-red-admin-token"}, True),
    ],
)
def test_permit_endpoint_uses_one_customer_egress_for_every_request_class(
    tmp_path, monkeypatch, request_class, headers, evidence_allowed
):
    server = _import_server(tmp_path, monkeypatch)
    _install_endpoint_stubs(server, monkeypatch, evidence_allowed=evidence_allowed)

    with _LiveServer(server.Handler) as live:
        payload = _post_permit(live.base, headers)

    _assert_public_boundary(payload)


def test_customer_egress_is_idempotent_and_does_not_demote_ten_lane_packet(tmp_path, monkeypatch):
    """The public serializer must be reusable by API/share/report/checklist paths."""
    server = _import_server(tmp_path, monkeypatch)
    payload = _projected_customer_payload()

    # This function is the required universal architecture introduced by the
    # remediation; exact R9 is expected to fail this RED contract before edits.
    once = server.project_customer_response_egress(payload)
    twice = server.project_customer_response_egress(once)

    assert once == twice
    _assert_public_boundary(once)


def test_historical_active_core_finalize_then_egress_keeps_authoritative_required_decision(
    tmp_path, monkeypatch
):
    """RED at exact 4f7f6e6: finalizer output must not be projected again."""
    server = _import_server(tmp_path, monkeypatch)
    case = _active_core_wrapped_result(monkeypatch)

    finalized = server.finalize_permit_lookup_result(
        copy.deepcopy(case["wrapped"]),
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )
    assert finalized == case["sealed"]

    public = server.build_customer_response_egress(
        finalized,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )

    assert public == server.project_customer_response_egress(case["sealed"])
    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_required"] is True
    assert public["permit_verdict"] == "YES"


def test_historical_finalized_active_core_stays_authoritative_on_share_report_and_checklist(
    tmp_path, monkeypatch
):
    """RED at exact 4f7f6e6: all downstream surfaces must reuse one projection."""
    server = _import_server(tmp_path, monkeypatch)
    case = _active_core_wrapped_result(monkeypatch)
    finalized = server.finalize_permit_lookup_result(
        copy.deepcopy(case["wrapped"]),
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )
    assert finalized == case["sealed"]
    expected = server.project_customer_response_egress(case["sealed"])

    slug = server.create_share(
        case["job_type"],
        case["city"],
        case["state"],
        finalized,
    )
    share = server.get_share(slug)
    assert share is not None
    assert share["data"] == expected
    report_html = server.render_share_page(share)
    assert '\"permit_decision\":\"REQUIRED\"' in report_html

    checklist = server.get_or_create_checklist(
        finalized,
        case["job_type"],
        case["city"],
        case["state"],
    )
    building = next(
        item for item in checklist["items"] if item.get("category") == "building"
    )
    assert building["required"] is True


def test_untrusted_tampered_active_core_still_fails_closed(tmp_path, monkeypatch):
    """The single-projection remedy must not weaken private-envelope integrity."""
    server = _import_server(tmp_path, monkeypatch)
    case = _active_core_wrapped_result(monkeypatch)
    tampered = copy.deepcopy(case["wrapped"])
    tampered["_permit_rule_engine_core"]["sealed_projection"]["payload_json"] = "{}"

    public = server.build_customer_response_egress(
        tampered,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )

    assert public["permit_decision"] == "UNKNOWN"
    assert public["permit_required"] is None
    assert public["permit_verdict"] == "CONTACT_AHJ"


def test_plain_external_customer_dto_cannot_self_assert_verified_state(
    tmp_path, monkeypatch
):
    server = _import_server(tmp_path, monkeypatch)
    case = _active_core_wrapped_result(monkeypatch)
    finalized = server.finalize_permit_lookup_result(
        copy.deepcopy(case["wrapped"]),
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )
    external_plain_dict = server.json.loads(server.json.dumps(finalized))

    public = server.build_customer_response_egress(
        external_plain_dict,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )

    assert public["permit_decision"] == "UNKNOWN"
    assert public["permit_required"] is None
    assert public["permit_verdict"] == "VERIFY"


def test_in_process_regulated_field_mutation_invalidates_verified_projection(
    tmp_path, monkeypatch
):
    server = _import_server(tmp_path, monkeypatch)
    case = _active_core_wrapped_result(monkeypatch)
    finalized = server.finalize_permit_lookup_result(
        copy.deepcopy(case["wrapped"]),
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )
    finalized["permit_decision"] = "NOT_REQUIRED"
    finalized["permit_required"] = False

    public = server.build_customer_response_egress(
        finalized,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )

    assert public["permit_decision"] == "UNKNOWN"
    assert public["permit_required"] is None
    assert public["decision_source"] == "permit_rule_engine_integrity_fail_closed"


def test_repeat_and_private_cache_rehydration_are_byte_stable(
    tmp_path, monkeypatch
):
    server = _import_server(tmp_path, monkeypatch)
    case = _active_core_wrapped_result(monkeypatch)
    _insert_cached_active_core_result(server, case)
    external_plain_dict = server.json.loads(server.json.dumps(case["sealed"]))
    snapshots = []

    for _ in range(3):
        verified = server._rehydrate_cached_verified_core_projection(
            external_plain_dict,
            case["job_type"],
            case["city"],
            case["state"],
        )
        assert isinstance(verified, server._VerifiedCustomerProjection)
        assert "_regulated_projection_sha256" not in verified
        public = server.build_customer_response_egress(
            verified,
            case["job_type"],
            case["city"],
            case["state"],
            job_category=case["job_category"],
        )
        slug = server.create_share(
            case["job_type"], case["city"], case["state"], verified
        )
        share = server.get_share(slug)
        assert share is not None
        checklist = server.get_or_create_checklist(
            verified, case["job_type"], case["city"], case["state"]
        )
        snapshots.append(
            server.json.dumps(
                {
                    "public": public,
                    "shared": share["data"],
                    "checklist": checklist,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )

    assert len(set(snapshots)) == 1
    assert server.json.loads(snapshots[0])["public"] == (
        server.project_customer_response_egress(case["sealed"])
    )


def test_http_share_and_checklist_rehydrate_cached_private_core_without_payload_marker(
    tmp_path, monkeypatch
):
    server = _import_server(tmp_path, monkeypatch)
    case = _active_core_wrapped_result(monkeypatch)
    _insert_cached_active_core_result(server, case)
    external_plain_dict = server.json.loads(server.json.dumps(case["sealed"]))

    with _LiveServer(server.Handler) as live:
        share_reply = _post_json(
            live.base,
            "/api/share",
            {
                "job_type": case["job_type"],
                "city": case["city"],
                "state": case["state"],
                "result": external_plain_dict,
            },
            {},
        )
        checklist = _post_json(
            live.base,
            "/api/checklist",
            {
                "job_type": case["job_type"],
                "city": case["city"],
                "state": case["state"],
                "result": external_plain_dict,
            },
            {},
        )

    share = server.get_share(share_reply["slug"])
    assert share is not None
    assert share["data"]["permit_decision"] == "REQUIRED"
    assert share["data"]["permit_required"] is True
    assert '\"permit_decision\":\"REQUIRED\"' in server.render_share_page(share)
    building = next(
        item for item in checklist["items"] if item.get("category") == "building"
    )
    assert building["required"] is True


def _post_json(base: str, path: str, payload: dict, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


@pytest.mark.parametrize(
    ("surface", "path", "headers", "batch"),
    [
        ("ordinary-public", "/api/permit", {}, False),
        ("sample-demo", "/api/permit", {"X-Sample-Demo": "1"}, False),
        (
            "paid-api-v1",
            "/api/v1/permit",
            {"Authorization": "Bearer pa_test_single_projection"},
            False,
        ),
        ("batch", "/api/batch-permit", {"X-Sample-Demo": "1"}, True),
    ],
)
def test_active_core_http_api_surfaces_preserve_one_authoritative_projection(
    tmp_path, monkeypatch, surface, path, headers, batch
):
    server = _import_server(tmp_path, monkeypatch)
    case = _active_core_wrapped_result(monkeypatch)
    _install_real_active_core_endpoint_stubs(server, monkeypatch, case)
    request_row = {
        "job_type": case["job_type"],
        "job_category": case["job_category"],
        "city": case["city"],
        "state": case["state"],
        "zip_code": "85326",
    }
    request_payload = {"lookups": [request_row]} if batch else request_row

    with _LiveServer(server.Handler) as live:
        payload = _post_json(live.base, path, request_payload, headers)

    public = payload["results"][0] if batch else payload
    leaked = [
        key_path
        for key_path, key in _walk_keys(public)
        if key.startswith("_") or key.lower() in FORBIDDEN_PUBLIC_KEYS
    ]
    assert leaked == [], surface
    assert public["permit_decision"] == "REQUIRED", surface
    assert public["permit_required"] is True, surface
    assert public["permit_verdict"] == "YES", surface
    assert public["decision_source"] == "sealed_permit_rule_engine_envelope", surface
    assert public["coverage_status"] == "validated_exact_complete", surface
    assert len(public["sources"]) == len(case["sealed"]["sources"]), surface


def test_batch_endpoint_evidence_execution_cannot_return_raw_result(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    _install_endpoint_stubs(server, monkeypatch, evidence_allowed=True)
    request_row = {
        "job_type": "replace an existing residential asphalt shingle roof",
        "job_category": "residential",
        "city": "Buckeye",
        "state": "AZ",
        "zip_code": "85326",
    }

    with _LiveServer(server.Handler) as live:
        payload = _post_json(
            live.base,
            "/api/batch-permit",
            {"lookups": [request_row]},
            {"X-Sample-Demo": "1"},
        )

    assert payload["total"] == 1
    _assert_public_boundary(payload["results"][0])


def test_paid_api_v1_evidence_execution_cannot_return_raw_result(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    _install_endpoint_stubs(server, monkeypatch, evidence_allowed=True)
    monkeypatch.setattr(server, "validate_api_key", lambda _authorization: ("paid@example.test", {"id": 1}))
    monkeypatch.setattr(server, "is_paid_user", lambda _email: True)

    with _LiveServer(server.Handler) as live:
        payload = _post_json(
            live.base,
            "/api/v1/permit",
            {
                "job_type": "replace an existing residential asphalt shingle roof",
                "job_category": "residential",
                "city": "Buckeye",
                "state": "AZ",
                "zip_code": "85326",
            },
            {"Authorization": "Bearer pa_test_integrated_boundary"},
        )

    _assert_public_boundary(payload)


def test_source_contains_no_feature_flag_raw_customer_result_bypass():
    source = (Path(__file__).resolve().parents[1] / "api" / "server.py").read_text(encoding="utf-8")
    assert "result if evidence_allowed" not in source
    assert "if evidence_allowed else build_customer_permit_view_model" not in source


def test_customer_builder_preserves_evidence_fail_closed_policy_without_metadata_leak(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    internal = _dirty_internal_result()
    internal.update(
        {
            "permit_type": None,
            "permits_required": [],
            "apply_url": None,
            "fee_range": None,
            "approval_timeline": "Verified AHJ review timeline",
            "companion_reviews_triggers": "Verified fire review trigger",
            "claim_citations": [{"field": "approval_timeline", "internal": True}],
            "_evidence_pack": {
                "enabled": True,
                "matched_fields": ["approval_timeline", "companion_reviews_triggers"],
                "failed_closed_fields": ["permit_type", "apply_url", "fee_range"],
                "provider": "internal-pack-runtime",
            },
        }
    )
    original = copy.deepcopy(internal)
    monkeypatch.setattr(
        server,
        "build_customer_permit_view_model",
        lambda *_args, **_kwargs: _projected_customer_payload(),
    )

    public = server.build_customer_response_egress(
        internal,
        "replace an existing residential asphalt shingle roof",
        "Buckeye",
        "AZ",
        job_category="residential",
    )

    assert internal == original
    assert public["permit_type"] is None
    assert public["permits_required"] == []
    assert public["apply_url"] is None
    assert public["fee_range"] is None
    assert public["approval_timeline"] == "Verified AHJ review timeline"
    assert public["companion_reviews_triggers"] == "Verified fire review trigger"
    assert public["apply_path"]["support_level"] == "not available"
    assert "_evidence_pack" not in public
    assert "claim_citations" not in public
