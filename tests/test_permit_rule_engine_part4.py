from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from scripts import generate_permit_rule_engine_part4_evidence as p4e

os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
os.environ.setdefault("FREE_LOOKUP_DB", "/tmp/permitassist-part4-free-lookups.db")

from api import permit_rule_engine as pre
from api.v24_decision_cells import V24Resolution, V24ResolutionStatus, load_v24_index, resolve_v24_cell


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "permit_rule_engine_part4_contract.json"
MANIFEST_PATH = Path(__file__).parent / "fixtures" / "permit_rule_engine_part4_contract_manifest.json"
POISON_BINARY = "POISON_LEGACY_BINARY"
POISON_MARKER = "POISON_INTERNAL_SECRET"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _build_wrapped(
    monkeypatch: pytest.MonkeyPatch,
    *,
    city: str,
    state: str,
    job_type: str,
    job_category: str,
) -> tuple[dict, dict]:
    resolution = resolve_v24_cell(city, state, job_type, job_category, force=True)
    assert resolution.status is V24ResolutionStatus.EXACT_CELL_PUBLISHABLE
    jurisdiction_id = str((resolution.cell or {}).get("jurisdiction_id") or "")
    assert jurisdiction_id
    monkeypatch.setenv(pre.CORE_SETTING, "active")
    monkeypatch.setenv(pre.CORE_ALLOWLIST_SETTING, jurisdiction_id)
    envelope = pre.build_core_decision_envelope(
        resolution,
        job_type=job_type,
        city=city,
        state=state,
        job_category=job_category,
    )
    legacy = {
        "permit_decision": "NOT_REQUIRED",
        "permit_required": False,
        "permit_verdict": "NO",
        "permit_name": POISON_BINARY,
        "summary": POISON_MARKER,
        "_internal_secret": POISON_MARKER,
    }
    wrapped = pre.attach_core_decision_envelope(legacy, envelope)
    sealed = json.loads(envelope.sealed_projection.payload_json)
    return wrapped, sealed


def _tamper(wrapped: dict, case: str) -> dict:
    broken = copy.deepcopy(wrapped)
    if case == "sealed_projection_payload_json":
        broken["_permit_rule_engine_core"]["sealed_projection"]["payload_json"] = "{}"
    elif case == "sealed_projection_payload_sha256":
        broken["_permit_rule_engine_core"]["sealed_projection"]["payload_sha256"] = "0" * 64
    elif case == "envelope_sha256":
        broken["_permit_rule_engine_core"]["envelope_sha256"] = "0" * 64
    elif case == "cache_schema_version":
        broken["_permit_rule_engine_cache_schema_version"] = "stale-schema"
    else:  # pragma: no cover - fixture contract protects this branch
        raise AssertionError(case)
    return broken


def _assert_no_poison(value: object) -> None:
    rendered = json.dumps(value, sort_keys=True, default=str)
    assert POISON_BINARY not in rendered
    assert POISON_MARKER not in rendered


def test_part4_frozen_contract_hashes_match_manifest() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for relative_path, expected_hash in manifest["sha256"].items():
        path = Path(__file__).parents[1] / relative_path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash


def test_part4_flag_off_remains_exact_same_object_and_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(pre.CORE_SETTING, raising=False)
    monkeypatch.delenv(pre.CORE_ALLOWLIST_SETTING, raising=False)
    legacy = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "nested": {"order": [3, 2, 1]},
    }
    before = json.dumps(legacy, sort_keys=False, separators=(",", ":"), ensure_ascii=False).encode()
    output = pre.maybe_attach_core_decision_envelope(
        legacy,
        job_type="residential reroof",
        city="Buckeye",
        state="AZ",
        job_category="residential",
    )
    after = json.dumps(output, sort_keys=False, separators=(",", ":"), ensure_ascii=False).encode()
    assert output is legacy
    assert before == after


def test_part4_activation_requires_exact_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pre.CORE_SETTING, "active")
    monkeypatch.setenv(pre.CORE_ALLOWLIST_SETTING, "us-az-buckeye")
    legacy = {"permit_decision": "REQUIRED"}
    assert pre.maybe_attach_core_decision_envelope(
        legacy,
        job_type="residential reroof",
        city="Phoenix",
        state="AZ",
        job_category="residential",
    ) is legacy
    activated = pre.maybe_attach_core_decision_envelope(
        legacy,
        job_type="residential reroof",
        city="Buckeye",
        state="AZ",
        job_category="residential",
    )
    assert activated is not legacy
    assert pre.validate_rule_engine_cache_payload(activated, required_version=pre.CORE_CACHE_SCHEMA_VERSION)


