#!/usr/bin/env python3
"""Business-readable summary writer for lx-yidongfenxi results."""

from __future__ import annotations

from typing import Any

from factor_formatter import (
    CITY_MARKET_METRIC,
    EFFICIENCY_DEFAULT_METRICS,
    FIRST_DRIVER_METRIC,
    GONGBU_METRIC,
    INCENTIVE_DEFAULT_METRICS,
    ONLINE_CAPACITY_METRICS,
    YUNLI_AGENT_METRIC,
    clean_text,
    format_channel_detail_fragment,
    format_detail_text,
)


DEFAULT_LAYER_ORDER = ("demand", "city_market", "capacity", "capacity_drivers", "efficiency")


def write_smart_analysis(
    state: Any,
    anomaly_type: str,
    city_movement: dict[str, Any],
    reason_tags: list[dict[str, Any]],
    diagnosis_layers: dict[str, Any],
    period_type: str,
) -> str:
    if anomaly_type == "new_open":
        if getattr(state, "selected_compare_dates", False):
            return f"基准期后开城：码表开城日期为 {getattr(state, 'open_city_date', '') or '未知'}，贡献来自基准期后新增运营。"
        return f"新开城：码表开城日期为 {getattr(state, 'open_city_date', '') or '未知'}，本期属于新开城贡献。"

    parts = []
    business_tags = [tag for tag in reason_tags if tag.get("code") != "evidence_insufficient"]
    tags_by_layer = _tags_by_layer(business_tags)
    for layer in diagnosis_layers.get("flow") or DEFAULT_LAYER_ORDER:
        layer_tags = tags_by_layer.get(layer) or []
        if not layer_tags:
            continue
        if layer == "capacity_drivers":
            parts.extend(_capacity_driver_fragments(layer_tags))
            continue
        fragments = [
            fragment
            for fragment in (_smart_reason_fragment(tag, period_type) for tag in layer_tags)
            if fragment
        ]
        parts.extend(fragments[:2])

    if parts:
        if any(tag.get("code") == "evidence_insufficient" for tag in reason_tags):
            parts.append("已按发单、运力和效率逐层检查，暂未发现单一强解释项")
        return "；".join(_unique_texts(parts)) + "。"

    return "已按发单、运力和效率逐层检查，暂未发现单一强解释项。"


