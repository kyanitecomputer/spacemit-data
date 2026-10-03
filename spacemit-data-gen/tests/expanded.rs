//! Scratch manifests exercise the seam without checking in numeric expansion data.
use chiptool::ir::{Access, Array, BitOffset, BlockItemInner, CursedArray, IR, RegularArray};
use spacemit_data_gen::{Policy, expanded, generate, load, validate};
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::atomic::{AtomicUsize, Ordering},
};

static NEXT: AtomicUsize = AtomicUsize::new(0);
struct Scratch(PathBuf);
impl Scratch {
    fn new() -> Self {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let path = std::env::temp_dir().join(format!(
            "spacemit-generator-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        for dir in ["data/chips", "data/registers", "provenance"] {
            fs::create_dir_all(path.join(dir)).unwrap();
            for e in fs::read_dir(root.join(dir)).unwrap() {
                let e = e.unwrap();
                if e.file_type().unwrap().is_file() {
                    fs::copy(e.path(), path.join(dir).join(e.file_name())).unwrap();
                }
            }
        }
        fs::create_dir_all(path.join("provenance/expanded")).unwrap();
        Self(path)
    }
    fn write(&self, path: &str, text: &str) {
        fs::write(self.0.join(path), text).unwrap();
    }
    fn manifest(&self, chip: &str, lane: &str) {
        let view = if chip == "k1" { "ap" } else { "secure" };
        let name = format!("{}_{}_TEST", view.to_uppercase(), chip.to_uppercase());
        self.write(&format!("provenance/expanded/{lane}.json"), &format!(r#"{{
            "schema":1,"chip":"{chip}","registers":["expanded_{chip}_test"],
            "sources":[{{"path":"test.md","sha256":"{}"}}],
            "instances":[{{"name":"{name}","block":"{chip}_test::Bank","address":4294967296,"view":"{view}","source":{{"path":"test.md","line":1}}}}],
            "aliases":{{"{chip}_test::Bank":[["status","clear"]]}},
            "write_mask_registers":["{chip}_test::Bank.clear"],
            "tables":[{{"source":"test.md","line":{},"status":"generated","registers":["{chip}_test::Bank.status","{chip}_test::Bank.clear","{chip}_test::Bank.bytes","{chip}_test::Bank.halves","{chip}_test::Bank.native_counter"]}}],
            "corrections":[]
        }}"#, "0".repeat(64), if chip == "k1" { 1 } else { 2 }));
        self.write(&format!("data/registers/expanded_{chip}_test.yaml"), &format!(r#"{{
            "block/{chip}_test::Bank":{{"items":[
                {{"name":"status","byte_offset":0,"access":"Read","fieldset":"{chip}_test::regs::Status"}},
                {{"name":"clear","byte_offset":0,"access":"Write"}},
                {{"name":"bytes","byte_offset":4,"bit_size":8,"array":{{"offsets":[0,2,3]}}}},
                {{"name":"halves","byte_offset":8,"bit_size":16,"array":{{"len":2,"stride":2}}}},
                {{"name":"native_counter","byte_offset":16,"bit_size":64,"access":"Read","fieldset":"{chip}_test::regs::Counter"}}
            ]}},
            "fieldset/{chip}_test::regs::Counter":{{"bit_size":64,"fields":[{{"name":"count","bit_offset":0,"bit_size":64}}]}},
            "fieldset/{chip}_test::regs::Status":{{"bit_size":32,"fields":[
                {{"name":"lanes","bit_offset":0,"bit_size":1,"array":{{"offsets":[0,3,7]}}}},
                {{"name":"split","bit_offset":[[8,9],[12,13]],"bit_size":4}}
            ]}}
        }}"#));
    }
    fn replace(&self, path: &str, old: &str, new: &str) {
        let value = fs::read_to_string(self.0.join(path)).unwrap();
        assert!(value.contains(old));
        self.write(path, &value.replace(old, new));
    }
}
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn shared_source_records_are_unique_per_manifest_not_across_lanes() {
    let s = Scratch::new();
    s.manifest("k1", "k1");
    s.manifest("k3", "k3");
    s.replace("provenance/expanded/k3.json", "\"line\":2", "\"line\":1");
    assert_eq!(expanded::discover(&s.0).unwrap().len(), 2);
    let path = "provenance/expanded/k3.json";
    let mut manifest: serde_yaml::Value =
        serde_yaml::from_str(&fs::read_to_string(s.0.join(path)).unwrap()).unwrap();
    let record = manifest["tables"][0].clone();
    manifest["tables"].as_sequence_mut().unwrap().push(record);
    s.write(path, &serde_yaml::to_string(&manifest).unwrap());
    assert!(
        expanded::discover(&s.0)
            .unwrap_err()
            .to_string()
            .contains("duplicate table accounting")
    );
}

#[test]
fn sorted_family_loading_keeps_old_api_and_typed_views_on_all_targets() {
    let s = Scratch::new();
    s.manifest("k3", "a_k3");
    s.manifest("k1", "z_k1");
    let path = "provenance/expanded/z_k1.json";
    let manifest = fs::read_to_string(s.0.join(path)).unwrap();
    let extra = r#"{"name":"RCPU_K1_TEST","block":"k1_test::Bank","address":4096,"view":"rcpu","source":{"path":"test.md","line":1}}"#;
    s.write(
        path,
        &manifest.replace("\"instances\":[", &format!("\"instances\":[{extra},")),
    );
    let manifests = expanded::discover(&s.0).unwrap();
    assert_eq!(manifests[0].chip, expanded::Family::K3);
    let a = generate(&s.0).unwrap();
    assert_eq!(a, generate(&s.0).unwrap());
    for feature in ["k1-rcpu", "k3-rcpu0", "k3-rcpu1"] {
        let text = &a[&PathBuf::from(format!("spacemit-pac/src/{}.rs", feature.replace('-', "_")))];
        assert!(text.contains("pub mod pxa_uart") && text.contains("pub const HART_ID"));
        assert!(
            text.contains("pub struct Instance<T>")
                && (text.contains("source_line: 1") || text.contains("source_line: 2"))
        );
        if feature == "k1-rcpu" {
            assert!(text.contains("AP_K1_TEST") && text.contains("AddressView::Ap"));
            assert!(!text.contains("SECURE_K3_TEST"));
            assert!(text.contains("RCPU_K1_TEST") && text.contains("AddressView::Rcpu"));
            assert!(text.contains("pub const fn mtime_lo(")); // Existing split-u32 API.
        } else {
            assert!(text.contains("SECURE_K3_TEST") && text.contains("AddressView::Secure"));
            assert!(!text.contains("AP_K1_TEST"));
            assert!(text.contains("Reg<u64"));
        }
        assert!(text.contains("Reg<u8") && text.contains("Reg<u16"));
        let source = s.0.join("pac.rs");
        fs::write(&source, format!("#![no_std]\n{text}")).unwrap();
        let target = if feature == "k1-rcpu" {
            "riscv32imafc-unknown-none-elf"
        } else {
            "riscv64gc-unknown-none-elf"
        };
        for target in [None, Some(target)] {
            let mut cmd = Command::new("rustc");
            cmd.args([
                "--edition",
                "2024",
                "--crate-type",
                "lib",
                "--crate-name",
                "scratch_pac",
            ])
            .arg(&source)
            .arg("-o")
            .arg(s.0.join("pac.rlib"));
            if let Some(target) = target {
                cmd.args(["--target", target]);
            }
            let output = cmd.output().unwrap();
            assert!(
                output.status.success(),
                "{}",
                String::from_utf8_lossy(&output.stderr)
            );
        }
    }
    // Execute field-array/noncontiguous helpers and the typed constructor on host
    // storage, never on the source address. No allocator/dependencies in the PAC.
    let text = &a[&PathBuf::from("spacemit-pac/src/k1_rcpu.rs")];
    let text = text.replace("address: 0x100000000,", "address: 0,");
    fs::write(s.0.join("pac.rs"), format!("#![no_std]\n{text}")).unwrap();
    let out = Command::new("rustc")
        .args([
            "--edition",
            "2024",
            "--crate-type",
            "lib",
            "--crate-name",
            "scratch_pac",
        ])
        .arg(s.0.join("pac.rs"))
        .arg("-o")
        .arg(s.0.join("libscratch_pac.rlib"))
        .output()
        .unwrap();
    assert!(out.status.success());
    s.write(
        "probe.rs",
        r#"
        extern crate scratch_pac as pac;
        fn main() {
            let mut words = [0u64; 4];
            let mut instance = pac::soc::peripherals::AP_K1_TEST;
            assert_eq!(instance.view, pac::soc::AddressView::Ap);
            assert_eq!(instance.source_path, "test.md");
            instance.address = words.as_mut_ptr() as u64;
            let bank = unsafe { instance.peripheral() };
            bank.clear().write_value(0x5);
            assert_eq!(bank.status().read().0, 5);
            assert_eq!(bank.bytes(1).as_ptr() as usize - bank.as_ptr() as usize, 6);
            bank.halves(1).write_value(0x1234);
            let mut status = bank.status().read();
            status.set_lanes(2, true);
            status.set_split(0b1011);
            assert!(status.lanes(2));
            assert_eq!(status.split(), 0b1011);
            let mut count = pac::k1_test::regs::Counter(0);
            count.set_count(0xfedcba9876543210);
            assert_eq!(count.count(), 0xfedcba9876543210);
            assert!(std::panic::catch_unwind(|| bank.bytes(3)).is_err());
            assert!(std::panic::catch_unwind(|| status.lanes(3)).is_err());
        }
    "#,
    );
    let out = Command::new("rustc")
        .arg(s.0.join("probe.rs"))
        .arg("--extern")
        .arg(format!(
            "scratch_pac={}",
            s.0.join("libscratch_pac.rlib").display()
        ))
        .arg("-o")
        .arg(s.0.join("probe"))
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let output = Command::new(s.0.join("probe")).output().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn strict_manifest_metadata_rejects_malformed_inputs() {
    for (old, new) in [
        ("\"schema\":1", "\"schema\":2"),
        ("\"schema\":1", "\"unknown\":0,\"schema\":1"),
        ("test.md", "../test.md"),
        ("test.md", "/test.md"),
        ("\"line\":1", "\"line\":0"),
        ("\"view\":\"ap\"", "\"view\":\"rcpu\""),
        ("AP_K1_TEST", "AP_K3_TEST"),
        ("\"status\":\"generated\"", "\"status\":\"source_gap\""),
        ("\"sha256\":\"0000", "\"sha256\":\"z000"),
    ] {
        let s = Scratch::new();
        s.manifest("k1", "test");
        s.replace("provenance/expanded/test.json", old, new);
        assert!(generate(&s.0).is_err(), "accepted {old} => {new}");
    }
    for (old, new) in [
        ("k1_test::Bank.bytes", "k1_test::Missing.bytes"),
        ("k1_test::Bank.bytes", "k1_test::Bank.missing"),
        (",\"k1_test::Bank.native_counter\"", ""),
        ("\"address\":4294967296", "\"address\":3"),
        ("\"address\":4294967296", "\"address\":18446744073709551608"),
    ] {
        let s = Scratch::new();
        s.manifest("k1", "test");
        s.replace("provenance/expanded/test.json", old, new);
        assert!(generate(&s.0).is_err(), "accepted {old} => {new}");
    }
    let s = Scratch::new();
    s.manifest("k1", "test");
    s.replace(
        "data/registers/expanded_k1_test.yaml",
        "\"access\":\"Write\"",
        "\"access\":\"ReadWrite\"",
    );
    assert!(generate(&s.0).is_err());
    let s = Scratch::new();
    s.manifest("k1", "test");
    fs::copy(
        s.0.join("provenance/expanded/test.json"),
        s.0.join("provenance/expanded/duplicate.json"),
    )
    .unwrap();
    assert!(generate(&s.0).is_err());
}

#[test]
fn legitimate_field_arrays_and_sparse_ranges_have_exact_bounds_and_overlap_checks() {
    let s = Scratch::new();
    s.manifest("k1", "test");
    let path = s.0.join("data/registers/expanded_k1_test.yaml");
    let ir: IR = load(&path).unwrap();
    let policy = Policy {
        aliases: [(
            "k1_test::Bank".into(),
            vec![vec!["status".into(), "clear".into()]],
        )]
        .into(),
        write_mask_registers: vec!["k1_test::Bank.clear".into()],
    };
    validate(&ir, &policy).unwrap();
    for array in [
        Some(Array::Regular(RegularArray { len: 33, stride: 1 })),
        Some(Array::Regular(RegularArray { len: 0, stride: 1 })),
        Some(Array::Cursed(CursedArray {
            offsets: vec![0, 0],
        })),
        Some(Array::Cursed(CursedArray {
            offsets: vec![0, 8],
        })),
    ] {
        let mut bad = ir.clone();
        bad.fieldsets
            .get_mut("k1_test::regs::Status")
            .unwrap()
            .fields[0]
            .array = array;
        assert!(validate(&bad, &policy).is_err());
    }
    for offset in [
        BitOffset::Regular(7),
        BitOffset::Cursed(vec![]),
        BitOffset::Cursed(vec![8..=9, 9..=10]),
        BitOffset::Cursed(vec![12..=13, 8..=9]),
        BitOffset::Cursed(vec![8..=9, 31..=32]),
    ] {
        let mut bad = ir.clone();
        bad.fieldsets
            .get_mut("k1_test::regs::Status")
            .unwrap()
            .fields[1]
            .bit_offset = offset;
        assert!(validate(&bad, &policy).is_err());
    }
    // Aliased arrays are legitimate when every element has identical offsets/widths.
    let mut arrays = ir.clone();
    let bank = arrays.blocks.get_mut("k1_test::Bank").unwrap();
    bank.items
        .retain(|i| i.name == "status" || i.name == "clear" || i.name == "native_counter");
    for item in bank.items.iter_mut().filter(|i| i.name != "native_counter") {
        item.array = Some(Array::Regular(RegularArray { len: 2, stride: 4 }));
    }
    validate(&arrays, &policy).unwrap();
    arrays.blocks.get_mut("k1_test::Bank").unwrap().items[1].array =
        Some(Array::Cursed(CursedArray {
            offsets: vec![4, 0],
        }));
    validate(&arrays, &policy).unwrap();
    bank_mismatch(&mut arrays);
    assert!(validate(&arrays, &policy).is_err());
    fn bank_mismatch(ir: &mut IR) {
        ir.blocks.get_mut("k1_test::Bank").unwrap().items[1].array =
            Some(Array::Regular(RegularArray { len: 3, stride: 4 }));
    }
}

#[test]
fn source_native_widths_and_dynamic_metadata_remain_unchanged() {
    let s = Scratch::new();
    let outputs = generate(&s.0).unwrap();
    let k1 = &outputs[&PathBuf::from("spacemit-pac/src/k1_rcpu.rs")];
    let k3 = &outputs[&PathBuf::from("spacemit-pac/src/k3_rcpu1.rs")];
    assert!(k1.contains("Reg<u8") && k1.contains("pub const fn mtime_lo("));
    assert!(k1.contains("IREGION_ECLIC_OFFSET") && k1.contains("FALLBACK_ECLIC_BASE"));
    assert!(k3.contains("Reg<u64") && k3.contains("HART_ID: u32 = 1"));
    let mut ir: IR = load(&s.0.join("data/registers/n308_eclic.yaml")).unwrap();
    let block = ir.blocks.get_mut("n308_eclic::Irq").unwrap();
    if let BlockItemInner::Register(r) = &mut block.items[0].inner {
        r.bit_size = 32;
        r.access = Access::ReadWrite;
    }
    let policy: Policy = load(&s.0.join("provenance/policy.yaml")).unwrap();
    assert!(validate(&ir, &policy).is_err()); // Byte-spaced IRQ register overlap.
}

#[test]
fn declared_esos_supplements_can_cover_registers_without_false_markdown_locations() {
    let s = Scratch::new();
    s.manifest("k1", "test");
    let path = "provenance/expanded/test.json";
    let manifest = fs::read_to_string(s.0.join(path)).unwrap();
    let source = format!(
        r#"{{"path":"bank.h","kind":"esos","sha256":"{}"}}"#,
        "0".repeat(64)
    );
    let record = r#"{"source":"bank.h","line":42,"status":"generated","registers":["k1_test::Bank.native_counter"],"note":"C struct declares native counter width."}"#;
    s.write(
        path,
        &manifest
            .replace("\"sources\":[", &format!("\"sources\":[{source},"))
            .replace(",\"k1_test::Bank.native_counter\"", "")
            .replace("\"tables\":[", &format!("\"tables\":[{record},")),
    );
    generate(&s.0).unwrap();
    s.replace(path, "\"source\":\"bank.h\"", "\"source\":\"undeclared.h\"");
    assert!(generate(&s.0).is_err());
}

#[test]
fn cyclic_or_missing_extends_are_rejected_before_chiptool_transform() {
    for (old, new) in [
        (
            r#""block/k1_test::Bank":{"items""#,
            r#""block/k1_test::Bank":{"extends":"k1_test::Bank","items""#,
        ),
        (
            r#""fieldset/k1_test::regs::Status":{"bit_size""#,
            r#""fieldset/k1_test::regs::Status":{"extends":"k1_test::regs::Status","bit_size""#,
        ),
        (
            r#""block/k1_test::Bank":{"items""#,
            r#""block/k1_test::Bank":{"extends":"k1_test::Missing","items""#,
        ),
    ] {
        let s = Scratch::new();
        s.manifest("k1", "test");
        s.replace("data/registers/expanded_k1_test.yaml", old, new);
        assert!(generate(&s.0).is_err());
    }
}
