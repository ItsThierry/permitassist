from __future__ import annotations

"""Canonical public packet DTO for PermitAssist report/API parity."""

from dataclasses import dataclass, asdict, field
from typing import Any, Literal
import copy

try:
    from family_reconciliation_gate import family_from_row
except Exception:  # pragma: no cover
    from api.family_reconciliation_gate import family_from_row

Decision = Literal["REQUIRED", "NOT_REQUIRED", "CONDITIONAL", "VERIFY"]


@dataclass
class PacketRow:
    permit_name: str
    family: str
    decision: Decision
    reason: str = ""
    conditional_text: str = ""
    source: str = ""
    action_url: str = ""
    fees: str = ""
    documents: list[str] = field(default_factory=list)
    inspections: list[str] = field(default_factory=list)


@dataclass
class PublicPacketDTO:
    jurisdiction: dict[str, Any]
    rows: list[PacketRow]
    headline: str
    summary: str
    checklist: list[str]
    gate_audit: list[dict[str, Any]] = field(default_factory=list)

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("gate_audit", None)
        return data


def _name(row: dict[str, Any]) -> str:
    return str(row.get("permit_name") or row.get("permit_type") or row.get("name") or "Permit").strip()


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            text = str(item.get("label") or item.get("name") or item.get("title") or item.get("stage") or item.get("description") or "").strip()
            if text:
                out.append(text)
    return list(dict.fromkeys(out))


def _safe_reason(row: dict[str, Any]) -> str:
    reason = str(row.get("reason") or row.get("customer_reason") or "").strip()
    if reason:
        return reason
    notes = str(row.get("notes") or "").strip()
    if notes and not any(token in notes.lower() for token in ("deterministic implication", "positive scope fact", "source-backed row", "veto", "demote", "family gate")):
        return notes
    return ""


def _packet_row(row: dict[str, Any], result: dict[str, Any], decision: Decision) -> PacketRow:
    return PacketRow(
        permit_name=_name(row),
        family=str(row.get("family") or family_from_row(row)),
        decision=decision,
        reason=_safe_reason(row),
        conditional_text=str(row.get("conditional_text") or row.get("required_if") or ""),
        source=str(row.get("source_url") or row.get("source") or ""),
        action_url=str(row.get("apply_url") or result.get("apply_url") or result.get("online_application_url") or ""),
        fees=str(row.get("fee") or result.get("fee_range") or result.get("fee") or ""),
        documents=_text_list(row.get("documents") or result.get("what_to_bring") or result.get("requirements") or result.get("documents_needed"))[:8],
        inspections=_text_list(row.get("inspections") or result.get("inspections") or result.get("inspection_checklist"))[:8],
    )


