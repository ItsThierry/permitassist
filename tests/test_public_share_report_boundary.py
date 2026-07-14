import importlib
import json
import re
import sys
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest
from bs4 import BeautifulSoup


def _import_server(tmp_path, monkeypatch):
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
    # Other contract tests intentionally pop/reload api.server with stubs.
    # Import a fresh module here so this suite is order-independent.
    sys.modules.pop("api.server", None)
    api_pkg = sys.modules.get("api")
    if api_pkg is not None and hasattr(api_pkg, "server"):
        delattr(api_pkg, "server")
    server = importlib.import_module("api.server")
    setattr(server, "CACHE_DB", str(tmp_path / "cache.db"))
    setattr(server, "DATA_DIR", str(tmp_path))
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


REPORT_DATA_RE = re.compile(
    r'<script\s+id=["\']report-data["\']\s+type=["\']application/json["\']>(.*?)</script>',
    re.S,
)
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
}
XSS_MARKER = (
    "<img src=x onerror=alert(1)>"
    "<svg onload=alert(1)>"
    "<script>alert(1)</script>"
    "</script><script>window.__PA_XSS=1</script>"
    "\u2028\u2029"
    "\"'`&<>/"
)


def _extract_report_payload(html: str) -> dict:
    match = REPORT_DATA_RE.search(html)
    assert match, "report-data JSON script tag missing"
    return json.loads(match.group(1))


