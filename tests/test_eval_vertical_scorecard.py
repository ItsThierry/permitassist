import json
from pathlib import Path
from importlib import util


def _load_run_eval():
    path = Path(__file__).resolve().parent.parent / "scripts" / "run_eval.py"
    spec = util.spec_from_file_location("permitassist_run_eval", path)
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_eval_pack_has_launch_vertical_coverage():
    data = json.loads((Path(__file__).resolve().parent.parent / "eval" / "permit_eval_cases.json").read_text())
    cases = data["cases"]
    blob_by_scope = {}
    for case in cases:
        blob_by_scope.setdefault(case["scope"], []).append(case)

    assert len(cases) >= 40
    assert len(blob_by_scope.get("commercial_restaurant", [])) >= 10
    assert len(blob_by_scope.get("commercial_medical_clinic_ti", [])) >= 10
    assert len(blob_by_scope.get("commercial_office_ti", [])) >= 10
    assert len(blob_by_scope.get("commercial_retail_ti", [])) >= 4
    assert len([c for c in cases if c["category"] == "residential"]) >= 8


def test_eval_primary_permit_and_confidence_checks_grade_failures():
    run_eval = _load_run_eval()
    case = {
        "id": "bad_commercial",
        "category": "commercial",
        "scope": "commercial_office_ti",
        "city": "Denver",
        "state": "CO",
        "rubric": {
            "must_primary_scope": "commercial_office_ti",
            "must_primary_permit_match": "(commercial|tenant improvement|building permit)",
            "must_primary_permit_not_match": "(residential|hvac only)",
            "must_confidence_not_high_without_sources": True,
            "permits_min": 1,
        },
    }
    response = {
        "_primary_scope": "commercial_office_ti",
        "confidence": "high",
        "permits_required": [{"permit_type": "Residential HVAC Only Permit"}],
        "sources": [],
    }

    result = run_eval.evaluate(case, response)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}

    assert "primary_permit_match" in failed
    assert "primary_permit_not_match" in failed
    assert "confidence_not_high_without_sources" in failed


def test_eval_residential_good_response_not_penalized_for_residential_words():
    run_eval = _load_run_eval()
    case = {
        "id": "good_residential",
        "category": "residential",
        "scope": "residential",
        "city": "Denver",
        "state": "CO",
        "rubric": {
            "must_primary_scope": "residential",
            "must_primary_permit_match": "(plumbing|water heater|residential)",
            "must_primary_permit_not_match": "(commercial|tenant improvement|interior alteration|change of occupancy|type i hood|grease interceptor|medical clinic|office ti|retail ti|restaurant ti)",
            "permits_min": 1,
        },
    }
    response = {
        "_primary_scope": "residential",
        "confidence": "medium",
        "permits_required": [{"permit_type": "Residential Water Heater Plumbing Permit"}],
        "sources": [{"url": "https://denvergov.org/permits"}],
    }

    result = run_eval.evaluate(case, response)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}

    assert failed == set()
    assert result["score"] == 100.0
