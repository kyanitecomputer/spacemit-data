#!/usr/bin/env python3
"""Extract register facts and source locations, not vendor descriptive prose.

This is the common input layer for the expanded PAC importers. Register grouping,
address views, arrays and side effects are resolved by each source-domain importer.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import html
import json
import operator
import re
from pathlib import Path


def text(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    return html.unescape(re.sub(r"<[^>]*>", "", value)).strip().strip("`*")


def cells(line: str) -> list[str]:
    return [text(cell) for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def column_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def identifier(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", text(value)).strip("_").lower()
    if not value or value[0].isdigit():
        value = "r_" + value
    if value in {"as", "async", "await", "break", "const", "continue", "crate", "dyn", "else", "enum", "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod", "move", "mut", "pub", "ref", "return", "self", "static", "struct", "super", "trait", "true", "type", "unsafe", "use", "where", "while", "yield", "gen"}:
        value += "_"
    return value


def integer(expression: str, variables: dict[str, int] | None = None) -> int:
    """Evaluate only integer arithmetic; no Python calls, attributes or indexing."""
    expression = text(expression).strip()
    expression = re.sub(r"(?<=\d)\s+(?=[0-9A-Fa-f])", "", expression)
    operators = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                 ast.LShift: operator.lshift, ast.RShift: operator.rshift,
                 ast.BitOr: operator.or_, ast.BitAnd: operator.and_}
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.Name) and variables is not None and node.id in variables:
            return variables[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in operators:
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, (ast.LShift, ast.RShift)) and not 0 <= right <= 64:
                raise ValueError("invalid shift")
            result = operators[type(node.op)](left, right)
            if not -(1 << 64) <= result < 1 << 64:
                raise ValueError("integer outside supported range")
            return result
        raise ValueError(f"unsupported integer expression: {expression}")
    return visit(ast.parse(expression, mode="eval").body)


def bit_range(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\[?\s*(\d+)\s*(?:[:\-]\s*(\d+))?\s*\]?", text(value))
    if match is None:
        raise ValueError(f"unsupported bit range: {value}")
    high, low = int(match[1]), int(match[2] or match[1])
    if not 0 <= low <= high < 64:
        raise ValueError(f"invalid bit range: {value}")
    return low, high - low + 1


def extract(path: Path, root: Path) -> list[dict]:
    lines = path.read_text().splitlines()
    headings: list[tuple[int, str]] = []
    offset_labels: list[dict] = []
    tables = []
    index = 0
    while index < len(lines):
        line = lines[index]
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            depth = len(heading[1])
            headings = [(d, t) for d, t in headings if d < depth] + [(depth, text(heading[2]))]
        if re.search(r"\boffset\s*[:=]", line, re.I):
            offset_labels.append({"line": index + 1, "value": text(line)})
        if not line.lstrip().startswith("|"):
            index += 1
            continue
        start = index
        table_lines = []
        while index < len(lines) and lines[index].lstrip().startswith("|"):
            table_lines.append((index + 1, cells(lines[index])))
            index += 1
        if len(table_lines) < 2:
            continue
        header = table_lines[0][1]
        keys = [column_key(value) for value in header]
        bits = any(key in {"bit", "bits", "bitrange", "bitfield"} for key in keys)
        summary = any("offset" in key or "address" in key for key in keys) and any("register" in key or key == "name" for key in keys)
        if not bits and not summary:
            continue
        # Descriptive prose remains in the separately obtained source document.
        keep = [i for i, key in enumerate(keys) if key not in {"description", "descriptions", "remark", "remarks", "function"}]
        rows = []
        for number, row in table_lines[1:]:
            if all(re.fullmatch(r"[:\s-]*", value) for value in row):
                continue
            rows.append({"line": number, "values": [row[i] if i < len(row) else "" for i in keep], "columns": len(row)})
        relative = path.relative_to(root).as_posix()
        tables.append({"id": f"{relative}:{start + 1}", "source": relative,
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "line": start + 1, "kind": "fields" if bits else "registers",
                       "heading": [title for _, title in headings],
                       "offset_labels": [value for value in offset_labels if value["line"] > max(0, start - 20)],
                       "header": [header[i] for i in keep], "rows": rows})
    return tables


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = [table for path in sorted(args.source_root.rglob("*.md")) for table in extract(path, args.source_root)]
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
