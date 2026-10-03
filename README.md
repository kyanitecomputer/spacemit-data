# spacemit-data

Source-reviewed K1/K3 register-data repository and generated `no_std`
`spacemit-pac`, preserving the K1 N308 and K3 RT24 auxiliary-core selections. Original work
is **Apache-2.0 OR MIT**. This is an ASPEED-style separation of register data,
deterministic host generator and firmware PAC, not a hardware bring-up claim.
No allocator or external dependencies in the PAC. Rust edition 2024.

## Layout and reproducible generation

- `data/registers/*.yaml`: chiptool IR, using JSON's strict YAML subset;
  reviewed aliases, access modes, original concise register facts.
- `data/chips/{k1-rcpu,k3-rcpu0,k3-rcpu1}.yaml`: typed generator descriptors
  selecting register blocks and address-view/core metadata. They deliberately
  do not use chiptool Device runtime glue (PLIC-oriented, invalid for N308).
- `provenance/`: pinned sources, SHA-256 fingerprints, semantics/conflicts and
  explicit alias/W1C policy. No substantial vendor manual prose redistributed.
- `spacemit-data-gen/`: host Rust generator, pinned Kyanite chiptool **git
  dependency** `4e232f0ac28f18f0901ae930dabf2b5b62e1a05f`.
- `spacemit-pac/`: generated Rust; never edit `src/*.rs` manually.

```sh
cargo run --locked --offline --manifest-path spacemit-data-gen/Cargo.toml
cargo run --locked --offline --manifest-path spacemit-data-gen/Cargo.toml -- --check
scripts/check.sh
# Optional integrity and original C ABI evidence with supplied research cache:
python3 scripts/verify-sources.py /tmp/spacemit-research
python3 scripts/verify-source-layout.py /tmp/spacemit-research
```

`rust-toolchain.toml` pins Rust/rustfmt 1.98.1 and both firmware targets. The
pipeline merges namespaced IR, ExpandExtends, Sort, validates byte ranges,
array bounds, alignment, field ranges and **only explicit reviewed aliases**,
then renders through chiptool and writes only changed files. Only chiptool's
incidental Git-description doc header is normalized to the full dependency pin
so source archive/git builds agree. Generator tests render twice byte-for-byte
and check committed freshness. The validated generator Cargo.lock is retained for reproducibility; PAC-only
locks and build artifacts remain ignored. No root Cargo workspace required.

A **verified source** local override is supported, not an untracked executable:

```sh
cargo run --manifest-path spacemit-data-gen/Cargo.toml \
  --config 'patch."https://github.com/kyanitecomputer/chiptool".chiptool.path="/tmp/spacemit-research/chiptool"' \
  -- --check
```

Use that only for an archive/checkout matching the pin, not arbitrary edits.
The normal git dependency does not rely on this research directory or a
prebuilt binary.

## Complete source coverage

Eight expanded manifests cover every register-bearing table in the public K1/K3
Markdown manuals, plus repeated K1 datasheet facts and pinned C supplements:
CPU/system/security, clocks/reset/PMU, memory/storage, GPIO/pinmux, serial/audio,
CAN/Ethernet/PCIe/USB, graphics/video/display/capture. Published numeric facts
are exposed even when hardware testing has not yet occurred. Unpublished maps,
unknown counts/access semantics and conflicting locations remain explicit source
limitations, not prerequisites for PAC generation.

The expansion contains **153 block definitions, 6,444 register-view declarations,
13,491 reused/compressed field definitions and 498 source-view instances**.
These are definition counts, **not unique silicon-register counts**: directional
aliases, layout variants, arrays and shared banks must not be multiplied into a
chip inventory. K3 V2D's 47 view declarations cover 386 source words/558 named
fields; K1 pinmux's 129 named words share 12 common field definitions.
[Exact per-lane inventory and count definitions](provenance/COVERAGE.md) include
all 1,771 register-oriented source tables, 44 genuine additional table headers,
C definition records, source revisions and precise residual gaps.

Normal generation only needs committed IR/manifests and the pinned Rust dependency
cache; it never reads or fetches vendor sources. `scripts/verify-expanded.py`
independently checks accounting offline; supplied source roots additionally enable
all source hashes, real table-header checks and exhaustive table completeness.

## Consumer API

Choose exactly one feature in the HAL/application dependency:

```toml
spacemit-pac = { path = "../spacemit-data/spacemit-pac", features = ["k3-rcpu1"] }
```

Features: `k1-rcpu`, `k3-rcpu0`, `k3-rcpu1`; no `rt` feature. Conflicting cores
and K3/RV32 or K1/RV64 firmware selections fail compilation. Host builds are
allowed for tests. Featureless crate exposes no MMIO API.

