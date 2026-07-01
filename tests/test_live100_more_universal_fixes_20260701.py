import copy
import json
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_real100_more_gpt54_20260630T233000Z"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

pytestmark = pytest.mark.skipif(
    not (ARTIFACT_ROOT / "evidence.jsonl").exists(),
    reason="Live100 more GPT-5.4 artifact bundle is absent; artifact-backed contracts run only in artifact-rich worktrees.",
)


def _install_server_stubs() -> None:
    requests_stub = types.ModuleType("requests")
    requests_stub.post = lambda *a, **k: None
    requests_stub.get = lambda *a, **k: None
    requests_stub.head = lambda *a, **k: types.SimpleNamespace(status_code=200)
    requests_stub.exceptions = types.SimpleNamespace(Timeout=TimeoutError)
    sys.modules["requests"] = requests_stub

    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = lambda *a, **k: object()
    sys.modules["openai"] = openai_stub

    google_stub = types.ModuleType("google")
    genai_stub = types.ModuleType("google.generativeai")
    genai_stub.configure = lambda *a, **k: None
    sys.modules["google"] = google_stub
    sys.modules["google.generativeai"] = genai_stub

    research_stub = types.ModuleType("research_engine")

    def classify_scope_required_permits(job_type, city="", state="", scope_contract=None):
        return []

    research_stub.research_permit = lambda *a, **k: {"permit_verdict": "MAYBE"}
    research_stub.build_google_maps_url = lambda *a, **k: ""
    research_stub.strip_pdf_from_result = lambda result: result
    research_stub.get_cache_hit_rate = lambda: 0
    research_stub.detect_primary_scope = lambda job_type: {"primary_scope": "generic", "signals": []}
    research_stub.classify_scope_required_permits = classify_scope_required_permits
    research_stub.classify_source_tier = lambda url, city="", state="", result=None: "local"
    research_stub.classify_source_authority = lambda url, city="", state="", result=None: {"category": "local_ahj", "tier": "local_ahj", "display_allowed": True}
    sys.modules["research_engine"] = research_stub


