from pathlib import Path


FRONTEND_INDEX = Path(__file__).resolve().parents[1] / "frontend" / "index.html"


def _frontend_source() -> str:
    return FRONTEND_INDEX.read_text(encoding="utf-8")


def test_standard_result_view_keeps_short_guidance_without_big_warning_block():
    source = _frontend_source()

    assert "PermitAssist is guidance only" in source
    assert "uncertainty-warning-card" not in source
    assert "Verify before relying on this result" not in source


def test_report_view_keeps_short_guidance_without_loud_uncertainty_rollup():
    source = _frontend_source()

    report_header_pos = source.index("PermitAssist Report v1.0")
    guidance_pos = source.index("PermitAssist is guidance only", report_header_pos)
    first_field_pos = source.index("// The 18 fields — locked order")
    assert report_header_pos < guidance_pos < first_field_pos
    assert "Report needs verification" not in source
    assert "This warning is intentionally shown in report/print view" not in source


def test_copy_result_preserves_short_guidance_only():
    source = _frontend_source()

    assert "PERMITASSIST GUIDANCE ONLY" in source
    assert "VERIFY BEFORE RELYING:" not in source
    assert "Uncertainty / verification warnings" not in source
