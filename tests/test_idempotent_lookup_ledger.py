import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.lookup_execution_ledger import (  # noqa: E402
    IdempotencyConflictError,
    LookupExecutionLedger,
)


def test_same_key_concurrency_elects_one_execution_and_one_digest():
    with tempfile.TemporaryDirectory() as directory:
        ledger = LookupExecutionLedger(Path(directory) / "ledger.db", claim_ttl_seconds=60)
        fingerprint = ledger.request_fingerprint({"job_type": "panel upgrade", "city": "Example", "state": "EX"})

        def claim(index):
            return ledger.claim("same-key", fingerprint, request_id=f"request-{index}").action

        with ThreadPoolExecutor(max_workers=100) as executor:
            actions = list(executor.map(claim, range(100)))
        assert actions.count("execute") == 1
        assert actions.count("in_progress") == 99

        owner = ledger.claim("same-key", fingerprint, request_id="ignored")
        assert owner.action == "in_progress"
        with ledger._connect() as conn:
            request_id = conn.execute(
                "SELECT request_id FROM lookup_executions WHERE idempotency_key='same-key'"
            ).fetchone()["request_id"]
        response = {"permit_decision": "CONDITIONAL", "family_decisions": []}
        with ledger._connect() as conn:
            execution_token = conn.execute(
                "SELECT execution_token FROM lookup_executions WHERE idempotency_key='same-key'"
            ).fetchone()["execution_token"]
        digest = ledger.complete(
            "same-key", fingerprint, response, request_id=request_id,
            execution_token=execution_token,
        )
        replay = ledger.replay("same-key")
        assert replay is not None
        assert replay.response == response
        assert replay.response_digest == digest


def test_body_conflict_restart_replay_and_stale_claim_recovery():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "ledger.db"
        first = LookupExecutionLedger(path, claim_ttl_seconds=10)
        fingerprint = first.request_fingerprint({"scope": "one"})
        assert first.claim("key", fingerprint, request_id="first", now=100).action == "execute"
        try:
            first.claim("key", first.request_fingerprint({"scope": "two"}), request_id="conflict", now=101)
        except IdempotencyConflictError:
            pass
        else:
            raise AssertionError("same key with a different body must conflict")
        assert first.claim("key", fingerprint, request_id="recovered", now=111).action == "indeterminate"
        with first._connect() as conn:
            conn.execute(
                "UPDATE lookup_executions SET state='FAILED' WHERE idempotency_key='key'"
            )
        recovered = first.claim("key", fingerprint, request_id="recovered", now=112)
        assert recovered.action == "execute"
        response = {"ok": True}
        first.complete(
            "key", fingerprint, response, request_id="recovered",
            execution_token=recovered.execution_token or "", now=113,
        )

        restarted = LookupExecutionLedger(path, claim_ttl_seconds=10)
        replay = restarted.claim("key", fingerprint, request_id="after-restart", now=114)
        assert replay.action == "replay"
        assert json.dumps(replay.response, sort_keys=True) == json.dumps(response, sort_keys=True)