def build_public_packet(result: dict[str, Any], facts: Any | None = None) -> PublicPacketDTO:
    data = copy.deepcopy(result) if isinstance(result, dict) else {}
    required_rows = [r for r in data.get("permits_required") or [] if isinstance(r, dict) and r.get("required") is not False and str(r.get("decision") or "REQUIRED").upper() != "CONDITIONAL"]
    conditional_rows = [r for r in data.get("conditional_permits") or [] if isinstance(r, dict)]
    rows = [_packet_row(r, data, "REQUIRED") for r in required_rows]
    rows.extend(_packet_row(r, data, "CONDITIONAL") for r in conditional_rows)
    if not rows and (data.get("permit_required") is False or str(data.get("permit_decision") or "").upper() == "NOT_REQUIRED"):
        rows.append(PacketRow("No permit required", "not_required", "NOT_REQUIRED", reason=str(data.get("not_required_reason") or data.get("summary") or "")))
    segment = str(getattr(facts, "segment", "") or data.get("segment") or "").strip().lower()
    prefix = "Commercial" if segment == "commercial" else ("Residential" if segment == "residential" else "Permit")
    required_names = [row.permit_name for row in rows if row.decision == "REQUIRED"]
    conditional_names = [row.permit_name for row in rows if row.decision == "CONDITIONAL"]
    if required_names:
        headline = f"{prefix} permit required: {required_names[0]}"
        summary = "Required permit package: " + "; ".join(required_names) + "."
        if conditional_names:
            summary += " Conditional guidance: " + "; ".join(conditional_names) + "."
    else:
        headline = f"{prefix} no permit required for the stated scope"
        summary = str(data.get("summary") or data.get("not_required_reason") or "No permit required for the stated scope.")
    checklist: list[str] = []
    for row in rows:
        if row.decision == "REQUIRED":
            checklist.append(f"Pull {row.permit_name} before starting work")
        elif row.decision == "CONDITIONAL":
            checklist.append(f"{row.conditional_text or 'If triggered'} — if triggered, pull {row.permit_name}")
        elif row.decision == "NOT_REQUIRED":
            checklist.append("No permit is required for this scope — keep this report with the job record")
    return PublicPacketDTO(
        jurisdiction={"name": data.get("applying_office") or data.get("jurisdiction") or "", "state": data.get("state") or "", "source_domain": ""},
        rows=rows,
        headline=headline,
        summary=summary,
        checklist=checklist,
        gate_audit=list(data.get("_family_gate_audit") or []),
    )


PUBLIC_ROW_KEYS = {
    "permit_type", "permit_name", "name", "kind", "family", "filing_family", "decision", "status", "required",
    "conditional_text", "required_if", "source_url", "source", "apply_url", "fee", "documents", "inspections",
    "portal_selection", "approval_type",
}


def _public_row_dict(row: dict[str, Any]) -> dict[str, Any]:
    cleaned = {k: copy.deepcopy(v) for k, v in row.items() if k in PUBLIC_ROW_KEYS and not str(k).startswith("_")}
    cleaned.pop("source_status", None)
    cleaned.pop("rationale", None)
    if "notes" in cleaned and any(token in str(cleaned.get("notes") or "").lower() for token in ("deterministic implication", "positive scope fact", "source-backed row", "veto", "demote", "family gate")):
        cleaned.pop("notes", None)
    return cleaned


def _clean_fee_text(text: str, facts: Any | None = None) -> str:
    value = str(text or "")
    value = value.replace(" — verify in before quoting", " — verify with the building department before quoting")
    value = value.replace(" — verify current fees with the issuing office before quoting", " — verify with the building department before quoting")
    if facts is not None and "no_sprinkler_alteration" in set(getattr(facts, "negative_facts", []) or []):
        value = value.replace(" + $4,000 fire-sprinkler-modify adder", "")
        value = value.replace(" + $4000 fire-sprinkler-modify adder", "")
        value = value.replace("fire-sprinkler-modify adder", "fire/life-safety review component (no sprinkler-modification adder)")
    return value


def apply_public_packet_projection(result: dict[str, Any], facts: Any | None = None) -> dict[str, Any]:
    out = copy.deepcopy(result) if isinstance(result, dict) else {}
    if out.get("fee_range"):
        out["fee_range"] = _clean_fee_text(out.get("fee_range"), facts)
    packet = build_public_packet(out, facts)
    public_packet = packet.public_dict()
    out.pop("_family_gate_audit", None)
    for row_key in ("permits_required", "conditional_permits", "related_permits"):
        if isinstance(out.get(row_key), list):
            out[row_key] = [_public_row_dict(row) if isinstance(row, dict) else row for row in out[row_key]]
    out["public_packet"] = public_packet
    out["canonical_public_packet"] = public_packet
    out["public_packet_rows"] = public_packet.get("rows") or []
    out["customer_headline"] = packet.headline
    out["summary"] = packet.summary
    out["job_summary"] = packet.summary
    out["permit_summary"] = packet.summary
    out["customer_result_summary"] = packet.summary
    out["customer_first_screen_summary"] = packet.summary
    return out
