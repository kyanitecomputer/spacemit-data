#!/usr/bin/env python3
"""Deterministic K3 image facts importer (no vendor description prose).

The source's complete V2D V2 bank is 386 consecutive 32-bit words. Repeated
scaler pairs, four layers and 256 palette entries are checked before folding.
No K1 top/FBC/MMU registers are extrapolated into the different K3 bank.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manual_tables import bit_range, extract, identifier, integer

DEFAULT_SOURCE_ROOT = Path(tempfile.gettempdir()) / 'spacemit-research/docs/en/key_stone'
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
CHAPTER = 'k3/k3_docs/k3_usermanual/10_image.md'
ADDRESS_MAP = 'k3/k3_docs/k3_usermanual/06_address_map.md'
INTERRUPTS = 'k3/k3_docs/k3_usermanual/07_interrupts.md'
BLOCK = 'k3_image::V2d'
LAYER = 'k3_image::Layer'
LINUX_PIN = '31c449aeaad8c7759bc983ca0e26946e5b6746dc'
C_URL = f'https://github.com/spacemit-com/linux-6.6/blob/{LINUX_PIN}/drivers/soc/spacemit/v2d/'
# These are explicit conservative access decisions, not assertions of W1C/W1S.
DIRECT_WRITE = {
    0: 'Trigger and configuration share one word; expose read/direct-write views without RMW.',
    129: 'DMA clear controls and configuration share one word; expose read/direct-write views without RMW. Clear polarity and self-clearing behavior are unspecified.',
}
FUNCTIONAL = {
    308: ('not_mmio', [], 'Pixel-algorithm variables, not MMIO bit positions.'),
    320: ('duplicate', [f'{LAYER}.reg_5'], 'Input format and swap functional references; numeric definitions cover all four layers.'),
    368: ('duplicate', [f'{LAYER}.reg_8', f'{LAYER}.reg_9'], 'Solid color functional references; enable and RGBA components are packed into two words per layer.'),
    356: ('not_mmio', [], 'Solid-color algorithm variables, not MMIO bit positions.'),
    456: ('not_mmio', [], 'Rotation algorithm variables, not MMIO bit positions.'),
    466: ('duplicate', [f'{LAYER}.reg_5', f'{LAYER}.reg_4'], 'Functional field references; numeric definitions cover all four layers.'),
    513: ('duplicate', [f'{LAYER}.reg_{i}' for i in range(9, 16)], 'CSC field references; twelve coefficients per layer are in seven packed words.'),
    520: ('not_mmio', [], 'CSC intermediate variables, not MMIO bit positions.'),
    629: ('not_mmio', [], 'Output algorithm variables, not MMIO bit positions.'),
    640: ('duplicate', [f'{BLOCK}.v2_reg_{i}' for i in range(35, 38)], 'Output format, swap, layout and crop functional references; use the numeric bank field widths.'),
}


def correction(line: int, decision: str, evidence: str) -> dict:
    return {'source': CHAPTER, 'line': line, 'decision': decision, 'evidence': evidence}


CORRECTIONS = [
    correction(7, 'MIPI CSI has no numeric register bank in chapter 10; no CSI registers or instances invented.', '10.1.1-10.1.3; address-map chapter 6 has no CSI instance address.'),
    correction(166, 'V2D MMU, AFBC and interrupt capabilities have no separate numeric control/status tables in this chapter. Do not import K1 top/FBC/MMU offsets into the K3 V2 bank.', '10.3.2 and 10.3.6; 07_interrupts.md:107 supplies APLIC input 86, not V2D interrupt-register definitions.'),
    correction(36, 'GPU has features but no numeric registers in chapter 10; the address-map base alone cannot define a register bank.', '10.2.1-10.2.2; 06_address_map.md:48 gives only the GPU window.'),
    correction(322, 'Functional text mentions only layers 0 and 1, but the numeric V2 bank contains layers 0 through 3. Use four layers, stride 0x54.', '10_image.md:1112-1979, V2D_V2_REG_43 through V2D_V2_REG_126; K3 V1.13 PDF pp.229-246.'),
    correction(642, 'Functional Output_format is called 3-bit, but the numeric R_OUT_FORMAT field is bits 3:0. Retain the numeric four-bit field without extrapolating enum values.', '10_image.md:642 and :1041; K3 V1.13 PDF V2D_V2_REG_35.'),
    correction(661, 'K3 RDMA burst length is eight bits at 23:16, unlike the related K1 C field at 3:1. Do not copy the K1 control bit layout.', C_URL + 'v2d_reg.h#L262-L270; 10_image.md:661; K3 V1.13 PDF p.220.'),
    correction(652, 'Keep K3 V2 offsets relative to 0xC0100000, without the K1 core offset 0x100. K1 top, encoder, decoder and MMU registers are not K3 supplements.', C_URL + 'v2d_reg.h#L5-L11 and #L39-L64; K3 V1.13 PDF p.220.'),
    correction(658, DIRECT_WRITE[0] + ' Source fields remain RW facts; this API restriction does not claim W1C.', '10_image.md:658-665; ' + C_URL + 'v2d_hw.c#L833-L858 uses a complete trigger/configuration write for the related K1 core.'),
    correction(2007, 'Preserve DAMC spelling for the two clear fields; do not silently rename them DMAC.', '10_image.md:2007-2008; K3 V1.13 PDF p.247; ' + C_URL + 'v2d_reg.h#L272-L292 uses the same damc spelling.'),
    correction(2005, DIRECT_WRITE[129] + ' Do not infer W1C from CLR names or grant modify.', '10_image.md:2005-2019 declares RW but descriptions contain no side-effect definition; ' + C_URL + 'v2d_hw.c#L819-L830 uses a complete DMA configuration write.'),
    correction(2024, 'Palette contains exactly 256 32-bit entries; index 255 is offset 0x604, not an unbounded formula.', 'V2D_V2_REG_130-385; 10_image.md:2021-4066; ' + C_URL + 'v2d_reg.h#L170 has the related K1 formula with an additional core base.'),
]


def parse_fields(table: dict) -> list[dict]:
    """Retain numeric access/reset facts; reject malformed or overlapping rows."""
    if table['header'] != ['Bits', 'Field', 'Type', 'Reset']:
        raise ValueError(f"unexpected field header at {table['line']}")
    result = []
    used = 0
    names = set()
    for row in table['rows']:
        bits, name, access, reset = row['values']
        lo, size = bit_range(bits)
        if lo + size > 32:
            raise ValueError(f"field outside 32-bit word at {row['line']}")
        mask = ((1 << size) - 1) << lo
        if mask & used:
            raise ValueError(f"overlapping field at {row['line']}")
        used |= mask
        if access not in {'RW', 'RO', 'WO'}:
            raise ValueError(f"unsupported access at {row['line']}: {access}")
        reset_value = integer(reset)
        if not 0 <= reset_value < (1 << size):
            raise ValueError(f"reset outside field at {row['line']}")
        if name != 'RSVD':
            if name in names:
                raise ValueError(f"duplicate field at {row['line']}")
            names.add(name)
        result.append({'name': name, 'bit_offset': lo, 'bit_size': size,
                       'access': access, 'reset': reset_value, 'line': row['line']})
    if used != 0xffffffff:
        raise ValueError(f"incomplete 32-bit field layout at {table['line']}")
    return result


def normalized(fields: list[dict], kind: str, index: int) -> list[dict]:
    result = []
    for field in fields:
        field = {k: v for k, v in field.items() if k != 'line'}
        name = field['name']
        if kind == 'layer':
            name = name.replace(f'R_L{index}_', 'R_L_')
        elif kind == 'scaler' and name != 'RSVD':
            if name == f'R_SCALER_COEF{2 * index}':
                name = 'R_SCALER_COEF_EVEN'
            elif name == f'R_SCALER_COEF{2 * index + 1}':
                name = 'R_SCALER_COEF_ODD'
            else:
                raise ValueError(f'unexpected scaler coefficient {name}')
        elif kind == 'palette' and name != 'RSVD':
            if name != f'R_PALETTE_TABLE{index}':
                raise ValueError(f'unexpected palette entry {name}')
            name = 'R_PALETTE_TABLE'
        field['name'] = name
        result.append(field)
    return result


def require_repeat(reference: list[dict], candidate: list[dict], kind: str, index: int):
    if reference != normalized(candidate, kind, index):
        raise ValueError(f'inconsistent {kind} layout at index {index}')


def import_bank(tables: list[dict]) -> tuple[dict, list[dict]]:
    bank = {}
    for table in tables:
        match = re.fullmatch(r'V2D_V2_REG_(\d+)', table['heading'][-1])
        if not match:
            if table['line'] not in FUNCTIONAL:
                raise ValueError(f"unaccounted table at {table['line']}")
            continue
        number = int(match[1])
        if number in bank:
            raise ValueError(f'duplicate register {number}')
        if not table['offset_labels']:
            raise ValueError(f'missing offset for register {number}')
        # The helper retains a 20-line context and may include the previous
        # word's label. The last label belongs to the current heading.
        offset = integer(table['offset_labels'][-1]['value'].split(':', 1)[1])
        if offset != 4 * number:
            raise ValueError(f'inconsistent offset for register {number}: {offset:#x}')
        bank[number] = (table, parse_fields(table))
    if set(bank) != set(range(386)):
        raise ValueError('expected complete V2D_V2_REG_0..385 bank')
    if {t['line'] for t in tables if not re.fullmatch(r'V2D_V2_REG_(\d+)', t['heading'][-1])} != set(FUNCTIONAL):
        raise ValueError('functional table inventory changed')

    scaler = normalized(bank[1][1], 'scaler', 0)
    for i in range(24):
        require_repeat(scaler, bank[1 + i][1], 'scaler', i)
    for j in range(21):
        layer = normalized(bank[43 + j][1], 'layer', 0)
        for i in range(4):
            require_repeat(layer, bank[43 + 21 * i + j][1], 'layer', i)
    palette = normalized(bank[130][1], 'palette', 0)
    for i in range(256):
        require_repeat(palette, bank[130 + i][1], 'palette', i)

    ir = {f'block/{BLOCK}': {'description': 'K3 V2D V2. 32-bit words, offsets 0x000..0x604. AP base 0xC0100000; APLIC input 86. No inferred RCPU translation.', 'items': []},
          f'block/{LAYER}': {'description': 'Layer n=0..3; base offset 0xAC+0x54*n. Local word j=0..20 is V2D_V2_REG_(43+21*n+j). Field R_L_ denotes source R_Ln_.', 'items': []}}
    accounting = []

    def add_register(block: str, name: str, offset: int, fields: list[dict], desc: str, array: dict | None = None, direct: bool = False) -> list[str]:
        reset = sum(f['reset'] << f['bit_offset'] for f in fields)
        fieldset_module = 'regs' if block == BLOCK else 'layer_regs'
        fieldset = f'k3_image::{fieldset_module}::{"".join(s.capitalize() for s in name.split("_"))}'
        nonreserved = [f for f in fields if f['name'] != 'RSVD']
        access_set = {f['access'] for f in nonreserved}
        if access_set == {'RO'}:
            access = 'Read'
        elif access_set == {'WO'}:
            access = 'Write'
        elif access_set <= {'RW', 'RO'}:
            access = 'ReadWrite'
        else:
            raise ValueError(f'unresolved mixed register access: {block}.{name}')
        ir[f'fieldset/{fieldset}'] = {'bit_size': 32, 'description': 'Numeric fields; reset facts do not define chiptool Default.',
                                    'fields': [{'name': identifier(f['name']), 'bit_offset': f['bit_offset'],
                                                'bit_size': f['bit_size'], 'description': f"Source {f['access']}; reset {f['reset']}."} for f in nonreserved]}
        names = [name + '_read', name + '_write'] if direct else [name]
        for k, item_name in enumerate(names):
            item = {'name': item_name, 'byte_offset': offset, 'access': ['Read', 'Write'][k] if direct else access,
                    'bit_size': 32, 'fieldset': fieldset, 'description': f'{desc} Reset word 0x{reset:08X}.'}
            if array:
                item['array'] = array
            ir[f'block/{block}']['items'].append(item)
        return [f'{block}.{n}' for n in names]

    references = {}
    for number in [0, *range(25, 43), 127, 128, 129]:
        table, fields = bank[number]
        references[number] = add_register(BLOCK, f'v2_reg_{number}', number * 4, fields,
                                           f"V2D_V2_REG_{number}; {CHAPTER}:{table['line']}.", direct=number in DIRECT_WRITE)
    scaler_ref = add_register(BLOCK, 'scaler_coef', 4, scaler, 'Index i=0..23: source word 1+i, coefficients 2*i and 2*i+1.', {'len': 24, 'stride': 4})
    for number in range(1, 25):
        references[number] = scaler_ref
    ir[f'block/{BLOCK}']['items'].append({'name': 'layer', 'byte_offset': 0xac, 'block': LAYER, 'array': {'len': 4, 'stride': 0x54}, 'description': 'Index n=0..3. Registers 43+21*n through 63+21*n.'})
    for j in range(21):
        refs = add_register(LAYER, f'reg_{j}', j * 4, normalized(bank[43 + j][1], 'layer', 0),
                            f'Layer-local word j={j}; source register 43+21*n+{j}, n=0..3.')
        for n in range(4):
            references[43 + 21 * n + j] = refs
    palette_ref = add_register(BLOCK, 'palette_table', 0x208, palette, 'Index i=0..255: source register 130+i, offset 0x208+4*i.', {'len': 256, 'stride': 4})
    for number in range(130, 386):
        references[number] = palette_ref
    for table in tables:
        match = re.fullmatch(r'V2D_V2_REG_(\d+)', table['heading'][-1])
        if match:
            number = int(match[1])
            refs = references[number]
            status = 'generated'
            if 1 <= number <= 24:
                note = f'Scaler pair index {number - 1}; offset {number * 4:#x}.'
            elif 43 <= number <= 126:
                note = f'Layer index {(number - 43) // 21}, local word {(number - 43) % 21}; offset {number * 4:#x}.'
            elif number >= 130:
                note = f'Palette index {number - 130}; offset {number * 4:#x}.'
            else:
                note = f'32-bit word at offset {number * 4:#x}.'
            if number in DIRECT_WRITE:
                note += ' Read/direct-write aliases; no RMW or assumed W1C.'
        else:
            status, refs, note = FUNCTIONAL[table['line']]
        accounting.append({'source': CHAPTER, 'line': table['line'], 'status': status, 'registers': refs, 'note': note})
    for key, value in ir.items():
        if key.startswith('block/'):
            value['items'].sort(key=lambda i: (i['byte_offset'], i['name']))
    return ir, accounting


def source_tables(path: Path, root: Path) -> list[dict]:
    tables = extract(path, root)
    known = {t['line'] for t in tables}
    # The common extractor deliberately ignores Register Name/Comment tables
    # without numeric columns. They still need a disposition in this chapter.
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.startswith('|') and (i == 0 or not lines[i - 1].startswith('|')) and i + 1 not in known:
            if line != '| Register Name | Comment |' or i + 1 not in {320, 368}:
                raise ValueError(f'unaccounted nonnumeric table at {i + 1}')
            tables.append({'source': CHAPTER, 'line': i + 1, 'heading': ['Functional register references']})
    return sorted(tables, key=lambda t: t['line'])


def build(source_root: Path) -> tuple[dict, dict]:
    source = source_root / CHAPTER
    contents = source.read_text()
    if not re.search(r'The base address:\s*0xC010_0000\.', contents):
        raise ValueError('unexpected V2D base')
    address_lines = (source_root / ADDRESS_MAP).read_text().splitlines()
    address_matches = [(i + 1, line) for i, line in enumerate(address_lines) if line.startswith('| V2D |')]
    if len(address_matches) != 1 or '0xC010_0000' not in address_matches[0][1] or '0x0020_0000' not in address_matches[0][1]:
        raise ValueError('unexpected V2D address-map instance')
    interrupt_lines = (source_root / INTERRUPTS).read_text().splitlines()
    irq_matches = [(i + 1, line) for i, line in enumerate(interrupt_lines) if 'v2d_irq' in line]
    if len(irq_matches) != 1 or irq_matches[0][1] != '| aplic_int[86] | v2d_irq |':
        raise ValueError('unexpected V2D interrupt')
    ir, tables = import_bank(source_tables(source, source_root))
    tables.extend({'source': INTERRUPTS, 'line': line, 'status': 'not_mmio', 'registers': [],
                   'note': 'Interrupt-source assignments, not numeric MMIO register layouts.'} for line in (23, 324))
    manifest = {'schema': 1, 'chip': 'k3', 'registers': ['expanded_k3_image'],
                'sources': [{'path': path, 'sha256': hashlib.sha256((source_root / path).read_bytes()).hexdigest(), 'kind': 'docs'} for path in [CHAPTER, ADDRESS_MAP, INTERRUPTS]],
                'instances': [{'name': 'AP_K3_IMAGE_V2D', 'block': BLOCK, 'address': 0xc0100000, 'view': 'ap', 'source': {'path': ADDRESS_MAP, 'line': address_matches[0][0]}}],
                'aliases': {BLOCK: [[f'v2_reg_{n}_read', f'v2_reg_{n}_write'] for n in DIRECT_WRITE]},
                'write_mask_registers': [f'{BLOCK}.v2_reg_{n}_write' for n in DIRECT_WRITE],
                'tables': tables, 'corrections': [dict(c) for c in CORRECTIONS]}
    return ir, manifest


def render(value: dict) -> str:
    # JSON is a deterministic subset of YAML understood by chiptool's serde.
    return json.dumps(value, indent=2) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument('--repo-root', type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    ir, manifest = build(args.source_root)
    for relative, value in [('data/registers/expanded_k3_image.yaml', ir), ('provenance/expanded/k3_image.json', manifest)]:
        path = args.repo_root / relative
        output = render(value)
        if args.check:
            if not path.is_file() or path.read_text() != output:
                raise SystemExit(f'stale import: {path}')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output)
    fields = sum(len(v['fields']) for k, v in ir.items() if k.startswith('fieldset/'))
    print(f"K3 image: {len(manifest['tables'])} tables; 386 physical words; 2 blocks; {fields} field definitions; 1 AP instance; {'check passed' if args.check else 'written'}")


if __name__ == '__main__':
    main()
