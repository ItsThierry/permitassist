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
import urllib.error
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
    "decision_projection_sha256",
    "projection_sha256",
    "projection_digest",
    "regulated_projection_sha256",
    "payload_sha256",
    "sealed_projection",
    "verification_metadata",
    "verified_projection",
    "already_projected",
    "trusted_state",
    "is_verified",
    "verified",
    "projection_handle",
    "projection_handles",
    "verified_projection_handle",
    "verified_projection_handles",
    "handoff_handle",
    "handoff_handles",
    "snapshot_hash",
    "projection_hash",
    "packet_hash",
    "citation_hash",
    "request_fingerprint_sha256",
    "source_metadata",
    "decision_cell",
    "cell_id",
    "resolver",
    "customerdecisiondto",
}
OFFICIAL_URL = "https://www.buckeyeaz.gov/business/development-services/permit-center"
OFFICIAL_REQUIRED_QUOTE = (
    "Permits are also required, but not limited to fencing, pools, spas, patios, "
    "solar and re-roofs."
)


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
        if key.startswith("_")
        or key.lower() in FORBIDDEN_PUBLIC_KEYS
        or key.lower().endswith(("_sha256", "_hash", "_digest", "_fingerprint"))
    ]
    assert leaked == []


def _assert_untrusted_nonbinary(payload: dict) -> None:
    # These endpoint fixtures deliberately return ordinary caller-shaped
    # dictionaries. URLs, quotes, and provenance-looking fields inside such a
    # dictionary cannot mint binary authority.
    assert payload["permit_decision"] == "VERIFY"
    assert payload["permit_required"] is None
    assert payload["permit_verdict"] == "VERIFY"
    assert payload.get("permits_required") == []
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
        "sources": [{"title": "City permit center", "url": OFFICIAL_URL, "quote": OFFICIAL_REQUIRED_QUOTE}],
        "source_urls": [OFFICIAL_URL],
        "claim_citations": [{
            "field": "permit_decision",
            "value": "REQUIRED",
            "source_url": OFFICIAL_URL,
            "quoted_snippet": OFFICIAL_REQUIRED_QUOTE,
        }],
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
        "family_decisions": copy.deepcopy(rows),
        "applying_office": "City of Buckeye Development Services Permit Center",
        "apply_url": OFFICIAL_URL,
        "source_urls": [OFFICIAL_URL],
        "sources": [{
            "title": "City permit center",
            "url": OFFICIAL_URL,
            "quote": OFFICIAL_REQUIRED_QUOTE,
        }],
        "requirements": ["Submit the roofing scope and product information."],
        "documents_needed": ["Roof plan or manufacturer details if requested."],
        "inspections": ["Schedule required roof inspections with the permit office."],
        # Deliberately dirty projection: the final universal egress must remove
        # these even if an upstream helper accidentally reintroduces them.
        "claim_citations": [{
            "field": "permit_decision",
            "value": "REQUIRED",
            "source_url": OFFICIAL_URL,
            "quoted_snippet": OFFICIAL_REQUIRED_QUOTE,
        }],
        "permit_decision_contract": {"decision": "REQUIRED"},
        "quality_warnings": ["internal"],
        "_projection_debug": {"provider": "internal"},
        "nested": {
            "retrieval_metadata": {"query": "internal"},
            "snapshot_hash": "a" * 64,
            "deeper": {
                "projection_digest": "b" * 64,
                "verified_projection_handles": ["vp_" + "A" * 43],
                "continuity_token": "vp_" + "B" * 43,
            },
        },
        "projection_handle": "vp_" + "C" * 43,
        "packet_hash": "c" * 64,
        "request_fingerprint_sha256": "d" * 64,
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


def _permit_request_body() -> bytes:
    return json.dumps(
        {
            "job_type": "replace an existing residential asphalt shingle roof",
            "job_category": "residential",
            "city": "Buckeye",
            "state": "AZ",
            "zip_code": "85326",
        }
    ).encode("utf-8")


