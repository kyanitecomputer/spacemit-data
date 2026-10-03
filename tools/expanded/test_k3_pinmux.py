"""K3 numeric import, sparse bounds and source conflicts."""
import hashlib
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import k3_pinmux as pinmux


class K3PinmuxTests(unittest.TestCase):
    def test_all_words_and_native_width(self):
        ir, manifest = pinmux.build()
        items = ir['block/' + pinmux.BLOCK]['items']
        self.assertEqual({i['byte_offset'] for i in items},
                         {n * 4 for n in range(155) if n not in (131, 132)})
        self.assertTrue(all(i['bit_size'] == 32 and i['access'] == 'ReadWrite' for i in items))
        self.assertEqual(manifest['instances'][0]['address'], 0xd401e000)
        self.assertEqual({i['name'] for i in items if i['byte_offset'] in (0x244, 0x248)},
                         {'pad_145', 'pad_146'})
        self.assertFalse(any('reset' in i for i in items))
        self.assertEqual(len(manifest['tables'][0]['registers']), 153)
        self.assertEqual(len(manifest['corrections']), 5)

    def test_official_pdf_locations_independently_corroborate_sdk(self):
        root = pinmux.ESOS.parent / 'full-manuals'
        self.assertEqual(hashlib.sha256((root / 'k3_um_en.pdf').read_bytes()).hexdigest(),
                         '570d7cf33ec28d1b537057350d462ab8c15fb8044fb3fb86855d0e10c6772c25')
        text = (root / 'K3-layout.txt').read_text()
        section = text.rsplit('3.5. Multi-Function Pin Register (MFPRs)', 1)[1].split('4. Electrical Characteristics', 1)[0]
        rows = re.findall(r'(\w+)\s+0x(D401E[0-9A-F]{3})\s+0x([0-9A-F]+)', section)
        self.assertEqual(len(rows), 153)
        ir, _ = pinmux.build()
        offsets = {i['name']: i['byte_offset'] for i in ir['block/' + pinmux.BLOCK]['items']}
        for name, address, offset in rows:
            physical = int(offset, 16)
            self.assertEqual(int(address, 16), 0xd401e000 + physical)
            if name in ('QSPI_CS1', 'QSPI_CLK'):
                key = f'pad_{physical // 4}'
                self.assertEqual((name, physical), ('QSPI_CS1', 0x244) if name == 'QSPI_CS1' else ('QSPI_CLK', 0x248))
            else:
                key = name.lower()
            self.assertEqual(offsets[key], physical)

    def test_pinned_c_compiler_encodings(self):
        # Execute only numeric binding definitions, not the vendor MMIO driver.
        header = pinmux.ESOS / pinmux.HEADER
        self.assertEqual(hashlib.sha256(header.read_bytes()).hexdigest(), pinmux.HASHES[pinmux.HEADER])
        checks = [f'assert(PAD_DS{i} == {i << 9});' for i in range(16)]
        checks += [f'assert(MUX_MODE{i} == {i});' for i in range(8)]
        checks += ['assert(ST02 == 512);', 'assert((ST02 & PAD_DS1) == 512);',
                   'assert(EDGE_NONE == 64);', 'assert(PULL_UP == 49152);',
                   'assert(PULL_DOWN == 40960);', 'assert(PWR_SSP_RXD * 4 == 0x268);']
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'oracle.c'
            source.write_text(f'#include <assert.h>\n#include "{header}"\nint main(void) {{\n' + '\n'.join(checks) + '\n}\n')
            subprocess.run(['cc', '-Wall', '-Werror', str(source), '-o', str(root / 'oracle')], check=True, capture_output=True)
            subprocess.run([str(root / 'oracle')], check=True, capture_output=True)

    def test_field_subset_does_not_invent_independent_schmitt(self):
        ir, _ = pinmux.build()
        fields = ir['fieldset/' + pinmux.FIELDSET]['fields']
        self.assertEqual(len(fields), 6)
        self.assertEqual([(f['bit_offset'], f['bit_size']) for f in fields if f['name'] == 'drive'], [(9, 4)])
        self.assertNotIn('schmitt', {f['name'] for f in fields})
        used = 0
        for field in fields:
            mask = ((1 << field['bit_size']) - 1) << field['bit_offset']
            self.assertEqual(used & mask, 0)
            used |= mask
        self.assertEqual(used & ((1 << 3) | (1 << 7) | (1 << 8)), 0)


if __name__ == '__main__':
    unittest.main()
