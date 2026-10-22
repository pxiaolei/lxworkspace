"""
lx_shujuku CLI 命令行工具

用法：
  "$WB_PYTHON" db_tools.py list-tables         列出所有表
  "$WB_PYTHON" db_tools.py describe <table>     查看表结构
  "$WB_PYTHON" db_tools.py indexes <table>      查看白名单表索引
  "$WB_PYTHON" db_tools.py catalog             全量表结构概览
  "$WB_PYTHON" db_tools.py query "<sql>"        执行 SQL 查询
  "$WB_PYTHON" db_tools.py count <table>        查询记录数
  "$WB_PYTHON" db_tools.py schema              导出全量 Schema JSON
  "$WB_PYTHON" db_tools.py health              健康检查
  "$WB_PYTHON" db_tools.py operator-brands      查询运营主体-品牌-城市码表

常用业务查询模板：
  "$WB_PYTHON" db_tools.py template capacity-by-brand --brand "方舟行车主" --date "2025-05-12"
  "$WB_PYTHON" db_tools.py template order-by-brand --brand "方舟行车主" --date "2025-05-12"
  "$WB_PYTHON" db_tools.py template time-split --brand "方舟行车主" --date "2025-05-12"
  "$WB_PYTHON" db_tools.py template operator-brands --operator "方舟行（上海）"
  "$WB_PYTHON" db_tools.py template driver-rt --tenant "方舟行（上海）" --date "2025-05-12"
  "$WB_PYTHON" db_tools.py template order-rt --tenant "方舟行（上海）" --date "2025-05-12"
  "$WB_PYTHON" db_tools.py template tr-config --date "2025-05-12" --brand "方舟行车主"
"""

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger("db_tools")


def _setup_path() -> None:
    """确保 scripts 目录在 sys.path 中"""
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)


_setup_path()
from lx_shujuku import DataReportingClient, create_client
from lx_shujuku.schema_tools import (
    diff_schemas,
    load_schema_file,
    render_diff_text,
    render_table_catalog,
)


def cmd_list_tables(client: DataReportingClient) -> None:
    """列出所有表"""
    tables = client.list_tables()
    print(f"\n{'='*60}")
    print(f"数据库: dataReporting ({client.base_url})")
    print(f"共 {len(tables)} 张表")
    print(f"{'='*60}\n")
    for i, t in enumerate(tables, 1):
        print(f"  {i:2d}. {t['name']:<40s} {t['comment']}")


def cmd_describe(client: DataReportingClient, table: str, no_whitelist: bool = False) -> None:
    """查看表结构，支持绕过本地白名单查看新分享表。"""
    if no_whitelist:
        columns = client.describe_online(table)
        source = "（线上实时）"
    else:
        try:
            columns = client.describe(table)
            source = ""
        except RuntimeError as e:
            err = str(e)
            if "表不在 schema 白名单中" in err:
                logger.warning(f"{err}，自动切换为线上实时结构")
                columns = client.describe_online(table)
                source = "（线上实时，未纳入本地白名单）"
            else:
                raise

    print(f"\n{'='*80}")
    print(f"表: {table} ({len(columns)} 个字段) {source}")
    print(f"{'='*80}\n")
    print(f"  {'字段名':<35s} {'类型':<20s} {'键':<5s} {'可为空':<6s} {'注释'}")
    print(f"  {'-'*33} {'-'*18} {'-'*3} {'-'*4} {'-'*20}")
    for col in columns:
        print(
            f"  {col['field']:<35s} "
            f"{col['type']:<20s} "
            f"{col['key']:<5s} "
            f"{col['null']:<6s} "
            f"{col['comment']}"
        )


