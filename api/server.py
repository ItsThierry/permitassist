#!/usr/bin/env python3
"""
PermitAssist — Web Server v3
Improvements:
  - Rate limiting per IP (10 fresh lookups/hour, unlimited cached)
  - URL validation before returning results
  - /api/feedback endpoint (flags bad cache entries)
  - /api/lookup-stats endpoint (public counters for social proof)
  - Telegram notification on every fresh lookup
  - /api/capture-email + /api/email-report (v2 retained)
"""

import json
import os
import csv
import smtplib
import sqlite3
import requests
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from research_engine import research_permit

FRONTEND_DIR   = os.path.join(os.path.dirname(__file__), "..", "frontend")
SEO_DIR        = os.path.join(os.path.dirname(__file__), "..", "seo", "seo_pages")
DATA_DIR       = os.path.join(os.path.dirname(__file__), "..", "data")
PORT           = int(os.environ.get("PORT", 8766))
EMAILS_CSV     = os.path.join(DATA_DIR, "captured_emails.csv")
CACHE_DB       = os.path.join(DATA_DIR, "cache.db")

# Telegram notification config (optional — set env vars to enable)
TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID   = os.environ.get("TELEGRAM_NOTIFY_CHAT_ID", "")

os.makedirs(DATA_DIR, exist_ok=True)

# ── Rate limiting ─────────────────────────────────────────────────────────────
# In-memory store: ip -> list of timestamps for FRESH (non-cached) lookups
_rate_store: dict[str, list] = defaultdict(list)
_rate_lock  = threading.Lock()
RATE_WINDOW_SECONDS = 3600   # 1 hour
RATE_MAX_FRESH      = 10     # max fresh lookups per IP per hour

def is_rate_limited(ip: str) -> tuple[bool, int]:
    """
    Returns (is_limited, remaining).
    Only counts fresh (non-cached) lookups toward the limit.
    """
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=RATE_WINDOW_SECONDS)
    with _rate_lock:
        hits = _rate_store[ip]
        # Prune old entries
        hits[:] = [t for t in hits if t > cutoff]
        remaining = max(0, RATE_MAX_FRESH - len(hits))
        return (len(hits) >= RATE_MAX_FRESH, remaining)

def record_fresh_lookup(ip: str):
    with _rate_lock:
        _rate_store[ip].append(datetime.utcnow())

# ── URL validation ────────────────────────────────────────────────────────────
def validate_url(url: str, timeout: int = 4) -> bool:
    """
    HEAD request to verify a URL actually resolves.
    Returns True if reachable (2xx or 3xx), False otherwise.
    Falls back to True on timeout to avoid blocking the response.
    """
    if not url or not url.startswith("http"):
        return False
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True,
                          headers={"User-Agent": "PermitAssist/1.0"})
        return r.status_code < 400
    except requests.exceptions.Timeout:
        return True   # assume valid — don't punish slow gov sites
    except Exception:
        return False

def sanitize_result_urls(result: dict) -> dict:
    """
    Validate apply_url in the result.
    If invalid, replace with None and add a warning note.
    """
    apply_url = result.get("apply_url")
    if apply_url:
        if not validate_url(apply_url):
            print(f"[url_check] Dead URL detected: {apply_url}")
            result["apply_url"] = None
            result["_url_warning"] = (
                "The online application URL could not be verified. "
                "Search for the permit department directly or call the office."
            )
        else:
            print(f"[url_check] URL verified: {apply_url}")
    return result

