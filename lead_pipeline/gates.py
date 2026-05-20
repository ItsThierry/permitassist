"""Pure fixture gates for PermitAssist Lead Pipeline Phase 1 M2.

M2 deliberately contains no connectors, no network checks, no SMTP, no paid APIs,
no database writes, and no outreach side effects. The functions below evaluate
synthetic fixture dictionaries and return event-shaped results compatible with
the M1 schema contracts.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import ExportEligibility, GateStatus, SourceClass, SuppressionStatus
from .schema import PHASE1_SCHEMA_VERSION

GATE_VERSION = "lead_pipeline_phase1_m2_fixture_gates_v1"
FIXTURE_BATCH_ID = "batch_fixture_m2"
FIXTURE_OBSERVED_AT_UTC = "2026-05-20T00:00:00Z"

_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
_PLACEHOLDER_DOMAINS = {"example.com", "example.org", "example.net", "test.com", "domain.com"}
_DISPOSABLE_DOMAINS = {"mailinator.com", "tempmail.com", "10minutemail.com"}
_FREE_PROVIDER_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "aol.com"}
_ROLE_PREFIXES = {"info", "sales", "admin", "support", "contact", "office", "hello"}
_NO_SEND_PREFIXES = {"noreply", "no-reply", "bounce", "donotreply", "do-not-reply"}


@dataclass(frozen=True)
class GateResult:
    """Pure gate result with a verification-event-compatible payload."""

    gate_name: str
    status: GateStatus
    reason_codes: tuple[str, ...]
    score: float | None = None
    event_table: str = "verification_events"
    input_hash: str = ""
    target_entity_id: str | None = None
    target_fact_id: str | None = None
    export_eligibility: ExportEligibility = ExportEligibility.NOT_EXPORTABLE
    send_authorized: bool = False
    network_used: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_event_payload(self) -> dict[str, Any]:
        """Return a deterministic payload shaped to M1 verification_events."""

        return {
            "verification_event_id": f"ve_{self.gate_name}_{self.input_hash[:16]}",
            "batch_id": FIXTURE_BATCH_ID,
            "target_entity_id": self.target_entity_id,
            "target_fact_id": self.target_fact_id,
            "gate_name": self.gate_name,
            "gate_version": GATE_VERSION,
            "input_hash": self.input_hash,
            "result_status": self.status.value,
            "score": self.score,
            "reason_codes": json.dumps(list(self.reason_codes), sort_keys=True),
            "network_used_flag": 1 if self.network_used else 0,
            "cached_result_flag": 0,
            "observed_at_utc": FIXTURE_OBSERVED_AT_UTC,
            "expires_at_utc": None,
            "raw_result_ref": None,
            "cost_event_id": None,
            "schema_version": PHASE1_SCHEMA_VERSION,
        }


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _result(
    gate_name: str,
    status: GateStatus,
    reason_codes: list[str] | tuple[str, ...],
    payload: Mapping[str, Any] | str,
    *,
    score: float | None = None,
    export_eligibility: ExportEligibility = ExportEligibility.NOT_EXPORTABLE,
    send_authorized: bool = False,
    details: Mapping[str, Any] | None = None,
) -> GateResult:
    return GateResult(
        gate_name=gate_name,
        status=status,
        reason_codes=tuple(reason_codes),
        score=score,
        input_hash=_stable_hash(payload),
        export_eligibility=export_eligibility,
        send_authorized=send_authorized,
        network_used=False,
        details=details or {},
    )


def _as_mapping(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return payload or {}


def _domain_from_email(email: str) -> str:
    return email.rsplit("@", 1)[1].lower() if "@" in email else ""


def _local_from_email(email: str) -> str:
    return email.split("@", 1)[0].lower() if "@" in email else ""


def _is_status_pass(value: Any) -> bool:
    if isinstance(value, GateStatus):
        return value is GateStatus.PASS_
    return str(value) == GateStatus.PASS_.value


def sourceability_gate(source: Mapping[str, Any] | None) -> GateResult:
    """Require source URL/path, timestamp, hash, field snippet, and open source policy."""

    source = _as_mapping(source)
    missing = [
        field
        for field in ("url_or_path", "observed_at_utc", "payload_hash_sha256", "field_relevant_snippet")
        if not source.get(field)
    ]
    if missing:
        return _result("sourceability_gate", GateStatus.FAIL_CLOSED, [f"missing_{field}" for field in missing], source, score=0.0)
    if source.get("blocked_or_captcha_flag"):
        return _result("sourceability_gate", GateStatus.BLOCKED_SOURCE_POLICY, ["blocked_or_captcha_source"], source, score=0.0)
    if source.get("requires_login"):
        return _result("sourceability_gate", GateStatus.BLOCKED_SOURCE_POLICY, ["login_required_source_blocked_phase1"], source, score=0.0)
    if source.get("paid_flag") or source.get("source_class") == SourceClass.PURCHASED_VENDOR.value:
        return _result("sourceability_gate", GateStatus.BLOCKED_SOURCE_POLICY, ["paid_adapter_disabled_phase1"], source, score=0.0)
    if source.get("source_class") in {SourceClass.SOCIAL_PROFILE.value, SourceClass.SCRAPED_PUBLIC_PAGE.value}:
        return _result("sourceability_gate", GateStatus.REVIEW_REQUIRED, ["source_class_requires_review_phase1"], source, score=0.4)
    return _result("sourceability_gate", GateStatus.PASS_, ["source_provenance_complete"], source, score=1.0)


def identity_gate(identity: Mapping[str, Any] | None) -> GateResult:
    """Require cited business identity; route weak identity-only evidence to review."""

    identity = _as_mapping(identity)
    if not identity.get("business_name"):
        return _result("identity_gate", GateStatus.FAIL_CLOSED, ["missing_business_name"], identity, score=0.0)
    if not identity.get("source_observation_id") or not identity.get("field_relevant_snippet"):
        return _result("identity_gate", GateStatus.FAIL_CLOSED, ["missing_identity_provenance"], identity, score=0.0)
    if identity.get("license_only_seed"):
        return _result("identity_gate", GateStatus.REVIEW_REQUIRED, ["official_license_seed_cannot_promote_alone"], identity, score=0.55)
    if identity.get("domain_name_only_match"):
        return _result("identity_gate", GateStatus.REVIEW_REQUIRED, ["domain_name_only_match_requires_review"], identity, score=0.5)
    if identity.get("duplicate_candidate"):
        return _result("identity_gate", GateStatus.REVIEW_REQUIRED, ["duplicate_requires_identity_edge_not_delete"], identity, score=0.6)
    if identity.get("conflicting_names"):
        return _result("identity_gate", GateStatus.REVIEW_REQUIRED, ["conflicting_identity_claims"], identity, score=0.45)
    if identity.get("source_class") == SourceClass.AGGREGATOR_DIRECTORY.value:
        return _result("identity_gate", GateStatus.REVIEW_REQUIRED, ["aggregator_only_identity_requires_corroboration"], identity, score=0.5)
    return _result("identity_gate", GateStatus.PASS_, ["cited_business_identity"], identity, score=1.0)


def icp_fit_gate(icp: Mapping[str, Any] | None) -> GateResult:
    """Require source-backed PermitAssist ICP evidence, not generic contractor labels."""

    icp = _as_mapping(icp)
    labels = {str(label).lower() for label in icp.get("vertical_labels", [])}
    snippet = str(icp.get("evidence_snippet") or "").lower()
    if not snippet:
        return _result("icp_fit_gate", GateStatus.FAIL_CLOSED, ["missing_icp_evidence_snippet"], icp, score=0.0)
    if "permit expediter" in labels or "code consultant" in labels or "designer" in labels:
        return _result("icp_fit_gate", GateStatus.REVIEW_REQUIRED, ["buyer_partner_competitor_ambiguous"], icp, score=0.5)
    commercial_terms = {"commercial tenant improvement", "commercial ti", "tenant improvement", "restaurant", "clinic", "office"}
    if labels.isdisjoint(commercial_terms) and not any(term in snippet for term in commercial_terms):
        return _result("icp_fit_gate", GateStatus.FAIL_CLOSED, ["uncited_or_generic_icp_fit"], icp, score=0.0)
    return _result("icp_fit_gate", GateStatus.PASS_, ["source_backed_permitassist_icp_fit"], icp, score=1.0)


def contact_observation_gate(contact: Mapping[str, Any] | None) -> GateResult:
    """Require observed per-email provenance and block unsafe contact candidates."""

    contact = _as_mapping(contact)
    email = str(contact.get("email") or "").strip().lower()
    if not email:
        return _result("contact_observation_gate", GateStatus.FAIL_CLOSED, ["missing_email"], contact, score=0.0)
    if not contact.get("source_observation_id") or not contact.get("field_relevant_snippet"):
        return _result("contact_observation_gate", GateStatus.FAIL_CLOSED, ["missing_per_email_provenance"], contact, score=0.0)
    if not _EMAIL_RE.match(email):
        return _result("contact_observation_gate", GateStatus.FAIL_CLOSED, ["invalid_email_syntax"], contact, score=0.0)
    domain = _domain_from_email(email)
    local = _local_from_email(email)
    expected_domain = str(contact.get("domain") or "").lower()
    if domain in _PLACEHOLDER_DOMAINS or local in {"email", "user", "name", "test"}:
        return _result("contact_observation_gate", GateStatus.FAIL_CLOSED, ["placeholder_email"], contact, score=0.0)
    if contact.get("guessed_pattern"):
        return _result("contact_observation_gate", GateStatus.UNKNOWN_NOT_PROMOTED, ["guessed_pattern_email_not_verified"], contact, score=0.2)
    if local in _NO_SEND_PREFIXES:
        return _result("contact_observation_gate", GateStatus.FAIL_CLOSED, ["no_reply_or_internal_email"], contact, score=0.0)
    if expected_domain and domain != expected_domain:
        return _result("contact_observation_gate", GateStatus.REVIEW_REQUIRED, ["off_domain_email_requires_review"], contact, score=0.4)
    if not contact.get("observed_on_domain"):
        return _result("contact_observation_gate", GateStatus.REVIEW_REQUIRED, ["email_not_observed_on_first_party_domain"], contact, score=0.4)
    if contact.get("role_account") or local in _ROLE_PREFIXES:
        return _result("contact_observation_gate", GateStatus.REVIEW_REQUIRED, ["role_account_requires_small_business_review"], contact, score=0.65)
    return _result("contact_observation_gate", GateStatus.PASS_, ["contact_observed_with_provenance"], contact, score=1.0)


def email_syntax_gate(email: str) -> GateResult:
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return _result("email_syntax_gate", GateStatus.FAIL_CLOSED, ["invalid_email_syntax"], email, score=0.0)
    if _domain_from_email(email) in _PLACEHOLDER_DOMAINS:
        return _result("email_syntax_gate", GateStatus.FAIL_CLOSED, ["placeholder_domain"], email, score=0.0)
    return _result("email_syntax_gate", GateStatus.PASS_, ["email_syntax_pass"], email, score=1.0)


def domain_quality_gate(domain_info: Mapping[str, Any] | None) -> GateResult:
    """Evaluate mocked domain/DNS facts only; never performs DNS/network I/O."""

    domain_info = _as_mapping(domain_info)
    domain = str(domain_info.get("domain") or "").lower()
    if not domain or "." not in domain:
        return _result("domain_quality_gate", GateStatus.FAIL_CLOSED, ["invalid_domain"], domain_info, score=0.0)
    if domain_info.get("disposable") or domain in _DISPOSABLE_DOMAINS:
        return _result("domain_quality_gate", GateStatus.FAIL_CLOSED, ["disposable_domain"], domain_info, score=0.0)
    if domain_info.get("parked"):
        return _result("domain_quality_gate", GateStatus.FAIL_CLOSED, ["parked_domain"], domain_info, score=0.0)
    if domain_info.get("domain_typo"):
        return _result("domain_quality_gate", GateStatus.REVIEW_REQUIRED, ["possible_domain_typo"], domain_info, score=0.45)
    mx_status = domain_info.get("mx_status")
    if mx_status == "absent":
        return _result("domain_quality_gate", GateStatus.FAIL_CLOSED, ["mx_absent"], domain_info, score=0.0)
    if mx_status in {"timeout", "greylist"}:
        return _result("domain_quality_gate", GateStatus.UNKNOWN_NOT_PROMOTED, ["mx_or_greylist_retry_required"], domain_info, score=0.35)
    if domain_info.get("catch_all"):
        return _result("domain_quality_gate", GateStatus.REVIEW_REQUIRED, ["catch_all_domain_requires_review"], domain_info, score=0.6)
    if domain_info.get("free_provider") or domain in _FREE_PROVIDER_DOMAINS:
        return _result("domain_quality_gate", GateStatus.REVIEW_REQUIRED, ["free_provider_requires_review"], domain_info, score=0.55)
    return _result("domain_quality_gate", GateStatus.PASS_, ["domain_quality_pass_mocked"], domain_info, score=1.0)


def suppression_gate(suppression: Mapping[str, Any] | None) -> GateResult:
    suppression = _as_mapping(suppression)
    if not suppression.get("snapshot_hash"):
        return _result("suppression_gate", GateStatus.UNKNOWN_NOT_PROMOTED, ["missing_suppression_snapshot"], suppression, score=0.0, export_eligibility=ExportEligibility.BLOCKED_UNKNOWN)
    status = suppression.get("status")
    if status == SuppressionStatus.CLEAR.value:
        return _result("suppression_gate", GateStatus.PASS_, ["suppression_clear"], suppression, score=1.0)
    if status in {SuppressionStatus.SUPPRESSION_UNKNOWN_HOLD.value, SuppressionStatus.SUPPRESSION_CONFLICT_HOLD.value}:
        return _result("suppression_gate", GateStatus.UNKNOWN_NOT_PROMOTED, ["suppression_unknown_or_conflict_hold"], suppression, score=0.0, export_eligibility=ExportEligibility.BLOCKED_UNKNOWN)
    return _result("suppression_gate", GateStatus.FAIL_CLOSED, ["suppression_blocks_export"], suppression, score=0.0, export_eligibility=ExportEligibility.BLOCKED_SUPPRESSED)


def enrichment_quality_gate(enrichment: Mapping[str, Any] | None) -> GateResult:
    enrichment = _as_mapping(enrichment)
    summary = str(enrichment.get("summary") or "").strip()
    supporting_fact_ids = enrichment.get("supporting_fact_ids") or []
    if enrichment.get("unsupported_claim_count", 0) > 0:
        return _result("enrichment_quality_gate", GateStatus.FAIL_CLOSED, ["unsupported_enrichment_claim"], enrichment, score=0.0)
    if not supporting_fact_ids:
        return _result("enrichment_quality_gate", GateStatus.UNKNOWN_NOT_PROMOTED, ["thin_or_uncited_enrichment"], enrichment, score=0.25)
    if len(summary) > 240:
        return _result("enrichment_quality_gate", GateStatus.FAIL_CLOSED, ["enrichment_too_long"], enrichment, score=0.0)
    if summary.lower() in {"great company!", "great company", "nice website", "impressive work"}:
        return _result("enrichment_quality_gate", GateStatus.FAIL_CLOSED, ["generic_icebreaker"], enrichment, score=0.0)
    if len(summary) < 30:
        return _result("enrichment_quality_gate", GateStatus.UNKNOWN_NOT_PROMOTED, ["thin_enrichment_content"], enrichment, score=0.35)
    return _result("enrichment_quality_gate", GateStatus.PASS_, ["source_backed_enrichment"], enrichment, score=1.0)


def outreach_readiness_gate(readiness: Mapping[str, Any] | None) -> GateResult:
    readiness = _as_mapping(readiness)
    if readiness.get("send_authorized"):
        return _result(
            "outreach_readiness_gate",
            GateStatus.FAIL_CLOSED,
            ["live_send_reserved_future_phase"],
            readiness,
            score=0.0,
            export_eligibility=ExportEligibility.FUTURE_LIVE_OUTREACH_RESERVED,
            send_authorized=False,
        )
    if readiness.get("join_failure"):
        return _result("outreach_readiness_gate", GateStatus.FAIL_CLOSED, ["handoff_join_failure"], readiness, score=0.0)
    required_statuses = (
        "sourceability_status",
        "identity_status",
        "icp_status",
        "contact_status",
        "email_status",
        "domain_status",
        "suppression_status",
        "enrichment_status",
    )
    failed = [name for name in required_statuses if not _is_status_pass(readiness.get(name))]
    if failed:
        eligibility = ExportEligibility.BLOCKED_UNKNOWN if "suppression_status" in failed else ExportEligibility.NOT_EXPORTABLE
        return _result("outreach_readiness_gate", GateStatus.FAIL_CLOSED, [f"upstream_gate_not_pass:{name}" for name in failed], readiness, score=0.0, export_eligibility=eligibility)
    if not readiness.get("cited_business_identity"):
        return _result("outreach_readiness_gate", GateStatus.FAIL_CLOSED, ["missing_cited_business_identity"], readiness, score=0.0, export_eligibility=ExportEligibility.BLOCKED_UNCITED)
    if not readiness.get("verified_contact_fact"):
        return _result("outreach_readiness_gate", GateStatus.FAIL_CLOSED, ["missing_verified_contact_fact"], readiness, score=0.0, export_eligibility=ExportEligibility.BLOCKED_UNVERIFIED_CONTACT)
    return _result(
        "outreach_readiness_gate",
        GateStatus.PASS_,
        ["internal_review_export_allowed_no_send"],
        readiness,
        score=1.0,
        export_eligibility=ExportEligibility.INTERNAL_REVIEW_ONLY,
        send_authorized=False,
    )


def benchmark_assumption_gate(payload: Mapping[str, Any] | None) -> GateResult:
    payload = _as_mapping(payload)
    if not payload.get("assumption_label"):
        return _result("benchmark_assumption_gate", GateStatus.REVIEW_REQUIRED, ["apollo_benchmark_must_be_assumption_labelled"], payload, score=0.0)
    return _result("benchmark_assumption_gate", GateStatus.PASS_, ["benchmark_assumption_labelled"], payload, score=1.0)


def evaluate_legacy_pitfall_fixture(fixture: Mapping[str, Any]) -> GateResult:
    """Route a cataloged pitfall fixture through the relevant pure gate."""

    gate = fixture.get("gate")
    if gate == "sourceability":
        return sourceability_gate(fixture)
    if gate == "identity":
        return identity_gate(fixture)
    if gate == "contact_observation":
        return contact_observation_gate(fixture)
    if gate == "domain_quality":
        return domain_quality_gate(fixture)
    if gate == "enrichment_quality":
        return enrichment_quality_gate(fixture)
    if gate == "suppression":
        return suppression_gate(fixture)
    if gate == "outreach_readiness":
        return outreach_readiness_gate(fixture)
    if gate == "benchmark_assumption":
        return benchmark_assumption_gate(fixture)
    return _result("legacy_pitfall_router", GateStatus.FAIL_CLOSED, ["unknown_legacy_pitfall_gate"], fixture, score=0.0)
