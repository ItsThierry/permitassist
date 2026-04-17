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

import sys, os
# Ensure the api/ directory is on the path regardless of working directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import os
import csv
import hmac
import hashlib
import sqlite3
import string
import requests
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from research_engine import research_permit, build_google_maps_url, strip_pdf_from_result
from openai import OpenAI as _OpenAI
import google.generativeai as _genai
import requests as _requests

# Module-level AI clients for /api/chat
_chat_openai_client = _OpenAI()
_GEMINI_API_KEY_SERVER = os.environ.get("GEMINI_API_KEY", "")
if _GEMINI_API_KEY_SERVER:
    _genai.configure(api_key=_GEMINI_API_KEY_SERVER)
_CHAT_MODEL = "gemini-2.5-flash"  # Gemini 2.5 Flash with thinking disabled (fastest, cheapest)

FRONTEND_DIR   = os.path.join(os.path.dirname(__file__), "..", "frontend")
SEO_DIR        = os.path.join(os.path.dirname(__file__), "..", "seo", "seo_pages")
# Support RAILWAY_VOLUME_MOUNT_PATH or CACHE_DIR env var for persistent volumes
# Railway volumes are configured in the dashboard and mounted at a custom path
_default_data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
DATA_DIR = os.environ.get("CACHE_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or _default_data_dir
PORT           = int(os.environ.get("PORT", 8766))
EMAILS_CSV     = os.path.join(DATA_DIR, "captured_emails.csv")
CACHE_DB       = os.path.join(DATA_DIR, "cache.db")
SHARE_TTL_DAYS = 30  # shareable links expire after 30 days


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

# Telegram notification config (optional — set env vars to enable)
TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID   = os.environ.get("TELEGRAM_NOTIFY_CHAT_ID", "")

# ── Auth & plan constants ─────────────────────────────────────────────────────
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
if not SESSION_SECRET or SESSION_SECRET == "pa-dev-secret-CHANGE-IN-PROD":
    import secrets as _secrets
    SESSION_SECRET = _secrets.token_hex(32)
    print("⚠️  [SECURITY] SESSION_SECRET not set in env — generated ephemeral secret.")
    print("   Sessions will be invalidated on restart. Set SESSION_SECRET in Railway env!")
STRIPE_WEBHOOK_SECRET  = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_SECRET_KEY      = os.environ.get("STRIPE_SECRET_KEY", "")
FREE_LOOKUPS_PER_MONTH = 3
UPGRADE_URL_SOLO       = "https://buy.stripe.com/4gM9AMddV9k08W9auh3VC0c"
UPGRADE_URL_ANNUAL     = "https://buy.stripe.com/fZueV63DlfIo5JX7i53VC0d"
UPGRADE_URL_TEAM       = "https://buy.stripe.com/8x25kwgq7gMs2xLauh3VC0b"
PRICE_SOLO             = "price_1TME9k43XpvaBuPhmXKDc2YC"  # $24.99/mo
PRICE_SOLO_LEGACY      = "price_1TLkkQ43XpvaBuPhhxdSRoID"   # old $19/mo (deactivated)
PRICE_SOLO_ANNUAL      = "price_1TME9y43XpvaBuPhfj9W8hgG"   # $199/yr
PRICE_TEAM             = "price_1TLkkQ43XpvaBuPh0vL7MnY4"
RESEND_API_KEY         = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL             = "noreply@permitassist.io"
APP_BASE_URL           = os.environ.get("APP_BASE_URL", "https://permitassist.io").rstrip("/")
ADMIN_TOKEN            = os.environ.get("PERMITASSIST_ADMIN_TOKEN", "")
REMINDER_LOOKAHEAD_DAYS = 30
REMINDER_CHECK_SECONDS  = 3600

os.makedirs(DATA_DIR, exist_ok=True)

# ── Rate limiting ─────────────────────────────────────────────────────────────
RATE_WINDOW_SECONDS = 3600   # 1 hour
RATE_MAX_FRESH      = 10     # max fresh lookups per IP per hour

def is_rate_limited(ip: str) -> tuple[bool, int]:
    """SQLite-backed rate limiting. Survives deploys."""
    now_ts = int(utc_now().timestamp())
    window = now_ts - (now_ts % RATE_WINDOW_SECONDS)  # current 1-hour window
    try:
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute(
            "SELECT count FROM rate_limits WHERE ip=? AND window_start=?",
            [ip, window]
        ).fetchone()
        conn.close()
        count = row[0] if row else 0
        remaining = max(0, RATE_MAX_FRESH - count)
        return (count >= RATE_MAX_FRESH, remaining)
    except Exception:
        return (False, RATE_MAX_FRESH)  # fail open on DB error

def record_fresh_lookup(ip: str):
    """Record a fresh lookup for rate limiting purposes."""
    now_ts = int(utc_now().timestamp())
    window = now_ts - (now_ts % RATE_WINDOW_SECONDS)
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            "INSERT INTO rate_limits (ip, window_start, count) VALUES (?,?,1) "
            "ON CONFLICT(ip, window_start) DO UPDATE SET count = count + 1",
            [ip, window]
        )
        # Clean up windows older than 2 hours
        old_window = window - (2 * RATE_WINDOW_SECONDS)
        conn.execute("DELETE FROM rate_limits WHERE window_start < ?", [old_window])
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[rate-limit] DB error: {e}")

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shared_results (
            slug        TEXT PRIMARY KEY,
            job_type    TEXT,
            city        TEXT,
            state       TEXT,
            result_json TEXT,
            created_at  TEXT,
            expires_at  TEXT,
            views       INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id           TEXT PRIMARY KEY,
            email        TEXT NOT NULL,
            job_name     TEXT,
            address      TEXT,
            city         TEXT,
            state        TEXT,
            trade        TEXT,
            permit_name  TEXT,
            status       TEXT DEFAULT 'planning',
            applied_date TEXT,
            approved_date TEXT,
            permit_number TEXT,
            expiry_date  TEXT,
            notes        TEXT,
            result_json  TEXT,
            created_at   TEXT,
            updated_at   TEXT
        )
    """)
    # ── Auth tables (Task 1) ────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            email                TEXT NOT NULL UNIQUE,
            plan                 TEXT DEFAULT 'free',
            plan_expires_at      TEXT,
            stripe_customer_id   TEXT,
            stripe_subscription_id TEXT,
            created_at           TEXT,
            last_login           TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            token      TEXT PRIMARY KEY,
            email      TEXT NOT NULL,
            expires_at TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS magic_tokens (
            token      TEXT PRIMARY KEY,
            email      TEXT NOT NULL,
            expires_at TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lookup_counts (
            email  TEXT NOT NULL,
            month  TEXT NOT NULL,
            count  INTEGER DEFAULT 0,
            PRIMARY KEY (email, month)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS team_members (
            owner_email  TEXT NOT NULL,
            member_email TEXT NOT NULL,
            joined_at    TEXT,
            PRIMARY KEY (owner_email, member_email)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS permit_reminders (
            id           TEXT PRIMARY KEY,
            email        TEXT NOT NULL,
            job_type     TEXT,
            city         TEXT,
            state        TEXT,
            expiry_date  TEXT,
            remind_at    TEXT,
            sent_at      TEXT,
            created_at   TEXT,
            UNIQUE(email, job_type, city, state, expiry_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_emails (
            id          TEXT PRIMARY KEY,
            email       TEXT NOT NULL,
            day_num     INTEGER NOT NULL,
            scheduled_at TEXT NOT NULL,
            sent_at      TEXT,
            created_at   TEXT,
            UNIQUE(email, day_num)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id              TEXT PRIMARY KEY,
            ref_code        TEXT NOT NULL,
            referrer_email  TEXT NOT NULL,
            referred_email  TEXT,
            referred_at     TEXT,
            subscribed_at   TEXT,
            credit_flagged  INTEGER DEFAULT 0,
            created_at      TEXT
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_referrals_code ON referrals(ref_code)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS permit_issued_reminders (
            id          TEXT PRIMARY KEY,
            email       TEXT NOT NULL,
            job_id      TEXT,
            job_name    TEXT,
            city        TEXT,
            state       TEXT,
            issued_date TEXT NOT NULL,
            remind_at   TEXT NOT NULL,
            sent_at     TEXT,
            created_at  TEXT,
            UNIQUE(email, job_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_limits (
            ip           TEXT NOT NULL,
            window_start INTEGER NOT NULL,
            count        INTEGER DEFAULT 1,
            PRIMARY KEY (ip, window_start)
        )
    """)
    conn.commit()
    conn.close()

# ── Auth / Session helpers ─────────────────────────────────────────────────

