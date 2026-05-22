"""PermitAssist 3.0 exact permit/application/category retrieval engine.

This module is intentionally deterministic for Phase 0/1: it proves the product
contract against a controlled 50-AHJ launch corpus and fails closed to a
structured completion ticket whenever exact names/categories are missing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
from urllib.parse import urlparse
import uuid
from typing import Any, Iterable

API_DIR = Path(__file__).resolve().parent
REPO_ROOT = API_DIR.parent
DEFAULT_CORPUS_PATH = REPO_ROOT / "data" / "permitassist3" / "launch_corpus.json"
DEFAULT_WRITEBACK_PATH = REPO_ROOT / "data" / "permitassist3" / "writeback_corpus.jsonl"
DEFAULT_TICKET_PATH = REPO_ROOT / "data" / "permitassist3" / "completion_tickets.jsonl"

FINAL_VERIFIED = "FINAL_VERIFIED_EXACT_NAME_PACKET"
NON_FINAL = "NON_FINAL_COMPLETION_REQUIRED"

FORBIDDEN_FINAL_PATTERNS = (
    re.compile(r"Manual filing path confirmation in progress", re.I),
    re.compile(r"exact permit type needs AHJ verification", re.I),
    re.compile(r"name not source-confirmed", re.I),
    re.compile(r"\bPermit required\s*(?:[—\-:·]|$)", re.I),
    re.compile(r"check\s+with\s+(?:the\s+)?(?:local\s+)?AHJ", re.I),
    re.compile(r"needs_manual_filing_path_confirmation", re.I),
)

COMMERCIAL_TI_VERTICALS = {"restaurant_ti", "medical_clinic_ti", "office_ti"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def normalize_ahj_id(city: str, state: str) -> str:
    city_clean = re.sub(r"^(city|town|county) of\s+", "", normalize_text(city), flags=re.I)
    return f"{str(state or '').lower()}_{normalize_slug(city_clean)}"


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def contains_forbidden_final_string(value: Any) -> bool:
    text = json.dumps(value, sort_keys=True, default=str) if not isinstance(value, str) else value
    return any(pattern.search(text) for pattern in FORBIDDEN_FINAL_PATTERNS)


@dataclass(frozen=True)
class DecomposedJob:
    vertical: str
    work_units: list[str]
    trades: list[str]
    permit_families: list[str]
    possible_companions: list[str]
    occupancy: str
    commercial: bool


class JobDecomposer:
    """Natural-language scope -> work units/trades/permit families."""

    def decompose(self, job_type: str, explicit_vertical: str | None = None) -> DecomposedJob:
        text = normalize_text(job_type).lower()
        vertical = explicit_vertical or self._infer_vertical(text)
        commercial = vertical in COMMERCIAL_TI_VERTICALS or "commercial" in text or "tenant" in text or "office" in text
        work_units: list[str] = []
        trades: list[str] = []
        companions: list[str] = []

        if vertical in COMMERCIAL_TI_VERTICALS or "tenant improvement" in text or "ti" in text:
            work_units.append("commercial_tenant_improvement")
            trades.append("building")
            companions.extend(["zoning_or_use_review", "certificate_of_occupancy_or_change_of_use"])
        if vertical == "restaurant_ti" or any(token in text for token in ["restaurant", "kitchen", "hood", "grease", "food"]):
            vertical = "restaurant_ti"
            work_units.extend(["commercial_kitchen", "food_service"])
            trades.extend(["mechanical", "plumbing", "electrical", "fire"])
            companions.extend(["health_food_establishment", "grease_fog", "hood_suppression", "fire_review"])
        if vertical == "medical_clinic_ti" or any(token in text for token in ["clinic", "medical", "dental", "healthcare"]):
            vertical = "medical_clinic_ti"
            work_units.append("clinical_tenant_improvement")
            trades.extend(["building", "mechanical", "electrical", "plumbing"])
            companions.extend(["healthcare_state_overlay", "accessibility_review", "life_safety_review"])
        if vertical == "office_ti" or "office" in text:
            vertical = "office_ti"
            work_units.append("office_tenant_improvement")
        for key, trade in (("electrical", "electrical"), ("panel", "electrical"), ("service", "electrical"), ("plumb", "plumbing"), ("mechanical", "mechanical"), ("hvac", "mechanical"), ("solar", "solar_pv"), ("battery", "battery")):
            if key in text and trade not in trades:
                trades.append(trade)
        families = UniversalFamilyMapper().families_for(vertical, trades)
        return DecomposedJob(
            vertical=vertical,
            work_units=sorted(set(work_units)) or [vertical],
            trades=sorted(set(trades)) or (["building"] if commercial else []),
            permit_families=families,
            possible_companions=sorted(set(companions)),
            occupancy="commercial" if commercial else "unknown",
            commercial=commercial,
        )

    def _infer_vertical(self, text: str) -> str:
        if any(token in text for token in ["restaurant", "hood", "grease", "food service", "commercial kitchen"]):
            return "restaurant_ti"
        if any(token in text for token in ["medical", "clinic", "dental", "healthcare"]):
            return "medical_clinic_ti"
        if "office" in text:
            return "office_ti"
        if "solar" in text or "pv" in text or "battery" in text:
            return "solar_pv_battery"
        if "electrical" in text or "panel" in text or "service" in text:
            return "commercial_electrical"
        if "mechanical" in text or "hvac" in text:
            return "commercial_mechanical"
        if "plumb" in text:
            return "commercial_plumbing"
        return "office_ti" if "commercial" in text or "tenant" in text else "general"


class UniversalFamilyMapper:
    def families_for(self, vertical: str, trades: Iterable[str] = ()) -> list[str]:
        families: list[str] = []
        if vertical in COMMERCIAL_TI_VERTICALS:
            families.extend(["building_tenant_improvement", "certificate_of_occupancy_or_change_of_use"])
        if vertical == "restaurant_ti":
            families.extend(["electrical", "plumbing", "mechanical", "fire_suppression", "health_food_establishment", "grease_fog"])
        if vertical == "medical_clinic_ti":
            families.extend(["electrical", "plumbing", "mechanical", "life_safety", "healthcare_state_overlay"])
        for trade in trades:
            if trade == "electrical":
                families.append("electrical")
            elif trade == "plumbing":
                families.append("plumbing")
            elif trade == "mechanical":
                families.append("mechanical")
            elif trade == "solar_pv":
                families.extend(["solar_pv", "electrical"])
            elif trade == "battery":
                families.extend(["battery_energy_storage", "electrical", "fire_review"])
        if not families and vertical.startswith("commercial_"):
            families.append(vertical)
        return list(dict.fromkeys(families or ["building_tenant_improvement"]))


class StateOverlay:
    OVERLAYS = {
        "CA": ["Title 24 energy documentation when triggered", "CALGreen/accessibility review when triggered"],
        "FL": ["Florida Building Code", "DBPR/AHCA/health/fire overlays when triggered"],
        "TX": ["local adopted code/TDLR accessibility when triggered"],
        "AZ": ["local code and utility/service coordination when triggered"],
        "IL": ["local building/fire/health overlays when triggered"],
        "NY": ["NYC/State code and DOB/fire/health overlays when triggered"],
        "NJ": ["Uniform Construction Code and local subcode officials when triggered"],
        "MA": ["Massachusetts State Building Code and local inspectional services"],
        "WA": ["state energy code and local permit authority overlays"],
        "OR": ["Oregon specialty code and local building authority overlays"],
        "CO": ["local adopted code and development review overlays"],
        "NC": ["state building code and county/city inspection authority overlays"],
        "GA": ["Georgia state minimum codes and local AHJ overlays"],
        "PA": ["UCC/local permitting overlays"],
        "MN": ["Minnesota building code/local inspection authority overlays"],
    }

    def apply(self, state: str, packet: dict[str, Any]) -> dict[str, Any]:
        packet["state_overlays"] = self.OVERLAYS.get(str(state or "").upper(), [])
        return packet


@dataclass
class RetrievalResult:
    status: str
    ahj_profile: dict[str, Any] | None
    records: list[dict[str, Any]] = field(default_factory=list)
    missing_families: list[str] = field(default_factory=list)


class AHJCapabilityRegistry:
    def __init__(self, corpus_path: Path | str | None = None):
        self.corpus_path = Path(corpus_path or os.environ.get("PERMITASSIST3_CORPUS_PATH") or DEFAULT_CORPUS_PATH)
        self._data = json.loads(self.corpus_path.read_text(encoding="utf-8"))
        self.registry = {row["ahj_id"]: row for row in self._data.get("ahj_registry", [])}
        self.records = list(self._data.get("exact_name_records", []))
        self.by_city_state = {(normalize_slug(row.get("city")), str(row.get("state", "")).upper()): row for row in self.registry.values()}

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self._data.get("metadata") or {})

    def resolve(self, city: str, state: str) -> dict[str, Any] | None:
        state_norm = str(state or "").upper()
        direct = self.registry.get(normalize_ahj_id(city, state_norm))
        if direct:
            return direct
        key = (normalize_slug(city), state_norm)
        if key in self.by_city_state:
            return self.by_city_state[key]
        city_tokens = set(normalize_slug(city).split("_"))
        for profile in self.registry.values():
            if str(profile.get("state", "")).upper() != state_norm:
                continue
            if city_tokens and city_tokens <= set(normalize_slug(profile.get("city")).split("_")):
                return profile
        return None

    def records_for(self, ahj_id: str, vertical: str) -> list[dict[str, Any]]:
        return [row for row in self.records if row.get("ahj_id") == ahj_id and row.get("vertical") == vertical]


class AHJResolver:
    def __init__(self, registry: AHJCapabilityRegistry | None = None):
        self.registry = registry or AHJCapabilityRegistry()

    def resolve(self, city: str, state: str) -> dict[str, Any] | None:
        return self.registry.resolve(city, state)


class OfficialSourceFilter:
    @staticmethod
    def _host(url: str) -> str:
        return urlparse(url).netloc.lower().removeprefix("www.")

    def official_enough(self, record: dict[str, Any], ahj_profile: dict[str, Any] | None = None) -> bool:
        url = normalize_text(record.get("source_url") or record.get("final_url"))
        if not url.startswith("http"):
            return False
        # Non-.gov official portals are allowed only because they came through prior PermitAssist evidence promotion.
        if not record.get("source_artifact_family"):
            return False
        if ahj_profile:
            source_host = self._host(url)
            official_hosts = {self._host(candidate) for candidate in ahj_profile.get("official_urls", []) if candidate}
            if official_hosts and source_host not in official_hosts:
                return False
        return True


class Verifier:
    def __init__(self, source_filter: OfficialSourceFilter | None = None):
        self.source_filter = source_filter or OfficialSourceFilter()

    def verify_record(self, record: dict[str, Any], ahj_profile: dict[str, Any] | None = None) -> bool:
        name = normalize_text(record.get("exact_name_or_category"))
        snippet = normalize_text(record.get("exact_quote_or_snippet"))
        source_hash = normalize_text(record.get("source_content_hash_sha256"))
        if not name or contains_forbidden_final_string(name):
            return False
        if not self.source_filter.official_enough(record, ahj_profile):
            return False
        if len(source_hash) != 64:
            return False
        # Exact-name records must quote the literal local name/category. Category-path records may be a promoted
        # official portal/category row when the local source snippet supports the category/path but not as a title.
        if name.lower() in snippet.lower():
            return True
        return record.get("verification_status") == "verified_category_path" and bool(snippet)


class RetrievalRouter:
    def __init__(self, registry: AHJCapabilityRegistry | None = None, verifier: Verifier | None = None):
        self.registry = registry or AHJCapabilityRegistry()
        self.verifier = verifier or Verifier()

    def retrieve(self, ahj_profile: dict[str, Any] | None, job: DecomposedJob) -> RetrievalResult:
        if not ahj_profile:
            return RetrievalResult("ahj_not_in_launch_registry", None, [], job.permit_families)
        if ahj_profile.get("profile_only"):
            return RetrievalResult("profile_only_non_final", ahj_profile, [], job.permit_families)
        candidates = self.registry.records_for(ahj_profile["ahj_id"], job.vertical)
        verified = [record for record in candidates if self.verifier.verify_record(record, ahj_profile)]
        if not verified:
            return RetrievalResult("no_verified_exact_name", ahj_profile, [], job.permit_families)
        return RetrievalResult("verified", ahj_profile, verified, [])


class MultiPermitPacketBuilder:
    def build(self, retrieval: RetrievalResult, job: DecomposedJob, city: str, state: str) -> dict[str, Any]:
        primary_names = []
        permits = []
        for record in retrieval.records:
            name = record["exact_name_or_category"]
            if name not in primary_names:
                primary_names.append(name)
                permits.append({
                    "permit_name_or_portal_category": name,
                    "permit_family": record.get("permit_family") or "building_tenant_improvement",
                    "required": True,
                    "source_url": record.get("source_url"),
                    "source_title": record.get("source_title"),
                    "last_verified_utc": record.get("last_verified_utc"),
                })
        # Commercial packets should expose likely companion tracks even when the exact local companion names remain missing.
        companions = []
        for family in job.permit_families:
            if permits and family == permits[0].get("permit_family"):
                continue
            if family not in {p.get("permit_family") for p in permits}:
                companions.append({
                    "permit_family": family,
                    "status": "companion_track_identified_exact_local_name_pending" if retrieval.status != "verified" else "companion_track_verify_local_trigger_before_filing",
                })
        packet = {
            "city": city,
            "state": state,
            "ahj_id": retrieval.ahj_profile.get("ahj_id") if retrieval.ahj_profile else normalize_ahj_id(city, state),
            "vertical": job.vertical,
            "work_units": job.work_units,
            "trades": job.trades,
            "permit_families": job.permit_families,
            "permit_names_or_categories": primary_names,
            "permits_required": permits,
            "companion_permits_reviews": companions,
            "filing_path": permits[0]["permit_name_or_portal_category"] if permits else None,
            "source_evidence": [
                {
                    "permit_name_or_category": r.get("exact_name_or_category"),
                    "source_url": r.get("source_url"),
                    "source_title": r.get("source_title"),
                    "exact_quote_or_snippet": r.get("exact_quote_or_snippet"),
                    "retrieved_at_utc": r.get("retrieved_at_utc"),
                    "source_content_hash_sha256": r.get("source_content_hash_sha256"),
                }
                for r in retrieval.records
            ],
            "freshness": {
                "last_verified_utc": min([str(r.get("last_verified_utc")) for r in retrieval.records if r.get("last_verified_utc")], default=None),
                "stale_after_utc": min([str(r.get("stale_after_utc")) for r in retrieval.records if r.get("stale_after_utc")], default=None),
            },
        }
        return StateOverlay().apply(state, packet)


class WriteBackCorpus:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or os.environ.get("PERMITASSIST3_WRITEBACK_PATH") or DEFAULT_WRITEBACK_PATH)

    def append_solution(self, packet: dict[str, Any], *, eval_mode: bool = False) -> bool:
        if eval_mode or not packet.get("permit_names_or_categories"):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = {"written_at_utc": utc_now_iso(), "packet_hash": stable_hash(packet), "packet": packet}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
        return True

    def hash(self) -> str:
        if not self.path.exists():
            return "0" * 64
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


class CompletionTicketQueue:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or os.environ.get("PERMITASSIST3_TICKET_PATH") or DEFAULT_TICKET_PATH)

    def create(self, city: str, state: str, job_type: str, job: DecomposedJob, retrieval: RetrievalResult, *, eval_mode: bool = False) -> dict[str, Any]:
        ticket = {
            "ticket_id": f"pa3_{uuid.uuid4().hex[:12]}",
            "status": "open",
            "final_answer_state": NON_FINAL,
            "created_at_utc": utc_now_iso(),
            "sla_hours": 24,
            "city": city,
            "state": state,
            "ahj_id": retrieval.ahj_profile.get("ahj_id") if retrieval.ahj_profile else normalize_ahj_id(city, state),
            "job_type": job_type,
            "vertical": job.vertical,
            "missing_exact_names_for_families": retrieval.missing_families or job.permit_families,
            "retrieval_status": retrieval.status,
            "candidate_official_urls": (retrieval.ahj_profile or {}).get("official_urls", []),
            "writeback_required_on_resolution": True,
        }
        if not eval_mode:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(ticket, sort_keys=True) + "\n")
        return ticket

    def contains(self, ticket_id: str) -> bool:
        if not self.path.exists():
            return False
        return any(ticket_id in line for line in self.path.read_text(encoding="utf-8").splitlines())


class FinalAnswerGate:
    def validate(self, packet: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        names = packet.get("permit_names_or_categories") or []
        if not names:
            reasons.append("missing_exact_permit_application_form_name_or_portal_category")
        if contains_forbidden_final_string(packet):
            reasons.append("forbidden_final_fallback_string_present")
        for ev in packet.get("source_evidence") or []:
            if not ev.get("source_url") or not ev.get("source_content_hash_sha256") or not ev.get("retrieved_at_utc"):
                reasons.append("source_evidence_missing_url_hash_or_retrieved_at")
                break
        if not packet.get("permits_required"):
            reasons.append("missing_required_permit_packet")
        return not reasons, reasons


class PermitAssist3ExactNameEngine:
    def __init__(self, corpus_path: Path | str | None = None, ticket_path: Path | str | None = None, writeback_path: Path | str | None = None):
        self.registry = AHJCapabilityRegistry(corpus_path)
        self.resolver = AHJResolver(self.registry)
        self.router = RetrievalRouter(self.registry)
        self.builder = MultiPermitPacketBuilder()
        self.gate = FinalAnswerGate()
        self.tickets = CompletionTicketQueue(ticket_path)
        self.writeback = WriteBackCorpus(writeback_path)

    def lookup(
        self,
        job_type: str,
        city: str,
        state: str,
        *,
        explicit_vertical: str | None = None,
        eval_mode: bool = False,
        persist_tickets: bool | None = None,
        writeback_verified: bool = False,
    ) -> dict[str, Any]:
        job = JobDecomposer().decompose(job_type, explicit_vertical)
        profile = self.resolver.resolve(city, state)
        retrieval = self.router.retrieve(profile, job)
        packet = self.builder.build(retrieval, job, city, state)
        ok, reasons = self.gate.validate(packet)
        if ok:
            if writeback_verified:
                self.writeback.append_solution(packet, eval_mode=eval_mode)
            packet.update({
                "final_answer_state": FINAL_VERIFIED,
                "permit_required": True,
                "permit_verdict": "YES",
                "completion_ticket": None,
                "final_answer_gate_reasons": [],
                "retrieval_status": retrieval.status,
            })
            return packet
        ticket_eval_mode = eval_mode if persist_tickets is None else not persist_tickets
        ticket = self.tickets.create(city, state, job_type, job, retrieval, eval_mode=ticket_eval_mode)
        return {
            "final_answer_state": NON_FINAL,
            "permit_required": None,
            "permit_verdict": "NON_FINAL",
            "city": city,
            "state": state,
            "ahj_id": ticket["ahj_id"],
            "vertical": job.vertical,
            "work_units": job.work_units,
            "permit_families": job.permit_families,
            "permit_names_or_categories": [],
            "permits_required": [],
            "source_evidence": [],
            "completion_ticket": ticket,
            "final_answer_gate_reasons": reasons,
            "retrieval_status": retrieval.status,
        }


def lookup_exact_name_packet(
    job_type: str,
    city: str,
    state: str,
    *,
    explicit_vertical: str | None = None,
    eval_mode: bool = False,
    persist_tickets: bool | None = None,
    writeback_verified: bool = False,
) -> dict[str, Any]:
    return PermitAssist3ExactNameEngine().lookup(
        job_type,
        city,
        state,
        explicit_vertical=explicit_vertical,
        eval_mode=eval_mode,
        persist_tickets=persist_tickets,
        writeback_verified=writeback_verified,
    )


def apply_permitassist3_contract(
    result: dict[str, Any],
    job_type: str,
    city: str,
    state: str,
    *,
    explicit_vertical: str | None = None,
    eval_mode: bool = False,
) -> dict[str, Any]:
    """Overlay legacy API result with the PA3 final/non-final contract.

    If PA3 has a verified exact/category packet, promote it. If not, prevent the
    legacy placeholder/fallback path from masquerading as a final answer.
    """
    if not isinstance(result, dict):
        return result
    try:
        packet = lookup_exact_name_packet(
            job_type,
            city,
            state,
            explicit_vertical=explicit_vertical,
            eval_mode=eval_mode,
            persist_tickets=not eval_mode,
            writeback_verified=False,
        )
    except Exception as exc:  # fail closed to non-final; never invent a final name
        packet = {
            "final_answer_state": NON_FINAL,
            "permit_verdict": "NON_FINAL",
            "completion_ticket": {"ticket_id": f"pa3_runtime_{uuid.uuid4().hex[:10]}", "status": "open", "sla_hours": 24, "error": str(exc)[:200]},
            "permit_names_or_categories": [],
            "permits_required": [],
            "source_evidence": [],
            "final_answer_gate_reasons": ["permitassist3_runtime_error"],
        }
    result["permitassist3"] = packet
    if packet.get("final_answer_state") == FINAL_VERIFIED:
        names = packet.get("permit_names_or_categories") or []
        primary = names[0]
        result.update({
            "permit_verdict": "YES",
            "permit_required": True,
            "permit_type": primary,
            "permit_name": primary,
            "_permit_display_name": primary,
            "permit_type_verified": True,
            "permit_name_verified": True,
            "permit_name_status": "exact_permitassist3_verified",
            "permit_name_confidence": "high",
            "permit_name_source_field": "permitassist3_exact_name_engine",
            "permits_required": packet.get("permits_required") or [{"permit_type": primary, "required": True}],
            "companion_reviews_triggers": packet.get("companion_permits_reviews", []),
            "claim_citations": packet.get("source_evidence", []),
            "sources": [ev for ev in packet.get("source_evidence", []) if ev.get("source_url")],
            "final_answer_state": FINAL_VERIFIED,
            "completion_ticket": None,
        })
        return result

    # Non-final structured state. Do not leave legacy fallback names in final slots.
    result.update({
        "permit_verdict": "NON_FINAL",
        "permit_required": None,
        "permit_type": None,
        "permit_name": None,
        "_permit_display_name": None,
        "permit_type_verified": False,
        "permit_name_verified": False,
        "permit_name_status": "non_final_exact_name_completion_required",
        "permit_name_confidence": "unknown",
        "permits_required": [],
        "final_answer_state": NON_FINAL,
        "completion_ticket": packet.get("completion_ticket"),
        "final_answer_gate_reasons": packet.get("final_answer_gate_reasons", []),
    })
    return result


__all__ = [
    "FINAL_VERIFIED",
    "NON_FINAL",
    "JobDecomposer",
    "UniversalFamilyMapper",
    "StateOverlay",
    "AHJCapabilityRegistry",
    "AHJResolver",
    "RetrievalRouter",
    "OfficialSourceFilter",
    "Verifier",
    "MultiPermitPacketBuilder",
    "WriteBackCorpus",
    "CompletionTicketQueue",
    "FinalAnswerGate",
    "PermitAssist3ExactNameEngine",
    "apply_permitassist3_contract",
    "contains_forbidden_final_string",
    "lookup_exact_name_packet",
]