def _post_permit_http(base: str, headers: dict[str, str]) -> tuple[int, dict, dict]:
    request = urllib.request.Request(
        f"{base}/api/permit",
        data=_permit_request_body(),
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return (
                response.status,
                json.loads(response.read().decode("utf-8")),
                dict(response.headers.items()),
            )
    except urllib.error.HTTPError as exc:
        return (
            exc.code,
            json.loads(exc.read().decode("utf-8")),
            dict(exc.headers.items()),
        )


def _post_permit(base: str, headers: dict[str, str]) -> dict:
    status, payload, _response_headers = _post_permit_http(base, headers)
    assert status == 200
    return payload


def test_v15_admin_canary_hits_exact_abuse_budget_on_lookup_eleven(tmp_path, monkeypatch):
    """Characterize the V15 live failure: admin bypass did not bypass 10/min/IP."""
    server = _import_server(tmp_path, monkeypatch)
    real_check_rate_limit = server.check_rate_limit
    _install_endpoint_stubs(server, monkeypatch, evidence_allowed=True)
    monkeypatch.setattr(server, "check_rate_limit", real_check_rate_limit)
    monkeypatch.setattr(server, "is_unlimited_lookup_ip", lambda _ip: False)
    server._RATE_LIMIT_STATE.clear()
    headers = {
        "X-Admin-Token": "integrated-red-admin-token",
        "X-Forwarded-For": "8.8.8.8",
    }

    with _LiveServer(server.Handler) as live:
        first_ten = [_post_permit_http(live.base, headers) for _ in range(10)]
        eleventh = _post_permit_http(live.base, headers)

    assert [status for status, _payload, _headers in first_ten] == [200] * 10
    assert eleventh[0] == 429
    assert eleventh[1] == {
        "error": "rate_limit_exceeded",
        "message": "Too many requests. Please wait a minute and try again.",
    }
    assert int(eleventh[2]["Retry-After"]) >= 1
    assert len(server._RATE_LIMIT_STATE["8.8.8.8"]) == 10


def _canary_admin_token() -> str:
    # Must be >= 32 chars to satisfy the canary secret floor.
    return "integrated-red-canary-admin-token-v16-32b"


def _canary_attempt_id() -> str:
    return "3e7416b-v16-20260717"


def _canary_deployment_id() -> str:
    return "d8448821-b889-4fcc-9d0c-7fcdcb02ad85"


def _permit_request_dict() -> dict:
    return {
        "job_type": "replace an existing residential asphalt shingle roof",
        "job_category": "residential",
        "city": "Buckeye",
        "state": "AZ",
        "zip_code": "85326",
    }


def _signed_canary_headers(server, *, ordinal: int, data: dict | None = None) -> dict[str, str]:
    payload = data if data is not None else _permit_request_dict()
    attempt = _canary_attempt_id()
    deployment = _canary_deployment_id()
    signature = server._staging_canary_signature(
        _canary_admin_token(),
        attempt_id=attempt,
        deployment_id=deployment,
        ordinal=ordinal,
        data=payload,
    )
    return {
        "X-Admin-Token": _canary_admin_token(),
        "X-Forwarded-For": "8.8.8.8",
        server.CANARY_ATTEMPT_HEADER: attempt,
        server.CANARY_CONTROLLER_HEADER: server.CANARY_CONTROLLER_VALUE,
        server.CANARY_DEPLOYMENT_HEADER: deployment,
        server.CANARY_ORDINAL_HEADER: str(ordinal),
        server.CANARY_NO_CACHE_HEADER: "1",
        server.CANARY_SIGNATURE_HEADER: signature,
    }


def _install_canary_endpoint_stubs(server, monkeypatch, *, capture: list | None = None) -> None:
    real_check_rate_limit = server.check_rate_limit
    _install_endpoint_stubs(server, monkeypatch, evidence_allowed=False)
    monkeypatch.setattr(server, "ADMIN_TOKEN", _canary_admin_token())
    monkeypatch.setattr(server, "check_rate_limit", real_check_rate_limit)
    monkeypatch.setattr(server, "is_unlimited_lookup_ip", lambda _ip: False)
    server._RATE_LIMIT_STATE.clear()
    server._CANARY_NEXT_ORDINAL.clear()
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "staging")
    monkeypatch.setenv(server.CANARY_ATTEMPT_ENV, _canary_attempt_id())
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", _canary_deployment_id())

    def _research(*_args, **kwargs):
        if capture is not None:
            capture.append(
                {
                    "use_cache": kwargs.get("use_cache"),
                    "suppress_cache_write": kwargs.get("suppress_cache_write"),
                    "bypass_lookup_caches": kwargs.get("bypass_lookup_caches"),
                }
            )
        return copy.deepcopy(_dirty_internal_result())

    monkeypatch.setattr(server, "_research_permit_with_budget", _research)


