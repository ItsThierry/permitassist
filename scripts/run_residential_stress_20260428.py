#!/usr/bin/env python3
"""Residential stress test — 5 GTM cities x 3 scopes = 15 scenarios.
Uses local engine (api.research_engine.research_permit) directly.
"""
import json, sys, os, traceback
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'api'))  # so research_engine's `from hidden_trigger_detector import ...` resolves
from api.research_engine import research_permit  # noqa: E402

OUT = ROOT / 'eval' / 'stress-test-2026-04-28-residential'
OUT.mkdir(parents=True, exist_ok=True)

# city_slug, city, state, zip, job_value(roughly)
CITIES = [
    ('los-angeles', 'Los Angeles', 'CA', '90042'),
    ('phoenix',     'Phoenix',      'AZ', '85016'),
    ('clark-county-nv', 'Las Vegas',  'NV', '89118'),  # mailing-Vegas typically Clark County
    ('seattle',     'Seattle',      'WA', '98103'),
    ('dallas',      'Dallas',       'TX', '75214'),
]

# Scope mapping — Scope A (ADU/large), Scope B (mid), Scope C (small/single trade)
SCOPES = {
    'los-angeles': {
        'A': ('hillside-adu', 'Hillside lot detached ADU 800 sf with attached 280 sf addition to main house, plus permit legalization for unpermitted bathroom in existing house. VHFHSZ likely.', 200000),
        'B': ('garage-conversion-jadu', 'Garage conversion of existing 400 sf attached garage to a JADU (junior accessory dwelling unit) with kitchenette, bathroom, separate entrance.', 80000),
        'C': ('kitchen-remodel-panel', 'Kitchen remodel including new cabinets, countertops, electrical and plumbing relocation, plus 200A main service panel upgrade.', 50000),
    },
    'phoenix': {
        'A': ('detached-adu', 'Detached accessory dwelling unit 750 sf, new construction on existing single-family lot.', 175000),
        'B': ('water-heater', 'Water heater changeout, replace 50 gallon gas tank water heater with new tankless gas water heater, same location.', 4500),
        'C': ('hvac-changeout', 'HVAC changeout — replace existing 3-ton split system air conditioner and gas furnace, like-for-like, no ductwork changes.', 9500),
    },
    'clark-county-nv': {
        'A': ('detached-adu', 'Detached accessory dwelling unit 750 sf on single-family residential lot.', 175000),
        'B': ('patio-cover', 'Patio cover 200 sf attached to rear of house, lattice/solid wood post-and-beam, no electrical.', 6000),
        'C': ('reroof', 'Reroof of existing 2,400 sf single-family home, asphalt shingles to asphalt shingles, like-for-like, no structural changes.', 14000),
    },
    'seattle': {
        'A': ('detached-adu', 'Detached accessory dwelling unit (DADU) 800 sf on single-family residential lot.', 220000),
        'B': ('kitchen-remodel-structural', 'Kitchen remodel including removal of an interior load-bearing wall between kitchen and dining room, with new structural beam, plus new cabinets, plumbing relocation, electrical updates.', 75000),
        'C': ('deck-2nd-floor', 'New deck 240 sf attached to second-floor wall of existing single-family house, accessed from upstairs bedroom.', 18000),
    },
    'dallas': {
        'A': ('detached-adu', 'Detached accessory dwelling unit 800 sf on single-family lot.', 200000),
        'B': ('foundation-repair', 'Foundation repair on pier-and-beam single-family house — replace deteriorated piers, level structure, no addition.', 22000),
        'C': ('window-replacement', 'Window replacement — replace 12 existing windows like-for-like, no structural changes, no header changes, no opening enlargement.', 8500),
    },
}

ORDER = ['A', 'B', 'C']

def main():
    scenarios = []
    for slug, city, state, zipc in CITIES:
        for letter in ORDER:
            stub, desc, jv = SCOPES[slug][letter]
            scenarios.append((slug, city, state, zipc, letter, stub, desc, jv))

    print(f"[run] {len(scenarios)} scenarios", flush=True)
    for i, (slug, city, state, zipc, letter, stub, desc, jv) in enumerate(scenarios, 1):
        out = OUT / f'{slug}-{letter}-{stub}.json'
        if out.exists() and out.stat().st_size > 200:
            print(f"[skip {i}/{len(scenarios)}] {out.name}", flush=True)
            continue
        print(f"[run {i}/{len(scenarios)}] {city} {letter} {stub}", flush=True)
        try:
            res = research_permit(
                desc, city, state,
                zip_code=zipc,
                use_cache=False,
                job_category='residential',
                job_value=jv,
            )
        except Exception as e:
            res = {
                '_error': type(e).__name__ + ': ' + str(e),
                '_traceback': traceback.format_exc(),
                '_input': {'desc': desc, 'city': city, 'state': state, 'zip': zipc, 'jv': jv},
            }
            print(f"[err] {e}", flush=True)
        out.write_text(json.dumps(res, indent=2, ensure_ascii=False))
        print(f"[saved] {out.name}", flush=True)

if __name__ == '__main__':
    main()
