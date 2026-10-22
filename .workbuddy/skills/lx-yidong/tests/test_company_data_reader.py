import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from company_data_reader import SOURCE_FIELDS, build_query_plan, load_company_payload
from anomaly_core import analyze_payload


PERIOD = {"type": "range", "start": "2026-10-06", "end": "2026-10-07", "compare_start": "2026-09-29", "compare_end": "2026-09-30"}


def daily(day, identifier):
    return {**{field: 10 for field in SOURCE_FIELDS}, "id": identifier, "brand_name": "示例品牌", "city_name": "示例市", "date_day": day}


class FakeClient:
    def __init__(self, current=None, compare=None):
        self.datasets = [current if current is not None else [daily("2026-10-06", 1), daily("2026-10-07", 2)], compare if compare is not None else [daily("2026-09-29", 3), daily("2026-09-30", 4)]]
        self.calls = []
    def execute_full_audited(self, sql, **kwargs):
        rows = self.datasets[len(self.calls)]
        self.calls.append(sql)
        return {"rows": rows, "row_count": len(rows), "total_rows": len(rows), "is_complete": True, "possible_truncation": False, "verification": {"passed": True, "count_before": len(rows), "count_after": len(rows)}}


class CompanyReaderTest(unittest.TestCase):
    def test_no_implicit_period_or_query(self):
        client = FakeClient()
        with self.assertRaises(ValueError):
            load_company_payload(client, {"type": "weekly"})
        self.assertEqual(client.calls, [])

    def test_sql_is_bounded_and_has_no_private_tables(self):
        sqls = build_query_plan(PERIOD, ["O'Reilly"], ["示例市"])
        for sql in sqls.values():
            self.assertIn("ORDER BY id", sql)
            self.assertIn("O''Reilly", sql)
            self.assertNotIn("operator_brand", sql)
            self.assertNotIn("LIMIT", sql)

    def test_ratios_use_sums_and_averages_use_calendar_days(self):
        a, b = daily("2026-10-06", 1), daily("2026-10-07", 2)
        a.update(response_count=90, passenger_order_count=100)
        b.update(response_count=1, passenger_order_count=10)
        value, _ = load_company_payload(FakeClient([a, b]), PERIOD)
        current = value["rows"][0]["current"]
        self.assertAlmostEqual(current["response_rate"], 91 / 110)
        self.assertEqual(current["response_count"], 45.5)
        self.assertEqual(value["coverage"]["current"]["expected_days"], 2)

    def test_missing_days_duplicate_and_null_core_block(self):
        a, b = daily("2026-10-06", 1), daily("2026-10-07", 2)
        for rows in ([a], [a, a, b], [{**a, "completed_order_count": None}, b]):
            value, _ = load_company_payload(FakeClient(rows), PERIOD)
            self.assertEqual(value["source_health"]["status"], "batch_blocked")
            self.assertEqual(analyze_payload(value, profile="partner_basic", min_current_completed=0)["results"], [])

    def test_auxiliary_null_degrades_only_dependencies(self):
        a, b = daily("2026-10-06", 1), daily("2026-10-07", 2)
        a["online_duration_hour"] = None
        value, _ = load_company_payload(FakeClient([a, b]), PERIOD)
        row = value["rows"][0]
        self.assertEqual(row["source_health"]["status"], "partial")
        self.assertEqual(row["current"]["completed_order_count"], 10)
        self.assertIsNone(row["current"]["completion_per_hour"])

    def test_incomplete_query_is_rejected(self):
        class Incomplete(FakeClient):
            def execute_full_audited(self, sql, **kwargs):
                result = super().execute_full_audited(sql, **kwargs)
                result["is_complete"] = False
                return result
        with self.assertRaises(ValueError):
            load_company_payload(Incomplete(), PERIOD)


if __name__ == "__main__":
    unittest.main()
