from __future__ import annotations

"""Deterministic filing-AHJ locality resolver.

This is a final customer-boundary helper: it does not decide whether a permit is
required.  It only resolves the filing authority for overlapping city/county
jurisdictions and keeps real county/state requirement facets as labeled context.
"""

import copy
from dataclasses import dataclass, field
import re
from typing import Any
from urllib.parse import quote_plus, urlparse


@dataclass(frozen=True)
class NestedJurisdiction:
    city: str
    state: str
    resolved_ahj_key: str
    resolved_ahj_name: str
    filing_authority_url: str
    fee_authority_url: str = ""
    retained_county_facets: tuple[str, ...] = field(default_factory=tuple)
    retained_source_patterns: tuple[str, ...] = field(default_factory=tuple)
    blocked_filing_patterns: tuple[str, ...] = field(default_factory=tuple)
    source_title: str = "Official city permitting source"
    resolved_level: str = "city"
    office_address: str = ""
    office_phone: str = ""
    official_source_urls: tuple[str, ...] = field(default_factory=tuple)


NESTED_JURISDICTIONS: dict[tuple[str, str], NestedJurisdiction] = {
    ("miami", "FL"): NestedJurisdiction(
        city="Miami",
        state="FL",
        resolved_ahj_key="miami_fl_city",
        resolved_ahj_name="City of Miami Building Department",
        filing_authority_url="https://www.miami.gov/Permits-Construction",
        fee_authority_url="https://www.miami.gov/Permits-Construction/Permitting-Resources/City-of-Miami-Building-Permit-Fee-Schedule",
        retained_county_facets=("Miami-Dade County HVHZ / NOA / Florida Product Approval",),
        retained_source_patterns=(r"windows[-_]?shutters[-_]?doors", r"product", r"NOA", r"HVHZ", r"florida-product"),
        blocked_filing_patterns=(r"ecobuilt\.miamidade\.gov", r"miamidade\.gov/buildingpermit", r"miamidade\.gov/global/economy/building/building-permit-fees", r"miamidade\.gov/global/permit", r"solicitationdetails", r"stratproc"),
        source_title="City of Miami permits and construction",
    ),
    ("chicago", "IL"): NestedJurisdiction(
        city="Chicago",
        state="IL",
        resolved_ahj_key="chicago_il_city",
        resolved_ahj_name="Chicago Department of Buildings (DOB)",
        filing_authority_url="https://www.chicago.gov/city/en/depts/bldgs.html",
        fee_authority_url="https://www.chicago.gov/city/en/depts/bldgs/provdrs/permits/svcs/permit_fee_calculator.html",
        source_title="Chicago Department of Buildings",
    ),
    ("new york", "NY"): NestedJurisdiction(
        city="New York",
        state="NY",
        resolved_ahj_key="nyc_dob",
        resolved_ahj_name="NYC Department of Buildings (DOB) / DOB NOW",
        filing_authority_url="https://www.nyc.gov/site/buildings/industry/dob-now.page",
        fee_authority_url="https://www.nyc.gov/site/buildings/dob/fees.page",
        source_title="NYC Department of Buildings DOB NOW",
    ),
    ("brooklyn", "NY"): NestedJurisdiction(
        city="Brooklyn",
        state="NY",
        resolved_ahj_key="nyc_dob",
        resolved_ahj_name="NYC Department of Buildings (DOB) / DOB NOW",
        filing_authority_url="https://www.nyc.gov/site/buildings/industry/dob-now.page",
        fee_authority_url="https://www.nyc.gov/site/buildings/dob/fees.page",
        source_title="NYC Department of Buildings DOB NOW",
    ),
    ("fort wayne", "IN"): NestedJurisdiction(
        city="Fort Wayne",
        state="IN",
        resolved_ahj_key="allen_county_building_department_joint",
        resolved_ahj_name="Allen County Building Department",
        filing_authority_url="https://aca-prod.accela.com/ACFW/Default.aspx",
        fee_authority_url="https://www.allencounty.in.gov/308/Applications-Fees",
        resolved_level="county_joint_city_county",
        office_address="200 East Berry Street, Suite 180, Fort Wayne, IN 46802",
        office_phone="260-449-7131",
        official_source_urls=(
            "https://www.allencounty.in.gov/234/Building-Department",
            "https://www.allencounty.in.gov/308/Applications-Fees",
            "https://www.allencounty.in.gov/directory.aspx?did=18",
        ),
        blocked_filing_patterns=(
            r"cityoffortwayne\.in\.gov/668/permits-and-bonds",
            r"cityoffortwayne\.org/.*/right-of-way",
        ),
    ),
}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _entry(city: str, state: str) -> NestedJurisdiction | None:
    return NESTED_JURISDICTIONS.get((_norm(city), str(state or "").upper().strip()))


