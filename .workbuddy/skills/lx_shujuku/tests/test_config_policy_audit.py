import re
import sys
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import SimpleNamespace


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from lx_shujuku.client import DataReportingClient, _parse_allowed_table_names
from lx_shujuku.query_policy import analyze_query_shape, ensure_readonly_sql


class ConfigParsingTests(unittest.TestCase):
    def test_inline_comments_are_not_part_of_values(self):
        config = DataReportingClient._parse_simple_yaml(
            """
api:
  base_url: "http://example.test"  # internal host
  username: "alice"     # account name
  password: 'secret#value'  # hash inside quotes is value
timeout: 45  # seconds
default_limit: 25
max_limit: 200
"""
        )

        self.assertEqual(config["base_url"], "http://example.test")
        self.assertEqual(config["username"], "alice")
        self.assertEqual(config["password"], "secret#value")
        self.assertEqual(config["timeout"], 45)
        self.assertEqual(config["default_limit"], 25)
        self.assertEqual(config["max_limit"], 200)

    def test_configured_timeout_and_limits_are_respected(self):
        with NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(
                """
api:
  base_url: "http://example.test"
  username: "alice"
  password: "secret"
timeout: 45
default_limit: 25
max_limit: 200
"""
            )
            config_path = f.name

        try:
            client = DataReportingClient(config_path=config_path)
        finally:
            Path(config_path).unlink(missing_ok=True)

        self.assertEqual(client.timeout, 45)
        self.assertEqual(client.default_limit, 25)
        self.assertEqual(client.max_limit, 200)


