#!/usr/bin/env python3
"""Import K1 system register facts from pinned public tables; no vendor prose output.

CSR encodings are not MMIO offsets. Side-effect registers use separate read and
write views, including mixed controls: callers must compose an explicit write
without copying read-back W1C/command bits. chiptool defaults are not resets.
"""

from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import manual_tables as mt

DEFAULT_ROOT = Path("/tmp/spacemit-research/docs/en/key_stone")
ESOS_ROOT = Path("/tmp/spacemit-research/esos-full")
PREFIX = "k1/k1_docs/k1_usermanual/"
CHAPTERS = ["8.CPU_System.md", "9.Top_System.md", "14.RCPU_Subsystem.md"]
NS = "k1_system"
LINUX_PIN = "cee9395acd8043be0644b25c34bfa86623f2b935"
LINUX_URL = "https://github.com/spacemit-com/linux/blob/" + LINUX_PIN + "/"
LINUX_SOURCES = {
    "arch/riscv/boot/dts/spacemit/k1.dtsi": "944d8f6fb253d0d7be9ed9ca81be8b99aefeb9a04026ebbf6f5b19d01a017464",
    "drivers/irqchip/irq-sifive-plic.c": "21390bc9d3015cf20025f993d4b5553325272dacae231d849739667efb53e4a2",
    "drivers/clocksource/timer-clint.c": "8bbc4ca3c7137fa2a9b1908bcb0330130e9cd81c6ee9e2cc8a9277395246fc80",
    "include/soc/spacemit/k1-syscon.h": "a6f6a70751f69e4dbb67cb28636668143c323632db1bae2f451962238a61f52f",
    "drivers/pci/controller/dwc/pcie-spacemit-k1.c": "aa9b4a1ef0f0ff2c04954d835e00d0a85bd0fa349b15c47ef5f8cecb4ea5c01a",
    "drivers/net/ethernet/spacemit/k1_emac.h": "7882c91bbf380b3809c3f14fc04652cba5555f1827b5f16452975c8b27d31892",
    "drivers/net/ethernet/spacemit/k1_emac.c": "db395f26410f759e3fcbd5f98e87b347cac6a9db6ce3246e142de4d4e7583191",
}
ESOS_FILES = [
    "components/nmsis/core/include/core_feature_timer.h",
    "components/nmsis/core/include/core_feature_eclic.h",
    "bsp/spacemit/drivers/mailbox/k1x_mailbox.h",
    "bsp/spacemit/drivers/mailbox/k1x-mailbox.c",
    "bsp/spacemit/drivers/dma/dma_k1x.c",
    "bsp/spacemit/drivers/clk/ccu-spacemit-k1x.h",
    "bsp/spacemit/drivers/clk/ccu-spacemit-k1x.c",
    "bsp/spacemit/platform/n308/k1-x/_register_defination.h",
    "bsp/spacemit/platform/n308/k1-x/dts/k1-x.dtsi",
    "libcpu/risc-v/spacemit/n308/spacemit_sdk_soc.h",
    "bsp/spacemit/drivers/pwm/pwm-pxa.c",
    "bsp/spacemit/drivers/pm/k1x_pm.c",
]
# Corrections are keyed by the original table header, never by guessed names.
OFFSET_CORRECTIONS = {
    ("9.Top_System.md", 1007): (
        "0xD4282800+0x3E0",
        "Port C control follows its clock/reset word at +4.",
        LINUX_URL
        + "drivers/pci/controller/dwc/pcie-spacemit-k1.c:44; arch/riscv/boot/dts/spacemit/k1.dtsi:1282",
    ),
    ("9.Top_System.md", 1071): (
        "0xD4282800+0x3F0",
        "EMAC1 delay word follows its control word at +4.",
        LINUX_URL
        + "drivers/net/ethernet/spacemit/k1_emac.h:APMU_EMAC_DLINE_REG; drivers/net/ethernet/spacemit/k1_emac.c:1593; arch/riscv/boot/dts/spacemit/k1.dtsi:1150",
    ),
    ("9.Top_System.md", 1287): (
        "0xD4015000+0x48",
        "OneWire clock word is aligned at 0x48, not 0x49.",
        LINUX_URL + "include/soc/spacemit/k1-syscon.h:53",
    ),
    ("9.Top_System.md", 3070): (
        "0x10",
        "IIR is the fifth 32-bit mailbox word, not ICR at 0xC.",
        "esos:bsp/spacemit/drivers/mailbox/k1x_mailbox.h:19-25; k1x-mailbox.c dummy write before read",
    ),
}
BASE_NAMES = {
    0xD5001000: "GenericCounter",
    0xD8440000: "DragonCiu",
    0xE0030000: "N308Timer",
    0xE0020000: "N308Eclic",
    0xD4050000: "MainPmu",
    0xD4282800: "ApPmu",
    0xD4015000: "Apbc",
    0xD4090000: "Pll",
    0xF0610000: "SecureApbc",
    0xC0880000: "RcpuSysctrl",
    0xC0882000: "AudioClock",
    0xC0888000: "RcpuPwmClock",
    0xC088C000: "AudioPmu",
    0xC0884000: "AhbDma",
    0xD4000000: "Pdma",
    0xF0703800: "Rng",
    0xD4014000: "TimerWatchdog",
    0xD4010000: "Rtc",
    0xD4013400: "Mailbox",
    0xD401A000: "Pwm",
}