def cmd_indexes(
    client: DataReportingClient,
    table: str,
    json_output: bool = False,
) -> None:
    """查看白名单表索引元数据。"""
    indexes = client.show_indexes(table)
    payload = {
        "type": "lx_shujuku.index_metadata",
        "version": 1,
        "database": "dataReporting",
        "table": table,
        "queried_at": datetime.now(timezone.utc).isoformat(),
        "index_row_count": len(indexes),
        "indexes": indexes,
    }
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return

    print(f"\n表: {table}（{len(indexes)} 条索引字段记录）")
    if not indexes:
        print("  （未返回索引元数据）")
        return

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in indexes:
        grouped.setdefault(str(item["key_name"]), []).append(item)
    for key_name, items in grouped.items():
        first = items[0]
        uniqueness = "唯一" if first["unique"] else "非唯一"
        index_type = first["index_type"] or "未知类型"
        print(f"  {key_name}（{uniqueness}，{index_type}）")
        for item in items:
            column = item["column_name"] or item["expression"] or "（未知）"
            print(f"    {item['seq_in_index']}. {column}")


def cmd_query(
    client: DataReportingClient,
    sql: str,
    limit: int = 50,
    question: str = "",
    metric: str = "",
    json_output: bool = False,
    output: str = "",
    audit: bool = False,
    no_whitelist: bool = False,
    full: bool = False,
    page_size: int = 500,
    max_rows: int = 10000,
    overwrite: bool = False,
    confirmed: bool = False,
) -> None:
    """执行 SQL 查询并明确标注结果完整性。"""
    if full and not (json_output or output or audit):
        raise RuntimeError("--full 必须同时使用 --json、--audit 或 --output，避免完整结果被终端截断")
    if output:
        _preflight_query_output(output, overwrite, confirmed)

    saved_path: Path | None = None
    if full:
        package = client.execute_full_audited(
            sql,
            question=question,
            metric=metric,
            enforce_table_whitelist=not no_whitelist,
            page_size=page_size,
            max_rows=max_rows,
        )
    else:
        package = client.execute_audited(
            sql,
            question=question,
            metric=metric,
            enforce_table_whitelist=not no_whitelist,
            default_limit=limit,
        )
    rows = package["rows"]
    if output or audit:
        saved_path = _write_query_run(
            package,
            output or "auto",
            overwrite=overwrite,
            confirmed=confirmed,
        )
    if json_output:
        print(json.dumps(package, ensure_ascii=False, indent=2, default=str))
        if saved_path:
            print(f"\n证据包已保存: {saved_path}")
        return
    if full:
        print("\n完整查询已验证")
        print(f"返回/总行数: {package['returned_rows']}/{package['total_rows']}")
        print(f"结果 SHA-256: {package['result_sha256']}")
        print(f"证据包: {saved_path}")
        return

    print(f"\n查询: {package['safe_sql']}")
    print(f"返回: {len(rows)} 行\n")
    if package["is_complete"] is True:
        print("完整性: 已验证")
    elif package["possible_truncation"]:
        print("完整性: 未确认（返回行数命中 LIMIT，结果可能截断）")
    else:
        print("完整性: 未确认")
    print(f"结果 SHA-256: {package['result_sha256']}\n")
    if saved_path:
        print(f"证据包: {saved_path}\n")

    if not rows:
        print("  （无结果）")
        return

    # 格式化输出
    for i, row in enumerate(rows, 1):
        print(f"--- 第 {i} 行 ---")
        for k, v in row.items():
            val_str = str(v) if v is not None else "NULL"
            if len(val_str) > 120:
                val_str = val_str[:117] + "... [仅显示截断；完整值见 JSON 证据包]"
            print(f"  {k}: {val_str}")
        print()


def cmd_count(
    client: DataReportingClient,
    table: str,
    where: str = "",
    no_whitelist: bool = False,
) -> None:
    """查询记录数"""
    cnt = client.count(table, where, enforce_table_whitelist=not no_whitelist)
    print(f"\n{table}: {cnt:,} 条记录")
    if where:
        print(f"条件: {where}")