| Module/type | Accessors |
|---|---|
| `pxa_uart::Uart` | `rbr/thr/dll`, `ier/dlh`, `iir/fcr`, `lcr/mcr/lsr/msr/scr` |
| `k1_mailbox::Mailbox` | `ipc_isrw/ipc_icr/ipc_iir/ipc_rdr` |
| `k3_mailbox::Mailbox` | `message/fifo_status/msg_status(channel)`, `irq(user)`, `threshold(user)` |
| `k3_mailbox::Irq` | `status/status_clr/enable_set/enable_status/enable_clr` |
| `rt24_clint::Clint` | `mtime` RO u64, `mtimecmp` RW u64 |
| `rt24_plic::Plic` | `priority(source)`, `pending/enable(word)`, `threshold`, `claim` |
| `n308_eclic::Eclic`, `Irq` | `cfg/info/mth`, `irq(index).ip/ie/attr/ctl` (byte IRQ registers) |
| `n308_timer::Timer` | `mtime_lo/hi`, `mtimecmp_lo/hi`, `mtimectl`, `msip` |
| `k3_boot::Boot` | `core0_boot_entry_lo/hi`, `core1_boot_entry_lo/hi` |

The original modules/constants above remain unchanged. Expanded modules use
`k1_system`, `k1_io`, `k1_media`, `k1_pinmux`, `k3_system`, `k3_io`, `k3_image`
and `k3_memory`. `soc::peripherals::*` exposes typed `soc::Instance<T>` metadata
with address, AP/RCPU/secure view, source kind/path/line and an unsafe
`peripheral()` constructor. These constants do **not** claim AP or secure
addresses are translated/reachable from RCPU, or that the consumer owns the
peripheral. Dynamic/prebanked controller metadata under `chip::*` is unchanged.

Every block has `unsafe const from_ptr(*mut ())` and `as_ptr() -> *mut ()`.
Registers use chiptool `common::Reg<T, R/W/RW>`: `read`, `write_value`, `write`
and `modify` only as allowed by register mode. UART LSR, K3 mailbox statuses,
ECLIC CFG/INFO/ATTR and timer control use public tuple fieldsets under each
module's `regs`, with `.0` for raw bits and named methods. Other views are
u8/u32/u64. W1C/W1S commands are **Write-only**: direct masks, never RMW.
FIFO/claim reads have side effects; **never modify** those raw RW views.

`chip::*` exports **u64** addresses/CSR numbers/rates and `HART_ID: u32`, not
unsafe global peripheral pointers. K3 PLIC/CLINT bases are prebanked by the
selected feature using `hart << 27`; HAL must verify actual mhartid. N308 bases
must be decoded by HAL from MCFG/MIRGB (bit16, align-down1024, internal offsets),
using fallback bases only when the CSR discovery indicates no internal region.
ECLIC 69-index firmware API window is NOT the discovered silicon capacity;
check INFO.numint and actual IRQ wiring. RT24 PLIC firmware window is 128, source 0
reserved. N308 uses split-32 counter reads/writes, not native u64 MMIO.

Rates are source constants: N308 131072 Hz, RT24 24000000 Hz. Absolute alarm
sequencing, high/low/high RV32 read retry and compare update ordering belong to
HAL. Do not confuse either machine timer with the APB timer/watchdog chapter.

**K3 readiness** writes opposite-core boot-entry LO=1; this only matches the
loader handshake, not RPMI management readiness. Keep vendor core0 management.
K3 firmware/resource-table addresses above 4 GiB remain u64 metadata, not truncated
VirtIO wire addresses. Protocol/shared-memory layouts are not supplied here;
consumers must use zerocopy wire types and an independently proven cache policy.

**K3 threshold conflict:** the PAC uses documented +0x180+0x10*user, while the
original ESOS struct computes user1+0x1a0 on RV64. The approved correction and
reproducible cross-C ABI assertions are in `provenance/corrections.yaml`.
Nonblocking HAL notifications must **not write thresholds**; future
configuration requires deliberate board validation. This is hardware-unverified.

## Validation and boundaries

`scripts/check.sh` runs 11 generator validation/determinism tests, 15 independent
verifier tests, and (with supplied source caches) eight importer checks/113
source tests including native C offset/bitfield oracles. It also runs actual
host storage-backed access tests for aliases, widths, status decoding, masks,
byte IRQ writes, array limits, counter high bits and opposite-core readiness
addressing, source-view instance construction, sparse pin/GPIO arrays, V2D
endpoints, full-width GPIO set masks, accessor-established GPU widths, MDIO GD,
clock requests, timer restart/W0C status and SDHCI native byte/halfword boundaries;
nine RO/WO/no-modify compile-fail doctests per feature;
strict generator Clippy; PAC cross-checks on
`riscv32imafc-unknown-none-elf` (K1) and `riscv64gc-unknown-none-elf` (both K3
cores); invalid feature/width rejection and no staged files. Host storage is
**not** a FIFO, W1C hardware model or a successful Linux handshake.

Hardware testing is deferred and is not a generation/delivery gate. No
RPMI/RPMsg protocol implementation, Linux transport validation, PM replacement,
suspend/resume, cache maintenance, or clock/reset/pinmux runtime changes. Inherited UART clock/pins and register ownership must be
established by a board before use. Unsupported/ambiguous behavior is recorded
in `provenance/README.md`, including conflicting N308 interrupt counts,
revision-dependent timer fields and UART divisor-high/FIFO ambiguities.
