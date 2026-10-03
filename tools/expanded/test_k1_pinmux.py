"""Exhaustive published MFPR facts and pinned-C K1 pin offset oracle."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import k1_pinmux as pinmux


class K1PinmuxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir, cls.manifest = pinmux.build()
        cls.items = cls.ir["block/" + pinmux.BLOCK]["items"]
        cls.fields = cls.ir["fieldset/" + pinmux.FIELDSET]["fields"]

    def test_every_published_pin_location_and_sparse_hole(self):
        header, pins = pinmux.pin_table(pinmux.DOC_ROOT / pinmux.CHAPTER)
        self.assertEqual(header, 1113)
        self.assertEqual(
            [(i["name"], i["byte_offset"]) for i in self.items],
            [(n, o) for n, o, _ in pins],
        )
        self.assertEqual(len(self.items), 129)
        offsets = {i["byte_offset"] for i in self.items}
        self.assertFalse({0, 0x15C, 0x160, 0x164, 0x180, 0x208} & offsets)
        self.assertIn(0x1E8, offsets)
        self.assertEqual(self.manifest["instances"][0]["address"], 0xD401E000)
        self.assertTrue(
            all(i["bit_size"] == 32 and i["access"] == "ReadWrite" for i in self.items)
        )
        self.assertTrue(all(i["fieldset"] == pinmux.FIELDSET for i in self.items))

    def test_drive_split_edges_and_reset_limits_are_not_reinterpreted(self):
        fields = {f["name"]: f for f in self.fields}
        self.assertEqual(
            (fields["drive_1_0"]["bit_offset"], fields["drive_1_0"]["bit_size"]),
            (11, 2),
        )
        self.assertEqual(
            (fields["drive_2"]["bit_offset"], fields["drive_2"]["bit_size"]), (10, 1)
        )
        self.assertEqual(fields["edge_clear"]["bit_offset"], 6)
        self.assertIn("not an inferred W1C", fields["edge_clear"]["description"])
        self.assertEqual(len(fields), 12)
        self.assertFalse(self.manifest["write_mask_registers"])
        self.assertTrue(
            any("per-pin resets" in c["decision"] for c in self.manifest["corrections"])
        )
        self.assertFalse(any("reset" in i for i in self.items))

    def test_common_fields_match_all_source_bits_without_prose(self):
        table = pinmux.extract(pinmux.DOC_ROOT / pinmux.CHAPTER, pinmux.DOC_ROOT)[0]
        expected = []
        for row in table["rows"]:
            bits, name, access, _ = row["values"]
            if name != "RSVD":
                low, width = pinmux.bit_range(bits)
                expected.append((pinmux.identifier(name), low, width))
                self.assertEqual(access, "RW")
        self.assertEqual(
            expected, [(f["name"], f["bit_offset"], f["bit_size"]) for f in self.fields]
        )
        self.assertLess(len(json.dumps(self.ir)), 45000)

    def test_manifest_real_headers_and_repeated_datasheet_dispositions(self):
        tables = {(t["source"], t["line"]): t for t in self.manifest["tables"]}
        self.assertEqual(len(tables), 14)
        for source in (pinmux.CHAPTER, pinmux.DATASHEET):
            required = {
                t["line"]
                for t in pinmux.extract(pinmux.DOC_ROOT / source, pinmux.DOC_ROOT)
            }
            self.assertTrue(required <= {line for src, line in tables if src == source})
        self.assertEqual(tables[pinmux.DATASHEET, 3678]["status"], "duplicate")
        self.assertEqual(
            tables[pinmux.DATASHEET, 3505]["registers"],
            tables[pinmux.CHAPTER, 1113]["registers"],
        )
        self.assertEqual(tables[pinmux.DATASHEET, 921]["status"], "source_gap")
        self.assertIn("without physical offsets", tables[pinmux.DATASHEET, 921]["note"])
        for source, line in tables:
            lines = (pinmux.DOC_ROOT / source).read_text().splitlines()
            self.assertTrue(lines[line - 1].startswith("|"))
            self.assertTrue(
                all(re.fullmatch(r"[: -]+", c) for c in pinmux.cells(lines[line]))
            )

    def test_native_c_compiler_matches_all_128_k1_gpio_offsets(self):
        source = (pinmux.C_ROOT / pinmux.C_PATH).read_text()
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), pinmux.C_SHA)
        function = re.search(
            r"static unsigned int spacemit_k1_pin_to_offset\(unsigned int pin\)\n\{.*?\n\}",
            source,
            re.S,
        )
        self.assertIsNotNone(function)
        assert function is not None
        pin_desc = source.split(
            "static const struct pinctrl_pin_desc k1_pin_desc[] = {", 1
        )[1].split("};", 1)[0]
        described = re.findall(r'PINCTRL_PIN\((\d+),\s*"([^"]+)"\)', pin_desc)
        self.assertEqual(len(described), 128)
        offsets = {i["name"]: i["byte_offset"] for i in self.items}
        checks = []
        for number, names in described:
            name = pinmux.identifier(names.split("/")[-1].replace("VCX0", "VCXO"))
            checks.append(
                f"assert(spacemit_k1_pin_to_offset({number}) == {offsets[name]});"
            )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "oracle.c").write_text(
                "#include <assert.h>\n"
                + function[0]
                + "\nint main(void) {\n"
                + "\n".join(checks)
                + "\n}\n"
            )
            subprocess.run(
                [
                    "cc",
                    "-std=gnu11",
                    "-Wall",
                    "-Werror",
                    str(path / "oracle.c"),
                    "-o",
                    str(path / "oracle"),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run([str(path / "oracle")], check=True, capture_output=True)
        self.assertIn("#define PAD_DRIVE_K1\t\tGENMASK(12, 10)", source)
        self.assertIn("writel_relaxed(mux | value, reg)", source)

    def test_malformed_address_field_and_pinned_source_fail_closed(self):
        table = pinmux.extract(pinmux.DOC_ROOT / pinmux.CHAPTER, pinmux.DOC_ROOT)[0]
        for value in ("32", "15:13", "bad"):
            malformed = copy.deepcopy(table)
            malformed["rows"][2]["values"][0] = value
            with self.assertRaises(ValueError):
                pinmux.common_fields(malformed)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            source = path / "pin.md"
            text = (pinmux.DOC_ROOT / pinmux.CHAPTER).read_text()
            source.write_text(text.replace("0xD401E004", "0xD401E000", 1))
            with self.assertRaisesRegex(ValueError, "inconsistent MFPR location"):
                pinmux.pin_table(source)
            source.write_text(
                text.replace(
                    "| GPIO_127     | 0xD401E24C", "| GPIO_00      | 0xD401E24C", 1
                )
            )
            with self.assertRaisesRegex(ValueError, "duplicate"):
                pinmux.pin_table(source)
            c = path / pinmux.C_PATH
            c.parent.mkdir(parents=True)
            c.write_text("changed")
            with self.assertRaisesRegex(ValueError, "pinned pinctrl C"):
                pinmux.build(linux_root=path)

    def test_deterministic_outputs_and_check_mode(self):
        self.assertEqual(
            pinmux.build(linux_root=pinmux.C_ROOT), (self.ir, self.manifest)
        )
        for relative, value in [
            ("data/registers/expanded_k1_pinmux.yaml", self.ir),
            ("provenance/expanded/k1_pinmux.json", self.manifest),
        ]:
            self.assertEqual(
                (pinmux.REPO / relative).read_text(), json.dumps(value, indent=2) + "\n"
            )
        subprocess.run(
            ["python3", str(pinmux.REPO / "tools/expanded/k1_pinmux.py"), "--check"],
            check=True,
            capture_output=True,
        )


if __name__ == "__main__":
    unittest.main()
