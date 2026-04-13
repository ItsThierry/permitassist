#!/usr/bin/env python3
"""
Data quality cleanup for verified_cities.json
Fixes:
1. Nonsensical fee ranges (project costs mistaken for permit fees)
2. Mismatched source URLs
3. Scrape artifacts in summaries
4. Empty phone numbers (extract from summary)
5. Malformed phone numbers (normalize format)
"""

import json
import re
import sys
from urllib.parse import urlparse

INPUT_FILE = '/data/permitassist/data/verified_cities.json'
OUTPUT_FILE = '/data/permitassist/data/verified_cities.json'

# ─── Counters ────────────────────────────────────────────────────────────────
fixes = {
    'fee_range_cleared': [],
    'source_url_fixed': [],
    'summary_cleaned': [],
    'phone_extracted': [],
    'phone_normalized': [],
    'trailing_comma_fixed': [],
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def extract_first_dollar_amount(fee_str):
    """Return the first numeric value in a fee string, ignoring commas."""
    m = re.search(r'\$?([\d,]+)', fee_str)
    if m:
        try:
            return int(m.group(1).replace(',', ''))
        except ValueError:
            pass
    return None


def normalize_phone(phone):
    """Normalize a phone number to (NXX) NXX-XXXX format."""
    # Strip all non-digit characters
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 10:
        return f'({digits[:3]}) {digits[3:6]}-{digits[6:]}'
    elif len(digits) == 11 and digits[0] == '1':
        return f'({digits[1:4]}) {digits[4:7]}-{digits[7:]}'
    return phone  # Can't parse — leave as-is


def is_malformed_phone(phone):
    """Return True if phone has dot-separators or missing closing paren."""
    if re.search(r'\d{3}\.\d{3}\.\d{4}', phone):
        return True
    if re.search(r'\(\d{3}\.', phone):
        return True
    # Missing closing paren like "(407 246-4444"
    if re.match(r'\(\d{3}\s+\d{3}', phone) and ')' not in phone:
        return True
    return False


def url_matches_city(url, city, state):
    """Heuristic: does the URL plausibly belong to this city/state?"""
    if not url:
        return True  # No URL = no mismatch
    parsed = urlparse(url.lower())
    domain = parsed.netloc.replace('www.', '')
    path = parsed.path.lower()
    full = domain + path

    city_words = [w for w in re.split(r'\W+', city.lower()) if len(w) > 3]
    state_lower = state.lower()

    # State abbreviation in domain/path is a good sign
    # But we rely more on the city name
    for w in city_words:
        if w in full:
            return True

    # Check state postal code in path/domain (e.g. ".wa.gov", "/ne/")
    if f'.{state_lower}.' in full or f'/{state_lower}/' in full or f'.{state_lower}.gov' in full:
        return True

    # Generic third-party permit info sites are OK
    generic_domains = [
        'permitplace.com', 'permitflow.com', 'permitmint.com',
        'startpermit.com', 'myshyft.com', 'designblendz.com',
        'bluebook.com', 'municode.com', 'ecode360.com', 'amlegal.com',
    ]
    if any(g in domain for g in generic_domains):
        return True

    return False  # Could not confirm a match → flag as mismatch


def find_matching_source(city, state, sources):
    """Find first URL in sources that matches the city/state."""
    for url in sources:
        if url_matches_city(url, city, state):
            return url
    return None


def clean_summary_artifacts(summary):
    """
    Remove leading navigation/menu garbage from summary text.
    Returns (cleaned_summary, was_modified).
    """
    if not summary:
        return summary, False

    original = summary

    # Pattern 1: "Footer menu" navigation leftovers mid-text
    # Keep content before "Footer menu" only if there's useful content there
    footer_match = re.search(r'Footer menu\.\s*Help\b', summary)
    if footer_match:
        before = summary[:footer_match.start()].strip()
        after_idx = footer_match.end()
        # Find the next substantive sentence after the nav items
        # Nav ends roughly after "FAQ · Live Chat · Codes · How-To-Guides · Fee Schedule"
        nav_end = re.search(r'Fee Schedule\s+', summary[after_idx:])
        if nav_end:
            rest = summary[after_idx + nav_end.end():].strip()
            if before:
                cleaned = before + ' ' + rest if rest else before
            else:
                cleaned = rest
            summary = cleaned.strip()

    # Pattern 2: Summary starts with "[Skip to content](...)" — Markdown link artifact
    if summary.startswith('[Skip to content]') or summary.startswith('Skip to content'):
        # Try to find first real content after markdown links/nav
        # Remove leading markdown links like [text](url)
        cleaned = re.sub(r'^\s*\[.*?\]\(.*?\)\s*', '', summary, flags=re.DOTALL)
        # Remove leading asterisk list items (nav menus)
        cleaned = re.sub(r'^(\s*\*\s+\S+\s*)+', '', cleaned, flags=re.DOTALL).strip()
        if cleaned and len(cleaned) > 50:
            summary = cleaned

    # Pattern 3: Cookie notice at start
    if re.match(r'(We use cookies|This site uses cookies|Cookie)', summary[:100], re.I):
        # Find content after cookie notice
        after = re.search(r'(Permit|Building|Trade|Electrical|Plumbing|HVAC|Roofing|Fee)', summary[100:], re.I)
        if after:
            summary = summary[100 + after.start():].strip()

    return summary, summary != original


def extract_phone_from_text(text):
    """Extract a US phone number from free text."""
    patterns = [
        r'\(?\d{3}\)?\s*[-.\s]\s*\d{3}\s*[-.\s]\s*\d{4}',
        r'\d{3}[-.\s]\d{3}[-.\s]\d{4}',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            raw = m.group()
            normalized = normalize_phone(raw)
            # Validate it normalized correctly
            if re.match(r'\(\d{3}\) \d{3}-\d{4}', normalized):
                return normalized
    return None


# ─── Main cleanup ─────────────────────────────────────────────────────────────

def cleanup(data):
    for key, entry in data.items():
        city = entry['city']
        state = entry['state']
        d = entry['data']
        sources = d.get('sources', [])

        # ── 1. Fix nonsensical fee ranges ────────────────────────────────────
        fee_range = d.get('fee_range', '')
        if fee_range:
            # Strip trailing commas/spaces (artifact)
            fee_stripped = fee_range.rstrip(' ,')
            if fee_stripped != fee_range:
                d['fee_range'] = fee_stripped
                fee_range = fee_stripped
                fixes['trailing_comma_fixed'].append(key)

            first_val = extract_first_dollar_amount(fee_range)
            if first_val is not None and first_val > 1000:
                # Double-check: if it looks like a range with a low starting value, keep it
                # e.g. "$50 - $5,000" → first_val is 50, fine
                # "$7,001" → first_val is 7001, clear
                # "$200-$1,500" → first_val is 200, keep
                d['fee_range'] = ''
                fixes['fee_range_cleared'].append((key, fee_range))

        # ── 2. Fix mismatched source URLs ─────────────────────────────────────
        source_url = entry.get('source_url', '')
        if source_url and not url_matches_city(source_url, city, state):
            # Find a better URL from sources list
            replacement = find_matching_source(city, state, sources)
            if replacement:
                entry['source_url'] = replacement
                fixes['source_url_fixed'].append((key, source_url, replacement))
            else:
                # Fall back to first source if any
                if sources:
                    entry['source_url'] = sources[0]
                    fixes['source_url_fixed'].append((key, source_url, sources[0]))

        # ── 3. Clean scrape artifacts from summaries ──────────────────────────
        summary = d.get('summary', '')
        if summary:
            cleaned, modified = clean_summary_artifacts(summary)
            if modified:
                d['summary'] = cleaned
                fixes['summary_cleaned'].append(key)

        # ── 4. Fix empty phone numbers ─────────────────────────────────────────
        phone = d.get('phone', '')
        if not phone:
            summary_text = d.get('summary', '')
            extracted = extract_phone_from_text(summary_text)
            if extracted:
                d['phone'] = extracted
                fixes['phone_extracted'].append((key, extracted))

        # ── 5. Fix malformed phone numbers ────────────────────────────────────
        phone = d.get('phone', '')
        if phone and is_malformed_phone(phone):
            normalized = normalize_phone(phone)
            if normalized != phone:
                d['phone'] = normalized
                fixes['phone_normalized'].append((key, phone, normalized))

    return data


# ─── Run ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print(f'Loading {INPUT_FILE}...')
    with open(INPUT_FILE, 'r') as f:
        data = json.load(f)

    print(f'Loaded {len(data)} entries')

    data = cleanup(data)

    # Verify no entries were lost
    assert len(data) == 470, f'Entry count changed! Expected 470, got {len(data)}'

    print(f'\nWriting cleaned data to {OUTPUT_FILE}...')
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print('\n' + '='*60)
    print('CLEANUP SUMMARY')
    print('='*60)
    print(f'\n1. Fee ranges cleared (were project costs): {len(fixes["fee_range_cleared"])}')
    for key, old_val in fixes['fee_range_cleared']:
        print(f'   {key}: {old_val!r} → ""')

    print(f'\n1a. Trailing comma artifacts fixed: {len(fixes["trailing_comma_fixed"])}')
    for key in fixes['trailing_comma_fixed']:
        print(f'   {key}')

    print(f'\n2. Source URLs fixed (mismatched city): {len(fixes["source_url_fixed"])}')
    for key, old_url, new_url in fixes['source_url_fixed']:
        print(f'   {key}:')
        print(f'     OLD: {old_url}')
        print(f'     NEW: {new_url}')

    print(f'\n3. Summaries cleaned (scrape artifacts removed): {len(fixes["summary_cleaned"])}')
    for key in fixes['summary_cleaned']:
        print(f'   {key}')

    print(f'\n4. Phone numbers extracted from summary: {len(fixes["phone_extracted"])}')
    for key, phone in fixes['phone_extracted']:
        print(f'   {key}: {phone}')

    print(f'\n5. Phone numbers normalized: {len(fixes["phone_normalized"])}')
    for key, old_p, new_p in fixes['phone_normalized']:
        print(f'   {key}: {old_p!r} → {new_p!r}')

    total = sum(len(v) for v in fixes.values())
    print(f'\nTOTAL FIXES: {total}')
    print('='*60)
