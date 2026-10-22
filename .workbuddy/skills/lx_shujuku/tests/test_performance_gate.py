"""公司库只读 SQL 性能门禁测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch
from lx_shujuku.client import DataReportingClient

from lx_shujuku.query_policy import (
    check_performance_gates,
    evaluate_explain_plan,
    load_perf_gate_config,
    should_run_explain,
)

CFG = load_perf_gate_config()


class CentralPerformanceGateTests(unittest.TestCase):
    def test_bad_explain_stops_before_the_real_query(self):
        client = object.__new__(DataReportingClient)
        sql = "SELECT brand_name, SUM(gmv) FROM honghu_detail_data WHERE date_day='2026-10-01' GROUP BY brand_name"
        with patch.object(client, "prepare_sql", return_value=sql), patch.object(client, "_execute_prepared_sql", return_value=[{"table": "honghu_detail_data", "type": "ALL", "key": None, "rows": 200000}]) as execute:
            with self.assertRaisesRegex(RuntimeError, "性能门禁"):
                client.execute(sql)
            self.assertEqual(execute.call_count, 1)
            self.assertTrue(execute.call_args.args[0].startswith("EXPLAIN "))

    def test_missing_explain_stops_before_the_real_query(self):
        client = object.__new__(DataReportingClient)
        sql = "SELECT brand_name, SUM(gmv) FROM honghu_detail_data WHERE date_day='2026-10-01' GROUP BY brand_name"
        with patch.object(client, "prepare_sql", return_value=sql), patch.object(client, "_execute_prepared_sql", side_effect=RuntimeError("fixture no plan")) as execute:
            with self.assertRaisesRegex(RuntimeError, "无法取得执行计划"):
                client.execute(sql)
            self.assertEqual(execute.call_count, 1)


def violations(sql: str) -> list[str]:
    return check_performance_gates(sql, CFG)


class TestRequireWhere(unittest.TestCase):
    def test_missing_where_is_blocked(self):
        self.assertTrue(
            any("性能门禁 1" in v for v in violations("SELECT brand_name FROM honghu_detail_data"))
        )

    def test_with_where_passes(self):
        self.assertEqual(
            violations("SELECT brand_name FROM operator_brand WHERE brand_name = 'x'"),
            [],
        )

    def test_date_range_passes(self):
        self.assertEqual(
            violations(
                "SELECT date_day, gmv FROM honghu_detail_data "
                "WHERE date_day >= '2026-09-01' AND date_day < '2026-10-01'"
            ),
            [],
        )


class TestSelectStar(unittest.TestCase):
    def test_bare_star_is_blocked(self):
        self.assertTrue(any("性能门禁 2" in v for v in violations("SELECT * FROM operator_brand WHERE id > 1")))

    def test_alias_star_is_blocked(self):
        self.assertTrue(
            any("性能门禁 2" in v for v in violations("SELECT t.* FROM operator_brand t WHERE id > 1"))
        )

    def test_count_star_is_not_select_star(self):
        self.assertEqual(
            violations("SELECT COUNT(*) AS c FROM operator_brand WHERE id > 1"),
            [],
        )

    def test_explicit_columns_pass(self):
        self.assertEqual(
            violations("SELECT id, brand_name FROM operator_brand WHERE id > 1"),
            [],
        )


class TestColumnFunction(unittest.TestCase):
    def test_year_on_column_is_blocked(self):
        sql = "SELECT id FROM honghu_order_data WHERE YEAR(date_day) = 2026"
        self.assertTrue(any("性能门禁 3" in v for v in violations(sql)))

    def test_trim_on_join_key_is_blocked(self):
        sql = (
            "SELECT f.brand_name FROM honghu_order_data f "
            "INNER JOIN operator_brand ob ON TRIM(ob.brand_name) = f.brand_name "
            "WHERE f.date_day = '2026-09-01'"
        )
        self.assertTrue(any("性能门禁 3" in v for v in violations(sql)))

    def test_leading_wildcard_like_is_blocked(self):
        sql = "SELECT brand_name FROM operator_brand WHERE brand_name LIKE '%出行'"
        self.assertTrue(any("性能门禁 3" in v for v in violations(sql)))

    def test_function_in_select_list_is_allowed(self):
        sql = (
            "SELECT DATE_FORMAT(date_day, '%Y-%m') AS m, SUM(gmv) "
            "FROM honghu_detail_data WHERE date_day >= '2026-09-01' GROUP BY m"
        )
        self.assertEqual(violations(sql), [])

    def test_range_comparison_passes(self):
        sql = (
            "SELECT id FROM honghu_order_data "
            "WHERE date_day >= '2026-01-01' AND date_day < '2026-02-01'"
        )
        self.assertEqual(violations(sql), [])


class TestNonSelectSkipped(unittest.TestCase):
    def test_show_is_not_gated(self):
        self.assertEqual(check_performance_gates("SHOW TABLES", CFG), [])

    def test_describe_is_not_gated(self):
        self.assertEqual(check_performance_gates("DESCRIBE operator_brand", CFG), [])


class TestExplainGate(unittest.TestCase):
    def test_join_triggers_explain(self):
        self.assertTrue(
            should_run_explain(
                "SELECT f.id FROM honghu_order_data f "
                "INNER JOIN operator_brand ob ON ob.brand_name = f.brand_name "
                "WHERE f.date_day = '2026-09-01'",
                CFG,
            )
        )

    def test_group_by_triggers_explain(self):
        self.assertTrue(
            should_run_explain(
                "SELECT date_day, SUM(gmv) FROM honghu_detail_data "
                "WHERE date_day >= '2026-09-01' GROUP BY date_day",
                CFG,
            )
        )

    def test_plain_select_does_not_trigger(self):
        self.assertFalse(
            should_run_explain("SELECT id FROM operator_brand WHERE id = 1", CFG)
        )

    def test_full_scan_above_threshold_is_blocked(self):
        plan = [{"table": "honghu_capacity_data", "type": "ALL", "key": None, "rows": 5642807}]
        self.assertTrue(evaluate_explain_plan(plan, CFG))

    def test_indexed_large_result_passes(self):
        plan = [{"table": "honghu_capacity_data", "type": "ref", "key": "idx_brand", "rows": 5000000}]
        self.assertEqual(evaluate_explain_plan(plan, CFG), [])

    def test_small_full_scan_passes(self):
        plan = [{"table": "operator_brand", "type": "ALL", "key": None, "rows": 788}]
        self.assertEqual(evaluate_explain_plan(plan, CFG), [])

    def test_null_key_above_threshold_is_blocked(self):
        plan = [{"table": "honghu_recon_data", "type": "index", "key": None, "rows": 900000}]
        self.assertTrue(evaluate_explain_plan(plan, CFG))


class TestConfigFallback(unittest.TestCase):
    def test_missing_file_returns_defaults(self):
        cfg = load_perf_gate_config("/nonexistent/path/perf_gate_config.json")
        self.assertEqual(cfg["mode"], "enforce")
        self.assertTrue(cfg["gates"]["require_where"])

    def test_default_mode_is_enforce(self):
        self.assertEqual(CFG["mode"], "enforce")

    def test_only_operator_brand_mapping_has_explicit_full_read_exception(self):
        self.assertEqual(CFG["where_exempt_tables"], ["operator_brand"])
        self.assertEqual(violations("SELECT brand_name FROM operator_brand"), [])
        self.assertTrue(violations("SELECT brand_name FROM honghu_detail_data"))


if __name__ == "__main__":
    unittest.main()
