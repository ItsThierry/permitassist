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
from .event_writer import (
    PERSISTENCE_VERSION,
    PersistenceSafetyError,
    WriteSummary,
    initialize_sqlite_schema,
    write_connector_run_result,
)
from .gates import GateResult
from .phase1_runner import (
    PHASE1_M8_RUNNER_VERSION,
    Phase1PipelineRunResult,
    Phase1PipelineSummary,
    Phase1RunnerSafetyError,
    run_phase1_fixture_pipeline,
)
from .review_artifacts import (
    INTERNAL_REVIEW_BANNER,
    PHASE1_M9_ARTIFACT_SCHEMA_VERSION,
    InternalReviewArtifact,
    InternalReviewArtifactSafetyError,
    WrittenInternalReviewArtifacts,
    render_internal_review_artifacts,
    write_internal_review_artifacts,
)
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
    "INTERNAL_REVIEW_BANNER",
    "InternalReviewArtifact",
    "InternalReviewArtifactSafetyError",
    "LiveFetchAttemptError",
    "PERMITASSIST_ADAPTER_ID",
    "PERSISTENCE_VERSION",
    "PHASE1_M8_RUNNER_VERSION",
    "PHASE1_M9_ARTIFACT_SCHEMA_VERSION",
    "PHASE1_SCHEMA_VERSION",
    "Phase1PipelineRunResult",
    "Phase1PipelineSummary",
    "Phase1RunnerSafetyError",
    "PermitAssistAdapterPolicy",
    "PersistenceSafetyError",
    "PromotionTier",
    "REQUIRED_TABLES",
    "UnknownConnectorError",
    "WriteSummary",
    "WrittenInternalReviewArtifacts",
    "assert_phase1_promotion_allowed",
    "enforce_adapter_policy_for_connector",
    "get_connector_spec",
    "get_permitassist_adapter_policy",
    "get_table_contracts",
    "initialize_sqlite_schema",
    "render_internal_review_artifacts",
    "run_fixture_connector",
    "run_phase1_fixture_pipeline",
    "write_connector_run_result",
    "write_internal_review_artifacts",
]
