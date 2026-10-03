#!/usr/bin/env python3
"""Import K3 audio/connectivity numeric facts from pinned public Markdown and ESOS.

Descriptions are original compact fact annotations. Hardware resets are source facts,
not chiptool Default values. No generated PAC is edited by this importer.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import manual_tables as mt

DOC_ROOT = Path('/tmp/spacemit-research/docs/en/key_stone')
ESOS_ROOT = Path('/tmp/spacemit-research/esos-full')
UBOOT_ROOT = Path('/tmp/k3-io-c/vendor')
UBOOT_FACT_SOURCES = {
    'arch/riscv/dts/k3.dtsi': '166b147eb230c8c60717001e0f19bbdd8160505f9b4f345b62d7cbb40674e6a8',
    'drivers/pci/pcie_dw_common.h': '1d6d5cea190649c7f09b924870507c4dcc3d83b800322612f76f7cc0c35d62d4',
    'drivers/pci/pcie_dw_spacemit.h': '4719d9ad3cb4d3f5bbbaeaac81bb88aa2aee03609794cbe260962c5b1b672af8',
    'drivers/pci/pcie_dw_spacemit.c': '2195472d1e992637101b61e51be63a39abbd5054a2f7287ec40c7b853dee9106',
    'include/usb/xhci.h': 'eba40815147b8d946845f527a7edbd37c61b8a5dca30ca651ce59068123e3f5b',
    'drivers/usb/host/xhci-dwc3.c': '1d49f9708deed567f8d762f277043c61363e75f11ca07fa310c751ccb7e4ad23',
}
BASE = 'k3/k3_docs/k3_usermanual/'
CBASE = 'bsp/spacemit/'
UBOOT = 'https://github.com/spacemit-com/uboot-2022.10/blob/28805afe074715a0ede143d42999ea06bee58fbd/'
UART_NAMES = ['rbr', 'thr', 'dll', 'dlh', 'ier', 'iir', 'fcr', 'lcr', 'mcr', 'lsr', 'msr', 'scr', 'isr', 'for_', 'abr', 'acr', 'baud', 'fcr_new', 'baud_newreg_en']
BLOCKS = {'13_audio.md': 'Sspa', 'ir_rx.md': 'Ir', 'spi.md': 'Spi', 'uart.md': 'Uart', 'i2c.md': 'I2c', 'espi.md': 'Espi', 'ethernet_gmac.md': 'GmacPlatform', 'usb.md': 'UsbPmu'}

def qualified(block):
    return 'k3_io::' + block

def all_tables(path):
    """Include base/interrupt/protocol tables in accounting, not just bit tables."""
    lines = path.read_text().splitlines()
    result = []
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith('|'):
            i += 1
            continue
        start = i
        rows = []
        while i < len(lines) and lines[i].lstrip().startswith('|'):
            rows.append(mt.cells(lines[i])); i += 1
        if len(rows) >= 2:
            result.append((start + 1, rows))
    return result

class Importer:
    def __init__(self, root, esos, uboot=UBOOT_ROOT):
        self.root, self.esos, self.uboot = root, esos, uboot
        self.ir: dict[str, Any] = {}
        self.manifest: dict[str, Any] = dict(schema=1, chip='k3', registers=['expanded_k3_io'], sources=[], instances=[], aliases={}, write_mask_registers=[], tables=[], corrections=[])
        self.by_table = {}
        self.source_paths = set()

    def source(self, path, kind='docs'):
        key = (kind, path)
        if key not in self.source_paths:
            self.source_paths.add(key)
            root = {'docs':self.root, 'esos':self.esos, 'uboot':self.uboot}[kind]
            digest = hashlib.sha256((root / path).read_bytes()).hexdigest()
            if kind=='uboot' and digest!=UBOOT_FACT_SOURCES[path]:
                raise ValueError('changed pinned declarative U-Boot fact source: '+path)
            self.manifest['sources'].append(dict(path=path, kind=kind, sha256=digest))

    def correction(self, source, line, decision, evidence):
        self.manifest['corrections'].append(dict(source=source, line=line, decision=decision, evidence=evidence))

    def instance(self, name, block, address, view, path, line):
        self.manifest['instances'].append(dict(name=view.upper()+'_K3_IO_'+name, block=qualified(block), address=address, view=view, source=dict(path=path, line=line)))

    def add(self, block, name, offset, access, fields=None, description=None, array=None, split=False):
        q = qualified(block)
        b = self.ir.setdefault('block/'+q, dict(description='K3 '+block+' numeric MMIO facts.', items=[]))
        reg: dict[str, Any] = dict(name=name, byte_offset=offset, access=access, bit_size=32)
        if description:
            reg['description'] = description
        if array:
            reg['array'] = array
        if fields:
            fs = 'k3_io::regs::'+block+''.join(w.capitalize() for w in name.split('_'))
            self.ir['fieldset/'+fs] = dict(bit_size=32, fields=fields)
            reg['fieldset'] = fs
        if split:
            reg['name'] = name+'_read'; reg['access'] = 'Read'
            b['items'].append(reg)
            wr = dict(reg, name=name+'_write', access='Write')
            if fields:
                writable = [f for f in fields if not f.get('description', '').startswith(('Source access RO;', 'Source access R;'))]
                if writable and writable != fields:
                    write_fs = reg['fieldset']+'Write'
                    self.ir['fieldset/'+write_fs] = dict(bit_size=32, fields=writable)
                    wr['fieldset'] = write_fs
            b['items'].append(wr)
            self.manifest['aliases'].setdefault(q, []).append([reg['name'], wr['name']])
            self.manifest['write_mask_registers'].append(q+'.'+wr['name'])
            return [q+'.'+reg['name'], q+'.'+wr['name']]
        b['items'].append(reg)
        if access == 'Write':
            self.manifest['write_mask_registers'].append(q+'.'+name)
        return [q+'.'+name]

    def parse_fields(self, table, raw_rows):
        result, types, occupied, side_effect = [], set(), 0, False
        for row in table['rows']:
            bits, name, access, reset = row['values'][:4]
            if not name or re.search(r'reserv|rsvd|unused', name, re.I):
                continue
            low, width = mt.bit_range(bits)
            if low+width > 32:
                raise ValueError(f"field outside native word at {table['source']}:{row['line']}")
            mask = ((1 << width)-1) << low
            if mask & occupied:
                raise ValueError(f"overlapping field at {table['source']}:{row['line']}")
            occupied |= mask
            n = mt.identifier(name)
            if any(f['name'] == n for f in result):
                n += '_bit'+str(low)
                self.correction(table['source'], row['line'], 'Disambiguate repeated field label by its documented bit position.', 'Same field table.')
            # Preserve access/reset facts without importing explanations.
            desc = 'Source access '+access+'; reset '+reset+'.'
            result.append(dict(name=n, bit_offset=low, bit_size=width, description=desc))
            a = access.upper().replace('/', '').replace(' ', '')
            types.add(a)
            raw = raw_rows.get(row['line'], '')
            if a in {'RW1C', 'RCW1C', 'W1C', 'RW1S', 'RWC'} or re.search(r'self.clear|automatically clear|cleared automatically|writing 1|write 1|set to 1 to start|flushes|read.clear|Write 1 = Capture|cleared by writing', raw, re.I):
                side_effect = True
        if not result:
            raise ValueError('empty named field table '+table['id'])
        valid = {'RO', 'R', 'WO', 'W', 'RW', 'RW1C', 'RCW1C', 'W1C', 'RW1S', 'RWC'}
        if types - valid:
            raise ValueError('unknown field access '+str(types-valid)+' '+table['id'])
        access = 'Read' if types <= {'R', 'RO'} else 'Write' if types <= {'W', 'WO'} else 'ReadWrite'
        return result, access, side_effect

    def docs(self):
        paths = [self.root/BASE/'13_audio.md', *sorted((self.root/BASE/'14_connectivity').glob('*.md'))]
        for path in paths:
            relative = path.relative_to(self.root).as_posix()
            self.source(relative)
            if path.name=='index.md':
                self.correction(relative,5,'Chapter index only; linked connectivity chapters are individually accounted.', 'Pinned chapter navigation links; no register layout in this file.')
            lines = path.read_text().splitlines()
            raw = {i+1: l for i,l in enumerate(lines)}
            uart_index = 0
            for t in mt.extract(path, self.root):
                if t['kind'] != 'fields':
                    continue
                block = BLOCKS[path.name]
                heading = t['heading'][-1]
                if path.name == 'espi.md':
                    match = re.fullmatch(r'(\w+)\((0x[0-9a-fA-F]+)\)', heading)
                    if not match:
                        raise ValueError('eSPI heading '+heading)
                    name, offset = match[1].lower().replace('rx_msg', 'rxmsg'), int(match[2],16)
                elif path.name == 'ethernet_gmac.md':
                    name = 'ctrl' if 'CTRL' in heading else 'dline'
                    offset = 0 if name == 'ctrl' else 4
                elif path.name == 'i2c.md' and t['line'] == 1254:
                    name, offset = 'idbr', 12
                    self.correction(relative, t['line'], 'Supply omitted IDBR offset 0x0c.', CBASE+'drivers/i2c/i2c-k1.h: REG_DBR = 0xc')
                else:
                    labels = [l for l in t['offset_labels'] if re.match(r'^Offset\s*:', l['value'], re.I)]
                    if not labels:
                        raise ValueError('missing offset '+t['id'])
                    value = labels[-1]['value'].split(':',1)[1].strip()
                    offset = mt.integer(value.split('/')[0])
                    if path.name == '13_audio.md':
                        # The short symbol line directly preceding Offset is authoritative.
                        symbols = [l.strip() for l in lines[max(0,labels[-1]['line']-6):labels[-1]['line']-1] if re.fullmatch(r'[A-Z][A-Z0-9_]+',l.strip())]
                        if not symbols:
                            raise ValueError('missing audio symbol '+t['id'])
                        name = mt.identifier(symbols[-1])
                        if not name:
                            raise ValueError('audio symbol')
                        if t['line'] > 290:
                            block = 'Adma'
                    elif path.name == 'uart.md':
                        name = UART_NAMES[uart_index]; uart_index += 1
                    else:
                        name = mt.identifier(re.sub(r'\s+REGISTER$', '', heading, flags=re.I))
                    if path.name == 'usb.md':
                        if name == 'pmu_usb3_wake_clr_a_b_c_d': name = 'pmu_usb3_wake_clr'
                        if t['line'] in {267,277}: block = 'UsbUtmi' if t['line']==267 else 'UsbUtmiPortA'
                        elif t['line'] in {301,313}: block = 'PhyCalibration'
                        elif t['line'] in {323,332}: block = 'UsbComboPhy'
                fields, access, side = self.parse_fields(t,raw)
                description = 'Source '+relative+':'+str(t['line'])+'.'
                array = dict(len=4, stride=4) if path.name=='usb.md' and t['line']==154 else None
                if path.name == '13_audio.md' and name in {'sspa_rx_data','sspa_tx_data'}:
                    description += ' DMA sample endpoint; not a CPU data access path.'
                if path.name == '13_audio.md' and name == 'sspa_tx_data':
                    access = 'Write'
                    self.correction(relative,t['line'],'TX sample port is a DMA destination; expose write-only, not the contradictory RO type.', relative+':178-182; destination transfer direction.')
                if path.name=='spi.md' and name=='ssdatr':
                    side=True; description += ' FIFO read pops RX; write pushes TX; test mode selects FIFO.'
                if path.name=='i2c.md' and name=='idbr':
                    side=True; description += ' Data buffer direction depends on transfer state.'
                if path.name=='i2c.md' and name=='wfifo':
                    access='Write'
                    for f in fields:
                        f['description'] = f['description'].replace('Source access RO;', 'Corrected access WO;')
                    self.correction(relative,t['line'],'WFIFO is a TX push port, not RO as labelled in its field table.',CBASE+'drivers/i2c/i2c-k1.c:528 writes REG_WFIFO; '+relative+':1343-1345 allows PIO/DMA fill.')
                if path.name=='i2c.md' and name in {'wfifo_wptr','wfifo_rptr','rfifo_wptr','rfifo_rptr'}:
                    side=True; description += ' Write zero flushes FIFO; no RMW.'
                if path.name=='espi.md' and name=='dn_txdata_port':
                    side=True; description += ' TX FIFO direct read/write views; write pushes data.'
                if path.name=='espi.md' and name=='slave0_rxvw':
                    description += ' Reading clears SYS_EVT_STS in SLAVE0_RXVW_STS.'
                if path.name=='ir_rx.md' and name=='idle_state':
                    side=True; description += ' Hardware clears; software sets idle.'
                if path.name=='uart.md':
                    if name in {'rbr','thr','dll','dlh','ier'}: description += ' Select DLAB view before access.'
                    if name in {'lsr','msr'}: description += ' Reading clears some status indications.'
                    if name in {'fcr','fcr_new'}: side=True; description += ' FIFO reset commands; no read-modify-write.'
                # A source-labelled read/write field with command or read-clear semantics
                # is exposed as exact RO/WO aliases, including mixed control/status words.
                split = access=='ReadWrite' and side
                if split:
                    description += ' Direct write only: preserve ordinary configuration explicitly and exclude unrelated clear/command bits.'
                refs = self.add(block,name,offset,access,fields,description,array,split)
                self.by_table[(relative,t['line'])] = refs
            for line, rows in all_tables(path):
                refs = self.by_table.get((relative,line), [])
                status = 'generated' if refs else 'not_mmio'
                note = None
                if not refs:
                    if path.name=='espi.md' and line==319:
                        status='duplicate'; refs=[r for (p,_),rs in self.by_table.items() if p==relative for r in rs]
                        note='Register summary duplicates all 28 detailed register layouts.'
                    elif (path.name=='spi.md' and line==305) or (path.name=='i2c.md' and line==1087) or (path.name=='13_audio.md' and line in {42,81,281}) or (path.name=='usb.md' and line in {255,287}):
                        note='Numeric peripheral base metadata; layouts are generated from subsequent field tables.'
                    elif path.name=='pcie.md':
                        note='Controller capability/configuration matrix, not an MMIO register layout; fixed vendor C registers supplemented separately.'
                    else:
                        note='Protocol, signal, interrupt, or programming-step table; not an MMIO register layout.'
                record=dict(source=relative,line=line,status=status,registers=refs)
                if note: record['note']=note
                self.manifest['tables'].append(record)
        # Exact source omissions are factual dispositions, not fabricated registers.
        for source,line,gap in [
            ('14_connectivity/can_fd.md',5,'No register layout tables in CAN chapter. ESOS CAN_Type and masks provide the available numeric layout; C declares 64 classic mailboxes, while manual advertises 128. Additional mailbox/FD RAM organization is not established.'),
            ('14_connectivity/usb.md',356,'Per-chip xHCI operational/runtime/doorbell bases and implemented port/interrupter counts are capability-dependent, not fixed numeric facts. Standard host layouts are supplemented from pinned vendor C; DWC3 device-mode registers and further extended capabilities are not documented in this chapter.'),
            ('13_audio.md',260,'DP/eDP audio section supplies features only; no audio register offsets or fields.'),
            ('14_connectivity/pcie.md',5,'No full PCIe DBI/DMA register layouts in manual. Pinned vendor C supplies fixed app, link, DBI, and outbound iATU facts; remaining configuration capabilities and DMA register organization not specified.'),
            ('14_connectivity/ethernet_gmac.md',128,'Manual describes only platform control/delay registers. ESOS eqos structs/masks supply known core registers; undocumented full MAC/MTL/DMA windows remain unpopulated.'),
        ]:
            self.correction(BASE+source,line,'Source gap: '+gap,'Pinned chapter inventory; all actual Markdown tables are separately accounted.')

    def instances(self):
        relative=BASE+'06_address_map.md'; self.source(relative)
        interrupt_path=BASE+'07_interrupts.md'
        self.source(interrupt_path)
        for line,_ in all_tables(self.root/interrupt_path):
            self.manifest['tables'].append(dict(source=interrupt_path,line=line,status='not_mmio',registers=[],note='Numeric interrupt routing assignments; not an MMIO register layout. AP and RCPU interrupt domains are distinct.'))
        for number,line in enumerate((self.root/relative).read_text().splitlines(),1):
            if not line.startswith('|'): continue
            row=mt.cells(line)
            if len(row)<2 or not re.fullmatch(r'0x[0-9a-fA-F_]+',row[1]): continue
            label=row[0]; address=int(row[1].replace('_',''),16)
            block=None; name=None
            view='rcpu' if label.startswith(('R_', 'r_','I2S')) else 'secure' if label.startswith('SEC ') else 'ap'
            if re.fullmatch(r'(SEC )?UART\d+|R_UART\d+',label): block='Uart'; name=label.replace('SEC ','').replace('R_','')
            elif re.match(r'(SEC )?I²C\d|R_I2C\d|R_PWR_I2C',label):
                block='I2c'; name=re.sub(r' \(.*','',label.removeprefix('SEC ').removeprefix('R_').replace('²','2'))
                if name=='PWR_I2C': name='I2C2'
            elif re.fullmatch(r'(SEC )?SSP\d+|R_SSP\d+',label): block='Spi'; name=label.replace('SEC ','').replace('R_','')
            elif re.match(r'SSPA\d',label): block='Spi'; name='I2S'+label[4]
            elif re.match(r'I2S\d[ _]SSPA',label): block='Sspa'; name='RI2S'+label[3]
            elif re.match(r'I2S\d[ _]ADMA',label): block='Adma'; name='ADMA'+label[3]
            elif re.fullmatch(r'IR1?|R_IRC\d',label): block='Ir'; name='IR'+(label[-1] if label[-1].isdigit() else '0')
            elif label=='ESPI (4KB)' or label=='r_espi_apb': block='Espi'; name='ESPI'
            elif re.fullmatch(r'CAN\d|r_can\d',label): block='Can'; name='CAN'+label[-1]
            elif re.match(r'GMAC\d',label): block='Gmac'; name='GMAC'+label[4]
            if block: self.instance(name,block,address,view,relative,number)
        usb=BASE+'14_connectivity/usb.md'
        for i,(name,base) in enumerate([('HOST',0xc0a20000),('A',0xcad20000),('B',0x81500000),('C',0x81800000),('D',0x81b00000)]):
            self.instance('USB_UTMI_'+name,'UsbUtmi',base,'ap',usb,257+i)
        self.instance('USB_UTMI_PORTA','UsbUtmiPortA',0xcad20000,'ap',usb,258)
        for name,base,line in [('A0',0xcad30000,289),('A1',0xcad40000,289),('B',0x81f00000,290),('C',0x82000000,291),('D',0x82100000,292)]:
            self.instance('USB_COMBO_PHY_'+name,'UsbComboPhy',base,'ap',usb,line)
        self.instance('USB_PMU','UsbPmu',0xd4282800,'secure',usb,81)
        self.instance('PHY_CALIBRATION','PhyCalibration',0xd4090000,'ap',usb,297)
        gm=BASE+'14_connectivity/ethernet_gmac.md'
        for n,addr,line,view in [(0,0xd4282be4,133,'ap'),(1,0xd4282bec,136,'ap'),(2,0xd4282a48,139,'ap'),(3,0xc08800e4,142,'rcpu')]:
            self.instance('GMAC'+str(n)+'_PLATFORM','GmacPlatform',addr,view,gm,line)
        self.correction(gm,133,'GMAC and PCIe C configuration addresses lie beyond the address-map AP PMU size 0x100, in its labelled reserved range. Keep main-CPU AP location, without asserting an undocumented security attribute.',BASE+'06_address_map.md:161-164; pinned K3 DT numeric app/ctrl-reg addresses.')
        self.correction(gm,133,'GMAC0/1/2 base label repeats GMAC0 absolute CTRL address; use APMU base 0xd4282800 plus the listed offsets.', UBOOT+'arch/riscv/dts/k3.dtsi; ctrl-reg/dline-reg 0xd4282be4/2be8, 2bec/2bf0, 2a48/2a4c.')
        dts=CBASE+'platform/rt24/k3.dtsi'; self.source(dts,'esos')
        self.instance('GMAC3','Gmac',0x20000000,'rcpu',dts,799)
        self.correction(BASE+'14_connectivity/espi.md',42,'Use chip address map for AP/RCPU eSPI instances, not generic IP default 0x2f848000.',relative+':54 and RCPU r_espi_apb row.')
        self.correction(BASE+'14_connectivity/uart.md',581,'BAUD_SYNC_DONE is write-clear with no clear-value specified; split read and direct-write views without guessing W1C.',BASE+'14_connectivity/uart.md:584; also full divisor address text conflicts with 0x30 field table.')
        self.correction(BASE+'14_connectivity/uart.md',330,'Retain documented DLH bits 7:0; bits 9:8 have no definition.', 'Pinned UART field table; do not infer from divisor formula.')
        self.correction(BASE+'14_connectivity/uart.md',443,'TDRQ is bit 5 and TEMT bit 6; reject contradictory DW_LSR_THRE bit 6 macro.',CBASE+'drivers/uart/pxa_uart.h:30,55-62; LSR_THR_EMPTY = 0x20 and DW_LSR_TEMT = 0x40 agree with manual.')
        self.correction(BASE+'14_connectivity/i2c.md',1397,'RFIFO field table repeats 4-bit CONTROL label despite 8-bit FIFO paragraph; retain known register and mark upper nibble meaning unresolved.',BASE+'14_connectivity/i2c.md:1389 versus 1400; ESOS i2c-k1.c:543-544 copies only byte data.')
        self.correction(BASE+'14_connectivity/espi.md',388,'Retain complete known upstream header fields; do not invent VALID bit 3 over documented HDATA3[7:0].',BASE+'14_connectivity/espi.md:93 versus 388-393 conflicts.')

    def can(self):
        path=CBASE+'drivers/can/drv_can.h'; self.source(path,'esos')
        self.source(CBASE+'drivers/can/fsl_flexcan.h','esos')
        self.source(CBASE+'drivers/can/fsl_flexcan.c','esos')
        text=(self.esos/path).read_text()
        layout=text.split('} CAN_Type;')[0]
        masks={m[1]:int(m[2],16) for m in re.finditer(r'#define\s+CAN_(\w+)_MASK\s+\(0x([0-9a-fA-F]+)U\)',text)}
        refs=[]
        for lineno,line in enumerate(layout.splitlines(),1):
            match=re.search(r'uint32_t\s+(\w+)(?:\[(\d+)\])?;.*?(?:array )?offset: (0x[0-9A-Fa-f]+)',line)
            if not match: continue
            name,count,offset=match[1],match[2],int(match[3],16)
            if name in {'CS','ID','WORD0','WORD1'}:
                count='64'; stride=16
            else: stride=4
            fields=[]
            prefix=name+'_'
            for macro,mask in masks.items():
                if not macro.startswith(prefix): continue
                if macro == 'IFLAG1_BUF4TO0I':
                    self.correction(path,lineno,'Represent BUF4TO0I mask 0x1f by disjoint BUF0I and BUF4TO1I fields; preserve combined mask as a fact.',path+':631-648')
                    continue
                if mask==0: raise ValueError('zero C mask')
                low=(mask&-mask).bit_length()-1; shifted=mask>>low
                if shifted & (shifted+1): raise ValueError('noncontiguous C mask '+macro)
                same = next((f for f in fields if f['bit_offset']==low and f['bit_size']==shifted.bit_length()),None)
                if same:
                    same['description'] = same.get('description','')+' Exact C mask alias '+macro+' = '+hex(mask)+'.'
                    self.correction(path,lineno,'Coalesce exact same-bit C field mask alias '+macro+'.',path+' register masks; mask '+hex(mask))
                else:
                    fields.append(dict(name=mt.identifier(macro[len(prefix):]),bit_offset=low,bit_size=shifted.bit_length()))
            access='Read' if name in {'TIMER','ECR','ESR2','CRCR','RXFIR','FDCRC','HR_TIME_STAMP'} else 'ReadWrite'
            split=name in {'MCR','CS','ESR1','IFLAG1','IFLAG2','ERFSR'}
            desc='ESOS CAN_Type offset/masks; 32-bit word. Reset unspecified.'
            if name in {'CS','ID','WORD0','WORD1'}: desc+=' Classic 64-mailbox view; FD payload allocation is mode-dependent.'
            if name=='CS': desc+=' Reading locks RX mailbox; writes can start transmission. No RMW.'
            if name=='TIMER': desc+=' Reading unlocks RX mailbox.'
            if split: desc+=' Direct-write command/status view; no modify.'
            if name in {'ESR1','IFLAG1','IFLAG2'}: desc+=' W1C interrupt mask.'
            if name=='ERFSR': desc+=' C header supplies no clear-value semantics; not asserted W1C.'
            # Extended members with no access operation in ESOS remain conservatively
            # separate read and direct-write views, not assumed safely modifiable.
            if name in {'GFWR','EPRS','ENCBT','EDCBT','ETDC','ERFCR','ERFIER','ERFFEL'}:
                split=True; desc+=' C layout lacks per-field access; no safe RMW assertion.'
            refs+=self.add('Can',mt.identifier(name),offset,access,fields,desc,dict(len=int(count),stride=stride) if count else None,split)
        self.manifest['tables'].append(dict(source=path,line=22,status='generated',registers=refs,note='C-only CAN_Type layouts and exact CAN_*_MASK definitions supplement missing manual register tables.'))
        self.correction(BASE+'14_connectivity/can_fd.md',27,'Mailbox CS reads lock RX mailboxes and TIMER reads unlock them; use direct CS read/write array aliases, without granting modify.',CBASE+'drivers/can/fsl_flexcan.c:1450,1488-1489; CS writes initiate TX in FLEXCAN_WriteTxMb.')
        self.correction(BASE+'14_connectivity/can_fd.md',27,'Expose only 64 classic mailbox slots declared in ESOS, not guessed extra 64 slots; keep native 32-bit IFLAG1/2 accesses.',path+':41-48; fsl_flexcan.h FLEXCAN_GetMbStatusFlags combines two 32-bit reads.')

    def gmac(self):
        path=CBASE+'drivers/gmac/dwc_eth_qos.h'; self.source(path,'esos')
        self.source(CBASE+'drivers/gmac/dwc_eth_qos.c','esos')
        self.source(CBASE+'drivers/gmac/dwc_eth_qos_spacemit.c','esos')
        text=(self.esos/path).read_text()
        refs=[]
        for m in re.finditer(r'rt_uint32_t\s+(\w+);\s*/\* (0x[0-9a-f]+) \*/',text):
            name,offset=m[1],int(m[2],16)
            if name.startswith('unused'): continue
            prefix='EQOS_'+('MAC_' if offset<0xd00 else 'MTL_' if offset<0x1000 else 'DMA_')+name.upper()+'_'
            fields=[]
            for bit in re.finditer(r'#define\s+'+prefix+r'(\w+)\s+BIT\((\d+)\)',text):
                fields.append(dict(name=mt.identifier(bit[1]),bit_offset=int(bit[2]),bit_size=1))
            for mask in re.finditer(r'#define\s+'+prefix+r'(\w+)_MASK\s+(0x[0-9a-f]+|\d+)\s*\n',text):
                shift=re.search(r'#define\s+'+prefix+mask[1]+r'_SHIFT\s+(\d+)',text)
                if shift:
                    width=int(mask[2],0).bit_length()
                    fields.append(dict(name=mt.identifier(mask[1]),bit_offset=int(shift[1]),bit_size=width))
                elif name == 'mdio_data' and mask[1] == 'GD':
                    if int(mask[2], 0) != 0xffff:
                        raise ValueError('MDIO GD mask changed')
                    fields.append(dict(name='gd', bit_offset=0, bit_size=16))
                    self.correction(path, 108, 'MDIO GD[15:0] follows explicit0xffff mask used directly on the native32-bit read; no redundant SHIFT macro is needed.',
                        path + ':42,108; ' + CBASE + 'drivers/gmac/dwc_eth_qos.c:188-189')
            access='Read' if name.startswith('hw_feature') or name.endswith('debug') else 'ReadWrite'
            split=name in {'mode','mdio_address','txq0_operation_mode'}
            refs+=self.add('Gmac',name,offset,access,fields,'ESOS eqos struct/masks. Reset unspecified.'+(' Command-bearing word: direct writes only.' if split else ''),split=split)
        refs+=self.add('Gmac','version',0x110,'Read',description='ESOS EQOS_VER_OFFSET; reset unspecified.')
        refs+=self.add('Gmac','pmt',0xc0,'ReadWrite',[dict(name='powerdown',bit_offset=1,bit_size=1)],description='ESOS EQOS_PMT. Reset unspecified; power management effects require direct writes.',split=True)
        self.manifest['tables'].append(dict(source=path,line=21,status='generated',registers=refs,note='C-only eqos native-word structs and explicit masks supplement manual platform-only definitions.'))
        self.correction(BASE+'14_connectivity/ethernet_gmac.md',128,'C-only EQOS shift macros without explicit masks remain untyped where width is absent (HW_FEATURE0 flags, MDIO PA/RDA/CR/GOC, CH0_CONTROL DSL); raw registers are retained. HW_FEATURE3 and pad/calibration macros lack corresponding struct offsets.',path+':83-106,178,192-197; no unsupported bit widths or offsets inferred.')

    def pcie(self):
        for path in UBOOT_FACT_SOURCES:
            self.source(path,'uboot')
        # Declarative numeric corrections from pinned vendor U-Boot: no vendor code
        # or descriptions are redistributed. Only source-established fixed registers.
        src=BASE+'14_connectivity/pcie.md'
        def add(block,name,off,access,fields):
            fs=[dict(name=n,bit_offset=b,bit_size=s) for n,b,s in fields]
            return self.add(block,name,off,access,fs,'Pinned U-Boot numeric fact; reset unspecified.')
        refs=[]
        refs+=add('PcieApp','device_cmd',0,'ReadWrite',[('ltssm_en',6,1),('perst_in',7,1),('aux_pwr_det',9,1),('hold_phy_rst',30,1),('device_type_rc',31,1)])
        refs+=add('PcieApp','ctrl_logic',4,'ReadWrite',[('perstn_oe',24,1),('perstn_out',25,1),('ignore_perstn',31,1)])
        refs+=add('PciePhyAhb','link_status',4,'Read',[('smlh_link_up',1,1),('ltssm',6,6),('rdlh_link_up',12,1)])
        for name,off,access,fields in [
            ('link_status',0x80,'Read',[('speed',16,4),('width',20,4)]),
            ('link_capability',0x7c,'ReadWrite',[]),('link_ctl2',0xa0,'ReadWrite',[('target_link_speed',0,4)]),
            ('port_link_control',0x710,'ReadWrite',[('dll_link_en',5,1),('fast_link_mode',7,1),('link_mode',16,6)]),
            ('link_width_speed_control',0x80c,'ReadWrite',[('n_fts',0,8),('link_width',8,5),('speed_change',17,1)]),
            ('gen3_eq_control',0x8a8,'ReadWrite',[]),('misc_control1',0x8bc,'ReadWrite',[('dbi_ro_wr_en',0,1)])]:
            refs+=add('PcieDbi',name,off,access,fields)
        for name,off in [('region_ctrl1',0),('region_ctrl2',4),('lower_base',8),('upper_base',12),('limit',16),('lower_target',20),('upper_target',24)]:
            fs=[dict(name='enable',bit_offset=31,bit_size=1),dict(name='bar_mode_enable',bit_offset=30,bit_size=1)] if off==4 else None
            for block,count in [('PcieOutboundAtu32',32),('PcieOutboundAtu16',16),('PcieOutboundAtu0',1)]:
                refs+=self.add(block,name,off,'ReadWrite',fs,'Outbound iATU; index stride 0x200; count from port capability table (D/E only C-used region 0).',dict(len=count,stride=512))
        self.correction(src,75,'BAR-relative EP unroll offset is not a CPU register base. Use pinned K3 DT app/DBI/ATU/PHY-AHB regions independently.',UBOOT+'drivers/pci/pcie_dw_spacemit.h and pcie_dw_common.h; '+UBOOT+'arch/riscv/dts/k3.dtsi:552-700')
        self.correction(src,65,'Expose A 32 and B/C 16 outbound regions from manual; D/E only C-used region zero. D/E total capacity and inbound register layout are not established.',UBOOT+'drivers/pci/pcie_dw_common.h: PCIE_GET_ATU_OUTB_UNR_REG_OFFSET; manual capability matrix.')
        # DT app/PHY-AHB addresses are distinct, not arithmetic translations.
        for i,(app,phy) in enumerate([(0xd42829f0,0x82900000),(0xd42829d0,0x82c00000),(0xd42829c8,0x82d00000),(0xd42829e0,0x82a00000),(0xd42829e8,0x82b00000)]):
            for block,addr in [('PcieApp',app),('PciePhyAhb',phy),('PcieDbi',0x80000000+0x400000*i),('PcieOutboundAtu32' if i==0 else 'PcieOutboundAtu16' if i<3 else 'PcieOutboundAtu0',0x80300000+0x400000*i)]:
                first_line=[554,584,614,645,675][i]
                tuple_line=first_line+(4 if block=='PcieApp' else 5 if block=='PciePhyAhb' else 2 if block.startswith('PcieOutbound') else 0)
                self.instance('PCIE'+str(i)+'_'+block.upper(),block,addr,'ap','arch/riscv/dts/k3.dtsi',tuple_line)
        self.correction(src,5,'PCIe numeric instance bases supplied by pinned K3 DT reg tuples; app A/B/C/D/E = 0xd42829f0/29d0/29c8/29e0/29e8; PHY-AHB = 0x82900000/82c00000/82d00000/82a00000/82b00000.',UBOOT+'arch/riscv/dts/k3.dtsi:554-560,584-590,614-620,645-651,675-681; DBI and ATU reg tuples in the same rows.')
        local=[r for r in refs if r.startswith(('k3_io::PcieApp.','k3_io::PciePhyAhb.')) or r.endswith('.gen3_eq_control')]
        dbi=[r for r in refs if r.startswith('k3_io::PcieDbi.') and not r.endswith('.gen3_eq_control')]
        atu=[r for r in refs if r.startswith('k3_io::PcieOutbound')]
        self.manifest['tables'].append(dict(source='drivers/pci/pcie_dw_spacemit.h',line=25,status='generated',registers=local,note='Pinned C fixed app/PHY/GEN3_EQ register definitions. K3 DT supplies the separate numeric bases.'))
        self.manifest['tables'].append(dict(source='drivers/pci/pcie_dw_common.h',line=16,status='generated',registers=dbi,note='Pinned C fixed DBI register/mask definitions, not inferred capability-relative offsets.'))
        self.manifest['tables'].append(dict(source='drivers/pci/pcie_dw_common.h',line=29,status='generated',registers=atu,note='Pinned C outbound iATU fixed offsets and index stride; array counts derive from manual port capabilities or C-used region zero.'))

    def xhci(self):
        src=BASE+'14_connectivity/usb.md'
        refs=[]
        def add(block,name,offset,access,fields=(),array=None,split=False):
            fs=[dict(name=n,bit_offset=b,bit_size=s) for n,b,s in fields]
            refs.extend(self.add(block,name,offset,access,fs,'Pinned U-Boot xHCI fact; reset unspecified. Capability-dependent windows require caller-discovered bases and implemented bounds.',array,split))
        for name,offset,fields in [
            ('capbase',0,[('caplength',0,8),('version',16,16)]),
            ('hcsparams1',4,[('max_slots',0,8),('max_interrupters',8,11),('max_ports',24,8)]),
            ('hcsparams2',8,[('ist',0,4),('erst_max',4,4),('scratchpad_hi',21,5),('scratchpad_lo',27,5)]),
            ('hcsparams3',12,[('u1_latency',0,8),('u2_latency',16,16)]),
            ('hccparams',16,[('addr64',0,1),('bandwidth_neg',1,1),('context64',2,1),('ppc',3,1),('indicator',4,1),('light_reset',5,1),('ltc',6,1),('nss',7,1),('max_psa',12,4)]),
            ('dboff',20,[('offset',2,30)]),('rtsoff',24,[('offset',5,27)])]:
            add('XhciCapability',name,offset,'Read',fields)
        add('XhciOperational','usbcmd',0,'ReadWrite',[('reset',1,1),('light_reset',7,1),('save',8,1),('restore',9,1),('pm_index',11,1)],split=True)
        add('XhciOperational','usbsts',4,'ReadWrite',[('fatal',2,1),('event_interrupt',3,1),('port_change',4,1),('save',8,1),('restore',9,1),('save_restore_error',10,1),('host_error',12,1)],split=True)
        add('XhciOperational','pagesize',8,'Read')
        add('XhciOperational','dnctrl',0x14,'ReadWrite',[('notification_enable',0,16)])
        add('XhciOperational','crcr_lo',0x18,'ReadWrite',[('cycle_state',0,1),('pause',1,1),('abort',2,1),('running',3,1),('pointer_low',6,26)],split=True)
        add('XhciOperational','crcr_hi',0x1c,'ReadWrite')
        add('XhciOperational','dcbaap_lo',0x30,'ReadWrite')
        add('XhciOperational','dcbaap_hi',0x34,'ReadWrite')
        add('XhciOperational','config',0x38,'ReadWrite',[('max_devices',0,8)])
        port_fields=[('connect',0,1),('enabled',1,1),('overcurrent',3,1),('reset',4,1),('link_state',5,4),('power',9,1),('speed',10,4),('indicator',14,2),('link_strobe',16,1),('connect_change',17,1),('enable_change',18,1),('warm_reset_change',19,1),('overcurrent_change',20,1),('reset_change',21,1),('link_change',22,1),('config_error_change',23,1),('wake_connect',25,1),('wake_disconnect',26,1),('wake_overcurrent',27,1),('removable',30,1),('warm_reset',31,1)]
        add('XhciPort','portsc',0,'ReadWrite',port_fields,split=True)
        add('XhciPort','portpmsc_usb2',4,'ReadWrite',[('l1_status',0,3),('remote_wake',3,1),('hird',4,4),('l1_device_slot',8,8),('hle',16,1)],split=True)
        add('XhciPort','portpmsc_usb3',4,'ReadWrite',[('u1_timeout',0,8),('u2_timeout',8,8)],split=True)
        add('XhciPort','portli',8,'ReadWrite',split=True)
        self.manifest['aliases'].setdefault(qualified('XhciPort'),[]).append(['portpmsc_usb2_read','portpmsc_usb2_write','portpmsc_usb3_read','portpmsc_usb3_write'])
        self.ir['block/'+qualified('XhciOperational')]['items'].append(dict(name='ports',byte_offset=0x400,block=qualified('XhciPort'),array=dict(len=255,stride=16),description='C structural maximum only. Require index < HCS_MAX_PORTS; not a K3 implemented-port assertion.'))
        add('XhciRuntime','microframe_index',0,'Read')
        for name,offset,access,fields,split in [
            ('irq_pending',0x20,'ReadWrite',[('pending',0,1),('enable',1,1)],True),
            ('irq_control',0x24,'ReadWrite',[('interval',0,16),('counter',16,16)],False),
            ('erst_size',0x28,'ReadWrite',[],False),('erst_base_lo',0x30,'ReadWrite',[],False),('erst_base_hi',0x34,'ReadWrite',[],False),
            ('erst_dequeue_lo',0x38,'ReadWrite',[('desi',0,3),('event_handler_busy',3,1),('pointer_low',4,28)],True),('erst_dequeue_hi',0x3c,'ReadWrite',[],False)]:
            add('XhciRuntime',name,offset,access,fields,dict(len=128,stride=32),split)
        add('XhciDoorbell','doorbell',0,'Write',[('target',0,8),('stream_id',16,16)],dict(len=256,stride=4))
        add('XhciProtocolCapability','revision',0,'Read',[('major',24,8)])
        add('XhciProtocolCapability','name_string',4,'Read')
        add('XhciProtocolCapability','port_info',8,'Read',[('port_offset',0,8),('port_count',8,8)])
        self.manifest['tables'].append(dict(source='include/usb/xhci.h',line=90,status='generated',registers=refs,note='Pinned C standard xHCI capability, operational, port, runtime, doorbell and protocol-capability numeric layouts. Memory descriptors/contexts are not MMIO.'))
        self.correction(src,356,'Use native 32-bit low/high accesses for xHCI pointer words, low before high; do not emit single 64-bit MMIO. Only capability bases are fixed; other windows require discovery. Array bounds 255/128/256 are C structural maxima, not K3 implemented counts.',UBOOT+'include/usb/xhci.h:90-181,396-461,1120-1141; '+UBOOT+'drivers/usb/host/xhci-dwc3.c:178-180 confirms HC_LENGTH discovery; SHA256 eba40815147b8d946845f527a7edbd37c61b8a5dca30ca651ce59068123e3f5b.')
        self.correction(src,356,'Retain raw USBCMD/USBSTS/HCCPARAMS for forwarded XHCI_CMD_*/XHCI_STS_*/XHCI_HCC_EXT_CAPS definitions absent in this header; PORTLI direction is unspecified and uses direct read/write views; PORTSC W1C/W1S/RW masks are not safely modifiable. ER_IRQ_CLEAR helper is explicitly marked incorrect for W1C; expose direct writes.',UBOOT+'include/usb/xhci.h:185-245,45-82,407-414.')
        for name,base,line in [('HOST',0xc0a00000,758),('A',0xcad00000,806),('B',0x81400000,852),('C',0x81700000,899),('D',0x81a00000,946)]:
            self.instance('USB_XHCI_'+name,'XhciCapability',base,'ap','arch/riscv/dts/k3.dtsi',line)
            self.correction(src,356,'USB '+name+' host-mode capability base '+hex(base)+' from pinned K3 DWC3 DT node; not an operational-register base.',UBOOT+'arch/riscv/dts/k3.dtsi:'+str(line))

    def build(self):
        self.docs(); self.can(); self.gmac(); self.pcie(); self.instances(); self.xhci()
        self.manifest['aliases'].setdefault(qualified('Uart'), []).extend([['dll','rbr','thr'], ['dlh','ier'], ['fcr','iir']])
        # Field tables may create direct-write aliases such as IER or DLL. Resolve
        # exact same-offset groups mechanically, retaining all reviewed views.
        for block,groups in self.manifest['aliases'].items():
            items=self.ir['block/'+block]['items']
            for group in groups:
                expanded=[]
                for name in group:
                    expanded.extend(i['name'] for i in items if i['name'] in {name,name+'_read',name+'_write'})
                group[:]=sorted(set(expanded))
            merged=[]
            for group in groups:
                intersect=[g for g in merged if set(g)&set(group)]
                for g in intersect:
                    group=sorted(set(group)|set(g)); merged.remove(g)
                merged.append(group)
            self.manifest['aliases'][block]=merged
        for p in ['drivers/i2c/i2c-k1.h','drivers/i2c/i2c-k1.c','drivers/spi/k1x_spi.h','drivers/uart/pxa_uart.h','platform/rt24/os0_rcpu/_register_defination.h']:
            self.source(CBASE+p,'esos')
        self.correction(BASE+'14_connectivity/espi.md',388,'Auxiliary V1.13 PDF confirms the same unresolved VALID-bit conflict; no layout correction beyond canonical Markdown.', 'K3 PDF SHA256 570d7cf33ec28d1b537057350d462ab8c15fb8044fb3fb86855d0e10c6772c25, p475 sec14.9.4.3 (header) versus sec14.9.2.4 (valid flag).')
        for block in [v for k,v in self.ir.items() if k.startswith('block/')]:

            block['items'].sort(key=lambda r:(r['byte_offset'],r['name']))
        for key in ['sources','instances','tables','corrections']:
            self.manifest[key].sort(key=lambda v:json.dumps(v,sort_keys=True))
        self.manifest['write_mask_registers'].sort()
        return self.ir,self.manifest

def render(root=DOC_ROOT, esos=ESOS_ROOT, uboot=UBOOT_ROOT):
    ir,manifest=Importer(root,esos,uboot).build()
    return {'data/registers/expanded_k3_io.yaml':json.dumps(ir,indent=2,sort_keys=True)+'\n','provenance/expanded/k3_io.json':json.dumps(manifest,indent=2,sort_keys=True)+'\n'}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-root',type=Path,default=DOC_ROOT)
    ap.add_argument('--esos-root',type=Path,default=ESOS_ROOT)
    ap.add_argument('--uboot-root',type=Path,default=UBOOT_ROOT)
    ap.add_argument('--repo-root',type=Path,default=Path(__file__).resolve().parents[2])
    ap.add_argument('--check',action='store_true')
    args=ap.parse_args()
    for name,value in render(args.source_root,args.esos_root,args.uboot_root).items():
        path=args.repo_root/name
        if args.check:
            if not path.exists() or path.read_text()!=value:
                raise SystemExit('stale: '+str(path))
        else:
            path.parent.mkdir(parents=True,exist_ok=True); path.write_text(value)
    print('K3 IO import '+('check passed' if args.check else 'written'))

if __name__=='__main__': main()
