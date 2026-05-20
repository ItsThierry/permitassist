"""PermitAssist lead pipeline Phase 1 fixture-only contracts and gates.

Phase 1 remains intentionally limited to schema metadata, enums, docs,
fixture-only gate functions, and tests. This package performs no network,
paid-provider, outreach, production, or customer-visible work.
"""

from .adapters import (
    PERMITASSIST_ADAPTER_ID,
    AdapterPolicyError,
    PermitAssistAdapterPolicy,
    enforce_adapter_policy_for_connector,
    get_permitassist_adapter_policy,
)
from .connectors import (
    FIXTURE_CONNECTOR_REGISTRY,
    ConnectorPolicyStatus,
    ConnectorRunResult,
    ConnectorSpec,
    FetchMode,
    FixtureDocument,
    LiveFetchAttemptError,
    UnknownConnectorError,
    get_connector_spec,
    run_fixture_connector,
)
from .contracts import GateStatus, PromotionTier, assert_phase1_promotion_allowed
from .gates import GateResult
from .schema import PHASE1_SCHEMA_VERSION, REQUIRED_TABLES, get_table_contracts

__all__ = [
    "AdapterPolicyError",
    "ConnectorPolicyStatus",
    "ConnectorRunResult",
    "ConnectorSpec",
    "FIXTURE_CONNECTOR_REGISTRY",
    "FetchMode",
    "FixtureDocument",
    "GateResult",
    "GateStatus",
    "LiveFetchAttemptError",
    "PERMITASSIST_ADAPTER_ID",
    "PHASE1_SCHEMA_VERSION",
    "PermitAssistAdapterPolicy",
    "PromotionTier",
    "REQUIRED_TABLES",
    "UnknownConnectorError",
    "assert_phase1_promotion_allowed",
    "enforce_adapter_policy_for_connector",
    "get_connector_spec",
    "get_permitassist_adapter_policy",
    "get_table_contracts",
    "run_fixture_connector",
]
