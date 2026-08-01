"""Exact-AHJ, claim-bound permit rules loaded from versioned knowledge records."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    from .scope_contract import _term_is_locally_negated
except ImportError:  # pragma: no cover - direct api/ import compatibility
    from scope_contract import _term_is_locally_negated

STATUSES = frozenset({"REQUIRED", "CONDITIONAL", "NOT_REQUIRED", "NEEDS_INPUT", "VERIFY"})
_RULE_ROOT = Path(__file__).resolve().parents[1] / "knowledge" / "ahj_rules"


@dataclass(frozen=True)
class AhjRule:
    rule_id: str
    state: str
    city: str
    issuing_authority: str
    family: str
    status: str
    scope_patterns: tuple[str, ...]
    source_url: str
    source_title: str
    source_quote: str
    source_claim_sha256: str
    retrieved_on: str
    application_url: str | None
    application_channel: str | None

    def matches_scope(self, job_type: str) -> bool:
        text = str(job_type or "")
        for pattern in self.scope_patterns:
            for match in re.finditer(pattern, text, re.I):
                if not _term_is_locally_negated(text, match.start()):
                    return True
        return False


def _load_rules() -> tuple[AhjRule, ...]:
    rules: list[AhjRule] = []
    if not _RULE_ROOT.exists():
        return ()
    for path in sorted(_RULE_ROOT.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "permitassist-ahj-rule-v1":
            raise ValueError(f"unsupported AHJ rule schema: {path}")
        for raw in payload.get("rules") or []:
            jurisdiction = raw.get("jurisdiction") or {}
            source = raw.get("official_source") or {}
            application = raw.get("application") or {}
            status = str(raw.get("status") or "").upper()
            if status not in STATUSES:
                raise ValueError(f"invalid AHJ rule status in {path}: {status!r}")
            url = str(source.get("url") or "")
            quote = str(source.get("quote") or "")
            digest = hashlib.sha256(f"{url}\n{quote}".encode()).hexdigest()
            if digest != source.get("claim_sha256"):
                raise ValueError(f"AHJ rule claim hash mismatch: {path}")
            host = (urlsplit(url).hostname or "").lower()
            if not url.startswith("https://") or not (host.endswith(".gov") or host == "sandiego.gov" or host.endswith(".sandiego.gov")):
                raise ValueError(f"AHJ rule source is not an official HTTPS host: {url}")
            patterns = tuple(str(value) for value in raw.get("scope_patterns") or [] if str(value))
            if not patterns:
                raise ValueError(f"AHJ rule has no scope patterns: {path}")
            for pattern in patterns:
                re.compile(pattern)
            rules.append(AhjRule(
                rule_id=str(raw.get("rule_id") or ""),
                state=str(jurisdiction.get("state") or "").upper(),
                city=str(jurisdiction.get("city") or ""),
                issuing_authority=str(jurisdiction.get("issuing_authority") or ""),
                family=str(raw.get("family") or "").lower(),
                status=status,
                scope_patterns=patterns,
                source_url=url,
                source_title=str(source.get("title") or ""),
                source_quote=quote,
                source_claim_sha256=digest,
                retrieved_on=str(source.get("retrieved_on") or ""),
                application_url=str(application.get("url") or "") or None,
                application_channel=str(application.get("channel") or "") or None,
            ))
    return tuple(rules)


AHJ_RULES = _load_rules()


def _source_urls(result: dict[str, Any]) -> set[str]:
    urls = {str(value).strip() for value in result.get("source_urls") or [] if isinstance(value, str)}
    for source in result.get("sources") or []:
        if isinstance(source, str):
            urls.add(source.strip())
        elif isinstance(source, dict):
            for key in ("url", "source_url"):
                if isinstance(source.get(key), str):
                    urls.add(source[key].strip())
    return urls


def resolve_ahj_rule(city: str, state: str, job_type: str, result: dict[str, Any]) -> AhjRule | None:
    """Resolve exact jurisdiction and scope from the retained integrity-checked rule registry.

    Runtime retrieval is not an authority prerequisite: a committed rule record
    already retains the official URL, verbatim quote, retrieval date, and claim
    digest. Requiring the same URL to appear in a mutable response made exact
    authority disappear during source timeouts.
    """
    city_key = str(city or "").strip().casefold()
    state_key = str(state or "").strip().upper()
    matches = [
        rule for rule in AHJ_RULES
        if rule.state == state_key
        and rule.city.casefold() == city_key
        and rule.matches_scope(job_type)
    ]
    if not matches:
        return None
    matches.sort(key=lambda rule: rule.rule_id)
    return matches[0]
