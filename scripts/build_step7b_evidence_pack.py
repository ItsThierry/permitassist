#!/usr/bin/env python3
"""Build the local/offline Step 7B Evidence Pack artifacts.

No production API, server wiring, deploy, secrets, or network source fetching.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.evidence_pack_tool import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PROJECT_BRIEFS_DIR,
    build_evidence_pack,
    discover_step6a_artifacts,
    evidence_pack_tool_readiness,
    write_evidence_pack_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build PermitAssist Step 7B offline evidence pack outputs")
    parser.add_argument("--project-briefs-dir", default=str(DEFAULT_PROJECT_BRIEFS_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--stem", default=None)
    parser.add_argument("--validate-existing-pack", default=None, help="Read-only readiness validation for an existing evidence-pack JSON")
    parser.add_argument("--report-path", default=None, help="Optional existing Markdown report path for --validate-existing-pack")
    parser.add_argument("artifacts", nargs="*", help="Optional explicit Step 6A evidence-upgrade JSON files")
    args = parser.parse_args()

    if args.validate_existing_pack:
        readiness = evidence_pack_tool_readiness(args.validate_existing_pack, report_path=args.report_path)
        print(f"Readiness verdict: {readiness['verdict']}")
        print(f"Readiness score: {readiness['readiness_score']}")
        print(f"Records: {readiness.get('record_count', 0)}")
        print(f"Safe for env activation: {str(readiness['safe_for_env_activation']).lower()}")
        if readiness["blockers"]:
            print("Blockers: " + ", ".join(readiness["blockers"]))
        print("Stages:")
        for stage, status in readiness["stages"].items():
            print(f"- {stage}: {status}")
        return 0 if readiness["verdict"] != "FAIL_CLOSED_NOT_READY" else 1

    paths = [Path(p) for p in args.artifacts] if args.artifacts else discover_step6a_artifacts(args.project_briefs_dir)
    if not paths:
        print("No Step 6A evidence-upgrade artifacts found", file=sys.stderr)
        return 2
    pack = build_evidence_pack(paths)
    json_path, md_path = write_evidence_pack_outputs(pack, args.output_dir, stem=args.stem)
    print(f"JSON: {json_path}")
    print(f"Markdown: {md_path}")
    print(f"Verdict: {pack['validation']['verdict']}")
    print(f"Records: {pack['validation']['total_records']} total / {pack['validation']['ingestion_ready_records']} ready / {pack['validation']['fail_closed_records']} fail-closed")
    return 0 if pack["records"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