def create_session_token(email: str) -> str:
    """Create a signed 30-day session token and store in DB."""
    raw = secrets.token_urlsafe(32)
    sig = hmac.new(SESSION_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    token = f"{raw}.{sig}"
    now = utc_now()
    exp = now + timedelta(days=30)
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            "INSERT OR REPLACE INTO user_sessions (token, email, expires_at, created_at) VALUES (?,?,?,?)",
            (token, email.lower().strip(), exp.isoformat(), now.isoformat())
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (email, plan, created_at, last_login) VALUES (?,?,?,?)",
            (email.lower().strip(), "free", now.isoformat(), now.isoformat())
        )
        conn.execute(
            "UPDATE users SET last_login=? WHERE email=?",
            (now.isoformat(), email.lower().strip())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[session] Create error: {e}")
    return token


def validate_session_token(token: str) -> str | None:
    """Validate HMAC-signed session token. Returns email or None."""
    if not token or "." not in token:
        return None
    try:
        raw, sig = token.rsplit(".", 1)
        expected = hmac.new(SESSION_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute(
            "SELECT email, expires_at FROM user_sessions WHERE token=?", [token]
        ).fetchone()
        conn.close()
        if not row:
            return None
        email_db, expires_at = row
        if utc_now() > parse_timestamp(expires_at):
            return None
        return email_db
    except Exception as e:
        print(f"[session] Validate error: {e}")
        return None


def get_user(email: str) -> dict | None:
    """Get user record from DB."""
    try:
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute(
            "SELECT id,email,plan,plan_expires_at,stripe_customer_id,"
            "stripe_subscription_id,created_at,last_login FROM users WHERE email=?",
            [email.lower().strip()]
        ).fetchone()
        conn.close()
        if not row:
            return None
        cols = ["id","email","plan","plan_expires_at","stripe_customer_id",
                "stripe_subscription_id","created_at","last_login"]
        return dict(zip(cols, row))
    except Exception as e:
        print(f"[user] Get error: {e}")
        return None


def get_or_create_user(email: str) -> dict:
    """Get or create user. Always returns a dict."""
    user = get_user(email)
    if user:
        return user
    now = utc_now().isoformat()
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            "INSERT OR IGNORE INTO users (email, plan, created_at, last_login) VALUES (?,?,?,?)",
            (email.lower().strip(), "free", now, now)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[user] Create error: {e}")
    return get_user(email) or {"email": email, "plan": "free"}


def is_paid_user(email: str) -> bool:
    """Check if user has active paid plan (solo or team) or is a team member."""
    email = email.lower().strip()
    user = get_user(email)
    if user and user.get("plan") in ("solo", "team"):
        exp = user.get("plan_expires_at")
        if exp:
            try:
                if utc_now() > parse_timestamp(exp):
                    return False
            except Exception:
                pass
        return True
    # Check if user is a team member under a paid owner
    try:
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute(
            "SELECT owner_email FROM team_members WHERE member_email=?", [email]
        ).fetchone()
        conn.close()
        if row:
            return is_paid_user(row[0])
    except Exception:
        pass
    return False


def get_team_scope_emails(email: str) -> list[str]:
    """Return emails whose jobs this user can see/manage."""
    email = email.lower().strip()
    scope = {email}
    try:
        conn = sqlite3.connect(CACHE_DB)
        owner_row = conn.execute(
            "SELECT owner_email FROM team_members WHERE member_email=?", [email]
        ).fetchone()
        owner_email = owner_row[0].lower().strip() if owner_row and owner_row[0] else email
        scope.add(owner_email)
        member_rows = conn.execute(
            "SELECT member_email FROM team_members WHERE owner_email=?", [owner_email]
        ).fetchall()
        conn.close()
        for row in member_rows:
            if row and row[0]:
                scope.add(row[0].lower().strip())
    except Exception:
        pass
    return sorted(scope)


def create_billing_portal_session(customer_id: str, return_url: str) -> str | None:
    if not STRIPE_SECRET_KEY or not customer_id:
        return None
    try:
        resp = requests.post(
            "https://api.stripe.com/v1/billing_portal/sessions",
            headers={"Authorization": f"Bearer {STRIPE_SECRET_KEY}"},
            data={"customer": customer_id, "return_url": return_url},
            timeout=20,
        )
        if resp.ok:
            return (resp.json() or {}).get("url")
        print(f"[stripe-portal] Failed {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[stripe-portal] Error: {e}")
    return None


def upsert_permit_reminder(email: str, job_type: str, city: str, state: str, expiry_date: str) -> dict:
    now = utc_now()
    reminder_id = str(uuid.uuid4())
    remind_at = ""
    if expiry_date:
        try:
            exp_dt = datetime.fromisoformat(expiry_date)
            remind_dt = exp_dt - timedelta(days=REMINDER_LOOKAHEAD_DAYS)
            remind_at = remind_dt.isoformat()
        except Exception:
            remind_at = ""
    conn = sqlite3.connect(CACHE_DB)
    existing = conn.execute(
        "SELECT id,sent_at FROM permit_reminders WHERE email=? AND job_type=? AND city=? AND state=? AND expiry_date=?",
        [email, job_type, city, state, expiry_date],
    ).fetchone()
    if existing:
        reminder_id = existing[0]
        conn.execute(
            "UPDATE permit_reminders SET remind_at=?, sent_at=NULL, created_at=? WHERE id=?",
            [remind_at, now.isoformat(), reminder_id],
        )
    else:
        conn.execute(
            "INSERT INTO permit_reminders (id,email,job_type,city,state,expiry_date,remind_at,sent_at,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [reminder_id, email, job_type, city, state, expiry_date, remind_at, None, now.isoformat()],
        )
    conn.commit()
    conn.close()
    return {"id": reminder_id, "remind_at": remind_at}


def process_due_reminders(now: datetime | None = None) -> int:
    now = now or utc_now()
    sent = 0
    try:
        conn = sqlite3.connect(CACHE_DB)
        rows = conn.execute(
            "SELECT id,email,job_type,city,state,expiry_date,remind_at FROM permit_reminders "
            "WHERE sent_at IS NULL AND remind_at IS NOT NULL AND remind_at<>'' AND remind_at<=?",
            [now.isoformat()],
        ).fetchall()
        for rid, email, job_type, city, state, expiry_date, remind_at in rows:
            subject = f"Permit reminder: {job_type or 'Permit'} in {city}, {state}"
            body = (
                f"Hi,\n\n"
                f"This is your PermitAssist reminder that your permit is coming up on expiry.\n\n"
                f"Job: {job_type or 'your job'}\n"
                f"Location: {city}{', ' + state if state else ''}\n"
                f"Expiry date: {expiry_date or 'Unknown'}\n"
                f"Reminder date: {remind_at}\n\n"
                f"If the permit is still active, make sure renewal or inspection closeout is handled in time.\n\n"
                f"— PermitAssist\n"
                f"{APP_BASE_URL}"
            )
            if resend_send(email, subject, body):
                conn.execute("UPDATE permit_reminders SET sent_at=? WHERE id=?", [now.isoformat(), rid])
                sent += 1
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[reminders] Process error: {e}")
    return sent


def reminder_worker():
    while True:
        try:
            process_due_reminders()
        except Exception as e:
            print(f"[reminders] Worker error: {e}")
        time.sleep(REMINDER_CHECK_SECONDS)


def get_review_queue(limit: int = 50) -> dict:
    feedback_items = []
    needs_review_items = []
    try:
        conn = sqlite3.connect(CACHE_DB)
        rows = conn.execute(
            "SELECT job_type, city, state, issue, submitted_at FROM feedback ORDER BY submitted_at DESC LIMIT ?",
            [limit],
        ).fetchall()
        for job_type, city, state, issue, submitted_at in rows:
            feedback_items.append({
                "job_type": job_type,
                "city": city,
                "state": state,
                "issue": issue,
                "submitted_at": submitted_at,
            })

        cache_rows = conn.execute(
            "SELECT job_type, city, state, result_json, created_at FROM permit_cache ORDER BY created_at DESC LIMIT ?",
            [max(limit * 4, 100)],
        ).fetchall()
        conn.close()

        for job_type, city, state, result_json, created_at in cache_rows:
            try:
                data = json.loads(result_json or "{}")
            except Exception:
                continue
            if not data.get("needs_review"):
                continue
            needs_review_items.append({
                "job_type": job_type,
                "city": city,
                "state": state,
                "created_at": created_at,
                "confidence": data.get("confidence", ""),
                "missing_fields": data.get("missing_fields", []),
                "confidence_reason": data.get("confidence_reason", ""),
            })
            if len(needs_review_items) >= limit:
                break
    except Exception as e:
        print(f"[review-queue] Error: {e}")

    return {
        "feedback": feedback_items,
        "needs_review": needs_review_items,
        "counts": {
            "feedback": len(feedback_items),
            "needs_review": len(needs_review_items),
        },
    }


def get_monthly_lookup_count(email: str) -> int:
    """Get current month's fresh lookup count for an email."""
    month = utc_now().strftime("%Y-%m")
    try:
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute(
            "SELECT count FROM lookup_counts WHERE email=? AND month=?",
            [email.lower().strip(), month]
        ).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception as e:
        print(f"[lookup_count] Get error: {e}")
        return 0


def increment_monthly_lookup(email: str) -> int:
    """Increment monthly lookup count. Returns new count."""
    month = utc_now().strftime("%Y-%m")
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            "INSERT INTO lookup_counts (email, month, count) VALUES (?,?,1) "
            "ON CONFLICT(email, month) DO UPDATE SET count = count + 1",
            [email.lower().strip(), month]
        )
        conn.commit()
        row = conn.execute(
            "SELECT count FROM lookup_counts WHERE email=? AND month=?",
            [email.lower().strip(), month]
        ).fetchone()
        conn.close()
        return row[0] if row else 1
    except Exception as e:
        print(f"[lookup_count] Increment error: {e}")
        return 1


def resend_send(to_addr: str, subject: str, text_body: str, html_body: str = None) -> bool:
    """Send email via Resend API."""
    if not RESEND_API_KEY:
        print(f"[resend] RESEND_API_KEY not set — skipping email to {to_addr}")
        return False
    payload = {
        "from": f"PermitAssist <{FROM_EMAIL}>",
        "to": [to_addr],
        "subject": subject,
        "text": text_body,
    }
    if html_body:
        payload["html"] = html_body
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=10
        )
        if resp.status_code in (200, 201):
            print(f"[resend] Sent to {to_addr} — id: {resp.json().get('id')}")
            return True
        else:
            print(f"[resend] Failed {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        print(f"[resend] Exception: {e}")
        return False


def send_magic_link_email(to_email: str, token: str) -> bool:
    """Send magic link / login code email via Resend."""
    if not RESEND_API_KEY:
        print(f"[magic-link] RESEND_API_KEY not set — token for {to_email}: {token}")
        return False  # Let frontend show the fallback code on screen
    verify_url = f"https://permitassist.io/api/verify-magic?token={token}"
    subject = f"Your PermitAssist login code: {token}"
    text_body = (
        f"Hi,\n\n"
        f"Your PermitAssist login code is: {token}\n\n"
        f"Or click this link to log in automatically (expires in 15 minutes):\n"
        f"{verify_url}\n\n"
        f"— PermitAssist\n"
        f"permitassist.io"
    )
    html_body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">
      <h2 style="color:#1e3a5f;margin-bottom:8px;">Your login code</h2>
      <p style="font-size:15px;color:#374151;">Use this code to sign in to PermitAssist:</p>
      <div style="background:#f0f4ff;border-radius:8px;padding:20px;text-align:center;margin:24px 0;">
        <span style="font-size:32px;font-weight:700;letter-spacing:6px;color:#2563eb;">{token}</span>
      </div>
      <p style="font-size:14px;color:#6b7280;">Or <a href="{verify_url}" style="color:#2563eb;">click here to log in automatically</a> (expires in 15 minutes).</p>
      <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
      <p style="font-size:12px;color:#9ca3af;">PermitAssist · permitassist.io</p>
    </div>
    """
    return resend_send(to_email, subject, text_body, html_body)


def send_confirmation_email(to_email: str, plan: str) -> bool:
    """Send plan upgrade confirmation email via Resend."""
    plan_name = "Team" if plan == "team" else "Solo"
    team_line_text = "\n\u2022 Up to 3 team seats — invite your crew at no extra cost" if plan == "team" else ""
    team_line_html = "<li>Up to 3 team seats — invite your crew at no extra cost</li>" if plan == "team" else ""
    subject = f"You're now on PermitAssist {plan_name} — unlimited lookups unlocked"
    text_body = (
        f"Hi,\n\n"
        f"You're now on PermitAssist {plan_name}! 🎉{team_line_text}\n\n"
        f"Unlimited permit lookups are now active on your account.\n\n"
        f"What you have now:\n"
        f"\u2022 Unlimited lookups every month, any job, any city\n"
        f"\u2022 Exact permit names, current fees, and office contacts\n"
        f"\u2022 Job tracker to manage all your permits in one place\n\n"
        f"Go look up your permits: https://permitassist.io\n\n"
        f"Questions? Just reply to this email.\n\n"
        f"— PermitAssist\n"
        f"permitassist.io"
    )
    html_body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">
      <h2 style="color:#1e3a5f;">You're on PermitAssist {plan_name}! 🎉</h2>
      <p style="color:#374151;">Unlimited permit lookups are now active on your account.</p>
      <ul style="color:#374151;line-height:1.8;">
        <li>Unlimited lookups every month, any job, any city</li>
        <li>Exact permit names, current fees, and office contacts</li>
        <li>Job tracker to manage all your permits in one place</li>
        {team_line_html}
      </ul>
      <a href="https://permitassist.io" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;margin:16px 0;">Look Up Your Permits →</a>
      <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
      <p style="font-size:12px;color:#9ca3af;">PermitAssist · permitassist.io</p>
    </div>
    """
    return resend_send(to_email, subject, text_body, html_body)


# ── Onboarding Email Drip ────────────────────────────────────────────────────────
ONBOARDING_SCHEDULE = [
    (0,  "Welcome to PermitAssist — here's how to get the most out of it"),
    (1,  "One thing most contractors miss about permits"),
    (3,  "Save your jobs, save your time"),
    (7,  "How are your lookups going?"),
    (14, "Upgrade to unlimited — you've earned it"),
]

ONBOARDING_BODIES = {
    0: (
        "Welcome to PermitAssist! Here are 3 quick tips to get the most out of it:\n\n"
        "1. 🔍 BE SPECIFIC with your job description. Instead of 'HVAC work', try 'Residential furnace replacement, gas, 3-ton unit'. You'll get a more exact permit name and fee.\n\n"
        "2. 📌 ADD THE CITY + STATE. Every jurisdiction has different rules. The building department in one city may require a permit that the next city over doesn't.\n\n"
        "3. 📁 SAVE YOUR LOOKUPS. After your first lookup, check the History tab (📋) to find past results instantly — no need to look up the same job twice.\n\n"
        "Start your first lookup now: https://permitassist.io\n\n"
        "— PermitAssist\n"
        "permitassist.io"
    ),
    1: (
        "Quick tip from the PermitAssist team:\n\n"
        "The biggest permit mistake contractors make: assuming the city handles everything.\n\n"
        "Many jurisdictions are split — your job may fall under city, county, OR state jurisdiction depending on location and type of work. A permit pulled at the wrong office = delays, re-submissions, and sometimes fines.\n\n"
        "PermitAssist always looks up the exact authority having jurisdiction (AHJ) for your job so you show up at the right counter.\n\n"
        "Try a lookup for your next job: https://permitassist.io\n\n"
        "— PermitAssist\n"
        "permitassist.io"
    ),
    3: (
        "Did you know PermitAssist has a Job Tracker?\n\n"
        "After any permit lookup, click '📁 Save to Job Tracker' to keep all your permits in one place.\n\n"
        "You can track status (Planning → Applied → Approved → Active → Closed), set expiry dates, add notes, and get reminders before permits expire.\n\n"
        "It's free for all users. Log in and try it: https://permitassist.io\n\n"
        "— PermitAssist\n"
        "permitassist.io"
    ),
    7: (
        "Hey, just checking in — how are your permit lookups going?\n\n"
        "If you've run into any issues or got a result that didn't look right, just reply to this email and we'll look into it.\n\n"
        "Also — you can look up any job, any city, any time. Try a job you've been meaning to research: https://permitassist.io\n\n"
        "— PermitAssist\n"
        "permitassist.io"
    ),
    14: (
        f"You've been with PermitAssist for 2 weeks now.\n\n"
        f"Free accounts get 3 lookups per month. If you're hitting that limit or doing more than 3 jobs/month, upgrading to Solo for $24.99/mo gets you:\n\n"
        f"\u2022 Unlimited lookups, every month\n"
        f"\u2022 Job tracker for all your permits\n"
        f"\u2022 Permit expiry reminders\n"
        f"\u2022 Priority city requests\n\n"
        f"Upgrade here (cancel anytime): {UPGRADE_URL_SOLO}\n\n"
        f"Or get the annual plan ($199/yr \u2014 saves $100): {UPGRADE_URL_ANNUAL}\n\n"
        f"\u2014 PermitAssist\n"
        f"permitassist.io"
    ),
}


def schedule_onboarding_emails(email: str):
    """Schedule 5 onboarding emails for a new user."""
    now = utc_now()
    try:
        conn = sqlite3.connect(CACHE_DB)
        for day_num, subject in ONBOARDING_SCHEDULE:
            scheduled_at = (now + timedelta(days=day_num)).isoformat()
            onboarding_id = str(uuid.uuid4())
            conn.execute(
                "INSERT OR IGNORE INTO onboarding_emails (id, email, day_num, scheduled_at, sent_at, created_at) VALUES (?,?,?,?,NULL,?)",
                [onboarding_id, email.lower().strip(), day_num, scheduled_at, now.isoformat()]
            )
        conn.commit()
        conn.close()
        print(f"[onboarding] Scheduled 5 emails for {email}")
    except Exception as e:
        print(f"[onboarding] Schedule error: {e}")


def process_onboarding_emails(now: datetime = None) -> int:
    """Send due onboarding emails. Returns count sent."""
    now = now or utc_now()
    sent = 0
    try:
        conn = sqlite3.connect(CACHE_DB)
        rows = conn.execute(
            "SELECT id, email, day_num FROM onboarding_emails "
            "WHERE sent_at IS NULL AND scheduled_at <= ?",
            [now.isoformat()]
        ).fetchall()
        for eid, email, day_num in rows:
            subject_text = dict(ONBOARDING_SCHEDULE).get(day_num, "PermitAssist Update")
            body = ONBOARDING_BODIES.get(day_num, "")
            if not body:
                continue
            if resend_send(email, subject_text, body):
                conn.execute("UPDATE onboarding_emails SET sent_at=? WHERE id=?", [now.isoformat(), eid])
                sent += 1
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[onboarding] Process error: {e}")
    return sent


# ── Referral System ─────────────────────────────────────────────────────────────────────
def generate_ref_code(email: str) -> str:
    """Generate deterministic 8-char ref code from email using SHA256."""
    h = hashlib.sha256(email.lower().strip().encode()).hexdigest()
    # Use uppercase alphanumeric chars from the hash
    chars = ''.join(c for c in h.upper() if c.isalnum())[:8]
    return chars


def ensure_referral_record(email: str) -> str:
    """Ensure a referral record exists for this email. Returns ref_code."""
    ref_code = generate_ref_code(email)
    now = utc_now().isoformat()
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            "INSERT OR IGNORE INTO referrals (id, ref_code, referrer_email, created_at) VALUES (?,?,?,?)",
            [str(uuid.uuid4()), ref_code, email.lower().strip(), now]
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[referral] Ensure error: {e}")
    return ref_code


def record_referral_signup(ref_code: str, referred_email: str):
    """Record when a referred user signs up."""
    if not ref_code:
        return
    now = utc_now().isoformat()
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            "UPDATE referrals SET referred_email=?, referred_at=? WHERE ref_code=? AND referred_email IS NULL",
            [referred_email.lower().strip(), now, ref_code]
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[referral] Record signup error: {e}")


def flag_referral_credit(referred_email: str):
    """Flag referral credit AND notify referrer by email when referred user subscribes."""
    try:
        conn = sqlite3.connect(CACHE_DB)
        # Find the referrer
        row = conn.execute(
            "SELECT ref_code, referrer_email FROM referrals WHERE referred_email=? AND credit_flagged=0",
            [referred_email.lower().strip()]
        ).fetchone()
        if not row:
            conn.close()
            return
        ref_code, referrer_email = row
        now_iso = utc_now().isoformat()
        # Mark credit as applied
        conn.execute(
            "UPDATE referrals SET subscribed_at=?, credit_flagged=1 WHERE ref_code=?",
            [now_iso, ref_code]
        )
        # Extend referrer's plan by 30 days
        referrer = conn.execute(
            "SELECT plan, plan_expires_at FROM users WHERE email=?", [referrer_email]
        ).fetchone()
        if referrer and referrer[0] in ("solo", "team"):
            current_exp = referrer[1]
            try:
                exp_dt = parse_timestamp(current_exp) if current_exp else utc_now()
                new_exp = max(exp_dt, utc_now()) + timedelta(days=30)
                conn.execute(
                    "UPDATE users SET plan_expires_at=? WHERE email=?",
                    [new_exp.isoformat(), referrer_email]
                )
                print(f"[referral] Extended {referrer_email} plan by 30 days → {new_exp.date()}")
            except Exception as e:
                print(f"[referral] Could not extend plan: {e}")
        conn.commit()
        conn.close()
        # Notify referrer by email
        subject = "You earned a free month on PermitAssist! 🎉"
        body_text = (
            f"Hi,\n\n"
            f"Great news — one of your referrals just subscribed to PermitAssist!\n\n"
            f"As a thank you, we've added 30 free days to your plan. No action needed.\n\n"
            f"Keep sharing your referral link from your Account page to earn more.\n\n"
            f"— PermitAssist\n"
            f"permitassist.io"
        )
        body_html = f"""
        <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">
          <h2 style="color:#1e3a5f;">You earned a free month! 🎉</h2>
          <p style="color:#374151;">One of your referrals just subscribed to PermitAssist.</p>
          <p style="color:#374151;">We've automatically added <strong>30 free days</strong> to your plan. No action needed.</p>
          <a href="https://permitassist.io/account" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;margin:16px 0;">View My Account →</a>
          <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
          <p style="font-size:12px;color:#9ca3af;">Keep sharing your referral link to earn more free months. Find it on your Account page.</p>
          <p style="font-size:12px;color:#9ca3af;">PermitAssist · permitassist.io</p>
        </div>
        """
        threading.Thread(
            target=resend_send, args=(referrer_email, subject, body_text, body_html), daemon=True
        ).start()
        notify_telegram(f"🤝 <b>Referral Credit Applied</b>\nReferrer: {referrer_email}\nReferred: {referred_email}\n+30 days added")
    except Exception as e:
        print(f"[referral] Flag credit error: {e}")


# ── 90-day permit issued reminders ────────────────────────────────────────────────────
def process_permit_issued_reminders(now: datetime = None) -> int:
    """Send 90-day permit expiry reminders for saved jobs with issued_date set."""
    now = now or utc_now()
    sent = 0
    try:
        # Find records where remind_at has passed and not yet sent
        conn = sqlite3.connect(CACHE_DB)
        rows = conn.execute(
            "SELECT id, email, job_name, city, state, issued_date FROM permit_issued_reminders "
            "WHERE sent_at IS NULL AND remind_at <= ?",
            [now.isoformat()]
        ).fetchall()
        for rid, email, job_name, city, state, issued_date in rows:
            subject = f"Your permit for {job_name or 'your job'} in {city}, {state} may be expiring soon"
            body = (
                f"Hi,\n\n"
                f"Heads up \u2014 your permit may be approaching the 90-day mark, which is when many jurisdictions require a final inspection or renewal.\n\n"
                f"Job: {job_name or 'your job'}\n"
                f"Location: {city}{', ' + state if state else ''}\n"
                f"Permit issued: {issued_date}\n\n"
                f"Action: Book your final inspection or contact the building department to confirm your permit status.\n\n"
                f"\u26a0\ufe0f Don't let it expire \u2014 an expired permit can result in stop-work orders, re-application fees, and failed final inspections.\n\n"
                f"\u2014 PermitAssist\n"
                f"permitassist.io"
            )
            if resend_send(email, subject, body):
                conn.execute("UPDATE permit_issued_reminders SET sent_at=? WHERE id=?", [now.isoformat(), rid])
                sent += 1
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[permit-issued-reminders] Error: {e}")
    return sent


def upsert_permit_issued_reminder(email: str, job_id: str, job_name: str, city: str, state: str, issued_date: str) -> dict:
    """Store a 90-day reminder for a permit issued date."""
    now = utc_now()
    reminder_id = str(uuid.uuid4())
    remind_at = ""
    if issued_date:
        try:
            issued_dt = datetime.fromisoformat(issued_date)
            remind_dt = issued_dt + timedelta(days=85)  # Remind at ~85 days
            remind_at = remind_dt.isoformat()
        except Exception:
            pass
    try:
        conn = sqlite3.connect(CACHE_DB)
        existing = conn.execute(
            "SELECT id FROM permit_issued_reminders WHERE email=? AND job_id=?",
            [email, job_id]
        ).fetchone()
        if existing:
            reminder_id = existing[0]
            conn.execute(
                "UPDATE permit_issued_reminders SET issued_date=?, remind_at=?, sent_at=NULL WHERE id=?",
                [issued_date, remind_at, reminder_id]
            )
        else:
            conn.execute(
                "INSERT INTO permit_issued_reminders (id,email,job_id,job_name,city,state,issued_date,remind_at,sent_at,created_at) VALUES (?,?,?,?,?,?,?,?,NULL,?)",
                [reminder_id, email, job_id, job_name, city, state, issued_date, remind_at, now.isoformat()]
            )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[permit-issued-reminders] Upsert error: {e}")
    return {"id": reminder_id, "remind_at": remind_at}


def verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """Verify Stripe webhook signature using HMAC-SHA256."""
    if not secret:
        print("[stripe-webhook] No STRIPE_WEBHOOK_SECRET — skipping signature check")
        return True
    try:
        parts: dict[str, list] = {}
        for item in sig_header.split(","):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                k = k.strip()
                parts.setdefault(k, []).append(v.strip())
        ts = parts.get("t", [""])[0]
        sigs = parts.get("v1", [])
        signed_payload = f"{ts}.{payload.decode('utf-8')}"
        expected = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, s) for s in sigs)
    except Exception as e:
        print(f"[stripe-webhook] Signature verify error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────

def record_lookup_stat(job_type: str, city: str, state: str, cached: bool):
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            "INSERT INTO lookup_stats (job_type, city, state, cached, looked_up_at) VALUES (?,?,?,?,?)",
            (job_type, city, state, int(cached), utc_now().isoformat())
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
            [(utc_now() - timedelta(hours=24)).isoformat()]
        ).fetchone()[0]
        conn.close()
        # Seed with realistic base for social proof
        BASE_LOOKUPS = 1847
        BASE_CITIES  = 312
        # Seed today count: approx 5-8 lookups/day average from before launch
        BASE_TODAY   = 6
        return {
            "total_lookups": total + BASE_LOOKUPS,
            "cities_covered": cities + BASE_CITIES,
            "lookups_today": today + BASE_TODAY,
        }
    except Exception:
        return {"total_lookups": 1847, "cities_covered": 312, "lookups_today": 6}

