#!/usr/bin/env python3
"""Partner input from the company query client; no RDS or private evidence imports."""
from __future__ import annotations

from datetime import date, timedelta
import math
from typing import Any

from input_validator import DERIVED_METRICS

VOLUME_FIELDS = (
    "completed_order_count", "passenger_order_count", "match_count", "response_count",
    "online_driver_count", "online_duration_hour", "completion_driver_count",
    "first_completion_driver_count", "merchant_subsidy_amount", "merchant_coupon_sales_revenue",
)
SOURCE_FIELDS = (*VOLUME_FIELDS, "gmv", "driver_cancel_count_after_response", "passenger_cancel_count_after_response")
EXTRA_RATIOS = {
    "merchant_subsidy_rate": (("merchant_subsidy_amount",), "gmv"),
    "card_sales_rate": (("merchant_coupon_sales_revenue",), "gmv"),
    "driver_cancel_rate": (("driver_cancel_count_after_response",), "response_count"),
    "passenger_cancel_rate": (("passenger_cancel_count_after_response",), "response_count"),
    "driver_passenger_cancel_rate": (("driver_cancel_count_after_response", "passenger_cancel_count_after_response"), "response_count"),
}


def validate_periods(period: dict[str, Any]) -> dict[str, Any]:
    """Require both inclusive date ranges; never infer yesterday or comparison dates."""
    parsed = {}
    for name in ("start", "end", "compare_start", "compare_end"):
        value = period.get(name)
        if not isinstance(value, str):
            raise ValueError(f"请明确 {name} 日期（YYYY-MM-DD）")
        try:
            parsed[name] = date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{name} 日期无效") from None
        if parsed[name].isoformat() != value:
            raise ValueError(f"{name} 必须为 YYYY-MM-DD")
    kind = period.get("type")
    if kind not in {"daily", "weekly", "range"}:
        raise ValueError("请明确周期类型 daily / weekly / range")
    for prefix in ("", "compare_"):
        days = (parsed[prefix + "end"] - parsed[prefix + "start"]).days + 1
        if days <= 0 or (kind == "daily" and days != 1) or (kind == "weekly" and days != 7):
            raise ValueError("日期顺序或天数与周期类型不符")
        parsed[prefix + "days"] = days
    return parsed


def _literal(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError("品牌城市筛选必须为非空文本")
    # Reject backslash: MySQL escaping mode cannot be assumed by the client.
    if "\\" in value:
        raise ValueError("筛选文本不能包含反斜杠")
    return "'" + value.replace("'", "''") + "'"


def query_sql(start: date, end: date, brands: list[str], cities: list[str]) -> str:
    where = [f"date_day >= '{start.isoformat()}'", f"date_day < '{(end + timedelta(days=1)).isoformat()}'"]
    for field, values in (("brand_name", brands), ("city_name", cities)):
        if values:
            where.append(f"{field} IN ({', '.join(_literal(v) for v in values)})")
    fields = ", ".join(("id", "date_day", "brand_name", "city_name", *SOURCE_FIELDS))
    return f"SELECT {fields} FROM honghu_detail_data WHERE {' AND '.join(where)} ORDER BY id"


def build_query_plan(period: dict[str, Any], brands: list[str] | None = None, cities: list[str] | None = None) -> dict[str, str]:
    parsed = validate_periods(period)
    return {name: query_sql(parsed[prefix + "start"], parsed[prefix + "end"], brands or [], cities or [])
            for name, prefix in (("current", ""), ("compare", "compare_"))}


def _complete_rows(package: dict[str, Any]) -> list[dict[str, Any]]:
    verification = package.get("verification") or {}
    rows = package.get("rows")
    if (package.get("is_complete") is not True or package.get("possible_truncation") is not False
            or verification.get("passed") is not True or not isinstance(rows, list)
            or package.get("row_count") != len(rows) or package.get("total_rows") != len(rows)
            or verification.get("count_before") != len(rows) or verification.get("count_after") != len(rows)):
        raise ValueError("公司查询未通过完整性证明，本次不分析")
    return rows


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _aggregate(rows: list[dict[str, Any]], start: date, end: date, period_name: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], bool]:
    expected = {(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)}
    seen = [str(row.get("date_day", "")) for row in rows]
    issues = []
    blocked = False
    if len(set(seen)) != len(seen):
        issues.append({"reason": "duplicate_daily_grain", "period": period_name})
        blocked = True
    if set(seen) != expected:
        issues.append({"reason": "incomplete_days", "period": period_name, "expected_days": len(expected), "actual_days": len(set(seen)), "missing_days": sorted(expected - set(seen))})
        blocked = True
    totals = {}
    health = {}
    for field in SOURCE_FIELDS:
        values = [_number(row.get(field)) for row in rows]
        totals[field] = sum(values) if values and all(v is not None for v in values) else None
        if totals[field] is None:
            health[field] = {"status": "unreliable", "reason": "source_metric_missing_or_invalid"}
            issues.append({"reason": "source_metric_missing_or_invalid", "period": period_name, "metric": field})
            if field == "completed_order_count":
                blocked = True
    metrics = {field: None if totals[field] is None else totals[field] / len(expected) for field in VOLUME_FIELDS}
    ratios = {**{key: ((num,), den) for key, (num, den) in DERIVED_METRICS.items()}, **EXTRA_RATIOS}
    for metric, (numerators, denominator) in ratios.items():
        dependencies = (*numerators, denominator)
        if any(totals.get(field) is None for field in dependencies):
            metrics[metric] = None
            health[metric] = {"status": "unreliable", "reason": "unreliable_dependency"}
        else:
            den = totals[denominator]
            metrics[metric] = sum(totals[field] for field in numerators) / den if den else None
    # Extra source columns are provenance, not additional enabled attribution metrics.
    allowed = set(metrics)
    return metrics, {k: v for k, v in health.items() if k in allowed}, issues, blocked


