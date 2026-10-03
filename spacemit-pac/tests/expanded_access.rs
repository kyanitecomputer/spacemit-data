#![cfg(any(feature = "k1-rcpu", feature = "k3-rcpu0", feature = "k3-rcpu1"))]
//! Source-named RAM-backed probes; no hardware side effects are simulated.
use spacemit_pac as pac;

#[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
#[test]
fn k3_pinmux_sparse_words_typed_fields_and_ap_view() {
    let mut storage = [0u32; 0x26c / 4];
    let mut instance = pac::soc::peripherals::AP_K3_PINMUX;
    assert_eq!(instance.address, 0xd401e000);
    assert_eq!(instance.view, pac::soc::AddressView::Ap);
    assert_eq!(instance.source_kind, pac::soc::SourceKind::Esos);
    instance.address = storage.as_mut_ptr() as u64;
    let bank = unsafe { instance.peripheral() };
    bank.gpio_00().write(|v| {
        v.set_function(4);
        v.set_drive(8);
        v.set_pull(6);
    });
    assert_eq!(storage[0], 0xd004);
    assert_eq!(
        bank.gpio_127().as_ptr() as usize - bank.as_ptr() as usize,
        0x1fc
    );
    assert_eq!(
        bank.pad_145().as_ptr() as usize - bank.as_ptr() as usize,
        0x244
    );
    assert_eq!(
        bank.pad_146().as_ptr() as usize - bank.as_ptr() as usize,
        0x248
    );
    assert_eq!(
        bank.pwr_ssp_rxd().as_ptr() as usize - bank.as_ptr() as usize,
        0x268
    );
    assert_eq!(storage[0x20c / 4], 0);
    assert_eq!(storage[0x210 / 4], 0);
    instance.address = u64::MAX;
    assert!(std::panic::catch_unwind(|| unsafe { instance.peripheral() }).is_err());
}

#[cfg(feature = "k1-rcpu")]
#[test]
fn k1_pinmux_typed_ap_instance_preserves_sparse_pins_and_split_drive() {
    let mut storage = [0u32; 0x250 / 4];
    let mut instance = pac::soc::peripherals::AP_K1_PINMUX;
    assert_eq!(instance.address, 0xd401e000);
    assert_eq!(instance.view, pac::soc::AddressView::Ap);
    assert_eq!(instance.source_kind, pac::soc::SourceKind::Docs);
    instance.address = storage.as_mut_ptr() as u64;
    let bank = unsafe { instance.peripheral() };
    bank.gpio_00().write(|v| {
        v.set_drive_1_0(2);
        v.set_drive_2(true);
        v.set_edge_clear(true); // Detection disable bit, not W1C inference.
    });
    assert_eq!(storage[1], 0x1440);
    assert_eq!(storage[0], 0); // No invented register at base offset zero.
    assert_eq!(
        bank.gpio_127().as_ptr() as usize - bank.as_ptr() as usize,
        0x24c
    );
    assert_eq!(
        bank.pmic_int_n().as_ptr() as usize - bank.as_ptr() as usize,
        0x1e8
    );
    instance.address = u64::MAX;
    assert!(std::panic::catch_unwind(|| unsafe { instance.peripheral() }).is_err());
}

#[cfg(feature = "k1-rcpu")]
#[test]
fn k1_io_gpio_interleaving_clear_aliases_and_qspi_bounds() {
    let mut storage = [0u32; 0x300 / 4];
    let gpio = unsafe { pac::k1_io::Gpio::from_ptr(storage.as_mut_ptr().cast()) };
    assert_eq!(
        gpio.gpio_plr(3).as_ptr() as usize - gpio.as_ptr() as usize,
        0x100
    );
    assert_eq!(
        gpio.gpio_edr3().as_ptr().cast::<()>(),
        gpio.gpio_edr_write3().as_ptr().cast::<()>()
    );
    gpio.gpio_edr_write3().write(|v| v.0 = 0x20);
    assert_eq!(gpio.gpio_edr3().read().0, 0x20);
    assert!(std::panic::catch_unwind(|| gpio.gpio_plr(4)).is_err());
    let qspi = unsafe { pac::k1_io::Qspi::from_ptr(storage.as_mut_ptr().cast()) };
    assert_eq!(
        qspi.rbdr(31).as_ptr() as usize - qspi.as_ptr() as usize,
        0x27c
    );
    assert!(std::panic::catch_unwind(|| qspi.rbdr(32)).is_err());
}

