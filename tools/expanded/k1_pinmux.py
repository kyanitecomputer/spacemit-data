#!/usr/bin/env python3
"""Import published K1 MFPR locations and common fields, not vendor prose."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manual_tables import bit_range, cells, extract, identifier, integer

REPO = Path(__file__).resolve().parents[2]
DOC_ROOT = Path("/tmp/spacemit-research/docs/en/key_stone")
C_ROOT = Path("/tmp/k1-pinmux-c/linux")
CHAPTER = "k1/k1_docs/k1_usermanual/3.Pinout.md"
DATASHEET = "k1/k1_docs/k1_ds.md"
C_PATH = "drivers/pinctrl/spacemit/pinctrl-k1.c"
C_PIN = "cee9395acd8043be0644b25c34bfa86623f2b935"
C_SHA = "80f60e3fdf64fa6b92c915236e20998519b0d099d0fe9e4e11681926908b93eb"
C_URL = f"https://github.com/spacemit-com/linux/blob/{C_PIN}/{C_PATH}"
BLOCK = "k1_pinmux::Mfpr"
FIELDSET = "k1_pinmux::regs::Pin"
BASE = 0xD401E000


def pin_table(path):
    lines = path.read_text().splitlines()
    headers = [
        i
        for i, line in enumerate(lines)
        if cells(line) == ["MFPR ID", "Address", "Offset"]
    ]
    if len(headers) != 1:
        raise ValueError("expected one MFPR address table")
    start = headers[0]
    rows = []
    names, offsets = set(), set()
    for i in range(start + 2, len(lines)):
        if not lines[i].startswith("|"):
            break
        values = cells(lines[i])
        if len(values) != 3:
            raise ValueError("malformed MFPR address row")
        name = identifier(values[0])
        address, offset = integer(values[1]), integer(values[2])
        if name in names or offset in offsets or offset % 4 or address != BASE + offset:
            raise ValueError("duplicate, unaligned or inconsistent MFPR location")
        names.add(name)
        offsets.add(offset)
        rows.append((name, offset, i + 1))
    if (
        len(rows) != 129
        or rows[0][:2] != ("gpio_00", 4)
        or rows[-1][:2] != ("gpio_127", 0x24C)
    ):
        raise ValueError("incomplete MFPR pin inventory")
    return start + 1, rows


def common_fields(table):
    if table["header"] != ["Bit(s)", "Field", "Type", "Reset"]:
        raise ValueError("unexpected MFPR field header")
    fields, used = [], 0
    for row in table["rows"]:
        bits, name, access, reset = row["values"]
        low, width = bit_range(bits)
        mask = ((1 << width) - 1) << low
        if low + width > 32 or mask & used:
            raise ValueError("invalid or overlapping MFPR bits")
        used |= mask
        if access not in {"RW", "RO"} or not 0 <= integer(reset) < 1 << width:
            raise ValueError("invalid MFPR access/reset")
        if name == "RSVD":
            continue
        if access != "RW":
            raise ValueError("unexpected MFPR named-field access")
        field = {"name": identifier(name), "bit_offset": low, "bit_size": width}
        if name.startswith("EDGE_"):
            field["description"] = (
                "Conditional wake-capable pin field; applicability is pin-dependent. EDGE_CLEAR disables detection, not an inferred W1C command."
            )
        fields.append(field)
    if used != 0xFFFFFFFF or len({f["name"] for f in fields}) != len(fields):
        raise ValueError("incomplete or duplicate MFPR fields")
    return fields


def build(source_root=DOC_ROOT, linux_root=None):
    header, pins = pin_table(source_root / CHAPTER)
    ds_header, ds_pins = pin_table(source_root / DATASHEET)
    if [(n, o) for n, o, _ in pins] != [(n, o) for n, o, _ in ds_pins]:
        raise ValueError("datasheet MFPR address conflict")
    manual = extract(source_root / CHAPTER, source_root)
    if [t["line"] for t in manual] != [1286]:
        raise ValueError("MFPR register-table inventory changed")
    fields = common_fields(manual[0])
    ds_tables = extract(source_root / DATASHEET, source_root)
    if [t["line"] for t in ds_tables] != [
        779,
        823,
        913,
        921,
        970,
        976,
        1088,
        1097,
        3678,
    ]:
        raise ValueError("datasheet numeric-table inventory changed")
    if fields != common_fields(ds_tables[-1]):
        raise ValueError("datasheet MFPR field conflict")
    if (
        linux_root is not None
        and hashlib.sha256((linux_root / C_PATH).read_bytes()).hexdigest() != C_SHA
    ):
        raise ValueError("pinned pinctrl C source changed")
    refs = [BLOCK + "." + name for name, _, _ in pins]
    ir = {
        "block/" + BLOCK: {
            "description": "K1 published sparse MFPR pin locations. No inferred RCPU/security translation or uniform per-pin reset.",
            "items": [
                {
                    "name": name,
                    "byte_offset": offset,
                    "bit_size": 32,
                    "access": "ReadWrite",
                    "fieldset": FIELDSET,
                    "description": f"Pin configuration; {CHAPTER}:{line}. Wake fields only apply where implemented.",
                }
                for name, offset, line in pins
            ],
        },
        "fieldset/" + FIELDSET: {
            "bit_size": 32,
            "description": "Common MFPR encoding. DRIVE[1:0] occupies bits12:11, DRIVE[2] bit10. Table reset examples are not per-pin reset guarantees.",
            "fields": fields,
        },
    }
    tables = [
        {"source": CHAPTER, "line": header, "status": "generated", "registers": refs},
        {"source": CHAPTER, "line": 1286, "status": "generated", "registers": refs},
        {
            "source": DATASHEET,
            "line": ds_header,
            "status": "duplicate",
            "registers": refs,
            "note": "Same 129 published MFPR addresses as the manual, not additional silicon registers.",
        },
        {
            "source": DATASHEET,
            "line": 3678,
            "status": "duplicate",
            "registers": refs,
            "note": "Same common MFPR bit layout as the manual; pin-specific reset/applicability is not inferred.",
        },
    ]
    # These datasheet functional tables repeat chapter 11, not a second numeric
    # MMIO bank. Widths without physical offsets remain unresolved source facts.
    for line, manual_line, status in [
        (779, 575, "not_mmio"),
        (789, 585, "source_gap"),
        (823, 619, "not_mmio"),
        (832, 628, "source_gap"),
        (913, 710, "not_mmio"),
        (921, 718, "source_gap"),
        (970, 767, "not_mmio"),
        (976, 773, "source_gap"),
        (1088, 885, "not_mmio"),
        (1097, 894, "source_gap"),
    ]:
        note = (
            "Repeated pixel-algorithm variables, not MMIO bit positions."
            if status == "not_mmio"
            else "Repeated functional register names/widths without physical offsets; no additional numeric MMIO layout."
        )
        tables.append(
            {
                "source": DATASHEET,
                "line": line,
                "status": status,
                "registers": [],
                "note": f"{note} Corresponding manual 11.Video_&_Graphics.md:{manual_line}; source-established C layouts are separately imported by k1_media.",
            }
        )
    corrections = [
        {
            "source": CHAPTER,
            "line": 1111,
            "decision": "Keep exactly the 129 published named addresses, including sparse holes; neither offset zero nor unnamed gap words are invented.",
            "evidence": f"{CHAPTER}:{header}-1244; {DATASHEET}:{ds_header}.",
        },
        {
            "source": CHAPTER,
            "line": 1286,
            "decision": "Common reset examples do not establish per-pin resets. Wake/edge fields are conditional; no per-pin wake-capability promise or W1C semantics inferred.",
            "evidence": f"{CHAPTER}:1290-1298; {DATASHEET}:3682-3690.",
        },
        {
            "source": C_PATH,
            "line": 45,
            "decision": "C PAD_DRIVE_K1 covers physical bits12:10 but shifts a contiguous encoding from bit10. Retain the manual split encoding DRIVE[1:0] at12:11 and DRIVE[2] at10; do not silently reorder the source fields.",
            "evidence": f"{C_URL}#L45; #L611-L614; {CHAPTER}:1292-1293.",
        },
        {
            "source": C_PATH,
            "line": 117,
            "decision": "C K1 pin-to-offset formulas corroborate all 128 GPIO/alternate-function pin locations; PMIC_INT_N at+0x1e8 is published separately, not an invented GPIO128.",
            "evidence": f"{C_URL}#L117-L160 and #L829-L956; {CHAPTER}:1219.",
        },
        {
            "source": C_PATH,
            "line": 267,
            "decision": "C accesses pin registers with native 32-bit readl/writel. IO-power-domain/APBC side registers in C are outside this common MFPR bank, not undocumented pin words.",
            "evidence": f"{C_URL}#L267; #L471-L472; #L649-L650.",
        },
    ]
    manifest = {
        "schema": 1,
        "chip": "k1",
        "registers": ["expanded_k1_pinmux"],
        "sources": [
            {
                "path": path,
                "sha256": hashlib.sha256((source_root / path).read_bytes()).hexdigest(),
                "kind": "docs",
            }
            for path in (CHAPTER, DATASHEET)
        ]
        + [{"path": C_PATH, "sha256": C_SHA, "kind": "linux"}],
        "instances": [
            {
                "name": "AP_K1_PINMUX",
                "block": BLOCK,
                "address": BASE,
                "view": "ap",
                "source": {"path": CHAPTER, "line": 1111},
            }
        ],
        "aliases": {},
        "write_mask_registers": [],
        "tables": sorted(tables, key=lambda t: (t["source"], t["line"])),
        "corrections": corrections,
    }
    return ir, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DOC_ROOT)
    parser.add_argument("--repo-root", type=Path, default=REPO)
    parser.add_argument("--linux-root", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    ir, manifest = build(args.source_root, args.linux_root)
    for relative, value in [
        ("data/registers/expanded_k1_pinmux.yaml", ir),
        ("provenance/expanded/k1_pinmux.json", manifest),
    ]:
        path = args.repo_root / relative
        rendered = json.dumps(value, indent=2) + "\n"
        if args.check:
            if path.read_text() != rendered:
                raise SystemExit(f"stale import: {relative}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered)
    print(
        f"K1 pinmux: 129 named native32-bit words, {len(ir['fieldset/' + FIELDSET]['fields'])} reused fields, 14 table dispositions; check={args.check}"
    )


if __name__ == "__main__":
    main()
