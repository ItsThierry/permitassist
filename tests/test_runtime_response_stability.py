from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / "api" / "server.py").read_text(encoding="utf-8")


def test_production_server_uses_threading_http_server():
    assert "from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler" in SERVER
    assert "server = ThreadingHTTPServer" in SERVER
    assert "server.daemon_threads = True" in SERVER
    assert "server = HTTPServer" not in SERVER


def test_send_json_handles_client_disconnect_without_traceback():
    send_json = SERVER.split("def send_json", 1)[1].split("def send_file", 1)[0]
    assert "redact_public_output" in send_json
    assert "BrokenPipeError" in send_json
    assert "ConnectionResetError" in send_json
    assert "ConnectionAbortedError" in send_json
    assert "client disconnected before JSON response completed" in send_json
    assert "self.wfile.write(body)" in send_json


def test_permit_lookup_research_path_is_serialized_to_avoid_edge_502_restart():
    assert "PERMIT_LOOKUP_CONCURRENCY_LIMIT" in SERVER
    assert "threading.BoundedSemaphore(PERMIT_LOOKUP_CONCURRENCY_LIMIT)" in SERVER
    permit_route = SERVER.split('if path == "/api/permit":', 1)[1].split('elif path == "/api/batch-permit":', 1)[0]
    research_pos = permit_route.index("result = research_permit(")
    acquire_pos = permit_route.index("PERMIT_LOOKUP_SEMAPHORE.acquire")
    release_pos = permit_route.index("PERMIT_LOOKUP_SEMAPHORE.release()")
    assert acquire_pos < research_pos < release_pos
    assert '"error": "server_busy"' in permit_route
    assert '"Retry-After": "10"' in permit_route


def test_public_no_header_limit_path_returns_before_lookup_semaphore():
    permit_route = SERVER.split('if path == "/api/permit":', 1)[1].split('elif path == "/api/batch-permit":', 1)[0]
    free_limit_pos = permit_route.index('"error": "free_limit_reached"')
    acquire_pos = permit_route.index("PERMIT_LOOKUP_SEMAPHORE.acquire")
    assert free_limit_pos < acquire_pos


def test_queued_public_lookup_rechecks_free_limit_before_research():
    permit_route = SERVER.split('if path == "/api/permit":', 1)[1].split('elif path == "/api/batch-permit":', 1)[0]
    acquire_pos = permit_route.index("PERMIT_LOOKUP_SEMAPHORE.acquire")
    recheck_pos = permit_route.index("used_now = get_effective_free_usage")
    research_pos = permit_route.index("result = research_permit(")
    assert acquire_pos < recheck_pos < research_pos
    assert "if not unlimited and not admin_bypass" in permit_route


def test_public_permit_response_is_customer_sanitized_after_internal_telemetry():
    permit_route = SERVER.split('if path == "/api/permit":', 1)[1].split('elif path == "/api/batch-permit":', 1)[0]
    telemetry_pos = permit_route.index('record_beta_event("lookup_completed"')
    sanitize_pos = permit_route.index("customer_result = result if evidence_allowed else build_customer_permit_view_model(result, job_type, city, state)")
    send_pos = permit_route.index("self.send_json(200, customer_result")
    assert telemetry_pos < sanitize_pos < send_pos
