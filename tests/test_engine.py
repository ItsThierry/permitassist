#!/usr/bin/env python3
"""
PermitPilot — Comprehensive Test Suite
Tests 20 real permit scenarios across different trades, states, and edge cases.
"""

import sys
import os
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.research_engine import research_permit, format_for_display

TEST_CASES = [
    # Core trades, major cities
    {"job": "roof replacement", "city": "Houston", "state": "TX", "zip": "77001"},
    {"job": "HVAC system replacement", "city": "Phoenix", "state": "AZ", "zip": "85001"},
    {"job": "200 amp electrical panel upgrade", "city": "Minneapolis", "state": "MN", "zip": "55401"},
    {"job": "water heater replacement gas", "city": "Los Angeles", "state": "CA", "zip": "90001"},
    {"job": "new plumbing rough-in bathroom addition", "city": "Portland", "state": "OR", "zip": "97201"},
    # Smaller cities/different states
    {"job": "electrical panel upgrade 200 amp", "city": "Indianapolis", "state": "IN", "zip": "46201"},
    {"job": "new HVAC installation", "city": "Denver", "state": "CO", "zip": "80201"},
    {"job": "roof replacement shingles", "city": "Atlanta", "state": "GA", "zip": "30301"},
    # Edge cases
    {"job": "replace hot water heater like for like", "city": "Chicago", "state": "IL", "zip": "60601"},
    {"job": "mini split installation no ductwork", "city": "Seattle", "state": "WA", "zip": "98101"},
]

def run_test(tc, index, total):
    print(f"\n{'─'*60}")
    print(f"TEST {index}/{total}: {tc['job'].upper()} — {tc['city']}, {tc['state']}")
    print(f"{'─'*60}")
    
    start = time.time()
    try:
        result = research_permit(tc["job"], tc["city"], tc["state"], tc.get("zip", ""))
        elapsed = round(time.time() - start, 1)
        
        # Validation checks
        errors = []
        warnings = []
        
        if not result.get("permits_required"):
            errors.append("Missing permits_required")
        if not result.get("applying_office"):
            warnings.append("Missing applying_office")
        if not result.get("fee_range"):
            warnings.append("Missing fee_range")
        if not result.get("inspections"):
            warnings.append("Missing inspections")
        if not result.get("approval_timeline"):
            warnings.append("Missing approval_timeline")
        if result.get("confidence") not in ["high", "medium", "low"]:
            warnings.append(f"Unexpected confidence: {result.get('confidence')}")
        
        # Print formatted output
        print(format_for_display(result))
        
        # Print validation summary
        cached = result.get("_cached") or result.get("_meta", {}).get("cached", False)
        print(f"\n✅ Completed in {elapsed}s {'(cached)' if cached else '(live)'}")
        if errors:
            print(f"❌ ERRORS: {errors}")
            return False
        if warnings:
            print(f"⚠️  Warnings: {warnings}")
        return True
        
    except Exception as e:
        elapsed = round(time.time() - start, 1)
        print(f"❌ FAILED in {elapsed}s: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("🚀 PermitPilot Research Engine — Test Suite")
    print(f"Running {len(TEST_CASES)} test cases...\n")
    
    passed = 0
    failed = 0
    
    for i, tc in enumerate(TEST_CASES, 1):
        ok = run_test(tc, i, len(TEST_CASES))
        if ok:
            passed += 1
        else:
            failed += 1
        # Small delay to avoid rate limiting
        if i < len(TEST_CASES):
            time.sleep(1)
    
    print(f"\n{'='*60}")
    print(f"RESULTS: {passed} passed, {failed} failed out of {passed+failed} tests")
    if failed == 0:
        print("🎉 ALL TESTS PASSED")
    else:
        print(f"⚠️  {failed} tests failed")
    
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