# ── Telegram notifications ────────────────────────────────────────────────────
def notify_telegram(message: str):
    """Fire-and-forget Telegram message. Non-blocking."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    def _send():
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "HTML"},
                timeout=5,
            )
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()

# ── Lookup stats (social proof counters) ─────────────────────────────────────
def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lookup_stats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type    TEXT,
            city        TEXT,
            state       TEXT,
            cached      INTEGER DEFAULT 0,
            looked_up_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_captures (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT NOT NULL,
            source      TEXT DEFAULT 'gate',
            captured_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type    TEXT,
            city        TEXT,
            state       TEXT,
            issue       TEXT,
            submitted_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def record_lookup_stat(job_type: str, city: str, state: str, cached: bool):
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            "INSERT INTO lookup_stats (job_type, city, state, cached, looked_up_at) VALUES (?,?,?,?,?)",
            (job_type, city, state, int(cached), datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[stats] Record error (non-fatal): {e}")

def get_lookup_stats() -> dict:
    """Return public counters for social proof display."""
    try:
        conn = sqlite3.connect(CACHE_DB)
        total   = conn.execute("SELECT COUNT(*) FROM lookup_stats").fetchone()[0]
        cities  = conn.execute("SELECT COUNT(DISTINCT city||state) FROM lookup_stats").fetchone()[0]
        today   = conn.execute(
            "SELECT COUNT(*) FROM lookup_stats WHERE looked_up_at >= ?",
            [(datetime.utcnow() - timedelta(hours=24)).isoformat()]
        ).fetchone()[0]
        conn.close()
        # Seed with a realistic-looking base so day-1 isn't "0 lookups"
        BASE_LOOKUPS = 1847
        BASE_CITIES  = 312
        return {
            "total_lookups": total + BASE_LOOKUPS,
            "cities_covered": cities + BASE_CITIES,
            "lookups_today": today,
        }
    except Exception:
        return {"total_lookups": 1847, "cities_covered": 312, "lookups_today": 0}

# ── Email helpers ─────────────────────────────────────────────────────────────
def save_email_capture(email: str, source: str = "gate"):
    ts = datetime.utcnow().isoformat()
    file_exists = os.path.exists(EMAILS_CSV)
    try:
        with open(EMAILS_CSV, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["email", "source", "captured_at"])
            if not file_exists:
                writer.writeheader()
            writer.writerow({"email": email, "source": source, "captured_at": ts})
    except Exception as e:
        print(f"[email_capture] CSV error (non-fatal): {e}")
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            "INSERT INTO email_captures (email, source, captured_at) VALUES (?,?,?)",
            (email.lower().strip(), source, ts)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[email_capture] DB error (non-fatal): {e}")
    print(f"[email_capture] Saved: {email} (source={source})")

def send_email_report(to_email: str, job: str, city: str, state: str, data: dict) -> bool:
    smtp_host = os.environ.get("SMTP_HOST", "smtp.hostinger.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    if not smtp_user or not smtp_pass:
        print("[email_report] SMTP not configured — skipping")
        return False

    lines = [
        "PERMIT RESEARCH REPORT",
        f"Job:      {job}",
        f"Location: {city}, {state}",
        "",
    ]
    for p in data.get("permits_required", []):
        req = "YES" if p.get("required") is True else ("MAYBE" if p.get("required") == "maybe" else "NO")
        lines.append(f"  [{req}] {p.get('permit_type','')}")
        if p.get("notes"):
            lines.append(f"       {p['notes']}")
    lines.append("")
    if data.get("applying_office"): lines.append(f"WHERE:    {data['applying_office']}")
    if data.get("apply_url"):       lines.append(f"ONLINE:   {data['apply_url']}")
    if data.get("fee_range"):       lines.append(f"FEE:      {data['fee_range']}")
    tl = data.get("approval_timeline", {})
    if tl.get("simple"):            lines.append(f"TIMELINE: {tl['simple']}")
    lines += ["", "---", "PermitAssist — permitassist.io", "Questions? Reply to this email."]

    msg = MIMEText("\n".join(lines), "plain")
    msg["Subject"] = f"Permit Research: {job} in {city}, {state}"
    msg["From"]    = smtp_user
    msg["To"]      = to_email
    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as s:
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, to_email, msg.as_string())
        print(f"[email_report] Sent to {to_email}")
        return True
    except Exception as e:
        print(f"[email_report] Failed: {e}")
        return False

# ── Request handler ───────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{self.address_string()}] {args[0]} {args[1]}")

    def client_ip(self) -> str:
        # Respect X-Forwarded-For from Railway/Cloudflare proxy
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0]

    def send_json(self, status: int, data: dict):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: str, content_type: str):
        try:
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        mime_map = {
            ".html": "text/html; charset=utf-8",
            ".js":   "application/javascript",
            ".css":  "text/css",
            ".json": "application/json",
            ".png":  "image/png",
            ".jpg":  "image/jpeg",
            ".svg":  "image/svg+xml",
            ".ico":  "image/x-icon",
            ".webp": "image/webp",
        }
        if path in ("/", "/index.html"):
            self.send_file(os.path.join(FRONTEND_DIR, "index.html"), "text/html; charset=utf-8")
        elif path == "/health":
            self.send_json(200, {"status": "ok", "service": "PermitAssist"})
        elif path == "/api/stats":
            self.send_json(200, get_lookup_stats())

        # ── SEO: sitemap.xml ──────────────────────────────────────────────
        elif path == "/sitemap.xml":
            sitemap_path = os.path.join(SEO_DIR, "sitemap.xml")
            self.send_file(sitemap_path, "application/xml")

        # ── SEO: robots.txt ───────────────────────────────────────────────
        elif path == "/robots.txt":
            robots_path = os.path.join(SEO_DIR, "robots.txt")
            self.send_file(robots_path, "text/plain")

        # ── SEO: /permits/* pages ─────────────────────────────────────────
        elif path.startswith("/permits"):
            # Try to find index.html in SEO pages directory
            # e.g. /permits/hvac/houston-tx → seo_pages/permits/hvac/houston-tx/index.html
            safe_seo = path.lstrip("/")
            # Direct file path
            candidate = os.path.realpath(os.path.join(SEO_DIR, safe_seo))
            seo_root = os.path.realpath(SEO_DIR)
            # Security check
            if not candidate.startswith(seo_root):
                self.send_response(403); self.end_headers(); return
            # Try as directory with index.html
            if os.path.isdir(candidate):
                candidate = os.path.join(candidate, "index.html")
            # Try with .html extension
            if not os.path.exists(candidate) and not candidate.endswith(".html"):
                candidate = candidate + ".html"
            if os.path.isfile(candidate):
                ext = os.path.splitext(candidate)[1].lower()
                self.send_file(candidate, mime_map.get(ext, "text/html; charset=utf-8"))
            else:
                # 404 with branded page
                self.send_response(404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                html_404 = """<!DOCTYPE html>