class QueryPolicyTests(unittest.TestCase):
    def test_select_gets_default_limit_and_uses_known_tables_only(self):
        sql = ensure_readonly_sql(
            "SELECT city_name FROM honghu_order_data WHERE date_day = '2026-08-31'",
            default_limit=100,
            max_limit=1000,
            allowed_tables={"honghu_order_data"},
        )

        self.assertEqual(
            sql,
            "SELECT city_name FROM honghu_order_data "
            "WHERE date_day = '2026-08-31' LIMIT 100",
        )

    def test_unknown_table_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "表不在 schema 白名单中"):
            ensure_readonly_sql(
                "SELECT * FROM unknown_table LIMIT 10",
                default_limit=100,
                max_limit=1000,
                allowed_tables={"honghu_order_data"},
            )

    def test_select_into_outfile_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "禁止关键字"):
            ensure_readonly_sql(
                "SELECT * FROM honghu_order_data INTO OUTFILE '/tmp/x' LIMIT 1",
                default_limit=100,
                max_limit=1000,
                allowed_tables={"honghu_order_data"},
            )

    def test_show_is_limited_to_table_metadata(self):
        with self.assertRaisesRegex(RuntimeError, "SHOW 仅允许"):
            ensure_readonly_sql(
                "SHOW VARIABLES",
                default_limit=100,
                max_limit=1000,
                allowed_tables={"honghu_order_data"},
            )

    def test_show_index_allows_only_a_known_table(self):
        sql = ensure_readonly_sql(
            "SHOW INDEX FROM honghu_order_data",
            default_limit=100,
            max_limit=1000,
            allowed_tables={"honghu_order_data"},
        )

        self.assertEqual(sql, "SHOW INDEX FROM honghu_order_data")

    def test_show_index_rejects_unknown_table(self):
        with self.assertRaisesRegex(RuntimeError, "表不在 schema 白名单中"):
            ensure_readonly_sql(
                "SHOW INDEX FROM unknown_table",
                default_limit=100,
                max_limit=1000,
                allowed_tables={"honghu_order_data"},
            )

    def test_show_index_cannot_disable_whitelist(self):
        with self.assertRaisesRegex(RuntimeError, "SHOW INDEX 必须使用本地 schema 白名单"):
            ensure_readonly_sql(
                "SHOW INDEX FROM unknown_table",
                default_limit=100,
                max_limit=1000,
                allowed_tables=None,
            )

    def test_show_index_rejects_any_suffix(self):
        with self.assertRaisesRegex(RuntimeError, "SHOW 仅允许"):
            ensure_readonly_sql(
                "SHOW INDEX FROM honghu_order_data WHERE Key_name = 'idx_date'",
                default_limit=100,
                max_limit=1000,
                allowed_tables={"honghu_order_data"},
            )

    def test_backtick_identifier_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "反引号"):
            ensure_readonly_sql(
                "SELECT * FROM `unknown_table` LIMIT 10",
                default_limit=100,
                max_limit=1000,
                allowed_tables={"honghu_order_data"},
            )

    def test_comma_join_unknown_table_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "表不在 schema 白名单中"):
            ensure_readonly_sql(
                "SELECT * FROM honghu_order_data, unknown_table LIMIT 10",
                default_limit=100,
                max_limit=1000,
                allowed_tables={"honghu_order_data"},
            )

    def test_limit_inside_string_does_not_replace_top_level_limit(self):
        sql = ensure_readonly_sql(
            "SELECT city_name FROM honghu_order_data WHERE city_name = 'limit 999'",
            default_limit=50,
            max_limit=1000,
            allowed_tables={"honghu_order_data"},
        )

        self.assertTrue(sql.endswith("LIMIT 50"))

    def test_limit_inside_subquery_does_not_replace_top_level_limit(self):
        sql = ensure_readonly_sql(
            "SELECT city_name FROM "
            "(SELECT city_name FROM honghu_order_data WHERE date_day = '2026-08-31' LIMIT 5) t",
            default_limit=50,
            max_limit=1000,
            allowed_tables={"honghu_order_data"},
        )

        self.assertTrue(sql.endswith("LIMIT 50"))

    def test_query_shape_only_reports_top_level_clauses(self):
        shape = analyze_query_shape(
            "SELECT * FROM (SELECT * FROM honghu_order_data LIMIT 5) t "
            "ORDER BY city_name LIMIT 20 OFFSET 40"
        )

        self.assertEqual(shape["top_level_limit"], 20)
        self.assertEqual(shape["top_level_offset"], 40)
        self.assertTrue(shape["has_top_level_order_by"])

    def test_deep_offset_is_rejected_before_query_execution(self):
        with self.assertRaisesRegex(RuntimeError, "OFFSET 不能超过 10000"):
            ensure_readonly_sql(
                "SELECT city_name FROM honghu_order_data "
                "WHERE date_day = '2026-08-31' LIMIT 100 OFFSET 10001",
                default_limit=100,
                max_limit=1000,
                allowed_tables={"honghu_order_data"},
            )

    def test_select_star_is_rejected_before_query_execution(self):
        with self.assertRaisesRegex(RuntimeError, r"SELECT \*"):
            ensure_readonly_sql(
                "SELECT * FROM honghu_order_data WHERE date_day = '2026-08-31' LIMIT 10",
                default_limit=100,
                max_limit=1000,
                allowed_tables={"honghu_order_data"},
            )

    def test_count_star_with_business_scope_remains_allowed(self):
        sql = ensure_readonly_sql(
            "SELECT COUNT(*) AS cnt FROM honghu_order_data "
            "WHERE date_day = '2026-08-31' LIMIT 1",
            default_limit=100,
            max_limit=1000,
            allowed_tables={"honghu_order_data"},
        )

        self.assertIn("COUNT(*)", sql)

    def test_unbounded_fact_query_is_rejected_even_with_limit(self):
        with self.assertRaisesRegex(RuntimeError, "业务范围"):
            ensure_readonly_sql(
                "SELECT id FROM honghu_order_data LIMIT 10",
                default_limit=100,
                max_limit=1000,
                allowed_tables={"honghu_order_data"},
            )

    def test_unbounded_fact_query_cannot_hide_inside_derived_table(self):
        with self.assertRaisesRegex(RuntimeError, "业务范围"):
            ensure_readonly_sql(
                "SELECT id FROM (SELECT id FROM honghu_order_data) t LIMIT 10",
                default_limit=100,
                max_limit=1000,
                allowed_tables={"honghu_order_data"},
            )

    def test_unbounded_max_boundary_query_remains_allowed(self):
        sql = ensure_readonly_sql(
            "SELECT MAX(date_day) AS latest_date FROM honghu_order_data",
            default_limit=100,
            max_limit=1000,
            allowed_tables={"honghu_order_data"},
        )

        self.assertTrue(sql.endswith("LIMIT 100"))

    def test_single_row_table_probe_remains_allowed(self):
        sql = ensure_readonly_sql(
            "SELECT 1 FROM honghu_order_data LIMIT 1",
            default_limit=100,
            max_limit=1000,
            allowed_tables={"honghu_order_data"},
        )

        self.assertEqual(sql, "SELECT 1 FROM honghu_order_data LIMIT 1")

    def test_where_true_only_is_not_a_business_scope(self):
        with self.assertRaisesRegex(RuntimeError, "业务范围"):
            ensure_readonly_sql(
                "SELECT id FROM brand_city_tr_data WHERE 1=1 LIMIT 10",
                default_limit=100,
                max_limit=1000,
                allowed_tables={"brand_city_tr_data"},
            )

    def test_predicate_functions_are_rejected(self):
        risky_sql = {
            "date": "SELECT id FROM honghu_order_data WHERE DATE(date_day) = '2026-08-31'",
            "trim": "SELECT id FROM honghu_order_data WHERE TRIM(city_name) = '上海市'",
            "cast": "SELECT id FROM honghu_order_data WHERE CAST(date_day AS DATE) = '2026-08-31'",
            "collate": "SELECT id FROM honghu_order_data WHERE city_name COLLATE utf8mb4_bin = '上海市'",
        }
        for name, sql in risky_sql.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(RuntimeError, "谓词"):
                    ensure_readonly_sql(
                        sql,
                        default_limit=100,
                        max_limit=1000,
                        allowed_tables={"honghu_order_data"},
                    )

    def test_aggregate_functions_in_select_list_remain_allowed(self):
        sql = ensure_readonly_sql(
            "SELECT city_name, COALESCE(SUM(completed_order_count), 0) AS completed "
            "FROM honghu_order_data WHERE date_day = '2026-08-31' GROUP BY city_name",
            default_limit=100,
            max_limit=1000,
            allowed_tables={"honghu_order_data"},
        )

        self.assertTrue(sql.endswith("LIMIT 100"))

    def test_predicate_arithmetic_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "算术表达式"):
            ensure_readonly_sql(
                "SELECT id FROM honghu_order_data "
                "WHERE completed_order_count + 1 > 10 LIMIT 10",
                default_limit=100,
                max_limit=1000,
                allowed_tables={"honghu_order_data"},
            )

    def test_leading_wildcard_like_is_rejected_but_prefix_search_is_allowed(self):
        with self.assertRaisesRegex(RuntimeError, "前导通配"):
            ensure_readonly_sql(
                "SELECT product_id FROM honghu_coupon_marketing_data "
                "WHERE product_name LIKE '%飞涨' LIMIT 10",
                default_limit=100,
                max_limit=1000,
                allowed_tables={"honghu_coupon_marketing_data"},
            )

        sql = ensure_readonly_sql(
            "SELECT product_id FROM honghu_coupon_marketing_data "
            "WHERE product_name LIKE '飞涨%' LIMIT 10",
            default_limit=100,
            max_limit=1000,
            allowed_tables={"honghu_coupon_marketing_data"},
        )
        self.assertIn("LIKE '飞涨%'", sql)

    def test_inclusive_date_upper_bound_and_between_are_rejected(self):
        risky_sql = [
            "SELECT id FROM honghu_order_data "
            "WHERE date_day >= '2026-08-01' AND date_day <= '2026-08-31'",
            "SELECT id FROM honghu_order_data "
            "WHERE date_day BETWEEN '2026-08-01' AND '2026-08-31'",
        ]
        for sql in risky_sql:
            with self.subTest(sql=sql):
                with self.assertRaisesRegex(RuntimeError, "半开区间"):
                    ensure_readonly_sql(
                        sql,
                        default_limit=100,
                        max_limit=1000,
                        allowed_tables={"honghu_order_data"},
                    )

        with self.assertRaisesRegex(RuntimeError, "半开区间"):
            ensure_readonly_sql(
                "SELECT id FROM answer_rate_data "
                "WHERE data_date >= '2026-08-01' AND data_date <= '2026-08-31'",
                default_limit=100,
                max_limit=1000,
                allowed_tables={"answer_rate_data"},
            )

    def test_half_open_date_range_remains_allowed(self):
        sql = ensure_readonly_sql(
            "SELECT id FROM honghu_order_data "
            "WHERE date_day >= '2026-08-01' AND date_day < '2026-09-01'",
            default_limit=100,
            max_limit=1000,
            allowed_tables={"honghu_order_data"},
        )

        self.assertTrue(sql.endswith("LIMIT 100"))