def _tags_by_layer(reason_tags: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {layer: [] for layer in DEFAULT_LAYER_ORDER}
    for tag in reason_tags:
        layer = clean_text(tag.get("analysis_layer")) or _layer_for_reason_code(clean_text(tag.get("code")))
        groups.setdefault(layer, []).append(tag)
    return groups


def _layer_for_reason_code(code: str) -> str:
    if code.startswith("demand_"):
        return "demand"
    if code.startswith("city_market_"):
        return "city_market"
    if code.startswith("capacity_"):
        return "capacity"
    if code.startswith(("gongbu_", "subsidy_activity_")) or code == "competitive_opening":
        return "capacity_drivers"
    if code.startswith("efficiency_"):
        return "efficiency"
    return "overall"


def _smart_reason_fragment(tag: dict[str, Any], period_type: str) -> str:
    code = clean_text(tag.get("code"))
    if code == "new_open":
        return clean_text(tag.get("label") or code)
    if code in {"demand_up", "demand_down"}:
        return _demand_fragment(tag, period_type)
    if code in {"city_market_up", "city_market_down"}:
        return _city_market_fragment(tag)
    if code in {"capacity_up", "capacity_down"}:
        return _capacity_fragment(tag)
    if code in {"efficiency_up", "efficiency_down"}:
        return _efficiency_fragment(tag)
    return _labelled_metric_fragment(tag)


def _city_market_fragment(tag: dict[str, Any]) -> str:
    texts = [
        format_detail_text(detail)
        for detail in tag.get("details") or []
        if clean_text(detail.get("metric")) == CITY_MARKET_METRIC
    ]
    text = next((item for item in _unique_texts(texts) if item), "")
    if not text:
        return ""
    return "城市大盘：" + text.removeprefix("城市大盘")


def _demand_fragment(tag: dict[str, Any], period_type: str) -> str:
    label = clean_text(tag.get("label") or tag.get("code"))
    details = tag.get("details") or []
    metric_texts = [
        format_detail_text(detail)
        for detail in details
        if detail.get("metric") not in {"weather", "local_event"}
    ]
    support_text = _supporting_evidence_text(details, period_type)
    fragments = [fragment for fragment in (_join_unique(metric_texts), support_text) if fragment]
    return f"{label}：" + "，".join(fragments) if fragments else label


def _capacity_fragment(tag: dict[str, Any]) -> str:
    label = clean_text(tag.get("label") or tag.get("code"))
    code = clean_text(tag.get("code"))
    direction = "提升" if code.endswith("_up") else "下降"
    details = tag.get("details") or []

    online_texts = [
        format_detail_text(detail)
        for detail in details
        if clean_text(detail.get("metric")) in ONLINE_CAPACITY_METRICS
    ]
    first_driver_texts = [
        format_detail_text(detail)
        for detail in details
        if clean_text(detail.get("metric")) == FIRST_DRIVER_METRIC
    ]
    channel_texts = [
        format_channel_detail_fragment(detail)
        for detail in details
        if clean_text(detail.get("metric")) == YUNLI_AGENT_METRIC
    ]

    capacity_parts = []
    online_texts = _unique_texts([text for text in online_texts if text])
    if len(online_texts) >= 2:
        capacity_parts.append(f"在线运力{direction}（{'、'.join(online_texts[:3])}）")
    elif online_texts:
        capacity_parts.append(online_texts[0])
    capacity_parts.extend(_unique_texts([text for text in first_driver_texts if text])[:2])

    parts = []
    if capacity_parts:
        parts.append(f"{label}：" + "，".join(capacity_parts))
    channel_texts = _unique_texts([text for text in channel_texts if text])
    if channel_texts:
        parts.append("渠道变化：" + "；".join(channel_texts[:3]))
    return "；".join(parts) if parts else label


def _capacity_driver_fragments(tags: list[dict[str, Any]]) -> list[str]:
    incentive_details = []
    context_parts = []
    direction = ""

    for tag in tags:
        code = clean_text(tag.get("code"))
        if code.endswith("_up"):
            direction = "增加"
        elif code.endswith("_down"):
            direction = "下降"

        if code.startswith(("gongbu_", "subsidy_activity_")):
            for detail in tag.get("details") or []:
                metric = clean_text(detail.get("metric"))
                if metric in INCENTIVE_DEFAULT_METRICS:
                    text = format_detail_text(detail)
                    if text:
                        incentive_details.append(text)
            continue

        if code == "competitive_opening":
            texts = [format_detail_text(detail) for detail in tag.get("details") or []]
            joined = _join_unique(texts[:3])
            if joined:
                context_parts.append(f"背景：竞品品牌开城：{joined}")
            continue

        fragment = _labelled_metric_fragment(tag)
        if fragment:
            context_parts.append(fragment)

    parts = []
    incentive_details = _unique_texts(incentive_details)
    if incentive_details:
        label = f"活动力度{direction or '变化'}"
        parts.append(f"{label}：" + "，".join(incentive_details[:4]))
    parts.extend(context_parts)
    return parts


def _efficiency_fragment(tag: dict[str, Any]) -> str:
    label = clean_text(tag.get("label") or tag.get("code"))
    details = [
        detail
        for detail in tag.get("details") or []
        if clean_text(detail.get("metric")) in EFFICIENCY_DEFAULT_METRICS
    ]
    texts = _unique_texts([format_detail_text(detail) for detail in details if detail])
    return f"{label}：" + "，".join(texts[:4]) if texts else ""


def _labelled_metric_fragment(tag: dict[str, Any]) -> str:
    label = clean_text(tag.get("label") or tag.get("code"))
    texts = _unique_texts([format_detail_text(detail) for detail in tag.get("details") or []])
    texts = [text for text in texts if text]
    return f"{label}：" + "，".join(texts[:4]) if texts else label


def _supporting_evidence_text(details: list[dict[str, Any]], period_type: str) -> str:
    support = []
    for detail in details:
        metric = clean_text(detail.get("metric"))
        if metric not in {"weather", "local_event"}:
            continue
        text = clean_text(detail.get("text") or detail.get("current"))
        label = clean_text(detail.get("label"))
        if not text:
            continue
        if metric == "weather":
            support.append(_weather_phrase(text, period_type))
        else:
            support.append(f"{label}（{text}）" if label else text)
    return "、".join([item for item in support if item][:3])


def _weather_phrase(text: str, period_type: str) -> str:
    del period_type
    return clean_text(text.removeprefix("天气："))


def _join_unique(values: list[str]) -> str:
    return "、".join(_unique_texts([value for value in values if value]))


def _unique_texts(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        value = clean_text(value)
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
