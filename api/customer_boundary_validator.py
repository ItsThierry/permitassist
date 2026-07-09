from __future__ import annotations

"""Offline customer-boundary/core-truth validator for PermitAssist.

This module is intentionally pure: it performs no network/model calls and does
not mutate production customer output.  It validates the customer-visible packet
(API JSON + public_packet/share/report/render text) against the sealed canonical
permit-family truth and, when supplied, Phase 0 fixture expectations.
"""

from dataclasses import asdict, dataclass, field
import copy
import json
import re
from typing import Any, Iterable

try:  # api/ on sys.path in production/tests
    from family_policy_matrix import forbidden_families as matrix_forbidden_families, mandatory_families as matrix_mandatory_families
    from family_reconciliation_gate import family_from_row
    from scope_contract import safety_critical_required_families
except Exception:  # pragma: no cover
    from api.family_policy_matrix import forbidden_families as matrix_forbidden_families, mandatory_families as matrix_mandatory_families
    from api.family_reconciliation_gate import family_from_row
    from api.scope_contract import safety_critical_required_families


_FAMILY_ALIASES = {
    "fire": "fire_suppression",
    "fire_life_safety": "fire_suppression",
    "life_safety": "fire_suppression",
    "health": "health_food",
    "food": "health_food",
    "pool": "health_food",
    "site_civil": "grading",
    "right_of_way": "grading",
    "row": "grading",
    "zoning": "planning_zoning",
    "planning": "planning_zoning",
    "co": "co_change_of_occupancy",
    "certificate_of_occupancy": "co_change_of_occupancy",
    "building_structural": "building",
    "structural": "building",
    "detached_garage_building": "building",
    "overbroad_electrical": "electrical",
    "duplicate_fire_alarm": "fire_alarm",
}

_FAMILY_TEXT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("wastewater_pretreatment_fog", r"\b(?:wastewater|fog|pretreatment|grease interceptor|grease trap)\b"),
    ("environmental", r"\b(?:environmental|fuel[- ]?system|fuel tank|fuel dispensers?|ust|oil tank)\b"),
    ("refrigeration", r"\b(?:refrigeration|refrigerant|line[- ]?set|walk[- ]?in cooler|cooler room|freezer|cold storage)\b"),
    ("fire_alarm", r"\bfire\s+alarm\b"),
    ("fire_suppression", r"\b(?:fire|sprinkler|suppression|wet chemical|ansul|life[- ]?safety|hood suppression)\b"),
    ("health_food", r"\b(?:health|food establishment|food service|restaurant|public pool|pool)\b"),
    ("elevator", r"\belevator\b"),
    ("sign", r"\bsign(?:age)?\b"),
    ("planning_zoning", r"\b(?:planning|zoning|land use)\b"),
    ("co_change_of_occupancy", r"\b(?:certificate of occupancy|change[- ]of[- ]occupancy|change[- ]of[- ]use|\bcoo\b)\b"),
    ("grading", r"\b(?:right[- ]of[- ]way|site/civil|site civil|grading|parking lot|curb cut|stormwater)\b"),
    ("gas", r"\b(?:fuel gas|gas piping|gas line|gas branch|gas permit)\b"),
    ("plumbing", r"\b(?:plumbing|sink|toilet|shower|drain|backflow|irrigation|water line|sewer)\b"),
    ("mechanical", r"\b(?:mechanical|hvac|heat pump|mini[- ]split|ductless|furnace|rtu|rooftop unit|ventilation|exhaust|wood stove|chimney)\b"),
    ("electrical", r"\b(?:electrical|circuit|subpanel|panel|service|charger|lighting|disconnect|receptacle|transformer)\b"),
    ("building_ti", r"\b(?:tenant improvement|commercial building|change[- ]of[- ]use|build[- ]?out|fit[- ]out)\b"),
    ("building", r"\b(?:building|structural|foundation|framing|addition|garage conversion|adu|accessory structure|mezzanine|carport|shed|window|roof)\b"),
)

_FEE_DUMP_RE = re.compile(
    r"(?:Fee Estimate:\s*){2,}|\$\{[^}]+\}|\{\{[^{}]+\}\}|\bfees?\b.{0,80}\bnan\b|\bnan\b.{0,80}\bfees?\b",
    re.I | re.S,
)
_NOT_REQUIRED_RE = re.compile(r"\b(?:no permit required|no permit needed|permit is not required|permit not required|no permit submission needed|no-permit scope|resolved no-permit)\b", re.I)
_REQUIRED_ACTION_RE = re.compile(r"\b(?:file|submit|pull)\b.{0,80}\b(?:required )?permit\b|\bapply\s+for\b.{0,80}\b(?:required )?permit\b", re.I)