@pytest.fixture()
def server(tmp_path, monkeypatch):
    _install_server_stubs()
    monkeypatch.setenv("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    monkeypatch.setenv("FREE_LOOKUP_DB", str(tmp_path / "ip_lookups.db"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    for name in ("server", "api.server"):
        sys.modules.pop(name, None)
    from api import server as server_mod

    server_mod.CACHE_DB = str(tmp_path / "cache.db")
    server_mod.DATA_DIR = str(tmp_path)
    server_mod.init_db()
    return server_mod


def _records() -> list[dict]:
    return [json.loads(line) for line in (ARTIFACT_ROOT / "evidence.jsonl").read_text().splitlines() if line.strip()]


def _failure_records() -> list[dict]:
    return [json.loads(line) for line in (ARTIFACT_ROOT / "failures.jsonl").read_text().splitlines() if line.strip()]


def _failure_case_ids() -> list[str]:
    return [rec["case"]["id"] for rec in _failure_records()]


def _record(case_id: str) -> dict:
    for rec in _records():
        if rec["case"]["id"] == case_id:
            return rec
    raise KeyError(case_id)


def _build_public(server, case_id: str) -> dict:
    rec = _record(case_id)
    case = rec["case"]
    return server.build_customer_permit_view_model(
        copy.deepcopy(rec["response_body"]),
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case.get("segment"),
    )


def _canonical_expected_family(value: str) -> str:
    value = str(value or "").lower().strip().replace("-", "_")
    return {
        "zoning": "planning",
        "land_use": "planning",
        "occupancy": "co",
        "right_of_way": "grading",
        "row": "grading",
    }.get(value, value)


def _row_family(server, row: dict) -> str:
    return str(server._customer_row_family(row) or row.get("filing_family") or row.get("family") or "").lower()


def _row_status(server, row: dict) -> str:
    return str(server._customer_row_status(row) or row.get("status") or row.get("decision") or "").upper()


def _visible_rows(public: dict) -> list[dict]:
    rows: list[dict] = []
    for key in ("permits_required", "related_permits", "companion_permits", "trade_permits"):
        rows.extend([row for row in public.get(key) or [] if isinstance(row, dict)])
    return rows


def _visible_families(server, public: dict) -> set[str]:
    return {_canonical_expected_family(_row_family(server, row)) for row in _visible_rows(public)}


def _required_families(server, public: dict) -> set[str]:
    return {
        _canonical_expected_family(_row_family(server, row))
        for row in public.get("permits_required") or []
        if isinstance(row, dict) and (_row_status(server, row) == "REQUIRED" or row.get("required") is True)
    }


def test_p1_normalizer_is_single_source_of_truth_for_live100_families(server):
    from api.permit_model import PermitFamily, normalize_family

    samples = {
        "solar pv battery": PermitFamily.SOLAR,
        "photovoltaic solarapp": PermitFamily.SOLAR,
        "7 foot fence wall": PermitFamily.FENCE,
        "reroof sheathing roof shingles": PermitFamily.ROOFING,
        "structural steel mezzanine platform": PermitFamily.STRUCTURAL,
        "utility interconnection permission to operate transformer": PermitFamily.UTILITY,
        "planning zoning land use": PermitFamily.PLANNING,
        "health food establishment": PermitFamily.HEALTH,
        "illuminated sign permit": PermitFamily.SIGN,
    }
    for text, expected in samples.items():
        row = {"permit_type": text, "permit_name": text, "approval_type": text}
        assert normalize_family(None, row) == expected
        assert server._pa20_row_family(row) == expected.value
        assert server._customer_row_family(row) == expected.value


@pytest.mark.parametrize("case_id", _failure_case_ids())
def test_frozen_30_failure_contracts_corrected_without_neutering(server, case_id):
    rec = _record(case_id)
    case = rec["case"]
    public = _build_public(server, case_id)
    decision = str(public.get("permit_decision") or "").upper().strip()
    assert decision in {"REQUIRED", "NOT_REQUIRED"}, {"case": case_id, "decision": decision, "public": public}
    assert decision == case["expected_decision"], {"case": case_id, "expected": case["expected_decision"], "public": public}

    expected_families = {_canonical_expected_family(fam) for fam in case.get("expected_families") or []}
    visible = _visible_families(server, public)
    required = _required_families(server, public)
    if decision == "REQUIRED":
        assert public.get("permits_required"), {"case": case_id, "public": public}
        assert expected_families <= visible, {"case": case_id, "missing": expected_families - visible, "visible": visible, "required": required, "public": public}
    else:
        assert not public.get("permits_required"), {"case": case_id, "public": public}


def test_p2_business_archetypes_add_health_without_cosmetic_neutering(server):
    required_cases = {
        "C2-009": {"health"},  # coffee shop
        "C2-015": {"health"},  # salon
        "C2-030": {"health", "fire"},  # daycare
        "C2-034": {"health"},  # bar
        "C2-036": {"health"},  # medical/procedure rooms
    }
    for case_id, families in required_cases.items():
        public = _build_public(server, case_id)
        required = _required_families(server, public)
        assert families <= required, {"case": case_id, "missing": families - required, "required": required, "public": public}


def test_p4_negative_scope_guards_do_not_force_false_required(server):
    for case_id, forbidden in {
        "R2-039": {"grading"},
        "C2-045": {"building", "health", "fire"},
    }.items():
        public = _build_public(server, case_id)
        assert public.get("permit_decision") == "NOT_REQUIRED", {"case": case_id, "public": public}
        assert not (_required_families(server, public) & forbidden), {"case": case_id, "public": public}


def test_main_decision_never_verify_unknown_maybe_across_exact_100(server):
    bad = []
    for rec in _records():
        public = _build_public(server, rec["case"]["id"])
        decision = str(public.get("permit_decision") or "").upper().strip()
        if decision not in {"REQUIRED", "NOT_REQUIRED"}:
            bad.append((rec["case"]["id"], decision))
    assert bad == []
