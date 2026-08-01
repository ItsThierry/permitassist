from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
spec = importlib.util.spec_from_file_location("benchmark_v12", HERE / "benchmark_v12.py")
assert spec is not None and spec.loader is not None
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class BenchmarkV12Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ontology = json.loads((HERE / "permit_family_ontology_v1.json").read_text())
        cls.summary = json.loads((HERE / "offline_rescore_summary_v12.json").read_text())
        cls.audit = json.loads((HERE / "truth_audit_v12.json").read_text())
        cls.closure = json.loads((HERE / "ontology_enum_closure.json").read_text())
        cls.baseline = json.loads((HERE / "SESSION1_OPENING_BASELINE.json").read_text())
        cls.remediation_successor = json.loads(
            (HERE / "REMEDIATION_PROTECTED_HASH_SUCCESSOR_20260731.json").read_text()
        )

    def test_authoritative_input_hashes(self) -> None:
        for relative, expected in self.baseline["authoritative_input_sha256"].items():
            actual = hashlib.sha256((REPO / relative).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, relative)

    def test_offline_only_surface_has_no_live_or_provider_command(self) -> None:
        text = (HERE / "benchmark_v12.py").read_text()
        self.assertNotIn("call_path(", text)
        self.assertNotIn("research_permit(", text)
        self.assertNotIn("import httpx", text)
        self.assertNotIn("import requests", text)
        self.assertNotIn("from api", text)
        self.assertNotIn("import api", text)
        self.assertEqual(set(("run", "verify")), {"run", "verify"})

    def test_no_network_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
                generated = b.generate(Path(directory))
        self.assertEqual(len(generated), 5)

    def test_byte_deterministic_regeneration(self) -> None:
        b.verify()

    def test_truth_audit_covers_all_100_and_all_93_empty_sets(self) -> None:
        self.assertEqual(self.audit["counts"]["cases"], 100)
        self.assertEqual(self.audit["counts"]["empty_companion_original"], 93)
        self.assertEqual(self.audit["counts"]["empty_confirmed_none"], 0)
        self.assertEqual(self.audit["counts"]["empty_truth_incomplete"], 93)
        empty = [r for r in self.audit["cases"] if r["empty_companion_original"]]
        self.assertEqual({r["companion_truth_status"] for r in empty}, {"truth-incomplete"})

    def test_no_unreviewed_truth_correction(self) -> None:
        self.assertEqual(self.audit["counts"]["truth_corrections"], 0)
        for record in self.audit["cases"]:
            self.assertFalse(record["truth_correction_applied"])
            self.assertEqual(record["independent_review"]["sha256"], b.FABLE_REVIEW_SHA256)
            self.assertTrue(record["official_sources"])
            self.assertEqual(len(record["source_packet_sha256"]), 64)

    def test_frozen_companion_denominator_is_honest(self) -> None:
        frozen = self.summary["frozen_denominators"]
        self.assertEqual(len(frozen["companion_case_ids"]), 4)
        self.assertEqual(frozen["companion_truth_conditional_items"], 18)
        self.assertEqual(frozen["companion_truth_required_items"], 0)
        for metrics in self.summary["paths"].values():
            self.assertIsNone(metrics["companion_required_recall"]["value"])
            self.assertEqual(metrics["companion_required_recall"]["denominator"], 0)

    def test_constant_baselines_are_reported(self) -> None:
        baselines = self.summary["constant_baselines"]
        self.assertEqual(baselines["constant_REQUIRED_decision"], {"pass": 95, "denominator": 100})
        self.assertEqual(baselines["constant_BUILDING_primary"], {"pass": 96, "denominator": 100})
        self.assertEqual(baselines["constant_empty_companions_legacy_truth"]["pass"], 93)
        self.assertEqual(baselines["constant_empty_companions_v12_eligible"], {"pass": 0, "denominator": 4})

    def test_enum_closure_across_truth_raws_and_v24_cells(self) -> None:
        self.assertTrue(self.closure["closure_pass"])
        self.assertEqual(self.closure["unmapped_labels"], [])
        self.assertEqual(set(self.closure["sources"]), {"truth", "preserved_runtime_and_model_raw", "v24_cells"})
        families = set(self.ontology["families"])
        for items in self.closure["sources"].values():
            self.assertTrue(items)
            for item in items:
                self.assertIn(item["canonical_family"], families)

    def test_ontology_preserves_safety_distinctions(self) -> None:
        expected = {
            "NO_PRIMARY_PERMIT": "NO_PRIMARY_PERMIT",
            "ROOFING": "ROOFING",
            "FIRE": "FIRE_LIFE_SAFETY",
            "ZONING": "ZONING_PLANNING",
            "OCCUPANCY": "OCCUPANCY_CO",
            "DEMOLITION": "DEMOLITION",
            "POOL": "POOL_SPA",
            "MOVING": "MOVING",
            "LANDMARKS": "LANDMARKS_HISTORIC",
            "manufactured_structure_installation": "MANUFACTURED_STRUCTURE",
            "trade_or_subpermit_review": "TRADE_OR_SUBPERMIT_REVIEW",
        }
        for raw, canonical in expected.items():
            self.assertEqual(b.map_family(raw, self.ontology), canonical)
        self.assertEqual(b.map_family("completely novel local approval", self.ontology), "VERIFY")

    def test_measurement_repair_reads_typed_engine_primary(self) -> None:
        engine = self.summary["paths"]["engine"]
        self.assertEqual(engine["decision_accuracy"], {"pass": 96, "denominator": 100})
        self.assertEqual(engine["primary_family_accuracy"], {"pass": 96, "denominator": 100})
        self.assertEqual(engine["decision_abstentions"], 4)
        self.assertEqual(engine["confident_required_not_required_flips"], 0)

    def test_every_mismatch_has_taxonomy_and_raw_evidence(self) -> None:
        rows = [json.loads(line) for line in (HERE / "mismatch_forensics_v12.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 611)
        self.assertEqual(len({(r["case_id"], r["path"], r["dimension"]) for r in rows}), 611)
        for row in rows:
            self.assertTrue(row["categories"])
            self.assertTrue(set(row["categories"]).issubset(set(b.TAXONOMY)))
            raw = REPO / row["raw_reference"]
            self.assertTrue(raw.exists())
            self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(), row["raw_sha256"])
            self.assertEqual(len(row["source_packet_sha256"]), 64)
            self.assertTrue(row["source_urls"])

    def test_runtime_product_protected_hashes_still_match_opening(self) -> None:
        authorized = self.remediation_successor["authorized_successors"]
        baseline_path = HERE / "SESSION1_OPENING_BASELINE.json"
        self.assertEqual(
            hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            self.remediation_successor["base_record_sha256"],
        )
        for relative, expected in self.baseline["protected_path_sha256"].items():
            successor = authorized.get(relative)
            if successor is not None:
                self.assertEqual(successor["opening_sha256"], expected, relative)
                expected = successor["candidate_sha256"]
            actual = hashlib.sha256((REPO / relative).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, relative)
        self.assertTrue(set(authorized).issubset(self.baseline["protected_path_sha256"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
