#!/usr/bin/env python3
"""Step 3 test — HTML-escaping double-escape guard (inline, no server import)."""

def esc_html(value) -> str:
    """
    Front-end-safe HTML escape that prevents double-escaping.
    If upstream or a template already encoded bare & as &amp;,
    this preserves it rather than producing &amp;amp;.
    """
    s = str(value or "")
    # Guard against double-escape: preserve already-escaped entities, then escape bare &
    s = s.replace("&amp;", "\x00AMP\x00")
    s = s.replace("&", "&amp;")
    s = s.replace("\x00AMP\x00", "&amp;")
    return s.replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def test_escapes_plain_ampersand():
    assert esc_html("A & B") == "A &amp; B"


def test_preserves_preescaped_amp():
    assert esc_html("Water &amp; Sewer") == "Water &amp; Sewer"
    assert esc_html("A &amp; B &amp; C") == "A &amp; B &amp; C"


def test_mixed_preescaped_and_bare():
    assert esc_html("Both &amp; and & OK") == "Both &amp; and &amp; OK"


if __name__ == "__main__":
    test_escapes_plain_ampersand()
    test_preserves_preescaped_amp()
    test_mixed_preescaped_and_bare()
    print("Step 3 tests: ALL PASSED")
