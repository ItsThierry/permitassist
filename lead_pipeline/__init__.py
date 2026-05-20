"""PermitAssist lead pipeline Phase 1 fixture-only schema contracts.

Milestone 1 is intentionally limited to schema metadata, enums, docs, and
fixture tests. This package performs no network, paid-provider, outreach,
production, or customer-visible work.
"""

from .contracts import PromotionTier, assert_phase1_promotion_allowed
from .schema import PHASE1_SCHEMA_VERSION, REQUIRED_TABLES, get_table_contracts

__all__ = [
    "PHASE1_SCHEMA_VERSION",
    "PromotionTier",
    "REQUIRED_TABLES",
    "assert_phase1_promotion_allowed",
    "get_table_contracts",
]
