from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from source_roles import SourceRole, classify_source, source_label_for_role


def test_icc_blog_is_background_not_official():
    role, evidence = classify_source(
        "https://www.iccsafe.org/building-safety-journal/bsj-technical/code-requirements-for-outdoor-kitchens/",
        {"city": "Jackson", "state": "MS"},
    )
    assert role == SourceRole.PUBLISHER_CONTEXT
    assert "blog" in evidence.lower() or "publisher" in evidence.lower()
    assert "official" not in source_label_for_role(role).lower()


def test_model_code_is_code_reference_not_filing_source():
    role, _evidence = classify_source("https://codes.iccsafe.org/content/IFC2021P2", {"city": "Boulder", "state": "CO"})
    assert role == SourceRole.NATIONAL_MODEL_CODE
    assert source_label_for_role(role).lower().startswith("code reference")


def test_city_gov_apply_page_is_local_official_filing():
    role, evidence = classify_source("https://devhub.portlandoregon.gov/", {"city": "Portland", "state": "OR"})
    assert role == SourceRole.LOCAL_OFFICIAL_FILING
    assert "local" in evidence.lower() or "official" in evidence.lower()


def test_city_info_page_is_official_info_not_apply_path():
    role, _evidence = classify_source("https://www.portland.gov/ppd/get-permit/apply-permits", {"city": "Portland", "state": "OR"})
    assert role in {SourceRole.LOCAL_OFFICIAL_FILING, SourceRole.LOCAL_OFFICIAL_INFO}
    assert "official" in source_label_for_role(role).lower()
