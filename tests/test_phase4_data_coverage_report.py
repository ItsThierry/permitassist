from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_phase4_data_coverage_report_runs_local_only(tmp_path) -> None:
    output = tmp_path / "coverage.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "phase4_data_coverage_report.py"), "--output", str(output)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=True,
    )
    assert "ok" in proc.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["network_calls"] is False
    assert report["prod_mutations"] is False
    assert report["lead_or_pii_files_opened"] is False
    assert report["ahj_contacts"]["record_count"] >= 3
    assert report["v24_cells"]["cell_count"] >= 1
    assert "data/verified_cities.db" in report["verified_city_artifacts"]
    assert report["phase4_next_actions"]
