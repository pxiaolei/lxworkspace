"""schema 对比和目录渲染工具。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def load_schema_file(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def diff_schemas(local: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    """对比本地 schema 与线上 schema。"""
    local_tables = _table_map(local)
    remote_tables = _table_map(remote)

    added_tables = sorted(set(remote_tables) - set(local_tables))
    removed_tables = sorted(set(local_tables) - set(remote_tables))
    changed_tables = []

    for table_name in sorted(set(local_tables) & set(remote_tables)):
        local_table = local_tables[table_name]
        remote_table = remote_tables[table_name]
        local_columns = _column_map(local_table)
        remote_columns = _column_map(remote_table)

        added_columns = sorted(set(remote_columns) - set(local_columns))
        removed_columns = sorted(set(local_columns) - set(remote_columns))
        changed_columns = []

        for field in sorted(set(local_columns) & set(remote_columns)):
            local_col = local_columns[field]
            remote_col = remote_columns[field]
            differences = {}
            for key in ("type", "key", "null", "default", "comment"):
                if local_col.get(key) != remote_col.get(key):
                    differences[key] = {
                        "local": local_col.get(key),
                        "remote": remote_col.get(key),
                    }
            if differences:
                changed_columns.append({"field": field, "differences": differences})

        comment_changed = local_table.get("comment", "") != remote_table.get("comment", "")
        if added_columns or removed_columns or changed_columns or comment_changed:
            changed_tables.append(
                {
                    "table": table_name,
                    "comment_changed": comment_changed,
                    "added_columns": added_columns,
                    "removed_columns": removed_columns,
                    "changed_columns": changed_columns,
                }
            )

    return {
        "type": "lx_shujuku.schema_diff",
        "version": 1,
        "generated_at": _now_iso(),
        "database": remote.get("database") or local.get("database") or "dataReporting",
        "summary": {
            "added_table_count": len(added_tables),
            "removed_table_count": len(removed_tables),
            "changed_table_count": len(changed_tables),
            "has_changes": bool(added_tables or removed_tables or changed_tables),
        },
        "added_tables": added_tables,
        "removed_tables": removed_tables,
        "changed_tables": changed_tables,
    }


def render_diff_text(diff: dict[str, Any]) -> str:
    summary = diff["summary"]
    lines = [
        "schema 对比结果",
        "=" * 40,
        f"新增表: {summary['added_table_count']}",
        f"删除表: {summary['removed_table_count']}",
        f"变化表: {summary['changed_table_count']}",
        "",
    ]

    if diff["added_tables"]:
        lines.append("新增表:")
        lines.extend(f"  + {name}" for name in diff["added_tables"])
        lines.append("")

    if diff["removed_tables"]:
        lines.append("删除表:")
        lines.extend(f"  - {name}" for name in diff["removed_tables"])
        lines.append("")

    if diff["changed_tables"]:
        lines.append("字段变化:")
        for table in diff["changed_tables"]:
            lines.append(f"  * {table['table']}")
            if table["comment_changed"]:
                lines.append("    - 表注释变化")
            for column in table["added_columns"]:
                lines.append(f"    + 字段 {column}")
            for column in table["removed_columns"]:
                lines.append(f"    - 字段 {column}")
            for column in table["changed_columns"]:
                keys = ", ".join(sorted(column["differences"]))
                lines.append(f"    ~ 字段 {column['field']} 变化: {keys}")
        lines.append("")

    if not summary["has_changes"]:
        lines.append("本地 schema 与线上 schema 一致。")

    return "\n".join(lines).rstrip() + "\n"


def render_table_catalog(schema: dict[str, Any]) -> str:
    generated_at = schema.get("generated_at") or _now_iso()
    database = schema.get("database") or "dataReporting"
    tables = schema.get("tables", [])

    lines = [
        "# dataReporting 数据库表结构目录",
        "",
        f"> 生成时间：{generated_at}  |  数据库：{database}  |  共 {len(tables)} 张表",
        "",
        "---",
        "",
        "## 目录",
        "",
    ]

    for index, table in enumerate(tables, 1):
        name = table.get("name", "")
        comment = table.get("comment", "")
        lines.append(f"{index}. [{name}](#{_anchor(name)}) — {comment}")

    lines.extend(["", "---", ""])

    for table in tables:
        name = table.get("name", "")
        comment = table.get("comment", "")
        columns = table.get("columns", [])
        lines.extend(
            [
                f"## {name}",
                "",
                f"**{comment}**  |  {len(columns)} 个字段",
                "",
                "| # | 字段名 | 类型 | 键 | 可为空 | 注释 |",
                "|---|--------|------|-----|--------|------|",
            ]
        )
        for index, column in enumerate(columns, 1):
            key = column.get("key", "")
            key_value = f"`{key}`" if key else ""
            nullable = column.get("null", "")
            null_value = "是" if nullable == "YES" else "否" if nullable == "NO" else nullable
            lines.append(
                "| {index} | `{field}` | `{type}` | {key} | {null} | {comment} |".format(
                    index=index,
                    field=column.get("field", ""),
                    type=column.get("type", ""),
                    key=key_value,
                    null=null_value,
                    comment=str(column.get("comment", "")).replace("\n", " "),
                )
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _table_map(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        table["name"]: table
        for table in schema.get("tables", [])
        if isinstance(table, dict) and table.get("name")
    }


def _column_map(table: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        column["field"]: column
        for column in table.get("columns", [])
        if isinstance(column, dict) and column.get("field")
    }


def _anchor(name: str) -> str:
    return name.replace("_", "-").lower()


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat()
