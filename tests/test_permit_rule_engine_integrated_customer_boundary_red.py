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