def reference(table):
    return {"source": table["source"], "line": table["line"]}


def parse_offsets(expression):
    """Parse documented lists/ranges without treating '/' as division."""
    result = []
    for part in expression.replace("–", "~").split("/"):
        part = part.strip()
        match = re.fullmatch(
            r"(0x[\dA-Fa-f]+)\s*~\s*(0x[\dA-Fa-f]+)\s*(?:\((0x[\dA-Fa-f]+)\))?", part
        )
        if match:
            first, last = int(match[1], 16), int(match[2], 16)
            stride = int(match[3], 16) if match[3] else 4
            if last < first or (last - first) % stride:
                raise ValueError("invalid offset range: " + part)
            result.extend(range(first, last + 1, stride))
        else:
            result.append(mt.integer(part))
    if len(set(result)) != len(result):
        raise ValueError("duplicate array offset")
    return result


def register_name(heading):
    parens = re.search(r"\(([^()]*)\)\s*$", heading)
    if parens:
        heading = parens[1].split(";")[0]
    heading = re.sub(r"\s+REGISTER(?:S)?$", "", heading, flags=re.I)
    return mt.identifier(heading)


def array(offsets):
    if len(offsets) < 2:
        return None
    delta = [o - min(offsets) for o in offsets]
    if all(delta[i] == i * delta[1] for i in range(len(delta))) and delta[1] > 0:
        return {"len": len(delta), "stride": delta[1]}
    return {"offsets": delta}


