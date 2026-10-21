#!/usr/bin/env python3
"""Read-only company queries with explicit full-result evidence output."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path

from lx_shujuku import DataReportingClient
from lx_shujuku.query_policy import ensure_readonly_sql

ROOT = Path(__file__).resolve().parents[4]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--metric", default="")
    parser.add_argument("--max-rows", type=int, default=10000)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--confirmed", action="store_true")
    parser.add_argument("--expected-preview-hash")
    args = parser.parse_args(argv)
    ensure_readonly_sql(args.sql, default_limit=100, max_limit=1000, append_default_limit=False)
    if args.max_rows <= 0:
        parser.error("max-rows 必须为正数")
    plan = {"source": "company", "sql": args.sql, "question": args.question, "metric": args.metric, "max_rows": args.max_rows, "output": str(args.output.resolve()) if args.output else "new timestamped task directory"}
    plan["preview_hash"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.confirmed:
        return 0
    if args.expected_preview_hash != plan["preview_hash"]:
        parser.error("确认 hash 不一致，未查询/写文件")
    path = args.output or ROOT / "workspace/04数据分析/数据库查询" / datetime.now().strftime("%Y%m%d_%H%M%S_query") / "query-run.json"
    if path.exists() or path.is_symlink():
        parser.error("结果已存在，请使用新目录")
    result = DataReportingClient().execute_full_audited(args.sql, question=args.question, metric=args.metric, max_rows=args.max_rows)
    if result.get("is_complete") is not True:
        raise ValueError("查询结果不完整")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    if json.loads(path.read_text(encoding="utf-8")) != result:
        raise ValueError("查询证据文件读回不一致")
    print(f"result: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
