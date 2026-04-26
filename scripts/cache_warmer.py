#!/usr/bin/env python3
"""
PermitIQ Cache Warmer
Pre-warms the lookup cache for top US cities so contractors get fast results.
Run nightly via cron.
"""
import sys, os, time, json, requests
# Add the repo root to sys.path. Works on Railway (/data/permitassist),
# Laura's Mac (/Users/lauravelez17/Code/permitassist), or any other host
# without hardcoding the path.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

TOP_CITIES = [
    # Top 50 metros + common contractor cities
    ("New York", "NY"), ("Los Angeles", "CA"), ("Chicago", "IL"), ("Houston", "TX"),
    ("Phoenix", "AZ"), ("Philadelphia", "PA"), ("San Antonio", "TX"), ("San Diego", "CA"),
    ("Dallas", "TX"), ("San Jose", "CA"), ("Austin", "TX"), ("Jacksonville", "FL"),
    ("Columbus", "OH"), ("Charlotte", "NC"), ("Indianapolis", "IN"), ("Denver", "CO"),
    ("Seattle", "WA"), ("Nashville", "TN"), ("Oklahoma City", "OK"), ("El Paso", "TX"),
    ("Boston", "MA"), ("Portland", "OR"), ("Las Vegas", "NV"), ("Memphis", "TN"),
    ("Louisville", "KY"), ("Baltimore", "MD"), ("Milwaukee", "WI"), ("Albuquerque", "NM"),
    ("Tucson", "AZ"), ("Fresno", "CA"), ("Sacramento", "CA"), ("Kansas City", "MO"),
    ("Mesa", "AZ"), ("Atlanta", "GA"), ("Omaha", "NE"), ("Colorado Springs", "CO"),
    ("Raleigh", "NC"), ("Long Beach", "CA"), ("Virginia Beach", "VA"), ("Minneapolis", "MN"),
    ("Tampa", "FL"), ("New Orleans", "LA"), ("Arlington", "TX"), ("Bakersfield", "CA"),
    ("Honolulu", "HI"), ("Anaheim", "CA"), ("Aurora", "CO"), ("Santa Ana", "CA"),
    ("Corpus Christi", "TX"), ("Riverside", "CA"),
    # Contractor hotspots
    ("Boise", "ID"), ("Salt Lake City", "UT"), ("Spokane", "WA"), ("Fargo", "ND"),
    ("Chattanooga", "TN"), ("Fort Worth", "TX"), ("Henderson", "NV"), ("Scottsdale", "AZ"),
    ("Gilbert", "AZ"), ("Chandler", "AZ"), ("Tempe", "AZ"), ("Plano", "TX"),
    ("Laredo", "TX"), ("Lubbock", "TX"), ("Garland", "TX"), ("Irving", "TX"),
    ("Reno", "NV"), ("Anchorage", "AK"), ("Providence", "RI"), ("St. Louis", "MO"),
    ("Pittsburgh", "PA"), ("Cincinnati", "OH"), ("Cleveland", "OH"), ("Detroit", "MI"),
    ("Miami", "FL"), ("Orlando", "FL"), ("Tampa", "FL"), ("Jacksonville", "FL"),
    ("Charlotte", "NC"), ("Durham", "NC"), ("Richmond", "VA"), ("Norfolk", "VA"),
]

JOB_TYPES = ["electrical permit", "HVAC permit", "plumbing permit", "roofing permit"]

BASE_URL = os.environ.get("PERMITASSIST_API_URL", "https://permitassist.io/api/permit")


def warm_city(city, state, job_type):
    try:
        r = requests.post(
            BASE_URL,
            json={"city": city, "state": state, "job_type": job_type},
            headers={"X-Cache-Warm": "1"},
            timeout=90
        )
        return r.status_code == 200
    except Exception as e:
        print(f"  FAIL {city} {state} {job_type}: {e}")
        return False


if __name__ == "__main__":
    cities = TOP_CITIES
    # Allow limiting for testing: TEST_CITIES=3 python3 cache_warmer.py
    limit = int(os.environ.get("TEST_CITIES", 0))
    if limit:
        cities = TOP_CITIES[:limit]
        print(f"[cache_warmer] TEST MODE: warming {limit} cities only")

    print(f"[cache_warmer] Starting warm for {len(cities)} cities x {len(JOB_TYPES)} job types")
    success = 0
    fail = 0
    for city, state in cities:
        for job_type in JOB_TYPES:
            ok = warm_city(city, state, job_type)
            if ok:
                success += 1
            else:
                fail += 1
            time.sleep(1)  # don't hammer the server
    print(f"[cache_warmer] Done. Success: {success}, Fail: {fail}")
