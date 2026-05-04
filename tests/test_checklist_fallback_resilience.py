from importlib import util
from pathlib import Path

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server


def test_checklist_fallback_accepts_string_shaped_result_fields(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permits_required": "Building Permit — Tenant Improvement / Office Interior Alteration",
        "fee_range": "$2,500–$8,000",
        "approval_timeline": "2–6 weeks",
        "what_to_bring": "Tenant improvement drawings",
        "requirements": [{"label": "Accessibility/path-of-travel notes"}],
        "documents_needed": {"title": "Existing floor plan"},
        "inspections": ["Final building inspection", {"stage": "Rough MEP", "timing": "Before cover"}],
        "pro_tips": {"description": "Confirm exact submittal checklist with the AHJ"},
        "common_mistakes": ["Starting before permit issuance"],
    }

    checklist = server.build_checklist_fallback(result, "office tenant improvement", "Boston", "MA")
    labels = "\n".join(item["label"] for item in checklist["items"])

    assert checklist["title"] == "Pre-Construction Compliance Checklist"
    assert "Building Permit — Tenant Improvement / Office Interior Alteration" in labels
    assert "Tenant improvement drawings" in labels
    assert "Final building inspection" in labels
    assert "Rough MEP" in labels
    assert "Confirm exact submittal checklist with the AHJ" in labels


def test_get_or_create_checklist_does_not_500_on_string_shaped_fields(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permits_required": ["Building Permit — Tenant Improvement / Office Interior Alteration"],
        "approval_timeline": "2–6 weeks",
        "what_to_bring": "Tenant improvement drawings",
        "inspections": ["Final building inspection"],
    }

    checklist = server.get_or_create_checklist(result, "office tenant improvement", "Boston", "MA")

    assert checklist["items"]
    assert checklist["cached"] is False
    assert checklist["result_hash"]