@pytest.mark.parametrize("tamper_case", _fixture()["tamper_cases"])
def test_part4_tampered_active_payload_fails_closed_without_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tamper_case: str,
) -> None:
    wrapped, _sealed = _build_wrapped(
        monkeypatch,
        city="Buckeye",
        state="AZ",
        job_type="residential reroof",
        job_category="residential",
    )
    broken = _tamper(wrapped, tamper_case)
    projection = pre.project_core_customer_boundary(
        broken,
        job_type="residential reroof",
        city="Buckeye",
        state="AZ",
        job_category="residential",
    )
    assert projection is not None
    assert projection["permit_decision"] == _fixture()["required_fail_closed_decision"]
    assert projection["permit_verdict"] == _fixture()["required_fail_closed_verdict"]
    assert projection["permit_required"] is None
    assert projection["family_decisions"] == [
        {
            "family": "building",
            "verdict": "ABSTAIN",
            "trigger": "decision_integrity_validation_failed",
            "authority": "",
            "apply_url": "",
            "validation_issue_codes": ["decision_integrity_validation_failed"],
        }
    ]
    _assert_no_poison(projection)


def test_part4_valid_active_payload_stays_exactly_sealed(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapped, sealed = _build_wrapped(
        monkeypatch,
        city="Buckeye",
        state="AZ",
        job_type="residential reroof",
        job_category="residential",
    )
    projection = pre.project_core_customer_boundary(
        wrapped,
        job_type="residential reroof",
        city="Buckeye",
        state="AZ",
        job_category="residential",
    )
    assert projection == sealed
    _assert_no_poison(projection)


def test_part4_tampered_w4_keeps_all_ten_lanes_visible_as_abstain(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapped, _sealed = _build_wrapped(
        monkeypatch,
        city="Anchorage",
        state="AK",
        job_type="commercial tenant improvement",
        job_category="commercial",
    )
    projection = pre.project_core_customer_boundary(
        _tamper(wrapped, "sealed_projection_payload_json"),
        job_type="commercial tenant improvement",
        city="Anchorage",
        state="AK",
        job_category="commercial",
    )
    expected_families = _fixture()["w4_family_order"]
    assert [row["family"] for row in projection["family_decisions"]] == expected_families
    assert [row["family"] for row in projection["verification_tasks"]] == expected_families
    assert {row["verdict"] for row in projection["family_decisions"]} == {"ABSTAIN"}
    assert len(projection["permits_required"]) == 10
    _assert_no_poison(projection)


def test_part4_factory_exception_and_unsupported_scope_remain_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pre, "classify_v24_seed", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    seed = pre.safe_factory_migrate_seed(
        "XX|example|unsupported",
        {"jurisdiction_id": "us-xx-example", "ahj": "Example", "state": "XX", "project_family": "unsupported"},
    )
    assert seed.classification is pre.SeedClassification.FAIL_CLOSED
    assert seed.binary_families == ()
    assert seed.issue_codes == ("factory_exception",)
    assert pre.classify_request_scope("unmapped quantum containment scope") is pre.SeedClassification.UNSUPPORTED_SCOPE


def test_part4_unsupported_scope_survives_second_customer_projection_without_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import server

    monkeypatch.setenv(pre.CORE_SETTING, "active")
    monkeypatch.setenv(pre.CORE_ALLOWLIST_SETTING, "us-az-buckeye")
    resolution = resolve_v24_cell(
        "Buckeye", "AZ", "interior painting only", "residential", force=True
    )
    assert resolution.status is V24ResolutionStatus.AMBIGUOUS_ABSTAIN
    legacy = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_name": POISON_BINARY,
        "summary": POISON_MARKER,
    }

    wrapped = pre.maybe_attach_core_decision_envelope(
        legacy,
        job_type="interior painting only",
        city="Buckeye",
        state="AZ",
        job_category="residential",
    )
    first_projection = server.finalize_permit_lookup_result(
        wrapped,
        job_type="interior painting only",
        city="Buckeye",
        state="AZ",
        job_category="residential",
    )
    cached_projection = server.finalize_permit_lookup_result(
        wrapped,
        job_type="interior painting only",
        city="Buckeye",
        state="AZ",
        job_category="residential",
        is_cached=True,
    )
    second_projection = server.build_customer_permit_view_model(
        first_projection,
        "interior painting only",
        "Buckeye",
        "AZ",
        "residential",
    )

    assert first_projection["permit_decision"] == "UNKNOWN"
    assert cached_projection == first_projection
    assert second_projection["permit_decision"] == "UNKNOWN"
    assert second_projection["permit_required"] is None
    assert second_projection["permit_verdict"] == "VERIFY"
    assert [row["family"] for row in second_projection["family_decisions"]] == sorted(pre._CORE_FAMILIES)
    assert {row["verdict"] for row in second_projection["family_decisions"]} == {"ABSTAIN"}
    assert len(second_projection["verification_tasks"]) == len(pre._CORE_FAMILIES)
    _assert_no_poison(second_projection)

    unsupported_envelope = pre.build_core_decision_envelope(
        V24Resolution(
            V24ResolutionStatus.AHJ_COVERED_PROJECT_NOT_COVERED,
            key="AZ|buckeye|unsupported",
            reason="AHJ covered but exact project family is unsupported",
        ),
        job_type="unmapped quantum containment scope",
        city="Buckeye",
        state="AZ",
        job_category="residential",
    )
    unsupported_projection = server.finalize_permit_lookup_result(
        pre.attach_core_decision_envelope(copy.deepcopy(legacy), unsupported_envelope),
        "unmapped quantum containment scope",
        "Buckeye",
        "AZ",
        job_category="residential",
    )
    assert unsupported_projection["permit_decision"] == "UNKNOWN"
    assert [row["family"] for row in unsupported_projection["family_decisions"]] == sorted(pre._CORE_FAMILIES)
    assert {row["verdict"] for row in unsupported_projection["family_decisions"]} == {"ABSTAIN"}
    _assert_no_poison(unsupported_projection)


@pytest.mark.parametrize(
    "snippet",
    ["", "The city adopted the 2024 building code for construction activity."],
)
def test_part4_customer_not_required_source_floor_demotes_unbound_official_urls(snippet: str) -> None:
    from api import server

    raw = {
        "permit_decision": "NOT_REQUIRED",
        "permit_required": False,
        "permit_verdict": "NO",
        "permit_name": "No permit required",
        "permit_type": "No permit required",
        "permit_kind": "Not Required",
        "not_required_reason": "No permit is required for this exact maintenance scope.",
        "summary": "No permit required for the described scope.",
        "sources": [
            {
                "url": "https://code.mecknc.gov/permitting",
                "title": "Official Charlotte source",
                "snippet": snippet,
            }
        ],
        "source_urls": ["https://code.mecknc.gov/permitting"],
        "claim_citations": [],
    }
    views = []
    for is_cached in (False, True):
        finalized = server.finalize_permit_lookup_result(
            copy.deepcopy(raw),
            "Replace kitchen faucet and garbage disposal only; no wall relocation, no new circuits, and no structural work",
            "Charlotte",
            "NC",
            is_cached=is_cached,
            job_category="residential",
        )
        views.append(server.build_customer_permit_view_model(
            finalized,
            "Replace kitchen faucet and garbage disposal only; no wall relocation, no new circuits, and no structural work",
            "Charlotte",
            "NC",
            "residential",
        ))
    assert views[0] == views[1]
    prefinalized_legacy_cache = copy.deepcopy(raw)
    prefinalized_legacy_cache["customer_result_summary"] = {"decision": "NOT_REQUIRED"}
    prefinalized_legacy_cache["customer_first_screen_summary"] = {"headline": "No permit required"}
    cache_hit_view = server.build_customer_permit_view_model(
        prefinalized_legacy_cache,
        "Replace kitchen faucet and garbage disposal only; no wall relocation, no new circuits, and no structural work",
        "Charlotte",
        "NC",
        "residential",
    )
    assert cache_hit_view["permit_decision"] == "UNKNOWN"
    assert cache_hit_view["permit_required"] is None
    cache_hit_view_again = server.build_customer_permit_view_model(
        cache_hit_view,
        "Replace kitchen faucet and garbage disposal only; no wall relocation, no new circuits, and no structural work",
        "Charlotte",
        "NC",
        "residential",
    )
    assert cache_hit_view_again == cache_hit_view
    view = views[0]

    assert view["permit_decision"] == "UNKNOWN"
    assert view["permit_required"] is None
    assert view["permit_verdict"] == "VERIFY"
    assert view.get("claim_citations") in (None, [])
    assert view["permits_required"]
    assert all(row.get("required") not in {True, False} for row in view["permits_required"])
    assert all(str(row.get("status") or row.get("required_status") or "").upper() == "VERIFY" for row in view["permits_required"])
    assert view["source_urls"] == ["https://code.mecknc.gov/permitting"]
    assert view["apply_path"]["channel"] == "contact_ahj"
    serialized = json.dumps(view, sort_keys=True).lower()
    for stale_claim in (
        "no permit required",
        "no permit submission needed",
        "no permit fee expected",
        "no permit inspection",
    ):
        assert stale_claim not in serialized


def test_part4_customer_not_required_source_floor_preserves_claim_linked_official_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import server
    from api.research_engine import classify_source_authority as real_classify_source_authority

    # Several legacy suites intentionally install a top-level research_engine
    # stub. Pin this gate to the production authority classifier so the frozen
    # contract is order-independent rather than accepting a test-only stub.
    monkeypatch.setattr(server, "classify_source_authority", real_classify_source_authority)

    decision = "NOT_REQUIRED"
    required = False
    verdict = "NO"
    quote = "No permit is required for an in-kind fixture replacement with no piping changes."
    url = "https://www.buckeyeaz.gov/business/development-services/permit-center"
    raw = {
        "permit_decision": decision,
        "permit_required": required,
        "permit_verdict": verdict,
        "not_required_reason": quote,
        "claim_citations": [{
            "id": "C1",
            "field": "permit_decision",
            "claim": "Permit requirement decision",
            "value": decision,
            "source_url": url,
            "source_title": "City of Buckeye Permit Center",
            "quoted_snippet": quote,
            "checked_at": "2026-07-13",
            "confidence": "high",
        }],
        "sources": [{"url": url, "title": "City of Buckeye Permit Center", "snippet": quote}],
        "source_urls": [url],
    }

    gated = server.enforce_unbound_not_required_source_floor(
        raw,
        "residential reroof",
        "Buckeye",
        "AZ",
    )
    assert gated["permit_decision"] == decision
    assert gated["permit_required"] is required
    assert gated["permit_verdict"] == verdict

    first_public = server.build_customer_permit_view_model(
        raw,
        "in-kind fixture replacement with no piping changes",
        "Buckeye",
        "AZ",
        "residential",
    )
    second_public = server.build_customer_permit_view_model(
        first_public,
        "in-kind fixture replacement with no piping changes",
        "Buckeye",
        "AZ",
        "residential",
    )
    assert first_public["permit_decision"] == "NOT_REQUIRED"
    assert first_public["permit_required"] is False
    assert second_public["permit_decision"] == "NOT_REQUIRED"
    assert second_public["permit_required"] is False


def test_part4_authoritative_not_required_cell_remains_idempotent_after_public_cache_projection() -> None:
    from api import server

    url = "https://www.buckeyeaz.gov/business/development-services/permit-center"
    raw = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_name": POISON_BINARY,
        "_decision_cell_primary_lock": {
            "source": "permitassist_v231_decision_cell",
            "exact_match": True,
            "permit_decision": "NOT_REQUIRED",
            "customer_action": "No permit is required for this exact in-kind maintenance scope.",
            "source_urls": [url],
            "sources": [{"url": url, "title": "City of Buckeye Permit Center"}],
        },
    }
    first = server.build_customer_permit_view_model(
        raw,
        "in-kind fixture maintenance with no piping or wiring changes",
        "Buckeye",
        "AZ",
        "residential",
    )
    second = server.build_customer_permit_view_model(
        first,
        "in-kind fixture maintenance with no piping or wiring changes",
        "Buckeye",
        "AZ",
        "residential",
    )

    third = server.build_customer_permit_view_model(
        second,
        "in-kind fixture maintenance with no piping or wiring changes",
        "Buckeye",
        "AZ",
        "residential",
    )

    assert first["permit_decision"] == "NOT_REQUIRED"
    assert first["permit_required"] is False
    assert first["data_source"] == "Official permit authority decision rule"
    assert first["source_urls"] == [url]
    assert second["permit_decision"] == "NOT_REQUIRED"
    assert second["permit_required"] is False
    assert second["data_source"] == "Official permit authority decision rule"
    assert third["permit_decision"] == "NOT_REQUIRED"
    assert third["permit_required"] is False
    assert third["data_source"] == "Official permit authority decision rule"