class BusinessTemplateSqlTests(unittest.TestCase):
    class RecordingClient(DataReportingClient):
        def __init__(self):
            self.max_limit = 1000
            self.schema = SimpleNamespace(validate_table_name=lambda table: table)
            self.seen_sql = []

        def execute(self, sql, *args, **kwargs):
            self.seen_sql.append(" ".join(sql.split()))
            return []

    def test_templates_select_explicit_fields(self):
        client = self.RecordingClient()
        client.query_capacity_by_brand_date("品牌A", "2026-08-31", "上海市")
        client.query_order_by_brand_date("品牌A", "2026-08-31")
        client.query_time_split_by_hour("品牌A", "2026-08-31", "上海市")
        client.query_driver_real_time("主体A", "2026-08-31")
        client.query_order_real_time("主体A", "2026-08-31")
        client.query_tr_config("2026-08-31", "品牌A", "上海市")

        for sql in client.seen_sql:
            with self.subTest(sql=sql):
                self.assertNotRegex(sql.lower(), r"^select\s+(?:[a-z_][a-z0-9_]*\.)?\*")

    def test_templates_without_usable_index_fail_closed(self):
        client = self.RecordingClient()
        calls = [
            lambda: client.query_by_brand_date("activity_data", "品牌A", "2026-08-31"),
            lambda: client.query_by_brand_date("card_data", "品牌A", "2026-08-31"),
            lambda: client.query_activity_by_operator("主体A"),
            lambda: client.query_coupon_by_product("飞涨卡"),
        ]

        for call in calls:
            with self.subTest(call=call):
                with self.assertRaisesRegex(RuntimeError, "可用索引"):
                    call()
        self.assertEqual(client.seen_sql, [])

    def test_tr_config_requires_date_and_brand_or_city_scope(self):
        client = self.RecordingClient()

        with self.assertRaisesRegex(RuntimeError, "日期"):
            client.query_tr_config("", "品牌A", "")
        with self.assertRaisesRegex(RuntimeError, "品牌或城市"):
            client.query_tr_config("2026-08-31")

        client.query_tr_config("2026-08-31", "品牌A", "上海市")
        self.assertIn("date_day = '2026-08-31'", client.seen_sql[0])


