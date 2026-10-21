"""SQL 只读策略与基础校验。"""

from __future__ import annotations

import re


ALLOWED_COMMANDS = {"select", "show", "describe", "desc", "explain"}
FORBIDDEN_KEYWORDS = {
    "analyze",
    "alter",
    "call",
    "create",
    "delete",
    "drop",
    "dumpfile",
    "grant",
    "handler",
    "into",
    "insert",
    "kill",
    "load",
    "lock",
    "optimize",
    "outfile",
    "repair",
    "rename",
    "replace",
    "revoke",
    "set",
    "truncate",
    "unlock",
    "update",
}

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LIMIT_RE = re.compile(r"\blimit\s+(\d+)(?:\s+offset\s+(\d+))?\b", re.IGNORECASE)
ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)
WHERE_RE = re.compile(r"\bwhere\b", re.IGNORECASE)
MAX_TOP_LEVEL_OFFSET = 10000
UNBOUNDED_SELECT_ALLOWLIST = {"operator_brand"}
PREDICATE_FUNCTIONS = (
    "cast",
    "convert",
    "date",
    "lower",
    "month",
    "trim",
    "upper",
    "year",
)
REPORTING_DATE_FIELDS = (
    "data_date",
    "date_day",
    "effect_end_time",
    "effect_start_time",
    "end_time",
    "open_city_date",
    "partition_date",
    "pull_time",
    "sale_end_time",
    "sale_start_time",
    "start_time",
    "datae_column_bc7a384cd7_day_real",
    "datae_column_b4276e28f8_day_real",
)
PREDICATE_BOUNDARY_RE = re.compile(
    r"\b(where|on|join|group\s+by|having|order\s+by|limit|union)\b",
    re.IGNORECASE,
)


def validate_identifier(name: str, allowed: set[str] | None = None) -> str:
    """校验表名/字段名这类简单标识符。"""
    value = name.strip()
    if not IDENTIFIER_RE.fullmatch(value):
        raise RuntimeError(f"非法表名或标识符: {name}")
    if allowed is not None and value not in allowed:
        raise RuntimeError(f"表不在 schema 白名单中: {value}")
    return value


def validate_limit(limit: int, max_limit: int) -> int:
    """校验 LIMIT 数值。"""
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"非法 LIMIT: {limit}") from exc
    if value <= 0:
        raise RuntimeError("LIMIT 必须大于 0")
    if value > max_limit:
        raise RuntimeError(f"LIMIT 不能超过 {max_limit}")
    return value


def ensure_readonly_sql(
    sql: str,
    default_limit: int,
    max_limit: int,
    allowed_tables: set[str] | None = None,
    append_default_limit: bool = True,
) -> str:
    """
    返回可安全执行的只读 SQL。

    约束：
    - 只允许 SELECT/SHOW/DESCRIBE/DESC/EXPLAIN
    - 禁止多语句和注释
    - 禁止写库/DDL/权限类关键字
    - 可选限制表名必须存在于 schema 白名单
    - 默认给未写顶层 LIMIT 的 SELECT 追加限制
    """
    normalized = _strip_trailing_semicolon(sql)
    masked = _mask_string_literals(normalized).lower()

    if ";" in masked:
        raise RuntimeError("禁止执行多条 SQL 语句")
    if "--" in masked or "/*" in masked or "#" in masked:
        raise RuntimeError("SQL 中不允许包含注释")
    if "`" in normalized:
        raise RuntimeError("SQL 中不允许使用反引号标识符，请使用 schema 白名单中的简单表名")

    match = re.match(r"\s*([A-Za-z]+)\b", masked)
    if not match:
        raise RuntimeError("SQL 为空或无法识别")
    command = match.group(1)
    if command not in ALLOWED_COMMANDS:
        allowed = ", ".join(sorted(ALLOWED_COMMANDS))
        raise RuntimeError(f"只允许只读 SQL: {allowed}")

    forbidden = sorted(
        word for word in FORBIDDEN_KEYWORDS
        if re.search(rf"\b{re.escape(word)}\b", masked)
    )
    if forbidden:
        raise RuntimeError(f"SQL 包含禁止关键字: {', '.join(forbidden)}")

    _validate_table_references(masked, command, allowed_tables)
    if command == "select":
        _validate_performance_shape(normalized, masked)

    if command == "select" and append_default_limit:
        return _ensure_limit(normalized, default_limit, max_limit)
    if command == "select":
        shape = analyze_query_shape(normalized)
        _validate_offset(shape["top_level_offset"])
        limit = shape["top_level_limit"]
        if limit is not None:
            validate_limit(limit, max_limit)
    return normalized


