#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量抓取司机身份证号（宏鹄 SaaS）

输入含司机ID列的 Excel（xls/xlsx 均可），输出追加"身份证号"列的新 xlsx。
每个司机 2 次请求：driverDetail 取 encrypt_id_no → clarify 解密。

用法：
  python fetch_idcards.py --company_id 1312 --input 司机表.xlsx
  python fetch_idcards.py --company_id 1312 --input 司机表.xls --output out.xlsx --threads 10

依赖：pandas, xlrd(读老xls), openpyxl。ticket 缓存过期时先跑 login.py。
"""
import json, os, sys, time, random, argparse, threading, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lx-shenfenzheng/scripts'))
import honghu_common as common
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "ticket_cache.json")
API = "https://api.honghusaas.com/biz-api/v1/workbench"
DETAIL = API + "/driver/driverDetail/driverDetail"
CLARIFY = API + "/driver/driverDetail/clarify"

STAT = {"req": 0, "err": 0}
LOCK = threading.Lock()
COOKIE = None
STOP = threading.Event()  # 13005 会话失效时熔断，避免全量废请求
# 已知坑：errno=0 但 basic 空（品牌选错/查无记录）；13008 商户被禁用；13006 权限不足


def load_ticket(username=None, company_id=None):
    return common.read_ticket(CACHE, username, company_id)


def post(url, body, retry=1):
    with LOCK:
        STAT['req'] += 1
    try:
        result = common.request_json(url, body, COOKIE)
        return result
    except ValueError:
        with LOCK:
            STAT['err'] += 1
        return None


def dec(enc):
    if not enc:
        return ''
    data = common.checked_response(post(CLARIFY, {'encrypt_info': enc}), STOP)
    if not isinstance(data, dict) or not data.get('decrypt_info'):
        raise ValueError('解密请求失败')
    return data['decrypt_info']


def _fetch_one(args_tuple):
    did, cid = args_tuple
    if STOP.is_set():
        return did, "", ["任务已中止（会话失效）"]
    d = post(DETAIL, {"company_id": cid, "driver_id": did})
    if not d:
        return did, "", ["详情请求失败"]
    common.checked_response(d, STOP)
    data = d.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("司机详情响应结构无效")
    basic = data.get("basic") or {}
    if not isinstance(basic, dict) or not basic:
        return did, "", ["查无司机记录"]
    return did, dec(basic.get("encrypt_id_no")), ([] if basic.get("encrypt_id_no") else ["平台未存身份证"])


def fetch_one(args_tuple):
    try:
        return _fetch_one(args_tuple)
    except ValueError as exc:
        return args_tuple[0], '', [str(exc)]


def read_table(path):
    """读 Excel 为 DataFrame（全 str，防司机ID精度丢失）。
    兼容真 xls、伪 xls（后缀 .xls 实为 xlsx）、xlsx。"""
    low = path.lower()
    if low.endswith(".xls"):
        try:
            return pd.read_excel(path, engine="xlrd", dtype=str)
        except Exception:
            return pd.read_excel(path, engine="openpyxl", dtype=str)  # 伪 xls
    return pd.read_excel(path, engine="openpyxl", dtype=str)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--company_id", required=True, help="租户id，宁低勿猜：先拿1个司机id验证")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default=None, help="默认在输入目录另存 *_身份证.xlsx")
    ap.add_argument("--threads", type=int, default=10)
    ap.add_argument("--id_col", default=None, help="司机ID列名，默认自动识别")
    ap.add_argument("--col", default="身份证号", help="输出列名")
    ap.add_argument("--username", required=True, help="本人登录账号，必须显式选择")
    common.add_confirmation(ap)
    args = ap.parse_args(argv)
    STOP.clear()
    STAT.update(req=0, err=0)

    global COOKIE

    try:
        common.batch_plan(args, '_身份证')
    except ValueError as exc:
        ap.error(str(exc))
    df = read_table(args.input)
    cols = [str(c) for c in df.columns]
    try:
        id_col = common.driver_column(cols, args.id_col)
        ids_to_check = df[id_col].dropna().astype(str).str.strip()
        if any(not value.isdecimal() for value in ids_to_check if value):
            raise ValueError('司机ID必须是数字文本，不能含小数或科学计数法')
    except ValueError as exc:
        ap.error(str(exc))
    print(f"[INFO] 司机ID列：{id_col}")

    if not args.col.strip() or args.col == id_col:
        ap.error("输出列名不能为空或等于司机ID列")
    if not common.authorize_batch(args, '_身份证', ap, df, id_col, [args.col]):
        return 0
    ticket = load_ticket(args.username, args.company_id)
    COOKIE = f"login_type=2; ticket={ticket}; company_id={args.company_id}"

    if args.col not in cols:
        df[args.col] = ""
    ids = df[id_col].dropna().astype(str).str.strip()
    ids = ids[ids != ""].tolist()
    # 已有值的行不重复请求（dtype=str 下空单元格是 NaN，必须判 nan）
    todo = [(did, args.company_id) for did in common.pending_driver_ids(df, id_col, [args.col])]
    print(f"[INFO] 司机 {len(ids)} 人，待取 {len(todo)} 人，{args.threads} 线程")

    result, notes = {}, {}
    sample = common.first_result(todo, fetch_one)
    if sample:
        result[sample[0]] = sample[1]
        todo = todo[1:]
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        futs = [ex.submit(fetch_one, t) for t in todo]
        for fu in as_completed(futs):
            did, idno, note = fu.result()
            result[did] = idno
            if note:
                notes[did] = note
            done += 1
            if done % 100 == 0 or done == len(todo):
                el = time.time() - t0
                eta = (len(todo) - done) / (done / el) if el else 0
                print(f"  [{done}/{len(todo)}] {done/el:.2f}人/s ETA {eta/60:.1f}min "
                      f"请求={STAT['req']} 错误={STAT['err']} 异常={len(notes)}", flush=True)

    def fill(row):
        did = str(row[id_col]).strip() if pd.notna(row[id_col]) else ""
        if pd.notna(row[args.col]) and str(row[args.col]).strip():
            return str(row[args.col]).strip()
        if did in result and result[did]:
            return result[did]
        cur = row[args.col]
        return str(cur).strip() if pd.notna(cur) else ""

    df[args.col] = df.apply(fill, axis=1)

    out = args.output or os.path.splitext(args.input)[0] + "_身份证.xlsx"
    if not out.lower().endswith(".xlsx"):
        out = os.path.splitext(out)[0] + ".xlsx"
    if STOP.is_set():
        raise ValueError('任务已中止；会话或权限失效，未输出成功文件')
    print(json.dumps(common.write_new_workbook(df, out), ensure_ascii=False))

    # 填充率校验（判空用 len>0，禁止关键词过滤——公司名含"无/空"会误伤）
    vals = df[args.col].astype(str).str.strip()
    filled = ((vals != "") & (vals.str.lower() != "nan")).sum()
    total = len(df)
    print(f"\n[DONE] {time.time()-t0:.0f}s，请求 {STAT['req']}，错误 {STAT['err']}")
    print(f"[填充率] {filled}/{total} = {filled/max(total, 1)*100:.1f}%")
    if notes:
        from collections import Counter
        print("异常分类: " + json.dumps(dict(Counter(reason for reasons in notes.values() for reason in reasons)), ensure_ascii=False))
    print(f"输出: {out}")
    return 2 if notes else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(2)