#[cfg(feature = "k1-rcpu")]
#[test]
fn k1_media_dsi_status_ack_is_direct_write_at_native_word_offset() {
    let mut storage = [0u32; 0x400 / 4];
    let bank = unsafe { pac::k1_media::Dsi::from_ptr(storage.as_mut_ptr().cast()) };
    assert_eq!(
        bank.dsi_irq_st().as_ptr(),
        bank.dsi_irq_st_write_view().as_ptr()
    );
    storage[4] = u32::MAX;
    bank.dsi_irq_st_write_view().write(|v| v.0 = 1);
    assert_eq!(storage[4], 1); // Direct RAM write, not acknowledgement emulation.
}

#[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
#[test]
fn k3_image_complete_indexed_bank_and_packed_fields() {
    let mut storage = [0u32; 386];
    let mut instance = pac::soc::peripherals::AP_K3_IMAGE_V2D;
    assert_eq!(instance.address, 0xc0100000);
    assert_eq!(instance.view, pac::soc::AddressView::Ap);
    instance.address = storage.as_mut_ptr() as u64;
    let bank = unsafe { instance.peripheral() };
    assert_eq!(
        bank.scaler_coef(23).as_ptr() as usize - bank.as_ptr() as usize,
        0x60
    );
    assert_eq!(
        bank.layer(3).reg_20().as_ptr() as usize - bank.as_ptr() as usize,
        0x1f8
    );
    assert_eq!(
        bank.palette_table(255).as_ptr() as usize - bank.as_ptr() as usize,
        0x604
    );
    bank.palette_table(255).write(|v| v.0 = 0xaabbccdd);
    assert_eq!(storage[385], 0xaabbccdd);
    bank.v2_reg_0_write().write(|v| v.0 = 1);
    assert_eq!(bank.v2_reg_0_read().read().0, 1);
    assert!(std::panic::catch_unwind(|| bank.scaler_coef(24)).is_err());
    assert!(std::panic::catch_unwind(|| bank.layer(4)).is_err());
    assert!(std::panic::catch_unwind(|| bank.palette_table(256)).is_err());
}

#[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
#[test]
fn k3_memory_sdhci_native_byte_halfword_views_do_not_overwrite_neighbors() {
    let mut storage = [0u32; 0x100 / 4];
    let bank = unsafe { pac::k3_memory::Sdhci::from_ptr(storage.as_mut_ptr().cast()) };
    bank.sd_block_size().write(|v| v.0 = 0x1234);
    bank.sd_block_count().write(|v| v.0 = 0x5678);
    assert_eq!(bank.sd_block_size().read().0, 0x1234);
    assert_eq!(bank.sd_block_count().read().0, 0x5678);
    bank.sd_host_control().write(|v| v.0 = 0x12);
    bank.sd_power_control().write(|v| v.0 = 0x34);
    assert_eq!(bank.sd_host_control().read().0, 0x12);
    assert_eq!(bank.sd_power_control().read().0, 0x34);
    assert_eq!(
        bank.sd_block_count().as_ptr() as usize - bank.as_ptr() as usize,
        6
    );
    assert_eq!(
        bank.sd_power_control().as_ptr() as usize - bank.as_ptr() as usize,
        0x29
    );
    storage[4] = 0xa5a5_5a5a;
    bank.sd_transfer_mode().write(|v| v.0 = 0x54);
    bank.sd_transfer_mode().modify(|v| v.set_dma_en(true));
    bank.sd_command_write().write(|v| {
        v.set_cmd_index(17);
        v.set_resp_type(2);
    });
    assert_eq!(bank.sd_command_read().read().0, 0x1102);
    assert_eq!(core::mem::size_of_val(&bank.sd_command_read().read()), 2);
    assert_eq!(bank.sd_transfer_mode().read().0, 0x55);
    assert_eq!(storage[4], 0xa5a5_5a5a);
    assert_eq!(
        bank.sd_command_read().as_ptr().cast::<()>(),
        bank.sd_command_write().as_ptr().cast::<()>()
    );
    assert_eq!(
        bank.sd_command_read().as_ptr() as usize - bank.as_ptr() as usize,
        0x0e
    );
}

