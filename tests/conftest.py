from __future__ import annotations

import os

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
