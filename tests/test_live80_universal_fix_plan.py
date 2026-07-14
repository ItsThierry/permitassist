import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import server  # noqa: E402

LIVE80 = ROOT / "artifacts" / "live_customer_80_20260628T2153Z" / "cases.jsonl"

CONFIRMED_DEFECTS = {
    "R11": {"must": {"building"}, "must_not": set(), "name_terms": ("short-form", "siding")},
    "R20": {"must": {"building"}, "must_not": {"plumbing", "electrical", "mechanical"}},
    "R25": {"must": {"building"}, "must_not": {"fire", "planning", "co"}},
    "R27": {"must": {"building", "mechanical"}, "must_not": {"electrical"}},
    "R31": {"must": {"electrical"}, "must_not": {"fire", "planning", "co"}},
    "R33": {"must": {"building", "electrical"}, "must_not": set(), "name_terms": ("garage",)},
    "R34": {"must": {"building", "electrical"}, "must_not": {"plumbing"}},
    "R35": {"must": {"plumbing"}, "must_not": {"electrical"}},
    "R38": {"must": {"building"}, "must_not": {"electrical", "plumbing", "planning"}},
    "R40": {"must": {"electrical"}, "must_not": {"fire", "planning", "co"}},
    "C26": {"must": {"building", "electrical"}, "must_not": {"plumbing", "planning"}},
    "C29": {"must": {"building", "electrical", "plumbing", "mechanical"}, "must_not": {"fire", "planning", "co"}},
}

FALSE_POSITIVE_A = {"R01", "R05", "R09", "R10", "R13", "R14", "R19", "R23", "R28", "R29", "R41"}
UNBOUND_NOT_REQUIRED_NEGATIVES = {"R01", "R05", "R09", "R13", "R14", "R41", "C02"}
NO_REGRESSION_GOLDENS = {
    "R02": {"building", "electrical", "plumbing", "mechanical"},  # ADU
    "R03": {"building", "electrical"},  # solar + battery
    "R04": {"plumbing", "electrical"},  # HPWH + new circuit
    "R08": {"electrical", "mechanical", "refrigeration"},  # Seattle mini-split
    "C02": set(),  # Dallas cosmetic office no permit
    "C25": {"sign"},  # commercial sign face-only; building/TI is not hard-required without structural/TI scope
}


def _records():
    if not LIVE80.exists():
        pytest.skip(f"live80 artifact missing: {LIVE80}")
    return [json.loads(line) for line in LIVE80.read_text().splitlines() if line.strip()]


def _by_id():
    return {rec["case"]["id"]: rec for rec in _records()}


def _public(rec):
    case = rec["case"]
    return server.build_customer_permit_view_model(
        copy.deepcopy(rec["response_body"]),
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case.get("segment"),
    )


def _row_status(row):
    return server._pa20_row_status(row) or server._customer_row_status(row)


def _required_rows(public):
    return [
        row for row in public.get("permits_required") or []
        if isinstance(row, dict) and _row_status(row) == "REQUIRED"
    ]


def _family(row):
    return server._pa20_row_family(row) or server._customer_row_family(row)


def _required_families(public):
    return {_family(row) for row in _required_rows(public)}


def _text(public):
    return json.dumps(public, sort_keys=True, default=str).lower()


@pytest.mark.parametrize("case_id,expect", sorted(CONFIRMED_DEFECTS.items()))
def test_live80_confirmed_defects_are_green_at_customer_boundary(case_id, expect):
    rec = _by_id()[case_id]
    public = _public(rec)
    families = _required_families(public)
    assert expect["must"].issubset(families), {"case": case_id, "families": sorted(families), "permit_name": public.get("permit_name")}
    assert not (expect["must_not"] & families), {"case": case_id, "forbidden": sorted(expect["must_not"] & families), "permit_name": public.get("permit_name")}
    for term in expect.get("name_terms", ()):  # under-call fixes must be visible in the customer header/rows
        assert term in _text(public), {"case": case_id, "missing_term": term, "permit_name": public.get("permit_name")}


@pytest.mark.parametrize("case_id", sorted(FALSE_POSITIVE_A))
def test_live80_grader_false_positives_remain_clean_customer_boundary(case_id):
    rec = _by_id()[case_id]
    public = _public(rec)
    case = rec["case"]
    families = _required_families(public)
    expected = set(case.get("expected_families") or [])
    if case.get("expected_decision") == "NOT_REQUIRED":
        if case_id in UNBOUND_NOT_REQUIRED_NEGATIVES:
            # Generic official URLs are contact paths, not exact exemption
            # evidence. R9 must fail these legacy binary negatives closed.
            assert public.get("permit_decision") == "UNKNOWN"
            assert public.get("permit_required") is None
            assert public.get("permit_verdict") == "VERIFY"
            assert not families
            assert all(_row_status(row) == "VERIFY" for row in public.get("permits_required") or [])
        else:
            assert public.get("permit_decision") == "NOT_REQUIRED"
            assert families == set()
            assert public.get("permits_required") == []
    elif expected:
        assert public.get("permit_decision") == "REQUIRED"
        assert families, {"case": case_id, "permit_name": public.get("permit_name")}
    assert not server.lint_customer_visible_result(public, case["city"], case["state"])


@pytest.mark.parametrize("case_id,expected", sorted(NO_REGRESSION_GOLDENS.items()))
def test_live80_no_regression_goldens_preserve_value(case_id, expected):
    rec = _by_id()[case_id]
    public = _public(rec)
    families = _required_families(public)
    if not expected:
        if case_id in UNBOUND_NOT_REQUIRED_NEGATIVES:
            assert public.get("permit_decision") == "UNKNOWN"
            assert public.get("permit_required") is None
            assert public.get("permit_verdict") == "VERIFY"
            assert not families
            assert all(_row_status(row) == "VERIFY" for row in public.get("permits_required") or [])
        else:
            assert public.get("permit_decision") == "NOT_REQUIRED"
            assert families == set()
    else:
        assert expected.issubset(families), {"case": case_id, "families": sorted(families), "permit_name": public.get("permit_name")}


def test_live80_banner_family_set_matches_required_rows_for_all_cases():
    failures = []
    for rec in _records():
        public = _public(rec)
        name = str(public.get("permit_name") or "")
        families = _required_families(public)
        if not name.lower().startswith("multiple permits required:"):
            continue
        header = name.split(":", 1)[1].lower()
        labels = {server._pa20_family_label(f, {}) for f in families}
        header_families = {
            fam for fam in families
            if server._pa20_family_label(fam, {}).lower() in header
        }
        if header_families != families:
            failures.append({"id": rec["case"]["id"], "families": sorted(families), "permit_name": name})
    assert failures == []
