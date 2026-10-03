#!/usr/bin/env python3
"""Deterministic K1 memory/IO fact importer; documentation pin 99fc0677.

JSON is also YAML. Facts in the C supplement are deliberately declarative: no
network access is performed by this importer. --linux-root optionally checks the
pinned C fingerprints; --source-root supplies the original Markdown tables.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import manual_tables as mt

DEFAULT_ROOT = Path('/tmp/spacemit-research/docs/en/key_stone')
DEFAULT_ESOS_ROOT = DEFAULT_ROOT.parents[2] / 'esos-full'
PREFIX = 'k1/k1_docs/k1_usermanual/'
CHAPTERS = ['10.Memory_&_Storage.md', '15.High-Speed_Interface_System.md',
            '16.Low-Speed_Interface_System.md']
MAP = PREFIX + '6.Address_Mapping.md'
IRQ = PREFIX + '7.Interrupt_Assignments.md'
LINUX_PIN = '31c449aeaad8c7759bc983ca0e26946e5b6746dc'
ESOS_PIN = 'e0b8d955bdc84957da3b7bba8c89e35ee0d27c2b'
EMAC_C = 'drivers/net/ethernet/spacemit/k1x-emac.h'
PCIE_C = 'drivers/pci/controller/dwc/pcie-k1x.c'
QSPI_C = 'drivers/spi/spi-k1x-qspi.c'
USB_C = 'drivers/usb/gadget/udc/k1x_ci_udc.h'
PHY_C = 'drivers/usb/phy/phy-k1x-ci-usb2.h'
DDR_C = 'drivers/soc/spacemit/ddr-bw/spacemit_ddrbw.h'
DTS_C = 'arch/riscv/boot/dts/spacemit/k1-x.dtsi'
ESOS_DTS = 'bsp/spacemit/platform/n308/k1-x/dts/k1-x.dtsi'

# Source errors resolved by independent numeric C definitions, not inferred order.
OFFSET_CORRECTIONS = {
    ('15.High-Speed_Interface_System.md', 1613): (0x1bc, 'MAC_TX_STATCTR_DATA_LOW', EMAC_C + ':126; K1 PDF V6.3 printed page 620 (PDF page 631) repeats the Markdown typo'),
    ('15.High-Speed_Interface_System.md', 2005): (0x364, 'PTP_1588_IRQ_EN', EMAC_C + ':421; K1 PDF V6.3 printed page 637 (PDF page 648) repeats the Markdown typo'),
}
# Prose-defined commands absent from the abbreviated access column.
COMMAND_TABLES = {
    '10.Memory_&_Storage.md': {423, 505, 530, 662, 936, 966, 1192},
    '15.High-Speed_Interface_System.md': {186, 202, 210, 234, 685, 718, 813, 824, 1332, 1519, 1551, 1589, 1731, 1868, 1880, 1993},
    '16.Low-Speed_Interface_System.md': {1105, 1367, 1382, 1413, 1428, 1771, 1906, 2461, 2498, 2515, 2881},
}
# Data reads/writes consume different FIFO views; no modify operation.
FIFO_TABLES = {
    '10.Memory_&_Storage.md': {475},
    '16.Low-Speed_Interface_System.md': {1254, 1823, 2895},
}


def camel(value):
    return ''.join(word.capitalize() for word in mt.identifier(value).split('_'))


def field_facts(table, corrections):
    """Keep known bits despite an unknown reset/access; never infer reset=0."""
    facts = []
    name_counts = {}
    for row in table['rows']:
        bits, name, access, reset = row['values'][:4]
        if not name or re.fullmatch(r'(reserved|rsvd)', name, re.I):
            continue
        low, size = mt.bit_range(bits)
        name = mt.identifier(name)
        name_counts[name] = name_counts.get(name, 0) + 1
        facts.append((row, name, low, size, access, reset))
    result = []
    used = 0
    for row, name, low, size, access, reset in facts:
        if name_counts[name] > 1:
            name += '_b' + str(low)
            corrections.append(dict(source=table['source'], line=row['line'],
                decision='Disambiguate repeated field labels by their documented low bit.',
                evidence=f"{table['source']}:{table['line']}"))
        if used & (((1 << size) - 1) << low):
            raise ValueError(f"overlapping named fields {table['source']}:{row['line']}")
        used |= ((1 << size) - 1) << low
        description = 'Access: ' + (access or 'unspecified') + '.'
        try:
            value = mt.integer(reset)
            if not 0 <= value < 1 << size:
                raise ValueError('reset outside field')
            description += f' Documented reset: {value:#x}.'
        except (ValueError, SyntaxError):
            corrections.append(dict(source=table['source'], line=row['line'],
                decision='Reset is absent or nonnumeric; no hardware reset value asserted.',
                evidence=f"{table['source']}:{row['line']}"))
        result.append(dict(name=name, bit_offset=low, bit_size=size, description=description))
    return result


class Builder:
    def __init__(self, root):
        self.root = root
        self.ir: dict[str, Any] = {}
        self.manifest: dict[str, Any] = dict(schema=1, chip='k1', registers=['expanded_k1_io'],
            sources=[], instances=[], aliases={}, write_mask_registers=[], tables=[], corrections=[])

    def correction(self, source, line, decision, evidence):
        self.manifest['corrections'].append(dict(source=source, line=line,
            decision=decision, evidence=evidence))

    def table(self, source, line, status, registers=(), note=None):
        entry: dict[str, Any] = dict(source=source, line=line, status=status, registers=list(registers))
        if note is None and not source.endswith('.md'):
            note = f'Pinned C definitions beginning at {source}:{line}; tableless supplemental register layout.'
        if note:
            entry['note'] = note
        self.manifest['tables'].append(entry)

    def block(self, name):
        return 'k1_io::' + name

    def register(self, block, name, offset, access='ReadWrite', fields=(), description=None, array=None):
        qualified = self.block(block)
        item = dict(name=name, byte_offset=offset, access=access, bit_size=32)
        if fields:
            fsname = f'k1_io::regs::{block}{camel(name)}'
            self.ir['fieldset/' + fsname] = dict(bit_size=32, fields=list(fields))
            item['fieldset'] = fsname
        if description:
            item['description'] = description
        if array:
            item['array'] = array
        self.ir.setdefault('block/' + qualified, dict(description='K1 ' + block + ' register facts.', items=[]))['items'].append(item)
        if access == 'Write':
            self.manifest['write_mask_registers'].append(qualified + '.' + name)
        return qualified + '.' + name

    def split(self, block, name, offset, fields, description, read_fields=None, write_fields=None):
        read = self.register(block, name, offset, 'Read', fields if read_fields is None else read_fields,
                             description + ' Read view; may have read side effects.')
        write = self.register(block, name + '_write', offset, 'Write', fields if write_fields is None else write_fields,
                              description + ' Explicit write only; no read-modify-write.')
        self.manifest['aliases'].setdefault(self.block(block), []).append([name, name + '_write'])
        return [read, write]

    def instance(self, name, block, address, view, source, line):
        self.manifest['instances'].append(dict(name=view.upper() + '_K1_IO_' + name,
            block=self.block(block), address=address, view=view, source=dict(path=source, line=line)))

    def add_array_block(self, block, target, name, offset, array):
        self.ir['block/' + self.block(block)] = dict(description='K1 indexed ' + target + ' banks.',
            items=[dict(name=name, byte_offset=offset, block=self.block(target), array=array)])

    def import_docs(self):
        for filename in CHAPTERS:
            source = PREFIX + filename
            path = self.root / source
            self.manifest['sources'].append(dict(path=source, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), kind='docs'))
            for table in mt.extract(path, self.root):
                if table['kind'] == 'registers':
                    self.table(source, table['line'], 'not_mmio', note='Instance base-address metadata, not a register layout.')
                    if filename.startswith('10.'):
                        block = 'Sdh'
                    elif table['line'] == 1086:
                        block = 'I2c'
                    elif table['line'] == 1733:
                        block = 'Ssp'
                    else:
                        block = 'GpioBank'
                    for row in table['rows']:
                        label, addr = row['values']
                        name = re.sub(r'_BASE.*', '', label).replace('SEC_', '')
                        view = 'secure' if 'SEC_' in label or 'SEC_I2C' in label or block == 'Ssp' and 'SSP2' in label else 'rcpu' if 'SHUB' in label else 'ap'
                        self.instance(mt.identifier(name).upper(), block, mt.integer(addr), view, source, row['line'])
                    continue
                if 'Descriptor Details' in ' '.join(table['heading']):
                    self.table(source, table['line'], 'not_mmio', note='DMA memory descriptor bits, not MMIO registers.')
                    continue
                if filename.startswith('10.'):
                    block = 'Sdh'
                elif filename.startswith('15.'):
                    block = 'Usb3Glue' if table['line'] < 440 else 'Emac'
                else:
                    section = table['heading'][1].split()[0]
                    block = {'16.1': 'I2c', '16.2': 'Ssp', '16.3': 'Uart', '16.4': 'GpioBank',
                             '16.5': 'OneWire', '16.6': 'IrRx', '16.7': 'Pwm'}[section]
                heading = table['heading'][-2] if table['line'] == 1254 else table['heading'][-1]
                name = mt.identifier(re.sub(r'\bregisters?\b', '', re.sub(r'^\d+(?:\.\d+)+\s+', '', heading), flags=re.I))
                # Repeated USB headings do not identify distinct registers reliably.
                expression = table['offset_labels'][-1]['value'].split(':', 1)[1].strip()
                offset = mt.integer(expression.split('+')[0])
                if block == 'Usb3Glue' and name == 'usb3_ctrl_clk_cfg' and offset:
                    name += f'_at_{offset:x}'
                    self.correction(source, table['line'], 'Retain repeated source heading with offset-qualified name; do not invent a hardware label.', f'{source}:{table["line"]}')
                if block == 'Uart' and name == 'fifo_control' and offset == 0x34:
                    name = 'fifo_control_new'
                fields = field_facts(table, self.manifest['corrections'])
                if filename == '16.Low-Speed_Interface_System.md' and table['line'] == 2613:
                    if [(f['name'], f['bit_offset'], f['bit_size']) for f in fields] != [('psn', 31, 1)]:
                        raise ValueError('GPIO_PSR source conflict changed')
                    fields[0].update(bit_offset=0, bit_size=32)
                    self.correction(source, 2615, 'GPIO_PSR PSn is a 32-bit set mask: prose specifies n=0..31 and native C writes 1 << (gpio & 31), contradicting the single Bits31 cell.',
                        f'ESOS {ESOS_PIN} bsp/spacemit/drivers/gpio/spacemit-gpio.c:35,109,251-263; K1 V6.3 PDF GPIO_PSR table repeats Bits31 (layout lines39522-39528).')
                key = (filename, table['line'])
                if key in OFFSET_CORRECTIONS:
                    offset, macro, evidence = OFFSET_CORRECTIONS[key]
                    self.correction(source, table['line'], f'Correct conflicting offset to {offset:#x} from {macro}.', f'Linux {LINUX_PIN} {evidence}')
                types = {r['values'][2].replace('/', '').upper() for r in table['rows']
                         if not re.fullmatch(r'(reserved|rsvd)', r['values'][1], re.I)}
                if not types <= {'R', 'RO', 'RC', 'RW', 'W', 'WO', 'RW1C', 'RWAC', 'RWC', ''}:
                    raise ValueError(f'unsupported access at {source}:{table["line"]}: {types}')
                if filename.startswith('15.') and table['line'] in {718, 1731, 1993}:
                    for field in fields:
                        if field['description'].startswith('Access: RW.'):
                            field['description'] = field['description'].replace('Access: RW.', 'Access: R/W1C (prose and C).')
                if key == ('15.High-Speed_Interface_System.md', 1294):
                    types = {'RW'}
                    self.correction(source, table['line'], 'Hash-table4 access column is empty; C writes all four hash words. Reset remains unasserted.', f'Linux {LINUX_PIN} {EMAC_C}:110 and k1x-emac.c emac_mac_hash_table')
                if key == ('16.Low-Speed_Interface_System.md', 1351):
                    types = {'WO'}
                    self.correction(source, table['line'], 'WFIFO transmit write view: table R conflicts with explicit PIO filling and C writes.', f'ESOS {ESOS_PIN} bsp/spacemit/drivers/i2c/i2c-k1.c:528')
                    self.correction(source, table['line'] + 3, 'WFIFO CONTROL mask is 0xf00 (bits 11:8), not the conflicting C WFIFO_CTRL_MSK 0x3e0.', f'ESOS {ESOS_PIN} bsp/spacemit/drivers/i2c/i2c-k1.h:122-126 defines START/STOP/ACKNAK/TB at bits 8/9/10/11; Markdown range and PDF section 16.1.4.9 agree')
                    for field in fields:
                        field['description'] = field['description'].replace('Access: R.', 'Access: W (corrected).')
                if key == ('15.High-Speed_Interface_System.md', 685):
                    self.correction(source, table['line'], 'Ignore reserved-range overlap with the explicitly named bits 17 through 20; retain named fields.', f'{source}:688-693; Linux {LINUX_PIN} {EMAC_C}:155-162')
                # Pure write, RO, or explicit command/FIFO views. Mixed config/clear
                # registers retain config fields in the write view with no modify.
                description = f'Fact source: {source}:{table["line"]}.'
                special = table['line'] in COMMAND_TABLES.get(filename, set()) or table['line'] in FIFO_TABLES.get(filename, set()) or any('W1C' in t or 'WAC' in t or t == 'RWC' for t in types)
                if types <= {'W', 'WO'}:
                    refs = [self.register(block, name, offset, 'Write', fields, description)]
                elif not any(t.startswith('RW') or t in {'W', 'WO'} for t in types):
                    refs = [self.register(block, name, offset, 'Read', fields, description)]
                elif special:
                    readable = [f for f in fields if not f['description'].startswith(('Access: W.', 'Access: WO.'))]
                    writable = [f for f in fields if f['description'].startswith(('Access: RW', 'Access: R/W', 'Access: W.', 'Access: WO.', 'Access: unspecified'))]
                    refs = self.split(block, name, offset, fields, description, readable, writable)
                    self.correction(source, table['line'], 'Separate read and direct-write views for documented commands, FIFO or mixed clear/config fields; no modify API.', f'{source}:{table["line"]}')
                else:
                    refs = [self.register(block, name, offset, 'ReadWrite', fields, description)]
                self.table(source, table['line'], 'generated', refs)
        for filename in [MAP, IRQ]:
            self.manifest['sources'].append({'path': filename, 'sha256': hashlib.sha256((self.root / filename).read_bytes()).hexdigest(), 'kind': 'docs'})
        self.import_instances()
        # These chapters have no further register tables for these families.
        self.correction(PREFIX + CHAPTERS[0], 16, 'DDR chapter has no controller/PHY register layout; only pinned C-defined control/performance registers are added. Training, scheduler and timing layouts remain unavailable.', 'Canonical source location; reviewed chapter has no further numeric layout.')
        self.correction(PREFIX + CHAPTERS[0], 172, 'QSPI chapter has no register layout; pinned K1 Linux supplies the declared register set and two used LUT sequences. Unspecified LUT sequence count is not invented.', 'Canonical source location; reviewed chapter has no further numeric layout.')
        self.correction(PREFIX + CHAPTERS[1], 447, 'PCIe chapter has no register tables; pinned C supplies wrapper/config/PHY-AHB facts. Full DesignWare DBI/ATU/eDMA and analog PHY layout is not supplied by this chapter.', 'Canonical source location; reviewed chapter has no further numeric layout.')
        self.correction(PREFIX + CHAPTERS[1], 131, 'USB chapter tables describe USB3 glue only. Pinned K1 C provides USB2 capability/operational/PHY layouts; the full USB3 DesignWare core layout is not documented here.', 'Canonical source location; reviewed chapter has no further numeric layout.')
        self.correction(MAP, 204, 'Do not synthesize RCPU translations for AP/secure peripherals: the bridge aperture is not an individual peripheral mapping. Sensor-hub numeric instances use their separately documented addresses.', MAP + ':169-204')
        self.correction(MAP, 83, 'GPIO Edge has a base address but no register layout in the owned low-speed chapter or reviewed K1 GPIO C driver; do not substitute the main GPIO bank layout.', 'Canonical source location; reviewed chapter has no further numeric layout.')
        self.correction(IRQ, 175, 'SHUB I2C1 has interrupt 14 but no numeric MMIO base in the address map, low-speed base table or pinned K1 ESOS DTS. No second SHUB I2C instance address is invented.', 'Canonical source location; reviewed chapter has no further numeric layout.')

    def import_instances(self):
        lines = (self.root / MAP).read_text().splitlines()
        rcpu = False
        for number, line in enumerate(lines, 1):
            if line.startswith('## 6.3'):
                rcpu = True
            if not line.startswith('|'):
                continue
            cells = mt.cells(line)
            module = cells[0]
            if len(cells) < 2 or not re.match(r'0x', cells[1]):
                continue
            address = mt.integer(cells[1])
            view = 'rcpu' if rcpu else 'secure' if module.startswith('SEC') else 'ap'
            if re.fullmatch(r'(SEC )?UART\d+|SHUB_UART\d+', module):
                self.instance(module.replace('SEC ', '').upper(), 'Uart', address, view, MAP, number)
            elif module == 'OneWire':
                self.instance('ONEWIRE', 'OneWire', address, view, MAP, number)
            elif module in {'IR', 'R_IR_RX'}:
                self.instance(module, 'IrRx', address, view, MAP, number)
            elif re.fullmatch(r'PWM\d+', module):
                self.instance(module, 'Pwm', address, view, MAP, number)
            elif module in {'GMAC0', 'GMAC1'}:
                self.instance(module, 'Emac', address, view, MAP, number)
            elif module == 'SHUB_SSP1 (SPI)':
                self.instance('SHUB_SSP1', 'Ssp', address, view, MAP, number)
        self.instance('USB3_GLUE', 'Usb3Glue', 0xc0a10000, 'ap', PREFIX + CHAPTERS[1], 134)
        self.correction(PREFIX + CHAPTERS[2], 2218, 'UART identity and addresses follow the chip address map: AP UART0 at D4017000, secure UART1 at F0612000; N308 UARTs remain distinct.', MAP + ':73-84,177 and ESOS ' + ESOS_DTS)
        self.correction(PREFIX + CHAPTERS[2], 3103, 'AP PWM0..7 and PWM8..19 occupy two windows; the chapter continuous stride is incorrect across the gap.', MAP + ':84-112; K1 PDF V6.3 printed page 764 (PDF page 775) repeats the continuous-stride error')
        self.correction(PREFIX + CHAPTERS[1], 264, 'Keep P_ADDR_RO2 access RW as explicitly tabled despite its RO spelling; the adjacent RO view does not establish a correction.', 'K1 PDF V6.3 printed page 552 (PDF page 563), section 15.1.4.13 corroborates RW')
        # Interleaved banks are register arrays, not overlapping block spans.
        gpio_items = []
        for item in self.ir['block/k1_io::GpioBank']['items']:
            if item['name'].startswith('gpio_edr'):
                for i, bank_offset in enumerate([0, 4, 8, 0x100]):
                    clone = copy.deepcopy(item)
                    clone['name'] += str(i)
                    clone['byte_offset'] += bank_offset
                    gpio_items.append(clone)
            else:
                clone = copy.deepcopy(item)
                clone['array'] = {'offsets': [0, 4, 8, 0x100]}
                gpio_items.append(clone)
        self.ir['block/k1_io::Gpio'] = {'description': 'K1 interleaved GPIO banks.', 'items': gpio_items}
        self.manifest['aliases']['k1_io::Gpio'] = [[f'gpio_edr{i}', f'gpio_edr_write{i}'] for i in range(4)]
        for item in gpio_items:
            if item['access'] == 'Write':
                self.manifest['write_mask_registers'].append('k1_io::Gpio.' + item['name'])
        self.add_array_block('PwmArray', 'Pwm', 'channel', 0, {'offsets': [i * 0x400 for i in range(8)] + [0x6000 + i * 0x400 for i in range(12)]})
        self.instance('GPIO', 'Gpio', 0xd4019000, 'ap', MAP, 82)
        self.instance('GPIO', 'Gpio', 0xf0619000, 'secure', MAP, 161)
        self.instance('PWM_CHANNELS', 'PwmArray', 0xd401a000, 'ap', MAP, 84)
        for blockname in ['Uart']:
            items = self.ir['block/' + self.block(blockname)]['items']
            offsets = {}
            for item in items:
                offsets.setdefault(item['byte_offset'], []).append(item['name'])
            for names in offsets.values():
                if len(names) > 1:
                    self.manifest['aliases'].setdefault(self.block(blockname), []).append(names)

    def finish(self):
        # The interleaved bank view reuses these exact source definitions.
        # Account its generated names at the original table/C locations too.
        gpio_names = {i['name'] for i in self.ir['block/k1_io::Gpio']['items']}
        for table in self.manifest['tables']:
            for ref in list(table['registers']):
                if ref.startswith('k1_io::GpioBank.'):
                    name = ref.split('.', 1)[1]
                    clones = [name + str(i) for i in range(4)] if name.startswith('gpio_edr') else [name]
                    table['registers'].extend('k1_io::Gpio.' + n for n in clones if n in gpio_names)
        for entry in self.ir.values():
            if 'items' in entry:
                entry['items'].sort(key=lambda x: (x['byte_offset'], x['name']))
        self.manifest['instances'].sort(key=lambda x: x['name'])
        names = [i['name'] for i in self.manifest['instances']]
        if len(names) != len(set(names)):
            raise ValueError('duplicate instance name')
        for block in self.manifest['aliases']:
            # Split views can coincide with banked UART aliases; use one exact set.
            groups = self.manifest['aliases'][block]
            merged = []
            for group in groups:
                intersect = [g for g in merged if set(g) & set(group)]
                union = set(group)
                for g in intersect:
                    union.update(g)
                    merged.remove(g)
                merged.append(sorted(union))
            self.manifest['aliases'][block] = sorted(merged)
        self.manifest['write_mask_registers'] = sorted(set(self.manifest['write_mask_registers']))
        return dict(sorted(self.ir.items())), self.manifest


# C supplement follows below. Register offsets/access are facts from the cited
# macros and actual readl/writel calls, not the peripheral's name alone.
C_FINGERPRINTS = {'linux:arch/riscv/boot/dts/spacemit/k1-x.dtsi': ['linux', 'arch/riscv/boot/dts/spacemit/k1-x.dtsi', '60e282c66f65170199a4ad54ff873918dfa1088a7dc2b6c9607ee91efae2c154'], 'linux:drivers/net/ethernet/spacemit/emac_ptp.c': ['linux', 'drivers/net/ethernet/spacemit/emac_ptp.c', 'f5d0c1188eb83477913e9b49e51963d8df73de8b1882828a0dfcf1060392870e'], 'linux:drivers/net/ethernet/spacemit/k1x-emac.c': ['linux', 'drivers/net/ethernet/spacemit/k1x-emac.c', '9e4b64275329f3f614397f8fa70839165803471e12a6e2825e1a10e5f5027f6f'], 'linux:drivers/net/ethernet/spacemit/k1x-emac.h': ['linux', 'drivers/net/ethernet/spacemit/k1x-emac.h', '08b8aa394facacd015642541914f0d81f847ca70aa1be9b718ad8916f8f7f953'], 'linux:drivers/pci/controller/dwc/pcie-k1x.c': ['linux', 'drivers/pci/controller/dwc/pcie-k1x.c', 'e241915acc3d6c3e05eaebf69cd8190bd619ed4e89d49aebfcfa0f34cb591c4c'], 'linux:drivers/soc/spacemit/ddr-bw/spacemit_ddrbw.c': ['linux', 'drivers/soc/spacemit/ddr-bw/spacemit_ddrbw.c', '63acab7a0aa408c2976b82824bb9872e375070e0839043b68038e5ab673feb77'], 'linux:drivers/soc/spacemit/ddr-bw/spacemit_ddrbw.h': ['linux', 'drivers/soc/spacemit/ddr-bw/spacemit_ddrbw.h', '91cce587e3af6ddaf870d1748065f739e056e32ef88616035568037be62dbcc8'], 'linux:drivers/spi/spi-k1x-qspi.c': ['linux', 'drivers/spi/spi-k1x-qspi.c', 'bcc1ef761cdf8213f54778a7fd31e7f2b9223d527ab2c985432c9705176fa773'], 'linux:drivers/usb/dwc3/dwc3-spacemit.c': ['linux', 'drivers/usb/dwc3/dwc3-spacemit.c', '33cc025a7f27dd65ac91ff5d0655f202180c330382ba889fed9701edfe79ff15'], 'linux:drivers/usb/gadget/udc/k1x_ci_udc.h': ['linux', 'drivers/usb/gadget/udc/k1x_ci_udc.h', 'daebbb2bebc6a6d530a00b0dadc58036ed82527a93ae595121fa47eba510aec4'], 'linux:drivers/usb/gadget/udc/k1x_udc_core.c': ['linux', 'drivers/usb/gadget/udc/k1x_udc_core.c', 'e5799f1bc70a1778a47b443e50a5fb1db35098ab777d15c14ddb6f679fea583b'], 'linux:drivers/usb/phy/phy-k1x-ci-usb2.c': ['linux', 'drivers/usb/phy/phy-k1x-ci-usb2.c', '850d77fc151215b3b4d5676ca747d8ed1723ed9846f3c01ba03b4ee19117988d'], 'linux:drivers/usb/phy/phy-k1x-ci-usb2.h': ['linux', 'drivers/usb/phy/phy-k1x-ci-usb2.h', 'b47864c3cb7ed35328095ee63a355bc0dfac87e73dd3970bea6f515361c46cd7'], 'esos:bsp/spacemit/drivers/gpio/spacemit-gpio.c': ['esos', 'bsp/spacemit/drivers/gpio/spacemit-gpio.c', '15baf6e82b45f326dc45ed6849398f80a18cac104fb5308fd96b6a27fb1f4b65'], 'esos:bsp/spacemit/drivers/i2c/i2c-k1.c': ['esos', 'bsp/spacemit/drivers/i2c/i2c-k1.c', '4ebd315fe4ce0950a4a89efea304486af980d5adf5e12dfcd278a7e0fd036c59'], 'esos:bsp/spacemit/drivers/i2c/i2c-k1.h': ['esos', 'bsp/spacemit/drivers/i2c/i2c-k1.h', '1330c98d044b66738104720c491bd447dac04347f8eb6819b0a21f7baf08aa33'], 'esos:bsp/spacemit/drivers/pwm/pwm-pxa.c': ['esos', 'bsp/spacemit/drivers/pwm/pwm-pxa.c', 'cc4ef492ae808c7286bcc6f7cb2d732efdb96449e786b0c2557d8ee159692424'], 'esos:bsp/spacemit/drivers/spi/k1x_spi.c': ['esos', 'bsp/spacemit/drivers/spi/k1x_spi.c', 'fd646776f93e8228f60f2359f3809ef9e108cc7186eb367fc6f823398509cecc'], 'esos:bsp/spacemit/drivers/spi/k1x_spi.h': ['esos', 'bsp/spacemit/drivers/spi/k1x_spi.h', 'c08ba6e393a6486fd146499373817a06ad256a6b7e9d57697e0824e94c0b32bb'], 'esos:bsp/spacemit/drivers/uart/pxa_uart.c': ['esos', 'bsp/spacemit/drivers/uart/pxa_uart.c', 'a3634ad1fc4d7b5801972f1163471d09e39ceb8e856345b3b4c9b4a4f37ed644'], 'esos:bsp/spacemit/drivers/uart/pxa_uart.h': ['esos', 'bsp/spacemit/drivers/uart/pxa_uart.h', '32e2a0aa68432b7fb0336b5e01283e0d2c2e2cfaa4cd906f90223a14b888dd33'], 'esos:bsp/spacemit/platform/n308/k1-x/dts/k1-x.dtsi': ['esos', 'bsp/spacemit/platform/n308/k1-x/dts/k1-x.dtsi', 'c35dc10e6962b4a2315856baf519c036b90fd5722650a9e21a6cd7425a8db357']}


def c_fields(spec):
    return [{'name': name, 'bit_offset': low, 'bit_size': size} for name, low, size in spec]


def supplement(b):
    def facts(block, source, line, specs):
        refs = []
        for name, offset, access, fields, array in specs:
            description = f'Linux {LINUX_PIN} {source}:{line}. 32-bit MMIO; no reset asserted.'
            if access == 'Command':
                write_fields = c_fields(fields)
                if block == 'Usb2Operational' and name == 'usbsts':
                    write_fields = [f for f in write_fields if f['name'] == 'interrupts']
                elif block == 'Usb2Phy' and name == 'reg01':
                    write_fields = []  # C reads PLL-ready but writes raw configuration.
                refs += b.split(block, name, offset, c_fields(fields), description, write_fields=write_fields)
            else:
                refs.append(b.register(block, name, offset, access, c_fields(fields), description, array))
        b.table(source, line, 'generated', refs)
        return refs

    # QSPI register declarations and their actual qspi_readl/qspi_writel use.
    # Unused declarations have an explicitly conservative read-only raw view.
    qspi = [
        ('mcr', 0x00, 'Command'), ('tcr', 0x04, 'Read'), ('ipcr', 0x08, 'Write'),
        ('flshcr', 0x0c, 'Read'), ('buf0cr', 0x10, 'Write'), ('buf1cr', 0x14, 'Write'),
        ('buf2cr', 0x18, 'Write'), ('buf3cr', 0x1c, 'Write'), ('bfgencr', 0x20, 'Write'),
        ('soccr', 0x24, 'Write'), ('buf0ind', 0x30, 'Write'), ('buf1ind', 0x34, 'Write'),
        ('buf2ind', 0x38, 'Write'), ('sfar', 0x100, 'Write'), ('sfacr', 0x104, 'Write'),
        ('smpr', 0x108, 'Write'), ('rbsr', 0x10c, 'Read'), ('rbct', 0x110, 'Write'),
        ('tbsr', 0x150, 'Read'), ('tbdr', 0x154, 'Write'), ('tbct', 0x158, 'Write'),
        ('sr', 0x15c, 'Read'), ('fr', 0x160, 'Command'), ('rser', 0x164, 'ReadWrite'),
        ('spndst', 0x168, 'Read'), ('sptrclr', 0x16c, 'Command'),
        ('sfa1ad', 0x180, 'Write'), ('sfa2ad', 0x184, 'Write'),
        ('sfb1ad', 0x188, 'Write'), ('sfb2ad', 0x18c, 'Write'), ('dlpr', 0x190, 'Read'),
        ('lutkey', 0x300, 'Write'), ('lckcr', 0x304, 'Write'),
    ]
    qfields = {
        'mcr': [('isd', 16, 4), ('mdis', 14, 1), ('clr_txf', 11, 1), ('clr_rxf', 10, 1), ('ddr_en', 7, 1), ('end_cfg', 2, 2), ('swrsthd', 1, 1), ('swrstsd', 0, 1)],
        'buf3cr': [('allmst', 31, 1), ('adatsz', 8, 8)],
        'smpr': [('ddrsmp', 16, 3), ('fsdly', 6, 1), ('fsphs', 5, 1), ('hsena', 0, 1)],
        'rbct': [('wmrk', 0, 5), ('rxbrd', 8, 1)],
        'sr': [('busy', 0, 1), ('ip_acc', 1, 1), ('ahb_acc', 2, 1), ('txfull', 27, 1)],
        'fr': [(name, bit, 1) for name, bit in [('tff', 0), ('ipgef', 4), ('ipief', 6), ('ipaef', 7), ('iuef', 11), ('abof', 12), ('aibsef', 13), ('aitef', 14), ('absef', 15), ('rbdf', 16), ('rbof', 17), ('illine', 23), ('tbuf', 26), ('tbff', 27)]],
        'rser': [(name, bit, 1) for name, bit in [('tfie', 0), ('ipgeie', 4), ('ipieie', 6), ('ipaeie', 7), ('iueie', 11), ('aboie', 12), ('aibsie', 13), ('aitie', 14), ('abseie', 15), ('rbdie', 16), ('rboie', 17), ('rbdde', 21), ('illinie', 23), ('tbfde', 25), ('tbuie', 26), ('tbfie', 27)]],
        'sptrclr': [('ipptrc', 8, 1), ('bfptrc', 0, 1)],
        'lckcr': [('lock', 0, 1), ('unlock', 1, 1)],
    }
    facts('Qspi', QSPI_C, 73, [(n, o, a, qfields.get(n, []), None) for n, o, a in qspi] + [
        ('rbdr', 0x200, 'Read', [], {'len': 32, 'stride': 4}),
        ('lut', 0x310, 'Write', [('operand0', 0, 8), ('pad0', 8, 2), ('instruction0', 10, 6), ('operand1', 16, 8), ('pad1', 24, 2), ('instruction1', 26, 6)], {'len': 8, 'stride': 4}),
    ])
    b.correction(QSPI_C, 73, 'TCR/FLSHCR/RBSR/SPNDST/DLPR are declared but have no access semantics in this driver; expose raw read-only views without guessing writes.', f'Linux {LINUX_PIN} {QSPI_C}:73-199')
    b.correction(QSPI_C, 197, 'LUT array covers the two C-used sequences (0 and 1), four words per sequence; total hardware LUT sequence count is unspecified. RBDR has 128-byte buffer / four-byte words.', f'Linux {LINUX_PIN} {QSPI_C}:62,197-207,630-648')
    b.instance('QSPI', 'Qspi', 0xd420c000, 'ap', MAP, 127)

    # C supplies GPIO interrupt masks absent from the chapter's tables.
    for name, offset in [('gapmask', 0x9c), ('gcpmask', 0xa8)]:
        ref = b.register('GpioBank', name, offset, 'ReadWrite', c_fields([('ports', 0, 32)]),
            f'ESOS {ESOS_PIN} bsp/spacemit/drivers/gpio/spacemit-gpio.c:45. Interrupt mask; 32-bit read/write.')
        b.table('bsp/spacemit/drivers/gpio/spacemit-gpio.c', 45 if name == 'gapmask' else 46, 'generated', [ref])
        item = copy.deepcopy(b.ir['block/k1_io::GpioBank']['items'][-1])
        item['array'] = {'offsets': [0, 4, 8, 0x100]}
        b.ir['block/k1_io::Gpio']['items'].append(item)
    # C RCPU PWM variant has only CR/DCR/PCR; no output counter asserted.
    pwm = copy.deepcopy(b.ir['block/k1_io::Pwm'])
    pwm['items'] = [item for item in pwm['items'] if item['byte_offset'] < 0x10]
    b.ir['block/k1_io::RcpuPwm'] = pwm
    b.table('bsp/spacemit/drivers/pwm/pwm-pxa.c', 13, 'generated', ['k1_io::RcpuPwm.' + item['name'] for item in pwm['items']])
    b.add_array_block('RcpuPwmArray', 'RcpuPwm', 'channel', 0, {'len': 10, 'stride': 0x100})
    b.instance('SHUB_PWM_CHANNELS', 'RcpuPwmArray', 0xc0888100, 'rcpu', ESOS_DTS, 164)
    for i in range(10):
        b.instance(f'SHUB_PWM{i}', 'RcpuPwm', 0xc0888100 + i * 0x100, 'rcpu', ESOS_DTS, 166 + i * 9)
    b.correction(PREFIX + CHAPTERS[2], 3103, 'RCPU PWM uses ten 0x100-spaced channels starting at C0888100, not the AP 0x400 stride; C supplies only three registers.', f'ESOS {ESOS_PIN} {ESOS_DTS}:164-253; bsp/spacemit/drivers/pwm/pwm-pxa.c:13-15')

    # PCIe wrapper: different address windows remain distinct blocks.
    facts('PcieConfig', PCIE_C, 81, [
        ('device_cmd', 0, 'ReadWrite', [('ltssm_en', 6, 1), ('perst_in', 7, 1), ('aux_pwr_det', 9, 1), ('rc_perst', 12, 1), ('ep_wake', 13, 1), ('hold_phy_rst', 30, 1), ('device_type_rc', 31, 1)], None),
        ('ctrl_logic', 4, 'ReadWrite', [('ignore_perstn', 2, 1)], None),
    ])
    facts('PciePhyAhb', PCIE_C, 40, [
        ('irq_en', 0, 'ReadWrite', [('irq_en', 0, 1), ('pme_turn_off', 5, 1)], None),
        ('link_sts', 4, 'Read', [('smlh_link_up', 1, 1), ('ltssm', 6, 6), ('rdlh_link_up', 12, 1)], None),
        ('irqstatus_intx', 8, 'Command', [('intx', 6, 4)], None),
        ('irqenable_set_intx', 0xc, 'ReadWrite', [('intx', 6, 4)], None),
        ('irqstatus_msi', 0x10, 'Read', [('edma_write', 0, 8), ('edma_read', 8, 8), ('remote_interrupt', 31, 1)], None),
        ('irqenable_set_msi', 0x14, 'ReadWrite', [('edma_write', 0, 8), ('edma_read', 8, 8), ('remote_interrupt', 31, 1)], None),
        ('intr_status1', 0x18, 'Command', [('msi', 0, 1), ('msix', 1, 8)], None),
        ('intr_enable1', 0x1c, 'ReadWrite', [('msi', 0, 1), ('msix', 1, 8)], None),
        ('msi_recv_ctrl', 0x80, 'ReadWrite', [('msi_mon_en', 0, 1), ('msix_mon_en', 1, 8), ('msix_afifo_empty', 29, 1), ('msix_afifo_full', 30, 1)], None),
        ('msi_recv_addr0', 0x84, 'Write', [], None), ('msix_mon_mask', 0x88, 'Read', [], None),
        ('msix_mon_base0', 0x8c, 'Write', [], None),
        ('mon_fifo_data0', 0xb0, 'Read', [], None), ('mon_fifo_data1', 0xb4, 'Read', [('int_vec', 0, 8)], None),
    ])
    b.correction(PCIE_C, 51, 'IRQENABLE_SET_* are read/write enables in actual C RMW and disable paths; do not infer W1S from SET spelling. MSI bit11 overlaps EDMA_INT11, so expose the unambiguous EDMA range only.', f'Linux {LINUX_PIN} {PCIE_C}:55-77,899-926')
    for i, letter in enumerate('ABC'):
        b.instance('PCIE_' + letter + '_CONFIG', 'PcieConfig', 0xd4282bcc + i * 8, 'ap', DTS_C, 2269 + i * 49)
        b.instance('PCIE_' + letter + '_PHY_AHB', 'PciePhyAhb', 0xc0b20000 + i * 0x100000, 'ap', DTS_C, 2270 + i * 49)
    b.table(PCIE_C, 125, 'source_gap', note='INTX_ASSERT/DEASSERT offsets 0x124/0x128 and ELBI EP offsets 0x18/0x28/0x2c have no unambiguous mapped base/access in the reviewed RC DTS. The config resource is eight bytes; do not attach these macros to a fictitious large config window or invent an ELBI base.')

    # USB2 C structs define native u32 layout; operational base is dynamic
    # CAPLENGTH, so there is no invented fixed operational instance address.
    facts('Usb2Capability', USB_C, 138, [
        ('caplength_hciversion', 0, 'Read', [('caplength', 0, 8)], None),
        ('hcsparams', 4, 'Read', [('ppc', 4, 1)], None), ('hccparams', 8, 'Read', [], None),
        ('dciversion', 0x20, 'Read', [], None), ('dccparams', 0x24, 'Read', [('den', 0, 5)], None),
    ])
    usbops = [
        ('usbcmd', 0, 'Command', [('run_stop', 0, 1), ('ctrl_reset', 1, 1), ('setup_tripwire', 13, 1), ('atdtw_tripwire', 14, 1)]),
        ('usbsts', 4, 'Command', [('interrupts', 0, 9), ('hc_halted', 12, 1), ('reclamation', 13, 1), ('periodic_schedule', 14, 1), ('async_schedule', 15, 1)]),
        ('usbintr', 8, 'ReadWrite', [('int_en', 0, 1), ('err_en', 1, 1), ('port_change_en', 2, 1), ('sys_err_en', 4, 1), ('iaa_en', 5, 1), ('reset_en', 6, 1), ('sof_en', 7, 1), ('suspend_en', 8, 1)]),
        ('frindex', 0xc, 'Read', [('index', 0, 14)]),
        ('deviceaddr', 0x14, 'ReadWrite', [('advance', 24, 1), ('address', 25, 7)]),
        ('eplistaddr', 0x18, 'ReadWrite', [('address', 11, 21)]),
        ('ttctrl', 0x1c, 'Read', []), ('burstsize', 0x20, 'Read', []),
        ('txfilltuning', 0x24, 'Read', []), ('epnak', 0x38, 'Read', []),
        ('epnaken', 0x3c, 'Read', []), ('configflag', 0x40, 'Read', []),
        ('otgsc', 0x64, 'Read', []), ('usbmode', 0x68, 'ReadWrite', [('controller_mode', 0, 2), ('setup_lock_off', 3, 1), ('stream_disable', 4, 1)]),
        ('epsetupstat', 0x6c, 'Command', []), ('epprime', 0x70, 'Command', []),
        ('epflush', 0x74, 'Command', []), ('epstatus', 0x78, 'Read', []),
        ('epcomplete', 0x7c, 'Command', []),
    ]
    facts('Usb2Operational', USB_C, 147, [(n, o, a, f, None) for n, o, a, f in usbops] + [
        ('portsc', 0x44, 'Read', [('change_w1c', 1, 1), ('enable_change_w1c', 3, 1), ('overcurrent_change_w1c', 5, 1), ('force_resume', 6, 1), ('suspend', 7, 1), ('reset', 8, 1), ('power', 12, 1), ('force_full_speed', 24, 1), ('speed', 26, 2), ('xcvr_select', 30, 2)], {'len': 8, 'stride': 4}),
        ('epctrlx', 0x80, 'Read', [('rx_stall', 0, 1), ('rx_type', 2, 2), ('rx_toggle_inhibit', 5, 1), ('rx_toggle_reset', 6, 1), ('rx_enable', 7, 1), ('tx_stall', 16, 1), ('tx_type', 18, 2), ('tx_toggle_reset', 22, 1), ('tx_enable', 23, 1)], {'len': 16, 'stride': 4}),
    ])
    # Array aliases are unsupported by the base validator: use explicit indexed
    # write views at the eight documented struct slots, one exact alias per slot.
    items = b.ir['block/k1_io::Usb2Operational']['items']
    port = next(item for item in items if item['name'] == 'portsc')
    items.remove(port)
    port_refs = []
    for i in range(8):
        port_fields = b.ir['fieldset/' + port['fieldset']]['fields']
        port_refs += b.split('Usb2Operational', f'portsc{i}', 0x44 + i * 4,
            port_fields, 'Mixed port configuration and W1C status; compose writes with clear bits zero unless acknowledging.',
            write_fields=[field for field in port_fields if field['name'] != 'speed'])
    b.table(USB_C, 162, 'generated', port_refs)
    del b.ir['fieldset/' + port['fieldset']]
    ep = next(item for item in items if item['name'] == 'epctrlx')
    items.remove(ep)
    ep_refs = []
    for i in range(16):
        ep_refs += b.split('Usb2Operational', f'epctrl{i}', 0x80 + i * 4,
            b.ir['fieldset/' + ep['fieldset']]['fields'], 'Endpoint configuration and toggle-reset commands; explicit writes only.')
    b.table(USB_C, 170, 'generated', ep_refs)
    del b.ir['fieldset/' + ep['fieldset']]
    # Update the struct table reference to the actual emitted slot names.
    for entry in b.manifest['tables']:
        if entry['source'] == USB_C and entry['line'] == 147:
            entry['registers'].remove('k1_io::Usb2Operational.portsc')
            entry['registers'].remove('k1_io::Usb2Operational.epctrlx')
    facts('Usb2Phy', PHY_C, 9, [(name, offset, access, fields, None) for name, offset, access, fields in [
        ('reg01', 4, 'Command', [('pll_ready', 0, 1)]),
        ('reg04', 0x10, 'ReadWrite', [('en_hstsof', 0, 1), ('auto_clear_dis', 2, 1)]),
        ('reg06', 0x18, 'ReadWrite', [('hs_src_sel', 0, 1)]),
        ('reg08', 0x20, 'Read', [('discon_det', 9, 1)]), ('reg0d', 0x34, 'Write', []),
        ('reg40', 0x40, 'Command', [('clr_disc', 0, 1)]),
        ('reg22', 0x88, 'Read', [('force_cdrclk', 6, 1)]), ('reg26', 0x98, 'Read', []),
        ('analog14_13', 0xa4, 'ReadWrite', [('hsdac_isel', 0, 4), ('hsdac_ireg_en', 4, 1)]),
    ]])
    for i in range(2):
        b.instance(f'USB2_{i}_CAPABILITY', 'Usb2Capability', 0xc0900100 + i * 0x80000, 'ap', DTS_C, 2035 + i * 60)
        b.instance(f'USB2_{i}_PHY', 'Usb2Phy', 0xc0940000 + i * 0x80000, 'ap', DTS_C, 2028 + i * 60)
    b.instance('USB3_USB2_PHY', 'Usb2Phy', 0xc0a30000, 'ap', DTS_C, 2157)
    b.correction(USB_C, 147, 'Operational block address is capability base + read CAPLENGTH, not a guessed constant. C struct arrays describe eight port slots and sixteen endpoint slots; implemented counts are runtime capability facts.', f'Linux {LINUX_PIN} drivers/usb/gadget/udc/k1x_udc_core.c:2776-2780')
    b.correction(USB_C, 148, 'Host-only/unused struct members have raw read-only views where this device driver lacks write semantics; port W1C/configuration is explicit-write only.', f'Linux {LINUX_PIN} {USB_C}:147-170; k1x_udc_core.c PORTSCX_W1C_BITS')

    # DDR performance facts available in the pinned driver. MC_CONTROL_0 has
    # observed reads only; do not manufacture timing/training or error-ID offsets.
    facts('DdrControl', DDR_C, 24, [
        ('mc_control_0', 0x44, 'Read', [], None),
        ('perf_conf', 0x100, 'ReadWrite', [(f'evt{i}', 8*i, 7) for i in range(4)] + [(f'en{i}', 8*i+7, 1) for i in range(4)], {'len': 2, 'stride': 4}),
        ('perf_status', 0x108, 'Read', [], None), ('perf_ctrl', 0x10c, 'ReadWrite', [('start_condition', 0, 1), ('stop_on_overflow', 4, 1)], None),
        ('perf_counter', 0x110, 'Read', [], {'len': 8, 'stride': 4}),
        ('isr', 0x140, 'Read', [('decode_err', 2, 1), ('mb_int_ch0', 10, 1), ('ref_exceed_limit_ch0', 12, 1), ('refrt_int_ch0', 14, 1), ('pend_ref_overflow_ch0', 16, 1)], None),
        ('ier', 0x144, 'Read', [], None), ('err_info', 0x150, 'Read', [], None),
        ('err_addr_low', 0x154, 'Read', [], None), ('err_addr_high', 0x158, 'Read', [], None),
        ('refctrl', 0x378, 'Read', [], None), ('rftiming', 0x3fc, 'Read', [], None),
    ])
    b.instance('DDR_CONTROL_PERF', 'DdrControl', 0xc0000000, 'ap', 'drivers/soc/spacemit/ddr-bw/spacemit_ddrbw.c', 1154)
    facts('DdrAxiMonitor', DDR_C, 277, [
        ('control', 0, 'ReadWrite', [('evt_sel', 0, 5), ('latch', 5, 1), ('read', 6, 1), ('enable', 31, 1)], {'len': 4, 'stride': 0x10}),
        ('id_control', 8, 'ReadWrite', [('monitor_id', 0, 3), ('clock_disable', 16, 3), ('enable', 24, 3)], {'len': 4, 'stride': 0x10}),
        ('data', 0xc, 'Read', [], {'len': 4, 'stride': 0x10}),
        ('id_select', 0x44, 'ReadWrite', [], {'offsets': [m * 0x10 + n * 4 for m in range(4) for n in range(3)]}),
    ])
    b.instance('DDR_AXI_MONITOR', 'DdrAxiMonitor', 0xc0058500, 'ap', DTS_C, 1818)
    b.correction(DDR_C, 291, 'ID selection array uses only C-written IDs 1..3 in each port. ID4 is allocated but skipped before enable, so unknown slots are not invented.', f'Linux {LINUX_PIN} drivers/soc/spacemit/ddr-bw/spacemit_ddrbw.c:356-379')
    b.table(DDR_C, 42, 'source_gap', note='DDRC_ERR_ID is defined as 0x15 (misaligned for readl) and unused; no corroborated offset exists. Do not invent 0x15c.')
    b.correction(DDR_C, 101, 'Eight declared performance counters retained; Linux allocates only seven. Declared unused DDR registers remain conservative raw read-only views without reset/access inventions.', f'Linux {LINUX_PIN} {DDR_C}:47-48,86-101')


def build(root, esos_root=DEFAULT_ESOS_ROOT, linux_root=None):
    builder = Builder(root)
    builder.import_docs()
    supplement(builder)
    for kind, path, digest in C_FINGERPRINTS.values():
        cache = esos_root if kind == 'esos' else linux_root
        if cache is not None:
            actual = hashlib.sha256((cache / path).read_bytes()).hexdigest()
            if actual != digest:
                raise ValueError(f'{kind} fingerprint mismatch: {path}')
        builder.manifest['sources'].append({'path': path, 'sha256': digest, 'kind': kind})
    return builder.finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--esos-root', type=Path, default=DEFAULT_ESOS_ROOT)
    parser.add_argument('--linux-root', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    ir, manifest = build(args.source_root, args.esos_root, args.linux_root)
    for relative, value in [('data/registers/expanded_k1_io.yaml', ir), ('provenance/expanded/k1_io.json', manifest)]:
        path = args.repo_root / relative
        rendered = json.dumps(value, indent=2) + '\n'
        if args.check:
            if path.read_text() != rendered:
                raise SystemExit(f'stale: {relative}')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered)
    print(f"K1 IO: {len(manifest['tables'])} dispositions, {sum(k.startswith('block/') for k in ir)} blocks, "
          f"{sum(len(v.get('items', [])) for v in ir.values())} items, "
          f"{sum(len(v.get('fields', [])) for v in ir.values())} fields, {len(manifest['instances'])} instances")


if __name__ == '__main__':
    main()
