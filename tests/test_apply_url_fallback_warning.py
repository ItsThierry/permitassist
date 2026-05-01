from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "api" / "server.py"


def test_apply_url_fallback_clears_stale_locality_warning_before_response():
    source = SERVER.read_text(encoding="utf-8")
    fallback_assignment = "result['apply_url'] = fallback_url"
    warning_clear = "result.pop('_apply_url_locality_warning', None)"
    assert fallback_assignment in source
    assert warning_clear in source
    assert source.index(fallback_assignment) < source.index(warning_clear)
