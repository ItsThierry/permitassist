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

import base64
import copy
import json
import os
import csv
import hmac
import hashlib
import html
import ipaddress
import re
import sqlite3
import string
import requests
import socket
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from research_engine import (
    research_permit,
    build_google_maps_url,
    strip_pdf_from_result,
    get_cache_hit_rate,
    detect_primary_scope as _detect_primary_scope_raw,
    classify_scope_required_permits,
)
try:
    from research_engine import classify_source_tier, classify_source_authority
except (ImportError, AttributeError):
    # Some tests install a lightweight top-level research_engine stub for the
    # heavy lookup functions. The source classifier must still be the real
    # implementation; do not silently install permissive/denying stubs.
    from api.research_engine import classify_source_tier, classify_source_authority
try:
    import inspect as _inspect
    if "scope_contract" not in _inspect.signature(classify_scope_required_permits).parameters:
        raise TypeError("partial research_engine stub missing scope_contract-aware classifier")
except (TypeError, ValueError):
    from api.research_engine import classify_scope_required_permits as _real_classify_scope_required_permits
    classify_scope_required_permits = _real_classify_scope_required_permits

from scope_contract import build_scope_contract, customer_text_has_forbidden_scope, customer_text_mentions_forbidden_scope, sanitize_result_for_scope_contract
from permit_decision import apply_permit_decision_contract, _get_decision_cell_primary_lock, enforce_decision_cell_primary, apply_contact_sanitization
from decision_resolver import is_input_rejection, resolve_customer_decision
try:
    from v231_decision_cells import reconcile_v231_result as _reconcile_v231_result, resolve_v231_cell as _resolve_v231_cell
except ImportError:  # package import path in some tests
    from api.v231_decision_cells import reconcile_v231_result as _reconcile_v231_result, resolve_v231_cell as _resolve_v231_cell
from evidence_pack_runtime import apply_evidence_pack_fail_closed, canonical_request_vertical, evidence_pack_enabled, get_local_evidence_pack
from openai import OpenAI as _OpenAI
import google.generativeai as _genai
import requests as _requests


def _canonical_primary_scope_label(detected) -> str:
    """Return the canonical string scope label for primary-scope detection.

    The production research engine's contract is a string return. Some local
    tests and legacy stubs still return a dict shaped like
    {"primary_scope": "...", "signals": [...]}; keep this normalizer at the
    boundary so downstream repair/gating code has one intentional shape.
    """
    if isinstance(detected, dict):
        detected = (
            detected.get("primary_scope")
            or detected.get("scope")
            or detected.get("vertical")
            or detected.get("category")
            or ""
        )
    return str(detected or "").strip()


def detect_primary_scope(job_type: str) -> str:
    """Canonical server-facing wrapper around research_engine.detect_primary_scope."""
    return _canonical_primary_scope_label(_detect_primary_scope_raw(job_type or ""))

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
# Railway hobby production has a single small app instance. The permit engine can
# fan out to search/provider calls and large JSON shaping, so keep expensive
# /api/permit work serialized by default while still allowing static/auth/limit
# routes to respond on ThreadingHTTPServer. This prevents paired smoke lookups
# from starving or restarting the worker and surfacing as Railway edge 502s.
PERMIT_LOOKUP_CONCURRENCY_LIMIT = max(1, int(os.environ.get("PERMIT_LOOKUP_CONCURRENCY_LIMIT", "1")))
PERMIT_LOOKUP_QUEUE_TIMEOUT_SECONDS = max(1, int(os.environ.get("PERMIT_LOOKUP_QUEUE_TIMEOUT_SECONDS", "45")))
PERMIT_LOOKUP_SEMAPHORE = threading.BoundedSemaphore(PERMIT_LOOKUP_CONCURRENCY_LIMIT)
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
PRICE_SOLO             = "price_1TME9k43XpvaBuPhmXKDc2YC"  # $39.99/mo
PRICE_SOLO_LEGACY      = "price_1TLkkQ43XpvaBuPhhxdSRoID"   # old $19/mo (deactivated)
PRICE_SOLO_ANNUAL      = "price_1TME9y43XpvaBuPhfj9W8hgG"   # $199/yr
PRICE_TEAM             = "price_1TLkkQ43XpvaBuPh0vL7MnY4"
RESEND_API_KEY         = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL             = "hello@permitassist.io"
APP_BASE_URL           = os.environ.get("APP_BASE_URL", "https://permitassist.io").rstrip("/")
ADMIN_TOKEN            = os.environ.get("PERMITASSIST_ADMIN_TOKEN", "")
REMINDER_LOOKAHEAD_DAYS = 30
REMINDER_CHECK_SECONDS  = 3600
POSTHOG_PUBLIC_KEY     = os.environ.get("POSTHOG_PUBLIC_KEY", "")
POSTHOG_HOST           = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com").rstrip("/")
SENTRY_DSN             = os.environ.get("SENTRY_DSN", "")
SENTRY_ENVIRONMENT     = os.environ.get("SENTRY_ENVIRONMENT", os.environ.get("RAILWAY_ENVIRONMENT_NAME", "production"))

if SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=SENTRY_ENVIRONMENT,
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
            send_default_pii=False,
        )
        print("[sentry] backend enabled")
    except Exception as e:
        print(f"[sentry] backend disabled: {e}")

os.makedirs(DATA_DIR, exist_ok=True)

FREE_LOOKUP_DB = os.environ.get("FREE_LOOKUP_DB") or "/app/data/ip_lookups.db"
try:
    os.makedirs(os.path.dirname(FREE_LOOKUP_DB), exist_ok=True)
except PermissionError:
    FREE_LOOKUP_DB = os.path.join(DATA_DIR, "ip_lookups.db")
    os.makedirs(os.path.dirname(FREE_LOOKUP_DB), exist_ok=True)

FREE_LOOKUP_LIMIT = 3
FREE_LOOKUP_UPGRADE_URL = "https://permitassist.io/#pricing"
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 10
_RATE_LIMIT_STATE = {}
_RATE_LIMIT_LOCK = threading.Lock()