def load_company_payload(client: Any, period: dict[str, Any], *, brands: list[str] | None = None,
                         cities: list[str] | None = None, max_rows: int = 10000) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed = validate_periods(period)
    sqls = build_query_plan(period, brands, cities)
    packages = {name: client.execute_full_audited(sql, question="伙伴基础异动分析", max_rows=max_rows)
                for name, sql in sqls.items()}
    groups: dict[str, dict[tuple[str, str], list[dict[str, Any]]]] = {"current": {}, "compare": {}}
    for name, package in packages.items():
        prefix = "" if name == "current" else "compare_"
        for row in _complete_rows(package):
            if not isinstance(row, dict):
                raise ValueError("公司查询返回了无效数据行")
            brand, city = row.get("brand_name"), row.get("city_name")
            if not isinstance(brand, str) or not brand.strip() or not isinstance(city, str) or not city.strip():
                raise ValueError("公司查询缺少品牌城市标识")
            day = str(row.get("date_day", ""))
            if not parsed[prefix + "start"].isoformat() <= day <= parsed[prefix + "end"].isoformat():
                raise ValueError("查询返回日期超出请求范围")
            if (brands and brand not in brands) or (cities and city not in cities):
                raise ValueError("查询返回范围与筛选不符")
            groups[name].setdefault((brand, city), []).append(row)
    output_rows = []
    for brand, city in sorted(set(groups["current"]) | set(groups["compare"])):
        row = {"brand": brand, "city": city}
        metric_health, issues, blocked = {}, [], False
        for name, prefix in (("current", ""), ("compare", "compare_")):
            metrics, health, gaps, unusable = _aggregate(groups[name].get((brand, city), []), parsed[prefix + "start"], parsed[prefix + "end"], name)
            row[name] = metrics
            metric_health[name] = health
            issues.extend(gaps)
            blocked = blocked or unusable
        row["source_health"] = {"status": "row_blocked" if blocked else "partial" if issues else "healthy", "checker": "lx-yidongfenxi.company_data_reader", "issues": issues, "metrics": metric_health}
        output_rows.append(row)
    blocked_count = sum(r["source_health"]["status"] == "row_blocked" for r in output_rows)
    batch_status = "batch_blocked" if not output_rows or blocked_count == len(output_rows) else "partial" if any(r["source_health"]["status"] != "healthy" for r in output_rows) else "healthy"
    batch_issues = [] if batch_status == "healthy" else [{"reason": "no_usable_rows" if batch_status == "batch_blocked" else "row_quality_issues"}]
    unit = "daily_value" if period["type"] == "daily" else "daily_average"
    payload = {
        "grain": ["brand", "city"], "metric_unit": unit,
        "aggregation": {"volume_metrics": "period_sum_divided_by_calendar_days", "rate_metrics": "period_numerator_sum_divided_by_period_denominator_sum"},
        "coverage": {"source_null_as_zero": False, **{name: {"start": parsed[prefix + "start"].isoformat(), "end": parsed[prefix + "end"].isoformat(), "expected_days": parsed[prefix + "days"]} for name, prefix in (("current", ""), ("compare", "compare_"))}},
        "period": {**period, "metric_unit": unit, "current_days": parsed["days"], "compare_days": parsed["compare_days"]},
        "source_health": {"status": batch_status, "checker": "lx-yidongfenxi.company_data_reader", "issues": batch_issues},
        "source": {"type": "company", "table": "honghu_detail_data", "filters": {"brands": brands or [], "cities": cities or []}, "query_proofs": {name: {key: package.get(key) for key in ("result_sha256", "row_count", "total_rows", "verification", "warnings", "schema_snapshot")} for name, package in packages.items()}},
        "rows": output_rows,
    }
    return payload, packages
