#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
PYTEST_CMD=(uv run --with pytest python -m pytest)
ARTIFACT_DIR="${PERMITASSIST_GATE_ARTIFACT_DIR:-$ROOT/artifacts/titi_build_20260706_session2/run_gates}"
mkdir -p "$ARTIFACT_DIR"

echo "RUN_GATES_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee "$ARTIFACT_DIR/run_gates_summary.txt"
echo "HEAD=$(git rev-parse HEAD)" | tee -a "$ARTIFACT_DIR/run_gates_summary.txt"
echo "STATUS_BEFORE" | tee -a "$ARTIFACT_DIR/run_gates_summary.txt"
git status --short | tee -a "$ARTIFACT_DIR/run_gates_summary.txt"

$PYTHON_BIN -m py_compile \
  api/public_packet.py \
  api/customer_pipeline.py \
  api/server.py \
  api/live100_fable5_final_gate.py \
  api/research_engine.py \
  scripts/phase4_data_coverage_report.py \
  scripts/phase6_pipeline_benchmark.py \
  scripts/split_app_cache_db.py \
  tests/conftest.py \
  tests/test_session2_phase3_contracts.py \
  tests/test_phase4_data_coverage_report.py \
  tests/test_session2_phase5_harness_contracts.py \
  tests/test_session3_phase6_phase7_contracts.py \
  2>&1 | tee "$ARTIFACT_DIR/py_compile.txt"

"${PYTEST_CMD[@]}" -q \
  tests/test_session2_phase3_contracts.py \
  tests/test_phase4_data_coverage_report.py \
  tests/test_session2_phase5_harness_contracts.py \
  tests/test_session3_phase6_phase7_contracts.py \
  tests/test_degraded_fallback_contract.py \
  tests/test_prod_mode_invariants.py \
  tests/test_seal_is_last_mutation.py \
  tests/test_session1_p1_contracts.py \
  tests/test_no_pytest_branches_in_api.py \
  2>&1 | tee "$ARTIFACT_DIR/pytest_focused.txt"

$PYTHON_BIN scripts/phase4_data_coverage_report.py --output "$ARTIFACT_DIR/phase4_data_coverage_report.json" \
  2>&1 | tee "$ARTIFACT_DIR/phase4_report_stdout.txt"

echo "RUN_GATES_OK=1" | tee -a "$ARTIFACT_DIR/run_gates_summary.txt"
