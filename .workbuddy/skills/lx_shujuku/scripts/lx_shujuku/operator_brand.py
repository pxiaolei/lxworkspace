"""operator_brand 业务接口与码表兼容结构。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


OPENING_STATUS_KEYS = ("is_opening", "opening_status", "开城状态", "是否开城", "是否开城中")
OPEN_CITY_DATE_KEYS = ("open_city_date", "开城日期")


def normalize_operator_brand_row(row: dict[str, Any]) -> dict[str, str]:
    """把公司库字段统一成稳定的中英文键。"""
    operator = _first_non_empty(row, ("operator_entity", "operator", "运营主体"))
    brand = _first_non_empty(row, ("brand_name", "brand", "品牌"))
    city = _first_non_empty(row, ("city_name", "city", "城市"))
    contact_person = _first_non_empty(row, ("contact_person", "person", "对接人"))
    result = {
        "operator": operator,
        "brand": brand,
        "city": city,
        "contact_person": contact_person,
        "运营主体": operator,
        "品牌": brand,
        "城市": city,
        "对接人": contact_person,
    }
    if _has_any_key(row, OPENING_STATUS_KEYS):
        is_opening = _first_non_empty(row, OPENING_STATUS_KEYS)
        result["is_opening"] = is_opening
        result["开城状态"] = is_opening
    if _has_any_key(row, OPEN_CITY_DATE_KEYS):
        open_city_date = _first_non_empty(row, OPEN_CITY_DATE_KEYS)
        result["open_city_date"] = open_city_date
        result["开城日期"] = open_city_date
    return result


def normalize_operator_brand_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """批量规范化 operator_brand 查询结果。"""
    result = []
    for row in rows:
        normalized = normalize_operator_brand_row(row)
        if normalized["operator"] and normalized["city"]:
            result.append(normalized)
    return result


def build_mabiao_mapping(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """构建兼容 lxx_share.excel_utils.load_mabiao 返回值的映射。"""
    city_to_zhuti: dict[str, set[str]] = defaultdict(set)
    brand_to_zhuti: dict[str, set[str]] = defaultdict(set)
    brand_city_to_zhuti: dict[tuple[str, str], set[str]] = defaultdict(set)
    zhuti_to_person: dict[str, set[str]] = defaultdict(set)
    operator_brand_rows: list[dict[str, str]] = []

    all_zhuti: set[str] = set()
    all_cities: set[str] = set()
    all_brands: set[str] = set()
    all_persons: set[str] = set()

    for row in normalize_operator_brand_rows(rows):
        operator = row["operator"]
        brand = row["brand"]
        city = row["city"]
        person = row["contact_person"]
        row_payload = {
            "operator": operator,
            "brand": brand,
            "city": city,
            "contact_person": person,
        }
        if "is_opening" in row:
            row_payload["is_opening"] = row["is_opening"]
        if "open_city_date" in row:
            row_payload["open_city_date"] = row["open_city_date"]
        operator_brand_rows.append(row_payload)

        city_to_zhuti[city].add(operator)
        all_cities.add(city)
        all_zhuti.add(operator)

        if brand:
            brand_to_zhuti[brand].add(operator)
            brand_city_to_zhuti[(brand, city)].add(operator)
            all_brands.add(brand)
        if person:
            zhuti_to_person[operator].add(person)
            all_persons.add(person)

    return {
        "city_to_zhuti": {k: sorted(v) for k, v in city_to_zhuti.items()},
        "brand_to_zhuti": {k: sorted(v) for k, v in brand_to_zhuti.items()},
        "brand_city_to_zhuti": {
            k: sorted(v) for k, v in brand_city_to_zhuti.items()
        },
        "zhuti_to_person": {k: sorted(v) for k, v in zhuti_to_person.items()},
        "all_zhuti": sorted(all_zhuti),
        "all_cities": sorted(all_cities),
        "all_brands": sorted(all_brands),
        "all_persons": sorted(all_persons),
        "operator_brand_rows": operator_brand_rows,
    }


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _has_any_key(row: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return any(key in row for key in keys)


def _first_non_empty(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        if key not in row:
            continue
        cleaned = _clean(row.get(key))
        if cleaned:
            return cleaned
    return ""
