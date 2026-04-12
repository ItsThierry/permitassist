#!/usr/bin/env python3
"""
PermitAssist City Database Expander
====================================
Phase 1: Haiku bulk discovery - finds building dept URLs + basic data for all US cities 25k+ pop
Phase 2: Sonnet verification - reads actual source pages/PDFs, confirms data is accurate
Phase 3: Merge into cities_expansion.json with verified/estimated badges

Usage:
  python3 city_scraper.py --phase 1 --limit 50          # Test run, 50 cities
  python3 city_scraper.py --phase 1                      # Full Phase 1 (all cities)
  python3 city_scraper.py --phase 2 --limit 100          # Verify top 100
  python3 city_scraper.py --phase 2                      # Full verification pass
  python3 city_scraper.py --merge                        # Merge results into KB

Requires:
  OPENAI_API_KEY - set in environment
  TAVILY_API_KEY - set in environment (or pass --tavily-key)
"""

import os
import json
import time
import argparse
import requests
import re
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

# ─── Config ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
SCRIPTS_DIR = BASE_DIR / "scripts"
OUTPUT_DIR = SCRIPTS_DIR / "scraper_output"
OUTPUT_DIR.mkdir(exist_ok=True)

PHASE1_OUTPUT = OUTPUT_DIR / "phase1_discovery.json"
PHASE2_OUTPUT = OUTPUT_DIR / "phase2_verified.json"
MERGE_OUTPUT = KNOWLEDGE_DIR / "cities_expansion.json"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

client = OpenAI(api_key=OPENAI_API_KEY)

# Models
DISCOVERY_MODEL = "claude-haiku-4-5"   # Fast + cheap for bulk discovery
VERIFY_MODEL = "gpt-4o"                # Strong comprehension for verification

# Rate limiting
PHASE1_DELAY = 0.3   # seconds between Haiku calls
PHASE2_DELAY = 1.0   # seconds between Sonnet calls
MAX_WORKERS_P1 = 5   # concurrent workers for Phase 1
MAX_WORKERS_P2 = 3   # concurrent workers for Phase 2

# ─── US Cities 25k+ Population ───────────────────────────────────────────────
# Source: US Census Bureau estimates. Format: (city, state_abbr, state_full)
# This covers ~1,200 cities. We include all to maximize coverage.

