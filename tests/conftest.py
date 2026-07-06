from __future__ import annotations

import os
import socket
from datetime import datetime, timezone

import pytest


@pytest.fixture(scope="session", autouse=True)
def _permitassist_customer_gates_on_by_default() -> None:
    os.environ.setdefault("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD", "1")
    os.environ.setdefault("PERMITASSIST_STRICT_CUSTOMER_INVARIANTS", "1")


@pytest.fixture
def prod_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD", raising=False)
    monkeypatch.delenv("PERMITASSIST_STRICT_CUSTOMER_INVARIANTS", raising=False)
    return monkeypatch


@pytest.fixture
def degraded_engine():
    """Return a helper that tests can monkeypatch into code paths to force degraded fallback."""
    def _raise(*args, **kwargs):
        raise TimeoutError("forced degraded_engine fixture")
    return _raise


@pytest.fixture
def offline_serper():
    """Return a canned official/local Serper-like result list; performs no network calls."""
    return [
        {
            "title": "Phoenix Planning and Development",
            "link": "https://www.phoenix.gov/pdd",
            "url": "https://www.phoenix.gov/pdd",
            "snippet": "Planning and Development permit information for Phoenix.",
        }
    ]


@pytest.fixture
def frozen_time():
    """Return a shared deterministic UTC time for replay/snapshot tests."""
    return datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch):
    """Block accidental socket creation in deterministic unit tests."""
    def _blocked(*args, **kwargs):
        raise AssertionError("network calls are blocked in this unit test")
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "socket", _blocked)
    return _blocked
