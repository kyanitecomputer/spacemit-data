#!/usr/bin/env python3
"""Independently check expanded manifests, IR references and source table coverage.

Offline by default. --source-root enables original Markdown hashes and exhaustive
per-document table accounting; C roots enable their hashes/location checks too.
No source cache is needed for normal Rust generation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def shape(value, required, optional=()):
    require(isinstance(value, dict) and set(required) <= value.keys()
            and value.keys() <= set(required) | set(optional), "invalid schema keys")


def relative(path):
    return isinstance(path, str) and bool(path) and not any(c in path for c in "\\:") and all(
        part not in {"", ".", ".."} for part in path.split("/"))


def positive(value):
    return type(value) is int and 0 < value <= 0xffffffff


def markdown_headers(path):
    """Full pipe-table headers, not prose anchors or individual table rows."""
    from manual_tables import cells  # pyright: ignore[reportMissingImports]
    lines = path.read_text().splitlines()
    headers = set()
    for index in range(len(lines) - 2):
        if not all(lines[i].lstrip().startswith("|") for i in (index, index + 1, index + 2)):
            continue
        header, separator = cells(lines[index]), cells(lines[index + 1])
        if (len(header) >= 2 and any(header) and len(separator) == len(header)
                and all(re.fullmatch(r":?-+:?", cell.replace(" ", "")) for cell in separator)
                and any(cells(lines[index + 2]))):
            headers.add(index + 1)
    return headers


def verify(repo, roots):
    sys.path.insert(0, str(repo / "tools"))
    from manual_tables import extract  # pyright: ignore[reportMissingImports]
    all_blocks = {}
    for ir_path in sorted((repo / "data/registers").glob("*.yaml")):
        for key, value in json.loads(ir_path.read_text()).items():
            if key.startswith("block/"):
                name = key.removeprefix("block/")
                require(name not in all_blocks, f"duplicate block: {name}")
                all_blocks[name] = value
    def items(name, stack=()):
        require(name in all_blocks and name not in stack, f"missing/recursive block: {name}")
        block = all_blocks[name]
        parent = items(block["extends"], (*stack, name)) if block.get("extends") else []
        return [*parent, *block["items"]]
    all_registers = {name + "." + i["name"] for name in all_blocks for i in items(name) if "block" not in i}
    totals = {"manifests": 0, "sources": 0, "tables": 0, "blocks": 0, "registers": 0, "fields": 0, "instances": 0}
    files, names, ir_names = set(), set(), set()
    hashes, statuses = 0, {}
    for path in sorted((repo / "provenance/expanded").glob("*.json")):
        m = json.loads(path.read_text())
        shape(m, ("schema", "chip", "registers", "sources", "instances", "aliases", "write_mask_registers", "tables", "corrections"))
        require(type(m["schema"]) is int and m["schema"] == 1 and m["chip"] in {"k1", "k3"}, "unsupported schema/chip")
        for key in ("registers", "sources", "instances", "write_mask_registers", "tables", "corrections"):
            require(isinstance(m[key], list), f"{key} must be a list")
        require(m["registers"] and m["sources"] and m["tables"], "empty manifest")
        totals["manifests"] += 1
        blocks, registers, sources, locations = {}, set(), {}, {}
        for file in m["registers"]:
            require(re.fullmatch(r"expanded_" + m["chip"] + r"_[A-Za-z0-9_]+", file) and file not in files, "invalid/duplicate register file")
            files.add(file)
            ir = json.loads((repo / "data/registers" / (file + ".yaml")).read_text())
            for key, definition in ir.items():
                require(key not in ir_names and "/" in key, "duplicate/invalid IR name")
                ir_names.add(key)
                kind, name = key.split("/", 1)
                require(kind in {"block", "fieldset", "enum"} and name.startswith(m["chip"] + "_"), "IR family/runtime mismatch")
                if kind == "block":
                    blocks[name] = definition
                    totals["blocks"] += 1
                    for item in definition["items"]:
                        if "block" not in item:
                            registers.add(name + "." + item["name"])
                            require(item.get("bit_size", 32) in {8, 16, 32, 64}, "invalid native MMIO width")
                            totals["registers"] += 1
                elif kind == "fieldset":
                    totals["fields"] += len(definition["fields"])
        registers = {name + "." + i["name"] for name in blocks for i in items(name) if "block" not in i}
        for s in m["sources"]:
            shape(s, ("path", "sha256"), ("kind",))
            kind = s.get("kind", "docs")
            require(kind in {"docs", "esos", "linux", "uboot"} and relative(s["path"]), "invalid source kind/path")
            require(s["path"] not in sources and re.fullmatch(r"[0-9a-f]{64}", s["sha256"]), "duplicate source/invalid hash")
            sources[s["path"]] = kind
            totals["sources"] += 1
            if roots.get(kind) is not None:
                candidates = roots[kind] if isinstance(roots[kind], list) else [roots[kind]]
                require(bool(candidates), "empty source root selection")
                source = candidates[0] / s["path"]
                raw = None
                for candidate in candidates:
                    root = candidate.resolve()
                    source = (root / s["path"]).resolve()
                    require(source.is_relative_to(root), "source symlink escapes cache")
                    if source.is_file():
                        data = source.read_bytes()
                        if hashlib.sha256(data).hexdigest() == s["sha256"]:
                            raw = data
                            break
                if raw is None and len(candidates) == 1 and not source.is_file():
                    raise FileNotFoundError(source)
                if raw is None:
                    raise ValueError(f"changed source: no pinned {kind} match for {s['path']}")
                locations[s["path"]] = len(raw.splitlines())
                hashes += 1
        def location(source, line, sources=sources, locations=locations):
            require(relative(source) and source in sources and positive(line), "invalid/unlisted source location")
            if source in locations:
                require(line <= locations[source], f"source line beyond EOF: {source}:{line}")
        # A shared source table/C definition may substantiate different lane
        # layouts. Its disposition must remain unique within each manifest.
        seen, covered, table_ids = {}, set(), set()
        for table in m["tables"]:
            shape(table, ("source", "line", "status", "registers"), ("note",))
            location(table["source"], table["line"])
            if sources[table["source"]] != "docs":
                require(isinstance(table.get("note"), str) and table["note"].strip(), "C supplemental accounting needs a concise note")
            key = (table["source"], table["line"])
            require(key not in table_ids, f"duplicate table {key}")
            table_ids.add(key)
            seen.setdefault(table["source"], set()).add(table["line"])
            status = table["status"]
            require(status in {"generated", "duplicate", "not_mmio", "source_gap"}, "invalid table status")
            refs = table["registers"]
            require(isinstance(refs, list) and len(set(refs)) == len(refs), "invalid/duplicate table references")
            require(all(ref in all_registers for ref in refs), f"unresolved table reference: {key}")
            if status in {"generated", "duplicate"}:
                require(refs, "generated/duplicate table has no register references")
                covered.update(refs)
            if status != "generated":
                require(isinstance(table.get("note"), str) and table["note"].strip(), "disposition lacks factual note")
            totals["tables"] += 1
            statuses[status] = statuses.get(status, 0) + 1
        require(registers <= covered, f"unaccounted registers: {sorted(registers - covered)}")
        for source, kind in sources.items():
            if kind != "docs":
                continue
            require(source in seen or any(i["source"]["path"] == source for i in m["instances"])
                    or any(c["source"] == source for c in m["corrections"]), f"document lacks accounted facts: {source}")
            if roots.get("docs") is not None:
                expected = {t["line"] for t in extract(roots["docs"] / source, roots["docs"])}
                recorded = seen.get(source, set())
                genuine = markdown_headers(roots["docs"] / source)
                require(expected <= recorded and recorded <= genuine,
                        f"table coverage mismatch {source}: missing={sorted(expected - recorded)}, "
                        f"unknown={sorted(recorded - genuine)}")
        require(isinstance(m["aliases"], dict), "aliases must be an object")
        for block, groups in m["aliases"].items():
            require(block in blocks and isinstance(groups, list), "unknown alias block")
            for group in groups:
                require(isinstance(group, list) and len(group) >= 2 and len(set(group)) == len(group)
                        and all(block + "." + r in registers for r in group), "invalid alias references")
        require(all(r in registers for r in m["write_mask_registers"]), "unknown mask register")
        for instance in m["instances"]:
            shape(instance, ("name", "block", "address", "view", "source"))
            shape(instance["source"], ("path", "line"))
            view = instance["view"]
            require(view in {"ap", "rcpu", "secure"}, "invalid view")
            name = instance["name"]
            require(re.fullmatch(view.upper() + "_" + m["chip"].upper() + r"_[A-Z0-9_]+", name)
                    and name not in names, "invalid/duplicate instance name")
            names.add(name)
            require(instance["block"] in all_blocks and instance["block"].startswith(m["chip"] + "_"), "unknown/wrong-family instance block")
            require(type(instance["address"]) is int and 0 <= instance["address"] <= 0xffffffffffffffff, "invalid instance address")
            location(instance["source"]["path"], instance["source"]["line"])
            totals["instances"] += 1
        for c in m["corrections"]:
            shape(c, ("source", "line", "decision", "evidence"))
            location(c["source"], c["line"])
            require(all(isinstance(c[k], str) and c[k].strip() for k in ("decision", "evidence")), "empty correction")
    return totals, hashes, statuses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--esos-root", type=Path)
    parser.add_argument("--linux-root", type=Path, action="append",
                        help="Pinned Linux cache; repeat for distinct revisions (each file must match its declared hash)")
    parser.add_argument("--uboot-root", type=Path, action="append",
                        help="Pinned U-Boot cache; repeat for separately retained source files")
    args = parser.parse_args()
    try:
        totals, hashes, statuses = verify(args.repo_root, {"docs": args.source_root, "esos": args.esos_root,
                                                             "linux": args.linux_root, "uboot": args.uboot_root})
    except (ValueError, KeyError, TypeError, OSError) as error:
        raise SystemExit(f"expanded verification failed: {error}") from error
    print(f"Expanded coverage: {totals}; dispositions={statuses}; source hashes checked={hashes}")
    if args.source_root is None:
        print("Offline structural accounting only; pass --source-root for independent exhaustive Markdown coverage.")


if __name__ == "__main__":
    main()
