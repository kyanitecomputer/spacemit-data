#!/usr/bin/env python3
"""Import K3 SDK pin locations/encodings, preserving conflicts as gaps."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manual_tables import integer

REPO = Path(__file__).resolve().parents[2]
ESOS = Path('/tmp') / 'spacemit-research' / 'esos-full'
HEADER = 'bsp/spacemit/platform/rt24/k3-pinctrl.h'
DTS = 'bsp/spacemit/platform/rt24/k3.dtsi'
DRIVER = 'bsp/spacemit/drivers/pinctrl/pinctrl-single.c'
BLOCK = 'k3_pinmux::Mfpr'
FIELDSET = 'k3_pinmux::regs::Pin'
# Exact input pins, not arbitrary current SDK revisions.
HASHES = {
    HEADER: '717b92d6997f6975bd57972936d5404fb999dd9fdcce72e219a2b0893235c713',
    DTS: 'ac320a4c4179ecac9ac07aeaac60ecd4c1224ef5fa2fe3404b176a7692f1cf20',
    DRIVER: '222e15f453afa90edb7ce279847f68b5477b8831f4d152cf142e7386c1788b9f',
}


def build(esos_root=ESOS):
    sources = {p: (esos_root / p).read_bytes() for p in (HEADER, DTS, DRIVER)}
    if any(hashlib.sha256(v).hexdigest() != HASHES[p] for p, v in sources.items()):
        raise ValueError('pinned K3 pinctrl sources changed')
    lines = sources[HEADER].decode().splitlines()
    locations = []
    for line, text in enumerate(lines, 1):
        match = re.fullmatch(r'\s*#define\s+(\w+)\s+PINID\((\d+)\)\s*', text)
        if match:
            name, index = match[1], int(match[2])
            if name.startswith('GPIO_') and index >= 128:
                continue  # C aliases, not additional physical words.
            # Do not resolve the manual vs SDK QSPI labels by guessing.
            name = f'pad_{index}' if index in (145, 146) else name.lower()
            locations.append((name, index * 4, line))
    if len(locations) != 153 or len({o for _, o, _ in locations}) != 153:
        raise ValueError('incomplete/duplicate K3 pin inventory')
    if {o for _, o, _ in locations} != {i * 4 for i in range(155) if i not in (131, 132)}:
        raise ValueError('K3 sparse pin inventory changed')
    macros = {}
    for text in lines:
        match = re.match(r'\s*#define\s+(\w+)\s+(.+?)(?:\s*/\*.*)?$', text)
        if match and match[1].startswith(('MUX_MODE', 'PAD_DS', 'EDGE_', 'PULL_', 'ST', 'SPU_', 'SLE_')):
            macros[match[1]] = integer(match[2].strip())
    expected = {f'MUX_MODE{i}': i for i in range(8)} | {f'PAD_DS{i}': i << 9 for i in range(16)}
    expected |= {'EDGE_NONE': 64, 'EDGE_RISE': 16, 'EDGE_FALL': 32, 'EDGE_BOTH': 48,
                 'PULL_DIS': 0, 'PULL_UP': 6 << 13, 'PULL_DOWN': 5 << 13,
                 'ST00': 0, 'ST01': 1 << 8, 'ST02': 2 << 8, 'ST03': 3 << 8,
                 'SPU_EN': 8, 'SLE_EN': 128}
    if macros != expected:
        raise ValueError('K3 pin encoding changed')
    dt = sources[DTS].decode()
    if 'pinctrl-single,register-width = <32>' not in dt or 'reg = <0xd401e000 0x250>' not in dt:
        raise ValueError('K3 pin width/address source changed')
    fields = [('function', 0, 3), ('edge_rise', 4, 1), ('edge_fall', 5, 1),
              ('edge_none', 6, 1), ('drive', 9, 4), ('pull', 13, 3)]
    ir = {'block/' + BLOCK: {'description': 'K3 native32 SDK MFPR words. No inferred reset or auxiliary translation. pad_145/pad_146 deliberately have no QSPI signal label.',
                            'items': [{'name': n, 'byte_offset': o, 'bit_size': 32, 'access': 'ReadWrite', 'fieldset': FIELDSET,
                                       'description': f'{HEADER}:{line}; mapping/ownership and per-pin applicability are board responsibilities.'} for n, o, line in locations]},
          'fieldset/' + FIELDSET: {'bit_size': 32, 'description': 'K3 SDK encoding subset. ST[9:8] overlaps DRIVE[12:9]: no Schmitt setter. Strong-pull/slew are excluded by SDK DT mask and left untouched.',
                                   'fields': [{'name': n, 'bit_offset': o, 'bit_size': w} for n, o, w in fields]}}
    corrections = [
        (HEADER, 217, 'ST[9:8] overlaps DRIVE[12:9]; do not combine or infer independent Schmitt and drive settings.', 'SDK ST02/ST03 and PAD_DS macros; HAL preserves bit8 and has no Schmitt setter.'),
        (HEADER, 174, 'Use neutral pad_145/pad_146 names: manual labels QSPI_CS1 at0x244 and QSPI_CLK at0x248, SDK reverses them.', 'Official K3 manual V1.13 page69, SHA256570d7cf33ec28d1b537057350d462ab8c15fb8044fb3fb86855d0e10c6772c25, section3.5. No automatic QSPI routing.'),
        (DTS, 111, 'DT aperture0x250 is smaller than the published SDK/MMIO word inventory ending0x268. Never infer whole-bank accessibility from this aperture.', 'K3 manual V1.13 pages68-70 confirms named sparse words throughPWR_SSP_RXD0x268. Board must prove mapping of each selected word.'),
        (DTS, 115, 'DT mask0xff77 excludes strong-pull bit3 and slew bit7. Preserve them; no setters or inferred electrical values.', f'{HEADER} macros; no reset or per-pin wake promise.'),
        (DRIVER, 217, 'Native32 readl/writel and pinctrl RMW establish the ordinary word access; preserve bits outside selected settings.', f'{DRIVER}:217-235,939-975; K3_PADCONF supplies pinid*4.'),
    ]
    manifest = {'schema': 1, 'chip': 'k3', 'registers': ['expanded_k3_pinmux'],
                'sources': [{'kind': 'esos', 'path': p, 'sha256': hashlib.sha256(v).hexdigest()} for p, v in sources.items()],
                'instances': [{'name': 'AP_K3_PINMUX', 'block': BLOCK, 'address': 0xd401e000, 'view': 'ap', 'source': {'path': DTS, 'line': 111}}],
                'aliases': {}, 'write_mask_registers': [],
                'tables': [{'source': HEADER, 'line': 13, 'status': 'generated',
                            'registers': [BLOCK + '.' + name for name, _, _ in locations],
                            'note': 'Pinned C numeric PINID and K3_PADCONF definitions; GPIO aliases do not add words. Width from K3 DT and native32 C accessors.'}],
                'corrections': [{'source': p, 'line': line, 'decision': d, 'evidence': e} for p, line, d, e in corrections]}
    return ir, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path)  # Shared importer CLI, no docs input required here.
    parser.add_argument('--esos-root', type=Path, default=ESOS)
    parser.add_argument('--repo-root', type=Path, default=REPO)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    ir, manifest = build(args.esos_root)
    for relative, value in [('data/registers/expanded_k3_pinmux.yaml', ir), ('provenance/expanded/k3_pinmux.json', manifest)]:
        rendered = json.dumps(value, indent=2) + '\n'
        path = args.repo_root / relative
        if args.check:
            if path.read_text() != rendered:
                raise SystemExit(f'stale import: {relative}')
        else:
            path.write_text(rendered)
    print('K3 pinmux:153 native32 words,6 unambiguous field definitions; conflicts retained')


if __name__ == '__main__':
    main()
