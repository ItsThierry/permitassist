from pathlib import Path


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
FORM_SUBMIT_CONTRACT = 'onsubmit="event.preventDefault(); doLookupWithOffline();"'
DIRECT_CLICK_BINDING = "document.getElementById('lookup-btn').onclick = doLookupWithOffline;"


def _html(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


def test_production_homepage_has_one_lookup_submit_owner():
    html = _html("index.html")
    assert FORM_SUBMIT_CONTRACT in html
    assert DIRECT_CLICK_BINDING not in html


def test_preview_homepage_matches_single_submit_contract():
    html = _html("preview-modern-reskinned-index.html")
    assert FORM_SUBMIT_CONTRACT in html
    assert DIRECT_CLICK_BINDING not in html


def test_no_form_owned_lookup_page_also_binds_lookup_button_click():
    offenders = []
    for path in sorted(FRONTEND.rglob("*.html")):
        html = path.read_text(encoding="utf-8")
        if FORM_SUBMIT_CONTRACT in html and DIRECT_CLICK_BINDING in html:
            offenders.append(str(path.relative_to(FRONTEND)))
    assert offenders == []
