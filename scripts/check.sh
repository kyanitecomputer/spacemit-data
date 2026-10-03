#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
cargo fmt --manifest-path spacemit-data-gen/Cargo.toml --check
cargo fmt --manifest-path spacemit-pac/Cargo.toml --check
cargo run --manifest-path spacemit-data-gen/Cargo.toml -- --check
cargo test --manifest-path spacemit-data-gen/Cargo.toml
for feature in k1-rcpu k3-rcpu0 k3-rcpu1; do
    cargo test --manifest-path spacemit-pac/Cargo.toml --features "$feature"
done
cargo check --manifest-path spacemit-pac/Cargo.toml --features k1-rcpu --target riscv32imafc-unknown-none-elf
for feature in k3-rcpu0 k3-rcpu1; do
    cargo check --manifest-path spacemit-pac/Cargo.toml --features "$feature" --target riscv64gc-unknown-none-elf
done
# Safety configuration failures must stay rejected; require our specific diagnostics.
mkdir -p spacemit-pac/target
if cargo check --manifest-path spacemit-pac/Cargo.toml --features k1-rcpu,k3-rcpu1 >spacemit-pac/target/conflict.log 2>&1; then
    echo 'conflicting core features unexpectedly accepted' >&2; exit 1
fi
grep -q 'select at most one auxiliary core' spacemit-pac/target/conflict.log
if cargo check --manifest-path spacemit-pac/Cargo.toml --features k3-rcpu1 --target riscv32imafc-unknown-none-elf >spacemit-pac/target/wrong-width.log 2>&1; then
    echo 'K3 on RV32 unexpectedly accepted' >&2; exit 1
fi
grep -q 'K3 requires RV64' spacemit-pac/target/wrong-width.log
test -z "$(git diff --cached --name-only)"
echo 'Generation, host behavior/access-mode tests, RV32/RV64 compilation, rejection checks and no-staged-files: passed'