def cmd_catalog(client: DataReportingClient) -> None:
    """全量表结构概览"""
    tables = client.list_tables()
    print(f"\n{'='*80}")
    print(f"dataReporting 数据库 - 全量表结构概览")
    print(f"{'='*80}\n")

    for t in tables:
        columns = client.describe_online(t["name"])
        print(f"[{t['name']}] {t['comment']} ({len(columns)} 字段)")
        # 只显示关键字段
        key_cols = [c for c in columns if c["key"] == "PRI" or c["comment"]]
        if key_cols:
            for c in key_cols[:10]:  # 最多显示 10 个
                key_mark = "[PK]" if c["key"] == "PRI" else ""
                print(f"    {c['field']:<35s} {c['type']:<20s} {key_mark} {c['comment']}")
            if len(key_cols) > 10:
                print(f"    ... 另有 {len(key_cols) - 10} 个含注释字段（共 {len(columns)} 字段）")
        else:
            for c in columns[:5]:
                print(f"    {c['field']:<35s} {c['type']}")
            if len(columns) > 5:
                print(f"    ... 另有 {len(columns) - 5} 个字段")
        print()


def cmd_schema(
    client: DataReportingClient,
    output: Optional[str] = None,
    *,
    overwrite: bool = False,
    confirmed: bool = False,
) -> None:
    """导出全量 Schema"""
    output_path = Path(output) if output else None
    if output_path:
        _require_output_write_allowed(output_path, overwrite, confirmed)
    schema = client.export_schema()

    if output_path:
        _write_json_file(
            schema,
            output_path,
            overwrite=overwrite,
            confirmed=confirmed,
        )
        print(f"Schema 已导出到: {output_path}")
    else:
        print(json.dumps(schema, ensure_ascii=False, indent=2))