US_CITIES = [
    # Alabama
    ("Birmingham", "AL", "Alabama"), ("Montgomery", "AL", "Alabama"),
    ("Huntsville", "AL", "Alabama"), ("Mobile", "AL", "Alabama"),
    ("Tuscaloosa", "AL", "Alabama"), ("Hoover", "AL", "Alabama"),
    ("Dothan", "AL", "Alabama"), ("Auburn", "AL", "Alabama"),
    ("Decatur", "AL", "Alabama"), ("Madison", "AL", "Alabama"),
    # Alaska
    ("Anchorage", "AK", "Alaska"), ("Fairbanks", "AK", "Alaska"),
    ("Juneau", "AK", "Alaska"),
    # Arizona
    ("Phoenix", "AZ", "Arizona"), ("Tucson", "AZ", "Arizona"),
    ("Mesa", "AZ", "Arizona"), ("Chandler", "AZ", "Arizona"),
    ("Scottsdale", "AZ", "Arizona"), ("Glendale", "AZ", "Arizona"),
    ("Gilbert", "AZ", "Arizona"), ("Tempe", "AZ", "Arizona"),
    ("Peoria", "AZ", "Arizona"), ("Surprise", "AZ", "Arizona"),
    ("Yuma", "AZ", "Arizona"), ("Avondale", "AZ", "Arizona"),
    ("Goodyear", "AZ", "Arizona"), ("Flagstaff", "AZ", "Arizona"),
    ("Buckeye", "AZ", "Arizona"), ("Lake Havasu City", "AZ", "Arizona"),
    ("Casa Grande", "AZ", "Arizona"), ("Sierra Vista", "AZ", "Arizona"),
    ("Maricopa", "AZ", "Arizona"), ("Oro Valley", "AZ", "Arizona"),
    ("Prescott", "AZ", "Arizona"), ("Queen Creek", "AZ", "Arizona"),
    # Arkansas
    ("Little Rock", "AR", "Arkansas"), ("Fort Smith", "AR", "Arkansas"),
    ("Fayetteville", "AR", "Arkansas"), ("Springdale", "AR", "Arkansas"),
    ("Jonesboro", "AR", "Arkansas"), ("North Little Rock", "AR", "Arkansas"),
    ("Conway", "AR", "Arkansas"), ("Rogers", "AR", "Arkansas"),
    ("Bentonville", "AR", "Arkansas"),
    # California
    ("Los Angeles", "CA", "California"), ("San Diego", "CA", "California"),
    ("San Jose", "CA", "California"), ("San Francisco", "CA", "California"),
    ("Fresno", "CA", "California"), ("Sacramento", "CA", "California"),
    ("Long Beach", "CA", "California"), ("Oakland", "CA", "California"),
    ("Bakersfield", "CA", "California"), ("Anaheim", "CA", "California"),
    ("Santa Ana", "CA", "California"), ("Riverside", "CA", "California"),
    ("Stockton", "CA", "California"), ("Chula Vista", "CA", "California"),
    ("Irvine", "CA", "California"), ("Fremont", "CA", "California"),
    ("San Bernardino", "CA", "California"), ("Modesto", "CA", "California"),
    ("Fontana", "CA", "California"), ("Moreno Valley", "CA", "California"),
    ("Glendale", "CA", "California"), ("Huntington Beach", "CA", "California"),
    ("Santa Clarita", "CA", "California"), ("Garden Grove", "CA", "California"),
    ("Oceanside", "CA", "California"), ("Rancho Cucamonga", "CA", "California"),
    ("Santa Rosa", "CA", "California"), ("Ontario", "CA", "California"),
    ("Lancaster", "CA", "California"), ("Elk Grove", "CA", "California"),
    ("Palmdale", "CA", "California"), ("Salinas", "CA", "California"),
    ("Hayward", "CA", "California"), ("Pomona", "CA", "California"),
    ("Escondido", "CA", "California"), ("Torrance", "CA", "California"),
    ("Sunnyvale", "CA", "California"), ("Pasadena", "CA", "California"),
    ("Orange", "CA", "California"), ("Fullerton", "CA", "California"),
    ("Thousand Oaks", "CA", "California"), ("Visalia", "CA", "California"),
    ("Simi Valley", "CA", "California"), ("Concord", "CA", "California"),
    ("Roseville", "CA", "California"), ("Santa Clara", "CA", "California"),
    ("Vallejo", "CA", "California"), ("Victorville", "CA", "California"),
    ("El Monte", "CA", "California"), ("Berkeley", "CA", "California"),
    ("Downey", "CA", "California"), ("Costa Mesa", "CA", "California"),
    ("Inglewood", "CA", "California"), ("Ventura", "CA", "California"),
    ("West Covina", "CA", "California"), ("Norwalk", "CA", "California"),
    ("Burbank", "CA", "California"), ("Antioch", "CA", "California"),
    ("Temecula", "CA", "California"), ("Richmond", "CA", "California"),
    ("Murrieta", "CA", "California"), ("Daly City", "CA", "California"),
    ("Peoria", "CA", "California"), ("Clovis", "CA", "California"),
    ("El Cajon", "CA", "California"), ("San Mateo", "CA", "California"),
    ("Jurupa Valley", "CA", "California"), ("Compton", "CA", "California"),
    ("Chico", "CA", "California"), ("South Bend", "CA", "California"),
    ("Lakewood", "CA", "California"), ("Broken Arrow", "CA", "California"),
    ("Santa Maria", "CA", "California"), ("Oxnard", "CA", "California"),
    ("Fairfield", "CA", "California"), ("Peoria", "CA", "California"),
    ("Hemet", "CA", "California"), ("Clearwater", "CA", "California"),
    ("West Sacramento", "CA", "California"), ("Carlsbad", "CA", "California"),
    ("Odessa", "CA", "California"), ("Pueblo", "CA", "California"),
    ("Menifee", "CA", "California"), ("High Point", "CA", "California"),
    ("Clovis", "CA", "California"), ("Tuscaloosa", "CA", "California"),
    ("Visalia", "CA", "California"), ("Round Rock", "CA", "California"),
    # Colorado
    ("Denver", "CO", "Colorado"), ("Colorado Springs", "CO", "Colorado"),
    ("Aurora", "CO", "Colorado"), ("Fort Collins", "CO", "Colorado"),
    ("Lakewood", "CO", "Colorado"), ("Thornton", "CO", "Colorado"),
    ("Arvada", "CO", "Colorado"), ("Westminster", "CO", "Colorado"),
    ("Pueblo", "CO", "Colorado"), ("Centennial", "CO", "Colorado"),
    ("Boulder", "CO", "Colorado"), ("Highlands Ranch", "CO", "Colorado"),
    ("Greeley", "CO", "Colorado"), ("Longmont", "CO", "Colorado"),
    ("Loveland", "CO", "Colorado"), ("Broomfield", "CO", "Colorado"),
    ("Castle Rock", "CO", "Colorado"), ("Parker", "CO", "Colorado"),
    # Connecticut
    ("Bridgeport", "CT", "Connecticut"), ("New Haven", "CT", "Connecticut"),
    ("Hartford", "CT", "Connecticut"), ("Stamford", "CT", "Connecticut"),
    ("Waterbury", "CT", "Connecticut"), ("Norwalk", "CT", "Connecticut"),
    ("Danbury", "CT", "Connecticut"), ("New Britain", "CT", "Connecticut"),
    # Delaware
    ("Wilmington", "DE", "Delaware"), ("Dover", "DE", "Delaware"),
    # Florida
    ("Jacksonville", "FL", "Florida"), ("Miami", "FL", "Florida"),
    ("Tampa", "FL", "Florida"), ("Orlando", "FL", "Florida"),
    ("St. Petersburg", "FL", "Florida"), ("Hialeah", "FL", "Florida"),
    ("Port St. Lucie", "FL", "Florida"), ("Cape Coral", "FL", "Florida"),
    ("Fort Lauderdale", "FL", "Florida"), ("Pembroke Pines", "FL", "Florida"),
    ("Hollywood", "FL", "Florida"), ("Miramar", "FL", "Florida"),
    ("Gainesville", "FL", "Florida"), ("Coral Springs", "FL", "Florida"),
    ("Miami Gardens", "FL", "Florida"), ("Clearwater", "FL", "Florida"),
    ("Palm Bay", "FL", "Florida"), ("West Palm Beach", "FL", "Florida"),
    ("Pompano Beach", "FL", "Florida"), ("Lakeland", "FL", "Florida"),
    ("Davie", "FL", "Florida"), ("Miami Beach", "FL", "Florida"),
    ("Sunrise", "FL", "Florida"), ("Plantation", "FL", "Florida"),
    ("Boca Raton", "FL", "Florida"), ("Deltona", "FL", "Florida"),
    ("Deerfield Beach", "FL", "Florida"), ("Palm Coast", "FL", "Florida"),
    ("Melbourne", "FL", "Florida"), ("Boynton Beach", "FL", "Florida"),
    ("Lauderhill", "FL", "Florida"), ("Wichita Falls", "FL", "Florida"),
    ("Fort Myers", "FL", "Florida"), ("Kissimmee", "FL", "Florida"),
    ("Homestead", "FL", "Florida"), ("Tallahassee", "FL", "Florida"),
    ("Pensacola", "FL", "Florida"), ("Ocala", "FL", "Florida"),
    ("Daytona Beach", "FL", "Florida"), ("St. George", "FL", "Florida"),
    ("North Port", "FL", "Florida"), ("Sarasota", "FL", "Florida"),
    ("Bradenton", "FL", "Florida"), ("Doral", "FL", "Florida"),
    ("Bonita Springs", "FL", "Florida"), ("Riverview", "FL", "Florida"),
    ("Brandon", "FL", "Florida"), ("Sanford", "FL", "Florida"),
    # Georgia
    ("Atlanta", "GA", "Georgia"), ("Augusta", "GA", "Georgia"),
    ("Columbus", "GA", "Georgia"), ("Macon", "GA", "Georgia"),
    ("Savannah", "GA", "Georgia"), ("Athens", "GA", "Georgia"),
    ("Sandy Springs", "GA", "Georgia"), ("Roswell", "GA", "Georgia"),
    ("Johns Creek", "GA", "Georgia"), ("Albany", "GA", "Georgia"),
    ("Warner Robins", "GA", "Georgia"), ("Alpharetta", "GA", "Georgia"),
    ("Marietta", "GA", "Georgia"), ("Valdosta", "GA", "Georgia"),
    ("Smyrna", "GA", "Georgia"), ("Peachtree City", "GA", "Georgia"),
    ("Brookhaven", "GA", "Georgia"), ("Gainesville", "GA", "Georgia"),
    # Hawaii
    ("Honolulu", "HI", "Hawaii"), ("Pearl City", "HI", "Hawaii"),
    ("Hilo", "HI", "Hawaii"), ("Kailua", "HI", "Hawaii"),
    # Idaho
    ("Boise", "ID", "Idaho"), ("Meridian", "ID", "Idaho"),
    ("Nampa", "ID", "Idaho"), ("Idaho Falls", "ID", "Idaho"),
    ("Pocatello", "ID", "Idaho"), ("Caldwell", "ID", "Idaho"),
    ("Coeur d'Alene", "ID", "Idaho"), ("Twin Falls", "ID", "Idaho"),
    # Illinois
    ("Chicago", "IL", "Illinois"), ("Aurora", "IL", "Illinois"),
    ("Joliet", "IL", "Illinois"), ("Naperville", "IL", "Illinois"),
    ("Rockford", "IL", "Illinois"), ("Springfield", "IL", "Illinois"),
    ("Elgin", "IL", "Illinois"), ("Peoria", "IL", "Illinois"),
    ("Champaign", "IL", "Illinois"), ("Waukegan", "IL", "Illinois"),
    ("Cicero", "IL", "Illinois"), ("Bloomington", "IL", "Illinois"),
    ("Arlington Heights", "IL", "Illinois"), ("Evanston", "IL", "Illinois"),
    ("Decatur", "IL", "Illinois"), ("Schaumburg", "IL", "Illinois"),
    ("Bolingbrook", "IL", "Illinois"), ("Palatine", "IL", "Illinois"),
    ("Skokie", "IL", "Illinois"), ("Des Plaines", "IL", "Illinois"),
    ("Orland Park", "IL", "Illinois"), ("Tinley Park", "IL", "Illinois"),
    ("Oak Lawn", "IL", "Illinois"), ("Berwyn", "IL", "Illinois"),
    ("Mount Prospect", "IL", "Illinois"), ("Normal", "IL", "Illinois"),
    ("Wheaton", "IL", "Illinois"), ("Downers Grove", "IL", "Illinois"),
    ("Alsip", "IL", "Illinois"), ("Oak Park", "IL", "Illinois"),
    # Indiana
    ("Indianapolis", "IN", "Indiana"), ("Fort Wayne", "IN", "Indiana"),
    ("Evansville", "IN", "Indiana"), ("South Bend", "IN", "Indiana"),
    ("Carmel", "IN", "Indiana"), ("Fishers", "IN", "Indiana"),
    ("Bloomington", "IN", "Indiana"), ("Hammond", "IN", "Indiana"),
    ("Gary", "IN", "Indiana"), ("Lafayette", "IN", "Indiana"),
    ("Muncie", "IN", "Indiana"), ("Terre Haute", "IN", "Indiana"),
    ("Noblesville", "IN", "Indiana"), ("Greenwood", "IN", "Indiana"),
    ("Kokomo", "IN", "Indiana"), ("Anderson", "IN", "Indiana"),
    # Iowa
    ("Des Moines", "IA", "Iowa"), ("Cedar Rapids", "IA", "Iowa"),
    ("Davenport", "IA", "Iowa"), ("Sioux City", "IA", "Iowa"),
    ("Iowa City", "IA", "Iowa"), ("Waterloo", "IA", "Iowa"),
    ("Council Bluffs", "IA", "Iowa"), ("Ames", "IA", "Iowa"),
    ("West Des Moines", "IA", "Iowa"), ("Dubuque", "IA", "Iowa"),
    # Kansas
    ("Wichita", "KS", "Kansas"), ("Overland Park", "KS", "Kansas"),
    ("Kansas City", "KS", "Kansas"), ("Olathe", "KS", "Kansas"),
    ("Topeka", "KS", "Kansas"), ("Lawrence", "KS", "Kansas"),
    ("Shawnee", "KS", "Kansas"), ("Manhattan", "KS", "Kansas"),
    ("Lenexa", "KS", "Kansas"), ("Salina", "KS", "Kansas"),
    # Kentucky
    ("Louisville", "KY", "Kentucky"), ("Lexington", "KY", "Kentucky"),
    ("Bowling Green", "KY", "Kentucky"), ("Owensboro", "KY", "Kentucky"),
    ("Covington", "KY", "Kentucky"), ("Richmond", "KY", "Kentucky"),
    ("Georgetown", "KY", "Kentucky"), ("Florence", "KY", "Kentucky"),
    # Louisiana
    ("New Orleans", "LA", "Louisiana"), ("Baton Rouge", "LA", "Louisiana"),
    ("Shreveport", "LA", "Louisiana"), ("Metairie", "LA", "Louisiana"),
    ("Lafayette", "LA", "Louisiana"), ("Lake Charles", "LA", "Louisiana"),
    ("Kenner", "LA", "Louisiana"), ("Bossier City", "LA", "Louisiana"),
    ("Monroe", "LA", "Louisiana"), ("Alexandria", "LA", "Louisiana"),
    # Maine
    ("Portland", "ME", "Maine"), ("Lewiston", "ME", "Maine"),
    ("Bangor", "ME", "Maine"), ("South Portland", "ME", "Maine"),
    # Maryland
    ("Baltimore", "MD", "Maryland"), ("Frederick", "MD", "Maryland"),
    ("Rockville", "MD", "Maryland"), ("Gaithersburg", "MD", "Maryland"),
    ("Bowie", "MD", "Maryland"), ("Hagerstown", "MD", "Maryland"),
    ("Annapolis", "MD", "Maryland"), ("College Park", "MD", "Maryland"),
    # Massachusetts
    ("Boston", "MA", "Massachusetts"), ("Worcester", "MA", "Massachusetts"),
    ("Springfield", "MA", "Massachusetts"), ("Lowell", "MA", "Massachusetts"),
    ("Cambridge", "MA", "Massachusetts"), ("New Bedford", "MA", "Massachusetts"),
    ("Brockton", "MA", "Massachusetts"), ("Quincy", "MA", "Massachusetts"),
    ("Lynn", "MA", "Massachusetts"), ("Fall River", "MA", "Massachusetts"),
    ("Newton", "MA", "Massachusetts"), ("Lawrence", "MA", "Massachusetts"),
    ("Somerville", "MA", "Massachusetts"), ("Framingham", "MA", "Massachusetts"),
    ("Haverhill", "MA", "Massachusetts"), ("Waltham", "MA", "Massachusetts"),
    ("Malden", "MA", "Massachusetts"), ("Brookline", "MA", "Massachusetts"),
    # Michigan
    ("Detroit", "MI", "Michigan"), ("Grand Rapids", "MI", "Michigan"),
    ("Warren", "MI", "Michigan"), ("Sterling Heights", "MI", "Michigan"),
    ("Ann Arbor", "MI", "Michigan"), ("Lansing", "MI", "Michigan"),
    ("Flint", "MI", "Michigan"), ("Dearborn", "MI", "Michigan"),
    ("Livonia", "MI", "Michigan"), ("Westland", "MI", "Michigan"),
    ("Troy", "MI", "Michigan"), ("Farmington Hills", "MI", "Michigan"),
    ("Kalamazoo", "MI", "Michigan"), ("Wyoming", "MI", "Michigan"),
    ("Southfield", "MI", "Michigan"), ("Rochester Hills", "MI", "Michigan"),
    ("Taylor", "MI", "Michigan"), ("Pontiac", "MI", "Michigan"),
    ("St. Clair Shores", "MI", "Michigan"), ("Royal Oak", "MI", "Michigan"),
    ("Novi", "MI", "Michigan"), ("Dearborn Heights", "MI", "Michigan"),
    # Minnesota
    ("Minneapolis", "MN", "Minnesota"), ("St. Paul", "MN", "Minnesota"),
    ("Rochester", "MN", "Minnesota"), ("Duluth", "MN", "Minnesota"),
    ("Bloomington", "MN", "Minnesota"), ("Brooklyn Park", "MN", "Minnesota"),
    ("Plymouth", "MN", "Minnesota"), ("St. Cloud", "MN", "Minnesota"),
    ("Eagan", "MN", "Minnesota"), ("Woodbury", "MN", "Minnesota"),
    ("Maple Grove", "MN", "Minnesota"), ("Eden Prairie", "MN", "Minnesota"),
    ("Coon Rapids", "MN", "Minnesota"), ("Burnsville", "MN", "Minnesota"),
    ("Apple Valley", "MN", "Minnesota"), ("Edina", "MN", "Minnesota"),
    # Mississippi
    ("Jackson", "MS", "Mississippi"), ("Gulfport", "MS", "Mississippi"),
    ("Southaven", "MS", "Mississippi"), ("Hattiesburg", "MS", "Mississippi"),
    ("Biloxi", "MS", "Mississippi"), ("Meridian", "MS", "Mississippi"),
    # Missouri
    ("Kansas City", "MO", "Missouri"), ("St. Louis", "MO", "Missouri"),
    ("Springfield", "MO", "Missouri"), ("Columbia", "MO", "Missouri"),
    ("Independence", "MO", "Missouri"), ("Lee's Summit", "MO", "Missouri"),
    ("O'Fallon", "MO", "Missouri"), ("St. Joseph", "MO", "Missouri"),
    ("St. Charles", "MO", "Missouri"), ("Blue Springs", "MO", "Missouri"),
    ("Joplin", "MO", "Missouri"), ("Chesterfield", "MO", "Missouri"),
    # Montana
    ("Billings", "MT", "Montana"), ("Missoula", "MT", "Montana"),
    ("Great Falls", "MT", "Montana"), ("Bozeman", "MT", "Montana"),
    ("Butte", "MT", "Montana"),
    # Nebraska
    ("Omaha", "NE", "Nebraska"), ("Lincoln", "NE", "Nebraska"),
    ("Bellevue", "NE", "Nebraska"), ("Grand Island", "NE", "Nebraska"),
    ("Kearney", "NE", "Nebraska"), ("Fremont", "NE", "Nebraska"),
    # Nevada
    ("Las Vegas", "NV", "Nevada"), ("Henderson", "NV", "Nevada"),
    ("Reno", "NV", "Nevada"), ("North Las Vegas", "NV", "Nevada"),
    ("Sparks", "NV", "Nevada"), ("Carson City", "NV", "Nevada"),
    # New Hampshire
    ("Manchester", "NH", "New Hampshire"), ("Nashua", "NH", "New Hampshire"),
    ("Concord", "NH", "New Hampshire"), ("Dover", "NH", "New Hampshire"),
    # New Jersey
    ("Newark", "NJ", "New Jersey"), ("Jersey City", "NJ", "New Jersey"),
    ("Paterson", "NJ", "New Jersey"), ("Elizabeth", "NJ", "New Jersey"),
    ("Edison", "NJ", "New Jersey"), ("Woodbridge", "NJ", "New Jersey"),
    ("Lakewood", "NJ", "New Jersey"), ("Toms River", "NJ", "New Jersey"),
    ("Hamilton", "NJ", "New Jersey"), ("Trenton", "NJ", "New Jersey"),
    ("Clifton", "NJ", "New Jersey"), ("Camden", "NJ", "New Jersey"),
    ("Brick", "NJ", "New Jersey"), ("Cherry Hill", "NJ", "New Jersey"),
    ("Passaic", "NJ", "New Jersey"), ("Middletown", "NJ", "New Jersey"),
    ("Union City", "NJ", "New Jersey"), ("Franklin", "NJ", "New Jersey"),
    ("Old Bridge", "NJ", "New Jersey"), ("Gloucester", "NJ", "New Jersey"),
    # New Mexico
    ("Albuquerque", "NM", "New Mexico"), ("Las Cruces", "NM", "New Mexico"),
    ("Rio Rancho", "NM", "New Mexico"), ("Santa Fe", "NM", "New Mexico"),
    ("Roswell", "NM", "New Mexico"), ("Farmington", "NM", "New Mexico"),
    # New York
    ("New York City", "NY", "New York"), ("Buffalo", "NY", "New York"),
    ("Rochester", "NY", "New York"), ("Yonkers", "NY", "New York"),
    ("Syracuse", "NY", "New York"), ("Albany", "NY", "New York"),
    ("New Rochelle", "NY", "New York"), ("Mount Vernon", "NY", "New York"),
    ("Schenectady", "NY", "New York"), ("Utica", "NY", "New York"),
    ("Troy", "NY", "New York"), ("Cheektowaga", "NY", "New York"),
    ("White Plains", "NY", "New York"), ("Hempstead", "NY", "New York"),
    # North Carolina
    ("Charlotte", "NC", "North Carolina"), ("Raleigh", "NC", "North Carolina"),
    ("Greensboro", "NC", "North Carolina"), ("Durham", "NC", "North Carolina"),
    ("Winston-Salem", "NC", "North Carolina"), ("Fayetteville", "NC", "North Carolina"),
    ("Cary", "NC", "North Carolina"), ("Wilmington", "NC", "North Carolina"),
    ("High Point", "NC", "North Carolina"), ("Concord", "NC", "North Carolina"),
    ("Asheville", "NC", "North Carolina"), ("Gastonia", "NC", "North Carolina"),
    ("Jacksonville", "NC", "North Carolina"), ("Chapel Hill", "NC", "North Carolina"),
    ("Rocky Mount", "NC", "North Carolina"), ("Huntersville", "NC", "North Carolina"),
    ("Burlington", "NC", "North Carolina"), ("Kannapolis", "NC", "North Carolina"),
    # North Dakota
    ("Fargo", "ND", "North Dakota"), ("Bismarck", "ND", "North Dakota"),
    ("Grand Forks", "ND", "North Dakota"), ("Minot", "ND", "North Dakota"),
    # Ohio
    ("Columbus", "OH", "Ohio"), ("Cleveland", "OH", "Ohio"),
    ("Cincinnati", "OH", "Ohio"), ("Toledo", "OH", "Ohio"),
    ("Akron", "OH", "Ohio"), ("Dayton", "OH", "Ohio"),
    ("Parma", "OH", "Ohio"), ("Canton", "OH", "Ohio"),
    ("Youngstown", "OH", "Ohio"), ("Lorain", "OH", "Ohio"),
    ("Hamilton", "OH", "Ohio"), ("Springfield", "OH", "Ohio"),
    ("Kettering", "OH", "Ohio"), ("Elyria", "OH", "Ohio"),
    ("Middletown", "OH", "Ohio"), ("Lakewood", "OH", "Ohio"),
    ("Cuyahoga Falls", "OH", "Ohio"), ("Euclid", "OH", "Ohio"),
    ("Newark", "OH", "Ohio"), ("Mansfield", "OH", "Ohio"),
    ("Mentor", "OH", "Ohio"), ("Beavercreek", "OH", "Ohio"),
    ("Cleveland Heights", "OH", "Ohio"), ("Strongsville", "OH", "Ohio"),
    ("Fairfield", "OH", "Ohio"), ("Dublin", "OH", "Ohio"),
    ("Grove City", "OH", "Ohio"), ("Warren", "OH", "Ohio"),
    # Oklahoma
    ("Oklahoma City", "OK", "Oklahoma"), ("Tulsa", "OK", "Oklahoma"),
    ("Norman", "OK", "Oklahoma"), ("Broken Arrow", "OK", "Oklahoma"),
    ("Lawton", "OK", "Oklahoma"), ("Edmond", "OK", "Oklahoma"),
    ("Moore", "OK", "Oklahoma"), ("Midwest City", "OK", "Oklahoma"),
    ("Enid", "OK", "Oklahoma"), ("Stillwater", "OK", "Oklahoma"),
    # Oregon
    ("Portland", "OR", "Oregon"), ("Salem", "OR", "Oregon"),
    ("Eugene", "OR", "Oregon"), ("Gresham", "OR", "Oregon"),
    ("Hillsboro", "OR", "Oregon"), ("Beaverton", "OR", "Oregon"),
    ("Bend", "OR", "Oregon"), ("Medford", "OR", "Oregon"),
    ("Springfield", "OR", "Oregon"), ("Corvallis", "OR", "Oregon"),
    ("Albany", "OR", "Oregon"), ("Tigard", "OR", "Oregon"),
    # Pennsylvania
    ("Philadelphia", "PA", "Pennsylvania"), ("Pittsburgh", "PA", "Pennsylvania"),
    ("Allentown", "PA", "Pennsylvania"), ("Erie", "PA", "Pennsylvania"),
    ("Reading", "PA", "Pennsylvania"), ("Scranton", "PA", "Pennsylvania"),
    ("Bethlehem", "PA", "Pennsylvania"), ("Lancaster", "PA", "Pennsylvania"),
    ("Harrisburg", "PA", "Pennsylvania"), ("Altoona", "PA", "Pennsylvania"),
    ("York", "PA", "Pennsylvania"), ("Wilkes-Barre", "PA", "Pennsylvania"),
    # Rhode Island
    ("Providence", "RI", "Rhode Island"), ("Warwick", "RI", "Rhode Island"),
    ("Cranston", "RI", "Rhode Island"), ("Pawtucket", "RI", "Rhode Island"),
    # South Carolina
    ("Columbia", "SC", "South Carolina"), ("Charleston", "SC", "South Carolina"),
    ("North Charleston", "SC", "South Carolina"), ("Mount Pleasant", "SC", "South Carolina"),
    ("Rock Hill", "SC", "South Carolina"), ("Greenville", "SC", "South Carolina"),
    ("Summerville", "SC", "South Carolina"), ("Sumter", "SC", "South Carolina"),
    ("Hilton Head Island", "SC", "South Carolina"), ("Florence", "SC", "South Carolina"),
    # South Dakota
    ("Sioux Falls", "SD", "South Dakota"), ("Rapid City", "SD", "South Dakota"),
    ("Aberdeen", "SD", "South Dakota"),
    # Tennessee
    ("Memphis", "TN", "Tennessee"), ("Nashville", "TN", "Tennessee"),
    ("Knoxville", "TN", "Tennessee"), ("Chattanooga", "TN", "Tennessee"),
    ("Clarksville", "TN", "Tennessee"), ("Murfreesboro", "TN", "Tennessee"),
    ("Franklin", "TN", "Tennessee"), ("Jackson", "TN", "Tennessee"),
    ("Johnson City", "TN", "Tennessee"), ("Bartlett", "TN", "Tennessee"),
    ("Hendersonville", "TN", "Tennessee"), ("Kingsport", "TN", "Tennessee"),
    ("Smyrna", "TN", "Tennessee"), ("Germantown", "TN", "Tennessee"),
    # Texas
    ("Houston", "TX", "Texas"), ("San Antonio", "TX", "Texas"),
    ("Dallas", "TX", "Texas"), ("Austin", "TX", "Texas"),
    ("Fort Worth", "TX", "Texas"), ("El Paso", "TX", "Texas"),
    ("Arlington", "TX", "Texas"), ("Corpus Christi", "TX", "Texas"),
    ("Plano", "TX", "Texas"), ("Laredo", "TX", "Texas"),
    ("Lubbock", "TX", "Texas"), ("Garland", "TX", "Texas"),
    ("Irving", "TX", "Texas"), ("Amarillo", "TX", "Texas"),
    ("Grand Prairie", "TX", "Texas"), ("Brownsville", "TX", "Texas"),
    ("McKinney", "TX", "Texas"), ("Frisco", "TX", "Texas"),
    ("Pasadena", "TX", "Texas"), ("Mesquite", "TX", "Texas"),
    ("Killeen", "TX", "Texas"), ("McAllen", "TX", "Texas"),
    ("Denton", "TX", "Texas"), ("Midland", "TX", "Texas"),
    ("Waco", "TX", "Texas"), ("Carrollton", "TX", "Texas"),
    ("Round Rock", "TX", "Texas"), ("Abilene", "TX", "Texas"),
    ("Beaumont", "TX", "Texas"), ("Odessa", "TX", "Texas"),
    ("Pearland", "TX", "Texas"), ("Richardson", "TX", "Texas"),
    ("Sugar Land", "TX", "Texas"), ("League City", "TX", "Texas"),
    ("Allen", "TX", "Texas"), ("Wichita Falls", "TX", "Texas"),
    ("Tyler", "TX", "Texas"), ("Edinburg", "TX", "Texas"),
    ("Lewisville", "TX", "Texas"), ("San Angelo", "TX", "Texas"),
    ("Cary", "TX", "Texas"), ("Longview", "TX", "Texas"),
    ("College Station", "TX", "Texas"), ("Flower Mound", "TX", "Texas"),
    ("Cedar Park", "TX", "Texas"), ("Conroe", "TX", "Texas"),
    ("New Braunfels", "TX", "Texas"), ("Visalia", "TX", "Texas"),
    ("Gainesville", "TX", "Texas"), ("Baytown", "TX", "Texas"),
    ("Harlingen", "TX", "Texas"), ("Pharr", "TX", "Texas"),
    ("Mansfield", "TX", "Texas"), ("Leander", "TX", "Texas"),
    ("Mission", "TX", "Texas"), ("Georgetown", "TX", "Texas"),
    ("Rowlett", "TX", "Texas"), ("Edinburg", "TX", "Texas"),
    ("Rosenberg", "TX", "Texas"), ("Kyle", "TX", "Texas"),
    ("Pflugerville", "TX", "Texas"), ("Euless", "TX", "Texas"),
    ("Grapevine", "TX", "Texas"), ("North Richland Hills", "TX", "Texas"),
    ("Burleson", "TX", "Texas"), ("Bedford", "TX", "Texas"),
    ("Haltom City", "TX", "Texas"),
    # Utah
    ("Salt Lake City", "UT", "Utah"), ("West Valley City", "UT", "Utah"),
    ("Provo", "UT", "Utah"), ("West Jordan", "UT", "Utah"),
    ("Orem", "UT", "Utah"), ("Sandy", "UT", "Utah"),
    ("Ogden", "UT", "Utah"), ("St. George", "UT", "Utah"),
    ("Layton", "UT", "Utah"), ("Millcreek", "UT", "Utah"),
    ("Taylorsville", "UT", "Utah"), ("Logan", "UT", "Utah"),
    ("South Jordan", "UT", "Utah"), ("Lehi", "UT", "Utah"),
    ("Draper", "UT", "Utah"), ("Herriman", "UT", "Utah"),
    # Vermont
    ("Burlington", "VT", "Vermont"), ("South Burlington", "VT", "Vermont"),
    # Virginia
    ("Virginia Beach", "VA", "Virginia"), ("Norfolk", "VA", "Virginia"),
    ("Chesapeake", "VA", "Virginia"), ("Richmond", "VA", "Virginia"),
    ("Newport News", "VA", "Virginia"), ("Alexandria", "VA", "Virginia"),
    ("Hampton", "VA", "Virginia"), ("Roanoke", "VA", "Virginia"),
    ("Portsmouth", "VA", "Virginia"), ("Suffolk", "VA", "Virginia"),
    ("Lynchburg", "VA", "Virginia"), ("Harrisonburg", "VA", "Virginia"),
    ("Charlottesville", "VA", "Virginia"), ("Blacksburg", "VA", "Virginia"),
    # Washington
    ("Seattle", "WA", "Washington"), ("Spokane", "WA", "Washington"),
    ("Tacoma", "WA", "Washington"), ("Vancouver", "WA", "Washington"),
    ("Bellevue", "WA", "Washington"), ("Kent", "WA", "Washington"),
    ("Everett", "WA", "Washington"), ("Renton", "WA", "Washington"),
    ("Spokane Valley", "WA", "Washington"), ("Federal Way", "WA", "Washington"),
    ("Kirkland", "WA", "Washington"), ("Bellingham", "WA", "Washington"),
    ("Kennewick", "WA", "Washington"), ("Yakima", "WA", "Washington"),
    ("Auburn", "WA", "Washington"), ("Redmond", "WA", "Washington"),
    ("Marysville", "WA", "Washington"), ("Shoreline", "WA", "Washington"),
    ("Richland", "WA", "Washington"), ("Lakewood", "WA", "Washington"),
    ("Pasco", "WA", "Washington"), ("Sammamish", "WA", "Washington"),
    ("Burien", "WA", "Washington"), ("South Hill", "WA", "Washington"),
    # West Virginia
    ("Charleston", "WV", "West Virginia"), ("Huntington", "WV", "West Virginia"),
    ("Parkersburg", "WV", "West Virginia"), ("Morgantown", "WV", "West Virginia"),
    # Wisconsin
    ("Milwaukee", "WI", "Wisconsin"), ("Madison", "WI", "Wisconsin"),
    ("Green Bay", "WI", "Wisconsin"), ("Kenosha", "WI", "Wisconsin"),
    ("Racine", "WI", "Wisconsin"), ("Appleton", "WI", "Wisconsin"),
    ("Waukesha", "WI", "Wisconsin"), ("Oshkosh", "WI", "Wisconsin"),
    ("Eau Claire", "WI", "Wisconsin"), ("Janesville", "WI", "Wisconsin"),
    ("West Allis", "WI", "Wisconsin"), ("La Crosse", "WI", "Wisconsin"),
    ("Sheboygan", "WI", "Wisconsin"), ("Wauwatosa", "WI", "Wisconsin"),
    ("Fond du Lac", "WI", "Wisconsin"), ("Wausau", "WI", "Wisconsin"),
    # Wyoming
    ("Cheyenne", "WY", "Wyoming"), ("Casper", "WY", "Wyoming"),
    ("Laramie", "WY", "Wyoming"),
]

