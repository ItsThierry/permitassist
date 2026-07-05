import copy
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_full_customer_pov_clean_5946c7c_20260704T234342Z"
FREEZE_PATH = ROOT / "tests" / "fixtures" / "live100_ab_green_freeze_20260705.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

pytestmark = pytest.mark.skipif(
    not (ARTIFACT_ROOT / "cases.jsonl").exists() or not FREEZE_PATH.exists(),
    reason="Live100 full customer artifact/baseline is absent; A/B green-freeze runs only in artifact-rich worktrees.",
)


def _install_server_stubs() -> None:
    requests_stub = types.ModuleType("requests")
    requests_stub.post = lambda *a, **k: None
    requests_stub.get = lambda *a, **k: None
    requests_stub.head = lambda *a, **k: types.SimpleNamespace(status_code=200)
    requests_stub.exceptions = types.SimpleNamespace(Timeout=TimeoutError)
    sys.modules.setdefault("requests", requests_stub)

    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = lambda *a, **k: object()
    sys.modules.setdefault("openai", openai_stub)

    google_stub = types.ModuleType("google")
    genai_stub = types.ModuleType("google.generativeai")
    genai_stub.configure = lambda *a, **k: None
    sys.modules.setdefault("google", google_stub)
    sys.modules.setdefault("google.generativeai", genai_stub)


@pytest.fixture()
def server(tmp_path, monkeypatch):
    _install_server_stubs()
    monkeypatch.setenv("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    monkeypatch.setenv("FREE_LOOKUP_DB", str(tmp_path / "ip_lookups.db"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD", "1")
    for name in ("server", "api.server"):
        sys.modules.pop(name, None)
    from api import server as server_mod

    server_mod.CACHE_DB = str(tmp_path / "cache.db")
    server_mod.DATA_DIR = str(tmp_path)
    server_mod.init_db()
    return server_mod


def _records() -> dict[str, dict]:
    return {
        json.loads(line)["case"]["id"]: json.loads(line)
        for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines()
        if line.strip()
    }


def _families(public: dict) -> list[str]:
    return sorted({
        str(row.get("filing_family") or row.get("family") or "")
        for row in public.get("permits_required") or []
        if isinstance(row, dict) and str(row.get("filing_family") or row.get("family") or "").strip()
    })


def test_live100_ab_green_freeze_no_decision_family_or_action_regression(server):
    """Freeze the 34 A + 43 B rows so future fixes cannot silently recreate B→C/F drift."""
    freeze = json.loads(FREEZE_PATH.read_text())
    records = _records()
    assert len(freeze["cases"]) == 77
    for case_id, expected in freeze["cases"].items():
        rec = records[case_id]
        case = rec["case"]
        public = server.build_customer_permit_view_model(
            copy.deepcopy(rec["response_body"]),
            case["job_type"],
            case["city"],
            case["state"],
            job_category=case.get("segment"),
        )
        assert str(public.get("permit_decision") or "") == expected["decision"], {"case": case_id, "public": public}
        assert _families(public) == expected["required_families"], {"case": case_id, "public": public, "expected": expected}
        if expected["decision"] == "REQUIRED":
            has_action_or_source = bool(
                public.get("apply_url")
                or public.get("source_urls")
                or (isinstance(public.get("apply_path"), dict) and public["apply_path"].get("office_name"))
            )
            assert has_action_or_source == expected["has_action_or_source"], {"case": case_id, "public": public, "expected": expected}


def test_trusted_missing_filing_path_repairs_are_city_and_state_keyed(server, monkeypatch):
    """A same-name different-state AHJ must not receive another state's trusted fallback URL."""
    repairs = dict(server._LIVE100_OFFICIAL_FILING_PATH_REPAIRS)
    repairs[("aurora", "il")] = {
        "apply_url": "https://www.aurora-il.org/permit-center",
        "source_urls": ["https://www.aurora-il.org/permit-center"],
        "title": "Aurora IL Permit Center",
        "trusted_missing_filing_path": True,
    }
    monkeypatch.setattr(server, "_LIVE100_OFFICIAL_FILING_PATH_REPAIRS", repairs)
    result = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_name": "Fire Sprinkler Permit",
        "permits_required": [{"permit_name": "Fire Sprinkler Permit", "family": "fire_suppression", "decision": "REQUIRED", "required": True}],
        "source_urls": [],
    }
    repaired = server._apply_live100_official_filing_path_repair(result, "Aurora", "CO", "modify existing fire sprinkler heads")
    assert not repaired.get("apply_url"), repaired
    assert "aurora-il.org" not in json.dumps(repaired).lower(), repaired
