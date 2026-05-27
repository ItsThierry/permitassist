import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_INDEX = ROOT / "frontend" / "index.html"


def _extract_js_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    in_string = None
    escape = False
    in_line_comment = False
    in_block_comment = False
    for idx in range(brace, len(source)):
        ch = source[idx]
        nxt = source[idx + 1] if idx + 1 < len(source) else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
            continue
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_string:
                in_string = None
            continue
        if ch in {"'", '"', "`"}:
            in_string = ch
            continue
        if ch == "/" and nxt == "/":
            in_line_comment = True
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    raise AssertionError(f"Could not extract {name}")


def _run_verdict_state(fixture: dict) -> dict:
    html = FRONTEND_INDEX.read_text(encoding="utf-8")
    helpers = "\n".join(
        _extract_js_function(html, name)
        for name in ["hasPositiveNoPermitEvidence", "verdictState", "customerFacingDecisionLabel"]
    )
    script = f"""
{helpers}
const fixture = {json.dumps(fixture)};
const verdict = verdictState(fixture);
const customerSummary = fixture.customer_result_summary || {{}};
const rawCustomerFirstScreen = (fixture.customer_first_screen_summary && typeof fixture.customer_first_screen_summary === 'object') ? fixture.customer_first_screen_summary : {{}};
const customerDecision = customerFacingDecisionLabel(rawCustomerFirstScreen.decision || customerSummary.permit_decision || fixture.permit_decision || fixture.permit_verdict, fixture, verdict);
console.log(JSON.stringify({{ verdict, customerDecision }}));
"""
    completed = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, check=True)
    return json.loads(completed.stdout)


def test_fail_closed_unknown_empty_permits_renders_check_required_not_not_required():
    dallas_style = {
        "permit_decision": "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE",
        "permit_verdict": "UNKNOWN",
        "permits_required": [],
        "job_summary": "PermitAssist could not verify a source-backed local permit decision for Dallas, TX.",
    }

    rendered = _run_verdict_state(dallas_style)

    assert rendered["verdict"] != "no"
    assert "NOT REQUIRED" not in rendered["customerDecision"].upper()
    assert rendered["customerDecision"].upper() in {"CHECK REQUIRED", "VERIFY REQUIRED"}


def test_mobile_first_summary_not_required_is_sanitized_without_positive_exemption_evidence():
    dallas_style_with_bad_fallback = {
        "permit_decision": "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE",
        "permit_verdict": "UNKNOWN",
        "permits_required": [],
        "customer_first_screen_summary": {"decision": "NOT REQUIRED"},
        "job_summary": "PermitAssist could not verify a source-backed local permit decision for Dallas, TX.",
    }

    rendered = _run_verdict_state(dallas_style_with_bad_fallback)

    assert rendered["verdict"] != "no"
    assert "NOT REQUIRED" not in rendered["customerDecision"].upper()
    assert rendered["customerDecision"].upper() in {"CHECK REQUIRED", "VERIFY REQUIRED"}


def test_unknown_no_evidence_empty_sources_never_renders_not_required():
    unsupported_empty_payload = {
        "permit_decision": "UNKNOWN",
        "permit_verdict": "NO_EVIDENCE",
        "permits_required": [],
        "sources": [],
    }

    rendered = _run_verdict_state(unsupported_empty_payload)

    assert rendered["verdict"] != "no"
    assert "NOT REQUIRED" not in rendered["customerDecision"].upper()
    assert rendered["customerDecision"].upper() in {"CHECK REQUIRED", "VERIFY REQUIRED"}


def test_positive_source_backed_no_permit_evidence_still_renders_not_required():
    legitimate_no_permit_payload = {
        "permit_decision": "NOT_REQUIRED",
        "permit_verdict": "NO",
        "permits_required": [
            {
                "permit_type": "Residential Cosmetic Interior Paint",
                "required": False,
                "exemption_reason": "No building permit required for cosmetic painting that does not alter electrical, plumbing, structural, or life-safety systems.",
                "source_url": "https://www.phoenix.gov/pdd/development/permits",
            }
        ],
        "code_citation": {
            "section": "City permit guidance",
            "text": "Official source says no permit required for ordinary cosmetic painting without regulated trade or structural work.",
        },
        "sources": [
            {
                "url": "https://www.phoenix.gov/pdd/development/permits",
                "title": "Phoenix Planning & Development Permits",
                "publisher": "City of Phoenix",
                "source_type": "official_local",
            }
        ],
        "customer_first_screen_summary": {"decision": "NOT_REQUIRED"},
    }

    rendered = _run_verdict_state(legitimate_no_permit_payload)

    assert rendered["verdict"] == "no"
    assert rendered["customerDecision"].upper() == "NOT REQUIRED"
