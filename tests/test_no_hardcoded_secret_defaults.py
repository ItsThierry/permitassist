import ast
import re
import subprocess
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

HIGH_CONFIDENCE_SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "stripe_secret_key": re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    "stripe_webhook_secret": re.compile(r"\bwhsec_[A-Za-z0-9]{16,}\b"),
    "openai_key": re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}\b|\bsk-[A-Za-z0-9]{32,}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "facebook_page_token": re.compile(r"\bEA[A-Za-z0-9]{80,}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"),
    "db_url_with_password": re.compile(
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:@/]+:[^\s:@/]+@[^\s]+",
        re.IGNORECASE,
    ),
}

SENSITIVE_ASSIGNMENT_NAME = re.compile(
    r"(^|_)(SECRET|TOKEN|API_KEY|PASSWORD|PRIVATE_KEY|CLIENT_SECRET|DSN)(_|$)",
    re.IGNORECASE,
)
PLACEHOLDER_VALUE = re.compile(
    r"^(|test|fake|dummy|example|sample|placeholder|benchmark.*|changeme|change-me|not[_-]?real|your[_-].*|configured-test-admin-token|anything|bad)$",
    re.IGNORECASE,
)
SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".html",
    ".css",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".md",
}
IGNORED_PATH_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
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


def _tracked_source_files():
    files = subprocess.check_output(["git", "ls-files"], cwd=REPO_ROOT, text=True).splitlines()
    for relative in files:
        path = REPO_ROOT / relative
        if any(part in IGNORED_PATH_PARTS for part in path.parts):
            continue
        if path.suffix.lower() in SOURCE_EXTENSIONS or ".env" in path.name:
            yield path


def _is_placeholder_secret(value):
    compact = value.strip()
    lower = compact.lower()
    return (
        PLACEHOLDER_VALUE.match(compact) is not None
        or "not_real" in lower
        or "fake" in lower
        or "example" in lower
    )


def test_sensitive_environment_variables_do_not_have_hardcoded_string_defaults():
    offenders = []
    for path in sorted((REPO_ROOT / "api").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            env_name = _env_name_from_call(node)
            default = _default_from_call(node)
            if env_name in SENSITIVE_ENV_NAMES and default:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} {env_name} default=[REDACTED]")

    assert not offenders, "Sensitive env vars must be env-only, no hardcoded defaults:\n" + "\n".join(offenders)


def test_tracked_source_does_not_contain_high_confidence_secret_literals():
    offenders = []
    for path in sorted(_tracked_source_files()):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for pattern_name, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS.items():
                for match in pattern.finditer(line):
                    if _is_placeholder_secret(match.group(0)):
                        continue
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number} {pattern_name} [REDACTED]")

    assert not offenders, "Tracked source contains hardcoded high-confidence secrets:\n" + "\n".join(offenders)


def test_python_sensitive_names_are_not_assigned_real_literal_values():
    offenders = []
    for path in sorted(_tracked_source_files()):
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            targets = []
            value = None
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value

            literal = _literal_string(value) if value is not None else None
            if literal is None or len(literal.strip()) < 8 or _is_placeholder_secret(literal):
                continue

            for target in targets:
                if isinstance(target, ast.Name):
                    target_name = target.id
                elif isinstance(target, ast.Attribute):
                    target_name = target.attr
                else:
                    continue

                normalized_target = target_name.lower()
                if normalized_target.endswith("_env") or normalized_target.endswith("_url"):
                    continue
                if SENSITIVE_ASSIGNMENT_NAME.search(target_name):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} {target_name}=[REDACTED]")

    assert not offenders, "Sensitive Python names must not be assigned literal secret values:\n" + "\n".join(offenders)