def _strip_trailing_semicolon(sql: str) -> str:
    value = sql.strip()
    while value.endswith(";"):
        value = value[:-1].strip()
    return value


def _ensure_limit(sql: str, default_limit: int, max_limit: int) -> str:
    shape = analyze_query_shape(sql)
    _validate_offset(shape["top_level_offset"])
    limit = shape["top_level_limit"]
    if limit is not None:
        validate_limit(limit, max_limit)
        return sql
    validate_limit(default_limit, max_limit)
    return f"{sql} LIMIT {default_limit}"


def _validate_offset(offset: int | None) -> None:
    if offset is not None and offset > MAX_TOP_LEVEL_OFFSET:
        raise RuntimeError(f"OFFSET 不能超过 {MAX_TOP_LEVEL_OFFSET}；请改用稳定游标或缩小查询范围")


def _validate_performance_shape(sql: str, masked_sql: str) -> None:
    """阻断无需执行计划即可确定的高风险查询形态。"""
    references = set(_extract_table_references(masked_sql))
    _validate_explicit_projection(masked_sql)
    _validate_predicates(sql, masked_sql)
    _validate_business_scope(masked_sql, references)


def _validate_explicit_projection(masked_sql: str) -> None:
    if re.search(
        r"\bselect\s+(?:distinct\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\.)?\*",
        masked_sql,
        re.IGNORECASE,
    ):
        raise RuntimeError("禁止 SELECT *；请只选择当前任务需要的明确字段")


def _validate_predicates(sql: str, masked_sql: str) -> None:
    function_pattern = "|".join(re.escape(name) for name in PREDICATE_FUNCTIONS)
    date_field_pattern = (
        r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?(?:"
        + "|".join(re.escape(field) for field in REPORTING_DATE_FIELDS)
        + r")\b"
    )
    arithmetic_pattern = (
        r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*"
        r"\s*[+\-*/]\s*(?:\d|(?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_])"
    )

    for raw_predicate, masked_predicate in _predicate_segments(sql, masked_sql):
        if re.search(rf"\b(?:{function_pattern})\s*\(", masked_predicate, re.IGNORECASE):
            raise RuntimeError("JOIN/WHERE 谓词列禁止函数包装；请改用裸字段直接比较")
        if re.search(r"\bcollate\b", masked_predicate, re.IGNORECASE):
            raise RuntimeError("JOIN/WHERE 谓词列禁止 COLLATE；请先核对字段兼容性")
        if re.search(arithmetic_pattern, masked_predicate, re.IGNORECASE):
            raise RuntimeError("JOIN/WHERE 谓词列禁止算术表达式；请改用裸字段范围")
        if re.search(r"\blike\s*(['\"])\s*%", raw_predicate, re.IGNORECASE):
            raise RuntimeError("禁止 LIKE 前导通配；请使用精确匹配或可走索引的前缀匹配")
        if re.search(rf"{date_field_pattern}\s*<=", masked_predicate, re.IGNORECASE):
            raise RuntimeError("日期范围必须使用半开区间：下界 >=，上界 <")
        if re.search(rf"{date_field_pattern}\s+between\b", masked_predicate, re.IGNORECASE):
            raise RuntimeError("日期范围必须使用半开区间，禁止对报表日期字段使用 BETWEEN")


def _predicate_segments(sql: str, masked_sql: str) -> list[tuple[str, str]]:
    tokens = list(PREDICATE_BOUNDARY_RE.finditer(masked_sql))
    segments: list[tuple[str, str]] = []
    for index, token in enumerate(tokens):
        if token.group(1).lower() not in {"where", "on"}:
            continue
        end = tokens[index + 1].start() if index + 1 < len(tokens) else len(masked_sql)
        segments.append((sql[token.end():end], masked_sql[token.end():end]))
    return segments


def _validate_business_scope(masked_sql: str, references: set[str]) -> None:
    scoped_tables = references - UNBOUNDED_SELECT_ALLOWLIST
    if not scoped_tables:
        return

    shape = analyze_query_shape(masked_sql)
    if shape["has_top_level_where"]:
        top_level = " ".join(_mask_nested_parentheses(masked_sql).split())
        if re.search(
            r"\bwhere\s+1\s*=\s*1\s*(?:group\s+by|order\s+by|limit|$)",
            top_level,
            re.IGNORECASE,
        ):
            raise RuntimeError("事实查询必须包含真实业务范围，WHERE 1=1 不构成业务范围")
        return

    if _is_single_row_probe(masked_sql) or _is_boundary_aggregate(masked_sql):
        return
    if re.search(
        r"\bfrom\s*\(\s*select\b[\s\S]*\bwhere\b",
        masked_sql,
        re.IGNORECASE,
    ):
        return

    tables = ", ".join(sorted(scoped_tables))
    raise RuntimeError(f"事实查询必须包含顶层 WHERE 业务范围；无界表: {tables}")


def _is_single_row_probe(masked_sql: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*select\s+1\s+from\s+[A-Za-z_][A-Za-z0-9_]*\s+limit\s+1\s*",
            masked_sql,
            re.IGNORECASE,
        )
    )


