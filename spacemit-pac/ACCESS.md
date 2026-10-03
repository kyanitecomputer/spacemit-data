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
supplied. No silicon validation is claimed.
