from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
os.environ.setdefault("FREE_LOOKUP_DB", "/tmp/permitassist-part4-free-lookups.db")

from api import permit_rule_engine as pre
from api.v24_decision_cells import V24ResolutionStatus, load_v24_index, resolve_v24_cell


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
    assert share["data"] == sealed
    html = server.render_share_page(share)
    assert POISON_BINARY not in html
    assert POISON_MARKER not in html
    soup = BeautifulSoup(html, "html.parser")
    payload_node = soup.find("script", {"id": "report-data"})
    assert payload_node is not None
    payload = json.loads(payload_node.string or "{}")
    expected_embedded = {
        key: value
        for key, value in sealed.items()
        if key not in _fixture()["report_embed_forbidden_fields"]
    }
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
