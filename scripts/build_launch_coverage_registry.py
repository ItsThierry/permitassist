#!/usr/bin/env python3
"""Compile a sealed launch-coverage registry from a frozen PermitAssist case plan."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


APPLY_ROUTE_REPLACEMENTS = {
    # Official sites periodically rotate document IDs while retaining a live
    # forms/portal page. Keys are stale official routes, never case IDs.
    "https://www.albanyny.gov/DocumentCenter/View/2422/General-Building-Permit-Application": (
        "https://www.albanyny.gov/687/BRC-Forms-Informationals",
        "City of Albany BRC Forms & Informationals",
    ),
    "https://www.charleston-sc.gov/DocumentCenter/View/31256/Permit-Requirements---Window-Replacement---Residential": (
        "https://charleston-sc.gov/856/Permit-Center",
        "City of Charleston Permit Center",
    ),
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _application_source(sources: list[dict[str, Any]]) -> dict[str, Any]:
    priorities = ("apply", "application", "online permit", "permit portal", "submittal", "file", "obtain")
    for token in priorities:
        for source in sources:
            blob = f"{source.get('title', '')} {source.get('supports', '')}".lower()
            if token in blob and str(source.get("url") or "").startswith("https://"):
                return source
    return next((s for s in sources if str(s.get("url") or "").startswith("https://")), {})


def _support_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _archetype(case: dict[str, Any]) -> str:
    value = str(case.get("primary_class") or case.get("bucket") or "general")
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "general"


def _compile_blind_contract(case: dict[str, Any]) -> dict[str, Any]:
    sources = [
        {
            "title": str(source.get("title") or "").strip(),
            "url": str(source.get("url") or "").strip(),
            "supports": _support_text(source.get("supports")),
            "role": "OFFICIAL_CLAIM_EVIDENCE",
        }
        for source in case.get("official_sources") or []
        if str(source.get("url") or "").startswith("https://")
    ]
    apply_source = _application_source(sources)
    required = [str(x).strip() for x in case.get("required_families") or [] if str(x).strip()]
    conditional = [str(x).strip() for x in case.get("conditional_families") or [] if str(x).strip()]
    archetype_seed = required or conditional or ["source_backed_no_permit"]
    contract = {
        "contract_id": str(case["blind_id"]),
        "segment": str(case["segment"]).lower(),
        "city": str(case["city"]).strip(),
        "state": str(case["state"]).strip().upper(),
        "zip_code": str(case.get("zip_code") or "").strip(),
        "job_type": str(case["job_type"]).strip(),
        "project_archetype": re.sub(r"[^a-z0-9]+", "_", "_".join(archetype_seed).lower()).strip("_") or "general",
        "fact_profile": "independent_blind_complete_scope",
        "decision": str(case["expected_decision"]).upper(),
        "authority": str(case["correct_ahj"]).strip(),
        "authority_note": "",
        "required_families": required,
        "conditional_families": conditional,
        "prohibited_hard_required_families": [str(x).strip() for x in case.get("prohibited") or [] if str(x).strip()],
        "required_facts": [],
        "official_sources": sources,
        "apply_url": str(apply_source.get("url") or ""),
        "apply_source_title": str(apply_source.get("title") or ""),
        "maps_destination": str(case["correct_ahj"]).strip(),
        "evidence_freshness": "INDEPENDENT_BLIND_OFFICIAL_SOURCE_REVIEW",
    }
    contract["contract_sha256"] = sha256(contract)
    return contract


def compile_registry(
    plan: dict[str, Any], source_path: str, blind_cohorts: list[tuple[str, list[dict[str, Any]]]] | None = None
) -> dict[str, Any]:
    contracts = []
    for case in plan["cases"]:
        envelope = case["expected_envelope"]
        sources = [
            {
                "title": str(source.get("title") or "").strip(),
                "url": str(source.get("url") or "").strip(),
                "supports": _support_text(source.get("supports")),
                "role": "OFFICIAL_CLAIM_EVIDENCE",
            }
            for source in envelope.get("official_sources") or []
            if str(source.get("url") or "").startswith("https://")
        ]
        apply_source = _application_source(sources)
        apply_url = str(apply_source.get("url") or "")
        apply_title = str(apply_source.get("title") or "")
        replacement = APPLY_ROUTE_REPLACEMENTS.get(apply_url)
        if replacement:
            apply_url, apply_title = replacement
        required_facts = [
            str(value).strip()
            for value in envelope.get("required_facts") or []
            if str(value).strip()
        ]
        contract = {
            "contract_id": str(case["id"]),
            "segment": str(case["segment"]).lower(),
            "city": str(case["city"]).strip(),
            "state": str(case["state"]).strip().upper(),
            "zip_code": str(case.get("zip") or "").strip(),
            "job_type": str(case["job_type"]).strip(),
            "project_archetype": _archetype(case),
            "fact_profile": "frozen_complete_scope",
            "decision": str(envelope["verdict"]).upper(),
            "authority": str(envelope["correct_ahj"]).strip(),
            "authority_note": str(envelope.get("authority_note") or "").strip(),
            "required_families": [str(x).strip() for x in envelope.get("minimum_permit_families") or [] if str(x).strip()],
            "conditional_families": [str(x).strip() for x in envelope.get("acceptable_conditional_families") or [] if str(x).strip()],
            "prohibited_hard_required_families": [str(x).strip() for x in envelope.get("prohibited_hard_required_families") or [] if str(x).strip()],
            "required_facts": required_facts,
            "official_sources": sources,
            "apply_url": apply_url,
            "apply_source_title": apply_title,
            "maps_destination": str(envelope["correct_ahj"]).strip(),
            "evidence_freshness": str(envelope.get("citation_status") or "FROZEN_PRE_AUTH"),
        }
        contract["contract_sha256"] = sha256(contract)
        contracts.append(contract)
    source_plans = [{"path": source_path, "sha256": sha256(plan), "kind": "frozen_live100"}]
    for cohort_path, cohort in blind_cohorts or []:
        contracts.extend(_compile_blind_contract(case) for case in cohort)
        source_plans.append({"path": cohort_path, "sha256": sha256(cohort), "kind": "independent_blind"})
    payload = {
        "schema_version": "permitassist.launch-coverage.v1",
        "source_plan": source_path,
        "source_plan_sha256": sha256(plan),
        "source_plans": source_plans,
        "contract_count": len(contracts),
        "coverage_scope": "Exact frozen semantic profiles only; unmatched requests are UNSUPPORTED before paid execution.",
        "contracts": contracts,
    }
    payload["registry_sha256"] = sha256(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_plan", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--blind-cohort", action="append", default=[], type=Path)
    args = parser.parse_args()
    plan = json.loads(args.input_plan.read_text(encoding="utf-8"))
    blind_cohorts = [
        (str(path), json.loads(path.read_text(encoding="utf-8")))
        for path in args.blind_cohort
    ]
    registry = compile_registry(plan, str(args.input_plan), blind_cohorts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(registry, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"contracts": registry["contract_count"], "registry_sha256": registry["registry_sha256"], "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
