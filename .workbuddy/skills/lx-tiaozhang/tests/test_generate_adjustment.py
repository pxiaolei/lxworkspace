"""Exercise the actual CLI with fictional records and isolated local files."""

import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "generate_adjustment.py"
HEADER = ["driver_id", "driver_name", "amount_yuan", "order_list", "activity_id", "desc"]


class AdjustmentTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="lx-tiaozhang-test-"))
        self.input = self.root / "input.json"
        self.output = self.root / "result.csv"

    def record(self, **values):
        record = {"driver_id": "00123456789012345678", "driver_name": "示例司机", "amount_yuan": "25"}
        record.update(values)
        return record

    def write_json(self, records):
        self.input.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

    def run_cli(self, kind="credit", dry_run=False, script=SCRIPT, input_path=None, output=None, env=None):
        command = [sys.executable, "-B", str(script), "--type", kind, "--input", str(input_path or self.input), "--output", str(output or self.output)]
        if dry_run:
            command.append("--dry-run")
        return subprocess.run(command, cwd=self.root, capture_output=True, text=True, encoding="utf-8", env=env)

    def success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def rejected(self, records, field=None):
        self.write_json(records)
        result = self.run_cli()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["status"], "error")
        if field:
            self.assertIn(field, summary["error"])
        self.assertFalse(self.output.exists())
        return summary

    def rows(self):
        return list(csv.reader(io.StringIO(self.output.read_bytes().decode("utf-8-sig"), newline="")))

    def test_credit_preserves_id_and_two_decimal_text_without_instruction_row(self):
        self.write_json([self.record()])
        summary = self.success(self.run_cli())
        self.assertEqual(self.rows(), [HEADER, ["00123456789012345678", "示例司机", "25.00", "", "", "补款"]])
        self.assertEqual(summary["record_count"], 1)
        self.assertEqual(summary["total_amount_yuan"], "25.00")
        self.assertEqual(summary["default_desc_count"], 1)
        self.assertEqual(summary["readback"], "passed")
        raw = self.output.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(raw.count(b"\r\n"), 2)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))

    def test_debit_uses_positive_amount_and_default_desc(self):
        self.write_json([self.record(amount_yuan="0.1")])
        summary = self.success(self.run_cli("debit"))
        self.assertEqual(self.rows()[1][2:], ["0.10", "", "", "扣款"])
        self.assertEqual(summary["type"], "debit")

    def test_amount_boundaries_and_total(self):
        self.write_json([self.record(amount_yuan="0.01"), self.record(amount_yuan="5000.00")])
        summary = self.success(self.run_cli())
        self.assertEqual([row[2] for row in self.rows()[1:]], ["0.01", "5000.00"])
        self.assertEqual(summary["total_amount_yuan"], "5000.01")

    def test_rejects_amount_range_precision_and_non_numbers(self):
        for amount in ("0", "-1", "5000.01", "25.001", "1.230", "NaN", "Infinity", "1e2", "25元", "", True, None):
            with self.subTest(amount=amount):
                self.rejected([self.record(amount_yuan=amount)], "amount_yuan")

    def test_json_numeric_amounts_keep_exact_decimal_values(self):
        self.input.write_text('[{"driver_id":"001","driver_name":"示例司机","amount_yuan":0.29},{"driver_id":"002","driver_name":"示例司机","amount_yuan":25}]', encoding="utf-8")
        summary = self.success(self.run_cli())
        self.assertEqual([row[2] for row in self.rows()[1:]], ["0.29", "25.00"])
        self.assertEqual(summary["total_amount_yuan"], "25.29")

    def test_required_fields_are_never_invented(self):
        for field in ("driver_id", "driver_name", "amount_yuan"):
            for value in (None, "", "   "):
                with self.subTest(field=field, value=value):
                    self.rejected([self.record(**{field: value})], field)
            record = self.record()
            record.pop(field)
            self.rejected([record], field)

    def test_ids_and_names_require_strings(self):
        for field, value in (("driver_id", 123), ("driver_name", 123), ("activity_id", 123), ("order_list", [123])):
            with self.subTest(field=field):
                self.rejected([self.record(**{field: value})], field)

    def test_optional_ids_and_csv_escaping_are_preserved(self):
        self.write_json([self.record(driver_name='示例,"司机"', order_list=["000001", "000002"], activity_id="00000003", desc='订单补款,"已核对"\n备注')])
        self.success(self.run_cli())
        self.assertEqual(self.rows()[1], ["00123456789012345678", '示例,"司机"', "25.00", "000001,000002", "00000003", '订单补款,"已核对"\n备注'])

    def test_blank_optional_fields_and_desc_defaults(self):
        self.write_json([self.record(order_list=None, activity_id=None, desc="   "), self.record(desc=None)])
        summary = self.success(self.run_cli("debit"))
        self.assertEqual([row[3:] for row in self.rows()[1:]], [["", "", "扣款"], ["", "", "扣款"]])
        self.assertEqual(summary["default_desc_count"], 2)

    def test_desc_50_characters_is_preserved_and_51_is_rejected(self):
        self.write_json([self.record(desc="补" * 50)])
        summary = self.success(self.run_cli())
        self.assertEqual(self.rows()[1][-1], "补" * 50)
        self.assertEqual(summary["default_desc_count"], 0)
        self.output = self.root / "too-long.csv"
        self.rejected([self.record(desc="补" * 51)], "desc")

    def test_one_bad_record_prevents_entire_output_without_logging_driver_data(self):
        summary = self.rejected([self.record(), self.record(amount_yuan="5001")], "amount_yuan")
        self.assertIn("2", summary["error"])
        self.assertNotIn("00123456789012345678", json.dumps(summary))
        self.assertNotIn("示例司机", summary["error"])

    def test_unknown_field_and_invalid_json_structure_are_rejected(self):
        self.rejected([self.record(amount="100")], "amount")
        for records in ({"records": [self.record()]}, [], ["invalid"]):
            with self.subTest(records=records):
                self.rejected(records)

    def test_dry_run_creates_no_files(self):
        self.write_json([self.record()])
        before = set(self.root.iterdir())
        summary = self.success(self.run_cli(dry_run=True))
        self.assertEqual(summary["status"], "preview")
        self.assertEqual(summary["total_amount_yuan"], "25.00")
        self.assertEqual(set(self.root.iterdir()), before)

    def test_existing_output_is_never_overwritten(self):
        self.write_json([self.record()])
        self.output.write_bytes(b"existing business file")
        for dry_run in (False, True):
            result = self.run_cli(dry_run=dry_run)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["status"], "error")
            self.assertEqual(self.output.read_bytes(), b"existing business file")

    def test_missing_output_parent_is_not_created(self):
        self.write_json([self.record()])
        target = self.root / "missing" / "result.csv"
        for dry_run in (True, False):
            result = self.run_cli(dry_run=dry_run, output=target)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["status"], "error")
            self.assertFalse(target.parent.exists())

    def test_symlink_output_is_rejected_without_touching_target(self):
        self.write_json([self.record()])
        target = self.root / "target.csv"
        try:
            self.output.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable in this environment")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["status"], "error")
        self.assertFalse(target.exists())
        self.assertTrue(self.output.is_symlink())

    def test_csv_input_uses_named_columns_without_deleting_first_driver(self):
        source = self.root / "source.csv"
        with source.open("w", encoding="utf-8-sig", newline="") as stream:
            stream.write("driver_name,amount_yuan,driver_id\r\n示例司机,0.1,001\r\n示例司机,25.00,002\r\n")
        original = source.read_bytes()
        summary = self.success(self.run_cli(input_path=source))
        self.assertEqual(summary["record_count"], 2)
        self.assertEqual(self.rows()[1:], [["001", "示例司机", "0.10", "", "", "补款"], ["002", "示例司机", "25.00", "", "", "补款"]])
        self.assertEqual(source.read_bytes(), original)

    def test_csv_input_skips_only_exact_template_instruction(self):
        source = self.root / "filled-template.csv"
        template = (SKILL / "assets" / "debit_template.csv").read_bytes()
        source.write_bytes(template + b"\r\n" + "001,示例司机,25.00,,,\r\n".encode("utf-8"))
        self.success(self.run_cli("debit", input_path=source))
        self.assertEqual(self.rows()[1:], [["001", "示例司机", "25.00", "", "", "扣款"]])

    def test_malformed_csv_headers_and_row_widths_are_rejected(self):
        for text in ("driver_id,driver_name,amount_yuan,amount_yuan\n001,示例司机,25,25\n", "driver_id,driver_name\n001,示例司机\n", "driver_id,driver_name,amount_yuan,unexpected\n001,示例司机,25,x\n", "driver_id,driver_name,amount_yuan\n001,示例司机,25,extra\n"):
            with self.subTest(text=text):
                source = self.root / "malformed.csv"
                source.write_text(text, encoding="utf-8")
                result = self.run_cli(input_path=source)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout)["status"], "error")
                self.assertFalse(self.output.exists())

    def test_entire_skill_can_run_after_copy_to_path_with_spaces(self):
        self.write_json([self.record()])
        copied = self.root / "portable skill"
        shutil.copytree(SKILL, copied, ignore=shutil.ignore_patterns("__pycache__"))
        self.success(self.run_cli(script=copied / "scripts" / "generate_adjustment.py"))
        self.assertEqual(self.rows()[1][2], "25.00")

    def test_input_output_suffixes_and_invalid_json_fail_without_output(self):
        self.write_json([self.record()])
        result = self.run_cli(output=self.root / "result.xlsx")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["status"], "error")
        self.assertFalse((self.root / "result.xlsx").exists())
        unsupported = self.root / "input.xlsx"
        unsupported.write_bytes(self.input.read_bytes())
        result = self.run_cli(input_path=unsupported)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["status"], "error")
        self.assertFalse(self.output.exists())
        self.input.write_text("[invalid", encoding="utf-8")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["status"], "error")
        self.assertFalse(self.output.exists())

    def test_json_summary_survives_ascii_console_and_chinese_output_path(self):
        self.write_json([self.record()])
        target = self.root / "补款结果.csv"
        env = dict(os.environ, PYTHONIOENCODING="ascii")
        summary = self.success(self.run_cli(output=target, env=env))
        self.assertEqual(summary["output"], str(target))
        self.assertTrue(target.exists())

    def test_validation_error_survives_ascii_console(self):
        self.write_json([self.record(amount_yuan="5001")])
        env = dict(os.environ, PYTHONIOENCODING="ascii")
        result = self.run_cli(env=env)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        self.assertIn("amount_yuan", json.loads(result.stdout)["error"])
        self.assertFalse(self.output.exists())
