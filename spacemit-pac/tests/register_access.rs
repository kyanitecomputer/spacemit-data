#![cfg(any(feature = "k1-rcpu", feature = "k3-rcpu0", feature = "k3-rcpu1"))]
use spacemit_pac as pac;

#[test]
fn uart_aliases_and_status_decode_use_reviewed_widths() {
    let mut storage = [0u32; 12];
    storage[5] = 0x61;
    let uart = unsafe { pac::pxa_uart::Uart::from_ptr(storage.as_mut_ptr().cast()) };
    assert_eq!(uart.rbr().as_ptr(), uart.thr().as_ptr());
    assert_eq!(uart.rbr().as_ptr(), uart.dll().as_ptr());
    assert_eq!(uart.ier().as_ptr(), uart.dlh().as_ptr());
    assert_eq!(uart.iir().as_ptr(), uart.fcr().as_ptr());
    uart.thr().write_value(0xa5);
    assert_eq!(uart.rbr().read(), 0xa5); // Same storage, NOT hardware FIFO emulation.
    uart.lcr().write_value(0x83);
    uart.dll().write_value(13);
    assert_eq!(storage[3], 0x83);
    assert_eq!(storage[0], 13);
    let status = uart.lsr().read();
    assert!(status.dr() && status.tdrq() && status.temt());
    assert!(!status.oe());
    assert_eq!(uart.scr().as_ptr() as usize - uart.as_ptr() as usize, 28);
}

#[cfg(feature = "k1-rcpu")]
#[test]
fn k1_doorbell_and_ack_are_direct_mask_writes() {
    let mut storage = [0u32; 6];
    let mailbox = unsafe { pac::k1_mailbox::Mailbox::from_ptr(storage.as_mut_ptr().cast()) };
    storage[3] = 0xffff; // A direct write must not OR the previous clear-mask value.
    mailbox.ipc_icr().write_value(1 << 2);
    mailbox.ipc_isrw().write_value(1 << (2 + 4));
    mailbox.ipc_iir().write_value(0);
    assert_eq!(storage, [0, 0, 64, 4, 0, 0]);
    // W1C hardware effects/interrupt ordering are a HAL/hardware test, not simulated here.
}

#[cfg(feature = "k1-rcpu")]
#[test]
fn n308_byte_controller_and_split_counter_preserve_storage() {
    let mut storage = [0u32; 1100];
    let eclic = unsafe { pac::n308_eclic::Eclic::from_ptr(storage.as_mut_ptr().cast()) };
    storage[1] = 69 | (3 << 21);
    assert_eq!(eclic.info().read().numint(), 69);
    assert_eq!(eclic.info().read().intctlbits(), 3);
    eclic.cfg().write(|v| v.set_nlbits(3));
    eclic.mth().write_value(0x80);
    eclic.irq(7).ie().write_value(1);
    eclic.irq(7).attr().write(|v| {
        v.set_shv(true);
        v.set_trig(2);
    });
    eclic.irq(7).ctl().write_value(0xe0);
    assert_eq!(storage[0], 6);
    assert_eq!(storage[2], 0x80000000);
    assert_eq!(storage[(0x1000 + 7 * 4) / 4], 0xe0050100);
    assert!(std::panic::catch_unwind(|| eclic.irq(69)).is_err());
    let mut timer_storage = [0u32; 1024];
    let timer = unsafe { pac::n308_timer::Timer::from_ptr(timer_storage.as_mut_ptr().cast()) };
    timer.mtimecmp_lo().write_value(u32::MAX);
    timer.mtimecmp_hi().write_value(0x12345678);
    timer.mtimecmp_lo().write_value(0x9abcdef0);
    assert_eq!(timer_storage[2..4], [0x9abcdef0, 0x12345678]);
    timer_storage[0..2].copy_from_slice(&[0xfedcba98, 0x76543210]);
    let time = (u64::from(timer.mtime_hi().read()) << 32) | u64::from(timer.mtime_lo().read());
    assert_eq!(time, 0x76543210fedcba98);
    timer.mtimectl().write(|v| v.set_timestop(true));
    assert_eq!(timer_storage[0xff8 / 4], 1);
    // CSR discovery is performed by the HAL; these are source-derived inputs, not globals.
    assert_eq!(pac::chip::CSR_MCFG_INFO, 0xfc2);
    assert_eq!(pac::chip::IREGION_PRESENT_MASK, 1 << 16);
    assert_eq!(pac::chip::IREGION_ECLIC_OFFSET, 0x20000);
    assert_eq!(pac::chip::IREGION_TIMER_OFFSET, 0x30000);
}

