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
