"""CLI entrypoint for the Phase 1 M8 fixture-only pipeline runner."""

from __future__ import annotations

import argparse
import sys

from .phase1_runner import Phase1RunnerSafetyError, run_phase1_fixture_pipeline, summary_to_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 1 M8 fixture-only lead pipeline.")
    parser.add_argument("--fixture", default="golden", help="Approved fixture id. Only 'golden' is supported in M8.")
    parser.add_argument("--format", choices=("json",), default="json", help="Output format.")
    args = parser.parse_args(argv)
    try:
        result = run_phase1_fixture_pipeline(fixture_id=args.fixture)
    except Phase1RunnerSafetyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(summary_to_json(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
