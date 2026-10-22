#!/usr/bin/env python3
"""Formatting helpers for lx-yidongfenxi factor evidence."""

from __future__ import annotations

from datetime import date
from typing import Any


YUNLI_AGENT_METRIC = "yunli_agent_completed_order_count"
CITY_MARKET_METRIC = "city_completed_order_count"
DEMAND_METRIC = "passenger_order_count"
GONGBU_METRIC = "gongbu_hours"
FIRST_DRIVER_METRIC = "first_completion_driver_count"
ONLINE_CAPACITY_METRICS = {
    "online_duration_hour",
    "online_driver_count",
    "completion_driver_count",
}
EFFICIENCY_DEFAULT_METRICS = {
    "response_rate",
    "transaction_rate",
    "driver_passenger_cancel_rate",
}
INCENTIVE_DEFAULT_METRICS = {
    GONGBU_METRIC,
    "merchant_subsidy_rate",
}

WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
DEFAULT_WEATHER_TAG_CODES = {
    "heavy_rain",
    "rainstorm",
    "typhoon",
    "snow",
    "thunderstorm",
    "hail",
    "wind",
    "gale",
}
DEFAULT_WEATHER_TAG_LABELS = {
    "heavy_rain": "强降雨",
    "rainstorm": "暴雨",
    "typhoon": "台风",
    "snow": "降雪",
    "thunderstorm": "雷暴",
    "hail": "冰雹",
    "wind": "大风",
    "gale": "大风",
}
DEFAULT_WEATHER_SUMMARY_LABELS = (
    ("特大暴雨", "特大暴雨"),
    ("大暴雨", "大暴雨"),
    ("强降雨", "强降雨"),
    ("暴雨", "暴雨"),
    ("台风", "台风"),
    ("暴雪", "降雪"),
    ("大雪", "降雪"),
    ("降雪", "降雪"),
    ("雷暴", "雷暴"),
    ("冰雹", "冰雹"),
    ("大风", "大风"),
    ("强风", "大风"),
)
DEFAULT_WEATHER_KEYWORDS = tuple(keyword for keyword, _label in DEFAULT_WEATHER_SUMMARY_LABELS)


def format_change_fragment(metric: str, label: str, change: Any, *, rate_metric: bool = False, comparison_label: str = "同比") -> str:
    delta = number_or_none(getattr(change, "delta", None))
    rate = number_or_none(getattr(change, "rate", None))
    if delta is None:
        return clean_text(label)

    metric = clean_text(metric)
    label = clean_text(label) or metric
    if rate_metric or is_rate_detail(metric, label):
        return f"{label} {format_signed_pp(delta)}"
    if metric == DEMAND_METRIC:
        if rate is not None:
            return f"{label}{comparison_label} {format_signed_rate(rate)}"
        return f"{label}变化 {format_signed_metric_delta(metric, label, delta)}"
    if metric == GONGBU_METRIC:
        return f"{label} {format_signed_metric_delta(metric, label, delta)}"
    if metric == FIRST_DRIVER_METRIC:
        return f"首单司机 {format_signed_metric_delta(metric, label, delta)}"

    suffix = f"（{format_signed_rate(rate)}）" if rate is not None else ""
    return f"{label} {format_signed_metric_delta(metric, label, delta)}{suffix}"


def format_detail_text(detail: dict[str, Any]) -> str:
    metric = clean_text(detail.get("metric"))
    if metric == YUNLI_AGENT_METRIC:
        return format_channel_detail_fragment(detail)
    if metric == CITY_MARKET_METRIC:
        current = number_or_none(detail.get("current"))
        compare = number_or_none(detail.get("compare"))
        delta = number_or_none(detail.get("delta"))
        if current is not None and compare is not None and delta is not None:
            return format_city_market_text(
                current=current,
                compare=compare,
                delta=delta,
                rate=number_or_none(detail.get("rate")),
            )
    if metric in {"weather", "local_event", "competitive_opening"}:
        return clean_text(detail.get("text") or detail.get("current"))

    delta = number_or_none(detail.get("delta"))
    if delta is not None and delta != 0:
        pseudo_change = _Change(delta=delta, rate=number_or_none(detail.get("rate")))
        return format_change_fragment(
            metric,
            clean_text(detail.get("label")),
            pseudo_change,
            rate_metric=is_rate_detail(metric, clean_text(detail.get("label"))),
            comparison_label=clean_text(detail.get("comparison_label")) or "同比",
        )
    return clean_text(detail.get("text") or detail.get("label") or metric)


def format_channel_detail_fragment(detail: dict[str, Any]) -> str:
    text = clean_text(detail.get("text"))
    agent_name = clean_channel_display_name(
        detail.get("display_agent_name") or detail.get("agent_name_norm")
    ) or "渠道"
    delta = number_or_none(detail.get("delta"))
    if delta is None or delta == 0:
        return clean_channel_text(text) or agent_name

    parts = [f"{agent_name}完单 {format_signed_metric_delta(YUNLI_AGENT_METRIC, '完单', delta)}"]
    rate = number_or_none(detail.get("rate"))
    if rate is not None:
        parts.append(f"同比 {format_signed_rate(rate)}")
    return "，".join(parts)


