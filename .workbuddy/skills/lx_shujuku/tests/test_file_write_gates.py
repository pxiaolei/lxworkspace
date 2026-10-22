import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import db_tools  # noqa: E402


LOCAL_SCHEMA = {
    "tables": [
        {
            "name": "old_table",
            "comment": "old",
            "columns": [{"field": "id", "type": "int", "comment": "id"}],
        }
    ]
}
REMOTE_SCHEMA = {
    "tables": [
        {
            "name": "new_table",
            "comment": "new",
            "columns": [{"field": "id", "type": "int", "comment": "id"}],
        }
    ]
}


class FakeClient:
    def __init__(self, schema_path=None):
        self.schema = SimpleNamespace(schema_path=schema_path)
        self.query_calls = 0
        self.export_calls = 0

    def execute_audited(self, *args, **kwargs):
        self.query_calls += 1
        return {
            "safe_sql": "SELECT 1 LIMIT 1",
            "rows": [{"value": 1}],
            "is_complete": True,
            "possible_truncation": False,
            "result_sha256": "0" * 64,
        }

    def export_schema(self):
        self.export_calls += 1
        return REMOTE_SCHEMA

    def show_indexes(self, table):
        return [
            {
                "table": table,
                "unique": False,
                "key_name": "idx_brand_date",
                "seq_in_index": 1,
                "column_name": "brand_name",
                "collation": "A",
                "cardinality": 100,
                "sub_part": None,
                "packed": None,
                "nullable": True,
                "index_type": "BTREE",
                "comment": "",
                "index_comment": "",
                "visible": True,
                "expression": None,
            }
        ]


class OutputGateTests(unittest.TestCase):
    def test_indexes_cli_returns_structured_metadata(self):
        client = FakeClient()
        stdout = io.StringIO()
        with patch.object(db_tools, "create_client", return_value=client), redirect_stdout(stdout):
            try:
                result = db_tools.main(["indexes", "honghu_order_data", "--json"])
            except SystemExit as exc:
                result = int(exc.code)

        self.assertEqual(result, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["type"], "lx_shujuku.index_metadata")
        self.assertEqual(payload["database"], "dataReporting")
        self.assertEqual(payload["table"], "honghu_order_data")
        self.assertEqual(payload["indexes"][0]["column_name"], "brand_name")

    def test_indexes_cli_has_no_whitelist_bypass(self):
        stderr = io.StringIO()
        with patch.object(db_tools, "create_client") as create_client, redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                db_tools.main(["indexes", "unknown_table", "--no-whitelist"])

        self.assertEqual(caught.exception.code, 2)
        create_client.assert_not_called()

    def test_query_existing_output_is_rejected_before_client_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "query.json"
            output.write_text("unchanged", encoding="utf-8")
            client = FakeClient()

            with self.assertRaisesRegex(RuntimeError, "--overwrite --confirmed"):
                db_tools.cmd_query(client, "SELECT 1", output=str(output))

            self.assertEqual(client.query_calls, 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "unchanged")

    def test_query_overwrite_requires_both_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "query.json"
            for overwrite, confirmed in ((True, False), (False, True)):
                output.write_text("unchanged", encoding="utf-8")
                client = FakeClient()
                with self.subTest(overwrite=overwrite, confirmed=confirmed), self.assertRaisesRegex(
                    RuntimeError, "--overwrite --confirmed"
                ):
                    db_tools.cmd_query(
                        client,
                        "SELECT 1",
                        output=str(output),
                        overwrite=overwrite,
                        confirmed=confirmed,
                    )
                self.assertEqual(client.query_calls, 0)
                self.assertEqual(output.read_text(encoding="utf-8"), "unchanged")

    def test_query_new_output_is_r1_and_does_not_require_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "query.json"
            client = FakeClient()

            db_tools.cmd_query(client, "SELECT 1", output=str(output))

            self.assertEqual(client.query_calls, 1)
            self.assertTrue(output.exists())

    def test_query_existing_output_can_be_overwritten_with_both_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "query.json"
            output.write_text("old", encoding="utf-8")
            client = FakeClient()

            db_tools.cmd_query(
                client,
                "SELECT 1",
                output=str(output),
                overwrite=True,
                confirmed=True,
            )

            self.assertEqual(client.query_calls, 1)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["rows"], [{"value": 1}])

    def test_query_existing_directory_allows_new_generated_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "runs"
            output_dir.mkdir()
            client = FakeClient()

            db_tools.cmd_query(client, "SELECT 1", output=str(output_dir))

            self.assertEqual(client.query_calls, 1)
            self.assertEqual(len(list(output_dir.glob("*.json"))), 1)

    def test_audit_auto_output_refuses_existing_resolved_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "audit.json"
            output.write_text("unchanged", encoding="utf-8")
            client = FakeClient()
            with patch.object(db_tools, "_default_query_run_path", return_value=output):
                with self.assertRaisesRegex(RuntimeError, "--overwrite --confirmed"):
                    db_tools.cmd_query(client, "SELECT 1", audit=True)

            self.assertEqual(client.query_calls, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "unchanged")

    def test_schema_and_schema_diff_refuse_existing_output_before_export(self):
        for command in (db_tools.cmd_schema, db_tools.cmd_schema_diff):
            with self.subTest(command=command.__name__), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "schema.json"
                output.write_text("unchanged", encoding="utf-8")
                client = FakeClient(schema_path=Path(tmp) / "local.json")
                if command is db_tools.cmd_schema_diff:
                    client.schema.schema_path.write_text(json.dumps(LOCAL_SCHEMA), encoding="utf-8")

                with self.assertRaisesRegex(RuntimeError, "--overwrite --confirmed"):
                    command(client, output=str(output))

                self.assertEqual(client.export_calls, 0)
                self.assertEqual(output.read_text(encoding="utf-8"), "unchanged")

    def test_main_rejects_existing_output_before_creating_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "query.json"
            output.write_text("unchanged", encoding="utf-8")
            with patch.object(
                db_tools,
                "create_client",
                side_effect=AssertionError("existing output must be rejected before client creation"),
            ):
                result = db_tools.main(["query", "SELECT 1", "--output", str(output)])

            self.assertEqual(result, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "unchanged")