# ─── Tavily Search ─────────────────────────────────────────────────────────────

def tavily_search(query: str, max_results: int = 3) -> list[dict]:
    """Search via Tavily API."""
    if not TAVILY_API_KEY:
        return []
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": True,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("results", [])
    except Exception as e:
        print(f"  [tavily] Error: {e}")
    return []


# ─── Phase 1: Haiku Discovery ─────────────────────────────────────────────────

DISCOVERY_PROMPT = """You are helping build a permit lookup database for contractors.
Given a city and state, extract building permit information from the search results provided.

Return a JSON object with these exact fields (use null if not found):
{
  "building_dept_url": "URL of the building/permits department",
  "phone": "Building department phone number",
  "apply_url": "Direct URL to apply for permits online (NOT a PDF)",
  "apply_pdf": "URL if application is PDF only",
  "hvac_permit_required": true/false/null,
  "hvac_fee_min": number or null,
  "hvac_fee_max": number or null,
  "electrical_permit_required": true/false/null,
  "plumbing_permit_required": true/false/null,
  "confidence": "high" | "medium" | "low",
  "notes": "any important notes about this city's permit process"
}

Rules:
- confidence=high: found official city website with fees
- confidence=medium: found official website but no fees
- confidence=low: couldn't find clear info
- Never invent data. Use null if unsure.
- phone must be format: (XXX) XXX-XXXX
- apply_url must NOT be a PDF
"""

