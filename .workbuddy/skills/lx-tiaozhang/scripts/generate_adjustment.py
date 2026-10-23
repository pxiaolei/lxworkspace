#!/usr/bin/env python3
"""Validate a complete adjustment batch and create a new, verified CSV."""

import argparse
import csv
import io
import json
import re
from decimal import Decimal
from pathlib import Path


FIELDS = ["driver_id", "driver_name", "amount_yuan", "order_list", "activity_id", "desc"]
REQUIRED = {"driver_id", "driver_name", "amount_yuan"}
TEMPLATES = {"credit": "credit_template.csv", "debit": "debit_template.csv"}
DEFAULT_DESC = {"credit": "补款", "debit": "扣款"}


def load_template(kind):
    path = Path(__file__).resolve().parents[1] / "assets" / TEMPLATES[kind]
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream, strict=True))
    if len(rows) != 2 or rows[0] != FIELDS or len(rows[1]) != len(FIELDS):
        raise ValueError("原模板须仅包含固定六列表头和一行说明")
    return rows[1]


def load_records(path, instruction):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        if path.suffix.lower() == ".json":
            return json.load(stream, parse_float=Decimal)
        if path.suffix.lower() != ".csv":
            raise ValueError("输入后缀必须是 .json 或 .csv")
        reader = csv.reader(stream, strict=True)
        header = next(reader, [])
        if len(header) != len(set(header)):
            raise ValueError("CSV 表头存在重复字段")
        if set(header) - set(FIELDS):
            raise ValueError("CSV 表头含未知字段")
        if not REQUIRED.issubset(header):
            raise ValueError("CSV 表头缺少必填字段 driver_id、driver_name 或 amount_yuan")
        records = []
        for row_number, row in enumerate(reader, 2):
            if row_number == 2 and header == FIELDS and row == instruction:
                continue
            if not row:
                continue
            if len(row) != len(header):
                raise ValueError(f"CSV 第 {row_number} 个记录的列数与表头不一致")
            records.append(dict(zip(header, row)))
        return records


def text_value(value, field, required=False):
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须由用户提供字符串")
    if required and not value.strip():
        raise ValueError(f"{field} 为必填字段，不能为空")
    return value


def amount_text(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("amount_yuan 必须是金额字符串或 JSON 数值")
    text = format(value, "f") if isinstance(value, Decimal) else str(value).strip()
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", text):
        raise ValueError("amount_yuan 必须是最多两位小数的正数，不可舍入或使用科学计数法")
    amount = Decimal(text)
    if not Decimal("0.01") <= amount <= Decimal("5000.00"):
        raise ValueError("amount_yuan 必须在 0.01–5000.00 元之间，扣款也使用正数")
    return format(amount, ".2f")


def prepare_records(records, kind):
    if not isinstance(records, list) or not records:
        raise ValueError("输入必须是包含至少一条司机记录的数组或 CSV")
    prepared = []
    errors = []
    default_count = 0
    for index, record in enumerate(records, 1):
        try:
            if not isinstance(record, dict):
                raise ValueError("每条记录必须是字段对象")
            unknown = set(record) - set(FIELDS)
            if unknown:
                raise ValueError("未知字段: " + ", ".join(sorted(unknown)))
            row = {
                "driver_id": text_value(record.get("driver_id"), "driver_id", required=True),
                "driver_name": text_value(record.get("driver_name"), "driver_name", required=True),
                "amount_yuan": amount_text(record.get("amount_yuan")),
                "activity_id": text_value(record.get("activity_id"), "activity_id"),
            }
            orders = record.get("order_list")
            if isinstance(orders, list):
                row["order_list"] = ",".join(text_value(order, "order_list", required=True) for order in orders)
            else:
                row["order_list"] = text_value(orders, "order_list")
            desc = text_value(record.get("desc"), "desc")
            if not desc.strip():
                desc = DEFAULT_DESC[kind]
                default_count += 1
            if len(desc) > 50:
                raise ValueError("desc 不能超过 50 字（含标点和空格）")
            row["desc"] = desc
            prepared.append(row)
        except ValueError as exc:
            errors.append(f"第 {index} 条: {exc}")
    if errors:
        raise ValueError("；".join(errors))
    return prepared, default_count


def check_output(path):
    if path.suffix.lower() != ".csv":
        raise ValueError("输出后缀必须是 .csv")
    if path.is_symlink() or path.exists():
        raise ValueError("输出目标已存在或为符号链接，请使用新的文件名")
    if not path.parent.is_dir():
        raise ValueError("输出父目录不存在，请先按任务目录约定准备目录")


def write_verified_csv(path, records):
    rows = [FIELDS] + [[record[field] for field in FIELDS] for record in records]
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    expected = buffer.getvalue().encode("utf-8-sig")
    # Exclusive creation also prevents a race from overwriting an existing file.
    with path.open("xb") as stream:
        stream.write(expected)
    actual = path.read_bytes()
    readback = list(csv.reader(io.StringIO(actual.decode("utf-8-sig"), newline=""), strict=True))
    if actual != expected or readback != rows:
        raise ValueError("输出读回不一致，未通过验收；保留文件供检查，不可提交后台")


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成司机补款/扣款 CSV（仅新建文件，不覆盖）")
    parser.add_argument("--type", choices=tuple(TEMPLATES), required=True, help="credit=补款，debit=扣款")
    parser.add_argument("--input", type=Path, required=True, help="UTF-8 JSON 数组或 CSV")
    parser.add_argument("--output", type=Path, required=True, help="父目录已存在的新 CSV 路径")
    parser.add_argument("--dry-run", action="store_true", help="只读预检，不创建目录、文件或日志")
    args = parser.parse_args(argv)
    try:
        instruction = load_template(args.type)
        records, default_count = prepare_records(load_records(args.input, instruction), args.type)
        check_output(args.output)
        summary = {
            "status": "preview" if args.dry_run else "generated",
            "type": args.type,
            "record_count": len(records),
            "total_amount_yuan": format(sum((Decimal(row["amount_yuan"]) for row in records), Decimal("0")), ".2f"),
            "default_desc_count": default_count,
            "output": str(args.output.absolute()),
            "readback": "not-run" if args.dry_run else "passed",
        }
        if not args.dry_run:
            write_verified_csv(args.output, records)
    except (OSError, UnicodeError, ValueError, csv.Error) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
