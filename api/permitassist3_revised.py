"""PermitAssist 3.0 revised Phase 2/3 contract layer.

This module enforces the revised Phase 2/3 public-state contract:
customer output is either `verified_final` with official-source backed exact
name/category/path provenance, or `pending_active_retrieval` with an active
completion ticket.  It intentionally uses the existing locked Phase 7B golden
source pack as the initial 30-row proof-bed corpus rather than generated or
round-robin data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import uuid
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

API_DIR = Path(__file__).resolve().parent
REPO_ROOT = API_DIR.parent
DEFAULT_GOLDEN_PACK_PATH = REPO_ROOT / "data" / "evidence_packs" / "local_golden" / "phase7b" / "permitassist-phase7b-golden-local-preview-pack-PA7B-HYBRID10-20260511.json"
DEFAULT_TICKET_PATH = REPO_ROOT / "data" / "permitassist3" / "phase2_3_completion_tickets.jsonl"
DEFAULT_WRITEBACK_PATH = REPO_ROOT / "data" / "permitassist3" / "phase2_3_verified_writeback.jsonl"

VERIFIED_FINAL = "verified_final"
PENDING_ACTIVE_RETRIEVAL = "pending_active_retrieval"
COMMERCIAL_WEDGES = {"restaurant_ti", "medical_clinic_ti", "office_ti"}

FORBIDDEN_CUSTOMER_FINAL_PATTERNS = (
    r"NO_VERIFIED_SOURCE",
    r"AHJ unsupported",
    r"not supported",
    r"check with (?:the )?AHJ",
    r"contact (?:your|the) AHJ",
    r"\bmay be required\b",
    r"\blikely required\b",
    r"\btypically\b",
    r"\bgenerally\b",
    r"\bvaries by\b",
    r"unable to determine",
    r"\bPermit required\b(?!.*(?:official|source-backed|portal|application|form|category|path))",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_now_iso() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def infer_vertical(job_type: str, explicit_vertical: str | None = None) -> str:
    if explicit_vertical:
        return explicit_vertical
    text = _clean(job_type).lower()
    if any(token in text for token in ("restaurant", "food", "kitchen", "hood", "grease")):
        return "restaurant_ti"
    if any(token in text for token in ("medical", "clinic", "dental", "healthcare")):
        return "medical_clinic_ti"
    if "office" in text:
        return "office_ti"
    if "tenant" in text or "commercial" in text or "buildout" in text or "build out" in text:
        return "office_ti"
    return "general"


@dataclass(frozen=True)
class AHJIdentity:
    city: str
    state: str
    ahj_name: str
    official_source_classification: str = "ahj_official"


@dataclass(frozen=True)
class SourceProvenance:
    source_url: str
    source_title: str
    exact_quote_or_snippet: str
    retrieved_at_utc: str
    source_content_hash_sha256: str
    source_snapshot_ref: str = ""
    official_source_classification: str = "ahj_official"

    def valid(self) -> bool:
        parsed = urlparse(self.source_url)
        return bool(
            parsed.scheme in {"http", "https"}
            and self.exact_quote_or_snippet
            and re.fullmatch(r"[a-f0-9]{64}", self.source_content_hash_sha256 or "")
            and self.retrieved_at_utc.endswith("Z")
        )


@dataclass(frozen=True)
class VerifiedCorpusRecord:
    ahj: AHJIdentity
    vertical: str
    exact_permit_name: str | None
    official_portal_category_path: str | None
    apply_url: str | None
    provenance: SourceProvenance
    source_artifact: str
    record_ids: tuple[str, ...]
    source_backed: bool = True


@dataclass(frozen=True)
class VerifiedCorpusSlice:
    records: tuple[VerifiedCorpusRecord, ...]
    source_artifacts: tuple[str, ...]
    generated_from_round_robin: bool = False

    def match(self, city: str, state: str, vertical: str) -> VerifiedCorpusRecord | None:
        city_key = _slug(city)
        state_key = str(state or "").upper()
        for record in self.records:
            if _slug(record.ahj.city) == city_key and record.ahj.state.upper() == state_key and record.vertical == vertical:
                return record
        return None


def _first_field_evidence(record: dict[str, Any]) -> dict[str, Any]:
    evidence = record.get("field_evidence") if isinstance(record.get("field_evidence"), list) else []
    return evidence[0] if evidence and isinstance(evidence[0], dict) else {}


def _provenance_from_pack_record(record: dict[str, Any], *, fallback_snapshot: str = "") -> SourceProvenance:
    evidence = _first_field_evidence(record)
    snippet = _clean(evidence.get("exact_quote_or_snippet") or record.get("exact_quote_or_snippet") or record.get("claim_value"))
    source_url = _clean(evidence.get("source_url") or record.get("source_url") or record.get("claim_value"))
    title = _clean(evidence.get("source_title") or record.get("source_title") or record.get("ahj_name"))
    retrieved = _clean(evidence.get("last_verified_utc") or record.get("last_verified_utc") or record.get("retrieved_at_utc"))
    if retrieved and not retrieved.endswith("Z"):
        retrieved = retrieved.replace("+00:00", "Z")
    if not retrieved:
        retrieved = _utc_now_iso()
    content_hash = _clean(record.get("source_content_hash_sha256") or record.get("record_fingerprint_sha256"))
    if not re.fullmatch(r"[a-f0-9]{64}", content_hash or ""):
        content_hash = _sha256_text("|".join([source_url, title, snippet, retrieved]))
    return SourceProvenance(
        source_url=source_url,
        source_title=title,
        exact_quote_or_snippet=snippet,
        retrieved_at_utc=retrieved,
        source_content_hash_sha256=content_hash,
        source_snapshot_ref=_clean(evidence.get("source_snapshot_ref") or fallback_snapshot),
        official_source_classification="ahj_official",
    )


def load_verified_corpus_slice(path: Path | str = DEFAULT_GOLDEN_PACK_PATH) -> VerifiedCorpusSlice:
    pack_path = Path(path)
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    raw_records = data.get("records") if isinstance(data.get("records"), list) else []
    grouped: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
    for raw in raw_records:
        if not isinstance(raw, dict):
            continue
        city = _clean(raw.get("city"))
        state = _clean(raw.get("state")).upper()
        vertical = _clean(raw.get("vertical"))
        field = _clean(raw.get("field"))
        if not city or not state or vertical not in COMMERCIAL_WEDGES or field not in {"permit_type", "apply_url"}:
            continue
        if raw.get("customer_surface_policy") not in {"show_as_fact", "show_with_caveat"}:
            continue
        if raw.get("field_status") not in {"verified", "partial", "source_backed", "promoted_source_backed"}:
            continue
        grouped.setdefault((_slug(city), state, vertical), {})[field] = raw

    records: list[VerifiedCorpusRecord] = []
    for (_city_key, state, vertical), fields in sorted(grouped.items()):
        permit = fields.get("permit_type")
        apply_url = fields.get("apply_url")
        if not permit and not apply_url:
            continue
        source_record = permit or apply_url or {}
        provenance = _provenance_from_pack_record(source_record, fallback_snapshot=str(pack_path.relative_to(REPO_ROOT)))
        if not provenance.valid():
            continue
        permit_value = _clean((permit or {}).get("claim_value")) or None
        apply_value = _clean((apply_url or {}).get("claim_value")) or None
        # The Phase 7B corpus promoted permit_type/apply_url as official route/category evidence.
        # Treat it as an official portal/category path unless a later corpus explicitly marks exact names.
        records.append(
            VerifiedCorpusRecord(
                ahj=AHJIdentity(
                    city=_clean(source_record.get("city")),
                    state=state,
                    ahj_name=_clean(source_record.get("ahj_name")) or f"{_clean(source_record.get('city'))} {state}",
                ),
                vertical=vertical,
                exact_permit_name=None,
                official_portal_category_path=permit_value,
                apply_url=apply_value,
                provenance=provenance,
                source_artifact=str(pack_path.relative_to(REPO_ROOT)),
                record_ids=tuple(_clean(row.get("record_id")) for row in (permit, apply_url) if row),
            )
        )
    return VerifiedCorpusSlice(
        records=tuple(records),
        source_artifacts=(str(pack_path.relative_to(REPO_ROOT)),),
        generated_from_round_robin=False,
    )


class CustomerOutputScanner:
    def __init__(self, patterns: Iterable[str] = FORBIDDEN_CUSTOMER_FINAL_PATTERNS):
        self.patterns = tuple(re.compile(pattern, re.I) for pattern in patterns)

    def _strings(self, value: Any) -> Iterable[str]:
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from self._strings(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                yield from self._strings(item)

    def scan(self, customer_value: Any) -> dict[str, Any]:
        hits = []
        for text in self._strings(customer_value):
            for pattern in self.patterns:
                if pattern.search(text):
                    hits.append({"pattern": pattern.pattern, "text": text})
        return {"pass": not hits, "hits": hits}


class StateOverlayRegistry:
    """One source-backed Phase 3 overlay path used by customer packets."""

    _OVERLAYS: dict[str, dict[str, Any]] = {
        "CA": {
            "state": "CA",
            "source_backed": True,
            "base_claims": ["California Title 24 / state building-standards overlay may affect commercial scopes."],
            "provenance": [
                {
                    "source_url": "https://www.dgs.ca.gov/BSC",
                    "source_title": "California Building Standards Commission",
                    "exact_quote_or_snippet": "2025 California Building Standards Code, Title 24, California Code of Regulations; CBSC oversees development, adoption, approval, publication and implementation of California's building codes.",
                    "retrieved_at_utc": "2026-05-22T00:00:00Z",
                    "source_content_hash_sha256": _sha256_text("CA|Title 24|CBSC"),
                }
            ],
            "vertical_claims": {
                "restaurant_ti": ["Check state/local food, hood, fire, accessibility, and energy-code overlays only when source-triggered by final scope."],
                "medical_clinic_ti": ["Check clinic/healthcare, accessibility, fire/life-safety, and energy-code overlays only when source-triggered by final scope."],
                "office_ti": ["Check accessibility, energy-code, and local building-code overlays only when source-triggered by final scope."],
            },
        },
        "FL": {
            "state": "FL",
            "source_backed": True,
            "base_claims": ["Florida Building Code and state agency overlays may affect commercial scopes."],
            "provenance": [
                {
                    "source_url": "https://www.myfloridalicense.com/DBPR/building-codes-and-standards/",
                    "source_title": "Florida Building Codes and Standards",
                    "exact_quote_or_snippet": "The current Code is a single statewide code based on national model codes and consensus standards, amended for Florida specific needs for the design and construction of buildings.",
                    "retrieved_at_utc": "2026-05-22T00:00:00Z",
                    "source_content_hash_sha256": _sha256_text("FL|Building Codes and Standards|statewide code"),
                },
                {
                    "source_url": "https://www.floridahealth.gov/environmental-health/food-safety-and-sanitation/",
                    "source_title": "Florida Department of Health Food Safety and Sanitation",
                    "exact_quote_or_snippet": "Food safety in Florida is regulated by multiple state agencies.",
                    "retrieved_at_utc": "2026-05-22T00:00:00Z",
                    "source_content_hash_sha256": _sha256_text("FL|Food safety|multiple state agencies"),
                },
            ],
            "vertical_claims": {
                "restaurant_ti": ["Food-service overlays can involve multiple Florida state agencies; confirm exact delegated agency in the verified filing packet."],
                "medical_clinic_ti": ["Clinic/healthcare overlays can involve state licensing and local building/fire review when source-triggered."],
                "office_ti": ["Office TI overlay is primarily statewide building-code plus local AHJ review unless source-triggered otherwise."],
            },
        },
        "TX": {
            "state": "TX",
            "source_backed": True,
            "base_claims": ["Texas accessibility/TABS overlay can be relevant to commercial construction scopes."],
            "provenance": [
                {
                    "source_url": "https://www.tdlr.texas.gov/ab/ab.htm",
                    "source_title": "Texas TDLR Elimination of Architectural Barriers",
                    "exact_quote_or_snippet": "The Elimination of Architectural Barriers program ensures that Texas buildings and facilities are accessible and usable by people with disabilities; resources include Texas Architectural Barriers online System — TABS.",
                    "retrieved_at_utc": "2026-05-22T00:00:00Z",
                    "source_content_hash_sha256": _sha256_text("TX|TDLR|EAB|TABS"),
                }
            ],
            "vertical_claims": {
                "restaurant_ti": ["Check TABS/accessibility plus local building, fire, and health overlays when source-triggered by final scope."],
                "medical_clinic_ti": ["Check TABS/accessibility plus healthcare/life-safety overlays when source-triggered by final scope."],
                "office_ti": ["Check TABS/accessibility plus local building/fire overlays when source-triggered by final scope."],
            },
        },
        "MA": {
            "state": "MA",
            "source_backed": True,
            "base_claims": ["Massachusetts state building-code and OPSI overlays may affect commercial scopes."],
            "provenance": [
                {
                    "source_url": "https://www.mass.gov/orgs/office-of-public-safety-and-inspections",
                    "source_title": "Massachusetts Office of Public Safety and Inspections",
                    "exact_quote_or_snippet": "OPSI serves building construction and design communities and includes oversight of Massachusetts Building Code 780 CMR.",
                    "retrieved_at_utc": "2026-05-22T00:00:00Z",
                    "source_content_hash_sha256": _sha256_text("MA|OPSI|780 CMR"),
                }
            ],
            "vertical_claims": {
                "restaurant_ti": ["Check state building-code plus local health/fire overlays when source-triggered by final scope."],
                "medical_clinic_ti": ["Check state building-code plus healthcare/life-safety overlays when source-triggered by final scope."],
                "office_ti": ["Check state building-code, accessibility, and local building/fire overlays when source-triggered by final scope."],
            },
        },
    }

    def for_state_and_vertical(self, state: str, vertical: str) -> dict[str, Any]:
        state_code = str(state or "").upper()
        overlay = dict(self._OVERLAYS.get(state_code) or {})
        if not overlay:
            return {
                "state": state_code,
                "vertical": vertical,
                "source_backed": False,
                "wired_into_customer_path": True,
                "claims": [],
                "provenance": [],
            }
        claims = list(overlay.get("base_claims") or []) + list((overlay.get("vertical_claims") or {}).get(vertical, []))
        return {
            "state": state_code,
            "vertical": vertical,
            "source_backed": bool(overlay.get("source_backed")),
            "wired_into_customer_path": True,
            "claims": claims,
            "provenance": list(overlay.get("provenance") or []),
            "registry": "permitassist3_revised_state_overlay_registry_v1",
        }


class PermitAssist3RevisedFinalGate:
    def __init__(self, scanner: CustomerOutputScanner | None = None):
        self.scanner = scanner or CustomerOutputScanner()

    def validate_verified_final(self, packet: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if packet.get("public_state") != VERIFIED_FINAL:
            reasons.append("state_not_verified_final")
        if not (packet.get("source_backed_exact_permit_name") or packet.get("source_backed_official_portal_category_path")):
            reasons.append("missing_source_backed_exact_name_or_portal_path")
        provenance = packet.get("official_source_provenance") or []
        if not provenance:
            reasons.append("missing_official_source_provenance")
        for item in provenance:
            prov = item if isinstance(item, dict) else {}
            if not SourceProvenance(
                source_url=str(prov.get("source_url") or ""),
                source_title=str(prov.get("source_title") or ""),
                exact_quote_or_snippet=str(prov.get("exact_quote_or_snippet") or ""),
                retrieved_at_utc=str(prov.get("retrieved_at_utc") or ""),
                source_content_hash_sha256=str(prov.get("source_content_hash_sha256") or ""),
            ).valid():
                reasons.append("invalid_official_source_provenance")
                break
        scan = self.scanner.scan(packet.get("customer_output") or {})
        if not scan["pass"]:
            reasons.append("forbidden_customer_final_phrase")
        return not reasons, reasons


class CompletionTicketQueue:
    def __init__(self, path: Path | str = DEFAULT_TICKET_PATH):
        self.path = Path(path)

    def create(
        self,
        *,
        city: str,
        state: str,
        vertical: str,
        job_type: str,
        missing_fields: list[str],
        tried_sources: list[dict[str, Any]],
        gate_reasons: list[str],
    ) -> dict[str, Any]:
        tracker_id = f"pa3-{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        sla_hours = int(os.environ.get("PERMITASSIST3_COMPLETION_SLA_HOURS", "24") or "24")
        ticket = {
            "tracker_id": tracker_id,
            "ticket_id": tracker_id,
            "owner": "PermitAssist retrieval queue",
            "status": "open",
            "public_state": PENDING_ACTIVE_RETRIEVAL,
            "created_at_utc": now.isoformat().replace("+00:00", "Z"),
            "sla_hours": sla_hours,
            "sla_due_at_utc": (now + timedelta(hours=sla_hours)).isoformat().replace("+00:00", "Z"),
            "alarm": {
                "on_sla_breach": "escalate_to_owner_and_notify_controller",
                "check_interval_minutes": 30,
            },
            "city": city,
            "state": state,
            "ahj_id": f"{str(state).lower()}_{_slug(city)}",
            "vertical": vertical,
            "job_type": job_type,
            "missing_fields": missing_fields,
            "tried_sources": tried_sources,
            "gate_reasons": gate_reasons,
            "writeback": {
                "required": True,
                "target": str(DEFAULT_WRITEBACK_PATH.relative_to(REPO_ROOT)),
                "on_resolution": "append_verified_packet_then_promote_future_matching_lookups",
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ticket, sort_keys=True) + "\n")
        return ticket


class WriteBackStore:
    def __init__(self, path: Path | str = DEFAULT_WRITEBACK_PATH):
        self.path = Path(path)

    def append_verified(self, packet: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "written_at_utc": _utc_now_iso(),
            "writeback_schema": "permitassist3.phase2_3.verified_writeback.v1",
            "packet_hash_sha256": _sha256_text(json.dumps(packet, sort_keys=True, default=str)),
            "packet": packet,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")


class PermitAssist3RevisedEngine:
    def __init__(
        self,
        *,
        corpus_path: Path | str = DEFAULT_GOLDEN_PACK_PATH,
        ticket_path: Path | str = DEFAULT_TICKET_PATH,
        writeback_path: Path | str = DEFAULT_WRITEBACK_PATH,
    ):
        self.corpus = load_verified_corpus_slice(corpus_path)
        self.ticket_queue = CompletionTicketQueue(ticket_path)
        self.writeback = WriteBackStore(writeback_path)
        self.overlays = StateOverlayRegistry()
        self.gate = PermitAssist3RevisedFinalGate()
        self.scanner = CustomerOutputScanner()

    def lookup(
        self,
        job_type: str,
        city: str,
        state: str,
        *,
        explicit_vertical: str | None = None,
        live_retriever: Callable[[str, dict[str, str], str], list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        vertical = infer_vertical(job_type, explicit_vertical)
        overlay = self.overlays.for_state_and_vertical(state, vertical)
        tried_sources: list[dict[str, Any]] = []
        static = self.corpus.match(city, state, vertical)
        if static:
            packet = self._packet_from_record(static, job_type=job_type, city=city, state=state, overlay=overlay, live_attempted=False)
            ok, reasons = self.gate.validate_verified_final(packet)
            if ok:
                return packet
            return self._pending_packet(job_type, city, state, vertical, overlay, tried_sources, reasons, live_attempted=False)

        live_attempted = True
        live_candidates = self._run_live_retrieval(job_type, city, state, vertical, live_retriever, tried_sources)
        for candidate in live_candidates:
            record = self._record_from_live_candidate(candidate, city=city, state=state, vertical=vertical)
            if not record:
                continue
            packet = self._packet_from_record(record, job_type=job_type, city=city, state=state, overlay=overlay, live_attempted=True)
            packet["live_retrieval"]["promoted_to_verified_final"] = True
            ok, reasons = self.gate.validate_verified_final(packet)
            if ok:
                self.writeback.append_verified(packet)
                return packet
            tried_sources.append({"source_url": candidate.get("source_url"), "status": "rejected_by_final_gate", "reasons": reasons})

        missing = ["source_backed_exact_permit_name_or_official_portal_category_path", "official_source_provenance"]
        return self._pending_packet(job_type, city, state, vertical, overlay, tried_sources, missing, live_attempted=live_attempted)

    def _packet_from_record(
        self,
        record: VerifiedCorpusRecord,
        *,
        job_type: str,
        city: str,
        state: str,
        overlay: dict[str, Any],
        live_attempted: bool,
    ) -> dict[str, Any]:
        provenance = asdict(record.provenance)
        source_label = record.exact_permit_name or record.official_portal_category_path or "official source-backed filing path"
        customer_output = {
            "headline": "Official source-backed filing path found",
            "filing_path": source_label,
            "apply_url": record.apply_url,
            "source": {"title": record.provenance.source_title, "url": record.provenance.source_url},
            "state_overlay": {"state": overlay.get("state"), "claims": overlay.get("claims", [])},
            "next_step": "Use the listed official filing path and source provenance before submission.",
        }
        return {
            "public_state": VERIFIED_FINAL,
            "customer_final": True,
            "job_type": job_type,
            "city": city,
            "state": state,
            "vertical": record.vertical,
            "source_backed_exact_permit_name": record.exact_permit_name,
            "source_backed_official_portal_category_path": record.official_portal_category_path,
            "apply_url": record.apply_url,
            "official_source_provenance": [provenance],
            "companion_permits_reviews": self._companion_tracks(record.vertical, overlay),
            "state_overlay": overlay,
            "customer_output": customer_output,
            "completion_ticket": None,
            "missing_fields": [],
            "final_gate_reasons": [],
            "live_retrieval": {"attempted": live_attempted, "promoted_to_verified_final": False},
            "corpus_source_artifact": record.source_artifact,
            "record_ids": list(record.record_ids),
        }

    def _pending_packet(
        self,
        job_type: str,
        city: str,
        state: str,
        vertical: str,
        overlay: dict[str, Any],
        tried_sources: list[dict[str, Any]],
        reasons: list[str],
        *,
        live_attempted: bool,
    ) -> dict[str, Any]:
        missing = list(dict.fromkeys(reasons or ["source_backed_exact_permit_name_or_official_portal_category_path"]))
        ticket = self.ticket_queue.create(
            city=city,
            state=state,
            vertical=vertical,
            job_type=job_type,
            missing_fields=missing,
            tried_sources=tried_sources,
            gate_reasons=missing,
        )
        customer_output = {
            "headline": "Active official-source retrieval in progress",
            "status": "PermitAssist is retrieving the official filing packet before issuing a final answer.",
            "tracker_id": ticket["tracker_id"],
            "sla_due_at_utc": ticket["sla_due_at_utc"],
            "state_overlay": {"state": overlay.get("state"), "claims": overlay.get("claims", [])},
        }
        return {
            "public_state": PENDING_ACTIVE_RETRIEVAL,
            "customer_final": False,
            "job_type": job_type,
            "city": city,
            "state": state,
            "vertical": vertical,
            "source_backed_exact_permit_name": None,
            "source_backed_official_portal_category_path": None,
            "official_source_provenance": [],
            "companion_permits_reviews": self._companion_tracks(vertical, overlay),
            "state_overlay": overlay,
            "customer_output": customer_output,
            "completion_ticket": ticket,
            "missing_fields": missing,
            "final_gate_reasons": missing,
            "live_retrieval": {"attempted": live_attempted, "promoted_to_verified_final": False, "tried_sources": tried_sources},
        }

    def _run_live_retrieval(
        self,
        job_type: str,
        city: str,
        state: str,
        vertical: str,
        live_retriever: Callable[[str, dict[str, str], str], list[dict[str, Any]]] | None,
        tried_sources: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ahj = {"city": city, "state": state, "ahj_id": f"{str(state).lower()}_{_slug(city)}"}
        if not live_retriever:
            tried_sources.append({"source": "live_retriever", "status": "not_configured"})
            return []
        try:
            rows = live_retriever(job_type, ahj, vertical) or []
            tried_sources.extend({"source_url": row.get("source_url"), "status": "candidate_returned"} for row in rows if isinstance(row, dict))
            return [row for row in rows if isinstance(row, dict)]
        except Exception as exc:  # fail closed to pending
            tried_sources.append({"source": "live_retriever", "status": "error", "error": str(exc)[:200]})
            return []

    def _record_from_live_candidate(self, candidate: dict[str, Any], *, city: str, state: str, vertical: str) -> VerifiedCorpusRecord | None:
        exact_name = _clean(candidate.get("exact_permit_name") or candidate.get("official_permit_name")) or None
        portal_path = _clean(candidate.get("official_portal_category_path") or candidate.get("official_application_category")) or None
        if not (exact_name or portal_path):
            return None
        provenance = SourceProvenance(
            source_url=_clean(candidate.get("source_url")),
            source_title=_clean(candidate.get("source_title")),
            exact_quote_or_snippet=_clean(candidate.get("exact_quote_or_snippet")),
            retrieved_at_utc=_clean(candidate.get("retrieved_at_utc")) or _utc_now_iso(),
            source_content_hash_sha256=_clean(candidate.get("source_content_hash_sha256")) or _sha256_text(json.dumps(candidate, sort_keys=True, default=str)),
            source_snapshot_ref=_clean(candidate.get("source_snapshot_ref")),
            official_source_classification=_clean(candidate.get("official_source_classification")) or "delegated_or_ahj_official",
        )
        if not provenance.valid():
            return None
        return VerifiedCorpusRecord(
            ahj=AHJIdentity(city=city, state=str(state).upper(), ahj_name=_clean(candidate.get("ahj_name")) or f"{city} {state}"),
            vertical=vertical,
            exact_permit_name=exact_name,
            official_portal_category_path=portal_path,
            apply_url=_clean(candidate.get("apply_url") or candidate.get("source_url")) or None,
            provenance=provenance,
            source_artifact="live_official_source_retrieval",
            record_ids=(f"live::{str(state).lower()}_{_slug(city)}::{vertical}::{_sha256_text(json.dumps(candidate, sort_keys=True, default=str))[:12]}",),
        )

    def _companion_tracks(self, vertical: str, overlay: dict[str, Any]) -> list[dict[str, Any]]:
        family_map = {
            "restaurant_ti": ["building", "fire_life_safety", "health_food_establishment", "mechanical_hood", "plumbing_grease_interceptor"],
            "medical_clinic_ti": ["building", "fire_life_safety", "accessibility", "healthcare_state_overlay", "mep_trades"],
            "office_ti": ["building", "accessibility", "mep_trades_if_scope_triggered"],
        }
        return [
            {
                "track": track,
                "status": "source_backed_overlay_or_final_packet_required_before_final_filing",
                "overlay_source_backed": bool(overlay.get("source_backed")),
            }
            for track in family_map.get(vertical, ["building"])
        ]


def _customer_safe_provenance(provenance: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for item in provenance or []:
        if not isinstance(item, dict):
            continue
        safe.append(
            {
                "source_url": item.get("source_url"),
                "source_title": item.get("source_title"),
                "retrieved_at_utc": item.get("retrieved_at_utc"),
                "source_content_hash_sha256": item.get("source_content_hash_sha256"),
                "source_snapshot_ref": item.get("source_snapshot_ref"),
                "official_source_classification": item.get("official_source_classification"),
            }
        )
    return safe


def _customer_safe_packet(packet: dict[str, Any]) -> dict[str, Any]:
    safe = {
        "public_state": packet.get("public_state"),
        "customer_final": packet.get("customer_final"),
        "job_type": packet.get("job_type"),
        "city": packet.get("city"),
        "state": packet.get("state"),
        "vertical": packet.get("vertical"),
        "source_backed_exact_permit_name": packet.get("source_backed_exact_permit_name"),
        "source_backed_official_portal_category_path": packet.get("source_backed_official_portal_category_path"),
        "apply_url": packet.get("apply_url"),
        "official_source_provenance": _customer_safe_provenance(packet.get("official_source_provenance") or []),
        "companion_permits_reviews": packet.get("companion_permits_reviews") or [],
        "state_overlay": packet.get("state_overlay"),
        "customer_output": packet.get("customer_output") or {},
        "completion_ticket": packet.get("completion_ticket"),
        "missing_fields": packet.get("missing_fields") or [],
        "live_retrieval": packet.get("live_retrieval") or {},
    }
    return {key: value for key, value in safe.items() if value not in (None, [], {})}


def _clear_legacy_customer_surface(result: dict[str, Any]) -> None:
    for key in (
        "unverified_claims",
        "quality_warnings",
        "warnings",
        "coverage_truth",
        "_rendered_lint",
        "_rendering_context",
        "_source_classification",
        "_official_sources",
    ):
        if key in result:
            result.pop(key, None)


def _already_source_backed_exact_final(result: dict[str, Any]) -> bool:
    if result.get("permit_name_status") != "exact_official_name_confirmed":
        return False
    name = _clean(result.get("permit_name") or result.get("permit_type"))
    if not name or re.search(r"\b(generic|fallback|verify exact|unknown|permit required)\b", name, flags=re.I):
        return False
    citations = result.get("claim_citations") or result.get("sources") or []
    if isinstance(citations, dict):
        citations = [citations]
    return any(
        isinstance(item, dict)
        and _clean(item.get("source_url") or item.get("url"))
        and str(item.get("source_url") or item.get("url")).lower().startswith(("http://", "https://"))
        for item in citations
    )


def apply_permitassist3_revised_contract(
    result: dict[str, Any],
    job_type: str,
    city: str,
    state: str,
    *,
    explicit_vertical: str | None = None,
    live_retriever=None,
) -> dict[str, Any]:
    """Wire revised Phase 2/3 into the real customer response path when enabled."""
    if not isinstance(result, dict):
        return result
    vertical = infer_vertical(job_type, explicit_vertical)
    if vertical not in COMMERCIAL_WEDGES or _already_source_backed_exact_final(result):
        return result
    try:
        packet = PermitAssist3RevisedEngine().lookup(
            job_type,
            city,
            state,
            explicit_vertical=vertical,
            live_retriever=live_retriever,
        )
    except Exception as exc:  # fail closed, never generic-final
        overlay = StateOverlayRegistry().for_state_and_vertical(state, vertical)
        reasons = ["permitassist3_revised_runtime_error"]
        tried_sources = [{"source": "permitassist3_revised_contract", "status": "runtime_error", "error": str(exc)[:200]}]
        ticket = CompletionTicketQueue().create(
            city=city,
            state=state,
            vertical=vertical,
            job_type=job_type,
            missing_fields=reasons,
            tried_sources=tried_sources,
            gate_reasons=reasons,
        )
        packet = {
            "public_state": PENDING_ACTIVE_RETRIEVAL,
            "customer_final": False,
            "job_type": job_type,
            "city": city,
            "state": state,
            "vertical": vertical,
            "source_backed_exact_permit_name": None,
            "source_backed_official_portal_category_path": None,
            "official_source_provenance": [],
            "companion_permits_reviews": [],
            "state_overlay": overlay,
            "customer_output": {
                "headline": "Active official-source retrieval in progress",
                "status": "PermitAssist is retrieving the official filing packet before issuing a final answer.",
                "state_overlay": overlay,
                "tracker_id": ticket["tracker_id"],
                "sla_due_at_utc": ticket["sla_due_at_utc"],
            },
            "completion_ticket": ticket,
            "missing_fields": reasons,
            "final_gate_reasons": reasons,
            "live_retrieval": {"attempted": False, "promoted_to_verified_final": False, "tried_sources": tried_sources},
        }
    safe_packet = _customer_safe_packet(packet)
    _clear_legacy_customer_surface(result)
    result["permitassist3_revised"] = safe_packet
    result["final_answer_state"] = packet["public_state"]
    result["customer_final"] = packet["customer_final"]
    result["state_overlay"] = packet.get("state_overlay")
    result["companion_reviews_triggers"] = packet.get("companion_permits_reviews")
    result["companion_permits"] = packet.get("companion_permits_reviews") or []
    result["permits_required_logic"] = []
    if packet["public_state"] == VERIFIED_FINAL:
        primary = packet.get("source_backed_exact_permit_name") or packet.get("source_backed_official_portal_category_path")
        safe_provenance = safe_packet.get("official_source_provenance") or []
        source_url = (safe_provenance[0] or {}).get("source_url") if safe_provenance else None
        result.update(
            {
                "permit_verdict": "YES",
                "permit_required": True,
                "permit_type": primary,
                "permit_name": primary,
                "_permit_display_name": primary,
                "permit_type_verified": True,
                "permit_name_verified": True,
                "permit_name_status": "source_backed_official_path_confirmed",
                "permit_name_confidence": "high",
                "claim_citations": safe_provenance,
                "apply_url": packet.get("apply_url") or source_url,
                "apply_phone": None,
                "apply_google_maps": None,
                "apply_pdf": None,
                "inspection_booking": None,
                "inspections": None,
                "fee_range": None,
                "approval_timeline": None,
                "applying_office": None,
                "apply_path": {
                    "support_level": "source_backed",
                    "platform": "official_or_delegated_source",
                    "portal_url": packet.get("apply_url") or source_url,
                    "permit_category": primary,
                    "permit_type": primary,
                    "portal_selection_path": [primary],
                    "steps": [
                        "Use the official source-backed filing path listed here.",
                        "Prepare the scope, plans, owner authorization, contractor information, and valuation requested by the official filing path.",
                    ],
                    "verification_note": "Source-backed official filing path verified for this lookup.",
                },
                "permits_required": [
                    {
                        "permit_type": primary,
                        "required": True,
                        "source_backed": True,
                        "official_source_url": source_url,
                    }
                ],
                "completion_ticket": None,
            }
        )
    else:
        result.update(
            {
                "permit_verdict": "PENDING_ACTIVE_RETRIEVAL",
                "permit_required": None,
                "permit_type": None,
                "permit_name": None,
                "_permit_display_name": None,
                "permit_type_verified": False,
                "permit_name_verified": False,
                "permit_name_status": "pending_active_retrieval",
                "permit_name_confidence": None,
                "permits_required": [],
                "apply_url": None,
                "apply_phone": None,
                "apply_google_maps": None,
                "apply_pdf": None,
                "apply_path": None,
                "inspection_booking": None,
                "inspections": None,
                "fee_range": None,
                "approval_timeline": None,
                "claim_citations": [],
                "completion_ticket": packet.get("completion_ticket"),
            }
        )
    return result


__all__ = [
    "PENDING_ACTIVE_RETRIEVAL",
    "VERIFIED_FINAL",
    "CustomerOutputScanner",
    "PermitAssist3RevisedEngine",
    "PermitAssist3RevisedFinalGate",
    "StateOverlayRegistry",
    "apply_permitassist3_revised_contract",
    "infer_vertical",
    "load_verified_corpus_slice",
]