def test_part4_ambiguous_jurisdiction_never_activates(monkeypatch: pytest.MonkeyPatch) -> None:
    index = load_v24_index() or {}
    duplicate = copy.deepcopy(next(iter(index.values())))
    duplicate["jurisdiction_id"] = "us-duplicate-boundary"
    duplicate["cell_id"] = "duplicate-boundary-cell"
    synthetic = {"A": next(iter(index.values())), "B": duplicate}
    seeds = pre.migrate_v24_seed_index(index=synthetic)
    assert {seed.classification for seed in seeds.values()} == {pre.SeedClassification.JURISDICTION_HOLD}
    assert all(seed.binary_families == () for seed in seeds.values())


def test_part4_server_surfaces_use_one_safe_projection_for_tampered_active_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from api import server

    server.CACHE_DB = str(tmp_path / "part4.db")
    server.DATA_DIR = str(tmp_path)
    server.init_db()
    wrapped, _sealed = _build_wrapped(
        monkeypatch,
        city="Anchorage",
        state="AK",
        job_type="commercial tenant improvement",
        job_category="commercial",
    )
    broken = _tamper(wrapped, "sealed_projection_payload_json")
    expected = pre.project_core_customer_boundary(
        broken,
        job_type="commercial tenant improvement",
        city="Anchorage",
        state="AK",
        job_category="commercial",
    )
    view = server.build_customer_permit_view_model(
        broken,
        "commercial tenant improvement",
        "Anchorage",
        "AK",
        "commercial",
    )
    assert view == expected
    checklist = server.get_or_create_checklist(
        broken,
        "commercial tenant improvement",
        "Anchorage",
        "AK",
    )
    assert [item["category"] for item in checklist["items"]] == _fixture()["w4_family_order"]
    assert {item["required"] for item in checklist["items"]} == {False}
    report_html = server.render_white_label_report_html(
        {
            "result": broken,
            "job_type": "commercial tenant improvement",
            "city": "Anchorage",
            "state": "AK",
        }
    )
    report_text = BeautifulSoup(report_html, "html.parser").get_text(" ", strip=True)
    for family in _fixture()["w4_family_order"]:
        assert f"{family.title()} Permit" in report_text
    _assert_no_poison({"view": view, "checklist": checklist, "report_html": report_html})


