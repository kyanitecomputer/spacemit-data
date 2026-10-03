# Complete source-coverage inventory

The expanded PAC accounts for every register-oriented table in the pinned K1/K3
public Markdown manuals, including K1 pin control, and the repeated K1 datasheet
facts. Unpublished layouts are explicitly recorded, not filled with invented
addresses. Hardware testing is deferred, not a generation prerequisite.

## Counts and definitions

| Manifest | Blocks | Register views | Direct indexed view elements | Field definitions | Instances | Docs / C records | Corrections |
|---|---:|---:|---:|---:|---:|---:|---:|
| `k1_system` | 25 | 351 | 1,224 | 1,371 | 58 | 286 / 7 | 37 |
| `k1_io` | 22 | 497 | 605 | 1,838 | 93 | 278 / 15 | 102 |
| `k1_media` | 37 | 3,896 | 4,930 | 4,957 | 126 | 159 / 1,776 | 88 |
| `k1_pinmux` | 1 | 129 | 129 | 12 | 1 | 14 / 0 | 5 |
| `k3_system` | 29 | 545 | 2,802 | 2,468 | 90 | 328 / 39 | 93 |
| `k3_io` | 27 | 320 | 2,741 | 1,177 | 111 | 154 / 6 | 46 |
| `k3_image` | 2 | 47 | 325 | 107 | 1 | 398 / 0 | 11 |
| `k3_memory` | 10 | 659 | 735 | 1,561 | 18 | 204 / 17 | 76 |
| **Expanded total** | **153** | **6,444** | **13,491** | **13,491** | **498** | **1,821 / 1,860** | **458** |

- A **register view** is one declared register item, before array expansion;
  RO/WO aliases and source-supported layout variants are separate views. Nested
  block items are not registers. The retained baseline adds 8 IR files, 10
  blocks, 50 register views and 24 fields; expansion adds 10 IR files. Whole
  repository definition totals are 18 files / 163 blocks / 6,494 views / 13,515
  fields. The two K3 feature outputs reuse the same family data, not two chips.
- **Direct indexed view elements** expand each register item's regular/sparse
  array, but do not multiply nested block reuse or instances. They include
  directional aliases, and are neither unique storage words nor chip quantities.
- **Field definitions** count nonreserved IR entries, including reused/compressed
  layouts and direction-specific fieldsets. They are not unique physical fields.
  K3 V2D's 47 compressed views/107 fields represent 386 source words/558 named
  fields after its four nested layers and arrays are placed. K1 MFPR's 129 named
  words reuse 12 common field definitions. K1 media's 3,896 views include
  uninstantiated portable GPU containers/variants alongside accessor-established
  views. A container's field width is not evidence of native MMIO width, and
  overlapping variants/aliases are not additional physical words.
- **Instances** are source-backed address/view metadata: 367 AP, 93 RCPU, 38
  secure. They may reuse layouts or describe different views of a bank. No
  AP/secure-to-RCPU translation, ownership, runtime capability or global unique
  silicon-register count is asserted. Unbound layouts remain usable definitions.
- **Records** are per-manifest table/C-definition dispositions. Shared IRQ tables
  and C definitions may support multiple lanes. Across 3,681 records there are
  3,402 generated, 149 duplicate, 82 not-MMIO and 48 source-gap dispositions.
  This is not a register count. Five gap records repeat datasheet functional
  names/widths without offsets already discussed in the manual, not new gaps in
  a second silicon bank. Broad chapter/prose gaps belong in corrections, not
  fake table records.

There are **1,815 distinct genuine documentation table headers**: all **1,771**
register-oriented helper candidates across the complete supplied corpus, plus
44 genuine additional headers (IRQ/base/protocol and Register/Comment/MFPR
summaries). The initial candidate number includes non-MMIO algorithm/CSR tables
and repeated datasheet tables, not 1,771 distinct register definitions. The
1,860 C records cover 1,859 distinct kind/path/hash/line identities. Exhaustive
Markdown completeness is checked independently of C supplemental accounting.

## Covered source families

