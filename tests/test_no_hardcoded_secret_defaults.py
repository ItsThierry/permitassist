import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_ENV_NAMES = {
    "ACCELA_APP_ID",
    "ACCELA_APP_SECRET",
    "ACCELA_CLIENT_ID",
    "ACCELA_CLIENT_SECRET",
    "FB_WEBHOOK_VERIFY_TOKEN",
    "FB_PAGE_TOKEN",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "TAVILY_API_KEY",
    "SERPER_API_KEY",
    "FIRECRAWL_API_KEY",
    "BRAVE_SEARCH_API_KEY",
    "RAILWAY_API_TOKEN",
    "POSTHOG_API_KEY",
    "SENTRY_DSN",
}


def _literal_string(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _env_name_from_call(call):
    if not isinstance(call, ast.Call):
        return None
    if isinstance(call.func, ast.Attribute):
        if call.func.attr == "getenv":
            return _literal_string(call.args[0]) if call.args else None
        if call.func.attr == "get" and isinstance(call.func.value, ast.Attribute):
            value = call.func.value
            if value.attr == "environ" and isinstance(value.value, ast.Name) and value.value.id == "os":
                return _literal_string(call.args[0]) if call.args else None
    return None


def _default_from_call(call):
    if len(call.args) >= 2:
        return _literal_string(call.args[1])
    for kw in call.keywords:
        if kw.arg == "default":
            return _literal_string(kw.value)
    return None


def test_sensitive_environment_variables_do_not_have_hardcoded_string_defaults():
    offenders = []
    for path in sorted((REPO_ROOT / "api").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            env_name = _env_name_from_call(node)
            default = _default_from_call(node)
            if env_name in SENSITIVE_ENV_NAMES and default:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} {env_name} default=[REDACTED]")

    assert not offenders, "Sensitive env vars must be env-only, no hardcoded defaults:\n" + "\n".join(offenders)
