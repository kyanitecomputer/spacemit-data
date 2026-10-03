//! Offline expanded-manifest seam. Source bytes are checked separately by verify-expanded.py.
use super::{Policy, block_size, load};
use anyhow::{Context, Result, ensure};
use chiptool::ir::{BlockItemInner, IR};
use serde::Deserialize;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
};

#[derive(Debug, Deserialize, PartialEq, Eq, Clone, Copy)]
#[serde(rename_all = "lowercase")]
pub enum Family {
    K1,
    K3,
}
impl Family {
    fn prefix(self) -> &'static str {
        match self {
            Self::K1 => "k1_",
            Self::K3 => "k3_",
        }
    }
    fn label(self) -> &'static str {
        match self {
            Self::K1 => "K1",
            Self::K3 => "K3",
        }
    }
}
#[derive(Debug, Deserialize, Clone, Copy)]
#[serde(rename_all = "lowercase")]
pub enum View {
    Ap,
    Rcpu,
    Secure,
}
impl View {
    fn label(self) -> &'static str {
        match self {
            Self::Ap => "AP",
            Self::Rcpu => "RCPU",
            Self::Secure => "SECURE",
        }
    }
}
#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum SourceKind {
    #[default]
    Docs,
    Esos,
    Linux,
    Uboot,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Source {
    pub path: String,
    pub sha256: String,
    #[serde(default)]
    pub kind: SourceKind,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Location {
    pub path: String,
    pub line: u32,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Instance {
    pub name: String,
    pub block: String,
    pub address: u64,
    pub view: View,
    pub source: Location,
}
#[derive(Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Generated,
    Duplicate,
    NotMmio,
    SourceGap,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Table {
    pub source: String,
    pub line: u32,
    pub status: Status,
    pub registers: Vec<String>,
    pub note: Option<String>,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Correction {
    pub source: String,
    pub line: u32,
    pub decision: String,
    pub evidence: String,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub schema: u32,
    pub chip: Family,
    pub registers: Vec<String>,
    pub sources: Vec<Source>,
    pub instances: Vec<Instance>,
    pub aliases: BTreeMap<String, Vec<Vec<String>>>,
    pub write_mask_registers: Vec<String>,
    pub tables: Vec<Table>,
    pub corrections: Vec<Correction>,
}

pub(crate) fn identifier(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
        && ![
            "as", "async", "await", "break", "const", "continue", "crate", "dyn", "else", "enum",
            "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod",
            "move", "mut", "pub", "ref", "return", "self", "Self", "static", "struct", "super",
            "trait", "true", "type", "unsafe", "use", "where", "while", "yield", "gen", "abstract",
            "become", "box", "do", "final", "macro", "override", "priv", "try", "typeof",
            "unsized", "virtual", "_",
        ]
        .contains(&value)
}
pub(crate) fn qualified(value: &str) -> bool {
    value.split("::").all(identifier)
}
pub(crate) fn relative(value: &str) -> bool {
    !value.is_empty()
        && !value.contains('\\')
        && !value.contains(':')
        && value
            .split('/')
            .all(|p| !p.is_empty() && p != "." && p != "..")
}
fn location(path: &str, line: u32, sources: &BTreeSet<&str>) -> Result<()> {
    ensure!(
        relative(path) && line > 0 && sources.contains(path),
        "unlisted or invalid source location {path}:{line}"
    );
    Ok(())
}
fn register<'a>(ir: &'a IR, reference: &str) -> Result<&'a chiptool::ir::Register> {
    let (block, name) = reference
        .rsplit_once('.')
        .context("register reference format")?;
    let item = ir
        .blocks
        .get(block)
        .with_context(|| format!("missing block {block}"))?
        .items
        .iter()
        .find(|i| i.name == name)
        .with_context(|| format!("missing register {reference}"))?;
    let BlockItemInner::Register(r) = &item.inner else {
        anyhow::bail!("not a register: {reference}")
    };
    Ok(r)
}

/// Sorted directory discovery, including strict validation of manifests for both families.
pub fn discover(root: &Path) -> Result<Vec<Manifest>> {
    let directory = root.join("provenance/expanded");
    if !directory.exists() {
        return Ok(Vec::new());
    }
    let mut paths = fs::read_dir(directory)?
        .map(|e| e.map(|e| e.path()))
        .collect::<std::io::Result<Vec<_>>>()?;
    paths.retain(|p| p.extension().is_some_and(|e| e == "json"));
    paths.sort();
    let mut manifests = Vec::new();
    let mut files = BTreeSet::new();
    let mut names = BTreeSet::new();
    for path in paths {
        let m: Manifest = load(&path)?;
        // Shared source tables/C definitions can substantiate distinct lane
        // layouts; duplicate dispositions are forbidden within each manifest.
        let mut tables = BTreeSet::new();
        ensure!(m.schema == 1, "unsupported expanded schema");
        ensure!(
            !m.registers.is_empty() && !m.sources.is_empty() && !m.tables.is_empty(),
            "empty expanded manifest"
        );
        for file in &m.registers {
            ensure!(
                identifier(file) && file.starts_with(&format!("expanded_{}", m.chip.prefix())),
                "register file family/name mismatch: {file}"
            );
            ensure!(files.insert(file.clone()), "duplicate register file {file}");
        }
        let mut sources = BTreeSet::new();
        for source in &m.sources {
            ensure!(
                relative(&source.path) && sources.insert(source.path.as_str()),
                "invalid/duplicate source path"
            );
            ensure!(
                source.sha256.len() == 64
                    && source
                        .sha256
                        .bytes()
                        .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()),
                "invalid source hash"
            );
        }
        for instance in &m.instances {
            ensure!(
                identifier(&instance.name)
                    && instance.name.chars().all(|c| !c.is_ascii_lowercase())
                    && instance.name.starts_with(&format!(
                        "{}_{}_",
                        instance.view.label(),
                        m.chip.label()
                    )),
                "instance name/view/family mismatch"
            );
            ensure!(
                names.insert(instance.name.clone()),
                "duplicate instance {}",
                instance.name
            );
            ensure!(
                qualified(&instance.block) && instance.block.starts_with(m.chip.prefix()),
                "instance block family mismatch"
            );
            location(&instance.source.path, instance.source.line, &sources)?;
        }
        let mut used = BTreeSet::new();
        for table in &m.tables {
            location(&table.source, table.line, &sources)?;
            if m.sources
                .iter()
                .any(|s| s.path == table.source && !matches!(s.kind, SourceKind::Docs))
            {
                ensure!(
                    table.note.as_ref().is_some_and(|s| !s.trim().is_empty()),
                    "C supplemental accounting needs a concise note"
                );
            }
            ensure!(
                tables.insert((table.source.clone(), table.line)),
                "duplicate table accounting"
            );
            used.insert(table.source.as_str());
            if table.status == Status::Generated || table.status == Status::Duplicate {
                ensure!(
                    !table.registers.is_empty(),
                    "generated/duplicate table needs register references"
                );
            }
            if table.status != Status::Generated {
                ensure!(
                    table.note.as_ref().is_some_and(|s| !s.trim().is_empty()),
                    "table disposition needs a factual note"
                );
            }
            let mut refs = BTreeSet::new();
            ensure!(
                table.registers.iter().all(|r| refs.insert(r)),
                "duplicate table register reference"
            );
        }
        for source in &m.sources {
            if matches!(source.kind, SourceKind::Docs) {
                ensure!(
                    used.contains(source.path.as_str())
                        || m.instances.iter().any(|i| i.source.path == source.path)
                        || m.corrections.iter().any(|c| c.source == source.path),
                    "document source has no accounted facts"
                );
            }
        }
        for c in &m.corrections {
            location(&c.source, c.line, &sources)?;
            ensure!(
                !c.decision.trim().is_empty() && !c.evidence.trim().is_empty(),
                "empty correction"
            );
        }
        manifests.push(m);
    }
    Ok(manifests)
}