def _is_boundary_aggregate(masked_sql: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*select\s+(?:max|min)\s*\(\s*"
            r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*\s*\)"
            r"(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?\s+from\s+"
            r"[A-Za-z_][A-Za-z0-9_]*(?:\s+limit\s+\d+)?\s*",
            masked_sql,
            re.IGNORECASE,
        )
    )


def analyze_query_shape(sql: str) -> dict[str, int | bool | None]:
    """分析最外层 SELECT 的限制与排序，不受字符串或子查询干扰。"""
    normalized = _strip_trailing_semicolon(sql)
    masked = _mask_string_literals(normalized)
    top_level = _mask_nested_parentheses(masked)

    if re.search(r"\blimit\s+\d+\s*,", top_level, re.IGNORECASE):
        raise RuntimeError("不支持 LIMIT offset,count；请使用 LIMIT count OFFSET offset")

    match = LIMIT_RE.search(top_level)
    return {
        "top_level_limit": int(match.group(1)) if match else None,
        "top_level_offset": int(match.group(2)) if match and match.group(2) else 0,
        "has_top_level_order_by": bool(ORDER_BY_RE.search(top_level)),
        "has_top_level_where": bool(WHERE_RE.search(top_level)),
    }


def top_level_order_columns(sql: str) -> list[str]:
    """解析精确分页允许的简单顶层 ORDER BY 字段。"""
    return [column for column, _direction in top_level_order_terms(sql)]


def top_level_order_terms(sql: str) -> list[tuple[str, str]]:
    """解析精确分页允许的简单顶层 ORDER BY 字段和方向。"""
    normalized = _strip_trailing_semicolon(sql)
    top_level = _mask_nested_parentheses(_mask_string_literals(normalized))
    match = re.search(r"\border\s+by\s+(.+?)(?:\blimit\b|$)", top_level, re.IGNORECASE)
    if not match:
        return []

    terms: list[tuple[str, str]] = []
    for item in match.group(1).split(","):
        item_match = re.fullmatch(
            r"\s*(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)"
            r"(?:\s+(asc|desc))?\s*",
            item,
            re.IGNORECASE,
        )
        if not item_match:
            raise RuntimeError(
                "完整查询的 ORDER BY 仅支持简单字段或别名；请先在 SELECT 中定义别名再排序"
            )
        terms.append((item_match.group(1), (item_match.group(2) or "ASC").upper()))
    return terms


