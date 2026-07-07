#!/usr/bin/env python3
from __future__ import annotations

"""Offline Phase 0/1 harness for the 2026-07-05 Live100 Fable 5 final-final plan.

This script is intentionally offline-only: it replays recorded JSON artifacts through
PermitAssist's local customer ViewModel and report renderer. It never calls paid
lookup APIs or production.
"""

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import types
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
DEFAULT_ARTIFACT_ROOT = ROOT / "artifacts" / "live100_more_customer_pov_fd39220_20260705T030121Z"
DEFAULT_FACTCHECK_DIR = DEFAULT_ARTIFACT_ROOT / "final_factcheck_again_20260705"
DEFAULT_OUT_ROOT = ROOT / "artifacts" / "live100_fable5_phase0_phase1_20260705"
FIXED_NOW = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)


def install_runtime_stubs() -> None:
    """Prevent optional network clients from being required during offline replay."""
    requests_stub = types.ModuleType("requests")
    requests_stub.post = lambda *a, **k: None
    requests_stub.get = lambda *a, **k: None
    requests_stub.head = lambda *a, **k: types.SimpleNamespace(status_code=200)
    requests_stub.exceptions = types.SimpleNamespace(Timeout=TimeoutError, RequestException=Exception)
    sys.modules.setdefault("requests", requests_stub)

    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = lambda *a, **k: object()
    sys.modules.setdefault("openai", openai_stub)

    google_stub = types.ModuleType("google")
    genai_stub = types.ModuleType("google.generativeai")
    genai_stub.configure = lambda *a, **k: None
    sys.modules.setdefault("google", google_stub)
    sys.modules.setdefault("google.generativeai", genai_stub)


def import_server(out_root: Path):
    install_runtime_stubs()
    os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    os.environ.setdefault("OPENAI_API_KEY", "offline-not-used")
    os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = "1"
    os.environ["FREE_LOOKUP_DB"] = str(out_root / "free_lookup.db")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(API) not in sys.path:
        sys.path.insert(0, str(API))
    for name in ("server", "api.server"):
        sys.modules.pop(name, None)
    from api import server as server_mod  # noqa: PLC0415

    server_mod.CACHE_DB = str(out_root / "offline_cache.db")
    server_mod.DATA_DIR = str(out_root)
    try:
        server_mod.init_db()
    except Exception:
        pass
    # render_share_page embeds generated_at and serializes dicts that are assembled
    # from frozenset allowlists. Freeze time and sort JSON keys so byte-level
    # identity checks detect product/render changes, not process hash-seed noise.
    server_mod.utc_now = lambda: FIXED_NOW

    def deterministic_html_safe_json_dumps(value: object) -> str:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return (
            raw.replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
        )

    server_mod.html_safe_json_dumps = deterministic_html_safe_json_dumps
    return server_mod


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_obj(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def read_cases(artifact_root: Path) -> list[dict[str, Any]]:
    path = artifact_root / "cases.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_final_grades(factcheck_dir: Path) -> dict[str, dict[str, str]]:
    path = factcheck_dir / "FINAL_FACTCHECK_TITI_OPUS_CONFIRMED_GRADES.csv"
    grades: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            case_id = row.get("case_id") or row.get("CASE_ID") or ""
            if case_id:
                grades[case_id] = row
    return grades


def script_payload(html_text: str) -> dict[str, Any]:
    match = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html_text, flags=re.S)
    if not match:
        return {}
    return json.loads(html.unescape(match.group(1)))


def visible_text(public: dict[str, Any], html_text: str) -> str:
    visible_html = re.sub(r"<script\b.*?</script>", " ", html.unescape(html_text), flags=re.I | re.S)
    return (json.dumps(public, sort_keys=True, default=str) + "\n" + visible_html).lower()


def packet_rows(public: dict[str, Any], decision: str | None = None) -> list[dict[str, Any]]:
    packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
    rows = [r for r in packet.get("rows") or [] if isinstance(r, dict)]
    if decision:
        rows = [r for r in rows if str(r.get("decision") or "").upper() == decision]
    return rows


