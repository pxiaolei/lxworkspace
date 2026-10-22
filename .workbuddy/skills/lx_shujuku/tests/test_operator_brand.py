import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from lx_shujuku.client import DataReportingClient
from lx_shujuku.operator_brand import build_mabiao_mapping


class OperatorBrandMappingTests(unittest.TestCase):
    def test_build_mapping_keeps_row_level_operator_brand_records(self):
        mapping = build_mabiao_mapping([
            {
                "operator_entity": "幸福千万家",
                "brand_name": "幸福千万家",
                "city_name": "福州市",
                "contact_person": "雷维亮",
            },
            {
                "operator_entity": "幸福千万家",
                "brand_name": "幸福千万家",
                "city_name": "太原市",
                "contact_person": "薛银",
            },
        ])

        self.assertEqual(mapping["operator_brand_rows"], [
            {
                "operator": "幸福千万家",
                "brand": "幸福千万家",
                "city": "福州市",
                "contact_person": "雷维亮",
            },
            {
                "operator": "幸福千万家",
                "brand": "幸福千万家",
                "city": "太原市",
                "contact_person": "薛银",
            },
        ])

    def test_build_mapping_preserves_opening_status_fields(self):
        mapping = build_mabiao_mapping([
            {
                "operator_entity": "幸福千万家",
                "brand_name": "幸福千万家",
                "city_name": "福州市",
                "contact_person": "雷维亮",
                "is_opening": 0,
                "open_city_date": "2026-09-01T00:00:00",
            },
        ])

        self.assertEqual(mapping["operator_brand_rows"], [
            {
                "operator": "幸福千万家",
                "brand": "幸福千万家",
                "city": "福州市",
                "contact_person": "雷维亮",
                "is_opening": "0",
                "open_city_date": "2026-09-01T00:00:00",
            },
        ])

    def test_get_operator_brands_selects_opening_fields(self):
        client = DataReportingClient(
            config_path="/tmp/missing-fog-config.yaml",
            base_url="http://example.invalid",
            username="user",
            password="password",
            default_limit=1,
            max_limit=10,
        )
        queries = []

        def fake_execute(sql):
            queries.append(sql)
            return []

        client.execute = fake_execute
        client.get_operator_brands(limit=1)

        self.assertIn("is_opening", queries[0])
        self.assertIn("open_city_date", queries[0])


if __name__ == "__main__":
    unittest.main()