def discover_city(city: str, state: str, state_full: str) -> dict:
    """Phase 1: Use Haiku to discover building dept info for a city."""
    query = f"{city} {state} building department permit application online phone number"
    results = tavily_search(query, max_results=4)
    
    search_text = ""
    for r in results:
        search_text += f"\nURL: {r.get('url', '')}\nContent: {r.get('content', '')[:500]}\n"
    
    if not search_text:
        search_text = "No search results found."
    
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",  # Use 4o-mini as Haiku equivalent
            messages=[
                {"role": "system", "content": DISCOVERY_PROMPT},
                {"role": "user", "content": f"City: {city}, {state} ({state_full})\n\nSearch Results:\n{search_text}\n\nExtract building permit information as JSON."}
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=500,
        )
        data = json.loads(resp.choices[0].message.content)
        data["city"] = city
        data["state"] = state
        data["state_full"] = state_full
        data["phase1_timestamp"] = datetime.now().isoformat()
        return data
    except Exception as e:
        print(f"  [discover] Error for {city}, {state}: {e}")
        return {
            "city": city, "state": state, "state_full": state_full,
            "confidence": "low", "error": str(e),
            "phase1_timestamp": datetime.now().isoformat()
        }


# ─── Phase 2: Sonnet Verification ─────────────────────────────────────────────

