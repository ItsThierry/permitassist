from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse
import re
from typing import Any


class SourceRole(str, Enum):
    LOCAL_OFFICIAL_FILING = "LOCAL_OFFICIAL_FILING"
    LOCAL_OFFICIAL_INFO = "LOCAL_OFFICIAL_INFO"
    STATE_OFFICIAL = "STATE_OFFICIAL"
    NATIONAL_MODEL_CODE = "NATIONAL_MODEL_CODE"
    PUBLISHER_CONTEXT = "PUBLISHER_CONTEXT"
    UNKNOWN = "UNKNOWN"


_STATE_HOST_RE = re.compile(r"(?:^|\.)(?:[a-z]{2}\.gov|state\.[a-z]{2}\.us|oregon\.gov|colorado\.gov|pa\.gov|ms\.gov)$", re.I)
_MODEL_CODE_HOSTS = {"codes.iccsafe.org", "up.codes", "upcodes.com"}
_PUBLISHER_HOSTS = {"iccsafe.org", "www.iccsafe.org", "nfpa.org", "www.nfpa.org"}
_FILING_HINT_RE = re.compile(r"\b(?:apply|permit|permits|portal|devhub|aca|accela|eplan|energov|opengov|licens|inspection|li\.)\b", re.I)
_INFO_HINT_RE = re.compile(r"\b(?:building|department|code|development|planning|bds|ppd|services|faq|get-permit)\b", re.I)


def _host(url: str) -> str:
    return urlparse(str(url or "")).netloc.lower().split("@").pop().split(":")[0].removeprefix("www.")


def _path(url: str) -> str:
    parsed = urlparse(str(url or ""))
    return f"{parsed.path} {parsed.query}".lower().replace("-", " ").replace("_", " ")


def _city_tokens(ahj_identity: dict[str, Any] | None) -> set[str]:
    city = str((ahj_identity or {}).get("city") or "").lower().strip()
    if not city:
        return set()
    return {token for token in re.split(r"[^a-z0-9]+", city) if len(token) >= 3}


_AUTHORITY_STOP_TOKENS = {
    "building", "department", "development", "permit", "permits", "planning",
    "county", "city", "town", "village", "government", "joint", "official",
}
_JOINT_COUNTY_TOKENS = {("fort wayne", "in"): {"allen"}}
_KNOWN_FILING_PORTAL_LOCALITY_TOKENS = {
    "devhub.portlandoregon.gov": {"portland"},
    "li.phila.gov": {"philadelphia"},
}


def _authority_tokens(ahj_identity: dict[str, Any] | None) -> set[str]:
    identity = ahj_identity or {}
    state = str(identity.get("state") or "").lower().strip()
    city = str(identity.get("city") or "").lower().strip()
    values = (
        identity.get("authority_name"),
        identity.get("resolved_ahj_name"),
        identity.get("resolved_ahj_key"),
        identity.get("county"),
    )
    tokens = set(_city_tokens(identity))
    for value in values:
        tokens.update(
            token for token in re.split(r"[^a-z0-9]+", str(value or "").lower())
            if len(token) >= 3 and token not in _AUTHORITY_STOP_TOKENS
        )
    tokens.update(_JOINT_COUNTY_TOKENS.get((city, state), set()))
    return tokens


