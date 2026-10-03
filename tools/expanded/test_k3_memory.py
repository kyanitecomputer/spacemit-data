"""Source-grounded K3 native widths, arrays, aliases, corrections and accounting."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import k3_memory as importer


class K3MemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir, cls.manifest = importer.build()

    def register(self, block, name):
        return next(
            item
            for item in self.ir["block/k3_memory::" + block]["items"]
            if item["name"] == name
        )

    def fields(self, register):
        return {
            f["name"]: f for f in self.ir["fieldset/" + register["fieldset"]]["fields"]
        }

    def test_every_documented_register_table_is_generated(self):
        documented = set()
        for chapter in importer.CHAPTERS:
            path = importer.DOC_DIR + chapter
            documented.update(
                (path, t["line"])
                for t in importer.extract(importer.DOC_ROOT / path, importer.DOC_ROOT)
            )
        self.assertEqual(len(documented), 202)
        accounted = {
            (t["source"], t["line"])
            for t in self.manifest["tables"]
            if t["status"] == "generated" and t["source"].endswith(".md")
        }
        self.assertEqual(documented, accounted)
        self.assertTrue(
            all(
                t["registers"]
                for t in self.manifest["tables"]
                if t["status"] == "generated"
            )
        )

    def test_native_sdhci_byte_and_halfword_offsets(self):
        expected = {
            "sd_block_size": (4, 16),
            "sd_block_count": (6, 16),
            "sd_transfer_mode": (12, 16),
            "sd_command_read": (14, 16),
            "sd_command_write": (14, 16),
            "sd_host_control": (0x28, 8),
            "sd_power_control": (0x29, 8),
            "sd_wake_up_control": (0x2B, 8),
            "sd_clock_control_read": (0x2C, 16),
            "sd_timeout_control": (0x2E, 8),
            "sd_software_reset_read": (0x2F, 8),
            "sd_adma_error_status": (0x54, 8),
            "sd_host_version": (0xFE, 16),
        }
        for name, fact in expected.items():
            item = self.register("Sdhci", name)
            self.assertEqual((item["byte_offset"], item["bit_size"]), fact)
        self.assertEqual(
            self.fields(self.register("Sdhci", "sd_command_write"))["cmd_index"][
                "bit_offset"
            ],
            8,
        )
        self.assertEqual(
            self.fields(self.register("Sdhci", "sd_software_reset_write"))[
                "sw_rst_dat"
            ]["bit_offset"],
            2,
        )

    def test_sd_command_trigger_does_not_reclassify_transfer_configuration(self):
        self.assertEqual(self.register('Sdhci', 'sd_transfer_mode')['access'], 'ReadWrite')
        self.assertIn(['sd_command_read', 'sd_command_write'], self.manifest['aliases']['k3_memory::Sdhci'])
        fields = self.fields(self.register('Sdhci', 'sd_command_write'))
        self.assertEqual((fields['cmd_index']['bit_offset'], fields['cmd_index']['bit_size']), (8, 6))
        self.assertEqual(fields['resp_type']['bit_offset'], 0)

    def test_sdhci_v4_c_controls_resolve_reserved_manual_bits(self):
        fields = self.fields(self.register("Sdhci", "sd_host_control2_write"))
        self.assertEqual(fields["adma2_length_mode"]["bit_offset"], 10)
        self.assertEqual(fields["addressing64_enable"]["bit_offset"], 13)

    def test_w1c_fifo_and_mixed_registers_do_not_allow_modify(self):
        for block, name in [
            ("Sdhci", "sd_command"),
            ("Sdhci", "sd_normal_int_status"),
            ("Sdhci", "sd_buffer_data_port_01"),
            ("Sdhci", "cqe_cqtdbr_reg"),
            ("Ufs", "interrupt_status"),
            ("Dsi", "dsi_irq_st"),
            ("Dsi", "dsi_phy_code_1"),
        ]:
            read = self.register(block, name + "_read")
            write = self.register(block, name + "_write")
            self.assertEqual(read["access"], "Read")
            self.assertEqual(write["access"], "Write")
            self.assertEqual(read["byte_offset"], write["byte_offset"])
            self.assertIn(
                "k3_memory::" + block + "." + name + "_write",
                self.manifest["write_mask_registers"],
            )
        # Read-only summary bits aren't offered as W1C setters.
        fields = self.fields(self.register("Sdhci", "sd_normal_int_status_write"))
        self.assertNotIn("err_int", fields)
        self.assertNotIn("cq_int", fields)

    def test_dsi_hardware_cleared_requests_are_direct_writes(self):
        for name in [
            "dsi_cpu_cmd_0",
            "dsi_cpu_cmd_3",
            "dsi_rx_pkt_ctrl",
            "dsi_lcd_bdg_ctrl0",
        ]:
            self.assertEqual(self.register("Dsi", name + "_read")["access"], "Read")
            self.assertEqual(self.register("Dsi", name + "_write")["access"], "Write")
        self.assertIn(
            "RWAC",
            self.fields(self.register("Dsi", "dsi_cpu_cmd_0_write"))["cfg_cpu_cmd_req"][
                "description"
            ],
        )

    def test_all_scalar_qspi_and_dp_c_definitions_are_present(self):
        qspi = self.ir["block/k3_memory::Qspi"]["items"]
        for line, source in enumerate(
            (importer.C_ROOT / "drivers/spi/k1x_qspi.c").read_text().splitlines(), 1
        ):
            match = re.fullmatch(
                r"#define\s+(QSPI_\w+)\s+(0x[0-9a-fA-F]+)", source.strip()
            )
            if match and 59 <= line <= 143 and not match[1].endswith("_VALUE"):
                self.assertIn(int(match[2], 16), {item["byte_offset"] for item in qspi})
        dp = self.ir["block/k3_memory::Dp"]["items"]
        for source in (
            (importer.C_ROOT / "drivers/video/spacemit/dp/inno_dp_reg.h")
            .read_text()
            .splitlines()
        ):
            match = re.match(
                r"#define\s+SOC_DPTX_(\w+)\s+(0x[0-9a-fA-F]+),(\d+),(\d+)", source
            )
            if match:
                field_name = importer.identifier(match[1])
                registers = [
                    item
                    for item in dp
                    if item["byte_offset"] == int(match[2], 16)
                    and item["access"] == "Read"
                ]
                self.assertEqual(len(registers), 1)
                field = self.fields(registers[0])[field_name]
                self.assertEqual(
                    (field["bit_offset"], field["bit_size"]),
                    (int(match[4]), int(match[3]) - int(match[4]) + 1),
                )

    def test_cqe_halt_column_correction(self):
        fields = self.fields(self.register("Sdhci", "cqe_cqctl_reg_write"))
        self.assertIn("halt", fields)
        self.assertTrue(
            any(
                "HALT is writable" in c["decision"]
                for c in self.manifest["corrections"]
            )
        )

    def test_address_halves_remain_native_u32(self):
        self.assertEqual(
            self.register("Dpu", "framebuffer_address_lo")["byte_offset"], 0x1024
        )
        self.assertEqual(
            self.register("Dpu", "framebuffer_address_hi")["byte_offset"], 0x1028
        )
        self.assertEqual(
            self.register("Ufs", "utp_transfer_request_list_base_address")[
                "byte_offset"
            ],
            0x50,
        )
        self.assertEqual(
            self.register("Ufs", "utp_transfer_request_list_base_address_upper_32bits")[
                "byte_offset"
            ],
            0x54,
        )
        self.assertTrue(
            all(i["bit_size"] == 32 for i in self.ir["block/k3_memory::Dpu"]["items"])
        )

    def test_qspi_buffer_array_and_exact_masks(self):
        item = self.register("Qspi", "qspi_rbdr")
        self.assertEqual(item["array"], {"len": 32, "stride": 4})
        self.assertEqual(item["byte_offset"] + 31 * 4, 0x27C)
        mcr = self.fields(self.register("Qspi", "qspi_mcr_write"))
        self.assertEqual((mcr["isd"]["bit_offset"], mcr["isd"]["bit_size"]), (16, 4))
        self.assertEqual(mcr["clr_rxf"]["bit_offset"], 10)
        self.assertEqual(
            self.register("Qspi", "qspi_lut_1_3_write")["byte_offset"], 0x32C
        )
        # Numeric definitions unused by the C writer are not invented setters.
        self.assertEqual(self.register("Qspi", "qspi_tcr")["access"], "Read")

    def test_sparse_phy_lane_indices_and_bus_width(self):
        tx = self.register("DdrPhy", "tx_impedance_array")
        offsets = [tx["byte_offset"] + delta for delta in tx["array"]["offsets"]]
        self.assertEqual(len(offsets), 20)
        self.assertEqual(
            offsets[:5], [v * 4 for v in [0x10040, 0x10042, 0x10043, 0x10044, 0x10045]]
        )
        self.assertEqual(offsets[-1], 0x13045 * 4)
        self.assertNotIn(0x10041 * 4, offsets)
        self.assertEqual(tx["bit_size"], 32)
        self.assertEqual(tx["access"], "Write")
        self.assertEqual(
            self.register("DdrPhy", "micro_reset")["byte_offset"], 0xD0099 * 4
        )
        self.assertEqual(self.register("Ddrc", "r_010000_read")["byte_offset"], 0x10000)

    def test_dp_status_has_no_speculative_write_fields(self):
        register = self.register("Dp", "r_0180_write")
        self.assertNotIn("ad_lock_corepll", self.fields(register))
        self.assertIn(
            "ad_lock_corepll", self.fields(self.register("Dp", "r_0180_read"))
        )

    def test_instance_bases_and_no_unestablished_views(self):
        instances = {i["name"]: i for i in self.manifest["instances"]}
        self.assertEqual(instances["AP_K3_MEMORY_DSI00"]["address"], 0xD421A800)
        self.assertEqual(instances["AP_K3_MEMORY_DSI01"]["address"], 0xD421AA00)
        self.assertEqual(instances["AP_K3_MEMORY_DDR_PHY1"]["address"], 0xCC800000)
        self.assertTrue(all(i["view"] == "ap" for i in instances.values()))
        self.assertFalse(any("VPU" in name or "SECURE" in name for name in instances))

    def test_every_reference_alias_and_field_bound(self):
        registers = {
            key.removeprefix("block/") + "." + item["name"]: item
            for key, block in self.ir.items()
            if key.startswith("block/")
            for item in block["items"]
        }
        refs = {r for t in self.manifest["tables"] for r in t["registers"]}
        self.assertEqual(refs, set(registers))
        for block, aliases in self.manifest["aliases"].items():
            for group in aliases:
                entries = [registers[block + "." + name] for name in group]
                self.assertEqual({v["access"] for v in entries}, {"Read", "Write"})
                self.assertEqual(
                    len({(v["byte_offset"], v["bit_size"]) for v in entries}), 1
                )
        for key, fieldset in self.ir.items():
            if key.startswith("fieldset/"):
                spans = set()
                for field in fieldset["fields"]:
                    bits = set(
                        range(
                            field["bit_offset"], field["bit_offset"] + field["bit_size"]
                        )
                    )
                    self.assertFalse(spans & bits)
                    self.assertLess(max(bits), fieldset["bit_size"])
                    spans |= bits

    def test_reset_facts_are_not_chiptool_defaults(self):
        field = self.fields(self.register("Dsi", "dsi_tx_timer"))["cfg_tx_timer_cnt"]
        self.assertIn("reset 0xffffffff", field["description"])
        self.assertNotIn("reset_value", field)
        self.assertFalse(
            any(
                "default" in item
                for value in self.ir.values()
                for item in value.get("fields", []) + value.get("items", [])
            )
        )

    def test_known_source_gaps_are_specific(self):
        gaps = [c["decision"] for c in self.manifest["corrections"]]
        self.assertEqual(len(self.manifest["tables"]), 221)
        self.assertFalse(any(t["status"] == "source_gap" for t in self.manifest["tables"]))
        self.assertTrue(any("Dynamic PLL offset/32-bit container known" in note for note in gaps))
        self.assertTrue(any("eight-plane" in note for note in gaps))
        self.assertTrue(any("ATOP" in note for note in gaps))
        self.assertTrue(any("Video chapter" in note for note in gaps))

    def malformed(self, old, new, message):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for chapter in importer.CHAPTERS + [
                "06_address_map.md",
                "07_interrupts.md",
            ]:
                dest = root / importer.DOC_DIR / chapter
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(importer.DOC_ROOT / importer.DOC_DIR / chapter, dest)
            path = root / importer.DOC_DIR / "09_memory_storage.md"
            source = path.read_text()
            self.assertIn(old, source)
            path.write_text(source.replace(old, new, 1))
            with self.assertRaisesRegex(ValueError, message):
                importer.build(root, importer.C_ROOT)

    def test_malformed_field_range_fails(self):
        self.malformed("31:16 | DMA_ADDR_H", "15:16 | DMA_ADDR_H", "invalid bit range")

    def test_malformed_native_boundary_fails(self):
        self.malformed(
            "31:16 | BLOCK_COUNT", "31:15 | BLOCK_COUNT", "field crosses native"
        )

    def test_unknown_access_fails(self):
        self.malformed("DMA_ADDR_H | RW", "DMA_ADDR_H | UNKNOWN", "unknown access")

    def test_c_header_offset_drift_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for source in importer.C_FILES:
                dest = root / source
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(importer.C_ROOT / source, dest)
            path = root / "drivers/video/spacemit/dsi/drv/spacemit_dsi_hw.h"
            path.write_text(
                path.read_text().replace(
                    "#define DSI_CTRL_0 0x0", "#define DSI_CTRL_0 0x4"
                )
            )
            with self.assertRaisesRegex(ValueError, "DSI C offset mismatch"):
                importer.build(importer.DOC_ROOT, root)

    def test_balanced_c_arguments(self):
        calls = importer.call_arguments(
            "qspi_writel(q, GENMASK(3, 2), b + QSPI_MCR);", "qspi_writel"
        )
        self.assertEqual(calls, [["q", " GENMASK(3, 2)", " b + QSPI_MCR"]])

    def test_deterministic_check_does_not_write(self):
        subprocess.run(
            [sys.executable, str(Path(importer.__file__)), "--check"], check=True
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = [
                sys.executable,
                str(Path(importer.__file__)),
                "--repo-root",
                str(root),
            ]
            subprocess.run(args, check=True, capture_output=True)
            path = root / "data/registers/expanded_k3_memory.yaml"
            path.write_text(path.read_text() + " ")
            before = path.read_bytes()
            result = subprocess.run(args + ["--check"], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
