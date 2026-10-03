#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
python3 spacemit-data-gen/tests/test_verify_expanded.py
python3 scripts/verify-expanded.py
# Source-dependent import/hash checks are optional; generation itself is offline.
cache=${SPACEMIT_RESEARCH_CACHE:-/tmp/spacemit-research}
if [[ -d "$cache/docs/en/key_stone" ]]; then
    source_args=(--source-root "$cache/docs/en/key_stone")
    [[ ! -d "$cache/esos-full" ]] || source_args+=(--esos-root "$cache/esos-full")
    [[ -z "${SPACEMIT_LINUX_SOURCE_ROOT:-}" ]] || source_args+=(--linux-root "$SPACEMIT_LINUX_SOURCE_ROOT")
    [[ -z "${SPACEMIT_UBOOT_SOURCE_ROOT:-}" ]] || source_args+=(--uboot-root "$SPACEMIT_UBOOT_SOURCE_ROOT")
    for importer in tools/expanded/*.py; do
        [[ -f "$importer" && "$(basename "$importer")" != test_* && "$(basename "$importer")" != __init__.py ]] || continue
        python3 "$importer" --source-root "$cache/docs/en/key_stone" --repo-root "$PWD" --check
    done
    if compgen -G 'tools/expanded/test_*.py' >/dev/null; then
        python3 -m unittest discover -s tools/expanded -p 'test_*.py'
    fi
    python3 scripts/verify-expanded.py "${source_args[@]}"
else
    echo 'Source cache absent: importer/source-byte checks skipped (offline PAC checks still run)'
fi
cargo fmt --manifest-path spacemit-data-gen/Cargo.toml --check
cargo fmt --manifest-path spacemit-pac/Cargo.toml --check
cargo run --locked --offline --manifest-path spacemit-data-gen/Cargo.toml -- --check
cargo test --locked --offline --manifest-path spacemit-data-gen/Cargo.toml
cargo clippy --locked --offline --manifest-path spacemit-data-gen/Cargo.toml --all-targets -- -D warnings
for feature in k1-rcpu k3-rcpu0 k3-rcpu1; do
    cargo test --locked --offline --manifest-path spacemit-pac/Cargo.toml --features "$feature"
done
cargo check --locked --offline --manifest-path spacemit-pac/Cargo.toml --features k1-rcpu --target riscv32imafc-unknown-none-elf
for feature in k3-rcpu0 k3-rcpu1; do
    cargo check --locked --offline --manifest-path spacemit-pac/Cargo.toml --features "$feature" --target riscv64gc-unknown-none-elf
done
# Safety configuration failures must stay rejected; require our specific diagnostics.
mkdir -p spacemit-pac/target
cargo check --locked --offline --manifest-path spacemit-pac/Cargo.toml --no-default-features
for features in k1-rcpu,k3-rcpu0 k1-rcpu,k3-rcpu1 k3-rcpu0,k3-rcpu1 k1-rcpu,k3-rcpu0,k3-rcpu1; do
    if cargo check --locked --offline --manifest-path spacemit-pac/Cargo.toml --features "$features" >"spacemit-pac/target/conflict-$features.log" 2>&1; then
        echo "conflicting core features unexpectedly accepted: $features" >&2; exit 1
    fi
    grep -q 'select at most one auxiliary core' "spacemit-pac/target/conflict-$features.log"
done
for feature in k3-rcpu0 k3-rcpu1; do
    if cargo check --locked --offline --manifest-path spacemit-pac/Cargo.toml --features "$feature" --target riscv32imafc-unknown-none-elf >"spacemit-pac/target/wrong-width-$feature.log" 2>&1; then
        echo "$feature on RV32 unexpectedly accepted" >&2; exit 1
    fi
    grep -q 'K3 requires RV64' "spacemit-pac/target/wrong-width-$feature.log"
done
if cargo check --locked --offline --manifest-path spacemit-pac/Cargo.toml --features k1-rcpu --target riscv64gc-unknown-none-elf >spacemit-pac/target/wrong-width-k1.log 2>&1; then
    echo 'K1 on RV64 unexpectedly accepted' >&2; exit 1
fi
grep -q 'K1 requires RV32' spacemit-pac/target/wrong-width-k1.log
if git_dir=$(git rev-parse --git-dir 2>/dev/null); then
    staged=$(git --git-dir "$git_dir" diff --cached --name-only)
elif [[ -d .jj ]]; then
    # Isolated jj workspaces have no .git; inspect the shared backend read-only.
    git_dir=$(python3 - <<'PY'
from pathlib import Path
repo = Path('.jj/repo')
if repo.is_file():
    repo = (repo.parent / repo.read_text().strip()).resolve()
target = repo / 'store/git_target'
print((target.parent / target.read_text().strip()).resolve() if target.is_file()
      else repo / 'store/git' if (repo / 'store/git').is_dir() else '')
PY
)
    staged=''
    if [[ -n "$git_dir" ]]; then
        staged=$(git --git-dir "$git_dir" diff --cached --name-only)
    fi
else
    echo 'Cannot verify repository staging state' >&2; exit 1
fi
test -z "$staged"
echo 'Generation, host behavior/access-mode tests, RV32/RV64 compilation, rejection checks and no-staged-files: passed'