def classify_source(url: str, ahj_identity: dict[str, Any] | None = None) -> tuple[SourceRole, str]:
    text = str(url or "").strip()
    if not text:
        return SourceRole.UNKNOWN, "empty URL"
    host = _host(text)
    path = _path(text)
    host_no_www = host.removeprefix("www.")
    if host_no_www in _MODEL_CODE_HOSTS:
        return SourceRole.NATIONAL_MODEL_CODE, "national/model code reference host"
    if host_no_www in _PUBLISHER_HOSTS:
        if re.search(r"\b(?:blog|journal|article|news|technical)\b", path, re.I):
            return SourceRole.PUBLISHER_CONTEXT, "publisher/blog/background-reading host"
        return SourceRole.NATIONAL_MODEL_CODE, "national code-organization reference"
    authority_tokens = _authority_tokens(ahj_identity)
    state = str((ahj_identity or {}).get("state") or "").lower().strip()
    is_gov = host.endswith(".gov") or ".gov." in host or host.endswith(".us")
    normalized_location = re.sub(r"[^a-z0-9]+", "", f"{host} {path}")
    localish = bool(authority_tokens and any(token in normalized_location for token in authority_tokens))
    normalized_host = re.sub(r"[^a-z0-9]+", "", host)
    host_localish = bool(authority_tokens and any(token in normalized_host for token in authority_tokens))
    if host_no_www in _KNOWN_FILING_PORTAL_LOCALITY_TOKENS:
        expected_tokens = _KNOWN_FILING_PORTAL_LOCALITY_TOKENS[host_no_www]
        if authority_tokens & expected_tokens:
            return SourceRole.LOCAL_OFFICIAL_FILING, "known local official filing portal for requested locality"
        return SourceRole.UNKNOWN, "filing portal is not confirmed for the requested locality"
    if host_no_www == "aca-prod.accela.com" and path.startswith("/acfw"):
        if state in {"", "in"} and ({"allen", "fort", "wayne"} & authority_tokens):
            return SourceRole.LOCAL_OFFICIAL_FILING, "known Allen County/City of Fort Wayne official filing portal for resolved locality"
        return SourceRole.UNKNOWN, "filing portal is not confirmed for the requested locality"
    municipal_host = bool(re.search(r"(?:^|\.)(?:[a-z0-9-]*county|cityof|townof|villageof)", host_no_www))
    if is_gov and municipal_host:
        if host_localish and _FILING_HINT_RE.search(f"{host} {path}"):
            return SourceRole.LOCAL_OFFICIAL_FILING, "local county/city government permit/filing URL"
        if host_localish:
            return SourceRole.LOCAL_OFFICIAL_INFO, "local county/city government permit information URL"
        return SourceRole.UNKNOWN, "county/city government URL is not confirmed for the requested locality"
    if is_gov and localish and _FILING_HINT_RE.search(f"{host} {path}"):
        return SourceRole.LOCAL_OFFICIAL_FILING, "local official filing/permit URL"
    if is_gov and localish:
        return SourceRole.LOCAL_OFFICIAL_INFO, "local official information URL"
    if _STATE_HOST_RE.search(host) or (is_gov and state and state in host):
        return SourceRole.STATE_OFFICIAL, "state official URL"
    if is_gov and (_FILING_HINT_RE.search(f"{host} {path}") or _INFO_HINT_RE.search(f"{host} {path}")):
        return SourceRole.UNKNOWN, "government permit URL is not confirmed for the requested locality"
    return SourceRole.UNKNOWN, "source role not classified as local/state/model/publisher"


def source_label_for_role(role: SourceRole | str) -> str:
    value = SourceRole(role) if not isinstance(role, SourceRole) else role
    return {
        SourceRole.LOCAL_OFFICIAL_FILING: "Official filing source",
        SourceRole.LOCAL_OFFICIAL_INFO: "Official permit information",
        SourceRole.STATE_OFFICIAL: "Official state source",
        SourceRole.NATIONAL_MODEL_CODE: "Code reference (model code — verify local adoption/amendments)",
        SourceRole.PUBLISHER_CONTEXT: "Background reading",
        SourceRole.UNKNOWN: "Supporting source (verify jurisdiction)",
    }[value]


def is_official_badge_role(role: SourceRole | str) -> bool:
    try:
        value = SourceRole(role)
    except Exception:
        return False
    return value in {SourceRole.LOCAL_OFFICIAL_FILING, SourceRole.LOCAL_OFFICIAL_INFO, SourceRole.STATE_OFFICIAL}


def is_apply_path_role(role: SourceRole | str) -> bool:
    try:
        return SourceRole(role) == SourceRole.LOCAL_OFFICIAL_FILING
    except Exception:
        return False