def cmd_schema_diff(
    client: DataReportingClient,
    json_output: bool = False,
    output: str = "",
    *,
    overwrite: bool = False,
    confirmed: bool = False,
) -> None:
    """对比本地 schema 与线上 schema。"""
    output_path = Path(output) if output else None
    if output_path:
        _require_output_write_allowed(output_path, overwrite, confirmed)
    local = load_schema_file(client.schema.schema_path)
    remote = client.export_schema()
    diff = diff_schemas(local, remote)

    if output_path:
        _write_json_file(
            diff,
            output_path,
            overwrite=overwrite,
            confirmed=confirmed,
        )

    if json_output:
        print(json.dumps(diff, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_diff_text(diff))
        if output_path:
            print(f"schema diff 已保存: {output_path}")


def cmd_refresh_schema(
    client: DataReportingClient,
    confirmed: bool = False,
    *,
    yes: bool = False,
) -> None:
    """刷新 schema.json 和 table_catalog.md；默认只预览 diff。"""
    skill_root = _skill_root()
    schema_path = client.schema.schema_path
    catalog_path = skill_root / "references" / "table_catalog.md"
    local = load_schema_file(schema_path)
    remote = client.export_schema()
    diff = diff_schemas(local, remote)

    print(render_diff_text(diff))
    if not diff["summary"]["has_changes"]:
        print("无需刷新。")
        return

    if not (confirmed or yes):
        print("以上为预览结果；如确认刷新，请追加 --confirmed。")
        return

    schema_backup = _backup_file(schema_path)
    catalog_backup = _backup_file(catalog_path)
    schema_path.write_text(
        json.dumps(remote, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    catalog_path.write_text(render_table_catalog(remote), encoding="utf-8")
    print(f"schema 已刷新: {schema_path}")
    print(f"schema 备份: {schema_backup}")
    print(f"catalog 已刷新: {catalog_path}")
    print(f"catalog 备份: {catalog_backup}")


def cmd_health(client: DataReportingClient) -> None:
    """健康检查"""
    result = client.health_check()
    print(f"\n数据报表平台健康检查")
    print(f"{'='*40}")
    print(f"地址: {client.base_url}")
    print(f"状态: {result['status']}")
    if result["status"] == "ok":
        print(f"表数量: {result['table_count']}")
        print(f"可用表: {', '.join(result['tables'])}")
        print(f"operator_brand 样例行数: {result['operator_brand_sample_count']}")
    else:
        print(f"错误: {result['message']}")


def cmd_operator_brands(client: DataReportingClient, args: argparse.Namespace) -> None:
    """查询运营主体-品牌-城市码表。"""
    rows = client.get_operator_brands(
        operator=args.operator or "",
        brand=args.brand or "",
        city=args.city or "",
        limit=args.limit,
    )
    print("\noperator_brand 码表查询")
    print(f"返回: {len(rows)} 行\n")
    if not rows:
        print("  （无结果）")
        return
    for i, row in enumerate(rows, 1):
        print(
            f"{i:3d}. {row['运营主体']} | {row['品牌']} | "
            f"{row['城市']} | {row['对接人'] or '-'}"
        )


def cmd_mabiao(client: DataReportingClient) -> None:
    """输出兼容本地 Excel 码表的映射统计。"""
    mapping = client.load_mabiao_mapping()
    print("\n公司库码表映射")
    print(f"运营主体: {len(mapping['all_zhuti'])}")
    print(f"品牌: {len(mapping['all_brands'])}")
    print(f"城市: {len(mapping['all_cities'])}")
    print(f"对接人: {len(mapping['all_persons'])}")


def cmd_metrics(metric_name: str = "") -> None:
    """浏览机器可读指标口径目录。"""
    catalog = _load_metrics_catalog()
    metrics = catalog.get("metrics", {})
    if metric_name:
        if metric_name not in metrics:
            print(f"未知指标: {metric_name}")
            print(f"可用指标: {', '.join(sorted(metrics))}")
            sys.exit(1)
        print(json.dumps(metrics[metric_name], ensure_ascii=False, indent=2))
        return

    print("\n指标口径目录")
    print(f"返回: {len(metrics)} 个指标\n")
    for name, metric in sorted(metrics.items()):
        print(f"- {name}: {metric.get('display_name', '')}")


def cmd_template(client: DataReportingClient, args: argparse.Namespace) -> None:
    """执行业务查询模板"""
    template_name = args.template_name
    kwargs: dict[str, Any] = {}

    if args.brand:
        kwargs["brand"] = args.brand
    if args.city:
        kwargs["city"] = args.city
    if args.date:
        kwargs["date"] = args.date
    if args.operator:
        kwargs["operator"] = args.operator
    if args.product:
        kwargs["product_name"] = args.product
    if args.tenant:
        kwargs["tenant_name"] = args.tenant
    if args.limit:
        kwargs["limit"] = args.limit

    templates = {
        "capacity-by-brand": client.query_capacity_by_brand_date,
        "order-by-brand": client.query_order_by_brand_date,
        "time-split": client.query_time_split_by_hour,
        "operator-brands": lambda **kw: client.query_operator_brands(kw.get("operator", "")),
        "driver-rt": client.query_driver_real_time,
        "order-rt": client.query_order_real_time,
        "tr-config": lambda **kw: client.query_tr_config(
            date=kw.get("date", ""), brand=kw.get("brand", ""), city=kw.get("city", "")
        ),
    }

    if template_name not in templates:
        print(f"未知模板: {template_name}")
        print(f"可用模板: {', '.join(sorted(templates.keys()))}")
        sys.exit(1)

    func = templates[template_name]
    try:
        rows = func(**kwargs)
    except TypeError as e:
        print(f"模板参数错误: {e}")
        print(f"模板 {template_name} 需要参数: {func.__code__.co_varnames}")
        sys.exit(1)

    print(f"\n模板: {template_name}")
    print(f"参数: {json.dumps(kwargs, ensure_ascii=False)}")
    print(f"返回: {len(rows)} 行\n")

    if not rows:
        print("  （无结果）")
        return

    for i, row in enumerate(rows, 1):
        print(f"--- 第 {i} 行 ---")
        for k, v in row.items():
            val_str = str(v) if v is not None else "NULL"
            if len(val_str) > 100:
                val_str = val_str[:97] + "..."
            print(f"  {k}: {val_str}")
        print()


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_metrics_catalog() -> dict[str, Any]:
    path = _skill_root() / "references" / "metrics_catalog.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_query_run(
    package: dict[str, Any],
    output: str,
    *,
    overwrite: bool = False,
    confirmed: bool = False,
) -> Path:
    path = _resolve_query_output_path(package, output)
    _require_output_write_allowed(path, overwrite, confirmed)
    _write_json_file(
        package,
        path,
        overwrite=overwrite,
        confirmed=confirmed,
    )
    return path


def _resolve_query_output_path(package: Optional[dict[str, Any]], output: str) -> Path:
    if output == "auto":
        if package is None:
            raise RuntimeError("自动证据包路径只能在查询完成后解析")
        return _default_query_run_path(package)
    path = Path(output)
    if path.suffix.lower() != ".json":
        if package is None:
            return path
        path = path / _default_query_run_filename(package)
    return path


def _require_output_write_allowed(path: Path, overwrite: bool, confirmed: bool) -> None:
    if path.exists() and not (overwrite and confirmed):
        raise RuntimeError(f"输出目标已存在，覆盖必须同时传 --overwrite --confirmed: {path}")


def _preflight_query_output(output: str, overwrite: bool, confirmed: bool) -> None:
    if output and output != "auto" and Path(output).suffix.lower() == ".json":
        _require_output_write_allowed(Path(output), overwrite, confirmed)


def _default_query_run_path(package: dict[str, Any]) -> Path:
    return _skill_root() / "assets" / "query_runs" / _default_query_run_filename(package)


def _default_query_run_filename(package: dict[str, Any]) -> str:
    tz = timezone(timedelta(hours=8))
    timestamp = datetime.now(tz).strftime("%Y%m%d_%H%M%S")
    digest = hashlib.sha1(package.get("safe_sql", "").encode("utf-8")).hexdigest()[:10]
    return f"{timestamp}_{digest}.json"


def _write_json_file(
    data: dict[str, Any],
    path: Path,
    *,
    overwrite: bool = False,
    confirmed: bool = False,
) -> None:
    _require_output_write_allowed(path, overwrite, confirmed)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if path.exists() and overwrite and confirmed else "x"
    with path.open(mode, encoding="utf-8") as file_obj:
        file_obj.write(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")


def _backup_file(path: Path) -> Path:
    tz = timezone(timedelta(hours=8))
    timestamp = datetime.now(tz).strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.name}.bak.{timestamp}")
    backup_path.write_bytes(path.read_bytes())
    return backup_path


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="lx_shujuku — 出行数据报表平台 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s list-tables
  %(prog)s describe card_data
  %(prog)s catalog
  %(prog)s query "SELECT id, brand_name, date_day, city_name, completed_order_count FROM honghu_order_data WHERE brand_name = '品牌A' AND date_day = '2026-08-01' LIMIT 5"
  %(prog)s query "SELECT 1 FROM new_table LIMIT 1" --no-whitelist
  %(prog)s describe new_table --no-whitelist
  %(prog)s count honghu_order_data --where "brand_name = '品牌A' AND date_day = '2026-08-01'"
  %(prog)s schema
  %(prog)s health
  %(prog)s operator-brands --operator "方舟行（上海）"
  %(prog)s mabiao
  %(prog)s template capacity-by-brand --brand "方舟行车主" --date "2025-05-12"
        """,

    )

    # 全局参数放最前面（parse_known_args 风格）
    parser.add_argument("--config", help="配置文件路径（默认 config/fog_config.yaml）")
    parser.add_argument("--quiet", "-q", action="store_true", help="安静模式，只输出错误")

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # list-tables
    subparsers.add_parser("list-tables", aliases=["ls"], help="列出所有表")

    # describe
    desc_parser = subparsers.add_parser("describe", aliases=["desc"], help="查看表结构")
    desc_parser.add_argument("table", help="表名")
    desc_parser.add_argument(
        "--no-whitelist",
        action="store_true",
        help="绕过本地 schema 白名单，直接查看线上表结构",
    )

    # indexes
    indexes_parser = subparsers.add_parser("indexes", help="查看白名单表索引元数据")
    indexes_parser.add_argument("table", help="表名（必须在本地 schema 白名单中）")
    indexes_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="输出结构化 JSON",
    )

    # catalog
    subparsers.add_parser("catalog", aliases=["cat"], help="全量表结构概览")

    # query
    query_parser = subparsers.add_parser("query", aliases=["q"], help="执行 SQL 查询")
    query_parser.add_argument("sql", help="SQL 语句")
    query_parser.add_argument("--limit", type=int, default=50, help="返回行数上限（默认 50）")
    query_parser.add_argument(
        "--full",
        action="store_true",
        help="有界完整模式：最多 20 页，单遍稳定游标读取并用前后计数验收",
    )
    query_parser.add_argument("--page-size", type=int, default=500, help="完整模式分页大小（默认 500）")
    query_parser.add_argument("--max-rows", type=int, default=10000, help="完整模式最大总行数（默认 10000）")
    query_parser.add_argument("--question", default="", help="原始业务问题，写入证据包")
    query_parser.add_argument("--metric", default="", help="指标口径 ID，写入证据包")
    query_parser.add_argument("--json", dest="json_output", action="store_true", help="输出结构化证据包 JSON")
    query_parser.add_argument("--audit", action="store_true", help="保存结构化证据包到默认目录")
    query_parser.add_argument("--output", "-o", default="", help="保存结构化证据包；可填文件、目录或 auto")
    query_parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有输出；必须同时传 --confirmed")
    query_parser.add_argument("--confirmed", action="store_true", help="确认覆盖已有输出")
    query_parser.add_argument(
        "--no-whitelist",
        action="store_true",
        help="绕过本地 schema 白名单，允许查询尚未同步的新分享表",
    )

    # count
    count_parser = subparsers.add_parser("count", help="查询表记录数")
    count_parser.add_argument("table", help="表名")
    count_parser.add_argument("--where", default="", help="WHERE 条件")
    count_parser.add_argument(
        "--no-whitelist",
        action="store_true",
        help="绕过本地 schema 白名单，直接查询线上授权表记录数",
    )

    # schema
    schema_parser = subparsers.add_parser("schema", help="导出全量 Schema JSON")
    schema_parser.add_argument("--output", "-o", help="输出文件路径")
    schema_parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有输出；必须同时传 --confirmed")
    schema_parser.add_argument("--confirmed", action="store_true", help="确认覆盖已有输出")

    # schema-diff
    schema_diff_parser = subparsers.add_parser("schema-diff", help="对比本地 schema 与线上 schema")
    schema_diff_parser.add_argument("--json", dest="json_output", action="store_true", help="输出 JSON")
    schema_diff_parser.add_argument("--output", "-o", help="保存 diff JSON 到指定路径")
    schema_diff_parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有输出；必须同时传 --confirmed")
    schema_diff_parser.add_argument("--confirmed", action="store_true", help="确认覆盖已有输出")

    # refresh-schema
    refresh_schema_parser = subparsers.add_parser("refresh-schema", help="刷新 schema.json 和 table_catalog.md")
    refresh_schema_parser.add_argument("--confirmed", action="store_true", help="确认覆盖 schema/catalog 并自动备份")
    refresh_schema_parser.add_argument("--yes", action="store_true", help="兼容旧确认参数；等同 --confirmed")

    # health
    subparsers.add_parser("health", aliases=["hc"], help="健康检查")

    # operator-brands
    op_parser = subparsers.add_parser("operator-brands", aliases=["op"], help="查询 operator_brand 码表")
    op_parser.add_argument("--operator", help="运营主体")
    op_parser.add_argument("--brand", help="品牌名称")
    op_parser.add_argument("--city", help="城市名称")
    op_parser.add_argument("--limit", type=int, default=100, help="返回行数上限")

    # mabiao
    subparsers.add_parser("mabiao", help="输出兼容本地 Excel 码表的映射统计")

    # metrics
    metrics_parser = subparsers.add_parser("metrics", help="浏览指标口径目录")
    metrics_parser.add_argument("metric_name", nargs="?", help="指标口径 ID")

    # template
    tmpl_parser = subparsers.add_parser("template", aliases=["tpl"], help="业务查询模板")
    tmpl_parser.add_argument("template_name", help="模板名称")
    tmpl_parser.add_argument("--brand", help="品牌名称")
    tmpl_parser.add_argument("--city", help="城市名称")
    tmpl_parser.add_argument("--date", help="日期 (YYYY-MM-DD)")
    tmpl_parser.add_argument("--operator", help="运营主体")
    tmpl_parser.add_argument("--product", help="商品/卡券名称")
    tmpl_parser.add_argument("--tenant", help="租户名称")
    tmpl_parser.add_argument("--limit", type=int, default=50, help="返回行数上限")

    # 全局参数
    # （已移至 subparsers 定义之前）

    args = parser.parse_args(argv)

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "metrics":
        cmd_metrics(args.metric_name or "")
        return 0

    try:
        if args.command in ("query", "q") and args.output:
            _preflight_query_output(args.output, args.overwrite, args.confirmed)
        elif args.command in {"schema", "schema-diff"} and getattr(args, "output", None):
            _require_output_write_allowed(Path(args.output), args.overwrite, args.confirmed)
    except RuntimeError as e:
        logger.error(str(e))
        return 1

    # 创建客户端
    try:
        client = create_client(config_path=args.config)
    except FileNotFoundError:
        logger.error(
            "配置文件未找到，请先编辑项目根目录 config/fog_config.yaml 的 lx_shujuku.api 段"
        )
        return 1
    except Exception as e:
        logger.error(f"初始化客户端失败: {e}")
        return 1

    # 路由命令
    try:
        if args.command in ("list-tables", "ls"):
            cmd_list_tables(client)
        elif args.command in ("describe", "desc"):
            cmd_describe(client, args.table, no_whitelist=getattr(args, "no_whitelist", False))
        elif args.command == "indexes":
            cmd_indexes(client, args.table, json_output=args.json_output)
        elif args.command in ("catalog", "cat"):
            cmd_catalog(client)
        elif args.command in ("query", "q"):
            cmd_query(
                client,
                args.sql,
                args.limit,
                question=args.question,
                metric=args.metric,
                json_output=args.json_output,
                output=args.output,
                audit=args.audit,
                no_whitelist=getattr(args, "no_whitelist", False),
                full=args.full,
                page_size=args.page_size,
                max_rows=args.max_rows,
                overwrite=args.overwrite,
                confirmed=args.confirmed,
            )
        elif args.command == "count":
            cmd_count(
                client,
                args.table,
                args.where,
                no_whitelist=getattr(args, "no_whitelist", False),
            )
        elif args.command == "schema":
            cmd_schema(
                client,
                args.output,
                overwrite=args.overwrite,
                confirmed=args.confirmed,
            )
        elif args.command == "schema-diff":
            cmd_schema_diff(
                client,
                json_output=args.json_output,
                output=args.output or "",
                overwrite=args.overwrite,
                confirmed=args.confirmed,
            )
        elif args.command == "refresh-schema":
            cmd_refresh_schema(client, confirmed=args.confirmed, yes=args.yes)
        elif args.command in ("health", "hc"):
            cmd_health(client)
        elif args.command in ("operator-brands", "op"):
            cmd_operator_brands(client, args)
        elif args.command == "mabiao":
            cmd_mabiao(client)
        elif args.command in ("template", "tpl"):
            cmd_template(client, args)
    except RuntimeError as e:
        logger.error(str(e))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
