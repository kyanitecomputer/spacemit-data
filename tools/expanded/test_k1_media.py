"""Source-grounded coverage and C layout oracle for the K1 media importer."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import k1_media as media

DOCS = Path(
    os.environ.get("K1_MEDIA_SOURCE_ROOT", "/tmp/spacemit-research/docs/en/key_stone")
)
LINUX = Path(os.environ.get("K1_MEDIA_LINUX_ROOT", "/tmp/k1-media-c/linux"))
REPO = Path(__file__).resolve().parents[2]


class NumericParserTests(unittest.TestCase):
    def test_expressions_fail_closed(self):
        macros = media.defines("#define BASE (0x100U)\n#define OFFSET (BASE + 4 * i)\n")
        self.assertEqual(media.evaluate(macros["OFFSET"][1], macros, {"i": 23}), 0x15C)
        for bad in ["__import__('os')", "BASE.attr", "i[0]", "1 << 128"]:
            with self.assertRaises((ValueError, SyntaxError)):
                media.evaluate(bad, macros, {"i": 0})

    def test_gpu_access_evidence_is_not_mask_type_or_dynamic_offset_inference(self):
        code = ('// RGXReadReg64(h, RGX_CR_FALSE);\n'
                'RGXReadReg32(h, RGX_CR_WORD);\n'
                'RGXWriteReg64(h, RGX_CR_WORD, 0);\n'
                'RGXReadReg64(h, RGX_CR_DYNAMIC + index);\n'
                '/* RGXWriteReg64(h, RGX_CR_FALSE, 0); */\n')
        self.assertEqual(media.gpu_access_evidence({'oracle.c': code}),
                         {'RGX_CR_WORD': {32: [('oracle.c', 2)], 64: [('oracle.c', 3)]}})

    def test_malformed_native_words_rejected(self):
        for bad in [
            "unsigned int field : 33;",
            "unsigned int a:20; unsigned int b:20;",
            "unsigned int field : 31;",
        ]:
            with self.assertRaises(ValueError):
                media.bitfields(bad)
        fs = media.bitfields("unsigned int high : 2; unsigned int reserved : 30;")
        self.assertEqual(fs, [{"name": "high", "bit_offset": 0, "bit_size": 2}])


@unittest.skipUnless(
    DOCS.exists() and LINUX.exists(), "pinned source caches not present"
)
class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.importer = media.Importer(DOCS, LINUX)
        cls.ir, cls.manifest = cls.importer.run()

    def items(self, block):
        return {r["name"]: r for r in self.ir["block/k1_media::" + block]["items"]}

    def test_all_markdown_tables_accounted(self):
        recorded = {(t["source"], t["line"]): t for t in self.manifest["tables"]}
        docpath = media.DOC_DIR + media.CHAPTERS[1]
        tables = media.mt.extract(DOCS / docpath, DOCS)
        self.assertEqual(len(tables), 149)
        for t in tables:
            self.assertEqual(recorded[(docpath, t["line"])]["status"], "generated")
        for ch in media.CHAPTERS:
            path = media.DOC_DIR + ch
            for t in media.mt.extract(DOCS / path, DOCS):
                self.assertIn((path, t["line"]), recorded)
        self.assertEqual(len(recorded), len(self.manifest["tables"]))
        chapter = media.DOC_DIR + media.CHAPTERS[0]
        lines = (DOCS / chapter).read_text().splitlines()
        for line in (585, 628):
            self.assertTrue(lines[line - 1].startswith("| Register"))
            self.assertEqual(recorded[(chapter, line)]["status"], "source_gap")
        self.assertEqual(
            sum(t["source"] == docpath for t in self.manifest["tables"]), 149
        )

    def test_manifest_references_and_native_spans(self):
        qualified = {}
        for key, block in self.ir.items():
            if not key.startswith("block/"):
                continue
            names = [r["name"] for r in block["items"]]
            self.assertEqual(len(names), len(set(names)))
            for r in block["items"]:
                qualified[key[6:] + "." + r["name"]] = r
                self.assertEqual(r["byte_offset"] % (r["bit_size"] // 8), 0)
        for table in self.manifest["tables"]:
            for r in table["registers"]:
                self.assertIn(r, qualified)
        for block, groups in self.manifest["aliases"].items():
            for group in groups:
                entries = [qualified[block + "." + name] for name in group]
                self.assertEqual({r["access"] for r in entries}, {"Read", "Write"})
                self.assertEqual(
                    len(
                        {
                            (r["byte_offset"], r["bit_size"], str(r.get("array")))
                            for r in entries
                        }
                    ),
                    1,
                )
        for r in self.manifest["write_mask_registers"]:
            self.assertEqual(qualified[r]["access"], "Write")
        for instance in self.manifest["instances"]:
            self.assertIn("block/" + instance["block"], self.ir)
        for key, fs in self.ir.items():
            if not key.startswith("fieldset/"):
                continue
            used = 0
            names = [f["name"] for f in fs["fields"]]
            self.assertEqual(len(names), len(set(names)))
            for f in fs["fields"]:
                self.assertLessEqual(f["bit_offset"] + f["bit_size"], fs["bit_size"])
                mask = ((1 << f["bit_size"]) - 1) << f["bit_offset"]
                self.assertEqual(used & mask, 0)
                used |= mask

    def test_dsi_resets_and_clear_aliases(self):
        dsi = self.items("Dsi")
        self.assertEqual(dsi["dsi_ctrl_0"]["byte_offset"], 0)
        self.assertIn("reset 0x110", dsi["dsi_ctrl_0"]["description"])
        self.assertEqual(dsi["dsi_irq_st"]["access"], "Read")
        self.assertEqual(dsi["dsi_irq_st_write_view"]["access"], "Write")
        self.assertEqual(dsi["dsi_rx_pkt_st_2"]["byte_offset"], 0x78)
        self.assertEqual(dsi["dsi_phy_debug"]["byte_offset"], 0x1EC)
        self.assertEqual(dsi["dsi_tx_timer"]["bit_size"], 32)
        self.assertIn("reset 0xffffffff", dsi["dsi_tx_timer"]["description"])

    def test_spi_lcd_base_conflict_and_mixed_w0c(self):
        spi = self.items("SpiLcd")
        instance = next(
            i for i in self.manifest["instances"] if i["name"] == "AP_K1_MEDIA_SPI_LCD"
        )
        self.assertEqual(instance["address"], 0xD4209000)
        self.assertEqual(spi["spu_irq_isr_raw_reg"]["access"], "Read")
        self.assertEqual(spi["spu_irq_isr_raw_reg_write_view"]["access"], "Write")
        self.assertIn("W0C", spi["spu_irq_isr_raw_reg"]["description"])
        self.assertEqual(spi["lcd_wdma_ctrl4_reg"]["byte_offset"], 0x20C)
        alpha = self.ir["fieldset/" + spi["lcd_afa_all2one_reg"]["fieldset"]]["fields"]
        selections = [
            f
            for f in alpha
            if f["name"].startswith(
                "select_alpha_when_panel_and_tv_path_graphics_overlaid"
            )
        ]
        self.assertEqual(
            {f["name"] for f in selections},
            {
                "select_alpha_when_panel_and_tv_path_graphics_overlaid_bits_10_11",
                "select_alpha_when_panel_and_tv_path_graphics_overlaid_bits_14_15",
            },
        )

    def test_v2d_index_and_packed_address_words(self):
        v2d = self.items("V2d")
        coef = v2d["v2d_scaler_coef_reg"]
        self.assertEqual(coef["array"], {"len": 24, "stride": 4})
        self.assertEqual(coef["byte_offset"] + 23 * 4, 0x160)
        word = v2d["v2d_layer1_uv_addr_h"]
        self.assertEqual(word["byte_offset"], 0x20C)
        fs = self.ir["fieldset/" + word["fieldset"]]["fields"]
        self.assertIn({"name": "in_addr_uv_33_32", "bit_offset": 0, "bit_size": 2}, fs)
        self.assertNotIn("v2d_layer1_bld_factor", v2d)
        for name, start, count in [
            ("V2dMmu", 0x40, 32),
            ("IspMmu", 0x12240, 24),
            ("CppMmu", 0x1040, 32),
        ]:
            array = next(
                r for r in self.items(name).values() if r["byte_offset"] == start
            )
            self.assertEqual(array["array"], {"len": count, "stride": 0x20})
            self.assertEqual(array["bit_size"], 32)

    def test_dpu_repetitions_and_native_split_address(self):
        instances = self.manifest["instances"]
        for prefix, count, stride, base in [
            ("LCD_DPURDMA", 12, 0x100, 0xC0340A80),
            ("HDMI_DPULAYER", 12, 0x300, 0xC0442000),
        ]:
            bank = {i["name"]: i for i in instances if prefix in i["name"]}
            self.assertEqual(len(bank), count)
            self.assertEqual(
                bank["AP_K1_MEDIA_" + prefix + str(count - 1)]["address"],
                base + stride * (count - 1),
            )
        rdma = self.items("DpuRdma")
        self.assertEqual(rdma["left_base_addr0_low"]["byte_offset"], 0x20)
        self.assertEqual(rdma["left_base_addr0_high"]["byte_offset"], 0x24)
        self.assertEqual(rdma["left_base_addr0_low"]["bit_size"], 32)
        fields = self.ir["fieldset/" + rdma["left_base_addr0_high"]["fieldset"]][
            "fields"
        ]
        self.assertEqual(fields[0]["bit_size"], 2)
        command = self.items("DpuCommandList")
        self.assertEqual(command["cmdlist_reg_0_13"]["byte_offset"], 13 * 4)
        self.assertEqual(command["cmdlist_reg_14_13"]["byte_offset"], 27 * 4)

    def test_isp_statistics_and_named_host_irq_bits(self):
        words = self.items("IspStatistics")
        self.assertEqual(words["reg_stat_aem_result_mem_entry0"]["byte_offset"], 0x4050)
        self.assertEqual(words["aem_result_first_read"]["byte_offset"], 0x4850)
        self.assertEqual(words["pdc_channel_count"]["array"], {"len": 4, "stride": 4})
        self.assertEqual(words["pdc_channel_width"]["byte_offset"], 0x408)
        instances = {i["name"]: i for i in self.manifest["instances"]}
        self.assertEqual(
            instances["AP_K1_MEDIA_ISP_STATISTICS1"]["address"], 0xC0238000
        )
        bits = {
            f["name"]: f["bit_offset"]
            for f in self.ir["fieldset/k1_media::IspHostIrqBits"]["fields"]
        }
        self.assertEqual(
            (bits["reset_done"], bits["aem_eof"], bits["isp_err"]), (14, 26, 31)
        )
        self.assertIn("ispdma_word_114", self.items("IspDma"))

    def test_gpu_widths_hdmi_native_words_and_vpu_sessions(self):
        gpu = self.items("GpuRgx")
        self.assertEqual(self.items('GpuRgxPortable32')["rgx_cr_core_id"]["bit_size"], 32)
        self.assertEqual(gpu["rgx_cr_soft_reset"]["bit_size"], 64)
        self.assertEqual(gpu["rgx_cr_soft_reset"]["access"], "Read")
        hdmi = self.items("Hdmi")
        self.assertTrue(all(r["bit_size"] == 32 for r in hdmi.values()))
        self.assertEqual(hdmi["word_00c"]["access"], "Read")
        sessions = [i for i in self.manifest["instances"] if "VPU_LSID" in i["name"]]
        self.assertEqual(
            [i["address"] for i in sessions], [0xC0500200 + i * 0x40 for i in range(4)]
        )

    def test_gpu_all_23_native_views_follow_pinned_accessors_not_header_typedefs(self):
        expected32 = {
            'event_status', 'event_clear', 'mips_wrapper_config', 'mips_debug_config',
            'meta_sp_mslvirqstatus', 'mts_bgctx_thread0_dm_assoc', 'mts_bgctx_thread1_dm_assoc',
            'mts_intctx_thread0_dm_assoc', 'mts_intctx_thread1_dm_assoc', 'meta_boot',
            'scratch14', 'fwcore_boot', 'fwcore_dmi_dmcontrol', 'slc_ctrl_bypass',
            'bif_trust', 'sys_bus_secure', 'safety_event_enable_roguexe',
            'safety_event_status_roguexe', 'safety_event_clear_roguexe',
            'fault_fw_status', 'fault_fw_clear',
        }
        expected64 = {'soft_reset', 'mts_garten_wrapper_config'}
        gpu = self.items('GpuRgx')
        self.assertEqual({(r['name'], r['bit_size']) for r in gpu.values() if r['access'] == 'Read'},
                         {('rgx_cr_' + n, 32) for n in expected32} | {('rgx_cr_' + n, 64) for n in expected64})
        paths = ['drivers/gpu/drm/img-rogue/' + n for n in
                 ['rgxlayer_impl.c', 'rgxlayer_impl_common.c', 'rgxinit.c', 'rgxpower.c', 'rgxstartstop.c']]
        contents = {p: (LINUX / p).read_text() for p in paths}
        evidence = media.gpu_access_evidence(contents)
        native_names = {media.mt.identifier(n): widths for n, widths in evidence.items()}
        for name, item in gpu.items():
            if item['access'] != 'Read':
                continue
            self.assertEqual(set(native_names[name]), {item['bit_size']})
            write = gpu[name + '_write_view']
            self.assertEqual((write['access'], write['bit_size'], write['byte_offset']), ('Write', item['bit_size'], item['byte_offset']))
            self.assertIn('k1_media::GpuRgx.' + write['name'], self.manifest['write_mask_registers'])
        source_paths = {s['path'] for s in self.manifest['sources'] if s['kind'] == 'linux'}
        self.assertTrue(set(paths) <= source_paths)
        bound = {i['block'] for i in self.manifest['instances']}
        self.assertIn('k1_media::GpuRgx', bound)
        for block in ['GpuRgxPortable32', 'GpuRgxPortable64', 'GpuRgxWideVariant64']:
            self.assertNotIn('k1_media::' + block, bound)

    def test_gpu_slc_native32_and_all_header_xe_mem_high_fields_survive_separately(self):
        item = self.items('GpuRgx')['rgx_cr_slc_ctrl_bypass']
        self.assertEqual((item['byte_offset'], item['bit_size']), (0x3828, 32))
        fs = self.ir['fieldset/' + item['fieldset']]['fields']
        self.assertTrue(all(f['bit_offset'] + f['bit_size'] <= 32 for f in fs))
        wide = self.items('GpuRgxWideVariant64')['rgx_cr_slc_ctrl_bypass_xe_mem']
        self.assertEqual((wide['byte_offset'], wide['bit_size']), (0x3828, 64))
        fields = {f['name']: (f['bit_offset'], f['bit_size']) for f in self.ir['fieldset/' + wide['fieldset']]['fields']}
        header = media.defines((LINUX / 'drivers/gpu/drm/img-rogue/km/rgx_cr_defs_km.h').read_text())
        prefix = 'RGX_CR_SLC_CTRL_BYPASS__XE_MEM__'
        for name, (_, expression, _) in header.items():
            if name.startswith(prefix) and name.endswith('_CLRMSK'):
                mask = ((1 << 64) - 1) ^ media.evaluate(expression, header)
                self.assertEqual(fields[media.mt.identifier(name[len(prefix):-len('_CLRMSK')])], ((mask & -mask).bit_length() - 1, mask.bit_count()))
        self.assertEqual(fields['req_tfbc_comp_zls'], (59, 1))
        self.assertIn('NOT verified native transactions', wide['description'])

    def test_all_dpu_bitfields_against_native_c_compiler(self):
        compiler = shutil.which("clang") or shutil.which("cc")
        if compiler is None:
            self.skipTest("C compiler unavailable")
        # Write one field at a time in the original C union, then inspect raw words.
        # This verifies every named field, nested structure, reserved gap and array.
        c = [
            "#include <stdint.h>",
            "#include <stdio.h>",
            "#include <string.h>",
            '#include "reg_map.h"',
            "int main(void) {",
        ]
        expected = []
        for path in sorted((LINUX / media.DPU_DIR).glob("*.h")):
            if path.name == "reg_map.h":
                continue
            source = path.read_text()
            import re

            types = re.findall(r"}\s*(\w+)\s*;", source)
            self.assertTrue(types, path.name)
            typ = types[-1]
            words = self.importer.c_word_layout(media.DPU_DIR + path.name)
            c.append("{ " + typ + " x;")
            for word in words:
                for f, member in zip(word["fields"], word["paths"], strict=True):
                    c.append(
                        "memset(&x,0,sizeof x); x."
                        + member
                        + " = "
                        + str((1 << f["bit_size"]) - 1)
                        + "U;"
                    )
                    c.append(
                        'for(unsigned j=0;j<sizeof(x)/4;j++) { uint32_t v; memcpy(&v,(char*)&x+4*j,4); if(v) printf("%u %u\\n",j,v); }'
                    )
                    expected.append(
                        (
                            word["offset"] // 4,
                            ((1 << f["bit_size"]) - 1) << f["bit_offset"],
                        )
                    )
            c.append("}")
        c.append("return 0; }")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "oracle.c").write_text("\n".join(c))
            subprocess.run(
                [
                    compiler,
                    "-std=gnu11",
                    "-w",
                    "-I",
                    str(LINUX / media.DPU_DIR),
                    str(tmp / "oracle.c"),
                    "-o",
                    str(tmp / "oracle"),
                ],
                check=True,
                capture_output=True,
            )
            output = subprocess.check_output([str(tmp / "oracle")], text=True)
        actual = [tuple(map(int, s.split())) for s in output.splitlines()]
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 1985)

    def test_committed_outputs_are_deterministic(self):
        result = subprocess.run(
            [
                sys.executable,
                str(REPO / "tools/expanded/k1_media.py"),
                "--check",
                "--source-root",
                str(DOCS),
                "--linux-root",
                str(LINUX),
            ],
            check=True,
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertIn("checked:", result.stdout)


if __name__ == "__main__":
    unittest.main()
