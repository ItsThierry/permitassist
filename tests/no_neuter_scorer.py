from __future__ import annotations

import re
from typing import Any


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def score_packet(packet: dict[str, Any]) -> dict[str, int]:
    text_values = list(_strings(packet))
    rows = [r for r in packet.get("permits_required") or [] if isinstance(r, dict) and r.get("required") is not False and str(r.get("decision") or "REQUIRED").upper() != "CONDITIONAL"]
    return {
        "required_rows": len(rows),
        "not_required": 1 if str(packet.get("permit_decision") or "").upper() == "NOT_REQUIRED" or packet.get("permit_required") is False else 0,
        "fee_amounts": len(re.findall(r"\$\s*\d", "\n".join(text_values))),
        "documents": len(packet.get("what_to_bring") or packet.get("requirements") or packet.get("documents_needed") or []),
        "inspections": len(packet.get("inspections") or packet.get("inspection_checklist") or []),
        "sources": len(packet.get("source_urls") or packet.get("sources") or []),
        "hedge_decision_phrases": len(re.findall(r"contact (?:your|the) (?:ahj|building department) to (?:verify|determine)", "\n".join(text_values), re.I)),
    }
