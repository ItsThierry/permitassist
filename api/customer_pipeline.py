from __future__ import annotations

"""Ordered customer-boundary pipeline registry for PermitAssist.

This module is intentionally small: it makes the late customer gate order
reviewable/testable without changing the public contract.  `server.py` still owns
request-specific fallback/telemetry and final render-seal error handling.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

try:  # top-level imports when api/ is on sys.path in production/tests
    from ahj_identity_guard import apply_ahj_identity_guard
    from ahj_locality_resolver import apply_ahj_locality_resolution
    from closed_world_decision import apply_closed_world_customer_contract
    from family_reconciliation_gate import apply_family_reconciliation_gate
    from public_packet import apply_public_packet_projection
except Exception:  # pragma: no cover - package import compatibility
    from api.ahj_identity_guard import apply_ahj_identity_guard
    from api.ahj_locality_resolver import apply_ahj_locality_resolution
    from api.closed_world_decision import apply_closed_world_customer_contract
    from api.family_reconciliation_gate import apply_family_reconciliation_gate
    from api.public_packet import apply_public_packet_projection

PIPELINE: tuple[str, ...] = (
    "locality",
    "family_reconciliation",
    "ahj_identity",
    "closed_world",
    # Terminal runtime order is final gate -> recovery guard -> projection -> seal.
    # server.py also performs an earlier pre-final projection and may re-project
    # degraded-source payloads; this registry intentionally captures the final
    # customer-boundary terminal segment that must precede the render seal.
    "final_gate",
    "recovery_guard",
    "projection",
    "seal",
)

PRE_PROJECTION_GATES: tuple[str, ...] = (
    "locality",
    "family_reconciliation",
    "ahj_identity",
    "closed_world",
)


@dataclass(frozen=True)
class CustomerPipelineContext:
    job_type: str
    city: str
    state: str
    scope_contract: dict[str, Any]
    job_category: str | None = None
    scope_facts: Any | None = None


def pipeline_order() -> tuple[str, ...]:
    """Return the canonical final customer pipeline order."""
    return PIPELINE


def _with_audit(payload: dict[str, Any], gate: str) -> dict[str, Any]:
    audit = list(payload.get("_customer_pipeline_gate_audit") or [])
    audit.append({"gate": gate})
    payload["_customer_pipeline_gate_audit"] = audit
    return payload


def _apply_gate(gate: str, payload: dict[str, Any], ctx: CustomerPipelineContext) -> dict[str, Any]:
    if gate == "locality":
        out = apply_ahj_locality_resolution(payload, ctx.city, ctx.state, ctx.job_type)
    elif gate == "family_reconciliation":
        out = apply_family_reconciliation_gate(
            payload,
            ctx.job_type,
            ctx.city,
            ctx.state,
            ctx.scope_contract,
            job_category=ctx.job_category,
        )
    elif gate == "ahj_identity":
        out = apply_ahj_identity_guard(payload, ctx.city, ctx.state, ctx.job_type)
    elif gate == "closed_world":
        out = apply_closed_world_customer_contract(
            payload,
            ctx.job_type,
            ctx.city,
            ctx.state,
            job_category=ctx.job_category,
            scope_facts_v2=ctx.scope_facts,
        )
    else:  # defensive; registry tests should prevent this from being reachable.
        raise ValueError(f"unknown customer pipeline gate: {gate}")
    return _with_audit(out if isinstance(out, dict) else {}, gate)


def apply_pre_projection_pipeline(payload: dict[str, Any], ctx: CustomerPipelineContext) -> dict[str, Any]:
    """Run the ordered pre-projection customer gates.

    The input is copied once at the server boundary.  Individual gates may still
    copy internally while they are being migrated, but this helper prevents the
    server from growing another ad-hoc gate chain and gives T-091 a stable
    benchmark seam.
    """
    out = payload if isinstance(payload, dict) else {}
    for gate in PRE_PROJECTION_GATES:
        out = _apply_gate(gate, out, ctx)
    return out


def apply_projection(payload: dict[str, Any], ctx: CustomerPipelineContext) -> dict[str, Any]:
    """Project a public packet at the current pipeline seam.

    server.py uses this both for the pre-final projection seam and again after
    the final gate/recovery guard; the render seal remains the terminal mutation
    guard in server.py.
    """
    out = apply_public_packet_projection(payload if isinstance(payload, dict) else {}, ctx.scope_facts)
    return _with_audit(out if isinstance(out, dict) else {}, "projection")


def run_pipeline_through_projection(payload: dict[str, Any], ctx: CustomerPipelineContext) -> dict[str, Any]:
    """Convenience wrapper used by server.py before later final-gate/recovery steps."""
    return apply_projection(apply_pre_projection_pipeline(payload, ctx), ctx)


def assert_pipeline_order_valid(order: tuple[str, ...] | None = None) -> None:
    """Static contract used by tests and local proof scripts."""
    order = tuple(order or PIPELINE)
    assert order[-1] == "seal", order
    assert order.index("projection") < order.index("seal"), order
    assert order.index("final_gate") < order.index("recovery_guard") < order.index("projection") < order.index("seal"), order
    for gate in PRE_PROJECTION_GATES:
        assert gate in order, order
