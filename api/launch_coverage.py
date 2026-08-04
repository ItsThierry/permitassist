"""Pre-charge support gate and deterministic source-backed customer projection.

The launch registry is intentionally narrow.  A request that does not match an
immutable, complete contract is blocked before model execution, report creation,
or retained charge.  This module never turns uncertainty into a paid VERIFY
report.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import quote_plus

DEFAULT_REGISTRY_PATH = Path(__file__).with_name("launch_coverage_registry.json")
_ALLOWED_DECISIONS = frozenset({"REQUIRED", "NOT_REQUIRED", "CONDITIONAL"})


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _normalized_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _request_key(*, job_type: str, city: str, state: str, zip_code: str, segment: str) -> str:
    return _sha256({
        "city": _normalized_text(city),
        "job_type": _normalized_text(job_type),
        "segment": _normalized_text(segment),
        "state": str(state or "").strip().upper(),
        "zip_code": re.sub(r"[^0-9-]", "", str(zip_code or "")),
    })


class SupportOutcome(str, Enum):
    SUPPORTED = "SUPPORTED"
    NEEDS_FACT = "NEEDS_FACT"
    UNSUPPORTED = "UNSUPPORTED"
    INFRA_FAILURE = "INFRA_FAILURE"


@dataclass(frozen=True)
class SupportResolution:
    outcome: SupportOutcome
    reason_code: str
    contract: Mapping[str, Any] | None = None
    missing_facts: tuple[str, ...] = ()
    customer_report: None = None
    retained_charge: bool = False
    model_call_allowed: bool = False


@dataclass(frozen=True)
class CoverageRegistry:
    schema_version: str
    registry_sha256: str
    contracts: tuple[Mapping[str, Any], ...]
    _by_key: Mapping[str, Mapping[str, Any]]
    _integrity_ok: bool

    @property
    def contract_count(self) -> int:
        return len(self.contracts)

    def verify_integrity(self) -> bool:
        return self._integrity_ok

    def find(self, *, job_type: str, city: str, state: str, zip_code: str, segment: str) -> Mapping[str, Any] | None:
        key = _request_key(job_type=job_type, city=city, state=state, zip_code=zip_code, segment=segment)
        return self._by_key.get(key)


def _validate_contract(raw: dict[str, Any]) -> dict[str, Any]:
    contract = copy.deepcopy(raw)
    recorded = str(contract.pop("contract_sha256", ""))
    if not recorded or _sha256(contract) != recorded:
        raise ValueError(f"coverage contract integrity failure: {raw.get('contract_id', 'unknown')}")
    contract["contract_sha256"] = recorded
    if contract.get("decision") not in _ALLOWED_DECISIONS:
        raise ValueError("coverage contract has invalid decision")
    if not contract.get("authority") or not contract.get("official_sources"):
        raise ValueError("coverage contract lacks authority or official sources")
    if any(not str(source.get("url") or "").startswith("https://") for source in contract["official_sources"]):
        raise ValueError("coverage contract contains a non-HTTPS source")
    return contract


@lru_cache(maxsize=8)
def _load_cached(path_string: str, mtime_ns: int, size: int) -> CoverageRegistry:
    del mtime_ns, size
    path = Path(path_string)
    raw = json.loads(path.read_text(encoding="utf-8"))
    recorded_registry_sha = str(raw.get("registry_sha256") or "")
    unsigned = copy.deepcopy(raw)
    unsigned.pop("registry_sha256", None)
    integrity_ok = bool(recorded_registry_sha and _sha256(unsigned) == recorded_registry_sha)
    if not integrity_ok:
        raise ValueError("launch coverage registry integrity failure")
    contracts = tuple(MappingProxyType(_validate_contract(item)) for item in raw.get("contracts") or [])
    if int(raw.get("contract_count", -1)) != len(contracts):
        raise ValueError("launch coverage registry count mismatch")
    by_key: dict[str, Mapping[str, Any]] = {}
    for contract in contracts:
        key = _request_key(
            job_type=str(contract["job_type"]), city=str(contract["city"]), state=str(contract["state"]),
            zip_code=str(contract.get("zip_code") or ""), segment=str(contract["segment"]),
        )
        if key in by_key:
            raise ValueError("duplicate launch coverage request key")
        by_key[key] = contract
    return CoverageRegistry(
        schema_version=str(raw.get("schema_version") or ""),
        registry_sha256=recorded_registry_sha,
        contracts=contracts,
        _by_key=MappingProxyType(by_key),
        _integrity_ok=True,
    )


def load_coverage_registry(path: Path | str = DEFAULT_REGISTRY_PATH) -> CoverageRegistry:
    resolved = Path(path).resolve()
    stat = resolved.stat()
    return _load_cached(str(resolved), stat.st_mtime_ns, stat.st_size)


def resolve_precharge_support(
    *, job_type: str, city: str, state: str, zip_code: str = "", segment: str = "",
    supplied_facts: Mapping[str, Any] | None = None, registry_path: Path | str = DEFAULT_REGISTRY_PATH,
) -> SupportResolution:
    try:
        registry = load_coverage_registry(registry_path)
    except Exception:
        return SupportResolution(SupportOutcome.INFRA_FAILURE, "coverage_registry_unavailable")
    contract = registry.find(job_type=job_type, city=city, state=state, zip_code=zip_code, segment=segment)
    if contract is None:
        return SupportResolution(SupportOutcome.UNSUPPORTED, "exact_coverage_contract_missing")
    required_facts = tuple(str(x) for x in contract.get("required_facts") or ())
    facts = supplied_facts or {}
    missing = tuple(name for name in required_facts if facts.get(name) in (None, ""))
    if missing:
        return SupportResolution(SupportOutcome.NEEDS_FACT, "material_fact_missing", contract=contract, missing_facts=missing)
    return SupportResolution(
        SupportOutcome.SUPPORTED,
        "sealed_source_backed_contract",
        contract=contract,
        model_call_allowed=False,
    )


def _canonical_family(value: str) -> str:
    text = _normalized_text(value)
    ordered = (
        ("electrical", ("electrical", "wiring", "circuit")),
        ("plumbing", ("plumbing", "water heater", "sewer")),
        ("mechanical", ("mechanical", "hvac", "heating", "cooling", "refrigeration")),
        ("gas", ("fuel gas", "gas piping")),
        ("fire", ("fire", "sprinkler", "alarm", "life safety")),
        ("health", ("health", "food establishment")),
        ("wastewater", ("wastewater", "septic", "ostds", "fog")),
        ("planning_zoning", ("planning", "zoning", "land use", "certificate of appropriateness", "historic")),
        ("building", ("building", "construction", "tenant improvement", "demolition", "roof", "deck", "pool")),
        ("certificate_of_occupancy", ("occupancy", "certificate of occupancy")),
    )
    for family, tokens in ordered:
        if any(token in text for token in tokens):
            return family
    return "specialty"


def _citation(source: Mapping[str, Any], *, field: str, value: str, permit_family: str = "") -> dict[str, Any]:
    item = {
        "field": field,
        "value": value,
        "source_title": str(source.get("title") or "Official authority source"),
        "source_url": str(source.get("url") or ""),
        "quoted_snippet": str(source.get("supports") or ""),
        "source_role": "OFFICIAL_CLAIM_EVIDENCE",
    }
    if permit_family:
        item["permit_family"] = permit_family
    return item


def build_supported_customer_projection(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Purely project one validated registry contract; never mutate the input."""
    value = copy.deepcopy(dict(contract))
    decision = str(value["decision"])
    sources = copy.deepcopy(value["official_sources"])
    primary_source = sources[0]
    required_rows = []
    citations = [_citation(primary_source, field="permit_decision", value=decision)]
    for family in value.get("required_families") or []:
        canonical_family = _canonical_family(family)
        row = {
            "permit_family": family,
            "filing_family": canonical_family,
            "family": canonical_family,
            "category": canonical_family,
            "kind": family,
            "permit_name": family,
            "status": "REQUIRED",
            "required_status": "REQUIRED",
            "required": True,
            "decision": "REQUIRED",
            "applying_office": value["authority"],
            "apply_url": value.get("apply_url") or primary_source["url"],
            "source_url": primary_source["url"],
            "source_title": primary_source["title"],
            "next_step": f"Use the official source and contact {value['authority']} to file the {family} lane.",
        }
        required_rows.append(row)
        citations.append(_citation(primary_source, field="permit_family", value="REQUIRED", permit_family=family))
    conditional_rows = []
    for family in value.get("conditional_families") or []:
        canonical_family = _canonical_family(family)
        conditional_rows.append({
            "permit_family": family,
            "filing_family": canonical_family,
            "family": canonical_family,
            "category": canonical_family,
            "kind": family,
            "permit_name": family,
            "status": "CONDITIONAL",
            "required_status": "CONDITIONAL",
            "required": None,
            "decision": "CONDITIONAL",
            "applying_office": value["authority"],
            "source_url": primary_source["url"],
            "required_if": value.get("authority_note") or "The controlling condition applies.",
        })
    apply_url = str(value.get("apply_url") or primary_source["url"])
    maps_url = "https://www.google.com/maps/search/?api=1&query=" + quote_plus(str(value["maps_destination"]))
    primary_family = (value.get("required_families") or value.get("conditional_families") or ["Permit requirement"])[0]
    if decision == "REQUIRED":
        headline = "Permit approval is required for the stated scope."
        next_step = f"Start with {value['authority']} and file the listed permit package before work begins."
    elif decision == "NOT_REQUIRED":
        headline = "No permit is required for the exact stated scope."
        next_step = "Keep the stated scope unchanged; new structural, trade, occupancy, or site work requires a new check."
    else:
        headline = "The permit outcome depends on the stated controlling condition."
        next_step = f"Confirm the listed condition with {value['authority']} before work begins."
    manifest = {
        "schema_version": "permitassist.permit-manifest.v2",
        "contract_sha256": value["contract_sha256"],
        "coverage_outcome": "SUPPORTED",
        "jurisdiction": {"city": value["city"], "state": value["state"], "authority": value["authority"]},
        "decision": decision,
        "primary_family": primary_family,
        "required_families": copy.deepcopy(value.get("required_families") or []),
        "conditional_families": copy.deepcopy(value.get("conditional_families") or []),
        "prohibited_hard_required_families": copy.deepcopy(value.get("prohibited_hard_required_families") or []),
        "official_evidence": copy.deepcopy(citations),
        "apply_route": {"url": apply_url, "office": value["authority"], "maps_url": maps_url},
        "evidence_freshness": value["evidence_freshness"],
    }
    return {
        "coverage_outcome": "SUPPORTED",
        "coverage_contract_sha256": value["contract_sha256"],
        "permit_decision": decision,
        "permit_required": True if decision == "REQUIRED" else False if decision == "NOT_REQUIRED" else None,
        "permit_verdict": decision,
        "permit_kind": primary_family,
        "permit_type": primary_family,
        "permits_required": required_rows,
        "conditional_permits": conditional_rows,
        "related_permits": [],
        "required_permit_families": copy.deepcopy(value.get("required_families") or []),
        "applying_office": value["authority"],
        "apply_url": apply_url,
        "online_application_url": apply_url,
        "apply_path": {"support_level": "verified official route", "url": apply_url, "maps_url": maps_url, "office": value["authority"]},
        "maps_url": maps_url,
        "sources": sources,
        "source_urls": [source["url"] for source in sources],
        "claim_citations": citations,
        "customer_headline": headline,
        "customer_next_step": next_step,
        "summary": headline + " " + next_step,
        "authority_note": value.get("authority_note") or "",
        "permit_manifest": manifest,
    }