VERIFY_PROMPT = """You are verifying permit data for a contractor database. 
You will be given a city's building department URL and existing extracted data.
Use the search results to VERIFY or CORRECT the data.

Return a JSON object with these fields:
{
  "building_dept_url": "verified URL",
  "phone": "verified phone number",
  "apply_url": "verified online application URL (NOT a PDF)",
  "apply_pdf": "PDF application URL if paper-only",
  "hvac_permit_required": true/false,
  "hvac_fee_typical": "e.g. '$150-250' or 'Varies by valuation'",
  "hvac_fee_min": number or null,
  "hvac_fee_max": number or null,
  "electrical_permit_required": true/false,
  "plumbing_permit_required": true/false,
  "portal_selection_hvac": "exact dropdown text for HVAC permits",
  "verified": true/false,
  "verification_source": "URL of the page that confirmed this data",
  "notes": "important notes"
}

Rules:
- Only mark verified=true if you found data from an official .gov or official city website
- Never guess fees — use null if not clearly stated
- portal_selection_hvac should be the EXACT text as it appears in the permit portal dropdown
"""

def verify_city(city_data: dict) -> dict:
    """Phase 2: Use GPT-4o to verify and deepen the data."""
    city = city_data.get("city", "")
    state = city_data.get("state", "")
    
    # Search for more specific data
    query1 = f"{city} {state} building permit fee schedule HVAC 2024 2025"
    query2 = f"site:{city_data.get('building_dept_url', '')} permit fees" if city_data.get("building_dept_url") else f"{city} {state} online permit application portal"
    
    results1 = tavily_search(query1, max_results=3)
    results2 = tavily_search(query2, max_results=2)
    
    search_text = f"Existing data:\n{json.dumps(city_data, indent=2)}\n\nNew search results:\n"
    for r in results1 + results2:
        search_text += f"\nURL: {r.get('url', '')}\nContent: {r.get('content', '')[:800]}\n"
    
    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": VERIFY_PROMPT},
                {"role": "user", "content": f"Verify permit data for {city}, {state}:\n\n{search_text}"}
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=700,
        )
        data = json.loads(resp.choices[0].message.content)
        data["city"] = city
        data["state"] = state
        data["state_full"] = city_data.get("state_full", "")
        data["phase2_timestamp"] = datetime.now().isoformat()
        return data
    except Exception as e:
        print(f"  [verify] Error for {city}, {state}: {e}")
        city_data["verification_error"] = str(e)
        return city_data