class AuditPackageTests(unittest.TestCase):
    def test_execute_audited_returns_reproducible_evidence(self):
        class FakeClient(DataReportingClient):
            def __init__(self):
                self.base_url = "http://example.test"
                self.default_limit = 100
                self.max_limit = 1000
                self.schema = SimpleNamespace(table_names={"honghu_order_data"})

            def _execute_prepared_sql(self, safe_sql):
                self.seen_sql = safe_sql
                return [{"city_name": "上海市", "completed_order_count": 1}]

        client = FakeClient()
        audit = client.execute_audited(
            "SELECT city_name, completed_order_count FROM honghu_order_data "
            "WHERE date_day = '2026-08-31'",
            question="查完单",
            metric="brand_city_daily_completed_orders",
        )

        self.assertEqual(audit["type"], "lx_shujuku.query_run")
        self.assertEqual(audit["version"], 2)
        self.assertEqual(audit["question"], "查完单")
        self.assertEqual(audit["metric"], "brand_city_daily_completed_orders")
        self.assertEqual(audit["safe_sql"], client.seen_sql)
        self.assertTrue(audit["safe_sql"].endswith("LIMIT 100"))
        self.assertEqual(audit["row_count"], 1)
        self.assertTrue(audit["is_complete"])
        self.assertFalse(audit["possible_truncation"])
        self.assertEqual(len(audit["result_sha256"]), 64)
        self.assertEqual(
            audit["metric_contract"]["id"],
            "brand_city_daily_completed_orders",
        )
        self.assertEqual(audit["rows"][0]["city_name"], "上海市")

    def test_limit_boundary_is_not_reported_as_complete(self):
        class FakeClient(DataReportingClient):
            def __init__(self):
                self.base_url = "http://example.test"
                self.default_limit = 1
                self.max_limit = 1000
                self.schema = SimpleNamespace(table_names={"honghu_order_data"})

            def _execute_prepared_sql(self, safe_sql):
                return [{"city_name": "上海市"}]

        audit = FakeClient().execute_audited(
            "SELECT city_name FROM honghu_order_data WHERE date_day = '2026-08-31'"
        )

        self.assertIsNone(audit["is_complete"])
        self.assertTrue(audit["possible_truncation"])
        self.assertIn("可能被截断", " ".join(audit["warnings"]))

    def test_unknown_metric_is_rejected_before_query(self):
        class FakeClient(DataReportingClient):
            def __init__(self):
                self.base_url = "http://example.test"
                self.default_limit = 100
                self.max_limit = 1000
                self.schema = SimpleNamespace(table_names={"honghu_order_data"})

            def _execute_prepared_sql(self, safe_sql):
                self.fail("未知指标不应执行查询")

        with self.assertRaisesRegex(RuntimeError, "未知指标口径 ID"):
            FakeClient().execute_audited(
                "SELECT city_name FROM honghu_order_data",
                metric="not_a_metric",
            )

    def test_full_query_uses_single_keyset_pass_and_count_boundaries(self):
        class FakeClient(DataReportingClient):
            def __init__(self):
                self.base_url = "http://example.test"
                self.default_limit = 100
                self.max_limit = 1000
                self.schema = SimpleNamespace(table_names={"honghu_order_data"})
                self.data = [
                    {"id": 1, "city_name": "上海市"},
                    {"id": 2, "city_name": "杭州市"},
                    {"id": 3, "city_name": "南京市"},
                ]
                self.seen_sql = []

            def _execute_prepared_sql(self, safe_sql):
                self.seen_sql.append(safe_sql)
                if "__lx_total_rows" in safe_sql:
                    return [{"__lx_total_rows": len(self.data)}]
                if safe_sql.endswith("ORDER BY id LIMIT 2"):
                    return self.data[:2]
                if "__lx_full_page.id > 2" in safe_sql and safe_sql.endswith("LIMIT 2"):
                    return self.data[2:]
                raise AssertionError(f"未识别 keyset 分页 SQL: {safe_sql}")

        client = FakeClient()
        audit = client.execute_full_audited(
            "SELECT id, city_name FROM honghu_order_data WHERE id >= 1 ORDER BY id",
            page_size=2,
            max_rows=10,
        )

        self.assertEqual(audit["result_mode"], "full")
        self.assertEqual(audit["version"], 3)
        self.assertTrue(audit["is_complete"])
        self.assertEqual(audit["total_rows"], 3)
        self.assertEqual(audit["verification"]["verification_passes"], 1)
        self.assertEqual(audit["verification"]["page_count"], 2)
        self.assertEqual(
            audit["verification"]["method"],
            "ordered_single_pass_keyset_with_count",
        )
        self.assertEqual(audit["order_columns"], ["id"])
        self.assertFalse(any(" OFFSET " in sql for sql in client.seen_sql))

    def test_full_query_rejects_more_than_twenty_pages_before_fetch(self):
        class FakeClient(DataReportingClient):
            def __init__(self):
                self.base_url = "http://example.test"
                self.default_limit = 100
                self.max_limit = 1000
                self.schema = SimpleNamespace(table_names={"honghu_order_data"})

            def _execute_prepared_sql(self, safe_sql):
                if "__lx_total_rows" in safe_sql:
                    return [{"__lx_total_rows": 10001}]
                match = re.search(r"LIMIT (\d+) OFFSET (\d+)$", safe_sql)
                if match is None:
                    raise AssertionError(f"未识别分页 SQL: {safe_sql}")
                size, offset = (int(value) for value in match.groups())
                return [{"id": value} for value in range(offset, min(offset + size, 10001))]

        with self.assertRaisesRegex(RuntimeError, "超过完整查询页数上限 20"):
            FakeClient().execute_full_audited(
                "SELECT id FROM honghu_order_data WHERE date_day = '2026-08-31' ORDER BY id",
                page_size=500,
                max_rows=20000,
            )

    def test_full_query_rejects_unbounded_fact_table_before_count(self):
        class FakeClient(DataReportingClient):
            def __init__(self):
                self.base_url = "http://example.test"
                self.default_limit = 100
                self.max_limit = 1000
                self.schema = SimpleNamespace(table_names={"honghu_order_data"})

            def _execute_prepared_sql(self, safe_sql):
                raise AssertionError(f"无界事实表查询不应发送到服务端: {safe_sql}")

        with self.assertRaisesRegex(RuntimeError, "必须包含顶层 WHERE"):
            FakeClient().execute_full_audited(
                "SELECT id FROM honghu_order_data ORDER BY id",
                page_size=500,
                max_rows=1000,
            )

    def test_full_query_allows_unbounded_small_code_table(self):
        class FakeClient(DataReportingClient):
            def __init__(self):
                self.base_url = "http://example.test"
                self.default_limit = 100
                self.max_limit = 1000
                self.schema = SimpleNamespace(table_names={"operator_brand"})

            def _execute_prepared_sql(self, safe_sql):
                if "__lx_total_rows" in safe_sql:
                    return [{"__lx_total_rows": 2}]
                return [{"id": 1}, {"id": 2}]

        audit = FakeClient().execute_full_audited(
            "SELECT id FROM operator_brand ORDER BY id",
            page_size=10,
            max_rows=10,
        )

        self.assertTrue(audit["is_complete"])
        self.assertEqual(audit["row_count"], 2)

    def test_full_query_keyset_supports_mixed_sort_directions(self):
        class FakeClient(DataReportingClient):
            def __init__(self):
                self.base_url = "http://example.test"
                self.default_limit = 100
                self.max_limit = 1000
                self.schema = SimpleNamespace(table_names={"honghu_order_data"})
                self.seen_sql = []

            def _execute_prepared_sql(self, safe_sql):
                self.seen_sql.append(safe_sql)
                if "__lx_total_rows" in safe_sql:
                    return [{"__lx_total_rows": 3}]
                if "__lx_full_page" not in safe_sql:
                    return [
                        {"date_day": "2026-08-02", "id": 1},
                        {"date_day": "2026-08-02", "id": 2},
                    ]
                if (
                    "__lx_full_page.date_day < '2026-08-02'" in safe_sql
                    and "__lx_full_page.date_day = '2026-08-02'" in safe_sql
                    and "__lx_full_page.id > 2" in safe_sql
                ):
                    return [{"date_day": "2026-08-01", "id": 1}]
                raise AssertionError(f"未识别复合游标 SQL: {safe_sql}")

        audit = FakeClient().execute_full_audited(
            "SELECT date_day, id FROM honghu_order_data "
            "WHERE date_day >= '2026-08-01' ORDER BY date_day DESC, id ASC",
            page_size=2,
            max_rows=10,
        )

        self.assertEqual(audit["order_columns"], ["date_day", "id"])
        self.assertEqual(audit["verification"]["page_count"], 2)

    def test_full_query_rejects_non_unique_order(self):
        class FakeClient(DataReportingClient):
            def __init__(self):
                self.base_url = "http://example.test"
                self.default_limit = 100
                self.max_limit = 1000
                self.schema = SimpleNamespace(table_names={"honghu_order_data"})

            def _execute_prepared_sql(self, safe_sql):
                if "__lx_total_rows" in safe_sql:
                    return [{"__lx_total_rows": 2}]
                return [
                    {"city_name": "上海市"},
                    {"city_name": "上海市"},
                ]

        with self.assertRaisesRegex(RuntimeError, "字段组合不唯一"):
            FakeClient().execute_full_audited(
                "SELECT city_name FROM honghu_order_data "
                "WHERE date_day = '2026-08-31' ORDER BY city_name",
                page_size=10,
                max_rows=10,
            )

    def test_full_single_row_aggregate_does_not_require_order_by(self):
        class FakeClient(DataReportingClient):
            def __init__(self):
                self.base_url = "http://example.test"
                self.default_limit = 100
                self.max_limit = 1000
                self.schema = SimpleNamespace(table_names={"honghu_order_data"})

            def _execute_prepared_sql(self, safe_sql):
                if "__lx_total_rows" in safe_sql:
                    return [{"__lx_total_rows": 1}]
                return [{"completed_order_count": 3}]

        audit = FakeClient().execute_full_audited(
            "SELECT SUM(completed_order_count) AS completed_order_count "
            "FROM honghu_order_data WHERE date_day = '2026-08-31'",
            page_size=10,
            max_rows=10,
        )

        self.assertTrue(audit["is_complete"])
        self.assertFalse(audit["order_by_present"])
        self.assertEqual(audit["order_columns"], [])
        self.assertEqual(
            audit["verification"]["method"],
            "single_row_single_read_with_count",
        )