def test_v16_signed_staging_canary_allows_exact_twelve_lookups_and_suppresses_cache(
    tmp_path, monkeypatch
):
    """Green path for the V15 11th-request failure under the exact 6+6 budget."""
    server = _import_server(tmp_path, monkeypatch)
    research_calls: list[dict] = []
    _install_canary_endpoint_stubs(server, monkeypatch, capture=research_calls)

    with _LiveServer(server.Handler) as live:
        statuses = []
        for ordinal in range(1, 13):
            status, payload, _headers = _post_permit_http(
                live.base,
                _signed_canary_headers(server, ordinal=ordinal),
            )
            statuses.append(status)
            _assert_public_boundary(payload)
            _assert_untrusted_nonbinary(payload)
        thirteenth = _post_permit_http(
            live.base,
            _signed_canary_headers(server, ordinal=13),
        )

    assert statuses == [200] * 12
    assert len(research_calls) == 12
    assert all(call["use_cache"] is False for call in research_calls)
    assert all(call["suppress_cache_write"] is True for call in research_calls)
    assert thirteenth[0] == 403
    assert thirteenth[1]["error"] == "canary_request_rejected"
    assert server._RATE_LIMIT_STATE.get("8.8.8.8") in (None, [])


def test_v16_canary_rejects_replay_wrong_body_and_bare_admin_stays_limited(
    tmp_path, monkeypatch
):
    server = _import_server(tmp_path, monkeypatch)
    _install_canary_endpoint_stubs(server, monkeypatch)

    with _LiveServer(server.Handler) as live:
        first = _post_permit_http(live.base, _signed_canary_headers(server, ordinal=1))
        replay = _post_permit_http(live.base, _signed_canary_headers(server, ordinal=1))
        wrong_body_headers = _signed_canary_headers(server, ordinal=2)
        # Signature is still bound to the canonical Buckeye body; mutate payload only.
        request = urllib.request.Request(
            f"{live.base}/api/permit",
            data=json.dumps(
                {
                    **_permit_request_dict(),
                    "city": "Phoenix",
                }
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", **wrong_body_headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                wrong_body = (
                    response.status,
                    json.loads(response.read().decode("utf-8")),
                    dict(response.headers.items()),
                )
        except urllib.error.HTTPError as exc:
            wrong_body = (
                exc.code,
                json.loads(exc.read().decode("utf-8")),
                dict(exc.headers.items()),
            )

        bare_admin_statuses = []
        for _ in range(11):
            status, payload, headers = _post_permit_http(
                live.base,
                {
                    "X-Admin-Token": _canary_admin_token(),
                    "X-Forwarded-For": "1.1.1.1",
                },
            )
            bare_admin_statuses.append(status)

    assert first[0] == 200
    assert replay[0] == 403
    assert replay[1]["error"] == "canary_request_rejected"
    assert wrong_body[0] == 403
    assert wrong_body[1]["error"] == "canary_request_rejected"
    assert bare_admin_statuses[:10] == [200] * 10
    assert bare_admin_statuses[10] == 429


def test_v16_canary_headers_without_staging_binding_are_rejected(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    _install_canary_endpoint_stubs(server, monkeypatch)
    monkeypatch.delenv(server.CANARY_ATTEMPT_ENV, raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")

    with _LiveServer(server.Handler) as live:
        status, payload, _headers = _post_permit_http(
            live.base,
            _signed_canary_headers(server, ordinal=1),
        )

    assert status == 403
    assert payload["error"] == "canary_request_rejected"


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
    _assert_untrusted_nonbinary(payload)


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
    assert "vp_" not in json.dumps(once, sort_keys=True)


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

    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_required"] is True
    required_rows = public.get("permits_required") or []
    assert required_rows
    assert {str(row.get("filing_family") or row.get("family") or "").lower() for row in required_rows} == {"building"}
    manifest = public.get("permit_manifest")
    assert isinstance(manifest, dict)
    assert manifest.get("schema_version") == "permit_manifest_v1"
    assert manifest.get("permit_decision") == "REQUIRED"
    assert "authority_tag" not in manifest
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

    assert public["permit_decision"] == "VERIFY"
    assert public["permit_required"] is None
    assert public["permit_verdict"] == "VERIFY"


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

    assert public["permit_decision"] == "VERIFY"
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

    assert public["permit_decision"] == "VERIFY"
    assert public["permit_required"] is None
    assert public["decision_source"] == "permit_rule_engine_integrity_fail_closed"


def test_repeat_and_server_side_handoff_revalidation_are_byte_stable(
    tmp_path, monkeypatch
):
    server = _import_server(tmp_path, monkeypatch)
    case = _active_core_wrapped_result(monkeypatch)
    customer_projection = server.build_customer_response_egress(
        case["wrapped"],
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )
    redacted_customer = server.redact_public_output(customer_projection)
    assert isinstance(redacted_customer, dict)
    external_plain_dict = server.json.loads(server.json.dumps(redacted_customer))
    handle = server.create_verified_projection_handoff(
        case["wrapped"],
        external_plain_dict,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )
    assert handle.startswith("vp_")
    snapshots = []

    for _ in range(3):
        verified = server.consume_verified_projection_handoff(
            handle,
            external_plain_dict,
            case["job_type"],
            case["city"],
            case["state"],
            job_category=case["job_category"],
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

    with server.sqlite3.connect(server.CACHE_DB) as conn:
        assert conn.execute("SELECT COUNT(*) FROM permit_cache").fetchone()[0] == 0
        assert conn.execute(
            "SELECT use_count FROM verified_projection_handoffs"
        ).fetchone()[0] == 3
    assert len(set(snapshots)) == 1
    expected_wire_projection = server.redact_public_output(customer_projection)
    assert server.json.loads(snapshots[0])["public"] == expected_wire_projection


def test_valid_handoff_with_non_ascii_customer_projection_remains_consumable(
    tmp_path, monkeypatch
):
    server = _import_server(tmp_path, monkeypatch)
    case = _active_core_wrapped_result(monkeypatch)
    customer_projection = server.build_customer_response_egress(
        case["wrapped"],
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )
    assert isinstance(customer_projection, dict)
    customer_projection = server.redact_public_output(customer_projection)
    assert isinstance(customer_projection.get("sources"), list)
    customer_projection["sources"][0]["title"] = (
        "Cañon City – División de Permisos"
    )
    customer_projection["requirements"] = [
        "Submit the city’s façade and re-roofing documents."
    ]
    external_plain_dict = server.json.loads(
        server.json.dumps(customer_projection, ensure_ascii=False)
    )
    handle = server.create_verified_projection_handoff(
        case["wrapped"],
        external_plain_dict,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )
    assert handle.startswith("vp_")

    verified = server.consume_verified_projection_handoff(
        handle,
        external_plain_dict,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )
    assert isinstance(verified, server._VerifiedCustomerProjection)
    assert verified["sources"][0]["title"] == "Cañon City – División de Permisos"
    assert verified["requirements"] == [
        "Submit the city’s façade and re-roofing documents."
    ]


def test_handoff_rejects_tamper_expiry_substitution_owner_mismatch_and_survives_core_flag_change(
    tmp_path, monkeypatch
):
    server = _import_server(tmp_path, monkeypatch)
    case = _active_core_wrapped_result(monkeypatch)
    customer = server.build_customer_response_egress(
        case["wrapped"],
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )
    redacted_customer = server.redact_public_output(customer)
    assert isinstance(redacted_customer, dict)
    customer = server.json.loads(server.json.dumps(redacted_customer))

    def issue(*, owner_scope=""):
        handle = server.create_verified_projection_handoff(
            case["wrapped"],
            customer,
            case["job_type"],
            case["city"],
            case["state"],
            job_category=case["job_category"],
            owner_scope=owner_scope,
        )
        assert handle.startswith("vp_")
        return handle

    mirror_handle = issue()
    tampered_customer = server.copy.deepcopy(customer)
    tampered_customer["permit_name"] = "SUBSTITUTED CUSTOMER VALUE"
    normalized = server.consume_verified_projection_handoff(
        mirror_handle,
        tampered_customer,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )
    if isinstance(customer.get("permit_manifest"), dict):
        # With an authenticated Manifest, mutable compatibility mirrors may be
        # repaired by returning the DB-authenticated snapshot.
        assert isinstance(normalized, server._VerifiedCustomerProjection)
        assert normalized["permit_name"] == customer["permit_name"]
        assert normalized["permit_name"] != "SUBSTITUTED CUSTOMER VALUE"

        manifest_handle = issue()
        tampered_manifest = server.copy.deepcopy(customer)
        tampered_manifest["permit_manifest"]["permit_decision"] = "NOT_REQUIRED"
        assert server.consume_verified_projection_handoff(
            manifest_handle,
            tampered_manifest,
            case["job_type"],
            case["city"],
            case["state"],
            job_category=case["job_category"],
        ) is None
    else:
        # Manifest mode is feature-gated. Without that smaller authenticated
        # authority object, exact public DTO equality is required and no mirror
        # repair is attempted.
        assert normalized is None

    handle = issue()
    assert server.consume_verified_projection_handoff(
        handle,
        customer,
        case["job_type"],
        "Phoenix",
        case["state"],
        job_category=case["job_category"],
    ) is None
    assert server.consume_verified_projection_handoff(
        "vp_" + "A" * 43,
        customer,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    ) is None

    owner_a = server._projection_owner_scope("owner-a@example.com")
    owner_b = server._projection_owner_scope("owner-b@example.com")
    owner_handle = issue(owner_scope=owner_a)
    assert server.consume_verified_projection_handoff(
        owner_handle,
        customer,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
        owner_scope=owner_b,
    ) is None
    assert isinstance(
        server.consume_verified_projection_handoff(
            owner_handle,
            customer,
            case["job_type"],
            case["city"],
            case["state"],
            job_category=case["job_category"],
            owner_scope=owner_a,
        ),
        server._VerifiedCustomerProjection,
    )

    tampered_use_count_handle = issue()
    tampered_use_count_sha = server.hashlib.sha256(
        tampered_use_count_handle.encode("utf-8")
    ).hexdigest()
    with server.sqlite3.connect(server.CACHE_DB) as conn:
        conn.execute(
            "UPDATE verified_projection_handoffs SET use_count=7 "
            "WHERE handle_sha256=?",
            [tampered_use_count_sha],
        )
    assert server.consume_verified_projection_handoff(
        tampered_use_count_handle,
        customer,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    ) is None

    tampered_row_handle = issue()
    tampered_row_sha = server.hashlib.sha256(
        tampered_row_handle.encode("utf-8")
    ).hexdigest()
    with server.sqlite3.connect(server.CACHE_DB) as conn:
        columns = [
            row[1]
            for row in conn.execute(
                'PRAGMA table_info("verified_projection_handoffs")'
            )
        ]
        assert not any(
            forbidden in " ".join(columns).lower()
            for forbidden in ("private", "raw_result", "core_envelope", "sealed_projection")
        )
        conn.execute(
            "UPDATE verified_projection_handoffs "
            "SET customer_projection_json='{}' WHERE handle_sha256=?",
            [tampered_row_sha],
        )
    assert server.consume_verified_projection_handoff(
        tampered_row_handle,
        customer,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    ) is None

    def reseal_row(handle, **updates):
        handle_sha = server.hashlib.sha256(handle.encode("utf-8")).hexdigest()
        with server.sqlite3.connect(server.CACHE_DB) as conn:
            raw = conn.execute(
                """
                SELECT customer_projection_json, customer_projection_sha256,
                       request_identity_json, owner_scope, created_at,
                       expires_at, max_uses, use_count
                FROM verified_projection_handoffs WHERE handle_sha256=?
                """,
                [handle_sha],
            ).fetchone()
            row = {
                "handle_sha256": handle_sha,
                "customer_projection_json": raw[0],
                "customer_projection_sha256": raw[1],
                "request_identity_json": raw[2],
                "owner_scope": raw[3],
                "created_at": raw[4],
                "expires_at": raw[5],
                "max_uses": raw[6],
                "use_count": raw[7],
            }
            row.update(updates)
            row_hmac = server._projection_handoff_row_hmac(row)
            conn.execute(
                """
                UPDATE verified_projection_handoffs
                SET customer_projection_json=?, customer_projection_sha256=?,
                    request_identity_json=?, owner_scope=?, created_at=?,
                    expires_at=?, max_uses=?, use_count=?, row_hmac_sha256=?
                WHERE handle_sha256=?
                """,
                [
                    row["customer_projection_json"],
                    row["customer_projection_sha256"],
                    row["request_identity_json"],
                    row["owner_scope"],
                    row["created_at"],
                    row["expires_at"],
                    row["max_uses"],
                    row["use_count"],
                    row_hmac,
                    handle_sha,
                ],
            )

    expired_handle = issue()
    reseal_row(
        expired_handle,
        expires_at=(server.utc_now() - server.timedelta(seconds=1)).isoformat(),
    )
    assert server.consume_verified_projection_handoff(
        expired_handle,
        customer,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    ) is None

    bounded_handle = issue()
    reseal_row(bounded_handle, max_uses=1)
    assert isinstance(
        server.consume_verified_projection_handoff(
            bounded_handle,
            customer,
            case["job_type"],
            case["city"],
            case["state"],
            job_category=case["job_category"],
        ),
        server._VerifiedCustomerProjection,
    )
    assert server.consume_verified_projection_handoff(
        bounded_handle,
        customer,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    ) is None

    core_flag_independent_handle = issue()
    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_CORE", "off")
    assert isinstance(
        server.consume_verified_projection_handoff(
            core_flag_independent_handle,
            customer,
            case["job_type"],
            case["city"],
            case["state"],
            job_category=case["job_category"],
        ),
        server._VerifiedCustomerProjection,
    )


def test_http_share_and_checklist_without_handoff_fail_closed_even_with_cache_row(
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
    assert share["data"]["permit_decision"] == "VERIFY"
    assert share["data"]["permit_required"] is None
    assert '\"permit_decision\":\"VERIFY\"' in server.render_share_page(share)
    assert not any(item.get("required") is True for item in checklist["items"])
    assert "decision_projection_sha256" not in json.dumps(checklist, sort_keys=True)


def test_evidence_no_cache_api_share_report_and_checklist_keep_authoritative_projection(
    tmp_path, monkeypatch
):
    """No-write evidence lookups require an opaque server-side continuity handoff."""
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

    with _LiveServer(server.Handler) as live:
        api_payload, api_headers = _post_json_with_response_headers(
            live.base,
            "/api/permit",
            request_row,
            {"X-Sample-Demo": "1"},
        )
        projection_handle = api_headers.get(
            "X-PermitAssist-Projection-Handle", ""
        )
        continuity_headers = (
            {"X-PermitAssist-Projection-Handle": projection_handle}
            if projection_handle
            else {}
        )
        share_reply = _post_json(
            live.base,
            "/api/share",
            {**request_row, "result": api_payload},
            continuity_headers,
        )
        checklist = _post_json(
            live.base,
            "/api/checklist",
            {**request_row, "result": api_payload},
            continuity_headers,
        )
        report_projection = server.consume_verified_projection_handoff(
            projection_handle,
            api_payload,
            case["job_type"],
            case["city"],
            case["state"],
            job_category=case["job_category"],
        )
        assert report_projection is not None

    with server.sqlite3.connect(server.CACHE_DB) as conn:
        assert conn.execute("SELECT COUNT(*) FROM permit_cache").fetchone()[0] == 0

    share = server.get_share(share_reply["slug"])
    assert share is not None
    assert api_payload["permit_decision"] == "REQUIRED"
    assert api_payload["permit_required"] is True
    # Exact rejected SHA 2bde136 fails here: no cache row means the plain DTO is
    # reprojected and the authoritative binary decision is demoted.
    assert share["data"]["permit_decision"] == api_payload["permit_decision"]
    assert share["data"]["permit_required"] is api_payload["permit_required"]
    report_html = server.render_share_page(share)
    assert '\"permit_decision\":\"REQUIRED\"' in report_html
    white_label_html = server.render_white_label_report_html(
        {**request_row, "result": report_projection}
    )
    assert "Permit decision" in white_label_html
    assert str(report_projection["permit_kind"]) in white_label_html
    assert projection_handle not in white_label_html
    building = next(
        item for item in checklist["items"] if item.get("category") == "building"
    )
    assert building["required"] is True

    assert projection_handle.startswith("vp_")
    cookie = api_headers.get("Set-Cookie", "")
    assert f"pa_vph={projection_handle}" in cookie
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert "Secure" not in cookie
    ipv6_loopback_cookie = server._projection_handoff_response_headers(
        projection_handle, {"Host": "[::1]:8765"}
    )["Set-Cookie"]
    assert "HttpOnly" in ipv6_loopback_cookie
    assert "SameSite=Strict" in ipv6_loopback_cookie
    assert "Secure" not in ipv6_loopback_cookie
    production_cookie = server._projection_handoff_response_headers(
        projection_handle, {"Host": "permitassist.io"}
    )["Set-Cookie"]
    assert "HttpOnly" in production_cookie and "Secure" in production_cookie
    assert projection_handle not in json.dumps(api_payload, sort_keys=True)
    assert projection_handle not in report_html
    assert "decision_projection_sha256" not in json.dumps(
        {"api": api_payload, "share": share, "checklist": checklist},
        sort_keys=True,
    )


def _post_json_with_response_headers(
    base: str, path: str, payload: dict, headers: dict[str, str]
) -> tuple[dict, dict[str, str]]:
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
        return (
            json.loads(response.read().decode("utf-8")),
            dict(response.headers.items()),
        )


def _post_json(base: str, path: str, payload: dict, headers: dict[str, str]) -> dict:
    response_payload, _response_headers = _post_json_with_response_headers(
        base, path, payload, headers
    )
    return response_payload


def test_authenticated_http_continuity_preserves_owner_bound_projection_across_consumers(
    tmp_path, monkeypatch
):
    server = _import_server(tmp_path, monkeypatch)
    case = _active_core_wrapped_result(monkeypatch)
    _install_real_active_core_endpoint_stubs(server, monkeypatch, case)
    auth_session = "owned-session-token"
    user_email = "owner@example.test"
    monkeypatch.setattr(
        server,
        "validate_session_token",
        lambda token: user_email if token == auth_session else None,
    )
    monkeypatch.setattr(server, "is_paid_user", lambda email: email == user_email)
    monkeypatch.setattr(server, "save_email_capture", lambda *_args, **_kwargs: None)
    emailed_results = []

    def capture_email(_email, _job, _city, _state, result):
        emailed_results.append(copy.deepcopy(result))
        return True

    monkeypatch.setattr(server, "send_email_report", capture_email)
    request_row = {
        "job_type": case["job_type"],
        "job_category": case["job_category"],
        "city": case["city"],
        "state": case["state"],
        "zip_code": "85326",
    }
    auth_headers = {
        "X-Session-Token": auth_session,
        "X-Sample-Demo": "1",
    }

    with _LiveServer(server.Handler) as live:
        public, response_headers = _post_json_with_response_headers(
            live.base,
            "/api/permit",
            request_row,
            auth_headers,
        )
        projection_handle = response_headers[
            server.VERIFIED_PROJECTION_HANDLE_HEADER
        ]
        continuity_headers = {
            "X-Session-Token": auth_session,
            server.VERIFIED_PROJECTION_HANDLE_HEADER: projection_handle,
        }
        checklist = _post_json(
            live.base,
            "/api/checklist",
            {**request_row, "result": public},
            continuity_headers,
        )
        share = _post_json(
            live.base,
            "/api/share",
            {**request_row, "result": public},
            continuity_headers,
        )
        emailed = _post_json(
            live.base,
            "/api/email-report",
            {
                "email": "recipient@example.test",
                "job": case["job_type"],
                "job_category": case["job_category"],
                "city": case["city"],
                "state": case["state"],
                "data": public,
            },
            continuity_headers,
        )

    assert public["permit_decision"] == "REQUIRED"
    stored_share = server.get_share(share["slug"])
    assert stored_share is not None
    assert stored_share["data"]["permit_decision"] == "REQUIRED"
    building = next(
        item for item in checklist["items"] if item.get("category") == "building"
    )
    assert building["required"] is True
    assert emailed == {"sent": True}
    assert emailed_results[-1]["permit_decision"] == "REQUIRED"
    with server.sqlite3.connect(server.CACHE_DB) as conn:
        use_count = conn.execute(
            "SELECT use_count FROM verified_projection_handoffs "
            "WHERE handle_sha256=?",
            [server.hashlib.sha256(projection_handle.encode("utf-8")).hexdigest()],
        ).fetchone()[0]
    assert use_count == 3


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
        payload, response_headers = _post_json_with_response_headers(
            live.base, path, request_payload, headers
        )

    public = payload["results"][0] if batch else payload
    if batch:
        projection_handles = json.loads(
            response_headers[server.VERIFIED_PROJECTION_HANDLES_HEADER]
        )
        assert len(projection_handles) == 1
        projection_handle = projection_handles[0]
    else:
        projection_handle = response_headers[
            server.VERIFIED_PROJECTION_HANDLE_HEADER
        ]
    assert projection_handle.startswith("vp_"), surface
    assert projection_handle not in json.dumps(payload, sort_keys=True), surface
    leaked = [
        key_path
        for key_path, key in _walk_keys(public)
        if key.startswith("_")
        or key.lower() in FORBIDDEN_PUBLIC_KEYS
        or key.lower().endswith(("_sha256", "_hash", "_digest", "_fingerprint"))
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
    _assert_untrusted_nonbinary(payload["results"][0])


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
    _assert_untrusted_nonbinary(payload)


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
    assert public["approval_timeline"] == "Verified issuing authority review timeline"
    assert public["companion_reviews_triggers"] == "Verified fire review trigger"
    assert public["apply_path"]["support_level"] == "not available"
    assert "_evidence_pack" not in public
    assert "claim_citations" not in public
