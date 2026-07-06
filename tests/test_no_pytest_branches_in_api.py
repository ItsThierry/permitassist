from __future__ import annotations

from pathlib import Path


def test_no_pytest_current_test_branches_in_api() -> None:
    api_dir = Path(__file__).resolve().parents[1] / "api"
    offenders: list[str] = []
    for path in api_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "PYTEST_CURRENT_TEST" in text:
            offenders.append(str(path.relative_to(api_dir.parent)))
    assert offenders == []
