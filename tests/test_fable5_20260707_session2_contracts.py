from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

os.environ.setdefault("OPENAI_API_KEY", "test-not-real-openai-key")
os.environ.setdefault("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD", "1")

import server  # noqa: E402
from api.live100_fable5_final_gate import apply_fable5_final_customer_gate  # noqa: E402

FRONTEND_INDEX = ROOT / "frontend" / "index.html"
HARNESS_PATH = ROOT / "scripts" / "live100_fable5_phase0_phase1_harness_20260705.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("live100_harness", HARNESS_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _sha(value: object) -> str:
    if isinstance(value, str):
        raw = value
    else:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_snapshot(root: Path, *, label: str, decision: str = "REQUIRED", permit_required: bool = True, fire_label: str) -> Path:
    snap = root / label
    (snap / "public_json").mkdir(parents=True)
    (snap / "html").mkdir(parents=True)
    public = {
        "permit_decision": decision,
        "permit_required": permit_required,
        "permit_verdict": "YES" if permit_required else "NO",
        "decision_basis": None,
        "confidence_tier": None,
        "degraded_sources": None,
        "permit_name": f"Permit package: Mechanical Permit; {fire_label}",
        "required_permit_names": ["Mechanical Permit", fire_label],
        "customer_next_step": f"File Mechanical Permit and {fire_label}.",
        "sealed_public_packet_hash": "a" * 64,
        "public_packet": {"sealed_public_packet_hash": "b" * 64, "render_seal_hash": "sha256:" + "c" * 64},
    }
    html = f'<script id="report-data" type="application/json">{json.dumps(public, sort_keys=True)}</script>'
    (snap / "public_json" / "044_C-044.json").write_text(json.dumps(public, indent=2, sort_keys=True), encoding="utf-8")
    (snap / "html" / "044_C-044.html").write_text(html, encoding="utf-8")
    manifest = {
        "records": 100,
        "rows": [{"case_id": "C-044", "public_sha256": _sha(public), "html_sha256": _sha(html)}],
    }
    (snap / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return snap


def test_c044_compare_canonicalizes_known_fire_hood_label_flap(tmp_path: Path) -> None:
    harness = _load_harness()
    before = _write_snapshot(tmp_path, label="before", fire_label="Fire Protection Permit — Kitchen Hood Suppression System")
    after = _write_snapshot(tmp_path, label="after", fire_label="Fire / Hood Suppression Permit")
    out = tmp_path / "compare"

    rc = harness.compare(argparse.Namespace(compare_before=before, compare_after=after, out=out))
    report = json.loads((out / "IDENTITY_DIFF_REPORT.json").read_text(encoding="utf-8"))

    assert rc == 0
    assert report["identity_diff_pass"] is True
    assert report["raw_diff_count"] == 2
    assert report["diff_count"] == 0
    assert {d["waived_by"] for d in report["waived_diffs"]} == {"C044_FIRE_HOOD_LABEL_CANONICALIZATION"}


def test_c044_compare_does_not_waive_decision_or_truth_changes(tmp_path: Path) -> None:
    harness = _load_harness()
    before = _write_snapshot(tmp_path, label="before", fire_label="Fire Protection Permit — Kitchen Hood Suppression System")
    after = _write_snapshot(tmp_path, label="after", decision="NOT_REQUIRED", permit_required=False, fire_label="Fire / Hood Suppression Permit")
    out = tmp_path / "compare"

    rc = harness.compare(argparse.Namespace(compare_before=before, compare_after=after, out=out))
    report = json.loads((out / "IDENTITY_DIFF_REPORT.json").read_text(encoding="utf-8"))

    assert rc == 1
    assert report["identity_diff_pass"] is False
    assert report["diff_count"] == 2
    assert report["waived_diffs"][0]["waiver_rejected"]["decision_fields"]["permit_decision"]["same"] is False


def _extract_js_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    in_string = None
    escape = False
    for idx in range(brace, len(source)):
        ch = source[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_string:
                in_string = None
            continue
        if ch in {"'", '"', "`"}:
            in_string = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    raise AssertionError(f"Could not extract {name}")


def _run_frontend_decision(fixture: dict) -> dict:
    html = FRONTEND_INDEX.read_text(encoding="utf-8")
    helpers = "\n".join(_extract_js_function(html, name) for name in ["hasPositiveNoPermitEvidence", "verdictState", "customerFacingDecisionLabel"])
    script = f"""
{helpers}
const fixture = {json.dumps(fixture)};
const verdict = verdictState(fixture);
const label = customerFacingDecisionLabel(fixture.customer_first_screen_summary?.decision || fixture.permit_decision || fixture.permit_verdict, fixture, verdict);
console.log(JSON.stringify({{verdict, label}}));
"""
    completed = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, check=True)
    return json.loads(completed.stdout)


def test_b2_backend_certified_not_required_renders_not_required_on_mobile_first_surface() -> None:
    payload = {
        "permit_required": False,
        "permit_decision": "NOT_REQUIRED",
        "permit_verdict": "NO",
        "permits_required": [],
        "sealed_public_packet_hash": "abc123",
        "public_packet": {"permit_required_verdict": "NOT_REQUIRED", "sealed_public_packet_hash": "abc123"},
        "customer_first_screen_summary": {"decision": "NOT_REQUIRED"},
    }

    rendered = _run_frontend_decision(payload)

    assert rendered == {"verdict": "no", "label": "NOT REQUIRED"}


def _families(public: dict, key: str = "permits_required") -> set[str]:
    return {str(row.get("family") or row.get("filing_family") or "") for row in public.get(key) or [] if isinstance(row, dict)}


def test_b4_generic_service_suite_sink_shower_drain_dishwasher_words_do_not_floor_required_families() -> None:
    base = {"permit_required": False, "permit_decision": "NOT_REQUIRED", "permit_verdict": "NO", "permits_required": [], "related_permits": []}
    job = (
        "commercial office suite service catalog update: sink, shower, drain, and dishwasher product photos only; "
        "no construction, no tenant improvement, no plumbing, no electrical, no mechanical, no food service, no commercial kitchen"
    )

    out = apply_fable5_final_customer_gate(copy.deepcopy(base), job, "Dallas", "TX", {"category": "commercial"})

    assert out["permit_decision"] == "NOT_REQUIRED"
    assert _families(out) == set()


def test_b4_positive_trade_context_still_floors_required_families() -> None:
    base = {"permit_required": False, "permit_decision": "NOT_REQUIRED", "permit_verdict": "NO", "permits_required": [], "related_permits": []}
    job = (
        "commercial restaurant tenant improvement with Type I hood, commercial dishwasher, prep sink, "
        "new floor drain, electrical service upgrade, food service, and grease interceptor"
    )

    out = apply_fable5_final_customer_gate(copy.deepcopy(base), job, "Chicago", "IL", {"category": "commercial"})

    fams = _families(out)
    assert {"building_ti", "electrical", "mechanical", "plumbing", "health_food", "wastewater_pretreatment_fog", "fire_suppression"}.issubset(fams)
    assert out["permit_decision"] == "REQUIRED"


def test_b4_positive_shower_drain_oil_separator_and_circuit_contexts_still_floor_trade_families() -> None:
    scenarios = [
        (
            "commercial fitness studio conversion with showers, locker rooms, HVAC balancing, and sound partitions",
            "commercial",
            {"plumbing", "mechanical", "building_ti", "planning_zoning", "co_change_of_occupancy"},
        ),
        (
            "add two service bays to auto repair shop with lifts oil separator and exhaust ventilation",
            "commercial",
            {"building", "electrical", "mechanical", "plumbing", "wastewater_pretreatment_fog"},
        ),
        (
            "residential kitchen remodel moving sink and dishwasher adding island circuits no structural wall removal",
            "residential",
            {"building", "electrical", "plumbing"},
        ),
        (
            "install refrigerated produce cooler rooms in warehouse with ammonia-free condensing units and drains",
            "commercial",
            {"building_ti", "mechanical", "refrigeration", "plumbing"},
        ),
    ]
    for job, segment, expected in scenarios:
        base = {"permit_required": False, "permit_decision": "NOT_REQUIRED", "permit_verdict": "NO", "permits_required": [], "related_permits": []}
        out = apply_fable5_final_customer_gate(copy.deepcopy(base), job, "Testville", "TX", {"category": segment})
        assert expected.issubset(_families(out)), (job, expected, _families(out))
        assert out["permit_decision"] == "REQUIRED"


def test_b5_residential_dishwasher_replacement_drops_commercial_health_food_and_fog_rows() -> None:
    stale = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permits_required": [
            {"permit_type": "Health Plan Review / Food Establishment Permit", "family": "health_food", "required": True, "decision": "REQUIRED"},
            {"permit_type": "Wastewater / FOG Approval", "family": "wastewater_pretreatment_fog", "required": True, "decision": "REQUIRED"},
            {"permit_type": "Electrical Permit", "family": "electrical", "required": True, "decision": "REQUIRED"},
        ],
        "related_permits": [],
    }
    job = "residential dishwasher replacement in same kitchen location using existing hookup; no commercial kitchen, no restaurant, no food service, no grease, no FOG"

    out = apply_fable5_final_customer_gate(stale, job, "Phoenix", "AZ", {"category": "residential"})
    serialized = json.dumps(out, sort_keys=True, default=str).lower()

    assert "health_food" not in _families(out)
    assert "wastewater_pretreatment_fog" not in _families(out)
    assert "food establishment" not in serialized
    assert "fog" not in serialized
    assert "grease" not in serialized


