from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from api.lookup_execution_ledger import IdempotencyConflictError, LookupExecutionLedger


def _ledger(tmp_path, *, ttl: float = 60.0) -> LookupExecutionLedger:
    return LookupExecutionLedger(str(tmp_path / "lookup-ledger.sqlite3"), claim_ttl_seconds=int(ttl))


def test_claim_complete_replay_is_immutable_and_digest_stable(tmp_path):
    ledger = _ledger(tmp_path)
    payload = {"job_type": "replace sink", "city": "Austin", "state": "TX"}
    fp = ledger.request_fingerprint(payload)

    first = ledger.claim("key-1", fp, owner_scope="anon:a", request_id="req-1")
    assert first.action == "execute"

    response = {"permit_decision": "NEEDS_INPUT", "permit_required": None}
    digest = ledger.complete(
        "key-1", fp, response, owner_scope="anon:a", request_id="req-1",
        execution_token=first.execution_token or "",
    )
    replay = ledger.claim("key-1", fp, owner_scope="anon:a", request_id="req-2")

    assert replay.action == "replay"
    assert replay.response == response
    assert replay.response_digest == digest

    # A duplicate completion is harmless and cannot replace the stored body.
    assert ledger.complete(
        "key-1", fp, {"permit_decision": "REQUIRED"},
        owner_scope="anon:a", request_id="req-2", execution_token="not-used-after-completion",
    ) == digest
    assert ledger.claim("key-1", fp, owner_scope="anon:a", request_id="req-3").response == response


def test_same_owner_key_different_body_conflicts_and_owner_scopes_are_isolated(tmp_path):
    ledger = _ledger(tmp_path)
    fp_a = ledger.request_fingerprint({"job_type": "sink"})
    fp_b = ledger.request_fingerprint({"job_type": "roof"})
    assert ledger.claim("same-key", fp_a, owner_scope="anon:a", request_id="a").action == "execute"

    with pytest.raises(IdempotencyConflictError):
        ledger.claim("same-key", fp_b, owner_scope="anon:a", request_id="b")
    assert ledger.claim("same-key", fp_a, owner_scope="anon:b", request_id="c").action == "execute"


def test_processing_claim_is_single_owner_under_concurrency(tmp_path):
    ledger = _ledger(tmp_path)
    fp = ledger.request_fingerprint({"job_type": "commercial TI", "city": "Oshkosh", "state": "WI"})
    barrier = threading.Barrier(16)

    def claim(index: int) -> str:
        barrier.wait()
        return ledger.claim(
            "concurrent-key",
            fp,
            owner_scope="anon:shared",
            request_id=f"req-{index}",
        ).action

    with ThreadPoolExecutor(max_workers=16) as pool:
        actions = list(pool.map(claim, range(16)))

    assert actions.count("execute") == 1
    assert actions.count("in_progress") == 15


def test_failed_and_stale_claims_are_recoverable_without_duplicate_completion(tmp_path):
    ledger = _ledger(tmp_path, ttl=1)
    fp = ledger.request_fingerprint({"job_type": "bath remodel"})

    failed = ledger.claim("failed-key", fp, owner_scope="anon:a", request_id="req-1")
    assert failed.action == "execute"
    ledger.fail(
        "failed-key", fp, owner_scope="anon:a", request_id="req-1",
        execution_token=failed.execution_token or "",
    )
    assert ledger.claim("failed-key", fp, owner_scope="anon:a", request_id="req-2").action == "execute"

    assert ledger.claim(
        "stale-key", fp, owner_scope="anon:a", request_id="req-3", now=100,
    ).action == "execute"
    unresolved = ledger.claim(
        "stale-key", fp, owner_scope="anon:a", request_id="req-4", now=102,
    )
    assert unresolved.action == "indeterminate"
    assert unresolved.request_id == "req-3"


def test_per_attempt_fencing_rejects_old_worker_even_when_request_id_is_reused(tmp_path):
    ledger = _ledger(tmp_path)
    fp = ledger.request_fingerprint({"job_type": "panel"})
    first = ledger.claim("fenced", fp, owner_scope="anon:a", request_id="same-request")
    ledger.fail(
        "fenced", fp, owner_scope="anon:a", request_id="same-request",
        execution_token=first.execution_token or "",
    )
    replacement = ledger.claim("fenced", fp, owner_scope="anon:a", request_id="same-request")
    assert replacement.execution_token != first.execution_token
    with pytest.raises(IdempotencyConflictError):
        ledger.complete(
            "fenced", fp, {"old": True}, owner_scope="anon:a",
            request_id="same-request", execution_token=first.execution_token or "",
        )
    ledger.complete(
        "fenced", fp, {"new": True}, owner_scope="anon:a",
        request_id="same-request", execution_token=replacement.execution_token or "",
    )
    replay = ledger.replay("fenced", owner_scope="anon:a")
    assert replay is not None
    assert replay.response == {"new": True}


def test_request_fingerprint_is_canonical_for_key_order(tmp_path):
    ledger = _ledger(tmp_path)
    assert ledger.request_fingerprint({"b": 2, "a": {"y": 2, "x": 1}}) == ledger.request_fingerprint(
        {"a": {"x": 1, "y": 2}, "b": 2}
    )


def test_free_quota_charge_is_once_per_owner_and_idempotency_key(tmp_path, monkeypatch):
    from api import server

    monkeypatch.setattr(server, "FREE_LOOKUP_DB", str(tmp_path / "free-usage.db"))
    server.init_free_lookup_db()
    first = server.record_lookup_usage_once(
        "203.0.113.9", "fp-a", owner_scope="anon:owner-a", idempotency_key="key-a"
    )
    duplicate = server.record_lookup_usage_once(
        "203.0.113.9", "fp-a", owner_scope="anon:owner-a", idempotency_key="key-a"
    )
    second_key = server.record_lookup_usage_once(
        "203.0.113.9", "fp-a", owner_scope="anon:owner-a", idempotency_key="key-b"
    )
    assert first == (1, 1)
    assert duplicate == (1, 1)
    assert second_key == (2, 2)
