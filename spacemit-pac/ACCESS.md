Generated register access is width- and register-mode typed. Construct blocks
with `unsafe from_ptr(*mut ())` only after validating base, alignment, address
view and exclusive ownership. `Default` values are zero, not hardware resets.
W1C/W1S registers are Write-only direct masks, so RMW is unavailable:

```compile_fail
use spacemit_pac::common::{Reg, W};
let reg = unsafe { Reg::<u32, W>::from_ptr(core::ptr::null_mut()) };
reg.modify(|v| *v |= 1); // clear/set command may not read and replay other bits
```

Read-only registers cannot write:

```compile_fail
use spacemit_pac::common::{Reg, R};
let reg = unsafe { Reg::<u32, R>::from_ptr(core::ptr::null_mut()) };
reg.write_value(1);
```

UART alias selection and FIFO/read-clear effects are driver responsibilities.
Raw RW FIFO and claim/completion views must never use `modify`. No runtime
interrupt/vector glue, loader, power-management or shared-memory protocol is
supplied. No silicon validation is claimed. Hardware testing is later, not a condition
for generating these source-backed definitions.

Expanded family modules cover the complete available public-manual numeric
layouts and pinned C supplements. Old modules, `chip::*` constants, three chip
features and prebanked controller metadata are retained. `soc::peripherals::*`
contains typed `Instance<T>` values labelled AP/RCPU/secure with original source
kind/path/line. Unsafe `peripheral()` checks pointer-width overflow of the full
block; the caller still proves mapping, alignment, access permissions and
ownership. No AP/secure-to-RCPU address translation or runtime feature discovery
is implied by metadata. Dynamic capability windows require consumer discovery.

RO/WO aliases prohibit modify on side-effect/command words. Mixed configuration
and clear/trigger words likewise use explicit write views where source semantics
require it. Some source-only unused words have conservative raw read views;
that restriction is not a claim of hardware read-only behavior. Fields reused
across arrays/variants do not establish per-instance resets or capabilities.
In particular, K1 MFPR DRIVE[1:0] is bits12:11, DRIVE[2] bit10, and EDGE_CLEAR
is a conditional detection-disable control, not an inferred W1C command.

See [coverage and unresolved source facts](../provenance/COVERAGE.md). Register
views, array elements and repeated per-instance quantities are different counts;
none is advertised as a unique silicon inventory.

## Source-fidelity command and width policies

K1 GPIO `PSn` is the full32-bit set mask in all four sparse/interleaved banks;
manual/PDF's Bits31 cell contradicts both its n=0..31 prose and native C stores.
K1 GPU `GpuRgx` contains23 accessor-observed names (21 native32, two native64),
including32-bit `SLC_CTRL_BYPASS+0x3828`. These have separate read/direct-write
views, never modify. Portable header/container and wider variant layouts
remain available but uninstantiated: their container widths are **not** verified
native transactions. Neither source-observed access nor metadata establishes
unconditional BVNC feature availability, resets or read/write side effects.

K3 clock request words require explicit complete configuration writes: set
selected requests and zero inactive requests, rather than replaying readback.
Normal configuration fields remain available. Timer restart+0x08 similarly
writes1 to selected counters and0 for no effect. Watchdog status+0xc0 is **W0C**:
write0 clears WTS; write1 has no effect. SD command+0x0e is read/direct-write
native16; transfer mode+0x0c remains independent modifiable native16 configuration.
MDIO GD is a16-bit field in a native32-bit data word, not a16-bit transaction.

Concrete compile-fail probes use the K3 views on either K3 feature; the hidden
fallback checks the same Write-only contract on other feature selections.

```compile_fail
# #[cfg(not(any(feature = "k3-rcpu0", feature = "k3-rcpu1")))]
# let reg = unsafe { spacemit_pac::common::Reg::<u32, spacemit_pac::common::W>::from_ptr(core::ptr::null_mut()) };
# #[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
let reg = unsafe { spacemit_pac::k3_system::Pmuap::from_ptr(core::ptr::null_mut()) }.pmu_lcd_clk_rst_ctrl5_write();
reg.modify(|_| {});
```

```compile_fail
# #[cfg(not(any(feature = "k3-rcpu0", feature = "k3-rcpu1")))]
# let reg = unsafe { spacemit_pac::common::Reg::<u32, spacemit_pac::common::W>::from_ptr(core::ptr::null_mut()) };
# #[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
let reg = unsafe { spacemit_pac::k3_system::Pmuap::from_ptr(core::ptr::null_mut()) }.pmu_cci_clk_ctrl_write();
reg.modify(|_| {});
```

```compile_fail
# #[cfg(not(any(feature = "k3-rcpu0", feature = "k3-rcpu1")))]
# let reg = unsafe { spacemit_pac::common::Reg::<u32, spacemit_pac::common::W>::from_ptr(core::ptr::null_mut()) };
# #[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
let reg = unsafe { spacemit_pac::k3_system::Pmuap::from_ptr(core::ptr::null_mut()) }.pmua_cpu_c0_clk_ctrl_write();
reg.modify(|_| {});
```

```compile_fail
# #[cfg(not(any(feature = "k3-rcpu0", feature = "k3-rcpu1")))]
# let reg = unsafe { spacemit_pac::common::Reg::<u32, spacemit_pac::common::W>::from_ptr(core::ptr::null_mut()) };
# #[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
let reg = unsafe { spacemit_pac::k3_system::RcpuPmu::from_ptr(core::ptr::null_mut()) }.rcpu_bus_clk_ctrl_write();
reg.modify(|_| {});
```

```compile_fail
# #[cfg(not(any(feature = "k3-rcpu0", feature = "k3-rcpu1")))]
# let reg = unsafe { spacemit_pac::common::Reg::<u32, spacemit_pac::common::W>::from_ptr(core::ptr::null_mut()) };
# #[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
let reg = unsafe { spacemit_pac::k3_system::TimerWatchdog::from_ptr(core::ptr::null_mut()) }.timer_count_restart_write();
reg.modify(|_| {});
```

```compile_fail
# #[cfg(not(any(feature = "k3-rcpu0", feature = "k3-rcpu1")))]
# let reg = unsafe { spacemit_pac::common::Reg::<u32, spacemit_pac::common::W>::from_ptr(core::ptr::null_mut()) };
# #[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
let reg = unsafe { spacemit_pac::k3_system::TimerWatchdog::from_ptr(core::ptr::null_mut()) }.timer_watchdog_status_write();
reg.modify(|_| {});
```

```compile_fail
# #[cfg(not(any(feature = "k3-rcpu0", feature = "k3-rcpu1")))]
# let reg = unsafe { spacemit_pac::common::Reg::<u16, spacemit_pac::common::W>::from_ptr(core::ptr::null_mut()) };
# #[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
let reg = unsafe { spacemit_pac::k3_memory::Sdhci::from_ptr(core::ptr::null_mut()) }.sd_command_write();
reg.modify(|_| {});
```
