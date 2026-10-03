//! Reviewed YAML to chiptool Rust. No vendor manual import is performed here.
use anyhow::{Context, Result, bail, ensure};
use chiptool::generate::rust::{self, DefmtOption, Options, RustArch};
use chiptool::ir::{Access, Array, BitOffset, BlockItemInner, IR};
use serde::Deserialize;
pub mod expanded;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::Write,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Chip {
    pub feature: String,
    pub pointer_width: u32,
    pub hart_id: u32,
    pub registers: Vec<String>,
    pub constants: BTreeMap<String, u64>,
    pub provenance: String,
}
#[derive(Debug, Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    pub aliases: BTreeMap<String, Vec<Vec<String>>>,
    pub write_mask_registers: Vec<String>,
}

pub fn load<T: serde::de::DeserializeOwned>(path: &Path) -> Result<T> {
    serde_yaml::from_slice(&fs::read(path)?).with_context(|| format!("parse {}", path.display()))
}

fn offsets(base: u32, array: &Option<Array>) -> Result<Vec<u64>> {
    let offsets = match array {
        None => vec![0],
        Some(Array::Regular(a)) => {
            ensure!(a.len > 0 && a.len <= 4096 && a.stride > 0, "invalid array");
            (0..a.len)
                .map(|i| u64::from(i) * u64::from(a.stride))
                .collect()
        }
        Some(Array::Cursed(a)) => {
            ensure!(
                !a.offsets.is_empty() && a.offsets.len() <= 4096,
                "invalid sparse array"
            );
            ensure!(
                a.offsets.iter().collect::<BTreeSet<_>>().len() == a.offsets.len(),
                "duplicate sparse offsets"
            );
            a.offsets.iter().map(|i| u64::from(*i)).collect()
        }
    };
    offsets
        .into_iter()
        .map(|o| {
            let end = u64::from(base) + o;
            ensure!(end <= u32::MAX as u64, "offset overflow");
            Ok(end)
        })
        .collect()
}

fn block_size(ir: &IR, name: &str, stack: &mut BTreeSet<String>) -> Result<u64> {
    ensure!(stack.insert(name.to_owned()), "recursive block {name}");
    let block = ir
        .blocks
        .get(name)
        .with_context(|| format!("missing block {name}"))?;
    let mut size = 0;
    for item in &block.items {
        let bytes = match &item.inner {
            BlockItemInner::Register(r) => {
                ensure!([8, 16, 32, 64].contains(&r.bit_size), "invalid MMIO width");
                u64::from(r.bit_size / 8)
            }
            BlockItemInner::Block(b) => block_size(ir, &b.block, stack)?,
        };
        for offset in offsets(item.byte_offset, &item.array)? {
            size = size.max(offset + bytes);
        }
    }
    stack.remove(name);
    Ok(size)
}

