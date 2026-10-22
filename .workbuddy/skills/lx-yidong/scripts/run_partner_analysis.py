#!/usr/bin/env python3
"""Fixed company/partner entrypoint; never accepts private source switches."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

from anomaly_core import analyze_payload
from company_data_reader import build_query_plan, load_company_payload

ROOT = Path(__file__).resolve().parents[4]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period-type", choices=("daily", "weekly", "range"), required=True)
    for field in ("start", "end", "compare-start", "compare-end"):
        parser.add_argument("--" + field, required=True)
    parser.add_argument("--brand", action="append", default=[])
    parser.add_argument("--city", action="append", default=[])
    parser.add_argument("--rate-threshold", type=float)
    parser.add_argument("--abs-threshold", type=float)
    parser.add_argument("--min-completed", type=float)
    parser.add_argument("--max-rows", type=int, default=10000)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--confirmed", action="store_true")
    parser.add_argument("--expected-preview-hash")
    args = parser.parse_args(argv)
    period = {"type": args.period_type, "start": args.start, "end": args.end, "compare_start": args.compare_start, "compare_end": args.compare_end}
    sqls = build_query_plan(period, args.brand, args.city)
    sys.path.insert(0, str(ROOT / ".workbuddy/skills"))
    from lxx_share.fog_config import get_section
    defaults = get_section("lx_yidongfenxi", ROOT).get("thresholds", {})
    thresholds = {"rate_threshold": args.rate_threshold if args.rate_threshold is not None else float(defaults.get("rate", .05)), "abs_threshold": args.abs_threshold if args.abs_threshold is not None else float(defaults.get("absolute", 500)), "min_current_completed": args.min_completed if args.min_completed is not None else float(defaults.get("minimum_completed", 0))}
    import math
    if any(not math.isfinite(value) or value < 0 for value in thresholds.values()) or args.max_rows <= 0:
        parser.error("阈值必须有限且非负，行数上限必须为正")
    plan = {"profile": "partner_basic", "source": "company", "period": period, "sql": sqls, "thresholds": thresholds, "max_rows": args.max_rows, "output": str(args.output_dir.resolve()) if args.output_dir else "new timestamped task directory"}
    plan["preview_hash"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.confirmed:
        return 0
    if args.expected_preview_hash != plan["preview_hash"]:
        parser.error("确认 hash 不一致，未取数/写文件")
    output = args.output_dir or ROOT / "workspace/04数据分析/异动分析" / datetime.now().strftime("%Y%m%d_%H%M%S_analysis")
    if output.exists() or output.is_symlink():
        parser.error("任务目录已存在，请新建")
    sys.path.insert(0, str(ROOT / ".workbuddy/skills/lx_shujuku/scripts"))
    from lx_shujuku import DataReportingClient
    value, audits = load_company_payload(DataReportingClient(), period, brands=args.brand, cities=args.city, max_rows=args.max_rows)
    result = analyze_payload(value, profile="partner_basic", **thresholds)
    output.mkdir(parents=True)
    artifacts = {"input.json": value, "analysis.json": result, "query-runs.json": audits}
    for filename, data in artifacts.items():
        path = output / filename
        with path.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        if json.loads(path.read_text(encoding="utf-8")) != data:
            raise ValueError("异动产物读回失败")
    lines = [f"本期：{args.start} 至 {args.end}；对比期：{args.compare_start} 至 {args.compare_end}", f"单位：{value['metric_unit']}；状态：{result['analysis_status']}", "数据为本账号可见范围；解释仅作为基础指标线索。"]
    lines += [f"{row['brand']} {row['city']}：{row['smart_analysis']}" for row in result["results"]]
    (output / "analysis.md").write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    print(f"result: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