# ─── Merge into KB ────────────────────────────────────────────────────────────

def merge_to_kb(verified_data: list[dict]):
    """Merge verified city data into cities_expansion.json format."""
    
    # Load existing expansion data
    existing = {}
    if MERGE_OUTPUT.exists():
        with open(MERGE_OUTPUT) as f:
            existing = json.load(f)
    
    added = 0
    updated = 0
    
    for city_data in verified_data:
        city = city_data.get("city", "")
        state = city_data.get("state", "")
        
        if not city or not state:
            continue
        
        # Skip low-confidence cities with no useful data
        if city_data.get("confidence") == "low" and not city_data.get("building_dept_url"):
            continue
        
        key = f"{city.lower().replace(' ', '_')}_{state.lower()}"
        
        entry = {
            "city": city,
            "state": state,
            "state_full": city_data.get("state_full", ""),
            "building_dept_url": city_data.get("building_dept_url"),
            "phone": city_data.get("phone"),
            "apply_url": city_data.get("apply_url"),
            "apply_pdf": city_data.get("apply_pdf"),
            "verified": city_data.get("verified", False),
            "verification_source": city_data.get("verification_source"),
            "hvac": {
                "permit_required": city_data.get("hvac_permit_required", True),
                "fee_min": city_data.get("hvac_fee_min"),
                "fee_max": city_data.get("hvac_fee_max"),
                "fee_typical": city_data.get("hvac_fee_typical"),
                "portal_selection": city_data.get("portal_selection_hvac"),
            },
            "electrical": {
                "permit_required": city_data.get("electrical_permit_required", True),
            },
            "plumbing": {
                "permit_required": city_data.get("plumbing_permit_required", True),
            },
            "notes": city_data.get("notes"),
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
        }
        
        if key in existing:
            updated += 1
        else:
            added += 1
        
        existing[key] = entry
    
    # Save merged output
    with open(MERGE_OUTPUT, "w") as f:
        json.dump(existing, f, indent=2)
    
    print(f"\n✅ Merge complete: {added} added, {updated} updated")
    print(f"   Total cities in KB: {len(existing)}")
    return existing


