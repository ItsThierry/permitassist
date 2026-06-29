import importlib

import pytest


def architecture_api():
    candidates = ("scope_signals", "project_archetype", "api.scope_signals", "api.project_archetype")
    errors = []
    for name in candidates:
        try:
            mod = importlib.import_module(name)
            break
        except Exception as exc:  # pragma: no cover - diagnostic only
            errors.append(f"{name}: {exc}")
    else:
        pytest.fail(
            "Phase 0 RED: missing universal scope architecture module. "
            "Expected one of scope_signals.py/project_archetype.py with request text -> ScopeSignals[] -> "
            "ProjectArchetype set -> union family floor -> resolver API. " + " | ".join(errors)
        )
    required = ["detect_scope_signals", "derive_project_archetypes", "derive_family_floor", "resolve_primary_family"]
    missing = [name for name in required if not callable(getattr(mod, name, None))]
    if missing:
        pytest.fail(f"Phase 0 RED: {mod.__name__} missing required architecture functions: {missing}")
    return mod


def as_set(value):
    if value is None:
        return set()
    if isinstance(value, dict):
        return set(value.keys())
    return set(value)


def signal_attr(signal, name, default=None):
    if isinstance(signal, dict):
        return signal.get(name, default)
    return getattr(signal, name, default)



def test_powered_hvac_equipment_signal_carries_mechanical_and_electrical_implications():
    api = architecture_api()
    signals = api.detect_scope_signals("install whole-house heat pump with outdoor condenser and condensate pump")
    ids = {signal_attr(s, "signal_id", "") for s in signals}
    implications = set().union(*(as_set(signal_attr(s, "trade_implications", ())) for s in signals))
    assert "powered_hvac_equipment" in ids
    assert {"mechanical", "electrical"}.issubset(implications)
    electrical_conditions = [signal_attr(s, "trigger_condition", {}) for s in signals if "electrical" in as_set(signal_attr(s, "trade_implications", ()))]
    assert electrical_conditions, "Electrical VERIFY/CONDITIONAL implication must carry a trigger condition."


def test_plumbing_relocation_signal_blocks_false_not_required():
    api = architecture_api()
    signals = api.detect_scope_signals("replace bathtub with walk-in shower and relocate drain six inches")
    implications = set().union(*(as_set(signal_attr(s, "trade_implications", ())) for s in signals))
    assert "plumbing" in implications
    assert not any(signal_attr(s, "signal_id", "") == "de_minimis_fixture_swap" for s in signals)
