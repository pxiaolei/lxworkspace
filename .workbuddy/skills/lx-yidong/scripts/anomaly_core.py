#!/usr/bin/env python3
"""Deterministic brand-city anomaly analysis for p-fog reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import sys
import math
from typing import Any

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
_SCRIPT_DIR_ADDED = _SCRIPT_DIR not in sys.path
if _SCRIPT_DIR_ADDED:
    sys.path.insert(0, _SCRIPT_DIR)
try:
    from factor_formatter import (
        CITY_MARKET_METRIC,
        YUNLI_AGENT_METRIC,
        format_city_market_text,
        format_change_fragment,
        format_detail_text,
        format_metric_delta,
        format_reason_summary,
        format_weather_periods_text,
        is_default_weather_item,
        number_or_none,
    )
    from input_validator import (
        RULESET_VERSION,
        ATTRIBUTION_METRICS,
        PARTNER_ATTRIBUTION_METRICS,
        SCHEMA_VERSION,
        apply_metric_health,
        health_issue_gap,
        missing_evidence,
        validate_metric_set,
        validate_payload_metadata,
        validate_source_health,
    )
    from summary_writer import write_smart_analysis
finally:
    if _SCRIPT_DIR_ADDED:
        sys.path.remove(_SCRIPT_DIR)


ANOMALY_RATE = 0.05
ANOMALY_ABS = 500.0
MIN_CURRENT_COMPLETED = 1000.0
CITY_MOVEMENT_MIN_COUNT = 2
CITY_MOVEMENT_MIN_RATIO = 0.60
DEFAULT_EXCLUDED_BRANDS = {"拼哒出行", "拼哒优行"}
PINDA_EXCLUSION_START = date(2026, 8, 1)
SUPPLY_RATE_THRESHOLD = 0.05
DEMAND_RATE_THRESHOLD = 0.05
CONVERSION_RATE_POINT_THRESHOLD = 0.01
EFFICIENCY_RATE_POINT_THRESHOLD = 0.03
CANCEL_RATE_POINT_THRESHOLD = 0.05
GONGBU_DAILY_HOUR_THRESHOLD = 3.0
GONGBU_WEEKLY_HOUR_THRESHOLD = 15.0
FIRST_COMPLETION_DRIVER_HIGH_DAILY = 15.0

REASON_LABELS = {
    "demand_up": "发单上涨",
    "demand_down": "发单下降",
    "capacity_up": "运力提升",
    "capacity_down": "运力下降",
    "competitive_opening": "竞品品牌开城",
    "local_event": "本地事件",
    "subsidy_activity_up": "活动力度增加",
    "subsidy_activity_down": "活动力度下降",
    "gongbu_up": "共补增加",
    "gongbu_down": "共补减少",
    "efficiency_up": "效率提升",
    "efficiency_down": "效率下降",
    "city_market_up": "城市大盘",
    "city_market_down": "城市大盘",
    "new_open": "新开城",
    "evidence_insufficient": "证据不足",
}

REASON_PRIORITY = {
    "new_open": 100,
    "demand_up": 82,
    "demand_down": 82,
    "capacity_up": 80,
    "capacity_down": 80,
    "subsidy_activity_up": 78,
    "subsidy_activity_down": 78,
    "gongbu_up": 76,
    "gongbu_down": 76,
    "local_event": 70,
    "efficiency_up": 74,
    "efficiency_down": 74,
    "evidence_insufficient": 0,
}
DIAGNOSIS_LAYER_ORDER = ("demand", "city_market", "capacity", "capacity_drivers", "efficiency")
DIAGNOSIS_LAYER_LABELS = {
    "demand": "发单",
    "city_market": "城市大盘",
    "capacity": "运力",
    "capacity_drivers": "渠道与活动",
    "efficiency": "效率",
}


@dataclass(frozen=True)
class MetricChange:
    current: float
    compare: float
    delta: float
    rate: float | None

    def as_dict(self) -> dict[str, float | None]:
        return {
            "current": self.current,
            "compare": self.compare,
            "delta": self.delta,
            "rate": self.rate,
        }


@dataclass
class RowState:
    source_index: int
    operator: str
    brand: str
    city: str
    contact: str
    open_city_date: str
    is_new_open: bool
    current: dict[str, float]
    compare: dict[str, float]
    current_quality: dict[str, dict[str, Any]]
    compare_quality: dict[str, dict[str, Any]]
    missing_evidence: list[dict[str, Any]]
    completed: MetricChange
    direction: str
    is_major: bool
    health_status: str
    row: dict[str, Any]
    profile: str = "private_full"
    gongbu_daily_average: bool = False
    gongbu_hour_threshold: float | None = None
    selected_compare_dates: bool = False

    @property
    def key(self) -> str:
        if self.profile == "partner_basic":
            return f"{self.brand}|{self.city}"
        return f"{self.operator}|{self.brand}|{self.city}"


def analyze_payload(
    payload: dict[str, Any],
    *,
    include_non_anomalies: bool = False,
    rate_threshold: float = ANOMALY_RATE,
    abs_threshold: float = ANOMALY_ABS,
    min_current_completed: float = MIN_CURRENT_COMPLETED,
    profile: str = "private_full",
) -> dict[str, Any]:
    if profile not in {"private_full", "partner_basic"}:
        raise ValueError("unknown analysis profile")
    partner = profile == "partner_basic"
    if partner and any(not math.isfinite(value) or value < 0 for value in (
        rate_threshold, abs_threshold, min_current_completed,
    )):
        raise ValueError("partner thresholds must be finite and non-negative")
    source_health_provided = "source_health" in payload
    batch_health, batch_health_gaps = validate_source_health(payload.get("source_health"), level="batch")
    batch_blocked = bool(
        source_health_provided
        and (batch_health is None or batch_health["status"] == "batch_blocked")
    )
    period = payload.get("period") or {}
    period_start = _parse_date(period.get("start"))
    period_end = _parse_date(period.get("end"))
    compare_end = _parse_date(period.get("compare_end"))
    period_type = str(period.get("type") or "weekly").lower()
    aggregation = payload.get("aggregation") if isinstance(payload.get("aggregation"), dict) else {}
    gongbu_daily_average = aggregation.get("gongbu_hours") == "daily_average"
    gongbu_hour_threshold = None
    if gongbu_daily_average:
        gongbu_hour_threshold = float(period.get("gongbu_daily_hour_threshold", 2.0))
        if not math.isfinite(gongbu_hour_threshold) or gongbu_hour_threshold <= 0:
            raise ValueError("日均共补门槛必须为有限正数")
    excluded_brands = (
        set(payload["excluded_brands"])
        if "excluded_brands" in payload
        else set(DEFAULT_EXCLUDED_BRANDS)
    )

    source_rows = payload.get("rows") or []
    skipped_analyses: list[dict[str, Any]] = []
    if batch_blocked:
        rows: list[RowState] = []
        scope_exclusions: list[dict[str, Any]] = []
        data_gaps = list(batch_health_gaps)
        issues = batch_health["issues"] if batch_health else [{"reason": "invalid_source_health"}]
        if not issues:
            issues = [{"reason": "batch_source_blocked", "message": "上游标记整批数据不可用于异动分析"}]
        report_ref = batch_health.get("report_ref", "") if batch_health else ""
        for index, raw in enumerate(source_rows if isinstance(source_rows, list) else []):
            raw = raw if isinstance(raw, dict) else {}
            issue = issues[0] if issues else {"reason": "batch_source_blocked"}
            skipped_analyses.append({
                **health_issue_gap(
                    issue,
                    source_index=index,
                    brand=_clean_text(raw.get("brand") or raw.get("brand_name")),
                    city=_clean_text(raw.get("city") or raw.get("city_name")),
                    severity="blocking",
                    report_ref=report_ref,
                ),
                "analysis_status": "skipped",
            })
        if batch_health:
            for issue in issues:
                data_gaps.append(health_issue_gap(
                    issue,
                    source_index=None,
                    severity="blocking",
                    report_ref=report_ref,
                ))
    else:
        rows, scope_exclusions, data_gaps, skipped_analyses = _normalize_rows(
            source_rows,
            period_start=period_start,
            period_end=period_end,
            compare_end=compare_end,
            excluded_brands=excluded_brands,
            rate_threshold=rate_threshold,
            abs_threshold=abs_threshold,
            min_current_completed=min_current_completed,
            upstream_null_as_zero=bool(
                not source_health_provided
                and isinstance(payload.get("coverage"), dict)
                and payload["coverage"].get("source_null_as_zero") is True
            ),
            profile=profile,
        )
        if batch_health and batch_health["status"] == "partial":
            data_gaps.extend(health_issue_gap(
                issue,
                source_index=None,
                severity="warning",
                report_ref=batch_health.get("report_ref", ""),
            ) for issue in batch_health["issues"])
    city_index = {} if partner else _city_index(rows)
    evidence_index = {} if partner else _city_evidence_index(payload.get("city_evidence") or [])
    evidence_context = {} if partner else (payload.get("evidence_context") if isinstance(payload.get("evidence_context"), dict) else {})
    data_gaps.extend(validate_payload_metadata(payload, expected_grain=["brand", "city"] if partner else ["operator", "brand", "city"]))

    results = []
    diagnosis_gaps = []
    for state in rows:
        state.gongbu_daily_average = gongbu_daily_average
        state.gongbu_hour_threshold = gongbu_hour_threshold
        state.selected_compare_dates = bool(period.get("compare_dates"))
        if not state.is_major and not include_non_anomalies:
            continue
        result = _build_partner_result(state, period_type) if partner else _build_result(
            state,
            city_rows=city_index.get(state.city, []),
            city_evidence=_merge_evidence_context_for_state(
                evidence_index.get(state.city, {}),
                evidence_context,
                state,
            ),
            period_type=period_type,
        )
        results.append(result)
        diagnosis_gaps.extend(result.get("diagnosis_gaps") or [])
        if result["primary_reason"] == "evidence_insufficient":
            diagnosis_gaps.append({
                "source_index": state.source_index,
                "key": state.key,
                "reason": "evidence_insufficient",
                "severity": "review",
            })

    missing_evidence_count = sum(len(state.missing_evidence) for state in rows)
    gaps = scope_exclusions + data_gaps + diagnosis_gaps
    health_partial = bool(
        batch_health and batch_health["status"] == "partial"
        or any(state.health_status == "partial" for state in rows)
    )
    analysis_status = "batch_blocked" if batch_blocked else (
        "analyzed_partial"
        if health_partial or data_gaps or diagnosis_gaps or skipped_analyses or missing_evidence_count
        else "analyzed"
    )
    meta = {
        "skill": "lx-yidongfenxi",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "anomaly_rate_threshold": rate_threshold,
        "anomaly_abs_threshold": abs_threshold,
        "min_current_completed": min_current_completed,
        "city_movement_min_count": CITY_MOVEMENT_MIN_COUNT,
        "city_movement_min_ratio": CITY_MOVEMENT_MIN_RATIO,
        "input_row_count": len(source_rows) if isinstance(source_rows, list) else 0,
        "analyzable_row_count": len(rows),
        "result_count": len(results),
        "skipped_analysis_count": len(skipped_analyses),
    }
    if partner:
        meta.pop("city_movement_min_count")
        meta.pop("city_movement_min_ratio")
        meta.update(profile=profile, grain=payload.get("grain"), metric_unit=payload.get("metric_unit"))
        for gap in gaps:
            if "suggested_check_skill" in gap:
                gap["suggested_check_skill"] = "lx_shujuku"
    evidence_summary = _evidence_context_summary(evidence_context)
    if evidence_summary:
        meta["evidence_context"] = evidence_summary
    return {
        "schema_version": SCHEMA_VERSION,
        "ruleset_version": "2026-10-n067-partner" if partner else RULESET_VERSION,
        "analysis_status": analysis_status,
        "meta": meta,
        "period": period,
        "results": results,
        "skipped_analyses": skipped_analyses,
        "scope_exclusions": scope_exclusions,
        "data_gaps": data_gaps,
        "diagnosis_gaps": diagnosis_gaps,
        "data_quality": {
            "status": (
                "complete"
                if not health_partial and not data_gaps and not diagnosis_gaps and missing_evidence_count == 0
                else "partial"
            ),
            "scope_exclusion_count": len(scope_exclusions),
            "data_gap_count": len(data_gaps),
            "diagnosis_gap_count": len(diagnosis_gaps),
            "missing_evidence_count": missing_evidence_count,
        },
        "gaps": gaps,
    }


def _normalize_rows(
    source_rows: list[dict[str, Any]],
    *,
    period_start: date | None,
    period_end: date | None,
    compare_end: date | None,
    excluded_brands: set[str],
    rate_threshold: float,
    abs_threshold: float,
    min_current_completed: float,
    upstream_null_as_zero: bool,
    profile: str = "private_full",
) -> tuple[list[RowState], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[RowState] = []
    scope_exclusions: list[dict[str, Any]] = []
    data_gaps: list[dict[str, Any]] = []
    skipped_analyses: list[dict[str, Any]] = []
    partner = profile == "partner_basic"
    attribution_metrics = PARTNER_ATTRIBUTION_METRICS if partner else ATTRIBUTION_METRICS
    if not isinstance(source_rows, list):
        return rows, scope_exclusions, [{
            "source_index": None,
            "reason": "invalid_rows_container",
            "expected": "array",
            "severity": "blocking",
        }], skipped_analyses
    for index, raw in enumerate(source_rows):
        if not isinstance(raw, dict):
            data_gaps.append({
                "source_index": index,
                "reason": "invalid_input_row",
                "expected": "object",
                "severity": "blocking",
            })
            continue
        operator = _clean_text(raw.get("operator") or raw.get("operator_name") or raw.get("subject"))
        brand = _clean_text(raw.get("brand") or raw.get("brand_name"))
        city = _clean_text(raw.get("city") or raw.get("city_name"))
        missing_identity_fields = [
            field
            for field, value in (("operator", operator), ("brand", brand), ("city", city))
            if not value and (not partner or field != "operator")
        ]
        if missing_identity_fields:
            data_gaps.extend({
                "source_index": index,
                "reason": "missing_identity_field",
                "field": field,
                "severity": "blocking",
            } for field in missing_identity_fields)
            continue
        if not partner and _is_closed(raw):
            scope_exclusions.append({"source_index": index, "brand": brand, "city": city, "reason": "not_opening"})
            continue
        if not partner and _is_excluded_brand(brand, period_start, excluded_brands):
            scope_exclusions.append({"source_index": index, "brand": brand, "city": city, "reason": "excluded_brand"})
            continue

        row_health_provided = "source_health" in raw
        row_health, row_health_gaps = validate_source_health(raw.get("source_health"), level="row")
        if row_health_gaps:
            for gap in row_health_gaps:
                gap.update({"source_index": index, "brand": brand, "city": city})
            data_gaps.extend(row_health_gaps)
            skipped_analyses.append({
                "source_index": index,
                "brand": brand,
                "city": city,
                "analysis_status": "skipped",
                "reason": "invalid_source_health",
                "severity": "blocking",
                "suggested_check_skill": "lx-shujujiankang",
            })
            continue
        if row_health and row_health["status"] == "row_blocked":
            issues = row_health["issues"] or [{"reason": "row_source_blocked"}]
            row_gaps = [health_issue_gap(
                issue,
                source_index=index,
                brand=brand,
                city=city,
                severity="blocking",
                report_ref=row_health.get("report_ref", ""),
            ) for issue in issues]
            data_gaps.extend(row_gaps)
            skipped_analyses.append({**row_gaps[0], "analysis_status": "skipped"})
            continue
        if row_health and row_health["status"] == "partial":
            data_gaps.extend(health_issue_gap(
                issue,
                source_index=index,
                brand=brand,
                city=city,
                severity="warning",
                report_ref=row_health.get("report_ref", ""),
            ) for issue in row_health["issues"])

        open_city_date = "" if partner else _clean_text(raw.get("open_city_date"))
        if open_city_date and _parse_date(open_city_date) is None:
            data_gaps.append({
                "source_index": index,
                "brand": brand,
                "city": city,
                "reason": "invalid_open_city_date",
                "field": "open_city_date",
                "value": open_city_date,
                "severity": "warning",
            })
        is_new_open = False if partner else _is_new_open(open_city_date, period_start, period_end, compare_end)
        allowed_metrics = set(attribution_metrics) | {"completed_order_count"}
        current_input = raw.get("current") or {}
        compare_input = raw.get("compare") or {}
        if partner:
            current_input = {key: value for key, value in current_input.items() if key in allowed_metrics}
            compare_input = {key: value for key, value in compare_input.items() if key in allowed_metrics}
            if row_health:
                for health_period in ("current", "compare"):
                    row_health["metrics"][health_period] = {key: value for key, value in row_health["metrics"][health_period].items() if key in allowed_metrics}
        current, current_quality, current_gaps = validate_metric_set(
            current_input,
            period="current",
            source_index=index,
            upstream_null_as_zero=upstream_null_as_zero and not row_health_provided and not partner,
            attribution_metrics=attribution_metrics,
        )
        compare, compare_quality, compare_gaps = validate_metric_set(
            compare_input,
            period="compare",
            source_index=index,
            upstream_null_as_zero=upstream_null_as_zero and not row_health_provided and not partner,
            attribution_metrics=attribution_metrics,
        )
        if row_health:
            current_gaps.extend(apply_metric_health(
                current,
                current_quality,
                row_health["metrics"]["current"],
                period="current",
                source_index=index,
            ))
            compare_gaps.extend(apply_metric_health(
                compare,
                compare_quality,
                row_health["metrics"]["compare"],
                period="compare",
                source_index=index,
            ))
        if (
            is_new_open
            and compare_quality["completed_order_count"]["status"] != "unreliable"
            and _is_numeric_zero((raw.get("compare") or {}).get("completed_order_count"))
        ):
            compare["completed_order_count"] = 0.0
            compare_quality["completed_order_count"] = {
                "value": 0.0,
                "status": "present",
                "source": "structural_zero_from_open_date",
            }
        data_gaps.extend(current_gaps)
        data_gaps.extend(compare_gaps)
        core_missing = False
        for period_name, quality in (("current", current_quality), ("compare", compare_quality)):
            status = quality["completed_order_count"]["status"]
            if status not in {"present", "derived"}:
                core_missing = True
                data_gaps.append({
                    "source_index": index,
                    "brand": brand,
                    "city": city,
                    "period": period_name,
                    "metric": "completed_order_count",
                    "reason": "core_metric_missing",
                    "status": status,
                    "severity": "blocking",
                })
        if core_missing:
            skipped_analyses.append({
                "source_index": index,
                "brand": brand,
                "city": city,
                "analysis_status": "skipped",
                "reason": "core_metric_unreliable" if any(
                    quality["completed_order_count"]["status"] == "unreliable"
                    for quality in (current_quality, compare_quality)
                ) else "core_metric_missing",
                "metric": "completed_order_count",
                "severity": "blocking",
                "suggested_check_skill": "lx-shujujiankang",
            })
            continue
        completed = _metric_change(current, compare, "completed_order_count")
        if completed is None:
            continue
        direction = _direction(completed.delta)
        completed_scale = completed.current if partner else max(completed.current, completed.compare)
        is_major = (
            completed_scale >= min_current_completed
            and (profile != "partner_basic" or direction != "flat")
            and _is_major_change(completed, rate_threshold, abs_threshold)
        )
        rows.append(
            RowState(
                source_index=index,
                operator=operator,
                brand=brand,
                city=city,
                contact=_clean_text(raw.get("contact") or raw.get("contact_person")),
                open_city_date=open_city_date,
                is_new_open=is_new_open,
                current=current,
                compare=compare,
                current_quality=current_quality,
                compare_quality=compare_quality,
                missing_evidence=missing_evidence(current_quality, compare_quality, attribution_metrics),
                completed=completed,
                direction=direction,
                is_major=is_major,
                health_status=(row_health or {}).get("status", "healthy") if row_health_provided else "not_provided",
                row=raw,
                profile=profile,
            )
        )
    return rows, scope_exclusions, data_gaps, skipped_analyses


def _build_partner_result(state: RowState, period_type: str) -> dict[str, Any]:
    """Build only the basic partner layers, without entering private evidence paths."""
    flow = ("demand", "capacity", "capacity_drivers", "efficiency")
    candidates = (
        _demand_reason_tag(state, {}, enhanced=False),
        _capacity_reason_tag(state, {}, enhanced=False),
        _subsidy_activity_reason_tag(state),
        _efficiency_reason_tag(state),
    ) if state.direction != "flat" else (None,) * 4
    tags = []
    layers = {"flow": list(flow)}
    for layer, candidate in zip(flow, candidates):
        if candidate:
            tag = _tag_with_layer(candidate, layer=layer, causal_role="supporting")
            if layer == "capacity_drivers":
                tag["label"] = "补贴力度增加" if state.direction == "up" else "补贴力度下降"
            for detail in tag.get("details") or []:
                detail["comparison_label"] = "较对比期"
                detail["text"] = format_detail_text(detail)
            tag["summary"] = format_reason_summary(tag["label"], tag.get("details") or [])
            tags.append(tag)
        layers[layer] = _diagnosis_layer(layer, [tags[-1]] if candidate else [], label="补贴力度" if layer == "capacity_drivers" else None)
    if not tags:
        tags = [_tag_with_layer(_reason_tag("evidence_insufficient", "基础指标暂未形成强解释项"), layer="overall", causal_role="unknown")]
    primary = _primary_reason(tags)
    partial = _row_has_quality_issues(state)
    anomaly_type = "up" if state.direction == "up" else "down" if state.direction == "down" else "flat"
    return {
        "key": state.key, "brand": state.brand, "city": state.city,
        "status": "analyzed" if state.is_major else "not_major_anomaly",
        "analysis_status": "analyzed_partial" if partial else "analyzed",
        "direction": state.direction, "anomaly_type": anomaly_type,
        "metrics": {"completed_order_count": state.completed.as_dict()},
        "signals": [{"code": tag["code"], "title": tag["label"], "details": tag.get("details", [])} for tag in tags if tag["code"] != "evidence_insufficient"],
        "primary_reason": primary, "secondary_reasons": _secondary_reasons(tags, primary),
        "reason_tags": tags, "diagnosis_layers": layers,
        "data_quality": {"status": "partial" if partial else "complete", "current": {"metric_status": state.current_quality}, "compare": {"metric_status": state.compare_quality}},
        "missing_evidence": state.missing_evidence, "diagnosis_gaps": [],
        "smart_analysis": write_smart_analysis(state, anomaly_type, {}, tags, layers, period_type),
        "needs_review": True,
    }


def _build_result(
    state: RowState,
    *,
    city_rows: list[RowState],
    city_evidence: dict[str, Any],
    period_type: str,
) -> dict[str, Any]:
    city_movement = _city_movement(state, city_rows)
    row_evidence = _merge_row_and_city_evidence(state.row, city_evidence)
    signals = _collect_signals(state, row_evidence, period_type)
    anomaly_type = _anomaly_type(state, city_movement)
    reason_tags, diagnosis_layers = _build_layered_diagnosis(
        state,
        row_evidence,
        period_type,
        anomaly_type,
        city_movement,
    )
    evidence_diagnosis_gaps = _evidence_diagnosis_gaps(state, row_evidence, city_movement, reason_tags)
    primary_reason = _primary_reason(reason_tags)
    secondary_reasons = _secondary_reasons(reason_tags, primary_reason)
    status = "not_major_anomaly"
    if state.is_major:
        status = "new_open" if state.is_new_open else "analyzed"

    result = {
        "key": state.key,
        "operator": state.operator,
        "brand": state.brand,
        "city": state.city,
        "contact": state.contact,
        "open_city_date": state.open_city_date,
        "status": status,
        "analysis_status": "analyzed_partial" if _row_has_quality_issues(state) or evidence_diagnosis_gaps else "analyzed",
        "direction": state.direction,
        "anomaly_type": anomaly_type,
        "metrics": {
            "completed_order_count": state.completed.as_dict(),
        },
        "city_movement": city_movement,
        "signals": signals,
        "primary_reason": primary_reason,
        "secondary_reasons": secondary_reasons,
        "reason_tags": reason_tags,
        "diagnosis_layers": diagnosis_layers,
        "data_quality": {
            "status": "complete" if not _row_has_quality_issues(state) and not evidence_diagnosis_gaps else "partial",
            "current": {"metric_status": state.current_quality},
            "compare": {"metric_status": state.compare_quality},
        },
        "missing_evidence": state.missing_evidence,
        "diagnosis_gaps": evidence_diagnosis_gaps,
        "smart_analysis": "",
        "needs_review": True,
    }
    result["smart_analysis"] = write_smart_analysis(
        state,
        anomaly_type,
        city_movement,
        reason_tags,
        diagnosis_layers,
        period_type,
    )
    return result


def _collect_signals(state: RowState, evidence: dict[str, Any], period_type: str) -> list[dict[str, Any]]:
    if state.direction == "flat":
        return []

    signals: list[dict[str, Any]] = []
    signals.extend(_external_evidence_signals(evidence, state.direction))
    gongbu = _gongbu_signal(state, period_type)
    if gongbu:
        signals.append(gongbu)

    supply = _metric_signal_group(
        state,
        "supply",
        "供给",
        [
            ("online_duration_hour", "在线时长"),
            ("online_driver_count", "在线司机"),
            ("completion_driver_count", "完单司机"),
            ("per_capita_online_duration_hour", "人均在线时长"),
            ("completion_per_hour", "时均完单"),
            ("per_capita_completion_count", "人均完单"),
        ],
        SUPPLY_RATE_THRESHOLD,
    )
    if supply:
        signals.append(supply)

    demand = _metric_signal_group(
        state,
        "demand",
        "需求",
        [("passenger_order_count", "发单")],
        DEMAND_RATE_THRESHOLD,
    )
    if demand:
        signals.append(demand)

    conversion = _metric_signal_group(
        state,
        "conversion",
        "转化",
        [
            ("match_rate", "匹配率（匹配/发单）"),
            ("response_rate", "应答率（应答/发单）"),
            ("response_match_rate", "应答/匹配率"),
            ("transaction_rate", "成交率（完单/发单）"),
        ],
        EFFICIENCY_RATE_POINT_THRESHOLD,
        rate_metric=True,
    )
    if conversion:
        signals.append(conversion)

    subsidy = _metric_signal_group(
        state,
        "coupon_activity",
        "卡券/活动",
        [
            ("merchant_coupon_sales_revenue", "卡券收入"),
            ("coupon_verification_order_count", "卡券核销订单"),
            ("activity_completed_order_count", "活动订单"),
            ("merchant_subsidy_amount", "商家补贴"),
        ],
        DEMAND_RATE_THRESHOLD,
    )
    if subsidy:
        signals.append(subsidy)

    return signals


def _collect_reason_tags(
    state: RowState,
    evidence: dict[str, Any],
    period_type: str,
    anomaly_type: str,
    city_movement: dict[str, Any],
) -> list[dict[str, Any]]:
    return _build_layered_diagnosis(state, evidence, period_type, anomaly_type, city_movement)[0]


def _build_layered_diagnosis(
    state: RowState,
    evidence: dict[str, Any],
    period_type: str,
    anomaly_type: str,
    city_movement: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if state.direction == "flat":
        tag = _tag_with_layer(
            _reason_tag("evidence_insufficient", "未形成明确上涨/下跌方向"),
            layer="overall",
            causal_role="unknown",
        )
        return [tag], _diagnosis_layers_from_groups({})
    if anomaly_type == "new_open":
        detail = {
            "metric": "open_city_date",
            "label": "开城日期",
            "current": state.open_city_date,
            "compare": "",
            "delta": "",
            "rate": None,
            "text": f"码表开城日期 {state.open_city_date or '未知'}",
        }
        tag = _tag_with_layer(
            _reason_tag("new_open", detail["text"], [detail], strength="strong"),
            layer="new_open",
            causal_role="structural_new_open",
        )
        return [tag], {
            "flow": ["new_open"],
            "new_open": _diagnosis_layer("new_open", [tag], label="新开城"),
        }

    demand_tag = _tag_with_layer(
        _demand_reason_tag(state, evidence),
        layer="demand",
        causal_role="observed_driver",
    )
    city_market_tag = _tag_with_layer(
        _city_market_reason_tag(city_movement)
        if anomaly_type == "city_co_movement"
        and city_movement.get("market_direction") == state.direction
        else None,
        layer="city_market",
        causal_role="context",
    )
    capacity_tag = _tag_with_layer(
        _capacity_reason_tag(state, evidence),
        layer="capacity",
        causal_role="observed_driver",
    )
    capacity_driver_tags = [
        _tag_with_layer(
            _gongbu_reason_tag(state, period_type),
            layer="capacity_drivers",
            causal_role="candidate_explanation",
        ),
        _tag_with_layer(
            _subsidy_activity_reason_tag(state),
            layer="capacity_drivers",
            causal_role="candidate_explanation",
        ),
        _tag_with_layer(
            _competitive_opening_reason_tag(state, evidence),
            layer="capacity_drivers",
            causal_role="competitive_context",
        ),
    ]
    capacity_driver_tags = [tag for tag in capacity_driver_tags if tag]
    efficiency_tag = _tag_with_layer(
        _efficiency_reason_tag(state),
        layer="efficiency",
        causal_role="downstream_indicator" if demand_tag or capacity_tag else "observed_driver",
    )

    groups = {
        "demand": [tag for tag in [demand_tag] if tag],
        "city_market": [tag for tag in [city_market_tag] if tag],
        "capacity": [tag for tag in [capacity_tag] if tag],
        "capacity_drivers": capacity_driver_tags,
        "efficiency": [tag for tag in [efficiency_tag] if tag],
    }
    tags = [tag for layer in DIAGNOSIS_LAYER_ORDER for tag in groups[layer]]
    if not any(tag.get("code") not in {"city_market_up", "city_market_down"} for tag in tags):
        context_tag = _tag_with_layer(
            _local_event_reason_tag(evidence, state.direction),
            layer="capacity_drivers",
            causal_role="context",
        )
        if context_tag:
            groups["capacity_drivers"].append(context_tag)
        insufficient = _tag_with_layer(
            _reason_tag("evidence_insufficient", "已按发单、运力和效率逐层检查，暂未发现单一强解释项"),
            layer="overall",
            causal_role="unknown",
        )
        tags = [insufficient]
        tags.extend(tag for layer in DIAGNOSIS_LAYER_ORDER for tag in groups[layer])

    return tags, _diagnosis_layers_from_groups(groups)


def _tag_with_layer(tag: dict[str, Any] | None, *, layer: str, causal_role: str) -> dict[str, Any] | None:
    if not tag:
        return None
    enriched = dict(tag)
    enriched["analysis_layer"] = layer
    enriched["causal_role"] = causal_role
    return enriched


def _diagnosis_layers_from_groups(groups: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    layers = {"flow": list(DIAGNOSIS_LAYER_ORDER)}
    for layer in DIAGNOSIS_LAYER_ORDER:
        layers[layer] = _diagnosis_layer(layer, groups.get(layer) or [])
    return layers


def _diagnosis_layer(layer: str, tags: list[dict[str, Any]], *, label: str | None = None) -> dict[str, Any]:
    return {
        "code": layer,
        "label": label or DIAGNOSIS_LAYER_LABELS.get(layer, layer),
        "status": "hit" if tags else "no_hit",
        "reason_codes": [tag.get("code") for tag in tags if tag.get("code")],
        "evidence_count": sum(len(tag.get("details") or []) for tag in tags),
    }


def _demand_reason_tag(state: RowState, evidence: dict[str, Any], *, enhanced: bool = True) -> dict[str, Any] | None:
    details = _aligned_metric_details(
        state,
        [("passenger_order_count", "发单")],
        DEMAND_RATE_THRESHOLD,
    )
    if details and enhanced:
        details.extend(_supporting_evidence_details(evidence, state.direction))
    return _metric_reason_tag(f"demand_{state.direction}", details)


def _capacity_reason_tag(state: RowState, evidence: dict[str, Any], *, enhanced: bool = True) -> dict[str, Any] | None:
    details = _aligned_metric_details(
        state,
        [
            ("online_duration_hour", "在线时长"),
            ("online_driver_count", "在线司机"),
            ("completion_driver_count", "完单司机"),
            ("first_completion_driver_count", "首次完单司机数"),
        ],
        SUPPLY_RATE_THRESHOLD,
    )
    details = [
        item for item in details
        if item["metric"] != "first_completion_driver_count" or abs(float(item["delta"])) >= 1.0
    ]
    has_first_driver_detail = any(item["metric"] == "first_completion_driver_count" for item in details)
    first_driver_is_high = (
        bool(state.row.get("first_completion_driver_is_high"))
        or state.current.get("first_completion_driver_count", 0.0) >= FIRST_COMPLETION_DRIVER_HIGH_DAILY
    )
    if state.direction == "up" and first_driver_is_high and not has_first_driver_detail:
        current = state.current.get("first_completion_driver_count")
        current_number = number_or_none(current)
        current_text = format_metric_delta("首次完单司机数", current_number) if current_number else "未知"
        details.append({
            "metric": "first_completion_driver_count",
            "label": "首次完单司机数",
            "current": current,
            "compare": state.compare.get("first_completion_driver_count"),
            "delta": "",
            "rate": None,
            "text": f"首次完单司机数处于高位（本期 {current_text}）",
        })
    if details and enhanced:
        details.extend(_yunli_agent_details(evidence, state.direction))
    return _metric_reason_tag(f"capacity_{state.direction}", details)


def _efficiency_reason_tag(state: RowState) -> dict[str, Any] | None:
    details = _aligned_metric_details(
        state,
        [
            ("response_rate", "应答率"),
            ("transaction_rate", "成交率"),
        ],
        EFFICIENCY_RATE_POINT_THRESHOLD,
        rate_metric=True,
    )
    cancel_detail = _combined_cancel_rate_detail(state)
    if cancel_detail and _is_aligned_change(
        MetricChange(
            current=float(cancel_detail["current"]),
            compare=float(cancel_detail["compare"]),
            delta=float(cancel_detail["delta"]),
            rate=cancel_detail["rate"],
        ),
        state.direction,
        CANCEL_RATE_POINT_THRESHOLD,
        rate_metric=True,
        inverse=True,
    ):
        details.append(cancel_detail)
    return _metric_reason_tag(f"efficiency_{state.direction}", details)


def _combined_cancel_rate_detail(state: RowState) -> dict[str, Any] | None:
    direct_change = _metric_change(state.current, state.compare, "driver_passenger_cancel_rate")
    if direct_change is not None:
        return _metric_detail("driver_passenger_cancel_rate", "司乘取消率", direct_change, rate_metric=True)

    current_driver = state.current.get("driver_cancel_rate")
    current_passenger = state.current.get("passenger_cancel_rate")
    compare_driver = state.compare.get("driver_cancel_rate")
    compare_passenger = state.compare.get("passenger_cancel_rate")
    if None in {current_driver, current_passenger, compare_driver, compare_passenger}:
        return None
    current = float(current_driver) + float(current_passenger)
    compare = float(compare_driver) + float(compare_passenger)
    delta = current - compare
    change = MetricChange(current=current, compare=compare, delta=delta, rate=(delta / compare) if compare else None)
    return _metric_detail("driver_passenger_cancel_rate", "司乘取消率", change, rate_metric=True)


def _subsidy_activity_reason_tag(state: RowState) -> dict[str, Any] | None:
    details = _aligned_metric_details(
        state,
        [
            ("merchant_subsidy_rate", "商家补贴率"),
        ],
        CONVERSION_RATE_POINT_THRESHOLD,
        rate_metric=True,
    )
    return _metric_reason_tag(f"subsidy_activity_{state.direction}", details)


def _gongbu_reason_tag(state: RowState, period_type: str) -> dict[str, Any] | None:
    change = _metric_change(state.current, state.compare, "gongbu_hours")
    if change is None:
        return None
    threshold = state.gongbu_hour_threshold if state.gongbu_hour_threshold is not None else (
        GONGBU_DAILY_HOUR_THRESHOLD if period_type == "daily" else GONGBU_WEEKLY_HOUR_THRESHOLD
    )
    if state.direction == "down" and change.delta > -threshold:
        return None
    if state.direction == "up" and change.delta < threshold:
        return None
    detail = _metric_detail("gongbu_hours", "日均共补小时" if state.gongbu_daily_average else "共补小时", change)
    return _metric_reason_tag(f"gongbu_{state.direction}", [detail])


def _competitive_opening_reason_tag(state: RowState, evidence: dict[str, Any]) -> dict[str, Any] | None:
    if state.direction != "down":
        return None
    openings = evidence.get("competitor_openings") or evidence.get("competitive_openings") or []
    if not isinstance(openings, list):
        return None
    details = []
    for item in openings:
        if not isinstance(item, dict):
            continue
        period = _clean_text(item.get("period"))
        if period and period not in {"current", "本期"}:
            continue
        impact = _clean_text(item.get("impact_direction") or item.get("direction"))
        if impact and impact != "down":
            continue
        competitor_brand = _clean_text(
            item.get("competitor_brand")
            or item.get("brand")
            or item.get("brand_name")
            or item.get("name")
        )
        open_date = _clean_text(item.get("open_city_date") or item.get("date") or item.get("start_date"))
        summary = _clean_text(item.get("summary"))
        text_parts = []
        if competitor_brand:
            text_parts.append(competitor_brand)
        if open_date:
            text_parts.append(f"{open_date}开城")
        if summary and summary not in text_parts:
            text_parts.append(summary)
        text = "，".join(text_parts) or _item_summary(item)
        details.append({
            "metric": "competitive_opening",
            "label": "竞品品牌开城",
            "current": text,
            "compare": "",
            "delta": "",
            "rate": None,
            "text": text,
        })
    if not details:
        return None
    return _reason_tag("competitive_opening", "竞品品牌开城：" + "、".join(item["text"] for item in details[:3]), details)


def _local_event_reason_tag(evidence: dict[str, Any], direction: str) -> dict[str, Any] | None:
    events = _matched_items(evidence.get("local_events") or evidence.get("events") or [], direction)
    details = [_evidence_detail("local_event", "本地事件", item) for item in events[:3]]
    if not details:
        return None
    return _reason_tag("local_event", "本地事件：" + "、".join(item["text"] for item in details), details)


def _supporting_evidence_details(evidence: dict[str, Any], direction: str) -> list[dict[str, Any]]:
    details = []
    weather_detail = _weather_evidence_detail(evidence, direction)
    if weather_detail:
        details.append(weather_detail)
    events = _matched_items(evidence.get("local_events") or evidence.get("events") or [], direction)
    if events:
        details.extend(_evidence_detail("local_event", "本地事件", item) for item in events[:3])
    return details


def _weather_evidence_detail(evidence: dict[str, Any], direction: str) -> dict[str, Any] | None:
    weather_periods = evidence.get("weather_periods") or evidence.get("weather_by_period")
    if isinstance(weather_periods, dict):
        text = format_weather_periods_text(weather_periods)
        if text:
            return {
                "metric": "weather",
                "label": "天气",
                "current": "",
                "compare": "",
                "delta": "",
                "rate": None,
                "text": text,
                "weather_periods": weather_periods,
            }

    weather = [
        item for item in _matched_items(evidence.get("weather") or evidence.get("weather_events") or [], direction)
        if is_default_weather_item(item)
    ]
    if not weather:
        return None
    text = format_weather_periods_text({"current": weather})
    if not text:
        text = "、".join(_strip_leading_date(_item_summary(item)) for item in weather[:3])
    return {
        "metric": "weather",
        "label": "天气",
        "current": "",
        "compare": "",
        "delta": "",
        "rate": None,
        "text": text if text else "天气异常",
    }


def _aligned_metric_details(
    state: RowState,
    metrics: list[tuple[str, str]],
    threshold: float,
    *,
    rate_metric: bool = False,
    inverse: bool = False,
) -> list[dict[str, Any]]:
    details = []
    for metric, label in metrics:
        change = _metric_change(state.current, state.compare, metric)
        if change is None:
            continue
        if not _is_aligned_change(change, state.direction, threshold, rate_metric=rate_metric, inverse=inverse):
            continue
        details.append(_metric_detail(metric, label, change, rate_metric=rate_metric))
    return details


def _metric_reason_tag(code: str, details: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not details:
        return None
    summary = format_reason_summary(REASON_LABELS.get(code, code), details)
    return _reason_tag(code, summary, details)


def _yunli_agent_details(evidence: dict[str, Any], direction: str) -> list[dict[str, Any]]:
    contributions = evidence.get("yunli_agent_contributions")
    if not isinstance(contributions, dict):
        return []
    items = contributions.get(direction)
    if not isinstance(items, list):
        return []
    details = []
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        detail = {
            key: value
            for key, value in item.items()
            if not key.startswith("supplier")
        }
        detail["metric"] = YUNLI_AGENT_METRIC
        detail["label"] = "代理商完单"
        details.append(detail)
    return details


def _reason_tag(
    code: str,
    summary: str,
    details: list[dict[str, Any]] | None = None,
    *,
    strength: str | None = None,
) -> dict[str, Any]:
    tag_details = details or []
    return {
        "code": code,
        "label": REASON_LABELS.get(code, code),
        "strength": strength or ("medium" if len(tag_details) >= 2 else "weak"),
        "details": tag_details,
        "summary": summary,
    }


def _metric_detail(
    metric: str,
    label: str,
    change: MetricChange,
    *,
    rate_metric: bool = False,
) -> dict[str, Any]:
    return {
        "metric": metric,
        "label": label,
        "current": change.current,
        "compare": change.compare,
        "delta": change.delta,
        "rate": change.rate,
        "text": format_change_fragment(metric, label, change, rate_metric=rate_metric),
    }


def _evidence_detail(kind: str, label: str, item: Any) -> dict[str, Any]:
    summary = _item_summary(item)
    if isinstance(item, dict):
        summary = _clean_text(item.get("text")) or summary
    return {
        "metric": kind,
        "label": label,
        "current": summary,
        "compare": "",
        "delta": "",
        "rate": None,
        "text": summary,
    }


def _reason_sort_key(tag: dict[str, Any]) -> tuple[int, int]:
    return (REASON_PRIORITY.get(tag["code"], 0), len(tag.get("details") or []))


def _primary_reason(reason_tags: list[dict[str, Any]]) -> str:
    for tag in reason_tags:
        code = tag.get("code")
        if code not in {"city_market_up", "city_market_down"}:
            return code or "evidence_insufficient"
    return "evidence_insufficient"


def _secondary_reasons(reason_tags: list[dict[str, Any]], primary_reason: str) -> list[str]:
    return [tag["code"] for tag in reason_tags if tag["code"] != primary_reason]


def _metric_signal_group(
    state: RowState,
    code: str,
    title: str,
    metrics: list[tuple[str, str]],
    threshold: float,
    *,
    rate_metric: bool = False,
) -> dict[str, Any] | None:
    details = []
    for metric, label in metrics:
        change = _metric_change(state.current, state.compare, metric)
        if change is None:
            continue
        if not _is_aligned_change(change, state.direction, threshold, rate_metric=rate_metric):
            continue
        details.append(
            {
                "metric": metric,
                "label": label,
                "current": change.current,
                "compare": change.compare,
                "delta": change.delta,
                "rate": change.rate,
                "text": format_change_fragment(metric, label, change, rate_metric=rate_metric),
            }
        )
    if not details:
        return None
    return {
        "code": code,
        "title": title,
        "strength": "medium" if len(details) >= 2 else "weak",
        "details": details,
        "summary": f"{title}侧：" + "、".join(item["text"] for item in details[:3]),
    }


def _external_evidence_signals(evidence: dict[str, Any], direction: str) -> list[dict[str, Any]]:
    signals = []
    weather_detail = _weather_evidence_detail(evidence, direction)
    if weather_detail:
        signals.append({
            "code": "weather",
            "title": "天气",
            "strength": "medium",
            "details": [weather_detail],
            "summary": weather_detail.get("text") or "天气异常",
        })
    events = _matched_items(evidence.get("local_events") or evidence.get("events") or [], direction)
    if events:
        signals.append({
            "code": "local_event",
            "title": "本地事件",
            "strength": "medium",
            "details": events,
            "summary": "本地事件：" + "；".join(_item_summary(item) for item in events[:2]),
        })
    return signals


def _gongbu_signal(state: RowState, period_type: str) -> dict[str, Any] | None:
    change = _metric_change(state.current, state.compare, "gongbu_hours")
    if change is None:
        return None
    threshold = state.gongbu_hour_threshold if state.gongbu_hour_threshold is not None else (
        GONGBU_DAILY_HOUR_THRESHOLD if period_type == "daily" else GONGBU_WEEKLY_HOUR_THRESHOLD
    )
    if state.direction == "down" and change.delta > -threshold:
        return None
    if state.direction == "up" and change.delta < threshold:
        return None
    label = "日均共补小时" if state.gongbu_daily_average else "共补小时"
    return {
        "code": "gongbu",
        "title": "共补",
        "strength": "medium",
        "details": [{
            "metric": "gongbu_hours",
            "label": label,
            "current": change.current,
            "compare": change.compare,
            "delta": change.delta,
            "rate": change.rate,
            "text": format_change_fragment("gongbu_hours", label, change),
        }],
        "summary": "共补变化：" + format_change_fragment("gongbu_hours", label, change),
    }


def _strip_leading_date(text: str) -> str:
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and _parse_date(parts[0]):
        return parts[1]
    return text


def _anomaly_type(state: RowState, city_movement: dict[str, Any]) -> str:
    if state.is_new_open and state.is_major:
        return "new_open"
    if city_movement.get("is_city_movement"):
        return "city_co_movement"
    if state.is_major:
        return "brand_city_anomaly"
    return "observation"


def _city_movement(state: RowState, city_rows: list[RowState]) -> dict[str, Any]:
    directional = [
        item for item in city_rows
        if item.is_major and not item.is_new_open and item.direction in {"up", "down"}
    ]
    same_direction = [item for item in directional if item.direction == state.direction]
    ratio = len(same_direction) / len(directional) if directional else 0.0
    is_city_movement = (
        state.is_major
        and not state.is_new_open
        and state.direction in {"up", "down"}
        and len(same_direction) >= CITY_MOVEMENT_MIN_COUNT
        and ratio >= CITY_MOVEMENT_MIN_RATIO
    )
    current_total = sum(item.completed.current for item in city_rows)
    compare_total = sum(item.completed.compare for item in city_rows)
    city_completed = MetricChange(
        current=current_total,
        compare=compare_total,
        delta=current_total - compare_total,
        rate=(current_total - compare_total) / compare_total if compare_total else None,
    )
    return {
        "city": state.city,
        "is_city_movement": is_city_movement,
        "same_direction_count": len(same_direction),
        "directional_major_count": len(directional),
        "same_direction_ratio": ratio,
        "same_direction_keys": [item.key for item in same_direction],
        "aggregate_row_count": len(city_rows),
        "aggregate_keys": [item.key for item in city_rows],
        "market_direction": _direction(city_completed.delta),
        "city_completed_order_count": city_completed.as_dict(),
    }


def _city_market_reason_tag(city_movement: dict[str, Any]) -> dict[str, Any] | None:
    direction = city_movement.get("market_direction")
    metrics = city_movement.get("city_completed_order_count")
    if direction not in {"up", "down"} or not isinstance(metrics, dict):
        return None
    current = number_or_none(metrics.get("current"))
    compare = number_or_none(metrics.get("compare"))
    delta = number_or_none(metrics.get("delta"))
    rate = number_or_none(metrics.get("rate"))
    if current is None or compare is None or delta is None:
        return None
    text = format_city_market_text(current=current, compare=compare, delta=delta, rate=rate)
    detail = {
        "metric": CITY_MARKET_METRIC,
        "label": "城市大盘完单",
        "city": city_movement.get("city") or "",
        "current": current,
        "compare": compare,
        "delta": delta,
        "rate": rate,
        "text": text,
    }
    return _reason_tag(f"city_market_{direction}", text, [detail], strength="medium")


def _metric_change(current: dict[str, float], compare: dict[str, float], metric: str) -> MetricChange | None:
    if metric not in current or metric not in compare:
        return None
    cur = current[metric]
    comp = compare[metric]
    delta = cur - comp
    rate = delta / comp if comp else None
    return MetricChange(cur, comp, delta, rate)


def _row_has_quality_issues(state: RowState) -> bool:
    if state.health_status == "partial":
        return True
    if state.missing_evidence:
        return True
    return any(
        detail.get("input_status") == "invalid"
        for statuses in (state.current_quality, state.compare_quality)
        for detail in statuses.values()
    )


def _is_major_change(change: MetricChange, rate_threshold: float, abs_threshold: float) -> bool:
    if change.compare == 0:
        return abs(change.delta) >= abs_threshold
    return abs(change.rate or 0.0) >= rate_threshold or abs(change.delta) >= abs_threshold


def _is_aligned_change(
    change: MetricChange,
    direction: str,
    threshold: float,
    *,
    rate_metric: bool,
    inverse: bool = False,
) -> bool:
    if inverse:
        if direction == "down" and change.delta <= 0:
            return False
        if direction == "up" and change.delta >= 0:
            return False
    elif direction == "down" and change.delta >= 0:
        return False
    elif direction == "up" and change.delta <= 0:
        return False
    if rate_metric:
        return abs(change.delta) >= threshold
    if change.compare == 0:
        return abs(change.delta) > 0
    return abs(change.rate or 0.0) >= threshold


def _merge_row_and_city_evidence(row: dict[str, Any], city_evidence: dict[str, Any]) -> dict[str, Any]:
    merged = dict(city_evidence)
    for key in ("weather", "weather_events", "local_events", "events", "competitor_openings", "competitive_openings"):
        if row.get(key):
            merged[key] = row[key]
    return merged


def _merge_evidence_context_for_state(
    city_evidence: dict[str, Any],
    evidence_context: dict[str, Any],
    state: RowState,
) -> dict[str, Any]:
    merged = dict(city_evidence)
    if not isinstance(evidence_context, dict):
        return merged

    yunli = evidence_context.get("yunli")
    if isinstance(yunli, dict):
        status = _clean_text(yunli.get("status"))
        if status:
            merged["yunli_evidence_status"] = status
        by_key = yunli.get("agent_contributions_by_key")
        if isinstance(by_key, dict):
            contributions = by_key.get(state.key)
            if isinstance(contributions, dict):
                merged["yunli_agent_contributions"] = contributions

    tianqi = evidence_context.get("tianqi")
    if isinstance(tianqi, dict):
        status = _clean_text(tianqi.get("status"))
        if status:
            merged["tianqi_evidence_status"] = status
        weather_periods_by_city = tianqi.get("weather_periods_by_city") or tianqi.get("weather_periods")
        if isinstance(weather_periods_by_city, dict):
            weather_periods = weather_periods_by_city.get(state.city)
            if weather_periods:
                merged["weather_periods"] = weather_periods
        weather_by_city = tianqi.get("weather_by_city") or tianqi.get("city_weather")
        if isinstance(weather_by_city, dict):
            weather = weather_by_city.get(state.city)
            if weather:
                merged["weather"] = weather

    competitive = evidence_context.get("competitive") or evidence_context.get("competitor")
    if isinstance(competitive, dict):
        status = _clean_text(competitive.get("status"))
        if status:
            merged["competitive_evidence_status"] = status
        by_city = (
            competitive.get("openings_by_city")
            or competitive.get("competitor_openings_by_city")
            or competitive.get("competitive_openings_by_city")
        )
        if isinstance(by_city, dict):
            openings = by_city.get(state.city)
            if isinstance(openings, list):
                merged["competitor_openings"] = openings

    local_events = evidence_context.get("local_events")
    if isinstance(local_events, dict):
        status = _clean_text(local_events.get("status"))
        if status:
            merged["local_events_evidence_status"] = status
        event_items = list(merged.get("local_events") or []) if isinstance(merged.get("local_events"), list) else []
        by_key = local_events.get("events_by_key")
        if isinstance(by_key, dict):
            key_events = by_key.get(state.key)
            if isinstance(key_events, list):
                event_items.extend(item for item in key_events if _event_matches_state(item, state))
        by_city = local_events.get("events_by_city") or local_events.get("city_events")
        if isinstance(by_city, dict):
            city_events = by_city.get(state.city)
            if isinstance(city_events, list):
                event_items.extend(item for item in city_events if _event_matches_state(item, state))
        if event_items:
            merged["local_events"] = _dedup_evidence_items(event_items)

    return merged


def _event_matches_state(item: Any, state: RowState) -> bool:
    if not isinstance(item, dict):
        return True
    cities = _clean_list(item.get("cities") or item.get("city") or item.get("city_name"))
    if cities and state.city not in cities:
        return False
    brands = _clean_list(item.get("brands") or item.get("brand") or item.get("brand_name"))
    if brands and state.brand not in brands:
        return False
    operators = _clean_list(item.get("operators") or item.get("operator") or item.get("operator_name") or item.get("subject"))
    if operators and state.operator not in operators:
        return False
    return True


def _dedup_evidence_items(items: list[Any]) -> list[Any]:
    result = []
    seen: set[str] = set()
    for item in items:
        marker = _evidence_item_marker(item)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def _evidence_item_marker(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("source_ref", "raw_text_hash"):
            value = _clean_text(item.get(key))
            if value:
                return f"{key}:{value}"
        return "|".join(
            _clean_text(item.get(key))
            for key in ("period", "date", "summary", "text")
        )
    return str(item)


def _evidence_context_summary(evidence_context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence_context, dict) or not evidence_context:
        return {}
    summary: dict[str, Any] = {}
    meta = evidence_context.get("meta")
    if isinstance(meta, dict):
        summary["source"] = meta.get("source")
        summary["requested_key_count"] = meta.get("requested_key_count")
        summary["requested_city_count"] = meta.get("requested_city_count")
        summary["current_period"] = meta.get("current_period")
        summary["compare_period"] = meta.get("compare_period")
    for section_name in ("yunli", "tianqi", "competitive", "local_events"):
        section = evidence_context.get(section_name)
        if not isinstance(section, dict):
            continue
        section_summary: dict[str, Any] = {
            "status": section.get("status"),
            "gap_count": len(section.get("gaps") or []),
        }
        coverage = section.get("coverage")
        if isinstance(coverage, dict):
            section_summary["coverage"] = coverage
        refresh_run = section.get("refresh_run")
        if isinstance(refresh_run, dict):
            section_summary["refresh_run"] = {
                key: refresh_run.get(key)
                for key in (
                    "run_id",
                    "status",
                    "period_start",
                    "period_end",
                    "target_row_count",
                    "expected_coverage_count",
                    "actual_coverage_count",
                    "content_hash",
                    "finished_at",
                )
                if key in refresh_run
            }
        refresh_runs = section.get("refresh_runs")
        if isinstance(refresh_runs, dict):
            section_summary["refresh_runs"] = refresh_runs
        summary[section_name] = section_summary
    return {
        key: value
        for key, value in summary.items()
        if value not in (None, {}, [])
    }


def _evidence_diagnosis_gaps(
    state: RowState,
    evidence: dict[str, Any],
    city_movement: dict[str, Any],
    reason_tags: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    gaps = []
    capacity_hit = any(tag.get("code") in {"capacity_up", "capacity_down"} for tag in reason_tags)
    has_agent_detail = any(
        detail.get("metric") == YUNLI_AGENT_METRIC
        for tag in reason_tags
        for detail in tag.get("details") or []
    )
    yunli_status = evidence.get("yunli_evidence_status")
    if capacity_hit and yunli_status in {"missing", "partial"} and not has_agent_detail:
        gaps.append({
            "source_index": state.source_index,
            "key": state.key,
            "brand": state.brand,
            "city": state.city,
            "reason": (
                "yunli_agent_evidence_partial"
                if yunli_status == "partial"
                else "yunli_agent_evidence_missing"
            ),
            "severity": "warning",
            "suggested_check": "检查 lxyidong evidence refresh 运行记录",
        })

    has_weather_detail = any(
        detail.get("metric") == "weather"
        for tag in reason_tags
        for detail in tag.get("details") or []
    )
    tianqi_status = evidence.get("tianqi_evidence_status")
    if (
        city_movement.get("is_city_movement")
        and tianqi_status in {"missing", "partial"}
        and not has_weather_detail
    ):
        gaps.append({
            "source_index": state.source_index,
            "key": state.key,
            "brand": state.brand,
            "city": state.city,
            "reason": (
                "tianqi_evidence_partial"
                if tianqi_status == "partial"
                else "tianqi_evidence_missing"
            ),
            "severity": "warning",
            "suggested_check": "检查 lxyidong evidence refresh 运行记录",
        })
    return gaps


def _matched_items(items: list[Any], direction: str) -> list[Any]:
    matched = []
    for item in items:
        if not isinstance(item, dict):
            matched.append(item)
            continue
        impact = _clean_text(item.get("impact_direction") or item.get("direction"))
        if not impact or impact == direction:
            matched.append(item)
    return matched


def _item_summary(item: Any) -> str:
    if isinstance(item, dict):
        date_text = _clean_text(item.get("date") or item.get("start_date"))
        summary = _clean_text(item.get("summary") or item.get("name") or item.get("event"))
        return f"{date_text} {summary}".strip() or str(item)
    return str(item)


def _city_evidence_index(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in items:
        city = _clean_text(item.get("city") or item.get("city_name"))
        if city:
            index[city] = item
    return index


def _city_index(rows: list[RowState]) -> dict[str, list[RowState]]:
    index: dict[str, list[RowState]] = {}
    for row in rows:
        index.setdefault(row.city, []).append(row)
    return index


def _is_new_open(
    open_city_date: str,
    period_start: date | None,
    period_end: date | None,
    compare_end: date | None,
) -> bool:
    open_date = _parse_date(open_city_date)
    if not open_date or not period_end:
        return False
    if compare_end:
        return compare_end < open_date <= period_end
    if period_start:
        return period_start <= open_date <= period_end
    return False


def _is_closed(row: dict[str, Any]) -> bool:
    if "is_opening" not in row:
        return False
    value = row.get("is_opening")
    if isinstance(value, str):
        return value.strip().lower() in {"0", "false", "no", "closed", "关城", "否"}
    return not bool(value)


def _is_excluded_brand(brand: str, period_start: date | None, excluded_brands: set[str]) -> bool:
    if brand not in excluded_brands:
        return False
    return period_start is None or period_start >= PINDA_EXCLUSION_START


def _direction(delta: float) -> str:
    if delta > 0:
        return "up"
    if delta < 0:
        return "down"
    return "flat"


def _is_numeric_zero(value: Any) -> bool:
    if isinstance(value, bool) or value in (None, "", "/"):
        return False
    try:
        return float(str(value).strip().replace(",", "")) == 0.0
    except (TypeError, ValueError):
        return False


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    values = value if isinstance(value, list) else [value]
    result = []
    for item in values:
        text = _clean_text(item)
        if text and text not in result:
            result.append(text)
    return result


def _parse_date(value: Any) -> date | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