# ─── CLI ──────────────────────────────────────────────────────────────────────

def run_phase1(limit: int = None, resume: bool = True):
    """Run Phase 1: Haiku bulk discovery."""
    print(f"\n🔍 Phase 1: Bulk Discovery")
    print(f"   Model: gpt-4o-mini (fast + cheap)")
    print(f"   Cities: {len(US_CITIES) if not limit else limit}")
    
    if not TAVILY_API_KEY:
        print("❌ TAVILY_API_KEY not set. Cannot run Phase 1.")
        print("   Set it with: export TAVILY_API_KEY=your-key-here")
        return []
    
    # Load existing results for resume
    existing = {}
    if resume and PHASE1_OUTPUT.exists():
        with open(PHASE1_OUTPUT) as f:
            existing = {f"{d['city']}_{d['state']}": d for d in json.load(f)}
        print(f"   Resuming: {len(existing)} cities already done")
    
    cities = US_CITIES[:limit] if limit else US_CITIES
    results = list(existing.values())
    todo = [(c, s, sf) for c, s, sf in cities if f"{c}_{s}" not in existing]
    
    print(f"   To process: {len(todo)} cities")
    print(f"   Estimated cost: ~${len(todo) * 0.003:.2f}")
    print(f"   Estimated time: ~{len(todo) * 2 / 60:.0f} minutes\n")
    
    for i, (city, state, state_full) in enumerate(todo):
        print(f"  [{i+1}/{len(todo)}] {city}, {state}...", end=" ", flush=True)
        data = discover_city(city, state, state_full)
        confidence = data.get("confidence", "low")
        print(f"[{confidence}]")
        results.append(data)
        
        # Save progress every 10 cities
        if (i + 1) % 10 == 0:
            with open(PHASE1_OUTPUT, "w") as f:
                json.dump(results, f, indent=2)
            print(f"  💾 Saved progress ({len(results)} cities)")
        
        time.sleep(PHASE1_DELAY)
    
    # Final save
    with open(PHASE1_OUTPUT, "w") as f:
        json.dump(results, f, indent=2)
    
    high = sum(1 for r in results if r.get("confidence") == "high")
    medium = sum(1 for r in results if r.get("confidence") == "medium")
    low = sum(1 for r in results if r.get("confidence") == "low")
    
    print(f"\n✅ Phase 1 complete!")
    print(f"   High confidence: {high}")
    print(f"   Medium confidence: {medium}")
    print(f"   Low confidence: {low}")
    print(f"   Results saved to: {PHASE1_OUTPUT}")
    
    return results


