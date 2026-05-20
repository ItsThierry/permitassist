import ast
import sqlite3
from pathlib import Path

import pytest

from lead_pipeline.contracts import (
    CostStage,
    EntityKind,
    ExportEligibility,
    FactField,
    GateStatus,
    Phase1ContractError,
    PromotionTier,
    SourceClass,
    SuppressionStatus,
    assert_phase1_promotion_allowed,
)
from lead_pipeline.schema import (
    APPEND_ONLY_TABLES,
    PHASE1_SCHEMA_VERSION,
    REQUIRED_TABLES,
    create_sqlite_schema,
    get_table_contract,
    get_table_contracts,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
NETWORK_IMPORTS = {
    "requests",
    "httpx",
    "urllib",
    "socket",
    "smtplib",
    "dns",
    "aiohttp",
    "selenium",
    "playwright",
    "firecrawl",
    "brave",
}


def test_required_milestone1_tables_are_defined():
    contracts = get_table_contracts()

    assert set(contracts) == set(REQUIRED_TABLES)
    assert list(REQUIRED_TABLES) == [
        "batches",
        "sources",
        "source_observations",
        "entities",
        "facts",
        "identity_edges",
        "verification_events",
        "enrichment_events",
        "suppression_events",
        "export_events",
        "cost_events",
        "human_review_events",
    ]
    for table_name in REQUIRED_TABLES:
        contract = contracts[table_name]
        assert contract.table_name == table_name
        assert contract.schema_version == PHASE1_SCHEMA_VERSION
        assert contract.primary_key
        assert contract.columns[contract.primary_key].required is True


def test_contracts_include_required_enums_and_phase1_blocks_tier7():
    assert SourceClass.OFFICIAL_LICENSING.value == "official_licensing_registry"
    assert SourceClass.FIRST_PARTY_WEBSITE.value == "first_party_website"
    assert EntityKind.BUSINESS.value == "business"
    assert FactField.CONTACT_EMAIL.value == "contact_email"
    assert GateStatus.UNKNOWN_NOT_PROMOTED.value == "unknown_not_promoted"
    assert SuppressionStatus.SUPPRESSION_UNKNOWN_HOLD.value == "suppression_unknown_hold"
    assert ExportEligibility.INTERNAL_REVIEW_ONLY.value == "internal_review_only"
    assert CostStage.HUMAN_REVIEW.value == "human_review"

    assert_phase1_promotion_allowed(PromotionTier.OUTREACH_READY_INTERNAL_ONLY)
    with pytest.raises(Phase1ContractError):
        assert_phase1_promotion_allowed(PromotionTier.LIVE_OUTREACH_READY_FUTURE_PHASE)


def test_fact_and_event_contracts_preserve_lineage_columns():
    facts = get_table_contract("facts")
    assert facts.append_only is True
    assert facts.columns["entity_id"].references == ("entities", "entity_id")
    assert facts.columns["source_observation_id"].references == (
        "source_observations",
        "observation_id",
    )
    assert facts.columns["promoted_by_gate_event_id"].references == (
        "verification_events",
        "verification_event_id",
    )
    assert facts.columns["supersedes_fact_id"].references == ("facts", "fact_id")
    assert facts.columns["field_relevant_snippet"].required is True

    for table_name in [
        "verification_events",
        "enrichment_events",
        "suppression_events",
        "export_events",
        "cost_events",
        "human_review_events",
    ]:
        contract = get_table_contract(table_name)
        assert "batch_id" in contract.columns or table_name == "human_review_events"
        assert contract.lineage_columns, table_name

    export_events = get_table_contract("export_events")
    for column_name in [
        "included_fact_ids",
        "included_source_observation_ids",
        "included_verification_event_ids",
        "suppression_event_id",
        "human_review_event_id",
        "signed_payload_hash_sha256",
        "send_authorized",
    ]:
        assert column_name in export_events.columns
    assert export_events.columns["send_authorized"].default is False


def test_append_only_tables_have_sqlite_update_and_delete_guards():
    ddl = create_sqlite_schema()
    conn = sqlite3.connect(":memory:")
    conn.executescript(ddl)

    trigger_rows = conn.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger'"
    ).fetchall()
    trigger_names = {row[0] for row in trigger_rows}
    for table_name in APPEND_ONLY_TABLES:
        assert f"{table_name}_append_only_no_update" in trigger_names
        assert f"{table_name}_append_only_no_delete" in trigger_names

    conn.execute(
        "INSERT INTO batches(batch_id, approved_scope_ref, adapter_id, started_at_utc, status, schema_version) "
        "VALUES ('batch_fixture', 'phase1_fixture_only', 'permitassist', '2026-05-20T00:00:00Z', 'open', ?) ",
        (PHASE1_SCHEMA_VERSION,),
    )
    conn.execute(
        "INSERT INTO sources(source_id, source_class, source_name, base_url_or_path, official_or_first_party_flag, requires_login, paid_flag, allowed_phase, schema_version) "
        "VALUES ('source_fixture', 'first_party_website', 'Fixture Source', 'fixture://source', 1, 0, 0, 'phase1_fixture_only', ?)",
        (PHASE1_SCHEMA_VERSION,),
    )
    conn.execute(
        "INSERT INTO source_observations(observation_id, source_id, batch_id, observed_at_utc, url_or_path, payload_hash_sha256, snippet_or_excerpt, blocked_or_captcha_flag, schema_version) "
        "VALUES ('obs_fixture', 'source_fixture', 'batch_fixture', '2026-05-20T00:00:00Z', 'fixture://source/page', 'hash_fixture', 'commercial TI fixture snippet', 0, ?)",
        (PHASE1_SCHEMA_VERSION,),
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE source_observations SET snippet_or_excerpt = 'changed' WHERE observation_id = 'obs_fixture'"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "DELETE FROM source_observations WHERE observation_id = 'obs_fixture'"
        )


def test_schema_foreign_keys_require_fact_and_event_lineage():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(create_sqlite_schema())

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO facts(fact_id, entity_id, fact_type, fact_value, promotion_status, source_observation_id, field_relevant_snippet, schema_version) "
            "VALUES ('fact_without_lineage', 'missing_entity', 'business_name', 'Fixture Co', 'candidate', 'missing_observation', 'fixture snippet', ?)",
            (PHASE1_SCHEMA_VERSION,),
        )


def test_lead_pipeline_package_has_no_network_or_paid_provider_imports():
    offenders = []
    for path in sorted((REPO_ROOT / "lead_pipeline").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported = []
            if isinstance(node, ast.Import):
                imported = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module.split(".")[0]]
            for module_name in imported:
                if module_name in NETWORK_IMPORTS:
                    offenders.append(f"{path.relative_to(REPO_ROOT)} imports {module_name}")
    assert not offenders, "Phase 1 M1 must stay fixture-only/no-network:\n" + "\n".join(offenders)
