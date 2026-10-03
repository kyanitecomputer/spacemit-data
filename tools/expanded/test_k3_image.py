#!/usr/bin/env python3
"""Source-grounded tests for the complete K3 V2D bank and importer rejection."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import unittest
from collections import Counter
from pathlib import Path

import k3_image as image


class K3ImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(os.environ.get('PAC_SOURCE_ROOT', image.DEFAULT_SOURCE_ROOT))
        cls.tables = image.source_tables(cls.root / image.CHAPTER, cls.root)
        cls.ir, cls.manifest = image.build(cls.root)

    def item(self, block, name):
        return next(i for i in self.ir[f'block/{block}']['items'] if i['name'] == name)

    def fields(self, block, name):
        return self.ir['fieldset/' + self.item(block, name)['fieldset']]['fields']

    def field(self, block, name, field):
        return next(f for f in self.fields(block, name) if f['name'] == field)

    def test_all_source_tables_accounted(self):
        self.assertEqual(len(self.tables), 396)
        self.assertEqual({t['line'] for t in self.tables}, {t['line'] for t in self.manifest['tables'] if t['source'] == image.CHAPTER})
        self.assertEqual(Counter(t['status'] for t in self.manifest['tables']), {'generated': 386, 'duplicate': 5, 'not_mmio': 7})
        for table in self.manifest['tables']:
            for register in table['registers']:
                block, name = register.rsplit('.', 1)
                self.assertIsNotNone(self.item(block, name))
        self.assertEqual(len(self.manifest['instances']), 1)
        self.assertEqual(self.manifest['instances'][0]['address'], 0xc0100000)
        self.assertEqual(self.manifest['instances'][0]['view'], 'ap')

    def test_checked_in_outputs_and_determinism(self):
        for path, value in [('data/registers/expanded_k3_image.yaml', self.ir), ('provenance/expanded/k3_image.json', self.manifest)]:
            self.assertEqual((image.DEFAULT_REPO_ROOT / path).read_text(), image.render(value))
            self.assertEqual(json.loads(image.render(value)), value)
        self.assertEqual(image.build(self.root), (self.ir, self.manifest))

    def test_all_physical_words_fields_and_resets_match_source(self):
        # Flatten actual emitted nested arrays, independently of importer folding.
        physical = {}
        def visit(block, base):
            for item in self.ir['block/' + block]['items']:
                array = item.get('array', {'len': 1, 'stride': 0})
                for index in range(array['len']):
                    address = base + item['byte_offset'] + index * array['stride']
                    if 'block' in item:
                        visit(item['block'], address)
                    else:
                        fields = copy.deepcopy(self.ir['fieldset/' + item['fieldset']]['fields'])
                        for field in fields:
                            if item['name'] == 'scaler_coef':
                                field['name'] = field['name'].replace('_even', str(2 * index)).replace('_odd', str(2 * index + 1))
                            elif item['name'] == 'palette_table':
                                field['name'] += str(index)
                            elif block == image.LAYER:
                                layer = (base - 0xac) // 0x54
                                field['name'] = field['name'].replace('r_l_', f'r_l{layer}_')
                        layout = [(f['name'], f['bit_offset'], f['bit_size'], f['description']) for f in fields]
                        if address in physical:
                            self.assertEqual(physical[address], layout)  # Exact aliases only.
                        else:
                            physical[address] = layout
        visit(image.BLOCK, 0)
        self.assertEqual(sorted(physical), list(range(0, 0x608, 4)))
        total = 0
        for table in self.tables:
            if not table['heading'][-1].startswith('V2D_V2_REG_'):
                continue
            number = int(table['heading'][-1].split('_')[-1])
            source = []
            for row in table['rows']:
                bits, name, access, reset = row['values']
                if name == 'RSVD':
                    continue
                lo, size = image.bit_range(bits)
                source.append((image.identifier(name), lo, size, f'Source {access}; reset {image.integer(reset)}.'))
            self.assertEqual(physical[4 * number], source, f'source word {number}')
            total += len(source)
        self.assertEqual(total, 558)

    def test_arrays_have_exact_bounds(self):
        scaler = self.item(image.BLOCK, 'scaler_coef')
        self.assertEqual(scaler['array'], {'len': 24, 'stride': 4})
        self.assertEqual(scaler['byte_offset'] + 23 * 4, 0x60)
        layers = self.item(image.BLOCK, 'layer')
        self.assertEqual(layers['array'], {'len': 4, 'stride': 0x54})
        self.assertEqual(layers['byte_offset'] + 3 * 0x54 + self.item(image.LAYER, 'reg_20')['byte_offset'], 0x1f8)
        palette = self.item(image.BLOCK, 'palette_table')
        self.assertEqual(palette['array'], {'len': 256, 'stride': 4})
        self.assertEqual(palette['byte_offset'] + 255 * 4, 0x604)
        self.assertEqual(len(self.ir['block/' + image.LAYER]['items']), 21)

    def test_split_34_bit_addresses_and_packed_fields(self):
        self.assertEqual(self.field(image.BLOCK, 'v2_reg_30', 'r_out_addr_y_31_0')['bit_size'], 32)
        self.assertEqual(self.field(image.BLOCK, 'v2_reg_31', 'r_out_addr_y_33_32')['bit_size'], 2)
        self.assertEqual(self.field(image.BLOCK, 'v2_reg_33', 'r_out_ori_width')['bit_offset'], 8)
        self.assertEqual(self.field(image.LAYER, 'reg_3', 'r_l_in_addr_uv_33_32')['bit_size'], 2)
        self.assertEqual(self.field(image.LAYER, 'reg_3', 'r_l_bld_alpha_rop2_code')['bit_offset'], 20)
        self.assertEqual(self.field(image.BLOCK, 'v2_reg_35', 'r_out_format')['bit_size'], 4)
        self.assertEqual(self.field(image.BLOCK, 'v2_reg_0_write', 'r_rdma_burst_len')['bit_offset'], 16)
        self.assertIn('Reset word 0x00101000', self.item(image.BLOCK, 'v2_reg_0_write')['description'])
        self.assertIn('Reset word 0x00FFFF00', self.item(image.BLOCK, 'v2_reg_127')['description'])
        self.assertIn('Reset word 0x01FFE002', self.item(image.BLOCK, 'v2_reg_129_write')['description'])

    def test_command_words_have_exact_read_write_aliases(self):
        for number in [0, 129]:
            read = self.item(image.BLOCK, f'v2_reg_{number}_read')
            write = self.item(image.BLOCK, f'v2_reg_{number}_write')
            self.assertEqual((read['byte_offset'], read['bit_size'], read['fieldset']), (write['byte_offset'], write['bit_size'], write['fieldset']))
            self.assertEqual((read['access'], write['access']), ('Read', 'Write'))
            self.assertIn(f'{image.BLOCK}.{write["name"]}', self.manifest['write_mask_registers'])
            self.assertIn([read['name'], write['name']], self.manifest['aliases'][image.BLOCK])
        self.assertEqual(self.field(image.BLOCK, 'v2_reg_129_write', 'r_damc_wr_int_clr')['bit_offset'], 31)

    def test_malformed_source_rejected(self):
        def changed(number):
            tables = copy.deepcopy(self.tables)
            table = next(t for t in tables if t['heading'][-1] == f'V2D_V2_REG_{number}')
            return tables, table
        tables, table = changed(385)
        table['offset_labels'][-1]['value'] = 'Offset:0x608'
        with self.assertRaisesRegex(ValueError, 'inconsistent offset'):
            image.import_bank(tables)
        tables, table = changed(0)
        table['rows'][1]['values'][3] = '256'
        with self.assertRaisesRegex(ValueError, 'reset outside field'):
            image.import_bank(tables)
        tables, table = changed(0)
        table['rows'][1]['values'][0] = '31:16'
        with self.assertRaisesRegex(ValueError, 'overlapping field'):
            image.import_bank(tables)
        tables, table = changed(126)
        table['rows'][1]['values'][1] = 'R_L3_UNKNOWN'
        with self.assertRaisesRegex(ValueError, 'inconsistent layer layout'):
            image.import_bank(tables)
        tables, table = changed(385)
        tables.remove(table)
        with self.assertRaisesRegex(ValueError, 'complete V2D'):
            image.import_bank(tables)
        tables, table = changed(385)
        table['rows'][0]['values'][1] = 'R_PALETTE_TABLE256'
        with self.assertRaisesRegex(ValueError, 'unexpected palette'):
            image.import_bank(tables)

    @unittest.skipUnless(os.environ.get('K3_IMAGE_C_ROOT'), 'optional pinned K1 C comparison cache not supplied')
    def test_pinned_related_k1_c_is_not_a_k3_address_or_field_oracle(self):
        root = Path(os.environ['K3_IMAGE_C_ROOT'])
        for name, fingerprint in {
            'v2d_reg.h': '5f84f67c2b2ae69f37f7eb81f8a87d2b04b754417f3360c8e63e39e93dc7532d',
            'v2d_hw.c': '47e741c28ee92a15efa1f6ae8f5780f8eff8048403e0609ef54199ca34471b3c',
        }.items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), fingerprint, 'C input must match pinned Linux revision')
        registers = (root / 'v2d_reg.h').read_text()
        hardware = (root / 'v2d_hw.c').read_text()
        self.assertRegex(registers, r'#define V2D_CORE_BASE\s+\(0x100\)')
        self.assertRegex(registers, r'#define SCALER_COEF_REG_NUM\s+\(24\)')
        self.assertRegex(registers, r'#define V2D_PALETTE_TABLE\(i\)\s+\(0x208 \+ i \* 0x4 \+ V2D_CORE_BASE\)')
        self.assertRegex(registers, r'unsigned int rdma_burst_len\s+: 3;')
        self.assertEqual(self.field(image.BLOCK, 'v2_reg_0_write', 'r_rdma_burst_len')['bit_size'], 8)
        self.assertIn('ctrl.field.trigger        = 1;', hardware)
        self.assertIn('v2d_write(V2D_CTRL_REG, ctrl.overlay);', hardware)
        self.assertIn('writel(val, v2dInfo->v2dreg_iomem_base+reg);', hardware)
        # The related K1 DMA bitfield matches all K3 documented DMA positions.
        match = re.search(r'typedef union \{\s*struct \{([^}]+)\} field;[^}]+\} v2d_dma_ctrl_reg_t;', registers)
        assert match is not None, 'missing C DMA control bitfield'
        union = match.group(1)
        position = 0
        for name, width in re.findall(r'unsigned int (\w+)\s*:\s*(\d+);', union):
            size = int(width)
            if name != 'reserved':
                field = self.field(image.BLOCK, 'v2_reg_129_write', 'r_' + name)
                self.assertEqual((field['bit_offset'], field['bit_size']), (position, size))
            position += size
        self.assertEqual(position, 32)


if __name__ == '__main__':
    unittest.main()
