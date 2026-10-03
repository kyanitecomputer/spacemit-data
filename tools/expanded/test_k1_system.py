"""Source-grounded tests for the complete K1 system lane."""

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import k1_system as k


class K1SystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir, cls.manifest = k.build()

    def item(self, block, name):
        return next(
            i
            for i in self.ir["block/k1_system::" + block]["items"]
            if i["name"] == name
        )

    def fields(self, block, name):
        return self.ir["fieldset/" + self.item(block, name)["fieldset"]]["fields"]

    def test_every_source_table_has_a_disposition(self):
        expected = {
            (t["source"], t["line"])
            for name in k.CHAPTERS
            for t in k.mt.extract(k.DEFAULT_ROOT / (k.PREFIX + name), k.DEFAULT_ROOT)
        }
        kinds = {s['path']: s.get('kind', 'docs') for s in self.manifest['sources']}
        actual = {(t["source"], t["line"]) for t in self.manifest["tables"] if kinds[t['source']] == 'docs' and t['source'] != k.PREFIX + '7.Interrupt_Assignments.md'}
        self.assertEqual(expected, actual)
        self.assertEqual(len(self.manifest['tables']), 293)
        self.assertEqual(len(expected), 284)
        for t in self.manifest["tables"]:
            self.assertIn(
                t["status"], ["generated", "duplicate", "not_mmio", "source_gap"]
            )
            if t["status"] in ["generated", "duplicate"]:
                self.assertTrue(t["registers"])
                for reg in t["registers"]:
                    block, name = reg.rsplit(".", 1)
                    self.assertTrue(
                        any(
                            i["name"] == name
                            for i in self.ir["block/" + block]["items"]
                        )
                    )
            else:
                self.assertTrue(t["note"])
        gaps = [t for t in self.manifest["tables"] if t["status"] == "source_gap"]
        self.assertEqual(len(gaps), 6)
        self.assertEqual(
            sum(t["status"] == "not_mmio" for t in self.manifest["tables"]), 12
        )

    def test_n308_local_timer_native_split_and_reset_key(self):
        for name, off in [
            ("mtime_lo", 0),
            ("mtime_hi", 4),
            ("mtimecmp_l", 8),
            ("mtimecmp_hi", 12),
            ("mtime_srw_ctrl", 0xFEC),
            ("msftrst_write", 0xFF0),
            ("mtimectl", 0xFF8),
            ("msip", 0xFFC),
        ]:
            item = self.item("N308Timer", name)
            self.assertEqual(item["byte_offset"], off)
            self.assertEqual(item["bit_size"], 32)
        self.assertEqual(self.fields("N308Timer", "msftrst_write")[0]["bit_size"], 32)
        self.assertIn(
            "0x80000A5F", self.fields("N308Timer", "msftrst_write")[0]["description"]
        )
        self.assertEqual(
            [
                (f["name"], f["bit_offset"])
                for f in self.fields("N308Timer", "mtimectl")
            ],
            [("clksrc", 2), ("cmpclren", 1), ("timestop", 0)],
        )
        c = (k.ESOS_ROOT / k.ESOS_FILES[0]).read_text()
        self.assertIn("__SW(addr, -1U)", c)
        self.assertIn("__SW(addr + 4, (uint32_t)(value >> 32))", c)
        self.assertIn("0x80000A5FUL", c)

    def test_eclic_bytes_dynamic_bits_and_firmware_window(self):
        self.assertEqual(self.item("N308Eclic", "mth")["byte_offset"], 11)
        self.assertEqual(self.item("N308Eclic", "mth")["bit_size"], 8)
        for n, off in [
            ("clicintip_i", 0x1000),
            ("clicintie_i", 0x1001),
            ("clicintattr_i", 0x1002),
            ("clicintctl_i", 0x1003),
        ]:
            i = self.item("N308Eclic", n)
            self.assertEqual(i["bit_size"], 8)
            self.assertEqual(i["byte_offset"], off)
            self.assertEqual(i["array"], {"len": 69, "stride": 4})
        ctl = self.item("N308Eclic", "clicintctl_i")
        self.assertNotIn("fieldset", ctl)
        self.assertEqual(ctl["access"], "ReadWrite")
        info = self.fields("N308Eclic", "clicinfo")
        self.assertEqual(
            [(f["bit_offset"], f["bit_size"]) for f in info],
            [(21, 4), (13, 8), (0, 13)],
        )
        c = (k.ESOS_ROOT / k.ESOS_FILES[1]).read_text()
        self.assertIn("uint32_t numint:13", c)
        self.assertIn("uint8_t INTCTRL", c)

    def test_all_aliases_and_masks_are_exact_and_no_modify(self):
        for block, pairs in self.manifest["aliases"].items():
            items = {i["name"]: i for i in self.ir["block/" + block]["items"]}
            for r, w in pairs:
                self.assertEqual(items[r]["byte_offset"], items[w]["byte_offset"])
                self.assertEqual(items[r]["bit_size"], items[w]["bit_size"])
                self.assertEqual(items[r]["access"], "Read")
                self.assertEqual(items[w]["access"], "Write")
                self.assertNotIn("array", items[r])
        for ref in self.manifest["write_mask_registers"]:
            block, n = ref.rsplit(".", 1)
            self.assertEqual(
                next(i for i in self.ir["block/" + block]["items"] if i["name"] == n)[
                    "access"
                ],
                "Write",
            )
        for name in ["isrw", "icr", "wdr"]:
            self.assertEqual(self.item("Mailbox", name)["access"], "Write")
        self.assertEqual(self.item("Mailbox", "iir_read")["byte_offset"], 16)
        self.assertEqual(self.item("Mailbox", "iir_write")["access"], "Write")
        self.assertEqual(self.item("Rtc", "rtc_status_read")["access"], "Read")
        self.assertEqual(self.item("Rtc", "rtc_status_write")["access"], "Write")
        self.assertEqual(len(self.fields("Rtc", "rtc_status_write")), 4)
        self.assertEqual(
            self.item("TimerWatchdog", "timer_watchdog_status_write")["access"], "Write"
        )
        self.assertEqual(
            self.item("Rng", "rng_interrupt_status_read")["access"], "Read"
        )

    def test_sparse_arrays_preserve_logical_order_and_bounds(self):
        self.assertEqual(
            self.item("Pll", "pllx_sw1_ctrl")["array"]["offsets"], [0, 24, 36]
        )
        self.assertEqual(
            self.item("TimerWatchdog", "timer_match")["array"]["offsets"],
            [0, 4, 8, 16, 20, 24, 32, 36, 40],
        )
        i = self.item("ApPmu", "pmu_c0_capmp_idle_cfgx_x")
        self.assertEqual(i["byte_offset"], 0xE4)
        self.assertEqual(i["array"]["offsets"], [60, 0, 108, 112])
        for key, block in self.ir.items():
            if not key.startswith("block/"):
                continue
            for i in block["items"]:
                a = i.get("array", {})
                offsets = a.get(
                    "offsets", [j * a.get("stride", 1) for j in range(a.get("len", 1))]
                )
                self.assertEqual(len(offsets), len(set(offsets)))
                self.assertTrue(all(o >= 0 for o in offsets))
                if "bit_size" in i:
                    self.assertTrue(
                        all(
                            (i["byte_offset"] + o) % (i["bit_size"] // 8) == 0
                            for o in offsets
                        )
                    )
        for name, off in [
            ("dma_sarn", 0x80),
            ("dma_darn", 0x84),
            ("dma_cntrn", 0x88),
            ("dma_rd_cnt", 0xA8),
        ]:
            i = self.item("AhbDma", name)
            self.assertEqual(i["byte_offset"], off)
            self.assertEqual(i["array"], {"len": 16, "stride": 64})
        self.assertEqual(self.item("Pdma", "drcmr_x")["array"]["len"], 52)
        self.assertEqual(self.item("Rtc", "rtc_backup")["array"]["len"], 5)

    def test_instance_views_and_pwm_discontinuity(self):
        instances = {i["name"]: i for i in self.manifest["instances"]}
        self.assertEqual(len(instances), len(self.manifest["instances"]))
        for name, base in [
            ("AP_K1_SYSTEM_PWM7", 0xD401BC00),
            ("AP_K1_SYSTEM_PWM8", 0xD4020000),
            ("AP_K1_SYSTEM_PWM19", 0xD4022C00),
            ("RCPU_K1_SYSTEM_PWM0", 0xC0888100),
            ("RCPU_K1_SYSTEM_PWM9", 0xC0888A00),
            ("RCPU_K1_SYSTEM_AUDIO_PMU", 0xC088C000),
            ("SECURE_K1_SYSTEM_RTC", 0xF0615000),
            ("AP_K1_SYSTEM_PLIC", 0xE0000000),
            ("AP_K1_SYSTEM_CLINT", 0xE4000000),
        ]:
            self.assertEqual(instances[name]["address"], base)
        self.assertNotIn(
            "pwm_outcnt",
            [i["name"] for i in self.ir["block/k1_system::RcpuPwm"]["items"]],
        )

    def test_ap_interrupt_layouts(self):
        self.assertEqual(
            self.item("ApPlic", "priority")["array"], {"len": 159, "stride": 4}
        )
        self.assertEqual(
            self.item("ApPlic", "pending")["array"], {"len": 5, "stride": 4}
        )
        self.assertEqual(
            self.item("ApPlic", "context")["array"], {"len": 16, "stride": 0x1000}
        )
        self.assertEqual(
            self.item("PlicContext", "claim_complete_read")["access"], "Read"
        )
        self.assertEqual(
            self.item("PlicContext", "claim_complete_write")["access"], "Write"
        )
        self.assertEqual(self.item("ApClint", "mtimecmp_hi")["byte_offset"], 0x4004)
        self.assertEqual(
            self.item("ApClint", "mtimecmp_hi")["array"], {"len": 8, "stride": 8}
        )
        self.assertEqual(self.item("ApClint", "mtime_hi")["byte_offset"], 0xBFFC)

    def test_deterministic_committed_json_ir(self):
        root = Path(__file__).resolve().parents[2]
        for path, obj in [
            ("data/registers/expanded_k1_system.yaml", self.ir),
            ("provenance/expanded/k1_system.json", self.manifest),
        ]:
            self.assertEqual(
                (root / path).read_text(), json.dumps(obj, indent=2) + "\n"
            )
        self.assertEqual(k.build(), (self.ir, self.manifest))

    def test_malformed_offsets_fail_closed(self):
        for expr in ["0x8~0x4(0x4)", "0x0/0x0", "0x0~0x7(0x4)", '__import__("os")']:
            with self.assertRaises((ValueError, SyntaxError)):
                k.parse_offsets(expr)

    def test_malformed_field_bounds_fail_in_importer(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for chapter in [
                "6.Address_Mapping.md",
                "7.Interrupt_Assignments.md",
            ] + k.CHAPTERS:
                target = root / (k.PREFIX + chapter)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(k.DEFAULT_ROOT / (k.PREFIX + chapter), target)
            p = root / (k.PREFIX + "14.RCPU_Subsystem.md")
            p.write_text(
                p.read_text().replace("| 23:0 | Count |", "| 32:0 | Count |", 1)
            )
            with self.assertRaisesRegex(ValueError, "out of bounds"):
                k.build(root)

    def test_all_document_and_esos_fingerprints(self):
        for s in self.manifest["sources"]:
            if s["kind"] == "linux":
                self.assertEqual(s["sha256"], k.LINUX_SOURCES[s["path"]])
                continue
            root = k.DEFAULT_ROOT if s["kind"] == "docs" else k.ESOS_ROOT
            self.assertEqual(
                s["sha256"], hashlib.sha256((root / s["path"]).read_bytes()).hexdigest()
            )


if __name__ == "__main__":
    unittest.main()