class RefreshSchemaGateTests(unittest.TestCase):
    def test_unconfirmed_refresh_keeps_schema_and_catalog_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schema_path = root / "assets" / "schema.json"
            catalog_path = root / "references" / "table_catalog.md"
            schema_path.parent.mkdir(parents=True)
            catalog_path.parent.mkdir(parents=True)
            schema_path.write_text(json.dumps(LOCAL_SCHEMA), encoding="utf-8")
            catalog_path.write_text("unchanged catalog", encoding="utf-8")
            client = FakeClient(schema_path=schema_path)

            with patch.object(db_tools, "_skill_root", return_value=root):
                db_tools.cmd_refresh_schema(client, confirmed=False)

            self.assertEqual(json.loads(schema_path.read_text(encoding="utf-8")), LOCAL_SCHEMA)
            self.assertEqual(catalog_path.read_text(encoding="utf-8"), "unchanged catalog")
            self.assertEqual(list(root.rglob("*.bak.*")), [])

    def test_confirmed_refresh_writes_and_backs_up_both_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schema_path = root / "assets" / "schema.json"
            catalog_path = root / "references" / "table_catalog.md"
            schema_path.parent.mkdir(parents=True)
            catalog_path.parent.mkdir(parents=True)
            schema_path.write_text(json.dumps(LOCAL_SCHEMA), encoding="utf-8")
            catalog_path.write_text("old catalog", encoding="utf-8")
            client = FakeClient(schema_path=schema_path)

            with patch.object(db_tools, "_skill_root", return_value=root):
                db_tools.cmd_refresh_schema(client, confirmed=True)

            self.assertEqual(json.loads(schema_path.read_text(encoding="utf-8")), REMOTE_SCHEMA)
            self.assertNotEqual(catalog_path.read_text(encoding="utf-8"), "old catalog")
            self.assertEqual(len(list(root.rglob("*.bak.*"))), 2)


if __name__ == "__main__":
    unittest.main()
