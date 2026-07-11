from __future__ import annotations

import ast
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


API_DIR = Path(__file__).resolve().parents[1] / "api"
sys.path.insert(0, str(API_DIR))

from model_config import (  # noqa: E402
    PERMITASSIST_AI_CACHE_NAMESPACE,
    PERMITASSIST_AI_MODEL,
    create_permitassist_chat_completion,
    require_permitassist_model_override,
)


RUNTIME_AI_FILES = (API_DIR / "research_engine.py", API_DIR / "server.py")


class _FakeCompletions:
    def __init__(self, observed_model: str):
        self.observed_model = observed_model
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(model=self.observed_model)


class _FakeClient:
    def __init__(self, observed_model: str):
        self.completions = _FakeCompletions(observed_model)
        self.chat = SimpleNamespace(completions=self.completions)


def test_single_model_constant_and_override_policy():
    assert PERMITASSIST_AI_MODEL == "gpt-5.6-luna"
    assert PERMITASSIST_AI_CACHE_NAMESPACE == "v5:gpt-5.6-luna"
    assert require_permitassist_model_override(None) == PERMITASSIST_AI_MODEL
    assert require_permitassist_model_override("luna") == PERMITASSIST_AI_MODEL
    assert require_permitassist_model_override("gpt-5.6-luna") == PERMITASSIST_AI_MODEL
    with pytest.raises(ValueError):
        require_permitassist_model_override("gpt-5.4-mini")
    with pytest.raises(ValueError):
        require_permitassist_model_override("gemini-3-flash-preview")


def test_chat_completion_wrapper_pins_and_verifies_luna():
    client = _FakeClient("gpt-5.6-luna")
    create_permitassist_chat_completion(client, messages=[])
    assert client.completions.calls == [{"model": "gpt-5.6-luna", "messages": []}]

    snapshot_client = _FakeClient("gpt-5.6-luna-2026-07-09")
    create_permitassist_chat_completion(snapshot_client, messages=[])

    wrong_client = _FakeClient("gpt-5.4-mini")
    with pytest.raises(RuntimeError, match="unexpected PermitAssist model"):
        create_permitassist_chat_completion(wrong_client, messages=[])

    with pytest.raises(TypeError, match="fixed"):
        create_permitassist_chat_completion(client, model="gpt-5.4-mini", messages=[])
    with pytest.raises(TypeError, match="default temperature"):
        create_permitassist_chat_completion(client, temperature=0.1, messages=[])


def test_runtime_ai_files_have_no_mixed_provider_or_model_routes():
    combined = "\n".join(path.read_text(encoding="utf-8") for path in RUNTIME_AI_FILES)
    for forbidden in (
        "google.generativeai",
        "gemini-2.5-flash",
        "gemini-3-flash-preview",
        "gpt-5.4-mini",
        "_call_gemini",
        "_gemini_primary_model",
        "_openai_fallback_model",
    ):
        assert forbidden not in combined
    assert "PERMITASSIST_AI_CACHE_NAMESPACE" in RUNTIME_AI_FILES[0].read_text(encoding="utf-8")


def test_runtime_openai_calls_go_through_single_model_wrapper_without_temperature():
    direct_create_calls = []
    wrapper_calls = 0
    for path in RUNTIME_AI_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "create_permitassist_chat_completion":
                wrapper_calls += 1
                assert all(keyword.arg != "temperature" for keyword in node.keywords)
            if isinstance(func, ast.Attribute) and func.attr == "create":
                owner = func.value
                if isinstance(owner, ast.Attribute) and owner.attr == "completions":
                    direct_create_calls.append((path.name, node.lineno))

    assert wrapper_calls >= 6
    assert direct_create_calls == []
