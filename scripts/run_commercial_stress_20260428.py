#!/usr/bin/env python3
import json, sys, os, re, importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from api.research_engine import research_permit
OUT=ROOT/'eval'/'stress-test-2026-04-28-commercial'
OUT.mkdir(parents=True,exist_ok=True)
scopes={
 'restaurant':'3,200 sf commercial restaurant tenant improvement: change of occupancy B to A-2 assembly, Type I kitchen hood, grease interceptor, ADA restroom and path of travel upgrades, sprinkler modifications, fire/life-safety review, outdoor patio seating',
 'office':'4,500 sf commercial office tenant improvement on 3rd floor: new demising partitions, office buildout, ceiling and lighting changes, sprinkler head relocation, electrical/data, accessible path of travel',
 'retail':'2,000 sf commercial retail tenant improvement: sales floor buildout, storefront signage, facade improvements, accessible entrance and ADA path of travel upgrades, electrical and lighting'
}
cities=[('phoenix','Phoenix','AZ','85004'),('clark-county-nv','Las Vegas / Clark County','NV','89118'),('seattle','Seattle','WA','98101'),('los-angeles','Los Angeles','CA','90015'),('dallas','Dallas','TX','75201')]
for slug,city,state,zipc in cities:
  for sk,desc in scopes.items():
    d=desc
    if slug=='seattle' and sk=='restaurant': d += ' in a 1980s strip mall'
    if slug=='los-angeles' and sk=='restaurant': d += ' in a mixed-use building'
    print('RUN',slug,sk,flush=True)
    try:
      res=research_permit(d,city,state,zip_code=zipc,use_cache=False,job_category='commercial')
    except Exception as e:
      res={'_error':type(e).__name__+': '+str(e)}
    (OUT/f'{slug}-{sk}.json').write_text(json.dumps(res,indent=2,ensure_ascii=False))
    print('SAVED',OUT/f'{slug}-{sk}.json',flush=True)
