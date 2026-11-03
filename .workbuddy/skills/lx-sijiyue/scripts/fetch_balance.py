#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量查询司机账户余额/可提现余额/免密状态（宏鹄 SaaS）

输入含司机ID列的 Excel（xls/xlsx 均可），批量调 balance 接口，输出追加三列的新 xlsx：
  账户余额   (data.real_balance)
  可提现余额 (data.withdraw_balance)
  免密状态   (data.is_sign，true/false)

接口：POST /driver/driverAccount/balance，form 载荷 driver_id=xxx&company_id=yyy。
每个司机 1 次请求，无需解密，比身份证/车辆批次快得多。

用法：
  python fetch_balance.py --company_id 1312 --input 司机表.xlsx
  python fetch_balance.py --company_id 1312 --input 司机表.xls --output out.xlsx --threads 10

依赖：pandas, xlrd(读老xls), openpyxl。ticket 缓存过期时先跑 login.py。
"""
import json, os, sys, time, random, argparse, threading, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lx-shenfenzheng/scripts'))
import honghu_common as common
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "ticket_cache.json")
API = "https://api.honghusaas.com/biz-api/v1/workbench"
BALANCE = API + "/driver/driverAccount/balance"

COLS = {"real_balance": "账户余额", "withdraw_balance": "可提现余额", "is_sign": "免密状态"}

STAT = {"req": 0, "err": 0}
LOCK = threading.Lock()
COOKIE = None
STOP = threading.Event()  # 13005 会话失效时熔断


def load_ticket(username=None, company_id=None):
    return common.read_ticket(CACHE, username, company_id)


def post_form(did, cid, retry=1):
    with LOCK:
        STAT['req'] += 1
    try:
        result = common.request_json(BALANCE, {'driver_id': did, 'company_id': cid}, COOKIE, form=True)
        return result
    except ValueError:
        with LOCK:
            STAT['err'] += 1
        return None


def _fetch_one(args_tuple):
    did, cid = args_tuple
    if STOP.is_set():
        return did, {k: "" for k in COLS.values()}, ["任务已中止（会话失效）"]
    r = post_form(did, cid)
    if not r:
        return did, {k: "" for k in COLS.values()}, ["余额请求失败"]
    common.checked_response(r, STOP)
    d = r.get("data") or {}
    if not d:
        return did, {k: "" for k in COLS.values()}, ["查无记录"]
    if not isinstance(d, dict):
        raise ValueError("余额响应结构无效")
    return did, {
        COLS["real_balance"]: str(d.get("real_balance") or ""),
        COLS["withdraw_balance"]: str(d.get("withdraw_balance") or ""),
        COLS["is_sign"]: "是" if d.get("is_sign") is True else "否" if d.get("is_sign") is False else "",
    }, []


def fetch_one(args_tuple):
    try:
        return _fetch_one(args_tuple)
    except ValueError as exc:
        return args_tuple[0], {}, [str(exc)]


def read_table(path):
    """读 Excel 为 DataFrame（全 str）。兼容真 xls、伪 xls（.xls 实为 xlsx）、xlsx。"""
    low = path.lower()
    if low.endswith(".xls"):
        try:
            return pd.read_excel(path, engine="xlrd", dtype=str)
        except Exception:
            return pd.read_excel(path, engine="openpyxl", dtype=str)
    return pd.read_excel(path, engine="openpyxl", dtype=str)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--company_id", required=True, help="租户id，宁低勿猜：先拿1个司机id验证")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default=None, help="默认在输入目录另存 *_余额.xlsx")
    ap.add_argument("--threads", type=int, default=10)
    ap.add_argument("--id_col", default=None, help="司机ID列名，默认自动识别")
    ap.add_argument("--username", required=True, help="本人登录账号，必须显式选择")
    common.add_confirmation(ap)
    args = ap.parse_args(argv)
    STOP.clear()
    STAT.update(req=0, err=0)

    global COOKIE

    try:
        common.batch_plan(args, '_余额')
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

    if not common.authorize_batch(args, '_余额', ap, df, id_col, list(COLS.values())):
        return 0
    ticket = load_ticket(args.username, args.company_id)
    COOKIE = f"login_type=2; ticket={ticket}; company_id={args.company_id}"

    for cn in COLS.values():
        if cn not in cols:
            df[cn] = ""

    ids = df[id_col].dropna().astype(str).str.strip()
    ids = ids[ids != ""].tolist()
    todo = [(did, args.company_id) for did in common.pending_driver_ids(df, id_col, list(COLS.values()))]
    print(f"[INFO] 司机 {len(ids)} 人，待查 {len(todo)} 人，{args.threads} 线程")

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
            did, out, note = fu.result()
            result[did] = out
            if note:
                notes[did] = note
            done += 1
            if STOP.is_set():
                break
            if done % 200 == 0 or done == len(todo):
                el = time.time() - t0
                eta = (len(todo) - done) / (done / el) if el else 0
                print(f"  [{done}/{len(todo)}] {done/el:.2f}人/s ETA {eta/60:.1f}min "
                      f"请求={STAT['req']} 错误={STAT['err']} 异常={len(notes)}", flush=True)

    def fill(row, cn):
        did = str(row[id_col]).strip() if pd.notna(row[id_col]) else ""
        if did in result and result[did].get(cn) != "":
            return result[did][cn]
        cur = row[cn]
        return str(cur).strip() if pd.notna(cur) else ""

    for cn in COLS.values():
        df[cn] = df.apply(lambda row, cn=cn: fill(row, cn), axis=1)

    out_path = args.output or os.path.splitext(args.input)[0] + "_余额.xlsx"
    if not out_path.lower().endswith(".xlsx"):
        out_path = os.path.splitext(out_path)[0] + ".xlsx"
    if STOP.is_set():
        raise ValueError('任务已中止；会话或权限失效，未输出成功文件')
    print(json.dumps(common.write_new_workbook(df, out_path), ensure_ascii=False))

    print(f"\n[DONE] {time.time()-t0:.0f}s，请求 {STAT['req']}，错误 {STAT['err']}" + (" [会话失效熔断]" if STOP.is_set() else ""))
    for cn in COLS.values():
        vals = df[cn].astype(str).str.strip()
        filled = ((vals != "") & (vals.str.lower() != "nan")).sum()
        print(f"[填充率] {cn}: {filled}/{len(df)} = {filled/max(len(df), 1)*100:.1f}%")
    if notes:
        from collections import Counter
        print("异常分类: " + json.dumps(dict(Counter(reason for reasons in notes.values() for reason in reasons)), ensure_ascii=False))
    print(f"输出: {out_path}")
    return 2 if notes else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(2)