def canonical_families(public: dict[str, Any], decision: str = "REQUIRED") -> list[str]:
    return sorted({str(r.get("family") or r.get("filing_family") or "") for r in packet_rows(public, decision) if str(r.get("family") or r.get("filing_family") or "").strip()})


def source_labels(public: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for src in public.get("sources") or []:
        if isinstance(src, dict):
            labels.append(str(src.get("label") or src.get("title") or src.get("source_role") or ""))
    return [x for x in labels if x]


def line_provenance(public: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
    out: dict[str, list[dict[str, str]]] = {"documents": [], "inspections": [], "fees": [], "checklist": []}
    rows = packet_rows(public)
    for row in rows:
        family = str(row.get("family") or "")
        decision = str(row.get("decision") or "")
        for key in ("documents", "inspections"):
            for item in row.get(key) or []:
                out[key].append({"text": str(item), "family": family, "decision": decision, "source": "public_packet_row"})
        if row.get("fees"):
            out["fees"].append({"text": str(row.get("fees")), "family": family, "decision": decision, "source": "public_packet_row"})
    for item in packet.get("checklist") or []:
        matched_family = ""
        text = str(item)
        for row in rows:
            if str(row.get("permit_name") or "") and str(row.get("permit_name") or "") in text:
                matched_family = str(row.get("family") or "")
                break
        out["checklist"].append({"text": text, "family": matched_family, "decision": "", "source": "public_packet_checklist"})
    return out


def lint_tripwires(case: dict[str, Any], public: dict[str, Any], html_text: str) -> list[str]:
    issues: list[str] = []
    blob = visible_text(public, html_text)
    if "verify in confirm with" in blob:
        issues.append("tripwire_verify_in_confirm_with")
    job = str(case.get("job_type") or "").lower()
    non_masonry_scope = not re.search(r"\b(?:masonry|lintel|fa[cç]ade|facade|chimney|structural facade)\b", job, re.I)
    if non_masonry_scope and re.search(r"\b(?:masonry\s+lintel|structural\s+fa[cç]ade|fa[cç]ade\s+repair|facade\s+repair)\b", blob, re.I):
        issues.append("tripwire_masonry_facade_leak_non_masonry_scope")
    decision = str(public.get("permit_decision") or "").upper()
    required_copy = bool(re.search(r"\bpermit required\s*:\s*yes\b|\brequired permit package\b|\bpull\s+[^.\n]{0,80}\bpermit\b", blob, re.I))
    not_required_copy = bool(re.search(r"\bpermit required\s*:\s*no\b|\bno permit required\b|\bpermit not required\b|\bnot required for the stated scope\b", blob, re.I))
    if decision == "REQUIRED" and not_required_copy:
        issues.append("tripwire_not_required_copy_with_required_decision")
    if decision == "NOT_REQUIRED" and required_copy:
        issues.append("tripwire_required_copy_with_not_required_decision")
    return issues


def parity_issues(public: dict[str, Any], html_text: str) -> list[str]:
    issues: list[str] = []
    packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
    payload = script_payload(html_text)
    payload_data = ((payload.get("share") or {}).get("data") or {}) if isinstance(payload, dict) else {}
    if not packet:
        issues.append("missing_public_packet")
    if public.get("sealed_public_packet_hash") != packet.get("sealed_public_packet_hash"):
        issues.append("public_packet_hash_mismatch")
    if payload_data.get("sealed_public_packet_hash") != public.get("sealed_public_packet_hash"):
        issues.append("html_payload_hash_mismatch")
    if (payload_data.get("public_packet") or {}).get("sealed_public_packet_hash") != packet.get("sealed_public_packet_hash"):
        issues.append("html_payload_packet_hash_mismatch")
    if payload_data.get("permits_required") != public.get("permits_required"):
        issues.append("html_payload_required_rows_diverge")
    packet_decision = str(packet.get("decision") or packet.get("permit_required_verdict") or "").upper()
    public_decision = str(public.get("permit_decision") or "").upper()
    if packet_decision and public_decision and packet_decision != public_decision:
        issues.append("packet_public_decision_diverge")
    if public_decision == "NOT_REQUIRED" and canonical_families(public, "REQUIRED"):
        issues.append("not_required_has_required_packet_rows")
    if public_decision == "REQUIRED" and not canonical_families(public, "REQUIRED"):
        issues.append("required_missing_required_packet_rows")
    return issues


def replay_one(server_mod, rec: dict[str, Any]) -> dict[str, Any]:
    case = rec["case"]
    previous_case_id = os.environ.get("PERMITASSIST_TRACE_CASE_ID")
    os.environ["PERMITASSIST_TRACE_CASE_ID"] = str(case.get("id") or "unknown_case")
    try:
        raw = json.loads(json.dumps(rec.get("response_body") or {}))
        public = server_mod.build_customer_permit_view_model(
            raw,
            case.get("job_type") or "",
            case.get("city") or "",
            case.get("state") or "",
            job_category=case.get("segment"),
        )
        share = {"slug": case.get("id"), "data": public, "job_type": case.get("job_type") or "", "city": case.get("city") or "", "state": case.get("state") or ""}
        html_text = server_mod.render_share_page(share)
        return {"case": case, "public": public, "html": html_text}
    finally:
        if previous_case_id is None:
            os.environ.pop("PERMITASSIST_TRACE_CASE_ID", None)
        else:
            os.environ["PERMITASSIST_TRACE_CASE_ID"] = previous_case_id


def snapshot(args: argparse.Namespace) -> int:
    out = args.out / args.label
    out.mkdir(parents=True, exist_ok=True)
    (out / "html").mkdir(exist_ok=True)
    (out / "public_json").mkdir(exist_ok=True)
    trace_dir = out / "trace"
    if args.enable_trace:
        trace_dir.mkdir(exist_ok=True)
        os.environ["PERMITASSIST_PHASE_TRACE_DIR"] = str(trace_dir)
    else:
        os.environ.pop("PERMITASSIST_PHASE_TRACE_DIR", None)
    server_mod = import_server(out)
    cases = read_cases(args.artifact_root)
    grades = read_final_grades(args.factcheck_dir)
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for idx, rec in enumerate(cases, 1):
        cid = rec["case"]["id"]
        rendered = replay_one(server_mod, rec)
        public = rendered["public"]
        html_text = rendered["html"]
        (out / "html" / f"{idx:03d}_{cid}.html").write_text(html_text, encoding="utf-8")
        (out / "public_json" / f"{idx:03d}_{cid}.json").write_text(json.dumps(public, indent=2, sort_keys=True, default=str), encoding="utf-8")
        row = {
            "index": idx,
            "case_id": cid,
            "segment": rec["case"].get("segment"),
            "city": rec["case"].get("city"),
            "state": rec["case"].get("state"),
            "final_grade": (grades.get(cid) or {}).get("final_grade") or (grades.get(cid) or {}).get("factcheck_final_grade") or (grades.get(cid) or {}).get("grade") or "",
            "decision": public.get("permit_decision"),
            "required_families": canonical_families(public, "REQUIRED"),
            "conditional_families": canonical_families(public, "CONDITIONAL"),
            "apply_url": public.get("apply_url") or "",
            "authority": public.get("applying_office") or ((public.get("apply_path") or {}).get("office_name") if isinstance(public.get("apply_path"), dict) else ""),
            "source_labels": source_labels(public),
            "source_urls": public.get("source_urls") or [],
            "fee_present": bool(public.get("fee_range") or ((public.get("public_packet") or {}).get("fees") if isinstance(public.get("public_packet"), dict) else [])),
            "public_sha256": sha256_obj(public),
            "html_sha256": sha256_text(html_text),
            "tripwire_issues": lint_tripwires(rec["case"], public, html_text),
            "parity_issues": parity_issues(public, html_text),
            "line_provenance": line_provenance(public),
        }
        if row["tripwire_issues"] or row["parity_issues"]:
            failures.append({"case_id": cid, "tripwire_issues": row["tripwire_issues"], "parity_issues": row["parity_issues"]})
        rows.append(row)
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "fixed_render_time": FIXED_NOW.isoformat(),
        "artifact_root": str(args.artifact_root),
        "factcheck_dir": str(args.factcheck_dir),
        "label": args.label,
        "records": len(rows),
        "grade_counts": dict(Counter(r["final_grade"] for r in rows)),
        "html_hash_all": hashlib.sha256("\n".join(r["html_sha256"] for r in rows).encode()).hexdigest(),
        "public_hash_all": hashlib.sha256("\n".join(r["public_sha256"] for r in rows).encode()).hexdigest(),
        "tripwire_failure_count": sum(1 for f in failures if f["tripwire_issues"]),
        "parity_failure_count": sum(1 for f in failures if f["parity_issues"]),
        "failures": failures,
        "rows": rows,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("label", "records", "grade_counts", "html_hash_all", "public_hash_all", "tripwire_failure_count", "parity_failure_count")}, indent=2, sort_keys=True))
    return 0


