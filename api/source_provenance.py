"""Central source/provenance role classifier for customer-truth gates.

This module intentionally wraps the existing locality/source machinery so row
confidence and family gates use one oracle instead of ad-hoc string sniffing.
"""
from __future__ import annotations

import json
from typing import Any

try:
    from research_engine import SOURCE_TIER_AHJ, classify_source_tier, classify_source_url
except Exception:  # pragma: no cover - package import path
    from api.research_engine import SOURCE_TIER_AHJ, classify_source_tier, classify_source_url


def _urls_from_value(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            urls.append(value)
    elif isinstance(value, dict):
        for key in ("url", "source_url", "href", "link"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.startswith(("http://", "https://")):
                urls.append(raw)
        for nested in value.values():
            if isinstance(nested, (dict, list, tuple)):
                urls.extend(_urls_from_value(nested))
    elif isinstance(value, (list, tuple)):
        for item in value:
            urls.extend(_urls_from_value(item))
    return list(dict.fromkeys(urls))


def urls_for_provenance(obj: dict[str, Any] | None) -> list[str]:
    obj = obj if isinstance(obj, dict) else {}
    urls: list[str] = []
    for key in ("source_url", "source_urls", "sources", "claim_citations", "apply_url", "online_application_url", "apply_path", "evidence", "citations"):
        urls.extend(_urls_from_value(obj.get(key)))
    return list(dict.fromkeys(urls))


def classify_row_source(row_or_result: dict[str, Any] | None, *, city: str = "", state: str = "", result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a compact provenance verdict used by resolver/gates.

    role values:
    - ``ahj_direct``: official/locality-verified AHJ source.
    - ``official_nonlocal``: official-looking source that is not locality-verified.
    - ``supplementary``: allowed reference/source but not official enough.
    - ``untrusted``: no source or excluded/blog/random source.
    """
    row_or_result = row_or_result if isinstance(row_or_result, dict) else {}
    urls = urls_for_provenance(row_or_result)
    if result and result is not row_or_result:
        urls.extend(urls_for_provenance(result))
    urls = list(dict.fromkeys(urls))
    locality_verified = False
    official = False
    supplementary = False
    for url in urls:
        try:
            if city and state and classify_source_tier(url, city, state, result=result or row_or_result) == SOURCE_TIER_AHJ:
                locality_verified = True
                official = True
                break
        except Exception:
            pass
        try:
            source_class = str(classify_source_url(url) or "").lower()
        except Exception:
            source_class = ""
        if "official" in source_class:
            official = True
        elif "supplement" in source_class:
            supplementary = True
    role = "ahj_direct" if locality_verified else ("official_nonlocal" if official else ("supplementary" if supplementary else "untrusted"))
    return {
        "role": role,
        "official": bool(official),
        "locality_verified": bool(locality_verified),
        "urls": urls,
    }


def has_local_official_source(obj: dict[str, Any] | None, *, city: str = "", state: str = "", result: dict[str, Any] | None = None) -> bool:
    verdict = classify_row_source(obj, city=city, state=state, result=result)
    return bool(verdict.get("official") and (verdict.get("locality_verified") or not (city and state)))