def run_phase2(limit: int = None, resume: bool = True):
    """Run Phase 2: GPT-4o verification."""
    print(f"\n✅ Phase 2: Sonnet Verification")
    print(f"   Model: gpt-4o (deep comprehension)")
    
    if not PHASE1_OUTPUT.exists():
        print("❌ Phase 1 output not found. Run Phase 1 first.")
        return []
    
    if not TAVILY_API_KEY:
        print("❌ TAVILY_API_KEY not set.")
        return []
    
    with open(PHASE1_OUTPUT) as f:
        phase1_data = json.load(f)
    
    # Prioritize: high confidence first, then medium
    phase1_data.sort(key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x.get("confidence", "low"), 2))
    
    if limit:
        phase1_data = phase1_data[:limit]
    
    # Load existing verified results for resume
    existing = {}
    if resume and PHASE2_OUTPUT.exists():
        with open(PHASE2_OUTPUT) as f:
            existing = {f"{d['city']}_{d['state']}": d for d in json.load(f)}
        print(f"   Resuming: {len(existing)} cities already verified")
    
    todo = [d for d in phase1_data if f"{d['city']}_{d['state']}" not in existing]
    results = list(existing.values())
    
    print(f"   To verify: {len(todo)} cities")
    print(f"   Estimated cost: ~${len(todo) * 0.02:.2f}")
    print(f"   Estimated time: ~{len(todo) * 5 / 60:.0f} minutes\n")
    
    for i, city_data in enumerate(todo):
        city = city_data.get("city", "")
        state = city_data.get("state", "")
        print(f"  [{i+1}/{len(todo)}] Verifying {city}, {state}...", end=" ", flush=True)
        
        verified = verify_city(city_data)
        verified_flag = verified.get("verified", False)
        print(f"[{'✓ verified' if verified_flag else 'estimated'}]")
        results.append(verified)
        
        # Save every 5 cities
        if (i + 1) % 5 == 0:
            with open(PHASE2_OUTPUT, "w") as f:
                json.dump(results, f, indent=2)
            print(f"  💾 Saved progress ({len(results)} verified)")
        
        time.sleep(PHASE2_DELAY)
    
    # Final save
    with open(PHASE2_OUTPUT, "w") as f:
        json.dump(results, f, indent=2)
    
    verified_count = sum(1 for r in results if r.get("verified", False))
    print(f"\n✅ Phase 2 complete!")
    print(f"   Verified: {verified_count}/{len(results)}")
    print(f"   Results saved to: {PHASE2_OUTPUT}")
    
    return results


def do_merge():
    """Merge Phase 2 output into the KB."""
    print("\n🔀 Merging into cities_expansion.json...")
    
    source = PHASE2_OUTPUT if PHASE2_OUTPUT.exists() else PHASE1_OUTPUT
    if not source.exists():
        print("❌ No phase output found. Run Phase 1 or 2 first.")
        return
    
    print(f"   Source: {source.name}")
    with open(source) as f:
        data = json.load(f)
    
    merge_to_kb(data)


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PermitAssist City Database Expander")
    parser.add_argument("--phase", type=int, choices=[1, 2], help="Run phase 1 (discovery) or phase 2 (verification)")
    parser.add_argument("--merge", action="store_true", help="Merge results into KB")
    parser.add_argument("--limit", type=int, help="Limit number of cities (for testing)")
    parser.add_argument("--no-resume", action="store_true", help="Start fresh, don't resume")
    parser.add_argument("--tavily-key", type=str, help="Tavily API key (or set TAVILY_API_KEY env var)")
    args = parser.parse_args()
    
    if args.tavily_key:
        TAVILY_API_KEY = args.tavily_key
        os.environ["TAVILY_API_KEY"] = args.tavily_key
    
    if not OPENAI_API_KEY:
        print("❌ OPENAI_API_KEY not set.")
        exit(1)
    
    if args.phase == 1:
        run_phase1(limit=args.limit, resume=not args.no_resume)
    elif args.phase == 2:
        run_phase2(limit=args.limit, resume=not args.no_resume)
    elif args.merge:
        do_merge()
    else:
        print(__doc__)
        parser.print_help()
