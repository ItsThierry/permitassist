from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "api"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from api import server, v24_decision_cells as v24  # noqa: E402
from api.permit_manifest import build_permit_manifest_projection  # noqa: E402

FIXTURE_PATH = ROOT / "tests" / "fixtures" / "session4_source_backed_product_contracts_v1.json"
MANIFEST_PATH = ROOT / "knowledge" / "v24" / "permitassist_v24_manifest.json"
ALIASES = {
    "BUILDING_TI": "BUILDING",
    "FIRE": "FIRE_LIFE_SAFETY",
    "OCCUPANCY": "OCCUPANCY_CO",
    "WASTEWATER": "WASTEWATER_FOG",
    "ZONING": "ZONING_PLANNING",
}


def _token(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "").upper()).strip("_")


def _family(value: object) -> str:
    token = _token(value)
    return ALIASES.get(token, token)


def _normalized_url(value: object) -> str:
    return str(value or "").strip().rstrip("/")


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_source_backed_fixture_hash_is_self_consistent() -> None:
    fixture = _fixture()
    expected = fixture.pop("fixture_sha256")
    actual = hashlib.sha256(
        json.dumps(fixture, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert actual == expected
    assert len(fixture["cases"]) == 36
    assert sum(case["split"] == "holdout" for case in fixture["cases"]) == 9


def test_source_backed_product_contracts_end_to_end(monkeypatch) -> None:
    fixture = _fixture()
    cases = fixture["cases"]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    monkeypatch.setenv("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_CORE", "active")
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    monkeypatch.setenv("PERMITASSIST_PERMIT_MANIFEST_MODE", "active")
    monkeypatch.setenv(
        "PERMITASSIST_RULE_ENGINE_CORE_ALLOWLIST",
        ",".join(case["jurisdiction_id"] for case in cases),
    )
    monkeypatch.setenv(
        "PERMITASSIST_V24_MANIFEST_SHA256",
        hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest(),
    )
    v24._V24_INDEX_CACHE.clear()

    failures: list[dict] = []
    for case in cases:
        try:
            raw = server.build_active_core_first_result(
                job_type=case["job_type"],
                city=case["city"],
                state=case["state"],
                job_category=case["job_category"],
            )
            assert raw is not None
            final = server.finalize_permit_lookup_result(
                server._mark_server_owned_result(raw),
                case["job_type"],
                case["city"],
                case["state"],
                job_category=case["job_category"],
            )
            public = server.build_customer_response_egress(
                final,
                case["job_type"],
                case["city"],
                case["state"],
                job_category=case["job_category"],
            )
            manifest = public["permit_manifest"]
            primary = manifest["primary"]
            rows = [primary, *manifest.get("companions", [])]
            required_rows = [row for row in rows if _token(row.get("status")) == "REQUIRED"]
            required_families = [_family(row.get("family")) for row in required_rows]
            filing = manifest["filing_destination"]

            assert _token(manifest["permit_decision"]) == case["expected_decision"]
            assert _family(primary["family"]) == _family(case["expected_primary_family"])
            assert set(required_families) == {_family(value) for value in case["expected_required_families"]}
            assert len(required_families) == len(set(required_families))
            assert all(row.get("source_ref") or row.get("source_refs") for row in required_rows)
            assert all(row.get("authority") for row in required_rows)
            assert filing.get("application_authority")
            assert _normalized_url(filing.get("apply_url")) in {
                _normalized_url(value) for value in case["accepted_route_urls"]
            }

            # Public egress keeps an inert signature-free display Manifest, but
            # it cannot be re-authenticated as a bearer authority capability.
            assert "authority_tag" not in manifest
            assert "permit_manifest" not in build_permit_manifest_projection(public)

            # A stripped public DTO is not authenticated server-held state. If it
            # is sent back through trusted egress, it must fail closed rather than
            # re-establishing REQUIRED from customer-controlled mirrors.
            untrusted_reentry = server.build_customer_response_egress(
                public,
                case["job_type"],
                case["city"],
                case["state"],
                job_category=case["job_category"],
            )
            assert _token(untrusted_reentry["permit_decision"]) == "VERIFY"
            assert untrusted_reentry["permit_required"] is None
        except Exception as exc:  # aggregate all contract failures in one report
            failures.append({
                "contract_id": case["contract_id"],
                "split": case["split"],
                "error": f"{type(exc).__name__}: {exc}",
            })

    assert failures == []
