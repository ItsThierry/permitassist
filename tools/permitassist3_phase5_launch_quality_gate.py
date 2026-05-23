#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""Run PermitAssist 3.0 Phase 5 launch quality gate."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
sys.path.insert(0, str(API))

from permitassist3_revised import PermitAssist3LaunchQualityGate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=str(ROOT / "artifacts" / "permitassist3_phase5_launch_quality_gate_report.json"))
    args = parser.parse_args()
    report = PermitAssist3LaunchQualityGate(report_path=Path(args.report)).run()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["acceptance"].get("phase5_launch_quality_gate_pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