def test_b6_source_backed_not_required_early_return_is_sealed_or_explicitly_marked(monkeypatch) -> None:
    monkeypatch.setenv("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD", "1")
    result = {
        "permit_required": False,
        "permit_decision": "NOT_REQUIRED",
        "permit_verdict": "NO",
        "permit_name": "No permit required",
        "not_required_reason": "Official source says no permit required for ordinary cosmetic painting with no regulated trade, structural, or life-safety work.",
        "permits_required": [],
        "sources": [{"url": "https://www.phoenix.gov/pdd/development/permits", "title": "Phoenix Planning & Development permits", "source_type": "official_local"}],
        "source_urls": ["https://www.phoenix.gov/pdd/development/permits"],
        "applying_office": "Phoenix Planning & Development",
    }

    public = server.build_customer_permit_view_model(result, "residential interior painting only, no electrical, no plumbing, no structural work", "Phoenix", "AZ", job_category="residential")

    assert public["permit_decision"] == "NOT_REQUIRED"
    sealed = bool(public.get("sealed_public_packet_hash") and (public.get("public_packet") or {}).get("sealed_public_packet_hash") == public.get("sealed_public_packet_hash"))
    explicitly_marked_unsealed = str(public.get("render_seal_status") or public.get("_render_seal_status") or "").startswith("UNSEALED")
    assert sealed or explicitly_marked_unsealed


