"""PermitAssist adapter policy for Phase 1 M3.

The adapter policy names the first ICP slice (commercial tenant-improvement
general contractors, design-build firms, and remodeling firms), the allowed
and blocked source classes, the allowed and blocked fixture connectors, and
a hard internal-review-only export ceiling. It explicitly forbids live
outreach and any path that could authorize sending.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .contracts import ExportEligibility, SourceClass
from .connectors import (
    FIXTURE_CONNECTOR_REGISTRY,
    ConnectorPolicyStatus,
    ConnectorSpec,
    UnknownConnectorError,
)

PERMITASSIST_ADAPTER_ID = "permitassist"
PERMITASSIST_ALLOWED_PHASE = "phase1_fixture_only"


class AdapterPolicyError(RuntimeError):
    """Raised when a connector is not allowed for this adapter in Phase 1."""


@dataclass(frozen=True)
class PermitAssistAdapterPolicy:
    """Immutable Phase 1 adapter policy for PermitAssist."""

    adapter_id: str
    icp_segments: tuple[str, ...]
    allowed_source_classes: frozenset[SourceClass]
    blocked_source_classes: frozenset[SourceClass]
    allowed_connector_ids: tuple[str, ...]
    blocked_connector_ids: tuple[str, ...]
    blocked_connector_reasons: Mapping[str, str]
    export_ceiling: ExportEligibility
    live_outreach_allowed: bool
    send_authorized_allowed: bool
    precision_first: bool
    allowed_phase: str
    review_only_notes: str

    def is_connector_allowed(self, connector_id: str) -> bool:
        return connector_id in self.allowed_connector_ids


_PERMITASSIST_ICP_SEGMENTS: tuple[str, ...] = (
    "commercial_tenant_improvement_gc_design_build_remodeling",
)

_ALLOWED_SOURCE_CLASSES: frozenset[SourceClass] = frozenset(
    {
        SourceClass.OFFICIAL_LICENSING,
        SourceClass.FIRST_PARTY_WEBSITE,
        SourceClass.USER_IMPORT,
    }
)

_BLOCKED_SOURCE_CLASSES: frozenset[SourceClass] = frozenset(
    {
        SourceClass.PURCHASED_VENDOR,
        SourceClass.SOCIAL_PROFILE,
        SourceClass.SCRAPED_PUBLIC_PAGE,
        SourceClass.SEARCH_OR_PLACES,
        SourceClass.AGGREGATOR_DIRECTORY,
    }
)


def _build_policy_from_registry(registry: Mapping[str, ConnectorSpec]) -> PermitAssistAdapterPolicy:
    allowed_ids: list[str] = []
    blocked_ids: list[str] = []
    blocked_reasons: dict[str, str] = {}
    for connector_id, spec in registry.items():
        if spec.policy_status == ConnectorPolicyStatus.ALLOWED_FIXTURE_PHASE1:
            if spec.source_class not in _ALLOWED_SOURCE_CLASSES:
                raise AdapterPolicyError(
                    f"Allowed connector {connector_id!r} has disallowed source class "
                    f"{spec.source_class.value!r} for PermitAssist Phase 1."
                )
            allowed_ids.append(connector_id)
        else:
            blocked_ids.append(connector_id)
            reason = spec.blocked_reason or spec.policy_status.value
            blocked_reasons[connector_id] = f"{spec.policy_status.value}: {reason}"

    return PermitAssistAdapterPolicy(
        adapter_id=PERMITASSIST_ADAPTER_ID,
        icp_segments=_PERMITASSIST_ICP_SEGMENTS,
        allowed_source_classes=_ALLOWED_SOURCE_CLASSES,
        blocked_source_classes=_BLOCKED_SOURCE_CLASSES,
        allowed_connector_ids=tuple(sorted(allowed_ids)),
        blocked_connector_ids=tuple(sorted(blocked_ids)),
        blocked_connector_reasons=MappingProxyType(blocked_reasons),
        export_ceiling=ExportEligibility.INTERNAL_REVIEW_ONLY,
        live_outreach_allowed=False,
        send_authorized_allowed=False,
        precision_first=True,
        allowed_phase=PERMITASSIST_ALLOWED_PHASE,
        review_only_notes=(
            "PermitAssist Phase 1 M3: internal review only. No live outreach, no SMTP, "
            "no paid APIs, no scraping, no login-required platforms, no customer-visible "
            "output."
        ),
    )


_PERMITASSIST_POLICY: PermitAssistAdapterPolicy = _build_policy_from_registry(FIXTURE_CONNECTOR_REGISTRY)


def get_permitassist_adapter_policy() -> PermitAssistAdapterPolicy:
    """Return the immutable Phase 1 PermitAssist adapter policy."""

    return _PERMITASSIST_POLICY


def enforce_adapter_policy_for_connector(connector_id: str) -> ConnectorPolicyStatus:
    """Return the connector's policy status; raise ``AdapterPolicyError`` if blocked.

    The error message embeds the exact blocked-status value so callers can
    assert on a specific policy outcome (paid vs. login vs. paid_api vs.
    scrape) rather than a broad blocked category.
    """

    try:
        spec = FIXTURE_CONNECTOR_REGISTRY[connector_id]
    except KeyError as exc:
        raise UnknownConnectorError(f"Unknown connector id: {connector_id!r}") from exc

    if spec.policy_status == ConnectorPolicyStatus.ALLOWED_FIXTURE_PHASE1:
        return spec.policy_status

    raise AdapterPolicyError(
        f"PermitAssist adapter policy blocks connector {connector_id!r}: "
        f"{spec.policy_status.value} ({spec.blocked_reason})"
    )


def assert_no_live_outreach(policy: PermitAssistAdapterPolicy | None = None) -> None:
    """Hard guarantee: never authorize live sending in Phase 1."""

    effective = policy or get_permitassist_adapter_policy()
    if effective.live_outreach_allowed or effective.send_authorized_allowed:
        raise AdapterPolicyError(
            "PermitAssist Phase 1 policy must never permit live outreach or send_authorized."
        )
    if effective.export_ceiling != ExportEligibility.INTERNAL_REVIEW_ONLY:
        raise AdapterPolicyError(
            "PermitAssist Phase 1 export ceiling must be INTERNAL_REVIEW_ONLY."
        )