/// Merge only the selected family; retain base policy entries for stable blocks.
pub fn merge(
    root: &Path,
    family: Family,
    manifests: &[Manifest],
    ir: &mut IR,
    policy: &mut Policy,
) -> Result<()> {
    for m in manifests.iter().filter(|m| m.chip == family) {
        let mut owned = BTreeSet::new();
        for file in &m.registers {
            let sub: IR = load(&root.join(format!("data/registers/{file}.yaml")))?;
            ensure!(
                sub.devices.is_empty(),
                "device runtime generation forbidden"
            );
            for name in sub
                .blocks
                .keys()
                .chain(sub.fieldsets.keys())
                .chain(sub.enums.keys())
            {
                ensure!(
                    qualified(name) && name.starts_with(family.prefix()),
                    "IR name/family mismatch: {name}"
                );
            }
            super::merge_ir(ir, sub.clone())?;
            owned.extend(sub.blocks.keys().cloned());
        }
        for (block, groups) in &m.aliases {
            ensure!(owned.contains(block), "alias block not owned: {block}");
            policy
                .aliases
                .entry(block.clone())
                .or_default()
                .extend(groups.clone());
        }
        for reference in &m.write_mask_registers {
            let (block, _) = reference.rsplit_once('.').context("mask policy format")?;
            ensure!(owned.contains(block), "mask register not owned");
            policy.write_mask_registers.push(reference.clone());
        }
    }
    Ok(())
}

