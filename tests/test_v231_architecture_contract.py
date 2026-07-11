import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_ENGINE = ROOT / "api" / "research_engine.py"
RESOLVER = ROOT / "api" / "v231_decision_cells.py"


def test_v231_research_engine_delegates_to_single_resolver_source_of_truth():
    assert RESOLVER.exists(), "api/v231_decision_cells.py must be the resolver source of truth"
    src = RESEARCH_ENGINE.read_text()
    assert "from v231_decision_cells import" in src or "from .v231_decision_cells import" in src
    assert "def _v231_project_slug_for_job" not in src
    assert "def _lookup_v231_decision_cell" not in src
    assert "def _load_v231_decision_index" not in src
    assert "def apply_v231_decision_cell_overlay" not in src
    assert "apply_v231_decision_cell_overlay(" not in src
    assert "decision_cell_overlay" not in src
    assert "permitassist_decision_cell_index_v231.json" not in src


def test_v231_reconciliation_is_on_cached_and_fresh_paths_before_cache_save():
    src = RESEARCH_ENGINE.read_text()
    assert "reconcile_v231_result" in src
    research_src = src[src.index("def research_permit"): src.index("# ─── Display Helper")]

    cached_block = research_src[research_src.index("if cached:"): research_src.index("# ── Check auto-verified data first")]
    cached_return = cached_block.index("return cached")
    cached_reconcile = cached_block.index("reconcile_v231_result(cached")
    cached_rulebook = cached_block.index("apply_rulebook_depth(cached")
    cached_sanitize = cached_block.index("sanitize_non_food_office_breakroom_text(cached, job_type)")
    assert cached_rulebook < cached_sanitize < cached_reconcile < cached_return

    fresh_reconcile = research_src.rindex("reconcile_v231_result(result")
    fresh_save = research_src.rindex("save_cache(key, job_type, job_category, city, state, zip_code, result)")
    fresh_sanitize = research_src.rindex("sanitize_non_food_office_breakroom_text(result, job_type)")
    assert fresh_sanitize < fresh_reconcile < fresh_save


def test_v231_early_context_is_optional_grounding_not_short_circuit():
    src = RESEARCH_ENGINE.read_text()
    research_src = src[src.index("def research_permit"): src.index("# ─── Display Helper")]
    context_call_pos = research_src.index("v231_prompt_context = build_v231_prompt_context(v231_resolution)")
    context_append_pos = research_src.index("kb_context_parts.append(v231_prompt_context)")
    model_call_pos = research_src.index("def _call_luna")
    fresh_reconcile = research_src.index("reconcile_v231_result(result")
    assert context_call_pos < context_append_pos < model_call_pos < fresh_reconcile
    assert "return resolve_v231_cell" not in src


def test_v231_reconcile_not_bypassed_by_auto_verified_or_refresh_paths():
    src = RESEARCH_ENGINE.read_text()
    tree = ast.parse(src)
    research = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "research_permit")
    top_level_returns: list[int] = []

    class TopLevelReturnVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):  # skip nested helpers inside research_permit
            if node is research:
                self.generic_visit(node)

        def visit_Return(self, node):
            top_level_returns.append(node.lineno)

    TopLevelReturnVisitor().visit(research)
    assert len(top_level_returns) == 2

    lines = src.splitlines()
    assert research.end_lineno is not None
    research_lines = list(range(research.lineno, research.end_lineno + 1))
    cached_return_line = next(i for i in research_lines if "return cached" in lines[i - 1])
    final_return_line = next(i for i in research_lines if lines[i - 1].strip() == "return result")
    fresh_reconcile_line = next(i for i in research_lines if "reconcile_v231_result(result" in lines[i - 1])
    auto_verified_marker_line = next(i for i in research_lines if "Check auto-verified data first" in lines[i - 1])

    assert sorted(top_level_returns) == [cached_return_line, final_return_line]
    assert auto_verified_marker_line < fresh_reconcile_line < final_return_line
    assert "fresh = research_permit(job_type, city, state, zip_code, use_cache=False" in src