fn validate_fields(ir: &IR) -> Result<()> {
    let all_names: BTreeSet<_> = ir
        .blocks
        .keys()
        .chain(ir.fieldsets.keys())
        .chain(ir.enums.keys())
        .collect();
    ensure!(
        all_names.len() == ir.blocks.len() + ir.fieldsets.len() + ir.enums.len(),
        "duplicate IR type paths"
    );
    for name in ir
        .blocks
        .keys()
        .chain(ir.fieldsets.keys())
        .chain(ir.enums.keys())
    {
        ensure!(expanded::qualified(name), "invalid IR identifier {name}");
    }
    let mut used_enums = BTreeSet::new();
    for (name, fs) in &ir.fieldsets {
        ensure!(
            [8, 16, 32, 64].contains(&fs.bit_size),
            "invalid fieldset width"
        );
        let mut occupied = BTreeSet::new();
        let mut names = BTreeSet::new();
        for field in &fs.fields {
            ensure!(
                expanded::identifier(&field.name) && names.insert(&field.name),
                "invalid/duplicate field name"
            );
            ensure!(
                field.bit_size > 0 && field.bit_size <= fs.bit_size,
                "invalid field width"
            );
            let bits: Vec<u64> = match &field.bit_offset {
                BitOffset::Regular(start) => (0..field.bit_size)
                    .map(|i| u64::from(*start) + u64::from(i))
                    .collect(),
                BitOffset::Cursed(ranges) => {
                    ensure!(!ranges.is_empty(), "empty bit ranges");
                    let mut bits = Vec::new();
                    let mut previous = None;
                    for range in ranges {
                        ensure!(
                            range.start() <= range.end() && *range.end() < fs.bit_size,
                            "invalid bit range"
                        );
                        ensure!(
                            previous.is_none_or(|p| p < *range.start()),
                            "unordered/overlapping bit ranges"
                        );
                        previous = Some(*range.end());
                        bits.extend(range.clone().map(u64::from));
                    }
                    ensure!(
                        bits.len() == field.bit_size as usize,
                        "bit range width mismatch"
                    );
                    bits
                }
            };
            for offset in offsets(0, &field.array)? {
                for bit in &bits {
                    let bit = offset + bit;
                    ensure!(
                        bit < u64::from(fs.bit_size),
                        "field out of bounds {name}.{}",
                        field.name
                    );
                    ensure!(occupied.insert(bit), "field overlap {name}.{}", field.name);
                }
            }
            if let Some(ename) = &field.enumm {
                let e = ir.enums.get(ename).context("missing field enum")?;
                ensure!(e.bit_size == field.bit_size, "enum width mismatch");
                used_enums.insert(ename);
            }
        }
    }
    for (name, e) in &ir.enums {
        ensure!(
            e.bit_size > 0 && e.bit_size <= 64 && used_enums.contains(name),
            "invalid/unused enum"
        );
        let mut names = BTreeSet::new();
        let mut values = BTreeSet::new();
        for v in &e.variants {
            ensure!(
                expanded::identifier(&v.name) && names.insert(&v.name) && values.insert(v.value),
                "invalid/duplicate enum variant"
            );
            ensure!(
                e.bit_size == 64 || v.value < (1u64 << e.bit_size),
                "enum value out of bounds"
            );
        }
    }
    Ok(())
}

fn block_alignment(ir: &IR, name: &str) -> u64 {
    ir.blocks[name]
        .items
        .iter()
        .map(|i| match &i.inner {
            BlockItemInner::Register(r) => u64::from(r.bit_size / 8),
            BlockItemInner::Block(b) => block_alignment(ir, &b.block),
        })
        .max()
        .unwrap_or(1)
}

