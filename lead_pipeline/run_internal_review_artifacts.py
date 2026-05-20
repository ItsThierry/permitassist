"""CLI for M9 local internal review artifacts.

The CLI runs the deterministic M8 golden fixture and writes local JSON + Markdown
review artifacts only. It performs no network, paid-provider, CRM, outreach, or
send action.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .phase1_runner import M8_BATCH_ID, run_phase1_fixture_pipeline
from .review_artifacts import write_internal_review_artifacts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render Lead Pipeline M9 internal review artifacts locally.")
    parser.add_argument("--fixture", default="golden", choices=["golden"], help="Approved fixture id; only 'golden' is supported.")
    parser.add_argument("--output-dir", required=True, help="Local directory for Markdown + JSON artifacts.")
    args = parser.parse_args(argv)

    run = run_phase1_fixture_pipeline(fixture_id=args.fixture)
    result = write_internal_review_artifacts(run.conn, output_dir=Path(args.output_dir), batch_id=M8_BATCH_ID)
    print(json.dumps(result.manifest(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