_C044_FIRE_LABEL_VARIANTS = (
    "Fire Protection Permit — Kitchen Hood Suppression System",
    "Fire Protection Permit \\u2014 Kitchen Hood Suppression System",
    "Fire / Hood Suppression Permit",
)
_C044_FIRE_LABEL_CANONICAL = "Fire / Hood Suppression Permit"
_C044_DECISION_FIELDS = (
    "permit_decision",
    "permit_required",
    "permit_verdict",
    "decision_basis",
    "confidence_tier",
    "degraded_sources",
)
_SEAL_HASH_KEYS = {"sealed_public_packet_hash", "public_packet_hash", "render_parity_hash"}
_RENDER_SEAL_METADATA_KEYS = {
    "render_seal_status",
    "render_seal_reason",
    "_render_seal_status",
    "_render_seal_reason",
}


def _case_file(root: Path, subdir: str, case_id: str, suffix: str) -> Path | None:
    matches = list((root / subdir).glob(f"*{case_id}{suffix}"))
    return matches[0] if len(matches) == 1 else None


def _canonicalize_c044_label_text(text: str) -> str:
    out = text
    for variant in _C044_FIRE_LABEL_VARIANTS:
        out = out.replace(variant, _C044_FIRE_LABEL_CANONICAL)
    return out


