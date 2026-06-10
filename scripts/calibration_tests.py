"""
Task 7 — Calibration set + regression assertions.

Three fixture prompts are defined here. Each runs through the full pipeline
(build_customer_permit_view_model) and asserts acceptance criteria.

Run manually:
    cd /home/boban/projects/permitassist && python3 scripts/calibration_tests.py

Run in CI:
    pytest scripts/calibration_tests.py -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

# Ensure api/ is on path
PROJECT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT / "api"))

from server import build_customer_permit_view_model
from serializer_fixes import has_fail_level_hit


class CalibrationTests(unittest.TestCase):
    maxDiff = None

    def test_prompt_a_tucson(self):
        """Prompt A — Tucson dental TI (anchor + serializer)."""
        prompt = (
            "I'm a GC converting a 3,000 sq ft former office suite into a dental office in Tucson, AZ. "
            "Scope: new rooftop HVAC unit with ductwork rework, plumbing rough-in for six operatories plus an ADA restroom, "
            "electrical service upgrade with new dedicated circuits for chairs and X-ray, non-structural demo and new interior partitions. "
            "What permits do I need to pull?"
        )
        result = build_customer_permit_view_model(
            {"job_summary": prompt},
            job_type=prompt, city="Tucson", state="AZ",
        )
        # Anchor/Kind = Building
        kind = result.get("permit_kind") or ""
        self.assertIn("Building", kind, f"Expected Building anchor, got {kind}")
        # Zero fail-level linter hits
        self.assertFalse(has_fail_level_hit(result), f"Linter fails: {result.get('_serializer_linter_hits')}")
        # No residential HVAC content
        text = json.dumps(result, default=str).lower()
        for bad in ("drain pan", "ahri"):
            self.assertNotIn(bad, text, f"Residential HVAC leak: {bad}")
        # Single Building permit entry
        permits = result.get("permits_required") or []
        building_names = [p.get("permit_type", "") for p in permits if "Building" in p.get("permit_type", "")]
        self.assertEqual(len(building_names), 1, f"Expected single Building entry, got {building_names}")

    def test_prompt_b_savannah(self):
        """Prompt B — Savannah restaurant TI (fees + gates + dedup)."""
        prompt = (
            "I'm a GC converting a 2,200 sq ft former retail space into a full-service restaurant in Savannah, GA. "
            "Scope: grease interceptor and three-compartment sink with new floor drains, restroom plumbing build-out, "
            "Type I kitchen hood with exhaust and makeup air, electrical panel upgrade with new circuits for the cooking line, "
            "non-structural demo and new kitchen/dining partition layout. What permits do I need to pull?"
        )
        result = build_customer_permit_view_model(
            {"job_summary": prompt},
            job_type=prompt, city="Savannah", state="GA",
        )
        text = json.dumps(result, default=str)
        text_lc = text.lower()
        # Anchor = Building
        kind = result.get("permit_kind") or ""
        self.assertIn("Building", kind)
        # Single Building entry
        permits = result.get("permits_required") or []
        building_names = [p.get("permit_type", "") for p in permits if "Building" in p.get("permit_type", "")]
        self.assertEqual(len(building_names), 1)
        # Fee card from formula — no $12,000 or $17,500
        fee = str(result.get("fee_range") or "")
        self.assertNotIn("$12,000", fee)
        self.assertNotIn("$17,500", fee)
        # FOG card mentions ClearForms
        self.assertIn("clearforms", text_lc)
        # Fire protection as required trade
        self.assertIn("fire protection", text_lc)
        # Phone renders 912-651-6530 with no address
        self.assertIn("912-651-6530", text)
        self.assertNotIn("5515 Abercorn", text)
        # Zero fail-level linter hits
        self.assertFalse(has_fail_level_hit(result), f"Linter fails: {result.get('_serializer_linter_hits')}")
        # No structural-RTU line (no RTU replacement here)
        self.assertNotIn("structural engineer", text_lc)

    def test_prompt_c_richmond(self):
        """Prompt C — Richmond dental TI (anchor worst-case + contact + structural)."""
        prompt = (
            "I'm a GC converting a 1,600 sq ft former retail space into a dental office in Richmond, VA. "
            "Scope: plumbing rough-in for four operatories including vacuum and compressed-air lines plus a nitrous oxide system, "
            "ADA restroom upgrade, electrical panel upgrade with dedicated circuits for chairs and X-ray, "
            "replacing the existing rooftop HVAC unit with a larger RTU and ductwork rework, non-structural demo and new partitions. "
            "Budget is around $190,000. What permits do I need to pull?"
        )
        result = build_customer_permit_view_model(
            {"job_summary": prompt},
            job_type=prompt, city="Richmond", state="VA",
        )
        text = json.dumps(result, default=str)
        text_lc = text.lower()
        # Anchor = Building
        kind = result.get("permit_kind") or ""
        self.assertIn("Building", kind)
        # Full verified contact card
        self.assertIn("804-646-4169", text)
        self.assertIn("900 E. Broad St", text)
        self.assertIn("verified", text_lc)
        # Structural roof-load line present
        self.assertIn("structural", text_lc)
        # Med-gas / nitrous content present
        self.assertIn("nitrous", text_lc)
        # Labeled national benchmark fee (no formula)
        fee = str(result.get("fee_range") or "")
        self.assertIn("benchmark", fee.lower())
        # Zero fail-level linter hits
        self.assertFalse(has_fail_level_hit(result), f"Linter fails: {result.get('_serializer_linter_hits')}")

    def _test_linter_ci_gate_injection(self):
        """Verify CI gate fails when a banned string is injected into a fixture."""
        # This is a synthetic injection test — not a real customer prompt
        from serializer_fixes import lint_output_as_dict
        bad = {"fee_range": "broken .0× multiplier"}
        hits = lint_output_as_dict(bad)
        self.assertTrue(any(h["code"] == "zero_prefix_multiplier" for h in hits))


if __name__ == "__main__":
    unittest.main(verbosity=2)
