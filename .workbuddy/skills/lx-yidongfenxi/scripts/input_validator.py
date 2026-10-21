#!/usr/bin/env python3
"""Input validation and metric semantics for lx-yidongfenxi."""

from __future__ import annotations

import math
from typing import Any


SCHEMA_VERSION = "2.1"
RULESET_VERSION = "2026-10-n072"
RATE_CONFLICT_TOLERANCE = 0.005
EXPECTED_GRAIN = ["operator", "brand", "city"]
ALLOWED_METRIC_UNITS = {"daily_average", "daily_value", "period_total"}
ALLOWED_BATCH_HEALTH_STATUSES = {"healthy", "partial", "batch_blocked"}
ALLOWED_ROW_HEALTH_STATUSES = {"healthy", "partial", "row_blocked"}
ALLOWED_METRIC_HEALTH_STATUSES = {"healthy", "unreliable"}

CORE_METRICS = {"completed_order_count"}

DERIVED_METRICS = {
    "match_rate": ("match_count", "passenger_order_count"),
    "response_rate": ("response_count", "passenger_order_count"),
    "response_match_rate": ("response_count", "match_count"),
    "completion_response_rate": ("completed_order_count", "response_count"),
    "transaction_rate": ("completed_order_count", "passenger_order_count"),
    "completion_per_hour": ("completed_order_count", "online_duration_hour"),
    "per_capita_online_duration_hour": ("online_duration_hour", "online_driver_count"),
    "per_capita_completion_count": ("completed_order_count", "completion_driver_count"),
}

ATTRIBUTION_METRICS = (
    "passenger_order_count",
    "match_count",
    "response_count",
    "online_driver_count",
    "online_duration_hour",
    "completion_driver_count",
    "first_completion_driver_count",
    "merchant_subsidy_rate",
    "card_sales_rate",
    "merchant_subsidy_amount",
    "merchant_coupon_sales_revenue",
    "coupon_verification_order_count",
    "activity_completed_order_count",
    "online_margin",
    "gongbu_hours",
    "driver_cancel_rate",
    "passenger_cancel_rate",
    "driver_passenger_cancel_rate",
    *DERIVED_METRICS.keys(),
)

# The partner contract deliberately excludes private activity/evidence metrics.
PARTNER_ATTRIBUTION_METRICS = (
    "passenger_order_count", "match_count", "response_count",
    "online_driver_count", "online_duration_hour", "completion_driver_count",
    "first_completion_driver_count", "merchant_subsidy_rate", "card_sales_rate",
    "merchant_subsidy_amount", "merchant_coupon_sales_revenue",
    "driver_cancel_rate", "passenger_cancel_rate", "driver_passenger_cancel_rate",
    *DERIVED_METRICS,
)


def validate_metric_set(
    metrics: dict[str, Any],
    *,
    period: str,
    source_index: int,
    upstream_null_as_zero: bool = False,
    attribution_metrics: tuple[str, ...] = ATTRIBUTION_METRICS,
) -> tuple[dict[str, float], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    raw = metrics if isinstance(metrics, dict) else {}
    values: dict[str, float] = {}
    statuses: dict[str, dict[str, Any]] = {}
    gaps: list[dict[str, Any]] = []

    expected = set(raw) | set(attribution_metrics) | CORE_METRICS
    base_metrics = expected - set(DERIVED_METRICS)
    for metric in sorted(base_metrics):
        status = _parse_input_metric(raw, metric, upstream_null_as_zero=upstream_null_as_zero)
        statuses[metric] = status
        if status["status"] == "present":
            values[metric] = status["value"]
        elif status["status"] == "invalid":
            gaps.append(_metric_gap(
                source_index,
                period,
                metric,
                "invalid_metric_value",
                raw_value=raw.get(metric),
            ))

    for metric, (numerator, denominator) in DERIVED_METRICS.items():
        status, gap = _validate_ratio(
            raw,
            metric,
            numerator,
            denominator,
            statuses,
            source_index=source_index,
            period=period,
            upstream_null_as_zero=upstream_null_as_zero,
        )
        statuses[metric] = status
        if status["status"] in {"present", "derived"}:
            values[metric] = status["value"]
        if gap:
            gaps.append(gap)

    return values, statuses, gaps


def missing_evidence(
    current_status: dict[str, dict[str, Any]],
    compare_status: dict[str, dict[str, Any]],
    attribution_metrics: tuple[str, ...] = ATTRIBUTION_METRICS,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for period, statuses in (("current", current_status), ("compare", compare_status)):
        for metric in attribution_metrics:
            status = statuses.get(metric, {"status": "missing", "value": None})
            if status["status"] in {"present", "derived"}:
                continue
            key = (period, metric)
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "period": period,
                "metric": metric,
                "status": status["status"],
                "reason": status.get("reason") or status["status"],
            })
    return items