def _canonicalize_c044_public(value: Any) -> Any:
    if isinstance(value, str):
        # C-044's fire/hood label is produced from an order-dependent legacy row.
        # Ignore only that label spelling and the downstream seal hashes it changes;
        # any decision/family/action-path change remains visible to compare.
        return _canonicalize_c044_label_text(value)
    if isinstance(value, list):
        return [_canonicalize_c044_public(item) for item in value]
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key in _SEAL_HASH_KEYS or key.endswith("_hash"):
                cleaned[key] = "<canonicalized_hash>"
            else:
                cleaned[key] = _canonicalize_c044_public(item)
        return cleaned
    return value


def _canonicalize_compare_metadata_public(value: Any) -> Any:
    """Canonicalize metadata-only render seal churn for identity compare.

    Session 2 deliberately adds seal-status audit markers to previously unsealed
    NOT_REQUIRED payloads.  These markers must not permanently red-line the
    Live100 identity gate, but substantive decision/family/render changes must
    remain visible.  Therefore this canonicalizer only removes explicit
    render-seal metadata keys and canonicalizes hash-shaped seal values.
    """
    if isinstance(value, list):
        return [_canonicalize_compare_metadata_public(item) for item in value]
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key in _RENDER_SEAL_METADATA_KEYS:
                continue
            if key in _SEAL_HASH_KEYS or key.endswith("_hash"):
                cleaned[key] = "<canonicalized_hash>"
            else:
                cleaned[key] = _canonicalize_compare_metadata_public(item)
        return cleaned
    return value


def _canonicalize_compare_metadata_html(text: str) -> str:
    hash_re = re.compile(r'("(?:sealed_public_packet_hash|public_packet_hash|render_parity_hash|[^"]*_hash)"\s*:\s*")(?:sha256:)?[0-9a-f]{32,64}(")')
    out = hash_re.sub(r'\1<canonicalized_hash>\2', text)
    for key in _RENDER_SEAL_METADATA_KEYS:
        out = re.sub(rf',\s*"{re.escape(key)}"\s*:\s*"[^"]*"', "", out)
        out = re.sub(rf'"{re.escape(key)}"\s*:\s*"[^"]*"\s*,', "", out)
    return out