- **K1**: manual pinout/MFPR; address/IRQ assignments; CPU counter/CIU, AP
  PLIC/CLINT, byte ECLIC and split native timer; clock/reset/PLL/PMU, DMA,
  security/RNG, RTC/watchdog/mailbox/PWM; memory/storage/QSPI/DDR observed
  layouts; low/high-speed GPIO/UART/I2C/SSP/EMAC/USB/PCIe/SDH; V2D/MMU,
  Saturn LCD/HDMI DPU, portable RGX GPU variants, VPU/HDMI, DSI/SPI-LCD,
  CCIC/CSI/ISP/CPP/JPEG. Public-manual register-bearing chapters 3 and 8–16
  are covered; chapters 6/7 supply address/IRQ metadata. Datasheet V2D/MFPR
  repetition is inventoried without treating it as extra MMIO.
- **K3**: address/IRQ, CPU/PM/clock/reset/security, GPIO/DMA/HDMA/IOPMP/
  spinlock/timer/watchdog/mailbox/PWM/JTAG capability facts; memory/storage/
  SDHCI/UFS/QSPI/DDR observed layouts; complete V2D V2 bank; DSI plus available
  DPU/DP C definitions; audio/ADMA/RI2S/SPI, IR/eSPI/I2C/UART/CAN/GMAC/PCIe/
  USB/xHCI. All register-bearing chapters 8–17, including every connectivity
  and peripheral subchapter, are inventoried. Feature-only video/CSI/GPU/
  security chapters retain precise missing-map corrections.

## Pins, identities and source checks

The 207 manifest source entries represent **185 distinct kind/path/SHA256
identities**: 45 docs, 37 ESOS, 83 Linux and 20 U-Boot. Repeated references
are counted once here; conflicting file bytes at distinct Linux pins are never
flattened into one alleged revision.

- Markdown: `99fc06777f48f8b2c2f901c2af8056b603645b6c`.
- ESOS: `e0b8d955bdc84957da3b7bba8c89e35ee0d27c2b`.
- Linux: K1 system/pinmux `cee9395acd8043be0644b25c34bfa86623f2b935`;
  K1 IO/media `31c449aeaad8c7759bc983ca0e26946e5b6746dc`.
- U-Boot: `28805afe074715a0ede143d42999ea06bee58fbd`.
- Corroboration only: K1 V6.3 PDF SHA256
  `484fe0fb426d67c0472cd17e430f9abea00538fa42432431252f8e031c0af88d`;
  K3 V1.13 PDF SHA256
  `570d7cf33ec28d1b537057350d462ab8c15fb8044fb3fb86855d0e10c6772c25`.
  PDF pages/sections are concise correction evidence, not redistributed prose.

Committed IR/manifests are sufficient for normal offline generation. Original
source hashes/table completeness are separate source-dependent evidence:

```sh
python3 scripts/verify-expanded.py \
  --source-root /tmp/spacemit-research/docs/en/key_stone \
  --esos-root /tmp/spacemit-research/esos-full \
  --linux-root /tmp/k1-system-linux-pinned \
  --linux-root /tmp/k1-io-c --linux-root /tmp/k1-media-c/linux \
  --linux-root /tmp/k1-pinmux-c/linux \
  --uboot-root /tmp/k3-io-c/vendor --uboot-root /tmp/k3-memory-c
```

Repeated C roots are explicit pinned file selections: every selected file must
match its manifest hash. No network fetch or vendor cache is part of PAC
compilation. Each `tools/expanded/<lane>.py --check` reproduces committed
outputs; optional C-root flags enforce fingerprints. `scripts/check.sh` runs
importer/source tests when the documentation cache is present, and all offline
PAC/generator gates regardless. Consumers do not need raw vendor sources.

## GPU native-width evidence and preserved portable facts

At Linux `31c449aeaad8c7759bc983ca0e26946e5b6746dc`, explicit constant
register arguments in `drivers/gpu/drm/img-rogue/{rgxlayer_impl.c,rgxinit.c,
rgxpower.c,rgxstartstop.c}` establish 23 names: 21 native32 and two native64
(`SOFT_RESET` and `MTS_GARTEN_WRAPPER_CONFIG`).
`rgxlayer_impl_common.c:188–281` connects the RGX32/64 calls to matching OS
hardware accessors. All five files have manifest SHA256 fingerprints; each
bound register's correction cites its exact call sites. `GpuRgx` uses separate
read/direct-write views, not modify, and only this access block is instantiated.

