#!/usr/bin/env python3
"""Import K3 system numeric facts. No vendor prose or generated Rust is copied.

JSON is emitted as the dependency-free subset of chiptool YAML. Locations refer
 to the pinned public Markdown and ESOS trees; resets are facts, not Default.
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

DOC = "k3/k3_docs/k3_usermanual/"
ESOS = "bsp/spacemit/"
DEFAULT_SOURCE = Path("/tmp/spacemit-research/docs/en/key_stone")
DEFAULT_ESOS = Path("/tmp/spacemit-research/esos-full")
# Curated original frequency-change request rows, not name-based RW gating.
# Held reset/enable configuration elsewhere in the chapter remains modifiable.
CLOCK_REQUEST_LINES = {
    996, 997, 998, 1000, 1051, 1052, 1072, 1088, 1133, 1195, 1224,
    1239, 1277, 1334, 1340, 1381, 1401, 1402, 1404, 1443, 1455,
    1456, 1494, 1507, 1519, 1532, 1544, 1557, 2352, 2364, 2377,
}
CLOCK_BASES = {
    "PMUMAIN": 0xD4050000,
    "APB_SPARE": 0xD4090000,
    "APBCLOCK": 0xD4015000,
    "PMUAP": 0xD4282800,
    "CIUDRAGON": 0xD8440000,
    "APB2CLOCK": 0xF0610000,
    "RCPU_SYSCTRL": 0xC0880000,
    "RCPU_UARTCTRL": 0xC0881F00,
    "RCPU_I2SCTRL": 0xC0882000,
    "RCPU_SPICTRL": 0xC0885F00,
    "RCPU_I2CCTRL": 0xC0886F00,
    "RCPU_PMU": 0xC088C000,
    "RCPU_PWMCTRL": 0xC088D000,
}
# Offset-order, not numeric sorting, is the documented channel order.
GPIO_NAMES = [
    "gplr",
    "gpdr",
    "gpsr",
    "gpcr",
    "grer",
    "gfer",
    "gedr",
    "gsdr",
    "gcdr",
    "gsrer",
    "gsfer",
    "gcfer",
    "gapmask",
    "gcpmask",
]
HSM = ESOS + "drivers/rpmi/k3/k3_hsm.h"
SOC = ESOS + "platform/rt24/os0_rcpu/_register_defination.h"
DT = ESOS + "platform/rt24/k3.dtsi"
C_FILES = [
    HSM,
    SOC,
    ESOS + "platform/rt24/os1_rcpu/_register_defination.h",
    DT,
    ESOS + "drivers/rpmi/k3/k3-os0_hsm_interface.c",
    ESOS + "drivers/pm/k3_pm.c",
    ESOS + "drivers/gpio/spacemit-gpio.c",
    ESOS + "drivers/mailbox/k3_mailbox.h",
    ESOS + "drivers/mailbox/k3-mailbox.c",
    ESOS + "drivers/hwtimer/k3-hwtimer.c",
    ESOS + "drivers/hwspinlock/spacemit_hwspinlock.c",
    ESOS + "drivers/pwm/pwm-pxa.c",
    ESOS + "drivers/clk/ccu-spacemit-k3.h",
    ESOS + "drivers/clk/ccu-spacemit-k3.c",
]


def offsets(expression, variables=None):
    expression = expression.replace("×", "*").strip("`* ")
    result = []
    for part in expression.split("/"):
        part = part.strip()
        match = re.fullmatch(
            r"(0x[\da-fA-F]+)~(0x[\da-fA-F]+)\s*\((0x[\da-fA-F]+)\)", part
        )
        if match:
            start, end, stride = (int(v, 16) for v in match.groups())
            if not stride or end < start or (end - start) % stride:
                raise ValueError("malformed offset range")
            result.extend(range(start, end + 1, stride))
        else:
            result.append(mt.integer(part, variables))
    if len(set(result)) != len(result) or any(v < 0 or v % 4 for v in result):
        raise ValueError("duplicate, negative or unaligned offsets")
    return result


def array_layout(values):
    if len(values) == 1:
        return values[0], None
    base = min(values)
    if (
        values == list(range(values[0], values[-1] + 1, values[1] - values[0]))
        and values[1] > values[0]
    ):
        return base, {"len": len(values), "stride": values[1] - values[0]}
    return base, {"offsets": [v - base for v in values]}


def access(value):
    value = value.upper().replace("/", "").replace(" ", "")
    return {
        "R": "RO",
        "W": "WO",
        "WR": "RW",
        "RWC": "W1C",
        "RW1C": "W1C",
        "RW1S": "W1S",
        "W1SS": "W1S",
    }.get(value, value)


def reserved(name):
    return bool(re.fullmatch(r"(rsvd?|reserved)\d*", name, re.I))


class Importer:
    def __init__(self, source_root, esos_root):
        self.root, self.esos = source_root, esos_root
        self.ir = {}
        self.manifest = {
            "schema": 1,
            "chip": "k3",
            "registers": ["expanded_k3_system"],
            "sources": [],
            "instances": [],
            "aliases": {},
            "write_mask_registers": [],
            "tables": [],
            "corrections": [],
        }
        self.lines = {}
        self.table_refs = {}

    def source(self, path, kind="docs"):
        root = self.root if kind == "docs" else self.esos
        content = (root / path).read_bytes()
        self.lines[path] = content.decode().splitlines()
        self.manifest["sources"].append(
            {"path": path, "sha256": hashlib.sha256(content).hexdigest(), "kind": kind}
        )

    def correction(self, path, line, decision, evidence):
        correction = {
            "source": path,
            "line": line,
            "decision": decision,
            "evidence": evidence,
        }
        if correction not in self.manifest["corrections"]:
            self.manifest["corrections"].append(correction)

    def block(self, name):
        name = "k3_system::" + name
        self.ir.setdefault("block/" + name, {"items": []})
        return name

    def instance(self, name, block, address, view, path, line):
        self.manifest["instances"].append(
            {
                "name": view.upper() + "_K3_SYSTEM_" + name,
                "block": block,
                "address": address,
                "view": view,
                "source": {"path": path, "line": line},
            }
        )

    def account(self, table, refs, status="generated", note=None):
        entry = {
            "source": table["source"],
            "line": table["line"],
            "status": status,
            "registers": refs,
        }
        if note:
            entry["note"] = note
        self.manifest["tables"].append(entry)
        self.table_refs[(table["source"], table["line"])] = refs

    def fields(self, table):
        header = [mt.column_key(h) for h in table["header"]]

        def col(prefix):
            return next(i for i, h in enumerate(header) if h.startswith(prefix))

        b, f, a, r = col("bit"), col("field"), col("type"), col("reset")
        result = []
        for row in table["rows"]:
            v = row["values"]
            if reserved(v[f]):
                continue
            try:
                low, width = mt.bit_range(v[b])
            except ValueError:
                if "IOPMP_DEVICEID_WIDTH" not in v[b]:
                    raise
                self.correction(
                    table["source"],
                    row["line"],
                    "Keep numeric register raw where configured field width is not fixed.",
                    "IOPMP_DEVICEID_WIDTH is a parameter; per-instance value is not specified.",
                )
                continue
            if low + width > 32:
                raise ValueError(f"field outside MMIO word: {row}")
            name = mt.identifier(v[f])
            mode = access(v[a])
            if table["source"].endswith("mailbox.md") and table["line"] == 259:
                name = ("not_full_dis_" if low % 2 else "new_msg_dis_") + str(low // 2)
            if table["source"].endswith("/dma.md") and name == "rasintr":
                mode = "W1C"
            if table["source"].endswith("/hdma.md"):
                if table["line"] == 469:
                    mode = "W1C"
                if table["line"] == 500:
                    mode = "W1S"
                if table["line"] in {536, 544, 552, 560}:
                    width = 24
            if (
                table["source"].endswith("17_clock_reset.md")
                and name == "pcie_wakeup_int_clr"
            ):
                mode = "W1C"
            if mode not in {"RO", "WO", "RW", "W1C", "W1S", "RWAC"}:
                raise ValueError(f"unknown access {mode}: {row}")
            if any(n["name"] == name for n in result):
                raise ValueError(f"duplicate field {name}: {row}")
            result.append(
                {
                    "name": name,
                    "bit_offset": low,
                    "bit_size": width,
                    "description": f"Source access {mode}; reset {v[r]}.",
                    "_access": mode,
                }
            )
        for i, one in enumerate(result):
            for two in result[:i]:
                if max(one["bit_offset"], two["bit_offset"]) < min(
                    one["bit_offset"] + one["bit_size"],
                    two["bit_offset"] + two["bit_size"],
                ):
                    raise ValueError(f"overlapping fields: {table['id']}")
        return result

    def register(
        self,
        block,
        name,
        values,
        fields=None,
        mode=None,
        effect=False,
        description=None,
    ):
        """Separate destructive/direct-write views, including mixed RW/command words.

        A mixed word's write view includes RW and command fields. Callers must
        provide the entire desired configuration and zero inactive command bits;
        no modify API is exposed. RO named fields have no write-view setters.
        """
        fields = fields or []
        modes = {f["_access"] for f in fields}
        reads = [f for f in fields if f["_access"] != "WO"]
        writes = [f for f in fields if f["_access"] != "RO"]
        if mode is None:
            mode = "ReadWrite" if writes and reads else "Write" if writes else "Read"
        split = mode == "ReadWrite" and (
            effect
            or bool(modes & {"W1C", "W1S", "RWAC"})
            or "RO" in modes
            or "WO" in modes
        )
        variants = (
            [(name + "_read", "Read", reads), (name + "_write", "Write", writes)]
            if split
            else [(name, mode, fields)]
        )
        base, array = array_layout(values)
        refs = []
        for regname, direction, fs in variants:
            item = {
                "name": regname,
                "byte_offset": base,
                "access": direction,
                "bit_size": 32,
            }
            desc = description or "32-bit register; source indices follow offset order."
            if split:
                desc += " Direct write only: supply complete configuration and source-defined command/clear values; no read-modify-write."
            item["description"] = desc
            if array:
                item["array"] = array
            if fs:
                fsname = block + "_" + regname
                self.ir["fieldset/" + fsname] = {
                    "bit_size": 32,
                    "fields": [
                        {k: v for k, v in f.items() if k != "_access"} for f in fs
                    ],
                }
                item["fieldset"] = fsname
            items = self.ir["block/" + block]["items"]
            if any(x["name"] == regname for x in items):
                raise ValueError(f"duplicate register {block}.{regname}")
            items.append(item)
            refs.append(block + "." + regname)
            if direction == "Write" and (
                effect or modes & {"W1C", "W1S", "WO", "RWAC"}
            ):
                self.manifest["write_mask_registers"].append(refs[-1])
        if split:
            self.manifest["aliases"].setdefault(block, []).append(
                [x[0] for x in variants]
            )
        return refs

    def context(self, table):
        lines = self.lines[table["source"]]
        j = table["line"] - 2
        while j >= 0 and not lines[j].startswith("#"):
            j -= 1
        return lines[j + 1 : table["line"] - 1]

    def offset(self, table):
        for line in reversed(self.context(table)):
            match = re.search(r"Offset\s*:\s*(.*)", mt.text(line), re.I)
            if match:
                return match[1].strip("`* ")
        raise ValueError("no offset: " + table["id"])

    def clock(self, tables):
        for t in tables:
            region = t["heading"][-2]
            block = self.block(region.title().replace("_", ""))
            name = self.context(t)[0].split("(")[0].strip()
            values = offsets(self.offset(t))
            request_lines = [r['line'] for r in t['rows'] if r['line'] in CLOCK_REQUEST_LINES]
            effect = bool(request_lines)
            refs = self.register(block, mt.identifier(name), values, self.fields(t), effect=effect,
                description=('Clock configuration with hardware-cleared frequency-change requests: write1 to selected requests, zero inactive requests; compose configuration explicitly.' if effect else None))
            if effect:
                self.correction(t['source'], t['line'],
                    'Read/direct-write aliases prevent replay of hardware-cleared clock requests while retaining normal configuration fields; no modify.',
                    t['source'] + ':' + ','.join(map(str, request_lines)))
            self.account(t, refs)
        path = DOC + "17_clock_reset.md"
        for region, base in CLOCK_BASES.items():
            block = self.block(region.title().replace("_", ""))
            line = next(
                i + 1
                for i, text in enumerate(self.lines[path])
                if text.startswith("| " + region + " |")
            )
            view = (
                "rcpu"
                if region.startswith("RCPU")
                else "secure"
                if region in {"PMUAP", "CIUDRAGON", "APB2CLOCK"}
                else "ap"
            )
            self.instance(region, block, base, view, path, line)
        self.correction(
            path,
            1553,
            "Keep cluster3 clock at +0x208; it is not contiguous with cluster0-2 clocks.",
            "https://github.com/spacemit-com/linux/blob/cee9395acd8043be0644b25c34bfa86623f2b935/include/soc/spacemit/k3-syscon.h:170 and drivers/clk/spacemit/ccu-k3.c:634 agree.",
        )
        self.correction(
            path,
            1647,
            "PORTBCDE logic table repeats reset-table fields. Retain only stated numeric facts; do not extrapolate PORTA fields.",
            path
            + ":1620,1647; PDF section17.4.2 repeats this table too. Offset-specific extra fields remain unspecified.",
        )
        self.correction(
            path,
            75,
            "Use full PMUAP offsets from this chapter, not the address-map 0x100 size.",
            DOC
            + "06_address_map.md:171; chapter 17 offsets extend through 0x3F8; ESOS k3_hsm.h agrees.",
        )

    def peripherals(self, filename, tables):
        block = self.block(
            {
                "dma": "Pdma",
                "hdma": "Hdma",
                "gpio": "GpioBank",
                "pwm": "Pwm",
                "spinlock": "Spinlock",
                "timer_watchdog": "TimerWatchdog",
            }[filename]
        )
        index = 0
        for t in tables:
            if t["kind"] != "fields":
                continue
            fs = self.fields(t)
            name = mt.identifier(
                re.sub(r"\s+(REGISTER|Register)$", "", t["heading"][-1])
            )
            if filename == "gpio":
                name = GPIO_NAMES[index]
            if filename == "pwm":
                values = offsets(self.offset(t), {"n": 1})
            elif filename == "spinlock":
                values = (
                    [4 * n for n in range(32)]
                    if index == 0
                    else offsets(self.offset(t))
                )
            else:
                values = offsets(self.offset(t))
            effect = filename == "spinlock" and index in {0, 5}
            if filename == "hdma" and name == "irq_sourc":
                name = "irq_source"
            if filename == "spinlock" and index == 5:
                for f in fs:
                    f["_access"] = "W1C"
            description = None
            if filename == 'timer_watchdog' and name in {'timer_count_restart', 'timer_watchdog_status'}:
                effect = True
                description = ('Timer restart: write1 to restart selected counters, write0 has no effect; readback retained.'
                    if name == 'timer_count_restart' else 'Watchdog reset status: write0 clears WTS, write1 has no effect (W0C); readback retained.')
                self.correction(t['source'], t['line'], description + ' Read/direct-write aliases prohibit implicit RMW.',
                    t['source'] + (':121-123; K3 V1.13 PDF section16.3.4.3' if name == 'timer_count_restart' else ':271-284; K3 V1.13 PDF section16.3.4.16'))
            refs = self.register(block, name, values, fs, effect=effect, description=description)
            self.account(t, refs)
            index += 1
        if filename == "gpio":
            refs = self.register(
                block,
                "gcrer",
                [0x28],
                [
                    {
                        "name": "rex",
                        "bit_offset": 0,
                        "bit_size": 32,
                        "_access": "WO",
                        "description": "Bitwise rising-edge disable mask; reset unspecified.",
                    }
                ],
            )
            source = ESOS + "drivers/gpio/spacemit-gpio.c"
            line = next(
                n + 1
                for n, text in enumerate(self.lines[source])
                if ".gcrer" in text and "0x28" in text
            )
            self.account(
                {"source": source, "line": line},
                refs,
                note="Tableless C supplement: omitted K3 rising-edge clear word.",
            )
            self.correction(
                DOC + "16_peripherals/gpio.md",
                263,
                "Add omitted rising-edge clear word at +0x28; banks are sparse.",
                ESOS
                + "drivers/gpio/spacemit-gpio.c:48-113 (k3_regs and k3_*_chip_data).",
            )
            for name, bank_offsets in [
                ("Gpio", [0, 0x40, 0x80, 0x100]),
                ("Rgpio", [0, 0x40]),
            ]:
                parent = self.block(name)
                base, ar = array_layout(bank_offsets)
                self.ir["block/" + parent]["items"].append(
                    {
                        "name": "bank",
                        "byte_offset": base,
                        "block": block,
                        "array": ar,
                        "description": "Bank0 has 32 GPIOs; RGPIO bank1 has only four implemented GPIOs."
                        if name == "Rgpio"
                        else "Four 32-pin banks; offsets follow GPIO0 through GPIO3.",
                    }
                )
        for t in tables:
            if t["kind"] == "registers":
                self.account(
                    t,
                    [
                        block + "." + i["name"]
                        for i in self.ir["block/" + block]["items"]
                    ],
                    "duplicate",
                    "Base/summary table; detailed tables define words.",
                )
        if filename == "hdma":
            path = DOC + "16_peripherals/hdma.md"
            self.correction(
                path,
                469,
                "IRQ_SOURCE is write-one-to-clear despite RO table cells; correct truncated register spelling.",
                path + ":59-61; matching PDF printed pp544-545 section16.2.4.3.",
            )
            self.correction(
                path,
                500,
                "TRANSFER_SUBMIT writes queue a transfer; zero has no effect. Use direct write-one request view.",
                path + ":83-85 and 503.",
            )
            self.correction(
                path,
                536,
                "X/Y length and source/destination stride implemented fields are 24 bits, not generic 32-bit table widths.",
                path + ":371-372; PDF section16.2.4.19 (bit-width limitation).",
            )
        if filename == "timer_watchdog":
            self.correction(
                DOC + "16_peripherals/timer_watchdog.md",
                253,
                "Retain manual watchdog enable/match at +0xB8/+0xBC; C +0x68/+0x6C macros are unused stale definitions.",
                ESOS
                + "drivers/hwtimer/k3-hwtimer.c:19-20; these conflict with timer2 interrupt enable at +0x68. WVR +0xCC agrees.",
            )
        if filename == "spinlock":
            self.correction(
                DOC + "16_peripherals/spinlock.md",
                160,
                "IRQ status clear uses write-one mask, not the example write-zero.",
                DOC
                + "16_peripherals/spinlock.md:137 explicitly specifies write-one-to-clear.",
            )

    def mailbox(self, tables):
        path = DOC + "16_peripherals/mailbox.md"
        refs_all = []
        for channels in [16, 8, 4]:
            block = self.block("Mailbox" + str(channels))
            for t in tables:
                if t["kind"] != "fields":
                    continue
                name = mt.identifier(t["heading"][-1].removeprefix("MAILBOX_"))
                expr = self.offset(t)
                fs = self.fields(t)
                if "_m" in name:
                    values = [mt.integer(expr, {"m": n}) for n in range(channels)]
                elif "_u" in name:
                    values = [mt.integer(expr, {"u": n}) for n in range(2)]
                else:
                    values = offsets(expr)
                # The 4/8-channel configurations implement only their channel bits.
                if name == "sysconfig":
                    fs = [f for f in fs if f["bit_offset"] < channels]
                if name.startswith(("irqstatus", "irqenable")):
                    fs = [f for f in fs if f["bit_offset"] < channels * 2]
                if name.startswith("irqthr") and int(name[6]) >= (channels + 3) // 4:
                    continue
                refs = self.register(
                    block, name, values, fs, effect=name == "message_m"
                )
                if channels == 16:
                    self.account(t, refs)
                refs_all.extend(refs)
        self.account(
            tables[0],
            refs_all,
            "duplicate",
            "Summary agrees with detailed offsets; 4/8/16-channel views are distinct.",
        )
        self.correction(
            path,
            259,
            "IRQENABLE_CLR field suffixes repeat incorrectly; name channel by bit pair, matching IRQENABLE_SET.",
            path + ":218; bit position and clear semantics agree.",
        )
        self.correction(
            path,
            251,
            "Accept missing and full-width table terminators; numeric columns are intact.",
            path + ":251,281,292; cells before descriptions are unambiguous.",
        )
        self.correction(
            path,
            297,
            "Threshold user stride is 0x10. Do not use the RV64 C struct stride 0x20.",
            ESOS
            + "drivers/mailbox/k3_mailbox.h:117-137 uses struct instead of union and unsigned long bitfields; manual explicit formula is authoritative.",
        )
        self.correction(
            path,
            17,
            "Keep generic 16/8-channel layouts unbound: address map does not identify their concrete mailbox numbers. Bind C-proven 4-channel layouts only.",
            ESOS
            + "drivers/mailbox/k3_mailbox.h:14,159-175 and "
            + DT
            + ":555-607 establish mailbox3-7.",
        )

    def security(self, tables):
        sec = self.block("SecCiu")
        for t in tables:
            if t["kind"] == "fields" and t["line"] < 350:
                name = next(line.strip() for line in self.context(t) if line.strip())
                self.account(
                    t,
                    self.register(
                        sec,
                        mt.identifier(name),
                        offsets(self.offset(t)),
                        self.fields(t),
                    ),
                )
        self.instance("SEC_CIU", sec, 0xF0580000, "secure", DOC + "15_security.md", 171)
        # Per-instance entries and SIDs are explicitly specified in the instance table.
        layouts = {
            (1, 64): "IopmpSid1",
            (16, 64): "IopmpSid16",
            (16, 256): "IopmpSid16Entries256",
        }
        summary_refs = {}
        for (sids, entries), bn in layouts.items():
            block = self.block(bn)
            for t in tables:
                if t["kind"] != "fields" or t["line"] < 350:
                    continue
                heading = t["heading"][-1]
                name = mt.identifier(heading.split("(")[0])
                match = re.search(r"\((0x.*)\)$", heading)
                if match is None:
                    raise ValueError("missing security offset: " + heading)
                expr = match[1].replace("(s)", "s").replace("(i)", "i")
                values = [
                    mt.integer(expr, {"s": n, "i": n})
                    for n in range(
                        sids
                        if name in {"srcmd_en", "srcmd_enh", "deviceid"}
                        else entries
                        if name.startswith("entry_")
                        else 1
                    )
                ]
                fs = self.fields(t)
                # Configurable DEVICEID width has no fixed per-instance evidence.
                mode = "ReadWrite" if name == "deviceid" else None
                refs = self.register(block, name, values, fs, mode=mode)
                summary_refs.setdefault(name, []).extend(refs)
                if (sids, entries) == (1, 64):
                    self.account(t, refs)
        for t in tables:
            if t["kind"] == "registers":
                if t["line"] == 363:
                    self.account(
                        t,
                        [ref for refs in summary_refs.values() for ref in refs],
                        "duplicate",
                        "Instance bases are emitted as secure metadata; register layouts are defined by detailed tables.",
                    )
                else:
                    refs = []
                    for row in t["rows"]:
                        name = mt.identifier(row["values"][0].split("(")[0])
                        refs += summary_refs.get(name, [])
                    if not refs:
                        raise ValueError("unmatched security summary " + t["id"])
                    self.account(
                        t,
                        refs,
                        "duplicate",
                        "Detailed register definitions and configured array sizes emitted.",
                    )
        bases = [
            0xF0800000,
            0xF0850000,
            0xF0870000,
            0xF0860000,
            0xF0880000,
            0xF0810000,
            0xF0820000,
            0xF0830000,
            0xF0840000,
        ]
        for n, base in enumerate(bases, 1):
            layout = (16, 256) if n == 4 else (1, 64) if n in {1, 3, 5} else (16, 64)
            line = next(
                i + 1
                for i, text in enumerate(self.lines[DOC + "15_security.md"])
                if text.startswith("| IOPMP" + str(n) + " ") and "0xF0" in text
            )
            self.instance(
                "IOPMP" + str(n),
                self.block(layouts[layout]),
                base,
                "secure",
                DOC + "15_security.md",
                line,
            )

    def macros(self, path):
        result = {}
        for n, line in enumerate(self.lines[path], 1):
            m = re.match(r"#define\s+(\w+)\s+(.+)", line)
            if not m:
                continue
            try:
                value = mt.integer(
                    m[2].split("/*")[0], {k: v[0] for k, v in result.items()}
                )
            except (ValueError, SyntaxError):
                continue
            result[m[1]] = (value, n)
        return result

    def c_fields(self, tag, modes=None):
        content = "\n".join(self.lines[SOC])
        match = re.search(
            r"typedef volatile union " + re.escape(tag) + r"\s*\{.*?struct\s*\{(.*?)\}",
            content,
            re.S,
        )
        if match is None:
            raise ValueError("missing C union " + tag)
        fields = []
        pos = 0
        for name, width in re.findall(r"rt_uint32_t\s+(\w+)\s*:\s*(\d+)\s*;", match[1]):
            width = int(width)
            if not reserved(name):
                fields.append(
                    {
                        "name": name,
                        "bit_offset": pos,
                        "bit_size": width,
                        "_access": (modes or {}).get(name, "RW"),
                        "description": "C bitfield; hardware reset unspecified.",
                    }
                )
            pos += width
        if pos != 32:
            raise ValueError("C bitfield width " + tag)
        return fields

    def pm_cpu_c(self):
        hsm, soc = self.macros(HSM), self.macros(SOC)
        path = DOC + "16_peripherals/power_management.md"
        tables = mt.extract(self.root / path, self.root)
        pmua, main = self.block("Pmuap"), self.block("Pmumain")
        specifications = [
            (
                tables[0],
                pmua,
                "core_idle_cfg",
                [hsm[f"PMU_CAP_CORE{n}_IDLE_CFG"][0] - 0xD4282800 for n in range(16)],
                False,
            ),
            (
                tables[1],
                pmua,
                "cluster_idle_cfg",
                [hsm[f"PMU_CX_CAPMP_IDLE_CFG{n}"][0] - 0xD4282800 for n in range(16)],
                False,
            ),
            (
                tables[2],
                pmua,
                "core_wakeup",
                [hsm[f"PMU_CAP_CORE{n}_WAKEUP"][0] - 0xD4282800 for n in range(16)],
                True,
            ),
            (
                tables[3],
                main,
                "apcr_core",
                [hsm[f"APCR_CORE{n}_VETE_REG"][0] - 0xD4050000 for n in range(16)],
                False,
            ),
            (
                tables[4],
                main,
                "apcr_per",
                [hsm["APCR_PER_VETE_REG"][0] - 0xD4050000],
                False,
            ),
        ]
        for t, block, name, values, effect in specifications:
            fs = self.fields(t)
            if effect:
                for f in fs:
                    f["_access"] = "W1S"
            self.account(t, self.register(block, name, values, fs, effect=effect))
            self.correction(
                path,
                t["line"],
                "Resolve missing offsets from sparse ESOS PMU macros; array index is hart number.",
                HSM
                + ":"
                + str(hsm["PMU_CAP_CORE0_IDLE_CFG"][1])
                + "; "
                + ESOS
                + "drivers/rpmi/k3/k3-os0_hsm_interface.c uses readl/writel.",
            )
        self.correction(
            path,
            69,
            "Wakeup is a write-one request, including cores6/15 despite RO table cells; expose separate read/write views.",
            path
            + ":69-84 prose; ESOS k3-os0_hsm_interface.c writes 1<<hart to per-hart wakeup register.",
        )
        # Additional numeric CPU/PM words established by C, not fabricated manual fields.
        groups = {
            "CpuConfig": (0xD4282C00, "secure"),
            "RcpuSysctrl": (0xC0880000, "rcpu"),
            "RcpuPmu": (0xC088C000, "rcpu"),
            "Ciudragon": (0xD8440000, "secure"),
            "Pmuap": (0xD4282800, "secure"),
            "Pmumain": (0xD4050000, "ap"),
        }
        selected_hsm = [
            "C" + str(n) + "_RVBADDR_" + part + "_ADDR"
            for n in range(4)
            for part in ["LO", "HI"]
        ]
        selected_hsm += [
            "PMU_CORE_STATUS0",
            "PMU_CORE_STATUS1",
            "PMU_CC2_AP",
            "PMU_CC3_AP",
        ]
        selected_hsm += [f"AP_C{n}_M2_INT_EN_REG" for n in range(4)] + [
            f"PMU_C{n}_L2_FLUSH_CTRL" for n in range(4)
        ]
        selected_soc = [
            "AUDIO_PMU_VOTE_REG",
            "AUDIO_VOTE_FOR_MAIN_PMU",
            "AUDIO_WAKEUP_EN_REG",
            "SOC_TOP_D2_LP_CTRL",
            "RT24_CORE0_IDLE_CFG_REG",
            "RT24_CORE1_IDLE_CFG_REG",
            "RT24_PMU_STATUS",
            "PWRCTL_LP_WAKEUP_MASK",
            "RCPU_CORE0_BOOT_ENTRY_LO",
            "RCPU_CORE0_BOOT_ENTRY_HI",
            "RCPU_CORE1_BOOT_ENTRY_LO",
            "RCPU_CORE1_BOOT_ENTRY_HI",
            "RT24_CORE0_SW_WAKEUP_REG",
            "RT24_CORE1_SW_WAKEUP_REG",
            "RCPU_CORE1_HART_ID_SET",
            "AWUCRM_REG",
        ]
        for source, macros, names in [
            (HSM, hsm, selected_hsm),
            (SOC, soc, selected_soc),
        ]:
            for name in names:
                address, line = macros[name]
                matches = [
                    (bn, base, view)
                    for bn, (base, view) in groups.items()
                    if base <= address < base + (0x2000 if bn == "Pmumain" else 0x1000)
                ]
                bn, base, view = max(matches, key=lambda x: x[1])
                block = self.block(bn)
                off = address - base
                existing = [
                    i
                    for i in self.ir["block/" + block]["items"]
                    if i["byte_offset"] == off and not i.get("array")
                ]
                if existing:
                    self.correction(
                        source,
                        line,
                        "C name aliases existing manual word; do not duplicate the offset.",
                        ",".join(block + "." + i["name"] for i in existing),
                    )
                    continue
                # C-only words with no side-effect specification keep raw views;
                # command-like PM control words use direct writes, never modify.
                ro = name.startswith("PMU_CORE_STATUS") or name == "RT24_PMU_STATUS"
                command = not ro
                tags = {
                    "AUDIO_PMU_VOTE_REG": "audio_pmu_vote_reg",
                    "AUDIO_VOTE_FOR_MAIN_PMU": "audio_vote_for_main_mpu_reg",
                    "AUDIO_WAKEUP_EN_REG": "audio_wakeup_en_reg",
                    "SOC_TOP_D2_LP_CTRL": "soc_top_d2_lp_ctrl_reg",
                    "RT24_CORE0_IDLE_CFG_REG": "rt24_core0_idle_cfg_reg",
                    "RT24_CORE1_IDLE_CFG_REG": "rt24_core0_idle_cfg_reg",
                }
                fs = self.c_fields(tags[name]) if name in tags else []
                if name == "SOC_TOP_D2_LP_CTRL":
                    for f in fs:
                        if f["bit_offset"] in {1, 2}:
                            f["_access"] = "WO"
                        if f["bit_offset"] in {4, 5}:
                            f["_access"] = "RO"
                if name.startswith("AP_C"):
                    fs = self.c_fields("clusterx_m2_lp_ctrl_reg")
                    for f in fs:
                        if f["bit_offset"] in {1, 2}:
                            f["_access"] = "WO"
                        if f["bit_offset"] >= 4:
                            f["_access"] = "RO"
                refs = self.register(
                    block,
                    mt.identifier(name),
                    [off],
                    fs,
                    mode="Read" if ro else "ReadWrite",
                    effect=command,
                    description=f"32-bit C MMIO word; {source}:{line}. Reset and undocumented bits unspecified.",
                )
                self.account(
                    {"source": source, "line": line},
                    refs,
                    note="Tableless C supplement: numeric CPU/PM macro and any associated uint32_t bitfields.",
                )
                self.correction(
                    source,
                    line,
                    "Supplement manual-omitted numeric CPU/PM word, using C readl/writel width.",
                    source + ":" + str(line),
                )
        self.instance(
            "CPU_CONFIG",
            self.block("CpuConfig"),
            0xD4282C00,
            "secure",
            DOC + "06_address_map.md",
            174,
        )

    def clock_c_extras(self):
        path = ESOS + "drivers/clk/ccu-spacemit-k3.h"
        macros = self.macros(path)
        for name, bn in [
            ("MPMU_WDTPCR", "Pmumain"),
            ("RCPU_UART_NM_CLK_14M_CTRL", "RcpuSysctrl"),
            ("RCPU_UART_NM_CLK_58M_CTRL", "RcpuSysctrl"),
        ]:
            off, line = macros[name]
            refs = self.register(
                self.block(bn),
                mt.identifier(name),
                [off],
                mode="ReadWrite",
                effect=True,
                description=f"C-only 32-bit clock word; {path}:{line}; reset unspecified; direct-write views avoid unknown side effects.",
            )
            self.account(
                {"source": path, "line": line},
                refs,
                note="Tableless C supplement: numeric clock-control macro.",
            )
            self.correction(
                path,
                line,
                "Retain numeric clock word omitted from manual tables without inventing fields.",
                path + ":" + str(line),
            )
        path = DOC + "15_security.md"
        self.correction(
            path,
            344,
            "Do not invent LCD1 SID field: prose identifies bits27:24 but the field table labels27:20 reserved.",
            path + ":344 versus317; PDF printed p514 has the same conflict.",
        )

    def extra_tables(self, files):
        # Account for base/configuration/descriptor tables excluded by the common
        # field/summary extractor. Descriptors reside in memory, not MMIO.
        for path in files:
            lines = self.lines[path]
            headings = []
            for i, line in enumerate(lines):
                if line.startswith("#"):
                    headings = [mt.text(line.lstrip("#"))]
                if (
                    not line.lstrip().startswith("|")
                    or i
                    and lines[i - 1].lstrip().startswith("|")
                ):
                    continue
                if (path, i + 1) in self.table_refs:
                    continue
                if not any(
                    "|" + key in line.replace(" ", "")
                    for key in [
                        "Bits",
                        "Register",
                        "BaseAddress",
                        "Name",
                        "Parameter",
                        "DRQ",
                        "Size",
                        "IOPMP",
                        "MemoryMapping",
                        "Module",
                        "InterruptNumber",
                    ]
                ):
                    continue
                status = (
                    "duplicate"
                    if path.endswith("17_clock_reset.md") and "Base Address" in line
                    else "not_mmio"
                )
                if "Base Address" in line and not path.endswith("dma.md"):
                    status = "duplicate"
                note = (
                    "Region base table is emitted as instance metadata."
                    if status == "duplicate"
                    else "Non-register "
                    + (headings[-1] if headings else "fact")
                    + " table; no MMIO words."
                )
                refs = []
                if status == "duplicate":
                    for block, value in self.ir.items():
                        if block.startswith("block/") and (
                            path.endswith("17_clock_reset.md")
                            and block.removeprefix("block/k3_system::")
                            in {
                                region.title().replace("_", "")
                                for region in CLOCK_BASES
                            }
                            or block == "block/k3_system::GpioBank"
                            and path.endswith("gpio.md")
                            or block.startswith("block/k3_system::Iopmp")
                            and path.endswith("15_security.md")
                        ):
                            refs.extend(
                                block.removeprefix("block/") + "." + item["name"]
                                for item in value["items"]
                                if "access" in item
                            )
                self.account({"source": path, "line": i + 1}, refs, status, note)

    def instances(self):
        path = DOC + "06_address_map.md"
        names = {
            "PDMA Controller Config": "Pdma",
            "Secure DMA Controller Config": "Pdma",
            "GPIO": "Gpio",
            "R_GPIO": "Rgpio",
            "Spinlock": "Spinlock",
        }
        for line, text in enumerate(self.lines[path], 1):
            if not text.startswith("|"):
                continue
            row = mt.cells(text)
            if len(row) < 2:
                continue
            name = row[0]
            block = names.get(name)
            if re.fullmatch(r"PWM\d+", name):
                block = "Pwm"
            if re.fullmatch(
                r"Timer\d+.*|AON_TIMER\d+|SEC_Timer 8|PMU Timer&WDT \(NDR\)", name
            ):
                block = "TimerWatchdog"
            if not block:
                continue
            address = mt.integer(row[1])
            view = (
                "secure"
                if address >= 0xF0000000
                else "rcpu"
                if 0xC0700000 <= address < 0xC0890000
                else "ap"
            )
            self.instance(
                mt.identifier(name).upper(),
                self.block(block),
                address,
                view,
                path,
                line,
            )
        gpio_path = DOC + "16_peripherals/gpio.md"
        for line, text in enumerate(self.lines[gpio_path], 1):
            if not text.startswith("|"):
                continue
            row = mt.cells(text)
            if len(row) < 2 or not re.fullmatch(r"(SEC_)?GPIO\d_BASE", row[0]):
                continue
            name = row[0].removesuffix("_BASE")
            self.instance(
                name,
                self.block("GpioBank"),
                mt.integer(row[1]),
                "secure" if name.startswith("SEC_") else "ap",
                gpio_path,
                line,
            )
        # C establishes four-channel mailbox variants and RCPU timer MMIO words.
        for n in range(3, 8):
            address = 0xCAC90000 + n * 0x400 if n < 7 else 0xC0760000
            line = next(
                i + 1
                for i, text in enumerate(self.lines[DT])
                if f"mailbox{n}: mailbox{n}@" in text
            )
            self.instance(
                "MAILBOX" + str(n), self.block("Mailbox4"), address, "rcpu", DT, line
            )
            if n < 7:
                docline = next(
                    i + 1
                    for i, text in enumerate(self.lines[path])
                    if text.startswith("| Mailbox" + str(n) + " |")
                )
                self.instance(
                    "MAILBOX" + str(n),
                    self.block("Mailbox4"),
                    address,
                    "ap",
                    path,
                    docline,
                )

    def run(self):
        chapters = ["08_cpu.md", "15_security.md", "17_clock_reset.md"]
        files = [DOC + c for c in chapters] + [
            p.relative_to(self.root).as_posix()
            for p in sorted((self.root / DOC / "16_peripherals").glob("*.md"))
        ]
        for path in files + [DOC + "06_address_map.md", DOC + "07_interrupts.md"]:
            self.source(path)
        for path in C_FILES:
            self.source(path, "esos")
        for path in files:
            tables = mt.extract(self.root / path, self.root)
            filename = Path(path).stem
            if filename == "17_clock_reset":
                self.clock(tables)
            elif filename == "15_security":
                self.security(tables)
            elif filename in {
                "dma",
                "hdma",
                "gpio",
                "pwm",
                "spinlock",
                "timer_watchdog",
            }:
                self.peripherals(filename, tables)
            elif filename == "mailbox":
                self.mailbox(tables)
        self.pm_cpu_c()
        self.clock_c_extras()
        self.instances()
        self.extra_tables(files + [DOC + "06_address_map.md", DOC + "07_interrupts.md"])
        self.correction(
            DOC + "16_peripherals/index.md", 1,
            "Navigation index; no numeric register definitions.",
            DOC + "16_peripherals/index.md:1",
        )
        # Precise dispositions include tableless numeric material and absent maps.
        gaps = [
            (
                "08_cpu.md",
                25,
                "source_gap",
                "X100/A100 ACLINT/APLIC/IMSIC/debug/trace layouts are not specified here. Preserve old hart-prebanked RT24 CLINT/PLIC definitions, not generic RISC-V extrapolations.",
            ),
            (
                "15_security.md",
                7,
                "source_gap",
                "Crypto/TRNG/eFuse capabilities only; no numeric register map. TE200 secure BCM base is documented but does not define its register offsets.",
            ),
            (
                "16_peripherals/tsensor.md",
                7,
                "source_gap",
                "Seven 12-bit sensors and AP base 0xD4018000 are known; no offsets/masks. Pinned Linux k1_tsensor.c describes five K1 sensors, not evidence for K3.",
            ),
            (
                "16_peripherals/timeout_monitor.md",
                7,
                "source_gap",
                "Capabilities only; no register offsets, widths or base in owned chapter or ESOS platform sources.",
            ),
            (
                "16_peripherals/jtag.md",
                43,
                "source_gap",
                "tap_ctl(0x98) routing values 0xA/0xE are JTAG-chain facts. No MMIO base/width is specified; SEC_JTAG base alone cannot prove this is a +0x98 MMIO word.",
            ),
            (
                "16_peripherals/hdma.md",
                10,
                "source_gap",
                "Eight HDMA instances are stated, but no per-instance bases/stride or mapping to HSDMA/AIDMA/DMA350 address windows is specified. Emit complete HDMA layout unbound rather than guess that mapping.",
            ),
            (
                "16_peripherals/mailbox.md",
                17,
                "source_gap",
                "Mailbox0/1/2 AP base windows are known, but 4/8/16-channel configuration mapping is unspecified; generic layouts remain available without invented instance bindings.",
            ),
        ]
        for path, line, status, note in gaps:
            self.correction(DOC + path, line, note, f"{DOC}{path}:{line}")
        for key, value in self.ir.items():
            if key.startswith("block/"):
                value["items"].sort(key=lambda i: (i["byte_offset"], i["name"]))
        for key in ["sources", "instances", "tables", "corrections"]:
            self.manifest[key].sort(
                key=lambda e: (
                    e.get(
                        "path",
                        e.get("source", "")
                        if isinstance(e.get("source", ""), str)
                        else e["source"]["path"],
                    ),
                    e.get("line", 0),
                    e.get("name", ""),
                    e.get("decision", ""),
                )
            )
        self.manifest["write_mask_registers"].sort()
        return self.ir, self.manifest


def render(data):
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--esos-root", type=Path, default=DEFAULT_ESOS)
    ap.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    ir, manifest = Importer(args.source_root, args.esos_root).run()
    outputs = {
        "data/registers/expanded_k3_system.yaml": ir,
        "provenance/expanded/k3_system.json": manifest,
    }
    for path, data in outputs.items():
        dest = args.repo_root / path
        content = render(data)
        if args.check:
            if not dest.exists() or dest.read_text() != content:
                raise SystemExit("stale " + path)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
    print(
        f"K3 system: {len(manifest['tables'])} dispositions, {sum(k.startswith('block/') for k in ir)} blocks, {sum(len(v['items']) for k, v in ir.items() if k.startswith('block/'))} register/block items, {len(manifest['instances'])} instances"
    )


if __name__ == "__main__":
    main()