#[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
#[test]
fn k3_fifo_user_strides_and_clear_masks() {
    let mut storage = [0u32; 128];
    let mailbox = unsafe { pac::k3_mailbox::Mailbox::from_ptr(storage.as_mut_ptr().cast()) };
    mailbox.message(3).write_value(u32::from(b'c'));
    assert_eq!(storage[0x4c / 4], 99);
    storage[0x8c / 4] = 2 | (1 << 29);
    assert!(mailbox.fifo_status(3).read().is_empty());
    assert!(mailbox.fifo_status(3).read().readempty());
    storage[0xcc / 4] = 5;
    assert_eq!(mailbox.msg_status(3).read().num_msg(), 5);
    storage[0x114 / 4] = 0xffff;
    mailbox.irq(1).status_clr().write_value(1 << 6);
    mailbox.irq(0).enable_set().write_value(0x55);
    assert_eq!(mailbox.irq(0).enable_status().read(), 0x55);
    mailbox.irq(1).enable_clr().write_value(0x80);
    mailbox.threshold(1).write_value(0x12345678);
    assert_eq!(storage[0x114 / 4], 64);
    assert_eq!(storage[0x11c / 4], 128);
    assert_eq!(storage[0x190 / 4], 0x12345678);
    assert!(std::panic::catch_unwind(|| mailbox.message(4)).is_err());
    assert!(std::panic::catch_unwind(|| mailbox.irq(2)).is_err());
}

#[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
#[test]
fn rt24_native_counter_banks_and_opposite_core_handshake() {
    let mut storage = vec![0u64; 0xc000 / 8];
    let timer = unsafe { pac::rt24_clint::Clint::from_ptr(storage.as_mut_ptr().cast()) };
    storage[0xbff8 / 8] = 0xfedcba9876543210;
    assert_eq!(timer.mtime().read(), 0xfedcba9876543210);
    timer.mtimecmp().write_value(0x123456789abcdef0);
    assert_eq!(storage[0x4000 / 8], 0x123456789abcdef0);
    let hart = pac::chip::HART_ID;
    assert_eq!(pac::chip::CLINT_BASE, 0xe4000000 + (u64::from(hart) << 27));
    assert_eq!(pac::chip::PLIC_BASE, 0xe0000000 + (u64::from(hart) << 27));
    assert!(pac::chip::FIRMWARE_ADDRESS > u32::MAX as u64);
    assert!(pac::chip::RESOURCE_TABLE_ADDRESS > u32::MAX as u64);
    let mut boot_storage = [0u32; 40];
    let boot = unsafe { pac::k3_boot::Boot::from_ptr(boot_storage.as_mut_ptr().cast()) };
    if hart == 0 {
        boot.core1_boot_entry_lo().write_value(1);
    } else {
        boot.core0_boot_entry_lo().write_value(1);
    }
    assert_eq!(boot_storage[pac::chip::READY_OFFSET as usize / 4], 1);
    assert_eq!(boot_storage[if hart == 0 { 0x7c / 4 } else { 0x8c / 4 }], 0);
    // Does NOT establish system-management readiness or satisfy a real SPL timeout.
    let plic = unsafe { pac::rt24_plic::Plic::from_ptr(pac::chip::PLIC_BASE as *mut ()) };
    assert_eq!(
        plic.priority(127).as_ptr() as u64,
        pac::chip::PLIC_BASE + 127 * 4
    );
    assert_eq!(
        plic.enable(3).as_ptr() as u64,
        pac::chip::PLIC_BASE + 0x200c
    );
    assert_eq!(
        plic.claim().as_ptr() as u64,
        pac::chip::PLIC_BASE + 0x200004
    );
    assert!(std::panic::catch_unwind(|| plic.priority(128)).is_err());
    // Address arithmetic only above: no physical MMIO pointer is dereferenced.
}