#[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
#[test]
fn k3_system_gpio_sparse_banks_keep_documented_order() {
    let mut storage = [0u32; 0x200 / 4];
    let bank = unsafe { pac::k3_system::Gpio::from_ptr(storage.as_mut_ptr().cast()) };
    for (index, offset) in [0, 0x40, 0x80, 0x100].into_iter().enumerate() {
        assert_eq!(
            bank.bank(index).as_ptr() as usize - bank.as_ptr() as usize,
            offset
        );
    }
    assert!(std::panic::catch_unwind(|| bank.bank(4)).is_err());
}

#[cfg(feature = "k1-rcpu")]
#[test]
fn k1_gpio_psn_native32_masks_cover_all_four_sparse_banks() {
    let mut storage = [0xa5a5_5a5au32; 0x200 / 4];
    let bank = unsafe { pac::k1_io::Gpio::from_ptr(storage.as_mut_ptr().cast()) };
    for (index, offset) in [0x18, 0x1c, 0x20, 0x118].into_iter().enumerate() {
        assert_eq!(
            bank.gpio_psr(index).as_ptr() as usize - bank.as_ptr() as usize,
            offset
        );
        for bit in [0, 15, 31] {
            let before = storage;
            bank.gpio_psr(index).write(|v| v.set_psn(1u32 << bit));
            assert_eq!(storage[offset / 4], 1u32 << bit);
            for (word, previous) in before.into_iter().enumerate() {
                if word != offset / 4 {
                    assert_eq!(storage[word], previous);
                }
            }
        }
    }
    assert!(std::panic::catch_unwind(|| bank.gpio_psr(4)).is_err());
}

#[cfg(feature = "k1-rcpu")]
#[test]
fn k1_gpu_slc_ctrl_bypass_uses_accessor_proven_native32_footprint() {
    #[repr(align(8))]
    struct Storage([u32; 0x3840 / 4]);
    let mut storage = Storage([0xa5a5_5a5a; 0x3840 / 4]);
    let mut instance = pac::soc::peripherals::AP_K1_MEDIA_GPU_RGX;
    assert_eq!(instance.address, 0xcac0_0000);
    instance.address = storage.0.as_mut_ptr() as u64;
    let bank = unsafe { instance.peripheral() };
    let before = storage.0;
    bank.rgx_cr_slc_ctrl_bypass_write_view()
        .write(|v| v.set_req_host(true));
    assert_eq!(bank.rgx_cr_slc_ctrl_bypass().read().0, 1 << 12);
    assert_eq!(
        core::mem::size_of_val(&bank.rgx_cr_slc_ctrl_bypass().read()),
        4
    );
    assert_eq!(
        bank.rgx_cr_slc_ctrl_bypass().as_ptr() as usize - bank.as_ptr() as usize,
        0x3828
    );
    assert_eq!(
        bank.rgx_cr_slc_ctrl_bypass().as_ptr(),
        bank.rgx_cr_slc_ctrl_bypass_write_view().as_ptr()
    );
    for (word, previous) in before.into_iter().enumerate() {
        if word != 0x3828 / 4 {
            assert_eq!(storage.0[word], previous);
        }
    }
}

