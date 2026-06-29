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


import pytest


@pytest.mark.parametrize("text,expected,forbidden", [
    ("replace rooftop evaporative cooler with new ducted evaporative cooler, same location", "mechanical", {"roofing", "building"}),
    ("replace private sewer line from foundation to property line using trenchless liner", "plumbing", {"foundation", "building"}),
    ("replace sump pump and discharge line in basement, no foundation work", "plumbing", {"building", "foundation"}),
    ("minor grading and drainage improvements for restaurant outdoor patio expansion", "grading", {"health", "fire", "co"}),
])
def test_specificity_ranked_primary_resolver_prefers_work_signal_over_context_noun(text, expected, forbidden):
    api = architecture_api()
    signals = api.detect_scope_signals(text)
    archetypes = api.derive_project_archetypes(signals)
    floor = api.derive_family_floor(signals, archetypes)
    primary = api.resolve_primary_family(signals, archetypes, floor, request_text=text)
    assert primary == expected
    assert primary not in forbidden
