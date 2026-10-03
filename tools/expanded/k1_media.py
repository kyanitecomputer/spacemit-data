#!/usr/bin/env python3
"""Deterministic K1 media numeric facts; no vendor description text is emitted.

Linux input is the pinned 31c449aeaad8c7759bc983ca0e26946e5b6746dc tree.
No network access is performed; --linux-root selects an independently obtained cache.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import manual_tables as mt

DOC_DIR = "k1/k1_docs/k1_usermanual/"
CHAPTERS = [
    "11.Video_&_Graphics.md",
    "12.Display_Subsystem.md",
    "13.Video_Capture_Subsystem.md",
]
LINUX_REV = "31c449aeaad8c7759bc983ca0e26946e5b6746dc"
LINUX_URL = "https://github.com/spacemit-com/linux-6.6/blob/" + LINUX_REV + "/"
DPU_DIR = "drivers/gpu/drm/spacemit/dpu/saturn_regs/"
CAM_DIR = "drivers/media/platform/spacemit/camera/"
HW_DIR = CAM_DIR + "vi/k1xvi/hw-seq/"
V2D_DIR = "drivers/soc/spacemit/v2d/"
VPU_DIR = "drivers/media/platform/spacemit/vpu_k1x/dev/"

# Corrections are source anchored, not name-derived hardware assumptions.
DOC_ALIASES = {
    "dsi_irq_st": "C writes observed interrupt status back at spacemit_dsi_drv.c:872; expose acknowledgement separately.",
    "dsi_cpu_status_0": "The field description explicitly specifies write-clear.",
    "dsi_cpn_status_0": "The field description explicitly specifies write-clear.",
    "dsi_rx_pkt_st_0": "All nonreserved fields have RWC access.",
    "dsi_rx_pkt_st_1": "All nonreserved fields have RWC access.",
    "dsi_rx_pkt_st_2": "All nonreserved fields have RWC access.",
    "dsi_rx_pkt_ctrl_1": "All nonreserved fields have RWC access.",
    "spu_irq_isr_raw_reg": "Mixed W0C/status; write zero clears upper interrupt bits. Read-clear behavior is controlled by SPU_IRQ_RSR. No modify view.",
    "lcd_wdma_ctrl4_reg": "Mixed control and interrupt-clear fields; independent write view avoids implicit read-modify-write.",
}


def uncomment(s):
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", s, flags=re.S)


def defines(content):
    """Restricted numeric object/function macros with exact source lines."""
    out = {}
    for line, s in enumerate(content.splitlines(), 1):
        m = re.match(r"\s*#define\s+(\w+)(\([^)]*\))?\s+(.+)", s)
        if m:
            out[m[1]] = (m[2], uncomment(m[3]).strip(), line)
    return out


def evaluate(expr, macros, variables=None, stack=()):
    expr = re.sub(r"\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]+\b", r"\1", expr)
    expr = re.sub(r"\bBIT\((\d+)\)", lambda m: str(1 << int(m[1])), expr)
    expr = re.sub(r"IMG_UINT64_C\((0x[\da-fA-F]+)\)", r"\1", expr)
    variables = variables or {}

    def sub(m):
        n = m[0]
        if n in variables:
            return str(variables[n])
        if n in macros and macros[n][0] is None:
            if n in stack:
                raise ValueError("recursive macro " + n)
            return str(evaluate(macros[n][1], macros, variables, stack + (n,)))
        return n

    expr = re.sub(r"\b[A-Za-z_]\w*\b", sub, expr)
    return mt.integer(expr)


def gpu_access_evidence(sources):
    """Exact constant register arguments to native accessors, with original lines.

    Dynamic offsets/expressions and MASKFULL typedefs are not width evidence.
    Keep conflicting source widths distinct rather than selecting by name/type.
    """
    result = {}
    pattern = re.compile(r"(?:RGX|OS)(?:Read|Write)(?:UncheckedHW|HW)?Reg(32|64)\s*\(\s*[^,]+,\s*(RGX_CR_\w+)\s*[,)]")
    for path, content in sources.items():
        clean = re.sub(r"/\*.*?\*/|//[^\n]*", lambda m: "".join("\n" if c == "\n" else " " for c in m[0]), content, flags=re.S)
        for match in pattern.finditer(clean):
            width = int(match[1])
            line = clean[:match.start()].count("\n") + 1
            result.setdefault(match[2], {}).setdefault(width, []).append((path, line))
    return result


def functions(content):
    """Top-level function bodies, excluding comments; braces are balanced."""
    clean = uncomment(content)
    for m in re.finditer(r"^\w[\w\s*]*\s+\w+\([^;]*?\)\s*\{", clean, re.M):
        start = m.end()
        depth = 1
        end = start
        while depth and end < len(clean):
            if clean[end] == "{":
                depth += 1
            if clean[end] == "}":
                depth -= 1
            end += 1
        if depth:
            raise ValueError("unterminated function")
        yield clean[start : end - 1]


def bitfields(content):
    fields = []
    pos = 0
    for m in re.finditer(
        r"(?:unsigned\s+int|int|UINT32)\s*(\w+)?\s*:\s*(\d+)\s*;", uncomment(content)
    ):
        name, width = m[1], int(m[2])
        if width < 1 or pos + width > 32:
            raise ValueError("malformed 32-bit C field layout")
        if name and not re.search(r"reserv|rsvd", name, re.I):
            fields.append(
                {"name": mt.identifier(name), "bit_offset": pos, "bit_size": width}
            )
        pos += width
    if pos != 32:
        raise ValueError(f"C word accounts for {pos} bits, not 32")
    return fields


class Importer:
    def __init__(self, docs, linux):
        self.docs, self.linux = docs, linux
        self.ir: dict[str, Any] = {}
        self.manifest: dict[str, Any] = {
            "schema": 1,
            "chip": "k1",
            "registers": [
                "expanded_k1_media",
                "expanded_k1_media_dpu",
                "expanded_k1_media_gpu",
            ],
            "sources": [],
            "instances": [],
            "aliases": {},
            "write_mask_registers": [],
            "tables": [],
            "corrections": [],
        }
        self.seen_sources = set()

    def source(self, path, kind="docs"):
        root = self.docs if kind == "docs" else self.linux
        content = (root / path).read_text()
        if (kind, path) not in self.seen_sources:
            self.seen_sources.add((kind, path))
            self.manifest["sources"].append(
                {
                    "path": path,
                    "kind": kind,
                    "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest(),
                }
            )
        return content

    def correction(self, path, line, decision, evidence=None):
        self.manifest["corrections"].append(
            {
                "source": path,
                "line": line,
                "decision": decision,
                "evidence": evidence or LINUX_URL + path + "#L" + str(line),
            }
        )

    def table(self, path, line, status, regs=(), note=None):
        if ("docs", path) in self.seen_sources:
            lines = (self.docs / path).read_text().splitlines()
            is_header = (
                line < len(lines)
                and lines[line - 1].lstrip().startswith("|")
                and re.fullmatch(r"[|:\s-]+", lines[line]) is not None
            )
            if not is_header:
                self.correction(
                    path,
                    line,
                    note or "No numeric register table in this section.",
                    path + ":" + str(line),
                )
                return
        row: dict[str, Any] = {
            "source": path,
            "line": line,
            "status": status,
            "registers": list(regs),
        }
        if note:
            row["note"] = note
        elif ("linux", path) in self.seen_sources:
            row["note"] = (
                "Concrete native C numeric declaration."
                if status == "generated"
                else "Same numeric word is accounted by its packed or indexed view."
            )
        elif status == "duplicate":
            row["note"] = "Same numeric word is accounted by another source table."
        self.manifest["tables"].append(row)

    def block(self, name):
        name = "k1_media::" + name
        self.ir.setdefault(
            "block/" + name,
            {"description": "K1 media numeric MMIO facts.", "items": []},
        )
        return name

    def reg(
        self,
        block,
        name,
        offset,
        fields=(),
        access="ReadWrite",
        width=32,
        array=None,
        alias=False,
        path="",
        line=1,
        note="",
    ):
        name = mt.identifier(name)
        item: dict[str, Any] = {
            "name": name,
            "byte_offset": offset,
            "access": access,
            "bit_size": width,
            "description": f"{path}:{line}. {note}".strip(),
        }
        if fields:
            key = block + "_" + name
            self.ir["fieldset/" + key] = {
                "bit_size": width,
                "fields": copy.deepcopy(list(fields)),
            }
            item["fieldset"] = key
        if array:
            item["array"] = array
        if alias:
            item["access"] = "Read"
            write = copy.deepcopy(item)
            write["name"] += "_write_view"
            write["access"] = "Write"
            self.ir["block/" + block]["items"].extend([item, write])
            self.manifest["aliases"].setdefault(block, []).append([name, write["name"]])
            self.manifest["write_mask_registers"].append(block + "." + write["name"])
            return [block + "." + name, block + "." + write["name"]]
        self.ir["block/" + block]["items"].append(item)
        if access == "Write":
            self.manifest["write_mask_registers"].append(block + "." + name)
        return [block + "." + name]

    def instance(self, name, block, addr, path, line, view="ap"):
        self.manifest["instances"].append(
            {
                "name": view.upper() + "_K1_MEDIA_" + name.upper(),
                "block": block,
                "address": addr,
                "view": view,
                "source": {"path": path, "line": line},
            }
        )

    def docs_import(self):
        for chapter in CHAPTERS:
            path = DOC_DIR + chapter
            content = self.source(path)
            tables = mt.extract(self.docs / path, self.docs)
            for t in tables:
                if chapter != CHAPTERS[1]:
                    is_reg = t["header"][0] == "Register"
                    self.table(
                        path,
                        t["line"],
                        "source_gap" if is_reg else "not_mmio",
                        note=(
                            "Algorithm-level names and widths; physical mapping absent here. V2D C register layout is imported separately."
                            if is_reg
                            else "Algorithm variable widths, not MMIO fields."
                        ),
                    )
                    continue
                name = mt.identifier(re.sub(r"\s*REGISTER$", "", t["heading"][-1]))
                block = self.block("Dsi" if name.startswith("dsi_") else "SpiLcd")
                offset = mt.integer(t["offset_labels"][-1]["value"].split(":", 1)[1])
                fields, types, reset, used = [], set(), 0, 0
                for row in t["rows"]:
                    bits, field, typ, rst = row["values"]
                    low, width = mt.bit_range(bits)
                    mask = ((1 << width) - 1) << low
                    if used & mask:
                        raise ValueError(f"{path}:{row['line']}: overlapping field")
                    used |= mask
                    v = mt.integer(rst)
                    if v >= 1 << width:
                        raise ValueError(f"{path}:{row['line']}: invalid reset")
                    reset |= v << low
                    if re.fullmatch(r"RSVD|Reserved", field, re.I):
                        continue
                    types.add(typ)
                    f = {
                        "name": mt.identifier(field),
                        "bit_offset": low,
                        "bit_size": width,
                        "description": f"Reset {hex(v)}; source access {typ}; line {row['line']}.",
                    }
                    fields.append(f)
                for f in fields:
                    if sum(other["name"] == f["name"] for other in fields) > 1:
                        original = f["name"]
                        for other in fields:
                            if other["name"] == original:
                                other["name"] += (
                                    "_bits_"
                                    + str(other["bit_offset"])
                                    + "_"
                                    + str(other["bit_offset"] + other["bit_size"] - 1)
                                )
                        self.correction(
                            path,
                            t["line"],
                            "Repeated field label has distinct bit ranges; suffix each physical range instead of guessing semantic names.",
                            "K1 V6.3 PDF SHA256 484fe0fb426d67c0472cd17e430f9abea00538fa42432431252f8e031c0af88d, physical pages 522-523 / section 12.4.5; Markdown rows 2187 and 2189.",
                        )
                alias = name in DOC_ALIASES or "RWC" in types
                access = "Read" if types == {"RO"} else "ReadWrite"
                regs = self.reg(
                    block,
                    name,
                    offset,
                    fields,
                    access=access,
                    alias=alias,
                    path=path,
                    line=t["line"],
                    note=f"Documented reset {hex(reset)}. " + DOC_ALIASES.get(name, ""),
                )
                self.table(path, t["line"], "generated", regs)
                if name in DOC_ALIASES:
                    self.correction(
                        path,
                        t["line"],
                        DOC_ALIASES[name],
                        LINUX_URL
                        + "drivers/gpu/drm/spacemit/dsi/spacemit_dsi_drv.c#L872"
                        if name == "dsi_irq_st"
                        else path + ":" + str(t["line"]),
                    )
            # Include register-name tables missed by the common numeric-table filter.
            if chapter == CHAPTERS[0]:
                covered = {t["line"] for t in tables}
                for i, s in enumerate(content.splitlines(), 1):
                    if s.startswith("| Register") and i not in covered:
                        self.table(
                            path,
                            i,
                            "source_gap",
                            note="Algorithm register names lack offsets; V2D C declarations provide numeric layout separately.",
                        )
                for needle, reason in [
                    (
                        "### 11.1.1",
                        "VPU chapter has no register map; pinned Linux host/LSID layout imported.",
                    ),
                    (
                        "### 11.2.1",
                        "GPU chapter has no register map; portable RGX definitions are not a K1-specific access map.",
                    ),
                ]:
                    self.table(
                        path,
                        content[: content.index(needle)].count("\n") + 1,
                        "source_gap",
                        note=reason,
                    )
            if chapter == CHAPTERS[1]:
                self.table(
                    path,
                    5,
                    "source_gap",
                    note="Display-controller registers absent from section 12.1; complete Saturn C struct layouts imported separately. Reserved C words are not registers.",
                )
                self.table(
                    path,
                    43,
                    "source_gap",
                    note="HDMI section contains no registers; all concrete native 32-bit MMIO offsets from pinned HDMI C imported separately.",
                )
            if chapter == CHAPTERS[2]:
                self.table(
                    path,
                    46,
                    "source_gap",
                    note="CSI register description says To be defined; numeric CCIC C register map imported separately.",
                )
                self.table(
                    path,
                    79,
                    "source_gap",
                    note="ISP register description says To be defined; published C top/DMA/postpipe/IOMMU maps imported. Published statistics/PDC words are also imported; full algorithm tuning register map remains unpublished.",
                )
        path = DOC_DIR + "6.Address_Mapping.md"
        self.source(path)
        self.source(DOC_DIR + "7.Interrupt_Assignments.md")
        self.correction(
            DOC_DIR + "7.Interrupt_Assignments.md",
            19,
            "Media IRQ assignments consulted: VPU 74, GPU 75, ISP 79/80/85, CSI 81/82/83, CPP 84, V2D 86, JPEG 87, display 88-95 and 130-140. These are not register offsets.",
            LINUX_URL + "arch/riscv/boot/dts/spacemit/k1-x.dtsi#L2501",
        )
        self.instance("DSI", self.block("Dsi"), 0xD421A800, path, 128)
        # Address-map location is authoritative; chapter 12's base collides with IPE1.
        lines = (self.docs / path).read_text().splitlines()

        def mapline(module):
            return next(
                i for i, s in enumerate(lines, 1) if s.startswith("| " + module + " |")
            )

        self.manifest["instances"][-1]["source"]["line"] = mapline("LCD_DSI")
        self.instance(
            "SPI_LCD", self.block("SpiLcd"), 0xD4209000, path, mapline("SPI_LCD")
        )
        self.correction(
            DOC_DIR + CHAPTERS[1],
            1239,
            "Use address-map SPI_LCD 0xD4209000, not chapter base 0xD420A000 (IPE1). Retain conflict for review.",
            path
            + ":"
            + str(mapline("SPI_LCD"))
            + "; K1 V6.3 PDF physical page 95 / section 6.2 corroborates 0xD4209000; page 470 / section 12.4.5 repeats the chapter conflict. PDF SHA256 484fe0fb426d67c0472cd17e430f9abea00538fa42432431252f8e031c0af88d.",
        )

    def c_numeric(
        self, path, block_name, selector, bounds=None, bias=0, access_overrides=None
    ):
        content = self.source(path, "linux")
        macros = defines(content)
        block = self.block(block_name)
        regs = {}
        for n, (params, expr, line) in macros.items():
            if not selector(n, expr):
                continue
            if params:
                if bounds is None or n not in bounds:
                    continue
                var = params.strip("() ")
                values = bounds[n]
                offsets = [evaluate(expr, macros, {var: i}) + bias for i in values]
                array = (
                    {"len": len(offsets), "stride": offsets[1] - offsets[0]}
                    if len(offsets) > 1
                    else None
                )
                offset = offsets[0]
            else:
                try:
                    offset = evaluate(expr, macros) + bias
                except (ValueError, SyntaxError):
                    continue
                array = None
            if offset < 0 or offset % 4:
                raise ValueError(f"unaligned register {path}:{line}")
            # Exact packed-word synonyms are one numeric register, not independent RMW aliases.
            signature = (offset, json.dumps(array, sort_keys=True))
            if signature in regs:
                self.correction(
                    path,
                    line,
                    n
                    + " shares the word with "
                    + regs[signature][0]
                    + "; preserve packed-word identity.",
                )
                self.table(path, line, "duplicate", regs[signature][1])
                continue
            access = (access_overrides or {}).get(n, "Alias")
            alias = access == "Alias"
            names = self.reg(
                block,
                n,
                offset,
                access="ReadWrite" if alias else access,
                array=array,
                alias=alias,
                path=path,
                line=line,
                note="Native 32-bit C MMIO. Reset not specified.",
            )
            regs[signature] = (n, names)
            self.table(path, line, "generated", names)
        return block

    def dsi_compare(self):
        path = "drivers/gpu/drm/spacemit/dsi/spacemit_dsi_hw.h"
        macros = defines(self.source(path, "linux"))
        self.source("drivers/gpu/drm/spacemit/dsi/spacemit_dsi_drv.c", "linux")
        block = self.block("Dsi")
        items = {i["name"]: i for i in self.ir["block/" + block]["items"]}
        for name, (params, expr, line) in macros.items():
            if (
                not name.startswith("DSI_")
                or params
                or not re.fullmatch(r"0x[0-9a-fA-F]+", expr)
            ):
                continue
            if name.startswith("DSI_FPGA_"):
                self.table(
                    path,
                    line,
                    "not_mmio",
                    note="CONFIG_SPACEMIT_FPGA-only test controls; not asserted as K1 silicon registers.",
                )
                continue
            identifier = mt.identifier(name)
            if identifier not in items or items[identifier]["byte_offset"] != evaluate(
                expr, macros
            ):
                raise ValueError("DSI C/manual disagreement: " + name)
            names = [block + "." + identifier]
            if identifier + "_write_view" in items:
                names.append(block + "." + identifier + "_write_view")
            self.table(
                path,
                line,
                "duplicate",
                names,
                note="Pinned native C offset matches the generated Markdown register.",
            )
        self.correction(
            "drivers/gpu/drm/spacemit/dpu/dpu_saturn.c",
            954,
            "LCD Saturn base 0xC0340000 is corroborated by pinned U-Boot display initialization.",
            "https://github.com/spacemit-com/uboot-2022.10/blob/28805afe074715a0ede143d42999ea06bee58fbd/drivers/video/spacemit/spacemit_dpu.c#L200",
        )

    def v2d(self):
        path = V2D_DIR + "v2d_reg.h"
        content = self.source(path, "linux")
        self.source(V2D_DIR + "v2d_hw.c", "linux")
        self.source(V2D_DIR + "v2d_iommu.c", "linux")
        bounds = {"V2D_SCALER_COEF_REG": range(24)}
        overrides = {
            "V2D_ERR_IRQ_STATUS": "Alias",
            "V2D_IRQ_STATUS": "Alias",
            "V2D_ENC_REG18": "Alias",
            "V2D_ENC_REG14": "Write",
            "V2D_ERR_IRQ_RAW": "Read",
            "V2D_IRQ_RAW": "Read",
        }
        # Every explicit concrete TOP/CORE/ENC/DEC word, including source spelling RGG/CRTL.
        block = self.c_numeric(
            path,
            "V2d",
            lambda n, e: (
                n.startswith("V2D_")
                and any(
                    k in e
                    for k in [
                        "V2D_TOP_BASE",
                        "V2D_CORE_BASE",
                        "V2D_ENC_BASE",
                        "V2D_L0_DEC_BASE",
                        "V2D_L1_DEC_BASE",
                    ]
                )
                and ("(" not in n)
            ),
            bounds=bounds,
            access_overrides=overrides,
        )
        # Replace explicit coefficient words with the corroborated indexed bank.
        items = self.ir["block/" + block]["items"]
        items[:] = [x for x in items if not x["name"].startswith("v2d_scaler_coef_")]
        self.manifest["aliases"][block] = [
            g
            for g in self.manifest["aliases"][block]
            if not g[0].startswith("v2d_scaler_coef_")
        ]
        self.manifest["write_mask_registers"] = [
            r
            for r in self.manifest["write_mask_registers"]
            if not r.startswith(block + ".v2d_scaler_coef_")
        ]
        # The macro's array is already present; update summary references of duplicates.
        for t in self.manifest["tables"]:
            if t["source"] == path and any(
                ".v2d_scaler_coef_" in r for r in t["registers"]
            ):
                t["status"] = "duplicate"
                t["registers"] = [block + ".v2d_scaler_coef_reg"]
        # Exclude the duplicate coefficient aliases captured before the array macro.
        self.ir["block/" + block]["items"][:] = [
            x for x in items if x["name"] != "v2d_scaler_coef_reg"
        ]
        regs = self.reg(
            block,
            "v2d_scaler_coef_reg",
            0x104,
            array={"len": 24, "stride": 4},
            path=path,
            line=59,
        )
        self.table(path, 59, "generated", regs)
        # The first palette word is known; the C ABI does not establish a fixed bound.
        palette_line = defines(content)["V2D_PALETTE_TABLE"][2]
        palette = self.reg(
            block,
            "v2d_palette_entry0",
            0x308,
            access="Write",
            path=path,
            line=palette_line,
            note="First native 32-bit palette word. C index formula +4*i; maximum entry count unspecified.",
        )
        self.table(
            path,
            palette_line,
            "generated",
            palette,
            note="Known first palette word; no invented array upper bound.",
        )
        self.table(
            V2D_DIR + "v2d_hw.c",
            727,
            "source_gap",
            note="Palette loop count is caller len/bpp, not a declared hardware bound. Only known entry0 is generated; offset formula is 0x308+4*i.",
        )
        self.instance("V2D", block, 0xC0100000, DOC_DIR + "6.Address_Mapping.md", 22)
        self.v2d_fields(content, block)
        self.correction(
            path,
            108,
            "Layer1 words repeat layer0 at stride 0x54; explicit banks preserve packed high-address/control sharing. Split addresses stay 32-bit MMIO.",
        )
        # The IOMMU lives at its own +0xB00 sub-bank; TBU count explicitly 32.
        mmu = self.c_numeric(
            path,
            "V2dMmu",
            lambda n, e: (
                n.startswith("V2D_MMU_") and not n.endswith(("_STEP", "_BASE"))
            ),
            access_overrides={
                "V2D_MMU_IRQ_STATUS": "Read",
                "V2D_MMU_ERROR_CLEAR": "Write",
                "V2D_MMU_VERSION": "Read",
                "V2D_MMU_TIMEOUT_VA_ADDR_LO": "Read",
                "V2D_MMU_TIMEOUT_VA_ADDR_HI": "Read",
                "V2D_MMU_LAST_VA_ADDR_LO": "Read",
                "V2D_MMU_LAST_VA_ADDR_HI": "Read",
                "V2D_MMU_LAST_PA_ADDR_LO": "Read",
                "V2D_MMU_LAST_PA_ADDR_HI": "Read",
            },
        )
        macros = defines(content)
        for n in ["TTBLR", "TTBHR", "TCR0", "TCR1", "TBU_STATUS"]:
            macro = "V2D_MMU_" + n + "_BASE"
            offset = evaluate(macros[macro][1], macros)
            names = self.reg(
                mmu,
                macro,
                offset,
                access="Read" if n == "TBU_STATUS" else "ReadWrite",
                array={"len": 32, "stride": 0x20},
                path=path,
                line=macros[macro][2],
            )
            self.table(path, macros[macro][2], "generated", names)
        self.instance("V2D_MMU", mmu, 0xC0100B00, path, 262)
        self.correction(
            path,
            250,
            "32 TBU entries use native 32-bit words; source TBU_NUM and V2D_MMU_TBUx_STEP bound the indexed banks.",
        )

    def v2d_fields(self, content, block):
        path = V2D_DIR + "v2d_reg.h"
        types = {}
        for m in re.finditer(
            r"typedef\s+union\s*\{\s*struct\s*\{(.*?)\}\s*field;\s*unsigned int overlay;[^}]*\}\s*(\w+);",
            uncomment(content),
            re.S,
        ):
            types[m[2]] = bitfields(m[1])
        for m in re.finditer(r"typedef\s+(\w+_reg_t)\s+(\w+_reg_t);", content):
            types[m[2]] = types[m[1]]
        macros = defines(content)
        by_offset = {}
        for body in functions(self.source(V2D_DIR + "v2d_hw.c", "linux")):
            variables = {}
            for m in re.finditer(r"(\w+_reg_t)\s+([\w, ]+);", body):
                for var in m[2].split(","):
                    variables[var.strip()] = m[1]
            for m in re.finditer(
                r"v2d_write\((V2D_\w+)(?:\((\w+)\))?(?:\s*\+\s*0x4\s*\*\s*i)?,\s*(\w+)\.overlay\)",
                body,
            ):
                macro, param, var = m[1], m[2], m[3]
                if var not in variables:
                    raise ValueError("unresolved V2D word type " + var)
                fs = types[variables[var]]
                # The coefficient and CSC loops are bounded explicitly in the C body.
                indices = (
                    range(24)
                    if macro == "V2D_SCALER_COEF_REG"
                    else range(2)
                    if param
                    else [0]
                )
                for i in indices:
                    values = {param: i} if param else {}
                    params, expr, _ = macros[macro]
                    if params:
                        values[params.strip("() ")] = i
                    offset = evaluate(expr, macros, values)
                    extra = range(5) if macro == "V2D_LAYER_CSC_CRTL1_LAYER" else [0]
                    for j in extra:
                        prior = by_offset.setdefault(offset + 4 * j, fs)
                        if prior != fs:
                            raise ValueError("conflicting V2D union word layout")
        for item in self.ir["block/" + block]["items"]:
            fs = by_offset.get(item["byte_offset"])
            if fs:
                key = block + "_" + item["name"].removesuffix("_write_view")
                self.ir["fieldset/" + key] = {"bit_size": 32, "fields": fs}
                item["fieldset"] = key
        self.correction(
            path,
            476,
            "Packed high-address/control synonyms are one word. Field layouts follow union types passed to v2d_write, including typedef aliases and five CSC coefficient pairs per layer.",
        )
        self.correction(
            DOC_DIR + CHAPTERS[0],
            773,
            "PDF confirms 12 logical signed 13-bit CSC coefficients, not 12 MMIO words. Native C packs coefficient0, five pairs and coefficient11 in seven 32-bit words per layer.",
            "K1 V6.3 PDF SHA256 484fe0fb426d67c0472cd17e430f9abea00538fa42432431252f8e031c0af88d, physical page 412 / section 11.2.2.3.4; "
            + V2D_DIR
            + "v2d_reg.h:563,576,589; "
            + V2D_DIR
            + "v2d_hw.c:618-627",
        )
        self.correction(
            DOC_DIR + CHAPTERS[0],
            585,
            "PDF confirms the Register/Comment headers at Markdown 585/628 are genuine algorithm-register tables without physical offsets; variable data[4][256] arrays are algorithm storage, not an MMIO array.",
            "K1 V6.3 PDF SHA256 484fe0fb426d67c0472cd17e430f9abea00538fa42432431252f8e031c0af88d, physical pages 407-408 / sections 11.2.2.3.1-2",
        )
        self.correction(
            DOC_DIR + CHAPTERS[1],
            5,
            "PDF display-controller section provides capabilities and a block diagram but no Saturn MMIO layout; retain the pinned native C layout and its exact repetitions rather than inventing a PDF register map.",
            "K1 V6.3 PDF SHA256 484fe0fb426d67c0472cd17e430f9abea00538fa42432431252f8e031c0af88d, physical pages 418-419 / sections 12.1.1-3; "
            + DPU_DIR
            + "reg_map.h",
        )

    def c_word_layout(self, path):
        """Read the first UINT32 bitfield struct, including nested repeated structs.

        This restricted parser fails closed. A separate C compiler oracle checks the
        original union sizes and named fields in source-based tests.
        """
        content = self.source(path, "linux")
        start = re.search(r"typedef\s+union\s*\{\s*struct\s*\{", content)
        if not start:
            raise ValueError("no source layout " + path)
        fragment = content[start.end() :]
        toks = [
            (m[0], fragment[: m.start()].count("\n") + 1)
            for m in re.finditer(
                r"//[^\n]*|/\*.*?\*/|[A-Za-z_]\w*|\d+|[{}\[\]:;]", fragment, re.S
            )
        ]
        # Token lines above are relative; restore source start line.
        origin = content[: start.end()].count("\n")
        toks = [(s, line + origin) for s, line in toks]
        cursor = 0

        def parse(default=""):
            nonlocal cursor
            words, pending, fields, field_paths, bits, line = [], default, [], [], 0, 1
            while cursor < len(toks):
                s, ln = toks[cursor]
                cursor += 1
                if s.startswith("//"):
                    m = re.match(r"//\s*(?:REGISTER\s+)?(\w+)\s*$", s)
                    if m:
                        pending, line = m[1], ln
                    continue
                if s.startswith("/*"):
                    continue
                if s == "}":
                    if bits:
                        raise ValueError(f"{path}:{ln}: incomplete word {bits}")
                    return words
                if s == "struct":
                    assert toks[cursor][0] == "{"
                    cursor += 1
                    child = parse(pending)
                    suffix = []
                    while toks[cursor][0] != ";":
                        suffix.append(toks[cursor][0])
                        cursor += 1
                    cursor += 1
                    count = int(suffix[suffix.index("[") + 1]) if "[" in suffix else 1
                    member = suffix[0] if suffix and suffix[0] != "[" else ""
                    for i in range(count):
                        for word in child:
                            word = copy.deepcopy(word)
                            if count > 1:
                                word["name"] += "_" + str(i)
                            if member:
                                component = member + (f"[{i}]" if count > 1 else "")
                                word["paths"] = [
                                    component + "." + p for p in word["paths"]
                                ]
                            words.append(word)
                    pending = ""
                    continue
                if s == "INT32":
                    # Redundant whole-bank value view; it is not a new MMIO range.
                    while toks[cursor][0] != ";":
                        cursor += 1
                    cursor += 1
                    continue
                if s != "UINT32":
                    raise ValueError(f"{path}:{ln}: unsupported token {s}")
                name = ""
                if toks[cursor][0] != ":":
                    name = toks[cursor][0]
                    cursor += 1
                assert toks[cursor][0] == ":"
                cursor += 1
                width = int(toks[cursor][0])
                cursor += 1
                assert toks[cursor][0] == ";"
                cursor += 1
                if not 0 < width <= 32 or bits + width > 32:
                    raise ValueError(f"{path}:{ln}: invalid field width")
                if name:
                    fields.append(
                        {
                            "name": mt.identifier(name),
                            "bit_offset": bits,
                            "bit_size": width,
                        }
                    )
                    field_paths.append(name)
                bits += width
                if bits == 32:
                    words.append(
                        {
                            "name": mt.identifier(pending or "word_" + str(len(words))),
                            "fields": fields,
                            "paths": field_paths,
                            "line": line,
                            "offset": len(words) * 4,
                        }
                    )
                    bits, fields, field_paths, pending = 0, [], [], ""
            raise ValueError("unterminated C layout")

        words = parse()
        outer = toks[cursor][0]
        for i, w in enumerate(words):
            w["offset"] = i * 4
            if outer != ";":
                w["paths"] = [outer + "." + p for p in w["paths"]]
        return words

    def dpu(self):
        self.source(DPU_DIR + "reg_map.h", "linux")
        specs = {
            "dpu_top": ("DpuTop", [0]),
            "dpu_ctl": ("DpuControl", [0x500]),
            "dpu_crg": ("DpuClock", [0x700]),
            "cmdlist": ("DpuCommandList", [0x800]),
            "dpu_intp": ("DpuInterrupt", [0x900]),
            "dma_top": ("DpuDmaTop", [0xA00]),
            "rdma_path_x": ("DpuRdma", [0xA80 + 0x100 * i for i in range(12)]),
            "mmu": ("DpuMmu", [0x1680]),
            "prepipe_layer_proc_x": (
                "DpuLayer",
                [0x2000 + 0x300 * i for i in range(12)],
            ),
            "cmps_x": ("DpuComposer", [0x4600 + 0x300 * i for i in range(4)]),
            "scaler_x": ("DpuScaler", [0x6000, 0x6200, 0x29200, 0x29600]),
            "outctrl_top_x": ("DpuOutput", [0x7000 + 0x8800 * i for i in range(4)]),
            "outctrl_proc_x": (
                "DpuPostprocess",
                [0x7100 + 0x8800 * i for i in range(4)],
            ),
            "wb_top": ("DpuWriteback", [0x29000, 0x29400]),
        }
        for stem, (bn, offsets) in specs.items():
            path = DPU_DIR + stem + ".h"
            words = self.c_word_layout(path)
            block = self.block(bn)
            for w in words:
                if not w["fields"]:
                    continue  # C padding alone is not a source register.
                names = self.reg(
                    block,
                    w["name"],
                    w["offset"],
                    w["fields"],
                    alias=True,
                    path=path,
                    line=w["line"],
                    note="C UINT32 layout. Separate raw views; C header has no access/reset metadata.",
                )
                self.table(path, w["line"], "generated", names)
            for interface, base, dt in [
                ("LCD", 0xC0340000, "k1-x-lcd.dtsi"),
                ("HDMI", 0xC0440000, "k1-x-hdmi.dtsi"),
            ]:
                dtpath = "arch/riscv/boot/dts/spacemit/" + dt
                self.source(dtpath, "linux")
                for i, offset in enumerate(offsets):
                    self.instance(
                        interface + "_" + bn.upper() + str(i),
                        block,
                        base + offset,
                        dtpath,
                        7,
                    )
            self.correction(
                path,
                10,
                "Anonymous UINT32 padding advances offsets but is not a register. C struct repetitions are expanded exactly; independent raw views do not infer modify/reset semantics.",
            )
        bases = defines(self.source(DPU_DIR + "reg_map.h", "linux"))
        for name, (_, _, line) in bases.items():
            if re.match(r"(?:LMERGE|ACAD|LUT3D|DSCA|DSCB)\d+_BASE_ADDR$", name):
                self.table(
                    DPU_DIR + "reg_map.h",
                    line,
                    "source_gap",
                    note=name
                    + " has a numeric sub-bank base but no register layout in the published Saturn headers; no source-less register is invented.",
                )
        self.source("drivers/gpu/drm/spacemit/dpu/dpu_saturn.c", "linux")
        self.source("drivers/gpu/drm/spacemit/spacemit_dpu.h", "linux")

    def capture(self):
        path = CAM_DIR + "cam_ccic/ccic_hwreg.h"
        block = self.c_numeric(
            path,
            "Csi",
            lambda n, e: n.startswith("REG_") and not n.startswith("REG_CLK_"),
            access_overrides={
                "REG_IRQSTATRAW": "Read",
                "REG_IRQSTAT": "Alias",
                "REG_LNNUM": "Read",
                "REG_FRAME_CNT": "Read",
                "REG_CSI_LANE_STATE_DBG": "Read",
            },
        )
        self.source(CAM_DIR + "cam_ccic/ccic_hwreg.c", "linux")
        for i, addr in enumerate([0xD420A000, 0xD420A800, 0xD4206000], 1):
            self.instance(
                "CSI" + str(i),
                block,
                addr,
                DOC_DIR + "6.Address_Mapping.md",
                {1: 126, 2: 127, 3: 123}[i],
            )
        path = HW_DIR + "hw_reg.h"
        content = self.source(path, "linux")
        banks = {
            "isp_top": ("IspTop", [0xC0231700, 0xC0239700]),
            "dma": ("IspDma", [0xC0241000]),
            "pp": ("IspPostpipe", [0xC0240000]),
            "scl": ("IspScaler", [0xC0240100, 0xC0240200]),
        }
        for prefix, (name, addrs) in banks.items():
            block = self.block(name)
            for m in re.finditer(
                r"union\s+" + prefix + r"_reg_(\d+)\s*\{\s*struct\s*\{(.*?)\}\s*field;",
                content,
                re.S,
            ):
                idx = int(m[1])
                fields = bitfields(m[2])
                line = content[: m.start()].count("\n") + 1
                names = self.reg(
                    block,
                    prefix + "_reg_" + str(idx),
                    4 * idx,
                    fields,
                    alias=True,
                    path=path,
                    line=line,
                    note="Native read32/write32. C bit layout; reset not specified.",
                )
                self.table(path, line, "generated", names)
            for i, addr in enumerate(addrs):
                self.instance(name + str(i), block, addr, path, 22)
        # MMIO calls identify additional raw words that have no named union.
        call_specs = [
            ("hw_isp.c", "IspTop", "ISP_TOP_REG", 0),
            ("hw_dma.c", "IspDma", "DMA_REG", 0),
            ("hw_postpipe.c", "IspPostpipe", "PP_REG", 0),
            ("hw_postpipe.c", "IspScaler", "SCL_REG", 0),
        ]
        for filename, name, macro, bias in call_specs:
            p = HW_DIR + filename
            c = self.source(p, "linux")
            block = self.block(name)
            existing = {x["byte_offset"] for x in self.ir["block/" + block]["items"]}
            for m in re.finditer(r"\b" + macro + r"\(\s*(\d+)\s*\)", uncomment(c)):
                idx = int(m[1])
                offset = 4 * idx + bias
                if offset in existing:
                    continue
                existing.add(offset)
                # Locate the original macro call (comments were removed for matching).
                line = next(
                    i
                    for i, s in enumerate(c.splitlines(), 1)
                    if re.search(r"\b" + macro + r"\(\s*" + str(idx) + r"\s*\)", s)
                )
                names = self.reg(
                    block,
                    name + "_word_" + str(idx),
                    offset,
                    alias=True,
                    path=p,
                    line=line,
                    note="Concrete read32/write32 C word; no published named fields/reset.",
                )
                self.table(p, line, "generated", names)
        self.capture_indexed()
        self.source(HW_DIR + "hw_isp.h", "linux")
        self.source(HW_DIR + "hw_dma.h", "linux")
        self.source(HW_DIR + "hw_postpipe.h", "linux")
        self.source(CAM_DIR + "vi/k1xvi/fe_isp.c", "linux")
        path = HW_DIR + "hw_reg_iommu.h"
        block = self.c_numeric(
            path,
            "IspMmu",
            lambda n, e: n.startswith("REG_IOMMU_"),
            bounds={
                n: range(24)
                for n in [
                    "REG_IOMMU_TTBL",
                    "REG_IOMMU_TTBH",
                    "REG_IOMMU_TCR0",
                    "REG_IOMMU_TCR1",
                    "REG_IOMMU_STAT",
                ]
            },
            access_overrides={
                "REG_IOMMU_ERR_CLR": "Write",
                "REG_IOMMU_GIRQ_STAT": "Read",
                "REG_IOMMU_VER": "Read",
            },
        )
        # These offsets are relative to the mapped ISP core 0xC0230000, not ASR ISP aperture.
        self.instance("ISP_MMU", block, 0xC0230000, path, 14)
        self.source(HW_DIR + "hw_iommu.c", "linux")
        self.source(HW_DIR + "hw_iommu.h", "linux")
        self.source("arch/riscv/boot/dts/spacemit/k1-x-camera-sdk.dtsi", "linux")
        self.correction(
            path,
            14,
            "IOMMU offsets use the driver ISP-core base 0xC0230000; not the chapter ASR aperture 0xC0200000. TBU range corroborated in hw_iommu.c.",
        )

    def isp_statistics(self):
        path = CAM_DIR + "cam_isp/k1x_isp_reg.h"
        content = self.source(path, "linux")
        macros = defines(content)
        statpath = CAM_DIR + "cam_isp/k1x_isp_statistic.c"
        self.source(statpath, "linux")
        self.source(CAM_DIR + "cam_isp/k1x_isp_reg.c", "linux")
        self.source(CAM_DIR + "cam_isp/k1x_isp_pipe.c", "linux")
        uapi = self.source("include/uapi/media/k1x/k1x_isp_drv_uapi.h", "linux")
        if evaluate("K1X_ISP_PDC_CHANNEL_NUM", defines(uapi)) != 4 or not re.search(
            r"ISP_HW_PIPELINE_ID_0,\s*ISP_HW_PIPELINE_ID_1,\s*ISP_HW_PIPELINE_ID_MAX",
            uapi,
        ):
            raise ValueError("unreviewed ISP pipe/plane bounds")

        def word(block, offset, line):
            names = [
                block + "." + i["name"]
                for i in self.ir["block/" + block]["items"]
                if i["byte_offset"] == offset
            ]
            if not names:
                names = self.reg(
                    block,
                    mt.identifier(block.split("::")[-1]) + "_word_" + str(offset // 4),
                    offset,
                    path=path,
                    line=line,
                    alias=True,
                    note="Pinned cam_isp native readl/writel word; access/reset unspecified.",
                )
            return names

        top = self.block("IspTop")
        dma = self.block("IspDma")
        for name in [
            "ISP_REG_OFFSET_GLOBALRESET",
            "ISP_REG_IRQ_STATUS",
            "ISP_REG_IRQ_MASK",
            "ISP_RE_IRQ_RAW",
            "ISP_REG_IDI_GAP_OFFSET",
            "ISP_REG_OFFSET_AVG0",
            "ISP_REG_OFFSET_AVG1",
        ]:
            line = macros[name][2]
            before = len(self.ir["block/" + top]["items"])
            names = word(top, evaluate(name, macros), line)
            self.table(
                path,
                line,
                "generated"
                if len(self.ir["block/" + top]["items"]) > before
                else "duplicate",
                names,
                note="Relative to TOP_PIPE; corroborated by separate cam_isp C interface.",
            )
        for name, count in [
            ("REG_ISP_DMA_CHANNEL_MASTER", 14),
            ("REG_ISP_DMA_CHANNEL_WR_PITCH", 14),
            ("REG_ISP_DMA_Y_ADDR", 14),
            ("REG_ISP_DMA_Y_HIGH_ADDR", 14),
            ("REG_ISP_PDC_DMA_BASE_ADDR", 2),
            ("REG_ISP_PDC_DMA_HIGH_BASE_ADDR", 2),
            ("REG_ISP_PDC_DMA_MASTER", 1),
            ("REG_ISP_DMA_IRQ_MASK1", 1),
            ("REG_ISP_DMA_IRQ_MASK2", 1),
        ]:
            line = macros[name][2]
            names = []
            before = len(self.ir["block/" + dma]["items"])
            for index in range(count):
                offset = evaluate(macros[name][1], macros, {"n": index}) - 0x41000
                planes = (
                    range(4)
                    if name
                    in ["REG_ISP_PDC_DMA_BASE_ADDR", "REG_ISP_PDC_DMA_HIGH_BASE_ADDR"]
                    else range(1)
                )
                for plane in planes:
                    names += word(dma, offset + 4 * plane, line)
            self.table(
                path,
                line,
                "generated"
                if len(self.ir["block/" + dma]["items"]) > before
                else "duplicate",
                names,
                note="Native DMA/PDC words; channel bounds 0..13 and two four-plane PDC banks from header/UAPI.",
            )
        fields = []
        enum = re.search(r"enum isp_host_irq_bit\s*\{(.*?)\};", content, re.S)
        if enum is None:
            raise ValueError("missing ISP IRQ enum")
        for bit, name in enumerate(re.findall(r"\bISP_IRQ_BIT_(\w+)", enum[1])):
            if name == "MAX_NUM":
                continue
            fields.append(
                {
                    "name": mt.identifier(name),
                    "bit_offset": bit,
                    "bit_size": 1,
                    "description": "Pinned host ISP IRQ enum bit.",
                }
            )
        if len(fields) != 32:
            raise ValueError("ISP IRQ enum width changed")
        for offset in [0x80, 0x84, 0x88]:
            for i in self.ir["block/" + top]["items"]:
                if i["byte_offset"] == offset:
                    i["fieldset"] = "k1_media::IspHostIrqBits"
        self.ir["fieldset/k1_media::IspHostIrqBits"] = {
            "bit_size": 32,
            "fields": fields,
        }
        block = self.block("IspStatistics")
        for name in [
            "REG_STAT_AEM_RESULT_MEM",
            "REG_STAT_AFC_RESULT_MEM",
            "REG_STAT_WBM_RESULT_MEM",
            "REG_STAT_LTM_RESULT_MEM",
        ]:
            line = macros[name][2]
            offset = evaluate(macros[name][1], macros, {"n": 0}) - 0x30000
            names = self.reg(
                block,
                mt.identifier(name) + "_entry0",
                offset,
                access="Read",
                path=path,
                line=line,
                note="Known first native statistics word; no undocumented memory array bound.",
            )
            self.table(path, line, "generated", names)
        names = self.reg(
            block,
            "aem_result_first_read",
            0x4850,
            access="Read",
            path=statpath,
            line=1152,
            note="C AE result starts 512 native words after AEM memory base.",
        )
        self.table(statpath, 1152, "generated", names)
        for name, offset in [
            ("pdc_channel_count", 0x3F8),
            ("pdc_channel_width", 0x408),
        ]:
            names = self.reg(
                block,
                name,
                offset,
                access="Read",
                array={"len": 4, "stride": 4},
                path=statpath,
                line=893,
                note="Four native plane words per pipe; UAPI K1X_ISP_PDC_CHANNEL_NUM=4.",
            )
            self.table(statpath, 893, "generated", names)
        self.table(
            statpath,
            1188,
            "source_gap",
            note="Statistics result read count derives from caller buffer length, not a declared hardware memory bound. Known entry0 and actual AE starting word generated without fabricated lengths.",
        )
        for pipe in range(2):
            self.instance(
                f"ISP_STATISTICS{pipe}",
                block,
                0xC0230000 + pipe * 0x8000,
                "arch/riscv/boot/dts/spacemit/k1-x-camera-sdk.dtsi",
                133,
            )
        self.correction(
            path,
            10,
            "cam_isp readl/writel masks address and subtracts 0x30000 from ISP core mapping 0xC0230000; preserve native words and named host IRQ bits. Full algorithm tuning tables remain unavailable.",
            CAM_DIR + "cam_isp/k1x_isp_reg.c:31; " + statpath + ":893,1152",
        )

    def capture_indexed(self):
        # Bounds are public C enums/definitions, not guessed from array names.
        self.source(CAM_DIR + "vi/k1xvi/k1xvi.h", "linux")
        self.source(CAM_DIR + "vi/k1xvi/fe_isp.h", "linux")
        counts = {
            "wdma_ch": 14,
            "rdma_ch": 2,
            "idx": 2,
            "idi_wdma_ch": 2,
            "rawdump_idx": 2,
            "rawdump_id": 2,
        }
        specs = [
            ("hw_isp.c", "IspTop", "ISP_TOP_REG"),
            ("hw_dma.c", "IspDma", "DMA_REG"),
            ("hw_postpipe.c", "IspPostpipe", "PP_REG"),
        ]
        for filename, bn, macro in specs:
            path = HW_DIR + filename
            c = self.source(path, "linux")
            block = self.block(bn)
            existing = {i["byte_offset"] for i in self.ir["block/" + block]["items"]}
            for line, s in enumerate(c.splitlines(), 1):
                s = uncomment(s)
                for m in re.finditer(r"\b" + macro + r"\(\s*([\w\s+*]+)\)", s):
                    expr = m[1].strip()
                    vars_ = re.findall(r"\b[A-Za-z_]\w*", expr)
                    if not vars_:
                        continue
                    if len(vars_) != 1 or vars_[0] not in counts:
                        self.table(
                            path,
                            line,
                            "source_gap",
                            note="Indexed expression "
                            + expr
                            + " lacks a bounded public bank declaration.",
                        )
                        continue
                    var = vars_[0]
                    for i in range(counts[var]):
                        off = 4 * mt.integer(expr, {var: i})
                        if off in existing:
                            continue
                        existing.add(off)
                        # The source reuses the same union for all elements.
                        base = 4 * mt.integer(expr, {var: 0})
                        prototype = next(
                            (
                                r
                                for r in self.ir["block/" + block]["items"]
                                if r["byte_offset"] == base and "fieldset" in r
                            ),
                            None,
                        )
                        fs = (
                            self.ir["fieldset/" + prototype["fieldset"]]["fields"]
                            if prototype
                            else []
                        )
                        names = self.reg(
                            block,
                            bn + "_word_" + str(off // 4),
                            off,
                            fs,
                            alias=True,
                            path=path,
                            line=line,
                            note="C indexed MMIO; bounds from AOUT_NUM/AIN_NUM/FORMATTER_NUM/RAWDUMP_NUM. Split address words stay 32-bit.",
                        )
                        self.table(path, line, "generated", names)

    def cpp(self):
        for name, filename in [
            ("Cpp", "regs-cpp-v2p0.h"),
            ("CppFbc", "regs-fbc-v2p0.h"),
        ]:
            path = CAM_DIR + "cam_cpp/" + filename
            block = self.c_numeric(
                path,
                name,
                lambda n, e: n.startswith("REG_"),
                access_overrides={
                    "REG_CPP_IRQ_STATUS": "Alias",
                    "REG_CPP_IRQ_RAW": "Read",
                    "REG_CPP_CHIP_ID": "Read",
                    "REG_FBC_TNRDEC_IRQ_RAW": "Alias",
                    "REG_FBC_TNRENC_IRQ_RAW": "Alias",
                    "REG_FBC_TNRDEC_IRQ_STATUS": "Read",
                    "REG_FBC_TNRENC_IRQ_STATUS": "Read",
                },
            )
            self.instance(
                name,
                block,
                0xC02F0000,
                "arch/riscv/boot/dts/spacemit/k1-x-camera-sdk.dtsi",
                175,
            )
        path = CAM_DIR + "cam_cpp/regs-cpp-iommu.h"
        block = self.c_numeric(
            path,
            "CppMmu",
            lambda n, e: n.startswith("REG_IOMMU_"),
            bounds={
                n: range(32)
                for n in [
                    "REG_IOMMU_TTBL",
                    "REG_IOMMU_TTBH",
                    "REG_IOMMU_TCR0",
                    "REG_IOMMU_TCR1",
                    "REG_IOMMU_STAT",
                ]
            },
            access_overrides={
                "REG_IOMMU_ERR_CLR": "Write",
                "REG_IOMMU_GIRQ_STAT": "Read",
                "REG_IOMMU_VER": "Read",
            },
        )
        self.instance(
            "CPP_MMU",
            block,
            0xC02F0000,
            "arch/riscv/boot/dts/spacemit/k1-x-camera-sdk.dtsi",
            175,
        )
        self.source(CAM_DIR + "cam_cpp/cpp_iommu.h", "linux")
        self.source(CAM_DIR + "cam_cpp/cpp-v2p0.c", "linux")
        self.source(CAM_DIR + "cam_cpp/cpp_iommu.c", "linux")

    def gpu(self):
        path = "drivers/gpu/drm/img-rogue/km/rgx_cr_defs_km.h"
        macros = defines(self.source(path, "linux"))
        access_paths = ["rgxlayer_impl.c", "rgxlayer_impl_common.c", "rgxinit.c", "rgxpower.c", "rgxstartstop.c"]
        access_sources = {"drivers/gpu/drm/img-rogue/" + p: self.source("drivers/gpu/drm/img-rogue/" + p, "linux") for p in access_paths}
        evidence = gpu_access_evidence(access_sources)
        block = self.block("GpuRgx")
        self.ir['block/' + block]['description'] = 'Accessor-established GPU views; conditional source paths do not establish universal BVNC presence, reset or access semantics.'
        spans = {}
        addresses = [n for n in macros if n + "_MASKFULL" in macros or n + "__MASKFULL" in macros]
        for n in addresses:
            line = macros[n][2]
            offset = evaluate(macros[n][1], macros)
            defs = {k: v for k, v in macros.items() if k.startswith(n + "_") and not any(
                other != n and len(other) > len(n) and k.startswith(other + "_") for other in addresses)}
            # Portable header variants have different fields at the same offset.
            # Keep those interpretations separate instead of mixing overlapping bits.
            groups = {"": {}}
            for k, v in defs.items():
                suffix = k[len(n):]
                variant = re.match(r"^__([A-Z0-9_]+?)__", suffix)
                tag = variant[1] if variant else ""
                groups.setdefault(tag, {})[k] = v
            refs = []
            for tag, group in groups.items():
                prefix = n + ("__" + tag + "__" if tag else "_")
                fields, used = [], 0
                for k, (_, expr, fl) in group.items():
                    if not k.endswith("_CLRMSK"):
                        continue
                    # The C literal type decodes complement masks only. It does
                    # NOT prove the width of a volatile/MMIO transaction.
                    mask_bits = 64 if "IMG_UINT64_C" in expr else 32
                    mask = ((1 << mask_bits) - 1) ^ evaluate(expr, macros)
                    if not mask:
                        continue
                    low = (mask & -mask).bit_length() - 1
                    bits = mask.bit_count()
                    if mask != ((1 << bits) - 1) << low or used & mask:
                        self.correction(path, fl, "Field " + k + " is noncontiguous or overlaps another interpretation within this header variant; retain its raw register without inventing a field layout.")
                        continue
                    used |= mask
                    fields.append({"name": mt.identifier(k[len(prefix):-len("_CLRMSK")]), "bit_offset": low, "bit_size": bits})
                full = next((v for k, v in group.items() if k.endswith("MASKFULL")), None)
                highest = max([f["bit_offset"] + f["bit_size"] for f in fields] + [evaluate(full[1], macros).bit_length() if full else 0])
                container_width = 64 if highest > 32 else 32
                regname = n + ("__" + tag if tag else "")
                widths = evidence.get(n, {}) if not tag else {}
                emitted_native = False
                for width, sites in sorted(widths.items()):
                    target = block if len(widths) == 1 else self.block("GpuRgxAccessVariant" + str(width))
                    native_fields = [f for f in fields if f["bit_offset"] + f["bit_size"] <= width]
                    names = self.reg(target, regname, offset, native_fields, width=width, alias=True, path=path, line=line,
                        note=f"Source-observed {width}-bit MMIO access; access/reset and BVNC availability are not universally asserted. No modify. Evidence: " + "; ".join(f"{p}:{ln}" for p, ln in sites))
                    spans.setdefault((target, offset, width // 8), []).extend(names)
                    refs.extend(names)
                    self.correction(sites[0][0], sites[0][1], n + f" uses explicit {width}-bit accessors on the reviewed source path; mask typedefs are not native-width evidence. Conditional/BVNC paths do not establish universal feature presence.",
                        "; ".join(f"{LINUX_URL}{p}#L{ln}" for p, ln in sites))
                    emitted_native = emitted_native or (len(widths) == 1 and container_width <= width)
                if not emitted_native:
                    target = self.block(("GpuRgxWideVariant" if tag or widths else "GpuRgxPortable") + str(container_width))
                    self.ir['block/' + target]['description'] = 'Uninstantiated portable C header/container facts; container widths are NOT verified native MMIO transactions. No BVNC applicability is inferred.'
                    names = self.reg(target, regname, offset, fields, width=container_width, alias=True, path=path, line=line,
                        note=f"Uninstantiated portable header {'variant ' + tag if tag else 'layout'}; {container_width}-bit container represents numeric field/mask positions, NOT verified native transactions. BVNC applicability/access/reset are unspecified.")
                    spans.setdefault((target, offset, container_width // 8), []).extend(names)
                    refs.extend(names)
            self.table(path, line, "generated", refs, note='Portable numeric header definitions: accessor-proven views and uninstantiated containers/variants; no universal native width or BVNC applicability claim.')
        for (bn, _offset, _size), names in spans.items():
            if len(names) > 2:
                raw = [r.rsplit(".", 1)[1] for r in names]
                self.manifest["aliases"][bn] = [g for g in self.manifest["aliases"][bn] if not set(g) & set(raw)]
                self.manifest["aliases"][bn].append(raw)
        dt = "arch/riscv/boot/dts/spacemit/k1-x.dtsi"
        self.source(dt, "linux")
        self.instance("GPU_RGX", block, 0xCAC00000, dt, 2564)
        self.correction(path, 45, "MASKFULL and CLRMSK identify portable numeric layout facts, not native transaction widths. Only explicitly accessor-proven words are in the bound GpuRgx block; all other container/variant layouts remain uninstantiated without BVNC applicability claims.",
            "Pinned accessor audit: rgxlayer_impl.c, rgxinit.c, rgxpower.c, rgxstartstop.c; rgxlayer_impl_common.c:188-281 implements native32/64 accessors.")

    def jpeg(self):
        path = "drivers/soc/spacemit/jpu/regdefine.h"
        content = self.source(path, "linux")
        macros = defines(content)
        block = self.c_numeric(
            path,
            "Jpeg",
            lambda n, e: (
                n.startswith(("MJPEG_", "JPU_")) and not n.endswith(("_BASE", "_STEP"))
            ),
            access_overrides={
                "MJPEG_PIC_STATUS_REG": "Alias",
                "MJPEG_MMU_IRQ_STATUS": "Alias",
                "MJPEG_MMU_ERROR_CLEAR": "Write",
                "MJPEG_MMU_VERSION": "Read",
            },
        )
        for n in ["TTBLR", "TTBHR", "TCR0", "TCR1", "TBU_STATUS"]:
            macro = "MJPEG_MMU_" + n + "_BASE"
            offset = evaluate(macros[macro][1], macros)
            names = self.reg(
                block,
                macro,
                offset,
                alias=True,
                array={"len": 32, "stride": 0x20},
                path=path,
                line=macros[macro][2],
                note="Native 32-bit TBU bank; TBU_NUM=32.",
            )
            self.table(path, macros[macro][2], "generated", names)
        dt = "arch/riscv/boot/dts/spacemit/k1-x.dtsi"
        self.instance("JPEG", block, 0xC02F8000, dt, 2524)
        self.source("drivers/soc/spacemit/jpu/jpu.c", "linux")
        self.correction(
            path,
            109,
            "TBU arrays span 32 entries. Native words retain separate high/low addresses; DT register resource is shorter than the whole array bank but TBU_NUM is explicit.",
        )

    def vpu(self):
        path = VPU_DIR + "mvx_hwreg.c"
        c = self.source(path, "linux")
        block = self.block("Vpu")
        lsid = self.block("VpuLsid")
        for pattern, target in [
            (r"case MVX_HWREG_(\w+):\s*return (0x[\da-f]+);", block),
            (r"case MVX_HWREG_(\w+):\s*offset \+= (0x[\da-f]+);", lsid),
        ]:
            for m in re.finditer(pattern, c):
                line = c[: m.start()].count("\n") + 1
                names = self.reg(
                    target,
                    m[1],
                    int(m[2], 16),
                    alias=True,
                    path=path,
                    line=line,
                    note="Native readl/writel host register. Reset not specified.",
                )
                self.table(path, line, "generated", names)
        self.instance("VPU", block, 0xC0500000, DOC_DIR + "6.Address_Mapping.md", 35)
        self.instance(
            "VPU",
            block,
            0xF0500000,
            DOC_DIR + "6.Address_Mapping.md",
            165,
            view="secure",
        )
        for i in range(4):
            self.instance("VPU_LSID" + str(i), lsid, 0xC0500200 + i * 0x40, path, 83)
        self.source(VPU_DIR + "mvx_lsid.h", "linux")
        self.correction(
            path,
            83,
            "Four logical-session banks at +0x200 with stride 0x40; MVX_LSID_MAX=4 in mvx_lsid.h. No firmware mailbox payload is treated as MMIO.",
        )

    def hdmi(self):
        path = "drivers/gpu/drm/spacemit/spacemit_hdmi.c"
        c = self.source(path, "linux")
        block = self.block("Hdmi")
        offsets = {}
        # Despite helper names ending in b, helpers actually use readl/writel.
        for m in re.finditer(
            r"\b(hdmi_readb|hdmi_writeb|hdmi_write_bits)\(hdmi,\s*(0x[\da-fA-F]+)",
            uncomment(c),
        ):
            offsets.setdefault(int(m[2], 16), set()).add(
                "Read" if m[1] == "hdmi_readb" else "Write"
            )
        for m in re.finditer(
            r"\b(readl(?:_relaxed)?|writel(?:_relaxed)?)\([^;]*?hdmi->regs\s*\+\s*(0x[\da-fA-F]+)",
            uncomment(c),
        ):
            offsets.setdefault(int(m[2], 16), set()).add(
                "Read" if m[1].startswith("read") else "Write"
            )
        offsets.setdefault(0xC, set()).update(["Read", "Write"])
        for off, access in sorted(offsets.items()):
            line = next(
                i
                for i, s in enumerate(c.splitlines(), 1)
                if re.search(r"\b0x0*" + format(off, "x") + r"\b", s, re.I)
            )
            names = self.reg(
                block,
                "word_" + format(off, "03x"),
                off,
                access="Read" if access == {"Read"} else "Write",
                alias=len(access) > 1,
                path=path,
                line=line,
                note="Native 32-bit MMIO; C helper b suffix does not imply byte width. Access inferred only from concrete C operations; reset unspecified.",
            )
            self.table(path, line, "generated", names)
        dtpath = "arch/riscv/boot/dts/spacemit/k1-x-hdmi.dtsi"
        self.source(dtpath, "linux")
        self.instance("HDMI", block, 0xC0400500, dtpath, 56)
        self.table(
            DOC_DIR + "6.Address_Mapping.md",
            166,
            "source_gap",
            note="Secure LCD_HDMI aperture is 0xF0400000; no source establishes a secure alias of the C0400500 native HDMI bank. No inferred +0x500 translation.",
        )
        self.correction(
            path,
            89,
            "hdmi_readb/writeb use native 32-bit relaxed I/O. Preserve mixed HPD status/mask/acknowledgement at +0xC as read/write aliases, not modify.",
        )

    def metadata_finish(self):
        map_path = DOC_DIR + "6.Address_Mapping.md"
        map_lines = (self.docs / map_path).read_text().splitlines()
        for instance in self.manifest["instances"]:
            src = instance["source"]
            if src["path"] == map_path:
                src["line"] = next(
                    i
                    for i, s in enumerate(map_lines, 1)
                    if hex(instance["address"])[2:].lower()
                    in s.replace("_", "").lower()
                )
            elif src["path"] == HW_DIR + "hw_reg.h":
                src.update(
                    path="arch/riscv/boot/dts/spacemit/k1-x-camera-sdk.dtsi", line=155
                )
            elif src["path"] == HW_DIR + "hw_reg_iommu.h":
                src.update(
                    path="arch/riscv/boot/dts/spacemit/k1-x-camera-sdk.dtsi", line=155
                )
            elif src["path"] == V2D_DIR + "v2d_reg.h":
                src["line"] = defines(self.source(src["path"], "linux"))[
                    "V2D_IOMMU_BASE_OFFSET"
                ][2]
        # Several generated C array elements share one original declaration.
        tables = {}
        for t in self.manifest["tables"]:
            key = (t["source"], t["line"])
            if key not in tables:
                tables[key] = t
                continue
            prior = tables[key]
            if t["status"] == "generated":
                prior["status"] = "generated"
            prior["registers"] = list(
                dict.fromkeys(prior["registers"] + t["registers"])
            )
        self.manifest["tables"] = list(tables.values())

    def run(self):
        self.docs_import()
        self.v2d()
        self.dpu()
        self.capture()
        self.isp_statistics()
        self.cpp()
        self.gpu()
        self.jpeg()
        self.vpu()
        self.hdmi()
        self.dsi_compare()
        self.metadata_finish()
        for b in [v for k, v in self.ir.items() if k.startswith("block/")]:
            b["items"].sort(key=lambda x: (x["byte_offset"], x["name"]))
            names = [x["name"] for x in b["items"]]
            if len(names) != len(set(names)):
                raise ValueError("duplicate register identifiers")
        self.manifest["sources"].sort(key=lambda x: (x["kind"], x["path"]))
        self.manifest["instances"].sort(key=lambda x: x["name"])
        self.manifest["write_mask_registers"] = sorted(
            set(self.manifest["write_mask_registers"])
        )
        self.manifest["tables"].sort(
            key=lambda x: (x["source"], x["line"], x["status"])
        )
        return self.ir, self.manifest


def render(value):
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--source-root",
        type=Path,
        default=Path("/tmp/spacemit-research/docs/en/key_stone"),
    )
    p.add_argument("--linux-root", type=Path, default=Path("/tmp/k1-media-c/linux"))
    p.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    p.add_argument("--check", action="store_true")
    a = p.parse_args()
    ir, manifest = Importer(a.source_root, a.linux_root).run()
    dpu_keys = {k for k in ir if k.split("/", 1)[1].startswith("k1_media::Dpu")}
    gpu_keys = {k for k in ir if k.split("/", 1)[1].startswith("k1_media::Gpu")}
    files = [
        (
            "data/registers/expanded_k1_media.yaml",
            {k: v for k, v in ir.items() if k not in dpu_keys | gpu_keys},
        ),
        (
            "data/registers/expanded_k1_media_dpu.yaml",
            {k: v for k, v in ir.items() if k in dpu_keys},
        ),
        (
            "data/registers/expanded_k1_media_gpu.yaml",
            {k: v for k, v in ir.items() if k in gpu_keys},
        ),
        ("provenance/expanded/k1_media.json", manifest),
    ]
    for path, value in files:
        dst = a.repo_root / path
        data = render(value)
        if a.check:
            if not dst.exists() or dst.read_text() != data:
                raise SystemExit("stale " + str(dst))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(data)
    print(
        f"{'checked' if a.check else 'wrote'}: {sum(k.startswith('block/') for k in ir)} blocks, {sum(len(v['items']) for k, v in ir.items() if k.startswith('block/'))} register views, {sum(len(v['fields']) for k, v in ir.items() if k.startswith('fieldset/'))} fields, {len(manifest['instances'])} instances, {len(manifest['tables'])} dispositions"
    )


if __name__ == "__main__":
    main()
