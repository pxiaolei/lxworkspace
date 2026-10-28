#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_path(raw: str | Path, root: Path | None = None) -> Path:
    base = root or skill_root()
    # 兼容 Windows 反斜杠（旧配置文件可能仍使用 \\）和 ${SKILL_ROOT} 占位符
    expanded = str(raw).replace("${SKILL_ROOT}", str(base)).replace("\\", "/")
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    # 单值包装为列表，避免配置错误导致静默丢失
    return [value]


def load_yaml(path: Path) -> dict[str, Any]:
    """加载 YAML 文件，使用 PyYAML 替代原有的简易解析器。"""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def load_brands(root: Path | None = None) -> list[dict[str, Any]]:
    base = root or skill_root()
    brands_dir = base / "brands"
    index_path = brands_dir / "brands.yaml"
    order: list[str] = []
    if index_path.is_file():
        index_data = load_yaml(index_path)
        order = [str(item) for item in as_list(index_data.get("brands"))]
    configs = {
        path.stem: load_yaml(path)
        for path in sorted(brands_dir.glob("*.yaml"))
        if path.name != "brands.yaml"
    }
    brands = [configs[brand_id] for brand_id in order if brand_id in configs] if order else list(configs.values())
    for brand in brands:
        brand["_config_path"] = str(brands_dir / f"{brand['brand_id']}.yaml")
    return brands


def load_templates(root: Path | None = None) -> list[dict[str, Any]]:
    base = root or skill_root()
    data = load_yaml(base / "assets" / "templates" / "templates.yaml")
    templates: list[dict[str, Any]] = []
    for template_id in as_list(data.get("templates")):
        item = data.get(str(template_id))
        if not isinstance(item, dict):
            continue
        template = dict(item)
        template["template_id"] = str(template_id)
        template["display_name"] = str(item.get("display_name") or template_id)
        template["example_path"] = str(item.get("example_path") or "")
        templates.append(template)
    return templates


def route_name(name: str, brands: list[dict[str, Any]]) -> dict[str, Any]:
    matches: list[dict[str, str]] = []
    for brand in brands:
        for keyword in as_list(brand.get("filename_keywords")):
            keyword_text = str(keyword)
            if keyword_text and keyword_text in name:
                matches.append(
                    {
                        "brand_id": str(brand["brand_id"]),
                        "brand": str(brand["canonical_name"]),
                        "keyword": keyword_text,
                    }
                )
                break
    if len(matches) == 1:
        match = matches[0]
        return {
            "status": "supported",
            "name": name,
            "brand_id": match["brand_id"],
            "brand": match["brand"],
            "matched_keyword": match["keyword"],
        }
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "name": name,
            "matches": matches,
            "reason": "file name matched multiple configured brands",
        }
    return {
        "status": "unsupported",
        "name": name,
        "reason": "file name did not match any configured brand",
    }


def find_brand(brand_name: str, brands: list[dict[str, Any]]) -> dict[str, Any] | None:
    needle = brand_name.strip()
    for brand in brands:
        names = {str(item) for item in as_list(brand.get("aliases"))}
        names.add(str(brand.get("canonical_name") or ""))
        names.add(str(brand.get("brand_id") or ""))
        validator_brand = brand.get("qr_validation", {}).get("validator_brand")
        if validator_brand:
            names.add(str(validator_brand))
        if needle in names:
            return brand
    return None
