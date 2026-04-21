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
BLOG_DIR       = os.path.join(os.path.dirname(__file__), "..", "seo", "blog")
# Support RAILWAY_VOLUME_MOUNT_PATH or CACHE_DIR env var for persistent volumes
# Railway volumes are configured in the dashboard and mounted at a custom path
_default_data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
DATA_DIR = os.environ.get("CACHE_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or _default_data_dir
PORT           = int(os.environ.get("PORT", 8766))
EMAILS_CSV     = os.path.join(DATA_DIR, "captured_emails.csv")
CACHE_DB       = os.path.join(DATA_DIR, "cache.db")
SHARE_TTL_DAYS = 90  # shareable links expire after 90 days


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
GOOGLE_CLIENT_ID       = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET   = os.environ.get("GOOGLE_CLIENT_SECRET", "")
FREE_LOOKUPS_PER_MONTH = 3
UPGRADE_URL_SOLO       = "https://buy.stripe.com/4gM9AMddV9k08W9auh3VC0c"
UPGRADE_URL_ANNUAL     = "https://buy.stripe.com/fZueV63DlfIo5JX7i53VC0d"
UPGRADE_URL_TEAM       = "https://buy.stripe.com/8x25kwgq7gMs2xLauh3VC0b"
PRICE_SOLO             = "price_1TME9k43XpvaBuPhmXKDc2YC"  # $24.99/mo
PRICE_SOLO_LEGACY      = "price_1TLkkQ43XpvaBuPhhxdSRoID"   # old $19/mo (deactivated)
PRICE_SOLO_ANNUAL      = "price_1TME9y43XpvaBuPhfj9W8hgG"   # $199/yr
PRICE_TEAM             = "price_1TLkkQ43XpvaBuPh0vL7MnY4"
RESEND_API_KEY         = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL             = "hello@permitassist.io"
APP_BASE_URL           = os.environ.get("APP_BASE_URL", "https://permitassist.io").rstrip("/")
ADMIN_TOKEN            = os.environ.get("PERMITASSIST_ADMIN_TOKEN", "")
REMINDER_LOOKAHEAD_DAYS = 30
REMINDER_CHECK_SECONDS  = 3600

os.makedirs(DATA_DIR, exist_ok=True)

# ── Rate limiting ─────────────────────────────────────────────────────────────
RATE_WINDOW_SECONDS = 3600   # 1 hour
RATE_MAX_FRESH      = 3      # max fresh lookups per IP per hour (guests get same limit as free tier)

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
# Allowlist of known-good permit portal domains, skip validation for these
TRUSTED_PERMIT_DOMAINS = [
    "accela.com", "aca-prod.accela.com",
    "tylertech.com", "tylerhost.net",
    "permitportal.com",
    "viewpointcloud.com",
    "energovweb.com",
    "onlineservices.cityofchicago.org",
    "permits.desmoines.gov",
    "shapephx.phoenix.gov",
    "nashville.gov",
    "mygovernmentonline.org",
    "citizenserve.com",
    "municity.com",
    "ecode360.com",
    "civicaccess.com",
    "opengov.com",
    "laserfiche.com",
    "municode.com",
    "etrakit.net",
    "permitworks.com",
]

def validate_url(url: str, timeout: int = 4) -> bool:
    """
    HEAD request to verify a URL actually resolves.
    Returns True if reachable (2xx or 3xx), False otherwise.
    Falls back to True on timeout to avoid blocking the response.
    """
    if not url or not url.startswith("http"):
        return False

    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if any(domain == d or domain.endswith('.' + d) for d in TRUSTED_PERMIT_DOMAINS):
        return True

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


def _normalize_permit_name(name: str) -> str:
    if not name:
        return ""
    n = str(name).lower()
    replacements = {
        "structural / building": "building",
        "structural/building": "building",
        "structural racking": "building",
        "building/structural": "building",
        "electrical permit": "electrical",
        "mechanical permit": "mechanical",
        "building permit": "building",
        "gas permit": "gas",
        "hvac": "mechanical",
    }
    for old, new in replacements.items():
        n = n.replace(old, new)
    n = ''.join(ch if ch.isalnum() or ch.isspace() else ' ' for ch in n)
    n = ' '.join(n.split())
    if any(p in n for p in ["utility coordination", "utility interconnection", "interconnection"]):
        return "utility"
    if any(p in n for p in ["plumbing", "water heater", "repipe"]):
        return "plumbing"
    if "gas" in n:
        return "gas"
    if any(p in n for p in ["mechanical", "hvac", "furnace", "air handler", "mini split"]):
        return "mechanical"
    if any(p in n for p in ["electrical", "service upgrade", "disconnect", "reconnect", "temporary power", "panel replacement", "panel upgrade"]):
        return "electrical"
    if any(p in n for p in ["building", "structural", "racking", "roof penetration", "roof penetrations"]):
        return "building"
    return n


def enrich_result_response(result: dict, job_type: str, city: str, state: str) -> dict:
    permits_required = result.get("permits_required") or []
    existing = {_normalize_permit_name(p.get("permit_type", "")) for p in permits_required if isinstance(p, dict)}
    deduped_companions = []
    seen = set()
    for cp in result.get("companion_permits") or []:
        if not isinstance(cp, dict):
            continue
        norm = _normalize_permit_name(cp.get("permit_type", ""))
        if not norm or norm in existing or norm in seen:
            continue
        seen.add(norm)
        deduped_companions.append(cp)
    result["companion_permits"] = deduped_companions

    job_lower = (job_type or "").lower()
    if not result.get("inspection_booking"):
        booking_bits = []
        if result.get("apply_url"):
            booking_bits.append(f"Schedule online at {result['apply_url']}")
        if result.get("apply_phone"):
            booking_bits.append(f"Phone: {result['apply_phone']}")
        context = " ".join((result.get("pro_tips") or []) + (result.get("common_mistakes") or []))
        context_lower = context.lower()
        if "48-hour" in context_lower or "48 hour" in context_lower:
            booking_bits.append("48 hours advance notice required")
        elif "24-hour" in context_lower or "24 hour" in context_lower:
            booking_bits.append("24 hours advance notice required")
        elif booking_bits:
            booking_bits.append("Advance notice may be required, verify when booking")
        if booking_bits:
            result["inspection_booking"] = ". ".join(booking_bits) + "."

    if any(token in job_lower for token in ["solar", "pv", "roof", "roofing", "shingle"]) and not result.get("zoning_hoa_flag"):
        result["zoning_hoa_flag"] = (
            "Check HOA rules, historic district overlays, and local zoning before applying. "
            "Solar and roofing jobs may face placement, material, or visibility restrictions that can delay approval."
        )

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS checklist_cache (
            result_hash   TEXT PRIMARY KEY,
            checklist_json TEXT,
            created_at    TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS city_watch (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            email            TEXT NOT NULL,
            city             TEXT NOT NULL,
            state            TEXT NOT NULL,
            job_type         TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            last_notified_at TEXT,
            last_hash        TEXT,
            UNIQUE(email, city, state, job_type)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            key TEXT NOT NULL UNIQUE,
            name TEXT,
            created_at TEXT,
            last_used_at TEXT,
            lookup_count INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS webhook_integrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            integration_key TEXT NOT NULL UNIQUE,
            name TEXT,
            callback_url TEXT,
            field_mapping TEXT,
            created_at TEXT,
            last_triggered_at TEXT,
            trigger_count INTEGER DEFAULT 0
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

def esc_html(value) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def make_result_hash(result: dict) -> str:
    clean = {k: v for k, v in (result or {}).items() if not str(k).startswith("_")}
    raw = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def build_checklist_fallback(result: dict, job_type: str = "", city: str = "", state: str = "") -> dict:
    permits = result.get("permits_required") or []
    permit_name = result.get("permit_name") or (permits[0].get("permit_type") if permits else "Permit") or "Permit"
    fee = result.get("fee_range") or result.get("fee") or "Confirm fee with the building department"
    timeline_obj = result.get("approval_timeline") or {}
    timeline = timeline_obj.get("simple") or timeline_obj.get("complex") or "Varies by jurisdiction"
    docs = list(dict.fromkeys((result.get("what_to_bring") or []) + (result.get("requirements") or []) + (result.get("documents_needed") or [])))
    inspections = result.get("inspections") or []
    special_notes = list(dict.fromkeys((result.get("pro_tips") or [])[:2] + (result.get("common_mistakes") or [])[:2]))
    items = [
        {"label": f"Pull {permit_name} before starting work", "category": "permit", "required": True},
        {"label": f"Confirm jurisdiction for {city}, {state}", "category": "jurisdiction", "required": True},
        {"label": f"Pay permit fee: {fee}", "category": "fees", "required": True},
        {"label": f"Plan for approval timeline: {timeline}", "category": "timeline", "required": False},
    ]
    if docs:
        items.append({"label": f"Required documents: {', '.join(docs[:6])}", "category": "documents", "required": True})
    for inspection in inspections[:6]:
        label = inspection.get("stage") or inspection.get("title") or inspection.get("name") or "Inspection step"
        timing = inspection.get("timing") or inspection.get("description") or ""
        items.append({"label": f"Schedule inspection: {label}{' — ' + timing if timing else ''}", "category": "inspection", "required": False})
    for note in special_notes[:4]:
        items.append({"label": note, "category": "special", "required": False})
    return {
        "title": "Pre-Construction Compliance Checklist",
        "summary": f"Action checklist for {job_type or permit_name} in {city}, {state}",
        "items": items[:12],
    }


def generate_checklist(result: dict, job_type: str = "", city: str = "", state: str = "") -> dict:
    fallback = build_checklist_fallback(result, job_type, city, state)
    system_prompt = (
        "You generate short, practical pre-construction compliance checklists for contractors. "
        "Return JSON with keys title, summary, items. Each item must be an object with label, category, required. "
        "Use the permit lookup result. Keep items concrete, no filler, max 12 items."
    )
    user_prompt = json.dumps({
        "job_type": job_type,
        "city": city,
        "state": state,
        "result": result,
    }, indent=2)
    try:
        resp = _chat_openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=700,
        )
        parsed = json.loads(resp.choices[0].message.content)
        if isinstance(parsed, dict) and isinstance(parsed.get("items"), list) and parsed.get("items"):
            parsed.setdefault("title", fallback["title"])
            parsed.setdefault("summary", fallback["summary"])
            return parsed
    except Exception as e:
        print(f"[checklist] AI fallback used: {e}")
    return fallback


def get_or_create_checklist(result: dict, job_type: str = "", city: str = "", state: str = "") -> dict:
    result_hash = make_result_hash(result)
    try:
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute(
            "SELECT checklist_json FROM checklist_cache WHERE result_hash=?",
            [result_hash]
        ).fetchone()
        if row and row[0]:
            conn.close()
            data = json.loads(row[0])
            data["cached"] = True
            data["result_hash"] = result_hash
            return data
        checklist = generate_checklist(result, job_type, city, state)
        conn.execute(
            "INSERT OR REPLACE INTO checklist_cache (result_hash, checklist_json, created_at) VALUES (?,?,?)",
            (result_hash, json.dumps(checklist), utc_now().isoformat())
        )
        conn.commit()
        conn.close()
        checklist["cached"] = False
        checklist["result_hash"] = result_hash
        return checklist
    except Exception as e:
        print(f"[checklist] Cache error: {e}")
        checklist = build_checklist_fallback(result, job_type, city, state)
        checklist["cached"] = False
        checklist["result_hash"] = result_hash
        return checklist


def load_report_template() -> str:
    template_path = os.path.join(FRONTEND_DIR, "report.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()


def render_share_page(share: dict) -> str:
    template = load_report_template()
    payload = {
        "share": share,
        "app_base_url": APP_BASE_URL,
        "generated_at": utc_now().isoformat(),
        "checklist": get_or_create_checklist(share.get("data") or {}, share.get("job_type", ""), share.get("city", ""), share.get("state", "")),
    }
    return template.replace("__REPORT_DATA__", json.dumps(payload))


def mask_api_key(key: str) -> str:
    if not key:
        return ""
    return key[:10] + "••••••" + key[-4:]


def create_api_key(email: str, name: str = "") -> dict:
    key = f"pa_live_{secrets.token_urlsafe(24)}"
    now = utc_now().isoformat()
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO api_keys (email, key, name, created_at, last_used_at, lookup_count) VALUES (?,?,?,?,?,0)",
        (email.lower().strip(), key, (name or "API Key").strip()[:80], now, None)
    )
    conn.commit()
    key_id = cur.lastrowid
    conn.close()
    return {"id": key_id, "key": key, "name": (name or "API Key").strip()[:80], "created_at": now, "last_used_at": None, "lookup_count": 0}


def list_api_keys(email: str) -> list[dict]:
    conn = sqlite3.connect(CACHE_DB)
    rows = conn.execute(
        "SELECT id, name, key, created_at, last_used_at, lookup_count FROM api_keys WHERE email=? ORDER BY created_at DESC",
        [email.lower().strip()]
    ).fetchall()
    conn.close()
    return [{
        "id": row[0], "name": row[1] or "API Key", "key_preview": mask_api_key(row[2]),
        "created_at": row[3], "last_used_at": row[4], "lookup_count": row[5],
    } for row in rows]


def delete_api_key(email: str, key_id: str) -> bool:
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.execute("DELETE FROM api_keys WHERE id=? AND email=?", [key_id, email.lower().strip()])
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def validate_api_key(auth_header: str) -> tuple[str | None, str | None]:
    if not auth_header or not auth_header.startswith("Bearer "):
        return (None, None)
    key = auth_header.split(" ", 1)[1].strip()
    if not key:
        return (None, None)
    try:
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute("SELECT email FROM api_keys WHERE key=?", [key]).fetchone()
        if row:
            conn.execute(
                "UPDATE api_keys SET last_used_at=?, lookup_count=lookup_count+1 WHERE key=?",
                (utc_now().isoformat(), key)
            )
            conn.commit()
        conn.close()
        return ((row[0] if row else None), key)
    except Exception as e:
        print(f"[api-key] Validation error: {e}")
        return (None, None)


def create_webhook_integration(email: str, name: str, callback_url: str, field_mapping: dict | None = None) -> dict:
    integration_key = f"wh_{secrets.token_urlsafe(18)}"
    now = utc_now().isoformat()
    clean_mapping = field_mapping or {
        "job_type": "job_type",
        "city": "city",
        "state": "state",
        "callback_url": "callback_url",
        "zip_code": "zip_code",
    }
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO webhook_integrations (email, integration_key, name, callback_url, field_mapping, created_at, last_triggered_at, trigger_count) VALUES (?,?,?,?,?,?,?,0)",
        (email.lower().strip(), integration_key, (name or "Webhook").strip()[:80], callback_url.strip(), json.dumps(clean_mapping), now, None)
    )
    conn.commit()
    integration_id = cur.lastrowid
    conn.close()
    return {
        "id": integration_id,
        "name": (name or "Webhook").strip()[:80],
        "integration_key": integration_key,
        "callback_url": callback_url.strip(),
        "field_mapping": clean_mapping,
        "created_at": now,
        "last_triggered_at": None,
        "trigger_count": 0,
    }


def list_webhook_integrations(email: str) -> list[dict]:
    conn = sqlite3.connect(CACHE_DB)
    rows = conn.execute(
        "SELECT id, name, integration_key, callback_url, field_mapping, created_at, last_triggered_at, trigger_count FROM webhook_integrations WHERE email=? ORDER BY created_at DESC",
        [email.lower().strip()]
    ).fetchall()
    conn.close()
    return [{
        "id": row[0], "name": row[1] or "Webhook", "integration_key": row[2], "callback_url": row[3],
        "field_mapping": json.loads(row[4] or "{}"), "created_at": row[5], "last_triggered_at": row[6], "trigger_count": row[7],
    } for row in rows]


def delete_webhook_integration(email: str, webhook_id: str) -> bool:
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.execute("DELETE FROM webhook_integrations WHERE id=? AND email=?", [webhook_id, email.lower().strip()])
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_webhook_by_key(integration_key: str) -> dict | None:
    conn = sqlite3.connect(CACHE_DB)
    row = conn.execute(
        "SELECT id, email, name, integration_key, callback_url, field_mapping, created_at, last_triggered_at, trigger_count FROM webhook_integrations WHERE integration_key=?",
        [integration_key]
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "email": row[1], "name": row[2], "integration_key": row[3], "callback_url": row[4],
        "field_mapping": json.loads(row[5] or "{}"), "created_at": row[6], "last_triggered_at": row[7], "trigger_count": row[8],
    }


def mark_webhook_triggered(integration_key: str):
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            "UPDATE webhook_integrations SET last_triggered_at=?, trigger_count=trigger_count+1 WHERE integration_key=?",
            (utc_now().isoformat(), integration_key)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[webhook] Trigger update error: {e}")


def resolve_webhook_field(data: dict, mapping: dict, key: str, default: str = "") -> str:
    source_key = (mapping or {}).get(key) or key
    return str(data.get(source_key, default) or "").strip()


def run_webhook_lookup_async(integration: dict, payload: dict):
    def _worker():
        try:
            mapping = integration.get("field_mapping") or {}
            job_type = resolve_webhook_field(payload, mapping, "job_type")
            city = resolve_webhook_field(payload, mapping, "city")
            state = resolve_webhook_field(payload, mapping, "state")
            zip_code = resolve_webhook_field(payload, mapping, "zip_code")
            callback_url = str(payload.get("callback_url") or integration.get("callback_url") or "").strip()
            if not (job_type and city and state and callback_url):
                raise ValueError("Webhook requires job_type, city, state, and callback_url")
            result = research_permit(job_type, city, state, zip_code)
            body = {
                "ok": True,
                "job_type": job_type,
                "city": city,
                "state": state,
                "integration": integration.get("name") or "Webhook",
                "result": result,
            }
            requests.post(callback_url, json=body, timeout=20)
            mark_webhook_triggered(integration["integration_key"])
        except Exception as e:
            print(f"[webhook] Delivery error: {e}")
            callback_url = str(payload.get("callback_url") or integration.get("callback_url") or "").strip()
            if callback_url:
                try:
                    requests.post(callback_url, json={"ok": False, "error": str(e)}, timeout=20)
                except Exception:
                    pass
    threading.Thread(target=_worker, daemon=True).start()

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
        "trade": kwargs.get("trade", "") or kwargs.get("job_type", ""),
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


def _city_watch_payload_hash(result: dict) -> str:
    permits = result.get("permits_required") or []
    fees = result.get("fee_range") or result.get("fee") or result.get("cost") or ""
    key_requirements = (
        result.get("what_to_bring")
        or result.get("requirements")
        or result.get("documents_needed")
        or result.get("key_requirements")
        or []
    )
    payload = {
        "required_permits": permits,
        "fees": fees,
        "key_requirements": key_requirements,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def create_city_watch(email: str, city: str, state: str, job_type: str) -> dict:
    now = utc_now().isoformat()
    normalized_email = email.lower().strip()
    normalized_city = city.strip()
    normalized_state = state.strip().upper()
    normalized_job = job_type.strip()
    result = research_permit(normalized_job, normalized_city, normalized_state)
    initial_hash = _city_watch_payload_hash(result)
    conn = sqlite3.connect(CACHE_DB)
    conn.execute(
        """
        INSERT INTO city_watch (email, city, state, job_type, created_at, last_hash)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(email, city, state, job_type)
        DO UPDATE SET last_hash=excluded.last_hash
        """,
        (normalized_email, normalized_city, normalized_state, normalized_job, now, initial_hash)
    )
    conn.commit()
    row = conn.execute(
        "SELECT id,email,city,state,job_type,created_at,last_notified_at,last_hash FROM city_watch WHERE email=? AND city=? AND state=? AND job_type=?",
        (normalized_email, normalized_city, normalized_state, normalized_job)
    ).fetchone()
    conn.close()
    cols = ["id", "email", "city", "state", "job_type", "created_at", "last_notified_at", "last_hash"]
    return dict(zip(cols, row)) if row else {}


def list_city_watches(email: str) -> list[dict]:
    conn = sqlite3.connect(CACHE_DB)
    rows = conn.execute(
        "SELECT id,email,city,state,job_type,created_at,last_notified_at,last_hash FROM city_watch WHERE email=? ORDER BY created_at DESC",
        [email.lower().strip()]
    ).fetchall()
    conn.close()
    cols = ["id", "email", "city", "state", "job_type", "created_at", "last_notified_at", "last_hash"]
    return [dict(zip(cols, row)) for row in rows]


def delete_city_watch(watch_id: str, email: str) -> bool:
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.execute(
        "DELETE FROM city_watch WHERE id=? AND email=?",
        [watch_id, email.lower().strip()]
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def check_city_changes(email: str, city: str, state: str, job_type: str) -> dict:
    normalized_email = email.lower().strip()
    normalized_city = city.strip()
    normalized_state = state.strip().upper()
    normalized_job = job_type.strip()
    conn = sqlite3.connect(CACHE_DB)
    row = conn.execute(
        "SELECT id,last_hash FROM city_watch WHERE email=? AND city=? AND state=? AND job_type=?",
        [normalized_email, normalized_city, normalized_state, normalized_job]
    ).fetchone()
    if not row:
        conn.close()
        return {"watched": False, "changed": False}
    watch_id, last_hash = row
    result = research_permit(normalized_job, normalized_city, normalized_state)
    current_hash = _city_watch_payload_hash(result)
    changed = bool(last_hash and last_hash != current_hash)
    now = utc_now().isoformat()
    if changed:
        requirements = result.get("what_to_bring") or result.get("requirements") or result.get("documents_needed") or []
        permits = result.get("permits_required") or []
        body_lines = [
            "Hi,",
            "",
            "PermitAssist detected a change in permit requirements for:",
            f"Job: {normalized_job}",
            f"Location: {normalized_city}, {normalized_state}",
            "",
            "Required permits:",
        ]
        body_lines.extend([f"- {p.get('permit_type', 'Permit')}" for p in permits] or ["- Review latest result in PermitAssist"])
        body_lines.extend([
            "",
            f"Fees: {result.get('fee_range') or result.get('fee') or 'Check with city'}",
        ])
        if requirements:
            body_lines.extend(["", "Key requirements:"])
            body_lines.extend([f"- {item}" for item in requirements[:8]])
        body_lines.extend(["", "Open PermitAssist to review the latest full result.", "", "- PermitAssist"])
        resend_send(normalized_email, f"Permit requirements changed: {normalized_job} in {normalized_city}, {normalized_state}", "\n".join(body_lines))
        conn.execute(
            "UPDATE city_watch SET last_hash=?, last_notified_at=? WHERE id=?",
            [current_hash, now, watch_id]
        )
    else:
        conn.execute("UPDATE city_watch SET last_hash=? WHERE id=?", [current_hash, watch_id])
    conn.commit()
    conn.close()
    return {"watched": True, "changed": changed, "last_hash": current_hash}


def get_rejection_fix_plan(job_id: str, rejection_text: str, city: str, state: str, job_type: str) -> str:
    cache_key = hashlib.sha256(f"rejection-fix|{rejection_text}|{city}|{state}|{job_type}".encode()).hexdigest()
    try:
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute(
            "SELECT checklist_json FROM checklist_cache WHERE result_hash=?",
            [cache_key]
        ).fetchone()
        conn.close()
        if row and row[0]:
            cached = json.loads(row[0])
            if cached.get("fix_plan"):
                return cached["fix_plan"]
    except Exception as e:
        print(f"[rejection-fix] Cache read error: {e}")

    prompt = (
        f"A contractor received this permit rejection comment: {rejection_text}. "
        f"The project is {job_type} in {city}, {state}. Give a clear, actionable fix plan: "
        f"what documents to resubmit, what to correct, and what to say when resubmitting. "
        f"Be specific and practical."
    )
    resp = _chat_openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a permit consultant helping contractors resolve permit rejections. Return plain text only."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=700,
    )
    fix_plan = (resp.choices[0].message.content or "").strip()
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            "INSERT OR REPLACE INTO checklist_cache (result_hash, checklist_json, created_at) VALUES (?,?,?)",
            (cache_key, json.dumps({"job_id": job_id, "fix_plan": fix_plan}), utc_now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[rejection-fix] Cache write error: {e}")
    return fix_plan


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
        content_length = self.headers.get("Content-Length")
        if content_length is not None and int(content_length) > 0:
            return json.loads(self.rfile.read(int(content_length)))
        # Fallback: handle chunked transfer encoding (Railway CDN may strip Content-Length)
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if "chunked" in transfer_encoding:
            data = b""
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    break
                try:
                    chunk_size = int(line, 16)
                except ValueError:
                    break
                if chunk_size == 0:
                    break
                data += self.rfile.read(chunk_size)
                self.rfile.read(2)  # consume CRLF
            return json.loads(data)
        # Last resort: try reading available data
        import io
        data = b""
        while True:
            try:
                chunk = self.rfile.read1(65536)
                if not chunk:
                    break
                data += chunk
            except Exception:
                break
        if data:
            return json.loads(data)
        raise json.JSONDecodeError("Empty request body", "", 0)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session-Token, Authorization")
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
        session_token = self.headers.get("X-Session-Token", "")
        user_email = validate_session_token(session_token) if session_token else None
        if path.startswith("/api/jobs/"):
            if not user_email:
                self.send_json(401, {"error": "Not authenticated"})
                return
            job_id = path[len("/api/jobs/"):].strip("/")
            if not job_id:
                self.send_json(400, {"error": "Job ID required"})
                return
            ok = delete_job(job_id, email=user_email)
            self.send_json(200 if ok else 404, {"deleted": ok})
        elif path.startswith("/api/city-watch/"):
            if not user_email:
                self.send_json(401, {"error": "Not authenticated"})
                return
            watch_id = path[len("/api/city-watch/"):].strip("/")
            if not watch_id:
                self.send_json(400, {"error": "Watch ID required"})
                return
            ok = delete_city_watch(watch_id, user_email)
            self.send_json(200 if ok else 404, {"deleted": ok})
        elif path.startswith("/api/integrations/api-key/"):
            if not user_email:
                self.send_json(401, {"error": "Not authenticated"})
                return
            key_id = path[len("/api/integrations/api-key/"):].strip("/")
            if not key_id:
                self.send_json(400, {"error": "API key ID required"})
                return
            ok = delete_api_key(user_email, key_id)
            self.send_json(200 if ok else 404, {"deleted": ok})
        elif path.startswith("/api/integrations/webhook/"):
            if not user_email:
                self.send_json(401, {"error": "Not authenticated"})
                return
            webhook_id = path[len("/api/integrations/webhook/"):].strip("/")
            if not webhook_id:
                self.send_json(400, {"error": "Webhook ID required"})
                return
            ok = delete_webhook_integration(user_email, webhook_id)
            self.send_json(200 if ok else 404, {"deleted": ok})
        else:
            self.send_json(404, {"error": "Not found"})

    def do_HEAD(self):
        # Delegate to do_GET but suppress the response body so HEAD requests
        # return proper status codes + headers (monitors, SEO crawlers, curl -I).
        # Python's default BaseHTTPRequestHandler returns 501 for HEAD, which
        # made the site look broken to health checkers even though GET worked.
        import io
        real_wfile = self.wfile
        dummy = io.BytesIO()
        class _HeadWriter:
            def __init__(self, inner):
                self._inner = inner
            def write(self, data):
                # Swallow body writes, but still count bytes if anything cares
                return len(data) if isinstance(data, (bytes, bytearray)) else 0
            def flush(self):
                try:
                    self._inner.flush()
                except Exception:
                    pass
            def __getattr__(self, name):
                return getattr(self._inner, name)
        # Replace wfile so headers go through (they use write_string etc via
        # send_response/send_header which go to wfile) — but body writes are dropped.
        # send_response/send_header actually write to self.wfile too, so we need
        # a smarter proxy: allow writes until end_headers, then drop.
        original_end_headers = self.end_headers
        state = {"headers_done": False}
        def wrapped_end_headers():
            original_end_headers()
            state["headers_done"] = True
        self.end_headers = wrapped_end_headers
        class _SmartWriter:
            def write(self, data):
                if state["headers_done"]:
                    return len(data) if isinstance(data, (bytes, bytearray)) else 0
                return real_wfile.write(data)
            def flush(self):
                try:
                    real_wfile.flush()
                except Exception:
                    pass
            def __getattr__(self, name):
                return getattr(real_wfile, name)
        self.wfile = _SmartWriter()
        try:
            self.do_GET()
        finally:
            self.wfile = real_wfile
            self.end_headers = original_end_headers

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

        # ── Trade-specific landing pages ──────────────────────────────────────
        elif path in ("/roofing", "/roofing/"):
            self.send_file(os.path.join(FRONTEND_DIR, "trades", "roofing.html"), "text/html; charset=utf-8")
        elif path in ("/plumbing", "/plumbing/"):
            self.send_file(os.path.join(FRONTEND_DIR, "trades", "plumbing.html"), "text/html; charset=utf-8")
        elif path in ("/electrical", "/electrical/"):
            self.send_file(os.path.join(FRONTEND_DIR, "trades", "electrical.html"), "text/html; charset=utf-8")
        elif path in ("/hvac", "/hvac/"):
            self.send_file(os.path.join(FRONTEND_DIR, "trades", "hvac.html"), "text/html; charset=utf-8")
        elif path in ("/solar", "/solar/"):
            self.send_file(os.path.join(FRONTEND_DIR, "trades", "solar.html"), "text/html; charset=utf-8")
        elif path in ("/terms", "/terms.html", "/terms/"):
            self.send_file(os.path.join(FRONTEND_DIR, "terms.html"), "text/html; charset=utf-8")
        elif path in ("/privacy", "/privacy.html", "/privacy/"):
            self.send_file(os.path.join(FRONTEND_DIR, "privacy.html"), "text/html; charset=utf-8")
        elif path in ("/login", "/login.html", "/login/"):
            self.send_file(os.path.join(FRONTEND_DIR, "login.html"), "text/html; charset=utf-8")
        elif path in ("/signup", "/signup.html", "/signup/", "/register", "/register/"):
            self.send_response(301)
            self.send_header("Location", "/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
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

        # ── Facebook Webhook verification (GET) ───────────────────────────────
        elif path == "/api/fb-webhook":
            params = parse_qs(urlparse(self.path).query)
            mode = params.get("hub.mode", [""])[0]
            token = params.get("hub.verify_token", [""])[0]
            challenge = params.get("hub.challenge", [""])[0]
            FB_VERIFY_TOKEN = os.environ.get("FB_WEBHOOK_VERIFY_TOKEN", "permitassist_webhook_2026")
            if mode == "subscribe" and token == FB_VERIFY_TOKEN:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(challenge.encode())
            else:
                self.send_json(403, {"error": "Forbidden"})

        # ── Account page (Task 5) ───────────────────────────────────────────────
        elif path in ("/account", "/account/"):
            self.send_file(os.path.join(FRONTEND_DIR, "account.html"), "text/html; charset=utf-8")
        elif path in ("/integrations", "/integrations/"):
            self.send_file(os.path.join(FRONTEND_DIR, "integrations.html"), "text/html; charset=utf-8")

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

        # ── Google SSO (Fix 6) ────────────────────────────────────────
        elif path == "/api/auth/google":
            if not GOOGLE_CLIENT_ID:
                self.send_json(500, {"error": "Google SSO not configured"})
                return
            redirect_uri = f"{APP_BASE_URL}/api/auth/google/callback"
            auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?client_id={GOOGLE_CLIENT_ID}&redirect_uri={redirect_uri}&response_type=code&scope=email profile&access_type=online"
            self.send_response(302)
            self.send_header("Location", auth_url)
            self.end_headers()

        elif path == "/api/auth/google/callback":
            qs = parse_qs(urlparse(self.path).query)
            code = qs.get("code", [""])[0]
            if not code:
                self.send_response(302)
                self.send_header("Location", "/login?error=google_missing_code")
                self.end_headers()
                return
            
            redirect_uri = f"{APP_BASE_URL}/api/auth/google/callback"
            token_url = "https://oauth2.googleapis.com/token"
            token_data = {
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"
            }
            try:
                r = requests.post(token_url, data=token_data, timeout=10)
                r.raise_for_status()
                token_info = r.json()
                access_token = token_info.get("access_token")
                
                user_info_r = requests.get("https://www.googleapis.com/oauth2/v2/userinfo", headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
                user_info_r.raise_for_status()
                user_info = user_info_r.json()
                email = user_info.get("email", "").lower().strip()
                
                if not email:
                    raise Exception("No email provided by Google")
                    
                get_or_create_user(email)
                session = create_session_token(email)
                from urllib.parse import quote as _quote
                final_redirect = f"/?t={_quote(session, safe='')}&verified=1"
                self.send_response(302)
                self.send_header("Location", final_redirect)
                self.end_headers()
                
            except Exception as e:
                print(f"[google-sso] Error: {e}")
                self.send_response(302)
                self.send_header("Location", "/login?error=google_failed")
                self.end_headers()

        # ── Shared result pages /report/[slug] and legacy /s/[slug] ────────────────────────────────────────
        elif path.startswith("/report/") or path.startswith("/s/"):
            prefix = "/report/" if path.startswith("/report/") else "/s/"
            slug = path[len(prefix):].strip("/")[:24]
            if not slug or not slug.replace("-", "").replace("_", "").isalnum():
                self.send_response(400); self.end_headers(); return
            share = get_share(slug)
            if not share:
                html_gone = f"""<!DOCTYPE html><html><head><meta charset='UTF-8'/>
<meta name='viewport' content='width=device-width,initial-scale=1'/>
<title>Link Expired — PermitAssist</title>
<style>body{{font-family:system-ui,sans-serif;background:#0b1220;color:#f0f4ff;display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center}}.box{{max-width:400px;padding:32px 20px}}.icon{{font-size:56px;margin-bottom:16px}}h1{{font-size:24px;font-weight:800;margin-bottom:10px}}p{{color:#b8c5e0;margin-bottom:24px;line-height:1.6}}a{{display:inline-block;background:#1a56db;color:#fff;padding:11px 28px;border-radius:8px;font-weight:700;text-decoration:none}}</style></head>
<body><div class='box'><div class='icon'>⏰</div><h1>Link Expired</h1>
<p>This shared result link is no longer active. Shared links expire after {SHARE_TTL_DAYS} days.</p>
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
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(html.encode())
        elif path == "/api/integrations":
            session_token = self.headers.get("X-Session-Token", "")
            user_email = validate_session_token(session_token) if session_token else None
            if not user_email:
                self.send_json(401, {"error": "Not authenticated"})
                return
            self.send_json(200, {
                "api_keys": list_api_keys(user_email),
                "webhooks": list_webhook_integrations(user_email),
                "paid": is_paid_user(user_email),
                "webhook_base_url": f"{APP_BASE_URL}/api/integrations/webhook/",
                "api_endpoint": f"{APP_BASE_URL}/api/v1/permit",
            })
        elif path == "/api/stats":
            self.send_json(200, get_lookup_stats())

        elif path == "/api/verified-cities":
            try:
                import sys
                sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
                from auto_verify import get_verified_cities
                cities = get_verified_cities()
                # Fallback 1: check knowledge/verified_cities.json (full 263-city list, baked into git)
                if not cities:
                    _vk_path = os.path.join(os.path.dirname(__file__), "..", "knowledge", "verified_cities.json")
                    if os.path.exists(_vk_path):
                        with open(_vk_path) as _f:
                            _vk_data = json.load(_f)
                        _seen = set()
                        for _entry in _vk_data.values():
                            _k = f"{_entry.get('city','')}|{_entry.get('state','')}"
                            if _k not in _seen and _entry.get('city') and _entry.get('state'):
                                _seen.add(_k)
                                cities.append({"city": _entry["city"], "state": _entry["state"]})
                        cities = sorted(cities, key=lambda x: (x["state"], x["city"]))
                # Fallback 2: knowledge/cities.json (150-city curated set)
                if not cities:
                    _kb_cities_path = os.path.join(os.path.dirname(__file__), "..", "knowledge", "cities.json")
                    if os.path.exists(_kb_cities_path):
                        with open(_kb_cities_path) as _f:
                            _kb = json.load(_f)
                        _seen = set()
                        for _entry in _kb.get("cities", {}).values():
                            _k = f"{_entry.get('city','')}|{_entry.get('state','')}"
                            if _k not in _seen:
                                _seen.add(_k)
                                cities.append({"city": _entry["city"], "state": _entry["state"]})
                        cities = sorted(cities, key=lambda x: (x["state"], x["city"])
                        )
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

        elif path == "/api/city-watch":
            session_token = self.headers.get("X-Session-Token", "")
            user_email = validate_session_token(session_token) if session_token else None
            if not user_email:
                self.send_json(401, {"error": "Not authenticated"})
                return
            self.send_json(200, {"watches": list_city_watches(user_email)})

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

        # ── SEO: /blog/* pages ──────────────────────────────────────────
        elif path.startswith("/blog"):
            safe_blog = path.lstrip("/blog").lstrip("/")
            if not safe_blog:
                safe_blog = "index.html"
            candidate = os.path.realpath(os.path.join(BLOG_DIR, safe_blog))
            blog_root = os.path.realpath(BLOG_DIR)
            # Security check
            if not candidate.startswith(blog_root):
                self.send_response(403); self.end_headers(); return
            # Try with .html extension
            if not os.path.exists(candidate) and not candidate.endswith(".html"):
                candidate = candidate + ".html"
            if os.path.isfile(candidate):
                ext = os.path.splitext(candidate)[1].lower()
                self.send_file(candidate, mime_map.get(ext, "text/html; charset=utf-8"))
            else:
                self.send_response(404); self.end_headers()

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

        # ── Facebook Webhook events (POST) ─────────────────────────────
        if path == "/api/fb-webhook":
            try:
                cl = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(cl) if cl > 0 else b""
                data = json.loads(body.decode("utf-8")) if body else {}
                object_type = data.get("object", "")
                entries = data.get("entry", [])
                for entry in entries:
                    # Page messaging events
                    for msg_event in entry.get("messaging", []):
                        sender_id = msg_event.get("sender", {}).get("id")
                        message = msg_event.get("message", {}).get("text", "")
                        if sender_id and message:
                            notify_telegram(f"💬 Facebook Page DM\nFrom: {sender_id}\nMessage: {message}")
                    # Feed events (comments, posts)
                    for change in entry.get("changes", []):
                        field = change.get("field", "")
                        val = change.get("value", {})
                        if field == "feed":
                            item = val.get("item", "")
                            verb = val.get("verb", "")
                            msg = val.get("message", "")
                            notify_telegram(f"📰 Facebook {item} {verb}: {msg[:200]}")
                self.send_json(200, {"status": "ok"})
            except Exception as e:
                self.send_json(200, {"status": "ok"})  # Always 200 to Facebook
            return

        # ── Debug endpoint — echo headers and body info ───────────────────
        if path == "/api/debug-headers":
            info = {
                "headers": dict(self.headers),
                "content_length": self.headers.get("Content-Length"),
                "transfer_encoding": self.headers.get("Transfer-Encoding"),
            }
            # Try reading body
            try:
                cl = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(cl) if cl > 0 else b""
                info["body_read_via_content_length"] = body.decode("utf-8", errors="replace")
            except Exception as e:
                info["body_read_error"] = str(e)
            self.send_json(200, info)
            return

        # ── Permit lookup ─────────────────────────────────────────────────
        if path == "/api/permit":
            try:
                try:
                    data = self.read_json_body()
                except json.JSONDecodeError:
                    self.send_json(400, {"error": "Invalid request body — expected JSON"})
                    return
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

                # Validate fresh URLs, then apply shared response safety nets for both fresh and cached results
                if not is_cached:
                    result = sanitize_result_urls(result)

                # Fallback: if apply_url is still null, use best URL from sources[]
                if not result.get('apply_url'):
                    sources = result.get('sources') or []
                    gov_urls = [s for s in sources if isinstance(s, str) and '.gov' in s and not s.lower().endswith('.pdf')]
                    portal_urls = [s for s in sources if isinstance(s, str) and any(p in s.lower() for p in ['accela', 'permit', 'portal', 'civic', 'govern']) and not s.lower().endswith('.pdf')]
                    other_urls = [s for s in sources if isinstance(s, str) and s.startswith('http') and not s.lower().endswith('.pdf')]
                    fallback_url = (gov_urls or portal_urls or other_urls or [None])[0]
                    if fallback_url:
                        result['apply_url'] = fallback_url
                        result['_url_warning'] = None

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

                result = enrich_result_response(result, job_type, city, state)

                # Record stats
                record_lookup_stat(job_type, city, state, is_cached)

                # No Telegram on lookups — only notify on paying customers

                self.send_json(200, result)

            except Exception as e:
                print(f"[permit] Error: {e}")
                import traceback; traceback.print_exc()
                self.send_json(500, {"error": "Lookup failed — please try again"})

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
                share_url = f"{APP_BASE_URL}/report/{slug}"
                self.send_json(200, {"url": share_url, "slug": slug, "expires_days": SHARE_TTL_DAYS})
            except Exception as e:
                print(f"[share] Error: {e}")
                self.send_json(500, {"error": str(e)})

        elif path == "/api/checklist":
            try:
                data = self.read_json_body()
                result = data.get("result") or {}
                job_type = data.get("job_type", "").strip()
                city = data.get("city", "").strip()
                state = data.get("state", "").strip()
                if not result:
                    self.send_json(400, {"error": "result is required"})
                    return
                self.send_json(200, get_or_create_checklist(result, job_type, city, state))
            except Exception as e:
                print(f"[checklist] Error: {e}")
                self.send_json(500, {"error": str(e)})

        elif path == "/api/integrations/api-key":
            try:
                session_token = self.headers.get("X-Session-Token", "")
                user_email = validate_session_token(session_token) if session_token else None
                if not user_email:
                    self.send_json(401, {"error": "Not authenticated"})
                    return
                if not is_paid_user(user_email):
                    self.send_json(403, {"error": "Paid plan required"})
                    return
                data = self.read_json_body()
                key = create_api_key(user_email, data.get("name", "API Key"))
                self.send_json(201, {"api_key": key, "api_keys": list_api_keys(user_email)})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif path == "/api/integrations/webhook":
            try:
                session_token = self.headers.get("X-Session-Token", "")
                user_email = validate_session_token(session_token) if session_token else None
                if not user_email:
                    self.send_json(401, {"error": "Not authenticated"})
                    return
                if not is_paid_user(user_email):
                    self.send_json(403, {"error": "Paid plan required"})
                    return
                data = self.read_json_body()
                callback_url = str(data.get("callback_url", "")).strip()
                if not callback_url.startswith("http"):
                    self.send_json(400, {"error": "Valid callback_url required"})
                    return
                field_mapping = data.get("field_mapping") or {}
                if isinstance(field_mapping, str):
                    try:
                        field_mapping = json.loads(field_mapping)
                    except Exception:
                        field_mapping = {}
                webhook = create_webhook_integration(user_email, data.get("name", "Webhook"), callback_url, field_mapping)
                self.send_json(201, {"webhook": webhook, "webhooks": list_webhook_integrations(user_email)})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif path.startswith("/api/integrations/webhook/"):
            try:
                integration_key = path[len("/api/integrations/webhook/"):].strip("/")
                integration = get_webhook_by_key(integration_key)
                if not integration:
                    self.send_json(404, {"error": "Integration not found"})
                    return
                data = self.read_json_body()
                run_webhook_lookup_async(integration, data)
                self.send_json(202, {"accepted": True, "integration": integration.get("name") or "Webhook", "callback_url": data.get("callback_url") or integration.get("callback_url")})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif path == "/api/v1/permit":
            try:
                user_email, _ = validate_api_key(self.headers.get("Authorization", ""))
                if not user_email:
                    self.send_json(401, {"error": "Invalid API key"})
                    return
                if not is_paid_user(user_email):
                    self.send_json(403, {"error": "Paid plan required"})
                    return
                data = self.read_json_body()
                job_type = data.get("job_type", "").strip()
                city = data.get("city", "").strip()
                state = data.get("state", "").strip()
                zip_code = data.get("zip_code", "").strip()
                job_category = data.get("job_category", "residential").strip() or "residential"
                if not job_type or not city or not state:
                    self.send_json(400, {"error": "job_type, city, and state are required"})
                    return
                result = research_permit(job_type, city, state, zip_code, job_category=job_category)
                self.send_json(200, result)
            except Exception as e:
                print(f"[api-v1-permit] Error: {e}")
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
                    trade=data.get("trade", "") or data.get("job_type", ""),
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

        elif path == "/api/city-watch":
            try:
                session_token = self.headers.get("X-Session-Token", "")
                user_email = validate_session_token(session_token) if session_token else None
                if not user_email:
                    self.send_json(401, {"error": "Not authenticated"})
                    return
                data = self.read_json_body()
                city = data.get("city", "").strip()
                state = data.get("state", "").strip()
                job_type = data.get("job_type", "").strip()
                if not city or not state or not job_type:
                    self.send_json(400, {"error": "city, state, job_type required"})
                    return
                watch = create_city_watch(user_email, city, state, job_type)
                self.send_json(201, {"watch": watch, "checked": check_city_changes(user_email, city, state, job_type)})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif path == "/api/rejection-fix":
            try:
                session_token = self.headers.get("X-Session-Token", "")
                user_email = validate_session_token(session_token) if session_token else None
                if not user_email:
                    self.send_json(401, {"error": "Not authenticated"})
                    return
                data = self.read_json_body()
                job_id = data.get("job_id", "").strip()
                rejection_text = data.get("rejection_text", "").strip()
                city = data.get("city", "").strip()
                state = data.get("state", "").strip()
                job_type = data.get("job_type", "").strip()
                if not rejection_text or not city or not state or not job_type:
                    self.send_json(400, {"error": "rejection_text, city, state, job_type required"})
                    return
                if job_id and not user_can_access_job(job_id, user_email):
                    self.send_json(403, {"error": "Access denied"})
                    return
                fix_plan = get_rejection_fix_plan(job_id, rejection_text, city, state, job_type)
                self.send_json(200, {"fix_plan": fix_plan})
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
                    # For subscription events, email is not on the object — look up the customer
                    if not email and obj.get("customer") and STRIPE_SECRET_KEY:
                        try:
                            cust_resp = requests.get(
                                f"https://api.stripe.com/v1/customers/{obj['customer']}",
                                headers={"Authorization": f"Bearer {STRIPE_SECRET_KEY}"},
                                timeout=10,
                            )
                            if cust_resp.ok:
                                email = cust_resp.json().get("email", "")
                                print(f"[stripe-webhook] Resolved email from customer lookup: {email}")
                        except Exception as e:
                            print(f"[stripe-webhook] Customer lookup failed (non-fatal): {e}")
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

        elif path == '/api/fix-rejection':
            try:
                _fix_length = int(self.headers.get('Content-Length', 0))
                _fix_raw = self.rfile.read(_fix_length)
                body = json.loads(_fix_raw)
                rejection_text = (body.get('rejection_text') or '').strip()
                job_type = (body.get('job_type') or '').strip()
                city = (body.get('city') or '').strip()
                state = (body.get('state') or '').strip()
                if not rejection_text:
                    self.send_json(400, {'error': 'rejection_text is required'})
                    return

                # Require login for this feature
                _fix_session_token = self.headers.get('X-Session-Token', '')
                _fix_user = validate_session_token(_fix_session_token) if _fix_session_token else None
                if not _fix_user:
                    self.send_json(401, {'error': 'Login required'})
                    return

                system_prompt = """You are PermitAssist, an expert permit consultant helping contractors respond to city permit rejection letters.

Your job: analyze the rejection letter and generate a professional, specific response letter the contractor can send to the building department to resolve the rejection and get their permit approved.

Response format (JSON only):
{
  "rejection_reasons": ["list of specific reasons the city rejected the permit"],
  "fix_steps": ["numbered action items the contractor must complete before resubmitting"],
  "response_letter": "Full professional letter text ready to send to the building department. Address it To: Building Department. Include: acknowledgment of rejection, specific corrections being made, resubmission statement. Professional tone. No placeholders — write it as if ready to send.",
  "code_refs": ["any relevant code sections mentioned or implied in the rejection"],
  "resubmission_tips": "1-2 sentences of practical advice for the resubmission"
}"""

                user_prompt = f"""Rejection letter from building department:
---
{rejection_text}
---

Job type: {job_type or 'not specified'}
City: {city or 'not specified'}, {state or 'not specified'}

Analyze this rejection and generate a complete response letter and fix plan."""

                result_text = ''
                try:
                    import google.generativeai as genai
                    genai.configure(api_key=_GEMINI_API_KEY_SERVER)
                    model = genai.GenerativeModel(
                        'gemini-2.5-flash',
                        generation_config=genai.types.GenerationConfig(
                            response_mime_type='application/json',
                            temperature=0.3
                        )
                    )
                    resp = model.generate_content(f"{system_prompt}\n\n{user_prompt}")
                    result_text = resp.text
                except Exception:
                    # Fallback to OpenAI
                    oai = _OpenAI()
                    resp = oai.chat.completions.create(
                        model='gpt-4.1',
                        messages=[
                            {'role': 'system', 'content': system_prompt},
                            {'role': 'user', 'content': user_prompt}
                        ],
                        response_format={'type': 'json_object'},
                        temperature=0.3
                    )
                    result_text = resp.choices[0].message.content

                parsed = json.loads(result_text)
                self.send_json(200, {'ok': True, 'result': parsed})

            except Exception as e:
                self.send_json(500, {'error': str(e)})
            return

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