def build(source_root=DEFAULT_ROOT, esos_root=ESOS_ROOT):
    ir = {}
    manifest = {
        "schema": 1,
        "chip": "k1",
        "registers": ["expanded_k1_system"],
        "sources": [],
        "instances": [],
        "aliases": {},
        "write_mask_registers": [],
        "tables": [],
        "corrections": [],
    }
    lines_by_path = {}
    for name in ["6.Address_Mapping.md", "7.Interrupt_Assignments.md"] + CHAPTERS:
        path = PREFIX + name
        raw = (source_root / path).read_bytes()
        lines_by_path[path] = raw.decode().splitlines()
        manifest["sources"].append(
            {"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "kind": "docs"}
        )
    for path in ESOS_FILES:
        raw = (esos_root / path).read_bytes()
        manifest["sources"].append(
            {"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "kind": "esos"}
        )

    for path, sha in sorted(LINUX_SOURCES.items()):
        manifest["sources"].append({"path": path, "sha256": sha, "kind": "linux"})

    def correction(t, decision, evidence=None):
        manifest["corrections"].append(
            {
                **reference(t),
                "decision": decision,
                "evidence": evidence or f"{t['source']}:{t['line']}",
            }
        )

    def instance(name, block, address, view, path, line):
        manifest["instances"].append(
            {
                "name": f"{view.upper()}_K1_SYSTEM_{name}",
                "block": NS + "::" + block,
                "address": address,
                "view": view,
                "source": {"path": path, "line": line},
            }
        )

    def add(
        block,
        name,
        offsets,
        width,
        fields,
        access,
        t,
        side=False,
        read_fields=None,
        write_fields=None,
        desc="",
    ):
        key = NS + "::" + block
        b = ir.setdefault(
            "block/" + key, {"description": block + " register bank.", "items": []}
        )
        fsname = NS + "::regs::" + block + "_" + name
        if fields:
            ir["fieldset/" + fsname] = {"bit_size": width, "fields": fields}
        regs = []
        # Expand side-effect aliases so the existing policy validator can verify
        # every exact alias pair without array-alias support.
        groups = (
            [(name, offsets)]
            if not side
            else [
                (name + (f"_{i}" if len(offsets) > 1 else ""), [o])
                for i, o in enumerate(offsets)
            ]
        )
        for itemname, offs in groups:
            views = (
                [(itemname, access, fields)]
                if not side
                else [
                    (
                        itemname + "_read",
                        "Read",
                        read_fields if read_fields is not None else fields,
                    ),
                    (
                        itemname + "_write",
                        "Write",
                        write_fields if write_fields is not None else fields,
                    ),
                ]
            )
            pair = []
            for vn, va, vf in views:
                item = {
                    "name": vn,
                    "byte_offset": min(offs),
                    "access": va,
                    "bit_size": width,
                    "description": desc
                    or f"{block} {name}. Source {t['source']}:{t['line']}.",
                }
                if array(offs):
                    item["array"] = array(offs)
                if vf:
                    viewfs = fsname if vf == fields else fsname + "_" + va.lower()
                    ir["fieldset/" + viewfs] = {"bit_size": width, "fields": vf}
                    item["fieldset"] = viewfs
                b["items"].append(item)
                regs.append(key + "." + vn)
                pair.append(vn)
                if va == "Write" and (side or access == "Write"):
                    manifest["write_mask_registers"].append(key + "." + vn)
            if side:
                manifest["aliases"].setdefault(key, []).append(pair)
        if not t['source'].endswith('.md'):
            record = next((r for r in manifest['tables']
                           if r['source'] == t['source'] and r['line'] == t['line']), None)
            if record is None:
                record = {**reference(t), 'status': 'generated', 'registers': [],
                          'note': f"Pinned C definitions beginning at {t['source']}:{t['line']}; tableless supplemental register layout."}
                manifest['tables'].append(record)
            record['registers'].extend(regs)
        return regs

    # Authoritative RCPU tables occur in chapter 14. The corresponding clock
    # facts from chapter 9 map to the same physical words, not a second bank.
    tables = [
        t
        for chapter in CHAPTERS
        for t in mt.extract(source_root / (PREFIX + chapter), source_root)
    ]
    tables.sort(
        key=lambda t: (
            0 if t["source"].endswith("14.RCPU_Subsystem.md") else 1,
            t["source"],
            t["line"],
        )
    )
    occupied = {}
    table_refs = {}
    for t in tables:
        chapter = Path(t["source"]).name
        line = t["line"]
        entry = {**reference(t), "status": "generated", "registers": []}
        manifest["tables"].append(entry)
        lines = lines_by_path[t["source"]]
        heading = t["heading"][-1]
        # Explicit CSR disposition, including all performance-counter selectors.
        if chapter == "8.CPU_System.md" and 700 <= line < 890:
            entry.update(
                status="not_mmio",
                note="N308 CSR instruction encoding, not a byte-addressed MMIO register.",
            )
            continue
        if t["kind"] == "registers":
            entry["note"] = (
                "Numeric instance base summary; references the associated register bank."
            )
            table_refs[(chapter, line)] = entry
            continue
        off = (
            t["offset_labels"][-1]["value"].split("Offset:", 1)[-1].strip()
            if t["offset_labels"]
            else None
        )
        if off is None:
            raise ValueError(f"missing offset {chapter}:{line}")
        if (chapter, line) in OFFSET_CORRECTIONS:
            off, reason, evidence = OFFSET_CORRECTIONS[(chapter, line)]
            correction(t, reason, evidence)
        if off == "Reserved":
            entry.update(
                status="source_gap",
                note="PAD PLIC trigger-control offset is explicitly Reserved; no numeric location established by pinned C.",
            )
            continue
        if chapter == "9.Top_System.md" and line == 3374:
            entry.update(
                status="source_gap",
                note="Power stable timer is assigned wake-clear offset 0x7C; conflicting independent layouts, no corroborated replacement offset in pinned C.",
            )
            continue
        # Base grouping follows source domains, never an absolute pseudo-bank.
        base = None
        if "+" in off and re.match(r"0x[\dA-Fa-f]{8}\+", off):
            bs, off = off.split("+", 1)
            base = int(bs, 16)
        elif chapter == "8.CPU_System.md":
            base = (
                0xD5001000
                if line < 110
                else (
                    0xD8440000
                    if line < 430
                    else (0xE0030000 if line < 970 else 0xE0020000)
                )
            )
        elif chapter == "14.RCPU_Subsystem.md":
            base = (
                0xC088C000
                if line < 190
                else (
                    0xC0880000
                    if line < 290
                    else (
                        0xC0882000
                        if line < 370
                        else (0xC0884000 if line < 495 else 0xD401A000)
                    )
                )
            )
        elif chapter == "9.Top_System.md":
            base = (
                0xD4000000
                if line < 2470
                else (
                    0xF0703800
                    if line < 2630
                    else (
                        0xD4014000
                        if line < 2930
                        else (0xD4010000 if line < 3020 else 0xD4013400)
                    )
                )
            )
        assert base is not None
        block = BASE_NAMES[base]
        offsets = []
        name = register_name(heading)
        if chapter == "9.Top_System.md" and line == 3294:
            name = "apcr_cluster1"
            correction(
                t,
                "Cluster 1 word is named apcr_cluster1; heading repeats cluster0.",
                "Same table heading and distinct offset 0x1094.",
            )
        if block == "Pwm":
            # Instance stride is not part of a single PWM register offset.
            off = re.split(r"\+", off)[0]
        elif block == "AhbDma" and "*n" in off:
            start = mt.integer(off, {"n": 0})
            offsets = [start + i * 0x40 for i in range(16)]
        elif block == "N308Eclic" and "*i" in off:
            start = mt.integer(off, {"i": 0})
            # Programming window, not a silicon-capacity assertion. INFO owns
            # runtime bounds; manual 64, C enum 69 and C macro 51 conflict.
            offsets = [start + i * 4 for i in range(69)]
            correction(
                t,
                "Expose firmware index window 0..68, not discovered silicon interrupt capacity; check clicinfo before indexing.",
                "esos:libcpu/risc-v/spacemit/n308/spacemit_sdk_soc.h:75-153,185; CPU manual 8.3.3 says 64.",
            )
        else:
            offsets = parse_offsets(off.replace(" ", ""))
        if block == "Pwm":
            offsets = parse_offsets(off)
        width = 8 if block == "N308Eclic" and name != "clicinfo" else 32
        fields, readable, writable = [], [], []
        side = False
        reset_facts = []
        for row in t["rows"]:
            values = row["values"]
            if len(values) != 4:
                raise ValueError(f"malformed field row {t['source']}:{row['line']}")
            bits, fn, typ, reset = values
            if re.search(r"reserved|rsvd", fn, re.I):
                continue
            source_cells = mt.cells(lines[row["line"] - 1])
            description = source_cells[-1]
            try:
                start, size = mt.bit_range(bits)
            except ValueError:
                correction(
                    t,
                    f"{name}: dynamic field positions retained as raw register, not a fixed level/priority split.",
                    "CPU manual cliccfg.nlbits and clicinfo.CLICINTCTLBITS; esos:components/nmsis/core/include/core_feature_eclic.h:CLIC_CTRL_Type",
                )
                continue
            if start + size > width:
                raise ValueError(f"out of bounds {chapter}:{row['line']}")
            f = {"name": mt.identifier(fn), "bit_offset": start, "bit_size": size}
            if any(x["name"] == f["name"] for x in fields):
                f["name"] += "_" + str(start)
            if any(
                start < x["bit_offset"] + x["bit_size"]
                and x["bit_offset"] < start + size
                for x in fields
            ):
                correction(
                    t,
                    f"{name}: overlapping field at bits {bits} omitted; numeric register retained raw for those bits.",
                    f"{t['source']}:{row['line']}",
                )
                continue
            # Documented access is retained as a short fact, including reset.
            f["description"] = f"Access {typ}; reset {reset}."
            try:
                rv = mt.integer(reset)
                if rv >= 1 << size:
                    correction(
                        t,
                        f"{name}.{f['name']}: reset {reset} exceeds {size}-bit field; no corrected reset asserted.",
                        f"{t['source']}:{row['line']}",
                    )
                else:
                    reset_facts.append((rv << start, ((1 << size) - 1) << start))
            except (ValueError, SyntaxError):
                pass
            fields.append(f)
            nt = typ.upper().replace("/", "").replace(" ", "")
            rd = nt not in ["W", "WO", "W1S"]
            wr = nt in ["RW", "RWAC", "W", "WO", "W1C", "W1S", "RW1C", "RW1S"]
            if nt not in [
                "RW",
                "RWAC",
                "R",
                "RO",
                "W",
                "WO",
                "W1C",
                "W1S",
                "RW1C",
                "RW1S",
            ]:
                raise ValueError(f"unknown access {chapter}:{row['line']}: {typ}")
            if rd:
                readable.append(f)
            if wr:
                writable.append(f)
            if (
                "W1" in nt
                or nt == "RWAC"
                or nt in ["W", "WO"]
                or re.search(
                    r'writing\s+(?:a\s+)?["`]?1.*clear|write[- ]one|self[- ]clear|self[- ]cleared|write clear|read clear|automatically cleared|1:\s*Clear',
                    description,
                    re.I,
                )
            ):
                side = True
        # Registers explicitly named clear/restart are documented commands even
        # when their table uses RW. Read-back must never enable modify.
        if block == "TimerWatchdog" and name in [
            "timer_interrupt_clear",
            "timer_watchdog_interrupt_clear",
            "timer_watchdog_counter_reset",
            "timer_count_restart",
            "timer_watchdog_status",
        ]:
            side = bool(readable)
        if block == "Mailbox" and name in ["isrw", "icr", "wdr"]:
            readable = []
        if block == "Mailbox" and name == "iir":
            writable = fields
            side = True
            correction(
                t,
                "IIR dummy write latches inbound state; split read and write without modify.",
                "esos:bsp/spacemit/drivers/mailbox/k1x-mailbox.c; manual IIR/RDR notes",
            )
        if block == "N308Timer" and name == "msftrst":
            # Full key is needed: exposing only the request bit is insufficient.
            fields = [
                {
                    "name": "key",
                    "bit_offset": 0,
                    "bit_size": 32,
                    "description": "Write 0x80000A5F to request reset.",
                }
            ]
            readable = fields
            writable = fields
            side = True
            correction(
                t,
                "Reset request writes the full 32-bit key, not just bit 31.",
                "esos:components/nmsis/core/include/core_feature_timer.h:81,SysTimer_SoftwareReset; CPU manual software-reset generation",
            )
        access = (
            "ReadWrite"
            if (readable and writable)
            or (block == "N308Eclic" and name == "clicintctl_i")
            else ("Write" if writable else "Read")
        )
        if side and not (writable and readable):
            side = False
        desc = f"{block} {name}. Source {t['source']}:{line}."
        if block == "TimerWatchdog" and name.startswith("timer_watchdog"):
            desc += " Protected writes require first key 0xBABA then second key 0xEB10 for each write."
        if block == "Rng" and name == "rng_interrupt_status":
            desc += " Reads clear selected flags when the corresponding reset-select bit is enabled."
        if reset_facts:
            val = 0
            mask = 0
            for rv, rm in reset_facts:
                val |= rv
                mask |= rm
            desc += f" Documented field reset value 0x{val:X}, known mask 0x{mask:X}; not a chiptool default."
        if side:
            desc += " Explicit writes only; preserve RW controls and zero unrelated clear/command bits; do not replay read-back."
        if block == "N308Timer" and name.startswith("mtime"):
            desc += " RV32 split counters: read high/low/high; compare writes low=MAX, high, low (ESOS SysTimer)."
        locs = [(block, o) for o in offsets]
        overlaps = [occupied[x] for x in locs if x in occupied]
        if overlaps:
            refs = list(dict.fromkeys(r for group in overlaps for r in group))
            if chapter == "9.Top_System.md" and 1490 <= line <= 1630:
                entry.update(
                    status="duplicate",
                    registers=refs,
                    note="Same numeric word(s) as chapter 14; chapter 14 field table is authoritative.",
                )
                correction(
                    t,
                    "Repeated RCPU clock word uses chapter 14 definition; no additional alias or invented location.",
                    "; ".join(refs),
                )
                continue
            raise ValueError(
                f"unresolved register overlap {chapter}:{line} {block} {name} {offsets}: {refs}"
            )
        regs = add(
            block,
            name,
            offsets,
            width,
            fields,
            access,
            t,
            side,
            readable,
            writable,
            desc,
        )
        entry["registers"] = regs
        for x in locs:
            occupied[x] = regs
    # Base-address summaries account for their entire related layout.
    for (chapter, line), entry in table_refs.items():
        block = "Pdma" if line == 2222 else ("TimerWatchdog" if line == 2707 else "Rtc")
        entry["registers"] = [
            NS + "::" + block + "." + i["name"]
            for i in ir["block/" + NS + "::" + block]["items"]
        ]

    # Actual numeric instances, with separately identified AP/RCPU/secure views.
    def docinst(name, block, addr, view, chapter, needle):
        path = PREFIX + chapter
        lineno = next(
            i + 1
            for i, source_line in enumerate(lines_by_path[path])
            if needle in source_line
        )
        instance(name, block, addr, view, path, lineno)

    for addr, block in BASE_NAMES.items():
        if block in ["Pwm", "AudioPmu"]:
            continue
        chapter = (
            "8.CPU_System.md"
            if block in ["GenericCounter", "DragonCiu", "N308Timer", "N308Eclic"]
            else ("14.RCPU_Subsystem.md" if block in ["AhbDma"] else "9.Top_System.md")
        )
        view = (
            "rcpu"
            if block
            in [
                "N308Timer",
                "N308Eclic",
                "RcpuSysctrl",
                "AudioClock",
                "RcpuPwmClock",
                "AhbDma",
            ]
            else ("secure" if addr >= 0xF0000000 else "ap")
        )
        needle = {
            0xD5001000: "0xD500_1000",
            0xD8440000: "0xD844_0000",
            0xE0030000: "0xE003_0000",
            0xE0020000: "0xE002_0000",
            0xD4010000: "0xD401_0000",
        }.get(addr, f"0x{addr:X}")
        docinst(block.upper(), block, addr, view, chapter, needle)
    docinst(
        "AUDIO_PMU",
        "AudioPmu",
        0xC088C000,
        "rcpu",
        "6.Address_Mapping.md",
        "AON _PMU_REG",
    )
    correction(
        {"source": PREFIX + "14.RCPU_Subsystem.md", "line": 46},
        "Audio PMU uses RCPU 0xC088C000; stated 0xC0A10000 collides with AP USB3 aperture.",
        "esos:bsp/spacemit/platform/n308/k1-x/_register_defination.h:37,53,75; address map 6.3 AON _PMU_REG",
    )
    for name, addr, view, needle in [
        ("DMA2", 0xF0600000, "secure", "DMA2_BASE"),
        ("APB_TIMER2", 0xD4016000, "ap", "APB_TIMERS2_BASE"),
        ("PMU_TIMER", 0xD4080000, "ap", "PMU_TIMERS_BASE"),
        ("SEC_TIMER", 0xF0616000, "secure", "SEC_TIMERS_BASE"),
    ]:
        docinst(
            name,
            "Pdma" if name == "DMA2" else "TimerWatchdog",
            addr,
            view,
            "9.Top_System.md",
            needle,
        )
    docinst(
        "AON_TIMER",
        "TimerWatchdog",
        0xC0889000,
        "rcpu",
        "6.Address_Mapping.md",
        "AON _TIMER",
    )
    docinst(
        "MAILBOX", "Mailbox", 0xC088A000, "rcpu", "6.Address_Mapping.md", "AON_IPC2AP"
    )
    docinst("RTC", "Rtc", 0xF0615000, "secure", "6.Address_Mapping.md", "SEC_RTC")
    correction(
        {"source": PREFIX + "9.Top_System.md", "line": 2955},
        "Secure RTC base follows chip address map 0xF0615000, not contradictory 0xD4010400 summary.",
        "Address map 6.2 SEC_RTC; non-secure RTC aperture size is 0x800, no separate secure identity at +0x400.",
    )
    for i in range(20):
        addr = (0xD401A000 + i * 0x400) if i < 8 else (0xD4020000 + (i - 8) * 0x400)
        docinst(f"PWM{i}", "Pwm", addr, "ap", "6.Address_Mapping.md", f"| PWM{i} |")
    correction(
        {"source": PREFIX + "14.RCPU_Subsystem.md", "line": 495},
        "AP PWM0..7 and PWM8..19 occupy two disjoint address windows; no linear twenty-instance translation.",
        "Chip address map 6.2 PWM0..PWM19.",
    )
    dtpath = "bsp/spacemit/platform/n308/k1-x/dts/k1-x.dtsi"
    dt = (esos_root / dtpath).read_text().splitlines()
    for i in range(10):
        addr = 0xC0888100 + i * 0x100
        lineno = next(
            j + 1
            for j, source_line in enumerate(dt)
            if f"reg = <0x{addr:x}" in source_line
        )
        instance(f"PWM{i}", "Pwm", addr, "rcpu", dtpath, lineno)
    correction(
        {"source": PREFIX + "14.RCPU_Subsystem.md", "line": 505},
        "RCPU PWM instances use ESOS DTS addresses with 0x100 stride; control, duty and period match driver offsets. OUTCNT is documented only for AP layout.",
        "esos:" + dtpath + "; bsp/spacemit/drivers/pwm/pwm-pxa.c:13-18",
    )
    # RCPU PWM has the three C-supported words, not speculative OUTCNT.
    ap_pwm = ir["block/" + NS + "::Pwm"]
    ir["block/" + NS + "::RcpuPwm"] = {
        "description": "RCPU PWM C-supported layout.",
        "items": [dict(i) for i in ap_pwm["items"] if i["name"] != "pwm_outcnt"],
    }
    manifest['tables'].append({
        'source': 'bsp/spacemit/drivers/pwm/pwm-pxa.c', 'line': 13,
        'status': 'generated',
        'registers': [NS + '::RcpuPwm.' + i['name'] for i in ir['block/' + NS + '::RcpuPwm']['items']],
        'note': 'Pinned ESOS PWMCR/PWMDCR/PWMPCR definitions supply the three-word RCPU PWM layout; tableless C supplement.',
    })
    for inst in manifest["instances"]:
        if inst["view"] == "rcpu" and inst["block"] == NS + "::Pwm":
            inst["block"] = NS + "::RcpuPwm"

    # C-only AHB DMA traffic counters. RD_CNT is readl-accessed; WR_CNT
    # has an offset definition but no access site, so grant no writes.
    dma_path = "bsp/spacemit/drivers/dma/dma_k1x.c"
    dma_lines = (esos_root / dma_path).read_text().splitlines()
    for name, off in [("dma_wr_cnt", 0xA4), ("dma_rd_cnt", 0xA8)]:
        ln = next(
            j + 1
            for j, source_line in enumerate(dma_lines)
            if "#define " + name.upper() + "(" in source_line
        )
        t = {"source": dma_path, "line": ln}
        add("AhbDma", name, [off + 0x40 * i for i in range(16)], 32, [], "Read", t)
        correction(
            t,
            "C-only per-channel traffic counter; reset and named bits not specified. "
            + (
                "WR_CNT access is not exercised by pinned C; expose a conservative read view, not a hardware read-only assertion."
                if name == "dma_wr_cnt"
                else "RD_CNT is accessed with readl."
            ),
            "esos:" + dma_path + ":26-27,268",
        )
    # C-established PMU PLL lock status omitted by the manual register section.
    t = {"source": ESOS_FILES[5], "line": 15}
    add(
        "MainPmu",
        "posr",
        [0x10],
        32,
        [
            {"name": f"pll{i}_lock", "bit_offset": 26 + i, "bit_size": 1}
            for i in range(1, 4)
        ],
        "Read",
        t,
    )
    correction(
        t,
        "Expose C-established PLL lock status word; reset unspecified.",
        "esos:" + ESOS_FILES[5] + ":15-18",
    )

    correction(
        {"source": PREFIX + "14.RCPU_Subsystem.md", "line": 240},
        "Retain CAN clock at 0x48 and IR at 0x4C; ESOS clock header swaps the labels, unlike manual and Linux.",
        LINUX_URL
        + "include/soc/spacemit/k1-syscon.h:125-126; esos:bsp/spacemit/drivers/clk/ccu-spacemit-k1x.h:57-63",
    )
    correction(
        {"source": PREFIX + "9.Top_System.md", "line": 3374},
        "Power stable timer location remains unresolved; PDF repeats the 0x7C wake-clear collision.",
        "K1 V6.3 PDF page 298, section 9.9.4.9.2; sha256 484fe0fb426d67c0472cd17e430f9abea00538fa42432431252f8e031c0af88d",
    )
    correction(
        {"source": PREFIX + "8.CPU_System.md", "line": 296},
        "PAD PLIC trigger-control locations remain Reserved in both public representations.",
        "K1 V6.3 PDF pages 116-117, sections 8.2.5.17-21; sha256 484fe0fb426d67c0472cd17e430f9abea00538fa42432431252f8e031c0af88d",
    )
    # A C-only wakeup mask is written by the N308 suspend implementation.
    t = {"source": "bsp/spacemit/platform/n308/k1-x/_register_defination.h", "line": 89}
    add(
        "AudioPmu",
        "lp_wakeup_mask",
        [0x60],
        32,
        [],
        "Write",
        t,
        desc="Wakeup mask. Pinned ESOS suspend writes zero; named bits and reset unspecified.",
    )
    correction(
        t,
        "Expose numeric wakeup mask written during suspend; no inferred field layout.",
        "esos:bsp/spacemit/drivers/pm/k1x_pm.c:__suspend_asm_finish; " + t["source"],
    )

    # CPU-facing IRQ banks: C establishes the layout and DTS establishes the
    # actual AP address, 159 sources, 16 M/S contexts and eight harts. No AP
    # address is asserted to translate into the RCPU local ECLIC window.
    dtpath = "arch/riscv/boot/dts/spacemit/k1.dtsi"
    t = {"source": "drivers/irqchip/irq-sifive-plic.c", "line": 42}
    add("ApPlic", "priority", [4 + 4 * i for i in range(159)], 32, [], "ReadWrite", t)
    add("ApPlic", "pending", [0x1000 + 4 * i for i in range(5)], 32, [], "Read", t)
    add("PlicEnable", "enable", [4 * i for i in range(5)], 32, [], "ReadWrite", t)
    add("PlicContext", "threshold", [0], 32, [], "ReadWrite", t)
    add(
        "PlicContext",
        "claim_complete",
        [4],
        32,
        [],
        "ReadWrite",
        t,
        True,
        desc="Read claims an interrupt; write completes its ID. No modify; read is destructive.",
    )
    ir["block/" + NS + "::ApPlic"]["items"].extend(
        [
            {
                "name": "enable_context",
                "byte_offset": 0x2000,
                "block": NS + "::PlicEnable",
                "array": {"len": 16, "stride": 0x80},
            },
            {
                "name": "context",
                "byte_offset": 0x200000,
                "block": NS + "::PlicContext",
                "array": {"len": 16, "stride": 0x1000},
            },
        ]
    )
    instance("PLIC", "ApPlic", 0xE0000000, "ap", dtpath, 889)
    correction(
        t,
        "Expose AP PLIC with 159 source priorities, five bitmap words and 16 contexts in DTS order hart0 M/S through hart7 M/S. IRQ chapter 7.1 swaps controller roles; AP addresses and roles follow pinned C rather than that prose.",
        LINUX_URL + dtpath + ":889-902; " + LINUX_URL + t["source"] + ":42-62",
    )
    t = {"source": "drivers/clocksource/timer-clint.c", "line": 31}
    add(
        "ApClint",
        "msip",
        [4 * i for i in range(8)],
        32,
        [{"name": "pending", "bit_offset": 0, "bit_size": 1}],
        "ReadWrite",
        t,
    )
    for name, off in [("mtimecmp_lo", 0x4000), ("mtimecmp_hi", 0x4004)]:
        add(
            "ApClint",
            name,
            [off + 8 * i for i in range(8)],
            32,
            [],
            "ReadWrite",
            t,
            desc="AP compare counter RV32 halves; write low=MAX, high, low.",
        )
    # AP CLINT time is read by the Linux driver. No unsupported writes granted.
    add("ApClint", "mtime_lo", [0xBFF8], 32, [], "Read", t)
    add("ApClint", "mtime_hi", [0xBFFC], 32, [], "Read", t)
    instance("CLINT", "ApClint", 0xE4000000, "ap", dtpath, 906)
    correction(
        t,
        "AP CLINT uses its own AP window and eight hart compare slots; independent of N308 local timer.",
        LINUX_URL
        + dtpath
        + ":906-917; "
        + LINUX_URL
        + t["source"]
        + ":32-33; ESOS RV32 split-counter access sequence",
    )

    # Remove unused full fieldsets when both aliases use filtered fields.
    used = {
        i["fieldset"]
        for b in ir.values()
        if "items" in b
        for i in b["items"]
        if "fieldset" in i
    }
    ir = {k: v for k, v in ir.items() if not k.startswith("fieldset/") or k[9:] in used}
    for line in (18, 159):
        manifest['tables'].append({'source': PREFIX + '7.Interrupt_Assignments.md',
            'line': line, 'status': 'not_mmio', 'registers': [],
            'note': 'Interrupt-source assignments, not numeric MMIO register layouts.'})
    manifest["tables"].sort(key=lambda t: (t["source"], t["line"]))
    manifest["instances"].sort(key=lambda i: i["name"])
    manifest["write_mask_registers"] = sorted(manifest["write_mask_registers"])
    manifest["corrections"].sort(key=lambda t: (t["source"], t["line"], t["decision"]))
    return dict(sorted(ir.items())), manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--esos-root", type=Path, default=ESOS_ROOT)
    parser.add_argument(
        "--linux-root",
        type=Path,
        help="Optional pinned Linux source tree for fingerprint verification",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    ir, manifest = build(args.source_root, args.esos_root)
    if args.linux_root:
        for path, sha in LINUX_SOURCES.items():
            if hashlib.sha256((args.linux_root / path).read_bytes()).hexdigest() != sha:
                raise SystemExit("Linux fingerprint mismatch: " + path)
    for path, obj in [
        ("data/registers/expanded_k1_system.yaml", ir),
        ("provenance/expanded/k1_system.json", manifest),
    ]:
        rendered = json.dumps(obj, indent=2) + "\n"
        dest = args.repo_root / path
        if args.check:
            if not dest.exists() or dest.read_text() != rendered:
                raise SystemExit("stale " + path)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(rendered)
    print(
        f"{len(manifest['tables'])} tables; {sum(k.startswith('block/') for k in ir)} blocks; {sum('bit_size' in i for k, v in ir.items() if k.startswith('block/') for i in v['items'])} register definitions; {len(manifest['instances'])} instances"
    )


if __name__ == "__main__":
    main()
