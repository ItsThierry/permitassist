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



def test_multi_scope_request_uses_archetype_set_and_union_floor_not_single_winner():
    api = architecture_api()
    text = "convert detached garage to ADU with kitchen, bathroom, mini-split, and new subpanel"
    signals = api.detect_scope_signals(text)
    archetypes = api.derive_project_archetypes(signals)
    floor = api.derive_family_floor(signals, archetypes)
    assert not isinstance(archetypes, str), "Archetypes must be a set/collection, not a single winner."
    assert {"NEW_DWELLING_UNIT", "HABITABLE_CONVERSION", "EQUIPMENT_SWAP_POWERED"}.issubset(set(archetypes))
    assert {"building", "electrical", "plumbing", "mechanical"}.issubset(as_set(floor))


def test_family_floor_is_visibility_floor_not_blanket_required():
    api = architecture_api()
    signals = api.detect_scope_signals("install mini split in garage using existing circuit if available")
    archetypes = api.derive_project_archetypes(signals)
    floor = api.derive_family_floor(signals, archetypes)
    electrical = floor.get("electrical") if isinstance(floor, dict) else None
    assert "electrical" in as_set(floor)
    assert electrical is None or str(electrical).upper() in {"VERIFY", "CONDITIONAL", "VERIFY_OR_REQUIRED", "REQUIRED"}