def test_part4_share_storage_hash_seals_and_preserves_exact_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from api import server

    server.CACHE_DB = str(tmp_path / "part4-share.db")
    server.DATA_DIR = str(tmp_path)
    server.init_db()
    wrapped, sealed = _build_wrapped(
        monkeypatch,
        city="Buckeye",
        state="AZ",
        job_type="residential reroof",
        job_category="residential",
    )
    slug = server.create_share("residential reroof", "Buckeye", "AZ", wrapped)
    share = server.get_share(slug)
    assert share is not None
    expected_embedded = {
        key: value
        for key, value in sealed.items()
        if key not in _fixture()["report_embed_forbidden_fields"]
    }
    assert share["data"] == expected_embedded
    html = server.render_share_page(share)
    assert POISON_BINARY not in html
    assert POISON_MARKER not in html
    soup = BeautifulSoup(html, "html.parser")
    payload_node = soup.find("script", {"id": "report-data"})
    assert payload_node is not None
    payload = json.loads(payload_node.string or "{}")
    assert payload["share"]["data"] == expected_embedded
    assert not set(_fixture()["report_embed_forbidden_fields"]) & set(payload["share"]["data"])


def test_part4_tampered_shared_storage_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from api import server
    import sqlite3

    server.CACHE_DB = str(tmp_path / "part4-share-tamper.db")
    server.DATA_DIR = str(tmp_path)
    server.init_db()
    wrapped, _sealed = _build_wrapped(
        monkeypatch,
        city="Buckeye",
        state="AZ",
        job_type="residential reroof",
        job_category="residential",
    )
    slug = server.create_share("residential reroof", "Buckeye", "AZ", wrapped)
    with sqlite3.connect(server.CACHE_DB) as conn:
        raw = conn.execute("SELECT result_json FROM shared_results WHERE slug=?", [slug]).fetchone()[0]
        stored = json.loads(raw)
        assert stored["schema_version"] == _fixture()["shared_result_schema_version"]
        stored["payload_json"] = "{}"
        conn.execute("UPDATE shared_results SET result_json=? WHERE slug=?", [json.dumps(stored), slug])
        conn.commit()
    assert server.get_share(slug) is None


def test_part4_evidence_is_byte_stable_across_utc_date_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_before = tmp_path / "before-midnight"
    run_after = tmp_path / "after-midnight"
    monkeypatch.setattr(
        p4e.server,
        "utc_now",
        lambda: datetime(2026, 7, 12, 23, 59, 59, tzinfo=timezone.utc),
    )
    p4e.generate(run_before, "test-source-commit")
    monkeypatch.setattr(
        p4e.server,
        "utc_now",
        lambda: datetime(2026, 7, 13, 0, 0, 1, tzinfo=timezone.utc),
    )
    p4e.generate(run_after, "test-source-commit")

    before = {path.name: path.read_bytes() for path in sorted(run_before.iterdir())}
    after = {path.name: path.read_bytes() for path in sorted(run_after.iterdir())}
    assert before == after