/// Validate overlaps including every array element; only exact reviewed alias sets pass.
pub fn validate(ir: &IR, policy: &Policy) -> Result<()> {
    validate_fields(ir)?;
    // Own bit/enum validation: upstream ignores arrays and misses single-bit
    // overlap endpoints, and its enum bound expression overflows for u64.
    let mut structural = ir.clone();
    structural.enums.clear();
    for fs in structural.fieldsets.values_mut() {
        for f in &mut fs.fields {
            f.enumm = None;
        }
    }
    let errors = chiptool::validate::validate(
        &structural,
        chiptool::validate::Options {
            allow_register_overlap: true, // Our stricter alias/byte-range check below owns overlaps.
            allow_field_overlap: true,
            allow_enum_dup_value: false,
            allow_unused_enums: false,
            allow_unused_fieldsets: false,
        },
    );
    ensure!(errors.is_empty(), "chiptool validation: {errors:?}");
    for (name, block) in &ir.blocks {
        let mut spans = Vec::new();
        let mut names = BTreeSet::new();
        for item in &block.items {
            ensure!(
                expanded::identifier(&item.name) && names.insert(&item.name),
                "duplicate item {name}.{}",
                item.name
            );
            let (bytes, alignment) = match &item.inner {
                BlockItemInner::Register(r) => {
                    ensure!([8, 16, 32, 64].contains(&r.bit_size), "invalid MMIO width");
                    if let Some(fs) = &r.fieldset {
                        ensure!(
                            ir.fieldsets[fs].bit_size == r.bit_size,
                            "fieldset width mismatch"
                        );
                    }
                    (u64::from(r.bit_size / 8), u64::from(r.bit_size / 8))
                }
                BlockItemInner::Block(b) => (
                    block_size(ir, &b.block, &mut BTreeSet::new())?,
                    block_alignment(ir, &b.block),
                ),
            };
            for offset in offsets(item.byte_offset, &item.array)? {
                ensure!(offset % alignment == 0, "misaligned {name}.{}", item.name);
                ensure!(offset + bytes <= u32::MAX as u64 + 1, "range overflow");
                spans.push((offset, offset + bytes, item.name.as_str()));
            }
        }
        let allowed = policy.aliases.get(name).cloned().unwrap_or_default();
        for group in &allowed {
            ensure!(group.len() >= 2, "alias needs at least two names");
            ensure!(
                group.iter().collect::<BTreeSet<_>>().len() == group.len(),
                "duplicate alias names"
            );
            let mut positions = BTreeSet::new();
            for alias in group {
                let item = block
                    .items
                    .iter()
                    .find(|i| &i.name == alias)
                    .context("stale alias policy")?;

                let BlockItemInner::Register(r) = &item.inner else {
                    bail!("block alias unsupported")
                };
                let mut alias_offsets = offsets(item.byte_offset, &item.array)?;
                alias_offsets.sort_unstable();
                positions.insert((alias_offsets, r.bit_size));
            }
            ensure!(positions.len() == 1, "alias offsets/widths differ");
        }
        spans.sort_unstable();
        for (i, a) in spans.iter().enumerate() {
            for b in &spans[i + 1..] {
                if b.0 >= a.1 {
                    break;
                }
                if a.0 < b.1 && b.0 < a.1 {
                    ensure!(
                        a.0 == b.0
                            && a.1 == b.1
                            && a.2 != b.2
                            && allowed
                                .iter()
                                .any(|g| g.iter().any(|x| x == a.2) && g.iter().any(|x| x == b.2)),
                        "unapproved overlap {name}: {} / {}",
                        a.2,
                        b.2
                    );
                }
            }
        }
    }
    for qualified in &policy.write_mask_registers {
        let (block, name) = qualified.rsplit_once('.').context("mask policy format")?;
        if let Some(block) = ir.blocks.get(block) {
            let item = block
                .items
                .iter()
                .find(|i| i.name == name)
                .context("stale write-mask policy")?;
            let BlockItemInner::Register(r) = &item.inner else {
                bail!("mask is not register")
            };
            ensure!(
                r.access == Access::Write,
                "mask register must be Write-only: {qualified}"
            );
        }
    }
    Ok(())
}

