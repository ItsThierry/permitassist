from __future__ import annotations

"""Opt-in sidecar trace logging for PermitAssist offline Phase 0 work.

The trace is disabled unless PERMITASSIST_PHASE_TRACE_DIR is set. It must never
mutate the customer ViewModel or rendered report; it writes JSONL sidecars only.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return repr(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > 2000:
            return value[:2000] + "…[truncated]"
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in value.items() if not str(k).lower().endswith("secret")}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v, depth + 1) for v in list(value)[:500]]
    if hasattr(value, "as_dict"):
        try:
            return _jsonable(value.as_dict(), depth + 1)
        except Exception:
            return repr(value)
    return repr(value)


def emit_trace(event: str, payload: dict[str, Any]) -> None:
    trace_dir = os.environ.get("PERMITASSIST_PHASE_TRACE_DIR")
    if not trace_dir:
        return
    try:
        root = Path(trace_dir)
        root.mkdir(parents=True, exist_ok=True)
        case_id = os.environ.get("PERMITASSIST_TRACE_CASE_ID") or "unknown_case"
        safe_case = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in case_id)[:80] or "unknown_case"
        record = {
            "event": event,
            "case_id": case_id,
            "emitted_at_utc": datetime.now(timezone.utc).isoformat(),
            "payload": _jsonable(payload),
        }
        with (root / f"{safe_case}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    except Exception:
        # Trace logging is diagnostic only; it must never affect product behavior.
        return