def test_b6_required_payload_does_not_keep_not_required_unsealed_marker() -> None:
    result = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_name": "Building Permit",
        "permit_kind": "Building",
        "permits_required": [{"permit_type": "Building Permit", "family": "building", "required": True, "decision": "REQUIRED"}],
        "render_seal_status": "UNSEALED_NOT_REQUIRED_CONTRACT",
        "render_seal_reason": "stale marker should not survive required projection",
    }

    public = server.build_customer_permit_view_model(result, "commercial tenant improvement", "Dallas", "TX", job_category="commercial")

    assert public["permit_decision"] == "REQUIRED"
    assert public.get("render_seal_status") != "UNSEALED_NOT_REQUIRED_CONTRACT"


def test_b2_not_required_payload_does_not_leak_required_package_header() -> None:
    result = {
        "permit_required": False,
        "permit_decision": "NOT_REQUIRED",
        "permit_verdict": "NO",
        "permit_name": "No permit required",
        "permit_kind": "Not Required",
        "permits_required": [],
        "package_header": "Multiple permits required: Building + Plumbing",
        "summary": "No permit required for this scope.",
        "sources": [{"url": "https://example.gov/permits", "title": "Official permits", "source_type": "official_local"}],
        "source_urls": ["https://example.gov/permits"],
    }

    public = server.build_customer_permit_view_model(result, "cosmetic repaint only with no regulated trade scope", "Testville", "TX", job_category="residential")

    assert public["permit_decision"] == "NOT_REQUIRED"
    assert public.get("package_header") is None


def test_b6_degraded_fallback_is_explicitly_marked_unsealed() -> None:
    fallback = server._build_degraded_lookup_fallback("install rooftop solar panels", "Phoenix", "AZ", reason="lookup_timeout")

    assert fallback["_runtime_degraded_fallback"]["reason"] == "lookup_timeout"
    assert fallback["_render_seal_status"] == "UNSEALED_DEGRADED_FALLBACK"