fn format_rust(source: &str) -> Result<String> {
    let mut process = Command::new("rustfmt")
        .args(["--edition", "2024", "--emit", "stdout"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    process.stdin.take().unwrap().write_all(source.as_bytes())?;
    let output = process.wait_with_output()?;
    ensure!(
        output.status.success(),
        "rustfmt: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(String::from_utf8(output.stdout)?)
}

// chiptool's build.rs probes git in its build directory. Normalize only that
// incidental header so pinned git and a verified source-archive override agree.
fn normalize_header(formatted: &str) -> Result<String> {
    let mut count = 0;
    let mut result = String::new();
    for line in formatted.lines() {
        if line.starts_with("#![doc = \"Peripheral access API (generated using chiptool ") {
            count += 1;
            result.push_str(
                "#![doc = \"Generated using chiptool 4e232f0ac28f18f0901ae930dabf2b5b62e1a05f\"]\n",
            );
        } else {
            result.push_str(line);
            result.push('\n');
        }
    }
    ensure!(count == 1, "unexpected chiptool header format");
    Ok(result)
}

fn validate_extends(ir: &IR) -> Result<()> {
    // Pinned chiptool's topological sort does not terminate on an extends cycle.
    for parents in [
        ir.blocks
            .iter()
            .map(|(n, b)| (n.as_str(), b.extends.as_deref()))
            .collect::<BTreeMap<_, _>>(),
        ir.fieldsets
            .iter()
            .map(|(n, f)| (n.as_str(), f.extends.as_deref()))
            .collect::<BTreeMap<_, _>>(),
    ] {
        for name in parents.keys() {
            let mut seen = BTreeSet::new();
            let mut current = Some(*name);
            while let Some(name) = current {
                ensure!(seen.insert(name), "recursive extends {name}");
                current = *parents
                    .get(name)
                    .with_context(|| format!("missing extends parent {name}"))?;
            }
        }
    }
    Ok(())
}

fn merge_ir(ir: &mut IR, sub: IR) -> Result<()> {
    ensure!(
        sub.devices.is_empty(),
        "device runtime generation forbidden"
    );
    ensure!(
        sub.blocks.keys().all(|k| !ir.blocks.contains_key(k))
            && sub.fieldsets.keys().all(|k| !ir.fieldsets.contains_key(k))
            && sub.enums.keys().all(|k| !ir.enums.contains_key(k)),
        "duplicate IR names"
    );
    ir.merge(sub);
    Ok(())
}

pub fn generate(root: &Path) -> Result<BTreeMap<PathBuf, String>> {
    let base_policy: Policy = load(&root.join("provenance/policy.yaml"))?;
    let manifests = expanded::discover(root)?;
    let mut outputs = BTreeMap::new();
    let mut lib = String::from(
        "// Generated by spacemit-data-gen; DO NOT EDIT.\n#![no_std]\n#![doc = include_str!(\"../ACCESS.md\")]\n#[cfg(all(target_arch = \"riscv32\", any(feature = \"k3-rcpu0\", feature = \"k3-rcpu1\")))] compile_error!(\"K3 requires RV64\");\n#[cfg(all(target_arch = \"riscv64\", feature = \"k1-rcpu\"))] compile_error!(\"K1 requires RV32\");\n",
    );
    let features = ["k1-rcpu", "k3-rcpu0", "k3-rcpu1"];
    for (i, feature) in features.iter().enumerate() {
        for other in &features[i + 1..] {
            lib.push_str(&format!("#[cfg(all(feature = {feature:?}, feature = {other:?}))] compile_error!(\"select at most one auxiliary core\");\n"));
        }
        let chip: Chip = load(&root.join(format!("data/chips/{feature}.yaml")))?;
        ensure!(chip.feature == *feature, "feature mismatch");
        ensure!([32, 64].contains(&chip.pointer_width), "pointer width");
        ensure!(root.join(&chip.provenance).is_file(), "missing provenance");
        let mut ir = IR::new();
        ensure!(
            expanded::relative(&chip.provenance),
            "invalid provenance path"
        );
        for block in &chip.registers {
            ensure!(expanded::identifier(block), "register file identifier");
            let sub: IR = load(&root.join(format!("data/registers/{block}.yaml")))?;
            merge_ir(&mut ir, sub)?;
        }
        let family = if *feature == "k1-rcpu" {
            expanded::Family::K1
        } else {
            expanded::Family::K3
        };
        ensure!(
            chip.pointer_width
                == if family == expanded::Family::K1 {
                    32
                } else {
                    64
                },
            "feature pointer width mismatch"
        );
        ensure!(
            chip.hart_id == if *feature == "k3-rcpu1" { 1 } else { 0 },
            "feature hart mismatch"
        );
        let mut policy = base_policy.clone();
        expanded::merge(root, family, &manifests, &mut ir, &mut policy)?;
        validate_extends(&ir)?;
        chiptool::transform::expand_extends::ExpandExtends {}.run(&mut ir)?;
        validate(&ir, &policy)?;
        expanded::validate_references(root, family, &manifests, &ir)?;
        chiptool::transform::sort::Sort {}.run(&mut ir)?;
        let mut render_ir = ir.clone();
        // Pinned chiptool computes wrapping_shl(64)-1 as zero. Two 32-bit
        // ranges preserve the exact u64 getter/setter API and every source bit.
        for fs in render_ir.fieldsets.values_mut() {
            for field in &mut fs.fields {
                if field.bit_size == 64 {
                    field.bit_offset = BitOffset::Cursed(vec![0..=31, 32..=63]);
                }
            }
        }
        ensure!(
            render_ir.enums.values().all(|e| e.bit_size < 64),
            "pinned chiptool cannot render 64-bit enums; explicit renderer support required"
        );
        let mut source = String::from("// Generated by spacemit-data-gen; DO NOT EDIT.\n");
        source.push_str(
            &rust::render(
                &render_ir,
                &Options::new()
                    .with_skip_no_std(true)
                    .with_arch(RustArch::RiscV)
                    .with_defmt(DefmtOption::Disabled),
            )?
            .to_string(),
        );
        source.push_str(&format!(
            "\npub mod chip {{ pub const HART_ID: u32 = {};\n",
            chip.hart_id
        ));
        for (name, value) in chip.constants {
            ensure!(
                name.chars()
                    .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_'),
                "constant identifier"
            );
            ensure!(expanded::identifier(&name), "constant identifier");
            if chip.pointer_width == 32 {
                ensure!(value <= u32::MAX as u64, "RV32 constant overflow");
            }
            source.push_str(&format!("pub const {name}: u64 = 0x{value:x};\n"));
        }
        source.push_str("}\n");
        source.push_str(&expanded::render(family, &manifests, &ir)?);
        let module = feature.replace('-', "_");
        lib.push_str(&format!(
            "#[cfg(feature = {feature:?})] #[path = \"{module}.rs\"] mod selected;\n"
        ));
        outputs.insert(
            PathBuf::from(format!("spacemit-pac/src/{module}.rs")),
            normalize_header(&format_rust(&source)?)?,
        );
    }
    lib.push_str("#[cfg(any(feature = \"k1-rcpu\", feature = \"k3-rcpu0\", feature = \"k3-rcpu1\"))] pub use selected::*;\n");
    outputs.insert(PathBuf::from("spacemit-pac/src/lib.rs"), format_rust(&lib)?);
    Ok(outputs)
}

pub fn update(root: &Path, check: bool) -> Result<()> {
    for (path, source) in generate(root)? {
        let path = root.join(path);
        if fs::read_to_string(&path).ok().as_ref() != Some(&source) {
            ensure!(!check, "stale generated file: {}", path.display());
            fs::write(path, source)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn root() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .to_owned()
    }
    fn uart() -> IR {
        load(&root().join("data/registers/pxa_uart.yaml")).unwrap()
    }
    fn policy() -> Policy {
        load(&root().join("provenance/policy.yaml")).unwrap()
    }
    #[test]
    fn generation_is_deterministic_and_fresh() {
        let a = generate(&root()).unwrap();
        let b = generate(&root()).unwrap();
        assert_eq!(a, b);
        update(&root(), true).unwrap();
        assert!(
            a[&PathBuf::from("spacemit-pac/src/k3_rcpu1.rs")].contains("0x0000_0001_0080_0000")
                || a[&PathBuf::from("spacemit-pac/src/k3_rcpu1.rs")].contains("0x100800000")
        );
    }
    #[test]
    fn only_reviewed_aliases_pass() {
        validate(&uart(), &policy()).unwrap();
        let mut ir = uart();
        ir.blocks.get_mut("pxa_uart::Uart").unwrap().items[0].byte_offset = 12;
        assert!(validate(&ir, &policy()).is_err());
        let mut p = policy();
        p.aliases.clear();
        assert!(validate(&uart(), &p).is_err());
    }
    #[test]
    fn bounds_alignment_and_arrays_are_checked() {
        let mut ir = uart();
        ir.blocks
            .get_mut("pxa_uart::Uart")
            .unwrap()
            .items
            .last_mut()
            .unwrap()
            .byte_offset = 29;
        assert!(validate(&ir, &policy()).is_err());
        let mut ir = uart();
        ir.fieldsets.get_mut("pxa_uart::regs::Lsr").unwrap().fields[0].bit_offset =
            BitOffset::Regular(32);
        assert!(validate(&ir, &policy()).is_err());
        let mut ir: IR = load(&root().join("data/registers/rt24_plic.yaml")).unwrap();
        ir.blocks.get_mut("rt24_plic::Plic").unwrap().items[0].array =
            Some(Array::Regular(chiptool::ir::RegularArray {
                len: 128,
                stride: 2,
            }));
        assert!(validate(&ir, &policy()).is_err());
    }
    #[test]
    fn mask_registers_cannot_gain_modify() {
        let mut ir: IR = load(&root().join("data/registers/k1_mailbox.yaml")).unwrap();
        validate(&ir, &policy()).unwrap();
        if let BlockItemInner::Register(r) =
            &mut ir.blocks.get_mut("k1_mailbox::Mailbox").unwrap().items[0].inner
        {
            r.access = Access::ReadWrite;
        }
        assert!(validate(&ir, &policy()).is_err());
    }
}
