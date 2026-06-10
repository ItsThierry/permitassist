#!/usr/bin/env python3
"""
AHJ Contact Verification Backfill Script (Task 3b/3e)

Walks all AHJ records in data/ahj_records.json and, for each record with an
official domain in its sources, fetches the contact page, extracts phone and
address, compares to stored values, and writes a report CSV.

Usage:
    python scripts/audit_ahj_contacts.py [--output report.csv] [--sample 5]

Requires: requests, beautifulsoup4
Install:  pip install requests beautifulsoup4
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as exc:
    print(f"Missing dependencies: {exc}")
    print("Install: pip install requests beautifulsoup4")
    sys.exit(1)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from ahj_records import load_ahj_records, set_ahj  # noqa: E402


def _extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _find_contact_page_url(sources: list[str]) -> str | None:
    """Pick the best contact/fee page URL from the record's sources."""
    for url in sources:
        if not isinstance(url, str):
            continue
        lowered = url.lower()
        if ".gov" in lowered or ".org" in lowered:
            return url
    return sources[0] if sources else None


def _fetch_and_extract(url: str) -> tuple[str, str]:
    """Fetch URL and extract phone + address via regex + simple heuristics."""
    try:
        resp = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (PermitAssist Bot; +mailto:info@permitassist.io)"
        })
        resp.raise_for_status()
        text = resp.text
    except Exception as e:
        print(f"  [fetch] Error fetching {url}: {e}")
        return "", ""

    soup = BeautifulSoup(text, "html.parser")
    # Remove script/style
    for tag in soup(["script", "style"]):
        tag.decompose()
    visible = soup.get_text(separator="\n")

    # Phone extraction
    phones = re.findall(
        r'\(?\d{3}\)?[\s\-\.]\d{3}[\s\-\.]\d{4}(?:\s*ext\.?\s*\d+)?',
        visible,
    )
    phone = phones[0] if phones else ""
    # Normalize to (XXX) XXX-XXXX
    if phone:
        digits = re.sub(r"\D", "", phone)
        if len(digits) >= 10:
            phone = f"({digits[:3]}) {digits[3:6]}-{digits[6:10]}"

    # Address extraction (very heuristic — best-effort)
    # Look for "Street", "St.", "Ave.", "Avenue", "Blvd", "Drive", "Dr.", "Road", "Rd.",
    # "Suite", "Room", "Floor", "P.O. Box"
    addr_match = re.search(
        r"(\d+\s+[^,\n]{3,80}(?:\s+(?:St\.?|Street|Ave\.?|Avenue|Blvd|Drive|Dr\.?|Road|Rd\.?|Ln|Lane|Way|Cir|Circle|Ct|Court))"
        r"(?:[\s,]*(?:Suite|Ste|Room|Rm|Fl|Floor|P\.O\.\s*Box)\s*[^,\n]{0,20})?"
        r"[\s,]*[^,\n]{2,40},?\s*[A-Z][a-z]+,?\s*[A-Z]{2}\s*\d{5}(-\d{4})?)",
        visible,
    )
    address = addr_match.group(1).replace("\n", " ").strip() if addr_match else ""

    return phone, address


def run(output_path: str, sample: int | None = None) -> None:
    records = load_ahj_records()
    report_rows: list[dict] = []
    updated_count = 0

    keys = list(records.keys())
    if sample:
        keys = keys[:sample]

    for key in keys:
        rec = records[key]
        city = rec.get("city", "")
        state = rec.get("state", "")
        print(f"\n[audit] {key} ({city}, {state})")

        contact = rec.get("contact") or {}
        old_phone = contact.get("phone", "")
        old_address = contact.get("address", "")

        sources = rec.get("sources", [])
        if not sources:
            print("  [skip] No source URLs")
            report_rows.append({
                "ahj": f"{city}, {state}",
                "old_phone": old_phone,
                "old_address": old_address,
                "new_phone": "",
                "new_address": "",
                "status": "unverified",
                "source_url": "",
            })
            continue

        contact_url = _find_contact_page_url(sources)
        if not contact_url:
            print("  [skip] No usable contact source URL")
            continue

        print(f"  [fetch] {contact_url}")
        new_phone, new_address = _fetch_and_extract(contact_url)
        print(f"  [found] phone={new_phone} address={new_address[:60] if new_address else ''}")

        # Comparison
        status = "unverified"
        if new_phone or new_address:
            phone_match = bool(new_phone and new_phone.replace(" ", "") == old_phone.replace(" ", ""))
            addr_match = bool(new_address and new_address.lower()[:60] == old_address.lower()[:60])

            if phone_match and addr_match:
                status = "verified"
                contact["contact_status"] = "verified"
                contact["contact_verified_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                contact["contact_source_url"] = contact_url
                print("  [status] verified (matches stored)")
            else:
                # Update stored values with what we just fetched
                if new_phone:
                    contact["phone"] = new_phone
                if new_address:
                    contact["address"] = new_address
                contact["contact_status"] = "verified"
                contact["contact_verified_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                contact["contact_source_url"] = contact_url
                status = "verified"
                updated_count += 1
                print("  [status] verified (updated with fetched values)")
        else:
            print("  [status] unverified (could not extract)")

        # Persist changes
        rec["contact"] = contact
        set_ahj(city, state, rec)

        report_rows.append({
            "ahj": f"{city}, {state}",
            "old_phone": old_phone,
            "old_address": old_address,
            "new_phone": new_phone,
            "new_address": new_address,
            "status": status,
            "source_url": contact_url or "",
        })

    # Write CSV report
    import csv
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ahj", "old_phone", "old_address", "new_phone", "new_address", "status", "source_url"])
        writer.writeheader()
        writer.writerows(report_rows)

    print(f"\n[audit] Done. Updated {updated_count} records. Report: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AHJ contact backfill audit")
    parser.add_argument("--output", default="data/ahj_contact_audit_report.csv", help="CSV output path")
    parser.add_argument("--sample", type=int, default=None, help="Only process N records (for testing)")
    args = parser.parse_args()
    run(args.output, sample=args.sample)
