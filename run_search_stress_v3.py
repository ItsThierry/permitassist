import re
from pathlib import Path
from statistics import mean

from api import research_engine as eng

TEST_CASES = [
    ("roofing permit", "Paragould", "AR", "", "state"),
    ("generator installation", "Unincorporated Harris County", "TX", "", "state"),
    ("EV charger installation", "Burlington", "VT", "", "state"),
    ("electrical panel upgrade", "Enumclaw", "WA", "", "state"),
    ("plumbing rough-in", "Paducah", "KY", "", "state"),
    ("HVAC replacement", "Gallup", "NM", "", "state"),
    ("fence permit", "Key West", "FL", "", "state"),
    ("electrical permit", "Laramie", "WY", "", "state"),
    ("plumbing permit", "Kenai", "AK", "", "state"),
    ("roofing permit", "Biloxi", "MS", "", "state"),
    ("water heater replacement", "Boise", "ID", "", "state"),
    ("deck permit", "Spokane", "WA", "", "state"),
    ("HVAC replacement", "Provo", "UT", "", "state"),
    ("solar panel installation", "Yuma", "AZ", "", "city"),
    ("HVAC replacement", "Shreveport", "LA", "", "state"),
    ("roofing permit", "Anchorage", "AK", "", "state"),
    ("electrical permit", "Virginia Beach", "VA", "", "state"),
    ("deck permit", "Cheyenne", "WY", "", "state"),
    ("solar permit", "Flagstaff", "AZ", "", "state"),
    ("water heater replacement", "Fargo", "ND", "", "state"),
]


def score_result(result: dict) -> tuple[int, dict]:
    phone = 2 if re.search(r'\d{3}.*\d{4}', str(result.get('apply_phone', ''))) else 0
    portal = 2 if str(result.get('apply_url') or '').startswith('http') else 0
    fee = 2 if '$' in str(result.get('fee_range') or '') else 0
    addr = 2 if re.search(r'\d+ .*\b(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court|Pkwy|Parkway)\b', str(result.get('apply_address') or '')) else (1 if result.get('apply_address') else 0)
    junk = 2
    noisy_fields = [result.get('apply_phone', ''), result.get('apply_address', ''), result.get('fee_range', '')]
    if any(any(tok in str(v).lower() for tok in ['copyright', 'privacy', 'click to', 'all rights reserved']) for v in noisy_fields):
        junk = 0
    total = phone + portal + fee + addr + junk
    return total, {'phone': phone, 'portal': portal, 'fees': fee, 'address': addr, 'no_junk': junk}


def layer_fired(city, state, city_match_level):
    search_city, search_state, _ = eng.normalize_jurisdiction(city, state)
    payload = eng.get_search_cache(search_city, search_state, city_match_level)
    if not payload:
        return 'unknown'
    results = payload.get('results') or []
    if not results:
        return payload.get('structured', {}).get('source') or 'unknown'
    return results[0].get('source_layer') or payload.get('structured', {}).get('source') or 'unknown'


out_lines = []
scores = []
low = []
for idx, (job, city, state, zip_code, match_level) in enumerate(TEST_CASES, 1):
    search_city, search_state, _ = eng.normalize_jurisdiction(city, state)
    eng.delete_search_cache(search_city, search_state)
    result = eng.research_permit(job, city, state, zip_code, use_cache=False)
    payload = eng.get_search_cache(search_city, search_state, city_match_level=match_level)
    layer = layer_fired(city, state, match_level)
    structured = (payload or {}).get('structured', {})
    score, breakdown = score_result(result)
    scores.append(score)
    auto_kb = bool((payload or {}).get('auto_kb_updated'))
    if score < 6:
        low.append((job, city, state, score))
    out_lines.append(f'[{idx}] {job} | {city}, {state}')
    out_lines.append(f'Layer fired: {layer}')
    out_lines.append('STRUCTURED PERMIT DATA:')
    out_lines.append(f"Phone: {structured.get('phone') or result.get('apply_phone')}")
    out_lines.append(f"Portal: {structured.get('portal_url') or result.get('apply_url')}")
    out_lines.append(f"Fees: {', '.join(structured.get('fees') or []) or result.get('fee_range')}")
    out_lines.append(f"Address: {structured.get('address') or result.get('apply_address')}")
    out_lines.append(f"Conflicts: {' | '.join(structured.get('conflicts') or []) or 'none'}")
    out_lines.append(f"Freshness: {structured.get('freshness') or 'n/a'}")
    out_lines.append(f"Auto-KB-update: {'yes' if auto_kb else 'no'}")
    out_lines.append(f'Score: {score}/10 ({breakdown})')
    out_lines.append('')

out_lines.append('SUMMARY')
out_lines.append(f'Average score: {mean(scores):.2f}/10')
out_lines.append('Per-city scores: ' + ', '.join(str(s) for s in scores))
out_lines.append('Low scorers: ' + (', '.join(f'{city} {state}={score}' for _, city, state, score in low) if low else 'none'))

path = Path('/data/permitassist/search_stress_test_v3.txt')
path.write_text('\n'.join(out_lines))
print(path)
print(f'Average={mean(scores):.2f}')
print(f'Low={low}')