#[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
#[test]
fn k3_clock_requests_use_direct_native_words_with_configuration_fields() {
    let mut storage = [0u32; 0x400 / 4];
    let ap = unsafe { pac::k3_system::Pmuap::from_ptr(storage.as_mut_ptr().cast()) };
    storage[0x300 / 4] = 0x1007;
    storage[0x304 / 4] = 0xa5a5_5a5a;
    ap.pmu_cci_clk_ctrl_write().write(|v| {
        v.set_cci550_biu_clk_div(2);
        v.set_cci550_pllsel(1);
    });
    assert_eq!(ap.pmu_cci_clk_ctrl_read().read().0, 0x201);
    assert_eq!(storage[0x304 / 4], 0xa5a5_5a5a);
    ap.pmu_lcd_clk_rst_ctrl5_write().write(|v| {
        v.set_dpu_aclk_req(true);
        v.set_dpu_aclk_sel(3);
        v.set_dpu_aclk_div(2);
    });
    assert_eq!(
        ap.pmu_lcd_clk_rst_ctrl5_read().read().0,
        (1 << 31) | (3 << 20) | (2 << 17)
    );
    ap.pmua_cpu_c0_clk_ctrl_write().write(|v| {
        v.set_c0_clk_fc_req(true);
        v.set_c0_core_clk_div(3);
        v.set_c0_clk_sel(2);
    });
    assert_eq!(ap.pmua_cpu_c0_clk_ctrl_read().read().0, 0x101a);
    let rcpu = unsafe { pac::k3_system::RcpuPmu::from_ptr(storage.as_mut_ptr().cast()) };
    rcpu.rcpu_bus_clk_ctrl_write().write(|v| {
        v.set_bus_clk_fc_req(true);
        v.set_axi_clk_div(3);
        v.set_apb_clk_div(2);
    });
    assert_eq!(rcpu.rcpu_bus_clk_ctrl_read().read().0, 0x11a);
    // RAM demonstrates direct stores, not hardware self-clearing behavior.
}

#[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
#[test]
fn k3_timer_restart_and_w0c_status_preserve_native32_readback() {
    let mut storage = [0u32; 0x100 / 4];
    let bank = unsafe { pac::k3_system::TimerWatchdog::from_ptr(storage.as_mut_ptr().cast()) };
    storage[2] = 7;
    storage[3] = 0xa5a5_5a5a;
    bank.timer_count_restart_write().write(|v| v.set_t1rs(true));
    assert_eq!(bank.timer_count_restart_read().read().0, 2);
    assert_eq!(storage[3], 0xa5a5_5a5a);
    storage[0xc4 / 4] = 0x1234_5678;
    bank.timer_watchdog_status_write()
        .write(|v| v.set_wts(false));
    assert_eq!(bank.timer_watchdog_status_read().read().0, 0);
    bank.timer_watchdog_status_write()
        .write(|v| v.set_wts(true));
    assert_eq!(bank.timer_watchdog_status_read().read().0, 1);
    assert_eq!(storage[0xc4 / 4], 0x1234_5678);
    assert_eq!(
        bank.timer_count_restart_read().as_ptr().cast::<()>(),
        bank.timer_count_restart_write().as_ptr().cast::<()>()
    );
    assert_eq!(
        bank.timer_watchdog_status_read().as_ptr().cast::<()>(),
        bank.timer_watchdog_status_write().as_ptr().cast::<()>()
    );
    // These are RAM store values, not emulation of restart or write-zero-clear.
}

#[cfg(any(feature = "k3-rcpu0", feature = "k3-rcpu1"))]
#[test]
fn k3_mdio_gd_uses_16_field_bits_in_a_native32_data_word() {
    let mut storage = [0u32; 0x210 / 4];
    let bank = unsafe { pac::k3_io::Gmac::from_ptr(storage.as_mut_ptr().cast()) };
    storage[0x204 / 4] = 0x1234_abcd;
    assert_eq!(bank.mdio_data().read().gd(), 0xabcd);
    storage[0x208 / 4] = 0xa5a5_5a5a;
    bank.mdio_data().write(|v| v.set_gd(0xbeef));
    assert_eq!(storage[0x204 / 4], 0xbeef);
    assert_eq!(storage[0x208 / 4], 0xa5a5_5a5a);
    assert_eq!(core::mem::size_of_val(&bank.mdio_data().read()), 4);
}
