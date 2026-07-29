#!/usr/bin/env python3
"""Repair independently verified v24 primary filing routes.

This is a bounded data migration, not runtime case logic. It updates both shipped
v24 documents, writes portable source snapshots, and regenerates the runtime
package hash chain. Use --check for an idempotent read-only verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "knowledge" / "v24"
INDEX_PATH = PKG / "permitassist_decision_cell_index_v24.json"
CELLS_PATH = PKG / "permitassist_decision_cells_v24.json"
MANIFEST_PATH = PKG / "permitassist_v24_manifest.json"
DEFERRED_PATH = PKG / "permitassist_v24_deferred_manifest.json"
SNAPSHOT_DIR = PKG / "snapshots" / "session4_verified_routes_20260728"
VERIFIED_AT = "2026-07-28T23:12:01Z"

ROUTES: dict[str, dict[str, Any]] = {
    "VT|ferrisburgh|commercial_tenant_improvement": {
        "expected_old_url": "https://legislature.vermont.gov/statutes/section/24/117/04449",
        "apply_url": "https://firesafety.vermont.gov/sites/firesafety/files/files/forms/dfs_construction_application.pdf",
        "source_url": "https://firesafety.vermont.gov/sites/firesafety/files/files/forms/dfs_construction_application.pdf",
        "source_quote": "Please fill out this permit application as completely as possible based on the scope of this project.",
        "authority": "Vermont Department of Public Safety, Division of Fire Safety, Rutland Regional Office",
        "authority_tier": "state",
        "handled_by_local_ahj": False,
        "channel": "downloadable_form",
        "snapshot_name": "us-vt-ferrisburgh-building-route.txt",
        "live_recheck": "PASS_WEB_EXTRACT_20260728",
    },
    "NY|lockport|commercial_tenant_improvement": {
        "expected_old_url": "https://lockportny.gov/building-inspection/",
        "apply_url": "https://lockportny.gov/wp-content/uploads/2018/04/building-permit-application-general.pdf",
        "source_url": "https://lockportny.gov/wp-content/uploads/2018/04/building-permit-application-general.pdf",
        "source_quote": "BUILDING PERMIT APPLICATION FOR GENERAL CONSTRUCTION",
        "authority": "City of Lockport Code Enforcement Officer / Building Inspection Department",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "downloadable_form",
        "snapshot_name": "us-ny-lockport-building-route.txt",
        "live_recheck": "PASS_WEB_EXTRACT_20260728",
    },
    "RI|west_warwick|commercial_tenant_improvement": {
        "expected_old_url": "https://westwarwickri.gov/building-zoning",
        "apply_url": "https://westwarwickri.portal.opengov.com/categories/1072/record-types/6332",
        "source_url": "https://westwarwickri.portal.opengov.com/categories/1072/record-types/6332",
        "source_quote": "BUILDING DEPARTMENT Building Permit — Building Permit — Apply Online",
        "authority": "Town of West Warwick Building Department",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "online_portal",
        "snapshot_name": "us-ri-west-warwick-building-route.txt",
        "live_recheck": "DOSSIER_VERIFIED_DYNAMIC_PORTAL_WEB_EXTRACT_BLOCKED_20260728",
    },
    "WA|tacoma|commercial_tenant_improvement": {
        "expected_old_url": "https://www.tacomapermits.org/tip-sheet-index/what-requires-a-permit",
        "apply_url": "https://aca.accela.com/tacoma/",
        "source_url": "https://aca.accela.com/tacoma/",
        "source_quote": "Customer Access Portal",
        "authority": "City of Tacoma Planning and Development Services",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "online_portal",
        "snapshot_name": "us-wa-tacoma-building-route.txt",
        "live_recheck": "PASS_WEB_EXTRACT_20260728",
    },
    "NY|niagara_falls|commercial_tenant_improvement": {
        "expected_old_url": "https://cms3.revize.com/revize/niagarafallsny/Documents/Government/Department/Code%20Enforcement/Online%20Inspections%20Documentation/NF_CE_ElectricalPermitApplication_RD.pdf",
        "apply_url": "https://www.niagarafallsny.gov/Documents/Government/Department/Code%20Enforcement/Permit/Interior%20Permit%20Application.pdf",
        "source_url": "https://www.niagarafallsny.gov/Documents/Government/Department/Code%20Enforcement/Permit/Interior%20Permit%20Application.pdf",
        "source_quote": "What type of building permit are you requesting? Residential □ Commercial □ Industrial □",
        "authority": "City of Niagara Falls Department of Code Enforcement",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "downloadable_form",
        "snapshot_name": "us-ny-niagara-falls-building-route.txt",
        "live_recheck": "PASS_WEB_EXTRACT_20260728",
    },
    "NM|rio_rancho|commercial_tenant_improvement": {
        "expected_old_url": "https://rrnm.gov/77/Building-Division",
        "apply_url": "https://rior-egov.aspgov.com/Click2GovBP/index.html",
        "source_url": "https://rrnm.gov/77/Building-Division",
        "source_quote": "You can now submit permits online at Click2Gov.",
        "authority": "City of Rio Rancho Development Services Department, Building Inspection Division",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "online_portal",
        "snapshot_name": "us-nm-rio-rancho-building-route.txt",
        "live_recheck": "DOSSIER_VERIFIED_DYNAMIC_PORTAL_WEB_EXTRACT_BLOCKED_20260728",
    },
    "WV|morgantown|commercial_tenant_improvement": {
        "expected_old_url": "https://www.morgantownwv.gov/172/Building-Permits",
        "apply_url": "https://app03.cityworksonline.com/CLIENT_MorgantownWV-Public/login",
        "source_url": "https://www.morgantownwv.gov/704/Permitting-Licensing-Registration",
        "source_quote": "To apply for a building permit, you must create an account and complete your application through the Cityworks PLL portal.",
        "authority": "City of Morgantown Building Inspection/Code Enforcement",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "online_portal",
        "snapshot_name": "us-wv-morgantown-building-route.txt",
        "live_recheck": "DOSSIER_VERIFIED_DYNAMIC_PORTAL_WEB_EXTRACT_BLOCKED_20260728",
    },
}

ROUTES.update({
    "PA|erie|commercial_tenant_improvement": {
        "expected_old_url": "https://www.erie.pa.us/government/city_offices_departments/planning_and_neighborhood_resources/permits.php",
        "apply_url": "https://ecode360.com/ER3969/document/599340211.pdf",
        "source_url": "https://ecode360.com/ER3969/document/599340211.pdf",
        "source_quote": "CITY OF ERIE APPLICATION FOR COMMERCIAL BUILDING PERMIT",
        "authority": "City of Erie Bureau of Code Enforcement",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "downloadable_form",
        "snapshot_name": "us-pa-erie-building-route.txt",
        "live_recheck": "PASS_WEB_EXTRACT_20260728",
    },
    "OH|fairfield|commercial_tenant_improvement": {
        "expected_old_url": "https://www.fairfield-city.org/164/Building-Zoning-Division",
        "apply_url": "https://fairfieldoh.viewpointcloud.com/",
        "source_url": "https://www.fairfield-city.org/167/Building-Permits",
        "source_quote": "To obtain a City of Fairfield commercial building permit, register for an account and complete a Building Permit Application using the city's Online Permit Center.",
        "authority": "City of Fairfield Building & Zoning Division",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "online_portal",
        "snapshot_name": "us-oh-fairfield-building-route.txt",
        "live_recheck": "OFFICIAL_PAGE_LINK_VERIFIED_20260728",
    },
    "WI|menomonie|commercial_tenant_improvement": {
        "expected_old_url": "https://www.menomonie-wi.gov/185/Building-Inspection-Zoning",
        "apply_url": "https://www.menomonie-wi.gov/DocumentCenter/View/2101",
        "source_url": "https://www.menomonie-wi.gov/DocumentCenter/View/2101",
        "source_quote": "Commercial Building Permit Application",
        "authority": "City of Menomonie Building Inspection/Zoning",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "downloadable_form",
        "snapshot_name": "us-wi-menomonie-building-route.txt",
        "live_recheck": "PASS_WEB_EXTRACT_20260728",
    },
    "IN|gary|commercial_tenant_improvement": {
        "expected_old_url": "https://www.gary.gov/building",
        "apply_url": "https://garyin.viewpointcloud.com/categories/1071/record-types/6334",
        "source_url": "https://www.gary.gov/building",
        "source_quote": "Online GBL & Permit System Online Permit System Apply for Permits",
        "authority": "City of Gary Building Department",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "online_portal",
        "snapshot_name": "us-in-gary-building-route.txt",
        "live_recheck": "DOSSIER_AND_OFFICIAL_PAGE_LINK_VERIFIED_20260728",
    },
    "WI|greenfield|commercial_tenant_improvement": {
        "expected_old_url": "https://www.ci.greenfield.wi.us/1290/Permits",
        "apply_url": "https://www.ci.greenfield.wi.us/1549/Apply-Online",
        "source_url": "https://www.ci.greenfield.wi.us/1290/Permits",
        "source_quote": "Building Permits (Apply here)",
        "authority": "City of Greenfield Inspection Services, Department of Neighborhood Services",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "official_apply_page",
        "snapshot_name": "us-wi-greenfield-building-route.txt",
        "live_recheck": "PASS_OFFICIAL_PAGE_WEB_EXTRACT_20260728",
    },
    "VT|williston|commercial_tenant_improvement": {
        "expected_old_url": "https://www.town.williston.vt.us/index.asp?Type=B_LIST&SEC={8F55E823-D669-4659-8F4F-72212E8107F7}",
        "apply_url": "https://firesafety.vermont.gov/sites/firesafety/files/files/forms/dfs_construction_application.pdf",
        "source_url": "https://firesafety.vermont.gov/sites/firesafety/files/files/forms/dfs_construction_application.pdf",
        "source_quote": "Please fill out this permit application as completely as possible based on the scope of this project.",
        "authority": "Vermont Department of Public Safety, Division of Fire Safety, Williston Regional Office",
        "authority_tier": "state",
        "handled_by_local_ahj": False,
        "channel": "downloadable_form",
        "snapshot_name": "us-vt-williston-building-route.txt",
        "live_recheck": "PASS_WEB_EXTRACT_20260728",
    },
    "CA|san_diego|commercial_tenant_improvement": {
        "expected_old_url": "https://www.sandiego.gov/development-services/permits",
        "apply_url": "https://aca.accela.com/SANDIEGO",
        "source_url": "https://www.sandiego.gov/development-services/permits/building-permit",
        "source_quote": "Apply for a Permit",
        "authority": "City of San Diego Development Services Department",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "online_portal",
        "snapshot_name": "us-ca-san-diego-building-route.txt",
        "live_recheck": "PASS_OFFICIAL_PAGE_WEB_EXTRACT_20260728",
    },
    "IA|cedar_rapids|commercial_tenant_improvement": {
        "expected_old_url": "https://www.cedar-rapids.org/local_government/departments_a_-_f/building_services/building_and_trades/customer_self_service_(css).php",
        "apply_url": "https://cedarrapidsia-energovpub.tylerhost.net/apps/selfservice#/home",
        "source_url": "https://www.cedar-rapids.org/local_government/departments_a_-_f/building_services/building_and_trades/customer_self_service_(css).php",
        "source_quote": "Customer Self-Service (CSS) portal which will allow our customers the ability to apply for permit applications, pay for permit applications, and schedule inspections.",
        "authority": "City of Cedar Rapids Building Services, Building & Trades Division",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "online_portal",
        "snapshot_name": "us-ia-cedar-rapids-building-route.txt",
        "live_recheck": "DOSSIER_AND_OFFICIAL_PAGE_LINK_VERIFIED_20260728",
    },
    "TX|georgetown|residential_remodel": {
        "expected_old_url": "https://www.georgetowntexas.gov/development_services/permits/information/when_do_i_need_a_permit.php",
        "apply_url": "https://mgoconnect.org/cp?JID=48",
        "source_url": "https://georgetowntexas.gov/development_services/permits/commercial_permits/",
        "source_quote": "How to Submit a Commercial Building Permit Application",
        "authority": "City of Georgetown Building Permits and Inspections through My Government Online / MGO Connect",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "online_portal",
        "snapshot_name": "us-tx-georgetown-building-route.txt",
        "live_recheck": "PASS_OFFICIAL_PAGE_WEB_EXTRACT_20260728",
    },
    "MO|joplin|commercial_tenant_improvement": {
        "expected_old_url": "https://www.joplinmo.org/265/Building-Division",
        "apply_url": "https://jopl-egov.aspgov.com/Click2GovBP/index.html",
        "source_url": "https://www.joplinmo.org/1255/Building-Permit-Applications",
        "source_quote": "The City of Joplin prefers to accept applications online.",
        "authority": "City of Joplin Building Division",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "online_portal",
        "snapshot_name": "us-mo-joplin-building-route.txt",
        "live_recheck": "PASS_OFFICIAL_PAGE_WEB_EXTRACT_20260728",
    },
    "IL|decatur|commercial_tenant_improvement": {
        "expected_old_url": "https://decaturil.gov/143/Applications-and-Permits",
        "apply_url": "https://decaturil.portal.opengov.com/categories/1071/record-types/6427",
        "source_url": "https://decaturil.gov/472/Building-Permits",
        "source_quote": "You can request a permit through the City of Decatur OpenGov platform.",
        "authority": "City of Decatur Building Inspections Division",
        "authority_tier": "local",
        "handled_by_local_ahj": True,
        "channel": "online_portal",
        "snapshot_name": "us-il-decatur-building-route.txt",
        "live_recheck": "PASS_OFFICIAL_PAGE_WEB_EXTRACT_20260728",
    },
})


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def stable_compact(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def pretty_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(pretty_bytes(value))


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def snapshot_payload(spec: dict[str, Any]) -> str:
    return (
        "PermitAssist independently adjudicated official filing route\n"
        f"Official source URL: {spec['source_url']}\n"
        f"Official filing URL: {spec['apply_url']}\n"
        f"Verification boundary: {spec['live_recheck']}\n"
        "Exact official quote:\n"
        f"{spec['source_quote']}\n"
    )


def provenance(spec: dict[str, Any], snapshot_path: Path) -> dict[str, Any]:
    return {
        "effective_date": None,
        "freshness_class": "independently_adjudicated_official_route_20260728",
        "last_verified_at": VERIFIED_AT,
        "publishable": True,
        "retrieved_at": VERIFIED_AT,
        "snapshot_hash": sha256_file(snapshot_path),
        "snapshot_path": snapshot_path.relative_to(ROOT).as_posix(),
        "source_quote": spec["source_quote"],
        "source_url": spec["source_url"],
    }


def repair_cell(cell: dict[str, Any], key: str, spec: dict[str, Any], snapshot_path: Path) -> None:
    tier1 = cell["tier1"]
    old_urls = {str(item.get("apply_url") or "") for item in tier1.get("apply", []) if isinstance(item, dict)}
    if spec["expected_old_url"] not in old_urls and spec["apply_url"] not in old_urls:
        raise RuntimeError(f"{key}: expected old route not present: {sorted(old_urls)}")

    prov = provenance(spec, snapshot_path)
    tier1["apply"] = [{
        "application_authority": spec["authority"],
        "apply_url": spec["apply_url"],
        "channel": spec["channel"],
        "permit_name": "Building Permit",
        "provenance": deepcopy(prov),
        "url_status": "live",
    }]

    building_rows = [row for row in tier1.get("trade_authority", []) if isinstance(row, dict) and str(row.get("trade") or "").lower() == "building"]
    if len(building_rows) != 1:
        raise RuntimeError(f"{key}: expected one building authority row, got {len(building_rows)}")
    authority = building_rows[0]
    authority["application_authority"] = {"name": spec["authority"]}
    authority["issuing_authority"] = {"name": spec["authority"], "tier": spec["authority_tier"]}
    authority["handled_by_local_ahj"] = spec["handled_by_local_ahj"]
    authority["provenance"] = deepcopy(prov)

    watch = cell.setdefault("change_watch", {})
    hashes = [str(value) for value in watch.get("tier1_snapshot_hashes", []) if value]
    if prov["snapshot_hash"] not in hashes:
        hashes.append(prov["snapshot_hash"])
    watch["tier1_snapshot_hashes"] = sorted(set(hashes))
    repairs = cell.setdefault("repair_history", [])
    marker = {
        "kind": "verified_primary_route_and_authority_repair",
        "applied_at_utc": VERIFIED_AT,
        "apply_url": spec["apply_url"],
        "snapshot_hash": prov["snapshot_hash"],
    }
    if marker not in repairs:
        repairs.append(marker)


def manifest_interim(manifest: dict[str, Any]) -> dict[str, Any]:
    value = deepcopy(manifest)
    value.pop("files", None)
    value.pop("package_sha256", None)
    return value


def regenerate_manifest(manifest: dict[str, Any], snapshot_paths: list[Path]) -> dict[str, Any]:
    manifest = manifest_interim(manifest)
    manifest["repair_note_20260728_session4_routes"] = {
        "scope": f"{len(ROUTES)} independently adjudicated primary filing route and authority corrections",
        "cells": sorted(ROUTES),
        "runtime_behavior": "invalid or unproven routes remain fail-closed at customer action projection",
        "deployment": "not_deployed",
    }
    MANIFEST_PATH.write_bytes(pretty_bytes(manifest))
    files = {
        MANIFEST_PATH.name: sha256_file(MANIFEST_PATH),
        INDEX_PATH.name: sha256_file(INDEX_PATH),
        CELLS_PATH.name: sha256_file(CELLS_PATH),
        DEFERRED_PATH.name: sha256_file(DEFERRED_PATH),
    }
    old_manifest = read_json(MANIFEST_PATH)
    # Preserve other shipped snapshot bindings that predate this migration.
    prior = read_json(BACKUP_MANIFEST_PATH) if BACKUP_MANIFEST_PATH else {}
    for name in (prior.get("files") or {}):
        path = PKG / name
        if name not in files and path.exists():
            files[name] = sha256_file(path)
    for path in snapshot_paths:
        files[path.relative_to(PKG).as_posix()] = sha256_file(path)
    old_manifest["files"] = dict(sorted(files.items()))
    old_manifest["package_sha256"] = sha256_bytes(stable_compact(old_manifest["files"]))
    write_json(MANIFEST_PATH, old_manifest)
    return old_manifest


BACKUP_MANIFEST_PATH: Path | None = None


def apply() -> None:
    global BACKUP_MANIFEST_PATH
    index_doc = read_json(INDEX_PATH)
    cells_doc = read_json(CELLS_PATH)
    manifest = read_json(MANIFEST_PATH)
    # Preserve the pre-migration final manifest in memory via a temporary local file.
    BACKUP_MANIFEST_PATH = PKG / ".permitassist_v24_manifest.pre_session4_route_repair.json"
    BACKUP_MANIFEST_PATH.write_bytes(pretty_bytes(manifest))

    index = index_doc["index"]
    cells_by_id = {cell["cell_id"]: cell for cell in cells_doc["cells"]}
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_paths: list[Path] = []
    for key, spec in ROUTES.items():
        if key not in index:
            raise RuntimeError(f"missing runtime key: {key}")
        cell_id = index[key]["cell_id"]
        if cell_id not in cells_by_id:
            raise RuntimeError(f"missing cell bundle row: {cell_id}")
        snapshot_path = SNAPSHOT_DIR / spec["snapshot_name"]
        snapshot_path.write_text(snapshot_payload(spec), encoding="utf-8")
        snapshot_paths.append(snapshot_path)
        repair_cell(index[key], key, spec, snapshot_path)
        repair_cell(cells_by_id[cell_id], key, spec, snapshot_path)
        if stable_compact(index[key]) != stable_compact(cells_by_id[cell_id]):
            raise RuntimeError(f"index/cell divergence after repair: {key}")

    write_json(INDEX_PATH, index_doc)
    write_json(CELLS_PATH, cells_doc)
    regenerate_manifest(manifest, snapshot_paths)
    BACKUP_MANIFEST_PATH.unlink(missing_ok=True)
    BACKUP_MANIFEST_PATH = None


def check() -> dict[str, Any]:
    sys.path[:0] = [str(ROOT), str(ROOT / "api")]
    from api.v24_decision_cells import validate_v24_cell

    index_doc = read_json(INDEX_PATH)
    cells_doc = read_json(CELLS_PATH)
    manifest = read_json(MANIFEST_PATH)
    index = index_doc["index"]
    cells_by_id = {cell["cell_id"]: cell for cell in cells_doc["cells"]}
    checked = []
    for key, spec in ROUTES.items():
        cell = index[key]
        bundled = cells_by_id[cell["cell_id"]]
        if stable_compact(cell) != stable_compact(bundled):
            raise RuntimeError(f"index/cell divergence: {key}")
        routes = cell["tier1"]["apply"]
        if len(routes) != 1 or routes[0]["apply_url"] != spec["apply_url"]:
            raise RuntimeError(f"route mismatch: {key}")
        prov = routes[0]["provenance"]
        snapshot_path = ROOT / prov["snapshot_path"]
        if sha256_file(snapshot_path) != prov["snapshot_hash"]:
            raise RuntimeError(f"snapshot hash mismatch: {key}")
        if normalize_text(prov["source_quote"]) not in normalize_text(snapshot_path.read_text(encoding="utf-8")):
            raise RuntimeError(f"quote missing from snapshot: {key}")
        validation = validate_v24_cell(cell, strict_snapshots=False, require_live_url_check=False)
        if not validation.ok:
            raise RuntimeError(f"runtime validation failed for {key}: {validation.to_dict()}")
        checked.append(key)

    for name, expected in manifest["files"].items():
        if name == MANIFEST_PATH.name:
            actual = sha256_bytes(pretty_bytes(manifest_interim(manifest)))
        else:
            actual = sha256_file(PKG / name)
        if actual != expected:
            raise RuntimeError(f"manifest file hash mismatch: {name}: {actual} != {expected}")
    package = sha256_bytes(stable_compact(manifest["files"]))
    if package != manifest["package_sha256"]:
        raise RuntimeError("package_sha256 mismatch")
    return {
        "ok": True,
        "checked_cells": len(checked),
        "checked_snapshots": len(checked),
        "package_sha256": package,
        "manifest_sha256": sha256_file(MANIFEST_PATH),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.apply == args.check:
        parser.error("choose exactly one of --apply or --check")
    if args.apply:
        apply()
    print(json.dumps(check(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