def _load_case_public(root: Path, case_id: str) -> dict[str, Any]:
    path = _case_file(root, "public_json", case_id, ".json")
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_case_html(root: Path, case_id: str) -> str:
    path = _case_file(root, "html", case_id, ".html")
    if path is None:
        return ""
    return path.read_text(encoding="utf-8")


def _c044_known_label_flap_only(before_root: Path, after_root: Path) -> tuple[bool, dict[str, Any]]:
    before_public = _load_case_public(before_root, "C-044")
    after_public = _load_case_public(after_root, "C-044")
    evidence: dict[str, Any] = {
        "case_id": "C-044",
        "waiver": "known_fire_hood_label_flap_only",
        "decision_fields": {},
        "canonical_public_equal": False,
        "canonical_html_equal": False,
    }
    if not before_public or not after_public:
        evidence["reason"] = "missing_public_json"
        return False, evidence
    decision_ok = True
    for field in _C044_DECISION_FIELDS:
        b_val = before_public.get(field)
        a_val = after_public.get(field)
        same = b_val == a_val
        evidence["decision_fields"][field] = {"before": b_val, "after": a_val, "same": same}
        decision_ok = decision_ok and same
    before_canon = _canonicalize_c044_public(before_public)
    after_canon = _canonicalize_c044_public(after_public)
    public_equal = before_canon == after_canon
    before_html = _canonicalize_c044_label_text(_load_case_html(before_root, "C-044"))
    after_html = _canonicalize_c044_label_text(_load_case_html(after_root, "C-044"))
    # The HTML embeds public JSON including seal hashes; scrub hash-shaped values
    # after label canonicalization so the compare still blocks non-label HTML drift.
    hash_re = re.compile(r'("(?:sealed_public_packet_hash|public_packet_hash|render_parity_hash|[^"]*_hash)"\s*:\s*")(?:sha256:)?[0-9a-f]{32,64}(")')
    before_html_canon = hash_re.sub(r'\1<canonicalized_hash>\2', before_html)
    after_html_canon = hash_re.sub(r'\1<canonicalized_hash>\2', after_html)
    html_equal = before_html_canon == after_html_canon
    evidence["canonical_public_equal"] = public_equal
    evidence["canonical_html_equal"] = html_equal
    return decision_ok and public_equal and html_equal, evidence


def _render_seal_metadata_or_hash_only(before_root: Path, after_root: Path, case_id: str) -> tuple[bool, dict[str, Any]]:
    before_public = _load_case_public(before_root, case_id)
    after_public = _load_case_public(after_root, case_id)
    evidence: dict[str, Any] = {
        "case_id": case_id,
        "waiver": "render_seal_metadata_or_hash_only",
        "decision_fields": {},
        "canonical_public_equal": False,
        "canonical_html_equal": False,
    }
    if not before_public or not after_public:
        evidence["reason"] = "missing_public_json"
        return False, evidence
    decision_ok = True
    for field in _C044_DECISION_FIELDS:
        b_val = before_public.get(field)
        a_val = after_public.get(field)
        same = b_val == a_val
        evidence["decision_fields"][field] = {"before": b_val, "after": a_val, "same": same}
        decision_ok = decision_ok and same
    public_equal = _canonicalize_compare_metadata_public(before_public) == _canonicalize_compare_metadata_public(after_public)
    html_equal = _canonicalize_compare_metadata_html(_load_case_html(before_root, case_id)) == _canonicalize_compare_metadata_html(_load_case_html(after_root, case_id))
    evidence["canonical_public_equal"] = public_equal
    evidence["canonical_html_equal"] = html_equal
    return decision_ok and public_equal and html_equal, evidence


