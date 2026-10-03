#!/usr/bin/env python3
"""Source-named tests for the K1 memory/IO seam; no board required."""
import copy
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
import k1_io as io

ROOT = Path(os.environ.get('SPACEMIT_DOCS_ROOT', str(io.DEFAULT_ROOT)))
LINUX = Path(os.environ.get('SPACEMIT_K1_LINUX_ROOT', '/tmp/k1-io-c'))
ESOS = Path(os.environ.get('SPACEMIT_ESOS_ROOT', str(io.DEFAULT_ESOS_ROOT)))


class K1IoSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir, cls.manifest = io.build(ROOT, ESOS, LINUX if (LINUX / io.EMAC_C).exists() else None)

    def register(self, block, name):
        return next(item for item in self.ir['block/k1_io::' + block]['items'] if item['name'] == name)

    def fields(self, block, name):
        item = self.register(block, name)
        return {f['name']: f for f in self.ir['fieldset/' + item['fieldset']]['fields']}

    def test_all_278_markdown_fact_tables_have_dispositions(self):
        tables = [t for f in io.CHAPTERS for t in io.mt.extract(ROOT / io.PREFIX / f, ROOT)]
        self.assertEqual(len(tables), 278)
        entries = {(t['source'], t['line']): t for t in self.manifest['tables']}
        for table in tables:
            entry = entries[(table['source'], table['line'])]
            if table['kind'] == 'fields' and 'Descriptor Details' not in ' '.join(table['heading']):
                self.assertEqual(entry['status'], 'generated')
                self.assertTrue(entry['registers'])
            else:
                self.assertEqual(entry['status'], 'not_mmio')
                self.assertTrue(entry['note'])

    def test_all_table_references_and_mask_directions_exist(self):
        for entry in self.manifest['tables']:
            for name in entry['registers']:
                block, register = name.rsplit('.', 1)
                self.assertTrue(any(item['name'] == register for item in self.ir['block/' + block]['items']), name)
        for name in self.manifest['write_mask_registers']:
            block, register = name.rsplit('.', 1)
            item = next(i for i in self.ir['block/' + block]['items'] if i['name'] == register)
            self.assertEqual(item['access'], 'Write', name)
        for block, groups in self.manifest['aliases'].items():
            for group in groups:
                items = [next(i for i in self.ir['block/' + block]['items'] if i['name'] == n) for n in group]
                self.assertEqual(len({(i['byte_offset'], i['bit_size']) for i in items}), 1)
                self.assertTrue(all('array' not in i for i in items))

    def test_native_mmio_words_are_not_small_field_widths(self):
        for key, block in self.ir.items():
            if key.startswith('block/'):
                for item in block['items']:
                    if 'bit_size' in item:
                        self.assertEqual(item['bit_size'], 32)
        self.assertEqual(self.fields('I2c', 'idbr')['data_buffer']['bit_size'], 8)
        self.assertEqual(self.fields('OneWire', 'transmit_receive_buffer')['data']['bit_size'], 8)

    def test_sdh_combined_interrupt_and_host_control_views(self):
        self.assertEqual(self.register('Sdh', 'sd_normal_int_status')['byte_offset'], 0x30)
        self.assertEqual(self.register('Sdh', 'sd_auto_cmd12_error_status_write')['access'], 'Write')
        fields = self.fields('Sdh', 'sd_auto_cmd12_error_status_write')
        self.assertEqual(fields['pre_val_en']['bit_offset'], 31)
        self.assertEqual(fields['auto_cmd12_not_exe']['bit_offset'], 0)
        self.assertNotIn('cmd_not_issued', fields)
        # Byte-width SDHCI concepts must not narrow this manual's combined word.
        self.assertEqual(self.register('Sdh', 'sd_block_size_cnt')['bit_size'], 32)

    def test_emac_c_offsets_correct_two_repeated_manual_pdf_typos(self):
        self.assertEqual(self.register('Emac', 'mac_transmit_statctr_data_low')['byte_offset'], 0x1bc)
        self.assertEqual(self.register('Emac', 'r_1588_interrupt_enable')['byte_offset'], 0x364)
        self.assertEqual(self.register('Emac', 'dma_status_and_irq_write')['access'], 'Write')
        self.assertEqual(self.fields('Emac', 'dma_status_and_irq_write')['transmit_transfer_done_irq']['bit_offset'], 0)
        self.assertNotIn('receive_dma_state', self.fields('Emac', 'dma_status_and_irq_write'))
        self.assertEqual(self.register('Emac', 'mac_multicast_hash_table_4')['access'], 'ReadWrite')
        self.assertNotIn('Documented reset', self.fields('Emac', 'mac_multicast_hash_table_4')['hash_table_63_to_48']['description'])

    def test_usb_duplicate_labels_and_documented_rw_debug_view(self):
        self.assertEqual(self.register('Usb3Glue', 'usb3_ctrl_clk_cfg_at_c')['byte_offset'], 12)
        self.assertEqual(self.register('Usb3Glue', 'usb3_ctrl_clk_cfg_at_20_write')['byte_offset'], 0x20)
        self.assertEqual(self.register('Usb3Glue', 'p_addr_ro2')['access'], 'ReadWrite')
        self.assertEqual(self.fields('Usb3Glue', 'usb3_ctrl_clk_cfg')['fladj_30mhz_reg']['description'], 'Access: RW. Documented reset: 0x20.')

    def test_i2c_wfifo_c_direction_and_interrupt_acknowledge(self):
        self.assertEqual(self.register('I2c', 'wfifo')['access'], 'Write')
        self.assertEqual(self.register('I2c', 'rfifo')['access'], 'Read')
        self.assertEqual(self.register('I2c', 'isr_write')['byte_offset'], 4)
        fields = self.fields('I2c', 'isr_write')
        self.assertNotIn('ibb', fields)
        self.assertEqual(fields['rxov']['bit_offset'], 31)
        self.assertEqual(self.fields('I2c', 'wfifo')['control']['bit_offset'], 8)
        self.assertEqual(self.fields('I2c', 'wfifo')['control']['bit_size'], 4)

    def test_ssp_fifo_alias_and_w1c_fields(self):
        self.assertEqual(self.register('Ssp', 'ssdatr')['byte_offset'], 0x10)
        self.assertEqual(self.register('Ssp', 'sssr_write')['access'], 'Write')
        self.assertEqual(self.fields('Ssp', 'sssr_write')['ror']['bit_offset'], 20)
        self.assertNotIn('rfl', self.fields('Ssp', 'sssr_write'))

    def test_gpio_interleaved_bank_arrays_endpoints_and_exact_masks(self):
        self.assertEqual(self.register('Gpio', 'gpio_plr')['array'], {'offsets': [0, 4, 8, 0x100]})
        self.assertEqual(self.register('Gpio', 'gpio_edr3')['byte_offset'], 0x148)
        self.assertEqual(self.register('Gpio', 'gpio_edr_write3')['access'], 'Write')
        self.assertEqual(self.register('Gpio', 'gapmask')['byte_offset'], 0x9c)
        self.assertEqual(self.register('Gpio', 'gcpmask')['array']['offsets'][-1], 0x100)

    def test_gpio_psn_full_mask_overrides_single_bits31_manual_pdf_typo(self):
        item = self.register('Gpio', 'gpio_psr')
        self.assertEqual((item['bit_size'], item['access']), (32, 'Write'))
        self.assertEqual(item['array']['offsets'], [0, 4, 8, 0x100])
        for block in ['Gpio', 'GpioBank']:
            field = self.fields(block, 'gpio_psr')['psn']
            self.assertEqual((field['bit_offset'], field['bit_size']), (0, 32))
            for bit in [0, 15, 31]:
                self.assertEqual(((1 << bit) & ((1 << field['bit_size']) - 1)) << field['bit_offset'], 1 << bit)
        source = (ESOS / 'bsp/spacemit/drivers/gpio/spacemit-gpio.c').read_text()
        self.assertIn('((gpio) & BANK_GPIO_MASK)', source)
        self.assertIn('bit = (1 << gpio_to_bank_offset(offset))', source)
        self.assertIn('writel(bit, bank->reg_bank + regs->gpsr)', source)
        decision = next(c for c in self.manifest['corrections'] if 'GPIO_PSR PSn' in c['decision'])
        self.assertIn('PDF', decision['evidence'])
        self.assertIn('spacemit-gpio.c', decision['evidence'])

    def test_pwm_split_ap_windows_and_rcpu_variant(self):
        offsets = self.ir['block/k1_io::PwmArray']['items'][0]['array']['offsets']
        self.assertEqual(len(offsets), 20)
        self.assertEqual(offsets[7:9], [0x1c00, 0x6000])
        self.assertEqual(offsets[-1], 0x8c00)
        self.assertEqual(self.ir['block/k1_io::RcpuPwmArray']['items'][0]['array'], {'len': 10, 'stride': 0x100})
        self.assertEqual(len(self.ir['block/k1_io::RcpuPwm']['items']), 3)

    def test_qspi_real_buffer_and_used_lut_array_bounds(self):
        self.assertEqual(self.register('Qspi', 'rbdr')['array'], {'len': 32, 'stride': 4})
        lut = self.register('Qspi', 'lut')
        self.assertEqual(lut['byte_offset'] + (lut['array']['len'] - 1) * 4, 0x32c)
        self.assertEqual(self.fields('Qspi', 'lut')['instruction1']['bit_offset'], 26)
        self.assertEqual(self.register('Qspi', 'fr_write')['access'], 'Write')

    def test_usb2_dynamic_base_and_indexed_command_views(self):
        instances = {i['block'] for i in self.manifest['instances']}
        self.assertNotIn('k1_io::Usb2Operational', instances)
        self.assertEqual(self.register('Usb2Operational', 'portsc7')['byte_offset'], 0x60)
        self.assertEqual(self.register('Usb2Operational', 'epctrl15_write')['byte_offset'], 0xbc)
        self.assertEqual(self.register('Usb2Operational', 'epctrl15_write')['access'], 'Write')
        self.assertNotIn('speed', self.fields('Usb2Operational', 'portsc7_write'))
        self.assertNotIn('hc_halted', self.fields('Usb2Operational', 'usbsts_write'))
        self.assertNotIn('fieldset', self.register('Usb2Phy', 'reg01_write'))

    def test_ddr_declared_counters_and_sparse_id_selection(self):
        self.assertEqual(self.register('DdrControl', 'perf_counter')['array']['len'], 8)
        item = self.register('DdrAxiMonitor', 'id_select')
        self.assertEqual(item['byte_offset'], 0x44)
        self.assertEqual(len(item['array']['offsets']), 12)
        self.assertEqual(item['byte_offset'] + item['array']['offsets'][-1], 0x7c)
        self.assertFalse(any(i['byte_offset'] == 0x15c for i in self.ir['block/k1_io::DdrControl']['items']))

    def test_instance_addresses_have_exact_source_locations(self):
        instances = {i['name']: i for i in self.manifest['instances']}
        self.assertEqual(instances['SECURE_K1_IO_UART1']['address'], 0xf0612000)
        self.assertEqual(instances['AP_K1_IO_UART0']['address'], 0xd4017000)
        self.assertEqual(instances['RCPU_K1_IO_SHUB_UART0']['address'], 0xc0881000)
        for item in instances.values():
            source = item['source']
            paths = [ROOT / source['path'], ESOS / source['path'], LINUX / source['path']]
            path = next((p for p in paths if p.exists()), None)
            if path is None:
                continue
            line = path.read_text().splitlines()[source['line'] - 1].lower().replace('_', '')
            self.assertIn(f"{item['address']:x}", line, item['name'])

    def test_pinned_c_fingerprints_and_independent_macros(self):
        if not (LINUX / io.EMAC_C).exists():
            self.skipTest('Supply SPACEMIT_K1_LINUX_ROOT for pinned C comparisons')
        source = (LINUX / io.EMAC_C).read_text()
        for macro, expected in [('MAC_TX_STATCTR_DATA_LOW', 0x1bc), ('PTP_1588_IRQ_EN', 0x364)]:
            match = re.search(r'#define\s+' + macro + r'\s+\(?([0-9a-fx]+)', source, re.I)
            self.assertIsNotNone(match)
            assert match is not None
            self.assertEqual(int(match[1], 0), expected)
        self.assertIn('writel', (ESOS / 'bsp/spacemit/drivers/i2c/i2c-k1.c').read_text())

    def test_malformed_ranges_overlap_access_and_integer_expressions(self):
        path = ROOT / io.PREFIX / io.CHAPTERS[0]
        table = io.mt.extract(path, ROOT)[1]
        malformed = copy.deepcopy(table)
        malformed['rows'][0]['values'][0] = '16:31'
        with self.assertRaises(ValueError):
            io.field_facts(malformed, [])
        overlap = copy.deepcopy(table)
        overlap['rows'][1]['values'][0] = '31:16'
        with self.assertRaisesRegex(ValueError, 'overlapping named fields'):
            io.field_facts(overlap, [])
        with self.assertRaises(ValueError):
            io.mt.integer('__import__("os")')
        with self.assertRaises(ValueError):
            io.mt.bit_range('64:0')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in io.CHAPTERS + ['6.Address_Mapping.md', '7.Interrupt_Assignments.md']:
                dst = root / io.PREFIX / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes((ROOT / io.PREFIX / name).read_bytes())
            chapter = root / io.PREFIX / io.CHAPTERS[0]
            chapter.write_text(chapter.read_text().replace('| R/W |', '| INVALID |', 1))
            with self.assertRaisesRegex(ValueError, 'unsupported access'):
                io.Builder(root).import_docs()

    def test_deterministic_committed_json_yaml_and_no_c_inventions(self):
        root = Path(__file__).resolve().parents[2]
        for name, data in [('data/registers/expanded_k1_io.yaml', self.ir), ('provenance/expanded/k1_io.json', self.manifest)]:
            self.assertEqual((root / name).read_text(), json.dumps(data, indent=2) + '\n')
        gaps = [t for t in self.manifest['tables'] if t['status'] == 'source_gap']
        self.assertTrue(any('DDRC_ERR_ID' in t['note'] for t in gaps))
        self.assertEqual(len(self.manifest['tables']), 293)
        self.assertEqual(len(gaps), 2)  # Actual C definition gaps remain tableless supplements.
        self.assertTrue(any('DesignWare' in c['decision'] for c in self.manifest['corrections']))
        self.assertTrue(any('GPIO Edge' in c['decision'] for c in self.manifest['corrections']))


if __name__ == '__main__':
    unittest.main()