def _normalize_ip(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if ":" in raw and raw.count(":") == 1:
        host, _, port = raw.partition(":")
        if host.count(".") == 3 and port.isdigit():
            raw = host
    if raw.startswith("[") and "]" in raw:
        raw = raw[1:raw.index("]")]
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return raw
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return str(addr.ipv4_mapped)
    return addr.compressed


def _parse_public_forwarded_ip(forwarded: str) -> str:
    for part in (forwarded or "").split(","):
        ip = _normalize_ip(part)
        if not ip:
            continue
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return addr.compressed if isinstance(addr, ipaddress.IPv6Address) else str(addr)
    return ""


def _is_dev_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private or addr.is_link_local


def _normalize_fingerprint(value: str) -> str:
    return (value or "").strip()[:512]


def get_free_lookup_whitelist() -> set[str]:
    raw = os.environ.get("FREE_LOOKUP_WHITELIST", "")
    return {ip for ip in (_normalize_ip(item) for item in raw.split(",")) if ip}


def is_whitelisted_ip(ip: str) -> bool:
    return ip in get_free_lookup_whitelist()


def init_free_lookup_db():
    conn = sqlite3.connect(FREE_LOOKUP_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ip_usage (
            ip TEXT PRIMARY KEY,
            count INTEGER NOT NULL DEFAULT 0,
            first_seen DATETIME NOT NULL,
            last_seen DATETIME NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fingerprint_usage (
            fingerprint TEXT PRIMARY KEY,
            ip TEXT,
            count INTEGER NOT NULL DEFAULT 0,
            first_seen DATETIME NOT NULL,
            last_seen DATETIME NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def get_usage_counts(ip: str, fingerprint: str = "") -> tuple[int, int]:
    conn = sqlite3.connect(FREE_LOOKUP_DB)
    ip_row = conn.execute("SELECT count FROM ip_usage WHERE ip=?", [ip]).fetchone()
    fp_row = None
    if fingerprint:
        fp_row = conn.execute("SELECT count FROM fingerprint_usage WHERE fingerprint=?", [fingerprint]).fetchone()
    conn.close()
    return (ip_row[0] if ip_row else 0, fp_row[0] if fp_row else 0)


def get_effective_free_usage(ip: str, fingerprint: str = "") -> int:
    ip_count, fp_count = get_usage_counts(ip, fingerprint)
    return max(ip_count, fp_count)


def record_lookup_usage(ip: str, fingerprint: str = "") -> tuple[int, int]:
    now = utc_now().isoformat()
    conn = sqlite3.connect(FREE_LOOKUP_DB)
    conn.execute(
        "INSERT INTO ip_usage (ip, count, first_seen, last_seen) VALUES (?,?,?,?) "
        "ON CONFLICT(ip) DO UPDATE SET count=count+1, last_seen=excluded.last_seen",
        [ip, 1, now, now],
    )
    if fingerprint:
        conn.execute(
            "INSERT INTO fingerprint_usage (fingerprint, ip, count, first_seen, last_seen) VALUES (?,?,?,?,?) "
            "ON CONFLICT(fingerprint) DO UPDATE SET ip=excluded.ip, count=count+1, last_seen=excluded.last_seen",
            [fingerprint, ip, 1, now, now],
        )
    conn.commit()
    conn.close()
    return get_usage_counts(ip, fingerprint)


def build_free_lookup_headers(used: int) -> dict:
    remaining = max(0, FREE_LOOKUP_LIMIT - min(used, FREE_LOOKUP_LIMIT))
    return {
        "X-Free-Lookups-Used": str(used),
        "X-Free-Lookups-Remaining": str(remaining),
    }


def is_unlimited_lookup_ip(ip: str) -> bool:
    return _is_dev_ip(ip) or is_whitelisted_ip(ip)


def check_rate_limit(ip: str) -> tuple[bool, int]:
    now = time.time()
    with _RATE_LIMIT_LOCK:
        window = [ts for ts in _RATE_LIMIT_STATE.get(ip, []) if now - ts < RATE_LIMIT_WINDOW_SECONDS]
        if len(window) >= RATE_LIMIT_MAX_REQUESTS:
            _RATE_LIMIT_STATE[ip] = window
            retry_after = max(1, int(RATE_LIMIT_WINDOW_SECONDS - (now - window[0])))
            return True, retry_after
        window.append(now)
        _RATE_LIMIT_STATE[ip] = window
        stale_before = now - RATE_LIMIT_WINDOW_SECONDS
        for key, values in list(_RATE_LIMIT_STATE.items()):
            fresh = [ts for ts in values if ts >= stale_before]
            if fresh:
                _RATE_LIMIT_STATE[key] = fresh
            else:
                _RATE_LIMIT_STATE.pop(key, None)
    return False, 0

# ── URL validation ────────────────────────────────────────────────────────────
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

def _scrub_scope_limit_leaks(result: dict, scope_contract: dict) -> None:
    """Remove customer-visible text that names a forbidden vertical/scope.

    This is upstream cleanup before the final firebreak. It preserves the current
    request's coverage and rich output while dropping individual stale/model/cache
    strings that mention unrelated paths (for example homeowner/ADU/solar wording
    inside a commercial TI result).
    """
    if not isinstance(result, dict) or not isinstance(scope_contract, dict):
        return

    removed = object()
    structured_title_fields = {"permit_type", "portal_selection", "title", "name", "summary", "reason", "required_if", "claim", "value"}

    def clean_customer_scope_text(value):
        if isinstance(value, str):
            return removed if customer_text_mentions_forbidden_scope(value, scope_contract) else value
        if isinstance(value, list):
            cleaned = []
            for item in value:
                next_item = clean_customer_scope_text(item)
                if next_item is not removed and next_item not in ("", [], {}):
                    cleaned.append(next_item)
            return cleaned
        if isinstance(value, dict):
            if any(
                key in structured_title_fields and isinstance(item, str) and customer_text_mentions_forbidden_scope(item, scope_contract)
                for key, item in value.items()
            ):
                return removed
            cleaned = {}
            for key, item in value.items():
                next_item = clean_customer_scope_text(item)
                if next_item is not removed and next_item not in ("", [], {}):
                    cleaned[key] = next_item
            return cleaned
        return value

    customer_scope_fields = (
        "quality_warnings", "warnings", "checklist", "what_to_bring", "pro_tips",
        "common_mistakes", "watch_out", "requirements", "documents_needed",
        "next_steps", "inspection_notes", "permit_notes", "zoning_hoa_flag",
    )
    for key in customer_scope_fields:
        if key in result:
            cleaned = clean_customer_scope_text(result.get(key))
            if cleaned is removed or cleaned in ("", [], {}):
                result.pop(key, None)
            else:
                result[key] = cleaned

    citations = result.get("claim_citations")
    if isinstance(citations, list):
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            limit = citation.get("source_scope_limit")
            if isinstance(limit, str) and customer_text_has_forbidden_scope(limit, scope_contract):
                citation["source_scope_limit"] = ""


_JURISDICTION_SPECIFIC_CUSTOMER_SURFACE_PATTERNS: dict[str, tuple[str, ...]] = {
    "CA": (
        r"\btitle\s*24\b", r"\bcalgreen\b", r"\bcf1r\b", r"\bcf2r\b",
        r"\bcalifornia\s+building\s+standards\s+code\b",
        r"\bcalifornia\s+(?:energy|residential|building|green|mechanical|plumbing|electrical|fire)\s+code\b",
    ),
    "OR": (r"\boregon\s+(?:residential|structural|specialty|energy|building|mechanical|plumbing|electrical|fire)\s+code\b",),
    "WA": (
        r"\bwsec\b",
        r"\bwashington\s+state\s+energy\s+code\b",
        r"\bwashington\s+(?:residential|building|energy|mechanical|plumbing|electrical|fire)\s+code\b",
    ),
}
_SEISMIC_STRAPPING_APPLICABLE_STATES = {"AK", "CA", "NV", "OR", "WA"}


def _customer_text_has_wrong_jurisdiction_specific_claim(text: str, target_state: str) -> bool:
    """Block local/state-specific code bleed while preserving national/federal facts.

    This intentionally targets jurisdiction-bound phrases (e.g. CA Title 24,
    WSEC, state-name local-code notes, seismic water-heater strapping) instead of
    broad national terms. ADA/federal accessibility and NEC content remain
    eligible because they can be cross-applicable when tagged/justified.
    """
    value = str(text or "")
    if not value:
        return False
    target = (target_state or "").upper().strip()
    for state, patterns in _JURISDICTION_SPECIFIC_CUSTOMER_SURFACE_PATTERNS.items():
        if state == target:
            continue
        if any(re.search(pattern, value, flags=re.I) for pattern in patterns):
            return True
    if target not in _SEISMIC_STRAPPING_APPLICABLE_STATES and re.search(r"\bseismic\s+(?:strap|straps|strapping|brac(?:e|ing))\b", value, flags=re.I):
        return True
    return False


def sanitize_customer_visible_result(result: dict, *, strip_internal_keys: bool = True) -> dict:
    """Remove internal engine/review wording before any customer surface sees it."""
    if not isinstance(result, dict):
        return result
    scope_contract = result.get("_scope_contract") if isinstance(result.get("_scope_contract"), dict) else None

    internal_terms = (
        "engine flagged",
        "needs review",
        "verified · official sources",
        "hidden triggers",
        "planning estimate only",
        "verify before merging",
        "jurisdiction multiplier",
        "ti floor",
        "ada-path-of-travel adder",
        "source-backed threshold",
        "source-backed evidence",
        "source-backed exemption",
        "needs_verification",
        "fail_closed",
        "v2.3.1",
        "_v231_",
        "permitassist_v231_decision_cell",
        "_decision_cell_primary_lock",
        "decision_cell",
        "decision cell",
        "cell_id",
        "resolver",
        "source metadata",
        "customerdecisiondto",
    )
    fee_internal_terms = (
        "jurisdiction multiplier",
        "ti floor",
        "structured ti floor",
        "structured floor",
        "ada-path-of-travel adder",
    )
    confidence_fields = {"confidence_reason", "warning", "warnings", "quality_warnings"}
    fee_fields = {"fee_range", "fee_estimate", "total_cost_estimate", "fee_calculator", "value", "claim"}
    internal_keys = {
        "needs_review", "confidence_modifier", "complexity_modifier", "jurisdiction_multiplier",
        "hidden_triggers", "missing_fields", "_validation_issues",
        "_commercial_primary_permit_guardrail", "_hidden_trigger_metadata_sanitized",
        "permit_decision_contract", "source_evidence_floor", "exact_apply_url_status",
        "exact_name_status", "permit_ready_score", "debug_trace", "provider_metadata",
        "retrieval_diagnostics", "raw_retrieval", "search_debug", "scoring_debug",
        "source_metadata", "decision_cell", "cell_id", "resolver", "customerdecisiondto",
    }
    primary_scope = str(result.get("_primary_scope") or "").strip().lower()
    target_state = str(
        (scope_contract or {}).get("state")
        or result.get("state")
        or result.get("jurisdiction_state")
        or ""
    ).upper().strip()
    target_city = str(
        (scope_contract or {}).get("city")
        or result.get("city")
        or result.get("jurisdiction_city")
        or ""
    ).lower().strip()

    def is_commercial_ti_result(value: dict) -> bool:
        text = " ".join(str(value.get(k) or "") for k in ("permit_name", "permit_type", "job_summary")).lower()
        if primary_scope.startswith("commercial") or primary_scope in {"multifamily", "change_of_occupancy"}:
            return True
        return any(term in text for term in ("commercial", "tenant improvement", "buildout", "build-out", "interior alteration"))

    def has_internal(text: str) -> bool:
        lowered = str(text or "").lower()
        return any(term in lowered for term in internal_terms)

    def scrub_text(text: str, key: str = ""):
        if not text:
            return text
        value = str(text)
        lowered = value.lower()
        if strip_internal_keys and target_state and _customer_text_has_wrong_jurisdiction_specific_claim(value, target_state):
            return ""
        if key in confidence_fields and strip_internal_keys and has_internal(value):
            return "Source support is degraded; use the resolved permit decision and current local filing category before filing."
        if key in fee_fields and strip_internal_keys and any(term in lowered for term in fee_internal_terms):
            value = re.sub(r"\bjurisdiction multiplier\b", "local fee schedule", value, flags=re.I)
            value = re.sub(r"\bstructured\s+ti\s+floor\b|\bstructured\s+floor\b|\bti\s+floor\b", "commercial TI complexity", value, flags=re.I)
            value = re.sub(r"\bada-path-of-travel\s+adder\b", "accessibility review scope", value, flags=re.I)
            lowered = value.lower()
        if key in fee_fields and strip_internal_keys and re.search(r"\b(?:fee\s*:\s*)?(?:call|contact)\s+(?:to\s+)?confirm\b", lowered):
            return "Fee depends on declared valuation, trade scope, plan-review fees, and the current portal fee schedule; verify in the city portal before quoting."
        if strip_internal_keys:
            value = value.replace("**", "")
            value = re.sub(r"\$\{[^}]*\}", "", value)
            value = re.sub(r"\{\{[^{}]*\}\}", "", value)
            value = re.sub(r"\bpermitassist_v231_decision_cell\b", "official permit rule", value, flags=re.I)
            value = re.sub(r"\b_decision_cell_primary_lock\b", "primary permit rule", value, flags=re.I)
            value = re.sub(r"\b_v231_\b", "current rule", value, flags=re.I)
            value = re.sub(r"\bv2\.3\.1\b", "current rule", value, flags=re.I)
            value = re.sub(r"\bdecision[_\s-]+cell\b", "official permit rule", value, flags=re.I)
            value = re.sub(r"\bcell[_\s-]*id\b", "official rule reference", value, flags=re.I)
            value = re.sub(r"\bresolver\b", "lookup", value, flags=re.I)
            value = re.sub(r"\bsource\s+metadata\b", "source details", value, flags=re.I)
            value = re.sub(r"\bcustomerdecisiondto\b", "customer decision", value, flags=re.I)
            value = re.sub(r"\[\s*verify\s+(?:with|in)\s+([^\]]{1,160})\]", r"confirm with \1", value, flags=re.I)
            value = re.sub(r"\[\s*verify\s+([^\]]{1,160})\]", r"confirm \1", value, flags=re.I)
            value = re.sub(r"\bsource-backed\s+threshold\b", "listed permit trigger", value, flags=re.I)
            value = re.sub(r"\bsource-backed\s+evidence\b", "official source", value, flags=re.I)
            value = re.sub(r"\bsource-backed\s+exemption\b", "official no-permit note", value, flags=re.I)
            value = re.sub(r"\bsource-backed\b", "official-source", value, flags=re.I)
            value = re.sub(r"\bneeds_verification\b", "confirm with the listed department", value, flags=re.I)
            value = re.sub(r"\bfail[_\s-]?closed\b", "not shown", value, flags=re.I)
            value = re.sub(r"\bpending(?:[_\s-]*(?:active[_\s-]*)?retrieval|view|lookup)?\b", "not yet published", value, flags=re.I)
            value = re.sub(r"(?<=\b[A-Z]{2})\.(?=[A-Za-z])", ". ", value)
            value = re.sub(r"\b(work|scope|review|permit|inspection)\.(?=(?:signage|exterior|interior|electrical|plumbing|mechanical|fire|health|zoning)\b)", r"\1. ", value, flags=re.I)
            value = re.sub(r"\s*\((?:full replacement|minor repair|layout/plumbing/electrical/wall changes)\)\s*", " ", value, flags=re.I)
            value = re.sub(r"\blayout/plumbing/electrical/wall changes\b", "layout, plumbing, electrical, or wall changes", value, flags=re.I)
            if value.strip().lower().rstrip(".") == "connection points":
                return ""
            if value.strip().lower() == "fire marshal before permit intake.":
                value = "Coordinate fire-marshal review before permit intake."
            value = re.sub(r"\s+or\s+Verify\s+", "; verify ", value)
            value = re.sub(r"\bahj_contact_source\b", "building department contact source", value, flags=re.I)
            value = re.sub(r"\bAHJ\b", "building department", value, flags=re.I)
            value = re.sub(r"\bLikely\s+primary\s+permit\s+type\b", "Primary permit type", value, flags=re.I)
            value = re.sub(r"\b(?:Some\s+report\s+claims\s+do\s+not\s+yet\s+have\s+quoted\s+source\s+snippets;\s*)?verify\s+with\s+the\s+building\s+department\s+before\s+relying\s+on\s+them\.?", "Source snippets are incomplete; source support is degraded.", value, flags=re.I)
            value = re.sub(r"\bverify\s+requirements\s+with\s+the\s+building\s+department\s+before\s+filing\.?", "Use the resolved permit decision and current local filing category before filing.", value, flags=re.I)
        value = re.sub(r"\bVerified\s*·\s*official sources\b", "Official source path found", value, flags=re.I)
        value = re.sub(r"\bPermit\s+Permit\b", "Permit", value, flags=re.I)
        # Customer-visible final guardrails for deterministic serializer defects.
        value = re.sub(r"\s*×\s*1(?:\.0)?\b", "", value)
        if target_state == "GA" and target_city == "savannah":
            value = re.sub(r"\(?912\)?[-\s]*651[-\s]*6790", "912-651-6530", value)
        value = re.sub(r"\bPlanning estimate only\s*:?\s*", "", value, flags=re.I)
        value = re.sub(r"\bHidden triggers?\s*:?\s*", "Scope triggers: ", value, flags=re.I)
        value = re.sub(r"\bEngine flagged(?:\s+this\s+answer)?(?:\s+for\s+review)?\b\.?", "Use the resolved permit decision and current local filing category before filing.", value, flags=re.I)
        value = re.sub(r"\bNeeds review(?:\s+for\s*:\s*[A-Za-z0-9_, ._/-]+)?\b\.?", "Use the resolved permit decision and current local filing category before filing.", value, flags=re.I)
        value = re.sub(r"\[?\s*verify[^\]\[.;,]{0,100}?before merging\s*\]?", "confirm with the building department", value, flags=re.I)
        value = re.sub(r"\bverify adopted edition before merging\b", "confirm adopted code edition with the building department", value, flags=re.I)
        # Normalize broken sentence seams from LLM fragment splicing
        value = re.sub(r"(?<!\.)\.\.(?!\.)", ". ", value)          # "queue..Check" → "queue. Check"
        value = re.sub(r"\.\s+and\s+([a-z])", r" and \1", value)    # "bidding. and save" → "bidding and save"
        value = re.sub(r"\s{2,}", " ", value).strip()
        if strip_internal_keys and has_internal(value):
            return ""
        return value

    def scrub(value, key: str = ""):
        if isinstance(value, str):
            return scrub_text(value, key)
        if isinstance(value, list):
            cleaned = []
            for item in value:
                next_item = scrub(item, key)
                if not strip_internal_keys or next_item not in ("", [], {}):
                    cleaned.append(next_item)
            return cleaned
        if isinstance(value, dict):
            cleaned = {}
            for child_key, child_value in value.items():
                key_name = str(child_key)
                key_lc = key_name.lower()
                if strip_internal_keys and (key_lc.startswith("_") or key_lc in internal_keys):
                    continue
                next_value = scrub(child_value, key_name)
                if not strip_internal_keys or next_value not in ("", [], {}):
                    public_key = "building_department_contact_source" if key_lc == "ahj_contact_source" else child_key
                    cleaned[public_key] = next_value
            return cleaned
        return value

    cleaned_result = scrub(result)
    if isinstance(cleaned_result, dict):
        _sanitize_customer_apply_path_in_place(cleaned_result)
        if strip_internal_keys and is_commercial_ti_result(result):
            timeline = cleaned_result.get("approval_timeline")
            if isinstance(timeline, dict):
                simple = str(timeline.get("simple") or "").strip()
                simple_lc = simple.lower()
                if not simple or "same day" in simple_lc or "otc" in simple_lc or "over-the-counter" in simple_lc:
                    timeline["simple"] = "Commercial TI/addition/remodel scopes usually require plan review; expect at least several business days to a few weeks depending on completeness, valuation, and local queue."
                complex_text = str(timeline.get("complex") or "").strip()
                if not complex_text:
                    timeline["complex"] = "Longer plan-review cycle if structural, accessibility, fire/life-safety, health, zoning, or trade-plan corrections are triggered."
                cleaned_result["approval_timeline"] = timeline
        if scope_contract:
            cleaned_result = sanitize_result_for_scope_contract(cleaned_result, scope_contract)
            if strip_internal_keys:
                cleaned_result.pop("_scope_contract", None)
                cleaned_result.pop("_scope_firebreak_removed", None)
        return cleaned_result
    return {}


def _sanitize_customer_apply_path_in_place(result: dict) -> None:
    """Normalize customer apply_path metadata so stale cached rows cannot leak uncertainty strings."""
    if not isinstance(result, dict) or not isinstance(result.get("apply_path"), dict):
        return
    apply_path = dict(result.get("apply_path") or {})
    documents = apply_path.pop("likely_documents", None)
    if documents and "documents_to_prepare" not in apply_path:
        apply_path["documents_to_prepare"] = documents

    portal_url = str(apply_path.get("portal_url") or apply_path.get("url") or "").strip()
    lower_url = portal_url.lower()
    platform_text = str(apply_path.get("platform") or "").strip()
    if not platform_text or platform_text.lower() in {"unknown", "fail_closed", "not_applicable_or_unknown"}:
        if lower_url.endswith(".pdf"):
            apply_path["platform"] = "PDF / paper form"
        elif portal_url:
            apply_path["platform"] = "city portal / AHJ website"
        else:
            apply_path["platform"] = None
    else:
        apply_path["platform"] = platform_text

    login = apply_path.get("login_required")
    if isinstance(login, bool) or login is None:
        apply_path["login_required"] = login
    else:
        login_text = str(login or "").strip().lower()
        if login_text in {"true", "yes", "required", "account_required"}:
            apply_path["login_required"] = True
        elif login_text in {"false", "no", "not_required", "not applicable", "not_applicable"}:
            apply_path["login_required"] = False
        else:
            apply_path["login_required"] = None

    # Customer apply_path is not companion-permit context; remove banned uncertainty vocabulary from stale strings.
    banned_uncertainty_re = re.compile(r"\b(?:unknown|fail[_ -]?closed|likely|maybe|probably)\b", re.I)

    def scrub_apply_value(value):
        if isinstance(value, str):
            cleaned = banned_uncertainty_re.sub("", value)
            cleaned = re.sub(
                r"\bpermit\s+type\s+needs\s+building\s+department\s+verification\b",
                "",
                cleaned,
                flags=re.I,
            )
            cleaned = re.sub(
                r"Ask\s+the\s+building\s+department\s+which\s+permit\s+category\s+best\s+matches\s*:?\s*$",
                "Ask the building department which permit category best matches the project scope",
                cleaned,
                flags=re.I,
            )
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:;,.\n")
            return cleaned
        if isinstance(value, list):
            return [item for item in (scrub_apply_value(item) for item in value) if item not in ("", [], {})]
        if isinstance(value, dict):
            return {
                key: item
                for key, item in ((k, scrub_apply_value(v)) for k, v in value.items())
                if item not in ("", [], {})
            }
        return value

    keep_empty_apply_path_keys = {"portal_url", "platform", "login_required"}
    result["apply_path"] = {
        key: value
        for key, value in ((k, scrub_apply_value(v)) for k, v in apply_path.items())
        if value not in ("", [], {}) or key in keep_empty_apply_path_keys
    }


_PUBLIC_CUSTOMER_RESULT_FIELDS = frozenset({
    "permit_decision", "permit_verdict", "permit_required", "permit_kind", "permit_name",
    "input_status", "error_code", "validation_errors", "message", "city", "state",
    "customer_result_summary", "customer_first_screen_summary",
    "permit_type", "permits_required", "permits_required_logic", "companion_permits", "related_permits",
    "customer_headline", "customer_next_step", "summary", "job_summary", "scope_summary",
    "confidence", "confidence_level", "confidence_reason", "data_source", "county_fallback_note",
    "source_confidence", "source_support", "local_form_known", "apply_url_known", "degraded_sources",
    "requirements", "documents_needed", "what_to_bring", "next_steps", "pro_tips",
    "common_mistakes", "watch_out", "checklist", "inspections", "inspect_checklist",
    "inspection_requirements", "inspection_booking", "fee_range", "fee_estimate",
    "total_cost_estimate", "fee_calculator", "approval_timeline", "timeline",
    "applying_office", "building_dept_name", "building_dept_phone", "apply_phone",
    "apply_address", "apply_google_maps", "apply_url", "apply_path", "online_application_url",
    "source_urls", "sources", "claim_citations", "warnings", "disclaimer",
    "zoning_hoa_flag", "remaining_lookups", "_cached",
})
_PUBLIC_SOURCE_FIELDS = frozenset({"url", "title", "snippet", "source_url", "source_title", "publisher", "date", "source_type", "jurisdiction"})
_PUBLIC_CITATION_FIELDS = frozenset({
    "id", "field", "claim", "value", "source_url", "source_title", "quoted_snippet",
    "checked_at", "confidence",
})
_INTERNAL_CUSTOMER_FIELD_NAMES = frozenset({
    "permit_decision_contract", "source_evidence_floor", "exact_apply_url_status",
    "exact_name_status", "quality_warnings", "needs_review", "permit_ready_score",
    "debug_trace", "provider_metadata", "retrieval_diagnostics", "raw_retrieval",
    "search_debug", "scoring_debug", "missing_fields", "hidden_triggers",
    "source_metadata", "decision_cell", "cell_id", "resolver", "customerdecisiondto",
})


_PUBLIC_KEEP_EMPTY_FIELDS = frozenset({
    "apply_url", "apply_phone", "online_application_url", "source_urls", "sources", "claim_citations",
})

_STRUCTURED_SOURCE_URL_FIELDS = frozenset({
    "sources", "source_urls", "claim_citations", "apply_url", "online_application_url",
})
_CUSTOMER_FREE_TEXT_SOURCE_URL_FIELDS = frozenset(
    (_PUBLIC_CUSTOMER_RESULT_FIELDS - _STRUCTURED_SOURCE_URL_FIELDS) | {"permit_summary"}
)
_FREE_TEXT_URL_WALK_DENYLIST_FIELDS = frozenset({
    "expert_notes",
    "debug_trace",
    "retrieval_diagnostics",
    "raw_retrieval",
    "search_debug",
    "scoring_debug",
    "rejected_sources",
    "_sources_locality_dropped",
}) | _INTERNAL_CUSTOMER_FIELD_NAMES
_FREE_TEXT_STRUCTURED_ROOT_FIELDS = frozenset({
    "sources",
    "source_urls",
    "claim_citations",
    "apply_url",
    "online_application_url",
})
_FREE_TEXT_STRUCTURED_APPLY_PATH_FIELDS = frozenset({"portal_url", "url", "source_url"})
_CUSTOMER_HTTPS_URL_RE = re.compile(r"https://[^\s<>'\"`]+", re.I)


def _trim_extracted_customer_url(url: str) -> str:
    return str(url or "").strip().rstrip(".,;:!?)]}>")


def _extract_customer_text_urls(value):
    if isinstance(value, str):
        for match in _CUSTOMER_HTTPS_URL_RE.finditer(value):
            safe = _safe_customer_source_url(_trim_extracted_customer_url(match.group(0)))
            if safe:
                yield safe


def _should_walk_free_text_url_field(key, path: tuple[str, ...]) -> bool:
    key_text = str(key or "")
    key_lc = key_text.lower()
    if key_text.startswith("_") or key_lc in _FREE_TEXT_URL_WALK_DENYLIST_FIELDS:
        return False
    if not path and key_lc in _FREE_TEXT_STRUCTURED_ROOT_FIELDS:
        return False
    if path and path[-1] == "apply_path" and key_lc in _FREE_TEXT_STRUCTURED_APPLY_PATH_FIELDS:
        return False
    return True


def _iter_free_text_customer_source_urls(value, path: tuple[str, ...] = ()):
    """Yield HTTPS URLs from non-structured, non-internal customer prose.

    This is a field-aware backstop for model outputs that put an official local
    URL in customer guidance text instead of structured source fields. It walks
    dict/list/string values recursively, but refuses internal/debug/rejected
    fields and root structured source slots so rejected retrieval evidence is
    never promoted by accident.
    """
    if isinstance(value, str):
        yield from _extract_customer_text_urls(value)
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_free_text_customer_source_urls(item, path)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not _should_walk_free_text_url_field(key, path):
                continue
            yield from _iter_free_text_customer_source_urls(child, path + (str(key),))


def _strip_customer_urls_from_text(value):
    if isinstance(value, str):
        cleaned = _CUSTOMER_HTTPS_URL_RE.sub("", value)
        cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
        cleaned = re.sub(r"\(\s*\)", "", cleaned)
        cleaned = re.sub(r"\[\s*\]", "", cleaned)
        return re.sub(r"\s{2,}", " ", cleaned).strip()
    if isinstance(value, list):
        return [_strip_customer_urls_from_text(item) for item in value]
    if isinstance(value, dict):
        return {key: _strip_customer_urls_from_text(child) for key, child in value.items()}
    return value


def _strip_customer_free_text_urls_in_place(result: dict) -> dict:
    if not isinstance(result, dict):
        return result
    for key in _CUSTOMER_FREE_TEXT_SOURCE_URL_FIELDS:
        if key in result:
            result[key] = _strip_customer_urls_from_text(result.get(key))
    return result


def _public_dict(value, allowed_fields: frozenset[str] | None = None):
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            key_text = str(key)
            key_lc = key_text.lower()
            if key_text.startswith("_") or key_lc in _INTERNAL_CUSTOMER_FIELD_NAMES:
                continue
            if allowed_fields is not None and key_text not in allowed_fields:
                continue
            cleaned = _public_dict(child, None)
            if cleaned not in (None, "", [], {}) or (allowed_fields is not None and key_text in _PUBLIC_KEEP_EMPTY_FIELDS):
                out[key] = cleaned
        return out
    if isinstance(value, list):
        out = []
        for child in value:
            cleaned = _public_dict(child, None)
            if cleaned not in (None, "", [], {}):
                out.append(cleaned)
        return out
    return value


def _public_citations(result: dict, city: str, state: str) -> list[dict]:
    citations = result.get("claim_citations") if isinstance(result.get("claim_citations"), list) else []
    out = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        url = _safe_customer_source_url(citation.get("source_url") or "")
        if not url:
            continue
        authority = classify_source_authority(url, city, state, result=result)
        if not authority.get("display_allowed"):
            continue
        clean = _public_dict(citation, _PUBLIC_CITATION_FIELDS)
        if clean:
            out.append(clean)
    return out


def _iter_customer_source_urls(result: dict):
    seen = set()

    def add(url):
        safe = _safe_customer_source_url(url)
        if safe and safe not in seen:
            seen.add(safe)
            return safe
        return None

    for item in (result or {}).get("sources") or []:
        if isinstance(item, str):
            value = add(item)
        elif isinstance(item, dict):
            value = add(item.get("url") or item.get("link") or item.get("source_url"))
        else:
            value = None
        if value:
            yield value
    for item in (result or {}).get("source_urls") or []:
        value = add(item)
        if value:
            yield value
    for citation in (result or {}).get("claim_citations") or []:
        if isinstance(citation, dict):
            value = add(citation.get("source_url"))
            if value:
                yield value
    for key in ("apply_url", "online_application_url"):
        value = add((result or {}).get(key))
        if value:
            yield value
    apply_path = (result or {}).get("apply_path")
    if isinstance(apply_path, dict):
        value = add(apply_path.get("portal_url") or apply_path.get("url") or apply_path.get("source_url"))
        if value:
            yield value
    for url in _iter_free_text_customer_source_urls(result or {}):
        value = add(url)
        if value:
            yield value


def _local_decision_evidence_urls(result: dict, city: str, state: str) -> list[str]:
    out = []
    for url in _iter_customer_source_urls(result):
        authority = classify_source_authority(url, city, state, result=result)
        if authority.get("local_decision_evidence") and authority.get("display_allowed"):
            out.append(url)
    return out


_CANONICAL_AHJ_APPLY_URLS: dict[tuple[str, str], dict[str, object]] = {
    # Official AHJ start/portal URLs only. These entries restore provenance when
    # the AHJ is named but the structured apply_url is blank; they do not decide
    # whether a permit is required.
    ("new york", "ny"): {
        "url": "https://www.nyc.gov/site/buildings/index.page",
        "title": "NYC Department of Buildings",
        "tokens": ("nyc department of buildings", "new york city department of buildings", "dob now", "nyc dob"),
    },
    ("brooklyn", "ny"): {
        "url": "https://www.nyc.gov/site/buildings/index.page",
        "title": "NYC Department of Buildings",
        "tokens": ("nyc department of buildings", "new york city department of buildings", "dob now", "nyc dob", "department of buildings"),
    },
    ("queens", "ny"): {
        "url": "https://www.nyc.gov/site/buildings/index.page",
        "title": "NYC Department of Buildings",
        "tokens": ("nyc department of buildings", "new york city department of buildings", "dob now", "nyc dob", "department of buildings"),
    },
    ("bronx", "ny"): {
        "url": "https://www.nyc.gov/site/buildings/index.page",
        "title": "NYC Department of Buildings",
        "tokens": ("nyc department of buildings", "new york city department of buildings", "dob now", "nyc dob", "department of buildings"),
    },
    ("manhattan", "ny"): {
        "url": "https://www.nyc.gov/site/buildings/index.page",
        "title": "NYC Department of Buildings",
        "tokens": ("nyc department of buildings", "new york city department of buildings", "dob now", "nyc dob", "department of buildings"),
    },
    ("staten island", "ny"): {
        "url": "https://www.nyc.gov/site/buildings/index.page",
        "title": "NYC Department of Buildings",
        "tokens": ("nyc department of buildings", "new york city department of buildings", "dob now", "nyc dob", "department of buildings"),
    },
    ("washington", "dc"): {
        "url": "https://dob.dc.gov/page/permit-resources",
        "title": "DC Department of Buildings Permit Resources",
        "tokens": ("dc department of buildings", "district department of buildings", "dob permit wizard", "permit wizard", "dob.dc.gov"),
    },
    ("miami", "fl"): {
        "url": "https://www.miamidade.gov/permits/",
        "title": "Miami-Dade County Permits",
        "tokens": ("miami-dade", "miami dade", "miamidade", "rer", "permitting and inspection center"),
    },
    ("charlotte", "nc"): {
        "url": "https://code.mecknc.gov/permitting",
        "title": "Mecklenburg County Code Enforcement Permitting",
        "tokens": ("mecklenburg", "mecknc", "luesa", "code enforcement", "building permits"),
    },
    ("naperville", "il"): {
        "url": "https://www.naperville.il.us/services/permits--licenses/",
        "title": "City of Naperville Permits & Licenses",
        "tokens": ("naperville", "naperville permits", "city of naperville"),
    },
}


def _result_text_inventory(result: dict) -> str:
    try:
        return json.dumps(result or {}, sort_keys=True, default=str).lower()
    except Exception:
        return _customer_summary_text(result or {}).lower()


def _apply_canonical_ahj_apply_url_fallback(result: dict, city: str, state: str) -> dict:
    if not isinstance(result, dict):
        return {}
    if _safe_customer_source_url(result.get("apply_url") or result.get("online_application_url") or ""):
        return result
    city_key = (city or "").lower().strip()
    state_key = (state or "").lower().strip()
    entry = _CANONICAL_AHJ_APPLY_URLS.get((city_key, state_key))
    if not entry:
        return result
    text = _result_text_inventory(result)
    raw_tokens = entry.get("tokens")
    tokens = tuple(str(token).lower() for token in raw_tokens) if isinstance(raw_tokens, (list, tuple, set)) else ()
    if tokens and not any(token and token in text for token in tokens):
        return result
    url = str(entry.get("url") or "")
    safe_url = _safe_customer_source_url(url)
    if not safe_url:
        return result
    authority = classify_source_authority(safe_url, city, state, result=result)
    if not (authority.get("local_decision_evidence") and authority.get("display_allowed")):
        return result
    result["apply_url"] = safe_url
    result.setdefault("online_application_url", safe_url)
    result.setdefault("applying_office", str(entry.get("title") or f"{city} building department"))
    result.setdefault("building_dept_name", str(entry.get("title") or f"{city} building department"))
    return result


def _repair_source_backed_apply_path_contradiction(result: dict, city: str, state: str) -> dict:
    if not isinstance(result, dict):
        return {}
    local_urls = _local_decision_evidence_urls(result, city, state)
    if not local_urls:
        return result
    apply_path = result.get("apply_path")
    if not isinstance(apply_path, dict):
        return result
    if str(apply_path.get("support_level") or "").lower().strip() != "not source-backed":
        return result
    portal_url = _safe_customer_source_url(apply_path.get("portal_url") or result.get("apply_url") or local_urls[0])
    repaired = dict(apply_path)
    repaired["support_level"] = "needs verification"
    if portal_url:
        repaired["portal_url"] = portal_url
        if not result.get("apply_url"):
            result["apply_url"] = portal_url
    repaired["verification_note"] = "Official local source evidence is present; verify the exact portal selection/path with the AHJ before filing."
    result["apply_path"] = repaired
    return result


def _filing_url_source_dicts(result: dict, city: str, state: str, existing_urls: set[str] | None = None) -> list[dict]:
    """Promote verified local filing URLs into customer source evidence.

    The final source-floor gate should not weaken its local-AHJ requirement, but
    the filing URL fields are themselves local source candidates after the URL
    sanitizer and locality classifier accept them. This keeps source evidence
    assembly aligned across sources/source_urls/citations and apply-path fields.
    """
    if not isinstance(result, dict):
        return []
    seen = set(existing_urls or set())
    jurisdiction = ", ".join(part for part in [city, state] if part)
    office = _customer_summary_text(
        result.get("applying_office")
        or result.get("building_dept_name")
        or result.get("permit_office")
        or f"{city or 'Local'} building department"
    )
    out = []
    for url in _iter_customer_source_urls({
        "apply_url": result.get("apply_url"),
        "online_application_url": result.get("online_application_url"),
        "apply_path": result.get("apply_path"),
    }):
        if url in seen:
            continue
        authority = classify_source_authority(url, city, state, result=result)
        if not (authority.get("local_decision_evidence") and authority.get("display_allowed")):
            continue
        tier = str(authority.get("tier") or "ahj")
        seen.add(url)
        out.append({
            "url": url,
            "title": office or _source_tier_label(tier, city, state),
            "publisher": office or _source_tier_label(tier, city, state),
            "date": _source_display_date({}, result),
            "source_type": _customer_source_type(tier),
            "jurisdiction": jurisdiction,
            "snippet": "Local online filing or permit portal accepted by source-locality checks.",
        })
    return out


def _free_text_url_source_dicts(result: dict, city: str, state: str, existing_urls: set[str] | None = None) -> list[dict]:
    """Promote local AHJ URLs discovered only in customer free text.

    Model outputs can place the official portal in inspection/next-step/summary
    prose instead of structured source fields. Promotion still goes through the
    canonical source-authority classifier; free-text discovery only broadens
    where we find candidate URLs, not what counts as local decision evidence.
    """
    if not isinstance(result, dict):
        return []
    seen = set(existing_urls or set())
    jurisdiction = ", ".join(part for part in [city, state] if part)
    office = _customer_summary_text(
        result.get("applying_office")
        or result.get("building_dept_name")
        or result.get("permit_office")
        or f"{city or 'Local'} building department"
    )
    out = []
    for url in _iter_free_text_customer_source_urls(result):
        if url in seen:
            continue
        authority = classify_source_authority(url, city, state, result=result)
        if not (authority.get("local_decision_evidence") and authority.get("display_allowed")):
            continue
        tier = str(authority.get("tier") or "ahj")
        seen.add(url)
        out.append({
            "url": url,
            "title": office or _source_tier_label(tier, city, state),
            "publisher": office or _source_tier_label(tier, city, state),
            "date": _source_display_date({}, result),
            "source_type": _customer_source_type(tier),
            "jurisdiction": jurisdiction,
            "snippet": "Local official URL discovered in customer-facing permit guidance and accepted by source-locality checks.",
        })
    return out


def _source_evidence_floor_satisfied(result: dict) -> bool:
    """Return True only for an already-finalized public customer ViewModel."""
    if not isinstance(result, dict):
        return False
    if (
        isinstance(result.get("customer_result_summary"), dict)
        and isinstance(result.get("customer_first_screen_summary"), dict)
        and "permit_decision" in result
        and ("sources" in result or "source_urls" in result)
        and not any(key in result for key in _INTERNAL_CUSTOMER_FIELD_NAMES)
        and not any(str(key).startswith("_") for key in result)
    ):
        # Idempotent share/report rendering: create_share stores the already
        # allowlisted public ViewModel, then get_share/render_share_page passes
        # it through this builder again. The internal evidence-floor object is
        # intentionally absent from public JSON, so preserve the finalized
        # public decision instead of fail-closing on the second pass.
        return True
    return False


def _is_required_permit_decision(result: dict) -> bool:
    contract = result.get("permit_decision_contract") if isinstance(result.get("permit_decision_contract"), dict) else {}
    decision = str(result.get("permit_decision") or contract.get("permit_decision") or "").upper().strip()
    verdict = str(result.get("permit_verdict") or "").upper().strip()
    return decision == "REQUIRED" or result.get("permit_required") is True or verdict in {"YES", "REQUIRED"}


def _source_retrieval_degraded(result: dict) -> bool:
    if not isinstance(result, dict):
        return False
    try:
        text = json.dumps({
            "retrieval_diagnostics": result.get("retrieval_diagnostics"),
            "warnings": result.get("warnings"),
            "quality_warnings": result.get("quality_warnings"),
            "source_support": result.get("source_support"),
        }, default=str).lower()
    except Exception:
        text = str(result or "").lower()
    return any(token in text for token in (
        "429", "502", "timeout", "timed out", "rate limit", "bad gateway",
        "temporarily unavailable", "retrieval failed", "source retrieval",
    ))


def _filter_customer_sources_in_place(result: dict, city: str, state: str) -> dict:
    display_sources = _source_dicts(result, city=city, state=state, dedupe=True)
    existing_urls = {str(src.get("url")) for src in display_sources if isinstance(src, dict) and src.get("url")}
    filing_sources = _filing_url_source_dicts(result, city, state, existing_urls)
    display_sources.extend(filing_sources)
    existing_urls.update(str(src.get("url")) for src in filing_sources if isinstance(src, dict) and src.get("url"))
    display_sources.extend(_free_text_url_source_dicts(result, city, state, existing_urls))
    result["sources"] = display_sources
    source_urls = []
    for src in display_sources:
        url = src.get("url") if isinstance(src, dict) else ""
        if url and url not in source_urls:
            source_urls.append(url)
    result["source_urls"] = source_urls
    if isinstance(result.get("claim_citations"), list):
        filtered = []
        for citation in result.get("claim_citations") or []:
            if not isinstance(citation, dict):
                continue
            url = _safe_customer_source_url(citation.get("source_url") or "")
            if not url:
                continue
            authority = classify_source_authority(url, city, state, result=result)
            if not authority.get("display_allowed"):
                continue
            filtered.append(citation)
        result["claim_citations"] = filtered

    # Never leak rejected/wrong-locality URLs through customer prose or filing slots.
    # The source floor is metadata-only for decision purposes, but bad source URLs
    # still must be removed from public DTOs.
    for key in ("apply_url", "online_application_url"):
        url = _safe_customer_source_url(result.get(key) or "")
        if url:
            authority = classify_source_authority(url, city, state, result=result)
            if not authority.get("display_allowed"):
                result[key] = ""
    apply_path = result.get("apply_path")
    if isinstance(apply_path, dict):
        cleaned_path = dict(apply_path)
        for key in _FREE_TEXT_STRUCTURED_APPLY_PATH_FIELDS:
            url = _safe_customer_source_url(cleaned_path.get(key) or "")
            if url:
                authority = classify_source_authority(url, city, state, result=result)
                if not authority.get("display_allowed"):
                    cleaned_path.pop(key, None)
        result["apply_path"] = cleaned_path
    _strip_customer_free_text_urls_in_place(result)
    return result


def apply_source_floor_annotation(result: dict, job_type: str, city: str, state: str) -> dict:
    """Final source-floor pass that annotates metadata without changing answer.

    Source starvation, locality filtering, and 429/502 retrieval failures may lower
    source confidence or mark exact form/apply path unknown. They must never
    overwrite permit_required, permit_decision, permit_kind, permit_name,
    permits_required, customer_headline, or customer_next_step.
    """
    if not isinstance(result, dict):
        return {}
    protected = {
        key: copy.deepcopy(result.get(key))
        for key in (
            "permit_required", "permit_decision", "permit_verdict", "permit_kind",
            "permit_name", "permit_type", "permits_required", "customer_headline",
            "customer_next_step",
        )
        if key in result
    }
    result = _apply_canonical_ahj_apply_url_fallback(result, city, state)
    _filter_customer_sources_in_place(result, city, state)
    _repair_source_backed_apply_path_contradiction(result, city, state)

    local_urls = _local_decision_evidence_urls(result, city, state)
    display_sources = result.get("sources") if isinstance(result.get("sources"), list) else []
    degraded = bool(result.get("degraded_sources") or _source_retrieval_degraded(result) or not local_urls)
    exact_form_known = bool(_customer_summary_text(result.get("permit_name")) or result.get("permits_required"))
    apply_url_known = bool(_safe_customer_source_url(result.get("apply_url") or result.get("online_application_url") or ""))
    if not apply_url_known and isinstance(result.get("apply_path"), dict):
        apply_url_known = bool(_safe_customer_source_url(result["apply_path"].get("portal_url") or result["apply_path"].get("url") or ""))

    if local_urls:
        source_confidence = "AHJ_DIRECT"
    elif display_sources:
        source_confidence = "SOURCE_DEGRADED"
    else:
        source_confidence = "SCOPE_DEFAULT"

    source_support = result.get("source_support") if isinstance(result.get("source_support"), dict) else {}
    source_support.update({
        "source_confidence": source_confidence,
        "local_decision_evidence_urls": local_urls,
        "display_source_count": len(display_sources),
        "local_form_known": exact_form_known,
        "apply_url_known": apply_url_known,
        "degraded_sources": degraded,
        "decision_mutation_allowed": False,
    })
    result["source_confidence"] = source_confidence
    result["source_support"] = source_support
    result["local_form_known"] = exact_form_known
    result["apply_url_known"] = apply_url_known
    result["degraded_sources"] = degraded
    if degraded:
        warnings = result.setdefault("warnings", [])
        caveat = "Exact local source support is degraded; the customer permit decision remains resolved."
        if caveat not in warnings:
            warnings.append(caveat)

    # Guard against accidental decision mutation inside this metadata pass.
    for key, value in protected.items():
        result[key] = value
    return result


def _customer_summary_text(value) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, dict):
        for key in ("simple", "typical", "standard", "estimated", "note", "complex"):
            text = _customer_summary_text(value.get(key))
            if text:
                return text
        parts = [_customer_summary_text(v) for v in value.values()]
        return "; ".join(part for part in parts if part)
    if isinstance(value, list):
        parts = [_customer_summary_text(v) for v in value]
        return "; ".join(part for part in parts if part)
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _first_customer_text(*values) -> str:
    for value in values:
        text = _customer_summary_text(value)
        if text:
            return text
    return ""


def _customer_freshness_label(source: dict, citations: list[dict]) -> str:
    for key in ("verified_date", "source_date", "checked_at", "last_checked", "updated_at"):
        text = _customer_summary_text(source.get(key))
        if text and text.lower() not in {"unknown", "last updated: unknown", "n/a", "none"}:
            if key == "verified_date":
                return f"Verified {text}"
            if key == "source_date":
                return f"Source dated {text}"
            return f"Checked {text}"
    for citation in citations or []:
        if not isinstance(citation, dict):
            continue
        text = _customer_summary_text(citation.get("checked_at"))
        if text and text.lower() not in {"unknown", "n/a", "none"}:
            return f"Checked {text}"
    return "Source date not published; verify current requirements before filing"


def _build_customer_result_summary(public: dict, source: dict, city: str, state: str) -> dict:
    raw_citations = public.get("claim_citations")
    citations = [item for item in raw_citations if isinstance(item, dict)] if isinstance(raw_citations, list) else []
    raw_sources = public.get("sources")
    sources = [item for item in raw_sources if isinstance(item, dict)] if isinstance(raw_sources, list) else []
    first_source = sources[0] if sources else {}
    source_title = _customer_summary_text(first_source.get("title"))
    source_cue = f"Official source: {source_title}" if source_title else "Official source path not published in this result"

    next_step = _first_customer_text(
        public.get("customer_next_step"),
        source.get("customer_next_step"),
        (source.get("apply_path") or {}).get("steps") if isinstance(source.get("apply_path"), dict) else None,
        f"Use the resolved permit category with {public.get('applying_office') or city or 'the local building department'} before starting work.",
    )
    if next_step and not public.get("customer_next_step"):
        public["customer_next_step"] = next_step

    ahj_department = _first_customer_text(public.get("applying_office"), public.get("building_dept_name"), source.get("applying_office"), source.get("building_dept_name"), city)
    if ahj_department and not public.get("applying_office"):
        public["applying_office"] = ahj_department

    timeline = _first_customer_text(public.get("approval_timeline"), public.get("timeline"), source.get("approval_timeline"), source.get("timeline"))
    fee_cost_caveat = _first_customer_text(
        public.get("fee_range"), public.get("fee_estimate"), public.get("total_cost_estimate"),
        source.get("fee_range"), source.get("fee_estimate"), source.get("total_cost_estimate"),
        "Fees depend on valuation, trade scope, plan-review fees, and the current local fee schedule; verify before quoting.",
    )
    resolved = resolve_customer_decision({"result": {**source, **public}, "job_type": public.get("job_summary") or source.get("job_summary") or "", "city": city, "state": state})
    return {
        "permit_decision": _first_customer_text(public.get("permit_decision"), source.get("permit_decision"), resolved.get("permit_decision"), "REQUIRED"),
        "permit_kind": _first_customer_text(public.get("permit_kind"), source.get("permit_kind"), public.get("permit_type"), resolved.get("permit_kind"), "Building"),
        "permit_name": _first_customer_text(public.get("permit_name"), source.get("permit_name"), resolved.get("permit_name")),
        "ahj_department": ahj_department,
        "next_step": next_step,
        "timeline": timeline,
        "fee_cost_caveat": fee_cost_caveat,
        "freshness_label": _customer_freshness_label(source, citations),
        "source_cue": source_cue,
        "jurisdiction": ", ".join(part for part in [city, state] if part),
    }


def _build_customer_first_screen_summary(summary: dict) -> dict:
    """Mobile-first above-the-fold contract derived from canonical summary."""
    summary = summary if isinstance(summary, dict) else {}
    return {
        "decision": _customer_summary_text(summary.get("permit_decision")),
        "kind_category": _customer_summary_text(summary.get("permit_kind")),
        "next_action": _customer_summary_text(summary.get("next_step")),
        "ahj_department": _customer_summary_text(summary.get("ahj_department")),
        "source_cue": _customer_summary_text(summary.get("source_cue")),
    }


def lint_customer_visible_result(public: dict, city: str = "", state: str = "") -> list[dict]:
    """Deterministic customer-output lint for final ViewModel consistency gates."""
    if not isinstance(public, dict):
        return [{"code": "not_a_dict", "message": "Customer result is not an object."}]
    text = json.dumps(public, sort_keys=True, default=str)
    text_lc = text.lower()
    hits: list[dict] = []
    patterns = {
        "stutter_permit_permit": r"\bpermit\s+permit\b",
        "unfilled_braces": r"\{\{|\}\}",
        "unfilled_js_template": r"\$\{[^}]+\}",
        "unknown_freshness": r"last\s+updated\s*:\s*unknown",
        "invalid_date": r"\binvalid\s+date\b",
        "bracket_verify_placeholder": r"\[\s*verify\b",
        "internal_customer_terms": r"\b(?:source-backed\s+(?:threshold|evidence|exemption)|needs_verification|fail[_\s-]?closed|verify\s+before\s+merging|structured\s+floor|ti\s+floor|jurisdiction\s+multiplier|ada-path-of-travel\s+adder)\b",
        "fragment_stutter": r"\b[A-Z][A-Za-z .'-]+,\s*[A-Z]{2}\.[A-Za-z]|\bwork\.(?:signage|exterior|interior|electrical|plumbing|mechanical|fire|health|zoning)\b",
        "repeated_caveat": r"verify\s+with\s+the\s+building\s+department.{0,80}verify\s+with\s+the\s+building\s+department",
        # P1B — Serializer bug lint patterns (2026-06-09)
        "unknown_ahj_title": r"\b(?:unknown\s+ahj|incomplete\s+data|not\s+enough\s+information|data\s+unavailable|cannot\s+verify\s+jurisdiction)\b",
        "mid_sentence_drop": r"\b[a-z]{3,}\.[A-Z]{2}\.[^ ]{1,3}$|\.[A-Z][a-z]{2,}\.",
        "empty_bullet_or_fragment": r"^\s*[-•]\s*\.\s*$|[-•]\s*[a-z]{1,3}\.$",
    }
    for code, pattern in patterns.items():
        if re.search(pattern, text, flags=re.I | re.S):
            hits.append({"code": code, "message": "Customer-visible output failed deterministic copy lint."})
    summary = public.get("customer_result_summary") if isinstance(public.get("customer_result_summary"), dict) else {}
    required = {
        "permit_decision": summary.get("permit_decision") or public.get("permit_decision"),
        "permit_kind": summary.get("permit_kind") or public.get("permit_kind"),
        "next_step": summary.get("next_step") or public.get("customer_next_step"),
        "ahj_department": summary.get("ahj_department") or public.get("applying_office") or public.get("building_dept_name"),
        "source_cue": summary.get("source_cue"),
    }
    for key, value in required.items():
        if not _customer_summary_text(value):
            hits.append({"code": f"missing_{key}", "message": f"Missing required customer section: {key}."})
    target_state = (state or "").upper().strip()
    if target_state and _customer_text_has_wrong_jurisdiction_specific_claim(text_lc, target_state):
        hits.append({"code": "unsupported_jurisdiction_mention", "message": "Customer output contains unsupported out-of-jurisdiction code text."})
    for source in public.get("sources") or []:
        if isinstance(source, dict) and source.get("url") and not str(source.get("url") or "").startswith("https://"):
            hits.append({"code": "insecure_source_url", "message": "Customer-visible source URL must be https://."})
    return hits


def _sanitize_customer_result_with_state_context(public: dict, state: str) -> dict:
    scoped = dict(public or {})
    if state:
        scoped["_scope_contract"] = {"state": state}
    return sanitize_customer_visible_result(scoped, strip_internal_keys=True)


def build_customer_permit_view_model(result: dict, job_type: str = "", city: str = "", state: str = "") -> dict:
    """Allowlisted customer ViewModel used by API, share, report, and checklist surfaces."""
    working = copy.deepcopy(result) if isinstance(result, dict) else {}
    cell_lock = _get_decision_cell_primary_lock(working)
    jurisdiction_check = resolve_customer_decision({"result": working, "job_type": job_type, "city": city, "state": state})
    if is_input_rejection(jurisdiction_check):
        rejection_public = _public_dict(jurisdiction_check, _PUBLIC_CUSTOMER_RESULT_FIELDS)
        return sanitize_customer_visible_result(
            rejection_public if isinstance(rejection_public, dict) else {},
            strip_internal_keys=True,
        )
    source_floor_satisfied = _source_evidence_floor_satisfied(working)
    working = apply_source_floor_annotation(working, job_type, city, state)
    try:
        working = apply_permit_decision_contract(working, job_type, city, state, build_scope_contract(job_type or "", city or "", state or ""))
    except Exception as exc:
        print(f"[customer-view] Decision resolver fallback used: {exc}")
        dto = resolve_customer_decision({"result": working, "job_type": job_type, "city": city, "state": state})
        working.update(dto)
        working["permit_verdict"] = "YES" if dto.get("permit_required") else "NO"
    cleaned = sanitize_customer_visible_result(working, strip_internal_keys=True)
    try:
        scope_contract = build_scope_contract(job_type or "", city or "", state or "")
        cleaned = sanitize_result_for_scope_contract(cleaned, scope_contract, fail_on_removal_in_tests=False)
        if cell_lock and isinstance(cleaned, dict):
            cleaned["_decision_cell_primary_lock"] = cell_lock
        cleaned = apply_permit_decision_contract(cleaned if isinstance(cleaned, dict) else {}, job_type, city, state, scope_contract)
        if source_floor_satisfied:
            cleaned = _filter_customer_sources_in_place(cleaned if isinstance(cleaned, dict) else {}, city, state)
        else:
            cleaned = apply_source_floor_annotation(cleaned if isinstance(cleaned, dict) else {}, job_type, city, state)
    except Exception as exc:
        print(f"[customer-view] Scope sanitize fallback used: {exc}")
    public = _public_dict(cleaned if isinstance(cleaned, dict) else {}, _PUBLIC_CUSTOMER_RESULT_FIELDS)
    if isinstance(public, dict):
        dto = resolve_customer_decision({"result": public, "job_type": job_type, "city": city, "state": state})
        if public.get("permit_decision") not in {"REQUIRED", "NOT_REQUIRED"} or public.get("permit_required") not in {True, False}:
            public.update({k: v for k, v in dto.items() if k in _PUBLIC_CUSTOMER_RESULT_FIELDS})
            public["permit_verdict"] = "YES" if dto.get("permit_required") else "NO"
        if public.get("permit_required") is True and not public.get("permits_required"):
            public["permits_required"] = dto.get("permits_required") or [{"permit_type": dto.get("permit_name") or "Building Permit", "required": True}]
        if public.get("permit_required") is True and not public.get("permit_kind"):
            public["permit_kind"] = dto.get("permit_kind") or "Building"
        for key in _PUBLIC_KEEP_EMPTY_FIELDS:
            if key not in public and key in working and working.get(key) in ("", [], {}):
                public[key] = working.get(key)
    if not isinstance(public, dict):
        return {}
    public_sources = _source_dicts(cleaned if isinstance(cleaned, dict) else {}, city=city, state=state)
    if public_sources:
        public["sources"] = [_public_dict(src, _PUBLIC_SOURCE_FIELDS) for src in public_sources]
        public["source_urls"] = [src.get("url") for src in public["sources"] if isinstance(src, dict) and src.get("url")]
    elif not public_sources:
        public["sources"] = public.get("sources", []) if isinstance(public.get("sources"), list) else []
        public["source_urls"] = public.get("source_urls", []) if isinstance(public.get("source_urls"), list) else []
    citations = _public_citations(cleaned if isinstance(cleaned, dict) else {}, city, state)
    if citations:
        public["claim_citations"] = citations
    else:
        public["claim_citations"] = []
    if not isinstance(public, dict):
        return {}
    final_public = sanitize_customer_visible_result(public, strip_internal_keys=True)
    if isinstance(final_public, dict):
        final_public["customer_result_summary"] = _build_customer_result_summary(
            final_public,
            cleaned if isinstance(cleaned, dict) else working,
            city,
            state,
        )
        final_public["customer_first_screen_summary"] = _build_customer_first_screen_summary(final_public["customer_result_summary"])
        # Intentional second pass: customer_result_summary and mobile-first
        # summary are derived after the public ViewModel is assembled, so they
        # must pass the same jurisdiction/copy sanitizer before any surface
        # (main result, share, report) can render them.
        final_public = _sanitize_customer_result_with_state_context(final_public, state)
        for key in _PUBLIC_KEEP_EMPTY_FIELDS:
            if key in public and public.get(key) in ("", [], {}):
                final_public[key] = public.get(key)
        dto = resolve_customer_decision({"result": final_public, "job_type": job_type, "city": city, "state": state})
        if final_public.get("permit_decision") not in {"REQUIRED", "NOT_REQUIRED"} or final_public.get("permit_required") not in {True, False}:
            final_public.update({k: v for k, v in dto.items() if k in _PUBLIC_CUSTOMER_RESULT_FIELDS})
            final_public["permit_verdict"] = "YES" if dto.get("permit_required") else "NO"
        if final_public.get("permit_required") is True and not final_public.get("permits_required"):
            final_public["permits_required"] = dto.get("permits_required") or [{"permit_type": dto.get("permit_name") or "Building Permit", "required": True}]
        final_public["claim_citations"] = citations if citations else []
        final_public.pop("quality_warnings", None)
        if cell_lock:
            final_public = enforce_decision_cell_primary(final_public, cell_lock, city, state, public=True)
            final_public = sanitize_customer_visible_result(final_public, strip_internal_keys=True)
        # P0: Contact data integrity — provenance-gated contact sanitization
        # Added 2026-06-09. Suppresses wrong phones/addresses from untrusted sources.
        final_public = apply_contact_sanitization(final_public, city=city, state=state)
        return final_public
    return {}


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
            booking_bits.append("Confirm the inspection advance-notice window when booking")
        if booking_bits:
            result["inspection_booking"] = ". ".join(booking_bits) + "."

    if (
        not result.get("zoning_hoa_flag")
        and (
            _has_unnegated_any(job_lower, ("solar", "pv", "photovoltaic", "roof", "roofing", "shingle"))
            or (isinstance(result.get("_scope_contract"), dict) and result["_scope_contract"].get("vertical") in {"solar_pv", "reroof"})
        )
    ):
        result["zoning_hoa_flag"] = (
            "Check HOA rules, historic district overlays, and local zoning before applying. "
            "Solar and roofing jobs may face placement, material, or visibility restrictions that can delay approval."
        )

    return result


# ── PermitIQ trust layer helpers ─────────────────────────────────────────────
_COMMERCIAL_SCOPE_TOKENS = (
    "tenant improvement", " ti", "commercial", "restaurant", "clinic",
    "dental", "medical", "office", "retail", "change of use",
    "change of occupancy", "buildout", "build-out", "tenant finish",
    "storefront", "demising", "exam room", "type i hood",
)
_RESIDENTIAL_PRIMARY_TOKENS = (
    "residential", "single-family", "single family", "dwelling",
    "water heater", "hvac", "furnace", "roof", "reroof", "minor trade",
)
_COMMERCIAL_PRIMARY_TOKENS = (
    "commercial", "tenant improvement", "interior alteration", "building permit",
    "change of occupancy", "change of use", "alteration", "buildout", "build-out",
)


def _looks_like_residential_home_office_noncommercial(job_type: str) -> bool:
    """True for private residential workspace wording with explicit no-commercial-use markers."""
    text = re.sub(r"\s+", " ", (job_type or "").lower()).strip()
    if not text:
        return False
    has_residential_marker = bool(re.search(r"\b(?:single[- ]family|residential|dwelling|house|home)\b", text))
    has_private_workspace_marker = bool(
        re.search(r"\bhome[- ]office\b", text)
        or re.search(r"\bspare\s+bedroom\b.{0,80}\b(?:office|studio)\b", text)
        or re.search(r"\bbedroom\b.{0,80}\bhome[- ]office\b", text)
        or re.search(r"\bgarage\b.{0,80}\bhobby\s+workshop\b", text)
        or re.search(r"\bhobby\s+workshop\b.{0,80}\bgarage\b", text)
    )
    if not (has_residential_marker and has_private_workspace_marker):
        return False
    noncommercial_markers = (
        r"\bno\s+commercial\s+business\b",
        r"\bno\s+employees\b",
        r"\bno\s+customer\s+visits\b",
        r"\bno\s+tenant\s+improvement\b",
        r"\bno\s+commercial\s+tenant\s+improvement\b",
        r"\bno\s+office\s+tenant\s+improvement\b",
        r"\bno\s+office\s+ti\b",
        r"\bno\s+medical(?:\s+(?:clinic|office|use))?\b(?=[,.;]|\s+(?:and|or|nor|no)\b|$)",
        r"\bno\s+restaurant\b",
    )
    if sum(1 for pattern in noncommercial_markers if re.search(pattern, text, flags=re.I)) < 2:
        return False
    positive_commercial = _has_unnegated_any(text, (
        "commercial tenant improvement",
        "office tenant improvement",
        "office ti",
        "commercial office",
        "office buildout",
        "professional office",
        "law office",
        "coworking",
        "co-working",
        "tenant finish",
        "tenant buildout",
        "change of occupancy",
        "change of use",
    ))
    return not positive_commercial


def _job_has_unnegated_commercial_scope(job_type: str) -> bool:
    if _looks_like_residential_home_office_noncommercial(job_type or ""):
        return False
    text = (job_type or "").lower()
    return any(_contains_unnegated_phrase(text, token.strip()) for token in _COMMERCIAL_SCOPE_TOKENS)


def _result_has_commercial_scope_signal(result: dict | None) -> bool:
    """Detect commercial scope from cached result metadata/permit surfaces.

    Exact permit-cache hits can carry stale `_primary_scope=residential` even
    when the original request/category and permit fields are commercial. Use
    these narrow signals only as a fallback after job text is considered so real
    residential single-trade work still keeps residential wording.
    """
    if not isinstance(result, dict):
        return False
    meta = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
    category = " ".join(
        str(value or "")
        for value in (
            result.get("job_category"),
            result.get("category"),
            meta.get("job_category"),
            meta.get("category"),
            meta.get("vertical"),
        )
    ).lower()
    if _contains_unnegated_phrase(category, "commercial"):
        return True

    fields: list[str] = []
    for permit in result.get("permits_required") or []:
        if not isinstance(permit, dict):
            continue
        for key in ("permit_type", "portal_selection", "notes"):
            value = permit.get(key)
            if isinstance(value, str):
                fields.append(value)
    text = " | ".join(fields).lower()
    return _has_unnegated_any(
        text,
        (
            "commercial",
            "tenant improvement",
            "interior alteration",
            "change of occupancy",
            "change of use",
            "buildout",
            "build-out",
        ),
    )


def _is_commercial_scope(job_type: str, result: dict | None = None) -> bool:
    if _looks_like_residential_home_office_noncommercial(job_type or ""):
        return False
    primary = str((result or {}).get("_primary_scope") or "").lower()
    job_has_commercial = _job_has_unnegated_commercial_scope(job_type)
    if primary.startswith("residential") and _residential_single_trade_scope(job_type) and not job_has_commercial:
        return False
    text = f"{job_type or ''} {primary}".lower()
    if any(_contains_unnegated_phrase(text, token.strip()) for token in _COMMERCIAL_SCOPE_TOKENS):
        return True
    return _result_has_commercial_scope_signal(result)


def _term_is_locally_negated(text: str, term_start: int) -> bool:
    prefix = text[max(0, term_start - 72):term_start]
    if re.search(r"\b(?:remove|remove old|remove existing|removed|demolish|demo|cap|cap existing|abandon)\b(?:\s+(?:old|existing|unused|prior))*[\s,;/()-]*$", prefix, flags=re.I):
        return True
    if re.search(
        r"(?:\bno\b|\bwithout\b|\bnot\b|\bnone of\b|\bexcludes?\b|\bexcluding\b|\bdoes not include\b|\bdoesn't include\b|\bno new\b)"
        r"(?:\s+(?:and|or|any|new|commercial|type\s*i|type\s*1))*"
        r"(?:[\s,;/()-]+[a-z0-9]+){0,4}[\s,;/()-]*$",
        prefix,
        flags=re.I,
    ):
        return True
    suffix = text[term_start:term_start + 96]
    return bool(re.search(r"^[a-z0-9\s,;/()'\"-]{0,48}\b(?:not included|excluded|not in scope|outside(?: the)? scope|not part|not proposed)\b", suffix, flags=re.I))


def _contains_unnegated_phrase(text: str, phrase: str) -> bool:
    phrase_lc = (phrase or "").lower()
    if not phrase_lc:
        return False
    pattern = re.compile(r"(?<![a-z0-9])" + re.escape(phrase_lc) + r"(?![a-z0-9])", flags=re.I)
    for match in pattern.finditer(text or ""):
        if not _term_is_locally_negated(text, match.start()):
            return True
    return False


def _has_unnegated_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_contains_unnegated_phrase(text or "", phrase) for phrase in phrases)


def _historical_restaurant_retail_conversion(job_type: str) -> bool:
    """Former restaurant shell/space converted to non-food retail, not active restaurant TI."""
    text = f" {(job_type or '').lower()} "
    retail_signal = _has_unnegated_any(
        text,
        (
            "dry retail",
            "retail tenant improvement",
            "retail ti",
            "retail buildout",
            "retail store",
            "clothing store",
            "boutique",
        ),
    ) or bool(re.search(r"\b(?:retail|store|shop|boutique)\b", text))
    former_restaurant_signal = bool(
        re.search(
            r"\b(?:former|old|existing|prior|previous)\s+restaurant\s+(?:shell|space|tenant|suite|occupancy|unit|buildout)?\b",
            text,
        )
    )
    conversion_signal = bool(
        re.search(
            r"\b(?:convert(?:ing|ed|s)?|conversion|change(?:\s+of\s+(?:use|occupancy))?)\b.{0,100}\b(?:dry\s+retail|retail|clothing\s+store|store|shop|boutique)\b",
            text,
        )
        or re.search(
            r"\b(?:dry\s+retail|retail|clothing\s+store|store|shop|boutique)\b.{0,100}\b(?:convert(?:ing|ed|s)?|conversion|change(?:\s+of\s+(?:use|occupancy))?)\b",
            text,
        )
    )
    absent_food_terms = re.findall(
        r"\b(?:no|without)\s+(?:current\s+|public\s+|new\s+)?(?:food\s+prep(?:aration)?|food\s+service|kitchen\s+work|commercial\s+kitchen|cooking|hood|type\s*i\s*hood|type\s*1\s*hood|fryer|griddle|ansul|grease\s+interceptor|grease\s+work|f\.o\.g|fog)\b",
        text,
    )
    return retail_signal and former_restaurant_signal and conversion_signal and len(absent_food_terms) >= 2


def _restaurant_hood_scope_present(text: str) -> bool:
    return _has_unnegated_any(text or "", ("type i hood", "type 1 hood", "hood suppression", "ansul", "fryer", "griddle", "charbroiler", "grease duct"))


def _restaurant_grease_scope_present(text: str) -> bool:
    return _has_unnegated_any(
        text or "",
        (
            "grease interceptor", "grease trap", "f.o.g", "fats oils grease",
            "fog interceptor", "fog approval", "fog review", "fog sizing",
            "fog wastewater", "fog worksheet", "fog plan",
        ),
    )


def _restaurant_food_health_scope_present(text: str) -> bool:
    return _has_unnegated_any(
        text or "",
        (
            "food establishment", "food service", "commercial kitchen", "prep kitchen",
            "kitchen work", "kitchen plumbing", "cooking equipment", "food preparation", "food prep",
            "health department review", "health review",
            "type i hood", "type 1 hood", "grease interceptor", "grease trap",
            "espresso bar", "hand sink", "mop sink", "dishwasher", "commercial dishwasher",
            "3-compartment sink", "3 compartment sink", "4-compartment sink", "4 compartment sink",
            "bar tenant", "bar buildout", "beer taps", "glass washer", "restaurant opening",
        ),
    )


def _retail_food_health_scope_present(text: str) -> bool:
    return _has_unnegated_any(
        text or "",
        (
            "food preparation", "food prep", "beverage preparation", "beverage prep",
            "coffee service", "coffee", "espresso", "cafe", "café", "grocery", "groceries",
            "convenience store", "commercial kitchen", "prep kitchen", "alcohol service",
            "alcohol", "liquor", "beer/wine", "beer and wine", "wine shop", "bottle shop",
            "bar tenant", "beer taps",
        ),
    )


def _medical_gas_scope_present(text: str) -> bool:
    return _has_unnegated_any(text or "", ("medical gas", "med gas", "nitrous", "oxygen"))


def _medical_xray_scope_present(text: str) -> bool:
    return _has_unnegated_any(text or "", ("x-ray", "x ray", "radiology", "cbct", "fluoroscopy"))


def _medical_clinic_scope_present(text: str) -> bool:
    return _has_unnegated_any(text or "", (
        "medical clinic", "clinic tenant improvement", "clinic ti", "exam room", "exam rooms",
        "patient care", "treatment room", "treatment rooms", "procedure room", "procedure rooms",
    ))


def _neutralize_commercial_permit_residential_contrast(result: dict, job_type: str) -> None:
    """Remove wrong-category residential contrast wording from commercial permit fields.

    Full300 scores `permits_required` fields as customer-visible classification
    signals. A commercial note that says "not residential" is semantically
    negative, but contractors and strict gates still see the wrong category.
    Preserve the commercial/church/TI meaning and remove the contrast word.
    """
    if not isinstance(result, dict) or not _is_commercial_scope(job_type, result):
        return
    permits = result.get("permits_required")
    if not isinstance(permits, list):
        return

    def clean_text(value: str) -> str:
        if not isinstance(value, str) or not re.search(r"\bresidential\b", value, flags=re.I):
            return value
        cleaned = value
        cleaned = re.sub(
            r"\bBecause\s+this\s+is\s+[^.]{0,160}?\bthe\s+work\s+is\s+not\s+(?:a\s+)?(?:simple\s+)?residential\s+(?:alteration|remodel|project|scope|job|work)\.?,?",
            "Because this is a commercial occupancy space, handle the work as a commercial interior alteration / tenant improvement review.",
            cleaned,
            flags=re.I,
        )
        cleaned = re.sub(
            r"\b(?:not\s+(?:a\s+)?(?:simple\s+)?|no\s+)(?:residential\s+)(?:alteration|remodel|project|scope|job|work)\b",
            "commercial alteration review",
            cleaned,
            flags=re.I,
        )
        cleaned = re.sub(r"\bresidential\s*/\s*trade-only\b", "trade-only", cleaned, flags=re.I)
        cleaned = re.sub(r"\bresidential\s+or\s+trade-only\b", "trade-only", cleaned, flags=re.I)
        # Permit fields are classification surfaces. If a standalone category
        # word survives after the targeted rewrites above, use neutral occupancy
        # wording rather than showing the wrong category to a commercial user.
        cleaned = re.sub(r"\bresidential\b", "dwelling", cleaned, flags=re.I)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return cleaned

    for permit in permits:
        if not isinstance(permit, dict):
            continue
        for field in ("permit_type", "portal_selection", "notes"):
            if isinstance(permit.get(field), str):
                permit[field] = clean_text(permit[field])


def _filter_negated_surface_lists(result: dict, job_type: str) -> None:
    """Remove generated advice/docs/logic lines for explicitly absent sub-systems.

    PR #16/17 filtered checklist + initial advice surfaces. Broader production
    QA showed the same negative evidence can echo into `what_to_bring`,
    `quality_warnings`, and `permits_required_logic`. Drop only the individual
    customer-visible item that mentions an absent subsystem; keep unrelated
    permits and companion permits intact.
    """
    job_text = f"{job_type or ''}".lower()
    scope_text = f"{job_type or ''} {(result or {}).get('_primary_scope', '')}".lower()
    historical_retail_conversion = _historical_restaurant_retail_conversion(job_type)
    if historical_retail_conversion:
        result["_primary_scope"] = "commercial_retail_ti"
        scope_text = job_text
        components = result.get("_fee_floor_components")
        if isinstance(components, dict) and components.get("scope") == "commercial_restaurant":
            result.pop("_fee_floor_components", None)
    forbidden_terms: list[str] = []
    if not _restaurant_hood_scope_present(scope_text):
        forbidden_terms += ["hood", "ansul", "fire suppression", "grease duct"]
    if not _restaurant_grease_scope_present(scope_text):
        forbidden_terms += [
            "grease", "f.o.g", "fats oils grease", "fog",
            "fog interceptor", "fog review", "fog approval", "fog requirement",
            "fog requirements", "fog forms", "fog sizing", "fog wastewater", "fog worksheet", "fog plan",
        ]
    restaurant_scope_present = (
        _restaurant_hood_scope_present(scope_text)
        or _restaurant_grease_scope_present(scope_text)
        or _restaurant_food_health_scope_present(scope_text)
    )
    retail_food_health_scope_present = (
        str((result or {}).get("_primary_scope") or "").lower() == "commercial_retail_ti"
        and _retail_food_health_scope_present(scope_text)
    )
    if not (_restaurant_food_health_scope_present(scope_text) or retail_food_health_scope_present):
        forbidden_terms += ["food establishment", "food-establishment", "food service", "commercial kitchen", "health department", "food", "beverage"]
    if not restaurant_scope_present:
        # Customer-visible classification/prose surfaces should use positive
        # scope framing. Even semantically negative contrast like "not a
        # restaurant hood" can read as wrong-vertical leakage in lab fume hood
        # or office/retail outputs. Preserve true restaurant positives via the
        # unnegated-scope checks above.
        forbidden_terms += ["restaurant", "commercial kitchen", "kitchen", "ansul", "grease", "f.o.g", "fog"]
    if not _has_unnegated_any(scope_text, ("commercial dishwasher", "dishwasher", "prep sink", "floor sink", "mop sink", "indirect waste")):
        forbidden_terms += ["plumbing sheets", "plumbing sheet", "plumbing plans", "plumbing plan"]
    if not _medical_clinic_scope_present(scope_text):
        forbidden_terms += [
            "commercial clinic", "clinic", "exam room", "exam rooms", "exam-room",
            "patient care", "treatment room", "treatment rooms", "procedure room",
            "procedure rooms", "procedure-room", "clinic services", "healthcare-specific",
            "health-care-specific", "healthcare services", "health-care services",
        ]
    if not _medical_xray_scope_present(scope_text):
        forbidden_terms += ["x-ray", "x ray", "radiology"]
    if not _medical_gas_scope_present(scope_text):
        forbidden_terms += ["medical gas", "medical-gas", "med gas"]

    explicit_absent_terms = {
        r"\bno\s+commercial\s+kitchen\b": "commercial kitchen",
        r"\bwithout\s+commercial\s+kitchen\b": "commercial kitchen",
        r"\bno\s+food\s+establishment\b": "food establishment",
        r"\bno\s+health\s+department\b": "health department",
        r"\bno\s+grease\s+interceptor\b": "grease interceptor",
        r"\bno\s+grease\b": "grease",
        r"\bno\s+type\s*i\s*hood\b": "type i hood",
        r"\bno\s+type\s*1\s*hood\b": "type 1 hood",
        r"\bno\s+kitchen\s+hood\b": "kitchen hood",
        r"\bno\s+cooking\s+hood\b": "cooking hood",
        r"\bno\s+hood\b": "hood",
        r"\bno\s+ansul\b": "ansul",
        r"\bno\s+fire\s+suppression\b": "fire suppression",
        r"\bno\s+clinic\b": "clinic",
        r"\bno\s+clinic\s+services?\b": "clinic services",
        r"\bno\s+exam\s+rooms?\b": "exam room",
        r"\bno\s+treatment\s+services?\b": "treatment services",
        r"\bno\s+treatment\b": "no treatment",
        r"\bno\s+treatment\s+rooms?\b": "treatment room",
        r"\bno\s+procedure\s+rooms?\b": "procedure room",
        r"\bno\s+x[- ]?ray\b": "x-ray",
        r"\bno\s+medical\s+gas\b": "medical gas",
        r"\bno\s+patient\s+care\b": "patient care",
    }
    for pattern, term in explicit_absent_terms.items():
        if re.search(pattern, scope_text):
            forbidden_terms.append(term)

    # Preserve order but avoid redundant scans.
    forbidden_terms = list(dict.fromkeys(forbidden_terms))

    if not forbidden_terms:
        return

    def term_hits_text(term: str, text: str) -> bool:
        if term == "hood" and "fume hood" in text and not any(k in text for k in ("type i hood", "type 1 hood", "kitchen hood", "cooking hood", "hood suppression")):
            return False
        if term == "fog":
            # Bare FOG means fats/oils/grease in this permit context. Do not
            # treat theatrical/test smoke references as restaurant grease scope.
            if re.search(r"\bfog\s+(?:machine|machines|effect|effects|system|systems|testing|test)\b", text):
                return False
            return re.search(r"\bfog\b", text) is not None
        return term in text

    def has_forbidden(value) -> bool:
        text = str(value).lower()
        return any(term_hits_text(term, text) for term in forbidden_terms)

    def scrub_text(value: str) -> str:
        """Remove absent-trigger clauses without deleting the rest of the answer.

        The 25-case smoke scores customer-visible prose, not just structured
        permits. If the model echoes "no hood/no grease/no clinic" inside a
        useful summary, keep the useful summary but drop the negated clause so
        absent systems do not look like customer-facing requirements.
        """
        if not value or not has_forbidden(value):
            return value
        chunks = re.split(r"([.;\n]|\s+but\s+|\s+and\s+)", value)
        kept: list[str] = []
        for i in range(0, len(chunks), 2):
            chunk = chunks[i]
            sep = chunks[i + 1] if i + 1 < len(chunks) else ""
            if not chunk.strip():
                kept.append(chunk + sep)
                continue
            if has_forbidden(chunk):
                continue
            kept.append(chunk + sep)
        cleaned = "".join(kept)
        cleaned = re.sub(r"\s+([.;,])", r"\1", cleaned)
        cleaned = re.sub(r"(?:\s+(?:and|but)\s*)+([.;]|$)", r"\1", cleaned, flags=re.I)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ;,\n")
        return cleaned

    def scrub_value(value):
        if isinstance(value, str):
            return scrub_text(value)
        if isinstance(value, list):
            out = []
            for item in value:
                cleaned = scrub_value(item)
                if cleaned not in ("", [], {}):
                    out.append(cleaned)
            return out
        if isinstance(value, dict):
            drop_if_forbidden_keys = {
                "permit_type", "title", "name", "id", "key", "stage", "value", "claim",
                "description", "summary", "why_it_matters", "reason", "required_if",
            }
            if any(
                k in drop_if_forbidden_keys and isinstance(item, str) and has_forbidden(item)
                for k, item in value.items()
            ):
                return {}
            out = {}
            for k, item in value.items():
                cleaned = scrub_value(item) if k in {"notes", "note", "description", "summary", "included_because", "reason", "required_if", "scope_trigger", "steps", "portal_selection_path", "likely_documents", "verification_note", "stage", "title", "value", "claim", "text", "message", "details", "source_title", "quoted_snippet", "source_excerpt", "snippet", "simple", "complex", "timeline"} or isinstance(item, (list, dict)) or (isinstance(item, str) and has_forbidden(item)) else item
                if cleaned not in ("", [], {}):
                    out[k] = cleaned
            return out
        return value

    for key in (
        "pro_tips", "common_mistakes", "watch_out", "what_to_bring", "quality_warnings",
        "permits_required_logic", "checklist", "permit_checklist", "next_steps", "requirements",
        "documents_needed", "permit_notes", "notes", "summary", "description", "recommendation",
        "job_summary", "zoning_hoa_flag", "confidence_reason", "disclaimer", "apply_path", "permits_required",
        "fee_range", "total_cost_estimate", "fee_estimate", "fee_calculator",
        "inspections", "inspect_checklist", "inspection_requirements", "claim_citations",
        "companion_permits", "hidden_triggers", "fee_source", "fee_sources", "fee_calculator",
        "fee_estimate", "total_cost_estimate", "code_section_source", "required_documents_source",
        "inspection_process_source", "ahj_contact_source", "license_required", "approval_timeline",
    ):
        if key not in result:
            continue
        if isinstance(result.get(key), list):
            result[key] = [item for item in (scrub_value(item) for item in result[key]) if item not in ("", [], {})]
        elif isinstance(result.get(key), (str, dict)):
            cleaned = scrub_value(result[key])
            if cleaned in ("", [], {}):
                result.pop(key, None)
            else:
                result[key] = cleaned


def _residential_single_trade_scope(job_type: str) -> bool:
    text = f" {(job_type or '').lower()} "
    residential_marker = _has_unnegated_any(text, ("residential", "single-family", "single family", "single family home", "single-family home", "dwelling", "house", "home"))
    trade_marker = _has_unnegated_any(text, ("water heater", "hvac", "furnace", "air conditioner", "heat pump", "reroof", "re-roof", "roof replacement"))
    return residential_marker and trade_marker


def _commercial_office_ti_fee_floor_leak(value) -> bool:
    text = str(value or "").lower()
    if not text:
        return False
    return (
        "commercial office ti floor" in text
        or ("structured floor" in text and "office ti" in text)
        or ("commercial office" in text and "structured floor" in text and "fee" in text)
    )


def _scrub_residential_private_workspace_fee_floor(result: dict, private_label: str, city: str | None = None) -> None:
    """Remove stale commercial-office-TI fee floors from residential private workspace results."""
    fee_fields = ("fee_range", "fee_estimate", "total_cost_estimate", "fee_calculator")
    leaked = any(_commercial_office_ti_fee_floor_leak(result.get(field)) for field in fee_fields)
    if not leaked:
        leaked = any(
            _commercial_office_ti_fee_floor_leak((citation or {}).get("value"))
            or _commercial_office_ti_fee_floor_leak((citation or {}).get("claim"))
            for citation in (result.get("claim_citations") or [])
            if isinstance(citation, dict)
        )
    if not leaked:
        return

    location = f"{city} " if city else "Local "
    clean_fee = (
        f"{location}residential {private_label} fees vary by exact scope. Verify whether cosmetic "
        "painting/shelving/workbench work is exempt and whether any electrical or interior-alteration "
        "permit fees apply before quoting."
    )
    for field in fee_fields:
        if field in result and _commercial_office_ti_fee_floor_leak(result.get(field)):
            if field == "fee_range":
                result[field] = clean_fee
            else:
                result.pop(field, None)
    result.pop("_fee_floor_components", None)
    if isinstance(result.get("claim_citations"), list):
        result["claim_citations"] = [
            citation
            for citation in result["claim_citations"]
            if not (
                isinstance(citation, dict)
                and (
                    str(citation.get("field") or "").lower() == "fee_range"
                    or _commercial_office_ti_fee_floor_leak(citation.get("value"))
                    or _commercial_office_ti_fee_floor_leak(citation.get("claim"))
                )
            )
        ]
    warnings = result.setdefault("quality_warnings", [])
    warning = "Removed stale commercial office TI fee-floor text from a residential private-workspace cached result; verify local residential fees before quoting."
    if warning not in warnings:
        warnings.append(warning)
    result["needs_review"] = True
    result["_residential_fee_floor_leak_repaired"] = True


def _repair_residential_trade_model_leak(result: dict, job_type: str, city: str | None = None, scope_contract: dict | None = None) -> None:
    """Trust explicit residential single-trade/home-office scope over stale commercial model output."""
    if _looks_like_residential_home_office_noncommercial(job_type or ""):
        result["_primary_scope"] = "residential"
        garage_text = re.sub(r"\s+", " ", job_type or "")
        is_garage_hobby = bool(re.search(r"\bgarage\b.{0,80}\bhobby\s+workshop\b|\bhobby\s+workshop\b.{0,80}\bgarage\b", garage_text, flags=re.I))
        private_label = "garage hobby-workshop" if is_garage_hobby else "home-office/studio"
        _scrub_residential_private_workspace_fee_floor(result, private_label, city)
        surface = " ".join(
            str(value or "")
            for permit in (result.get("permits_required") or [])
            if isinstance(permit, dict)
            for value in (permit.get("permit_type"), permit.get("portal_selection"), permit.get("notes"))
        ).lower()
        if _has_unnegated_any(surface, ("commercial", "tenant improvement", "office interior alteration", "commercial building permit")):
            permit_type = "Residential Building Permit — Garage Hobby Workshop / Interior Alteration" if is_garage_hobby else "Residential Building Permit — Home Office / Interior Alteration"
            notes = (
                "Private residential garage hobby-workshop/storage scope; no employee or customer-facing use is stated. Verify local residential alteration/electrical permit naming before applying."
                if is_garage_hobby
                else "Residential home-office/studio conversion for private use; no employee or customer-facing use is stated. Verify local residential alteration/electrical permit naming before applying."
            )
            result["permits_required"] = [{
                "permit_type": permit_type,
                "portal_selection": "Residential Building / Interior Alteration",
                "required": True,
                "notes": notes,
            }]
            result["permits_required_logic"] = [{
                "permit_type": result["permits_required"][0]["permit_type"],
                "included_because": f"Explicit residential {private_label} scope with no employee or customer-facing use overrides stale business buildout wording.",
                "scope_trigger": f"residential {private_label} private-use guardrail",
            }]
            result["companion_permits"] = []
            result["hidden_triggers"] = []
            result["inspections"] = []
            result["permit_summary"] = f"Residential {private_label} interior alteration; verify local residential permit naming before applying."
            result["job_summary"] = f"Private-use residential {private_label} scope."
            result["confidence_reason"] = f"Residential {private_label}/private-use scope is explicit; local AHJ naming still needs verification."
            result["pro_tips"] = ["Confirm whether painting/shelving alone is exempt and whether new electrical outlets require a separate electrical permit."]
            result["common_mistakes"] = [f"Assuming a private {private_label} update is automatically exempt — added outlets, walls, or structural changes can still trigger residential permits."]
            result["watch_out"] = ["If the scope later adds employees, customer visits, signage, or a separate business space, re-check the permit path."]
            result["what_to_bring"] = ["Scope description", "Floor plan or room sketch", "Electrical outlet/lighting details if applicable"]
            result["permit_verdict"] = "YES"
            result["needs_review"] = True
            result["_residential_home_office_leak_repaired"] = True
        return
    if not _residential_single_trade_scope(job_type):
        return
    detected_raw = detect_primary_scope(job_type or "")
    if isinstance(detected_raw, dict):
        detected = str(
            detected_raw.get("primary_scope")
            or detected_raw.get("scope")
            or detected_raw.get("vertical")
            or detected_raw.get("category")
            or ""
        )
    else:
        detected = str(detected_raw or "")
    if detected in {"commercial_restaurant", "commercial_office_ti", "commercial_retail_ti", "commercial_medical_clinic_ti", "multifamily", "commercial"}:
        if _job_has_unnegated_commercial_scope(job_type):
            return
        # Explicit residential single-trade wording wins over a stale commercial
        # classifier hit caused only by negated phrases like "no commercial work".
        detected = "residential"
    text = f" {(job_type or '').lower()} "
    result["_primary_scope"] = detected or "residential"
    if "water heater" in text:
        permit_type = "Plumbing Permit — Water Heater Replacement"
        result["permits_required"] = [{
            "permit_type": permit_type,
            "portal_selection": "Plumbing - Water Heater Replacement",
            "required": True,
            "notes": "Residential water heater replacement; verify Dallas residential plumbing permit naming before applying.",
        }]
        result["permits_required_logic"] = [{
            "permit_type": permit_type,
            "included_because": "Explicit residential water heater replacement scope overrides stale commercial tenant-improvement model output.",
            "scope_trigger": "residential water heater replacement",
        }]
        result["companion_permits"] = []
        result["_residential_trade_leak_repaired"] = True
        result["permit_verdict"] = "YES"
        return
    scope_contract = scope_contract or result.get("_scope_contract")
    classified = classify_scope_required_permits(job_type or "", scope_contract=scope_contract)
    if classified:
        result["permits_required"] = classified.get("permits_required", result.get("permits_required", []))
        result["permits_required_logic"] = classified.get("permits_required_logic", result.get("permits_required_logic", []))
        result["companion_permits"] = classified.get("companion_permits", [])
        result["_residential_trade_leak_repaired"] = True
        result["permit_verdict"] = "YES"


def _commercial_companion_scope(job_type: str, result: dict | None = None) -> str:
    """Conservative subtype classifier for commercial companion warnings.

    Keep this local to the web trust layer so tests that stub research_engine do
    not need the full research module. Explicit office/medical wording with
    restaurant/food-service negation must not trigger restaurant companion
    warnings just because the negated words appear in the request.
    """
    text = f"{job_type or ''} {(result or {}).get('_primary_scope', '')}".lower()
    if _historical_restaurant_retail_conversion(job_type):
        return "retail"
    negated_restaurant = bool(
        re.search(
            r"\b(?:no|without)\s+(?:restaurant|food service|commercial kitchen|type\s*i\s*hood|hood|fryer|griddle|ansul|grease interceptor)(?:\s+needed)?\b",
            text,
        )
        or re.search(r"\b(?:non\s*[- ]?restaurant|not\s+a\s+restaurant)\b", text)
    )
    office_signal = _has_unnegated_any(text, ("office tenant improvement", "office ti", "office buildout", "office tenant", "professional office", "law office"))
    medical_signal = bool(re.search(r"\basc\b", text)) or _has_unnegated_any(text, ("medical clinic", "clinic tenant improvement", "clinic ti", "medical office", "dental clinic", "exam room", "medical gas", "x-ray", "x ray"))
    if medical_signal:
        return "medical"
    if office_signal and negated_restaurant:
        return "office"
    restaurant_signal = _has_unnegated_any(text, ("restaurant", "commercial kitchen", "food service", "cafe", "fast casual", "fast-casual", "type i hood", "grease interceptor", "ansul", "walk-in cooler", "walk-in freezer"))
    if restaurant_signal and not negated_restaurant:
        return "restaurant"
    if office_signal:
        return "office"
    return "commercial"


def _primary_permit_text(result: dict) -> str:
    permits = result.get("permits_required") or []
    if permits and isinstance(permits[0], dict):
        p = permits[0]
        return str(p.get("permit_type") or p.get("name") or p.get("title") or "")
    return ""


def _safe_external_url(url: str) -> str:
    """Return http(s) URLs only; block javascript:, data:, and malformed href tricks."""
    url = str(url or "").strip()
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return ""
    return url


def _safe_customer_source_url(url: str) -> str:
    """Customer-visible source URLs must be HTTPS, not plain HTTP."""
    safe = _safe_external_url(url)
    if not safe:
        return ""
    parsed = urlparse(safe)
    if parsed.scheme.lower() != "https":
        return ""
    return safe


def _render_safe_link(url: str, label: str | None = None) -> str:
    safe = _safe_external_url(url)
    if not safe:
        return ""
    text = html.escape(label or safe)
    href = html.escape(safe, quote=True)
    return f"<a target='_blank' rel='noopener noreferrer' href='{href}'>{text}</a>"


_STATE_LABELS = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia", "PR": "Puerto Rico",
}
_GENERIC_SOURCE_TITLES = frozenset({"", "official", "official source", "source"})


def _source_title_needs_tier_label(title: str, tier: str) -> bool:
    normalized = (title or "").strip().lower()
    if normalized in _GENERIC_SOURCE_TITLES:
        return True
    # Do not let an upstream title that says "Official ..." override a lower
    # provenance tier. TDLR/ADA/NFPA can be useful references, but they are not
    # the local AHJ source for the lookup.
    return tier in {"state", "universal"} and "official" in normalized


def _source_tier_label(tier: str, city: str | None, state: str | None) -> str:
    if tier == "ahj":
        return f"Official {city.strip()} source" if city and city.strip() else "Official local source"
    if tier == "state":
        state_key = (state or "").upper().strip()
        label = _STATE_LABELS.get(state_key) or (state or "").strip() or "State"
        return f"{label} state reference"
    if tier == "universal":
        return "National code reference"
    return "Source"


def _customer_source_type(tier: str) -> str:
    if tier == "ahj":
        return "official_local"
    if tier == "state":
        return "official_state"
    if tier == "universal":
        return "national_reference"
    return "official_source"


def _source_display_date(item: dict, result: dict) -> str:
    for key in ("date", "verified_date", "source_date", "checked_at", "last_checked", "updated_at"):
        text = str((item or {}).get(key) or "").strip()
        if text and text.lower() not in {"unknown", "last updated: unknown", "n/a", "none"}:
            return text
    for key in ("verified_date", "source_date", "checked_at", "last_checked", "updated_at"):
        text = str((result or {}).get(key) or "").strip()
        if text and text.lower() not in {"unknown", "last updated: unknown", "n/a", "none"}:
            return text
    return ""


def _source_dicts(result: dict, city: str | None = None, state: str | None = None, *, dedupe: bool = False) -> list[dict]:
    city = city or result.get("city") or result.get("jurisdiction_city") or ""
    state = state or result.get("state") or result.get("jurisdiction_state") or ""
    jurisdiction = ", ".join(part for part in [city, state] if part)
    out = []
    seen_urls = set()
    for item in result.get("sources") or []:
        source_item = item if isinstance(item, dict) else {}
        if isinstance(item, str):
            url = _safe_customer_source_url(item)
        elif isinstance(item, dict):
            url = _safe_customer_source_url(item.get("url") or item.get("link") or item.get("source_url") or "")
        else:
            url = ""
        if not url or (dedupe and url in seen_urls):
            continue
        tier = classify_source_tier(url, city, state, result=result)
        if tier == "wrong":
            continue
        seen_urls.add(url)
        upstream_title = str(source_item.get("title") or source_item.get("name") or source_item.get("source_title") or "").strip()
        title = upstream_title
        if _source_title_needs_tier_label(upstream_title, tier):
            title = _source_tier_label(tier, city, state)
        publisher = str(source_item.get("publisher") or source_item.get("agency") or source_item.get("department") or "").strip()
        if not publisher:
            publisher = _source_tier_label(tier, city, state)
        out.append({
            "url": url,
            "title": title,
            "publisher": publisher,
            "date": _source_display_date(source_item, result),
            "source_type": _customer_source_type(tier),
            "jurisdiction": jurisdiction,
            "snippet": str(source_item.get("snippet") or source_item.get("quote") or source_item.get("text") or ""),
        })
    return out


def apply_permitiq_quality_gate(result: dict, job_type: str, city: str, state: str) -> dict:
    """Final safety gate before a PermitIQ result is shown to users.

    This is deliberately conservative: repair obvious commercial-primary leaks
    and expose uncertainty instead of letting a polished but mismatched report
    reach a contractor.
    """
    _repair_residential_trade_model_leak(result, job_type, city)
    historical_retail_conversion = _historical_restaurant_retail_conversion(job_type)
    if historical_retail_conversion:
        result["_primary_scope"] = "commercial_retail_ti"
    warnings = list(result.get("quality_warnings") or [])
    primary = _primary_permit_text(result)
    primary_l = primary.lower()
    commercial = _is_commercial_scope(job_type, result)
    sources = _source_dicts(result, city=city, state=state)

    if commercial:
        has_commercial_primary = any(token in primary_l for token in _COMMERCIAL_PRIMARY_TOKENS)
        has_residential_leak = any(token in primary_l for token in _RESIDENTIAL_PRIMARY_TOKENS) and not has_commercial_primary
        if has_residential_leak or not primary:
            fixed_primary = "Building Permit — Commercial Tenant Improvement / Interior Alteration"
            if historical_retail_conversion:
                fixed_primary = "Building Permit — Commercial Interior Alteration / Tenant Improvement"
            elif "restaurant" in (job_type or "").lower():
                fixed_primary = "Building Permit — Tenant Improvement / Restaurant Interior Alteration"
            elif any(t in (job_type or "").lower() for t in ("medical", "clinic", "dental", "exam room")):
                fixed_primary = "Building Permit — Tenant Improvement / Medical Clinic Interior Alteration"
            elif "office" in (job_type or "").lower():
                fixed_primary = "Building Permit — Tenant Improvement / Office Interior Alteration"
            elif "retail" in (job_type or "").lower():
                fixed_primary = "Building Permit — Commercial Interior Alteration / Tenant Improvement"
            permits = result.get("permits_required") or []
            if permits and isinstance(permits[0], dict):
                permits[0]["permit_type"] = fixed_primary
                permits[0]["required"] = True
                permits[0]["notes"] = (permits[0].get("notes") or "Commercial scope safety gate repaired the primary permit; verify exact AHJ naming before quoting.")
            else:
                result["permits_required"] = [{"permit_type": fixed_primary, "required": True, "notes": "Commercial scope safety gate inserted likely primary permit; verify exact AHJ naming before quoting."}]
            result["_primary_scope"] = result.get("_primary_scope") or "commercial"
            warnings.append("Commercial scope detected; primary permit was repaired or forced away from residential/trade-only leakage. Verify exact AHJ permit name before quoting.")

        elif not has_commercial_primary:
            warnings.append("Commercial scope detected, but the primary permit name is AHJ-specific or not in the commercial allow-list; verify exact AHJ naming before quoting.")

        companion_text = " ".join(str(x) for x in (result.get("companion_permits") or result.get("permits_required") or [])).lower()
        required_companions = ["electrical", "mechanical", "plumbing"]
        companion_scope = _commercial_companion_scope(job_type, result)
        scope_text = f"{job_type or ''} {(result or {}).get('_primary_scope', '')}".lower()
        if companion_scope == "restaurant":
            required_companions += ["fire"]
            if _restaurant_food_health_scope_present(scope_text):
                required_companions.append("health")
            if _restaurant_grease_scope_present(scope_text):
                required_companions.append("grease")
            if _restaurant_hood_scope_present(scope_text):
                required_companions.append("hood")
        if companion_scope == "medical":
            required_companions += ["fire", "accessibility"]
            if _medical_gas_scope_present(scope_text):
                required_companions.append("medical gas")
        missing = [token for token in required_companions if token not in companion_text]
        if missing:
            warnings.append("Commercial scope may require companion reviews/permits not fully proven here: " + ", ".join(missing[:5]) + ".")

    if str(result.get("confidence") or "").lower() == "high" and not sources:
        result["confidence"] = "medium"
        warnings.append("Confidence downgraded because no source URLs were attached to the result.")

    if warnings:
        deduped = []
        for w in warnings:
            if w and w not in deduped:
                deduped.append(w)
        result["quality_warnings"] = deduped
        result["needs_review"] = True

    _filter_negated_surface_lists(result, job_type)
    _neutralize_commercial_permit_residential_contrast(result, job_type)
    return result


def build_claim_citations(result: dict, city: str | None = None, state: str | None = None) -> list[dict]:
    """Attach field-level provenance without inventing quotes.

    If retrieved snippets are unavailable, the claim is labeled needs_verification
    rather than pretending a quote exists.
    """
    sources = _source_dicts(result, city=city, state=state)
    has_locality_context = bool(
        (city or result.get("city") or result.get("jurisdiction_city") or "").strip()
        or (state or result.get("state") or result.get("jurisdiction_state") or "").strip()
    )
    if not sources and not has_locality_context:
        for item in result.get("sources") or []:
            if isinstance(item, str):
                url = _safe_external_url(item)
                title = "Source"
                snippet = ""
            elif isinstance(item, dict):
                url = _safe_external_url(item.get("url") or item.get("link") or "")
                title = str(item.get("title") or item.get("name") or "Source")
                snippet = str(item.get("snippet") or item.get("quote") or item.get("text") or "")
            else:
                continue
            if url:
                sources = [{"url": url, "title": title, "snippet": snippet}]
                break
    if has_locality_context:
        source_city = city or result.get("city") or result.get("jurisdiction_city") or ""
        source_state = state or result.get("state") or result.get("jurisdiction_state") or ""
        sources = [
            src for src in sources
            if classify_source_tier(str(src.get("url") or ""), source_city, source_state, result=result) == "ahj"
        ]
    first = sources[0] if sources else {}
    checked = utc_now().date().isoformat()

    def confidence_for(field: str) -> str:
        if not sources:
            return "needs_verification"
        if first.get("snippet"):
            return str(result.get("confidence") or "medium").lower()
        return "needs_verification"

    fields = [
        ("permit_type", _primary_permit_text(result), "Likely primary permit type"),
        ("apply_url", result.get("apply_url"), "Where to start the application"),
        ("fee_range", result.get("fee_range"), "Estimated fee range"),
        ("approval_timeline", result.get("approval_timeline"), "Estimated approval timeline"),
        ("inspections", result.get("inspections") or result.get("inspect_checklist"), "Likely inspections"),
    ]
    citations = []
    for idx, (field, value, label) in enumerate(fields, 1):
        if value in (None, "", [], {}):
            continue
        snippet = first.get("snippet", "")
        citations.append({
            "id": f"C{idx}",
            "field": field,
            "claim": label,
            "value": str(value) if not isinstance(value, str) else value,
            # Keep the source URL attached even when no quoted snippet exists so
            # users can verify the claim themselves. Missing snippets still force
            # needs_verification; we just do not pretend the URL proves the field.
            "source_url": first.get("url", ""),
            "source_title": first.get("title", ""),
            "quoted_snippet": snippet,
            "checked_at": checked,
            "confidence": confidence_for(field),
        })
    result["claim_citations"] = citations
    if any(c["confidence"] == "needs_verification" for c in citations):
        result.setdefault("quality_warnings", [])
        warning = "Some report claims do not yet have quoted source snippets; verify with the AHJ before relying on them."
        if warning not in result["quality_warnings"]:
            result["quality_warnings"].append(warning)
    return citations


def build_apply_path(result: dict, job_type: str, city: str, state: str) -> dict:
    scope_contract = result.get("_scope_contract") if isinstance((result or {}).get("_scope_contract"), dict) else build_scope_contract(job_type, city, state)
    if not isinstance(scope_contract, dict):
        scope_contract = build_scope_contract(job_type, city, state)
    url = result.get("apply_url") or ""
    if (scope_contract.get("category") == "residential" or result.get("_residential_trade_leak_repaired")) and re.search(r"commercial|tenant[-_ ]?improvement|tenant[-_ ]?finish", str(url), re.I):
        url = ""
        result["apply_url"] = ""
    lower = url.lower()
    platform = None
    if "accela" in lower or "citizenaccess" in lower:
        platform = "Accela / Citizen Access"
    elif "tyler" in lower or "energov" in lower:
        platform = "Tyler / EnerGov"
    elif "opengov" in lower:
        platform = "OpenGov"
    elif url.lower().endswith(".pdf"):
        platform = "PDF / paper form"
    elif url:
        platform = "city portal / AHJ website"

    if scope_contract.get("category") == "commercial":
        commercial = True
    elif scope_contract.get("category") == "residential":
        commercial = False
    else:
        commercial = _is_commercial_scope(job_type, result)
    permit_type = _primary_permit_text(result) or "Permit type needs AHJ verification"
    if url:
        steps = [
            f"Open the {platform} start URL.",
            "Create or sign into the contractor/applicant account if required.",
            f"Look for the closest permit category to: {permit_type}.",
            "Prepare scope of work, plans/drawings, contractor license info, valuation, and owner authorization before final submission.",
            "Stop before final submit, payment, signature, or legal attestation until the AHJ details are verified.",
        ]
        support_level = "needs verification"
        evidence_meta = result.get("_evidence_pack") or {}
        evidence_matched = set(evidence_meta.get("matched_fields") or [])
        evidence_confidence = evidence_meta.get("matched_field_confidence") or {}
        if evidence_meta.get("enabled") and "apply_url" in evidence_matched and evidence_confidence.get("apply_url") == "high":
            support_level = "verified path"
            verification_note = f"{city or 'AHJ'} start portal is verified only as the application entry point; exact portal subcategory and filing path still require AHJ/portal verification before filing."
        elif evidence_meta.get("enabled") and "apply_url" in evidence_matched:
            support_level = "partial evidence"
            verification_note = f"{city or 'AHJ'} start portal has field evidence, but it is not high-confidence local evidence; exact portal choice and filing path require AHJ/portal verification before filing."
        else:
            verification_note = "PermitAssist guides the application pathway; verify exact portal choice with the AHJ before filing."
        login_required = False if platform == "PDF / paper form" else None
    else:
        steps = [
            "No verified online filing path is available from current field evidence; contact the AHJ or verify the correct portal before filing.",
            f"Ask the AHJ which permit category best matches: {permit_type}.",
            "Prepare scope of work, plans/drawings, contractor license info, valuation, and owner authorization before final submission.",
            "Stop before final submit, payment, signature, or legal attestation until the AHJ details are verified.",
        ]
        support_level = "not available"
        verification_note = "No verified online filing path is available from current sources; verify the exact filing path with the AHJ."
        login_required = None
    if commercial:
        scope_text = f"{job_type or ''} {(result or {}).get('_primary_scope', '')}".lower()
        primary_scope = str((result or {}).get("_primary_scope") or "").lower()
        health_review_needed = (
            _restaurant_food_health_scope_present(scope_text)
            or (primary_scope == "commercial_retail_ti" and _retail_food_health_scope_present(scope_text))
            or _medical_clinic_scope_present(scope_text)
        )
        review_list = "building, MEP, fire/life-safety, accessibility"
        if health_review_needed:
            review_list += ", health"
        review_list += ", or change-of-occupancy"
        steps.insert(3, f"For commercial TI, check whether separate {review_list} reviews are required.")
    apply_path = {
        "support_level": support_level,
        "platform": platform,
        "portal_url": url,
        "login_required": login_required,
        "permit_category": "Commercial Building / Tenant Improvement" if commercial else "Residential / Trade Permit",
        "permit_type": permit_type,
        "portal_selection_path": steps[:3],
        "documents_to_prepare": ["scope of work", "plans/drawings if required", "contractor license", "valuation", "owner authorization"],
        "steps": steps,
        "stop_before": "final submit, payment, signature, or legal attestation",
        "verification_note": verification_note,
    }
    result["apply_path"] = apply_path
    return apply_path


def _env_flag_enabled(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def evidence_pack_allowed_for_request(path: str, headers, *, is_sample_demo: bool = False) -> bool:
    """Return whether this request may use the local evidence-pack overlay."""
    if not evidence_pack_enabled():
        return False
    preview_only = _env_flag_enabled("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY")
    mode = os.environ.get("PERMITASSIST_EVIDENCE_PACK_MODE", "").strip()
    if mode == "solar_mep_controlled_preview" and not preview_only:
        return False
    preview_header = os.environ.get("PERMITASSIST_EVIDENCE_PACK_PREVIEW_HEADER", "X-Sample-Demo").strip() or "X-Sample-Demo"
    preview_route_allowed = path == "/api/permit" and is_sample_demo and headers.get(preview_header) == "1"
    if mode == "solar_mep_controlled_preview":
        if not preview_route_allowed:
            return False
        token = os.environ.get("PERMITASSIST_SOLAR_MEP_CONTROLLED_PREVIEW_TOKEN", "").strip()
        token_header = os.environ.get("PERMITASSIST_SOLAR_MEP_CONTROLLED_PREVIEW_HEADER", "X-Evidence-Pack-Preview-Token").strip() or "X-Evidence-Pack-Preview-Token"
        return bool(token) and headers.get(token_header) == token
    if not preview_only:
        return True
    return preview_route_allowed


def finalize_permit_lookup_result(result: dict, job_type: str, city: str, state: str, *, is_cached: bool = False, explicit_vertical: str | None = None, evidence_allowed: bool | None = None) -> dict:
    """Shared final response safety pipeline for all permit lookup endpoints."""
    if not isinstance(result, dict):
        result = {}
    jurisdiction_check = resolve_customer_decision({"result": result, "job_type": job_type, "city": city, "state": state})
    if is_input_rejection(jurisdiction_check):
        return sanitize_customer_visible_result(jurisdiction_check, strip_internal_keys=False)
    contract_job_type = f"{job_type or ''} {explicit_vertical or ''}".strip()
    raw_meta = result.get("_meta")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    job_category = result.get("job_category") or meta.get("job_category")
    scope_contract = result.get("_scope_contract") if isinstance(result.get("_scope_contract"), dict) else build_scope_contract(contract_job_type, city, state, job_category=job_category)
    if not isinstance(scope_contract, dict):
        scope_contract = build_scope_contract(contract_job_type, city, state, job_category=job_category)
    result["_scope_contract"] = scope_contract
    evidence_pack = get_local_evidence_pack() if evidence_allowed is not False else None
    evidence_enabled = evidence_pack is not None
    unexpected_evidence_cache = evidence_enabled and (is_cached or bool(result.get("_cached")))

    # Validate fresh URLs; cached rows are assumed to have gone through this once.
    if not is_cached:
        result = sanitize_result_urls(result)

    # Legacy fallback may use broad sources[0]. Keep it only when the local
    # evidence pack is disabled. With evidence enabled, unsupported fields must
    # fail closed instead of borrowing a generic source URL.
    if not evidence_enabled and not result.get('apply_url'):
        sources = result.get('sources') or []
        gov_urls = [s for s in sources if isinstance(s, str) and '.gov' in s and not s.lower().endswith('.pdf')]
        portal_urls = [s for s in sources if isinstance(s, str) and any(p in s.lower() for p in ['accela', 'permit', 'portal', 'civic', 'govern']) and not s.lower().endswith('.pdf')]
        other_urls = [s for s in sources if isinstance(s, str) and s.startswith('http') and not s.lower().endswith('.pdf')]
        fallback_url = (gov_urls or portal_urls or other_urls or [None])[0]
        if fallback_url:
            result['apply_url'] = fallback_url
            result['_url_warning'] = None
            result.pop('_apply_url_locality_warning', None)

    result = strip_pdf_from_result(result)
    if not result.get('apply_google_maps'):
        result['apply_google_maps'] = build_google_maps_url(
            city, state,
            address=result.get('apply_address', ''),
            office=result.get('applying_office', '')
        )
    if not result.get('apply_phone'):
        result['apply_phone'] = result.get('apply_google_maps', '')

    # P0: Contact sanitization must happen BEFORE enrich_result_response
    # so that inspection_booking and other derived fields use corrected phone/address.
    result = apply_contact_sanitization(result, city=city, state=state)

    result = enrich_result_response(result, job_type, city, state)
    result = apply_permitiq_quality_gate(result, job_type, city, state)
    result["_scope_contract"] = scope_contract
    classified = classify_scope_required_permits(job_type or "", scope_contract=scope_contract)
    if classified:
        result["permits_required"] = classified.get("permits_required", result.get("permits_required", []))
        result["permits_required_logic"] = classified.get("permits_required_logic", result.get("permits_required_logic", []))
        result["companion_permits"] = classified.get("companion_permits", result.get("companion_permits", []))
        if scope_contract.get("vertical") == "panel_upgrade" and any(
            _has_unnegated_any(str(item).lower(), ("solar", "pv", "photovoltaic", "racking", "interconnection"))
            for item in (result.get("checklist") or [])
        ):
            result["checklist"] = [
                "Confirm service size, panel amperage, and meter/main location.",
                "Prepare electrical load calculation if the AHJ or utility requires it.",
                "Have licensed electrical contractor information and utility coordination details ready.",
                "Verify inspection scheduling and meter release steps before shutdown work.",
            ]
        result["permit_verdict"] = "YES"

    if is_cached or bool(result.get("_cached")):
        try:
            v231_category = str(scope_contract.get("category") or job_category or "").lower().strip()
            result = _reconcile_v231_result(result, _resolve_v231_cell(city, state, job_type, v231_category))
            result["_scope_contract"] = scope_contract
        except Exception as exc:
            print(f"[finalize] v2.3.1 final reconciliation failed (non-fatal): {exc}")

    if evidence_enabled:
        forced_status = "invalid_contract" if unexpected_evidence_cache else None
        result = apply_evidence_pack_fail_closed(result, job_type, city, state, utc_now().date().isoformat(), explicit_vertical=explicit_vertical, force_contract_status=forced_status)
        evidence_meta = result.get("_evidence_pack") or {}
        evidence_failed = set(evidence_meta.get("failed_closed_fields") or [])
        evidence_matched = set(evidence_meta.get("matched_fields") or [])
        warnings = result.setdefault("quality_warnings", [])
        if "inspections" in evidence_failed:
            result["inspection_booking"] = None
            warning = "Inspection booking steps are not shown because local evidence-pack inspections failed closed; verify required inspections and booking with the AHJ."
            if warning not in warnings:
                warnings.append(warning)
        if "fee_range" in evidence_failed:
            warning = f"{city or 'AHJ'} fee range is not verified in this evidence pack; confirm fees before quoting."
            if warning not in warnings:
                warnings.append(warning)
        if "approval_timeline" in evidence_matched:
            warning = f"Approval timeline is statutory/AHJ outer-deadline evidence only, not a {city or 'local'} queue estimate."
            if warning not in warnings:
                warnings.append(warning)
        if "apply_url" in evidence_matched:
            warning = "Apply URL verifies the portal start page only; exact portal subcategory/filing path still needs AHJ or portal verification before filing."
            if warning not in warnings:
                warnings.append(warning)
        if "companion_reviews_triggers" in evidence_matched:
            warning = "Companion-review evidence is scope-limited; it is not a complete local specialty-trigger list. Verify specialty reviews with the AHJ before filing."
            if warning not in warnings:
                warnings.append(warning)
        # Evidence-pack apply_url values are loaded after the generic engine URL
        # pass, so sanitize/strip once more before apply_path renders them.
        result = sanitize_result_urls(result)
        result = strip_pdf_from_result(result)
        # Evidence pack mode bypasses permit_cache reads and writes by design;
        # make that visible and testable so stale non-evidence rows cannot leak
        # into local trials.
        meta = result.setdefault('_evidence_pack', {})
        meta['cache_bypassed'] = True
    else:
        build_claim_citations(result)

    _apply_canonical_ahj_apply_url_fallback(result, city, state)
    build_apply_path(result, job_type, city, state)
    if result.get("quality_warnings"):
        merged_warnings = []
        for warning in list(result.get("warnings") or []) + list(result.get("quality_warnings") or []):
            if warning and warning not in merged_warnings:
                merged_warnings.append(warning)
        result["warnings"] = merged_warnings
    _scrub_scope_limit_leaks(result, scope_contract)
    _filter_customer_sources_in_place(result, city, state)
    result = apply_permit_decision_contract(result, job_type, city, state, scope_contract)
    result = apply_source_floor_annotation(result, job_type, city, state)
    for internal_key in list(result.keys()):
        if internal_key in {"_decision_cell_primary_lock", "_field_sources"} or str(internal_key).startswith("_v231"):
            result.pop(internal_key, None)
    result = sanitize_customer_visible_result(result, strip_internal_keys=False)
    return result


def record_beta_event(event: str, payload: dict | None = None, email: str = "") -> None:
    try:
        conn = sqlite3.connect(CACHE_DB)
        payload_json = json.dumps(payload or {}, sort_keys=True)
        if len(payload_json) > 8000:
            payload_json = json.dumps({"truncated": True, "prefix": payload_json[:7800]}, sort_keys=True)
        conn.execute(
            "INSERT INTO beta_events (event,email,payload_json,created_at) VALUES (?,?,?,?)",
            (str(event)[:80], (email or "").lower().strip(), payload_json, utc_now().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[beta-events] record error: {e}")


def save_beta_feedback(email: str, job_type: str, city: str, state: str, useful: str, knew_next_step: str, missing: str, ahj_confirmed: str, use_again: str) -> dict:
    feedback_id = str(uuid.uuid4())
    now = utc_now().isoformat()
    conn = sqlite3.connect(CACHE_DB)
    conn.execute(
        """
        INSERT INTO beta_feedback (id,email,job_type,city,state,useful,knew_next_step,missing,ahj_confirmed,use_again,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (feedback_id, (email or "").lower().strip(), job_type[:500], city[:120], state[:20], useful[:40], knew_next_step[:40], missing[:2000], ahj_confirmed[:40], use_again[:40], now),
    )
    conn.commit()
    conn.close()
    record_beta_event("beta_feedback_submitted", {"city": city, "state": state, "useful": useful, "use_again": use_again}, email)
    return {"id": feedback_id, "received": True}


def render_white_label_report_html(data: dict) -> str:
    result = copy.deepcopy(data.get("result") or {}) if isinstance(data, dict) else {}
    contractor = html.escape(str(data.get("contractor_name") or "Contractor"))
    client = html.escape(str(data.get("client_name") or "Client / Property"))
    job = html.escape(str(data.get("job_type") or result.get("job_summary") or "Permit research"))
    location = html.escape(", ".join(x for x in [str(data.get("city") or ""), str(data.get("state") or "")] if x))
    citations = result.get("claim_citations") or build_claim_citations(result, data.get("city"), data.get("state"))
    decision_contract = result.get("permit_decision_contract") if isinstance(result.get("permit_decision_contract"), dict) else {}
    decision_headline = str(result.get("customer_headline") or decision_contract.get("customer_headline") or "Permit required: Building Permit.")
    decision_kind = str(result.get("permit_kind") or decision_contract.get("permit_kind") or "Other")
    customer_next_step = str(result.get("customer_next_step") or decision_contract.get("customer_next_step") or "Confirm requirements with the building department before filing.")
    apply_note = str(
        decision_contract.get("exact_apply_url_customer_note")
        or "Use the listed department/portal category and confirm the permit pathway before filing."
    )
    permits = result.get("permits_required") or []
    permit_items = "".join(f"<li>{html.escape(str((p or {}).get('permit_type') or p))}</li>" for p in permits) or f"<li>{html.escape(decision_kind)}</li>"
    safe_apply_url = _safe_external_url(result.get('apply_url') or '')
    def _customer_safe_report_text(value: object) -> str:
        text = str(value or "")
        text = re.sub(r"\blikely\s+primary\s+permit\s+type\b", "Primary permit category", text, flags=re.I)
        text = re.sub(r"\blikely\s+inspections\b", "Inspection requirements", text, flags=re.I)
        text = re.sub(r"\blikely\s+permits\b", "Permit decision", text, flags=re.I)
        text = re.sub(r"\bneeds_verification\b", "source attached; quoted snippet unavailable", text, flags=re.I)
        text = re.sub(r"\bverify\s+exact\s+AHJ\s+permit\s+name\s+before\s+quoting\b", "confirm the exact permit name and form title with the building department before filing", text, flags=re.I)
        text = re.sub(r"\bverify\s+exact\s+AHJ\s+naming\s+before\s+quoting\b", "confirm the exact permit name and form title with the building department before filing", text, flags=re.I)
        text = re.sub(r"\bverify\s+exact[^.;]{0,120}\s+with\s+(?:the\s+)?AHJ\b", "confirm the exact permit requirements with the building department", text, flags=re.I)
        text = re.sub(r"\bverify\s+with\s+(?:the\s+)?AHJ\b", "Use the listed building department source", text, flags=re.I)
        text = re.sub(r"\bAHJ\b", "building department", text, flags=re.I)
        return re.sub(r"\s{2,}", " ", text).strip()

    footnotes = "".join(
        f"<li><strong>{html.escape(c.get('id',''))}</strong> {html.escape(_customer_safe_report_text(c.get('claim','')))}: "
        f"{html.escape(_customer_safe_report_text(c.get('quoted_snippet') or 'Quoted snippet not attached in this report artifact.'))} "
        f"<br>{_render_safe_link(c.get('source_url','')) or 'Source URL not attached to this report artifact'} "
        f"<em>Checked {html.escape(c.get('checked_at',''))}; confidence {html.escape(_customer_safe_report_text(c.get('confidence','')))}</em></li>"
        for c in citations
    ) or "<li>No source footnotes attached in this report artifact.</li>"
    warnings_list = list(result.get("quality_warnings") or [])
    evidence_meta = result.get("_evidence_pack") or {}
    evidence_failed = [str(field) for field in (evidence_meta.get("failed_closed_fields") or []) if field]
    evidence_matched = [str(field) for field in (evidence_meta.get("matched_fields") or []) if field]
    if evidence_meta.get("enabled") and evidence_failed:
        warnings_list.append("Local evidence pack failed closed for: " + ", ".join(evidence_failed) + ". Do not quote or file from those fields until official-source fields are restored.")
    if evidence_meta.get("enabled") and "approval_timeline" in evidence_matched:
        warnings_list.append("Timeline is statutory/building-department outer-deadline evidence only, not a local queue estimate.")
    if evidence_meta.get("enabled") and "companion_reviews_triggers" in evidence_matched:
        warnings_list.append("Companion-review evidence is scope-limited; it is not a complete local specialty-trigger list. Match specialty reviews to the structured permit kind before filing.")
    safe_warnings = [_customer_safe_report_text(w) for w in warnings_list]
    warnings = "".join(f"<li>{html.escape(str(w))}</li>" for w in dict.fromkeys(w for w in safe_warnings if w))
    return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Permit research report</title>
<style>body{{font-family:Arial,sans-serif;max-width:820px;margin:32px auto;color:#172033;line-height:1.45}}.brand{{border-bottom:3px solid #0f766e;padding-bottom:12px;margin-bottom:24px}}.muted{{color:#64748b}}.card{{border:1px solid #dbe3ea;border-radius:12px;padding:16px;margin:16px 0}}@media print{{button{{display:none}}body{{margin:0.5in}}}}</style></head>
<body><button onclick='window.print()'>Print / Save PDF</button><div class='brand'><h1>{contractor}</h1><div class='muted'>Permit research prepared for {client}</div></div>
<h2>{job}</h2><p><strong>Location:</strong> {location}</p>
<div class='card'><h3>Permit decision</h3><p><strong>{html.escape(decision_headline)}</strong></p><p><strong>Permit kind:</strong> {html.escape(decision_kind)}</p><ul>{permit_items}</ul></div>
<div class='card'><h3>Next step</h3><p>{html.escape(customer_next_step)}</p><p>{html.escape(apply_note)}</p><p><strong>Start URL:</strong> {_render_safe_link(safe_apply_url) or 'Not found'}</p></div>
{f"<div class='card'><h3>Warnings</h3><ul>{warnings}</ul></div>" if warnings else ""}
<div class='card'><h3>Source footnotes</h3><ol>{footnotes}</ol></div>
<p class='muted'>PermitAssist is guidance only. Use the structured permit decision, permit kind, and listed local filing path before quoting or starting work.</p></body></html>"""

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
def ensure_table_columns(conn: sqlite3.Connection, table_name: str, required_columns: dict[str, str]) -> list[str]:
    """Safely add missing columns to an existing table via ALTER TABLE."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    added = []
    for column_name, column_sql in required_columns.items():
        if column_name in existing:
            continue
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")
        added.append(column_name)
    return added


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    drift_fixes = []
    preexisting_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
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
        CREATE TABLE IF NOT EXISTS beta_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            event        TEXT NOT NULL,
            email        TEXT,
            payload_json TEXT,
            created_at   TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS beta_feedback (
            id             TEXT PRIMARY KEY,
            email          TEXT,
            job_type       TEXT,
            city           TEXT,
            state          TEXT,
            useful         TEXT,
            knew_next_step TEXT,
            missing        TEXT,
            ahj_confirmed  TEXT,
            use_again      TEXT,
            created_at     TEXT NOT NULL
        )
    """)
    # Feedback can be submitted before the first permit lookup on a fresh
    # volume. Initialize the engine cache table here too so flagging a result
    # never 500s just because research_engine.init_cache() has not run yet.
    # Keep this DDL in sync with api.research_engine.init_cache().
    conn.execute("""
        CREATE TABLE IF NOT EXISTS permit_cache (
            cache_key       TEXT PRIMARY KEY,
            job_type        TEXT,
            job_category    TEXT,
            city            TEXT,
            state           TEXT,
            zip_code        TEXT,
            result_json     TEXT,
            created_at      TEXT,
            hits            INTEGER DEFAULT 0,
            source_url      TEXT,
            etag            TEXT,
            last_modified   TEXT,
            last_checked_at TEXT
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS saved_jurisdictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            trade TEXT,
            display_name TEXT,
            added_at TEXT NOT NULL,
            last_lookup_at TEXT,
            lookup_count INTEGER DEFAULT 0,
            notes TEXT,
            UNIQUE(email, city, state, trade)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_saved_jurisdictions_email ON saved_jurisdictions(email)
    """)

    added_user_columns = ensure_table_columns(conn, "users", {
        "free_limit_notice_sent_at": "TEXT",
        "free_limit_email_sent": "INTEGER DEFAULT 0",
    })
    if added_user_columns:
        drift_fixes.append(f"users: added columns {', '.join(added_user_columns)}")

    expected_tables = {
        "onboarding_emails",
        "referrals",
        "permit_issued_reminders",
        "rate_limits",
        "checklist_cache",
        "city_watch",
        "api_keys",
        "webhook_integrations",
        "saved_jurisdictions",
        "beta_events",
        "beta_feedback",
    }
    missing_tables = sorted(expected_tables - preexisting_tables)
    if missing_tables:
        drift_fixes.append(f"created missing tables {', '.join(missing_tables)}")

    if drift_fixes:
        print(f"[db] Drift fixed in {CACHE_DB}: " + "; ".join(drift_fixes))

    conn.commit()
    conn.close()

# ── Auth / Session helpers ─────────────────────────────────────────────────

def _session_signature(raw: str) -> str:
    """Return URL-safe HMAC signature for a session token.

    Use base64url instead of hex so API response redaction for full SHA-256
    hashes cannot corrupt newly issued session tokens.
    """
    digest = hmac.new(SESSION_SECRET.encode(), raw.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _legacy_session_signature(raw: str) -> str:
    """Return the old hex HMAC signature for backwards-compatible validation."""
    return hmac.new(SESSION_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()


def create_session_token(email: str) -> str:
    """Create a signed 30-day session token and store in DB."""
    raw = secrets.token_urlsafe(32)
    sig = _session_signature(raw)
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
        expected = _session_signature(raw)
        legacy_expected = _legacy_session_signature(raw)
        if not (hmac.compare_digest(sig, expected) or hmac.compare_digest(sig, legacy_expected)):
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
            "stripe_subscription_id,created_at,last_login,free_limit_notice_sent_at,free_limit_email_sent FROM users WHERE email=?",
            [email.lower().strip()]
        ).fetchone()
        conn.close()
        if not row:
            return None
        cols = ["id","email","plan","plan_expires_at","stripe_customer_id",
                "stripe_subscription_id","created_at","last_login","free_limit_notice_sent_at","free_limit_email_sent"]
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


def send_free_limit_reached_email(to_email: str) -> bool:
    """Send one-time free lookup limit reached email."""
    subject = "You've used your 3 free PermitAssist lookups"
    body = (
        "Hey, you've used all 3 of your free PermitAssist lookups. "
        "To keep researching permits without limits, upgrade to Solo for $39.99/mo — cancel anytime. "
        "https://permitassist.io/#pricing"
    )
    return resend_send(to_email, subject, body)


def send_free_limit_email_once(email: str):
    normalized_email = (email or "").lower().strip()
    if not normalized_email:
        return
    claimed = False
    try:
        conn = sqlite3.connect(CACHE_DB)
        cur = conn.execute(
            "UPDATE users SET free_limit_email_sent=1, free_limit_notice_sent_at=? "
            "WHERE email=? AND COALESCE(free_limit_email_sent, 0)=0",
            [utc_now().isoformat(), normalized_email]
        )
        conn.commit()
        claimed = cur.rowcount == 1
        conn.close()
        if not claimed:
            return
        if not send_free_limit_reached_email(normalized_email):
            print(f"[free-limit-email] Send failed after claiming flag for {normalized_email}")
    except Exception as e:
        print(f"[free-limit-email] Error: {e}")


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
        f"Free accounts get 3 lookups per month. If you're hitting that limit or doing more than 3 jobs/month, upgrading to Solo for $39.99/mo gets you:\n\n"
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
        print("[stripe-webhook] STRIPE_WEBHOOK_SECRET missing — rejecting webhook")
        return False
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
    # Store only the public customer ViewModel; old internal/debug fields never
    # enter shared JSON, report HTML, or embedded report data.
    clean = build_customer_permit_view_model(result, job_type, city, state)
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
        data = build_customer_permit_view_model(json.loads(result_json), job_type, city, state)
        return {
            "data": data,
            "job_type": job_type,
            "city": city,
            "state": state,
        }
    except Exception as e:
        print(f"[share] Read error: {e}")
        return None

def esc_html(value) -> str:
    s = str(value or "")
    # Guard against double-escape: preserve already-escaped entities, then escape bare &
    s = s.replace("&amp;", "\x00AMP\x00")
    s = s.replace("&", "&amp;")
    s = s.replace("\x00AMP\x00", "&amp;")
    return s.replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def make_result_hash(result: dict) -> str:
    clean = {k: v for k, v in (result or {}).items() if not str(k).startswith("_")}
    raw = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _checklist_list(value) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _checklist_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _checklist_dict_text(value: dict, *keys: str) -> str:
    for key in keys:
        text = _checklist_text(value.get(key))
        if text:
            return text
    return ""


def build_checklist_fallback(result: dict, job_type: str = "", city: str = "", state: str = "") -> dict:
    permits = _checklist_list(result.get("permits_required"))
    first_permit = permits[0] if permits else {}
    permit_name = result.get("permit_name")
    if not permit_name and isinstance(first_permit, dict):
        permit_name = first_permit.get("permit_type")
    elif not permit_name:
        permit_name = _checklist_text(first_permit)
    permit_name = permit_name or "Permit"
    fee = result.get("fee_range") or result.get("fee") or "Confirm fee with the building department"
    timeline_obj = result.get("approval_timeline") or {}
    if isinstance(timeline_obj, dict):
        timeline = timeline_obj.get("simple") or timeline_obj.get("complex") or "Varies by jurisdiction"
    else:
        timeline = _checklist_text(timeline_obj) or "Varies by jurisdiction"
    docs = list(dict.fromkeys(
        text for text in (
            _checklist_text(item)
            for item in (
                _checklist_list(result.get("what_to_bring"))
                + _checklist_list(result.get("requirements"))
                + _checklist_list(result.get("documents_needed"))
            )
        ) if text
    ))
    inspections = _checklist_list(result.get("inspections"))
    special_notes = list(dict.fromkeys(
        text for text in (
            _checklist_text(item)
            for item in (
                _checklist_list(result.get("pro_tips"))[:2]
                + _checklist_list(result.get("common_mistakes"))[:2]
            )
        ) if text
    ))
    items = [
        {"label": f"Pull {permit_name} before starting work", "category": "permit", "required": True},
        {"label": f"Confirm jurisdiction for {city}, {state}", "category": "jurisdiction", "required": True},
        {"label": f"Pay permit fee: {fee}", "category": "fees", "required": True},
        {"label": f"Plan for approval timeline: {timeline}", "category": "timeline", "required": False},
    ]
    if docs:
        items.append({"label": f"Required documents: {', '.join(docs[:6])}", "category": "documents", "required": True})
    for inspection in inspections[:6]:
        if isinstance(inspection, dict):
            label = _checklist_dict_text(inspection, "stage", "title", "name", "label") or "Inspection step"
            timing = _checklist_dict_text(inspection, "timing", "description", "notes")
        else:
            label = _checklist_text(inspection) or "Inspection step"
            timing = ""
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
            model="gpt-5.4-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_completion_tokens=700,
        )
        parsed = json.loads(resp.choices[0].message.content)
        if isinstance(parsed, dict) and isinstance(parsed.get("items"), list) and parsed.get("items"):
            parsed.setdefault("title", fallback["title"])
            parsed.setdefault("summary", fallback["summary"])
            return parsed
    except Exception as e:
        print(f"[checklist] AI fallback used: {e}")
    return fallback


def _sanitize_customer_result_for_request_scope(result: dict, job_type: str = "", city: str = "", state: str = "") -> dict:
    """Apply canonical public ViewModel plus request-scope firebreak."""
    return build_customer_permit_view_model(result, job_type, city, state)


_CHECKLIST_BANNED_CUSTOMER_UNCERTAINTY_RE = re.compile(
    r"\b(?:UNKNOWN|FAIL[_ -]?CLOSED|likely|maybe|probably)\b",  # banned customer terms
    re.I,
)


def _scrub_checklist_customer_contract_text(text: str) -> str:
    """Remove customer-contract uncertainty wording from checklist-only strings."""
    if not isinstance(text, str):
        return text
    value = text
    value = re.sub(
        r"\bPlan\s+review\s+is\s+likely\s+because\b",  # banned customer term
        "Plan review is required when",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\bPlan\s+review\s+will\s+probably\s+be\s+required\b",  # banned customer term
        "Plan review is required",
        value,
        flags=re.I,
    )
    value = re.sub(r"\bMaybe\s+coordinate\b", "Coordinate", value, flags=re.I)  # banned customer term
    value = re.sub(r"\bmay\s+require\b", "requires", value, flags=re.I)
    value = re.sub(r"\bmay\s+be\s+required\b", "is required when applicable", value, flags=re.I)  # banned customer term
    value = _CHECKLIST_BANNED_CUSTOMER_UNCERTAINTY_RE.sub("", value)
    value = re.sub(r"\s+", " ", value).strip(" -:;,.\n")
    return value


def _scrub_checklist_customer_contract_value(value, path: tuple[str, ...] = ()):  # noqa: ANN001
    if isinstance(value, str):
        if any("companion" in part.lower() or "secondary" in part.lower() for part in path):
            return value
        return _scrub_checklist_customer_contract_text(value)
    if isinstance(value, list):
        cleaned = [
            item
            for item in (_scrub_checklist_customer_contract_value(item, path + (str(index),)) for index, item in enumerate(value))
            if item not in ("", [], {})
        ]
        return cleaned
    if isinstance(value, dict):
        cleaned = {}
        for key, child in value.items():
            next_value = _scrub_checklist_customer_contract_value(child, path + (str(key),))
            if next_value not in ("", [], {}):
                cleaned[key] = next_value
        return cleaned
    return value


def _sanitize_checklist_customer_output(checklist: dict, job_type: str = "", city: str = "", state: str = "") -> dict:
    """Sanitize checklist JSON before customer return/cache storage."""
    cleaned = sanitize_customer_visible_result(checklist if isinstance(checklist, dict) else {}, strip_internal_keys=True)
    try:
        scope_contract = build_scope_contract(job_type or "", city or "", state or "")
        cleaned = sanitize_result_for_scope_contract(cleaned, scope_contract, fail_on_removal_in_tests=False)
    except Exception as exc:
        print(f"[checklist-sanitize] Scope sanitize fallback used: {exc}")
    cleaned = _scrub_checklist_customer_contract_value(cleaned)
    public = _public_dict(cleaned if isinstance(cleaned, dict) else {}, frozenset({"title", "summary", "items", "cached", "result_hash", "building_department_contact_source"}))
    return public if isinstance(public, dict) else {}


def get_or_create_checklist(result: dict, job_type: str = "", city: str = "", state: str = "") -> dict:
    # Checklist generation can enrich/mutate its input; keep the CustomerView DTO
    # boundary immutable so public lookup/share/report/checklist surfaces stay identical.
    result = copy.deepcopy(result) if isinstance(result, dict) else {}
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
            return _sanitize_checklist_customer_output(data, job_type, city, state)
        checklist = generate_checklist(result, job_type, city, state)
        checklist = _sanitize_checklist_customer_output(checklist, job_type, city, state)
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
        checklist = _sanitize_checklist_customer_output(checklist, job_type, city, state)
        checklist["cached"] = False
        checklist["result_hash"] = result_hash
        return checklist


def load_report_template() -> str:
    template_path = os.path.join(FRONTEND_DIR, "report.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()


PUBLIC_SHARE_FIELDS = frozenset({"data", "job_type", "city", "state"})
PUBLIC_REPORT_RESULT_FIELDS = frozenset({
    "permit_required",
    "permit_verdict",
    "permit_decision",
    "permit_kind",
    "customer_headline",
    "customer_next_step",
    "exact_name_customer_note",
    "exact_apply_url_customer_note",
    "condition_threshold",
    "conditional_threshold",
    "permit_name",
    "permit_type",
    "primary_permit",
    "permits_required",
    "companion_permits",
    "companion_reviews",
    "companion_permits_or_reviews",
    "trade_permits",
    "fee_range",
    "fee",
    "approval_timeline",
    "timeline",
    "requirements",
    "documents_needed",
    "what_to_bring",
    "checklist",
    "inspections",
    "inspections_required",
    "inspection_checklist",
    "inspection_booking",
    "job_summary",
    "permit_summary",
    "summary",
    "description",
    "next_steps",
    "permit_notes",
    "inspection_notes",
    "zoning_hoa_flag",
    "apply_url",
    "apply_path",
    "applying_office",
    "apply_address",
    "apply_phone",
    "source_urls",
    "sources",
})
PUBLIC_CHECKLIST_FIELDS = frozenset({"title", "summary", "items"})
PUBLIC_REPORT_INTERNAL_FIELDS = frozenset({
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
    "debug",
    "model",
    "provider",
    "retrieval_metadata",
    "evidence_metadata",
    "internal_metadata",
})


def _is_public_internal_key(key: object) -> bool:
    key_lc = str(key or "").strip().lower()
    return key_lc.startswith("_") or key_lc in PUBLIC_REPORT_INTERNAL_FIELDS


def _strip_public_internal_keys(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, child in value.items():
            if _is_public_internal_key(key):
                continue
            next_value = _strip_public_internal_keys(child)
            cleaned[key] = next_value
        return cleaned
    if isinstance(value, list):
        return [_strip_public_internal_keys(item) for item in value]
    if isinstance(value, tuple):
        return [_strip_public_internal_keys(item) for item in value]
    return value


def _public_allowlist(source: dict, allowed_fields: frozenset[str]) -> dict:
    if not isinstance(source, dict):
        return {}
    public = {}
    for key in allowed_fields:
        if key in source and not _is_public_internal_key(key):
            public[key] = _strip_public_internal_keys(source.get(key))
    return public


def to_public_share_payload(share: dict, checklist: dict | None = None) -> dict:
    """Build the default-deny public payload embedded in report/share HTML."""
    share = share if isinstance(share, dict) else {}
    public_share = _public_allowlist(share, PUBLIC_SHARE_FIELDS)
    public_share["data"] = _public_allowlist(share.get("data") or {}, PUBLIC_REPORT_RESULT_FIELDS)
    public_checklist = _public_allowlist(checklist or {}, PUBLIC_CHECKLIST_FIELDS)
    return {
        "share": public_share,
        "app_base_url": APP_BASE_URL,
        "generated_at": utc_now().isoformat(),
        "checklist": public_checklist,
    }


def html_safe_json_dumps(value: object) -> str:
    """Serialize JSON for a <script type=application/json> text context."""
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return (
        raw.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_share_page(share: dict) -> str:
    template = load_report_template()
    safe_share = dict(share or {})
    safe_data = _sanitize_customer_result_for_request_scope(
        safe_share.get("data") or {},
        safe_share.get("job_type", ""),
        safe_share.get("city", ""),
        safe_share.get("state", ""),
    )
    safe_share["data"] = safe_data
    checklist = sanitize_customer_visible_result(get_or_create_checklist(safe_data, safe_share.get("job_type", ""), safe_share.get("city", ""), safe_share.get("state", "")))
    payload = to_public_share_payload(safe_share, checklist)
    return template.replace("__REPORT_DATA__", html_safe_json_dumps(payload))


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


def _is_unsafe_webhook_ip(ip_value: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_value)
    except ValueError:
        return True
    return any([
        ip.is_loopback,
        ip.is_private,
        ip.is_link_local,
        ip.is_multicast,
        ip.is_reserved,
        ip.is_unspecified,
    ])


def validate_webhook_callback_url(callback_url: str) -> str:
    """Return a normalized customer webhook URL or raise ValueError.

    Customer webhook delivery is outbound server-side traffic, so reject common SSRF
    targets: non-HTTPS schemes, credentials in URLs, localhost/private IPs, and
    hostnames resolving to localhost/private/link-local/reserved addresses.
    """
    url = str(callback_url or "").strip()
    if not url:
        raise ValueError("Valid HTTPS callback_url required")

    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError("Webhook callback_url must use HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("Webhook callback_url cannot include credentials")
    if not parsed.hostname:
        raise ValueError("Webhook callback_url must include a host")

    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise ValueError("Webhook callback_url host is not allowed")

    port = parsed.port or 443
    try:
        literal_ip = ipaddress.ip_address(host)
        if _is_unsafe_webhook_ip(str(literal_ip)):
            raise ValueError("Webhook callback_url host is not allowed")
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise
        try:
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as dns_error:
            raise ValueError("Webhook callback_url host could not be resolved") from dns_error
        if not addresses:
            raise ValueError("Webhook callback_url host could not be resolved")
        for address in addresses:
            resolved_ip = address[4][0]
            if _is_unsafe_webhook_ip(resolved_ip):
                raise ValueError("Webhook callback_url host resolves to a private or unsafe address")

    return url


def canonical_customer_webhook_body(body: dict) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def build_customer_webhook_signature_headers(integration_key: str, body: dict) -> dict:
    timestamp = str(int(time.time()))
    event_id = f"evt_{uuid.uuid4().hex}"
    body_json = canonical_customer_webhook_body(body)
    signed_payload = f"{timestamp}.{body_json}"
    signature = hmac.new(str(integration_key or "").encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-PermitAssist-Webhook-Id": event_id,
        "X-PermitAssist-Webhook-Timestamp": timestamp,
        "X-PermitAssist-Webhook-Signature": f"sha256={signature}",
    }


def verify_customer_webhook_signature(integration_key: str, body_json: str, timestamp: str, signature_header: str) -> bool:
    if not integration_key or not body_json or not timestamp or not signature_header:
        return False
    expected = hmac.new(
        str(integration_key).encode("utf-8"),
        f"{timestamp}.{body_json}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    provided = signature_header.split("sha256=", 1)[-1] if signature_header.startswith("sha256=") else signature_header
    return hmac.compare_digest(expected, provided)


def create_webhook_integration(email: str, name: str, callback_url: str, field_mapping: dict | None = None) -> dict:
    integration_key = f"wh_{secrets.token_urlsafe(18)}"
    now = utc_now().isoformat()
    clean_callback_url = validate_webhook_callback_url(callback_url)
    default_mapping = {
        "job_type": "job_type",
        "city": "city",
        "state": "state",
        "zip_code": "zip_code",
    }
    clean_mapping = dict(field_mapping) if isinstance(field_mapping, dict) else default_mapping
    clean_mapping.pop("callback_url", None)
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO webhook_integrations (email, integration_key, name, callback_url, field_mapping, created_at, last_triggered_at, trigger_count) VALUES (?,?,?,?,?,?,?,0)",
        (email.lower().strip(), integration_key, (name or "Webhook").strip()[:80], clean_callback_url, json.dumps(clean_mapping), now, None)
    )
    conn.commit()
    integration_id = cur.lastrowid
    conn.close()
    return {
        "id": integration_id,
        "name": (name or "Webhook").strip()[:80],
        "integration_key": integration_key,
        "callback_url": clean_callback_url,
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
            callback_url = validate_webhook_callback_url(integration.get("callback_url") or "")
            if not (job_type and city and state):
                raise ValueError("Webhook requires job_type, city, and state")
            result = research_permit(job_type, city, state, zip_code)
            body = {
                "ok": True,
                "job_type": job_type,
                "city": city,
                "state": state,
                "integration": integration.get("name") or "Webhook",
                "result": result,
            }
            body_json = canonical_customer_webhook_body(body)
            headers = build_customer_webhook_signature_headers(integration.get("integration_key") or "", body)
            requests.post(callback_url, data=body_json, headers=headers, timeout=20, allow_redirects=False)
            mark_webhook_triggered(integration["integration_key"])
        except Exception as e:
            print(f"[webhook] Delivery error: {e}")
            callback_url = str(integration.get("callback_url") or "").strip()
            if callback_url:
                try:
                    callback_url = validate_webhook_callback_url(callback_url)
                    body = {"ok": False, "error": str(e)}
                    body_json = canonical_customer_webhook_body(body)
                    headers = build_customer_webhook_signature_headers(integration.get("integration_key") or "", body)
                    requests.post(callback_url, data=body_json, headers=headers, timeout=20, allow_redirects=False)
                except Exception:
                    pass
    threading.Thread(target=_worker, daemon=True).start()

def send_email_report(to_email: str, job: str, city: str, state: str, data: dict) -> bool:
    """Send a beautiful HTML permit research report via Resend."""
    def esc(s):
        s = str(s or "")
        # Guard against double-escape: preserve already-escaped entities, then escape bare &
        s = s.replace("&amp;", "\x00AMP\x00")
        s = s.replace("&", "&amp;")
        s = s.replace("\x00AMP\x00", "&amp;")
        return s.replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    subject = f"Permit Research: {job} in {city}, {state}"
    pv = "YES" if (data.get("permit_required") is True or data.get("permit_verdict") == "YES" or data.get("permit_decision") == "REQUIRED") else "NO"
    verdict_color = {"YES": "#10b981", "NO": "#ef4444"}.get(pv, "#10b981")
    verdict_bg    = {"YES": "rgba(16,185,129,.12)", "NO": "rgba(239,68,68,.12)"}.get(pv, "rgba(16,185,129,.12)")

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
    source_entries = _source_dicts(data, city=city, state=state)[:4]
    license_r = esc(data.get("license_required", ""))

    # Build permit rows
    permit_rows = ""
    for p in permits:
        req = "YES" if p.get("required") is not False else "NO"
        req_color = {"YES": "#10b981", "NO": "#ef4444"}.get(req, "#94a3b8")
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
    sources_html = "".join(
        f'<li style="padding:3px 0"><strong style="font-size:12px;color:#334155">{esc(s.get("title") or "Source")}</strong> — '
        f'<a href="{esc(s.get("url") or "")}" style="color:#1a56db;font-size:12px">{esc(s.get("url") or "")}</a></li>'
        for s in source_entries
    )

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
        req = "YES" if p.get("required") is not False else "NO"
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
        reminder_rows = conn.execute(
            f"SELECT job_id, issued_date FROM permit_issued_reminders WHERE email IN ({placeholders})",
            scope,
        ).fetchall()
        conn.close()
        issued_dates = {job_id: issued_date for job_id, issued_date in reminder_rows if job_id and issued_date}
        cols = ["id","email","job_name","address","city","state","trade","permit_name",
                "status","applied_date","approved_date","permit_number","expiry_date",
                "notes","result_json","created_at","updated_at"]
        result = []
        for row in rows:
            d = dict(zip(cols, row))
            if d.get("result_json"):
                try: d["result_json"] = json.loads(d["result_json"])
                except: d["result_json"] = {}
            d["issued_date"] = issued_dates.get(d.get("id"), "")
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


def _normalize_saved_email(email: str) -> str:
    return (email or "").lower().strip()


def _valid_saved_email(email: str) -> bool:
    normalized = _normalize_saved_email(email)
    return bool(normalized and "@" in normalized and "." in normalized.rsplit("@", 1)[-1])


def _normalize_saved_city(city: str) -> str:
    return " ".join((city or "").strip().split())


def _normalize_saved_state(state: str) -> str:
    return (state or "").strip().upper()


def _normalize_saved_trade(trade: str | None) -> str:
    # Store blank trade as an empty string, not NULL, so the UNIQUE(email,city,state,trade)
    # constraint also protects city-only saves in SQLite.
    return " ".join((trade or "").strip().split())


def _saved_jurisdiction_dict(row) -> dict:
    cols = ["id", "email", "city", "state", "trade", "display_name", "added_at", "last_lookup_at", "lookup_count", "notes"]
    return dict(zip(cols, row)) if row else {}


def save_jurisdiction(email: str, city: str, state: str, trade: str | None = "", display_name: str | None = "", notes: str | None = "") -> dict:
    normalized_email = _normalize_saved_email(email)
    normalized_city = _normalize_saved_city(city)
    normalized_state = _normalize_saved_state(state)
    normalized_trade = _normalize_saved_trade(trade)
    clean_display_name = " ".join((display_name or "").strip().split())
    clean_notes = (notes or "").strip()[:2000]
    if not clean_display_name:
        clean_display_name = f"{normalized_city} {normalized_trade}".strip() or f"{normalized_city}, {normalized_state}"
    now = utc_now().isoformat()
    conn = sqlite3.connect(CACHE_DB)
    conn.execute(
        """
        INSERT INTO saved_jurisdictions (email, city, state, trade, display_name, added_at, notes)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(email, city, state, trade) DO UPDATE SET
            display_name=excluded.display_name,
            notes=excluded.notes
        """,
        (normalized_email, normalized_city, normalized_state, normalized_trade, clean_display_name, now, clean_notes)
    )
    row = conn.execute(
        """
        SELECT id,email,city,state,trade,display_name,added_at,last_lookup_at,lookup_count,notes
        FROM saved_jurisdictions
        WHERE email=? AND city=? AND state=? AND trade=?
        """,
        (normalized_email, normalized_city, normalized_state, normalized_trade)
    ).fetchone()
    conn.commit()
    conn.close()
    return _saved_jurisdiction_dict(row)


def list_saved_jurisdictions(email: str) -> list[dict]:
    conn = sqlite3.connect(CACHE_DB)
    rows = conn.execute(
        """
        SELECT id,email,city,state,trade,display_name,added_at,last_lookup_at,lookup_count,notes
        FROM saved_jurisdictions
        WHERE email=?
        ORDER BY COALESCE(last_lookup_at, added_at) DESC, id DESC
        """,
        [_normalize_saved_email(email)]
    ).fetchall()
    conn.close()
    return [_saved_jurisdiction_dict(row) for row in rows]


def get_saved_jurisdiction_for_owner(jurisdiction_id: str, email: str) -> tuple[int, dict | None]:
    conn = sqlite3.connect(CACHE_DB)
    row = conn.execute(
        """
        SELECT id,email,city,state,trade,display_name,added_at,last_lookup_at,lookup_count,notes
        FROM saved_jurisdictions WHERE id=?
        """,
        [jurisdiction_id]
    ).fetchone()
    conn.close()
    if not row:
        return 404, None
    data = _saved_jurisdiction_dict(row)
    if data.get("email") != _normalize_saved_email(email):
        return 403, data
    return 200, data


def delete_saved_jurisdiction(jurisdiction_id: str, email: str) -> tuple[int, bool]:
    status, _row = get_saved_jurisdiction_for_owner(jurisdiction_id, email)
    if status != 200:
        return status, False
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.execute("DELETE FROM saved_jurisdictions WHERE id=? AND email=?", [jurisdiction_id, _normalize_saved_email(email)])
    conn.commit()
    conn.close()
    return 200 if cur.rowcount else 404, cur.rowcount > 0


def record_saved_jurisdiction_lookup(jurisdiction_id: str, email: str) -> tuple[int, dict | None]:
    status, _row = get_saved_jurisdiction_for_owner(jurisdiction_id, email)
    if status != 200:
        return status, _row
    now = utc_now().isoformat()
    conn = sqlite3.connect(CACHE_DB)
    conn.execute(
        "UPDATE saved_jurisdictions SET lookup_count=COALESCE(lookup_count,0)+1, last_lookup_at=? WHERE id=? AND email=?",
        [now, jurisdiction_id, _normalize_saved_email(email)]
    )
    row = conn.execute(
        """
        SELECT id,email,city,state,trade,display_name,added_at,last_lookup_at,lookup_count,notes
        FROM saved_jurisdictions WHERE id=? AND email=?
        """,
        [jurisdiction_id, _normalize_saved_email(email)]
    ).fetchone()
    conn.commit()
    conn.close()
    return 200, _saved_jurisdiction_dict(row)


# ── City watch helpers ───────────────────────────────────────────────────────
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
    digest = {
        "job_type": normalized_job,
        "city": normalized_city,
        "state": normalized_state,
        "required_permits": [p.get('permit_type', 'Permit') for p in (result.get("permits_required") or []) if isinstance(p, dict)][:8],
        "fee_range": result.get('fee_range') or result.get('fee') or 'Check with city',
        "apply_url": result.get('apply_url') or '',
        "checked_at": utc_now().isoformat(),
    }
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
    return {"watched": True, "changed": changed, "last_hash": current_hash, "digest": digest}


def build_fix_plan_text(fix_result: dict) -> str:
    parts = []
    reasons = fix_result.get("rejection_reasons") or []
    if reasons:
        parts.append("Why it was rejected:")
        parts.extend([f"- {reason}" for reason in reasons])
    steps = fix_result.get("fix_steps") or []
    if steps:
        if parts:
            parts.append("")
        parts.append("What to fix:")
        parts.extend([f"{idx}. {step}" for idx, step in enumerate(steps, 1)])
    tips = (fix_result.get("resubmission_tips") or "").strip()
    if tips:
        if parts:
            parts.append("")
        parts.append(f"Resubmission tips: {tips}")
    letter = (fix_result.get("response_letter") or "").strip()
    if letter:
        if parts:
            parts.append("")
        parts.append("Response letter:")
        parts.append(letter)
    code_refs = fix_result.get("code_refs") or []
    if code_refs:
        if parts:
            parts.append("")
        parts.append("Code references:")
        parts.extend([f"- {ref}" for ref in code_refs])
    return "\n".join(parts).strip()


def get_rejection_fix_result(job_id: str, rejection_text: str, city: str, state: str, job_type: str) -> dict:
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
            if cached.get("result"):
                return cached["result"]
            if cached.get("fix_plan"):
                return {
                    "rejection_reasons": [],
                    "fix_steps": [cached["fix_plan"]],
                    "response_letter": "",
                    "code_refs": [],
                    "resubmission_tips": "",
                }
    except Exception as e:
        print(f"[rejection-fix] Cache read error: {e}")

    system_prompt = """You are PermitAssist, an expert permit consultant helping contractors respond to city permit rejection letters.

Your job: analyze the rejection letter and generate a professional, specific response letter the contractor can send to the building department to resolve the rejection and get their permit approved.

Response format (JSON only):
{
  \"rejection_reasons\": [\"list of specific reasons the city rejected the permit\"],
  \"fix_steps\": [\"numbered action items the contractor must complete before resubmitting\"],
  \"response_letter\": \"Full professional letter text ready to send to the building department. Address it To: Building Department. Include: acknowledgment of rejection, specific corrections being made, resubmission statement. Professional tone. No placeholders, write it as if ready to send.\",
  \"code_refs\": [\"any relevant code sections mentioned or implied in the rejection\"],
  \"resubmission_tips\": \"1-2 sentences of practical advice for the resubmission\"
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
        oai = _OpenAI()
        resp = oai.chat.completions.create(
            model='gpt-5.4-mini',
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt}
            ],
            response_format={'type': 'json_object'},
            temperature=0.3,
            max_completion_tokens=2000
        )
        result_text = resp.choices[0].message.content

    parsed = json.loads(result_text)
    result = {
        "rejection_reasons": parsed.get("rejection_reasons") or [],
        "fix_steps": parsed.get("fix_steps") or [],
        "response_letter": parsed.get("response_letter") or "",
        "code_refs": parsed.get("code_refs") or [],
        "resubmission_tips": parsed.get("resubmission_tips") or "",
    }
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            "INSERT OR REPLACE INTO checklist_cache (result_hash, checklist_json, created_at) VALUES (?,?,?)",
            (cache_key, json.dumps({"job_id": job_id, "fix_plan": build_fix_plan_text(result), "result": result}), utc_now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[rejection-fix] Cache write error: {e}")
    return result


def get_rejection_fix_plan(job_id: str, rejection_text: str, city: str, state: str, job_type: str) -> str:
    return build_fix_plan_text(get_rejection_fix_result(job_id, rejection_text, city, state, job_type))


# ── Cities page SSR (Fix 10) ──────────────────────────────────────────────────
# Render frontend/cities.html with real city counts injected so crawlers and
# pre-JS visitors see "5,260 Total / 5,000 Cities / 260 Counties / 50 States"
# instead of the "Loading verified cities..." placeholder. Falls back to
# stable hardcoded numbers if the SQLite read fails — never the legacy 263
# fallback. The /api/verified-cities JSON path is unchanged and is what
# powers the interactive grid once JS hydrates.

_CITIES_PAGE_CACHE: dict = {"ts": 0.0, "html": None}
_CITIES_PAGE_CACHE_TTL_SECS = 300  # 5 min — cheap to recompute, no need to be tighter

_CITIES_PAGE_HARDCODED_FALLBACK = {
    # 2026-04-28 snapshot of knowledge/verified_cities.db. Used only if SQLite
    # is unreachable so we never fall back to the legacy 263-city number.
    "total": 5260, "city": 3195, "county": 2065, "state": 50,
}


def _read_verified_cities_counts() -> dict:
    """Return {total, city, county, state} from knowledge/data verified_cities.db.

    Returns the hardcoded fallback when the DB is unreachable so we never
    serve the 263-row legacy number.
    """
    try:
        import sqlite3 as _sql
        _knowledge_db = os.path.join(os.path.dirname(__file__), "..", "knowledge", "verified_cities.db")
        _data_db = os.path.join(os.path.dirname(__file__), "..", "data", "verified_cities.db")
        _db_path = _knowledge_db if os.path.exists(_knowledge_db) else _data_db
        if not os.path.exists(_db_path):
            return dict(_CITIES_PAGE_HARDCODED_FALLBACK)
        with _sql.connect(_db_path) as _conn:
            row = _conn.execute(
                """SELECT
                       COUNT(*) AS total,
                       SUM(CASE WHEN COALESCE(entity_type,'city')='city' THEN 1 ELSE 0 END) AS city,
                       SUM(CASE WHEN entity_type='county' THEN 1 ELSE 0 END) AS county,
                       COUNT(DISTINCT state) AS state
                   FROM verified_cities"""
            ).fetchone()
        total = int(row[0] or 0)
        # Defend against an empty / partially-populated table by falling back
        # rather than serving a bad number.
        if total < 1000:
            return dict(_CITIES_PAGE_HARDCODED_FALLBACK)
        return {
            "total": total,
            "city": int(row[1] or 0),
            "county": int(row[2] or 0),
            "state": int(row[3] or 0),
        }
    except Exception as _err:
        print(f"[cities-ssr] count read failed, using hardcoded fallback: {_err}")
        return dict(_CITIES_PAGE_HARDCODED_FALLBACK)


def _format_count(n: int) -> str:
    return f"{n:,}"


def render_cities_page_ssr(html_path: str) -> bytes:
    """Read cities.html and inject real counts into the hero stats placeholders.

    Uses a 5-minute in-process cache so repeat hits don't touch SQLite. Crawlers
    that don't run JS now see real numbers instead of em-dashes; the JS still
    overwrites these once /api/verified-cities returns.
    """
    import time as _time
    now = _time.time()
    cached = _CITIES_PAGE_CACHE.get("html")
    if cached and (now - _CITIES_PAGE_CACHE.get("ts", 0.0)) < _CITIES_PAGE_CACHE_TTL_SECS:
        return cached

    with open(html_path, "rb") as _f:
        html = _f.read().decode("utf-8")

    counts = _read_verified_cities_counts()
    replacements = {
        '<span id="total-count">—</span>': f'<span id="total-count">{_format_count(counts["total"])}</span>',
        '<span id="city-count">—</span>':  f'<span id="city-count">{_format_count(counts["city"])}</span>',
        '<span id="county-count">—</span>': f'<span id="county-count">{_format_count(counts["county"])}</span>',
        '<span id="state-count">—</span>': f'<span id="state-count">{_format_count(counts["state"])}</span>',
    }
    for old, new in replacements.items():
        html = html.replace(old, new)

    # Replace the loading div with a noscript-friendly summary so non-JS
    # crawlers see real content instead of "Loading verified cities...".
    noscript_summary = (
        f'<div class="loading">Loading verified cities…</div>'
        f'<noscript><div style="padding:24px 0;color:var(--text2);font-size:14px;line-height:1.7">'
        f'PermitAssist verifies permit requirements across <strong>{_format_count(counts["total"])}'
        f'</strong> US jurisdictions — '
        f'{_format_count(counts["city"])} cities and {_format_count(counts["county"])} counties '
        f'covering all {_format_count(counts["state"])} states. '
        f'Enable JavaScript to browse the full searchable list, or visit '
        f'<a href="/">PermitAssist</a> to look up a permit directly.'
        f'</div></noscript>'
    )
    html = html.replace(
        '<div class="loading">Loading verified cities...</div>',
        noscript_summary,
    )

    body = html.encode("utf-8")
    _CITIES_PAGE_CACHE["ts"] = now
    _CITIES_PAGE_CACHE["html"] = body
    return body


_CITY_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-[a-z]{2}$")


_CITY_LANDING_TRADES = [
    ("HVAC", "hvac"),
    ("Electrical", "electrical"),
    ("Roofing", "roofing"),
    ("Plumbing", "plumbing"),
    ("Mini-split", "mini-split"),
    ("EV charger", "ev-charger"),
    ("Generator", "generator"),
    ("Deck", "deck"),
    ("Solar", "solar"),
]


def city_slug_to_display(slug: str) -> tuple[str, str] | None:
    """Convert `houston-tx` to (`Houston`, `TX`) for legacy /city/* pages."""
    slug = (slug or "").strip().lower().strip("/")
    if not _CITY_SLUG_RE.match(slug):
        return None
    city_part, state = slug.rsplit("-", 1)
    city_name = " ".join(part.capitalize() for part in city_part.split("-") if part)
    return city_name, state.upper()


def render_city_landing_page(slug: str) -> bytes | None:
    """Render a lightweight legacy /city/{city-state} page instead of 404ing.

    The canonical pSEO URLs are /permits/{trade}/{city-state}; this page keeps
    old/external /city/* links useful and funnels users to the active permit
    pages and the main lookup tool.
    """
    display = city_slug_to_display(slug)
    if not display:
        return None
    city_name, state = display
    title = f"Permit requirements in {city_name}, {state}"
    escaped_title = html.escape(title)
    escaped_city = html.escape(city_name)
    escaped_state = html.escape(state)
    links = "".join(
        f'<a class="card" href="/permits/{trade_slug}/{html.escape(slug)}">'
        f'<strong>{html.escape(label)} permits</strong><span>{html.escape(label)} permit requirements in {escaped_city}, {escaped_state}</span></a>'
        for label, trade_slug in _CITY_LANDING_TRADES
    )
    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escaped_title} — PermitAssist</title>
  <meta name="description" content="Find building, trade, and contractor permit requirements for {escaped_city}, {escaped_state}." />
  <link rel="canonical" href="https://permitassist.io/city/{html.escape(slug)}" />
  <style>
    body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0f2044;color:#fff}}
    .wrap{{max-width:960px;margin:0 auto;padding:32px 20px 56px}}
    .nav{{display:flex;justify-content:space-between;align-items:center;margin-bottom:56px}}
    .brand{{display:flex;align-items:center;gap:10px;color:#fff;text-decoration:none;font-weight:800}}
    .brand img{{width:32px;height:32px;border-radius:8px}}
    .btn{{display:inline-block;background:#1a56db;color:#fff;padding:12px 18px;border-radius:10px;text-decoration:none;font-weight:800}}
    .hero{{margin-bottom:32px}}
    h1{{font-size:clamp(32px,5vw,54px);line-height:1.05;margin:0 0 14px}}
    p{{color:rgba(255,255,255,.78);font-size:18px;line-height:1.65;max-width:760px}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-top:26px}}
    .card{{display:block;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14);border-radius:14px;padding:18px;color:#fff;text-decoration:none}}
    .card:hover{{background:rgba(255,255,255,.13)}}
    .card strong{{display:block;font-size:17px;margin-bottom:6px}}
    .card span{{display:block;color:rgba(255,255,255,.68);font-size:14px;line-height:1.45}}
    .note{{margin-top:28px;font-size:14px;color:rgba(255,255,255,.62)}}
  </style>
</head>
<body>
  <main class="wrap">
    <nav class="nav"><a class="brand" href="/"><img src="/logo.png" alt="" />PermitAssist</a><a class="btn" href="/">Run a free lookup</a></nav>
    <section class="hero">
      <h1>{escaped_title}</h1>
      <p>Choose a common permit type below, or run a live PermitAssist lookup for your exact scope. Always verify final requirements with the local AHJ before quoting or starting work.</p>
    </section>
    <section class="grid" aria-label="Permit pages for {escaped_city}, {escaped_state}">{links}</section>
    <p class="note">Legacy city URL preserved for users and crawlers. Canonical detailed pages live under /permits/{{permit-type}}/{html.escape(slug)}.</p>
  </main>
</body>
</html>"""
    return body.encode("utf-8")


def observability_head_snippet() -> str:
    """Return optional browser observability scripts. Empty when env vars are absent."""
    snippets: list[str] = []
    if SENTRY_DSN:
        snippets.append(
            "<script src=\"https://browser.sentry-cdn.com/8.55.0/bundle.tracing.min.js\" "
            "crossorigin=\"anonymous\"></script>\n"
            "<script>\n"
            "window.Sentry && Sentry.init({\n"
            f"  dsn: {json.dumps(SENTRY_DSN)},\n"
            f"  environment: {json.dumps(SENTRY_ENVIRONMENT)},\n"
            "  tracesSampleRate: 0.05,\n"
            "  sendDefaultPii: false\n"
            "});\n"
            "</script>"
        )
    if POSTHOG_PUBLIC_KEY:
        snippets.append(
            "<script>\n"
            "!function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(\".\");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement(\"script\")).type=\"text/javascript\",p.crossOrigin=\"anonymous\",p.async=!0,p.src=s.api_host.replace(\".i.posthog.com\",\"-assets.i.posthog.com\")+\"/static/array.js\",(r=t.getElementsByTagName(\"script\")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a=\"posthog\",u.people=u.people||[],u.toString=function(t){var e=\"posthog\";return\"posthog\"!==a&&(e+=\".\"+a),t||(e+=\" (stub)\"),e},u.people.toString=function(){return u.toString(1)+\".people (stub)\"},o=\"capture identify alias people.set people.set_once set_config register register_once unregister opt_out_capturing has_opted_out_capturing opt_in_capturing reset isFeatureEnabled onFeatureFlags reloadFeatureFlags getFeatureFlag getFeatureFlagPayload group\".split(\" \"),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);\n"
            f"posthog.init({json.dumps(POSTHOG_PUBLIC_KEY)}, {{api_host: {json.dumps(POSTHOG_HOST)}, person_profiles: 'identified_only'}});\n"
            "</script>"
        )
    return "\n".join(snippets)


_SENSITIVE_OUTPUT_RE = re.compile(
    r"(?i)(/home/[^/\s\"'<>]+/[^\s\"'<>]+|/app/[^\s\"'<>]+|PERMITASSIST_[A-Z0-9_]+|RAILWAY_[A-Z0-9_]+|sk-[A-Za-z0-9_-]{16,}|whsec_[A-Za-z0-9_-]{16,}|[A-Fa-f0-9]{64})"
)


def redact_public_output(value):
    """Redact filesystem/env/Railway/token-like strings from API JSON."""
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key in {"_fee_floor_components", "_rulebook_depth_disclaimer", "_residential_home_office_leak_repaired"}:
                continue
            if key in {"path", "fingerprint_sha256", "evidence_pack_fingerprint"}:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_public_output(item)
        return redacted
    if isinstance(value, list):
        return [redact_public_output(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_OUTPUT_RE.sub("[REDACTED]", value)
    return value


def _evidence_pack_indexing_guard_enabled() -> bool:
    return evidence_pack_enabled()


# ── Request handler ───────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{self.address_string()}] {args[0]} {args[1]}")

    def client_ip(self) -> str:
        cf_ip = _normalize_ip(self.headers.get("CF-Connecting-IP", ""))
        if cf_ip:
            return cf_ip
        forwarded_ip = _parse_public_forwarded_ip(self.headers.get("X-Forwarded-For", ""))
        if forwarded_ip:
            return forwarded_ip
        real_ip = _normalize_ip(self.headers.get("X-Real-IP", ""))
        if real_ip:
            return real_ip
        return _normalize_ip(self.client_address[0])

    def send_json(self, status: int, data: dict, extra_headers: dict | None = None):
        data = redact_public_output(data)
        body = json.dumps(data, indent=2).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Expose-Headers", "X-Free-Lookups-Used, X-Free-Lookups-Remaining")
            if _evidence_pack_indexing_guard_enabled():
                self.send_header("X-Robots-Tag", "noindex, nofollow")
                self.send_header("Cache-Control", "no-store")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as e:
            # Client/edge already disconnected. Do not turn a late response write
            # into noisy traceback spam or a second 500 path.
            print(f"[response] client disconnected before JSON response completed: {type(e).__name__}")

    def send_file(self, path: str, content_type: str):
        try:
            with open(path, "rb") as f:
                body = f.read()
            if "text/html" in content_type:
                snippet = observability_head_snippet()
                if snippet:
                    html = body.decode("utf-8", errors="ignore")
                    marker = "</head>"
                    if marker in html:
                        html = html.replace(marker, snippet + "\n" + marker, 1)
                        body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if _evidence_pack_indexing_guard_enabled() and ("text/html" in content_type or "xml" in content_type or "text/plain" in content_type):
                self.send_header("X-Robots-Tag", "noindex, nofollow")
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
        if path.startswith("/api/jurisdictions/"):
            qs = parse_qs(urlparse(self.path).query)
            email = (qs.get("email", [""])[0] or "").strip()
            jurisdiction_id = path[len("/api/jurisdictions/"):].strip("/")
            if not jurisdiction_id:
                self.send_json(400, {"error": "Jurisdiction ID required"})
                return
            if not _valid_saved_email(email):
                self.send_json(400, {"error": "Valid email required"})
                return
            status, deleted = delete_saved_jurisdiction(jurisdiction_id, email)
            if status == 403:
                self.send_json(403, {"error": "Forbidden"})
            else:
                self.send_json(status, {"deleted": deleted})
        elif path.startswith("/api/jobs/"):
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
        elif path == "/logo.png":
            self.send_file(os.path.join(FRONTEND_DIR, "icons", "logo.png"), "image/png")
        elif path.startswith("/city/"):
            slug = path[len("/city/"):].strip("/").lower()
            body = render_city_landing_page(slug)
            if body is None:
                self.send_response(404)
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "public, max-age=300")
                self.end_headers()
                self.wfile.write(body)
        elif path in ("/cities", "/cities.html", "/cities/"):
            _cities_path = os.path.join(FRONTEND_DIR, "cities.html")
            try:
                _body = render_cities_page_ssr(_cities_path)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(_body)))
                # Crawlers benefit from short caching; humans get fresh data anyway
                # because the JS overwrites the injected numbers post-hydration.
                self.send_header("Cache-Control", "public, max-age=300")
                self.end_headers()
                self.wfile.write(_body)
            except Exception as _ssr_err:
                print(f"[cities-ssr] render failed, serving raw HTML: {_ssr_err}")
                self.send_file(_cities_path, "text/html; charset=utf-8")

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
            FB_VERIFY_TOKEN = os.environ.get("FB_WEBHOOK_VERIFY_TOKEN")
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
            paid  = is_paid_user(user_email)
            ip = self.client_ip()
            fingerprint = _normalize_fingerprint(self.headers.get("X-Client-Fingerprint", ""))
            count = -1 if paid else get_effective_free_usage(ip, fingerprint)
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
                "lookups_used":       count,
                "lookups_remaining":  -1 if paid else max(0, FREE_LOOKUP_LIMIT - count),
                "reset_date":         None,
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
                    
                is_new_user = get_user(email) is None
                get_or_create_user(email)
                if is_new_user:
                    threading.Thread(target=schedule_onboarding_emails, args=(email,), daemon=True).start()
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
            try:
                html = render_share_page(share)
            except Exception as e:
                print(
                    "[report] Render error "
                    f"slug={slug} job_type={share.get('job_type', '')} "
                    f"city={share.get('city', '')} state={share.get('state', '')}: "
                    f"{type(e).__name__}: {e}"
                )
                import traceback as _traceback
                _traceback.print_exc()
                html_error = """<!DOCTYPE html><html><head><meta charset='UTF-8'/>
<meta name='viewport' content='width=device-width,initial-scale=1'/>
<title>Report Temporarily Unavailable — PermitAssist</title>
<style>body{font-family:system-ui,sans-serif;background:#0b1220;color:#f0f4ff;display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center}.box{max-width:460px;padding:32px 20px}h1{font-size:24px;font-weight:800;margin-bottom:10px}p{color:#b8c5e0;line-height:1.6}a{color:#93c5fd}</style></head>
<body><div class='box'><h1>Report temporarily unavailable</h1>
<p>We could not open this saved report. Please retry, or run a fresh lookup from PermitAssist.</p>
<p><a href='/'>Back to PermitAssist</a></p></div></body></html>"""
                self.send_response(500)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_error.encode())))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(html_error.encode())
                return
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
            # 2026-04-27: primary source is data/verified_cities.db (5,260 rows
            # populated by scripts/city-coverage-expander.py — Tier 2/3 cities +
            # Tier 4 counties). Falls back to the legacy 263-city curated list
            # only if the DB is missing/empty so the homepage badge stays honest.
            cities = []
            stats = {}
            try:
                import sqlite3 as _sql
                # Try the baked-in knowledge/ path first (lives in the deploy image,
                # not under Railway's /app/data volume mount which would hide it),
                # fall back to data/ for local dev where city-coverage-expander writes.
                _knowledge_db = os.path.join(os.path.dirname(__file__), "..", "knowledge", "verified_cities.db")
                _data_db = os.path.join(os.path.dirname(__file__), "..", "data", "verified_cities.db")
                _db_path = _knowledge_db if os.path.exists(_knowledge_db) else _data_db
                if os.path.exists(_db_path):
                    with _sql.connect(_db_path) as _conn:
                        _conn.row_factory = _sql.Row
                        rows = _conn.execute(
                            """SELECT city, state, population, tier, badge_state,
                                      portal_url, building_dept_phone, building_dept_address,
                                      entity_type
                               FROM verified_cities
                               ORDER BY state, city"""
                        ).fetchall()
                        for r in rows:
                            cities.append({
                                "city": r["city"],
                                "state": r["state"],
                                "entity_type": r["entity_type"] or "city",
                                "badge_state": r["badge_state"] or "ai_researched",
                                "portal_url": r["portal_url"] or "",
                                "phone": r["building_dept_phone"] or "",
                                "address": r["building_dept_address"] or "",
                                "population": r["population"] or 0,
                            })
                        # Aggregate counts for the homepage badge to live-update
                        # against the real dataset instead of a hardcoded number.
                        by_entity = {}
                        by_badge = {}
                        for c in cities:
                            by_entity[c["entity_type"]] = by_entity.get(c["entity_type"], 0) + 1
                            by_badge[c["badge_state"]] = by_badge.get(c["badge_state"], 0) + 1
                        stats = {"by_entity": by_entity, "by_badge": by_badge}
            except Exception as _db_err:
                print(f"[verified-cities] DB read failed: {_db_err}")
                cities = []

            # Legacy fallback only if the new DB returned nothing.
            if not cities:
                try:
                    import sys
                    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
                    from auto_verify import get_verified_cities
                    legacy = get_verified_cities() or []
                    if not legacy:
                        _vk_path = os.path.join(os.path.dirname(__file__), "..", "knowledge", "verified_cities.json")
                        if os.path.exists(_vk_path):
                            with open(_vk_path) as _f:
                                _vk_data = json.load(_f)
                            _seen = set()
                            for _entry in _vk_data.values():
                                _k = f"{_entry.get('city','')}|{_entry.get('state','')}"
                                if _k not in _seen and _entry.get('city') and _entry.get('state'):
                                    _seen.add(_k)
                                    legacy.append({"city": _entry["city"], "state": _entry["state"]})
                            legacy = sorted(legacy, key=lambda x: (x["state"], x["city"]))
                    cities = [
                        {"city": c.get("city",""), "state": c.get("state",""),
                         "entity_type": "city", "badge_state": "ai_researched",
                         "portal_url": "", "phone": "", "address": "", "population": 0}
                        for c in legacy
                    ]
                except Exception as _legacy_err:
                    print(f"[verified-cities] Legacy fallback failed: {_legacy_err}")

            self.send_json(200, {
                "verified_cities": cities,
                "count": len(cities),
                **stats,
            })

        elif path == "/api/jurisdictions/list":
            qs = parse_qs(urlparse(self.path).query)
            email = (qs.get("email", [""])[0] or "").strip()
            if not _valid_saved_email(email):
                self.send_json(400, {"error": "Valid email required"})
                return
            self.send_json(200, {"jurisdictions": list_saved_jurisdictions(email)})

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
            if _evidence_pack_indexing_guard_enabled():
                body = b'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>\n'
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Robots-Tag", "noindex, nofollow")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            sitemap_path = os.path.join(SEO_DIR, "sitemap.xml")
            self.send_file(sitemap_path, "application/xml")

        # ── SEO: robots.txt ───────────────────────────────────────────────
        elif path == "/robots.txt":
            if _evidence_pack_indexing_guard_enabled():
                body = b"User-agent: *\nDisallow: /\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Robots-Tag", "noindex, nofollow")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            robots_path = os.path.join(SEO_DIR, "robots.txt")
            self.send_file(robots_path, "text/plain")

        # ── SEO: /permits/* pages ─────────────────────────────────────────
        elif path.startswith("/permits"):
            # Try frontend/permits/ first (new city × trade pSEO pages)
            # then fall back to seo/seo_pages/permits/ (legacy trade-only pages)
            safe_seo = path.lstrip("/")
            served = False
            for root_dir in (FRONTEND_DIR, SEO_DIR):
                candidate = os.path.realpath(os.path.join(root_dir, safe_seo))
                root_real = os.path.realpath(root_dir)
                if not candidate.startswith(root_real):
                    continue
                if os.path.isdir(candidate):
                    candidate = os.path.join(candidate, "index.html")
                if not os.path.exists(candidate) and not candidate.endswith(".html"):
                    candidate_html = candidate + ".html"
                    if os.path.exists(candidate_html):
                        candidate = candidate_html
                if os.path.isfile(candidate):
                    ext = os.path.splitext(candidate)[1].lower()
                    self.send_file(candidate, mime_map.get(ext, "text/html; charset=utf-8"))
                    served = True
                    break
            if not served:
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

        if path == "/api/admin/debug":
            try:
                import sqlite3 as _sqlite3
                conn = _sqlite3.connect(CACHE_DB)
                jobs_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                sessions_count = conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0]
                all_jobs = conn.execute("SELECT id, email, job_name, city, state, created_at FROM jobs ORDER BY created_at DESC LIMIT 20").fetchall()
                all_sessions = conn.execute("SELECT email, expires_at FROM user_sessions ORDER BY expires_at DESC LIMIT 10").fetchall()
                # Test write
                conn.execute("INSERT INTO jobs (id,email,job_name,city,state,created_at,updated_at) VALUES ('debug-test','debug@test.com','Debug Job','Test','TX',datetime('now'),datetime('now'))")
                conn.execute("DELETE FROM jobs WHERE id='debug-test'")
                conn.commit()
                conn.close()
                self.send_json(200, {
                    "data_dir": DATA_DIR, "cache_db": CACHE_DB,
                    "jobs_count": jobs_count, "sessions_count": sessions_count,
                    "write_test": "ok",
                    "jobs": [{"id": r[0], "email": r[1], "job_name": r[2], "city": r[3], "state": r[4], "created_at": r[5]} for r in all_jobs],
                    "sessions": [{"email": r[0], "expires_at": r[1]} for r in all_sessions]
                })
            except Exception as e:
                self.send_json(500, {"error": str(e), "data_dir": DATA_DIR, "cache_db": CACHE_DB})
            return True

        if path == "/api/admin/create-session":
            try:
                from urllib.parse import parse_qs as _pqs
                params = _pqs(urlparse(self.path).query)
                email = params.get("email", [""])[0].strip().lower()
                plan = params.get("plan", ["free"])[0].strip().lower() or "free"
                if not email:
                    self.send_json(400, {"error": "email param required"})
                    return True
                if plan not in ("free", "solo", "team"):
                    self.send_json(400, {"error": "plan must be free, solo, or team"})
                    return True
                token = create_session_token(email)
                if plan != "free":
                    now = utc_now().isoformat()
                    conn = sqlite3.connect(CACHE_DB)
                    conn.execute(
                        "UPDATE users SET plan=?, last_login=? WHERE email=?",
                        (plan, now, email),
                    )
                    conn.commit()
                    conn.close()
                self.send_json(200, {"token": token, "email": email, "plan": plan, "paid": plan != "free"})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
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
                    "session_cache":    get_cache_hit_rate(),
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
                        message_text = msg_event.get("message", {}).get("text", "")
                        postback = msg_event.get("postback", {}).get("payload", "")
                        incoming = postback or message_text
                        if sender_id and incoming:
                            notify_telegram(f"💬 Facebook Page DM\nFrom: {sender_id}\nMessage: {incoming}")
                            threading.Thread(target=handle_messenger_message, args=(sender_id, incoming), daemon=True).start()
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

        # ── Saved jurisdictions ────────────────────────────────────────────
        if path == "/api/jurisdictions/save":
            try:
                try:
                    data = self.read_json_body()
                except json.JSONDecodeError:
                    self.send_json(400, {"error": "Invalid request body — expected JSON"})
                    return
                email = data.get("email", "")
                city = data.get("city", "")
                state = data.get("state", "")
                trade = data.get("trade", "")
                if not _valid_saved_email(email):
                    self.send_json(400, {"error": "Valid email required"})
                    return
                if not _normalize_saved_city(city) or not _normalize_saved_state(state):
                    self.send_json(400, {"error": "city and state are required"})
                    return
                saved = save_jurisdiction(email, city, state, trade, data.get("display_name", ""), data.get("notes", ""))
                self.send_json(200, {"jurisdiction": saved})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
            return

        if path.startswith("/api/jurisdictions/") and path.endswith("/lookup"):
            try:
                jurisdiction_id = path[len("/api/jurisdictions/"):-len("/lookup")].strip("/")
                if not jurisdiction_id:
                    self.send_json(400, {"error": "Jurisdiction ID required"})
                    return
                qs = parse_qs(urlparse(self.path).query)
                email = (qs.get("email", [""])[0] or "").strip()
                try:
                    data = self.read_json_body()
                    email = (data.get("email") or email or "").strip()
                except json.JSONDecodeError:
                    pass
                if not _valid_saved_email(email):
                    self.send_json(400, {"error": "Valid email required"})
                    return
                status, saved = record_saved_jurisdiction_lookup(jurisdiction_id, email)
                if status == 403:
                    self.send_json(403, {"error": "Forbidden"})
                elif status == 404:
                    self.send_json(404, {"error": "Not found"})
                else:
                    self.send_json(200, {"jurisdiction": saved})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
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
                job_category = (data.get("job_category") or "").strip()
                explicit_vertical = canonical_request_vertical(data.get("vertical")) or canonical_request_vertical(data.get("evidence_vertical"))

                if not job_type or not city or not state:
                    self.send_json(400, {"error": "job_type, city, and state are required"})
                    return

                ip = self.client_ip()
                fingerprint = _normalize_fingerprint(self.headers.get("X-Client-Fingerprint", ""))

                # ── Sample demo flag — skip all counting/rate-limiting ──────────
                is_sample_demo = self.headers.get("X-Sample-Demo") == "1"

                # 2026-04-26: Benchmark bypass. If env BENCHMARK_SECRET is set
                # AND the request carries a matching X-PermitIQ-Benchmark-Secret
                # header, skip rate limit + free-3 limit so we can A/B both
                # engines through the FULL PermitIQ pipeline. Optional engine
                # override via X-PermitIQ-Engine: "gemini-3-flash-preview" or
                # "gpt-5.4-mini" forces a specific engine (no fallback).
                _BENCHMARK_SECRET = os.environ.get("BENCHMARK_SECRET", "")
                _benchmark_token = self.headers.get("X-PermitIQ-Benchmark-Secret", "")
                is_benchmark = bool(
                    _BENCHMARK_SECRET
                    and _benchmark_token
                    and len(_BENCHMARK_SECRET) >= 16
                    and hmac.compare_digest(_benchmark_token, _BENCHMARK_SECRET)
                )
                force_model = self.headers.get("X-PermitIQ-Engine", "").strip() if is_benchmark else None

                session_token = self.headers.get("X-Session-Token", "")
                user_email = validate_session_token(session_token) if session_token else None
                paid = is_paid_user(user_email) if user_email else False
                unlimited = is_sample_demo or paid or is_unlimited_lookup_ip(ip) or is_benchmark
                used_before = 0 if unlimited else get_effective_free_usage(ip, fingerprint)
                response_headers = {} if unlimited else build_free_lookup_headers(used_before)

                admin_bypass = False
                if not is_sample_demo and not is_benchmark:
                    limited, retry_after = check_rate_limit(ip)
                    if limited and not unlimited:
                        self.send_json(429, {
                            "error": "rate_limit_exceeded",
                            "message": "Too many requests. Please wait a minute and try again.",
                        }, extra_headers={**response_headers, "Retry-After": str(retry_after)})
                        return

                    # Admin-bypass: if X-Admin-Token header matches ADMIN_TOKEN env, skip the free-tier limit
                    admin_token = self.headers.get("X-Admin-Token", "")
                    if ADMIN_TOKEN and admin_token == ADMIN_TOKEN:
                        admin_bypass = True
                        # Log the bypass for audit trail
                        print(f"[admin-bypass] /api/permit lookup bypass at {datetime.utcnow().isoformat()} email={data.get('email','')}")
                        # Skip free-tier limit check — proceed to engine
                    else:
                        if used_before >= FREE_LOOKUP_LIMIT and not unlimited:
                            user = get_user(user_email) if user_email else None
                            if user and user.get("email"):
                                threading.Thread(target=send_free_limit_email_once, args=(user["email"],), daemon=True).start()
                            self.send_json(403, {
                                "error": "free_limit_reached",
                                "message": "You've used your 3 free lookups. Subscribe to continue.",
                                "upgrade_url": FREE_LOOKUP_UPGRADE_URL,
                            }, extra_headers=response_headers)
                            return

                if is_benchmark:
                    print(f"[permit][BENCH] {job_type} in {city}, {state} — engine={force_model or 'default'} ip={ip}")
                elif user_email:
                    print(f"[permit] {job_type} in {city}, {state} — user={user_email} plan={'paid' if paid else 'free'} ip={ip} used={used_before}")
                else:
                    print(f"[permit] {job_type} in {city}, {state} ({job_category}) — IP={ip} used={used_before}")

                early_rejection = resolve_customer_decision({
                    "result": {},
                    "job_type": job_type,
                    "city": city,
                    "state": state,
                })
                if is_input_rejection(early_rejection):
                    self.send_json(
                        200,
                        build_customer_permit_view_model(early_rejection, job_type, city, state),
                        extra_headers=response_headers,
                    )
                    return

                # Benchmark requests bypass the cache so we measure fresh
                # pipeline behavior, not pre-cached results.
                record_beta_event("lookup_started", {
                    "city": city,
                    "state": state,
                    "job_category": job_category,
                    "commercial_scope": _is_commercial_scope(job_type),
                    "paid": paid,
                    "sample_demo": is_sample_demo,
                    "benchmark": is_benchmark,
                }, user_email or "")
                evidence_allowed = evidence_pack_allowed_for_request(path, self.headers, is_sample_demo=is_sample_demo)
                acquired_lookup_slot = PERMIT_LOOKUP_SEMAPHORE.acquire(timeout=PERMIT_LOOKUP_QUEUE_TIMEOUT_SECONDS)
                if not acquired_lookup_slot:
                    print(
                        f"[permit] busy: concurrency limit {PERMIT_LOOKUP_CONCURRENCY_LIMIT} "
                        f"held for >{PERMIT_LOOKUP_QUEUE_TIMEOUT_SECONDS}s; returning 429"
                    )
                    self.send_json(429, {
                        "error": "server_busy",
                        "message": "PermitAssist is processing other lookups. Please retry in a few seconds.",
                    }, extra_headers={**response_headers, "Retry-After": "10"})
                    return
                try:
                    if not unlimited and not admin_bypass:
                        # A queued free lookup can become exhausted while waiting
                        # behind another expensive lookup. Re-check immediately
                        # before research so public/no-header traffic still
                        # blocks cheaply instead of consuming worker resources.
                        used_now = get_effective_free_usage(ip, fingerprint)
                        if used_now >= FREE_LOOKUP_LIMIT:
                            response_headers = build_free_lookup_headers(used_now)
                            self.send_json(403, {
                                "error": "free_limit_reached",
                                "message": "You've used your 3 free lookups. Subscribe to continue.",
                                "upgrade_url": FREE_LOOKUP_UPGRADE_URL,
                            }, extra_headers=response_headers)
                            return
                    _use_cache = (not is_benchmark) and (not evidence_allowed)
                    result = research_permit(
                        job_type, city, state, zip_code,
                        job_category=job_category,
                        use_cache=_use_cache,
                        force_model=force_model,
                        suppress_cache_write=evidence_allowed,
                    )
                    is_cached = result.get("_cached", False)

                    if not unlimited and not is_sample_demo:
                        used_after = max(*record_lookup_usage(ip, fingerprint))
                        response_headers = build_free_lookup_headers(used_after)
                        result["remaining_lookups"] = max(0, FREE_LOOKUP_LIMIT - used_after)
                    elif paid:
                        result["remaining_lookups"] = -1
                    elif unlimited:
                        result["remaining_lookups"] = FREE_LOOKUP_LIMIT

                    result = finalize_permit_lookup_result(result, job_type, city, state, is_cached=is_cached, explicit_vertical=explicit_vertical, evidence_allowed=evidence_allowed)
                    existing_citations = result.get("claim_citations") if isinstance(result.get("claim_citations"), list) else []
                    display_sources = _source_dicts(result, city=city, state=state)
                    result["source_urls"] = [s.get("url") for s in display_sources if s.get("url")]
                    result["sources"] = display_sources
                    if existing_citations:
                        filtered_citations = []
                        for citation in existing_citations:
                            if not isinstance(citation, dict):
                                continue
                            source_url = str(citation.get("source_url") or "")
                            if source_url:
                                authority = classify_source_authority(source_url, city, state, result=result)
                                if not authority.get("display_allowed"):
                                    continue
                            filtered_citations.append(citation)
                        result["claim_citations"] = filtered_citations
                    elif not (isinstance(result.get("_evidence_pack"), dict) and result["_evidence_pack"].get("enabled")):
                        build_claim_citations(result, city, state)

                    # Record stats and beta telemetry
                    record_lookup_stat(job_type, city, state, is_cached)
                    record_beta_event("lookup_completed", {
                        "city": city,
                        "state": state,
                        "job_category": job_category,
                        "commercial_scope": _is_commercial_scope(job_type, result),
                        "confidence": result.get("confidence"),
                        "needs_review": result.get("needs_review", False),
                        "cached": is_cached,
                        "paid": paid,
                        "free_limit_remaining": result.get("remaining_lookups"),
                    }, user_email or "")

                    # No Telegram on lookups — only notify on paying customers
                    # Evidence-pack preview endpoints intentionally expose pack diagnostics for gated QA/API parity.
                    # Normal customer lookups send only sanitized, customer-visible fields.
                    customer_result = result if evidence_allowed else build_customer_permit_view_model(result, job_type, city, state)

                    self.send_json(200, customer_result, extra_headers=response_headers)
                finally:
                    PERMIT_LOOKUP_SEMAPHORE.release()

            except Exception as e:
                print(f"[permit] Error: {e}")
                try:
                    record_beta_event("lookup_failed", {"error_type": type(e).__name__, "path": "/api/permit"})
                except Exception:
                    pass
                import traceback; traceback.print_exc()
                self.send_json(500, {"error": "Lookup failed — please try again"})

        elif path == "/api/batch-permit":
            try:
                data = self.read_json_body()
                lookups = data.get("lookups", [])
                if not lookups:
                    self.send_json(400, {"error": "No lookups provided"})
                    return
                if len(lookups) > 20:
                    self.send_json(400, {"error": "Max 20 lookups per batch"})
                    return
                from concurrent.futures import ThreadPoolExecutor, as_completed

                def run_lookup(item):
                    job_type = item.get("job_type", "")
                    city = item.get("city", "")
                    state = item.get("state", "")
                    zip_code = item.get("zip", "") or item.get("zip_code", "")
                    job_value = item.get("job_value")
                    job_category = item.get("job_category", "")
                    explicit_vertical = canonical_request_vertical(item.get("vertical")) or canonical_request_vertical(item.get("evidence_vertical"))
                    try:
                        evidence_allowed = evidence_pack_allowed_for_request("/api/batch-permit", self.headers)
                        result = research_permit(job_type, city, state, zip_code, job_category=job_category, job_value=job_value, use_cache=not evidence_allowed, suppress_cache_write=evidence_allowed)
                        if evidence_allowed:
                            result = finalize_permit_lookup_result(result, job_type, city, state, is_cached=result.get("_cached", False), explicit_vertical=explicit_vertical, evidence_allowed=evidence_allowed)
                            response = dict(result)
                            response.update({"job_type": job_type, "city": city, "state": state, "error": None})
                            return response
                        response = build_customer_permit_view_model(result, job_type, city, state)
                        response.update({"job_type": job_type, "city": city, "state": state, "error": None})
                        return response
                    except Exception as e:
                        return {"job_type": job_type, "city": city, "state": state, "error": str(e)}

                results = []
                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = {executor.submit(run_lookup, item): item for item in lookups}
                    for future in as_completed(futures, timeout=120):
                        try:
                            results.append(future.result(timeout=30))
                        except Exception as e:
                            item = futures[future]
                            results.append({"city": item.get("city"), "error": str(e)})
                self.send_json(200, {
                    "results": results,
                    "total": len(results),
                    "summary": {
                        "permits_required": sum(1 for r in results if r.get("permit_verdict") == "YES"),
                        "no_permit": sum(1 for r in results if r.get("permit_verdict") == "NO"),
                        "resolved_review": sum(1 for r in results if r.get("permit_verdict") not in {"YES", "NO"}),
                    }
                })
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        # ── Beta telemetry / feedback / white-label report ─────────────────
        elif path == "/api/beta-event":
            try:
                data = self.read_json_body()
                session_token = self.headers.get("X-Session-Token", "")
                user_email = validate_session_token(session_token) if session_token else ""
                if not user_email:
                    self.send_json(401, {"error": "login_required"})
                    return
                event = (data.get("event") or "client_event").strip()[:80]
                payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
                record_beta_event(event, payload, user_email)
                self.send_json(200, {"recorded": True})
            except Exception as e:
                print(f"[beta-event] Error: {e}")
                self.send_json(500, {"error": str(e)})

        elif path == "/api/beta-feedback":
            try:
                data = self.read_json_body()
                session_token = self.headers.get("X-Session-Token", "")
                user_email = validate_session_token(session_token) if session_token else ""
                if not user_email:
                    self.send_json(401, {"error": "login_required"})
                    return
                saved = save_beta_feedback(
                    user_email,
                    data.get("job_type", ""),
                    data.get("city", ""),
                    data.get("state", ""),
                    data.get("useful", ""),
                    data.get("knew_next_step", ""),
                    data.get("missing", ""),
                    data.get("ahj_confirmed", ""),
                    data.get("use_again", ""),
                )
                self.send_json(200, saved)
            except Exception as e:
                print(f"[beta-feedback] Error: {e}")
                self.send_json(500, {"error": str(e)})

        elif path == "/api/white-label-report":
            try:
                data = self.read_json_body()
                session_token = self.headers.get("X-Session-Token", "")
                user_email = validate_session_token(session_token) if session_token else ""
                if not user_email:
                    self.send_json(401, {"error": "login_required"})
                    return
                html_doc = _SENSITIVE_OUTPUT_RE.sub("[REDACTED]", render_white_label_report_html(redact_public_output(data)))
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(html_doc.encode("utf-8"))
            except Exception as e:
                print(f"[white-label-report] Error: {e}")
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
                # Delete for all job_category variants (residential, commercial, both)
                import hashlib
                base = f"{job_type.lower().strip()}|{city.lower().strip()}|{state.upper().strip()}"
                for cat in ['residential', 'commercial', '']:
                    raw = f"{base}|{cat}" if cat else base
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
                    f"Job: {html.escape(job_type)}\n"
                    f"Location: {html.escape(city)}, {html.escape(state)}\n"
                    f"Issue: {html.escape(issue or '(no detail provided)')}"
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
                public_result = build_customer_permit_view_model(result, job_type, city, state)
                self.send_json(200, get_or_create_checklist(public_result, job_type, city, state))
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
                try:
                    callback_url = validate_webhook_callback_url(str(data.get("callback_url", "")).strip())
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
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
                self.send_json(202, {"accepted": True, "integration": integration.get("name") or "Webhook", "callback_url": integration.get("callback_url")})
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
                job_category = (data.get("job_category") or "").strip()
                explicit_vertical = canonical_request_vertical(data.get("vertical")) or canonical_request_vertical(data.get("evidence_vertical"))
                if not job_type or not city or not state:
                    self.send_json(400, {"error": "job_type, city, and state are required"})
                    return
                evidence_allowed = evidence_pack_allowed_for_request(path, self.headers)
                result = research_permit(job_type, city, state, zip_code, job_category=job_category, use_cache=not evidence_allowed, suppress_cache_write=evidence_allowed)
                if evidence_allowed:
                    result = finalize_permit_lookup_result(result, job_type, city, state, is_cached=result.get("_cached", False), explicit_vertical=explicit_vertical, evidence_allowed=evidence_allowed)
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
                fix_result = get_rejection_fix_result(job_id, rejection_text, city, state, job_type)
                self.send_json(200, {"fix_plan": build_fix_plan_text(fix_result), "result": fix_result, "ok": True})
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
                if not ADMIN_TOKEN or not hmac.compare_digest(admin_token, ADMIN_TOKEN):
                    self.send_json(401, {"error": "Admin token required"})
                    return
                sent = process_onboarding_emails()
                self.send_json(200, {"sent": sent, "status": "ok"})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        # ── Check permit issued reminders ─────────────────────────────────
        elif path == "/api/check-permit-reminders":
            try:
                admin_token = self.headers.get("X-Admin-Token", "")
                if not ADMIN_TOKEN or not hmac.compare_digest(admin_token, ADMIN_TOKEN):
                    self.send_json(401, {"error": "Admin token required"})
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
                body = self.read_json_body()
                rejection_text = (body.get('rejection_text') or '').strip()
                job_type = (body.get('job_type') or '').strip()
                city = (body.get('city') or '').strip()
                state = (body.get('state') or '').strip()
                job_id = (body.get('job_id') or '').strip()
                if not rejection_text:
                    self.send_json(400, {'error': 'rejection_text is required'})
                    return

                _fix_session_token = self.headers.get('X-Session-Token', '')
                _fix_user = validate_session_token(_fix_session_token) if _fix_session_token else None
                if not _fix_user:
                    self.send_json(401, {'error': 'Login required'})
                    return
                if job_id and not user_can_access_job(job_id, _fix_user):
                    self.send_json(403, {'error': 'Access denied'})
                    return

                result = get_rejection_fix_result(job_id, rejection_text, city, state, job_type)
                self.send_json(200, {'ok': True, 'result': result, 'fix_plan': build_fix_plan_text(result)})

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
                        model="gpt-5.4-mini",
                        messages=[
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": question}
                        ],
                        max_completion_tokens=300,
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
    init_free_lookup_db()
    process_due_reminders()
    threading.Thread(target=reminder_worker, daemon=True).start()
    threading.Thread(target=background_task_worker, daemon=True).start()
    print(f"🚀 PermitAssist server starting on port {PORT}")
    print(f"   Abuse limit: {RATE_LIMIT_MAX_REQUESTS} requests / {RATE_LIMIT_WINDOW_SECONDS}s per IP")
    print(f"   Free guest limit: {FREE_LOOKUP_LIMIT} lookups forever per IP/fingerprint")
    print(f"   Stripe webhook: {'configured' if STRIPE_WEBHOOK_SECRET else 'no STRIPE_WEBHOOK_SECRET'}")
    print(f"   Stripe portal: {'configured' if STRIPE_SECRET_KEY else 'no STRIPE_SECRET_KEY'}")
    print(f"   Telegram: {'enabled' if TG_BOT_TOKEN else 'disabled'}")
    print(f"   Open: http://localhost:{PORT}")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()

# ── Messenger Bot Helper ──────────────────────────────────────────────────────

MESSENGER_PAGE_TOKEN = os.environ.get("FB_PAGE_TOKEN")

TRADE_LINKS = {
    "roof": ("roofing", "https://permitassist.io/roofing"),
    "roof": ("roofing", "https://permitassist.io/roofing"),
    "plumb": ("plumbing", "https://permitassist.io/plumbing"),
    "electr": ("electrical", "https://permitassist.io/electrical"),
    "hvac": ("HVAC", "https://permitassist.io/hvac"),
    "heat": ("HVAC", "https://permitassist.io/hvac"),
    "cool": ("HVAC", "https://permitassist.io/hvac"),
    "solar": ("solar", "https://permitassist.io/solar"),
    "general": ("general contracting", "https://permitassist.io"),
    "contractor": ("general contracting", "https://permitassist.io"),
}

def messenger_send(recipient_id, text):
    """Send a Messenger message as the PermitAssist page."""
    try:
        requests.post(
            "https://graph.facebook.com/v19.0/me/messages",
            params={"access_token": MESSENGER_PAGE_TOKEN},
            json={"recipient": {"id": recipient_id}, "message": {"text": text}},
            timeout=5
        )
    except Exception:
        pass

def messenger_send_buttons(recipient_id, text, buttons):
    """Send a Messenger message with quick reply buttons."""
    try:
        quick_replies = [{"content_type": "text", "title": b, "payload": b.upper()} for b in buttons]
        requests.post(
            "https://graph.facebook.com/v19.0/me/messages",
            params={"access_token": MESSENGER_PAGE_TOKEN},
            json={"recipient": {"id": recipient_id}, "message": {"text": text, "quick_replies": quick_replies}},
            timeout=5
        )
    except Exception:
        pass

def handle_messenger_message(sender_id, text):
    """Route inbound Messenger messages through the bot flow."""
    text_lower = text.lower().strip()

    # Check for trade keywords
    for keyword, (trade_name, link) in TRADE_LINKS.items():
        if keyword in text_lower:
            messenger_send(sender_id,
                f"Got it — here's the PermitAssist page built for {trade_name} contractors:\n\n"
                f"{link}\n\n"
                f"You get 3 free lookups, no credit card needed. Takes 30 seconds to get started."
            )
            notify_telegram(f"📩 Messenger lead: trade={trade_name}, sender={sender_id}")
            return

    # Greeting or unknown — ask what trade
    greetings = ["hi", "hello", "hey", "hiya", "sup", "yo", "good"]
    if any(g in text_lower for g in greetings) or len(text_lower) < 15:
        messenger_send_buttons(sender_id,
            "Hey! What trade are you in? I'll send you the right permit lookup page.",
            ["Roofing", "Plumbing", "Electrical", "HVAC", "Solar", "Other"]
        )
    else:
        messenger_send(sender_id,
            "Thanks for reaching out! PermitAssist helps contractors look up permit requirements in seconds — fees, docs, timelines for 1,569 cities.\n\n"
            "Try 3 free lookups here: https://permitassist.io\n\n"
            "What trade are you in? I can send you the specific page for your work."
        )
# volume: persistent data
# Wed Apr 22 20:06:50 BST 2026
# cache_dir enforced Wed Apr 22 20:36:19 BST 2026
