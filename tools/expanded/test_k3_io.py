"""Source-named K3 IO tests; run with PYTHONDONTWRITEBYTECODE=1."""
import copy
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import k3_io as lane


class K3IoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir, cls.manifest = lane.Importer(lane.DOC_ROOT, lane.ESOS_ROOT).build()

    def reg(self, block, name):
        return next(r for r in self.ir['block/'+lane.qualified(block)]['items'] if r['name']==name)

    def fields(self, block, name):
        return self.ir['fieldset/'+self.reg(block,name)['fieldset']]['fields']

    def test_ir_rx_all_nine_offsets_and_w1c(self):
        r = self.ir['block/k3_io::Ir']['items']
        self.assertEqual(sorted(set(i['byte_offset'] for i in r)), list(range(0, 36, 4)))
        self.assertEqual(self.reg('Ir','int_flag_read')['access'], 'Read')
        self.assertEqual(self.reg('Ir','int_flag_write')['access'], 'Write')
        self.assertEqual([(f['bit_offset'],f['bit_size']) for f in self.fields('Ir','fifo_sts')], [(31,1),(30,1),(0,6)])
        instances = {i['name']: i['address'] for i in self.manifest['instances'] if i['block']=='k3_io::Ir'}
        self.assertEqual(set(instances.values()), {0xd4017e00,0xd4017f00,0xc0887000,0xc088e000})

    def test_espi_no_offset_label_all_28_registers(self):
        items=self.ir['block/k3_io::Espi']['items']
        self.assertEqual(len(set(r['byte_offset'] for r in items)),28)
        self.assertEqual(self.reg('Espi','slave0_rxmsg_hdr0')['byte_offset'],0x74)
        self.assertEqual(self.reg('Espi','slave0_s2m_mask')['byte_offset'],0xb8)
        self.assertEqual(self.reg('Espi','up_rxdata_port')['access'],'Read')
        self.assertEqual(self.reg('Espi','slave0_int_sts_write')['access'],'Write')
        self.assertNotIn('valid', [f['name'] for f in self.fields('Espi','up_rxhdr_0')])

    def test_uart_all_dlab_and_newreg_aliases(self):
        self.assertEqual(self.reg('Uart','dll')['byte_offset'],0)
        self.assertEqual(self.reg('Uart','dlh')['byte_offset'],4)
        self.assertIn(['dll','rbr','thr'],self.manifest['aliases']['k3_io::Uart'])
        self.assertEqual(self.reg('Uart','fcr_new_write')['byte_offset'],0x34)
        self.assertEqual(self.reg('Uart','baud_newreg_en_write')['byte_offset'],0x38)
        self.assertEqual([(f['bit_offset'],f['bit_size']) for f in self.fields('Uart','dlh')],[(0,8)])
        lsr={f['name']:f['bit_offset'] for f in self.fields('Uart','lsr')}
        self.assertEqual(lsr['tdrq'],5)
        self.assertEqual(lsr['temt'],6)

    def test_i2c_c_resolved_idbr_fifo_and_write_mask(self):
        self.assertEqual(self.reg('I2c','idbr_write')['byte_offset'],12)
        self.assertEqual(self.reg('I2c','wfifo')['access'],'Write')
        self.assertEqual(self.reg('I2c','rfifo')['access'],'Read')
        self.assertEqual(self.reg('I2c','rfifo_rptr_read')['byte_offset'],0x34)
        writable={f['name'] for f in self.fields('I2c','isr_write')}
        self.assertNotIn('ibb',writable)
        self.assertIn('ald',writable)

    def test_audio_adma_complete_native_words_and_instances(self):
        self.assertEqual(self.reg('Sspa','sspa_tx_data')['access'],'Write')
        self.assertEqual(self.reg('Adma','adma_chan_1_int_status_read')['byte_offset'],0xa4)
        instances={i['name']:i['address'] for i in self.manifest['instances']}
        for n in range(4):
            self.assertEqual(instances['RCPU_K3_IO_RI2S'+str(n)],0xc0883100+0x400*n)
            self.assertEqual(instances['RCPU_K3_IO_ADMA'+str(n)],0xc0883000+0x400*n)
        self.assertEqual(instances['AP_K3_IO_I2S5'],0xd4041800)

    def test_usb3_wake_array_alias_and_port_a_only_layout(self):
        for name in ['pmu_usb3_wake_clr_read','pmu_usb3_wake_clr_write']:
            r=self.reg('UsbPmu',name)
            self.assertEqual(r['array'],dict(len=4,stride=4))
            self.assertEqual(r['byte_offset']+(r['array']['len']-1)*4,0x3d0)
        self.assertEqual(self.reg('UsbUtmiPortA','usb2phyregister05')['byte_offset'],0x14)
        self.assertEqual(len([i for i in self.manifest['instances'] if i['block']=='k3_io::UsbUtmiPortA']),1)

    def test_can_c_arrays_masks_and_compiled_offsets(self):
        self.assertEqual(self.reg('Can','cs_read')['array'],dict(len=64,stride=16))
        self.assertEqual(self.reg('Can','erffel_read')['array'],dict(len=128,stride=4))
        self.assertEqual(self.reg('Can','erffel_read')['byte_offset']+127*4,0x31fc)
        self.assertEqual(self.reg('Can','iflag1_write')['bit_size'],32)
        self.assertEqual(self.reg('Can','iflag2_write')['bit_size'],32)
        header=(lane.ESOS_ROOT/lane.CBASE/'drivers/can/drv_can.h').read_text()
        struct=header[header.index('typedef struct {'):header.index('} CAN_Type;')+len('} CAN_Type;')]
        checks=[]
        for m in re.finditer(r'uint32_t\s+(\w+)(?:\[(\d+)\])?;.*?(?:array )?offset: (0x[0-9A-Fa-f]+)',struct):
            name=m[1]
            member='MB[0].'+name if name in {'CS','ID','WORD0','WORD1'} else name
            checks.append('_Static_assert(offsetof(CAN_Type,'+member+')=='+m[3]+',"'+name+'");')
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'oracle.c'
            source.write_text('#include <stddef.h>\n#include <stdint.h>\n'+struct+'\n'+'\n'.join(checks)+'\nint main(void){return 0;}\n')
            subprocess.run(['cc','-std=c11','-Wall','-Werror',str(source),'-o',str(Path(tmp)/'oracle')],check=True,capture_output=True)
            subprocess.run([str(Path(tmp)/'oracle')],check=True)
        self.assertEqual(len(checks),36)

    def test_xhci_native_split_words_dynamic_bounds_and_host_instances(self):
        self.assertEqual(self.reg('XhciOperational','crcr_lo_write')['byte_offset'],0x18)
        self.assertEqual(self.reg('XhciOperational','crcr_hi')['byte_offset'],0x1c)
        self.assertEqual(self.reg('XhciOperational','dcbaap_lo')['byte_offset'],0x30)
        self.assertEqual(self.reg('XhciOperational','dcbaap_hi')['byte_offset'],0x34)
        self.assertEqual(self.reg('XhciRuntime','erst_dequeue_lo_write')['byte_offset'],0x38)
        self.assertEqual(self.reg('XhciRuntime','erst_dequeue_hi')['byte_offset'],0x3c)
        self.assertEqual(self.reg('XhciRuntime','irq_pending_write')['array'],dict(len=128,stride=32))
        self.assertEqual(self.reg('XhciDoorbell','doorbell')['array'],dict(len=256,stride=4))
        self.assertEqual(self.reg('XhciDoorbell','doorbell')['access'],'Write')
        ports=self.reg('XhciOperational','ports')
        self.assertEqual(ports['byte_offset'],0x400)
        self.assertEqual(ports['array'],dict(len=255,stride=16))
        self.assertEqual(ports['block'],'k3_io::XhciPort')
        instances=[i for i in self.manifest['instances'] if 'Xhci' in i['block']]
        self.assertEqual(set(i['block'] for i in instances),{'k3_io::XhciCapability'})
        self.assertEqual(set(i['address'] for i in instances),{0xc0a00000,0xcad00000,0x81400000,0x81700000,0x81a00000})
        for k,b in self.ir.items():
            if k.startswith('block/k3_io::Xhci'):
                for r in b['items']:
                    if 'bit_size' in r:
                        self.assertEqual(r['bit_size'],32)

    def test_xhci_port_commands_never_expose_modify_views(self):
        for name in ['portsc','portpmsc_usb2','portpmsc_usb3']:
            self.assertEqual(self.reg('XhciPort',name+'_read')['access'],'Read')
            self.assertEqual(self.reg('XhciPort',name+'_write')['access'],'Write')
        groups=self.manifest['aliases']['k3_io::XhciPort']
        self.assertTrue(any(set(g)=={'portpmsc_usb2_read','portpmsc_usb2_write','portpmsc_usb3_read','portpmsc_usb3_write'} for g in groups))

    def test_gmac_platform_bases_and_c_native_offsets(self):
        instances={i['name']:i['address'] for i in self.manifest['instances']}
        self.assertEqual(instances['AP_K3_IO_GMAC2_PLATFORM'],0xd4282a48)
        self.assertEqual(instances['RCPU_K3_IO_GMAC3'],0x20000000)
        self.assertEqual(self.reg('Gmac','ch0_rxdesc_ring_length')['byte_offset'],0x1130)
        self.assertEqual(self.reg('Gmac','version')['byte_offset'],0x110)

    def test_mdio_gd_explicit_mask_without_shift_does_not_guess_shift_only_fields(self):
        item = self.reg('Gmac', 'mdio_data')
        self.assertEqual((item['byte_offset'], item['bit_size'], item['access']), (0x204, 32, 'ReadWrite'))
        self.assertEqual([(f['name'], f['bit_offset'], f['bit_size']) for f in self.fields('Gmac', 'mdio_data')], [('gd', 0, 16)])
        header = (lane.ESOS_ROOT / lane.CBASE / 'drivers/gmac/dwc_eth_qos.h').read_text()
        implementation = (lane.ESOS_ROOT / lane.CBASE / 'drivers/gmac/dwc_eth_qos.c').read_text()
        self.assertRegex(header, r'EQOS_MAC_MDIO_DATA_GD_MASK\s+0xffff')
        self.assertNotIn('EQOS_MAC_MDIO_DATA_GD_SHIFT', header)
        self.assertIn('val &= EQOS_MAC_MDIO_DATA_GD_MASK;', implementation)
        fields = {f['name'] for f in self.fields('Gmac', 'mdio_address_write')}
        self.assertFalse(fields & {'pa', 'rda', 'cr', 'goc'})

    def test_pcie_port_counts_and_separate_app_phy_bases(self):
        self.assertEqual(self.reg('PcieOutboundAtu32','upper_target')['byte_offset']+31*512,0x3e18)
        self.assertEqual(self.reg('PcieOutboundAtu16','upper_target')['byte_offset']+15*512,0x1e18)
        self.assertEqual(self.reg('PcieOutboundAtu0','upper_target')['array']['len'],1)
        instances={i['name']:i['address'] for i in self.manifest['instances']}
        self.assertEqual(instances['AP_K3_IO_PCIE2_PCIEAPP'],0xd42829c8)
        self.assertEqual(instances['AP_K3_IO_PCIE2_PCIEPHYAHB'],0x82d00000)

    def test_all_tables_accounted_and_references_exist(self):
        tables={(t['source'],t['line']) for t in self.manifest['tables']}
        for path in [lane.DOC_ROOT/lane.BASE/'13_audio.md', *sorted((lane.DOC_ROOT/lane.BASE/'14_connectivity').glob('*.md'))]:
            for line,_ in lane.all_tables(path):
                self.assertIn((path.relative_to(lane.DOC_ROOT).as_posix(),line),tables)
        refs={k.removeprefix('block/')+'.'+r['name'] for k,b in self.ir.items() if k.startswith('block/') for r in b['items']}
        for t in self.manifest['tables']:
            self.assertTrue(set(t['registers'])<=refs)
        names=[i['name'] for i in self.manifest['instances']]
        self.assertEqual(len(set(names)),len(names))
        for i in self.manifest['instances']:
            self.assertIn('block/'+i['block'],self.ir)
        for r in self.manifest['write_mask_registers']:
            block,name=r.rsplit('.',1)
            self.assertEqual(next(i for i in self.ir['block/'+block]['items'] if i['name']==name)['access'],'Write')

    def test_malformed_overlap_bits_and_unknown_access_rejected(self):
        path=lane.DOC_ROOT/lane.BASE/'14_connectivity/ir_rx.md'
        table=lane.mt.extract(path,lane.DOC_ROOT)[-1]
        for value in ['1:4','99']:
            bad=copy.deepcopy(table); bad['rows'][1]['values'][0]=value
            with self.assertRaises(ValueError): lane.Importer(lane.DOC_ROOT,lane.ESOS_ROOT).parse_fields(bad,{})
        bad=copy.deepcopy(table); bad['rows'][1]['values'][2]='MAYBE'
        with self.assertRaisesRegex(ValueError,'unknown field access'):
            lane.Importer(lane.DOC_ROOT,lane.ESOS_ROOT).parse_fields(bad,{})
        bad=copy.deepcopy(table); bad['rows'][2]['values'][0]=bad['rows'][1]['values'][0]
        with self.assertRaisesRegex(ValueError,'overlapping field'):
            lane.Importer(lane.DOC_ROOT,lane.ESOS_ROOT).parse_fields(bad,{})

    def test_declarative_uboot_facts_require_pinned_source_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for source in lane.UBOOT_FACT_SOURCES:
                target=root/source
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes((lane.UBOOT_ROOT/source).read_bytes())
            changed=root/'include/usb/xhci.h'
            changed.write_text(changed.read_text()+'\n/* changed input */\n')
            with self.assertRaisesRegex(ValueError,'changed pinned declarative'):
                lane.Importer(lane.DOC_ROOT,lane.ESOS_ROOT,root).build()

    def test_no_synthetic_markdown_table_anchors(self):
        sources={s['path']:s for s in self.manifest['sources']}
        for record in self.manifest['tables']:
            if sources[record['source']]['kind']=='docs':
                actual={line for line,_ in lane.all_tables(lane.DOC_ROOT/record['source'])}
                self.assertIn(record['line'],actual)
        for i in self.manifest['instances']:
            if i['block'].startswith('k3_io::Pcie') or i['block']=='k3_io::XhciCapability':
                self.assertEqual(sources[i['source']['path']]['kind'],'uboot')

    def test_deterministic_check_committed_outputs(self):
        for path,expected in lane.render().items():
            self.assertEqual((Path(__file__).resolve().parents[2]/path).read_text(),expected)


if __name__=='__main__': unittest.main()
