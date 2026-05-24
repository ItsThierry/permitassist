"""Fixture-only source connector registry for Phase 1 M3.

M3 introduces a *policy and shape* contract for source connectors. It does
not perform any live I/O. Every supported connector either:

  - is allowed only against synthetic ``fixture://`` documents and emits
    M1-schema-shaped ``sources`` / ``source_observations`` / ``cost_events``
    payload dictionaries, or
  - is recorded as an explicitly *blocked* policy entry (paid / login /
    API / scrape) so the system never silently accepts those sources in
    Phase 1.

This module performs no network, DNS, SMTP, scraping, paid-provider,
database, outreach, or production action. It cannot authorize sending.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from .contracts import ContractEnum, SourceClass
from .schema import PHASE1_SCHEMA_VERSION

CONNECTOR_VERSION = "lead_pipeline_phase1_m3_fixture_connectors_v1"
FIXTURE_URL_PREFIX = "fixture://"


class FetchMode(ContractEnum):
    """Only fixture-mode runs are allowed in Phase 1."""

    FIXTURE_ONLY = "fixture_only"


class ConnectorPolicyStatus(ContractEnum):
    """Exact policy status for each connector in the M3 registry."""

    ALLOWED_FIXTURE_PHASE1 = "allowed_fixture_phase1"
    BLOCKED_PAID_PHASE1 = "blocked_paid_phase1"
    BLOCKED_PAID_API_PHASE1 = "blocked_paid_api_phase1"
    BLOCKED_LOGIN_PHASE1 = "blocked_login_phase1"
    BLOCKED_SCRAPING_REVIEW_REQUIRED_PHASE1 = "blocked_scraping_review_required_phase1"


class LiveFetchAttemptError(RuntimeError):
    """Raised if any caller attempts a non-fixture mode or live URL fetch."""


class UnknownConnectorError(KeyError):
    """Raised if a connector id is not in the M3 registry."""


@dataclass(frozen=True)
class ConnectorSpec:
    """Source policy and shape definition for a single M3 connector."""

    connector_id: str
    source_class: SourceClass
    source_name: str
    base_url_or_path: str
    official_or_first_party_flag: bool
    terms_notes: str
    robots_notes: str
    requires_login: bool
    paid_flag: bool
    requires_api_key: bool
    policy_status: ConnectorPolicyStatus
    allowed_phase: str
    blocked_reason: str = ""
    rate_limit_policy_id: str | None = None
    budget_policy_id: str | None = None


@dataclass(frozen=True)
class FixtureDocument:
    """Synthetic fixture payload representing one observation-shaped document."""

    url_or_path: str
    fetched_at_utc: str
    payload_text: str
    snippet: str
    content_type: str = "text/plain"


@dataclass(frozen=True)
class ConnectorRunResult:
    """Deterministic, no-network connector run output."""

    connector_run_id: str
    connector_id: str
    mode: str
    network_used: bool
    send_authorized: bool
    source: Mapping[str, Any]
    observations: tuple[Mapping[str, Any], ...]
    cost_events: tuple[Mapping[str, Any], ...]


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _payload_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_id_for(connector_id: str) -> str:
    return f"src_{connector_id}_{_stable_hash(connector_id)[:16]}"


def _observation_id_for(connector_run_id: str, doc_index: int, doc: FixtureDocument) -> str:
    fingerprint = _stable_hash([connector_run_id, doc_index, doc.url_or_path, doc.fetched_at_utc])
    return f"obs_{fingerprint[:24]}"


def _connector_run_id_for(connector_id: str, batch_id: str, documents: Sequence[FixtureDocument]) -> str:
    fingerprint = _stable_hash(
        [
            connector_id,
            batch_id,
            [(doc.url_or_path, doc.fetched_at_utc, doc.payload_text, doc.snippet, doc.content_type) for doc in documents],
        ]
    )
    return f"crun_{fingerprint[:24]}"


def _cost_event_id_for(connector_run_id: str, observation_id: str) -> str:
    return f"cost_{_stable_hash([connector_run_id, observation_id])[:24]}"


_ALLOWED_PHASE = "phase1_fixture_only"
_BLOCKED_PHASE = "blocked_future_phase_requires_separate_approval"


def _allowed(
    connector_id: str,
    *,
    source_class: SourceClass,
    source_name: str,
    base_url_or_path: str,
    terms_notes: str,
    robots_notes: str,
    official_or_first_party_flag: bool | None = None,
) -> ConnectorSpec:
    if not base_url_or_path.startswith(FIXTURE_URL_PREFIX):
        raise ValueError(f"Allowed connector {connector_id!r} must use a fixture:// base_url_or_path")
    if official_or_first_party_flag is None:
        official_or_first_party_flag = source_class in {
            SourceClass.OFFICIAL_LICENSING,
            SourceClass.FIRST_PARTY_WEBSITE,
        }
    return ConnectorSpec(
        connector_id=connector_id,
        source_class=source_class,
        source_name=source_name,
        base_url_or_path=base_url_or_path,
        official_or_first_party_flag=official_or_first_party_flag,
        terms_notes=terms_notes,
        robots_notes=robots_notes,
        requires_login=False,
        paid_flag=False,
        requires_api_key=False,
        policy_status=ConnectorPolicyStatus.ALLOWED_FIXTURE_PHASE1,
        allowed_phase=_ALLOWED_PHASE,
        blocked_reason="",
    )


def _blocked(
    connector_id: str,
    *,
    source_class: SourceClass,
    source_name: str,
    base_url_or_path: str,
    policy_status: ConnectorPolicyStatus,
    blocked_reason: str,
    requires_login: bool = False,
    paid_flag: bool = False,
    requires_api_key: bool = False,
    terms_notes: str = "",
    robots_notes: str = "",
) -> ConnectorSpec:
    return ConnectorSpec(
        connector_id=connector_id,
        source_class=source_class,
        source_name=source_name,
        base_url_or_path=base_url_or_path,
        official_or_first_party_flag=False,
        terms_notes=terms_notes,
        robots_notes=robots_notes,
        requires_login=requires_login,
        paid_flag=paid_flag,
        requires_api_key=requires_api_key,
        policy_status=policy_status,
        allowed_phase=_BLOCKED_PHASE,
        blocked_reason=blocked_reason,
    )


_REGISTRY_ITEMS: tuple[ConnectorSpec, ...] = (
    # Allowed — official / free / first-party / operator-imported.
    _allowed(
        "state_contractor_license_registry",
        source_class=SourceClass.OFFICIAL_LICENSING,
        source_name="State contractor license registry (official, free public lookup)",
        base_url_or_path="fixture://state-license-registry/",
        terms_notes="Public official licensing data; record provenance per lookup.",
        robots_notes="Public lookup endpoints only; respect any per-record query limits.",
    ),
    _allowed(
        "secretary_of_state_business_registry",
        source_class=SourceClass.OFFICIAL_LICENSING,
        source_name="Secretary of State business entity registry (official, free)",
        base_url_or_path="fixture://secretary-of-state-business-registry/",
        terms_notes="Public official entity filings; cite filing snippet.",
        robots_notes="Public business entity search endpoints; avoid bulk export.",
    ),
    _allowed(
        "municipal_permit_portal_public_search",
        source_class=SourceClass.OFFICIAL_LICENSING,
        source_name="Municipal/AHJ public permit portal search",
        base_url_or_path="fixture://municipal-permit-portal/",
        terms_notes="Public permit history; cite permit record snippet.",
        robots_notes="Public AHJ permit search endpoints; honor each AHJ's terms.",
    ),
    _allowed(
        "contractor_first_party_website",
        source_class=SourceClass.FIRST_PARTY_WEBSITE,
        source_name="Contractor first-party website (services/about pages)",
        base_url_or_path="fixture://contractor-first-party/",
        terms_notes="First-party website content cited with per-field snippet.",
        robots_notes="Honor robots.txt; first-party content only.",
    ),
    _allowed(
        "user_uploaded_company_list_csv",
        source_class=SourceClass.USER_IMPORT,
        source_name="Operator-uploaded company seed list CSV",
        base_url_or_path="fixture://user-import/",
        terms_notes="Operator-supplied seed list; lineage recorded per row.",
        robots_notes="No external fetch; lineage recorded against import.",
        official_or_first_party_flag=False,
    ),
    # Blocked — paid / login / API / scrape.
    _blocked(
        "apollo_io_b2b_database",
        source_class=SourceClass.PURCHASED_VENDOR,
        source_name="Apollo.io B2B database (paid)",
        base_url_or_path="blocked://paid/apollo",
        policy_status=ConnectorPolicyStatus.BLOCKED_PAID_PHASE1,
        blocked_reason="Paid B2B database; not allowed in Phase 1 fixture-only milestone.",
        paid_flag=True,
        requires_api_key=True,
        terms_notes="Paid vendor terms of service; not applicable while blocked.",
        robots_notes="N/A — connector blocked.",
    ),
    _blocked(
        "zoominfo_export",
        source_class=SourceClass.PURCHASED_VENDOR,
        source_name="ZoomInfo paid export",
        base_url_or_path="blocked://paid/zoominfo",
        policy_status=ConnectorPolicyStatus.BLOCKED_PAID_PHASE1,
        blocked_reason="Paid B2B database export; not allowed in Phase 1 fixture-only milestone.",
        paid_flag=True,
        requires_api_key=True,
    ),
    _blocked(
        "serper_google_search_api",
        source_class=SourceClass.SEARCH_OR_PLACES,
        source_name="Serper Google search API (paid)",
        base_url_or_path="blocked://api/serper",
        policy_status=ConnectorPolicyStatus.BLOCKED_PAID_API_PHASE1,
        blocked_reason="Paid third-party search API; not allowed in Phase 1 fixture-only milestone.",
        paid_flag=True,
        requires_api_key=True,
    ),
    _blocked(
        "clearbit_enrichment_api",
        source_class=SourceClass.PURCHASED_VENDOR,
        source_name="Clearbit enrichment API (paid)",
        base_url_or_path="blocked://api/clearbit",
        policy_status=ConnectorPolicyStatus.BLOCKED_PAID_API_PHASE1,
        blocked_reason="Paid enrichment API; not allowed in Phase 1 fixture-only milestone.",
        paid_flag=True,
        requires_api_key=True,
    ),
    _blocked(
        "linkedin_sales_navigator",
        source_class=SourceClass.SOCIAL_PROFILE,
        source_name="LinkedIn Sales Navigator (login-required)",
        base_url_or_path="blocked://login/linkedin-sales-navigator",
        policy_status=ConnectorPolicyStatus.BLOCKED_LOGIN_PHASE1,
        blocked_reason="Login-required social platform with terms restrictions; not allowed in Phase 1.",
        requires_login=True,
    ),
    _blocked(
        "generic_web_scrape_robots_disallow",
        source_class=SourceClass.SCRAPED_PUBLIC_PAGE,
        source_name="Generic web scraper for robots-disallowed pages",
        base_url_or_path="blocked://scrape/robots-disallow",
        policy_status=ConnectorPolicyStatus.BLOCKED_SCRAPING_REVIEW_REQUIRED_PHASE1,
        blocked_reason="Robots-disallowed scraping requires explicit review; not allowed in Phase 1.",
    ),
)


FIXTURE_CONNECTOR_REGISTRY: Mapping[str, ConnectorSpec] = MappingProxyType(
    {spec.connector_id: spec for spec in _REGISTRY_ITEMS}
)


def get_connector_spec(connector_id: str) -> ConnectorSpec:
    """Return a connector spec by id, or raise ``UnknownConnectorError``."""

    try:
        return FIXTURE_CONNECTOR_REGISTRY[connector_id]
    except KeyError as exc:
        raise UnknownConnectorError(f"Unknown connector id: {connector_id!r}") from exc


def _bool_to_int(value: bool) -> int:
    return 1 if value else 0


def _coerce_fetch_mode(mode: FetchMode | str) -> FetchMode:
    if isinstance(mode, FetchMode):
        return mode
    if isinstance(mode, str) and mode == FetchMode.FIXTURE_ONLY.value:
        return FetchMode.FIXTURE_ONLY
    raise LiveFetchAttemptError(
        f"Phase 1 M3 connectors only support FetchMode.FIXTURE_ONLY; got {mode!r}"
    )


def _validate_documents(documents: Sequence[FixtureDocument]) -> None:
    if not documents:
        raise ValueError("run_fixture_connector requires at least one FixtureDocument")
    for doc in documents:
        if not isinstance(doc, FixtureDocument):
            raise TypeError(f"Expected FixtureDocument, got {type(doc).__name__}")
        if not doc.url_or_path.startswith(FIXTURE_URL_PREFIX):
            raise LiveFetchAttemptError(
                f"Fixture connector refused non-fixture URL {doc.url_or_path!r}; "
                "Phase 1 M3 requires fixture:// prefix."
            )


def _source_payload(spec: ConnectorSpec) -> dict[str, Any]:
    return {
        "source_id": _source_id_for(spec.connector_id),
        "source_class": spec.source_class.value,
        "source_name": spec.source_name,
        "base_url_or_path": spec.base_url_or_path,
        "official_or_first_party_flag": _bool_to_int(spec.official_or_first_party_flag),
        "terms_notes": spec.terms_notes,
        "robots_notes": spec.robots_notes,
        "requires_login": _bool_to_int(spec.requires_login),
        "paid_flag": _bool_to_int(spec.paid_flag),
        "allowed_phase": spec.allowed_phase,
        "rate_limit_policy_id": spec.rate_limit_policy_id,
        "budget_policy_id": spec.budget_policy_id,
        "schema_version": PHASE1_SCHEMA_VERSION,
    }


def _observation_payload(
    *,
    observation_id: str,
    source_id: str,
    batch_id: str,
    connector_run_id: str,
    doc: FixtureDocument,
    cost_event_id: str,
) -> dict[str, Any]:
    return {
        "observation_id": observation_id,
        "source_id": source_id,
        "batch_id": batch_id,
        "connector_run_id": connector_run_id,
        "observed_at_utc": doc.fetched_at_utc,
        "url_or_path": doc.url_or_path,
        "http_status": None,
        "content_type": doc.content_type,
        "payload_hash_sha256": _payload_hash(doc.payload_text),
        "snapshot_ref": None,
        "raw_payload_ref": None,
        "snippet_or_excerpt": doc.snippet,
        "extractor_version": CONNECTOR_VERSION,
        "robots_or_terms_classification": "fixture_only_no_fetch",
        "blocked_or_captcha_flag": 0,
        "cost_event_id": cost_event_id,
        "schema_version": PHASE1_SCHEMA_VERSION,
    }


def _cost_event_payload(
    *,
    cost_event_id: str,
    batch_id: str,
    connector_run_id: str,
    provider_or_tool: str,
    observed_at_utc: str,
) -> dict[str, Any]:
    return {
        "cost_event_id": cost_event_id,
        "batch_id": batch_id,
        "entity_id": None,
        "connector_run_id": connector_run_id,
        "stage": "raw_collection",
        "provider_or_tool": provider_or_tool,
        "units_consumed": 1.0,
        "cost_usd": 0.0,
        "allocated_flag": 1,
        "waste_reason": None,
        "created_at_utc": observed_at_utc,
        "schema_version": PHASE1_SCHEMA_VERSION,
    }


def run_fixture_connector(
    connector_id: str,
    *,
    documents: Sequence[FixtureDocument],
    batch_id: str,
    mode: FetchMode | str = FetchMode.FIXTURE_ONLY,
) -> ConnectorRunResult:
    """Run an allowed fixture connector against synthetic documents.

    The function performs no network or filesystem I/O. It validates the
    fetch mode, refuses live URLs, refuses blocked connector ids via the
    adapter policy, and returns deterministic M1-shaped payload dicts.
    """

    fetch_mode = _coerce_fetch_mode(mode)

    # Late import to avoid module-import cycle between connectors and adapters.
    from .adapters import enforce_adapter_policy_for_connector

    enforce_adapter_policy_for_connector(connector_id)

    spec = get_connector_spec(connector_id)
    _validate_documents(documents)

    connector_run_id = _connector_run_id_for(connector_id, batch_id, documents)
    source_payload = _source_payload(spec)

    observation_payloads: list[Mapping[str, Any]] = []
    cost_event_payloads: list[Mapping[str, Any]] = []
    for index, doc in enumerate(documents):
        observation_id = _observation_id_for(connector_run_id, index, doc)
        cost_event_id = _cost_event_id_for(connector_run_id, observation_id)
        cost_event_payloads.append(
            _cost_event_payload(
                cost_event_id=cost_event_id,
                batch_id=batch_id,
                connector_run_id=connector_run_id,
                provider_or_tool=f"fixture_connector::{connector_id}",
                observed_at_utc=doc.fetched_at_utc,
            )
        )
        observation_payloads.append(
            _observation_payload(
                observation_id=observation_id,
                source_id=source_payload["source_id"],
                batch_id=batch_id,
                connector_run_id=connector_run_id,
                doc=doc,
                cost_event_id=cost_event_id,
            )
        )

    return ConnectorRunResult(
        connector_run_id=connector_run_id,
        connector_id=connector_id,
        mode=fetch_mode.value,
        network_used=False,
        send_authorized=False,
        source=MappingProxyType(source_payload),
        observations=tuple(MappingProxyType(payload) for payload in observation_payloads),
        cost_events=tuple(MappingProxyType(payload) for payload in cost_event_payloads),
    )


def iter_allowed_connector_ids() -> Iterable[str]:
    """Iterator over Phase 1 M3 allowed-fixture connector ids."""

    for connector_id, spec in FIXTURE_CONNECTOR_REGISTRY.items():
        if spec.policy_status == ConnectorPolicyStatus.ALLOWED_FIXTURE_PHASE1:
            yield connector_id


def iter_blocked_connector_ids() -> Iterable[str]:
    """Iterator over Phase 1 M3 blocked connector ids."""

    for connector_id, spec in FIXTURE_CONNECTOR_REGISTRY.items():
        if spec.policy_status != ConnectorPolicyStatus.ALLOWED_FIXTURE_PHASE1:
            yield connector_id
