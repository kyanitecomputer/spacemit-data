use anyhow::{Result, ensure};
use std::path::Path;

fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    ensure!(
        args.is_empty() || args == ["--check"],
        "usage: spacemit-data-gen [--check]"
    );
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    spacemit_data_gen::update(root, !args.is_empty())
}
