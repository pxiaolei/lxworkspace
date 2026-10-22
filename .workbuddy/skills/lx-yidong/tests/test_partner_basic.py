import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import anomaly_core as core
from input_validator import PARTNER_ATTRIBUTION_METRICS


def payload(current=120, compare=100):
    def metrics(completed):
        result = {key: 1 for key in PARTNER_ATTRIBUTION_METRICS if not key.endswith("rate") and not key.startswith("per_capita") and key != "completion_per_hour"}
        result.update(completed_order_count=completed, passenger_order_count=completed * 2, match_count=completed * 1.5, response_count=completed * 1.2, merchant_subsidy_rate=.1, card_sales_rate=.02, driver_cancel_rate=.01, passenger_cancel_rate=.01, driver_passenger_cancel_rate=.02)
        return result
    return {"grain": ["brand", "city"], "metric_unit": "daily_value", "aggregation": {"volume_metrics": "daily"}, "coverage": {"source_null_as_zero": False, "current": {"expected_days": 1}, "compare": {"expected_days": 1}}, "period": {"type": "daily", "start": "2026-10-07", "end": "2026-10-07", "compare_start": "2026-09-30", "compare_end": "2026-09-30"}, "rows": [{"brand": "拼哒出行", "city": "示例市", "is_opening": False, "open_city_date": "2026-10-07", "current": metrics(current), "compare": metrics(compare)}]}


def analyze(value):
    return core.analyze_payload(value, profile="partner_basic", min_current_completed=0, abs_threshold=20)


class PartnerBasicTest(unittest.TestCase):
    def test_disabled_paths_are_never_called(self):
        names = ("_is_closed", "_is_excluded_brand", "_is_new_open", "_city_index", "_city_evidence_index", "_merge_evidence_context_for_state", "_supporting_evidence_details", "_yunli_agent_details", "_collect_signals", "_evidence_diagnosis_gaps")
        from contextlib import ExitStack
        value = payload()
        value["city_evidence"] = [{"city": "示例市", "weather": "暴雨"}]
        value["rows"][0]["current"]["gongbu_hours"] = 90
        with ExitStack() as stack:
            for name in names:
                stack.enter_context(patch.object(core, name, side_effect=AssertionError(name)))
            result = analyze(value)
        row = result["results"][0]
        self.assertEqual(row["key"], "拼哒出行|示例市")
        self.assertNotIn("operator", row)
        self.assertNotIn("city_market", row["diagnosis_layers"])
        self.assertNotIn("gongbu_hours", json.dumps(result))
        self.assertIn("较对比期", row["smart_analysis"])
        self.assertNotIn("同比", row["smart_analysis"])

    def test_small_team_real_zero_and_zero_baseline(self):
        for current, compare in ((0, 100), (30, 0), (120, 100)):
            self.assertEqual(len(analyze(payload(current, compare))["results"]), 1)
        self.assertEqual(analyze(payload(100, 100))["results"], [])

    def test_null_completed_is_not_zero(self):
        value = payload()
        value["rows"][0]["current"]["completed_order_count"] = None
        self.assertEqual(analyze(value)["results"], [])
        self.assertTrue(analyze(value)["skipped_analyses"])

    def test_zero_thresholds_do_not_classify_unchanged_rows(self):
        for completed in (0, 100):
            for rate_threshold, abs_threshold in ((0, 500), (.05, 0), (0, 0)):
                with self.subTest(completed=completed, rate=rate_threshold, absolute=abs_threshold):
                    result = core.analyze_payload(payload(completed, completed), profile="partner_basic", min_current_completed=0, rate_threshold=rate_threshold, abs_threshold=abs_threshold)
                    self.assertEqual(result["results"], [])

    def test_threshold_contract(self):
        with self.assertRaises(ValueError):
            core.analyze_payload(payload(), profile="partner_basic", abs_threshold=float("nan"))
        with self.assertRaises(ValueError):
            core.analyze_payload(payload(), profile="unknown")

    def test_partner_custom_scale_still_requires_current_period(self):
        result = core.analyze_payload(payload(700, 2000), profile="partner_basic", min_current_completed=1000)
        self.assertEqual(result["results"], [])

    def test_private_default_is_unchanged(self):
        value = payload()
        value["rows"][0]["operator"] = "示例主体"
        first = core.analyze_payload(value)
        second = core.analyze_payload(value, profile="private_full")
        first["meta"].pop("generated_at")
        second["meta"].pop("generated_at")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
