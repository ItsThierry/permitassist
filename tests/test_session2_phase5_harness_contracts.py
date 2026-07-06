from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_phase5_shared_fixtures_available(degraded_engine, offline_serper, frozen_time, no_network) -> None:
    assert callable(degraded_engine)
    assert offline_serper and offline_serper[0]["url"].startswith("https://www.phoenix.gov")
    assert frozen_time.isoformat().startswith("2026-07-06T12:00:00")
    try:
        socket.create_connection(("example.com", 80), timeout=1)
    except AssertionError as exc:
        assert "network calls are blocked" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("no_network fixture did not block sockets")


def test_run_gates_script_exists_and_lists_session2_contracts() -> None:
    script = ROOT / "scripts" / "run_gates.sh"
    text = script.read_text(encoding="utf-8")
    assert script.exists()
    assert "tests/test_session2_phase3_contracts.py" in text
    assert "tests/test_phase4_data_coverage_report.py" in text
    assert "PERMITASSIST_GATE_ARTIFACT_DIR" in text
    assert "phase4_data_coverage_report.py" in text


def test_frontend_static_lints_cover_tel_raw_url_guard() -> None:
    html_files = [ROOT / "frontend" / "index.html", *sorted((ROOT / "frontend" / "trades").glob("*.html"))]
    assert html_files
    for path in html_files:
        text = path.read_text(encoding="utf-8")
        assert "function looksLikeRawUrl" in text, path
        assert "function safePhoneForDisplay" in text, path
        assert "tel:${normalizePhone(phone)}" in text or "tel:${_esc((phone || '').replace" in text, path


def test_no_tracked_backup_artifacts_are_introduced() -> None:
    proc = subprocess.run(
        ["git", "ls-files", "*.bak", "*.backup"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    tracked = [line for line in proc.stdout.splitlines() if line.strip()]
    assert tracked == []
