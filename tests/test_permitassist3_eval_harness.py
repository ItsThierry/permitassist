import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_phase0_1_eval_harness_acceptance(tmp_path):
    report = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "permitassist3_eval.py"), "--report", str(report)],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=True,
    )
    summary = json.loads(report.read_text(encoding="utf-8"))
    assert summary["case_count"] == 100
    assert summary["passed_count"] == 100
    assert summary["failed_count"] == 0
    assert summary["holdout_case_count"] == 18
    assert summary["holdout_pass_count"] == 18
    assert summary["holdout_pass_rate"] == 1.0
    assert summary["acceptance"]["holdout_pass_rate_at_least_0_80"] is True
    assert all(summary["acceptance"].values())
    assert "Manual filing path confirmation in progress" not in proc.stdout
    assert "exact permit type needs AHJ verification" not in proc.stdout
