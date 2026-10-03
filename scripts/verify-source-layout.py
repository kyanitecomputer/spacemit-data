#!/usr/bin/env python3
"""Compile original K3 mailbox type declarations for both reviewed C target ABIs."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit('usage: verify-source-layout.py RESEARCH_CACHE (requires clang)')
root = Path(__file__).resolve().parent.parent
relative = 'bsp/spacemit/drivers/mailbox/k3_mailbox.h'
source = json.loads((root / 'provenance/sources.json').read_text())[0]
fact = next(entry for entry in source['files'] if entry['path'] == relative)
header = (Path(sys.argv[1]) / source['cache_dir'] / relative).read_bytes()
if hashlib.sha256(header).hexdigest() != fact['sha256']:
    raise SystemExit('header hash differs from pinned review')
text = header.decode()
# Extract the actual declarations, not a retyped register layout; dependencies
# outside this slice are RT-Thread objects and irrelevant to register offsets.
start = text.index('typedef volatile union mbox_msg {')
end = text.index('struct spacemit_mailbox {')
c = 'typedef unsigned int rt_uint32_t;\n' + text[start:end]
c += '\n_Static_assert(sizeof(rt_uint32_t) == 4, "u32 width");\n'
c += '_Static_assert(sizeof(unsigned long) == EXPECTED_LONG, "target long width");\n'
c += '_Static_assert(sizeof(mbox_thresh_t) == EXPECTED_STRIDE, "threshold stride");\n'
c += '_Static_assert(__builtin_offsetof(mbox_reg_desc_t, mbox_thresh[0]) == 0x180, "user0 threshold");\n'
c += '_Static_assert(__builtin_offsetof(mbox_reg_desc_t, mbox_thresh[1]) == EXPECTED_OFFSET, "user1 threshold");\n'
target = root / 'spacemit-pac/target'
target.mkdir(parents=True, exist_ok=True)
path = target / 'threshold_layout.c'
path.write_text(c)
for arch, march, abi, stride, offset, long_width in [
    ('riscv64', 'rv64gc', 'lp64d', 32, 0x1a0, 8),
    ('riscv32', 'rv32imafc', 'ilp32f', 20, 0x194, 4),
]:
    subprocess.run([
        'clang', f'--target={arch}', f'-march={march}', f'-mabi={abi}',
        f'-DEXPECTED_STRIDE={stride}', f'-DEXPECTED_OFFSET={offset}',
        f'-DEXPECTED_LONG={long_width}',
        '-fsyntax-only', str(path),
    ], check=True)
    print(f'{arch}/{abi}: original C stride {stride}, user1 offset {offset:#x}; manual hardware offset 0x190')
