import copy
import json
from importlib import util
from pathlib import Path

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper_phase7d_contract",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "api" / "evidence_pack_registry.v1.json"
GOLDEN_PACK = REPO_ROOT / "data" / "evidence_packs" / "local_golden" / "phase7b" / "permitassist-phase7b-golden-local-preview-pack-PA7B-HYBRID10-20260511.json"


def test_phase7d_runtime_contract_is_registry_backed():
    from api import evidence_pack_runtime as runtime

    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    assert registry["schema"] == "permitassist.evidence_pack.registry.v1"
    assert runtime.RUNTIME_CONTRACT_SCHEMA == registry["runtime_contract_schema"]
    assert runtime.RUNTIME_CONTRACT_VERSION == registry["runtime_contract_version"]
    assert set(runtime.SUPPORTED_FIELDS) == set(registry["supported_fields"])
    assert set(runtime.SUPPORTED_EVIDENCE_PACK_VERSIONS) == set(registry["pack_versions"])
    assert runtime.ALLOWED_EVIDENCE_PACK_MODES == set(registry["modes"])
    assert runtime.EVIDENCE_PACK_VERSION_BY_MODE == {
        mode: config["expected_version"]
        for mode, config in registry["modes"].items()
        if config.get("expected_version")
    }
    assert runtime.PRODUCTION_WIRING_ALLOWED_BY_VERSION == {
        version: config["production_wiring_allowed"]
        for version, config in registry["pack_versions"].items()
    }


def test_phase7d_manifest_validates_existing_golden_pack_contract():
    from api import evidence_pack_runtime as runtime

    data = json.loads(GOLDEN_PACK.read_text(encoding="utf-8"))
    warnings = runtime._validate_manifest_promotion(
        data["metadata"]["mode"],
        GOLDEN_PACK,
        GOLDEN_PACK.read_bytes(),
        data,
        data["metadata"],
    )
    assert warnings == ()

    wrong = copy.deepcopy(data)
    wrong["metadata"]["batch_id"] = "WRONG-BATCH"
    warnings = runtime._validate_manifest_promotion(
        wrong["metadata"]["mode"],
        GOLDEN_PACK,
        GOLDEN_PACK.read_bytes(),
        wrong,
        wrong["metadata"],
    )
    assert "phase7b_golden_batch_id_mismatch" in warnings


def test_phase7d_public_redaction_is_policy_based_not_mode_name_based(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    public = server.redact_public_output({
        "ok": True,
        "_evidence_pack": {
            "enabled": True,
            "mode": "future_registry_pack_preview",
            "public_redaction": "drop_evidence_pack",
            "local_golden": {"row_id": "internal"},
        },
    })
    assert public == {"ok": True}

    redacted = server.redact_public_output({
        "_evidence_pack": {
            "enabled": True,
            "mode": "future_registry_pack_preview",
            "public_redaction": "redact_internal_fields",
            "fingerprint_sha256": "a" * 64,
            "local_golden": {"row_id": "internal"},
        },
    })
    assert "_evidence_pack" in redacted
    assert redacted["_evidence_pack"]["fingerprint_sha256"] == "[REDACTED]"
    assert "local_golden" not in redacted["_evidence_pack"]