def _walk_keys(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield child_path, str(key)
            yield from _walk_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_keys(child, f"{path}[{index}]")


def _assert_public_payload_has_no_internal_keys(payload: dict):
    leaked = [
        path
        for path, key in _walk_keys(payload)
        if key.startswith("_") or key in FORBIDDEN_PUBLIC_KEYS
    ]
    assert leaked == []


LOCAL_SOURCE_BY_CITY_STATE = {
    ("Dallas", "TX"): "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/default.aspx",
    ("Austin", "TX"): "https://www.austintexas.gov/department/development-services",
    ("San Jose", "CA"): "https://www.sanjoseca.gov/your-government/departments-offices/planning-building-code-enforcement/building-division",
    ("Los Angeles", "CA"): "https://www.ladbs.org/services/core-services/plan-check-permit",
    ("Phoenix", "AZ"): "https://www.phoenix.gov/pdd/development/permits",
    ("Miami", "FL"): "https://www.miami.gov/Services/Permits",
    ("Denver", "CO"): "https://www.denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Community-Planning-and-Development/Permits",
    ("Seattle", "WA"): "https://www.seattle.gov/sdci/permits",
    ("Chicago", "IL"): "https://www.chicago.gov/city/en/depts/bldgs/provdrs/permits.html",
}


def _local_source_url(city: str, state: str) -> str:
    return LOCAL_SOURCE_BY_CITY_STATE[(city, state)]


def _base_result(permit_kind: str, *, marker: str = "", source_url: str = "https://www.miami.gov/Services/Permits") -> dict:
    text = marker or permit_kind
    return {
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_decision": "REQUIRED",
        "permit_kind": permit_kind,
        "customer_headline": f"Permit required: {text}",
        "customer_next_step": f"File the {text} before starting work.",
        "permit_name": text,
        "permit_type": permit_kind,
        "primary_permit": {"permit_type": permit_kind, "portal_selection": text},
        "permits_required": [{"permit_type": permit_kind}],
        "companion_permits": [{"permit_type": "Electrical", "trigger": text}],
        "fee_range": text,
        "approval_timeline": {"simple": text, "complex": text},
        "requirements": [text],
        "documents_needed": [text],
        "what_to_bring": [text],
        "inspections": [text, {"stage": text, "timing": text}],
        "job_summary": text,
        "permit_summary": text,
        "applying_office": text,
        "apply_address": text,
        "apply_phone": text,
        "apply_path": {"label": text, "likely_path": text},
        "sources": [{"title": text, "url": source_url, "snippet": text}],
        "source_urls": [source_url],
        "quality_warnings": ["internal warning must not be public"],
        "permit_decision_contract": {
            "permit_decision": "REQUIRED",
            "permit_kind": permit_kind,
            "source_evidence_floor": {"status": "satisfied", "has_source_backed_evidence": True},
            "exact_name_status": "unverified",
            "exact_apply_url_status": "unverified",
            "customer_next_step": f"File the {permit_kind} before starting work.",
        },
        "source_evidence_floor": {"status": "satisfied"},
        "exact_name_status": "unverified",
        "exact_apply_url_status": "unverified",
        "needs_review": True,
        "_private_debug": "secret",
        "retrieval_metadata": {"query": "internal"},
    }


@pytest.mark.parametrize(
    ("job_type", "city", "state", "permit_kind"),
    [
        ("Residential kitchen remodel", "Dallas", "TX", "Residential Building / Remodel"),
        ("Commercial restaurant tenant improvement", "Austin", "TX", "Commercial Building / Tenant Improvement"),
        ("Rooftop solar PV install", "San Jose", "CA", "Solar PV"),
        ("Garage ADU conversion", "Los Angeles", "CA", "ADU / Accessory Dwelling Unit"),
        ("Electrical panel and HVAC replacement", "Phoenix", "AZ", "MEP / Trade"),
        ("Roof replacement", "Miami", "FL", "Roofing"),
        ("Fire alarm and sprinkler alteration", "Denver", "CO", "Fire / Life Safety"),
        ("Zoning land-use change review", "Seattle", "WA", "Zoning / Land Use"),
        ("Restaurant TI with hood and grease interceptor", "Chicago", "IL", "Commercial Building / Tenant Improvement"),
    ],
)
def test_public_share_report_payload_is_allowlisted_across_scope_matrix(
    tmp_path, monkeypatch, job_type, city, state, permit_kind
):
    server = _import_server(tmp_path, monkeypatch)

    html = server.render_share_page(
        {
            "data": _base_result(permit_kind, source_url=_local_source_url(city, state)),
            "job_type": job_type,
            "city": city,
            "state": state,
        }
    )

    payload = _extract_report_payload(html)
    _assert_public_payload_has_no_internal_keys(payload)
    public_result = payload["share"]["data"]
    assert public_result["permit_decision"] == "REQUIRED"
    assert public_result["permit_kind"] == permit_kind
    assert public_result["customer_headline"].startswith("Permit required:")
    assert public_result["customer_next_step"]


def test_report_json_embedding_prevents_script_breakout_and_markup_payloads(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    html = server.render_share_page(
        {
            "data": _base_result("Commercial Building / Tenant Improvement", marker=XSS_MARKER, source_url=_local_source_url("Dallas", "TX")),
            "job_type": XSS_MARKER,
            "city": "Dallas",
            "state": "TX",
        }
    )

    payload = _extract_report_payload(html)
    payload_text = json.dumps(payload, ensure_ascii=False)
    assert "window.__PA_XSS=1" in payload_text
    assert "<img src=x onerror=alert(1)>" in payload_text
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script")
    assert [script.get("id") for script in scripts].count("report-data") == 1
    # Static application script + JSON data script only; payload must not create script tags.
    assert len(scripts) == 2
    assert not soup.find_all("img")
    assert not soup.find_all("svg")
    assert "<img src=x onerror=alert(1)>" not in html
    assert "<svg onload=alert(1)>" not in html
    assert "</script><script>window.__PA_XSS=1</script>" not in html


def test_report_template_does_not_render_payload_with_raw_inner_html():
    template = (Path(__file__).parents[1] / "frontend" / "report.html").read_text(encoding="utf-8")

    assert "app.innerHTML" not in template
    assert ".insertAdjacentHTML" not in template


def test_report_template_has_no_static_customer_uncertainty_literals():
    template = (Path(__file__).parents[1] / "frontend" / "report.html").read_text(encoding="utf-8")

    forbidden = re.compile(r"\b(?:UNKNOWN|FAIL[_ -]?CLOSED|likely|maybe|probably)\b", re.I)
    hits = [
        match.group(0)
        for match in forbidden.finditer(template)
        if "companion" not in template[max(0, match.start() - 120): match.end() + 120].lower()
    ]
    assert hits == []


def _post_json(url: str, body: dict):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


@pytest.mark.parametrize(
    ("job_type", "city", "state", "permit_kind"),
    [
        ("Residential kitchen remodel", "Dallas", "TX", "Residential Building / Remodel"),
        ("Commercial restaurant tenant improvement", "Austin", "TX", "Commercial Building / Tenant Improvement"),
    ],
)
def test_actual_share_report_http_path_uses_public_safe_payload(
    tmp_path, monkeypatch, job_type, city, state, permit_kind
):
    server = _import_server(tmp_path, monkeypatch)

    with _LiveServer(server.Handler) as live:
        status, body = _post_json(
            f"{live.base}/api/share",
            {
                "job_type": job_type,
                "city": city,
                "state": state,
                "result": _base_result(permit_kind, marker=XSS_MARKER, source_url=_local_source_url(city, state)),
            },
        )
        assert status == 200
        slug = json.loads(body)["slug"]
        report_status, html = _get(f"{live.base}/report/{slug}")

    assert report_status == 200
    payload = _extract_report_payload(html)
    _assert_public_payload_has_no_internal_keys(payload)
    assert payload["share"]["data"]["permit_decision"] == "REQUIRED"
    assert payload["share"]["data"]["permit_kind"] == permit_kind
    soup = BeautifulSoup(html, "html.parser")
    assert len(soup.find_all("script")) == 2
    assert not soup.find_all("img")
    assert not soup.find_all("svg")


def test_verified_share_round_trip_preserves_live_shaped_public_permit_rows(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    raw = _base_result(
        "Roofing",
        source_url="https://www.buckeyeaz.gov/business/development-services/permit-center",
    )
    result = server.build_customer_response_egress(
        raw,
        "replace the existing residential asphalt shingle roof",
        "Buckeye",
        "AZ",
    )
    result["permits_required"] = [
        {
            "ahj_name": "Buckeye permit office",
            "ahj_type": "planning_department",
            "application_authority_name": "Buckeye permit office",
            "apply_url_status": "needs_verification",
            "approval_type": "Historic Preservation Review",
            "decision": "CONDITIONAL_REQUIRED",
            "filing_family": "historic_review",
            "kind": "Roofing",
            "notes": "Confirm the exact portal category with the listed permit office before filing.",
            "permit_type": "Historic Preservation Review",
            "provenance": "universal_filing_packet_reconciler",
            "required": True,
            "row_category": "conditional_review",
            "source_status": "source_needed",
            "trigger_signal_ids": ["historic_or_planning_overlay"],
        },
        {
            "kind": "Roofing",
            "permit_type": "Roofing / Building Permit",
            "required": True,
        },
    ]
    expected = server.project_customer_response_egress(result)

    slug = server.create_share(
        "replace the existing residential asphalt shingle roof",
        "Buckeye",
        "AZ",
        result,
    )
    share = server.get_share(slug)
    html = server.render_share_page(share)
    embedded = _extract_report_payload(html)

    _assert_public_payload_has_no_internal_keys(share)
    _assert_public_payload_has_no_internal_keys(embedded)
    expected_embedded = server.to_public_share_payload({"data": expected}, {})["share"]["data"]
    assert share["data"] == expected
    assert embedded["share"]["data"] == expected_embedded
    assert len(embedded["share"]["data"]["permits_required"]) == 2
    assert {
        row["permit_type"] for row in embedded["share"]["data"]["permits_required"]
    } == {"Historic Preservation Review", "Roofing / Building Permit"}
