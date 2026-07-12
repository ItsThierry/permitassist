from __future__ import annotations

import hashlib
import json
import os

for key in (
    "PERMITASSIST_RULE_ENGINE_CORE",
    "PERMITASSIST_RULE_ENGINE_CORE_ALLOWLIST",
    "PERMITASSIST_RULE_ENGINE_SHADOW",
    "PERMITASSIST_RULE_ENGINE_SHADOW_LOG",
):
    os.environ.pop(key, None)

from api.research_engine import cache_key
from api.server import build_customer_permit_view_model, finalize_permit_lookup_result

legacy = {
    "permit_decision": "Permit Required",
    "permit_verdict": "YES",
    "permit_required": True,
    "permit_type": "Building Permit",
    "permit_kind": "Building",
    "permit_name": "Building Permit",
    "applying_office": "Example Building Department",
    "apply_url": "https://example.gov/apply",
    "permit_portal_url": "https://example.gov/apply",
    "customer_next_step": "Apply with the listed office.",
    "recommended_action": "Apply with the listed office.",
    "required_documents": ["Plans", "Scope narrative"],
    "permits_required": [
        {
            "permit_type": "Building Permit",
            "permit_kind": "Building",
            "required": True,
            "trigger": "Interior alteration",
            "applying_office": "Example Building Department",
            "apply_url": "https://example.gov/apply",
            "provenance": {
                "source_url": "https://example.gov/rule",
                "source_quote": "A permit is required.",
                "snapshot_hash": "a" * 64,
                "snapshot_path": "snapshots/example.html",
                "publishable": True,
            },
        }
    ],
    "sources": [{"url": "https://example.gov/rule", "title": "Official rule"}],
    "warnings": ["Verify current fees."],
    "processing_time": "10 business days",
    "fee_estimate": "$100–$200",
    "inspections": ["Final inspection"],
}
view = build_customer_permit_view_model(dict(legacy), "interior alteration", "Example", "EX")
finalized = finalize_permit_lookup_result(dict(legacy), "interior alteration", "Example", "EX")
payload = {
    "cache_key": cache_key("interior alteration", "Example", "EX", "commercial"),
    "customer_view": view,
    "finalized": finalized,
}
raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(json.dumps({"payload_sha256": hashlib.sha256(raw).hexdigest(), "payload": payload}, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