In particular, `rgxlayer_impl.c:123–126,461` reads/writes `SLC_CTRL_BYPASS`
with native32 access at +0x3828. Its `__XE_MEM` high-bit definitions remain in
uninstantiated `GpuRgxWideVariant64`. `GpuRgxPortable32/64` likewise preserve
header-only layout facts. All 2,063 original GPU offset/field-span facts remain;
the current union contains 2,078. Mask/container types alone do not establish
native width; conditional source paths do not prove K1 BVNC applicability,
universal feature presence, reset values or side-effect semantics. The source
tests check every one of the 23 observed widths and all retained XE_MEM spans.

## Precise remaining source limits

Full **available-source** coverage is not a claim that every proprietary IP
register or implemented capability is published. The exact locations/evidence
remain in [expanded manifests](expanded/) and their deterministic emitters.

- K1 PAD PLIC trigger offsets are Reserved; PMU stable timer collides with wake
  clear at+0x7c. ECLIC capacity claims disagree; INFO remains authoritative.
  Crypto publishes RNG, not complete AES/RSA/ECC/HASH banks. DMA WR_CNT access
  and one timeout reset are unresolved, not guessed.
- K1/K3 complete DDRCTL/PHY training/scheduler/timing maps and QSPI total LUT
  bounds are absent; only established slots and driver-selected sequences are
  emitted. K1 misaligned unused DDRC_ERR_ID=0x15 is not changed to invented0x15c.
- K1 GPIO Edge has a base but no layout; SHUB I2C1 has an IRQ but no numeric
  base. Full K1 USB3/PCIe DBI/ATU/eDMA/analog maps and ambiguous INTX/ELBI bases
  are not fabricated. K1 MFPR wake applicability/per-pin reset values are not
  uniformly established; manual split DRIVE encoding differs from C's logical
  contiguous shift and is explicitly preserved.
- K1 media: five algorithm name/width tables lack offsets; 28 DPU sub-bank
  bases lack layouts (LMERGE/ACAD/LUT3D/DSCA/DSCB). V2D palette and ISP result
  memory bounds depend on caller data; no fixed lengths invented. Full ISP
  tuning, GPU BVNC applicability and secure HDMI translation remain unknown.
- K3 CPU ACLINT/APLIC/IMSIC/debug/trace, crypto/TRNG/eFuse, thermal and timeout
  layouts are absent. JTAG tap control is not proven MMIO. HDMA instance bases
  and mailbox0–2 channel-variant mapping are unspecified. IOPMP parameter
  widths, LCD1 SID and PORTBCDE field conflicts are retained.
- K3 CSI/GPU/VPU banks are not supplied. The complete V2D V2 bank does not
  establish separate MMU/AFBC/IRQ banks or DMA-clear polarity; related K1
  offsets are not imported as K3 facts.
- K3 DPU full plane stride/secure equivalence, UFS ATOP analog map/base conflict,
  dynamic DSI PLL bits and DP field write permissions remain limited. Known
  words are retained without guessed layouts or safe setters.
- K3 CAN advertises128 mailboxes, while the C classic layout establishes64;
  FD/RAM organization is not fabricated. Remaining GMAC windows, PCIe
  inbound/DMA layouts and D/E region capacities, and dynamic xHCI capability
  counts/bases require additional source/runtime discovery. eSPI VALID, UART
  divisor-high and I2C FIFO field conflicts stay explicit.
- Retained K3 mailbox thresholds use documented stride0x10; original C structs
  produce RV64 stride32/RV32 stride20. Source/C ABI tests reproduce the conflict;
  no hardware test is claimed and no generation gate is imposed.

Native MMIO widths, directional aliases and raw bits do not supply runtime
power/pinmux/clock policy, firmware transport, board ownership or physical
validation. Existing HAL/runtime/firmware contracts are unchanged.
