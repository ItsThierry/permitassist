"""Enums and type contracts for the fixture-only Phase 1 lead pipeline."""

from __future__ import annotations

from enum import Enum


class ContractEnum(str, Enum):
    """String enum helper with stable values for docs and schema checks."""

    def __str__(self) -> str:
        return self.value


class SourceClass(ContractEnum):
    OFFICIAL_LICENSING = "official_licensing_registry"
    FIRST_PARTY_WEBSITE = "first_party_website"
    SEARCH_OR_PLACES = "search_or_places_discovery"
    AGGREGATOR_DIRECTORY = "aggregator_directory"
    SOCIAL_PROFILE = "social_profile"
    SCRAPED_PUBLIC_PAGE = "scraped_public_page_future_phase"
    PURCHASED_VENDOR = "purchased_vendor_raw_seed"
    USER_IMPORT = "user_import"


class PageType(ContractEnum):
    FIRST_PARTY_HOMEPAGE = "first_party_homepage"
    FIRST_PARTY_SERVICES = "first_party_services"
    FIRST_PARTY_CONTACT = "first_party_contact"
    OFFICIAL_REGISTRY = "official_registry"
    SEARCH_RESULT = "search_result"
    AGGREGATOR_DIRECTORY = "aggregator_directory"
    BLOCKED_UNKNOWN = "blocked_unknown"


class RobotsTermsPrefetchStatus(ContractEnum):
    ALLOWED_FIXTURE_ONLY = "allowed_fixture_only"
    ALLOWED_PUBLIC = "allowed_public"
    CONDITIONAL_REVIEW_REQUIRED = "conditional_review_required"
    BLOCKED = "blocked"


class EntityKind(ContractEnum):
    BUSINESS = "business"
    PERSON = "person"
    DOMAIN = "domain"
    PHONE = "phone"
    EMAIL = "email"
    ADDRESS = "address"
    SOURCE_PROFILE = "source_profile"


class FactField(ContractEnum):
    BUSINESS_NAME = "business_name"
    LEGAL_NAME = "legal_name"
    DBA_NAME = "dba_name"
    WEBSITE_URL = "website_url"
    SERVICE_AREA = "service_area"
    TRADE_CATEGORY = "trade_category"
    VERTICAL_FIT_LABEL = "vertical_fit_label"
    LICENSE_CLASS = "license_class"
    CONTACT_EMAIL = "contact_email"
    CONTACT_ROLE = "contact_role"
    PERMITASSIST_ICP_SEGMENT = "permitassist_icp_segment"
    LOW_CALL_RELEVANCE_SIGNAL = "low_call_relevance_signal"
    CONTRACTOR_SOFTWARE_SIGNAL = "contractor_software_signal"


class PromotionTier(ContractEnum):
    RAW_DISCOVERY = "raw_discovery"
    IDENTITY_CANDIDATE = "identity_candidate"
    ICP_CANDIDATE = "icp_candidate"
    CONTACT_CANDIDATE = "contact_candidate"
    VERIFIED_CONTACT = "verified_contact"
    QUALIFIED_LEAD_REVIEW_REQUIRED = "qualified_lead_review_required"
    OUTREACH_READY_INTERNAL_ONLY = "outreach_ready_internal_only"
    LIVE_OUTREACH_READY_FUTURE_PHASE = "live_outreach_ready_future_phase"


class GateStatus(ContractEnum):
    PASS_ = "pass"
    FAIL_CLOSED = "fail_closed"
    BLOCKED_SOURCE_POLICY = "blocked_source_policy"
    REVIEW_REQUIRED = "review_required"
    UNKNOWN_NOT_PROMOTED = "unknown_not_promoted"
    RESERVED_FUTURE_PHASE = "reserved_future_phase"


class SuppressionStatus(ContractEnum):
    CLEAR = "clear"
    SUPPRESSED_EMAIL = "suppressed_email"
    SUPPRESSED_DOMAIN = "suppressed_domain"
    DUPLICATE_IN_CAMPAIGN = "duplicate_in_campaign"
    NOT_INTERESTED = "not_interested"
    SUPPRESSION_CONFLICT_HOLD = "suppression_conflict_hold"
    SUPPRESSION_UNKNOWN_HOLD = "suppression_unknown_hold"


class ExportEligibility(ContractEnum):
    NOT_EXPORTABLE = "not_exportable"
    BLOCKED_UNKNOWN = "blocked_unknown"
    BLOCKED_SUPPRESSED = "blocked_suppressed"
    BLOCKED_UNCITED = "blocked_uncited"
    BLOCKED_UNVERIFIED_CONTACT = "blocked_unverified_contact"
    REVIEW_REQUIRED = "review_required"
    INTERNAL_REVIEW_ONLY = "internal_review_only"
    FUTURE_LIVE_OUTREACH_RESERVED = "future_live_outreach_reserved"


class CostStage(ContractEnum):
    RAW_COLLECTION = "raw_collection"
    IDENTITY_RESOLUTION = "identity_resolution"
    CONTACT_DISCOVERY = "contact_discovery"
    CONTACT_VERIFICATION = "contact_verification"
    ICP_CLASSIFICATION = "icp_classification"
    ENRICHMENT_VALIDATION = "enrichment_validation"
    SUPPRESSION_CHECK = "suppression_check"
    HUMAN_REVIEW = "human_review"
    EXPORT_PREPARATION = "export_preparation"
    WASTE_ALLOCATION = "waste_allocation"


IDENTITY_MERGE_KEY_ORDER: tuple[str, ...] = (
    "license",
    "secretary_of_state_entity_id",
    "domain",
    "phone",
    "address",
    "business_name",
)


def identity_merge_key_order() -> tuple[str, ...]:
    """Return the approved M10 strongest-to-weakest identity merge key order."""

    return IDENTITY_MERGE_KEY_ORDER


PHASE1_ALLOWED_PROMOTION_TIERS = frozenset(
    {
        PromotionTier.RAW_DISCOVERY,
        PromotionTier.IDENTITY_CANDIDATE,
        PromotionTier.ICP_CANDIDATE,
        PromotionTier.CONTACT_CANDIDATE,
        PromotionTier.VERIFIED_CONTACT,
        PromotionTier.QUALIFIED_LEAD_REVIEW_REQUIRED,
        PromotionTier.OUTREACH_READY_INTERNAL_ONLY,
    }
)


class Phase1ContractError(ValueError):
    """Raised when Phase 1 skeleton code attempts a future/live state."""


def assert_phase1_promotion_allowed(tier: PromotionTier | str) -> None:
    """Fail closed if Phase 1 code tries to write a future live outreach tier."""

    promotion_tier = tier if isinstance(tier, PromotionTier) else PromotionTier(tier)
    if promotion_tier not in PHASE1_ALLOWED_PROMOTION_TIERS:
        raise Phase1ContractError(
            f"Promotion tier {promotion_tier.value!r} is reserved for a future "
            "Boban-approved live outreach phase and cannot be written in Phase 1."
        )