pub fn validate_references(
    root: &Path,
    family: Family,
    manifests: &[Manifest],
    ir: &IR,
) -> Result<()> {
    for m in manifests.iter().filter(|m| m.chip == family) {
        for reference in &m.write_mask_registers {
            register(ir, reference)?;
        }
        let mut covered = BTreeSet::new();
        for table in &m.tables {
            for reference in &table.registers {
                register(ir, reference)?;
                if table.status == Status::Generated || table.status == Status::Duplicate {
                    covered.insert(reference.clone());
                }
            }
        }
        for file in &m.registers {
            let sub: IR = load(&root.join(format!("data/registers/{file}.yaml")))?;
            for block in sub.blocks.keys() {
                for item in &ir.blocks[block].items {
                    if matches!(item.inner, BlockItemInner::Register(_)) {
                        ensure!(
                            covered.contains(&format!("{block}.{}", item.name)),
                            "register lacks table accounting {block}.{}",
                            item.name
                        );
                    }
                }
            }
        }
        for instance in &m.instances {
            let size = block_size(ir, &instance.block, &mut BTreeSet::new())?;
            ensure!(
                size > 0 && instance.address.checked_add(size - 1).is_some(),
                "instance address range overflow/empty block"
            );
            // No address translation: alignment is checked in the documented view.
            fn aligned(ir: &IR, name: &str, base: u64) -> Result<()> {
                for item in &ir.blocks[name].items {
                    for offset in super::offsets(item.byte_offset, &item.array)? {
                        let address = base
                            .checked_add(offset)
                            .context("instance offset overflow")?;
                        match &item.inner {
                            BlockItemInner::Register(r) => ensure!(
                                address % u64::from(r.bit_size / 8) == 0,
                                "misaligned instance register"
                            ),
                            BlockItemInner::Block(b) => aligned(ir, &b.block, address)?,
                        }
                    }
                }
                Ok(())
            }
            aligned(ir, &instance.block, instance.address)?;
        }
    }
    Ok(())
}

pub fn render(family: Family, manifests: &[Manifest], ir: &IR) -> Result<String> {
    let mut instances: Vec<_> = manifests
        .iter()
        .filter(|m| m.chip == family)
        .flat_map(|m| &m.instances)
        .collect();
    if instances.is_empty() {
        return Ok(String::new());
    }
    instances.sort_by(|a, b| a.name.cmp(&b.name));
    let mut out = String::from(
        r#"
/// Documented SoC address views; AP/secure addresses are NOT translated to RCPU.
pub mod soc {
    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    pub enum AddressView { Ap, Rcpu, Secure }
    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    pub enum SourceKind { Docs, Esos, Linux, Uboot }
    /// Source-labelled address metadata, not an assertion of mapping or ownership.
    #[derive(Clone, Copy, Debug)]
    pub struct Instance<T> {
        pub address: u64,
        pub view: AddressView,
        pub source_kind: SourceKind,
        pub source_path: &'static str,
        pub source_line: u32,
        marker: core::marker::PhantomData<T>,
    }
"#,
    );
    let types: BTreeSet<_> = instances.iter().map(|i| &i.block).collect();
    for block in types {
        let last = block_size(ir, block, &mut BTreeSet::new())? - 1;
        out.push_str(&format!(r#"
    impl Instance<super::{block}> {{
        /// Construct this block in its documented address view, without translation.
        ///
        /// # Safety
        /// Caller must establish mapping, privilege, clocking and exclusive access
        /// for this exact view and the entire block. Panics if the address does not
        /// fit the target pointer width; metadata remains available on every target.
        pub unsafe fn peripheral(&self) -> super::{block} {{
            let address = usize::try_from(self.address).expect("MMIO address exceeds target pointer width");
            assert!(self.address.checked_add({last}).is_some_and(|end| usize::try_from(end).is_ok()), "MMIO block exceeds target pointer width");
            unsafe {{ super::{block}::from_ptr(address as *mut ()) }}
        }}
    }}
"#));
    }
    out.push_str("pub mod peripherals {\n");
    for i in instances {
        let kind = manifests
            .iter()
            .filter(|m| m.chip == family)
            .find(|m| m.instances.iter().any(|instance| instance.name == i.name))
            .unwrap()
            .sources
            .iter()
            .find(|s| s.path == i.source.path)
            .unwrap();
        let kind = match kind.kind {
            SourceKind::Docs => "Docs",
            SourceKind::Esos => "Esos",
            SourceKind::Linux => "Linux",
            SourceKind::Uboot => "Uboot",
        };
        let view = match i.view {
            View::Ap => "Ap",
            View::Rcpu => "Rcpu",
            View::Secure => "Secure",
        };
        out.push_str(&format!("pub const {}: super::Instance<super::super::{}> = super::Instance {{ address: 0x{:x}, view: super::AddressView::{view}, source_kind: super::SourceKind::{kind}, source_path: {:?}, source_line: {}, marker: core::marker::PhantomData }};\n", i.name, i.block, i.address, i.source.path, i.source.line));
    }
    out.push_str("}\n}\n");
    Ok(out)
}