class TableDiscoveryTests(unittest.TestCase):
    def test_show_indexes_returns_normalized_ordered_metadata(self):
        class FakeSchema:
            table_names = {"honghu_order_data"}

            @staticmethod
            def validate_table_name(table_name):
                if table_name != "honghu_order_data":
                    raise RuntimeError(f"表不在 schema 白名单中: {table_name}")
                return table_name

        class FakeClient(DataReportingClient):
            def __init__(self):
                self.default_limit = 100
                self.max_limit = 1000
                self.schema = FakeSchema()
                self.seen_sql = []

            def _execute_prepared_sql(self, safe_sql):
                self.seen_sql.append(safe_sql)
                return [
                    {
                        "Table": "honghu_order_data",
                        "Non_unique": 1,
                        "Key_name": "idx_brand_date",
                        "Seq_in_index": 2,
                        "Column_name": "date_day",
                        "Collation": "A",
                        "Cardinality": 365,
                        "Sub_part": None,
                        "Packed": None,
                        "Null": "YES",
                        "Index_type": "BTREE",
                        "Comment": "",
                        "Index_comment": "",
                        "Visible": "YES",
                        "Expression": None,
                    },
                    {
                        "Table": "honghu_order_data",
                        "Non_unique": 1,
                        "Key_name": "idx_brand_date",
                        "Seq_in_index": 1,
                        "Column_name": "brand_name",
                        "Collation": "A",
                        "Cardinality": 100,
                        "Sub_part": None,
                        "Packed": None,
                        "Null": "YES",
                        "Index_type": "BTREE",
                        "Comment": "",
                        "Index_comment": "",
                        "Visible": "YES",
                        "Expression": None,
                    },
                ]

        client = FakeClient()
        indexes = client.show_indexes("honghu_order_data")

        self.assertEqual(client.seen_sql, ["SHOW INDEX FROM honghu_order_data"])
        self.assertEqual(
            [item["column_name"] for item in indexes],
            ["brand_name", "date_day"],
        )
        self.assertEqual(indexes[0]["key_name"], "idx_brand_date")
        self.assertFalse(indexes[0]["unique"])
        self.assertEqual(indexes[0]["index_type"], "BTREE")

    def test_parse_allowed_table_names_from_server_error(self):
        message = (
            "查询失败: 表 [missing_table] 不在允许查询的范围内，"
            "仅支持以下表: [card_data, honghu_profit_data, operator_brand]"
        )

        self.assertEqual(
            _parse_allowed_table_names(message),
            ["card_data", "honghu_profit_data", "operator_brand"],
        )

    def test_list_tables_merges_show_tables_and_server_allowed_tables(self):
        class FakeClient(DataReportingClient):
            def __init__(self):
                self.schema = SimpleNamespace(table_names={"old_table"})

            def execute(self, sql, enforce_table_whitelist=True):
                if sql == "SHOW TABLES":
                    return [
                        {
                            "Tables_in_datareporting": "old_table",
                            "TABLE_COMMENT": "旧表",
                        }
                    ]
                if "lx_shujuku_table_probe_missing" in sql:
                    raise RuntimeError(
                        "查询失败: 表 [lx_shujuku_table_probe_missing] "
                        "不在允许查询的范围内，仅支持以下表: [old_table, new_table]"
                    )
                return []

        self.assertEqual(
            FakeClient().list_tables(),
            [
                {"name": "new_table", "comment": ""},
                {"name": "old_table", "comment": "旧表"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