def format_city_market_text(*, current: float, compare: float, delta: float, rate: float | None) -> str:
    if rate is not None and delta != 0:
        direction = "上涨" if delta > 0 else "下降"
        return f"城市大盘完单{direction} {abs(rate):.2%}"
    if compare == 0 and current > 0:
        return f"城市大盘完单由 0 增至 {format_metric_delta('完单', current)}"
    if delta == 0:
        return "城市大盘完单持平"
    direction = "增加" if delta > 0 else "减少"
    return f"城市大盘完单{direction} {abs(delta):,.0f}"


def format_reason_summary(label: str, details: list[dict[str, Any]], *, limit: int = 3) -> str:
    fragments = [format_detail_text(detail) for detail in details[:limit]]
    fragments = [fragment for fragment in fragments if fragment]
    return f"{label}：" + "、".join(fragments) if fragments else label


def format_metric_delta(label: str, value: float) -> str:
    if any(token in label for token in ("人均", "完单/小时", "时均完单", "单价")):
        return f"{value:,.2f}"
    if "时长" in label or "小时" in label:
        return f"{value:,.0f}小时"
    return f"{value:,.0f}"


def format_signed_metric_delta(metric: str, label: str, value: float) -> str:
    if any(token in label for token in ("人均", "完单/小时", "时均完单", "单价")):
        return f"{value:+,.2f}"
    if metric.endswith("_hour") or "duration_hour" in metric or "时长" in label or "小时" in label:
        return f"{value:+,.0f}小时"
    return f"{value:+,.0f}"


def format_signed_rate(value: float | None) -> str:
    if value is None:
        return "/"
    return f"{value:+.2%}"


def format_signed_pp(value: float) -> str:
    return f"{value * 100:+.2f}pp"


def is_rate_detail(metric: str, label: str) -> bool:
    return metric.endswith("_rate") or metric == "online_margin" or "率" in label


def clean_channel_text(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    for suffix in ("科技有限公司", "有限公司"):
        text = text.replace(suffix, "")
    return text


def clean_channel_display_name(value: Any) -> str:
    text = clean_channel_text(value)
    for suffix in ("科技", "公司"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.strip()


def format_weather_periods_text(weather_periods: dict[str, Any]) -> str:
    parts = []
    for period, label in (("current", "本期"), ("compare", "同比期")):
        items = weather_periods.get(period)
        if not isinstance(items, list):
            continue
        period_text = _weather_items_text(label, items)
        if period_text:
            parts.append(period_text)
    return "；".join(parts)


def is_default_weather_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    return bool(_default_weather_summary(item))


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def number_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    text = str(value).strip().replace(",", "")
    try:
        if text.endswith("%"):
            return float(text[:-1]) / 100
        return float(text)
    except (TypeError, ValueError):
        return None


class _Change:
    def __init__(self, *, delta: float, rate: float | None) -> None:
        self.delta = delta
        self.rate = rate


def _weather_items_text(period_label: str, items: list[Any]) -> str:
    grouped: dict[str, list[str]] = {}
    for item in sorted(items, key=_weather_item_sort_key):
        if not isinstance(item, dict):
            continue
        summary = _default_weather_summary(item)
        if not summary:
            continue
        weekday = clean_text(item.get("weekday")) or _weekday_from_date(clean_text(item.get("date") or item.get("date_day")))
        weekdays = grouped.setdefault(summary, [])
        if weekday and weekday not in weekdays:
            weekdays.append(weekday)
    fragments = []
    for summary, weekdays in grouped.items():
        if weekdays:
            fragments.append(f"{period_label}{'、'.join(weekdays)}有{summary}")
        else:
            fragments.append(f"{period_label}有{summary}")
    return "，".join(fragments[:3])


def _weather_item_sort_key(item: Any) -> tuple[str, str]:
    if not isinstance(item, dict):
        return ("", str(item))
    return (
        clean_text(item.get("date") or item.get("date_day") or ""),
        clean_text(item.get("summary") or item.get("weather_summary") or item.get("weather_text") or ""),
    )


def _default_weather_summary(item: dict[str, Any]) -> str:
    labels = []
    tags = item.get("evidence_tags") or item.get("tags") or []
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict):
                code = clean_text(tag.get("code"))
            else:
                code = clean_text(tag)
            label = DEFAULT_WEATHER_TAG_LABELS.get(code)
            if label and label not in labels:
                labels.append(label)
    summary = clean_text(item.get("summary") or item.get("weather_summary") or item.get("weather_text"))
    for keyword, label in DEFAULT_WEATHER_SUMMARY_LABELS:
        if keyword in summary and label not in labels:
            labels.append(label)
    if labels:
        return "、".join(labels)
    if any(keyword in summary for keyword in DEFAULT_WEATHER_KEYWORDS):
        return summary
    return ""


def _weekday_from_date(date_text: str) -> str:
    parsed = _parse_date(date_text)
    if not parsed:
        return ""
    return WEEKDAY_LABELS[parsed.weekday()]


def _parse_date(value: Any) -> date | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