def resolve_ahj_locality(city: str, state: str, result: dict[str, Any] | None = None, job_type: str = "") -> dict[str, Any] | None:
    entry = _entry(city, state)
    if not entry:
        return None
    return {
        "resolved_level": entry.resolved_level,
        "resolved_ahj_name": entry.resolved_ahj_name,
        "resolved_ahj_key": entry.resolved_ahj_key,
        "filing_authority_url": entry.filing_authority_url,
        "fee_authority_url": entry.fee_authority_url,
        "retained_county_facets": list(entry.retained_county_facets),
        "office_address": entry.office_address,
        "office_phone": entry.office_phone,
        "source": "deterministic_nested_jurisdiction_table",
    }


def _url(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("url") or value.get("source_url") or value.get("portal_url") or "")
    return str(value or "")


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _blocked_by_entry(url: str, entry: NestedJurisdiction) -> bool:
    text = str(url or "").lower()
    return any(re.search(pattern, text, flags=re.I) for pattern in entry.blocked_filing_patterns)


def _retained_by_entry(url: str, entry: NestedJurisdiction) -> bool:
    text = str(url or "")
    return any(re.search(pattern, text, flags=re.I) for pattern in entry.retained_source_patterns)


def classify_source_for_resolution(source: Any, resolution: dict[str, Any] | None) -> str:
    """Return filing_authority, retained_requirement, irrelevant_or_procurement, or context."""
    if not resolution:
        return "context"
    entry = next((e for e in NESTED_JURISDICTIONS.values() if e.resolved_ahj_key == resolution.get("resolved_ahj_key")), None)
    if not entry:
        return "context"
    url = _url(source)
    host = _host(url)
    filing_hosts = {
        _host(entry.filing_authority_url),
        *(_host(url) for url in entry.official_source_urls),
    }
    if host and host in filing_hosts:
        return "filing_authority"
    if _retained_by_entry(url, entry):
        return "retained_requirement"
    if _blocked_by_entry(url, entry):
        return "irrelevant_or_procurement"
    return "context"


def _source(url: str, title: str, relevance: str = "filing_authority") -> dict[str, Any]:
    return {
        "url": url,
        "title": title,
        "publisher": title,
        "source_tier": "local_permit_source" if relevance == "filing_authority" else "retained_requirement_source",
        "source_relevance": relevance,
    }


def _dedupe_urls(urls: list[str]) -> list[str]:
    out: list[str] = []
    for url in urls:
        if url and url not in out:
            out.append(url)
    return out


def _add_hvhz_docs(out: dict[str, Any], entry: NestedJurisdiction, job_type: str) -> None:
    if entry.resolved_ahj_key != "miami_fl_city":
        return
    scope = _norm(job_type or out.get("job_summary") or out.get("summary") or out.get("permit_name"))
    if not re.search(r"\b(?:window|door|storefront|opening|impact|exterior)\b", scope):
        return
    docs_to_add = [
        "NOA / Florida Product Approval for impact-rated windows, doors, or storefront systems",
        "Miami-Dade HVHZ product-approval documentation for exterior opening protection",
    ]
    for row in out.get("permits_required") or []:
        if not isinstance(row, dict):
            continue
        text = _norm(" ".join(str(row.get(k) or "") for k in ("permit_name", "permit_type", "family", "kind")))
        if any(token in text for token in ("building", "window", "door", "storefront", "tenant improvement")):
            docs = list(row.get("documents") or []) if isinstance(row.get("documents"), list) else []
            for doc in docs_to_add:
                if doc not in docs:
                    docs.append(doc)
            row["documents"] = docs
    legacy_docs = list(out.get("documents_to_prepare") or []) if isinstance(out.get("documents_to_prepare"), list) else []
    for doc in docs_to_add:
        if doc not in legacy_docs:
            legacy_docs.append(doc)
    if legacy_docs:
        out["documents_to_prepare"] = legacy_docs
        out["what_to_bring"] = legacy_docs
        out["requirements"] = legacy_docs
        if isinstance(out.get("apply_path"), dict):
            out["apply_path"]["documents_to_prepare"] = legacy_docs


