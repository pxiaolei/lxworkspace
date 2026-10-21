"""本地 schema 白名单加载。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .query_policy import validate_identifier


class SchemaCatalog:
    """基于 assets/schema.json 的表结构索引。"""

    def __init__(self, schema_path: Path) -> None:
        self.schema_path = schema_path
        self._tables = self._load_tables(schema_path)

    @classmethod
    def from_skill_root(cls, skill_root: Path) -> "SchemaCatalog":
        return cls(skill_root / "assets" / "schema.json")

    @property
    def table_names(self) -> set[str]:
        return set(self._tables)

    def validate_table_name(self, table_name: str) -> str:
        return validate_identifier(table_name, self.table_names)

    def columns(self, table_name: str) -> list[dict[str, Any]]:
        name = self.validate_table_name(table_name)
        return list(self._tables[name].get("columns", []))

    @staticmethod
    def _load_tables(schema_path: Path) -> dict[str, dict[str, Any]]:
        if not schema_path.exists():
            raise RuntimeError(f"schema 文件不存在: {schema_path}")
        with open(schema_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        tables = data.get("tables", []) if isinstance(data, dict) else []
        result: dict[str, dict[str, Any]] = {}
        for table in tables:
            name = table.get("name")
            if name:
                result[name] = table
        if not result:
            raise RuntimeError(f"schema 文件中没有表定义: {schema_path}")
        return result
