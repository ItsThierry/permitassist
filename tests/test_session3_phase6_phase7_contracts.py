from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import customer_pipeline  # noqa: E402
import family_reconciliation_gate  # noqa: E402
import live100_fable5_final_gate  # noqa: E402
import residential_universal_gate  # noqa: E402
import server  # noqa: E402


def test_phase6_pipeline_registry_has_reviewable_terminal_order() -> None:
    order = customer_pipeline.pipeline_order()
    customer_pipeline.assert_pipeline_order_valid(order)
    assert order == (
        "locality",
        "family_reconciliation",
        "ahj_identity",
        "closed_world",
        "final_gate",
        "recovery_guard",
        "projection",
        "seal",
    )


def test_phase6_server_uses_customer_pipeline_registry() -> None:
    source = (ROOT / "api" / "server.py").read_text()
    assert "run_pipeline_through_projection(" in source
    assert "CustomerPipelineContext(" in source
    first_pipeline = source.rindex("run_pipeline_through_projection(")
    final_gate = source.rindex("apply_fable5_final_customer_gate(")
    seal = source.rindex("apply_render_parity_seal(")
    recovery_guard = source.rindex("_live100_core_truth_recovery_guard(")
    projection = source.rindex("apply_public_packet_projection(")
    assert first_pipeline < final_gate < recovery_guard < projection < seal


def test_phase6_pipeline_audit_key_is_not_customer_visible() -> None:
    payload = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_name": "Building Permit",
        "permit_kind": "Building",
        "applying_office": "Phoenix Planning and Development Department",
        "source_urls": ["https://www.phoenix.gov/pdd"],
        "sources": [{"url": "https://www.phoenix.gov/pdd", "title": "Phoenix PDD"}],
        "permits_required": [
            {"permit_type": "Building Permit", "family": "building", "required": True, "decision": "REQUIRED"}
        ],
    }
    public = server.build_customer_permit_view_model(
        payload,
        "single-family kitchen remodel, no exterior work",
        "Phoenix",
        "AZ",
        job_category="residential",
    )
    assert "_customer_pipeline_gate_audit" not in public
    assert not any(str(key).startswith("_") for key in public)


def test_phase6_gate_idempotence_on_golden_payload() -> None:
    payload = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_name": "Building Permit",
        "permit_kind": "Building",
        "applying_office": "Phoenix Planning and Development Department",
        "source_urls": ["https://www.phoenix.gov/pdd"],
        "sources": [{"url": "https://www.phoenix.gov/pdd", "title": "Phoenix PDD"}],
        "permits_required": [
            {"permit_type": "Building Permit", "family": "building", "required": True, "decision": "REQUIRED"}
        ],
    }
    job = "single-family kitchen remodel, no exterior work"
    scope_contract = {"category": "residential", "family": "residential_remodel"}
    once = residential_universal_gate.apply_residential_universal_gate(payload, job, "Phoenix", "AZ", scope_contract=scope_contract)
    twice = residential_universal_gate.apply_residential_universal_gate(once, job, "Phoenix", "AZ", scope_contract=scope_contract)
    assert twice == once

    once = family_reconciliation_gate.apply_family_reconciliation_gate(payload, job, "Phoenix", "AZ", scope_contract, job_category="residential")
    twice = family_reconciliation_gate.apply_family_reconciliation_gate(once, job, "Phoenix", "AZ", scope_contract, job_category="residential")
    assert twice == once

    once = live100_fable5_final_gate.apply_fable5_final_customer_gate(payload, job, "Phoenix", "AZ", scope_contract, None)
    twice = live100_fable5_final_gate.apply_fable5_final_customer_gate(once, job, "Phoenix", "AZ", scope_contract, None)
    assert twice == once


def test_phase6_frontend_shared_render_module_and_backup_ignores() -> None:
    js_path = ROOT / "frontend" / "js" / "render_result.js"
    assert js_path.exists()
    js = js_path.read_text()
    assert "PermitAssistRender" in js
    for rel in [
        "frontend/index.html",
        "frontend/trades/electrical.html",
        "frontend/trades/hvac.html",
        "frontend/trades/plumbing.html",
        "frontend/trades/roofing.html",
        "frontend/trades/solar.html",
    ]:
        assert "/js/render_result.js" in (ROOT / rel).read_text(), rel
    ignore = (ROOT / ".gitignore").read_text()
    assert "*.bak" in ignore
    assert "*.backup" in ignore
    tracked_backups = subprocess.check_output(["git", "ls-files", "*.bak", "*.backup"], cwd=ROOT, text=True).strip()
    assert tracked_backups == ""


