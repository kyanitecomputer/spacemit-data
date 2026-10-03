"""Source-named regression tests for K3 system facts and importer rejection."""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import k3_system as ks


class K3SystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.importer = ks.Importer(ks.DEFAULT_SOURCE, ks.DEFAULT_ESOS)
        cls.ir, cls.manifest = cls.importer.run()

    def item(self, block, name):
        return next(
            i
            for i in self.ir["block/k3_system::" + block]["items"]
            if i["name"] == name
        )

    def test_all_owned_markdown_field_and_summary_tables_accounted(self):
        accounted = {(t["source"], t["line"]): t for t in self.manifest["tables"]}
        for source in self.manifest["sources"]:
            if source["kind"] != "docs" or Path(source["path"]).name in {
                "06_address_map.md",
                "07_interrupts.md",
            }:
                continue
            for t in ks.mt.extract(
                ks.DEFAULT_SOURCE / source["path"], ks.DEFAULT_SOURCE
            ):
                disposition = accounted[(t["source"], t["line"])]
                self.assertEqual(
                    disposition["status"],
                    "generated" if t["kind"] == "fields" else "duplicate",
                    t["id"],
                )
                self.assertTrue(disposition["registers"], t["id"])
        self.assertEqual(len(accounted), len(self.manifest["tables"]))
        self.assertEqual(len(accounted), 367)
        self.assertFalse(any(t["status"] == "source_gap" for t in accounted.values()))
        decisions = [c["decision"] for c in self.manifest["corrections"]]
        self.assertTrue(any("X100/A100 ACLINT/APLIC/IMSIC" in d for d in decisions))
        self.assertTrue(any(d == "Navigation index; no numeric register definitions." for d in decisions))

    def test_mailbox_threshold_fifo_and_corrected_channel_labels(self):
        self.assertEqual(
            self.item("Mailbox4", "irqthr0_u")["array"], {"len": 2, "stride": 16}
        )
        self.assertEqual(self.item("Mailbox16", "irqthr3_u")["byte_offset"], 0x18C)
        self.assertEqual(self.item("Mailbox16", "message_m_read")["array"]["len"], 16)
        self.assertEqual(self.item("Mailbox8", "message_m_write")["array"]["len"], 8)
        self.assertEqual(self.item("Mailbox4", "message_m_write")["access"], "Write")
        item = self.item("Mailbox16", "irqenable_clr_u_write")
        fields = self.ir["fieldset/" + item["fieldset"]]["fields"]
        self.assertEqual(
            next(f for f in fields if f["bit_offset"] == 31)["name"], "not_full_dis_15"
        )
        self.assertEqual(
            next(f for f in fields if f["bit_offset"] == 0)["name"], "new_msg_dis_0"
        )

    def test_gpio_sparse_banks_c_omission_and_secure_bases(self):
        self.assertEqual(
            self.item("Gpio", "bank")["array"]["offsets"], [0, 0x40, 0x80, 0x100]
        )
        self.assertEqual(
            self.item("Rgpio", "bank")["array"], {"len": 2, "stride": 0x40}
        )
        self.assertEqual(self.item("GpioBank", "gcrer")["byte_offset"], 0x28)
        self.assertEqual(self.item("GpioBank", "gedr_write")["access"], "Write")
        instances = {i["name"]: i for i in self.manifest["instances"]}
        self.assertEqual(instances["SECURE_K3_SYSTEM_SEC_GPIO3"]["address"], 0xF0619100)

    def test_timer_arrays_and_native_watchdog_access(self):
        self.assertEqual(
            self.item("TimerWatchdog", "timer_match")["array"]["offsets"],
            [0, 4, 8, 16, 20, 24, 32, 36, 40],
        )
        self.assertEqual(
            self.item("TimerWatchdog", "timer_watchdog_match_enable")["byte_offset"],
            0xB8,
        )
        self.assertEqual(
            self.item("TimerWatchdog", "timer_watchdog_match")["byte_offset"], 0xBC
        )
        self.assertEqual(
            self.item("TimerWatchdog", "timer_watchdog_first_access")["access"], "Write"
        )

    def test_timer_restart_and_w0c_status_keep_readback_without_modify(self):
        for name, offset in [('timer_count_restart', 8), ('timer_watchdog_status', 0xc0)]:
            read = self.item('TimerWatchdog', name + '_read')
            write = self.item('TimerWatchdog', name + '_write')
            self.assertEqual((read['access'], write['access']), ('Read', 'Write'))
            self.assertEqual((read['byte_offset'], write['byte_offset']), (offset, offset))
            self.assertEqual((read['bit_size'], write['bit_size']), (32, 32))
            self.assertIn('k3_system::TimerWatchdog.' + name + '_write', self.manifest['write_mask_registers'])
            self.assertIn([name + '_read', name + '_write'], self.manifest['aliases']['k3_system::TimerWatchdog'])
        status = self.item('TimerWatchdog', 'timer_watchdog_status_write')
        self.assertIn('write0 clears WTS, write1 has no effect (W0C)', status['description'])
        fields = self.ir['fieldset/' + status['fieldset']]['fields']
        self.assertEqual([(f['name'], f['bit_offset'], f['bit_size']) for f in fields], [('wts', 0, 1)])
        self.assertIn('Source access RW', fields[0]['description'])

    def test_clock_rw_request_prose_enforces_explicit_configuration_writes(self):
        expected = [
            ('Pmuap', 'pmu_lcd_clk_rst_ctrl5', 0x274, 'dpu_aclk_req', 31),
            ('Pmuap', 'pmu_cci_clk_ctrl', 0x300, 'cci550_fc_req', 12),
            ('Pmuap', 'pmua_aclk_ctrl', 0x388, 'aclk_fc_req', 4),
            ('RcpuPmu', 'rcpu_bus_clk_ctrl', 0xc0, 'bus_clk_fc_req', 8),
            ('RcpuPmu', 'rt24_core0_clk_ctrl', 0xc4, 'core_clk_fc_req', 8),
            ('RcpuPmu', 'rt24_core1_clk_ctrl', 0xc8, 'core_clk_fc_req', 8),
        ] + [('Pmuap', f'pmua_cpu_c{n}_clk_ctrl', offset, f'c{n}_clk_fc_req', 12)
             for n, offset in enumerate([0x38c, 0x390, 0x394, 0x208])]
        for block, name, offset, field, bit in expected:
            read, write = self.item(block, name + '_read'), self.item(block, name + '_write')
            self.assertEqual((read['access'], write['access']), ('Read', 'Write'))
            self.assertEqual((read['byte_offset'], write['byte_offset']), (offset, offset))
            self.assertEqual((read['bit_size'], write['bit_size']), (32, 32))
            fields = {f['name']: f for f in self.ir['fieldset/' + write['fieldset']]['fields']}
            self.assertEqual((fields[field]['bit_offset'], fields[field]['bit_size']), (bit, 1))
            self.assertIn('Source access RW', fields[field]['description'])
            self.assertIn('k3_system::' + block + '.' + name + '_write', self.manifest['write_mask_registers'])
            self.assertIn([name + '_read', name + '_write'], self.manifest['aliases']['k3_system::' + block])
            self.assertIn('zero inactive requests', write['description'])
        config = self.item('Pmuap', 'pmu_cci_clk_ctrl_write')
        fields = {f['name'] for f in self.ir['fieldset/' + config['fieldset']]['fields']}
        self.assertIn('cci550_pllsel', fields)
        self.assertIn('cci550_biu_clk_div', fields)
        self.assertEqual(self.item('Pmuap', 'pmu_cc2_ap')['access'], 'ReadWrite')
        self.assertEqual(self.item('Pmuap', 'pmu_lcd_clk_res_ctrl2')['access'], 'ReadWrite')
        # Existing mixed W1C/RW request words retain every writable field.
        lcd = self.item('Pmuap', 'pmu_lcd_clk_res_ctrl1_write')
        self.assertIn('lcd_dscclk_fc_req', {f['name'] for f in self.ir['fieldset/' + lcd['fieldset']]['fields']})

    def test_pdma_channels_requests_high_addresses_and_mixed_w1c(self):
        self.assertEqual(
            self.item("Pdma", "dcsr_x_write")["array"], {"len": 16, "stride": 4}
        )
        self.assertEqual(
            self.item("Pdma", "drcmr_x")["array"], {"len": 52, "stride": 4}
        )
        self.assertEqual(self.item("Pdma", "dtadr_h_x")["byte_offset"], 0x308)
        self.assertEqual(
            self.item("Pdma", "dtadr_h_x")["array"], {"len": 16, "stride": 16}
        )
        write = self.item("Pdma", "dcsr_x_write")
        self.assertNotIn(
            "stopintr",
            {f["name"] for f in self.ir["fieldset/" + write["fieldset"]]["fields"]},
        )

    def test_hdmas_prose_corrections_and_unbound_instances(self):
        self.assertEqual(self.item("Hdma", "irq_source_write")["byte_offset"], 0x88)
        self.assertEqual(self.item("Hdma", "transfer_submit_write")["access"], "Write")
        item = self.item("Hdma", "x_length")
        self.assertEqual(
            self.ir["fieldset/" + item["fieldset"]]["fields"][0]["bit_size"], 24
        )
        self.assertFalse(
            any(i["block"] == "k3_system::Hdma" for i in self.manifest["instances"])
        )

    def test_iopmp_configuration_arrays_raw_parameter_and_native_width(self):
        self.assertEqual(
            self.item("IopmpSid16Entries256", "entry_addr")["array"],
            {"len": 256, "stride": 16},
        )
        self.assertEqual(
            self.item("IopmpSid16", "srcmd_en_write")["array"],
            {"len": 16, "stride": 32},
        )
        self.assertNotIn("fieldset", self.item("IopmpSid16", "deviceid"))
        self.assertEqual(self.item("IopmpSid1", "version")["byte_offset"], 0)
        self.assertEqual(self.item("IopmpSid1", "hwcfg0_write")["access"], "Write")
        self.assertTrue(
            all(
                i["bit_size"] == 32
                for k, v in self.ir.items()
                if k.startswith("block/")
                for i in v["items"]
                if "access" in i
            )
        )

    def test_pm_c_hart_order_and_clock_nonmonotonic_order(self):
        i = self.item("Pmuap", "core_idle_cfg")
        self.assertEqual(
            [i["byte_offset"] + o for o in i["array"]["offsets"]],
            [
                0x124,
                0x128,
                0x160,
                0x164,
                0x304,
                0x308,
                0x30C,
                0x310,
                0x340,
                0x344,
                0x348,
                0x34C,
                0x20C,
                0x210,
                0x214,
                0x218,
            ],
        )
        i = self.item("Apbclock", "apbc_twsin_clk_rst")
        self.assertEqual(
            [i["byte_offset"] + o for o in i["array"]["offsets"]],
            [0x2C, 0x30, 0x38, 0x40, 0x4C, 0x60, 0x20],
        )
        self.assertEqual(
            self.item("CpuConfig", "c3_rvbaddr_hi_addr_read")["byte_offset"], 0x264
        )

    def test_manifest_refs_aliases_masks_and_exact_overlap_validation(self):
        blocks = {
            k.removeprefix("block/"): v
            for k, v in self.ir.items()
            if k.startswith("block/")
        }
        for t in self.manifest["tables"]:
            for ref in t["registers"]:
                block, name = ref.rsplit(".", 1)
                self.assertIn(name, {i["name"] for i in blocks[block]["items"]})
        for ref in self.manifest["write_mask_registers"]:
            block, name = ref.rsplit(".", 1)
            self.assertEqual(
                next(i for i in blocks[block]["items"] if i["name"] == name)["access"],
                "Write",
            )
        for block, definition in blocks.items():
            occupied = {}
            for i in definition["items"]:
                if "access" not in i:
                    continue
                a = i.get("array", {})
                positions = a.get(
                    "offsets", [n * a.get("stride", 0) for n in range(a.get("len", 1))]
                )
                for off in positions:
                    for byte in range(
                        i["byte_offset"] + off, i["byte_offset"] + off + 4
                    ):
                        if byte in occupied:
                            other = occupied[byte]
                            self.assertTrue(
                                any(
                                    i["name"] in g and other in g
                                    for g in self.manifest["aliases"].get(block, [])
                                ),
                                (block, i["name"], other),
                            )
                        occupied[byte] = i["name"]

    def test_malformed_ranges_fields_and_unsafe_expression_rejected(self):
        for expr in ["0x10~0x18 (0x0)", "0x10/0x10", "0x3", '__import__("os")']:
            with self.assertRaises((ValueError, SyntaxError)):
                ks.offsets(expr)
        p = ks.DEFAULT_SOURCE / (ks.DOC + "16_peripherals/pwm.md")
        table = copy.deepcopy(ks.mt.extract(p, ks.DEFAULT_SOURCE)[0])
        table["rows"][1]["values"][0] = "99:8"
        with self.assertRaises(ValueError):
            self.importer.fields(table)
        table = copy.deepcopy(ks.mt.extract(p, ks.DEFAULT_SOURCE)[0])
        table["rows"][1]["values"][2] = "unknown"
        with self.assertRaises(ValueError):
            self.importer.fields(table)

    def test_esos_clock_macro_offsets_match_all_correlated_regions(self):
        macros = self.importer.macros(ks.ESOS + "drivers/clk/ccu-spacemit-k3.h")
        regions = {
            "MPMU_": "Pmumain",
            "APBS_": "ApbSpare",
            "RCPU1_": "RcpuUartctrl",
            "RCPU2_": "RcpuI2Sctrl",
            "RCPU3_": "RcpuSpictrl",
            "RCPU4_": "RcpuI2Cctrl",
            "RCPU5_": "RcpuPmu",
            "RCPU6_": "RcpuPwmctrl",
        }
        matched = 0
        for name, (offset, line) in macros.items():
            region = next(
                (block for prefix, block in regions.items() if name.startswith(prefix)),
                None,
            )
            if region is None:
                continue
            all_offsets = set()
            for item in self.ir["block/k3_system::" + region]["items"]:
                a = item.get("array", {})
                all_offsets.update(
                    item["byte_offset"] + value
                    for value in a.get(
                        "offsets",
                        [n * a.get("stride", 0) for n in range(a.get("len", 1))],
                    )
                )
            self.assertIn(offset, all_offsets, (name, line))
            matched += 1
        self.assertGreaterEqual(matched, 60)

    @unittest.skipUnless(shutil.which("cc"), "C compiler not available")
    def test_esos_mailbox_struct_lp64_abi_conflicts_with_threshold_formula(self):
        # Compile numeric typedefs directly from the pinned external header. This
        # is an LP64 ABI comparison, not a claim of hardware threshold behavior.
        path = ks.DEFAULT_ESOS / (ks.ESOS + "drivers/mailbox/k3_mailbox.h")
        text = path.read_text()
        declarations = text[
            text.index("typedef volatile union mbox_msg") : text.index(
                "struct spacemit_mailbox {"
            )
        ]
        fixture = (
            "#include <stdint.h>\n#include <stddef.h>\ntypedef uint32_t rt_uint32_t;\n"
            + declarations
        )
        fixture += """
_Static_assert(sizeof(unsigned long) == 8, "LP64 oracle");
_Static_assert(sizeof(mbox_irqthresh_t) == 16, "C struct is not a union");
_Static_assert(sizeof(mbox_thresh_t) == 32, "C threshold user stride");
_Static_assert(offsetof(mbox_reg_desc_t, mbox_thresh) == 0x180, "threshold base");
_Static_assert(offsetof(mbox_reg_desc_t, mbox_irq) == 0x100, "IRQ base");
_Static_assert(sizeof(mbox_irq_t) == 0x10, "IRQ user stride");
_Static_assert(offsetof(mbox_reg_desc_t, mbox_msg) == 0x40, "FIFO base");
int main(void) { return 0; }
"""
        with tempfile.TemporaryDirectory(prefix="k3-system-c-abi-") as directory:
            source = Path(directory) / "mailbox_abi.c"
            source.write_text(fixture)
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Werror",
                    str(source),
                    "-o",
                    str(Path(directory) / "abi"),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [str(Path(directory) / "abi")], check=True, capture_output=True
            )
        self.assertEqual(self.item("Mailbox4", "irqthr0_u")["array"]["stride"], 16)

    def test_committed_json_ir_and_manifest_are_deterministic(self):
        repo = Path(__file__).resolve().parents[2]
        self.assertEqual(
            json.loads((repo / "data/registers/expanded_k3_system.yaml").read_text()),
            self.ir,
        )
        self.assertEqual(
            json.loads((repo / "provenance/expanded/k3_system.json").read_text()),
            self.manifest,
        )


if __name__ == "__main__":
    unittest.main()
