#!/usr/bin/env python3
"""Generate deterministic Permit Rule Engine Part 4 customer-boundary evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
os.environ.setdefault("FREE_LOOKUP_DB", "/tmp/permitassist-part4-evidence-free.db")

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "api"
for path in (str(REPO_ROOT), str(API_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from api import permit_rule_engine as pre  # noqa: E402
from api import server  # noqa: E402
from api.v24_decision_cells import V24ResolutionStatus, resolve_v24_cell  # noqa: E402

SCHEMA_VERSION = "permitassist.rule-engine-part4-evidence.v1"
EVIDENCE_UTC_NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
POISON_BINARY = "POISON_LEGACY_BINARY"
POISON_MARKER = "POISON_INTERNAL_SECRET"
CANARIES = (
    ("Anchorage", "AK", "commercial tenant improvement", "commercial"),
    ("Albertville", "AL", "residential remodel", "residential"),
    ("Buckeye", "AZ", "residential reroof", "residential"),
)
TAMPER_CASES = (
    "sealed_projection_payload_json",
    "sealed_projection_payload_sha256",
    "envelope_sha256",
    "cache_schema_version",
)
REPORT_DATA_RE = re.compile(
    r'<script\s+id=["\']report-data["\']\s+type=["\']application/json["\']>(.*?)</script>',
    re.S,
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def build_wrapped(
    *,
    city: str,
    state: str,
    job_type: str,
    job_category: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    resolution = resolve_v24_cell(city, state, job_type, job_category, force=True)
    if resolution.status is not V24ResolutionStatus.EXACT_CELL_PUBLISHABLE:
        raise AssertionError((city, state, job_type, resolution.status))
    jurisdiction_id = str((resolution.cell or {}).get("jurisdiction_id") or "")
    if not jurisdiction_id:
        raise AssertionError("missing jurisdiction id")
    os.environ[pre.CORE_SETTING] = "active"
    os.environ[pre.CORE_ALLOWLIST_SETTING] = jurisdiction_id
    envelope = pre.build_core_decision_envelope(
        resolution,
        job_type=job_type,
        city=city,
        state=state,
        job_category=job_category,
    )
    legacy = {
        "permit_decision": "NOT_REQUIRED",
        "permit_required": False,
        "permit_verdict": "NO",
        "permit_name": POISON_BINARY,
        "summary": POISON_MARKER,
        "_internal_secret": POISON_MARKER,
    }
    wrapped = pre.attach_core_decision_envelope(legacy, envelope)
    sealed = json.loads(envelope.sealed_projection.payload_json)
    return wrapped, sealed, jurisdiction_id


def tamper(wrapped: dict[str, Any], case: str) -> dict[str, Any]:
    broken = copy.deepcopy(wrapped)
    if case == "sealed_projection_payload_json":
        broken["_permit_rule_engine_core"]["sealed_projection"]["payload_json"] = "{}"
    elif case == "sealed_projection_payload_sha256":
        broken["_permit_rule_engine_core"]["sealed_projection"]["payload_sha256"] = "0" * 64
    elif case == "envelope_sha256":
        broken["_permit_rule_engine_core"]["envelope_sha256"] = "0" * 64
    elif case == "cache_schema_version":
        broken["_permit_rule_engine_cache_schema_version"] = "stale-schema"
    else:
        raise AssertionError(case)
    return broken


def assert_no_poison(value: Any) -> None:
    text = canonical_json(value)
    if POISON_BINARY in text or POISON_MARKER in text:
        raise AssertionError("legacy poison crossed customer boundary")


def extract_report_payload(report_html: str) -> dict[str, Any]:
    match = REPORT_DATA_RE.search(report_html)
    if not match:
        raise AssertionError("report-data payload missing")
    return json.loads(match.group(1))


def generate(output_dir: Path, source_commit: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    original_core = os.environ.get(pre.CORE_SETTING)
    original_allowlist = os.environ.get(pre.CORE_ALLOWLIST_SETTING)
    original_utc_now = server.utc_now
    valid_rows: list[dict[str, Any]] = []
    tamper_rows: list[dict[str, Any]] = []
    try:
        # Renderer fallbacks stamp citation dates with server.utc_now(). Freeze
        # the evidence clock so artifact hashes remain reproducible across UTC
        # date boundaries without changing production rendering behavior.
        server.utc_now = lambda: EVIDENCE_UTC_NOW
        os.environ.pop(pre.CORE_SETTING, None)
        os.environ.pop(pre.CORE_ALLOWLIST_SETTING, None)
        legacy = {"permit_decision": "REQUIRED", "nested": {"order": [3, 2, 1]}}
        before = canonical_json(legacy).encode("utf-8")
        disabled = pre.maybe_attach_core_decision_envelope(
            legacy,
            job_type="residential reroof",
            city="Buckeye",
            state="AZ",
            job_category="residential",
        )
        flag_off = {
            "same_object": disabled is legacy,
            "same_bytes": before == canonical_json(disabled).encode("utf-8"),
            "core_mode": pre.get_rule_engine_core_mode(),
            "passed": disabled is legacy and before == canonical_json(disabled).encode("utf-8"),
        }

        with tempfile.TemporaryDirectory(prefix="permitassist-part4-evidence-") as temp_dir:
            server.CACHE_DB = str(Path(temp_dir) / "permitassist.db")
            server.DATA_DIR = temp_dir
            server.init_db()
            for city, state, job_type, job_category in CANARIES:
                wrapped, sealed, jurisdiction_id = build_wrapped(
                    city=city,
                    state=state,
                    job_type=job_type,
                    job_category=job_category,
                )
                current_request = server._mark_server_owned_result(wrapped)
                projected = pre.project_core_customer_boundary(
                    wrapped,
                    job_type=job_type,
                    city=city,
                    state=state,
                    job_category=job_category,
                )
                api_view = server.build_customer_permit_view_model(
                    current_request,
                    job_type,
                    city,
                    state,
                    job_category,
                )
                finalized = server.finalize_permit_lookup_result(
                    current_request,
                    job_type,
                    city,
                    state,
                    job_category=job_category,
                )
                checklist = server.get_or_create_checklist(
                    current_request, job_type, city, state
                )
                report_html = server.render_white_label_report_html(
                    {
                        "result": current_request,
                        "job_type": job_type,
                        "job_category": job_category,
                        "city": city,
                        "state": state,
                    }
                )
                slug = server.create_share(
                    job_type, city, state, current_request
                )
                share = server.get_share(slug)
                if share is None:
                    raise AssertionError("sealed share retrieval failed")
                share_html = server.render_share_page(share)
                report_payload = extract_report_payload(share_html)
                embedded = report_payload["share"]["data"]
                expected_embedded = server.project_customer_response_egress(sealed)
                families = [str(row.get("family") or "") for row in sealed.get("family_decisions", [])]
                checklist_families = [str(item.get("category") or "") for item in checklist.get("items", [])]
                permit_labels = [str(row.get("permit_type") or "") for row in sealed.get("permits_required", [])]
                row = {
                    "city": city,
                    "state": state,
                    "job_type": job_type,
                    "jurisdiction_id": jurisdiction_id,
                    "decision": sealed.get("permit_decision"),
                    "family_count": len(families),
                    "families": families,
                    "sealed_projection_sha256": stable_hash(sealed),
                    "report_html_sha256": hashlib.sha256(report_html.encode("utf-8")).hexdigest(),
                    "projected_equals_sealed": projected == sealed,
                    "api_equals_sealed": api_view == sealed,
                    "finalized_equals_sealed": finalized == sealed,
                    "stored_share_equals_default_deny_projection": share.get("data") == expected_embedded,
                    "embedded_share_equals_default_deny_projection": embedded == expected_embedded,
                    "checklist_families_equal_projection": checklist_families == families,
                    "report_contains_every_permit_lane": all(label in report_html for label in permit_labels),
                    "share_template_renders_family_decision_matrix": all(
                        marker in share_html
                        for marker in (
                            "safeArray(d.family_decisions)",
                            "Permit decision matrix",
                            "decision-matrix",
                            "decision-row",
                        )
                    ),
                    "share_template_rejects_empty_application_routes": all(
                        marker in share_html
                        for marker in (
                            "const raw = String(url || '').trim()",
                            "if (!raw) return fallback",
                        )
                    ),
                    "share_template_uses_actionable_verification_tasks": all(
                        marker in share_html
                        for marker in (
                            "safeArray(d.verification_tasks)",
                            "task.action",
                        )
                    ),
                }
                row["passed"] = all(
                    value is True
                    for key, value in row.items()
                    if key.endswith("sealed")
                    or key.endswith("projection")
                    or key.startswith("report_contains")
                    or key.startswith("share_template_")
                ) and row["checklist_families_equal_projection"]
                assert_no_poison(
                    {
                        "projected": projected,
                        "api": api_view,
                        "finalized": finalized,
                        "checklist": checklist,
                        "report": report_html,
                        "share": share_html,
                    }
                )
                if not row["passed"]:
                    raise AssertionError(row)
                valid_rows.append(row)

            wrapped, _sealed, _jurisdiction_id = build_wrapped(
                city="Anchorage",
                state="AK",
                job_type="commercial tenant improvement",
                job_category="commercial",
            )
            for case in TAMPER_CASES:
                broken = tamper(wrapped, case)
                current_request = server._mark_server_owned_result(broken)
                projected = pre.project_core_customer_boundary(
                    broken,
                    job_type="commercial tenant improvement",
                    city="Anchorage",
                    state="AK",
                    job_category="commercial",
                )
                api_view = server.build_customer_permit_view_model(
                    current_request,
                    "commercial tenant improvement",
                    "Anchorage",
                    "AK",
                    "commercial",
                )
                checklist = server.get_or_create_checklist(
                    current_request,
                    "commercial tenant improvement",
                    "Anchorage",
                    "AK",
                )
                report_html = server.render_white_label_report_html(
                    {
                        "result": current_request,
                        "job_type": "commercial tenant improvement",
                        "job_category": "commercial",
                        "city": "Anchorage",
                        "state": "AK",
                    }
                )
                families = [str(row.get("family") or "") for row in (projected or {}).get("family_decisions", [])]
                verdicts = sorted({str(row.get("verdict") or "") for row in (projected or {}).get("family_decisions", [])})
                row = {
                    "tamper_case": case,
                    "permit_decision": (projected or {}).get("permit_decision"),
                    "permit_verdict": (projected or {}).get("permit_verdict"),
                    "family_count": len(families),
                    "families": families,
                    "family_verdicts": verdicts,
                    "api_equals_core_projection": api_view == projected,
                    "checklist_family_count": len(checklist.get("items", [])),
                    "report_html_sha256": hashlib.sha256(report_html.encode("utf-8")).hexdigest(),
                }
                row["passed"] = bool(
                    projected
                    and row["permit_decision"] == "NEEDS_INPUT"
                    and row["permit_verdict"] == "NEEDS_INPUT"
                    and row["family_count"] == 10
                    and row["family_verdicts"] == ["NEEDS_INPUT"]
                    and row["api_equals_core_projection"]
                    and row["checklist_family_count"] == 10
                )
                assert_no_poison(
                    {
                        "projected": projected,
                        "api": api_view,
                        "checklist": checklist,
                        "report": report_html,
                    }
                )
                if not row["passed"]:
                    raise AssertionError(row)
                tamper_rows.append(row)

            # The v2 shared-result wrapper must reject a storage mutation.
            wrapped, _sealed, _jurisdiction_id = build_wrapped(
                city="Buckeye",
                state="AZ",
                job_type="residential reroof",
                job_category="residential",
            )
            current_request = server._mark_server_owned_result(wrapped)
            slug = server.create_share(
                "residential reroof", "Buckeye", "AZ", current_request
            )
            with sqlite3.connect(server.CACHE_DB) as conn:
                raw = conn.execute("SELECT result_json FROM shared_results WHERE slug=?", [slug]).fetchone()[0]
                stored = json.loads(raw)
                wrapper_schema = stored.get("schema_version")
                stored["payload_json"] = "{}"
                conn.execute("UPDATE shared_results SET result_json=? WHERE slug=?", [json.dumps(stored), slug])
                conn.commit()
            shared_storage_tamper = {
                "wrapper_schema": wrapper_schema,
                "tampered_row_rejected": server.get_share(slug) is None,
            }
            shared_storage_tamper["passed"] = bool(
                wrapper_schema == server.SHARED_RESULT_SCHEMA_VERSION
                and shared_storage_tamper["tampered_row_rejected"]
            )

        summary = {
            "schema_version": SCHEMA_VERSION,
            "source_commit": source_commit,
            "evidence_clock_utc": EVIDENCE_UTC_NOW.isoformat().replace("+00:00", "Z"),
            "valid_canary_count": len(valid_rows),
            "tamper_case_count": len(tamper_rows),
            "w4_family_count": next(row["family_count"] for row in valid_rows if row["job_type"] == "commercial tenant improvement"),
            "flag_off_passed": flag_off["passed"],
            "valid_canaries_passed": all(row["passed"] for row in valid_rows),
            "tamper_matrix_passed": all(row["passed"] for row in tamper_rows),
            "shared_storage_tamper_passed": shared_storage_tamper["passed"],
        }
        summary["passed"] = all(
            summary[key]
            for key in (
                "flag_off_passed",
                "valid_canaries_passed",
                "tamper_matrix_passed",
                "shared_storage_tamper_passed",
            )
        ) and summary["w4_family_count"] == 10

        write_json(output_dir / "flag_off_parity.json", flag_off)
        write_json(output_dir / "customer_boundary_proof.json", {"schema_version": SCHEMA_VERSION, "rows": valid_rows})
        write_json(output_dir / "security_tamper_matrix.json", {"schema_version": SCHEMA_VERSION, "rows": tamper_rows})
        write_json(output_dir / "shared_storage_tamper.json", shared_storage_tamper)
        write_json(output_dir / "summary.json", summary)
        files: dict[str, dict[str, Any]] = {}
        for path in sorted(output_dir.iterdir()):
            if path.is_file() and path.name != "manifest.json":
                data = path.read_bytes()
                files[path.name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "source_commit": source_commit,
            "files": files,
            "artifact_set_sha256": stable_hash(files),
            "manifest_self_hash_excluded": True,
        }
        write_json(output_dir / "manifest.json", manifest)
        return summary
    finally:
        server.utc_now = original_utc_now
        if original_core is None:
            os.environ.pop(pre.CORE_SETTING, None)
        else:
            os.environ[pre.CORE_SETTING] = original_core
        if original_allowlist is None:
            os.environ.pop(pre.CORE_ALLOWLIST_SETTING, None)
        else:
            os.environ[pre.CORE_ALLOWLIST_SETTING] = original_allowlist


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    summary = generate(args.output_dir.resolve(), args.source_commit)
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
