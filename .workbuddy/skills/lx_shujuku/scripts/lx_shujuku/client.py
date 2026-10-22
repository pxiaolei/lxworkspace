"""
lx_shujuku 公司数据库客户端

提供对 dataReporting 平台数据库的完整访问能力：
- 自动登录鉴权（Token 缓存/过期重登）
- 只读 SQL 查询执行
- 表结构浏览
- 常用业务查询模板

认证流程：
1. POST /dataReporting/user/login → 获取 token
2. 后续请求 Header: token: {token}
3. 返回 401 时自动重新登录
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError

from .operator_brand import build_mabiao_mapping, normalize_operator_brand_rows
from .query_policy import (
    analyze_query_shape,
    check_performance_gates, evaluate_explain_plan, load_perf_gate_config, should_run_explain,
    ensure_readonly_sql,
    referenced_tables,
    top_level_order_terms,
    validate_identifier,
    validate_limit,
)
from .schema import SchemaCatalog

logger = logging.getLogger(__name__)
_PERF_GATE_CONFIG_CACHE: dict[str, Any] = {}
MAX_FULL_QUERY_PAGES = 20
UNBOUNDED_FULL_QUERY_ALLOWLIST = {"operator_brand"}


class DataReportingClient:
    """dataReporting 平台数据库客户端"""

    def __init__(
        self,
        config_path: Optional[str] = None,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: Optional[int] = None,
        default_limit: Optional[int] = None,
        max_limit: Optional[int] = None,
    ) -> None:
        """
        初始化客户端。

        优先级：直接传参 > config/fog_config.yaml
        """
        # 解析配置文件
        if config_path is None:
            config_path = self._resolve_config_path()
        try:
            self._config = self._load_config(config_path) if config_path else {}
        except FileNotFoundError:
            logger.warning(f"配置文件未找到: {config_path}，请确认是否已创建")
            self._config = {}

        self.base_url = base_url or self._config.get("base_url", "http://datareporting.sfczhushou.com")
        self.username = username or self._config.get("username", "")
        self.password = password or self._config.get("password", "")
        self.max_limit = _to_positive_int(
            max_limit if max_limit is not None else self._config.get("max_limit", 1000),
            "max_limit",
        )
        self.timeout = _to_positive_int(
            timeout if timeout is not None else self._config.get("timeout", 30),
            "timeout",
        )
        self.default_limit = validate_limit(
            default_limit if default_limit is not None else self._config.get("default_limit", 100),
            self.max_limit,
        )
        self.schema = SchemaCatalog.from_skill_root(self._skill_dir())

        if not self.username or not self.password:
            raise RuntimeError(
                "未配置用户名或密码。请编辑 config/fog_config.yaml 的 lx_shujuku.api 段，"
                "或通过 DataReportingClient(username=..., password=...) 传参。"
            )

        self._token: Optional[str] = None
        self._token_expiry: float = 0.0
        self._token_lifetime: float = 3600.0  # 假设 token 有效期 1 小时

    # ---- 配置加载 ----

    @staticmethod
    def _skill_dir() -> Path:
        """返回 Skill 根目录。"""
        return Path(__file__).resolve().parents[2]

    def _resolve_config_path(self) -> Optional[str]:
        """解析配置路径，使用项目根目录 config/fog_config.yaml。"""
        fog_config_path = self._project_root() / "config" / "fog_config.yaml"
        return str(fog_config_path)

    @staticmethod
    def _project_root() -> Path:
        """返回 FOG 项目根目录。"""
        for candidate in [Path(__file__).resolve(), *Path(__file__).resolve().parents]:
            if (candidate / "config" / "fog_config.yaml").exists():
                return candidate
            if (candidate / ".workbuddy").is_dir() and (candidate / "config").is_dir():
                return candidate
        return DataReportingClient._skill_dir().parents[1]

    @staticmethod
    def _load_config(path: str) -> dict:
        """加载 YAML 配置文件（简易解析，无第三方依赖）"""
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        data = DataReportingClient._parse_simple_yaml(content)
        if "lx_shujuku" in data and isinstance(data["lx_shujuku"], dict):
            section = data["lx_shujuku"]
            api = section.get("api") if isinstance(section.get("api"), dict) else {}
            query = section.get("query") if isinstance(section.get("query"), dict) else {}
            result = {
                "base_url": api.get("base_url", "http://datareporting.sfczhushou.com"),
                "username": api.get("username", ""),
                "password": api.get("password", ""),
            }
            if "timeout" in section:
                result["timeout"] = section["timeout"]
            if "default_limit" in query:
                result["default_limit"] = query["default_limit"]
            if "max_limit" in query:
                result["max_limit"] = query["max_limit"]
            return result
        return data

    @staticmethod
    def _parse_simple_yaml(content: str) -> dict[str, Any]:
        """
        简易 YAML 解析器（仅支持 FOG 配置常用映射格式，不依赖 PyYAML）。
        """
        result: dict[str, Any] = {}
        stack: list[tuple[int, dict[str, Any]]] = [(-1, result)]

        for line in content.split("\n"):
            content_line = _strip_yaml_line_comment(line)
            stripped = content_line.strip()
            # 跳过注释和空行
            if not stripped or stripped.startswith("#"):
                continue

            if stripped.startswith("- ") or ":" not in stripped:
                continue

            indent = len(content_line) - len(content_line.lstrip(" "))
            key, value = _split_yaml_key_value(stripped)
            while stack and indent <= stack[-1][0]:
                stack.pop()
            parent = stack[-1][1]
            if value == "":
                child: dict[str, Any] = {}
                parent[key] = child
                stack.append((indent, child))
            else:
                parent[key] = _parse_yaml_scalar(value)

        # 扁平化处理：如果 api 存在，提取其子属性到顶层
        if "api" in result and isinstance(result["api"], dict):
            api_config = result["api"]
            result["base_url"] = api_config.get("base_url", result.get("base_url"))
            result["username"] = api_config.get("username", result.get("username"))
            result["password"] = api_config.get("password", result.get("password"))

        return result

    # ---- 鉴权 ----

    def login(self) -> str:
        """
        执行登录，获取 Token。

        Returns:
            token 字符串

        Raises:
            RuntimeError: 登录失败时抛出
        """
        url = f"{self.base_url}/dataReporting/user/login"
        payload = json.dumps({
            "username": self.username,
            "password": self.password,
        }).encode("utf-8")

        try:
            resp = self._request(url, payload, use_auth=False)
            data = json.loads(resp)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"登录响应解析失败: {e}") from e

        if data.get("code") != 200:
            raise RuntimeError(f"登录失败: {data.get('message', '未知错误')}")

        token = data.get("data", {}).get("token", "")
        if not token:
            raise RuntimeError("登录成功但未返回 token")

        self._token = token
        self._token_expiry = time.time() + self._token_lifetime
        logger.info("登录成功，Token 已缓存")
        return token

    def ensure_token(self) -> str:
        """确保 token 有效，必要时重新登录"""
        if self._token and time.time() < self._token_expiry:
            return self._token
        return self.login()

    # ---- HTTP 请求 ----

    def _request(
        self, url: str, payload: bytes, use_auth: bool = True
    ) -> bytes:
        """
        发送 HTTP POST 请求。

        Args:
            url: 请求地址
            payload: JSON 请求体（bytes）
            use_auth: 是否携带 token

        Returns:
            响应体 bytes

        Raises:
            RuntimeError: 网络错误或 HTTP 错误时抛出
        """
        headers = {"Content-Type": "application/json"}
        if use_auth:
            headers["token"] = self.ensure_token()

        req = Request(url, data=payload, headers=headers, method="POST")

        # 使用无代理 opener 绕过本地 HTTP 代理（服务器为内网直连）
        opener = build_opener(ProxyHandler({}))

        try:
            with opener.open(req, timeout=self.timeout) as response:
                return response.read()
        except HTTPError as e:
            if e.code == 401 and use_auth:
                # Token 过期，重新登录后重试一次
                logger.warning("Token 过期，重新登录...")
                self._token = None
                self._token_expiry = 0.0
                headers["token"] = self.ensure_token()
                req = Request(url, data=payload, headers=headers, method="POST")
                with opener.open(req, timeout=self.timeout) as response:
                    return response.read()
            raise RuntimeError(f"HTTP {e.code}: {e.reason}") from e
        except URLError as e:
            raise RuntimeError(f"网络连接失败: {e.reason}") from e

    # ---- SQL 安全工具 ----

    @staticmethod
    def _esc(value: str) -> str:
        """
        转义 SQL 字符串参数中的特殊字符，防止基础 SQL 注入。

        注意：这是简易转义，生产环境应使用参数化查询。
        本工具仅允许 SELECT 查询，风险可控。
        """
        return value.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')

    # ---- SQL 查询 ----

    def prepare_sql(
        self,
        sql: str,
        enforce_table_whitelist: bool = True,
        default_limit: int | None = None,
        append_default_limit: bool = True,
    ) -> str:
        """执行前标准化并校验 SQL。"""
        allowed_tables = self.schema.table_names if enforce_table_whitelist else None
        return ensure_readonly_sql(
            sql,
            default_limit=self.default_limit if default_limit is None else default_limit,
            max_limit=self.max_limit,
            allowed_tables=allowed_tables,
            append_default_limit=append_default_limit,
        )

    def _perf_gate_config(self) -> dict[str, Any]:
        if not _PERF_GATE_CONFIG_CACHE:
            _PERF_GATE_CONFIG_CACHE.update(load_perf_gate_config())
        return _PERF_GATE_CONFIG_CACHE



    def _enforce_performance_gates(
        self,
        safe_sql: str,
    ) -> None:
        """执行前性能门禁。enforce 模式拦截，warn 模式只告警。"""
        cfg = self._perf_gate_config()
        violations = check_performance_gates(safe_sql, cfg)

        if should_run_explain(safe_sql, cfg):
            try:
                plan = self._execute_prepared_sql(f"EXPLAIN {safe_sql}")
            except Exception as exc:  # noqa: BLE001 - EXPLAIN 失败必须阻止真实执行
                violations.append(
                    f"性能门禁 4：无法取得执行计划（{exc}），已停止本次查询。"
                )
            else:
                violations.extend(evaluate_explain_plan(plan, cfg))

        if not violations:
            return
        if str(cfg.get("mode") or "enforce").lower() == "warn":
            for item in violations:
                logger.warning("性能门禁(warn)：%s", item)
            return
        raise RuntimeError(
            "SQL 未通过公司库性能门禁，已停止执行：\n  - "
            + "\n  - ".join(violations)
            + "\n请先缩小业务范围；必要的例外交维护者联系技术/DBA核对。"
        )


    def execute(
        self,
        sql: str,
        auto_retry: bool = True,
        enforce_table_whitelist: bool = True,
        default_limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        执行只读 SQL 查询并返回结果。

        Args:
            sql: SQL 语句，只允许只读语句
            auto_retry: Token 过期时是否自动重试

        Returns:
            查询结果列表，每行为一个 dict

        Raises:
            RuntimeError: 查询失败时抛出
        """
        safe_sql = self.prepare_sql(
            sql,
            enforce_table_whitelist=enforce_table_whitelist,
            default_limit=default_limit,
        )
        self._enforce_performance_gates(safe_sql)
        return self._execute_prepared_sql(safe_sql)

    def execute_audited(
        self,
        sql: str,
        question: str = "",
        metric: str = "",
        enforce_table_whitelist: bool = True,
        default_limit: int | None = None,
    ) -> dict[str, Any]:
        """执行查询并返回可用于文档生成的结构化证据包。"""
        tz = timezone(timedelta(hours=8))
        started_at = datetime.now(tz)
        started_monotonic = time.monotonic()
        metric_contract = self._metric_contract(metric)
        safe_sql = self.prepare_sql(
            sql,
            enforce_table_whitelist=enforce_table_whitelist,
            default_limit=default_limit,
        )
        self._enforce_performance_gates(safe_sql)
        rows = self._execute_prepared_sql(safe_sql)
        duration_ms = round((time.monotonic() - started_monotonic) * 1000, 3)
        shape = analyze_query_shape(safe_sql)
        effective_limit = shape["top_level_limit"]
        possible_truncation = bool(
            effective_limit is not None and len(rows) >= effective_limit
        )
        is_complete: bool | None = None if possible_truncation else True
        warnings = self._query_warnings(safe_sql)
        if possible_truncation:
            warnings.append("返回行数已命中顶层 LIMIT；结果可能被截断，不能声明为完整结果")

        package = {
            "type": "lx_shujuku.query_run",
            "version": 2,
            "executed_at": started_at.isoformat(),
            "duration_ms": duration_ms,
            "database": "dataReporting",
            "base_url": self.base_url,
            "question": question,
            "metric": metric,
            "sql": sql,
            "safe_sql": safe_sql,
            "result_mode": "limited",
            "requested_limit": default_limit,
            "effective_limit": effective_limit,
            "row_count": len(rows),
            "returned_rows": len(rows),
            "total_rows": None,
            "is_complete": is_complete,
            "possible_truncation": possible_truncation,
            "order_by_present": shape["has_top_level_order_by"],
            "rows": rows,
            "result_sha256": self._rows_sha256(rows),
            "schema_snapshot": self._schema_snapshot(),
            "metric_contract": metric_contract,
            "verification": {
                "method": "limit_boundary",
                "passed": not possible_truncation,
            },
            "warnings": warnings,
        }
        return package

    def execute_full_audited(
        self,
        sql: str,
        question: str = "",
        metric: str = "",
        enforce_table_whitelist: bool = True,
        page_size: int = 500,
        max_rows: int = 10000,
    ) -> dict[str, Any]:
        """通过稳定游标、单遍分页和前后计数返回有边界的完整结果。"""
        page_size = validate_limit(page_size, self.max_limit)
        max_rows = _to_positive_int(max_rows, "max_rows")
        metric_contract = self._metric_contract(metric)
        tz = timezone(timedelta(hours=8))
        started_at = datetime.now(tz)
        started_monotonic = time.monotonic()
        safe_sql = self.prepare_sql(
            sql,
            enforce_table_whitelist=enforce_table_whitelist,
            append_default_limit=False,
        )
        self._enforce_performance_gates(safe_sql)
        if not safe_sql.lstrip().lower().startswith("select"):
            raise RuntimeError("完整查询仅支持 SELECT")

        shape = analyze_query_shape(safe_sql)
        if shape["top_level_limit"] is not None:
            raise RuntimeError("完整查询不能包含顶层 LIMIT；请交给 --full 分页")
        unbounded_tables = set(referenced_tables(safe_sql)) - UNBOUNDED_FULL_QUERY_ALLOWLIST
        if unbounded_tables and not shape["has_top_level_where"]:
            tables = ", ".join(sorted(unbounded_tables))
            raise RuntimeError(
                f"完整查询必须包含顶层 WHERE 业务范围；无界表: {tables}"
            )
        order_terms = top_level_order_terms(safe_sql)
        order_columns = [column for column, _direction in order_terms]
        count_before = self._count_query_rows(safe_sql)
        if count_before > max_rows:
            raise RuntimeError(
                f"完整查询预计返回 {count_before} 行，超过 --max-rows {max_rows}；"
                "请缩小筛选范围或显式提高上限"
            )
        if count_before > 1 and not order_columns:
            raise RuntimeError("多行完整查询必须使用简单字段组成的顶层 ORDER BY，保证分页顺序稳定")
        required_pages = (count_before + page_size - 1) // page_size
        if required_pages > MAX_FULL_QUERY_PAGES:
            raise RuntimeError(
                f"完整查询需要 {required_pages} 页，超过完整查询页数上限 "
                f"{MAX_FULL_QUERY_PAGES}；请缩小日期或业务范围"
            )

        result_rows, page_count = self._fetch_full_pages(
            safe_sql, count_before, page_size, order_terms
        )
        count_after = self._count_query_rows(safe_sql)
        result_hash = self._rows_sha256(result_rows)
        verified = count_before == count_after == len(result_rows)
        if not verified:
            raise RuntimeError(
                "完整查询单遍读回行数或前后计数不一致；数据可能在查询期间变化，"
                "本次不输出为准确结果，请稍后重试"
            )

        duration_ms = round((time.monotonic() - started_monotonic) * 1000, 3)
        warnings = self._query_warnings(safe_sql)
        if order_columns:
            warnings.append(
                "查询服务不提供事务快照；已用唯一稳定游标、单遍读取和前后计数验证完整性；"
                "无法排除查询期间非排序字段原地变更"
            )
            verification_method = "ordered_single_pass_keyset_with_count"
        else:
            warnings.append(
                "查询服务不提供事务快照；单行结果已用单次读取和前后计数验证完整性；"
                "无法排除查询期间值原地变更"
            )
            verification_method = "single_row_single_read_with_count"
        return {
            "type": "lx_shujuku.query_run",
            "version": 3,
            "executed_at": started_at.isoformat(),
            "duration_ms": duration_ms,
            "database": "dataReporting",
            "base_url": self.base_url,
            "question": question,
            "metric": metric,
            "sql": sql,
            "safe_sql": safe_sql,
            "result_mode": "full",
            "requested_limit": None,
            "effective_limit": None,
            "row_count": len(result_rows),
            "returned_rows": len(result_rows),
            "total_rows": count_before,
            "is_complete": True,
            "possible_truncation": False,
            "order_by_present": bool(order_columns),
            "order_columns": order_columns,
            "rows": result_rows,
            "result_sha256": result_hash,
            "schema_snapshot": self._schema_snapshot(),
            "metric_contract": metric_contract,
            "verification": {
                "method": verification_method,
                "passed": True,
                "count_before": count_before,
                "count_after": count_after,
                "verification_passes": 1,
                "page_count": page_count,
                "cursor_mode": "keyset" if order_columns else "single_row",
                "max_pages": MAX_FULL_QUERY_PAGES,
            },
            "warnings": warnings,
        }

    def _count_query_rows(self, safe_sql: str) -> int:
        count_sql = (
            "SELECT COUNT(*) AS __lx_total_rows FROM "
            f"({safe_sql}) AS __lx_full_count LIMIT 1"
        )
        rows = self._execute_prepared_sql(count_sql)
        if not rows or "__lx_total_rows" not in rows[0]:
            raise RuntimeError("完整查询计数失败：服务端未返回 __lx_total_rows")
        try:
            return int(rows[0]["__lx_total_rows"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("完整查询计数失败：返回值不是整数") from exc

    def _fetch_full_pages(
        self,
        safe_sql: str,
        total_rows: int,
        page_size: int,
        order_terms: list[tuple[str, str]],
    ) -> tuple[list[dict[str, Any]], int]:
        rows: list[dict[str, Any]] = []
        page_count = 0
        seen: set[tuple[str, ...]] = set()
        previous_key: tuple[Any, ...] | None = None
        while len(rows) < total_rows:
            if previous_key is None:
                page_sql = f"{safe_sql} LIMIT {page_size}"
            else:
                page_sql = self._keyset_page_sql(safe_sql, order_terms, previous_key, page_size)
            page = self._execute_prepared_sql(page_sql)
            if not page:
                raise RuntimeError(f"完整查询游标在第 {page_count + 1} 页提前结束")
            page_count += 1
            if len(rows) + len(page) > total_rows:
                raise RuntimeError("完整查询分页返回行数超过查询前计数；数据可能在查询期间变化")
            for row in page:
                index = len(rows) + 1
                missing = [column for column, _direction in order_terms if column not in row]
                if missing:
                    raise RuntimeError(
                        f"完整查询第 {index} 行缺少 ORDER BY 字段: {', '.join(missing)}"
                    )
                raw_key = tuple(row[column] for column, _direction in order_terms)
                if any(value is None for value in raw_key):
                    raise RuntimeError("完整查询的 ORDER BY 字段不能包含 NULL；请改用非空稳定键")
                canonical_key = tuple(self._canonical_value(value) for value in raw_key)
                if canonical_key in seen:
                    raise RuntimeError(
                        "完整查询的 ORDER BY 字段组合不唯一；请追加唯一键或足以唯一定位结果行的字段"
                    )
                if previous_key is not None and not self._key_advances(previous_key, raw_key, order_terms):
                    raise RuntimeError("完整查询结果未按 ORDER BY 严格递增；无法安全推进游标")
                seen.add(canonical_key)
                previous_key = raw_key
                rows.append(row)

        if len(rows) != total_rows:
            raise RuntimeError(f"完整查询分页行数不一致：预期 {total_rows}，实际 {len(rows)}")
        return rows, page_count

    @classmethod
    def _keyset_page_sql(
        cls,
        safe_sql: str,
        order_terms: list[tuple[str, str]],
        previous_key: tuple[Any, ...],
        page_size: int,
    ) -> str:
        alias = "__lx_full_page"
        branches: list[str] = []
        equals: list[str] = []
        for (column, direction), value in zip(order_terms, previous_key):
            qualified = f"{alias}.{column}"
            operator = ">" if direction == "ASC" else "<"
            literal = cls._sql_literal(value)
            branches.append("(" + " AND ".join([*equals, f"{qualified} {operator} {literal}"]) + ")")
            equals.append(f"{qualified} = {literal}")
        order_by = ", ".join(f"{alias}.{column} {direction}" for column, direction in order_terms)
        predicate = " OR ".join(branches)
        return (
            f"SELECT * FROM ({safe_sql}) AS {alias} WHERE {predicate} "
            f"ORDER BY {order_by} LIMIT {page_size}"
        )

    @classmethod
    def _key_advances(
        cls,
        previous_key: tuple[Any, ...],
        current_key: tuple[Any, ...],
        order_terms: list[tuple[str, str]],
    ) -> bool:
        for previous, current, (_column, direction) in zip(previous_key, current_key, order_terms):
            comparison = cls._compare_values(previous, current)
            if comparison == 0:
                continue
            return comparison < 0 if direction == "ASC" else comparison > 0
        return False

    @classmethod
    def _compare_values(cls, left: Any, right: Any) -> int:
        try:
            return (left > right) - (left < right)
        except TypeError:
            left_value = cls._canonical_value(left)
            right_value = cls._canonical_value(right)
            return (left_value > right_value) - (left_value < right_value)

    @staticmethod
    def _sql_literal(value: Any) -> str:
        if value is None:
            raise RuntimeError("完整查询游标不能使用 NULL")
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, Decimal)):
            return str(value)
        if isinstance(value, float):
            if not math.isfinite(value):
                raise RuntimeError("完整查询游标不能使用 NaN 或 Infinity")
            return repr(value)
        if isinstance(value, (date, datetime)):
            value = value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
        text = str(value).replace("\\", "\\\\").replace("'", "''")
        return f"'{text}'"

    @staticmethod
    def _canonical_value(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @classmethod
    def _rows_sha256(cls, rows: list[dict[str, Any]]) -> str:
        payload = json.dumps(
            rows,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _schema_snapshot(self) -> dict[str, Any]:
        path = self._skill_dir() / "assets" / "schema.json"
        raw = path.read_bytes()
        schema_data = json.loads(raw.decode("utf-8"))
        return {
            "generated_at": schema_data.get("generated_at"),
            "table_count": schema_data.get("table_count", len(schema_data.get("tables", []))),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    def _metric_contract(self, metric: str) -> dict[str, Any] | None:
        if not metric:
            return None
        path = self._skill_dir() / "references" / "metrics_catalog.json"
        raw = path.read_bytes()
        catalog = json.loads(raw.decode("utf-8"))
        metrics = catalog.get("metrics", {})
        if metric not in metrics:
            available = ", ".join(sorted(metrics))
            raise RuntimeError(f"未知指标口径 ID: {metric}；可用指标: {available}")
        definition = metrics[metric]
        return {
            "id": metric,
            "display_name": definition.get("display_name", ""),
            "catalog_version": catalog.get("version"),
            "catalog_updated_at": catalog.get("updated_at"),
            "catalog_sha256": hashlib.sha256(raw).hexdigest(),
        }

    def _execute_prepared_sql(self, safe_sql: str) -> list[dict[str, Any]]:
        """执行已经通过只读策略校验的 SQL。"""
        url = f"{self.base_url}/dataReporting/sql-query/execute"
        payload = json.dumps({"sql": safe_sql}).encode("utf-8")

        resp_text = self._request(url, payload).decode("utf-8")
        try:
            data = json.loads(resp_text)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"查询响应解析失败: {e}\n原始响应: {resp_text[:500]}") from e

        if data.get("code") != 200:
            raise RuntimeError(f"查询失败: {data.get('message', '未知错误')}")

        return data.get("data", [])

    @staticmethod
    def _query_warnings(sql: str) -> list[str]:
        warnings: list[str] = []
        compact = " ".join(sql.lower().split())
        if compact.startswith("select *"):
            warnings.append("查询包含 SELECT *；生成文档时建议只取必要字段")
        return warnings

    def execute_one(self, sql: str) -> Optional[dict[str, Any]]:
        """执行 SQL 并返回单行结果（无结果时返回 None）"""
        rows = self.execute(sql)
        return rows[0] if rows else None

    # ---- 表结构浏览 ----

    def list_tables(self) -> list[dict[str, str]]:
        """
        列出数据库中所有表。

        Returns:
            [{"name": "表名", "comment": "注释"}, ...]
        """
        rows = self.execute("SHOW TABLES", enforce_table_whitelist=False)
        by_name: dict[str, dict[str, str]] = {}
        for row in rows:
            # MySQL: {"Tables_in_datareporting": "xxx", "TABLE_COMMENT": "xxx"}
            table_name = row.get("Tables_in_datareporting", "")
            comment = row.get("TABLE_COMMENT", "")
            if table_name:
                by_name[table_name] = {"name": table_name, "comment": comment}

        # 公司查询服务还有一层服务端允许表列表。历史上 SHOW TABLES
        # 可能只返回旧子集，所以这里合并服务端错误信息中的允许表名。
        for table_name in self._server_allowed_table_names():
            by_name.setdefault(table_name, {"name": table_name, "comment": ""})

        return [by_name[name] for name in sorted(by_name)]

    def describe(self, table_name: str) -> list[dict[str, Any]]:
        """
        查看表结构。

        Returns:
            [{"field": "字段名", "type": "类型", "null": "YES/NO",
              "key": "PRI/MUL/...", "default": "默认值", "comment": "注释"}, ...]
        """
        safe_table = self.schema.validate_table_name(table_name)
        rows = self.execute(f"DESCRIBE {safe_table}")
        return self._normalize_describe_rows(rows)

    def describe_online(self, table_name: str) -> list[dict[str, Any]]:
        """查看线上表结构，不要求表已存在于本地 schema 白名单。"""
        from .query_policy import validate_identifier

        safe_table = validate_identifier(table_name)
        rows = self.execute(
            f"DESCRIBE {safe_table}",
            enforce_table_whitelist=False,
        )
        return self._normalize_describe_rows(rows)

    def show_indexes(self, table_name: str) -> list[dict[str, Any]]:
        """查看本地 schema 白名单表的索引元数据。"""
        safe_table = self.schema.validate_table_name(table_name)
        rows = self.execute(f"SHOW INDEX FROM {safe_table}")

        def optional_int(value: Any) -> int | None:
            if value in (None, ""):
                return None
            return int(value)

        normalized: list[dict[str, Any]] = []
        for row in rows:
            visible = row.get("Visible")
            normalized.append(
                {
                    "table": row.get("Table", safe_table),
                    "unique": int(row.get("Non_unique", 1)) == 0,
                    "key_name": row.get("Key_name", ""),
                    "seq_in_index": int(row.get("Seq_in_index", 0)),
                    "column_name": row.get("Column_name"),
                    "collation": row.get("Collation"),
                    "cardinality": optional_int(row.get("Cardinality")),
                    "sub_part": optional_int(row.get("Sub_part")),
                    "packed": row.get("Packed"),
                    "nullable": str(row.get("Null", "")).upper() == "YES",
                    "index_type": row.get("Index_type", ""),
                    "comment": row.get("Comment", ""),
                    "index_comment": row.get("Index_comment", ""),
                    "visible": None
                    if visible in (None, "")
                    else str(visible).upper() != "NO",
                    "expression": row.get("Expression"),
                }
            )

        return sorted(
            normalized,
            key=lambda item: (str(item["key_name"]), int(item["seq_in_index"])),
        )

    @staticmethod
    def _normalize_describe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "field": r.get("Field", ""),
                "type": r.get("Type", ""),
                "null": r.get("Null", ""),
                "key": r.get("Key", ""),
                "default": r.get("Default"),
                "comment": r.get("COLUMN_COMMENT", ""),
            }
            for r in rows
        ]

    def count(
        self,
        table_name: str,
        where: str = "",
        enforce_table_whitelist: bool = True,
    ) -> int:
        """查询表记录数"""
        safe_table = (
            self.schema.validate_table_name(table_name)
            if enforce_table_whitelist
            else validate_identifier(table_name)
        )
        sql = f"SELECT COUNT(*) AS cnt FROM {safe_table}"
        if where:
            sql += f" WHERE {where}"
        rows = self.execute(sql, enforce_table_whitelist=enforce_table_whitelist)
        row = rows[0] if rows else None
        return int(row["cnt"]) if row else 0

    # ---- 常用业务查询模板 ----

    def query_by_brand_date(
        self, table: str, brand: str, date: str, limit: int = 50
    ) -> list[dict]:
        """已禁用：当前活动/卡片表没有以品牌或时间开头的可用索引。"""
        safe_table = self.schema.validate_table_name(table)
        if safe_table not in {"activity_data", "card_data"}:
            raise RuntimeError("按品牌日期模板只支持 activity_data 或 card_data")
        raise RuntimeError(
            f"{safe_table} 当前没有以品牌或时间开头的可用索引；"
            "该模板已失败关闭，请把查询范围和索引需求交技术/DBA核验"
        )

    def query_by_city_date(
        self, table: str, city: str, date: str, limit: int = 50
    ) -> list[dict]:
        """按城市和日期查询（适用于宏鹄系列表）"""
        safe_table = self.schema.validate_table_name(table)
        safe_limit = validate_limit(limit, self.max_limit)
        sql = (
            f"SELECT id, date_day, brand_name, city_name FROM {safe_table} "
            f"WHERE city_name = '{self._esc(city)}' "
            f"AND date_day = '{self._esc(date)}' "
            f"LIMIT {safe_limit}"
        )
        return self.execute(sql)

    def query_activity_by_operator(
        self, operator: str, status: Optional[int] = None, limit: int = 50
    ) -> list[dict]:
        """已禁用：activity_data 没有以运营主体开头的可用索引。"""
        raise RuntimeError(
            "activity_data 当前没有以运营主体开头的可用索引；"
            "该模板已失败关闭，请把查询范围和索引需求交技术/DBA核验"
        )

    def query_coupon_by_product(
        self, product_name: str, limit: int = 50
    ) -> list[dict]:
        """已禁用：商品名称不在卡券表现有复合索引的可搜索前缀中。"""
        raise RuntimeError(
            "honghu_coupon_marketing_data 当前没有以商品名称开头的可用索引；"
            "该模板已失败关闭，请改用品牌、日期、城市和 product_id 范围或交技术/DBA核验"
        )

    def query_capacity_by_brand_date(
        self, brand: str, date: str, city: str = "", limit: int = 50
    ) -> list[dict]:
        """按品牌和日期查询运力数据"""
        safe_limit = validate_limit(limit, self.max_limit)
        sql = (
            "SELECT id, brand_name, date_day, city_name, supplier, online_driver_count, "
            "completion_driver_count, online_duration_hour, service_duration_hour "
            "FROM honghu_capacity_data "
            f"WHERE brand_name = '{self._esc(brand)}' AND date_day = '{self._esc(date)}'"
        )
        if city:
            sql += f" AND city_name = '{self._esc(city)}'"
        sql += f" LIMIT {safe_limit}"
        return self.execute(sql)

    def query_order_by_brand_date(
        self, brand: str, date: str, limit: int = 50
    ) -> list[dict]:
        """按品牌和日期查询订单数据"""
        safe_limit = validate_limit(limit, self.max_limit)
        sql = (
            "SELECT id, brand_name, date_day, city_name, traffic_channel, "
            "passenger_order_count, response_count, completed_order_count, gmv "
            "FROM honghu_order_data "
            f"WHERE brand_name = '{self._esc(brand)}' AND date_day = '{self._esc(date)}' "
            f"LIMIT {safe_limit}"
        )
        return self.execute(sql)

    def query_time_split_by_hour(
        self, brand: str, date: str, city: str = "", limit: int = 50
    ) -> list[dict]:
        """按品牌和日期查询分时明细"""
        safe_limit = validate_limit(limit, self.max_limit)
        sql = (
            "SELECT id, brand_name, date_day, city_name, hour, passenger_order_count, "
            "response_count, completed_order_count, online_driver_count, serving_driver_count, "
            "load_rate, gmv FROM honghu_time_split_data "
            f"WHERE brand_name = '{self._esc(brand)}' AND date_day = '{self._esc(date)}'"
        )
        if city:
            sql += f" AND city_name = '{self._esc(city)}'"
        sql += f" ORDER BY hour LIMIT {safe_limit}"
        return self.execute(sql)

    def query_driver_real_time(
        self, tenant_name: str, date: str, limit: int = 20
    ) -> list[dict]:
        """按租户和日期查询运力实时数据"""
        safe_limit = validate_limit(limit, self.max_limit)
        sql = (
            "SELECT id, tenant_name, datae_column_bc7a384cd7_day_real, "
            "cal_42b6ccdb9832a04000d13c0b00a8f365, cal_7050206267d5d6f983310c0fd15ebc9a, "
            "cal_f26aa9c3cef567bbf2f68eb01a671790, cal_2bf40e137f207dce4e82d799ad25b0ac, "
            "cal_ebfde248b6edb7171b6b34ef0deff019, pull_time, time_period "
            "FROM driver_real_time_data "
            f"WHERE tenant_name = '{self._esc(tenant_name)}' "
            f"AND datae_column_bc7a384cd7_day_real = '{self._esc(date)}' "
            f"LIMIT {safe_limit}"
        )
        return self.execute(sql)

    def query_order_real_time(
        self, tenant_name: str, date: str, limit: int = 20
    ) -> list[dict]:
        """按租户和日期查询订单实时数据"""
        safe_limit = validate_limit(limit, self.max_limit)
        sql = (
            "SELECT id, tenant_name, datae_column_b4276e28f8_day_real, "
            "cal_7202039077, cal_eee801eeed, cal_6bb0fc3c10, cal_7adff81b3c, "
            "cal_8c37dbc1e0, cal_794655f35b, cal_7eee019f5d, pull_time, time_period "
            "FROM order_real_time_data "
            f"WHERE tenant_name = '{self._esc(tenant_name)}' "
            f"AND datae_column_b4276e28f8_day_real = '{self._esc(date)}' "
            f"LIMIT {safe_limit}"
        )
        return self.execute(sql)

    def query_operator_brands(self, operator: str) -> list[dict]:
        """兼容旧方法：查询运营主体下的所有品牌-城市组合。"""
        return self.get_operator_brands(operator=operator)

    def get_operator_brands(
        self,
        operator: str = "",
        brand: str = "",
        city: str = "",
        limit: int = 1000,
    ) -> list[dict[str, str]]:
        """查询 operator_brand，并返回稳定的中英文键。"""
        safe_limit = validate_limit(limit, self.max_limit)
        conditions = []
        if operator:
            conditions.append(f"operator_entity = '{self._esc(operator)}'")
        if brand:
            conditions.append(f"brand_name = '{self._esc(brand)}'")
        if city:
            conditions.append(f"city_name = '{self._esc(city)}'")
        where = " AND ".join(conditions) if conditions else "1=1"
        sql = (
            "SELECT operator_entity, brand_name, city_name, contact_person, is_opening, open_city_date "
            f"FROM operator_brand WHERE {where} "
            f"ORDER BY operator_entity, brand_name, city_name LIMIT {safe_limit}"
        )
        return normalize_operator_brand_rows(self.execute(sql))

    def load_mabiao_mapping(self) -> dict[str, Any]:
        """返回兼容本地 Excel 码表的映射结构。"""
        return build_mabiao_mapping(self.get_operator_brands(limit=self.max_limit))

    def query_tr_config(self, date: str = "", brand: str = "", city: str = "") -> list[dict]:
        """查询品牌城市 TR 配置"""
        if not date:
            raise RuntimeError("TR 配置查询必须提供日期")
        try:
            safe_date = datetime.strptime(date, "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise RuntimeError("日期必须使用 YYYY-MM-DD") from exc
        if not brand and not city:
            raise RuntimeError("TR 配置查询必须至少提供品牌或城市")
        conditions = [f"date_day = '{safe_date}'"]
        if brand:
            conditions.append(f"brand_name = '{self._esc(brand)}'")
        if city:
            conditions.append(f"city_name = '{self._esc(city)}'")
        where = " AND ".join(conditions)
        return self.execute(
            "SELECT id, date_day, brand_name, city_name, tr_value, tr_total "
            f"FROM brand_city_tr_data WHERE {where}"
        )

    # ---- 工具方法 ----

    def health_check(self) -> dict[str, Any]:
        """健康检查：验证连接和认证"""
        try:
            tables = self.list_tables()
            operator_brand_sample = self.get_operator_brands(limit=5)
            return {
                "status": "ok",
                "base_url": self.base_url,
                "table_count": len(tables),
                "tables": [t["name"] for t in tables],
                "operator_brand_sample_count": len(operator_brand_sample),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def export_schema(self) -> dict[str, Any]:
        """
        导出全量数据库 Schema。

        Returns:
            {
                "generated_at": "ISO时间",
                "tables": [
                    {
                        "name": "表名",
                        "comment": "注释",
                        "columns": [...]
                    },
                    ...
                ]
            }
        """
        from datetime import datetime, timezone, timedelta

        tz = timezone(timedelta(hours=8))
        result = {
            "generated_at": datetime.now(tz).isoformat(),
            "database": "dataReporting",
            "tables": [],
        }

        tables = self.list_tables()
        for t in tables:
            columns = self.describe_online(t["name"])
            result["tables"].append({
                "name": t["name"],
                "comment": t["comment"],
                "column_count": len(columns),
                "columns": columns,
            })

        return result

    def _server_allowed_table_names(self) -> list[str]:
        """
        读取服务端 SQL 查询网关的允许表列表。

        dataReporting 不开放 information_schema；服务端越权错误会返回
        “仅支持以下表: [...]”。这个列表比 SHOW TABLES 更接近真实授权边界。
        """
        probe_table = "lx_shujuku_table_probe_missing"
        try:
            self.execute(
                f"SELECT 1 FROM {probe_table} LIMIT 1",
                enforce_table_whitelist=False,
            )
        except RuntimeError as exc:
            return _parse_allowed_table_names(str(exc))
        return []


# ---- 便捷工厂函数 ----

def create_client(config_path: Optional[str] = None) -> DataReportingClient:
    """
    创建客户端实例（自动从 config/fog_config.yaml 加载配置）。

    Args:
        config_path: 配置文件路径，为 None 时自动查找

    Returns:
        DataReportingClient 实例
    """
    return DataReportingClient(config_path=config_path)


def _parse_allowed_table_names(message: str) -> list[str]:
    match = re.search(r"仅支持以下表:\s*\[([^\]]+)\]", message)
    if not match:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw_name in match.group(1).split(","):
        name = raw_name.strip()
        if not name or name in seen:
            continue
        try:
            result.append(validate_identifier(name))
        except RuntimeError:
            continue
        seen.add(name)
    return result


def _split_yaml_key_value(line: str) -> tuple[str, str]:
    parts = line.split(":", 1)
    key = parts[0].strip()
    value = _strip_inline_comment(parts[1].strip()) if len(parts) > 1 else ""
    return key, value


def _parse_yaml_scalar(value: str) -> Any:
    cleaned = _strip_inline_comment(value).strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1]
    if cleaned.lstrip("-").isdigit():
        return int(cleaned)
    if cleaned.lower() in {"true", "false"}:
        return cleaned.lower() == "true"
    return cleaned


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    escaped = False

    for index, char in enumerate(value):
        if quote:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote:
                quote = None
            continue

        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#":
            return value[:index].rstrip()

    return value.strip()


def _strip_yaml_line_comment(value: str) -> str:
    quote: str | None = None
    escaped = False

    for index, char in enumerate(value):
        if quote:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote:
                quote = None
            continue

        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#":
            return value[:index].rstrip()

    return value.rstrip()


def _to_positive_int(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} 必须是正整数: {value}") from exc
    if result <= 0:
        raise RuntimeError(f"{name} 必须是正整数: {value}")
    return result
