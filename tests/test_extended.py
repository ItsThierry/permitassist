#!/usr/bin/env python3
"""
PermitAssist — Extended Test Suite
Tests edge cases, tricky scenarios, rural areas, permit-not-required cases,
multi-permit jobs, and geographic extremes.
"""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from api.research_engine import research_permit, format_for_display

TESTS = [
    # ── EDGE CASE: Job that might NOT need a permit ──
    {"label": "Like-for-like water heater (might not need permit)", 
     "job": "replace water heater same size same location", "city": "Nashville", "state": "TN"},
    
    # ── EDGE CASE: Rural county (less regulation) ──
    {"label": "Rural county HVAC",
     "job": "HVAC replacement", "city": "Amarillo", "state": "TX"},
    
    # ── EDGE CASE: High-regulation city ──
    {"label": "San Francisco (notoriously strict)",
     "job": "electrical panel upgrade 200 amp", "city": "San Francisco", "state": "CA"},
    
    # ── EDGE CASE: Multi-permit job ──
    {"label": "Kitchen remodel (multiple permits needed)",
     "job": "kitchen remodel with new plumbing electrical and gas line", "city": "Dallas", "state": "TX"},
    
    # ── EDGE CASE: EV charger (newer permit type) ──
    {"label": "EV charger installation",
     "job": "Level 2 EV charger installation 240v", "city": "Sacramento", "state": "CA"},
    
    # ── EDGE CASE: Solar panels ──
    {"label": "Solar panel installation",
     "job": "rooftop solar panel installation 10kW", "city": "Phoenix", "state": "AZ"},

    # ── EDGE CASE: Small town ──
    {"label": "Small town Indiana",
     "job": "furnace replacement", "city": "Brazil", "state": "IN"},
    
    # ── EDGE CASE: Generator ──
    {"label": "Standby generator hookup",
     "job": "whole house standby generator installation with transfer switch", "city": "Charlotte", "state": "NC"},
    
    # ── EDGE CASE: Deck ──
    {"label": "Deck addition",
     "job": "attached deck addition 400 sq ft", "city": "Denver", "state": "CO"},
    
    # ── EDGE CASE: Vague job description ──
    {"label": "Vague job description handling",
     "job": "fix HVAC", "city": "Miami", "state": "FL"},
]

def run_test(tc, i, total):
    print(f"\n{'─'*60}")
    print(f"TEST {i}/{total}: {tc['label']}")
    print(f"Job: {tc['job']} | {tc['city']}, {tc['state']}")
    print(f"{'─'*60}")
    
    start = time.time()
    errors = []
    warnings = []
    
    try:
        result = research_permit(tc["job"], tc["city"], tc["state"], use_cache=False)
        elapsed = round(time.time() - start, 1)
        
        # Structural checks
        if not isinstance(result.get("permits_required"), list):
            errors.append("permits_required is not a list")
        if not result.get("applying_office"):
            warnings.append("No applying_office")
        if not result.get("fee_range"):
            warnings.append("No fee_range")
        if not result.get("inspections"):
            warnings.append("No inspections listed")
        if result.get("confidence") not in ["high", "medium", "low"]:
            errors.append(f"Bad confidence value: {result.get('confidence')}")
        if not result.get("disclaimer"):
            warnings.append("No disclaimer")
        
        # Content quality checks
        permits = result.get("permits_required", [])
        for p in permits:
            if not p.get("permit_type"):
                errors.append("Permit missing permit_type")
            if p.get("required") not in [True, False, "maybe"]:
                warnings.append(f"Unexpected required value: {p.get('required')}")
        
        print(format_for_display(result))
        
        cached = result.get("_cached") or result.get("_meta", {}).get("cached", False)
        print(f"\n✅ {elapsed}s {'(cached)' if cached else '(live)'} | Errors: {len(errors)} | Warnings: {len(warnings)}")
        if errors:
            for e in errors: print(f"  ❌ {e}")
        if warnings:
            for w in warnings: print(f"  ⚠️  {w}")
        
        return len(errors) == 0
        
    except Exception as ex:
        elapsed = round(time.time() - start, 1)
        print(f"❌ EXCEPTION in {elapsed}s: {ex}")
        import traceback; traceback.print_exc()
        return False

def main():
    print("🧪 PermitAssist — Extended Edge Case Test Suite")
    print(f"Running {len(TESTS)} edge case tests...\n")
    
    passed = failed = 0
    for i, tc in enumerate(TESTS, 1):
        ok = run_test(tc, i, len(TESTS))
        if ok: passed += 1
        else: failed += 1
        if i < len(TESTS):
            time.sleep(1)
    
    print(f"\n{'='*60}")
    print(f"RESULTS: {passed} passed, {failed} failed out of {passed+failed}")
    print("🎉 ALL PASSED" if failed == 0 else f"⚠️  {failed} failed")
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
