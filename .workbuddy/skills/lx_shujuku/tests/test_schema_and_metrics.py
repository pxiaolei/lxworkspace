import json
import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from lx_shujuku.schema_tools import diff_schemas


class SchemaDiffTests(unittest.TestCase):
    def test_diff_schemas_reports_table_and_column_changes(self):
        local = {
            "tables": [
                {
                    "name": "old_table",
                    "comment": "old",
                    "columns": [{"field": "id", "type": "int", "comment": "id"}],
                },
                {
                    "name": "same_table",
                    "comment": "same",
                    "columns": [
                        {"field": "id", "type": "int", "comment": "id"},
                        {"field": "old_col", "type": "varchar(10)", "comment": ""},
                    ],
                },
            ]
        }
        remote = {
            "tables": [
                {
                    "name": "new_table",
                    "comment": "new",
                    "columns": [{"field": "id", "type": "int", "comment": "id"}],
                },
                {
                    "name": "same_table",
                    "comment": "same",
                    "columns": [
                        {"field": "id", "type": "bigint", "comment": "id"},
                        {"field": "new_col", "type": "varchar(10)", "comment": ""},
                    ],
                },
            ]
        }

        diff = diff_schemas(local, remote)

        self.assertEqual(diff["summary"]["added_table_count"], 1)
        self.assertEqual(diff["summary"]["removed_table_count"], 1)
        self.assertEqual(diff["summary"]["changed_table_count"], 1)
        self.assertEqual(diff["added_tables"], ["new_table"])
        self.assertEqual(diff["removed_tables"], ["old_table"])
        change = diff["changed_tables"][0]
        self.assertEqual(change["table"], "same_table")
        self.assertEqual(change["added_columns"], ["new_col"])
        self.assertEqual(change["removed_columns"], ["old_col"])
        self.assertEqual(change["changed_columns"][0]["field"], "id")


class MetricsCatalogTests(unittest.TestCase):
    def test_completed_orders_metric_has_required_business_contract(self):
        catalog_path = SKILL_DIR / "references" / "metrics_catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        metric = catalog["metrics"]["brand_city_daily_completed_orders"]

        self.assertEqual(metric["default_table"], "honghu_order_data")
        self.assertEqual(metric["date_field"], "date_day")
        self.assertEqual(metric["measure"]["field"], "completed_order_count")
        self.assertEqual(metric["measure"]["aggregation"], "SUM")
        self.assertIn("verification_queries", metric)


if __name__ == "__main__":
    unittest.main()