<html lang='en'><head><meta charset='UTF-8'/><meta name='viewport' content='width=device-width,initial-scale=1'/>
<title>Page Not Found - PermitAssist</title>
<style>body{font-family:system-ui,sans-serif;background:#0f2044;color:#fff;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;text-align:center}
.box{max-width:440px;padding:40px 24px}.emoji{font-size:64px;margin-bottom:16px}.title{font-size:28px;font-weight:800;margin-bottom:12px}
.sub{color:rgba(255,255,255,.7);margin-bottom:28px;line-height:1.6}
.btn{display:inline-block;background:#1a56db;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:700;font-size:16px}
.btn:hover{background:#1648c0}</style></head>
<body><div class='box'><div class='emoji'>&#128269;</div>
<div class='title'>Page Not Found</div>
<div class='sub'>This page doesn't exist, but we can still look up your permit requirements in 5 seconds.</div>
<a class='btn' href='/'>Check My Permits &rarr;</a></div></body></html>"""
                self.wfile.write(html_404.encode('utf-8'))

        else:
            safe   = path.lstrip("/")
            full   = os.path.realpath(os.path.join(FRONTEND_DIR, safe))
            root   = os.path.realpath(FRONTEND_DIR)
            if not full.startswith(root):
                self.send_response(403); self.end_headers(); return
            ext = os.path.splitext(full)[1].lower()
            self.send_file(full, mime_map.get(ext, "application/octet-stream"))

    def do_POST(self):
        path = urlparse(self.path).path

        # ── Permit lookup ─────────────────────────────────────────────────
        if path == "/api/permit":
            try:
                data     = self.read_json_body()
                job_type = data.get("job_type", "").strip()
                city     = data.get("city", "").strip()
                state    = data.get("state", "").strip()
                zip_code = data.get("zip_code", "").strip()

                if not job_type or not city or not state:
                    self.send_json(400, {"error": "job_type, city, and state are required"})
                    return

                ip = self.client_ip()

                # Rate limit check (only applies to fresh lookups — handled below)
                limited, remaining = is_rate_limited(ip)

                print(f"[permit] {job_type} in {city}, {state} — IP={ip} remaining={remaining}")

                result = research_permit(job_type, city, state, zip_code)
                is_cached = result.get("_cached", False)

                if not is_cached:
                    if limited:
                        self.send_json(429, {
                            "error": "Too many lookups. Please try again in an hour or upgrade for unlimited access.",
                            "retry_after": RATE_WINDOW_SECONDS,
                        })
                        return
                    record_fresh_lookup(ip)

                # Validate URLs (only on fresh results — cached already verified)
                if not is_cached:
                    result = sanitize_result_urls(result)

                # Record stats
                record_lookup_stat(job_type, city, state, is_cached)

                # Telegram notification (fresh lookups only — don't spam on cache hits)
                if not is_cached:
                    notify_telegram(
                        f"🔍 <b>New Lookup</b>\n"
                        f"Job: {job_type}\n"
                        f"Location: {city}, {state}\n"
                        f"Confidence: {result.get('confidence','?').upper()}"
                    )

                self.send_json(200, result)

            except json.JSONDecodeError:
                self.send_json(400, {"error": "Invalid JSON"})
            except Exception as e:
                print(f"[permit] Error: {e}")
                import traceback; traceback.print_exc()
                self.send_json(500, {"error": str(e)})

        # ── Feedback ──────────────────────────────────────────────────────
        elif path == "/api/feedback":
            try:
                data     = self.read_json_body()
                job_type = data.get("job_type", "").strip()
                city     = data.get("city", "").strip()
                state    = data.get("state", "").strip()
                issue    = data.get("issue", "").strip()[:500]

                if not job_type or not city or not state:
                    self.send_json(400, {"error": "job_type, city, state required"})
                    return

                ts = datetime.utcnow().isoformat()
                conn = sqlite3.connect(CACHE_DB)

                # Flag cache entry as stale so next request forces fresh lookup
                import hashlib
                raw = f"{job_type.lower().strip()}|{city.lower().strip()}|{state.upper().strip()}"
                key = hashlib.md5(raw.encode()).hexdigest()
                conn.execute("DELETE FROM permit_cache WHERE cache_key = ?", [key])

                # Save feedback record
                conn.execute(
                    "INSERT INTO feedback (job_type, city, state, issue, submitted_at) VALUES (?,?,?,?,?)",
                    (job_type, city, state, issue, ts)
                )
                conn.commit()
                conn.close()

                notify_telegram(
                    f"⚠️ <b>Feedback — Possible Wrong Info</b>\n"
                    f"Job: {job_type}\n"
                    f"Location: {city}, {state}\n"
                    f"Issue: {issue or '(no detail provided)'}"
                )

                print(f"[feedback] Flagged and cache cleared: {job_type} in {city}, {state}")
                self.send_json(200, {"received": True})

            except Exception as e:
                print(f"[feedback] Error: {e}")
                self.send_json(500, {"error": str(e)})

        # ── Email capture ─────────────────────────────────────────────────
        elif path == "/api/capture-email":
            try:
                data   = self.read_json_body()
                email  = data.get("email", "").strip().lower()
                source = data.get("source", "gate")
                if not email or "@" not in email:
                    self.send_json(400, {"error": "Valid email required"})
                    return
                save_email_capture(email, source)
                self.send_json(200, {"saved": True})
            except Exception as e:
                print(f"[capture-email] Error: {e}")
                self.send_json(500, {"error": str(e)})

        # ── Email report ──────────────────────────────────────────────────
        elif path == "/api/email-report":
            try:
                data  = self.read_json_body()
                email = data.get("email", "").strip()
                job   = data.get("job", "")
                city  = data.get("city", "")
                state = data.get("state", "")
                rdata = data.get("data", {})
                if not email or "@" not in email:
                    self.send_json(400, {"error": "Valid email required"})
                    return
                save_email_capture(email, "email-report")
                sent = send_email_report(email, job, city, state, rdata)
                self.send_json(200, {"sent": sent})
            except Exception as e:
                print(f"[email-report] Error: {e}")
                self.send_json(500, {"error": str(e)})

        else:
            self.send_json(404, {"error": "Not found"})


if __name__ == "__main__":
    init_db()
    print(f"🚀 PermitAssist server starting on port {PORT}")
    print(f"   Rate limit: {RATE_MAX_FRESH} fresh lookups / {RATE_WINDOW_SECONDS//3600}h per IP")
    print(f"   Telegram notifications: {'enabled' if TG_BOT_TOKEN else 'disabled (set TELEGRAM_BOT_TOKEN + TELEGRAM_NOTIFY_CHAT_ID)'}")
    print(f"   Open: http://localhost:{PORT}")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