def validate_source_health(source_health: Any, *, level: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if source_health is None:
        return None, []
    if not isinstance(source_health, dict):
        return None, [_health_contract_gap(level, source_health)]

    allowed = ALLOWED_BATCH_HEALTH_STATUSES if level == "batch" else ALLOWED_ROW_HEALTH_STATUSES
    status = source_health.get("status")
    if status not in allowed:
        return None, [_health_contract_gap(level, source_health)]
    issues = source_health.get("issues", [])
    if not isinstance(issues, list) or any(not isinstance(item, dict) for item in issues):
        return None, [_health_contract_gap(level, source_health)]
    if status == "healthy" and issues:
        return None, [_health_contract_gap(level, source_health)]
    if any(not _clean_optional_text(item.get("reason")) for item in issues):
        return None, [_health_contract_gap(level, source_health)]

    normalized = {
        "status": status,
        "checker": _clean_optional_text(source_health.get("checker")),
        "checked_at": _clean_optional_text(source_health.get("checked_at")),
        "report_ref": _clean_optional_text(source_health.get("report_ref")),
        "issues": issues,
    }
    if level == "row":
        metrics, metric_gaps = _validate_health_metrics(source_health.get("metrics"))
        if metric_gaps:
            return None, metric_gaps
        normalized["metrics"] = metrics
    return normalized, []


def apply_metric_health(
    values: dict[str, float],
    statuses: dict[str, dict[str, Any]],
    metric_health: dict[str, dict[str, Any]],
    *,
    period: str,
    source_index: int,
) -> list[dict[str, Any]]:
    gaps = []
    unreliable_metrics = {
        metric
        for metric, health in metric_health.items()
        if health["status"] == "unreliable"
    }
    dependent_health: dict[str, dict[str, Any]] = {}
    for metric, (numerator, denominator) in DERIVED_METRICS.items():
        dependencies = sorted({numerator, denominator} & unreliable_metrics)
        if dependencies and metric not in metric_health:
            dependent_health[metric] = {
                "status": "unreliable",
                "reason": "unreliable_dependency",
                "message": f"依赖指标不可信: {', '.join(dependencies)}",
                "report_ref": "",
            }
    metric_health = {**metric_health, **dependent_health}
    for metric, health in metric_health.items():
        if health["status"] == "healthy":
            continue
        values.pop(metric, None)
        status = {
            "value": None,
            "status": "unreliable",
            "source": "source_health",
            "reason": health["reason"],
        }
        if health.get("message"):
            status["message"] = health["message"]
        if health.get("report_ref"):
            status["report_ref"] = health["report_ref"]
        statuses[metric] = status
        gaps.append({
            "source_index": source_index,
            "period": period,
            "metric": metric,
            "reason": health["reason"],
            "message": health.get("message", ""),
            "report_ref": health.get("report_ref", ""),
            "severity": "blocking" if metric in CORE_METRICS else "warning",
            "suggested_check_skill": "lx-shujujiankang",
        })
    return gaps


def health_issue_gap(
    issue: dict[str, Any],
    *,
    source_index: int | None,
    brand: str = "",
    city: str = "",
    severity: str,
    report_ref: str = "",
) -> dict[str, Any]:
    return {
        "source_index": source_index,
        "brand": brand,
        "city": city,
        "reason": _clean_optional_text(issue.get("reason")) or "source_health_issue",
        "message": _clean_optional_text(issue.get("message")),
        "report_ref": _clean_optional_text(issue.get("report_ref")) or report_ref,
        "severity": severity,
        "suggested_check_skill": "lx-shujujiankang",
    }


def validate_payload_metadata(payload: dict[str, Any], *, expected_grain: list[str] = EXPECTED_GRAIN) -> list[dict[str, Any]]:
    gaps = []
    for field in ("grain", "metric_unit", "aggregation", "coverage"):
        if payload.get(field) in (None, "", [], {}):
            gaps.append({
                "source_index": None,
                "reason": "missing_input_metadata",
                "field": field,
                "severity": "warning",
            })
    grain = payload.get("grain")
    if grain not in (None, "", [], {}) and grain != expected_grain:
        gaps.append(_metadata_gap("grain", "invalid_input_metadata", expected=expected_grain, actual=grain))
    metric_unit = payload.get("metric_unit")
    if metric_unit not in (None, "", [], {}) and metric_unit not in ALLOWED_METRIC_UNITS:
        gaps.append(_metadata_gap(
            "metric_unit",
            "invalid_input_metadata",
            expected=sorted(ALLOWED_METRIC_UNITS),
            actual=metric_unit,
        ))
    aggregation = payload.get("aggregation")
    if aggregation not in (None, "", [], {}) and not isinstance(aggregation, dict):
        gaps.append(_metadata_gap("aggregation", "invalid_input_metadata", expected="object", actual=aggregation))
    coverage = payload.get("coverage")
    if coverage not in (None, "", [], {}) and not isinstance(coverage, dict):
        gaps.append(_metadata_gap("coverage", "invalid_input_metadata", expected="object", actual=coverage))
        return gaps
    if isinstance(coverage, dict):
        if not isinstance(coverage.get("source_null_as_zero"), bool):
            gaps.append(_metadata_gap(
                "coverage.source_null_as_zero",
                "invalid_input_metadata",
                expected="boolean",
                actual=coverage.get("source_null_as_zero"),
            ))
        for period in ("current", "compare"):
            period_coverage = coverage.get(period)
            if not isinstance(period_coverage, dict) or not period_coverage:
                gaps.append(_metadata_gap(
                    f"coverage.{period}",
                    "invalid_input_metadata",
                    expected="non-empty object",
                    actual=period_coverage,
                ))
    if isinstance(coverage, dict) and coverage.get("source_null_as_zero") is True:
        gaps.append({
            "source_index": None,
            "reason": "upstream_null_coerced_to_zero",
            "field": "coverage.source_null_as_zero",
            "severity": "warning",
        })
    return gaps


def _metadata_gap(field: str, reason: str, **details: Any) -> dict[str, Any]:
    return {
        "source_index": None,
        "reason": reason,
        "field": field,
        "severity": "warning",
        **details,
    }


def _validate_health_metrics(value: Any) -> tuple[dict[str, dict[str, dict[str, Any]]], list[dict[str, Any]]]:
    if value in (None, {}):
        return {"current": {}, "compare": {}}, []
    if not isinstance(value, dict):
        return {}, [_health_contract_gap("row.metrics", value)]

    normalized: dict[str, dict[str, dict[str, Any]]] = {"current": {}, "compare": {}}
    for period in ("current", "compare"):
        period_metrics = value.get(period, {})
        if not isinstance(period_metrics, dict):
            return {}, [_health_contract_gap(f"row.metrics.{period}", period_metrics)]
        for metric, health in period_metrics.items():
            if not isinstance(health, dict) or health.get("status") not in ALLOWED_METRIC_HEALTH_STATUSES:
                return {}, [_health_contract_gap(f"row.metrics.{period}.{metric}", health)]
            status = health["status"]
            reason = _clean_optional_text(health.get("reason"))
            if status == "unreliable" and not reason:
                return {}, [_health_contract_gap(f"row.metrics.{period}.{metric}", health)]
            normalized[period][str(metric)] = {
                "status": status,
                "reason": reason or "healthy",
                "message": _clean_optional_text(health.get("message")),
                "report_ref": _clean_optional_text(health.get("report_ref")),
            }
    return normalized, []


def _health_contract_gap(level: str, actual: Any) -> dict[str, Any]:
    return {
        "source_index": None,
        "reason": "invalid_source_health",
        "field": f"source_health.{level}",
        "expected": "valid source_health contract",
        "actual": actual,
        "severity": "blocking",
        "suggested_check_skill": "lx-shujujiankang",
    }


def _clean_optional_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _parse_input_metric(
    raw: dict[str, Any],
    metric: str,
    *,
    upstream_null_as_zero: bool = False,
) -> dict[str, Any]:
    if metric not in raw or raw.get(metric) in (None, "", "/"):
        return {"value": None, "status": "missing", "source": "input"}
    value = _finite_float(raw.get(metric))
    if value is None:
        return {
            "value": None,
            "status": "invalid",
            "source": "input",
            "reason": "not_a_finite_number",
        }
    if metric == "completed_order_count" and value < 0:
        return {"value": None, "status": "invalid", "source": "input", "reason": "negative_completed_count"}
    if upstream_null_as_zero and value == 0:
        return {
            "value": None,
            "status": "missing",
            "source": "input",
            "reason": "ambiguous_upstream_zero",
        }
    return {"value": value, "status": "present", "source": "input"}


def _validate_ratio(
    raw: dict[str, Any],
    metric: str,
    numerator: str,
    denominator: str,
    statuses: dict[str, dict[str, Any]],
    *,
    source_index: int,
    period: str,
    upstream_null_as_zero: bool,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    input_status = _parse_input_metric(
        raw,
        metric,
        upstream_null_as_zero=upstream_null_as_zero,
    )
    numerator_status = statuses.get(numerator, {"status": "missing", "value": None})
    denominator_status = statuses.get(denominator, {"status": "missing", "value": None})
    bases_present = (
        numerator_status["status"] == "present"
        and denominator_status["status"] == "present"
    )

    if not bases_present:
        if input_status["status"] == "present":
            return input_status, None
        if input_status["status"] == "invalid":
            return input_status, _metric_gap(
                source_index,
                period,
                metric,
                "invalid_metric_value",
                raw_value=raw.get(metric),
            )
        if input_status.get("reason") == "ambiguous_upstream_zero":
            return input_status, None
        return {
            "value": None,
            "status": "missing",
            "source": "derived",
            "reason": "missing_numerator_or_denominator",
            "numerator": numerator,
            "denominator": denominator,
        }, None

    denominator_value = denominator_status["value"]
    if denominator_value == 0:
        status = {
            "value": None,
            "status": "undefined",
            "source": "derived",
            "reason": "zero_denominator",
            "numerator": numerator,
            "denominator": denominator,
        }
        return status, _metric_gap(
            source_index,
            period,
            metric,
            "zero_denominator",
            denominator_metric=denominator,
        )

    derived_value = numerator_status["value"] / denominator_value
    if input_status["status"] == "invalid":
        return {
            "value": derived_value,
            "status": "derived",
            "source": "derived_after_invalid_input",
            "input_status": "invalid",
            "input_value": None,
            "numerator": numerator,
            "denominator": denominator,
        }, _metric_gap(
            source_index,
            period,
            metric,
            "invalid_metric_value",
            raw_value=raw.get(metric),
        )
    if input_status["status"] == "present" and abs(input_status["value"] - derived_value) > RATE_CONFLICT_TOLERANCE:
        status = {
            "value": None,
            "status": "conflict",
            "source": "input_vs_derived",
            "reason": "input_rate_differs_from_counts",
            "input_value": input_status["value"],
            "derived_value": derived_value,
            "tolerance": RATE_CONFLICT_TOLERANCE,
        }
        return status, _metric_gap(
            source_index,
            period,
            metric,
            "metric_conflict",
            input_value=input_status["value"],
            derived_value=derived_value,
            tolerance=RATE_CONFLICT_TOLERANCE,
        )
    return {
        "value": derived_value,
        "status": "derived",
        "source": "derived",
        "numerator": numerator,
        "denominator": denominator,
    }, None


def _metric_gap(
    source_index: int,
    period: str,
    metric: str,
    reason: str,
    **details: Any,
) -> dict[str, Any]:
    return {
        "source_index": source_index,
        "period": period,
        "metric": metric,
        "reason": reason,
        "severity": "warning",
        **details,
    }


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        text = str(value).strip().replace(",", "")
        result = float(text[:-1]) / 100 if text.endswith("%") else float(text)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None
