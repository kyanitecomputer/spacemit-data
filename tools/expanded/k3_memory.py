#!/usr/bin/env python3
"""Import K3 memory/storage and display facts from pinned public sources.

No reset values are used as chiptool defaults. C initialization values are not
reset values. Unknown bit semantics remain raw registers, never guessed fields.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manual_tables import bit_range, cells, extract, identifier, integer

DOC_ROOT = Path("/tmp/spacemit-research/docs/en/key_stone")
C_ROOT = Path("/tmp/k3-memory-c")
DOC_DIR = "k3/k3_docs/k3_usermanual/"
CHAPTERS = ["09_memory_storage.md", "11_video.md", "12_display.md"]
UBOOT_REV = "28805afe074715a0ede143d42999ea06bee58fbd"
C_FILES = [
    "include/sdhci.h",
    "drivers/mmc/spacemit_sdhci.c",
    "drivers/spi/k1x_qspi.c",
    "arch/riscv/dts/k3.dtsi",
    "drivers/video/spacemit/dsi/drv/spacemit_dsi_hw.h",
    "drivers/video/spacemit/dsi/drv/spacemit_dsi_drv.c",
    "drivers/video/spacemit/dp/inno_dp_reg.h",
    "drivers/video/spacemit/dp/inno_dp.c",
    "drivers/video/spacemit/dp/inno_dp_phy.c",
    "drivers/video/spacemit/spacemit_dpu.c",
    "drivers/video/spacemit/spacemit_dpu.h",
    "drivers/ddr/spacemit/k3/k3_ddr.h",
    "drivers/ddr/spacemit/k3/lpddr_common.c",
    "drivers/ddr/spacemit/k3/lpddr4x_init.c",
    "drivers/ddr/spacemit/k3/lpddr5_init.c",
]
# Split the manual's word-sized presentation into the C-established native
# SDHCI byte/halfword registers. Tuple: relative byte offset, width, name.
SD_SPLITS = {
    0x04: [(0, 16, "sd_block_size"), (2, 16, "sd_block_count")],
    0x0C: [(0, 16, "sd_transfer_mode"), (2, 16, "sd_command")],
    0x28: [
        (0, 8, "sd_host_control"),
        (1, 8, "sd_power_control"),
        (2, 8, "sd_block_gap_control"),
        (3, 8, "sd_wake_up_control"),
    ],
    0x2C: [
        (0, 16, "sd_clock_control"),
        (2, 8, "sd_timeout_control"),
        (3, 8, "sd_software_reset"),
    ],
    0x3C: [(0, 16, "sd_auto_cmd12_error_status"), (2, 16, "sd_host_control2")],
    0x50: [(0, 16, "sd_force_auto_cmd12_error"), (2, 16, "sd_force_interrupt_error")],
    0x54: [(0, 8, "sd_adma_error_status")],
    0x60: [(0, 16, "sd_preset_initialization"), (2, 16, "sd_preset_default_speed")],
    0x64: [(0, 16, "sd_preset_high_speed"), (2, 16, "sd_preset_sdr12")],
    0x68: [(0, 16, "sd_preset_sdr25"), (2, 16, "sd_preset_sdr50")],
    0x6C: [(0, 16, "sd_preset_sdr104"), (2, 16, "sd_preset_ddr50")],
    0xFC: [(0, 16, "sd_slot_interrupt_status"), (2, 16, "sd_host_version")],
}


def call_arguments(source, function):
    """Read balanced C call arguments; nested macros cannot hide the MMIO address."""
    result = []
    for match in re.finditer(r"\b" + function + r"\(", source):
        depth = 0
        args = []
        start = match.end()
        for index in range(start, len(source)):
            char = source[index]
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    args.append(source[start:index])
                    result.append(args)
                    break
                depth -= 1
            elif char == "," and depth == 0:
                args.append(source[start:index])
                start = index + 1
    return result


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(source_root=DOC_ROOT, c_root=C_ROOT):
    ir: dict[str, Any] = {}
    manifest: dict[str, Any] = {
        "schema": 1,
        "chip": "k3",
        "registers": ["expanded_k3_memory"],
        "sources": [],
        "instances": [],
        "aliases": {},
        "write_mask_registers": [],
        "tables": [],
        "corrections": [],
    }
    for p in [
        DOC_DIR + x for x in CHAPTERS + ["06_address_map.md", "07_interrupts.md"]
    ]:
        manifest["sources"].append(
            {"path": p, "sha256": digest(source_root / p), "kind": "docs"}
        )
    c_lines = {}
    for p in C_FILES:
        manifest["sources"].append(
            {"path": p, "sha256": digest(c_root / p), "kind": "uboot"}
        )
        c_lines[p] = (c_root / p).read_text().splitlines()

    def correction(source, line, decision, evidence):
        manifest["corrections"].append(
            {"source": source, "line": line, "decision": decision, "evidence": evidence}
        )

    for path in C_FILES:
        correction(
            path,
            1,
            "Pinned C fact comparison; no descriptive prose imported.",
            f"https://github.com/spacemit-com/uboot-2022.10/blob/{UBOOT_REV}/{path}",
        )

    def block(name):
        qualified = "k3_memory::" + name
        ir.setdefault(
            "block/" + qualified,
            {"description": "K3 " + name + " register window.", "items": []},
        )
        return qualified

    def emit(
        b,
        name,
        offset,
        width,
        fields,
        readable,
        writable,
        direct=False,
        description="",
        array=None,
    ):
        items = ir["block/" + b]["items"]
        result = []
        if not readable and not writable:
            raise ValueError("register has no established access: " + name)
        views = (
            [(name, "ReadWrite", fields)]
            if readable and writable and not direct
            else []
        )
        if not views:
            if readable:
                views.append(
                    (name if not writable else name + "_read", "Read", readable)
                )
            if writable:
                views.append(
                    (name if not readable else name + "_write", "Write", writable)
                )
        if len(views) == 2:
            if array:
                raise ValueError("array aliases must be scalarized")
            manifest["aliases"].setdefault(b, []).append([v[0] for v in views])
        for n, access, fs in views:
            if any(i["name"] == n for i in items):
                raise ValueError("duplicate register " + b + "." + n)
            item = {
                "name": n,
                "byte_offset": offset,
                "access": access,
                "bit_size": width,
            }
            if description:
                item["description"] = description
            if array:
                item["array"] = array
            if isinstance(fs, list) and fs:
                fname = "k3_memory::regs::" + identifier(b.split("::")[-1] + "_" + n)
                ir["fieldset/" + fname] = {"bit_size": width, "fields": fs}
                item["fieldset"] = fname
            items.append(item)
            result.append(b + "." + n)
            if direct and access == "Write":
                manifest["write_mask_registers"].append(b + "." + n)
        return result

    # Native-width register access is established by the SDHCI accessors and
    # driver; the manual is a 32-bit table presentation, not an access-width rule.
    sd = block("Sdhci")
    ufs = block("Ufs")
    mng = block("UfsManagement")
    dsi = block("Dsi")
    for chapter in ["09_memory_storage.md", "12_display.md"]:
        p = DOC_DIR + chapter
        lines = (source_root / p).read_text().splitlines()
        for table in extract(source_root / p, source_root):
            assert table["kind"] == "fields"
            off_label = table["offset_labels"][-1]
            offset = integer(off_label["value"].split(":", 1)[1])
            name = next(
                x.strip() for x in reversed(lines[: off_label["line"] - 1]) if x.strip()
            )
            if not re.fullmatch(r"[A-Z][A-Z0-9_]+", name):
                raise ValueError("invalid register label " + name)
            b = (
                dsi
                if chapter == "12_display.md"
                else sd
                if table["line"] < 1600
                else ufs
                if table["line"] < 2040
                else mng
            )
            fields = []
            types = {}
            prose = {}
            for row in table["rows"]:
                bits, f, type_, reset = row["values"]
                if re.search(r"rsvd|reserved", f, re.I):
                    continue
                if type_ not in {
                    "RW",
                    "RO",
                    "R",
                    "ROC",
                    "RWAC",
                    "RW1C",
                    "RW1S",
                    "RWC",
                    "RWS",
                    "W1C",
                    "WO",
                }:
                    raise ValueError("unknown access " + type_)
                lo, size = bit_range(bits)
                raw = cells(lines[row["line"] - 1])
                prose[f] = raw[-1]
                if (name, f) in {
                    ("DSI_CPU_CMD_0", "CFG_CPU_CMD_REQ"),
                    ("DSI_CPU_CMD_3", "CFG_CPU_DAT_REQ"),
                    ("DSI_RX_PKT_CTRL", "RX_PKT_RD_REQ"),
                }:
                    type_ = "RWAC"
                    correction(
                        p,
                        row["line"],
                        "Request field is hardware-cleared after completion; no modify.",
                        "Same row explicitly describes hardware de-asserting/clearing the request after its operation.",
                    )
                if f == "TX_REQ_SK_BYTE_SET" and name == "DSI_PHY_CODE_1":
                    type_ = "RWAC"
                    correction(
                        p,
                        row["line"],
                        "Calibration is write-one-trigger, hardware-cleared, not W1C status.",
                        "Same row explicitly states write 1 to trigger calibration and hardware clears on completion.",
                    )
                if f == "HALT" and name == "CQE_CQCTL_REG":
                    type_ = "RW"
                    correction(
                        p,
                        row["line"],
                        "HALT is writable despite RO column.",
                        "Same row describes writing 1 to halt and 0 to resume.",
                    )
                field: dict[str, Any] = {
                    "name": identifier(f),
                    "bit_offset": lo,
                    "bit_size": size,
                    "description": "Access "
                    + type_
                    + "; documented reset "
                    + reset
                    + ".",
                }
                if f == "ADMA_SYS_ADDR":
                    field["name"] += "_hi" if lo == 16 else "_lo"
                    correction(
                        p,
                        row["line"],
                        "Disambiguate repeated ADMA address half name.",
                        "Same table assigns the identical label to distinct 31:16 and 15:0 ranges.",
                    )
                if any(v["name"] == field["name"] for v in fields):
                    field["name"] += f"_bits_{lo}_{lo + size - 1}"
                    correction(
                        p,
                        row["line"],
                        "Disambiguate repeated field label by documented bit range.",
                        f"Source repeats {f} at distinct ranges; numeric positions unchanged.",
                    )
                if any(
                    lo < v["bit_offset"] + v["bit_size"] and v["bit_offset"] < lo + size
                    for v in fields
                ):
                    raise ValueError(f"overlapping fields {p}:{row['line']}")
                fields.append(field)
                types[field["name"]] = type_
            if name == "SD_AUTO_CMD12_ERROR_STATUS":
                for bit, label in [
                    (26, "adma2_length_mode"),
                    (27, "cmd23_enable"),
                    (28, "host_version4_enable"),
                    (29, "addressing64_enable"),
                ]:
                    fields.append(
                        {
                            "name": label,
                            "bit_offset": bit,
                            "bit_size": 1,
                            "description": "C-established RW control; reset unspecified.",
                        }
                    )
                    types[label] = "RW"
                correction(
                    p,
                    table["line"],
                    "Host control2 bits 10:13 override reserved manual range.",
                    f"Pinned U-Boot {UBOOT_REV} drivers/mmc/spacemit_sdhci.c:15-18,36-46 writes these control bits with sdhci_writew.",
                )
            parts = (
                SD_SPLITS.get(offset, [(0, 32, identifier(name))])
                if b == sd
                else [(0, 32, identifier(name))]
            )
            refs = []
            for delta, width, n in parts:
                subset = []
                for f in fields:
                    lo = f["bit_offset"]
                    end = lo + f["bit_size"]
                    if lo >= delta * 8 and end <= delta * 8 + width:
                        subset.append(dict(f, bit_offset=lo - delta * 8))
                    elif lo < delta * 8 + width and end > delta * 8:
                        raise ValueError(
                            "field crosses native access boundary "
                            + name
                            + "."
                            + f["name"]
                        )
                readable = [f for f in subset if types[f["name"]] not in {"WO", "W1C"}]
                writable = [
                    f for f in subset if types[f["name"]] not in {"RO", "R", "ROC"}
                ]
                # RWC in DSI does not state which write value clears. Preserve a
                # direct write view without implying W1C or providing modify.
                direct = any(
                    types[f["name"]] not in {"RW", "RO", "R", "ROC"} for f in subset
                )
                direct |= name in {
                    "SD_BUFFER_DATA_PORT_01",
                    "DSI_IRQ_ST",
                    "DSI_IRQ_ST1",
                    "DSI_CPU_STATUS_0",
                    "DSI_CPN_STATUS_0",
                    "DSI_LCD_BDG_CTRL0",
                    "CQE_CQTCLR_REG",
                }
                if b == sd and n == 'sd_command':
                    direct = True
                    correction(p, table['line'],
                        'SD command +0x0e is a native16-bit trigger: preserve readback and direct-write aliases, not modify. Transfer mode +0x0c remains an independent native16-bit configuration view.',
                        f'{p}:467-479,550; pinned U-Boot include/sdhci.h SDHCI_COMMAND and sdhci_writew command accesses.')
                # Mixed readable/writable views get separate fieldsets, ensuring
                # RO status and WO command fields are not offered as setters/getters.
                direct |= bool(
                    readable
                    and writable
                    and {f["name"] for f in readable} != {f["name"] for f in writable}
                )
                if name in {
                    "DSI_IRQ_ST",
                    "DSI_IRQ_ST1",
                    "DSI_CPU_STATUS_0",
                    "DSI_CPN_STATUS_0",
                }:
                    writable = subset
                    direct = True
                    correction(
                        p,
                        table["line"],
                        "Expose separate status/direct-write views; no modify.",
                        "Register prose states interrupt write-1-clear or counter write-clear, overriding generic Type columns.",
                    )
                # An entirely reserved word is still a known numeric register.
                refs += emit(
                    b,
                    n,
                    offset + delta,
                    width,
                    subset,
                    readable or (True if not subset else []),
                    writable,
                    direct,
                    f"{p}:{table['line']}.",
                )
            manifest["tables"].append(
                {
                    "source": p,
                    "line": table["line"],
                    "status": "generated",
                    "registers": refs,
                    "note": "Native numeric register facts; reserved bits omitted.",
                }
            )
            if b == sd and offset in SD_SPLITS:
                correction(
                    p,
                    table["line"],
                    "Split word presentation into native SDHCI accesses.",
                    f"https://github.com/spacemit-com/uboot-2022.10/blob/{UBOOT_REV}/include/sdhci.h; byte/halfword offsets and sdhci_readb/readw/writeb/writew accessors.",
                )
            if any(t == "RWC" for t in types.values()) and b == dsi:
                correction(
                    p,
                    table["line"],
                    "RWC retained as separate read/direct-write views; clear value unspecified.",
                    "Source Type=RWC; no write-clear polarity is asserted.",
                )

    # Known bases only; AP addresses are not silently translated to RCPU.
    def instance(name, b, address, p, line, view="ap"):
        if p == DOC_DIR + "06_address_map.md":
            address_hex = f"{address:x}"
            matching = [
                i
                for i, s in enumerate((source_root / p).read_text().splitlines(), 1)
                if address_hex in s.lower().replace("_", "").replace("0x", "")
            ]
            # DDR subwindows derive from the C +0x600000/+0x800000 bases.
            if matching:
                line = matching[0]
            else:
                p = "drivers/ddr/spacemit/k3/lpddr_common.c"
                line = 205 if b.endswith("DdrConfig") else 12
        elif p == DOC_DIR + "09_memory_storage.md":
            normalized = f"{address:x}"
            line = next(
                i
                for i, s in enumerate((source_root / p).read_text().splitlines(), 1)
                if normalized in s.lower().replace("_", "").replace("0x", "")
            )
        manifest["instances"].append(
            {
                "name": "AP_K3_MEMORY_" + name,
                "block": b,
                "address": address,
                "view": view,
                "source": {"path": p, "line": line},
            }
        )

    for i, address in enumerate([0xD4280000, 0xD4280800, 0xD4281000], 1):
        instance("SD" + str(i), sd, address, DOC_DIR + "09_memory_storage.md", 428 + i)
    instance("UFS", ufs, 0xC0E00000, DOC_DIR + "09_memory_storage.md", 1650)
    instance("UFS_MANAGEMENT", mng, 0xC0E01B00, DOC_DIR + "09_memory_storage.md", 2040)
    instance(
        "DSI00", dsi, 0xD421A800, "drivers/video/spacemit/dsi/drv/spacemit_dsi_hw.h", 13
    )
    instance("DSI01", dsi, 0xD421AA00, DOC_DIR + "12_display.md", 55)
    correction(
        DOC_DIR + "12_display.md",
        54,
        "DSI00 base corrected from 0xd421a000 to 0xd421a800.",
        f"06_address_map.md LCD_DSI row; K3 PDF V1.13 pp83/289 repeats the conflicting bases. U-Boot {UBOOT_REV} spacemit_dsi_hw.h:13 and k3.dtsi:1089-1092 agree on a800. DSI01 0xd421aa00 remains documented only.",
    )
    # Corroborate every DSI C offset whose definition is present in the header.
    dsi_items = ir["block/" + dsi]["items"]
    for s in c_lines["drivers/video/spacemit/dsi/drv/spacemit_dsi_hw.h"]:
        m = re.fullmatch(r"#define\s+(DSI_\w+)\s+(0x[0-9A-Fa-f]+)", s.strip())
        if not m or m[1] == "DSI_REG_BASE":
            continue
        matches = [
            v
            for v in dsi_items
            if v["name"]
            in {
                identifier(m[1]),
                identifier(m[1]) + "_read",
                identifier(m[1]) + "_write",
            }
        ]
        if not matches or any(v["byte_offset"] != int(m[2], 16) for v in matches):
            raise ValueError("DSI C offset mismatch " + m[1])

    # QSPI: register definitions and access operations, not guessed IP variants.
    qp = "drivers/spi/k1x_qspi.c"
    q = block("Qspi")
    qrefs = []
    qsource = "\n".join(c_lines[qp])
    dump = re.search(r"u32 reg_offset_table\[\]\s*=\s*\{(.*?)\};", qsource, re.S)
    assert dump
    q_read = set(re.findall(r"QSPI_\w+", dump[1]))
    q_write = set()
    for args in call_arguments(qsource, "qspi_writel"):
        q_write.update(re.findall(r"QSPI_\w+", args[-1]))
    q_fields = {}
    for line, s in enumerate(c_lines[qp], 1):
        m = re.match(
            r"#define\s+(QSPI_\w+)\s+(?:BIT\((\d+)\)|GENMASK\((\d+),\s*(\d+)\))", s
        )
        if m and 59 <= line <= 143:
            lo = int(m[2] or m[4])
            hi = int(m[2] or m[3])
            # All masks in this source region follow their register declaration.
            prefix = "_".join(m[1].split("_")[:2])
            field_name = identifier(
                m[1].removeprefix(prefix + "_").removesuffix("_MASK")
            )
            if prefix == "QSPI_LCKER":
                prefix = "QSPI_LCKCR"
            q_fields.setdefault(prefix, []).append(
                {
                    "name": field_name,
                    "bit_offset": lo,
                    "bit_size": hi - lo + 1,
                    "description": f"{qp}:{line}; C mask 0x{((1 << (hi - lo + 1)) - 1) << lo:x}.",
                }
            )
    for line, s in enumerate(c_lines[qp], 1):
        m = re.fullmatch(r"#define\s+(QSPI_\w+)\s+(0x[0-9a-fA-F]+)", s.strip())
        if not m or not 59 <= line <= 143 or m[1].endswith("_VALUE"):
            continue
        n = m[1]
        fs = q_fields.get(n, [])
        qrefs += emit(
            q,
            identifier(n),
            int(m[2], 16),
            32,
            fs,
            fs or (n in q_read),
            (fs or True) if n in q_write else [],
            True,
            f"{qp}:{line}; 32-bit C access.",
        )
    qrefs += emit(
        q,
        "qspi_rbdr",
        0x200,
        32,
        [],
        True,
        False,
        True,
        "IP receive buffer words.",
        {"len": 32, "stride": 4},
    )
    correction(
        qp,
        59,
        "Write views restricted to C-observed writes; masks imported exactly.",
        "All declared offsets occur in the C diagnostic read table. Unused configuration offsets remain readable numeric registers without speculative setters.",
    )
    # The driver defines a sequence index, but does not bound every hardware LUT
    # sequence. Expose only its two explicitly selected sequences (8 words).
    for seq in range(2):
        for word in range(4):
            qrefs += emit(
                q,
                f"qspi_lut_{seq}_{word}",
                0x310 + seq * 16 + word * 4,
                32,
                [],
                True,
                True,
                True,
                "Driver-selected lookup word; no reset assertion.",
            )
    manifest["tables"].append(
        {
            "source": qp,
            "line": 59,
            "status": "generated",
            "registers": qrefs,
            "note": "Tableless C QSPI offsets; read/direct-write views avoid unproven modify semantics. RX array has 32 words; only two driver-selected LUT sequences have established bounds.",
        }
    )
    instance("QSPI", q, 0xD420C000, DOC_DIR + "06_address_map.md", 180)

    # DPU C writes establish numeric registers, including 64-bit framebuffer
    # address halves. No eight-plane stride can be inferred from a plane-0 driver.
    dpup = "drivers/video/spacemit/spacemit_dpu.c"
    dpu = block("Dpu")
    drefs = []
    seen = set()
    for line, s in enumerate(c_lines[dpup], 1):
        m = re.search(r"dpu_writel\(dpu_base_addr,\s*(0x[0-9a-fA-F]+)", s)
        if not m:
            continue
        off = int(m[1], 16)
        if off in seen:
            continue
        seen.add(off)
        n = {0x1024: "framebuffer_address_lo", 0x1028: "framebuffer_address_hi"}.get(
            off, f"r_{off:05x}"
        )
        drefs += emit(
            dpu,
            n,
            off,
            32,
            [],
            False,
            True,
            True,
            f"{dpup}:{line}; observed 32-bit write.",
        )
    manifest["tables"].append(
        {
            "source": dpup,
            "line": 56,
            "status": "generated",
            "registers": drefs,
            "note": "Tableless DPU C offsets, including framebuffer address halves; no undocumented plane stride inferred.",
        }
    )
    cp = "drivers/video/spacemit/spacemit_dpu.h"
    header_refs = []
    for line, source in enumerate(c_lines[cp], 1):
        match = re.fullmatch(
            r"#define\s+(DPU_INT_REG_\d+)\s+(0x[0-9a-fA-F]+)", source.strip()
        )
        if match:
            header_refs += emit(
                dpu,
                identifier(match[1]),
                int(match[2], 16),
                32,
                [],
                True,
                False,
                True,
                f"{cp}:{line}; numeric offset; write semantics unspecified.",
            )
    manifest["tables"].append(
        {
            "source": cp,
            "line": 13,
            "status": "generated",
            "registers": header_refs,
            "note": "Tableless DPU interrupt register macros; raw read views, no speculative clear/mask setters.",
        }
    )
    instance("DPU0", dpu, 0xC0340000, DOC_DIR + "06_address_map.md", 25)
    instance("DPU1", dpu, 0xC0440000, DOC_DIR + "06_address_map.md", 34)

    # DP field macros encode (byte offset, msb, lsb). Group by actual offset;
    # don't treat DPCD sink addresses as local MMIO.
    dpp = "drivers/video/spacemit/dp/inno_dp_reg.h"
    dp = block("Dp")
    groups = {}
    dprefs = []
    for line, s in enumerate(c_lines[dpp], 1):
        m = re.match(r"#define\s+SOC_DPTX_(\w+)\s+(0x[0-9a-fA-F]+),(\d+),(\d+)", s)
        if not m:
            continue
        off = int(m[2], 16)
        hi = int(m[3])
        lo = int(m[4])
        assert 0 <= lo <= hi < 32
        groups.setdefault(off, []).append(
            (
                line,
                {"name": identifier(m[1]), "bit_offset": lo, "bit_size": hi - lo + 1},
            )
        )
    write_names = set()
    for cp in [
        "drivers/video/spacemit/dp/inno_dp.c",
        "drivers/video/spacemit/dp/inno_dp_phy.c",
    ]:
        for s in c_lines[cp]:
            m = re.search(r"soc_dp_reg_(?:only_)?write_range\(\w+,\s*SOC_DPTX_(\w+)", s)
            if m:
                write_names.add(identifier(m[1]))
    for off, rows in sorted(groups.items()):
        fields = [v for _, v in rows]
        # C macros preserve every named bit. Only C-observed field writes get
        # setters: do not grant a setter to analog PLL lock/status bits.
        write_fields = [f for f in fields if f["name"] in write_names]
        dprefs += emit(
            dp,
            f"r_{off:04x}",
            off,
            32,
            fields,
            fields,
            write_fields,
            True,
            f"{dpp}:{rows[0][0]}; C-observed writes only; no modify.",
        )
        unresolved = [f["name"] for f in fields if f["name"] not in write_names]
        if unresolved:
            correction(
                dpp,
                rows[0][0],
                "Non-written macro fields retained in read view; write permission not inferred.",
                "C consumers do not establish setters for: " + ", ".join(unresolved),
            )
    manifest["tables"].append(
        {
            "source": dpp,
            "line": 10,
            "status": "generated",
            "registers": dprefs,
            "note": "Tableless local DP field macros; C readl/writel width. Status and direct-write views; no reset asserted.",
        }
    )
    instance("DP0", dp, 0xCAC84000, DOC_DIR + "06_address_map.md", 49)
    instance("DP1", dp, 0xCAC88000, DOC_DIR + "06_address_map.md", 50)

    # DDR C pointers explicitly use uint32_t slots, including the PHY whose
    # initialization payloads are uint16_t. Do not confuse payload and bus width.
    ctl = block("Ddrc")
    cfg = block("DdrConfig")
    phy = block("DdrPhy")
    for b, ptr in [(ctl, "ddrc_reg"), (cfg, "cfg_reg"), (phy, "dphy_reg")]:
        observations = {}
        permissions = {}
        for cp in [
            "drivers/ddr/spacemit/k3/lpddr4x_init.c",
            "drivers/ddr/spacemit/k3/lpddr5_init.c",
            "drivers/ddr/spacemit/k3/lpddr_common.c",
        ]:
            for line, s in enumerate(c_lines[cp], 1):
                if s.lstrip().startswith("//"):
                    continue
                for m in re.finditer(
                    r"\b" + ptr + r"\[\s*(0x[0-9a-fA-F]+)\s*(/\s*4)?\s*\]", s
                ):
                    off = int(m[1], 16) * (1 if m[2] else 4)
                    observations.setdefault(off, (cp, line))
                    tail = s[m.end() :].lstrip()
                    assignment = re.match(r"(\|=|&=|\^=|=(?!=))", tail)
                    readable = not assignment or assignment[1] != "="
                    writable = bool(assignment)
                    old = permissions.get(off, (False, False))
                    permissions[off] = (old[0] or readable, old[1] or writable)
        if b == phy:
            # These two C source arrays are sparse word-indexed lane controls,
            # not contiguous planes. LPDDR5 has five entries; LPDDR4x a subset.
            cp = "drivers/ddr/spacemit/k3/lpddr5_init.c"
            for label, count in [
                ("tx_impedance_array", 4),
                ("tx_impedance_array1", 2),
                ("odt_impedance_array", 4),
            ]:
                text = "\n".join(c_lines[cp])
                match = re.search(
                    r"static const uint32_t " + label + r"\[\] = \{(.*?)\};", text
                )
                assert match
                slots = [int(v.strip(), 16) for v in match[1].split(",")]
                offsets = sorted(
                    (slot + i * 0x1000) * 4 for i in range(count) for slot in slots
                )
                refs = emit(
                    phy,
                    label,
                    offsets[0],
                    32,
                    [],
                    False,
                    True,
                    True,
                    "C sparse per-lane PHY word indices.",
                    {"offsets": [v - offsets[0] for v in offsets]},
                )
                line = next(
                    i
                    for i, s in enumerate(c_lines[cp], 1)
                    if "static const uint32_t " + label + "[]" in s
                )
                manifest["tables"].append(
                    {
                        "source": cp,
                        "line": line,
                        "status": "generated",
                        "registers": refs,
                        "note": f"{count} lanes, sparse slot list from C; uint32_t MMIO, uint16_t payload does not narrow access. LPDDR4x is a subset.",
                    }
                )
            for cp in [
                "drivers/ddr/spacemit/k3/lpddr4x_init.c",
                "drivers/ddr/spacemit/k3/lpddr5_init.c",
            ]:
                for line, s in enumerate(c_lines[cp], 1):
                    m = re.search(r"reg_table\[\d+\]\.offset = (0x[0-9a-fA-F]+);", s)
                    if m:
                        off = int(m[1], 16) * 4
                        observations.setdefault(off, (cp, line))
                        old = permissions.get(off, (False, False))
                        permissions[off] = (old[0], True)
        # Header PHY slots are word indices; known named definitions coexist
        # with directly accessed offsets, never as overlapping duplicate items.
        named = {}
        if b == phy:
            cp = "drivers/ddr/spacemit/k3/k3_ddr.h"
            for line, s in enumerate(c_lines[cp], 1):
                m = re.match(r"#define\s+(\w+)\s+\((0x[0-9A-Fa-f]+)\)", s)
                if m and 52 <= line <= 61:
                    off = int(m[2], 16) * 4
                    named[off] = identifier(m[1])
                    observations.setdefault(off, (cp, line))
        by_source = {}
        for off, (cp, line) in sorted(observations.items()):
            n = named.get(off, f"r_{off:06x}")
            read, write = permissions.get(off, (True, False))
            if off not in permissions:
                correction(
                    cp,
                    line,
                    "Macro-only register retained as raw read view; no speculative setter.",
                    "Numeric word index supplied; selected C accessors do not establish a write operation at this slot.",
                )
            refs = emit(
                b,
                n,
                off,
                32,
                [],
                read,
                write,
                True,
                f"{cp}:{line}; C 32-bit slot, reset unknown.",
            )
            by_source.setdefault(cp, []).extend(refs)
        for cp, refs in by_source.items():
            line = min(number for path, number in observations.values() if path == cp)
            manifest["tables"].append(
                {
                    "source": cp,
                    "line": line,
                    "status": "generated",
                    "registers": refs,
                    "note": f"Tableless {b} C uint32_t register slots; no initialization value interpreted as reset.",
                }
            )
    for i, base in enumerate([0xCB000000, 0xCC000000]):
        for label, b, delta in [
            ("DDRC", ctl, 0),
            ("DDR_CONFIG", cfg, 0x600000),
            ("DDR_PHY", phy, 0x800000),
        ]:
            instance(
                label + str(i), b, base + delta, DOC_DIR + "06_address_map.md", 68 + i
            )
    for cp, line in [
        ("drivers/ddr/spacemit/k3/lpddr4x_init.c", 27),
        ("drivers/ddr/spacemit/k3/lpddr5_init.c", 189),
    ]:
        manifest["tables"].append(
            {
                "source": cp,
                "line": line,
                "status": "not_mmio",
                "registers": [],
                "note": "Firmware data-memory clearing loop (PHY DMEM word indices below 0x60000), not named control registers. Firmware training payload SRAM tables are not imported as registers.",
            }
        )
    # Source gaps describe unavailable numeric layouts, not hardware readiness.
    for chapter, line, note in [
        (
            "09_memory_storage.md",
            17,
            "LPDDR narrative has no complete DDRCTL/PHY register layout. C direct slots supplemented; training SRAM payload tables are not named register definitions.",
        ),
        (
            "09_memory_storage.md",
            259,
            "QSPI manual has no register tables. C supplements known offsets; LUT sequences beyond driver-selected 0/1 lack established bound in this pinned driver.",
        ),
        (
            "09_memory_storage.md",
            2075,
            "UFS analog backdoor mentions byte-wide ATOP accesses, but no ATOP register list or resolved base (C0A8 versus UFS C0E0); no invented analog registers.",
        ),
        (
            "11_video.md",
            5,
            "Video chapter has no numeric register definitions. Pinned ESOS has no K3 video driver; available pinned U-Boot contains no VPU layout. Linux pinned tree API returned 404; no codec register bank invented.",
        ),
        (
            "12_display.md",
            7,
            "DPU manual is feature-only. C supplements all literal DPU writes; full eight-plane offsets/stride and secure-layout equivalence not supplied, so no plane array or secure instances invented.",
        ),
        (
            "12_display.md",
            1044,
            "DP/eDP manual has no numeric register definitions. Local DP field macro offsets supplemented from pinned U-Boot; no sink DPCD registers treated as MMIO.",
        ),
        (
            "12_display.md",
            832,
            "Dynamic PLL offset/32-bit container known; detailed bits referenced only by inaccessible vendor file URL. Full-width field retained.",
        ),
    ]:
        correction(DOC_DIR + chapter, line, note, f"{DOC_DIR}{chapter}:{line}")
    for key, value in ir.items():
        if key.startswith("block/"):
            value["items"].sort(key=lambda i: (i["byte_offset"], i["name"]))
    manifest["write_mask_registers"].sort()
    manifest["instances"].sort(key=lambda i: i["name"])
    for line in (23, 324):
        manifest['tables'].append({'source': DOC_DIR + '07_interrupts.md',
            'line': line, 'status': 'not_mmio', 'registers': [],
            'note': 'Interrupt-source assignments, not numeric MMIO register layouts.'})
    manifest["tables"].sort(key=lambda i: (i["source"], i["line"]))
    manifest["corrections"].sort(key=lambda i: (i["source"], i["line"], i["decision"]))
    return ir, manifest


def render(value):
    return json.dumps(value, indent=2, ensure_ascii=True) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DOC_ROOT)
    parser.add_argument("--c-root", type=Path, default=C_ROOT)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    ir, manifest = build(args.source_root, args.c_root)
    for p, value in [
        ("data/registers/expanded_k3_memory.yaml", ir),
        ("provenance/expanded/k3_memory.json", manifest),
    ]:
        target = args.repo_root / p
        content = render(value)
        if args.check:
            if target.read_text() != content:
                raise SystemExit("stale: " + p)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
    print(
        f"{len(manifest['tables'])} accounted tables/dispositions; "
        + f"{sum(k.startswith('block/') for k in ir)} blocks; "
        + f"{sum(len(v['items']) for k, v in ir.items() if k.startswith('block/'))} register views; "
        + f"{sum(len(v['fields']) for k, v in ir.items() if k.startswith('fieldset/'))} fields; "
        + f"{len(manifest['instances'])} instances"
    )


if __name__ == "__main__":
    main()