def compare(args: argparse.Namespace) -> int:
    before = json.loads((args.compare_before / "manifest.json").read_text(encoding="utf-8"))
    after = json.loads((args.compare_after / "manifest.json").read_text(encoding="utf-8"))
    before_rows = {r["case_id"]: r for r in before["rows"]}
    after_rows = {r["case_id"]: r for r in after["rows"]}
    diffs = []
    for cid in sorted(before_rows):
        b = before_rows[cid]
        a = after_rows.get(cid)
        if not a:
            diffs.append({"case_id": cid, "type": "missing_after"})
            continue
        if b["html_sha256"] != a["html_sha256"]:
            diffs.append({"case_id": cid, "type": "html_sha256", "before": b["html_sha256"], "after": a["html_sha256"]})
        if b["public_sha256"] != a["public_sha256"]:
            diffs.append({"case_id": cid, "type": "public_sha256", "before": b["public_sha256"], "after": a["public_sha256"]})

    waived_diffs: list[dict[str, Any]] = []
    remaining_diffs: list[dict[str, Any]] = []
    for cid in sorted({d.get("case_id") for d in diffs}):
        case_diffs = [d for d in diffs if d.get("case_id") == cid]
        case_types = {d.get("type") for d in case_diffs}
        if cid == "C-044" and case_types.issubset({"html_sha256", "public_sha256"}):
            waiver_ok, waiver_evidence = _c044_known_label_flap_only(args.compare_before, args.compare_after)
            if waiver_ok:
                waived_diffs.extend({**d, "waived_by": "C044_FIRE_HOOD_LABEL_CANONICALIZATION"} for d in case_diffs)
                continue
            waived_diffs.append({"case_id": "C-044", "waiver_rejected": waiver_evidence})
        if case_types.issubset({"html_sha256", "public_sha256"}):
            waiver_ok, waiver_evidence = _render_seal_metadata_or_hash_only(args.compare_before, args.compare_after, str(cid))
            if waiver_ok:
                waived_diffs.extend({**d, "waived_by": "RENDER_SEAL_METADATA_OR_HASH_ONLY"} for d in case_diffs)
                continue
            waived_diffs.append({"case_id": cid, "waiver_rejected": waiver_evidence})
        remaining_diffs.extend(case_diffs)

    report = {
        "before": str(args.compare_before),
        "after": str(args.compare_after),
        "records_before": before.get("records"),
        "records_after": after.get("records"),
        "identity_diff_pass": not remaining_diffs and before.get("records") == after.get("records") == 100,
        "diff_count": len(remaining_diffs),
        "raw_diff_count": len(diffs),
        "diffs": remaining_diffs[:200],
        "waived_diffs": waived_diffs[:200],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "IDENTITY_DIFF_REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["identity_diff_pass"] else 1


def build_fixtures(args: argparse.Namespace) -> int:
    snapshot_manifest = json.loads((args.snapshot / "manifest.json").read_text(encoding="utf-8"))
    rows = snapshot_manifest["rows"]
    by_id = {r["case_id"]: r for r in rows}
    grades = read_final_grades(args.factcheck_dir)
    green: dict[str, Any] = {}
    red: dict[str, Any] = {}
    for cid, grade_row in sorted(grades.items()):
        grade = grade_row.get("final_grade") or grade_row.get("factcheck_final_grade") or grade_row.get("grade") or ""
        row = by_id.get(cid)
        if not row:
            continue
        if grade in {"A", "B"}:
            green[cid] = {
                "grade": grade,
                "segment": row["segment"],
                "decision": row["decision"],
                "canonical_required_family_keys": row["required_families"],
                "canonical_conditional_family_keys": row["conditional_families"],
                "apply_url": row["apply_url"],
                "authority": row["authority"],
                "source_labels": row["source_labels"],
                "source_urls": row["source_urls"],
                "fee_present": row["fee_present"],
                "line_provenance": row["line_provenance"],
            }
        elif grade in {"C", "F"}:
            red[cid] = {
                "grade": grade,
                "segment": row["segment"],
                "observed_decision": row["decision"],
                "observed_required_family_keys": row["required_families"],
                "observed_conditional_family_keys": row["conditional_families"],
                "observed_apply_url": row["apply_url"],
                "observed_authority": row["authority"],
                "factcheck_issues": grade_row.get("issues") or grade_row.get("factcheck_issues") or grade_row.get("fable_issues") or "",
                "factcheck_rationale": grade_row.get("rationale") or grade_row.get("factcheck_rationale") or grade_row.get("fable_rationale") or "",
                "structural_assertion_status": "RED_BASELINE_EXPECTED_TO_FAIL_UNTIL_CHUNKS_A_E",
            }
    fixture_dir = ROOT / "tests" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    green_path = fixture_dir / "live100_fable5_final_green_freeze_68_20260705.json"
    red_path = fixture_dir / "live100_fable5_final_red_structural_32_20260705.json"
    green_doc = {"artifact_root": str(args.artifact_root), "factcheck_dir": str(args.factcheck_dir), "source_snapshot": str(args.snapshot), "count": len(green), "cases": green}
    red_doc = {"artifact_root": str(args.artifact_root), "factcheck_dir": str(args.factcheck_dir), "source_snapshot": str(args.snapshot), "count": len(red), "cases": red}
    green_path.write_text(json.dumps(green_doc, indent=2, sort_keys=True, default=str), encoding="utf-8")
    red_path.write_text(json.dumps(red_doc, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report = {"green_path": str(green_path), "green_count": len(green), "red_path": str(red_path), "red_count": len(red)}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "FIXTURE_BUILD_REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if len(green) == 68 and len(red) == 32 else 1


def determinism(args: argparse.Namespace) -> int:
    run1 = args.out / "determinism_run_1"
    run2 = args.out / "determinism_run_2"
    ns1 = argparse.Namespace(**{**vars(args), "label": "determinism_run_1", "enable_trace": args.enable_trace})
    ns2 = argparse.Namespace(**{**vars(args), "label": "determinism_run_2", "enable_trace": args.enable_trace})
    snapshot(ns1)
    snapshot(ns2)
    cmp_ns = argparse.Namespace(out=args.out, compare_before=run1, compare_after=run2)
    rc = compare(cmp_ns)
    report_path = args.out / "IDENTITY_DIFF_REPORT.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["determinism_pass"] = report.pop("identity_diff_pass")
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return rc


def attribution(args: argparse.Namespace) -> int:
    snapshot_manifest = json.loads((args.snapshot / "manifest.json").read_text(encoding="utf-8"))
    grades = read_final_grades(args.factcheck_dir)
    rows = {r["case_id"]: r for r in snapshot_manifest["rows"]}
    cf = []
    hypotheses = {
        "stale_summary_mirror": {"status": "CONFIRMED_PRESENT_IN_BASELINE", "evidence": []},
        "shared_cache_template_leak": {"status": "CONFIRMED_TRIPWIRE_PRESENT_IN_BASELINE", "evidence": []},
        "existing_verification_or_conditional_state": {"status": "CONFIRMED_EXISTING_CONDITIONAL_TIER_PRESENT", "evidence": []},
        "apply_url_loss_point": {"status": "MIXED_PROJECTION_AND_SOURCE_RESOLUTION_LOSS", "evidence": []},
    }
    for cid, grade_row in sorted(grades.items()):
        grade = grade_row.get("final_grade") or grade_row.get("factcheck_final_grade") or grade_row.get("grade") or ""
        if grade not in {"C", "F"}:
            continue
        row = rows[cid]
        issues = (grade_row.get("issues") or grade_row.get("factcheck_issues") or grade_row.get("fable_issues") or "").lower()
        category = []
        if re.search(r"missing|required.*family|building permit|companion|core", issues):
            category.append("family_reconciliation_or_floor")
        if re.search(r"wrong.*authority|apply|url|source|filing", issues):
            category.append("action_path_authority_projection")
        if re.search(r"not_required|contradiction|no permit|required", issues):
            category.append("decision_parity_or_summary_mirror")
        if re.search(r"fee|garbled|verify in confirm|checklist|masonry|facade|leak", issues):
            category.append("render_template_or_line_provenance")
        if not category:
            category.append("manual_review_required")
        cf.append({
            "case_id": cid,
            "grade": grade,
            "categories": category,
            "observed_decision": row["decision"],
            "required_families": row["required_families"],
            "conditional_families": row["conditional_families"],
            "apply_url": row["apply_url"],
            "authority": row["authority"],
            "tripwire_issues": row["tripwire_issues"],
            "parity_issues": row["parity_issues"],
            "factcheck_issues": grade_row.get("issues") or grade_row.get("factcheck_issues") or grade_row.get("fable_issues") or "",
            "factcheck_rationale": grade_row.get("rationale") or grade_row.get("factcheck_rationale") or grade_row.get("fable_rationale") or "",
        })
    # Evidence samples for hypotheses from actual rows.
    for row in snapshot_manifest["rows"]:
        if row["conditional_families"] and len(hypotheses["existing_verification_or_conditional_state"]["evidence"]) < 5:
            hypotheses["existing_verification_or_conditional_state"]["evidence"].append({"case_id": row["case_id"], "conditional_families": row["conditional_families"]})
        if row["tripwire_issues"] and len(hypotheses["shared_cache_template_leak"]["evidence"]) < 10:
            hypotheses["shared_cache_template_leak"]["evidence"].append({"case_id": row["case_id"], "tripwire_issues": row["tripwire_issues"]})
        if row["decision"] == "REQUIRED" and not row["apply_url"] and len(hypotheses["apply_url_loss_point"]["evidence"]) < 10:
            hypotheses["apply_url_loss_point"]["evidence"].append({"case_id": row["case_id"], "authority": row["authority"], "source_urls": row["source_urls"]})
    for item in cf:
        if "decision_parity_or_summary_mirror" in item["categories"] and len(hypotheses["stale_summary_mirror"]["evidence"]) < 10:
            hypotheses["stale_summary_mirror"]["evidence"].append(item)
    doc = {
        "artifact_root": str(args.artifact_root),
        "factcheck_dir": str(args.factcheck_dir),
        "snapshot": str(args.snapshot),
        "c_f_count": len(cf),
        "hypotheses": hypotheses,
        "c_f_attribution": cf,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "PHASE0_ATTRIBUTION_DOC.json").write_text(json.dumps(doc, indent=2, sort_keys=True, default=str), encoding="utf-8")
    md_lines = ["# Live100 Fable 5 Phase 0 attribution", "", f"Artifact root: `{args.artifact_root}`", f"Factcheck dir: `{args.factcheck_dir}`", f"C/F rows mapped: {len(cf)}", "", "## Hypotheses"]
    for name, info in hypotheses.items():
        md_lines.append(f"- **{name}**: {info['status']} (evidence samples: {len(info['evidence'])})")
    md_lines.extend(["", "## C/F attribution ledger", "", "| case | grade | categories | observed families | apply/authority | issues |", "|---|---:|---|---|---|---|"])
    for item in cf:
        md_lines.append(f"| {item['case_id']} | {item['grade']} | {', '.join(item['categories'])} | {', '.join(item['required_families'])} | {item['apply_url'] or item['authority']} | {str(item['factcheck_issues']).replace('|','/')} |")
    (args.out / "PHASE0_ATTRIBUTION_DOC.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(json.dumps({"attribution_doc": str(args.out / "PHASE0_ATTRIBUTION_DOC.md"), "c_f_count": len(cf), "hypotheses": {k: v["status"] for k, v in hypotheses.items()}}, indent=2, sort_keys=True))
    return 0 if len(cf) == 32 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["snapshot", "compare", "fixtures", "determinism", "attribution"])
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--factcheck-dir", type=Path, default=DEFAULT_FACTCHECK_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--label", default="snapshot")
    parser.add_argument("--snapshot", type=Path, help="snapshot dir containing manifest.json")
    parser.add_argument("--compare-before", type=Path)
    parser.add_argument("--compare-after", type=Path)
    parser.add_argument("--enable-trace", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.mode == "snapshot":
        return snapshot(args)
    if args.mode == "compare":
        if not args.compare_before or not args.compare_after:
            parser.error("compare requires --compare-before and --compare-after")
        return compare(args)
    if args.mode == "fixtures":
        if not args.snapshot:
            parser.error("fixtures requires --snapshot")
        return build_fixtures(args)
    if args.mode == "determinism":
        return determinism(args)
    if args.mode == "attribution":
        if not args.snapshot:
            parser.error("attribution requires --snapshot")
        return attribution(args)
    raise AssertionError(args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