def referenced_tables(sql: str) -> list[str]:
    """返回 SQL 中 FROM/JOIN 引用的简单表名。"""
    normalized = _strip_trailing_semicolon(sql)
    return _extract_table_references(_mask_string_literals(normalized).lower())


def _mask_nested_parentheses(sql: str) -> str:
    """保留最外层 SQL，使用空格遮蔽括号及括号内部内容。"""
    chars: list[str] = []
    depth = 0
    for char in sql:
        if char == "(":
            depth += 1
            chars.append(" ")
        elif char == ")":
            depth = max(0, depth - 1)
            chars.append(" ")
        elif depth:
            chars.append(" ")
        else:
            chars.append(char)
    return "".join(chars)


def _validate_table_references(
    masked_sql: str,
    command: str,
    allowed_tables: set[str] | None,
) -> None:
    normalized_allowed = (
        {table.lower() for table in allowed_tables}
        if allowed_tables is not None
        else None
    )

    if command == "show":
        _validate_show_sql(masked_sql, normalized_allowed)
        return

    if normalized_allowed is None:
        return

    if command in {"describe", "desc"}:
        match = re.match(r"\s*(?:describe|desc)\s+([A-Za-z_][A-Za-z0-9_]*)\b", masked_sql)
        if not match:
            raise RuntimeError("DESCRIBE 语句缺少表名")
        _validate_known_table(match.group(1), normalized_allowed)
        return

    for table_name in _extract_table_references(masked_sql):
        _validate_known_table(table_name, normalized_allowed)


def _validate_show_sql(masked_sql: str, allowed_tables: set[str] | None) -> None:
    compact = " ".join(masked_sql.split())
    if compact.startswith("show tables") or compact.startswith("show full tables"):
        return

    index_match = re.fullmatch(
        r"show\s+index\s+from\s+([A-Za-z_][A-Za-z0-9_]*)",
        compact,
    )
    if index_match:
        if allowed_tables is None:
            raise RuntimeError("SHOW INDEX 必须使用本地 schema 白名单")
        _validate_known_table(index_match.group(1), allowed_tables)
        return

    match = re.match(
        r"show\s+(?:columns|fields)\s+(?:from|in)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
        compact,
    )
    if match:
        if allowed_tables is not None:
            _validate_known_table(match.group(1), allowed_tables)
        return

    raise RuntimeError("SHOW 仅允许 TABLES、COLUMNS/FIELDS 或白名单表的 INDEX 元数据查询")


def _extract_table_references(masked_sql: str) -> list[str]:
    references = [
        match.group(1)
        for match in re.finditer(
            r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            masked_sql,
        )
    ]

    for match in re.finditer(
        r"\bfrom\s+(.+?)(?:\bwhere\b|\bgroup\b|\border\b|\blimit\b|\bhaving\b|\bunion\b|$)",
        masked_sql,
    ):
        segment = match.group(1).strip()
        if segment.startswith("("):
            continue
        references.extend(
            comma_match.group(1)
            for comma_match in re.finditer(r",\s*([A-Za-z_][A-Za-z0-9_]*)\b", segment)
        )

    return references


def _validate_known_table(table_name: str, allowed_tables: set[str]) -> None:
    validate_identifier(table_name)
    if table_name.lower() not in allowed_tables:
        raise RuntimeError(f"表不在 schema 白名单中: {table_name}")


def _mask_string_literals(sql: str) -> str:
    """把字符串字面量替换为空格，避免关键字误判。"""
    chars: list[str] = []
    quote: str | None = None
    escaped = False

    for char in sql:
        if quote:
            if escaped:
                escaped = False
                chars.append(" ")
                continue
            if char == "\\":
                escaped = True
                chars.append(" ")
                continue
            if char == quote:
                quote = None
            chars.append(" ")
            continue

        if char in {"'", '"', "`"}:
            quote = char
            chars.append(" ")
        else:
            chars.append(char)

    return "".join(chars)