# ── Email helpers ─────────────────────────────────────────────────────────────
def save_email_capture(email: str, source: str = "gate"):
    ts = utc_now().isoformat()
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

# ── Shared result links ──────────────────────────────────────────────────────
import secrets

def create_share(job_type: str, city: str, state: str, result: dict) -> str:
    """Store a result and return a short slug. Expires in SHARE_TTL_DAYS days."""
    slug = secrets.token_urlsafe(8)  # e.g. 'aB3xY7qR'
    now  = utc_now()
    exp  = now + timedelta(days=SHARE_TTL_DAYS)
    # Strip internal metadata before storing
    clean = {k: v for k, v in result.items() if not k.startswith('_')}
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            "INSERT OR REPLACE INTO shared_results "
            "(slug, job_type, city, state, result_json, created_at, expires_at, views) "
            "VALUES (?,?,?,?,?,?,?,0)",
            (slug, job_type, city, state, json.dumps(clean), now.isoformat(), exp.isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[share] DB error: {e}")
    return slug

def get_share(slug: str) -> dict | None:
    """Retrieve a shared result by slug. Returns None if expired or not found."""
    try:
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute(
            "SELECT result_json, expires_at, job_type, city, state FROM shared_results WHERE slug=?",
            [slug]
        ).fetchone()
        if not row:
            conn.close()
            return None
        result_json, expires_at, job_type, city, state = row
        if utc_now() > parse_timestamp(expires_at):
            # Expired — delete and return None
            conn.execute("DELETE FROM shared_results WHERE slug=?", [slug])
            conn.commit()
            conn.close()
            return None
        # Increment view counter
        conn.execute("UPDATE shared_results SET views=views+1 WHERE slug=?", [slug])
        conn.commit()
        conn.close()
        return {
            "data": json.loads(result_json),
            "job_type": job_type,
            "city": city,
            "state": state,
        }
    except Exception as e:
        print(f"[share] Read error: {e}")
        return None

def render_share_page(share: dict) -> str:
    """Render a clean, read-only HTML page for a shared permit result."""
    d  = share["data"]
    job   = share["job_type"]
    city  = share["city"]
    state = share["state"]
    pv    = d.get("permit_verdict", "MAYBE")
    verdict_color = {"YES": "#10b981", "NO": "#ef4444", "MAYBE": "#f59e0b"}.get(pv, "#f59e0b")
    verdict_bg    = {"YES": "rgba(16,185,129,.12)", "NO": "rgba(239,68,68,.12)", "MAYBE": "rgba(245,158,11,.12)"}.get(pv, "rgba(245,158,11,.12)")

    def esc(s):
        return str(s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

    fee    = esc(d.get("fee_range", ""))
    phone  = esc(d.get("apply_phone", ""))
    office = esc(d.get("applying_office", ""))
    addr   = esc(d.get("apply_address", ""))
    tl     = d.get("approval_timeline", {})
    maps   = esc(d.get("apply_google_maps", ""))
    permits = d.get("permits_required", [])
    tips    = d.get("pro_tips", [])[:3]
    bring   = d.get("what_to_bring", [])[:5]
    sources = [esc(s) for s in (d.get("sources") or [])[:4] if s]
    permit_name = esc(d.get("permit_name") or (permits[0].get("permit_type") if permits else "") or "")
    summary = esc(d.get("job_summary") or d.get("permit_summary") or "")
    license_r = esc(d.get("license_required", ""))

    phone_raw = "".join(c for c in phone if c.isdigit() or c == "+")

    rows = ""
    if fee:    rows += f'<tr><td>💰 Fee</td><td><strong style="color:#10b981">{fee}</strong></td></tr>'
    if tl.get("simple"): rows += f'<tr><td>⏱ Timeline</td><td>{esc(tl["simple"])}</td></tr>'
    if license_r: rows += f'<tr><td>🧰 Who pulls it</td><td>{license_r}</td></tr>'

    tips_html = "".join(f"<li>{esc(t)}</li>" for t in tips)
    bring_html = "".join(f"<li>{esc(b)}</li>" for b in bring)
    sources_html = "".join(f"<li><a href=\"{s}\" target=\"_blank\" rel=\"noopener\" style=\"color:#1a56db\">{s}</a></li>" for s in sources)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Permit: {esc(job)} in {esc(city)}, {esc(state)} — PermitAssist</title>
  <meta name="description" content="Permit requirements for {esc(job)} in {esc(city)}, {esc(state)}. Shared via PermitAssist."/>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    :root{{
      --bg:#ffffff;--bg2:#f1f5f9;--border:#e2e8f0;--text:#0f172a;--text2:#475569;
      --text3:#64748b;--card-bg:#f8fafc;--badge-bg:#f1f5f9;--badge-border:#e2e8f0;
      --hero-bg:linear-gradient(135deg,rgba(26,86,219,.08),rgba(26,86,219,.03));
      --hero-border:rgba(26,86,219,.2);--td-border:#e2e8f0;
    }}
    @media(prefers-color-scheme:dark){{
      :root{{
        --bg:#0b1220;--bg2:#111827;--border:#253045;--text:#f0f4ff;--text2:#b8c5e0;
        --text3:#7888a8;--card-bg:#111827;--badge-bg:#1a2336;--badge-border:#253045;
        --hero-bg:linear-gradient(135deg,rgba(26,86,219,.15),rgba(26,86,219,.05));
        --hero-border:rgba(26,86,219,.25);--td-border:#253045;
      }}
    }}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}
    .wrap{{max-width:640px;margin:0 auto;padding:24px 20px 48px}}
    .nav{{display:flex;align-items:center;gap:10px;margin-bottom:28px;padding-bottom:16px;border-bottom:1px solid var(--border)}}
    .logo-mark{{width:32px;height:32px;border-radius:7px;background:#1a56db;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0}}
    .logo-text{{font-size:17px;font-weight:800;color:var(--text)}}.logo-text em{{font-style:normal;color:#1a56db}}
    .shared-badge{{margin-left:auto;font-size:11px;color:var(--text3);background:var(--badge-bg);border:1px solid var(--badge-border);border-radius:20px;padding:4px 10px}}
    .result-hero{{background:var(--hero-bg);border:1px solid var(--hero-border);border-radius:12px;padding:18px 20px;margin-bottom:14px}}
    .result-job{{font-size:19px;font-weight:800;margin-bottom:3px;color:var(--text)}}
    .result-loc{{font-size:13px;color:var(--text2)}}
    .verdict-pill{{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:7px 14px;font-size:13px;font-weight:800;margin-top:10px;background:{verdict_bg};color:{verdict_color}}}
    .card{{background:var(--card-bg);border:1px solid var(--border);border-radius:12px;padding:16px 18px;margin-bottom:12px}}
    .card-label{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:var(--text3);margin-bottom:10px}}
    table{{width:100%;border-collapse:collapse}}td{{padding:9px 0;border-bottom:1px solid var(--td-border);font-size:14px;color:var(--text2);vertical-align:top}}td:first-child{{width:120px;font-size:12px;color:var(--text3);font-weight:600}}tr:last-child td{{border-bottom:none}}
    .contact-phone{{display:block;font-size:24px;font-weight:900;color:#1a56db;text-decoration:none;margin-bottom:6px}}
    .contact-office{{font-size:14px;font-weight:700;color:var(--text);margin-bottom:4px}}
    .contact-addr{{font-size:13px;color:var(--text2)}}
    .maps-link{{display:inline-flex;align-items:center;gap:5px;margin-top:10px;font-size:13px;color:#1a56db;font-weight:700;text-decoration:none;background:rgba(26,86,219,.08);border:1px solid rgba(26,86,219,.2);border-radius:8px;padding:7px 12px}}
    .maps-link:hover{{background:rgba(26,86,219,.15)}}
    ul{{margin-left:18px;color:var(--text2);line-height:1.6}}li{{padding:4px 0}}
    .cta{{background:#1a56db;border-radius:12px;padding:18px 20px;text-align:center;margin-top:24px}}
    .cta p{{font-size:13px;color:rgba(255,255,255,.75);margin-bottom:12px}}
    .cta a{{background:#fff;color:#1a56db;font-weight:800;font-size:15px;padding:11px 28px;border-radius:8px;text-decoration:none;display:inline-block}}
    .disclaimer{{font-size:11px;color:var(--text3);text-align:center;margin-top:20px;line-height:1.6}}
  </style>
</head>
<body>
<div class="wrap">
  <div class="nav">
    <div class="logo-mark">📋</div>
    <div class="logo-text">Permit<em>Assist</em></div>
    <div class="shared-badge">🔗 Shared result</div>
  </div>
  <div class="result-hero">
    <div class="result-job">{esc(job)}</div>
    <div class="result-loc">📍 {esc(city)}, {esc(state)}</div>
    <div class="verdict-pill">{pv}</div>
  </div>
  {'<div class="card"><div class="card-label">Permit Info</div><div style="font-size:18px;font-weight:900;margin-bottom:8px">' + permit_name + '</div><div style="font-size:13px;color:#b8c5e0">' + summary + '</div></div>' if permit_name or summary else ''}
  {'<div class="card"><div class="card-label">📞 Contact</div><a class="contact-phone" href="tel:' + phone_raw + '">' + phone + '</a><div class="contact-office">' + office + '</div><div class="contact-addr">' + addr + '</div>' + ('<a class="maps-link" href="' + maps + '" target="_blank" rel="noopener">📍 Open in Google Maps</a>' if maps else '') + '</div>' if phone or office else ''}
  {'<div class="card"><div class="card-label">💰 Cost · ⏱ Timeline · 🧰 Who Pulls It</div><table>' + rows + '</table></div>' if rows else ''}
  {'<div class="card"><div class="card-label">📎 What to Bring</div><ul>' + bring_html + '</ul></div>' if bring_html else ''}
  {'<div class="card"><div class="card-label">🔗 Sources</div><ul>' + sources_html + '</ul></div>' if sources_html else ''}
  {'<div class="card"><div class="card-label">💡 Pro Tips</div><ul>' + tips_html + '</ul></div>' if tips_html else ''}
  <div class="cta">
    <p>Look up permit requirements for your own jobs — free, no signup needed.</p>
    <a href="https://permitassist.io">Try PermitAssist Free →</a>
  </div>
  <div class="disclaimer">📌 Always verify requirements directly with your local building department before starting work.<br>Shared via <a href="https://permitassist.io" style="color:#1a56db">PermitAssist</a></div>
</div>
</body>
</html>"""

def send_email_report(to_email: str, job: str, city: str, state: str, data: dict) -> bool:
    """Send a beautiful HTML permit research report via Resend."""
    def esc(s):
        return str(s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

    subject = f"Permit Research: {job} in {city}, {state}"
    pv  = data.get("permit_verdict", "MAYBE")
    verdict_color = {"YES": "#10b981", "NO": "#ef4444", "MAYBE": "#f59e0b"}.get(pv, "#f59e0b")
    verdict_bg    = {"YES": "rgba(16,185,129,.12)", "NO": "rgba(239,68,68,.12)", "MAYBE": "rgba(245,158,11,.12)"}.get(pv, "rgba(245,158,11,.12)")

    fee    = esc(data.get("fee_range", ""))
    office = esc(data.get("applying_office", ""))
    addr   = esc(data.get("apply_address", ""))
    phone  = esc(data.get("apply_phone", ""))
    portal = esc(data.get("apply_url", ""))
    maps   = esc(data.get("apply_google_maps", ""))
    tl     = data.get("approval_timeline", {})
    timeline = esc(tl.get("simple", ""))
    permits  = data.get("permits_required", [])
    tips     = data.get("pro_tips", [])[:4]
    bring    = data.get("what_to_bring", [])[:5]
    sources  = [s for s in (data.get("sources") or [])[:4] if s]
    license_r = esc(data.get("license_required", ""))

    # Build permit rows
    permit_rows = ""
    for p in permits:
        req = "YES" if p.get("required") is True else ("MAYBE" if p.get("required") == "maybe" else "NO")
        req_color = {"YES": "#10b981", "MAYBE": "#f59e0b", "NO": "#ef4444"}.get(req, "#94a3b8")
        permit_rows += f"""
        <tr>
          <td style="padding:10px 0;border-bottom:1px solid #e2e8f0;vertical-align:top;width:60px">
            <span style="display:inline-block;background:{req_color}22;color:{req_color};border-radius:6px;padding:2px 8px;font-size:11px;font-weight:700">{req}</span>
          </td>
          <td style="padding:10px 0 10px 12px;border-bottom:1px solid #e2e8f0;vertical-align:top">
            <strong style="color:#0f172a;font-size:14px">{esc(p.get('permit_type',''))}</strong>
            {('<br><span style="font-size:12px;color:#64748b;margin-top:3px;display:block">' + esc(p.get('notes','')) + '</span>') if p.get('notes') else ''}
          </td>
        </tr>"""

    tips_html    = "".join(f'<li style="padding:3px 0;color:#475569;font-size:13px">{esc(t)}</li>' for t in tips)
    bring_html   = "".join(f'<li style="padding:3px 0;color:#475569;font-size:13px">{esc(b)}</li>' for b in bring)
    sources_html = "".join(f'<li style="padding:3px 0"><a href="{esc(s)}" style="color:#1a56db;font-size:12px">{esc(s)}</a></li>' for s in sources)

    contact_section = ""
    if office or phone or addr:
        contact_section = f"""
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:16px 18px;margin-bottom:12px">
          <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#94a3b8;margin-bottom:10px">📞 Contact</div>
          {('<a href="tel:' + ''.join(c for c in phone if c.isdigit() or c == '+') + '" style="font-size:22px;font-weight:900;color:#1a56db;text-decoration:none;display:block;margin-bottom:5px">' + phone + '</a>') if phone else ''}
          {('<div style="font-size:14px;font-weight:700;color:#0f172a;margin-bottom:3px">' + office + '</div>') if office else ''}
          {('<div style="font-size:13px;color:#64748b;margin-bottom:8px">' + addr + '</div>') if addr else ''}
          {('<a href="' + maps + '" style="display:inline-flex;align-items:center;gap:5px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:7px 12px;font-size:13px;color:#1a56db;font-weight:700;text-decoration:none">📍 Open in Google Maps</a>') if maps else ''}
        </div>"""

    # Plain text fallback
    text_lines = [f"PERMIT RESEARCH REPORT\nJob: {job}\nLocation: {city}, {state}\n"]
    for p in permits:
        req = "YES" if p.get("required") is True else ("MAYBE" if p.get("required") == "maybe" else "NO")
        text_lines.append(f"[{req}] {p.get('permit_type','')}")
        if p.get("notes"): text_lines.append(f"  {p['notes']}")
    if fee:      text_lines.append(f"\nFee: {data.get('fee_range','')}")
    if timeline: text_lines.append(f"Timeline: {tl.get('simple','')}")
    if office:   text_lines.append(f"Where: {data.get('applying_office','')}")
    if portal:   text_lines.append(f"Online: {data.get('apply_url','')}")
    if maps:     text_lines.append(f"Maps: {data.get('apply_google_maps','')}")
    text_lines.append("\n---\nPermitAssist — permitassist.io")

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"/></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif">
  <div style="max-width:600px;margin:32px auto;padding:0 16px 48px">
    <!-- Header -->
    <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:14px;overflow:hidden;margin-bottom:12px">
      <div style="background:#1a56db;padding:20px 24px;display:flex;align-items:center;gap:10px">
        <span style="font-size:22px">📋</span>
        <span style="font-size:18px;font-weight:800;color:#ffffff">Permit<span style="opacity:.8">Assist</span></span>
      </div>
      <div style="padding:20px 24px">
        <div style="font-size:18px;font-weight:800;color:#0f172a;margin-bottom:4px">{esc(job)}</div>
        <div style="font-size:13px;color:#64748b;margin-bottom:12px">📍 {esc(city)}, {esc(state)}</div>
        <span style="display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:7px 14px;font-size:13px;font-weight:800;background:{verdict_bg};color:{verdict_color}">{pv}</span>
      </div>
    </div>

    <!-- Permits Required -->
    {('<div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;margin-bottom:12px"><div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#94a3b8;margin-bottom:10px">Permits Required</div><table style="width:100%;border-collapse:collapse">' + permit_rows + '</table></div>') if permits else ''}

    <!-- Key Info -->
    {('<div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;margin-bottom:12px"><div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#94a3b8;margin-bottom:10px">💰 Cost · ⏱ Timeline</div><table style="width:100%;border-collapse:collapse">' + (f'<tr><td style="padding:9px 0;border-bottom:1px solid #e2e8f0;font-size:12px;color:#94a3b8;font-weight:600;width:120px">Fee</td><td style="padding:9px 0;border-bottom:1px solid #e2e8f0;font-size:14px;color:#10b981;font-weight:700">' + fee + '</td></tr>' if fee else '') + (f'<tr><td style="padding:9px 0;border-bottom:1px solid #e2e8f0;font-size:12px;color:#94a3b8;font-weight:600">Timeline</td><td style="padding:9px 0;border-bottom:1px solid #e2e8f0;font-size:14px;color:#475569">' + timeline + '</td></tr>' if timeline else '') + (f'<tr><td style="padding:9px 0;font-size:12px;color:#94a3b8;font-weight:600">Who Pulls It</td><td style="padding:9px 0;font-size:14px;color:#475569">' + license_r + '</td></tr>' if license_r else '') + '</table></div>') if fee or timeline or license_r else ''}

    <!-- Contact / Maps -->
    {contact_section}

    {('<div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;margin-bottom:12px"><div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#94a3b8;margin-bottom:10px">🌐 Online Portal</div><a href="' + data.get('apply_url','') + '" style="color:#1a56db;font-size:13px;font-weight:600;word-break:break-all">' + portal + '</a></div>') if portal else ''}

    {('<div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;margin-bottom:12px"><div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#94a3b8;margin-bottom:8px">📎 What to Bring</div><ul style="margin-left:18px;padding:0">' + bring_html + '</ul></div>') if bring_html else ''}

    {('<div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;margin-bottom:12px"><div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#94a3b8;margin-bottom:8px">💡 Pro Tips</div><ul style="margin-left:18px;padding:0">' + tips_html + '</ul></div>') if tips_html else ''}

    {('<div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;margin-bottom:12px"><div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#94a3b8;margin-bottom:8px">🔗 Sources</div><ul style="margin-left:18px;padding:0">' + sources_html + '</ul></div>') if sources_html else ''}

    <!-- CTA -->
    <div style="background:#1a56db;border-radius:12px;padding:20px;text-align:center;margin-top:8px">
      <p style="font-size:13px;color:rgba(255,255,255,.75);margin:0 0 12px">Look up more permits — free, no signup needed.</p>
      <a href="https://permitassist.io" style="background:#ffffff;color:#1a56db;font-weight:800;font-size:15px;padding:12px 28px;border-radius:8px;text-decoration:none;display:inline-block">Open PermitAssist →</a>
    </div>

    <p style="font-size:11px;color:#94a3b8;text-align:center;margin-top:20px;line-height:1.6">
      📌 Always verify requirements with your local building department before starting work.<br>
      You're receiving this because you requested it at <a href="https://permitassist.io" style="color:#1a56db">permitassist.io</a>
    </p>
  </div>
</body>
</html>"""

    return resend_send(to_email, subject, "\n".join(text_lines), html)

# ── Job Tracker helpers ──────────────────────────────────────────────────────

def create_job(email: str, job_name: str, city: str, state: str, **kwargs) -> dict:
    job_id = str(uuid.uuid4())
    now = utc_now().isoformat()
    fields = {
        "id": job_id, "email": email.lower().strip(), "job_name": job_name,
        "city": city, "state": state, "created_at": now, "updated_at": now,
        "address": kwargs.get("address", ""),
        "trade": kwargs.get("trade", ""),
        "permit_name": kwargs.get("permit_name", ""),
        "status": kwargs.get("status", "planning"),
        "applied_date": kwargs.get("applied_date", ""),
        "approved_date": kwargs.get("approved_date", ""),
        "permit_number": kwargs.get("permit_number", ""),
        "expiry_date": kwargs.get("expiry_date", ""),
        "notes": kwargs.get("notes", ""),
        "result_json": json.dumps(kwargs.get("result_json", {})) if kwargs.get("result_json") else "",
    }
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            "INSERT INTO jobs (id,email,job_name,address,city,state,trade,permit_name,"
            "status,applied_date,approved_date,permit_number,expiry_date,notes,result_json,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fields["id"], fields["email"], fields["job_name"], fields["address"],
             fields["city"], fields["state"], fields["trade"], fields["permit_name"],
             fields["status"], fields["applied_date"], fields["approved_date"],
             fields["permit_number"], fields["expiry_date"], fields["notes"],
             fields["result_json"], fields["created_at"], fields["updated_at"])
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[jobs] Create error: {e}")
    return fields


def list_jobs(email: str) -> list:
    scope = get_team_scope_emails(email)
    placeholders = ",".join("?" for _ in scope)
    try:
        conn = sqlite3.connect(CACHE_DB)
        rows = conn.execute(
            f"SELECT id,email,job_name,address,city,state,trade,permit_name,status,"
            f"applied_date,approved_date,permit_number,expiry_date,notes,result_json,"
            f"created_at,updated_at FROM jobs WHERE email IN ({placeholders}) ORDER BY created_at DESC",
            scope,
        ).fetchall()
        conn.close()
        cols = ["id","email","job_name","address","city","state","trade","permit_name",
                "status","applied_date","approved_date","permit_number","expiry_date",
                "notes","result_json","created_at","updated_at"]
        result = []
        for row in rows:
            d = dict(zip(cols, row))
            if d.get("result_json"):
                try: d["result_json"] = json.loads(d["result_json"])
                except: d["result_json"] = {}
            result.append(d)
        return result
    except Exception as e:
        print(f"[jobs] List error: {e}")
        return []


def user_can_access_job(job_id: str, email: str) -> bool:
    scope = get_team_scope_emails(email)
    placeholders = ",".join("?" for _ in scope)
    try:
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute(
            f"SELECT 1 FROM jobs WHERE id=? AND email IN ({placeholders})",
            [job_id, *scope],
        ).fetchone()
        conn.close()
        return bool(row)
    except Exception as e:
        print(f"[jobs] Access check error: {e}")
        return False


def update_job(job_id: str, updates: dict, email: str | None = None) -> bool:
    allowed = ["job_name","address","trade","permit_name","status",
               "applied_date","approved_date","permit_number","expiry_date","notes"]
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return False
    if email and not user_can_access_job(job_id, email):
        return False
    fields["updated_at"] = utc_now().isoformat()
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [job_id]
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(f"UPDATE jobs SET {set_clause} WHERE id=?", values)
        conn.commit()
        conn.close()
        if email and fields.get("expiry_date"):
            try:
                user_jobs = [j for j in list_jobs(email) if j.get("id") == job_id]
                if user_jobs:
                    j = user_jobs[0]
                    upsert_permit_reminder(
                        j.get("email", email),
                        j.get("job_name", ""),
                        j.get("city", ""),
                        j.get("state", ""),
                        fields.get("expiry_date", ""),
                    )
            except Exception as e:
                print(f"[jobs] Reminder sync error: {e}")
        return True
    except Exception as e:
        print(f"[jobs] Update error: {e}")
        return False


def delete_job(job_id: str, email: str | None = None) -> bool:
    if email and not user_can_access_job(job_id, email):
        return False
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[jobs] Delete error: {e}")
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
            # Prevent browser caching for HTML — always serve fresh version
            if "text/html" in content_type:
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                # Tell Railway/CDN edge NOT to cache HTML pages
                self.send_header("Surrogate-Control", "no-store")
                self.send_header("CDN-Cache-Control", "no-store")
            else:
                # Static assets (JS, CSS, images) — cache 1 hour
                self.send_header("Cache-Control", "public, max-age=3600")
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session-Token")
        self.end_headers()

    def do_PATCH(self):
        path = urlparse(self.path).path
        if path.startswith("/api/jobs/"):
            session_token = self.headers.get("X-Session-Token", "")
            user_email = validate_session_token(session_token) if session_token else None
            if not user_email:
                self.send_json(401, {"error": "Not authenticated"})
                return
            job_id = path[len("/api/jobs/"):].strip("/")
            if not job_id:
                self.send_json(400, {"error": "Job ID required"})
                return
            try:
                updates = self.read_json_body()
                ok = update_job(job_id, updates, email=user_email)
                self.send_json(200 if ok else 404, {"updated": ok})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
        else:
            self.send_json(404, {"error": "Not found"})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/jobs/"):
            session_token = self.headers.get("X-Session-Token", "")
            user_email = validate_session_token(session_token) if session_token else None
            if not user_email:
                self.send_json(401, {"error": "Not authenticated"})
                return
            job_id = path[len("/api/jobs/"):].strip("/")
            if not job_id:
                self.send_json(400, {"error": "Job ID required"})
                return
            ok = delete_job(job_id, email=user_email)
            self.send_json(200 if ok else 404, {"deleted": ok})
        else:
            self.send_json(404, {"error": "Not found"})

    def do_GET(self):
        path = urlparse(self.path).path
        # Admin API GET endpoints
        if path.startswith("/api/admin/"):
            self.do_GET_admin(path)
            return
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
        elif path in ("/cities", "/cities.html", "/cities/"):
            self.send_file(os.path.join(FRONTEND_DIR, "cities.html"), "text/html; charset=utf-8")
        elif path in ("/terms", "/terms.html", "/terms/"):
            self.send_file(os.path.join(FRONTEND_DIR, "terms.html"), "text/html; charset=utf-8")
        elif path in ("/privacy", "/privacy.html", "/privacy/"):
            self.send_file(os.path.join(FRONTEND_DIR, "privacy.html"), "text/html; charset=utf-8")
        elif path in ("/login", "/login.html", "/login/"):
            self.send_file(os.path.join(FRONTEND_DIR, "login.html"), "text/html; charset=utf-8")
        elif path in ("/help", "/help.html", "/help/"):
            self.send_file(os.path.join(FRONTEND_DIR, "help.html"), "text/html; charset=utf-8")
        elif path in ("/pricing", "/pricing.html", "/pricing/"):
            self.send_file(os.path.join(FRONTEND_DIR, "pricing.html"), "text/html; charset=utf-8")
        elif path in ("/review", "/review.html", "/review/"):
            self.send_file(os.path.join(FRONTEND_DIR, "review.html"), "text/html; charset=utf-8")
        elif path in ("/admin", "/admin.html", "/admin/"):
            self.send_file(os.path.join(FRONTEND_DIR, "admin.html"), "text/html; charset=utf-8")
        elif path == "/health":
            self.send_json(200, {"status": "ok", "service": "PermitAssist"})

        # ── Account page (Task 5) ───────────────────────────────────────────────
        elif path in ("/account", "/account/"):
            self.send_file(os.path.join(FRONTEND_DIR, "account.html"), "text/html; charset=utf-8")

        # ── GET /api/account ──────────────────────────────────────────────────
        elif path == "/api/account":
            session_token = self.headers.get("X-Session-Token", "")
            user_email = validate_session_token(session_token) if session_token else None
            if not user_email:
                self.send_json(401, {"error": "Not authenticated"})
                return
            user  = get_or_create_user(user_email)
            count = get_monthly_lookup_count(user_email)
            paid  = is_paid_user(user_email)
            now_dt = utc_now()
            if now_dt.month == 12:
                reset_date = f"{now_dt.year + 1}-01-01"
            else:
                reset_date = f"{now_dt.year}-{now_dt.month + 1:02d}-01"
            try:
                conn = sqlite3.connect(CACHE_DB)
                team_rows = conn.execute(
                    "SELECT member_email FROM team_members WHERE owner_email=?",
                    [user_email]
                ).fetchall()
                conn.close()
                team_members = [r[0] for r in team_rows]
            except Exception:
                team_members = []
            self.send_json(200, {
                "email":              user_email,
                "plan":               user.get("plan", "free"),
                "paid":               paid,
                "lookups_this_month": count,
                "lookups_remaining":  -1 if paid else max(0, FREE_LOOKUPS_PER_MONTH - count),
                "reset_date":         reset_date,
                "plan_expires_at":    user.get("plan_expires_at"),
                "team_members":       team_members,
            })

        # ── GET /api/verify-magic (Task 1) ───────────────────────────────────
        elif path == "/api/verify-magic":
            qs    = parse_qs(urlparse(self.path).query)
            token = (qs.get("token", [""])[0] or "").strip().upper()
            def _magic_page(title, icon, msg, link_label, link_href, color):
                return (
                    f"<!DOCTYPE html><html><head><meta charset='UTF-8'/>"
                    f"<meta name='viewport' content='width=device-width,initial-scale=1'/>"
                    f"<title>{title} — PermitAssist</title>"
                    f"<style>body{{font-family:system-ui,sans-serif;background:#0b1220;color:#f0f4ff;"
                    f"display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center;margin:0}}"
                    f".box{{max-width:420px;padding:40px 20px}}"
                    f".icon{{font-size:56px;margin-bottom:16px}}"
                    f"h1{{font-size:24px;font-weight:800;color:{color};margin-bottom:12px}}"
                    f"p{{color:#b8c5e0;margin-bottom:24px;line-height:1.6}}"
                    f"a{{display:inline-block;background:#1a56db;color:#fff;padding:12px 28px;border-radius:8px;font-weight:700;text-decoration:none}}"
                    f"</style></head><body><div class='box'><div class='icon'>{icon}</div>"
                    f"<h1>{title}</h1><p>{msg}</p><a href='{link_href}'>{link_label}</a></div></body></html>"
                )
            if not token:
                html = _magic_page("Invalid Link", "⚠️", "This magic link is missing a token.", "Back to PermitAssist", "/", "#ef4444")
                self.send_response(400); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(html.encode()); return
            try:
                conn = sqlite3.connect(CACHE_DB)
                row = conn.execute(
                    "SELECT email, expires_at FROM magic_tokens WHERE token=?", [token]
                ).fetchone()
                if not row:
                    conn.close()
                    html = _magic_page("Invalid Code", "❌", "This login code is not recognised. Check the code or request a new one.", "Back to PermitAssist", "/", "#ef4444")
                    self.send_response(400); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(html.encode()); return
                email_m, exp_m = row
                if utc_now() > parse_timestamp(exp_m):
                    conn.execute("DELETE FROM magic_tokens WHERE token=?", [token])
                    conn.commit(); conn.close()
                    html = _magic_page("Link Expired", "⏰", "This login link has expired. Request a new one from the homepage.", "Get New Link", "/", "#f59e0b")
                    self.send_response(410); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(html.encode()); return
                conn.execute("DELETE FROM magic_tokens WHERE token=?", [token])
                conn.commit(); conn.close()
                session = create_session_token(email_m)
                from urllib.parse import quote as _quote
                redirect_url = f"/?t={_quote(session, safe='')}&verified=1"
                self.send_response(302)
                self.send_header("Location", redirect_url)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
            except Exception as e:
                print(f"[verify-magic] Error: {e}")
                import traceback; traceback.print_exc()
                self.send_json(500, {"error": "Server error"})

        # ── Shared result pages /s/[slug] ────────────────────────────────────────
        elif path.startswith("/s/"):
            slug = path[3:].strip("/")[:20]  # max 20 chars, no traversal
            if not slug or not slug.replace("-", "").replace("_", "").isalnum():
                self.send_response(400); self.end_headers(); return
            share = get_share(slug)
            if not share:
                # Expired or not found
                html_gone = """<!DOCTYPE html><html><head><meta charset='UTF-8'/>
<meta name='viewport' content='width=device-width,initial-scale=1'/>
<title>Link Expired — PermitAssist</title>
<style>body{font-family:system-ui,sans-serif;background:#0b1220;color:#f0f4ff;display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center}
.box{max-width:400px;padding:32px 20px}.icon{font-size:56px;margin-bottom:16px}
h1{font-size:24px;font-weight:800;margin-bottom:10px}p{color:#b8c5e0;margin-bottom:24px;line-height:1.6}
a{display:inline-block;background:#1a56db;color:#fff;padding:11px 28px;border-radius:8px;font-weight:700;text-decoration:none}</style></head>
<body><div class='box'><div class='icon'>⏰</div><h1>Link Expired</h1>
<p>This shared result link is no longer active. Shared links expire after 30 days.</p>
<a href='/'>Look Up Your Permits →</a></div></body></html>"""
                self.send_response(410)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html_gone.encode())
                return
            html = render_share_page(share)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html.encode())))
            self.end_headers()
            self.wfile.write(html.encode())
        elif path == "/api/stats":
            self.send_json(200, get_lookup_stats())

        elif path == "/api/verified-cities":
            try:
                import sys
                sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
                from auto_verify import get_verified_cities
                cities = get_verified_cities()
                self.send_json(200, {"verified_cities": cities, "count": len(cities)})
            except Exception as e:
                self.send_json(200, {"verified_cities": [], "count": 0, "note": str(e)})

        elif path == "/api/jobs":
            session_token = self.headers.get("X-Session-Token", "")
            user_email = validate_session_token(session_token) if session_token else None
            if not user_email:
                self.send_json(401, {"error": "Not authenticated"})
                return
            self.send_json(200, {"jobs": list_jobs(user_email)})

        # ── GET /api/referral-link ────────────────────────────────────────────────────
        elif path == "/api/referral-link":
            session_token = self.headers.get("X-Session-Token", "")
            user_email = validate_session_token(session_token) if session_token else None
            if not user_email:
                self.send_json(401, {"error": "Not authenticated"})
                return
            ref_code = ensure_referral_record(user_email)
            ref_url = f"{APP_BASE_URL}/?ref={ref_code}"
            self.send_json(200, {"ref_code": ref_code, "ref_url": ref_url})

        elif path == "/api/billing-portal":
            session_token = self.headers.get("X-Session-Token", "")
            user_email = validate_session_token(session_token) if session_token else None
            if not user_email:
                self.send_json(401, {"error": "Not authenticated"})
                return
            user = get_user(user_email)
            if not user or not user.get("stripe_customer_id"):
                self.send_json(400, {"error": "No Stripe customer found for this account"})
                return
            portal_url = create_billing_portal_session(user["stripe_customer_id"], APP_BASE_URL + "/account")
            if not portal_url:
                self.send_json(500, {"error": "Billing portal unavailable"})
                return
            self.send_json(200, {"url": portal_url})

        elif path == "/api/review-queue":
            admin_token = self.headers.get("X-Admin-Token", "")
            if not ADMIN_TOKEN:
                self.send_json(403, {"error": "Admin review queue not configured"})
                return
            if admin_token != ADMIN_TOKEN:
                self.send_json(401, {"error": "Invalid admin token"})
                return
            qs = parse_qs(urlparse(self.path).query)
            limit = int((qs.get("limit", ["50"])[0] or "50").strip() or "50")
            limit = max(1, min(limit, 200))
            self.send_json(200, get_review_queue(limit=limit))

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
            if os.path.isdir(full):
                full = os.path.join(full, "index.html")
            elif not os.path.exists(full) and not os.path.splitext(full)[1]:
                html_full = full + ".html"
                if os.path.exists(html_full):
                    full = html_full
            base_name = os.path.basename(full)
            if base_name.startswith('.') or base_name.endswith('.backup'):
                self.send_response(404); self.end_headers(); return
            if not os.path.exists(full):
                self.send_response(404); self.end_headers(); return
            ext = os.path.splitext(full)[1].lower()
            self.send_file(full, mime_map.get(ext, "application/octet-stream"))

    def do_GET_admin(self, path):
        """Handle GET requests for admin API endpoints."""
        admin_token = self.headers.get("X-Admin-Token", "")
        if not ADMIN_TOKEN or admin_token != ADMIN_TOKEN:
            self.send_json(401, {"error": "Admin token required"})
            return True

        if path == "/api/admin/stats":
            try:
                import sqlite3 as _sqlite3
                conn = _sqlite3.connect(CACHE_DB)
                cache_count    = conn.execute("SELECT COUNT(*) FROM permit_cache").fetchone()[0]
                cache_hits     = conn.execute("SELECT SUM(hits) FROM permit_cache").fetchone()[0] or 0
                top_queries    = conn.execute(
                    "SELECT job_type, city, state, hits FROM permit_cache ORDER BY hits DESC LIMIT 20"
                ).fetchall()
                feedback_count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
                user_count     = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                sub_count      = conn.execute(
                    "SELECT COUNT(*) FROM users WHERE plan != 'free'"
                ).fetchone()[0]
                conn.close()
                self.send_json(200, {
                    "cache_entries":    cache_count,
                    "cache_hits_total": cache_hits,
                    "feedback_flags":   feedback_count,
                    "total_users":      user_count,
                    "paid_users":       sub_count,
                    "top_queries":      [{"job_type": r[0], "city": r[1], "state": r[2], "hits": r[3]} for r in top_queries]
                })
            except Exception as e:
                self.send_json(500, {"error": str(e)})
            return True

        if path == "/api/admin/flags":
            try:
                import sqlite3 as _sqlite3
                conn = _sqlite3.connect(CACHE_DB)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_type TEXT, city TEXT, state TEXT,
                        issue TEXT, submitted_at TEXT
                    )
                """)
                rows  = conn.execute(
                    "SELECT id, job_type, city, state, issue, submitted_at "
                    "FROM feedback ORDER BY submitted_at DESC LIMIT 200"
                ).fetchall()
                total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
                conn.close()
                flags = [{"id": r[0], "job_type": r[1], "city": r[2],
                          "state": r[3], "issue": r[4], "submitted_at": r[5]} for r in rows]
                self.send_json(200, {"flags": flags, "total": total})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
            return True

        if path == "/api/admin/referral-credits":
            try:
                import sqlite3 as _sqlite3
                conn = _sqlite3.connect(CACHE_DB)
                rows = conn.execute(
                    "SELECT ref_code, referrer_email, referred_email, subscribed_at "
                    "FROM referrals WHERE credit_flagged=1 ORDER BY subscribed_at DESC"
                ).fetchall()
                conn.close()
                credits = [{"ref_code": r[0], "referrer_email": r[1],
                            "referred_email": r[2], "subscribed_at": r[3]} for r in rows]
                self.send_json(200, {"pending_credits": credits, "count": len(credits)})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
            return True

        return False  # not handled

    def do_POST(self):
        path = urlparse(self.path).path

        # ── Permit lookup ─────────────────────────────────────────────────
        if path == "/api/permit":
            try:
                data     = self.read_json_body()
                job_type     = data.get("job_type", "").strip()
                city         = data.get("city", "").strip()
                state        = data.get("state", "").strip()
                zip_code     = data.get("zip_code", "").strip()
                job_category = data.get("job_category", "residential").strip() or "residential"

                if not job_type or not city or not state:
                    self.send_json(400, {"error": "job_type, city, and state are required"})
                    return

                ip = self.client_ip()

                # ── Sample demo flag — skip all counting/rate-limiting ──────────
                is_sample_demo = self.headers.get("X-Sample-Demo") == "1"

                # ── Session-based auth (email → server-side monthly limit) ──────
                session_token = self.headers.get("X-Session-Token", "")
                user_email    = validate_session_token(session_token) if session_token else None

                if user_email:
                    paid          = is_paid_user(user_email)
                    monthly_count = get_monthly_lookup_count(user_email)
                    email_limited = (not paid) and (monthly_count >= FREE_LOOKUPS_PER_MONTH)
                    print(f"[permit] {job_type} in {city}, {state} — user={user_email} plan={'paid' if paid else 'free'} count={monthly_count}")
                    limited = False  # IP limit not used for authenticated users
                else:
                    # IP-based rate limiting (guests / unauthenticated)
                    limited, _    = is_rate_limited(ip)
                    email_limited = False
                    paid          = False
                    print(f"[permit] {job_type} in {city}, {state} ({job_category}) — IP={ip}")

                result    = research_permit(job_type, city, state, zip_code, job_category=job_category)
                is_cached = result.get("_cached", False)

                if is_sample_demo:
                    # Demo lookup — never count against any limit
                    remaining_lookups = None
                    print(f"[permit] sample demo lookup — skipping all rate/count logic")
                elif not is_cached:
                    if user_email:
                        if email_limited:
                            self.send_json(429, {
                                "error": "Monthly lookup limit reached. Upgrade for unlimited access.",
                                "retry_after": 0,
                                "upgrade_url": UPGRADE_URL_SOLO,
                            })
                            return
                        if not paid:
                            new_count         = increment_monthly_lookup(user_email)
                            remaining_lookups = max(0, FREE_LOOKUPS_PER_MONTH - new_count)
                        else:
                            remaining_lookups = -1  # unlimited
                    else:
                        if limited:
                            self.send_json(429, {
                                "error": "Too many lookups. Please try again in an hour or upgrade for unlimited access.",
                                "retry_after": RATE_WINDOW_SECONDS,
                            })
                            return
                        record_fresh_lookup(ip)
                        remaining_lookups = None
                else:
                    # Cached lookup — always free, compute remaining for display
                    if user_email and not paid:
                        remaining_lookups = max(0, FREE_LOOKUPS_PER_MONTH - get_monthly_lookup_count(user_email))
                    elif user_email and paid:
                        remaining_lookups = -1
                    else:
                        remaining_lookups = None

                # Inject server-side lookup count into result
                if remaining_lookups is not None:
                    result["remaining_lookups"] = remaining_lookups

                # Validate URLs (only on fresh results — cached already verified)
                if not is_cached:
                    result = sanitize_result_urls(result)
                    # Strip PDF from apply_url → apply_pdf (server-side safety net)
                    result = strip_pdf_from_result(result)
                    # Ensure apply_google_maps always set (prefer pinned address)
                    if not result.get('apply_google_maps'):
                        result['apply_google_maps'] = build_google_maps_url(
                            city, state,
                            address=result.get('apply_address', ''),
                            office=result.get('applying_office', '')
                        )
                    # Ensure apply_phone is never completely empty
                    if not result.get('apply_phone'):
                        result['apply_phone'] = result.get('apply_google_maps', '')

                # Record stats
                record_lookup_stat(job_type, city, state, is_cached)

                # No Telegram on lookups — only notify on paying customers

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

                ts = utc_now().isoformat()
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

        # ── Expiry reminder ───────────────────────────────────────────────
        elif path == "/api/expiry-reminder":
            try:
                data      = self.read_json_body()
                email     = data.get("email", "").strip().lower()
                job_type  = data.get("job_type", "").strip()
                city      = data.get("city", "").strip()
                state     = data.get("state", "").strip()
                expiry    = data.get("expiry_date", "").strip()
                if not email or "@" not in email:
                    self.send_json(400, {"error": "Valid email required"})
                    return
                save_email_capture(email, "expiry-reminder")
                reminder = upsert_permit_reminder(email, job_type, city, state, expiry)
                # Send confirmation email in background thread
                def _send_reminder_confirm():
                    expiry_line = f"\nPermit expiry: {expiry}" if expiry else ""
                    remind_line = f"\nReminder date: {reminder.get('remind_at', '')[:10]}" if reminder.get('remind_at') else ""
                    body = (
                        f"Hi,\n\n"
                        f"You've set a permit expiry reminder for:\n"
                        f"  Job: {job_type or 'your job'}\n"
                        f"  Location: {city}{', ' + state if state else ''}{expiry_line}{remind_line}\n\n"
                        f"We'll remind you {REMINDER_LOOKAHEAD_DAYS} days before your permit expires so you have "
                        f"time to renew or close out inspections.\n\n"
                        f"Questions? Just reply to this email.\n\n"
                        f"— PermitAssist\n"
                        f"permitassist.io"
                    )
                    subject = f"Reminder set: {job_type or 'Permit'} in {city}, {state}"
                    result = resend_send(email, subject, body)
                    if result:
                        print(f"[expiry-reminder] Confirmation sent to {email}")
                    else:
                        print(f"[expiry-reminder] Email failed for {email}")
                threading.Thread(target=_send_reminder_confirm, daemon=True).start()
                self.send_json(200, {"saved": True, "reminder_id": reminder["id"], "remind_at": reminder.get("remind_at", "")})
            except Exception as e:
                print(f"[expiry-reminder] Error: {e}")
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
        # ── Share result ──────────────────────────────────────────────────────
        elif path == "/api/share":
            try:
                data     = self.read_json_body()
                job_type = data.get("job_type", "").strip()
                city     = data.get("city", "").strip()
                state    = data.get("state", "").strip()
                result   = data.get("result", {})
                if not job_type or not city or not state or not result:
                    self.send_json(400, {"error": "job_type, city, state, result required"})
                    return
                slug = create_share(job_type, city, state, result)
                host = self.headers.get("Host", "permitassist.io")
                scheme = "https" if "railway" in host or "permitassist" in host else "http"
                share_url = f"{scheme}://{host}/s/{slug}"
                self.send_json(200, {"url": share_url, "slug": slug, "expires_days": SHARE_TTL_DAYS})
            except Exception as e:
                print(f"[share] Error: {e}")
                self.send_json(500, {"error": str(e)})

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
                # Run SMTP in thread with 10s timeout to prevent handler hang
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(send_email_report, email, job, city, state, rdata)
                    try:
                        sent = future.result(timeout=10)
                    except concurrent.futures.TimeoutError:
                        sent = False
                        print("[email-report] SMTP timed out")
                self.send_json(200, {"sent": sent})
            except Exception as e:
                print(f"[email-report] Error: {e}")
                self.send_json(500, {"error": str(e)})

        # ── Jobs API POST ────────────────────────────────────────────────
        elif path == "/api/jobs":
            try:
                session_token = self.headers.get("X-Session-Token", "")
                user_email = validate_session_token(session_token) if session_token else None
                if not user_email:
                    self.send_json(401, {"error": "Not authenticated"})
                    return
                data  = self.read_json_body()
                job_name = data.get("job_name", "").strip()
                city  = data.get("city", "").strip()
                state = data.get("state", "").strip()
                if not job_name or not city or not state:
                    self.send_json(400, {"error": "job_name, city, state required"})
                    return
                job = create_job(
                    user_email, job_name, city, state,
                    address=data.get("address", ""),
                    trade=data.get("trade", ""),
                    permit_name=data.get("permit_name", ""),
                    status=data.get("status", "planning"),
                    notes=data.get("notes", ""),
                    expiry_date=data.get("expiry_date", ""),
                    result_json=data.get("result_json"),
                )
                if job.get("expiry_date"):
                    upsert_permit_reminder(user_email, job_name, city, state, job.get("expiry_date", ""))
                self.send_json(201, {"job": job})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        # ── Magic link auth (Task 1) ─────────────────────────────────────
        elif path == "/api/magic-link":
            try:
                data  = self.read_json_body()
                email = data.get("email", "").strip().lower()
                if not email or "@" not in email:
                    self.send_json(400, {"error": "Valid email required"})
                    return
                # Rate limit: max 1 magic link per email per 60 seconds
                conn_check = sqlite3.connect(CACHE_DB)
                recent = conn_check.execute(
                    "SELECT created_at FROM magic_tokens WHERE email=? ORDER BY created_at DESC LIMIT 1",
                    [email]
                ).fetchone()
                conn_check.close()
                if recent:
                    try:
                        last_sent = parse_timestamp(recent[0])
                        if (utc_now() - last_sent).total_seconds() < 60:
                            self.send_json(429, {"error": "Please wait 60 seconds before requesting another code", "retry_after": 60})
                            return
                    except Exception:
                        pass
                # Generate 6-char uppercase alphanumeric token
                chars = string.ascii_uppercase + string.digits
                token = "".join(secrets.choice(chars) for _ in range(6))
                now   = utc_now()
                exp   = now + timedelta(minutes=15)
                conn  = sqlite3.connect(CACHE_DB)
                conn.execute(
                    "INSERT OR REPLACE INTO magic_tokens (token, email, expires_at, created_at) VALUES (?,?,?,?)",
                    (token, email, exp.isoformat(), now.isoformat())
                )
                conn.commit()
                conn.close()
                is_new_user = get_user(email) is None
                get_or_create_user(email)
                sent = send_magic_link_email(email, token)
                # If email failed (Railway SMTP blocked), return token in response
                # so frontend can show it on screen as fallback
                resp = {"sent": sent, "expires_in": 900}
                if not sent:
                    resp["code"] = token  # show code on screen
                # Schedule onboarding drip for new users
                if is_new_user:
                    threading.Thread(target=schedule_onboarding_emails, args=(email,), daemon=True).start()
                    # Free signups — no Telegram alert (only notify on paid conversions)
                    # Also record referral if ref_code in request
                    ref_code = (data or {}).get("ref_code", "").strip()
                    if ref_code:
                        threading.Thread(target=record_referral_signup, args=(ref_code, email), daemon=True).start()
                self.send_json(200, resp)
            except Exception as e:
                print(f"[magic-link] Error: {e}")
                self.send_json(500, {"error": str(e)})

        elif path == "/api/verify-magic":
            try:
                data  = self.read_json_body()
                token = (data.get("token", "") or "").strip().upper()
                if not token:
                    self.send_json(400, {"error": "Login code required"})
                    return
                conn = sqlite3.connect(CACHE_DB)
                row = conn.execute(
                    "SELECT email, expires_at FROM magic_tokens WHERE token=?", [token]
                ).fetchone()
                if not row:
                    conn.close()
                    self.send_json(400, {"error": "Invalid or expired code"})
                    return
                email_m, exp_m = row
                if utc_now() > parse_timestamp(exp_m):
                    conn.execute("DELETE FROM magic_tokens WHERE token=?", [token])
                    conn.commit(); conn.close()
                    self.send_json(410, {"error": "Code expired"})
                    return
                conn.execute("DELETE FROM magic_tokens WHERE token=?", [token])
                conn.commit(); conn.close()
                session = create_session_token(email_m)
                self.send_json(200, {"session_token": session, "email": email_m})
            except Exception as e:
                print(f"[verify-magic-post] Error: {e}")
                import traceback; traceback.print_exc()
                self.send_json(500, {"error": "Server error"})

        # ── Stripe webhook (Task 2) ──────────────────────────────────────
        elif path == "/api/stripe-webhook":
            try:
                length    = int(self.headers.get("Content-Length", 0))
                raw_body  = self.rfile.read(length)
                sig_header = self.headers.get("Stripe-Signature", "")
                if not verify_stripe_signature(raw_body, sig_header, STRIPE_WEBHOOK_SECRET):
                    self.send_json(400, {"error": "Invalid signature"})
                    return
                event  = json.loads(raw_body)
                etype  = event.get("type", "")
                obj    = event.get("data", {}).get("object", {})
                print(f"[stripe-webhook] Event: {etype}")

                if etype in ("checkout.session.completed", "customer.subscription.created"):
                    # Extract email
                    email = (
                        obj.get("customer_details", {}).get("email")
                        or obj.get("customer_email")
                        or obj.get("metadata", {}).get("email")
                        or ""
                    )
                    # Extract price ID to determine plan
                    price_id = ""
                    if etype == "checkout.session.completed":
                        line_items = obj.get("line_items", {}).get("data") or []
                        if line_items:
                            price_id = line_items[0].get("price", {}).get("id", "")
                        if not price_id:
                            price_id = obj.get("metadata", {}).get("price_id", "")
                        # If still no price_id, re-fetch session from Stripe to get line_items
                        if not price_id and STRIPE_SECRET_KEY and obj.get("id"):
                            try:
                                resp = requests.get(
                                    f"https://api.stripe.com/v1/checkout/sessions/{obj['id']}",
                                    headers={"Authorization": f"Bearer {STRIPE_SECRET_KEY}"},
                                    params={"expand[]": "line_items"},
                                    timeout=10,
                                )
                                if resp.ok:
                                    session_data = resp.json()
                                    fetched_items = session_data.get("line_items", {}).get("data") or []
                                    if fetched_items:
                                        price_id = fetched_items[0].get("price", {}).get("id", "")
                                        print(f"[stripe-webhook] Re-fetched price_id: {price_id}")
                            except Exception as e:
                                print(f"[stripe-webhook] Failed to re-fetch session: {e}")
                    elif etype == "customer.subscription.created":
                        items = obj.get("items", {}).get("data") or []
                        if items:
                            price_id = items[0].get("price", {}).get("id", "")
                    plan = "team" if price_id == PRICE_TEAM else "solo"

                    if email:
                        email = email.lower().strip()
                        now_dt = utc_now()
                        exp_dt = now_dt + timedelta(days=365)
                        stripe_cust = obj.get("customer", "")
                        stripe_sub  = obj.get("subscription", "") or obj.get("id", "")
                        conn = sqlite3.connect(CACHE_DB)
                        conn.execute(
                            "INSERT OR IGNORE INTO users (email, plan, created_at, last_login) VALUES (?,?,?,?)",
                            (email, "free", now_dt.isoformat(), now_dt.isoformat())
                        )
                        conn.execute(
                            "UPDATE users SET plan=?, plan_expires_at=?, stripe_customer_id=?, "
                            "stripe_subscription_id=?, last_login=? WHERE email=?",
                            (plan, exp_dt.isoformat(), stripe_cust, stripe_sub, now_dt.isoformat(), email)
                        )
                        conn.commit()
                        conn.close()
                        print(f"[stripe-webhook] Upgraded {email} to {plan}")
                        notify_telegram(f"💰 <b>New Subscription</b>\nEmail: {email}\nPlan: {plan}")
                        threading.Thread(
                            target=send_confirmation_email, args=(email, plan), daemon=True
                        ).start()
                        # Flag referral credit if this user was referred
                        threading.Thread(
                            target=flag_referral_credit, args=(email,), daemon=True
                        ).start()
                    else:
                        print(f"[stripe-webhook] Could not extract email from event")

                elif etype == "customer.subscription.deleted":
                    email = obj.get("customer_email", "")
                    if not email:
                        cust_id = obj.get("customer", "")
                        if cust_id:
                            conn = sqlite3.connect(CACHE_DB)
                            row = conn.execute(
                                "SELECT email FROM users WHERE stripe_customer_id=?", [cust_id]
                            ).fetchone()
                            conn.close()
                            if row:
                                email = row[0]
                    if email:
                        conn = sqlite3.connect(CACHE_DB)
                        conn.execute(
                            "UPDATE users SET plan='free', plan_expires_at=NULL WHERE email=?",
                            [email.lower()]
                        )
                        conn.commit()
                        conn.close()
                        print(f"[stripe-webhook] Downgraded {email} to free")
                        notify_telegram(f"📉 <b>Subscription Cancelled</b>\nEmail: {email}")
                        # Send cancellation email
                        def _send_cancellation_email(to_email):
                            subject = "You've cancelled PermitAssist — we're sorry to see you go"
                            body_text = (
                                f"Hi,\n\n"
                                f"Your PermitAssist subscription has been cancelled. You'll keep access until the end of your current billing period.\n\n"
                                f"We'd love to know why you left — just reply to this email with any feedback. It helps us improve.\n\n"
                                f"If you change your mind, you can resubscribe anytime at:\n"
                                f"https://permitassist.io/pricing\n\n"
                                f"— PermitAssist\n"
                                f"permitassist.io"
                            )
                            body_html = f"""
                            <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">
                              <h2 style="color:#1e3a5f;">Subscription cancelled</h2>
                              <p style="color:#374151;">Your PermitAssist subscription has been cancelled. You'll keep access until the end of your current billing period.</p>
                              <p style="color:#374151;">We'd love to know why — just reply to this email with any feedback. It helps us improve.</p>
                              <a href="https://permitassist.io/pricing" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;margin:16px 0;">Resubscribe Anytime →</a>
                              <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
                              <p style="font-size:12px;color:#9ca3af;">PermitAssist · permitassist.io</p>
                            </div>
                            """
                            resend_send(to_email, subject, body_text, body_html)
                        threading.Thread(target=_send_cancellation_email, args=(email,), daemon=True).start()

                self.send_json(200, {"received": True})
            except json.JSONDecodeError:
                self.send_json(400, {"error": "Invalid JSON"})
            except Exception as e:
                print(f"[stripe-webhook] Error: {e}")
                import traceback; traceback.print_exc()
                self.send_json(500, {"error": str(e)})


        # ── Onboarding emails processing ──────────────────────────────────
        elif path == "/api/process-onboarding-emails":
            try:
                admin_token = self.headers.get("X-Admin-Token", "")
                if ADMIN_TOKEN and admin_token != ADMIN_TOKEN:
                    self.send_json(401, {"error": "Invalid admin token"})
                    return
                sent = process_onboarding_emails()
                self.send_json(200, {"sent": sent, "status": "ok"})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        # ── Check permit issued reminders ─────────────────────────────────
        elif path == "/api/check-permit-reminders":
            try:
                admin_token = self.headers.get("X-Admin-Token", "")
                if ADMIN_TOKEN and admin_token != ADMIN_TOKEN:
                    self.send_json(401, {"error": "Invalid admin token"})
                    return
                sent = process_permit_issued_reminders()
                self.send_json(200, {"sent": sent, "status": "ok"})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        # ── Save permit issued date (90-day reminder) ─────────────────────
        elif path == "/api/permit-issued-date":
            try:
                session_token = self.headers.get("X-Session-Token", "")
                user_email = validate_session_token(session_token) if session_token else None
                if not user_email:
                    self.send_json(401, {"error": "Not authenticated"})
                    return
                body = self.read_json_body()
                job_id = body.get("job_id", "").strip()
                job_name = body.get("job_name", "").strip()
                city = body.get("city", "").strip()
                state = body.get("state", "").strip()
                issued_date = body.get("issued_date", "").strip()
                if not job_id or not issued_date:
                    self.send_json(400, {"error": "job_id and issued_date required"})
                    return
                result = upsert_permit_issued_reminder(user_email, job_id, job_name, city, state, issued_date)
                self.send_json(200, {"saved": True, "remind_at": result.get("remind_at", "")})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        # ── Admin: referral credits ───────────────────────────────────────
        elif path == "/api/admin/referral-credits":
            try:
                admin_token = self.headers.get("X-Admin-Token", "")
                if not ADMIN_TOKEN or admin_token != ADMIN_TOKEN:
                    self.send_json(401, {"error": "Admin token required"})
                    return
                import sqlite3 as _sqlite3
                conn = _sqlite3.connect(CACHE_DB)
                rows = conn.execute(
                    "SELECT ref_code, referrer_email, referred_email, subscribed_at "
                    "FROM referrals WHERE credit_flagged=1 ORDER BY subscribed_at DESC"
                ).fetchall()
                conn.close()
                credits = [{
                    "ref_code": r[0], "referrer_email": r[1],
                    "referred_email": r[2], "subscribed_at": r[3]
                } for r in rows]
                self.send_json(200, {"pending_credits": credits, "count": len(credits)})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        # ── Admin: feedback flags ──────────────────────────────────────────
        elif path == "/api/admin/flags":
            try:
                admin_token = self.headers.get("X-Admin-Token", "")
                if not ADMIN_TOKEN or admin_token != ADMIN_TOKEN:
                    self.send_json(401, {"error": "Admin token required"})
                    return
                import sqlite3 as _sqlite3
                conn = _sqlite3.connect(CACHE_DB)
                # Ensure feedback table exists
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_type TEXT, city TEXT, state TEXT,
                        issue TEXT, submitted_at TEXT
                    )
                """)
                rows = conn.execute(
                    "SELECT id, job_type, city, state, issue, submitted_at "
                    "FROM feedback ORDER BY submitted_at DESC LIMIT 200"
                ).fetchall()
                total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
                conn.close()
                flags = [{
                    "id": r[0], "job_type": r[1], "city": r[2],
                    "state": r[3], "issue": r[4], "submitted_at": r[5]
                } for r in rows]
                self.send_json(200, {"flags": flags, "total": total})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        # ── Admin: delete a flag ───────────────────────────────────────────
        elif path == "/api/admin/flags/delete":
            try:
                admin_token = self.headers.get("X-Admin-Token", "")
                if not ADMIN_TOKEN or admin_token != ADMIN_TOKEN:
                    self.send_json(401, {"error": "Admin token required"})
                    return
                body = self.read_json_body()
                flag_id = body.get("id")
                if not flag_id:
                    self.send_json(400, {"error": "id required"})
                    return
                import sqlite3 as _sqlite3
                conn = _sqlite3.connect(CACHE_DB)
                conn.execute("DELETE FROM feedback WHERE id = ?", [flag_id])
                conn.commit()
                conn.close()
                self.send_json(200, {"deleted": True})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        # ── Admin: cache stats ─────────────────────────────────────────────
        elif path == "/api/admin/stats":
            try:
                admin_token = self.headers.get("X-Admin-Token", "")
                if not ADMIN_TOKEN or admin_token != ADMIN_TOKEN:
                    self.send_json(401, {"error": "Admin token required"})
                    return
                import sqlite3 as _sqlite3
                conn = _sqlite3.connect(CACHE_DB)
                cache_count = conn.execute("SELECT COUNT(*) FROM permit_cache").fetchone()[0]
                cache_hits  = conn.execute("SELECT SUM(hits) FROM permit_cache").fetchone()[0] or 0
                top_queries = conn.execute(
                    "SELECT job_type, city, state, hits FROM permit_cache ORDER BY hits DESC LIMIT 20"
                ).fetchall()
                feedback_count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
                user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                sub_count  = conn.execute(
                    "SELECT COUNT(*) FROM users WHERE plan != 'free'"
                ).fetchone()[0]
                conn.close()
                self.send_json(200, {
                    "cache_entries": cache_count,
                    "cache_hits_total": cache_hits,
                    "feedback_flags": feedback_count,
                    "total_users": user_count,
                    "paid_users": sub_count,
                    "top_queries": [{"job_type": r[0], "city": r[1], "state": r[2], "hits": r[3]} for r in top_queries]
                })
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        # ── Team invite (Task 7) ─────────────────────────────────────────
        elif path == "/api/chat":
            try:
                data = self.read_json_body()
                question = data.get("question", "").strip()
                context  = data.get("context", {})
                if not question:
                    self.send_json(400, {"error": "question is required"})
                    return
                city        = context.get("city", "")
                state       = context.get("state", "")
                permit_name = context.get("permit_name", "")
                job_type    = context.get("job_type", "")
                system_msg = (
                    f"You are a helpful permit assistant for contractors. "
                    f"The user just looked up permit requirements for '{job_type}' in {city}, {state}. "
                    f"The permit name is '{permit_name}'. "
                    f"Answer the user's follow-up question concisely and accurately. "
                    f"If you're unsure, say so. Keep answers under 200 words."
                )
                answer = None
                # Use Gemini 2.5 Flash (thinking disabled) — faster and cleaner for simple Q&A
                if _GEMINI_API_KEY_SERVER:
                    try:
                        _chat_gemini = _genai.GenerativeModel(
                            model_name=_CHAT_MODEL,
                            generation_config=_genai.GenerationConfig(
                                temperature=0.3,
                                max_output_tokens=350,
                                thinking_config=_genai.types.ThinkingConfig(thinking_budget=0),
                            ),
                            system_instruction=system_msg,
                        )
                        gemini_resp = _chat_gemini.generate_content(question)
                        answer = gemini_resp.text.strip()
                        print(f"[chat] Gemini 2.5 Flash answered ({len(answer)} chars)")
                    except Exception as ge:
                        print(f"[chat] Gemini failed ({ge}), falling back to OpenAI")
                        answer = None
                if answer is None:
                    # Fallback to GPT-4o-mini if Gemini unavailable
                    resp = _chat_openai_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": question}
                        ],
                        max_tokens=300,
                        temperature=0.3
                    )
                    answer = resp.choices[0].message.content.strip()
                    print(f"[chat] GPT-4o-mini fallback answered ({len(answer)} chars)")
                self.send_json(200, {"answer": answer})
            except Exception as e:
                print(f"[chat] Error: {e}")
                self.send_json(500, {"error": str(e)})

        elif path == "/api/team/invite":
            try:
                session_token = self.headers.get("X-Session-Token", "")
                owner_email   = validate_session_token(session_token) if session_token else None
                if not owner_email:
                    self.send_json(401, {"error": "Not authenticated"})
                    return
                user = get_user(owner_email)
                if not user or user.get("plan") != "team":
                    self.send_json(403, {"error": "Team plan required to invite members"})
                    return
                data         = self.read_json_body()
                invite_email = data.get("invite_email", "").strip().lower()
                if not invite_email or "@" not in invite_email:
                    self.send_json(400, {"error": "Valid invite_email required"})
                    return
                conn = sqlite3.connect(CACHE_DB)
                seat_count = conn.execute(
                    "SELECT COUNT(*) FROM team_members WHERE owner_email=?", [owner_email]
                ).fetchone()[0]
                if seat_count >= 3:
                    conn.close()
                    self.send_json(400, {"error": "Team seat limit reached (max 3 members)"})
                    return
                now_iso = utc_now().isoformat()
                conn.execute(
                    "INSERT OR IGNORE INTO team_members (owner_email, member_email, joined_at) VALUES (?,?,?)",
                    (owner_email, invite_email, now_iso)
                )
                conn.commit()
                conn.close()
                get_or_create_user(invite_email)

                def _send_team_invite():
                    owner_label = owner_email
                    subject = f"You've been invited to PermitAssist"
                    body = (
                        f"Hi,\n\n"
                        f"{owner_label} added you to their PermitAssist team.\n\n"
                        f"You can log in with this email at:\n"
                        f"{APP_BASE_URL}/account\n\n"
                        f"Once you log in, you'll be able to access the shared team workspace for permit jobs.\n\n"
                        f"— PermitAssist\n"
                        f"{APP_BASE_URL}"
                    )
                    resend_send(invite_email, subject, body)

                threading.Thread(target=_send_team_invite, daemon=True).start()
                self.send_json(200, {"invited": True, "member_email": invite_email})
            except Exception as e:
                print(f"[team/invite] Error: {e}")
                self.send_json(500, {"error": str(e)})

        else:
            self.send_json(404, {"error": "Not found"})


def background_task_worker():
    """Background worker: runs onboarding emails + permit issued reminders hourly."""
    while True:
        try:
            process_onboarding_emails()
        except Exception as e:
            print(f"[bg-worker] Onboarding error: {e}")
        try:
            process_permit_issued_reminders()
        except Exception as e:
            print(f"[bg-worker] Permit issued reminders error: {e}")
        time.sleep(REMINDER_CHECK_SECONDS)


if __name__ == "__main__":
    init_db()
    process_due_reminders()
    threading.Thread(target=reminder_worker, daemon=True).start()
    threading.Thread(target=background_task_worker, daemon=True).start()
    print(f"🚀 PermitAssist server starting on port {PORT}")
    print(f"   Rate limit: {RATE_MAX_FRESH} fresh lookups / {RATE_WINDOW_SECONDS//3600}h per IP (guests)")
    print(f"   Free tier: {FREE_LOOKUPS_PER_MONTH} lookups/month per email (auth users)")
    print(f"   Stripe webhook: {'configured' if STRIPE_WEBHOOK_SECRET else 'no STRIPE_WEBHOOK_SECRET'}")
    print(f"   Stripe portal: {'configured' if STRIPE_SECRET_KEY else 'no STRIPE_SECRET_KEY'}")
    print(f"   Telegram: {'enabled' if TG_BOT_TOKEN else 'disabled'}")
    print(f"   Open: http://localhost:{PORT}")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
