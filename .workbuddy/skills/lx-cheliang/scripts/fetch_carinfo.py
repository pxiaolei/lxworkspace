#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量抓取司机车辆信息（宏鹄 SaaS）

输入含司机ID列的 Excel（xls/xlsx 均可），批量调 carInfo 取车辆信息并写回新 xlsx。
可抓字段（--fields 逗号组合，默认全部）：
  plate 车牌号        (需解密 encrypt_plate_no)
  owner 车辆所有人    (需解密 encrypt_owner)
  vin   车辆识别代码  (明文)
  model 车系          (明文 brand_desc，格式 品牌#车系)
  color 车身颜色      (明文)

多车处理（2026-11-02 实测：一个司机可绑多辆车）：
  第 1 辆用原列名（车牌号/车辆所有人/...），第 2 辆写进后缀列（车牌号2/车辆所有人2/...），
  第 3 辆后缀 3，以此类推。排序规则：in_use==1 的车排第一，其余按接口原序。

每个司机 1 次 carInfo + 每辆车最多 2 次 clarify。
注意：平台不支持按车牌号反查，输入必须是司机ID列表。

用法：
  python fetch_carinfo.py --company_id 1502 --input 司机表.xlsx
  python fetch_carinfo.py --company_id 1502 --input 司机表.xls --output out.xlsx --fields plate,owner

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
CAR = API + "/driver/driverDetail/carInfo"
CLARIFY = API + "/driver/driverDetail/clarify"

# 字段 -> (输出列名, 是否需解密)
FIELD_DEF = {
    "plate": ("车牌号", True),
    "owner": ("车辆所有人", True),
    "vin":   ("车辆识别代码", False),
    "model": ("车系", False),
    "color": ("车身颜色", False),
}
# 明文字段 -> carInfo[].basic 里的字段名
PLAIN_KEY = {"vin": "vin", "model": "brand_desc", "color": "color"}
# 密文字段 -> basic 里的密文字段名
ENC_KEY = {"plate": "encrypt_plate_no", "owner": "encrypt_owner"}

STAT = {"req": 0, "err": 0}
LOCK = threading.Lock()
COOKIE = None
STOP = threading.Event()  # 13005 会话失效时熔断，避免全量废请求


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


def order_cars(arr):
    """车辆数组排序：in_use==1 的车排最前，其余保持接口原序。"""
    in_use = [c for c in arr if (c.get("basic") or {}).get("in_use") == 1]
    rest = [c for c in arr if (c.get("basic") or {}).get("in_use") != 1]
    return in_use + rest


def _fetch_one(args_tuple, fields):
    did, cid = args_tuple
    if STOP.is_set():
        return did, [], ["任务已中止（会话失效）"]
    r = post(CAR, {"company_id": cid, "driver_id": did})
    if not r:
        return did, [], ["车辆信息请求失败"]
    common.checked_response(r, STOP)
    arr = r.get("data") or []
    if not arr:
        return did, [], ["无车辆记录"]
    if not isinstance(arr, list) or any(not isinstance(car, dict) or not isinstance(car.get("basic"), dict) or not car["basic"] for car in arr):
        raise ValueError("车辆响应结构无效或无车辆记录")
    cars = []
    for car in order_cars(arr):
        basic = car.get("basic") or {}
        out = {}
        for f in fields:
            if FIELD_DEF[f][1]:
                out[f] = dec(basic.get(ENC_KEY[f]))
            else:
                out[f] = basic.get(PLAIN_KEY[f], "") or ""
        cars.append(out)
    return did, cars, []


def fetch_one(args_tuple, fields):
    try:
        return _fetch_one(args_tuple, fields)
    except ValueError as exc:
        return args_tuple[0], [], [str(exc)]


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
    ap.add_argument("--output", default=None, help="默认在输入目录另存 *_车辆信息.xlsx")
    ap.add_argument("--fields", default="plate,owner,model,vin,color",
                    help="逗号组合：" + ",".join(FIELD_DEF))
    ap.add_argument("--threads", type=int, default=10)
    ap.add_argument("--id_col", default=None, help="司机ID列名，默认自动识别")
    ap.add_argument("--username", required=True, help="本人登录账号，必须显式选择")
    common.add_confirmation(ap)
    args = ap.parse_args(argv)
    STOP.clear()
    STAT.update(req=0, err=0)

    global COOKIE

    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    bad = [f for f in fields if f not in FIELD_DEF]
    if not fields or len(set(fields)) != len(fields):
        ap.error("字段不能为空或重复")
    if bad:
        print(f"[ERROR] 未知字段 {bad}，可选：{list(FIELD_DEF)}")
        sys.exit(1)

    try:
        common.batch_plan(args, '_车辆信息')
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
    print(f"[INFO] 司机ID列：{id_col}，字段：{fields}")

    if not common.authorize_batch(args, '_车辆信息', ap, df, id_col, [FIELD_DEF[f][0] for f in fields]):
        return 0
    ticket = load_ticket(args.username, args.company_id)
    COOKIE = f"login_type=2; ticket={ticket}; company_id={args.company_id}"

    # 第 1 辆车的输出列：不存在则新建（已有值的行跳过请求）
    for f in fields:
        cn = FIELD_DEF[f][0]
        if cn not in cols:
            df[cn] = ""

    ids = df[id_col].dropna().astype(str).str.strip()
    ids = ids[ids != ""].tolist()
    todo = [(did, args.company_id) for did in common.pending_driver_ids(df, id_col, [FIELD_DEF[f][0] for f in fields])]
    ndec = sum(1 for f in fields if FIELD_DEF[f][1])
    nreq_est = len(todo) * (1 + ndec)
    print(f"[INFO] 司机 {len(ids)} 人，待查 {len(todo)} 人，预计 ~{nreq_est} 请求，{args.threads} 线程")

    result, notes = {}, {}
    sample = common.first_result(todo, (lambda item: fetch_one(item, fields)))
    if sample:
        result[sample[0]] = sample[1]
        todo = todo[1:]
    t0 = time.time()
    done = 0
    multi = int(bool(sample and len(sample[1]) > 1))
    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        futs = [ex.submit(fetch_one, t, fields) for t in todo]
        for fu in as_completed(futs):
            did, cars, note = fu.result()
            result[did] = cars
            if note:
                notes[did] = note
            elif len(cars) > 1:
                multi += 1
            done += 1
            if done % 200 == 0 or done == len(todo):
                el = time.time() - t0
                eta = (len(todo) - done) / (done / el) if el else 0
                print(f"  [{done}/{len(todo)}] {done/el:.2f}人/s ETA {eta/60:.1f}min "
                      f"请求={STAT['req']} 错误={STAT['err']} 异常={len(notes)} 多车={multi}", flush=True)

    # 多车列：第 n 辆 -> 列名后缀 n（第1辆无后缀）。列按需创建。
    max_cars = max((len(c) for c in result.values()), default=1)
    print(f"[INFO] 最多 {max_cars} 辆车/人，多车司机 {multi} 人")

    def fill(row, f, idx):
        did = str(row[id_col]).strip() if pd.notna(row[id_col]) else ""
        cars = result.get(did) or []
        current = row.get(FIELD_DEF[f][0] + (str(idx+1) if idx else ""), "")
        if pd.notna(current) and str(current).strip():
            return str(current).strip()
        if idx < len(cars) and cars[idx].get(f):
            return cars[idx][f]
        if idx == 0:  # 第1辆回退到输入已有值
            cur = row[FIELD_DEF[f][0]]
            return str(cur).strip() if pd.notna(cur) else ""
        return ""

    for idx in range(max_cars):
        suf = "" if idx == 0 else str(idx + 1)
        for f in fields:
            cn = FIELD_DEF[f][0] + suf
            if cn not in df.columns:
                df[cn] = ""
            df[cn] = df.apply(lambda row, f=f, idx=idx: fill(row, f, idx), axis=1)

    out_path = args.output or os.path.splitext(args.input)[0] + "_车辆信息.xlsx"
    if not out_path.lower().endswith(".xlsx"):
        out_path = os.path.splitext(out_path)[0] + ".xlsx"
    if STOP.is_set():
        raise ValueError('任务已中止；会话或权限失效，未输出成功文件')
    print(json.dumps(common.write_new_workbook(df, out_path), ensure_ascii=False))

    # 填充率校验（判空用 len>0，禁止关键词过滤）
    print(f"\n[DONE] {time.time()-t0:.0f}s，请求 {STAT['req']}，错误 {STAT['err']}")
    for idx in range(max_cars):
        suf = "" if idx == 0 else str(idx + 1)
        for f in fields:
            cn = FIELD_DEF[f][0] + suf
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
