"""Temporary manifests independently exercise source hashes and table accounting."""
import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("verify_expanded", REPO / "scripts/verify-expanded.py")
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class ExpandedCoverage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        for folder in ("provenance/expanded", "data/registers", "tools", "docs", "esos"):
            (self.repo / folder).mkdir(parents=True)
        shutil.copy(REPO / "tools/manual_tables.py", self.repo / "tools/manual_tables.py")
        self.docs = self.repo / "docs"
        self.source = self.docs / "bank.md"
        self.source.write_text("| Register | Offset |\n| --- | --- |\n| CONTROL | 0 |\n\n"
                               "| Bit | Name |\n| --- | --- |\n| 0 | enable |\n")
        (self.repo / "esos/bank.h").write_text("#define BASE 0x1000\n")
        self.manifest = {
            "schema": 1, "chip": "k1", "registers": ["expanded_k1_test"],
            "sources": [{"path": "bank.md", "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest()},
                        {"path": "bank.h", "kind": "esos", "sha256": hashlib.sha256((self.repo / "esos/bank.h").read_bytes()).hexdigest()}],
            "instances": [{"name": "RCPU_K1_TEST", "block": "k1_test::Bank", "address": 4096,
                           "view": "rcpu", "source": {"path": "bank.h", "line": 1}}],
            "aliases": {}, "write_mask_registers": [], "corrections": [],
            "tables": [{"source": "bank.md", "line": line, "status": "generated",
                        "registers": ["k1_test::Bank.control"]} for line in (1, 5)]}
        self.ir: dict[str, Any] = {"block/k1_test::Bank": {"items": [{"name": "control", "byte_offset": 0, "bit_size": 8}]}}

    def check(self, online=True):
        (self.repo / "provenance/expanded/test.json").write_text(json.dumps(self.manifest))
        (self.repo / "data/registers/expanded_k1_test.yaml").write_text(json.dumps(self.ir))
        roots = {"docs": self.docs, "esos": self.repo / "esos"} if online else {}
        return VERIFY.verify(self.repo, roots)

    def test_hashes_sources_and_all_original_tables(self):
        totals, hashes, statuses = self.check()
        self.assertEqual((totals["tables"], totals["blocks"], totals["registers"], totals["instances"], hashes), (2, 1, 1, 1, 2))
        self.assertEqual(statuses, {"generated": 2})
        self.assertEqual(self.check(False)[1], 0)

    def test_address_only_document_source_needs_no_fabricated_table(self):
        source = self.docs / "bases.md"
        source.write_text("Documented base address: 0x1000.\n")
        self.manifest["sources"].append({"path": "bases.md", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
        self.manifest["instances"][0]["source"] = {"path": "bases.md", "line": 1}
        self.assertEqual(self.check()[1], 3)

    def test_esos_supplement_acceptance_does_not_hide_missing_docs_table(self):
        record = {"source": "bank.h", "line": 1, "status": "generated",
                  "registers": ["k1_test::Bank.control"], "note": "C macro supplies base and register facts."}
        self.manifest["tables"].append(record)
        self.assertEqual(self.check()[0]["tables"], 3)
        self.manifest["tables"].pop(1)
        with self.assertRaisesRegex(ValueError, "table coverage mismatch"):
            self.check()

    def test_esos_supplement_requires_declared_and_existing_source(self):
        self.manifest["tables"].append({"source": "unknown.h", "line": 1, "status": "generated",
                                       "registers": ["k1_test::Bank.control"], "note": "C supplement."})
        with self.assertRaisesRegex(ValueError, "unlisted source"):
            self.check()
        self.manifest["tables"][-1]["source"] = "bank.h"
        (self.repo / "esos/bank.h").unlink()
        with self.assertRaises(FileNotFoundError):
            self.check()

    def test_missing_table_is_detected_independently_of_register_coverage(self):
        self.manifest["tables"].pop()
        self.check(False)  # Reference coverage alone cannot detect the missing bit table.
        with self.assertRaisesRegex(ValueError, "table coverage mismatch"):
            self.check()

    def extra_table(self, header, status="not_mmio", refs=None):
        self.source.write_text(self.source.read_text() + "\n" + header
                               + "\n| --- | --- |\n| 23 | peripheral |\n")
        self.manifest["sources"][0]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.manifest["tables"].append({"source": "bank.md", "line": 9, "status": status,
                                        "registers": refs or [], "note": "Genuine supplemental source table."})

    def test_genuine_irq_table_and_register_name_comment_duplicate(self):
        self.extra_table("| IRQ | Source |")
        self.assertEqual(self.check()[0]["tables"], 3)
        self.source.write_text(self.source.read_text().replace("| IRQ | Source |", "| Register Name | Comment |"))
        self.manifest["sources"][0]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.manifest["tables"][-1].update(status="duplicate", registers=["k1_test::Bank.control"])
        self.assertEqual(self.check()[2]["duplicate"], 1)
        self.manifest["tables"][-1]["registers"] = []
        with self.assertRaisesRegex(ValueError, "no register references"):
            self.check()

    def test_extra_and_c_records_cannot_replace_required_register_table(self):
        self.extra_table("| Capability | Protocol |")
        self.manifest["tables"].append({"source": "bank.h", "line": 1, "status": "generated",
                                       "registers": ["k1_test::Bank.control"], "note": "C supplement."})
        self.manifest["tables"].pop(1)
        with self.assertRaisesRegex(ValueError, r"missing=\[5\]"):
            self.check()

    def test_fake_prose_chapter_and_header_like_anchors_rejected(self):
        for anchor in ("# Register chapter", "Register facts are unavailable.",
                       "| Register Name | Comment |"):
            self.source.write_text("| Register | Offset |\n| --- | --- |\n| CONTROL | 0 |\n\n"
                                   "| Bit | Name |\n| --- | --- |\n| 0 | enable |\n\n" + anchor + "\n")
            self.manifest["sources"][0]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
            self.manifest["tables"] = self.manifest["tables"][:2] + [{"source": "bank.md", "line": 9,
                "status": "source_gap", "registers": [], "note": "No numeric layout here."}]
            with self.assertRaisesRegex(ValueError, r"unknown=\[9\]"):
                self.check()

    def test_pinned_multi_root_selection_preserves_conflicting_bytes(self):
        other = self.repo / "other"
        other.mkdir()
        (other / "bank.h").write_text("#define BASE 0x2000\n")
        self.check(False)  # Persist the fixture before direct verify calls.
        roots = {"docs": self.docs, "esos": [other, self.repo / "esos"]}
        self.assertEqual(VERIFY.verify(self.repo, roots)[1], 2)
        with self.assertRaisesRegex(ValueError, "no pinned esos match"):
            VERIFY.verify(self.repo, {"docs": self.docs, "esos": [other]})

    def test_inherited_accessors_need_explicit_accounting_like_rust(self):
        self.ir['block/k1_test::Derived'] = {'extends': 'k1_test::Bank', 'items': []}
        with self.assertRaisesRegex(ValueError, 'unaccounted registers'):
            self.check()
        self.manifest['tables'].append({'source': 'bank.h', 'line': 1, 'status': 'duplicate',
                                       'registers': ['k1_test::Derived.control'], 'note': 'Inherited source layout.'})
        self.check()

    def test_real_gpio_clones_and_system_c_supplements_require_accounting(self):
        for lane, required in (
            ('k1_io', {'k1_io::Gpio.gapmask', 'k1_io::Gpio.gpio_edr_write3'}),
            ('k1_system', {'k1_system::AhbDma.dma_rd_cnt', 'k1_system::MainPmu.posr',
                           'k1_system::ApPlic.priority', 'k1_system::ApClint.msip',
                           'k1_system::AudioPmu.lp_wakeup_mask', 'k1_system::RcpuPwm.pwm_crx'}),
        ):
            with tempfile.TemporaryDirectory() as temp:
                repo = Path(temp)
                for folder in ('provenance/expanded', 'data/registers', 'tools'):
                    (repo / folder).mkdir(parents=True)
                shutil.copy(REPO / 'tools/manual_tables.py', repo / 'tools/manual_tables.py')
                manifest = json.loads((REPO / f'provenance/expanded/{lane}.json').read_text())
                for file in manifest['registers']:
                    shutil.copy(REPO / f'data/registers/{file}.yaml', repo / f'data/registers/{file}.yaml')
                target = repo / f'provenance/expanded/{lane}.json'
                target.write_text(json.dumps(manifest))
                VERIFY.verify(repo, {})
                covered = {ref for t in manifest['tables'] for ref in t['registers']}
                self.assertTrue(required <= covered)
                for ref in required:
                    saved = json.loads(json.dumps(manifest))
                    for table in saved['tables']:
                        table['registers'] = [r for r in table['registers'] if r != ref]
                    # Remove emptied generated records, so coverage (not shape) is tested.
                    saved['tables'] = [t for t in saved['tables'] if t['registers'] or t['status'] not in {'generated', 'duplicate'}]
                    target.write_text(json.dumps(saved))
                    with self.assertRaisesRegex(ValueError, 'unaccounted registers'):
                        VERIFY.verify(repo, {})

    def test_shared_source_dispositions_are_unique_within_each_manifest(self):
        self.check(False)
        other = json.loads(json.dumps(self.manifest).replace('k1_test', 'k3_test').replace('expanded_k1_', 'expanded_k3_'))
        other['chip'] = 'k3'
        other['instances'][0]['name'] = 'RCPU_K3_TEST'
        manifest_path = self.repo / 'provenance/expanded/other.json'
        manifest_path.write_text(json.dumps(other))
        ir = json.loads(json.dumps(self.ir).replace('k1_test', 'k3_test'))
        (self.repo / 'data/registers/expanded_k3_test.yaml').write_text(json.dumps(ir))
        self.assertEqual(VERIFY.verify(self.repo, {})[0]['tables'], 4)
        other['tables'].append(other['tables'][0])
        manifest_path.write_text(json.dumps(other))
        with self.assertRaisesRegex(ValueError, 'duplicate table'):
            VERIFY.verify(self.repo, {})

    def test_source_hash_and_location(self):
        self.source.write_text(self.source.read_text() + "changed\n")
        with self.assertRaisesRegex(ValueError, "changed source"):
            self.check()
        self.manifest["sources"][0]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.manifest["instances"][0]["source"]["line"] = 2
        with self.assertRaisesRegex(ValueError, "beyond EOF"):
            self.check()

    def test_traversal_unknown_schema_width_and_unresolved_reference(self):
        for mutate in (
            lambda m: m.update(unknown=1),
            lambda m: m["sources"][0].update(path="../bank.md"),
            lambda m: m["tables"][0].update(registers=["k1_test::Bank.absent"]),
            lambda m: m["tables"][0].update(status="source_gap"),
        ):
            saved = json.loads(json.dumps(self.manifest))
            mutate(self.manifest)
            with self.assertRaises(ValueError):
                self.check()
            self.manifest = saved
        self.ir["block/k1_test::Bank"]["items"][0]["bit_size"] = 24
        with self.assertRaisesRegex(ValueError, "MMIO width"):
            self.check()

    def test_dispositions_need_notes_and_resolved_generated_coverage(self):
        self.manifest["tables"][1].update(status="duplicate", note="Bit table describes summary register.")
        self.check()
        self.manifest["tables"][1].update(status="source_gap", registers=[], note="Offset is absent from source.")
        self.check()
        self.manifest["tables"][0].update(status="not_mmio", registers=[], note="Protocol fields only.")
        with self.assertRaisesRegex(ValueError, "unaccounted registers"):
            self.check()


if __name__ == "__main__":
    unittest.main()