def canonical_family(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace("/", "_").replace(" ", "_")
    raw = re.sub(r"_+", "_", raw).strip("_")
    return _FAMILY_ALIASES.get(raw, raw)


def _stable_unique(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        fam = canonical_family(value)
        if not fam or fam in {"not_required", "none", "unknown", "verify", "permit"}:
            continue
        if fam not in seen:
            seen.add(fam)
            out.append(fam)
    return tuple(out)


def _row_family(row: dict[str, Any]) -> str:
    if not isinstance(row, dict):
        return ""
    fam = canonical_family(row.get("family") or row.get("filing_family") or "")
    if fam:
        return fam
    try:
        return canonical_family(family_from_row(row))
    except Exception:
        pass
    text = " ".join(str(row.get(k) or "") for k in ("permit_type", "permit_name", "approval_type", "kind", "display_family"))
    for family, pattern in _FAMILY_TEXT_PATTERNS:
        if re.search(pattern, text, re.I):
            return family
    return ""


def _row_status(row: dict[str, Any]) -> str:
    raw = str(row.get("decision") or row.get("status") or row.get("requirement") or "").upper().strip()
    if raw in {"REQUIRED", "NOT_REQUIRED", "CONDITIONAL", "VERIFY"}:
        return raw
    if row.get("required") is True:
        return "REQUIRED"
    if row.get("required") is False:
        return "CONDITIONAL"
    return ""


@dataclass(frozen=True)
class CanonicalDecisionObject:
    """Sealed public decision core used for pure projection/parity checks."""

    decision: str
    required: bool | None
    segment: str = ""
    required_families: tuple[str, ...] = field(default_factory=tuple)
    conditional_families: tuple[str, ...] = field(default_factory=tuple)
    not_required_families: tuple[str, ...] = field(default_factory=tuple)
    permit_names: tuple[str, ...] = field(default_factory=tuple)
    authority_name: str = ""
    apply_url: str = ""
    source_urls: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_public(cls, public: dict[str, Any]) -> "CanonicalDecisionObject":
        data = public if isinstance(public, dict) else {}
        packet_raw = data.get("public_packet")
        packet: dict[str, Any] = packet_raw if isinstance(packet_raw, dict) else {}
        rows_value = packet.get("rows")
        rows = rows_value if isinstance(rows_value, list) else (data.get("permits_required") or [])
        required_rows = [r for r in rows if isinstance(r, dict) and _row_status(r) == "REQUIRED"]
        conditional_rows = [r for r in rows if isinstance(r, dict) and _row_status(r) == "CONDITIONAL"]
        not_required_rows = [r for r in rows if isinstance(r, dict) and _row_status(r) == "NOT_REQUIRED"]
        packet_required = packet.get("required_families") if isinstance(packet.get("required_families"), list) else []
        packet_conditional = packet.get("conditional_families") if isinstance(packet.get("conditional_families"), list) else []
        decision = str(packet.get("decision") or packet.get("permit_required_verdict") or data.get("permit_decision") or "").upper().strip()
        if decision == "YES":
            decision = "REQUIRED"
        elif decision == "NO":
            decision = "NOT_REQUIRED"
        authority = packet.get("authority") if isinstance(packet.get("authority"), dict) else {}
        source_urls = list(data.get("source_urls") or [])
        source_urls.extend(authority.get("source_urls") or [])
        apply_url = str(authority.get("apply_url") or data.get("apply_url") or data.get("online_application_url") or "").strip()
        return cls(
            decision=decision,
            required=(True if decision == "REQUIRED" else False if decision == "NOT_REQUIRED" else data.get("permit_required")),
            segment=str(packet.get("segment") or data.get("segment") or "").lower().strip(),
            required_families=_stable_unique([*(packet_required or []), *(_row_family(r) for r in required_rows)]),
            conditional_families=_stable_unique([*(packet_conditional or []), *(_row_family(r) for r in conditional_rows)]),
            not_required_families=_stable_unique(_row_family(r) for r in not_required_rows),
            permit_names=tuple(str(r.get("permit_name") or r.get("permit_type") or "").strip() for r in required_rows if str(r.get("permit_name") or r.get("permit_type") or "").strip()),
            authority_name=str(authority.get("name") or data.get("applying_office") or data.get("building_dept_name") or "").strip(),
            apply_url=apply_url,
            source_urls=tuple(dict.fromkeys(str(u) for u in source_urls if isinstance(u, str) and u.startswith("http"))),
        )

    def validate(self) -> list[str]:
        issues: list[str] = []
        if self.decision not in {"REQUIRED", "NOT_REQUIRED"}:
            issues.append("invalid_decision")
        if self.decision == "REQUIRED" and not self.required_families:
            issues.append("required_without_required_families")
        if self.decision == "NOT_REQUIRED" and self.required_families:
            issues.append("not_required_with_required_families")
        if self.decision == "NOT_REQUIRED" and self.apply_url:
            issues.append("not_required_with_apply_url")
        return issues

    def as_public_dict(self) -> dict[str, Any]:
        return asdict(self)


def project_canonical_decision(base: dict[str, Any], canonical: CanonicalDecisionObject) -> dict[str, Any]:
    """Purely rewrite public mirrors from the canonical decision object.

    The projection does not infer or change permit truth; it mirrors the sealed
    canonical rows into legacy customer fields so renderers cannot resurrect stale
    families from old top-level values.
    """
    out = copy.deepcopy(base) if isinstance(base, dict) else {}
    out["permit_decision"] = canonical.decision
    out["permit_required"] = canonical.required
    out["permit_verdict"] = "YES" if canonical.decision == "REQUIRED" else "NO" if canonical.decision == "NOT_REQUIRED" else canonical.decision
    out["required_permit_families"] = list(canonical.required_families)
    out["required_permit_names"] = list(canonical.permit_names)
    if canonical.decision == "NOT_REQUIRED":
        out["permits_required"] = []
        out["conditional_permits"] = []
        out["related_permits"] = []
        out["permit_name"] = "No permit required"
        out["permit_type"] = "No permit required"
        out["permit_kind"] = "Not Required"
        out["apply_url"] = ""
        out["online_application_url"] = ""
        return out
    packet_raw = out.get("public_packet")
    packet: dict[str, Any] = packet_raw if isinstance(packet_raw, dict) else {}
    rows = [r for r in (packet.get("rows") or []) if isinstance(r, dict) and _row_status(r) == "REQUIRED"]
    if rows:
        out["permits_required"] = [
            {
                "permit_type": r.get("permit_name") or r.get("permit_type"),
                "permit_name": r.get("permit_name") or r.get("permit_type"),
                "family": _row_family(r),
                "filing_family": _row_family(r),
                "decision": "REQUIRED",
                "status": "REQUIRED",
                "required": True,
            }
            for r in rows
        ]
    if canonical.permit_names:
        out["permit_name"] = canonical.permit_names[0] if len(canonical.permit_names) == 1 else "Permit package: " + "; ".join(canonical.permit_names)
        out["permit_type"] = out["permit_name"]
    return out


@dataclass(frozen=True)
class CustomerBoundaryFinding:
    code: str
    severity: str = "error"
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "detail": self.detail}


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _visible_surface(public: dict[str, Any], visible_text: str = "", html_text: str = "") -> str:
    # Scan customer-visible copy, not inert JavaScript source/fallback strings.
    # The renderer includes code literals such as "Pull permit before starting
    # work" that are not displayed for NOT_REQUIRED payloads and should not
    # create false status-contradiction findings.
    rendered_html_text = re.sub(r"<script\b.*?</script>", " ", html_text or "", flags=re.I | re.S)
    pieces = [visible_text or "", rendered_html_text]
    for key in ("customer_headline", "customer_next_step", "summary", "job_summary", "required_permit_summary", "fee_range", "permit_name", "permit_type", "permit_kind"):
        value = public.get(key) if isinstance(public, dict) else None
        if value not in (None, ""):
            pieces.append(str(value))
    return "\n".join(pieces)


def _fee_copy_surface(public: dict[str, Any], visible_text: str = "") -> str:
    """Return only customer-copy fields that can legitimately contain fee text.

    Do not scan full serialized JSON: ordinary machine values such as null/None
    are not customer-visible fee corruption. Rendered/body text and explicitly
    fee/copy-shaped fields are enough to catch template leaks and duplicate fee
    fragments without turning every empty JSON field into a RED finding.
    """
    data = public if isinstance(public, dict) else {}
    pieces = [visible_text or ""]
    for key in (
        "fee_range",
        "fee_estimate",
        "fee_notes",
        "permit_fee",
        "customer_headline",
        "customer_next_step",
        "summary",
        "job_summary",
        "required_permit_summary",
    ):
        value = data.get(key)
        if value not in (None, ""):
            pieces.append(str(value))
    raw_packet = data.get("public_packet")
    packet = raw_packet if isinstance(raw_packet, dict) else {}
    for value in packet.get("fees") or []:
        if value not in (None, ""):
            pieces.append(str(value))
    return "\n".join(pieces)


def _families_from_text(text: str) -> set[str]:
    out: set[str] = set()
    for fam, pattern in _FAMILY_TEXT_PATTERNS:
        if re.search(pattern, text or "", re.I):
            out.add(fam)
    return out


def _families_in_public_required(public: dict[str, Any]) -> set[str]:
    rows = [r for r in public.get("permits_required") or [] if isinstance(r, dict)] if isinstance(public, dict) else []
    return {fam for fam in (_row_family(r) for r in rows if _row_status(r) in {"", "REQUIRED"}) if fam}


def _matrix_expected_families(facts: Any | None) -> tuple[set[str], dict[str, str], dict[str, str]]:
    mandatory = matrix_mandatory_families(facts) if facts is not None else {}
    forbidden = matrix_forbidden_families(facts) if facts is not None else {}
    return set(mandatory), mandatory, forbidden


def validate_customer_boundary(
    public: dict[str, Any],
    *,
    visible_text: str = "",
    html_text: str = "",
    expected: dict[str, Any] | None = None,
    facts: Any | None = None,
    include_matrix: bool = False,
) -> list[CustomerBoundaryFinding]:
    """Return post-render customer-boundary findings.

    `expected` is optional and used by Phase 0 RED fixture replay.  Runtime/local
    replay can call this without expectations to catch self-contradictions,
    projection parity, matrix floor/ceiling misses, and stale rendered mirrors.
    """
    data = public if isinstance(public, dict) else {}
    expected = expected if isinstance(expected, dict) else {}
    findings: list[CustomerBoundaryFinding] = []
    canonical = CanonicalDecisionObject.from_public(data)
    for issue in canonical.validate():
        findings.append(CustomerBoundaryFinding(issue, detail="canonical decision object invalid"))

    decision = canonical.decision or str(data.get("permit_decision") or "").upper().strip()
    public_required = _families_in_public_required(data)
    packet = data.get("public_packet") if isinstance(data.get("public_packet"), dict) else {}
    packet_required = set(canonical.required_families)
    surface = _visible_surface(data, visible_text, html_text)
    serialized = _json_text(data)

    if decision == "REQUIRED" and not public_required and not packet_required:
        findings.append(CustomerBoundaryFinding("empty_or_generic_required_package"))
    if decision == "REQUIRED" and _NOT_REQUIRED_RE.search(surface):
        findings.append(CustomerBoundaryFinding("status_contradiction_required_with_no_permit_text"))
    if decision == "NOT_REQUIRED" and (public_required or packet_required or _REQUIRED_ACTION_RE.search(surface)):
        findings.append(CustomerBoundaryFinding("status_contradiction_not_required_with_required_artifacts"))
    safety_families = safety_critical_required_families(facts)
    if decision == "NOT_REQUIRED" and safety_families:
        findings.append(CustomerBoundaryFinding("safety_trigger_not_required", detail=",".join(sorted(safety_families))))
    if decision == "NOT_REQUIRED" and not re.search(r"\b(?:exempt|no permit|not required|finish work only|same[- ]?kind|like[- ]for[- ]like)\b", serialized, re.I):
        findings.append(CustomerBoundaryFinding("not_required_missing_positive_exemption_evidence", "warning"))
    if _FEE_DUMP_RE.search(_fee_copy_surface(data, visible_text)):
        findings.append(CustomerBoundaryFinding("fee_or_report_dump_corruption"))

    if packet:
        if packet_required and public_required and packet_required != public_required:
            findings.append(CustomerBoundaryFinding("canonical_vs_public_required_family_diff", detail=f"packet={sorted(packet_required)} public={sorted(public_required)}"))
        mirror_families = _stable_unique(data.get("required_permit_families") or [])
        # Legacy mirrors may include display labels, but cannot omit canonical families.
        if packet_required and not packet_required.issubset(set(mirror_families) | _families_from_text(" ".join(str(x) for x in data.get("required_permit_families") or []))):
            findings.append(CustomerBoundaryFinding("stale_required_family_mirror"))
        packet_decision = str(packet.get("decision") or packet.get("permit_required_verdict") or "").upper().strip()
        if packet_decision in {"REQUIRED", "NOT_REQUIRED"} and decision in {"REQUIRED", "NOT_REQUIRED"} and packet_decision != decision:
            findings.append(CustomerBoundaryFinding("status_coherence_packet_api_mismatch", detail=f"packet={packet_decision} api={decision}"))

    expected_decision = str(expected.get("expected_decision") or "").upper().strip()
    if expected_decision and decision in {"REQUIRED", "NOT_REQUIRED"} and expected_decision != decision:
        findings.append(CustomerBoundaryFinding("expected_decision_mismatch", detail=f"expected={expected_decision} actual={decision}"))
    must_include = {canonical_family(f) for f in expected.get("required_families_must_include") or [] if canonical_family(f)}
    if must_include:
        missing = sorted(must_include - (public_required | packet_required))
        if missing:
            findings.append(CustomerBoundaryFinding("missing_or_demoted_required_family", detail=",".join(missing)))
    forbidden = {canonical_family(f) for f in expected.get("forbidden_hard_required_families") or [] if canonical_family(f)}
    present_forbidden = sorted(forbidden & (public_required | packet_required))
    if present_forbidden:
        findings.append(CustomerBoundaryFinding("unsupported_extra_hard_required_family", detail=",".join(present_forbidden)))
    for phrase in expected.get("forbidden_rendered_phrases") or []:
        if phrase and re.search(re.escape(str(phrase)), surface, re.I):
            findings.append(CustomerBoundaryFinding("forbidden_rendered_phrase", detail=str(phrase)[:160]))

    if include_matrix and facts is not None:
        mandatory_set, mandatory, forbidden_map = _matrix_expected_families(facts)
        missing_matrix = sorted(mandatory_set - (public_required | packet_required))
        if missing_matrix:
            findings.append(CustomerBoundaryFinding("matrix_expected_family_floor_dry_run", detail=",".join(missing_matrix)))
        present_forbidden_matrix = sorted(set(forbidden_map) & (public_required | packet_required))
        if present_forbidden_matrix:
            findings.append(CustomerBoundaryFinding("matrix_forbidden_family_ceiling_dry_run", detail=",".join(present_forbidden_matrix)))

    return findings


def canonical_render_diffs(public: dict[str, Any]) -> list[CustomerBoundaryFinding]:
    canonical = CanonicalDecisionObject.from_public(public if isinstance(public, dict) else {})
    projected = project_canonical_decision(public if isinstance(public, dict) else {}, canonical)
    diffs: list[CustomerBoundaryFinding] = []
    for key in ("permit_decision", "permit_required", "permit_verdict", "required_permit_families", "required_permit_names"):
        if (public or {}).get(key) != projected.get(key):
            diffs.append(CustomerBoundaryFinding("canonical_projection_diff", detail=key))
    return diffs


def canonical_payload_diffs(public: dict[str, Any], payload_data: dict[str, Any]) -> list[CustomerBoundaryFinding]:
    """Validate the rendered share/report payload against the canonical API JSON.

    Part 2 establishes `public_packet` + `permits_required` as the rendered
    customer contract. Legacy top-level `required_permit_families` and
    `required_permit_names` are intentionally absent from the HTML share/report
    payload so stale mirrors cannot become a second source of truth.
    """
    public_data = public if isinstance(public, dict) else {}
    payload = payload_data if isinstance(payload_data, dict) else {}
    diffs: list[CustomerBoundaryFinding] = []
    for key in ("permit_decision", "permit_required", "permit_verdict", "permits_required", "public_packet"):
        if payload.get(key) != public_data.get(key):
            diffs.append(CustomerBoundaryFinding("html_payload_public_mismatch", detail=key))
    for key in ("required_permit_families", "required_permit_names"):
        if key in payload:
            diffs.append(CustomerBoundaryFinding("legacy_render_mirror_present", detail=key))
    return diffs
