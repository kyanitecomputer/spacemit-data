# Reviewed fact provenance and limitations

Original concise register facts and API policy only. No manual prose is imported.
Source pin: [SpacemiT ESOS](https://github.com/spacemit-com/esos),
`e0b8d955bdc84957da3b7bba8c89e35ee0d27c2b` (`k3-br-v1.0.y`).
Paths below are relative to that tree. C source is marked Apache-2.0;
NMSIS core headers have their own upstream licenses. Generated chiptool support
is Apache-2.0 OR MIT. Vendor facts do not constitute silicon validation. Hardware testing is later,
not a PAC generation prerequisite. [Complete expanded inventory](COVERAGE.md)
covers all public-manual register-bearing tables, repeated K1 datasheet facts,
pinned C supplements, exact count definitions and residual source gaps. The
entries below describe the retained firmware-selected API, not the boundaries
of the expanded register inventory.

| Data | Definition and behavior evidence |
|---|---|
| `pxa_uart.yaml` | `bsp/spacemit/platform/{n308/k1-x,rt24/os0_rcpu,rt24/os1_rcpu}/_register_defination.h`: `pxa_uart_reg_t` anonymous unions at 0,4,8; 32-bit stride; LCR/MCR/LSR/MSR/SCR. `bsp/spacemit/drivers/uart/pxa_uart.h`: bits for DR=0, TX data request=5, TEMT=6, DLAB=7, FIFO commands=0..2, UUE=6. `pxa_uart.c`: TX uses `DW_LSR_TRANS_EMPTY` bit5; conflicting `DW_LSR_THRE` bit6 is not used as TX readiness. RX reads RBR low byte; UART initialization preserves alias sequencing. |
| `rt24_plic.yaml` | `libcpu/risc-v/spacemit/rt24/riscv-plic.h`: base 0xe0000000, hart bank shift27, source stride4, pending+0x1000, enable+0x2000, threshold+0x200000, claim+0x200004. `riscv-interrupt.c`: SOC_INT_MAX=128 is a firmware bound, not a hardware discovery result. Pending exposed read-only: vendor write/clear helpers disagree on banking. Claim read has side effects; write completes, never use modify. |
| `rt24_clint.yaml` | `libcpu/risc-v/spacemit/rt24/clint.h`: base 0xe4000000, hart shift27, compare+0x4000, timer+0xbff8; volatile uint64_t access. `rt24/spacemit_sdk_soc.h`: SOC_TIMER_FREQ=24000000. Not derived from APB timer chapter. Timer read-only is a restricted firmware API (vendor only reads it). |
| `n308_eclic.yaml` | `components/nmsis/core/include/core_feature_eclic.h`: CLIC_Type CFG byte+0, INFO word+4, MTH byte+0xb; CTRL byte quartet at+0x1000 stride4. CFG.nlbits bits1..4; INFO C layout numint bits0..12, version13..20, intctlbits21..24; ATTR shv0,trig1..2. INFO NUM mask disagrees (12 vs 13 bits), use explicit layout, record conflict. `n308/spacemit_sdk_soc.h`: enum through68 but __ECLIC_INTNUM=51. Exposed 69-index firmware window is NOT a silicon input count. Consumer must check discovered INFO before access. No TEE registers or PLIC runtime glue. |
| `n308_timer.yaml` | `components/nmsis/core/include/core_feature_timer.h`: MTIMER+0, MTIMERCMP+8, MTIMECTL+0xff8, MSIP+0xffc; RV32 high/low/high read loop and low=MAX/high/low compare update. Timer control bits0..2. Native u64 accessor omitted on RV32; split halves represent exact 64-bit registers. MTIMECTL advanced bits depend on Nuclei revision (>=1.4); consumers must not enable unverified features. Reset key/register and nonzero-hart CLINT mode intentionally omitted. |
| N308 address policy | `libcpu/risc-v/spacemit/n308/riscv-system-init.c` `_get_iregion_info`: test MCFG bit16; MIRGB align-down1024, ECLIC+0x20000, TIMER+0x30000. Otherwise fallback ECLIC=0x0c000000, TIMER=0x02000000. `components/nmsis/core/include/riscv_encoding.h`: MCFG_INFO CSR0xfc2, MIRGB_INFO0x7f7. `n308/spacemit_sdk_soc.h`: timer32768*4 Hz. Constants only; HAL must read the CSRs and construct blocks dynamically. |
| `k1_mailbox.yaml` | `bsp/spacemit/drivers/mailbox/k1x_mailbox.h`: six u32 members at0..20, four channels, ACK offset4. `k1x-mailbox.c` IRQ: write0 IIR then read status; ACK clear at+12; deliver RX, clear+12 then ACK bit+4 at+8. Send rings one bit, ignores bytes. Set/clear registers exposed Write-only to prohibit RMW. DW/WDR unsupported/omitted. |
| `k3_mailbox.yaml` | `bsp/spacemit/drivers/mailbox/k3_mailbox.h`: four FIFO words+0x40, status+0x80, count+0xc0, two user quartets+0x100 stride16, thresholds+0x180 stride16. Explicit uint32 facts and bit indices, not host sizeof(unsigned long) for threshold bitfields. `k3-mailbox.c`: user selection reversed with rcpu_communicate; read/pop before callback, constant 'c' kick. Vendor RMWs clear/set registers: PAC instead offers W1C/W1S direct-write views, separate enable_status read alias. SYS_CONFIG/reset intentionally omitted. |
| `k3_boot.yaml` | `bsp/spacemit/platform/rt24/os{0,1}_rcpu/_register_defination.h`: core0 LO/HI 0xc088007c/80, core1 LO/HI 0xc088008c/90. `bsp/spacemit/drivers/rpmi/spacemit-rpmi.c`: core0 writes core1 LO=1; core1 writes core0 LO=1, including core1 without RPMI. This is a loader handshake, not service success. |
| Chip address views | `bsp/spacemit/platform/n308/k1-x/dts/k1-x.dtsi`: UART0 0xc0881000, UART1 0xc088d000, mailbox0xc088a000. `bsp/spacemit/platform/rt24/k3.dtsi`: UART0 0xc0881000, UART1..4 0xc0881200..1500; mailbox3..6 0xcac90c00..1800, mailbox7 0xc0760000. DTS disable/enable and pinmux are board choices, not token ownership. K3 core descriptors preselect only the matching hart bank; HAL must verify actual mhartid. |
| >4GiB firmware facts | root `esos_rt24.its`, `bsp/spacemit/platform/rt24/os{0,1}_rcpu/gcc.ld` and platform definitions: core0 firmware0x100200000/resource0x1006fc000; core1 firmware0x100800000/resource0x100cfc000. These are u64 metadata, NOT u32 VirtIO addresses or universal linker allocations. |

### Approved K3 threshold correction (hardware-unverified)

`provenance/corrections.yaml` records the header fingerprint and selected
manual formula `MAILBOX_IRQTHR0_u = 0x180 + 0x10*u` (line46). The ESOS C
`mbox_irqthresh_t` is a **struct**, not a union: val is followed by
unsigned-long bitfields. Cross-target Clang 21.1.8 static assertions on the
original declarations prove `sizeof(mbox_thresh_t)=32` with RV64/LP64D
(user1 offset0x1a0) and20 with RV32/ILP32F (user1 offset0x194), not16.
`python3 scripts/verify-source-layout.py RESEARCH_CACHE` reproduces both
sizeof and offsetof checks without importing source into tracked files.

The PAC retains documented user1 offset0x190, not accidental C pointer
arithmetic. The supervisor approved this correction explicitly. It is **not**
hardware-verified. Nonblocking HAL mailbox notifications must not write these
thresholds (ESOS uses that path only when tx_block is set); future threshold
configuration requires deliberate board validation. API names are unchanged.

Independent loader comparison: research `k3-rproc.c` (SpacemiT vendor U-Boot
remoteproc driver) writes ELF entry LO/HI, sets core hart ID, clears the opposite
LO and waits for a nonzero flag. `spl-k3.c` loads FIT `rcpu0-fw`/`rcpu1-fw`.
Research `k1x-rproc.c` (Linux) uses `esos-entry-point` and DDR remapping;
the PAC does not implement either loader or address translation.

Secondary cross-check: official `docs-chip` Markdown at
`99fc06777f48f8b2c2f901c2af8056b603645b6c`, K3 manual V1.13:
`en/key_stone/k3/k3_docs/k3_usermanual/14_connectivity/uart.md`
LSR table lines447..453 and DLAB line406; mailbox chapter
`16_peripherals/mailbox.md` is a broader/security-dependent block, NOT a reason
to enlarge the ESOS four-channel RCPU arrays. Redistribution rights unclear;
no chapter contents shipped. UART divisor-high bits9:8 unspecified, FIFO-depth
claims differ, not encoded. No reset values are guessed: generated Default is
zero, not hardware reset. Manual W1C descriptions and vendor command usage
support direct mask writes, but no hardware side effect is tested here.

## Runtime boundaries

The expansion supplies source-backed clock/reset/pinmux, DMA/GPIO/APB timer,
AP controller and DDR register definitions, while leaving all runtime policy
with consumers. A board must establish UART readiness and ownership before
using it; this is not a prerequisite for generating the PAC. No general safe
PM reconfiguration policy, suspend/resume, management service, cache maintenance,
RPMsg queue, RPMI transport or shared wire struct implementation is supplied.
Shared wire layouts must use zerocopy, not PAC register fieldsets. PAC fieldsets are native-endian MMIO values, never host
protocol layouts. UART reads and mailbox FIFO reads have side effects. Generic
`modify` exists on raw RW FIFO/claim views due to chiptool; it is forbidden for
those registers by the reviewed API contract. W1C/W1S registers have no modify.
