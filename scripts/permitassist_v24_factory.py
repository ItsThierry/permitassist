#!/usr/bin/env python3
"""PermitAssist v2.4 factory CLI.

Local/staged-only utilities for auditing v2.3.1 cells, generating v2.4 spine
candidates, and running the v2.4 merge gate. This script intentionally does not
write production/compiled/runtime files unless an explicit output path is passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
for path in (ROOT, API):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from api.v24_decision_cells import (  # noqa: E402
    audit_v231_index_for_v24,
    convert_v231_cell_to_v24_spine,
    load_field_registry,
    validate_field_registry,
    validate_v24_cell,
)


def _live_url_checker(url: str, timeout: float = 12.0) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    try:
        req = Request(url, method="HEAD", headers={"User-Agent": "PermitAssist-v24-validator/1.0"})
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - validator deliberately checks public URLs
            return 200 <= int(resp.status) < 400
    except Exception:
        try:
            req = Request(url, method="GET", headers={"User-Agent": "PermitAssist-v24-validator/1.0"})
            with urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return 200 <= int(resp.status) < 400
        except Exception:
            return False


def cmd_registry_check(args: argparse.Namespace) -> int:
    registry = load_field_registry(args.registry)
    result = validate_field_registry(registry)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.ok else 2


def cmd_audit_v231(args: argparse.Namespace) -> int:
    report = audit_v231_index_for_v24(args.index)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def cmd_migrate_v231_spines(args: argparse.Namespace) -> int:
    index_doc = json.loads(Path(args.index).read_text())
    index = index_doc.get("index")
    if not isinstance(index, dict):
        raise SystemExit("v231 index must contain an index object")
    cells = []
    for key in sorted(index):
        cell = index[key]
        if not isinstance(cell, dict):
            continue
        cells.append(convert_v231_cell_to_v24_spine(cell, index_key=key))
        if args.limit and len(cells) >= args.limit:
            break
    out = {
        "schema_version": "permitassist_v24_spine_candidates_1",
        "source_index": str(args.index),
        "count": len(cells),
        "note": "DRAFT spine candidates only; run enrichment + merge gate before publish.",
        "cells": cells,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(out, indent=2, sort_keys=True))
    print(json.dumps({"ok": True, "output": args.output, "count": len(cells)}, indent=2, sort_keys=True))
    return 0


def cmd_validate_cell(args: argparse.Namespace) -> int:
    cell = json.loads(Path(args.cell).read_text())
    if isinstance(cell, dict) and "cells" in cell and isinstance(cell["cells"], list):
        issues = []
        for idx, one in enumerate(cell["cells"]):
            result = validate_v24_cell(
                one,
                snapshot_root=args.snapshot_root,
                live_url_checker=_live_url_checker if args.live_url_check else None,
                strict_snapshots=not args.allow_missing_snapshots,
            )
            if not result.ok:
                issues.append({"index": idx, **result.to_dict()})
        out = {"ok": not issues, "cell_count": len(cell["cells"]), "failures": issues}
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["ok"] else 2
    result = validate_v24_cell(
        cell,
        snapshot_root=args.snapshot_root,
        live_url_checker=_live_url_checker if args.live_url_check else None,
        strict_snapshots=not args.allow_missing_snapshots,
    )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.ok else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PermitAssist v2.4 schema/factory utilities")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("registry-check", help="Validate the v2.4 field registry")
    p.add_argument("--registry", default=str(ROOT / "schema" / "permitassist_v24" / "fields.json"))
    p.set_defaults(func=cmd_registry_check)

    p = sub.add_parser("audit-v231", help="Audit v2.3.1 runtime index for v2.4 enrichment readiness")
    p.add_argument("--index", default=str(ROOT / "knowledge" / "permitassist_decision_cell_index_v231.json"))
    p.set_defaults(func=cmd_audit_v231)

    p = sub.add_parser("migrate-v231-spines", help="Generate DRAFT v2.4 spine candidates from v2.3.1 cells")
    p.add_argument("--index", default=str(ROOT / "knowledge" / "permitassist_decision_cell_index_v231.json"))
    p.add_argument("--output", required=True)
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_migrate_v231_spines)

    p = sub.add_parser("validate-cell", help="Run the v2.4 merge gate on one cell or a generated cell bundle")
    p.add_argument("cell")
    p.add_argument("--snapshot-root", default=str(ROOT))
    p.add_argument("--allow-missing-snapshots", action="store_true", help="Dry-run mode only; merge gate should not use this")
    p.add_argument("--live-url-check", action="store_true", help="Perform public HEAD/GET checks for apply URLs")
    p.set_defaults(func=cmd_validate_cell)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