def apply_ahj_locality_resolution(result: dict[str, Any], city: str, state: str, job_type: str = "") -> dict[str, Any]:
    out = copy.deepcopy(result) if isinstance(result, dict) else {}
    entry = _entry(city, state)
    if not entry:
        return out
    resolution = resolve_ahj_locality(city, state, out, job_type) or {}
    out["ahj_resolution"] = resolution
    out["applying_office"] = entry.resolved_ahj_name
    out["apply_url"] = entry.filing_authority_url
    out["online_application_url"] = entry.filing_authority_url
    if entry.office_address:
        out["apply_address"] = entry.office_address
        maps_query = quote_plus(f"{entry.resolved_ahj_name}, {entry.office_address}")
        maps_url = f"https://www.google.com/maps/search/?api=1&query={maps_query}"
        out["apply_google_maps"] = maps_url
        out["maps_url"] = maps_url
    if entry.office_phone:
        out["apply_phone"] = entry.office_phone
        out["building_dept_phone"] = entry.office_phone
    if str(out.get("permit_decision") or "").upper() == "REQUIRED" or out.get("permit_required") is True:
        out["apply_url_known"] = True
    apply_path = dict(out.get("apply_path") or {}) if isinstance(out.get("apply_path"), dict) else {}
    apply_path.update({
        "state": "resolved_portal",
        "status": "RESOLVED_PORTAL",
        "typed_status": "RESOLVED_PORTAL",
        "channel": "online_portal",
        "portal_url": entry.filing_authority_url,
        "office_name": entry.resolved_ahj_name,
        "authority": entry.resolved_ahj_name,
        "source_relevance": "filing_authority",
    })
    out["apply_path"] = apply_path

    fee_text = str(out.get("fee_range") or "")
    fee_needs_verification = bool(
        entry.fee_authority_url
        and re.search(
            r"miami[- ]dade|\$\s*158|\b(?:rough|budget|estimate|not confirmed|not found|verification needed|verify)\b",
            fee_text,
            re.I,
        )
    )
    safe_fee_text = (
        f"Permit fee not confirmed; verify the current {entry.resolved_ahj_name} fee schedule before quoting: {entry.fee_authority_url}"
        if entry.fee_authority_url
        else ""
    )
    if fee_needs_verification:
        out["fee_range"] = safe_fee_text

    def rewrite_rows(rows: Any) -> None:
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            row["action_url"] = entry.filing_authority_url
            row["apply_url"] = entry.filing_authority_url
            row["source"] = entry.filing_authority_url
            row["source_url"] = entry.filing_authority_url
            if fee_needs_verification and (
                str(row.get("decision") or row.get("status") or "REQUIRED").upper() == "REQUIRED"
                or row.get("required") is True
            ):
                row.pop("fees", None)
                row["fee"] = safe_fee_text

    for key in ("permits_required", "conditional_permits", "related_permits", "companion_permits", "public_packet_rows"):
        rewrite_rows(out.get(key))
    for packet_key in ("public_packet", "canonical_public_packet"):
        packet = out.get(packet_key)
        if isinstance(packet, dict):
            rewrite_rows(packet.get("rows"))

    clean_sources: list[Any] = []
    retained_sources: list[Any] = []
    degraded: list[dict[str, Any]] = []
    for src in out.get("sources") or []:
        if not isinstance(src, dict):
            continue
        relevance = classify_source_for_resolution(src, resolution)
        if relevance == "irrelevant_or_procurement":
            degraded.append({"url": _url(src), "status": "irrelevant_or_wrong_authority", "reason": "Source is not the resolved filing authority for this locality."})
            continue
        item = copy.deepcopy(src)
        if relevance == "retained_requirement":
            item["source_relevance"] = "retained_requirement"
            item["title"] = item.get("title") if re.search(r"hvhz|noa|product|window|door", str(item.get("title") or ""), re.I) else "County HVHZ / NOA retained requirement source"
            retained_sources.append(item)
        elif relevance == "filing_authority" or not _blocked_by_entry(_url(src), entry):
            if relevance == "filing_authority":
                item["source_relevance"] = "filing_authority"
            clean_sources.append(item)
    official = [_source(entry.filing_authority_url, entry.source_title, "filing_authority")]
    official.extend(_source(url, entry.resolved_ahj_name, "filing_authority") for url in entry.official_source_urls)
    if entry.fee_authority_url:
        official.append(_source(entry.fee_authority_url, f"{entry.resolved_ahj_name} fee schedule", "filing_authority"))
    if entry.resolved_ahj_key == "miami_fl_city":
        retained_url = "https://www.miamidade.gov/global/economy/building/windows-shutters-doors.page"
        if not any(_url(src) == retained_url for src in retained_sources):
            retained_sources.append(_source(retained_url, "Miami-Dade HVHZ windows, shutters, doors retained requirement", "retained_requirement"))
    out["sources"] = [*official, *retained_sources, *clean_sources]
    out["source_urls"] = _dedupe_urls([_url(src) for src in out["sources"] if _url(src)])
    if degraded:
        resolution["discarded_wrong_authority_sources"] = degraded
        out["ahj_resolution"] = resolution
    _add_hvhz_docs(out, entry, job_type)
    return out