def test_phase7_marketing_csvs_untracked_but_history_rewrite_not_required() -> None:
    tracked_csvs = subprocess.check_output(["git", "ls-files", "marketing/*.csv"], cwd=ROOT, text=True).strip()
    assert tracked_csvs == ""
    ignore = (ROOT / ".gitignore").read_text()
    assert "marketing/*.csv" in ignore


def _load_split_module():
    path = ROOT / "scripts" / "split_app_cache_db.py"
    spec = importlib.util.spec_from_file_location("split_app_cache_db", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def test_phase7_db_split_script_local_fixture_only(tmp_path: Path) -> None:
    split = _load_split_module()
    source = tmp_path / "source.db"
    app_db = tmp_path / "app.db"
    cache_db = tmp_path / "cache.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
        conn.execute("CREATE TABLE user_sessions (token TEXT PRIMARY KEY, user_id INTEGER)")
        conn.execute("CREATE TABLE magic_tokens (token TEXT PRIMARY KEY, email TEXT)")
        conn.execute("CREATE TABLE api_keys (key TEXT PRIMARY KEY, user_id INTEGER)")
        conn.execute("CREATE TABLE webhook_integrations (id INTEGER PRIMARY KEY, callback_url TEXT)")
        conn.execute("CREATE TABLE permit_cache (cache_key TEXT PRIMARY KEY, result_json TEXT)")
        conn.execute("CREATE TABLE search_cache (cache_key TEXT PRIMARY KEY, payload_json TEXT)")
        conn.execute("INSERT INTO users VALUES (1, 'local@example.test')")
        conn.execute("INSERT INTO permit_cache VALUES ('k', '{}')")
        conn.commit()
    result = split.split_db(source, app_db, cache_db)
    assert "users" in result["app_tables"]
    assert "permit_cache" in result["cache_tables"]
    assert {"users", "user_sessions", "magic_tokens", "api_keys", "webhook_integrations"}.issubset(_tables(app_db))
    assert {"permit_cache", "search_cache"}.issubset(_tables(cache_db))
    assert "permit_cache" not in _tables(app_db)
    assert "users" not in _tables(cache_db)


def test_phase7_sensitive_paths_documented_without_pii_content_contract() -> None:
    doc = (ROOT / "docs" / "sensitive_paths.md").read_text()
    assert "Do not open or scan contents" in doc
    assert "marketing/lead-registry/ready_lead_registry.sqlite" in doc
    assert "marketing/*.csv" in doc
    assert "data/verified_cities.db" in doc
    assert "knowledge/verified_cities.db" in doc
    assert "JSON copies are non-authoritative" in doc


def test_phase7_job_category_canonicalized_for_runtime_cache_and_telemetry() -> None:
    cases = [
        # Ambiguous/minimal scopes preserve the explicit customer segment rather
        # than being eaten by a residential default.
        ("interior repaint lobby no walls no MEP", "commercial", "commercial"),
        ("repaint lobby no walls no MEP", "commercial", "commercial"),
        ("bathroom remodel", "commercial", "commercial"),
        ("kitchen remodel", "commercial", "commercial"),
        ("tenant kitchen remodel", "commercial", "commercial"),
        # Clearly contradictory text wins over stale/default client labels.
        ("commercial tenant improvement for restaurant with Type I hood", "residential", "commercial"),
        ("commercial kitchen remodel for restaurant", "residential", "commercial"),
        ("single-family kitchen remodel", "commercial", "residential"),
        ("single-family bathroom remodel with no layout changes", "commercial", "residential"),
    ]
    for job_type, explicit_category, expected in cases:
        assert server.canonical_request_job_category(
            job_type,
            "Phoenix",
            "AZ",
            explicit_category,
            None,
        ) == expected


def test_phase8_local_rc_proof_plan_is_plan_only() -> None:
    plan = (ROOT / "artifacts" / "titi_build_20260706_session3" / "SESSION3_PHASE8_LOCAL_RC_PROOF_PLAN.md").read_text()
    forbidden = ["Do not deploy", "Do not", "Approval-gated steps not executed"]
    assert all(item in plan for item in forbidden)
    assert "Railway volume backup" in plan
    assert "paid/live" in plan
