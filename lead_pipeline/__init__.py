"""PermitAssist lead pipeline Phase 1 fixture-only contracts and gates.

Phase 1 remains intentionally limited to schema metadata, enums, docs,
fixture-only gate functions, and tests. This package performs no network,
paid-provider, outreach, production, or customer-visible work.
"""

from .contracts import GateStatus, PromotionTier, assert_phase1_promotion_allowed
from .gates import GateResult
from .schema import PHASE1_SCHEMA_VERSION, REQUIRED_TABLES, get_table_contracts

__all__ = [
    "GateResult",
    "GateStatus",
    "PHASE1_SCHEMA_VERSION",
    "PromotionTier",
    "REQUIRED_TABLES",
    "assert_phase1_promotion_allowed",
    "get_table_contracts",
]
