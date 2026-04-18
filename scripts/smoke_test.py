#!/usr/bin/env python3
"""PermitAssist smoke test.

Runs lightweight checks that do not require live OpenAI/Tavily lookups.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
FRONTEND_DIR = ROOT / "frontend"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def check_frontend_js() -> None:
    for html in [FRONTEND_DIR / "index.html", FRONTEND_DIR / "account.html", FRONTEND_DIR / "help.html", FRONTEND_DIR / "review.html", FRONTEND_DIR / "pricing.html"]:
        text = html.read_text()
        scripts = "\n".join(re.findall(r"<script>(.*?)</script>", text, flags=re.S))
        if not scripts.strip():
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tmp:
            tmp.write(scripts)
            tmp_path = tmp.name
        try:
            proc = subprocess.run(["node", "--check", tmp_path], capture_output=True, text=True)
            if proc.returncode != 0:
                raise AssertionError(f"JS syntax failed for {html.name}: {proc.stderr}")
        finally:
            os.unlink(tmp_path)


def check_frontend_content() -> None:
    index = (FRONTEND_DIR / "index.html").read_text()
    account = (FRONTEND_DIR / "account.html").read_text()
    help_page = (FRONTEND_DIR / "help.html").read_text()
    review_page = (FRONTEND_DIR / "review.html").read_text()

    required_index = [
        "Official Sources",
        "Print / PDF",
        "downloadReport()",
        "Job Address",
        "Jurisdiction Match",
        "Next Steps",
        "Foreman Brief",
        "cosmetic cabinets/counters only, no plumbing/electrical/walls",
        "Log in to use Job Tracker",
        "See a sample result",
        "/pricing",
    ]
    for needle in required_index:
        assert needle in index, f"Missing index feature: {needle}"

    required_account = [
        "manageSubscription()",
        "Manage Subscription",
    ]
    for needle in required_account:
        assert needle in account, f"Missing account feature: {needle}"

    assert "Help" in help_page and "How It Works" in help_page
    assert "PermitAssist Review Queue" in review_page


def http_request(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    data: dict | None = None,
    follow_redirects: bool = True,
) -> tuple[int, str, object]:
    payload = None
    req_headers = headers.copy() if headers else {}
    if data is not None:
        payload = json.dumps(data).encode()
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, headers=req_headers, data=payload, method=method)
    opener = urllib.request.build_opener() if follow_redirects else urllib.request.build_opener(NoRedirectHandler)
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.getcode(), resp.read().decode(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers



def http_get(url: str, headers: dict | None = None) -> tuple[int, str]:
    status, body, _ = http_request(url, headers=headers)
    return status, body



def check_backend_helpers() -> None:
    sys.path.insert(0, str(API_DIR))
    server = load_module("pa_server", API_DIR / "server.py")
    research = load_module("pa_research", API_DIR / "research_engine.py")
    server.resend_send = lambda *args, **kwargs: True

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        capture_csv = db_path + ".emails.csv"
        server.CACHE_DB = db_path
        server.EMAILS_CSV = capture_csv
        research.CACHE_DB = db_path
        server.init_db()
        research.init_cache()

        server.get_or_create_user("owner@example.com")
        server.get_or_create_user("crew@example.com")

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO team_members (owner_email, member_email, joined_at) VALUES (?,?,?)",
            ("owner@example.com", "crew@example.com", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        job = server.create_job("owner@example.com", "Roof replacement", "Houston", "TX", status="planning")
        assert server.user_can_access_job(job["id"], "owner@example.com")
        assert server.user_can_access_job(job["id"], "crew@example.com")
        assert server.update_job(job["id"], {"status": "approved"}, email="crew@example.com")
        rows = server.list_jobs("crew@example.com")
        assert rows and rows[0]["status"] == "approved"

        reminder = server.upsert_permit_reminder(
            "owner@example.com", "Roof replacement", "Houston", "TX", "2026-05-20"
        )
        assert reminder["id"] and reminder["remind_at"]

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO feedback (job_type, city, state, issue, submitted_at) VALUES (?,?,?,?,?)",
            ("Roof replacement", "Houston", "TX", "wrong fee", "2026-04-13T00:00:00"),
        )
        conn.execute(
            "INSERT INTO permit_cache (cache_key, job_type, city, state, zip_code, result_json, created_at, hits) VALUES (?,?,?,?,?,?,?,?)",
            (
                "test-key",
                "Roof replacement",
                "Houston",
                "TX",
                "77001",
                json.dumps({"needs_review": True, "missing_fields": ["fee_range"], "confidence": "low", "confidence_reason": "Needs review for fee_range"}),
                "2026-04-13T00:00:00",
                0,
            ),
        )
        conn.commit()
        conn.close()
        queue = server.get_review_queue(limit=10)
        assert queue["counts"]["feedback"] == 1
        assert queue["counts"]["needs_review"] == 1

        share_payload = {
            "permit_verdict": "YES",
            "fee_range": "$120",
            "apply_phone": "555-555-5555",
            "applying_office": "City Permit Office",
            "apply_address": "123 Main",
            "approval_timeline": {"simple": "2 days"},
            "permits_required": [{"permit_type": "Building Permit", "required": True}],
            "sources": ["https://city.example/permit"],
        }
        shared_html = server.render_share_page(
            {
                "job_type": "Roof replacement",
                "city": "Houston",
                "state": "TX",
                "data": share_payload,
            }
        )
        assert "🔗 Sources" in shared_html and "city.example/permit" in shared_html

        clean = research.clean_summary_text(
            'Minimum fee. * Facebook "Click to share with Facebook"). Print; "Click to print this page") Useful info.'
        )
        assert "Facebook" not in clean and "print this page" not in clean.lower()
        missing = research.compute_missing_fields(
            {
                "permit_verdict": "YES",
                "permits_required": [],
                "applying_office": "",
                "fee_range": "",
                "approval_timeline": {},
                "inspections": [],
            }
        )
        assert "permit_details" in missing and "fee_range" in missing

        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE users SET plan='team' WHERE email=?", ("owner@example.com",))
        conn.commit()
        conn.close()
        owner_session = server.create_session_token("owner@example.com")

        httpd = server.HTTPServer(("127.0.0.1", 0), server.Handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            status, body = http_get(f"http://127.0.0.1:{port}/")
            assert status == 200 and "Job Address" in body and "Official Sources" in body
            status, body = http_get(f"http://127.0.0.1:{port}/help")
            assert status == 200 and "How It Works" in body
            status, body = http_get(f"http://127.0.0.1:{port}/index.html.backup")
            assert status == 404
            status, body = http_get(f"http://127.0.0.1:{port}/login")
            assert status == 200 and "No password needed" in body
            status, body = http_get(f"http://127.0.0.1:{port}/account")
            assert status == 200 and "Manage Subscription" in body
            status, body = http_get(f"http://127.0.0.1:{port}/pricing")
            assert status == 200 and "$19" in body and "$49" in body
            status, body = http_get(f"http://127.0.0.1:{port}/review")
            assert status == 200

            status, body = http_get(f"http://127.0.0.1:{port}/api/account")
            assert status == 401 and "Not authenticated" in body
            status, body, _ = http_request(
                f"http://127.0.0.1:{port}/api/account",
                headers={"X-Session-Token": owner_session},
            )
            account_data = json.loads(body)
            assert status == 200 and account_data["email"] == "owner@example.com"
            assert "crew@example.com" in account_data["team_members"]

            status, body = http_get(f"http://127.0.0.1:{port}/review")
            assert status == 200 and "PermitAssist Review Queue" in body
            status, body, _ = http_request(
                f"http://127.0.0.1:{port}/api/share",
                method="POST",
                data={
                    "job_type": "Roof replacement",
                    "city": "Houston",
                    "state": "TX",
                    "result": share_payload,
                },
            )
            share_data = json.loads(body)
            assert status == 200 and share_data["expires_days"] == 30 and share_data["slug"]
            status, body = http_get(f"http://127.0.0.1:{port}/s/{share_data['slug']}")
            assert status == 200 and "city.example/permit" in body and "Roof replacement" in body
            conn = sqlite3.connect(db_path)
            conn.execute(
                "UPDATE shared_results SET expires_at=? WHERE slug=?",
                ((server.utc_now() - server.timedelta(days=1)).isoformat(), share_data["slug"]),
            )
            conn.commit()
            conn.close()
            status, body = http_get(f"http://127.0.0.1:{port}/s/{share_data['slug']}")
            assert status == 410 and "Link Expired" in body
            status, body, _ = http_request(
                f"http://127.0.0.1:{port}/api/team/invite",
                method="POST",
                headers={"X-Session-Token": owner_session},
                data={"invite_email": "newcrew@example.com"},
            )
            invite_data = json.loads(body)
            assert status == 200 and invite_data["invited"] is True
            status, body, _ = http_request(
                f"http://127.0.0.1:{port}/api/team/invite",
                method="POST",
                headers={"X-Session-Token": owner_session},
                data={"invite_email": "crew3@example.com"},
            )
            assert status == 200
            status, body, _ = http_request(
                f"http://127.0.0.1:{port}/api/team/invite",
                method="POST",
                headers={"X-Session-Token": owner_session},
                data={"invite_email": "crew4@example.com"},
            )
            assert status == 400 and "seat limit" in body.lower()
            status, body, _ = http_request(
                f"http://127.0.0.1:{port}/api/expiry-reminder",
                method="POST",
                data={
                    "email": "owner@example.com",
                    "job_type": "Roof replacement",
                    "city": "Houston",
                    "state": "TX",
                    "expiry_date": "2026-05-20",
                },
            )
            reminder_data = json.loads(body)
            assert status == 200 and reminder_data["saved"] is True and reminder_data["reminder_id"]
            status, body = http_get(f"http://127.0.0.1:{port}/api/jobs")
            assert status == 401 and "Not authenticated" in body
            status, body = http_get(f"http://127.0.0.1:{port}/api/billing-portal")
            assert status == 401 and "Not authenticated" in body
            status, body, _ = http_request(
                f"http://127.0.0.1:{port}/api/billing-portal",
                headers={"X-Session-Token": owner_session},
            )
            assert status == 400 and "No Stripe customer" in body
            status, body = http_get(f"http://127.0.0.1:{port}/api/verify-magic")
            assert status == 400 and "missing a token" in body
            status, body = http_get(f"http://127.0.0.1:{port}/api/verify-magic?token=BAD123")
            assert status == 400 and "not recognised" in body
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO magic_tokens (token, email, expires_at) VALUES (?,?,?)",
                ("GOOD12", "owner@example.com", (server.utc_now() + server.timedelta(minutes=10)).isoformat()),
            )
            conn.commit()
            conn.close()
            status, _, headers = http_request(
                f"http://127.0.0.1:{port}/api/verify-magic?token=GOOD12",
                follow_redirects=False,
            )
            assert status == 302 and headers.get("Location", "").startswith("/?t=")
            status, body = http_get(f"http://127.0.0.1:{port}/api/review-queue")
            assert status == 403 and "not configured" in body.lower()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
    finally:
        os.unlink(db_path)
        if os.path.exists(capture_csv):
            os.unlink(capture_csv)


def main() -> int:
    check_frontend_js()
    check_frontend_content()
    check_backend_helpers()
    print(json.dumps({"ok": True, "checks": ["frontend_js", "frontend_content", "backend_helpers"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
